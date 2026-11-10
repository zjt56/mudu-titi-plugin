"""确定性解析快路：按题号从 Word 原文直接抄录，不调用模型、不重抄、不比 PDF。"""
import json
import tempfile
import unittest
from pathlib import Path

from workflow import explanations_direct


class ExplanationsDirectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        materials = self.batch / "01_source/materials"
        materials.mkdir(parents=True)
        # 用户上传的 Word 原文：试卷 + 答案 + 解析 + 文章大意同在一份文件。
        # 带 OCR 星号，验证清洗后按题号抄录。
        (materials / "source.txt").write_text(
            "**【文章大意】**这是一篇应用文。\n"
            "**58****.****What's the purpose?**\n"
            "**56****.****A　【解析】细节题:根据第一段可知答案。**\n"
            "**57****.****B　【解析】推断题:由上下文可推断。**\n"
            "**58****.****C　【解析】目的意图题:呼吁捐赠。**\n",
            encoding="utf-8")
        (self.batch / "01_source/structure.confirmed.json").write_text(json.dumps({
            "units": [{"unit_id": "group-56-57-58", "unit_kind": "group",
                       "type": "阅读理解", "numbers": [56, 57, 58]}]}), encoding="utf-8")

    def test_direct_build_copies_word_text_by_number(self):
        payload = explanations_direct.build_payload(self.batch)
        self.assertIsNotNone(payload)
        unit = payload["items"][0]
        self.assertEqual([c["number"] for c in unit["subquestions"]], [56, 57, 58])
        self.assertIn("细节题", unit["subquestions"][0]["explanation"])
        self.assertNotIn("*", unit["subquestions"][0]["explanation"])
        self.assertIn("应用文", unit["articleSummary"])
        self.assertEqual(unit["evidence"], [explanations_direct.DIRECT_COPY_EVIDENCE])

    def test_missing_explanation_uses_placeholder(self):
        (self.batch / "01_source/materials/source.txt").write_text(
            "56. A 【解析】只有56有解析。\n57. B\n58. C\n", encoding="utf-8")
        payload = explanations_direct.build_payload(self.batch)
        subs = {c["number"]: c["explanation"] for c in payload["items"][0]["subquestions"]}
        self.assertIn("只有56有解析", subs[56])
        self.assertEqual(subs[57], "无")
        self.assertEqual(subs[58], "无")

    def test_stray_explanation_line_binds_to_preceding_answer(self):
        (self.batch / "01_source/materials/source.txt").write_text(
            "56. A 【解析】56题解析。\n57. B\n58. C\n\n【解析】58题独立成段的解析。\n",
            encoding="utf-8")
        payload = explanations_direct.build_payload(self.batch)
        subs = {c["number"]: c["explanation"] for c in payload["items"][0]["subquestions"]}
        self.assertIn("58题独立成段", subs[58])

    def test_pdf_fills_fields_word_lacks_but_never_overrides_word(self):
        # Word 只有 56 的解析；57/58 与文章大意只在 PDF 提取文本里。
        (self.batch / "01_source/materials/source.txt").write_text(
            "56. A 【解析】Word 里的56解析。\n57. B\n58. C\n", encoding="utf-8")
        (self.batch / "01_source/materials/attachment_001.txt").write_text(
            "【文章大意】PDF 里的文章大意。\n"
            "56. A 【解析】PDF 改写的56解析。\n"
            "57. B 【解析】PDF 提供的57解析。\n"
            "58. C 【解析】PDF 提供的58解析。\n", encoding="utf-8")
        payload = explanations_direct.build_payload(self.batch)
        unit = payload["items"][0]
        subs = {c["number"]: c["explanation"] for c in unit["subquestions"]}
        self.assertIn("Word 里的56解析", subs[56])      # Word 优先，不被 PDF 覆盖
        self.assertIn("PDF 提供的57解析", subs[57])     # Word 缺失 → PDF 兜底
        self.assertIn("PDF 提供的58解析", subs[58])
        self.assertIn("PDF 里的文章大意", unit["articleSummary"])
        self.assertTrue(any("PDF" in e for e in unit["evidence"]), unit["evidence"])

    def test_pdf_only_builds_payload_when_word_absent(self):
        (self.batch / "01_source/materials/source.txt").unlink()
        (self.batch / "01_source/materials/attachment_001.txt").write_text(
            "【文章大意】PDF 文章大意。\n56. A 【解析】PDF 56。\n57. B 【解析】PDF 57。\n58. C 【解析】PDF 58。\n",
            encoding="utf-8")
        payload = explanations_direct.build_payload(self.batch)
        self.assertIsNotNone(payload)
        subs = {c["number"]: c["explanation"] for c in payload["items"][0]["subquestions"]}
        self.assertIn("PDF 56", subs[56])
        self.assertIn("PDF 文章大意", payload["items"][0]["articleSummary"])

    def test_no_word_source_returns_none_so_model_path_used(self):
        (self.batch / "01_source/materials/source.txt").unlink()
        (self.batch / "01_source/正文.txt").unlink(missing_ok=True)
        self.assertIsNone(explanations_direct.build_payload(self.batch))


if __name__ == "__main__":
    unittest.main()
