"""Source-proven labeling snapshots; temporary batches and mocked model only."""
import contextlib
import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import agent_tasks, local_agent, unit_context, unit_tasks


class LabelPartReuseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        self.first = "1. She reads a book. A. reads B. read C. reading D. to read"
        self.second = "Read the shared story.\n2. Who went? A. Tom B. Ann\n3. Why? A. Fun B. Work"
        self.source = "Paper title\n" + self.first + "\n" + self.second + "\nEnd of paper\n"
        self.units = [
            {"unit_id": "q-1", "unit_kind": "standalone", "numbers": [1], "type": "单项填空", "stem": self.first},
            {"unit_id": "group-2-3", "unit_kind": "group", "numbers": [2, 3], "type": "阅读理解", "stem": self.second}]
        self.write_source()
        self.write("01_source/structure.json", {"sections": [{"type": "单项填空", "questions": [1]},
                                                              {"type": "阅读理解", "questions": [2, 3]}],
                                                  "groups": [{"numbers": [2, 3], "stem": self.second}]})
        self.original = self.write("original.txt", self.source)
        self.manifest = {"meta": {"source_type": "中考真题"}, "inputs": {
            "source": {"path": str(self.original), "sha256": "registration-digest", "mtime": 1}},
            "stages": {"types": {"status": "pending"}}, "updated_at": "first"}
        self.write("workflow/manifest.json", self.manifest)
        self.types = {"items": [
            {"unit_id": "q-1", "type": "单项填空", "subtypes": ["四个选项"], "words": 5,
             "contentComplete": True, "evidence": ["正文 L2"]},
            {"unit_id": "group-2-3", "type": "阅读理解", "subtypes": ["阅读判断"], "words": 10,
             "contentComplete": True, "evidence": ["正文 L3-5"]}]}
        self.write(agent_tasks.ARTIFACTS["types"], self.types)
        self.answers = {"answers": {"1": "A", "2": "A", "3": "B"},
                        "evidence": {"1": ["附录 L1"], "2": ["附录 L2"], "3": ["附录 L3"]}}
        self.write(agent_tasks.ARTIFACTS["answers"], self.answers)
        self.config = {"provider": "codex", "model": "model-A", "executable": "/unused/model",
                       "max_input_chars": 1000000, "review_part_units": 1,
                       "_part_model_binding": {"files": {}}}

    def write(self, relative, value):
        path = self.batch / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def write_source(self):
        self.write("01_source/正文.txt", self.source)
        self.write("01_source/structure.confirmed.json", {"version": "1.0", "units": self.units})

    def build(self, stage="knowledge"):
        return unit_tasks.build_parts(self.batch, stage, 1000000, max_units=1)

    def keys(self, stage="knowledge", config=None):
        base, parts = self.build(stage)
        self.assertTrue(all(p.get("cache_scope") for p in parts))
        return [local_agent._part_snapshot_key(stage, p, base["input_hashes"], config or self.config) for p in parts]

    def context(self, part):
        return json.loads(part["prompt"].split(unit_tasks._BEGIN, 1)[1].rsplit(unit_tasks._END, 1)[0])

    def source_text(self, context, path):
        if path in context:
            return context[path]
        return "\n".join(line["text"] for source in context["source_fragment_index"]["sources"]
                         if source["path"] == path for line in source["lines"])

    def test_all_label_stages_project_actual_prompt_and_keep_full_import_hashes(self):
        for stage in unit_context.STAGES:
            with self.subTest(stage=stage):
                base, parts = self.build(stage)
                value = self.context(parts[0])
                self.assertIn(self.first, self.source_text(value, "01_source/正文.txt"))
                self.assertNotIn("Who went", parts[0]["prompt"])
                self.assertIn("Paper title", parts[0]["prompt"])
                self.assertIn("End of paper", parts[0]["prompt"])
                if stage in ("answers", "explanations"):
                    self.assertNotIn("01_source/structure.confirmed.json", value)
                else:
                    self.assertEqual([u["unit_id"] for u in value["01_source/structure.confirmed.json"]["units"]], ["q-1"])
                self.assertNotIn("workflow/manifest.json", base["input_hashes"])
                self.assertIn("01_source/正文.txt", base["input_hashes"])
                self.assertTrue(any(Path(k).is_absolute() and Path(k).resolve() == self.original.resolve()
                                    for k in base["input_hashes"]))
                local_agent._check_input_hashes(self.batch, base["input_hashes"])
                draft = value.get(unit_context.DRAFT)
                if draft:
                    self.assertEqual([r["unit_id"] for r in draft["items"]], ["q-1"])
                    self.assertNotIn("input_hashes", draft)
                    self.assertNotIn("content_sha256", draft)

    def test_progress_ledger_rewrite_does_not_invalidate_in_flight_task(self):
        """监督器每轮都会改 manifest 的状态/时间；这不是题目输入变化。"""
        base, _ = self.build("knowledge")
        self.assertEqual(local_agent._check_input_hashes(self.batch, base["input_hashes"]), None)
        self.write("workflow/manifest.json", dict(self.manifest, updated_at="second", status="running"))
        local_agent._check_input_hashes(self.batch, base["input_hashes"])
        # 真正的原文变化仍必须让任务失效。
        self.write("01_source/正文.txt", self.source + "\n4. Extra? A. Yes B. No")
        with self.assertRaises(local_agent.AgentError) as caught:
            local_agent._check_input_hashes(self.batch, base["input_hashes"])
        self.assertEqual(caught.exception.code, "context_changed")

    def test_group_partition_keeps_shared_passage_and_original_summary_ownership_evidence(self):
        appendix = ("单项填空 A\n1.【解析】 This explanation belongs to question one.\n"
                    "阅读理解 A\n【文章大意】 This summary belongs to the shared story.\n"
                    "2.【解析】 Tom went.\n3.【解析】 He went for fun.\n")
        self.write("01_source/附录.txt", appendix)
        _, parts = self.build("explanations")
        value = self.context(parts[1])
        self.assertIn(self.second, self.source_text(value, "01_source/正文.txt"))
        self.assertNotIn(self.first, self.source_text(value, "01_source/正文.txt"))
        # Repeated A headings cannot establish ownership: keep the authoritative
        # appendix and its boundary/number context complete, including original lines.
        self.assertEqual(self.source_text(value, "01_source/附录.txt"), appendix.rstrip("\n"))
        self.assertEqual(value["partition_source_scope"]["selected_spans"], [
            {"unit_id": "group-2-3", "path": "01_source/正文.txt", "start_line": 3, "end_line": 5}])
        self.assertEqual([r["unit_id"] for r in value[agent_tasks.ARTIFACTS["types"]]["items"]], ["group-2-3"])
        # Explanations run beside answer filling and consume original material,
        # never the independently arriving answer worker's artifact.
        self.assertNotIn(agent_tasks.ARTIFACTS["answers"], value)

    def test_changed_type_invalidates_only_its_unit_group(self):
        before = self.keys()
        self.types["items"][0]["words"] += 1
        self.write(agent_tasks.ARTIFACTS["types"], self.types)
        after = self.keys()
        self.assertNotEqual(before[0], after[0])
        self.assertEqual(before[1], after[1])

    def test_answer_change_does_not_retag_knowledge(self):
        # knowledge now consumes only types/structure; answers merge at assemble.
        before = self.keys()
        self.answers["answers"]["1"] = "D"
        self.write(agent_tasks.ARTIFACTS["answers"], self.answers)
        self.assertEqual(self.keys(), before)

    def test_source_txt_change_localizes_only_with_exact_original_and_stable_spans(self):
        for stage in unit_context.STAGES:
            before = self.keys(stage)
            self.source = self.source.replace("She reads", "She likes")
            self.units[0]["stem"] = self.first.replace("She reads", "She likes")
            self.write_source()
            self.write("original.txt", self.source)
            after = self.keys(stage)
            self.assertNotEqual(before[0], after[0])
            if stage == "explanations":
                self.assertNotEqual(before[1], after[1])  # Full-file source refs must be refreshed.
            else:
                self.assertEqual(before[1], after[1])
            self.source = self.source.replace("She likes", "She reads")
            self.units[0]["stem"] = self.first
            self.write_source()
            self.write("original.txt", self.source)

    def test_refreshed_extraction_duplicates_do_not_invalidate_unchanged_parts(self):
        for stage in unit_context.STAGES:
            with self.subTest(stage=stage):
                extraction = {"text": self.source, "blocks": [{"text": self.source}],
                              "pages": [{"text": self.source}], "tables": [], "media": [], "warnings": []}
                self.write("01_source/extraction.json", extraction)
                before_base, before = self.build(stage)
                self.source = self.source.replace("She reads", "She likes")
                self.units[0]["stem"] = self.first.replace("She reads", "She likes")
                self.write_source()
                self.write("original.txt", self.source)
                extraction.update(text=self.source, blocks=[{"text": self.source}], pages=[{"text": self.source}])
                self.write("01_source/extraction.json", extraction)
                after_base, after = self.build(stage)
                old_keys = [local_agent._part_snapshot_key(stage, p, before_base["input_hashes"], self.config) for p in before]
                new_keys = [local_agent._part_snapshot_key(stage, p, after_base["input_hashes"], self.config) for p in after]
                self.assertNotEqual(old_keys[0], new_keys[0])
                if stage == "explanations":
                    self.assertNotEqual(old_keys[1], new_keys[1])
                else:
                    self.assertEqual(old_keys[1], new_keys[1])
                    self.assertEqual(before[1]["prompt"], after[1]["prompt"])
                self.assertNotEqual(before_base["input_hashes"]["01_source/extraction.json"],
                                    after_base["input_hashes"]["01_source/extraction.json"])
                self.assertNotEqual(before[1]["freshness_guards"]["01_source/extraction.json"],
                                    after[1]["freshness_guards"]["01_source/extraction.json"])
                self.source = self.source.replace("She likes", "She reads")
                self.units[0]["stem"] = self.first
                self.write_source()
                self.write("original.txt", self.source)

    def test_extraction_raw_duplicates_still_fail_inflight_freshness(self):
        extraction = {"text": self.source, "blocks": [{"text": self.source}], "warnings": []}
        self.write("01_source/extraction.json", extraction)
        base, parts = self.build()
        extraction["text"] += " raw duplicate changed during task"
        self.write("01_source/extraction.json", extraction)
        with self.assertRaises(local_agent.AgentError):
            local_agent._check_input_hashes(self.batch, base["input_hashes"])
        with self.assertRaisesRegex(ValueError, "changed during"):
            unit_tasks.check_part_inputs(self.batch, parts[0])

    def test_compact_extraction_evidence_still_invalidates_all_parts(self):
        extraction = {"text": self.source, "tables": [], "media": [], "warnings": []}
        for field, change in (("tables", [{"rows": [["new table"]]}]),
                              ("media", [{"path": "new-image.png"}]),
                              ("warnings", ["unlocated extraction warning"]),
                              ("unknown_evidence", {"value": "must remain bound"})):
            with self.subTest(field=field):
                self.write("01_source/extraction.json", extraction)
                before = self.keys()
                extraction[field] = change
                self.write("01_source/extraction.json", extraction)
                self.assertTrue(all(a != b for a, b in zip(before, self.keys())))

    def test_shared_material_change_invalidates_group_only(self):
        before = self.keys()
        self.source = self.source.replace("shared story", "different story")
        self.units[1]["stem"] = self.second.replace("shared story", "different story")
        self.write_source()
        self.write("original.txt", self.source)
        structure = json.loads((self.batch / "01_source/structure.json").read_text())
        structure["groups"][0]["stem"] = self.units[1]["stem"]
        self.write("01_source/structure.json", structure)
        after = self.keys()
        self.assertEqual(before[0], after[0])
        self.assertNotEqual(before[1], after[1])

    def test_manifest_progress_and_json_formatting_do_not_change_local_keys(self):
        before = self.keys()
        self.manifest["updated_at"] = "second"
        self.manifest["stages"]["types"] = {"status": "completed", "completed_at": "now"}
        self.manifest["inputs"]["source"]["mtime"] = 99
        self.write("workflow/manifest.json", self.manifest)
        self.write("04_knowledge/knowledge.json", {"items": [{"unit_id": "q-1"}, {"unit_id": "group-2-3"}]})
        self.assertEqual(before, self.keys())
        self.write(agent_tasks.ARTIFACTS["types"], json.dumps(self.types, indent=4))
        self.assertEqual(before, self.keys())

    def test_unknown_source_lines_appendix_ocr_media_and_originals_invalidate_all(self):
        changes = [("01_source/附录.txt", "1. A 原解析"),
                   ("01_source_ans/正文.txt", "1. A externally supplied explanation"),
                   ("01_source/pdf-ocr.json", {"pages": [{"text": "changed"}]}),
                   ("01_source/media/unassigned.png", "unassigned image"),
                   ("01_source/extraction.json", {"warnings": ["unlocated extraction change"]})]
        for relative, value in changes:
            with self.subTest(relative=relative):
                before = self.keys()
                self.write(relative, value)
                after = self.keys()
                self.assertTrue(all(a != b for a, b in zip(before, after)))
        before = self.keys()
        self.write("original.txt", self.source + "unlocated original change")
        self.assertTrue(all(a != b for a, b in zip(before, self.keys())))
        before = self.keys()
        self.source += "Unassigned instructions\n"
        self.write_source()
        self.write("original.txt", self.source)
        self.assertTrue(all(a != b for a, b in zip(before, self.keys())))

    def test_replacing_existing_media_invalidates_all_and_keeps_local_draft_media_binding(self):
        self.write("01_source/media/owned.png", "image A")
        self.source = self.source.replace("She reads", "She reads [IMG owned.png]")
        self.units[0]["stem"] = self.first.replace("She reads", "She reads [IMG owned.png]")
        self.units[0]["source_span"] = {"basis": "ai_classified", "start_line": 2, "end_line": 2}
        self.write_source()
        self.write("original.txt", self.source)
        before = self.keys()
        _, parts = self.build()
        draft = self.context(parts[0]).get(unit_context.DRAFT)
        if draft:
            self.assertIn("01_source/media/owned.png", draft["source_bindings"]["q-1"]["media_hashes"])
        self.write("01_source/media/owned.png", "image B")
        self.assertTrue(all(a != b for a, b in zip(before, self.keys())))
        with self.assertRaisesRegex(ValueError, "changed during"):
            unit_tasks.check_part_inputs(self.batch, parts[0])

    def test_non_text_original_bytes_are_global_guards(self):
        original = self.write("original.docx", "opaque original A")
        self.manifest["inputs"]["source"]["path"] = str(original)
        self.write("workflow/manifest.json", self.manifest)
        before = self.keys()
        original.write_bytes(b"opaque original B")
        self.assertTrue(all(a != b for a, b in zip(before, self.keys())))

    def test_unproven_or_overlapping_scopes_fall_back_to_full_binding(self):
        for bad in ("unmatched stale source", self.first + "\n" + self.second):
            self.units[1]["stem"] = bad
            self.write_source()
            base, parts = self.build("types")
            self.assertTrue(all("cache_scope" not in part for part in parts))
            self.assertIn("Who went", parts[0]["prompt"])
            key = local_agent._part_snapshot_key("types", parts[0], base["input_hashes"], self.config)
            changed = dict(base["input_hashes"], other="changed")
            self.assertNotEqual(key, local_agent._part_snapshot_key("types", parts[0], changed, self.config))
            candidate = {"status": "completed", "artifacts": {}, "notes": [], "findings": []}
            local_agent._store_part_snapshot(self.batch, "types", parts[0], base["input_hashes"], self.config, candidate)
            self.assertIsNone(local_agent._load_part_snapshot(self.batch, "types", parts[0], base["input_hashes"], self.config))

    def test_original_changed_during_task_fails_whole_batch_freshness(self):
        base, _ = self.build()
        self.write("original.txt", "changed")
        with self.assertRaises(local_agent.AgentError):
            local_agent._check_input_hashes(self.batch, base["input_hashes"])

    def test_global_file_and_media_arrival_during_task_are_rejected(self):
        for relative in ("01_source/附录.txt", "01_source/media/new.png"):
            _, parts = self.build()
            self.write(relative, "new evidence")
            with self.assertRaisesRegex(ValueError, "changed during"):
                unit_tasks.check_part_inputs(self.batch, parts[0])

    def test_model_command_executable_and_rule_content_are_bound(self):
        before = self.keys()
        for change in ({"model": "model-B"}, {"command": ["python", "bridge.py", "--model=B"]},
                       {"executable": "/different/executable"}, {"model_cache_identity": "route-v2"}):
            changed = dict(self.config, **change)
            self.assertTrue(all(a != b for a, b in zip(before, self.keys(config=changed))))
        base, parts = self.build()
        old = parts[0]["prompt"]
        parts[0]["prompt"] += "\nChanged actual stage rule/catalog"
        self.assertNotEqual(local_agent._part_snapshot_key("knowledge", dict(parts[0], prompt=old), base["input_hashes"], self.config),
                            local_agent._part_snapshot_key("knowledge", parts[0], base["input_hashes"], self.config))

    def test_unknown_implicit_model_cannot_store_or_load_local_snapshot(self):
        base, parts = self.build()
        candidate = {"status": "completed", "artifacts": {}, "notes": [], "findings": []}
        unknown = {"provider": "dsh", "executable": "/some/dsh"}
        local_agent._store_part_snapshot(self.batch, "knowledge", parts[0], base["input_hashes"], unknown, candidate)
        self.assertIsNone(local_agent._load_part_snapshot(self.batch, "knowledge", parts[0], base["input_hashes"], unknown))
        self.assertFalse(local_agent._part_snapshot_dir(self.batch, "knowledge").exists())

    def test_router_configuration_is_fingerprinted_without_logging_contents(self):
        script = self.write("backend/wf-router.sh", "#!/bin/sh\n")
        router = self.write("config/model-router.json", {"default": {"provider": "provider", "model": "A"}})
        config = {"provider": "command", "command": [str(script), "{prompt_file}", "{response_file}"]}
        with patch.dict(os.environ, {"WF_PLUGIN_DIR": str(self.batch), "WF_ROUTER_FILE": str(router)}):
            first = local_agent._part_model_binding(config)
            self.write("config/model-router.json", {"default": {"provider": "provider", "model": "B"}})
            second = local_agent._part_model_binding(config)
        self.assertNotEqual(first, second)
        self.assertTrue(all(len(v) == 64 for v in first["files"].values()))

    def test_cli_reuses_unchanged_part_but_imports_full_current_candidate_once(self):
        calls, imports = [], []

        def invoke(prompt, run_dir, config, **options):
            value = json.loads(prompt.split(unit_tasks._BEGIN, 1)[1].rsplit(unit_tasks._END, 1)[0])
            uid = value["confirmed_units_with_stable_ids"][0]["unit_id"]
            calls.append(uid)
            row = next(r for r in self.types["items"] if r["unit_id"] == uid)
            return {"response": {"status": "completed", "notes": [], "findings": [],
                                 "artifacts": {agent_tasks.ARTIFACTS["types"]: {"items": [copy.deepcopy(row)]}}}}

        actual_accept = agent_tasks.accept_result

        def accept(batch, stage, response, **kwargs):
            imports.append(copy.deepcopy(response))
            return actual_accept(batch, stage, response, **kwargs)

        # 标注阶段默认整卷一次分析；这里显式开启分片，验证“未变分片复用快照”能力仍在。
        config = dict(self.config, retries=0, part_concurrency=1, label_split=True)
        config.pop("_part_model_binding")
        with patch.object(local_agent, "load_config", return_value=config), \
             patch.object(local_agent, "invoke", side_effect=invoke), \
             patch.object(agent_tasks, "accept_result", side_effect=accept), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(local_agent.main(["--stage", "types", "--batch-dir", str(self.batch)]), 0)
            self.source = self.source.replace("She reads", "She likes")
            self.units[0]["stem"] = self.first.replace("She reads", "She likes")
            self.write_source()
            self.write("original.txt", self.source)
            self.assertEqual(local_agent.main(["--stage", "types", "--batch-dir", str(self.batch)]), 0)
        self.assertEqual(calls, ["q-1", "group-2-3", "q-1"])
        self.assertEqual(len(imports), 2)
        for response in imports:
            self.assertEqual([r["unit_id"] for r in response["artifacts"][agent_tasks.ARTIFACTS["types"]]["items"]],
                             ["q-1", "group-2-3"])


if __name__ == "__main__":
    unittest.main()
