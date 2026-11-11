"""Tooltip status is evidence, never candidate existence or scheduler readiness."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import activity, substeps
from workflow.checks import CHECKS
from workflow.contracts import STAGES
from workbench import server


class SubstepsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def put(self, relative, value):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value), encoding="utf-8")
        return target

    def states(self, sid, status="pending", **record):
        manifest = {"stages": {sid: {"status": status, **record}}}
        return {r["id"]: r["status"] for r in substeps.project(manifest, self.root)[sid]}

    def test_all_stages_have_compact_named_steps(self):
        projected = substeps.project({})
        self.assertEqual(set(projected), {s.id for s in STAGES})
        for rows in projected.values():
            self.assertTrue(rows)
            self.assertEqual(len(rows), len({r["id"] for r in rows}))
            for row in rows:
                self.assertEqual(set(row), {"id", "title", "status"})
                self.assertEqual(row["status"], "pending")
        self.assertEqual(len(projected["validate"]), len(CHECKS) + 2)

    def test_pending_and_invalidated_ignore_all_old_files(self):
        self.put("07_review/validation-report.json", {"passed": True, "checks": [
            {"check": c.name, "passed": True} for c in CHECKS], "source_audit": {"passed": True},
            "upload_local_preflight": {"passed": True}})
        self.put("04_knowledge/explanations.json", {"status": "completed", "items": [{"explanation": "old"}]})
        for status, record in (("pending", {}), ("invalidated", {}), ("completed", {"rebuild_required": True})):
            with self.subTest(status=status):
                self.assertEqual(set(self.states("validate", status, **record).values()), {"pending"})
                self.assertEqual(set(self.states("explanations", status, **record).values()), {"pending"})

    def test_failed_validate_preserves_exact_individual_outcomes(self):
        self.put("07_review/validation-report.json", {"passed": False, "checks": [
            {"check": CHECKS[0].name, "passed": True},
            {"script": "scripts/06-upload/check_structure.py", "passed": False},
            {"check": "原卷逐题覆盖与材料归属 source_audit", "passed": True}],
            "skipped": [{"check": "答案 check_answers", "reason": "缺少输入"}],
            "upload_local_preflight": {"passed": False}})
        states = self.states("validate", "failed")
        self.assertEqual(states["label_audit"], "completed")
        self.assertEqual(states["check_structure"], "failed")
        self.assertEqual(states["source_audit"], "completed")
        self.assertEqual(states["check_answers"], "skipped")
        self.assertEqual(states["local_preflight"], "failed")
        self.assertEqual(states["check_tags"], "pending")
        self.assertNotIn("stage_execution", states)

    def test_unknown_parent_failure_is_only_stage_failure(self):
        states = self.states("answers", "failed")
        self.assertEqual(states.pop("stage_execution"), "failed")
        self.assertEqual(set(states.values()), {"pending"})

    def test_completed_parent_proves_accepted_fields_without_reading_candidates(self):
        for sid in ("preassemble", "types", "answers", "explanations", "knowledge", "assemble", "meta", "underlines"):
            with self.subTest(stage=sid):
                self.assertEqual(set(self.states(sid, "completed").values()), {"completed"})

    def test_nonblocking_check_failure_is_warning_and_accepted_parent_wins(self):
        self.put("07_review/validation-report.json", {"passed": False, "checks": [
            {"check": "表格 check_tables", "passed": False, "blocking": False},
            {"check": CHECKS[0].name, "passed": False}],
            "upload_local_preflight": {"passed": False}})
        states = self.states("validate", "completed")
        self.assertEqual(states["check_tables"], "warning")
        self.assertEqual(states["label_audit"], "completed")
        self.assertEqual(states["local_preflight"], "warning")
        self.assertNotIn("failed", states.values())

    def test_malformed_and_oversized_reports_are_unknown(self):
        self.put("07_review/validation-report.json", {"checks": None, "skipped": {}})
        self.assertEqual(self.states("validate", "failed")["label_audit"], "pending")
        path = self.put("07_review/validation-report.json", {"checks": [{"check": CHECKS[0].name, "passed": True}]})
        path.write_text(' ' * (4 * 1024 * 1024 + 1))
        self.assertEqual(self.states("validate", "failed")["label_audit"], "pending")

    def test_old_report_before_attempt_does_not_locate_failure(self):
        target = self.put("07_review/validation-report.json", {"checks": [{"check": CHECKS[0].name, "passed": False}]})
        os.utime(target, (1, 1))
        states = self.states("validate", "failed", started_at="2026-01-01T00:00:00Z")
        self.assertEqual(states["label_audit"], "pending")
        self.assertEqual(states["stage_execution"], "failed")

    def test_actual_optional_absence_is_skipped_not_completed(self):
        manifest = {"inputs": {"source": {"path": "paper.pdf"}}, "stages": {}}
        result = substeps.project(manifest, self.root)
        self.assertEqual({r["status"] for r in result["audio"]}, {"skipped"})
        self.assertEqual(result["underlines"][0]["status"], "skipped")
        self.assertEqual(next(r for r in result["extract"] if r["id"] == "answers")["status"], "skipped")
        # Answers/explanations still need explicit missing-content decisions.
        self.assertEqual({r["status"] for r in result["answers"]}, {"pending"})

    def test_native_extraction_completion_never_invents_ocr(self):
        states = self.states("extract", "completed")
        self.assertEqual(states["source"], "completed")
        self.assertEqual(states["ocr"], "pending")
        self.put("01_source/extraction.json", {"ocr_assist_enabled": False})
        self.assertEqual(self.states("extract", "completed")["ocr"], "skipped")
        self.put("01_source/extraction.json", {"pages": [{"method": "ocr", "text": "real"}]})
        self.assertEqual(self.states("extract", "completed")["ocr"], "completed")
        self.assertEqual(self.states("extract", "pending")["ocr"], "pending")

    def test_running_stage_without_position_only_adds_one_execution_row(self):
        states = self.states("knowledge", "running")
        self.assertEqual(states.pop("stage_execution"), "running")
        self.assertEqual(set(states.values()), {"pending"})
        manifest = {"stages": {"extract": {"status": "pending"}}}
        rows = substeps.project(manifest, self.root, {"stage": "extract", "detail": "卷头识别仍在执行"})["extract"]
        self.assertEqual([r["id"] for r in rows if r["status"] == "running"], ["header"])

    def test_live_audio_phase_only_marks_current_work(self):
        manifest = {"inputs": {"audio": {"path": "audio.mp3"}}, "stages": {"audio": {"status": "running"}}}
        for phase, current in (("transcribe", "transcription"), ("cut", "segments")):
            rows = substeps.project(manifest, self.root, audio_state={"running": True, "phase": phase})["audio"]
            self.assertEqual([r["id"] for r in rows if r["status"] == "running"], [current])
        rows = substeps.project(manifest, self.root, audio_state={"running": True, "phase": "cut", "stale": True})["audio"]
        self.assertEqual([r["id"] for r in rows if r["status"] == "running"], ["stage_execution"])

    def test_review_uses_actual_summary_rounds_and_execution_not_quality(self):
        self.put("07_review/overall-review-report.json", {"execution_completed": False, "quality_passed": False,
            "rounds": [{"round_id": "labels", "completed": True}, {"round_id": "sample", "completed": False}]})
        states = self.states("review", "needs_review")
        self.assertEqual(states, {"labels": "completed", "sample": "pending", "coverage": "pending"})
        self.put("07_review/overall-review-report.json", {"execution_completed": True, "quality_passed": False,
            "rounds": [{"round_id": r, "completed": True} for r in ("labels", "sample")]})
        self.assertEqual(set(self.states("review", "needs_review").values()), {"completed"})
        self.put("07_review/review-gate.json", {"passed": False, "manual_only": True, "rounds": {"execution_completed": True}})
        self.assertEqual(self.states("review_gate", "needs_review"), {"differences": "completed", "conclusion": "needs_review"})

    def test_format_pass_does_not_mask_source_freeze_failure_or_deferred(self):
        self.put("07_review/upload-format.json", {"passed": True, "errors": [],
            "deferred": [{"code": "MEDIA_DEPLOYMENT_REQUIRED"}]})
        states = self.states("format_gate", "failed")
        self.assertEqual(states["media"], "completed")
        self.assertEqual(states["freeze"], "pending")
        self.assertEqual(states["stage_execution"], "failed")
        self.put("07_review/upload-format.json", {"passed": False, "errors": [{"code": "ATTACHMENT_URL_INVALID"}]})
        states = self.states("format_gate", "failed")
        self.assertEqual(states["media"], "failed")
        self.assertEqual(states["fields"], "pending")

    def test_human_and_upload_stages_retain_own_actual_status(self):
        manifest = {"stages": {s.id: {"status": "completed"} for s in STAGES}}
        for sid in ("upload_gate", "upload", "publish_gate"):
            manifest["stages"][sid]["status"] = "pending"
        for sid in ("upload_gate", "upload", "publish_gate"):
            self.assertEqual(substeps.project(manifest)[sid][0]["status"], "pending")

    def test_projection_does_not_mutate_inputs_or_call_business_operations(self):
        manifest = {"stages": {"answers": {"status": "completed"}}}
        before = copy.deepcopy(manifest)
        with patch("workflow.manifest.save", side_effect=AssertionError("write")), \
             patch("workflow.local_agent.invoke", side_effect=AssertionError("model")), \
             patch("workflow.upload.local_preflight", side_effect=AssertionError("write")), \
             patch("workflow.review.evaluate_rounds", side_effect=AssertionError("heavy evaluation")):
            substeps.project(manifest, self.root)
        self.assertEqual(before, manifest)

    def test_live_overview_refreshes_report_without_mutating_cached_cards(self):
        self.put("workflow/manifest.json", {"stages": {"validate": {"status": "failed"}}})
        report = {"checks": [{"check": CHECKS[0].name, "passed": False}]}
        self.put("07_review/validation-report.json", report)
        cached = {"stage": {"items": [{"id": "validate", "substeps": [{"status": "old"}]}]}}
        original = copy.deepcopy(cached)
        with patch.object(server, "advance_state", return_value={}), patch.object(server, "upload_state", return_value={}):
            first = server._live_overview(cached, "fixture", self.root)
            report["checks"][0]["passed"] = True
            self.put("07_review/validation-report.json", report)
            second = server._live_overview(cached, "fixture", self.root)
        get = lambda value: next(c for c in value["stage"]["items"] if c["id"] == "validate")["substeps"][0]["status"]
        self.assertEqual(get(first), "failed")
        self.assertEqual(get(second), "completed")
        self.assertEqual(cached, original)

    def test_audio_bootstrap_can_observe_work_before_parent_running_commit(self):
        self.put("workflow/manifest.json", {"inputs": {"audio": {"path": "audio.mp3"}},
            "stages": {"audio": {"status": "pending"}}})
        with patch.object(server, "advance_state", return_value={}), patch.object(server, "upload_state", return_value={}), \
             patch.object(server, "_audio_has_live_worker", return_value=True), \
             patch.object(server, "audio_status", return_value={"running": True, "phase": "transcribe"}):
            value = server._live_overview({"stage": {}}, "fixture", self.root)
        card = next(c for c in value["stage"]["items"] if c["id"] == "audio")
        self.assertTrue(card["active"])
        self.assertEqual([r["id"] for r in card["substeps"] if r["status"] == "running"], ["transcription"])

    def test_active_pending_parent_projects_only_real_activity(self):
        self.put("workflow/manifest.json", {"stages": {"extract": {"status": "pending"}}})
        cached = {"stage": {"items": []}}
        with patch.object(server, "advance_state", return_value={}), patch.object(server, "upload_state", return_value={}):
            with activity.track(self.root, "extract", "卷头识别"):
                value = server._live_overview(cached, "fixture", self.root)
        extract = next(c for c in value["stage"]["items"] if c["id"] == "extract")
        self.assertTrue(extract["active"])
        self.assertEqual([s["id"] for s in extract["substeps"] if s["status"] == "running"], ["header"])


if __name__ == "__main__":
    unittest.main()
