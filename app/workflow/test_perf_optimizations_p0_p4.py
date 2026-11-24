# -*- coding: utf-8 -*-
"""Comprehensive offline regression suite for P0–P4 zero-quality-loss performance optimizations."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from workbench import server
from workflow import local_agent, review_rounds, runner, structure_gate


REPO_ROOT = Path(__file__).resolve().parents[2]


class P0MultiBatchConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for key, value in (
            ("WORKFLOWS", self.root),
            ("_advance_state", {}),
            ("_advance_queue", []),
            ("_advance_worker_thread", None),
            ("_advance_worker_threads", []),
            ("_advance_worker_running", False),
            ("_advance_worker_start_error", None),
            ("_workflow_supervisor", None),
        ):
            p = patch.object(server, key, value)
            p.start()
            self.addCleanup(p.stop)

    def _mk_batch(self, name: str, created: str) -> Path:
        folder = self.root / name
        (folder / "workflow").mkdir(parents=True)
        (folder / "workflow" / "manifest.json").write_text(
            json.dumps({"created_at": created, "stages": {"upload": {"status": "pending"}}}),
            encoding="utf-8",
        )
        return folder

    def test_max_concurrent_batches_env_parsing_and_clamping(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WF_MAX_CONCURRENT_BATCHES", None)
            self.assertEqual(server._max_concurrent_batches(), 1)
        for raw, expected in [("1", 1), ("2", 2), ("4", 4), ("9", 4), ("0", 1), ("-3", 1), ("abc", 1)]:
            with patch.dict(os.environ, {"WF_MAX_CONCURRENT_BATCHES": raw}, clear=False):
                self.assertEqual(server._max_concurrent_batches(), expected, raw)

    def test_concurrency_window_allows_top_k_and_blocks_beyond_k(self):
        self._mk_batch("b1", "2026-01-01T00:00:00+08:00")
        self._mk_batch("b2", "2026-01-02T00:00:00+08:00")
        self._mk_batch("b3", "2026-01-03T00:00:00+08:00")
        with patch.object(server, "wf_execution_state",
                          SimpleNamespace(local_complete=lambda f, m: False)):
            with patch.dict(os.environ, {"WF_MAX_CONCURRENT_BATCHES": "2"}, clear=False):
                self.assertIsNone(server._waiting_for_predecessor("b1"))
                self.assertIsNone(server._waiting_for_predecessor("b2"))
                self.assertEqual(server._waiting_for_predecessor("b3"), "b1")
            with patch.dict(os.environ, {"WF_MAX_CONCURRENT_BATCHES": "1"}, clear=False):
                self.assertIsNone(server._waiting_for_predecessor("b1"))
                self.assertEqual(server._waiting_for_predecessor("b2"), "b1")

    def test_two_batches_execute_truly_concurrently_and_wake_supervisor(self):
        b1 = self._mk_batch("b1", "2026-01-01T00:00:00+08:00")
        b2 = self._mk_batch("b2", "2026-01-02T00:00:00+08:00")
        barrier = threading.Barrier(2, timeout=5)
        ran = []
        ran_lock = threading.Lock()
        claim_kwargs = []

        def fake_claim(folder, **kwargs):
            claim_kwargs.append((folder.name, kwargs))
            return nullcontext(True)

        def fake_run(name, folder):
            # Proves both batches are executing simultaneously on separate worker threads.
            barrier.wait()
            with ran_lock:
                ran.append(name)
            with server._lock:
                server._advance_state[name] = {"running": False, "result": {"ok": True}}

        wake_mock = Mock()
        with patch.dict(os.environ, {"WF_MAX_CONCURRENT_BATCHES": "2"}, clear=False), \
                patch.object(server, "wf_execution_state", SimpleNamespace(local_complete=lambda f, m: False)), \
                patch("workflow.supervisor.advance_claim", side_effect=fake_claim), \
                patch.object(server, "_run_advance_now", side_effect=fake_run), \
                patch.object(server, "_wake_supervisor", wake_mock):
            r1 = server._launch_advance("b1", b1)
            r2 = server._launch_advance("b2", b2)
            self.assertTrue(r1["started"])
            self.assertTrue(r2["started"])
            # Wait for all spawned workers to drain cleanly.
            for t in list(server._advance_worker_threads):
                t.join(5)
            if server._advance_worker_thread is not None:
                server._advance_worker_thread.join(5)

        self.assertEqual(sorted(ran), ["b1", "b2"])
        self.assertIsNone(server._advance_worker_thread)
        self.assertFalse(server._advance_worker_running)
        self.assertEqual(server._advance_queue, [])
        # Each launch passed include_global_agent=False in concurrent mode.
        self.assertTrue(all(kw.get("include_global_agent") is False for _, kw in claim_kwargs))
        # Supervisor woken immediately on completion to eliminate inter-batch handoff gap.
        self.assertGreaterEqual(wake_mock.call_count, 2)

    def test_plugin_host_sets_default_concurrent_batches_env(self):
        js = (REPO_ROOT / "lib" / "index.js").read_text(encoding="utf-8")
        self.assertIn("WF_MAX_CONCURRENT_BATCHES", js)


class P1ScopedStructureFastPathTests(unittest.TestCase):
    def _make_scoped_batch(self, root: Path, *, unresolved=None) -> Path:
        batch = root / "sync_batch"
        (batch / "01_source").mkdir(parents=True)
        (batch / "07_review").mkdir(parents=True)
        (batch / "workflow").mkdir(parents=True)
        body = (
            "第一课时 Section A\n"
            "一、单项填空\n"
            "1. —Hello! —____\n"
            "A. Hi! B. Bye! C. Thanks. D. OK.\n"
            "2. —How are you? —____\n"
            "A. Fine. B. Ten. C. Red. D. Yes.\n"
            "第二课时 Section B\n"
            "一、单项填空\n"
            "1. She ____ to school every day.\n"
            "A. go B. goes C. going D. went\n"
        )
        (batch / "01_source" / "正文.txt").write_text(body, encoding="utf-8")
        structure = {
            "sections": [
                {"type": "单项填空", "title": "一、单项填空", "source_line": 2, "source_end_line": 6,
                 "questions": [1, 2], "declared_count": 2},
                {"type": "单项填空", "title": "一、单项填空", "source_line": 8, "source_end_line": 10,
                 "questions": [1], "declared_count": 1},
            ],
            "groups": [],
            "unresolved": unresolved or [],
        }
        (batch / "01_source" / "structure.json").write_text(
            json.dumps(structure, ensure_ascii=False), encoding="utf-8"
        )
        (batch / "workflow" / "manifest.json").write_text(
            json.dumps({"inputs": {}, "stages": {}}, ensure_ascii=False), encoding="utf-8"
        )
        return batch

    def test_clean_scoped_sync_practice_skips_structure_ai(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = self._make_scoped_batch(Path(tmp))
            with patch.dict(os.environ, {"WF_STRUCTURE_AI": "1"}, clear=False), \
                    patch("workflow.structure_ai.auto_classify") as ai_mock:
                report = structure_gate.run_gate(batch)
            ai_mock.assert_not_called()
            self.assertTrue(report["passed"])
            codes = {w.get("code") for w in report.get("warnings", [])}
            self.assertIn("AI_STRUCTURE_SKIPPED", codes)
            confirmed = json.loads((batch / "01_source" / "structure.confirmed.json").read_text(encoding="utf-8"))
            self.assertEqual(confirmed.get("identity_version"), "scoped-v1")
            self.assertEqual(len(confirmed.get("units", [])), 3)

    def test_non_clean_scoped_batch_still_falls_back_to_structure_ai(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = self._make_scoped_batch(
                Path(tmp),
                unresolved=[{"code": "IMAGE_SECTION_UNNUMBERED", "message": "图片题未标号"}],
            )
            with patch.dict(os.environ, {"WF_STRUCTURE_AI": "1"}, clear=False), \
                    patch("workflow.structure_ai.auto_classify", return_value={"units": []}) as ai_mock:
                structure_gate.run_gate(batch)
            ai_mock.assert_called_once()

    def test_force_structure_ai_env_overrides_scoped_fast_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = self._make_scoped_batch(Path(tmp))
            with patch.dict(os.environ, {"WF_STRUCTURE_AI": "1", "WF_FORCE_STRUCTURE_AI": "1"}, clear=False), \
                    patch("workflow.structure_ai.auto_classify", return_value={"units": []}) as ai_mock:
                structure_gate.run_gate(batch)
            ai_mock.assert_called_once()


class P2ConfigGuardrailTests(unittest.TestCase):
    def test_all_agent_configs_aligned_and_memory_safe(self):
        plugin_template = REPO_ROOT / "backend" / "local-agent.plugin.json"
        plugin_configs = [plugin_template]
        effective = REPO_ROOT / "backend" / ".local-agent.effective.json"
        if effective.is_file():
            plugin_configs.append(effective)
        for path in plugin_configs:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["timeout_seconds"], 480, path.name)
            self.assertEqual(data["part_concurrency"], 4, path.name)
            self.assertEqual(data["review_part_units"], 5, path.name)
            self.assertEqual(data["review_part_parallel"], 4, path.name)
            self.assertEqual(data["retries"], 2, path.name)
            self.assertEqual(data["review_max_attempts"], 2, path.name)
            # 2 concurrent batches * review_part_parallel <= 8 (well below 16GB OOM threshold of 16)
            self.assertLessEqual(2 * data["review_part_parallel"], 8, path.name)

        # Standalone local workflow config (excluded from npm package; intentionally higher
        # single-batch concurrency 8/6 per 2026-11-23-knowledge-inline-budget-and-concurrency.md).
        local_cfg = REPO_ROOT / "app" / "workflow" / "local-agent.json"
        if local_cfg.is_file():
            data = json.loads(local_cfg.read_text(encoding="utf-8"))
            self.assertEqual(data["timeout_seconds"], 480, local_cfg.name)
            self.assertEqual(data["review_part_units"], 5, local_cfg.name)
            self.assertEqual(data["retries"], 2, local_cfg.name)
            self.assertEqual(data["review_max_attempts"], 2, local_cfg.name)
            self.assertGreaterEqual(data["part_concurrency"], 1, local_cfg.name)
            self.assertLessEqual(data["part_concurrency"], 8, local_cfg.name)
            self.assertGreaterEqual(data["review_part_parallel"], 1, local_cfg.name)
            self.assertLessEqual(data["review_part_parallel"], 6, local_cfg.name)


class P3TimeoutRetryAndTranscriptTests(unittest.TestCase):
    def test_transcript_excerpt_strips_nested_segment_words_losslessly(self):
        segments = [
            {"start": 0, "end": 10, "text": "before", "words": [{"start": 0, "end": 1, "word": "before"}]},
            {"start": 100, "end": 115, "text": "target dialogue",
             "words": [{"start": 100, "end": 102, "word": "target"}, {"start": 103, "end": 105, "word": "dialogue"}]},
            {"start": 200, "end": 210, "text": "after", "words": [{"start": 200, "end": 201, "word": "after"}]},
        ]
        words = [
            {"start": 0, "end": 1, "word": "before"},
            {"start": 100, "end": 102, "word": "target"},
            {"start": 103, "end": 105, "word": "dialogue"},
            {"start": 200, "end": 201, "word": "after"},
        ]
        transcript = {
            "schema_version": 2,
            "segments": segments,
            "words": words,
            "text": " ".join(s["text"] for s in segments),
        }
        evidence = {"items": [{"unit_id": "u1", "start": 100, "end": 120, "verified": True}]}
        cropped = review_rounds._transcript_excerpt(transcript, evidence, {"u1"})
        self.assertEqual(len(cropped["segments"]), 1)
        self.assertEqual(cropped["segments"][0]["text"], "target dialogue")
        self.assertNotIn("words", cropped["segments"][0])
        # Top-level cropped words are still preserved.
        self.assertEqual(cropped["words"], words[1:3])
        self.assertNotIn("text", cropped)

    def test_partition_fallback_strips_words_and_exact_duplicate_text(self):
        segments = [
            {"start": 0.0, "end": 2.0, "text": "Hello world.", "words": [{"word": "Hello"}]},
            {"start": 2.0, "end": 4.0, "text": "How are you?", "words": [{"word": "How"}]},
        ]
        transcript = {
            "segments": segments,
            "words": [{"word": "Hello"}, {"word": "How"}],
            "text": "Hello world. How are you?",
        }
        context = {
            "confirmed_units_with_stable_ids": [
                {"unit_id": "u1", "type": "听力短对话", "numbers": [1], "stem": "1. Q"}
            ],
            "02_audio/full-transcript.json": transcript,
        }
        # No segment-evidence -> fallback path
        base = {"prompt": "BEGIN SUPPLIED CONTENT (JSON)\n" + json.dumps(context, ensure_ascii=False) + "\nEND SUPPLIED CONTENT"}
        parts = review_rounds._partition_contexts(base, 500000, round_id="custom")
        part_ctx = json.loads(parts[0]["context"].split("\n", 1)[1])
        tx = part_ctx["02_audio/full-transcript.json"]
        self.assertNotIn("words", tx)
        self.assertNotIn("text", tx)
        self.assertNotIn("words", tx["segments"][0])
        self.assertEqual([s["text"] for s in tx["segments"]], ["Hello world.", "How are you?"])

    def test_partition_fallback_keeps_non_duplicate_text(self):
        transcript = {
            "segments": [{"start": 0.0, "end": 2.0, "text": "Hello."}],
            "text": "Hello. Extra untimed note.",
        }
        context = {
            "confirmed_units_with_stable_ids": [
                {"unit_id": "u1", "type": "听力短对话", "numbers": [1], "stem": "1. Q"}
            ],
            "02_audio/full-transcript.json": transcript,
        }
        base = {"prompt": "BEGIN SUPPLIED CONTENT (JSON)\n" + json.dumps(context, ensure_ascii=False) + "\nEND SUPPLIED CONTENT"}
        parts = review_rounds._partition_contexts(base, 500000, round_id="custom")
        part_ctx = json.loads(parts[0]["context"].split("\n", 1)[1])
        self.assertEqual(part_ctx["02_audio/full-transcript.json"]["text"], "Hello. Extra untimed note.")

    def test_single_shot_main_retries_timeout_once_and_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp) / "b"
            (batch / "workflow").mkdir(parents=True)
            cfg = dict(local_agent.DEFAULTS, retries=2, timeout_retries=1, max_input_chars=100000)
            calls = []

            def fake_invoke(prompt, run_dir, config, **kwargs):
                calls.append(prompt)
                if len(calls) == 1:
                    raise local_agent.AgentError("timed out after 480s", "timeout")
                return {"response": {"status": "completed"}, "artifacts": {}}

            with patch.object(local_agent, "load_config", return_value=cfg), \
                    patch.object(local_agent, "_agent_lock", return_value=nullcontext()), \
                    patch.object(local_agent, "_check_input_hashes"), \
                    patch.object(local_agent, "_record_assemble_guard"), \
                    patch.object(local_agent, "invoke", side_effect=fake_invoke), \
                    patch("workflow.agent_tasks.build_task", return_value={"prompt": "PROMPT", "input_hashes": {}}), \
                    patch("workflow.agent_tasks.accept_result", return_value={"completed": True}):
                code = local_agent.main(["--stage", "audio", "--batch-dir", str(batch)])
            self.assertEqual(code, 0)
            self.assertEqual(len(calls), 2)
            # Timeout retry must not inject spurious validation feedback into the prompt.
            self.assertEqual(calls[0], calls[1])

    def test_single_shot_main_stops_after_bounded_timeout_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp) / "b"
            (batch / "workflow").mkdir(parents=True)
            cfg = dict(local_agent.DEFAULTS, retries=2, timeout_retries=1, max_input_chars=100000)
            calls = []

            def always_timeout(prompt, run_dir, config, **kwargs):
                calls.append(1)
                raise local_agent.AgentError("timed out after 480s", "timeout")

            with patch.object(local_agent, "load_config", return_value=cfg), \
                    patch.object(local_agent, "_agent_lock", return_value=nullcontext()), \
                    patch.object(local_agent, "_check_input_hashes"), \
                    patch.object(local_agent, "invoke", side_effect=always_timeout), \
                    patch("workflow.agent_tasks.build_task", return_value={"prompt": "PROMPT", "input_hashes": {}}):
                code = local_agent.main(["--stage", "audio", "--batch-dir", str(batch)])
            self.assertEqual(code, 2)
            # 1 initial + 1 bounded timeout retry = 2 calls (does NOT burn a 3rd attempt).
            self.assertEqual(len(calls), 2)


class P4RunnerParallelStagesTests(unittest.TestCase):
    def test_runner_source_includes_answers_in_parallel_trio(self):
        src = (REPO_ROOT / "app" / "workflow" / "runner.py").read_text(encoding="utf-8")
        self.assertIn('parallel_ids = ("answers", "explanations", "knowledge")', src)
        self.assertIn('if len(ready) >= 2:', src)


if __name__ == "__main__":
    unittest.main()
