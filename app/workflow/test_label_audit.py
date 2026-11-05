"""最终文件的标签检查与闸门接线回归。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import checks, label_audit, manifest as mf, runner


class LabelAuditTest(unittest.TestCase):
    def test_group_child_knowledge_may_be_empty_but_group_must_be_labeled(self):
        from workbench import taxonomy
        item = {"question_type": "补全对话", "unit_kind": "group",
                "subtypes": ["7选5"],
                "knowledge": ["语篇体裁/日常口语/日常对话",
                              "主题/人与社会/社会服务与人际沟通/人际交往",
                              "语用/情景交际"],
                "subquestions": [{"number": 71, "knowledge": []}]}
        self.assertEqual(taxonomy.check_question_labels(item), [])
        item["knowledge"] = []
        self.assertTrue(any("knowledge 必须是非空" in p
                            for p in taxonomy.check_question_labels(item)))

    def test_optional_child_knowledge_still_rejects_invalid_catalog_values(self):
        from workbench import taxonomy
        item = {"question_type": "补全对话", "unit_kind": "group",
                "subtypes": ["7选5"], "knowledge": ["语用/情景交际"],
                "subquestions": [{"number": 71, "knowledge": ["自造知识点"]}]}
        self.assertTrue(any("子题[1]" in p
                            for p in taxonomy.check_question_labels(item)))

    def test_empty_or_malformed_batch_never_passes(self):
        for payload in (None, [], {}, {"items": []}, {"items": [None]}):
            with self.subTest(payload=payload):
                self.assertFalse(label_audit.inspect_batch(payload)["passed"])

    def test_invalid_type_has_unit_and_file_hash_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "batch.json"
            path.write_text(json.dumps({"items": [{"id": "U1", "question_type": "自创题型"}]}))
            result = label_audit.inspect_file(path)
            self.assertFalse(result["passed"])
            self.assertEqual(result["errors"][0]["unit_id"], "U1")
            self.assertEqual(len(result["batch_sha256"]), 64)
            self.assertEqual(len(result["catalog_sha256"]), 3)

    def test_builtin_is_executable_without_external_skill_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "batch.json"
            path.write_text(json.dumps({"items": [{"id": "U1", "question_type": "自创题型"}]}))
            result = checks.run_all(Path(__file__).resolve().parents[1], path, {},
                                    only=("workflow/label_audit.py",))
            self.assertFalse(result["passed"])
            self.assertEqual(len(result["checks"]), 1)
            self.assertEqual(result["checks"][0]["exit_code"], 1)
            self.assertIn("目录与匹配规则 label_audit", result["blocking_failures"])

    def test_review_pass_does_not_override_invalid_current_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "06_batch").mkdir()
            (folder / "06_batch" / "batch_meta.json").write_text(
                json.dumps({"items": [{"id": "U1", "question_type": "自创题型"}]}))
            manifest = mf.new_manifest("test", folder, {}, {})
            review_pass = {"passed": True, "errors": [], "counts": {
                "blocking_findings": 0, "open_manual_items": 0, "required_unchecked": 0}}
            with patch.object(runner.review, "evaluate", return_value=review_pass), \
                 patch.object(runner, "flowchart_checks", return_value={"passed": True, "errors": []}):
                self.assertEqual(runner.do_review_gate(folder, manifest), 1)
            report = json.loads((folder / "07_review" / "review-gate.json").read_text())
            self.assertFalse(report["passed"])
            self.assertFalse(report["label_audit"]["passed"])
            self.assertEqual(manifest["stages"]["review_gate"]["status"], mf.BLOCKED)
