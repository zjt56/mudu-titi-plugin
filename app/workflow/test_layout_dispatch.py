"""Runner-facing dispatch using temporary batches and mocked HTTP responses."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from workflow import layout_dispatch as dispatch
from workflow import layout_review as layout


class LayoutDispatchTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.batch = Path(temp.name).resolve()
        self.source = self.batch / "exam.pdf"
        self.source.write_bytes(b"%PDF-synthetic")
        self.original = self.batch / "01_source"
        self.original.mkdir()
        self.structure = self.original / "structure.json"
        self.structure.write_text(json.dumps({"source_sha256": layout._hash(self.source),
          "unresolved": [{"code": "GROUP_BOUNDARY_REVIEW", "line": 2, "message": "uncertain"}]}))
        (self.original / "原始正文.txt").write_text("[PAGE 3]\nuncertain\n")
        self.manifest = {"inputs": {"source": {"path": str(self.source), "sha256": layout._hash(self.source)}}}
        self.gate = {"passed": False, "blockers": [{"code": "OPTIONS_MISSING", "source_line": 2}]}
        self.env = patch.dict(os.environ, {"OCR_BASE_URL": "https://example.invalid", "OCR_API_KEY": "SECRET"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def response(self, payload):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.status = 200
        response.read.return_value = json.dumps(payload).encode()
        return response

    def run_dispatch(self):
        return dispatch.prepare_layout_fallback(self.batch, self.manifest, self.gate)

    def test_real_adapter_submit_then_resume_only_get_and_inputs_untouched(self):
        manifest = copy.deepcopy(self.manifest)
        structure_bytes = self.structure.read_bytes()
        with patch.object(layout.urllib.request, "urlopen", side_effect=[
            self.response({"jobs": [{"id": "job-1"}]}),
            self.response({"id": "job-1", "status": "done", "engine_used": "paddle", "result": {
                "pages": [{"page": 1, "markdown": "candidate", "blocks": [{"bbox": [0, 0, 10, 10]}]}]}})
        ]) as http:
            first = self.run_dispatch()
            second = self.run_dispatch()
        self.assertEqual(first["job_id"], "job-1")
        self.assertEqual(second["dispatch_state"], "layout_available")
        self.assertEqual([c.args[0].method for c in http.call_args_list], ["POST", "GET"])
        self.assertEqual(first["status"], "needs_review")
        self.assertNotIn("passed", second)
        self.assertNotIn("SECRET", json.dumps(second))
        self.assertEqual(self.manifest, manifest)
        self.assertEqual(self.structure.read_bytes(), structure_bytes)
        request = json.loads(Path(second["request_path"]).read_text())
        self.assertEqual(len(request["targets"]), 2)
        self.assertEqual(request["targets"][1]["pages_requested"], [3])
        self.assertEqual(request["source"]["sha256"], layout._hash(self.source))
        self.assertTrue(request["bindings"]["structure"]["sha256"])

    def test_no_config_records_unavailable_and_keeps_existing_job_id(self):
        with patch.object(layout.urllib.request, "urlopen", return_value=self.response({"jobs": [{"id": "job-1"}]})):
            self.run_dispatch()
        with patch.dict(os.environ, {}, clear=True), patch.object(layout.urllib.request, "urlopen") as http:
            result = self.run_dispatch()
        self.assertEqual(result["dispatch_state"], "unavailable")
        self.assertEqual(result["job_id"], "job-1")
        self.assertTrue(Path(result["request_path"]).is_file())
        http.assert_not_called()

    def test_missing_config_does_not_submit(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(layout.urllib.request, "urlopen") as http:
            result = self.run_dispatch()
        self.assertEqual(result["reason"], "ocr_http_not_configured")
        self.assertEqual(result["status"], "needs_review")
        self.assertTrue(result["targets"])
        http.assert_not_called()

    def test_submit_timeout_does_not_retry_on_next_runner_invocation(self):
        with patch.object(layout.urllib.request, "urlopen", side_effect=TimeoutError("secret URL")) as http:
            first, second = self.run_dispatch(), self.run_dispatch()
        http.assert_called_once()
        self.assertEqual(first["dispatch_state"], "submission_unknown")
        self.assertEqual(second["dispatch_state"], "submission_unknown")
        self.assertNotIn("secret URL", json.dumps(second))

    def test_status_timeout_retains_id_and_next_invocation_only_queries(self):
        with patch.object(layout.urllib.request, "urlopen", side_effect=[
            self.response({"jobs": [{"id": "job-1"}]}), TimeoutError(),
            self.response({"id": "job-1", "status": "error", "error": "Paddle unavailable"})
        ]) as http:
            self.run_dispatch()
            timeout = self.run_dispatch()
            failed = self.run_dispatch()
        self.assertEqual(timeout["job_id"], "job-1")
        self.assertEqual(failed["dispatch_state"], "failed")
        self.assertEqual([c.args[0].method for c in http.call_args_list], ["POST", "GET", "GET"])

    def docx(self):
        docx = self.batch / "exam.docx"
        docx.write_bytes(b"PK DOCX synthetic")
        self.manifest["inputs"]["source"] = {"path": str(docx)}
        structure = json.loads(self.structure.read_text())
        structure["source_sha256"] = layout._hash(docx)
        self.structure.write_text(json.dumps(structure))
        return docx

    def test_docx_without_render_provides_cli_steps_no_picture_upload(self):
        self.docx()
        with patch.object(layout.urllib.request, "urlopen") as http:
            result = self.run_dispatch()
        self.assertEqual(result["dispatch_state"], "requires_render")
        self.assertIn("--rendered-source", result["next_steps"][-1])
        http.assert_not_called()
        picture = self.batch / "question.png"
        picture.write_bytes(b"image")
        self.manifest["inputs"]["rendered_source"] = {"path": str(picture)}
        self.assertEqual(self.run_dispatch()["dispatch_state"], "requires_render")

    def test_docx_explicit_relative_render_pdf_submits_and_binds_both_hashes(self):
        docx = self.docx()
        self.manifest["inputs"]["layout_pdf"] = {"path": self.source.name, "sha256": layout._hash(self.source)}
        with patch.object(layout.urllib.request, "urlopen", return_value=self.response({"jobs": [{"id": "render-job"}]})) as http:
            request = self.run_dispatch()
        self.assertEqual(request["job_id"], "render-job")
        report = json.loads(Path(request["report_path"]).read_text())
        self.assertEqual(report["source"]["sha256"], layout._hash(docx))
        self.assertEqual(report["input"]["sha256"], layout._hash(self.source))
        self.assertIn(b"%PDF-synthetic", http.call_args.args[0].data)
        self.assertNotIn(b"PK DOCX synthetic", http.call_args.args[0].data)

    def test_registered_hash_mismatch_is_unavailable_without_submission(self):
        self.manifest["inputs"]["source"]["sha256"] = "old"
        with patch.object(layout.urllib.request, "urlopen") as http:
            result = self.run_dispatch()
        self.assertEqual(result["dispatch_state"], "unavailable")
        http.assert_not_called()

    def test_stale_structure_never_reuploads(self):
        with patch.object(layout.urllib.request, "urlopen", return_value=self.response({"jobs": [{"id": "job-1"}]})) as http:
            self.run_dispatch()
            self.structure.write_text(json.dumps({"unresolved": [{"code": "CHANGED"}]}))
            result = self.run_dispatch()
        http.assert_called_once()
        self.assertEqual(result["job_id"], "job-1")
        self.assertEqual(result["status"], "needs_review")

    def test_passed_gate_has_no_side_effects(self):
        self.gate = {"passed": True}
        result = self.run_dispatch()
        self.assertEqual(result["dispatch_state"], "not_required")
        self.assertFalse((self.batch / "07_review").exists())

    def test_malformed_optional_json_does_not_raise_or_print_exception(self):
        self.structure.write_text("broken")
        result = self.run_dispatch()
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["dispatch_state"], "unavailable")


if __name__ == "__main__":
    unittest.main()
