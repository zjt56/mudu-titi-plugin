"""Whole unnumbered standalone word lists retain one scoped answer, never children."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import agent_tasks, answers_direct, upload_content
from workflow.question_identity import assign_scoped, question_key


class ScopedWholeWordAnswersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        (self.batch / '01_source').mkdir()
        self.body = (
            'Starter Unit 3 Welcome!\n第四课时 Project\n'
            'Ⅰ.写出下列古诗画线句中表示数字的英文单词\n'
            '[TABLE 1]\n古诗画线文字\n[/TABLE]\n<u>　</u>\n'
            'Ⅱ.用方框中所给单词的适当形式填空\n'
            'red blue green black white yellow\n'
            + ''.join(f'{n}.A numbered blank <u>　</u>.\n' for n in range(1, 7))
            + 'Ⅲ.根据汉语意思完成句子,每空一词\n'
            + ''.join(f'{n}.Complete this sentence <u>　</u>.\n' for n in range(1, 6))
            + '任务型阅读\nRead this passage.\n'
            + ''.join(f'{n}.Read a numbered blank <u>　</u>.\n' for n in range(1, 11))
        )
        # Deliberately differs from the reported paper: no trial-answer special case.
        self.literal = 'alpha,beta,gamma,delta'
        self.appendix = (
            '参考答案\n'
            '[IMG first.jpeg]重要考点集训[IMG last.jpeg]\n'
            f'Ⅰ.{self.literal}\n'
            'Ⅱ.1.red　2.blue　3.green　4.black　5.white　6.yellow\n'
            'Ⅲ.1.first pair　2.second pair　3.third pair　4.fourth pair　5.fifth pair\n'
            '[IMG first.jpeg]创新阅读专练[IMG last.jpeg]\n'
            '1~5 CEDBF\n6.six　7.seven　8.eight　9.nine　10.ten\n'
        )
        rows = self.body.splitlines()
        first, second, third, last = (rows.index(title) + 1 for title in (
            'Ⅰ.写出下列古诗画线句中表示数字的英文单词',
            'Ⅱ.用方框中所给单词的适当形式填空',
            'Ⅲ.根据汉语意思完成句子,每空一词', '任务型阅读'))
        raw = [{'unit_kind': 'standalone', 'type': '单词拼写', 'numbers': [1],
                'source_span': {'start_line': first + 1, 'end_line': second - 1,
                                'basis': 'cleaned_source',
                                'number_binding': 'unnumbered_section', 'printed_numbers': []}},
               {'unit_kind': 'group', 'type': '选词填空', 'numbers': list(range(1, 7)),
                'source_span': {'start_line': second + 1, 'end_line': third - 1,
                                'basis': 'cleaned_source'}}]
        raw.extend({'unit_kind': 'standalone', 'type': '完成句子', 'numbers': [n],
                    'source_span': {'start_line': third + n, 'end_line': third + n,
                                    'basis': 'cleaned_source'}} for n in range(1, 6))
        raw.append({'unit_kind': 'group', 'type': '任务型阅读', 'numbers': list(range(1, 11)),
                    'source_span': {'start_line': last + 1, 'end_line': len(rows),
                                    'basis': 'cleaned_source'}})
        self.units = assign_scoped(raw, self.body)
        self.write_sources()

    def write_sources(self):
        (self.batch / '01_source/正文.txt').write_text(self.body, encoding='utf-8')
        (self.batch / '01_source/附录.txt').write_text(self.appendix, encoding='utf-8')
        (self.batch / '01_source/structure.confirmed.json').write_text(json.dumps(
            {'identity_version': 'scoped-v1', 'units': self.units}, ensure_ascii=False), encoding='utf-8')

    def test_eight_units_have_22_answers_and_literal_is_one_verbatim_answer(self):
        before = {p: p.read_bytes() for p in self.batch.rglob('*') if p.is_file()}
        with patch('workflow.local_agent.invoke', side_effect=AssertionError('model called')):
            payload = answers_direct.build_payload(self.batch)
            self.assertIsNotNone(payload)
            self.assertEqual(len(self.units), 8)
            self.assertEqual(len(payload['answers']), 22)
            key = question_key(self.units[0], 1)
            self.assertEqual(payload['answers'][key], self.literal)
            self.assertEqual(payload['evidence'][key], ['01_source/附录.txt:3'])
            self.assertEqual(payload['missing'], {})
            agent_tasks._validate(self.batch, 'answers', payload,
                                  {'01_source/附录.txt': self.appendix}, self.units)
        self.assertEqual({p: p.read_bytes() for p in self.batch.rglob('*') if p.is_file()}, before)
        expected = ['red', 'blue', 'green', 'black', 'white', 'yellow',
                    'first pair', 'second pair', 'third pair', 'fourth pair', 'fifth pair',
                    'C', 'E', 'D', 'B', 'F', 'six', 'seven', 'eight', 'nine', 'ten']
        self.assertEqual([payload['answers'][question_key(u, n)]
                          for u in self.units[1:] for n in u['numbers']], expected)

    def test_explicit_whole_section_proof_is_required(self):
        original = copy.deepcopy(self.units[0])
        for field, value in [('number_binding', None), ('number_binding', 'single_writing_section'),
                             ('printed_numbers', None), ('printed_numbers', [1]),
                             ('basis', 'guessed')]:
            with self.subTest(field=field, value=value):
                unit = copy.deepcopy(original)
                if value is None:
                    unit['source_span'].pop(field)
                else:
                    unit['source_span'][field] = value
                self.assertIsNone(upload_content.resolve_scoped_answer(self.batch, unit, 1))
        unit = copy.deepcopy(original)
        unit['numbers'] = []
        self.assertIsNone(upload_content.resolve_scoped_answer(self.batch, unit, 1))

    def test_source_scope_and_coordinates_cannot_be_borrowed(self):
        unit = copy.deepcopy(self.units[0])
        unit['source_section'] = copy.deepcopy(self.units[1]['source_section'])
        self.assertIsNone(upload_content.resolve_scoped_answer(self.batch, unit, 1))
        unit = self.units[0]
        for value, citations in [(self.literal, ['01_source/附录.txt:4']),
                                 ('alpha', ['01_source/附录.txt:3']),
                                 ('beta,alpha,gamma,delta', ['01_source/附录.txt:3'])]:
            with self.subTest(value=value, citations=citations):
                with self.assertRaises(ValueError):
                    upload_content.validate_scoped_answer(self.batch, unit, 1, value, citations)

    def test_partial_or_wrong_lesson_skeleton_is_rejected(self):
        appendix = self.appendix
        for changed in [appendix.replace('Ⅱ.1.red', 'Ⅳ.1.red'),
                        appendix.replace('　6.yellow', ''),
                        '第五课时 Project\n' + appendix,
                        appendix + appendix]:
            with self.subTest(appendix=changed):
                self.appendix = changed
                self.write_sources()
                self.assertIsNone(answers_direct.build_payload(self.batch))
        self.appendix = appendix

    def test_free_form_and_bare_word_lists_are_not_whole_standalone_answers(self):
        appendix = self.appendix
        for value in ['These are some words.', 'alpha;beta;gamma', 'A;B;C', 'A,B,C']:
            with self.subTest(value=value):
                self.appendix = appendix.replace(self.literal, value)
                self.write_sources()
                self.assertIsNone(upload_content.resolve_scoped_answer(self.batch, self.units[0], 1))
        self.appendix = appendix.replace('Ⅰ.' + self.literal, self.literal)
        self.write_sources()
        self.assertIsNone(answers_direct.build_payload(self.batch))

    def test_added_printed_anchor_cannot_use_whole_task_alias(self):
        self.body = self.body.replace('古诗画线文字', '1.古诗画线文字')
        self.write_sources()
        self.assertIsNone(upload_content.resolve_scoped_answer(self.batch, self.units[0], 1))


if __name__ == '__main__':
    unittest.main()
