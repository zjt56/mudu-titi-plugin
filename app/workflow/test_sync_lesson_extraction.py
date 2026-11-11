# -*- coding: utf-8 -*-
"""同步课时提取口径回归：课时/版块标题变体、提示标记保真、目录主类齐备。

口径来自 Mudu-English-Upload（课时拆分件、Reading Plus/Project 版块、原卷提示标记），
只做识别与留痕强化，不改历史批次、不调用真实模型。
"""
import unittest

from workflow import sync_practice as sp


class LessonHeadingVariantTest(unittest.TestCase):
    def test_lesson_heading_variants_are_recognized(self):
        variants = [
            '第一课时　Section A (1a-2d)',
            '第1课时 Section A',
            '第一课时Section A',
            '第1节课 Section B',
            '课时1 Pronunciation',
            'Period 1 Section A',
            'Lesson 2 Grammar Focus',
        ]
        for line in variants:
            with self.subTest(line=line):
                self.assertTrue(sp.looks_like_sync_practice(line + '\nⅠ.单项填空\n(　)1.Who?'))

    def test_non_lesson_lines_are_not_lesson_headings(self):
        for line in ('第一课 Section A', '课时安排', '本学期课时', '第二课堂活动'):
            with self.subTest(line=line):
                self.assertFalse(sp.looks_like_sync_practice(line + '\nⅠ.单项填空\n(　)1.Who?'))

    def test_exam_header_still_wins_over_lesson_word(self):
        self.assertFalse(sp.looks_like_sync_practice('2026年期末测试卷\n第一课时复习\nⅠ.阅读理解'))


class SectionTitleTest(unittest.TestCase):
    def test_section_titles_are_bounded(self):
        for line in ('综合阅读提升', '单元话题写作', 'Pronunciation', 'Reading Plus', 'Project', 'Reflecting'):
            with self.subTest(line=line):
                self.assertTrue(sp.is_section_title(line))
        for line in ('阅读理解', '单项填空', 'Reading Plus 练习'):
            with self.subTest(line=line):
                self.assertFalse(sp.is_section_title(line))

    def test_section_title_ends_previous_window(self):
        text = ('第一课时 Section A\n'
                'Ⅰ.阅读理解\n'
                'Story.\n'
                '(　)1.Who?\n'
                'A.a\tB.b\n'
                'Reading Plus\n'
                'Ⅱ.任务型阅读\n'
                '1.Answer.\n')
        rows = sp.heading_candidates(text)
        self.assertEqual([row['start_line'] for row in rows], [2, 7])
        self.assertEqual(rows[0]['end_line'], 5)
        self.assertEqual(rows[1]['context'], 'Reading Plus')


class SourceMarkTest(unittest.TestCase):
    def test_source_marks_pick_real_hints_only(self):
        text = ('Ⅰ.完形填空[2025·六安月考]\n'
                'A story about a boy. [新考法·跨学科]\n'
                '[情境题] 改编自 [2023·安徽] 真题\n'
                '[IMG word/media/image1.png]\n'
                '[TABLE 1]\n'
                '【解析】见下\n'
                '[答案] B\n')
        marks = sp.source_marks(text)
        self.assertIn('[2025·六安月考]', marks)
        self.assertIn('[新考法·跨学科]', marks)
        self.assertIn('[情境题]', marks)
        self.assertIn('[2023·安徽]', marks)
        self.assertEqual(len(marks), 4)

    def test_missing_marks_are_reported_without_rewriting(self):
        text = ('第一课时 Section A\n'
                'Ⅰ.完形填空[2025·六安月考]\n'
                'A story about a boy. [新考法·跨学科]\n'
                '1.He ___ .\n'
                'A.a\tB.b\n')
        kept = {'unit_kind': 'group', 'type': '完形填空', 'numbers': [1],
                'stem': '完形填空[2025·六安月考] A story about a boy. [新考法·跨学科]',
                'options': ['A.a', 'B.b'],
                'source_span': {'start_line': 2, 'end_line': 5}}
        self.assertEqual(sp.audit_source_marks([kept], text), [])

        dropped = dict(kept, stem='完形填空 A story about a boy.')
        found = sp.audit_source_marks([dropped], text)
        self.assertEqual(len(found), 1)
        self.assertEqual(sorted(found[0]['marks']), ['[2025·六安月考]', '[新考法·跨学科]'])
        self.assertTrue(found[0]['problems'][0].startswith('原卷提示标记未进入题干/材料'))
        # 只留痕：单元本身不被改写
        self.assertEqual(dropped['stem'], '完形填空 A story about a boy.')

    def test_exam_has_no_mark_audit(self):
        self.assertEqual(sp.audit_source_marks(
            [{'stem': 'x', 'source_span': {'start_line': 2, 'end_line': 2}}],
            '2026期末测试卷\n[2025·六安月考]1.Who?'), [])


class CatalogAndPromptTest(unittest.TestCase):
    def test_catalog_type_kind_is_complete(self):
        kinds = sp.type_kind()
        self.assertEqual(len(kinds), 27)
        self.assertEqual(kinds.get('听力其他'), 'group')
        self.assertEqual(sp.validate(), [])

    def test_prompt_requires_source_marks(self):
        block = sp.prompt_block()
        self.assertIn('方括号提示标记', block)
        self.assertIn('[新考法·跨学科]', block)


if __name__ == '__main__':
    unittest.main()
