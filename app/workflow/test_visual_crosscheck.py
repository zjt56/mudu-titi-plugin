"""交叉印证回归：Word[IMG]↔视觉语义映射、归属、留痕边界，不写真实批次。"""
import json
import tempfile
import unittest
from pathlib import Path

from workflow import visual_crosscheck


class VisualCrosscheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / "01_source").mkdir(parents=True)

    def _write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def _semantics(self, items):
        self._write("01_source/image_semantics.json",
                    json.dumps({"items": items}, ensure_ascii=False))

    def test_maps_img_to_unit_and_verdict(self):
        self._write("01_source/正文.txt",
                    "Ⅰ.听力\n[IMG image1.jpeg]\n1.填空\n[IMG image9.jpeg]\n")
        self._write("01_source/structure.confirmed.json", json.dumps({"units": [
            {"unit_id": "u1", "type": "听力", "numbers": [1],
             "source_span": {"start_line": 2, "end_line": 5}}]}, ensure_ascii=False))
        self._semantics([
            {"path": "01_source/media/image1.jpeg", "kind": "icon", "title": "标题栏", "contains_text": False},
            {"path": "01_source/media/image9.jpeg", "kind": "material_table", "title": "表格", "contains_text": True},
            {"path": "01_source/media/zzz.jpeg", "kind": "other", "title": "未引用", "contains_text": False},
        ])
        result = visual_crosscheck.build(self.root)
        self.assertEqual(result["status"], "built")
        artifact = json.loads((self.root / visual_crosscheck.ARTIFACT).read_text(encoding="utf-8"))
        rows = {r["file"]: r for r in artifact["rows"]}
        self.assertEqual(rows["image1.jpeg"]["visual_kind"], "icon")
        self.assertEqual(rows["image9.jpeg"]["visual_kind"], "material_table")
        self.assertEqual(rows["image9.jpeg"]["unit_id"], "u1")
        self.assertIn("zzz.jpeg", artifact["unreferenced_images"])
        # 边界：note 必须声明原文为准、不据此放行。
        self.assertIn("原文", artifact["note"])

    def test_skips_without_semantics(self):
        self._write("01_source/正文.txt", "text")
        result = visual_crosscheck.build(self.root)
        self.assertEqual(result["status"], "skipped")
        self.assertFalse((self.root / visual_crosscheck.ARTIFACT).exists())

    def test_flags_other_kind_as_note_not_ok(self):
        self._write("01_source/正文.txt", "[IMG a.jpeg]\n")
        self._write("01_source/structure.confirmed.json", json.dumps({"units": []}))
        self._semantics([{"path": "01_source/media/a.jpeg", "kind": "other", "title": "", "contains_text": False}])
        artifact = json.loads(self._read_artifact())
        self.assertEqual(artifact["rows"][0]["verdict"], "note")

    def test_pure_image_line_flagged(self):
        self._write("01_source/正文.txt", "1.题干\n[IMG x.jpeg]\n")
        self._write("01_source/structure.confirmed.json", json.dumps({"units": [
            {"unit_id": "u1", "type": "阅读", "numbers": [1],
             "source_span": {"start_line": 1, "end_line": 2}}]}))
        self._semantics([{"path": "01_source/media/x.jpeg", "kind": "chart", "title": "图", "contains_text": False}])
        artifact = json.loads(self._read_artifact())
        self.assertFalse(artifact["rows"][0]["line_has_text"])  # 纯图行

    def _read_artifact(self):
        visual_crosscheck.build(self.root)
        return (self.root / visual_crosscheck.ARTIFACT).read_text(encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
