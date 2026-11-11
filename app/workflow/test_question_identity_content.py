"""Repeated printed numbers must retain source scope through every content path."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow.content_rules import validate
from workflow.explanations_direct import build_payload
from workflow.question_identity import assign_scoped
from workflow.upload_content import (inspect_content, validate_scoped_answer, validate_scoped_missing,
                                     collect_source_index, scoped_source_matches)


class ScopedContentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        (self.batch / '01_source').mkdir()
        self.body = 'Unit 1\nⅠ.单项填空\n1. First question?\nⅡ.单项填空\n1. Second question?\n'
        self.appendix = 'Unit 1\nⅠ.单项填空\n1.A 【解析】第一大题解析。\nⅡ.单项填空\n1.B 【解析】第二大题解析。\n'
        self.units = assign_scoped([
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '单项填空', 'source_span': {'start_line': 3, 'end_line': 3}},
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '单项填空', 'source_span': {'start_line': 5, 'end_line': 5}},
        ], self.body)
        self.typed = {u['unit_id']: {'type': '单项填空'} for u in self.units}
        self.write_sources()

    def write_sources(self):
        (self.batch / '01_source/正文.txt').write_text(self.body, encoding='utf-8')
        (self.batch / '01_source/附录.txt').write_text(self.appendix, encoding='utf-8')
        (self.batch / '01_source/structure.confirmed.json').write_text(json.dumps({'units': self.units}), encoding='utf-8')

    def payload(self, texts=('第一大题解析。', '第二大题解析。')):
        return {'items': [{'unit_id': u['unit_id'], 'explanation': text, 'articleSummary': '无',
                           'subquestions': [], 'evidence': ['附录原文']} for u, text in zip(self.units, texts)]}

    def ref(self, line):
        path = self.batch / '01_source/附录.txt'
        return {'source_ref': {'path': '01_source/附录.txt', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                               'start_line': line, 'end_line': line}}

    def test_same_one_different_explanations_are_bound_by_scope(self):
        rows = validate(self.payload(), self.units, self.typed, self.batch)
        self.assertEqual([rows[u['unit_id']]['explanation'] for u in self.units], ['第一大题解析。', '第二大题解析。'])
        with self.assertRaisesRegex(ValueError, 'EXPLANATION_SOURCE_UNVERIFIED'):
            validate(self.payload(('第二大题解析。', '第一大题解析。')), self.units, self.typed, self.batch)

    def test_wrong_scope_reference_rejected_even_when_text_is_identical(self):
        self.appendix = self.appendix.replace('第二大题解析。', '第一大题解析。')
        self.write_sources()
        payload = self.payload(('第一大题解析。', '第一大题解析。'))
        payload['items'][0]['explanation'] = self.ref(5)
        with self.assertRaisesRegex(ValueError, 'source_ref.*source scope'):
            validate(payload, self.units, self.typed, self.batch)

    def test_correct_source_references_keep_printed_numbers(self):
        payload = self.payload()
        for item, line in zip(payload['items'], (3, 5)):
            item['explanation'] = self.ref(line)
        rows = validate(payload, self.units, self.typed, self.batch)
        self.assertEqual([rows[u['unit_id']]['numbers'] for u in self.units], [[1], [1]])

    def test_numeric_appendix_without_scope_cannot_claim_binding_or_absence(self):
        self.appendix = '1.A 【解析】第一大题解析。\n1.B 【解析】第二大题解析。\n'
        self.write_sources()
        with self.assertRaisesRegex(ValueError, 'CONTENT_SCOPE_UNBOUND'):
            validate(self.payload(('无', '无')), self.units, self.typed, self.batch)

    def test_scoped_fast_path_declines_instead_of_number_index(self):
        with patch('workflow.explanations_direct._units', return_value=self.units):
            self.assertIsNone(build_payload(self.batch))

    def test_answer_evidence_requires_scope_and_real_coordinates(self):
        validate_scoped_answer(self.batch, self.units[0], 1, 'A', ['01_source/附录.txt:3'])
        validate_scoped_answer(self.batch, self.units[1], 1, 'B', ['附录 L5'])
        for unit, answer, evidence in ((self.units[0], 'B', ['附录 L5']),
                                       (self.units[1], 'B', ['附录 L3']),
                                       (self.units[0], 'A', ['单项填空第一大题'])):
            with self.subTest(answer=answer, evidence=evidence), self.assertRaisesRegex(ValueError, 'scoped answer source'):
                validate_scoped_answer(self.batch, unit, 1, answer, evidence)

    def test_scoped_source_index_uses_qids(self):
        index, _ = collect_source_index(self.batch)
        self.assertEqual(index, {self.units[0]['question_ids']['1']: '第一大题解析。',
                                 self.units[1]['question_ids']['1']: '第二大题解析。'})

    def test_unscoped_numeric_answer_appendix_blocks_missing_binding(self):
        self.appendix = '1.A\n1.B\n'
        self.write_sources()
        with self.assertRaisesRegex(ValueError, 'answer scope unbound'):
            validate_scoped_missing(self.batch, self.units[0], 1)

    def test_missing_rejects_only_provable_original_answer(self):
        with self.assertRaisesRegex(ValueError, 'missing contradicts'):
            validate_scoped_missing(self.batch, self.units[0], 1)
        self.appendix = self.appendix.replace('1.A 【解析】第一大题解析。', '1.无答案')
        self.write_sources()
        validate_scoped_missing(self.batch, self.units[0], 1)
        with self.assertRaisesRegex(ValueError, 'missing contradicts'):
            validate_scoped_missing(self.batch, self.units[1], 1)

    def test_pdf_fills_only_same_scope_word_gap(self):
        materials = self.batch / '01_source/materials'
        materials.mkdir()
        (materials / 'attachment_001.txt').write_text(self.appendix.replace('第一大题解析。', 'PDF第一解析。'), encoding='utf-8')
        validate(self.payload(), self.units, self.typed, self.batch)
        with self.assertRaisesRegex(ValueError, 'EXPLANATION_SOURCE_UNVERIFIED'):
            validate(self.payload(('PDF第一解析。', '第二大题解析。')), self.units, self.typed, self.batch)
        self.appendix = self.appendix.replace('1.A 【解析】第一大题解析。', '1.A')
        self.write_sources()
        validate(self.payload(('PDF第一解析。', '第二大题解析。')), self.units, self.typed, self.batch)

    def test_answer_word_priority_cannot_be_bypassed_by_matching_pdf(self):
        materials = self.batch / '01_source/materials'
        materials.mkdir()
        (materials / 'attachment_001.txt').write_text(self.appendix.replace('1.A', '1.C'), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'scoped answer source'):
            validate_scoped_answer(self.batch, self.units[0], 1, 'C', ['01_source/materials/attachment_001.txt:3'])

    def test_same_number_group_summaries_keep_scope(self):
        self.body = 'Unit 1\nⅠ.阅读理解\nA\n1. First?\n2. Second?\nⅡ.阅读理解\nA\n1. Third?\n2. Fourth?\n'
        self.appendix = 'Unit 1\nⅠ.阅读理解\nA\n【文章大意】第一篇大意。\n1.A 【解析】甲解析。\n2.B 【解析】乙解析。\nⅡ.阅读理解\nA\n【文章大意】第二篇大意。\n1.C 【解析】丙解析。\n2.D 【解析】丁解析。\n'
        self.units = assign_scoped([
            {'numbers': [1, 2], 'unit_kind': 'group', 'type': '阅读理解', 'source_span': {'start_line': 3, 'end_line': 5}},
            {'numbers': [1, 2], 'unit_kind': 'group', 'type': '阅读理解', 'source_span': {'start_line': 7, 'end_line': 9}},
        ], self.body)
        self.typed = {u['unit_id']: {'type': '阅读理解'} for u in self.units}
        self.write_sources()
        payload = {'items': [{'unit_id': u['unit_id'], 'explanation': '无', 'articleSummary': summary,
                              'subquestions': [{'number': n, 'explanation': text} for n, text in zip((1, 2), texts)],
                              'evidence': ['附录']} for u, summary, texts in zip(self.units, ('第一篇大意。', '第二篇大意。'),
                                                                                 (('甲解析。', '乙解析。'), ('丙解析。', '丁解析。')))]}
        validate(copy.deepcopy(payload), self.units, self.typed, self.batch)
        payload['items'][0]['articleSummary'] = '第二篇大意。'
        with self.assertRaisesRegex(ValueError, 'SUMMARY_SOURCE_UNVERIFIED'):
            validate(payload, self.units, self.typed, self.batch)

    def test_inline_dialogue_numbers_bind_real_companion_explanations(self):
        self.body = 'Unit 1\nⅠ.补全对话\nC:1. ____ D:2. ____\n'
        self.appendix = 'Unit 1\nⅠ.补全对话\n1.A 【解析】第一空解析。\n2.B 【解析】第二空解析。\n'
        self.units = assign_scoped([{'numbers': [1, 2], 'unit_kind': 'group', 'type': '补全对话',
                                    'source_span': {'start_line': 3, 'end_line': 3}}], self.body)
        self.typed = {u['unit_id']: {'type': '补全对话'} for u in self.units}
        self.write_sources()
        payload = {'items': [{'unit_id': self.units[0]['unit_id'], 'explanation': '无', 'articleSummary': '无',
                              'subquestions': [{'number': 1, 'explanation': '第一空解析。'},
                                               {'number': 2, 'explanation': '第二空解析。'}], 'evidence': ['附录']}]}
        validate(payload, self.units, self.typed, self.batch)
        validate_scoped_answer(self.batch, self.units[0], 2, 'B', ['附录 L4'])

    def test_unnumbered_image_singleton_alias_binds_only_its_real_section(self):
        self.body = 'Unit 1\nⅠ.看图题\n[IMG pic.png]\nⅡ.看图题\n[IMG other.png]\n'
        self.appendix = 'Unit 1\nⅠ.看图题\n1.A 【解析】图片解析。\nⅡ.看图题\n1.B 【解析】另一图片解析。\n'
        self.units = assign_scoped([
            {'numbers': [1], 'unit_kind': 'group', 'type': '看图题',
             'source_span': {'start_line': line, 'end_line': line, 'number_binding': 'unnumbered_section'}}
            for line in (3, 5)], self.body)
        self.typed = {u['unit_id']: {'type': '看图题'} for u in self.units}
        self.write_sources()
        payload = {'items': [{'unit_id': u['unit_id'], 'explanation': '无', 'articleSummary': '无',
                              'subquestions': [{'number': 1, 'explanation': text}], 'evidence': ['附录']}
                             for u, text in zip(self.units, ('图片解析。', '另一图片解析。'))]}
        validate(copy.deepcopy(payload), self.units, self.typed, self.batch)
        validate_scoped_answer(self.batch, self.units[0], 1, 'A', ['附录 L3'])
        payload['items'][0]['subquestions'][0]['explanation'] = self.ref(5)
        with self.assertRaisesRegex(ValueError, 'source_ref.*source scope'):
            validate(payload, self.units, self.typed, self.batch)
        lines = {'01_source/正文.txt': self.body.splitlines(), '01_source/附录.txt': self.appendix.splitlines()}
        self.assertFalse(scoped_source_matches(self.units[0], 2, '01_source/附录.txt', 3, lines))
        invalid = copy.deepcopy(self.units[0]); invalid['source_span'].pop('number_binding')
        self.assertFalse(scoped_source_matches(invalid, 1, '01_source/附录.txt', 3, lines))

    def test_unnumbered_alias_cannot_select_one_of_multiple_image_windows(self):
        self.body = 'Unit 1\nⅠ.看图题\n[IMG first.png]\n[IMG second.png]\n'
        self.appendix = 'Unit 1\nⅠ.看图题\n1.A 【解析】不明归属解析。\n'
        self.units = assign_scoped([
            {'numbers': [1], 'unit_kind': 'group', 'type': '看图题',
             'source_span': {'start_line': line, 'end_line': line, 'number_binding': 'unnumbered_section'}}
            for line in (3, 4)], self.body)
        self.write_sources()
        with self.assertRaisesRegex(ValueError, 'scoped answer source'):
            validate_scoped_answer(self.batch, self.units[0], 1, 'A', ['附录 L3'])

    def test_repeated_passage_one_keeps_exact_body_window_evidence(self):
        self.body = 'Unit 1\nⅠ.阅读理解\nA\n1.A 【解析】甲篇解析。\nB\n1.B 【解析】乙篇解析。\n'
        self.appendix = ''
        self.units = assign_scoped([
            {'numbers': [1], 'unit_kind': 'group', 'type': '阅读理解',
             'source_span': {'start_line': start, 'end_line': end}}
            for start, end in ((3, 4), (5, 6))], self.body)
        self.typed = {u['unit_id']: {'type': '阅读理解'} for u in self.units}
        self.write_sources()
        payload = {'items': [{'unit_id': u['unit_id'], 'explanation': '无', 'articleSummary': '无',
                              'subquestions': [{'number': 1, 'explanation': text}], 'evidence': ['正文']}
                             for u, text in zip(self.units, ('甲篇解析。', '乙篇解析。'))]}
        validate(payload, self.units, self.typed, self.batch)
        validate_scoped_answer(self.batch, self.units[0], 1, 'A', ['01_source/正文.txt:4'])
        with self.assertRaisesRegex(ValueError, 'scoped answer source'):
            validate_scoped_answer(self.batch, self.units[0], 1, 'B', ['01_source/正文.txt:6'])

    def test_same_section_passages_repeated_one_cannot_borrow_single_companion_row(self):
        self.body = 'Unit 1\nⅠ.阅读理解\nA\n1. First passage?\nB\n1. Second passage?\n'
        self.appendix = 'Unit 1\nⅠ.阅读理解\nB\n1.B 【解析】只属于B篇。\n'
        self.units = assign_scoped([
            {'numbers': [1], 'unit_kind': 'group', 'type': '阅读理解',
             'source_span': {'start_line': start, 'end_line': end}}
            for start, end in ((3, 4), (5, 6))], self.body)
        self.typed = {u['unit_id']: {'type': '阅读理解'} for u in self.units}
        self.write_sources()
        with self.assertRaisesRegex(ValueError, 'scoped answer source'):
            validate_scoped_answer(self.batch, self.units[0], 1, 'B', ['附录 L4'])
        payload = {'items': [{'unit_id': u['unit_id'], 'explanation': '无', 'articleSummary': '无',
                              'subquestions': [{'number': 1, 'explanation': '只属于B篇。'}], 'evidence': ['附录']}
                             for u in self.units]}
        with self.assertRaisesRegex(ValueError, 'EXPLANATION_SOURCE_UNVERIFIED|CONTENT_SCOPE_UNBOUND'):
            validate(payload, self.units, self.typed, self.batch)
        payload['items'][0]['subquestions'][0]['explanation'] = self.ref(4)
        with self.assertRaisesRegex(ValueError, 'source_ref.*source scope'):
            validate(payload, self.units, self.typed, self.batch)

    def test_legacy_unscoped_sample_keeps_existing_behavior(self):
        self.body = '1. First?\n2. Second?\n'
        self.appendix = '1.A 【解析】第一大题解析。\n2.B 【解析】第二大题解析。\n'
        self.units = [{'unit_id': 'q-1', 'numbers': [1], 'unit_kind': 'standalone'},
                      {'unit_id': 'q-2', 'numbers': [2], 'unit_kind': 'standalone'}]
        self.typed = {u['unit_id']: {'type': '单项填空'} for u in self.units}
        self.write_sources()
        self.assertEqual(len(validate(self.payload(), self.units, self.typed, self.batch)), 2)
        validate_scoped_answer(self.batch, self.units[0], 1, 'A', ['legacy evidence'])

    def test_writing_same_number_does_not_exempt_objective_scope(self):
        self.units[1]['type'] = '书面表达'
        self.write_sources()
        report = inspect_content(self.batch, self.payload(('其它内容。', '无')), strict=True)
        self.assertTrue(any(e['code'] == 'EXPLANATION_SOURCE_UNVERIFIED' and e['unit_id'] == self.units[0]['unit_id']
                            for e in report['errors']))


if __name__ == '__main__':
    unittest.main()
