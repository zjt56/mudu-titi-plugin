# -*- coding: utf-8 -*-
"""题型版面体检（audit_type_layout）回归。

覆盖用户强调的「一错就整屏变形」的点：
* E20 选项条数与选项字母连续（A→… 不跳号/不重复）；
* C11 完形单元级 options 留空、选项挂子题；
* C12 选词/短文/教材语篇填空子题不带选项；
* D7/SHELL_SUB 材料已带齐题号时，子题 stem 只能是空壳，不重复整句或已含题号。

运行：cd app && python3 -m unittest workflow.test_audit_type_layout
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "06-upload" / "audit_type_layout.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("audit_type_layout_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tl = _load_module()


def _run(batch):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "batch.json"
        path.write_text(json.dumps(batch, ensure_ascii=False), encoding="utf-8")
        return tl.main(["--batch", str(path)])


class TestCompletingSentenceAndCloze(unittest.TestCase):
    def test_cloze_unit_options_must_be_empty_c11(self):
        batch = {"items": [{
            "unit_id": "U3", "question_type": "完形填空", "unit_kind": "group",
            "stem": "读短文", "material": ".. __1__ .. __2__",
            "options": ["A. stray"],
            "subquestions": [
                {"number": 1, "stem": "1.", "options": ["A. x", "B. y", "C. z", "D. w"], "answer": "A"},
                {"number": 2, "stem": "2.", "options": ["A. x", "B. y", "C. z", "D. w"], "answer": "B"},
            ],
        }]}
        code = _run(batch)
        self.assertNotEqual(code, 0)
        self.assertEqual(_run(batch), 1)

    def test_cloze_option_letters_must_be_contiguous(self):
        batch = {"items": [{
            "unit_id": "U3", "question_type": "完形填空", "unit_kind": "group",
            "stem": "读短文", "material": ".. __1__ ..",
            "options": [],
            "subquestions": [
                {"number": 1, "stem": "1.", "options": ["A. x", "B. y", "D. z", "D. w"], "answer": "A"},
            ],
        }]}
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            _run(batch)
        self.assertIn("[E20]", buf.getvalue())
        self.assertIn("连续", buf.getvalue())

    def test_fill_in_sub_should_not_carry_options_c12(self):
        batch = {"items": [{
            "unit_id": "U4", "question_type": "选词填空", "unit_kind": "group",
            "stem": "方框选词", "material": "1. __1__\n2. __2__",
            "subquestions": [
                {"number": 1, "stem": "______", "answer": "a", "options": ["A. x"]},
                {"number": 2, "stem": "______", "answer": "b"},
            ],
        }]}
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            _run(batch)
        self.assertIn("[C12]", buf.getvalue())


class TestShellSubquestions(unittest.TestCase):
    def test_completion_dialogue_duplicate_sentence_flagged(self):
        batch = {"items": [{
            "unit_id": "U2", "question_type": "补全对话", "unit_kind": "group",
            "stem": "指令", "material": "A: Hi. __1__\nB: __2__",
            "options": ["A. a", "B. b", "C. c", "D. d", "E. e", "F. f", "G. g"],
            "subquestions": [
                {"number": 1, "stem": "______", "answer": "A"},
                {"number": 2, "stem": "Hi, how are you today my friend", "answer": "B"},
            ],
        }]}
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = _run(batch)
        self.assertNotEqual(code, 0)
        self.assertIn("[D7]", buf.getvalue())
        self.assertIn("空壳", buf.getvalue())

    def test_shell_stems_pass(self):
        batch = {"items": [{
            "unit_id": "U1", "question_type": "补全对话", "unit_kind": "group",
            "stem": "指令", "material": "A: Hello. __1__\nB: Fine. __2__",
            "options": ["A. a", "B. b", "C. c", "D. d", "E. e", "F. f", "G. g"],
            "subquestions": [
                {"number": 1, "stem": "______", "answer": "A"},
                {"number": 2, "stem": "______", "answer": "B"},
            ],
        }]}
        self.assertEqual(_run(batch), 0)

    def test_material_without_full_numbers_does_not_trip_shell_rule(self):
        # 材料没带齐题号时，不按「材料已编号」判，子题写整句不报 D7
        batch = {"items": [{
            "unit_id": "U7", "question_type": "短文填空", "unit_kind": "group",
            "stem": "短文", "material": "Some text without any number __1__",
            "subquestions": [
                {"number": 2, "stem": "This question has its own full sentence here", "answer": "a"},
            ],
        }]}
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            _run(batch)
        self.assertNotIn("[D7]", buf.getvalue())


class TestCleanBatch(unittest.TestCase):
    def test_clean_batch_passes(self):
        batch = {"items": [
            {"unit_id": "U1", "question_type": "补全对话", "unit_kind": "group", "stem": "指令",
             "material": "A: Hello. __1__\nB: Fine. __2__",
             "options": ["A. a", "B. b", "C. c", "D. d", "E. e", "F. f", "G. g"],
             "subquestions": [{"number": 1, "stem": "______", "answer": "A"},
                              {"number": 2, "stem": "______", "answer": "B"}]},
            {"unit_id": "U3", "question_type": "完形填空", "unit_kind": "group", "stem": "读短文",
             "material": ".. __1__ .. __2__", "options": [],
             "subquestions": [
                 {"number": 1, "stem": "1.", "options": ["A. x", "B. y", "C. z", "D. w"], "answer": "A"},
                 {"number": 2, "stem": "2.", "options": ["A. x", "B. y", "C. z", "D. w"], "answer": "B"}]},
            {"unit_id": "U5", "question_type": "单项填空", "unit_kind": "standalone",
             "stem": "He ___ here.", "options": ["A. is", "B. are", "C. be", "D. am"], "answer": "A"},
        ]}
        self.assertEqual(_run(batch), 0)


class TestReadingShortAnswerExtension(unittest.TestCase):
    def test_reading_extension_short_answer_exempt_from_e20(self):
        # 阅读理解题组末尾附 `☆拓展设问`，用文字回答（无 A–D 选项），不应被 E20 判错。
        batch = {"items": [{
            "unit_id": "U7", "question_type": "阅读理解", "unit_kind": "group",
            "stem": "读短文", "material": "Passage text.",
            "options": [],
            "subquestions": [
                {"number": 1, "stem": "What does it mean?", "options": ["A. x", "B. y", "C. z", "D. w"], "answer": "A"},
                {"number": 2, "stem": "What does Wu find about the fruits?（不超过15个词）",
                 "options": [], "answer": "Many of them are very small."},
            ]}]}
        self.assertEqual(_run(batch), 0)

    def test_reading_mc_sub_missing_options_still_flagged(self):
        # 没有简答/词数提示的阅读理解子题缺选项仍应报 E20，不当简答豁免。
        batch = {"items": [{
            "unit_id": "U8", "question_type": "阅读理解", "unit_kind": "group",
            "stem": "读短文", "material": "Passage text.", "options": [],
            "subquestions": [
                {"number": 1, "stem": "What is the main idea?", "options": [], "answer": "A"}]}]}
        self.assertNotEqual(_run(batch), 0)


if __name__ == "__main__":
    unittest.main()
