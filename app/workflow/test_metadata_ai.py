# -*- coding: utf-8 -*-
"""上传后自动识别元信息：只写可确认的字段，识别不出留空，失败不阻断。"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import manifest as mf, metadata_ai

SOURCE_TYPES = ["中考真题", "中考模拟", "同步练习"]


class SanitizeTest(unittest.TestCase):
    def test_keeps_only_verifiable_fields(self):
        found = metadata_ai.sanitize({
            "paper_title": " 2014年安徽省初中毕业学业考试 ",
            "source": "", "source_type": "中考真题", "region": "安徽省", "year": "2014"}, SOURCE_TYPES)
        self.assertEqual(found, {"paper_title": "2014年安徽省初中毕业学业考试",
                                 "source_type": "中考真题", "region": "安徽省", "year": 2014})

    def test_rejects_guesses_placeholders_and_bad_values(self):
        for reply in ({"source_type": "随便编的"}, {"year": "20xx"}, {"year": 1200},
                      {"paper_title": "未提供"}, {"region": "未知"}, None, "text"):
            self.assertEqual(metadata_ai.sanitize(reply, SOURCE_TYPES), {}, reply)

    def test_unknown_fields_are_omitted_not_filled(self):
        self.assertEqual(metadata_ai.sanitize({"source": "   "}, SOURCE_TYPES), {})

    def test_reads_metadata_from_protocol_envelope(self):
        reply = {"status": "completed", "notes": [], "findings": [],
                 "artifacts": {"metadata": {"source_type": "中考模拟", "year": 2025}}}
        self.assertEqual(metadata_ai.extract_metadata(reply),
                         {"source_type": "中考模拟", "year": 2025})
        self.assertEqual(metadata_ai.extract_metadata({"artifacts": {"region": "安徽省"}}),
                         {"region": "安徽省"})
        self.assertEqual(metadata_ai.extract_metadata({"status": "completed", "artifacts": {}}), {})


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        (self.batch / "01_source").mkdir()
        self.batch.joinpath("01_source/metadata.json").write_text(
            json.dumps({"year": 2014, "source_type": "未提供", "paper_title": "规则识别标题"}),
            encoding="utf-8")
        self.batch.joinpath("workflow").mkdir()
        mf.save(self.batch, mf.new_manifest("测试", self.batch, {}, {}))

    def test_fills_blank_fields_and_keeps_identified_values(self):
        result = metadata_ai.merge_metadata(self.batch, {
            "paper_title": "模型标题", "source_type": "中考真题", "region": "安徽省", "year": 2000})
        self.assertEqual(sorted(result["applied"]), ["region", "source_type"])
        self.assertIn("paper_title", result["kept"])
        self.assertIn("year", result["kept"])
        data = json.loads((self.batch / "01_source/metadata.json").read_text())
        self.assertEqual(data["source_type"], "中考真题")
        self.assertEqual(data["region"], "安徽省")
        self.assertEqual(data["paper_title"], "规则识别标题")
        self.assertEqual(data["year"], 2014)
        self.assertTrue(data["evidence"])
        meta = mf.load(self.batch)["meta"]
        self.assertEqual(meta["source_type"], "中考真题")
        self.assertEqual(meta["region"], "安徽省")
        self.assertNotIn("paper_title", meta)


class AutoIdentifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        (self.batch / "01_source").mkdir()
        (self.batch / "01_source/正文.txt").write_text("2014年安徽省初中毕业学业考试\n", encoding="utf-8")
        (self.batch / "workflow").mkdir()
        mf.save(self.batch, mf.new_manifest("测试", self.batch, {}, {}))

    def test_can_be_disabled_by_env(self):
        with patch.dict(os.environ, {"WF_AUTO_IDENTIFY_META": "0"}):
            with patch("workflow.local_agent.invoke") as invoke:
                result = metadata_ai.auto_identify(self.batch)
        self.assertFalse(result["ran"])
        invoke.assert_not_called()

    def test_without_configured_model_does_nothing(self):
        with patch.dict(os.environ, {"WF_AUTO_IDENTIFY_META": "1"}), \
             patch("workflow.agent.configured", return_value=False):
            result = metadata_ai.auto_identify(self.batch)
        self.assertFalse(result["ran"])
        self.assertIn("模型未配置", result["reason"])

    def test_writes_result_once_and_reuses_current_source(self):
        calls = []
        def invoke(prompt, run_dir, config):
            calls.append(prompt)
            self.assertIn("中考真题", prompt)
            return {"response": {"status": "completed", "notes": [], "findings": [],
                                 "artifacts": {"metadata": {
                                     "paper_title": "2014年安徽省初中毕业学业考试",
                                     "source_type": "中考真题", "year": 2014}}}}
        with patch.dict(os.environ, {"WF_AUTO_IDENTIFY_META": "1"}):
            first = metadata_ai.auto_identify(self.batch, invoke_fn=invoke, config={})
            second = metadata_ai.auto_identify(self.batch, invoke_fn=invoke, config={})
        self.assertTrue(first["ran"])
        self.assertEqual(sorted(first["applied"]), ["paper_title", "source_type", "year"])
        self.assertFalse(second["ran"])
        self.assertEqual(len(calls), 1)
        data = json.loads((self.batch / "01_source/metadata.json").read_text())
        self.assertEqual(data["source_type"], "中考真题")

    def test_failure_is_reported_not_raised(self):
        def boom(prompt, run_dir, config):
            raise RuntimeError("transport failed")
        with patch.dict(os.environ, {"WF_AUTO_IDENTIFY_META": "1"}):
            result = metadata_ai.auto_identify(self.batch, invoke_fn=boom, config={})
        self.assertFalse(result["ran"])
        self.assertIn("transport failed", result["reason"])


class RunnerCommandTest(unittest.TestCase):
    def test_identify_meta_reports_applied_fields(self):
        from workflow import runner
        with tempfile.TemporaryDirectory() as tmp, \
             patch("workflow.metadata_ai.auto_identify", return_value={
                 "ran": True, "identified": {"source_type": "中考真题"},
                 "applied": ["source_type"], "kept": []}):
            self.assertEqual(runner.main(["identify-meta", "--batch-dir", tmp]), 0)

    def test_identify_meta_failure_is_non_blocking(self):
        from workflow import runner
        with tempfile.TemporaryDirectory() as tmp, \
             patch("workflow.metadata_ai.auto_identify", return_value={
                 "ran": False, "reason": "模型未配置"}):
            self.assertEqual(runner.main(["identify-meta", "--batch-dir", tmp]), 0)


if __name__ == "__main__":
    unittest.main()
