"""Real preassembly/portable imports/finalization; isolated and model-free."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import agent, agent_protocol, batch_fill, contracts, format_gate, manifest as mf, runner
from workbench import server


class ProgressivePipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="progressive-pipeline-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.text = "1. She ______ a book. A. reads B. read C. reading D. to read"
        self.put("01_source/正文.txt", self.text)
        self.put("01_source/附录.txt", "参考答案\n1. A")
        self.put("01_source/structure.json", {"sections": [{"type": "单项填空", "questions": [1]}], "groups": []})
        self.put("01_source/extraction.json", {})
        self.meta = {"source": "2025年安徽省中考英语", "source_type": "中考真题", "year": "2025", "region": "安徽省", "paper_title": "2025年安徽省中考英语"}
        self.put("01_source/metadata.json", self.meta)
        self.put("01_source/structure.confirmed.json", {"meta": self.meta, "units": [{"unit_kind": "standalone", "numbers": [1], "type": "单项填空", "stem": self.text,
                  "source_span": {"basis": "ai_classified", "start_line": 1, "end_line": 1}}]})
        self.put("07_review/structure-gate.json", {"passed": True})
        source = self.put("source.txt", self.text)
        self.state = mf.new_manifest("progressive-fixture", self.root, mf.register_input(self.root, "source", source), {}, self.meta)
        for sid in ("extract", "structure_gate"):
            mf.touch_stage(self.state, sid, mf.COMPLETED)
        mf.touch_stage(self.state, "audio", mf.SKIPPED, notes="无音频")
        mf.save(self.root, self.state)

    def put(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def load(self, relative=batch_fill.BATCH):
        return json.loads((self.root / relative).read_text(encoding="utf-8"))

    def accept(self, stage, payload):
        # Fixture exercises field-fill plumbing, not artefact-level handoff.
        with patch.object(mf, "dependency_satisfied", return_value=True), \
             patch.object(agent_protocol.mf, "dependency_satisfied", return_value=True):
            task = agent_protocol.export_task(self.root, stage, max_input_chars=900000)
        self.assertEqual(task["status"], "ready", task)
        response = self.put("result.json", {"protocol": agent_protocol.PROTOCOL, "task_id": task["task_id"], "result": {
            "status": "completed", "artifacts": {agent_protocol.agent_tasks.ARTIFACTS[stage]: payload}, "notes": [], "findings": []}})
        with patch.object(agent_protocol.mf, "dependency_satisfied", return_value=True):
            return agent_protocol.import_result(self.root, task["task_id"], response)

    def prepare(self):
        result = self.accept("preassemble", {"items": [{
            "unit_id": "q-1", "type": "单项填空", "subtypes": ["四个选项"],
            "words": 5, "contentComplete": True, "evidence": ["正文 L1"]}]})
        self.assertTrue(result["completed"], result)
        self.assertEqual(mf.load(self.root)["stages"]["preassemble"]["status"], mf.COMPLETED)
        self.assertTrue((self.root / "03_types/types.json").is_file())
        self.assertTrue((self.root / batch_fill.BATCH).is_file())
        self.assertTrue((self.root / batch_fill.BATCH_BASE).is_file())
        self.assertEqual(mf.load(self.root)["stages"]["assemble"]["status"], mf.PENDING)
        return self.load()["items"][0]

    def fill(self):
        initial = self.prepare()
        base_bytes = (self.root / batch_fill.BATCH_BASE).read_bytes()
        types_path = self.root / "03_types/types.json"
        types_bytes = types_path.read_bytes()
        self.assertEqual(runner._execute_command(
            self.root, mf.load(self.root), contracts.STAGE_BY_ID["types"]), 0)
        self.assertEqual(types_path.read_bytes(), types_bytes)
        typed = self.load()["items"][0]
        self.assertEqual(typed["question_type"], "单项填空")
        self.assertEqual(typed["knowledge"], [])
        self.accept("answers", {"answers": {"1": "A"}, "evidence": {"1": ["附录 L2"]}, "missing": {}, "difficulty": {"1": "容易"}})
        self.accept("explanations", {"items": [{"unit_id": "q-1", "explanation": "无", "articleSummary": "无", "subquestions": [], "evidence": ["附录仅给答案，无原解析/大意"]}]})
        before_tags = self.load()["items"][0]
        self.assertEqual(before_tags["answer"], "A")
        self.assertEqual(before_tags["difficulty"], "容易")
        self.assertEqual(before_tags["explanation"], "无")
        self.accept("knowledge", {"items": [{"unit_id": "q-1", "knowledge": ["词汇/词汇辨析/动词辨析"], "subquestions": [], "evidence": ["正文 L1"]}]})
        filled = self.load()["items"][0]
        for key in ("id", "unit_id", "numbers", "stem", "options", "material"):
            self.assertEqual(filled[key], initial[key], key)
        self.assertEqual(filled["answer"], "A")
        self.assertEqual(filled["explanation"], "无")
        self.assertEqual(filled["articleSummary"], "无")
        self.assertEqual(filled["knowledge"], ["词汇/词汇辨析/动词辨析"])
        self.assertEqual((self.root / batch_fill.BATCH_BASE).read_bytes(), base_bytes)
        return filled

    def test_preassemble_is_real_and_fields_fill_same_objects_before_finalization(self):
        self.fill()
        # A managed batch finalizes without even loading an AI configuration.
        with patch("workflow.agent.configured", side_effect=AssertionError("must not consult model")), \
                patch("workflow.manifest.dependency_satisfied", return_value=True), \
                patch("workflow.local_agent.invoke", side_effect=AssertionError("must not call model")):
            self.assertEqual(agent.main(["run", "--stage", "assemble", "--batch-dir", str(self.root)]), 0)
        self.assertEqual(mf.load(self.root)["stages"]["assemble"]["status"], mf.COMPLETED)
        self.assertFalse(batch_fill.is_filling(self.root))
        state = mf.load(self.root)
        self.assertEqual(runner._execute_command(self.root, state, contracts.STAGE_BY_ID["meta"]), 0)
        self.assertFalse(mf.is_stale(self.root, mf.load(self.root), contracts.STAGE_BY_ID["assemble"])[0])
        self.assertEqual(runner._execute_command(self.root, mf.load(self.root), contracts.STAGE_BY_ID["underlines"]), 0)
        report = format_gate.run(self.root)
        self.assertTrue(report["passed"], report)
        # Missing semantic-review execution is not manufactured by format checking.
        self.assertNotEqual(mf.load(self.root)["stages"]["review"]["status"], mf.COMPLETED)

    def test_only_current_format_check_finishes_upload_queue(self):
        from workflow.test_format_gate import batch
        final = self.put(format_gate.FINAL_PATH, batch())
        state = self.state
        mf.touch_stage(state, "review", mf.COMPLETED)
        mf.touch_stage(state, "review_gate", mf.COMPLETED)
        self.assertFalse(server.final_format_complete(self.root, state))
        report = format_gate.run(self.root)
        self.assertTrue(report["passed"], report)
        self.assertFalse(server.final_format_complete(self.root, state))
        mf.touch_stage(state, "format_gate", mf.COMPLETED)
        self.assertFalse(server.final_format_complete(self.root, state))  # Other local actions are still pending.
        from workflow.supervisor import LOCAL_STAGES
        for sid in LOCAL_STAGES:
            mf.touch_stage(state, sid, mf.COMPLETED)
        self.assertTrue(server.final_format_complete(self.root, state))
        final.write_bytes(final.read_bytes() + b" ")
        self.assertFalse(server.final_format_complete(self.root, state))

    def test_new_local_stages_do_not_requeue_historical_remote_submissions(self):
        state = self.state
        state["stages"].pop("preassemble")
        state["stages"].pop("format_gate")
        mf.touch_stage(state, "upload", mf.COMPLETED)
        mf.save(self.root, state)
        loaded = mf.load(self.root)
        for sid in ("preassemble", "format_gate"):
            self.assertEqual(loaded["stages"][sid]["status"], mf.SKIPPED)
            self.assertTrue(mf.skip_reason(loaded, contracts.STAGE_BY_ID[sid]))
        self.assertFalse((self.root / batch_fill.BATCH).exists())

    def test_legacy_completed_local_batch_skips_only_new_preassembly(self):
        state = self.state
        state['stages'].pop('preassemble')
        mf.touch_stage(state, 'assemble', mf.COMPLETED)
        mf.touch_stage(state, 'review', mf.COMPLETED)
        self.put(batch_fill.BATCH, {'items': []})
        mf.save(self.root, state)
        loaded = mf.load(self.root)
        self.assertEqual(loaded['stages']['preassemble']['status'], mf.SKIPPED)
        self.assertTrue(mf.skip_reason(loaded, contracts.STAGE_BY_ID['preassemble']))
        self.assertEqual(loaded['stages']['format_gate']['status'], mf.PENDING)
        mf.touch_stage(loaded, 'assemble', mf.INVALIDATED)
        self.assertFalse(mf.skip_reason(loaded, contracts.STAGE_BY_ID['preassemble']))

    def test_final_format_continues_after_explicit_unqualified_review_without_approving_it(self):
        from types import SimpleNamespace
        from workflow.test_format_gate import batch
        self.put(format_gate.FINAL_PATH, batch())
        state = self.state
        mf.touch_stage(state, 'review', mf.COMPLETED)
        mf.touch_stage(state, 'review_gate', mf.BLOCKED,
                       execution_completed=True, continue_unqualified=True,
                       quality_status='failed', release_ready=False)
        mf.save(self.root, state)
        args = SimpleNamespace(stage='format_gate', until=None, force=False, dry_run=False)
        # A blocking review_gate is a real unmet upstream now: format_gate must
        # wait instead of continuing and marking itself complete.
        self.assertNotEqual(runner._cmd_run_locked(self.root, args), 0)
        loaded = mf.load(self.root)
        self.assertNotEqual(loaded['stages']['format_gate']['status'], mf.COMPLETED)
        self.assertEqual(loaded['stages']['review_gate']['status'], mf.BLOCKED)
        self.assertFalse(loaded['stages']['review_gate']['release_ready'])
        self.assertFalse(server.final_format_complete(self.root, loaded))  # Format alone does not manufacture missing local execution.
        cards = server.stage_cards(loaded, has_output=True)
        self.assertNotEqual((cards['current'] or {}).get('id'), 'review_gate')

    def test_stage_cards_use_contract_order_and_do_not_call_partial_batch_stale(self):
        state = self.state
        mf.touch_stage(state, "preassemble", mf.COMPLETED)
        cards = server.stage_cards(state, has_output=True)
        self.assertEqual([card["id"] for card in cards["items"]], list(contracts.STAGE_ORDER))
        self.assertLess(contracts.STAGE_ORDER.index("preassemble"), contracts.STAGE_ORDER.index("types"))
        self.assertLess(contracts.STAGE_ORDER.index("review_gate"), contracts.STAGE_ORDER.index("format_gate"))
        self.assertLess(contracts.STAGE_ORDER.index("format_gate"), contracts.STAGE_ORDER.index("upload_gate"))
        self.assertFalse(cards["stale"])
        card = next(row for row in cards["items"] if row["id"] == "types")
        self.assertIn("preassemble", card["depends_on"])
        self.assertTrue(card["description"])


if __name__ == "__main__":
    unittest.main()
