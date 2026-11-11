"""Checked scoped image ownership must survive the complete preview response."""
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from workflow import denoise, preview
from workbench import media_audit, server


class ScopedImagePreviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.batch = self.root / "lesson"
        self.source = self.batch / "01_source"
        self.media = self.source / "media"
        self.media.mkdir(parents=True)
        self.lines = ["2026年期末测试卷", "第一课时 Section A", "Ⅰ.按照字母表顺序,写出空白处所缺字母",
                      "[IMG image2.png]", "Ⅱ.根据图片补全对话", "[IMG image3.png]",
                      "Ⅲ.单项填空", "1.What is it?", "A. a B. b C. c D. d", "[IMG footer.png]"]
        self.write(self.source / "正文.txt", "\n".join(self.lines))
        cleaned = denoise.clean_body("\n".join(self.lines))[0].splitlines()
        self.units = []
        for index, image in enumerate(("image2.png", "image3.png"), 1):
            line = cleaned.index(f"[IMG {image}]") + 1
            uid = f"sec-{index}-u1"
            self.units.append({"identity_version": "scoped-v1", "unit_id": uid, "section_id": f"sec-{index}",
                               "unit_kind": "group", "numbers": [1], "printed_number": False,
                               "type": "语音题" if index == 1 else "补全对话", "subtypes": ["看图填空"],
                               "stem": f"[IMG {image}]",
                               "question_ids": {"1": f"{uid}-q1"},
                               "source_section": {"title": cleaned[line - 2], "start_line": line - 1, "end_line": line},
                               "source_span": {"basis": "cleaned_source", "start_line": line, "end_line": line,
                                               "number_binding": "unnumbered_section", "printed_numbers": []}})
        self.save_confirmed()
        items = [dict(unit, id=unit["unit_id"], question_type=unit["type"], material=unit["stem"], subquestions=[])
                 for unit in self.units]
        for name in ("batch.json", "batch_meta.json"):
            self.write(self.batch / "06_batch" / name, {"items": items})
        # First image lies before the first printed question; the second also has banner dimensions.
        for image, width in (("image2.png", 100), ("image3.png", 1000), ("footer.png", 1000)):
            (self.media / image).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", width, 100))

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def save_confirmed(self):
        self.write(self.source / "structure.confirmed.json", {"units": self.units})

    def audit(self):
        return {item["name"]: item for item in media_audit.audit(self.batch, [1])["items"]}

    def test_source_slice_blocks_detail_and_http_keep_both_image_groups(self):
        before = {path: path.read_bytes() for path in (self.source / "正文.txt", self.batch / "06_batch/batch.json")}
        for unit, image in zip(self.units, ("image2.png", "image3.png")):
            with self.subTest(image=image):
                chunk = preview.source_slice(self.batch, [1], unit_id=unit["unit_id"])
                self.assertEqual(chunk, [f"[IMG {image}]"])
                self.assertEqual(server.source_blocks(chunk), [{"type": "image", "name": image}])
                with patch.object(server, "WORKFLOWS", self.root):
                    result = server.detail("lesson", unit["unit_id"])
                    handler = object.__new__(server.Handler)
                    handler.path = f"/api/batches/lesson/units/{unit['unit_id']}"
                    handler.wfile = io.BytesIO()
                    handler.send_response = lambda status: self.assertEqual(status, 200)
                    handler.send_header = lambda *args: None
                    handler.end_headers = lambda: None
                    handler.do_GET()
                    response = json.loads(handler.wfile.getvalue())
                for payload in (result, response):
                    self.assertIn(f"[IMG {image}]", payload["source_preview"])
                    self.assertEqual(payload["hidden_media"], [])
                    self.assertEqual(payload["unit_kind"], "group")
                    self.assertEqual(payload["subtypes"], ["看图填空"])
                    self.assertEqual(payload["source_section"], unit["source_section"])
                    self.assertEqual(payload["printed_numbers"], [])
                    block = next(block for block in payload["preview_blocks"] if block["type"] == "image")
                    self.assertEqual(block["name"], image)
                    self.assertIn(f"/media/{image}", block["url"])
                    self.assertEqual(payload["media"][0]["verdict"], "question")
                    self.assertEqual(payload["media"][0]["unit_ids"], [unit["unit_id"]])
        self.assertEqual(self.audit()["footer.png"]["verdict"], "banner")
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_wide_image_after_printed_question_uses_verified_owner_over_ratio(self):
        lines = self.lines + ["Ⅳ.看图题", "[IMG wide.png]"]
        self.write(self.source / "正文.txt", "\n".join(lines))
        cleaned = denoise.clean_body("\n".join(lines))[0].splitlines()
        line = cleaned.index("[IMG wide.png]") + 1
        unit = dict(self.units[0], unit_id="sec-4-u1", stem="[IMG wide.png]",
                    source_section={"title": cleaned[line - 2], "start_line": line - 1, "end_line": line},
                    source_span={"basis": "ai_classified", "start_line": line, "end_line": line})
        self.units.append(unit)
        self.save_confirmed()
        (self.media / "wide.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", 1000, 100))
        item = self.audit()["wide.png"]
        self.assertEqual(item["verdict"], "question")
        self.assertEqual(item["unit_ids"], ["sec-4-u1"])

    def test_cross_section_or_stale_section_cannot_promote_image(self):
        from copy import deepcopy
        original = deepcopy(self.units)
        for mutation in ('cross_section', 'title', 'bounds', 'context', 'occurrence'):
            with self.subTest(mutation=mutation):
                self.units = deepcopy(original)
                unit = self.units[0]
                if mutation == 'cross_section':
                    # Unique valid stem in a broad window does not own the next section's image.
                    self.units = [unit]
                    unit['source_span']['end_line'] = self.units[0]['source_span']['start_line'] + 2
                elif mutation == 'title':
                    unit['source_section']['title'] = 'Ⅰ.单项填空'
                elif mutation == 'bounds':
                    unit['source_section']['end_line'] += 1
                elif mutation == 'context':
                    unit['source_section']['context'] = '第二课时'
                else:
                    unit['source_section']['occurrence'] = 999
                self.save_confirmed()
                audited = self.audit()
                self.assertEqual(audited['image2.png']['verdict'], 'header')
                self.assertEqual(audited['image2.png']['unit_ids'], [])
                if mutation == 'cross_section':
                    self.assertEqual(audited['image3.png']['verdict'], 'header')
                    self.assertEqual(audited['image3.png']['unit_ids'], [])

    def test_explicit_exclusion_still_hides_verified_image(self):
        media_audit.save_exclusions(self.batch, {"excluded": ["image2.png"], "notes": {"image2.png": "explicit"}})
        self.assertEqual(self.audit()["image2.png"]["verdict"], "excluded")
        with patch.object(server, "WORKFLOWS", self.root):
            result = server.detail("lesson", self.units[0]["unit_id"])
        self.assertEqual(result["hidden_media"], ["image2.png"])
        self.assertFalse(any(block["type"] == "image" for block in result["preview_blocks"]))

    def test_invalid_stale_or_ambiguous_binding_cannot_promote_image(self):
        from copy import deepcopy
        original = deepcopy(self.units)
        for mutation in ("bounds", "stem", "basis", "kind", "duplicate_id", "shared_image", "standalone"):
            with self.subTest(mutation=mutation):
                self.units = deepcopy(original)
                unit = self.units[0]
                if mutation == "bounds":
                    unit["source_span"]["end_line"] = 9999
                elif mutation == "stem":
                    unit["stem"] = "missing original stem"
                elif mutation == "basis":
                    unit["source_span"]["basis"] = "unknown"
                elif mutation == "kind":
                    unit["unit_kind"] = "unknown"
                elif mutation == "duplicate_id":
                    self.units.append(deepcopy(unit))
                elif mutation == "shared_image":
                    self.units.append(dict(deepcopy(unit), unit_id="different-owner"))
                else:
                    # A valid standalone stem elsewhere in a broad span doesn't own an earlier image.
                    cleaned = denoise.clean_body("\n".join(self.lines))[0].splitlines()
                    unit["unit_kind"] = "standalone"
                    unit["stem"] = "1.What is it?"
                    unit["source_span"]["end_line"] = cleaned.index(unit["stem"]) + 1
                self.save_confirmed()
                item = self.audit()["image2.png"]
                self.assertEqual(item["verdict"], "header")
                self.assertEqual(item["unit_ids"], [])


if __name__ == "__main__":
    unittest.main()
