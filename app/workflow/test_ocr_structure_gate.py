"""OCR-informed grouping must remain bound to the actual Word text."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import structure_gate


class OCRStructureGateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.batch = Path(self.temp.name)
        (self.batch / '01_source').mkdir()
        self.lines = [
            '听下面一段对话，回答第6至7题。',
            '6. First question?',
            '7. Second question?',
            '听下面一段对话，回答第8至9题。',
            '8. Third question?',
            '9. Fourth question?',
        ]
        self.structure = {
            'sections': [{'type': '听力长对话', 'questions': [6, 7, 8, 9]}],
            'groups': [{'questions': [6, 7, 8, 9]}],
            'unresolved': [{'code': 'OCR_VISUAL_REVIEW', 'message': 'review page'}],
        }
        self.units = [self.unit([6, 7], 1, 3), self.unit([8, 9], 4, 6)]

    def unit(self, numbers, start, end):
        return {'unit_kind': 'group', 'type': '听力长对话', 'numbers': numbers,
                'stem': '\n'.join(self.lines[start - 1:end]),
                'source_span': {'basis': 'ai_classified', 'start_line': start, 'end_line': end}}

    def run_case(self, units=None):
        (self.batch / '01_source/正文.txt').write_text('\n'.join(self.lines), encoding='utf-8')
        (self.batch / '01_source/structure.json').write_text(json.dumps(self.structure), encoding='utf-8')
        with patch('workflow.structure_ai.auto_classify', return_value={'units': self.units if units is None else units}):
            return structure_gate.run_gate(self.batch)

    def codes(self, report):
        return {item['code'] for item in report['blockers']}

    def test_bound_regrouping_overrides_stale_candidate_and_retains_layout_review(self):
        report = self.run_case()
        self.assertTrue(report['passed'], report['blockers'])
        self.assertTrue(report['layout_required'])
        self.assertIn('OCR_VISUAL_REVIEW', {w['code'] for w in report['warnings']})
        confirmed = json.loads((self.batch / '01_source/structure.confirmed.json').read_text())
        self.assertEqual([u['numbers'] for u in confirmed['units']], [[6, 7], [8, 9]])

    def test_legacy_candidate_still_checks_original_boundaries(self):
        self.assertIn('GROUP_BOUNDARY_MISMATCH', self.codes(self.run_case([])))

    def test_unbound_or_hallucinated_units_do_not_override_boundaries(self):
        for change in ('span', 'omission', 'hallucination', 'duplicate'):
            with self.subTest(change=change):
                units = [dict(unit, source_span=dict(unit['source_span'])) for unit in self.units]
                if change == 'span':
                    units[0]['source_span']['end_line'] = 2
                elif change == 'omission':
                    units = units[:1]
                elif change == 'hallucination':
                    units[1]['numbers'] = [8, 10]
                else:
                    units[1]['numbers'] = [7, 8, 9]
                codes = self.codes(self.run_case(units))
                self.assertIn('AI_SOURCE_BINDING_INVALID', codes)
                self.assertIn('GROUP_BOUNDARY_MISMATCH', codes)

    def test_actual_source_omission_and_duplicate_remain_blocking(self):
        self.lines.append('10. Unreported question?')
        self.assertIn('SOURCE_NUMBERS_UNDECLARED', self.codes(self.run_case()))
        self.lines[-1] = '6. Repeated question?'
        self.assertIn('SOURCE_DUPLICATE_NUMBERS', self.codes(self.run_case()))

    def test_independent_guide_conflict_and_bad_group_remain_blocking(self):
        self.lines.append('听下面一段对话，回答第6至9题。')
        self.assertIn('GUIDE_GROUP_MISMATCH', self.codes(self.run_case()))
        self.lines.pop()
        self.structure['groups'].append({'questions': [9, 10]})
        codes = self.codes(self.run_case())
        self.assertIn('DUPLICATE_GROUP_NUMBERS', codes)
        self.assertIn('GROUP_OUT_OF_SECTION', codes)


if __name__ == '__main__':
    unittest.main()
