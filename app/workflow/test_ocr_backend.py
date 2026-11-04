"""GPU-free adapter tests; optional PyMuPDF coverage uses a synthetic PDF."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from workflow import ocr_backend as backend


class OCRBackendTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.script = self.root / "local ocr.py"
        self.script.write_text("# placeholder; subprocess is mocked\n", encoding="utf-8")
        self.source = self.root / "input with spaces.pdf"
        self.source.write_bytes(b"%PDF-placeholder")
        self.config = {"python": sys.executable, "script": str(self.script), "timeout_seconds": 10,
                       "lock_timeout_seconds": 1, "max_new_tokens": 2048, "dpi": 180}
        self.output = self.root / "batch"

    def success(self, command, **kwargs):
        output = Path(command[command.index("-o") + 1])
        output.write_text("# recognized\n", encoding="utf-8")
        output.with_suffix(".json").write_text(json.dumps({"model": "test", "device": "cpu", "pages": [
            {"page": 2, "text": "recognized", "method": "ocr", "seconds": 0.1}]}), encoding="utf-8")
        kwargs["stdout"].write("captured diagnostic\n")
        return subprocess.CompletedProcess(command, 0)

    def test_config_precedence_relative_paths_and_defaults(self):
        path = self.root / "config.json"
        path.write_text(json.dumps({"python": sys.executable, "script": self.script.name}), encoding="utf-8")
        with patch.dict(os.environ, {"MUDU_OCR_CONFIG": str(path)}):
            settings = backend.load_config()
            self.assertEqual(settings["script"], str(self.script))
            self.assertEqual(settings["timeout_seconds"], 1800)
            with self.assertRaisesRegex(backend.OCRBackendError, "Cannot load"):
                backend.load_config(self.root / "missing.json")

    def test_config_preserves_venv_python_symlink(self):
        if os.name == "nt":
            self.skipTest("symlink creation may require Windows privilege")
        executable = self.root / "venv-python"
        executable.symlink_to(sys.executable)
        config = backend._validate_config({**self.config, "python": str(executable)}, self.root)
        self.assertEqual(config["python"], str(executable))

    def test_invalid_config_is_terminal(self):
        for payload in ([], {}, {**self.config, "script": ""}, {**self.config, "python": "missing"},
                        {**self.config, "timeout_seconds": 1801}, {**self.config, "timeout_seconds": True},
                        {**self.config, "dpi": 0}, {**self.config, "max_new_tokens": 1.5},
                        {**self.config, "preview_dpi": float("nan")}):
            with self.subTest(payload=payload), self.assertRaises(backend.OCRBackendError):
                backend._validate_config(payload, self.root)
        path = self.root / "broken.json"
        path.write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(backend.OCRBackendError, "Cannot load"):
            backend.load_config(path)

    @patch.object(backend.subprocess, "run")
    def test_ocr_arguments_artifacts_and_no_invented_coordinates(self, run):
        run.side_effect = self.success
        result = backend.recognize(self.source, self.output, force_ocr=True, pages=2,
                                   task="formula", config=self.config)
        command = run.call_args.args[0]
        self.assertIsInstance(command, list)
        self.assertIn(str(self.source), command)
        self.assertIn("--force-ocr", command)
        self.assertEqual(command[command.index("--pages") + 1], "2")
        self.assertEqual(command[command.index("--task") + 1], "formula")
        self.assertEqual(command[command.index("--max-new-tokens") + 1], "2048")
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(run.call_args.kwargs["stderr"], subprocess.STDOUT)
        self.assertEqual(run.call_args.kwargs["timeout"], 10)
        self.assertEqual(result["pages"][0]["blocks"], [])
        self.assertNotIn("confidence", result["pages"][0])
        self.assertIn("coordinates_absent", result["pages"][0]["warnings"])
        self.assertIn("captured diagnostic", Path(result["artifacts"]["log"]).read_text())
        self.assertEqual(result["provenance"]["model"], "test")

    @patch.object(backend.subprocess, "run")
    def test_repeated_attempt_preserves_prior_outputs(self, run):
        run.side_effect = self.success
        first = backend.recognize(self.source, self.output, config=self.config)
        original = Path(first["artifacts"]["markdown"])
        original.write_text("keep prior evidence", encoding="utf-8")
        second = backend.recognize(self.source, self.output, config=self.config)
        self.assertNotEqual(first["artifacts"]["attempt_dir"], second["artifacts"]["attempt_dir"])
        self.assertEqual(original.read_text(), "keep prior evidence")

    @patch.object(backend.subprocess, "run")
    def test_failures_and_timeouts_do_not_retry(self, run):
        for error, expected in ((subprocess.TimeoutExpired("test", 10), "timed out"),
                                (OSError("cannot execute"), "Cannot start"),
                                (subprocess.CompletedProcess([], 7), "exit 7")):
            run.reset_mock()
            if isinstance(error, Exception):
                run.side_effect = error
            else:
                run.side_effect = None
                run.return_value = error
            with self.subTest(expected=expected), self.assertRaisesRegex(backend.OCRBackendError, expected):
                backend.recognize(self.source, self.output, config=self.config)
            self.assertEqual(run.call_count, 1)
        self.assertEqual(len(list(self.output.glob("ocr-*/subprocess.log"))), 3)

    @patch.object(backend.subprocess, "run")
    def test_missing_malformed_and_empty_results_are_not_success(self, run):
        for payload in (None, "{", "{}", '{"pages": []}', '{"pages": [{"page": 0, "text": ""}]}',
                        '{"pages": [{"page": 1, "text": 123}]}'):
            def bad_result(command, **kwargs):
                path = Path(command[command.index("-o") + 1])
                path.write_text("", encoding="utf-8")
                if payload is not None:
                    path.with_suffix(".json").write_text(payload, encoding="utf-8")
                return subprocess.CompletedProcess(command, 0)
            run.side_effect = bad_result
            with self.subTest(payload=payload), self.assertRaises(backend.OCRBackendError):
                backend.recognize(self.source, self.output, config=self.config)

    @patch.object(backend.subprocess, "run")
    def test_invalid_arguments_never_launch(self, run):
        for pages in ("0", "-1", "3-2", "1,3", "1;echo bad", ""):
            with self.subTest(pages=pages), self.assertRaises(backend.OCRBackendError):
                backend.recognize(self.source, self.output, pages=pages, config=self.config)
        with self.assertRaises(backend.OCRBackendError):
            backend.recognize(self.source, self.output, task="unknown", config=self.config)
        with self.assertRaises(backend.OCRBackendError):
            backend.recognize(self.root / "missing.pdf", self.output, config=self.config)
        run.assert_not_called()

    def test_quality_routes_short_corrupt_and_glued_text(self):
        self.assertEqual(backend._quality_reasons("Normal readable words. " * 8), [])
        self.assertEqual(backend._quality_reasons("中文段落" * 30), [])
        self.assertIn("short_text", backend._quality_reasons("page 1"))
        self.assertIn("suspicious_characters", backend._quality_reasons("\ufffd" + "word " * 40))
        self.assertIn("long_glued_run", backend._quality_reasons("a" * 90))

    def test_advisory_lock_excludes_separate_process_and_releases(self):
        settings = backend._validate_config(self.config, self.root)
        code = ("import json,sys; from workflow.ocr_backend import _model_lock, OCRBackendError; "
                "config=json.loads(sys.argv[1]);\n"
                "try:\n with _model_lock(config): print('acquired')\n"
                "except OCRBackendError: sys.exit(9)\n")
        command = [sys.executable, "-c", code, json.dumps(settings)]
        with backend._model_lock(settings):
            blocked = subprocess.run(command, capture_output=True, text=True, timeout=5)
        self.assertEqual(blocked.returncode, 9, blocked.stderr)
        released = subprocess.run(command, capture_output=True, text=True, timeout=5)
        self.assertEqual(released.returncode, 0, released.stderr)
        self.assertIn("acquired", released.stdout)

    @patch.object(backend.subprocess, "run")
    def test_pdf_inspection_uses_configured_interpreter_without_ocr_script(self, run):
        def success(command, **kwargs):
            attempt = Path(command[4])
            (attempt / "result.json").write_text(json.dumps({"pages": [
                {"page": 1, "text": "native", "blocks": [], "images": [], "preview": None}],
                "warnings": []}), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0)
        run.side_effect = success
        result = backend.read_pdf(self.source, self.output, config=self.config)
        command = run.call_args.args[0]
        self.assertEqual(command[0], self.config["python"])
        self.assertEqual(command[2], "--inspect-pdf")
        self.assertNotIn(str(self.script), command)
        self.assertEqual(result["provenance"]["backend"], "pymupdf")

    @unittest.skipUnless(importlib.util.find_spec("pymupdf"), "PyMuPDF not installed in test interpreter")
    def test_real_pdf_worker_retains_boxes_images_and_scan_preview(self):
        import pymupdf
        pdf = pymupdf.open()
        page = pdf.new_page()
        page.insert_textbox(pymupdf.Rect(40, 40, 500, 300), "Native readable text with ordinary words. " * 6)
        scan = pdf.new_page()
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 12, 12), False)
        pixmap.clear_with(200)
        image = pixmap.tobytes("png")
        scan.insert_image(pymupdf.Rect(30, 40, 160, 180), stream=image, keep_proportion=False)
        pdf.save(self.source)
        pdf.close()
        result = backend.read_pdf(self.source, self.output, config=self.config)
        first, second = result["pages"]
        self.assertFalse(first["needs_ocr"])
        self.assertTrue(first["blocks"])
        self.assertEqual(len(first["blocks"][0]["bbox"]), 4)
        self.assertTrue(second["needs_ocr"])
        for actual, expected in zip(second["images"][0]["bbox"], [30, 40, 160, 180]):
            self.assertAlmostEqual(actual, expected, places=3)
        self.assertEqual(Path(second["images"][0]["path"]).read_bytes(), image)
        self.assertTrue(Path(second["preview"]).is_file())
        self.assertNotIn("confidence", second)


if __name__ == "__main__":
    unittest.main()
