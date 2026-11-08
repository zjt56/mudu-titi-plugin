"""Offline fairness and evidence-bound cooldown regressions."""
from dataclasses import replace
from unittest.mock import Mock

from workflow import contracts, manifest as mf, supervisor
from workflow.test_supervisor import SupervisorFixture, put


class SupervisorSchedulingTest(SupervisorFixture):
    def test_three_continuously_due_batches_share_single_dispatch_slot(self):
        self.config = replace(self.config, max_no_progress=100)
        for name in ("c", "a", "b"):
            self.make_batch(name)
        calls = []
        watch = self.make_supervisor(lambda name: calls.append(name))
        for _ in range(12):
            before = len(calls)
            watch.one_tick()
            self.assertEqual(len(calls), before + 1)
            # Every batch is due again before every tick; lexical a cannot win
            # simply because a previous dispatch made b/c temporarily ineligible.
            self.clock.now += 2000
        self.assertEqual(calls, ["a", "b", "c"] * 4)
        self.assertEqual(watch.config.max_dispatch_per_tick, 1)

    def test_restart_uses_persisted_order_with_never_dispatched_first(self):
        for name in ("a", "b", "c"):
            self.make_batch(name)
        calls = []
        for _ in range(9):
            self.make_supervisor(lambda name: calls.append(name)).one_tick()
            self.clock.now += 2000
        self.assertEqual(calls, ["a", "b", "c"] * 3)

    def test_timestamp_ties_use_name_and_undispatched_batches_go_first(self):
        for name, timestamp in (("a", 900), ("b", 800), ("c", 800)):
            folder = self.make_batch(name)
            put(folder / "workflow" / supervisor.LEDGER,
                {"last_dispatched_at": timestamp, "next_retry_at": 0})
        self.make_batch("z-new")
        calls = []
        for _ in range(4):
            self.make_supervisor(lambda name: calls.append(name)).one_tick()
            self.clock.now += 2000
        self.assertEqual(calls, ["z-new", "b", "c", "a"])

    def test_explicit_dispatch_limit_is_still_enforced(self):
        for name in ("a", "b", "c"):
            self.make_batch(name)
        self.config = replace(self.config, max_dispatch_per_tick=2)
        dispatch = Mock(return_value={"started": True})
        self.make_supervisor(dispatch).one_tick()
        self.assertEqual([call.args[0] for call in dispatch.call_args_list], ["a", "b"])

    def failed_inputs(self):
        folder = self.make_batch(stages={"extract": {"status": "failed"}})
        source = folder / "source.txt"
        source.write_text("broken input", encoding="utf-8")
        manifest = supervisor._read(folder / "workflow/manifest.json")
        manifest["inputs"] = {"source": {"path": str(source), "exists": True}}
        put(folder / "workflow/manifest.json", manifest)
        healing = {"last_stage": "extract", "next_retry_at": 9000,
                   "last_inputs": {"extract": mf.input_hashes(
                       folder, manifest, contracts.STAGE_BY_ID["extract"])}}
        put(folder / "workflow/self-heal.json", healing)
        return folder, source, manifest, healing

    def test_fixed_input_releases_copied_selfheal_cooldown_across_restart(self):
        folder, source, _, _ = self.failed_inputs()
        dispatch = Mock(return_value={"started": True})
        self.assertEqual(self.make_supervisor(dispatch).one_tick()["batches"]["active"], "cooldown")
        self.assertEqual(self.state(folder)["next_retry_at"], 9000)
        before = (folder / "workflow/manifest.json").read_bytes()
        healing_before = (folder / "workflow/self-heal.json").read_bytes()
        source.write_text("fixed input", encoding="utf-8")
        self.clock.now += 1
        self.assertEqual(self.make_supervisor(dispatch).one_tick()["batches"]["active"], "dispatched")
        dispatch.assert_called_once_with("active")
        # Scheduling reads evidence but does not migrate stage metadata or queue
        # model reruns by rewriting manifest/self-heal records.
        self.assertEqual((folder / "workflow/manifest.json").read_bytes(), before)
        self.assertEqual((folder / "workflow/self-heal.json").read_bytes(), healing_before)

    def test_latest_event_stage_can_supply_the_diagnosed_stage(self):
        folder, source, _, healing = self.failed_inputs()
        del healing["last_stage"]
        healing["events"] = [{"stage": "types"}, {"stage": "extract"}]
        put(folder / "workflow/self-heal.json", healing)
        source.write_text("fixed input", encoding="utf-8")
        self.assertEqual(self.make_supervisor().one_tick()["batches"]["active"], "dispatched")

    def test_provider_cooldown_cannot_be_bypassed_by_fixed_inputs(self):
        folder, source, _, _ = self.failed_inputs()
        path = folder / "workflow/agent-runs/.rate-limit-cooldown"
        path.parent.mkdir()
        path.write_text("1200")
        dispatch = Mock(return_value={"started": True})
        self.make_supervisor(dispatch).one_tick()
        source.write_text("fixed input", encoding="utf-8")
        self.clock.now = 1100
        self.assertEqual(self.make_supervisor(dispatch).one_tick()["batches"]["active"], "cooldown")
        self.assertEqual(self.state(folder)["next_retry_at"], 1200)
        dispatch.assert_not_called()
        self.clock.now = 1201
        self.assertEqual(self.make_supervisor(dispatch).one_tick()["batches"]["active"], "dispatched")

    def test_valid_supervisor_reservation_survives_changed_selfheal_inputs(self):
        folder, source, manifest, _ = self.failed_inputs()
        put(folder / "workflow" / supervisor.LEDGER, {"next_retry_at": 9000,
            "retry_reservation_at": 1300, "last_dispatched_at": 999,
            "progress": supervisor._progress(manifest)})
        source.write_text("fixed input", encoding="utf-8")
        dispatch = Mock(return_value={"started": True})
        self.assertEqual(self.make_supervisor(dispatch).one_tick()["batches"]["active"], "cooldown")
        self.assertEqual(self.state(folder)["next_retry_at"], 1300)
        dispatch.assert_not_called()
        self.clock.now = 1301
        self.assertEqual(self.make_supervisor(dispatch).one_tick()["batches"]["active"], "dispatched")

    def test_same_inputs_and_unrelated_progress_do_not_release_cooldown(self):
        folder, _, manifest, _ = self.failed_inputs()
        dispatch = Mock()
        watch = self.make_supervisor(dispatch)
        watch.one_tick()
        manifest["stages"]["types"] = {"status": "completed", "output_hashes": {"meta": "new"}}
        manifest["inputs"]["source"]["sha256"] = "unverified metadata change"
        put(folder / "workflow/manifest.json", manifest)
        self.assertEqual(watch.one_tick()["batches"]["active"], "cooldown")
        self.assertEqual(self.state(folder)["next_retry_at"], 9000)
        dispatch.assert_not_called()

    def test_missing_empty_or_wrong_stage_evidence_does_not_release(self):
        folder, source, manifest, original = self.failed_inputs()
        source.write_text("fixed input", encoding="utf-8")
        for last_inputs in (None, {}, {"extract": {}}, {"types": {"input:docx": "old"}}):
            with self.subTest(last_inputs=last_inputs):
                healing = dict(original, last_inputs=last_inputs)
                put(folder / "workflow/self-heal.json", healing)
                dispatch = Mock()
                self.assertEqual(self.make_supervisor(dispatch).one_tick()["batches"]["active"], "cooldown")
                dispatch.assert_not_called()
        # Completion/manifest bookkeeping without a diagnosed stage is no proof.
        put(folder / "workflow/self-heal.json", {"next_retry_at": 9000,
            "last_inputs": original["last_inputs"]})
        manifest["stages"]["extract"]["input_hashes"] = {"bookkeeping": "new"}
        put(folder / "workflow/manifest.json", manifest)
        self.assertEqual(self.make_supervisor().one_tick()["batches"]["active"], "cooldown")

    def test_same_fixed_input_only_resets_budget_once(self):
        folder, source, _, _ = self.failed_inputs()
        source.write_text("fixed input", encoding="utf-8")
        dispatch = Mock(return_value={"started": True})
        self.make_supervisor(dispatch).one_tick()
        self.assertEqual(self.state(folder)["no_progress_attempts"], 1)
        self.due(folder)
        self.make_supervisor(dispatch).one_tick()
        self.assertEqual(self.state(folder)["no_progress_attempts"], 2)

    def test_unrelated_deferred_audio_does_not_reset_failed_extract_budget(self):
        folder = self.make_batch(stages={
            "extract": {"status": "failed"},
            "audio": {"status": "pending", "notes": "已延后：无音频"}})
        manifest = supervisor._read(folder / "workflow/manifest.json")
        put(folder / "workflow" / supervisor.LEDGER, {"status": "exhausted",
            "no_progress_attempts": 3, "next_retry_at": 0,
            "progress": supervisor._progress(manifest)})
        for attempts in range(4, 8):
            self.make_supervisor().one_tick()
            state = self.state(folder)
            self.assertEqual(state["no_progress_attempts"], attempts)
            self.assertEqual(state["status"], "exhausted")
            self.assertEqual(state["next_retry_at"] - self.clock.now, 1000)
            self.due(folder)

    def test_new_ready_independent_work_unfreezes_only_once_across_restart(self):
        folder = self.make_batch(stages={
            "extract": {"status": "failed"},
            "structure_gate": {"status": "completed"},
            "preassemble": {"status": "completed"},
            "types": {"status": "pending"},
            "audio": {"status": "pending", "notes": "已延后：无音频"}})
        manifest = supervisor._read(folder / "workflow/manifest.json")
        put(folder / "workflow" / supervisor.LEDGER, {"status": "exhausted",
            "no_progress_attempts": 3, "next_retry_at": 0,
            "progress": supervisor._progress(manifest)})
        dispatch = Mock(return_value={"started": True})
        # A batch that already exhausted its budget keeps counting up while it
        # remains stalled, then re-dispatches after the cooldown; the counter is
        # never silently reset to zero by bookkeeping alone.
        previous = 3
        for _ in range(3):
            self.make_supervisor(dispatch).one_tick()
            current = self.state(folder)["no_progress_attempts"]
            self.assertGreaterEqual(current, previous)
            previous = current
            self.due(folder)
        self.assertIn(self.state(folder)["status"], ("exhausted", "scheduled", "active"))

    def test_unrelated_file_change_does_not_release_cooldown(self):
        folder, _, _, _ = self.failed_inputs()
        (folder / "unrelated.txt").write_text("changed", encoding="utf-8")
        dispatch = Mock()
        self.assertEqual(self.make_supervisor(dispatch).one_tick()["batches"]["active"], "cooldown")
        dispatch.assert_not_called()


if __name__ == "__main__":
    import unittest
    unittest.main()
