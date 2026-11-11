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


class SourceRulesTest(unittest.TestCase):
    NAME = "26秋《课时A计划》七上英语RJ正文(学用).docx"
    BODY = "Unit 1 Hello!\n**第一课时** Section** **A (1a-2d)\n1.[2025·芜湖月考]题目\n"

    def test_combines_original_name_and_native_lesson_heading(self):
        found = metadata_ai.identify_source(self.BODY, self.NAME)
        self.assertEqual(found["source"], "七上人教版课时A计划 Unit 1 课时1")
        self.assertEqual(found["paper_title"], found["source"])
        self.assertEqual(found["grade"], "七年级")
        self.assertEqual(found["textbook_version"], "人教版")
        self.assertEqual(found["section"], "Section A")
        self.assertEqual(found["source_scope"], "lesson")
        self.assertEqual(found["source_evidence"]["lesson"]["text"], "**第一课时** Section** **A (1a-2d)")
        self.assertNotIn("year", found)
        self.assertNotIn("region", found)

    def test_starter_unit_is_not_ordinary_unit(self):
        found = metadata_ai.identify_source(self.BODY.replace("Unit 1", "Starter Unit 1"), self.NAME)
        self.assertEqual(found["unit"], "Starter Unit 1")
        self.assertIn("Starter Unit 1", found["source"])

    def test_uint_is_normalized(self):
        found = metadata_ai.identify_source(self.BODY.replace("Unit 1", "uint 1"), self.NAME)
        self.assertEqual(found["unit"], "Unit 1")
        self.assertNotIn("uint", found["source"])

    def test_explicit_complete_source_filename_is_supported(self):
        found = metadata_ai.identify_source("1.题目", "七上人教版课时A计划 uint 1 课时1.docx")
        self.assertEqual(found["source"], "七上人教版课时A计划 Unit 1 课时1")

    def test_unknown_grade_version_series_are_not_guessed(self):
        found = metadata_ai.identify_source(self.BODY, "source.docx")
        self.assertEqual(found["source"], "Unit 1 课时1")
        for key in ("grade", "book", "textbook_version", "series", "year", "region"):
            self.assertNotIn(key, found)

    def test_bare_course_name_does_not_invent_series(self):
        found = metadata_ai.identify_source(self.BODY, "26秋课时七上英语RJ正文(学用).docx")
        self.assertNotIn("series", found)
        self.assertEqual(found["source"], "七上人教版 Unit 1 课时1")

    def test_explicit_original_path_can_prove_series(self):
        found = metadata_ai.identify_source(self.BODY, r"D:\原件\26秋《课时A计划》\26秋课时七上英语RJ正文(学用).docx")
        self.assertEqual(found["series"], "课时A计划")

    def test_entire_workbook_does_not_claim_first_lesson(self):
        body = self.BODY + "x" * 5000 + "\nUnit 2\n第二课时 Section B\n"
        found = metadata_ai.identify_source(body, self.NAME)
        self.assertEqual(found["source"], "七上人教版课时A计划")
        self.assertEqual(found["source_scope"], "book")
        for key in ("unit", "lesson", "section"):
            self.assertNotIn(key, found)

    def test_starter_and_unit_same_number_are_multiple_units(self):
        found = metadata_ai.identify_source(self.BODY + "Starter Unit 1\n第一课时 Section A\n", self.NAME)
        self.assertNotIn("lesson", found)
        self.assertNotIn("unit", found)
        self.assertEqual(found["source_scope"], "book")

    def test_exam_citation_alone_is_not_sync_source(self):
        self.assertEqual(metadata_ai.identify_source("1.[2025期中/月考]题目", "source.docx"), {})

    def test_prompt_supports_workbooks_and_source_rules(self):
        prompt = metadata_ai.build_prompt(self.BODY, self.NAME, SOURCE_TYPES)
        self.assertNotIn("ONE English exam", prompt)
        for expected in ("workbooks", "Starter Unit", "first lesson", "Question-level citations"):
            self.assertIn(expected, prompt)


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


class SyncAutoIdentifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        (self.batch / "01_source").mkdir()
        (self.batch / "workflow").mkdir()
        mf.save(self.batch, mf.new_manifest("测试", self.batch, {}, {}))
        self.body = SourceRulesTest.BODY
        self.source = self.batch / "01_source/正文.txt"
        self.source.write_text(self.body, encoding="utf-8")
        self.original = SourceRulesTest.NAME
        state = mf.load(self.batch)
        state["inputs"] = {"source": {"path": str(self.batch / "attachments/source.docx"), "exists": True}}
        mf.save(self.batch, state)
        (self.batch / "attachments").mkdir()
        self.descriptor = self.batch / "attachments/batch.json"
        self.descriptor.write_text(json.dumps({"original_filenames": {"source": self.original}}), encoding="utf-8")

    def test_manifest_descriptor_original_name_used_without_model(self):
        with patch("workflow.agent.configured", return_value=False), patch("workflow.local_agent.invoke") as invoke:
            result = metadata_ai.auto_identify(self.batch)
        invoke.assert_not_called()
        self.assertTrue(result["ran"])
        self.assertEqual(result["identified"]["source"], "七上人教版课时A计划 Unit 1 课时1")
        data = json.loads((self.batch / "01_source/metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(data["unit"], "Unit 1")
        self.assertIn("lesson", data["source_evidence"])
        self.assertEqual(mf.load(self.batch)["workflow_id"], "测试")

    def test_explicit_filename_beats_descriptor_and_batch(self):
        result = metadata_ai.auto_identify(self.batch, filename="八上人教版课时A计划 Unit 1 课时1.docx", invoke_fn=lambda *args: {})
        self.assertEqual(result["identified"]["book"], "八上")

    def test_manifest_original_filename_beats_localized_path(self):
        state = mf.load(self.batch)
        state["inputs"]["source"]["original_filename"] = "九上人教版课时A计划.docx"
        mf.save(self.batch, state)
        self.assertEqual(metadata_ai.source_filename(self.batch, state), "九上人教版课时A计划.docx")

    def test_source_path_used_when_no_original_name_survives(self):
        self.descriptor.write_text("{}", encoding="utf-8")
        state = mf.load(self.batch)
        state["inputs"]["source"]["path"] = r"D:\原件\课时A计划\七上英语RJ.docx"
        mf.save(self.batch, state)
        self.assertEqual(metadata_ai.source_filename(self.batch, state), state["inputs"]["source"]["path"])
        result = metadata_ai.auto_identify(self.batch, invoke_fn=lambda *args: {})
        self.assertEqual(result["identified"]["series"], "课时A计划")

    def test_original_basename_and_source_path_keep_separate_evidence(self):
        self.descriptor.write_text(json.dumps({"original_filenames": {"source": "26秋课时七上英语RJ正文(学用).docx"}}), encoding="utf-8")
        state = mf.load(self.batch)
        state["inputs"]["source"]["path"] = r"D:\原件\26秋《课时A计划》\source.docx"
        mf.save(self.batch, state)
        with patch("workflow.agent.configured", return_value=False), patch("workflow.local_agent.invoke") as invoke:
            result = metadata_ai.auto_identify(self.batch)
        invoke.assert_not_called()
        evidence = result["identified"]["source_evidence"]
        self.assertEqual(evidence["series"]["origin"], "source_path")
        self.assertEqual(evidence["book"]["origin"], "filename")
        self.assertEqual(result["identified"]["source"], "七上人教版课时A计划 Unit 1 课时1")
        saved = mf.load(self.batch)["meta"]
        self.assertEqual(saved["source_evidence"]["series"], evidence["series"])
        self.assertEqual(saved["source_evidence"]["lesson"], evidence["lesson"])

    def test_existing_metadata_and_manifest_values_are_preserved(self):
        (self.batch / "01_source/metadata.json").write_text(json.dumps({"source": "用户来源", "paper_title": "用户标题", "lesson": "课时3"}), encoding="utf-8")
        state = mf.load(self.batch)
        state["meta"] = {"source": "manifest用户来源", "grade": "九年级"}
        mf.save(self.batch, state)
        result = metadata_ai.auto_identify(self.batch, invoke_fn=lambda *args: {})
        self.assertIn("source", result["kept"])
        data = json.loads((self.batch / "01_source/metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(data["source"], "用户来源")
        self.assertEqual(data["lesson"], "课时3")
        self.assertNotIn("lesson", data["source_evidence"])
        self.assertEqual(mf.load(self.batch)["meta"]["source"], "manifest用户来源")
        self.assertEqual(mf.load(self.batch)["meta"]["grade"], "九年级")

    def test_question_citations_cannot_override_deterministic_source(self):
        result = metadata_ai.auto_identify(self.batch, invoke_fn=lambda *args: {"response": {
            "source": "2025期中月考", "paper_title": "2025期中月考", "source_type": "中考真题", "year": 2025, "region": "芜湖"}})
        self.assertEqual(result["identified"]["source_type"], "同步练习")
        self.assertNotIn("year", result["identified"])
        self.assertNotIn("region", result["identified"])

    def test_cache_binds_original_filename_and_rules(self):
        calls = []
        def invoke(prompt, *args):
            calls.append(prompt)
            return {}
        first = metadata_ai.auto_identify(self.batch, invoke_fn=invoke)
        second = metadata_ai.auto_identify(self.batch, invoke_fn=invoke)
        self.descriptor.write_text(json.dumps({"original_filenames": {"source": "八上人教版课时A计划.docx"}}), encoding="utf-8")
        third = metadata_ai.auto_identify(self.batch, invoke_fn=invoke)
        with patch.object(metadata_ai, "RULES_VERSION", "next-rules"):
            fourth = metadata_ai.auto_identify(self.batch, invoke_fn=invoke)
        self.assertTrue(first["ran"])
        self.assertFalse(second["ran"])
        self.assertEqual(third["identified"]["book"], "八上")
        self.assertEqual(len(calls), 3)
        self.assertNotIn(self.batch.name, calls[0])
        self.assertIn(self.original, calls[0])
        self.assertTrue(fourth["ran"])

    def test_local_only_cache_does_not_hide_later_model_configuration(self):
        with patch("workflow.agent.configured", return_value=False):
            metadata_ai.auto_identify(self.batch)
        calls = []
        result = metadata_ai.auto_identify(self.batch, invoke_fn=lambda *args: calls.append(args) or {})
        self.assertTrue(result["ran"])
        self.assertEqual(len(calls), 1)

    def test_deterministic_values_survive_model_failure(self):
        def boom(*args):
            raise RuntimeError("transport failed")
        result = metadata_ai.auto_identify(self.batch, invoke_fn=boom)
        self.assertTrue(result["ran"])
        self.assertIn("transport failed", result["reason"])
        self.assertEqual(result["identified"]["lesson"], "课时1")


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
