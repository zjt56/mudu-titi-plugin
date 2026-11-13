"""Durable operations incidents with separate investigation and repair admission.

No model or remote service is invoked here. A receipt is written *before* an
operation; an interrupted request remains unknown and must never be replayed.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager, nullcontext
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
SERVICE_TTL = 20
_service_until = 0.0


def service_heartbeat(available):
    """Volatile host readiness; restart/disabled/stale host restores legacy healing."""
    if not isinstance(available, bool):
        raise ValueError('available must be boolean')
    global _service_until
    _service_until = time.monotonic() + SERVICE_TTL if available else 0.0
    return {'available': service_available(), 'ttl_seconds': SERVICE_TTL}


def service_available():
    return _service_until > time.monotonic()

ACTIONS = frozenset(("diagnose", "read_evidence", "preview_repair", "apply_repair",
                     "verify", "resume", "regenerate_units", "record_finding", "finish", "heartbeat",
                     "progress", "release", "cancel", "request_code_repair",
                     "code_repair", "install_code_repair", "promote"))
INVESTIGATION_ACTIONS = frozenset(("diagnose", "read_evidence", "record_finding",
                                   "heartbeat", "progress", "release"))
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


class OperationsBusy(ValueError):
    """Admission was refused before any operations request could execute."""


@contextmanager
def _metadata_locked(root, folder):
    """Short ledger-only lock. Never acquires an execution/model lock inside it."""
    root, folder = Path(root), Path(folder)
    _active(root, folder)
    identity = _identity(folder)
    with supervisor._try_file_lock(folder / "workflow/.operations.lock") as acquired:
        if not acquired:
            raise OperationsBusy("worker_busy: 运维事件台账正在更新")
        _active(root, folder)
        if _identity(folder) != identity:
            raise ValueError("identity_changed: 批次身份已变化")
        yield


@contextmanager
def _locked(root, folder, *, execution=False, runner=False):
    root, folder = Path(root), Path(folder)
    _active(root, folder)
    identity = _identity(folder)
    with supervisor._try_file_lock(supervisor._auto_lock_path(folder)) as acquired:
        if not acquired:
            raise OperationsBusy("worker_busy: 批次执行锁已占用")
        _active(root, folder)
        if _identity(folder) != identity:
            raise ValueError("identity_changed: 批次身份已变化")
        # Metadata/heartbeats never contend with an unrelated batch's model.
        # Actual artifact writers HOLD execution/import locks, not just a probe.
        from . import local_agent
        run_guard = supervisor._try_file_lock(folder / "workflow/.run.lock") if execution or runner else nullcontext(True)
        with run_guard as run_acquired:
            if not run_acquired:
                raise OperationsBusy("worker_busy: runner owns batch")
            with ExitStack() as stack:
                try:
                    stack.enter_context(local_agent._agent_lock(0) if execution else nullcontext())
                except local_agent.AgentError as exc:
                    if exc.code == "lock_timeout":
                        raise OperationsBusy("worker_busy: live model/import worker") from exc
                    raise
                with _metadata_locked(root, folder):
                    yield


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


def _pid_alive(pid):
    """Whether a recorded execution process is still running.

    A second backend on the same data root is a live co-driver, not a restart:
    only a dead pid (or a lapsed lease) may invalidate an in-flight attempt.
    Windows must never use os.kill(pid, 0) here — that maps to TerminateProcess.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # HANDLE is pointer sized; the default c_int restype truncates it.
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(0x1000, 0, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _foreign_owner(incident, process=None):
    """Another live driver still inside its lease, if any.

    A different generation of *this* pid is our own earlier start, not a second
    backend: that lease is reclaimed so a restarted plugin resumes its recovery.
    """
    owner = incident.get("execution_process") or {}
    if not owner or owner == (process or _process_identity()):
        return None
    if incident.get("lease_expires_at", 0) <= time.time():
        return None
    try:
        pid = int(owner.get("pid"))
    except (TypeError, ValueError):
        return None
    if pid == os.getpid():
        return None
    return owner if _pid_alive(pid) else None


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


def _candidate_evidence(folder, stage):
    """Expose bounded local candidates, never treat their presence as acceptance."""
    if stage not in {"answers", "explanations", "knowledge", "types", "audio"}:
        return []
    def directory(path):
        # Symlinks and junctions can redirect a listing into another batch.
        return path.is_dir() and not supervisor._redirected(path)

    base = Path(folder) / "workflow" / "agent-runs"
    if not directory(base):
        return []
    rows = []
    try:
        runs = sorted((p for p in base.glob(stage + "-*")
                       if directory(p) and p.name != stage + "-parts"),
                      key=lambda p: p.stat().st_mtime, reverse=True)[:2]
        paths = []
        for run in runs:
            paths.append(run / "response.json")
            progress = sorted((p for p in run.glob("repair-progress*.json")
                               if p.is_file() and not supervisor._redirected(p)),
                              key=lambda p: p.stat().st_mtime, reverse=True)[:1]
            paths.extend(progress)
            parts = sorted((p for p in run.glob("round-*-part-*") if directory(p)),
                           reverse=True)[:40]
            paths.extend(part / "response.json" for part in parts)
        failures = base / (stage + "-parts")
        if directory(failures):
            paths.extend(sorted(failures.glob("*.failure.json"),
                                key=lambda p: p.stat().st_mtime, reverse=True)[:20])
        for path in paths:
            relative = path.relative_to(folder).as_posix()
            checked = supervisor._regular_file(Path(folder), relative)
            if checked is not None:
                rows.append({"path": relative, "bytes": checked.stat().st_size,
                             "accepted": False})
    except OSError:
        pass
    return rows[:104]


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
        "candidate_evidence": _candidate_evidence(folder, stage),
        "candidate_evidence_note": "候选存在不代表已验收；正式产物缺失不证明模型未产出。先读候选、repair-progress 验收错误和来源坐标，再决定局部修复或隔离代码修复。",
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
        "recovery": incident.get("recovery"),
        "authorization": _install_authorization(),
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
    budgets = _budgets(incident)
    incident["attempt_count"] = budgets["attempts_total"] + (outcome != "refused")
    incident["install_count"] = budgets["installs_total"] + (kind == "install" and outcome == "installed")
    incident["regeneration_count"] = budgets["regenerations_total"] + (kind == "regenerate" and outcome == "scheduled")
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
    total_attempts = incident.get("attempt_count", sum(1 for row in rows if row.get("outcome") != "refused"))
    installs = incident.get("install_count", sum(1 for row in rows
                            if row.get("kind") == "install" and row.get("outcome") == "installed"))
    regenerations = incident.get("regeneration_count", sum(1 for row in rows
                                 if row.get("kind") == "regenerate"
                                 and (row.get("scheduled_at") or row.get("outcome") == "scheduled")))
    return {
        "attempts": total_attempts - incident.get("budget_baseline", 0),
        "attempts_total": total_attempts,
        "attempts_limit": BUDGET_ATTEMPTS,
        "installs": installs - incident.get("install_baseline", 0),
        "installs_total": installs,
        "installs_limit": BUDGET_INSTALLS,
        "regenerations": regenerations - incident.get("regeneration_baseline", 0),
        "regenerations_total": regenerations,
        "regenerations_limit": BUDGET_REGENERATIONS,
    }


def _check_budget(incident, kind):
    budgets = _budgets(incident)
    if budgets["attempts"] >= budgets["attempts_limit"]:
        incident["escalation"] = {"kind": "code_repair", "at": mf.now_iso(),
                                  "reason": f"该证据已尝试 {budgets['attempts']} 次，超过上限 {budgets['attempts_limit']}"}
        raise ValueError(f"attempt_budget_exhausted: 当前输入与代码累计 {budgets['attempts']} 次尝试，"
                         "超过跨轮次上限；记录证据并进入自治恢复队列，等待真实输入或代码变化")
    if kind == "install" and budgets["installs"] >= budgets["installs_limit"]:
        raise ValueError(f"install_budget_exhausted: 本次故障已安装 {budgets['installs']} 次，"
                         "同一故障最多安装两次；记录证据并等待新的受验证修复方案")
    if kind == "regenerate" and budgets["regenerations"] >= budgets["regenerations_limit"]:
        raise ValueError(f"regenerate_budget_exhausted: 本次故障已局部重生成 {budgets['regenerations']} 次，"
                         "超过上限；请检查范围是否过窄或证据是否有误")


def leased(root):
    """True while any batch holds an unexpired operations lease.

    The host defers backend restarts while this is true: a restart replaces the
    execution process and voids the in-flight attempt mid-turn.
    """
    root = Path(root)
    if not root.exists():
        return False
    now = time.time()
    for folder in sorted(root.iterdir()):
        try:
            incident = _load(folder).get("incident") or {}
        except (ValueError, OSError):
            continue
        if incident.get("status") != "claimed":
            continue
        if incident.get("lease_expires_at", 0) <= now:
            continue
        owner = incident.get("execution_process") or {}
        if not owner or _pid_alive(owner.get("pid")):
            return True
    return False


def lease_active(folder):
    """Read-only guard, called under auto lock BEFORE reconciliation or writes."""
    state = _load(Path(folder))
    incident = state.get("incident") or {}
    return (incident.get("status") == "claimed" and incident.get("mode", "repair") == "repair"
            and incident.get("lease_expires_at", 0) > time.time()
            and incident.get("execution_process") in (None, _process_identity()))


def _expire(incident):
    if incident.get("status") != "claimed":
        return
    process = _process_identity()
    restarted = bool(incident.get("execution_process")) and incident["execution_process"] != process
    expired = incident.get("lease_expires_at", 0) <= time.time()
    # A live foreign driver keeps its lease until it lapses: two backends on one
    # data root used to cancel each other's attempt on every poll.
    if restarted and not expired and _foreign_owner(incident, process):
        return
    if restarted or expired:
        incident.update(status="queued", eligible=True, attempt_id=None, lease_expires_at=0,
                        not_before=0, queue_generation=incident.get("queue_generation", 0) + 1)
        _event(incident, "lease", "process_replaced" if restarted else "expired",
               "执行进程已更换，旧尝试失效；重新认领后仍须原检查复验" if restarted else "租约超时，允许重新认领")


def _recovery_plan(folder, incident):
    """Offer bounded, unspent strategies; a disposition is never a human queue."""
    evidence = _evidence(folder, incident)
    budgets = _budgets(incident)
    record = _code_record(folder) or {}
    actions = []
    if budgets["attempts"] < budgets["attempts_limit"]:
        if record.get("status") == "installed_pending_restart":
            actions = ["restart_backend_then_claim_verify"]
        elif record.get("status") == "restarted_pending_verify":
            actions = ["resume:retry_stage_with_fresh_context", "verify"]
        elif record.get("status") == "prepared":
            if budgets["installs"] < budgets["installs_limit"]:
                actions = ["install_code_repair"]
        elif record.get("status") in {"workspace_ready", "tests_passed", "tests_failed"}:
            actions = ["code_repair"]
        else:
            actions = ["request_code_repair"]
    # Findings, heartbeats and a new session are deliberately not progress.
    progress = _hash({"evidence": evidence, "attempts": incident.get("attempts", []),
                      "repair": {key: record.get(key) for key in
                                 ("job_id", "status", "plan_id", "installed_at", "settled_at", "workspace_fingerprint")}})
    return {"evidence": evidence, "progress": progress, "next_actions": actions,
            "mode": "isolated_code_repair", "requires_human": False}


def _queue_recovery(folder, incident, disposition, summary):
    plan = _recovery_plan(folder, incident)
    prior = incident.get("recovery") or {}
    # An idle model must execute the offered strategy rather than converting
    # untouched work into a permanent wait or another paid investigation.
    same_progress = prior.get("progress") == plan["progress"]
    attempted = any(row.get("outcome") not in {"refused", "applied", "installed", "scheduled"}
                    for row in incident.get("attempts", []))
    if same_progress and plan["next_actions"] and not attempted:
        incident["recovery"] = {**plan, "state": "action_required", "requires_human": False}
        raise ValueError("autonomous_action_required: 尚未执行可用自治修复步骤；"
                         + ", ".join(plan["next_actions"]))
    waiting = not plan["next_actions"] or same_progress
    incident.update(status="waiting_evidence" if waiting else "queued", eligible=not waiting,
                    attempt_id=None, lease_expires_at=0,
                    queue_generation=incident.get("queue_generation", 0) + 1,
                    not_before=0 if waiting else time.time() + 60,
                    prior_disposition=disposition)
    incident["recovery"] = {**plan, "summary": _redact(summary)[:2000],
                            "state": "waiting_evidence" if waiting else "scheduled",
                            "reason": "等待输入、代码或实际修复证据变化，拒绝相同调查空转" if waiting
                                      else "未修复事件自动升级隔离修复，复验后续跑",
                            "at": mf.now_iso()}
    incident["escalation"] = {"kind": "code_repair", "at": mf.now_iso(),
                              "reason": incident["recovery"]["reason"]}
    _event(incident, "recovery", incident["status"], summary)


def _review_differences(folder, incident):
    if incident.get("stage") not in {"review", "review_gate"}:
        return False
    from . import review
    try:
        result = review.evaluate_rounds(folder)
        gate = (mf.load(folder).get("stages", {}).get("review_gate") or {})
        return (result.get("execution_completed") is True
                and gate.get("status") == mf.NEEDS_REVIEW
                and gate.get("execution_completed") is True
                and gate.get("continue_unqualified") is True and not gate.get("release_ready"))
    except (OSError, ValueError, KeyError, TypeError):
        return False


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
    state.setdefault("identity", _identity(folder))
    state.setdefault("incident", None)
    state.setdefault("receipts", [])
    state.setdefault("failures", {})
    _reconcile_stage_refusal(state)
    incident = state.get("incident")
    if incident:
        _expire(incident)
        if incident.get("status") not in {"resolved", "cancelled", "quality_differences"} and _review_differences(folder, incident):
            incident.update(status="quality_differences", eligible=False, attempt_id=None, lease_expires_at=0,
                            failure_class="quality_differences")
            _event(incident, "reconcile", "quality_differences", "当前审核已执行且仅有差异，保留审核产出，不触发修复")
            return state
        # The original pipeline may have completed while an old incident was
        # queued or parked. Reconcile consumers before spending another model
        # round; status alone and historical findings are not proof. A request
        # whose result was never confirmed stays recorded and is never replayed,
        # but it must not keep a genuinely re-verified stage open forever: the
        # current consumer evidence is the truth for this stage, and the stale
        # request is carried on the incident as quarantined history.
        if incident.get("status") in {"queued", "waiting_evidence", "needs_human",
                                      "needs_deep_repair", "awaiting_execution"}:
            stage_record = (mf.load(folder).get("stages", {}).get(incident.get("stage")) or {})
            if stage_record.get("status") in {mf.COMPLETED, mf.APPROVED}:
                checked = _verify(folder, incident)
                incident["verification"] = checked
                if checked["verified"]:
                    quarantined = [row.get("request_id") for row in state["receipts"]
                                   if row.get("status") == "unknown" and row.get("request_id")]
                    prior = incident["status"]
                    incident.update(status="resolved", eligible=False, attempt_id=None,
                                    lease_expires_at=0, not_before=0, prior_disposition=prior)
                    if quarantined:
                        incident["quarantined_receipts"] = quarantined
                    _settle_code_repair(folder, incident, "resolved")
                    detail = "原阶段已由当前消费者复验通过；保留历史发现，不再调查旧卡点"
                    if quarantined:
                        detail += ("；历史未确认请求保持隔离、不重放：" + "、".join(quarantined[:3]))
                    _event(incident, "reconcile", "resolved", detail, checked)
                    return state
        if incident.get("status") == "needs_human":
            _queue_recovery(folder, incident, "needs_human", "迁移历史人工终态，自动升级隔离修复")
        if incident.get("status") == "waiting_evidence":
            plan = _recovery_plan(folder, incident)
            if plan["evidence"] != (incident.get("recovery") or {}).get("evidence"):
                budgets = _budgets(incident)
                incident.update(budget_baseline=budgets["attempts_total"],
                                install_baseline=budgets["installs_total"],
                                regeneration_baseline=budgets["regenerations_total"])
                plan = _recovery_plan(folder, incident)
                incident.update(status="queued", eligible=True, not_before=0,
                                queue_generation=incident.get("queue_generation", 0) + 1)
                incident["recovery"] = {**plan, "state": "scheduled", "at": mf.now_iso()}
                _event(incident, "recovery", "queued", "真实输入或代码已变化，恢复自动调查；原检查仍须通过")
            return state
        if incident.get("status") == "needs_deep_repair":
            _queue_recovery(folder, incident, "needs_deep_repair", "尚未通过真实复验，自动升级隔离修复")
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
    if stage == "review":
        if record.get("status") in {mf.COMPLETED, mf.APPROVED}:
            return state
        if record.get("status") == mf.NEEDS_REVIEW:
            # Quality differences are valid review output, but needs_review also
            # represents protocol/stall failures. Verify the current round
            # evidence instead of trusting a status or cached completion flag.
            from . import review
            try:
                execution_completed = review.evaluate_rounds(folder).get("execution_completed") is True
            except (OSError, ValueError, KeyError, TypeError):
                execution_completed = False
            if execution_completed:
                return state
    # Match real failed-repair events by stage + exact current detail. Do NOT
    # recompute a hash: self_heal's signature includes retry evidence (input/code
    # hashes), so a locally derived stage+detail hash can never equal it. Use the
    # failed event's own signature as the incident identity instead.
    # self_heal.BLOCKER_OUTCOMES 是「自愈已判定卡住」的完整集合（修复不可用、修复失败、
    # 预算耗尽）。运维只接管这些，不接管仍在推进的 retry_scheduled/upstream_escalated；
    # 旧实现只认 repair_failed/retry_failed，导致 retry_budget_exhausted 这类真正的卡点
    # 永远建不出运维单（面板只显示“待命”）。
    blocker_outcomes = set(self_heal.BLOCKER_OUTCOMES) | {"retry_failed"}
    failed = []
    for event in self_heal._load(folder).get("events", []):
        if (event.get("stage") != stage or event.get("detail") != detail
                or event.get("outcome") not in blocker_outcomes
                or event.get("error_code") in {"rate_limited", "rate_limit", "model_cooldown"}
                or not str(event.get("signature") or "")):
            continue
        failed.append(event)
    for event in failed:
        key = _hash({k: event.get(k) for k in ("at", "stage", "signature", "attempt")})
        state["failures"][key] = {"stage": stage, "signature": event["signature"],
                                  "detail": detail}
    count = sum(1 for x in state["failures"].values()
                if x.get("stage") == stage and x.get("detail") == detail)
    signature = failed[-1]["signature"] if failed else ""
    # An online Host can investigate the first actual blocker. Do not wait for
    # an ordinary self-heal model to fail first. Stable substantive evidence
    # deduplicates this handoff; historical retry counters/timestamps do not.
    unusable_structure = (stage == 'structure_gate'
                          and (record.get('status') in mf.TERMINAL_OK or record.get('rebuild_required'))
                          and bool(self_heal._structure_unusable(folder)))
    early = (service_available()
             and (record.get('status') in {mf.FAILED, mf.BLOCKED, mf.NEEDS_REVIEW} or unusable_structure)
             and not TRANSIENT.search(detail) and not re.search(
                 r'(?i)429|quota|rate[_ -]?limit|额度|限流|冷却|等待外部', detail))
    if early or (incident and incident.get('trigger') == 'first_blocker'
                 and incident.get('stage') == stage and incident.get('detail') == _redact(detail)):
        evidence = json.dumps(self_heal._retry_evidence(folder, manifest, stage), sort_keys=True)
        signature = hashlib.sha256((stage + '\n' + detail + '\n' + evidence).encode('utf-8')).hexdigest()[:16]
    if (count or early) and not (incident and incident.get("signature") == signature):
        state["incident"] = {"incident_id": uuid.uuid4().hex, "batch_name": folder.name,
            "batch_dir": str(folder), "status": "queued", "eligible": True, "stage": stage,
            "signature": signature, "detail": _redact(detail), "self_heal_failures": count,
            "created_at": mf.now_iso(), "events": [], "fence": 0, "queue_generation": 1,
            "trigger": "first_blocker" if early else "failed_self_heal"}
        _event(state["incident"], "discover", "queued", detail)
    return state


def handoff_if_available(folder):
    """Called only by the lock-owning scheduler, before paid self-heal work.

    Queue normal exclusive operations and yield the failed stage at a safe
    checkpoint. Never loosen claim/action locks to race a live executor.
    """
    if not service_available():
        return None
    folder = Path(folder)
    with _metadata_locked(folder.parent, folder):
        return _handoff_locked(folder)


def _handoff_locked(folder):
    state = _discover(folder)
    incident = state.get('incident') or {}
    if not incident or incident.get('status') not in {'queued', 'claimed', 'waiting_evidence'}:
        return None
    stage, _ = self_heal._candidate(folder, mf.load(folder))
    if stage != incident.get('stage'):
        return None
    _save(folder, state)
    return {'changed': False, 'status': 'operations_pending', 'stage': stage,
            'incident_id': incident['incident_id'],
            'reason': '异常已交 AI 运维分析，等待接手或查看已记录的修复建议'}


def pending_incident(folder):
    """Read-only admission hint; terminal history never keeps scanning alive.

    Discovery/expiry/reconciliation remain owned by the ordinary incidents path.
    In particular this hint never saves a ledger just to check if work exists.
    """
    incident = (_load(Path(folder)) or {}).get('incident') or {}
    return incident.get('status') in {
        'queued', 'claimed', 'awaiting_execution', 'investigating',
        'repairing', 'verifying', 'waiting_external', 'waiting_evidence',
        'needs_human', 'needs_deep_repair',
    }


def view(root, folder, *, discover=True):
    folder = Path(folder)
    try:
        with _metadata_locked(root, folder):
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
                    and incident.get("not_before", 0) <= time.time()
                    and not any(row.get("status") == "unknown" for row in _load(folder).get("receipts", []))):
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
    mode = payload.get("mode", "repair")
    if mode == "investigate":
        mode = "investigation"
    if mode not in {"investigation", "repair"}:
        raise ValueError("invalid_claim_mode")
    guard = _metadata_locked(root, folder) if mode == "investigation" else _locked(root, folder, runner=True)
    with guard:
        state = _discover(folder)
        incident = state.get("incident") or {}
        if incident.get("incident_id") != payload.get("incident_id"):
            raise ValueError("incident_mismatch")
        process = _process_identity()
        if (incident.get("status") == "claimed" and incident.get("execution_process")
                and incident["execution_process"] != process):
            if _foreign_owner(incident, process):
                raise OperationsBusy("worker_busy: 另一后端实例正在处理该批次")
            incident.update(status="queued", attempt_id=None, lease_expires_at=0, not_before=0,
                            queue_generation=incident.get("queue_generation", 0) + 1)
        if incident.get("status") == "claimed" and incident.get("session_id") == session:
            if incident.get("mode", "repair") != mode:
                raise ValueError("explicit_promotion_required")
            return dict(incident)
        if incident.get("status") != "queued" or incident.get("not_before", 0) > time.time():
            raise ValueError("incident_not_claimable")
        if any(r.get("status") == "unknown" for r in state.get("receipts", [])):
            _event(incident, "claim", "unknown_quarantine", "未确定动作结果，禁止自动重试")
            _save(folder, state)
            raise ValueError("unknown_receipt_quarantine")
        incident.update(status="claimed", mode=mode, eligible=False, session_id=session,
                        attempt_id=uuid.uuid4().hex, fence=incident.get("fence", 0) + 1,
                        lease_expires_at=time.time() + LEASE_SECONDS,
                        snapshot=_snapshot(folder, incident["stage"]), execution_process=process)
        if mode == "investigation":
            incident["investigation_code"] = _code_fingerprint()
        incident["investigation_ready"] = any(
            finding.get("code_fingerprint") == incident.get("investigation_code")
            and finding.get("snapshot") == incident["snapshot"]
            and finding.get("observed_snapshot") == incident["snapshot"]
            and finding.get("incident_id") == incident["incident_id"]
            for finding in incident.get("findings", []))
        if mode == "repair":
            _acknowledge_restart(folder, incident)
        _event(incident, "claim", "claimed", session)
        _save(folder, state)
        return dict(incident)


