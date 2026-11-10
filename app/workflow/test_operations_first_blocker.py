"""First-blocker admission uses safe scheduler checkpoints, never weakened locks."""
import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import operations as op, self_heal, supervisor, auto
from workflow import test_operations as fixtures


class FirstBlockerTests(unittest.TestCase):
    put = fixtures.OperationsTests.put

    def setUp(self):
        fixtures.OperationsTests.setUp(self)
        self.put('workflow/self-heal.json', {'events': []})
        self.addCleanup(patch.stopall)
        patch.object(op, '_service_until', 0.0).start()
        patch.object(self_heal, '_retry_evidence', return_value={'inputs': {'source': 'same'}, 'code': 'code'}).start()

    def test_first_failure_admitted_without_waiting_for_self_heal(self):
        op.service_heartbeat(True)
        found = op.incidents(self.root)['incidents']
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['trigger'], 'first_blocker')
        self.assertEqual(found[0]['self_heal_failures'], 0)
        again = op.incidents(self.root)['incidents'][0]
        self.assertEqual(again['incident_id'], found[0]['incident_id'])
        self.assertEqual(again['queue_generation'], 1)

    def test_completed_structure_with_unusable_delivery_also_admits_first_fault(self):
        self.manifest['stages']['structure_gate'] = {'status': 'completed'}
        self.put('workflow/manifest.json', self.manifest)
        self.candidate.return_value = ('structure_gate', 'confirmed structure missing')
        op.service_heartbeat(True)
        with patch.object(self_heal, '_structure_unusable', return_value='confirmed structure missing'):
            handoff = op.handoff_if_available(self.folder)
        self.assertEqual(handoff['stage'], 'structure_gate')
        self.assertEqual(op._load(self.folder)['incident']['trigger'], 'first_blocker')

    def test_service_disabled_or_stale_falls_back_to_legacy(self):
        self.assertEqual(op.incidents(self.root)['incidents'], [])
        with patch.object(op.time, 'monotonic', return_value=100):
            op.service_heartbeat(True)
            self.assertTrue(op.service_available())
        with patch.object(op.time, 'monotonic', return_value=121):
            self.assertFalse(op.service_available())
            self.assertIsNone(op.handoff_if_available(self.folder))
        op.service_heartbeat(False)
        self.assertFalse(op.service_available())

    def test_transient_wait_and_quality_output_do_not_create_incident(self):
        op.service_heartbeat(True)
        for stage, detail in [('validate', 'rate_limit 429'), ('validate', '额度冷却'),
                              ('review_gate', 'quality differences')]:
            self.candidate.return_value = (stage, detail)
            self.assertEqual(op.incidents(self.root)['incidents'], [])

    def test_handoff_under_scheduler_lock_queues_once_but_claim_still_requires_lock(self):
        op.service_heartbeat(True)
        with supervisor._try_file_lock(supervisor._auto_lock_path(self.folder)) as owned:
            self.assertTrue(owned)
            handoff = op.handoff_if_available(self.folder)
            self.assertEqual(handoff['status'], 'operations_pending')
            self.assertEqual(op.handoff_if_available(self.folder)['incident_id'], handoff['incident_id'])
            self.assertFalse(op.lease_active(self.folder), 'queued incident is not a repair lease')
            with self.assertRaisesRegex(ValueError, 'worker_busy'):
                op.claim(self.root, self.folder, {'incident_id': handoff['incident_id'], 'session_id': 'early'})
        claimed = op.claim(self.root, self.folder, {'incident_id': handoff['incident_id'], 'session_id': 'early'})
        self.assertEqual(claimed['status'], 'claimed')
        self.assertTrue(op.lease_active(self.folder))

    def test_terminal_advice_is_retained_without_rebilling_same_fault(self):
        op.service_heartbeat(True)
        found = op.incidents(self.root)['incidents'][0]
        state = op._load(self.folder)
        state['incident'].update(status='needs_deep_repair', eligible=False,
                                 findings=[{'summary': 'check DOCX glyph mapping'}])
        op._save(self.folder, state)
        self.assertEqual(op.incidents(self.root)['incidents'], [])
        handoff = op.handoff_if_available(self.folder)
        self.assertEqual(handoff['incident_id'], found['incident_id'])
        self.assertEqual(op._load(self.folder)['incident']['findings'][0]['summary'], 'check DOCX glyph mapping')

    def test_legacy_terminal_fault_keeps_identity_and_budgets_when_service_comes_online(self):
        import hashlib
        evidence = json.dumps(self_heal._retry_evidence(self.folder, self.manifest, 'validate'), sort_keys=True)
        signature = hashlib.sha256(('validate\nbroken\n' + evidence).encode()).hexdigest()[:16]
        self.put('workflow/self-heal.json', {'events': [{'stage': 'validate', 'detail': 'broken',
            'signature': signature, 'outcome': 'repair_failed', 'attempt': 1}]})
        found = op.incidents(self.root)['incidents'][0]
        state = op._load(self.folder)
        state['incident'].update(status='needs_human', eligible=False, session_id='legacy-session',
                                 attempts=[{'name': 'repair', 'outcome': 'failed'}])
        op._save(self.folder, state)
        op.service_heartbeat(True)
        self.assertEqual(op.incidents(self.root)['incidents'], [])
        current = op._load(self.folder)['incident']
        self.assertEqual(current['incident_id'], found['incident_id'])
        self.assertEqual(current['session_id'], 'legacy-session')
        self.assertEqual(len(current['attempts']), 1)

    def test_current_handoff_is_not_bypassed_by_old_heal_input_hashes(self):
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(auto.contracts, 'STAGES', (auto.contracts.STAGE_BY_ID['validate'],)))
            stack.enter_context(patch.object(auto.mf, 'reconcile_deliveries'))
            stack.enter_context(patch.object(auto.mf, 'input_hashes', return_value={'source': 'new'}))
            stack.enter_context(patch.object(auto, '_read_cooldown', return_value=0))
            stack.enter_context(patch.object(auto, '_result', return_value={}))
            stack.enter_context(patch.object(self_heal, '_load', return_value={'last_inputs': {'validate': {'source': 'old'}}}))
            stack.enter_context(patch.object(op, 'handoff_if_available', return_value={
                'status': 'operations_pending', 'stage': 'validate', 'changed': False,
                'reason': 'AI运维分析'}))
            validate = stack.enter_context(patch.object(auto.rn, 'do_validate'))
            recover = stack.enter_context(patch.object(self_heal, 'recover'))
            result = auto._advance_locked(self.folder, {}, stop_at=None)
            recover.assert_not_called()
            validate.assert_not_called()
            self.assertEqual(result['pending_stages'][0]['action'], 'operations_pending')

    def test_auto_uses_handoff_instead_of_model_heal_and_retains_fallback(self):
        # Exercise the real scheduler; isolate unrelated execution/model work.
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(auto.contracts, 'STAGES', ()))
            stack.enter_context(patch.object(auto.mf, 'reconcile_deliveries'))
            stack.enter_context(patch.object(auto, '_result', return_value={}))
            stack.enter_context(patch.object(self_heal, '_load', return_value={}))
            handoff = stack.enter_context(patch.object(op, 'handoff_if_available', return_value={
                'status': 'operations_pending', 'stage': 'validate', 'changed': False}))
            recover = stack.enter_context(patch.object(self_heal, 'recover', return_value={
                'changed': False, 'status': 'idle'}))
            auto._advance_locked(self.folder, {}, stop_at='extract')
            recover.assert_not_called()
            handoff.assert_called_once_with(self.folder)
            handoff.return_value = None
            auto._advance_locked(self.folder, {}, stop_at='extract')
            recover.assert_called_once_with(self.folder)


if __name__ == '__main__':
    unittest.main()
