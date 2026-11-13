import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import auto, content_agent, manifest as mf


class HistoricalUpstreamTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name) / 'batch'
        self.queue = Path(self.tmp.name) / 'queue'
        self.run = self.batch / 'workflow/agent-runs/answers-abc'
        self.binding = self.run / 'round-00-part-0000/content-task.json'
        self.binding.parent.mkdir(parents=True)
        self.task_id = 'a' * 32
        self.task_path = self.queue / self.task_id / 'task.json'
        self.task_path.parent.mkdir(parents=True)
        self.state = mf.new_manifest('batch', self.batch, {}, {})
        self.state['stages']['answers'].update(status=mf.FAILED,
            started_at='2026-11-13T15:02:11+08:00', finished_at='2026-11-13T15:02:36+08:00',
            notes='Upstream HTTP/2 stream failed', input_hashes={'baseline': 'keep'}, output_hashes={'delivery': 'keep'})
        mf.save(self.batch, self.state)
        self.task = dict(protocol=content_agent.PROTOCOL, task_id=self.task_id, identity='identity',
            batch_dir=str(self.batch.resolve()), stage='answers', status='failed',
            error='Upstream HTTP/2 stream failed', accepted=False, session_id='host-session',
            attempts=[], input_hashes={'input': 'hash'}, unit_ids=['u1', 'u2'],
            created_at=1794553331.5, updated_at=1794553356.5)
        self.progress = dict(stage='answers', validated_units=[], failed_units={'u1': self.task['error'], 'u2': self.task['error']},
                             deterministic_failures={}, calls=[])
        self.binding.write_text(json.dumps({'task_id': self.task_id, 'identity': 'identity'}))
        self.save_evidence()
        for p in (patch.object(content_agent, 'QUEUE_ROOT', self.queue),
                  patch('workflow.operations.lease_active', return_value=False),
                  patch('workflow.local_agent._check_input_hashes')):
            p.start()
            self.addCleanup(p.stop)
        self.bridge = auto.load_bridge()

    def save_evidence(self):
        self.task_path.write_text(json.dumps(self.task))
        (self.run / 'repair-progress-00.json').write_text(json.dumps(self.progress))

    def check(self):
        return auto._requeue_historical_upstream(self.batch, mf.load(self.batch), self.bridge)

    def test_bound_failure_requeues_once_and_preserves_baseline(self):
        self.assertTrue(self.check())
        record = mf.load(self.batch)['stages']['answers']
        self.assertEqual(record['status'], mf.PENDING)
        self.assertEqual(record['input_hashes'], {'baseline': 'keep'})
        self.assertEqual(record['output_hashes'], {'delivery': 'keep'})
        self.assertGreater(auto._read_cooldown(self.batch), 0)
        self.assertFalse(self.check())
        self.assertEqual(json.loads(self.task_path.read_text()), self.task)

    def test_ambiguous_or_mixed_evidence_fails_closed(self):
        cases = [
            ('candidate', lambda: self.task.update(candidate={})),
            ('untrusted error', lambda: self.task.update(error='other')),
            ('no host', lambda: self.task.update(session_id=None)),
            ('wrong identity', lambda: self.task.update(identity='wrong')),
            ('wrong batch', lambda: self.task.update(batch_dir='/other')),
            ('active', lambda: self.task.update(status='running')),
            ('unknown write', lambda: self.task.update(response={})),
            ('deterministic', lambda: self.progress.update(deterministic_failures={'u1': {}})),
            ('mixed', lambda: self.progress['failed_units'].update(u1='bad output')),
            ('scope', lambda: self.task.update(unit_ids=['u1'])),
            ('partial', lambda: self.progress.update(validated_units=['u3'])),
            ('calls', lambda: self.progress.update(calls=[{}])),
            ('stale attempt', lambda: self.task.update(created_at=1794553300)),
        ]
        original_task = dict(self.task)
        original_progress = json.loads(json.dumps(self.progress))
        for name, modify in cases:
            with self.subTest(name=name):
                self.task = dict(original_task)
                self.progress = json.loads(json.dumps(original_progress))
                modify()
                self.save_evidence()
                self.assertFalse(self.check())
                self.assertEqual(mf.load(self.batch)['stages']['answers']['status'], mf.FAILED)
                self.assertEqual(auto._read_cooldown(self.batch), 0)

    def test_lease_and_changed_input_are_refused(self):
        with patch('workflow.operations.lease_active', return_value=True):
            self.assertFalse(self.check())
        with patch('workflow.local_agent._check_input_hashes', side_effect=ValueError('changed')):
            self.assertFalse(self.check())
        self.assertEqual(mf.load(self.batch)['stages']['answers']['status'], mf.FAILED)

    def test_multiple_attempts_are_ambiguous(self):
        (self.batch / 'workflow/agent-runs/answers-another').mkdir()
        self.assertFalse(self.check())


if __name__ == '__main__':
    unittest.main()
