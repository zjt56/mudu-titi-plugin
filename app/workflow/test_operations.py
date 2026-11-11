"""Operations tests use disposable workflows only; no models or remote requests."""
import concurrent.futures
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import operations as op, supervisor, auto
from workflow import operations_development as dev


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "workflows"
        self.folder = self.root / "paper"
        (self.folder / "workflow").mkdir(parents=True)
        self.manifest = {"batch_dir": str(self.folder), "stages": {"validate": {"status": "failed"}}, "inputs": {}}
        self.put("workflow/manifest.json", self.manifest)
        # self_heal signs with retry evidence (inputs/code hashes), so the exact
        # value is opaque here — operations must use the event's own signature.
        self.signature = __import__('hashlib').sha256(b"validate\nbroken\n{\"inputs\":{}}").hexdigest()[:16]
        self.put("workflow/self-heal.json", {"events": [{"at": "2026-01-01", "stage": "validate",
            "detail": "broken", "signature": self.signature, "outcome": "repair_failed", "attempt": 1}]})
        self.candidate = patch.object(op.self_heal, "_candidate", return_value=("validate", "broken")).start()
        self.snapshot = patch.object(op, "_snapshot", return_value="snapshot1").start()
        self.busy = patch.object(supervisor, "execution_busy", return_value=False).start()
        self.addCleanup(patch.stopall)

    def put(self, relative, value):
        path = self.folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def claim(self):
        incident = op.incidents(self.root)["incidents"][0]
        return op.claim(self.root, self.folder, {"incident_id": incident["incident_id"], "session_id": "session"})

    def request(self, claim, name, data=None, request="req", **kwargs):
        return op.action(self.root, self.folder, {k: claim[k] for k in ("incident_id", "session_id", "attempt_id")} |
                         {"request_id": request, "action": name, "input": data or {}}, **kwargs)

    def test_host_failure_diagnostics_persist_in_receipt_and_event(self):
        claimed = self.claim()
        result = self.request(claimed, "progress", {
            "state": "session_failed", "error": "resume failed; code=MISSING_SESSION",
            "reason": {"error": {"message": "token=private-value"}, "payload": "DO_NOT_STORE"},
            "headers": {"authorization": "DO_NOT_STORE"}, "stack": "DO_NOT_STORE",
        })
        detail = result["evidence"]["detail"]
        self.assertIn("code=MISSING_SESSION", detail)
        self.assertIn("[REDACTED]", detail)
        self.assertNotIn("private-value", detail)
        self.assertNotIn("DO_NOT_STORE", detail)
        state = op._load(self.folder)
        self.assertEqual(state["incident"]["events"][-1]["detail"], detail)
        released = self.request(claimed, "release", {"reason": "attempt incomplete"}, request="release")
        self.assertEqual(released["evidence"]["detail"], "attempt incomplete")
        self.assertEqual(op._load(self.folder)["incident"]["status"], "queued")

    def test_session_identity_recovery_preserves_audit_cooldown_and_fences_attempt(self):
        claimed = self.claim()
        recovery = {"session_id": claimed["session_id"], "reason": "missing_user_message_identity"}
        now = op.time.time()
        with patch.object(op.time, "time", return_value=now):
            released = self.request(claimed, "release", {"session_corruption": recovery})
        self.assertEqual(released["status"], "completed")
        incident = op._load(self.folder)["incident"]
        self.assertIsNone(incident["session_id"])
        self.assertEqual(incident["session_recovery"], recovery | {"at": now})
        self.assertEqual(incident["not_before"], now + 60)
        self.assertEqual(incident["queue_generation"], claimed["queue_generation"] + 1)
        self.assertIsNone(incident["attempt_id"])
        with self.assertRaisesRegex(ValueError, "stale_lease"):
            self.request(claimed, "progress", request="old-progress")
        state = op._load(self.folder)
        state["incident"]["not_before"] = 0
        op._save(self.folder, state)
        next_claim = op.claim(self.root, self.folder, {"incident_id": claimed["incident_id"], "session_id": "replacement"})
        self.assertNotEqual(next_claim["attempt_id"], claimed["attempt_id"])
        self.assertEqual(next_claim["session_id"], "replacement")
        with self.assertRaisesRegex(ValueError, "stale_lease"):
            self.request(claimed, "release", request="old-release")
        with self.assertRaisesRegex(ValueError, "session_recovery_already_used"):
            self.request(next_claim, "release", {"session_corruption": {
                "session_id": "replacement", "reason": "missing_user_message_identity"}}, request="second-recovery")

    def test_session_identity_recovery_refuses_any_unknown_receipt(self):
        claimed = self.claim()
        state = op._load(self.folder)
        state["receipts"].append({"request_id": "unrelated-old", "status": "unknown", "fingerprint": "old"})
        op._save(self.folder, state)
        with self.assertRaisesRegex(ValueError, "unknown_receipt_quarantine"):
            self.request(claimed, "release", {"session_corruption": {
                "session_id": claimed["session_id"], "reason": "missing_user_message_identity"}})
        incident = op._load(self.folder)["incident"]
        self.assertEqual(incident["session_id"], claimed["session_id"])
        self.assertEqual(incident["status"], "claimed")
        self.assertNotIn("session_recovery", incident)

    def test_session_identity_recovery_refuses_mismatched_or_invalid_report(self):
        claimed = self.claim()
        with self.assertRaisesRegex(ValueError, "session_corruption_identity_mismatch"):
            self.request(claimed, "release", {"session_corruption": {
                "session_id": "other", "reason": "missing_user_message_identity"}})
        for report in (None, {}, {"session_id": "session", "reason": "transport"}):
            with self.assertRaisesRegex(ValueError, "invalid_session_corruption"):
                self.request(claimed, "release", {"session_corruption": report})
        with self.assertRaisesRegex(ValueError, "invalid_session_corruption"):
            self.request(claimed, "progress", {"session_corruption": {
                "session_id": "session", "reason": "missing_user_message_identity"}})
        self.assertEqual(op._load(self.folder)["incident"]["status"], "claimed")

    def test_ordinary_release_never_retires_session_identity(self):
        claimed = self.claim()
        self.assertEqual(self.request(claimed, "release")["status"], "completed")
        incident = op._load(self.folder)["incident"]
        self.assertEqual(incident["session_id"], claimed["session_id"])
        self.assertNotIn("session_recovery", incident)

    def test_discovery_accepts_self_heal_evidence_signature(self):
        # Regression: self_heal's signature = sha256(stage+detail+evidence).
        # Operations previously recomputed sha256(stage+detail) and could never
        # match, so AI operations never saw any real failed repair.
        real = __import__('hashlib').sha256(b"validate\nbroken\n{\"inputs\":{\"a\":\"b\"}}").hexdigest()[:16]
        self.put("workflow/self-heal.json", {"events": [{"at": "2026-01-02", "stage": "validate",
            "detail": "broken", "signature": real, "outcome": "repair_failed", "attempt": 2}]})
        found = op.incidents(self.root)["incidents"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["signature"], real)

    def test_discovery_requires_actual_failed_heal(self):
        self.put("workflow/self-heal.json", {"events": []})
        self.assertEqual(op.incidents(self.root), {"incidents": []})

    def test_discovery_accepts_every_recorded_blocker_outcome(self):
        # 回归：self_heal 判定“卡住”的 outcome 不止 repair_failed/retry_failed。
        # retry_budget_exhausted（预算耗尽）等同样是真实卡点，运维必须能建单接手。
        for outcome in ("repair_not_applicable", "retry_budget_exhausted",
                        "gate_retry_budget_exhausted"):
            with self.subTest(outcome=outcome):
                self.put("workflow/operations.json", {"identity": None, "incident": None,
                                                      "receipts": [], "failures": {}})
                self.put("workflow/self-heal.json", {"events": [{"at": "2026-01-03",
                    "stage": "validate", "detail": "broken", "signature": self.signature,
                    "outcome": outcome, "attempt": 3}]})
                found = op.incidents(self.root)["incidents"]
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0]["signature"], self.signature)

    def test_discovery_ignores_retries_still_in_progress(self):
        for outcome in ("diagnosing", "retry_scheduled", "upstream_escalated",
                        "content_retry_with_feedback"):
            with self.subTest(outcome=outcome):
                self.put("workflow/self-heal.json", {"events": [{"at": "2026-01-03",
                    "stage": "validate", "detail": "broken", "signature": self.signature,
                    "outcome": outcome, "attempt": 4}]})
                self.assertEqual(op.incidents(self.root), {"incidents": []})

    def test_rate_limit_does_not_escalate(self):
        self.put("workflow/self-heal.json", {"events": [{"stage": "validate", "detail": "broken",
                 "signature": self.signature, "outcome": "repair_failed", "error_code": "rate_limited"}]})
        self.assertEqual(op.incidents(self.root), {"incidents": []})

    def test_normal_running_and_review_do_not_escalate(self):
        self.manifest["stages"]["validate"]["status"] = "running"
        self.put("workflow/manifest.json", self.manifest)
        self.assertEqual(op.incidents(self.root), {"incidents": []})
        self.candidate.return_value = ("review_gate", "difference")
        self.assertEqual(op.incidents(self.root), {"incidents": []})

    def test_claim_and_auto_guard_before_reconcile(self):
        self.claim()
        with patch.object(auto.mf, "reconcile_deliveries") as reconcile:
            self.assertEqual(auto._advance_locked(self.folder, {})["action"], "operations_leased")
            reconcile.assert_not_called()

    def test_lease_expiry_fences_old_attempt(self):
        old = self.claim()
        state = op._load(self.folder)
        state["incident"]["lease_expires_at"] = 1
        op._save(self.folder, state)
        new = op.claim(self.root, self.folder, {"incident_id": old["incident_id"], "session_id": "new"})
        self.assertNotEqual(old["attempt_id"], new["attempt_id"])
        self.assertGreater(new["queue_generation"], old["queue_generation"])
        with self.assertRaisesRegex(ValueError, "stale_lease"):
            self.request(old, "progress")

    def test_duplicate_receipt_never_reapplies(self):
        claim = self.claim()
        with patch("workflow.self_heal_actions.apply_source_table_repair", return_value={"installed": True}) as repair, \
             patch.object(op.self_heal, "_snapshot", return_value={"files": {}}), \
             patch("workflow.self_heal_actions._prepare_tables", return_value={"passed": True, "changes": ["table"]}):
            self.request(claim, "preview_repair", {"repair": "repair_source_tables"}, request="preview")
            first = self.request(claim, "apply_repair", {"repair": "repair_source_tables"})
            second = self.request(claim, "apply_repair", {"repair": "repair_source_tables"})
        self.assertEqual(first, second)
        self.assertEqual(repair.call_count, 1)

    def test_unknown_receipt_is_not_replayed(self):
        claim = self.claim()
        receipt = self.request(claim, "diagnose")
        state = op._load(self.folder)
        state["receipts"][0]["status"] = "unknown"
        op._save(self.folder, state)
        self.assertEqual(self.request(claim, "diagnose")["status"], "unknown")
        self.assertEqual(len(op.view(self.root, self.folder)["receipts"]), 1)

    def test_unknown_quarantine_blocks_new_mutation_and_claim(self):
        claim = self.claim()
        self.request(claim, "diagnose")
        state = op._load(self.folder)
        state["receipts"][0]["status"] = "unknown"
        op._save(self.folder, state)
        with self.assertRaisesRegex(ValueError, "unknown_receipt_quarantine"):
            self.request(claim, "apply_repair", request="new")
        self.request(claim, "release", request="release")
        state = op._load(self.folder)
        state["incident"]["not_before"] = 0
        op._save(self.folder, state)
        with self.assertRaisesRegex(ValueError, "unknown_receipt_quarantine"):
            op.claim(self.root, self.folder, {"incident_id": claim["incident_id"], "session_id": "new"})

    def test_apply_requires_real_successful_preview(self):
        claim = self.claim()
        with patch("workflow.self_heal_actions.apply_source_table_repair") as apply:
            result = self.request(claim, "apply_repair", {"repair": "repair_source_tables"})
            self.assertEqual(result["status"], "failed")
            apply.assert_not_called()
        with patch("workflow.self_heal_actions._prepare_tables", return_value={"passed": False, "refused": ["source mismatch"]}):
            result = self.request(claim, "preview_repair", {"repair": "repair_source_tables"}, request="preview")
            self.assertFalse(result["evidence"]["passed"])

    def test_escalation_reconciles_only_with_current_stage_verification(self):
        self.claim()
        manifest = op.mf.load(self.folder)
        manifest['stages']['validate']['status'] = op.mf.COMPLETED
        op.mf.save(self.folder, manifest)
        original = op._load(self.folder)
        original["incident"].update(status="needs_deep_repair", verification={"verified": True},
                                    findings=[{"summary": "retain historical finding"}])
        for passed in (False, True):
            op._save(self.folder, json.loads(json.dumps(original)))
            with patch.object(op, "_verify", return_value={"verified": passed, "stage": "validate"}):
                result = op._discover(self.folder)
            self.assertEqual(result["incident"]["status"], "resolved" if passed else "queued")
            self.assertEqual(result["incident"]["findings"], original["incident"]["findings"])
            if passed:
                self.assertEqual(result["incident"]["prior_disposition"], "needs_deep_repair")

    def test_legacy_answers_prewrite_refusal_reconciles_without_replay(self):
        claim = self.claim()
        state = op._load(self.folder)
        state["incident"]["stage"] = "answers"
        state["receipts"] = [{"request_id": "legacy", "fingerprint": "bound-original",
            "status": "unknown", "evidence": {
                "error": "Knowledge rejudgment is not applicable to this stage"}}]
        original = json.loads(json.dumps(state))
        with patch.object(op.self_heal, "_apply") as apply:
            op._reconcile_stage_refusal(state)
            apply.assert_not_called()
        receipt = state["receipts"][0]
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["evidence"], original["receipts"][0]["evidence"])
        self.assertEqual(receipt["fingerprint"], "bound-original")
        self.assertFalse(receipt["reconciliation"]["replayed"])
        state["incident"]["status"] = "queued"
        generation = state["incident"]["queue_generation"]
        op._reconcile_stage_refusal(state)
        self.assertEqual(state["incident"]["queue_generation"], generation + 1)
        self.assertEqual(receipt["reconciliation"]["queue_generation"], generation + 1)
        count = len(state["incident"]["events"])
        op._reconcile_stage_refusal(state)
        self.assertEqual(len(state["incident"]["events"]), count)
        for change in ("other_stage", "outbox", "other_error"):
            refused = json.loads(json.dumps(original))
            if change == "other_stage":
                refused["incident"]["stage"] = "validate"
            elif change == "outbox":
                refused["resume_outbox"] = {"request_id": "legacy"}
            else:
                refused["receipts"][0]["evidence"]["error"] = "interrupted after write"
            op._reconcile_stage_refusal(refused)
            self.assertEqual(refused["receipts"][0]["status"], "unknown")

    def test_inapplicable_knowledge_resume_fails_before_mutation(self):
        claim = self.claim()
        state = op._load(self.folder)
        state["incident"]["stage"] = "answers"
        op._save(self.folder, state)
        with patch.object(op.self_heal, "_apply") as apply, \
             patch.object(op.self_heal, "_snapshot", return_value={"files": {}}):
            result = self.request(claim, "resume", {
                "strategy": "rejudge_knowledge", "reason": "test stage mismatch"},
                enqueue=lambda **kwargs: None)
        self.assertEqual(result["status"], "failed")
        self.assertIn("not applicable", result["evidence"]["error"])
        apply.assert_not_called()
        self.assertEqual(self.request(claim, "diagnose", request="after")["status"], "completed")
        self.assertNotIn("resume_outbox", op._load(self.folder))

    def test_partial_mutation_exception_stays_unknown(self):
        claim = self.claim()
        with patch("workflow.self_heal_actions._prepare_tables", return_value={"passed": True}), \
             patch.object(op.self_heal, "_snapshot", return_value={"files": {}}):
            self.request(claim, "preview_repair", {"repair": "repair_source_tables"}, request="preview")
            def interrupted(*args, **kwargs):
                self.put("partial-test-marker.json", {"written": True})
                raise OSError("process interrupted after first write")
            with patch("workflow.self_heal_actions.apply_source_table_repair", side_effect=interrupted):
                result = self.request(claim, "apply_repair", {"repair": "repair_source_tables"})
        self.assertEqual(result["status"], "unknown")
        with self.assertRaisesRegex(ValueError, "unknown_receipt_quarantine"):
            self.request(claim, "apply_repair", request="second")

    def test_resume_outbox_recovers_failed_admission_without_reapplying(self):
        claim = self.claim()
        def fail(expected):
            raise OSError("queue temporarily unavailable")
        result = self.request(claim, "resume", enqueue=fail)
        self.assertEqual(result["status"], "unknown")
        self.assertIn("resume_outbox", op._load(self.folder))
        calls = []
        def admit(name, folder, **kwargs):
            calls.append(name)
            self.assertEqual(kwargs["operations_expectation"]["identity"], op._identity(folder))
            return {"queued": True}
        op.recover_resumes(self.root, admit)
        state = op._load(self.folder)
        self.assertEqual(calls, [self.folder.name])
        self.assertNotIn("resume_outbox", state)
        self.assertEqual(state["receipts"][0]["status"], "completed")

    def test_old_resume_queue_never_executes_recreated_batch(self):
        from workbench import server
        claim = self.claim()
        with patch.object(server, "WORKFLOWS", self.root.resolve()), \
             patch.object(server, "_advance_queue", []), patch.object(server, "_advance_state", {}), \
             patch.object(server, "_ensure_advance_worker"), \
             patch.object(auto, "_advance_locked") as execute:
            result = self.request(claim, "resume", enqueue=lambda expected: server._launch_advance(
                self.folder.name, self.folder, operations_expectation=expected))
            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(server._advance_queue), 1)
            self.folder.rename(self.root / "archived-paper")
            (self.folder / "workflow").mkdir(parents=True)
            self.put("workflow/manifest.json", self.manifest)
            before = (self.folder / "workflow/manifest.json").read_bytes()
            server._advance_worker_loop()
            execute.assert_not_called()
            self.assertEqual(before, (self.folder / "workflow/manifest.json").read_bytes())
            self.assertFalse((self.folder / "workflow/supervisor-result.json").exists())

    def test_resume_rejected_admission_remains_unknown(self):
        claim = self.claim()
        result = self.request(claim, "resume", enqueue=lambda expected: {"started": False})
        self.assertEqual(result["status"], "unknown")
        self.assertIn("resume_outbox", op._load(self.folder))

    def test_failed_review_execution_eligible_but_quality_difference_not(self):
        self.candidate.return_value = ("review", "broken")
        signature = __import__('hashlib').sha256(b"review\nbroken\n{}").hexdigest()[:16]
        self.put("workflow/self-heal.json", {"events": [{"at": "now", "stage": "review", "detail": "broken", "signature": signature, "outcome": "repair_failed"}]})
        self.manifest["stages"]["review"] = {"status": "needs_review", "execution_completed": True}
        self.put("workflow/manifest.json", self.manifest)
        with patch("workflow.review.evaluate_rounds", return_value={
                "execution_completed": True, "passed": False, "findings": [{"field": "knowledge"}]}):
            self.assertEqual(op.incidents(self.root)["incidents"], [])
        self.manifest["stages"]["review"]["status"] = "failed"
        self.put("workflow/manifest.json", self.manifest)
        self.assertEqual(len(op.incidents(self.root)["incidents"]), 1)

    def test_incomplete_review_needs_review_escalates_despite_cached_completion(self):
        self.candidate.return_value = ("review", "labels protocol stall")
        self.put("workflow/self-heal.json", {"events": [{"at": "now", "stage": "review",
            "detail": "labels protocol stall", "signature": "current-review-failure",
            "outcome": "retry_failed"}]})
        self.manifest["stages"]["review"] = {"status": "needs_review", "execution_completed": True}
        self.put("workflow/manifest.json", self.manifest)
        # No real round evidence exists: a stale manifest flag cannot hide an
        # internal review failure from the operations agent.
        found = op.incidents(self.root)["incidents"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["stage"], "review")
        self.assertEqual(found[0]["signature"], "current-review-failure")
        self.assertEqual(found[0]["status"], "queued")
        self.assertTrue(found[0]["eligible"])

    def test_unreadable_review_evidence_does_not_hide_failed_execution(self):
        self.candidate.return_value = ("review", "broken")
        self.put("workflow/self-heal.json", {"events": [{"at": "now", "stage": "review",
            "detail": "broken", "signature": "unreadable-review", "outcome": "repair_failed"}]})
        self.manifest["stages"]["review"] = {"status": "needs_review"}
        self.put("workflow/manifest.json", self.manifest)
        with patch("workflow.review.evaluate_rounds", side_effect=ValueError("damaged review record")):
            self.assertEqual(len(op.incidents(self.root)["incidents"]), 1)

    def test_view_returns_persisted_snapshot_when_batch_runner_holds_lock(self):
        incident = op.incidents(self.root)["incidents"][0]
        with supervisor._try_file_lock(supervisor._auto_lock_path(self.folder)) as acquired:
            self.assertTrue(acquired)
            view = op.view(self.root, self.folder)
        self.assertEqual(view["incident"]["incident_id"], incident["incident_id"])
        self.assertEqual(view["incident"]["status"], "queued")
        self.assertEqual(view["receipts"], [])

    def test_http_contract_roundtrip_without_models(self):
        from http.server import ThreadingHTTPServer
        from workbench import server
        import threading, urllib.request
        http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        worker = threading.Thread(target=http.serve_forever, daemon=True)
        worker.start()
        def call(path, payload=None):
            req = urllib.request.Request('http://127.0.0.1:%d%s' % (http.server_port, path),
                data=None if payload is None else json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=3) as response:
                return json.load(response)
        try:
            with patch.object(server, "WORKFLOWS", self.root):
                incident = call('/api/operations/incidents')["incidents"][0]
                claim = call('/api/batches/paper/operations/claim', {"incident_id": incident["incident_id"], "session_id": "http-session"})
                payload = {key: claim[key] for key in ("incident_id", "attempt_id", "session_id")}
                result = call('/api/batches/paper/operations/action', payload | {"request_id": "http-req", "action": "diagnose"})
                self.assertEqual(result["status"], "completed")
                view = call('/api/batches/paper/operations')
                self.assertEqual(view["receipts"][0]["request_id"], "http-req")
                self.assertEqual(view["incident"]["session_id"], "http-session")
        finally:
            http.shutdown()
            http.server_close()
            worker.join(timeout=3)

    def test_unrelated_global_model_lock_allows_metadata_but_blocks_apply(self):
        from workflow import local_agent
        import hashlib, subprocess, sys
        claim = self.claim()
        key = hashlib.sha256(str(Path(local_agent.__file__).resolve().parent).encode()).hexdigest()
        path = Path(tempfile.gettempdir()) / ('mudu-local-agent-' + key + '.lock')
        code = 'from pathlib import Path; import sys; from workflow.supervisor import _try_file_lock\nwith _try_file_lock(Path(sys.argv[1])) as acquired:\n assert acquired\n print("ready",flush=True)\n sys.stdin.read()'
        process = subprocess.Popen([sys.executable, '-c', code, str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            self.assertEqual(self.request(claim, 'heartbeat')["status"], 'completed')
            self.assertEqual(op.view(self.root, self.folder)["incident"]["status"], 'claimed')
            with self.assertRaisesRegex(ValueError, 'worker_busy'):
                self.request(claim, 'apply_repair', {"repair": "repair_source_tables"}, request='apply')
        finally:
            process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0)

    def test_cross_process_runner_lock_blocks_claim(self):
        import subprocess, sys
        incident = op.incidents(self.root)["incidents"][0]
        code = 'from pathlib import Path; import sys; from workflow.supervisor import _try_file_lock\nwith _try_file_lock(Path(sys.argv[1])) as acquired:\n assert acquired\n print("ready",flush=True)\n sys.stdin.read()'
        process = subprocess.Popen([sys.executable, "-c", code, str(self.folder / "workflow/.run.lock")],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), "ready")
            with self.assertRaisesRegex(ValueError, "worker_busy"):
                op.claim(self.root, self.folder, {"incident_id": incident["incident_id"], "session_id": "other"})
        finally:
            process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0)

    def test_release_backoff_hides_incident(self):
        claim = self.claim()
        self.request(claim, "release")
        self.assertEqual(op.incidents(self.root)["incidents"], [])

    def test_request_id_conflict(self):
        claim = self.claim()
        self.request(claim, "diagnose")
        with self.assertRaisesRegex(ValueError, "request_id_conflict"):
            self.request(claim, "verify")

    def test_input_change_rejects_write_allows_release(self):
        claim = self.claim()
        self.snapshot.return_value = "changed"
        with self.assertRaisesRegex(ValueError, "inputs_changed"):
            self.request(claim, "apply_repair")
        self.assertEqual(self.request(claim, "release")["status"], "completed")

    def test_real_worker_prevents_claim(self):
        incident = op.incidents(self.root)["incidents"][0]
        with supervisor._try_file_lock(self.folder / "workflow/.run.lock") as acquired:
            self.assertTrue(acquired)
            with self.assertRaisesRegex(ValueError, "worker_busy"):
                op.claim(self.root, self.folder, {"incident_id": incident["incident_id"], "session_id": "s"})

    def test_competing_claims_only_one_wins(self):
        incident = op.incidents(self.root)["incidents"][0]
        def attempt(i):
            try:
                return op.claim(self.root, self.folder, {"incident_id": incident["incident_id"], "session_id": str(i)})
            except ValueError:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(sum(r is not None for r in results), 1)

    def test_recreated_batch_rejects_old_identity(self):
        claim = self.claim()
        self.folder.rename(self.root / "archived-original")
        (self.folder / "workflow").mkdir(parents=True)
        self.put("workflow/manifest.json", self.manifest)
        with self.assertRaisesRegex(ValueError, "incident_mismatch"):
            self.request(claim, "progress")

    def test_archive_and_stopped_reject_actions(self):
        claim = self.claim()
        self.manifest["status"] = "stopped"
        self.put("workflow/manifest.json", self.manifest)
        with self.assertRaisesRegex(ValueError, "inactive_batch"):
            self.request(claim, "heartbeat")

    def test_evidence_blocks_config_traversal_and_symlink(self):
        claim = self.claim()
        for index, path in enumerate(("../secret", "config/model-router.json", "/etc/passwd", "workflow/manifest.json")):
            self.assertEqual(self.request(claim, "read_evidence", {"path": path}, request=str(index))["status"], "failed")
        source = self.folder / "01_source/正文.txt"
        source.parent.mkdir()
        source.symlink_to(self.folder / "workflow/manifest.json")
        self.assertEqual(self.request(claim, "read_evidence", {"path": "01_source/正文.txt"})["status"], "failed")

    def test_read_source_and_redact_credentials(self):
        claim = self.claim()
        source = self.folder / "01_source/正文.txt"
        source.parent.mkdir()
        source.write_text("password=secret123\nBearer abc123\nquestion")
        text = self.request(claim, "read_evidence", {"path": "01_source/正文.txt"})["evidence"]["text"]
        self.assertNotIn("secret123", text)
        self.assertNotIn("abc123", text)
        source_result = self.request(claim, "read_evidence", {"scope": "plugin", "path": "app/workflow/operations.py"}, request="src")
        self.assertEqual(source_result["status"], "completed")

    def test_finish_rejects_model_self_report(self):
        claim = self.claim()
        result = self.request(claim, "finish", {"outcome": "resolved", "summary": "done", "verified": True, "evidence_id": "fake"})
        self.assertEqual(result["status"], "failed")
        self.assertIn("verification_required", result["evidence"]["error"])

    def test_finish_reverifies(self):
        claim = self.claim()
        with patch.object(op, "_verify", return_value={"verified": True, "evidence_id": "real", "snapshot": "snapshot1"}) as verify:
            self.request(claim, "verify", request="verify")
            self.assertEqual(self.request(claim, "finish", {"outcome": "resolved", "summary": "checked"})["status"], "completed")
            self.assertEqual(verify.call_count, 2)

    def test_resume_uses_fifo_after_unlock(self):
        claim = self.claim()
        def enqueue(expected):
            with supervisor._try_file_lock(supervisor._auto_lock_path(self.folder)) as held:
                self.assertTrue(held)
            self.assertFalse(op.lease_active(self.folder))
            return {"queued": True}
        self.request(claim, "resume", enqueue=enqueue)
        self.assertEqual(op.view(self.root, self.folder)["incident"]["status"], "awaiting_execution")
        self.snapshot.return_value = "after-execution"
        self.assertEqual(op.view(self.root, self.folder)["incident"]["status"], "queued")

    def test_remote_and_arbitrary_commands_rejected(self):
        claim = self.claim()
        for name in ("upload", "publish", "shell", "write_manifest"):
            with self.assertRaisesRegex(ValueError, "unsupported_action"):
                self.request(claim, name)
        self.assertEqual(self.request(claim, "apply_repair", {"repair": "upload"})["status"], "failed")

    def test_findings_and_deep_repair_persist(self):
        claim = self.claim()
        self.request(claim, "record_finding", {"repro": "fixture", "expected": "good", "actual": "bad"}, request="finding")
        self.request(claim, "finish", {"outcome": "needs_deep_repair", "summary": "code defect"})
        incident = op.view(self.root, self.folder)["incident"]
        self.assertEqual(incident["status"], "queued")
        self.assertEqual(incident["prior_disposition"], "needs_deep_repair")
        self.assertEqual(incident["findings"][0]["actual"], "bad")
        self.assertTrue(all(set(("timestamp", "action", "outcome", "detail", "verification")) <= e.keys() for e in incident["events"]))


    def test_legacy_human_disposition_schedules_autonomous_recovery(self):
        claim = self.claim()
        receipt = self.request(claim, "finish", {"outcome": "needs_human", "summary": "旧客户端交回未修复事件"}, request="human")
        self.assertEqual(receipt["status"], "completed", receipt)
        incident = op.view(self.root, self.folder)["incident"]
        self.assertEqual(incident["status"], "queued")
        self.assertEqual(incident["prior_disposition"], "needs_human")
        self.assertEqual(incident["escalation"]["kind"], "code_repair")
        self.assertFalse(incident["recovery"]["requires_human"])
        self.assertEqual(incident["attempt_id"], None)
        self.assertEqual(receipt["evidence"]["outcome"], "recovery_queued")
        self.assertFalse(receipt["evidence"]["verified"])


