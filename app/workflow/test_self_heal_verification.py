"""Offline recovery verification: upstream progress is not downstream repair."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import contracts, manifest as mf, self_heal


class SelfHealVerificationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="self-heal-verification-")
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name) / "fixture"
        self.batch.mkdir()
        self.state = mf.new_manifest("fixture", self.batch, {}, {})
        for spec in contracts.STAGES:
            self.state["stages"][spec.id]["status"] = mf.COMPLETED
            for relative in spec.produces:
                path = self.batch / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}", encoding="utf-8")
        mf.touch_stage(self.state, "validate", mf.FAILED, notes="original defect")
        mf.save(self.batch, self.state)
        # Fail immediately if a regression accidentally reaches a real model.
        model = patch("workflow.local_agent.invoke", side_effect=AssertionError("offline test: no model"))
        model.start()
        self.addCleanup(model.stop)
        # These cases exercise the outcome-observation matrix, not artefact
        # validity; treat the placeholder deliveries as satisfied prerequisites.
        from workflow import execution_state as _es
        def _dep(manifest, stage_id, **_kw):
            stages = manifest.get("stages") or {}
            return not any((stages.get(dep) or {}).get("rebuild_required")
                           for dep in contracts.upstream_of(stage_id))
        dep = patch.object(mf, "dependency_satisfied", side_effect=_dep)
        dep.start()
        self.addCleanup(dep.stop)

    def schedule(self, outcome="retry_scheduled", origin="validate", target="assemble", legacy=False):
        event = {"at": "2020-01-01T00:00:00+00:00", "stage": origin,
                 "target_stage": target, "outcome": outcome, "verified": False}
        if not legacy:
            event.update(origin_stage=origin, verification={"version": 1,
                "origin_before": self_heal._execution_baseline(self.state["stages"][origin]),
                "target_before": self_heal._execution_baseline(self.state["stages"][target])})
        self_heal._save(self.batch, {"events": [event], "attempts": {}})
        mf.touch_stage(self.state, target, mf.PENDING)
        return event

    def finish(self, stage, status=mf.COMPLETED):
        # A fixed clock proves that history, not only second-resolution times,
        # distinguishes consecutive executions.
        with patch.object(mf, "now_iso", return_value="2026-01-01T00:00:00+00:00"):
            mf.touch_stage(self.state, stage, mf.RUNNING)
            mf.touch_stage(self.state, stage, status, notes="new result")

    def latest(self):
        return self_heal._load(self.batch)["events"][-1]

    def test_all_scheduled_outcomes_wait_for_original_stage_then_verify(self):
        for outcome in sorted(self_heal.RETRY_OUTCOMES):
            with self.subTest(outcome=outcome):
                self.schedule(outcome)
                self.finish("assemble")
                original = dict(self.state["stages"]["validate"])
                self.assertEqual(self_heal.observe_outcomes(self.batch, self.state), {"verified": [], "failed": []})
                self.assertEqual(self.latest()["verification_status"], "pending")
                self.assertTrue(self.latest()["target_completed"])
                self.assertFalse(self.latest()["verified"])
                self.assertEqual(self.state["stages"]["validate"], original)
                self.finish("validate")
                self.assertEqual(self_heal.observe_outcomes(self.batch, self.state), {"verified": ["validate"], "failed": []})
                self.assertEqual(self.latest()["outcome"], "retry_verified")
                self.assertEqual(self_heal.observe_outcomes(self.batch, self.state), {"verified": [], "failed": []})
                mf.touch_stage(self.state, "validate", mf.FAILED, notes="original defect")

    def test_completed_origin_without_a_new_execution_stays_pending(self):
        self.state["stages"]["validate"]["status"] = mf.COMPLETED
        self.schedule()
        self.finish("assemble")
        self.assertEqual(self_heal.observe_outcomes(self.batch, self.state)["verified"], [])
        self.assertEqual(self.latest()["verification_status"], "pending")

    def test_original_failure_after_actual_rerun_is_failed(self):
        self.schedule("upstream_escalated")
        self.finish("assemble")
        self.finish("validate", mf.BLOCKED)
        self.assertEqual(self_heal.observe_outcomes(self.batch, self.state), {"verified": [], "failed": ["validate"]})
        self.assertEqual(self.latest()["verification_stage"], "validate")

    def test_upstream_failure_is_reported_against_original_fault(self):
        self.schedule("upstream_escalated")
        self.finish("assemble", mf.FAILED)
        self.assertEqual(self_heal.observe_outcomes(self.batch, self.state)["failed"], ["validate"])
        self.assertEqual(self.latest()["verification_stage"], "assemble")

    def test_completed_but_stale_original_is_not_verified(self):
        self.schedule()
        self.finish("assemble")
        self.finish("validate")
        stage = contracts.STAGE_BY_ID["validate"]
        self.state["stages"]["validate"]["output_hashes"] = mf.output_hashes(self.batch, stage)
        (self.batch / stage.produces[0]).write_text('{"changed":true}', encoding="utf-8")
        self.assertEqual(self_heal.observe_outcomes(self.batch, self.state)["failed"], ["validate"])
        self.assertIn("变化", self.latest()["verification_error"])

    def test_rebuilding_ancestor_prevents_verification(self):
        self.schedule()
        self.finish("assemble")
        self.finish("validate")
        self.state["stages"]["extract"]["rebuild_required"] = True
        self.assertEqual(self_heal.observe_outcomes(self.batch, self.state), {"verified": [], "failed": []})
        self.assertFalse(self.latest()["verified"])

    def test_same_stage_retry_requires_new_run_even_when_times_match(self):
        self.state["stages"]["validate"]["started_at"] = "2026-01-01T00:00:00+00:00"
        self.schedule("content_retry_with_feedback", target="validate")
        self.finish("validate")
        self.assertEqual(self_heal.observe_outcomes(self.batch, self.state)["verified"], ["validate"])

    def test_new_skip_does_not_claim_fault_was_retested(self):
        self.schedule(target="validate")
        mf.touch_stage(self.state, "validate", mf.SKIPPED)
        self.assertEqual(self_heal.observe_outcomes(self.batch, self.state)["verified"], [])
        self.assertFalse(self.latest()["verified"])

    def test_legacy_single_stage_retry_keeps_existing_behavior(self):
        self.schedule(target="validate", legacy=True)
        self.state["stages"]["validate"]["status"] = mf.COMPLETED
        self.assertEqual(self_heal.observe_outcomes(self.batch, self.state)["verified"], ["validate"])

    def test_legacy_cross_stage_retry_waits_for_origin_execution(self):
        for outcome in sorted(self_heal.RETRY_OUTCOMES):
            with self.subTest(outcome=outcome):
                self.state["stages"]["validate"]["started_at"] = "2019-01-01T00:00:00+00:00"
                self.schedule(outcome, legacy=True)
                self.finish("assemble")
                self.assertEqual(self_heal.observe_outcomes(self.batch, self.state)["verified"], [])
                self.finish("validate")
                self.assertEqual(self_heal.observe_outcomes(self.batch, self.state)["verified"], ["validate"])

    def test_recovery_persists_origin_target_and_escalation_loop_guard(self):
        plan = {"stage": "validate", "action": "upstream_rederive", "reason": "same real error"}
        with patch.object(self_heal, "_candidate", return_value=("validate", "same real error")), \
                patch.object(self_heal, "_busy", return_value=False), \
                patch.object(self_heal, "_plan", return_value=plan), \
                patch.object(self_heal, "_error_producer", return_value="assemble"), \
                patch.object(self_heal, "_feedback", return_value=None):
            first = self_heal.recover(self.batch)
            ledger = self_heal._load(self.batch)
            self.assertEqual(first["outcome"], "upstream_escalated")
            self.assertEqual(ledger["events"][-1]["origin_stage"], "validate")
            self.assertEqual(ledger["events"][-1]["target_stage"], "assemble")
            self.assertIn("origin_before", ledger["events"][-1]["verification"])
            signature = ledger["escalated"]["validate"]
            self.assertEqual(ledger["last_escalation"]["signature"], signature)
            second = self_heal.recover(self.batch)
        self.assertEqual(second["outcome"], "deferred_and_continuing")
        ledger = self_heal._load(self.batch)
        self.assertEqual(ledger["escalated"]["validate"], signature)
        self.assertEqual(len(ledger["events"]), 2)
        status = json.loads((self.batch / "workflow/self-heal-status.json").read_text())
        self.assertFalse(status["verified"])


class CrossStageRecoveryDispatchTest(unittest.TestCase):
    def exercise(self, passed):
        import contextlib
        import hashlib
        from workflow import auto, execution_state
        with tempfile.TemporaryDirectory(prefix='origin-recheck-') as temp:
            batch = Path(temp)
            state = mf.new_manifest('fixture', batch, {}, {})
            for spec in contracts.STAGES:
                state['stages'][spec.id]['status'] = mf.COMPLETED
                for relative in spec.produces:
                    path = batch / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text('{}')
            (batch / '01_source/structure.confirmed.json').write_text('{"units":[{}]}')
            final = batch / '06_batch/batch_meta.json'
            (batch / '07_review/upload-format.json').write_text(json.dumps({
                'passed': True, 'final_sha256': hashlib.sha256(final.read_bytes()).hexdigest()}))
            for spec in contracts.STAGES:
                state['stages'][spec.id].update(input_hashes=mf.input_hashes(batch, state, spec),
                                              output_hashes=mf.output_hashes(batch, spec))
            mf.touch_stage(state, 'validate', mf.BLOCKED, execution_completed=True,
                           continue_unqualified=True, quality_status='failed', release_ready=False, notes='defect')
            mf.save(batch, state)
            with patch.object(self_heal, '_candidate', return_value=('validate', 'defect')), \
                 patch.object(self_heal, '_busy', return_value=False), \
                 patch.object(self_heal, '_plan', return_value={
                     'stage': 'validate', 'action': 'upstream_rederive', 'reason': 'defect'}), \
                 patch.object(self_heal, '_error_producer', return_value='assemble'), \
                 patch.object(self_heal, '_feedback', return_value=None):
                recovered = self_heal.recover(batch)
            self.assertEqual(recovered['target_stage'], 'assemble')
            self.assertTrue(mf.load(batch)['stages']['validate']['rebuild_required'])
            self.assertFalse(execution_state.local_complete(batch, mf.load(batch)))
            calls = []
            def execute(folder, manifest, spec):
                calls.append(spec.id)
                mf.touch_stage(manifest, spec.id, mf.RUNNING)
                status = mf.COMPLETED if spec.id != 'validate' or passed else mf.BLOCKED
                mf.touch_stage(manifest, spec.id, status,
                    input_hashes=mf.input_hashes(folder, manifest, spec),
                    output_hashes=mf.output_hashes(folder, spec))
                mf.save(folder, manifest)
                return 0 if status == mf.COMPLETED else 1
            with contextlib.ExitStack() as stack:
                for obj, key, value in ((auto, 'ZERO_TOUCH', True), (auto, 'PARALLEL_AGENTS_ENABLED', False)):
                    stack.enter_context(patch.object(obj, key, value))
                stack.enter_context(patch.object(mf, 'reconcile_deliveries', return_value=[]))
                stack.enter_context(patch.object(mf, 'dependency_satisfied', return_value=True))
                stack.enter_context(patch.object(mf, 'skip_reason', return_value=''))
                stack.enter_context(patch('workflow.agent_tasks.stage_gaps', return_value={}))
                stack.enter_context(patch.object(auto, '_auto_prepare_audio', return_value={'status': 'ready'}))
                stack.enter_context(patch.object(self_heal, 'recover', return_value={'changed': False, 'status': 'idle'}))
                stack.enter_context(patch.object(auto.rn, '_skip_agent_stage', return_value=False))
                stack.enter_context(patch.object(auto.rn, '_execute_command', side_effect=execute))
                stack.enter_context(patch.object(auto.rn, 'do_validate',
                    side_effect=lambda folder, manifest: execute(folder, manifest, contracts.STAGE_BY_ID['validate'])))
                stack.enter_context(patch('workflow.local_agent.invoke', side_effect=AssertionError('no live model')))
                auto._advance_locked(batch, {'assemble': ['offline-executor']}, stop_at='upload_gate')
            self.assertEqual(calls, ['assemble', 'validate'])
            event = self_heal._load(batch)['events'][-1]
            self.assertEqual(event['outcome'], 'retry_verified' if passed else 'retry_failed')
            self.assertEqual(event['verified'], passed)
            if not passed:
                self.assertEqual(mf.load(batch)['stages']['validate']['status'], mf.BLOCKED)
                self.assertNotEqual(mf.load(batch)['stages']['validate'].get('quality_status'), 'passed')
                # A still-failing recheck keeps needing a rebuild and is never
                # treated as a satisfied delivery.
                self.assertTrue(mf.load(batch)['stages']['validate'].get('rebuild_required'))

    def test_identical_upstream_output_still_dispatches_original_recheck(self):
        self.exercise(True)

    def test_original_recheck_failure_is_not_repaired_or_approved(self):
        self.exercise(False)


if __name__ == "__main__":
    unittest.main()
