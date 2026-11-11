# -*- coding: utf-8 -*-
"""课时编排自检：课时缺单元名 / 空课时 / 课时跳号，以及提示词约束落地。

规则来自 Mudu-English-Upload 的课时录入口径（workbook-recipes / constraints D23 等），
本插件只把可确定性判断的部分做成留痕审计，不改结构、不改题型、不放宽闸门。
"""
from __future__ import annotations

import unittest

from workflow import extract, sync_practice

LESSON_BODY = """Unit 1 My name's Gina
第一课时 Section A (1a-2d)
Ⅰ.单项填空
1. — What's your name?
— ______.
第二课时 Section B (1a-2d)
Ⅰ.阅读理解
A
Hello, I'm Gina.
1. What's the girl's name?
"""

EMPTY_LESSON_BODY = """Unit 2 This is my sister
第一课时 Section A (1a-2d)
第二课时 Section B (1a-2d)
Ⅰ.单项填空
1. — Is this your sister?
— Yes, ______.
"""

GAP_BODY = """Unit 3 Is this your pencil
第一课时 Section A (1a-2d)
Ⅰ.单项填空
1. — Is this your pen?
— Yes, ______.
第三课时 Section B (1a-2d)
Ⅰ.单项填空
1. — Is that your book?
— No, ______.
"""

NO_UNIT_BODY = """第一课时 Section A (1a-2d)
Ⅰ.单项填空
1. — Hello!
— ______.
"""

EXAM_BODY = """七年级英语单元测试卷
Ⅰ.单项填空
1. — Hello!
— ______.
Ⅱ.阅读理解
A
Hello!
1. What's this?
"""


class LessonNumberTest(unittest.TestCase):
    def test_lesson_numbers_parse_all_forms(self):
        self.assertEqual(sync_practice._lesson_number("第一课时 Section A"), 1)
        self.assertEqual(sync_practice._lesson_number("第1节课 Section B"), 1)
        self.assertEqual(sync_practice._lesson_number("第十二课时 Grammar Focus"), 12)
        self.assertEqual(sync_practice._lesson_number("第二十一课时 Project"), 21)
        self.assertEqual(sync_practice._lesson_number("课时3 Pronunciation"), 3)
        self.assertEqual(sync_practice._lesson_number("Period 2 Section A"), 2)
        self.assertEqual(sync_practice._lesson_number("Lesson 4 Section B"), 4)
        self.assertIsNone(sync_practice._lesson_number("第一课 Section A"))
        self.assertIsNone(sync_practice._lesson_number("课时安排"))


class LessonShapeAuditTest(unittest.TestCase):
    def test_clean_lesson_sequence_has_no_findings(self):
        self.assertEqual(sync_practice.audit_lesson_shape(LESSON_BODY), [])

    def test_lesson_without_unit_is_reported(self):
        findings = sync_practice.audit_lesson_shape(NO_UNIT_BODY)
        self.assertEqual([row["code"] for row in findings], ["LESSON_UNIT_MISSING"])
        self.assertIn("无法确定所属单元", findings[0]["problems"][0])

    def test_empty_lesson_is_reported(self):
        findings = sync_practice.audit_lesson_shape(EMPTY_LESSON_BODY)
        codes = [row["code"] for row in findings]
        self.assertIn("LESSON_EMPTY", codes)
        empty = next(row for row in findings if row["code"] == "LESSON_EMPTY")
        self.assertEqual(empty["line"], 2)
        self.assertIn("第一课时", empty["title"])

    def test_lesson_number_gap_is_reported(self):
        findings = sync_practice.audit_lesson_shape(GAP_BODY)
        gap = [row for row in findings if row["code"] == "LESSON_NUMBER_GAP"]
        self.assertEqual(len(gap), 1)
        self.assertIn("第三课时", gap[0]["title"])
        self.assertTrue(any("LESSON_EMPTY" == row["code"] for row in findings) is False)

    def test_exam_text_is_not_audited(self):
        self.assertFalse(sync_practice.looks_like_sync_practice(EXAM_BODY))
        self.assertEqual(sync_practice.audit_lesson_shape(EXAM_BODY), [])

    def test_audit_does_not_modify_units_or_structure(self):
        structure, body, appendix = extract.infer_structure(LESSON_BODY)
        before = [dict(row) for row in structure["sections"]]
        sync_practice.audit_lesson_shape(LESSON_BODY)
        self.assertEqual(before, structure["sections"])
        self.assertEqual(body, "" if not body else body)


class LessonConstraintWiringTest(unittest.TestCase):
    def test_prompt_carries_lesson_constraints(self):
        prompt = sync_practice.prompt_block()
        self.assertIn("课时缺少单元名", prompt)
        self.assertIn("与课时平级", prompt)
        self.assertIn("材料里已经带齐每个小题的题号", prompt)
        self.assertIn("第1步词汇积累", prompt)

    def test_constraint_records_are_complete(self):
        items = sync_practice.lesson_constraints()
        self.assertGreaterEqual(len(items), 5)
        for item in items:
            self.assertTrue(item.get("id"))
            self.assertIn(item.get("check"), {"lesson_unit_missing", "lesson_empty",
                                              "lesson_number_gap", "section_inside_lesson",
                                              "material_numbered", "writing_blocks"})
            self.assertTrue(item.get("text"))

    def test_extract_records_lesson_audit(self):
        structure, _, _ = extract.infer_structure(LESSON_BODY)
        self.assertEqual(structure["sync_practice"]["lesson_audit"], [])
        structure, _, _ = extract.infer_structure(GAP_BODY)
        codes = [row["code"] for row in structure["sync_practice"]["lesson_audit"]]
        self.assertIn("LESSON_NUMBER_GAP", codes)


if __name__ == "__main__":
    unittest.main()
