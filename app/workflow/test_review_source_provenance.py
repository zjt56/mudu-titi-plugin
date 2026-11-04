"""Provenance checks exercise actual review imports and their durable records."""
import copy
import json
import unittest
from unittest.mock import patch

from workflow import review, review_rounds
from workflow import test_review_rounds as fixture_module


class ReviewSourceProvenanceTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.ReviewRoundsTest('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.batch = self.fixture.batch
        units = [{'unit_id': f'u{n}', 'unit_kind': 'standalone', 'numbers': [n],
                  'stem': f'Question text for unit {n}.'} for n in (1, 2)]
        self.fixture.write('01_source/正文.txt', '\n'.join(u['stem'] for u in units))
        self.fixture.write('01_source/structure.confirmed.json', {'units': units})
        self.fixture.write('01_source_ans/answers.json', {'answers': {'1': 'A', '2': 'A'},
                                                         'difficulty': {'1': '容易', '2': '容易'}})
        self.fixture.plan = review.prepare_round_plan(self.batch)

    def response(self, exported):
        binding = json.loads((self.batch / 'workflow/review-bindings' / (exported['task_id'] + '.json')).read_text())
        state = json.loads((self.batch / binding['state_path']).read_text())
        targets = state['parts'][binding['part_index']]['targets']
        checks = [dict(unit_id=uid, verdict='ok', rationale='核对原文。',
                       evidence=[{'path': '01_source/正文.txt', 'locator': uid,
                                  'quote': f'Question text for unit {uid[1:]}.'}],
                       assessment={'type': '阅读理解', 'subtypes': ['阅读判断'],
                                   'knowledge': ['语篇体裁/记叙文/故事'], 'subquestions': [],
                                   'difficulty': [{'number': uid[1:], 'value': '容易'}]}) for uid in targets]
        return {'status': 'completed', 'artifacts': {'review_round': {'checks': checks}}, 'notes': [], 'findings': []}

    def test_real_parts_and_forged_sources(self):
        first = review_rounds.export_task(self.batch, max_units_per_part=1)
        self.assertEqual(first['part_count'], 2)
        review_rounds.import_result(self.batch, first['task_id'], self.response(first))
        second = review_rounds.export_task(self.batch)
        review_rounds.import_result(self.batch, second['task_id'], self.response(second))
        path = self.batch / '07_review/review-rounds.json'
        payload = json.loads(path.read_text())
        self.assertIn('labels', review.evaluate_rounds(self.batch, payload)['checked_rounds'])
        for mutation in ('fake', 'other_binding', 'checks', 'targets'):
            bad = copy.deepcopy(payload)
            part = bad['rounds'][0]['parts'][0]
            if mutation == 'fake':
                part['execution_id'] = 'round-' + '0' * 32
            elif mutation == 'other_binding':
                duplicate = review_rounds.export_task(self.batch)
                part['execution_id'] = duplicate['task_id']
            elif mutation == 'checks':
                bad['rounds'][0]['checks'][0]['rationale'] = '另一条伪造判断'
            else:
                bad['rounds'][0]['parts'].reverse()
                bad['rounds'][0]['parts'][0]['targets'], bad['rounds'][0]['parts'][1]['targets'] = (
                    bad['rounds'][0]['parts'][1]['targets'], bad['rounds'][0]['parts'][0]['targets'])
            with self.subTest(mutation=mutation):
                self.assertNotIn('labels', review.evaluate_rounds(self.batch, bad)['checked_rounds'])

    def test_final_partition_receipt_failure_recovers(self):
        first = review_rounds.export_task(self.batch, max_units_per_part=1)
        review_rounds.import_result(self.batch, first['task_id'], self.response(first))
        second = review_rounds.export_task(self.batch)
        response = self.response(second)
        save = review_rounds._save

        def fail_final_receipt(batch, path, data):
            if path == 'workflow/review-bindings/' + second['task_id'] + '.receipt.json':
                raise OSError('injected receipt failure')
            return save(batch, path, data)

        with patch.object(review_rounds, '_save', side_effect=fail_final_receipt):
            with self.assertRaises(OSError):
                review_rounds.import_result(self.batch, second['task_id'], response)
        self.assertIn('labels', review.evaluate_rounds(self.batch)['checked_rounds'])
        self.assertEqual(review_rounds.import_result(self.batch, second['task_id'], response)['round_completed'], 'labels')

    def test_single_execution_must_have_real_import(self):
        exported = review_rounds.export_task(self.batch, max_units_per_part=10)
        review_rounds.import_result(self.batch, exported['task_id'], self.response(exported))
        payload = json.loads((self.batch / '07_review/review-rounds.json').read_text())
        self.assertIn('labels', review.evaluate_rounds(self.batch, payload)['checked_rounds'])
        payload['rounds'][0]['execution_id'] = 'round-' + '0' * 32
        self.assertNotIn('labels', review.evaluate_rounds(self.batch, payload)['checked_rounds'])
