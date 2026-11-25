# -*- coding: utf-8 -*-
"""Regression tests for red anomaly projection on paper cards and unit lists."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from workbench import anomaly_views, batch_overview
from workflow import preview


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class AnomalyViewsTest(unittest.TestCase):
    def test_distinguishes_red_anomalies_from_yellow_label_diffs(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            manifest = {
                "status": "failed",
                "stages": {
                    "knowledge": {"status": "failed", "error": "校验未通过"},
                    "review_gate": {"status": "needs_review"},
                },
            }
            _write_json(folder / "workflow/manifest.json", manifest)
            _write_json(
                folder / "workflow/agent-runs/knowledge-run1/repair-progress-00.json",
                {
                    "stage": "knowledge",
                    "validated_units": ["u1", "u2"],
                    "failed_units": {"u3": "目录外知识点路径"},
                },
            )
            # Rule 17: deferred/warnings in upload-format.json must NOT count as hard errors.
            _write_json(
                folder / "07_review/upload-format.json",
                {"errors": [], "warnings": [{"unit_id": "u1", "message": "minor style"}], "deferred": ["audio"]},
            )
            rows = [
                {"unit_id": "u1", "number": "1", "type": "单项填空", "state": "ok", "needs_human": False},
                {
                    "unit_id": "u2",
                    "number": "2",
                    "type": "单项填空",
                    "state": "attention",
                    "verdict": "uncertain",
                    "needs_human": True,
                    "open_summary": "知识点：建议调整",
                },
                {"unit_id": "u3", "number": "3", "type": "完形填空", "state": "unverified", "needs_human": False},
                {"unit_id": "u4", "number": "4", "type": "", "state": "unverified", "needs_human": False},
                {
                    "unit_id": "u5",
                    "number": "5",
                    "type": "阅读理解",
                    "state": "attention",
                    "verdict": "error",
                    "needs_human": True,
                    "open_summary": "答案与原卷不符",
                },
                {"unit_id": "u6", "number": "6", "type": "", "excluded": True, "state": "excluded"},
            ]
            summary = anomaly_views.decorate_anomaly_rows(folder, rows, manifest=manifest, advance={"running": False})

            # u1 (ok), u2 (yellow label diff), u6 (excluded listening) must NOT be red anomalies.
            self.assertFalse(rows[0]["is_anomaly"])
            self.assertFalse(rows[1]["is_anomaly"])
            self.assertTrue(rows[1]["needs_human"])
            self.assertFalse(rows[5]["is_anomaly"])

            # u3 (failed knowledge unit), u4 (missing type), u5 (verdict == error) MUST be red anomalies.
            self.assertTrue(rows[2]["is_anomaly"])
            self.assertIn("目录外知识点路径", rows[2]["anomaly_summary"])
            self.assertTrue(rows[3]["is_anomaly"])
            self.assertIn("题型缺失", rows[3]["anomaly_summary"])
            self.assertTrue(rows[4]["is_anomaly"])
            self.assertIn("一审内容判错", rows[4]["anomaly_summary"])

            self.assertTrue(summary["has_anomaly"])
            self.assertEqual(summary["anomaly_unit_count"], 3)
            self.assertEqual(summary["anomaly_count"], 3)
            self.assertTrue(summary["stage_blocked"])

    def test_stuck_stage_surfaces_in_review_list_unless_actively_advancing(self):
        stuck = {"name": "b1", "flow_done": False, "stage_blocked": True, "advance": {"running": False}}
        running = {"name": "b2", "flow_done": False, "stage_blocked": True, "advance": {"running": True}}
        normal_queued = {"name": "b3", "flow_done": False, "stage_blocked": False, "advance": {"running": False}}

        self.assertFalse(batch_overview._is_queued_unfinished(stuck))
        self.assertTrue(batch_overview._is_queued_unfinished(running))
        self.assertTrue(batch_overview._is_queued_unfinished(normal_queued))

        payload = batch_overview.batch_list_payload([stuck, running, normal_queued], is_review_list=True)
        self.assertEqual([b["name"] for b in payload["batches"]], ["b1"])

    def test_preview_fallback_builds_rows_for_mid_stage_batch_without_batch_json(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            _write_json(
                folder / "06_batch/batch.base.json",
                {
                    "name": "mid-stage",
                    "items": [
                        {
                            "id": "sec-1-u1",
                            "unit_id": "sec-1-u1",
                            "identity_version": "scoped-v1",
                            "unit_kind": "standalone",
                            "number": 1,
                            "numbers": [1],
                            "stem": "1. Hello ______.",
                            "options": ["A. world", "B. earth"],
                        }
                    ],
                },
            )
            _write_json(
                folder / "03_types/types.json",
                {"items": [{"unit_id": "sec-1-u1", "type": "单项填空", "subtypes": ["四个选项"]}]},
            )
            rows = preview.batch_rows(folder, [])
            self.assertIsNotNone(rows)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["unit_id"], "sec-1-u1")
            self.assertEqual(rows[0]["type"], "单项填空")

    def test_unit_anomalies_ledger_quarantine_and_clear_roundtrip(self):
        from workflow import unit_anomalies

        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            self.assertFalse(unit_anomalies.should_quarantine({}, {"u1", "u2"}, {}))
            self.assertFalse(unit_anomalies.should_quarantine({}, {"u2"}, {"u2": {"diagnostics": []}}))
            self.assertTrue(unit_anomalies.should_quarantine({"u1": {}}, {"u2"}, {}))
            self.assertTrue(unit_anomalies.should_quarantine({"u1": {}}, {"u2"}, {"u2": {"diagnostics": []}}))

            unit_anomalies.record_anomalies(folder, "answers", {"u2": "Answer not verbatim-bound"})
            self.assertEqual(unit_anomalies.quarantined_unit_ids(folder, "answers"), {"u2"})

            from workflow import upload_payload
            blocked = upload_payload.build_payload({"items": []}, batch_dir=folder)
            self.assertTrue(
                any(err.get("code") == "UNIT_QUARANTINED" and err.get("unit_id") == "u2"
                    and "Answer not verbatim-bound" in str(err)
                    for err in blocked["errors"]),
                blocked["errors"],
            )

            rows = [
                {"unit_id": "u1", "number": "1", "type": "单项填空", "state": "ok", "needs_human": False},
                {"unit_id": "u2", "number": "2", "type": "单项填空", "state": "ok", "needs_human": False},
            ]
            summary = anomaly_views.decorate_anomaly_rows(folder, rows, manifest={"stages": {}}, advance={"running": False})
            self.assertFalse(rows[0]["is_anomaly"])
            self.assertTrue(rows[1]["is_anomaly"])
            self.assertIn("Answer not verbatim-bound", rows[1]["anomaly_summary"])
            self.assertTrue(summary["has_anomaly"])
            self.assertEqual(summary["anomaly_unit_count"], 1)

            resp = unit_anomalies.quarantined_response_for_unit(
                "answers",
                {"unit_id": "u2", "unit_kind": "standalone", "numbers": [2]},
                "Answer not verbatim-bound",
            )
            self.assertEqual(resp["status"], "completed")
            self.assertIn("2", resp["artifacts"]["01_source_ans/answers.json"]["missing"])

            unit_anomalies.clear_stage_anomalies(folder, "answers", ["u2"])
            self.assertEqual(unit_anomalies.quarantined_unit_ids(folder, "answers"), set())
            cleared = upload_payload.build_payload({"items": []}, batch_dir=folder)
            self.assertFalse(any(err.get("code") == "UNIT_QUARANTINED" for err in cleared["errors"]))

    def test_one_word_per_blank_merged_underline_is_not_flagged_as_red_anomaly(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            # u1：原卷四词连排成一条 32 字符长横线（结构证据成立）；u6：横线过短，空数冲突真实存在。
            _write_json(folder / "06_batch/batch.json", {"items": [
                {"unit_id": "u1", "stem": "________________________________, we should keep trying.",
                 "answer": "Last but not least"},
                {"unit_id": "u6", "stem": "He ______ the early bus.", "answer": "failed to catch"},
            ]})
            rows = [
                {
                    "unit_id": "u1",
                    "number": "1",
                    "type": "完成句子",
                    "state": "attention",
                    "verdict": "error",
                    "needs_human": True,
                    "open_summary": "labels：该答案四词填入一个空，与大题“每空一词”冲突。",
                },
                {
                    "unit_id": "u2",
                    "number": "2",
                    "type": "完成句子",
                    "state": "attention",
                    "verdict": "error",
                    "needs_human": True,
                    "open_summary": "labels：答案与原卷不符，且与大题“每空一词”冲突。",
                },
                {
                    "unit_id": "u3",
                    "number": "3",
                    "type": "单项填空",
                    "state": "attention",
                    "verdict": "error",
                    "needs_human": True,
                    "open_summary": "labels：题干全角标点与空格排版不规范，存在格式瑕疵。",
                },
                {
                    "unit_id": "u4",
                    "number": "4",
                    "type": "单项填空",
                    "state": "attention",
                    "verdict": "error",
                    "needs_human": True,
                    "open_summary": "labels：题干存在格式问题且缺选项D。",
                },
                {
                    "unit_id": "u5",
                    "number": "5",
                    "type": "完成句子",
                    "state": "attention",
                    "verdict": "error",
                    "needs_human": True,
                    "open_summary": "labels：题干下划线排版有问题，答案词形错误。",
                },
                {
                    "unit_id": "u6",
                    "number": "6",
                    "type": "完成句子",
                    "state": "attention",
                    "verdict": "error",
                    "needs_human": True,
                    "open_summary": "labels：该答案三词填入一个空，与大题“每空一词”冲突。",
                },
            ]
            summary = anomaly_views.decorate_anomaly_rows(folder, rows, manifest={"stages": {}}, advance={"running": False})
            # u1（每空一词连线，且横线结构证据成立）与 u3（纯标点/空格排版瑕疵）均不标红
            self.assertFalse(rows[0]["is_anomaly"])
            self.assertFalse(rows[2]["is_anomaly"])
            # u2（答案与原卷不符）与 u4（缺选项）含实质内容错误，仍须标红
            self.assertTrue(rows[1]["is_anomaly"])
            self.assertTrue(rows[3]["is_anomaly"])
            # u5（格式描述夹带答案词形错误）与 u6（横线长度不足以容纳答案词，空数冲突真实）必须标红
            self.assertTrue(rows[4]["is_anomaly"])
            self.assertTrue(rows[5]["is_anomaly"])
            self.assertEqual(summary["anomaly_unit_count"], 4)

    def test_format_filter_keeps_answer_semantic_errors_red(self):
        check = anomaly_views._is_source_format_only_complaint
        for text in (
            "题干下划线排版有问题，答案词形错误",
            "单复数错误，空格多余",
            "时态错误；换行断行",
            "标点全角，答案少词",
            "全角标点不规范，空位数量不确定",
            "缩进不齐，且与原句不一致",
        ):
            self.assertFalse(check({}, text), text)
        self.assertTrue(check({}, "题干全角标点与空格排版不规范"))
        # 每空一词：没有横线结构证据不豁免；小题横线总长足以容纳全部答案词才豁免。
        cue = "该答案四词填入一个空，与大题“每空一词”冲突。"
        self.assertFalse(check({}, cue))
        merged = {"subquestions": [{"stem": "________________________________, we should keep trying.",
                                    "answer": "Last but not least"}]}
        self.assertTrue(check(merged, cue))
        short = {"subquestions": [{"stem": "______, we should keep trying.", "answer": "Last but not least"}]}
        self.assertFalse(check(short, cue))
        self.assertFalse(check(merged, cue + "且答案时态错误"))
        # 横线连排类通用格式抱怨同样需要结构证据；批次条目可经惰性函数按 unit_id 提供。
        self.assertFalse(check({}, "原卷横线连排未断开"))
        self.assertTrue(check(merged, "原卷横线连排未断开"))
        self.assertTrue(check({"unit_id": "x1"}, cue, batch_item=lambda uid: merged if uid == "x1" else {}))

    def test_review_check_path_canonicalization_records_rewrite_log(self):
        from workflow import review_rounds

        checks = [{
            "unit_id": "q-1",
            "verdict": "ok",
            "rationale": "核对知识点路径。",
            "evidence": ["原文"],
            "assessment": {
                "type": "单项填空",
                "subtypes": ["四个选项"],
                "knowledge": ["语法/错误中间层/一般现在时"],
                "answer_points": [],
                "subquestions": [],
            },
        }]
        rewrites = []
        projected = review_rounds._project_review_checks(
            checks,
            units_by_id={"q-1": {"unit_id": "q-1", "unit_kind": "standalone"}},
            rewrite_log=rewrites,
        )
        self.assertEqual(len(projected), 1)
        if projected[0]["assessment"]["knowledge"] != ["语法/错误中间层/一般现在时"]:
            self.assertTrue(any("语法/错误中间层/一般现在时 -> " in entry for entry in rewrites), rewrites)


if __name__ == "__main__":
    unittest.main()
