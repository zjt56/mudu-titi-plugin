"""Focused deterministic copying, passage ownership and source precedence tests."""
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
        self.assertIn(explanations_direct.DIRECT_COPY_EVIDENCE, unit["evidence"])
        self.assertTrue(any("source.txt:3" in e for e in unit["evidence"]))

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

    def write_word(self, text):
        (self.batch / "01_source/materials/source.txt").write_text(text, encoding="utf-8")

    def set_units(self, groups):
        (self.batch / "01_source/structure.confirmed.json").write_text(json.dumps({
            "units": [{"unit_id": uid, "unit_kind": "group", "type": "阅读理解",
                       "numbers": numbers} for uid, numbers in groups]}), encoding="utf-8")

    def test_image_labels_preserve_actual_prose_and_multiline(self):
        self.write_word(
            "A\n[IMG a.jpeg]文章大意[IMG b.jpeg]第一段大意。\n第二段大意。\n"
            "56.A [IMG x.jpeg]解析[IMG x.jpeg]A项意为甲。\n续行保留。\n\n再一段。\n"
            "57.B [解析]第二题。\n58.C\n[IMG x.jpeg]解析[IMG y.jpeg]第三题。\n"
            "[IMG a.jpeg]评分标准[IMG b.jpeg]\n不得吞入。\n")
        item = explanations_direct.build_payload(self.batch)["items"][0]
        self.assertEqual(item["articleSummary"], "第一段大意。\n第二段大意。")
        self.assertEqual(item["subquestions"][0]["explanation"], "A项意为甲。\n续行保留。\n\n再一段。")
        self.assertEqual(item["subquestions"][2]["explanation"], "第三题。")

    def test_answer_label_and_bare_image_are_not_explanation(self):
        self.write_word("56. 【答案】A\n57.B [IMG x.jpeg]\n58.C\n")
        item = explanations_direct.build_payload(self.batch)["items"][0]
        self.assertEqual([c["explanation"] for c in item["subquestions"]], ["无"] * 3)

    def test_stray_label_does_not_cross_a_heading(self):
        self.write_word("56.A\nB\n【解析】不能属于56。\n57.B\n58.C\n")
        item = explanations_direct.build_payload(self.batch)["items"][0]
        self.assertEqual(item["subquestions"][0]["explanation"], "无")

    def test_annotations_and_tables_stop_explanation(self):
        for boundary in ("【熟词生义】", "[TABLE 1]", "Ⅷ.阅读理解", "Passage B"):
            with self.subTest(boundary=boundary):
                self.write_word("56.A 【解析】原解析。\n" + boundary + "\n不属于解析。\n57.B\n58.C\n")
                item = explanations_direct.build_payload(self.batch)["items"][0]
                self.assertEqual(item["subquestions"][0]["explanation"], "原解析。")

    def test_answer_ranges_end_explanations_without_becoming_owners(self):
        for separator in ('~', '～', '—', '-'):
            with self.subTest(separator=separator):
                self.write_word('56.A 【解析】原解析。\n57' + separator + '58 BC\n【解析】无逐题归属。\n')
                item = explanations_direct.build_payload(self.batch)['items'][0]
                self.assertEqual([r['explanation'] for r in item['subquestions']], ['原解析。', '无', '无'])
        self.write_word('56.A 【解析】原解析。\n57~58 BC\n56.A 【解析】真实异文。\n')
        self.assertIsNone(explanations_direct.build_payload(self.batch))

    def test_distinct_duplicate_explanations_decline_fast_path(self):
        for text in ("56.A 【解析】第一份。\n56.A 【解析】另一份。\n",
                     "56.A 【解析】第一份。\n【解析】另一份。\n"):
            with self.subTest(text=text):
                self.write_word(text)
                self.assertIsNone(explanations_direct.build_payload(self.batch))

    def test_conflict_in_word_cannot_be_filled_from_pdf(self):
        self.write_word("56.A 【解析】一。\n56.A 【解析】二。\n")
        (self.batch / "01_source/materials/attachment_001.txt").write_text(
            "56.A 【解析】PDF。\n", encoding="utf-8")
        self.assertIsNone(explanations_direct.build_payload(self.batch))

    def test_compact_multiple_question_anchors_decline_fast_path(self):
        self.write_word("56.A 57.B【解析】无法唯一归属。\n")
        self.assertIsNone(explanations_direct.build_payload(self.batch))

    def test_missing_first_summary_does_not_shift_later_passage(self):
        self.set_units([("first", [56, 57]), ("second", [58, 59])])
        self.write_word("A\n56.A 【解析】一。\n57.B 【解析】二。\nB\n"
                        "【文章大意】第二篇。\n58.A 【解析】三。\n59.B 【解析】四。\n")
        items = explanations_direct.build_payload(self.batch)["items"]
        self.assertEqual([i["articleSummary"] for i in items], ["无", "第二篇。"])

    def test_unanchored_or_partial_summary_declines_fast_path(self):
        for text in ("【文章大意】孤立的大意。\nB\n56.A 【解析】一。\n",
                     "【文章大意】只绑定56。\n56.A 【解析】一。\nB\n57.B\n58.C\n"):
            with self.subTest(text=text):
                self.write_word(text)
                self.assertIsNone(explanations_direct.build_payload(self.batch))

    def test_trailing_multiline_summary_binds_prior_passage(self):
        self.write_word("A\n56.A 【解析】一。\n57.B 【解析】二。\n58.C 【解析】三。\n"
                        "【文章大意】本篇。\n续行。\nB\n")
        item = explanations_direct.build_payload(self.batch)["items"][0]
        self.assertEqual(item["articleSummary"], "本篇。\n续行。")

    def test_pdf_summary_fills_same_unit_despite_reordered_units(self):
        self.set_units([("second", [58, 59]), ("first", [56, 57])])
        self.write_word("A\n【文章大意】Word第一篇。\n56.A 【解析】Word56。\n57.B 【解析】Word57。\n"
                        "B\n58.A 【解析】Word58。\n59.B\n")
        (self.batch / "01_source/materials/attachment_001.txt").write_text(
            "A\n【文章大意】PDF第一篇不能覆盖。\n56.A 【解析】PDF56不能覆盖。\n57.B 【解析】PDF57。\n"
            "B\n【文章大意】PDF第二篇。\n58.A 【解析】PDF58不能覆盖。\n59.B 【解析】PDF59。\n",
            encoding="utf-8")
        items = explanations_direct.build_payload(self.batch)["items"]
        self.assertEqual([i["articleSummary"] for i in items], ["PDF第二篇。", "Word第一篇。"])
        self.assertEqual(items[0]["subquestions"][0]["explanation"], "Word58。")
        self.assertEqual(items[0]["subquestions"][1]["explanation"], "PDF59。")
        self.assertFalse(any("PDF" in e for e in items[1]["evidence"]))

    def test_conflicting_summaries_decline_fast_path(self):
        self.write_word("A\n【文章大意】一。\n56.A 【解析】甲。\n57.B 【解析】乙。\n58.C 【解析】丙。\n"
                        "A\n【文章大意】二。\n56.A 【解析】甲。\n57.B 【解析】乙。\n58.C 【解析】丙。\n")
        self.assertIsNone(explanations_direct.build_payload(self.batch))

    def test_canonical_appendix_candidate_passes_strict_consumer(self):
        from workflow import upload_content
        appendix = ("Ⅷ.阅读理解\nA\n[IMG a.jpeg]文章大意[IMG b.jpeg]本篇大意。\n"
                    "56.A [IMG a.jpeg]解析[IMG b.jpeg]A项意为甲。\n解释续行。\n"
                    "57.B [解析]第二题。\n58.C 【解析】第三题。\n")
        (self.batch / "01_source/附录.txt").write_text(appendix, encoding="utf-8")
        (self.batch / "01_source/正文.txt").write_text(
            "Ⅷ.阅读理解\nA\n56. Original question?\n57. Next question?\n58. Last question?\n",
            encoding="utf-8")
        # A stale combined extraction is only a projection, not a competitor.
        self.write_word("56.A 【解析】旧副本。\n")
        payload = explanations_direct.build_payload(self.batch)
        report = upload_content.inspect_content(self.batch, payload, strict=True)
        self.assertEqual(report["errors"], [])
        self.assertEqual(payload["items"][0]["subquestions"][0]["explanation"], "A项意为甲。\n解释续行。")
        self.assertTrue(any("附录.txt:4" in e for e in payload["items"][0]["evidence"]))

    def test_canonical_conflicting_stray_explanations_decline(self):
        (self.batch / "01_source/附录.txt").write_text(
            "56.A\n【解析】原解析。\n【解析】另一原解析。\n", encoding="utf-8")
        self.assertIsNone(explanations_direct.build_payload(self.batch))


if __name__ == "__main__":
    unittest.main()
