"""Runner publishes deterministic failures only after releasing execution locks."""
import argparse
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from workflow import auto, manifest as mf, operations, runner


class RunnerOperationsHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.batch = Path(self.temp.name) / "batch"
        self.batch.mkdir()

    def test_explicit_runner_handoff_occurs_after_run_lock_release(self):
        lock_held = False

        @contextmanager
        def fake_run_lock(_batch):
            nonlocal lock_held
            lock_held = True
            try:
                yield
            finally:
                lock_held = False

        args = argparse.Namespace(batch_dir=str(self.batch), dry_run=False)
        with patch.object(runner, "_single_run_lock", fake_run_lock), \
                patch.object(runner, "_cmd_run_locked", return_value=1), \
                patch.object(operations, "handoff_if_available",
                             side_effect=lambda _batch: self.assertFalse(lock_held)) as handoff:
            self.assertEqual(runner.cmd_run(args), 1)
        handoff.assert_called_once_with(self.batch.resolve())

    def test_success_and_dry_run_do_not_publish_handoff(self):
        for dry_run, code in ((False, 0), (True, 1)):
            args = argparse.Namespace(batch_dir=str(self.batch), dry_run=dry_run)
            with patch.object(runner, "_single_run_lock"), \
                    patch.object(runner, "_cmd_run_locked", return_value=code), \
                    patch.object(operations, "handoff_if_available") as handoff:
                self.assertEqual(runner.cmd_run(args), code)
                handoff.assert_not_called()

    def test_automatic_result_uses_same_handoff_after_manifest_lock(self):
        manifest = mf.new_manifest("batch", self.batch, {}, {})
        mf.save(self.batch, manifest)
        handoff_seen = []

        def handoff(batch):
            handoff_seen.append(batch)
            return {"status": "operations_pending"}

        with patch("workflow.agent_protocol._agent_lock") as lock, \
                patch.object(operations, "handoff_if_available", side_effect=handoff), \
                patch("workflow.self_heal.observe_outcomes", return_value={}):
            # The operation lock is released before the event handoff is called.
            lock.return_value.__enter__.return_value = None
            lock.return_value.__exit__.side_effect = lambda *_: None
            result = auto._result(self.batch, manifest, "validate", "gate failed", "gate")
        self.assertEqual(handoff_seen, [self.batch])
        self.assertEqual(result["action"], "gate")


if __name__ == "__main__":
    unittest.main()
