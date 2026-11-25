"""Unit tests for deterministic knowledge-tag merge (「标签合并」)."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from workbench import taxonomy
from workflow import label_audit, label_merge, review

KP_A = "主题/人与自我/生活与学习/日常起居"
KP_B = "主题/人与自我/生活与学习/日常活动"


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class LabelMergeTests(unittest.TestCase):
    def test_round_ids_only_labels_and_sample_is_legacy(self):
        self.assertEqual(review.ROUND_IDS, ("labels",))
        self.assertIn("sample", review.LEGACY_ROUND_IDS)

    def test_merge_knowledge_lists_unions_deduplicates_and_filters_invalid(self):
        merged, added = label_merge.merge_knowledge_lists(
            [KP_A],
            [KP_A, KP_B, "不存在的目录/编造知识点"],
            main_type="阅读理解",
        )
        self.assertEqual(merged, [KP_A, KP_B])
        self.assertEqual(added, [KP_B])

    def test_merge_filters_out_disallowed_dim_for_type(self):
        # 单项填空不允许「主题」维度，合并时应过滤掉不合法维度，避免上传预检失败
        merged, added = label_merge.merge_knowledge_lists(
            [],
            [KP_B],
            main_type="单项填空",
        )
        self.assertEqual(merged, [])
        self.assertEqual(added, [])

    def test_manual_unit_labels_override_auto_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch_dir = Path(tmp)
            _write_json(
                batch_dir / "07_review/review-rounds.json",
                {
                    "rounds": [
                        {
                            "round_id": "labels",
                            "checks": [
                                {
                                    "unit_id": "q-1",
                                    "assessment": {"knowledge": [KP_B]},
                                }
                            ],
                        }
                    ]
                },
            )
            auto = label_merge.merged_unit_knowledge(
                batch_dir, "q-1", [KP_A], main_type="阅读理解"
            )
            self.assertFalse(auto["manually_edited"])
            self.assertEqual(auto["knowledge"], [KP_A, KP_B])

            # Save manual edit to candidates/units/q-1.json -> must take precedence.
            _write_json(
                batch_dir / "candidates/units/q-1.json",
                {"unit_id": "q-1", "knowledge": [KP_A]},
            )
            manual = label_merge.merged_unit_knowledge(
                batch_dir, "q-1", [KP_A], main_type="阅读理解"
            )
            self.assertTrue(manual["manually_edited"])
            self.assertEqual(manual["knowledge"], [KP_A])

    def test_project_batch_merged_knowledge_only_updates_knowledge_and_syncs_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch_dir = Path(tmp)
            _write_json(
                batch_dir / "07_review/review-rounds.json",
                {
                    "rounds": [
                        {
                            "round_id": "labels",
                            "checks": [
                                {
                                    "unit_id": "q-1",
                                    "assessment": {
                                        "type": "完形填空",  # must NOT overwrite original type
                                        "difficulty": "困难",  # must NOT overwrite original difficulty
                                        "knowledge": [KP_B],
                                    },
                                }
                            ],
                        }
                    ]
                },
            )
            id_a = taxonomy.resolve_knowledge([KP_A], "阅读理解")["resolved"][0]["id"]
            id_b = taxonomy.resolve_knowledge([KP_B], "阅读理解")["resolved"][0]["id"]
            genre = "语篇体裁/其他语篇/剧本"
            id_g = taxonomy.resolve_knowledge([genre], "阅读理解")["resolved"][0]["id"]
            batch = {
                "items": [
                    {
                        "unit_id": "q-1",
                        "type": "阅读理解",
                        "difficulty": "容易",
                        "filterMetadata": {"subtypes": ["选择4题"]},
                        "knowledge": [genre, KP_A],
                        "knowledgeIds": [id_g, id_a],
                        "knowledge_ids": [id_g, id_a],
                    }
                ]
            }
            projected = label_merge.project_batch_merged_knowledge(batch, batch_dir)
            item = projected["items"][0]
            self.assertEqual(item["type"], "阅读理解")
            self.assertEqual(item["difficulty"], "容易")
            self.assertEqual(item["filterMetadata"]["subtypes"], ["选择4题"])
            self.assertEqual(item["knowledge"], [genre, KP_A, KP_B])
            self.assertEqual(item["knowledgeIds"], [id_g, id_a, id_b])
            self.assertEqual(item["knowledge_ids"], [id_g, id_a, id_b])
            audit = label_audit.inspect_batch(projected)
            self.assertTrue(audit["passed"], audit["errors"])

    def test_current_round1_assessment_loads_with_real_round_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch_dir = Path(tmp)
            source = batch_dir / "01_source/正文.txt"
            source.parent.mkdir()
            source.write_text("1. What does she read?\n", encoding="utf-8")
            _write_json(batch_dir / "01_source/structure.confirmed.json", {
                "units": [{"unit_id": "q-1", "unit_kind": "standalone", "numbers": [1], "stem": "1. What does she read?", "source_span": {"start_line": 1, "end_line": 1}}],
            })
            _write_json(batch_dir / "03_types/types.json", {
                "items": [{"unit_id": "q-1", "type": "阅读理解"}],
            })
            _write_json(batch_dir / "04_knowledge/knowledge.json", {
                "items": [{"unit_id": "q-1", "knowledge": [KP_A]}],
            })
            batch = {"items": [{"unit_id": "q-1", "type": "阅读理解", "knowledge": [KP_A]}]}
            _write_json(batch_dir / "06_batch/batch_meta.json", batch)
            plan = review.prepare_round_plan(batch_dir)
            # Incomplete Round 1 evidence (missing execution_id/verdict/evidence) must be rejected.
            _write_json(batch_dir / "07_review/review-rounds.json", {
                "plan": plan,
                "rounds": [{"round_id": "labels", "input_binding": plan["input_binding"],
                            "checks": [{"unit_id": "q-1", "assessment": {"knowledge": [KP_B]}}]}],
            })
            self.assertEqual(label_merge.load_round1_assessments(batch_dir), {})
            # Complete, source-verified Round 1 evidence is accepted (and rejected when task binding is missing).
            from unittest import mock
            valid_check = {
                "unit_id": "q-1", "verdict": "ok",
                "rationale": "核对原文题干与目录标签一致",
                "evidence": [{"path": "01_source/正文.txt", "locator": "L1", "quote": "What does she read?"}],
                "assessment": {"type": "阅读理解", "subtypes": [], "knowledge": [KP_B], "subquestions": []},
            }
            _write_json(batch_dir / "07_review/review-rounds.json", {
                "plan": plan,
                "rounds": [{"round_id": "labels", "execution_id": "round-" + "a" * 32,
                            "input_binding": plan["input_binding"],
                            "checks": [valid_check]}],
            })
            # Without real binding/receipt on disk, _verify_round_sources rejects it.
            self.assertEqual(label_merge.load_round1_assessments(batch_dir), {})
            # Once source binding verification succeeds, Round 1 assessment is loaded and merged.
            with mock.patch.object(review, "_verify_round_sources", return_value=None):
                self.assertEqual(label_merge.load_round1_assessments(batch_dir)["q-1"]["knowledge"], [KP_B])
                projected = label_merge.project_batch_merged_knowledge(batch, batch_dir)
            self.assertEqual(projected["items"][0]["knowledge"], [KP_A, KP_B])
            self.assertEqual(batch["items"][0]["knowledge"], [KP_A])

    def test_stale_round1_unit_binding_is_rejected_while_valid_unit_is_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch_dir = Path(tmp)
            source = batch_dir / "01_source/正文.txt"
            source.parent.mkdir(parents=True)
            source.write_text(
                "1. What does she read?\n2. Where does he go?\n",
                encoding="utf-8",
            )
            _write_json(batch_dir / "01_source/structure.confirmed.json", {
                "units": [
                    {"unit_id": "q-1", "unit_kind": "standalone", "numbers": [1], "stem": "1. What does she read?", "source_span": {"start_line": 1, "end_line": 1}},
                    {"unit_id": "q-2", "unit_kind": "standalone", "numbers": [2], "stem": "2. Where does he go?", "source_span": {"start_line": 2, "end_line": 2}},
                ],
            })
            _write_json(batch_dir / "03_types/types.json", {
                "items": [
                    {"unit_id": "q-1", "type": "阅读理解"},
                    {"unit_id": "q-2", "type": "阅读理解"},
                ],
            })
            _write_json(batch_dir / "04_knowledge/knowledge.json", {
                "items": [
                    {"unit_id": "q-1", "knowledge": [KP_A]},
                    {"unit_id": "q-2", "knowledge": [KP_A]},
                ],
            })
            id_a = taxonomy.resolve_knowledge([KP_A], "阅读理解")["resolved"][0]["id"]
            id_b = taxonomy.resolve_knowledge([KP_B], "阅读理解")["resolved"][0]["id"]
            batch = {
                "items": [
                    {
                        "unit_id": "q-1",
                        "type": "阅读理解",
                        "stem": "What does she read?",
                        "knowledge": [KP_A],
                        "knowledgeIds": [id_a],
                    },
                    {
                        "unit_id": "q-2",
                        "type": "阅读理解",
                        "stem": "Where does he go?",
                        "knowledge": [KP_A],
                        "knowledgeIds": [id_a],
                    },
                ]
            }
            _write_json(batch_dir / "06_batch/batch_meta.json", batch)

            # Freeze a real Round 1 plan (real SHA-256 string unit_bindings).
            old_plan = review.prepare_round_plan(batch_dir)
            _write_json(
                batch_dir / "07_review/review-rounds.json",
                {
                    "plan": old_plan,
                    "rounds": [
                        {
                            "round_id": "labels",
                            "execution_id": "round-" + "b" * 32,
                            "input_binding": old_plan["input_binding"],
                            "checks": [
                                {"unit_id": "q-1", "verdict": "ok", "rationale": "核对原文第1题",
                                 "evidence": [{"path": "01_source/正文.txt", "locator": "L1", "quote": "What does she read?"}],
                                 "assessment": {"type": "阅读理解", "subtypes": [], "knowledge": [KP_B], "subquestions": []}},
                                {"unit_id": "q-2", "verdict": "ok", "rationale": "核对原文第2题",
                                 "evidence": [{"path": "01_source/正文.txt", "locator": "L2", "quote": "Where does he go?"}],
                                 "assessment": {"type": "阅读理解", "subtypes": [], "knowledge": [KP_B], "subquestions": []}},
                            ],
                        }
                    ],
                },
            )
            # Even if an older comparison snapshot exists, stale review-rounds.json must not fall back to it.
            _write_json(
                batch_dir / "07_review/review-comparison.json",
                {
                    "input_binding": old_plan["input_binding"],
                    "rounds": [
                        {
                            "round_id": "labels",
                            "checks": [
                                {"unit_id": "q-1", "assessment": {"knowledge": [KP_B]}},
                            ],
                        }
                    ],
                },
            )

            # Mutate ONLY q-1 in batch_meta.json; q-2 remains unchanged.
            batch["items"][0]["stem"] = "What does she write after dinner?"
            _write_json(batch_dir / "06_batch/batch_meta.json", batch)

            # For partial rebase where q-1 changed and q-2 is unchanged, simulate verified rebase of surviving q-2 check.
            from unittest import mock
            new_plan = review.prepare_round_plan(batch_dir)
            rebased_payload = {
                "plan": new_plan,
                "rounds": [{
                    "round_id": "labels", "execution_id": "round-" + "b" * 32,
                    "input_binding": new_plan["input_binding"],
                    "checks": [{
                        "unit_id": "q-2", "verdict": "ok", "rationale": "核对原文第2题",
                        "evidence": [{"path": "01_source/正文.txt", "locator": "L2", "quote": "Where does he go?"}],
                        "assessment": {"type": "阅读理解", "subtypes": [], "knowledge": [KP_B], "subquestions": []},
                    }],
                }],
            }
            with mock.patch.object(review, "rebase_rounds", return_value=rebased_payload), \
                 mock.patch.object(review, "_verify_round_sources", return_value=None):
                assessments = label_merge.load_round1_assessments(batch_dir)
                self.assertNotIn("q-1", assessments)
                self.assertIn("q-2", assessments)
                projected = label_merge.project_batch_merged_knowledge(batch, batch_dir)

            item1, item2 = projected["items"]
            # q-1 binding changed -> stale Round 1 tag must NOT leak into upload payload.
            self.assertEqual(item1["knowledge"], [KP_A])
            self.assertEqual(item1["knowledgeIds"], [id_a])
            # q-2 binding still matches -> real string digest must pass and merge normally.
            self.assertEqual(item2["knowledge"], [KP_A, KP_B])
            self.assertEqual(item2["knowledgeIds"], [id_a, id_b])

    def test_unverified_round1_without_plan_is_rejected_on_real_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch_dir = Path(tmp)
            source = batch_dir / "01_source/正文.txt"
            source.parent.mkdir(parents=True)
            source.write_text("1. What does she read?\n", encoding="utf-8")
            _write_json(batch_dir / "01_source/structure.confirmed.json", {
                "units": [{"unit_id": "q-1", "unit_kind": "standalone", "numbers": [1]}],
            })
            _write_json(batch_dir / "03_types/types.json", {
                "items": [{"unit_id": "q-1", "type": "阅读理解"}],
            })
            _write_json(batch_dir / "04_knowledge/knowledge.json", {
                "items": [{"unit_id": "q-1", "knowledge": [KP_A]}],
            })
            _write_json(batch_dir / "06_batch/batch_meta.json", {
                "items": [{"unit_id": "q-1", "type": "阅读理解", "knowledge": [KP_A]}],
            })
            _write_json(
                batch_dir / "07_review/review-rounds.json",
                {
                    "rounds": [
                        {
                            "round_id": "labels",
                            "checks": [
                                {"unit_id": "q-1", "assessment": {"knowledge": [KP_B]}},
                            ],
                        }
                    ]
                },
            )
            self.assertEqual(label_merge.load_round1_assessments(batch_dir), {})

    def test_load_round1_assessments_caches_and_invalidates_on_file_change(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            batch_dir = Path(tmp)
            rounds_path = batch_dir / "07_review/review-rounds.json"
            _write_json(rounds_path, {
                "rounds": [{"round_id": "labels", "checks": [{"unit_id": "q-1", "assessment": {"knowledge": [KP_A]}}]}],
            })
            with mock.patch.object(label_merge, "_validated_round1_checks", wraps=label_merge._validated_round1_checks) as spy:
                first = label_merge.load_round1_assessments(batch_dir)
                second = label_merge.load_round1_assessments(batch_dir)
                self.assertEqual(first["q-1"]["knowledge"], [KP_A])
                self.assertEqual(second["q-1"]["knowledge"], [KP_A])
                self.assertEqual(spy.call_count, 1)

                # Mutating the bound file invalidates the cache immediately.
                _write_json(rounds_path, {
                    "rounds": [{"round_id": "labels", "checks": [{"unit_id": "q-1", "assessment": {"knowledge": [KP_B]}}]}],
                })
                third = label_merge.load_round1_assessments(batch_dir)
                self.assertEqual(third["q-1"]["knowledge"], [KP_B])
                self.assertEqual(spy.call_count, 2)

    def test_manual_type_and_metadata_edits_project_into_upload_payload_without_rerun(self):
        from workbench import server
        from workflow import manifest as wf_manifest, upload_payload

        with tempfile.TemporaryDirectory() as tmp:
            batch_dir = Path(tmp).resolve()
            # 原始批次里是旧题型（其他），模拟人工在审核页修改成 单项填空/四个选项 + 合法知识点。
            batch = {
                "batch_id": "b-manual",
                "name": "人工改题型不重跑测试",
                "items": [{
                    "unit_id": "q-1",
                    "id": "q-1",
                    "unit_kind": "standalone",
                    "number": 1,
                    "numbers": [1],
                    "question_type": "其他",
                    "stem": "1. He ____ a student.",
                    "options": ["A. is", "B. are", "C. am", "D. be"],
                    "answer": "A",
                    "explanation": "主语第三人称单数用 is。",
                    "difficulty": "容易",
                    "filterMetadata": {"subtypes": [], "categories": [], "source": "测试卷", "source_type": "中考真题", "year": 2025},
                    "knowledge": ["词汇/词汇辨析/动词辨析"],
                    "subquestions": [],
                }],
            }
            _write_json(batch_dir / "06_batch/batch_meta.json", batch)
            _write_json(batch_dir / "01_source/structure.confirmed.json", {
                "units": [{
                    "unit_id": "q-1", "unit_kind": "standalone", "numbers": [1],
                    "type": "单项填空", "stem": "1. He ____ a student.",
                    "source_span": {"start_line": 1, "end_line": 1},
                }]
            })
            stages = {name: {"status": wf_manifest.COMPLETED, "input_hashes": {}, "output_hashes": {}}
                      for name in ("types", "answers", "knowledge", "assemble", "validate", "review", "format_gate")}
            _write_json(batch_dir / "workflow/manifest.json", {"stages": stages, "status": "completed"})

            # 非法主类直接拒绝保存
            with self.assertRaisesRegex(ValueError, "不在目录中"):
                server.Handler.save_units(batch_dir, "q-1", [], {"type": "不存在的编造题型", "subtypes": []})

            # 合法改题型/子类/难度/知识点：保存后零阶段失效，且上传 payload 严格按新题型和装配结构生效
            new_kp = "语法/动词/系动词"
            if new_kp not in taxonomy.knowledge_index()[1]:
                new_kp = next(p for p in sorted(taxonomy.knowledge_index()[1]) if p.startswith("语法/"))
            server.Handler.save_units(batch_dir, "q-1", [], {
                "type": "单项填空",
                "subtypes": ["四个选项"],
                "difficulty": "适中",
                "categories": ["优选题"],
                "knowledge": [new_kp],
            })
            manifest_after = wf_manifest.load(batch_dir)
            for name in ("types", "answers", "knowledge", "assemble", "validate", "review", "format_gate"):
                self.assertEqual(manifest_after["stages"][name]["status"], wf_manifest.COMPLETED, name)

            built = upload_payload.build_payload(batch, catalog=None, batch_dir=batch_dir)
            non_catalog_errors = [e for e in built["errors"] if e.get("code") != "CATALOG_REQUIRED"]
            self.assertEqual(non_catalog_errors, [], non_catalog_errors)
            up_item = built["payload"]["items"][0]
            self.assertEqual(up_item["type"], "单项填空")
            self.assertEqual(up_item["filterMetadata"]["subtypes"], ["四个选项"])
            self.assertEqual(up_item["filterMetadata"]["categories"], ["优选题"])
            self.assertEqual(up_item["difficulty"], "中")
            self.assertEqual(up_item["knowledge"], [new_kp])


if __name__ == "__main__":
    unittest.main()
