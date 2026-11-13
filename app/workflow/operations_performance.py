"""Bounded performance observations; never a fault or a repair authorization.

Only manifest stage metadata is read. ``collect`` is pure read-only;
``observe`` may atomically write the dedicated operations-performance.json report.
Stage wall time includes orchestration/waiting and is not model inference latency.
No elapsed-time observation can time out a running task or promote an incident.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import contracts, execution_state

VERSION = "workflow-operations-performance/v1"
REPORT_PATH = "workflow/operations-performance.json"
TOTAL_THRESHOLD_SECONDS = 600
MODEL_STAGE_THRESHOLD_SECONDS = 180
# These stages can invoke a model. Assemble/types are now deterministic handoffs.
MODEL_STAGES = frozenset(("structure_gate", "preassemble", "answers", "audio",
                          "explanations", "knowledge", "review"))
_MAX_BYTES = 4 * 1024 * 1024
_WRITE_LOCK = threading.Lock()


def _object(value):
    return value if isinstance(value, dict) else {}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _path(root, relative):
    """Only regular metadata files inside this batch; never follow symlinks."""
    if root.is_symlink():
        return None
    target = root
    for part in Path(relative).parts:
        if part in ("..", "/"):
            return None
        target = target / part
        if target.is_symlink():
            return None
    return target


def _read(root, relative):
    path = _path(root, relative)
    try:
        if path is None or not path.is_file() or path.stat().st_size > _MAX_BYTES:
            return {}
        with path.open("rb") as stream:
            raw = stream.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            return {}
        return _object(json.loads(raw.decode("utf-8-sig")))
    except (OSError, ValueError, UnicodeError, RecursionError):
        return {}


def _time(value):
    # Require timezone-aware metadata so host timezone cannot change evidence.
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.timestamp() if stamp.tzinfo is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def _iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _signature(manifest):
    # No updated_at, notes, serialized formatting, paths or source text participate.
    inputs = {str(key): _object(value).get("sha256")
              for key, value in _object(manifest.get("inputs")).items()}
    stages = _object(manifest.get("stages"))
    finished = {sid: {"finished_at": _object(stages.get(sid)).get("finished_at"),
                      "input_hashes": _object(stages.get(sid)).get("input_hashes") or {}}
                for sid in execution_state.LOCAL_STAGES}
    return _digest({"workflow_id": manifest.get("workflow_id"),
                    "inputs": inputs, "finished": finished})


def collect(folder, manifest=None):
    """Return a metadata-only candidate report, or ``detected=False`` with reason.

    Requires every local stage executed under the existing unqualified-delivery
    contract; pending/running/queued/failed/blocked work never becomes a timeout.
    Quality differences remain output, not a fault or a reason for regeneration.
    """
    root = Path(folder)
    state = _read(root, "workflow/manifest.json") if manifest is None else _object(manifest)
    if not state or root.is_symlink():
        return {"detected": False, "reason": "manifest_unavailable"}
    records = _object(state.get("stages"))
    if not all(execution_state.executed(records.get(sid), allow_unqualified=True)
               for sid in execution_state.LOCAL_STAGES):
        return {"detected": False, "reason": "local_execution_incomplete"}
    rows = []
    model_durations = []
    for spec in contracts.STAGES:
        if spec.id not in execution_state.LOCAL_STAGES:
            continue
        record = _object(records.get(spec.id))
        if record.get("status") == "skipped":
            continue
        a, b = _time(record.get("started_at")), _time(record.get("finished_at"))
        if a is None or b is None or b < a or not math.isfinite(b - a):
            continue
        row = {"stage": spec.id, "wall_seconds": round(b - a, 3),
               "started_at": _iso(a), "finished_at": _iso(b),
               "model_capable": spec.id in MODEL_STAGES,
               "timing_basis": "completed_stage_wall_time"}
        rows.append(row)
        if row["model_capable"]:
            model_durations.append(b - a)
    # Do not substitute now, manifest creation/update, file mtime or skipped stages.
    finishes = [_time(_object(records.get(sid)).get("finished_at"))
                for sid in execution_state.LOCAL_STAGES
                if _object(records.get(sid)).get("status") != "skipped"]
    finishes = [value for value in finishes if value is not None]
    starts = [_time(row["started_at"]) for row in rows]
    if not starts or not finishes:
        return {"detected": False, "reason": "completed_timing_unavailable"}
    total_raw = max(finishes) - min(starts)
    model_raw = max(model_durations, default=0)
    # Compare raw durations: rounding 599.9999/179.9999 must not admit a report.
    if total_raw < TOTAL_THRESHOLD_SECONDS or model_raw < MODEL_STAGE_THRESHOLD_SECONDS:
        return {"detected": False, "reason": "below_fixed_thresholds"}
    total, model_max = round(total_raw, 3), round(model_raw, 3)
    signature = _signature(state)
    summary = (f"本地执行完成，阶段时间跨度 {total:g} 秒；最长模型相关阶段 {model_max:g} 秒。"
               "命中固定保守性能调查阈值，不代表历史统计异常或故障；尚未进行 AI 调查。")
    return {"version": VERSION, "detected": True, "id": "performance-" + signature[:24],
            "signature": signature, "kind": "performance", "stage": "performance",
            "status": "detected", "scope": "read_only",
            "investigation_status": "not_started", "investigation_started": False,
            "batch": root.name, "batch_dir": str(root.absolute()),
            "summary": summary, "detail": summary,
            "allowed_actions": ["read_evidence"], "promote_allowed": False,
            "evidence": {"thresholds": {"total_seconds": TOTAL_THRESHOLD_SECONDS,
                                         "model_stage_seconds": MODEL_STAGE_THRESHOLD_SECONDS,
                                         "basis": "fixed_conservative_threshold_not_historical_anomaly"},
                         "metrics": {"total_seconds": total, "model_stage_seconds": model_max,
                                     "started_at": _iso(min(starts)), "finished_at": _iso(max(finishes)),
                                     "stages": rows},
                         "paths": ["workflow/manifest.json"],
                         "limitations": ["阶段墙钟时间可能包含排队、限流等待、重试与本地验收，不能归因为模型推理慢。",
                                         "未建立历史基线；审核差异不是故障；不推断在途模型超时。",
                                         "只读元数据，不读取原卷正文、提示词、候选响应、日志或凭据。"]}}


def observe(folder, manifest=None):
    """Persist only the dedicated report atomically; return existing on duplicate.

    Failures are additive-capability results, never reasons to stop the workflow.
    This API has no promote/repair/continue/upload action and writes no fault ledger.
    """
    report = collect(folder, manifest)
    if not report.get("detected"):
        return report
    root = Path(folder)
    target = _path(root, REPORT_PATH)
    if target is None or not target.parent.is_dir():
        return dict(report, persisted=False, reason="unsafe_report_path")
    temporary = None
    try:
        with _WRITE_LOCK:
            if target.exists():
                old = _read(root, REPORT_PATH)
                if not old:
                    return dict(report, persisted=False, reason="existing_report_unreadable")
                if old.get("signature") == report["signature"]:
                    return old
            if _path(root, REPORT_PATH) is None:
                return dict(report, persisted=False, reason="unsafe_report_path")
            report["persisted"] = True
            temporary = target.parent / (".operations-performance." + uuid.uuid4().hex + ".tmp")
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            temporary = None
        return report
    except (OSError, ValueError, TypeError):
        return dict(report, persisted=False, reason="performance_report_write_failed")
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
