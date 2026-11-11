"""Narrow D11/W11 coverage through the real assemble precheck subprocess."""
import copy
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import assemble_precheck


ROOT = Path(__file__).resolve().parents[1]


class AssembleFormatWarningTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        module = runpy.run_path(str(ROOT / 'scripts/06-upload/audit_underlines.py'))
        cls.check_unit = staticmethod(module['_check_unit'])

    def unit(self, **changes):
        return {'unit_id': 'q-1', 'question_type': '单项填空',
                'stem': 'What colour is the bag?', 'answer': 'A', **changes}

    def precheck(self, unit):
        payload = {'items': [unit]}
        before = copy.deepcopy(payload)
        # Only isolate the script list; execution, exit classification and report
        # formatting are real. No source or live batch is needed by this script.
        with patch.object(assemble_precheck, 'SCRIPTS', ('audit_underlines.py',)):
            result = assemble_precheck.precheck(payload)
        self.assertEqual(payload, before)
        return result

    def test_original_question_without_blank_is_warning_and_passes(self):
        unit = self.unit()
        self.assertTrue(any(line.startswith('[W11]') for line in self.check_unit(unit)))
        self.assertEqual(self.precheck(unit), {'passed': True, 'problems': []})

    def test_picture_spelling_without_text_blank_is_warning_and_passes(self):
        unit = self.unit(question_type='单词拼写', stem='[IMG picture.png]', answer='apple')
        self.assertTrue(any(line.startswith('[W11]') for line in self.check_unit(unit)))
        self.assertTrue(self.precheck(unit)['passed'])

    def test_native_spelling_blank_remains_valid_without_warning(self):
        unit = self.unit(question_type='单词拼写', stem='The lake is <u>r</u><u>　　</u>.', answer='rich')
        self.assertEqual(self.check_unit(unit), [])
        self.assertTrue(self.precheck(unit)['passed'])

    def test_semicolon_only_answer_still_blocks_with_or_without_blank(self):
        for stem, answer in [('What colour is the bag?', '; ;'), ('It is ______.', '；；')]:
            with self.subTest(stem=stem, answer=answer):
                result = self.precheck(self.unit(stem=stem, answer=answer))
                self.assertFalse(result['passed'])
                self.assertTrue(any(line.startswith('[D11]') and '只剩分号' in line for line in result['problems']))

    def test_real_empty_answer_still_blocks(self):
        for answer in ('', '　 \n', None):
            with self.subTest(answer=answer):
                result = self.precheck(self.unit(answer=answer))
                self.assertFalse(result['passed'])
                self.assertTrue(any(line.startswith('[D11]') and '答案为空' in line for line in result['problems']))
        missing = self.unit()
        missing.pop('answer')
        self.assertFalse(self.precheck(missing)['passed'])

    def test_group_with_answered_children_needs_no_aggregate_answer(self):
        unit = self.unit(question_type='单词拼写', stem='[IMG spelling-grid.png]',
                         subquestions=[{'number': 1, 'answer': 'apple'}, {'number': 2, 'answer': 'bag'}])
        unit.pop('answer')
        self.assertTrue(self.precheck(unit)['passed'])

    def test_group_empty_or_semicolon_child_and_semicolon_aggregate_still_block(self):
        for child_answer, aggregate in [('', None), (';；', None), ('apple', '; ;')]:
            with self.subTest(child=child_answer, aggregate=aggregate):
                unit = self.unit(question_type='单词拼写', answer=aggregate,
                                 subquestions=[{'number': 1, 'answer': child_answer}])
                result = self.precheck(unit)
                self.assertFalse(result['passed'])
                self.assertTrue(any(line.startswith('[D11]') for line in result['problems']))

    def test_other_substantive_code_is_not_ignored(self):
        result = self.precheck(self.unit(stem='It is ______.', material='It was　51　there.'))
        self.assertFalse(result['passed'])
        self.assertTrue(any(line.startswith('[B11]') for line in result['problems']))
