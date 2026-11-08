"""完整知识点目录、严格保存与可恢复删除；全部使用临时批次。"""
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from workbench import server, taxonomy
from workflow.test_workbench_group_save import make_batch


class CatalogTest(unittest.TestCase):
    def test_tree_contains_every_catalog_node_and_leaf(self):
        tree = taxonomy.knowledge_tree()
        nodes = []
        def visit(items):
            for item in items:
                nodes.append(item)
                visit(item["children"])
        visit(tree["items"])
        by_path, leaves = taxonomy.knowledge_index()
        self.assertTrue(tree["complete"])
        self.assertEqual({n["path"] for n in nodes}, set(by_path))
        self.assertEqual(len(nodes), tree["total"])
        self.assertEqual({n["path"] for n in nodes if n["leaf"]}, leaves)
        self.assertEqual(tree["leaf_total"], len(leaves))
        self.assertGreater(len(leaves), 200)
        for node in nodes:
            self.assertEqual(node["leaf"], not bool(node["children"]))
            self.assertEqual(node["name"], by_path[node["path"]]["name"])

    def test_tree_api_is_complete(self):
        response = invoke("GET", "/api/knowledge/tree")
        self.assertEqual(response["total"], len(taxonomy.knowledge_points()))
        self.assertTrue(response["complete"])

    def test_invalid_knowledge_rejected_before_any_draft_write(self):
        for invalid in (["不存在/自由值"], ["主题/人与自我"], "主题", [123], [""]):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as tmp:
                folder = make_batch(Path(tmp))
                original = (folder / "candidates" / "LOCAL-31.json").read_bytes()
                with self.assertRaises(ValueError):
                    server.Handler.save_units(folder, "FX-001", [{"number": 31, "stem": "changed"}], {"knowledge": invalid})
                self.assertFalse((folder / "candidates" / "units").exists())
                self.assertEqual((folder / "candidates" / "LOCAL-31.json").read_bytes(), original)

    def test_cleared_knowledge_does_not_reappear_from_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = make_batch(Path(tmp))
            server.Handler.save_units(folder, "FX-001", [], {"knowledge": []})
            values = server.label_payload({"unit_id": "FX-001", "knowledge": ["主题/人与自我"]},
                {"knowledge": ["主题/人与自我"]}, [{"knowledge": ["主题/人与自我"]}], folder, "FX-001")
            self.assertEqual(values["knowledge"], [])

    def test_question_type_and_numeric_filter_difficulty_are_mapped(self):
        with tempfile.TemporaryDirectory() as tmp:
            values = server.label_payload({}, {"question_type": "阅读理解", "filterMetadata": {
                "difficultyValue": 0.8, "source_type": "中考真题"}}, [], Path(tmp))
            self.assertEqual(values["type"], "阅读理解")
            self.assertEqual(values["difficulty"], "容易")
            self.assertEqual(values["source_type"], "中考真题")


