# -*- coding: utf-8 -*-
"""题组保存：题面逐小题落盘，标签整组只写一份。"""
import json
import tempfile
import unittest
from pathlib import Path

from workbench.server import Handler, load_unit_labels, merged_rows, report_for


def make_batch(root: Path) -> Path:
    folder = root / "batch_fixture"
    (folder / "candidates").mkdir(parents=True)
    batch = {
        "batch": "fixture", "subject": "english",
        "items": [{
            "id": "FX-001", "unit_kind": "group", "type": "完形填空",
            "material": "Passage with __31__ and __32__.",
            "subquestions": [
                {"number": 31, "stem": "31. first", "options": ["A. a", "B. b"], "answer": "A"},
                {"number": 32, "stem": "32. second", "options": ["A. c", "B. d"], "answer": "B"},
            ],
        }],
    }
    (folder / "06_batch").mkdir()
    (folder / "06_batch" / "batch.json").write_text(json.dumps(batch, ensure_ascii=False), encoding="utf-8")
    (folder / "01_source").mkdir()
    (folder / "01_source" / "正文.txt").write_text("Ⅵ.完形填空\n31. first\nA. a B. b\n32. second\nA. c B. d\n", encoding="utf-8")
    (folder / "candidates" / "LOCAL-31.json").write_text(
        json.dumps({"unit_id": "FX-001", "number": 31, "stem": "31. first", "answer": "A"}, ensure_ascii=False),
        encoding="utf-8")
    return folder


class GroupSaveTest(unittest.TestCase):
    def test_group_saves_content_per_question_but_labels_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = make_batch(Path(tmp))
            result = Handler.save_units(folder, "FX-001", [
                {"number": 31, "stem": "31. first edited", "answer": "A"},
                {"number": 32, "stem": "32. second edited", "answer": "B"},
            ], {"type": "完形填空", "difficulty": "适中",
                "knowledge": ["主题/人与自我/生活与学习/日常起居"], "subtypes": ["完形填空"]})
            self.assertEqual(len(result["saved"]), 3)
            one = json.loads((folder / "candidates" / "LOCAL-31.json").read_text(encoding="utf-8"))
            two = json.loads((folder / "candidates" / "LOCAL-Q32.json").read_text(encoding="utf-8"))
            self.assertEqual(one["stem"], "31. first edited")
            self.assertEqual(two["stem"], "32. second edited")
            self.assertEqual(two["number"], "32")
            self.assertEqual(two["unit_id"], "FX-001")
            self.assertTrue((folder / "candidates" / "LOCAL-31.json.bak").is_file())
            self.assertNotIn("knowledge", two)  # 标签不再散落到小题
            labels = json.loads((folder / "candidates" / "units" / "FX-001.json").read_text(encoding="utf-8"))
            self.assertEqual(labels["applies_to"], "unit")
            self.assertEqual(labels["numbers"], ["31", "32"])
            self.assertTrue(labels["inherit_subquestions"])
            self.assertEqual(labels["knowledge"], ["主题/人与自我/生活与学习/日常起居"])
            self.assertEqual(len(labels["knowledgeIds"]), 1)
            self.assertFalse(any("必须是叶子" in text for text in labels["labelWarnings"]))
            rows = merged_rows(folder, report_for(folder))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["numbers"], [31, 32])

    def test_clearing_knowledge_clears_previous_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = make_batch(Path(tmp))
            Handler.save_units(folder, "FX-001", [], {
                "type": "完形填空", "knowledge": ["主题/人与自我/生活与学习/日常起居"]})
            path = folder / "candidates" / "units" / "FX-001.json"
            self.assertTrue(json.loads(path.read_text())["knowledgeIds"])
            Handler.save_units(folder, "FX-001", [], {"knowledge": []})
            self.assertEqual(json.loads(path.read_text())["knowledgeIds"], [])

    def test_unit_labels_win_over_batch_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = make_batch(Path(tmp))
            (folder / "candidates" / "units").mkdir()
            (folder / "candidates" / "units" / "FX-001.json").write_text(json.dumps({
                "unit_id": "FX-001", "applies_to": "unit", "type": "完形填空",
                "difficulty": "困难", "knowledge": ["主题/人与自我/生活与学习/日常起居"],
            }, ensure_ascii=False), encoding="utf-8")
            labels = load_unit_labels(folder, "FX-001")
            self.assertEqual(labels["difficulty"], "困难")

    def test_group_detail_keeps_material_and_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_batch(Path(tmp))
            data = describe(Path(tmp))
            self.assertEqual(data["numbers"], [31, 32])
            self.assertIn("Passage with", data["material"] or "")
            self.assertEqual([s["number"] for s in data["subquestions"]], [31, 32])


def describe(root: Path):
    """在临时批次上复现 detail 的单元解析，不依赖全局 workflows 目录。"""
    folder = root / "batch_fixture"
    row = merged_rows(folder, report_for(folder))[0]
    return {
        "numbers": row["numbers"],
        "material": row.get("material"),
        "subquestions": row.get("subquestions"),
    }


if __name__ == "__main__":
    unittest.main()
