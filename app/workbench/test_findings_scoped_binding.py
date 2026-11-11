# -*- coding: utf-8 -*-
"""待审核标签必须按来源身份绑定，审核投影必须可缓存。

背景：用户 2026-11-11 反馈「课时刚录的有差异却没有待审核标签」。根因是列表行按
题号数字绑定差异，而 scoped-v1 批次里每个大题都从 1 重新编号，`sec-A-u1` 与
`sec-B-u1` 的 `as_number()` 都是 `1`，导致别的大题的差异被算到本行、本行真实
差异反而漏掉。

同一轮还发现切换题目每次都要重验全批逐字绑定（每行 0.8-0.9 秒），因此
`review_snapshot` 按输入指纹缓存；缓存必须返回深拷贝，调用方改了返回值不能污染
缓存，输入文件变化后必须重新读取。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from . import server


def _finding(unit_id, field="knowledge"):
    return {"finding_id": "f-" + unit_id, "unit_id": unit_id, "field": field,
            "status": "open", "reason": "与原标签不一致"}


class FindingsScopedBindingTest(unittest.TestCase):
    def setUp(self):
        self.queue = {"items": [_finding("sec-A-u1"), _finding("sec-B-u1"),
                                {"finding_id": "batch-level", "status": "open",
                                 "reason": "整批差异"}]}
        self.number_map = {"sec-A-u1": "1", "sec-B-u1": "1"}

    def test_same_printed_number_only_marks_its_own_unit(self):
        got = server.findings_for(self.queue, ["1"], self.number_map,
                                  unit_id="sec-A-u1", scoped=True)
        self.assertEqual([item["unit_id"] for item in got], ["sec-A-u1"])

        other = server.findings_for(self.queue, ["1"], self.number_map,
                                    unit_id="sec-B-u1", scoped=True)
        self.assertEqual([item["unit_id"] for item in other], ["sec-B-u1"])

    def test_scoped_unit_keeps_every_finding_on_that_unit(self):
        self.queue["items"].append(_finding("sec-A-u1", field="explanation"))
        got = server.findings_for(self.queue, ["1"], self.number_map,
                                  unit_id="sec-A-u1", scoped=True)
        self.assertEqual(len(got), 2)

    def test_batch_level_finding_is_not_pinned_to_a_row(self):
        got = server.findings_for(self.queue, ["1"], self.number_map,
                                  unit_id="sec-A-u1", scoped=True)
        self.assertNotIn("batch-level", [item.get("finding_id") for item in got])

    def test_legacy_batches_still_bind_by_printed_number(self):
        queue = {"items": [_finding("LOCAL-01"), _finding("LOCAL-02")]}
        got = server.findings_for(queue, ["1", "2"], {"LOCAL-01": "1", "LOCAL-02": "2"})
        self.assertEqual([item["unit_id"] for item in got], ["LOCAL-01", "LOCAL-02"])

    def test_scoped_rows_ignore_numbers_entirely(self):
        # 数字列表里没有 1 也不影响：scoped 只认单元身份。
        got = server.findings_for(self.queue, [], self.number_map,
                                  unit_id="sec-B-u1", scoped=True)
        self.assertEqual([item["unit_id"] for item in got], ["sec-B-u1"])


class ReviewSnapshotCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="review-cache-")
        self.folder = Path(self.tmp.name) / "batch"
        (self.folder / "07_review").mkdir(parents=True)
        self.rounds = self.folder / "07_review/review-rounds.json"
        self.rounds.write_text(json.dumps({"rounds": []}), encoding="utf-8")
        server._REVIEW_SNAPSHOT_CACHE.clear()
        self.calls = 0

    def tearDown(self):
        server._REVIEW_SNAPSHOT_CACHE.clear()
        self.tmp.cleanup()

    def _fake(self, *_args, **_kwargs):
        self.calls += 1
        return {"generated": self.calls, "rounds": [{"round_id": "labels"}]}

    def test_second_call_reuses_projection(self):
        with mock.patch.object(server, "_review_snapshot_uncached", side_effect=self._fake):
            first = server.review_snapshot(self.folder)
            second = server.review_snapshot(self.folder)
        self.assertEqual(self.calls, 1)
        self.assertEqual(first, second)

    def test_cache_returns_deep_copy(self):
        with mock.patch.object(server, "_review_snapshot_uncached", side_effect=self._fake):
            first = server.review_snapshot(self.folder)
            first["rounds"].append({"round_id": "sample"})
            second = server.review_snapshot(self.folder)
        self.assertEqual(len(second["rounds"]), 1)

    def test_input_change_invalidates_projection(self):
        with mock.patch.object(server, "_review_snapshot_uncached", side_effect=self._fake):
            server.review_snapshot(self.folder)
            self.rounds.write_text(json.dumps({"rounds": [], "note": "changed"}), encoding="utf-8")
            server.review_snapshot(self.folder)
        self.assertEqual(self.calls, 2)

    def test_missing_inputs_still_cache(self):
        self.rounds.unlink()
        with mock.patch.object(server, "_review_snapshot_uncached", side_effect=self._fake):
            server.review_snapshot(self.folder)
            server.review_snapshot(self.folder)
        self.assertEqual(self.calls, 1)


if __name__ == "__main__":
    unittest.main()
