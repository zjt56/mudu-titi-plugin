# -*- coding: utf-8 -*-
"""切段存活判断：状态文件只是缓存，OS 锁才是“真的还在跑”的证据。

回归 2026-11-09 卡点：Windows 上旧的探活回退把任何 running=true 都当成活的，
被强杀的切段进程让整批干等 900s 心跳超时；组装又硬依赖 audio_map，于是反复
“Stage preconditions unmet”。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from audio import segment
from workflow import auto
from workbench import server


def _make_batch(root: Path, name: str = "批次A") -> Path:
    batch = root / name
    (batch / "02_audio").mkdir(parents=True)
    return batch


def _hold_lock(lock_path: Path):
    """用与 _batch_segment_lock 相同的方式在第二个句柄上持锁。"""
    handle = lock_path.open("r+b")
    if os.name == "nt":
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


def _release_lock(handle) -> None:
    if os.name == "nt":
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    handle.close()


class SegmentLockProbeTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.batch = _make_batch(self.root)
        self.lock = self.batch / "02_audio/.segment.lock"
        self.lock.write_bytes(b"0")

    def test_missing_lock_file_is_not_held(self):
        self.lock.unlink()
        self.assertFalse(segment.lock_is_held(self.batch))
        self.assertFalse(auto._segment_lock_held(self.batch))

    def test_free_lock_file_is_not_held_on_any_platform(self):
        # 残留的锁文件（进程已死）不得被当成“仍在跑”。
        self.assertFalse(segment.lock_is_held(self.batch))
        self.assertFalse(auto._segment_lock_held(self.batch))

    def test_held_lock_is_detected_on_any_platform(self):
        handle = _hold_lock(self.lock)
        try:
            self.assertTrue(segment.lock_is_held(self.batch))
            self.assertTrue(auto._segment_lock_held(self.batch))
        finally:
            _release_lock(handle)
        self.assertFalse(segment.lock_is_held(self.batch))


class StaleAudioStatusTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workflows = self.root / "workflows"
        self.batch = _make_batch(self.workflows)
        self.data = self.root / "audio-data"
        self.data.mkdir()
        patcher = mock.patch.object(server, "WORKFLOWS", self.workflows)
        patcher.start()
        self.addCleanup(patcher.stop)
        job_patch = mock.patch.dict(server._audio_job, {"running": False, "kind": "", "batch": ""})
        job_patch.start()
        self.addCleanup(job_patch.stop)

    def _boot(self, state: dict):
        boot = mock.Mock()
        boot.read_status.return_value = dict(state)
        boot.write_status.return_value = {}
        boot.ready.return_value = True
        return boot

    def test_status_without_live_lock_is_not_alive(self):
        self.assertFalse(server._audio_has_live_worker("批次A"))

    def test_lock_owned_by_a_real_process_counts_as_alive(self):
        lock = self.batch / "02_audio/.segment.lock"
        lock.write_bytes(b"0")
        handle = _hold_lock(lock)
        try:
            self.assertTrue(server._audio_has_live_worker("批次A"))
        finally:
            _release_lock(handle)

    def test_unknown_batch_is_not_alive(self):
        self.assertFalse(server._audio_has_live_worker("不存在的批次"))
        self.assertFalse(server._audio_has_live_worker(None))

    def test_reap_resets_residual_running_status(self):
        boot = self._boot({"job": "segment", "running": True, "phase": "transcribe",
                           "updated": 1.0})
        server._audio_reap_stale("segment", "批次A", self.data, boot)
        boot.write_status.assert_called_once()
        kwargs = boot.write_status.call_args.kwargs
        self.assertFalse(kwargs["running"])
        self.assertEqual(kwargs["phase"], "error")
        self.assertIn("残留", kwargs["error"])

    def test_reap_leaves_a_live_job_alone(self):
        lock = self.batch / "02_audio/.segment.lock"
        lock.write_bytes(b"0")
        handle = _hold_lock(lock)
        try:
            boot = self._boot({"job": "segment", "running": True, "updated": 1.0})
            server._audio_reap_stale("segment", "批次A", self.data, boot)
        finally:
            _release_lock(handle)
        boot.write_status.assert_not_called()

    def test_audio_status_never_reports_a_dead_worker_as_running(self):
        boot = self._boot({"job": "segment", "running": True, "ok": None, "phase": "transcribe",
                           "pct": 35, "detail": "转写区域 13/28", "updated": 1.0})
        cfgmod = mock.Mock()
        cfgmod.load.return_value = {"enabled": True}
        cfgmod.data_dir.return_value = str(self.data)
        with mock.patch.object(server, "_audio_modules", return_value=(cfgmod, boot, mock.Mock())):
            state = server.audio_status("批次A")
        self.assertFalse(state["running"])
        self.assertTrue(state["stale"])
        self.assertIn("中断", state["detail"])


class AssembleDoesNotWaitForAudioTest(unittest.TestCase):
    """contracts.assemble 刻意不依赖 audio：组装的采集不得把 audio_map 当硬前置。"""

    def test_collect_never_requires_audio_map_for_assemble(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        batch = Path(temporary.name)
        (batch / "01_source").mkdir(parents=True)
        (batch / "01_source/正文.txt").write_text("1. She ______ a book. A. reads B. read",
                                                  encoding="utf-8")
        from workflow import agent_tasks as tasks
        from workflow import manifest as mf
        manifest = mf.new_manifest("batch", batch, {}, {})
        manifest["batch_dir"] = str(batch)
        manifest["inputs"]["audio"] = {"path": str(batch / "attachments/audio.mp3"), "exists": True}
        mf.save(batch, manifest)

        _, hashes, missing, _ = tasks._collect(batch, "assemble")
        self.assertFalse([row for row in missing if "02_audio/audio_map.json" in row], missing)
        self.assertNotIn(tasks.ARTIFACTS["audio"], hashes)

        _, _, review_missing, _ = tasks._collect(batch, "review")
        self.assertTrue([row for row in review_missing if "02_audio/audio_map.json" in row],
                        review_missing)

    def test_collect_skips_audio_for_assemble_even_when_map_exists(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        batch = Path(temporary.name)
        (batch / "01_source").mkdir(parents=True)
        (batch / "02_audio").mkdir()
        (batch / "01_source/正文.txt").write_text("1. She ______ a book.", encoding="utf-8")
        (batch / "02_audio/audio_map.json").write_text(json.dumps({"items": []}), encoding="utf-8")
        from workflow import agent_tasks as tasks
        from workflow import manifest as mf
        manifest = mf.new_manifest("batch", batch, {}, {})
        manifest["batch_dir"] = str(batch)
        manifest["inputs"]["audio"] = {"path": str(batch / "attachments/audio.mp3"), "exists": True}
        mf.save(batch, manifest)

        _, hashes, _, _ = tasks._collect(batch, "assemble")
        self.assertNotIn("02_audio/audio_map.json", hashes)
        _, review_hashes, _, _ = tasks._collect(batch, "review")
        self.assertIn("02_audio/audio_map.json", review_hashes)


if __name__ == "__main__":
    unittest.main()