class RealOperationsRepairTests(unittest.TestCase):
    def test_real_source_transaction_and_consumer_verification(self):
        # Reuse a source-bound legacy table fixture without mocking repair,
        # snapshots, source proof, handoff validation or verification.
        from workflow.test_self_heal_actions import SelfHealActionsTest
        from workflow import format_gate, manifest as mf, contracts, runner
        fixture = SelfHealActionsTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.fixture()
        fixture.add_manifest()
        folder = fixture.root
        self.assertFalse(format_gate.run(folder)["passed"])
        incident = {"incident_id": "real-incident", "batch_name": folder.name, "batch_dir": str(folder),
                    "stage": "format_gate", "detail": "actual failing format report", "status": "queued",
                    "eligible": True, "fence": 0, "queue_generation": 1, "events": []}
        op._save(folder, {"identity": op._identity(folder), "incident": incident, "receipts": [], "failures": {}})
        claim = op.claim(folder.parent, folder, {"incident_id": "real-incident", "session_id": "real-session"})
        def action(name, data=None):
            return op.action(folder.parent, folder, {k: claim[k] for k in ("incident_id", "attempt_id", "session_id")} |
                             {"request_id": name, "action": name, "input": data or {}})
        preview = action("preview_repair", {"repair": "repair_upload_format"})
        self.assertTrue(preview["evidence"]["passed"], preview)
        self.assertTrue(preview["evidence"]["changes"])
        applied = action("apply_repair", {"repair": "repair_upload_format"})
        self.assertTrue(applied["evidence"]["installed"], applied)
        self.assertTrue(format_gate.run(folder)["passed"])
        # Normal deterministic executor records the genuinely passing report;
        # operations itself never edits a manifest to manufacture success.
        action("release")
        manifest = mf.load(folder)
        self.assertTrue(runner._record_success(folder, manifest, contracts.STAGE_BY_ID["format_gate"]))
        mf.save(folder, manifest)
        state = op._load(folder)
        state["incident"]["not_before"] = 0
        op._save(folder, state)
        # Discovery consumes the same real verifier and retires the old queued
        # incident without starting another model session after pipeline success.
        resolved = op.view(folder.parent, folder)['incident']
        self.assertEqual(resolved['status'], 'resolved')
        self.assertTrue(resolved['verification']['verified'], resolved)
        with self.assertRaisesRegex(ValueError, 'incident_not_claimable'):
            op.claim(folder.parent, folder, {'incident_id': 'real-incident', 'session_id': 'real-session'})


