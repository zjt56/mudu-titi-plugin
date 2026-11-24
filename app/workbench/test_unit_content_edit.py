"""原卷修改：文章大意/解析写入正式批次，材料等原文投影仍只存草稿。

用户要求「原卷修改」也能改文章大意，并确认保存要真正进入上传内容。
解析与文章大意属于派生文本字段，写入 06_batch/batch_meta.json 不会破坏
画线/元数据投影；材料、题干、选项、答案属于原文/答案投影，仍只写草稿，
避免绕过对应阶段直接改写原文。人工编辑同时登记在
workflow/manual-content-edits.json，供重新组装时重放。
"""
import json
import tempfile
import unittest
from pathlib import Path

from workbench import server
from workflow import manifest as mf
from workflow import manual_content


class UnitContentEditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        batch = self.folder / "06_batch"
        batch.mkdir(parents=True)
        self.item = {
            "unit_id": "q-1", "id": "q-1", "number": 1, "numbers": [1],
            "unit_kind": "standalone", "stem": "What did Bill get?",
            "material": "原文材料", "options": [], "answer": "A",
            "explanation": "旧解析", "articleSummary": "旧大意", "subquestions": [],
            "question_type": "单项填空",
        }
        (batch / "batch_meta.json").write_text(
            json.dumps({"items": [self.item], "name": "t"}, ensure_ascii=False), encoding="utf-8")
        knowledge = self.folder / "04_knowledge"
        knowledge.mkdir(parents=True)
        (knowledge / "explanations.json").write_text(json.dumps({"items": [{
            "unit_id": "q-1", "explanation": "旧解析", "articleSummary": "旧大意",
            "subquestions": [], "evidence": ["按题号从 Word 原文直接抄录。"],
        }]}, ensure_ascii=False), encoding="utf-8")

    def save(self, unit, items=None):
        return server.Handler.save_units(self.folder, "q-1", items or [], None, unit)

    def candidate(self):
        return json.loads((self.folder / "candidates" / "q-1.json").read_text(encoding="utf-8"))

    def batch_row(self):
        data = json.loads((self.folder / "06_batch" / "batch_meta.json").read_text(encoding="utf-8"))
        return data["items"][0]

    def manual_edits(self):
        data = json.loads((self.folder / "workflow" / "manual-content-edits.json").read_text(encoding="utf-8"))
        return data["edits"]["q-1"]

    def test_summary_and_explanation_are_applied_to_the_formal_batch(self):
        result = self.save({"articleSummary": "本文介绍 Bill 的礼物。", "explanation": "由第二句可知。"})
        row = self.batch_row()
        self.assertEqual(row["articleSummary"], "本文介绍 Bill 的礼物。")
        self.assertEqual(row["explanation"], "由第二句可知。")
        self.assertTrue(result["applied"]["applied"])
        # 审核页再次读取时，正式行已是最新值（不靠草稿遮盖旧值）。
        item, _ = server.candidate(self.folder, "q-1")
        self.assertEqual(item["articleSummary"], "本文介绍 Bill 的礼物。")
        self.assertEqual(item["explanation"], "由第二句可知。")
        # 解析产物必须与交付内容逐字一致，否则下一次组装会回滚。
        artifact = json.loads((self.folder / "04_knowledge" / "explanations.json").read_text(encoding="utf-8"))
        self.assertEqual(artifact["items"][0]["articleSummary"], "本文介绍 Bill 的礼物。")
        self.assertEqual(artifact["items"][0]["explanation"], "由第二句可知。")
        # 覆盖上一个内容版本前留存可回退副本。
        self.assertTrue((self.folder / "06_batch" / "batch_meta.json.bak").is_file())
        self.assertTrue((self.folder / "04_knowledge" / "explanations.json.bak").is_file())

    def test_material_stays_a_draft_and_is_reported(self):
        result = self.save({"material": "手改材料", "articleSummary": "新大意"})
        row = self.batch_row()
        self.assertEqual(row["material"], "原文材料")
        self.assertEqual(row["articleSummary"], "新大意")
        self.assertEqual(self.candidate()["material"], "手改材料")
        self.assertIn("material", result["applied"]["skipped"])
        self.assertTrue(any("材料" in text for text in result["warnings"]))

    def test_manual_record_is_replayable_and_scoped(self):
        self.save({"articleSummary": "人工大意", "explanation": "人工解析", "material": "手改材料"},
                  items=[{"number": 1, "stem": "手改题干"}])
        edit = self.manual_edits()
        self.assertEqual(edit["articleSummary"], "人工大意")
        self.assertEqual(edit["explanation"], "人工解析")
        self.assertNotIn("material", edit)
        self.assertNotIn("questions", edit)
        self.assertEqual(edit["updated_by"], "workbench")

    def test_summary_can_be_cleared(self):
        self.save({"articleSummary": "旧大意-改"})
        self.assertEqual(self.batch_row()["articleSummary"], "旧大意-改")
        self.save({"articleSummary": ""})
        self.assertEqual(self.batch_row()["articleSummary"], "")
        self.assertEqual(self.manual_edits()["articleSummary"], "")

    def test_downstream_gates_are_invalidated_after_a_real_change(self):
        workflow = self.folder / "workflow"
        workflow.mkdir(parents=True, exist_ok=True)
        stages = {name: {"status": mf.COMPLETED, "input_hashes": {}, "output_hashes": {}}
                  for name in ("explanations", "assemble", "validate", "review", "upload", "upload_gate")}
        stages["upload"]["status"] = mf.SKIPPED
        (workflow / "manifest.json").write_text(
            json.dumps({"stages": stages, "status": "completed"}, ensure_ascii=False), encoding="utf-8")
        self.save({"articleSummary": "改动后的大意"})
        current = mf.load(self.folder)
        for name in ("assemble", "validate", "review", "upload", "upload_gate"):
            self.assertEqual(current["stages"][name]["status"], mf.INVALIDATED, name)
        # 解析产物本身是人工确认的：重登记而不是标成过期重跑。
        explanations = current["stages"]["explanations"]
        self.assertEqual(explanations["status"], mf.COMPLETED)
        self.assertEqual(explanations["output_hashes"], mf.output_hashes(
            self.folder, server.wf_contracts.STAGE_BY_ID["explanations"]))
        self.assertIn("workflow/manual-content-edits.json", " ".join(current["human_decisions"][-1]["evidence"]))
        # 人工编辑记录本身不是阶段完成凭据，也不能被伪造成审核通过。
        self.assertNotEqual(current["stages"]["review"]["status"], mf.COMPLETED)

    def test_label_only_save_does_not_touch_formal_content(self):
        self.save({})
        self.assertEqual(self.batch_row()["articleSummary"], "旧大意")
        self.assertFalse((self.folder / "workflow" / "manual-content-edits.json").exists())

    def test_review_candidate_view_exposes_article_summary(self):
        self.save({"articleSummary": "审核页看到的新大意"})
        item, path = server.candidate(self.folder, "q-1")
        self.assertEqual(item["articleSummary"], "审核页看到的新大意")
        # 解析/大意直接写正式批次，草稿里不再留一份可能过期的副本。
        self.assertFalse((self.folder / "candidates" / "q-1.json").exists())
        self.assertIsNone(path)


    def test_stale_editor_cannot_overwrite_a_newer_value(self):
        self.save({"articleSummary": "新大意"})
        with self.assertRaisesRegex(ValueError, "正式内容已变化"):
            server.Handler.save_units(self.folder, "q-1", [], None,
                                      {"articleSummary": "过期修改"}, {"articleSummary": "旧大意"})
        self.assertEqual(self.batch_row()["articleSummary"], "新大意")

    def test_edit_routes_respect_the_same_batch_model_lock(self):
        from workflow.local_agent import _batch_lock

        with _batch_lock(self.folder, 0, shared=True):
            with self.assertRaisesRegex(ValueError, "正在运行"):
                self.save({"articleSummary": "未保存"})
            with self.assertRaisesRegex(ValueError, "正在运行"):
                server.Handler.save_content_edit(self.folder, "q-1", {})
        self.assertEqual(self.batch_row()["articleSummary"], "旧大意")
        self.assertTrue(self.save({"articleSummary": "新大意"})["applied"]["applied"])

    def test_replace_failure_rolls_back_producer_and_formal_batch(self):
        from unittest.mock import patch
        from workflow import agent_tasks

        final = self.folder / "06_batch/batch_meta.json"
        producer = self.folder / "04_knowledge/explanations.json"
        before = final.read_bytes(), producer.read_bytes()
        replace = agent_tasks.os.replace
        failed = False

        def fail_once(src, dst):
            nonlocal failed
            if Path(dst) == final.resolve() and not failed:
                failed = True
                raise OSError("test write failure")
            return replace(src, dst)

        with patch.object(agent_tasks.os, "replace", side_effect=fail_once):
            with self.assertRaisesRegex(OSError, "test write failure"):
                self.save({"articleSummary": "未保存"})
        self.assertEqual((final.read_bytes(), producer.read_bytes()), before)
        self.assertFalse((self.folder / manual_content.RELATIVE).exists())
        self.assertFalse((self.folder / "candidates/q-1.json").exists())

    def test_upload_preconditions_allow_validated_batch_after_manual_content_edit(self):
        from unittest.mock import patch
        from workflow import label_audit, runner

        workflow = self.folder / "workflow"
        workflow.mkdir(parents=True, exist_ok=True)
        stages = {
            name: {"status": mf.COMPLETED, "input_hashes": {}, "output_hashes": {}}
            for name in ("explanations", "assemble", "validate", "review", "upload_gate")
        }
        stages["upload"] = {"status": mf.PENDING, "input_hashes": {}, "output_hashes": {}}
        (workflow / "manifest.json").write_text(
            json.dumps({"stages": stages, "status": "completed"}, ensure_ascii=False),
            encoding="utf-8",
        )
        # 人工修改大意会将 validate 标记为 invalidated，但 history 保留了曾经 completed 的记录。
        self.save({"articleSummary": "人工修订后的文章大意"})
        current = mf.load(self.folder)
        self.assertEqual(current["stages"]["validate"]["status"], mf.INVALIDATED)

        with patch.object(runner, "unreviewed_summary", return_value={"revision": "r1"}), \
                patch.object(label_audit, "inspect_file", return_value={"passed": True}), \
                patch.object(runner, "flowchart_checks", return_value={"passed": True}):
            server._check_upload_preconditions(self.folder, None, None)

    def test_upload_preconditions_still_block_if_validate_never_passed(self):
        from unittest.mock import patch
        from workflow import label_audit, runner

        workflow = self.folder / "workflow"
        workflow.mkdir(parents=True, exist_ok=True)
        stages = {
            "explanations": {"status": mf.COMPLETED, "input_hashes": {}, "output_hashes": {}},
            "assemble": {"status": mf.COMPLETED, "input_hashes": {}, "output_hashes": {}},
            "validate": {"status": mf.FAILED, "input_hashes": {}, "output_hashes": {}},
        }
        (workflow / "manifest.json").write_text(
            json.dumps({"stages": stages, "status": "failed"}, ensure_ascii=False),
            encoding="utf-8",
        )
        # 即使从未通过体检的批次再经历人工改解析（validate: failed -> invalidated），仍必须拦截上传。
        self.save({"explanation": "人工修订解析"})
        current = mf.load(self.folder)
        self.assertEqual(current["stages"]["validate"]["status"], mf.INVALIDATED)

        with patch.object(runner, "unreviewed_summary", return_value={"revision": "r1"}), \
                patch.object(label_audit, "inspect_file", return_value={"passed": True}), \
                patch.object(runner, "flowchart_checks", return_value={"passed": True}):
            with self.assertRaisesRegex(ValueError, "批次校验未通过"):
                server._check_upload_preconditions(self.folder, None, None)


if __name__ == "__main__":
    unittest.main()
