# -*- coding: utf-8 -*-
"""题组合并显示：一个入库单元一行，整组材料与全部小题一次呈现。"""
import unittest
from pathlib import Path

from workbench.server import detail, overview


SAMPLE = Path(__file__).resolve().parent.parent / "workflows" / "_real_2025"
requires_sample = unittest.skipUnless(SAMPLE.is_dir(), "需要本地样例批次 workflows/_real_2025")


@requires_sample
class MergedUnitTest(unittest.TestCase):
    def test_batch_lists_units_not_single_questions(self):
        data = overview("_real_2025")
        self.assertEqual(data["unit_total"], 34)
        self.assertGreaterEqual(data["question_total"], 75)
        group = next(row for row in data["rows"] if row["unit_id"] == "AH-2025-031")
        self.assertEqual(group["numbers"], list(range(31, 41)))
        self.assertEqual(group["unit_kind"], "group")
        self.assertEqual(group["label"], "31–40")

    def test_group_detail_exposes_every_subquestion(self):
        data = detail("_real_2025", "AH-2025-031")
        self.assertEqual(data["unit_kind"], "group")
        self.assertEqual(len(data["subquestions"]), 10)
        self.assertEqual([sub["number"] for sub in data["subquestions"]], list(range(31, 41)))
        self.assertTrue(all(len(sub["options"]) == 4 for sub in data["subquestions"]))
        preview = data["source_preview"]
        for number in range(31, 41):
            self.assertIn(str(number), preview)
        self.assertNotIn("(　)41.", preview)
        self.assertIn("Ⅵ.完形填空", preview)

    def test_reading_group_keeps_shared_passage(self):
        data = detail("_real_2025", "AH-2025-073")
        preview = data["source_preview"]
        self.assertIn("Nico", preview)
        for number in (73, 74, 75):
            self.assertIn(f"{number}.", preview)
        self.assertTrue(data["material"])

    def test_single_question_still_merges(self):
        data = detail("_real_2025", "AH-2025-001")
        self.assertEqual(data["numbers"], [1])
        self.assertEqual(len(data["subquestions"]), 1)
        images = [block["name"] for block in data["preview_blocks"] if block["type"] == "image"]
        self.assertEqual(images, ["image5.jpeg", "image6.jpeg", "image7.jpeg"])

    def test_old_per_question_id_still_resolves(self):
        data = detail("_real_2025", "Q001")
        self.assertEqual(data["unit_id"], "AH-2025-001")
        self.assertIn("What is Sandy", data["source_preview"])


if __name__ == "__main__":
    unittest.main()
