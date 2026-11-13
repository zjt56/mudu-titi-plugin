"""Batch-local admission immediately before model dispatch; never gates local work."""
from __future__ import annotations

import math
from pathlib import Path
import time


class ModelCooldownError(RuntimeError):
    """The model has not started; retry after the existing batch cooldown."""


def resolve_batch(*, batch_dir=None, run_dir=None):
    """Use explicit identity or a workflow attempt ancestor, never cwd/name guesses.

    Standalone transport calls outside a workflow keep their independent routing.
    """
    if batch_dir is not None:
        return Path(batch_dir).expanduser().resolve()
    if run_dir is not None:
        root = Path(run_dir).expanduser().resolve()
        return next((p.parent for p in (root, *root.parents) if p.name == "workflow"), None)
    return None


def deferral(exc):
    """Machine-readable wait result for existing best-effort consumers."""
    if getattr(exc, "code", None) != "model_cooldown":
        return {}
    return {"status": "deferred", "error_code": "model_cooldown", "retryable": True,
            "retry_after": exc.retry_after, "batch_dir": exc.batch_dir,
            "model_started": False}


def require_model_call(batch_dir, *, error_type=ModelCooldownError):
    """Reject only a fresh model call; do not create/extend/clear cooldown state."""
    if batch_dir is None:
        return
    batch = Path(batch_dir).expanduser().resolve()
    try:
        until = float((batch / "workflow/agent-runs/.rate-limit-cooldown").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return
    remaining = until - time.time()
    if not math.isfinite(remaining) or remaining <= 0:
        return
    exc = error_type(f"MODEL_COOLDOWN: 批次 {batch.name} ({batch}) 模型调用延期，约 {math.ceil(remaining)}s 后重试")
    exc.code = "model_cooldown"
    exc.retryable = True
    exc.retry_after = remaining
    exc.batch_dir = str(batch)
    exc.model_started = False
    raise exc
