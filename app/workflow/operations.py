"""Durable local operations incidents. All mutations share auto's OS lock.

No model or remote service is invoked here. A receipt is written *before* an
operation; an interrupted request remains unknown and must never be replayed.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
import hashlib
import json
from pathlib import Path
import re
import time
import uuid

from . import contracts, manifest as mf, self_heal, supervisor

LEDGER = "operations.json"
LEASE_SECONDS = 180
ACTIONS = frozenset(("diagnose", "read_evidence", "preview_repair", "apply_repair",
                     "verify", "resume", "record_finding", "finish", "heartbeat",
                     "progress", "release", "cancel"))
REPAIRS = {"repair_source_tables": "apply_source_table_repair",
           "repair_source_options": "apply_source_option_repair",
           "repair_upload_format": "apply_source_format_repair"}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _identity(folder):
    stat = folder.stat()
    workflow = (folder / "workflow").stat()
    return [stat.st_dev, stat.st_ino, stat.st_birthtime if hasattr(stat, "st_birthtime") else 0,
            workflow.st_dev, workflow.st_ino]


def _active(root, folder):
    root, folder = Path(root), Path(folder)
    if root.is_symlink() or folder.is_symlink() or not supervisor._active_folder(root.resolve(), folder.resolve()):
        raise ValueError("inactive_batch: 批次不存在、已归档或路径不安全")
    manifest = mf.load(folder)
    if manifest.get("status") in {"stopped", "cancelled", "archived"}:
        raise ValueError("inactive_batch: 批次已停止")
    return manifest


@contextmanager
def _locked(root, folder, *, execution=False, runner=False):
    root, folder = Path(root), Path(folder)
    _active(root, folder)
    identity = _identity(folder)
    with supervisor._try_file_lock(supervisor._auto_lock_path(folder)) as acquired:
        if not acquired:
            raise ValueError("worker_busy: 批次执行锁已占用")
        _active(root, folder)
        if _identity(folder) != identity:
            raise ValueError("identity_changed: 批次身份已变化")
        # Metadata/heartbeats never contend with an unrelated batch's model.
        # Actual artifact writers HOLD execution/import locks, not just a probe.
        from . import local_agent
        run_guard = supervisor._try_file_lock(folder / "workflow/.run.lock") if execution or runner else nullcontext(True)
        with run_guard as run_acquired:
            if not run_acquired:
                raise ValueError("worker_busy: runner owns batch")
            try:
                with (local_agent._agent_lock(0) if execution else nullcontext()):
                    yield
            except local_agent.AgentError as exc:
                if exc.code == "lock_timeout":
                    raise ValueError("worker_busy: live model/import worker") from exc
                raise


def _load(folder):
    value = supervisor._read(folder / "workflow" / LEDGER)
    if value and value.get("identity") != _identity(folder):
        return {}
    return value


def _save(folder, state):
    if state.get("identity") != _identity(folder):
        raise ValueError("identity_changed: 拒绝迟到写入")
    if not supervisor._write_existing(folder, LEDGER, state):
        raise ValueError("state_write_failed")


def _event(incident, action, outcome, detail="", verification=None):
    incident.setdefault("events", []).append({"timestamp": mf.now_iso(), "action": action,
        "outcome": outcome, "detail": _redact(str(detail))[:2000], "verification": verification})
    incident["events"] = incident["events"][-100:]


def _redact(text):
    text = re.sub(r"(?i)(bearer\s+)[\w.\-]+", r"\1[REDACTED]", text)
    return re.sub(r'(?i)((?:api[_-]?key|token|password|secret|authorization|credential)[\"\s:=]+)[^\s,\"}]+',
                  r'\1[REDACTED]', text)


def _snapshot(folder, stage):
    return _hash(self_heal._snapshot(folder, mf.load(folder), stage))


def lease_active(folder):
    """Read-only guard, called under auto lock BEFORE reconciliation or writes."""
    state = _load(Path(folder))
    incident = state.get("incident") or {}
    return incident.get("status") == "claimed" and incident.get("lease_expires_at", 0) > time.time()


def _expire(incident):
    if incident.get("status") == "claimed" and incident.get("lease_expires_at", 0) <= time.time():
        incident.update(status="queued", eligible=True, attempt_id=None, lease_expires_at=0,
                        queue_generation=incident.get("queue_generation", 0) + 1)
        _event(incident, "lease", "expired", "租约超时，允许重新认领")


def _discover(folder):
    state = _load(folder) or {"identity": _identity(folder), "incident": None, "receipts": [], "failures": {}}
    incident = state.get("incident")
    if incident:
        _expire(incident)
        if incident.get("status") in {"claimed", "queued"}:
            return state
        if incident.get("status") == "awaiting_execution":
            current = _snapshot(folder, incident["stage"])
            if current != incident.get("snapshot"):
                incident.update(status="queued", eligible=True,
                                queue_generation=incident.get("queue_generation", 0) + 1)
                _event(incident, "resume", "queued", "正常流水线执行产生新证据，重新认领核验")
            return state
    manifest = mf.load(folder)
    stage, detail = self_heal._candidate(folder, manifest)
    if not stage or stage in self_heal.BOUNDARIES or stage == "review_gate":
        return state
    record = manifest.get("stages", {}).get(stage) or {}
    if record.get("status") == mf.RUNNING:
        return state
    if stage == "review" and record.get("status") in {mf.NEEDS_REVIEW, mf.COMPLETED, mf.APPROVED}:
        return state
    signature = hashlib.sha256((stage + "\n" + detail).encode()).hexdigest()[:16]
    # Count actual failed repair events, independently of resettable watchdog budgets.
    for event in self_heal._load(folder).get("events", []):
        if (event.get("stage") != stage or event.get("signature") != signature
                or event.get("outcome") not in {"repair_failed", "retry_failed"}
                or event.get("error_code") in {"rate_limited", "rate_limit", "model_cooldown"}):
            continue
        key = _hash({k: event.get(k) for k in ("at", "stage", "signature", "attempt")})
        state["failures"][key] = {"stage": stage, "signature": signature}
    count = sum(x == {"stage": stage, "signature": signature} for x in state["failures"].values())
    if count and not (incident and incident.get("signature") == signature):
        state["incident"] = {"incident_id": uuid.uuid4().hex, "batch_name": folder.name,
            "batch_dir": str(folder), "status": "queued", "eligible": True, "stage": stage,
            "signature": signature, "detail": _redact(detail), "self_heal_failures": count,
            "created_at": mf.now_iso(), "events": [], "fence": 0, "queue_generation": 1}
        _event(state["incident"], "discover", "queued", detail)
    return state


def view(root, folder, *, discover=True):
    folder = Path(folder)
    with _locked(root, folder):
        state = _discover(folder) if discover else _load(folder)
        if state:
            _save(folder, state)
        return {"incident": state.get("incident"), "receipts": state.get("receipts", [])}


def incidents(root):
    root = Path(root)
    found = []
    if root.is_symlink() or not root.is_dir():
        return {"incidents": []}
    for folder in sorted(root.iterdir()):
        if not supervisor._active_folder(root, folder):
            continue
        try:
            incident = view(root, folder)["incident"]
            if (incident and incident.get("status") == "queued"
                    and incident.get("not_before", 0) <= time.time()):
                with supervisor._try_file_lock(folder / "workflow/.run.lock") as available:
                    if available:
                        found.append(incident)
        except (ValueError, OSError):
            continue
    return {"incidents": found}


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError("invalid_" + label)
    return value


def claim(root, folder, payload):
    folder = Path(folder)
    session = _text(payload.get("session_id"), "session_id")
    with _locked(root, folder, runner=True):
        state = _discover(folder)
        incident = state.get("incident") or {}
        if incident.get("incident_id") != payload.get("incident_id"):
            raise ValueError("incident_mismatch")
        if incident.get("status") == "claimed" and incident.get("session_id") == session:
            return dict(incident)
        if incident.get("status") != "queued" or incident.get("not_before", 0) > time.time():
            raise ValueError("incident_not_claimable")
        if any(r.get("status") == "unknown" for r in state.get("receipts", [])):
            _event(incident, "claim", "unknown_quarantine", "未确定动作结果，禁止自动重试")
            _save(folder, state)
            raise ValueError("unknown_receipt_quarantine")
        incident.update(status="claimed", eligible=False, session_id=session,
                        attempt_id=uuid.uuid4().hex, fence=incident.get("fence", 0) + 1,
                        lease_expires_at=time.time() + LEASE_SECONDS,
                        snapshot=_snapshot(folder, incident["stage"]))
        _event(incident, "claim", "claimed", session)
        _save(folder, state)
        return dict(incident)


def _read_evidence(folder, data):
    relative = data.get("path")
    if not isinstance(relative, str) or "\\" in relative or any(p in {"", ".", ".."} for p in relative.split("/")):
        raise ValueError("unsafe_evidence_path")
    # Exact evidence filenames plus stage logs; no arbitrary config/agent-run files.
    allowed = set(self_heal.SOURCE_FILES) | set(self_heal.REPORTS)
    if data.get("scope") == "plugin":
        if not re.fullmatch(r"app/workflow/[a-z_]+\.py", relative):
            raise ValueError("source_not_allowlisted")
        path = supervisor._regular_file(Path(__file__).resolve().parents[2], relative)
    else:
        if relative not in allowed and relative not in {"workflow/logs/" + s.id + ".log" for s in contracts.STAGES}:
            raise ValueError("evidence_not_allowlisted")
        path = supervisor._regular_file(folder, relative)
    if path is None:
        raise ValueError("evidence_missing")
    offset = max(0, min(int(data.get("offset", 0)), 10000000))
    limit = max(1, min(int(data.get("limit", 12000)), 24000))
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        stream.seek(offset)
        text = stream.read(limit)
    return {"path": relative, "offset": offset, "text": _redact(text), "limit": limit}


def _verify(folder, incident):
    stage = contracts.STAGE_BY_ID[incident["stage"]]
    manifest = mf.load(folder)
    record = manifest.get("stages", {}).get(stage.id, {})
    stale, reason = mf.is_stale(folder, manifest, stage)
    candidate, _ = self_heal._candidate(folder, manifest)
    from .handoff import inspect_stage
    checked = inspect_stage(folder, manifest, stage.id)
    verified = (record.get("status") in {mf.COMPLETED, mf.APPROVED}
                and not stale and not mf.missing_produces(folder, stage) and candidate != stage.id
                and checked.get("passed") is True
                and record.get("input_hashes") == mf.input_hashes(folder, manifest, stage))
    return {"verified": bool(verified), "evidence_id": uuid.uuid4().hex,
            "stage": stage.id, "snapshot": _snapshot(folder, stage.id),
            "reason": reason if stale else ("current validated stage evidence" if verified else "阶段尚未真实通过")}


def action(root, folder, payload, *, enqueue=None):
    folder = Path(folder)
    name = payload.get("action")
    if name not in ACTIONS:
        raise ValueError("unsupported_action")
    request = _text(payload.get("request_id"), "request_id")
    data = payload.get("input") or {}
    if not isinstance(data, dict):
        raise ValueError("invalid_input")
    resume = False
    writes_artifacts = name == "apply_repair" or name == "resume" and bool(data.get("strategy"))
    with _locked(root, folder, execution=writes_artifacts,
                 runner=name in {"preview_repair", "verify", "finish"}):
        state = _load(folder)
        incident = state.get("incident") or {}
        if incident.get("incident_id") != payload.get("incident_id"):
            raise ValueError("incident_mismatch")
        # Receipt lookup precedes lease checks: completed/unknown requests never run twice.
        old = next((r for r in state.get("receipts", []) if r["request_id"] == request), None)
        fingerprint = _hash(payload)
        if old:
            if old.get("fingerprint") != fingerprint:
                raise ValueError("request_id_conflict")
            return dict(old)
        if any(r.get("status") == "unknown" for r in state.get("receipts", [])) and name not in {"release", "cancel", "heartbeat", "progress", "read_evidence", "diagnose", "record_finding"}:
            raise ValueError("unknown_receipt_quarantine")
        _expire(incident)
        if (incident.get("status") != "claimed" or incident.get("attempt_id") != payload.get("attempt_id")
                or incident.get("session_id") != payload.get("session_id")):
            _save(folder, state)
            raise ValueError("stale_lease")
        if name not in {"release", "cancel"} and incident.get("snapshot") != _snapshot(folder, incident["stage"]):
            raise ValueError("inputs_changed: release then claim a fresh snapshot")
        receipt = {"request_id": request, "status": "unknown", "evidence": None, "fingerprint": fingerprint}
        state.setdefault("receipts", []).append(receipt)
        _save(folder, state)
        mutating = False
        try:
            result = {}
            if name == "diagnose":
                result = {"stage": incident["stage"], "detail": incident["detail"],
                          "repairs": list(REPAIRS), "evidence_paths": sorted(set(self_heal.SOURCE_FILES) | set(self_heal.REPORTS))}
            elif name == "read_evidence":
                result = _read_evidence(folder, data)
            elif name in {"preview_repair", "apply_repair"}:
                from . import self_heal_actions
                repair = data.get("repair")
                if repair not in REPAIRS:
                    raise ValueError("unsupported_repair")
                if name == "preview_repair":
                    from . import legacy_format
                    prepare = {"repair_source_tables": self_heal_actions._prepare_tables,
                               "repair_source_options": self_heal_actions._prepare_options,
                               "repair_upload_format": legacy_format.prepare}[repair]
                    prepared = prepare(folder)
                    result = {key: prepared.get(key) for key in ("passed", "changes", "refused", "input_sha256", "checks", "errors")}
                    result.update(repair=repair, snapshot=incident["snapshot"], applied=False)
                    incident["preview"] = result
                else:
                    preview = incident.get("preview") or {}
                    if preview.get("repair") != repair or preview.get("snapshot") != incident["snapshot"] or preview.get("passed") is not True:
                        raise ValueError("current_successful_preview_required")
                    snapshot = self_heal._snapshot(folder, mf.load(folder), incident["stage"])
                    expected = {key: value for key, value in snapshot["files"].items() if not key.startswith("input:")}
                    mutating = True
                    result = getattr(self_heal_actions, REPAIRS[repair])(folder, expected_inputs=expected)
                    incident.pop("verification", None)
            elif name == "verify":
                result = _verify(folder, incident)
                incident["verification"] = result
            elif name == "finish":
                outcome = data.get("outcome")
                if outcome not in {"resolved", "needs_deep_repair"} or not isinstance(data.get("summary"), str) or not data["summary"].strip():
                    raise ValueError("invalid_finish")
                previous = incident.get("verification") or {}
                if outcome == "resolved" and not (previous.get("verified") is True and previous.get("evidence_id")
                        and previous.get("snapshot") == incident["snapshot"] and _verify(folder, incident)["verified"]):
                    raise ValueError("verification_required")
                incident.update(status=outcome, eligible=False, attempt_id=None, lease_expires_at=0)
                result = {"outcome": outcome, "summary": _redact(data["summary"])[:2000], "verification": previous,
                          "verified": outcome == "resolved", "evidence_id": previous.get("evidence_id")}
            elif name in {"release", "cancel", "resume"}:
                if name == "resume" and enqueue is None:
                    raise ValueError("resume_unavailable")
                if name == "resume" and data.get("strategy"):
                    strategy = data["strategy"]
                    if strategy not in {"retry_stage_with_fresh_context", "upstream_rederive", "rejudge_knowledge"}:
                        raise ValueError("unsupported_resume_strategy")
                    reason = str(data.get("reason") or "")[:2000]
                    if not reason.strip():
                        raise ValueError("resume_reason_required")
                    manifest = mf.load(folder)
                    mutating = True
                    result = self_heal._apply(folder, manifest, incident["stage"], incident["detail"],
                        {"action": strategy, "reason": reason}, incident.get("fence", 1),
                        self_heal._snapshot(folder, manifest, incident["stage"]))
                incident.update(status={"cancel": "cancelled", "resume": "awaiting_execution", "release": "queued"}[name],
                                eligible=name == "release", attempt_id=None, lease_expires_at=0)
                if name == "release":
                    incident["queue_generation"] = incident.get("queue_generation", 0) + 1
                    incident["release_count"] = incident.get("release_count", 0) + 1
                    incident["not_before"] = time.time() + min(900, 60 * 3 ** min(incident["release_count"] - 1, 3))
                result = {**result, "released": True, "reclaim_required": True}
                resume = name == "resume"
                if resume:
                    state["resume_outbox"] = {"request_id": request, "identity": state["identity"], "created_at": time.time()}
            elif name == "record_finding":
                result = {key: _redact(str(data.get(key) or ""))[:4000]
                          for key in ("summary", "repro", "expected", "actual", "paths")}
                incident.setdefault("findings", []).append(result)
                incident["findings"] = incident["findings"][-30:]
            else:
                result = {"detail": _redact(str(data.get("detail") or data.get("summary") or ""))[:2000]}
            if incident.get("status") == "claimed":
                incident["lease_expires_at"] = time.time() + LEASE_SECONDS
            incident["snapshot"] = _snapshot(folder, incident["stage"])
            receipt.update(status="unknown" if resume else "completed", evidence=result)
            _event(incident, name, "completed", result.get("detail") or result.get("summary") or "", result if name == "verify" else None)
        except Exception as exc:
            status = "unknown" if mutating else "failed"
            receipt.update(status=status, evidence={"error": _redact(str(exc))[:1000]})
            _event(incident, name, status, str(exc))
        _save(folder, state)
    if resume:
        # Durable outbox is replayable; only the FIFO admission is retried, never
        # the strategy/action. FIFO deduplicates currently queued/active batches.
        _dispatch_resume(root, folder, enqueue)
        latest = _load(folder)
        receipt = next((r for r in latest.get("receipts", []) if r["request_id"] == request), receipt)
    return dict(receipt)


def _dispatch_resume(root, folder, enqueue):
    state = _load(folder)
    outbox = state.get("resume_outbox")
    if not outbox:
        return
    if outbox.get("identity") != _identity(folder):
        return
    try:
        incident = state.get("incident") or {}
        progressed = incident.get("snapshot") != _snapshot(folder, incident["stage"])
        expectation = {"identity": outbox["identity"], "incident_id": incident["incident_id"]}
        admission = {"observed_progress": True} if progressed else enqueue(expectation)
        if (not isinstance(admission, dict) or admission.get("external") or admission.get("error")
                or not any(admission.get(key) is True for key in ("queued", "running", "started", "observed_progress"))):
            return  # No definite admission; keep outbox and unknown receipt.
    except Exception:
        # Persisted unknown + outbox survive; do not falsely declare completion.
        return
    try:
        with _locked(root, folder):
            state = _load(folder)
            if state.get("resume_outbox") != outbox:
                return
            receipt = next((r for r in state.get("receipts", []) if r["request_id"] == outbox["request_id"]), None)
            if receipt:
                receipt.update(status="completed", evidence={**(receipt.get("evidence") or {}), "queued": True})
            state.pop("resume_outbox", None)
            _event(state["incident"], "resume", "queued", "已交给原FIFO本地执行器")
            _save(folder, state)
    except (ValueError, OSError, RuntimeError):
        pass  # Worker already owns lock; durable outbox is reconciled next read.


def observe_execution(folder):
    """Called by the FIFO worker while it still holds the original auto lock."""
    folder = Path(folder)
    state = _load(folder)
    incident = state.get("incident") or {}
    if incident.get("status") != "awaiting_execution":
        return
    changed = incident.get("snapshot") != _snapshot(folder, incident["stage"])
    incident.update(status="queued", eligible=True,
                    queue_generation=incident.get("queue_generation", 0) + 1,
                    not_before=0 if changed else time.time() + 60)
    outbox = state.pop("resume_outbox", None)
    if outbox:
        for receipt in state.get("receipts", []):
            if receipt["request_id"] == outbox["request_id"]:
                receipt.update(status="completed", evidence={**(receipt.get("evidence") or {}), "executed": True})
    _event(incident, "resume", "executed", "原流水线本地执行已结束；须重新认领并核验")
    _save(folder, state)


def recover_resumes(root, enqueue):
    """Replay only FIFO admission after a crash; never replay a repair strategy."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        return
    for folder in root.iterdir():
        if supervisor._active_folder(root, folder):
            try:
                _active(root, folder)
                _dispatch_resume(root, folder, lambda expected: enqueue(folder.name, folder, operations_expectation=expected))
            except (ValueError, OSError, RuntimeError):
                continue
