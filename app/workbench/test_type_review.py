"""Tests for cross-batch paginated question-type review endpoint and helper."""
from __future__ import annotations

import json
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from workbench import server, taxonomy


def _make_batch(name: str, updated_at: str, units: list[dict], *, uploaded: bool = False) -> dict:
    return {
        "name": name,
        "updated_at": updated_at,
        "flow_finished_at": updated_at,
        "paper": {"title": f"试卷-{name}"},
        "uploaded": uploaded,
        "upload_pending_confirmation": False,
        "rows": units,
    }


def _make_unit(uid: str, number: int, qtype: str, *, needs_human: bool = False,
               excluded: bool = False, knowledge=None, subtypes=None) -> dict:
    return {
        "unit_id": uid,
        "number": str(number),
        "numbers": [number],
        "printed_numbers": [number],
        "label": str(number),
        "unit_kind": "standalone",
        "type": qtype,
        "subtypes": subtypes or [],
        "state": "excluded" if excluded else ("attention" if needs_human else "ok"),
        "needs_human": needs_human,
        "excluded": excluded,
        "open_summary": "知识点差异" if needs_human else "",
        "open_findings": 1 if needs_human else 0,
        "difficulty": "适中",
        "source_section": {"title": f"Section {qtype}", "context": "Section A"},
        "knowledge": knowledge or ["语法/动词/时态"],
        # Heavy payload fields that must NOT leak into paginated summary rows:
        "stem": "VERY_HEAVY_STEM_TEXT " * 20,
        "material": "VERY_HEAVY_MATERIAL " * 20,
        "blocks": [{"type": "text", "text": "heavy"}],
    }


