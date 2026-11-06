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
        # 默认多语种 small：资源占用低；large-v3-turbo 可显式选择。
        self.assertEqual(cfgmod.DEFAULTS["asr_model"], "small")
        self.assertEqual(cfgmod.DEFAULTS["anchor_language"], "zh")
        self.assertFalse(cfgmod.DEFAULTS["enabled"])
        self.assertEqual(cfgmod.DEFAULTS["align_model"], "facebook/wav2vec2-base-960h")
        self.assertEqual(cfgmod.DEFAULTS["vad_method"], "pyannote")
        self.assertEqual(cfgmod.validate({"skeleton_silence": 3.0})["skeleton_silence"], 3.0)
        with self.assertRaises(ValueError):
            cfgmod.validate({"asr_model": "unknown-model"})
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

    def test_word_anchors_handles_plural_and_ranges(self):
        # 最常见的 “answer questions 6 and 7” 过去会把 questions 的 s 当题号而丢掉；
        # 现在必须正确展开成 6、7，并共享该句起点时间。
        def toks(pairs):
            return [{"word": w, "start": t, "end": t + 0.2} for w, t in pairs]
        words = toks([("Listen", 2.0), ("to", 2.2), ("answer", 2.4),
                      ("questions", 3.0), ("6", 3.2), ("and", 3.4), ("7", 3.6),
                      ("Now", 20.0),
                      ("questions", 20.4), ("16", 20.6), ("to", 20.8), ("20", 21.0)])
        anchors = segment.word_anchors(words)
        self.assertEqual(anchors.get(6), [3.0])
        self.assertEqual(anchors.get(7), [3.0])
        for n in range(16, 21):
            self.assertEqual(anchors.get(n), [20.4])

    def test_anchor_regex_ignores_section_and_chapter_words(self):
        # Section/Part/Text/Conversation 是章节词，绝不是题号，不能产生假锚点。
        for text in ("Section B", "Part two", "Text 8.", "Conversation 1.", "Passage 2"):
            self.assertEqual(segment.ANCHOR_RE.findall(text), [], text)
        # 而真正的题号线索仍能命中。
        self.assertTrue(segment.ANCHOR_RE.search("question 3"))
        self.assertTrue(segment.ANCHOR_RE.search("questions 6-7"))
        self.assertTrue(segment.ANCHOR_RE.search("第 6、7 题"))

    def test_bare_question_numbers_without_cue_words(self):
        # 真实听力音频常直接念题号（"3." / "Six,"），没有 question/number 线索词；
        # 只认线索词会只剩极少数锚点，其余靠等分插值，切点全错。
        def toks(pairs):
            return [{"word": w, "start": t, "end": t + 0.3} for w, t in pairs]
        words = toks([("pencil", 8.0),
                      ("3.", 20.0), ("You", 20.5), ("may", 20.8),
                      ("Six", 40.0), ("excuse", 40.5), ("me", 40.8),
                      ("10.", 60.0), ("Do", 60.5), ("you", 60.8)])
        anchors = segment.word_anchors(words)
        self.assertEqual(anchors.get(3), [20.0])
        self.assertEqual(anchors.get(6), [40.0])
        self.assertEqual(anchors.get(10), [60.0])

    def test_mid_sentence_number_is_not_a_question_anchor(self):
        # "take a number two bus" 里的 number two 不是题号；它还会破坏题号与
        # 时间的同向关系，必须被剔除，而不是把该题起点拖到后面。
        def toks(pairs):
            return [{"word": w, "start": t, "end": t + 0.3} for w, t in pairs]
        words = toks([("Three", 10.0), ("You", 10.4),
                      ("Four", 30.0), ("My", 30.4),
                      ("take", 50.0), ("a", 50.3), ("number", 50.6),
                      ("two", 50.9), ("bus", 51.2)])
        anchors = segment.word_anchors(words)
        self.assertEqual(anchors, {3: [10.0], 4: [30.0]})
        self.assertNotIn(2, anchors)

    def test_chinese_announcements_with_traditional_and_cn_numerals(self):
        # 实测中文播报是繁体 + 中文数字，且分词会在字之间插空格。
        def toks(pairs):
            return [{"word": w, "start": t, "end": t + 0.3} for w, t in pairs]
        words = toks([("聽", 1.0), ("下面", 1.2), ("一段", 1.4), ("對話", 1.6),
                      ("回答", 1.8), ("第", 2.0), ("16", 2.2), ("至", 2.4),
                      ("17", 2.6), ("小", 2.8), ("題", 3.0),
                      ("第", 30.0), ("十八", 30.2), ("至", 30.4),
                      ("二十", 30.6), ("小", 30.8), ("題", 31.0)])
        anchors = segment.word_anchors(words)
        self.assertEqual(anchors.get(16), [2.0])
        self.assertEqual(anchors.get(17), [2.0])
        for n in (18, 19, 20):
            self.assertEqual(anchors.get(n), [30.0])

    def test_section_anchors_map_big_sections_to_first_number(self):
        # 短文理解/信息转换不念题号，只能靠大题标题播报定位起点。
        units = [{"unit_id": "q-1", "numbers": [1], "type": "听力关键词语选择"},
                 {"unit_id": "q-2", "numbers": [2], "type": "听力关键词语选择"},
                 {"unit_id": "group-16-17", "numbers": [16, 17], "type": "听力长对话理解"},
                 {"unit_id": "group-21-25", "numbers": [21, 22, 23, 24, 25], "type": "听力短文理解"},
                 {"unit_id": "group-26-30", "numbers": [26, 27, 28, 29, 30], "type": "听力信息转换"}]
        words = [{"word": "1.", "start": 10.0, "end": 10.2},
                 {"word": "關鍵詞語選擇", "start": 11.0, "end": 11.5},
                 {"word": "長對話理解", "start": 100.0, "end": 100.5},
                 {"word": "短文理解", "start": 200.0, "end": 200.5},
                 {"word": "信息轉換", "start": 300.0, "end": 300.5}]
        found = segment.section_anchors(units, words)
        self.assertEqual(found.get(1), 11.0)
        self.assertEqual(found.get(16), 100.0)
        self.assertEqual(found.get(21), 200.0)
        self.assertEqual(found.get(26), 300.0)

    def test_plan_distributes_unanchored_units_between_anchors(self):
        # q-3 有锚点、q-5 有锚点、q-4 没有：q-4 必须落在两者之间，
        # 而不是被挤到 q-5 前面只剩几秒。
        units = [{"unit_id": f"q-{i}", "numbers": [i]} for i in range(1, 6)]
        cuts = [0.0, 50.0, 100.0, 150.0, 200.0]
        anchors = {3: [103.0], 5: [148.0]}
        rows = segment.plan(units, anchors, cuts, 200.0)
        by_id = {r["unit_id"]: r for r in rows}
        self.assertLess(by_id["q-3"]["start"], by_id["q-4"]["start"])
        self.assertLess(by_id["q-4"]["start"], by_id["q-5"]["start"])
        self.assertGreater(by_id["q-4"]["duration"], segment.MIN_SEGMENT_SECONDS * 3)

    def test_plan_avoids_micro_segments_when_few_cuts(self):
        # 19 个单元只有 4 个骨架切点：过去会堆出十几个 0.5s 微段；现在应
        # 从 0 开始、用真实切点 + 剩余区间等分，全部 >= MIN_SEGMENT、严格递增。
        units = [{"unit_id": f"q-{i}", "numbers": [i]} for i in range(1, 20)]
        cuts = [0.0, 60.0, 120.0, 180.0, 240.0, 300.0]
        rows = segment.plan(units, {}, cuts, 300.0)
        self.assertEqual(rows[0]["start"], 0.0)
        self.assertEqual(rows[-1]["end"], 300.0)
        self.assertTrue(all(r["duration"] >= segment.MIN_SEGMENT_SECONDS for r in rows),
                        [(r["unit_id"], r["duration"]) for r in rows if r["duration"] < segment.MIN_SEGMENT_SECONDS])
        self.assertTrue(all(rows[i]["start"] < rows[i + 1]["start"] for i in range(len(rows) - 1)))
        self.assertFalse([r for r in rows if r["quality"] == "micro"])

    def test_plan_flags_micro_when_audio_too_short_for_units(self):
        # 音频总长装不下这么多单元（每个至少 MIN_SEGMENT 秒）时，才不可避免出现微段，
        # 这些段必须标 quality=micro，run() 不把它们当 verified。
        units = [{"unit_id": f"q-{i}", "numbers": [i]} for i in range(1, 6)]
        cuts = [0.0, 4.0]  # 总长仅 4s，5 个单元不可能都 >= 2s
        rows = segment.plan(units, {}, cuts, 4.0)
        micro = [r for r in rows if r["quality"] == "micro"]
        self.assertTrue(micro, "音频过短时应检测出微段")
        self.assertTrue(all(r["duration"] < segment.MIN_SEGMENT_SECONDS for r in micro))

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

            def fake_transcribe(audio, cfg, out, language="en", align=True, regions=None, progress=None):
                out.write_text(json.dumps({"words": words, "text": "..."}), encoding="utf-8")
                return {"words": words}

            def fake_cut(audio, out_dir, item):
                name = "seg-" + item["unit_id"] + ".mp3"
                (out_dir / name).write_bytes(b"ID3cut")
                return ["ffmpeg -ss " + str(item["start"]), "silencedetect 骨架", "整卷偏移"]

            cfg = dict(cfgmod.DEFAULTS)
            with mock.patch.object(segment, "probe_duration", return_value=60.0), \
                 mock.patch.object(segment, "detect_silences", return_value=[(10.5, 13.5), (30.5, 33.5), (48.5, 51.5)]), \
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
            audio = Path(tmp) / "a.mp3"
            audio.write_bytes(b"ID3audio")
            out = Path(tmp) / "words.json"
            def worker(command, **kwargs):
                target = Path(command[command.index("--out") + 1])
                target.write_text(json.dumps({"schema_version": 2, "segments": [
                    {"start": 0, "end": 1, "text": "hello"}], "words": [],
                    "timestamp_precision": "segment"}))
            with mock.patch.object(seg.bootstrap, "venv_python", return_value=audio), \
                 mock.patch.object(seg, "_run", side_effect=worker) as run:
                seg.transcribe(audio, cfg, out)
            self.assertEqual(run.call_args.kwargs["env"]["HF_ENDPOINT"], "https://hf-mirror.com")
            self.assertEqual(run.call_args.kwargs["env"]["HF_HOME"], cfgmod.model_env(cfg)["HF_HOME"])

    def test_transcribe_reuses_cached_transcript_to_resume(self):
        import audio.segment as seg
        with tempfile.TemporaryDirectory() as tmp:
            cfg = dict(cfgmod.DEFAULTS, data_dir=str(tmp))
            audio = Path(tmp) / "a.mp3"
            audio.write_bytes(b"ID3audio")
            out = Path(tmp) / "full-transcript.json"
            out.write_text(json.dumps({"schema_version": 2, "timestamp_precision": "word",
                 "segments": [{"text": "hi", "start": 0, "end": 1}],
                 "words": [{"word": "hi", "start": 0, "end": 1}]}))
            out.with_name(out.name + ".key.json").write_text(json.dumps({
                "key": seg._transcript_key(audio, cfg), "sha256": seg._file_digest(out)}))
            with mock.patch.object(seg, "_run") as run:
                data = seg.transcribe(audio, cfg, out)
            run.assert_not_called()
            self.assertEqual(data["words"][0]["word"], "hi")

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
