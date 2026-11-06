# -*- coding: utf-8 -*-
"""卡点诊断聚合器回归：确保把分散在日志/报告里的真实原因翻译出来。"""
import json
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
        self.assertEqual(result["blocked_at"], "assemble")
        item = diagnose.explain(self.batch, "assemble")
        # 真实错误从日志里被挖出来了，而不是那句笼统的 notes。
        self.assertIn("explanation", item["detail"])
        self.assertNotIn("装配待重试", item["detail"])
        self.assertTrue(item["command"].endswith("--stage assemble --force"))
        # assemble 是唯一的真正卡点，其余是被上游阻塞的等待项。
        self.assertEqual(result["blocking_count"], 1)
        self.assertGreaterEqual(result["waiting_count"], 1)

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

    def test_missing_input_reported(self):
        manifest = self._load_manifest()
        manifest["stages"]["audio"]["status"] = mf.PENDING
        manifest["inputs"] = {"audio": {"exists": False}}
        mf.save(self.batch, manifest)
        item = diagnose.explain(self.batch, "audio")
        self.assertIn("audio", item["missing_inputs"])
        self.assertTrue(item["blocking"])

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
