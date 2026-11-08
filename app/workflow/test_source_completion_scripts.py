"""Missing production script regressions, using isolated DOCX/JSON fixtures."""
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts/06-upload" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


underline = module("mark_underline")
answers = module("check_answers")


def docx(path, paragraphs):
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = []
    import html
    for text in paragraphs:
        runs = []
        import re
        cursor = 0
        for match in re.finditer(r"<u>(.*?)</u>", text):
            runs.append('<w:r><w:t xml:space="preserve">' + html.escape(text[cursor:match.start()]) + '</w:t></w:r>')
            runs.append('<w:r><w:rPr><w:u/></w:rPr><w:t xml:space="preserve">' + html.escape(match.group(1)) + '</w:t></w:r>')
            cursor = match.end()
        runs.append('<w:r><w:t xml:space="preserve">' + html.escape(text[cursor:]) + '</w:t></w:r>')
        body.append("<w:p>" + "".join(runs) + "</w:p>")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", f'<w:document xmlns:w="{namespace}"><w:body>' + "".join(body) + '</w:body></w:document>')


class SourceCompletionScriptsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.batch = self.root / "batch.json"
        self.source = self.root / "source.docx"
        self.key = self.root / "answers.json"

    def write_batch(self, items):
        self.batch.write_text(json.dumps({"items": items}, ensure_ascii=False))

    def run_script(self, script, argv):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result = script.main(argv)
        return result, json.loads(output.getvalue())

    def test_native_docx_restores_word_mark_using_full_context_and_is_idempotent(self):
        docx(self.source, ["Lion dancing will continue to <u>evolve</u>. Other things evolve too.",
                           "61.What does <u>It</u> refer to?", "40.A.<u>choices</u> B.words C.time D.air"])
        self.write_batch([{"number": 59, "material": "Lion dancing will continue to evolve. Other things evolve too.",
                           "stem": "", "subquestions": [{"number": 61, "stem": "What does It refer to?", "options": []}]},
                          {"number": 40, "stem": "______", "options": ["A.choices", "B.words", "C.time", "D.air"]}])
        original = self.batch.read_bytes()
        code, report = self.run_script(underline, ["--batch", str(self.batch), "--docx", str(self.source)])
        self.assertEqual(code, 0, report)
        self.assertEqual(len(report["changes"]), 3)
        self.assertEqual(original, self.batch.read_bytes())
        code, report = self.run_script(underline, ["--batch", str(self.batch), "--docx", str(self.source), "--apply"])
        self.assertEqual(code, 0, report)
        data = json.loads(self.batch.read_text())
        self.assertEqual(data["items"][0]["material"], "Lion dancing will continue to <u>evolve</u>. Other things evolve too.")
        self.assertIn("<u>It</u>", data["items"][0]["subquestions"][0]["stem"])
        self.assertEqual(data["items"][1]["options"][0], "A.<u>choices</u>")
        after = self.batch.read_bytes()
        code, report = self.run_script(underline, ["--batch", str(self.batch), "--docx", str(self.source), "--apply"])
        self.assertEqual(code, 0, report)
        self.assertEqual(report["changes"], [])
        self.assertEqual(after, self.batch.read_bytes())

    def test_answer_appendix_marks_never_modify_question_surface(self):
        # Formal extraction splits at the repeated paper title + answer table.
        # The appendix title is absent from the surface, and its marked opening
        # sentence repeats an unmarked surface sentence: neither is a source mark.
        docx(self.source, [
            '2023年安徽省初中学业水平考试',
            'Ⅰ.阅读理解(共1小题)',
            'Read the <u>soundly</u> example.',
            '1.What does soundly mean?', 'A.Deeply B.Poorly C.Kindly D.Slowly',
            'Ⅹ.书面表达(共1小题)', 'My Experience of Making a Poster',
            'Last week,I made a poster.',
            '2023年安徽省初中学业水平考试',
            '题号 | 1 | 2', '答案 | A | B',
            '<u>My Experience of Making a Poster</u>',
            '<u>Last week,I made a poster.</u>',
        ])
        self.write_batch([
            {'number': 1, 'material': 'Read the soundly example.', 'stem': 'What does soundly mean?'},
            {'number': 2, 'stem': 'My Experience of Making a Poster\nLast week,I made a poster.'},
        ])
        code, report = self.run_script(underline, ['--batch', str(self.batch), '--docx', str(self.source), '--apply'])
        self.assertEqual(code, 0, report)
        data = json.loads(self.batch.read_text())
        self.assertEqual(data['items'][0]['material'], 'Read the <u>soundly</u> example.')
        self.assertNotIn('<u>', data['items'][1]['stem'])
        self.assertEqual([x['underlined'] for x in report['changes']], ['soundly'])
        code, report = self.run_script(underline, ['--batch', str(self.batch), '--docx', str(self.source)])
        self.assertEqual(code, 0, report)
        self.assertEqual(report['changes'], [])

    def test_repeated_section_answer_appendix_excluded_but_true_surface_mark_missing_fails(self):
        docx(self.source, [
            'Ⅰ.阅读理解(共1小题)', 'Read the <u>soundly</u> example.',
            '1.What does soundly mean?', 'A.Deeply B.Poorly C.Kindly D.Slowly',
            'Ⅰ.阅读理解', '1.A 【解析】 Answer.', '2.B 【解析】 More.',
            'An appendix <u>absent</u> word.',
        ])
        self.write_batch([{'number': 1, 'stem': 'Changed text without the source sentence.'}])
        before = self.batch.read_bytes()
        code, report = self.run_script(underline, ['--batch', str(self.batch), '--docx', str(self.source), '--apply'])
        self.assertEqual(code, 1, report)
        self.assertEqual([x['underlined'] for x in report['issues']], ['soundly'])
        self.assertEqual(self.batch.read_bytes(), before)

    def test_same_unit_stem_material_mirror_is_marked_not_ambiguous(self):
        # The same passage mirrored in one unit's own stem and material is the
        # same logical underline: both occurrences must be marked, not refused.
        docx(self.source, ["The marked <u>It</u> is here."])
        self.write_batch([{"number": 1, "stem": "The marked It is here.",
                           "material": "The marked It is here.\nThe rest continues."}])
        code, report = self.run_script(underline, ["--batch", str(self.batch), "--docx", str(self.source)])
        self.assertEqual(code, 0, report)
        self.assertNotIn("UNDERLINE_MAPPING_AMBIGUOUS", {i["code"] for i in report["issues"]})
        code, report = self.run_script(underline, ["--batch", str(self.batch), "--docx", str(self.source), "--apply"])
        self.assertEqual(code, 0, report)
        data = json.loads(self.batch.read_text())
        self.assertIn("<u>It</u>", data["items"][0]["stem"])
        self.assertIn("<u>It</u>", data["items"][0]["material"])

    def _mk(self, path):
        return ("owner", "key", path, 0, 1, False, False)

    def test_same_unit_mirror_helper_strict_semantics(self):
        # Positive: unit stem + its own material + its own materialBlocks cell.
        ok = [self._mk(("items", 3, "stem")),
              self._mk(("items", 3, "material")),
              self._mk(("items", 3, "materialBlocks", 0, "rows", 0, "cells", 0, "text"))]
        self.assertTrue(underline._same_unit_mirror(ok))
        # Negative: a subquestion field is not a shared-passage mirror.
        self.assertFalse(underline._same_unit_mirror(
            [self._mk(("items", 3, "stem")),
             self._mk(("items", 3, "subquestions", 0, "stem"))]))
        # Negative: an option text is not a mirror.
        self.assertFalse(underline._same_unit_mirror(
            [self._mk(("items", 3, "material")),
             self._mk(("items", 3, "options", 0, "text"))]))
        # Negative: the same field twice (repeated paragraph in one field).
        self.assertFalse(underline._same_unit_mirror(
            [self._mk(("items", 3, "stem")), self._mk(("items", 3, "stem"))]))
        # Negative: two different units.
        self.assertFalse(underline._same_unit_mirror(
            [self._mk(("items", 3, "stem")), self._mk(("items", 4, "stem"))]))

    def test_same_unit_same_field_repeated_is_ambiguous_not_mirror(self):
        # The same paragraph twice inside ONE field is not a stem/material mirror:
        # it must stay ambiguous and never apply a partial change.
        paragraph = "The marked It is here. A second sentence pads the paragraph."
        docx(self.source, ["The marked <u>It</u> is here. A second sentence pads the paragraph."])
        self.write_batch([{"number": 1, "stem": paragraph + "\n" + paragraph}])
        before = self.batch.read_bytes()
        code, report = self.run_script(underline, ["--batch", str(self.batch), "--docx", str(self.source), "--apply"])
        self.assertEqual(code, 1, report)
        self.assertEqual(before, self.batch.read_bytes())
        self.assertIn("UNDERLINE_MAPPING_AMBIGUOUS", {i["code"] for i in report["issues"]})

    def test_same_unit_different_subquestions_is_ambiguous_not_mirror(self):
        # The same sentence in two subquestions of one unit are separate questions,
        # not a shared-passage mirror: must stay ambiguous.
        docx(self.source, ["The marked <u>It</u> is here."])
        self.write_batch([{"number": 1, "stem": "",
                           "subquestions": [{"number": 1, "stem": "The marked It is here."},
                                            {"number": 2, "stem": "The marked It is here."}]}])
        before = self.batch.read_bytes()
        code, report = self.run_script(underline, ["--batch", str(self.batch), "--docx", str(self.source), "--apply"])
        self.assertEqual(code, 1, report)
        self.assertEqual(before, self.batch.read_bytes())
        self.assertIn("UNDERLINE_MAPPING_AMBIGUOUS", {i["code"] for i in report["issues"]})

    def test_cross_unit_ambiguity_still_fails_without_partial_apply(self):
        # The same sentence in two genuinely different units is a real ambiguity.
        docx(self.source, ["The marked <u>It</u> is here.", "A single <u>I</u> occurs here."])
        self.write_batch([{"number": 1, "stem": "The marked It is here."},
                          {"number": 2, "stem": "The marked It is here."},
                          {"number": 3, "stem": "A single I occurs here. Another I is there."}])
        before = self.batch.read_bytes()
        code, report = self.run_script(underline, ["--batch", str(self.batch), "--docx", str(self.source), "--apply"])
        self.assertEqual(code, 1)
        self.assertEqual(before, self.batch.read_bytes())
        self.assertIn("UNDERLINE_MAPPING_AMBIGUOUS", {i["code"] for i in report["issues"]})

    def test_bare_single_letter_never_triggers_global_marking(self):
        batch = {"items": [{"stem": "The only I occurs here."}]}
        candidate, report = underline.restore(batch, "<u>I</u>")
        self.assertFalse(report["passed"])
        self.assertEqual(candidate, batch)
        candidate, report = underline.restore({"items": [{"stem": "An Item occurs here."}]}, "<u>It</u>")
        self.assertFalse(report["passed"])
        self.assertNotIn("<u>", candidate["items"][0]["stem"])

    def test_word_changed_to_blank_is_missing_and_numbered_blanks_untouched(self):
        docx(self.source, ["The word <u>evolve</u> matters.", "The gap <u>　40　</u> remains.", "A spelling <u>r　　　　</u> word."])
        self.write_batch([{"number": 40, "material": "The word ______ matters. The gap __40__ remains."}])
        before = self.batch.read_bytes()
        code, report = self.run_script(underline, ["--batch", str(self.batch), "--docx", str(self.source), "--apply"])
        self.assertEqual(code, 1)
        self.assertEqual(len(report["issues"]), 1, report)
        self.assertEqual(report["issues"][0]["underlined"], "evolve")
        self.assertEqual(before, self.batch.read_bytes())

    def test_mapping_across_table_cell_text_and_option_dict(self):
        candidate, report = underline.restore({"items": [{"materialBlocks": [{"type": "table", "rows": [{"cells": [{"text": "Read this marked word here."}]}]}],
                                                         "options": [{"label": "A", "text": "Second marked option."}]}]},
                                             "Read this <u>marked</u> word here.\nA.Second <u>marked</u> option.")
        self.assertTrue(report["passed"], report)
        self.assertIn("<u>marked</u>", candidate["items"][0]["materialBlocks"][0]["rows"][0]["cells"][0]["text"])
        self.assertIn("<u>marked</u>", candidate["items"][0]["options"][0]["text"])

    def test_json_answer_map_covers_40_arrays_and_writing_whitespace(self):
        self.write_batch([{"number": 39, "unit_kind": "group", "subquestions": [{"number": 39, "answer": "C"}, {"number": 40, "answer": ["D"]}]},
                          {"number": 81, "answer": "Dear Mike,\nI recommend this book.\nYours, Li Hui"}])
        self.key.write_text(json.dumps({"answers": {"39": ["C"], "40": "d.", "81": "Dear Mike, I recommend this book. Yours, Li Hui"}, "evidence": {"40": ["L1"]}}))
        before = self.batch.read_bytes()
        code, report = self.run_script(answers, ["--batch", str(self.batch), "--answers", str(self.key)])
        self.assertEqual(code, 0, report)
        self.assertEqual(report["checked_numbers"], [39, 40, 81])
        self.assertEqual(before, self.batch.read_bytes())

    def test_missing_40_or_conflicting_key_fails_not_solves(self):
        self.write_batch([{"number": 40, "answer": "D"}])
        self.key.write_text('{"answers":{"39":"D"}}')
        code, report = self.run_script(answers, ["--batch", str(self.batch), "--answers", str(self.key)])
        self.assertEqual(code, 1)
        self.assertIn("ANSWER_SOURCE_MISSING", {i["code"] for i in report["issues"]})
        self.key.write_text('{"40":"D","40":"C"}')
        code, report = self.run_script(answers, ["--batch", str(self.batch), "--answers", str(self.key)])
        self.assertEqual(code, 1)
        self.assertIn("重复", report["issues"][0]["message"])
        code, report = self.run_script(answers, ["--batch", str(self.batch)])
        self.assertEqual(code, 1)

    def test_numbered_txt_and_native_docx_answer_sources(self):
        self.write_batch([{"number": 39, "answer": "C"}, {"number": 40, "answer": "D"}, {"number": 81, "answer": "Dear Mike,\nThank you.\nLi Hui"}])
        text = "参考答案\n39.C 40.D\n81. Dear Mike,\nThank you.\nLi Hui"
        key = self.root / "key.txt"
        key.write_text(text)
        code, report = self.run_script(answers, ["--batch", str(self.batch), "--answers", str(key)])
        self.assertEqual(code, 0, report)
        docx(self.source, text.splitlines())
        code, report = self.run_script(answers, ["--batch", str(self.batch), "--answers", str(self.source)])
        self.assertEqual(code, 0, report)

    def test_answer_multi_part_arrays_preserve_order_and_meaning(self):
        batch = {"items": [{"number": 40, "answer": ["in", "the park"]}]}
        report = answers.inspect(batch, answers.collect_json({"40": ["in", "the  park"]}))
        self.assertTrue(report["passed"])
        report = answers.inspect(batch, answers.collect_json({"40": ["the park", "in"]}))
        self.assertFalse(report["passed"])
        self.assertEqual(report["issues"][0]["code"], "ANSWER_MISMATCH")

    def test_answer_rows_and_source_sequence_are_explicit_and_cover_every_number(self):
        self.assertTrue(answers.inspect({"items": [{"number": 40, "answer": "D"}]}, answers.collect_json([{"number": 40, "answer": ["D"]}]))["passed"])
        sequence = answers.collect_json({"answers": ["C"] * 39 + ["D"]})
        self.assertEqual(sequence["40"]["normalized"], "D")
        with self.assertRaises(ValueError):
            answers.parse_text("A B C D")
        with self.assertRaises(ValueError):
            answers.parse_text("40.D\n40.C")


