# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import contracts, manifest as mf, self_heal


class SelfHealTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name) / "batch"
        self.batch.mkdir()
        manifest = mf.new_manifest("batch", self.batch, {}, {})
        manifest["stages"]["extract"]["status"] = mf.FAILED
        manifest["stages"]["extract"]["notes"] = "解析失败：结构输出缺失"
        mf.save(self.batch, manifest)
        # These cases test diagnosis, not whether a real batch currently holds
        # the process-wide execution lock; simulate an idle batch explicitly.
        busy = patch.object(self_heal, "_busy", return_value=False)
        busy.start()
        self.addCleanup(busy.stop)

    def test_model_plan_requeues_only_allowed_stage_and_invalidates_downstream(self):
        response = {"response": {"status": "completed", "artifacts": {
            "self_heal": {"action": "retry_stage", "stage": "extract", "reason": "重新解析"}},
            "notes": [], "findings": []}}
        with patch("workflow.local_agent.load_config", return_value={}), \
             patch("workflow.local_agent.invoke", return_value=response) as invoke:
            result = self_heal.recover(self.batch)
        self.assertTrue(result["changed"])
        self.assertEqual(mf.load(self.batch)["stages"]["extract"]["status"], mf.PENDING)
        # Pending descendants remain pending; invalidate_downstream only stamps
        # stages that had prior work, so the scheduler can execute them normally.
        self.assertEqual(mf.load(self.batch)["stages"]["structure_gate"]["status"], mf.PENDING)
        invoke.assert_called_once()
        ledger = json.loads((self.batch / self_heal.LEDGER).read_text(encoding="utf-8"))
        self.assertEqual(ledger["events"][-1]["plan"]["action"], "retry_stage")

    def test_invalid_model_action_is_recorded_without_state_mutation(self):
        response = {"response": {"status": "completed", "artifacts": {
            "self_heal": {"action": "run_shell", "stage": "extract", "reason": "bad"}},
            "notes": [], "findings": []}}
        with patch("workflow.local_agent.load_config", return_value={}), \
             patch("workflow.local_agent.invoke", return_value=response):
            result = self_heal.recover(self.batch)
        self.assertFalse(result["changed"])
        self.assertEqual(mf.load(self.batch)["stages"]["extract"]["status"], mf.FAILED)
        ledger = json.loads((self.batch / self_heal.LEDGER).read_text(encoding="utf-8"))
        self.assertIn("error", ledger["events"][-1])

    def test_old_downstream_failure_waits_for_queued_prerequisites(self):
        manifest = mf.load(self.batch)
        manifest['stages']['extract']['status'] = mf.INVALIDATED
        manifest['stages']['structure_gate']['status'] = mf.COMPLETED
        manifest['stages']['preassemble']['status'] = mf.COMPLETED
        manifest['stages']['assemble']['status'] = mf.NEEDS_REVIEW
        item = {'stage': 'assemble', 'status': mf.NEEDS_REVIEW,
                'blocking': True, 'detail': 'old format error'}
        with patch.object(self_heal.diagnose, 'diagnose', return_value={'items': [item]}):
            self.assertEqual(self_heal._candidate(self.batch, manifest), (None, ''))

    def test_stale_running_record_is_requeued_without_a_model_call(self):
        manifest = mf.load(self.batch)
        manifest["stages"]["extract"]["status"] = mf.RUNNING
        manifest["stages"]["extract"]["started_at"] = "2020-01-01T00:00:00+08:00"
        mf.save(self.batch, manifest)
        with patch.object(self_heal, "_stale_seconds", return_value=30.0), \
             patch.object(self_heal, "_busy", return_value=False), \
             patch.object(self_heal.diagnose, "diagnose", return_value={"items": []}), \
             patch("workflow.local_agent.invoke") as invoke:
            result = self_heal.recover(self.batch)
        invoke.assert_not_called()
        self.assertEqual(result["outcome"], "stale_running")
        self.assertEqual(mf.load(self.batch)["stages"]["extract"]["status"], mf.PENDING)

    def test_record_only_budget_preserves_failure_and_rechecks_for_actual_repairs(self):
        manifest = mf.load(self.batch)
        mf.touch_stage(manifest, "review", mf.FAILED, notes="specific failed check")
        mf.save(self.batch, manifest)
        clock = [1000.0]
        plans = [{"action": "record_only", "reason": "explanation %d" % n}
                 for n in range(5)] + [{"action": "retry_stage_with_fresh_context", "reason": "new strategy"}]
        with patch.object(self_heal, "_candidate", return_value=("review", "specific failed check")), \
             patch.object(self_heal, "_retry_evidence", return_value={"inputs": "same", "code": "same"}), \
             patch.object(self_heal, "_plan", side_effect=plans) as plan, \
             patch.object(self_heal.time, "time", side_effect=lambda: clock[0]):
            for n in range(6):
                clock[0] += 10000
                before = mf.load(self.batch)["stages"]["review"]
                result = self_heal.recover(self.batch)
                if n < 2 or n == 5:
                    self.assertTrue(result["changed"], result)
                    # Simulate the normal executor reproducing the same failure.
                    state = mf.load(self.batch)
                    mf.touch_stage(state, "review", mf.FAILED, notes="specific failed check")
                    mf.save(self.batch, state)
                else:
                    self.assertFalse(result["changed"], result)
                    self.assertEqual(result["status"], "retry_budget_exhausted")
                    self.assertEqual(result["event"]["escalation"], "code_repair")
                    self.assertGreater(result["next_retry_at"], clock[0])
                    self.assertLessEqual(result["retry_after"], 3600)
                    self.assertEqual(mf.load(self.batch)["stages"]["review"], before)
                    # An immediate supervisor tick cannot call the model or rerun.
                    self.assertEqual(self_heal.recover(self.batch)["status"], "cooldown")
            self.assertEqual(plan.call_count, 6)
        ledger = self_heal._load(self.batch)
        self.assertEqual(list(ledger["attempts"].values()), [6])

    def test_changed_input_or_code_opens_new_budget_during_cooldown(self):
        original = self.batch / "source.txt"
        original.write_text("first source", encoding="utf-8")
        manifest = mf.load(self.batch)
        manifest["inputs"] = mf.register_input(self.batch, "source", original)
        mf.save(self.batch, manifest)
        with patch.object(self_heal, "_candidate", return_value=("extract", "same failure")), \
             patch("workflow.operations._code_fingerprint", side_effect=["v1", "v1", "v2"]), \
             patch.object(self_heal, "_plan", return_value={"action": "record_only", "reason": "no safe edit"}):
            results = [self_heal.recover(self.batch)]
            original.write_text("changed source", encoding="utf-8")
            results.append(self_heal.recover(self.batch))
            results.append(self_heal.recover(self.batch))
        self.assertEqual(len({result["signature"] for result in results}), 3)
        self.assertEqual([result["event"]["attempt"] for result in results], [1, 1, 1])
        self.assertTrue(all(result["status"] != "cooldown" for result in results))

    def test_human_gate_is_not_sent_to_model(self):
        manifest = mf.load(self.batch)
        for stage in contracts.STAGES:
            manifest["stages"][stage.id]["status"] = mf.COMPLETED
        manifest["stages"]["upload_gate"]["status"] = mf.BLOCKED
        mf.save(self.batch, manifest)
        with patch("workflow.local_agent.invoke") as invoke:
            result = self_heal.recover(self.batch)
        self.assertFalse(result["changed"])
        invoke.assert_not_called()


if __name__ == "__main__":
    unittest.main()
