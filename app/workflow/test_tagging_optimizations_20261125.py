# -*- coding: utf-8 -*-
"""2026-11-25 知识点标注与一审优化全链路回归测试。

覆盖：
1. 完成句子：knowledge 依赖 answers 并注入结构化答案，生产/一审共用「以答案推考点」规则；
2. 阅读理解：设题角度按小题落标（组级仅体裁+主题，子题逐题设题角度/阅读/*）、通读全文判文体、
   一审逐小题比对（review_consensus）、逐小题标签合并（label_merge）、工作台投影、上传透传与聚合；
3. 选词填空：整题（group）仅答案考点（目录 knowledge=[]、直标免模型、派生词汇/考察词汇/<词>）；
   单题（standalone）删除主题和语篇体裁（仅保留词汇/语法，目录树与目录包物理排除主题/体裁）；
4. 听力信息转换：仅保留主题、语篇体裁和答案考点（separate=true），禁止设题角度与目录词汇/语法；
5. 原标签和一审细致度：取消一审 30–60 字上限，收紧重型阅读题组分片上限（每片至多 2 组）。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workbench import review_views, taxonomy
from workflow import (
    agent_roles,
    agent_tasks,
    catalog_packs,
    contracts,
    knowledge_direct,
    label_merge,
    manifest as wf_manifest,
    review,
    review_consensus,
    review_rounds,
    unit_tasks,
    upload_payload,
)


class TaggingOptimizations20261125Test(unittest.TestCase):
    # ------------------------------------------------------------ 1. 完成句子
    def test_sentence_completion_depends_on_answers_and_uses_answer_driven_rule(self):
        from workflow import quarantine_policy
        # knowledge 不再在阶段图上硬阻塞等 answers，改为按题型软依赖：仅含完成句子且 answers 尚未终态时先等答案。
        self.assertNotIn("answers", contracts.STAGE_BY_ID["knowledge"].depends_on)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "03_types").mkdir(parents=True)
            (root / "03_types/types.json").write_text(
                json.dumps({"items": [{"unit_id": "q-1", "type": "单项填空"}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            self.assertFalse(quarantine_policy.knowledge_should_wait_answers(root, {"stages": {"answers": {"status": "pending"}}}))
            (root / "03_types/types.json").write_text(
                json.dumps({"items": [{"unit_id": "q-1", "type": "完成句子"}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            self.assertTrue(quarantine_policy.knowledge_should_wait_answers(root, {"stages": {"answers": {"status": "pending"}}}))
            self.assertFalse(quarantine_policy.knowledge_should_wait_answers(root, {"stages": {"answers": {"status": "completed"}}}))
            self.assertFalse(quarantine_policy.knowledge_should_wait_answers(root, {"stages": {"answers": {"status": "needs_review"}}}))
        rubric = agent_roles.knowledge_judgment_rules(["完成句子"])
        self.assertIn("以答案推考点", rubric)
        self.assertIn("严禁拿题干非空已有内容打标", rubric)

        rev_prompt = review_rounds._round_prompt("labels", ["u1"], json.dumps({
            "references/type-knowledge-map.json": {"by_type": {"完成句子": {"dims": []}}}
        }, ensure_ascii=False))
        self.assertIn("以答案推考点", rev_prompt)
        self.assertIn("完成句子须点明空格标准答案及由该答案反推的具体词汇/语法考点", rev_prompt)

    # ------------------------------------------------------------ 2. 阅读理解
    def test_reading_comprehension_group_level_angles_from_children_and_full_passage_genre(self):
        self.assertTrue(taxonomy.whole_unit_type("阅读理解"))

        # 所有组题标签全部标在组题级，子题 knowledge=[]；设题角度由各小题问法去重汇总到组题级
        valid_group = {
            "unit_kind": "group",
            "question_type": "阅读理解",
            "subtypes": ["选择3题"],
            "knowledge": [
                "语篇体裁/说明文/介绍类短文",
                "主题/人与自然/环境保护/绿色生活与环保行动",
                "设题角度/阅读/细节理解",
                "设题角度/阅读/猜测词义",
                "设题角度/阅读/最佳标题",
            ],
            "subquestions": [
                {"number": 1, "knowledge": []},
                {"number": 2, "knowledge": []},
                {"number": 3, "knowledge": []},
            ],
        }
        self.assertEqual(taxonomy.check_question_labels(valid_group), [])

        # 组题目录树包含 语篇体裁 + 主题 + 设题角度/阅读
        tree = taxonomy.knowledge_tree("阅读理解", unit_kind="group")
        self.assertEqual({n["name"] for n in tree["items"]}, {"语篇体裁", "主题", "设题角度"})

        # 共用规则与一审提示词均强调：设题角度标在组题级但来源于逐小题问法分析 + 通读全文判文体
        rubric = agent_roles.knowledge_judgment_rules(["阅读理解"])
        self.assertIn("语篇体裁必须通读全文判断，严禁只看首段或其中一小段", rubric)
        self.assertIn("设题角度仍标在组题级 knowledge（子题 knowledge=[]），但具体标签必须来源于逐小题的问法分析", rubric)
        rev_prompt = review_rounds._round_prompt("labels", ["u1"], json.dumps({
            "references/type-knowledge-map.json": {"by_type": {"阅读理解": {"dims": []}}}
        }, ensure_ascii=False))
        self.assertIn("全部标签（语篇体裁 + 主题 + 设题角度/阅读/*）标在组题级 assessment.knowledge，subquestions[i].knowledge 保持 []", rev_prompt)
        self.assertIn("阅读理解须先概括全文首中尾结构说明体裁与主题依据，再逐小题说明各题解题路径与对应设题角度", rev_prompt)

    def test_reading_group_level_consensus_and_label_merge_keep_children_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            stem = "A passage about green energy.\n1. What is green energy?\nA. Solar B. Coal\n2. What is the best title?\nA. Green B. Coal"
            (batch / "01_source").mkdir(parents=True)
            (batch / "03_types").mkdir(parents=True)
            (batch / "04_knowledge").mkdir(parents=True)
            (batch / "01_source_ans").mkdir(parents=True)
            (batch / "06_batch").mkdir(parents=True)
            (batch / "01_source/正文.txt").write_text(stem, encoding="utf-8")
            (batch / "01_source/structure.confirmed.json").write_text(json.dumps({
                "units": [{"unit_id": "g-read", "unit_kind": "group", "numbers": [1, 2], "stem": stem}]
            }, ensure_ascii=False), encoding="utf-8")
            (batch / "03_types/types.json").write_text(json.dumps({
                "items": [{"unit_id": "g-read", "type": "阅读理解", "subtypes": ["选择3题"]}]
            }, ensure_ascii=False), encoding="utf-8")
            orig_kp = [
                "语篇体裁/说明文/介绍类短文",
                "主题/人与自然/环境保护/绿色生活与环保行动",
                "设题角度/阅读/细节理解",
                "设题角度/阅读/主旨大意",
            ]
            (batch / "04_knowledge/knowledge.json").write_text(json.dumps({
                "items": [{
                    "unit_id": "g-read",
                    "knowledge": orig_kp,
                    "subquestions": [
                        {"number": "1", "knowledge": []},
                        {"number": "2", "knowledge": []},
                    ],
                }]
            }, ensure_ascii=False), encoding="utf-8")
            (batch / "01_source_ans/answers.json").write_text(json.dumps({
                "answers": {"1": "A", "2": "A"}, "difficulty": {"1": "容易", "2": "适中"}
            }, ensure_ascii=False), encoding="utf-8")
            (batch / "04_knowledge/explanations.json").write_text(json.dumps({
                "items": [{"unit_id": "g-read", "explanation": "无", "articleSummary": "无"}]
            }, ensure_ascii=False), encoding="utf-8")
            (batch / "06_batch/batch.json").write_text(json.dumps({"items": []}), encoding="utf-8")

            plan = review.prepare_round_plan(batch)
            r1_assessment = {
                "type": "阅读理解",
                "subtypes": ["选择3题"],
                "knowledge": [
                    "语篇体裁/说明文/介绍类短文",
                    "主题/人与自然/环境保护/绿色生活与环保行动",
                    "设题角度/阅读/细节理解",
                    "设题角度/阅读/最佳标题",
                ],
                "subquestions": [
                    {"number": "1", "knowledge": []},
                    {"number": "2", "knowledge": []},
                ],
                "difficulty": [{"number": "1", "value": "容易"}, {"number": "2", "value": "适中"}],
            }
            record = {
                "round_id": "labels",
                "input_binding": plan["input_binding"],
                "execution_id": "r1-exec",
                "checks": [{
                    "unit_id": "g-read",
                    "verdict": "ok",
                    "rationale": "全文说明绿色能源，第1题细节理解，第2题最佳标题，汇总在组级。",
                    "evidence": [{"path": "01_source/正文.txt", "locator": "L1", "quote": "A passage about green energy."}],
                    "assessment": r1_assessment,
                }],
            }
            findings = review_consensus.compare(batch, plan, [record])
            self.assertEqual(len(findings), 1, findings)
            self.assertEqual(findings[0]["field"], "knowledge")

            merged = label_merge.merged_unit_knowledge(
                batch, "g-read", orig_kp,
                main_type="阅读理解", manual_labels={}, round1_assessment=r1_assessment, is_group=True,
            )
            self.assertIn("设题角度/阅读/最佳标题", merged["knowledge"])
            self.assertIn("设题角度/阅读/主旨大意", merged["knowledge"])

    # ------------------------------------------------------------ 3. 选词填空
    def test_word_choice_cloze_group_vs_standalone_dimensions_and_direct_tagging(self):
        # 整题（group）：仅答案考点
        self.assertTrue(taxonomy.answer_point_only_type("选词填空", "group"))
        self.assertTrue(taxonomy.has_answer_point_dim("选词填空", "group"))
        self.assertEqual(taxonomy._allowed_prefixes("选词填空", unit_kind="group"), [])
        self.assertEqual(catalog_packs.packs_for_type("选词填空", unit_kind="group"), [])
        self.assertEqual(catalog_packs.bucket_for_type("选词填空", unit_kind="group"), "answer_point_only")

        group_tree = taxonomy.knowledge_tree("选词填空", unit_kind="group")
        self.assertTrue(group_tree["answer_point_only"])
        self.assertEqual(group_tree["selectable_total"], 0)

        # 整题目录 knowledge=[] 合法，挂主题/体裁/词汇均报错
        ok_group = {
            "unit_kind": "group",
            "question_type": "选词填空",
            "subtypes": ["短文"],
            "knowledge": [],
            "subquestions": [{"number": 1, "knowledge": []}, {"number": 2, "knowledge": []}],
        }
        self.assertEqual(taxonomy.check_question_labels(ok_group), [])
        bad_group = dict(ok_group, knowledge=["语篇体裁/记叙文/故事", "主题/人与自我/生活与学习/日常活动"])
        self.assertTrue(taxonomy.check_question_labels(bad_group))
        # 规则 51：选词填空整题不能标「单句」，单题必须标「单句」且不能标「多句/短文」
        self.assertTrue(any("单句" in p for p in taxonomy.check_question_labels(dict(ok_group, subtypes=["单句"]))))

        # 单题（standalone）：删除主题和语篇体裁，只保留词汇/语法，不启用答案考点
        self.assertFalse(taxonomy.answer_point_only_type("选词填空", "standalone"))
        self.assertFalse(taxonomy.has_answer_point_dim("选词填空", "standalone"))
        self.assertEqual(taxonomy._allowed_prefixes("选词填空", unit_kind="standalone"), ["词汇", "语法"])
        self.assertEqual(catalog_packs.packs_for_type("选词填空", unit_kind="standalone"), ["vocab-full", "grammar"])
        standalone_tree = taxonomy.knowledge_tree("选词填空", unit_kind="standalone")
        self.assertEqual({n["name"] for n in standalone_tree["items"]}, {"词汇", "语法"})

        ok_standalone = {
            "unit_kind": "standalone",
            "question_type": "选词填空",
            "subtypes": ["单句"],
            "knowledge": ["词汇/词汇辨析/动词辨析"],
            "subquestions": [],
        }
        self.assertEqual(taxonomy.check_question_labels(ok_standalone), [])
        bad_standalone = dict(ok_standalone, knowledge=["主题/人与自我/生活与学习/日常活动"])
        self.assertTrue(taxonomy.check_question_labels(bad_standalone))
        self.assertTrue(any("单句" in p for p in taxonomy.check_question_labels(dict(ok_standalone, subtypes=["多句"]))))
        self.assertTrue(any("单句" in p for p in taxonomy.check_question_labels(dict(ok_standalone, subtypes=["短文"]))))

        # 整题确定性直标 + 上传答案考点自动派生
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            (batch / "01_source").mkdir(parents=True)
            (batch / "03_types").mkdir(parents=True)
            (batch / "01_source_ans").mkdir(parents=True)
            (batch / "06_batch").mkdir(parents=True)
            (batch / "workflow").mkdir(parents=True)
            stem = "Box: happy, run\n1. She is ___.\n2. They ___ fast."
            (batch / "01_source/正文.txt").write_text(stem, encoding="utf-8")
            units = [{"unit_id": "u-wc", "unit_kind": "group", "numbers": [1, 2], "stem": stem}]
            (batch / "01_source/structure.confirmed.json").write_text(json.dumps({"units": units}), encoding="utf-8")
            (batch / "03_types/types.json").write_text(json.dumps({
                "items": [{"unit_id": "u-wc", "type": "选词填空", "subtypes": ["短文"]}]
            }, ensure_ascii=False), encoding="utf-8")
            direct = knowledge_direct.tag_units(batch, units)
            self.assertIn("u-wc", direct)
            self.assertEqual(direct["u-wc"]["knowledge"], [])

            # 答案派生：选词填空整题归入 unit-scoped 答案考点
            self.assertIn("选词填空", upload_payload._ANSWER_POINT_UNIT_SCOPED_TYPES)
            (batch / "01_source_ans/answers.json").write_text(json.dumps({
                "answers": {"1": "happy", "2": "run"}, "missing": {}
            }), encoding="utf-8")
            batch_doc = {
                "items": [{
                    "unit_id": "u-wc", "unit_kind": "group", "question_type": "选词填空",
                    "filterMetadata": {"subtypes": ["短文"]},
                    "material": "Box: happy, run", "knowledge": [], "numbers": [1, 2],
                    "subquestions": [
                        {"number": 1, "stem": "1. She is ___.", "answer": "happy", "knowledge": []},
                        {"number": 2, "stem": "2. They ___ fast.", "answer": "run", "knowledge": []},
                    ],
                }]
            }
            (batch / "06_batch/batch_meta.json").write_text(json.dumps(batch_doc, ensure_ascii=False), encoding="utf-8")
            mf_state = wf_manifest.new_manifest(
                batch.name, str(batch),
                {"source": {"path": str(batch / "01_source/正文.txt"), "exists": True}},
                {},
            )
            wf_manifest.touch_stage(
                mf_state, "answers", wf_manifest.COMPLETED,
                output_hashes={"01_source_ans/answers.json": wf_manifest.sha256_file(batch / "01_source_ans/answers.json")},
            )
            wf_manifest.save(batch, mf_state)
            with patch.object(wf_manifest, "is_stale", return_value=(False, "")):
                self.assertEqual(review_views.answer_point_tags(batch, "u-wc"), ["happy", "run"])
                projected = upload_payload._project_answer_point_tags(batch_doc, batch)
                self.assertEqual(projected["items"][0]["knowledge"], ["词汇/考察词汇/happy", "词汇/考察词汇/run"])

    # ------------------------------------------------------------ 4. 听力信息转换
    def test_listening_info_transfer_keeps_only_theme_genre_and_separate_answer_points(self):
        self.assertEqual(taxonomy._allowed_prefixes("听力信息转换"), ["语篇体裁", "主题"])
        self.assertTrue(taxonomy.has_answer_point_dim("听力信息转换"))
        self.assertEqual(catalog_packs.packs_for_type("听力信息转换"), ["theme", "genre"])
        self.assertEqual(catalog_packs.bucket_for_type("听力信息转换"), "discourse")

        tree = taxonomy.knowledge_tree("听力信息转换")
        self.assertEqual({n["name"] for n in tree["items"]}, {"语篇体裁", "主题"})

        ok = {
            "unit_kind": "group",
            "question_type": "听力信息转换",
            "subtypes": [],
            "knowledge": ["语篇体裁/日常口语/独白", "主题/人与自我/生活与学习/校园活动"],
            "subquestions": [{"number": 16, "knowledge": []}],
        }
        self.assertEqual(taxonomy.check_question_labels(ok), [])
        # 设题角度/听力 和 目录词汇/语法 必须报错
        for forbidden in ("设题角度/听力/细节理解", "词汇/拼写与词形", "语法/名词/名词的数"):
            bad = dict(ok, knowledge=ok["knowledge"] + [forbidden])
            self.assertTrue(taxonomy.check_question_labels(bad), forbidden)

    # ------------------------------------------------------------ 5. 分片细致度
    def test_heavy_reading_groups_capped_at_two_per_shard(self):
        _, leaves = taxonomy.knowledge_index()
        units = [{"unit_id": f"r-{i}", "numbers": [i * 5 - 4, i * 5], "unit_kind": "group"} for i in range(1, 6)]
        context = {
            "confirmed_units_with_stable_ids": units,
            "01_source/正文.txt": "\n".join(f"Passage {i}." for i in range(1, 6)),
            agent_tasks.ARTIFACTS["types"]: {
                "items": [{"unit_id": f"r-{i}", "type": "阅读理解", "subtypes": ["选择5题"]} for i in range(1, 6)]
            },
            "references/knowledge-catalog.json": {"paths": sorted(leaves)},
            "references/type-knowledge-map.json": {"_comment": "map", "by_type": {"阅读理解": {"dims": []}}},
        }
        base = {
            "prompt": "header\n" + unit_tasks._BEGIN + json.dumps(context, ensure_ascii=False) + unit_tasks._END,
            "preconditions": [],
            "input_hashes": {"source": "h"},
        }
        with patch.object(agent_tasks, "build_task", return_value=base):
            _, parts = unit_tasks.build_parts("unused", "knowledge", 600000, max_units=8, balance_work=True)
        # 5 篇阅读理解必须拆分为每片至多 2 篇（2 + 2 + 1）
        self.assertTrue(all(len(p["unit_ids"]) <= 2 for p in parts), [p["unit_ids"] for p in parts])
        self.assertEqual(sum(len(p["unit_ids"]) for p in parts), 5)

    def test_review_splits_heavy_reading_and_writing_to_one_unit_per_shard(self):
        # 一审中长阅读（reading）与书面表达/完形（discourse）每片 1 单元并行盲审，避免 480s 超时
        _, leaves = taxonomy.knowledge_index()
        units = [
            {"unit_id": "w-1", "numbers": [1], "unit_kind": "standalone", "stem": "Write an essay 1."},
            {"unit_id": "w-2", "numbers": [2], "unit_kind": "standalone", "stem": "Write an essay 2."},
            {"unit_id": "r-1", "numbers": [3, 4], "unit_kind": "group", "stem": "Passage 1."},
            {"unit_id": "r-2", "numbers": [5, 6], "unit_kind": "group", "stem": "Passage 2."},
        ]
        context = {
            "confirmed_units_with_stable_ids": units,
            "01_source/正文.txt": "Write an essay 1.\nWrite an essay 2.\nPassage 1.\nPassage 2.",
            "03_types/types.json": {
                "items": [
                    {"unit_id": "w-1", "type": "书面表达", "subtypes": ["列中文要点"]},
                    {"unit_id": "w-2", "type": "书面表达", "subtypes": ["思维导图"]},
                    {"unit_id": "r-1", "type": "阅读理解", "subtypes": ["选择4题"]},
                    {"unit_id": "r-2", "type": "任务型阅读", "subtypes": ["阅读问答"]},
                ]
            },
            "04_knowledge/knowledge.json": {"items": [{"unit_id": u["unit_id"], "knowledge": []} for u in units]},
            "06_batch/batch_meta.json": {"items": [{"unit_id": u["unit_id"], "stem": u["stem"]} for u in units]},
            "references/knowledge-catalog.json": json.loads(taxonomy.KNOWLEDGE_PATH.read_text(encoding="utf-8-sig")),
            "references/type-knowledge-map.json": json.loads(taxonomy.TYPE_KNOWLEDGE_PATH.read_text(encoding="utf-8-sig")),
        }
        base = {
            "prompt": "header\n" + unit_tasks._BEGIN + json.dumps(context, ensure_ascii=False) + unit_tasks._END,
            "preconditions": [],
            "input_hashes": {},
        }
        parts = review_rounds._partition_contexts(base, 600000, max_units=5, round_id="labels")
        self.assertEqual([p["unit_ids"] for p in parts], [["w-1"], ["w-2"], ["r-1"], ["r-2"]])

    # ------------------------------------------------------------ 6. 全部组题仅在组题级落标
    def test_any_group_rejects_child_knowledge_in_tagging_review_and_merge(self):
        # 即使非 WHOLE_UNIT_TYPES（例如语音题/单词拼写以组题形态出现），标签也一律只在组题级，不在子题呈现
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            unit = {"unit_id": "g-ph", "unit_kind": "group", "numbers": [1, 2], "stem": "辨音题"}
            ctx = {
                agent_tasks.ARTIFACTS["types"]: {
                    "items": [{"unit_id": "g-ph", "type": "语音题", "subtypes": [], "evidence": ["L1"]}],
                },
            }
            p1 = "语音/基本读音/常见元音字母组合读音"
            p2 = "语音/基本读音/常见辅音连缀读音"
            bad_kn = {
                "items": [{
                    "unit_id": "g-ph",
                    "knowledge": [p1],
                    "subquestions": [
                        {"number": 1, "knowledge": [p1]},
                        {"number": 2, "knowledge": []},
                    ],
                    "evidence": ["L1"],
                }]
            }
            with self.assertRaisesRegex(ValueError, "Group knowledge belongs to the group"):
                agent_tasks._validate(batch, "knowledge", bad_kn, ctx, [unit])

            # 合法：全部标签在组级，子题 knowledge=[]
            good_kn = {
                "items": [{
                    "unit_id": "g-ph",
                    "knowledge": [p1],
                    "subquestions": [
                        {"number": 1, "knowledge": []},
                        {"number": 2, "knowledge": []},
                    ],
                    "evidence": ["L1"],
                }]
            }
            agent_tasks._validate(batch, "knowledge", good_kn, ctx, [unit])

            # label_merge.project_batch_merged_knowledge 不会向任何组题的子题合并或残留标签
            from workflow import label_merge
            (batch / "07_review").mkdir(parents=True, exist_ok=True)
            (batch / "07_review/review-rounds.json").write_text(json.dumps({
                "rounds": [{
                    "round_id": "labels",
                    "checks": [{
                        "unit_id": "g-ph",
                        "assessment": {
                            "knowledge": [p2],
                            "subquestions": [
                                {"number": "1", "knowledge": [p2]},
                                {"number": "2", "knowledge": []},
                            ],
                        },
                    }],
                }]
            }, ensure_ascii=False), encoding="utf-8")
            legacy_batch = {
                "items": [{
                    "unit_id": "g-ph",
                    "unit_kind": "group",
                    "type": "语音题",
                    "knowledge": [p1],
                    "subquestions": [
                        {"number": 1, "knowledge": [p1]},
                        {"number": 2, "knowledge": []},
                    ],
                }]
            }
            projected = label_merge.project_batch_merged_knowledge(legacy_batch, batch)
            self.assertEqual(projected["items"][0]["knowledge"], [p1, p2])
            self.assertEqual([c["knowledge"] for c in projected["items"][0]["subquestions"]], [[], []])


if __name__ == "__main__":
    unittest.main()
