"""First-delivery scoped recovery uses verified private units, never fake delivery."""
import copy
import json
import unittest
from unittest.mock import patch

from workflow import agent_tasks, label_execution, private_label_baseline, scoped_regeneration, unit_tasks
from workflow import test_label_execution as fixtures


class PrivateBaselineTest(unittest.TestCase):
    setUp = fixtures.LabelExecutionTest.setUp
    put = fixtures.LabelExecutionTest.put
    selected = fixtures.LabelExecutionTest.selected
    answers = fixtures.LabelExecutionTest.answers
    explanations = fixtures.LabelExecutionTest.explanations

    def fail_first_delivery(self, stage='answers'):
        calls = []
        def invoke(prompt, *args, **kwargs):
            units = self.selected(prompt)
            calls.extend(u['unit_id'] for u in units)
            if stage == 'answers':
                return {'response': self.answers(units, bad=True)}
            return {'response': self.explanations(units, bad=True)}
        result = label_execution.run(self.batch, stage, dict(self.config, retries=0),
                                     self.batch / 'initial', invoke=invoke)
        self.assertEqual(result['response']['status'], 'needs_review')
        self.assertEqual(calls, ['q-1', 'q-2'])
        self.assertFalse((self.batch / agent_tasks.ARTIFACTS[stage]).exists())
        self.feedback(stage, 'q-2')
        return result

    def feedback(self, stage, uid):
        self.put('workflow/repair-feedback/' + stage + '.json', {
            'version': 1, 'stage': stage, 'scoped_units': True, 'unit_ids': [uid], 'all_units': False,
            'reason': 'repair only this unit', 'error': 'candidate content incorrect',
            'feedback_by_unit': {uid: {'reason': 'repair', 'error': 'incorrect candidate'}},
            'attempt': 1})

    def test_first_delivery_private_answers_merge_and_normal_import(self):
        self.fail_first_delivery()
        task = agent_tasks.build_task(self.batch, 'answers')
        self.assertFalse(task['preconditions'])
        receipts = {k: v for k, v in task['input_hashes'].items() if '-baseline/' in k}
        self.assertEqual(len(receipts), 1)
        before = {k: (self.batch / k).read_bytes() for k in receipts}
        calls = []
        def invoke(prompt, *args, **kwargs):
            units = self.selected(prompt); calls.extend(u['unit_id'] for u in units)
            return {'response': self.answers(units)}
        result = label_execution.run(self.batch, 'answers', self.config,
                                     self.batch / 'repair', invoke=invoke)
        self.assertEqual(calls, ['q-2'])
        artifact = agent_tasks.ARTIFACTS['answers']
        # The run response carries only the units this call really produced; the
        # untouched unit comes from the revalidated receipt at acceptance time,
        # never as if the model had regenerated it.
        self.assertEqual(result['response']['artifacts'][artifact]['answers'], {'2': 'B'})
        self.assertFalse((self.batch / artifact).exists())
        self.assertEqual(before, {k: (self.batch / k).read_bytes() for k in receipts})
        self.assertTrue(agent_tasks.accept_result(self.batch, 'answers', result['response'])['completed'])
        self.assertEqual(json.loads((self.batch / artifact).read_text(encoding='utf-8'))['answers'], {'1': 'A', '2': 'B'})

    def test_no_baseline_widens_to_full_delivery_instead_of_blocking(self):
        self.feedback('answers', 'q-2')
        task = agent_tasks.build_task(self.batch, 'answers')
        self.assertFalse(task['preconditions'])
        # Nothing was ever verified, so both units must be produced; the model is
        # never asked to "deliver" untested rows it did not write.
        data = json.loads(task['prompt'].split(unit_tasks._BEGIN, 1)[1].split(unit_tasks._END, 1)[0])
        self.assertEqual(data['repair_feedback']['unit_ids'], ['q-1', 'q-2'])
        self.assertTrue(data['repair_feedback']['all_units'])

    def test_source_changed_widens_to_full_delivery(self):
        self.fail_first_delivery()
        self.put('01_source/附录.txt', '1. A 【解析】changed\n2. B')
        task = agent_tasks.build_task(self.batch, 'answers')
        self.assertFalse(task['preconditions'])
        data = json.loads(task['prompt'].split(unit_tasks._BEGIN, 1)[1].split(unit_tasks._END, 1)[0])
        self.assertEqual(data['repair_feedback']['unit_ids'], ['q-1', 'q-2'])

    def test_current_validator_rechecks_untouched_unit_and_rejects_changes(self):
        self.fail_first_delivery()
        original = agent_tasks._validate
        def validate(batch, stage, payload, context, units, **kwargs):
            original(batch, stage, payload, context, units, **kwargs)
            if [u['unit_id'] for u in units] == ['q-1']:
                payload['evidence']['1'] = ['requires a code repair']
        with patch.object(agent_tasks, '_validate', side_effect=validate):
            task = agent_tasks.build_task(self.batch, 'answers')
        # A receipt that no longer validates is never reused: the stage falls back
        # to producing every unit instead of blocking or faking the untouched row.
        self.assertFalse(task['preconditions'])
        data = json.loads(task['prompt'].split(unit_tasks._BEGIN, 1)[1].split(unit_tasks._END, 1)[0])
        self.assertEqual(data['repair_feedback']['unit_ids'], ['q-1', 'q-2'])
        self.assertIn('无可复用已验收单元', data['repair_feedback']['reason'])

    def test_merge_cas_rejects_receipt_changed_since_task(self):
        self.fail_first_delivery()
        context, hashes, missing, units = agent_tasks._collect(self.batch, 'answers')
        self.assertFalse(missing)
        relative = next(k for k in hashes if '-baseline/' in k)
        path = self.batch / relative
        path.write_bytes(path.read_bytes() + b'\n')
        chosen = [u for u in units if u['unit_id'] == 'q-2']
        candidate = self.answers(chosen)['artifacts'][agent_tasks.ARTIFACTS['answers']]
        with self.assertRaisesRegex(ValueError, 'private checkpoint changed'):
            scoped_regeneration.merge(self.batch, 'answers', candidate, context, units)
        self.assertFalse((self.batch / agent_tasks.ARTIFACTS['answers']).exists())

    def test_canonical_baseline_remains_authority(self):
        payload = self.answers([{'numbers': [1, 2]}])['artifacts'][agent_tasks.ARTIFACTS['answers']]
        self.put(agent_tasks.ARTIFACTS['answers'], payload)
        self.feedback('answers', 'q-2')
        task = agent_tasks.build_task(self.batch, 'answers')
        self.assertFalse(task['preconditions'])
        self.assertFalse(any('-baseline/' in k for k in task['input_hashes']))

    def test_external_absolute_answer_source_is_not_a_rule(self):
        hashes = {str(self.batch / 'external-answers.txt'): 'a',
                  str(agent_tasks.ROOT / 'docs/content-labeling.md'): 'rule-a'}
        binding = private_label_baseline._external(hashes)
        self.assertIn(str(self.batch / 'external-answers.txt'), binding)
        self.assertNotIn(str(agent_tasks.ROOT / 'docs/content-labeling.md'), binding)


if __name__ == '__main__':
    unittest.main()
