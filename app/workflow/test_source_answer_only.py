# -*- coding: utf-8 -*-
import unittest
import json
import tempfile
from pathlib import Path

from workflow import structure_gate

from workflow.source_answer_only import detect_source_answer_only


class SourceAnswerOnlyTests(unittest.TestCase):
    def test_answer_booklet_across_pages(self):
        text = """[PAGE 1]
2025年中考英语复习资料
答案及解析
1.C 2.B 3.A 4.D 5.B 6.C
7.A 8.D 9.B 10.C
11.【解析】根据句意选择。
书面表达参考范文
Dear friends, thank you for your letter.
[PAGE 2]
听力录音稿
1. W: What do you want to do?
评分标准
"""
        result = detect_source_answer_only(text)
        self.assertEqual(result["classification"], "source_answer_only")
        self.assertEqual(result["evidence"]["answer_heading_lines"], [3])
        self.assertEqual(result["evidence"]["answer_index_count"], 10)
        self.assertNotIn("year", result)

    def test_range_indices(self):
        result = detect_source_answer_only("答案与解析\n1-5 ABCDA\n6-10 BCDAB\n听力录音材料")
        self.assertEqual(result["classification"], "source_answer_only")

    def test_paper_with_answer_appendix_is_not_answer_only(self):
        text = "1. What is the answer?\nA. Yes  B. No\n参考答案\n1.A 2.B 3.C 4.D 5.A 6.B\n听力录音稿"
        self.assertEqual(detect_source_answer_only(text)["classification"], "undetermined")

    def test_options_without_question_mark_prevent_classification(self):
        text = "1. The book is ___.\nA. red\nB. blue\n参考答案\n1.A 2.B 3.C 4.D 5.A 6.B\n评分标准"
        self.assertEqual(detect_source_answer_only(text)["classification"], "undetermined")

    def test_each_signal_is_required(self):
        for text in ("参考答案\n1.A 2.B 3.C 4.D 5.A 6.B",
                     "参考答案\n1.A 2.B\n听力录音稿",
                     "1.A 2.B 3.C 4.D 5.A 6.B\n听力录音稿",
                     ""):
            with self.subTest(text=text):
                self.assertEqual(detect_source_answer_only(text)["classification"], "undetermined")

    def test_gate_retains_text_only_answer_booklet_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            source = batch / '01_source'
            source.mkdir()
            raw = '答案及解析\n1.A 2.B 3.C 4.D 5.A 6.B\n听力录音稿\n'
            (source / '原始正文.txt').write_text(raw, encoding='utf-8')
            (source / '正文.txt').write_text(raw, encoding='utf-8')
            (source / 'structure.json').write_text(json.dumps({'sections': [], 'groups': []}), encoding='utf-8')
            report = structure_gate.run_gate(batch)
            self.assertFalse(report['passed'])
            self.assertIn('SOURCE_ANSWER_ONLY', [b['code'] for b in report['blockers']])
            self.assertEqual(report['source_diagnostic']['classification'], 'source_answer_only')
            self.assertEqual(report['source_diagnostic']['scope'], 'full_extracted_text_only; not visual verification')
            self.assertTrue(report['source_diagnostic']['source_sha256'])

    def test_scattered_indices_are_not_dense(self):
        text = "参考答案\n1.A\n" + "内容\n" * 10 + "2.B 3.C 4.D 5.A 6.B\n评分标准"
        self.assertEqual(detect_source_answer_only(text)["classification"], "undetermined")


if __name__ == "__main__":
    unittest.main()
