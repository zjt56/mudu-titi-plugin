"""Regression tests for P0-P4 AI Operations improvements."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from workflow import manifest as mf, operations as op, operations_development as dev, self_heal


def _seed_batch(root: Path, name: str = "demo-batch") -> Path:
    folder = root / name
    (folder / "workflow").mkdir(parents=True)
    (folder / "01_source").mkdir(parents=True)
    manifest = {
        "batch_name": name,
        "status": "failed",
        "stages": {
            "parse": {"status": mf.COMPLETED},
            "structure_gate": {"status": mf.COMPLETED},
            "review_gate": {
                "status": mf.FAILED,
                "error": "REVIEW_SAMPLE_SOURCE_UNVERIFIED: source_binding mismatch",
            },
        },
    }
    mf.save(folder, manifest)
    return folder


class OperationsImprovementsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.orig_service_until = op._service_until
        self.orig_hb_file = op._HEARTBEAT_FILE
        op._service_until = 0.0
        op._HEARTBEAT_FILE = self.root / "hb.json"

    def tearDown(self):
        op._service_until = self.orig_service_until
        op._HEARTBEAT_FILE = self.orig_hb_file
        self.tmp.cleanup()

    # --- P0: Cross-process heartbeat fallback ---
    def test_cross_process_heartbeat_fallback(self):
        self.assertFalse(op.service_available())
        # Same PID in file should NOT override local _service_until == 0.0
        op._HEARTBEAT_FILE.write_text(json.dumps({
            "pid": os.getpid(), "available": True, "until_epoch": time.time() + 30
        }), encoding="utf-8")
        self.assertFalse(op.service_available())
        # Different alive PID (pid=1) with valid TTL should be recognized as available
        op._HEARTBEAT_FILE.write_text(json.dumps({
            "pid": 1, "available": True, "until_epoch": time.time() + 30
        }), encoding="utf-8")
        self.assertTrue(op.service_available())
        # Expired different PID returns False
        op._HEARTBEAT_FILE.write_text(json.dumps({
            "pid": 1, "available": True, "until_epoch": time.time() - 5
        }), encoding="utf-8")
        self.assertFalse(op.service_available())

    # --- P0: Deterministic review_gate admission vs quality_differences rejection ---
    def test_discover_admits_deterministic_review_gate_blocker(self):
        folder = _seed_batch(self.root)
        op.service_heartbeat(True)
        with mock.patch.object(self_heal, "_candidate", return_value=("review_gate", "REVIEW_SAMPLE_SOURCE_UNVERIFIED")), \
             mock.patch.object(op, "_review_differences", return_value=False):
            state = op._discover(folder)
        self.assertIsNotNone(state.get("incident"))
        self.assertEqual(state["incident"]["stage"], "review_gate")
        self.assertEqual(state["incident"]["status"], "queued")

    def test_discover_rejects_quality_differences_review_gate(self):
        folder = _seed_batch(self.root, "diff-batch")
        manifest = mf.load(folder)
        manifest["stages"]["review_gate"] = {
            "status": mf.NEEDS_REVIEW,
            "execution_completed": True,
            "continue_unqualified": True,
        }
        mf.save(folder, manifest)
        with mock.patch.object(self_heal, "_candidate", return_value=("review_gate", "quality differences")), \
             mock.patch.object(op, "_review_differences", return_value=True):
            state = op._discover(folder)
        self.assertIsNone(state.get("incident"))

    # --- P0 & P2: self_heal delegation and source_binding producer ---
    def test_self_heal_producer_recognizes_source_binding(self):
        folder = _seed_batch(self.root, "prod-batch")
        self.assertEqual(
            self_heal._producer_for_issue({"code": "REVIEW_SAMPLE_SOURCE_UNVERIFIED", "message": ""}),
            "structure_gate",
        )
        self.assertEqual(
            self_heal._error_producer(folder, "review_gate", "failure_class=source_binding"),
            "structure_gate",
        )

    def test_self_heal_recover_yields_to_operations_when_service_available(self):
        folder = _seed_batch(self.root, "yield-batch")
        with mock.patch.object(self_heal, "_candidate", return_value=("review_gate", "REVIEW_SAMPLE_SOURCE_UNVERIFIED")), \
             mock.patch.object(self_heal, "_busy", return_value=False), \
             mock.patch.object(op, "service_available", return_value=True):
            res = self_heal.recover(folder)
        self.assertFalse(res.get("changed"))
        self.assertEqual(res.get("status"), "delegated_to_operations")

    # --- P1: _packet suspected_sources & recommended_action ---
    def test_packet_includes_suspected_sources_and_recommended_action(self):
        folder = _seed_batch(self.root, "pkt-batch")
        incident = {
            "incident_id": "inc-1",
            "stage": "review_gate",
            "detail": "REVIEW_SAMPLE_SOURCE_UNVERIFIED: source_binding error",
            "signature": "sig-1",
            "attempts": [],
        }
        pkt = op._packet(folder, incident)
        self.assertIn("suspected_sources", pkt)
        self.assertIn("recommended_action", pkt)
        self.assertIn("app/workflow/structure.py", pkt["suspected_sources"])
        self.assertIn("upstream_rederive", pkt["recommended_action"])

    # --- P3: patch_source diagnostics, patch_lines, multi_patch, export_patch_bundle, fast tests ---
    def test_code_repair_patch_extensions_and_fast_test_gate(self):
        src_root = self.root / "src_repo"
        (src_root / "app" / "workflow").mkdir(parents=True)
        target = src_root / "app" / "workflow" / "sample_mod.py"
        target.write_text("alpha = 1\nbeta = 2\ngamma = 3\nalpha = 1\n", encoding="utf-8")
        test_file = src_root / "app" / "workflow" / "test_sample_mod.py"
        test_file.write_text(
            "import unittest\n"
            "from workflow import sample_mod\n"
            "class T(unittest.TestCase):\n"
            "    def test_ok(self): self.assertEqual(sample_mod.beta, 20)\n",
            encoding="utf-8",
        )
        ws_root = self.root / "workspaces"
        ws_root.mkdir(parents=True, exist_ok=True)
        job = dev.create(src_root, ws_root)
        info = dev.read(job, "app/workflow/sample_mod.py")
        sha = info["sha256"]

        # 1. patch_source mismatch includes current_sha256 and near_lines
        with self.assertRaises(ValueError) as ctx:
            dev.patch_source(job, "app/workflow/sample_mod.py", "alpha = 1", "alpha = 9", expected_sha=sha)
        self.assertIn("current_sha256=" + sha, str(ctx.exception))
        self.assertIn("near_lines=[1, 4]", str(ctx.exception))

        # 2. patch_lines replaces exact line range
        res1 = dev.patch_lines(job, "app/workflow/sample_mod.py", 2, 2, "beta = 20", expected_sha=sha)
        sha2 = res1["sha256"]
        self.assertIn("beta = 20\n", dev.read(job, "app/workflow/sample_mod.py")["text"])

        # 3. multi_patch atomically applies multiple replacements
        res2 = dev.multi_patch(
            job,
            "app/workflow/sample_mod.py",
            [
                {"old_text": "beta = 20", "new_text": "beta = 20"},
                {"old_text": "gamma = 3", "new_text": "gamma = 30"},
            ],
            expected_sha=sha2,
        )
        self.assertTrue(res2["written"])
        self.assertIn("gamma = 30", dev.read(job, "app/workflow/sample_mod.py")["text"])

        # 4. export_patch_bundle flags hot-reloadability
        bundle = dev.export_patch_bundle(job)
        self.assertTrue(bundle["available"])
        self.assertFalse(bundle["non_hot_reloadable"])
        self.assertIn("gamma = 30", bundle["patch"])

        # 5. fast=True skips CORE_REGRESSIONS, while prepare_install still enforces them
        with mock.patch.object(dev, "required_core_regressions", return_value=["workflow.test_missing_core"]), \
             mock.patch.object(dev, "_check_core_regression_sources", return_value=None):
            fast_res = dev.run_tests(job, ["workflow.test_sample_mod"], fast=True)
            self.assertTrue(fast_res["passed"])
            self.assertTrue(fast_res["fast"])
            with self.assertRaises(ValueError) as prep_ctx:
                dev.prepare_install(job)
            self.assertIn("core_regression_tests_required", str(prep_ctx.exception))

    def test_budget_installs_raised_to_three(self):
        self.assertEqual(op.BUDGET_INSTALLS, 3)

    # --- P4: unfreeze, performance_summary, code_repair_diff ---
    def test_unfreeze_resets_budgets_and_respects_unknown_receipt_quarantine(self):
        folder = _seed_batch(self.root, "freeze-batch")
        state = {
            "identity": op._identity(folder),
            "incident": {
                "incident_id": "inc-frz",
                "stage": "review_gate",
                "status": "waiting_evidence",
                "eligible": False,
                "not_before": time.time() + 600,
                "release_count": 3,
                "queue_generation": 2,
                "attempt_count": 10,
                "install_count": 2,
                "regeneration_count": 4,
                "attempts": [],
                "events": [],
                "recovery": {"state": "waiting_evidence", "reason": "no progress"},
            },
            "receipts": [],
            "failures": {},
        }
        op._save(folder, state)

        res = op.unfreeze(self.root, folder)
        self.assertTrue(res["unfrozen"])
        inc = res["incident"]
        self.assertEqual(inc["status"], "queued")
        self.assertTrue(inc["eligible"])
        self.assertEqual(inc["queue_generation"], 3)
        self.assertEqual(inc["budget_baseline"], 10)
        self.assertEqual(inc["install_baseline"], 2)

        perf = op.performance_summary(self.root, folder)
        self.assertTrue(perf["has_incident"])
        self.assertEqual(perf["budgets"]["attempts"], 0)
        self.assertEqual(perf["budgets"]["installs"], 0)

        diff_res = op.code_repair_diff(self.root, folder)
        self.assertFalse(diff_res["available"])

        # Unknown receipt must block unfreeze
        state2 = op._load(folder)
        state2["incident"]["status"] = "waiting_evidence"
        state2["receipts"] = [{"request_id": "req-u", "status": "unknown"}]
        op._save(folder, state2)
        with self.assertRaises(ValueError) as ctx:
            op.unfreeze(self.root, folder)
        self.assertIn("unknown_receipt_quarantine", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
