import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from workbench import server


class UploadStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "batch"
        self.folder.mkdir()
        self.workflows = patch.object(server, "WORKFLOWS", self.root)
        self.workflows.start()
        self.addCleanup(self.workflows.stop)
        check = patch.object(server, '_check_upload_preconditions')
        check.start()
        self.addCleanup(check.stop)
        self.jobs = patch.object(server, "_upload_jobs", {})
        self.jobs.start()
        self.addCleanup(self.jobs.stop)

    def test_duplicate_upload_reports_reused_batch_and_uploaded_state(self):
        fake_files = [{"filename": "原卷.docx", "data": b"source-bytes"}]
        with patch.object(server, "auto_flow", object()), \
                patch.object(server, "wf_manifest", SimpleNamespace(load=lambda folder: {})), \
                patch.object(server, "paper_info", return_value={}), \
                patch.object(server, "stage_cards", return_value={}), \
                patch.object(server, "_existing_upload_batch", return_value=self.folder), \
                patch.object(server, "overview", return_value={"uploaded": True}):
            result = server.handle_upload({"title": "原卷"}, fake_files)
        self.assertTrue(result["duplicate"])
        self.assertTrue(result["uploaded"])
        self.assertIn("已上传完成", result["message"])
        self.assertEqual(result["name"], "batch")

    def test_flow_timing_uses_real_stage_timestamps_in_minutes(self):
        manifest = {"created_at": "2026-11-09T22:18:45+08:00", "stages": {
            "extract": {"started_at": "2026-11-09T22:18:45+08:00", "finished_at": "2026-11-09T22:18:53+08:00"},
            "review": {"started_at": "2026-11-09T23:27:49+08:00", "finished_at": "2026-11-09T23:39:14+08:00"},
            "format_gate": {"started_at": "2026-11-09T23:39:28+08:00", "finished_at": "2026-11-09T23:39:28+08:00"}}}
        start, finish, minutes = server._flow_timing(manifest)
        self.assertEqual(start, "2026-11-09T22:18:45+08:00")
        self.assertEqual(finish, "2026-11-09T23:39:28+08:00")
        self.assertEqual(minutes, 81)
        # Missing timestamps and malformed values never crash or invent a duration.
        self.assertEqual(server._flow_timing({"stages": {}}), (None, None, None))
        self.assertEqual(server._flow_timing({"stages": {"extract": {"started_at": "bad", "finished_at": "bad"}}}),
                         (None, None, None))

    def record(self, path, value):
        target = self.folder / "07_upload" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value), encoding="utf-8")

    def test_receipt_survives_restart_and_is_pending_confirmation(self):
        receipt = {"id": "stage-1", "stage": "pending_confirmation", "state": "staged",
                   "payload_sha256": "abc", "remote_confirmation_required": True}
        self.record("receipt.json", receipt)
        self.record("attempts/abc.json", {"state": "submitting", "payload_sha256": "abc"})
        status = server.upload_state("batch")
        self.assertEqual(status["state"], "accepted")
        self.assertEqual(status["receipt"], receipt)
        overview = server.overview("batch")
        self.assertTrue(overview["upload_pending_confirmation"])
        self.assertFalse(overview["uploaded"])
        self.assertEqual(overview["upload_status"]["state"], "accepted")

    def test_idle_and_receipt_precedes_later_failed_job(self):
        self.assertEqual(server.upload_state('batch')['state'], 'idle')
        self.record('receipt.json', {'id': 'stage-1', 'payload_sha256': 'abc'})
        self.record('preflight.json', {'passed': False, 'errors': ['old failure']})
        server._upload_jobs['batch'] = {'running': False, 'ok': False,
                                        'step': '准备上传', 'error': 'later failure'}
        status = server.upload_state('batch')
        self.assertEqual(status['state'], 'accepted')
        self.assertEqual(status['receipt']['id'], 'stage-1')
        self.assertEqual(status['error'], 'later failure')

    def test_unknown_other_attempt_is_not_hidden_by_receipt(self):
        self.record("receipt.json", {"id": "one", "payload_sha256": "abc"})
        self.record("attempts/def.json", {"state": "unknown", "payload_sha256": "def", "details": "timeout"})
        status = server.upload_state("batch")
        self.assertEqual(status["state"], "unknown")
        self.assertEqual(status["error"], "timeout")
        self.assertEqual(status["receipt"]["id"], "one")

    def test_attempt_and_preflight_failures_after_restart(self):
        self.record("attempts/abc.json", {"state": "rejected", "error_code": "FORBIDDEN"})
        status = server.upload_state("batch")
        self.assertEqual((status["state"], status["error"]), ("failed", "FORBIDDEN"))
        (self.folder / "07_upload/attempts/abc.json").unlink()
        self.record("preflight.json", {"passed": False, "errors": ["missing media"]})
        status = server.upload_state("batch")
        self.assertEqual(status["state"], "failed")
        self.assertIn("missing media", status["error"])

    def test_failed_subprocess_reports_actual_step_and_output(self):
        server._upload_jobs["batch"] = {"running": True, "ok": None, "step": "", "output": "", "error": ""}
        failure = SimpleNamespace(returncode=1, stdout="detail\n", stderr="bad preflight\n")
        with patch.object(server, "_run_runner", return_value=failure) as run, \
             patch('workbench.ssh_tunnel.ensure_upload_connection', return_value='ready'):
            server._run_batch_upload("batch", "actor")
        run.assert_called_once()
        status = server.upload_state("batch")
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["step"], "准备上传（冻结请求）")
        self.assertIn("bad preflight", status["error"])
        self.assertIn("detail\nbad preflight", status["output"])

    def test_start_rejects_uncertain_or_accepted_before_opening_tunnel(self):
        self.record('attempts/a.json', {'state': 'unknown', 'payload_sha256': 'a'})
        with patch.object(server.threading, 'Thread') as thread:
            with self.assertRaisesRegex(ValueError, '结果未知'):
                server.start_batch_upload('batch')
            thread.assert_not_called()
        (self.folder / '07_upload/attempts/a.json').unlink()
        self.record('receipt.json', {'id': 'stage-1', 'payload_sha256': 'a'})
        with patch.object(server.threading, 'Thread') as thread:
            with self.assertRaisesRegex(ValueError, '已有上传暂存回执'):
                server.start_batch_upload('batch')
            thread.assert_not_called()

    def test_running_and_unknown_submission(self):
        self.record("attempts/a.json", {"state": "submitting", "payload_sha256": "a"})
        server._upload_jobs["batch"] = {"running": True, "step": "写入云端上传暂存"}
        self.assertEqual(server.upload_state("batch")["state"], "running")
        server._upload_jobs.clear()
        self.assertEqual(server.upload_state("batch")["state"], "unknown")


if __name__ == "__main__":
    unittest.main()
