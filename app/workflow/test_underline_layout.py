"""Strict underline restoration across explicit DOCX/JSON answer-rule layouts."""
import contextlib
import importlib.util
import io
import json
import html
from pathlib import Path
import re
import tempfile
import unittest
import zipfile


def docx(path, paragraphs):
    """Minimal native underline fixture, independent of other test modules."""
    body = []
    for paragraph in paragraphs:
        runs, cursor = [], 0
        for mark in re.finditer(r'<u>(.*?)</u>', paragraph):
            runs.append('<w:r><w:t xml:space="preserve">' + html.escape(paragraph[cursor:mark.start()]) + '</w:t></w:r>')
            runs.append('<w:r><w:rPr><w:u/></w:rPr><w:t xml:space="preserve">' + html.escape(mark.group(1)) + '</w:t></w:r>')
            cursor = mark.end()
        runs.append('<w:r><w:t xml:space="preserve">' + html.escape(paragraph[cursor:]) + '</w:t></w:r>')
        body.append('<w:p>' + ''.join(runs) + '</w:p>')
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('word/document.xml',
                          '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                          + ''.join(body) + '</w:body></w:document>')

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/06-upload/mark_underline.py'
spec = importlib.util.spec_from_file_location('underline_layout', SCRIPT)
underline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(underline)

SOURCE = '92.The word “<u>remove</u>” in the second paragraph probably means<u>　　　　</u>in Chinese.'
STEM = 'The word “remove” in the second paragraph probably means______in Chinese.'


class UnderlineLayoutTest(unittest.TestCase):
    def test_actual_2014_prompt_existing_mark_is_idempotent(self):
        batch = {'items': [{'number': 91, 'subquestions': [
            {'number': 92, 'stem': STEM.replace('remove', '<u>remove</u>')}]}]}
        candidate, report = underline.restore(batch, SOURCE)
        self.assertTrue(report['passed'], report)
        self.assertEqual(report['changes'], [])
        self.assertEqual(candidate, batch)

    def test_missing_mark_is_restored_and_second_run_is_noop(self):
        batch = {'items': [{'stem': STEM}]}
        candidate, report = underline.restore(batch, SOURCE)
        self.assertTrue(report['passed'], report)
        self.assertEqual(len(report['changes']), 1)
        self.assertEqual(candidate['items'][0]['stem'], STEM.replace('remove', '<u>remove</u>'))
        self.assertEqual(batch['items'][0]['stem'], STEM)
        again, report = underline.restore(candidate, SOURCE)
        self.assertTrue(report['passed'], report)
        self.assertEqual(report['changes'], [])
        self.assertEqual(again, candidate)

    def test_blank_width_and_word_offsets_before_and_after_blank(self):
        for blank in ('__', '__________', '<u>　　</u>', '<u>  </u>'):
            with self.subTest(blank=blank):
                batch = {'items': [{'stem': 'A remove B ' + blank + ' C tooth D.'}]}
                candidate, report = underline.restore(batch, 'A <u>remove</u> B <u>　　</u> C <u>tooth</u> D.')
                self.assertTrue(report['passed'], report)
                self.assertEqual(candidate['items'][0]['stem'], 'A <u>remove</u> B ' + blank + ' C <u>tooth</u> D.')

    def test_rule_at_end_of_mark_preserves_its_entire_width(self):
        candidate, report = underline.restore({'items': [{'stem': 'Read ______'}]}, '<u>Read __</u>')
        self.assertTrue(report['passed'], report)
        self.assertEqual(candidate['items'][0]['stem'], '<u>Read ______</u>')
        _, again = underline.restore(candidate, '<u>Read __</u>')
        self.assertTrue(again['passed'], again)
        self.assertEqual(again['changes'], [])

    def test_bare_marked_field_with_rule_cannot_match_embedded_prose(self):
        batch = {'items': [{'stem': 'Before Read ______ After'}]}
        candidate, report = underline.restore(batch, '<u>Read __</u>')
        self.assertFalse(report['passed'], report)
        self.assertEqual(candidate, batch)

    def test_blank_only_source_is_not_restored_as_a_word(self):
        batch = {'items': [{'stem': 'An answer ______ remains.'}]}
        candidate, report = underline.restore(batch, 'An answer <u>　　</u> remains.')
        self.assertTrue(report['passed'], report)
        self.assertEqual(report['changes'], [])
        self.assertEqual(candidate, batch)

    def test_missing_moved_or_replaced_blank_and_edited_context_still_fail(self):
        for stem in (STEM.replace('______', ''), STEM.replace('______', 'answer'),
                     STEM.replace('______', '').replace('probably', '______probably'),
                     STEM.replace('second', 'third'), STEM.replace('remove', 'removed'),
                     STEM.replace('“', '"').replace('”', '"')):
            with self.subTest(stem=stem):
                batch = {'items': [{'stem': stem}]}
                candidate, report = underline.restore(batch, SOURCE)
                self.assertFalse(report['passed'], report)
                self.assertEqual(report['issues'][0]['code'], 'UNDERLINE_MAPPING_MISSING')
                self.assertEqual(candidate, batch)

    def test_existing_mark_in_another_question_does_not_hide_missing_context(self):
        batch = {'items': [{'stem': STEM.replace('remove', '______')},
                           {'stem': 'Another question has <u>remove</u>.'}]}
        candidate, report = underline.restore(batch, SOURCE)
        self.assertFalse(report['passed'], report)
        self.assertEqual(report['issues'][0]['code'], 'UNDERLINE_MAPPING_MISSING')
        self.assertEqual(candidate, batch)

    def test_normalized_duplicates_remain_ambiguous_even_if_one_is_marked(self):
        batch = {'items': [{'stem': STEM}, {'stem': STEM.replace('remove', '<u>remove</u>').replace('______', '__')}]}
        candidate, report = underline.restore(batch, SOURCE)
        self.assertFalse(report['passed'], report)
        self.assertEqual(report['issues'][0]['code'], 'UNDERLINE_MAPPING_AMBIGUOUS')
        self.assertEqual(candidate, batch)

    def test_partial_existing_word_mark_is_a_conflict(self):
        batch = {'items': [{'stem': STEM.replace('remove', '<u>rem</u>ove')}]}
        candidate, report = underline.restore(batch, SOURCE)
        self.assertFalse(report['passed'], report)
        self.assertEqual(report['issues'][0]['code'], 'UNDERLINE_OVERLAP')
        self.assertEqual(candidate, batch)

    def test_cli_dry_run_apply_and_rerun_with_native_docx_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, batch = root / 'source.docx', root / 'batch.json'
            docx(source, [SOURCE])
            batch.write_text(json.dumps({'items': [{'stem': STEM}]}), encoding='utf-8')
            source_bytes, batch_bytes = source.read_bytes(), batch.read_bytes()
            def run(apply=False):
                args = ['--batch', str(batch), '--docx', str(source)] + (['--apply'] if apply else [])
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    code = underline.main(args)
                return code, json.loads(output.getvalue())
            code, report = run()
            self.assertEqual(code, 0, report)
            self.assertEqual(len(report['changes']), 1)
            self.assertFalse(report['applied'])
            self.assertEqual(batch.read_bytes(), batch_bytes)
            code, report = run(True)
            self.assertEqual(code, 0, report)
            self.assertTrue(report['applied'])
            applied = batch.read_bytes()
            self.assertIn('<u>remove</u>', json.loads(applied)['items'][0]['stem'])
            code, report = run(True)
            self.assertEqual(code, 0, report)
            self.assertEqual(report['changes'], [])
            self.assertFalse(report['applied'])
            self.assertEqual(batch.read_bytes(), applied)
            self.assertEqual(source.read_bytes(), source_bytes)

    def test_cli_refuses_partial_write_when_another_mark_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, batch = root / 'source.docx', root / 'batch.json'
            docx(source, [SOURCE, 'Other <u>missing</u> source context.'])
            batch.write_text(json.dumps({'items': [{'stem': STEM}]}), encoding='utf-8')
            before = batch.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()) as output:
                code = underline.main(['--batch', str(batch), '--docx', str(source), '--apply'])
            report = json.loads(output.getvalue())
            self.assertEqual(code, 1, report)
            self.assertEqual(len(report['changes']), 1)
            self.assertFalse(report['applied'])
            self.assertEqual(batch.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
