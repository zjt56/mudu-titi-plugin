"""Content recovery admission: exact scope, deterministic refusal, real merge paths."""
import copy
import json
import unittest
from unittest.mock import patch

from workflow import agent_tasks, manifest as mf, self_heal
from workflow import test_repair_feedback as fixture


class ContentRetryScopeTest(unittest.TestCase):
    setUp = fixture.RepairFeedbackTest.setUp
    write = fixture.RepairFeedbackTest.write
    stages = ('answers', 'explanations', 'types', 'knowledge', 'audio')
    aliases = ('retry_stage', 'retry_stage_with_fresh_context', 'refresh_inputs',
               'record_only', 'upstream_rederive', 'defer_and_continue')

    def state(self):
        state = copy.deepcopy(self.manifest)
        state['stages'] = {stage: {'status': mf.FAILED} for stage in (*self.stages, 'preassemble', 'review')}
        self.write('workflow/manifest.json', state)
        return state

    def test_all_content_recovery_aliases_refuse_missing_scope_without_mutation(self):
        for stage in self.stages:
            for action in self.aliases:
                with self.subTest(stage=stage, action=action):
                    state = self.state()
                    before = (self.batch / 'workflow/manifest.json').read_bytes()
                    with self.assertRaisesRegex(ValueError, 'content_retry_requires_scope'), \
                         patch.object(self_heal, '_requeue') as queue:
                        self_heal._apply(self.batch, state, stage,
                            'Official artifact missing; response already exists but import refused',
                            {'action': action, 'reason': 'retry'}, 1, {'files': {}})
                    queue.assert_not_called()
                    self.assertEqual((self.batch / 'workflow/manifest.json').read_bytes(), before)
                    self.assertFalse((self.batch / 'workflow/repair-feedback' / (stage + '.json')).exists())

    def test_all_aliases_write_true_scoped_feedback_preserving_official_files(self):
        self.units[0]['type'] = '听力选择题'
        self.write('01_source/structure.confirmed.json', {'units': self.units})
        for stage in self.stages:
            for action in self.aliases:
                with self.subTest(stage=stage, action=action):
                    state = self.state()
                    artifacts = {name: (self.batch / rel).read_bytes() for name, rel in
                                 agent_tasks.ARTIFACTS.items() if (self.batch / rel).is_file()}
                    with patch.object(self_heal, '_requeue'), patch.object(self_heal, '_defer_and_continue',
                            return_value={'changed': True}):
                        self_heal._apply(self.batch, state, stage, 'q-1: invalid value',
                            {'action': action, 'reason': 'retry'}, 1, {'files': {}})
                    feedback = json.loads((self.batch / 'workflow/repair-feedback' / (stage + '.json')).read_text(encoding='utf-8'))
                    self.assertEqual(feedback['unit_ids'], ['q-1'])
                    self.assertTrue(feedback['scoped_units'])
                    self.assertFalse(feedback['all_units'])
                    for name, raw in artifacts.items():
                        self.assertEqual((self.batch / agent_tasks.ARTIFACTS[name]).read_bytes(), raw)

    def test_structured_source_conflict_refuses_even_explicit_scope_and_fresh_context(self):
        for failure_class in ('source_binding', 'validator_fault'):
            detail = json.dumps({'errors': [{'code': 'EXPLANATION_SOURCE_UNVERIFIED',
                'unit_id': 'q-1', 'message': 'conflicting authoritative source',
                'retryable': False, 'failure_class': failure_class}]})
            for action in self.aliases:
                with self.subTest(failure_class=failure_class, action=action), \
                     self.assertRaisesRegex(ValueError, 'content_source_binding_requires_code_repair'), \
                     patch.object(self_heal, '_requeue') as queue:
                    self_heal._apply(self.batch, self.state(), 'explanations', detail,
                        {'action': action, 'reason': 'new context'}, 1, {'files': {}})
                queue.assert_not_called()
            with self.assertRaisesRegex(ValueError, 'requires_code_repair'):
                self_heal._content_retry_scope(self.batch, 'explanations', detail, unit_ids=['q-1'])

    def test_refresh_preassemble_cannot_fall_through_to_content_stage_retry(self):
        for stage in self.stages:
            with self.subTest(stage=stage), self.assertRaisesRegex(ValueError, 'refresh_preassemble_not_applicable'), \
                 patch.object(self_heal, '_requeue') as queue:
                self_heal._apply(self.batch, self.state(), stage, 'q-1 failed',
                    {'action': 'refresh_preassemble', 'reason': 'alias'}, 1, {'files': {}})
            queue.assert_not_called()

    def test_generic_source_unverified_is_not_blanket_deterministic_refusal(self):
        detail = json.dumps({'code': 'EXPLANATION_SOURCE_UNVERIFIED', 'unit_id': 'q-1', 'evidence': []})
        self.assertEqual(self_heal._content_retry_scope(self.batch, 'explanations', detail), ['q-1'])

    def test_unknown_scope_and_nonlistening_audio_rejected_by_existing_scope_contract(self):
        with self.assertRaises(ValueError):
            self_heal._content_retry_scope(self.batch, 'answers', '', unit_ids=['unknown'])
        with self.assertRaises(ValueError):
            self_heal._content_retry_scope(self.batch, 'audio', '', unit_ids=['q-1'])

    def test_initial_tasks_and_review_protocol_are_not_content_recovery(self):
        self.assertIsNone(self_heal._retry_admission(self.batch, 'review', 'bad review protocol', 'retry_stage'))
        self.assertIsNone(self_heal._retry_admission(self.batch, 'preassemble', 'first task', 'retry_stage'))
        # Building an ordinary initial task with no repair feedback remains full coverage.
        context = agent_tasks._collect(self.batch, 'types')
        self.assertNotIn('repair_feedback', context)
        self.assertEqual([u['unit_id'] for u in agent_tasks.confirmed_units(self.batch)], ['q-1', 'q-2'])

    def test_deterministic_copy_allows_one_full_delivery_without_scope(self):
        state = self.state()
        with patch.object(self_heal, '_deterministic_delivery', return_value=True), \
             patch.object(self_heal, '_requeue') as queue:
            result = self_heal._apply(self.batch, state, 'explanations',
                'Label stage inputs are not ready', {'action': 'retry_stage', 'reason': 'retry'}, 1,
                {'files': {}})
        queue.assert_called_once()
        self.assertTrue(result['changed'])
        self.assertEqual(result['outcome'], 'retry_scheduled')
        # 整阶段直抄重跑不带局部反馈文件：没有"部分单元"可复用，重新整卷交付。
        self.assertIsNone(result['feedback_path'])
        self.assertFalse((self.batch / 'workflow/repair-feedback/explanations.json').exists())

    def test_placeholder_only_copy_is_not_deterministic_delivery(self):
        from workflow import explanations_direct
        placeholder = explanations_direct.PLACEHOLDER
        payload = {'items': [{'unit_id': 'q-1', 'explanation': placeholder,
                              'articleSummary': placeholder, 'subquestions': []}]}
        with patch.object(explanations_direct, 'build_payload', return_value=payload):
            self.assertFalse(self_heal._deterministic_delivery(self.batch, 'explanations'))
        payload['items'][0]['explanation'] = '按原文抄录的解析'
        with patch.object(explanations_direct, 'build_payload', return_value=payload):
            self.assertTrue(self_heal._deterministic_delivery(self.batch, 'explanations'))
        # 只听写的 answers/audio 等阶段没有直抄交付通道，仍受范围准入约束。
        self.assertFalse(self_heal._deterministic_delivery(self.batch, 'answers'))

    def test_actual_diagnosis_prompt_states_scope_and_deterministic_rules(self):
        self.state()
        response = {'response': {'status': 'completed', 'notes': [], 'findings': [],
            'artifacts': {'self_heal': {'action': 'record_only', 'stage': 'explanations', 'reason': 'needs code repair'}}}}
        with patch('workflow.local_agent.invoke', return_value=response) as invoke, \
             patch.object(self_heal.diagnose, 'diagnose', return_value={'items': []}):
            self_heal._plan(self.batch, 'explanations', 'q-1 failed', {})
        prompt = invoke.call_args.args[0]
        for text in ('正式产物缺失不等于模型未产出', 'response候选', 'retryable=false',
                     'source_binding/validator_fault', 'scoped_regeneration', '无定位', '保留成功单元'):
            self.assertIn(text, prompt)


if __name__ == '__main__':
    unittest.main()
