"""Scoped identity invariants, independent of predicted type and answer text."""
import copy
import unittest
from .question_identity import assign_scoped, question_key, validate_units


class IdentityTests(unittest.TestCase):
    lines = ['Unit 1', '第一课时 Section A', 'Ⅰ.根据汉语提示完成句子。',
             '1. Apple is red.', '2. Pear is green.', 'Ⅱ.根据汉语提示完成句子。',
             '1. Banana is yellow.']

    def units(self):
        return [{'numbers': [1], 'unit_kind': 'standalone', 'type': '完成句子',
                 'stem': self.lines[3], 'source_span': {'start_line': 4, 'end_line': 4, 'basis': 'cleaned_source'}},
                {'numbers': [2], 'unit_kind': 'standalone', 'type': '完成句子',
                 'stem': self.lines[4], 'source_span': {'start_line': 5, 'end_line': 5, 'basis': 'cleaned_source'}},
                {'numbers': [1], 'unit_kind': 'standalone', 'type': '完成句子',
                 'stem': self.lines[6], 'source_span': {'start_line': 7, 'end_line': 7, 'basis': 'cleaned_source'}}]

    def test_original_numbers_and_roman_order_are_preserved(self):
        units = assign_scoped(self.units(), self.lines)
        self.assertEqual([u['numbers'] for u in units], [[1], [2], [1]])
        self.assertEqual([u['source_section']['ordinal'] for u in units], ['Ⅰ.', 'Ⅰ.', 'Ⅱ.'])
        self.assertEqual(units[0]['source_section']['title'], self.lines[2])
        self.assertEqual(units[0]['section_id'], units[1]['section_id'])
        self.assertNotEqual(question_key(units[0], 1), question_key(units[2], 1))
        self.assertTrue(validate_units(units))

    def test_type_answer_and_window_change_do_not_change_question_identity(self):
        before = assign_scoped(self.units(), self.lines)
        raw = self.units()
        raw[0].update(type='单词拼写', answer='different')
        raw[0]['source_span']['start_line'] = 3
        after = assign_scoped(raw, self.lines)
        self.assertEqual(before[0]['unit_id'], after[0]['unit_id'])
        self.assertEqual(before[0]['question_ids'], after[0]['question_ids'])

    def test_question_identity_survives_grouping_change(self):
        singles = assign_scoped(self.units(), self.lines)
        group = dict(self.units()[0], numbers=[1, 2], unit_kind='group',
                     source_span={'start_line': 3, 'end_line': 5, 'basis': 'cleaned_source'})
        grouped = assign_scoped([group, self.units()[2]], self.lines)
        self.assertEqual(question_key(singles[1], 2), question_key(grouped[0], 2))

    def test_existing_explicit_identity_survives_reassignment(self):
        units = assign_scoped(self.units(), self.lines)
        units[0]['unit_id'] = 'persisted-unit'
        units[0]['question_ids']['1'] = 'persisted-question'
        self.assertEqual(assign_scoped(units, self.lines)[0]['unit_id'], 'persisted-unit')
        self.assertEqual(question_key(assign_scoped(units, self.lines)[0], 1), 'persisted-question')

    def test_partial_or_duplicate_identity_fails_closed(self):
        units = assign_scoped(self.units(), self.lines)
        broken = copy.deepcopy(units)
        broken[0]['question_ids'] = {}
        self.assertFalse(validate_units(broken))
        with self.assertRaises(ValueError):
            question_key(broken[0], 1)
        broken = copy.deepcopy(units)
        broken[2]['question_ids']['1'] = broken[0]['question_ids']['1']
        self.assertFalse(validate_units(broken))
        self.assertEqual(question_key({'numbers': [1]}, 1), '1')


if __name__ == '__main__':
    unittest.main()
