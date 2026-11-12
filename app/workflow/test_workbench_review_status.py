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

    def test_stale_round_keeps_history_without_claiming_current_completion(self):
        import json
        payload = {"rounds": [{"round_id": "labels", "execution_id": "real-run",
                              "checks": [{"unit_id": "q-1", "verdict": "ok"}]}]}
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "07_review").mkdir()
            (folder / "07_review/review-rounds.json").write_text(json.dumps(payload), encoding="utf-8")
            with patch("workflow.review.evaluate_rounds", return_value={
                    "checked_rounds": [], "execution_completed": False, "passed": False}):
                status, records = server.review_snapshot(folder)
            self.assertEqual(records, {})
            first, second = status["rounds"]
            self.assertEqual(first["state"], "needs_refresh")
            self.assertEqual(first["historical_reviewed"], 1)
            self.assertFalse(first["completed"])
            self.assertEqual(first["reviewed"], 0)
            self.assertEqual(second["state"], "not_started")
            with patch("workflow.review.evaluate_rounds", return_value={
                    "checked_rounds": ["labels"], "execution_completed": False, "passed": False}):
                status, records = server.review_snapshot(folder)
            self.assertEqual(status["rounds"][0]["state"], "completed")
            self.assertEqual(status["rounds"][0]["reviewed"], 1)
            self.assertIn("labels", records)

    def test_cache_key_tracks_all_declared_inputs_and_nested_drafts(self):
        import json
        import os
        from workflow import review
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for relative in (*review.ROUND_INPUTS,
                             '01_source/materials/attachment_001.txt',
                             'candidates/units/scoped-u1.json',
                             'workflow/review-attempts/round-one/response.json'):
                with self.subTest(input=relative):
                    target = folder / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text('{}')
                    before = server._review_inputs_key(folder)
                    info = target.stat()
                    target.write_text('[]')
                    os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
                    self.assertNotEqual(before, server._review_inputs_key(folder))
            media = folder / '01_source/images/one.png'
            media.parent.mkdir(parents=True)
            media.write_bytes(b'old')
            (folder / 'workflow/media-observations.json').write_text(json.dumps({
                'observations': [{'path': '01_source/images/one.png'}]}))
            before = server._review_inputs_key(folder)
            media.write_bytes(b'new')
            self.assertNotEqual(before, server._review_inputs_key(folder))

    def test_cached_snapshot_rechecks_when_original_label_changes(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            target = folder / '04_knowledge/knowledge.json'
            target.parent.mkdir(parents=True)
            target.write_text('old')
            def evaluate(*args):
                complete = target.read_text() == 'new'
                return {'checked_rounds': ['labels'] if complete else [],
                        'execution_completed': False, 'passed': False}
            with patch('workflow.review.evaluate_rounds', side_effect=evaluate) as evaluator:
                server.review_snapshot(folder)
                server.review_snapshot(folder)
                self.assertEqual(evaluator.call_count, 1)
                target.write_text('new')
                status, _ = server.review_snapshot(folder)
                self.assertEqual(evaluator.call_count, 2)
                self.assertEqual(status['rounds'][0]['state'], 'completed')

    def test_without_skip_marker_listening_units_stay_unverified(self):
        data = self.run_overview(False, {}, [])
        by_id = {row["unit_id"]: row for row in data["rows"]}
        self.assertFalse(by_id["q-1"].get("excluded"))
        self.assertEqual(data["counts"]["unverified"], 3)


if __name__ == "__main__":
    unittest.main()
