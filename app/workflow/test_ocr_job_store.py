"""Durable receipt and OS lock tests; all inputs are synthetic."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from workflow import ocr_job_store as store
from workflow import paddle_jobs


class OCRJobStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = paddle_jobs.validate_config({"token": "private-value"})
        self.identity = store.binding(b"synthetic", self.config, paddle_jobs.OPTION_KEYS, file_type=".pdf")

    def test_semantic_fingerprint_excludes_credentials_and_timing(self):
        changed = {**self.config, "token": "new-secret", "token_file": "/private/file", "timeout_seconds": 1,
                   "poll_interval_seconds": 1, "backend": "paddle_jobs"}
        self.assertEqual(self.identity, store.binding(b"synthetic", changed, paddle_jobs.OPTION_KEYS, file_type=".pdf"))
        for config, binary, pages, task in (({**self.config, "model": "other"}, b"synthetic", None, "ocr"),
             ({**self.config, "endpoint": "https://other.example/jobs"}, b"synthetic", None, "ocr"),
             ({**self.config, "useChartRecognition": True}, b"synthetic", None, "ocr"),
             (self.config, b"different", None, "ocr"), (self.config, b"synthetic", "1", "ocr"),
             (self.config, b"synthetic", None, "layout")):
            other = store.binding(binary, config, paddle_jobs.OPTION_KEYS, pages=pages, task=task, file_type=".pdf")
            self.assertNotEqual(self.identity["fingerprint"], other["fingerprint"])
        self.assertNotIn("private-value", json.dumps(self.identity))
        self.assertNotIn("https://", json.dumps(self.identity))

    def test_receipt_atomic_roundtrip_and_safe_progress(self):
        job = store.JobStore(self.root, self.identity)
        with job.lock():
            job.load()
            job.save(phase="submitting")
            job.count("submit")
        states = store.progress(self.root)
        self.assertEqual(states[0]["phase"], "submission_unknown")
        self.assertEqual(states[0]["attempts"]["submit"], 1)
        self.assertEqual(states[0]["batch_id"], job.receipt["batch_id"])
        self.assertNotIn("private-value", job.receipt_path.read_text())
        self.assertNotIn("paddleocr.aistudio", job.receipt_path.read_text())
        with job.lock():
            self.assertEqual(job.load()["attempts"]["submit"], 1)
            with self.assertRaises(store.StoreError):
                job.save(token="do-not-write")
        self.assertFalse(list(job.directory.glob(".ocr-write-*")))

    def test_corrupt_receipt_is_fail_closed(self):
        job = store.JobStore(self.root, self.identity)
        with job.lock():
            job.load()
        for content in ("{broken", json.dumps({**job.receipt, "token": "injected"}),
                        json.dumps({**job.receipt, "source": {"sha256": "wrong"}})):
            job.receipt_path.write_text(content)
            with job.lock(), self.assertRaises(store.StoreError):
                job.load()
            self.assertEqual(store.progress(self.root)[0]["last_error"], "receipt_corrupt")
            self.assertNotIn("injected", json.dumps(store.progress(self.root)))

    def test_orphan_artifacts_do_not_allow_fresh_submission(self):
        job = store.JobStore(self.root, self.identity)
        job.directory.mkdir()
        (job.directory / "result.md").write_text("orphan")
        with job.lock(), self.assertRaises(store.StoreError):
            job.load()

    def test_os_cross_process_lock_releases_after_crash(self):
        job = store.JobStore(self.root, self.identity)
        script = """import json, sys
from workflow.ocr_job_store import JobStore
job = JobStore(sys.argv[1], json.loads(sys.argv[2]))
with job.lock():
    job.load()
    job.save(phase='submitting')
    print('locked', flush=True)
    sys.stdin.read()
"""
        process = subprocess.Popen([sys.executable, "-c", script, str(self.root), json.dumps(self.identity)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        self.assertEqual(process.stdout.readline().strip(), "locked")
        with self.assertRaises(store.StoreError) as error:
            with job.lock():
                pass
        self.assertEqual(error.exception.code, "waiting")
        process.kill()
        process.communicate(timeout=5)
        with job.lock():
            self.assertEqual(job.load()["phase"], "submitting")

    def test_callback_cannot_mutate_live_receipt(self):
        def change_snapshot(value):
            value["attempts"]["submit"] = 999
            value["source"]["sha256"] = "bad"
        job = store.JobStore(self.root, self.identity, progress_callback=change_snapshot)
        with job.lock():
            job.load()
            self.assertEqual(job.receipt["attempts"]["submit"], 0)
            self.assertEqual(job.receipt["source"]["sha256"], self.identity["source"]["sha256"])
            self.assertEqual(job.load()["attempts"]["submit"], 0)

    def test_missing_job_handle_or_mutated_binding_is_corrupt(self):
        job = store.JobStore(self.root, self.identity)
        with job.lock():
            job.load()
            for changes in ({"phase": "polling"}, {"job_id": 123}, {"source": {**job.receipt["source"], "size": 900}}):
                bad = {**job.receipt, **changes}
                job.receipt_path.write_text(json.dumps(bad))
                with self.assertRaises(store.StoreError):
                    job.load()

    def test_completion_manifest_checks_media_bytes_and_core_artifacts(self):
        job = store.JobStore(self.root, self.identity)
        with job.lock():
            job.load()
            job.save(phase="downloading", job_id="remote-job")
            media = job.directory / "media/page-0001-images-001.png"
            media.parent.mkdir()
            media.write_bytes(b"image-synthetic")
            result = {"job_id": "remote-job", "pages": [{"text": "OCR text"}], "artifacts": {"images": [str(media)]}}
            (job.directory / "result.json").write_text(json.dumps(result))
            (job.directory / "result.md").write_text("OCR text")
            (job.directory / "raw-result.jsonl").write_text('{}\n')
            job.complete(result)
            self.assertIsNotNone(job.verify_result())
            # On-disk artifact names are POSIX regardless of host separator;
            # a Windows "\" key would not match the media name rule.
            self.assertIn("media/page-0001-images-001.png", job.receipt["artifacts"])
            self.assertNotIn("media\\page-0001-images-001.png", job.receipt["artifacts"])
            media.write_bytes(b"tampered")
            self.assertIsNone(job.verify_result())

    def test_binding_mismatch_and_unsafe_progress_are_rejected(self):
        job = store.JobStore(self.root, self.identity)
        with job.lock():
            job.load()
            bad = {**job.receipt, "config": {**job.receipt["config"], "pages": "https://secret"}}
            job.receipt_path.write_text(json.dumps(bad))
            with self.assertRaises(store.StoreError):
                job.load()
        self.assertEqual(store.progress(self.root)[0]["phase"], "failed")


if __name__ == "__main__":
    unittest.main()
