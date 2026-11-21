# -*- coding: utf-8 -*-
"""流程状态实时同步回归测试：覆盖列表快照失效、顶层完成态叠加、残留 activity 隔离与阶段报告指纹。"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from workbench import server
from workflow import contracts, execution_state


class StatusSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inbox = self.root / "inbox"
        self.workflows = self.root / "workflows"
        self.inbox.mkdir(parents=True)
        self.workflows.mkdir(parents=True)
        self.batch = self.workflows / "paper-sync"
        (self.batch / "workflow").mkdir(parents=True)
        (self.batch / "06_batch").mkdir(parents=True)
        (self.batch / "07_review").mkdir(parents=True)

        self.final_batch = self.batch / "06_batch" / "batch_meta.json"
        self.final_batch.write_text(json.dumps({"items": []}, ensure_ascii=False), encoding="utf-8")
        self.final_sha = hashlib.sha256(self.final_batch.read_bytes()).hexdigest()

        stages = {s.id: {"status": "completed"} for s in contracts.STAGES}
        for remote in execution_state.REMOTE_STAGES:
            stages[remote] = {"status": "pending"}
        stages["format_gate"] = {"status": "running"}
        self.manifest = {
            "workflow_id": "paper-sync",
            "status": "validating",
            "stages": stages,
        }
        self._write("workflow/manifest.json", self.manifest)

        self.addCleanup(patch.stopall)
        patch.object(server, "WORKFLOWS", self.workflows).start()
        patch.object(server, "INBOX", self.inbox).start()
        patch.object(server, "_batches_snapshot", None).start()
        patch.object(server, "_batches_epoch", 0).start()
        patch.object(server, "_batches_read_lock", threading.Lock()).start()
        patch.object(server, "_OVERVIEW_CACHE", {}).start()
        patch.object(server, "_OVERVIEW_SNAPSHOT", {}).start()
        patch.object(server, "_advance_state", {}).start()
        patch.object(server, "_upload_jobs", {}).start()

    def _write(self, relative: str, payload: dict):
        path = self.batch / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_cached_list_batches_busts_immediately_on_manifest_and_format_completion(self):
        first = server.cached_list_batches()
        self.assertEqual(len(first), 1)
        self.assertFalse(first[0]["flow_done"])

        # 无变化时 2 秒内命中快照，不重复构建
        with patch.object(server, "list_batches", side_effect=AssertionError("should hit snapshot")):
            cached = server.cached_list_batches()
            self.assertEqual(cached[0]["name"], "paper-sync")

        # 立即完成 format_gate 并写入 upload-format.json（远小于 2s TTL）
        self.manifest["status"] = "pending_confirmation"
        self.manifest["stages"]["format_gate"] = {"status": "completed"}
        self._write("workflow/manifest.json", self.manifest)
        self._write("07_review/upload-format.json", {"passed": True, "final_sha256": self.final_sha})

        second = server.cached_list_batches()
        self.assertTrue(second[0]["flow_done"], "format_gate 完成后 /api/batches 应立即同步 flow_done=True")
        self.assertEqual(second[0]["status"], "pending_confirmation")

    def test_cached_list_batches_busts_immediately_on_advance_state_change(self):
        server._advance_state["paper-sync"] = {"running": True}
        first = server.cached_list_batches()
        self.assertTrue(first[0]["advance"]["running"])

        # 后台线程结束运行，不走 @serialized HTTP POST，也应立即击穿 2s 快照
        server._advance_state["paper-sync"] = {"running": False, "finished_at": "2026-11-21T03:00:00+08:00"}
        second = server.cached_list_batches()
        self.assertFalse(second[0]["advance"]["running"])

    def test_completed_stage_is_not_forced_active_by_lingering_activity(self):
        # answers 已在 manifest 中标记 completed，但同进程残留 activity.json 仍指向 answers
        self._write("workflow/activity.json", {
            "stage": "answers",
            "detail": "答案填充收尾",
            "pid": os.getpid(),
            "token": "tok-1",
        })
        ov = server.overview("paper-sync")
        items = {c["id"]: c for c in ov["stage"]["items"]}
        self.assertEqual(items["answers"]["status"], "completed")
        self.assertFalse(items["answers"].get("active"), "已完成阶段不应再被残留 activity 标成处理中")
        self.assertNotEqual((ov["stage"].get("current") or {}).get("id"), "answers")

        # 若 activity 指向尚未完成的 format_gate，则正常高亮为处理中
        self._write("workflow/activity.json", {
            "stage": "format_gate",
            "detail": "正在执行格式校验",
            "pid": os.getpid(),
            "token": "tok-2",
        })
        ov2 = server.overview("paper-sync")
        items2 = {c["id"]: c for c in ov2["stage"]["items"]}
        self.assertTrue(items2["format_gate"].get("active"))
        self.assertEqual(ov2["stage"]["hint"], "正在执行格式校验")

    def test_live_overview_syncs_top_level_flow_done_and_status(self):
        # 模拟缓存中仍是旧的 flow_done=False，而磁盘 manifest + upload-format 已完成
        stale_cached = {"name": "paper-sync", "rows": [], "status": "validating",
                        "flow_done": False, "uploaded": False, "diagnostics": None}
        self.manifest["status"] = "pending_confirmation"
        self.manifest["stages"]["format_gate"] = {"status": "completed"}
        self._write("workflow/manifest.json", self.manifest)
        self._write("07_review/upload-format.json", {"passed": True, "final_sha256": self.final_sha})

        live = server._live_overview(stale_cached, "paper-sync", self.batch)
        self.assertTrue(live["flow_done"])
        self.assertEqual(live["status"], "pending_confirmation")

    def test_overview_key_tracks_stage_reports(self):
        base_key = server._overview_key(self.batch)
        for rel in ("07_review/extraction-report.json", "07_review/structure-gate.json",
                    "07_review/validation-report.json", "07_review/overall-review-report.json",
                    "07_upload/preflight.json", "02_audio/audio_map.json"):
            self._write(rel, {"updated": rel})
            new_key = server._overview_key(self.batch)
            self.assertNotEqual(new_key, base_key, f"{rel} 变化应触发概览缓存刷新")
            base_key = new_key


if __name__ == "__main__":
    unittest.main()
