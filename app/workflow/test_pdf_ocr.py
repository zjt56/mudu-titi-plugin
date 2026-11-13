import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import pdf_ocr


class PdfOcrTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.batch = self.root / "batch"
        self.batch.mkdir()
        self.pdf = self.root / "original.PDF"
        self.pdf.write_bytes(b"pdf one")
        self.other = self.root / "answer.pdf"
        self.other.write_bytes(b"pdf two")
        self.word = self.root / "paper.docx"
        self.word.write_bytes(b"word")

    def entry(self, path):
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def test_all_pdf_inputs_snapshots_and_full_results(self):
        manifest = {"inputs": {"source": self.entry(self.word), "answers": self.entry(self.pdf),
                               "attachment_001": self.entry(self.other), "alias": self.entry(self.pdf)}}
        calls = []
        def recognize(source, output, **kwargs):
            calls.append((source.read_bytes(), output, kwargs))
            return {"pages": [{"page": 1, "text": "OCR text", "blocks": [{"bbox": [1, 2, 3, 4]}],
                               "images": {"x": "image.png"}}], "warnings": ["layout uncertain"]}
        config = {"backend": "paddle_jobs"}
        with patch.object(pdf_ocr.ocr_backend, "recognize", side_effect=recognize):
            report = pdf_ocr.run(self.batch, manifest, config)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(len(calls), 2)
        # Every uploaded PDF is fully OCR'd, including the companion PDFs of a
        # Word-primary batch: their JSON is the Word layout reference and must
        # exist even when the OCR assist switch is off.
        for _, _, kwargs in calls:
            self.assertEqual(kwargs, {"force_ocr": True, "config": config, "mandatory_pdf": True})
        self.assertEqual(report['sources'][0]['pages'][0]['text'], 'OCR text')
        first, second, duplicate = report["sources"]
        self.assertEqual(duplicate["duplicate_of"], "answers")
        self.assertEqual(first["result_path"], duplicate["result_path"])
        self.assertEqual(first["markdown_path"], duplicate["markdown_path"])
        self.assertEqual(report["markdown_path"], "01_source/pdf-ocr.md")
        self.assertEqual((self.batch / first["markdown_path"]).name, "acquisition.md")
        self.assertEqual((self.batch / first["markdown_path"]).read_text(), "OCR text")
        aggregate = (self.batch / report["markdown_path"]).read_text()
        self.assertEqual(aggregate.count("OCR text"), 2)
        self.assertNotIn("## alias", aggregate)
        self.assertNotEqual(first["result_dir"], second["result_dir"])
        self.pdf.unlink()
        self.assertEqual((self.batch / first["snapshot"]).read_bytes(), b"pdf one")
        stored = json.loads((self.batch / first["result_path"]).read_text())
        self.assertEqual(stored["source_sha256"], first["sha256"])
        self.assertEqual(stored["markdown_path"], first["markdown_path"])
        self.assertEqual(stored["result"]["pages"][0]["blocks"][0]["bbox"], [1, 2, 3, 4])
        self.assertEqual(json.loads((self.batch / pdf_ocr.REPORT).read_text()), report)

    def test_failures_deduplicated_and_other_sources_continue(self):
        manifest = {"inputs": {"a": self.entry(self.pdf), "b": self.entry(self.pdf),
                               "c": self.entry(self.other)}}
        with patch.object(pdf_ocr.ocr_backend, "recognize", side_effect=[RuntimeError("secret"),
                {"pages": [{"page": 1, "text": "ok"}]}]) as mock:
            report = pdf_ocr.run(self.batch, manifest)
        self.assertEqual(mock.call_count, 2)
        self.assertEqual([x["status"] for x in report["sources"]], ["failed", "failed", "completed"])
        self.assertEqual(report["status"], "failed")
        self.assertNotIn("secret", json.dumps(report))

    def test_hash_mismatch_missing_file_and_empty_results_are_failures(self):
        wrong = self.entry(self.pdf)
        wrong["sha256"] = "wrong"
        manifest = {"inputs": {"wrong": wrong, "missing": {"path": "missing.pdf"},
                               "empty": self.entry(self.other)}}
        with patch.object(pdf_ocr.ocr_backend, "recognize", return_value={"pages": []}) as mock:
            report = pdf_ocr.run(self.batch, manifest)
        self.assertEqual(mock.call_count, 1)
        self.assertEqual(report["status"], "failed")
        self.assertTrue(all(x["status"] == "failed" for x in report["sources"]))
        self.assertIn("SHA-256", report["sources"][0]["error"]["message"])

    def test_companion_pdf_of_word_batch_is_fully_ocrd(self):
        manifest = {"inputs": {"source": self.entry(self.word),
                               "attachment_001": self.entry(self.other)}}
        calls = []
        def recognize(source, output, **kwargs):
            calls.append(kwargs)
            return {"pages": [{"page": 1, "text": "OCR text"}]}
        with patch.object(pdf_ocr.ocr_backend, "recognize", side_effect=recognize):
            report = pdf_ocr.run(self.batch, manifest)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(calls, [{"force_ocr": True, "config": None, "mandatory_pdf": True}])

    def test_markdown_preserves_service_order_and_refreshes_aggregate(self):
        manifest = {"inputs": {"source": self.entry(self.pdf)}}
        pages = [{"page": 2, "text": "# second\n\n| x | y |\n|---|---|"},
                 {"page": 1, "text": "first  \nline"}]
        with patch.object(pdf_ocr.ocr_backend, "recognize", return_value={"pages": pages}):
            report = pdf_ocr.run(self.batch, manifest)
        expected = "\n\n".join(page["text"] for page in pages)
        self.assertEqual((self.batch / report["sources"][0]["markdown_path"]).read_text(), expected)
        self.assertEqual((self.batch / report["markdown_path"]).read_text(), "## source\n\n" + expected)
        # A later failed acquisition must not expose a previous successful body.
        with patch.object(pdf_ocr.ocr_backend, "recognize", side_effect=RuntimeError("private")):
            failed = pdf_ocr.run(self.batch, manifest)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual((self.batch / failed["markdown_path"]).read_text(), "")
        self.assertNotIn("markdown_path", failed["sources"][0])

    def test_no_pdfs(self):
        report = pdf_ocr.run(self.batch, {"inputs": {"source": self.entry(self.word)}})
        self.assertEqual(report["status"], "no_pdfs")


if __name__ == "__main__":
    unittest.main()
