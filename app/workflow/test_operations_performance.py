"""Performance metadata observations must never become fault/repair authority."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from workflow import execution_state, operations_performance as performance


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "batch"
        (self.root / "workflow").mkdir(parents=True)
        self.base = datetime(2026, 11, 12, tzinfo=timezone.utc)
        self.state = {
            "workflow_id": "test-batch", "status": "pending_confirmation",
            "inputs": {"source": {"sha256": "source-hash", "path": "/private/source.docx"}},
            "stages": {sid: {"status": "completed", "started_at": self.at(0),
                             "finished_at": self.at(1), "input_hashes": {"source": "hash"}}
                       for sid in execution_state.LOCAL_STAGES}}
        self.state["stages"]["knowledge"].update(started_at=self.at(100), finished_at=self.at(300))
        self.state["stages"]["format_gate"].update(started_at=self.at(799), finished_at=self.at(800))
        self.save()

    def at(self, seconds):
        return (self.base + timedelta(seconds=seconds)).isoformat()

    def save(self):
        (self.root / "workflow/manifest.json").write_text(json.dumps(self.state), encoding="utf-8")

    def test_detects_fixed_threshold_after_completed_local_execution(self):
        report = performance.collect(self.root)
        self.assertTrue(report["detected"])
        self.assertEqual(report["status"], "detected")
        self.assertEqual(report["kind"], "performance")
        self.assertEqual(report["scope"], "read_only")
        self.assertEqual(report["evidence"]["metrics"]["total_seconds"], 800)
        self.assertEqual(report["evidence"]["metrics"]["model_stage_seconds"], 200)
        self.assertIn("not_historical_anomaly", report["evidence"]["thresholds"]["basis"])
        self.assertEqual(report["investigation_status"], "not_started")
        self.assertFalse(report["investigation_started"])
        self.assertFalse(report["promote_allowed"])
        self.assertEqual(report["allowed_actions"], ["read_evidence"])
        self.assertFalse((self.root / performance.REPORT_PATH).exists())

    def test_running_queued_failed_blocked_pending_never_become_timeout(self):
        for status in ("running", "queued", "failed", "blocked", "pending", "invalidated"):
            with self.subTest(status=status):
                state = copy.deepcopy(self.state)
                state["stages"]["knowledge"].update(status=status, started_at=self.at(-100000))
                self.assertEqual(performance.observe(self.root, state)["reason"], "local_execution_incomplete")
        self.assertFalse((self.root / performance.REPORT_PATH).exists())

    def test_review_differences_are_completed_output_and_not_fault(self):
        self.state["stages"]["review_gate"].update(status="needs_review", execution_completed=True,
                                                      continue_unqualified=True, release_ready=False,
                                                      finished_at=None, started_at=None)
        report = performance.collect(self.root, self.state)
        self.assertTrue(report["detected"])
        self.assertFalse(report["promote_allowed"])
        self.assertNotIn("failure_class", report)
        self.assertEqual(self.state["stages"]["review_gate"]["status"], "needs_review")

    def test_incomplete_review_and_rebuild_do_not_trigger(self):
        self.state["stages"]["review_gate"].update(status="needs_review", execution_completed=False)
        self.assertFalse(performance.collect(self.root, self.state)["detected"])
        self.state["stages"]["review_gate"].update(status="completed", rebuild_required=True)
        self.assertFalse(performance.collect(self.root, self.state)["detected"])

    def test_remote_upload_pending_is_ignored_and_never_modified(self):
        self.state["stages"]["upload"] = {"status": "pending"}
        before = copy.deepcopy(self.state)
        self.assertTrue(performance.collect(self.root, self.state)["detected"])
        self.assertEqual(self.state, before)

    def test_both_thresholds_are_required_and_inclusive(self):
        self.state["stages"]["format_gate"].update(started_at=self.at(599), finished_at=self.at(600))
        self.state["stages"]["knowledge"].update(started_at=self.at(100), finished_at=self.at(280))
        self.assertTrue(performance.collect(self.root, self.state)["detected"])
        self.state["stages"]["knowledge"]["finished_at"] = self.at(279)
        self.assertFalse(performance.collect(self.root, self.state)["detected"])
        self.state["stages"]["knowledge"]["finished_at"] = self.at(280)
        self.state["stages"]["format_gate"]["finished_at"] = self.at(599)
        self.assertFalse(performance.collect(self.root, self.state)["detected"])

    def test_threshold_rounding_does_not_admit_subthreshold_durations(self):
        self.state["stages"]["knowledge"].update(started_at=self.at(100), finished_at=self.at(279.9999))
        self.assertFalse(performance.collect(self.root, self.state)["detected"])
        self.state["stages"]["knowledge"]["finished_at"] = self.at(280)
        self.state["stages"]["format_gate"].update(started_at=self.at(599), finished_at=self.at(599.9999))
        self.assertFalse(performance.collect(self.root, self.state)["detected"])

    def test_skipped_or_deterministic_long_stage_is_not_model_stage(self):
        self.state["stages"]["knowledge"].update(status="skipped")
        self.state["stages"]["assemble"].update(started_at=self.at(0), finished_at=self.at(700))
        self.assertFalse(performance.collect(self.root, self.state)["detected"])

    def test_signature_ignores_order_updated_notes_and_source_path(self):
        first = performance.collect(self.root, self.state)
        changed = copy.deepcopy(self.state)
        changed.update(updated_at="tomorrow", notes="not evidence")
        changed["inputs"]["source"]["path"] = "/moved/source.docx"
        changed["stages"]["knowledge"].update(notes="queue/rate limit/review differences")
        changed["stages"] = dict(reversed(list(changed["stages"].items())))
        second = performance.collect(self.root, changed)
        self.assertEqual(first["signature"], second["signature"])
        changed["inputs"]["source"]["sha256"] = "new"
        self.assertNotEqual(first["signature"], performance.collect(self.root, changed)["signature"])
        changed = copy.deepcopy(self.state)
        changed["stages"]["knowledge"]["finished_at"] = self.at(301)
        self.assertNotEqual(first["signature"], performance.collect(self.root, changed)["signature"])

    def test_observe_atomic_and_duplicate_does_not_touch_any_input(self):
        fault = self.root / "workflow/operations.json"
        fault.write_text('{"incident":"fault-must-survive"}')
        before = {p.relative_to(self.root): (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in self.root.rglob("*") if p.is_file()}
        first = performance.observe(self.root)
        report_path = self.root / performance.REPORT_PATH
        self.assertTrue(first["persisted"])
        stamp = report_path.stat().st_mtime_ns
        second = performance.observe(self.root)
        self.assertEqual(first, second)
        self.assertEqual(report_path.stat().st_mtime_ns, stamp)
        for rel, value in before.items():
            p = self.root / rel
            self.assertEqual((p.read_bytes(), p.stat().st_mtime_ns), value)
        self.assertEqual([p.name for p in report_path.parent.glob("*.tmp")], [])
        self.assertEqual(json.loads(report_path.read_text()), first)

    def test_metadata_allowlist_does_not_read_credentials_prompts_or_body(self):
        for name in ("01_source/正文.txt", "workflow/agent-runs/knowledge-1/input.txt",
                     "workflow/agent-runs/knowledge-1/attempt.json", ".credentials.json"):
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("SECRET_CANARY_WHOLE_PAPER")
        self.state["meta"] = {"body": "SECRET_CANARY_WHOLE_PAPER", "api_key": "credential"}
        self.save()
        opened = []
        original = Path.open
        def track(path, *args, **kwargs):
            opened.append(path.relative_to(self.root).as_posix())
            return original(path, *args, **kwargs)
        with patch.object(Path, "open", track):
            report = performance.collect(self.root)
        self.assertEqual(opened, ["workflow/manifest.json"])
        text = json.dumps(report)
        self.assertNotIn("SECRET_CANARY", text)
        self.assertNotIn("credential", text)
        self.assertNotIn("/private/source.docx", text)

    def test_missing_invalid_naive_reverse_timing_is_not_invented(self):
        for value in (None, "bad", "2026-11-12T00:00:00", self.at(400)):
            with self.subTest(value=value):
                state = copy.deepcopy(self.state)
                state["stages"]["knowledge"]["started_at"] = value
                self.assertFalse(performance.collect(self.root, state)["detected"])

    def test_read_or_atomic_write_failure_preserves_existing_files(self):
        target = self.root / performance.REPORT_PATH
        with patch.object(performance.os, "replace", side_effect=OSError("disk full")):
            result = performance.observe(self.root)
        self.assertFalse(result["persisted"])
        self.assertFalse(target.exists())
        self.assertFalse(list(target.parent.glob(".operations-performance.*.tmp")))
        target.write_text("damaged")
        result = performance.observe(self.root)
        self.assertFalse(result["persisted"])
        self.assertEqual(target.read_text(), "damaged")

    def test_symlink_report_or_manifest_is_refused(self):
        outside = Path(self.temp.name) / "outside.json"
        outside.write_text('{"untouched":true}')
        report = self.root / performance.REPORT_PATH
        report.symlink_to(outside)
        self.assertFalse(performance.observe(self.root)["persisted"])
        self.assertEqual(outside.read_text(), '{"untouched":true}')
        manifest = self.root / "workflow/manifest.json"
        manifest.unlink()
        manifest.symlink_to(outside)
        self.assertFalse(performance.collect(self.root)["detected"])

    def test_no_ghost_batch_created(self):
        root = self.root / "missing"
        self.assertFalse(performance.observe(root)["detected"])
        self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