def invoke(method, path, payload=None):
    """通过真实 Handler 路由，不占用 TCP 端口。"""
    handler = object.__new__(server.Handler)
    handler.path = path
    raw = json.dumps(payload or {}).encode()
    handler.headers = {"Content-Length": str(len(raw))}
    handler.rfile = io.BytesIO(raw)
    result = {}
    handler.send_json = lambda data, status=200: result.update(data=data, status=status)
    getattr(handler, "do_" + method)()
    if result.get("status") != 200:
        raise ValueError(result)
    return result["data"]


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.patches = [patch.object(server, "ROOT", self.root),
                        patch.object(server, "WORKFLOWS", self.root / "workflows"),
                        patch.object(server, "INBOX", self.root / "inbox"),
                        patch.object(server, "_advance_state", {})]
        for p in self.patches:
            p.start()
        server.WORKFLOWS.mkdir()
        server.INBOX.mkdir()
        self.folder = make_batch(server.WORKFLOWS).rename(server.WORKFLOWS / "测试批次-001")
        (self.folder / "workflow").mkdir()
        server.save_json(self.folder / "workflow" / "manifest.json", {"status": "pending", "stages": {}})
        self.name = self.folder.name
        self.inbox = server.INBOX / self.name
        self.inbox.mkdir()
        (self.inbox / "source.docx").write_bytes(b"original source")

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_archive_api_preserves_both_directories_and_allows_same_name_upload(self):
        result = invoke("POST", "/api/batches/" + quote(self.name) + "/archive", {"confirm_name": self.name})
        archive = self.root / result["archive"]
        self.assertTrue(result["recoverable"])
        self.assertTrue((archive / "workflows" / "06_batch" / "batch.json").is_file())
        self.assertEqual((archive / "inbox" / "source.docx").read_bytes(), b"original source")
        self.assertEqual(server.load(archive / "receipt.json")["status"], "archived")
        self.assertFalse(self.folder.exists())
        self.assertFalse(self.inbox.exists())
        self.assertEqual(server.list_batches(), [])
        with patch.object(server, "start_advance", return_value={"running": False}):
            upload = server.handle_upload({"title": self.name}, [{"filename": "paper.pdf", "data": b"%PDF-fixture"}])
        self.assertEqual(upload["name"], self.name)
        self.assertTrue((server.WORKFLOWS / self.name / "attachments/source.pdf").exists())
        self.assertTrue((server.WORKFLOWS / self.name / "workflow" / "manifest.json").exists())
        self.assertTrue((archive / "inbox" / "source.docx").exists())

    def test_invalid_knowledge_api_rejects_and_does_not_write(self):
        with self.assertRaisesRegex(ValueError, "目录叶子"):
            invoke("POST", "/api/batches/" + quote(self.name) + "/units/FX-001", {
                "labels": {"knowledge": ["主题/人与自我"]}})
        self.assertFalse((self.folder / "candidates" / "units").exists())

    def test_archived_batch_cannot_be_recreated_by_stale_save_or_advance(self):
        server.archive_batch(self.name, self.name)
        with self.assertRaisesRegex(ValueError, "不存在"):
            server.Handler.save_units(self.folder, "FX-001", [], {"knowledge": []})
        with self.assertRaisesRegex(ValueError, "不存在"):
            server.start_advance(self.name)
        self.assertFalse(self.folder.exists())

    def test_confirmation_required_and_traversal_rejected(self):
        for name, confirm in ((self.name, "wrong"), ("..", ".."), ("../outside", "../outside"),
                              ("a\\b", "a\\b"), ("/absolute", "/absolute")):
            with self.subTest(name=name), self.assertRaises(ValueError):
                server.archive_batch(name, confirm)
        self.assertTrue(self.folder.exists())
        self.assertTrue(self.inbox.exists())

    def test_running_background_and_manifest_tasks_rejected(self):
        server._advance_state[self.name] = {"running": True}
        with self.assertRaisesRegex(ValueError, "正在运行"):
            server.archive_batch(self.name, self.name)
        server._advance_state.clear()
        server.save_json(self.folder / "workflow" / "manifest.json", {"stages": {"extract": {"status": "running"}}})
        with self.assertRaisesRegex(ValueError, "正在运行"):
            server.archive_batch(self.name, self.name)
        self.assertTrue(self.folder.exists())

    def test_running_status_is_reported_as_uncertain_without_changing_manifest(self):
        manifest_path = self.folder / "workflow" / "manifest.json"
        for manifest in (
            {"status": "running", "stages": {}},
            {"status": "pending", "stages": {"extract": {"status": "pending"},
                                              "answers": {"status": "running"}}},
        ):
            with self.subTest(manifest=manifest):
                server.save_json(manifest_path, manifest)
                original = manifest_path.read_bytes()
                server._advance_state[self.name] = {"running": False}
                result = server.overview(self.name)
                self.assertTrue(result["stage"]["running_unknown"])
                self.assertIn("实际状态未知", result["stage"]["hint"])
                self.assertIn("外部 CLI 可能仍在运行", result["stage"]["hint"])
                self.assertEqual(manifest_path.read_bytes(), original)
                with self.assertRaisesRegex(ValueError, "正在运行"):
                    server.archive_batch(self.name, self.name)
                self.assertEqual(manifest_path.read_bytes(), original)

    def test_workbench_running_is_distinguished_from_ledger_only(self):
        server.save_json(self.folder / "workflow" / "manifest.json",
                         {"status": "running", "stages": {}})
        server._advance_state[self.name] = {"running": True}
        result = server.overview(self.name)
        self.assertFalse(result["stage"]["running_unknown"])
        self.assertEqual(result["stage"]["hint"], "工作台正在处理")
        server._advance_state.clear()
        server.save_json(self.folder / "workflow" / "manifest.json",
                         {"status": "pending", "stages": {}})
        self.assertFalse(server.overview(self.name)["stage"]["running_unknown"])

    def test_symlink_batch_inbox_trash_and_nested_paths_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        marker = outside / "keep.txt"
        marker.write_text("keep")
        for link in (server.WORKFLOWS / "alias", self.inbox / "nested", self.folder / "nested", self.root / ".workbench-trash"):
            with self.subTest(link=link):
                link.symlink_to(outside, target_is_directory=True)
                name = "alias" if link.name == "alias" else self.name
                with self.assertRaises(ValueError):
                    server.archive_batch(name, name)
                # Only remove this known temporary symlink created by the test.
                link.unlink()
                self.assertEqual(marker.read_text(), "keep")
        self.assertTrue(self.folder.exists())

    def test_second_move_failure_rolls_back_first(self):
        rename = Path.rename
        def fail_inbox(path, target):
            if path == self.inbox:
                raise OSError("simulated move failure")
            return rename(path, target)
        with patch.object(Path, "rename", fail_inbox), self.assertRaises(OSError):
            server.archive_batch(self.name, self.name)
        self.assertTrue(self.folder.exists())
        self.assertTrue(self.inbox.exists())
        receipts = list((self.root / ".workbench-trash").glob("*/receipt.json"))
        self.assertEqual(server.load(receipts[0])["status"], "rolled_back")

    def test_advance_registration_prevents_archive_until_worker_finishes(self):
        entered, release = threading.Event(), threading.Event()
        original_advance = server.auto_flow._advance_locked
        def worker(*args, **kwargs):
            self.assertEqual(kwargs.get("stop_at"), "upload_gate")
            entered.set()
            release.wait(5)
            return {"done": True}
        with patch.object(server.auto_flow, "_advance_locked", worker), patch.object(server.auto_flow, "load_bridge", return_value={}):
            server.start_advance(self.name)
            self.assertTrue(entered.wait(2))
            try:
                with self.assertRaisesRegex(ValueError, "正在运行"):
                    server.archive_batch(self.name, self.name)
            finally:
                release.set()
                # Wait deterministically for the worker's registry update before fixture cleanup.
                for thread in threading.enumerate():
                    if thread.name == f"workbench-advance-{self.name}":
                        thread.join(2)
        self.assertIs(server.auto_flow._advance_locked, original_advance)
        self.assertTrue(self.folder.exists())


