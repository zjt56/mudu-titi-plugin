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
        for mark in re.finditer(r'<u>(.*?)</u>|\*\*(.*?)\*\*', paragraph):
            runs.append('<w:r><w:t xml:space="preserve">' + html.escape(paragraph[cursor:mark.start()]) + '</w:t></w:r>')
            style = 'u' if mark.group(1) is not None else 'b'
            runs.append(f'<w:r><w:rPr><w:{style}/></w:rPr><w:t xml:space="preserve">'
                        + html.escape(mark.group(1) if style == 'u' else mark.group(2)) + '</w:t></w:r>')
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
    def test_separate_output_without_docx_preserves_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / 'batch_meta.base.json'
            final = Path(tmp) / 'batch_meta.json'
            data = {'items': [{'stem': 'A question'}]}
            base.write_text(json.dumps(data), encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(underline.main(['--batch', str(base), '--out', str(final),
                                                 '--docx', '', '--apply']), 0)
            self.assertEqual(json.loads(final.read_text()), data)
            self.assertEqual(json.loads(base.read_text()), data)

    def test_separate_output_with_docx_keeps_base_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base, final, paper = root / 'base.json', root / 'final.json', root / 'paper.docx'
            data = {'items': [{'stem': 'Choose the word remove.'}]}
            base.write_text(json.dumps(data), encoding='utf-8')
            docx(paper, ['Choose the word <u>remove</u>.'])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(underline.main(['--batch', str(base), '--out', str(final),
                                                 '--docx', str(paper), '--apply']), 0)
            self.assertEqual(json.loads(base.read_text()), data)
            self.assertEqual(json.loads(final.read_text())['items'][0]['stem'],
                             'Choose the word <u>remove</u>.')

    def test_actual_2014_prompt_existing_mark_is_idempotent(self):
        batch = {'items': [{'number': 91, 'subquestions': [
            {'number': 92, 'stem': STEM.replace('remove', '<u>remove</u>')}]}]}
        candidate, report = underline.restore(batch, SOURCE)
        self.assertTrue(report['passed'], report)
        self.assertEqual(report['changes'], [])
        self.assertEqual(candidate, batch)

    def test_bold_question_numbers_preserve_full_stem_and_underline(self):
        cases = (
            ('**1.**I’m <u>fine</u>.(对画线部分提问)', 'I’m fine.(对画线部分提问)', 'fine'),
            ('**1****.**[传统文化]The loong was a symbol in <u>a</u>________(古代的) China.',
             '[传统文化]The loong was a symbol in a______(古代的) China.', 'a______'),
            ('**2.**My name is <u>Susan</u>.(对画线部分提问)',
             'My name is Susan.(对画线部分提问)', 'Susan'),
            ('**(　)3.**读音与hell<u>o</u>相同的是______。',
             '读音与hello相同的是______。', 'o'),
        )
        for source, stem, word in cases:
            with self.subTest(source=source):
                replacement = '<u>a</u>______' if word == 'a______' else f'<u>{word}</u>'
                expected = '**' + (stem.replace(word, replacement, 1)) + '\n作答线 ______'
                batch = {'items': [{'stem': '**' + stem + '\n作答线 ______'}]}
                candidate, report = underline.restore(batch, source)
                self.assertTrue(report['passed'], report)
                self.assertEqual(candidate['items'][0]['stem'], expected)
                self.assertEqual(len(report['changes']), 1)
                again, report = underline.restore(candidate, source)
                self.assertTrue(report['passed'], report)
                self.assertEqual(report['changes'], [])
                self.assertEqual(again, candidate)

    def test_bold_number_keeps_missing_and_ambiguous_context_checks(self):
        source = '**1.**I’m <u>fine</u>.(对画线部分提问)'
        stem = 'I’m fine.(对画线部分提问)'
        for stems, code in ((['I’m fine.(改为同义句)'], 'UNDERLINE_MAPPING_MISSING'),
                            ([stem, stem], 'UNDERLINE_MAPPING_AMBIGUOUS')):
            with self.subTest(code=code):
                batch = {'items': [{'stem': text} for text in stems]}
                candidate, report = underline.restore(batch, source)
                self.assertFalse(report['passed'], report)
                self.assertEqual([issue['code'] for issue in report['issues']], [code])
                self.assertEqual(candidate, batch)

    def test_bold_decimal_at_start_remains_part_of_context(self):
        stem = '3.14 is an approximation.'
        candidate, report = underline.restore({'items': [{'stem': stem}]},
                                              '**3.14** is an <u>approximation</u>.')
        self.assertTrue(report['passed'], report)
        self.assertEqual(candidate['items'][0]['stem'], '3.14 is an <u>approximation</u>.')

    def test_native_docx_bold_number_and_underlines_survive_separate_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper, base, final = root / 'source.docx', root / 'base.json', root / 'final.json'
            docx(paper, ['**1.**I am <u>fine</u>.', '**(　)3.**读音与hell<u>o</u>相同的是______。'])
            data = {'items': [{'number': 1, 'stem': 'I am <u>fine</u>.'},
                              {'number': 3, 'stem': '读音与hello相同的是______。'}]}
            base.write_text(json.dumps(data), encoding='utf-8')
            paper_before, base_before = paper.read_bytes(), base.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()) as output:
                code = underline.main(['--batch', str(base), '--docx', str(paper),
                                       '--out', str(final), '--apply'])
            report = json.loads(output.getvalue())
            self.assertEqual(code, 0, report)
            self.assertEqual([change['field'] for change in report['changes']], ['items/1/stem'])
            expected = {'items': [data['items'][0],
                                  {'number': 3, 'stem': '读音与hell<u>o</u>相同的是______。'}]}
            self.assertEqual(json.loads(final.read_text()), expected)
            self.assertEqual(base.read_bytes(), base_before)
            self.assertEqual(paper.read_bytes(), paper_before)

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

    def test_whole_line_label_inside_merged_passage_is_restored(self):
        # A table cell such as "Testing Dates:" is its own paragraph in the
        # source, so context == marked text. The assembled material merges
        # those cells into one passage field; the label must still be found.
        batch = {'items': [{'material': 'School Policy Testing Dates: June 18 Homework: every day'}]}
        candidate, report = underline.restore(batch, 'School Policy\n<u>Testing Dates:</u>\nJune 18\n<u>Homework:</u>\nevery day')
        self.assertTrue(report['passed'], report)
        self.assertEqual(candidate['items'][0]['material'],
                         'School Policy <u>Testing Dates:</u> June 18 <u>Homework:</u> every day')

    def test_option_underlines_do_not_compete_with_article_substrings(self):
        batch = {'items': [{'subquestions': [{'options': ['A. w<u>e</u>', 'B. <u>o</u>ld', 'C. p<u>u</u>t']}]},
                           {'material': 'The weather is fine. He is 3 years old. What is a computer?'}]}
        candidate, report = underline.restore(batch, '1.A.w<u>e</u> B.<u>o</u>ld C.p<u>u</u>t')
        self.assertTrue(report['passed'], report)
        self.assertEqual(candidate, batch)
        self.assertEqual(report['changes'], [])
        duplicate = {'items': [{'options': ['A. we']}, {'options': ['A. we']}]}
        _, report = underline.restore(duplicate, '1.A.w<u>e</u>')
        self.assertFalse(report['passed'])
        self.assertIn('UNDERLINE_MAPPING_AMBIGUOUS', {i['code'] for i in report['issues']})

    def test_whole_line_label_must_stay_unique_across_units(self):
        batch = {'items': [{'material': 'Notice <u>Homework:</u> first'},
                           {'material': 'Notice Homework: second'}]}
        candidate, report = underline.restore(batch, '<u>Homework:</u>')
        self.assertFalse(report['passed'], report)
        self.assertEqual(report['issues'][0]['code'], 'UNDERLINE_MAPPING_AMBIGUOUS')
        self.assertEqual(candidate, batch)

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

    def test_cross_unit_quote_is_owned_by_the_exact_paragraph_field(self):
        # A 熟词生义 item repeats a cloze sentence verbatim. Only the unit whose
        # own field is exactly the marked source paragraph owns the underline;
        # the copy inside the longer passage stays unmarked.
        quoted = 'Then they __3__ the students 40 seconds to memorize them.'
        source = ('Do you find it hard to memorize words?\n'
                  'Some Canadian scientists did the study.Then they __<u>3</u>__ the students '
                  '40 seconds to memorize them.\n'
                  'Then they __<u>3</u>__ the students 40 <u>seconds</u> to memorize them.')
        batch = {'items': [{'material': 'Do you find it hard to memorize words?\n'
                                        'Some Canadian scientists did the study.Then they __3__ '
                                        'the students 40 seconds to memorize them.'},
                           {'stem': quoted}]}
        candidate, report = underline.restore(batch, source)
        self.assertTrue(report['passed'], report)
        self.assertEqual([change['field'] for change in report['changes']], ['items/1/stem'])
        self.assertEqual(candidate['items'][1]['stem'],
                         'Then they __3__ the students 40 <u>seconds</u> to memorize them.')
        self.assertNotIn('<u>', candidate['items'][0]['material'])
        again, report = underline.restore(candidate, source)
        self.assertTrue(report['passed'], report)
        self.assertEqual(report['changes'], [])
        self.assertEqual(again, candidate)

    def test_cross_unit_quote_already_marked_is_idempotent(self):
        source = ('Do you find it hard to memorize words?\n'
                  'Then they __<u>3</u>__ the students 40 <u>seconds</u> to memorize them.')
        batch = {'items': [{'material': 'Do you find it hard to memorize words?\n'
                                        'Then they __3__ the students 40 seconds to memorize them.'},
                           {'stem': 'Then they __3__ the students 40 <u>seconds</u> to memorize them.'}]}
        candidate, report = underline.restore(batch, source)
        self.assertTrue(report['passed'], report)
        self.assertEqual(report['changes'], [])
        self.assertEqual(candidate, batch)

    def test_two_exact_paragraph_fields_stay_ambiguous(self):
        source = 'Then they __<u>3</u>__ the students 40 <u>seconds</u> to memorize them.'
        stem = 'Then they __3__ the students 40 seconds to memorize them.'
        batch = {'items': [{'stem': stem}, {'stem': stem}]}
        candidate, report = underline.restore(batch, source)
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

    def test_promoted_img_marker_in_table_cell_restores_across_material_and_blocks(self):
        source = (
            '[IMG image41.jpg]<br>Dear Dave,<br>Most boys catch up around __<u>▲</u>__.<br>'
            'Why don’t you tell your sister you don’t like her <u>nickname</u> for you?<br>Yours, Aunt Ann'
        )
        cell_text = (
            '\nDear Dave,\nMost boys catch up around __<u>▲</u>__.\n'
            'Why don’t you tell your sister you don’t like her nickname for you?\nYours, Aunt Ann'
        )
        batch = {
            'items': [{
                'unit_id': 'u48',
                'material': '[TABLE 1]\nDear Aunt Ann,\nHello.\n' + cell_text + '\n[/TABLE]',
                'materialBlocks': [{
                    'type': 'table',
                    'rows': [
                        {'cells': [{'text': 'Dear Aunt Ann,\nHello.'}]},
                        {'cells': [{'text': cell_text}]},
                    ],
                }],
                'attachments': [{'kind': 'image', 'name': 'image41.jpg',
                                 'url': '01_source/media/image41.jpg', 'placement': 'question'}],
            }]
        }
        candidate, report = underline.restore(batch, source)
        self.assertTrue(report['passed'], report)
        self.assertEqual(
            {change['field'] for change in report['changes']},
            {'items/0/material', 'items/0/materialBlocks/0/rows/1/cells/0/text'},
        )
        self.assertIn('her <u>nickname</u> for you?', candidate['items'][0]['material'])
        self.assertIn('her <u>nickname</u> for you?',
                      candidate['items'][0]['materialBlocks'][0]['rows'][1]['cells'][0]['text'])
        again, report2 = underline.restore(candidate, source)
        self.assertTrue(report2['passed'], report2)
        self.assertEqual(report2['changes'], [])
        self.assertEqual(again, candidate)

        # 反向防线：正文文字或非 IMG 方括号标记改动仍必须报 UNDERLINE_MAPPING_MISSING
        edited = {'items': [{'material': cell_text.replace('Dear Dave', 'Dear Mike')}]}
        _, bad = underline.restore(edited, source)
        self.assertFalse(bad['passed'])
        self.assertEqual([i['code'] for i in bad['issues']], ['UNDERLINE_MAPPING_MISSING'])

        marked_source = '[新考法·词义猜测]Why don’t you like her <u>nickname</u>?'
        missing_mark = {'items': [{'stem': 'Why don’t you like her nickname?'}]}
        _, bad_mark = underline.restore(missing_mark, marked_source)
        self.assertFalse(bad_mark['passed'])
        self.assertEqual([i['code'] for i in bad_mark['issues']], ['UNDERLINE_MAPPING_MISSING'])

    def test_underline_wrapping_only_img_marker_is_ignored(self):
        batch = {'items': [{'stem': 'Read the passage and answer.'}]}
        candidate, report = underline.restore(batch, '<u>[IMG icon.png]</u>Read the passage and answer.')
        self.assertTrue(report['passed'], report)
        self.assertEqual(report['changes'], [])
        self.assertEqual(candidate, batch)


if __name__ == '__main__':
    unittest.main()