def _acknowledge_restart(folder, incident):
    process = _process_identity()
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


def _read_evidence(folder, data):
    relative = data.get("path")
    if not isinstance(relative, str) or "\\" in relative or any(p in {"", ".", ".."} for p in relative.split("/")):
        raise ValueError("unsafe_evidence_path")
    # Exact evidence filenames plus indexed candidates for the active stage only.
    # Do not expose arbitrary inputs, prompts, another stage or another batch.
    allowed = set(self_heal.SOURCE_FILES) | set(self_heal.REPORTS)
    if data.get("scope") == "plugin":
        if not re.fullmatch(r"app/workflow/[a-z_]+\.py", relative):
            raise ValueError("source_not_allowlisted")
        path = supervisor._regular_file(Path(__file__).resolve().parents[2], relative)
    else:
        current = _load(folder).get("incident") or {}
        candidate_paths = {row["path"] for row in _candidate_evidence(folder, current.get("stage"))}
        if (relative not in allowed and relative not in candidate_paths
                and relative not in {"workflow/logs/" + s.id + ".log" for s in contracts.STAGES}):
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
    if incident.get("stage") in ("review", "review_gate"):
        # 一审/二审与原标签的差异是审核产出，不是内容阶段故障（AGENTS.md 规则 6）。
        # 自动重生成只会把差异“改没”或改标签，破坏用户要的人工判断证据。
        raise ValueError(
            "review_differences_are_output_not_blocker: 审核差异是审核产出，不是卡点；"
            "不得用局部重生成自动改标签/改判通过或消除差异。请保留差异交人工判断；"
            "只有审核执行或协议本身失败时才重跑审核阶段")
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
    unit_ids = self_heal._content_retry_scope(folder, target, detail,
        origin_stage=incident['stage'], unit_ids=unit_ids)
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
    elif operation == "read_lines":
        result = dev.read_lines(job, data.get("path"), start_line=data.get("start_line", 1),
                                limit=data.get("limit", 80))
    elif operation == "search":
        result = dev.search(job, data.get("query"), relative=data.get("path"),
                            start_line=data.get("start_line", 1), max_matches=data.get("max_matches", 20),
                            context=data.get("context", 2), cursor=data.get("cursor"))
    elif operation == "write":
        result = dev.write(job, data.get("path"), data.get("content"), expected_sha=data.get("expected_sha"))
    elif operation == "test":
        evidence = _hash({"code": dev.fingerprint(job), "tests": data.get("tests")})
        _block_repeat(incident, "code_test", "isolated_tests", evidence)
        _check_budget(incident, "repair")
        result = dev.run_tests(job, data.get("tests"))
        result["output"] = result["output"][-8000:]
        _attempt(incident, "code_test", "isolated_tests", evidence,
                 "succeeded" if result["passed"] else "failed", result["output"])
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
    record["workspace_fingerprint"] = dev.fingerprint(job)
    _save_code_record(folder, record)
    dev.save(job, record["path"])
    result["status"] = record["status"]
    return result