class TypeReviewUnitsTests(unittest.TestCase):
    def setUp(self):
        self.batches = [
            _make_batch(
                "batch-newer",
                "2026-11-24T12:00:00+08:00",
                [_make_unit(f"n-{i}", i, "单项填空" if i % 2 == 0 else "单词拼写", needs_human=(i % 3 == 0))
                 for i in range(1, 26)],
            ),
            _make_batch(
                "batch-older",
                "2026-11-23T12:00:00+08:00",
                [
                    *[_make_unit(f"o-{i}", i, "阅读理解", needs_human=True) for i in range(1, 21)],
                    _make_unit("o-listen", 21, "听力短对话理解", needs_human=True, excluded=True),
                    _make_unit("o-untyped", 22, "", needs_human=False),
                ],
                uploaded=True,
            ),
        ]

    def test_defaults_to_all_types_caps_page_size_at_20_and_strips_heavy_fields(self):
        with patch.object(server, "cached_list_batches", return_value=self.batches):
            res1 = server.type_review_units(page=1, page_size=999)
            res2 = server.type_review_units(page=2, page_size=20)
            res3 = server.type_review_units(page=99, page_size=20)

        self.assertEqual(res1["max_page_size"], 20)
        self.assertEqual(res1["page_size"], 20)
        self.assertEqual(res1["total"], 47)
        self.assertEqual(res1["total_pages"], 3)
        self.assertEqual(res1["page"], 1)
        self.assertEqual(len(res1["items"]), 20)
        self.assertEqual(res1["selected_type"], "")
        # Newest batch comes first
        self.assertEqual(res1["items"][0]["batch_name"], "batch-newer")
        self.assertEqual(res1["items"][0]["unit_id"], "n-1")
        # Lightweight rows must not include heavy body blocks/stem/material
        for key in ("stem", "material", "blocks"):
            self.assertNotIn(key, res1["items"][0])

        self.assertEqual(res2["page"], 2)
        self.assertEqual(len(res2["items"]), 20)
        # Page 3 clamped from page=99 has the remaining 7 items
        self.assertEqual(res3["page"], 3)
        self.assertEqual(len(res3["items"]), 7)
        self.assertEqual(res3["items"][-1]["unit_id"], "o-untyped")

    def test_filters_by_type_status_query_and_reports_catalog_counts(self):
        with patch.object(server, "cached_list_batches", return_value=self.batches):
            reading = server.type_review_units(question_type="阅读理解")
            untyped = server.type_review_units(question_type=server.UNTYPED_TYPE_VALUE)
            open_only = server.type_review_units(status="open")
            searched = server.type_review_units(query="单词拼写 batch-newer")

        self.assertEqual(reading["selected_type"], "阅读理解")
        self.assertEqual(reading["total"], 20)
        self.assertTrue(all(it["type"] == "阅读理解" for it in reading["items"]))

        self.assertEqual(untyped["total"], 1)
        self.assertEqual(untyped["items"][0]["unit_id"], "o-untyped")

        # Excluded listening unit must not be returned under status=open
        self.assertFalse(any(it["unit_id"] == "o-listen" for it in open_only["items"]))
        self.assertEqual(open_only["total"], 8 + 20)  # n-{3,6,9,12,15,18,21,24} + 20 reading units

        self.assertEqual(searched["total"], 13)
        self.assertTrue(all(it["type"] == "单词拼写" and it["batch_name"] == "batch-newer" for it in searched["items"]))

        types_by_val = {entry["value"]: entry for entry in reading["types"]}
        for t_name in taxonomy.types():
            self.assertIn(t_name, types_by_val)
        self.assertEqual(types_by_val["阅读理解"]["count"], 20)
        self.assertEqual(types_by_val["阅读理解"]["pending"], 20)
        self.assertEqual(types_by_val[server.UNTYPED_TYPE_VALUE]["count"], 1)

    def test_http_get_route_returns_paginated_type_review_units(self):
        captured = {}

        class FakeHandler(server.Handler):
            def __init__(self, path):
                self.path = path
                self.headers = {}
                self.wfile = BytesIO()

            def _check_request_security(self, **kwargs):
                return True

            def send_json(self, payload, status=200):
                captured["status"] = status
                captured["payload"] = payload

        handler = FakeHandler("/api/type-review/units?type=%E5%8D%95%E9%A1%B9%E5%A1%AB%E7%A9%BA&page=1&page_size=50&status=all")
        with patch.object(server, "cached_list_batches", return_value=self.batches):
            handler.do_GET()

        self.assertEqual(captured["status"], 200)
        payload = captured["payload"]
        self.assertEqual(payload["selected_type"], "单项填空")
        self.assertEqual(payload["page_size"], 20)
        self.assertEqual(payload["total"], 12)
        self.assertEqual(len(payload["items"]), 12)

    def test_pending_count_matches_open_filter_and_supports_uploaded_filter(self):
        # Unit with state="attention" and needs_human=False must be counted identically
        # in all_pending, types[].pending, and status="open" results.
        attention_only = _make_unit("a-only", 99, "单项填空", needs_human=False)
        attention_only["state"] = "attention"
        batches = [
            _make_batch("b-unup", "2026-11-24T12:00:00+08:00", [attention_only], uploaded=False),
            _make_batch("b-up", "2026-11-23T12:00:00+08:00", [_make_unit("u-up", 1, "阅读理解", needs_human=True)], uploaded=True),
        ]
        with patch.object(server, "cached_list_batches", return_value=batches):
            res_all = server.type_review_units(status="all")
            res_open = server.type_review_units(status="open")
            res_unup = server.type_review_units(uploaded="unuploaded")
            res_up = server.type_review_units(uploaded="uploaded")

        self.assertEqual(res_all["all_pending"], res_open["total"])
        self.assertEqual(sum(t["pending"] for t in res_all["types"]), res_open["total"])
        self.assertEqual(res_open["total"], 2)
        self.assertEqual(res_unup["uploaded"], "unuploaded")
        self.assertEqual(res_unup["total"], 1)
        self.assertEqual(res_unup["items"][0]["batch_name"], "b-unup")
        self.assertEqual(res_up["uploaded"], "uploaded")
        self.assertEqual(res_up["total"], 1)
        self.assertEqual(res_up["items"][0]["batch_name"], "b-up")

    def test_unfinished_queued_batches_excluded_from_review_list_and_type_review_but_kept_in_lite(self):
        queued = _make_batch("b-queued", "2026-11-24T13:00:00+08:00",
                             [_make_unit("q-1", 1, "单项填空", needs_human=True)], uploaded=False)
        queued["flow_done"] = False
        queued["queue_removed"] = False

        done = _make_batch("b-done", "2026-11-24T12:00:00+08:00",
                           [_make_unit("d-1", 1, "阅读理解", needs_human=True)], uploaded=False)
        done["flow_done"] = True
        done["queue_removed"] = False

        staged = _make_batch("b-staged", "2026-11-24T11:00:00+08:00",
                             [_make_unit("s-1", 1, "单词拼写", needs_human=False)], uploaded=False)
        staged["flow_done"] = False
        staged["upload_pending_confirmation"] = True

        removed = _make_batch("b-removed", "2026-11-24T10:00:00+08:00",
                              [_make_unit("r-1", 1, "单项填空", needs_human=False)], uploaded=False)
        removed["flow_done"] = False
        removed["queue_removed"] = True

        mixed = [queued, done, staged, removed]

        def dispatch(path):
            captured = {}

            class FakeHandler(server.Handler):
                def __init__(self, p):
                    self.path = p
                    self.headers = {}
                    self.wfile = BytesIO()

                def _check_request_security(self, **kwargs):
                    return True

                def send_json(self, payload, status=200):
                    captured["status"] = status
                    captured["payload"] = payload

            h = FakeHandler(path)
            with patch.object(server, "cached_list_batches", return_value=mixed):
                h.do_GET()
            return captured["status"], captured["payload"]

        # 试卷审核列表（review_list=1）：排除上传排队区未完成批次，但保留 has_active_work 标记供轮询感知
        st_rev, pay_rev = dispatch("/api/batches?review_list=1")
        self.assertEqual(st_rev, 200)
        self.assertEqual([b["name"] for b in pay_rev["batches"]], ["b-done", "b-staged", "b-removed"])
        self.assertTrue(pay_rev["has_active_work"])

        # 上传排队区（lite=1）：保留未完成排队批次
        st_lite, pay_lite = dispatch("/api/batches?lite=1")
        self.assertEqual(st_lite, 200)
        self.assertEqual([b["name"] for b in pay_lite["batches"]], ["b-queued", "b-done", "b-staged", "b-removed"])

        # 题型审核（type_review_units）：同样排除排队区未完成批次
        with patch.object(server, "cached_list_batches", return_value=mixed):
            tr = server.type_review_units(page=1, page_size=20)
        self.assertEqual([it["batch_name"] for it in tr["items"]], ["b-done", "b-staged", "b-removed"])
        self.assertEqual(tr["all_total"], 3)


if __name__ == "__main__":
    unittest.main()
