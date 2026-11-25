"""Standalone 书面表达: the marked 大题作答指令 heading must reach the stem.

The confirmed span may keep blank separator lines before the first content line,
so the window rebuilt from the exact stem starts a line or two later. That is not
content loss: the section heading is still this unit's directive and must be
prepended, otherwise the original source mark only survives in
``source_section`` metadata and audit_source_marks (D27/B9) rejects the batch.
"""
import unittest

from . import question_draft
from .question_identity import VERSION as IDENTITY_VERSION

HEADING = ("[2024·四川南充]假如你是李明,最近你的英国笔友Eric发来求助邮件。"
           "请你根据下面邮件的内容给他写一封回信,内容包括:")
STEM = "1.表示理解;\n2.给出建议;\n3.表达希望。"
BODY = ["1.表示理解;", "2.给出建议;", "3.表达希望。", "To:Eric"]


def _unit(stem=STEM):
    return {
        "unit_id": "sec-demo-u1",
        "unit_kind": "standalone",
        "identity_version": IDENTITY_VERSION,
        "numbers": [1],
        "stem": stem,
        "section_id": "sec-demo",
        "question_ids": {"1": "sec-demo-l3-q1"},
        "source_span": {"start_line": 3, "end_line": 6,
                        "number_binding": "single_writing_section", "printed_numbers": []},
        "source_section": {"title": HEADING, "start_line": 1, "end_line": 6},
    }


class StandaloneMarkedHeadingTest(unittest.TestCase):
    def recover(self, between):
        unit = _unit()
        source = "\n".join([HEADING, *between, *BODY])
        proof = {"start_line": 3, "end_line": 6, "line_basis": "01_source/正文.txt"}
        return question_draft._standalone_marked_heading(unit, proof, source, [], [unit])

    def test_blank_separator_lines_do_not_drop_marked_directive(self):
        self.assertEqual(self.recover([""]), HEADING)
        self.assertEqual(self.recover(["", ""]), HEADING)

    def test_content_between_heading_and_window_still_blocks(self):
        self.assertEqual(self.recover(["请务必将答案写在答题卡上。"]), "")

    def test_bold_marked_heading_matches_plain_section_title_in_draft_and_preview(self):
        from . import preview
        bold_heading = ("[2024·**四川南充**]假如你是李明,最近你的英国笔友Eric发来求助邮件。"
                        "请你根据下面邮件的内容给他写一封回信,内容包括:")
        unit = _unit()
        source = "\n".join([bold_heading, "", *BODY])
        proof = {"start_line": 3, "end_line": 6, "line_basis": "01_source/正文.txt"}
        self.assertEqual(
            question_draft._standalone_marked_heading(unit, proof, source, [], [unit]),
            bold_heading,
        )
        sliced = preview._scoped_source_slice(unit, source.splitlines())
        self.assertTrue(sliced and sliced[0] == bold_heading)

    def test_numbered_marked_heading_matches_plain_section_title(self):
        bold_heading = "Ⅰ.[2024·**四川南充**]根据汉语提示完成句子。"
        plain_heading = "Ⅰ.[2024·四川南充]根据汉语提示完成句子。"
        unit = {
            "unit_id": "sec-num-u1",
            "unit_kind": "standalone",
            "identity_version": IDENTITY_VERSION,
            "numbers": [1],
            "stem": "1. Hello ____.",
            "section_id": "sec-num",
            "question_ids": {"1": "sec-num-l2-q1"},
            "source_span": {"start_line": 2, "end_line": 2, "basis": "cleaned_source"},
            "source_section": {"title": plain_heading, "start_line": 1, "end_line": 2},
        }
        lines = [bold_heading, "1. Hello ____."]
        headings = [{"title": plain_heading, "start_line": 1, "end_line": 2}]
        proof = {"start_line": 2, "end_line": 2}
        self.assertEqual(
            question_draft._numbered_marked_heading(unit, proof, lines, headings, [unit]),
            bold_heading,
        )

    def test_span_beyond_section_end_is_rejected(self):
        unit = _unit()
        source = "\n".join([HEADING, "", *BODY])
        proof = {"start_line": 3, "end_line": 7, "line_basis": "01_source/正文.txt"}
        self.assertEqual(question_draft._standalone_marked_heading(unit, proof, source, [], [unit]), "")
