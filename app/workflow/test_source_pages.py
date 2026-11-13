"""Full-page evidence tests; synthetic batches live only in temporary directories."""
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

from workflow import source_pages as pages


def png(width=20, height=30):
    def chunk(kind, content):
        return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\0" + b"\xff" * width * 3) * height)) + chunk(b"IEND", b""))


class SourcePagesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in ("workflow", "01_source", "attachments"):
            (self.root / directory).mkdir()
        self.source = self.root / "attachments/source.docx"
        self.source.write_bytes(b"Word source fixture")
        (self.root / pages.TEXT).write_text("Word text authoritative\n", encoding="utf-8")
        self.manifest({"source": {"path": "attachments/source.docx"}})
        self.env = patch.dict(os.environ, {"DSH_INSTALL_DIR": "", "DSH_NODE_EXECUTABLE": "", "MUDU_SOURCE_PAGES_CONFIG": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.path = patch.object(pages.shutil, "which", return_value=None)
        self.path.start()
        self.addCleanup(self.path.stop)

    def manifest(self, inputs):
        (self.root / "workflow/manifest.json").write_text(json.dumps({"inputs": inputs}), encoding="utf-8")

    def rendered(self, source, output, settings):
        output.mkdir()
        images = []
        for number in (1, 2):
            target = output / f"page-{number:04d}.png"
            target.write_bytes(png())
            images.append({"page": number, "path": target.name})
        (output / "manifest.json").write_text(json.dumps({"source_sha256": pages._sha(source.read_bytes()),
            "page_count": 2, "full_page": True, "pages": images}), encoding="utf-8")
        return "test-command"

    def prepare(self):
        with patch.object(pages, "_render", side_effect=self.rendered):
            return pages.prepare(self.root)

    def test_complete_self_contained_manifest_and_hashes(self):
        result = self.prepare()
        self.assertEqual([p["page"] for p in result], [1, 2])
        report = pages.load_verified(self.root)
        self.assertEqual(report["status"], "verified")
        self.assertEqual(report["source_sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(report["text_sha256"], pages._sha((self.root / pages.TEXT).read_bytes()))
        for row in result:
            self.assertFalse(Path(row["path"]).is_absolute())
            self.assertTrue(row["full_page"])
            self.assertEqual(row["image_sha256"], pages._sha((self.root / row["path"]).read_bytes()))
            self.assertEqual((row["width"], row["height"]), (20, 30))
        self.assertTrue((self.root / pages.ROOT / report["source_snapshot"]).is_file())
        self.assertTrue((self.root / pages.ROOT / report["text_snapshot"]).is_file())
        self.assertNotIn(str(self.root), (self.root / pages.MANIFEST).read_text())

    def test_verified_cache_does_not_render_again(self):
        expected = self.prepare()
        with patch.object(pages, "_render", side_effect=AssertionError("must not render")):
            self.assertEqual(pages.prepare(self.root), expected)

    def test_current_source_and_text_hashes_invalidate_cache(self):
        self.prepare()
        self.source.write_bytes(b"changed original")
        self.assertEqual(pages.load_verified(self.root)["reason"], "page_manifest_stale")
        self.prepare()
        (self.root / pages.TEXT).write_text("changed text")
        self.assertEqual(pages.load_verified(self.root)["reason"], "page_manifest_stale")

    def test_tampered_image_and_snapshot_are_rejected(self):
        result = self.prepare()
        (self.root / result[0]["path"]).write_bytes(png(21, 30))
        self.assertEqual(pages.load_verified(self.root)["reason"], "page_image_sha256_mismatch")
        self.prepare()
        report = pages.load_verified(self.root)
        (self.root / pages.ROOT / report["source_snapshot"]).write_bytes(b"tampered")
        self.assertEqual(pages.load_verified(self.root)["reason"], "source_snapshot_sha256_mismatch")

    def test_missing_renderer_does_not_use_embedded_media(self):
        media = self.root / "01_source/media"
        media.mkdir()
        (media / "tiny.png").write_bytes(png(4, 4))
        (self.root / "01_source/extraction.json").write_text(json.dumps({"source_sha256": pages._sha(self.source.read_bytes()),
            "pages": [{"page": 1, "images": [{"path": str(media / "tiny.png")}]}]}))
        self.assertEqual(pages.prepare(self.root), [])
        self.assertEqual(pages.load_verified(self.root)["status"], "unavailable")
        self.assertEqual(pages.load_verified(self.root)["reason"], "renderer_unavailable")

    def test_word_preferred_even_with_pdf_preview(self):
        pdf = self.root / "attachments/source.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        self.manifest({"source": {"path": "attachments/source.pdf"}, "docx": {"path": "attachments/source.docx"}})
        seen = []
        def render(source, output, config):
            seen.append(source.suffix)
            return self.rendered(source, output, config)
        with patch.object(pages, "_render", side_effect=render):
            self.assertEqual(len(pages.prepare(self.root)), 2)
        self.assertEqual(seen, [".docx"])
        self.assertEqual(pages.load_verified(self.root)["source_sha256"], pages._sha(self.source.read_bytes()))

    def pdf_preview(self, acquisition=False, partial=False):
        self.source = self.root / "attachments/source.pdf"
        self.source.write_bytes(b"%PDF-1.4 fake")
        self.manifest({"source": {"path": "attachments/source.pdf"}})
        preview = self.root / "01_source/full-preview.png"
        preview.write_bytes(png())
        rows = [{"page": 1, "preview": str(preview)}]
        if partial:
            rows.append({"page": 2, "images": [{"path": str(preview)}]})
        evidence = {"source_sha256": pages._sha(self.source.read_bytes()), "pages": rows}
        if acquisition:
            result = self.root / "01_source/acquisition.json"
            result.write_text(json.dumps({"source_sha256": evidence["source_sha256"], "result": {"pages": rows}}))
            (self.root / "01_source/pdf-ocr.json").write_text(json.dumps({"sources": [{"status": "completed",
                "sha256": evidence["source_sha256"], "result_path": "01_source/acquisition.json"}]}))
        else:
            (self.root / "01_source/extraction.json").write_text(json.dumps(evidence))
        return preview

    def test_pdf_page_previews_copied_and_portable(self):
        preview = self.pdf_preview()
        result = pages.prepare(self.root)
        preview.unlink()
        self.assertEqual(len(result), 1)
        self.assertEqual(pages.load_verified(self.root)["status"], "verified")
        self.assertEqual(pages.load_verified(self.root)["renderer"], "pdf-page-previews")

    def test_pdf_ocr_acquisition_full_page_preview_supported(self):
        self.pdf_preview(acquisition=True)
        self.assertEqual(len(pages.prepare(self.root)), 1)

    def test_partial_pdf_previews_never_delivered(self):
        self.pdf_preview(partial=True)
        self.assertEqual(pages.prepare(self.root), [])
        self.assertEqual(pages.load_verified(self.root)["reason"], "full_page_previews_unavailable")

    def test_explicit_config_failure_does_not_fallback_or_use_cache(self):
        self.prepare()
        self.assertEqual(pages.prepare(self.root, {"command": []}), [])
        report = json.loads((self.root / pages.MANIFEST).read_text())
        self.assertEqual(report["reason"], "renderer_command_invalid")
        self.pdf_preview()
        self.assertEqual(pages.prepare(self.root, {"command": ["missing-command", "{input}", "{output_dir}"]}), [])
        self.assertEqual(pages.load_verified(self.root)["reason"], "renderer_unavailable")

    def test_bad_manifest_page_coverage_and_crop_rejected(self):
        for change, reason in (({"page_count": 3}, "renderer_page_coverage_incomplete"),
                               ({"full_page": False}, "renderer_full_page_unproven"),
                               ({"source_sha256": "wrong"}, "rendered_source_sha256_mismatch")):
            with self.subTest(change=change):
                def render(source, output, settings):
                    self.rendered(source, output, settings)
                    path = output / "manifest.json"
                    data = json.loads(path.read_text())
                    data.update(change)
                    path.write_text(json.dumps(data))
                    return "bad"
                with patch.object(pages, "_render", side_effect=render):
                    self.assertEqual(pages.prepare(self.root), [])
                self.assertEqual(pages.load_verified(self.root)["reason"], reason)

    def test_registered_source_hash_mismatch(self):
        self.manifest({"source": {"path": "attachments/source.docx", "sha256": "wrong"}})
        self.assertEqual(pages.prepare(self.root), [])
        self.assertEqual(pages.load_verified(self.root)["reason"], "source_sha256_mismatch")

    def test_command_renderer_real_subprocess_argument_array(self):
        script = self.root / "renderer.py"
        script.write_text('''import hashlib,json,pathlib,sys
source,out=map(pathlib.Path,sys.argv[1:3]);out.mkdir()
(out/'page.png').write_bytes(bytes.fromhex(sys.argv[3]))
(out/'manifest.json').write_text(json.dumps({'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'full_page':True,'page_count':1,'pages':[{'page':1,'path':'page.png'}]}))
''')
        result = pages.prepare(self.root, {"command": [sys.executable, str(script), "{input}", "{output_dir}", png().hex()]})
        self.assertEqual(len(result), 1)
        self.assertEqual(pages.load_verified(self.root)["renderer"], "command")

    def test_official_manifest_accepted(self):
        def render(source, output, config):
            self.rendered(source, output, config)
            path = output / "manifest.json"
            data = json.loads(path.read_text())
            path.write_text(json.dumps({"schemaVersion": 1, "rasterEngine": "libreoffice", "sourceSha256": data["source_sha256"],
                "pageCount": 2, "images": data["pages"], "missingFonts": ["Synthetic Font"]}))
            return "office-kit"
        with patch.object(pages, "_render", side_effect=render):
            self.assertEqual(len(pages.prepare(self.root)), 2)
        self.assertEqual(pages.load_verified(self.root)["missing_fonts"], ["Synthetic Font"])
        self.assertEqual(pages.load_verified(self.root)["warnings"][0]["code"], "MISSING_FONTS")
        self.assertEqual(pages.load_verified(self.root)["requested_dpi"], 144)

    def test_renderer_output_path_escape_rejected(self):
        def render(source, output, config):
            self.rendered(source, output, config)
            outside = output.parent / "outside.png"
            outside.write_bytes(png())
            path = output / "manifest.json"
            data = json.loads(path.read_text())
            data["pages"][0]["path"] = "../outside.png"
            path.write_text(json.dumps(data))
            return "bad"
        with patch.object(pages, "_render", side_effect=render):
            self.assertEqual(pages.prepare(self.root), [])
        self.assertEqual(pages.load_verified(self.root)["reason"], "page_image_outside_output")

    def test_renderer_source_or_text_change_rejected(self):
        def render(source, output, config):
            value = self.rendered(source, output, config)
            (self.root / pages.TEXT).write_text("changed during render")
            return value
        with patch.object(pages, "_render", side_effect=render):
            self.assertEqual(pages.prepare(self.root), [])
        report = json.loads((self.root / pages.MANIFEST).read_text())
        self.assertEqual(report["reason"], "source_or_text_changed_during_render")

    def test_truncated_png_is_unavailable(self):
        def render(source, output, config):
            self.rendered(source, output, config)
            (output / "page-0001.png").write_bytes(png()[:24])
            return "bad"
        with patch.object(pages, "_render", side_effect=render):
            self.assertEqual(pages.prepare(self.root), [])
        self.assertEqual(pages.load_verified(self.root)["reason"], "page_image_invalid")

    def test_cache_path_escape_is_unavailable(self):
        self.prepare()
        path = self.root / pages.MANIFEST
        report = json.loads(path.read_text())
        report["pages"][0]["path"] = "attachments/source.docx"
        path.write_text(json.dumps(report))
        self.assertEqual(pages.load_verified(self.root)["reason"], "page_image_outside_output")

    def test_malformed_batch_manifest_is_unavailable(self):
        (self.root / "workflow/manifest.json").write_text("[]")
        self.assertEqual(pages.prepare(self.root), [])
        self.assertEqual(pages.load_verified(self.root)["reason"], "batch_manifest_invalid")

    def test_timeout_is_explicit(self):
        import subprocess
        with patch.object(pages.subprocess, "run", side_effect=subprocess.TimeoutExpired("renderer", 1)):
            self.assertEqual(pages.prepare(self.root, {"command": ["renderer", "{input}", "{output_dir}"]}), [])
        self.assertEqual(pages.load_verified(self.root)["reason"], "renderer_timeout")


if __name__ == "__main__":
    unittest.main()
