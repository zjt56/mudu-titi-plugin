# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
from pathlib import Path

from workflow import ai_supplement, upload_content


class AiSupplementTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.batch = Path(self._tmp.name)
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


if __name__ == "__main__":
    unittest.main()
