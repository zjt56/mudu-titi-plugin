# -*- coding: utf-8 -*-
"""批次列表面板：进程外投影快照必须按内容指纹校验后再用。

冷投影 13 个批次要 8-9 秒，前端 15 秒就放弃；快照让重启后的首屏直接复用，
但不能因为「文件存在」就相信它——指纹不符的批次必须重算。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from workbench import server


class OverviewSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=tempfile.gettempdir())
        root = Path(self.tmp.name)
        self.workflows = root / "workflows"
        self.batch = self.workflows / "paper-a"
        (self.batch / "workflow").mkdir(parents=True)
        (self.batch / "workflow/manifest.json").write_text("{}", encoding="utf-8")
        self.cache = root / "cache"
        self.overview = {"name": "paper-a", "counts": {"ok": 3}}
        server._OVERVIEW_CACHE.clear()
        server._OVERVIEW_SNAPSHOT = None

    def tearDown(self):
        server._OVERVIEW_CACHE.clear()
        server._OVERVIEW_SNAPSHOT = None
        self.tmp.cleanup()

    def _patches(self):
        return (mock.patch.object(server, "WORKFLOWS", self.workflows),
                mock.patch.dict("os.environ", {"WF_CACHE_DIR": str(self.cache)}))

    def test_snapshot_round_trips_for_the_same_fingerprint(self):
        workflows, env = self._patches()
        with workflows, env:
            key = (str(self.batch), "fingerprint-1")
            server._OVERVIEW_CACHE[key] = self.overview
            server._save_overview_snapshot()
            self.assertTrue((self.cache / "overview-snapshot.json").is_file())
            server._OVERVIEW_CACHE.clear()
            server._OVERVIEW_SNAPSHOT = None
            saved = server._load_overview_snapshot()
            self.assertEqual(saved["paper-a"]["value"], self.overview)
            self.assertEqual(saved["paper-a"]["key"], "fingerprint-1")
            with mock.patch.object(server, "_overview_key", return_value="fingerprint-1"), \
                    mock.patch.object(server, "_overview_uncached", side_effect=AssertionError("不得重算")), \
                    mock.patch.object(server, "_live_overview", side_effect=lambda value, *a: value):
                self.assertEqual(server.overview("paper-a"), self.overview)

    def test_stale_fingerprint_recomputes_instead_of_reusing(self):
        workflows, env = self._patches()
        with workflows, env:
            server._OVERVIEW_CACHE[(str(self.batch), "old")] = self.overview
            server._save_overview_snapshot()
            server._OVERVIEW_CACHE.clear()
            server._OVERVIEW_SNAPSHOT = None
            fresh = {"name": "paper-a", "counts": {"ok": 9}}
            with mock.patch.object(server, "_overview_key", return_value="new"), \
                    mock.patch.object(server, "_overview_uncached", return_value=fresh) as recompute, \
                    mock.patch.object(server, "_live_overview", side_effect=lambda value, *a: value):
                self.assertEqual(server.overview("paper-a"), fresh)
                self.assertEqual(recompute.call_count, 1)

    def test_snapshot_is_ignored_when_the_file_is_corrupt(self):
        workflows, env = self._patches()
        with workflows, env:
            self.cache.mkdir(parents=True, exist_ok=True)
            (self.cache / "overview-snapshot.json").write_text("{not json", encoding="utf-8")
            server._OVERVIEW_SNAPSHOT = None
            self.assertEqual(server._load_overview_snapshot(), {})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
