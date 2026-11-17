import copy
import unittest

from workflow import stage_semantics as semantics


class EarlyMetadataSemanticsTest(unittest.TestCase):
    def sample(self):
        return {'items': [{'unit_id': 'q-1', 'unit_kind': 'standalone',
                           'numbers': [1], 'stem': '<u>A</u> word',
                           'filterMetadata': {'source': 'paper', 'difficulty': '适中'},
                           'evidence': {'answer': 'source-owned binding'}}]}

    def test_accepted_fills_do_not_change_early_owned_view(self):
        early = self.sample()
        final = copy.deepcopy(early)
        final['items'][0].update(answer='B', explanation='original explanation',
                                 knowledge=['leaf'], question_type='单项填空')
        final['items'][0]['filterMetadata']['subtypes'] = ['四个选项']
        self.assertEqual(semantics.early_metadata_view(early), semantics.early_metadata_view(final))

    def test_source_underlines_metadata_and_nested_evidence_remain_bound(self):
        early = self.sample()
        for mutation in (
            lambda row: row.update(stem='A word'),
            lambda row: row['filterMetadata'].update(source='other'),
            lambda row: row['filterMetadata'].update(difficulty='容易'),
            lambda row: row['evidence'].update(answer='different source binding'),
            lambda row: row.update(numbers=[2]),
        ):
            final = copy.deepcopy(early)
            mutation(final['items'][0])
            self.assertNotEqual(semantics.early_metadata_view(early), semantics.early_metadata_view(final))

    def test_projection_applies_to_assemble_input_not_final_content_consumers(self):
        early = self.sample()
        final = copy.deepcopy(early)
        final['items'][0]['answer'] = 'B'
        rel = '06_batch/batch_meta.json'
        self.assertEqual(semantics._projection('assemble', rel, early),
                         semantics._projection('assemble', rel, final))
        self.assertNotEqual(semantics._projection('validate', rel, early),
                            semantics._projection('validate', rel, final))


if __name__ == '__main__':
    unittest.main()
