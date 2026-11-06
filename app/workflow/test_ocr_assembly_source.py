"""Source-bound OCR-informed grouping in the final assembly audit."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow.question_content import audit_batch_source


class OcrAssemblySourceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source_dir = self.root / '01_source'
        source_dir.mkdir()
        self.source = 'Passage one.\n1. First?\n2. Second?\nPassage two.\n3. Third?\n4. Fourth?'
        (source_dir / '正文.txt').write_text(self.source, encoding='utf-8')
        (source_dir / 'structure.json').write_text('{}', encoding='utf-8')
        self.units = [self.unit([1, 2], 1, 3), self.unit([3, 4], 4, 6)]
        self.fresh = [dict(unit_kind='group', type='阅读理解', numbers=[1, 2, 3, 4], stem=self.source)]
        self.items = [dict(unit_id='a', numbers=[1, 2], material='Passage one.',
                           subquestions=[dict(number=1, stem='First?'), dict(number=2, stem='Second?')]),
                      dict(unit_id='b', numbers=[3, 4], material='Passage two.',
                           subquestions=[dict(number=3, stem='Third?'), dict(number=4, stem='Fourth?')])]

    def unit(self, numbers, start, end):
        return dict(unit_kind='group', type='阅读理解', numbers=numbers,
                    stem='\n'.join(self.source.splitlines()[start-1:end]),
                    source_span=dict(start_line=start, end_line=end, basis='ai_classified'))

    def audit(self, units=None, items=None):
        (self.root / '01_source/structure.confirmed.json').write_text(
            json.dumps({'units': self.units if units is None else units}), encoding='utf-8')
        with patch('workflow.denoise.clean_body', return_value=(self.source, [], [])), \
             patch('workflow.structure_gate._build_units', return_value=self.fresh):
            return audit_batch_source(self.root, {'items': self.items if items is None else items})

    def codes(self, units=None, items=None):
        return {issue['code'] for issue in self.audit(units, items)['issues']}

    def test_valid_ai_grouping_uses_word_spans(self):
        self.assertEqual(self.codes(), set())
        missing = copy.deepcopy(self.items)
        missing[0]['material'] = ''
        self.assertIn('SOURCE_MATERIAL_MISSING', self.codes(items=missing))

    def test_rejects_invalid_spans_stems_and_unbound_numbers(self):
        for mutate in (
            lambda u: u[0]['source_span'].update(start_line=0),
            lambda u: u[0]['source_span'].update(end_line=4),
            lambda u: u[0].update(stem='Passage ONE.\n1. First?\n2. Second?'),
            lambda u: u[0].update(numbers=[1, 3]),
            lambda u: u[1].update(numbers=[3]),
            lambda u: u[0]['source_span'].update(start_line=True),
        ):
            units = copy.deepcopy(self.units)
            mutate(units)
            self.assertIn('SOURCE_GROUP_BOUNDARY_MISMATCH', self.codes(units))

    def test_short_exact_stem_cannot_hide_source_material(self):
        units = copy.deepcopy(self.units)
        units[0]['stem'] = '1. First?\n2. Second?'
        items = copy.deepcopy(self.items)
        items[0]['material'] = ''
        self.assertIn('SOURCE_MATERIAL_MISSING', self.codes(units, items))

    def test_separate_guides_cannot_be_merged(self):
        self.source = ('回答第1至2小题\nPassage one.\n1. First?\n2. Second?\n'
                       '回答第3至4小题\nPassage two.\n3. Third?\n4. Fourth?')
        (self.root / '01_source/正文.txt').write_text(self.source, encoding='utf-8')
        units = [self.unit([1, 2, 3, 4], 1, 8)]
        items = [dict(unit_id='merged', numbers=[1, 2, 3, 4])]
        self.assertIn('SOURCE_GROUP_BOUNDARY_MISMATCH', self.codes(units, items))

    def test_formal_number_coverage_remains_required(self):
        self.assertIn('BATCH_NUMBER_COVERAGE', self.codes(items=self.items[:1]))

    def test_legacy_keeps_deterministic_boundary_check(self):
        units = copy.deepcopy(self.units)
        for unit in units:
            unit['source_span']['basis'] = 'cleaned_source'
        self.assertIn('SOURCE_GROUP_BOUNDARY_MISMATCH', self.codes(units))


if __name__ == '__main__':
    unittest.main()
