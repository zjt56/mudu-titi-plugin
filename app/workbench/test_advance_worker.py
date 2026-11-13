"""Offline regression coverage for the serial worker's lifecycle and reconciliation."""
import json
import tempfile
import threading
import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from workbench import server


class AdvanceWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for key, value in (("WORKFLOWS", self.root), ("_advance_state", {}),
                           ("_advance_queue", []), ("_advance_worker_thread", None),
                           ("_advance_worker_running", False),
                           ("_advance_worker_start_error", None),
                           ("_workflow_supervisor", None)):
            item = patch.object(server, key, value)
            item.start()
            self.addCleanup(item.stop)
        for target, value in (("_waiting_for_predecessor", None),):
            item = patch.object(server, target, return_value=value)
            item.start()
            self.addCleanup(item.stop)
        claim = patch("workflow.supervisor.advance_claim", side_effect=lambda folder: nullcontext(True))
        claim.start()
        self.addCleanup(claim.stop)

    def batch(self, name="batch"):
        folder = self.root / name
        (folder / "workflow").mkdir(parents=True)
        (folder / "workflow/manifest.json").write_text(json.dumps({"stages": {}}))
        return folder

    def test_start_failure_retains_queue_and_duplicate_request_recovers(self):
        folder = self.batch()
        failed = Mock()
        failed.start.side_effect = RuntimeError("thread unavailable")
        recovered = Mock()
        recovered.is_alive.return_value = True
        prepare = Mock(return_value={"prepared": True})
        with patch.object(server.threading, "Thread", side_effect=[failed, recovered]) as factory:
            self.assertTrue(server._launch_advance("batch", folder, prepare=prepare)["queued"])
            self.assertFalse(server._advance_worker_running)
            self.assertIsNone(server._advance_worker_thread)
            self.assertEqual(server._advance_queue, ["batch"])
            self.assertEqual(server._advance_worker_start_error, "thread unavailable")
            self.assertFalse(server._launch_advance("batch", folder, prepare=prepare)["started"])
            self.assertTrue(server._advance_worker_running)
            self.assertIs(server._advance_worker_thread, recovered)
            self.assertIsNone(server._advance_worker_start_error)
            self.assertEqual(factory.call_count, 2)
            prepare.assert_called_once()
            self.assertEqual(server._advance_queue, ["batch"])

    def test_constructor_failure_can_be_retried_without_losing_queue(self):
        server._advance_queue[:] = ["batch"]
        with patch.object(server.threading, "Thread", side_effect=RuntimeError("no resources")):
            self.assertFalse(server._ensure_advance_worker())
        self.assertFalse(server._advance_worker_running)
        worker = Mock(is_alive=Mock(return_value=True))
        with patch.object(server.threading, "Thread", return_value=worker):
            self.assertTrue(server._ensure_advance_worker())
        self.assertEqual(server._advance_queue, ["batch"])
        worker.start.assert_called_once()

    def test_concurrent_ensure_starts_only_one_thread(self):
        server._advance_queue[:] = ["batch"]
        worker = Mock(is_alive=Mock(return_value=True))
        callers = [threading.Thread(target=server._ensure_advance_worker) for _ in range(4)]
        # Build the callers before replacing the Thread constructor used by ensure.
        with patch.object(server.threading, "Thread", return_value=worker) as factory:
            for caller in callers:
                caller.start()
            for caller in callers:
                caller.join(2)
            factory.assert_called_once()
            worker.start.assert_called_once()

    def test_health_reconciles_stale_flag_and_reports_oldest_age(self):
        server._advance_queue[:] = ["one", {"name": "two"}]
        server._advance_worker_running = True
        server._advance_worker_thread = Mock(is_alive=Mock(return_value=False))
        server._advance_state.update({"one": {"queued_at": (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()},
                                      "two": {"queued_at": "bad"}})
        worker = Mock(is_alive=Mock(return_value=True))
        with patch.object(server.threading, "Thread", return_value=worker):
            health = server.advance_worker_health()
        worker.start.assert_called_once()
        self.assertTrue(health["worker_alive"])
        self.assertEqual(health["queue_length"], 2)
        self.assertGreaterEqual(health["queue_oldest_age_seconds"], 90)

    def test_live_worker_is_not_replaced_even_if_flag_false(self):
        server._advance_queue[:] = ["batch"]
        server._advance_worker_thread = Mock(is_alive=Mock(return_value=True))
        with patch.object(server.threading, "Thread") as factory:
            self.assertTrue(server._ensure_advance_worker())
            factory.assert_not_called()
        self.assertTrue(server._advance_worker_running)

    def test_dead_worker_with_running_owner_is_visible_and_not_replayed(self):
        server._advance_state["owner"] = {"running": True}
        server._advance_queue[:] = ["later"]
        server._advance_worker_thread = Mock(is_alive=Mock(return_value=False))
        with patch.object(server.threading, "Thread") as factory:
            health = server.advance_worker_health()
        factory.assert_not_called()
        self.assertTrue(health["running_without_worker"])
        self.assertEqual(health["running_batches"], ["owner"])
        self.assertEqual(server._advance_queue, ["later"])

    def test_worker_exit_releases_handle_before_next_enqueue(self):
        worker = Mock(is_alive=Mock(return_value=True))
        server._advance_worker_thread = worker
        server._advance_worker_running = True
        server._advance_worker_loop()
        self.assertIsNone(server._advance_worker_thread)
        server._advance_queue.append("new")
        replacement = Mock(is_alive=Mock(return_value=True))
        with patch.object(server.threading, "Thread", return_value=replacement):
            self.assertTrue(server._ensure_advance_worker())
        replacement.start.assert_called_once()

    def test_health_endpoint_exposes_worker_health(self):
        handler = object.__new__(server.Handler)
        handler.path = "/api/health"
        handler.send_json = Mock()
        with patch.object(server, "work_in_flight", return_value=False):
            handler.do_GET()
        health = handler.send_json.call_args.args[0]
        self.assertTrue(health["ok"])
        self.assertEqual(health["advance_worker"]["queue_length"], 0)
        self.assertFalse(health["advance_worker"]["worker_alive"])

    def test_empty_queue_does_not_start_thread(self):
        server._advance_worker_running = True
        with patch.object(server.threading, "Thread") as factory:
            health = server.advance_worker_health()
            factory.assert_not_called()
        self.assertFalse(server._advance_worker_running)
        self.assertFalse(health["worker_alive"])
        self.assertIsNone(health["queue_oldest_age_seconds"])

    def test_duplicate_launch_while_worker_runs_does_not_enqueue_again(self):
        folder = self.batch()
        entered = threading.Event()
        release = threading.Event()
        calls = []
        def run(name, folder):
            calls.append(name)
            entered.set()
            release.wait(3)
            with server._lock:
                server._advance_state[name] = {"running": False}
        try:
            with patch.object(server, "_run_advance_now", side_effect=run), \
                    patch.object(server, "_wake_supervisor"):
                server._launch_advance("batch", folder)
                self.assertTrue(entered.wait(2))
                for _ in range(5):
                    self.assertFalse(server._launch_advance("batch", folder)["started"])
                worker = server._advance_worker_thread
                release.set()
                worker.join(3)
                self.assertFalse(worker.is_alive())
                self.assertIsNone(server._advance_worker_thread)
        finally:
            release.set()
            if server._advance_worker_thread is not None:
                server._advance_worker_thread.join(3)
        self.assertEqual(calls, ["batch"])

    def test_worker_serial_fifo_and_skips_cancelled_or_missing_batches(self):
        for name in ("one", "two", "deleted", "removed"):
            self.batch(name)
        (self.root / "deleted/workflow/.delete-requested").touch()
        (self.root / "removed" / server.QUEUE_REMOVED).touch()
        server._advance_queue[:] = ["one", "deleted", "missing", "removed", "two"]
        calls = []
        with patch.object(server, "_run_advance_now", side_effect=lambda name, folder: calls.append(name)):
            server._advance_worker_loop()
        self.assertEqual(calls, ["one", "two"])
        self.assertEqual(server._advance_queue, [])
        self.assertFalse(server._advance_worker_running)
        for name in ("deleted", "removed", "missing"):
            self.assertNotIn(name, server._advance_state)

    def test_deleted_batch_cannot_be_reenqueued(self):
        folder = self.batch()
        (folder / "workflow/.delete-requested").touch()
        with patch.object(server, "_ensure_advance_worker") as ensure:
            self.assertTrue(server._launch_advance("batch", folder, reset_budget=True)["removed"])
        ensure.assert_not_called()
        self.assertEqual(server._advance_queue, [])

    def test_worker_rechecks_predecessor_before_execution(self):
        self.batch("later")
        server._advance_queue[:] = ["later"]
        with patch.object(server, "_waiting_for_predecessor", return_value="earlier"), \
                patch.object(server, "_run_advance_now") as run:
            server._advance_worker_loop()
        run.assert_not_called()
        self.assertEqual(server._advance_state["later"]["waiting_for"], "earlier")


if __name__ == "__main__":
    unittest.main()
