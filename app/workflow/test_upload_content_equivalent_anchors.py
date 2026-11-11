"""Content-equivalent legacy anchors retain real source ownership (no model)."""
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
from workflow.upload_content import collect_source_index, inspect_content


class EquivalentAnchorsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        (self.batch / '01_source').mkdir()
        self.body = 'Ⅰ.单项填空\n31. First question?\n32. Second question?\n'
        self.original = '考查不定代词。故选C项。'
        self.unit = {'unit_id': 'q-31', 'numbers': [31], 'unit_kind': 'standalone', 'type': '单项填空'}
        self.write(self.duplicate())

    def duplicate(self, second=None):
        return ('Ⅰ.单项填空\n31.C 【解析】' + self.original + '\n32.A\n31.C 【解析】'
                + (self.original if second is None else second) + '\n')

    def write(self, appendix, units=None):
        (self.batch / '01_source/正文.txt').write_text(self.body, encoding='utf-8')
        (self.batch / '01_source/附录.txt').write_text(appendix, encoding='utf-8')
        (self.batch / '01_source/structure.confirmed.json').write_text(
            json.dumps({'units': units or [self.unit]}, ensure_ascii=False), encoding='utf-8')

    def payload(self, explanation=None):
        return {'items': [{'unit_id': 'q-31', 'numbers': [31],
                           'explanation': self.original if explanation is None else explanation,
                           'articleSummary': '无', 'subquestions': [], 'evidence': ['附录真实原文']} ]}

    def ref(self, line, path='01_source/附录.txt', **columns):
        return {'source_ref': {'path': path, 'sha256': hashlib.sha256((self.batch / path).read_bytes()).hexdigest(),
                               'start_line': line, 'end_line': line, **columns}}

    def validate(self, payload):
        for row in payload['items']:
            row.pop('numbers', None)
        return validate(payload, [self.unit], {'q-31': {'type': '单项填空'}}, self.batch)

    def test_identical_repeats_pass_string_and_each_real_reference(self):
        self.assertTrue(inspect_content(self.batch, self.payload(), strict=True)['passed'])
        for line in (2, 4):
            with self.subTest(line=line):
                self.assertEqual(self.validate(self.payload(self.ref(line)))['q-31']['explanation'], self.original)

    def test_valid_section_candidate_is_not_vetoed_by_equal_wrong_section_copy(self):
        self.body = 'Ⅰ.信息转换\n30. Other?\nⅡ.单项填空\n31. First?\n'
        self.write('Ⅰ.信息转换\n30.word\nⅡ.单项填空\n31.C 【解析】' + self.original
                   + '\nⅠ.信息转换\n30.word\n31.C 【解析】' + self.original + '\n')
        self.assertEqual(self.validate(self.payload(self.ref(4)))['q-31']['explanation'], self.original)
        self.assertEqual(collect_source_index(self.batch)[0][31], self.original)
        with self.assertRaisesRegex(ValueError, 'source_ref_ownership_mismatch'):
            self.validate(self.payload(self.ref(7)))
        self.write('Ⅰ.信息转换\n30.word\nⅡ.单项填空\n31.C 【解析】' + self.original
                   + '\nⅠ.信息转换\n30.word\n31.C 【解析】异文。\n')
        self.assertFalse(inspect_content(self.batch, self.payload(), strict=True)['passed'])

    def test_compact_answer_range_does_not_enter_duplicate_explanation(self):
        self.write(self.duplicate() + '32~33 AB\n【解析】不能跨范围借31号。\n')
        rows = inspect_content(self.batch, self.payload(), strict=True)
        self.assertTrue(rows['passed'], rows['errors'])
        self.assertEqual([(r['line'], r['end_line']) for r in rows['all_questions']], [(2, 2), (4, 4)])
        with patch('workflow.explanations_direct._units', return_value=[self.unit]):
            payload = build_payload(self.batch)
        self.assertEqual(self.validate(payload)['q-31']['explanation'], self.original)
        self.write(self.duplicate() + '31~32 AB\n【解析】不能成为31号解析。\n')
        self.assertEqual(len(inspect_content(self.batch, self.payload(), strict=True)['all_questions']), 2)

    def test_repeated_stray_labels_bind_their_real_numbered_anchors(self):
        self.write('Ⅰ.单项填空\n31.C\n【解析】' + self.original + '\n31.C\n【解析】' + self.original + '\n')
        for line in (3, 5):
            with self.subTest(line=line):
                self.assertEqual(self.validate(self.payload(self.ref(line)))['q-31']['explanation'], self.original)

    def test_different_originals_have_structured_nonretryable_conflict(self):
        self.write(self.duplicate('不同解析。'))
        report = inspect_content(self.batch, self.payload(), strict=True)
        error = next(row for row in report['errors'] if row['code'] == 'EXPLANATION_SOURCE_UNVERIFIED')
        self.assertIs(error['retryable'], False)
        self.assertEqual(error['failure_class'], 'source_binding')
        detail = error['evidence'][0]
        self.assertEqual(detail['candidate_count'], 2)
        self.assertEqual({row['line'] for row in detail['candidates']}, {2, 4})
        self.assertTrue(all(row['anchor_count'] == 2 for row in detail['candidates']))
        self.assertIn('conflicting_original_content', detail['reasons'])
        self.assertNotIn(31, collect_source_index(self.batch)[0])

    def test_actual_punctuation_difference_is_still_a_source_conflict(self):
        self.write(self.duplicate(self.original.replace('。', '．')))
        self.assertFalse(inspect_content(self.batch, self.payload(), strict=True)['passed'])
        self.assertNotIn(31, collect_source_index(self.batch)[0])

    def test_correct_reference_with_source_conflict_is_nonretryable(self):
        self.write(self.duplicate('不同解析。'))
        with self.assertRaises(ValueError) as caught:
            self.validate(self.payload(self.ref(2)))
        rows = json.loads(str(caught.exception).split('Content source/ownership check failed: ', 1)[1])
        error = next(row for row in rows if row['code'] == 'EXPLANATION_SOURCE_UNVERIFIED')
        self.assertIs(error['retryable'], False)
        self.assertEqual(error['failure_class'], 'source_binding')

    def test_wrong_model_text_with_source_conflict_remains_retryable(self):
        self.write(self.duplicate('不同解析。'))
        report = inspect_content(self.batch, self.payload('模型错误内容。'), strict=True)
        error = next(row for row in report['errors'] if row['code'] == 'EXPLANATION_SOURCE_UNVERIFIED')
        self.assertNotEqual(error.get('retryable'), False)
        self.assertTrue(error['evidence'][0]['candidates'])

    def test_wrong_number_reference_rejected_even_for_identical_text(self):
        self.write(self.duplicate() + '32.A 【解析】' + self.original + '\n')
        with self.assertRaisesRegex(ValueError, 'source_ref_ownership_mismatch'):
            self.validate(self.payload(self.ref(5)))

    def test_raw_reference_columns_do_not_shift_across_question_ownership(self):
        for prefix in ('    ', '**    '):
            with self.subTest(prefix=prefix):
                raw = prefix + '32.A 【解析】' + self.original
                self.write(self.duplicate() + raw + '\n')
                ref = self.ref(5, start_column=raw.index(self.original), end_column=len(raw))
                with self.assertRaisesRegex(ValueError, 'source_ref_ownership_mismatch'):
                    self.validate(self.payload(ref))
        raw = '    31.C 【解析】' + self.original
        self.write('Ⅰ.单项填空\n' + raw + '\n')
        self.assertEqual(self.validate(self.payload(self.ref(2, start_column=raw.index(self.original),
                                                          end_column=len(raw))))['q-31']['explanation'], self.original)

    def test_raw_columns_keep_distinct_parent_field_references_valid(self):
        self.unit.update(type='阅读理解')
        self.body = 'Ⅰ.阅读理解\n31. First?\n'
        summary = '本文介绍学校生活。'
        summary_raw = '    【文章大意】' + summary
        explanation_raw = '**    31.C 【解析】' + self.original + '**'
        self.write('Ⅰ.阅读理解\n' + summary_raw + '\n' + explanation_raw + '\n')
        payload = self.payload(self.ref(3, start_column=explanation_raw.index(self.original),
                                       end_column=len(explanation_raw) - 2))
        payload['items'][0]['articleSummary'] = self.ref(2, start_column=summary_raw.index(summary))
        result = self.validate(payload)['q-31']
        self.assertEqual(result['explanation'], self.original)
        self.assertEqual(result['articleSummary'], summary)

    def test_wrong_coordinates_and_hash_never_borrow_good_duplicate(self):
        self.write(self.duplicate() + self.original + '\n')
        for ref in (self.ref(5), self.ref(2, start_column=10, end_column=12)):
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                self.validate(self.payload(ref))
        ref = self.ref(2)
        ref['source_ref']['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'stale or forged'):
            self.validate(self.payload(ref))

    def test_wrong_reference_with_source_conflict_remains_retryable(self):
        self.write(self.duplicate('不同解析。') + '32.A 【解析】' + self.original + '\n')
        with self.assertRaises(ValueError) as caught:
            self.validate(self.payload(self.ref(5)))
        rows = json.loads(str(caught.exception).split('Content source/ownership check failed: ', 1)[1])
        error = next(row for row in rows if row['code'] == 'EXPLANATION_SOURCE_UNVERIFIED')
        self.assertNotEqual(error.get('retryable'), False)
        self.assertIn('source_ref_ownership_mismatch', error['evidence'][0]['reasons'])

    def test_reference_only_source_neither_competes_nor_supplies(self):
        path = '01_source_ans/正文.txt'
        (self.batch / '01_source_ans').mkdir()
        (self.batch / path).write_text('Ⅰ.单项填空\n31.C 【解析】参考件异文。\n', encoding='utf-8')
        (self.batch / '01_source/material-roles.json').write_text(json.dumps({'documents': [
            {'text_path': path, 'content_authority': False}]}), encoding='utf-8')
        self.assertTrue(inspect_content(self.batch, self.payload(), strict=True)['passed'])
        with self.assertRaises(ValueError):
            self.validate(self.payload(self.ref(2, path)))
        self.assertEqual(collect_source_index(self.batch)[0][31], self.original)

    def test_different_authoritative_source_still_conflicts(self):
        (self.batch / '01_source_ans').mkdir()
        (self.batch / '01_source_ans/正文.txt').write_text('Ⅰ.单项填空\n31.C 【解析】另一来源异文。\n', encoding='utf-8')
        self.assertFalse(inspect_content(self.batch, self.payload(), strict=True)['passed'])
        self.assertNotIn(31, collect_source_index(self.batch)[0])

    def test_bare_duplicate_anchor_does_not_count_as_equal_explanation(self):
        self.write('Ⅰ.单项填空\n31.C 【解析】' + self.original + '\n31.C\n')
        report = inspect_content(self.batch, self.payload(), strict=True)
        self.assertFalse(report['passed'])
        self.assertIn('ambiguous_repeated_anchors', report['errors'][0]['evidence'][0]['reasons'])
        self.assertNotIn(31, collect_source_index(self.batch)[0])

    def test_index_direct_and_final_consumer_agree_on_identical_content(self):
        self.assertEqual(collect_source_index(self.batch)[0][31], self.original)
        with patch('workflow.explanations_direct._units', return_value=[self.unit]):
            payload = build_payload(self.batch)
        self.assertEqual(payload['items'][0]['explanation'], self.original)
        self.assertEqual(self.validate(copy.deepcopy(payload))['q-31']['explanation'], self.original)
        self.write(self.duplicate('不同解析。'))
        with patch('workflow.explanations_direct._units', return_value=[self.unit]):
            self.assertIsNone(build_payload(self.batch))

    def test_scoped_identical_text_cannot_cross_scopes_or_duplicate_window(self):
        self.body = 'Unit 1\nⅠ.单项填空\n1. First?\nⅡ.单项填空\n1. Second?\n'
        units = assign_scoped([
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '单项填空', 'source_span': {'start_line': 3, 'end_line': 3}},
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '单项填空', 'source_span': {'start_line': 5, 'end_line': 5}},
        ], self.body)
        same = '1.C 【解析】' + self.original + '\n'
        self.write('Unit 1\nⅠ.单项填空\n' + same + 'Ⅱ.单项填空\n' + same, units)
        payload = {'items': [dict(self.payload()['items'][0], unit_id=u['unit_id'], numbers=[1]) for u in units]}
        typed = {u['unit_id']: {'type': '单项填空'} for u in units}
        for row in payload['items']:
            row.pop('numbers')
        self.assertEqual(len(validate(copy.deepcopy(payload), units, typed, self.batch)), 2)
        payload['items'][0]['explanation'] = self.ref(5)
        with self.assertRaisesRegex(ValueError, 'source scope'):
            validate(payload, units, typed, self.batch)
        self.write('Unit 1\nⅠ.单项填空\n' + same + same + 'Ⅱ.单项填空\n' + same, units)
        self.assertFalse(inspect_content(self.batch, {'items': [dict(self.payload()['items'][0],
            unit_id=units[0]['unit_id'], numbers=[1])]}, strict=True)['passed'])


if __name__ == '__main__':
    unittest.main()
