"""Synthetic loopback HTTP tests for durable OCR recovery (no live OCR)."""
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from workflow import ocr_job_store
from workflow import paddle_jobs


class PaddleRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "synthetic.pdf"
        self.source.write_bytes(b"%PDF-synthetic-ocr-test")
        self.output = self.root / "out"
        self.calls = []
        self.states = ["done"]
        self.post_drop = False
        self.get_failures = 0
        self.get_http = None
        self.batch_jobs = None
        self.batch_id = None
        self.progress_value = {"totalPages": "12", "extractedPages": "3"}
        self.raw = json.dumps({"result": {"layoutParsingResults": [{"markdown": {"text": "Synthetic OCR", "images": {}},
                    "prunedResult": {"parsing_res_list": [{"block_content": "Synthetic OCR", "block_bbox": [0, 0, 10, 10]}]}}]}}) + "\n"
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def respond(self, data, status=200):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                try:
                    self.wfile.write(data if isinstance(data, bytes) else json.dumps(data).encode())
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                owner.calls.append(("POST", self.path))
                match = re.search(rb'name="batchId"\r\n\r\n([^\r]+)', raw)
                owner.batch_id = match.group(1).decode() if match else None
                if owner.post_drop:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                self.respond({"data": {"jobId": "durable-job"}})

            def do_GET(self):
                owner.calls.append(("GET", self.path))
                if "/batch/" in self.path:
                    jobs = owner.batch_jobs if owner.batch_jobs is not None else [{"jobId": "durable-job", "state": "done"}]
                    self.respond({"data": {"batchId": self.path.rsplit("/", 1)[-1], "extractResult": jobs}})
                elif self.path == "/jobs/durable-job":
                    if owner.get_http:
                        status, code = owner.get_http
                        self.respond({"errorCode": code, "errorMsg": "remote secret-private https://signed.example/?signature=secret"}, status)
                    elif owner.get_failures:
                        owner.get_failures -= 1
                        self.respond({"errorCode": 500}, 503)
                    else:
                        state = owner.states.pop(0) if len(owner.states) > 1 else owner.states[0]
                        self.respond({"data": {"state": state, "extractProgress": owner.progress_value,
                                      "resultUrl": {"jsonUrl": owner.origin + "/result.jsonl?signature=secret"}}})
                else:
                    self.respond(owner.raw.encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.origin = "http://127.0.0.1:" + str(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.config = {"endpoint": self.origin + "/jobs", "token": "secret-private", "timeout_seconds": 1,
                       "poll_interval_seconds": .05}

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def recognize(self, **kwargs):
        return paddle_jobs.recognize(self.source, self.output, config=self.config, **kwargs)

    def receipt_path(self):
        return next(self.output.glob("paddle-ocr-*/receipt.json"))

    def test_success_reuses_verified_local_result_without_network(self):
        first = self.recognize()
        calls = list(self.calls)
        self.config.update(token="rotated-token", timeout_seconds=10, poll_interval_seconds=.1)
        second = self.recognize()
        self.assertEqual(self.calls, calls)
        self.assertEqual(first, second)
        receipt = json.loads(self.receipt_path().read_text())
        self.assertEqual(receipt["phase"], "succeeded")
        self.assertEqual(receipt["attempts"]["submit"], 1)
        self.assertEqual(set(receipt["artifacts"]), {"result.json", "result.md", "raw-result.jsonl"})
        for secret in ("secret-private", self.origin, "signature", "raw_jsonl", "resultUrl"):
            self.assertNotIn(secret, self.receipt_path().read_text())
        self.assertEqual(receipt["total_pages"], 12)
        self.assertEqual(receipt["extracted_pages"], 3)

    def test_waiting_retains_id_and_next_call_only_gets(self):
        self.states = ["running"]
        with self.assertRaises(paddle_jobs.PaddleJobsError) as error:
            self.recognize()
        self.assertEqual(error.exception.code, "waiting")
        self.assertEqual(error.exception.job_id, "durable-job")
        self.assertTrue(error.exception.retryable)
        self.assertEqual(error.exception.progress["state"], "running")
        self.states = ["done"]
        self.recognize()
        self.assertEqual(sum(method == "POST" for method, _ in self.calls), 1)

    def test_post_disconnect_is_unknown_then_batch_reconciles_without_post(self):
        self.post_drop = True
        with self.assertRaises(paddle_jobs.PaddleJobsError) as error:
            self.recognize()
        self.assertEqual(error.exception.code, "submission_unknown")
        self.assertIsNone(error.exception.job_id)
        self.assertEqual(json.loads(self.receipt_path().read_text())["phase"], "submission_unknown")
        self.post_drop = False
        result = self.recognize()
        self.assertEqual(result["job_id"], "durable-job")
        self.assertEqual(sum(method == "POST" for method, _ in self.calls), 1)
        self.assertIn(("GET", "/jobs/batch/" + self.batch_id), self.calls)

    def test_batch_empty_or_conflicting_results_never_repost(self):
        self.post_drop = True
        with self.assertRaises(paddle_jobs.PaddleJobsError):
            self.recognize()
        self.batch_jobs = []
        with self.assertRaises(paddle_jobs.PaddleJobsError) as error:
            self.recognize()
        self.assertEqual(error.exception.code, "submission_unknown")
        self.batch_jobs = [{"jobId": "a"}, {"jobId": "b"}]
        with self.assertRaises(paddle_jobs.PaddleJobsError) as error:
            self.recognize()
        self.assertEqual(error.exception.code, "submission_conflict")
        self.assertFalse(error.exception.retryable)
        self.assertEqual(sum(method == "POST" for method, _ in self.calls), 1)

    def test_crashed_submitting_receipt_uses_batch_reconciliation(self):
        config = paddle_jobs.validate_config(self.config)
        identity = ocr_job_store.binding(self.source.read_bytes(), config, paddle_jobs.OPTION_KEYS, file_type=".pdf")
        store = ocr_job_store.JobStore(self.output, identity)
        with store.lock():
            store.load()
            store.save(phase="submitting")
        self.recognize()
        self.assertFalse(any(method == "POST" for method, _ in self.calls))

    def test_corrupt_cached_files_recover_by_get_not_post(self):
        result = self.recognize()
        for filename in ("result.json", "result.md", "raw-result.jsonl"):
            Path(result["artifacts"]["attempt_dir"], filename).write_text("damaged")
            result = self.recognize()
            self.assertEqual(result["pages"][0]["text"], "Synthetic OCR")
        self.assertEqual(sum(method == "POST" for method, _ in self.calls), 1)
        self.assertEqual(sum(path.startswith("/jobs/durable-job") for _, path in self.calls), 4)

    def test_terminal_failures_do_not_resubmit_or_requery(self):
        for status, remote_code, expected in ((404, 11001, "job_not_found"), (400, 11002, "job_expired")):
            with self.subTest(status=status):
                self.output = self.root / ("out-" + str(status))
                self.get_http = status, remote_code
                with self.assertRaises(paddle_jobs.PaddleJobsError) as error:
                    self.recognize()
                self.assertEqual(error.exception.code, expected)
                self.assertEqual(error.exception.job_id, "durable-job")
                calls = list(self.calls)
                with self.assertRaises(paddle_jobs.PaddleJobsError) as error:
                    self.recognize()
                self.assertEqual(error.exception.code, expected)
                self.assertEqual(self.calls, calls)
                self.assertNotIn("secret", self.receipt_path().read_text())
        self.get_http = None
        self.states = ["failed"]
        self.output = self.root / "failed"
        with self.assertRaises(paddle_jobs.PaddleJobsError) as error:
            self.recognize()
        self.assertEqual(error.exception.code, "job_failed")
        calls = list(self.calls)
        with self.assertRaises(paddle_jobs.PaddleJobsError):
            self.recognize()
        self.assertEqual(calls, self.calls)

    def test_each_get_has_bounded_transient_retry(self):
        self.get_failures = 2
        self.recognize()
        self.assertEqual(sum(path == "/jobs/durable-job" for _, path in self.calls), 3)
        self.assertEqual(sum(method == "POST" for method, _ in self.calls), 1)

    def test_exhausted_get_retry_is_waiting_and_resumable(self):
        self.get_failures = 99
        with self.assertRaises(paddle_jobs.PaddleJobsError) as error:
            self.recognize()
        self.assertEqual(error.exception.code, "waiting")
        self.assertEqual(sum(path == "/jobs/durable-job" for _, path in self.calls), 3)
        self.get_failures = 0
        self.recognize()
        self.assertEqual(sum(method == "POST" for method, _ in self.calls), 1)

    def test_scope_and_semantics_split_but_credentials_do_not(self):
        self.recognize()
        self.recognize(task="layout")
        self.recognize(pages=1)
        self.config["useChartRecognition"] = True
        self.recognize()
        self.config["token"] = "rotation"
        self.recognize()
        self.assertEqual(sum(method == "POST" for method, _ in self.calls), 4)
        self.output = self.root / "different-batch"
        self.recognize()
        self.assertEqual(sum(method == "POST" for method, _ in self.calls), 5)

    def test_corrupt_receipt_blocks_network_and_progress_is_safe(self):
        self.recognize()
        self.receipt_path().write_text('{"token":"injected-secret"}')
        calls = list(self.calls)
        with self.assertRaises(paddle_jobs.PaddleJobsError) as error:
            self.recognize()
        self.assertEqual(error.exception.code, "receipt_corrupt")
        self.assertEqual(calls, self.calls)
        self.assertNotIn("injected-secret", json.dumps(paddle_jobs.progress(self.output)))

    def test_callback_gets_safe_progress_and_errors_never_duplicate_post(self):
        seen = []
        self.recognize(progress_callback=seen.append)
        self.assertEqual(seen[-1]["phase"], "succeeded")
        self.assertEqual(seen[-1]["total_pages"], 12)
        self.assertNotIn("secret", json.dumps(seen))
        self.output = self.root / "callback-failure"
        def fail_after_submission(value):
            if value.get("job_id"):
                raise RuntimeError("secret-private")
        with self.assertRaises(paddle_jobs.PaddleJobsError) as error:
            self.recognize(progress_callback=fail_after_submission)
        self.assertEqual(error.exception.code, "storage_error")
        self.assertEqual(error.exception.job_id, "durable-job")
        before = sum(method == "POST" for method, _ in self.calls)
        self.recognize()
        self.assertEqual(sum(method == "POST" for method, _ in self.calls), before)

    def test_process_crash_after_post_retains_submitting_and_recovers_batch(self):
        script = """import json, os, sys
from workflow import paddle_jobs
config = json.loads(sys.argv[1])
class Crash:
    def __call__(self, status):
        if status.get('job_id'):
            os._exit(17)
paddle_jobs.recognize(sys.argv[2], sys.argv[3], config=config, progress_callback=Crash())
"""
        result = subprocess.run([sys.executable, "-c", script, json.dumps(self.config), str(self.source), str(self.output)], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 17, result.stderr.decode())
        self.assertEqual(json.loads(self.receipt_path().read_text())["job_id"], "durable-job")
        self.recognize()
        self.assertEqual(sum(method == "POST" for method, _ in self.calls), 1)

    def test_completion_receipt_is_not_written_when_verification_fails(self):
        with patch.object(ocr_job_store.JobStore, "verify_result", return_value=None):
            with self.assertRaises(paddle_jobs.PaddleJobsError) as error:
                self.recognize()
        self.assertEqual(error.exception.code, "invalid_result")
        self.assertNotEqual(json.loads(self.receipt_path().read_text())["phase"], "succeeded")
        self.recognize()
        self.assertEqual(sum(method == "POST" for method, _ in self.calls), 1)


if __name__ == "__main__":
    unittest.main()
