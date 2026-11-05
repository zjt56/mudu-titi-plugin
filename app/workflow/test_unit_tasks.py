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

    def test_missing_inputs_never_split(self):
        self.base["preconditions"] = ["missing source"]
        with patch.object(agent_tasks, "build_task", return_value=self.base):
            self.assertEqual(unit_tasks.build_parts("unused", "assemble", 100)[1], [])


if __name__ == "__main__":
    unittest.main()
