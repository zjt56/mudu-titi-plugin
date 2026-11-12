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
        prompt = invoke.call_args.args[0]
        self.assertIn('审核差异是产出，不是故障', prompt)
        self.assertIn('field=knowledge 均不是内容修复授权', prompt)
        self.assertIn('不能用 rejudge_knowledge、upstream_rederive', prompt)
        ledger = json.loads((self.batch / self_heal.LEDGER).read_text(encoding="utf-8"))
        self.assertEqual(ledger["events"][-1]["plan"]["action"], "retry_stage")

    def test_review_differences_never_select_a_content_producer(self):
        for code in ('REVIEW_ROUND_FINDING', 'MANUAL_REVIEW_OPEN', 'REVIEW_FINDING_OPEN'):
            for field, producer in (('knowledge', 'knowledge'), ('answer', 'answers')):
                with self.subTest(code=code, field=field):
                    issue = {'code': code, 'field': field, 'producer_stage': producer,
                             'message': '知识点与答案存在差异'}
                    self.assertIsNone(self_heal._producer_for_issue(issue))
        self.assertEqual(self_heal._producer_for_issue(
            {'code': 'KNOWLEDGE_INVALID', 'field': 'knowledge'}), 'knowledge')

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

    def test_interrupted_diagnosis_is_closed_without_consuming_the_budget(self):
        response = {"response": {"status": "completed", "artifacts": {
            "self_heal": {"action": "retry_stage", "stage": "extract", "reason": "重新解析"}},
            "notes": [], "findings": []}}
        with patch("workflow.local_agent.load_config", return_value={}), \
             patch("workflow.local_agent.invoke", return_value=response):
            self_heal.recover(self.batch)
        ledger = json.loads((self.batch / self_heal.LEDGER).read_text(encoding="utf-8"))
        key = ledger["active_signature"]
        # Replay what an externally killed diagnosis leaves behind: the claim is
        # persisted, the result never arrives, and the cooldown is still armed.
        interrupted = ledger["events"][-1]
        interrupted.update(outcome="diagnosing", at="2020-01-01T00:00:00+08:00")
        interrupted.pop("plan", None)
        interrupted.pop("finished_at", None)
        ledger["attempts"][key] = 1
        ledger["next_retry_at"] = 0
        (self.batch / self_heal.LEDGER).write_text(json.dumps(ledger), encoding="utf-8")
        manifest = mf.load(self.batch)
        manifest["stages"]["extract"]["status"] = mf.FAILED
        manifest["stages"]["extract"]["notes"] = "解析失败：结构输出缺失"
        mf.save(self.batch, manifest)
        with patch("workflow.local_agent.load_config", return_value={}), \
             patch("workflow.local_agent.invoke", return_value=response):
            result = self_heal.recover(self.batch)
        self.assertNotEqual(result["status"], "cooldown")
        ledger = json.loads((self.batch / self_heal.LEDGER).read_text(encoding="utf-8"))
        closed = [event for event in ledger["events"]
                  if event.get("outcome") == "diagnostic_interrupted"]
        self.assertEqual(len(closed), 1)
        self.assertTrue(closed[0].get("finished_at"))
        self.assertNotIn("diagnostic_interrupted", self_heal.BLOCKER_OUTCOMES)
        # The interruption was not billed as a content failure: the retry reuses
        # the budget slot it had taken instead of advancing to attempt two.
        self.assertEqual(ledger["events"][-1]["attempt"], 1)

    def test_recent_claim_and_finished_diagnosis_are_left_alone(self):
        recent = {"attempts": {"extract:abc": 1}, "events": [
            {"at": mf.now_iso(), "stage": "extract", "signature": "abc",
             "outcome": "diagnosing"}]}
        self.assertFalse(self_heal._close_interrupted_diagnosis(
            recent, "extract:abc", "extract", "abc"))
        self.assertEqual(recent["attempts"]["extract:abc"], 1)
        settled = {"attempts": {"extract:abc": 2}, "events": [
            {"at": "2020-01-01T00:00:00+08:00", "stage": "extract", "signature": "abc",
             "outcome": "repair_failed", "finished_at": "2020-01-01T00:01:00+08:00"}]}
        self.assertFalse(self_heal._close_interrupted_diagnosis(
            settled, "extract:abc", "extract", "abc"))
        self.assertEqual(settled["attempts"]["extract:abc"], 2)

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

    def test_volatile_detail_does_not_reset_retry_budget(self):
        # Regression: the retry signature used to include the raw failure detail,
        # which drifts on every whole-paper rerun (different unit ids, killed
        # subprocess exit codes, shard order). Each drift looked like new evidence
        # and reset the budget to zero, so a deterministic blocker re-ran the whole
        # stage forever — spawning dozens of headless processes that froze the host.
        # Same class of failure must now accumulate one budget regardless of noise.
        variant_a = "sec-0ed7068246aa3d4e-u21: scoped answer source is not uniquely bound; " \
                    "sec-8a849c1f18e3dbda-u30: Agent subprocess failed (exit -15, exit)"
        variant_b = "sec-1111111122223333-u5: scoped answer source is not uniquely bound; " \
                    "sec-99999999eeeebbbb-u44: Agent subprocess failed (exit -9, exit)"
        self.assertEqual(self_heal._failure_class_key(variant_a),
                         self_heal._failure_class_key(variant_b))
        # A genuinely different defect still earns a distinct budget.
        self.assertNotEqual(
            self_heal._failure_class_key(variant_a),
            self_heal._failure_class_key(
                "sec-0ed7068246aa3d4e-u21: knowledge label 超出允许维度"))

        manifest = mf.load(self.batch)
        mf.touch_stage(manifest, "review", mf.FAILED, notes="scoped answer source is not uniquely bound")
        mf.save(self.batch, manifest)
        # Repeated failures of the same class, each with a slightly different
        # detail/shared retry evidence, must exhaust the budget instead of looping.
        details = [
            "sec-1111111122223333-u5: scoped answer source is not uniquely bound; exit -15",
            "sec-2222222233334444-u6: scoped answer source is not uniquely bound; exit -9",
            "sec-3333333344445555-u7: scoped answer source is not uniquely bound; exit -6",
            "sec-4444444455556666-u8: scoped answer source is not uniquely bound; exit -3",
        ]
        clock = [10000.0]
        cycle = iter(details)
        with patch.object(self_heal, "_candidate",
                          side_effect=lambda b, m: ("review", next(cycle))), \
             patch.object(self_heal, "_retry_evidence", return_value={"inputs": "same", "code": "same"}), \
             patch.object(self_heal, "_plan", return_value={"action": "record_only", "reason": "no safe edit"}), \
             patch.object(self_heal.time, "time", side_effect=lambda: clock[0]):
            statuses = []
            for _ in range(len(details)):
                clock[0] += 100000
                result = self_heal.recover(self.batch)
                statuses.append(result["status"])
                # Executor reproduces the same class of failure with fresh noise.
                state = mf.load(self.batch)
                mf.touch_stage(state, "review", mf.FAILED, notes="scoped answer source is not uniquely bound")
                mf.save(self.batch, state)
        # One shared budget: it must reach retry_budget_exhausted, not stay requeued.
        self.assertIn("retry_budget_exhausted", statuses, statuses)
        ledger = self_heal._load(self.batch)
        self.assertEqual(list(ledger["attempts"].values()), [len(details)],
                         "volatile detail must not fork the retry budget")


if __name__ == "__main__":
    unittest.main()
