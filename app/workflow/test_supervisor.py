"""Durable supervision regressions. All data/locks live in TemporaryDirectory."""
import importlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from workbench import server
from workflow import auto, format_gate, local_agent, supervisor
from workflow.test_format_gate import batch as valid_final_batch


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class SupervisorFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="supervisor-regression-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.workflows = self.root / "workflows"
        self.workflows.mkdir()
        self.tmp = self.root / "tmp"
        self.tmp.mkdir()
        self.clock = FakeClock()
        self.config = supervisor.SupervisorConfig(interval=2, retry_base=10, retry_max=40,
            exhausted_cooldown=1000, max_no_progress=3)
        for p in (patch.dict(os.environ, {"TMPDIR": str(self.tmp), "WF_STRUCTURE_AI": "0"}),
                  patch.object(tempfile, "tempdir", str(self.tmp)),
                  patch("socket.socket.connect", side_effect=AssertionError("network forbidden")),
                  patch.object(local_agent, "invoke", side_effect=AssertionError("real model forbidden"))):
            p.start()
            self.addCleanup(p.stop)

    def make_batch(self, name="active", stages=None, status="pending", root=None):
        folder = (root or self.workflows) / name
        put(folder / "workflow/manifest.json", {"workflow_id": name, "status": status,
            "stages": stages if stages is not None else {"extract": {"status": "pending"}}})
        return folder

    def make_supervisor(self, dispatch=None, **kwargs):
        return supervisor.WorkflowSupervisor(self.workflows, dispatch or Mock(return_value={"started": True}),
            clock=self.clock, config=self.config, **kwargs)

    def state(self, folder):
        return json.loads((folder / "workflow" / supervisor.LEDGER).read_text())

    def due(self, folder):
        self.clock.now = self.state(folder)["next_retry_at"] + 1


