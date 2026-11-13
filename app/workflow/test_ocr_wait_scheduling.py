"""Offline integration of resumable OCR waits; only temporary batches are used."""
from contextlib import ExitStack, nullcontext
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from workflow import auto, contracts, diagnose, manifest as mf, runner, self_heal, supervisor


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class OcrWaitSchedulingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ocr-wait-scheduling-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.batch = self.root / "new-batch"
        self.batch.mkdir()
        self.inputs = {}
        for key in ("source", "answers"):
            path = self.batch / (key + ".pdf")
            path.write_bytes(("test pdf " + key).encode())
            self.inputs.update(mf.register_input(self.batch, key, path))
        self.manifest = mf.new_manifest("new-batch", self.batch, self.inputs, {})
        mf.save(self.batch, self.manifest)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("socket.socket.connect", side_effect=AssertionError("network forbidden")))
        self.stack.enter_context(patch("workflow.local_agent.invoke", side_effect=AssertionError("model forbidden")))
        self.stack.enter_context(patch("workflow.local_agent._agent_lock", side_effect=lambda *a, **k: nullcontext()))
        self.stack.enter_context(patch("workflow.agent_protocol._agent_lock", side_effect=lambda *a, **k: nullcontext()))
        self.recover = self.stack.enter_context(patch("workflow.self_heal.recover", return_value={"changed": False}))
        self.handoff = self.stack.enter_context(patch("workflow.operations.handoff_if_available", return_value=None))
        self.stack.enter_context(patch("workflow.operations.lease_active", return_value=False))
        self.stack.enter_context(patch("workflow.operations_performance.observe", return_value={}))
        self.stack.enter_context(patch.object(auto, "_requeue_historical_upstream", return_value=False))
        self.stage = contracts.STAGE_BY_ID["extract"]

    def report(self, *, updated=None):
        return {"version": 2, "status": "waiting", "updated_at": time.time() if updated is None else updated,
                "sources": [{"input": key, "path": value["path"], "sha256": value["sha256"],
                             "status": "waiting", "error": {"code": "waiting"},
                             "progress": {"job_id": "job-" + key, "phase": "queued", "state": "pending",
                                          "retry_after_seconds": 30,
                                          "url": "https://secret.invalid/?token=PRIVATE",
                                          "message": "PRIVATE remote message"}}
                            for key, value in self.inputs.items()]}

    def execute(self, report=None, code=6):
        def child(*args, **kwargs):
            value = self.report() if report is None else report
            put(self.batch / "01_source/pdf-ocr.json", value)
            return SimpleNamespace(returncode=code, stdout="OCR waiting", stderr="")
        with patch.object(runner.subprocess, "run", side_effect=child):
            return runner._execute_command(self.batch, self.manifest, self.stage)

    def accept(self, *, updated=None):
        report = self.report(updated=updated)
        put(self.batch / "01_source/pdf-ocr.json", report)
        wait = runner.ocr_wait_state(self.batch, self.manifest, since=report["updated_at"])
        self.assertTrue(wait)
        mf.touch_stage(self.manifest, "extract", mf.PENDING, exit_code=6, notes=runner.OCR_WAIT_NOTE, ocr_wait=wait)
        mf.save(self.batch, self.manifest)
        return report, wait

    def test_runner_exit6_is_pending_without_delivery_or_completion(self):
        # Existing partial artifacts cannot make the waiting attempt complete.
        for value in self.stage.produces:
            path = self.batch / value
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
        with patch.object(runner, "_record_success") as complete:
            self.assertEqual(self.execute(), 6)
        record = mf.load(self.batch)["stages"]["extract"]
        self.assertEqual(record["status"], mf.PENDING)
        self.assertEqual(record["notes"], runner.OCR_WAIT_NOTE)
        self.assertIsNone(record["finished_at"])
        self.assertEqual(record["output_hashes"], {})
        self.assertNotIn("execution_completed", record)
        self.assertNotIn("release_ready", record)
        complete.assert_not_called()
        self.assertFalse(mf.dependency_satisfied(mf.load(self.batch), "extract"))

    def test_runner_completed_plus_waiting_multiple_pdfs_is_pending(self):
        report = self.report(updated=time.time() + 0.01)
        report["sources"][0].update(status="completed")
        self.assertEqual(self.execute(report), 6)
        self.assertEqual(mf.load(self.batch)["stages"]["extract"]["status"], mf.PENDING)

    def test_runner_rejects_stale_partial_failed_and_source_mismatches(self):
        cases = ("old", "missing_pdf", "failed", "unknown_submission", "no_job", "wrong_sha", "wrong_path", "duplicate", "bad_overall")
        for case in cases:
            with self.subTest(case=case):
                report = self.report(updated=time.time() + 0.01)
                if case == "old":
                    report["updated_at"] = time.time() - 20
                elif case == "missing_pdf":
                    report["sources"].pop()
                elif case == "failed":
                    report["sources"][1].update(status="failed", error={"code": "job_not_found"})
                elif case == "unknown_submission":
                    report["sources"][0]["error"] = {"code": "submission_unknown"}
                elif case == "no_job":
                    report["sources"][0]["progress"].pop("job_id")
                elif case == "wrong_sha":
                    report["sources"][0]["sha256"] = "0" * 64
                elif case == "wrong_path":
                    report["sources"][0]["path"] = "different.pdf"
                elif case == "duplicate":
                    report["sources"][1] = report["sources"][0]
                else:
                    report["status"] = "partial"
                self.assertEqual(self.execute(report), 1)
                self.assertEqual(mf.load(self.batch)["stages"]["extract"]["status"], mf.FAILED)

    def test_runner_rejects_current_bytes_different_from_manifest_sha(self):
        (self.batch / "source.pdf").write_bytes(b"changed source")
        report = self.report(updated=time.time() + 0.01)
        report["sources"][0]["sha256"] = hashlib.sha256(b"changed source").hexdigest()
        self.assertEqual(self.execute(report), 1)

    def test_auto_reports_pending_external_and_keeps_downstream_unfinished(self):
        def child(*args, **kwargs):
            put(self.batch / "01_source/pdf-ocr.json", self.report())
            return SimpleNamespace(returncode=6, stdout="", stderr="")
        with patch.object(runner.subprocess, "run", side_effect=child), \
             patch.object(runner, "do_structure_gate") as gate:
            outcome = auto._advance_impl(self.batch, {}, audio_worker=None)
        self.assertEqual(outcome["action"], "waiting_external")
        self.assertEqual(outcome["failed_stages"], [])
        self.assertTrue(any(row["stage"] == "extract" and row["action"] == "waiting_external"
                            for row in outcome["pending_stages"]))
        self.assertEqual(outcome["retry_after"], 30)
        self.assertEqual(auto._read_cooldown(self.batch), 0)
        gate.assert_not_called()
        self.assertNotIn(mf.load(self.batch)["stages"]["structure_gate"]["status"], mf.TERMINAL_OK)

    def test_auto_wait_window_avoids_command_recovery_and_operations(self):
        self.accept()
        self.recover.reset_mock()
        self.handoff.reset_mock()
        with patch.object(runner, "_execute_command") as execute:
            outcome = auto._advance_impl(self.batch, {}, audio_worker=None)
        self.assertEqual(outcome["action"], "waiting_external")
        execute.assert_not_called()
        self.recover.assert_not_called()
        self.handoff.assert_not_called()
        self.assertFalse(auto._cooldown_path(self.batch).exists())

    def test_auto_due_wait_resumes_original_extraction_and_can_complete(self):
        self.accept(updated=time.time() - 60)
        calls = []
        def complete(batch, manifest, stage):
            calls.append(stage.id)
            mf.touch_stage(manifest, stage.id, mf.COMPLETED, notes="", exit_code=0)
            manifest["stages"][stage.id].pop("ocr_wait", None)
            mf.save(batch, manifest)
            return 0
        with patch.object(runner, "_execute_command", side_effect=complete):
            auto._advance_impl(self.batch, {}, stop_at="structure_gate", audio_worker=None)
        self.assertEqual(calls, ["extract"])
        self.assertEqual(mf.load(self.batch)["stages"]["extract"]["status"], mf.COMPLETED)
        self.recover.assert_not_called()
        # Normal post-success observation resumes after the external wait ends.
        self.handoff.assert_called_once_with(self.batch)

    def test_diagnose_wait_is_nonblocking_safe_and_self_heal_not_candidate(self):
        self.accept()
        view = diagnose.diagnose(self.batch)
        item = next(row for row in view["items"] if row["stage"] == "extract")
        self.assertFalse(item["blocking"])
        self.assertEqual(item["next_action"], "waiting_external")
        self.assertIn("排队", item["detail"])
        self.assertIn("提取尚未完成", item["detail"])
        self.assertNotIn("PRIVATE", json.dumps(view))
        self.assertNotIn("secret.invalid", json.dumps(view))
        self.assertEqual(self_heal._candidate(self.batch, mf.load(self.batch)), (None, ""))
        self.assertEqual(view["blocking_count"], 0)

    def test_diagnose_failed_report_cannot_keep_a_benign_wait(self):
        report, _ = self.accept()
        report["status"] = "failed"
        report["sources"][1].update(status="failed", error={"code": "submission_unknown"})
        put(self.batch / "01_source/pdf-ocr.json", report)
        item = next(row for row in diagnose.diagnose(self.batch)["items"] if row["stage"] == "extract")
        self.assertTrue(item["blocking"])
        self.assertEqual(item["status"], mf.FAILED)

    def test_diagnose_real_other_failure_is_still_blocking(self):
        self.accept()
        mf.touch_stage(self.manifest, "knowledge", mf.FAILED, notes="real failure")
        mf.save(self.batch, self.manifest)
        item = next(row for row in diagnose.diagnose(self.batch)["items"] if row["stage"] == "knowledge")
        self.assertTrue(item["blocking"])

    def test_diagnose_uses_fixed_copy_for_all_progress_phases(self):
        for phase, state, text in (("submitted", "", "已提交"), ("polling", "queued", "排队"),
                                   ("polling", "running", "运行"), ("downloading", "done", "下载")):
            with self.subTest(phase=phase, state=state):
                report, _ = self.accept()
                for item in report["sources"]:
                    item["progress"].update(phase=phase, state=state)
                put(self.batch / "01_source/pdf-ocr.json", report)
                item = next(row for row in diagnose.diagnose(self.batch)["items"] if row["stage"] == "extract")
                self.assertFalse(item["blocking"])
                self.assertIn(text, item["detail"])
                self.assertIn("稍后续查", item["detail"])

    def test_auto_unknown_post_failure_is_failed_not_external_wait(self):
        report = self.report()
        report["status"] = "failed"
        report["sources"][0].update(status="failed", error={"code": "submission_unknown"})
        report["sources"][0]["progress"].pop("job_id")
        def child(*args, **kwargs):
            put(self.batch / "01_source/pdf-ocr.json", report)
            return SimpleNamespace(returncode=1, stdout="submission_unknown", stderr="")
        with patch.object(runner.subprocess, "run", side_effect=child):
            outcome = auto._advance_impl(self.batch, {}, audio_worker=None)
        self.assertEqual(outcome["action"], "failed")
        self.assertTrue(any(row["stage"] == "extract" for row in outcome["failed_stages"]))
        self.assertEqual(mf.load(self.batch)["stages"]["extract"]["status"], mf.FAILED)

    def test_wait_consumers_reject_report_replacement_or_changed_source(self):
        report, _ = self.accept()
        report["updated_at"] += 0.001
        put(self.batch / "01_source/pdf-ocr.json", report)
        self.assertEqual(runner.ocr_wait_state(self.batch, self.manifest), {})
        self.accept()
        (self.batch / "answers.pdf").write_bytes(b"new answer PDF")
        self.assertEqual(runner.ocr_wait_state(self.batch, self.manifest), {})
        item = next(row for row in diagnose.diagnose(self.batch)["items"] if row["stage"] == "extract")
        self.assertTrue(item["blocking"])

    def test_retry_after_is_bounded_and_invalid_values_are_safe(self):
        for value, expected in ((-1, 5), (0, 5), (1000000, 300), ("inf", 30), (None, 30), ("x", 30)):
            self.assertEqual(runner.ocr_retry_after(value), expected)

    def test_supervisor_wait_timer_resumes_without_budget_or_busy_loop(self):
        report, waiting = self.accept()
        now = [report["updated_at"]]
        dispatch = Mock(return_value={"started": True})
        config = supervisor.SupervisorConfig(interval=1, retry_base=2, retry_max=5, max_no_progress=1)
        watch = supervisor.WorkflowSupervisor(self.root, dispatch, clock=lambda: now[0], config=config,
                                              busy=lambda folder: False)
        for _ in range(4):
            self.assertEqual(watch.one_tick()["batches"][self.batch.name], "waiting_external")
        dispatch.assert_not_called()
        ledger = supervisor._read(self.batch / "workflow/supervisor.json")
        self.assertEqual(ledger["no_progress_attempts"], 0)
        self.assertEqual(ledger["next_retry_at"], waiting["next_retry_at"])
        for _ in range(4):
            now[0] = ledger["next_retry_at"] + 0.01
            self.assertEqual(watch.one_tick()["batches"][self.batch.name], "dispatched")
            ledger = supervisor._read(self.batch / "workflow/supervisor.json")
            self.assertEqual(ledger["no_progress_attempts"], 0)
            self.assertEqual(watch.one_tick()["batches"][self.batch.name], "waiting_external")
        self.assertEqual(dispatch.call_count, 4)
        self.assertFalse((self.batch / "workflow/blockers.json").exists())
        self.assertFalse((self.batch / "workflow/self-heal.json").exists())

    def test_supervisor_refunds_initial_dispatch_and_ignores_model_timer_for_poll(self):
        report, waiting = self.accept()
        put(self.batch / "workflow/supervisor.json", {"no_progress_attempts": 1, "dispatch_counted": True,
             "retry_reservation_at": time.time() + 999, "progress": supervisor._progress(self.manifest)})
        auto._set_cooldown(self.batch, 999)
        put(self.batch / "workflow/self-heal.json", {"next_retry_at": time.time() + 999})
        dispatch = Mock(return_value={"started": True})
        watch = supervisor.WorkflowSupervisor(self.root, dispatch, clock=lambda: waiting["next_retry_at"] + 1,
                                              busy=lambda folder: False)
        self.assertEqual(watch.one_tick()["batches"][self.batch.name], "dispatched")
        self.assertEqual(supervisor._read(self.batch / "workflow/supervisor.json")["no_progress_attempts"], 0)
        self.assertGreater(auto._read_cooldown(self.batch), 0)

    def test_supervisor_real_failure_does_not_borrow_ocr_wait_exemption(self):
        self.accept()
        mf.touch_stage(self.manifest, "knowledge", mf.FAILED, notes="real failure")
        mf.save(self.batch, self.manifest)
        dispatch = Mock(return_value={"started": True})
        watch = supervisor.WorkflowSupervisor(self.root, dispatch, busy=lambda folder: False)
        self.assertEqual(watch.one_tick()["batches"][self.batch.name], "dispatched")
        self.assertEqual(supervisor._read(self.batch / "workflow/supervisor.json")["no_progress_attempts"], 1)


if __name__ == "__main__":
    unittest.main()
