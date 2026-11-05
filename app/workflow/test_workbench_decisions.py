"""处理意见与上传的回归测试：不能因为改状态把问题字段写丢。"""
import json
import pathlib
import tempfile
import unittest
import zipfile

from workbench import server


def make_batch(root: pathlib.Path) -> pathlib.Path:
    folder = root / "FIX-001"
    (folder / "07_review").mkdir(parents=True)
    (folder / "workflow").mkdir(parents=True)
    queue = {
        "queue_version": "1.0",
        "items": [
            {
                "finding_id": "WF-0001",
                "unit_id": "Q005",
                "field": "knowledge",
                "severity": "major",
                "blocking": True,
                "status": "open",
                "question": "主题由答案反推，题干无线索。",
            }
        ],
    }
    (folder / "07_review" / "manual-review-queue.json").write_text(
        json.dumps(queue, ensure_ascii=False), encoding="utf-8"
    )
    return folder


class DecisionMergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = make_batch(pathlib.Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def _queue(self):
        return json.loads((self.folder / "07_review" / "manual-review-queue.json").read_text(encoding="utf-8"))

    def test_partial_item_keeps_original_fields(self):
        item = server.apply_decision(self.folder, {"finding_id": "WF-0001", "status": "fixed"}, "tester")
        self.assertEqual(item["status"], "fixed")
        self.assertEqual(item["decided_by"], "tester")
        stored = self._queue()["items"][0]
        self.assertEqual(stored["unit_id"], "Q005")
        self.assertEqual(stored["field"], "knowledge")
        self.assertEqual(stored["severity"], "major")
        self.assertTrue(stored["question"])
        self.assertEqual(len(self._queue()["items"]), 1)

    def test_decision_syncs_same_finding_to_gate_files(self):
        path = self.folder / '07_review/metadata-review.json'
        path.write_text(json.dumps({'findings': [{'finding_id': 'WF-0001', 'status': 'needs_review',
            'reason': '原问题', 'blocking': True}]}), encoding='utf-8')
        server.apply_decision(self.folder, {'finding_id': 'WF-0001', 'status': 'fixed',
            'resolution_note': '已据原卷修正并验证'}, 'tester')
        finding = json.loads(path.read_text())['findings'][0]
        self.assertEqual(finding['status'], 'fixed')
        self.assertEqual(finding['resolution_note'], '已据原卷修正并验证')
        self.assertEqual(finding['decided_by'], 'tester')
        self.assertTrue(finding['blocking'])

    def test_unattributed_queue_status_does_not_resolve_gate_finding(self):
        path = self.folder / '07_review/metadata-review.json'
        path.write_text(json.dumps({'findings': [{'finding_id': 'WF-0001', 'status': 'needs_review'}]}))
        server.sync_review_decisions(self.folder, {'items': [{'finding_id': 'WF-0001', 'status': 'fixed'}]})
        self.assertEqual(json.loads(path.read_text())['findings'][0]['status'], 'needs_review')

    def test_note_is_stored_and_backup_written(self):
        server.apply_decision(self.folder, {"finding_id": "WF-0001", "status": "accepted", "note": "已改知识点"})
        self.assertEqual(self._queue()["items"][0]["note"], "已改知识点")
        self.assertTrue((self.folder / "07_review" / "manual-review-queue.json.bak").exists())
        log = (self.folder / "workflow" / "decisions.jsonl").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(log), 1)
        self.assertIn("Q005", log[0])

    def test_unknown_finding_is_rejected(self):
        with self.assertRaises(ValueError):
            server.apply_decision(self.folder, {"finding_id": "WF-9999", "status": "fixed"})

    def test_bad_status_is_rejected(self):
        with self.assertRaises(ValueError):
            server.apply_decision(self.folder, {"finding_id": "WF-0001", "status": "done"})


class UploadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self._inbox = server.INBOX
        self._workflows = server.WORKFLOWS
        self._advance = server.start_advance
        server.INBOX = root / "inbox"
        server.WORKFLOWS = root / "workflows"
        server.INBOX.mkdir()
        server.WORKFLOWS.mkdir()
        self.started = []
        server.start_advance = lambda name: self.started.append(name) or {"started": True, "running": True}
        self.root = root

    def tearDown(self):
        server.INBOX = self._inbox
        server.WORKFLOWS = self._workflows
        server.start_advance = self._advance
        self.tmp.cleanup()

    def _docx(self, name: str) -> dict:
        path = self.root / name
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("word/document.xml", "<w:document/>")
        return {"field": "files", "filename": name, "data": path.read_bytes()}

    def test_upload_creates_batch_and_starts_flow(self):
        result = server.handle_upload(
            {"title": "2026年安徽省中考英语", "year": "2026", "region": "安徽", "subject": "english"},
            [self._docx("2026年安徽省中考英语.docx")],
        )
        self.assertEqual(result["name"], "2026年安徽省中考英语")
        self.assertEqual(result["paper"]["title"], "2026年安徽省中考英语")
        inbox = server.INBOX / result["name"]
        self.assertTrue((inbox / "source.docx").exists())
        self.assertTrue((inbox / "batch.json").exists())
        self.assertEqual(self.started, [result["name"]])
        # 试卷名不填时用地区+年份兜底，重名自动加后缀
        again = server.handle_upload({"title": "2026年安徽省中考英语"}, [self._docx("A.docx")])
        self.assertNotEqual(again["name"], result["name"])

    def test_upload_without_title_preserves_original_filename(self):
        source_name = "【木牍教育】14年安徽中考英语.docx"
        audio_name = "【木牍教育】14年安徽中考英语音频.mp3"
        result = server.handle_upload({}, [self._docx(source_name),
            {"field": "files", "filename": audio_name, "data": b"ID3 test audio"}])
        self.assertEqual(result["name"], pathlib.Path(source_name).stem)
        descriptor = json.loads((server.INBOX / result["name"] / "batch.json").read_text())
        self.assertEqual(descriptor["original_filenames"]["source"], source_name)
        self.assertEqual(descriptor["original_filenames"]["audio"], audio_name)
        self.assertEqual(descriptor["source"], "source.docx", "内部文件路径保持稳定")
        self.assertEqual(self.started, [result["name"]])

    def test_upload_requires_document(self):
        with self.assertRaises(ValueError):
            server.handle_upload({"title": "x"}, [{"field": "files", "filename": "a.mp3", "data": b""}])


if __name__ == "__main__":
    unittest.main()