class ArchivePluginLayoutTest(unittest.TestCase):
    """插件版布局：代码在 app/（ROOT），数据根是另一层目录。

    ROOT 是代码根，而 WORKFLOWS/INBOX 通过 WF_WORKFLOWS_DIR/WF_INBOX_DIR 指向插件目录。
    删除批次的数据边界必须以数据根（workflows/inbox 的共同父目录）为准，
    不能用 ROOT，否则每次删除都会被误判成"越出工作区"而无法删除。
    """

    def setUp(self):
        self.code_tmp = tempfile.TemporaryDirectory()
        self.data_tmp = tempfile.TemporaryDirectory()
        self.code_root = Path(self.code_tmp.name).resolve()   # 模拟 .../上传插件版/app
        self.data_root = Path(self.data_tmp.name).resolve()   # 模拟 .../上传插件版
        self.patches = [patch.object(server, "ROOT", self.code_root),
                        patch.object(server, "WORKFLOWS", self.data_root / "workflows"),
                        patch.object(server, "INBOX", self.data_root / "inbox"),
                        patch.object(server, "_advance_state", {})]
        for p in self.patches:
            p.start()
        server.WORKFLOWS.mkdir()
        server.INBOX.mkdir()
        self.name = "插件布局批次"
        self.folder = make_batch(server.WORKFLOWS).rename(server.WORKFLOWS / self.name)
        (self.folder / "workflow").mkdir()
        server.save_json(self.folder / "workflow" / "manifest.json", {"status": "pending", "stages": {}})
        self.inbox = server.INBOX / self.name
        self.inbox.mkdir()
        (self.inbox / "source.docx").write_bytes(b"original source")

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.code_tmp.cleanup()
        self.data_tmp.cleanup()

    def test_archive_works_when_root_is_not_the_data_root(self):
        result = server.archive_batch(self.name, self.name)
        archive = self.data_root / result["archive"]
        self.assertTrue(result["recoverable"])
        self.assertEqual(server.load(archive / "receipt.json")["status"], "archived")
        self.assertTrue((archive / "workflows" / "06_batch" / "batch.json").is_file())
        self.assertEqual((archive / "inbox" / "source.docx").read_bytes(), b"original source")
        self.assertFalse(self.folder.exists())
        self.assertFalse(self.inbox.exists())
        self.assertTrue((self.data_root / ".workbench-trash").is_dir())

    def test_inbox_outside_data_root_still_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            stray = Path(outside).resolve() / "inbox"
            stray.mkdir()
            (stray / self.name).mkdir()
            with patch.object(server, "INBOX", stray):
                with self.assertRaisesRegex(ValueError, "越出工作区"):
                    server.archive_batch(self.name, self.name)
        self.assertTrue(self.folder.exists())


if __name__ == "__main__":
    unittest.main()
