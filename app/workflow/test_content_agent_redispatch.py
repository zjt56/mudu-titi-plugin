"""Native transient redispatch uses only private temporary receipts, never a model."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import content_agent as broker, local_agent


class RedispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.queue = self.root / 'queue'
        self.queue_patch = patch.object(broker, 'QUEUE_ROOT', self.queue)
        self.queue_patch.start()
        self.addCleanup(self.queue_patch.stop)
        self.config = {'native_agent': {'enabled': True, 'stages': ['knowledge']},
                       '_content_batch_dir': str(self.root), '_content_input_hashes': {'source': 'hash'},
                       '_content_route_identity': {'provider': 'p', 'model': 'm'}}

    def task(self, uid='a' * 32, **updates):
        task = {'protocol': broker.PROTOCOL, 'identity': 'same', 'task_id': uid,
                'status': 'failed', 'session_id': 'host', 'error': 'Upstream HTTP/2 stream failed',
                'accepted': False, 'attempts': [], 'updated_at': 100}
        task.update(updates)
        broker._write(broker._task_path(uid), task)
        return task

    def invoke(self):
        with patch.object(broker, '_context', return_value=({}, ['u'], 'knowledge')), \
             patch.object(broker, '_digest', return_value='same'), \
             patch.object(broker, '_child_prompt', return_value='child prompt'), \
             patch.object(broker.content_media, 'build_snapshot', return_value={'input_hashes': {}, 'media': []}), \
             patch.object(local_agent, '_check_input_hashes'), \
             patch.object(broker.agent_tasks, 'confirmed_units', return_value=[{'unit_id': 'u'}]), \
             patch.object(broker, '_guard'), patch.object(broker, '_check_route'), \
             patch.object(broker.time, 'time', return_value=1000), \
             patch('workflow.model_admission.require_model_call'), \
             patch.object(broker.time, 'sleep', side_effect=self.fail_host):
            with self.assertRaises(local_agent.AgentError):
                broker.invoke('prompt', self.root / 'run', self.config)
        return broker._read(self.root / 'run' / 'content-task.json')

    def fail_host(self, _delay):
        task, _ = broker._identity_receipt('same')
        task.update(status='failed', session_id='host', error='Upstream HTTP/2 stream failed', updated_at=1000)
        broker._write(broker._task_path(task['task_id']), task)

    def test_invoke_creates_one_new_receipt_then_reuses_during_cooldown(self):
        old = self.task()
        binding = self.invoke()
        self.assertNotEqual(binding['task_id'], old['task_id'])
        new = broker._read(broker._task_path(binding['task_id']))
        self.assertEqual(new['redispatch_of'], old['task_id'])
        self.assertEqual(broker._read(broker._task_path(old['task_id'])), old)
        self.assertEqual(self.invoke()['task_id'], binding['task_id'])
        self.assertEqual(len(list(self.queue.glob('*/task.json'))), 2)

    def test_rejects_untrusted_or_ambiguous_failures(self):
        base = self.task()
        for changes in ({'status': 'cancelled'}, {'session_id': None},
                        {'error': 'Upstream HTTP/2 stream failed extra'}, {'error_code': 'validation'},
                        {'attempts': ['candidate-0001.json']}, {'candidate': None},
                        {'accepted': True}, {'response': None}, {'feedback': {}}, {'updated_at': float('nan')}):
            with self.subTest(changes=changes):
                self.assertFalse(broker._retryable_upstream_task(dict(base, **changes)))
        (broker._task_path(base['task_id']).parent / 'candidate-0001.json').write_text('{}')
        self.assertFalse(broker._retryable_upstream_task(base))

    def test_bounded_chain_and_ambiguous_receipts(self):
        previous = self.task()
        for index in range(1, 4):
            current = self.task(uid=f'{index:032x}', redispatch_of=previous['task_id'])
            previous = current
        current, count = broker._identity_receipt('same')
        self.assertEqual((current['task_id'], count), (previous['task_id'], 3))
        self.assertEqual(self.invoke()['task_id'], previous['task_id'])
        self.task(uid='f' * 32)
        self.assertEqual(broker._identity_receipt('same')[1], broker.UPSTREAM_MAX_REDISPATCHES)


if __name__ == '__main__':
    unittest.main()
