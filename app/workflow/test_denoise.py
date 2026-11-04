# -*- coding: utf-8 -*-
"""正文去噪与元信息抽取的单元测试。"""
import unittest

from workflow import denoise


class DenoiseTest(unittest.TestCase):
    def test_meta_extracted_and_title_removed(self):
        text = "\n".join([
            "2026年安徽省初中学业水平考试",
            "英语试题",
            "满分120分，考试时间90分钟",
            "本试卷共8页，答题前请将姓名填写在答题卡上",
            "( )1. A. apple  B. banana",
        ])
        cleaned, meta, removed = denoise.clean_body(text)
        self.assertEqual(meta.get("year"), 2026)
        self.assertIn("安徽", meta.get("region", ""))
        self.assertEqual(meta.get("total_score"), 120)
        self.assertEqual(meta.get("duration_minutes"), 90)
        # 卷名/说明行被剔除，不再出现在正文
        self.assertNotIn("初中学业水平考试", cleaned)
        self.assertNotIn("满分120分", cleaned)
        self.assertNotIn("本试卷共8页", cleaned)
        # 题目行必须保留
        self.assertIn("A. apple", cleaned)
        self.assertTrue(any(r["kind"] == "meta" for r in removed))

    def test_noise_lines_dropped_and_question_kept(self):
        text = "\n".join([
            "扫码练听力",
            "-------",
            "-101-",
            "( )2. What is the man doing?",
        ])
        cleaned, _meta, removed = denoise.clean_body(text)
        self.assertNotIn("扫码练听力", cleaned)
        self.assertNotIn("-------", cleaned)
        self.assertNotIn("-101-", cleaned)
        self.assertIn("What is the man doing", cleaned)
        self.assertTrue(all(r["kind"] == "noise" for r in removed))

    def test_answer_references_in_stems_continuations_and_options_are_kept(self):
        lines = [
            "1. 阅读下面的说明并回答问题。",
            "答案见老师给出的说明：不要选择 B。",
            "答案链接中提到的信息是否正确？",
            "答案册里记录了学生的思考过程。",
            "A. 答案见老师给出的说明",
            "B. 答案链接有误",
            "C. 答案册",
            "答案见答案册后面的解释，选择正确的说法。",
        ]
        text = "\n".join(lines)
        cleaned, _, removed = denoise.clean_body(text)
        self.assertEqual(cleaned, text)
        self.assertEqual(removed, [])

    def test_explicit_answer_book_guidance_is_noise(self):
        lines = ["答案册", "答案见答案册", "答案链接：https://example.com/answers",
                 "答案册链接: https://example.com/book", "扫码练听力", "-------", "-101-"]
        text = "\n".join(lines + ["1. What is the answer?"])
        cleaned, _, removed = denoise.clean_body(text)
        self.assertEqual(cleaned, "1. What is the answer?")
        self.assertEqual([item["text"] for item in removed], lines)
        self.assertTrue(all(item["kind"] == "noise" for item in removed))

    def test_question_line_with_number_is_not_dropped_as_meta(self):
        # 题干里出现年份，但带题号，必须保留
        text = "( )3. In 2020, the city built a new library."
        cleaned, _meta, _removed = denoise.clean_body(text)
        self.assertIn("new library", cleaned)

    def test_markdown_title_classified_before_question_text(self):
        meta = denoise.extract_meta([
            "2022**年安徽省初中学业水平考试**", "英语试题",
            "Ⅰ.单项填空", "1.2026年上海市举办活动。",
        ])
        self.assertEqual(meta["year"], 2022)
        self.assertEqual(meta["region"], "安徽省")
        self.assertEqual(meta["source"], "2022年安徽省初中学业水平考试")
        self.assertEqual(meta["source_type"], "中考真题")
        self.assertTrue(meta["evidence"])

    def test_section_scores_are_retained_as_question_boundaries(self):
        text = "2022年安徽省初中学业水平考试\nⅡ.长对话理解(共5小题;每小题1分,满分5分)\n6.What?\nⅩ.书面表达(共1小题;满分20分)"
        cleaned, _, _ = denoise.clean_body(text)
        self.assertIn("Ⅱ.长对话理解", cleaned)
        self.assertIn("Ⅹ.书面表达", cleaned)
        self.assertNotIn("2022年安徽省初中学业水平考试", cleaned)

    def test_mock_paper_is_not_classified_as_real_exam(self):
        meta = denoise.extract_meta(["2025年安徽省中考模拟考试", "Ⅰ.单项填空"])
        self.assertEqual(meta["source_type"], "中考模拟")

    def test_conflicting_header_years_are_not_silently_passed(self):
        meta = denoise.extract_meta(["2022年安徽省中考", "2023年安徽省中考"])
        self.assertTrue(meta["conflicts"])

    def test_numbered_exam_headers_preserve_original_evidence(self):
        for prefix in ("1.", "1、", "1．"):
            with self.subTest(prefix=prefix):
                title = prefix + "2024年安徽省初中学业水平考试"
                cleaned, meta, removed = denoise.clean_body(
                    "[IMG image3.jpeg]\n" + title + "\n英语\nⅠ.听力\n1.What?"
                )
                self.assertEqual(meta["source"], "2024年安徽省初中学业水平考试")
                self.assertEqual(meta["paper_title"], meta["source"])
                self.assertEqual(meta["source_type"], "中考真题")
                self.assertEqual(meta["year"], 2024)
                self.assertEqual(meta["region"], "安徽省")
                self.assertEqual(meta["evidence"], ["正文第2行：" + title])
                self.assertNotIn(title, cleaned)
                self.assertIn("1.What?", cleaned)
                self.assertTrue(any(item["text"] == title for item in removed))

    def test_question_with_exam_words_is_not_a_title(self):
        for line in ("1.2024年安徽省中考是什么时候？", "1、2024年安徽省中考是什么时候？",
                     "1.我参加安徽省中考", "1.2024年我参加安徽省中考",
                     "( )1. What is the 中考?", "1.What is the 2024年安徽省中考?"):
            with self.subTest(line=line):
                cleaned, meta, _ = denoise.clean_body(line + "\n2025年上海市中考")
                self.assertIn(line, cleaned)
                self.assertNotIn("source", meta)
                self.assertNotIn("year", meta)

    def test_book_edition_year_does_not_override_exam_year(self):
        meta = denoise.extract_meta(["2025年精编", "1.2024年安徽省初中学业水平考试"])
        self.assertEqual(meta["year"], 2024)
        self.assertEqual(meta["region"], "安徽省")
        self.assertEqual(meta["evidence"], ["正文第2行：1.2024年安徽省初中学业水平考试"])

    def test_numbered_title_conflicts_keep_both_original_lines(self):
        meta = denoise.extract_meta(["1.2024年安徽省中考", "2、2023年安徽省中考"])
        self.assertEqual(meta["year"], 2024)
        self.assertEqual(meta["evidence"], ["正文第1行：1.2024年安徽省中考", "正文第2行：2、2023年安徽省中考"])
        self.assertIn("2023", meta["conflicts"][0])
        self.assertIn("2024", meta["conflicts"][0])

    def test_web_addresses_in_question_material_and_options_are_preserved(self):
        for line in ("1. Visit www.example.com for more information.",
                     "Visit www.example.cn and enter your school name.",
                     "A. www.example.net", "www.example.com",
                     "1. 学科网提供了什么资料？"):
            with self.subTest(line=line):
                cleaned, _, removed = denoise.clean_body(line)
                self.assertEqual(cleaned, line)
                self.assertEqual(removed, [])
        for watermark in ("学科网", "来源：学科网", "学科网 www.zxxk.com"):
            self.assertTrue(denoise.is_noise_line(watermark))

    def test_clean_text_idempotent_aware(self):
        text = "2026年安徽省初中学业水平考试\n( )1. A. x"
        cleaned1, _, _ = denoise.clean_body(text)
        cleaned2, _, _ = denoise.clean_body(cleaned1)
        self.assertNotIn("初中学业水平考试", cleaned2)
        self.assertIn("A. x", cleaned2)


if __name__ == "__main__":
    unittest.main()
