# -*- coding: utf-8 -*-
"""听力切段适配器回归：全部 mock ffmpeg / WhisperX，不联网、不下载模型。

重点锁定 02_audio/segment-evidence.json 的契约（workflow.agent_tasks 的 audio 阶段
要求 verified=true + file/start/end/evidence 与真实切片一致）。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from audio import bootstrap, config as cfgmod, segment


def _batch(root: Path, numbers=(1, 2, 3)) -> Path:
    batch = root / "paper"
    (batch / "01_source").mkdir(parents=True)
    (batch / "03_types").mkdir(parents=True)
    (batch / "02_audio").mkdir(parents=True)
    (batch / "workflow").mkdir(parents=True)
    units = [{"unit_id": f"q-{n}", "unit_kind": "standalone", "numbers": [n]} for n in numbers]
    (batch / "01_source" / "structure.confirmed.json").write_text(
        json.dumps({"units": units}), encoding="utf-8")
    (batch / "03_types" / "types.json").write_text(
        json.dumps({"items": [{"unit_id": f"q-{n}", "type": "听力短对话理解"} for n in numbers]}),
        encoding="utf-8")
    audio = root / "full.mp3"
    audio.write_bytes(b"ID3fakeaudio")
    (batch / "workflow" / "manifest.json").write_text(
        json.dumps({"inputs": {"audio": {"path": str(audio)}}}), encoding="utf-8")
    return batch


class ConfigTest(unittest.TestCase):
    def test_defaults_and_validation(self):
        self.assertEqual(cfgmod.DEFAULTS["asr_model"], "small.en")
        self.assertFalse(cfgmod.DEFAULTS["enabled"])
        self.assertEqual(cfgmod.DEFAULTS["align_model"], "facebook/wav2vec2-base-960h")
        self.assertEqual(cfgmod.DEFAULTS["vad_method"], "pyannote")
        self.assertEqual(cfgmod.validate({"skeleton_silence": 3.0})["skeleton_silence"], 3.0)
        with self.assertRaises(ValueError):
            cfgmod.validate({"asr_model": "large-v3"})
        with self.assertRaises(ValueError):
            cfgmod.validate({"cut_silence": 9.0})

    def test_save_roundtrip_is_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.json"
            with mock.patch.dict(os.environ, {"WF_AUDIO_CONFIG": str(path)}):
                out = cfgmod.save({"enabled": True, "noise": "-40dB"})
                self.assertTrue(out["enabled"])
                self.assertEqual(cfgmod.load()["noise"], "-40dB")
                self.assertFalse(path.with_suffix(".tmp").exists())


class SilenceTest(unittest.TestCase):
    def test_skeleton_uses_only_long_silences(self):
        silences = [(10.0, 14.0), (20.0, 20.2), (30.0, 34.0), (50.0, 51.0)]
        self.assertEqual(segment.skeleton(silences, 3.0, 60.0), [0.0, 12.0, 32.0, 60.0])

    def test_detect_silences_parses_ffmpeg_output(self):
        proc = mock.Mock(returncode=0, stdout="", stderr=(
            "[silencedetect @ 0x1] silence_start: 10.5\n"
            "[silencedetect @ 0x1] silence_end: 14.0 | silence_duration: 3.5\n"
            "[silencedetect @ 0x1] silence_start: 30.0\n"
            "[silencedetect @ 0x1] silence_end: 33.0 | silence_duration: 3.0\n"))
        with mock.patch.object(segment.subprocess, "run", return_value=proc):
            self.assertEqual(segment.detect_silences(Path("a.mp3"), "-35dB", 0.35),
                             [(10.5, 14.0), (30.0, 33.0)])


class AnchorTest(unittest.TestCase):
    def test_word_anchors_find_question_numbers(self):
        words = [
            {"word": "Question", "start": 1.0, "end": 1.2},
            {"word": "one", "start": 1.3, "end": 1.5},
            {"word": "Question", "start": 20.0, "end": 20.2},
            {"word": "two", "start": 20.3, "end": 20.5},
            {"word": "第", "start": 40.0, "end": 40.1},
            {"word": "3", "start": 40.1, "end": 40.2},
            {"word": "题", "start": 40.2, "end": 40.3},
        ]
        anchors = segment.word_anchors(words)
        self.assertEqual(anchors[1], [1.0])
        self.assertEqual(anchors[2], [20.0])
        self.assertEqual(anchors[3], [40.0])

    def test_plan_snaps_anchors_to_prior_skeleton_cut(self):
        units = [{"unit_id": "q-1", "numbers": [1]}, {"unit_id": "q-2", "numbers": [2]},
                 {"unit_id": "q-3", "numbers": [3]}]
        cuts = [0.0, 12.0, 32.0, 50.0, 60.0]
        rows = segment.plan(units, {1: [12.4], 2: [32.8], 3: [52.0]}, cuts, 60.0)
        self.assertEqual([r["start"] for r in rows], [12.0, 32.0, 50.0])
        self.assertEqual([r["end"] for r in rows], [32.0, 50.0, 60.0])
        self.assertEqual([r["confidence"] for r in rows], ["anchored"] * 3)

    def test_plan_interpolates_without_anchors(self):
        units = [{"unit_id": "q-1", "numbers": [1]}, {"unit_id": "q-2", "numbers": [2]}]
        rows = segment.plan(units, {}, [0.0, 30.0, 60.0], 60.0)
        self.assertEqual([r["confidence"] for r in rows], ["interpolated", "interpolated"])
        self.assertEqual(rows[0]["end"], rows[1]["start"])
        self.assertEqual(rows[-1]["end"], 60.0)


class EvidenceContractTest(unittest.TestCase):
    def test_run_writes_evidence_matching_audio_stage_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = _batch(root)
            words = [{"word": "Question", "start": 12.4, "end": 12.6},
                     {"word": "one", "start": 12.7, "end": 12.9},
                     {"word": "Question", "start": 32.8, "end": 33.0},
                     {"word": "two", "start": 33.1, "end": 33.3},
                     {"word": "Question", "start": 52.0, "end": 52.2},
                     {"word": "three", "start": 52.3, "end": 52.5}]

            def fake_transcribe(audio, cfg, out):
                out.write_text(json.dumps({"words": words, "text": "..."}), encoding="utf-8")
                return {"words": words}

            def fake_cut(audio, out_dir, item):
                name = "seg-" + item["unit_id"] + ".mp3"
                (out_dir / name).write_bytes(b"ID3cut")
                return ["ffmpeg -ss " + str(item["start"]), "silencedetect 骨架", "整卷偏移"]

            cfg = dict(cfgmod.DEFAULTS)
            with mock.patch.object(segment, "probe_duration", return_value=60.0), \
                 mock.patch.object(segment, "detect_silences", return_value=[(11.0, 13.0), (31.0, 33.0), (49.0, 51.0)]), \
                 mock.patch.object(segment, "transcribe", side_effect=fake_transcribe), \
                 mock.patch.object(segment, "cut", side_effect=fake_cut):
                result = segment.run(batch, cfg)

            self.assertTrue(result["ok"])
            doc = json.loads((batch / "02_audio" / segment.EVIDENCE_NAME).read_text(encoding="utf-8"))
            self.assertEqual(len(doc["items"]), 3)
            for item in doc["items"]:
                self.assertIs(item["verified"], True)
                self.assertTrue(item["evidence"])
                self.assertLess(item["start"], item["end"])
                self.assertTrue(item["file"].startswith("02_audio/"))
                self.assertTrue((batch / item["file"]).is_file())
            # 同一份数据写进 audio_map.json，供审核界面播放
            amap = json.loads((batch / "02_audio" / "audio_map.json").read_text(encoding="utf-8"))
            self.assertEqual([x["unit_id"] for x in amap["items"]], [x["unit_id"] for x in doc["items"]])
            self.assertTrue((batch / "02_audio" / "full-transcript.json").is_file())


class BootstrapTest(unittest.TestCase):
    def test_status_roundtrip_and_ready_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            dd = Path(tmp)
            self.assertEqual(bootstrap.read_status(dd)["phase"], "idle")
            bootstrap.write_status(dd, job="bootstrap", running=True, phase="pip", pct=20, detail="installing")
            state = bootstrap.read_status(dd)
            self.assertEqual(state["pct"], 20)
            self.assertEqual(state["phase"], "pip")
            self.assertFalse(dd.joinpath("status.json.tmp").exists())
            cfg = dict(cfgmod.DEFAULTS, data_dir=str(dd))
            self.assertFalse(bootstrap.ready(cfg))

    def test_data_root_matches_host_legacy_branch(self):
        # 宿主的 resolveDataRoot() 在插件目录内已有 workflows/ 时沿用插件目录；
        # 不对齐会导致 DSH 重启后已装好的 venv/模型「凭空消失」。
        from audio import config as c
        with mock.patch.dict(os.environ, {}, clear=False):
            for key in ("WF_DATA_DIR", "WF_AUDIO_DATA_DIR"):
                os.environ.pop(key, None)
            expected = (c.PLUGIN_ROOT / "audio").resolve() if (c.PLUGIN_ROOT / "workflows").is_dir() else None
            if expected is not None:
                self.assertEqual(c.data_dir({}), expected)

    def test_model_env_is_shared_between_bootstrap_and_segment(self):
        # 安装与切段必须用同一套缓存/镜像，否则切段时会重新下载或找不到权重。
        with tempfile.TemporaryDirectory() as tmp:
            cfg = dict(cfgmod.DEFAULTS, data_dir=str(tmp),
                       hf_endpoint="https://hf-mirror.com")
            env = cfgmod.model_env(cfg)
            root = str(Path(tmp).resolve() / "models")
            self.assertEqual(env["HF_ENDPOINT"], "https://hf-mirror.com")
            self.assertEqual(env["HF_HOME"], root)
            self.assertEqual(env["HUGGINGFACE_HUB_CACHE"], root + "/hub")
            self.assertEqual(env["TRANSFORMERS_CACHE"], root + "/transformers")

    def test_transcribe_passes_shared_env_to_worker(self):
        import audio.segment as seg
        with tempfile.TemporaryDirectory() as tmp:
            cfg = dict(cfgmod.DEFAULTS, data_dir=str(tmp), hf_endpoint="https://hf-mirror.com")
            out = Path(tmp) / "words.json"
            out.write_text('{"words": []}', encoding="utf-8")
            with mock.patch.object(seg.bootstrap, "venv_python", return_value=Path(tmp) / "v" / "bin" / "python"), \
                 mock.patch.object(seg.bootstrap, "worker_path", return_value=Path(tmp) / "worker.py"), \
                 mock.patch.object(Path, "is_file", return_value=True), \
                 mock.patch.object(seg, "_run", return_value=mock.Mock()) as run:
                seg.transcribe(Path(tmp) / "a.mp3", cfg, out)
            self.assertEqual(run.call_args.kwargs["env"]["HF_ENDPOINT"], "https://hf-mirror.com")
            self.assertEqual(run.call_args.kwargs["env"]["HF_HOME"],
                             cfgmod.model_env(cfg)["HF_HOME"])

    def test_hf_endpoint_resolution_order(self):
        with mock.patch.dict(os.environ, {"HF_ENDPOINT": "https://env.example"}, clear=False):
            self.assertEqual(cfgmod.resolve_hf_endpoint({"hf_endpoint": "https://cfg.example"}),
                             "https://cfg.example")
            self.assertEqual(cfgmod.resolve_hf_endpoint({}), "https://env.example")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HF_ENDPOINT", None)
            with mock.patch.object(cfgmod, "_hf_direct_reachable", return_value=True):
                self.assertEqual(cfgmod.resolve_hf_endpoint({}), "")
            with mock.patch.object(cfgmod, "_hf_direct_reachable", return_value=False):
                self.assertEqual(cfgmod.resolve_hf_endpoint({}), cfgmod.HF_MIRROR)

    def test_validate_rejects_bad_endpoint(self):
        with self.assertRaises(ValueError):
            cfgmod.validate({"hf_endpoint": "not-a-url"})

    def test_preflight_rejects_old_python_and_missing_ffmpeg(self):
        with mock.patch.object(bootstrap.sys, "version_info", (3, 9, 0)):
            with self.assertRaisesRegex(RuntimeError, "Python 3.10"):
                bootstrap.preflight()
        with mock.patch.object(bootstrap, "tool_path", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "ffmpeg"):
                bootstrap.preflight()
        # 版本与工具都满足时不应抛错
        with mock.patch.object(bootstrap, "tool_path", return_value="/x/ffmpeg"):
            bootstrap.preflight()

    def test_platform_key_is_sane(self):
        key = bootstrap.platform_key()
        self.assertRegex(key, r"^(darwin|win32|linux)-(arm64|x64)$")

    def test_vendor_binaries_take_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            vdir = Path(tmp)
            plat = bootstrap.platform_key()
            pybin = vdir / "python" / plat / ("python.exe" if os.name == "nt" else "bin/python3")
            pybin.parent.mkdir(parents=True); pybin.write_text("")
            ff = vdir / "ffmpeg" / plat / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            ff.parent.mkdir(parents=True); ff.write_text("")
            with mock.patch.object(bootstrap, "VENDOR_DIR", vdir):
                self.assertEqual(bootstrap.vendor_python(), pybin.resolve())
                self.assertEqual(bootstrap.base_python(), str(pybin.resolve()))
                self.assertEqual(bootstrap.tool_path("ffmpeg"), str(ff.resolve()))
                env = bootstrap.runtime_env({"data_dir": tmp, "hf_endpoint": "https://hf-mirror.com"})
                self.assertTrue(env["PATH"].startswith(str(ff.parent)))

    def test_missing_vendor_and_system_ffmpeg_reports_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(bootstrap, "VENDOR_DIR", Path(tmp) / "none"), \
                 mock.patch.object(bootstrap.shutil, "which", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "fetch_vendor"):
                    bootstrap.preflight()

    def test_no_bootstrap_downloads_until_explicit_call(self):
        # 默认配置 enabled=false；这里锁定「不调用 bootstrap 就不会有 READY 标记」。
        with tempfile.TemporaryDirectory() as tmp:
            cfg = dict(cfgmod.DEFAULTS, data_dir=str(tmp))
            self.assertFalse(bootstrap.ready(cfg))
            self.assertFalse((Path(tmp) / "READY.json").exists())


if __name__ == "__main__":
    unittest.main()
