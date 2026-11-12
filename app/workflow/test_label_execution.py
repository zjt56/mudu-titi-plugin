"""Isolated model stubs exercise real per-unit source validation and final import."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import agent_tasks, label_execution, unit_tasks, local_agent


class LabelExecutionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        self.put('01_source/正文.txt', '1. First? A. Yes B. No\n2. Second? A. Yes B. No')
        units = [{'unit_id': 'q-' + str(n), 'unit_kind': 'standalone', 'numbers': [n],
                  'type': '单项填空', 'stem': line} for n, line in enumerate(
                      (self.batch / '01_source/正文.txt').read_text().splitlines(), 1)]
        self.put('01_source/structure.confirmed.json', {'units': units})
        self.put('01_source/structure.json', {'sections': [], 'groups': []})
        self.put('01_source/附录.txt', '1. A 【解析】第一题原解析。\n2. B 【解析】第二题原解析。')
        self.put(agent_tasks.ARTIFACTS['types'], {'items': [
            {'unit_id': u['unit_id'], 'type': '单项填空', 'subtypes': ['四个选项'],
             'words': 3, 'contentComplete': True, 'evidence': ['正文']} for u in units]})
        self.config = {'max_input_chars': 180000, 'retries': 1, 'part_concurrency': 1,
                       'model_cache_identity': 'test-model'}

    def put(self, rel, value):
        p = self.batch / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def selected(self, prompt):
        data = json.loads(prompt.split(unit_tasks._BEGIN, 1)[1].split(unit_tasks._END, 1)[0])
        return data['confirmed_units_with_stable_ids']

    def answers(self, units, bad=False):
        numbers = [str(n) for u in units for n in u['numbers']]
        payload = {'answers': {n: ('A' if n == '1' else 'B') for n in numbers},
                   'evidence': {n: ['附录第' + n + '题'] for n in numbers}, 'missing': {}}
        if bad and '2' in numbers:
            payload['answers']['2'] = 'NONEXISTENT'
        return {'status': 'completed', 'artifacts': {agent_tasks.ARTIFACTS['answers']: payload},
                'notes': [], 'findings': []}

    def test_failed_answer_only_is_retried_and_success_is_preserved(self):
        calls = []
        def invoke(prompt, directory, config, **kw):
            units = self.selected(prompt)
            calls.append([u['unit_id'] for u in units])
            return {'response': self.answers(units, bad=len(calls) == 1)}
        result = label_execution.run(self.batch, 'answers', self.config, self.batch / 'run', invoke=invoke)
        self.assertEqual(calls, [['q-1', 'q-2'], ['q-2']])
        self.assertEqual(result['response']['artifacts'][agent_tasks.ARTIFACTS['answers']]['answers'], {'1': 'A', '2': 'B'})
        self.assertFalse((self.batch / agent_tasks.ARTIFACTS['answers']).exists())
        self.assertTrue(agent_tasks.accept_result(self.batch, 'answers', result['response'])['completed'])

    def test_exhaustion_keeps_valid_checkpoint_and_resume_calls_only_failure(self):
        def bad(prompt, *a, **kw):
            return {'response': self.answers(self.selected(prompt), bad=True)}
        failed = label_execution.run(self.batch, 'answers', self.config, self.batch / 'run', invoke=bad)
        self.assertEqual(failed['response']['status'], 'needs_review')
        self.assertIn('q-2', failed['response']['notes'][0])
        calls = []
        def good(prompt, *a, **kw):
            units = self.selected(prompt); calls.append([u['unit_id'] for u in units])
            return {'response': self.answers(units)}
        label_execution.run(self.batch, 'answers', self.config, self.batch / 'run2', invoke=good)
        self.assertEqual(calls, [['q-2']])
        self.put('01_source/附录.txt', '1. A 【解析】改变来源\n2. B')
        calls.clear()
        label_execution.run(self.batch, 'answers', self.config, self.batch / 'run3', invoke=good)
        self.assertEqual(calls, [['q-1', 'q-2']])

    def test_repair_cannot_overwrite_valid_unit(self):
        calls = []
        def invoke(prompt, *a, **kw):
            units = self.selected(prompt); calls.append(units)
            if len(calls) == 1:
                return {'response': self.answers(units, bad=True)}
            # Attempt to overwrite q-1 while repairing q-2.
            return {'response': self.answers([{'numbers': [1, 2]}])}
        failed = label_execution.run(self.batch, 'answers', self.config, self.batch / 'run', invoke=invoke)
        self.assertEqual(failed['response']['status'], 'needs_review')
        self.assertIn('unknown numbers', failed['response']['notes'][0])
        self.assertFalse((self.batch / agent_tasks.ARTIFACTS['answers']).exists())

    def test_group_with_one_missing_child_is_retried_as_a_whole(self):
        lines = (self.batch / '01_source/正文.txt').read_text().splitlines()
        self.put('01_source/正文.txt', '\n'.join(lines) + '\n3. Third? A. Yes B. No')
        self.put('01_source/附录.txt', '1. A\n2. B\n3. B')
        self.put('01_source/structure.confirmed.json', {'units': [
            {'unit_id': 'group-1-2', 'unit_kind': 'group', 'numbers': [1, 2],
             'type': '完形填空', 'stem': '\n'.join(lines)},
            {'unit_id': 'q-3', 'unit_kind': 'standalone', 'numbers': [3],
             'type': '单项填空', 'stem': '3. Third? A. Yes B. No'}]})
        calls = []
        def invoke(prompt, *a, **kw):
            units = self.selected(prompt); calls.append(copy.deepcopy(units))
            result = self.answers(units)
            if len(calls) == 1:
                for key in ('answers', 'evidence'):
                    del result['artifacts'][agent_tasks.ARTIFACTS['answers']][key]['2']
            return {'response': result}
        result = label_execution.run(self.batch, 'answers', self.config, self.batch / 'run', invoke=invoke)
        self.assertEqual([[u['unit_id'] for u in call] for call in calls], [['group-1-2', 'q-3'], ['group-1-2']])
        self.assertEqual(calls[1][0]['numbers'], [1, 2])
        self.assertEqual(result['response']['status'], 'completed')

    def test_localized_major_finding_retries_only_affected_unit(self):
        calls = []
        def invoke(prompt, *a, **kw):
            units = self.selected(prompt); calls.append([u['unit_id'] for u in units])
            response = self.answers(units)
            if len(calls) == 1:
                response['status'] = 'needs_review'
                response['findings'] = [{'unit_id': 'q-2', 'severity': 'major', 'field': 'answer',
                                         'reason': '答案来源不确定', 'evidence': ['附录第二行']}]
            return {'response': response}
        result = label_execution.run(self.batch, 'answers', self.config, self.batch / 'run', invoke=invoke)
        self.assertEqual(calls, [['q-1', 'q-2'], ['q-2']])
        self.assertEqual(result['response']['status'], 'completed')

    def test_overall_answer_timeout_can_resume_without_partial_install(self):
        source = '\n'.join(str(n) + '. Question? A. Yes B. No' for n in range(1, 10))
        self.put('01_source/正文.txt', source)
        self.put('01_source/附录.txt', '\n'.join(str(n) + '. B' for n in range(1, 10)))
        self.put('01_source/structure.confirmed.json', {'units': [
            {'unit_id': 'q-' + str(n), 'unit_kind': 'standalone', 'numbers': [n], 'type': '单项填空', 'stem': line}
            for n, line in enumerate(source.splitlines(), 1)]})
        config = dict(self.config, part_concurrency=2)
        def timeout(*args, **kwargs):
            raise local_agent.AgentError('timeout', 'timeout')
        with self.assertRaises(local_agent.AgentError):
            label_execution.run(self.batch, 'answers', config, self.batch / 'run', invoke=timeout)
        calls = []
        def resume(prompt, *a, **kw):
            units = self.selected(prompt)
            calls.extend(u['unit_id'] for u in units)
            response = self.answers(units)
            response['artifacts'][agent_tasks.ARTIFACTS['answers']]['answers'] = {
                str(n): 'B' for u in units for n in u['numbers']}
            return {'response': response}
        result = label_execution.run(self.batch, 'answers', config, self.batch / 'run2', invoke=resume)
        self.assertEqual(result['response']['status'], 'completed')
        self.assertEqual(set(calls), {'q-' + str(n) for n in range(1, 10)})

    def test_scoped_regeneration_consumes_real_feedback_and_preserves_other_unit(self):
        from workflow import self_heal
        prior = self.answers([{'numbers': [1, 2]}])['artifacts'][agent_tasks.ARTIFACTS['answers']]
        prior['answers']['1'] = 'B'
        prior['difficulty'] = {'2': '容易'}
        prior['difficultyEvidence'] = {'2': ['原有难度证据']}
        self.put(agent_tasks.ARTIFACTS['answers'], prior)
        self_heal._feedback(self.batch, 'answers', '重生成第一题', 'q-1 答案需修复', 1, unit_ids=['q-1'])
        calls = []
        def invoke(prompt, *a, **kw):
            units = self.selected(prompt); calls.extend(u['unit_id'] for u in units)
            return {'response': self.answers(units)}
        result = label_execution.run(self.batch, 'answers', self.config, self.batch / 'run', invoke=invoke)
        self.assertEqual(calls, ['q-1'])
        self.assertTrue(agent_tasks.accept_result(self.batch, 'answers', result['response'])['completed'])
        saved = json.loads((self.batch / agent_tasks.ARTIFACTS['answers']).read_text(encoding='utf-8'))
        for field in ('answers', 'evidence', 'difficulty', 'difficultyEvidence'):
            self.assertEqual(saved[field]['2'], prior[field]['2'])
        self.assertEqual(saved['answers']['1'], 'A')

    def test_new_answer_source_during_execution_invalidates_missing_decision(self):
        # No proven projection: current raw source differs from the confirmed stem.
        self.put('01_source/structure.confirmed.json', {'units': [
            {'unit_id': 'q-1', 'unit_kind': 'group', 'numbers': [1], 'type': '阅读理解',
             'stem': 'Non-contiguous candidate\n1. First?'}]})
        appendix = self.batch / '01_source/附录.txt'
        appendix.rename(self.batch / 'old-appendix.txt')
        def invoke(prompt, *a, **kw):
            self.put('01_source/附录.txt', '1. A')
            return {'response': {'status': 'completed', 'notes': [], 'findings': [], 'artifacts': {
                agent_tasks.ARTIFACTS['answers']: {'answers': {}, 'evidence': {}, 'missing': {'1': ['未提供答案']}}}}}
        with self.assertRaises(local_agent.AgentError) as caught:
            label_execution.run(self.batch, 'answers', self.config, self.batch / 'run', invoke=invoke)
        self.assertEqual(caught.exception.code, 'context_changed')
        self.assertFalse((self.batch / agent_tasks.ARTIFACTS['answers']).exists())

    def test_native_router_identity_is_bound_for_checkpoint_reuse(self):
        script = self.batch / 'plugin/backend/wf-router.mjs'
        script.parent.mkdir(parents=True)
        script.write_text('// test router')
        router = self.batch / 'plugin/config/model-router.json'
        router.parent.mkdir(parents=True)
        router.write_text(json.dumps({'default': {'provider': 'test', 'model': 'one'}}))
        import os
        with patch.dict(os.environ, {'WF_PLUGIN_DIR': str(self.batch / 'plugin'),
                                     'WF_ROUTER_FILE': str(router)}):
            binding = local_agent._part_model_binding({'command': ['node', str(script)]})
            files = set(binding['files'])
            self.assertIn(str(script.resolve()), files)
            self.assertIn(str(router.resolve()), files)
            self.assertTrue(all(len(value) == 64 for value in binding['files'].values()))
            router.write_text(json.dumps({'default': {'provider': 'test', 'model': 'two'}}))
            self.assertNotEqual(binding, local_agent._part_model_binding({'command': ['node', str(script)]}))

    def test_compaction_reduces_duplicate_inputs_without_losing_sources(self):
        for stage in ('answers', 'explanations'):
            full = agent_tasks.build_task(self.batch, stage, max_input_chars=1000000)
            slim = agent_tasks.build_task(self.batch, stage, max_input_chars=1000000, slim=True)
            self.assertLess(len(slim['prompt']), len(full['prompt']))
            self.assertEqual(slim['input_hashes'], full['input_hashes'])
            context = json.loads(slim['prompt'].split(unit_tasks._BEGIN, 1)[1].split(unit_tasks._END, 1)[0])
            self.assertNotIn('01_source/structure.confirmed.json', context)
            if stage == 'explanations':
                self.assertNotIn('01_source/附录.txt', context)
                self.assertIn('第二题原解析', json.dumps(context['source_fragment_index'], ensure_ascii=False))

    def explanations(self, units, bad=False):
        rows = [{'unit_id': u['unit_id'],
                 'explanation': '第一题原解析。' if u['numbers'][0] == 1 else '第二题原解析。',
                 'articleSummary': '无', 'subquestions': [], 'evidence': ['附录原文']}
                for u in units]
        if bad:
            for row in rows:
                if row['unit_id'] == 'q-2':
                    row['explanation'] = '模型自行改写'
        return {'status': 'completed', 'artifacts': {agent_tasks.ARTIFACTS['explanations']: {'items': rows}},
                'notes': [], 'findings': []}

    def source_failure(self, **overrides):
        row = {'code': 'EXPLANATION_SOURCE_UNVERIFIED', 'unit_id': 'q-1',
               'retryable': False, 'failure_class': 'source_binding',
               'evidence': [{'reason': 'conflicting_source_content', 'candidate_count': 2,
                             'anchor_count': 2, 'candidates': [{'path': '01_source/附录.txt', 'line': 1},
                                                           {'path': '01_source/附录.txt', 'line': 3}]}]}
        row.update(overrides)
        return ValueError(label_execution._OWNERSHIP_FAILURE + json.dumps([row], ensure_ascii=False))

    def test_confirmed_source_fault_stops_first_retry_but_other_candidate_is_repaired(self):
        original = agent_tasks._validate
        calls = []
        def validate(batch, stage, payload, context, units):
            if stage == 'explanations' and [u['unit_id'] for u in units] == ['q-1']:
                # Expansion may mutate the working copy before the error.
                payload['items'][0]['explanation'] = 'mutated validator working copy'
                raise self.source_failure()
            return original(batch, stage, payload, context, units)
        def invoke(prompt, *a, **kw):
            units = self.selected(prompt); calls.append([u['unit_id'] for u in units])
            return {'response': self.explanations(units, bad=len(calls) == 1)}
        config = dict(self.config, retries=7)
        with patch.object(agent_tasks, '_validate', side_effect=validate):
            failed = label_execution.run(self.batch, 'explanations', config, self.batch / 'run', invoke=invoke)
            self.assertEqual(calls, [['q-1', 'q-2'], ['q-2']])
            calls.clear()
            # Different repair feedback and run directory cannot restart the model.
            self.put('workflow/repair-feedback/explanations.json', {
                'version': 1, 'stage': 'explanations', 'reason': 'retry again', 'error': 'same source fault',
                'all_units': False, 'unit_ids': ['q-1'],
                'feedback_by_unit': {'q-1': {'reason': 'retry again', 'error': 'same source fault'}}, 'attempt': 2})
            resumed = label_execution.run(self.batch, 'explanations', config, self.batch / 'run2', invoke=invoke)
            self.assertEqual(calls, [])
        self.assertEqual(failed['response']['status'], 'needs_review')
        self.assertEqual(resumed['response']['artifacts'], {})
        progress = json.loads((self.batch / 'run2/repair-progress.json').read_text(encoding='utf-8'))
        self.assertEqual(progress['validated_units'], ['q-2'])
        self.assertEqual(progress['calls'], [])
        evidence = json.loads(Path(progress['deterministic_failures']['q-1']['evidence_path']).read_text(encoding='utf-8'))
        self.assertEqual(evidence['payload']['items'][0]['explanation'], '第一题原解析。')
        self.assertEqual(len(evidence['original_response']['artifacts'][agent_tasks.ARTIFACTS['explanations']]['items']), 2)
        self.assertIn('EXPLANATION_SOURCE_UNVERIFIED', evidence['validation_error'])
        self.assertFalse((self.batch / agent_tasks.ARTIFACTS['explanations']).exists())
        # A fixed validator accepts the same saved candidate with zero model calls.
        with patch.object(local_agent, 'invoke', side_effect=AssertionError('unexpected model call')):
            recovered = label_execution.run(self.batch, 'explanations', config, self.batch / 'run3')
        self.assertEqual(recovered['response']['status'], 'completed')
        self.assertTrue(agent_tasks.accept_result(self.batch, 'explanations', recovered['response'])['completed'])

    def test_real_conflicting_source_diagnostic_stops_repeated_stage_calls(self):
        self.put('01_source/附录.txt', '1. A 【解析】第一题原解析。\n1. A 【解析】同题不同解析。\n2. B 【解析】第二题原解析。')
        calls = []
        def invoke(prompt, *a, **kw):
            units = self.selected(prompt); calls.append([u['unit_id'] for u in units])
            return {'response': self.explanations(units)}
        config = dict(self.config, retries=7)
        for index in range(3):
            result = label_execution.run(self.batch, 'explanations', config,
                                         self.batch / ('run-' + str(index)), invoke=invoke)
            self.assertEqual(result['response']['status'], 'needs_review')
            self.assertEqual(result['response']['artifacts'], {})
        self.assertEqual(calls, [['q-1', 'q-2']])
        progress = json.loads((self.batch / 'run-2/repair-progress.json').read_text(encoding='utf-8'))
        self.assertEqual(progress['validated_units'], ['q-2'])
        diagnostic = progress['deterministic_failures']['q-1']['diagnostics'][0]
        self.assertFalse(diagnostic['retryable'])
        self.assertEqual(diagnostic['failure_class'], 'source_binding')
        self.assertGreaterEqual(diagnostic['evidence'][0]['candidate_count'], 2)
        self.assertFalse(agent_tasks.accept_result(self.batch, 'explanations', result['response'])['completed'])
        self.assertFalse((self.batch / agent_tasks.ARTIFACTS['explanations']).exists())

    def test_unverified_candidate_text_is_still_model_correctable(self):
        calls = []
        def invoke(prompt, *a, **kw):
            units = self.selected(prompt); calls.append([u['unit_id'] for u in units])
            return {'response': self.explanations(units, bad=len(calls) == 1)}
        result = label_execution.run(self.batch, 'explanations', self.config, self.batch / 'run', invoke=invoke)
        self.assertEqual(calls, [['q-1', 'q-2'], ['q-2']])
        self.assertEqual(result['response']['status'], 'completed')
        self.assertEqual(list((self.batch / 'workflow').rglob('*.failure.json')), [])

    def test_only_explicit_validator_diagnostics_stop_retry(self):
        for exc in (ValueError('ordinary failure'),
                    ValueError('EXPLANATION_SOURCE_UNVERIFIED'),
                    self.source_failure(retryable=True), self.source_failure(retryable=0),
                    self.source_failure(failure_class='candidate'),
                    ValueError(label_execution._OWNERSHIP_FAILURE + 'invalid JSON'),
                    ValueError(label_execution._OWNERSHIP_FAILURE + '[]')):
            with self.subTest(error=str(exc)):
                self.assertIsNone(label_execution._deterministic_diagnostics(exc))
        self.assertIsNotNone(label_execution._deterministic_diagnostics(self.source_failure()))
        self.assertIsNotNone(label_execution._deterministic_diagnostics(self.source_failure(failure_class='validator_fault')))
        rows = json.loads(str(self.source_failure()).split(label_execution._OWNERSHIP_FAILURE)[1])
        mixed = ValueError(label_execution._OWNERSHIP_FAILURE + json.dumps(rows + [{'code': 'candidate_error'}]))
        self.assertIsNone(label_execution._deterministic_diagnostics(mixed))

    def test_failure_binding_ignores_repair_feedback_and_code_but_not_source(self):
        key = label_execution._failure_path(self.batch, 'explanations', 'q-1')
        # Repair feedback and repository rules are instructions, not evidence: a
        # changed code fix must be able to revalidate the saved candidate.
        self.put('workflow/repair-feedback/explanations.json', {'reason': 'changed feedback'})
        self.put('plugin/rules.py', 'print("code fix")\n')
        self.assertEqual(key, label_execution._failure_path(self.batch, 'explanations', 'q-1'))
        self.put('01_source/附录.txt', '1. A 【解析】changed source.\n2. B 【解析】x')
        self.assertNotEqual(key, label_execution._failure_path(self.batch, 'explanations', 'q-1'))

    def test_source_evidence_change_releases_failed_candidate_for_new_model_call(self):
        calls = []
        original = agent_tasks._validate
        def validate(batch, stage, payload, context, units):
            if [u['unit_id'] for u in units] == ['q-1']:
                raise self.source_failure()
            return original(batch, stage, payload, context, units)
        def invoke(prompt, *a, **kw):
            units = self.selected(prompt); calls.append([u['unit_id'] for u in units])
            return {'response': self.explanations(units)}
        with patch.object(agent_tasks, '_validate', side_effect=validate):
            label_execution.run(self.batch, 'explanations', self.config, self.batch / 'run', invoke=invoke)
        self.put('01_source/附录.txt', '1. A 【解析】第一题新解析。\n2. B 【解析】第二题原解析。')
        calls.clear()
        def changed(prompt, *a, **kw):
            response = invoke(prompt, *a, **kw)
            for row in response['response']['artifacts'][agent_tasks.ARTIFACTS['explanations']]['items']:
                if row['unit_id'] == 'q-1':
                    row['explanation'] = '第一题新解析。'
            return response
        result = label_execution.run(self.batch, 'explanations', self.config, self.batch / 'run2', invoke=changed)
        self.assertEqual(calls, [['q-1', 'q-2']])
        self.assertEqual(result['response']['status'], 'completed')

    def test_timeout_is_not_content_retry(self):
        with patch.object(local_agent, 'invoke', side_effect=local_agent.AgentError('timeout', 'timeout')) as call:
            with self.assertRaises(local_agent.AgentError):
                label_execution.run(self.batch, 'answers', self.config, self.batch / 'run')
        self.assertEqual(call.call_count, 1)

    def test_explanation_ref_is_expanded_and_only_bad_unit_is_repaired(self):
        self.put(agent_tasks.ARTIFACTS['answers'], self.answers([{'numbers': [1, 2]}])['artifacts'][agent_tasks.ARTIFACTS['answers']])
        from workflow.content_fragments import source_index
        source = next(s for s in source_index(self.batch)['sources'] if s['path'] == '01_source/附录.txt')
        calls = []
        def invoke(prompt, *a, **kw):
            units = self.selected(prompt); calls.append([u['unit_id'] for u in units])
            rows = []
            for u in units:
                n = u['numbers'][0]
                line = source['lines'][n - 1]['text']
                ref = {'path': source['path'], 'sha256': source['sha256'], 'start_line': n, 'end_line': n,
                       'start_column': line.index('】') + 1}
                rows.append({'unit_id': u['unit_id'], 'explanation': {'source_ref': ref},
                             'articleSummary': '无', 'subquestions': [], 'evidence': ['附录第' + str(n) + '题']})
            if len(calls) == 1:
                rows[1]['explanation']['source_ref']['sha256'] = '0' * 64
            return {'response': {'status': 'completed', 'artifacts': {agent_tasks.ARTIFACTS['explanations']: {'items': rows}},
                                 'notes': [], 'findings': []}}
        result = label_execution.run(self.batch, 'explanations', self.config, self.batch / 'run', invoke=invoke)
        self.assertEqual(calls, [['q-1', 'q-2'], ['q-2']])
        payload = result['response']['artifacts'][agent_tasks.ARTIFACTS['explanations']]
        self.assertEqual([r['explanation'] for r in payload['items']], ['第一题原解析。', '第二题原解析。'])
        self.assertTrue(agent_tasks.accept_result(self.batch, 'explanations', result['response'])['completed'])


if __name__ == '__main__':
    unittest.main()
