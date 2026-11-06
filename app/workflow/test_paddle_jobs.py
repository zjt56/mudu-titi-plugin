"""Synthetic-input, real HTTP jobs protocol tests. No provider credentials."""
import contextlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from workflow import paddle_jobs
from workflow import ocr_backend


class PaddleJobsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "synthetic.png"
        # A minimal synthetic PNG; never a user file.
        import base64
        self.source.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aRXYAAAAASUVORK5CYII="))
        self.calls = []
        self.states = ["pending", "running", "done"]
        self.result = {"result": {"layoutParsingResults": [
            {"markdown": {"text": "# Synthetic OCR\nHello 123", "images": {"img.png": "https://example.org/img.png"}}, "outputImages": {"preview": "https://example.org/preview.png"}},
            {"markdown": {"text": "Page two", "images": {}}}]}}
        self.redirect = False
        self.http_fail = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def respond(self, body, content_type="application/json"):
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                owner.calls.append(("POST", self.path, dict(self.headers), body))
                if owner.redirect:
                    self.send_response(307)
                    self.send_header("Location", owner.origin + "/leak")
                    self.end_headers()
                elif owner.http_fail:
                    self.send_response(401)
                    self.end_headers()
                    self.wfile.write(json.dumps({"errorCode": "INVALID_TOKEN", "errorMsg": "Invalid token synthetic-test-token at https://service.example/jobs?token=synthetic-test-token", "debug": "secret response must never appear"}).encode())
                else:
                    self.respond(json.dumps({"data": {"jobId": "job-test"}}).encode())

            def do_GET(self):
                owner.calls.append(("GET", self.path, dict(self.headers), b""))
                if self.path.startswith("/media/"):
                    self.respond(owner.source.read_bytes(), "image/png")
                elif self.path == "/redirect-media":
                    self.send_response(302)
                    self.send_header("Location", owner.origin + "/media/should-not-follow.png")
                    self.end_headers()
                elif self.path == "/jobs/job-test":
                    state = owner.states.pop(0) if len(owner.states) > 1 else owner.states[0]
                    self.respond(json.dumps({"data": {"state": state, "resultUrl": {"jsonUrl": owner.origin + "/result.jsonl"}}}).encode())
                else:
                    text = owner.result if isinstance(owner.result, str) else json.dumps(owner.result) + "\n"
                    self.respond(text.encode(), "application/jsonl")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.origin = "http://127.0.0.1:" + str(self.server.server_address[1])
        self.result["result"]["layoutParsingResults"][0]["markdown"]["images"]["img.png"] = self.origin + "/media/image.png"
        self.result["result"]["layoutParsingResults"][0]["outputImages"]["preview"] = self.origin + "/media/preview.png"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.config = {"backend": "paddle_jobs", "endpoint": self.origin + "/jobs", "token": "synthetic-test-token", "timeout_seconds": 5, "poll_interval_seconds": 0.01}

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_url_full_polling_and_jsonl_normalization(self):
        result = paddle_jobs.run_job(self.config, file_url="https://example.org/synthetic.png")
        self.assertEqual([x[:2] for x in self.calls], [("POST", "/jobs"), ("GET", "/jobs/job-test"), ("GET", "/jobs/job-test"), ("GET", "/jobs/job-test"), ("GET", "/result.jsonl")])
        submitted = json.loads(self.calls[0][3])
        self.assertEqual(submitted["fileUrl"], "https://example.org/synthetic.png")
        self.assertEqual(submitted["model"], "PaddleOCR-VL-1.6")
        self.assertEqual(submitted["optionalPayload"], {key: False for key in paddle_jobs.OPTION_KEYS})
        self.assertTrue(all(call[2].get("Authorization") == "Bearer synthetic-test-token" for call in self.calls[:-1]))
        self.assertNotIn("Authorization", self.calls[-1][2])
        self.assertEqual(result["pages"][1]["text"], "Page two")
        self.assertIn("img.png", result["pages"][0]["images"])
        self.assertIn("preview", result["pages"][0]["outputImages"])
        self.assertNotIn("synthetic-test-token", json.dumps(result))

    def test_multipart_workflow_adapter_pages_and_artifacts(self):
        result = ocr_backend.recognize(self.source, self.root / "out", config=self.config, pages=2)
        submitted = self.calls[0]
        self.assertIn("multipart/form-data; boundary=", submitted[2]["Content-Type"])
        self.assertIn(self.source.read_bytes(), submitted[3])
        self.assertIn(b'name="model"', submitted[3])
        self.assertIn(b'name="optionalPayload"', submitted[3])
        self.assertNotIn(str(self.root).encode(), submitted[3])
        self.assertEqual(result["pages"][0]["page"], 2)
        self.assertEqual(Path(result["artifacts"]["markdown"]).read_text(), "Page two")
        self.assertEqual(json.loads(Path(result["artifacts"]["json"]).read_text())["provenance"]["backend"], "paddle_jobs")

    def test_failed_job_and_malformed_jsonl(self):
        self.states = ["failed"]
        with self.assertRaises(paddle_jobs.PaddleJobsError) as error:
            paddle_jobs.run_job(self.config, file_path=self.source)
        self.assertEqual(error.exception.code, "job_failed")
        self.assertEqual(sum(call[0] == "POST" for call in self.calls), 1)
        self.states = ["done"]
        for raw in ("broken", "{}", '{"result":{"layoutParsingResults":[]}}', '{"result":{"layoutParsingResults":[{"markdown":{"text":4}}]}}'):
            self.result = raw
            with self.subTest(raw=raw), self.assertRaises(paddle_jobs.PaddleJobsError) as error:
                paddle_jobs.run_job(self.config, file_path=self.source)
            self.assertEqual(error.exception.code, "invalid_result")

    def test_timeout_no_resubmission(self):
        self.states = ["running"]
        with self.assertRaises(paddle_jobs.PaddleJobsError) as error:
            paddle_jobs.run_job({**self.config, "timeout_seconds": 1, "poll_interval_seconds": 0.2}, file_path=self.source)
        self.assertEqual(error.exception.code, "timeout")
        self.assertEqual(sum(call[0] == "POST" for call in self.calls), 1)

    def test_redirect_and_remote_errors_never_echo(self):
        for redirect in (True, False):
            self.redirect = redirect
            self.http_fail = not redirect
            with self.assertRaises(paddle_jobs.PaddleJobsError) as error:
                paddle_jobs.run_job(self.config, file_path=self.source)
            self.assertEqual(error.exception.code, "unsafe_redirect" if redirect else "http_error")
            self.assertNotIn("secret", str(error.exception))
            self.assertNotIn("synthetic-test-token", str(error.exception))
            self.assertNotIn("https://", str(error.exception))
            self.assertEqual(error.exception.details["http_status"], 307 if redirect else 401)
            if not redirect:
                self.assertEqual(error.exception.details["error_code"], "INVALID_TOKEN")
                self.assertIn("Invalid token", error.exception.details["error_message"])
        self.assertEqual(len(self.calls), 2)

    def test_invalid_sources_and_config_fail_before_submit(self):
        for config in ({**self.config, "endpoint": "http://public.example/jobs"}, {**self.config, "token": "a\nb"}, {**self.config, "timeout_seconds": True}, {**self.config, "useChartRecognition": "true"}):
            with self.assertRaises(paddle_jobs.PaddleJobsError):
                paddle_jobs.run_job(config, file_path=self.source)
        for source in ({}, {"file_path": self.source, "file_url": "https://example.org/test.png"}, {"file_path": self.root / "missing.png"}, {"file_url": "file:///etc/passwd"}):
            with self.assertRaises(paddle_jobs.PaddleJobsError):
                paddle_jobs.run_job(self.config, **source)
        self.assertEqual(self.calls, [])

    def test_media_downloads_safe_paths_relative_markdown_and_failure_warnings(self):
        self.states = ["done"]
        self.result = {"result": {"layoutParsingResults": [{"markdown": {
            "text": "![figure](../../escape.png)\n![blocked](bad.png)",
            "images": {"../../escape.png": self.origin + "/media/image.png", "bad.png": self.origin + "/redirect-media"}},
            "outputImages": {"../../preview.png": self.origin + "/media/preview.png"}}]}}
        result = ocr_backend.recognize(self.source, self.root / "out", config=self.config)
        attempt = Path(result["artifacts"]["attempt_dir"])
        self.assertEqual(len(result["artifacts"]["images"]), 2)
        for filename in result["artifacts"]["images"]:
            path = Path(filename)
            self.assertTrue(path.is_relative_to(attempt))
            self.assertEqual(path.read_bytes(), self.source.read_bytes())
        text = Path(result["artifacts"]["markdown"]).read_text()
        self.assertIn("](media/page-0001-images-001.png)", text)
        self.assertNotIn("](../../escape.png)", text)
        self.assertFalse((self.root / "escape.png").exists())
        self.assertTrue(any("media_download_failed" in warning for warning in result["warnings"]))
        self.assertFalse(any(call[1] == "/media/should-not-follow.png" for call in self.calls))
        for call in self.calls:
            if call[1].startswith("/media/") or call[1] == "/redirect-media":
                self.assertNotIn("Authorization", call[2])

    def test_private_config_becomes_live_without_environment_token(self):
        config_path = self.root / "private.json"
        key_path = self.root / "token"
        with patch.dict(os.environ, {"MUDU_PADDLE_OCR_CONFIG": str(config_path)}):
            key_path.write_text("synthetic-test-token")
            config_path.write_text(json.dumps({k: v for k, v in {**self.config, "token_file": str(key_path)}.items() if k != "token"}))
            result = ocr_backend.recognize(self.source, self.root / "out")
            self.assertEqual(result["provenance"]["backend"], "paddle_jobs")
            key_path.write_text("")
            with self.assertRaisesRegex(ocr_backend.OCRBackendError, "token"):
                ocr_backend.recognize(self.source, self.root / "out")

    def test_native_pdf_keeps_legacy_interpreter(self):
        local_script = self.root / "local.py"
        local_script.write_text("# synthetic local OCR")
        local = self.root / "local.json"
        import sys
        local.write_text(json.dumps({"python": sys.executable, "script": str(local_script)}))
        pdf = self.root / "synthetic.pdf"
        pdf.write_bytes(b"%PDF-synthetic")
        def run(command, settings, log):
            self.assertEqual(settings["python"], sys.executable)
            Path(command[4], "result.json").write_text(json.dumps({"pages": [{"page": 1, "text": "Native", "images": []}], "warnings": []}))
        with patch.dict(os.environ, {"MUDU_OCR_CONFIG": str(local)}), patch.object(ocr_backend, "_run", side_effect=run):
            result = ocr_backend.read_pdf(pdf, self.root / "out", config=self.config)
        self.assertEqual(result["provenance"]["backend"], "pymupdf")


if __name__ == "__main__":
    unittest.main()
