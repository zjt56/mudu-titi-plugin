import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import auto, contracts, manifest as mf, agent_protocol, agent_tasks, local_agent


class UpstreamCooldownTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.batch = Path(self.temp.name) / 'batch'
        state = mf.new_manifest('batch', self.batch, {}, {})
        for stage in ('extract', 'structure_gate', 'preassemble', 'types'):
            mf.touch_stage(state, stage, mf.COMPLETED)
        mf.save(self.batch, state)
        p = patch.object(mf, 'dependency_satisfied', return_value=True)
        p.start()
        self.addCleanup(p.stop)

    @staticmethod
    def failure(message):
        return f'Agent subprocess failed (exit 1, exit)：{message}；完整日志见 stderr.log'

    def test_only_trusted_subprocess_error_is_recognized(self):
        for message in ('Upstream HTTP/2 stream failed', 'Upstream service temporarily unavailable'):
            self.assertTrue(auto._is_upstream_call_failure(self.failure(message)))
            self.assertTrue(auto._is_upstream_call_failure(json.dumps({'code': 'exit', 'error': self.failure(message)})))
            for text in (message, '{"message": "' + message + '"}',
                         'validation failed: ' + message,
                         self.failure('validation failed: ' + message),
                         self.failure('{"answer": "' + message + '"}')):
                self.assertFalse(auto._is_upstream_call_failure(text), text)

    def test_parallel_upstream_waits_but_independent_delivery_proceeds(self):
        for message in ('Upstream HTTP/2 stream failed', 'Upstream service temporarily unavailable'):
            with self.subTest(message=message):
                self._parallel_case(message, False)
                auto._clear_cooldown(self.batch)

    def test_native_fatal_exception_requires_matching_host_receipt(self):
        from workflow import content_agent
        run = self.batch / 'run'
        attempt = run / 'round-00-part-0000'
        attempt.mkdir(parents=True)
        queue = self.batch / 'queue'
        task_id = 'a' * 32
        task = queue / task_id / 'task.json'
        task.parent.mkdir(parents=True)
        identity = 'bound-identity'
        message = 'Upstream HTTP/2 stream failed'
        (attempt / 'content-task.json').write_text(json.dumps({'task_id': task_id, 'identity': identity}))
        receipt = {'protocol': content_agent.PROTOCOL, 'task_id': task_id, 'identity': identity,
                   'batch_dir': str(self.batch.resolve()), 'stage': 'answers',
                   'status': 'failed', 'error': message}
        task.write_text(json.dumps(receipt))
        artifacts = {'task': str(task), 'attempt_dir': str(attempt)}
        with patch.object(content_agent, 'QUEUE_ROOT', queue):
            self.assertTrue(auto._is_native_upstream_failure('failed', message, artifacts, self.batch, 'answers', run))
            receipt['candidate'] = {'notes': [message]}
            task.write_text(json.dumps(receipt))
            self.assertFalse(auto._is_native_upstream_failure('failed', message, artifacts, self.batch, 'answers', run))
            del receipt['candidate']
            receipt['batch_dir'] = '/other/batch'
            task.write_text(json.dumps(receipt))
            self.assertFalse(auto._is_native_upstream_failure('failed', message, artifacts, self.batch, 'answers', run))

    def test_mixed_deterministic_failure_is_not_hidden(self):
        self._parallel_case('Upstream HTTP/2 stream failed', True)

    def test_serial_bridge_cools_and_runs_independent_local_stage(self):
        from workflow import runner
        stages = [contracts.STAGE_BY_ID['answers'], contracts.STAGE_BY_ID['knowledge']]
        state = mf.load(self.batch)
        bridge = auto.load_bridge()
        def execute(batch, manifest, stage):
            note = json.dumps({'code': 'exit', 'error': self.failure('Upstream service temporarily unavailable')})
            mf.touch_stage(manifest, stage.id, mf.FAILED, notes=note, exit_code=1)
            mf.save(batch, manifest)
            return 1
        def local(batch, stage):
            current = mf.load(batch)
            mf.touch_stage(current, stage.id, mf.COMPLETED)
            mf.save(batch, current)
            return 0, {}
        with patch.object(contracts, 'STAGES', stages), \
             patch.object(mf, 'reconcile_deliveries'), \
             patch('workflow.operations.lease_active', return_value=False), \
             patch('workflow.operations.handoff_if_available', return_value=None), \
             patch('workflow.self_heal.recover', return_value={}), \
             patch.object(auto, 'PARALLEL_AGENTS_ENABLED', False), \
             patch.object(auto, '_auto_prepare_audio', return_value={'status': 'waiting'}), \
             patch.object(runner, '_skip_agent_stage', return_value=False), \
             patch.object(runner, '_execute_command', side_effect=execute) as command, \
             patch.object(auto, '_cooldown_preflight', side_effect=local), \
             patch.object(auto, '_result', side_effect=lambda b, m, s, r, a: {'action': a, 'reason': r}):
            result = auto._advance_impl(self.batch, bridge, audio_worker=None)
        current = mf.load(self.batch)
        self.assertEqual(command.call_count, 1)
        self.assertEqual(current['stages']['answers']['status'], mf.PENDING)
        self.assertEqual(current['stages']['knowledge']['status'], mf.COMPLETED)
        self.assertEqual(result['action'], 'cooldown')
        self.assertGreater(result['cooldown_seconds'], 0)
        self.assertLessEqual(result['cooldown_seconds'], auto.RATE_LIMIT_BACKOFF_SECONDS)
        self.assertFalse(result['failed_stages'])

    def _parallel_case(self, message, deterministic):
        state = mf.load(self.batch)
        for stage in ('answers', 'knowledge'):
            mf.touch_stage(state, stage, mf.PENDING)
        mf.save(self.batch, state)
        def export(batch, stage, **kwargs):
            package = batch / 'packages' / stage
            package.mkdir(parents=True, exist_ok=True)
            (package / 'task.json').write_text(json.dumps({'prompt': stage + '\nFILE PROTOCOL: test'}))
            return {'status': 'ready', 'task_id': stage, 'package_dir': str(package)}
        def label(batch, stage, config, run_dir, **kwargs):
            (run_dir / 'repair-progress-00.json').write_text(json.dumps({
                'deterministic_failures': {'unit-1': {'diagnostics': 'bad source'}} if deterministic else {}}))
            raise local_agent.AgentError(self.failure(message), 'exit')
        def deliver(batch, stage, path):
            state = mf.load(batch)
            mf.touch_stage(state, stage, mf.COMPLETED)
            mf.save(batch, state)
            return {'completed': True}
        with patch('workflow.stage_preflight.run', return_value=None), \
             patch('workflow.agent_prepare.prepare_answers'), \
             patch.object(agent_protocol, 'export_task', side_effect=export), \
             patch.object(agent_tasks, 'build_task', side_effect=lambda b, s, **kw: {'status': 'ready', 'prompt': s}), \
             patch.object(agent_tasks, 'accept_result'), \
             patch('workflow.label_execution.run', side_effect=label), \
             patch.object(local_agent, 'invoke', return_value={'response': {'status': 'completed'}}), \
             patch.object(agent_protocol, 'import_result', side_effect=deliver):
            result = auto._parallel_ready_agents(self.batch, [contracts.STAGE_BY_ID[s] for s in ('answers', 'knowledge')],
                                                {'max_input_chars': 10000, 'retries': 0})
        state = mf.load(self.batch)
        self.assertEqual(result['knowledge'], 0)
        self.assertEqual(state['stages']['knowledge']['status'], mf.COMPLETED)
        self.assertEqual(result['answers'], 1 if deterministic else 4)
        self.assertEqual(state['stages']['answers']['status'], mf.FAILED if deterministic else mf.PENDING)
        self.assertEqual(auto._read_cooldown(self.batch) > 0, not deterministic)


if __name__ == '__main__':
    unittest.main()
