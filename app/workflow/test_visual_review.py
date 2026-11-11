"""视觉复核模块回归：只测解析/降级/产物，不依赖真实网关。"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import visual_review


class VisualReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / "01_source" / "media").mkdir(parents=True)
        # 建两张最小图片（内容不参与识别，识别被 mock）。
        for name in ("image1.jpeg", "image2.png"):
            (self.root / "01_source" / "media" / name).write_bytes(b"\xff\xd8\xff\xe0stub")

    def test_parse_json_tolerates_wrapped_unescaped_newlines(self):
        text = '说明文字 ```json\n{"kind":"page","title":"版面","contains_text":true,"readable_text":"行1\n行2","summary":"x"}\n``` 结尾'
        obj = visual_review._parse_json(text)
        self.assertIsNotNone(obj)
        self.assertEqual(obj["kind"], "page")
        self.assertIn("行1", obj["readable_text"])

    def test_parse_json_none_on_truncated(self):
        # 被截断的 JSON 无法解析，返回 None（不能伪造）。
        self.assertIsNone(visual_review._parse_json('{"kind":"page","title":"未完成'))

    def test_build_writes_candidate_artifact(self):
        with patch.object(visual_review, "_load_key", return_value="k"), \
             patch.object(visual_review, "_identify",
                          side_effect=lambda path, rel, *a, **kw: {
                              "kind": "icon", "title": "天气图标", "contains_text": False,
                              "readable_text": "", "summary": "图标"}):
            result = visual_review.build(self.root, force=True)
        self.assertEqual(result["status"], "built")
        self.assertEqual(result["image_count"], 2)
        artifact = json.loads((self.root / visual_review.ARTIFACT).read_text(encoding="utf-8"))
        self.assertEqual(artifact["image_count"], 2)
        self.assertTrue(all(it["kind"] == "icon" for it in artifact["items"]))
        # 候选证据边界：note 必须声明不据此放行、原文为准。
        self.assertIn("原文为准", artifact["note"])

    def test_unavailable_without_key_does_not_write(self):
        with patch.object(visual_review, "_load_key", return_value=""):
            result = visual_review.build(self.root, force=True)
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse((self.root / visual_review.ARTIFACT).exists())

    def test_unavailable_when_all_images_fail(self):
        with patch.object(visual_review, "_load_key", return_value="k"), \
             patch.object(visual_review, "_identify",
                          side_effect=visual_review.VisualReviewError("网关挂了")):
            result = visual_review.build(self.root, force=True)
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse((self.root / visual_review.ARTIFACT).exists())

    def test_cached_is_idempotent(self):
        with patch.object(visual_review, "_load_key", return_value="k"), \
             patch.object(visual_review, "_identify",
                          side_effect=lambda path, rel, *a, **kw: {
                              "kind": "other", "title": "t", "contains_text": False,
                              "readable_text": "", "summary": "s"}):
            first = visual_review.build(self.root, force=True)
            second = visual_review.build(self.root)  # 不加 force = 复用
        self.assertEqual(first["status"], "built")
        self.assertEqual(second["status"], "cached")

    def test_temp_extract_dirs_are_skipped(self):
        tmpdir = self.root / "01_source" / "extract-abc" / "media"
        tmpdir.mkdir(parents=True)
        (tmpdir / "page-1.jpeg").write_bytes(b"\xff\xd8stub")
        rels = [rel for _, rel in visual_review._iter_images(self.root)]
        self.assertFalse(any("extract-abc" in r for r in rels))
        self.assertEqual(len(rels), 2)


if __name__ == "__main__":
    unittest.main()
