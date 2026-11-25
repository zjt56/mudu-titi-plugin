"""原卷提示标记的加粗包裹不得改变标记本体（6326 Unit 8 D27/B9 现场）。

书面表达大题作答指令原文是 ``[2024·**四川南充**]假如你是李明…``；标记必须按
``[2024·四川南充]`` 原样进入题干，任何清洗都只能去掉排版星号，不能改动方括号
里的来源身份。
"""
import unittest

from . import sync_practice


class BoldWrappedMarkTest(unittest.TestCase):
    def test_bold_wrapping_is_normalized_but_body_is_kept(self):
        self.assertEqual(sync_practice.source_marks("[2024·**四川南充**]假如你是李明"),
                         ["[2024·四川南充]"])
        self.assertEqual(sync_practice.source_marks("[2024·四川南充]假如你是李明"),
                         ["[2024·四川南充]"])

    def test_rewritten_or_partial_mark_is_not_the_same_identity(self):
        original = sync_practice.source_marks("[2024·**四川南充**]假如你是李明")[0]
        self.assertFalse(sync_practice._keeps(original, "[2024·四川]假如你是李明"))
        self.assertFalse(sync_practice._keeps(original, "[2025·四川南充]假如你是李明"))
