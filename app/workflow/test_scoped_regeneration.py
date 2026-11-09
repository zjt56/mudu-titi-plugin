"""Real task builders and consumers with fake model calls and temporary sources."""
import contextlib
import copy
import io
import json
import unittest
from unittest.mock import patch

from workflow import agent, agent_tasks, local_agent, self_heal, unit_tasks
from workflow import test_label_execution as fixtures


class ScopedRegenerationTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.LabelExecutionTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.batch = self.fixture.batch.resolve()
        self.put = self.fixture.put
        self.config = dict(self.fixture.config, provider='command', command=['unused-model'],
                           timeout_seconds=20, retries=0)

    def response(self, stage, payload, status='completed'):
        return {'status': status, 'artifacts': {agent_tasks.ARTIFACTS[stage]: copy.deepcopy(payload)},
                'notes': [], 'findings': []}

    def baseline(self, stage):
        types = json.loads((self.batch / agent_tasks.ARTIFACTS['types']).read_text())
        if stage == 'types':
            payload = types
        elif stage == 'knowledge':
            payload = {'items': [{'unit_id': 'q-' + str(n), 'knowledge': ['词汇/词汇辨析/动词辨析'],
                                  'subquestions': [], 'evidence': ['正文第' + str(n) + '题']}
                                 for n in (1, 2)]}
        elif stage == 'audio':
            document = json.loads((self.batch / '01_source/structure.confirmed.json').read_text())
            for u in document['units']:
                u['type'] = '听力短对话理解'
            self.put('01_source/structure.confirmed.json', document)
            for row in types['items']:
                row.update(type='听力短对话理解', subtypes=['选文字'])
            self.put(agent_tasks.ARTIFACTS['types'], types)
            payload = {'items': [{'unit_id': 'q-' + str(n), 'file': '02_audio/' + str(n) + '.mp3',
                                  'start': n * 10, 'end': n * 10 + 5, 'evidence': ['真实切段证据']}
                                 for n in (1, 2)]}
            for n in (1, 2):
                self.put('02_audio/' + str(n) + '.mp3', 'fake audio bytes')
            import hashlib
            from audio.binding import listening_context
            binding, _ = listening_context(self.batch)
            self.put('02_audio/segment-evidence.json', {
                'unit_binding': binding,
                'items': [dict(r, verified=True, file_sha256=hashlib.sha256(
                    (self.batch / r['file']).read_bytes()).hexdigest()) for r in payload['items']]})
        else:
            raise AssertionError(stage)
        self.assertTrue(agent_tasks.accept_result(self.batch, stage, self.response(stage, payload))['completed'])
        return json.loads((self.batch / agent_tasks.ARTIFACTS[stage]).read_text())

    def scope(self, stage):
        self_heal._feedback(self.batch, stage, '仅重生成第一题', 'q-1 需重新核对', 1, unit_ids=['q-1'])

    def context(self, task):
        return json.loads(task['prompt'].split(unit_tasks._BEGIN, 1)[1].split(unit_tasks._END, 1)[0])

    def test_cli_scopes_types_knowledge_and_audio_and_preserves_untouched_row(self):
        for stage in ('types', 'knowledge', 'audio'):
            with self.subTest(stage=stage):
                prior = self.baseline(stage)
                self.scope(stage)
                calls = []
                def invoke(prompt, *a, **kw):
                    ctx = self.context({'prompt': prompt})
                    calls.append([u['unit_id'] for u in ctx['confirmed_units_with_stable_ids']])
                    row = copy.deepcopy(prior['items'][0])
                    row['evidence'] = ['已重新按原文核对第一题']
                    return {'response': self.response(stage, {'items': [row]})}
                with patch.object(local_agent, 'load_config', return_value=self.config), \
                     patch.object(local_agent, 'invoke', side_effect=invoke), \
                     contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(local_agent.main(['--stage', stage, '--batch-dir', str(self.batch)]), 0)
                self.assertEqual(calls, [['q-1']])
                current = json.loads((self.batch / agent_tasks.ARTIFACTS[stage]).read_text())
                self.assertEqual(current['items'][1], prior['items'][1])
                self.assertNotEqual(current['items'][0]['evidence'], prior['items'][0]['evidence'])

    def test_foreign_output_and_failed_scope_do_not_write_artifact(self):
        prior = self.baseline('types')
        self.scope('types')
        before = (self.batch / agent_tasks.ARTIFACTS['types']).read_bytes()
        with self.assertRaises(ValueError):
            agent_tasks.accept_result(self.batch, 'types', self.response('types', prior))
        report = agent_tasks.accept_result(self.batch, 'types', self.response('types', prior, 'needs_review'))
        self.assertFalse(report['completed'])
        self.assertEqual((self.batch / agent_tasks.ARTIFACTS['types']).read_bytes(), before)

    def test_invalid_untouched_row_is_refused_without_expanding_scope(self):
        prior = self.baseline('types')
        prior['items'][1]['subtypes'] = ['不存在的子类']
        self.put(agent_tasks.ARTIFACTS['types'], prior)
        self.scope('types')
        task = agent_tasks.build_task(self.batch, 'types', max_input_chars=1000000)
        self.assertEqual([u['unit_id'] for u in self.context(task)['confirmed_units_with_stable_ids']], ['q-1'])
        with self.assertRaisesRegex(ValueError, 'untouched units'):
            agent_tasks.accept_result(self.batch, 'types', self.response('types', {'items': [prior['items'][0]]}))

    def test_baseline_is_hash_bound_and_changes_inflight_reject_import(self):
        prior = self.baseline('types')
        self.scope('types')
        task = agent_tasks.build_task(self.batch, 'types', max_input_chars=1000000)
        self.assertIn(agent_tasks.ARTIFACTS['types'], task['input_hashes'])
        prior['items'][1]['words'] += 1
        self.put(agent_tasks.ARTIFACTS['types'], prior)
        with self.assertRaises(local_agent.AgentError):
            local_agent._check_input_hashes(self.batch, task['input_hashes'])

    def real_audio_map(self, multi=False, all_units=False):
        prior = self.baseline('audio')
        parent = json.loads((self.batch / '02_audio/segment-evidence.json').read_text())
        mapping = {'unit_binding': copy.deepcopy(parent['unit_binding']), 'items': [
            {k: r[k] for k in ('unit_id', 'file', 'start', 'end')} for r in prior['items']]}
        if multi:
            parent.update(generator='audio.segment/multi-v1', generation='test-generation', sources={'audio': {}})
            mapping['generation'] = parent['generation']
            self.put('02_audio/sources/audio/full-transcript.json', {'segments': []})
            for row, cut in zip(mapping['items'], parent['items']):
                source = {'source_audio': 'audio', 'source_path': str(self.batch / 'original.wav'),
                          'source_sha256': 'a' * 64}
                row.update(source)
                cut.update(source)
        self.put('02_audio/segment-evidence.json', parent)
        self.put(agent_tasks.ARTIFACTS['audio'], mapping)
        self_heal._feedback(self.batch, 'audio', '局部重生成', 'q-1 需核对', 1,
                            unit_ids=['q-1', 'q-2'] if all_units else ['q-1'])
        rows = copy.deepcopy(prior['items'] if all_units else prior['items'][:1])
        rows[0]['evidence'] = ['根据真实切段重新核对']
        report = agent_tasks.accept_result(self.batch, 'audio', self.response('audio', {'items': rows}))
        self.assertTrue(report['completed'])
        saved = json.loads((self.batch / agent_tasks.ARTIFACTS['audio']).read_text())
        self.assertEqual(saved['unit_binding'], mapping['unit_binding'])
        if multi:
            self.assertEqual(saved['generation'], mapping['generation'])
            self.assertEqual(saved['items'][0]['source_path'], mapping['items'][0]['source_path'])
        if not all_units:
            self.assertEqual(saved['items'][1], mapping['items'][1])
            self.assertNotIn('evidence', saved['items'][1])
        from audio.consistency import pair_is_consistent
        self.assertTrue(pair_is_consistent(self.batch))

    def test_real_single_audio_cutter_schema_retains_untouched_rows(self):
        self.real_audio_map()

    def test_real_multi_audio_cutter_schema_retains_raw_provenance(self):
        self.real_audio_map(multi=True)

    def test_full_audio_scope_preserves_generation_and_binding(self):
        self.real_audio_map(multi=True, all_units=True)

    def test_portable_task_and_import_use_same_scope_and_merge(self):
        from workflow import agent_protocol, manifest as mf
        prior = self.baseline('types')
        manifest = mf.new_manifest('scope-test', self.batch, {}, {})
        mf.save(self.batch, manifest)
        self.scope('types')
        with patch.object(agent_protocol, '_preconditions', return_value=[]):
            exported = agent_protocol.export_task(self.batch, 'types', max_input_chars=1000000)
            task = json.loads((self.batch / 'workflow/agent-tasks' / exported['task_id'] / 'task.json').read_text())
            self.assertEqual([u['unit_id'] for u in self.context(task)['confirmed_units_with_stable_ids']], ['q-1'])
            row = copy.deepcopy(prior['items'][0])
            row['words'] += 2
            result = self.batch / 'response.json'
            result.write_text(json.dumps({'protocol': agent_protocol.PROTOCOL, 'task_id': exported['task_id'],
                                          'result': self.response('types', {'items': [row]})}), encoding='utf-8')
            report = agent_protocol.import_result(self.batch, exported['task_id'], result)
        self.assertTrue(report['completed'])
        saved = json.loads((self.batch / agent_tasks.ARTIFACTS['types']).read_text())
        self.assertEqual(saved['items'][1], prior['items'][1])
        self.assertEqual(saved['items'][0]['words'], row['words'])

    def test_audio_scope_does_not_take_whole_map_fast_path(self):
        prior = self.baseline('audio')
        self.scope('audio')
        with patch.object(agent, '_audio_evidence_ready', side_effect=AssertionError('whole-map fast path')), \
             patch.object(agent, 'configured', return_value=True), \
             patch.object(local_agent, 'main', return_value=3) as model:
            self.assertEqual(agent.main(['run', '--stage', 'audio', '--batch-dir', str(self.batch)]), 3)
        model.assert_called_once()
        self.assertEqual(json.loads((self.batch / agent_tasks.ARTIFACTS['audio']).read_text()), prior)


if __name__ == '__main__':
    unittest.main()
