import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import agent_tasks, local_agent, unit_tasks


class UnitTasksTest(unittest.TestCase):
    def setUp(self):
        self.units = [{"unit_id": "q-1", "numbers": [1]}, {"unit_id": "q-2", "numbers": [2]}]
        self.context = {"confirmed_units_with_stable_ids": self.units,
                        "01_source/正文.txt": "full source ending ORIGINAL-END",
                        agent_tasks.ARTIFACTS["knowledge"]: {"items": [
                            {"unit_id": "q-1", "explanation": "a" * 2000},
                            {"unit_id": "q-2", "explanation": "b" * 2000}]}}
        self.prompt = "header\n" + unit_tasks._BEGIN + json.dumps(self.context) + unit_tasks._END
        self.base = {"prompt": self.prompt, "preconditions": [], "input_hashes": {"source": "hash"}}

    def build(self):
        with patch.object(agent_tasks, "build_task", return_value=self.base):
            return unit_tasks.build_parts("unused", "review", len(self.prompt) - 500)

    def response(self, uid):
        return {"status": "completed", "notes": [], "findings": [], "artifacts": {
            agent_tasks.ARTIFACTS["review"]: {"findings": [], "checked_units": [uid], "unchecked": []}}}

    def test_partitions_keep_source_and_exact_coverage(self):
        base, parts = self.build()
        self.assertEqual(base["input_hashes"], {"source": "hash"})
        self.assertEqual([p["unit_ids"] for p in parts], [["q-1"], ["q-2"]])
        for part in parts:
            self.assertIn("ORIGINAL-END", part["prompt"])
        merged = unit_tasks.merge("review", parts, [self.response("q-1"), self.response("q-2")])
        self.assertEqual(merged["artifacts"][agent_tasks.ARTIFACTS["review"]]["checked_units"], ["q-1", "q-2"])

    def test_review_partitions_filter_batch_meta_and_preserve_metadata(self):
        self.context["06_batch/batch_meta.json"] = {"source": "original paper", "items": [
            {"id": "q-1", "stem": "first"}, {"unit_id": "q-2", "id": "q-2", "stem": "second"}]}
        self.base["prompt"] = "header\n" + unit_tasks._BEGIN + json.dumps(self.context) + unit_tasks._END
        _, parts = self.build()
        for part in parts:
            content = part["prompt"].split(unit_tasks._BEGIN, 1)[1].rsplit(unit_tasks._END, 1)[0]
            value = json.loads(content)["06_batch/batch_meta.json"]
            self.assertEqual(value["source"], "original paper")
            self.assertEqual([row.get("unit_id") or row["id"] for row in value["items"]], part["unit_ids"])
        self.context["06_batch/batch_meta.json"]["items"][1]["id"] = "wrong"
        self.base["prompt"] = "header\n" + unit_tasks._BEGIN + json.dumps(self.context) + unit_tasks._END
        with self.assertRaisesRegex(ValueError, "IDs disagree"):
            self.build()

    def test_partitions_drop_duplicated_extraction_fields(self):
        # 每个分片都重背整份版式是主要 token 开销：正文与 blocks 属于重复/噪声，
        # 表格、图片和警告是证据，必须原样留在分片里。
        self.context["01_source/extraction.json"] = {
            "text": "duplicated full source", "blocks": [{"text": "layout noise"}],
            "pages": [{"page": 1}], "tables": [{"rows": [["cell"]]}],
            "media": [{"path": "image1.jpeg", "kind": "question"}], "warnings": ["w"]}
        self.base["prompt"] = "header\n" + unit_tasks._BEGIN + json.dumps(self.context) + unit_tasks._END
        _, parts = self.build()
        for part in parts:
            content = part["prompt"].split(unit_tasks._BEGIN, 1)[1].rsplit(unit_tasks._END, 1)[0]
            value = json.loads(content)["01_source/extraction.json"]
            self.assertEqual(set(value), {"tables", "media", "warnings"})
            self.assertIn("image1.jpeg", part["prompt"])
            self.assertNotIn("duplicated full source", part["prompt"])
            self.assertNotIn("layout noise", part["prompt"])

    def test_assemble_partitions_drop_catalog_rules_but_review_keeps_them(self):
        # 组装的标签合法性由导入器程序校验，分片不必再背整份目录；审核仍需目录核对叶子。
        self.context["references/knowledge-catalog.json"] = {"points": [{"id": "EN-KP-x"}]}
        self.context["docs/knowledge-tagging.md"] = "标签规则正文"
        self.base["prompt"] = "header\n" + unit_tasks._BEGIN + json.dumps(self.context) + unit_tasks._END
        with patch.object(agent_tasks, "build_task", return_value=self.base):
            _, assemble = unit_tasks.build_parts("unused", "assemble", len(self.prompt) - 500)
            _, review = unit_tasks.build_parts("unused", "review", len(self.prompt) - 500)
        for part in assemble:
            self.assertNotIn("knowledge-catalog.json", part["prompt"])
            self.assertNotIn("标签规则正文", part["prompt"])
        for part in review:
            self.assertIn("knowledge-catalog.json", part["prompt"])
            self.assertIn("标签规则正文", part["prompt"])

    def test_configurable_max_units_limits_partition_size(self):
        with patch.object(agent_tasks, "build_task", return_value=self.base):
            _, parts = unit_tasks.build_parts("unused", "review", len(self.prompt) + 1000, max_units=1)
        self.assertEqual([part["unit_ids"] for part in parts], [["q-1"], ["q-2"]])
        with patch.object(agent_tasks, "build_task", return_value=self.base):
            with self.assertRaisesRegex(ValueError, "max_units"):
                unit_tasks.build_parts("unused", "review", len(self.prompt) + 1000, max_units=0)

    def test_rejects_wrong_or_duplicate_partition_coverage(self):
        _, parts = self.build()
        with self.assertRaises(ValueError):
            unit_tasks.merge("review", parts, [self.response("q-2"), self.response("q-2")])
        parts[1]["unit_ids"] = ["q-1"]
        with self.assertRaises(ValueError):
            unit_tasks.merge("review", parts, [self.response("q-1"), self.response("q-1")])

    def test_one_unit_over_budget_fails_without_truncation(self):
        with patch.object(agent_tasks, "build_task", return_value=self.base):
            with self.assertRaisesRegex(ValueError, "no source was truncated"):
                unit_tasks.build_parts("unused", "review", 100)

    def test_cli_invokes_parts_and_imports_once(self):
        self.base.update(stage="review", status="ready")
        limit = len(self.prompt) - 500
        calls = []

        def build(batch, stage, max_input_chars):
            if max_input_chars == limit:
                raise ValueError("review exceeding max_input_chars")
            return self.base

        def invoke(prompt, run_dir, config):
            calls.append(str(run_dir))
            return {"response": self.response("q-" + str(len(calls)))}

        with tempfile.TemporaryDirectory() as batch, \
             patch.object(local_agent, "load_config", return_value={"max_input_chars": limit}), \
             patch.object(local_agent, "_check_input_hashes"), \
             patch.object(local_agent, "invoke", side_effect=invoke), \
             patch.object(agent_tasks, "build_task", side_effect=build), \
             patch.object(agent_tasks, "accept_result", return_value={"completed": True}) as accept, \
             contextlib.redirect_stdout(io.StringIO()):
            code = local_agent.main(["--stage", "review", "--batch-dir", batch])
            self.assertEqual(code, 0)
            self.assertEqual(len(calls), 2)
            self.assertNotEqual(calls[0], calls[1])
            self.assertTrue(Path(calls[0]).parent.is_dir())
            self.assertEqual(accept.call_count, 1)
            self.assertEqual(accept.call_args.args[2]["artifacts"][agent_tasks.ARTIFACTS["review"]]["checked_units"], ["q-1", "q-2"])

    def test_cli_runs_parts_concurrently_and_keeps_merge_order(self):
        # 并发只压墙钟：分片完成顺序可以乱，合并仍按原分片顺序。
        import time
        self.base.update(stage="review", status="ready")
        limit = len(self.prompt) - 500

        def build(batch, stage, max_input_chars):
            if max_input_chars == limit:
                raise ValueError("review exceeding max_input_chars")
            return self.base

        def invoke(prompt, run_dir, config, *, permit_parallel=False):
            self.assertTrue(permit_parallel)
            time.sleep(0.3)
            uid = "q-1" if "part-0001" in str(run_dir) else "q-2"
            return {"response": self.response(uid)}

        with tempfile.TemporaryDirectory() as batch, \
             patch.object(local_agent, "load_config", return_value={"max_input_chars": limit, "part_concurrency": 2}), \
             patch.object(local_agent, "_check_input_hashes"), \
             patch.object(local_agent, "invoke", side_effect=invoke), \
             patch.object(agent_tasks, "build_task", side_effect=build), \
             patch.object(agent_tasks, "accept_result", return_value={"completed": True}) as accept, \
             contextlib.redirect_stdout(io.StringIO()):
            start = time.monotonic()
            code = local_agent.main(["--stage", "review", "--batch-dir", batch])
            elapsed = time.monotonic() - start
        self.assertEqual(code, 0)
        self.assertLess(elapsed, 0.5)  # 串行需要 0.6 秒以上
        self.assertEqual(accept.call_count, 1)
        merged = accept.call_args.args[2]["artifacts"][agent_tasks.ARTIFACTS["review"]]["checked_units"]
        self.assertEqual(merged, ["q-1", "q-2"])

    def test_cli_retries_only_invalid_part_with_feedback(self):
        self.base.update(stage="review", status="ready")
        limit = len(self.prompt) - 500
        calls = []

        def build(batch, stage, max_input_chars):
            if max_input_chars == limit:
                raise ValueError("review exceeding max_input_chars")
            return self.base

        def invoke(prompt, run_dir, config):
            calls.append((prompt, str(run_dir)))
            if len(calls) == 1:
                return {"response": self.response("q-2")}
            return {"response": self.response("q-1" if len(calls) == 2 else "q-2")}

        with tempfile.TemporaryDirectory() as batch, \
             patch.object(local_agent, "load_config", return_value={"max_input_chars": limit, "retries": 1}), \
             patch.object(local_agent, "_check_input_hashes"), \
             patch.object(local_agent, "invoke", side_effect=invoke), \
             patch.object(agent_tasks, "build_task", side_effect=build), \
             patch.object(agent_tasks, "accept_result", return_value={"completed": True}) as accept, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(local_agent.main(["--stage", "review", "--batch-dir", batch]), 0)
            self.assertEqual(len(calls), 3)
            self.assertIn("上一次分片结果未通过校验", calls[1][0])
            self.assertIn("part-0001-attempt-02", calls[1][1])
            self.assertIn("part-0002-attempt-01", calls[2][1])
            self.assertEqual(accept.call_count, 1)

    def test_proactive_limit_fifteen_units(self):
        self.context["confirmed_units_with_stable_ids"] = [
            {"unit_id": "q-%d" % n, "numbers": [n]} for n in range(1, 33)]
        self.context[agent_tasks.ARTIFACTS["knowledge"]] = {"items": [
            {"unit_id": "q-%d" % n} for n in range(1, 33)]}
        self.base["prompt"] = "header\n" + unit_tasks._BEGIN + json.dumps(self.context) + unit_tasks._END
        with patch.object(agent_tasks, "build_task", return_value=self.base):
            _, parts = unit_tasks.build_parts("unused", "review", 1000000)
        self.assertEqual([len(p["unit_ids"]) for p in parts], [15, 15, 2])

    def test_label_fallback_scopes_indexed_rows_but_keeps_all_original_evidence(self):
        # Missing/raw-unmatched stems force the conservative full-source binding.
        # Indexed draft rows must still stay local instead of duplicating the paper.
        self.context.update({
            "01_source/structure.confirmed.json": {"units": self.units},
            "01_source/structure.json": {"groups": [
                {"unit_id": "q-1", "numbers": [1], "stem": "selected structure"},
                {"unit_id": "q-2", "numbers": [2], "stem": "UNRELATED-STRUCTURE"}]},
            "06_batch/question-draft.json": {"input_hashes": {"whole": "digest"},
                "content_sha256": "whole digest", "items": [
                    {"unit_id": "q-1", "stem": "selected draft"},
                    {"unit_id": "q-2", "stem": "UNRELATED-DRAFT"}],
                "source_bindings": {"q-1": {"start_line": 10, "end_line": 14},
                                    "q-2": {"start_line": 20, "end_line": 30}},
                "issues": [{"unit_id": "q-2", "reason": "UNRELATED-ISSUE"},
                           {"reason": "global warning"}]},
            "incremental_gaps": {"unrelated": "UNRELATED-GAPS"},
            "01_source/附录.txt": "A篇【文章大意】 original summary\n1.【解析】 original explanation",
            "01_source/materials/attachment_001.txt": "Ambiguous section 2. original appendix",
            "01_source/pdf-ocr.json": {"sources": [{"text": "complete layout evidence"}]},
            agent_tasks.ARTIFACTS["answers"]: {"answers": {"1": "A", "2": "B"},
                "evidence": {"1": ["附录 L2"], "2": ["UNRELATED-ANSWER-EVIDENCE"]}},
        })
        self.base["prompt"] = "header\n" + unit_tasks._BEGIN + json.dumps(self.context) + unit_tasks._END
        for stage in ("types", "explanations", "knowledge"):
            with self.subTest(stage=stage), patch.object(agent_tasks, "build_task", return_value=self.base):
                base, parts = unit_tasks.build_parts("unused", stage, 1000000, max_units=1)
                prompt = parts[0]["prompt"]
                value = json.loads(prompt.split(unit_tasks._BEGIN, 1)[1].rsplit(unit_tasks._END, 1)[0])
                self.assertNotIn("cache_scope", parts[0])
                self.assertEqual(base["input_hashes"], {"source": "hash"})
                for marker in ("UNRELATED-STRUCTURE", "UNRELATED-DRAFT", "UNRELATED-ISSUE",
                               "UNRELATED-GAPS", "UNRELATED-ANSWER-EVIDENCE"):
                    self.assertNotIn(marker, prompt)
                for path in ("01_source/正文.txt", "01_source/附录.txt",
                             "01_source/materials/attachment_001.txt", "01_source/pdf-ocr.json"):
                    self.assertEqual(value[path], self.context[path])
                draft = value["06_batch/question-draft.json"]
                self.assertEqual(draft["source_bindings"], {"q-1": {"start_line": 10, "end_line": 14}})
                self.assertEqual(draft["issues"], [{"reason": "global warning"}])
                self.assertNotIn("input_hashes", draft)
                self.assertNotIn("content_sha256", draft)
                self.assertEqual(value[agent_tasks.ARTIFACTS["answers"]]["evidence"], {"1": ["附录 L2"]})

    def test_label_fallback_prompt_scales_with_selected_rows(self):
        def build(count, selected):
            units = [{"unit_id": "q-%d" % n, "numbers": [n]} for n in range(1, count + 1)]
            context = {"confirmed_units_with_stable_ids": units,
                       "01_source/正文.txt": "unknown ownership: keep complete source",
                       "01_source/structure.confirmed.json": {"units": units},
                       "06_batch/question-draft.json": {"items": [
                           {"unit_id": u["unit_id"], "stem": "text-%s-" % u["unit_id"] + "x" * 4000}
                           for u in units]}}
            base = dict(self.base, prompt="header\n" + unit_tasks._BEGIN + json.dumps(context) + unit_tasks._END)
            with patch.object(agent_tasks, "build_task", return_value=base):
                return unit_tasks.build_parts("unused", "explanations", 1000000, max_units=selected)[1][0]["prompt"]
        one = build(20, 1)
        self.assertEqual(one, build(40, 1))
        self.assertGreater(len(build(20, 2)), len(one) + 4000)
        self.assertNotIn("text-q-2-", one)

    def test_explanations_drop_only_irrelevant_knowledge_rules(self):
        self.context.update({"references/knowledge-catalog.json": {"leaf": "ALL-KNOWLEDGE-LEAVES"},
                             "skills-reference/type-knowledge-map.md": "KNOWLEDGE-MAPPING",
                             "skills-reference/type-taxonomy.md": "COMPLETE-TAXONOMY",
                             "docs/content-labeling.md": "SOURCE-OWNERSHIP-RULES",
                             "references/filter-framework.json": {"all_types": "COMPLETE-FRAMEWORK"}})
        self.base["prompt"] = "header\n" + unit_tasks._BEGIN + json.dumps(self.context) + unit_tasks._END
        for stage in ("types", "explanations", "knowledge"):
            with patch.object(agent_tasks, "build_task", return_value=self.base):
                _, parts = unit_tasks.build_parts("unused", stage, 1000000, max_units=1)
            prompt = parts[0]["prompt"]
            for required in ("COMPLETE-TAXONOMY", "SOURCE-OWNERSHIP-RULES", "COMPLETE-FRAMEWORK"):
                self.assertIn(required, prompt)
            for marker in ("ALL-KNOWLEDGE-LEAVES", "KNOWLEDGE-MAPPING"):
                if stage == "explanations":
                    self.assertNotIn(marker, prompt)
                else:
                    self.assertIn(marker, prompt)

    def test_missing_inputs_never_split(self):
        self.base["preconditions"] = ["missing source"]
        with patch.object(agent_tasks, "build_task", return_value=self.base):
            self.assertEqual(unit_tasks.build_parts("unused", "assemble", 100)[1], [])


if __name__ == "__main__":
    unittest.main()
