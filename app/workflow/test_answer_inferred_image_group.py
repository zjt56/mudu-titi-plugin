"""Tests for deterministic answer-inferred numbering on image-only scoped groups."""
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workbench import server
from workflow import (
    phonetic_structure,
    question_draft,
    structure_gate,
    upload_content,
    upload_numbering,
)


def _png_bytes(width=1200, height=500):
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", width, height)


class AnswerInferredImageGroupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.batch = self.root / "lesson_u2_4"
        self.source = self.batch / "01_source"
        self.media = self.source / "media"
        self.media.mkdir(parents=True)
        self.body = "\n".join([
            "九上 · 人教版 · 课时A计划",
            "**Unit 2　Inspiring People**",
            "**第四课时**　Section B (1a-1f)",
            "**[IMG image9.jpg]****一、教材语篇回顾****[IMG image10.jpg]**",
            "[IMG image28.jpg]",
            "",
            "**【二、A 基础巩固】**",
            "**Ⅰ. 根据句意及汉语提示完成单词**",
            "**1.**In order to save time, near the ________(银行).",
            "**2.**I hope they are healthy ________(永远).",
            "**Ⅱ. 单项填空**",
            "**1.**It is ________ to take a taxi.",
            "A. popular  B. necessary  C. important  D. possible",
        ])
        self.appendix = "\n".join([
            "**参考答案**",
            "**[IMG image85.jpg]****一、教材语篇回顾****[IMG image86.jpg]**",
            "1.was born in　2.researching　3.all/different kinds of　4.radio　5.focused on　6.braved　7.more than",
            "8.lives on",
            "**[IMG image85.jpg]****二、基础巩固****[IMG image86.jpg]**",
            "Ⅰ.1.bank　2.forever",
            "Ⅱ.1.B",
        ])
        self.structure = {
            "sections": [
                {
                    "title": "**[IMG image9.jpg]****一、教材语篇回顾****[IMG image10.jpg]**",
                    "type": "教材语篇填空",
                    "questions": [],
                    "image_only": True,
                    "source_start_line": 4,
                    "source_end_line": 6,
                },
                {
                    "title": "**Ⅰ. 根据句意及汉语提示完成单词**",
                    "type": "单词拼写",
                    "questions": [1, 2],
                    "source_start_line": 8,
                    "source_end_line": 10,
                },
                {
                    "title": "**Ⅱ. 单项填空**",
                    "type": "单项填空",
                    "questions": [1],
                    "source_start_line": 11,
                    "source_end_line": 13,
                },
            ],
            "groups": [],
        }
        for name, size in (("image9.jpg", (40, 40)), ("image10.jpg", (40, 40)), ("image28.jpg", (2106, 893))):
            (self.media / name).write_bytes(_png_bytes(*size))
        self._write("01_source/正文.txt", self.body)
        self._write("01_source/附录.txt", self.appendix)
        self._write("01_source/structure.json", self.structure)

    def _write(self, rel, value):
        path = self.batch / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def test_image_group_infers_numbers_splits_answers_and_projects_range(self):
        report = structure_gate.run_gate(self.batch)
        self.assertTrue(report["passed"], report)
        confirmed = json.loads((self.source / "structure.confirmed.json").read_text(encoding="utf-8-sig"))
        unit = confirmed["units"][0]
        self.assertEqual(unit["unit_kind"], "group")
        self.assertEqual(unit["numbers"], [1, 2, 3, 4, 5, 6, 7, 8])
        span = unit["source_span"]
        self.assertEqual(span["number_binding"], "answer_inferred_image_group")
        self.assertEqual(span["printed_numbers"], [])
        self.assertEqual(span["inferred_numbers"], [1, 2, 3, 4, 5, 6, 7, 8])
        self.assertEqual(span["inferred_evidence"], ["01_source/附录.txt:3", "01_source/附录.txt:4"])
        self.assertEqual(set(unit["question_ids"]), {str(i) for i in range(1, 9)})

        expected_answers = {
            1: "was born in",
            2: "researching",
            3: "all/different kinds of",
            4: "radio",
            5: "focused on",
            6: "braved",
            7: "more than",
            8: "lives on",
        }
        for n, expected in expected_answers.items():
            resolved = upload_content.resolve_scoped_answer(self.batch, unit, n)
            self.assertIsNotNone(resolved, f"missing answer for q{n}")
            self.assertEqual(resolved["answer"], expected)
            upload_content.validate_scoped_answer(self.batch, unit, n, expected, resolved["evidence"])

        draft = question_draft.build(self.batch)
        self.assertEqual(draft["issues"], [])
        item = draft["items"][0]
        self.assertEqual([c["number"] for c in item["subquestions"]], list(range(1, 9)))
        self.assertEqual([a["name"] for a in item["attachments"]], ["image28.jpg"])

        assembled = dict(item, question_type="教材语篇填空")
        for child in assembled["subquestions"]:
            child["answer"] = expected_answers[child["number"]]
        self._write("06_batch/batch.json", {"items": [assembled]})
        self._write("06_batch/batch_meta.json", {"items": [assembled]})

        verified = phonetic_structure.verified_groups(self.batch / "06_batch/batch.json")
        self.assertIn(unit["unit_id"], verified)
        self.assertEqual(phonetic_structure.shape(assembled, verified), (True, True))

        projected = upload_numbering.project_batch({"items": [assembled]}, batch_dir=self.batch)
        self.assertEqual(projected["errors"], [])
        self.assertEqual(projected["items"][0]["numberStart"], 1)
        self.assertEqual(projected["items"][0]["numberEnd"], 8)
        self.assertEqual(projected["evidence"]["units"][0]["state"], "answer-inferred")

        with patch.object(server, "WORKFLOWS", self.root):
            detail = server.detail(self.batch.name, unit["unit_id"])
        self.assertEqual(detail["label"], "1–8")
        self.assertEqual(detail["hidden_media"], [])
        self.assertTrue(any(b.get("type") == "image" and b.get("name") == "image28.jpg" for b in detail["preview_blocks"]))
        self.assertEqual(len(detail["subquestions"]), 8)
        self.assertEqual([s["number"] for s in detail["subquestions"]], list(range(1, 9)))
        self.assertNotIn("display_number", detail["subquestions"][0])

    def test_unnumbered_or_noncontiguous_appendix_falls_back_to_unnumbered_section(self):
        for case, key_line in (
            ("noncontiguous", "1.was born in　3.radio　4.braved"),
            ("unnumbered_prose", "was born in; researching; radio"),
        ):
            with self.subTest(case=case):
                appendix = "\n".join([
                    "**参考答案**",
                    "**一、教材语篇回顾**",
                    key_line,
                    "**二、基础巩固**",
                    "Ⅰ.1.bank　2.forever",
                    "Ⅱ.1.B",
                ])
                self._write("01_source/附录.txt", appendix)
                (self.source / "structure.confirmed.json").unlink(missing_ok=True)
                report = structure_gate.run_gate(self.batch)
                self.assertTrue(report["passed"], report)
                confirmed = json.loads((self.source / "structure.confirmed.json").read_text(encoding="utf-8-sig"))
                unit = confirmed["units"][0]
                self.assertEqual(unit["numbers"], [1])
                self.assertEqual(unit["source_span"]["number_binding"], "unnumbered_section")


if __name__ == "__main__":
    unittest.main()