def _install_authorization(confirm=False):
    from . import operations_development as dev
    authorize = getattr(dev, "install_authorization", None)
    if authorize is not None:
        return authorize(_source_root(), confirm=confirm)
    return {"allowed": confirm is True and os.environ.get("MD_OPERATIONS_INSTALL") == "1",
            "mode": "legacy" if os.environ.get("MD_OPERATIONS_INSTALL") == "1" else "disabled"}


def _install_code_repair(folder, incident, data):
    from . import operations_development as dev
    authorization = _install_authorization(confirm=data.get("confirm") is True)
    if not authorization.get("allowed"):
        if data.get("confirm") is not True:
            raise ValueError("install_confirmation_required")
        raise ValueError("install_disabled: 缺少受控宿主安装授权")
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
                  install_process=_process_identity(), install_attempt_id=incident["attempt_id"],
                  authorization_mode=authorization.get("mode"))
    _save_code_record(folder, record)
    dev.save(job, record["path"])
    incident["code_repair"] = {**(incident.get("code_repair") or {}), "status": "installed_pending_restart",
                               "plan_id": plan["plan_id"], "installed_at": record["installed_at"]}
    _attempt(incident, "install", plan["plan_id"], _evidence(folder, incident), "installed")
    incident.pop("verification", None)
    incident.pop("_install_started", None)
    return {"status": "installed_pending_restart", "plan_id": plan["plan_id"],
            "changes": [change["path"] for change in plan["changes"]], "journal": installed.get("journal"),
            "restart_required": True, "rolled_back": False, "authorization": authorization,
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
    # Read-only tools may run alongside the batch. Any repair admission obtains
    # execution locks first, then the short ledger lock (never the reverse).
    metadata_only = (name in INVESTIGATION_ACTIONS
                     and ((_load(folder).get("incident") or {}).get("mode") == "investigation"))
    guard = (_metadata_locked(root, folder) if metadata_only else
             _locked(root, folder, execution=writes_artifacts,
                     runner=name in {"preview_repair", "verify", "finish", "promote"}))
    with guard:
        state = _load(folder)
        incident = state.get("incident") or {}
        if metadata_only and incident.get("mode", "repair") != "investigation":
            raise OperationsBusy("worker_busy: 调查已升级，须按当前修复身份重新准入")
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
        investigation = incident.get("mode", "repair") == "investigation"
        if investigation and name not in INVESTIGATION_ACTIONS | {"promote"}:
            raise ValueError("repair_claim_required")
        if not investigation and name not in {"release", "cancel"} and incident.get("snapshot") != _snapshot(folder, incident["stage"]):
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
            if name == "promote":
                if not investigation:
                    raise ValueError("investigation_claim_required")
                if (incident.get("snapshot") != _snapshot(folder, incident["stage"])
                        or incident.get("investigation_code") != _code_fingerprint()):
                    raise ValueError("inputs_changed: release then claim a fresh snapshot")
                if incident.get("kind") == "performance" or incident.get("trigger") == "performance":
                    raise ValueError("performance_investigation_read_only")
                mutating = True  # A lost promotion result must be reconciled, never replayed.
                incident.update(mode="repair", attempt_id=uuid.uuid4().hex,
                                fence=incident.get("fence", 0) + 1)
                incident.pop("preview", None)
                incident.pop("verification", None)
                _acknowledge_restart(folder, incident)
                result = {"incident": dict(incident), "promoted": True}
            elif name == "diagnose":
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
                if record and record.get("status") == "restarted_pending_verify" and result["verified"]:
                    _settle_code_repair(folder, incident, "resolved")
                elif record and record.get("status") == "restarted_pending_verify":
                    incident["recovery"] = {**_recovery_plan(folder, incident), "state": "original_check_required"}
            elif name == "finish":
                outcome = data.get("outcome")
                if outcome not in {"resolved", "needs_deep_repair", "needs_human", "recovery_queued"} or not isinstance(data.get("summary"), str) or not data["summary"].strip():
                    raise ValueError("invalid_finish")
                previous = incident.get("verification") or {}
                repair_record = _code_record(folder) or {}
                if outcome == "resolved" and repair_record.get("status") in {"installed_pending_restart", "restarted_pending_verify"}:
                    raise ValueError("restart_and_verification_required")
                if outcome == "resolved" and not (previous.get("verified") is True and previous.get("evidence_id")
                        and previous.get("snapshot") == incident["snapshot"] and _verify(folder, incident)["verified"]):
                    raise ValueError("verification_required")
                if outcome == "resolved":
                    incident.update(status=outcome, eligible=False, attempt_id=None, lease_expires_at=0)
                    _settle_code_repair(folder, incident, outcome)
                else:
                    plan = _recovery_plan(folder, incident)
                    if plan["next_actions"] == ["request_code_repair"]:
                        # Deterministic isolated setup makes the first escalation
                        # concrete; it never edits production or batch content.
                        _request_code_repair(folder, incident, {"summary": data["summary"]})
                    _queue_recovery(folder, incident, outcome, data["summary"])
                result = {"outcome": "resolved" if outcome == "resolved" else "recovery_queued",
                          "requested_outcome": outcome, "status": incident["status"],
                          "recovery": incident.get("recovery"),
                          "summary": _redact(data["summary"])[:2000], "verification": previous,
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
                    if strategy == "upstream_rederive" and incident["stage"] in (
                            "review", "review_gate"):
                        # 审核失败（含模型 JSON 协议错误）应重跑审核本身；重派生上游
                        # 会改写 knowledge/batch，反过来让审核绑定失效，形成死循环。
                        raise ValueError(
                            "review_failure_requires_stage_retry: 审核失败应重跑审核阶段，"
                            "不得重派生上游内容；请用 retry_stage_with_fresh_context")
                    # Pure refusal must precede the uncertain-write boundary:
                    # no scope/source conflict may enqueue work or create unknown receipts.
                    self_heal._retry_admission(folder, incident['stage'], incident['detail'], strategy)
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
                             "applied" if result.get("changed") or result.get("passed") or result.get("installed") else "refused")
                    if not (result.get('changed') or result.get('passed') or result.get('installed')):
                        mutating = False
                        raise ValueError('resume_strategy_refused: ' + str(result.get('reason') or result.get('status')))
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
                    if data.get("state") == "no_substantive_progress":
                        # A bounded host attempt exhausted read-only navigation.
                        # Preserve findings/workspace and wait for new evidence,
                        # rather than re-buying the same investigation each minute.
                        plan = _recovery_plan(folder, incident)
                        incident.update(status="waiting_evidence", eligible=False, not_before=0)
                        incident["recovery"] = {**plan, "state": "waiting_evidence",
                            "reason": "相同证据下未产生新的修改或复验，保留隔离工作区等待真实证据变化",
                            "at": mf.now_iso()}
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
                phase = {'read': 'evidence_read', 'read_lines': 'evidence_read', 'search': 'evidence_read',
                         'write': 'code_write', 'test': 'code_test', 'prepare': 'code_prepare'}.get(data.get('operation'))
                if phase:
                    incident['repair_progress'] = {'phase': phase, 'at': mf.now_iso(),
                        'operation': data.get('operation'), 'passed': result.get('passed')}
            elif name == "install_code_repair":
                result = _install_code_repair(folder, incident, data)
                mutating = True
                incident["snapshot"] = _snapshot(folder, incident["stage"])
            elif name == "record_finding":
                result = {key: _redact(str(data.get(key) or ""))[:4000]
                          for key in ("summary", "repro", "expected", "actual", "paths")}
                if investigation:
                    result.update(incident_id=incident["incident_id"], attempt_id=incident["attempt_id"],
                                  snapshot=incident["snapshot"], observed_snapshot=_snapshot(folder, incident["stage"]),
                                  code_fingerprint=incident.get("investigation_code"))
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
                if name == "heartbeat":
                    result["lease_expires_at"] = incident["lease_expires_at"]
            if not investigation or name == "promote":
                incident["snapshot"] = _snapshot(folder, incident["stage"])
            if name == "promote":
                result["incident"] = dict(incident)
            receipt.update(status="unknown" if resume else "completed", evidence=result)
            _event(incident, name, "completed", result.get("detail") or result.get("summary") or "", result if name == "verify" else None)
        except Exception as exc:
            # Only an action that may already have written is unknown; a refusal
            # before any write is a normal failure and must stay retryable.
            status = "unknown" if (mutating or incident.pop("_install_started", False)
                                   or incident.pop("_regenerate_started", False)) else "failed"
            evidence = {"error": _redact(str(exc))[:1000]}
            if name == "promote" and str(exc).startswith("inputs_changed:"):
                evidence["code"] = "inputs_changed"
            receipt.update(status=status, evidence=evidence)
            _event(incident, name, status, str(exc))
        _save(folder, state)
    if resume:
        # Durable outbox is replayable; only the FIFO admission is retried, never
        # the strategy/action. FIFO deduplicates currently queued/active batches.
        _dispatch_resume(root, folder, enqueue)
        latest = _load(folder)
        receipt = next((r for r in latest.get("receipts", []) if r["request_id"] == request), receipt)
    return dict(receipt)


def promote(root, folder, payload):
    """Explicit host handoff; a successful receipt carries the replacement attempt."""
    return action(root, folder, {**payload, "action": "promote"})


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
    with _metadata_locked(folder.parent, folder):
        return _observe_execution_locked(folder)


def _observe_execution_locked(folder):
    state = _load(folder)
    incident = state.get("incident") or {}
    _settle_regenerations(folder, incident)
    if incident.get("status") != "awaiting_execution":
        if incident:
            _save(folder, state)
        return
    changed = incident.get("snapshot") != _snapshot(folder, incident["stage"])
    checked = _verify(folder, incident)
    incident["verification"] = checked
    if checked["verified"]:
        incident.update(status="resolved", eligible=False, attempt_id=None, lease_expires_at=0, not_before=0)
        _settle_code_repair(folder, incident, "resolved")
    elif _review_differences(folder, incident):
        incident.update(status="quality_differences", failure_class="quality_differences", eligible=False,
                        attempt_id=None, lease_expires_at=0)
    else:
        record = _code_record(folder) or {}
        if record.get("status") == "restarted_pending_verify":
            _settle_code_repair(folder, incident, "needs_deep_repair")
        incident.update(status="queued", eligible=True,
                        queue_generation=incident.get("queue_generation", 0) + 1,
                        not_before=0 if changed else time.time() + 60)
        incident["recovery"] = {**_recovery_plan(folder, incident), "state": "original_check_failed"}
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
