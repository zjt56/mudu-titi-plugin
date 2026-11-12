"""Native selection is explicit, bound, and never falls back after selection."""
import contextlib
import hashlib
from http.server import ThreadingHTTPServer
import json
import threading
import time
from urllib.request import Request, urlopen
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from workflow import local_agent, content_agent, visual_transport, agent_tasks


class ContentIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.batch = Path(self.temp.name)
        self.config = {"provider": "dsh", "executable": sys.executable,
                       "timeout_seconds": 2, "max_input_chars": 180000,
                       "_content_batch_dir": str(self.batch),
                       "_content_input_hashes": {"01_source/body.txt": "abc"},
                       "native_agent": {"enabled": True, "mode": "auto", "stages": ["knowledge"]}}
        self.prompt = "Stage: knowledge\nAgent: knowledge\nBEGIN SUPPLIED CONTENT\n{}"
        self.route = {"provider": "test-provider", "model": "test-model", "profile": "test-profile"}

    def test_live_host_uses_exact_route_and_bound_inputs(self):
        result = {"response": {"status": "completed"}}
        with patch.object(content_agent, "available", return_value=True), \
             patch.object(visual_transport, "route_identity", return_value=self.route), \
             patch.object(content_agent, "invoke", return_value=result) as invoke, \
             patch.object(local_agent.subprocess, "Popen") as spawn:
            actual = local_agent.invoke(self.prompt, self.batch / "run", self.config, permit_parallel=True)
        self.assertIs(actual, result)
        spawn.assert_not_called()
        args, kw = invoke.call_args
        self.assertEqual(args[2]["_content_route_identity"], self.route)
        self.assertEqual(args[2]["_content_input_hashes"], self.config["_content_input_hashes"])
        self.assertTrue(kw["permit_parallel"])

    def test_selected_native_failure_never_starts_cli(self):
        with patch.object(content_agent, "available", return_value=True), \
             patch.object(visual_transport, "route_identity", return_value=self.route), \
             patch.object(content_agent, "invoke", side_effect=local_agent.AgentError("Rejected", "response")), \
             patch.object(local_agent.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(local_agent.AgentError, "Rejected"):
                local_agent.invoke(self.prompt, self.batch / "run", self.config)
        spawn.assert_not_called()

    def test_broken_selected_route_is_configuration_error(self):
        with patch.object(content_agent, "available", return_value=True), \
             patch.object(visual_transport, "route_identity", side_effect=visual_transport.VisualTransportError("broken route")), \
             patch.object(content_agent, "invoke") as invoke, \
             patch.object(local_agent.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(local_agent.AgentError, "broken route"):
                local_agent.invoke(self.prompt, self.batch / "run", self.config)
        spawn.assert_not_called()
        invoke.assert_not_called()

    def test_strict_does_not_fallback_when_no_service(self):
        self.config["native_agent"]["mode"] = "strict"
        with patch.object(content_agent, "available", return_value=False), \
             patch.object(visual_transport, "route_identity", return_value=self.route), \
             patch.object(content_agent, "invoke", side_effect=local_agent.AgentError("host unavailable", "timeout")) as invoke, \
             patch.object(local_agent.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(local_agent.AgentError, "host unavailable"):
                local_agent.invoke(self.prompt, self.batch / "run", self.config)
        invoke.assert_called_once()
        spawn.assert_not_called()

    def test_missing_private_binding_rebuilds_current_stage_task(self):
        self.config.pop("_content_input_hashes")
        with patch.object(content_agent, "available", return_value=True), \
             patch.object(visual_transport, "route_identity", return_value=self.route), \
             patch.object(agent_tasks, "build_task", return_value={"input_hashes": {"source": "sha"}}) as build, \
             patch.object(content_agent, "invoke", return_value={}) as invoke:
            local_agent.invoke(self.prompt, self.batch / "run", self.config)
        self.assertEqual(build.call_args.args[:2], (self.batch, "knowledge"))
        self.assertEqual(invoke.call_args.args[2]["_content_input_hashes"], {"source": "sha"})

    def test_auto_without_host_preserves_existing_transport(self):
        def spawn(_args, **kw):
            kw["stdout"].write(json.dumps({"status": "completed", "artifacts": {}}).encode())
            proc = MagicMock(returncode=0, pid=987654)
            proc.communicate.return_value = (None, None)
            return proc
        with patch.object(content_agent, "available", return_value=False), \
             patch.object(content_agent, "invoke") as native, \
             patch.object(local_agent.subprocess, "Popen", side_effect=spawn) as cli, \
             patch.object(local_agent, "_agent_lock", return_value=contextlib.nullcontext()):
            actual = local_agent.invoke(self.prompt, self.batch / "run", self.config)
        self.assertEqual(actual["provider"], "dsh")
        cli.assert_called_once()
        native.assert_not_called()

    def test_policy_rejects_invalid_types_and_stages(self):
        for policy in ({"enabled": "yes"}, {"enabled": True, "mode": "fallback"},
                       {"enabled": True, "stages": ["review"]},
                       {"enabled": True, "stages": ["knowledge", "knowledge"]}):
            with self.subTest(policy=policy), self.assertRaises(local_agent.AgentError):
                local_agent._validate_config({**self.config, "native_agent": policy}, self.batch)


class ContentHttpIntegrationTests(unittest.TestCase):
    def test_real_handler_roundtrip_uses_broker_receipts_and_validator(self):
        from workbench import server as workbench
        from workflow import unit_tasks
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            batch = base / "batch"
            source = batch / "01_source/body.txt"
            source.parent.mkdir(parents=True)
            source.write_text("1. A source-bound question", encoding="utf-8")
            hashes = {"01_source/body.txt": hashlib.sha256(source.read_bytes()).hexdigest()}
            units = [{"unit_id": "u-1", "numbers": [1], "unit_kind": "standalone"}]
            context = {"confirmed_units_with_stable_ids": units,
                       "01_source/body.txt": source.read_text(encoding="utf-8"),
                       content_agent.CATALOG: {"paths": ["语法/时态"]}}
            prompt = "Stage: knowledge\n" + unit_tasks._BEGIN + json.dumps(context) + unit_tasks._END
            route = {"provider": "fixture", "model": "fixture-model"}
            config = {"native_agent": {"enabled": True, "stages": ["knowledge"],
                                      "timeout_seconds": 5, "poll_seconds": .01},
                      "_content_batch_dir": str(batch), "_content_input_hashes": hashes,
                      "_content_route_identity": route}
            response = {"status": "completed", "artifacts": {"05_knowledge/knowledge.json": {
                "items": [{"unit_id": "u-1", "knowledge": ["语法/时态"]}]}}, "notes": [], "findings": []}
            result = {}
            with patch.object(content_agent, "QUEUE_ROOT", base / "queue"), \
                 patch.object(agent_tasks, "confirmed_units", return_value=units), \
                 patch.object(agent_tasks, "accept_result", return_value={"completed": True}) as validate, \
                 patch.object(visual_transport, "route_identity", return_value=route):
                class QuietHandler(workbench.Handler):
                    def log_message(self, *_args):
                        pass
                http = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
                host_thread = threading.Thread(target=http.serve_forever, daemon=True)
                host_thread.start()
                def request(path, payload=None):
                    data = None if payload is None else json.dumps(payload).encode("utf-8")
                    req = Request(f"http://127.0.0.1:{http.server_port}" + path, data=data,
                                  headers={"Content-Type": "application/json"})
                    with urlopen(req, timeout=3) as reply:
                        return json.load(reply)
                def invoke():
                    try:
                        result["value"] = content_agent.invoke(prompt, base / "run", config)
                    except BaseException as exc:
                        result["error"] = exc
                worker = threading.Thread(target=invoke, daemon=True)
                try:
                    self.assertTrue(request("/api/content-agent/service", {"available": True})["available"])
                    worker.start()
                    deadline = time.monotonic() + 3
                    tasks = []
                    while time.monotonic() < deadline:
                        tasks = request("/api/content-agent/tasks")["tasks"]
                        if tasks:
                            break
                        time.sleep(.01)
                    self.assertTrue(tasks, result)
                    endpoint = "/api/content-agent/tasks/" + tasks[0]["task_id"]
                    claim = request(endpoint + "/claim", {"session_id": "http-fixture"})
                    self.assertEqual(claim["task"]["session_id"], "http-fixture")
                    source_reply = request(endpoint + "/tools", {"session_id": "http-fixture",
                        "action": "read_source", "args": {"path": "01_source/body.txt"}})
                    self.assertEqual(source_reply["text"], "1. A source-bound question")
                    accepted = request(endpoint + "/tools", {"session_id": "http-fixture",
                        "action": "submit_candidate", "args": {"candidate": response}})
                    self.assertIs(accepted["accepted"], True, accepted)
                    receipt = request(endpoint)["task"]
                    self.assertIs(receipt["accepted"], True)
                    self.assertEqual(receipt["status"], "completed")
                    worker.join(3)
                    self.assertFalse(worker.is_alive())
                    self.assertNotIn("error", result)
                    self.assertEqual(result["value"]["response"], response)
                    self.assertIs(validate.call_args.kwargs["validate_only"], True)
                    self.assertFalse((batch / "05_knowledge").exists())
                    self.assertFalse((batch / "workflow/manifest.json").exists())
                finally:
                    http.shutdown()
                    http.server_close()
                    host_thread.join(3)
                    worker.join(6)


if __name__ == "__main__":
    unittest.main()
