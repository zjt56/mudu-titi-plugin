"""Autonomous recovery admission, fencing and original consumer verification."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import operations as op


class AutonomousOperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'workflows'
        self.folder = self.root / 'paper'
        (self.folder / 'workflow').mkdir(parents=True)
        self.put('workflow/manifest.json', {'inputs': {}, 'stages': {'validate': {'status': 'failed'}}})
        self.put('workflow/self-heal.json', {'events': [{'at': 'now', 'stage': 'validate',
                 'detail': 'check E42 failed', 'signature': 'original', 'outcome': 'repair_failed'}]})
        self.candidate = patch.object(op.self_heal, '_candidate', return_value=('validate', 'check E42 failed')).start()
        self.snapshot = patch.object(op, '_snapshot', return_value='initial').start()
        self.evidence = patch.object(op, '_evidence', return_value='inputs-code-v1').start()
        self.addCleanup(patch.stopall)

    def put(self, path, data):
        target = self.folder / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data), encoding='utf-8')

    def claim(self):
        incident = op.incidents(self.root)['incidents'][0]
        return op.claim(self.root, self.folder, {'incident_id': incident['incident_id'], 'session_id': 'session'})

    def action(self, claimed, name, data=None, request='req', **kwargs):
        payload = {key: claimed[key] for key in ('incident_id', 'session_id', 'attempt_id')}
        return op.action(self.root, self.folder, {**payload, 'action': name, 'input': data or {}, 'request_id': request}, **kwargs)

    def ready(self):
        state = op._load(self.folder)
        state['incident']['not_before'] = 0
        op._save(self.folder, state)

    def test_legacy_dispositions_migrate_to_bounded_recovery(self):
        for outcome in ('needs_human', 'needs_deep_repair'):
            with self.subTest(outcome=outcome):
                claimed = self.claim()
                state = op._load(self.folder)
                state['incident']['status'] = outcome
                op._save(self.folder, state)
                viewed = op.view(self.root, self.folder)['incident']
                self.assertEqual(viewed['status'], 'queued')
                self.assertFalse(viewed['recovery']['requires_human'])
                self.assertEqual(viewed['recovery']['next_actions'], ['request_code_repair'])
                self.assertNotIn('verification', viewed)
                (self.folder / 'workflow/operations.json').unlink()

    def test_idle_investigation_must_execute_offered_repair_instead_of_waiting(self):
        claimed = self.claim()
        first = self.action(claimed, 'finish', {'outcome': 'needs_human', 'summary': 'code defect'})
        self.assertEqual(first['evidence']['outcome'], 'recovery_queued')
        self.assertFalse(first['evidence']['verified'])
        self.ready()
        claimed = self.claim()
        self.action(claimed, 'record_finding', {'summary': 'same finding'}, request='finding')
        result = self.action(claimed, 'finish', {'outcome': 'recovery_queued', 'summary': 'same defect'}, request='finish2')
        self.assertEqual(result['status'], 'failed')
        self.assertIn('autonomous_action_required', result['evidence']['error'])
        state = op._load(self.folder)
        self.assertEqual(state['incident']['status'], 'claimed')
        self.assertEqual(state['incident']['recovery']['next_actions'], ['code_repair'])
        self.assertEqual(op._code_record(self.folder)['status'], 'workspace_ready')
        # A real failed test then allows a bounded recovery round; another idle
        # finish after that round waits for actual changed input/code evidence.
        op._attempt(state['incident'], 'code_test', 'isolated_tests', 'workspace-v1', 'failed')
        op._save(self.folder, state)
        self.action(claimed, 'finish', {'outcome': 'recovery_queued', 'summary': 'failed test'}, request='finish3')
        self.ready()
        claimed = self.claim()
        self.action(claimed, 'finish', {'outcome': 'recovery_queued', 'summary': 'same failed test'}, request='finish4')
        self.assertEqual(op._load(self.folder)['incident']['status'], 'waiting_evidence')
        self.assertEqual(op.incidents(self.root)['incidents'], [])
        self.evidence.return_value = 'inputs-code-v2'
        self.assertEqual(len(op.incidents(self.root)['incidents']), 1)

    def test_restart_discovery_reclaims_live_old_process_lease_immediately(self):
        old = self.claim()
        with patch.object(op, '_process_identity', return_value={'pid': 99, 'generation': 'new'}):
            self.assertFalse(op.lease_active(self.folder))
            discovered = op.incidents(self.root)['incidents']
            self.assertEqual(len(discovered), 1)
            fresh = op.claim(self.root, self.folder, {'incident_id': old['incident_id'], 'session_id': 'replacement'})
            self.assertNotEqual(fresh['attempt_id'], old['attempt_id'])
            with self.assertRaisesRegex(ValueError, 'stale_lease'):
                self.action(old, 'progress', request='old')

    def test_unknown_receipt_never_becomes_automatic_replay(self):
        claimed = self.claim()
        state = op._load(self.folder)
        state['receipts'].append({'request_id': 'interrupted', 'status': 'unknown', 'fingerprint': 'bound'})
        state['incident']['execution_process'] = {'pid': -1, 'generation': 'dead'}
        op._save(self.folder, state)
        self.assertEqual(op.incidents(self.root)['incidents'], [])
        with self.assertRaisesRegex(ValueError, 'unknown_receipt_quarantine'):
            op.claim(self.root, self.folder, {'incident_id': claimed['incident_id'], 'session_id': 'replacement'})
        self.assertEqual(op._load(self.folder)['receipts'][0]['status'], 'unknown')

    def test_budget_exhaustion_waits_for_real_evidence_and_preserves_history(self):
        claimed = self.claim()
        state = op._load(self.folder)
        state['incident']['attempts'] = [{'kind': 'repair', 'name': str(i), 'outcome': 'failed'}
                                         for i in range(op.BUDGET_ATTEMPTS)]
        op._save(self.folder, state)
        self.action(claimed, 'finish', {'outcome': 'recovery_queued', 'summary': 'budget exhausted'})
        self.assertEqual(op.incidents(self.root)['incidents'], [])
        self.evidence.return_value = 'changed-original-or-code'
        fresh = op.incidents(self.root)['incidents'][0]
        self.assertEqual(op._budgets(fresh)['attempts'], 0)
        self.assertEqual(op._budgets(fresh)['attempts_total'], op.BUDGET_ATTEMPTS)
        self.assertEqual(len(fresh['attempts']), op.BUDGET_ATTEMPTS)

    def test_original_execution_verification_is_required_to_resolve(self):
        claimed = self.claim()
        self.action(claimed, 'resume', enqueue=lambda expected: {'queued': True})
        with patch.object(op, '_verify', return_value={'verified': False, 'stage': 'validate'}) as verify:
            op.observe_execution(self.folder)
            verify.assert_called_once()
        self.assertNotEqual(op._load(self.folder)['incident']['status'], 'resolved')
        self.ready()
        claimed = self.claim()
        self.action(claimed, 'resume', request='resume2', enqueue=lambda expected: {'queued': True})
        with patch.object(op, '_verify', return_value={'verified': True, 'stage': 'validate', 'evidence_id': 'real'}):
            op.observe_execution(self.folder)
        self.assertEqual(op._load(self.folder)['incident']['status'], 'resolved')

    def test_existing_review_difference_incident_is_retired_without_mutation(self):
        self.claim()
        state = op._load(self.folder)
        state['incident']['stage'] = 'review'
        state['incident']['status'] = 'needs_human'
        op._save(self.folder, state)
        self.put('workflow/manifest.json', {'inputs': {}, 'stages': {'review_gate': {
            'status': 'needs_review', 'execution_completed': True,
            'continue_unqualified': True, 'release_ready': False}}})
        manifest = (self.folder / 'workflow/manifest.json').read_bytes()
        with patch('workflow.review.evaluate_rounds', return_value={'execution_completed': True}):
            incident = op.view(self.root, self.folder)['incident']
        self.assertEqual(incident['status'], 'quality_differences')
        self.assertEqual(incident['failure_class'], 'quality_differences')
        self.assertFalse(op.pending_incident(self.folder))
        self.assertEqual(manifest, (self.folder / 'workflow/manifest.json').read_bytes())

    def test_verify_before_original_execution_does_not_discard_installed_fix(self):
        claimed = self.claim()
        record = {'job_id': 'job', 'path': 'unused', 'status': 'restarted_pending_verify'}
        with patch.object(op, '_code_record', return_value=record), \
             patch.object(op, '_verify', return_value={'verified': False}), \
             patch.object(op, '_settle_code_repair') as settle:
            result = self.action(claimed, 'verify')
        self.assertFalse(result['evidence']['verified'])
        settle.assert_not_called()
        self.assertIn('resume:retry_stage_with_fresh_context', op._load(self.folder)['incident']['recovery']['next_actions'])

    def test_discovery_resolves_consumed_stage_without_another_model_round(self):
        self.claim()
        original = op._load(self.folder)
        original['incident']['findings'] = [{'summary': 'historical guess must remain evidence only'}]
        self.put('workflow/manifest.json', {'inputs': {}, 'stages': {'validate': {
            'status': 'completed', 'input_hashes': {}}}})
        manifest = (self.folder / 'workflow/manifest.json').read_bytes()
        for status in ('queued', 'waiting_evidence', 'needs_human', 'needs_deep_repair', 'awaiting_execution'):
            with self.subTest(status=status):
                state = json.loads(json.dumps(original))
                state['incident']['status'] = status
                op._save(self.folder, state)
                with patch.object(op.mf, 'is_stale', return_value=(False, '')), \
                     patch.object(op.mf, 'missing_produces', return_value=[]), \
                     patch.object(op.mf, 'input_hashes', return_value={}), \
                     patch('workflow.handoff.inspect_stage', return_value={'passed': True}), \
                     patch.object(op.self_heal, '_candidate', return_value=(None, '')), \
                     patch.object(op, '_settle_code_repair') as settle:
                    result = op.view(self.root, self.folder)['incident']
                self.assertEqual(result['status'], 'resolved')
                self.assertTrue(result['verification']['verified'])
                self.assertEqual(result['findings'], original['incident']['findings'])
                settle.assert_called_once_with(self.folder, result, 'resolved')
        self.assertEqual(manifest, (self.folder / 'workflow/manifest.json').read_bytes())

    def test_discovery_completed_status_with_stale_or_invalid_consumers_cannot_resolve(self):
        self.claim()
        state = op._load(self.folder)
        state['incident']['status'] = 'queued'
        self.put('workflow/manifest.json', {'inputs': {}, 'stages': {'validate': {
            'status': 'completed', 'input_hashes': {}}}})
        for stale, passed in ((True, True), (False, False)):
            with self.subTest(stale=stale, passed=passed):
                op._save(self.folder, state)
                with patch.object(op.mf, 'is_stale', return_value=(stale, 'stale inputs')), \
                     patch.object(op.mf, 'missing_produces', return_value=[]), \
                     patch.object(op.mf, 'input_hashes', return_value={}), \
                     patch('workflow.handoff.inspect_stage', return_value={'passed': passed}), \
                     patch.object(op.self_heal, '_candidate', return_value=(None, '')):
                    result = op.view(self.root, self.folder)['incident']
                self.assertEqual(result['status'], 'queued')
                self.assertFalse(result['verification']['verified'])

    def test_discovery_unknown_write_prevents_consumed_stage_auto_resolution(self):
        self.claim()
        state = op._load(self.folder)
        state['incident']['status'] = 'queued'
        state['receipts'] = [{'request_id': 'unknown', 'status': 'unknown'}]
        op._save(self.folder, state)
        self.put('workflow/manifest.json', {'inputs': {}, 'stages': {'validate': {'status': 'completed'}}})
        with patch.object(op, '_verify', return_value={'verified': True}) as verify:
            result = op.view(self.root, self.folder)['incident']
        verify.assert_not_called()
        self.assertEqual(result['status'], 'queued')
        self.assertEqual(op._load(self.folder)['receipts'][0]['status'], 'unknown')

    def test_same_workspace_and_test_strategy_cannot_repeat(self):
        from workflow import operations_development as dev
        incident = {'attempts': []}
        record = {'status': 'workspace_ready', 'path': 'unused'}
        with patch.object(op, '_code_job', return_value=(record, {})), \
             patch.object(dev, 'fingerprint', return_value='same-code'), \
             patch.object(dev, 'run_tests', return_value={'passed': False, 'output': 'E42 failed'}) as run, \
             patch.object(op, '_save_code_record'), patch.object(dev, 'save'):
            op._code_repair(self.folder, incident, {'operation': 'test', 'tests': ['workflow.test_x']})
            with self.assertRaisesRegex(ValueError, 'repeat_without_new_evidence'):
                op._code_repair(self.folder, incident, {'operation': 'test', 'tests': ['workflow.test_x']})
            self.assertEqual(run.call_count, 1)

    def test_persistent_budget_counters_survive_history_truncation(self):
        incident = {'attempts': []}
        for i in range(50):
            op._attempt(incident, 'install', str(i), 'same', 'installed')
        budgets = op._budgets(incident)
        self.assertEqual(len(incident['attempts']), 40)
        self.assertEqual(budgets['attempts_total'], 50)
        self.assertEqual(budgets['installs_total'], 50)

    def test_same_resume_strategy_on_same_evidence_is_still_refused(self):
        incident = {'attempts': []}
        op._attempt(incident, 'resume', 'retry_stage_with_fresh_context', 'same', 'applied')
        with self.assertRaisesRegex(ValueError, 'repeat_without_new_evidence'):
            op._block_repeat(incident, 'resume', 'retry_stage_with_fresh_context', 'same')
        self.assertIsNone(op._block_repeat(incident, 'resume', 'retry_stage_with_fresh_context', 'new-code'))

    def test_upload_and_publish_never_discovered_for_auto_recovery(self):
        for stage in ('upload_gate', 'upload', 'publish_gate', 'review_gate'):
            self.candidate.return_value = (stage, 'not permitted')
            self.assertEqual(op.incidents(self.root)['incidents'], [])


if __name__ == '__main__':
    unittest.main()
