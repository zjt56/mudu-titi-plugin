"""Legacy transient incident migration, using only disposable batch directories."""
import time
import unittest
from unittest.mock import patch

from workflow import operations as op
from workflow import test_operations as fixtures


class TransientMigrationTests(unittest.TestCase):
    put = fixtures.OperationsTests.put

    def setUp(self):
        fixtures.OperationsTests.setUp(self)
        self.manifest['stages']['answers'] = {'status': 'failed'}
        self.put('workflow/manifest.json', self.manifest)
        self.detail = '；'.join(f'题{i}：Upstream HTTP/2 stream failed' for i in range(1, 20))
        self.candidate.return_value = ('answers', self.detail)
        self.put('workflow/self-heal.json', {'events': []})
        op.service_heartbeat(True)
        self.addCleanup(op.service_heartbeat, False)

    def legacy(self, status='queued', detail=None, receipts=None, **fields):
        incident = {'incident_id': 'old', 'stage': 'answers', 'status': status,
                    'eligible': status == 'queued', 'detail': detail or self.detail,
                    'signature': 'old-signature', 'events': [{'action': 'old'}],
                    'attempts': [{'outcome': 'failed'}], **fields}
        op._save(self.folder, {'identity': op._identity(self.folder), 'incident': incident,
                               'receipts': receipts or [], 'failures': {}})

    def test_handoff_persists_retirement_and_preserves_unknown_receipt(self):
        receipt = {'request_id': 'unknown', 'status': 'unknown', 'evidence': None}
        self.legacy(receipts=[receipt])
        state = op._load(self.folder)
        state['resume_outbox'] = {'request_id': 'unknown'}
        op._save(self.folder, state)
        self.assertIsNone(op.handoff_if_available(self.folder))
        state = op._load(self.folder)
        self.assertEqual(state['incident']['status'], 'transient_deferred')
        self.assertFalse(state['incident']['eligible'])
        self.assertEqual(state['receipts'], [receipt])
        self.assertEqual(state['resume_outbox'], {'request_id': 'unknown'})
        self.assertEqual(state['incident']['events'][0], {'action': 'old'})
        self.assertEqual(state['incident']['attempts'], [{'outcome': 'failed'}])
        self.assertFalse(op.pending_incident(self.folder))
        self.assertIsNone(op.handoff_if_available(self.folder))
        self.assertEqual(len(op._load(self.folder)['incident']['events']), 2)

    def test_waiting_and_expired_lease_retire_but_live_lease_does_not(self):
        for status, expiry, expected in [('waiting_evidence', 0, 'transient_deferred'),
                                         ('waiting_external', 0, 'transient_deferred'),
                                         ('claimed', time.time() - 1, 'transient_deferred'),
                                         ('claimed', time.time() + 300, 'claimed')]:
            with self.subTest(status=status, expected=expected):
                self.legacy(status, lease_expires_at=expiry, execution_process=op._process_identity())
                self.assertEqual(op.view(self.root, self.folder)['incident']['status'], expected)

    def test_mixed_diagnostic_is_not_retired(self):
        self.legacy(detail=self.detail + '；题20：答案不匹配')
        self.assertEqual(op.view(self.root, self.folder)['incident']['status'], 'queued')

    def test_changed_real_blocker_creates_new_incident_and_keeps_old_audit(self):
        receipt = {'request_id': 'unknown', 'status': 'unknown', 'evidence': None}
        self.legacy(receipts=[receipt])
        self.candidate.return_value = ('answers', '题20：答案不匹配')
        with patch.object(op.self_heal, '_retry_evidence', return_value={'input': 'new'}):
            result = op.view(self.root, self.folder)
        self.assertEqual(result['incident']['status'], 'queued')
        self.assertNotEqual(result['incident']['incident_id'], 'old')
        state = op._load(self.folder)
        self.assertEqual(state['incident_history'][0]['status'], 'transient_deferred')
        self.assertEqual(state['receipts'], [receipt])
        self.assertEqual(op.incidents(self.root)['incidents'], [])
        self.assertIsNone(op.handoff_if_available(self.folder))
        with self.assertRaisesRegex(ValueError, 'unknown_receipt_quarantine'):
            op.claim(self.root, self.folder, {'incident_id': result['incident']['incident_id'], 'session_id': 's'})

    def test_changed_current_stage_never_reuses_old_failure(self):
        self.legacy()
        self.manifest['stages']['answers']['status'] = 'completed'
        self.manifest['stages']['validate']['status'] = 'running'
        self.put('workflow/manifest.json', self.manifest)
        self.candidate.return_value = ('validate', 'broken')
        with patch.object(op, '_verify', side_effect=AssertionError('must not claim resolved')):
            result = op.view(self.root, self.folder)
        self.assertEqual(result['incident']['status'], 'transient_deferred')
        self.assertEqual(result['incident']['incident_id'], 'old')
        self.assertNotIn('verification', result['incident'])


if __name__ == '__main__':
    unittest.main()