class FastRepairOperationsTests(unittest.TestCase):
    """Failure packets, repeat escalation and isolated code repair.

    Every test uses a disposable batch copy; the real plugin checkout is never
    patched because ``_source_root`` points at a fake repository.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "workflows"
        self.folder = self.root / "paper"
        (self.folder / "workflow").mkdir(parents=True)
        self.put("workflow/manifest.json", {"batch_dir": str(self.folder),
                                            "stages": {"validate": {"status": "failed"}}, "inputs": {}})
        signature = __import__("hashlib").sha256(b"validate\nbroken\n{\"inputs\":{}}").hexdigest()[:16]
        self.put("workflow/self-heal.json", {"events": [{"at": "2026-01-01", "stage": "validate",
            "detail": "broken", "signature": signature, "outcome": "repair_failed", "attempt": 1}]})
        patch.object(op.self_heal, "_candidate", return_value=("validate", "broken")).start()
        patch.object(op, "_snapshot", return_value="snapshot1").start()
        patch.object(supervisor, "execution_busy", return_value=False).start()
        self.addCleanup(patch.stopall)
        self.source = Path(self.tmp.name) / "source"
        workflow = self.source / "app" / "workflow"
        workflow.mkdir(parents=True)
        (workflow / "__init__.py").write_text("", encoding="utf-8")
        (workflow / "fixture_mod.py").write_text("def answer():\n    return 1\n", encoding="utf-8")
        (workflow / "test_fixture.py").write_text(
            "import unittest\nfrom workflow import fixture_mod\n\n"
            "class Fixture(unittest.TestCase):\n"
            "    def test_answer(self):\n"
            "        self.assertEqual(fixture_mod.answer(), 1)\n", encoding="utf-8")
        (workflow / "operations.py").write_text("# control plane\n", encoding="utf-8")
        (self.source / "workflows" / "user").mkdir(parents=True)
        (self.source / "workflows" / "user" / "private.json").write_text("{}", encoding="utf-8")
        self.devdir = Path(self.tmp.name) / "dev"
        patch.object(op, "_source_root", return_value=self.source).start()
        patch.object(op, "_code_root", return_value=self.devdir).start()

    def put(self, relative, value):
        path = self.folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def claim(self):
        incident = op.incidents(self.root)["incidents"][0]
        return op.claim(self.root, self.folder, {"incident_id": incident["incident_id"], "session_id": "session"})

    def request(self, claim, name, data=None, request="req", **kwargs):
        return op.action(self.root, self.folder, {k: claim[k] for k in ("incident_id", "session_id", "attempt_id")} |
                         {"request_id": request, "action": name, "input": data or {}}, **kwargs)

    def test_diagnose_returns_self_contained_failure_packet(self):
        claim = self.claim()
        packet = self.request(claim, "diagnose")["evidence"]["packet"]
        self.assertEqual(packet["batch_name"], "paper")
        self.assertEqual(packet["stage"], "validate")
        self.assertEqual(packet["batch_dir"], str(self.folder))
        for key in ("failed_checks", "affected_unit_ids", "inputs", "input_files", "produced_files",
                    "completed_independent_stages", "blocked_downstream_stages", "attempts",
                    "code_fingerprint", "repairs", "resume_strategies"):
            self.assertIn(key, packet)
        self.assertEqual(packet["blocked_downstream_stages"], sorted(op._downstream("validate")))
        self.assertNotIn("validate", packet["completed_independent_stages"])
        self.assertNotIn("validate", packet["blocked_downstream_stages"])

    def test_repeated_repair_on_identical_evidence_escalates_to_code_repair(self):
        claim = self.claim()
        with patch("workflow.self_heal_actions._prepare_tables", return_value={"passed": True, "changes": ["t"]}), \
             patch("workflow.self_heal_actions.apply_source_table_repair", return_value={"installed": True}) as repair, \
             patch.object(op.self_heal, "_snapshot", return_value={"files": {}}):
            self.request(claim, "preview_repair", {"repair": "repair_source_tables"}, request="p1")
            first = self.request(claim, "apply_repair", {"repair": "repair_source_tables"}, request="a1")
            self.assertEqual(first["status"], "completed", first)
            self.request(claim, "preview_repair", {"repair": "repair_source_tables"}, request="p2")
            second = self.request(claim, "apply_repair", {"repair": "repair_source_tables"}, request="a2")
        self.assertEqual(repair.call_count, 1)
        self.assertEqual(second["status"], "failed", second)
        self.assertIn("repeat_without_new_evidence", second["evidence"]["error"])
        incident = op._load(self.folder)["incident"]
        self.assertEqual(incident["escalation"]["kind"], "code_repair")
        self.assertEqual(incident["escalation"]["attempts"], 1)
        self.assertEqual([row["outcome"] for row in incident["attempts"]], ["applied"])

    def test_resume_strategy_repeat_requires_changed_code_or_evidence(self):
        claim = self.claim()
        def enqueue(expected):
            return {"queued": True}
        with patch("workflow.self_heal._apply", return_value={"passed": True}) as apply:
            first = self.request(claim, "resume", {"strategy": "retry_stage_with_fresh_context", "reason": "重新执行"},
                                 request="r1", enqueue=enqueue)
            self.assertEqual(first["status"], "completed", first)
            state = op._load(self.folder)
            state["incident"].update(status="queued", not_before=0)
            op._save(self.folder, state)
            claim = self.claim()
            second = self.request(claim, "resume", {"strategy": "retry_stage_with_fresh_context", "reason": "再试一次"},
                                  request="r2", enqueue=enqueue)
            self.assertEqual(second["status"], "failed", second)
            self.assertIn("repeat_without_new_evidence", second["evidence"]["error"])
            self.assertEqual(apply.call_count, 1)
            with patch.object(op, "_code_fingerprint", return_value="changed-code"):
                third = self.request(claim, "resume", {"strategy": "retry_stage_with_fresh_context", "reason": "代码已修复"},
                                     request="r3", enqueue=enqueue)
        self.assertEqual(third["status"], "completed", third)
        self.assertEqual(apply.call_count, 2)

    def test_code_repair_lifecycle_is_isolated_and_install_is_gated(self):
        claim = self.claim()
        started = self.request(claim, "request_code_repair", {"summary": "fixture defect"}, request="start")
        self.assertEqual(started["status"], "completed", started)
        evidence = started["evidence"]
        self.assertTrue(Path(evidence["workspace"]).is_dir())
        self.assertIn("app/workflow/operations.py", evidence["locked"])
        self.assertFalse((Path(evidence["workspace"]) / "workflows").exists())
        with self.assertRaisesRegex(ValueError, "source_not_allowlisted"):
            op._code_repair(self.folder, {"incident_id": claim["incident_id"], "stage": "validate"},
                            {"operation": "read", "path": "app/workflow/operations.py"})
        written = self.request(claim, "code_repair", {
            "operation": "write", "path": "app/workflow/test_new.py",
            "content": "import unittest\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n"}, request="write")
        self.assertEqual(written["status"], "completed", written)
        tested = self.request(claim, "code_repair", {"operation": "test",
            "tests": ["workflow.test_fixture", "workflow.test_new"]}, request="test")
        self.assertTrue(tested["evidence"]["passed"], tested)
        prepared = self.request(claim, "code_repair", {"operation": "prepare"}, request="prepare")
        self.assertEqual(prepared["status"], "completed", prepared)
        self.assertEqual([change["path"] for change in prepared["evidence"]["changes"]],
                         ["app/workflow/test_new.py"])
        refused = self.request(claim, "install_code_repair", {"confirm": True}, request="install-off")
        self.assertEqual(refused["status"], "failed", refused)
        self.assertIn("install_disabled", refused["evidence"]["error"])
        self.assertFalse((self.source / "app/workflow/test_new.py").exists())
        with patch.dict(os.environ, {"MD_OPERATIONS_INSTALL": "1"}):
            installed = self.request(claim, "install_code_repair", {"confirm": True}, request="install-on")
        self.assertEqual(installed["status"], "completed", installed)
        self.assertTrue(installed["evidence"]["restart_required"])
        self.assertTrue((self.source / "app/workflow/test_new.py").is_file())
        self.assertEqual(op._code_record(self.folder)["status"], "installed_pending_restart")
        self.assertEqual(op._load(self.folder)["incident"]["code_repair"]["status"], "installed_pending_restart")
        release = json.loads((self.source / dev.RELEASE_MARKER).read_text(encoding="utf-8"))
        self.assertEqual(release["state"], "installed")
        self.assertEqual(release["files"], ["app/workflow/test_new.py"])
        self.assertTrue(release["plan_id"])

    def test_code_repair_refuses_install_without_confirmation(self):
        claim = self.claim()
        self.request(claim, "request_code_repair", {}, request="start")
        with patch.dict(os.environ, {"MD_OPERATIONS_INSTALL": "1"}):
            refused = self.request(claim, "install_code_repair", {}, request="install")
        self.assertEqual(refused["status"], "failed", refused)
        self.assertIn("install_confirmation_required", refused["evidence"]["error"])

    def test_regenerate_units_scopes_feedback_and_is_idempotent(self):
        from workflow import agent_tasks
        units = [{"unit_id": "u1"}, {"unit_id": "u2"}]
        with patch.object(agent_tasks, "confirmed_units", return_value=units):
            claim = self.claim()
            first = self.request(claim, "regenerate_units", {
                "target_stage": "answers", "unit_ids": ["u1"], "reason": "答案与原文不符"}, request="regen")
            self.assertEqual(first["status"], "completed", first)
            self.assertEqual(first["evidence"]["unit_ids"], ["u1"])
            feedback = json.loads((self.folder / first["evidence"]["feedback_path"]).read_text(encoding="utf-8"))
            self.assertEqual(feedback["unit_ids"], ["u1"])
            self.assertTrue(feedback["scoped_units"])
            self.assertFalse(feedback["all_units"])
            self.assertEqual(op.mf.load(self.folder)["stages"]["answers"]["status"], op.mf.PENDING)
            again = self.request(claim, "regenerate_units", {
                "target_stage": "answers", "unit_ids": ["u1"], "reason": "答案与原文不符"}, request="regen2")
            self.assertEqual(again["evidence"]["status"], "already_scheduled", again)
            self.assertEqual(len([row for row in op._load(self.folder)["incident"]["attempts"]
                                  if row["kind"] == "regenerate"]), 1)

    def test_regenerate_units_refuses_review_differences_as_blocker(self):
        # 审核差异是产出，不是内容故障；不允许用局部重生成把差异“改没”。
        incident = {"stage": "review", "detail": "REVIEW_ROUND_FINDING q-1", "attempts": []}
        with self.assertRaisesRegex(ValueError, "review_differences_are_output_not_blocker"):
            op._regenerate_units(self.folder, incident, {
                "target_stage": "knowledge", "unit_ids": ["q-1"]})
        self.assertFalse((self.folder / "workflow/repair-feedback/knowledge.json").exists())

    def test_regenerate_units_rejects_unknown_and_non_content_scope(self):
        from workflow import agent_tasks
        with patch.object(agent_tasks, "confirmed_units", return_value=[{"unit_id": "u1"}]):
            claim = self.claim()
            unknown = self.request(claim, "regenerate_units", {
                "target_stage": "answers", "unit_ids": ["u9"]}, request="unknown")
            self.assertEqual(unknown["status"], "failed", unknown)
            self.assertIn("regenerate_units_unknown_units", unknown["evidence"]["error"])
            boundary = self.request(claim, "regenerate_units", {
                "target_stage": "upload", "unit_ids": ["u1"]}, request="boundary")
            self.assertEqual(boundary["status"], "failed", boundary)
            self.assertIn("regenerate_units_refuses_boundary", boundary["evidence"]["error"])
            other = self.request(claim, "regenerate_units", {
                "target_stage": "assemble", "unit_ids": ["u1"]}, request="other")
            self.assertEqual(other["status"], "failed", other)
            self.assertIn("regenerate_units_stage_unsupported", other["evidence"]["error"])

    def test_cross_round_budget_caps_repeats(self):
        def rows(count, kind, outcome):
            return [{"kind": kind, "name": "n%d" % index, "evidence": "e%d" % index,
                     "outcome": outcome} for index in range(count)]
        incident = {"attempts": rows(op.BUDGET_ATTEMPTS, "repair", "failed")}
        with self.assertRaisesRegex(ValueError, "attempt_budget_exhausted"):
            op._check_budget(incident, "repair")
        self.assertEqual(incident["escalation"]["kind"], "code_repair")
        incident = {"attempts": rows(op.BUDGET_INSTALLS, "install", "installed")}
        with self.assertRaisesRegex(ValueError, "install_budget_exhausted"):
            op._check_budget(incident, "install")
        # A different kind is not blocked by the install cap, but the total cap
        # still applies once attempts are exhausted.
        self.assertIsNone(op._check_budget(incident, "repair"))
        incident = {"attempts": rows(op.BUDGET_REGENERATIONS, "regenerate", "scheduled")}
        with self.assertRaisesRegex(ValueError, "regenerate_budget_exhausted"):
            op._check_budget(incident, "regenerate")
        # Refusals do not count toward the total: they changed nothing.
        incident = {"attempts": rows(op.BUDGET_ATTEMPTS, "repair", "refused")}
        self.assertIsNone(op._check_budget(incident, "repair"))

    def test_packet_reports_budget(self):
        claim = self.claim()
        packet = self.request(claim, "diagnose", {}, request="packet")["evidence"]["packet"]
        self.assertEqual(packet["budgets"]["attempts"], 0)
        self.assertEqual(packet["budgets"]["installs"], 0)
        self.assertEqual(packet["budgets"]["installs_limit"], op.BUDGET_INSTALLS)

    def test_unresolved_finish_preserves_installed_repair_until_restart_verification(self):
        claim = self.claim()
        self.request(claim, "request_code_repair", {"summary": "程序缺陷"}, request="repair")
        record = op._code_record(self.folder)
        record["status"] = "installed_pending_restart"
        op._save_code_record(self.folder, record)
        receipt = self.request(claim, "finish", {"outcome": "needs_human", "summary": "需人工决定"},
                               request="human")
        self.assertEqual(receipt["status"], "completed", receipt)
        record = op._code_record(self.folder)
        self.assertEqual(record["status"], "installed_pending_restart")
        self.assertFalse(record.get("settled_at"))
        incident = op.view(self.root, self.folder)["incident"]
        self.assertEqual(incident["code_repair"]["status"], "workspace_ready")
        self.assertEqual(incident["recovery"]["next_actions"], ["restart_backend_then_claim_verify"])

    def test_restart_reclaim_failed_verify_creates_fresh_second_repair(self):
        claim = self.claim()
        self.request(claim, "request_code_repair", request="start")
        self.request(claim, "code_repair", {"operation": "write", "path": "app/workflow/test_new.py",
                     "content": "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n"}, request="write")
        tested = self.request(claim, "code_repair", {"operation": "test",
                              "tests": ["workflow.test_new"]}, request="tests")
        self.assertTrue(tested["evidence"]["passed"], tested)
        self.request(claim, "code_repair", {"operation": "prepare"}, request="prepare")
        with patch.dict(os.environ, {"MD_OPERATIONS_INSTALL": "1"}):
            installed = self.request(claim, "install_code_repair", {"confirm": True}, request="install")
        self.assertEqual(installed["status"], "completed", installed)
        first = op._code_record(self.folder)
        self.assertEqual(first["install_process"], op._process_identity())
        blocked = self.request(claim, "verify", request="verify-before-restart")
        self.assertIn("installed_pending_restart", blocked["evidence"]["error"])
        self.request(claim, "release", request="release")
        state = op._load(self.folder)
        state["incident"]["not_before"] = 0
        op._save(self.folder, state)
        same_process = self.claim()
        self.assertEqual(op._code_record(self.folder)["status"], "installed_pending_restart")
        with patch.object(op, "_process_identity", return_value={"pid": 99999, "generation": "new-interpreter"}):
            # The old process lease cannot authorize actions in a new process.
            with self.assertRaisesRegex(ValueError, "stale_lease"):
                self.request(same_process, "verify", request="stale-after-restart")
            restarted = op.claim(self.root, self.folder, {
                "incident_id": claim["incident_id"], "session_id": "session"})
            self.assertNotEqual(restarted["attempt_id"], same_process["attempt_id"])
            self.assertEqual(op._code_record(self.folder)["status"], "restarted_pending_verify")
            reuse = self.request(restarted, "request_code_repair", request="before-verify")
            self.assertTrue(reuse["evidence"]["reused"])
            with patch.object(op, "_verify", return_value={"verified": False, "stage": "validate",
                                                         "snapshot": "snapshot1", "evidence_id": "real-failure"}):
                verified = self.request(restarted, "verify", request="failed-verify")
            self.assertFalse(verified["evidence"]["verified"])
            self.assertEqual(op._code_record(self.folder)["status"], "restarted_pending_verify")
            # A pre-run check cannot reject newly installed code. Only actual
            # execution followed by the original consumer check can settle it.
            state = op._load(self.folder)
            state["incident"]["status"] = "awaiting_execution"
            op._save(self.folder, state)
            with patch.object(op, "_verify", return_value={"verified": False, "stage": "validate",
                                                         "snapshot": "snapshot1", "evidence_id": "real-failure"}):
                op.observe_execution(self.folder)
            self.assertEqual(op._code_record(self.folder)["status"], "failed")
            state = op._load(self.folder)
            state["incident"]["not_before"] = 0
            op._save(self.folder, state)
            restarted = op.claim(self.root, self.folder, {
                "incident_id": claim["incident_id"], "session_id": "session"})
            fresh = self.request(restarted, "request_code_repair", request="second-repair")
            self.assertEqual(fresh["status"], "completed", fresh)
            second = op._code_record(self.folder)
            self.assertNotEqual(second["job_id"], first["job_id"])
            self.assertEqual(second["history"][0]["job_id"], first["job_id"])
            self.assertEqual(second["history"][0]["status"], "failed")
            current = dev.load(second["path"])
            self.assertIn("app/workflow/test_new.py", current["base"]["files"])
            self.assertEqual(dev.diff(current)["files"], [])
            self.assertEqual(op._budgets(op._load(self.folder)["incident"])["installs"], 1)
            self.assertTrue(Path(first["path"]).is_file())
            second_write = self.request(restarted, "code_repair", {"operation": "write",
                "path": "app/workflow/fixture_mod.py", "content": "def answer():\n    return 1  # second fix\n",
                "expected_sha": current["base"]["files"]["app/workflow/fixture_mod.py"]["sha256"]}, request="second-write")
            self.assertEqual(second_write["status"], "completed", second_write)
            second_test = self.request(restarted, "code_repair", {"operation": "test",
                "tests": ["workflow.test_fixture"]}, request="second-test")
            self.assertTrue(second_test["evidence"]["passed"], second_test)
            second_plan = self.request(restarted, "code_repair", {"operation": "prepare"}, request="second-plan")
            self.assertEqual(second_plan["status"], "completed", second_plan)
            with patch.dict(os.environ, {"MD_OPERATIONS_INSTALL": "1"}):
                second_install = self.request(restarted, "install_code_repair", {"confirm": True}, request="second-install")
            self.assertEqual(second_install["status"], "completed", second_install)
            self.assertEqual(op._budgets(op._load(self.folder)["incident"])["installs"], 2)
            self.assertIn("second fix", (self.source / "app/workflow/fixture_mod.py").read_text())

    def test_process_identity_is_stable_across_calls_and_distinct_in_child(self):
        import subprocess, sys
        identity = op._process_identity()
        self.assertEqual(identity, op._process_identity())
        child = subprocess.run([sys.executable, "-c",
            "import json; from workflow.operations import _process_identity; print(json.dumps(_process_identity()))"],
            capture_output=True, text=True, check=True)
        self.assertNotEqual(identity, json.loads(child.stdout))

    def test_regeneration_failed_execution_reclaim_escalates_same_evidence(self):
        from workflow import agent_tasks
        with patch.object(agent_tasks, "confirmed_units", return_value=[{"unit_id": "u1"}]):
            claim = self.claim()
            data = {"target_stage": "answers", "unit_ids": ["u1"]}
            self.assertEqual(self.request(claim, "regenerate_units", data, request="regen")["status"], "completed")
            self.request(claim, "resume", request="resume", enqueue=lambda expected: {"queued": True})
            manifest = op.mf.load(self.folder)
            manifest["stages"]["answers"]["status"] = op.mf.FAILED
            self.put("workflow/manifest.json", manifest)
            op.observe_execution(self.folder)
            state = op._load(self.folder)
            self.assertEqual(state["incident"]["attempts"][0]["outcome"], "failed")
            self.assertEqual(op._budgets(state["incident"])["regenerations"], 1)
            state["incident"]["not_before"] = 0
            op._save(self.folder, state)
            claim = self.claim()
            failed = self.request(claim, "regenerate_units", data, request="again")
            self.assertEqual(failed["status"], "failed", failed)
            self.assertIn("repeat_without_new_evidence", failed["evidence"]["error"])
            self.assertEqual(op._load(self.folder)["incident"]["escalation"]["kind"], "code_repair")

    def test_regeneration_pending_stays_scheduled_success_settles_once(self):
        from workflow import agent_tasks
        with patch.object(agent_tasks, "confirmed_units", return_value=[{"unit_id": "u1"}]):
            claim = self.claim()
            self.request(claim, "regenerate_units", {"target_stage": "answers", "unit_ids": ["u1"]})
            op.observe_execution(self.folder)
            self.assertEqual(op._load(self.folder)["incident"]["attempts"][0]["outcome"], "scheduled")
            manifest = op.mf.load(self.folder)
            manifest["stages"]["answers"]["status"] = op.mf.COMPLETED
            self.put("workflow/manifest.json", manifest)
            with patch.object(op, "_verify", return_value={"verified": True, "evidence_id": "consumer-proof"}) as verify:
                op.observe_execution(self.folder)
                op.observe_execution(self.folder)
                self.assertEqual(verify.call_count, 1)
            row = op._load(self.folder)["incident"]["attempts"][0]
            self.assertEqual(row["outcome"], "succeeded")
            self.assertEqual(row["verification"]["evidence_id"], "consumer-proof")

    def test_partial_regeneration_feedback_or_requeue_failure_stays_unknown(self):
        from workflow import agent_tasks
        for failing_function in ("_feedback", "_requeue"):
            with self.subTest(failing_function=failing_function):
                # Independent incident; an unknown request must remain quarantined.
                self.put("workflow/operations.json", {})
                with patch.object(agent_tasks, "confirmed_units", return_value=[{"unit_id": "u1"}]), \
                     patch.object(op.self_heal, failing_function, side_effect=OSError("interrupted after write")):
                    claim = self.claim()
                    result = self.request(claim, "regenerate_units", {"target_stage": "answers", "unit_ids": ["u1"]})
                    self.assertEqual(result["status"], "unknown", result)
                    self.assertEqual(self.request(claim, "regenerate_units", {
                        "target_stage": "answers", "unit_ids": ["u1"]}), result)
                    with self.assertRaisesRegex(ValueError, "unknown_receipt_quarantine"):
                        self.request(claim, "regenerate_units", request="another")


if __name__ == "__main__":
    unittest.main()
