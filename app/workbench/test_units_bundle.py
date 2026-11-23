# -*- coding: utf-8 -*-
"""Tests for GET /api/batches/<name>/units whole-paper bundle preloading."""
from __future__ import annotations

import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen

from workbench import server


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_regular_batch(workflows: Path, name: str) -> Path:
    folder = workflows / name
    (folder / "01_source" / "media").mkdir(parents=True, exist_ok=True)
    (folder / "01_source" / "media" / "pic1.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (folder / "01_source" / "正文.txt").write_text(
        "Ⅰ. 单项选择\n"
        "1. What is this?\n"
        "[IMG pic1.png]\n"
        "A. An apple  B. A book\n"
        "Ⅱ. 完形填空\n"
        "Read the passage carefully.\n"
        "2. She ____ to school every day.\n"
        "A. go  B. goes\n"
        "3. He ____ football yesterday.\n"
        "A. play  B. played\n",
        encoding="utf-8",
    )
    _write_json(folder / "workflow" / "manifest.json", {
        "workflow_id": name,
        "status": "completed",
        "meta": {"title": "2026年测试试卷", "source": "2026年测试试卷", "year": 2026},
        "stages": {
            "extract": {"status": "completed"},
            "structure_gate": {"status": "completed"},
        },
    })
    _write_json(folder / "07_review" / "report.json", {
        "meta": {"title": "2026年测试试卷", "source": "2026年测试试卷", "year": 2026},
        "rows": [
            {
                "unit_id": "U1", "unit_kind": "standalone", "number": 1, "numbers": [1],
                "label": "1", "type": "单项选择", "answer": "A", "difficulty": "简单",
                "knowledge": ["语法/冠词/不定冠词"], "state": "ok",
            },
            {
                "unit_id": "U2", "unit_kind": "group", "number": 2, "numbers": [2, 3],
                "label": "2-3", "type": "完形填空", "difficulty": "适中",
                "knowledge": ["主题/人与自我/生活与学习/学校生活"], "state": "attention",
                "subquestions": [
                    {"number": 2, "stem": "She ____ to school.", "options": ["A. go", "B. goes"], "answer": "B"},
                    {"number": 3, "stem": "He ____ football.", "options": ["A. play", "B. played"], "answer": "B"},
                ],
            },
        ],
    })
    _write_json(folder / "06_batch" / "batch_meta.json", {
        "batch": name,
        "source": "2026年测试试卷",
        "year": 2026,
        "items": [
            {
                "unit_id": "U1", "number": 1, "question_type": "单项选择",
                "stem": "What is this?", "options": ["A. An apple", "B. A book"],
                "answer": "A", "difficulty": "简单", "knowledge": ["语法/冠词/不定冠词"],
            },
            {
                "unit_id": "U2", "question_type": "完形填空",
                "material": "Read the passage carefully.",
                "difficulty": "适中", "knowledge": ["主题/人与自我/生活与学习/学校生活"],
                "subquestions": [
                    {"number": 2, "stem": "She ____ to school.", "options": ["A. go", "B. goes"], "answer": "B"},
                    {"number": 3, "stem": "He ____ football.", "options": ["A. play", "B. played"], "answer": "B"},
                ],
            },
        ],
    })
    _write_json(folder / "07_review" / "manual-review-queue.json", {
        "items": [
            {
                "finding_id": "F1", "unit_id": "U2", "field": "knowledge",
                "status": "open", "severity": "major", "reason": "知识点可补充",
            },
        ],
    })
    return folder


def _make_scoped_batch(workflows: Path, name: str) -> Path:
    folder = workflows / name
    (folder / "01_source").mkdir(parents=True, exist_ok=True)
    (folder / "01_source" / "正文.txt").write_text(
        "Ⅰ. 词汇运用\n"
        "1. First item\n"
        "Ⅱ. 句型转换\n"
        "1. Second item\n",
        encoding="utf-8",
    )
    _write_json(folder / "workflow" / "manifest.json", {
        "workflow_id": name,
        "status": "completed",
        "stages": {"extract": {"status": "completed"}},
    })
    _write_json(folder / "01_source" / "structure.confirmed.json", {
        "units": [
            {
                "unit_id": "SEC1-Q1", "unit_kind": "standalone", "identity_version": "scoped-v1",
                "section_id": "SEC1", "source_section": {"title": "Ⅰ. 词汇运用"},
                "numbers": [1], "printed_numbers": [1], "question_ids": {"1": "SEC1-Q1"},
            },
            {
                "unit_id": "SEC2-Q1", "unit_kind": "standalone", "identity_version": "scoped-v1",
                "section_id": "SEC2", "source_section": {"title": "Ⅱ. 句型转换"},
                "numbers": [1], "printed_numbers": [1], "question_ids": {"1": "SEC2-Q1"},
            },
        ],
    })
    _write_json(folder / "07_review" / "report.json", {
        "rows": [
            {
                "unit_id": "SEC1-Q1", "unit_kind": "standalone", "identity_version": "scoped-v1",
                "number": 1, "numbers": [1], "label": "1", "type": "单词拼写", "answer": "cat",
            },
            {
                "unit_id": "SEC2-Q1", "unit_kind": "standalone", "identity_version": "scoped-v1",
                "number": 1, "numbers": [1], "label": "1", "type": "句型转换", "answer": "Did he",
            },
        ],
    })
    _write_json(folder / "07_review" / "manual-review-queue.json", {
        "items": [
            {"finding_id": "SF1", "unit_id": "SEC2-Q1", "field": "type", "status": "open", "reason": "题型待确认"},
        ],
    })
    return folder


class UnitsBundleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._orig_workflows = server.WORKFLOWS
        self._orig_inbox = server.INBOX
        root = Path(self._tmp.name)
        server.WORKFLOWS = (root / "workflows").resolve()
        server.INBOX = (root / "inbox").resolve()
        server.WORKFLOWS.mkdir(parents=True, exist_ok=True)
        server.INBOX.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        server.WORKFLOWS = self._orig_workflows
        server.INBOX = self._orig_inbox
        self._tmp.cleanup()

    def test_regular_bundle_matches_single_detail_for_all_units(self):
        _make_regular_batch(server.WORKFLOWS, "paper-reg")
        bundle = server.units_bundle("paper-reg")
        self.assertEqual(bundle["batch"], "paper-reg")
        self.assertEqual(bundle["order"], ["U1", "U2"])
        self.assertIn("taxonomy", bundle["shared"])
        self.assertIn("paper", bundle["shared"])
        for uid in bundle["order"]:
            hydrated = {**bundle["shared"], **bundle["units"][uid]}
            single = server.detail("paper-reg", uid)
            self.assertEqual(hydrated, single, f"Mismatch for unit {uid}")

    def test_scoped_bundle_matches_single_detail_and_isolates_findings(self):
        _make_scoped_batch(server.WORKFLOWS, "paper-scoped")
        bundle = server.units_bundle("paper-scoped")
        self.assertEqual(bundle["order"], ["SEC1-Q1", "SEC2-Q1"])
        for uid in bundle["order"]:
            hydrated = {**bundle["shared"], **bundle["units"][uid]}
            single = server.detail("paper-scoped", uid)
            self.assertEqual(hydrated, single, f"Mismatch for scoped unit {uid}")
        self.assertEqual(bundle["units"]["SEC1-Q1"]["open_findings"], 0)
        self.assertEqual(bundle["units"]["SEC2-Q1"]["open_findings"], 1)

    def test_http_handler_routes_bundle_and_keeps_single_unit_route(self):
        _make_regular_batch(server.WORKFLOWS, "paper-http")

        def dispatch_get(path):
            captured = {}
            handler = server.Handler.__new__(server.Handler)
            handler.path = path
            handler.send_json = lambda value, status=200: captured.update(status=status, body=value)
            server.Handler.do_GET(handler)
            return captured["status"], captured["body"]

        status, bundle = dispatch_get("/api/batches/paper-http/units")
        self.assertEqual(status, 200)
        self.assertEqual(bundle["order"], ["U1", "U2"])

        status_single, single = dispatch_get("/api/batches/paper-http/units/U1")
        self.assertEqual(status_single, 200)
        self.assertEqual({**bundle["shared"], **bundle["units"]["U1"]}, single)

    def test_units_bundle_disk_cache_survives_ram_clear_invalidates_on_edit_and_cleans_on_purge(self):
        import os
        from unittest.mock import patch

        folder = _make_regular_batch(server.WORKFLOWS, "paper-cache")
        cache_dir = Path(self._tmp.name) / "cache"
        with patch.dict(os.environ, {"WF_CACHE_DIR": str(cache_dir)}):
            server._UNITS_BUNDLE_CACHE.clear()
            server._REVIEW_SNAPSHOT_CACHE.clear()
            first = server.units_bundle("paper-cache")
            disk_file = server._units_bundle_disk_path(folder)
            self.assertTrue(disk_file.is_file())

            # Full RAM clear (simulated backend restart): detail() hits disk cache without rebuilding.
            server._UNITS_BUNDLE_CACHE.clear()
            server._REVIEW_SNAPSHOT_CACHE.clear()
            with patch.object(server, "_prepare_batch_detail_context", side_effect=AssertionError("should hit disk cache")):
                d_disk = server.detail("paper-cache", "U1")
            self.assertEqual(d_disk["labels"], first["units"]["U1"]["labels"])

            # Editing a unit's manual labels immediately invalidates both RAM and disk cache.
            _write_json(folder / "candidates" / "units" / "U1.json", {
                "unit_id": "U1", "type": "单项填空", "knowledge": ["语法/时态/一般现在时"],
                "knowledge_manually_saved": True,
            })
            d_updated = server.detail("paper-cache", "U1")
            self.assertEqual(d_updated["labels"]["knowledge"], ["语法/时态/一般现在时"])

            # Purging the batch clears RAM and deletes the disk cache file (zero leftover trace).
            server._clear_purged_batch_state("paper-cache")
            self.assertFalse(disk_file.exists())


if __name__ == "__main__":
    unittest.main()
