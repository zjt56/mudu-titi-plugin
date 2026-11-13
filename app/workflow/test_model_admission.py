"""Offline batch admission regressions: no real provider/config/network access."""
from contextlib import nullcontext
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from workflow import model_admission as admission, local_agent, visual_transport


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.batch = Path(self.temp.name).resolve() / "batch-a"
        self.marker = self.batch / "workflow/agent-runs/.rate-limit-cooldown"
        self.marker.parent.mkdir(parents=True)
        self.marker.write_text("2000\n")
        self.clock = patch.object(admission.time, "time", return_value=1000)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def test_wait_is_batch_bound_and_does_not_modify_marker(self):
        with self.assertRaises(admission.ModelCooldownError) as caught:
            admission.require_model_call(self.batch)
        result = admission.deferral(caught.exception)
        self.assertEqual(result["retry_after"], 1000)
        self.assertEqual(result["error_code"], "model_cooldown")
        self.assertFalse(result["model_started"])
        self.assertIn(str(self.batch), str(caught.exception))
        self.assertEqual(self.marker.read_text(), "2000\n")
        admission.require_model_call(self.batch.parent / "batch-b")
        admission.require_model_call(None)

    def test_expired_invalid_missing_markers_do_not_block(self):
        for text in ("999", "not a time", "nan", "inf", "-inf"):
            self.marker.write_text(text)
            admission.require_model_call(self.batch)
        self.marker.unlink()
        admission.require_model_call(self.batch)

    def test_identity_uses_workflow_ancestor_not_cwd_or_image_location(self):
        attempt = self.batch / "workflow/metadata-attempts/meta-x"
        self.assertEqual(admission.resolve_batch(run_dir=attempt), self.batch)
        self.assertIsNone(admission.resolve_batch(run_dir=self.batch / "arbitrary"))
        other = self.batch.parent / "other"
        self.assertEqual(admission.resolve_batch(batch_dir=other, run_dir=attempt), other)

    def test_visual_rejects_before_bridge_but_route_only_is_local(self):
        route = {"provider": "selected", "model": "exact", "profile": "current"}
        with patch.object(visual_transport, "_call", return_value=route) as bridge:
            with self.assertRaises(visual_transport.VisualTransportError) as caught:
                visual_transport.identify([], "look", batch_dir=self.batch)
            self.assertEqual(caught.exception.code, "model_cooldown")
            bridge.assert_not_called()
            self.assertEqual(visual_transport.route_identity(), route)
            self.assertTrue(bridge.call_args.args[0]["route_only"])

    def test_cli_guard_runs_after_lock_before_process(self):
        config = {"provider": "dsh", "executable": sys.executable,
                  "timeout_seconds": 1, "max_input_chars": 180000}
        attempt = self.batch / "workflow/agent-runs/one"
        with patch.object(local_agent, "_agent_lock", return_value=nullcontext(None)), \
             patch.object(local_agent.subprocess, "Popen", side_effect=AssertionError("no model process")) as spawn:
            with self.assertRaises(local_agent.AgentError) as caught:
                local_agent.invoke("Stage: answers\nData", attempt, config)
        self.assertEqual(caught.exception.code, "model_cooldown")
        self.assertTrue(caught.exception.retryable)
        spawn.assert_not_called()
        metadata = json.loads((attempt / "attempt.json").read_text())
        self.assertIsNone(metadata["wall_start"])
        self.assertEqual(metadata["error_code"], "model_cooldown")

    def test_legacy_visual_cache_precedes_guard_and_network_is_blocked(self):
        from workflow import visual_review
        with patch.object(visual_review, "_load_key", return_value="fixture-only"), \
             patch.object(visual_review, "_iter_images", return_value=[(self.batch / "x.png", "x.png")]), \
             patch.object(visual_review, "_identify", side_effect=AssertionError("no network")) as call:
            result = visual_review.build(self.batch, endpoint="https://fixture.invalid")
            self.assertEqual(result["status"], "deferred")
            self.assertFalse(result["model_started"])
            call.assert_not_called()
            artifact = self.batch / visual_review.ARTIFACT
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("{}")
            self.assertEqual(visual_review.build(self.batch, endpoint="https://fixture.invalid")["status"], "cached")

    def test_existing_native_claim_protocol_is_unchanged_during_cooldown(self):
        # Characterization of the remaining boundary: a preexisting queue item may
        # be claimed. Blocking it requires a coordinated host deferral protocol.
        from workflow import content_agent
        task = {"task_id": "a" * 32, "batch_dir": str(self.batch), "status": "queued", "session_id": None}
        with patch.object(content_agent, "_lock", side_effect=lambda: nullcontext()), \
             patch.object(content_agent, "_task_path", return_value=self.marker), \
             patch.object(content_agent, "_read", return_value=task), \
             patch.object(content_agent, "_expire"), patch.object(content_agent, "_save"), \
             patch.object(content_agent, "_public", side_effect=lambda value: value):
            result = content_agent.dispatch("POST", "/api/content-agent/tasks/" + "a" * 32 + "/claim",
                                            {"session_id": "existing-host"})
        self.assertEqual(result["task"]["status"], "claimed")

    def test_native_new_task_waits_but_existing_receipt_remains_readable(self):
        from workflow import content_agent
        config = {"native_agent": {"enabled": True, "stages": ["knowledge"]},
                  "_content_batch_dir": str(self.batch), "_content_input_hashes": {"source": "hash"},
                  "_content_route_identity": {"provider": "p", "model": "m"}}
        task = {"identity": "same", "task_id": "a" * 32, "status": "completed", "response": {"status": "completed"}}
        from contextlib import ExitStack
        with ExitStack() as stack:
            for name, value in (("_context", ({}, ["u"], "knowledge")), ("_digest", "same"),
                                ("_tasks", []), ("_expire", None), ("_guard", None), ("_check_route", None), ("_write", None)):
                stack.enter_context(patch.object(content_agent, name, return_value=value))
            stack.enter_context(patch.object(content_agent, "_lock", side_effect=lambda: nullcontext()))
            stack.enter_context(patch.object(local_agent, "_check_input_hashes"))
            stack.enter_context(patch.object(content_agent.content_media, "build_snapshot", return_value={"input_hashes": {}}))
            save = stack.enter_context(patch.object(content_agent, "_save"))
            with self.assertRaises(local_agent.AgentError) as caught:
                content_agent.invoke("prompt", self.batch / "receipt", config)
            self.assertEqual(caught.exception.code, "model_cooldown")
            save.assert_not_called()
            with patch.object(content_agent, "_tasks", return_value=[task]), \
                 patch.object(content_agent, "_read", return_value=task):
                result = content_agent.invoke("prompt", self.batch / "receipt", config)
            self.assertEqual(result["response"], task["response"])


if __name__ == "__main__":
    unittest.main()
