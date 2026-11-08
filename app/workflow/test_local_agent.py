"""No model/API calls: provider contracts use mocks and a local fake CLI."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

from workflow import local_agent as agent


RESPONSE = {"status": "completed", "artifacts": {"example.json": {"value": "中文"}}}


class LocalAgentTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.config = {"provider": "dsh", "executable": sys.executable, "timeout_seconds": 1,
                       "max_input_chars": 180000}

    def process(self, *, stdout=None, stderr=None, final=None, returncode=0):
        def spawn(command, **kwargs):
            if stdout is not None:
                kwargs["stdout"].write(stdout.encode())
            if stderr is not None:
                kwargs["stderr"].write(stderr.encode())
            if final is not None:
                Path(command[command.index("--output-last-message") + 1]).write_text(final, encoding="utf-8")
            result = MagicMock()
            result.returncode = returncode
            result.pid = 987654
            result.communicate.return_value = (None, None)
            return result
        return spawn

    def test_config_defaults_relative_executable_and_precedence(self):
        path = self.root / "config.json"
        executable = self.root / "fake cli"
        executable.write_text("placeholder", encoding="utf-8")
        executable.chmod(0o700)
        path.write_text(json.dumps({"executable": executable.name}), encoding="utf-8")
        with patch.dict(os.environ, {"MUDU_AGENT_CONFIG": str(path)}):
            config = agent.load_config()
            self.assertEqual(config["executable"], str(executable))
            self.assertEqual(config["provider"], "dsh")
            self.assertEqual(config["timeout_seconds"], 900)
            self.assertEqual(config["max_input_chars"], 180000)
            self.assertEqual(config["assemble_part_units"], 8)
            self.assertEqual(config["review_part_units"], 15)
            with self.assertRaises(agent.AgentError):
                agent.load_config(self.root / "missing.json")

    def test_implicit_missing_config_and_path_lookup(self):
        with patch.object(agent, "__file__", str(self.root / "local_agent.py")), patch.dict(os.environ, {}, clear=True), patch.object(agent.shutil, "which", return_value=sys.executable):
            config = agent.load_config()
        self.assertEqual(config["executable"], sys.executable)

    def test_bad_config(self):
        for change in ({"provider": "other"}, {"model": "foo"}, {"timeout_seconds": 1501},
                       {"timeout_seconds": True}, {"timeout_seconds": float("nan")},
                       {"max_input_chars": 1.2}, {"max_input_chars": False},
                       {"executable": ""}, {"executable": str(self.root / "missing")},
                       {"assemble_part_units": 0}, {"assemble_part_units": True},
                        {"review_part_units": -1}, {"review_part_units": 1.5},
                        {"provider": "codex", "model": ""}):
            with self.subTest(change=change), self.assertRaises(agent.AgentError):
                agent._validate_config({**self.config, **change}, self.root)
        path = self.root / "broken.json"
        path.write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(agent.AgentError, "Cannot load"):
            agent.load_config(path)

    def test_parallel_opt_in_preserves_default_lock(self):
        with patch.object(agent.subprocess, "Popen", side_effect=self.process(stdout=json.dumps(RESPONSE))), \
             patch.object(agent, "_agent_lock", return_value=contextlib.nullcontext()) as lock:
            agent.invoke("task", self.root / "serial", self.config)
            lock.assert_called_once()
            lock.reset_mock()
            agent.invoke("task", self.root / "parallel", self.config, permit_parallel=True)
            lock.assert_called_once_with(1.0, shared=True)
            with self.assertRaises(agent.AgentError):
                agent.invoke("task", self.root / "invalid", self.config, permit_parallel="yes")

    @unittest.skipIf(os.name == "nt", "POSIX shared flock only")
    def test_shared_lock_excludes_legacy_calls(self):
        with agent._agent_lock(1, shared=True):
            with agent._agent_lock(1, shared=True):
                with self.assertRaisesRegex(agent.AgentError, "Timed out"):
                    with agent._agent_lock(0.05):
                        pass
        with agent._agent_lock(1):
            with self.assertRaisesRegex(agent.AgentError, "Timed out"):
                with agent._agent_lock(0.05, shared=True):
                    pass

    def test_dsh_arguments_stdin_logs_and_response(self):
        with patch.object(agent.subprocess, "Popen", side_effect=self.process(stdout=json.dumps(RESPONSE), stderr="startup diagnostic")) as spawn:
            result = agent.invoke("task data", self.root / "attempt", self.config)
        self.assertEqual(result["response"], RESPONSE)
        self.assertEqual(result["provider"], "dsh")
        self.assertEqual(spawn.call_args.args[0], [sys.executable, "headless", "-"])
        kwargs = spawn.call_args.kwargs
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["cwd"], str(self.root / "attempt"))
        self.assertEqual(kwargs["start_new_session"], os.name != "nt")
        self.assertEqual(Path(result["artifacts"]["input"]).read_text(), agent.INSTRUCTIONS + "task data")
        self.assertEqual(Path(result["artifacts"]["stderr"]).read_text(), "startup diagnostic")
        self.assertEqual(json.loads(Path(result["artifacts"]["response"]).read_text()), RESPONSE)
        self.assertEqual(json.loads(Path(result["artifacts"]["metadata"]).read_text())["status"], "completed")
        self.assertNotIn("final", result["artifacts"])

    def test_codex_final_only_and_no_stderr_parsing(self):
        settings = {**self.config, "provider": "codex", "model": "test-model"}
        process = MagicMock(returncode=0)
        def spawn(command, **kwargs):
            self.process(stdout="progress text, not JSON", stderr="Codex version\n", final="```json\n" + json.dumps(RESPONSE) + "\n```")(command, **kwargs)
            return process
        with patch.object(agent.subprocess, "Popen", side_effect=spawn) as popen:
            result = agent.invoke("task", self.root / "codex", settings)
        self.assertEqual(result["response"], RESPONSE)
        self.assertEqual(popen.call_args.args[0], [sys.executable, "-a", "never", "exec", "--sandbox", "read-only", "--skip-git-repo-check", "--color", "never", "--output-last-message", str(self.root / "codex" / "final.txt"), "-m", "test-model", "-"])
        sent = process.communicate.call_args.kwargs["input"].decode()
        self.assertTrue(sent.startswith(agent.INSTRUCTIONS))
        self.assertTrue(sent.endswith("task"))

    def test_command_stdin_stdout_and_literal_arguments(self):
        script = self.root / "sample agent with spaces.py"
        script.write_text(
            "import json, pathlib, sys\n"
            "text = sys.stdin.read()\n"
            "print(json.dumps({'status':'completed','artifacts':{'stdin':text,'argv':sys.argv[1:],'cwd':str(pathlib.Path.cwd())}}))\n",
            encoding="utf-8")
        literal = "; touch SHOULD_NOT_EXIST $(echo nope) `echo nope` & | > *"
        settings = {"provider": "command", "command": ["{python}", str(script), literal,
                    '{"key": 1}', "--prompt={prompt_file}", "{work_dir}"], "timeout_seconds": 2}
        attempt = self.root / "run with spaces ; $literal"
        result = agent.invoke("中文 task\n", attempt, settings)
        payload = result["response"]["artifacts"]
        self.assertEqual(payload["stdin"], agent.INSTRUCTIONS + "中文 task\n")
        self.assertEqual(Path(result["artifacts"]["input"]).read_bytes(), payload["stdin"].encode("utf-8"))
        self.assertEqual(payload["argv"], [literal, '{"key": 1}', "--prompt=" + str(attempt / "input.txt"), str(attempt)])
        self.assertEqual(payload["cwd"], str(attempt))
        self.assertNotIn("final", result["artifacts"])
        self.assertFalse((attempt / "SHOULD_NOT_EXIST").exists())

    def test_command_prompt_file_response_file_and_no_stdin(self):
        script = self.root / "file agent.py"
        script.write_text(
            "import json, pathlib, sys\n"
            "text = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')\n"
            "payload = {'status':'completed','artifacts':{'prompt':text,'stdin':sys.stdin.read()}}\n"
            "pathlib.Path(sys.argv[2]).write_text(json.dumps(payload), encoding='utf-8')\n"
            "print('progress is not the response')\n", encoding="utf-8")
        settings = {"provider": "command", "command": ["{python}", str(script), "{prompt_file}", "{response_file}"],
                    "input_mode": "none", "response_mode": "file", "timeout_seconds": 2}
        result = agent.invoke("file task", self.root / "file attempt", settings)
        self.assertEqual(result["response"]["artifacts"], {"prompt": agent.INSTRUCTIONS + "file task", "stdin": ""})
        self.assertEqual(Path(result["artifacts"]["stdout"]).read_text(), "progress is not the response\n")
        self.assertEqual(json.loads(Path(result["artifacts"]["final"]).read_text()), result["response"])

    def test_command_config_rejects_malformed_commands_and_templates(self):
        invalid = [None, "python -c anything", [], [123], [""], ["{work_dir}/agent"],
                   ["prefix{python}"], ["{python}", "{unknown}"], ["{python}", "{python}"],
                   ["{python}", "bad\x00argument"]]
        for command in invalid:
            with self.subTest(command=command), self.assertRaises(agent.AgentError) as caught:
                agent._validate_config({"provider": "command", "command": command}, self.root)
            self.assertEqual(caught.exception.code, "config")
        for modes in ({"response_mode": "other"}, {"input_mode": "other"},
                      {"response_mode": "file"}, {"input_mode": "none"}, {"model": "not-codex"}):
            with self.subTest(modes=modes), self.assertRaises(agent.AgentError):
                agent._validate_config({"provider": "command", "command": ["{python}"], **modes}, self.root)

    def test_command_relative_executable_path_lookup_and_check(self):
        executable = self.root / "my agent"
        executable.write_text("placeholder", encoding="utf-8")
        executable.chmod(0o700)
        path = self.root / "command.json"
        path.write_text(json.dumps({"provider": "command", "command": [executable.name, "{prompt_file}"]}))
        config = agent.load_config(path)
        self.assertEqual(config["command"][0], str(executable))
        self.assertEqual(config["input_mode"], "stdin")
        self.assertEqual(config["response_mode"], "stdout")
        with patch.object(agent.shutil, "which", return_value=sys.executable) as which:
            by_path = agent._validate_config({"provider": "command", "command": ["my-agent"]}, self.root)
        which.assert_called_once_with("my-agent")
        self.assertEqual(by_path["command"][0], sys.executable)
        with patch.object(agent.subprocess, "Popen") as spawn, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(agent.main(["--check", "--config", str(path)]), 0)
        spawn.assert_not_called()
        self.assertEqual(output.getvalue().strip(), str(executable))
        expected = [str(executable), str(self.root / "input.txt")]
        self.assertEqual(agent._command(config, self.root / "final.txt"), expected)

    def test_command_both_response_modes_use_strict_json(self):
        for mode in ("stdout", "file"):
            settings = {"provider": "command", "command": ["{python}", "{response_file}"], "response_mode": mode}
            def spawn(command, **kwargs):
                kwargs["stdout"].write(b'narrative {"status":"completed","artifacts":{}}')
                Path(command[1]).write_text('{"status":"completed","artifacts":{},"duplicate":1,"duplicate":2}')
                return MagicMock(returncode=0)
            with self.subTest(mode=mode), patch.object(agent.subprocess, "Popen", side_effect=spawn):
                with self.assertRaises(agent.AgentError) as caught:
                    agent.invoke("task", self.root / ("strict-" + mode), settings)
            self.assertEqual(caught.exception.code, "response")

    def test_json_rejects_surrounding_text_wrong_schema_and_duplicates(self):
        valid = json.dumps(RESPONSE)
        for text in ("prefix\n" + valid, valid + "\ntrailing", valid + valid,
                     "```json\n" + valid + "\n```\n```json\n{}\n```", "[]", "{}",
                     '{"status":"done","artifacts":{}}',
                     '{"status":"completed","artifacts":[]}',
                     '{"status":"completed","artifacts":{},"score":NaN}',
                     '{"status":"completed","artifacts":{},"score":1e999}',
                     '{"status":"completed","artifacts":{},"status":"needs_review"}'):
            with self.subTest(text=text), self.assertRaises(agent.AgentError):
                agent._parse_response(text)
        self.assertEqual(agent._parse_response("```JSON\n" + valid + "\n```"), RESPONSE)
        self.assertEqual(agent._parse_response("```\n" + valid + "\n```"), RESPONSE)

    def test_parse_response_repairs_missing_final_brace_and_hoists_notes(self):
        # 大结果里模型常见：根对象少一个右花括号，且把 notes/findings 写进文件对象。
        text = ('{"status":"completed","artifacts":{"04_knowledge/knowledge.json":'
                '{"items":[],"notes":["n"],"findings":[]}}')
        parsed = agent._parse_response(text)
        self.assertEqual(parsed["notes"], ["n"])
        self.assertEqual(parsed["findings"], [])
        self.assertEqual(parsed["artifacts"]["04_knowledge/knowledge.json"], {"items": []})

    def test_unit_merge_supports_explanations_and_knowledge_partitions(self):
        from workflow import unit_tasks
        parts = [{"unit_ids": ["u1"]}, {"unit_ids": ["u2"]}]
        explosions = {"status": "completed",
                      "artifacts": {"04_knowledge/explanations.json": {"items": [
                          {"unit_id": "u1", "explanation": "待补充", "articleSummary": "", "subquestions": [], "evidence": ["x"]}]}},
                      "notes": [], "findings": []}
        exp2 = {"status": "completed",
                "artifacts": {"04_knowledge/explanations.json": {"items": [
                    {"unit_id": "u2", "explanation": "待补充", "articleSummary": "", "subquestions": [], "evidence": ["y"]}]}},
                "notes": [], "findings": []}
        merged = unit_tasks.merge("explanations", parts, [explosions, exp2])
        self.assertEqual(sorted(i["unit_id"] for i in merged["artifacts"]["04_knowledge/explanations.json"]["items"]), ["u1", "u2"])
        kn1 = {"status": "completed",
               "artifacts": {"04_knowledge/knowledge.json": {"items": [
                   {"unit_id": "u1", "knowledge": [], "subquestions": [], "evidence": ["x"]}]}},
               "notes": [], "findings": []}
        kn2 = {"status": "completed",
               "artifacts": {"04_knowledge/knowledge.json": {"items": [
                   {"unit_id": "u2", "knowledge": [], "subquestions": [], "evidence": ["y"]}]}},
               "notes": [], "findings": []}
        merged = unit_tasks.merge("knowledge", parts, [kn1, kn2])
        self.assertEqual(sorted(i["unit_id"] for i in merged["artifacts"]["04_knowledge/knowledge.json"]["items"]), ["u1", "u2"])
        with self.assertRaises(ValueError):
            unit_tasks.merge("explanations", parts, [exp2, exp2])  # overlapping/duplicate ids
        with self.assertRaises(ValueError):
            unit_tasks.build_parts(self.root, "answers", 1000, max_units=5)

    def test_failure_is_classified_saved_and_never_retried(self):
        with patch.object(agent.subprocess, "Popen", side_effect=self.process(stdout="bad", stderr="helpful error", returncode=7)) as spawn:
            with self.assertRaises(agent.AgentError) as caught:
                agent.invoke("task", self.root / "failure", self.config)
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual(caught.exception.code, "exit")
        evidence = caught.exception.artifacts
        metadata = json.loads(Path(evidence["metadata"]).read_text())
        self.assertEqual(metadata["error_code"], "exit")
        self.assertEqual(metadata["exit_code"], 7)
        self.assertEqual(Path(evidence["stderr"]).read_text(), "helpful error")

    def test_router_prefill_capacity_429_is_transient_rate_limit(self):
        stderr = ('dsh: RATE_LIMIT: 429: {"type":"server_error",'
                  '"message":"Upstream request failed: [prefill_cap_refused] the cell is at capacity"}\n'
                  'wf-router: RATE_LIMIT: throttled')
        self.assertEqual(agent.failure_details(stderr), ('rate_limit', 429))

    def test_quota_failure_is_classified(self):
        stderr = 'dsh: QUOTA: magpie-responses API error (429): DAILY_LIMIT_EXCEEDED'
        with patch.object(agent.subprocess, "Popen", side_effect=self.process(stdout="", stderr=stderr, returncode=1)):
            with self.assertRaises(agent.AgentError) as caught:
                agent.invoke("task", self.root / "quota", self.config)
        self.assertEqual(caught.exception.code, "quota")
        metadata = json.loads(Path(caught.exception.artifacts["metadata"]).read_text())
        self.assertEqual(metadata["error_code"], "quota")

    def test_opt_in_candidate_cache_revalidates_and_avoids_second_call(self):
        batch = self.root / "batch"
        batch.mkdir()
        (batch / "source.txt").write_text("source", encoding="utf-8")
        digest = hashlib.sha256(b"source").hexdigest()
        parent = types.ModuleType("workflow.agent_tasks")
        parent.build_task = MagicMock(return_value={"prompt": "context", "input_hashes": {"source.txt": digest}})
        parent.accept_result = MagicMock(return_value={"completed": True})
        config = {**self.config, "candidate_cache": True}
        response = {"status": "completed", "artifacts": {"03_types/types.json": {"items": []}}, "notes": [], "findings": []}
        with patch.dict(sys.modules, {"workflow.agent_tasks": parent}), patch("workflow.agent_tasks", parent, create=True), patch.object(agent, "load_config", return_value=config), patch.object(agent, "invoke", return_value={"response": response, "artifacts": {}}) as invoke, contextlib.redirect_stdout(io.StringIO()) as output:
            args = ["--stage", "types", "--batch-dir", str(batch)]
            self.assertEqual(agent.main(args), 0)
            self.assertEqual(agent.main(args), 0)
        self.assertEqual(invoke.call_count, 1)
        self.assertTrue(json.loads(output.getvalue().splitlines()[-1])["cache_hit"])
        self.assertGreaterEqual(sum(bool(call.kwargs.get("validate_only")) for call in parent.accept_result.call_args_list), 2)

    def test_validation_failure_retries_once_with_feedback(self):
        parent = types.ModuleType("workflow.agent_tasks")
        parent.build_task = MagicMock(return_value={"prompt": "bundled context", "input_hashes": {}})
        parent.accept_result = MagicMock(side_effect=[ValueError("findings must be objects"), {"completed": True}])
        config = {**self.config, "retries": 1}
        response = {"status": "completed", "artifacts": {}}
        with patch.dict(sys.modules, {"workflow.agent_tasks": parent}), patch("workflow.agent_tasks", parent, create=True), patch.object(agent, "load_config", return_value=config), patch.object(agent, "invoke", return_value={"response": response, "artifacts": {}}) as invoke, contextlib.redirect_stdout(io.StringIO()):
            status = agent.main(["--stage", "structure", "--batch-dir", str(self.root / "batch")])
        self.assertEqual(status, 0)
        self.assertEqual(invoke.call_count, 2)
        self.assertIn("上一次返回未通过校验", invoke.call_args_list[1].args[0])
        self.assertIn("findings must be objects", invoke.call_args_list[1].args[0])

    def test_timeout_kills_group_and_drains_without_retry(self):
        process = MagicMock()
        process.pid = 987654
        process.communicate.side_effect = [subprocess.TimeoutExpired("fake", 1), (None, None)]
        with patch.object(agent.subprocess, "Popen", return_value=process) as spawn, patch.object(agent.os, "killpg", create=True) as killpg:
            with self.assertRaises(agent.AgentError) as caught:
                agent.invoke("task", self.root / "timeout", self.config)
        self.assertEqual(caught.exception.code, "timeout")
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual(process.communicate.call_count, 2)
        if os.name == "nt":
            process.kill.assert_called_once()
        else:
            killpg.assert_called_once_with(process.pid, signal.SIGKILL)
        self.assertEqual(json.loads((self.root / "timeout" / "attempt.json").read_text())["error_code"], "timeout")

    def test_existing_output_and_attempt_are_preserved(self):
        run_dir = self.root / "attempt"
        with patch.object(agent.subprocess, "Popen", side_effect=self.process(stdout=json.dumps(RESPONSE))) as spawn:
            agent.invoke("first prompt", run_dir, self.config)
            before = {p.name: p.read_bytes() for p in run_dir.iterdir()}
            with self.assertRaises(agent.AgentError) as caught:
                agent.invoke("second prompt", run_dir, self.config)
        self.assertEqual(caught.exception.code, "output_exists")
        self.assertEqual(before, {p.name: p.read_bytes() for p in run_dir.iterdir()})
        self.assertEqual(spawn.call_count, 1)
        other = self.root / "preexisting"
        other.mkdir()
        (other / "stdout.log").write_text("keep")
        with self.assertRaises(agent.AgentError):
            agent.invoke("task", other, self.config)
        self.assertEqual((other / "stdout.log").read_text(), "keep")

    def test_prompt_and_final_limits(self):
        with patch.object(agent.subprocess, "Popen") as spawn:
            with self.assertRaises(agent.AgentError) as caught:
                agent.invoke("too long", self.root / "input-limit", {**self.config, "max_input_chars": 2})
            self.assertEqual(caught.exception.code, "input_limit")
            spawn.assert_not_called()
        with patch.object(agent, "MAX_FINAL_BYTES", 8), patch.object(agent.subprocess, "Popen", side_effect=self.process(stdout=json.dumps(RESPONSE))):
            with self.assertRaises(agent.AgentError) as caught:
                agent.invoke("task", self.root / "output-limit", self.config)
            self.assertEqual(caught.exception.code, "output_limit")

    def test_spawn_and_malformed_output_errors_persist(self):
        for index, effect, expected in ((1, OSError("not executable"), "spawn"),
                                        (2, self.process(stdout="narrative {}"), "response")):
            with self.subTest(expected=expected), patch.object(agent.subprocess, "Popen", side_effect=effect):
                with self.assertRaises(agent.AgentError) as caught:
                    agent.invoke("task", self.root / str(index), self.config)
                self.assertEqual(caught.exception.code, expected)
                self.assertTrue(Path(caught.exception.artifacts["metadata"]).is_file())

    def test_check_does_not_call_provider(self):
        with patch.object(agent, "load_config", return_value=self.config), patch.object(agent.subprocess, "Popen") as spawn, contextlib.redirect_stdout(io.StringIO()) as output:
            status = agent.main(["--check"])
        self.assertEqual(status, 0)
        self.assertEqual(output.getvalue().strip(), sys.executable)
        spawn.assert_not_called()

    def test_cli_dry_run_and_needs_review_persistence(self):
        parent = types.ModuleType("workflow.agent_tasks")
        parent.build_task = MagicMock(return_value={"prompt": "bundled context"})
        parent.accept_result = MagicMock(return_value={"completed": False, "needs_review": True})
        config = {**self.config, "max_input_chars": 180000}
        with patch.dict(sys.modules, {"workflow.agent_tasks": parent}), patch("workflow.agent_tasks", parent, create=True), patch.object(agent, "load_config", return_value=config), patch.object(agent.subprocess, "Popen", side_effect=self.process(stdout=json.dumps({"status": "needs_review", "artifacts": {}}))) as spawn, contextlib.redirect_stdout(io.StringIO()) as output:
            args = ["--stage", "structure", "--batch-dir", str(self.root / "batch")]
            self.assertEqual(agent.main(args + ["--dry-run"]), 0)
            spawn.assert_not_called()
            self.assertEqual(agent.main(args), 3)
        parent.accept_result.assert_called_once_with(self.root / "batch", "structure", {"status": "needs_review", "artifacts": {}})
        lines = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertNotEqual(lines[0]["attempt_dir"], lines[1]["attempt_dir"])
        self.assertEqual((Path(lines[0]["attempt_dir"]) / "input.txt").read_text(), "bundled context")
        self.assertEqual(json.loads((Path(lines[1]["attempt_dir"]) / "import-report.json").read_text()), {"completed": False, "needs_review": True})

    def test_precondition_needs_review_does_not_require_prompt_or_invoke(self):
        parent = types.ModuleType("workflow.agent_tasks")
        task = {"status": "needs_review", "completed": False, "reason": "Missing context"}
        parent.build_task = MagicMock(return_value=task)
        parent.accept_result = MagicMock()
        with patch.dict(sys.modules, {"workflow.agent_tasks": parent}), patch("workflow.agent_tasks", parent, create=True), patch.object(agent, "load_config", return_value=self.config), patch.object(agent, "invoke") as invoke, contextlib.redirect_stdout(io.StringIO()) as output:
            status = agent.main(["--stage", "structure", "--batch-dir", str(self.root / "batch")])
        self.assertEqual(status, 3)
        invoke.assert_not_called()
        parent.accept_result.assert_not_called()
        summary = json.loads(output.getvalue())
        self.assertEqual(json.loads((Path(summary["attempt_dir"]) / "task-report.json").read_text()), task)

    def test_input_hashes_accept_absolute_rules_and_detect_changes(self):
        batch_dir = self.root / "batch"
        batch_dir.mkdir()
        source = batch_dir / "context.json"
        source.write_text("original")
        rules = self.root / "rules.md"
        rules.write_text("shared rules")
        hashes = {"context.json": hashlib.sha256(source.read_bytes()).hexdigest(),
                  str(rules): hashlib.sha256(rules.read_bytes()).hexdigest()}
        agent._check_input_hashes(batch_dir, hashes)
        source.write_text("changed")
        with self.assertRaises(agent.AgentError) as caught:
            agent._check_input_hashes(batch_dir, hashes)
        self.assertEqual(caught.exception.code, "context_changed")

    def test_optional_content_input_appearance_deletion_and_symlink(self):
        from workflow import agent_tasks
        batch_dir = self.root / "batch"
        batch_dir.mkdir()
        for relative in agent_tasks.CONTENT_OPTIONAL_INPUTS:
            with self.subTest(relative=relative):
                path = batch_dir / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                hashes = {relative: agent_tasks.MISSING_INPUT}
                agent._check_input_hashes(batch_dir, hashes)
                path.write_text("present")
                with self.assertRaises(agent.AgentError) as caught:
                    agent._check_input_hashes(batch_dir, hashes)
                self.assertEqual(caught.exception.code, "context_changed")
                hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
                agent._check_input_hashes(batch_dir, hashes)
                path.unlink()
                with self.assertRaises(agent.AgentError) as caught:
                    agent._check_input_hashes(batch_dir, hashes)
                self.assertEqual(caught.exception.code, "context_changed")
                path.symlink_to(self.root / "nonexistent")
                with self.assertRaises(agent.AgentError) as caught:
                    agent._check_input_hashes(batch_dir, {relative: agent_tasks.MISSING_INPUT})
                self.assertEqual(caught.exception.code, "context_changed")
                path.unlink()
        with self.assertRaises(agent.AgentError):
            agent._check_input_hashes(batch_dir, {"../outside": agent_tasks.MISSING_INPUT})

    def test_cli_rejects_optional_input_created_during_invocation(self):
        from workflow import agent_tasks
        batch_dir = self.root / "batch"
        batch_dir.mkdir()
        relative = "01_source/附录.txt"
        path = batch_dir / relative
        def invoke(*args):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("new appendix")
            return {"response": RESPONSE, "artifacts": {}}
        task = {"status": "ready", "prompt": "task", "input_hashes": {relative: agent_tasks.MISSING_INPUT}}
        config = {**self.config, "max_input_chars": 180000}
        with patch.object(agent_tasks, "build_task", return_value=task), patch.object(agent_tasks, "accept_result") as accept, patch.object(agent, "load_config", return_value=config), patch.object(agent, "invoke", side_effect=invoke), contextlib.redirect_stderr(io.StringIO()) as errors:
            status = agent.main(["--stage", "knowledge", "--batch-dir", str(batch_dir)])
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(errors.getvalue())["code"], "context_changed")
        accept.assert_not_called()

    def test_cli_changed_context_preserves_response_and_skips_import(self):
        batch_dir = self.root / "batch"
        batch_dir.mkdir()
        source = batch_dir / "context.json"
        source.write_text("original")
        hashes = {"context.json": hashlib.sha256(source.read_bytes()).hexdigest()}
        parent = types.ModuleType("workflow.agent_tasks")
        parent.build_task = MagicMock(return_value={"prompt": "task", "input_hashes": hashes})
        parent.accept_result = MagicMock()
        def spawn(command, **kwargs):
            source.write_text("concurrent change")
            return self.process(stdout=json.dumps(RESPONSE))(command, **kwargs)
        config = {**self.config, "max_input_chars": 180000}
        with patch.dict(sys.modules, {"workflow.agent_tasks": parent}), patch("workflow.agent_tasks", parent, create=True), patch.object(agent, "load_config", return_value=config), patch.object(agent.subprocess, "Popen", side_effect=spawn), contextlib.redirect_stderr(io.StringIO()) as errors:
            status = agent.main(["--stage", "structure", "--batch-dir", str(batch_dir)])
        self.assertEqual(status, 2)
        parent.accept_result.assert_not_called()
        failure = json.loads(errors.getvalue())
        self.assertEqual(failure["code"], "context_changed")
        attempt = Path(failure["artifacts"]["attempt_dir"])
        self.assertEqual(json.loads((attempt / "response.json").read_text()), RESPONSE)
        self.assertFalse(json.loads((attempt / "import-report.json").read_text())["completed"])
        self.assertEqual(source.read_text(), "concurrent change")

    @unittest.skipIf(os.name == "nt", "flock ownership test is POSIX only")
    def test_shared_lock_rejects_concurrent_attempt(self):
        with agent._agent_lock(1):
            with self.assertRaises(agent.AgentError) as caught:
                with agent._agent_lock(0.02):
                    self.fail("second lock must not be acquired")
        self.assertEqual(caught.exception.code, "lock_timeout")
        with agent._agent_lock(0.02):
            pass  # Released locks are reusable; their files are never removed.

    @unittest.skipIf(os.name == "nt", "Executable shebang fixture is POSIX only")
    def test_real_fake_cli_streams_stdin_and_timeout(self):
        executable = self.root / "fake cli"
        executable.write_text("#!" + sys.executable + "\n" +
                              "import json, pathlib, sys, time\n"
                              "prompt = sys.stdin.read()\n"
                              "print('fake startup', file=sys.stderr, flush=True)\n"
                              "if prompt.endswith('timeout'): time.sleep(20)\n"
                              "payload = json.dumps({'status':'completed','artifacts':{'stdin':prompt,'argv':sys.argv[1:]}})\n"
                              "if '--output-last-message' in sys.argv:\n"
                              " pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text(payload)\n"
                              " print('codex progress')\n"
                              "else: print(payload)\n", encoding="utf-8")
        executable.chmod(0o700)
        for provider in ("dsh", "codex"):
            settings = {**self.config, "provider": provider, "executable": str(executable), "timeout_seconds": 5}
            result = agent.invoke("real stdin", self.root / provider, settings)
            self.assertTrue(result["response"]["artifacts"]["stdin"].endswith("real stdin"))
            self.assertEqual(Path(result["artifacts"]["stderr"]).read_text(), "fake startup\n")
        with self.assertRaises(agent.AgentError) as caught:
            agent.invoke("timeout", self.root / "real-timeout", {**settings, "timeout_seconds": 0.1})
        self.assertEqual(caught.exception.code, "timeout")


if __name__ == "__main__":
    unittest.main()
