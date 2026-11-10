"""听力阶段确定性快路与阶段自动恢复的回归。

背景：切段器已经确定性地产出 `02_audio/segment-evidence.json`，再让模型
"重新生成"一遍既慢，又会重写产物把已完成的审核指纹改掉（审核随即作废）。
只要产物能通过同一套契约校验，就应该直接判定该阶段完成。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import agent, agent_protocol, contracts, local_agent, manifest as mf


def _batch(root: Path) -> Path:
    batch = root / "batch"
    (batch / "01_source").mkdir(parents=True)
    (batch / "02_audio").mkdir(parents=True)
    (batch / "workflow").mkdir(parents=True)
    stem = "Ⅰ.关键词语选择\n(　　)1.A.page B.paper C.pencil\n"
    (batch / "01_source" / "正文.txt").write_text(stem, encoding="utf-8")
    (batch / "01_source" / "structure.confirmed.json").write_text(json.dumps({
        "units": [{"unit_id": "q-1", "unit_kind": "standalone", "numbers": [1],
                   "type": "听力关键词语选择", "stem": "(　　)1.A.page B.paper C.pencil"}]},
        ensure_ascii=False), encoding="utf-8")
    (batch / "02_audio" / "seg-q-1.mp3").write_bytes(b"ID3cut")
    # A cut is only current when its type/unit binding matches and the file hash
    # is recorded, exactly as the splitter now produces it.
    (batch / "03_types").mkdir(parents=True, exist_ok=True)
    (batch / "03_types" / "types.json").write_text(json.dumps({"items": [{
        "unit_id": "q-1", "type": "听力关键词语选择", "subtypes": [], "words": 3,
        "contentComplete": True, "evidence": ["正文 L2"]}]}, ensure_ascii=False), encoding="utf-8")
    import hashlib
    from audio.binding import listening_context
    binding, _ = listening_context(batch)
    cut_sha = hashlib.sha256((batch / "02_audio" / "seg-q-1.mp3").read_bytes()).hexdigest()
    (batch / "02_audio" / "segment-evidence.json").write_text(json.dumps({
        "generator": "audio.segment/multi-v1", "unit_binding": binding,
        "items": [{"unit_id": "q-1", "file": "02_audio/seg-q-1.mp3",
                   "start": 0.0, "end": 12.5, "duration": 12.5,
                   "file_sha256": cut_sha,
                   "confidence": "anchored", "quality": "ok", "verified": True,
                   "evidence": ["ffmpeg -ss 0.0"]}]}, ensure_ascii=False), encoding="utf-8")
    manifest = mf.new_manifest("batch", batch, {}, {})
    manifest["batch_dir"] = str(batch)
    mf.save(batch, manifest)
    return batch


class AudioFastPathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = _batch(Path(self.tmp.name))

    def test_evidence_passing_contract_is_ready(self):
        self.assertTrue(agent._audio_evidence_ready(self.batch))

    def test_missing_or_invalid_evidence_is_not_ready(self):
        path = self.batch / "02_audio" / "segment-evidence.json"
        path.unlink()
        self.assertFalse(agent._audio_evidence_ready(self.batch))
        # 引用不存在的切片：契约不通过，必须回落到模型路径。
        path.write_text(json.dumps({"items": [{
            "unit_id": "q-1", "file": "02_audio/nope.mp3", "start": 0.0,
            "end": 5.0, "evidence": ["x"]}]}, ensure_ascii=False), encoding="utf-8")
        self.assertFalse(agent._audio_evidence_ready(self.batch))

    def test_audio_stage_skips_model_when_evidence_ready(self):
        evidence = json.loads((self.batch / '02_audio/segment-evidence.json').read_text())
        mapping = {'unit_binding': evidence['unit_binding'], 'items': [
            {k: row[k] for k in ('unit_id', 'file', 'start', 'end')} for row in evidence['items']]}
        (self.batch / '02_audio/audio_map.json').write_text(json.dumps(mapping), encoding='utf-8')
        # This fixture isolates the audio contract; upstream delivery validation
        # is covered separately and must not be silently bypassed in production.
        with patch.object(agent, "configured", return_value=True), \
             patch.object(local_agent, "main") as invoke, \
             patch.object(agent_protocol, '_preconditions', return_value=[]):
            code = agent.main(["run", "--stage", "audio", "--batch-dir", str(self.batch)])
        self.assertEqual(code, 0)
        invoke.assert_not_called()
        self.assertEqual(mf.load(self.batch)['stages']['audio']['status'], mf.COMPLETED)


class SkippedStageRevivalTest(unittest.TestCase):
    def test_skipped_stage_without_reason_is_revived(self):
        # 用户删掉 SKIP_LISTENING 后，audio 阶段不能永远停在 skipped。
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp) / "batch"
            batch.mkdir()
            (batch / "workflow").mkdir()
            (batch / "01_source").mkdir()
            (batch / "01_source" / "structure.confirmed.json").write_text(json.dumps({
                "units": [{"unit_id": "q-1", "unit_kind": "standalone", "numbers": [1],
                           "type": "听力短对话理解", "stem": "1. Hi."}]},
                ensure_ascii=False), encoding="utf-8")
            manifest = mf.new_manifest("batch", batch, {}, {})
            manifest["batch_dir"] = str(batch)
            mf.touch_stage(manifest, "audio", mf.SKIPPED,
                           notes="用户选择跳过听力（02_audio/SKIP_LISTENING 标记）")
            mf.save(batch, manifest)
            stage = contracts.STAGE_BY_ID["audio"]
            self.assertEqual(mf.skip_reason(manifest, stage), "")
            loaded = mf.load(batch)
            self.assertEqual(loaded["stages"]["audio"]["status"], mf.SKIPPED)

    def test_runner_actually_revives_skip_before_terminal_shortcut(self):
        from types import SimpleNamespace
        from workflow import runner
        with tempfile.TemporaryDirectory() as directory:
            batch = _batch(Path(directory))
            manifest = mf.load(batch)
            for record in manifest['stages'].values():
                record['status'] = mf.COMPLETED
            mf.touch_stage(manifest, 'audio', mf.SKIPPED, notes='old skip')
            mf.save(batch, manifest)
            args = SimpleNamespace(stage='audio', until=None, force=False, dry_run=False, config=None)
            with patch.object(mf, 'dependency_satisfied', return_value=True), \
                 patch.object(runner, '_execute_command', return_value=0) as execute:
                self.assertEqual(runner._cmd_run_locked(batch, args), 0)
            execute.assert_called_once()
            self.assertEqual(mf.load(batch)['stages']['audio']['status'], mf.PENDING)

    def test_skip_revival_dry_run_does_not_mutate_manifest(self):
        from types import SimpleNamespace
        from workflow import runner
        with tempfile.TemporaryDirectory() as directory:
            batch = _batch(Path(directory)); manifest = mf.load(batch)
            for record in manifest['stages'].values(): record['status'] = mf.COMPLETED
            mf.touch_stage(manifest, 'audio', mf.SKIPPED, notes='old skip'); mf.save(batch, manifest)
            target = batch / 'workflow/manifest.json'; before = target.read_bytes()
            args = SimpleNamespace(stage='audio', until=None, force=False, dry_run=True, config=None)
            self.assertEqual(runner._cmd_run_locked(batch, args), 0)
            self.assertEqual(before, target.read_bytes())


if __name__ == "__main__":
    unittest.main()
