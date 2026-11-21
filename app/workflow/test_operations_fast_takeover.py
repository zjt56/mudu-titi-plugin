"""Regression tests for P0/P1/P2 AI operations fast takeover & trigger optimizations."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from workflow import auto, manifest as mf, operations as op, self_heal


def _iso_ago(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


class FastTakeoverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "workflows"
        self.folder = self.root / "paper-fast"
        (self.folder / "workflow").mkdir(parents=True)
        (self.folder / "07_review").mkdir(parents=True)
        self.hb_patch = mock.patch.object(
            op, "_HEARTBEAT_FILE", Path(self.tmp.name) / "hb.json"
        )
        self.hb_patch.start()
        op._service_until = 0.0
        self.manifest = {
            "workflow_id": "paper-fast",
            "status": "failed",
            "inputs": {},
            "stages": {
                "validate": {
                    "status": mf.FAILED,
                    "notes": "QUESTION_NOT_FOUND",
                    "updated_at": _iso_ago(5),
                }
            },
        }
        self._put("workflow/manifest.json", self.manifest)
        self._put("workflow/self-heal.json", {"events": []})

    def tearDown(self):
        op._service_until = 0.0
        self.hb_patch.stop()
        self.tmp.cleanup()

    def _put(self, rel: str, data):
        path = self.folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    # ---------------- P0: worker_busy release does not trigger exponential backoff

    def test_worker_busy_release_skips_backoff_and_release_count(self):
        op.service_heartbeat(True)
        with mock.patch.object(self_heal, "_candidate", return_value=("validate", "broken")), \
             mock.patch.object(self_heal, "_snapshot", return_value={"stage_inputs": {}, "files": {}}):
            found = op.incidents(self.root)["incidents"]
            self.assertEqual(len(found), 1)
            inc = found[0]
            claimed = op.claim(self.root, self.folder, {
                "incident_id": inc["incident_id"],
                "session_id": "sess-busy-1",
                "mode": "repair",
                "execution_first": True,
            })
            before = time.time()
            receipt = op.action(self.root, self.folder, {
                "incident_id": claimed["incident_id"],
                "attempt_id": claimed["attempt_id"],
                "session_id": claimed["session_id"],
                "request_id": "req-busy-rel-1",
                "action": "release",
                "input": {
                    "reason": "worker_busy: waiting for batch execution lock",
                    "reason_code": "worker_busy",
                    "busy_retry": True,
                    "state": "worker_busy",
                },
            })
            self.assertEqual(receipt["status"], "completed")
            updated = op._load(self.folder)["incident"]
            self.assertEqual(updated["status"], "queued")
            self.assertEqual(updated.get("release_count", 0), 0)
            self.assertLessEqual(updated["not_before"] - before, 4.0)
            self.assertEqual(updated["queue_generation"], 2)

    def test_normal_release_preserves_exponential_backoff(self):
        op.service_heartbeat(True)
        with mock.patch.object(self_heal, "_candidate", return_value=("validate", "broken")), \
             mock.patch.object(self_heal, "_snapshot", return_value={"stage_inputs": {}, "files": {}}):
            inc = op.incidents(self.root)["incidents"][0]
            claimed = op.claim(self.root, self.folder, {
                "incident_id": inc["incident_id"],
                "session_id": "sess-norm-1",
                "mode": "repair",
                "execution_first": True,
            })
            before = time.time()
            op.action(self.root, self.folder, {
                "incident_id": claimed["incident_id"],
                "attempt_id": claimed["attempt_id"],
                "session_id": claimed["session_id"],
                "request_id": "req-norm-rel-1",
                "action": "release",
                "input": {"reason": "idle incomplete", "state": "incomplete"},
            })
            updated = op._load(self.folder)["incident"]
            self.assertEqual(updated["release_count"], 1)
            self.assertGreaterEqual(updated["not_before"] - before, 55.0)

    # ---------------- P0: stale / orphaned RUNNING detection

    def test_orphaned_running_stage_escalates_after_grace(self):
        op.service_heartbeat(True)
        self.manifest["stages"]["validate"] = {
            "status": mf.RUNNING,
            "updated_at": _iso_ago(op.ORPHAN_RUNNING_GRACE_SECONDS + 10),
        }
        self._put("workflow/manifest.json", self.manifest)
        with mock.patch.object(self_heal, "_candidate", return_value=(None, "")), \
             mock.patch.object(op, "_execution_busy", return_value=False):
            found = op.incidents(self.root)["incidents"]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["stage"], "validate")
            self.assertEqual(found[0]["trigger"], "stale_running")

    def test_fresh_running_stage_without_timestamp_does_not_escalate(self):
        op.service_heartbeat(True)
        self.manifest["stages"]["validate"] = {"status": mf.RUNNING}
        self._put("workflow/manifest.json", self.manifest)
        with mock.patch.object(self_heal, "_candidate", return_value=(None, "")), \
             mock.patch.object(op, "_execution_busy", return_value=False):
            self.assertEqual(op.incidents(self.root)["incidents"], [])

    def test_stale_running_stage_escalates_even_when_busy(self):
        op.service_heartbeat(True)
        self.manifest["stages"]["validate"] = {
            "status": mf.RUNNING,
            "started_at": _iso_ago(op.STALE_RUNNING_SECONDS + 30),
        }
        self._put("workflow/manifest.json", self.manifest)
        with mock.patch.object(self_heal, "_candidate", return_value=(None, "")), \
             mock.patch.object(op, "_execution_busy", return_value=True):
            found = op.incidents(self.root)["incidents"]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["trigger"], "stale_running")

    # ---------------- P1: repeated transient failure escalation + debounce

    def test_repeated_transient_failure_escalates_at_threshold_with_debounce(self):
        op.service_heartbeat(True)
        detail = "Q1：Upstream service temporarily unavailable"
        with mock.patch.object(self_heal, "_candidate", return_value=("validate", detail)), \
             mock.patch.object(self_heal, "_snapshot", return_value={"stage_inputs": {}, "files": {}}):
            # Rapid polling with unchanged stage updated_at should NOT inflate count.
            for _ in range(op.TRANSIENT_ESCALATION_THRESHOLD + 2):
                self.assertEqual(op.incidents(self.root)["incidents"], [])
            # Advancing stage updated_at simulates genuine new retry failures.
            for idx in range(1, op.TRANSIENT_ESCALATION_THRESHOLD - 1):
                self.manifest["stages"]["validate"]["updated_at"] = _iso_ago(4 - idx)
                self._put("workflow/manifest.json", self.manifest)
                self.assertEqual(op.incidents(self.root)["incidents"], [])
            self.manifest["stages"]["validate"]["updated_at"] = _iso_ago(0)
            self._put("workflow/manifest.json", self.manifest)
            found = op.incidents(self.root)["incidents"]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["trigger"], "repeated_transient")
            # Subsequent discovery must NOT retire it back to transient_deferred.
            again = op.incidents(self.root)["incidents"]
            self.assertEqual(len(again), 1)
            self.assertEqual(again[0]["status"], "queued")

    # ---------------- P1: shortened cooldowns in observe_execution

    def test_observe_execution_unchanged_uses_short_cooldown(self):
        op.service_heartbeat(True)
        with mock.patch.object(self_heal, "_candidate", return_value=("validate", "broken")), \
             mock.patch.object(self_heal, "_snapshot", return_value={"stage_inputs": {}, "files": {}}), \
             mock.patch.object(op, "_verify", return_value={"verified": False, "reason": "still broken"}):
            inc = op.incidents(self.root)["incidents"][0]
            state = op._load(self.folder)
            state["incident"]["status"] = "awaiting_execution"
            state["incident"]["snapshot"] = op._snapshot(self.folder, "validate")
            op._save(self.folder, state)
            before = time.time()
            op.observe_execution(self.folder)
            updated = op._load(self.folder)["incident"]
            self.assertEqual(updated["status"], "queued")
            self.assertLessEqual(updated["not_before"] - before, 8.0)

    # ---------------- P2: manual takeover

    def test_manual_takeover_clears_cooldown_and_freeze(self):
        op.service_heartbeat(True)
        with mock.patch.object(self_heal, "_candidate", return_value=("validate", "broken")), \
             mock.patch.object(self_heal, "_snapshot", return_value={"stage_inputs": {}, "files": {}}):
            inc = op.incidents(self.root)["incidents"][0]
            state = op._load(self.folder)
            state["incident"].update(
                status="waiting_evidence",
                eligible=False,
                not_before=time.time() + 900,
                release_count=4,
            )
            op._save(self.folder, state)
            res = op.takeover(self.root, self.folder)
            self.assertTrue(res["ok"])
            self.assertTrue(res["triggered"])
            updated = res["incident"]
            self.assertEqual(updated["status"], "queued")
            self.assertTrue(updated["eligible"])
            self.assertEqual(updated["not_before"], 0)
            self.assertEqual(updated["release_count"], 0)
            self.assertEqual(updated["trigger"], "manual_takeover")

    def test_manual_takeover_forces_discovery_when_no_incident_yet(self):
        # Even if service heartbeat is offline, manual takeover forces incident creation.
        op._service_until = 0.0
        with mock.patch.object(self_heal, "_candidate", return_value=("validate", "broken")), \
             mock.patch.object(self_heal, "_snapshot", return_value={"stage_inputs": {}, "files": {}}):
            res = op.takeover(self.root, self.folder)
            self.assertTrue(res["ok"])
            self.assertTrue(res["triggered"])
            self.assertEqual(res["incident"]["trigger"], "manual_takeover")
            self.assertEqual(res["incident"]["not_before"], 0)

    def test_manual_takeover_respects_unknown_receipt_quarantine(self):
        op.service_heartbeat(True)
        with mock.patch.object(self_heal, "_candidate", return_value=("validate", "broken")), \
             mock.patch.object(self_heal, "_snapshot", return_value={"stage_inputs": {}, "files": {}}):
            op.incidents(self.root)
            state = op._load(self.folder)
            state["receipts"].append({"request_id": "unk-1", "status": "unknown"})
            op._save(self.folder, state)
            with self.assertRaisesRegex(ValueError, "unknown_receipt_quarantine"):
                op.takeover(self.root, self.folder)

    # ---------------- P0: auto.py fast yield

    def test_auto_fast_yield_breaks_when_no_independent_stage_remains(self):
        # Set all stages up to assemble as completed, validate failed -> only review/review_gate/upload remain,
        # all of which depend on validate or are upload gates. auto should break immediately after handoff.
        from workflow import contracts
        manifest = {
            "workflow_id": "paper-fast",
            "status": "running",
            "inputs": {},
            "stages": {},
        }
        for spec in contracts.STAGES:
            if spec.id in {"validate", "format_gate", "review", "review_gate", "upload_gate", "upload", "publish_gate"}:
                manifest["stages"][spec.id] = {"status": mf.PENDING}
            else:
                manifest["stages"][spec.id] = {"status": mf.COMPLETED}
        self._put("workflow/manifest.json", manifest)

        handoff_calls = []

        def fake_handoff(folder):
            handoff_calls.append(str(folder))
            return {"changed": False, "status": "operations_pending", "stage": "validate"}

        from workflow import agent_tasks
        with mock.patch.object(op, "handoff_if_available", side_effect=fake_handoff), \
             mock.patch.object(op, "lease_active", return_value=False), \
             mock.patch.object(mf, "reconcile_deliveries"), \
             mock.patch.object(agent_tasks, "stage_gaps", return_value={}), \
             mock.patch.object(auto, "_recover_portable_results", return_value=(manifest, set())), \
             mock.patch.object(auto.rn, "do_validate", return_value=1), \
             mock.patch.object(auto.rn, "do_review_gate", side_effect=AssertionError("should not reach review_gate")):
            res = auto._advance_impl(self.folder, {}, audio_worker=None)
            self.assertEqual(res["stopped_at"], "validate")
            self.assertGreaterEqual(len(handoff_calls), 1)

    # ---------------- Round 2 P0-1: awaiting_restart release skips backoff

    def test_awaiting_restart_release_skips_backoff(self):
        op.service_heartbeat(True)
        with mock.patch.object(self_heal, "_candidate", return_value=("validate", "broken")), \
             mock.patch.object(self_heal, "_snapshot", return_value={"stage_inputs": {}, "files": {}}):
            inc = op.incidents(self.root)["incidents"][0]
            claimed = op.claim(self.root, self.folder, {
                "incident_id": inc["incident_id"],
                "session_id": "sess-restart-1",
                "mode": "repair",
                "execution_first": True,
            })
            before = time.time()
            receipt = op.action(self.root, self.folder, {
                "incident_id": claimed["incident_id"],
                "attempt_id": claimed["attempt_id"],
                "session_id": claimed["session_id"],
                "request_id": "req-restart-rel-1",
                "action": "release",
                "input": {
                    "reason": "installed repair awaits host restart before execution and verification",
                    "state": "awaiting_restart",
                },
            })
            self.assertEqual(receipt["status"], "completed")
            updated = op._load(self.folder)["incident"]
            self.assertEqual(updated["status"], "queued")
            self.assertEqual(updated.get("release_count", 0), 0)
            self.assertLessEqual(updated["not_before"] - before, 3.0)

    # ---------------- Round 2 P0-2: deterministic stale_running reset in recover

    def test_recover_resets_stale_running_stage_deterministically(self):
        op.service_heartbeat(True)
        self.manifest["stages"]["validate"] = {
            "status": mf.RUNNING,
            "updated_at": _iso_ago(op.ORPHAN_RUNNING_GRACE_SECONDS + 20),
        }
        self._put("workflow/manifest.json", self.manifest)
        with mock.patch.object(self_heal, "_candidate", return_value=(None, "")), \
             mock.patch.object(op, "_execution_busy", return_value=False):
            inc = op.incidents(self.root)["incidents"][0]
            self.assertEqual(inc["trigger"], "stale_running")
            claimed = op.claim(self.root, self.folder, {
                "incident_id": inc["incident_id"],
                "session_id": "sess-stale-1",
                "mode": "repair",
                "execution_first": True,
            })
            receipt = op.action(self.root, self.folder, {
                "incident_id": claimed["incident_id"],
                "attempt_id": claimed["attempt_id"],
                "session_id": claimed["session_id"],
                "request_id": "req-stale-rec-1",
                "action": "recover",
                "input": {},
            })
            self.assertEqual(receipt["status"], "completed")
            res = receipt["evidence"]
            self.assertEqual(res["repair"], "reset_stale_running")
            self.assertTrue(res["resume_required"])
            reloaded = mf.load(self.folder)
            self.assertEqual(reloaded["stages"]["validate"]["status"], mf.PENDING)

    # ---------------- Round 2 P0-3: manual reconcile_unknown

    def test_reconcile_unknown_unblocks_quarantined_incident_without_replay(self):
        op.service_heartbeat(True)
        with mock.patch.object(self_heal, "_candidate", return_value=("validate", "broken")), \
             mock.patch.object(self_heal, "_snapshot", return_value={"stage_inputs": {}, "files": {}}):
            op.incidents(self.root)
            state = op._load(self.folder)
            state["receipts"].append({
                "request_id": "unk-manual-1",
                "action": "recover",
                "status": "unknown",
            })
            state["incident"].update(status="waiting_evidence", eligible=False)
            op._save(self.folder, state)
            res = op.reconcile_unknown(self.root, self.folder, note="verified by operator")
            self.assertTrue(res["ok"])
            self.assertEqual(res["reconciled"], 1)
            after = op._load(self.folder)
            unk = [r for r in after["receipts"] if r.get("request_id") == "unk-manual-1"][0]
            self.assertEqual(unk["status"], "failed")
            self.assertFalse(unk["reconciliation"]["replayed"])
            self.assertEqual(after["incident"]["status"], "queued")
            self.assertTrue(after["incident"]["eligible"])

    # ---------------- Round 2 P2-9: receipt compaction

    def test_compact_receipts_preserves_unknown_and_caps_settled(self):
        state = {"identity": op._identity(self.folder), "incident": None, "receipts": [], "failures": {}}
        big_text = "X" * 2500
        receipts = []
        for i in range(140):
            receipts.append({
                "request_id": f"req-{i}",
                "action": "read_evidence",
                "status": "completed",
                "evidence": {"text": big_text, "path": "a.txt"},
            })
        receipts.insert(0, {"request_id": "unk-keep", "action": "recover", "status": "unknown"})
        state["receipts"] = receipts
        op._save(self.folder, state)
        saved = op._load(self.folder)["receipts"]
        unknowns = [r for r in saved if r.get("status") == "unknown"]
        settled = [r for r in saved if r.get("status") != "unknown"]
        self.assertEqual(len(unknowns), 1)
        self.assertEqual(len(settled), op.MAX_STORED_RECEIPTS)
        # Older settled receipts have large text compacted
        self.assertTrue(settled[0]["evidence"].get("compacted"))
        self.assertNotIn("text", settled[0]["evidence"])
        # Most recent 20 settled receipts retain full text
        self.assertEqual(settled[-1]["evidence"]["text"], big_text)


if __name__ == "__main__":
    unittest.main()
