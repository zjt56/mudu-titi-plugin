# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
from pathlib import Path

from workflow import ai_supplement, upload_content


class AiSupplementTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.batch = Path(self._tmp.name).resolve()
        (self.batch / "01_source").mkdir()
        (self.batch / "06_batch").mkdir()
        (self.batch / "01_source/正文.txt").write_text("Unit 1\n", encoding="utf-8")
        (self.batch / "01_source/附录.txt").write_text("参考答案\n1.A\n", encoding="utf-8")

        long_material = " ".join(["word"] * 60) + "."
        self.meta = {
            "items": [
                {
                    "unit_id": "u-reading",
                    "unit_kind": "group",
                    "question_type": "阅读理解",
                    "material": long_material,
                    "articleSummary": "",
                    "numbers": [1],
                    "subquestions": [
                        {"number": 1, "stem": "What is it?", "options": [{"key": "A", "text": "Yes"}], "answer": "A", "explanation": ""}
                    ],
                },
                {
                    "unit_id": "u-listen",
                    "unit_kind": "standalone",
                    "question_type": "听力短对话理解",
                    "stem": "1.What does the boy want?",
                    "answer": "A",
                    "explanation": "",
                },
                {
                    "unit_id": "u-short",
                    "unit_kind": "standalone",
                    "question_type": "单项填空",
                    "stem": "2.She ___ a student.",
                    "answer": "is",
                    "explanation": "无",
                },
            ]
        }
        (self.batch / "06_batch/batch_meta.json").write_text(
            json.dumps(self.meta, ensure_ascii=False), encoding="utf-8"
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_eligible_targets_filter_types_and_listening(self):
        sum_targets = ai_supplement.eligible_targets(self.batch, "articleSummary")
        self.assertEqual([t["key"] for t in sum_targets], ["u-reading"])

        exp_targets = ai_supplement.eligible_targets(self.batch, "explanation")
        self.assertEqual([t["key"] for t in exp_targets], ["u-reading/1", "u-short"])

    def test_supplement_marks_ai_and_passes_strict_inspect_content(self):
        def fake_invoke(prompt, run_dir):
            if "文章大意" in prompt:
                return {"result": {"items": [{"key": "u-reading", "text": "文章大意：本文主要讲述了词汇学习。"}]}}
            return {
                "result": {
                    "items": [
                        {"key": "u-reading/1", "text": "【解析】根据文章第一句可知选A。"},
                        {"key": "u-short", "text": "解析：主语是第三人称单数，故填is。"},
                    ]
                }
            }

        res_sum = ai_supplement.supplement(self.batch, "articleSummary", invoke_fn=fake_invoke)
        self.assertEqual(res_sum["applied"], 1)
        res_exp = ai_supplement.supplement(self.batch, "explanation", invoke_fn=fake_invoke)
        self.assertEqual(res_exp["applied"], 2)

        updated = json.loads((self.batch / "06_batch/batch_meta.json").read_text(encoding="utf-8"))
        reading = updated["items"][0]
        short = updated["items"][2]
        self.assertEqual(reading["articleSummary"], "本文主要讲述了词汇学习。" + upload_content.AI_MARK)
        self.assertEqual(reading["subquestions"][0]["explanation"], "根据文章第一句可知选A。" + upload_content.AI_MARK)
        self.assertEqual(short["explanation"], "主语是第三人称单数，故填is。" + upload_content.AI_MARK)

        report = upload_content.inspect_content(self.batch, updated, strict=True)
        self.assertTrue(report["passed"], report)

    def test_clamp_50_chars_and_regenerate_overwrites_ai_only(self):
        long_text = "本文主要讲述了主人公通过坚持不懈的努力学习英语词汇与阅读理解技巧，最终在期末考试中取得优异成绩并收获成长的故事。"
        self.assertGreater(len(long_text), 50)

        def invoke_v1(prompt, run_dir):
            if "文章大意" in prompt:
                return {"result": {"items": [{"key": "u-reading", "text": long_text}]}}
            return {"result": {"items": [
                {"key": "u-reading/1", "text": long_text},
                {"key": "u-short", "text": "主语单三填is。"},
            ]}}

        ai_supplement.supplement(self.batch, "articleSummary", invoke_fn=invoke_v1)
        ai_supplement.supplement(self.batch, "explanation", invoke_fn=invoke_v1)

        data_v1 = json.loads((self.batch / "06_batch/batch_meta.json").read_text(encoding="utf-8"))
        body_sum = data_v1["items"][0]["articleSummary"][:-len(upload_content.AI_MARK)]
        body_exp = data_v1["items"][0]["subquestions"][0]["explanation"][:-len(upload_content.AI_MARK)]
        self.assertLessEqual(len(body_sum), 50)
        self.assertLessEqual(len(body_exp), 50)

        # Clicking again regenerates and overwrites previous AI-generated items
        def invoke_v2(prompt, run_dir):
            if "文章大意" in prompt:
                return {"result": {"items": [{"key": "u-reading", "text": "重新生成的简短大意。"}]}}
            return {"result": {"items": [
                {"key": "u-reading/1", "text": "重新生成的简短解析。"},
                {"key": "u-short", "text": "第三人称单数用is。"},
            ]}}

        res2 = ai_supplement.supplement(self.batch, "articleSummary", invoke_fn=invoke_v2)
        self.assertEqual(res2["applied"], 1)
        ai_supplement.supplement(self.batch, "explanation", invoke_fn=invoke_v2)

        data_v2 = json.loads((self.batch / "06_batch/batch_meta.json").read_text(encoding="utf-8"))
        self.assertEqual(data_v2["items"][0]["articleSummary"], "重新生成的简短大意。" + upload_content.AI_MARK)
        self.assertEqual(data_v2["items"][0]["subquestions"][0]["explanation"], "重新生成的简短解析。" + upload_content.AI_MARK)
        self.assertEqual(data_v2["items"][2]["explanation"], "第三人称单数用is。" + upload_content.AI_MARK)
        prog = ai_supplement.read_progress(self.batch)
        self.assertEqual(prog["status"], "done")
        self.assertEqual(prog["completed"], prog["total"])

    def test_save_content_edit_updates_summary_answers_and_sub_explanations(self):
        res = ai_supplement.save_content_edit(self.batch, "u-reading", {
            "articleSummary": "人工编辑后的大意。",
            "subquestions": [{"number": 1, "answer": "B", "explanation": "人工编辑后的解析。"}],
        })
        self.assertTrue(res["applied"])
        updated = json.loads((self.batch / "06_batch/batch_meta.json").read_text(encoding="utf-8"))
        reading = updated["items"][0]
        self.assertEqual(reading["articleSummary"], "人工编辑后的大意。")
        self.assertEqual(reading["subquestions"][0]["answer"], "B")
        self.assertEqual(reading["subquestions"][0]["explanation"], "人工编辑后的解析。")
        report = upload_content.inspect_content(self.batch, updated, strict=True)
        self.assertTrue(report["passed"], report)

    def test_multi_round_ai_supplement_edit_and_upload_not_blocked(self):
        from workbench import taxonomy
        from workflow import test_upload_payload, upload, upload_payload

        k_idx = taxonomy.knowledge_index()[0]
        read_genre = "语篇体裁/记叙文/故事"
        read_theme = "主题/人与自我/生活与学习/学校生活"
        read_sub = "设题角度/阅读/细节理解"
        short_kp = "词汇/词汇辨析/动词辨析"

        # Inject ** markers into stems/materials/options and complete upload-ready metadata
        data = json.loads((self.batch / "06_batch/batch_meta.json").read_text(encoding="utf-8"))
        data.update({"batch_id": "b1", "name": "b1", "upload_mode": "source", "upload_target": "同步练习"})
        data["items"][0].update({
            "id": "Q-read",
            "number": 1,
            "numbers": [1, 2, 3],
            "stem": "**Ⅰ. 阅读理解**",
            "material": "**" + data["items"][0]["material"] + "**",
            "filterMetadata": {"subtypes": ["选择3题"], "difficultyValue": 0.65},
            "knowledge": [read_genre, read_theme],
            "knowledgeIds": [k_idx[read_genre]["id"], k_idx[read_theme]["id"]],
            "subquestions": [
                {
                    "number": n,
                    "stem": f"**{n}. What is it?**",
                    "options": [
                        {"label": "A", "text": "**Yes**"},
                        {"label": "B", "text": "**No**"},
                    ],
                    "answer": "A",
                    "explanation": "",
                    "knowledge": [read_sub],
                    "knowledgeIds": [k_idx[read_sub]["id"]],
                }
                for n in (1, 2, 3)
            ],
            "difficulty": "适中",
            "source_type": "同步练习",
            "year": 2026,
            "source": "2026 同步练习原卷",
            "location": "第一部分 第1题",
            "version": "原卷扫描版",
            "authorization": "已获授权",
        })
        # Replace u-listen with a valid reading/choice item or valid listening item with audio/options
        data["items"][1].update({
            "id": "Q-listen",
            "number": 4,
            "numbers": [4],
            "question_type": "单项填空",
            "filterMetadata": {"subtypes": ["四个选项"], "difficultyValue": 0.65},
            "stem": "**4. What does the boy want?**",
            "options": [
                {"label": "A", "text": "**go**"},
                {"label": "B", "text": "see"},
                {"label": "C", "text": "read"},
                {"label": "D", "text": "write"},
            ],
            "knowledge": [short_kp],
            "knowledgeIds": [k_idx[short_kp]["id"]],
            "difficulty": "适中",
            "source_type": "同步练习",
            "year": 2026,
            "source": "2026 同步练习原卷",
            "location": "第二部分 第4题",
            "version": "原卷扫描版",
            "authorization": "已获授权",
        })
        data["items"][2].update({
            "id": "Q-short",
            "number": 5,
            "numbers": [5],
            "filterMetadata": {"subtypes": ["四个选项"], "difficultyValue": 0.65},
            "stem": "**5. She ___ a student.**",
            "options": [
                {"label": "A", "text": "**is**"},
                {"label": "B", "text": "am"},
                {"label": "C", "text": "are"},
                {"label": "D", "text": "be"},
            ],
            "knowledge": [short_kp],
            "knowledgeIds": [k_idx[short_kp]["id"]],
            "difficulty": "适中",
            "source_type": "同步练习",
            "year": 2026,
            "source": "2026 同步练习原卷",
            "location": "第三部分 第5题",
            "version": "原卷扫描版",
            "authorization": "已获授权",
        })
        (self.batch / "06_batch/batch_meta.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        catalog = test_upload_payload.catalog_snapshot()

        # Round 1: AI generate articleSummary + explanation
        ai_supplement.supplement(
            self.batch, "articleSummary",
            invoke_fn=lambda p, d: {"result": {"items": [{"key": "u-reading", "text": "第一轮大意。"}]}},
        )
        ai_supplement.supplement(
            self.batch, "explanation",
            invoke_fn=lambda p, d: {"result": {"items": [
                {"key": "u-reading/1", "text": "第一轮阅读解析1。"},
                {"key": "u-reading/2", "text": "第一轮阅读解析2。"},
                {"key": "u-reading/3", "text": "第一轮阅读解析3。"},
                {"key": "u-listen", "text": "第一轮第4题解析。"},
                {"key": "u-short", "text": "第一轮单选解析。"},
            ]}},
        )
        r1 = json.loads((self.batch / "06_batch/batch_meta.json").read_text(encoding="utf-8"))
        self.assertTrue(upload_content.inspect_content(self.batch, r1, strict=True)["passed"])
        p1 = upload_payload.build_payload(r1, catalog, batch_id="b1", name="b1", batch_dir=self.batch)
        self.assertTrue(p1["passed"], p1["errors"])
        self.assertNotIn("**", p1["payload"]["items"][0]["stem"])
        self.assertNotIn("**", p1["payload"]["items"][0]["material"])
        self.assertNotIn("**", p1["payload"]["items"][2]["stem"])

        # Round 2: Regenerate AI (overwrite) + manual content & label edit
        ai_supplement.supplement(
            self.batch, "articleSummary",
            invoke_fn=lambda p, d: {"result": {"items": [{"key": "u-reading", "text": "第二轮重写大意。"}]}},
        )
        ai_supplement.save_content_edit(self.batch, "u-short", {
            "answer": "is",
            "explanation": "人工复核修改后的解析。",
        })
        ai_supplement.save_content_edit(self.batch, "u-reading", {
            "subquestions": [{"number": 1, "answer": "B", "explanation": "人工微调阅读解析。"}],
        })
        r2 = json.loads((self.batch / "06_batch/batch_meta.json").read_text(encoding="utf-8"))
        r2["items"][2]["difficulty"] = "容易"
        r2["items"][2]["filterMetadata"]["difficultyValue"] = 0.85
        r2["items"][2]["year"] = 2025
        (self.batch / "06_batch/batch_meta.json").write_text(json.dumps(r2, ensure_ascii=False), encoding="utf-8")

        self.assertEqual(r2["items"][0]["articleSummary"], "第二轮重写大意。" + upload_content.AI_MARK)
        self.assertEqual(r2["items"][0]["subquestions"][0]["answer"], "B")
        self.assertEqual(r2["items"][2]["answer"], "is")
        self.assertEqual(r2["items"][2]["explanation"], "人工复核修改后的解析。")

        # Round 3: Verify strict content inspection, payload build, and upload.prepare are NOT blocked
        strict_res = upload_content.inspect_content(self.batch, r2, strict=True)
        self.assertTrue(strict_res["passed"], strict_res)
        p2 = upload_payload.build_payload(r2, catalog, batch_id="b1", name="b1", batch_dir=self.batch)
        self.assertTrue(p2["passed"], p2["errors"])
        from workflow import manifest as mf
        (self.batch / "attachments").mkdir(exist_ok=True)
        doc = self.batch / "attachments" / "source.docx"
        doc.write_bytes(b"synthetic docx")
        state = mf.new_manifest("b1", self.batch, {"source": {"path": str(doc), "exists": True}}, {})
        mf.save(self.batch, state)
        prep = upload.prepare(self.batch, catalog=catalog, check_media=False)
        self.assertTrue(prep["passed"], prep)

    def test_save_content_edit_two_sessions_preserve_non_conflicting_and_reject_stale(self):
        # Session A edits subquestion 1 explanation with expected baseline ""
        res_a = ai_supplement.save_content_edit(self.batch, "u-reading", {
            "subquestions": [
                {"number": 1, "explanation": "会话A的新解析。", "expected": {"explanation": ""}}
            ],
        })
        self.assertTrue(res_a["applied"])

        # Session B (opened from initial snapshot) only edits articleSummary
        res_b = ai_supplement.save_content_edit(self.batch, "u-reading", {
            "articleSummary": "会话B的新大意。",
            "expected": {"articleSummary": ""},
        })
        self.assertTrue(res_b["applied"])

        updated = json.loads((self.batch / "06_batch/batch_meta.json").read_text(encoding="utf-8"))
        reading = updated["items"][0]
        self.assertEqual(reading["articleSummary"], "会话B的新大意。")
        self.assertEqual(reading["subquestions"][0]["explanation"], "会话A的新解析。")

        # Session C (stale snapshot) tries to overwrite subquestion 1 explanation -> conflict!
        with self.assertRaises(ai_supplement.ContentEditConflictError) as ctx:
            ai_supplement.save_content_edit(self.batch, "u-reading", {
                "subquestions": [
                    {"number": 1, "explanation": "会话C的旧快照解析。", "expected": {"explanation": ""}}
                ],
            })
        self.assertTrue(ctx.exception.conflicts)
        self.assertEqual(ctx.exception.conflicts[0]["field"], "explanation")
        self.assertEqual(ctx.exception.conflicts[0]["number"], "1")
        after_conflict = json.loads((self.batch / "06_batch/batch_meta.json").read_text(encoding="utf-8"))
        self.assertEqual(after_conflict["items"][0]["subquestions"][0]["explanation"], "会话A的新解析。")

    def test_eligible_targets_scoped_v1_checks_per_unit_identity_not_global_number(self):
        (self.batch / "01_source/structure.confirmed.json").write_text(
            json.dumps({
                "units": [
                    {
                        "unit_id": "sec1-u1",
                        "identity_version": "scoped-v1",
                        "unit_kind": "standalone",
                        "numbers": [1],
                        "question_ids": {"1": "sec1-u1#1"},
                    },
                    {
                        "unit_id": "sec2-u1",
                        "identity_version": "scoped-v1",
                        "unit_kind": "standalone",
                        "numbers": [1],
                        "question_ids": {"1": "sec2-u1#1"},
                    },
                ]
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.batch / "06_batch/batch_meta.json").write_text(
            json.dumps({
                "items": [
                    {
                        "unit_id": "sec1-u1",
                        "identity_version": "scoped-v1",
                        "unit_kind": "standalone",
                        "question_type": "单项填空",
                        "number": 1,
                        "question_id": "sec1-u1#1",
                        "stem": "1. A?",
                        "answer": "A",
                        "explanation": "",
                    },
                    {
                        "unit_id": "sec2-u1",
                        "identity_version": "scoped-v1",
                        "unit_kind": "standalone",
                        "question_type": "单项填空",
                        "number": 1,
                        "question_id": "sec2-u1#1",
                        "stem": "1. B?",
                        "answer": "B",
                        "explanation": "",
                    },
                ]
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        orig_collect = upload_content.collect_source_index
        upload_content.collect_source_index = lambda _b: ({"sec1-u1#1": "原卷第一大题第1题解析。"}, [])
        try:
            targets = ai_supplement.eligible_targets(self.batch, "explanation")
        finally:
            upload_content.collect_source_index = orig_collect
        self.assertEqual([t["key"] for t in targets], ["sec2-u1"])

    def test_prompt_emits_agent_header_and_resolves_custom_or_default_model(self):
        import os
        targets = ai_supplement.eligible_targets(self.batch, "articleSummary")
        p_sum = ai_supplement._build_prompt("articleSummary", targets)
        self.assertIn("Stage: ai_supplement\nAgent: ai_summary\n", p_sum)

        exp_targets = ai_supplement.eligible_targets(self.batch, "explanation")
        p_exp = ai_supplement._build_prompt("explanation", exp_targets)
        self.assertIn("Stage: ai_supplement\nAgent: ai_explanation\n", p_exp)

        router_path = self.batch / "router.json"
        router_path.write_text(json.dumps({
            "default": {"provider": "deepseek-official", "model": "deepseek-chat"},
            "roles": {
                "ai_summary": {"provider": "opencode-go", "model": "fast-summary-model"},
            },
        }), encoding="utf-8")
        old_env = os.environ.get("WF_ROUTER_FILE")
        os.environ["WF_ROUTER_FILE"] = str(router_path)
        try:
            sum_info = ai_supplement.resolve_model_info("articleSummary")
            exp_info = ai_supplement.resolve_model_info("explanation")
        finally:
            if old_env is None:
                os.environ.pop("WF_ROUTER_FILE", None)
            else:
                os.environ["WF_ROUTER_FILE"] = old_env

        self.assertEqual(sum_info["model"], "fast-summary-model")
        self.assertEqual(sum_info["provider"], "opencode-go")
        self.assertEqual(sum_info["model_source"], "custom")
        self.assertEqual(exp_info["model"], "deepseek-chat")
        self.assertEqual(exp_info["provider"], "deepseek-official")
        self.assertEqual(exp_info["model_source"], "default")

    def test_start_background_runs_independently_while_pipeline_holds_agent_lock(self):
        import time
        from workflow import local_agent, supervisor

        # Simulate main upload pipeline holding global _agent_lock AND auto lock on this batch
        with local_agent._agent_lock(0), supervisor._try_file_lock(supervisor._auto_lock_path(self.batch)):
            initial = ai_supplement.start_background(
                self.batch,
                "articleSummary",
                invoke_fn=lambda p, d: {"result": {"items": [{"key": "u-reading", "text": "独立任务大意。"}]}},
            )
            self.assertEqual(initial["status"], "running")
            self.assertIn("job_id", initial)

            deadline = time.monotonic() + 5.0
            prog = initial
            while time.monotonic() < deadline:
                prog = ai_supplement.read_progress(self.batch)
                if prog["status"] != "running":
                    break
                time.sleep(0.05)

        self.assertEqual(prog["status"], "done", prog)
        self.assertEqual(prog["applied"], 1)
        updated = json.loads((self.batch / "06_batch/batch_meta.json").read_text(encoding="utf-8"))
        self.assertEqual(updated["items"][0]["articleSummary"], "独立任务大意。" + upload_content.AI_MARK)

    def test_supplement_preserves_manual_edit_saved_during_generation(self):
        def fake_invoke(prompt, run_dir):
            # User manually edits articleSummary while AI is still generating
            ai_supplement.save_content_edit(self.batch, "u-reading", {
                "articleSummary": "人工刚写好的正式大意。",
            })
            return {"result": {"items": [{"key": "u-reading", "text": "AI晚到的大意。"}]}}

        res = ai_supplement.supplement(self.batch, "articleSummary", invoke_fn=fake_invoke)
        self.assertEqual(res["applied"], 0)
        updated = json.loads((self.batch / "06_batch/batch_meta.json").read_text(encoding="utf-8"))
        self.assertEqual(updated["items"][0]["articleSummary"], "人工刚写好的正式大意。")

    def test_read_progress_recovers_stale_running_status(self):
        prog_path = self.batch / ai_supplement.PROGRESS_REL
        prog_path.parent.mkdir(parents=True, exist_ok=True)
        prog_path.write_text(json.dumps({
            "status": "running",
            "mode": "articleSummary",
            "total": 1,
            "completed": 0,
            "applied": 0,
            "pid": 99999999,
            "message": "正在准备生成（共 1 项）…",
        }, ensure_ascii=False), encoding="utf-8")
        prog = ai_supplement.read_progress(self.batch)
        self.assertEqual(prog["status"], "error")
        self.assertIn("中断", prog["message"])

    def test_duplicate_start_background_does_not_deadlock(self):
        import threading
        import time

        started = threading.Event()
        release = threading.Event()

        def slow_invoke(prompt, run_dir):
            started.set()
            release.wait(timeout=5.0)
            return {"result": {"items": [{"key": "u-reading", "text": "防重复大意。"}]}}

        first = ai_supplement.start_background(self.batch, "articleSummary", invoke_fn=slow_invoke)
        self.assertEqual(first["status"], "running")
        self.assertTrue(started.wait(timeout=3.0))

        # Second request on the same batch must return immediately without deadlocking _JOBS_LOCK
        second = ai_supplement.start_background(self.batch, "articleSummary", invoke_fn=slow_invoke)
        self.assertEqual(second["status"], "running")
        self.assertEqual(second["job_id"], first["job_id"])

        release.set()
        deadline = time.monotonic() + 5.0
        prog = second
        while time.monotonic() < deadline:
            prog = ai_supplement.read_progress(self.batch)
            if prog["status"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(prog["status"], "done", prog)
        self.assertEqual(prog["applied"], 1)

    def test_answer_change_during_generation_skips_changed_target_only(self):
        def fake_invoke(prompt, run_dir):
            # While AI generates explanation for u-short (answer='is'), user changes answer to 'was'
            ai_supplement.save_content_edit(self.batch, "u-short", {"answer": "was"})
            return {
                "result": {
                    "items": [
                        {"key": "u-reading/1", "text": "根据首句可知选A。"},
                        {"key": "u-short", "text": "主语单三一般现在时填is。"},
                    ]
                }
            }

        res = ai_supplement.supplement(self.batch, "explanation", invoke_fn=fake_invoke)
        self.assertEqual(res["applied"], 1)
        self.assertEqual(res["skipped_changed"], 1)
        updated = json.loads((self.batch / "06_batch/batch_meta.json").read_text(encoding="utf-8"))
        # Unchanged target u-reading/1 is committed
        self.assertEqual(updated["items"][0]["subquestions"][0]["explanation"], "根据首句可知选A。" + upload_content.AI_MARK)
        # Changed target u-short keeps its new answer 'was' and refuses stale 'is' explanation
        self.assertEqual(updated["items"][2]["answer"], "was")
        self.assertEqual(updated["items"][2]["explanation"], "无")

    def test_deleted_batch_is_not_recreated_by_background_worker_and_work_in_flight(self):
        import shutil
        import threading
        import time
        from workbench import server

        started = threading.Event()
        release = threading.Event()

        def slow_invoke(prompt, run_dir):
            started.set()
            release.wait(timeout=5.0)
            return {"result": {"items": [{"key": "u-reading", "text": "删除后的幽灵大意。"}]}}

        ai_supplement.start_background(self.batch, "articleSummary", invoke_fn=slow_invoke)
        self.assertTrue(started.wait(timeout=3.0))
        self.assertTrue(ai_supplement.is_running(self.batch))
        self.assertTrue(ai_supplement.has_active_jobs())
        self.assertTrue(server.work_in_flight())

        # Force-delete the batch directory mid-flight; worker must NOT recreate it on exit
        shutil.rmtree(self.batch)
        self.assertFalse(self.batch.exists())
        release.set()

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and ai_supplement.has_active_jobs():
            time.sleep(0.05)

        self.assertFalse(self.batch.exists(), "background worker must not recreate deleted batch dir")
        self.assertFalse(ai_supplement.has_active_jobs())

    def test_busy_run_lock_caches_pending_candidates_and_reuses_without_reinvoking(self):
        from workflow import supervisor

        run_lock = self.batch / "workflow" / ".run.lock"
        run_lock.parent.mkdir(parents=True, exist_ok=True)
        run_lock.touch()

        calls = []
        def first_invoke(prompt, run_dir):
            calls.append(1)
            return {"result": {"items": [{"key": "u-reading", "text": "暂存的珍贵大意。"}]}}

        with supervisor._try_file_lock(run_lock):
            with self.assertRaises(ValueError) as ctx:
                ai_supplement.supplement(self.batch, "articleSummary", invoke_fn=first_invoke)
        self.assertIn("已暂存", str(ctx.exception))
        self.assertEqual(len(calls), 1)
        prog = ai_supplement.read_progress(self.batch)
        self.assertEqual(prog["completed"], 1)
        self.assertEqual(prog["pending_saved"], 1)
        self.assertTrue((self.batch / ai_supplement.PENDING_REL).is_file())

        # Now .run.lock is free: retrying commits cached candidate with ZERO model calls
        def should_not_be_called(prompt, run_dir):
            raise AssertionError("model should not be re-invoked when bound pending candidate matches")

        res = ai_supplement.supplement(self.batch, "articleSummary", invoke_fn=should_not_be_called)
        self.assertEqual(res["applied"], 1)
        self.assertFalse((self.batch / ai_supplement.PENDING_REL).exists())
        updated = json.loads((self.batch / "06_batch/batch_meta.json").read_text(encoding="utf-8"))
        self.assertEqual(updated["items"][0]["articleSummary"], "暂存的珍贵大意。" + upload_content.AI_MARK)

    def test_validate_runs_outside_content_write_lock_and_pid_uses_operations(self):
        import os
        from workflow import manifest as mf, operations, runner

        self.assertIs(ai_supplement._pid_alive(os.getpid()), True)
        orig_op_alive = operations._pid_alive
        seen_pids = []
        operations._pid_alive = lambda pid: (seen_pids.append(pid) or False)
        try:
            self.assertFalse(ai_supplement._pid_alive(123456))
            self.assertEqual(seen_pids, [123456])
        finally:
            operations._pid_alive = orig_op_alive

        # Verify runner.do_validate executes AFTER content_write_lock is released
        (self.batch / "workflow").mkdir(exist_ok=True)
        (self.batch / "attachments").mkdir(exist_ok=True)
        doc = self.batch / "attachments" / "s.docx"
        doc.write_bytes(b"x")
        mf.save(self.batch, mf.new_manifest("b1", self.batch, {"source": {"path": str(doc), "exists": True}}, {}))

        lock_free_during_validate = []
        orig_validate = runner.do_validate
        def fake_validate(batch_dir, current, *, lock_held=False, refresh_only=False):
            with ai_supplement.content_write_lock(batch_dir, timeout=0.0):
                lock_free_during_validate.append(True)
            return 0
        runner.do_validate = fake_validate
        try:
            res = ai_supplement.supplement(
                self.batch, "articleSummary",
                invoke_fn=lambda p, d: {"result": {"items": [{"key": "u-reading", "text": "无锁体检大意。"}]}},
            )
        finally:
            runner.do_validate = orig_validate
        self.assertEqual(res["applied"], 1)
        self.assertEqual(lock_free_during_validate, [True])

    def test_listening_script_targets_dedup_prompt_format_and_strict_inspect(self):
        (self.batch / "02_audio").mkdir(exist_ok=True)
        (self.batch / "02_audio/full-transcript.json").write_text(
            json.dumps({
                "segments": [
                    {"start": 0.0, "end": 4.0, "text": "第一节 听下面一段短对话。"},
                    {"start": 4.0, "end": 12.0, "text": "Good morning! Where is the library? It is next to the science building."},
                    {"start": 12.0, "end": 15.0, "text": "下面请听第二遍。"},
                    {"start": 15.0, "end": 23.0, "text": "Good morning! Where is the library? It is next to the science building."},
                    {"start": 30.0, "end": 45.0, "text": "Hi Tom, are you coming to the concert tonight? Yes, I will meet you at seven thirty."},
                    {"start": 45.0, "end": 60.0, "text": "Hi Tom, are you coming to the concert tonight? Yes, I will meet you at seven thirty."},
                    {"start": 70.0, "end": 80.0, "text": "Hello, how are you? Fine, thank you."},
                ]
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.batch / "02_audio/segment-evidence.json").write_text(
            json.dumps({
                "items": [
                    {"unit_id": "u-listen", "start": 0.0, "end": 25.0, "verified": True},
                    {"unit_id": "u-listen-group", "start": 28.0, "end": 62.0, "verified": False},
                    {"unit_id": "u-listen-orig", "start": 68.0, "end": 82.0, "verified": True},
                ]
            }, ensure_ascii=False),
            encoding="utf-8",
        )

        data = json.loads((self.batch / "06_batch/batch_meta.json").read_text(encoding="utf-8"))
        data["items"].extend([
            {
                "unit_id": "u-listen-group",
                "unit_kind": "group",
                "question_type": "听力长对话理解",
                "numbers": [6, 7],
                "explanation": "原题解析说明。",
                "subquestions": [
                    {"number": 6, "stem": "6. Where will they go?", "options": [{"key": "A", "text": "Concert"}], "answer": "A", "explanation": ""},
                    {"number": 7, "stem": "7. When will they meet?", "options": [{"key": "B", "text": "7:30"}], "answer": "B", "explanation": ""},
                ],
            },
            {
                "unit_id": "u-listen-orig",
                "unit_kind": "standalone",
                "question_type": "听力短对话理解",
                "stem": "8. How is the speaker?",
                "answer": "A",
                "explanation": "",
            },
        ])
        (self.batch / "06_batch/batch_meta.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
        ai_supplement.save_content_edit(
            self.batch,
            "u-listen-orig",
            {"explanation": "听力录音材料：\nM: Hello, how are you?\nW: Fine, thank you."},
        )

        targets = ai_supplement.eligible_targets(self.batch, "listeningScript")
        self.assertEqual([t["key"] for t in targets], ["u-listen", "u-listen-group"])

        prompt = ai_supplement._build_prompt("listeningScript", targets)
        self.assertIn("Agent: ai_listening_script", prompt)
        self.assertIn("读两遍去重", prompt)
        self.assertIn("[4.0s-12.0s]", prompt)

        long_dialogue_1 = (
            "M: Good morning! Excuse me, where is the school library?\n"
            "W: It is right next to the science building on the second floor."
        )
        long_dialogue_2 = (
            "W: Hi Tom, are you coming to the school concert tonight?\n"
            "M: Yes, of course! I will meet you at the front gate at seven thirty."
        )
        self.assertGreater(len(long_dialogue_1), 80)

        def fake_invoke(p, run_dir):
            return {
                "result": {
                    "items": [
                        {"key": "u-listen", "text": "听力录音材料：\n" + long_dialogue_1},
                        {"key": "u-listen-group", "text": long_dialogue_2 + "（AI转录）"},
                    ]
                }
            }

        res = ai_supplement.supplement(self.batch, "listeningScript", invoke_fn=fake_invoke)
        self.assertEqual(res["applied"], 2)
        self.assertEqual(res["keys"], ["u-listen", "u-listen-group"])

        updated = json.loads((self.batch / "06_batch/batch_meta.json").read_text(encoding="utf-8"))
        by_uid = {it["unit_id"]: it for it in updated["items"]}

        # Full multi-line dialogue preserved (not clamped to 50 chars) + marked as AI转录
        self.assertEqual(
            by_uid["u-listen"]["explanation"],
            f"听力录音材料：\n{long_dialogue_1}\n{upload_content.AI_TRANSCRIPT_MARK}",
        )
        # Existing non-script prefix preserved before 听力录音材料：
        self.assertEqual(
            by_uid["u-listen-group"]["explanation"],
            f"原题解析说明。\n\n听力录音材料：\n{long_dialogue_2}\n{upload_content.AI_TRANSCRIPT_MARK}",
        )
        # Original listening script untouched
        self.assertEqual(
            by_uid["u-listen-orig"]["explanation"],
            "听力录音材料：\nM: Hello, how are you?\nW: Fine, thank you.",
        )

        # Passes strict upload_content inspection because recorded in manual-content-edits.json
        report = upload_content.inspect_content(self.batch, updated, strict=True)
        self.assertTrue(report["passed"], report)

        # Re-running listeningScript overwrites AI-transcribed items without duplicating prefix
        res2 = ai_supplement.supplement(
            self.batch,
            "listeningScript",
            invoke_fn=lambda p, d: {
                "result": {
                    "items": [
                        {"key": "u-listen", "text": "M: Where is the library?\nW: Next to the science building."},
                        {"key": "u-listen-group", "text": "W: Concert tonight?\nM: Yes, at 7:30."},
                    ]
                }
            },
        )
        self.assertEqual(res2["applied"], 2)
        updated2 = json.loads((self.batch / "06_batch/batch_meta.json").read_text(encoding="utf-8"))
        by_uid2 = {it["unit_id"]: it for it in updated2["items"]}
        self.assertTrue(by_uid2["u-listen-group"]["explanation"].startswith("原题解析说明。\n\n听力录音材料：\n"))
        self.assertEqual(by_uid2["u-listen-group"]["explanation"].count("原题解析说明。"), 1)
        self.assertEqual(by_uid2["u-listen-orig"]["explanation"], "听力录音材料：\nM: Hello, how are you?\nW: Fine, thank you.")

    def test_strict_non_overwrite_across_all_three_modes(self):
        """原文或已有非AI内容存在时，三种模式均不得覆盖。"""
        (self.batch / "01_source/正文.txt").write_text(
            "1. Q1?\n2. Q2?\n3. He ___ tall.\n",
            encoding="utf-8",
        )
        (self.batch / "01_source/附录.txt").write_text(
            "参考答案与解析\n"
            "1. A 【解析】原卷自带第1题解析。\n"
            "听力材料\n"
            "1. M: Hello from original appendix.\n"
            "W: Nice to meet you.\n",
            encoding="utf-8",
        )
        (self.batch / "01_source/structure.confirmed.json").write_text(
            json.dumps({
                "units": [
                    {"unit_id": "u-listen-src", "type": "听力短对话理解", "unit_kind": "standalone", "numbers": [1]},
                    {"unit_id": "u-listen-ai-prefix-orig-script", "type": "听力短对话理解", "unit_kind": "standalone", "numbers": [4]},
                    {"unit_id": "u-listen-missing", "type": "听力短对话理解", "unit_kind": "standalone", "numbers": [5]},
                ]
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.batch / "02_audio").mkdir(exist_ok=True)
        (self.batch / "02_audio/full-transcript.json").write_text(
            json.dumps({
                "segments": [
                    {"start": 0.0, "end": 10.0, "text": "Hello from ASR transcript."},
                    {"start": 12.0, "end": 22.0, "text": "Second dialogue from ASR."},
                    {"start": 24.0, "end": 34.0, "text": "Third dialogue from ASR."},
                ]
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.batch / "02_audio/segment-evidence.json").write_text(
            json.dumps({
                "items": [
                    {"unit_id": "u-listen-src", "start": 0.0, "end": 10.0, "verified": True},
                    {"unit_id": "u-listen-ai-prefix-orig-script", "start": 12.0, "end": 22.0, "verified": True},
                    {"unit_id": "u-listen-missing", "start": 24.0, "end": 34.0, "verified": True},
                ]
            }, ensure_ascii=False),
            encoding="utf-8",
        )

        long_material = " ".join(["passage"] * 60) + "."
        meta = {
            "items": [
                {
                    "unit_id": "u-read-existing",
                    "unit_kind": "group",
                    "question_type": "阅读理解",
                    "material": long_material,
                    "articleSummary": "原卷已有文章大意，禁止覆盖。",
                    "numbers": [1, 2],
                    "subquestions": [
                        {"number": 1, "stem": "1. Q1?", "options": [{"key": "A", "text": "Yes"}], "answer": "A", "explanation": ""},
                        {"number": 2, "stem": "2. Q2?", "options": [{"key": "B", "text": "No"}], "answer": "B", "explanation": "原卷已有第2题解析，禁止覆盖。"},
                    ],
                },
                {
                    "unit_id": "u-short-missing",
                    "unit_kind": "standalone",
                    "question_type": "单项填空",
                    "number": 3,
                    "stem": "3. He ___ tall.",
                    "answer": "is",
                    "explanation": "",
                },
                {
                    "unit_id": "u-listen-src",
                    "unit_kind": "standalone",
                    "question_type": "听力短对话理解",
                    "number": 1,
                    "stem": "1. Listen?",
                    "answer": "A",
                    "explanation": "",
                },
                {
                    "unit_id": "u-listen-ai-prefix-orig-script",
                    "unit_kind": "standalone",
                    "question_type": "听力短对话理解",
                    "number": 4,
                    "stem": "4. Listen?",
                    "answer": "B",
                    "explanation": f"AI前缀说明。{upload_content.AI_MARK}\n\n听力录音材料：\nM: Original script must stay.",
                },
                {
                    "unit_id": "u-listen-missing",
                    "unit_kind": "standalone",
                    "question_type": "听力短对话理解",
                    "number": 5,
                    "stem": "5. Listen?",
                    "answer": "C",
                    "explanation": "",
                },
            ]
        }
        (self.batch / "06_batch/batch_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8"
        )

        # 1) articleSummary: u-read-existing already has non-AI summary -> 0 targets
        self.assertEqual(ai_supplement.eligible_targets(self.batch, "articleSummary"), [])

        # 2) explanation: subquestion 1 is in 附录.txt, subquestion 2 already has non-AI explanation -> only u-short-missing
        exp_targets = ai_supplement.eligible_targets(self.batch, "explanation")
        self.assertEqual([t["key"] for t in exp_targets], ["u-short-missing"])

        # 3) listeningScript: u-listen-src is in 附录.txt; u-listen-ai-prefix-orig-script has AI_MARK prefix
        #    but ORIGINAL non-AI 听力录音材料 -> ONLY u-listen-missing is eligible!
        ls_targets = ai_supplement.eligible_targets(self.batch, "listeningScript")
        self.assertEqual([t["key"] for t in ls_targets], ["u-listen-missing"])

        res = ai_supplement.supplement(
            self.batch,
            "listeningScript",
            invoke_fn=lambda p, d: {
                "result": {
                    "items": [
                        {"key": "u-listen-src", "text": "试图覆盖原卷附录"},
                        {"key": "u-listen-ai-prefix-orig-script", "text": "试图覆盖AI前缀+原卷听力"},
                        {"key": "u-listen-missing", "text": "M: Third dialogue from ASR."},
                    ]
                }
            },
        )
        self.assertEqual(res["applied"], 1)
        self.assertEqual(res["keys"], ["u-listen-missing"])
        updated = json.loads((self.batch / "06_batch/batch_meta.json").read_text(encoding="utf-8"))
        by_uid = {it["unit_id"]: it for it in updated["items"]}
        self.assertEqual(by_uid["u-read-existing"]["articleSummary"], "原卷已有文章大意，禁止覆盖。")
        self.assertEqual(by_uid["u-read-existing"]["subquestions"][1]["explanation"], "原卷已有第2题解析，禁止覆盖。")
        self.assertEqual(by_uid["u-listen-src"]["explanation"], "")
        self.assertEqual(
            by_uid["u-listen-ai-prefix-orig-script"]["explanation"],
            f"AI前缀说明。{upload_content.AI_MARK}\n\n听力录音材料：\nM: Original script must stay.",
        )
        self.assertEqual(
            by_uid["u-listen-missing"]["explanation"],
            f"听力录音材料：\nM: Third dialogue from ASR.\n{upload_content.AI_TRANSCRIPT_MARK}",
        )

    def test_listening_script_post_completion_keeps_local_complete_and_uploads_verbatim(self):
        """后置补全听力材料不打断已完成批次状态、不触发重跑、无锁死锁，且上传载荷原样包含AI转录材料。"""
        from workbench import server, taxonomy
        from workflow import execution_state, format_gate, local_agent, manifest as mf
        from workflow import test_upload_payload, upload, upload_payload

        k_idx = taxonomy.knowledge_index()[0]
        listen_kp = "主题/人与自我/生活与学习/学校生活"
        short_kp = "词汇/词汇辨析/动词辨析"

        (self.batch / "01_source/正文.txt").write_text(
            "1. Where is the bus stop?\n2. She ______ a student.\n",
            encoding="utf-8",
        )
        (self.batch / "01_source/附录.txt").write_text(
            "参考答案与解析\n1. A\n2. A 【解析】原卷自带解析。\n",
            encoding="utf-8",
        )
        (self.batch / "01_source_ans").mkdir(exist_ok=True)
        (self.batch / "01_source_ans/answers.json").write_text(
            json.dumps({
                "items": [
                    {"unit_id": "u-listen", "number": 1, "answer": "A"},
                    {"unit_id": "u-short", "number": 2, "answer": "A"},
                ]
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.batch / "01_source/structure.json").write_text(
            json.dumps({
                "sections": [
                    {"title": "一、听力短对话理解", "type": "听力短对话理解", "questions": [1]},
                    {"title": "二、单项填空", "type": "单项填空", "questions": [2]},
                ]
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.batch / "01_source/structure.confirmed.json").write_text(
            json.dumps({
                "units": [
                    {"unit_id": "u-listen", "type": "听力短对话理解", "unit_kind": "standalone", "numbers": [1]},
                    {"unit_id": "u-short", "type": "单项填空", "unit_kind": "standalone", "numbers": [2]},
                ]
            }, ensure_ascii=False),
            encoding="utf-8",
        )

        (self.batch / "attachments").mkdir(exist_ok=True)
        doc = self.batch / "attachments" / "source.docx"
        doc.write_bytes(b"synthetic docx")
        (self.batch / "02_audio").mkdir(parents=True, exist_ok=True)
        (self.batch / "02_audio/full-transcript.json").write_text(
            json.dumps({
                "segments": [
                    {"start": 0.0, "end": 8.0, "text": "Where is the bus stop? It is across from the park."},
                    {"start": 10.0, "end": 18.0, "text": "Where is the bus stop? It is across from the park."},
                ]
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.batch / "02_audio/segment-evidence.json").write_text(
            json.dumps({
                "items": [{"unit_id": "u-listen", "start": 0.0, "end": 20.0, "verified": True}]
            }, ensure_ascii=False),
            encoding="utf-8",
        )

        meta = {
            "batch_id": "b-listen",
            "name": "b-listen",
            "upload_mode": "source",
            "upload_target": "同步练习",
            "items": [
                {
                    "id": "Q-listen",
                    "unit_id": "u-listen",
                    "unit_kind": "standalone",
                    "question_type": "听力短对话理解",
                    "number": 1,
                    "numbers": [1],
                    "stem": "1. Where is the bus stop?",
                    "options": [
                        "A. Across from the park",
                        "B. Near the school",
                        "C. Behind the bank",
                    ],
                    "answer": "A",
                    "explanation": "",
                    "filterMetadata": {"subtypes": ["选文字"], "difficulty": "适中", "difficultyValue": 0.65},
                    "knowledge": [listen_kp],
                    "knowledgeIds": [k_idx[listen_kp]["id"]],
                    "knowledge_ids": [k_idx[listen_kp]["id"]],
                    "difficulty": "适中",
                    "source_type": "同步练习",
                    "year": 2026,
                    "source": "2026 同步练习原卷",
                    "location": "第一部分 第1题",
                    "version": "原卷扫描版",
                    "authorization": "已获授权",
                },
                {
                    "id": "Q-short",
                    "unit_id": "u-short",
                    "unit_kind": "standalone",
                    "question_type": "单项填空",
                    "number": 2,
                    "numbers": [2],
                    "stem": "2. She ______ a student.",
                    "options": [
                        "A. is",
                        "B. am",
                        "C. are",
                        "D. be",
                    ],
                    "answer": "A",
                    "explanation": "原卷自带解析。",
                    "filterMetadata": {"subtypes": ["四个选项"], "difficulty": "适中", "difficultyValue": 0.65},
                    "knowledge": [short_kp],
                    "knowledgeIds": [k_idx[short_kp]["id"]],
                    "knowledge_ids": [k_idx[short_kp]["id"]],
                    "difficulty": "适中",
                    "source_type": "同步练习",
                    "year": 2026,
                    "source": "2026 同步练习原卷",
                    "location": "第二部分 第2题",
                    "version": "原卷扫描版",
                    "authorization": "已获授权",
                },
            ],
        }
        (self.batch / "06_batch/batch_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8"
        )

        # Simulate a fully completed batch (local_complete == True) before post-hoc AI supplement
        import hashlib
        from workflow import final_audit
        (self.batch / "07_review").mkdir(exist_ok=True)
        (self.batch / "07_review/structure-gate.json").write_text(
            json.dumps({
                "passed": True,
                "input_hashes": {
                    rel: hashlib.sha256((self.batch / rel).read_bytes()).hexdigest()
                    for rel in final_audit.SOURCE_FILES
                },
                "original_sha256": hashlib.sha256(doc.read_bytes()).hexdigest(),
                "structure_warnings": [],
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        state = mf.new_manifest("b-listen", self.batch, {"source": {"path": str(doc), "exists": True}}, {})
        for stage in execution_state.LOCAL_STAGES:
            state["stages"][stage] = {"status": "completed"}
        mf.save(self.batch, state)
        fg_initial = format_gate.run(self.batch)
        self.assertTrue(fg_initial["passed"], fg_initial)
        self.assertTrue(execution_state.local_complete(self.batch, mf.load(self.batch)))

        # Step 1: Run listeningScript supplement on the completed batch
        transcript_text = "M: Excuse me, where is the bus stop?\nW: It is right across from the park."
        res = ai_supplement.supplement(
            self.batch,
            "listeningScript",
            invoke_fn=lambda p, d: {"result": {"items": [{"key": "u-listen", "text": transcript_text}]}},
        )
        self.assertEqual(res["applied"], 1)
        self.assertTrue(res["format_gate_passed"], res)

        # Workflow state MUST remain local_complete == True and not regress stages
        after_manifest = mf.load(self.batch)
        self.assertTrue(execution_state.local_complete(self.batch, after_manifest))
        self.assertEqual(after_manifest["stages"]["format_gate"]["status"], "completed")
        self.assertEqual(after_manifest["stages"]["assemble"]["status"], "completed")

        # Step 2: Verify upload_content strict check, build_payload, upload.prepare & upload.verify
        catalog = test_upload_payload.catalog_snapshot()
        updated = json.loads((self.batch / "06_batch/batch_meta.json").read_text(encoding="utf-8"))
        expected_exp = f"听力录音材料：\n{transcript_text}\n{upload_content.AI_TRANSCRIPT_MARK}"
        self.assertEqual(updated["items"][0]["explanation"], expected_exp)

        strict_report = upload_content.inspect_content(self.batch, updated, strict=True)
        self.assertTrue(strict_report["passed"], strict_report)

        built = upload_payload.build_payload(updated, catalog, batch_id="b-listen", name="b-listen", batch_dir=self.batch)
        self.assertTrue(built["passed"], built["errors"])
        self.assertEqual(built["payload"]["items"][0]["explanation"], expected_exp)

        prep = upload.prepare(self.batch, catalog=catalog, check_media=False)
        self.assertTrue(prep["passed"], prep)
        ver = upload.verify(self.batch)
        frozen_payload = json.loads(ver["payload"].decode("utf-8"))
        self.assertEqual(frozen_payload["items"][0]["explanation"], expected_exp)

        # Step 3: Manual edit while holding _agent_lock (simulating server.Handler.save_content_edit)
        # must NOT deadlock and must preserve local_complete == True
        with local_agent._agent_lock(0):
            edit_res = ai_supplement.save_content_edit(
                self.batch,
                "u-listen",
                {"explanation": "听力录音材料：\nM: Excuse me, where is the bus stop?\nW: Across from the park."},
                lock_held=True,
            )
        self.assertTrue(edit_res["applied"])
        self.assertTrue(edit_res["format_gate_passed"], edit_res)
        self.assertTrue(execution_state.local_complete(self.batch, mf.load(self.batch)))
        # Verify server.Handler.save_content_edit passes lock_held=True
        import inspect
        self.assertIn("lock_held=True", inspect.getsource(server.Handler.save_content_edit))

    def test_full_afternoon_integrated_multi_round_lifecycle(self):
        """综合端到端多轮测试：三类AI补全、严禁覆盖原文、人工改解析不锁死/不拦截上传、旧一审不混入冻结载荷、审批后禁写、缺转录兜底。"""
        import hashlib
        from workbench import server, taxonomy
        from workflow import execution_state, final_audit, format_gate, local_agent, manifest as mf
        from workflow import test_upload_payload, upload

        k_idx = taxonomy.knowledge_index()[0]
        listen_kp = "主题/人与自我/生活与学习/学校生活"
        read_genre = "语篇体裁/记叙文/故事"
        read_theme = "主题/人与自我/生活与学习/学校生活"
        read_sub = "设题角度/阅读/细节理解"
        short_kp = "词汇/词汇辨析/动词辨析"
        bogus_stale_kp = next(p for p in k_idx if p != short_kp and p.startswith("词汇/词汇辨析/"))

        (self.batch / "01_source/正文.txt").write_text(
            "1. Where is the library?\n"
            "2. Where will they go?\n3. When will they meet?\n"
            "4. How is the speaker?\n"
            "5. Q5?\n6. Q6?\n7. Q7?\n"
            "8. She ___ a student.\n",
            encoding="utf-8",
        )
        (self.batch / "01_source/附录.txt").write_text(
            "参考答案与解析\n"
            "1. A\n2. A\n3. B\n4. A\n"
            "5. A\n6. B 【解析】原卷第6题解析，不得覆盖。\n7. A\n8. A\n",
            encoding="utf-8",
        )
        (self.batch / "01_source_ans").mkdir(exist_ok=True)
        (self.batch / "01_source_ans/answers.json").write_text(
            json.dumps({
                "items": [
                    {"unit_id": "u-listen-standalone", "number": 1, "answer": "A"},
                    {"unit_id": "u-listen-group", "number": 2, "answer": "A"},
                    {"unit_id": "u-listen-group", "number": 3, "answer": "B"},
                    {"unit_id": "u-listen-orig", "number": 4, "answer": "A"},
                    {"unit_id": "u-read", "number": 5, "answer": "A"},
                    {"unit_id": "u-read", "number": 6, "answer": "B"},
                    {"unit_id": "u-read", "number": 7, "answer": "A"},
                    {"unit_id": "u-short", "number": 8, "answer": "A"},
                ]
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.batch / "01_source/structure.json").write_text(
            json.dumps({
                "sections": [
                    {"title": "一、听力短对话理解", "type": "听力短对话理解", "questions": [1, 4]},
                    {"title": "二、听力长对话理解", "type": "听力长对话理解", "questions": [2, 3]},
                    {"title": "三、阅读理解", "type": "阅读理解", "questions": [5, 6, 7]},
                    {"title": "四、单项填空", "type": "单项填空", "questions": [8]},
                ]
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.batch / "01_source/structure.confirmed.json").write_text(
            json.dumps({
                "units": [
                    {"unit_id": "u-listen-standalone", "type": "听力短对话理解", "unit_kind": "standalone", "numbers": [1]},
                    {"unit_id": "u-listen-group", "type": "听力长对话理解", "unit_kind": "group", "numbers": [2, 3]},
                    {"unit_id": "u-listen-orig", "type": "听力短对话理解", "unit_kind": "standalone", "numbers": [4]},
                    {"unit_id": "u-read", "type": "阅读理解", "unit_kind": "group", "numbers": [5, 6, 7]},
                    {"unit_id": "u-short", "type": "单项填空", "unit_kind": "standalone", "numbers": [8]},
                ]
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.batch / "attachments").mkdir(exist_ok=True)
        doc = self.batch / "attachments" / "source.docx"
        doc.write_bytes(b"synthetic docx for integrated test")
        (self.batch / "02_audio").mkdir(parents=True, exist_ok=True)
        (self.batch / "02_audio/full-transcript.json").write_text(
            json.dumps({
                "segments": [
                    {"start": 0.0, "end": 8.0, "text": "Where is the library? Next to the lab."},
                    {"start": 10.0, "end": 18.0, "text": "Where is the library? Next to the lab."},
                    {"start": 20.0, "end": 32.0, "text": "Are you going to the concert? Yes, at seven thirty."},
                    {"start": 34.0, "end": 46.0, "text": "Are you going to the concert? Yes, at seven thirty."},
                    {"start": 50.0, "end": 60.0, "text": "How are you? Fine, thanks."},
                ]
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.batch / "02_audio/segment-evidence.json").write_text(
            json.dumps({
                "items": [
                    {"unit_id": "u-listen-standalone", "start": 0.0, "end": 19.0, "verified": True},
                    {"unit_id": "u-listen-group", "start": 19.5, "end": 47.0, "verified": False},
                    {"unit_id": "u-listen-orig", "start": 49.0, "end": 61.0, "verified": True},
                ]
            }, ensure_ascii=False),
            encoding="utf-8",
        )

        orig_script = "听力录音材料：\nM: Original dialogue.\nW: Keep intact."
        long_material = " ".join(["reading"] * 65) + "."
        common_meta = {
            "difficulty": "适中",
            "source_type": "同步练习",
            "year": 2026,
            "source": "2026 同步练习原卷",
            "version": "原卷扫描版",
            "authorization": "已获授权",
        }
        meta = {
            "batch_id": "b-integrated",
            "name": "b-integrated",
            "upload_mode": "source",
            "upload_target": "同步练习",
            "items": [
                {
                    **common_meta,
                    "id": "Q-ls-1",
                    "unit_id": "u-listen-standalone",
                    "unit_kind": "standalone",
                    "question_type": "听力短对话理解",
                    "number": 1,
                    "numbers": [1],
                    "stem": "1. Where is the library?",
                    "options": ["A. Next to the lab", "B. Near the gym", "C. Behind the hall"],
                    "answer": "A",
                    "explanation": "",
                    "filterMetadata": {"subtypes": ["选文字"], "difficulty": "适中", "difficultyValue": 0.65},
                    "knowledge": [listen_kp],
                    "knowledgeIds": [k_idx[listen_kp]["id"]],
                    "knowledge_ids": [k_idx[listen_kp]["id"]],
                    "location": "第一部分 第1题",
                },
                {
                    **common_meta,
                    "id": "Q-ls-2",
                    "unit_id": "u-listen-group",
                    "unit_kind": "group",
                    "question_type": "听力长对话理解",
                    "number": 2,
                    "numbers": [2, 3],
                    "stem": "听下面一段对话，回答第2至3小题。",
                    "material": "听下面一段对话，回答第2至3小题。",
                    "explanation": "",
                    "filterMetadata": {"subtypes": ["选择2题"], "difficulty": "适中", "difficultyValue": 0.65},
                    "knowledge": [listen_kp],
                    "knowledgeIds": [k_idx[listen_kp]["id"]],
                    "knowledge_ids": [k_idx[listen_kp]["id"]],
                    "location": "第二部分 第2-3题",
                    "subquestions": [
                        {
                            "number": 2,
                            "stem": "2. Where will they go?",
                            "options": ["A. Concert", "B. Cinema", "C. Park"],
                            "answer": "A",
                            "explanation": "",
                            "knowledge": [listen_kp],
                            "knowledgeIds": [k_idx[listen_kp]["id"]],
                            "knowledge_ids": [k_idx[listen_kp]["id"]],
                        },
                        {
                            "number": 3,
                            "stem": "3. When will they meet?",
                            "options": ["A. 7:00", "B. 7:30", "C. 8:00"],
                            "answer": "B",
                            "explanation": "",
                            "knowledge": [listen_kp],
                            "knowledgeIds": [k_idx[listen_kp]["id"]],
                            "knowledge_ids": [k_idx[listen_kp]["id"]],
                        },
                    ],
                },
                {
                    **common_meta,
                    "id": "Q-ls-4",
                    "unit_id": "u-listen-orig",
                    "unit_kind": "standalone",
                    "question_type": "听力短对话理解",
                    "number": 4,
                    "numbers": [4],
                    "stem": "4. How is the speaker?",
                    "options": ["A. Fine", "B. Tired", "C. Busy"],
                    "answer": "A",
                    "explanation": "",
                    "filterMetadata": {"subtypes": ["选文字"], "difficulty": "适中", "difficultyValue": 0.65},
                    "knowledge": [listen_kp],
                    "knowledgeIds": [k_idx[listen_kp]["id"]],
                    "knowledge_ids": [k_idx[listen_kp]["id"]],
                    "location": "第一部分 第4题",
                },
                {
                    **common_meta,
                    "id": "Q-read",
                    "unit_id": "u-read",
                    "unit_kind": "group",
                    "question_type": "阅读理解",
                    "number": 5,
                    "numbers": [5, 6, 7],
                    "stem": "阅读下列短文，回答第5至7小题。",
                    "material": long_material,
                    "articleSummary": "",
                    "filterMetadata": {"subtypes": ["选择3题"], "difficulty": "适中", "difficultyValue": 0.65},
                    "knowledge": [read_genre, read_theme],
                    "knowledgeIds": [k_idx[read_genre]["id"], k_idx[read_theme]["id"]],
                    "knowledge_ids": [k_idx[read_genre]["id"], k_idx[read_theme]["id"]],
                    "location": "第三部分 第5-7题",
                    "subquestions": [
                        {
                            "number": 5,
                            "stem": "5. Q5?",
                            "options": ["A. Yes", "B. No", "C. Maybe", "D. Unknown"],
                            "answer": "A",
                            "explanation": "",
                            "knowledge": [read_sub],
                            "knowledgeIds": [k_idx[read_sub]["id"]],
                            "knowledge_ids": [k_idx[read_sub]["id"]],
                        },
                        {
                            "number": 6,
                            "stem": "6. Q6?",
                            "options": ["A. Yes", "B. No", "C. Maybe", "D. Unknown"],
                            "answer": "B",
                            "explanation": "原卷第6题解析，不得覆盖。",
                            "knowledge": [read_sub],
                            "knowledgeIds": [k_idx[read_sub]["id"]],
                            "knowledge_ids": [k_idx[read_sub]["id"]],
                        },
                        {
                            "number": 7,
                            "stem": "7. Q7?",
                            "options": ["A. Yes", "B. No", "C. Maybe", "D. Unknown"],
                            "answer": "A",
                            "explanation": "",
                            "knowledge": [read_sub],
                            "knowledgeIds": [k_idx[read_sub]["id"]],
                            "knowledge_ids": [k_idx[read_sub]["id"]],
                        },
                    ],
                },
                {
                    **common_meta,
                    "id": "Q-short",
                    "unit_id": "u-short",
                    "unit_kind": "standalone",
                    "question_type": "单项填空",
                    "number": 8,
                    "numbers": [8],
                    "stem": "8. She ______ a student.",
                    "options": ["A. is", "B. am", "C. are", "D. be"],
                    "answer": "A",
                    "explanation": "无",
                    "filterMetadata": {"subtypes": ["四个选项"], "difficulty": "适中", "difficultyValue": 0.65},
                    "knowledge": [short_kp],
                    "knowledgeIds": [k_idx[short_kp]["id"]],
                    "knowledge_ids": [k_idx[short_kp]["id"]],
                    "location": "第四部分 第8题",
                },
            ],
        }
        (self.batch / "06_batch/batch_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8"
        )
        # Seed manual-content-edits so pre-existing original script passes strict inspect_content
        ai_supplement.save_content_edit(self.batch, "u-listen-orig", {"explanation": orig_script})
        ai_supplement.save_content_edit(self.batch, "u-listen-group", {"explanation": "原题解析要点。"})

        (self.batch / "07_review").mkdir(exist_ok=True)
        (self.batch / "07_review/structure-gate.json").write_text(
            json.dumps({
                "passed": True,
                "input_hashes": {
                    rel: hashlib.sha256((self.batch / rel).read_bytes()).hexdigest()
                    for rel in final_audit.SOURCE_FILES
                },
                "original_sha256": hashlib.sha256(doc.read_bytes()).hexdigest(),
                "structure_warnings": [],
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        state = mf.new_manifest("b-integrated", self.batch, {"source": {"path": str(doc), "exists": True}}, {})
        for stage in execution_state.LOCAL_STAGES:
            state["stages"][stage] = {"status": "completed"}
        mf.save(self.batch, state)
        fg = format_gate.run(self.batch)
        self.assertTrue(fg["passed"], fg)
        self.assertTrue(execution_state.local_complete(self.batch, mf.load(self.batch)))

        def assert_completed_and_stable():
            cur = mf.load(self.batch)
            vr = (self.batch / "07_review/validation-report.json").read_text(encoding="utf-8") if (self.batch / "07_review/validation-report.json").is_file() else ""
            self.assertTrue(execution_state.local_complete(self.batch, cur), vr)
            for st in ("review", "review_gate", "validate", "format_gate"):
                self.assertEqual(cur["stages"][st]["status"], "completed", (st, cur["stages"][st], vr))
                self.assertFalse(cur["stages"][st].get("rebuild_required"), st)

        # Step A: Run all 3 AI modes sequentially
        res_sum = ai_supplement.supplement(
            self.batch, "articleSummary",
            invoke_fn=lambda p, d: {"result": {"items": [{"key": "u-read", "text": "本文讲述校园阅读故事。"}]}},
        )
        self.assertEqual(res_sum["applied"], 1)
        assert_completed_and_stable()

        res_exp = ai_supplement.supplement(
            self.batch, "explanation",
            invoke_fn=lambda p, d: {"result": {"items": [
                {"key": "u-read/5", "text": "细节定位第1段选A。"},
                {"key": "u-read/6", "text": "试图覆盖原卷第6题解析"},
                {"key": "u-read/7", "text": "细节定位第3段选A。"},
                {"key": "u-short", "text": "主语单三用is。"},
            ]}},
        )
        self.assertEqual(res_exp["applied"], 3)
        self.assertEqual(sorted(res_exp["keys"]), ["u-read/5", "u-read/7", "u-short"])
        assert_completed_and_stable()

        res_ls = ai_supplement.supplement(
            self.batch, "listeningScript",
            invoke_fn=lambda p, d: {"result": {"items": [
                {"key": "u-listen-standalone", "text": "M: Where is the library?\nW: Next to the lab."},
                {"key": "u-listen-group", "text": "W: Are you going to the concert?\nM: Yes, at seven thirty."},
                {"key": "u-listen-orig", "text": "试图覆盖原卷听力材料"},
            ]}},
        )
        self.assertEqual(res_ls["applied"], 2)
        self.assertEqual(res_ls["keys"], ["u-listen-standalone", "u-listen-group"])
        assert_completed_and_stable()

        # Step B: Regenerate listeningScript, locked manual edit, stale conflict check, _validate_ever_passed
        res_ls2 = ai_supplement.supplement(
            self.batch, "listeningScript",
            invoke_fn=lambda p, d: {"result": {"items": [
                {"key": "u-listen-standalone", "text": "M: Excuse me, where is the library?\nW: Right next to the lab."},
                {"key": "u-listen-group", "text": "W: Concert tonight?\nM: Yes, at 7:30."},
            ]}},
        )
        self.assertEqual(res_ls2["applied"], 2)
        assert_completed_and_stable()

        with local_agent._agent_lock(0):
            edit_res = ai_supplement.save_content_edit(
                self.batch, "u-short",
                {"explanation": "人工复核：第三人称单数用is。"},
                lock_held=True,
            )
        self.assertTrue(edit_res["applied"])
        assert_completed_and_stable()

        # Stale optimistic lock must reject without corrupting saved text
        with self.assertRaises(ai_supplement.ContentEditConflictError):
            ai_supplement.save_content_edit(
                self.batch, "u-short",
                {"explanation": "旧快照覆盖", "expected": {"explanation": "无"}},
            )
        cur_meta = json.loads((self.batch / "06_batch/batch_meta.json").read_text(encoding="utf-8"))
        by_uid = {it["unit_id"]: it for it in cur_meta["items"]}
        self.assertEqual(by_uid["u-short"]["explanation"], "人工复核：第三人称单数用is。")
        self.assertEqual(by_uid["u-listen-orig"]["explanation"], orig_script)
        self.assertEqual(by_uid["u-read"]["subquestions"][1]["explanation"], "原卷第6题解析，不得覆盖。")
        self.assertEqual(by_uid["u-listen-group"]["explanation"].count("原题解析要点。"), 1)
        self.assertTrue(by_uid["u-listen-group"]["explanation"].endswith(upload_content.AI_TRANSCRIPT_MARK))

        # _validate_ever_passed accepts both completed and invalidated-after-completed
        self.assertTrue(server._validate_ever_passed({"status": mf.COMPLETED}, mf))
        self.assertTrue(server._validate_ever_passed(
            {"status": mf.INVALIDATED, "history": [{"from": mf.COMPLETED, "to": mf.INVALIDATED}]}, mf
        ))
        self.assertFalse(server._validate_ever_passed(
            {"status": mf.INVALIDATED, "history": [{"from": mf.PENDING, "to": mf.INVALIDATED}]}, mf
        ))

        # Step C: Inject stale Round 1 record -> upload.prepare & verify must NOT merge stale label,
        # while preserving all AI-transcribed and original listening scripts verbatim
        (self.batch / "07_review/review-rounds.json").write_text(
            json.dumps({
                "rounds": [{
                    "round_id": "labels",
                    "execution_id": "stale-r1",
                    "input_binding": "stale-binding-mismatch",
                    "checks": [{
                        "unit_id": "u-short",
                        "verdict": "error",
                        "assessment": {"knowledge": [bogus_stale_kp]},
                    }],
                }]
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        catalog = test_upload_payload.catalog_snapshot()
        prep = upload.prepare(self.batch, catalog=catalog, check_media=False)
        self.assertTrue(prep["passed"], prep)
        ver = upload.verify(self.batch)
        frozen = json.loads(ver["payload"].decode("utf-8"))
        frozen_by_id = {it["id"]: it for it in frozen["items"]}

        self.assertNotIn("REMOTE-" + k_idx[bogus_stale_kp]["id"], frozen_by_id["Q-short"]["knowledge_ids"])
        self.assertNotIn(k_idx[bogus_stale_kp]["id"], frozen_by_id["Q-short"]["knowledge_ids"])
        self.assertIn("REMOTE-" + k_idx[short_kp]["id"], frozen_by_id["Q-short"]["knowledge_ids"])
        self.assertEqual(
            frozen_by_id["Q-ls-1"]["explanation"],
            f"听力录音材料：\nM: Excuse me, where is the library?\nW: Right next to the lab.\n{upload_content.AI_TRANSCRIPT_MARK}",
        )
        self.assertEqual(
            frozen_by_id["Q-ls-2"]["explanation"],
            f"原题解析要点。\n\n听力录音材料：\nW: Concert tonight?\nM: Yes, at 7:30.\n{upload_content.AI_TRANSCRIPT_MARK}",
        )
        self.assertEqual(frozen_by_id["Q-ls-4"]["explanation"], orig_script)
        self.assertEqual(frozen_by_id["Q-read"]["articleSummary"], "本文讲述校园阅读故事。" + upload_content.AI_MARK)
        self.assertEqual(frozen_by_id["Q-read"]["subquestions"][1]["explanation"], "原卷第6题解析，不得覆盖。")

        # Step D: Once upload_gate is approved, all 4 mutation paths must be blocked
        approved_state = mf.load(self.batch)
        approved_state["stages"]["upload_gate"] = {"status": mf.APPROVED}
        mf.save(self.batch, approved_state)
        for mode in ("listeningScript", "explanation", "articleSummary"):
            with self.assertRaises(ValueError):
                ai_supplement.supplement(self.batch, mode, invoke_fn=lambda p, d: {"result": {"items": []}})
        with self.assertRaises(ValueError):
            ai_supplement.save_content_edit(self.batch, "u-short", {"explanation": "审批后尝试修改"})

        # Step E: Missing audio transcript files degrade gracefully without crashing
        with tempfile.TemporaryDirectory() as clean_tmp:
            clean_batch = Path(clean_tmp).resolve()
            (clean_batch / "01_source").mkdir()
            (clean_batch / "06_batch").mkdir()
            (clean_batch / "01_source/正文.txt").write_text("1. Listen?\n", encoding="utf-8")
            (clean_batch / "06_batch/batch_meta.json").write_text(
                json.dumps({
                    "items": [{
                        "unit_id": "u-no-audio",
                        "unit_kind": "standalone",
                        "question_type": "听力短对话理解",
                        "stem": "1. Listen?",
                        "answer": "A",
                        "explanation": "",
                    }]
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            self.assertEqual(ai_supplement.eligible_targets(clean_batch, "listeningScript"), [])
            empty_res = ai_supplement.supplement(
                clean_batch, "listeningScript",
                invoke_fn=lambda p, d: (_ for _ in ()).throw(AssertionError("should not invoke")),
            )
            self.assertEqual(empty_res["applied"], 0)

    def test_post_edit_gate_refresh_does_not_hang_on_foreign_agent_lock(self):
        import time
        from workflow import format_gate, local_agent, manifest as mf, runner

        (self.batch / "workflow").mkdir(exist_ok=True)
        (self.batch / "attachments").mkdir(exist_ok=True)
        doc = self.batch / "attachments" / "s.docx"
        doc.write_bytes(b"x")
        state = mf.new_manifest("b-lock", self.batch, {"source": {"path": str(doc), "exists": True}}, {})
        state["stages"]["format_gate"]["status"] = mf.COMPLETED
        mf.save(self.batch, state)

        seen_lock_held = []
        orig_validate = runner.do_validate
        orig_format_run = format_gate.run

        def fake_validate(batch_dir, current, *, lock_held=False, refresh_only=False):
            seen_lock_held.append(lock_held)
            return 0

        runner.do_validate = fake_validate
        format_gate.run = lambda batch_dir: {"passed": True, "errors": []}
        try:
            # Simulate an unrelated batch holding global _agent_lock (e.g. review running).
            with local_agent._agent_lock(0, shared=True):
                initial = ai_supplement.start_background(
                    self.batch,
                    "articleSummary",
                    invoke_fn=lambda p, d: {"result": {"items": [{"key": "u-reading", "text": "不被他批锁阻塞的大意。"}]}},
                )
                deadline = time.monotonic() + 5.0
                prog = initial
                while time.monotonic() < deadline:
                    prog = ai_supplement.read_progress(self.batch)
                    if prog["status"] != "running":
                        break
                    time.sleep(0.05)
        finally:
            runner.do_validate = orig_validate
            format_gate.run = orig_format_run

        self.assertEqual(prog["status"], "done", prog)
        self.assertEqual(seen_lock_held, [True])
        self.assertEqual(prog["validate_passed"], True)
        self.assertEqual(prog["format_gate_passed"], True)

    def test_provenance_and_check_explanations_accept_ai_transcript_mark(self):
        import importlib.util
        from workflow import agent_tasks
        row = {
            "answer": "A",
            "explanation": f"听力录音材料：\nM: Good morning.\n{upload_content.AI_TRANSCRIPT_MARK}",
        }
        # Assemble provenance accepts AI_TRANSCRIPT_MARK only when backed by verified workbench transcript record
        agent_tasks._provenance(
            row, source_text="1. A", expected_answer="A",
            verified_transcript_explanations={row["explanation"]},
        )
        # Unverified AI_TRANSCRIPT_MARK without workbench transcript record must be rejected
        with self.assertRaises(ValueError):
            agent_tasks._provenance(row, source_text="1. A", expected_answer="A")
        # Unsourced answer cannot be bypassed via AI_TRANSCRIPT_MARK
        with self.assertRaises(ValueError):
            agent_tasks._provenance(
                {"answer": "Z", "explanation": row["explanation"]},
                source_text="1. A",
                verified_transcript_explanations={row["explanation"]},
            )
        # But AI_TRANSCRIPT_MARK is never allowed inside answer
        with self.assertRaises(ValueError):
            agent_tasks._provenance({"answer": f"A{upload_content.AI_TRANSCRIPT_MARK}", "explanation": "无"}, "1. A")
        # check_explanations.audit counts AI_TRANSCRIPT_MARK under stat['transcript'] without flagging unverified
        mod_path = Path(__file__).resolve().parents[1] / "scripts" / "06-upload" / "check_explanations.py"
        spec = importlib.util.spec_from_file_location("check_explanations_mod", mod_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        stat, bad = mod.audit([{"unit_id": "q-1", "explanation": row["explanation"], "articleSummary": "无"}], source="")
        self.assertEqual(stat["transcript"], 1)
        self.assertEqual(stat["unverified"], 0)
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
