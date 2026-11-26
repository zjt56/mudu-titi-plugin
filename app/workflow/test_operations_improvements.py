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
        self.assertIn("app/workflow/structure_gate.py", pkt["suspected_sources"])
        self.assertIn("upstream_rederive", pkt["recommended_action"])
        self.assertIn("unit_excerpts", pkt)
        self.assertIn("log_tail", pkt)

    def test_stage_source_hints_point_to_existing_files(self):
        repo_root = Path(__file__).resolve().parents[2]
        for stage, paths in op._STAGE_SOURCE_HINTS.items():
            for rel in paths:
                with self.subTest(stage=stage, rel=rel):
                    self.assertTrue((repo_root / rel).is_file(), f"missing hint {rel} for stage {stage}")

    def test_code_repair_requires_explicit_workspace_request_and_normalizes_test_names(self):
        src_root = self.root / "src_auto"
        (src_root / "app" / "workflow").mkdir(parents=True)
        (src_root / "app" / "workflow" / "sample_mod.py").write_text(
            "\n".join(f"x_{i} = {i}" for i in range(1, 150)) + "\n", encoding="utf-8"
        )
        (src_root / "app" / "workflow" / "test_sample_mod.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        folder = _seed_batch(self.root, "auto-init-batch")
        incident = {"incident_id": "inc-auto", "stage": "validate", "detail": "fail", "attempts": []}
        with mock.patch.object(op, "_source_root", return_value=src_root), \
             mock.patch.object(op, "_code_root", return_value=self.root / "dev-root"):
            with self.assertRaisesRegex(ValueError, "code_repair_not_requested"):
                op._code_repair(folder, incident, {
                    "operation": "read_lines", "path": "app/workflow/sample_mod.py", "start_line": 1,
                })
            op._request_code_repair(folder, incident, {"summary": "explicit repair request"})
            res = op._code_repair(folder, incident, {
                "operation": "read_lines", "path": "app/workflow/sample_mod.py", "start_line": 1,
            })
            self.assertNotIn("workspace_auto_initialized", res)
            self.assertEqual(res["limit"], 200)
            self.assertEqual(len(res["lines"]), 149)
            tested = op._code_repair(folder, incident, {
                "operation": "test",
                "tests": ["app/workflow/test_sample_mod.py"],
                "fast": True,
            })
            self.assertTrue(tested["passed"])
            self.assertEqual(tested["tests"], ["workflow.test_sample_mod"])

    def test_read_evidence_allows_safe_artifacts_and_prompts_while_blocking_secrets(self):
        folder = _seed_batch(self.root, "ev-batch")
        (folder / "01_source" / "materials").mkdir(parents=True, exist_ok=True)
        (folder / "01_source" / "material-roles.json").write_text('{"roles": []}', encoding="utf-8")
        (folder / "01_source" / "materials" / "notes.txt").write_text("answer key", encoding="utf-8")
        (folder / "01_source" / "materials" / "secret_token.txt").write_text("nope", encoding="utf-8")
        (folder / "workflow" / "repair-feedback").mkdir(parents=True, exist_ok=True)
        (folder / "workflow" / "repair-feedback" / "answers.json").write_text('{"units": []}', encoding="utf-8")
        self.assertIn("roles", op._read_evidence(folder, {"path": "01_source/material-roles.json"})["text"])
        self.assertIn("answer key", op._read_evidence(folder, {"path": "01_source/materials/notes.txt"})["text"])
        self.assertIn("units", op._read_evidence(folder, {"path": "workflow/repair-feedback/answers.json"})["text"])
        prompt_res = op._read_evidence(folder, {"scope": "plugin", "path": "app/prompts/roles/answers.md", "limit": 120})
        self.assertEqual(prompt_res["path"], "app/prompts/roles/answers.md")
        with self.assertRaisesRegex(ValueError, "evidence_not_allowlisted"):
            op._read_evidence(folder, {"path": "01_source/materials/secret_token.txt"})
        with self.assertRaisesRegex(ValueError, "evidence_not_allowlisted"):
            op._read_evidence(folder, {"path": "workflow/manifest.json"})

    def test_recover_auto_resumes_restarted_pending_verify(self):
        folder = _seed_batch(self.root, "reverify-batch")
        op._save(folder, {
            "identity": op._identity(folder),
            "incident": {
                "incident_id": "inc-rv", "stage": "assemble", "status": "claimed",
                "mode": "repair", "eligible": False, "attempt_id": "att-1",
                "session_id": "sess-1", "lease_expires_at": 9999999999.0,
                "execution_process": op._process_identity(),
                "snapshot": op._snapshot(folder, "assemble"),
                "detail": "assemble failed", "attempts": [],
            },
            "receipts": [], "failures": {},
        })
        op._save_code_record(folder, {
            "job_id": "cr-1", "path": str(self.root / "unused.json"),
            "status": "restarted_pending_verify", "incident_id": "inc-rv",
        })
        with mock.patch.object(op, "_verify", return_value={"verified": False, "stage": "assemble", "reason": "not yet"}):
            receipt = op.action(self.root, folder, {
                "action": "recover", "request_id": "req-rv",
                "incident_id": "inc-rv", "attempt_id": "att-1", "session_id": "sess-1", "input": {},
            })
        self.assertEqual(receipt["status"], "completed")
        ev = receipt["evidence"]
        self.assertEqual(ev["phase"], "restarted_auto_resume")
        self.assertTrue(ev["resume_required"])
        self.assertEqual(ev["resume_input"]["strategy"], "retry_stage_with_fresh_context")

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

    def test_awaiting_execution_does_not_preempt_running_stage(self):
        folder = _seed_batch(self.root, "await-running-batch")
        manifest = mf.load(folder)
        mf.touch_stage(manifest, "assemble", mf.PENDING, notes="requeued for reverify")
        mf.save(folder, manifest)
        op._save(folder, {
            "identity": op._identity(folder),
            "incident": {
                "incident_id": "inc-await", "stage": "assemble", "status": "awaiting_execution",
                "mode": "repair", "eligible": False, "attempt_id": None,
                "session_id": "sess-1", "lease_expires_at": 0,
                "snapshot": op._snapshot(folder, "assemble"),
                "detail": "assemble failed", "attempts": [], "events": [],
                "queue_generation": 1,
            },
            "receipts": [], "failures": {},
        })
        op._save_code_record(folder, {
            "job_id": "cr-await", "path": str(self.root / "unused.json"),
            "status": "restarted_pending_verify", "incident_id": "inc-await",
        })
        # Pipeline transitions stage from pending -> running (snapshot changes!)
        manifest = mf.load(folder)
        mf.touch_stage(manifest, "assemble", mf.RUNNING, notes="running")
        mf.save(folder, manifest)
        v1 = op.view(self.root, folder)
        self.assertEqual(v1["incident"]["status"], "awaiting_execution")
        self.assertEqual(op._code_record(folder)["status"], "restarted_pending_verify")

        # Even if stage becomes failed while batch execution lock is still held, do not preempt
        manifest = mf.load(folder)
        mf.touch_stage(manifest, "assemble", mf.FAILED, notes="still failing")
        mf.save(folder, manifest)
        with mock.patch.object(op.supervisor, "execution_busy", return_value=True):
            v2 = op.view(self.root, folder)
            self.assertEqual(v2["incident"]["status"], "awaiting_execution")

        # Once stage finished (failed) and execution lock is released, re-queue and settle restarted_pending_verify
        with mock.patch.object(op.supervisor, "execution_busy", return_value=False):
            v3 = op.view(self.root, folder)
            self.assertEqual(v3["incident"]["status"], "queued")
            self.assertEqual(op._code_record(folder)["status"], "failed")

    def test_recover_readonly_verify_does_not_set_write_active(self):
        folder = _seed_batch(self.root, "recover-ro-batch")
        op._save(folder, {
            "identity": op._identity(folder),
            "incident": {
                "incident_id": "inc-ro", "stage": "knowledge", "status": "claimed",
                "mode": "repair", "eligible": False, "attempt_id": "att-ro",
                "session_id": "sess-ro", "lease_expires_at": 9999999999.0,
                "execution_process": op._process_identity(),
                "execution_first": True,
                "snapshot": op._snapshot(folder, "knowledge"),
                "detail": "knowledge failed", "attempts": [], "events": [],
            },
            "receipts": [], "failures": {},
        })
        observed = {}

        def spy_verify(f, inc):
            observed["write_active"] = (op._load(f).get("incident") or {}).get("write_active")
            observed["lease_active"] = op.lease_active(f)
            return {"verified": False, "stage": "knowledge", "reason": "not yet"}

        with mock.patch.object(op, "_verify", side_effect=spy_verify):
            receipt = op.action(self.root, folder, {
                "action": "recover", "request_id": "req-ro",
                "incident_id": "inc-ro", "attempt_id": "att-ro", "session_id": "sess-ro", "input": {},
            })
        self.assertEqual(receipt["status"], "completed")
        self.assertIsNone(observed["write_active"])
        self.assertFalse(observed["lease_active"])

    def test_claimed_incident_reconciles_when_stage_completed_by_pipeline(self):
        folder = _seed_batch(self.root, "claimed-reconcile-batch")
        manifest = mf.load(folder)
        mf.touch_stage(manifest, "knowledge", mf.COMPLETED, notes="done by pipeline")
        mf.save(folder, manifest)
        op._save(folder, {
            "identity": op._identity(folder),
            "incident": {
                "incident_id": "inc-cr", "stage": "knowledge", "status": "claimed",
                "mode": "repair", "eligible": False, "attempt_id": "att-cr",
                "session_id": "sess-cr", "lease_expires_at": 9999999999.0,
                "execution_process": op._process_identity(),
                "execution_first": True,
                "snapshot": op._snapshot(folder, "knowledge"),
                "detail": "knowledge failed", "attempts": [], "events": [],
            },
            "receipts": [], "failures": {},
        })
        # installed_pending_restart must NOT be reconciled before restart
        op._save_code_record(folder, {
            "job_id": "cr-inst", "path": str(self.root / "unused.json"),
            "status": "installed_pending_restart", "incident_id": "inc-cr",
        })
        with mock.patch.object(op, "_verify", return_value={"verified": True, "evidence_id": "ev-1", "stage": "knowledge"}):
            v_wait = op.view(self.root, folder)
            self.assertEqual(v_wait["incident"]["status"], "claimed")

        # After restart (restarted_pending_verify), pipeline completion reconciles claimed incident immediately
        op._save_code_record(folder, {
            "job_id": "cr-inst", "path": str(self.root / "unused.json"),
            "status": "restarted_pending_verify", "incident_id": "inc-cr",
        })
        with mock.patch.object(op, "_verify", return_value={"verified": True, "evidence_id": "ev-2", "stage": "knowledge"}):
            v_done = op.view(self.root, folder)
            self.assertEqual(v_done["incident"]["status"], "resolved")
            self.assertEqual(v_done["incident"]["resolution_source"], "pipeline_progress")
            self.assertEqual(op._code_record(folder)["status"], "verified")


if __name__ == "__main__":
    unittest.main()