if __name__ == "__main__":
    unittest.main()


class MissingAnswerIsNotAnInputErrorTest(unittest.TestCase):
    """书面表达等原卷无答案时，不能把整卷体检判成 ANSWER_INPUT_ERROR。"""

    def answers_module(self):
        import importlib.util
        from pathlib import Path
        path = Path(__file__).resolve().parents[1] / "scripts/06-upload/check_answers.py"
        spec = importlib.util.spec_from_file_location("check_answers_missing", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_blank_writing_answer_does_not_raise(self):
        module = self.answers_module()
        self.assertIsNone(module.normalize(""))
        self.assertIsNone(module.normalize(None))
        self.assertIsNone(module.normalize("   "))
        self.assertEqual(module.normalize("C"), "C")

    def test_batch_with_blank_writing_answer_passes_and_is_not_input_error(self):
        module = self.answers_module()
        batch = {"items": [
            {"number": 1, "answer": "A"},
            {"number": 96, "answer": ""},  # 书面表达：原卷无独立答案
        ]}
        answers = module.collect_json({"answers": {"1": "A"}})
        report = module.inspect(batch, answers)
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["issues"], [])

    def test_truly_blank_choice_answer_is_still_not_a_crash(self):
        module = self.answers_module()
        batch = {"items": [{"number": 2, "answer": ""}]}
        # 空白答案视为无答案：不需要独立答案覆盖，也不会抛输入错误。
        report = module.inspect(batch, {})
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["issues"], [])