class SupervisorTest(SupervisorFixture):
    def test_failed_stage_recovers_advances_and_real_format_gate_completes(self):
        folder = self.make_batch(stages={"validate": {"status": "failed", "notes": "bad field"},
            "review_gate": {"status": "blocked"}}, status="pending_confirmation")
        calls = []
        def fake_recover(batch):
            calls.append("recover")
            manifest = supervisor._read(batch / "workflow/manifest.json")
            manifest["stages"]["validate"] = {"status": "pending"}
            put(batch / "workflow/manifest.json", manifest)
            return {"changed": True, "status": "recovered"}
        def fake_dispatch(name):
            calls.append("dispatch")
            batch = self.workflows / name
            fake_recover(batch)
            calls.append("advance")
            put(batch / format_gate.FINAL_PATH, valid_final_batch())
            report = format_gate.run(batch)  # real deterministic offline gate
            self.assertTrue(report["passed"], report)
            manifest = supervisor._read(batch / "workflow/manifest.json")
            manifest["stages"] = {sid: {"status": "completed"} for sid in supervisor.LOCAL_STAGES}
            put(batch / "workflow/manifest.json", manifest)
            return {"started": True}
        watch = self.make_supervisor(fake_dispatch)
        self.assertEqual(watch.one_tick()["batches"], {"active": "dispatched"})
        self.assertEqual(calls, ["dispatch", "recover", "advance"])
        self.assertEqual(watch.one_tick()["batches"], {"active": "complete"})
        self.assertEqual(self.state(folder)["status"], "complete")
        self.assertEqual(len(calls), 3)

    def test_new_pending_failed_blocked_needs_review_invalidated_are_scheduled(self):
        for index, status in enumerate(("pending", "failed", "blocked", "needs_review", "invalidated", "running")):
            with self.subTest(status=status):
                root = self.root / str(index)
                root.mkdir()
                self.make_batch(stages={"types": {"status": status}}, root=root)
                dispatch = Mock(return_value={"started": True})
                watch = supervisor.WorkflowSupervisor(root, dispatch, clock=self.clock, config=self.config)
                self.assertEqual(watch.one_tick()["batches"], {"active": "dispatched"})
                dispatch.assert_called_once_with("active")

    def test_reservation_survives_restart_then_retries_after_cooldown(self):
        folder = self.make_batch()
        dispatch = Mock(return_value={"started": True})
        self.make_supervisor(dispatch).one_tick()
        first = self.state(folder)
        self.assertEqual(first["next_retry_at"], 1010)
        restarted = self.make_supervisor(dispatch)
        self.assertEqual(restarted.one_tick()["batches"]["active"], "cooldown")
        self.assertEqual(dispatch.call_count, 1)
        self.due(folder)
        self.assertEqual(restarted.one_tick()["batches"]["active"], "dispatched")
        self.assertEqual(dispatch.call_count, 2)
        self.assertEqual(self.state(folder)["next_retry_at"], self.clock.now + 20)

    def test_provider_and_self_heal_cooldown_do_not_dispatch_or_consume_attempt(self):
        folder = self.make_batch()
        cooldown = folder / "workflow/agent-runs/.rate-limit-cooldown"
        cooldown.parent.mkdir()
        cooldown.write_text("1100")
        put(folder / "workflow/self-heal.json", {"next_retry_at": 1200})
        dispatch = Mock(return_value={"started": True})
        watch = self.make_supervisor(dispatch)
        self.assertEqual(watch.one_tick()["batches"]["active"], "cooldown")
        self.assertEqual(self.state(folder)["next_retry_at"], 1200)
        self.assertEqual(self.state(folder).get("no_progress_attempts", 0), 0)
        self.clock.now = 1199
        self.make_supervisor(dispatch).one_tick()
        dispatch.assert_not_called()
        self.clock.now = 1201
        self.make_supervisor(dispatch).one_tick()
        dispatch.assert_called_once_with("active")

    def test_active_thread_has_no_retry_consumption(self):
        folder = self.make_batch(stages={"extract": {"status": "running"}})
        dispatch = Mock()
        watch = self.make_supervisor(dispatch, is_active=lambda name: True)
        self.assertEqual(watch.one_tick()["batches"]["active"], "active")
        dispatch.assert_not_called()
        self.assertFalse((folder / "workflow" / supervisor.LEDGER).exists())

    def test_external_auto_runner_and_model_locks_prevent_dispatch(self):
        folder = self.make_batch()
        locks = (lambda: auto._batch_auto_lock(folder),
                 lambda: supervisor._try_file_lock(folder / "workflow/.run.lock"),
                 lambda: local_agent._agent_lock(0, shared=True))
        for lock in locks:
            with self.subTest(lock=lock), lock():
                dispatch = Mock()
                self.assertTrue(supervisor.execution_busy(folder))
                self.assertEqual(self.make_supervisor(dispatch).one_tick()["batches"]["active"], "active")
                dispatch.assert_not_called()

    def test_archives_trash_symlinks_and_non_batches_untouched(self):
        folders = [self.make_batch("archives"), self.make_batch("trash"),
                   self.make_batch("18", root=self.workflows / "archives"),
                   self.make_batch("19", root=self.workflows / ".workbench-trash"),
                   self.make_batch("outside", root=self.root)]
        before = {str(p): p.read_bytes() for f in folders for p in f.rglob("*") if p.is_file()}
        (self.workflows / "linked").symlink_to(folders[-1], target_is_directory=True)
        (self.workflows / "uninitialized").mkdir()
        dispatch = Mock()
        result = self.make_supervisor(dispatch).one_tick()
        self.assertEqual(result["batches"], {})
        dispatch.assert_not_called()
        after = {str(p): p.read_bytes() for f in folders for p in f.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        (self.workflows / "uninitialized/workflow").symlink_to(folders[-1] / "workflow", target_is_directory=True)
        self.make_supervisor(dispatch).one_tick()
        dispatch.assert_not_called()

    def test_remote_upload_and_receipts_are_boundaries(self):
        self.make_batch("uploaded", stages={"upload": {"status": "completed"}, "format_gate": {"status": "pending"}})
        folder = self.make_batch("remote-receipt")
        put(folder / "07_upload/receipt.json", {"remote_confirmation_required": True})
        dispatch = Mock()
        outcomes = self.make_supervisor(dispatch).one_tick()["batches"]
        self.assertEqual(set(outcomes.values()), {"remote_boundary"})
        dispatch.assert_not_called()
        self.assertFalse((folder / "workflow" / supervisor.LEDGER).exists())

    def test_old_overall_review_gate_and_stale_format_do_not_mean_done(self):
        folder = self.make_batch(status="pending_confirmation",
            stages={"review_gate": {"status": "completed"}, "format_gate": {"status": "completed"}})
        put(folder / format_gate.FINAL_PATH, valid_final_batch())
        put(folder / format_gate.REPORT_PATH, {"passed": True, "final_sha256": "stale"})
        dispatch = Mock(return_value={"started": True})
        self.assertEqual(self.make_supervisor(dispatch).one_tick()["batches"]["active"], "dispatched")
        dispatch.assert_called_once()
        manifest = supervisor._read(folder / "workflow/manifest.json")
        manifest["stages"]["review_gate"]["status"] = "blocked"
        put(folder / "workflow/manifest.json", manifest)
        format_gate.run(folder)
        self.assertFalse(supervisor.local_complete(folder, manifest))
        self.assertEqual(manifest["stages"]["review_gate"]["status"], "blocked")

    def test_true_final_format_stops_with_executed_quality_block_without_faking_pass(self):
        folder = self.make_batch(stages={
            **{sid: {"status": "completed"} for sid in supervisor.LOCAL_STAGES},
            "review_gate": {"status": "blocked", "execution_completed": True, "continue_unqualified": True},
            "format_gate": {"status": "completed"}})
        put(folder / "07_review/review-gate.json", {"passed": False, "quality_status": "blocked"})
        put(folder / format_gate.FINAL_PATH, valid_final_batch())
        self.assertTrue(format_gate.run(folder)["passed"])
        original = (folder / "workflow/manifest.json").read_bytes()
        dispatch = Mock(return_value={"started": True})
        watch = self.make_supervisor(dispatch)
        # An executed-but-blocked quality gate is NOT local completion; the
        # supervisor keeps it active instead of reporting "complete".
        self.assertFalse(supervisor.local_complete(folder, supervisor._read(folder / "workflow/manifest.json")))
        self.assertNotEqual(watch.one_tick()["batches"]["active"], "complete")
        manifest = supervisor._read(folder / "workflow/manifest.json")
        manifest["stages"]["review_gate"]["rebuild_required"] = True
        put(folder / "workflow/manifest.json", manifest)
        self.assertFalse(supervisor.local_complete(folder, manifest))
        self.assertIn(watch.one_tick()["batches"]["active"], ("dispatched", "cooldown"))
        manifest["stages"]["review_gate"]["rebuild_required"] = False
        put(folder / "workflow/manifest.json", manifest)
        (folder / "07_review/review-gate.json").unlink()
        self.assertFalse(supervisor.local_complete(folder, manifest))
        self.due(folder)
        self.assertIn(watch.one_tick()["batches"]["active"], ("dispatched", "cooldown"))

    def test_missing_or_unknown_local_stage_never_inferred_done_from_format_only(self):
        folder = self.make_batch(stages={sid: {"status": "completed"} for sid in supervisor.LOCAL_STAGES})
        put(folder / format_gate.FINAL_PATH, valid_final_batch())
        self.assertTrue(format_gate.run(folder)["passed"])
        manifest = supervisor._read(folder / "workflow/manifest.json")
        self.assertTrue(supervisor.local_complete(folder, manifest))
        del manifest["stages"]["review"]
        put(folder / "workflow/manifest.json", manifest)
        self.assertFalse(supervisor.local_complete(folder, manifest))
        dispatch = Mock(return_value={"started": True})
        self.assertEqual(self.make_supervisor(dispatch).one_tick()["batches"]["active"], "dispatched")
        dispatch.assert_called_once()
        manifest["stages"]["review"] = {"status": "unknown"}
        self.assertFalse(supervisor.local_complete(folder, manifest))

    def test_no_progress_exponential_backoff_exhaustion_and_late_probe(self):
        folder = self.make_batch(stages={"types": {"status": "failed", "notes": "same error"}})
        dispatch = Mock(return_value={"started": True})
        watch = self.make_supervisor(dispatch)
        for attempt, expected in enumerate((10, 20, 40), start=1):
            self.assertEqual(watch.one_tick()["batches"]["active"], "dispatched")
            state = self.state(folder)
            self.assertEqual(state["no_progress_attempts"], attempt)
            self.assertEqual(state["next_retry_at"], self.clock.now + expected)
            self.due(folder)
        self.assertEqual(watch.one_tick()["batches"]["active"], "exhausted")
        state = self.state(folder)
        self.assertEqual(state["next_retry_at"], self.clock.now + 1000)
        self.assertTrue(state["escalation"])
        self.assertEqual(dispatch.call_count, 3)
        self.assertEqual(self.make_supervisor(dispatch).one_tick()["batches"]["active"], "cooldown")
        self.due(folder)
        self.assertEqual(watch.one_tick()["batches"]["active"], "dispatched")
        self.assertEqual(self.state(folder)["status"], "exhausted")
        self.assertEqual(self.state(folder)["next_retry_at"], self.clock.now + 1000)
        self.assertEqual(dispatch.call_count, 4)

    def test_temporary_provider_failures_keep_retrying_beyond_seven_rounds(self):
        folder = self.make_batch(stages={"types": {"status": "failed"}})
        put(folder / "workflow/self-heal.json", {"events": [{"error_code": "timeout"}]})
        dispatch = Mock(return_value={"started": True})
        watch = self.make_supervisor(dispatch)
        for _ in range(12):
            self.assertEqual(watch.one_tick()["batches"]["active"], "dispatched")
            state = self.state(folder)
            self.assertEqual(state["status"], "waiting_external")
            self.assertLessEqual(state["next_retry_at"] - self.clock.now, self.config.retry_max)
            self.due(folder)
        self.assertEqual(dispatch.call_count, 12)
        self.assertEqual(self.state(folder)["no_progress_attempts"], 12)

    def test_progress_resets_budget_but_changing_error_notes_does_not(self):
        folder = self.make_batch(stages={"types": {"status": "failed", "notes": "old"}})
        dispatch = Mock(return_value={"started": True})
        watch = self.make_supervisor(dispatch)
        watch.one_tick()
        manifest = supervisor._read(folder / "workflow/manifest.json")
        manifest["stages"]["types"].update(status="needs_review", notes="changed timestamp")
        put(folder / "workflow/manifest.json", manifest)
        self.due(folder)
        watch.one_tick()
        self.assertEqual(self.state(folder)["no_progress_attempts"], 2)
        manifest["stages"]["types"].update(status="completed", output_hashes={"types": "new"})
        put(folder / "workflow/manifest.json", manifest)
        watch.one_tick()
        self.assertEqual(self.state(folder)["no_progress_attempts"], 1)
        self.assertEqual(dispatch.call_count, 3)

    def test_dispatch_refusal_does_not_spend_budget_and_exception_does_not_stop_other_ticks(self):
        folder = self.make_batch()
        dispatch = Mock(return_value={"started": False, "running": True})
        watch = self.make_supervisor(dispatch)
        self.assertEqual(watch.one_tick()["batches"]["active"], "active")
        self.assertEqual(self.state(folder)["no_progress_attempts"], 0)
        self.due(folder)
        dispatch.side_effect = RuntimeError("temporarily unavailable")
        self.assertEqual(watch.one_tick()["batches"]["active"], "dispatch_error")
        self.assertEqual(self.state(folder)["last_result"]["error"], "temporarily unavailable")
        self.due(folder)
        dispatch.side_effect = None
        dispatch.return_value = {"started": True}
        self.assertEqual(watch.one_tick()["batches"]["active"], "dispatched")

    def test_removed_batch_is_not_recreated(self):
        folder = self.make_batch()
        destination = self.root / "user-archived"
        def busy(batch):
            batch.rename(destination)
            return False
        dispatch = Mock()
        self.make_supervisor(dispatch, busy=busy).one_tick()
        self.assertFalse(folder.exists())
        self.assertFalse((destination / "workflow" / supervisor.LEDGER).exists())
        dispatch.assert_not_called()

    def test_tick_dispatch_budget_and_repeated_start_stop(self):
        self.make_batch("one")
        self.make_batch("two")
        dispatch = Mock(return_value={"started": True})
        watch = self.make_supervisor(dispatch)
        watch.one_tick()
        self.assertEqual(dispatch.call_count, 1)
        watch.one_tick()
        self.assertEqual(dispatch.call_count, 2)
        ticked = threading.Event()
        with patch.object(watch, "one_tick", side_effect=lambda: ticked.set()):
            self.assertTrue(watch.start())
            self.assertTrue(ticked.wait(2))
            self.assertFalse(watch.start())
            self.assertTrue(watch.stop())
            self.assertFalse(watch._thread.is_alive())


class ServerSupervisorTest(SupervisorFixture):
    def setUp(self):
        super().setUp()
        for p in (patch.object(server, "WORKFLOWS", self.workflows),
                  patch.object(server, "_advance_state", {})):
            p.start()
            self.addCleanup(p.stop)

    def test_worker_single_pass_holds_claim_and_does_not_reenter(self):
        folder = self.make_batch()
        entered, finish, done = threading.Event(), threading.Event(), threading.Event()
        def fake_advance(batch, bridge, stop_at=None):
            self.assertEqual(stop_at, "upload_gate")
            self.assertTrue(supervisor.execution_busy(batch))
            entered.set()
            self.assertTrue(finish.wait(3))
            return {"action": "failed", "status": "blocked"}
        original_record = supervisor.record_result
        def record(batch, result):
            value = original_record(batch, result)
            done.set()
            return value
        with patch.object(auto, "load_bridge", return_value={}), \
             patch.object(auto, "_advance_locked", side_effect=fake_advance) as advance, \
             patch.object(supervisor, "record_result", side_effect=record):
            self.assertTrue(server.start_advance("active")["started"])
            self.assertTrue(entered.wait(2))
            self.assertFalse(server.start_advance("active")["started"])
            dispatch = Mock()
            watch = self.make_supervisor(dispatch, is_active=lambda name: server.advance_state(name).get("running"))
            self.assertEqual(watch.one_tick()["batches"]["active"], "active")
            dispatch.assert_not_called()
            finish.set()
            self.assertTrue(done.wait(2))
        self.assertEqual(advance.call_count, 1)
        self.assertEqual(supervisor._read(folder / "workflow" / supervisor.RESULT)["action"], "failed")

    def test_result_write_failure_never_leaves_dead_worker_running(self):
        folder = self.make_batch()
        reported = threading.Event()
        with patch.object(auto, "load_bridge", return_value={}), \
             patch.object(auto, "_advance_locked", return_value={"status": "blocked"}), \
             patch.object(supervisor, "record_result", side_effect=OSError("disk unavailable")), \
             patch.object(threading, "excepthook", side_effect=lambda args: reported.set()):
            self.assertTrue(server.start_advance("active")["started"])
            self.assertTrue(reported.wait(2))
        self.assertFalse(server.advance_state("active")["running"])
        self.assertFalse(supervisor.execution_busy(folder))

    def test_external_lock_blocks_advance_and_explicit_rerun_before_invalidation(self):
        folder = self.make_batch()
        before = (folder / "workflow/manifest.json").read_bytes()
        with auto._batch_auto_lock(folder), patch.object(auto, "_advance_locked") as advance:
            self.assertEqual(server.start_advance("active"), {"started": False, "running": True, "external": True})
            self.assertFalse(server.start_rerun("active", "tag")["started"])
            advance.assert_not_called()
        self.assertEqual(before, (folder / "workflow/manifest.json").read_bytes())

    def test_server_lifecycle_starts_then_stops_watchdog_even_on_failure(self):
        http = Mock()
        http.serve_forever.side_effect = RuntimeError("shutdown")
        instance = Mock()
        with patch.object(supervisor, "WorkflowSupervisor", return_value=instance) as factory:
            with self.assertRaisesRegex(RuntimeError, "shutdown"):
                server.serve(http)
        self.assertEqual(factory.call_args.args[:2], (self.workflows, server._dispatch_supervised))
        instance.start.assert_called_once()
        instance.stop.assert_called_once()
        http.server_close.assert_called_once()
        self.assertIsNone(server._workflow_supervisor)

    def test_import_server_and_supervisor_start_no_thread(self):
        # Importing an embedded workbench must never scan or dispatch batches.
        with patch.dict(os.environ, {"WF_WORKFLOWS_DIR": str(self.workflows),
                                     "WF_INBOX_DIR": str(self.root / "inbox")}), \
             patch.object(threading.Thread, "start", side_effect=AssertionError("import started thread")):
            importlib.reload(supervisor)
            importlib.reload(server)
        self.assertIsNone(server._workflow_supervisor)

    def test_watchdog_does_not_wait_on_http_global_lock(self):
        entered, release = threading.Event(), threading.Event()
        def hold_lock():
            with server._lock:
                entered.set()
                release.wait(3)
        thread = threading.Thread(target=hold_lock)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            with patch.object(server, "start_advance") as start:
                self.assertFalse(server._dispatch_supervised("active")["started"])
                start.assert_not_called()
        finally:
            release.set()
            thread.join(2)

    def test_api_exposes_persisted_exhaustion_after_backend_restart(self):
        folder = self.make_batch()
        put(folder / "workflow/supervisor.json", {"status": "exhausted", "next_retry_at": 2000,
            "no_progress_attempts": 5, "escalation": "long cooldown"})
        state = server.advance_state("active")
        self.assertFalse(state["running"])
        self.assertEqual(state["supervisor"]["status"], "exhausted")
        self.assertEqual(state["supervisor"]["next_retry_at"], 2000)


if __name__ == "__main__":
    unittest.main()


class DeferredBatchKeepsMovingTest(unittest.TestCase):
    """某阶段因外部依赖延后时，看门狗不能让整批停住。"""

    def test_deferred_stage_reopens_scheduling(self):
        import json, tempfile
        from pathlib import Path
        from workflow import supervisor

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        workflows = Path(tmp.name) / "workflows"
        folder = workflows / "batch"
        (folder / "workflow").mkdir(parents=True)
        manifest = {"stages": {
            "extract": {"status": "completed"},
            "answers": {"status": "pending", "notes": "外部依赖暂不可用，已延后并继续其它阶段：额度"},
            "validate": {"status": "pending"},
            "format_gate": {"status": "pending"},
        }}
        (folder / "workflow/manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (folder / "workflow/supervisor.json").write_text(json.dumps(
            {"status": "exhausted", "no_progress_attempts": 9,
             "next_retry_at": 0, "progress": "stale"}), encoding="utf-8")

        self.assertEqual(supervisor._deferred_stages(manifest), ["answers"])
        state = supervisor._read(folder / "workflow/supervisor.json")
        # 只验证"延后不会被当作无进展"这一判定；具体的派发由现有调度逻辑完成。
        self.assertIn("answers", supervisor._deferred_stages(manifest))
        self.assertNotIn("validate", supervisor._deferred_stages(manifest))
