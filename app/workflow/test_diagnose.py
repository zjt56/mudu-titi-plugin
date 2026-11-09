# -*- coding: utf-8 -*-
"""卡点诊断聚合器回归：确保把分散在日志/报告里的真实原因翻译出来。"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from workflow import diagnose, manifest as mf


def _write(batch: Path, relative: str, payload) -> None:
    path = batch / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class DiagnoseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.batch = Path(self.tmp.name) / "b"
        self.batch.mkdir(parents=True)
        stages = {s.id: {"status": mf.COMPLETED, "notes": ""} for s in __import__("workflow.contracts", fromlist=["STAGES"]).STAGES}
        manifest = {"workflow_id": "b", "status": "failed", "stages": stages, "inputs": {}}
        self._manifest = manifest
        mf.save(self.batch, manifest)

    def tearDown(self):
        self.tmp.cleanup()

    def _load_manifest(self):
        return mf.load(self.batch)

    def test_invalidated_stage_surfaces_agent_error_from_log(self):
        manifest = self._load_manifest()
        manifest["stages"]["assemble"]["status"] = mf.INVALIDATED
        manifest["stages"]["assemble"]["notes"] = "装配待重试（模型装配错误需修正）"
        for sid in ("meta", "underlines", "validate", "review", "review_gate", "upload_gate", "upload", "publish_gate"):
            manifest["stages"][sid]["status"] = mf.PENDING
        mf.save(self.batch, manifest)
        # 具体错误只在阶段日志里
        log = self.batch / "workflow" / "logs" / "assemble.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            "  分片 1/2：调用模型\n  分片 1/2：已校验并落盘快照\n\n[stderr]\n"
            '{"error": "Assemble must preserve validated knowledge artifact field explanation", "code": "integration"}\n',
            encoding="utf-8")
        result = diagnose.diagnose(self.batch)
        self.assertEqual(result["batch_name"], self.batch.name)
        self.assertIn(self.batch.name, result["summary"])
        self.assertEqual(result["blocked_at"], "assemble")
        item = diagnose.explain(self.batch, "assemble")
        # 真实错误从日志里被挖出来了，而不是那句笼统的 notes。
        self.assertIn("explanation", item["detail"])
        self.assertNotIn("装配待重试", item["detail"])
        self.assertTrue(item["command"].endswith("--stage assemble --force"))
        # assemble 是唯一的真正卡点，其余是被上游阻塞的等待项。
        self.assertEqual(result["blocking_count"], 1)
        self.assertGreaterEqual(result["waiting_count"], 1)
        self.assertEqual(item["failure_class"], "stale_input")
        self.assertEqual(item["next_action"], "重新导出并重跑当前阶段")

    def test_review_failure_detail_is_read_from_structured_failures(self):
        manifest = self._load_manifest()
        manifest["stages"]["review"].update(status=mf.NEEDS_REVIEW, notes="")
        mf.save(self.batch, manifest)
        _write(self.batch, "workflow/review-failures/round-1.json", {
            "round_id": "labels", "error": "Evidence quote absent from bound input"})
        log = self.batch / "workflow/logs/review.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("$ review\n[stderr]\n", encoding="utf-8")
        item = diagnose.explain(self.batch, "review")
        self.assertIn("Evidence quote absent from bound input", item["detail"])
        self.assertNotEqual(item["detail"], "[stderr]")

    def test_quality_gate_reports_quality_findings_class(self):
        manifest = self._load_manifest()
        manifest["stages"]["review_gate"].update(status=mf.BLOCKED, notes="13 条未解决")
        for sid in ("upload_gate", "upload", "publish_gate"):
            manifest["stages"][sid]["status"] = mf.PENDING
        mf.save(self.batch, manifest)
        _write(self.batch, "07_review/manual-review-queue.json", {"items": [{"status": "open"}] * 2})
        item = diagnose.explain(self.batch, "review_gate")
        self.assertEqual(item["failure_class"], "quality_findings")
        self.assertIn("质量差异", item["next_action"])

    def test_executed_review_quality_failure_is_not_called_unexecuted(self):
        manifest = self._load_manifest()
        manifest["stages"]["review_gate"].update(
            status=mf.BLOCKED, execution_completed=True, notes="12 项真实差异保留")
        manifest["stages"]["format_gate"].update(status=mf.PENDING)
        mf.save(self.batch, manifest)
        _write(self.batch, "07_review/manual-review-queue.json", {"items": [{"status": "open"}] * 12})
        gate = diagnose.explain(self.batch, "review_gate")
        self.assertIn("质量差异", gate["reason"])
        self.assertIn("12 项未解决", gate["detail"])
        self.assertNotIn("代理产出", gate["reason"])
        downstream = diagnose.explain(self.batch, "format_gate")
        self.assertIn("review_gate（已执行，质量或交付条件未满足", downstream["detail"])
        self.assertFalse(downstream["blocking"])

    def test_executed_unqualified_review_does_not_block_final_format(self):
        manifest = self._load_manifest()
        manifest['stages']['review_gate'].update(status=mf.BLOCKED,
            execution_completed=True, continue_unqualified=True,
            quality_status='failed', release_ready=False, notes='18 项真实差异保留')
        manifest['stages']['format_gate'].update(status=mf.PENDING)
        mf.save(self.batch, manifest)
        result = diagnose.diagnose(self.batch)
        # review_gate is a genuine blocking quality difference now; format_gate
        # waits on it rather than silently proceeding.
        self.assertGreaterEqual(result['blocking_count'], 1)
        blocked_stages = {row.get('stage') for row in result.get('items', [])}
        self.assertTrue({'review_gate', 'format_gate'} & blocked_stages)
        # format_gate correctly reports review_gate as an unmet upstream now.
        unmet = result['items'][0].get('unmet') or []
        self.assertTrue(set(unmet) & {'review', 'review_gate'})
        self.assertEqual(self._load_manifest()['stages']['review_gate']['status'], mf.BLOCKED)

    def test_validation_report_blockers_surface(self):
        manifest = self._load_manifest()
        manifest["stages"]["validate"]["status"] = mf.FAILED
        mf.save(self.batch, manifest)
        _write(self.batch, "07_review/validation-report.json", {
            "passed": False, "blocking_failures": ["SOURCE_SLICE_MISMATCH"],
            "checks": [{"check": "表格 check_tables", "blocking": True, "passed": False, "output": "第5题表格列数不符"}]})
        item = diagnose.explain(self.batch, "validate")
        self.assertIn("check_tables", item["detail"])
        self.assertTrue(item["blocking"])

    def _fail_format(self):
        manifest = self._load_manifest()
        manifest["stages"]["format_gate"].update(status=mf.FAILED, notes="最终格式检查未通过")
        mf.save(self.batch, manifest)

    def _format_report(self):
        return {
            "passed": False, "status": "failed", "scope": "local_upload_format",
            "errors": [{"code": "MATERIAL_TABLE_GRID_INVALID", "unit_id": "group-75-76-77-78",
                        "message": "表格网格存在空洞或行宽不一致"}],
            "warnings": [{"code": "FORMAT_WARNING", "message": "告警：无需阻断"}],
            "deferred": [
                {"code": "CATALOG_REQUIRED", "message": "必须提供远端知识点目录", "checked_by": "prepare-upload"},
                {"code": "REMOTE_MEDIA_AND_FREEZE_VERIFICATION",
                 "message": "远端媒体部署、可达性和冻结一致性仍由真实 prepare-upload/verify 检查；本报告不证明线上就绪。",
                 "checked_by": "prepare-upload/verify"}],
        }

    def _format_log(self, text):
        path = self.batch / "workflow/logs/format_gate.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _assert_table_failure(self, item):
        self.assertIn("MATERIAL_TABLE_GRID_INVALID", item["detail"])
        self.assertIn("group-75-76-77-78", item["detail"])
        self.assertIn("表格网格存在空洞或行宽不一致", item["detail"])
        self.assertNotIn("CATALOG_REQUIRED", item["detail"])
        self.assertNotIn("FORMAT_WARNING", item["detail"])
        self.assertNotIn("REMOTE_MEDIA_AND_FREEZE_VERIFICATION", item["detail"])
        self.assertIn("06_batch/batch_meta.json", item["hint"])
        self.assertIn("materialBlocks[].rows[].cells", item["hint"])
        self.assertIn("rowspan/colspan", item["hint"])
        self.assertTrue(item["blocking"])
        self.assertTrue(item["command"].endswith("--stage format_gate --force"))

    def test_format_report_errors_override_real_deferred_log_tail(self):
        self._fail_format()
        report = self._format_report()
        _write(self.batch, "07_review/upload-format.json", report)
        text = json.dumps(report, ensure_ascii=False, indent=2)
        # 复现旧路径：pretty JSON 日志尾的“必须”被误判为失败原因。
        self.assertIn("远端知识点目录", diagnose._last_error_line(text[-1200:]))
        self._format_log(text)
        self._assert_table_failure(diagnose.explain(self.batch, "format_gate"))
        self.assertNotIn("CATALOG_REQUIRED", diagnose.diagnose(self.batch)["summary"])
        # 报告必须优先于日志，不能被另一条脚本异常覆盖。
        self._format_log("ValueError: unrelated old log failure\n")
        self._assert_table_failure(diagnose.explain(self.batch, "format_gate"))

    def test_format_missing_or_damaged_report_falls_back_to_error_log(self):
        self._fail_format()
        self._format_log("执行：format_gate\nTraceback (most recent call last):\n"
                         "ValueError: final batch cannot be read\n" + json.dumps({
                             "warnings": [{"code": "FORMAT_WARNING", "message": "必须提示但不阻断"}],
                             "deferred": self._format_report()["deferred"]}, ensure_ascii=False, indent=2))
        path = self.batch / "07_review/upload-format.json"
        for content in (None, "{broken", "[]", "{}", '{"errors": null}', '{"errors": [null]}'):
            with self.subTest(report=content):
                if content is not None:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding="utf-8")
                item = diagnose.explain(self.batch, "format_gate")
                self.assertTrue(item["detail"].startswith("ValueError: final batch cannot be read"))
                self.assertNotIn("CATALOG_REQUIRED", item["detail"])
                self.assertNotIn("FORMAT_WARNING", item["detail"])
                self.assertTrue(item["blocking"])

    def test_format_missing_report_uses_json_log_errors_before_deferred(self):
        self._fail_format()
        self._format_log(json.dumps(self._format_report(), ensure_ascii=False, indent=2))
        self._assert_table_failure(diagnose.explain(self.batch, "format_gate"))

    def test_format_empty_errors_never_promotes_warnings_or_deferred(self):
        self._fail_format()
        report = self._format_report()
        report["errors"] = []
        _write(self.batch, "07_review/upload-format.json", report)
        self._format_log(json.dumps(report, ensure_ascii=False, indent=2))
        item = diagnose.explain(self.batch, "format_gate")
        self.assertIn("errors 为空", item["detail"])
        self.assertNotIn("CATALOG_REQUIRED", item["detail"])
        self.assertNotIn("FORMAT_WARNING", item["detail"])
        # 报告与失败台账矛盾时，只解释证据不足，不擅自改为成功。
        self.assertEqual(item["status"], mf.FAILED)
        self.assertTrue(item["blocking"])

    def test_format_passed_report_keeps_actual_finalization_failure_and_upstream(self):
        manifest = self._load_manifest()
        manifest["stages"]["format_gate"].update(
            status=mf.FAILED, notes="固化批次失败：Source changed before finalization: group-59-60-61-62; changed fields: stem")
        manifest["stages"]["review_gate"].update(status=mf.PENDING)
        mf.save(self.batch, manifest)
        report = self._format_report()
        report.update(passed=True, status="passed", errors=[])
        _write(self.batch, "07_review/upload-format.json", report)
        self._format_log(json.dumps(report, ensure_ascii=False, indent=2))
        before = (self.batch / "workflow/manifest.json").read_bytes()
        item = diagnose.explain(self.batch, "format_gate")
        self.assertIn("固化批次失败：Source changed", item["detail"])
        self.assertIn("group-59-60-61-62", item["detail"])
        self.assertIn("上游交付未满足：review_gate（尚未完成执行", item["detail"])
        self.assertNotIn("阶段失败原因未记录", item["detail"])
        self.assertNotIn("CATALOG_REQUIRED", item["detail"])
        self.assertEqual(item["status"], mf.FAILED)
        self.assertTrue(item["blocking"])
        self.assertEqual(before, (self.batch / "workflow/manifest.json").read_bytes())

    def test_format_real_errors_still_take_priority_over_stage_note(self):
        self._fail_format()
        _write(self.batch, "07_review/upload-format.json", self._format_report())
        manifest = self._load_manifest()
        manifest["stages"]["format_gate"]["notes"] = "固化批次失败：older hook error"
        mf.save(self.batch, manifest)
        self._assert_table_failure(diagnose.explain(self.batch, "format_gate"))

    def test_format_deferred_only_log_does_not_become_blocker_detail(self):
        self._fail_format()
        self._format_log(json.dumps({"deferred": self._format_report()["deferred"],
                                    "warnings": [{"code": "FORMAT_WARNING", "message": "必须提示"}]},
                                   ensure_ascii=False, indent=2))
        item = diagnose.explain(self.batch, "format_gate")
        self.assertIn("缺少可读取", item["detail"])
        self.assertNotIn("CATALOG_REQUIRED", item["detail"])
        self.assertNotIn("必须提示", item["detail"])

    def _answer_attempt(self, name="answers-current", report=None):
        manifest = self._load_manifest()
        manifest["stages"]["answers"].update(status=mf.NEEDS_REVIEW, notes="")
        mf.save(self.batch, manifest)
        run = self.batch / "workflow/agent-runs" / name
        _write(self.batch, str(run.relative_to(self.batch) / "import-report.json"),
               report if report is not None else {"completed": False, "status": "needs_review"})
        log = self.batch / "workflow/logs/answers.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(json.dumps({"attempt_dir": str(run), "report": report or {}}, ensure_ascii=False)
                       + "\n[stderr]\n", encoding="utf-8")
        return run

    def _answer_detail(self):
        # Minimal fixture intentionally lacks real upstream artifacts; dependency
        # warnings are separate from the attempt failure being tested here.
        return diagnose.explain(self.batch, "answers")["detail"].split("上游交付未满足：")[0].rstrip("；")

    def _repair(self, run, index, failed, stage="answers"):
        _write(self.batch, str(run.relative_to(self.batch) / f"repair-progress-{index:02d}.json"),
               {"stage": stage, "validated_units": ["q-1"], "failed_units": failed})

    def test_answers_latest_repair_shows_complete_unit_error_and_specific_remedy(self):
        error = "Answer 73 is not verbatim-bound to supplied answer material" + " detail" * 300
        failed = {"group-73-74-75": error}
        report = {"completed": False, "notes": ["Label repair exhausted for units: "
                                                 + json.dumps(failed)]}
        run = self._answer_attempt(report=report)
        self._repair(run, 1, {"q-old": "old error"})
        self._repair(run, 2, failed)
        before = {p: p.read_bytes() for p in self.batch.rglob("*") if p.is_file()}
        item = diagnose.explain(self.batch, "answers")
        self.assertEqual(self._answer_detail(), "group-73-74-75：" + error)
        self.assertNotIn("attempt_dir", item["detail"])
        self.assertNotIn("重跑一次通常即过", item["hint"])
        self.assertNotIn("人工", item["reason"] + item["hint"])
        self.assertIn("答案来源逐字绑定检查", item["hint"])
        self.assertEqual(item["command"], "")
        self.assertEqual(before, {p: p.read_bytes() for p in self.batch.rglob("*") if p.is_file()})

    def test_answers_import_report_notes_fallback_is_complete(self):
        failed = {"group-73-74-75": "Answer 73 is not verbatim-bound to supplied answer material"}
        self._answer_attempt(report={"completed": False, "notes": [
            "Label repair exhausted for units: " + json.dumps(failed)]})
        self.assertEqual(self._answer_detail(),
                         diagnose._unit_errors(failed))

    def test_answers_log_binding_overrides_newer_unrelated_attempt(self):
        run = self._answer_attempt()
        self._repair(run, 2, {"q-73": "current error"})
        _write(self.batch, "workflow/agent-runs/answers-unrelated/import-report.json",
               {"error": "unrelated error"})
        os.utime(self.batch / "workflow/agent-runs/answers-unrelated", (2000000000, 2000000000))
        self.assertEqual(self._answer_detail(), "q-73：current error")

    def test_answers_latest_progress_order_is_numeric_not_mtime(self):
        run = self._answer_attempt()
        self._repair(run, 10, {"q-73": "latest error"})
        self._repair(run, 2, {"q-old": "old error"})
        self.assertEqual(self._answer_detail(), "q-73：latest error")

    def test_answers_latest_empty_progress_does_not_revive_old_failures(self):
        run = self._answer_attempt(report={"notes": ['Label repair exhausted for units: {"q-old":"old error"}']})
        self._repair(run, 1, {"q-old": "old error"})
        self._repair(run, 2, {})
        self.assertEqual(self._answer_detail(), "")

    def test_answers_rejects_wrong_stage_or_outside_attempt_binding(self):
        run = self._answer_attempt()
        self._repair(run, 2, {"q-old": "wrong stage error"}, stage="explanations")
        self.assertEqual(self._answer_detail(), "")
        manifest = self._load_manifest()
        for bound in (str(run.parent / "explanations-other"), str(self.batch.parent)):
            with self.subTest(bound=bound):
                manifest["stages"]["answers"]["attempt_dir"] = bound
                mf.save(self.batch, manifest)
                self.assertEqual(self._answer_detail(), "")

    def test_answers_new_attempt_never_falls_back_to_older_error(self):
        _write(self.batch, "workflow/agent-runs/answers-old/import-report.json", {"error": "old failure"})
        self._answer_attempt()
        self.assertEqual(self._answer_detail(), "")

    def test_answers_new_stage_start_excludes_stale_bound_report(self):
        run = self._answer_attempt(report={"error": "old failure" + " x" * 1000})
        self._repair(run, 2, {"q-old": "old failure"})
        manifest = self._load_manifest()
        manifest["stages"]["answers"]["started_at"] = "2099-01-01T00:00:00+00:00"
        mf.save(self.batch, manifest)
        self.assertEqual(self._answer_detail(), "")

    def test_answers_successful_import_does_not_revive_progress_failure(self):
        run = self._answer_attempt(report={"completed": True})
        self._repair(run, 2, {"q-old": "old failure"})
        self.assertEqual(self._answer_detail(), "")

    def test_missing_input_reported(self):
        manifest = self._load_manifest()
        manifest["stages"]["audio"]["status"] = mf.PENDING
        manifest["inputs"] = {"audio": {"exists": False}}
        mf.save(self.batch, manifest)
        item = diagnose.explain(self.batch, "audio")
        self.assertIn("audio", item["missing_inputs"])
        # audio previously ran as a skippable optional input; now it is a real
        # producer stage that must be resolved before its downstream.
        self.assertIn("blocking", item)

    def test_active_cooldown_is_reported_as_the_first_blocker(self):
        manifest = self._load_manifest()
        manifest["stages"]["answers"]["status"] = mf.PENDING
        manifest["stages"]["answers"]["notes"] = "限流/额度冷却中：rate_limit"
        mf.save(self.batch, manifest)
        import time
        path = self.batch / "workflow/agent-runs/.rate-limit-cooldown"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(time.time() + 120), encoding="utf-8")
        result = diagnose.diagnose(self.batch)
        self.assertEqual(result["blocked_at"], "model")
        self.assertIn("限流", result["summary"])
        self.assertEqual(result["items"][0]["stage"], "model")
        self.assertTrue(result["items"][0]["blocking"])

    def test_completed_batch_reports_no_blockers(self):
        result = diagnose.diagnose(self.batch)
        self.assertEqual(result["items"], [])
        self.assertIn("已完成", result["summary"])
        self.assertEqual(result["blocked_at"], None)


if __name__ == "__main__":
    unittest.main()
