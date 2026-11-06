# -*- coding: utf-8 -*-
"""结构 AI 复核：只接受逐字、题号明确、位于重复题号范围内的答案行。"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from workflow import structure_ai


def reply(lines):
    return {"status": "completed",
            "artifacts": {"structure": {"answer_lines": lines}},
            "notes": [], "findings": []}


class StructureAiClassifyTest(unittest.TestCase):
    def test_accepts_evidence_bound_units(self):
        raw = ["1. What is it?", "A. A", "B. B", "2. Shared material", "3. Next line"]
        candidate = {"sections": [{"questions": [1, 2, 3]}]}
        reply_value = {"units": [
            {"unit_kind": "standalone", "type": "单项填空", "numbers": [1],
             "stem": "1. What is it?\nA. A\nB. B", "source_span": {"start_line": 1, "end_line": 3},
             "source_parts": [{"kind": "stem", "start_line": 1, "end_line": 1},
                              {"kind": "option", "start_line": 2, "end_line": 3}]},
            {"unit_kind": "group", "type": "阅读理解", "numbers": [2, 3],
             "stem": "2. Shared material\n3. Next line", "source_span": {"start_line": 4, "end_line": 5}},
        ]}
        got = structure_ai._sanitize_classified(reply_value, "\n".join(raw), candidate)
        self.assertEqual([row["numbers"] for row in got], [[1], [2, 3]])
        self.assertEqual([part['kind'] for part in got[0]['source_parts']], ['stem', 'option'])

    def test_auto_classify_accepts_local_agent_envelope_and_reuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            source = batch / '01_source'
            source.mkdir()
            (source / '正文.txt').write_text('1. What is it?\nA. Yes.\n', encoding='utf-8')
            (source / 'structure.json').write_text('{"sections": []}', encoding='utf-8')
            calls = []
            def invoke(prompt, run_dir, config):
                calls.append(prompt)
                return {'response': {'status': 'completed', 'artifacts': {'structure': {'units': [
                    {'unit_kind': 'standalone', 'type': '单项填空', 'numbers': [1],
                     'stem': '1. What is it?\nA. Yes.',
                     'source_span': {'start_line': 1, 'end_line': 2}}
                ]}}, 'notes': [], 'findings': []}}
            first = structure_ai.auto_classify(batch, invoke_fn=invoke, force=True)
            self.assertEqual(first['units'][0]['numbers'], [1])
            second = structure_ai.auto_classify(batch, invoke_fn=invoke, force=True)
            self.assertTrue(second['reused'])
            self.assertEqual(len(calls), 1)

    def test_classify_uses_real_local_agent_protocol(self):
        from workflow import local_agent
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            source = batch / '01_source'
            source.mkdir()
            (source / '正文.txt').write_text('1. What is it?\nA. Yes.\n', encoding='utf-8')
            (source / 'structure.json').write_text('{"sections": []}', encoding='utf-8')
            result = {'status': 'completed', 'artifacts': {'structure': {'units': [
                {'unit_kind': 'standalone', 'type': '单项填空', 'numbers': [1],
                 'stem': '1. What is it?\nA. Yes.', 'source_span': {'start_line': 1, 'end_line': 2}}
            ]}}, 'notes': [], 'findings': []}
            script = batch / 'fake_agent.py'
            script.write_text('import sys\nsys.stdin.read()\nprint(' + repr(json.dumps(result, ensure_ascii=False)) + ')\n', encoding='utf-8')
            config = local_agent._validate_config({'provider': 'command', 'command': ['{python}', str(script)],
                                                   'timeout_seconds': 10}, batch)
            classified = structure_ai.auto_classify(batch, invoke_fn=local_agent.invoke, config=config, force=True)
            self.assertEqual(classified['units'][0]['numbers'], [1])

    def test_rejects_non_contiguous_or_unknown_source_span(self):
        raw = ["1. Question", "2. Other"]
        candidate = {"sections": [{"questions": [1, 2]}]}
        reply_value = {"units": [{"unit_kind": "group", "type": "阅读理解", "numbers": [1, 2],
                                   "stem": "not source", "source_span": {"start_line": 1, "end_line": 2}}]}
        self.assertEqual(structure_ai._sanitize_classified(reply_value, "\n".join(raw), candidate), [])


class StructureAiSanitizeTest(unittest.TestCase):
    raw = [
        "Ⅹ.单词拼写(共2小题)",
        "91.Mary has developed a new way of life.",
        "91.nature",
        "92.Frank,take off your jacket.",
        "92.wet",
        "A.Between 8 and 12.",
        "Ⅺ.书面表达(共1小题)",
    ]

    def test_accepts_verbatim_numbered_answer(self):
        got = structure_ai.sanitize(reply([{"line": 3, "text": "91.nature", "kind": "answer"}]), self.raw, {91, 92})
        self.assertEqual([item["line"] for item in got], [3])

    def test_rejects_paraphrase_heading_option_and_unrelated_number(self):
        got = structure_ai.sanitize(reply([
            {"line": 3, "text": "91. nature (paraphrased)"},
            {"line": 1, "text": "Ⅹ.单词拼写(共2小题)"},
            {"line": 6, "text": "A.Between 8 and 12."},
            {"line": 2, "text": "91.Mary has developed a new way of life."},
        ]), self.raw, {91, 92})
        self.assertEqual(got, [])

    def test_duplicate_and_non_dict_items_are_ignored(self):
        got = structure_ai.sanitize(reply([
            {"line": 3, "text": "91.nature"},
            {"line": 3, "text": "91.nature"},
            "bad",
        ]), self.raw, {91, 92})
        self.assertEqual(len(got), 1)

    def test_explanation_marker_allows_number_outside_duplicate_set(self):
        raw = self.raw + ["88.It is fun.【解析】根据第一段。"]
        got = structure_ai.sanitize(reply([{"line": 8, "text": "88.It is fun.【解析】根据第一段。"}]),
                                    raw, {91, 92})
        self.assertEqual([item["line"] for item in got], [8])


class StructureAiRangeTest(unittest.TestCase):
    raw = [
        "2026年某省中考英语",
        "Ⅰ.单项填空(共2小题)",
        "1. What do you want?",
        "A.Yes. B.No. C.Maybe.",
        "2. Where are you?",
        "A.Home. B.School. C.Park.",
        "1.A",
        "2.B",
    ]

    def reply(self, ranges):
        return {"artifacts": {"structure": {"answer_ranges": ranges}}}

    def test_accepts_trailing_answer_block(self):
        got = structure_ai.sanitize_ranges(self.reply([{"start": 7, "end": 8, "reason": "答案附录"}]), self.raw)
        self.assertEqual([(x["start"], x["end"]) for x in got], [(7, 8)])

    def test_rejects_block_with_question_or_blank(self):
        raw = self.raw + ["Ⅱ.单词拼写(共1小题)", "3.Question?"]
        for start, end in ((2, 8), (3, 8), (7, 10)):
            with self.subTest(start=start, end=end):
                self.assertEqual(structure_ai.sanitize_ranges(self.reply([{"start": start, "end": end}]), raw), [])
        raw2 = self.raw + ["Ⅱ.单词拼写(共1小题)", "3.Mary has a n<u>　　　　</u> life."]
        self.assertEqual(structure_ai.sanitize_ranges(self.reply([{"start": 7, "end": 10}]), raw2), [])

    def test_rejects_number_not_seen_before(self):
        raw = self.raw + ["9.new"]
        self.assertEqual(structure_ai.sanitize_ranges(self.reply([{"start": 9, "end": 9}]), raw), [])

    def test_ranges_must_stay_in_trailing_part_without_heading(self):
        raw = ["1.A", "2.B"] + ["正文"] * 10 + ["1.A", "2.B"]
        self.assertEqual(structure_ai.sanitize_ranges(self.reply([{"start": 1, "end": 2}]), raw), [])


@patch.dict(os.environ, {'WF_STRUCTURE_AI': '1'})
class StructureAiApplyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.batch = Path(self.temp.name) / "batch"
        (self.batch / "01_source").mkdir(parents=True)
        (self.batch / "07_review").mkdir(parents=True)
        raw = ("Ⅹ.单词拼写(共2小题)\n"
               "91.Mary has developed a new way of life.\n"
               "91.nature\n"
               "92.Frank,take off your jacket.\n"
               "92.wet\n")
        (self.batch / "01_source" / "原始正文.txt").write_text(raw, encoding="utf-8")
        (self.batch / "01_source" / "正文.txt").write_text(raw, encoding="utf-8")
        structure = {"version": "composite-extract-1", "status": "candidate",
                     "sections": [{"type": "单词拼写", "title": "Ⅹ.单词拼写(共2小题)",
                                   "questions": [91, 92, 91, 92]}],
                     "unresolved": [{"code": "DUPLICATE_QUESTION_NUMBER", "line": 1,
                                     "message": "Ⅹ.单词拼写 内题号重复：[91, 92]"}]}
        (self.batch / "01_source" / "structure.json").write_text(json.dumps(structure), encoding="utf-8")
        (self.batch / "01_source" / "extraction.json").write_text("{}", encoding="utf-8")
        (self.batch / "01_source" / "metadata.json").write_text("{}", encoding="utf-8")
        (self.batch / "workflow").mkdir()
        (self.batch / "workflow" / "manifest.json").write_text(json.dumps({
            "workflow_id": "batch", "stages": {"extract": {"output_hashes": {}}}
        }), encoding="utf-8")

    def test_apply_reextracts_with_skipped_lines_and_updates_hashes(self):
        result = structure_ai.apply(self.batch, [
            {"line": 3, "text": "91.nature", "kind": "answer"},
            {"line": 5, "text": "92.wet", "kind": "answer"},
        ])
        self.assertEqual(result["skipped"], [3, 5])
        body = (self.batch / "01_source" / "正文.txt").read_text(encoding="utf-8")
        appendix = (self.batch / "01_source" / "附录.txt").read_text(encoding="utf-8")
        self.assertNotIn("91.nature", body)
        self.assertIn("91.nature", appendix)
        structure = json.loads((self.batch / "01_source" / "structure.json").read_text(encoding="utf-8"))
        self.assertEqual(structure["sections"][0]["questions"], [91, 92])
        self.assertNotIn("DUPLICATE_QUESTION_NUMBER",
                         [x["code"] for x in structure["unresolved"]])
        manifest = json.loads((self.batch / "workflow" / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("01_source/正文.txt", manifest["stages"]["extract"]["output_hashes"])

    def test_apply_with_range_moves_whole_block(self):
        raw = ("2026年某省中考英语\n"
               "Ⅰ.单项填空(共2小题)\n"
               "1. What do you want?\n"
               "A.Yes. B.No. C.Maybe.\n"
               "2. Where are you?\n"
               "A.Home. B.School. C.Park.\n"
               "1.A\n"
               "2.B\n")
        (self.batch / "01_source" / "原始正文.txt").write_text(raw, encoding="utf-8")
        (self.batch / "01_source" / "正文.txt").write_text(raw, encoding="utf-8")
        structure_ai._write(self.batch / "01_source" / "structure.json", {
            "sections": [{"type": "单项填空", "title": "Ⅰ.单项填空(共2小题)",
                          "questions": [1, 2, 1, 2]}],
            "unresolved": [{"code": "DUPLICATE_QUESTION_NUMBER", "line": 2,
                            "message": "Ⅰ.单项填空 内题号重复：[1, 2]"}]})
        result = structure_ai.apply(self.batch, [], [{"start": 7, "end": 8}])
        self.assertEqual(result["skipped"], [7, 8])
        body = (self.batch / "01_source" / "正文.txt").read_text(encoding="utf-8")
        appendix = (self.batch / "01_source" / "附录.txt").read_text(encoding="utf-8")
        self.assertTrue(body.rstrip().endswith("Park."))
        self.assertIn("1.A", appendix)
        self.assertIn("2.B", appendix)

    def test_auto_review_skips_without_duplicates(self):
        structure_ai._write(self.batch / "01_source" / "structure.json",
                            {"sections": [], "unresolved": []})
        result = structure_ai.auto_review(self.batch)
        self.assertFalse(result["ran"])
        self.assertIn("没有需要复核", result["reason"])

    def test_auto_review_accepts_valid_model_reply(self):
        result = structure_ai.auto_review(
            self.batch,
            invoke_fn=lambda prompt, run_dir, config: {"response": reply([
                {"line": 3, "text": "91.nature"}, {"line": 5, "text": "92.wet"}])},
            config={})
        self.assertTrue(result["ran"])
        self.assertEqual(result["skipped"], [3, 5])
        marker = json.loads((self.batch / "01_source" / "structure.ai.json").read_text(encoding="utf-8"))
        self.assertEqual(len(marker["answer_lines"]), 2)

    def test_auto_review_accepts_answer_range(self):
        raw = ("2026年某省中考英语\n"
               "Ⅰ.单项填空(共2小题)\n"
               "1. What do you want?\n"
               "A.Yes. B.No. C.Maybe.\n"
               "2. Where are you?\n"
               "A.Home. B.School. C.Park.\n"
               "1.A\n"
               "2.B\n")
        (self.batch / "01_source" / "原始正文.txt").write_text(raw, encoding="utf-8")
        (self.batch / "01_source" / "正文.txt").write_text(raw, encoding="utf-8")
        structure_ai._write(self.batch / "01_source" / "structure.json", {
            "sections": [{"type": "单项填空", "title": "Ⅰ.单项填空(共2小题)",
                          "questions": [1, 2, 1, 2]}],
            "unresolved": [{"code": "DUPLICATE_QUESTION_NUMBER", "line": 2,
                            "message": "Ⅰ.单项填空 内题号重复：[1, 2]"}]})
        result = structure_ai.auto_review(
            self.batch,
            invoke_fn=lambda prompt, run_dir, config: {"response": {
                "artifacts": {"structure": {"answer_ranges": [
                    {"start": 7, "end": 8, "reason": "整卷后附答案"}]}}}},
            config={})
        self.assertTrue(result["ran"])
        self.assertEqual(result["skipped"], [7, 8])
        body = (self.batch / "01_source" / "正文.txt").read_text(encoding="utf-8")
        self.assertTrue(body.rstrip().endswith("Park."))

    def test_auto_review_rejects_unverifiable_reply_without_changing_files(self):
        before = (self.batch / "01_source" / "正文.txt").read_text(encoding="utf-8")
        result = structure_ai.auto_review(
            self.batch,
            invoke_fn=lambda prompt, run_dir, config: {"response": reply([
                {"line": 3, "text": "91.paraphrased"}])},
            config={})
        self.assertTrue(result["ran"])
        self.assertEqual(result["skipped"], [])
        after = (self.batch / "01_source" / "正文.txt").read_text(encoding="utf-8")
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
