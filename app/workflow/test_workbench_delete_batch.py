"""继续处理区「删除」：先停到阶段边界、再彻底删除并清缓存。全部使用临时批次。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workbench import server
from workflow import auto
from workflow.test_workbench_group_save import make_batch


class DeleteBatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.patches = [patch.object(server, "ROOT", self.root),
                        patch.object(server, "WORKFLOWS", self.root / "workflows"),
                        patch.object(server, "INBOX", self.root / "inbox"),
                        patch.object(server, "_advance_state", {}),
                        patch.object(server, "_advance_queue", []),
                        patch.object(server, "_upload_jobs", {}),
                        patch.object(server, "_OVERVIEW_CACHE", {}),
                        patch("audio.config.data_dir", return_value=self.root / "audio"),
                        patch("workflow.supervisor.tempfile.gettempdir", return_value=str(self.root))]
        for p in self.patches:
            p.start()
        server.WORKFLOWS.mkdir()
        server.INBOX.mkdir()
        self.folder = make_batch(server.WORKFLOWS).rename(server.WORKFLOWS / "测试批次-删-001")
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

    def test_delete_requires_exact_confirm_name(self):
        with self.assertRaises(ValueError):
            server.delete_batch(self.name, "wrong-name")
        self.assertTrue(self.folder.exists())

    def test_delete_purges_when_idle(self):
        result = server.delete_batch(self.name, self.name)
        self.assertTrue(result["purged"])
        self.assertFalse(self.folder.exists())
        self.assertFalse(self.inbox.exists())
        self.assertEqual(server.list_batches(), [])

    def test_native_cleanup_failure_keeps_batch_and_inbox(self):
        with patch("workflow.content_agent.purge_batch", side_effect=ValueError("native purge failed")):
            with self.assertRaisesRegex(ValueError, "native purge failed"):
                server.delete_batch(self.name, self.name)
        self.assertTrue(self.folder.exists())
        self.assertTrue(self.inbox.exists())

    def test_native_cleanup_precedes_batch_removal(self):
        def cleanup(folder):
            self.assertEqual(folder.resolve(), self.folder.resolve())
            self.assertTrue(folder.exists())
            self.assertTrue(self.inbox.exists())
            return {"removed": 0}
        with patch("workflow.content_agent.purge_batch", side_effect=cleanup) as call:
            result = server.delete_batch(self.name, self.name)
        self.assertTrue(result["purged"])
        call.assert_called_once()

    def test_content_post_receipt_does_not_wait_for_global_delete_lock(self):
        import threading
        done = threading.Event()
        class Handler:
            path = "/api/content-agent/purges/test"
        def do_POST(handler):
            done.set()
        wrapped = server.serialized(do_POST)
        with server._lock:
            worker = threading.Thread(target=wrapped, args=(Handler(),), daemon=True)
            worker.start()
            self.assertTrue(done.wait(1), "native purge acknowledgement was blocked by delete lock")
        worker.join(1)

    def test_delete_requests_cancel_queue_and_purges_when_queued(self):
        # 批次排列在排队区：删除应先移除队列项，再彻底删除。
        server._advance_state[self.name] = {"running": False, "queued": True, "queued_at": "now"}
        server._advance_queue[:] = [self.name, "keep"]
        result = server.delete_batch(self.name, self.name)
        self.assertTrue(result["purged"])
        self.assertFalse(self.folder.exists())
        self.assertEqual(server._advance_queue, ["keep"])
        self.assertNotIn(self.name, server._advance_state)

    def test_delete_writes_cancel_marker_for_running_batch(self):
        # 模拟一个正在运行的批次：删除应写入取消标记（供阶段边界读取），
        # 但因 worker 未真正释放，应在超时内判定为未停稳并拒绝删除。
        server._advance_state[self.name] = {"running": True, "started_at": "now"}
        with patch.object(server, "DELETE_STOP_WAIT_SECONDS", 0.2):
            with self.assertRaises(ValueError) as ctx:
                server.delete_batch(self.name, self.name)
        self.assertIn("运行", str(ctx.exception))
        self.assertTrue(auto.cancel_requested(self.folder))
        self.assertTrue(self.folder.exists())  # 未删除数据

    def test_delete_stops_running_batch_then_purges(self):
        # 运行中的 worker 在收到取消标记后会释放（此处用 side effect 模拟）。
        server._advance_state[self.name] = {"running": True, "started_at": "now"}

        def fake_wait(name, folder, wait_seconds=90.0):
            auto.request_cancel(folder)
            server._advance_state[name] = {"running": False, "cancelled": True}
            return {"stopped": True, "was_running": True}

        with patch.object(server, "_cancel_running_batch", side_effect=fake_wait):
            result = server.delete_batch(self.name, self.name)
        self.assertTrue(result["purged"])
        self.assertFalse(self.folder.exists())
        self.assertNotIn(self.name, server._advance_state)


class CancelBoundaryTest(unittest.TestCase):
    def test_cancel_marker_helpers_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self.assertFalse(auto.cancel_requested(folder))
            auto.request_cancel(folder)
            self.assertTrue(auto.cancel_requested(folder))
            auto.clear_cancel(folder)
            self.assertFalse(auto.cancel_requested(folder))

    def test_advance_impl_stops_at_first_stage_boundary_when_cancel_pending(self):
        from workflow import contracts, manifest as mf
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp) / "B1"
            (batch / "workflow").mkdir(parents=True)
            (batch / "01_source").mkdir()
            (batch / "01_source" / "正文.txt").write_text("hello", encoding="utf-8")
            mf.save(batch, {"workflow_id": "B1", "status": "pending", "stages": {}})
            auto.request_cancel(batch)
            bridge = {s.id: ["offline"] for s in contracts.STAGES}
            out = auto._advance_impl(batch, bridge, None, audio_worker=None)
        self.assertEqual(out.get("action"), "cancelled")
        self.assertIn("删除", out.get("reason", ""))


if __name__ == "__main__":
    unittest.main()
