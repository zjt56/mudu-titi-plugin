"""Offline transport regressions: local mock processes only; no credentials/models."""
import contextlib
from datetime import datetime
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from workflow import agent_tasks, local_agent as agent, unit_tasks


RESPONSE = {"status": "completed", "artifacts": {}, "notes": [], "findings": []}
BRIDGE = Path(__file__).resolve().parents[1] / "scripts" / "bin" / "wf-agent.sh"


class FailureClassificationTest(unittest.TestCase):
    def test_ordinary_numbers_and_quota_words_are_not_transport_errors(self):
        for text in ("question 429", "QUOTA", "RATE_LIMIT", "quota exceeded in question text",
                     'payload {"artifacts":{"code":"quota"}}',
                     'output {"notes":["HTTP 429 is an example"]}',
                     "diagnostic-id=42991", "EPERM opening credentials"):
            with self.subTest(text=text):
                self.assertEqual(agent.classify_failure(text), "exit")

    def test_structured_and_explicit_http_errors(self):
        cases = {
            '{"error":{"code":429}}': "rate_limit",
            'provider failure:\n{\n  "error": {\n    "code": 429\n  }\n}': "rate_limit",
            '{"error":{"code":"rate_limit_exceeded"}}': "rate_limit",
            '{"error":{"code":"insufficient_quota"},"status":429}': "quota",
            'dsh: QUOTA: provider API error (429): DAILY_LIMIT_EXCEEDED': "quota",
            "HTTP/1.1 429 Too Many Requests": "rate_limit",
            "HTTP status: 401": "authentication",
            '{"status_code":403}': "authentication",
            "API error (402): no balance": "quota",
            '{"error":{"type":"invalid_request_error"},"status":400}': "request",
            "HTTP 503 Service Unavailable": "exit",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(agent.classify_failure(text), expected)

    def test_final_wrapper_summary_preserves_final_and_first_failure_separately(self):
        text = ('HTTP 429 first\nHTTP 401 second\nwf-agent-result: '
                '{"error_code":"authentication","http_status":401,"first_error_code":"rate_limit"}')
        self.assertEqual(agent.failure_details(text), ("authentication", 401))

    def test_review_header_after_long_instructions_and_before_data_only(self):
        self.assertTrue(agent.is_review_prompt("long instructions" * 300 + "\nROUND: labels\n"))
        self.assertTrue(agent.is_review_prompt("long instructions" * 300 + "\nROUND: sample\n"))
        self.assertTrue(agent.is_review_prompt("Stage: review\n"))
        self.assertFalse(agent.is_review_prompt("Stage: types\nBEGIN SUPPLIED CONTENT\nROUND: labels\n"))


class ShellBridgeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        executable = self.root / ".local" / "bin" / "dsh"
        executable.parent.mkdir(parents=True)
        executable.write_text(
            "#!" + sys.executable + "\n"
            "import json, os, pathlib, sys\n"
            "root=pathlib.Path(os.environ['MOCK_ROOT'])\n"
            "calls=root/'calls.json'\n"
            "history=json.loads(calls.read_text()) if calls.exists() else []\n"
            "steps=json.loads((root/'scenario.json').read_text())\n"
            "step=steps[len(history)]\n"
            "payload=sys.stdin.buffer.read()\n"
            "history.append({'profile':sys.argv[2],'prompt_bytes':len(payload)})\n"
            "calls.write_text(json.dumps(history))\n"
            "sys.stdout.buffer.write(step['stdout'].encode())\n"
            "sys.stderr.buffer.write(step['stderr'].encode())\n"
            "raise SystemExit(step['exit'])\n", encoding="utf-8")
        executable.chmod(0o700)
        self.env = {**os.environ, "HOME": str(self.root), "MOCK_ROOT": str(self.root),
                    "DSH_HOME": str(self.root / "never-read-credentials"),
                    "DEEPSEEK_API_KEY": "mock-only-private-key"}

    def call(self, prompt, steps):
        (self.root / "scenario.json").write_text(json.dumps(steps), encoding="utf-8")
        (self.root / "prompt.txt").write_text(prompt, encoding="utf-8")
        result = subprocess.run(["sh", str(BRIDGE), str(self.root / "prompt.txt"),
                                 str(self.root / "response.json")], env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        self.calls = json.loads((self.root / "calls.json").read_text())
        self.evidence = next(self.root.glob("wf-agent-attempts-*"))
        return result

    @staticmethod
    def step(stdout=None, stderr="", exit=0):
        return {"stdout": json.dumps(RESPONSE) if stdout is None else stdout,
                "stderr": stderr, "exit": exit}

    def assert_metadata(self, index, code):
        directory = self.evidence / ("attempt-%02d" % index)
        meta = json.loads((directory / "attempt.json").read_text())
        self.assertEqual(meta["error_category"], code)
        self.assertGreaterEqual(meta["duration"], 0)
        self.assertLessEqual(datetime.fromisoformat(meta["wall_start"]), datetime.fromisoformat(meta["wall_end"]))
        self.assertEqual(meta["prompt_bytes"], len((self.root / "prompt.txt").read_bytes()))
        self.assertNotIn("mock-only-private-key", json.dumps(meta))
        return directory, meta

    def test_long_review_header_uses_pro_and_preserves_exact_model_bytes(self):
        raw = '  {"status":"needs_review","artifacts":{"review_round":{"checks":[]}},"notes":["待核"],"findings":[]}\n'
        result = self.call("x" * 4000 + "\nROUND: labels\n", [self.step(raw, "startup\n")])
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.calls[0]["profile"], "wf-deepseek")
        self.assertEqual(result.stdout, b"")
        self.assertEqual((self.root / "response.json").read_bytes(), raw.encode())
        directory, _ = self.assert_metadata(1, None)
        self.assertEqual((directory / "stdout.log").read_bytes(), raw.encode())
        self.assertEqual((directory / "stderr.log").read_text(), "startup\n")

    def test_sample_review_429_never_silently_downgrades(self):
        result = self.call("x" * 4000 + "\nROUND: sample\n", [self.step(stderr="HTTP 429 Too Many Requests", exit=9)])
        self.assertEqual(result.returncode, 9)
        self.assertEqual([c["profile"] for c in self.calls], ["wf-deepseek"])
        self.assertFalse((self.root / "response.json").exists())
        self.assert_metadata(1, "rate_limit")

    def test_non_review_explicit_429_fallback_preserves_every_raw_stream(self):
        result = self.call("Stage: types\n", [
            self.step(stdout="first stdout", stderr="HTTP 429 first failure", exit=7),
            self.step(stdout="second stdout", stderr="HTTP 401 second failure mock-only-private-key", exit=8)])
        self.assertEqual(result.returncode, 8)
        self.assertEqual([c["profile"] for c in self.calls], ["wf-deepseek-fast", "wf-deepseek"])
        self.assertIn(b"first failure", result.stderr)
        self.assertIn(b"second failure", result.stderr)
        first, _ = self.assert_metadata(1, "rate_limit")
        second, _ = self.assert_metadata(2, "authentication")
        self.assertEqual((first / "stdout.log").read_text(), "first stdout")
        self.assertEqual((second / "stdout.log").read_text(), "second stdout")
        summary = json.loads((self.evidence / "result.json").read_text())
        self.assertEqual(summary["first_error_code"], "rate_limit")
        self.assertEqual(summary["error_code"], "authentication")
        self.assertNotIn("mock-only-private-key", json.dumps(summary))

    def test_429_fallback_success_retains_first_failure_and_exact_response(self):
        result = self.call("Stage: knowledge\n", [
            self.step(stdout="failure output", stderr='{"error":{"code":429}}', exit=5),
            self.step()])
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(len(self.calls), 2)
        self.assert_metadata(1, "rate_limit")
        self.assert_metadata(2, None)
        self.assertEqual((self.root / "response.json").read_text(), json.dumps(RESPONSE))

    def test_auth_quota_format_and_bare_substrings_do_not_retry(self):
        cases = [("HTTP 401", 1, "invalid", "authentication"),
                 ('{"error":{"code":"insufficient_quota"},"status":429}', 2, "bad", "quota"),
                 ("quota QUOTA 429 RATE_LIMIT", 3, "bad", "exit"),
                 ("", 0, '{"status":"completed","artifacts":{}', "response")]
        for index, (stderr, exit, stdout, code) in enumerate(cases):
            # Each invocation has its own fake home and isolated output paths.
            with self.subTest(code=code):
                folder = self.root / ("case-%d" % index)
                folder.mkdir()
                (self.root / "scenario.json").write_text(json.dumps([self.step(stdout, stderr, exit)]))
                calls_path = self.root / "calls.json"
                if calls_path.exists():
                    calls_path.unlink()
                (folder / "prompt.txt").write_text("Stage: types\n")
                result = subprocess.run(["sh", str(BRIDGE), str(folder / "prompt.txt"), str(folder / "response.json")],
                                        env=self.env, capture_output=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(json.loads(calls_path.read_text())), 1)
                directory = next(folder.glob("wf-agent-attempts-*"))
                meta = json.loads((directory / "attempt-01" / "attempt.json").read_text())
                self.assertEqual(meta["error_code"], code)
                if code == "response":
                    self.assertEqual(meta["exit_code"], 0)
                    self.assertEqual(meta["bridge_exit_code"], 1)


class LocalInvocationEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = {"provider": "dsh", "executable": sys.executable, "timeout_seconds": 3}

    def spawn(self, stdout, stderr="", rc=0):
        def run(command, **kwargs):
            kwargs["stdout"].write(stdout.encode())
            kwargs["stderr"].write(stderr.encode())
            return MagicMock(returncode=rc)
        return run

    def test_timing_and_sanitized_metadata_for_success_and_failure(self):
        for index, code in enumerate((None, "authentication")):
            run_dir = self.root / str(index)
            with patch.object(agent.subprocess, "Popen", side_effect=self.spawn(
                    json.dumps(RESPONSE), "HTTP 401 Bearer MOCK-SECRET" if code else "", 7 if code else 0)) as spawn:
                if code:
                    with self.assertRaises(agent.AgentError):
                        agent.invoke("中文", run_dir, self.config)
                else:
                    agent.invoke("中文", run_dir, self.config)
            self.assertEqual(spawn.call_count, 1)
            meta = json.loads((run_dir / "attempt.json").read_text())
            self.assertEqual(meta["error_category"], code)
            self.assertEqual(meta["prompt_bytes"], len((agent.INSTRUCTIONS + "中文").encode()))
            self.assertLessEqual(datetime.fromisoformat(meta["wall_start"]), datetime.fromisoformat(meta["wall_end"]))
            self.assertGreaterEqual(meta["duration"], 0)
            self.assertGreaterEqual(meta["total_duration"], meta["duration"])
            self.assertNotIn("MOCK-SECRET", json.dumps(meta))

    def test_review_json_is_not_repaired_or_hoisted(self):
        raw = '{"status":"completed","artifacts":{"review_round":{"checks":[],"findings":["keep"]}}}'
        with patch.object(agent.subprocess, "Popen", side_effect=self.spawn(raw)):
            result = agent.invoke("x" * 3000 + "\nROUND: labels\n", self.root / "strict", self.config)
        self.assertEqual(result["response"], json.loads(raw))
        self.assertEqual(json.loads((self.root / "strict" / "response.json").read_text()), json.loads(raw))
        with patch.object(agent.subprocess, "Popen", side_effect=self.spawn(raw[:-1])) as spawn:
            with self.assertRaises(agent.AgentError) as caught:
                agent.invoke("ROUND: sample\n", self.root / "truncated", self.config)
        self.assertEqual(caught.exception.code, "response")
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual((self.root / "truncated" / "stdout.log").read_text(), raw[:-1])
        self.assertFalse((self.root / "truncated" / "response.json").exists())

    @unittest.skipIf(os.name == "nt", "shared flock is POSIX only")
    def test_main_parts_really_overlap_while_import_remains_exclusive(self):
        barrier = threading.Barrier(2)
        intervals = []
        mutex = threading.Lock()

        def spawn(command, **kwargs):
            kwargs["stdout"].write(json.dumps(RESPONSE).encode())
            process = MagicMock(returncode=0)
            def communicate(**ignored):
                barrier.wait(timeout=2)  # Deadlocks/fails with the old exclusive invocation lock.
                with mutex:
                    intervals.append(threading.current_thread().name)
            process.communicate.side_effect = communicate
            return process

        def accept(*args):
            self.assertEqual(len(intervals), 2)
            with self.assertRaises(agent.AgentError) as caught:
                with agent._agent_lock(0.02, shared=True):
                    pass
            self.assertEqual(caught.exception.code, "lock_timeout")
            return {"completed": True}

        task = {"prompt": "parent", "input_hashes": {}}
        parts = [{"prompt": "part one", "unit_ids": ["u1"]},
                 {"prompt": "part two", "unit_ids": ["u2"]}]
        config = {**self.config, "part_concurrency": 2, "max_input_chars": 180000,
                  "unit_split": True, "retries": 1}
        with patch.object(agent, "load_config", return_value=config), \
             patch.object(agent_tasks, "build_task", side_effect=ValueError("exceeding max_input_chars")), \
             patch.object(unit_tasks, "build_parts", return_value=(task, parts)), \
             patch.object(unit_tasks, "validate_part"), \
             patch.object(unit_tasks, "merge", return_value=RESPONSE), \
             patch.object(agent_tasks, "accept_result", side_effect=accept) as importer, \
             patch.object(agent.subprocess, "Popen", side_effect=spawn) as popen, \
             contextlib.redirect_stdout(io.StringIO()):
            result = agent.main(["--stage", "explanations", "--batch-dir", str(self.root / "batch")])
        self.assertEqual(result, 0)
        self.assertEqual(popen.call_count, 2)
        self.assertEqual(importer.call_count, 1)
        self.assertEqual(len(set(intervals)), 2)


if __name__ == "__main__":
    unittest.main()
