# -*- coding: utf-8 -*-
"""Performance and stability regression tests for workbench server and taxonomy."""
from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from workbench import server, taxonomy
from workflow import contracts, execution_state, operations


class WorkbenchPerfStabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inbox = self.root / "inbox"
        self.workflows = self.root / "workflows"
        self.inbox.mkdir(parents=True)
        self.workflows.mkdir(parents=True)
        self.batch = self.workflows / "paper-idle"
        (self.batch / "workflow").mkdir(parents=True)
        (self.batch / "06_batch").mkdir(parents=True)
        (self.batch / "07_review").mkdir(parents=True)

        self.final_batch = self.batch / "06_batch" / "batch_meta.json"
        self.final_batch.write_text(json.dumps({"items": []}, ensure_ascii=False), encoding="utf-8")
        self.final_sha = hashlib.sha256(self.final_batch.read_bytes()).hexdigest()

        stages = {s.id: {"status": "completed"} for s in contracts.STAGES}
        for remote in execution_state.REMOTE_STAGES:
            stages[remote] = {"status": "pending"}
        self.manifest = {
            "workflow_id": "paper-idle",
            "status": "pending_confirmation",
            "stages": stages,
        }
        self._write("workflow/manifest.json", self.manifest)
        self._write("07_review/upload-format.json", {"passed": True, "final_sha256": self.final_sha})

        self.addCleanup(patch.stopall)
        patch.object(server, "WORKFLOWS", self.workflows).start()
        patch.object(server, "INBOX", self.inbox).start()
        patch.object(server, "_batches_snapshot", None).start()
        patch.object(server, "_batches_epoch", 0).start()
        patch.object(server, "_batches_read_lock", threading.Lock()).start()
        patch.object(server, "_OVERVIEW_CACHE", {}).start()
        patch.object(server, "_OVERVIEW_SNAPSHOT", {}).start()
        patch.object(server, "_WORK_STATE_CACHE", {}).start()
        patch.object(server, "_advance_state", {}).start()
        patch.object(server, "_upload_jobs", {}).start()

    def _write(self, relative: str, payload: dict):
        path = self.batch / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_idle_overview_fast_path_skips_redundant_disk_reads_and_tracks_attempts(self):
        first = server.overview("paper-idle")
        self.assertTrue(first["flow_done"])

        # Second call on an unchanged idle batch must hit _OVERVIEW_CACHE and skip upload_state/final_format_complete.
        with patch.object(server, "upload_state", side_effect=AssertionError("idle fast path should not call upload_state")), \
             patch.object(server, "final_format_complete", side_effect=AssertionError("idle fast path should not re-hash format")):
            second = server.overview("paper-idle")
        self.assertTrue(second["flow_done"])
        self.assertEqual(second["name"], "paper-idle")

        # Writing a new upload attempt file must change _overview_key and recompute upload_status.
        old_key = server._overview_key(self.batch)
        self._write("07_upload/attempts/a1.json", {"state": "rejected", "details": "quota"})
        new_key = server._overview_key(self.batch)
        self.assertNotEqual(old_key, new_key)
        third = server.overview("paper-idle")
        self.assertEqual((third.get("upload_status") or {}).get("state"), "failed")

    def test_operations_service_post_does_not_bust_batches_snapshot(self):
        # Populate _batches_snapshot
        server.cached_list_batches()
        self.assertIsNotNone(server._batches_snapshot)
        epoch_before = server._batches_epoch

        captured = {}

        class FakePostHandler(server.Handler):
            def __init__(self, path, body):
                self.path = path
                self.headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
                self.rfile = BytesIO(body)
                self.wfile = BytesIO()

            def _check_request_security(self, **kwargs):
                return True

            def send_json(self, payload, status=200):
                captured["status"] = status
                captured["payload"] = payload

        raw = json.dumps({"available": True, "include_work_state": True}).encode("utf-8")
        handler = FakePostHandler("/api/operations/service", raw)
        with patch("workflow.operations.service_heartbeat", return_value={"available": True}):
            handler.do_POST()

        self.assertEqual(captured["status"], 200)
        self.assertFalse(captured["payload"]["has_work"])
        self.assertIsNotNone(server._batches_snapshot, "operations heartbeat must not invalidate _batches_snapshot")
        self.assertEqual(server._batches_epoch, epoch_before)

    def test_operations_work_state_caches_unchanged_folders_and_invalidates_on_change(self):
        res1 = server.operations_work_state()
        self.assertFalse(res1["has_work"])

        # Unchanged folder must hit _WORK_STATE_CACHE without re-reading manifest.
        with patch.object(server.wf_manifest, "load", side_effect=AssertionError("should hit work-state cache")):
            res2 = server.operations_work_state()
        self.assertFalse(res2["has_work"])

        # Updating manifest to running must immediately invalidate cache and report active work.
        self.manifest["stages"]["format_gate"] = {"status": "running"}
        self._write("workflow/manifest.json", self.manifest)
        res3 = server.operations_work_state()
        self.assertTrue(res3["has_work"])
        self.assertIn("paper-idle", res3["batches"])

    def test_work_state_refreshes_when_only_operations_ledger_changes(self):
        self.assertFalse(server.operations_work_state()["has_work"])
        ledger = {"identity": operations._identity(self.batch), "incident": {"status": "queued"}}
        operations._save(self.batch, ledger)
        self.assertEqual(server.operations_work_state(), {"has_work": True, "batches": ["paper-idle"]})
        ledger["incident"]["status"] = "resolved"
        operations._save(self.batch, ledger)
        self.assertEqual(server.operations_work_state(), {"has_work": False, "batches": []})

    def test_send_json_absorbs_broken_pipe_without_raising(self):
        class BrokenPipeStream:
            def write(self, _data):
                raise BrokenPipeError("client disconnected")

        class FakeHandler(server.Handler):
            def __init__(self):
                self.wfile = BrokenPipeStream()

            def send_response(self, _status):
                pass

            def send_header(self, _k, _v):
                pass

            def end_headers(self):
                pass

        # Must not raise or pollute stderr.
        FakeHandler().send_json({"ok": True})

    def test_taxonomy_leaf_index_is_exact_and_knowledge_tree_is_cached(self):
        by_path, leaves = taxonomy.knowledge_index()
        self.assertGreater(len(by_path), 100)
        # Verify O(N) leaf set matches exact prefix definition on a sample + full invariant.
        for path in leaves:
            self.assertFalse(any(other != path and other.startswith(path + "/") for other in by_path))
        non_leaves = set(by_path) - leaves
        for path in list(non_leaves)[:50]:
            self.assertTrue(any(other.startswith(path + "/") for other in by_path))

        taxonomy._knowledge_tree_cached.cache_clear()
        t1 = taxonomy.knowledge_tree("阅读理解")
        info1 = taxonomy._knowledge_tree_cached.cache_info()
        t2 = taxonomy.knowledge_tree("阅读理解")
        info2 = taxonomy._knowledge_tree_cached.cache_info()
        self.assertIs(t1, t2)
        self.assertEqual(info2.hits, info1.hits + 1)


if __name__ == "__main__":
    unittest.main()
