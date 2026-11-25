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

    def test_manual_content_edit_does_not_invalidate_upstream_or_downstream(self):
        workflow = self.folder / "workflow"
        workflow.mkdir(parents=True, exist_ok=True)
        stages = {name: {"status": mf.COMPLETED, "input_hashes": {}, "output_hashes": {}}
                  for name in ("types", "explanations", "assemble", "validate", "review", "format_gate", "upload", "upload_gate")}
        stages["upload"]["status"] = mf.SKIPPED
        stages["upload_gate"]["status"] = mf.SKIPPED
        (workflow / "manifest.json").write_text(
            json.dumps({"stages": stages, "status": "completed"}, ensure_ascii=False), encoding="utf-8")
        self.save({"articleSummary": "改动后的大意"})
        current = mf.load(self.folder)
        # 进入人工审核后的修改不触发上游或下游重跑，已完成阶段保持原态。
        for name in ("types", "explanations", "assemble", "validate", "review", "format_gate"):
            self.assertEqual(current["stages"][name]["status"], mf.COMPLETED, name)
        explanations = current["stages"]["explanations"]
        self.assertEqual(explanations["output_hashes"], mf.output_hashes(
            self.folder, server.wf_contracts.STAGE_BY_ID["explanations"]))
        self.assertIn("workflow/manual-content-edits.json", " ".join(current["human_decisions"][-1]["evidence"]))
        # 仅改题面草稿也不再把 review 标成 INVALIDATED。
        self.save({}, items=[{"number": 1, "stem": "手改题干"}])
        after_draft = mf.load(self.folder)
        self.assertEqual(after_draft["stages"]["review"]["status"], mf.COMPLETED)

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
        # 人工修改大意不触发下游失效重跑，validate 保持 completed，批次仍可正常上传。
        self.save({"articleSummary": "人工修订后的文章大意"})
        current = mf.load(self.folder)
        self.assertEqual(current["stages"]["validate"]["status"], mf.COMPLETED)

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
        # 从未通过体检的批次经历人工改解析后，validate 保持 failed（不被失效掩盖），仍必须拦截上传。
        self.save({"explanation": "人工修订解析"})
        current = mf.load(self.folder)
        self.assertEqual(current["stages"]["validate"]["status"], mf.FAILED)

        with patch.object(runner, "unreviewed_summary", return_value={"revision": "r1"}), \
                patch.object(label_audit, "inspect_file", return_value={"passed": True}), \
                patch.object(runner, "flowchart_checks", return_value={"passed": True}):
            with self.assertRaisesRegex(ValueError, "批次校验未通过"):
                server._check_upload_preconditions(self.folder, None, None)

    def test_answer_draft_keeps_answers_quarantine_and_blocks_upload(self):
        from workflow import unit_anomalies, upload_payload

        unit_anomalies.record_anomalies(self.folder, "answers", {"q-1": "Answer not verbatim-bound"})
        result = self.save(None, [{"number": 1, "answer": "B"}])
        # 小题答案只落 candidates/ 草稿：正式批次答案不变，草稿路径有记录。
        self.assertIn("items", result["applied"]["skipped"])
        self.assertTrue(any(path.startswith("candidates/") for path in result["saved"]), result["saved"])
        self.assertEqual(self.batch_row()["answer"], "A")
        self.assertTrue(any("答案提取隔离仍保留" in w for w in result["warnings"]), result["warnings"])
        # 草稿未经答案来源绑定与原文校验：answers 隔离必须保留，上传仍被阻断并带原始原因。
        self.assertEqual(unit_anomalies.quarantined_unit_ids(self.folder, "answers"), {"q-1"})
        payload = upload_payload.build_payload({"items": []}, batch_dir=self.folder)
        blocked = [err for err in payload["errors"]
                   if err.get("code") == "UNIT_QUARANTINED" and err.get("unit_id") == "q-1"]
        self.assertTrue(blocked, payload["errors"])
        text = json.dumps(blocked, ensure_ascii=False)
        self.assertIn("Answer not verbatim-bound", text)
        self.assertIn("答案提取", text)

    def test_label_save_rejects_invalid_subtypes_immediately(self):
        # 合法主类 + 不属于该主类的子类：保存时即阻断，不再拖到上传才报错。
        with self.assertRaisesRegex(ValueError, "不属于"):
            server.Handler.save_units(
                self.folder, "q-1", [],
                {"type": "单项填空", "subtypes": ["首字母"]},
            )
        # 无子类的主类提交子类：阻断（若目录中存在无子类主类）。
        no_sub_type = next((t for t in server.taxonomy.types() if not server.taxonomy.subtypes(t)), None)
        if no_sub_type:
            with self.assertRaisesRegex(ValueError, "没有子类"):
                server.Handler.save_units(
                    self.folder, "q-1", [],
                    {"type": no_sub_type, "subtypes": ["四个选项"]},
                )
        # 非单词拼写主类提交多个子类：阻断。
        with self.assertRaisesRegex(ValueError, "只能单选"):
            server.Handler.save_units(
                self.folder, "q-1", [],
                {"type": "单项填空", "subtypes": ["四个选项", "三个选项"]},
            )
        # 合法主类 + 合法子类：正常保存。
        saved = server.Handler.save_units(
            self.folder, "q-1", [],
            {"type": "单项填空", "subtypes": ["四个选项"]},
        )
        self.assertTrue(saved["saved"])

    def test_install_retries_transient_windows_permission_error(self):
        from unittest.mock import patch
        from workflow import agent_tasks

        final = self.folder / "06_batch/batch_meta.json"
        real_replace = agent_tasks.os.replace
        attempts = {"count": 0}

        def flaky_replace(src, dst):
            if Path(dst) == final.resolve() and attempts["count"] < 2:
                attempts["count"] += 1
                raise PermissionError(13, "WinError 5 simulated sharing violation")
            return real_replace(src, dst)

        with patch.object(agent_tasks.os, "replace", side_effect=flaky_replace):
            res = self.save({"articleSummary": "重试后落盘成功"})
        self.assertTrue(res["applied"]["applied"])
        self.assertEqual(attempts["count"], 2)
        self.assertEqual(self.batch_row()["articleSummary"], "重试后落盘成功")

    def test_manual_content_edit_refreshes_receipts_and_prepare_upload_passes_e2e(self):
        from unittest.mock import patch
        from workflow import execution_state, handoff, upload, upload_client
        from workflow.test_upload_payload import catalog_snapshot

        folder = self.folder
        # 构造一份完整的已通过 validate / review_gate / format_gate 的真实可上传批次。
        (folder / "01_source").mkdir(parents=True, exist_ok=True)
        (folder / "01_source_ans").mkdir(parents=True, exist_ok=True)
        (folder / "03_types").mkdir(parents=True, exist_ok=True)
        (folder / "07_review").mkdir(parents=True, exist_ok=True)
        (folder / "workflow").mkdir(parents=True, exist_ok=True)

        source_txt = "一、单项填空\n1. What did Bill get?\nA. A pen\nB. A book\nC. A bag\nD. A cap\n"
        (folder / "01_source/正文.txt").write_text(source_txt, encoding="utf-8")
        (folder / "01_source/structure.confirmed.json").write_text(json.dumps({
            "version": 1,
            "units": [{
                "unit_id": "q-1", "unit_kind": "standalone", "numbers": [1],
                "source_span": {"start_line": 1, "end_line": 6},
                "source_section": {"title": "一、单项填空"},
            }],
        }, ensure_ascii=False), encoding="utf-8")
        (folder / "03_types/types.json").write_text(json.dumps({
            "items": [{
                "unit_id": "q-1", "type": "单项填空", "subtypes": ["四个选项"],
                "words": 5, "contentComplete": True, "evidence": ["L2"],
            }],
        }, ensure_ascii=False), encoding="utf-8")
        (folder / "01_source_ans/answers.json").write_text(json.dumps({
            "answers": {"1": "A"}, "missing": {}, "evidence": {"1": ["答案：A"]},
        }, ensure_ascii=False), encoding="utf-8")
        (folder / "04_knowledge/knowledge.json").write_text(json.dumps({
            "items": [{
                "unit_id": "q-1", "knowledge": ["词汇/词汇辨析/动词辨析"],
                "subquestions": [], "evidence": ["did"],
            }],
        }, ensure_ascii=False), encoding="utf-8")

        batch_item = {
            "unit_id": "q-1", "id": "q-1", "number": 1, "numbers": [1],
            "unit_kind": "standalone", "stem": "What did Bill get?",
            "material": "",
            "options": [
                {"label": "A", "text": "A pen"},
                {"label": "B", "text": "A book"},
                {"label": "C", "text": "A bag"},
                {"label": "D", "text": "A cap"},
            ],
            "answer": "A",
            "explanation": "旧解析",
            "articleSummary": "旧大意",
            "subquestions": [],
            "question_type": "单项填空",
            "difficulty": "容易",
            "knowledge": ["词汇/词汇辨析/动词辨析"],
            "source_type": "中考真题",
            "year": 2025,
            "source": "模拟期中卷",
            "location": "第一部分 第1题",
            "version": "原卷扫描版",
            "authorization": "已获授权",
            "filterMetadata": {
                "subtypes": ["四个选项"],
                "difficultyValue": 0.85,
                "categories": ["优选题"],
            },
        }
        batch_path = folder / "06_batch/batch_meta.json"
        batch_path.write_text(json.dumps({
            "batch_id": "test-e2e-batch", "name": "测试卷", "items": [batch_item],
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        old_batch_sha = mf.sha256_file(batch_path)
        old_exp_sha = mf.sha256_file(folder / "04_knowledge/explanations.json")

        (folder / "07_review/validation-report.json").write_text(json.dumps({
            "passed": True, "batch_sha256": old_batch_sha, "checks": [], "blocking_failures": [],
        }, ensure_ascii=False), encoding="utf-8")
        (folder / "07_review/review-gate.json").write_text(json.dumps({
            "passed": True, "batch_sha256": old_batch_sha, "errors": [],
        }, ensure_ascii=False), encoding="utf-8")
        (folder / "07_review/upload-format.json").write_text(json.dumps({
            "passed": True, "final_sha256": old_batch_sha, "errors": [],
        }, ensure_ascii=False), encoding="utf-8")

        stages = {
            sid: {"status": mf.COMPLETED, "input_hashes": {}, "output_hashes": {}}
            for sid in execution_state.LOCAL_STAGES
        }
        stages["explanations"]["output_hashes"] = {"04_knowledge/explanations.json": old_exp_sha}
        stages["assemble"]["output_hashes"] = {"06_batch/batch_meta.json": old_batch_sha}
        stages["validate"]["output_hashes"] = {
            "07_review/validation-report.json": mf.sha256_file(folder / "07_review/validation-report.json"),
        }
        stages["review_gate"]["output_hashes"] = {
            "07_review/review-gate.json": mf.sha256_file(folder / "07_review/review-gate.json"),
        }
        stages["format_gate"]["output_hashes"] = {
            "07_review/upload-format.json": mf.sha256_file(folder / "07_review/upload-format.json"),
        }
        stages["upload_gate"] = {"status": mf.SKIPPED, "input_hashes": {}, "output_hashes": {}}
        stages["upload"] = {"status": mf.SKIPPED, "input_hashes": {}, "output_hashes": {}}
        stages["publish_gate"] = {"status": mf.SKIPPED, "input_hashes": {}, "output_hashes": {}}
        (folder / "workflow/manifest.json").write_text(json.dumps({
            "workflow_id": "test-e2e-batch",
            "status": "pending_confirmation",
            "stages": stages,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        # 在不调用任何外部校验脚本的情况下模拟确定性 validate + format_gate 复验通过。
        from workflow import format_gate, runner

        def fake_do_validate(batch_dir, manifest, *, lock_held=False, refresh_only=False):
            new_sha = mf.sha256_file(Path(batch_dir) / "06_batch/batch_meta.json")
            (Path(batch_dir) / "07_review/validation-report.json").write_text(json.dumps({
                "passed": True, "batch_sha256": new_sha, "checks": [], "blocking_failures": [],
            }, ensure_ascii=False), encoding="utf-8")
            cur = mf.load(batch_dir)
            spec = server.wf_contracts.STAGE_BY_ID["validate"]
            mf.touch_stage(cur, "validate", mf.COMPLETED, output_hashes=mf.output_hashes(batch_dir, spec))
            mf.save(batch_dir, cur)
            return 0

        def fake_format_run(batch_dir, write_report=True):
            new_sha = mf.sha256_file(Path(batch_dir) / "06_batch/batch_meta.json")
            rep = {"passed": True, "final_sha256": new_sha, "errors": []}
            if write_report:
                format_gate.write_report_file(batch_dir, rep)
            return rep

        with patch.object(runner, "do_validate", side_effect=fake_do_validate) as mocked_val, \
                patch.object(format_gate, "run", side_effect=fake_format_run) as mocked_fg:
            res = self.save({"explanation": "人工修订后的精准解析", "articleSummary": "人工修订大意"})

        self.assertTrue(res["applied"]["applied"])
        mocked_val.assert_called_once()
        mocked_fg.assert_called_once()

        new_batch_sha = mf.sha256_file(batch_path)
        self.assertNotEqual(new_batch_sha, old_batch_sha)

        # 三份下游回执哈希全部与新 batch_meta.json 对齐，handoff 严格校验全部通过。
        fresh_manifest = mf.load(folder)
        self.assertEqual(
            fresh_manifest["stages"]["assemble"]["output_hashes"]["06_batch/batch_meta.json"],
            new_batch_sha,
        )
        for stage_id in ("validate", "review_gate", "format_gate"):
            inspect = handoff.inspect_stage(folder, fresh_manifest, stage_id)
            self.assertTrue(inspect["passed"], f"{stage_id}: {inspect}")

        # execution_state.local_complete 保持 True（supervisor 不会误判过期重跑）。
        self.assertTrue(execution_state.local_complete(folder, fresh_manifest))

        # 端到端 prepare-upload：冻结产物通过并包含人工修订后的解析和大意。
        cat = catalog_snapshot()
        with patch("workflow.upload_content.inspect_content", return_value={"passed": True, "errors": [], "warnings": [], "source_sha256": "s"}):
            prep = upload.prepare(
                folder,
                catalog=cat,
                config=upload_client.load_config(),
                check_media=False,
                ensure_catalog=False,
            )
        self.assertTrue(prep["passed"], prep)
        frozen_payload = json.loads((folder / prep["payload_file"]).read_text(encoding="utf-8"))
        self.assertEqual(frozen_payload["items"][0]["explanation"], "人工修订后的精准解析")
        self.assertEqual(frozen_payload["items"][0]["articleSummary"], "人工修订大意")


if __name__ == "__main__":
    unittest.main()
