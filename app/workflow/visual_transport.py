"""JSON-only image transport using the user's exact configured Harness model."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
import shutil
import subprocess


class VisualTransportError(RuntimeError):
    """An explicit transport/configuration/provider failure, never a fallback."""


def identify(images: list[dict], prompt: str, *, stage: str = "extract",
             max_tokens: int = 1200, timeout: float = 90) -> dict:
    """Send all image bytes in one native pi-ai call and return route + text.

    Images have ``id`` and ``path`` keys. The caller owns output-schema parsing
    and evidence caching; this seam never substitutes OCR or another model.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        raise VisualTransportError("INVALID_REQUEST: prompt must be non-empty")
    _stage(stage)
    agent_header = "" if re.search(r"^Agent:[\t ]*[A-Za-z0-9_-]+", prompt, re.MULTILINE) else f"Agent: {stage}\n"
    payload = {"prompt": f"Stage: {stage}\n{agent_header}{prompt}", "images": [
        {"id": image["id"], "path": str(Path(image["path"]).resolve())}
        for image in images
    ], "max_tokens": max_tokens}
    result = _call(payload, timeout)
    if not all(isinstance(result.get(key), str) and result[key].strip()
               for key in ("provider", "model", "text")):
        raise VisualTransportError("INVALID_RESPONSE: provider/model/text must be non-empty strings")
    return result


def _stage(stage: str) -> None:
    if not isinstance(stage, str) or not stage or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in stage):
        raise VisualTransportError("INVALID_REQUEST: invalid stage")


def route_identity(stage: str = "extract") -> dict:
    """Read the exact selected route without a provider call or credentials read."""
    _stage(stage)
    result = _call({"prompt": f"Stage: {stage}\nAgent: {stage}\nRoute identity", "route_only": True}, 15)
    if not all(isinstance(result.get(key), str) and result[key].strip()
               for key in ("provider", "model", "profile")):
        raise VisualTransportError("INVALID_RESPONSE: route identity is incomplete")
    return result


def _call(payload: dict, timeout: float) -> dict:
    node = os.environ.get("WF_VISION_NODE") or shutil.which("node")
    if not node:
        raise VisualTransportError("NODE_UNAVAILABLE: Node.js is required by wf-vision")
    bridge = Path(__file__).resolve().parents[2] / "backend" / "wf-vision.mjs"
    env = dict(os.environ)
    env["WF_VISION_TIMEOUT_MS"] = str(max(1, int(timeout * 1000)))
    try:
        completed = subprocess.run([node, str(bridge)], input=json.dumps(payload, ensure_ascii=False),
                                   text=True, encoding="utf-8", capture_output=True, timeout=timeout + 5, env=env)
    except subprocess.TimeoutExpired as exc:
        raise VisualTransportError("VISION_TIMEOUT: selected model call timed out; no fallback attempted") from exc
    except OSError as exc:
        raise VisualTransportError("RUNTIME_START_FAILED: cannot start wf-vision") from exc
    try:
        result = json.loads(completed.stdout)
    except (ValueError, TypeError) as exc:
        raise VisualTransportError("INVALID_RESPONSE: vision bridge did not return one JSON object") from exc
    if not isinstance(result, dict):
        raise VisualTransportError("INVALID_RESPONSE: vision bridge response must be an object")
    if completed.returncode or result.get("error"):
        error = result.get("error") or {}
        # Never echo stderr: SDK diagnostics may contain credentials or headers.
        raise VisualTransportError(f"{error.get('code', 'VISION_FAILED')}: {error.get('message', 'selected image call failed; no fallback attempted')}")
    return result
