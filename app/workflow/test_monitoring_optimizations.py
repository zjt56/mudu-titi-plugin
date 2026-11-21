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


if __name__ == "__main__":
    unittest.main()
