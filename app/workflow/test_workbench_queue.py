# -*- coding: utf-8 -*-
"""待审口径：一般（minor）与“已接受”的标签发现仍然要人看，只有已修复/驳回才清除；
题面/选项/表格/图片等格式问题归前置流程，单独计数，不算标签待审。"""
import json
import pathlib
import unittest
from pathlib import Path

from workbench.server import as_number, findings_for, is_open, overview, split_findings

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_queue(batch: str) -> dict:
    path = ROOT / "workflows" / batch / "07_review" / "manual-review-queue.json"
    return json.loads(path.read_text(encoding="utf-8"))


SAMPLE = Path(__file__).resolve().parent.parent / "workflows" / "_real_2025"
requires_sample = unittest.skipUnless(SAMPLE.is_dir(), "需要本地样例批次 workflows/_real_2025")


@requires_sample
class QueueSemanticsTest(unittest.TestCase):
    def test_numbers_normalise_across_id_styles(self):
        self.assertEqual(as_number("LOCAL-01"), "1")
        self.assertEqual(as_number("Q001"), "1")
        self.assertEqual(as_number(1), "1")
        self.assertEqual(as_number("AH-2025-031"), "31")
        self.assertEqual(as_number(""), "")

    def test_minor_and_accepted_still_need_human(self):
        queue = {"items": [
            {"finding_id": "F1", "unit_id": "LOCAL-01", "severity": "minor", "status": "accepted"},
            {"finding_id": "F2", "unit_id": "LOCAL-01", "severity": "minor", "status": "fixed"},
            {"finding_id": "F3", "unit_id": "Q001", "severity": "minor", "status": "rejected"},
        ]}
        found = findings_for(queue, [1], {})
        self.assertEqual([f["finding_id"] for f in found], ["F1", "F2", "F3"])
        self.assertTrue(is_open(found[0]))
        self.assertFalse(is_open(found[1]))
        self.assertFalse(is_open(found[2]))

    def test_real_batch_counts_general_items_as_human(self):
        data = overview("_real_2025")
        flagged = [row for row in data["rows"] if row.get("needs_human")]
        self.assertTrue(flagged, "真实批次应至少有一道题的标签待审")
        self.assertEqual(data["needs_human"], len(flagged))
        for row in flagged:
            self.assertGreater(row["open_count"], 0)
            self.assertIn(row["severity"], ("major", "minor"))
        # 一律按队列里的实际状态算，不写死某道题（人工会在看台上把问题处理掉）。
        queue = load_queue("_real_2025")
        resolvable = [item for item in queue["items"] if item.get("status") in ("fixed", "rejected")]
        self.assertTrue(resolvable, "队列里应有已修复/驳回的样本用来验证清除逻辑")
        for item in resolvable[:5]:
            number = as_number(item.get("unit_id"))
            if not number:
                continue
            found = findings_for(queue, [int(number)], {})
            self.assertTrue(all(not is_open(f) for f in found if f.get("status") in ("fixed", "rejected")))

    def test_format_findings_do_not_mark_labels(self):
        queue = {"items": [
            {"finding_id": "F1", "unit_id": "AH-2025-001", "field": "options", "status": "accepted"},
            {"finding_id": "F2", "unit_id": "AH-2025-001", "field": "evidence", "status": "open"},
        ]}
        found = findings_for(queue, [1], {})
        label, fmt, other = split_findings(found)
        self.assertEqual([f["finding_id"] for f in fmt], ["F1", "F2"])
        self.assertEqual(label, [])
        self.assertEqual(other, [])


if __name__ == "__main__":
    unittest.main()
