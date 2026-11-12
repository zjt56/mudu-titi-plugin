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

    def test_structured_service_blocks_preserve_reading_order_and_raw_fields(self):
        # Official response schema, also confirmed by GET of an existing done
        # job: prunedResult.parsing_res_list; never order by the block bbox.
        blocks = [
            {"block_label": "text", "block_content": "Left bottom", "block_bbox": [10, 200, 80, 240],
             "block_order": 1, "block_id": 7, "group_id": 3, "block_polygon_points": [[10, 200], [80, 240]]},
            {"block_label": "table", "block_content": "<table>Right top</table>", "block_bbox": [100, 10, 200, 80],
             "block_order": None, "block_id": 2, "future_field": {"kept": True}}]
        self.result["result"]["layoutParsingResults"] = [{"markdown": {"text": "service markdown", "images": {}},
            "prunedResult": {"width": 300, "height": 400, "parsing_res_list": blocks}}]
        result = paddle_jobs.run_job(self.config, file_path=self.source)
        page = result["pages"][0]
        self.assertEqual(page["text"], "service markdown")
        self.assertEqual([b["block_id"] for b in page["blocks"]], [7, 2])
        self.assertEqual([b["service_index"] for b in page["blocks"]], [0, 1])
        for source, actual in zip(blocks, page["blocks"]):
            self.assertEqual({key: actual[key] for key in source}, source)
            self.assertEqual(actual["text"], source["block_content"])
            self.assertEqual(actual["type"], source["block_label"])
            self.assertEqual(actual["bbox"], source["block_bbox"])
            self.assertEqual(actual["order"], source["block_order"])
        self.assertNotIn("coordinates_absent", result["warnings"])
        self.assertFalse(result["provenance"]["layout_verified"])
        raw = json.loads(result["raw_jsonl"])
        self.assertEqual(raw["result"]["layoutParsingResults"][0]["prunedResult"]["width"], 300)
        self.assertEqual(paddle_jobs.normalize_jsonl(result["raw_jsonl"])["pages"][0]["blocks"], page["blocks"])

    def test_coordinate_warnings_reflect_missing_partial_and_invalid_data(self):
        base = {"markdown": {"text": "legacy-compatible"}}
        valid = {"block_bbox": [0, 0, 20, 30], "block_content": "text", "block_label": "text"}
        for raw_blocks, expected in (([], "coordinates_absent"), ([valid, {"block_content": "missing"}], "coordinates_partial"),
                                     ([{**valid, "block_bbox": [20, 0, 0, 30]}], "coordinates_invalid"),
                                     ([{**valid, "block_bbox": [True, 0, 20, 30]}], "coordinates_invalid")):
            raw = json.dumps({"result": {"layoutParsingResults": [{**base, "prunedResult": {"parsing_res_list": raw_blocks}}]}})
            with self.subTest(expected=expected, blocks=raw_blocks):
                result = paddle_jobs.normalize_jsonl(raw)
                self.assertIn(expected, result["warnings"])
                self.assertEqual(len(result["pages"][0]["blocks"]), len(raw_blocks))
        legacy = paddle_jobs.normalize_jsonl(json.dumps({"result": {"layoutParsingResults": [base]}}))
        self.assertEqual(legacy["pages"][0]["blocks"], [])
        self.assertIn("coordinates_absent", legacy["warnings"])

    def test_raw_jsonl_all_rows_survive_page_selection_and_credentials_are_redacted(self):
        signed = self.origin + "/media/image.png?X-Amz-Signature=sign-secret&token=other-token"
        self.result["unknown_service_field"] = {"authorization": "Bearer unknown-secret", "apiKey": "unknown-key",
                                                 "nested": {"signature": "another-secret", "url": signed}}
        page = self.result["result"]["layoutParsingResults"][0]
        page["markdown"]["text"] = "![figure](" + signed + ") synthetic-test-token"
        page["markdown"]["images"] = {"img.png": signed}
        page["prunedResult"] = {"parsing_res_list": [{"block_content": "synthetic-test-token", "block_bbox": [0, 0, 1, 1]}]}
        first = json.dumps(self.result)
        self.result = first + "\n" + json.dumps({"result": {"layoutParsingResults": [{"markdown": {"text": "third page"}}]}})
        result = paddle_jobs.recognize(self.source, self.root / "out", config=self.config, pages=2)
        self.assertEqual([p["page"] for p in result["pages"]], [2])
        raw_path = Path(result["artifacts"]["raw_jsonl"])
        rows = [json.loads(line) for line in raw_path.read_text().splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["result"]["layoutParsingResults"][0]["markdown"]["text"], "third page")
        for path in (raw_path, Path(result["artifacts"]["json"]), Path(result["artifacts"]["markdown"])):
            text = path.read_text()
            for secret in ("synthetic-test-token", "sign-secret", "other-token", "unknown-secret", "unknown-key", "another-secret", "X-Amz-Signature"):
                self.assertNotIn(secret, text)
        self.assertIn("unknown_service_field", rows[0])

    def test_signed_media_downloads_before_redaction_even_when_download_fails(self):
        signed = self.origin + "/media/image.png?signature=private-signature"
        self.result["result"]["layoutParsingResults"] = [{"markdown": {"text": "![figure](" + signed + ")", "images": {"figure.png": signed}},
            "outputImages": {"bad": self.origin + "/redirect-media?token=unknown-secret"}}]
        result = paddle_jobs.recognize(self.source, self.root / "out", config=self.config)
        self.assertEqual(len(result["artifacts"]["images"]), 1)
        self.assertTrue(any(call[1] == "/media/image.png?signature=private-signature" for call in self.calls))
        self.assertIn("](media/page-0001-images-001.png)", Path(result["artifacts"]["markdown"]).read_text())
        self.assertNotIn("private-signature", json.dumps(result))
        self.assertNotIn("unknown-secret", json.dumps(result))
        self.assertNotIn("private-signature", Path(result["artifacts"]["raw_jsonl"]).read_text())

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
