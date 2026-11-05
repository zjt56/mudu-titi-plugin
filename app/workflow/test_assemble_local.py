import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import assemble_local


def _write(batch: Path, relative: str, payload) -> None:
    path = batch / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def make_batch(root: Path) -> Path:
    batch = root / "batch"
    _write(batch, assemble_local.STRUCTURE, {"units": [
        {"unit_kind": "standalone", "numbers": ["1"], "type": "听力关键词语选择",
         "stem": "1.A.farm\tB.film\tC.form"}]})
    _write(batch, assemble_local.BATCH, {"items": [{
        "unit_id": "q-1", "id": "q-1", "unit_kind": "standalone", "number": 1, "numbers": [1],
        "question_type": "听力关键词语选择", "filterMetadata": {"subtypes": []},
        "stem": "1.", "material": "",
        "options": [{"label": "A", "text": "farm"}, {"label": "B", "text": "film"}, {"label": "C", "text": "form"}],
        "materialBlocks": [], "attachments": [], "knowledge": ["旧标签"], "answer": "A",
        "explanation": "旧解析", "articleSummary": "", "subquestions": [],
        "knowledgeIds": ["OLD"], "knowledge_ids": ["OLD"], "wordCount": 3, "difficulty": "适中"}]})
    _write(batch, assemble_local.KNOWLEDGE, {"items": [{
        "unit_id": "q-1", "knowledge": ["新标签"], "knowledgeIds": ["NEW"],
        "subquestions": [], "answer": "B", "explanation": "新解析"}]})
    _write(batch, assemble_local.EXPLANATIONS, {"items": [{
        "unit_id": "q-1", "explanation": "新解析", "articleSummary": "", "subquestions": []}]})
    _write(batch, assemble_local.TYPES, {"items": [{
        "unit_id": "q-1", "type": "听力关键词语选择", "subtypes": [], "words": 3}]})
    _write(batch, assemble_local.ANSWERS, {"answers": {"1": "B"}, "difficulty": {"1": "容易"}})
    return batch


class AssembleLocalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.batch = make_batch(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_without_baseline_falls_back_to_model(self):
        ok, reason = assemble_local.guard_status(self.batch)
        self.assertFalse(ok)
        self.assertIn("基线", reason)
        self.assertIsNone(assemble_local.try_assemble(self.batch))

    def test_overlay_updates_content_and_preserves_structure(self):
        assemble_local.record_guard(self.batch)
        before = json.loads((self.batch / assemble_local.BATCH).read_text(encoding="utf-8"))
        after = assemble_local.build(self.batch)
        item = after["items"][0]
        self.assertEqual(item["knowledge"], ["新标签"])
        self.assertEqual(item["knowledgeIds"], ["NEW"])
        self.assertEqual(item["knowledge_ids"], ["NEW"])
        self.assertEqual(item["answer"], "B")
        self.assertEqual(item["difficulty"], "容易")
        self.assertEqual(item["explanation"], "新解析")
        for key in ("stem", "material", "options", "materialBlocks", "attachments", "subquestions", "numbers"):
            self.assertEqual(item[key], before["items"][0][key], key)

    def test_unchanged_upstream_reproduces_accepted_batch_exactly(self):
        before = json.loads((self.batch / assemble_local.BATCH).read_text(encoding="utf-8"))
        # 上游与批次一致时，确定性结果必须逐字段等于已接受批次（幂等）
        _write(self.batch, assemble_local.KNOWLEDGE, {"items": [{
            "unit_id": "q-1", "knowledge": ["旧标签"], "knowledgeIds": ["OLD"],
            "subquestions": [], "answer": "A", "explanation": "旧解析"}]})
        _write(self.batch, assemble_local.EXPLANATIONS, {"items": [{
            "unit_id": "q-1", "explanation": "旧解析", "articleSummary": "", "subquestions": []}]})
        _write(self.batch, assemble_local.ANSWERS, {"answers": {"1": "A"}, "difficulty": {"1": "适中"}})
        self.assertEqual(assemble_local.build(self.batch), before)

    def test_structure_change_invalidates_baseline(self):
        assemble_local.record_guard(self.batch)
        structure = json.loads((self.batch / assemble_local.STRUCTURE).read_text(encoding="utf-8"))
        structure["units"][0]["stem"] = "1.A.changed"
        _write(self.batch, assemble_local.STRUCTURE, structure)
        ok, reason = assemble_local.guard_status(self.batch)
        self.assertFalse(ok)
        self.assertIn("结构确认已变化", reason)

    def test_batch_tamper_invalidates_baseline(self):
        assemble_local.record_guard(self.batch)
        data = json.loads((self.batch / assemble_local.BATCH).read_text(encoding="utf-8"))
        data["items"][0]["stem"] = "被手工改过的题干"
        _write(self.batch, assemble_local.BATCH, data)
        ok, reason = assemble_local.guard_status(self.batch)
        self.assertFalse(ok)
        self.assertIn("被改动", reason)

    def test_label_only_drift_still_allows_deterministic_rebuild(self):
        assemble_local.record_guard(self.batch)
        data = json.loads((self.batch / assemble_local.BATCH).read_text(encoding="utf-8"))
        # 重新打标签/换解析只改内容字段，结构不变：必须继续走确定性路径。
        data["items"][0].update(knowledge=["再改一次"], answer="C", explanation="再换解析",
                                difficulty="困难", knowledgeIds=["X"], knowledge_ids=["X"])
        _write(self.batch, assemble_local.BATCH, data)
        ok, reason = assemble_local.guard_status(self.batch)
        self.assertTrue(ok, reason)
        self.assertIn("结构未变", reason)
        self.assertTrue(assemble_local.available(self.batch))

    def test_available_is_false_without_baseline(self):
        """runner.run 的确定性组装入口依赖 available()，缺失基线时不得误判可用。"""
        self.assertFalse(assemble_local.available(self.batch))

    def test_try_assemble_validates_then_imports_and_records_guard(self):
        assemble_local.record_guard(self.batch)
        calls = []

        def accept(batch, stage, candidate, validate_only=False):
            calls.append(validate_only)
            return {"completed": True, "artifacts": [assemble_local.BATCH]}

        with patch("workflow.agent_tasks.accept_result", side_effect=accept):
            code = assemble_local.try_assemble(self.batch)
        self.assertEqual(code, 0)
        self.assertEqual(calls, [True, False])  # 先只读校验，通过后才真正导入

    def test_validation_failure_falls_back_without_installing(self):
        assemble_local.record_guard(self.batch)
        with patch("workflow.agent_tasks.accept_result", side_effect=ValueError("选项被改写")):
            self.assertIsNone(assemble_local.try_assemble(self.batch))
        self.assertTrue((self.batch / assemble_local.FALLBACK).is_file())

    def test_seed_rejects_option_not_in_structure(self):
        data = json.loads((self.batch / assemble_local.BATCH).read_text(encoding="utf-8"))
        data["items"][0]["options"][0]["text"] = "原文里没有的选项"
        _write(self.batch, assemble_local.BATCH, data)
        ok, problems = assemble_local.verify_against_structure(self.batch)
        self.assertFalse(ok)
        self.assertTrue(any("无法回指结构题干" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
