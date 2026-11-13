"""Offline investigation admission tests; disposable batches, no model calls."""
import concurrent.futures
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import operations as op, supervisor


class InvestigationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "workflows"
        self.folder = self.root / "paper"
        (self.folder / "workflow").mkdir(parents=True)
        (self.folder / "workflow/manifest.json").write_text(json.dumps({
            "stages": {"validate": {"status": "failed"}}, "inputs": {}}))
        (self.folder / "workflow/self-heal.json").write_text(json.dumps({"events": [{
            "stage": "validate", "detail": "broken", "signature": "failure",
            "outcome": "repair_failed", "attempt": 1}]}))
        self.candidate = patch.object(op.self_heal, "_candidate", return_value=("validate", "broken")).start()
        self.snapshot = patch.object(op, "_snapshot", return_value="original").start()
        self.addCleanup(patch.stopall)

    def claim(self, mode="investigation"):
        incident = op.incidents(self.root)["incidents"][0]
        return op.claim(self.root, self.folder, {"incident_id": incident["incident_id"],
                        "session_id": "session", "mode": mode})

    def payload(self, claim, name, request="request", data=None):
        return {**{key: claim[key] for key in ("incident_id", "session_id", "attempt_id")},
                "action": name, "request_id": request, "input": data or {}}

    def test_no_progress_release_parks_same_evidence_without_reclaim_loop(self):
        claim = self.claim('repair')
        with patch.object(op, '_recovery_plan', return_value={'evidence': 'same', 'progress': 'same', 'next_actions': ['code_repair']}):
            receipt = op.action(self.root, self.folder,
                self.payload(claim, 'release', data={'state': 'no_substantive_progress', 'reason': 'read-only loop'}))
            self.assertEqual(receipt['status'], 'completed')
            self.assertEqual(op.view(self.root, self.folder)['incident']['status'], 'waiting_evidence')
            self.assertEqual(op.incidents(self.root)['incidents'], [])
            self.assertFalse(op.lease_active(self.folder))
        with patch.object(op, '_recovery_plan', return_value={'evidence': 'changed', 'progress': 'changed', 'next_actions': ['code_repair']}):
            self.assertEqual(len(op.incidents(self.root)['incidents']), 1)

    def test_investigation_discovery_claim_and_tools_work_under_execution_locks(self):
        with supervisor._try_file_lock(supervisor._auto_lock_path(self.folder)) as auto:
            with supervisor._try_file_lock(self.folder / "workflow/.run.lock") as runner:
                self.assertTrue(auto and runner)
                claimed = self.claim()
                self.assertEqual(claimed["mode"], "investigation")
                self.assertFalse(op.lease_active(self.folder))
                with patch.object(op, "_packet", return_value={"safe": True}):
                    result = op.action(self.root, self.folder, self.payload(claimed, "diagnose"))
                self.assertEqual(result["status"], "completed")
                finding = op.action(self.root, self.folder, self.payload(claimed, "record_finding", "finding",
                                    {"summary": "evidence", "incident_id": "other"}))
                self.assertEqual(finding["evidence"]["incident_id"], claimed["incident_id"])
                busy_payload = self.payload(claimed, "promote", "upgrade")
                with self.assertRaises(op.OperationsBusy):
                    op.action(self.root, self.folder, busy_payload)
                self.assertFalse(any(row["request_id"] == "upgrade" for row in op._load(self.folder)["receipts"]))
        promoted = op.action(self.root, self.folder, busy_payload)
        new = promoted["evidence"]["incident"]
        self.assertEqual(new["mode"], "repair")
        self.assertNotEqual(new["attempt_id"], claimed["attempt_id"])
        self.assertEqual(new["fence"], claimed["fence"] + 1)
        self.assertTrue(op.lease_active(self.folder))
        self.assertEqual(op.action(self.root, self.folder, busy_payload), promoted)
        with self.assertRaisesRegex(ValueError, "stale_lease"):
            op.action(self.root, self.folder, self.payload(claimed, "record_finding", "late"))
        self.assertEqual(op.action(self.root, self.folder, self.payload(new, "heartbeat", "heartbeat"))["status"], "completed")

    def test_investigation_cannot_use_any_repair_tool_or_claim_upgrade(self):
        claimed = self.claim()
        for name in op.ACTIONS - op.INVESTIGATION_ACTIONS - {"promote"}:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "repair_claim_required"):
                op.action(self.root, self.folder, self.payload(claimed, name, name))
        with self.assertRaisesRegex(ValueError, "explicit_promotion_required"):
            op.claim(self.root, self.folder, {"incident_id": claimed["incident_id"], "session_id": "session"})
        self.assertEqual(op._load(self.folder)["receipts"], [])

    def test_changed_snapshot_remains_readable_but_cannot_promote(self):
        claimed = self.claim()
        self.snapshot.return_value = "changed"
        finding = op.action(self.root, self.folder, self.payload(claimed, "record_finding", data={"summary": "new"}))
        self.assertEqual(finding["evidence"]["snapshot"], "original")
        self.assertEqual(finding["evidence"]["observed_snapshot"], "changed")
        self.assertEqual(op._load(self.folder)["incident"]["snapshot"], "original")
        refused = op.promote(self.root, self.folder, self.payload(claimed, "promote", "upgrade"))
        self.assertEqual(refused["status"], "failed")
        self.assertEqual(refused["evidence"]["code"], "inputs_changed")
        self.assertFalse(op.lease_active(self.folder))

    def test_unknown_receipt_quarantine_blocks_promotion(self):
        claimed = self.claim()
        state = op._load(self.folder)
        state["receipts"].append({"request_id": "unknown", "status": "unknown"})
        op._save(self.folder, state)
        with self.assertRaisesRegex(ValueError, "unknown_receipt_quarantine"):
            op.promote(self.root, self.folder, self.payload(claimed, "promote"))
        self.assertEqual(op._load(self.folder)["incident"]["attempt_id"], claimed["attempt_id"])

    def test_metadata_lock_serializes_writers_and_never_uses_auto_lock(self):
        claimed = self.claim()
        with op._metadata_locked(self.root, self.folder):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                task = pool.submit(op.action, self.root, self.folder, self.payload(claimed, "record_finding"))
                with self.assertRaises(op.OperationsBusy):
                    task.result()
        self.assertEqual(op._load(self.folder)["receipts"], [])

    def test_default_claim_is_repair_and_legacy_lease_blocks_auto(self):
        claimed = self.claim("repair")
        self.assertTrue(op.lease_active(self.folder))
        state = op._load(self.folder)
        del state["incident"]["mode"]
        op._save(self.folder, state)
        self.assertTrue(op.lease_active(self.folder))
        self.assertEqual(op.action(self.root, self.folder, self.payload(claimed, "heartbeat"))["status"], "completed")

    def test_ready_finding_survives_release_without_repeating_investigation(self):
        claimed = self.claim()
        op.action(self.root, self.folder, self.payload(claimed, "record_finding", data={"summary": "cause"}))
        op.action(self.root, self.folder, self.payload(claimed, "release", "release"))
        state = op._load(self.folder)
        state["incident"]["not_before"] = 0
        op._save(self.folder, state)
        fresh = self.claim()
        self.assertTrue(fresh["investigation_ready"])
        op.action(self.root, self.folder, self.payload(fresh, "release", "release-new"))
        state = op._load(self.folder)
        state["incident"]["not_before"] = 0
        op._save(self.folder, state)
        self.snapshot.return_value = "changed"
        self.assertFalse(self.claim()["investigation_ready"])

    def test_code_change_requires_fresh_investigation_before_promotion(self):
        with patch.object(op, "_code_fingerprint", return_value="code-before") as code:
            claimed = self.claim()
            code.return_value = "code-after"
            result = op.promote(self.root, self.folder, self.payload(claimed, "promote"))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["evidence"]["code"], "inputs_changed")
        self.assertEqual(op._load(self.folder)["incident"]["attempt_id"], claimed["attempt_id"])
        self.assertFalse(op.lease_active(self.folder))

    def test_investigation_does_not_consume_repair_budget(self):
        claimed = self.claim()
        before = op._budgets(op._load(self.folder)["incident"])
        for number in range(3):
            op.action(self.root, self.folder, self.payload(claimed, "record_finding", str(number),
                                                        {"summary": "evidence"}))
        self.assertEqual(op._budgets(op._load(self.folder)["incident"]), before)

    def test_run_lock_alone_prevents_repair_promotion(self):
        claimed = self.claim()
        with supervisor._try_file_lock(self.folder / "workflow/.run.lock") as acquired:
            self.assertTrue(acquired)
            with self.assertRaises(op.OperationsBusy):
                op.promote(self.root, self.folder, self.payload(claimed, "promote"))
        self.assertEqual(op._load(self.folder)["receipts"], [])
        self.assertFalse(op.lease_active(self.folder))

    def test_performance_event_can_never_promote(self):
        claimed = self.claim()
        state = op._load(self.folder)
        state["incident"]["kind"] = "performance"
        op._save(self.folder, state)
        result = op.promote(self.root, self.folder, self.payload(claimed, "promote"))
        self.assertEqual(result["status"], "failed")
        self.assertIn("performance_investigation_read_only", result["evidence"]["error"])
        self.assertFalse(op.lease_active(self.folder))

    def test_changed_identity_and_deletion_never_write_replacement_batch(self):
        claimed = self.claim()
        old_workflow = self.folder / "old-workflow"
        (self.folder / "workflow").rename(old_workflow)
        (self.folder / "workflow").mkdir()
        (self.folder / "workflow/manifest.json").write_text(json.dumps({"stages": {}, "inputs": {}}))
        with self.assertRaisesRegex(ValueError, "incident_mismatch"):
            op.action(self.root, self.folder, self.payload(claimed, "record_finding"))
        self.assertFalse((self.folder / "workflow/operations.json").exists())
        (self.folder / "workflow/manifest.json").unlink()
        (self.folder / "workflow/.operations.lock").unlink()
        (self.folder / "workflow").rmdir()
        with self.assertRaises((ValueError, FileNotFoundError)):
            op.action(self.root, self.folder, self.payload(claimed, "record_finding"))
        self.assertFalse((self.folder / "workflow").exists())
