"""Regression tests for real-time monitoring optimizations on 17-year exam runs."""
import contextlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import (
    agent_tasks,
    content_agent,
    knowledge_direct,
    local_agent,
    manifest as mf,
    operations as op,
    review_rounds,
    unit_tasks,
)


class MonitoringOptimizationsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name) / "batch"
        self.batch.mkdir(parents=True)

    def _put(self, rel, value):
        p = self.batch / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return p

    def test_knowledge_direct_tags_fixed_single_path_types_and_merges_with_model(self):
        self._put("01_source/正文.txt", "Ⅰ.关键词语选择\n1. A. apple B. banana C. pear\nⅡ.单项填空\n2. She ___.\n")
        units = [
            {"unit_id": "q-1", "unit_kind": "standalone", "numbers": [1], "type": "听力关键词语选择"},
            {"unit_id": "q-2", "unit_kind": "standalone", "numbers": [2], "type": "单项填空"},
        ]
        self._put("01_source/structure.confirmed.json", {"units": units})
        self._put("03_types/types.json", {
            "items": [
                {"unit_id": "q-1", "type": "听力关键词语选择", "subtypes": [], "words": 4, "contentComplete": True, "evidence": ["正文 L2"]},
                {"unit_id": "q-2", "type": "单项填空", "subtypes": ["四个选项"], "words": 3, "contentComplete": True, "evidence": ["正文 L4"]},
            ]
        })
        direct = knowledge_direct.tag_units(self.batch, units)
        self.assertEqual(set(direct), {"q-1"})
        self.assertEqual(direct["q-1"]["knowledge"], ["语音/基本读音"])

    def test_content_agent_rejects_empty_explicit_candidate_without_wasting_attempt(self):
        with tempfile.TemporaryDirectory() as qdir:
            with patch.object(content_agent, "QUEUE_ROOT", Path(qdir)):
                task = {
                    "task_id": "a" * 32,
                    "stage": "knowledge",
                    "status": "claimed",
                    "session_id": "s1",
                    "unit_ids": ["q-1"],
                    "partial": True,
                    "created_at": 100.0,
                    "deadline": time.time() + 300,
                    "route": {"provider": "p", "model": "m"},
                    "context": {},
                    "input_hashes": {},
                    "validator_identity": "v1",
                    "attempts": [],
                    "candidate_parts": [],
                }
                content_agent._save(task)
                resp = content_agent._tool(task, "submit_candidate", {"candidate": {}})
                self.assertFalse(resp["accepted"])
                self.assertTrue(resp["retryable"])
                self.assertEqual(resp["code"], "empty_or_invalid_candidate")
                reloaded = content_agent._read(content_agent._task_path("a" * 32))
                self.assertEqual(reloaded.get("attempts", []), [])
                self.assertNotIn("validation_rejection", reloaded)

    def test_content_agent_lists_tasks_in_fifo_created_at_order(self):
        with tempfile.TemporaryDirectory() as qdir:
            with patch.object(content_agent, "QUEUE_ROOT", Path(qdir)):
                # Note: 'ffff...' is lexicographically AFTER '1111...', but created_at=10 < 20.
                older_id = "f" * 32
                newer_id = "1" * 32
                for tid, ts in ((newer_id, 20.0), (older_id, 10.0)):
                    content_agent._save({
                        "task_id": tid,
                        "stage": "knowledge",
                        "status": "queued",
                        "batch_dir": str(self.batch),
                        "unit_ids": ["q-1"],
                        "created_at": ts,
                        "deadline": time.time() + 300,
                        "route": {"provider": "p", "model": "m"},
                        "context": {},
                        "input_hashes": {},
                    })
                listed = content_agent.dispatch("GET", "/api/content-agent/tasks")["tasks"]
                self.assertEqual([t["task_id"] for t in listed], [older_id, newer_id])

    def test_review_partition_contexts_strips_unverified_words_and_prunes_catalog(self):
        ctx = {
            "confirmed_units_with_stable_ids": [
                {"unit_id": "q-1", "unit_kind": "standalone", "numbers": [1], "type": "听力关键词语选择"},
            ],
            "01_source/正文.txt": "Ⅰ.关键词语选择\n1. A. a B. b C. c",
            "03_types/types.json": {
                "items": [{"unit_id": "q-1", "type": "听力关键词语选择", "subtypes": []}]
            },
            "02_audio/full-transcript.json": {
                "text": "hello",
                "words": [{"word": "hello", "start": 0.0, "end": 0.5}],
                "segments": [{"start": 0.0, "end": 0.5, "text": "hello", "words": [{"word": "hello"}]}],
            },
            "skills-reference/type-knowledge-map.md": "duplicate md",
            "references/type-knowledge-map.json": {
                "_comment": "c",
                "by_type": {
                    "听力关键词语选择": {"dims": [{"dim": "语音"}]},
                    "阅读理解": {"dims": [{"dim": "主题"}]},
                },
            },
            "references/knowledge-catalog.json": {"points": [{"path": "语音/基本读音"}, {"path": "主题/人与自我"}]},
        }
        base = {
            "prompt": "Header\nBEGIN SUPPLIED CONTENT (JSON)\n" + json.dumps(ctx, ensure_ascii=False) + "\nEND SUPPLIED CONTENT\n"
        }
        parts = review_rounds._partition_contexts(base, 500000, max_units=5, round_id="labels", section_types=None)
        self.assertEqual(len(parts), 1)
        raw_c = parts[0]["context"]
        part_ctx = json.loads(raw_c[raw_c.find("{"):]) if isinstance(raw_c, str) else raw_c
        self.assertNotIn("skills-reference/type-knowledge-map.md", part_ctx)
        self.assertEqual(set(part_ctx["references/type-knowledge-map.json"]["by_type"]), {"听力关键词语选择"})
        self.assertNotIn("words", part_ctx["02_audio/full-transcript.json"])
        self.assertNotIn("words", part_ctx["02_audio/full-transcript.json"]["segments"][0])

    def test_stale_running_skips_when_recent_review_attempt_activity_exists(self):
        self._put("01_source/正文.txt", "1. hi")
        manifest = mf.new_manifest("test", self.batch, {"source": {"path": str(self.batch / "01_source/正文.txt"), "sha256": "x"}}, {})
        manifest["stages"]["review"] = {
            "status": mf.RUNNING,
            "started_at": "2020-01-01T00:00:00+00:00",
        }
        self._put("workflow/manifest.json", manifest)
        self._put("workflow/review-attempts/round-1/input.txt", "active")
        with patch.object(op, "_execution_busy", return_value=True):
            stage_id, _ = op._stale_running_candidate(self.batch, manifest)
            self.assertIsNone(stage_id)

    def test_review_runs_non_listening_while_audio_pending_and_reuses_cache_after_audio(self):
        self._put("01_source/正文.txt", "Ⅰ.关键词语选择\n1. A. a B. b C. c\nⅡ.单项填空\n2. She ___.\n")
        units = [
            {"unit_id": "q-1", "unit_kind": "standalone", "numbers": [1], "type": "听力关键词语选择"},
            {"unit_id": "q-2", "unit_kind": "standalone", "numbers": [2], "type": "单项填空"},
        ]
        self._put("01_source/structure.confirmed.json", {"units": units})
        self._put("03_types/types.json", {
            "items": [
                {"unit_id": "q-1", "type": "听力关键词语选择", "subtypes": []},
                {"unit_id": "q-2", "type": "单项填空", "subtypes": ["四个选项"]},
            ]
        })
        manifest = mf.new_manifest("test-opt-a", self.batch, {"source": {"path": str(self.batch / "01_source/正文.txt"), "sha256": "x"}}, {})
        manifest["stages"]["audio"] = {"status": mf.RUNNING}
        self._put("workflow/manifest.json", manifest)

        self.assertTrue(review_rounds._audio_pending(self.batch))
        nl_key = review_rounds._nonlistening_cache_path("labels", {"unit_ids": ["q-2"]}, {"01_source/正文.txt": "h1", "02_audio/full-transcript.json": "missing"})
        nl_key_after = review_rounds._nonlistening_cache_path("labels", {"unit_ids": ["q-2"]}, {"01_source/正文.txt": "h1", "02_audio/full-transcript.json": "sha256-ready"})
        self.assertEqual(nl_key, nl_key_after)

        manifest["stages"]["audio"] = {"status": mf.SKIPPED, "notes": "no audio"}
        self._put("workflow/manifest.json", manifest)
        self.assertFalse(review_rounds._audio_pending(self.batch))

    def test_p0_1_review_waiting_audio_exit_8_keeps_pending_and_skips_handoff(self):
        from workflow import agent, contracts, runner as rn

        self._put("01_source/正文.txt", "1. hi")
        manifest = mf.new_manifest("test-exit8", self.batch, {"source": {"path": str(self.batch / "01_source/正文.txt"), "sha256": "x"}}, {})
        self._put("workflow/manifest.json", manifest)

        from workflow import review as rev
        with patch.object(agent, "_review_inputs_ready", return_value=True), \
             patch.object(review_rounds, "run", return_value={"action": "waiting_audio", "waiting_audio": True, "completed": False}), \
             patch.object(rev, "evaluate", return_value={"passed": False}), \
             patch.object(local_agent, "load_config", return_value={"provider": "p", "model": "m"}), \
             patch.object(agent, "configured", return_value=True):
            code = agent._run_review_rounds(self.batch)
        self.assertEqual(code, agent.REVIEW_WAITING_AUDIO_EXIT)
        self.assertEqual(code, 8)

        review_stage = contracts.STAGE_BY_ID["review"]
        def fake_subproc_waiting_audio(*args, **kwargs):
            cur = mf.load(self.batch)
            mf.touch_stage(cur, "review", mf.PENDING, notes="非听力审核分片已完成并缓存，等待听力切段完成后补跑听力分片")
            mf.save(self.batch, cur)
            return type("P", (), {"returncode": 8, "stdout": '{"action":"waiting_audio"}', "stderr": ""})()
        with patch.object(rn.subprocess, "run", side_effect=fake_subproc_waiting_audio):
            rc = rn._execute_command_stage(self.batch, manifest, review_stage)
        self.assertEqual(rc, 8)
        reloaded = mf.load(self.batch)
        self.assertEqual(reloaded["stages"]["review"]["status"], mf.PENDING)

        # Fail closed if a subprocess exits 8 without transitioning review back to PENDING.
        fake_bogus = type("P", (), {"returncode": 8, "stdout": "", "stderr": "bogus"})()
        with patch.object(rn.subprocess, "run", return_value=fake_bogus):
            rc_bogus = rn._execute_command_stage(self.batch, reloaded, review_stage)
        self.assertEqual(rc_bogus, 1)
        self.assertEqual(mf.load(self.batch)["stages"]["review"]["status"], mf.FAILED)

    def test_p0_2_auto_defers_audio_after_first_review_and_requeues_second_review(self):
        from concurrent.futures import Future
        from workflow import auto, contracts, runner as rn

        self._put("01_source/正文.txt", "1. hi")
        self._put("attachments/audio.mp3", "fake")
        manifest = mf.new_manifest(
            "test-auto-defer",
            self.batch,
            {
                "source": {"path": str(self.batch / "01_source/正文.txt"), "sha256": "x", "exists": True},
                "audio": {"path": str(self.batch / "attachments/audio.mp3"), "sha256": "y", "exists": True},
            },
            {},
        )
        for sid in contracts.STAGE_ORDER:
            if sid in ("audio", "review", "review_gate", "upload_gate", "upload", "publish_gate"):
                manifest["stages"][sid] = {"status": mf.PENDING}
            else:
                manifest["stages"][sid] = {"status": mf.COMPLETED}
        self._put("workflow/manifest.json", manifest)

        audio_fut = Future()
        calls = []

        def fake_submit(fn, *args, **kwargs):
            return audio_fut

        def fake_exec(batch_dir, m, stage):
            calls.append(stage.id)
            if stage.id == "review" and calls.count("review") == 1:
                audio_fut.set_result({"status": "ready"})
                return 8
            if stage.id == "audio":
                cur = mf.load(batch_dir)
                mf.touch_stage(cur, "audio", mf.COMPLETED, notes="ok")
                mf.save(batch_dir, cur)
                return 0
            if stage.id == "review":
                cur = mf.load(batch_dir)
                mf.touch_stage(cur, "review", mf.COMPLETED, notes="ok")
                mf.save(batch_dir, cur)
                return 0
            return 0

        fake_worker = type("W", (), {"submit": staticmethod(fake_submit)})()
        bridge = {
            "audio": ["{python}", "-m", "workflow.agent", "run", "--stage", "audio", "--batch-dir", "{batch_dir}"],
            "review": ["{python}", "-m", "workflow.agent", "run", "--stage", "review", "--batch-dir", "{batch_dir}"],
        }
        with patch.object(auto, "PARALLEL_AGENTS_ENABLED", False), \
             patch.object(mf, "reconcile_deliveries", return_value=None), \
             patch.object(mf, "dependency_satisfied", side_effect=lambda m, sid, **kw: m["stages"].get(sid, {}).get("status") in mf.TERMINAL_OK), \
             patch.object(agent_tasks, "stage_gaps", return_value={}), \
             patch.object(auto, "_recover_portable_results", side_effect=lambda b, m, s: (m, set())), \
             patch.object(rn, "_execute_command", side_effect=fake_exec), \
             patch.object(rn, "do_review_gate", return_value=0), \
             patch.object(op, "handoff_if_available", return_value=None):
            outcome = auto._advance_impl(self.batch, bridge, stop_at="upload_gate", audio_worker=fake_worker)

        self.assertEqual(calls, ["review", "audio", "review"], msg=f"outcome={outcome}")
        self.assertEqual(outcome["failed_stages"], [])

    def test_p0_3_nl_cache_key_includes_targets_prompt_digest_generation_and_restart_cleans_it(self):
        base_part = {
            "unit_ids": ["q-2"],
            "targets": ["q-2"],
            "prompt_digest": "pd-1",
            "generation_nonce": "gen-1",
        }
        hashes = {"01_source/正文.txt": "h1", "02_audio/full-transcript.json": "a1"}
        k0 = review_rounds._nonlistening_cache_path("sample", base_part, hashes)
        k_audio_changed = review_rounds._nonlistening_cache_path(
            "sample", base_part, {"01_source/正文.txt": "h1", "02_audio/full-transcript.json": "a2"}
        )
        k_targets = review_rounds._nonlistening_cache_path(
            "sample", {**base_part, "targets": [{"unit_id": "q-2", "number": "2"}]}, hashes
        )
        k_prompt = review_rounds._nonlistening_cache_path(
            "sample", {**base_part, "prompt_digest": "pd-2"}, hashes
        )
        k_gen = review_rounds._nonlistening_cache_path(
            "sample", {**base_part, "generation_nonce": "gen-2"}, hashes
        )
        self.assertEqual(k0, k_audio_changed)
        self.assertNotEqual(k0, k_targets)
        self.assertNotEqual(k0, k_prompt)
        self.assertNotEqual(k0, k_gen)

        nl_file = self._put("workflow/review-pending-nl/nl-deadbeef.json", {"status": "completed"})
        self.assertTrue(nl_file.is_file())
        with patch.object(review_rounds.review, "prepare_round_plan", return_value={"input_binding": "b1", "units": ["q-1"], "sample": []}):
            res = review_rounds.restart_rounds(self.batch)
        self.assertFalse(nl_file.is_file())
        archived = Path(res["archive_path"]) / "review-pending-nl" / "nl-deadbeef.json"
        self.assertTrue(archived.is_file())

    def test_p0_4_and_p1_5_serial_audio_pending_and_nonlistening_failure_not_swallowed(self):
        self._put("01_source/正文.txt", "1. hi")
        manifest = mf.new_manifest("test-p04-p15", self.batch, {"source": {"path": str(self.batch / "01_source/正文.txt"), "sha256": "x"}}, {})
        manifest["stages"]["audio"] = {"status": mf.RUNNING}
        self._put("workflow/manifest.json", manifest)

        tid_nl = "round-" + "a" * 32
        tid_listen = "round-" + "b" * 32
        fake_group = {
            "action": "exported",
            "round_id": "labels",
            "parts": [
                {
                    "task_id": tid_nl,
                    "round_id": "labels",
                    "part_index": 1,
                    "part_count": 2,
                    "has_listening": False,
                    "unit_ids": ["q-2"],
                    "targets": ["q-2"],
                    "input_hashes": {"01_source/正文.txt": "h1"},
                    "prompt_digest": "pd1",
                    "generation_nonce": None,
                    "prompt_chars": 100,
                },
                {
                    "task_id": tid_listen,
                    "round_id": "labels",
                    "part_index": 2,
                    "part_count": 2,
                    "has_listening": True,
                    "unit_ids": ["q-1"],
                    "targets": ["q-1"],
                    "input_hashes": {"01_source/正文.txt": "h1"},
                    "prompt_digest": "pd1",
                    "generation_nonce": None,
                    "prompt_chars": 100,
                },
            ],
        }

        def fake_export_group(batch_dir, **kwargs):
            if kwargs.get("only_round") == "labels":
                return fake_group
            return {"action": "complete", "completed": False}

        def fake_run_ok(batch_dir, parts, config, workers, retries):
            self.assertEqual(workers, 1)
            self.assertEqual([p["task_id"] for p in parts], [tid_nl])
            review_rounds._save_pending(Path(batch_dir), tid_nl, {"status": "completed", "artifacts": {"review_round": {"checks": []}}, "notes": [], "findings": []})
            return review_rounds._PartOutcomes(), False

        with patch.object(review_rounds, "_effective_round_plan", return_value={"input_binding": "b1", "units": ["q-1", "q-2"], "sample": []}), \
             patch.object(review_rounds, "_purge_stale_pending", return_value=0), \
             patch.object(review_rounds, "export_group", side_effect=fake_export_group), \
             patch.object(review_rounds, "_run_parts_parallel", side_effect=fake_run_ok), \
             patch.object(review_rounds, "write_summary", return_value={"ok": True}):
            res_ok = review_rounds._run(self.batch, config={"review_part_parallel": 1})
        self.assertEqual(res_ok["action"], "waiting_audio")
        self.assertTrue(review_rounds._has_nonlistening_cache(self.batch))

        review_rounds._clear_pending(self.batch, tid_nl)
        for f in (self.batch / "workflow/review-pending-nl").glob("nl-*.json"):
            f.unlink()

        def fake_run_fail(batch_dir, parts, config, workers, retries):
            out = review_rounds._PartOutcomes()
            out[tid_nl] = "invalid json response"
            out.error_codes[tid_nl] = "response"
            return out, False

        with patch.object(review_rounds, "_effective_round_plan", return_value={"input_binding": "b1", "units": ["q-1", "q-2"], "sample": []}), \
             patch.object(review_rounds, "_purge_stale_pending", return_value=0), \
             patch.object(review_rounds, "export_group", side_effect=fake_export_group), \
             patch.object(review_rounds, "_run_parts_parallel", side_effect=fake_run_fail), \
             patch.object(review_rounds, "write_summary", return_value={"ok": False}):
            res_fail = review_rounds._run(self.batch, config={"review_part_parallel": 2})
        self.assertEqual(res_fail["action"], "blocked")
        self.assertIn("labels", res_fail.get("failures", {}))
        self.assertEqual(res_fail["failures"]["labels"]["error_code"], "response")

    def test_p1_6_operations_filters_logs_by_stage_and_uses_review_stale_threshold(self):
        self._put("01_source/正文.txt", "1. hi")
        manifest = mf.new_manifest("test-op", self.batch, {"source": {"path": str(self.batch / "01_source/正文.txt"), "sha256": "x"}}, {})
        started_400s = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 400))
        manifest["stages"]["review"] = {"status": mf.RUNNING, "started_at": started_400s}
        self._put("workflow/manifest.json", manifest)

        self._put("workflow/logs/audio.log", "audio noise")
        self.assertFalse(op._recent_stage_activity(self.batch, "review"))
        self._put("workflow/review-pending-nl/nl-1234.json", "{}")
        self.assertTrue(op._recent_stage_activity(self.batch, "review"))
        (self.batch / "workflow/review-pending-nl/nl-1234.json").unlink()

        with patch.object(op, "_execution_busy", return_value=True):
            stage_id, _ = op._stale_running_candidate(self.batch, manifest)
            self.assertIsNone(stage_id)

        manifest["stages"]["review"] = {"status": mf.PENDING}
        manifest["stages"]["types"] = {"status": mf.RUNNING, "started_at": started_400s}
        self._put("workflow/manifest.json", manifest)
        with patch.object(op, "_execution_busy", return_value=True):
            stage_id, _ = op._stale_running_candidate(self.batch, manifest)
            self.assertEqual(stage_id, "types")

    def test_p1_7_types_direct_tight_guards_and_knowledge_direct_group_subquestions(self):
        from workflow import types_direct

        self._put("01_source/正文.txt", "Ⅰ.短对话理解\n1. [IMG:1] A. cat B. dog C. bird\n2. A. [IMG:2] B. [IMG:3] C. [IMG:4]\nⅡ.书面表达\n3. 写一篇短文\n")
        items = [
            {"unit_id": "q-1", "unit_kind": "standalone", "numbers": [1], "type": "听力短对话理解",
             "stem": "[IMG:1] What does the man like?", "options": ["A. cat", "B. dog", "C. bird"]},
            {"unit_id": "q-2", "unit_kind": "standalone", "numbers": [2], "type": "听力短对话理解",
             "stem": "Which picture?", "options": ["A. [IMG:2]", "B. [IMG:3]", "C. [IMG:4]"]},
            {"unit_id": "q-3", "unit_kind": "standalone", "numbers": [3], "type": "书面表达",
             "stem": "1. 介绍你的周末计划\n2. 说明你的理由", "material": "根据下面的图表写一篇短文。", "options": []},
            {"unit_id": "q-4", "unit_kind": "standalone", "numbers": [4], "type": "书面表达",
             "stem": "1. 介绍你的周末计划\n2. 说明你的理由", "material": "", "options": [], "attachments": [{"kind": "doc", "path": "x"}]},
            {"unit_id": "q-5", "unit_kind": "standalone", "numbers": [5], "type": "书面表达",
             "stem": "1. 介绍你的周末计划\n2. 说明你的理由", "material": "", "options": []},
        ]
        self._put("01_source/structure.confirmed.json", {"units": items})
        self._put("06_batch/question-draft.json", {"items": items})
        classified = types_direct.classify_units(self.batch, items)
        self.assertNotIn("q-1", classified)
        self.assertEqual(classified["q-2"]["subtypes"], ["选图片"])
        self.assertNotIn("q-3", classified)
        self.assertNotIn("q-4", classified)
        self.assertEqual(classified["q-5"]["subtypes"], ["列中文要点"])

        group_units = [{"unit_id": "g-1", "unit_kind": "group", "numbers": [1, 2], "type": "听力关键词语选择"}]
        self._put("03_types/types.json", {
            "items": [{"unit_id": "g-1", "type": "听力关键词语选择", "subtypes": [], "words": 10, "contentComplete": True, "evidence": ["正文 L1"]}]
        })
        direct_k = knowledge_direct.tag_units(self.batch, group_units)
        self.assertEqual(direct_k["g-1"]["subquestions"], [{"number": "1", "knowledge": []}, {"number": "2", "knowledge": []}])

    def test_p1_8_partition_contexts_keeps_audio_when_confirmed_type_missing_and_strips_when_known_non_listening(self):
        ctx = {
            "confirmed_units_with_stable_ids": [
                {"unit_id": "q-1", "unit_kind": "standalone", "numbers": [1]},
                {"unit_id": "q-2", "unit_kind": "standalone", "numbers": [2], "type": "单项填空"},
                {"unit_id": "q-3", "unit_kind": "standalone", "numbers": [3], "type": "听力短对话理解"},
            ],
            "01_source/正文.txt": "1. A. a B. b C. c\n2. She ___.\n3. A. x B. y C. z\n",
            "03_types/types.json": {
                "items": [
                    {"unit_id": "q-1", "type": "单项填空", "subtypes": ["三个选项"]},
                    {"unit_id": "q-2", "type": "单项填空", "subtypes": ["四个选项"]},
                    {"unit_id": "q-3", "type": "听力短对话理解", "subtypes": ["选文字"]},
                ]
            },
            "02_audio/full-transcript.json": {"text": "hello", "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}]},
            "references/knowledge-catalog.json": {"points": [{"path": "语音/基本读音"}]},
        }
        base = {
            "prompt": "Header\nBEGIN SUPPLIED CONTENT (JSON)\n" + json.dumps(ctx, ensure_ascii=False) + "\nEND SUPPLIED CONTENT\n"
        }
        parts = review_rounds._partition_contexts(base, 500000, max_units=1, round_id="labels", section_types=None)
        self.assertEqual(len(parts), 3)
        by_uid = {}
        for p in parts:
            raw_c = p["context"]
            by_uid[p["unit_ids"][0]] = json.loads(raw_c[raw_c.find("{"):])
        # q-1 lacks confirmed type -> must keep transcript (never strip on model tag alone).
        self.assertIn("02_audio/full-transcript.json", by_uid["q-1"])
        # q-2 has confirmed non-listening type -> strips transcript.
        self.assertNotIn("02_audio/full-transcript.json", by_uid["q-2"])
        # q-3 has confirmed listening type -> keeps transcript.
        self.assertIn("02_audio/full-transcript.json", by_uid["q-3"])

    def test_review_inputs_ready_allows_missing_audio_map_only_when_audio_pending(self):
        from workflow import agent

        self._put("01_source/正文.txt", "1. She ___ a book. A. reads B. read C. reading D. to read")
        self._put("attachments/audio.mp3", "fake")
        manifest = mf.new_manifest(
            "test-pending-audio-collect",
            self.batch,
            {
                "source": {"path": str(self.batch / "01_source/正文.txt"), "sha256": "x", "exists": True},
                "audio": {"path": str(self.batch / "attachments/audio.mp3"), "sha256": "y", "exists": True},
            },
            {},
        )
        manifest["stages"]["validate"] = {"status": mf.COMPLETED}
        manifest["stages"]["audio"] = {"status": mf.RUNNING}
        self._put("workflow/manifest.json", manifest)

        _, _, missing_strict, _ = agent_tasks._collect(self.batch, "review", allow_pending_audio=False)
        self.assertTrue(any("02_audio/audio_map.json" in row for row in missing_strict))

        _, _, missing_pending, _ = agent_tasks._collect(self.batch, "review", allow_pending_audio=True)
        self.assertFalse(any("02_audio/audio_map.json" in row for row in missing_pending))

        self.assertFalse(agent._review_inputs_ready(self.batch, allow_pending_audio=False))

    def test_scope_knowledge_rules_and_prefix_ordering(self):
        from workflow import review
        rules_path = Path(review.__file__).resolve().parents[1] / "skills-reference/knowledge-matching-rules.md"
        raw = rules_path.read_text(encoding="utf-8")
        # 单词拼写 + 完成句子: keeps 词汇/语法/语音 + 四步流程 + 边界 + 踩坑清单, drops 主题/语篇体裁/设题角度/语用/校验脚本.
        scoped = review.scope_knowledge_rules(raw, ["单词拼写", "完成句子"])
        self.assertIn("## 一、四步流程", scoped)
        self.assertIn("## 四、词汇", scoped)
        self.assertIn("## 五、语法", scoped)
        self.assertIn("## 十一、真踩过的错", scoped)
        self.assertNotIn("## 二、主题", scoped)
        self.assertNotIn("## 六、语篇体裁", scoped)
        self.assertNotIn("## 七、设题角度", scoped)
        self.assertNotIn("## 十二、校验脚本", scoped)
        self.assertLess(len(scoped), len(raw) * 0.6)

        # 阅读理解: keeps 语篇体裁/主题/设题角度(仅阅读), drops 听力角度与语音/语法/词汇.
        reading = review.scope_knowledge_rules(raw, ["阅读理解"])
        self.assertIn("## 二、主题", reading)
        self.assertIn("## 六、语篇体裁", reading)
        self.assertIn("## 七、设题角度", reading)
        self.assertNotIn("**听力**（`设题角度/听力/*`）", reading)
        self.assertNotIn("## 五、语法", reading)

        # Fallback on unknown type keeps all business sections I-XI but still drops CLI scripts.
        fb = review.scope_knowledge_rules(raw, ["未知题型"])
        self.assertIn("## 二、主题", fb)
        self.assertIn("## 五、语法", fb)
        self.assertNotIn("## 十二、校验脚本", fb)

        # Prefix ordering puts static rules/catalogs before per-shard dynamic question data.
        ordered = review.order_prompt_context({
            "01_source/正文.txt": "body",
            "06_batch/batch_meta.json": {"items": []},
            "references/knowledge-catalog.json": {"paths": []},
            "skills-reference/knowledge-matching-rules.md": scoped,
        })
        keys = list(ordered.keys())
        self.assertLess(keys.index("skills-reference/knowledge-matching-rules.md"), keys.index("01_source/正文.txt"))
        self.assertLess(keys.index("references/knowledge-catalog.json"), keys.index("06_batch/batch_meta.json"))

    def test_operations_incidents_skips_completed_idle_batches_and_scans_active(self):
        from workflow import contracts, manifest as mf, operations, sync_practice
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            idle = root / "batch-idle-done"
            (idle / "workflow").mkdir(parents=True)
            (idle / "01_source").mkdir(parents=True)
            (idle / "06_batch").mkdir(parents=True)
            (idle / "06_batch/batch.base.json").write_text("{}", encoding="utf-8")
            (idle / "01_source/structure.confirmed.json").write_text(
                json.dumps({"units": [{"unit_id": "u1", "numbers": [1], "unit_kind": "standalone", "stem": "1. Hi"}]}),
                encoding="utf-8",
            )
            m_idle = {"stages": {}}
            for spec in contracts.STAGES:
                if spec.id in ("upload_gate", "upload", "publish_gate"):
                    m_idle["stages"][spec.id] = {"status": mf.PENDING}
                elif spec.id == "review_gate":
                    m_idle["stages"][spec.id] = {
                        "status": mf.NEEDS_REVIEW, "execution_completed": True, "continue_unqualified": True
                    }
                else:
                    m_idle["stages"][spec.id] = {"status": mf.COMPLETED}
            mf.save(idle, m_idle)

            active = root / "batch-active-failed"
            (active / "workflow").mkdir(parents=True)
            m_active = {"stages": {spec.id: {"status": mf.PENDING} for spec in contracts.STAGES}}
            m_active["stages"]["assemble"] = {"status": mf.FAILED}
            mf.save(active, m_active)

            self.assertFalse(operations._folder_needs_incident_scan(idle))
            self.assertTrue(operations._folder_needs_incident_scan(active))

            # Even if idle batch gets a pending incident or missing structure.confirmed.json, it MUST be scanned.
            (idle / "workflow/operations.json").write_text(
                json.dumps({"identity": operations._identity(idle),
                            "incident": {"incident_id": "i1", "status": "queued"}}), encoding="utf-8"
            )
            self.assertTrue(operations._folder_needs_incident_scan(idle))
            (idle / "workflow/operations.json").unlink()
            self.assertFalse(operations._folder_needs_incident_scan(idle))

            (idle / "01_source/structure.confirmed.json").unlink()
            self.assertTrue(operations._folder_needs_incident_scan(idle))

            # heading_candidates cache returns isolated dicts so caller mutation cannot corrupt cache.
            sample_text = "Unit 1\n**Ⅰ. 根据首字母及汉语提示补全单词**\n1. apple\n"
            first = sync_practice.heading_candidates(sample_text)
            self.assertTrue(first)
            first[0]["title"] = "MUTATED"
            second = sync_practice.heading_candidates(sample_text)
            self.assertNotEqual(second[0]["title"], "MUTATED")


if __name__ == "__main__":
    unittest.main()
