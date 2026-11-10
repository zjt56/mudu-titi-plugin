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

    def test_legacy_review_failure_before_current_stage_is_not_revived(self):
        _write(self.batch, "workflow/review-failures/legacy.json", {
            "round_id": "labels", "error": "obsolete review failure"})
        record = {"started_at": "2099-01-01T00:00:00+00:00"}
        self.assertEqual(diagnose._review_failure_detail(self.batch, record), "")

    def test_old_review_log_does_not_revive_rejected_failure(self):
        manifest = self._load_manifest()
        manifest["stages"]["review"].update(status=mf.NEEDS_REVIEW,
            started_at="2099-01-01T00:00:00+00:00", notes="current review waiting")
        mf.save(self.batch, manifest)
        _write(self.batch, "workflow/review-failures/legacy.json", {
            "round_id": "labels", "error": "obsolete structured failure"})
        log = self.batch / "workflow/logs/review.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("Error: obsolete log failure", encoding="utf-8")
        detail = diagnose.explain(self.batch, "review")["detail"]
        self.assertNotIn("obsolete", detail)
        self.assertIn("current review waiting", detail)

    def test_legacy_review_failure_cannot_override_explicit_task(self):
        _write(self.batch, "workflow/review-failures/legacy.json", {
            "round_id": "labels", "error": "unbound review failure"})
        self.assertEqual(diagnose._review_failure_detail(
            self.batch, {"task_id": "round-" + "a" * 32}), "")

    def test_quality_gate_reports_quality_findings_class(self):
        manifest = self._load_manifest()
        manifest["stages"]["review_gate"].update(status=mf.BLOCKED, notes="13 条未解决")
        for sid in ("upload_gate", "upload", "publish_gate"):
            manifest["stages"][sid]["status"] = mf.PENDING
        mf.save(self.batch, manifest)
        _write(self.batch, "07_review/manual-review-queue.json", {"items": [{"status": "open"}] * 2})
        item = diagnose.explain(self.batch, "review_gate")
        self.assertEqual(item["failure_class"], "quality_findings")
        self.assertTrue(item["blocking"])
        # 确定性阻断项才是卡点；人工差异保留，不自动改判。
        self.assertIn("确定性阻断项", item["next_action"])
        self.assertIn("不自动改判", item["next_action"])

    def test_executed_review_quality_failure_is_not_called_unexecuted(self):
        manifest = self._load_manifest()
        manifest["stages"]["review_gate"].update(
            status=mf.BLOCKED, execution_completed=True, notes="12 项真实差异保留")
        manifest["stages"]["format_gate"].update(status=mf.PENDING)
        mf.save(self.batch, manifest)
        _write(self.batch, "07_review/manual-review-queue.json", {"items": [{"status": "open"}] * 12})
        _write(self.batch, "07_review/review-gate.json", {
            "passed": False,
            "errors": [
                {"code": "LABEL_AUDIT_INVALID", "unit_id": "q-1", "reason": "知识点不是目录叶子"},
                {"code": "REVIEW_ROUND_FINDING", "unit_id": "q-2", "reason": "标签差异"},
            ],
        })
        gate = diagnose.explain(self.batch, "review_gate")
        self.assertIn("确定性阻断项", gate["reason"])
        self.assertIn("LABEL_AUDIT_INVALID", gate["detail"])
        self.assertIn("q-1", gate["detail"])
        self.assertIn("知识点不是目录叶子", gate["detail"])
        # 差异码不能冒充卡点原因；只提示另有若干人工差异。
        self.assertNotIn("REVIEW_ROUND_FINDING", gate["detail"])
        self.assertIn("12 项人工审核差异待判断", gate["detail"])
        self.assertNotIn("代理产出", gate["reason"])
        downstream = diagnose.explain(self.batch, "format_gate")
        self.assertIn("review_gate（已执行，质量或交付条件未满足", downstream["detail"])
        self.assertFalse(downstream["blocking"])

    def test_executed_unqualified_review_differences_are_not_a_blocker(self):
        manifest = self._load_manifest()
        manifest['stages']['review_gate'].update(status=mf.NEEDS_REVIEW,
            execution_completed=True, continue_unqualified=True,
            quality_status='pending', release_ready=False, notes='18 项真实差异保留')
        mf.save(self.batch, manifest)
        _write(self.batch, '07_review/manual-review-queue.json', {'items': [{'status': 'open'}] * 18})
        result = diagnose.diagnose(self.batch)
        gate = diagnose.explain(self.batch, 'review_gate')
        # 差异是审核产出：保留在队列与报告里，但不是要修掉的卡点。
        self.assertFalse(gate['blocking'])
        self.assertEqual(gate['failure_class'], 'quality_differences')
        self.assertIn('待人工判断', gate['reason'])
        self.assertIn('18 项', gate['detail'])
        self.assertIn('不阻断手动上传', gate['detail'])
        self.assertIn('人工判断', gate['hint'])
        self.assertNotIn('自动修正', gate['hint'])
        self.assertEqual(gate['command'], '')
        self.assertEqual(gate['unmet'], [])
        self.assertEqual(result['blocking_count'], 0)
        self.assertIsNone(result['blocked_at'])
        self.assertIn('18 项审核差异待人工判断', result['summary'])
        self.assertIn('未宣称放行', result['summary'])
        # 不因为诊断展示而改台账状态。
        self.assertEqual(self._load_manifest()['stages']['review_gate']['status'], mf.NEEDS_REVIEW)

    def test_pending_or_running_work_with_old_differences_never_claims_completion(self):
        for sid in ('review', 'format_gate'):
            for status in (mf.PENDING, mf.RUNNING):
                with self.subTest(stage=sid, status=status):
                    manifest = self._load_manifest()
                    manifest['stages']['review_gate'].update(
                        status=mf.NEEDS_REVIEW, execution_completed=True,
                        continue_unqualified=True, release_ready=False)
                    manifest['stages']['review']['status'] = mf.COMPLETED
                    manifest['stages']['format_gate']['status'] = mf.COMPLETED
                    manifest['stages'][sid]['status'] = status
                    mf.save(self.batch, manifest)
                    result = diagnose.diagnose(self.batch)
                    self.assertEqual(result['blocking_count'], 0)
                    self.assertNotIn('自动阶段已执行完成', result['summary'])
                    self.assertIn('审核差异保留', result['summary'])
                    self.assertIn('等待', result['summary'])

    def test_historical_gate_report_does_not_override_current_failure_or_invalidation(self):
        _write(self.batch, '07_review/review-gate.json', {
            'errors': [{'code': 'OLD_LABEL_ERROR', 'message': '历史标签错误'}]})
        for status in (mf.FAILED, mf.INVALIDATED):
            with self.subTest(status=status):
                manifest = self._load_manifest()
                manifest['stages']['review_gate'].update(
                    status=status, notes='current gate failure',
                    invalidation={'scope': 'stage', 'reason': 'current input changed'})
                mf.save(self.batch, manifest)
                item = diagnose.explain(self.batch, 'review_gate')
                self.assertTrue(item['blocking'])
                self.assertNotIn('OLD_LABEL_ERROR', item['detail'])
                self.assertIn('current input changed' if status == mf.INVALIDATED
                              else 'current gate failure', item['detail'])

    def test_blocked_gate_without_deterministic_evidence_does_not_invent_it(self):
        manifest = self._load_manifest()
        manifest['stages']['review_gate'].update(status=mf.BLOCKED, notes='20 项差异')
        mf.save(self.batch, manifest)
        _write(self.batch, '07_review/review-gate.json', {
            'errors': [{'code': 'REVIEW_ROUND_FINDING', 'field': 'knowledge'}]})
        item = diagnose.explain(self.batch, 'review_gate')
        self.assertIn('待核验', item['reason'])
        self.assertNotIn('存在确定性阻断项', item['reason'])
        self.assertNotIn('REVIEW_ROUND_FINDING', item['detail'])

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

    def _producer_envelope(self, stage, *, error=None, task=None):
        manifest = self._load_manifest()
        manifest["stages"][stage].update(
            status=mf.FAILED if error else mf.NEEDS_REVIEW, notes="代理桥执行失败")
        mf.save(self.batch, manifest)
        run = self.batch / "workflow/agent-runs" / (stage + "-current")
        run.mkdir(parents=True)
        if error:
            # local_agent.invoke/main: no import-report exists when invocation fails.
            payload = {"error": error, "code": "execution", "artifacts": {
                "attempt_dir": str(run), "stderr": str(run / "stderr.txt")}}
        else:
            _write(self.batch, str(run.relative_to(self.batch) / "task-report.json"), task)
            payload = {"attempt_dir": str(run), "status": "needs_review", "report": task}
        log = self.batch / "workflow/logs" / (stage + ".log")
        log.parent.mkdir(parents=True, exist_ok=True)
        # runner replaces the stage log with captured stdout followed by stderr.
        log.write_text("$ agent run\n\n" + ("\n[stderr]\n" if error else "")
                       + json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        return run, log

    def _stage_failure_detail(self, stage):
        return diagnose.explain(self.batch, stage)["detail"].split("上游交付未满足：")[0].rstrip("；")

    def test_invocation_error_with_nested_attempt_identity_is_recovered(self):
        error = "Agent invocation failed: executable unavailable" + " detail" * 300
        run, _ = self._producer_envelope("knowledge", error=error)
        # A newer unrelated run must not override the invocation's explicit identity.
        _write(self.batch, "workflow/agent-runs/knowledge-unrelated/import-report.json",
               {"error": "unrelated failure"})
        os.utime(run.parent / "knowledge-unrelated", (2000000000, 2000000000))
        before = {p: p.read_bytes() for p in self.batch.rglob("*") if p.is_file()}
        self.assertEqual(self._stage_failure_detail("knowledge"), error)
        self.assertEqual(before, {p: p.read_bytes() for p in self.batch.rglob("*") if p.is_file()})

    def test_task_report_preconditions_are_recovered_from_real_envelope(self):
        task = {"stage": "knowledge", "status": "needs_review", "completed": False,
                "preconditions": ["Missing confirmed units", "Missing current types artifact"]}
        self._producer_envelope("knowledge", task=task)
        self.assertEqual(self._stage_failure_detail("knowledge"), "；".join(task["preconditions"]))
        self.assertNotIn("attempt_dir", diagnose.explain(self.batch, "knowledge")["detail"])

    def test_real_envelopes_reject_wrong_stage_or_outside_record_binding(self):
        for error in ("invocation failed", None):
            with self.subTest(error=error):
                stage = "knowledge" if error else "assemble"
                run, _ = self._producer_envelope(stage, error=error, task={
                    "stage": stage, "status": "needs_review", "preconditions": ["missing input"]})
                manifest = self._load_manifest()
                for binding in (str(run.parent / "review-other"), str(self.batch.parent)):
                    manifest["stages"][stage]["attempt_dir"] = binding
                    mf.save(self.batch, manifest)
                    self.assertEqual(self._stage_failure_detail(stage), "代理桥执行失败")

    def test_task_report_wrong_stage_is_not_exposed_by_log_fallback(self):
        self._producer_envelope("knowledge", task={"stage": "review", "status": "needs_review",
                                                 "preconditions": ["wrong stage missing input"]})
        self.assertEqual(self._stage_failure_detail("knowledge"), "代理桥执行失败")

    def test_real_envelopes_from_previous_stage_start_are_suppressed(self):
        for stage, error in (("knowledge", "old invocation failed"), ("assemble", None)):
            with self.subTest(stage=stage):
                self._producer_envelope(stage, error=error, task={
                    "stage": stage, "status": "needs_review", "preconditions": ["old missing input"]})
                manifest = self._load_manifest()
                manifest["stages"][stage]["started_at"] = "2099-01-01T00:00:00+00:00"
                mf.save(self.batch, manifest)
                self.assertEqual(self._stage_failure_detail(stage), "代理桥执行失败")

    def test_fresh_envelope_does_not_revive_stale_import_or_task_report(self):
        run, _ = self._producer_envelope("knowledge", error="current invocation failed")
        manifest = self._load_manifest()
        manifest["stages"]["knowledge"]["started_at"] = "2020-01-01T00:00:00+00:00"
        mf.save(self.batch, manifest)
        for filename, report in (
                ("import-report.json", {"error": "stale import failure"}),
                ("task-report.json", {"stage": "knowledge", "status": "needs_review",
                                      "preconditions": ["stale missing input"]})):
            _write(self.batch, str(run.relative_to(self.batch) / filename), report)
            os.utime(run / filename, (1, 1))
        self.assertEqual(self._stage_failure_detail("knowledge"), "current invocation failed")

    def test_nested_invocation_binding_cannot_override_current_attempt(self):
        run, log = self._producer_envelope("knowledge", error="old invocation failed")
        manifest = self._load_manifest()
        current = run.parent / "knowledge-next"
        current.mkdir()
        manifest["stages"]["knowledge"]["attempt_dir"] = str(current)
        mf.save(self.batch, manifest)
        self.assertEqual(self._stage_failure_detail("knowledge"), "代理桥执行失败")
        # Even without a record binding, the logged attempt must belong to this stage.
        manifest["stages"]["knowledge"].pop("attempt_dir")
        mf.save(self.batch, manifest)
        log.write_text(json.dumps({"error": "wrong stage invocation failed", "code": "execution",
                                   "artifacts": {"attempt_dir": str(current.parent / "review-other")}}),
                       encoding="utf-8")
        self.assertEqual(self._stage_failure_detail("knowledge"), "代理桥执行失败")

    def test_fresh_task_envelope_cannot_revive_stale_preconditions(self):
        run, _ = self._producer_envelope("knowledge", task={
            "stage": "knowledge", "status": "needs_review", "preconditions": ["old missing input"]})
        os.utime(run / "task-report.json", (1, 1))
        manifest = self._load_manifest()
        manifest["stages"]["knowledge"]["started_at"] = "2020-01-01T00:00:00+00:00"
        mf.save(self.batch, manifest)
        self.assertEqual(self._stage_failure_detail("knowledge"), "代理桥执行失败")

    def test_current_success_or_empty_repair_suppresses_invocation_envelope(self):
        for stage, completed in (("knowledge", True), ("assemble", False)):
            with self.subTest(stage=stage):
                run, _ = self._producer_envelope(stage, error="obsolete invocation failed")
                if completed:
                    _write(self.batch, str(run.relative_to(self.batch) / "import-report.json"),
                           {"stage": stage, "completed": True})
                else:
                    self._repair(run, 2, {}, stage=stage)
                self.assertEqual(self._stage_failure_detail(stage), "代理桥执行失败")

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


class BoundReviewFailureDiagnoseTest(unittest.TestCase):
    def setUp(self):
        from workflow import review_rounds
        from workflow.test_review_rounds import ReviewRoundsTest
        self.fixture = ReviewRoundsTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.batch = self.fixture.batch
        self.rounds = review_rounds
        self.task = review_rounds.export_task(self.batch)
        self.task_id = self.task["task_id"]
        binding = json.loads((self.batch / f"workflow/review-bindings/{self.task_id}.json").read_text())
        self.state_path = binding["state_path"]
        self.state = json.loads((self.batch / self.state_path).read_text())
        self.failure = {"task_id": self.task_id, "round_id": "labels", "error": "current failure",
                        "input_binding": binding["plan"]["input_binding"],
                        "state_path": self.state_path, "part_index": binding["part_index"],
                        "targets": self.state["parts"][binding["part_index"]]["targets"]}
        self.save_failure()

    def save_failure(self, **changes):
        _write(self.batch, f"workflow/review-failures/{self.task_id}.json", self.failure | changes)

    def detail(self, record=None):
        return diagnose._review_failure_detail(self.batch, record)

    def test_current_bound_failure_is_read_without_writing_state(self):
        before = {str(path.relative_to(self.batch)): path.read_bytes()
                  for path in self.batch.rglob("*") if path.is_file()}
        self.assertEqual(self.detail({"task_id": self.task_id}), "current failure")
        after = {str(path.relative_to(self.batch)): path.read_bytes()
                 for path in self.batch.rglob("*") if path.is_file()}
        self.assertEqual(after, before)

    def test_bound_failure_rejects_wrong_task_and_report_identity(self):
        self.assertEqual(self.detail({"task_id": "round-" + "f" * 32}), "")
        for changes in ({"input_binding": "old-input"}, {"state_path": "workflow/other.json"},
                        {"part_index": 999}, {"targets": ["unrelated-unit"]}, {"round_id": "sample"}):
            with self.subTest(changes=changes):
                self.save_failure(**changes)
                self.assertEqual(self.detail(), "")

    def test_bound_failure_rejects_changed_input_bytes(self):
        source = self.batch / "01_source/正文.txt"
        source.write_text(source.read_text() + "\nChanged actual question content.", encoding="utf-8")
        self.assertEqual(self.detail(), "")

    def test_bound_failure_rejects_retired_generation(self):
        self.rounds.restart_rounds(self.batch)
        self.assertEqual(self.detail(), "")

    def test_bound_failure_rejects_completed_part(self):
        self.state["completed"] = [{"task_id": self.task_id}]
        _write(self.batch, self.state_path, self.state)
        self.assertEqual(self.detail(), "")

    def test_checked_round_suppresses_bound_and_legacy_failures(self):
        self.rounds.import_result(self.batch, self.task_id, self.fixture.response("labels"))
        _write(self.batch, "workflow/review-failures/legacy.json", {
            "round_id": "labels", "error": "historical legacy failure"})
        self.assertEqual(self.detail(), "")

    def test_legacy_task_named_file_uses_its_existing_binding(self):
        self.save_failure(task_id=None)
        self.assertEqual(self.detail(), "current failure")


if __name__ == "__main__":
    unittest.main()
