"""Durable local operations incidents. All mutations share auto's OS lock.

No model or remote service is invoked here. A receipt is written *before* an
operation; an interrupted request remains unknown and must never be replayed.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import uuid

from . import contracts, manifest as mf, self_heal, supervisor

LEDGER = "operations.json"
LEASE_SECONDS = 180
ACTIONS = frozenset(("diagnose", "read_evidence", "preview_repair", "apply_repair",
                     "verify", "resume", "regenerate_units", "record_finding", "finish", "heartbeat",
                     "progress", "release", "cancel", "request_code_repair",
                     "code_repair", "install_code_repair"))
REGENERABLE = frozenset(("answers", "explanations", "types", "knowledge", "audio"))
REPAIRS = {"repair_source_tables": "apply_source_table_repair",
           "repair_source_options": "apply_source_option_repair",
           "repair_upload_format": "apply_source_format_repair"}
TRANSIENT = re.compile(r"(?i)\b(rate[_ -]?limit|timeout|timed out|busy|locked|unavailable|"
                       r"temporar|connection|network|cooldown)\b")


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


def _process_identity():
    """Interpreter lifetime, stable across module reloads and distinct after fork/exec.

    Kept on sys rather than this module: reimporting code is not restart proof.
    Never accept a caller supplied generation or a lease/session change as proof.
    """
    identity = getattr(sys, "_mdu_operations_process", None)
    if not identity or identity["pid"] != os.getpid():
        identity = {"pid": os.getpid(), "generation": uuid.uuid4().hex}
        sys._mdu_operations_process = identity
    return dict(identity)


def _source_root():
    """Running plugin checkout. Patched in tests; never a user batch path."""
    return Path(__file__).resolve().parents[2]


def _code_files():
    """Allowlisted production sources, excluding the locked control plane."""
    from . import operations_development
    return operations_development.files(_source_root())


def _code_fingerprint():
    digest = hashlib.sha256()
    for relative, path in _code_files().items():
        digest.update(relative.encode())
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"unreadable")
    return digest.hexdigest()


def _downstream(stage):
    descendants, frontier = set(), {stage}
    while frontier:
        current = frontier.pop()
        for spec in contracts.STAGES:
            if current in spec.depends_on and spec.id not in descendants:
                descendants.add(spec.id)
                frontier.add(spec.id)
    return descendants


def _report_files(stage):
    return {"format_gate": {"07_review/upload-format.json"},
            "validate": {"07_review/validation-report.json"},
            "structure_gate": {"07_review/structure-gate.json"},
            "review": {"07_review/review-gate.json"},
            "review_gate": {"07_review/review-gate.json"}}.get(stage, set())


def _packet(folder, incident):
    """Self-contained failure packet: what failed, where, what changed, what is
    still independent. Bounded so a receipt never becomes a data dump."""
    folder = Path(folder)
    stage = incident["stage"]
    manifest = mf.load(folder)
    snapshot = self_heal._snapshot(folder, manifest, stage)
    issues = [issue for issue in self_heal._issues(folder)
              if not _report_files(stage) or issue.get("report") in _report_files(stage)]
    detail = str(incident.get("detail") or "")
    unit_ids, units = self_heal._affected_units(folder, detail + json.dumps(issues, ensure_ascii=False), stage)
    completed = {key for key, record in manifest.get("stages", {}).items()
                 if record.get("status") in {mf.COMPLETED, mf.APPROVED}}
    downstream = _downstream(stage)
    attempts = [row for row in incident.get("attempts", [])][-10:]
    return {
        "batch_name": folder.name,
        "batch_dir": str(folder),
        "stage": stage,
        "stage_title": contracts.STAGE_BY_ID[stage].title,
        "signature": incident.get("signature"),
        "detail": detail[:4000],
        "failed_checks": [{"report": issue.get("report"), "code": issue.get("code"),
                           "check": issue.get("check"), "unit_id": issue.get("unit_id"),
                           "number": issue.get("number"), "message": str(issue.get("message") or "")[:600]}
                          for issue in issues[:12]],
        "affected_unit_ids": list(unit_ids)[:40],
        "affected_units": [{"unit_id": unit["unit_id"], "numbers": unit.get("numbers"),
                            "stem_chars": len(str(unit.get("stem") or ""))} for unit in units[:12]],
        "inputs": snapshot.get("stage_inputs"),
        "input_files": {key: value for key, value in snapshot.get("files", {}).items()
                        if key.startswith("input:")},
        "produced_files": {key: value for key, value in snapshot.get("files", {}).items()
                           if not key.startswith("input:")},
        "completed_independent_stages": sorted(completed - downstream - {stage}),
        "blocked_downstream_stages": sorted(downstream),
        "attempts": attempts,
        "budgets": _budgets(incident),
        "escalation": incident.get("escalation"),
        "code_fingerprint": _code_fingerprint(),
        "repairs": sorted(REPAIRS),
        "resume_strategies": ["retry_stage_with_fresh_context", "upstream_rederive", "rejudge_knowledge"],
    }


def _evidence(folder, incident):
    """Evidence identity for repeat detection: real inputs plus current code.

    Changing an original, an upstream product or the plugin code lifts the
    block; retrying the same strategy on identical evidence does not.
    """
    snapshot = self_heal._snapshot(folder, mf.load(folder), incident["stage"])
    return _hash({"stage_inputs": snapshot.get("stage_inputs"),
                  "inputs": {key: value for key, value in snapshot.get("files", {}).items()
                             if key.startswith("input:")},
                  "code": _code_fingerprint()})


def _attempt(incident, kind, name, evidence, outcome, error=""):
    incident.setdefault("attempts", []).append({
        "kind": kind, "name": name, "evidence": evidence,
        "outcome": outcome, "transient": bool(TRANSIENT.search(str(error))),
        "at": mf.now_iso()})
    incident["attempts"] = incident["attempts"][-40:]


def _repeated(incident, kind, name, evidence):
    return [row for row in incident.get("attempts", [])
            if row.get("kind") == kind and row.get("name") == name
            and row.get("evidence") == evidence and not row.get("transient")
            and (kind != "regenerate" or row.get("outcome") == "failed")]


def _block_repeat(incident, kind, name, evidence):
    repeated = _repeated(incident, kind, name, evidence)
    if not repeated:
        return
    incident["escalation"] = {
        "kind": "code_repair",
        "reason": f"{kind}:{name} 已在相同输入与代码上失败 {len(repeated)} 次",
        "at": mf.now_iso(),
        "attempts": len(repeated),
    }
    raise ValueError(f"repeat_without_new_evidence: {name} 未改变输入、证据或代码，禁止原样重试；"
                     "请 request_code_repair 进入隔离修复，或改用诊断支持的其他策略")


# Cross-round budget: an incident survives attempt releases, so a single bad
# blocker could otherwise be retried forever by fresh 40-action attempts. These
# caps count what the incident has already spent, not what this attempt spent.
BUDGET_ATTEMPTS = 24
BUDGET_INSTALLS = 2
BUDGET_REGENERATIONS = 6


def _budgets(incident):
    rows = incident.get("attempts") or []
    return {
        "attempts": sum(1 for row in rows if row.get("outcome") != "refused"),
        "attempts_limit": BUDGET_ATTEMPTS,
        "installs": sum(1 for row in rows if row.get("kind") == "install" and row.get("outcome") == "installed"),
        "installs_limit": BUDGET_INSTALLS,
        "regenerations": sum(1 for row in rows if row.get("kind") == "regenerate"
                             and (row.get("scheduled_at") or row.get("outcome") == "scheduled")),
        "regenerations_limit": BUDGET_REGENERATIONS,
    }


def _check_budget(incident, kind):
    budgets = _budgets(incident)
    if budgets["attempts"] >= budgets["attempts_limit"]:
        incident["escalation"] = {"kind": "human", "at": mf.now_iso(),
                                  "reason": f"该故障已尝试 {budgets['attempts']} 次，超过上限 {budgets['attempts_limit']}"}
        raise ValueError(f"attempt_budget_exhausted: 本次故障累计 {budgets['attempts']} 次尝试，"
                         "超过跨轮次上限；请记录证据并交人工处理，不要继续重试")
    if kind == "install" and budgets["installs"] >= budgets["installs_limit"]:
        raise ValueError(f"install_budget_exhausted: 本次故障已安装 {budgets['installs']} 次，"
                         "同一故障最多安装两次；请记录证据并交人工处理")
    if kind == "regenerate" and budgets["regenerations"] >= budgets["regenerations_limit"]:
        raise ValueError(f"regenerate_budget_exhausted: 本次故障已局部重生成 {budgets['regenerations']} 次，"
                         "超过上限；请检查范围是否过窄或证据是否有误")


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


def _reconcile_stage_refusal(state):
    """Settle the legacy, proven pre-write answers refusal; never replay it."""
    incident = state.get("incident") or {}
    if incident.get("stage") != "answers" or state.get("resume_outbox"):
        return
    for receipt in state.get("receipts", []):
        if (receipt.get("status") == "unknown" and receipt.get("fingerprint")
                and receipt.get("evidence") == {
                    "error": "Knowledge rejudgment is not applicable to this stage"}):
            receipt.update(status="failed", reconciliation={
                "original_status": "unknown", "reason": "proven_prewrite_stage_refusal",
                "at": mf.now_iso(), "replayed": False})
            _event(incident, "reconcile", "failed", "已核实旧answers知识点重判在写入前拒绝；保留原错误，不重放请求")
    reconciled = [r["reconciliation"] for r in state.get("receipts", [])
                  if (r.get("reconciliation") or {}).get("reason") == "proven_prewrite_stage_refusal"
                  and not r["reconciliation"].get("queue_generation")]
    if (reconciled and incident.get("status") == "queued"
            and not any(r.get("status") == "unknown" for r in state.get("receipts", []))):
        incident["queue_generation"] = incident.get("queue_generation", 0) + 1
        for record in reconciled:
            record["queue_generation"] = incident["queue_generation"]
        _event(incident, "reconcile", "queued", "写前拒绝已结算，允许新代次认领；不重放旧动作")


def _discover(folder):
    state = _load(folder) or {"identity": _identity(folder), "incident": None, "receipts": [], "failures": {}}
    _reconcile_stage_refusal(state)
    incident = state.get("incident")
    if incident:
        _expire(incident)
        # A model's escalation cannot override current consumer verification of
        # this incident's stage. Retain its finding and original disposition.
        if (incident.get("status") == "needs_deep_repair"
                and (incident.get("verification") or {}).get("verified") is True
                and not any(r.get("status") == "unknown" for r in state.get("receipts", []))):
            checked = _verify(folder, incident)
            if checked["verified"]:
                incident.update(status="resolved", eligible=False, verification=checked,
                                prior_disposition="needs_deep_repair")
                _event(incident, "reconcile", "resolved",
                       "历史事件阶段已通过当前消费者复验；保留模型发现，审核差异不视为通过", checked)
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
    try:
        with _locked(root, folder):
            state = _discover(folder) if discover else _load(folder)
            if state:
                _save(folder, state)
    except ValueError as exc:
        if not str(exc).startswith("worker_busy:"):
            raise
        # Reads may race a live batch worker. Return only the last persisted
        # snapshot; discovery and ledger writes still require the batch lock.
        state = _load(folder)
    return {"incident": state.get("incident") if state else None,
            "receipts": state.get("receipts", []) if state else []}


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
        process = _process_identity()
        if (incident.get("status") == "claimed" and incident.get("execution_process")
                and incident["execution_process"] != process):
            incident.update(status="queued", attempt_id=None, lease_expires_at=0, not_before=0,
                            queue_generation=incident.get("queue_generation", 0) + 1)
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
                        snapshot=_snapshot(folder, incident["stage"]), execution_process=process)
        record = _code_record(folder)
        if (record and record.get("status") == "installed_pending_restart"
                and record.get("install_process") and record["install_process"] != process
                and record.get("install_attempt_id") != incident["attempt_id"]):
            record.update(status="restarted_pending_verify", restart_process=process,
                          restart_attempt_id=incident["attempt_id"], restarted_at=mf.now_iso())
            _save_code_record(folder, record)
            incident["code_repair"] = {**(incident.get("code_repair") or {}),
                                       "status": record["status"], "restarted_at": record["restarted_at"]}
            _event(incident, "restart", "acknowledged", "新执行进程已重新认领；仍须原检查复验")
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


def _code_state_path(folder):
    return Path(folder) / "workflow" / "code-repair.json"


def _code_root(folder):
    key = hashlib.sha256(str(Path(folder).resolve()).encode()).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / "mdu-operations-dev" / key


def _code_record(folder):
    try:
        value = json.loads(_code_state_path(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) and value.get("job_id") and value.get("path") else None


def _save_code_record(folder, record):
    path = _code_state_path(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temporary, path)


def _settle_code_repair(folder, incident, outcome):
    """Close the repair record with the incident's final disposition.

    An install stays `installed_pending_restart` until the incident is really
    settled, so a missing verification cannot silently look like a fixed batch.
    """
    record = _code_record(folder)
    if not record or record.get("status") not in {"workspace_ready", "tests_passed", "prepared",
                                                  "installed_pending_restart", "restarted_pending_verify", "tests_failed"}:
        return
    status = {"resolved": "verified", "needs_human": "needs_human",
              "needs_deep_repair": "failed"}.get(outcome)
    if not status:
        return
    record["status"] = status
    record["settled_at"] = mf.now_iso()
    _save_code_record(folder, record)
    try:
        from . import operations_development as dev
        job = dev.load(record["path"])
        job["status"] = status
        dev.save(job, record["path"])
    except (OSError, ValueError, KeyError):
        pass
    repair = incident.get("code_repair") or {}
    repair["status"] = status
    incident["code_repair"] = repair


def _code_job(folder):
    record = _code_record(folder)
    if not record:
        raise ValueError("code_repair_not_requested")
    from . import operations_development
    job = operations_development.load(record["path"])
    if job["job_id"] != record["job_id"] or not Path(job["workspace"]).is_dir():
        raise ValueError("code_repair_workspace_missing: 重新 request_code_repair")
    return record, job


def _code_status(record, job):
    from . import operations_development as dev
    return {"job_id": job["job_id"], "status": record.get("status"),
            "workspace": job["workspace"], "base_fingerprint": job["base"].get("code_fingerprint"),
            "fingerprint": dev.fingerprint(job),
            "tests": [{"passed": t["passed"], "tests": t["tests"], "returncode": t["returncode"],
                       "at": t["at"]} for t in job.get("tests", [])[-3:]],
            "plan": job.get("plan"), "install": job.get("install"),
            "writes": sorted(job.get("writes", {}))}


def _regenerate_units(folder, incident, data):
    """Scoped regeneration: rerun only the affected units of one content stage.

    The stage still owns its own model call and validation; this action only
    narrows the feedback and requeues that stage instead of the whole paper.
    """
    from .agent_tasks import ARTIFACTS, confirmed_units
    target = str(data.get("target_stage") or "")
    detail = str(incident.get("detail") or "")
    if not target:
        target = self_heal._error_producer(folder, incident["stage"], detail) or incident["stage"]
    if target in self_heal.BOUNDARIES:
        raise ValueError("regenerate_units_refuses_boundary")
    if target not in ARTIFACTS or target not in REGENERABLE:
        raise ValueError("regenerate_units_stage_unsupported: 仅支持答案/解析/题型/知识点/听力，"
                         f"当前目标为 {target or '未知'}；其他阶段请用 resume 或隔离代码修复")
    try:
        known = {unit["unit_id"] for unit in confirmed_units(folder)}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ValueError("regenerate_units_units_unavailable: 无法确认当前题目单元") from exc
    requested = [str(unit) for unit in (data.get("unit_ids") or [])]
    if requested:
        unknown = [unit for unit in requested if unit not in known]
        if unknown:
            raise ValueError("regenerate_units_unknown_units: " + "、".join(unknown[:5]))
        unit_ids = list(dict.fromkeys(requested))
    else:
        unit_ids, _ = self_heal._affected_units(
            folder, detail + "\n" + json.dumps(self_heal._issues(folder), ensure_ascii=False), incident["stage"])
        unit_ids = [unit for unit in unit_ids if unit in known]
    if not unit_ids:
        raise ValueError("regenerate_units_requires_scope: 证据未定位到受影响题目；"
                         "请从 packet.affected_unit_ids 指定，或改用 resume 策略")
    evidence = _evidence(folder, incident)
    name = target + ":" + ",".join(sorted(unit_ids))
    for row in incident.get("attempts", []):
        if (row.get("kind") == "regenerate" and row.get("name") == name
                and row.get("evidence") == evidence and row.get("outcome") == "scheduled"):
            return {"status": "already_scheduled", "stage": target, "unit_ids": unit_ids,
                    "notes": "同一批题目已在相同的输入与代码上排队，避免重复入队；等待执行后复验"}
    _block_repeat(incident, "regenerate", name, evidence)
    _check_budget(incident, "regenerate")
    reason = str(data.get("reason") or "").strip()[:500] or detail.strip()[:500] or "局部重新生成受影响题目"
    attempt = len(incident.get("attempts", [])) + 1
    manifest = mf.load(folder)
    # Feedback itself writes; retain this flag through every uncertain exception.
    incident["_regenerate_started"] = True
    feedback = self_heal._feedback(folder, target, reason, detail, attempt, unit_ids=unit_ids)
    self_heal._requeue(folder, manifest, target,
                       "AI 局部重生成 %d 题：%s" % (len(unit_ids), reason[:120]))
    _attempt(incident, "regenerate", name, evidence, "scheduled")
    incident["attempts"][-1].update(stage=target, unit_ids=unit_ids, scheduled_at=mf.now_iso())
    incident.pop("_regenerate_started", None)
    incident.pop("verification", None)
    return {"status": "requeued", "stage": target, "unit_ids": unit_ids,
            "unit_count": len(unit_ids), "feedback_path": feedback,
            "reason": reason[:200],
            "notes": "只重跑该阶段的这些单元；其余已完成产物保留，下游按依赖重新校验"}


def _request_code_repair(folder, incident, data):
    from . import operations_development as dev
    _check_budget(incident, "repair")
    existing = _code_record(folder)
    if existing and existing.get("status") not in {"failed", "verified", "needs_human", "rolled_back"}:
        record, job = _code_job(folder)
        return {"reused": True, "job_id": job["job_id"], "status": record.get("status"),
                "workspace": job["workspace"], "base": job["base"].get("code_fingerprint"),
                "instructions": "复用现有工作区；不得并行创建第二个。"}
    root = _source_root()
    parent = _code_root(folder)
    job = dev.create(root, parent)
    record = {"job_id": job["job_id"], "path": str(parent / (job["job_id"] + ".json")),
              "workspace": job["workspace"], "status": "workspace_ready",
              "created_at": mf.now_iso(), "incident_id": incident["incident_id"],
              "stage": incident["stage"], "summary": _redact(str(data.get("summary") or ""))[:2000],
              "history": ([*(existing.get("history") or []),
                           {k: v for k, v in existing.items() if k != "history"}] if existing else [])}
    _save_code_record(folder, record)
    incident["code_repair"] = {"job_id": job["job_id"], "status": "workspace_ready",
                               "created_at": record["created_at"]}
    return {"job_id": job["job_id"], "status": "workspace_ready", "workspace": job["workspace"],
            "base": job["base"].get("code_fingerprint"), "source_root": job["source_root"],
            "allowed": sorted(dev.files(root)), "locked": sorted(dev.LOCKED),
            "next": ["code_repair operation=read/write 复现并修复",
                     "code_repair operation=test 运行相关 unittest（必须通过）",
                     "code_repair operation=diff 检查改动范围",
                     "code_repair operation=prepare 生成安装计划",
                     "install_code_repair confirm=true 由受控宿主安装并重启"]}


def _code_repair(folder, incident, data):
    from . import operations_development as dev
    record, job = _code_job(folder)
    operation = data.get("operation")
    if operation == "status":
        return _code_status(record, job)
    if record.get("status") in {"installed_pending_restart", "restarted_pending_verify", "failed", "verified", "needs_human"}:
        raise ValueError("code_repair_closed_or_pending_restart: 先重启后端并复验，再 request_code_repair")
    result = {}
    if operation == "read":
        result = dev.read(job, data.get("path"), offset=data.get("offset", 0), limit=data.get("limit", 12000))
        result["text"] = result["text"][:12000]
    elif operation == "write":
        result = dev.write(job, data.get("path"), data.get("content"), expected_sha=data.get("expected_sha"))
    elif operation == "test":
        result = dev.run_tests(job, data.get("tests"))
        result["output"] = result["output"][-8000:]
        record["status"] = "tests_passed" if result["passed"] else "tests_failed"
    elif operation == "diff":
        result = dev.diff(job)
        result["patch"] = result["patch"][:6000]
    elif operation == "prepare":
        result = dev.prepare_install(job)
        record["status"] = "prepared"
    elif operation == "rollback":
        result = dev.rollback(job, backup_dir=_code_root(folder))
        record["status"] = "rolled_back"
    else:
        raise ValueError("unsupported_code_operation")
    if operation in {"write", "test", "prepare"}:
        record["status"] = {"write": "workspace_ready", "test": record.get("status"),
                            "prepare": "prepared"}[operation]
    _save_code_record(folder, record)
    dev.save(job, record["path"])
    result["status"] = record["status"]
    return result


def _install_code_repair(folder, incident, data):
    from . import operations_development as dev
    if data.get("confirm") is not True:
        raise ValueError("install_confirmation_required")
    if os.environ.get("MD_OPERATIONS_INSTALL") != "1":
        raise ValueError("install_disabled: 宿主须暂停源码监听并设置 MD_OPERATIONS_INSTALL=1")
    _check_budget(incident, "install")
    record, job = _code_job(folder)
    if record.get("status") != "prepared":
        raise ValueError("current_install_plan_required")
    plan = job.get("plan") or {}
    if not plan or plan.get("code_fingerprint") != dev.fingerprint(job):
        raise ValueError("current_install_plan_required")
    incident["_install_started"] = True
    installed = dev.install(job, backup_dir=_code_root(folder), allow=True)
    record.update(status="installed_pending_restart", installed_at=mf.now_iso(), plan_id=plan["plan_id"],
                  install_process=_process_identity(), install_attempt_id=incident["attempt_id"])
    _save_code_record(folder, record)
    dev.save(job, record["path"])
    incident["code_repair"] = {**(incident.get("code_repair") or {}), "status": "installed_pending_restart",
                               "plan_id": plan["plan_id"], "installed_at": record["installed_at"]}
    _attempt(incident, "install", plan["plan_id"], _evidence(folder, incident), "installed")
    incident.pop("verification", None)
    incident.pop("_install_started", None)
    return {"status": "installed_pending_restart", "plan_id": plan["plan_id"],
            "changes": [change["path"] for change in plan["changes"]], "journal": installed.get("journal"),
            "restart_required": True, "rolled_back": False,
            "next": "宿主单次重启后端，重新 claim 后 verify 并 resume"}


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
    writes_artifacts = name in {"apply_repair", "install_code_repair", "regenerate_units"} or (
        name == "resume" and bool(data.get("strategy")))
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
                or incident.get("session_id") != payload.get("session_id")
                or (incident.get("execution_process") and incident["execution_process"] != _process_identity())):
            _save(folder, state)
            raise ValueError("stale_lease")
        if name not in {"release", "cancel"} and incident.get("snapshot") != _snapshot(folder, incident["stage"]):
            raise ValueError("inputs_changed: release then claim a fresh snapshot")
        recovery = data.get("session_corruption")
        if "session_corruption" in data:
            # An authenticated Host reports the exact Harness restore defect; this
            # backend cannot inspect DSH logs. Authorize only identity retirement,
            # once, with no uncertain effects anywhere in this incident ledger.
            if (name != "release" or not isinstance(recovery, dict)
                    or set(recovery) != {"session_id", "reason"}
                    or recovery.get("reason") != "missing_user_message_identity"):
                raise ValueError("invalid_session_corruption")
            if recovery.get("session_id") != incident.get("session_id"):
                raise ValueError("session_corruption_identity_mismatch")
            if "session_recovery" in incident:
                raise ValueError("session_recovery_already_used")
            if any(r.get("status") == "unknown" for r in state.get("receipts", [])):
                raise ValueError("unknown_receipt_quarantine")
        receipt = {"request_id": request, "status": "unknown", "evidence": None, "fingerprint": fingerprint}
        state.setdefault("receipts", []).append(receipt)
        _save(folder, state)
        mutating = False
        try:
            result = {}
            if name == "diagnose":
                result = {"stage": incident["stage"], "detail": incident["detail"],
                          "repairs": list(REPAIRS), "evidence_paths": sorted(set(self_heal.SOURCE_FILES) | set(self_heal.REPORTS)),
                          "packet": _packet(folder, incident)}
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
                    evidence = _evidence(folder, incident)
                    _block_repeat(incident, "repair", repair, evidence)
                    _check_budget(incident, "repair")
                    snapshot = self_heal._snapshot(folder, mf.load(folder), incident["stage"])
                    expected = {key: value for key, value in snapshot["files"].items() if not key.startswith("input:")}
                    mutating = True
                    try:
                        result = getattr(self_heal_actions, REPAIRS[repair])(folder, expected_inputs=expected)
                    except Exception as exc:
                        _attempt(incident, "repair", repair, evidence, "failed", str(exc))
                        raise
                    _attempt(incident, "repair", repair, evidence,
                             "applied" if result.get("installed") or result.get("passed") else "refused")
                    incident.pop("verification", None)
            elif name == "verify":
                record = _code_record(folder)
                if record and record.get("status") == "installed_pending_restart":
                    raise ValueError("code_repair_installed_pending_restart: 必须真实重启并重新认领")
                result = _verify(folder, incident)
                incident["verification"] = result
                if record and record.get("status") == "restarted_pending_verify":
                    _settle_code_repair(folder, incident, "resolved" if result["verified"] else "needs_deep_repair")
            elif name == "finish":
                outcome = data.get("outcome")
                if outcome not in {"resolved", "needs_deep_repair", "needs_human"} or not isinstance(data.get("summary"), str) or not data["summary"].strip():
                    raise ValueError("invalid_finish")
                previous = incident.get("verification") or {}
                repair_record = _code_record(folder) or {}
                if outcome == "resolved" and repair_record.get("status") in {"installed_pending_restart", "restarted_pending_verify"}:
                    raise ValueError("restart_and_verification_required")
                if outcome == "resolved" and not (previous.get("verified") is True and previous.get("evidence_id")
                        and previous.get("snapshot") == incident["snapshot"] and _verify(folder, incident)["verified"]):
                    raise ValueError("verification_required")
                incident.update(status=outcome, eligible=False, attempt_id=None, lease_expires_at=0)
                _settle_code_repair(folder, incident, outcome)
                if outcome == "needs_human":
                    # Budget exhaustion or a decision only a person can make: keep
                    # the evidence, stop automatic retries, never pretend resolved.
                    incident["escalation"] = {"kind": "human", "at": mf.now_iso(),
                                              "reason": _redact(data["summary"])[:1000]}
                    incident["not_before"] = 0
                result = {"outcome": outcome, "summary": _redact(data["summary"])[:2000], "verification": previous,
                          "verified": outcome == "resolved", "evidence_id": previous.get("evidence_id")}
            elif name == "regenerate_units":
                result = _regenerate_units(folder, incident, data)
                mutating = result.get("status") == "requeued"
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
                    # Reject this deterministic stage mismatch before entering
                    # the potentially mutating self-heal transaction.
                    if strategy == "rejudge_knowledge" and incident["stage"] not in (
                            "format_gate", "validate", "assemble", "knowledge"):
                        raise ValueError("Knowledge rejudgment is not applicable to this stage")
                    manifest = mf.load(folder)
                    evidence = _evidence(folder, incident)
                    _block_repeat(incident, "resume", strategy, evidence)
                    _check_budget(incident, "resume")
                    mutating = True
                    try:
                        result = self_heal._apply(folder, manifest, incident["stage"], incident["detail"],
                            {"action": strategy, "reason": reason}, incident.get("fence", 1),
                            self_heal._snapshot(folder, manifest, incident["stage"]))
                    except Exception as exc:
                        _attempt(incident, "resume", strategy, evidence, "failed", str(exc))
                        raise
                    _attempt(incident, "resume", strategy, evidence,
                             "applied" if result.get("passed") or result.get("installed") else "refused")
                incident.update(status={"cancel": "cancelled", "resume": "awaiting_execution", "release": "queued"}[name],
                                eligible=name == "release", attempt_id=None, lease_expires_at=0)
                if name == "release":
                    if recovery is not None:
                        incident["session_recovery"] = {**recovery, "at": time.time()}
                        incident["session_id"] = None
                        result["session_recovery"] = dict(incident["session_recovery"])
                    incident["queue_generation"] = incident.get("queue_generation", 0) + 1
                    incident["release_count"] = incident.get("release_count", 0) + 1
                    incident["not_before"] = time.time() + min(900, 60 * 3 ** min(incident["release_count"] - 1, 3))
                result = {**result, "released": True, "reclaim_required": True}
                if isinstance(data.get("reason"), str):
                    result["detail"] = _redact(data["reason"])[:2000]
                resume = name == "resume"
                if resume:
                    state["resume_outbox"] = {"request_id": request, "identity": state["identity"], "created_at": time.time()}
            elif name == "request_code_repair":
                result = _request_code_repair(folder, incident, data)
            elif name == "code_repair":
                # Workspace edits are atomic and never touch production; a refusal
                # there is an ordinary failure that stays retryable. Only the
                # production install marks the receipt unknown (see below).
                result = _code_repair(folder, incident, data)
            elif name == "install_code_repair":
                result = _install_code_repair(folder, incident, data)
                mutating = True
                incident["snapshot"] = _snapshot(folder, incident["stage"])
            elif name == "record_finding":
                result = {key: _redact(str(data.get(key) or ""))[:4000]
                          for key in ("summary", "repro", "expected", "actual", "paths")}
                incident.setdefault("findings", []).append(result)
                incident["findings"] = incident["findings"][-30:]
            else:
                # Host diagnostics arrive as error/reason, not detail. Preserve only
                # known scalar fields; never serialize headers, payloads or stacks.
                reason = data.get("reason")
                if isinstance(reason, dict):
                    reason_error = reason.get("error")
                    reason = reason_error.get("message") if isinstance(reason_error, dict) else None
                parts = [value for value in (data.get("detail"), data.get("summary"),
                         data.get("error"), reason) if isinstance(value, str) and value.strip()]
                result = {"detail": _redact("; ".join(dict.fromkeys(parts)))[:2000]}
            if incident.get("status") == "claimed":
                incident["lease_expires_at"] = time.time() + LEASE_SECONDS
            incident["snapshot"] = _snapshot(folder, incident["stage"])
            receipt.update(status="unknown" if resume else "completed", evidence=result)
            _event(incident, name, "completed", result.get("detail") or result.get("summary") or "", result if name == "verify" else None)
        except Exception as exc:
            # Only an action that may already have written is unknown; a refusal
            # before any write is a normal failure and must stay retryable.
            status = "unknown" if (mutating or incident.pop("_install_started", False)
                                   or incident.pop("_regenerate_started", False)) else "failed"
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


def _settle_regenerations(folder, incident):
    """Queue admission is not a result. Settle only actual terminal execution."""
    manifest = mf.load(folder)
    for row in incident.get("attempts", []):
        if row.get("kind") != "regenerate" or row.get("outcome") != "scheduled":
            continue
        stage = row.get("stage") or row.get("name", "").split(":", 1)[0]
        status = (manifest.get("stages", {}).get(stage) or {}).get("status")
        if status in {mf.PENDING, mf.RUNNING, None}:
            continue
        checked = _verify(folder, {**incident, "stage": stage})
        row.update(outcome="succeeded" if checked["verified"] else "failed",
                   scheduled_at=row.get("scheduled_at") or row.get("at"),
                   executed_at=mf.now_iso(), verification=checked)
        _event(incident, "regenerate_units", row["outcome"], row["name"], checked)


def observe_execution(folder):
    """Called by the FIFO worker while it still holds the original auto lock."""
    folder = Path(folder)
    state = _load(folder)
    incident = state.get("incident") or {}
    _settle_regenerations(folder, incident)
    if incident.get("status") != "awaiting_execution":
        if incident:
            _save(folder, state)
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
