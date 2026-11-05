"""工作台总览：真实分轮结果与跳过听力的显示口径。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workbench import server


def rows():
    return [
        {"unit_id": "group-76-77-78-79-80", "number": "76", "numbers": [76, 77, 78, 79, 80],
         "type": "阅读理解", "state": "unverified", "needs_human": False, "open_count": 0},
        {"unit_id": "q-1", "number": "1", "numbers": [1], "type": "听力关键词语选择",
         "state": "unverified", "needs_human": False, "open_count": 0},
        {"unit_id": "q-31", "number": "31", "numbers": [31], "type": "单项填空",
         "state": "unverified", "needs_human": False, "open_count": 0},
    ]


class ReviewStatusOverviewTest(unittest.TestCase):
    def run_overview(self, folder, records, findings):
        manifest = {"stages": {}, "meta": {}, "inputs": {}}
        snapshot = {"execution_completed": True, "quality_passed": False, "rounds": [],
                    "question_total": 101, "open_findings": len(findings), "revision": "r1"}
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(server, "batch_dir", return_value=Path(tmp)), \
                patch.object(server, "report_for", return_value=({}, manifest, {})), \
                patch.object(server, "question_rows", return_value=[]), \
                patch.object(server, "merged_rows", return_value=rows()), \
                patch.object(server, "review_snapshot", return_value=(snapshot, records)), \
                patch.object(server, "paper_info", return_value={}), \
                patch.object(server, "media_audit") as media, \
                patch.object(server, "advance_state", return_value={}), \
                patch.object(server, "stage_cards", return_value={}), \
                patch.object(server, "diagnose", create=True):
            media.audit.return_value = {"summary": {}}
            with patch("workflow.diagnose.diagnose", return_value=None):
                Path(tmp, "02_audio").mkdir()
                if folder:
                    Path(tmp, "02_audio", "SKIP_LISTENING").write_text("", encoding="utf-8")
                return server.overview("新试卷")

    def test_rounds_come_from_bound_records_and_skipped_listening_is_not_unverified(self):
        records = {"labels": {"checks": [
                      {"unit_id": "group-76-77-78-79-80", "verdict": "ok"},
                      {"unit_id": "q-31", "verdict": "ok"}]},
                   "sample": {"checks": [
                      {"unit_id": "group-76-77-78-79-80", "number": "77", "verdict": "ok"}]}}
        data = self.run_overview(True, records, [])
        by_id = {row["unit_id"]: row for row in data["rows"]}
        group = by_id["group-76-77-78-79-80"]
        self.assertEqual(group["review_rounds"], ["labels", "sample"])
        self.assertEqual(group["verdict"], "ok")
        self.assertEqual(data["counts"]["ok"], 2)
        listening = by_id["q-1"]
        self.assertTrue(listening["excluded"])
        self.assertEqual(listening["state"], "excluded")
        self.assertEqual(data["counts"]["excluded"], 1)
        self.assertEqual(data["counts"]["unverified"], 0)
        self.assertEqual(data["review_status"]["excluded_units"], 1)

    def test_without_skip_marker_listening_units_stay_unverified(self):
        data = self.run_overview(False, {}, [])
        by_id = {row["unit_id"]: row for row in data["rows"]}
        self.assertFalse(by_id["q-1"].get("excluded"))
        self.assertEqual(data["counts"]["unverified"], 3)


if __name__ == "__main__":
    unittest.main()
