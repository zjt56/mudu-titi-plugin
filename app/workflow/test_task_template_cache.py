import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import agent_tasks as tasks


class TaskTemplateCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.batch = Path(self.temp.name)
        self.write("01_source/正文.txt", "1. Source question")
        self.write("01_source/structure.confirmed.json", json.dumps({"units": [
            {"unit_id": "q-1", "unit_kind": "standalone", "numbers": [1]}]}))
        self.write("01_source/附录.txt", "1. A")
        tasks._TASK_TEMPLATE_CACHE.clear()
        self.addCleanup(tasks._TASK_TEMPLATE_CACHE.clear)

    def write(self, path, text):
        dest = self.batch / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")

    def test_reuses_template_and_defensively_copies(self):
        with patch.object(tasks, "_build_task_template", wraps=tasks._build_task_template) as build:
            first = tasks.build_task(self.batch, "answers")
            first["input_hashes"].clear()
            first["preconditions"].append("tampered")
            second = tasks.build_task(self.batch, "answers")
            self.assertEqual(build.call_count, 1)
            self.assertTrue(second["input_hashes"])
            self.assertNotIn("tampered", second["preconditions"])

    def test_input_optional_arrival_rules_and_budget_invalidate(self):
        with patch.object(tasks, "_build_task_template", wraps=tasks._build_task_template) as build:
            before = tasks.build_task(self.batch, "answers")
            self.write("01_source/正文.txt", "1. Changed question")
            after = tasks.build_task(self.batch, "answers")
            self.assertNotEqual(before["prompt"], after["prompt"])
            self.write("01_source_ans/正文.txt", "1. B")
            arrived = tasks.build_task(self.batch, "answers")
            self.assertIn("1. B", arrived["prompt"])
            with patch.dict(tasks.RULES, {"answers": "Changed rules"}):
                self.assertIn("Changed rules", tasks.build_task(self.batch, "answers")["prompt"])
            tasks.build_task(self.batch, "answers", max_input_chars=200000)
            self.assertEqual(build.call_count, 5)

    def test_assembly_rules_do_not_invalidate_answer_task(self):
        before = tasks.build_task(self.batch, "answers")
        constraints = tasks.ROOT / "skills-reference/constraints.md"
        original = constraints.read_text(encoding="utf-8")
        try:
            constraints.write_text(original + "\n临时装配版式规则\n", encoding="utf-8")
            after = tasks.build_task(self.batch, "answers")
        finally:
            constraints.write_text(original, encoding="utf-8")
        self.assertEqual(before["prompt"], after["prompt"])

    def test_assembly_rules_invalidate_assemble_task(self):
        self.write("01_source/structure.json", json.dumps({"sections": [], "groups": []}))
        self.write("01_source/metadata.json", "{}")
        self.write("03_types/types.json", json.dumps({"items": [{"unit_id": "q-1", "type": "语音题", "subtypes": [], "words": 0, "contentComplete": True, "evidence": ["正文 L1"]}]}))
        self.write("04_knowledge/knowledge.json", json.dumps({"items": [{"unit_id": "q-1", "knowledge": ["语音/基本读音/字母在单词中的基本读音"], "subquestions": [], "evidence": ["正文 L1"]}]}))
        self.write("04_knowledge/explanations.json", json.dumps({"items": [{"unit_id": "q-1", "explanation": "无", "articleSummary": "无", "subquestions": [], "evidence": ["正文 L1"]}]}))
        before = tasks.build_task(self.batch, "assemble")
        constraints = tasks.ROOT / "skills-reference/constraints.md"
        original = constraints.read_text(encoding="utf-8")
        try:
            constraints.write_text(original + "\n临时装配版式规则\n", encoding="utf-8")
            after = tasks.build_task(self.batch, "assemble")
        finally:
            constraints.write_text(original, encoding="utf-8")
        self.assertNotEqual(before["prompt"], after["prompt"])

    def test_cache_hit_does_not_bypass_path_checks(self):
        tasks.build_task(self.batch, "answers")
        path = self.batch / "01_source/正文.txt"
        target = self.batch / "other.txt"
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            tasks.build_task(self.batch, "answers")


if __name__ == "__main__":
    unittest.main()
