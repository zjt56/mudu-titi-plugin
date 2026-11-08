"""Operations tests use disposable workflows only; no models or remote requests."""
import concurrent.futures
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import operations as op, supervisor, auto


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "workflows"
        self.folder = self.root / "paper"
        (self.folder / "workflow").mkdir(parents=True)
        self.manifest = {"batch_dir": str(self.folder), "stages": {"validate": {"status": "failed"}}, "inputs": {}}
        self.put("workflow/manifest.json", self.manifest)
        self.signature = __import__('hashlib').sha256(b"validate\nbroken").hexdigest()[:16]
        self.put("workflow/self-heal.json", {"events": [{"at": "2026-01-01", "stage": "validate",
            "signature": self.signature, "outcome": "repair_failed", "attempt": 1}]})
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

    def test_discovery_requires_actual_failed_heal(self):
        self.put("workflow/self-heal.json", {"events": []})
        self.assertEqual(op.incidents(self.root), {"incidents": []})

    def test_rate_limit_does_not_escalate(self):
        self.put("workflow/self-heal.json", {"events": [{"stage": "validate", "signature": self.signature,
                 "outcome": "repair_failed", "error_code": "rate_limited"}]})
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
        signature = __import__('hashlib').sha256(b"review\nbroken").hexdigest()[:16]
        self.put("workflow/self-heal.json", {"events": [{"at": "now", "stage": "review", "signature": signature, "outcome": "repair_failed"}]})
        self.manifest["stages"]["review"] = {"status": "needs_review", "execution_completed": True}
        self.put("workflow/manifest.json", self.manifest)
        self.assertEqual(op.incidents(self.root)["incidents"], [])
        self.manifest["stages"]["review"]["status"] = "failed"
        self.put("workflow/manifest.json", self.manifest)
        self.assertEqual(len(op.incidents(self.root)["incidents"]), 1)

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
        code = 'import fcntl,sys; f=open(sys.argv[1],"a+b"); fcntl.flock(f,fcntl.LOCK_EX); print("ready",flush=True); sys.stdin.read()'
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
        code = 'import fcntl,sys; f=open(sys.argv[1],"a+b"); fcntl.flock(f,fcntl.LOCK_EX); print("ready",flush=True); sys.stdin.read()'
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
        self.assertEqual(incident["status"], "needs_deep_repair")
        self.assertEqual(incident["findings"][0]["actual"], "bad")
        self.assertTrue(all(set(("timestamp", "action", "outcome", "detail", "verification")) <= e.keys() for e in incident["events"]))


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
        claim = op.claim(folder.parent, folder, {"incident_id": "real-incident", "session_id": "real-session"})
        verified = action("verify")
        self.assertTrue(verified["evidence"]["verified"], verified)
        finished = action("finish", {"outcome": "resolved", "summary": "source-proven transaction and consumer recheck"})
        self.assertTrue(finished["evidence"]["verified"], finished)


if __name__ == "__main__":
    unittest.main()
