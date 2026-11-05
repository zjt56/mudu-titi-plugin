"""End-to-end regression for attributed decisions on round disagreements."""
import copy
import json
import unittest

from workflow import review, review_rounds
from workflow import test_review_consensus


class ResolveRoundDecisionTest(test_review_consensus.ConsensusTest):
    def test_round_observations_resolve_independently_in_two_round_review(self):
        payload = self.payload()
        for record in payload['rounds']:
            record['checks'][0].update(verdict='uncertain', rationale='原文存在同一处歧义。',
                                       issue_key='题目答案冲突', resolution_key='核对题目答案')
        # 组题的一审与二审都在组级观察；用不同的问题标识区分两轮的具体观察，
        # 以便验证“解决一轮的观察不覆盖另一轮”。
        payload['rounds'][1]['checks'][0].update(issue_key='组级标签存疑', resolution_key='复核组级标签')
        for record in payload['rounds']:
            task = review_rounds.export_task(self.batch)
            response = {'status': 'completed', 'artifacts': {'review_round': {'checks': record['checks']}},
                        'notes': [], 'findings': []}
            report = review_rounds.import_result(self.batch, task['task_id'], response)
        self.assertTrue(report['completed'], report)
        queue_path = self.batch / '07_review/manual-review-queue.json'
        items = json.loads(queue_path.read_text(encoding='utf-8'))['items']
        labels_item = next(item for item in items if item['round_sources'][0]['round_id'] == 'labels')
        sample_item = next(item for item in items if item['round_sources'][0]['round_id'] == 'sample')
        result = review.resolve(self.batch, finding_ids=[labels_item['finding_id']], action='accept',
                                actor='reviewer', note='已核对一审观察。', force=True)
        self.assertEqual(result['resolved'], 1, result)
        evaluated = review.evaluate_rounds(self.batch)
        self.assertEqual({f['field'] for f in evaluated['resolved_findings']}, {'labels'})
        self.assertEqual([e['field'] for e in evaluated['errors'] if e['code'] == 'REVIEW_ROUND_FINDING'], ['sample'])
        saved = json.loads(queue_path.read_text(encoding='utf-8'))['items']
        self.assertEqual(next(i for i in saved if i['finding_id'] == sample_item['finding_id']), sample_item)
        # A later changed source is not covered by the earlier decision.
        changed = json.loads((self.batch / '07_review/review-rounds.json').read_text(encoding='utf-8'))
        changed['rounds'][1]['checks'][0]['assessment']['difficulty'][0]['value'] = '困难'
        evaluated = review.evaluate_rounds(self.batch, changed)
        self.assertIn('sample', {e['round_id'] for e in evaluated['unchecked']})

    def test_resolve_requires_exact_sources_and_leaves_other_disagreement_open(self):
        payload = self.payload()
        for check in payload['rounds'][1]['checks']:
            check['assessment']['subtypes'] = ['选择4题']
        payload['rounds'][1]['checks'][0]['assessment']['difficulty'][0]['value'] = '困难'
        for record in payload['rounds']:
            task = review_rounds.export_task(self.batch)
            response = {'status': 'completed', 'artifacts': {'review_round': {'checks': record['checks']}},
                        'notes': [], 'findings': []}
            report = review_rounds.import_result(self.batch, task['task_id'], response)
        self.assertTrue(report['completed'], report)
        self.assertFalse(report['quality_passed'], report)
        queue_path = self.batch / '07_review/manual-review-queue.json'
        queue_payload = json.loads(queue_path.read_text(encoding='utf-8'))
        items = queue_payload['items']
        self.assertEqual(len(items), 2)
        chosen = next(item for item in items if item['field'] == 'subtypes')
        other = next(item for item in items if item['field'] == 'difficulty')
        original_sources = copy.deepcopy(chosen['round_sources'])
        self.assertGreater(len(original_sources), 1)
        note = '已核对全部轮次证据并作出决定。'
        result = review.resolve(self.batch, finding_ids=[chosen['finding_id']], action='accept',
                                actor='reviewer', note=note, force=True)
        self.assertEqual(result['resolved'], 1, result)
        saved = json.loads(queue_path.read_text(encoding='utf-8'))
        chosen_saved = next(item for item in saved['items'] if item['finding_id'] == chosen['finding_id'])
        other_saved = next(item for item in saved['items'] if item['finding_id'] == other['finding_id'])
        self.assertEqual(chosen_saved['resolution_note'], note)
        self.assertEqual(chosen_saved['round_sources'], original_sources)
        self.assertEqual(other_saved, other)
        evaluated = review.evaluate_rounds(self.batch)
        self.assertFalse(evaluated['passed'])
        self.assertEqual([f['field'] for f in evaluated['resolved_findings']], ['subtypes'])
        self.assertEqual(evaluated['resolved_findings'][0]['round_sources'], original_sources)
        self.assertEqual([e['field'] for e in evaluated['errors'] if e['code'] == 'REVIEW_ROUND_FINDING'], ['difficulty'])

        # A decision cannot cover even a single omitted or modified source.
        for changed_sources in (original_sources[:-1], list(reversed(original_sources))):
            chosen_saved['round_sources'] = changed_sources
            queue_path.write_text(json.dumps(saved, ensure_ascii=False), encoding='utf-8')
            evaluated = review.evaluate_rounds(self.batch)
            self.assertEqual(evaluated['resolved_findings'], [])
            self.assertEqual({e['field'] for e in evaluated['errors'] if e['code'] == 'REVIEW_ROUND_FINDING'},
                             {'subtypes', 'difficulty'})
        chosen_saved['round_sources'] = original_sources
        queue_path.write_text(json.dumps(saved, ensure_ascii=False), encoding='utf-8')
        review.resolve(self.batch, finding_ids=[other['finding_id']], action='accept',
                       actor='reviewer', note='已核对难度证据。', force=True)
        evaluated = review.evaluate_rounds(self.batch)
        self.assertTrue(evaluated['passed'], evaluated['errors'])
        self.assertEqual({f['field'] for f in evaluated['resolved_findings']}, {'subtypes', 'difficulty'})


if __name__ == '__main__':
    unittest.main()
