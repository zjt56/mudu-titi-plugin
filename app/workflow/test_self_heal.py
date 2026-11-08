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
