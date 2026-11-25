# -*- coding: utf-8 -*-
"""Regression tests for unified filelock, atomic retry, quarantine caching,
parallel salvage, and Windows UTF-8 subprocess defaults.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from workflow import filelock, manifest as mf, unit_anomalies


class FileLockTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_try_file_lock_excludes_contended_caller_and_rejects_symlink(self):
        lock_path = self.root / "a.lock"
        with filelock.try_file_lock(lock_path) as first:
            self.assertTrue(first)
            self.assertTrue(filelock.is_file_locked(lock_path))
            with filelock.try_file_lock(lock_path) as second:
                self.assertFalse(second)
        self.assertFalse(filelock.is_file_locked(lock_path))

        if os.name != "nt":
            sym = self.root / "sym.lock"
            sym.symlink_to(lock_path)
            with filelock.try_file_lock(sym) as acquired:
                self.assertFalse(acquired)
            self.assertFalse(filelock.is_file_locked(sym))

    def test_file_lock_timeout_and_release(self):
        lock_path = self.root / "sub" / "b.lock"
        with filelock.file_lock(lock_path, timeout=1.0):
            with self.assertRaises(TimeoutError):
                with filelock.file_lock(lock_path, timeout=0.06, poll_interval=0.01):
                    pass
        with filelock.file_lock(lock_path, timeout=0.5):
            pass

    def test_acquire_slot_lock_waits_and_uses_free_slot(self):
        slots = [self.root / "slot-0.lock", self.root / "slot-1.lock"]
        waited = []
        with filelock.file_lock(slots[0], timeout=1.0):
            with filelock.acquire_slot_lock(slots, poll_interval=0.01, on_wait=lambda: waited.append(1)):
                self.assertEqual(waited, [])
                self.assertTrue(filelock.is_file_locked(slots[1]))
            self.assertFalse(filelock.is_file_locked(slots[1]))

        # All slots held: verify wait callback fires and acquisition succeeds once released.
        released = threading.Event()
        acquired = threading.Event()
        with filelock.file_lock(slots[0], timeout=1.0), filelock.file_lock(slots[1], timeout=1.0):
            def worker():
                with filelock.acquire_slot_lock(slots, poll_interval=0.01, on_wait=lambda: waited.append("wait")):
                    acquired.set()
                    released.wait(2.0)

            t = threading.Thread(target=worker)
            t.start()
            time.sleep(0.05)
            self.assertTrue(waited)
        self.assertTrue(acquired.wait(2.0))
        released.set()
        t.join(2.0)


class QuarantineCacheAndRetryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def test_atomic_write_json_retries_transient_permission_error(self):
        target = unit_anomalies.anomalies_path(self.folder)
        real_replace = os.replace
        calls = {"n": 0}

        def flaky(src, dst):
            if Path(dst) == target and calls["n"] < 2:
                calls["n"] += 1
                raise PermissionError(13, "simulated WinError 32")
            return real_replace(src, dst)

        with patch.object(mf.os, "replace", side_effect=flaky):
            unit_anomalies.record_anomalies(self.folder, "answers", {"u1": "bad answer"})
        self.assertEqual(calls["n"], 2)
        self.assertEqual(unit_anomalies.quarantined_unit_ids(self.folder, "answers"), {"u1"})

    def test_quarantined_unit_ids_uses_mtime_cache_and_invalidates_on_change(self):
        unit_anomalies.record_anomalies(self.folder, "knowledge", {"u1": "bad kp"})
        first = unit_anomalies.quarantined_unit_ids(self.folder, "knowledge")
        self.assertEqual(first, {"u1"})

        with patch.object(unit_anomalies, "quarantined_entries", side_effect=AssertionError("should hit cache")):
            second = unit_anomalies.quarantined_unit_ids(self.folder, "knowledge")
        self.assertEqual(second, {"u1"})

        # Mutating the ledger invalidates the cache immediately.
        unit_anomalies.clear_stage_anomalies(self.folder, "knowledge", ["u1"])
        self.assertEqual(unit_anomalies.quarantined_unit_ids(self.folder, "knowledge"), set())


class ParallelSalvageTest(unittest.TestCase):
    def test_quarantine_type_partition_salvages_units_in_parallel(self):
        from workflow import quarantine_policy, unit_tasks

        with tempfile.TemporaryDirectory() as td:
            batch = Path(td)
            run_dir = batch / "run"
            run_dir.mkdir(parents=True)
            units = [
                {"unit_id": "u1", "unit_kind": "standalone", "numbers": [1], "stem": "Q1"},
                {"unit_id": "u2", "unit_kind": "standalone", "numbers": [2], "stem": "Q2"},
            ]
            part = {"unit_ids": ["u1", "u2"], "prompt": "p"}
            barrier = threading.Barrier(2, timeout=3.0)
            seen_parallel = []

            def fake_build_parts(_batch, _stage, _max_chars, max_units=1, selected_ids=None):
                uid = selected_ids[0]
                return {}, [{"unit_ids": [uid], "prompt": f"prompt-{uid}"}]

            def fake_invoke(prompt, sub_dir, config, **kwargs):
                seen_parallel.append(bool(kwargs.get("permit_parallel")))
                barrier.wait()  # Proves both units run concurrently!
                uid = "u1" if "prompt-u1" in prompt else "u2"
                return {
                    "response": {
                        "status": "completed",
                        "artifacts": {
                            "03_types/types.json": {
                                "items": [{
                                    "unit_id": uid, "type": "单项填空", "subtypes": ["四个选项"],
                                    "words": 3, "contentComplete": True, "evidence": ["L1"],
                                }]
                            }
                        },
                        "notes": [],
                        "findings": [],
                    }
                }

            with patch("workflow.agent_tasks.confirmed_units", return_value=units), \
                    patch.object(unit_tasks, "build_parts", side_effect=fake_build_parts), \
                    patch.object(unit_tasks, "check_part_inputs"), \
                    patch.object(unit_tasks, "validate_part"), \
                    patch("workflow.local_agent._check_input_hashes"):
                merged = quarantine_policy.quarantine_type_partition(
                    batch,
                    part,
                    ValueError("partition failed"),
                    run_dir=run_dir,
                    part_index=0,
                    config={"max_input_chars": 10000, "part_concurrency": 2},
                    invoke=fake_invoke,
                    invocation_options={},
                    strict_hashes={},
                )

            self.assertEqual(seen_parallel, [True, True])
            items = merged["artifacts"]["03_types/types.json"]["items"]
            self.assertEqual([row["unit_id"] for row in items], ["u1", "u2"])


class WindowsSubprocessEncodingTest(unittest.TestCase):
    def test_bootstrap_preflight_sets_utf8_and_pythonioencoding(self):
        from audio import bootstrap

        captured = {}

        def fake_run(cmd, **kwargs):
            captured.update(kwargs)
            return type("P", (), {"stdout": "3.12\n", "stderr": "", "returncode": 0})()

        with patch.object(bootstrap, "venv_python", return_value=Path("/nonexistent/venv/python")), \
                patch.object(bootstrap, "base_python", return_value="/fake/python3.12"), \
                patch.object(bootstrap, "tool_path", return_value="/usr/bin/ffmpeg"), \
                patch("audio.bootstrap.subprocess.run", side_effect=fake_run):
            bootstrap.preflight({})
        self.assertEqual(captured.get("encoding"), "utf-8")
        self.assertEqual(captured.get("errors"), "replace")
        self.assertEqual((captured.get("env") or {}).get("PYTHONIOENCODING"), "utf-8")

    def test_batch_upload_runner_sets_utf8_and_pythonioencoding(self):
        from workbench import batch_upload

        captured = {}

        def fake_run(cmd, **kwargs):
            captured.update(kwargs)
            return type("P", (), {"stdout": "", "stderr": "", "returncode": 0})()

        with patch("workbench.batch_upload.subprocess.run", side_effect=fake_run):
            batch_upload._run_runner(["status"])
        self.assertEqual(captured.get("encoding"), "utf-8")
        self.assertEqual(captured.get("errors"), "replace")
        self.assertEqual((captured.get("env") or {}).get("PYTHONIOENCODING"), "utf-8")

    def test_assemble_precheck_sets_utf8_and_pythonioencoding(self):
        from workflow import assemble_precheck

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(kwargs)
            return type("P", (), {"stdout": "", "stderr": "", "returncode": 0})()

        with patch("workflow.assemble_precheck.subprocess.run", side_effect=fake_run):
            res = assemble_precheck.precheck({"items": []})
        self.assertTrue(res["passed"])
        self.assertTrue(calls)
        for kw in calls:
            self.assertEqual(kw.get("encoding"), "utf-8")
            self.assertEqual(kw.get("errors"), "replace")
            self.assertEqual((kw.get("env") or {}).get("PYTHONIOENCODING"), "utf-8")


if __name__ == "__main__":
    unittest.main()
