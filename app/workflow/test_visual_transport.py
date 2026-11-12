from __future__ import annotations

import json
import subprocess
from pathlib import Path
import unittest
from unittest.mock import patch

from workflow.visual_transport import identify, route_identity, VisualTransportError


class VisualTransportTests(unittest.TestCase):
    def run_result(self, result, code=0):
        return subprocess.CompletedProcess([], code, json.dumps(result), "private SDK diagnostic")

    @patch("workflow.visual_transport.shutil.which", return_value="node")
    @patch("workflow.visual_transport.subprocess.run")
    def test_identify_sends_all_images_once_and_preserves_route(self, run, _which):
        run.return_value = self.run_result({"provider": "selected", "model": "precise", "text": '{"images":[]} '})
        result = identify([{"id": "one", "path": "one.png"}, {"id": "two", "path": "two.jpg"}], "read images", stage="extract", max_tokens=1500)
        self.assertEqual(result["model"], "precise")
        self.assertEqual(run.call_count, 1)
        payload = json.loads(run.call_args.kwargs["input"])
        self.assertEqual([i["id"] for i in payload["images"]], ["one", "two"])
        self.assertEqual(payload["max_tokens"], 1500)
        self.assertTrue(payload["prompt"].startswith("Stage: extract\nAgent: extract\n"))
        self.assertNotIn("shell", run.call_args.kwargs)

    @patch("workflow.visual_transport.shutil.which", return_value="node")
    @patch("workflow.visual_transport.subprocess.run")
    def test_identify_pins_utf8_for_non_ascii_paths_and_prompt(self, run, _which):
        run.return_value = self.run_result({"provider": "selected", "model": "precise", "text": '{"items":[]}'})
        path = Path("课时") / "基础巩固.jpeg"
        identify([{"id": "图片一", "path": str(path)}], "核对基础巩固图片")
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["encoding"], "utf-8")
        # Model bridge reads stdin as UTF-8, independent of Windows locale.
        received = json.loads(kwargs["input"].encode(kwargs["encoding"]).decode("utf-8"))
        self.assertEqual(received["images"], [{"id": "图片一", "path": str(path.resolve())}])
        self.assertIn("核对基础巩固图片", received["prompt"])
        self.assertEqual(run.call_count, 1)

    @patch("workflow.visual_transport.shutil.which", return_value="node")
    @patch("workflow.visual_transport.subprocess.run")
    def test_route_only_does_not_send_images(self, run, _which):
        run.return_value = self.run_result({"provider": "chosen", "model": "m", "profile": "p"})
        self.assertEqual(route_identity()["provider"], "chosen")
        payload = json.loads(run.call_args.kwargs["input"])
        self.assertTrue(payload["route_only"])
        self.assertIn("Agent: extract\n", payload["prompt"])
        self.assertNotIn("images", payload)

    @patch("workflow.visual_transport.shutil.which", return_value="node")
    @patch("workflow.visual_transport.subprocess.run")
    def test_provider_error_is_explicit_and_does_not_echo_stderr(self, run, _which):
        run.return_value = self.run_result({"error": {"code": "NO_ADAPTER", "message": "selected provider unavailable"}}, 1)
        with self.assertRaisesRegex(VisualTransportError, "NO_ADAPTER") as raised:
            identify([{"id": "a", "path": "one.png"}], "read")
        self.assertNotIn("private", str(raised.exception))
        self.assertEqual(run.call_count, 1)

    @patch("workflow.visual_transport.shutil.which", return_value="node")
    @patch("workflow.visual_transport.subprocess.run")
    def test_text_cli_and_incomplete_responses_are_rejected(self, run, _which):
        for response in ("CLI says it saw the image", "{}", "[]"):
            run.return_value = subprocess.CompletedProcess([], 0, response, "")
            with self.assertRaises(VisualTransportError):
                identify([{"id": "a", "path": "one.png"}], "read")

    @patch("workflow.visual_transport.shutil.which", return_value="node")
    @patch("workflow.visual_transport.subprocess.run", side_effect=subprocess.TimeoutExpired("node", 90))
    def test_timeout_has_no_second_model_call(self, run, _which):
        with self.assertRaisesRegex(VisualTransportError, "VISION_TIMEOUT"):
            identify([{"id": "a", "path": "one.png"}], "read")
        self.assertEqual(run.call_count, 1)

    def test_invalid_stage_fails_before_process(self):
        with self.assertRaisesRegex(VisualTransportError, "invalid stage"):
            route_identity("extract\nAgent: injected")


if __name__ == "__main__":
    unittest.main()
