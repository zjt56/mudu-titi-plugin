# -*- coding: utf-8 -*-
"""上传后自动切段：结构闸门通过即调用真实切段器，失败不打断整批推进。"""
import json
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import auto, manifest as mf


def _manifest(batch: Path, *, audio=True):
    manifest = mf.new_manifest("batch", batch, {}, {})
    manifest["batch_dir"] = str(batch)
    if audio:
        manifest["inputs"]["audio"] = {"path": str(batch / "audio.mp3"), "exists": True}
    manifest["stages"]["structure_gate"]["status"] = mf.COMPLETED
    return manifest


class AutoAudioPrepareTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.batch = Path(self.temporary.name) / "2025试点"
        (self.batch / "02_audio").mkdir(parents=True)
        self.manifest = _manifest(self.batch)
        (self.batch / "audio.mp3").write_bytes(b"ID3")

    def test_ready_runs_splitter_once_before_agent_stage(self):
        with patch("audio.config.load", return_value={"enabled": True}), \
             patch("audio.config.data_dir", return_value=str(self.batch / "audio-data")), \
             patch("audio.bootstrap.ready", return_value=True), \
             patch("audio.bootstrap.venv_python", return_value=Path(sys.executable)), \
             patch("audio.bootstrap.write_status") as status, \
             patch("audio.segment.run", return_value={"ok": True, "needs_review_units": []}) as run:
            result = auto._auto_prepare_audio(self.batch, self.manifest)
        self.assertEqual(result["status"], "ready")
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], self.batch)
        self.assertTrue(any(call.kwargs.get("batch") == "2025试点" for call in status.call_args_list))

    def test_multiple_audio_runs_separate_analysis_but_not_single_audio_split(self):
        self.manifest['inputs']['audio_001'] = {'path': str(self.batch / 'second.mp3'), 'exists': True}
        with patch('audio.config.load', return_value={'enabled': True}), \
             patch('audio.config.data_dir', return_value=str(self.batch / 'audio-data')), \
             patch('audio.bootstrap.venv_python', return_value=Path(sys.executable)), \
             patch('audio.bootstrap.read_status', return_value={}), \
             patch('audio.bootstrap.write_status'), \
             patch('audio.segment.inspect_multiple_audio', return_value={'complete': False}) as analyze, \
             patch('audio.segment.slice_multiple_audio') as split, \
             patch('audio.segment.run') as run:
            result = auto._auto_prepare_audio(self.batch, self.manifest)
        self.assertEqual(result['status'], 'multiple_audio')
        analyze.assert_called_once()
        split.assert_not_called()
        run.assert_not_called()
        self.assertFalse((self.batch / '02_audio/audio_map.json').exists())

    def test_complete_multi_audio_analysis_calls_multi_source_cutter(self):
        self.manifest['inputs']['audio_001'] = {'path': str(self.batch / 'second.mp3'), 'exists': True}
        analysis = {'complete': True}
        with patch('audio.config.load', return_value={'enabled': True}), \
             patch('audio.config.data_dir', return_value=str(self.batch / 'audio-data')), \
             patch('audio.bootstrap.venv_python', return_value=Path(sys.executable)), \
             patch('audio.bootstrap.read_status', return_value={}), \
             patch('audio.bootstrap.write_status'), \
             patch('audio.segment.inspect_multiple_audio', return_value=analysis), \
             patch('audio.segment.slice_multiple_audio', return_value={'ok': True}) as split:
            result = auto._auto_prepare_audio(self.batch, self.manifest)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(split.call_args.args[:2], (self.batch, analysis))

    def test_multiple_audio_failure_never_becomes_skippable_error(self):
        self.manifest['inputs']['audio_001'] = {'path': str(self.batch / 'second.mp3'), 'exists': True}
        with patch('audio.config.load', return_value={'enabled': True}), \
             patch('audio.config.data_dir', return_value=str(self.batch / 'audio-data')), \
             patch('audio.bootstrap.venv_python', return_value=Path(sys.executable)), \
             patch('audio.bootstrap.read_status', return_value={}), \
             patch('audio.bootstrap.write_status'), \
             patch('audio.segment.inspect_multiple_audio', side_effect=RuntimeError('ASR 失败')):
            result = auto._auto_prepare_audio(self.batch, self.manifest)
        self.assertEqual(result['status'], 'multiple_audio')
        self.assertIn('ASR 失败', result['message'])

    def test_existing_evidence_is_not_overwritten(self):
        (self.batch / "02_audio" / "segment-evidence.json").write_text("{}", encoding="utf-8")
        with patch("audio.segment.run", side_effect=AssertionError("must not rerun")) as run:
            result = auto._auto_prepare_audio(self.batch, self.manifest)
        self.assertEqual(result["status"], "evidence")
        run.assert_not_called()

    def test_stale_running_heartbeat_restarts_splitter(self):
        with patch("audio.config.load", return_value={"enabled": True}), \
             patch("audio.config.data_dir", return_value=str(self.batch / "audio-data")), \
             patch("audio.bootstrap.venv_python", return_value=Path(sys.executable)), \
             patch("audio.bootstrap.read_status", return_value={"running": True, "updated": 0}), \
             patch("audio.bootstrap.write_status"), \
             patch("audio.segment.run", return_value={"ok": True, "needs_review_units": []}) as run:
            result = auto._auto_prepare_audio(self.batch, self.manifest)
        self.assertEqual(result["status"], "ready")
        run.assert_called_once()

    def test_disabled_switch_reports_actionable_note_without_running(self):
        with patch("audio.config.load", return_value={"enabled": False}), \
             patch("audio.segment.run", side_effect=AssertionError("must not run")):
            result = auto._auto_prepare_audio(self.batch, self.manifest)
        self.assertEqual(result["status"], "disabled")
        self.assertIn("听力切段未启用", result["message"])

    def test_unverified_boundaries_return_pending_units(self):
        with patch("audio.config.load", return_value={"enabled": True}), \
             patch("audio.config.data_dir", return_value=str(self.batch / "audio-data")), \
             patch("audio.bootstrap.ready", return_value=True), \
             patch("audio.bootstrap.venv_python", return_value=Path(sys.executable)), \
             patch("audio.bootstrap.write_status"), \
             patch("audio.segment.run", return_value={"ok": False, "needs_review_units": ["q-2", "q-3"]}):
            result = auto._auto_prepare_audio(self.batch, self.manifest)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["pending"], ["q-2", "q-3"])

    def test_missing_audio_skips_without_calling_splitter(self):
        manifest = _manifest(self.batch, audio=False)
        with patch("audio.segment.run", side_effect=AssertionError("must not run")):
            result = auto._auto_prepare_audio(self.batch, manifest)
        self.assertEqual(result["status"], "no_audio")


if __name__ == "__main__":
    unittest.main()
