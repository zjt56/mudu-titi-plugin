"""Regression: ``structure.json`` carries raw extraction line coordinates while
every consumer reads the denoised body. ``denoise.project_structure`` must map
those coordinates through the exact deletion map, so no consumer may index past
the cleaned body or slide an anchor onto a different question.

Source of the incident: 七上人教版课时A计划 Starter Unit 2 Keep Tidy! 课时二
Pronunciation, where a single ``___`` separator line was removed by denoising
and ``structure_gate`` then raised ``IndexError: list index out of range``.
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import denoise, question_content, structure_gate

RAW = "\n".join([
    "Unit 3",
    "第四课时 Project",
    "Ⅰ.单词拼写",
    "___",                 # line 4: separator noise removed by denoising
    "1. Apple a____.",
    "2. Banana b____.",
    "Ⅱ.完成句子",
    "___",                 # line 8: separator noise removed by denoising
    "1. He ____ a student.",
    "2. She ____ happy.",
])
CLEAN, _, REMOVED = denoise.clean_body(RAW)
CLEAN_LINES = CLEAN.splitlines()

SECTIONS = [
    {"title": "Ⅰ.单词拼写", "type": "单词拼写", "questions": [1, 2],
     "source_line": 3, "source_end_line": 6},
    {"title": "Ⅱ.完成句子", "type": "完成句子", "questions": [1, 2],
     "source_line": 7, "source_end_line": 10},
]


class ProjectStructureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_removals_are_real_precondition(self):
        self.assertEqual([row["line_no"] for row in REMOVED], [4, 8])
        self.assertEqual(len(CLEAN_LINES), len(RAW.splitlines()) - 2)

    def test_sections_are_projected_onto_the_clean_body(self):
        projected = denoise.project_structure({"sections": SECTIONS}, RAW, CLEAN)
        self.assertEqual([(s["source_line"], s["source_end_line"]) for s in projected["sections"]],
                         [(3, 5), (6, 8)])
        for section in projected["sections"]:
            self.assertLessEqual(section["source_end_line"], len(CLEAN_LINES))
            # The heading really is on the projected line in the cleaned body.
            self.assertEqual(CLEAN_LINES[section["source_line"] - 1], section["title"])
        self.assertEqual(projected["coordinate_projection"]["cleaned_sha256"],
                         hashlib.sha256(CLEAN.encode()).hexdigest())

    def test_groups_and_nested_keys_are_projected_too(self):
        structure = {"sections": SECTIONS,
                     "groups": [{"start_line": 1, "end_line": 10, "topic": "Unit 3",
                                 "heading_line": 3, "children": [{"line": 9}]}]}
        projected = denoise.project_structure(structure, RAW, CLEAN)
        group = projected["groups"][0]
        self.assertEqual((group["start_line"], group["end_line"], group["heading_line"]), (1, 8, 3))
        self.assertEqual(group["children"][0]["line"], 7)
        # Non-coordinate integers stay untouched.
        self.assertEqual(projected["groups"][0]["topic"], "Unit 3")

    def test_anchor_on_a_removed_line_is_refused_instead_of_slid(self):
        broken = [{"title": "Ⅰ.单词拼写", "type": "单词拼写", "questions": [1],
                   "source_line": 4, "source_end_line": 6}]
        with self.assertRaisesRegex(ValueError, "SOURCE_COORDINATE_MISMATCH"):
            denoise.project_structure({"sections": broken}, RAW, CLEAN)

    def test_heading_that_no_longer_matches_the_body_is_refused(self):
        broken = [{"title": "Ⅱ.完成句子", "type": "完成句子", "questions": [1],
                   "source_line": 3, "source_end_line": 6}]
        with self.assertRaisesRegex(ValueError, "SOURCE_COORDINATE_MISMATCH"):
            denoise.project_structure({"sections": broken}, RAW, CLEAN)

    def test_cleaned_text_must_match_the_denoising_projection(self):
        with self.assertRaisesRegex(ValueError, "SOURCE_COORDINATE_MISMATCH"):
            denoise.project_structure({"sections": SECTIONS}, RAW, "another body")

    def test_projection_is_idempotent_and_bound_to_its_own_body(self):
        once = denoise.project_structure({"sections": SECTIONS}, RAW, CLEAN)
        twice = denoise.project_structure(once, RAW, CLEAN)
        self.assertEqual(twice["sections"], once["sections"])
        self.assertEqual(twice["coordinate_projection"], once["coordinate_projection"])
        other = RAW.replace("2. She ____ happy.", "2. She ____ glad.")
        with self.assertRaisesRegex(ValueError, "SOURCE_COORDINATE_MISMATCH"):
            denoise.project_structure(once, other)

    def test_no_removal_keeps_the_structure_untouched(self):
        body = "\n".join(["Ⅰ.单词拼写", "1. Apple a____."])
        structure = {"sections": [{"title": "Ⅰ.单词拼写", "questions": [1],
                                   "source_line": 1, "source_end_line": 2}]}
        self.assertEqual(denoise.project_structure(structure, body), structure)


class ConsumerChainTest(unittest.TestCase):
    """The gate, the scoped builder and the consumer rebuild share one projection."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        self.out = Path(self.tmp.name) / "out"
        source = self.batch / "01_source"
        source.mkdir(parents=True)
        (source / "正文.txt").write_text(RAW, encoding="utf-8")
        (source / "structure.json").write_text(
            json.dumps({"sections": SECTIONS, "groups": []}, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        (self.batch / "01_source" / "正文.txt").unlink(missing_ok=True)

    def test_scoped_windows_stay_inside_the_clean_body(self):
        structure = denoise.project_structure({"sections": SECTIONS, "groups": []}, RAW, CLEAN)
        windows = structure_gate._scope_windows(structure["sections"], CLEAN_LINES)
        self.assertEqual([(w["source_line"], w["source_end_line"]) for w in windows], [(3, 5), (6, 8)])
        units = structure_gate._build_units(structure["sections"], [], CLEAN_LINES)
        self.assertGreaterEqual(len(units), 2)
        for unit in units:
            span = unit["source_span"]
            self.assertLessEqual(span["end_line"], len(CLEAN_LINES))
            self.assertGreaterEqual(span["start_line"], 1)
            self.assertLess(span["start_line"], span["end_line"] + 1)
            self.assertTrue(unit.get("stem") or unit.get("material"))
        # Every printed question in the cleaned body is owned exactly once.
        owned = [n for unit in units for n in unit.get("numbers") or []]
        self.assertEqual(sorted(owned), [1, 1, 2, 2])

    def test_consumer_rebuild_reads_raw_structure_against_clean_body(self):
        structure = json.loads((self.batch / "01_source/structure.json").read_text(encoding="utf-8"))
        units = question_content._rebuild_source_units(self.batch, structure, CLEAN)
        self.assertGreaterEqual(len(units), 2)
        self.assertEqual(sorted(n for unit in units for n in unit.get("numbers") or []), [1, 1, 2, 2])
        for unit in units:
            self.assertLessEqual(unit["source_span"]["end_line"], len(CLEAN_LINES))

    def test_gate_no_longer_indexes_past_the_clean_body(self):
        with patch("workflow.structure_ai.auto_classify", return_value={"ran": False}):
            report = structure_gate.run_gate(self.batch, output_dir=self.out)
        self.assertIsInstance(report, dict)
        # The old bug surfaced as IndexError before any report was produced; the
        # gate must now return a real report (its content verdict is not asserted
        # here because this synthetic body intentionally lacks source metadata).
        self.assertIn("checks", report)
        self.assertIn("blockers", report)
        self.assertTrue((self.out / "01_source/structure.confirmed.json").is_file()
                        or report.get("passed") is False)


if __name__ == "__main__":
    unittest.main()
