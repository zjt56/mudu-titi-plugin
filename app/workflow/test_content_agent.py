"""Native content broker regressions: temporary queues/batches and validators."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from workflow import agent_tasks, content_agent as broker, local_agent, unit_tasks


class ContentAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.batch = self.base / "batch"
        self.batch.mkdir()
        self.source = self.batch / "01_source/source.txt"
        self.source.parent.mkdir()
        self.source.write_text("First line\nSecond line\nThird line", encoding="utf-8")
        self.hashes = {"01_source/source.txt": hashlib.sha256(self.source.read_bytes()).hexdigest()}
        self.units = [{"unit_id": "u-1", "numbers": [1], "unit_kind": "standalone"}]
        self.context = {"confirmed_units_with_stable_ids": self.units,
                        "01_source/source.txt": self.source.read_text(encoding="utf-8"),
                        broker.CATALOG: {"paths": ["语法/时态/一般现在时", "词汇/动词"]},
                        "03_types/types.json": {"items": [{"unit_id": "u-1", "type": "单项填空"}]}}
        self.config = {"native_agent": {"enabled": True, "stages": ["knowledge"],
                                        "timeout_seconds": 3, "poll_seconds": .01},
                       "_content_batch_dir": str(self.batch), "_content_input_hashes": self.hashes,
                       "_content_route_identity": {"provider": "test", "model": "test-model"}}
        self.response = {"status": "completed", "artifacts": {
            agent_tasks.ARTIFACTS["knowledge"]: {"items": [
                {"unit_id": "u-1", "knowledge": ["词汇/动词"], "subquestions": [], "evidence": ["正文 L1"]}]}},
            "notes": [], "findings": []}
        self.addCleanup(patch.stopall)
        patch.object(broker, "QUEUE_ROOT", self.base / "queue").start()
        self.confirmed = patch.object(agent_tasks, "confirmed_units", return_value=self.units).start()
        self.accept = patch.object(agent_tasks, "accept_result", return_value={"completed": True}).start()
        patch.object(agent_tasks, "_collect", side_effect=lambda *a, **kw:
                     (self.context, self.hashes, [], self.confirmed.return_value)).start()
        self.validator = patch.object(broker, "_validator_identity", return_value="validator-v1").start()
        self.route = patch.object(broker.visual_transport, "route_identity",
                                  side_effect=lambda **kw: self.config["_content_route_identity"]).start()
        self.workers = []
        self.addCleanup(self._stop_workers)

    def _stop_workers(self):
        for thread in self.workers:
            thread.join(5)

    def prompt(self, context=None):
        return "Stage: knowledge\n" + unit_tasks._BEGIN + json.dumps(
            context or self.context, ensure_ascii=False) + unit_tasks._END

    def start(self, *, config=None, context=None, run="attempt", prompt=None):
        result = {}
        def work():
            try:
                result["value"] = broker.invoke(prompt or self.prompt(context), self.base / run,
                                                self.config if config is None else config)
            except BaseException as exc:
                result["error"] = exc
        thread = threading.Thread(target=work)
        thread.start()
        self.workers.append(thread)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            tasks = broker.dispatch("GET", "/api/content-agent/tasks")["tasks"]
            if tasks:
                return tasks[-1], thread, result
            if not thread.is_alive():
                break
            time.sleep(.01)
        self.fail("Broker did not publish task: " + str(result))

    def test_native_prompt_uses_scoped_tools_without_file_transport_conflict(self):
        original = agent_tasks.OFFLINE_CONTEXT_GUARD + self.prompt()
        prompt = broker._child_prompt(original, self.context, 'knowledge', [])
        self.assertNotIn('NO tools', prompt)
        self.assertIn('submit_candidate', prompt)
        self.assertIn('Stage: knowledge', prompt)
        self.assertIn('untrusted DATA', prompt)
        self.assertIn('NO tools', original)

    def test_model_stream_timeout_evidence_survives_state_and_return(self):
        task, thread, result = self.start()
        self.claim(task)
        state = broker.dispatch('POST', f"/api/content-agent/tasks/{task['task_id']}/state", {
            'session_id': 'session-A', 'status': 'failed', 'error': 'Content task time budget exceeded',
            'error_code': 'timeout', 'failure_class': 'model_timeout', 'transport': 'native_content',
            'phase': 'model_stream', 'last_provider_error': {'code': 'TIMEOUT', 'message': 'discard raw provider URL'},
            'last_model_activity_at': 1000, 'last_tool_at': 900})['task']
        self.assertEqual(state['error_code'], 'timeout')
        self.assertEqual(state['last_provider_error']['message'], 'Provider stream idle timeout')
        self.assertEqual(state['failure_class'], 'model_timeout')
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result['error'].code, 'timeout')
        self.assertEqual(result['error'].failure_class, 'model_timeout')
        self.assertEqual(result['error'].diagnostics['last_tool_at'], 900)
        self.assertFalse(state['accepted'])
        self.accept.assert_not_called()

    def claim(self, task, session="session-A"):
        return broker.dispatch("POST", f"/api/content-agent/tasks/{task['task_id']}/claim",
                               {"session_id": session})

    def tool(self, task, action, args=None, session="session-A"):
        return broker.dispatch("POST", f"/api/content-agent/tasks/{task['task_id']}/tools",
                               {"session_id": session, "action": action, "args": args or {}})

    def state(self, task, status):
        return broker.dispatch("POST", f"/api/content-agent/tasks/{task['task_id']}/state",
                               {"session_id": "session-A", "status": status})

    def finish(self, task, thread, result):
        accepted = self.tool(task, "submit_candidate", {"candidate": self.response})
        self.assertTrue(accepted["accepted"], accepted)
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertNotIn("error", result, result)
        return result["value"]

    def purge_ack(self, batch):
        errors = []
        def acknowledge():
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                jobs = broker.dispatch("GET", "/api/content-agent/purges")["purges"]
                if jobs:
                    broker.dispatch("POST", "/api/content-agent/purges/" + jobs[0]["purge_id"], {"status": "completed"})
                    return
                time.sleep(.01)
            errors.append("No purge job")
        thread = threading.Thread(target=acknowledge)
        thread.start()
        result = broker.purge_batch(batch, wait_seconds=2)
        thread.join(3)
        self.assertFalse(errors)
        return result

    def test_full_validation_and_return_shape_without_official_writes(self):
        task, thread, result = self.start()
        self.assertEqual(task["route"], self.config["_content_route_identity"])
        self.claim(task)
        value = self.finish(task, thread, result)
        self.assertEqual(value["response"], self.response)
        self.accept.assert_called_once_with(self.batch, "knowledge", self.response, validate_only=True)
        self.assertFalse((self.batch / "04_knowledge").exists())
        self.assertFalse((self.batch / "workflow/manifest.json").exists())
        self.assertEqual(broker.dispatch("GET", "/api/content-agent/tasks"), {"tasks": []})
        self.state(task, "failed")
        receipt = broker.dispatch("GET", f"/api/content-agent/tasks/{task['task_id']}")["task"]
        self.assertEqual(receipt["status"], "completed")
        self.assertTrue(receipt["accepted"])

    def test_claim_is_persistent_idempotent_and_session_guarded(self):
        task, thread, result = self.start()
        first = self.claim(task)
        self.assertEqual(first["task"]["session_id"], "session-A")
        self.assertEqual(self.claim(task)["task"]["session_id"], "session-A")
        self.assertEqual(self.claim(task, "other")["code"], "session_conflict")
        self.assertEqual(self.tool(task, "read_unit", {"unit_id": "u-1"}, session="other")["code"], "session_conflict")
        self.state(task, "running")
        self.assertEqual(broker.dispatch("GET", "/api/content-agent/tasks")["tasks"][0]["status"], "running")
        self.assertEqual(broker._read(broker._task_path(task["task_id"]))["session_id"], "session-A")
        self.finish(task, thread, result)

    def test_tools_are_exact_snapshots_and_whitelisted(self):
        task, thread, result = self.start()
        self.assertEqual(self.tool(task, "read_unit", {"unit_id": "u-1"})["code"], "unclaimed")
        self.claim(task)
        unit = self.tool(task, "content_read_unit", {"unit_id": "u-1"})
        self.assertEqual(unit["unit"], self.units[0])
        self.assertIn("03_types/types.json", unit["context"])
        self.assertEqual(self.tool(task, "read_source", {"path": "01_source/source.txt", "start_line": 2,
                                                      "end_line": 2})["text"], "Second line")
        for path in ("../secret", str(self.source), "01_source/../secret.txt", "01_source/absent.txt",
                     "01_source\\source.txt"):
            self.assertFalse(self.tool(task, "read_source", {"path": path})["retryable"])
        self.assertIn("error", self.tool(task, "read_unit", {"unit_id": "u-2"}))
        search = self.tool(task, "search_catalog", {"query": "语法", "limit": 1})
        self.assertEqual(search["items"], ["语法/时态/一般现在时"])
        self.assertEqual(self.tool(task, "read_catalog", {"offset": 1, "limit": 1})["items"], ["词汇/动词"])
        self.assertIn("error", self.tool(task, "read_catalog", {"path": "references/else.json"}))
        self.finish(task, thread, result)

    def test_stale_input_cancels_before_every_tool(self):
        task, thread, result = self.start()
        self.claim(task)
        self.source.write_text("changed", encoding="utf-8")
        denied = self.tool(task, "read_unit", {"unit_id": "u-1"})
        self.assertFalse(denied["accepted"])
        thread.join(3)
        self.assertEqual(result["error"].code, "context_changed")
        self.accept.assert_not_called()

    def test_delete_request_cancels_and_disappears(self):
        task, thread, result = self.start()
        (self.batch / "workflow").mkdir()
        (self.batch / "workflow/.delete-requested").touch()
        self.assertEqual(broker.dispatch("GET", "/api/content-agent/tasks"), {"tasks": []})
        thread.join(3)
        self.assertEqual(result["error"].code, "cancelled")

    def test_timeout_is_terminal_and_identical_prompt_never_requeued(self):
        config = copy.deepcopy(self.config)
        config["native_agent"]["timeout_seconds"] = .15
        task, thread, result = self.start(config=config)
        thread.join(2)
        self.assertEqual(result["error"].code, "timeout")
        with self.assertRaises(local_agent.AgentError) as caught:
            broker.invoke(self.prompt(), self.base / "retry", config)
        self.assertEqual(caught.exception.code, "timeout")
        self.assertEqual(len(list(broker._tasks())), 1)
        self.assertEqual(broker.dispatch("GET", f"/api/content-agent/tasks/{task['task_id']}")["task"]["status"], "cancelled")

    def test_failed_candidates_are_preserved_then_retry_validates(self):
        self.accept.side_effect = [ValueError("Unknown knowledge path"), {"completed": True}]
        task, thread, result = self.start()
        self.claim(task)
        denied = self.tool(task, "submit_candidate", {"candidate": self.response})
        self.assertFalse(denied["accepted"])
        self.assertTrue(denied["retryable"])
        candidate = broker._task_path(task["task_id"]).parent / "candidate-0001.json"
        self.assertEqual(broker._read(candidate)["candidate"], self.response)
        self.finish(task, thread, result)
        self.assertTrue(candidate.with_name("candidate-0002.json").is_file())

    def test_repeated_validation_error_stops_without_burning_time_budget(self):
        self.accept.side_effect = ValueError("Missing answer q1 needs source evidence")
        task, thread, result = self.start()
        self.claim(task)
        for attempt in range(3):
            candidate = copy.deepcopy(self.response)
            candidate["notes"] = [f"Reformatted candidate {attempt}"]
            denied = self.tool(task, "submit_candidate", {"candidate": candidate})
            self.assertEqual(denied["retryable"], attempt < 2)
        self.assertEqual(denied["failure_class"], "validation_no_progress")
        self.assertIn("Missing answer q1", denied["error"])
        thread.join(2)
        self.assertIn("error", result)
        saved = broker._read(broker._task_path(task["task_id"]))
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(saved["validation_rejection"]["count"], 3)
        receipt = broker._read(broker._task_path(task["task_id"]).parent / "candidate-0003.json")
        self.assertEqual(receipt["feedback"]["failure_class"], "validation_no_progress")
        with self.assertRaises(local_agent.AgentError):
            broker.invoke(self.prompt(), self.base / "retry", self.config)
        self.assertEqual(self.accept.call_count, 3)
        # A real validator repair rechecks the preserved candidate locally.
        self.validator.return_value = "validator-v2"
        self.accept.side_effect = None
        self.accept.return_value = {"completed": True}
        recovered = broker.invoke(self.prompt(), self.base / "repaired", self.config)
        self.assertEqual(recovered["task_id"], task["task_id"])
        self.assertEqual(len(list(broker._tasks())), 1)

    def test_changed_validation_error_resets_no_progress_streak(self):
        self.accept.side_effect = [ValueError("error A"), ValueError("error A"),
                                   ValueError("error B"), {"completed": True}]
        task, thread, result = self.start()
        self.claim(task)
        for _ in range(3):
            self.assertTrue(self.tool(task, "submit_candidate", {"candidate": self.response})["retryable"])
        self.finish(task, thread, result)

    def test_alternating_validation_errors_still_have_a_candidate_limit(self):
        self.accept.side_effect = [ValueError(f"error {i % 2}") for i in range(6)]
        task, thread, result = self.start()
        self.claim(task)
        for attempt in range(6):
            denied = self.tool(task, "submit_candidate", {"candidate": self.response})
            self.assertEqual(denied["retryable"], attempt < 5)
        self.assertEqual(denied["rejected_candidates"], 6)
        self.assertEqual(denied["failure_class"], "validation_no_progress")
        thread.join(2)
        self.assertIn("error", result)

    def test_deterministic_failure_reuses_receipt_and_code_change_revalidates(self):
        error = ValueError('Content source/ownership check failed: [{"retryable":false,"failure_class":"validator_fault"}]')
        self.accept.side_effect = error
        task, thread, result = self.start()
        self.claim(task)
        denied = self.tool(task, "submit_candidate", {"candidate": self.response})
        self.assertFalse(denied["retryable"])
        thread.join(2)
        with self.assertRaises(local_agent.AgentError):
            broker.invoke(self.prompt(), self.base / "same", self.config)
        self.assertEqual(self.accept.call_count, 1)
        self.validator.return_value = "validator-v2"
        self.accept.side_effect = None
        self.accept.return_value = {"completed": True}
        value = broker.invoke(self.prompt(), self.base / "fixed", self.config)
        self.assertEqual(value["task_id"], task["task_id"])
        self.assertEqual(value["response"], self.response)
        self.assertEqual(len(list(broker._tasks())), 1)

    def test_candidate_needs_review_never_accepted(self):
        self.accept.return_value = {"completed": False, "status": "needs_review"}
        task, thread, result = self.start()
        self.claim(task)
        self.assertFalse(self.tool(task, "submit_candidate", {"candidate": self.response})["accepted"])
        self.state(task, "failed")
        thread.join(2)
        self.assertIn("error", result)

    def test_partial_candidate_uses_partition_validator_only(self):
        self.confirmed.return_value = self.units + [{"unit_id": "u-2"}]
        with patch.object(unit_tasks, "validate_part", return_value=self.response) as validate, \
                patch.object(agent_tasks, "_validate") as semantic:
            task, thread, result = self.start()
            self.claim(task)
            self.finish(task, thread, result)
            validate.assert_called_once_with(self.batch, "knowledge", {"unit_ids": ["u-1"]}, self.response)
            semantic.assert_called_once()
            self.assertEqual(semantic.call_args.args[1], "knowledge")
            self.assertEqual(semantic.call_args.args[4], self.units)
            self.accept.assert_not_called()

    def test_tool_budget_failure_and_host_cannot_claim_completion(self):
        config = copy.deepcopy(self.config)
        config["native_agent"]["max_tool_calls"] = 1
        task, thread, result = self.start(config=config)
        self.claim(task)
        self.assertIn("error", self.state(task, "completed"))
        self.tool(task, "read_unit", {"unit_id": "u-1"})
        denied = self.tool(task, "submit_candidate", {"candidate": self.response})
        self.assertFalse(denied["retryable"])
        thread.join(2)
        self.assertIn("error", result)
        self.accept.assert_not_called()

    def test_each_enabled_text_stage_uses_its_full_validator(self):
        for stage in ("answers", "explanations", "types", "preassemble"):
            config = copy.deepcopy(self.config)
            config["native_agent"]["stages"] = ["types" if stage == "preassemble" else stage]
            prompt = self.prompt().replace("Stage: knowledge", f"Stage: {stage}", 1)
            response = dict(self.response, artifacts={agent_tasks.ARTIFACTS[stage]: {"items": []}})
            def submit_pending():
                until = time.monotonic() + 2
                while time.monotonic() < until:
                    tasks = broker.dispatch("GET", "/api/content-agent/tasks")["tasks"]
                    if tasks:
                        task = tasks[-1]
                        self.assertEqual(task["stage"], stage)
                        self.claim(task)
                        self.assertTrue(self.tool(task, "submit_candidate", {"candidate": response})["accepted"])
                        return
                    time.sleep(.005)
            worker = threading.Thread(target=submit_pending)
            worker.start()
            self.workers.append(worker)
            with self.subTest(stage=stage):
                value = broker.invoke(prompt, self.base / stage, config)
                self.assertEqual(value["response"], response)
                self.assertEqual(self.accept.call_args.args[1], stage)
            worker.join(3)

    def test_partial_semantic_source_failure_is_not_accepted(self):
        self.confirmed.return_value = self.units + [{"unit_id": "u-2"}]
        with patch.object(unit_tasks, "validate_part", return_value=self.response), \
                patch.object(agent_tasks, "_validate", side_effect=ValueError("source ownership mismatch")):
            task, thread, result = self.start()
            self.claim(task)
            denied = self.tool(task, "submit_candidate", {"candidate": self.response})
            self.assertFalse(denied["accepted"])
            self.assertIn("source ownership", denied["error"])
            self.state(task, "failed")
            thread.join(3)
            self.assertIn("error", result)

    def test_service_heartbeat_expires_and_can_be_disabled(self):
        self.assertFalse(broker.available())
        self.assertTrue(broker.dispatch("POST", "/api/content-agent/service", {"available": True})["available"])
        self.assertTrue(broker.dispatch("GET", "/api/content-agent/service")["available"])
        with patch.object(broker.time, "time", return_value=time.time() + 20):
            self.assertFalse(broker.available())
        self.assertFalse(broker.dispatch("POST", "/api/content-agent/service", {"available": False})["available"])

    def test_purge_refuses_active_task_then_removes_all_candidate_evidence(self):
        task, thread, result = self.start()
        self.claim(task)
        with self.assertRaises(local_agent.AgentError) as caught:
            broker.purge_batch(self.batch)
        self.assertEqual(caught.exception.code, "busy")
        self.accept.side_effect = ValueError("bad candidate")
        self.tool(task, "submit_candidate", {"candidate": "not json"})
        self.state(task, "failed")
        thread.join(3)
        directory = broker._task_path(task["task_id"]).parent
        self.assertTrue((directory / "candidate-0001.json").exists())
        self.assertEqual(self.purge_ack(self.batch), {"removed": 1})
        self.assertFalse(directory.exists())

    def test_purge_matches_only_exact_resolved_batch(self):
        task, thread, result = self.start()
        self.claim(task)
        self.finish(task, thread, result)
        self.assertEqual(broker.purge_batch(self.base / "other-batch"), {"removed": 0})
        self.assertTrue(broker._task_path(task["task_id"]).exists())
        self.assertEqual(self.purge_ack(self.batch / "."), {"removed": 1})

    def test_fragment_source_keeps_original_numbers_and_denies_omitted_lines(self):
        context = copy.deepcopy(self.context)
        context.pop("01_source/source.txt")
        context["source_fragment_index"] = {"sources": [{"path": "01_source/source.txt", "lines": [
            {"number": 5, "text": "source five"}, {"number": 6, "text": "source six"},
            {"number": 10, "text": "source ten"}]}]}
        task, thread, result = self.start(context=context)
        self.claim(task)
        value = self.tool(task, "read_source", {"path": "01_source/source.txt"})
        self.assertEqual(value["start_line"], 5)
        self.assertEqual(value["end_line"], 6)
        self.assertEqual(value["text"], "source five\nsource six")
        denied = self.tool(task, "read_source", {"path": "01_source/source.txt", "start_line": 6, "end_line": 10})
        self.assertIn("error", denied)
        self.finish(task, thread, result)

    def test_child_prompt_keeps_rules_and_only_evidence_directories(self):
        context = copy.deepcopy(self.context)
        context["01_source/source.txt"] = "SECRET_RAW\n" * 500
        context[broker.CATALOG] = {"paths": ["SECRET_CATALOG"] * 500}
        prompt = "Stage: knowledge\nKEEP EVERY RULE\n" + unit_tasks._BEGIN + json.dumps(context) + unit_tasks._END
        task, thread, result = self.start(prompt=prompt)
        self.assertIn("KEEP EVERY RULE", task["prompt"])
        self.assertNotIn("SECRET_RAW", task["prompt"])
        self.assertNotIn("SECRET_CATALOG", task["prompt"])
        self.assertIn("confirmed_unit_ids", task["prompt"])
        self.claim(task)
        self.assertIn("SECRET_RAW", self.tool(task, "read_source", {"path": "01_source/source.txt"})["text"])
        self.finish(task, thread, result)

    def structure_start(self, directory=True):
        config = copy.deepcopy(self.config)
        config["native_agent"]["stages"] = ["structure_gate"]
        candidate = {"units": [{"id": "c-1", "numbers": [1]}]} if directory else {}
        prepared = broker.content_structure.prepare(batch_dir=self.batch, raw="1. Actual question", candidate=candidate, prompt="STRUCTURE RULE")
        patch.object(broker.content_structure, "verify_binding", return_value=None).start()
        return self.start(config=config, prompt=prepared["prompt"])

    def test_structure_directory_can_be_empty_and_reads_whole_source(self):
        task, thread, result = self.structure_start(False)
        self.assertEqual(task["unit_ids"], [])
        self.assertIn("whole_paper", task["prompt"])
        self.claim(task)
        read = self.tool(task, "read_source", {"path": "body"})
        self.assertEqual(read["lines"][0]["text"], "1. Actual question")
        self.state(task, "failed")
        thread.join(3)

    def test_structure_candidate_directory_is_not_confirmed_units(self):
        task, thread, result = self.structure_start()
        self.claim(task)
        row = self.tool(task, "read_unit", {"unit_id": "candidate-units:1"})
        self.assertTrue(row["candidate_only"])
        self.assertEqual(row["scope"], "whole_paper")
        self.state(task, "failed")
        thread.join(3)

    def test_structure_rejection_retains_raw_candidate_and_diagnostics(self):
        task, thread, result = self.structure_start()
        self.claim(task)
        diagnostics = [{"check": "MISSING_QUESTION", "source_line": 5}]
        with patch.object(broker.content_structure, "validate", return_value={"passed": False, "diagnostics": diagnostics}):
            reply = self.tool(task, "submit_candidate", {"candidate": self.response})
        self.assertFalse(reply["retryable"])
        self.assertEqual(reply["diagnostics"], diagnostics)
        thread.join(3)
        self.assertEqual(result["error"].code, "validation")
        self.assertEqual(result["error"].diagnostics, diagnostics)
        self.assertEqual(json.loads((self.base / "attempt/candidate.json").read_text(encoding="utf-8")), self.response)
        self.accept.assert_not_called()

    def test_structure_success_uses_adapter_validation(self):
        task, thread, result = self.structure_start()
        self.claim(task)
        with patch.object(broker.content_structure, "validate", return_value={"passed": True}) as validate:
            self.finish(task, thread, result)
        validate.assert_called_once()
        self.accept.assert_not_called()

    def test_media_snapshot_binding_and_tools(self):
        context = copy.deepcopy(self.context)
        image = self.batch / "01_source/media/picture.png"
        image.parent.mkdir()
        image.write_bytes(b"\x89PNG\r\n\x1a\nFAKE_TEST_BYTES")
        context["media"] = ["picture.png"]
        task, thread, result = self.start(context=context)
        self.claim(task)
        listed = self.tool(task, "content_list_media")
        self.assertEqual(len(listed["media"]), 1)
        saved = broker._read(broker._task_path(task["task_id"]))
        self.assertIn("01_source/media/picture.png", saved["input_hashes"])
        with patch.object(broker.content_media, "inspect_media", return_value={"status": "ok", "description": "visible", "model_calls": 1}) as inspect:
            value = self.tool(task, "content_inspect_media", {"media_id": listed["media"][0]["media_id"], "question": "What is visible?"})
        self.assertEqual(value["status"], "ok")
        self.assertEqual(inspect.call_args.kwargs["cache"].name, "media-cache")
        self.finish(task, thread, result)

    def test_media_budget_is_reserved_and_inspection_releases_global_lock(self):
        config = copy.deepcopy(self.config)
        config["native_agent"]["max_media_calls"] = 1
        task, thread, result = self.start(config=config)
        self.claim(task)
        entered, release = threading.Event(), threading.Event()
        def inspect(*a, **kw):
            entered.set()
            release.wait(2)
            return {"status": "ok"}
        output = {}
        with patch.object(broker.content_media, "inspect_media", side_effect=inspect):
            call = threading.Thread(target=lambda: output.update(self.tool(task, "inspect_media", {"media_id": "test", "question": "see"})))
            call.start()
            self.assertTrue(entered.wait(1))
            self.assertEqual(broker.dispatch("GET", "/api/content-agent/tasks")["tasks"][0]["media_calls"], 1)
            second = self.tool(task, "inspect_media", {"media_id": "test", "question": "again"})
            self.assertEqual(second["code"], "MEDIA_BUDGET_EXHAUSTED")
            self.state(task, "cancelled")
            release.set()
            call.join(3)
        self.assertEqual(output["code"], "cancelled")
        self.assertNotIn("status", output)
        thread.join(3)

    def test_route_change_rejects_candidate_without_validation(self):
        task, thread, result = self.start()
        self.claim(task)
        self.route.side_effect = None
        self.route.return_value = {"provider": "other", "model": "other"}
        feedback = self.tool(task, "submit_candidate", {"candidate": self.response})
        self.assertFalse(feedback["retryable"])
        thread.join(3)
        self.assertEqual(result["error"].code, "route_changed")
        self.accept.assert_not_called()

    def test_transcript_is_readable_only_when_supplied(self):
        context = copy.deepcopy(self.context)
        context["02_audio/full-transcript.json"] = {"text": "Heard words"}
        task, thread, result = self.start(context=context)
        self.claim(task)
        self.assertEqual(self.tool(task, "read_source", {"path": "02_audio/full-transcript.json"})["value"], {"text": "Heard words"})
        self.finish(task, thread, result)

    def test_purge_ack_removes_job_and_tasks_without_tombstone(self):
        task, thread, result = self.start()
        self.claim(task)
        self.finish(task, thread, result)
        self.assertEqual(self.purge_ack(self.batch), {"removed": 1})
        self.assertEqual(list((broker.QUEUE_ROOT.parent / "purges").glob("*.json")), [])
        self.assertEqual(list(broker._tasks()), [])

    def test_purge_timeout_preserves_data_and_reuses_pending_job(self):
        task, thread, result = self.start()
        self.claim(task)
        self.finish(task, thread, result)
        for _ in range(2):
            with self.assertRaises(local_agent.AgentError) as error:
                broker.purge_batch(self.batch, wait_seconds=.01)
            self.assertEqual(error.exception.code, "timeout")
        self.assertTrue(broker._task_path(task["task_id"]).exists())
        self.assertTrue(self.source.exists())
        self.assertEqual(len(broker.dispatch("GET", "/api/content-agent/purges")["purges"]), 1)
        self.purge_ack(self.batch)

    def test_concurrent_claims_have_one_persisted_owner(self):
        task, thread, result = self.start()
        gate = threading.Barrier(3)
        replies = []
        def claim(session):
            gate.wait()
            replies.append((session, self.claim(task, session)))
        workers = [threading.Thread(target=claim, args=(session,)) for session in ("session-A", "session-B")]
        for worker in workers:
            worker.start()
        gate.wait()
        for worker in workers:
            worker.join(2)
        winners = [session for session, reply in replies if "task" in reply]
        self.assertEqual(len(winners), 1)
        stored = broker._read(broker._task_path(task["task_id"]))
        self.assertEqual(stored["session_id"], winners[0])
        broker.dispatch("POST", f"/api/content-agent/tasks/{task['task_id']}/state",
                        {"session_id": winners[0], "status": "cancelled"})
        thread.join(3)

    def test_completed_receipt_route_drift_becomes_permanent_cancel(self):
        task, thread, result = self.start()
        self.claim(task)
        self.finish(task, thread, result)
        self.route.side_effect = None
        self.route.return_value = {"provider": "changed", "model": "changed"}
        with self.assertRaises(local_agent.AgentError) as caught:
            broker.invoke(self.prompt(), self.base / "reused", self.config)
        self.assertEqual(caught.exception.code, "route_changed")
        stored = broker._read(broker._task_path(task["task_id"]))
        self.assertEqual(stored["status"], "cancelled")
        self.assertFalse(stored["accepted"])
        self.validator.return_value = "changed-validator"
        self.route.side_effect = lambda **kw: self.config["_content_route_identity"]
        with self.assertRaises(local_agent.AgentError):
            broker.invoke(self.prompt(), self.base / "reused-again", self.config)
        self.assertEqual(self.accept.call_count, 1)

    def test_configured_host_budgets_are_validated_and_forwarded(self):
        config = copy.deepcopy(self.config)
        config["native_agent"].update(max_turns=1, max_tool_calls=7, timeout_seconds=2.5)
        task, thread, result = self.start(config=config)
        self.assertEqual(task["budgets"], {"max_turns": 1, "max_tool_calls": 7, "max_attempt_ms": 2500})
        self.claim(task)
        self.finish(task, thread, result)
        for value in (True, 0, -1, 1.5, "4"):
            invalid = copy.deepcopy(self.config)
            invalid["native_agent"]["max_turns"] = value
            with self.subTest(value=value), self.assertRaises(local_agent.AgentError):
                broker.invoke(self.prompt(), self.base / "invalid-turns", invalid)

    def test_large_supplied_rules_remain_reachable_with_exact_paged_reads(self):
        context = copy.deepcopy(self.context)
        rules = "\n".join(f"业务规则 {n}: preserve exact original wording and evidence." for n in range(1, 621))
        mapping = {"types": [{"type": f"类型{n}", "knowledge": ["语法/时态", "词汇/动词"]} for n in range(180)]}
        context["skills-reference/knowledge-matching-rules.md"] = rules
        context["docs/knowledge-tagging.md"] = rules
        context["references/type-knowledge-map.json"] = mapping
        context["answer_source_text"] = rules
        task, thread, result = self.start(context=context)
        self.claim(task)
        _, _, tail = task["prompt"].partition(unit_tasks._BEGIN)
        summary = json.loads(tail.partition(unit_tasks._END)[0])
        directory = {row["path"]: row for row in summary["task_reference_files"]}
        self.assertEqual(directory["skills-reference/knowledge-matching-rules.md"]["total_lines"], 620)
        self.assertEqual(directory["answer_source_text"]["total_lines"], 620)
        for path in ("skills-reference/knowledge-matching-rules.md", "docs/knowledge-tagging.md", "answer_source_text"):
            first = self.tool(task, "read_source", {"path": path})
            second = self.tool(task, "read_source", {"path": path, "start_line": 501, "end_line": 620})
            self.assertEqual(first["text"] + "\n" + second["text"], rules)
            self.assertEqual(first["end_line"], 500)
        expected = json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True).splitlines()
        path = "references/type-knowledge-map.json"
        self.assertEqual(directory[path]["total_lines"], len(expected))
        lines = []
        for start in range(1, len(expected) + 1, 500):
            page = self.tool(task, "read_source", {"path": path, "start_line": start, "end_line": min(start + 499, len(expected))})
            lines.extend(row["text"] for row in page["lines"])
        self.assertEqual(lines, expected)
        self.finish(task, thread, result)

    def test_reference_reads_deny_missing_keys_traversal_and_invalid_windows(self):
        context = copy.deepcopy(self.context)
        context["docs/rules.md"] = "line\n" * 601
        disk = self.batch / "docs/unoffered.md"
        disk.parent.mkdir()
        disk.write_text("MUST NEVER READ", encoding="utf-8")
        task, thread, result = self.start(context=context)
        self.claim(task)
        cases = [{"path": "docs/unoffered.md"}, {"path": "docs/../docs/rules.md"},
                 {"path": "docs/./rules.md"}, {"path": str(disk.resolve())},
                 {"path": "docs/rules.md", "start_line": 1, "end_line": 501},
                 {"path": "docs/rules.md", "start_line": 601, "end_line": 602},
                 {"path": "docs/rules.md", "start_line": 0},
                 {"path": "docs/rules.md", "start_line": True}]
        for args in cases:
            with self.subTest(args=args):
                self.assertIn("error", self.tool(task, "read_source", args))
        self.finish(task, thread, result)

    def test_unknown_receipt_is_explicit(self):
        result = broker.dispatch("GET", "/api/content-agent/tasks/" + "0" * 32)
        self.assertEqual(result["code"], "not_found")
        self.assertFalse(result["retryable"])

    def test_stored_success_reused_without_child_or_validation(self):
        task, thread, result = self.start()
        self.claim(task)
        self.finish(task, thread, result)
        stored = broker._read(broker._task_path(task["task_id"]))
        stored["deadline"] = time.time() - 60
        broker._save(stored)
        again = broker.invoke(self.prompt(), self.base / "again", self.config)
        self.assertEqual(again["task_id"], task["task_id"])
        self.assertEqual(self.accept.call_count, 1)

    def test_input_hash_file_fallback_and_required_route(self):
        config = copy.deepcopy(self.config)
        config.pop("_content_input_hashes")
        root = self.base / "attempt"
        root.mkdir()
        (root / "input-hashes.json").write_text(json.dumps(self.hashes), encoding="utf-8")
        task, thread, result = self.start(config=config)
        self.claim(task)
        self.finish(task, thread, result)
        config.pop("_content_route_identity")
        with self.assertRaises(local_agent.AgentError):
            broker.invoke(self.prompt(), root, config)

    def test_config_requires_explicit_enable_and_supported_stage(self):
        for native in ({}, {"enabled": 1}, {"enabled": True, "stages": ["audio"]},
                       {"enabled": True, "mode": "fallback"}, {"enabled": True, "max_tool_calls": True}):
            config = dict(self.config, native_agent=native)
            with self.subTest(native=native), self.assertRaises(local_agent.AgentError):
                broker.invoke(self.prompt(), self.base / "bad", config)
        with self.assertRaises(local_agent.AgentError):
            broker.invoke("Stage: answers\n" + unit_tasks._BEGIN + "{}" + unit_tasks._END,
                          self.base / "bad", self.config)


if __name__ == "__main__":
    unittest.main()
