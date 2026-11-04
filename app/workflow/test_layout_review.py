"""Offline whole-page evidence tests: no OCR models, service or real batches."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock, patch

from workflow import layout_review as lr


class LayoutReviewTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "exam.pdf"
        self.source.write_bytes(b"%PDF-1.7 synthetic input")
        self.output = self.root / "evidence"
        self.report = self.output / "layout-review.json"
        self.client = Mock()
        self.client.submit.return_value = (200, {"jobs": [{"id": "job-1", "status": "queued"}]})

    def start(self, **kwargs):
        return lr.start_review(self.source, self.output, client=self.client, **kwargs)

    def done(self, pages=None, **kwargs):
        return {"id": "job-1", "status": "done", "engine_used": "paddle", "result": {
            "pages": pages if pages is not None else [{"page": 1, "markdown": "1. Which?",
              "blocks": [{"type": "text", "bbox": [1, 2, 100, 30], "text": "1. Which?"}]}]}, **kwargs}

    def resume(self, job=None, status=200):
        self.client.status.return_value = (status, job or self.done())
        return lr.resume_review(self.report, client=self.client)

    def test_submit_resume_preserves_raw_hash_pages_and_never_approves(self):
        initial = self.start(expected_pages=1)
        self.assertEqual(initial["job_id"], "job-1")
        self.assertEqual(initial["source"]["sha256"], lr._hash(self.source))
        result = self.resume()
        self.assertEqual(result["evidence_state"], "layout_available")
        self.assertEqual(result["pages"][0]["blocks"][0]["bbox"], [1, 2, 100, 30])
        self.assertEqual(result["pages"][0]["source_page"], 1)
        self.assertEqual(result["status"], "needs_review")
        self.assertFalse(result["passed"])
        self.assertFalse(result["coverage_verified"])
        self.assertEqual(len(result["raw_results"]), 2)
        for raw in result["raw_results"]:
            self.assertEqual(raw["sha256"], lr._hash(raw["path"]))
        self.assertEqual(json.loads(Path(result["raw_results"][-1]["path"]).read_text())["payload"], self.done())
        self.start()  # Repeated invocation reuses persisted id, never POSTs again.
        self.client.submit.assert_called_once()

    def test_504_with_id_resumes_get_only(self):
        self.client.submit.return_value = (504, {"job_id": "job-1", "note": "still running"})
        self.assertEqual(self.start()["job_id"], "job-1")
        self.start()
        self.resume({"id": "job-1", "status": "running"})
        result = self.resume()
        self.assertEqual(result["evidence_state"], "layout_available")
        self.client.submit.assert_called_once()
        self.assertEqual(self.client.status.call_count, 2)

    def test_network_submit_ambiguity_blocks_reupload_and_can_attach_reconciled_id(self):
        self.client.submit.side_effect = TimeoutError("https://secret.example api_key=secret")
        result = self.start()
        self.assertEqual(result["submission_state"], "submission_unknown")
        self.assertNotIn("secret", self.report.read_text())
        self.start()
        self.client.submit.assert_called_once()
        with self.assertRaisesRegex(lr.LayoutReviewError, "No job id"):
            lr.resume_review(self.report, client=self.client)
        self.client.status.return_value = (200, self.done())
        result = lr.resume_review(self.report, client=self.client, job_id="job-1")
        self.assertEqual(result["job_id"], "job-1")
        self.client.submit.assert_called_once()

    def test_poll_timeout_retains_id_and_can_resume(self):
        self.start()
        self.client.status.side_effect = TimeoutError("private config")
        result = lr.resume_review(self.report, client=self.client)
        self.assertEqual(result["job_id"], "job-1")
        self.assertEqual(result["evidence_state"], "unavailable")
        self.assertNotIn("private config", self.report.read_text())
        self.client.status.side_effect = None
        self.assertEqual(self.resume()["evidence_state"], "layout_available")
        self.client.submit.assert_called_once()

    def test_failed_or_interrupted_job_stays_needs_review(self):
        self.start()
        for state in ("error", "interrupted"):
            result = self.resume({"id": "job-1", "status": state, "error": "paddle unavailable"})
            self.assertEqual(result["status"], "needs_review")
            self.assertEqual(result["evidence_state"], "failed")
            self.assertFalse(result["passed"])
        self.client.submit.assert_called_once()

    def test_no_blocks_and_wrong_engine_are_not_layout_evidence(self):
        self.start()
        for pages in ([], [{"page": 1, "markdown": "text", "blocks": []}],
                      [{"page": 1, "blocks": [{"bbox": [2, 3, 1, 4]}]}],
                      [{"page": 1, "blocks": [{"bbox": [0, 0, True, 5]}]}]):
            result = self.resume(self.done(pages))
            self.assertEqual(result["evidence_state"], "incomplete")
        result = self.resume(self.done(engine_used="unimernet"))
        self.assertIn("document_engine_not_confirmed", result["warnings"])
        self.assertEqual(result["evidence_state"], "incomplete")

    def test_page_coverage_and_duplicate_pages(self):
        self.start(expected_pages=2)
        result = self.resume()
        self.assertIn("page_coverage_incomplete", result["warnings"])
        result = self.resume(self.done(self.done()["result"]["pages"] * 2))
        self.assertIn("invalid_or_duplicate_page", result["warnings"])
        self.assertFalse(result["coverage_verified"])

    def test_docx_requires_explicit_render_and_embedded_image_is_not_whole_page(self):
        docx = self.root / "exam.docx"
        docx.write_bytes(b"PK fake docx")
        for kwargs in ({}, {"rendered_source": self.source}):
            with self.assertRaises(lr.LayoutReviewError):
                lr.start_review(docx, self.output, client=self.client, **kwargs)
        image = self.root / "embedded.png"
        image.write_bytes(b"fake image")
        with self.assertRaisesRegex(lr.LayoutReviewError, "whole_page"):
            lr.start_review(image, self.output, client=self.client)
        self.client.submit.assert_not_called()
        result = lr.start_review(docx, self.output, rendered_source=self.source,
                                 whole_page=True, client=self.client)
        self.assertEqual(result["source"]["sha256"], lr._hash(docx))
        self.assertEqual(result["input"]["sha256"], lr._hash(self.source))
        self.assertEqual(result["input_scope"], "rendered_document")
        self.client.submit.assert_called_once_with(self.source)

    def test_targets_bind_structure_original_text_pages_and_page_image_mapping(self):
        structure = self.root / "structure.json"
        text = self.root / "source.txt"
        text.write_text("[PAGE 7]\n1. Which?\n", encoding="utf-8")
        structure.write_text(json.dumps({"source_sha256": lr._hash(self.source), "unresolved": [
            {"code": "GROUP_BOUNDARY_REVIEW", "line": 2},
            {"code": "UNKNOWN"}, {"code": "OTHER", "page": 9}]}))
        self.start(structure_path=structure, source_text_path=text, page_map={"1": 7})
        result = self.resume()
        target = result["targets"][0]
        self.assertEqual(target["pages_requested"], [7])
        self.assertEqual(target["evidence_refs"][0]["source_page"], 7)
        self.assertEqual(target["evidence_refs"][0]["block_indexes"], [0])
        self.assertEqual(target["status"], "needs_review")
        self.assertEqual(result["targets"][1]["association"], "unmapped")
        self.assertEqual(result["targets"][1]["evidence_refs"], [])
        self.assertEqual(result["targets"][2]["association"], "page_missing")
        self.assertEqual(result["bindings"]["structure"]["sha256"], lr._hash(structure))
        structure.write_text("{}")
        calls = self.client.status.call_count
        self.assertEqual(lr.resume_review(self.report, client=self.client)["evidence_state"], "stale")
        self.assertEqual(self.client.status.call_count, calls)

    def test_source_changes_never_query_or_resubmit(self):
        self.start()
        self.source.write_bytes(b"changed")
        self.assertEqual(lr.resume_review(self.report, client=self.client)["evidence_state"], "stale")
        self.client.status.assert_not_called()
        with self.assertRaises(lr.LayoutReviewError):
            self.start()
        self.client.submit.assert_called_once()

    def test_partial_page_map_never_silently_uses_service_number_as_original(self):
        self.start(page_map={"1": 7})
        pages = self.done()["result"]["pages"]
        pages.append({"page": 2, "blocks": [{"bbox": [0, 0, 20, 20]}]})
        result = self.resume(self.done(pages))
        self.assertEqual(result["evidence_state"], "incomplete")
        self.assertIsNone(result["pages"][1]["source_page"])
        self.assertIn("source_page_mapping_incomplete", result["warnings"])

    def test_changed_binding_or_scope_rejects_reuse(self):
        self.start(page_map={"1": 7})
        with self.assertRaises(lr.LayoutReviewError):
            self.start(page_map={"1": 8})
        self.client.submit.assert_called_once()

    def test_lock_and_preexisting_started_report_prevent_duplicate_post(self):
        self.start()
        report = json.loads(self.report.read_text())
        report["submission_state"], report["job_id"] = "request_started", None
        self.report.write_text(json.dumps(report))
        self.assertEqual(self.start()["submission_state"], "request_started")
        with lr._lock(self.report):
            with self.assertRaisesRegex(lr.LayoutReviewError, "locked"):
                self.start()
        self.client.submit.assert_called_once()

    def test_config_env_precedence_and_no_cli_confusion(self):
        config = self.root / "config.json"
        config.write_text(json.dumps({"base_url": "https://example.invalid", "api_key": "file-key"}))
        with patch.dict(os.environ, {"OCR_CONFIG": str(config), "OCR_BASE_URL": "http://127.0.0.1:8800", "OCR_API_KEY": "env-key"}, clear=True):
            self.assertEqual(lr.load_config()["api_key"], "env-key")
            self.assertEqual(lr.load_config()["base_url"], "http://127.0.0.1:8800")
        config.write_text(json.dumps({"python": "private", "script": "private"}))
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(lr.LayoutReviewError):
            lr.load_config(config)

    def test_http_posts_paddle_layout_and_redacts_credentials_without_health(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.status = 200
        response.read.return_value = b'{"jobs":[{"id":"job-1"}],"api_key":"secret","echo":"secret"}'
        adapter = lr.HTTPWorkbench({"base_url": "https://example.invalid", "api_key": "secret"})
        with patch.object(lr.urllib.request, "urlopen", return_value=response) as request:
            status, payload = adapter.submit(self.source)
        request.assert_called_once()
        sent = request.call_args.args[0]
        self.assertEqual(sent.full_url, "https://example.invalid/api/jobs")
        self.assertIn(b'name="engine"\r\n\r\npaddle', sent.data)
        self.assertIn(b'name="layout"\r\n\r\ntrue', sent.data)
        self.assertNotIn("secret", json.dumps(payload))
        with patch.object(lr.urllib.request, "urlopen", side_effect=TimeoutError) as request:
            with self.assertRaises(TimeoutError):
                adapter.submit(self.source)
        request.assert_called_once()

    def test_existing_health_blocks_failed_document_engine_without_new_request(self):
        health = {"ok": True, "engines": [{"name": "paddle", "state": "error", "ready": False},
                                            {"name": "unimernet", "state": "ready", "ready": True}]}
        result = self.start(health=health)
        self.assertEqual(result["submission_state"], "not_submitted")
        self.assertEqual(result["status"], "needs_review")
        self.assertFalse(result["passed"])
        self.client.submit.assert_not_called()
        self.assertEqual(lr.document_engine_readiness({"ok": True}), "unknown")
        self.assertEqual(lr.document_engine_readiness({"engines": [{"name": "paddle", "state": "idle"}]}), "cold")
        self.assertEqual(lr.document_engine_readiness({"engines": [{"name": "paddle", "ready": True}]}), "ready")

    def test_http_504_body_preserves_job_id(self):
        adapter = lr.HTTPWorkbench({"base_url": "https://example.invalid"})
        error = urllib.error.HTTPError("https://example.invalid", 504, "timeout", {}, io.BytesIO(b'{"job_id":"job-1"}'))
        with patch.object(lr.urllib.request, "urlopen", side_effect=error):
            status, payload = adapter.submit(self.source)
        self.assertEqual((status, payload["job_id"]), (504, "job-1"))


if __name__ == "__main__":
    unittest.main()
