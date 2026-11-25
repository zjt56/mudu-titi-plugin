"""Integration tests for dual-process workbench API/Worker split and optimizations."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from workbench import execution_client as ec
from workbench import server


class WorkerSplitIntegrationTests(unittest.TestCase):
    def test_per_batch_locks_are_isolated_per_batch_name(self):
        lock_a = server._batch_lock("batch-alpha")
        lock_b = server._batch_lock("batch-beta")
        self.assertIsNot(lock_a, lock_b)
        self.assertIs(lock_a, server._batch_lock("batch-alpha"))

    def test_split_mode_work_in_flight_protects_local_uploads_and_audio_only(self):
        orig_uploads = dict(server._upload_jobs)
        orig_audio = dict(server._audio_job)
        try:
            server._upload_jobs.clear()
            server._audio_job["running"] = False
            with mock.patch.dict(os.environ, {"WF_SPLIT_WORKER": "1"}, clear=False):
                self.assertFalse(server.work_in_flight())
                server._upload_jobs["b1"] = {"running": True}
                self.assertTrue(server.work_in_flight())
                server._upload_jobs.clear()
                server._audio_job["running"] = True
                self.assertTrue(server.work_in_flight())
        finally:
            server._upload_jobs.clear()
            server._upload_jobs.update(orig_uploads)
            server._audio_job["running"] = orig_audio.get("running", False)

    def test_launch_advance_in_split_mode_runs_prepare_then_delegates(self):
        with tempfile.TemporaryDirectory() as tmp:
            wf_dir = Path(tmp) / "workflows"
            batch = wf_dir / "b1"
            (batch / "workflow").mkdir(parents=True)
            prepared = []
            fake_client = mock.Mock()
            fake_client.advance_state.return_value = {"running": False}
            fake_client.enqueue.return_value = {"started": True, "queued": True}
            with mock.patch.object(server, "WORKFLOWS", wf_dir), \
                    mock.patch.dict(os.environ, {"WF_SPLIT_WORKER": "1"}, clear=False), \
                    mock.patch.object(server, "_remote_worker", return_value=fake_client), \
                    mock.patch.object(server, "_waiting_for_predecessor", return_value=None):
                res = server._launch_advance(
                    "b1", batch,
                    prepare=lambda: (prepared.append(True) or {"prepared": True}),
                    reset_budget=True,
                )
            self.assertEqual(prepared, [True])
            self.assertTrue(res["started"])
            fake_client.enqueue.assert_called_once()
            _, kwargs = fake_client.enqueue.call_args
            self.assertEqual(kwargs["details"], {"prepared": True})

    def test_worker_daemon_ipc_health_state_and_graceful_drain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wf_dir = root / "workflows"
            inbox_dir = root / "inbox"
            rt_dir = root / "runtime"
            wf_dir.mkdir()
            inbox_dir.mkdir()
            rt_dir.mkdir()

            env = dict(os.environ)
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
            env["WF_WORKFLOWS_DIR"] = str(wf_dir)
            env["WF_INBOX_DIR"] = str(inbox_dir)
            env["WF_RUNTIME_DIR"] = str(rt_dir)
            env["WF_SPLIT_WORKER"] = "0"

            proc = subprocess.Popen(
                [sys.executable, "-m", "workbench.worker", "--host", "127.0.0.1", "--port", "0"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                client = ec.RemoteWorkerClient(wf_dir)
                deadline = time.monotonic() + 10.0
                health = None
                while time.monotonic() < deadline:
                    try:
                        health = client.health(timeout=1.0)
                        if health.get("ok"):
                            break
                    except Exception:
                        time.sleep(0.1)
                self.assertIsNotNone(health)
                self.assertTrue(health["ok"])
                self.assertEqual(health["pid"], proc.pid)
                self.assertFalse(health["busy"])

                # Disk snapshot reader works with zero socket hops.
                snap = client.read_state()
                self.assertEqual(snap.get("pid"), proc.pid)
                self.assertTrue(snap.get("alive"))
                self.assertGreaterEqual(client.state_seq(), 1)

                # Wake supervisor works cleanly.
                wake = client.wake_supervisor()
                self.assertTrue(wake.get("ok"))

                # Unauthorized token must be rejected with 403.
                ep = json.loads(ec.endpoint_path(wf_dir).read_text(encoding="utf-8"))
                bad_req = urllib.request.Request(
                    f"http://127.0.0.1:{ep['port']}/internal/health",
                    headers={"X-Worker-Token": "wrong-token"},
                )
                with self.assertRaises(urllib.error.HTTPError) as bad_ctx:
                    urllib.request.urlopen(bad_req, timeout=2.0)
                self.assertEqual(bad_ctx.exception.code, 403)

                # Graceful drain exits the worker cleanly and cleans up endpoint file.
                drain = client.drain(timeout=3.0)
                self.assertTrue(drain.get("ok"))
                self.assertTrue(drain.get("draining"))
                exit_code = proc.wait(timeout=10.0)
                self.assertEqual(exit_code, 0)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5.0)
                    except subprocess.TimeoutExpired:
                        proc.kill()

    def test_api_batches_etag_and_304_not_modified(self):
        pending = {"name": "pending", "flow_done": False, "rows": [{"number": 2}]}
        done = {"name": "done", "flow_done": True, "uploaded": False,
                "rows": [{"unit_id": "unit-1"}], "extraction": {}, "structure_gate": {},
                "diagnostics": {}, "stage": {"items": [], "hint": "completed"}}
        uploaded = {"name": "uploaded", "flow_done": True, "uploaded": True}
        batches = [pending, done, uploaded]
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with mock.patch.object(server, "cached_list_batches", return_value=batches), \
                    mock.patch.object(server, "_is_split_worker", return_value=False), \
                    mock.patch.dict(os.environ, {"WF_WORKBENCH_TOKEN": ""}, clear=False):
                base = f"http://127.0.0.1:{port}/api/batches"
                cases = {
                    "": {"batches": batches},
                    "?lite=1": {"batches": [
                        {"name": "pending", "flow_done": False, "first_unit_id": "2"},
                        {"name": "done", "flow_done": True, "uploaded": False,
                         "structure_gate": {}, "diagnostics": {}, "stage": done["stage"],
                         "first_unit_id": "unit-1"}, uploaded]},
                    "?review_list=1&unuploaded=1": {"has_active_work": True, "batches": [
                        {"name": "done", "flow_done": True, "uploaded": False,
                         "stage": {"hint": "completed"}, "first_unit_id": "unit-1"}]},
                }
                etags = set()
                for query, expected in cases.items():
                    with self.subTest(query=query):
                        url = base + query
                        with urllib.request.urlopen(url, timeout=5.0) as resp:
                            self.assertEqual(resp.status, 200)
                            etag = resp.headers.get("ETag")
                            self.assertTrue(etag and etag.startswith('W/"'))
                            etags.add(etag)
                            self.assertEqual(json.loads(resp.read().decode("utf-8")), expected)
                        req = urllib.request.Request(url, headers={"If-None-Match": etag})
                        with self.assertRaises(urllib.error.HTTPError) as ctx:
                            urllib.request.urlopen(req, timeout=5.0)
                        self.assertEqual(ctx.exception.code, 304)
                self.assertEqual(len(etags), len(cases))
                self.assertEqual(done["rows"], [{"unit_id": "unit-1"}])
                self.assertEqual(done["stage"], {"items": [], "hint": "completed"})
        finally:
            httpd.shutdown()
            httpd.server_close()



    def test_health_does_not_deadlock_with_concurrent_cached_list_batches(self):
        # Regression test for AB-BA lock inversion between _batches_read_lock
        # (held by cached_list_batches -> _live_overview -> _lock) and
        # @serialized (previously acquiring _batches_read_lock inside _lock).
        entered_list = threading.Event()
        release_list = threading.Event()
        errors = []

        def slow_list():
            entered_list.set()
            release_list.wait(timeout=2.0)
            with server._lock:
                return [{"name": "b1"}]

        def reader():
            try:
                server.cached_list_batches()
            except Exception as exc:
                errors.append(exc)

        with mock.patch.object(server, "list_batches", side_effect=slow_list):
            t = threading.Thread(target=reader, daemon=True)
            t.start()
            self.assertTrue(entered_list.wait(timeout=2.0))
            # While reader holds _batches_read_lock and is about to acquire _lock,
            # calling a @serialized function must NOT deadlock.
            server._ensure_advance_worker()
            release_list.set()
            t.join(timeout=2.0)
            self.assertFalse(t.is_alive())
            self.assertEqual(errors, [])

    def test_cached_worker_state_rechecks_pid_liveness_on_same_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            wf_dir = Path(tmp) / "workflows"
            wf_dir.mkdir()
            ec.atomic_write_json(ec.state_path(wf_dir), {
                "seq": 5, "pid": 999999, "busy": True,
                "advance_state": {"b1": {"running": True}}, "queue": ["b1"],
            })
            client = ec.RemoteWorkerClient(wf_dir)
            with mock.patch.object(ec, "_pid_alive", return_value=True):
                snap1 = client.read_state()
                self.assertTrue(snap1["alive"])
                self.assertTrue(snap1["busy"])
            # Worker crashes without modifying worker-state.json (same mtime_ns).
            with mock.patch.object(ec, "_pid_alive", return_value=False):
                snap2 = client.read_state()
                self.assertFalse(snap2["alive"])
                self.assertFalse(snap2["busy"])
                self.assertEqual(snap2["advance_state"], {})
                self.assertEqual(snap2["queue"], [])


if __name__ == "__main__":
    unittest.main()
