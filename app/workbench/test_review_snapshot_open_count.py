# -*- coding: utf-8 -*-
"""审核卡片的“待人工审核差异”数必须等于活动队列里的未裁决条目。

背景：来源占位清理后，一条只因绑定过期而被退休的旧差异被正常恢复进
`07_review/manual-review-queue.json`，但它的 round_sources 指向已经不在当前
两轮计划里的旧轮次，`review.evaluate_rounds` 看不到它。卡片若只看 evaluated
findings 就会显示 12，而人工打开队列是 13 条，用户无法判断到底还差几条。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from workflow import review

from . import server


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _item(fid: str, status: str = "open") -> dict:
    return {"finding_id": fid, "unit_id": "unit-" + fid, "field": "type",
            "status": status, "reason": "题组 type 判断不一致", "round_sources": []}


class ReviewSnapshotCountTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="review-snapshot-")
        self.folder = Path(self.tmp.name) / "batch"
        (self.folder / "07_review").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _snapshot(self, items, findings):
        _write(self.folder / "07_review/review-rounds.json",
               {"plan": {"input_binding": "b", "questions": []}, "rounds": []})
        if items is not None:
            _write(self.folder / "07_review/manual-review-queue.json",
                   {"queue_version": "1.0", "items": items})
        evaluated = {"checked_rounds": [], "execution_completed": True, "passed": False,
                     "plan": {"questions": []}, "findings": findings}
        with mock.patch.object(review, "evaluate_rounds", return_value=evaluated):
            status, _ = server.review_snapshot(self.folder)
        return status

    def test_restored_historical_difference_is_counted(self):
        items = [_item("WF-0001"), _item("WF-0002"), _item("WF-0003", "resolved"),
                 _item("WF-0021")]
        status = self._snapshot(items, findings=[{"finding_id": "WF-0001"}])
        self.assertEqual(status["open_findings"], 3)

    def test_missing_queue_falls_back_to_evaluated_findings(self):
        status = self._snapshot(None, findings=[{"finding_id": "WF-0001"},
                                                {"finding_id": "WF-0002"}])
        self.assertEqual(status["open_findings"], 2)


if __name__ == "__main__":
    unittest.main()
