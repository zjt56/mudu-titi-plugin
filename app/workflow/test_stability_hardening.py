# -*- coding: utf-8 -*-
"""稳定性加固回归测试：运行快照与安装互斥、preassemble 只读诊断、Review 证据局部补审、
分片级进展监控、Underlines 候选排序诊断与六维批次健康统计。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest

from workflow import (
    batch_metrics,
    local_agent,
    manifest as mf,
    operations as op,
    operations_development as dev,
    preassemble_diagnostics,
    review_progress,
    run_snapshot,
    supervisor,
)

ROOT = Path(__file__).resolve().parents[1]


def _load_mark_underline():
    spec = importlib.util.spec_from_file_location(
        "mark_underline_mod", ROOT / "scripts" / "06-upload" / "mark_underline.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestRunSnapshotAndInstallMutex(unittest.TestCase):
    def test_classify_input_change_distinguishes_four_kinds(self):
        self.assertEqual(run_snapshot.classify_input_change("01_source/正文.txt"), "source_material")
        self.assertEqual(run_snapshot.classify_input_change("app/references/sync-practice-rules.json"), "rules")
        self.assertEqual(run_snapshot.classify_input_change("rules:sync-practice-rules.json"), "rules")
        self.assertEqual(run_snapshot.classify_input_change("app/workflow/runner.py"), "code_version")
        self.assertEqual(run_snapshot.classify_input_change("06_batch/batch_meta.json"), "runtime_config")

    def test_capture_and_diff_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            (batch / "01_source").mkdir()
            (batch / "01_source/正文.txt").write_text("1. Hello\n", encoding="utf-8")
            snap1 = run_snapshot.capture(batch)
            self.assertTrue((batch / run_snapshot.SNAPSHOT_REL).is_file())
            (batch / "01_source/正文.txt").write_text("1. Hello world\n", encoding="utf-8")
            snap2 = run_snapshot.capture(batch)
            diffs = run_snapshot.diff_snapshots(snap1, snap2)
            self.assertTrue(any(d["change_kind"] == "source_material" for d in diffs))

    def test_local_agent_check_input_hashes_includes_change_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            (batch / "01_source").mkdir()
            (batch / "01_source/正文.txt").write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(local_agent.AgentError, r"change_kind=source_material"):
                local_agent._check_input_hashes(batch, {"01_source/正文.txt": "0" * 64})

    def test_install_refuses_when_active_batch_holds_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            batch = root / "workflows" / "batch-live"
            (batch / "workflow").mkdir(parents=True)
            lock_path = batch / "workflow" / ".run.lock"
            with supervisor._try_file_lock(lock_path) as acquired:
                self.assertTrue(acquired)
                self.assertEqual(run_snapshot.detect_active_batches(root), ["batch-live"])
                with self.assertRaisesRegex(ValueError, "active_batch_running:batch-live"):
                    dev.install({"source_root": str(root)}, backup_dir=Path(tmp) / "bak", allow=True)


class TestPreassembleDiagnostics(unittest.TestCase):
    def test_advisory_unit_findings_do_not_block_or_mutate(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            items = [
                {
                    "unit_id": "u-a8",
                    "kind": "group",
                    "type": "阅读理解",
                    "material": "",
                    "subQuestions": [{"number": 1, "stem": ""}],
                },
                {
                    "unit_id": "u-e20",
                    "kind": "standalone",
                    "type": "单项填空",
                    "subtypes": ["四个选项"],
                    "stem": "1. Hello ___",
                    "options": ["A. a", "B. b", "C. c"],
                },
                {
                    "unit_id": "u-dialog-word",
                    "kind": "group",
                    "type": "补全对话",
                    "subtypes": ["填写单词"],
                    "material": "",
                    "subQuestions": [{"number": 1, "stem": "A: ___ is this?"}],
                },
            ]
            report = preassemble_diagnostics.diagnose(batch, items, write_report=True)
            self.assertTrue(report["advisory_only"])
            self.assertTrue((batch / preassemble_diagnostics.REPORT_REL).is_file())
            a8_codes = {f["code"] for f in report["by_unit"].get("u-a8", [])}
            self.assertIn("PRE_A8_MISSING_SHARED_MATERIAL", a8_codes)
            self.assertIn("PRE_A9_EMPTY_SUBQUESTION", a8_codes)
            e20_codes = {f["code"] for f in report["by_unit"].get("u-e20", [])}
            self.assertIn("PRE_E20_OPTION_COUNT_CONFLICT", e20_codes)
            # 补全对话-填写单词 不应误报 PRE_A8
            self.assertNotIn("u-dialog-word", report["by_unit"])


class TestReviewProgressAndStaleWatchdog(unittest.TestCase):
    def test_part_level_progress_controls_stale_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            (batch / "workflow").mkdir(parents=True)
            now = time.time()
            review_progress.record_part_start(batch, "round-abc", "labels", 4, lease_seconds=120)
            self.assertTrue(review_progress.has_active_progress(batch, 60, now=now + 10))
            # 超出窗口且租约过期 -> 判为无有效进展（即使 activity 仍存在）
            self.assertFalse(review_progress.has_active_progress(batch, 60, now=now + 200))
            # 完成后 -> 不再计作活跃运行分片
            review_progress.record_part_progress(batch, "round-abc", installed_targets=4, status="completed", active_lease=False)
            self.assertFalse(review_progress.has_active_progress(batch, 60, now=now + 1))

    def test_stale_running_skips_review_when_review_gate_is_quality_differences(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            (batch / "workflow").mkdir(parents=True)
            manifest = {
                "stages": {
                    "review": {"status": mf.RUNNING, "started_at": "2020-01-01T00:00:00Z"},
                    "review_gate": {"status": mf.NEEDS_REVIEW, "execution_completed": True},
                }
            }
            stage_id, _ = op._stale_running_candidate(batch, manifest, force=True)
            self.assertIsNone(stage_id)


class TestUnderlineCandidateRanking(unittest.TestCase):
    def test_ambiguous_underline_emits_ranked_candidates_without_mutating(self):
        mod = _load_mark_underline()
        batch_data = {
            "items": [
                {"id": "q1", "options": ["A. cat", "B. school"]},
                {"id": "q2", "options": ["A. cat", "B. school"]},
            ]
        }
        source_text = "1. Pick one.\nA. cat   B. s<u>ch</u>ool\n2. Pick another.\nA. cat   B. school\n"
        out, report = mod.restore(batch_data, source_text)
        self.assertFalse(report["passed"])
        self.assertEqual(report["changes"], [])
        self.assertEqual(out["items"][0]["options"][1], "B. school")
        self.assertEqual(out["items"][1]["options"][1], "B. school")
        issue = report["issues"][0]
        self.assertEqual(issue["code"], "UNDERLINE_MAPPING_AMBIGUOUS")
        self.assertEqual(len(issue["candidates"]), 2)
        self.assertIn("score", issue["candidates"][0])


class TestBatchMetricsSixDimensions(unittest.TestCase):
    def test_distinguishes_pipeline_progress_quality_diff_and_self_heal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 批次 1：硬校验通过 + 审核完成但有真实差异 + pipeline_progress 收口
            b1 = root / "b1"
            (b1 / "workflow").mkdir(parents=True)
            (b1 / "07_review").mkdir(parents=True)
            (b1 / "workflow/manifest.json").write_text(json.dumps({
                "batch_name": "b1",
                "status": "needs_review",
                "stages": {
                    "validate": {"status": "completed"},
                    "format_gate": {"status": "completed"},
                    "review_gate": {"status": "needs_review", "execution_completed": True, "release_ready": False},
                },
            }), encoding="utf-8")
            (b1 / "07_review/validation-report.json").write_text('{"passed": true}', encoding="utf-8")
            (b1 / "07_review/upload-format.json").write_text('{"passed": true}', encoding="utf-8")
            (b1 / "workflow/operations.json").write_text(json.dumps({
                "incident": {"status": "resolved", "resolution_source": "pipeline_progress"}
            }), encoding="utf-8")

            # 批次 2：首次直通无卡点且质量放行
            b2 = root / "b2"
            (b2 / "workflow").mkdir(parents=True)
            (b2 / "07_review").mkdir(parents=True)
            (b2 / "workflow/manifest.json").write_text(json.dumps({
                "batch_name": "b2",
                "status": "completed",
                "stages": {
                    "validate": {"status": "completed"},
                    "format_gate": {"status": "completed"},
                    "review_gate": {"status": "completed", "execution_completed": True, "release_ready": True},
                },
            }), encoding="utf-8")
            (b2 / "07_review/validation-report.json").write_text('{"passed": true}', encoding="utf-8")
            (b2 / "07_review/upload-format.json").write_text('{"passed": true}', encoding="utf-8")

            summary = batch_metrics.summarize_batches([b1, b2])
            m = summary["metrics"]
            self.assertEqual(m["hard_validation_passed"]["count"], 2)
            self.assertEqual(m["review_execution_completed"]["count"], 2)
            self.assertEqual(m["quality_released"]["count"], 1)
            self.assertEqual(m["first_pass_no_blocker"]["count"], 1)
            self.assertEqual(m["self_heal_verified"]["count"], 0)
            self.assertEqual(m["pipeline_progress_only"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
