"""Operations must preserve forward progress without replaying a failed stage.

Disposable manifests and the real scheduler/operations lease paths are used;
the stage executor is a local stub and no model or user batch is accessed.
"""
from contextlib import ExitStack
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import auto, contracts, manifest as mf, operations as op, self_heal, supervisor


class OperationsProgressExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'workflows'
        self.batch = self.root / 'paper'
        state = mf.new_manifest('paper', self.batch, {}, {})
        mf.touch_stage(state, 'knowledge', mf.FAILED, notes='catalog validator rejected candidate')
        mf.save(self.batch, state)
        self.put('workflow/self-heal.json', {'events': [{
            'stage': 'knowledge', 'detail': 'catalog validator rejected candidate',
            'signature': 'fixture-failure', 'outcome': 'repair_failed', 'attempt': 1}]})
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(self_heal, '_candidate',
            return_value=('knowledge', 'catalog validator rejected candidate')))
        self.stack.enter_context(patch.object(op, '_snapshot', return_value='bound-snapshot'))
        self.stack.enter_context(patch.object(op, '_service_until', 0.0))
        op.service_heartbeat(True)

    def put(self, relative, value):
        path = self.batch / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def claim(self):
        incident = op.incidents(self.root)['incidents'][0]
        return op.claim(self.root, self.batch, {
            'incident_id': incident['incident_id'], 'session_id': 'fixture-repair',
            'mode': 'repair', 'execution_first': True})

    def schedule(self, *, recovery=None):
        # Retain the actual auto.advance batch lock, operations handoff and
        # scheduler, while isolating unrelated gates and missing fixture data.
        stages = (
            replace(contracts.STAGE_BY_ID['knowledge'], depends_on=()),
            replace(contracts.STAGE_BY_ID['meta'], depends_on=()),
            replace(contracts.STAGE_BY_ID['assemble'], depends_on=('knowledge', 'meta')),
        )
        calls = []

        def execute(batch, manifest, stage):
            calls.append(stage.id)
            self.assertEqual(stage.id, 'meta', 'failed knowledge must not be replayed')
            (batch / 'independent-command.txt').write_text('completed', encoding='utf-8')
            mf.touch_stage(manifest, stage.id, mf.COMPLETED)
            mf.save(batch, manifest)
            return 0

        def dep_ready(manifest, stage_id, **kwargs):
            return manifest['stages'].get(stage_id, {}).get('status') in mf.TERMINAL_OK

        with ExitStack() as stack:
            stack.enter_context(patch.object(contracts, 'STAGES', stages))
            stack.enter_context(patch.object(mf, 'reconcile_deliveries'))
            stack.enter_context(patch.object(mf, 'dependency_satisfied', side_effect=dep_ready))
            stack.enter_context(patch.object(auto, '_requeue_historical_upstream', return_value=False))
            stack.enter_context(patch.object(auto, '_read_cooldown', return_value=0))
            stack.enter_context(patch.object(auto, '_result', return_value={}))
            stack.enter_context(patch.object(auto.rn, '_skip_agent_stage', return_value=False))
            stack.enter_context(patch.object(auto.rn, '_execute_command', side_effect=execute))
            stack.enter_context(patch('workflow.agent_tasks.stage_gaps', return_value={}))
            invoke = stack.enter_context(patch('workflow.local_agent.invoke',
                side_effect=AssertionError('no model call is authorized by these fixtures')))
            recover = stack.enter_context(patch.object(self_heal, 'recover', return_value=(
                recovery or {'changed': False, 'status': 'offline'})))
            outcome = auto.advance(self.batch, {'knowledge': ['offline-model'], 'assemble': ['offline-model']},
                                   stop_at='review')
        invoke.assert_not_called()
        return outcome, calls, recover

    def test_repair_claim_allows_independent_stage_but_preserves_failed_stage_wait(self):
        claim = self.claim()
        self.assertEqual(claim['mode'], 'repair')
        outcome, calls, recover = self.schedule()
        self.assertEqual(calls, ['meta'])
        self.assertEqual((self.batch / 'independent-command.txt').read_text(), 'completed')
        recover.assert_not_called()
        current = mf.load(self.batch)
        self.assertEqual(current['stages']['knowledge']['status'], mf.FAILED)
        self.assertEqual(current['stages']['assemble']['status'], mf.PENDING)
        waiting = {row['stage']: row for row in outcome['pending_stages']}
        self.assertEqual(waiting['knowledge']['action'], 'operations_pending')
        self.assertEqual(waiting['assemble']['unmet_dependencies'], ['knowledge'])

    def test_waiting_evidence_releases_handoff_without_replaying_refused_stage(self):
        self.claim()
        state = op._load(self.batch)
        state['incident'].update(status='waiting_evidence', eligible=False,
                                 attempt_id=None, lease_expires_at=0)
        state['incident']['recovery'] = op._recovery_plan(self.batch, state['incident'])
        op._save(self.batch, state)
        self.assertIsNone(op.handoff_if_available(self.batch))
        outcome, calls, recover = self.schedule(recovery={
            'changed': False, 'stage': 'knowledge', 'status': 'refused',
            'reason': 'same failed input requires a code repair'})
        recover.assert_called_once_with(self.batch)
        self.assertEqual(calls, ['meta'])
        self.assertEqual(mf.load(self.batch)['stages']['knowledge']['status'], mf.FAILED)
        self.assertEqual(mf.load(self.batch)['stages']['assemble']['status'], mf.PENDING)

    def test_actual_write_transaction_still_excludes_normal_execution(self):
        self.claim()
        with op._locked(self.root, self.batch, execution=True):
            with supervisor.advance_claim(self.batch) as allowed:
                self.assertFalse(allowed)
        with supervisor.advance_claim(self.batch) as allowed:
            self.assertTrue(allowed, 'a surviving repair claim must not freeze the next normal step')

    def test_isolated_test_releases_batch_and_metadata_locks(self):
        claim = self.claim()
        payload = {k: claim[k] for k in ('incident_id', 'session_id', 'attempt_id')}
        payload.update(action='code_repair', request_id='isolated-test', input={'operation': 'test'})
        def run(*args):
            with supervisor.advance_claim(self.batch) as allowed:
                self.assertTrue(allowed)
            with op._metadata_locked(self.root, self.batch):
                pass  # heartbeat and ledger reconciliation stay available
            return {'passed': True}
        with patch.object(op, '_code_repair', side_effect=run) as test:
            receipt = op.action(self.root, self.batch, payload)
            self.assertEqual(receipt['status'], 'completed')
            self.assertEqual(op.action(self.root, self.batch, payload), receipt)
            test.assert_called_once()

    def test_execution_first_claim_can_start_while_independent_worker_holds_locks(self):
        incident = op.incidents(self.root)['incidents'][0]
        with supervisor._try_file_lock(supervisor._auto_lock_path(self.batch)) as auto_lock, \
             supervisor._try_file_lock(self.batch / 'workflow/.run.lock') as run_lock:
            self.assertTrue(auto_lock and run_lock)
            claim = op.claim(self.root, self.batch, {
                'incident_id': incident['incident_id'], 'session_id': 'background-repair',
                'mode': 'repair', 'execution_first': True})
            self.assertEqual(claim['status'], 'claimed')
            self.assertFalse(op.lease_active(self.batch))

    def test_background_code_work_does_not_defer_backend_activation(self):
        self.claim()
        self.assertFalse(op.leased(self.root),
                         'background repair ownership alone is not an in-flight batch mutation')

    def test_known_source_repair_runs_once_then_requests_real_pipeline_resume(self):
        from workflow import self_heal_actions
        self.stack.enter_context(patch.object(self_heal, '_candidate', return_value=('validate', 'bad options')))
        state = mf.load(self.batch)
        mf.touch_stage(state, 'validate', mf.FAILED, notes='bad options')
        mf.save(self.batch, state)
        incident = op.incidents(self.root)['incidents'][0]
        claim = op.claim(self.root, self.batch, {'incident_id': incident['incident_id'],
            'session_id': 'source-repair', 'mode': 'repair', 'execution_first': True})
        payload = {k: claim[k] for k in ('incident_id', 'session_id', 'attempt_id')}
        payload.update(action='recover', request_id='source-repair-1', input={})
        with patch.object(op, '_verify', return_value={'verified': False, 'evidence_id': 'check'}), \
             patch.object(op, '_packet', return_value={}), \
             patch.object(self_heal_actions, '_prepare_options', return_value={'passed': True, 'changes': ['one']}), \
             patch.object(self_heal_actions, 'apply_source_option_repair', return_value={'installed': True}) as apply:
            receipt = op.action(self.root, self.batch, payload)
            self.assertEqual(receipt['status'], 'completed')
            self.assertTrue(receipt['evidence']['resume_required'])
            self.assertFalse(receipt['evidence']['verified'])
            self.assertEqual(op.action(self.root, self.batch, payload), receipt)
            apply.assert_called_once()


class OperationsObservedCodeFingerprintTests(unittest.TestCase):
    def test_protected_and_host_code_changes_are_observed_without_write_permission(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            core = root / 'app/workflow/operations.py'
            host = root / 'lib/operations-agent.js'
            core.parent.mkdir(parents=True)
            host.parent.mkdir(parents=True)
            core.write_text('CONTROL_VERSION = 1\n', encoding='utf-8')
            host.write_text('export const hostVersion = 1;\n', encoding='utf-8')
            with patch.object(op, '_source_root', return_value=root):
                before = op._code_fingerprint()
                core.write_text('CONTROL_VERSION = 2\n', encoding='utf-8')
                after_core = op._code_fingerprint()
                self.assertNotEqual(before, after_core, 'read-only protected code is still recovery evidence')
                host.write_text('export const hostVersion = 2;\n', encoding='utf-8')
                self.assertNotEqual(after_core, op._code_fingerprint(),
                                    'a host fix must wake an incident waiting on old code')


if __name__ == '__main__':
    unittest.main()
