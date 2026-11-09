"""Real isolated preassembly handoff; no live models, user batches or network."""
import copy
import io
import json
import tempfile
import unittest
from contextlib import nullcontext, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from workflow import agent, agent_protocol, agent_tasks, batch_fill, contracts, handoff, local_agent, manifest as mf, preassembly


class PreassemblyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="preassembly-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        text = "1. She reads a book. A. Yes B. No C. Good D. Bad"
        original = self.put("original.txt", text)
        self.put("01_source/正文.txt", text)
        self.put("01_source/structure.json", {"sections": [], "groups": []})
        self.put("01_source/extraction.json", {})
        self.put("07_review/extraction-report.json", {})
        self.put("01_source/metadata.json", {"original_sha256": mf.sha256_file(original)})
        self.put("01_source/structure.confirmed.json", {"units": [{
            "unit_kind": "standalone", "numbers": [1], "type": "单项填空", "stem": text,
            "source_span": {"basis": "ai_classified", "start_line": 1, "end_line": 1}}]})
        self.put("07_review/structure-gate.json", {"passed": True,
            "original_sha256": mf.sha256_file(original),
            "input_hashes": {name: mf.sha256_file(self.root / name) for name in (
                "01_source/正文.txt", "01_source/structure.json", "01_source/structure.confirmed.json")}})
        state = mf.new_manifest("preassembly-test", self.root, mf.register_input(self.root, "source", original), {})
        for sid in ("extract", "structure_gate"):
            mf.touch_stage(state, sid, mf.COMPLETED, output_hashes=mf.output_hashes(self.root, contracts.STAGE_BY_ID[sid]))
        mf.save(self.root, state)
        self.payload = {"items": [{"unit_id": "q-1", "type": "单项填空", "subtypes": ["四个选项"],
            "words": 10, "contentComplete": True, "evidence": ["正文 L1"]}]}
        self.response = {"status": "completed", "artifacts": {"03_types/types.json": self.payload}, "notes": [], "findings": []}
        for owner in (agent_protocol, local_agent):
            lock = patch.object(owner, "_agent_lock", side_effect=lambda *a, **k: nullcontext())
            lock.start()
            self.addCleanup(lock.stop)

    def put(self, rel, value):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def export(self):
        return agent_protocol.export_task(self.root, "preassemble")

    def import_response(self, package, response=None):
        value = {"protocol": agent_protocol.PROTOCOL, "task_id": package["task_id"],
                 "result": self.response if response is None else response}
        return agent_protocol.import_result(self.root, package["task_id"], self.put("result.json", value))

    def test_legacy_default_bridge_adds_assembly_without_overwriting_overrides(self):
        from workflow import auto
        legacy = ["{python}", "-m", "workflow.agent", "run", "--stage", "types", "--batch-dir", "{batch_dir}"]
        path = self.put("old-bridge.json", {"types": legacy, "answers": ["custom-answer"]})
        migrated = auto.load_bridge(path)
        self.assertEqual(migrated["preassemble"][5], "preassemble")
        self.assertEqual(migrated["answers"], ["custom-answer"])
        self.put("old-bridge.json", {"types": legacy, "preassemble": ["custom-assembly"]})
        self.assertEqual(auto.load_bridge(path)["preassemble"], ["custom-assembly"])

    def test_export_owns_source_and_labels_and_import_unlocks_answers(self):
        package = self.export()
        self.assertEqual(package["status"], "ready")
        self.assertTrue((self.root / batch_fill.BATCH_BASE).is_file())
        self.assertFalse(mf.dependency_satisfied(mf.load(self.root), "preassemble"))
        task = json.loads((Path(package["package_dir"]) / "task.json").read_text())
        self.assertEqual(task["stage"], "preassemble")
        self.assertEqual(task["expected_artifacts"], ["03_types/types.json"])
        base = (self.root / batch_fill.BATCH_BASE).read_bytes()
        self.assertTrue(self.import_response(package)["completed"])
        state = mf.load(self.root)
        self.assertTrue(mf.dependency_satisfied(state, "preassemble"))
        self.assertEqual(state["stages"]["types"]["status"], mf.PENDING)
        self.assertEqual((self.root / batch_fill.BATCH_BASE).read_bytes(), base)
        batch = json.loads((self.root / batch_fill.BATCH).read_text())
        self.assertEqual(batch["items"][0]["question_type"], "单项填空")
        with patch.object(local_agent, "invoke", side_effect=AssertionError("checkpoint must not call a model")):
            self.assertTrue(preassembly.check(self.root)["passed"])
        self.assertEqual(mf.load(self.root)["stages"]["preassemble"]["status"], mf.COMPLETED)

    def test_invalid_labels_never_complete_or_unlock_downstream(self):
        package = self.export()
        invalid = copy.deepcopy(self.response)
        invalid["artifacts"]["03_types/types.json"]["items"][0]["contentComplete"] = False
        with self.assertRaisesRegex(ValueError, "contentComplete|incomplete"):
            self.import_response(package, invalid)
        self.assertFalse(mf.dependency_satisfied(mf.load(self.root), "preassemble"))
        self.assertFalse((self.root / "03_types/types.json").exists())

    def test_source_base_changed_during_model_call_rejects_import(self):
        package = self.export()
        base = json.loads((self.root / batch_fill.BATCH_BASE).read_text())
        base["items"][0]["stem"] = "Changed"
        self.put(batch_fill.BATCH_BASE, base)
        with self.assertRaisesRegex(ValueError, "stale"):
            self.import_response(package)

    def test_skeleton_alone_is_not_a_delivered_assembly(self):
        preassembly.prepare(self.root)
        state = mf.load(self.root)
        mf.touch_stage(state, "preassemble", mf.COMPLETED)
        checked = handoff.inspect_stage(self.root, state, "preassemble")
        self.assertFalse(checked["passed"])
        self.assertIn("types.json", str(checked["errors"]))
        self.assertFalse(mf.dependency_satisfied(state, "preassemble"))

    def test_local_agent_completes_preassembly_with_one_type_model_call(self):
        config = {"max_input_chars": 180000, "timeout_seconds": 30, "retries": 0,
                  "label_split": False, "unit_split": False}
        with patch.object(local_agent, "load_config", return_value=config), \
             patch.object(local_agent, "_part_model_binding", return_value=None), \
             patch.object(local_agent, "invoke", return_value={"response": self.response}) as invoke, \
             redirect_stdout(io.StringIO()):
            code = agent.main(["run", "--stage", "preassemble", "--batch-dir", str(self.root), "--config", "unused.json"])
        self.assertEqual(code, 0)
        self.assertEqual(invoke.call_count, 1)
        self.assertTrue(mf.dependency_satisfied(mf.load(self.root), "preassemble"))
        self.assertEqual(contracts.STAGE_BY_ID["types"].kind, contracts.COMMAND)

    def test_local_original_changed_during_model_call_rejects_install(self):
        config = {"max_input_chars": 180000, "timeout_seconds": 30, "retries": 0,
                  "label_split": False, "unit_split": False}
        def mutate_original(*args, **kwargs):
            self.put("original.txt", "A different original paper")
            return {"response": self.response}
        with patch.object(local_agent, "load_config", return_value=config), \
             patch.object(local_agent, "_part_model_binding", return_value=None), \
             patch.object(local_agent, "invoke", side_effect=mutate_original), \
             redirect_stdout(io.StringIO()):
            code = agent.main(["run", "--stage", "preassemble", "--batch-dir", str(self.root), "--config", "unused.json"])
        self.assertNotEqual(code, 0)
        self.assertFalse((self.root / "03_types/types.json").exists())
        self.assertNotEqual(mf.load(self.root)["stages"]["preassemble"]["status"], mf.COMPLETED)

    def test_type_only_repair_keeps_shared_assembly_delivery_current(self):
        self.import_response(self.export())
        state = mf.load(self.root)
        mf.touch_stage(state, "types", mf.PENDING)
        mf.save(self.root, state)
        package = agent_protocol.export_task(self.root, "types")
        repaired = copy.deepcopy(self.response)
        repaired["artifacts"]["03_types/types.json"]["items"][0]["words"] = 11
        self.import_response(package, repaired)
        self.assertTrue(mf.dependency_satisfied(mf.load(self.root), "preassemble"))

    def test_rejected_handoff_keeps_actual_diagnostic_message(self):
        preassembly.prepare(self.root)
        with self.assertRaisesRegex(ValueError, "types.json"):
            preassembly.check(self.root)
        # A malformed unowned source object must produce the real source error,
        # not TypeError from joining structured diagnostic rows.
        with patch.object(preassembly, "require_current_source"), \
             patch("workflow.handoff.inspect_stage", return_value={"passed": False,
                "errors": [{"code": "HANDOFF_INVALID", "message": "q-1 stem is not a string"}]}):
            with self.assertRaisesRegex(ValueError, "q-1 stem"):
                agent_tasks.accept_result(self.root, "preassemble", self.response)

    def test_assembly_retry_feedback_reaches_internal_type_task(self):
        from workflow import self_heal
        preassembly.prepare(self.root)
        path = self_heal._feedback(self.root, "preassemble", "修正题型字段", "q-1 题型不合规", 1)
        self.assertEqual(path, "workflow/repair-feedback/types.json")
        task = agent_tasks.build_task(self.root, "preassemble")
        self.assertIn("q-1 题型不合规", task["prompt"])
        self.assertIn(path, task["input_hashes"])

    def test_failed_compatibility_check_returns_to_real_producer(self):
        from workflow import self_heal
        self.import_response(self.export())
        state = mf.load(self.root)
        mf.touch_stage(state, "types", mf.FAILED)
        mf.save(self.root, state)
        outcome = self_heal._apply(self.root, state, "types", "q-1 题型不合规",
            {"action": "retry_stage", "reason": "修正题型"}, 1, {"files": {}})
        self.assertEqual(outcome["target_stage"], "preassemble")
        self.assertEqual(mf.load(self.root)["stages"]["preassemble"]["status"], mf.PENDING)
        self.assertFalse(outcome["verified"])

    def test_legacy_source_only_task_can_finish_without_bypassing_new_contract(self):
        preassembly.prepare(self.root)
        state = mf.load(self.root)
        mf.touch_stage(state, "preassemble", mf.COMPLETED,
                       output_hashes={batch_fill.BATCH_BASE: mf.sha256_file(self.root / batch_fill.BATCH_BASE)})
        mf.save(self.root, state)
        self.assertFalse(mf.dependency_satisfied(state, "preassemble"))
        package = agent_protocol.export_task(self.root, "types")
        self.assertEqual(package["status"], "ready")
        self.assertTrue(self.import_response(package)["completed"])
        self.assertTrue(mf.dependency_satisfied(mf.load(self.root), "preassemble"))


if __name__ == "__main__":
    unittest.main()
