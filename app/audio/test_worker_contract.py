# -*- coding: utf-8 -*-
"""Worker 合同回归：只用 mock，不加载/下载模型，不运行真实转写。"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from audio import config, worker


class ConfigResourceTests(unittest.TestCase):
    def test_multilingual_large_default_and_explicit_legacy_models(self):
        self.assertEqual(config.DEFAULTS["asr_model"], "large-v3-turbo")
        self.assertEqual(config.validate({})["asr_model"], "large-v3-turbo")
        self.assertEqual(config.validate({"asr_model": "large-v3"})["asr_model"], "large-v3")
        self.assertEqual(config.validate({"asr_model": "small"})["asr_model"], "small")
        shipped = json.loads((config.PLUGIN_ROOT / "config" / "audio.json").read_text())
        for key in ("asr_model", "cpu_threads", "batch_size", "max_parallel_transcriptions"):
            self.assertEqual(shipped[key], config.DEFAULTS[key])

    def test_budget_validation(self):
        for key, bad in (("cpu_threads", 0), ("cpu_threads", True), ("batch_size", 1.5),
                         ("batch_size", "2"), ("max_parallel_transcriptions", 3),
                         ("cpu_threads", 65), ("batch_size", -1)):
            with self.subTest(key=key, value=bad), self.assertRaises(ValueError):
                config.validate({key: bad})
        cfg = config.validate({"cpu_threads": 2, "batch_size": 1, "max_parallel_transcriptions": 2})
        with patch.object(config, "resolve_hf_endpoint", return_value=""):
            env = config.model_env(cfg)
        self.assertEqual(env["WF_AUDIO_CPU_THREADS"], "2")
        self.assertEqual(env["WF_AUDIO_BATCH_SIZE"], "1")
        self.assertEqual(env["WF_AUDIO_MAX_PARALLEL"], "2")
        self.assertEqual(env["OMP_NUM_THREADS"], "2")
        self.assertEqual(env["MKL_NUM_THREADS"], "2")
        self.assertEqual(env["OPENBLAS_NUM_THREADS"], "2")


class WorkerContractTests(unittest.TestCase):
    def run_worker(self, sources, *, no_align=False, aligned=None, language="en", align_effect=None,
                   regions=None, audio=None, transcriptions=None):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "transcript.json"
            args = argparse.Namespace(asr="large-v3-turbo", align="facebook/wav2vec2-base-960h",
                                      device="cpu", compute_type="int8", model_dir=directory,
                                      vad_method="pyannote", cpu_threads=2, batch_size=1,
                                      max_parallel=1, language=language, no_align=no_align,
                                      audio="not-opened.wav", out=str(output), regions=regions)
            api = Mock()
            if audio is not None:
                api.load_audio.return_value = audio
            api.load_model.return_value.transcribe.return_value = {
                "language": language, "segments": deepcopy(sources)}
            if transcriptions is not None:
                api.load_model.return_value.transcribe.side_effect = deepcopy(transcriptions)
            api.load_align_model.return_value = (object(), {"language": "en"})
            api.align.return_value = aligned or {"segments": []}
            if align_effect:
                api.align.side_effect = align_effect
            with patch.object(worker, "common", return_value=api), \
                    patch.object(worker, "capabilities", return_value={"model_loaded": False}), \
                    patch.object(worker, "_configure_torch"), \
                    patch.dict(os.environ), redirect_stdout(io.StringIO()):
                worker.cmd_transcribe(args)
                self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")
                self.assertEqual(os.environ["TRANSFORMERS_OFFLINE"], "1")
            data = json.loads(output.read_text())
            self.assertFalse(output.with_name(output.name + ".tmp").exists())
            return data, api

    def test_regions_use_one_model_real_offsets_and_full_audio_for_alignment(self):
        audio = range(40 * worker.SAMPLE_RATE)
        regions = [{"start": 10, "end": 12}, {"start": 20, "end": 22}]
        results = [{"language": "en", "segments": [{"start": .25, "end": 1, "text": "Hello"}]},
                   {"language": "en", "segments": [{"start": .5, "end": 1.5, "text": "Bye"}]}]
        def align(segments, model, metadata, source_audio, device, **kwargs):
            self.assertIs(source_audio, audio)
            segment = segments[0]
            return {"segments": [{"words": [{"word": segment["text"], "start": segment["start"],
                                               "end": segment["end"]}]}]}
        data, api = self.run_worker([], regions=json.dumps(regions), audio=audio,
                                    transcriptions=results, align_effect=align)
        api.load_model.assert_called_once()
        calls = api.load_model.return_value.transcribe.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].args[0], audio[160000:192000])
        self.assertEqual(calls[1].args[0], audio[320000:352000])
        self.assertEqual([(s["start"], s["end"], s["region_id"]) for s in data["segments"]],
                         [(10.25, 11.0, 0), (20.5, 21.5, 1)])
        self.assertEqual([(w["start"], w["end"]) for w in data["words"]],
                         [(10.25, 11.0), (20.5, 21.5)])
        self.assertEqual(data["timestamp_precision"], "word")
        self.assertEqual([word["region_id"] for word in data["words"]], [0, 1])
        self.assertEqual(data["regions"][1]["sample_start"], 320000)

    def test_regions_empty_speech_region_keeps_later_offsets(self):
        results = [{"language": "en", "segments": []},
                   {"language": "en", "segments": [{"start": .1, "end": .8, "text": "Hello"}]}]
        data, api = self.run_worker([], regions=[{"start": 0, "end": 2}, {"start": 3, "end": 4}],
                                    audio=range(4 * worker.SAMPLE_RATE), no_align=True,
                                    transcriptions=results)
        api.load_model.assert_called_once()
        self.assertEqual(api.load_model.return_value.transcribe.call_count, 2)
        self.assertEqual(len(data["segments"]), 1)
        self.assertEqual(data["segments"][0]["region_id"], 1)
        self.assertAlmostEqual(data["segments"][0]["start"], 3.1)
        self.assertEqual(data["diagnostics"][0]["code"], "empty_region")
        self.assertEqual(data["diagnostics"][0]["region_id"], 0)

    def test_regions_all_empty_remain_real_empty_transcript(self):
        data, api = self.run_worker([], regions=[{"start": 0, "end": 2}],
                                    audio=range(2 * worker.SAMPLE_RATE), no_align=True,
                                    transcriptions=[{"language": "en", "segments": []}])
        self.assertEqual(data["segments"], [])
        self.assertEqual(data["words"], [])
        self.assertEqual(data["text"], "")
        api.load_model.assert_called_once()

    def test_regions_mixed_languages_still_align_english_region(self):
        results = [{"language": "zh", "segments": [{"start": .1, "end": 1.8, "text": "第三题"}]},
                   {"language": "en", "segments": [{"start": .1, "end": .8, "text": "Hello"}]}]
        aligned = {"segments": [{"words": [{"word": "Hello", "start": 3.1, "end": 3.8}]}]}
        data, api = self.run_worker([], regions=[{"start": 0, "end": 2}, {"start": 3, "end": 4}],
                                    audio=range(4 * worker.SAMPLE_RATE), language="auto",
                                    transcriptions=results, aligned=aligned)
        self.assertEqual(data["language"], "mixed")
        self.assertEqual(data["timestamp_precision"], "mixed")
        self.assertEqual([word["word"] for word in data["words"]], ["Hello"])
        self.assertEqual(data["segments"][0]["language"], "zh")
        self.assertEqual(data["segments"][0]["timestamp_precision"], "segment")
        self.assertEqual(api.align.call_count, 1)
        self.assertIsNone(api.load_model.return_value.transcribe.call_args_list[0].kwargs["language"])

    def test_regions_invalid_inputs_fail_before_model_load(self):
        invalid = [[], {}, "{}",  [{"start": -1, "end": 1}], [{"start": 0, "end": 0}],
                   [{"start": 1, "end": 0}], [{"start": 0, "end": float("inf")}],
                   [{"start": float("nan"), "end": 1}], [{"start": True, "end": 1}],
                   [{"start": "0", "end": 1}], [{"start": 0}],
                   [{"start": 0, "end": 2}, {"start": 1, "end": 3}],
                   [{"start": 3, "end": 4}, {"start": 0, "end": 1}],
                   [{"start": 0, "end": 11}], [{"start": .000001, "end": .000002}], "[bad"]
        for regions in invalid:
            with self.subTest(regions=regions), tempfile.TemporaryDirectory() as directory:
                args = argparse.Namespace(asr="large-v3-turbo", align="fake", device="cpu",
                                          compute_type="int8", model_dir=directory, vad_method="pyannote",
                                          cpu_threads=2, batch_size=1, max_parallel=1, language="en",
                                          no_align=True, audio="fake", out=str(Path(directory) / "out.json"),
                                          regions=regions)
                api = Mock()
                api.load_audio.return_value = range(10 * worker.SAMPLE_RATE)
                with patch.object(worker, "common", return_value=api), \
                        patch.object(worker, "capabilities", return_value={}), \
                        patch.object(worker, "_configure_torch"), patch.dict(os.environ), \
                        redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
                    worker.cmd_transcribe(args)
                api.load_model.assert_not_called()
                self.assertFalse(Path(args.out).exists())

    def test_regions_cli_argument_reaches_transcribe(self):
        regions = '[{"start":0,"end":2}]'
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ), \
                patch.object(worker, "cmd_transcribe") as transcribe, redirect_stdout(io.StringIO()):
            worker.main(["transcribe", "--model-dir", directory, "--audio", "fake.wav",
                         "--out", str(Path(directory) / "fake.json"), "--regions", regions])
        self.assertEqual(transcribe.call_args.args[0].regions, regions)

    def test_regions_json_file_and_actual_sample_offset(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "regions.json"
            source.write_text('[{"start":0.0001,"end":1}]')
            regions = worker._parse_regions(str(source))
            checked = worker._regions_for_audio(regions, range(worker.SAMPLE_RATE))
            self.assertEqual(checked[0]["sample_start"], 1)
            result = Mock()
            result.transcribe.return_value = {"language": "en", "segments": [
                {"start": .1, "end": .5, "text": "Hello"}]}
            with redirect_stdout(io.StringIO()):
                merged = worker._transcribe_regions(result, range(worker.SAMPLE_RATE), checked, 1, "en", [])
            self.assertAlmostEqual(merged["segments"][0]["start"], .1 + 1 / worker.SAMPLE_RATE)

    def test_region_model_local_out_of_bounds_is_diagnosed_not_shifted(self):
        results = [{"language": "en", "segments": [{"start": .1, "end": 2.1, "text": "invalid"},
                   {"start": .1, "end": .8, "text": "Hello"}]}]
        data, _ = self.run_worker([], regions=[{"start": 2, "end": 4}],
                                  audio=range(4 * worker.SAMPLE_RATE), no_align=True, transcriptions=results)
        self.assertEqual([s["text"] for s in data["segments"]], ["Hello"])
        self.assertIn("region_segment_out_of_bounds", [d["code"] for d in data["diagnostics"]])

    def test_no_align_preserves_real_segments_without_pseudo_words(self):
        source = [{"start": 1.125, "end": 21.875, "text": "下面回答第十八至二十小题。"}]
        data, api = self.run_worker(source, no_align=True, language="zh")
        self.assertEqual(data["schema_version"], 2)
        self.assertEqual(data["timestamp_precision"], "segment")
        self.assertEqual(data["words"], [])
        self.assertEqual(worker._segments_to_words(source), [])
        self.assertEqual(data["segments"][0]["start"], 1.125)
        self.assertEqual(data["segments"][0]["end"], 21.875)
        self.assertEqual(data["segments"][0]["text"], source[0]["text"])
        self.assertEqual(data["segments"][0]["timestamp_precision"], "segment")
        self.assertIn("alignment_disabled", [d["code"] for d in data["diagnostics"]])
        api.align.assert_not_called()
        api.load_align_model.assert_not_called()
        api.load_model.assert_called_once_with("large-v3-turbo", device="cpu", compute_type="int8",
                                              download_root=api.load_model.call_args.kwargs["download_root"],
                                              vad_method="pyannote", language="zh", local_files_only=True,
                                              threads=2)
        api.load_model.return_value.transcribe.assert_called_once_with(api.load_audio.return_value,
                                                                       batch_size=1, language="zh")

    def test_valid_alignment_keeps_source_bounds_and_sentence_mapping(self):
        source = [{"start": 2, "end": 9, "text": "Hello. World."},
                  {"start": 10, "end": 12, "text": "Bye."}]
        def align(segments, *args, **kwargs):
            self.assertEqual(kwargs["interpolate_method"], "ignore")
            if segments[0]["start"] == 2:
                # 模拟 WhisperX 原地修改，并将一原段拆成两句。
                segments[0]["start"] = 0
                return {"segments": [
                    {"start": 0, "end": 3, "words": [{"word": "Hello.", "start": 2.4, "end": 3}]},
                    {"start": 6, "end": 9, "words": [{"word": "World.", "start": 7, "end": 8}]}]}
            return {"segments": [{"words": [{"word": "Bye.", "start": 10.2, "end": 11}]}]}
        data, api = self.run_worker(source, align_effect=align)
        self.assertEqual(data["timestamp_precision"], "word")
        self.assertEqual(len(data["words"]), 3)
        self.assertEqual([(s["start"], s["end"]) for s in data["segments"]], [(2, 9), (10, 12)])
        self.assertEqual(api.align.call_count, 2)
        self.assertEqual(data["diagnostics"], [])

    def test_outside_neighbor_and_illegal_times_are_rejected(self):
        source = [{"start": 1, "end": 3, "text": "Question"},
                  {"start": 3, "end": 5, "text": "Two"}]
        for start, end, reason in ((0.9, 2, "word_outside_source_segment"),
                                   (1.1, 3.2, "word_outside_source_segment"),
                                   (1, float("nan"), "invalid_word_bounds"),
                                   (2, 2, "invalid_word_bounds"),
                                   (-1, 1, "invalid_word_bounds"),
                                   (2, 1, "invalid_word_bounds"),
                                   (True, 2, "invalid_word_bounds")):
            with self.subTest(start=start, end=end):
                results = iter([
                    {"segments": [{"words": [{"word": "Question", "start": start, "end": end}]}]},
                    {"segments": [{"words": [{"word": "Two", "start": 3.1, "end": 4}]}]}])
                data, _ = self.run_worker(source, align_effect=lambda *a, **k: next(results))
                self.assertEqual(data["timestamp_precision"], "mixed")
                self.assertEqual([w["word"] for w in data["words"]], ["Two"])
                self.assertIn(reason, [d.get("reason") for d in data["diagnostics"]])
                self.assertEqual(data["segments"][0]["end"], 3)

    def test_words_cannot_overlap_another_source_segment(self):
        source = [{"start": 1, "end": 4, "text": "Question"},
                  {"start": 3, "end": 5, "text": "Two"}]
        results = iter([
            {"segments": [{"words": [{"word": "Question", "start": 2, "end": 3.5}]}]},
            {"segments": [{"words": [{"word": "Two", "start": 4.1, "end": 4.8}]}]}])
        data, _ = self.run_worker(source, align_effect=lambda *a, **k: next(results))
        self.assertEqual([w["word"] for w in data["words"]], ["Two"])
        self.assertIn("word_overlaps_other_segment", [d.get("reason") for d in data["diagnostics"]])

    def test_overlapping_words_or_missing_tokens_reject_whole_segment(self):
        source = [{"start": 0, "end": 5, "text": "Question one"}]
        for words, reason in (([{"word": "Question", "start": 1, "end": 3},
                                {"word": "one", "start": 2, "end": 4}], "word_order_or_overlap"),
                              ([{"word": "Question", "start": 1, "end": 2},
                                {"word": "one"}], "invalid_word_bounds"),
                              ([{"word": "Question", "start": 1, "end": 2}], "aligned_text_mismatch")):
            with self.subTest(reason=reason):
                data, _ = self.run_worker(source, aligned={"segments": [{"words": words}]})
                self.assertEqual(data["words"], [])
                self.assertEqual(data["timestamp_precision"], "segment")
                self.assertIn(reason, [d.get("reason") for d in data["diagnostics"]])

    def test_alignment_compressed_rejects_real_reported_front_loaded_timing(self):
        source = [{"start": 102.884, "end": 130.205, "text": "Question three Question four"}]
        aligned = {"segments": [{"words": [
            {"word": "Question", "start": 102.9, "end": 104},
            {"word": "three", "start": 104, "end": 106},
            {"word": "Question", "start": 106, "end": 110},
            {"word": "four", "start": 110, "end": 111.8}]}]}
        data, _ = self.run_worker(source, aligned=aligned)
        self.assertEqual(data["words"], [])
        self.assertEqual(data["timestamp_precision"], "segment")
        self.assertEqual(data["alignment_validation_version"], 2)
        diagnostic = next(d for d in data["diagnostics"] if d.get("reason") == "alignment_compressed")
        self.assertAlmostEqual(diagnostic["end_gap"], 18.405)
        self.assertAlmostEqual(diagnostic["boundary_tolerance"], 5.4642)
        self.assertEqual(data["segments"][0]["end"], 130.205)

    def test_boundary_coverage_checks_start_and_end_with_documented_tolerance(self):
        for start, end, rejected in ((0, 7.999, True), (2.001, 10, True),
                                      (2, 8, False), (0, 8, False), (0, 10, False)):
            with self.subTest(start=start, end=end):
                source = [{"start": 0, "end": 10, "text": "Hello"}]
                aligned = {"segments": [{"words": [{"word": "Hello", "start": start, "end": end}]}]}
                data, _ = self.run_worker(source, aligned=aligned)
                self.assertEqual(data["timestamp_precision"], "segment" if rejected else "word")
                if rejected:
                    self.assertIn("alignment_compressed", [d.get("reason") for d in data["diagnostics"]])

    def test_cjk_and_mixed_segments_stay_coarse_without_rejecting_english_segments(self):
        for text in ("下面请听第三题", "下面 Question three", "中文𠀀", "こんにちは", "한국어"):
            with self.subTest(text=text):
                source = [{"start": 22.542, "end": 47.163, "text": text},
                          {"start": 50, "end": 54, "text": "Hello"}]
                aligned = {"segments": [{"words": [{"word": "Hello", "start": 50.1, "end": 53.8}]}]}
                data, api = self.run_worker(source, aligned=aligned)
                self.assertEqual(data["timestamp_precision"], "mixed")
                self.assertEqual([word["word"] for word in data["words"]], ["Hello"])
                self.assertEqual(data["segments"][0]["timestamp_precision"], "segment")
                self.assertEqual(data["segments"][0]["start"], 22.542)
                self.assertEqual(data["segments"][0]["end"], 47.163)
                self.assertIn("english_alignment_contains_cjk", [d.get("reason") for d in data["diagnostics"]])
                self.assertEqual(api.align.call_count, 1)

    def test_pure_revalidation_downgrades_old_words_without_mutating_input(self):
        old = {"schema_version": 2, "language": "en", "timestamp_precision": "word", "words": ["stale"],
               "diagnostics": [], "segments": [
                   {"start": 102.884, "end": 130.205, "text": "Question four", "timestamp_precision": "word",
                    "words": [{"word": "Question", "start": 103, "end": 107},
                              {"word": "four", "start": 108, "end": 111.8}]},
                   {"start": 22.542, "end": 47.163, "text": "下面请听第三题", "timestamp_precision": "word",
                    "words": [{"word": "下面请听第三题", "start": 22.562, "end": 31.069}]},
                   {"start": 50, "end": 54, "text": "Hello", "timestamp_precision": "word",
                    "words": [{"word": "Hello", "start": 50.1, "end": 53.8}]}]}
        untouched = deepcopy(old)
        with patch.object(worker, "common") as common:
            reviewed = worker.revalidate_transcript(old)
            common.assert_not_called()
        self.assertEqual(old, untouched)
        self.assertEqual(reviewed["timestamp_precision"], "mixed")
        self.assertEqual([word["word"] for word in reviewed["words"]], ["Hello"])
        self.assertEqual(reviewed["segments"][0]["words"], [])
        self.assertEqual(reviewed["segments"][1]["words"], [])
        self.assertEqual(reviewed["segments"][0]["end"], 130.205)
        reasons = [d.get("reason") for d in reviewed["diagnostics"]]
        self.assertIn("alignment_compressed", reasons)
        self.assertIn("english_alignment_contains_cjk", reasons)
        self.assertEqual(reviewed["alignment_validation_version"], worker.ALIGNMENT_VALIDATION_VERSION)

    def test_direct_revalidation_clears_stale_words_even_when_input_aliases_candidates(self):
        segments = [{"start": 0, "end": 10, "text": "Hello", "timestamp_precision": "word",
                     "words": [{"word": "Hello", "start": 0, "end": 3}]}]
        diagnostics = []
        result = worker._validated_alignment(segments, {"segments": segments}, diagnostics)
        self.assertEqual(result, [])
        self.assertEqual(segments[0]["words"], [])
        self.assertEqual(segments[0]["timestamp_precision"], "segment")
        self.assertEqual(diagnostics[0]["reason"], "alignment_compressed")

    def test_non_english_does_not_use_english_align_model(self):
        data, api = self.run_worker([{"start": 0, "end": 5, "text": "第十八题"}], language="zh")
        api.load_align_model.assert_not_called()
        self.assertEqual(data["timestamp_precision"], "segment")
        self.assertEqual(data["diagnostics"][0]["code"], "alignment_language_unsupported")

    def test_alignment_failure_reports_and_preserves_source(self):
        data, _ = self.run_worker([{"start": 0, "end": 5, "text": "Hello"}],
                                  align_effect=RuntimeError("mock alignment failure"))
        self.assertEqual(data["words"], [])
        self.assertEqual(data["segments"][0]["end"], 5)
        self.assertIn("alignment_failed", [d["code"] for d in data["diagnostics"]])

    def test_asr_failure_has_no_small_fallback_or_output(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ):
            args = argparse.Namespace(asr="large-v3-turbo", align="fake", device="cpu",
                                      compute_type="int8", model_dir=directory, vad_method="pyannote",
                                      cpu_threads=2, batch_size=1, max_parallel=1, language="en",
                                      no_align=False, audio="fake", out=str(Path(directory) / "out.json"))
            api = Mock()
            api.load_model.side_effect = RuntimeError("model not cached")
            with patch.object(worker, "common", return_value=api), \
                    patch.object(worker, "capabilities", return_value={}), \
                    patch.object(worker, "_configure_torch"), redirect_stdout(io.StringIO()), \
                    self.assertRaisesRegex(RuntimeError, "not cached"):
                worker.cmd_transcribe(args)
            self.assertEqual(api.load_model.call_count, 1)
            self.assertEqual(api.load_model.call_args.args[0], "large-v3-turbo")
            self.assertFalse(Path(args.out).exists())

    def test_bootstrap_keeps_explicit_download_and_thread_budget(self):
        args = argparse.Namespace(asr="large-v3-turbo", align="facebook/wav2vec2-base-960h",
                                  device="cpu", compute_type="int8", model_dir="models",
                                  vad_method="pyannote", cpu_threads=3)
        api = Mock()
        api.load_align_model.return_value = (object(), {})
        with patch.object(worker, "common", return_value=api), \
                patch.object(worker, "capabilities", return_value={}), \
                patch.object(worker, "_configure_torch"), redirect_stdout(io.StringIO()):
            worker.cmd_bootstrap(args)
        self.assertEqual(api.load_model.call_args.args[0], "large-v3-turbo")
        self.assertFalse(api.load_model.call_args.kwargs["local_files_only"])
        self.assertEqual(api.load_model.call_args.kwargs["threads"], 3)
        api.load_align_model.assert_called_once()

    def test_configure_threads_limits_native_and_torch_pools(self):
        args = argparse.Namespace(cpu_threads=2)
        torch = Mock()
        with patch.dict(os.environ), patch.dict(sys.modules, {"torch": torch}):
            worker._configure_threads(args)
            worker._configure_torch(args)
            for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
                self.assertEqual(os.environ[name], "2")
            self.assertEqual(os.environ["TOKENIZERS_PARALLELISM"], "false")
        torch.set_num_threads.assert_called_once_with(2)
        torch.set_num_interop_threads.assert_called_once_with(1)

    def test_cli_resource_environment_and_capability_probe_do_not_load_model(self):
        captured = []
        def probe(args):
            captured.append(args)
            return {"model_loaded": False}
        with patch.dict(os.environ, {"WF_AUDIO_CPU_THREADS": "3", "WF_AUDIO_BATCH_SIZE": "1",
                                      "WF_AUDIO_MAX_PARALLEL": "2"}), \
                patch.object(worker, "capabilities", side_effect=probe), \
                patch.object(worker, "model_slot") as slot, \
                patch.object(worker, "common") as common, redirect_stdout(io.StringIO()):
            self.assertEqual(worker.main(["capabilities", "--model-dir", "/not-created"]), 0)
            self.assertEqual(captured[0].cpu_threads, 3)
            self.assertEqual(captured[0].batch_size, 1)
            self.assertEqual(captured[0].max_parallel, 2)
            self.assertEqual(captured[0].asr, "large-v3-turbo")
            slot.assert_not_called()
            common.assert_not_called()

    @unittest.skipIf(os.name == "nt", "POSIX cross-process lock regression")
    def test_model_slot_serializes_other_process_and_releases_after_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            # 使用独立进程，不导入任何 ML 依赖，也不转写。
            code = "import sys\nfrom audio.worker import model_slot\nwith model_slot(sys.argv[1], 1):\n print('acquired', flush=True)\n"
            env = dict(os.environ, PYTHONPATH=str(config.APP_ROOT))
            process = None
            try:
                with self.assertRaisesRegex(RuntimeError, "release"), worker.model_slot(directory, 1):
                    process = subprocess.Popen([sys.executable, "-u", "-c", code, directory],
                                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                               text=True, env=env)
                    # 子进程必须先报告等待，而不能获取第二份模型槽。
                    first_line = process.stdout.readline()
                    self.assertEqual(json.loads(first_line)["phase"], "queue")
                    raise RuntimeError("release")
                out, err = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, err)
                self.assertEqual(out.strip(), "acquired")
            finally:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.communicate()


if __name__ == "__main__":
    unittest.main()
