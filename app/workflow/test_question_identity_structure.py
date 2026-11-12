"""Source-position identity regressions; no real batch data is touched."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from .extract import infer_structure
from .question_identity import question_key
from .structure_ai import _sanitize_classified
from .structure_gate import _build_units, _scoped_source_errors, run_gate


class ScopedStructureTests(unittest.TestCase):
    TEXT = "\n".join([
        "Unit 1", "第一课时 Section A", "基础巩固", "Ⅰ.单项填空",
        "1. Choose the first colour.", "A. red B. blue C. green D. white",
        "Ⅱ.根据汉语意思完成句子,每空一词", "1. Second answer is yellow. ______",
        "提升训练", "Ⅰ.根据首字母及汉语提示补全单词", "1. Third answer is zebra. z______",
    ])

    def build(self, text=None):
        text = text or self.TEXT
        structure, body, _ = infer_structure(text)
        lines = body.splitlines()
        units = _build_units(structure["sections"], structure["groups"], lines)
        return structure, body, lines, units

    def test_three_sections_keep_original_numbers_roman_and_source(self):
        structure, body, lines, units = self.build()
        self.assertEqual([u["numbers"] for u in units], [[1], [1], [1]])
        self.assertEqual([u["source_section"]["title"] for u in units],
                         ["Ⅰ.单项填空", "Ⅱ.根据汉语意思完成句子,每空一词", "Ⅰ.根据首字母及汉语提示补全单词"])
        self.assertEqual([u["source_section"]["ordinal"] for u in units], ["Ⅰ.", "Ⅱ.", "Ⅰ."])
        self.assertEqual(len({question_key(u, 1) for u in units}), 3)
        for unit, word in zip(units, ["red", "yellow", "zebra"]):
            self.assertIn(word, unit["stem"])
        self.assertEqual(_scoped_source_errors(units, structure["sections"], lines), [])
        self.assertEqual(len(_sanitize_classified({"units": units}, body, structure)), 3)

    def test_same_scope_duplicate_rejected(self):
        text = "Unit 1\n第一课时\nⅠ.单项填空\n1. Red?\nA. a B. b C. c D. d\n1. Blue?\nA. e B. f C. g D. h"
        structure, body, lines, units = self.build(text)
        self.assertIn("SOURCE_DUPLICATE_NUMBERS", {e["code"] for e in _scoped_source_errors(units, structure["sections"], lines)})
        self.assertEqual(_sanitize_classified({"units": units}, body, structure), [])

    def test_missing_local_window_not_hidden_by_other_section(self):
        structure, body, lines, units = self.build()
        shortened = units[:1] + units[2:]
        errors = _scoped_source_errors(shortened, structure["sections"], lines)
        self.assertTrue(any(e["code"] == "SOURCE_NUMBERS_UNDECLARED" and e["number"] == 1 for e in errors))
        self.assertEqual(_sanitize_classified({"units": shortened}, body, structure), [])

    def test_two_articles_restart_inside_same_section(self):
        text = "Unit 1\n第一课时\nⅠ.阅读理解\nA\nAlpha lives here.\n1. Alpha?\nA. yes B. no C. red D. blue\nB\nBeta lives there.\n1. Beta?\nA. no B. yes C. green D. white"
        structure, body, lines, units = self.build(text)
        self.assertEqual(len(units), 2)
        self.assertEqual(units[0]["section_id"], units[1]["section_id"])
        self.assertNotEqual(question_key(units[0], 1), question_key(units[1], 1))
        self.assertIn("Alpha lives", units[0]["stem"])
        self.assertNotIn("Beta lives", units[0]["stem"])
        self.assertIn("Beta lives", units[1]["stem"])
        self.assertEqual(_scoped_source_errors(units, structure["sections"], lines), [])
        self.assertEqual(len(_sanitize_classified({"units": units}, body, structure)), 2)
        partial_stem = deepcopy(units)
        partial_stem[0]["stem"] = "1. Alpha?"
        restored = _sanitize_classified({"units": partial_stem}, body, structure)
        self.assertIn("Alpha lives here.", restored[0]["stem"])

    def test_cross_article_group_rejected(self):
        text = "Unit 1\n第一课时\nⅠ.阅读理解\nA\nAlpha lives here.\n1. Alpha?\nB\nBeta lives there.\n2. Beta?"
        structure, body, lines, units = self.build(text)
        combined = deepcopy(units[:1])
        combined[0]["numbers"] = [1, 2]
        combined[0]["source_span"]["end_line"] = len(lines)
        self.assertIn("GROUP_CROSSES_SECTION", {e["code"] for e in _scoped_source_errors(combined, structure["sections"], lines)})

    def test_same_line_duplicate_is_rejected(self):
        text = "Unit 1\n第一课时\nⅠ.判断下列各组字母的大小写是否一致\n1.A a ( )    1.B b ( )"
        structure, _, lines, units = self.build(text)
        self.assertIn("SOURCE_DUPLICATE_NUMBERS", {e["code"] for e in _scoped_source_errors(units, structure["sections"], lines)})

    def test_class_number_inside_stem_is_not_a_second_printed_item(self):
        text = "Unit 1\n第一课时\nⅠ.按要求完成句子,每空一词\n1.Peter is here.\n2.He is there.\n3.Emma is in <u>Class 2</u>.(对画线部分提问)"
        structure, _, lines, units = self.build(text)
        self.assertEqual(_scoped_source_errors(units, structure["sections"], lines), [])
        from .question_identity import printed_anchors, numbered_projection
        self.assertEqual(printed_anchors([lines[-1]]), [(0, 3)])
        self.assertEqual(numbered_projection([lines[-1]]), [lines[-1]])

    def test_two_column_abbreviations_keep_ten_exact_scoped_units(self):
        text = "Unit 1\n第一课时\nⅡ.写出下列缩写词的完全形式\n" + "\n".join(
            f"{i}.I’m<u>　　　 </u>\t{i+1}.it’s<u>　　　 </u>" for i in (1, 3, 5, 7, 9))
        structure, _, lines, units = self.build(text)
        self.assertEqual([u["numbers"] for u in units], [[i] for i in range(1, 11)])
        self.assertEqual(len({u["unit_id"] for u in units}), 10)
        for unit in units:
            span = unit["source_span"]
            self.assertEqual(span["start_line"], span["end_line"])
            self.assertTrue(0 <= span["start_offset"] < span["end_offset"])
            from .question_content import audit_source_unit
            self.assertTrue(audit_source_unit(unit, lines)["passed"])
        self.assertEqual(_scoped_source_errors(units, structure["sections"], lines), [])

    def test_group_cannot_drop_material_before_first_number(self):
        text = "Unit 1\n第一课时\nⅠ.阅读理解\nA\nAlpha is a real passage.\n1. Alpha?\nA. yes B. no C. red D. blue"
        structure, body, lines, units = self.build(text)
        shortened = deepcopy(units)
        shortened[0]["source_span"]["start_line"] = 6
        shortened[0]["stem"] = "\n".join(lines[5:])
        self.assertIn("SOURCE_MATERIAL_OMITTED", {e["code"] for e in _scoped_source_errors(shortened, structure["sections"], lines)})
        self.assertEqual(_sanitize_classified({"units": shortened}, body, structure), [])

    def test_legacy_global_number_behavior_preserved(self):
        text = "2026年期中考试\n一、单项填空\n1. Red?\nA. a B. b C. c D. d\n2. Blue?\nA. a B. b C. c D. d"
        structure, body, lines, units = self.build(text)
        self.assertEqual([u["numbers"] for u in units], [[1], [2]])
        self.assertTrue(all("identity_version" not in u for u in units))
        self.assertEqual(question_key(units[0], 1), "1")
        duplicate = deepcopy(units)
        duplicate[1]["numbers"] = [1]
        self.assertEqual(_sanitize_classified({"units": duplicate}, body, structure), [])

    def test_inline_slots_and_unnumbered_image_keep_source_identity(self):
        text = "\n".join([
            "Unit 1", "第一课时", "Ⅰ.按照字母表顺序,写出空白处所缺字母", "[IMG image2.png]",
            "Ⅱ.判断下列各组字母的大小写是否一致", "1.A a ( )    2.B b ( )",
            "Ⅲ.补全对话", "A: Good morning.", "B:1.<u>　　　　</u>", "A:2.<u>　　　　</u>",
        ])
        structure, body, lines, units = self.build(text)
        self.assertEqual([u["numbers"] for u in units], [[1], [1, 2], [1, 2]])
        self.assertIsNone(units[0]["original_number"])
        self.assertFalse(units[0]["printed_number"])
        self.assertEqual(units[0]["source_span"]["number_binding"], "unnumbered_section")
        self.assertEqual(_scoped_source_errors(units, structure["sections"], lines), [])
        self.assertEqual(len(_sanitize_classified({"units": units}, body, structure)), 3)
        with tempfile.TemporaryDirectory() as root:
            batch = Path(root)
            (batch / "01_source").mkdir()
            (batch / "01_source/正文.txt").write_text(body, encoding="utf-8")
            (batch / "01_source/structure.json").write_text(json.dumps(structure), encoding="utf-8")
            with patch("workflow.structure_ai.auto_classify", return_value={"ran": False}):
                report = run_gate(batch)
            self.assertTrue(report["passed"], report.get("blockers"))

    def test_source_shaped_gate_draft_audit_and_dialogue_deletion(self):
        from .question_content import audit_batch_source
        from .question_draft import build
        text = "\n".join([
            "Unit 1", "第一课时", "Ⅰ.按照字母表顺序,写出空白处所缺字母", "[IMG image2.jpeg]",
            "Ⅱ.判断下列各组字母的大小写是否一致", "1.A a ( )    2.B b ( )",
            "Ⅲ.补全对话", "A&B:Good morning,Ms Gao!", "C:1.<u>　　　　</u> What’s your name?",
            "A:2.<u>　　　　</u>", "C:Nice to meet you.", "A:3.<u>　　　　</u>",
            "C:4.<u>　　　　</u>", "B:My name is Peter.", "C:5.<u>　　　　</u>",
            "B:Hi,Emma.", "A:Hi,Peter.", "A.Yes,I am.", "B.Thank you.", "C.Good morning!",
            "D.My name is Emma.", "E.Nice to meet you too.", "F.So what’s your name?",
            "G.Can you say hi to each other(互相)?",
        ])
        structure, body, _, units = self.build(text)
        with tempfile.TemporaryDirectory() as root:
            batch = Path(root)
            (batch / "01_source/media").mkdir(parents=True)
            (batch / "01_source/media/image2.jpeg").write_bytes(b"image-evidence")
            (batch / "01_source/正文.txt").write_text(body, encoding="utf-8")
            (batch / "01_source/structure.json").write_text(json.dumps(structure), encoding="utf-8")
            with patch("workflow.structure_ai.auto_classify", return_value={"ran": False}):
                report = run_gate(batch)
            self.assertTrue(report["passed"], report.get("blockers"))
            draft = build(batch, units=units)
            self.assertEqual(draft["issues"], [])
            audit = audit_batch_source(batch, draft)
            self.assertTrue(audit["passed"], audit["issues"])
            changed = deepcopy(draft)
            dialogue = next(item for item in changed["items"] if item["numbers"] == [1, 2, 3, 4, 5])
            self.assertIn("What’s your name?", dialogue["material"])
            dialogue["material"] = dialogue["material"].replace("What’s your name?", "")
            self.assertFalse(audit_batch_source(batch, changed)["passed"])

    def test_gate_uses_local_coverage_and_source_audit(self):
        structure, body, _, units = self.build()
        with tempfile.TemporaryDirectory() as root:
            batch = Path(root)
            (batch / "01_source").mkdir()
            (batch / "01_source/正文.txt").write_text(body, encoding="utf-8")
            (batch / "01_source/structure.json").write_text(json.dumps(structure), encoding="utf-8")
            with patch("workflow.structure_ai.auto_classify", return_value={"ran": False}):
                report = run_gate(batch)
            self.assertTrue(report["passed"], report.get("blockers"))
            confirmed = json.loads((batch / "01_source/structure.confirmed.json").read_text(encoding="utf-8"))
            self.assertEqual(len(confirmed["units"]), 3)
            self.assertEqual(len({u["unit_id"] for u in confirmed["units"]}), 3)


if __name__ == "__main__":
    unittest.main()
