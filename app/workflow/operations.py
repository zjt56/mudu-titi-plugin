"""Durable operations incidents with separate investigation and repair admission.

No model or remote service is invoked here. A receipt is written *before* an
operation; an interrupted request remains unknown and must never be replayed.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager, nullcontext
from datetime import datetime
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
STALE_RUNNING_SECONDS = int(os.environ.get("WF_OPERATIONS_STALE_RUNNING_SECONDS") or 120)
MODEL_STALE_RUNNING_SECONDS = int(
    os.environ.get("WF_OPERATIONS_MODEL_STALE_RUNNING_SECONDS") or max(480, STALE_RUNNING_SECONDS)
)
REVIEW_STALE_RUNNING_SECONDS = int(
    os.environ.get("WF_OPERATIONS_REVIEW_STALE_RUNNING_SECONDS") or max(600, STALE_RUNNING_SECONDS)
)
_MODEL_STAGES = frozenset(("structure_gate", "preassemble", "answers", "explanations", "knowledge"))
ORPHAN_RUNNING_GRACE_SECONDS = int(os.environ.get("WF_OPERATIONS_ORPHAN_RUNNING_GRACE_SECONDS") or 15)
TRANSIENT_ESCALATION_THRESHOLD = int(os.environ.get("WF_OPERATIONS_TRANSIENT_THRESHOLD") or 3)
TRANSIENT_STREAK_MIN_GAP_SECONDS = int(os.environ.get("WF_OPERATIONS_TRANSIENT_GAP_SECONDS") or 30)
MAX_STORED_RECEIPTS = 120
_service_until = 0.0
_HEARTBEAT_FILE = Path(tempfile.gettempdir()) / "mdu-operations-service-heartbeat.json"


def service_heartbeat(available):
    """Host readiness with cross-process heartbeat file fallback."""
    if not isinstance(available, bool):
        raise ValueError('available must be boolean')
    global _service_until
    _service_until = time.monotonic() + SERVICE_TTL if available else 0.0
    try:
        payload = {
            "pid": os.getpid(),
            "available": available,
            "until_epoch": (time.time() + SERVICE_TTL) if available else 0.0,
        }
        tmp = _HEARTBEAT_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, _HEARTBEAT_FILE)
    except OSError:
        pass
    return {'available': service_available(), 'ttl_seconds': SERVICE_TTL}


def service_available():
    if _service_until > 0.0:
        return _service_until > time.monotonic()
    try:
        data = json.loads(_HEARTBEAT_FILE.read_text(encoding="utf-8"))
        if (isinstance(data, dict) and data.get("available") is True
                and isinstance(data.get("pid"), int) and data.get("pid") != os.getpid()
                and float(data.get("until_epoch") or 0) > time.time()):
            try:
                os.kill(data["pid"], 0)
                return True
            except PermissionError:
                return True
            except OSError:
                return False
    except (OSError, ValueError, TypeError):
        pass
    return False

ACTIONS = frozenset(("diagnose", "read_evidence", "preview_repair", "apply_repair",
                     "verify", "resume", "regenerate_units", "record_finding", "finish", "heartbeat",
                     "progress", "release", "cancel", "request_code_repair",
                     "code_repair", "install_code_repair", "promote", "recover"))
INVESTIGATION_ACTIONS = frozenset(("diagnose", "read_evidence", "record_finding",
                                   "heartbeat", "progress", "release"))
REGENERABLE = frozenset(("answers", "explanations", "types", "knowledge", "audio"))
REPAIRS = {"repair_source_tables": "apply_source_table_repair",
           "repair_source_options": "apply_source_option_repair",
           "repair_upload_format": "apply_source_format_repair"}
TRANSIENT = re.compile(r"(?i)\b(rate[_ -]?limit|timeout|timed out|busy|locked|unavailable|"
                       r"temporar|connection|network|cooldown)\b")
# Authentication and route-credential failures are external configuration
# blockers. They remain recorded by the pipeline, but cannot be repaired by
# editing workflow source code or by creating a code-repair workspace.
_EXTERNAL_AUTHENTICATION = re.compile(
    r"(?is)(?:\b(?:authentication|unauthori[sz]ed|permission[_ -]?denied|forbidden|"
    r"invalid[_ -]?api[_ -]?key|api[_ -]?key|credential|凭据|密钥)\b"
    r".{0,120}?(?:\b(?:fail(?:ed|ure)?|invalid|reject(?:ed)?|denied|error)\b|错误|失败|拒绝)|"
    r"(?:\b(?:fail(?:ed|ure)?|invalid|reject(?:ed)?|denied|error)\b|错误|失败|拒绝)"
    r".{0,120}?\b(?:authentication|unauthori[sz]ed|permission[_ -]?denied|forbidden|"
    r"invalid[_ -]?api[_ -]?key|api[_ -]?key|credential|凭据|密钥)\b|\bHTTP\s*[45]0[13]\b)")


def _external_authentication_failure(detail):
    """Return true only for an explicit provider credential/config fault."""
    text = str(detail or "")
    if not text:
        return False
    if re.search(r'"(?:error_code|code|type)"\s*:\s*"(?:authentication|invalid_api_key|unauthorized|permission_denied|forbidden)"', text, re.I):
        return True
    if re.search(r"\bHTTP\s*(?:401|403)\b", text, re.I) and re.search(
            r"(?i)authentication|api[_ -]?key|credential|凭据|密钥", text):
        return True
    return bool(_EXTERNAL_AUTHENTICATION.search(text))


def _only_transient_detail(detail):
    # Diagnoses aggregate independent unit failures with a Chinese semicolon.
    # Each item must itself identify a transient failure; mixed reports remain blockers.
    parts = [part.strip() for part in str(detail or "").split("；") if part.strip()]
    # An arbitrary mention of timeout/network is insufficient: it can appear
    # alongside a validator or source error in the very same unit message.
    transient = re.compile(
        r"(?i)(?:(?:upstream\s+)?http/2\s+stream\s+failed|"
        r"upstream\s+response\s+stream\s+was\s+interrupted|"
        r"upstream service temporarily unavailable|"
        r"(?:[A-Za-z][A-Za-z0-9._-]*\s+)?messages stream:\s*tool input is invalid json|"
        r"(?:request\s+)?(?:timeout|timed out|rate[_ -]?limit(?:ed)?(?:\s+429)?|"
        r"temporarily unavailable|connection (?:failed|reset|error)|"
        r"network (?:error|unavailable)|model[_ -]?cooldown|"
        r"quota (?:exceeded|exhausted)|429|限流|额度不足|额度冷却|冷却中|等待外部))"
        r"[.!。]?")
    from .local_agent import classify_failure
    for part in parts:
        # Unit diagnostics use a full-width colon between identity and error.
        message = part.split("：", 1)[-1].strip()
        if not transient.fullmatch(message) and classify_failure(message) not in ("rate_limit", "quota"):
            return False
    return bool(parts)


def _execution_busy(folder):
    """Return True if a batch execution lock (.auto lock or .run.lock) is currently held."""
    folder = Path(folder)
    checker = getattr(supervisor, "execution_busy", None)
    if callable(checker):
        try:
            return bool(checker(folder))
        except (OSError, ValueError, RuntimeError):
            pass
    try:
        with supervisor._try_file_lock(supervisor._auto_lock_path(folder)) as auto_free:
            if not auto_free:
                return True
        with supervisor._try_file_lock(folder / "workflow/.run.lock") as run_free:
            if not run_free:
                return True
    except OSError:
        return True
    return False


def _stage_age_seconds(record):
    """Seconds elapsed since the stage record was last updated or started."""
    for key in ("updated_at", "started_at"):
        value = record.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            return max(0.0, time.time() - dt.timestamp())
        except ValueError:
            continue
    return None


def _recent_stage_activity(folder, stage_id, window=STALE_RUNNING_SECONDS):
    """Return True if a stage's run/attempt directory or live activity shows active work."""
    wf = Path(folder) / "workflow"
    if stage_id == "review":
        try:
            from . import review_progress
            prog = review_progress.has_active_progress(folder, window)
            if prog is True:
                return True
            if prog is False:
                return False
        except (ImportError, OSError, ValueError, TypeError):
            pass
    try:
        from . import activity as wf_activity
        live = wf_activity.read(folder)
        if live and live.get("stage") == stage_id:
            return True
    except (ImportError, OSError, ValueError, TypeError):
        pass
    now = time.time()
    dirs = []
    if stage_id == "review":
        dirs.extend([wf / "review-attempts", wf / "review-pending", wf / "review-pending-nl"])
    elif stage_id == "structure_gate":
        dirs.extend([wf / "structure-classify-attempts", wf / "metadata-attempts"])
    else:
        dirs.extend([wf / "agent-runs", wf / "agent-tasks"])
    dirs.append(wf / "logs")
    for base in dirs:
        if not base.is_dir():
            continue
        try:
            for entry in base.rglob("*"):
                if not entry.is_file():
                    continue
                if base.name == "logs":
                    if not (entry.stem == stage_id
                            or entry.name.startswith(stage_id + ".")
                            or entry.name.startswith(stage_id + "-")):
                        continue
                elif stage_id not in ("review", "structure_gate") and base.name == "agent-runs" and stage_id not in str(entry.relative_to(base)):
                    continue
                if now - entry.stat().st_mtime < window:
                    return True
        except OSError:
            continue
    return False


def _stale_running_candidate(folder, manifest, *, force=False):
    """Detect a RUNNING stage that is orphaned (no execution lock held) or stale."""
    stages = manifest.get("stages") or {}
    for spec in contracts.STAGES:
        if spec.id in self_heal.BOUNDARIES:
            continue
        record = stages.get(spec.id) or {}
        if record.get("status") != mf.RUNNING:
            continue
        if spec.id == "review":
            rg = stages.get("review_gate") or {}
            if rg.get("status") == mf.NEEDS_REVIEW and rg.get("execution_completed") is True:
                continue
        age = _stage_age_seconds(record)
        busy = _execution_busy(folder)
        if not busy and (force or (age is not None and age >= ORPHAN_RUNNING_GRACE_SECONDS)):
            if not force and _recent_stage_activity(folder, spec.id, ORPHAN_RUNNING_GRACE_SECONDS):
                continue
            detail = f"阶段 {spec.id} 处于运行中但无活跃执行锁（已滞留 {int(age) if age is not None else 0}s），疑似僵死"
            return spec.id, detail
        if spec.id == "review":
            stale_limit = REVIEW_STALE_RUNNING_SECONDS
        elif spec.id in _MODEL_STAGES:
            stale_limit = MODEL_STALE_RUNNING_SECONDS
        else:
            stale_limit = STALE_RUNNING_SECONDS
        if age is not None and age >= stale_limit:
            if busy and not force and _recent_stage_activity(folder, spec.id, stale_limit):
                continue
            detail = f"阶段 {spec.id} 处于运行中已超过 {int(age)}s（阈值 {stale_limit}s），疑似卡死"
            return spec.id, detail
    return None, ""


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
def _locked(root, folder, *, execution=False, runner=False, batch_execution=False):
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
        # Source installation excludes workers across all batches. Batch-local
        # repairs only exclude other writers for their own batch.
        from . import local_agent
        run_guard = supervisor._try_file_lock(folder / "workflow/.run.lock") if execution or batch_execution or runner else nullcontext(True)
        with run_guard as run_acquired:
            if not run_acquired:
                raise OperationsBusy("worker_busy: runner owns batch")
            with ExitStack() as stack:
                try:
                    if execution:
                        stack.enter_context(local_agent._agent_lock(0))
                        stack.enter_context(local_agent._runtime_lock(0, shared=False))
                    elif batch_execution:
                        stack.enter_context(local_agent._batch_lock(folder, 0))
                    elif runner:
                        stack.enter_context(local_agent._runtime_lock(0))
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


def _compact_receipts(state):
    """Bound operations.json size without ever dropping unknown receipts or recent replay evidence."""
    receipts = state.get("receipts")
    if not isinstance(receipts, list) or len(receipts) <= 20:
        return
    unknown = [r for r in receipts if isinstance(r, dict) and r.get("status") == "unknown"]
    settled = [r for r in receipts if isinstance(r, dict) and r.get("status") != "unknown"]
    if len(settled) > MAX_STORED_RECEIPTS:
        settled = settled[-MAX_STORED_RECEIPTS:]
    for row in settled[:-20]:
        ev = row.get("evidence")
        if isinstance(ev, dict) and isinstance(ev.get("text"), str) and len(ev["text"]) > 1000:
            text_val = ev["text"]
            row["evidence"] = {
                **{k: v for k, v in ev.items() if k != "text"},
                "text_chars": len(text_val),
                "text_sha256": hashlib.sha256(text_val.encode("utf-8", errors="replace")).hexdigest()[:16],
                "compacted": True,
            }
    state["receipts"] = unknown + settled


def _save(folder, state):
    if state.get("identity") != _identity(folder):
        raise ValueError("identity_changed: 拒绝迟到写入")
    _compact_receipts(state)
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
    """Observe repair evidence without granting files permission to be installed."""
    root = _source_root()
    result = {}
    for relative in ("app/workflow", "app/scripts", "lib", "config/model-router.json"):
        base = root / relative
        candidates = base.rglob("*") if base.is_dir() else (base,)
        for path in candidates:
            if (path.is_symlink() or not path.is_file()
                    or path.suffix not in ('.py', '.js') and relative != 'config/model-router.json'
                    or any(part.startswith(".") or part == "__pycache__"
                           for part in path.relative_to(root).parts)
                    or any((root.joinpath(*path.relative_to(root).parts[:i])).is_symlink()
                           for i in range(1, len(path.relative_to(root).parts)))):
                continue
            result[path.relative_to(root).as_posix()] = path
    return dict(sorted(result.items()))


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
    if stage not in {"answers", "explanations", "knowledge", "types", "audio", "review"}:
        return []
    def directory(path):
        # Symlinks and junctions can redirect a listing into another batch.
        return path.is_dir() and not supervisor._redirected(path)

    def newest_json(path, limit):
        if not directory(path):
            return []
        return sorted((p for p in path.glob("*.json") if p.is_file() and not supervisor._redirected(p)),
                      key=lambda p: p.stat().st_mtime_ns, reverse=True)[:limit]

    rows = []
    try:
        paths = []
        if stage == "review":
            wf = Path(folder) / "workflow"
            failures = newest_json(wf / "review-failures", 10)
            paths.extend(failures)
            for failure in failures:
                checked = supervisor._regular_file(Path(folder), failure.relative_to(folder).as_posix())
                if checked is None or checked.stat().st_size > 128_000:
                    continue
                report = supervisor._read(checked)
                state_path = report.get("state_path")
                if state_path is None and re.fullmatch(r"round-[0-9a-f]{32}", failure.stem):
                    binding = supervisor._regular_file(Path(folder), f"workflow/review-bindings/{failure.name}")
                    if binding is not None:
                        state_path = supervisor._read(binding).get("state_path")
                if isinstance(state_path, str) and re.fullmatch(r"workflow/review-partitions/[0-9a-f]{64}\.json", state_path):
                    paths.append(Path(folder) / state_path)
            paths.extend(newest_json(wf / "review-partial", 10))
            paths.extend(newest_json(wf / "review-partitions", 4))
        else:
            base = Path(folder) / "workflow" / "agent-runs"
            if not directory(base):
                return []
            runs = sorted((p for p in base.glob(stage + "-*")
                           if directory(p) and p.name != stage + "-parts"),
                          key=lambda p: p.stat().st_mtime, reverse=True)[:2]
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
        for path in dict.fromkeys(paths):
            relative = path.relative_to(folder).as_posix()
            checked = supervisor._regular_file(Path(folder), relative)
            if checked is not None:
                rows.append({"path": relative, "bytes": checked.stat().st_size,
                             "accepted": False})
    except OSError:
        pass
    return rows[:104]


_STAGE_SOURCE_HINTS = {
    "extract": ["app/workflow/extract.py", "app/workflow/docx_extract.py", "app/workflow/pdf_ocr.py"],
    "structure_gate": ["app/workflow/structure_gate.py", "app/workflow/structure_checks.py", "app/workflow/structure_ai.py", "app/workflow/question_content.py"],
    "preassemble": ["app/workflow/preassembly.py", "app/workflow/question_draft.py", "app/workflow/question_content.py", "app/workflow/source_objects.py"],
    "answers": ["app/workflow/agent_tasks.py", "app/workflow/answers_direct.py", "app/workflow/local_agent.py"],
    "explanations": ["app/workflow/agent_tasks.py", "app/workflow/explanations_direct.py", "app/workflow/local_agent.py"],
    "types": ["app/workflow/agent_tasks.py", "app/workflow/types_direct.py", "app/workflow/preassembly.py"],
    "knowledge": ["app/workflow/agent_tasks.py", "app/workflow/knowledge_direct.py", "app/workflow/label_audit.py", "app/workflow/label_merge.py"],
    "audio": ["app/workflow/listening_script.py", "app/workflow/optional_inputs.py"],
    "content_snapshot": ["app/workflow/content_snapshot.py", "app/workflow/handoff.py"],
    "meta": ["app/workflow/metadata_ai.py", "app/workflow/metadata_policy.py"],
    "underlines": ["app/scripts/06-upload/mark_underline.py"],
    "assemble": ["app/workflow/assemble_local.py", "app/workflow/assemble_precheck.py", "app/workflow/batch_fill.py", "app/workflow/question_content.py"],
    "validate": ["app/workflow/checks.py", "app/workflow/final_audit.py", "app/workflow/handoff.py", "app/workflow/assemble_local.py"],
    "format_gate": ["app/workflow/format_gate.py", "app/workflow/format_gate_core.py", "app/workflow/legacy_format.py"],
    "review": ["app/workflow/review_rounds.py", "app/workflow/review.py", "app/workflow/review_child_source.py", "app/workflow/local_agent.py"],
    "review_gate": ["app/workflow/review.py", "app/workflow/review_rounds.py", "app/workflow/handoff.py"],
}


def _suspected_sources(stage, detail, issues):
    codes = " ".join([str(detail or "")] + [str(i.get("code") or "") + " " + str(i.get("message") or "") for i in issues]).lower()
    hints = list(_STAGE_SOURCE_HINTS.get(stage, []))
    if "source_binding" in codes or "review_sample_source_unverified" in codes or "source_unverified" in codes:
        hints = ["app/workflow/structure_gate.py", "app/workflow/review.py", "app/workflow/preassembly.py", "app/workflow/source_anchors.py"] + hints
    elif "format" in codes or "legacy" in codes or "[e" in codes:
        hints = ["app/workflow/legacy_format.py", "app/workflow/assemble_precheck.py", "app/workflow/question_content.py"] + hints
    elif "option" in codes or "table" in codes:
        hints = ["app/workflow/question_content.py", "app/workflow/self_heal_actions.py", "app/workflow/question_draft.py"] + hints
    elif "json" in codes or "schema" in codes:
        hints = ["app/workflow/local_agent.py", "app/workflow/agent_protocol.py", "app/workflow/review.py"] + hints
    return list(dict.fromkeys(hints))[:6]


def _log_tail(folder, stage, max_chars=1200):
    path = supervisor._regular_file(Path(folder), f"workflow/logs/{stage}.log")
    if path is None:
        return ""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    text = raw[-max_chars:].strip()
    return _redact(text) if text else ""


def _unit_excerpts(folder, unit_ids, limit=3):
    if not unit_ids:
        return []
    wanted = list(dict.fromkeys(str(u) for u in unit_ids if u))[:limit]
    wanted_set = set(wanted)
    by_id = {}
    confirmed = supervisor._regular_file(Path(folder), "01_source/structure.confirmed.json")
    if confirmed is not None:
        try:
            data = json.loads(confirmed.read_text(encoding="utf-8"))
            rows = data.get("units") if isinstance(data, dict) else (data if isinstance(data, list) else [])
            for row in rows or []:
                uid = str((row or {}).get("unit_id") or "")
                if uid in wanted_set:
                    by_id[uid] = {
                        "unit_id": uid,
                        "kind": row.get("kind"),
                        "numbers": row.get("numbers"),
                        "section_title": str(row.get("section_title") or "")[:120],
                        "stem_preview": _redact(str(row.get("stem") or ""))[:400],
                        "material_preview": _redact(str(row.get("material") or ""))[:400],
                    }
        except (OSError, ValueError, TypeError):
            pass
    batch_file = supervisor._regular_file(Path(folder), "06_batch/batch.json")
    if batch_file is not None:
        try:
            bdata = json.loads(batch_file.read_text(encoding="utf-8"))
            items = bdata.get("questions") if isinstance(bdata, dict) else (bdata if isinstance(bdata, list) else [])
            for item in items or []:
                if not isinstance(item, dict):
                    continue
                uid = str(item.get("unit_id") or item.get("source_unit_id") or "")
                if uid in wanted_set:
                    entry = by_id.setdefault(uid, {"unit_id": uid})
                    subs = item.get("sub_questions") if isinstance(item.get("sub_questions"), list) else []
                    entry["assembled"] = {
                        "type": item.get("type") or item.get("question_type"),
                        "sub_type": item.get("sub_type"),
                        "option_count": len(item.get("options") or []) if isinstance(item.get("options"), list) else 0,
                        "sub_question_count": len(subs),
                        "sub_option_counts": [len(s.get("options") or []) if isinstance(s, dict) and isinstance(s.get("options"), list) else 0 for s in subs[:10]],
                    }
        except (OSError, ValueError, TypeError):
            pass
    return [by_id[uid] for uid in wanted if uid in by_id]


def _is_model_contract_error(stage, detail):
    """Return True when failure is a transient model schema/completeness miss rather than a code defect."""
    text = str(detail or "").lower()
    json_markers = (
        "invalid final json response",
        "final response must be",
        "tool input is invalid json",
    )
    if stage in ("review", "knowledge", "preassemble", "answers", "explanations", "types"):
        if any(m in text for m in json_markers):
            return True
    if stage in ("review", "knowledge", "preassemble"):
        markers = (
            "invalid fields",
            "every review needs knowledge",
            "review checks must cover each target",
            "need repair:",
        )
        return any(m in text for m in markers)
    return False


def _is_external_auth_error(detail):
    """Return True when failure is caused by external model API key / auth misconfiguration."""
    if _external_authentication_failure(detail):
        return True
    text = str(detail or "").lower()
    markers = (
        "authentication fails",
        "invalid api key",
        "invalid_api_key",
        "incorrect api key",
        "api key is invalid",
        "your api key:",
    )
    return any(m in text for m in markers)


def _is_bundled_input_changed(detail):
    """Return True when a bundled rule or input file changed while the task was running."""
    text = str(detail or "").lower()
    return ("bundled input changed during agent task" in text
            or "inputs_changed" in text)


def _is_code_runtime_crash(detail):
    """Return True when detail clearly carries an unhandled Python runtime code exception."""
    text = str(detail or "")
    return bool(re.search(
        r"\b(NameError|AttributeError|UnboundLocalError|SyntaxError|ImportError|ModuleNotFoundError)\b",
        text,
    ))


def _recommended_action(stage, detail, issues, incident):
    codes = " ".join([str(detail or "")] + [str(i.get("code") or "") for i in issues]).lower()
    if _is_external_auth_error(detail):
        return "waiting_external（外部模型认证/API Key 失效，禁止进入 request_code_repair 改代码，请等待外部密钥或路由配置恢复）"
    if _is_bundled_input_changed(detail):
        return "resume(retry_stage_with_fresh_context)（任务执行期间规则或输入文件变更，直接用最新输入重跑原阶段，不要改代码）"
    if _is_code_runtime_crash(detail):
        return "request_code_repair（确定性代码运行时异常，直接进入隔离工作区复现、修补并跑单测）"
    if incident.get("escalation"):
        return "request_code_repair"
    if _is_model_contract_error(stage, detail):
        return "resume(retry_stage_with_fresh_context)（模型输出契约缺漏，优先局部重问/刷新上下文，不要改代码）"
    if stage == "underlines":
        return "read_evidence（核对 01_source/正文.txt 与 06_batch 选项标号、同行兄弟选项及画线坐标；确认属通用映射规则缺陷后再 request_code_repair）"
    if stage in ("review", "review_gate"):
        if "review_sample_source_unverified" in codes or "source_binding" in codes or "source_unverified" in codes:
            return "resume(upstream_rederive) 或 request_code_repair"
        return "resume(retry_stage_with_fresh_context)"
    if stage in REGENERABLE:
        return "regenerate_units（先核对候选与受影响题号，保留已验证基线，严禁整卷重跑）"
    if stage in ("assemble", "validate", "format_gate"):
        if "[a8]" in codes or "[a9]" in codes:
            return "read_evidence（先核对原卷是否确无共享文字材料如看图/微对话组题；真实漏提走上游恢复，合法形态误拦再走 shared_rubric/phonetic_structure 隔离修复，禁止编造占位）"
        if any(tok in codes for tok in ("source_options_incomplete", "source_table", "attachment_url", "group_boundary_review")):
            return "preview_repair/apply_repair（结构化源数据修复优先；预演拒绝后再评估上游溯源或隔离代码修复）"
        return "preview_repair/apply_repair 或 request_code_repair"
    return "read_evidence 后选择最小修复动作或 request_code_repair"


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
        "suspected_sources": _suspected_sources(stage, detail, issues),
        "recommended_action": _recommended_action(stage, detail, issues, incident),
        "candidate_evidence": _candidate_evidence(folder, stage),
        "candidate_evidence_note": "候选存在不代表已验收；正式产物缺失不证明模型未产出。先读候选、repair-progress 验收错误和来源坐标，再决定局部修复或隔离代码修复。",
        "affected_unit_ids": list(unit_ids)[:40],
        "affected_units": [{"unit_id": unit["unit_id"], "numbers": unit.get("numbers"),
                            "stem_chars": len(str(unit.get("stem") or ""))} for unit in units[:12]],
        "unit_excerpts": _unit_excerpts(folder, unit_ids),
        "log_tail": _log_tail(folder, stage),
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
BUDGET_INSTALLS = 3
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
                         f"同一故障最多安装 {budgets['installs_limit']} 次；记录证据并等待新的受验证修复方案")
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
        legacy_exclusive = not incident.get("execution_first", False)
        if (incident.get("write_active") or legacy_exclusive) and (not owner or _pid_alive(owner.get("pid"))):
            return True
    return False


def lease_active(folder):
    """Read-only guard, called under auto lock BEFORE reconciliation or writes."""
    state = _load(Path(folder))
    incident = state.get("incident") or {}
    return (incident.get("status") == "claimed" and incident.get("mode", "repair") == "repair"
            and (incident.get("write_active") or not incident.get("execution_first", False))
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
    record = _incident_code_record(folder, incident) or {}
    actions = []
    authorization_wait = ""
    if budgets["attempts"] < budgets["attempts_limit"]:
        if record.get("status") == "installed_pending_restart":
            actions = ["restart_backend_then_claim_verify"]
        elif record.get("status") == "restarted_pending_verify":
            actions = ["resume:retry_stage_with_fresh_context", "verify"]
        elif record.get("status") == "prepared":
            if budgets["installs"] < budgets["installs_limit"]:
                authorization = _install_authorization(confirm=True)
                if authorization.get("allowed") is True:
                    actions = ["install_code_repair"]
                else:
                    authorization_wait = "等待受控宿主安装授权；未授权时不重复调用 install_code_repair"
        elif record.get("status") in {"workspace_ready", "tests_passed", "tests_failed"}:
            actions = ["code_repair"]
        else:
            actions = ["request_code_repair"]
    # Findings, heartbeats and a new session are deliberately not progress.
    progress = _hash({"evidence": evidence, "attempts": incident.get("attempts", []),
                      "repair": {key: record.get(key) for key in
                                 ("job_id", "status", "plan_id", "installed_at", "settled_at", "workspace_fingerprint")}})
    return {"evidence": evidence, "progress": progress, "next_actions": actions,
            "mode": "isolated_code_repair", "requires_human": False,
            "authorization_wait": authorization_wait}


def _agent_lock_free():
    """Probe the same source installation locks used by the installer."""
    from . import local_agent
    try:
        with local_agent._agent_lock(0), local_agent._runtime_lock(0, shared=False):
            return True
    except local_agent.AgentError as exc:
        if exc.code == "lock_timeout":
            return False
        raise


def _prepared_install_unattempted(folder, incident):
    if _install_authorization(confirm=True).get("allowed") is not True:
        return False, ""
    record = _incident_code_record(folder, incident) or {}
    if record.get("status") != "prepared":
        return False, ""
    plan_id = record.get("plan_id") or ""
    if any(row.get("kind") == "install" and (not plan_id or row.get("name") == plan_id)
           for row in incident.get("attempts", [])):
        return False, ""
    return True, plan_id


def _prepared_install_waiting_lock(folder, incident, plan):
    if plan.get("next_actions") != ["install_code_repair"]:
        return False, ""
    unattempted, plan_id = _prepared_install_unattempted(folder, incident)
    if not unattempted:
        return False, ""
    recovery = incident.get("recovery") or {}
    if recovery.get("waiting_lock") is True:
        return True, plan_id
    token = plan_id or "__prepared__"
    if token in (incident.get("lock_woken_plans") or []):
        return False, ""
    recent_releases = [ev for ev in incident.get("events", [])[-12:] if ev.get("action") == "release"]
    if any("worker_busy" in str(ev.get("detail") or "") for ev in recent_releases):
        return True, plan_id
    return False, ""


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
    unattempted_install, plan_id = _prepared_install_unattempted(folder, incident)
    token = plan_id or "__prepared__"
    waiting_lock = (
        waiting
        and plan["next_actions"] == ["install_code_repair"]
        and unattempted_install
        and (not _agent_lock_free() or incident.get("busy_wait_plan_id") == token)
    )
    incident.pop("busy_wait_plan_id", None)
    incident.update(status="waiting_evidence" if waiting else "queued", eligible=not waiting,
                    attempt_id=None, lease_expires_at=0,
                    queue_generation=incident.get("queue_generation", 0) + 1,
                    not_before=0 if waiting else time.time() + 3,
                    prior_disposition=disposition)
    reason = ("已准备的代码修复等待全局执行锁释放后自动安装" if waiting_lock
              else "等待输入、代码或实际修复证据变化，拒绝相同调查空转" if waiting
              else "未修复事件自动升级隔离修复，复验后续跑")
    incident["recovery"] = {**plan, "summary": _redact(summary)[:2000],
                            "state": "waiting_evidence" if waiting else "scheduled",
                            "waiting_lock": bool(waiting_lock),
                            "reason": reason,
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


def _discover(folder, *, force=False):
    state = _load(folder) or {"identity": _identity(folder), "incident": None, "receipts": [], "failures": {}}
    state.setdefault("identity", _identity(folder))
    state.setdefault("incident", None)
    state.setdefault("receipts", [])
    state.setdefault("failures", {})
    _reconcile_stage_refusal(state)
    incident = state.get("incident")
    if incident:
        _expire(incident)
        # Old versions admitted purely transient failures before the current
        # filter existed. Retire only parked/queued incidents, never a live
        # lease or an in-flight execution. Unknown receipts and outboxes remain
        # untouched and continue to quarantine any later operation.
        preserve_transient = incident.get("trigger") in {"repeated_transient", "manual_takeover"}
        current_stage, current_detail, current_record = None, "", {}
        if (not preserve_transient
                and incident.get("status") in {"queued", "waiting_evidence", "waiting_external"}
                and _only_transient_detail(incident.get("detail"))):
            manifest = mf.load(folder)
            current_stage, current_detail = self_heal._candidate(folder, manifest)
            current_detail = str(current_detail or "")
            current_record = manifest.get("stages", {}).get(current_stage) or {}
        if (not preserve_transient
                and incident.get("status") in {"queued", "waiting_evidence", "waiting_external"}
                and not state.get("resume_outbox")
                and not any(row.get("status") == "unknown" for row in state["receipts"])
                and current_stage == incident.get("stage")
                and current_record.get("status") in {mf.FAILED, mf.BLOCKED, mf.NEEDS_REVIEW}
                and _redact(current_detail) == incident.get("detail")
                and _only_transient_detail(current_detail)):
            prior = incident["status"]
            incident.update(status="transient_deferred", eligible=False, attempt_id=None,
                            lease_expires_at=0, not_before=0, prior_disposition=prior)
            _event(incident, "reconcile", "transient_deferred",
                   "历史纯瞬时故障退出运维队列；未判定故障已修复")
        elif (not preserve_transient
              and incident.get("status") in {"queued", "waiting_evidence", "waiting_external"}
              and _only_transient_detail(incident.get("detail"))
              and not state.get("resume_outbox")
              and not any(row.get("status") == "unknown" for row in state["receipts"])
              and current_stage and current_record.get("status") in {mf.FAILED, mf.BLOCKED, mf.NEEDS_REVIEW}
              and (current_stage != incident.get("stage") or _redact(current_detail) != incident.get("detail"))
              and not _only_transient_detail(current_detail)):
            incident.update(status="superseded", eligible=False, attempt_id=None, lease_expires_at=0,
                            not_before=0)
            _event(incident, "reconcile", "superseded", "当前出现不同的真实阻断；保留旧事件，未声称故障已恢复")
        if incident.get("status") not in {"resolved", "cancelled", "quality_differences", "transient_deferred", "superseded"} and _review_differences(folder, incident):
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
        code_record = _incident_code_record(folder, incident) or {}
        can_reconcile_stage = (
            incident.get("status") in {"queued", "waiting_evidence", "needs_human",
                                       "needs_deep_repair", "awaiting_execution"}
            or (incident.get("status") == "claimed" and not incident.get("write_active"))
        )
        if can_reconcile_stage and code_record.get("status") != "installed_pending_restart":
            stage_record = (mf.load(folder).get("stages", {}).get(incident.get("stage")) or {})
            if stage_record.get("status") in {mf.COMPLETED, mf.APPROVED}:
                checked = _verify(folder, incident)
                incident["verification"] = checked
                if checked["verified"]:
                    outbox = state.pop("resume_outbox", None)
                    if outbox:
                        for receipt in state.get("receipts", []):
                            if receipt.get("request_id") == outbox.get("request_id"):
                                receipt.update(status="completed", evidence={**(receipt.get("evidence") or {}), "executed": True})
                    quarantined = [row.get("request_id") for row in state["receipts"]
                                   if row.get("status") == "unknown" and row.get("request_id")]
                    prior = incident["status"]
                    incident.update(status="resolved", eligible=False, attempt_id=None,
                                    lease_expires_at=0, not_before=0, prior_disposition=prior,
                                    resolution_source="pipeline_progress", resolved_by_operations=False)
                    if quarantined:
                        incident["quarantined_receipts"] = quarantined
                    _settle_code_repair(folder, incident, "resolved")
                    detail = "流水线自行推进后，原阶段经当前消费者复验通过；标记 resolution_source=pipeline_progress，不代表 AI 运维执行了修复"
                    if quarantined:
                        detail += ("；历史未确认请求保持隔离、不重放：" + "、".join(quarantined[:3]))
                    _event(incident, "reconcile", "resolved", detail, checked)
                    return state
        if incident.get("status") == "needs_human":
            _queue_recovery(folder, incident, "needs_human", "迁移历史人工终态，自动升级隔离修复")
        if incident.get("status") == "waiting_evidence":
            plan = _recovery_plan(folder, incident)
            evidence_changed = plan["evidence"] != (incident.get("recovery") or {}).get("evidence")
            lock_ready, wake_plan_id = _prepared_install_waiting_lock(folder, incident, plan)
            lock_unblocked = (not evidence_changed) and lock_ready and _agent_lock_free()
            if evidence_changed or lock_unblocked:
                if evidence_changed:
                    budgets = _budgets(incident)
                    incident.update(budget_baseline=budgets["attempts_total"],
                                    install_baseline=budgets["installs_total"],
                                    regeneration_baseline=budgets["regenerations_total"])
                    plan = _recovery_plan(folder, incident)
                else:
                    woken = list(incident.get("lock_woken_plans") or [])
                    token = wake_plan_id or "__prepared__"
                    if token not in woken:
                        woken.append(token)
                    incident["lock_woken_plans"] = woken[-10:]
                incident.pop("busy_wait_plan_id", None)
                incident.update(status="queued", eligible=True, not_before=0,
                                queue_generation=incident.get("queue_generation", 0) + 1)
                incident["recovery"] = {**plan, "state": "scheduled", "waiting_lock": False, "at": mf.now_iso()}
                _event(incident, "recovery", "queued",
                       "真实输入或代码已变化，恢复自动调查；原检查仍须通过" if evidence_changed
                       else "全局执行锁已释放，恢复已准备的代码修复安装；原检查仍须通过")
            return state
        if incident.get("status") == "needs_deep_repair":
            _queue_recovery(folder, incident, "needs_deep_repair", "尚未通过真实复验，自动升级隔离修复")
        if incident.get("status") in {"claimed", "queued"}:
            return state
        if incident.get("status") == "awaiting_execution":
            stage_status = (mf.load(folder).get("stages", {}).get(incident["stage"]) or {}).get("status")
            if (stage_status in {mf.PENDING, mf.RUNNING}
                    or supervisor.execution_busy(folder, include_global_agent=False)):
                return state
            current = _snapshot(folder, incident["stage"])
            if current != incident.get("snapshot"):
                if code_record.get("status") == "restarted_pending_verify":
                    _settle_code_repair(folder, incident, "needs_deep_repair")
                incident.update(status="queued", eligible=True,
                                queue_generation=incident.get("queue_generation", 0) + 1)
                _event(incident, "resume", "queued", "正常流水线执行产生新证据，重新认领核验")
            return state
    manifest = mf.load(folder)
    stage, detail = self_heal._candidate(folder, manifest)
    stale_running = False
    if not stage or stage in self_heal.BOUNDARIES:
        stage, detail = _stale_running_candidate(folder, manifest, force=force)
        if not stage:
            return state
        stale_running = True
    record = manifest.get("stages", {}).get(stage) or {}
    # Credential and route-authentication failures are already persisted by the
    # pipeline with a typed wait. Operations must not create a repair incident
    # or spend code-repair attempts for them.
    if _external_authentication_failure(detail) or (record.get("model_authentication") is not None):
        return state
    if stage == "review_gate":
        detail_low = str(detail or "").strip().lower()
        if (record.get("status") not in {mf.FAILED, mf.BLOCKED, mf.NEEDS_REVIEW}
                or _review_differences(folder, {"stage": "review_gate"})
                or "quality difference" in detail_low
                or detail_low == "difference"
                or (record.get("status") == mf.NEEDS_REVIEW
                    and record.get("execution_completed") is True
                    and record.get("continue_unqualified") is True)):
            return state
    if record.get("status") == mf.RUNNING and not stale_running:
        stale_stage, stale_detail = _stale_running_candidate(folder, manifest, force=force)
        if stale_stage == stage:
            detail = stale_detail
            stale_running = True
        else:
            return state
    if stage == "review":
        if record.get("status") in {mf.COMPLETED, mf.APPROVED}:
            return state
        # 审核已执行完成且 review_gate 仅剩审核差异时，差异是产出而非卡点（规则 6/18/24）。
        # 复用 reconcile 同一判定口径，避免先开卡点、事后再改判 quality_differences。
        if _review_differences(folder, {"stage": "review"}):
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
    repeated_transient = False
    if _only_transient_detail(detail):
        if force:
            repeated_transient = True
        else:
            streaks = state.setdefault("transient_streaks", {})
            streak_key = f"{stage}|{_redact(detail)}"
            entry = streaks.get(streak_key)
            if isinstance(entry, int):
                entry = {"count": entry, "last_at": 0.0, "stage_updated_at": ""}
            elif not isinstance(entry, dict):
                entry = {"count": 0, "last_at": 0.0, "stage_updated_at": ""}
            now_ts = time.time()
            stage_updated = str(record.get("updated_at") or record.get("finished_at") or "")
            stage_changed = bool(stage_updated and stage_updated != entry.get("stage_updated_at"))
            gap_elapsed = (now_ts - float(entry.get("last_at") or 0.0)) >= TRANSIENT_STREAK_MIN_GAP_SECONDS
            if int(entry.get("count") or 0) == 0 or stage_changed or gap_elapsed:
                entry["count"] = int(entry.get("count") or 0) + 1
                entry["last_at"] = now_ts
                entry["stage_updated_at"] = stage_updated
                streaks[streak_key] = entry
            if int(entry.get("count") or 0) < TRANSIENT_ESCALATION_THRESHOLD:
                return state
            repeated_transient = True
    elif isinstance(state.get("transient_streaks"), dict):
        prefix = f"{stage}|"
        for key in [k for k in state["transient_streaks"] if k.startswith(prefix)]:
            state["transient_streaks"].pop(key, None)
    blocker_outcomes = set(self_heal.BLOCKER_OUTCOMES) | {"retry_failed"}
    failed = []
    for event in self_heal._load(folder).get("events", []):
        if (event.get("stage") != stage or event.get("detail") != detail
                or event.get("outcome") not in blocker_outcomes
                or event.get("error_code") in {"rate_limited", "rate_limit", "quota", "model_cooldown"}
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
    early = ((service_available() or force)
             and (record.get('status') in {mf.FAILED, mf.BLOCKED, mf.NEEDS_REVIEW}
                  or unusable_structure or stale_running or repeated_transient)
             and (not _only_transient_detail(detail) or repeated_transient or force))
    if early or (incident and incident.get('trigger') in {'first_blocker', 'stale_running', 'repeated_transient', 'manual_takeover'}
                 and incident.get('stage') == stage and incident.get('detail') == _redact(detail)):
        evidence = json.dumps(self_heal._retry_evidence(folder, manifest, stage), sort_keys=True)
        signature = hashlib.sha256((stage + '\n' + detail + '\n' + evidence).encode('utf-8')).hexdigest()[:16]
    if (count or early) and not (incident and incident.get("status") not in {"transient_deferred", "superseded"}
                                  and incident.get("signature") == signature):
        if incident and incident.get("status") in {"transient_deferred", "superseded"}:
            state.setdefault("incident_history", []).append(incident)
        trigger_name = ("manual_takeover" if force
                        else "stale_running" if stale_running
                        else "repeated_transient" if repeated_transient
                        else "first_blocker" if early else "failed_self_heal")
        state["incident"] = {"incident_id": uuid.uuid4().hex, "batch_name": folder.name,
            "batch_dir": str(folder), "status": "queued", "eligible": True, "stage": stage,
            "signature": signature, "detail": _redact(detail), "self_heal_failures": count,
            "created_at": mf.now_iso(), "events": [], "fence": 0, "queue_generation": 1,
            "trigger": trigger_name}
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
    # Discovery can retire an old incident even when no handoff is needed.
    _save(folder, state)
    if not incident or incident.get('status') not in {'queued', 'claimed'}:
        return None
    # An unknown request prevents a new claim; do not park ordinary work behind
    # an operations handoff that cannot be admitted. Never replay the request.
    if (incident.get('status') != 'claimed'
            and (state.get('resume_outbox')
                 or any(row.get('status') == 'unknown' for row in state.get('receipts', [])))):
        return None
    stage, _ = self_heal._candidate(folder, mf.load(folder))
    if stage != incident.get('stage'):
        return None
    _save(folder, state)
    return {'changed': False, 'status': 'operations_pending', 'stage': stage,
            'incident_id': incident['incident_id'],
            'reason': '异常已交 AI 运维分析，等待接手或查看已记录的修复建议'}


_PENDING_INCIDENT_STATUSES = frozenset({
    'queued', 'claimed', 'awaiting_execution', 'investigating',
    'repairing', 'verifying', 'waiting_external', 'waiting_evidence',
    'needs_human', 'needs_deep_repair',
})

_INCIDENT_SCAN_STAT_FILES = (
    "workflow/" + LEDGER,
    "workflow/manifest.json",
    "workflow/ocr-reconciliation.json",
    "06_batch/batch.base.json",
    "01_source/structure.confirmed.json",
)
_INCIDENT_SCAN_IDLE_CACHE: dict[str, tuple[tuple, bool]] = {}
_INCIDENT_SCAN_IDLE_CACHE_MAX = 512


def _is_pending_incident(state) -> bool:
    incident = (state or {}).get('incident') or {}
    return incident.get('status') in _PENDING_INCIDENT_STATUSES


def pending_incident(folder):
    """Read-only admission hint; terminal history never keeps scanning alive.

    Discovery/expiry/reconciliation remain owned by the ordinary incidents path.
    In particular this hint never saves a ledger just to check if work exists.
    """
    return _is_pending_incident(_load(Path(folder)))


def _incident_scan_stat_key(folder: Path) -> tuple:
    parts = []
    for rel in _INCIDENT_SCAN_STAT_FILES:
        path = folder / rel
        try:
            st = path.stat()
            parts.append((st.st_ino, st.st_mtime_ns, st.st_ctime_ns, st.st_size))
        except OSError:
            parts.append(None)
    return tuple(parts)


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


def _folder_needs_incident_scan(folder: Path) -> bool:
    """Cheap read-only guard so `incidents()` skips provably-idle batches.

    Without this filter, every poll scans all historical completed batches
    through `diagnose()` and `review.evaluate_rounds()`, burning 10-20s of CPU
    per poll and tripping the host poller's 20s stale-scan alarm.
    Only returns False when `_discover()` provably cannot create, update, or
    reconcile an incident on this folder.
    """
    folder = Path(folder)
    cache_key = str(folder)
    stat_key = _incident_scan_stat_key(folder)
    cached = _INCIDENT_SCAN_IDLE_CACHE.get(cache_key)
    if cached is not None and cached[0] == stat_key:
        return False
    state = _load(folder)
    if state:
        if _is_pending_incident(state):
            _INCIDENT_SCAN_IDLE_CACHE.pop(cache_key, None)
            return True
        if state.get("resume_outbox"):
            _INCIDENT_SCAN_IDLE_CACHE.pop(cache_key, None)
            return True
        if any(isinstance(row, dict) and row.get("status") == "unknown"
               for row in (state.get("receipts") or [])):
            _INCIDENT_SCAN_IDLE_CACHE.pop(cache_key, None)
            return True
    try:
        manifest = mf.load(folder)
    except (OSError, ValueError, KeyError, TypeError):
        _INCIDENT_SCAN_IDLE_CACHE.pop(cache_key, None)
        return True
    stages = manifest.get("stages") or {}
    sg = stages.get("structure_gate") or {}
    if sg.get("rebuild_required") or (
            sg.get("status") in mf.TERMINAL_OK and bool(self_heal._structure_unusable(folder))):
        _INCIDENT_SCAN_IDLE_CACHE.pop(cache_key, None)
        return True
    ext = stages.get("extract") or {}
    if ext.get("status") == mf.PENDING and (
            ext.get("exit_code") == 6 or (folder / "workflow/ocr-reconciliation.json").is_file()):
        _INCIDENT_SCAN_IDLE_CACHE.pop(cache_key, None)
        return True
    for spec in contracts.STAGES:
        if spec.id in self_heal.BOUNDARIES or spec.kind == contracts.HUMAN:
            continue
        rec = stages.get(spec.id) or {}
        st = rec.get("status") or mf.PENDING
        if (spec.id == "review_gate" and st == mf.NEEDS_REVIEW
                and rec.get("execution_completed") is True
                and rec.get("continue_unqualified") is True):
            continue
        if st in {mf.FAILED, mf.BLOCKED, mf.NEEDS_REVIEW, mf.RUNNING}:
            _INCIDENT_SCAN_IDLE_CACHE.pop(cache_key, None)
            return True
    if len(_INCIDENT_SCAN_IDLE_CACHE) >= _INCIDENT_SCAN_IDLE_CACHE_MAX:
        _INCIDENT_SCAN_IDLE_CACHE.clear()
    _INCIDENT_SCAN_IDLE_CACHE[cache_key] = (stat_key, False)
    return False


def incidents(root):
    root = Path(root)
    found = []
    if root.is_symlink() or not root.is_dir():
        return {"incidents": []}
    for folder in sorted(root.iterdir()):
        if not supervisor._active_folder(root, folder):
            continue
        if not _folder_needs_incident_scan(folder):
            continue
        try:
            incident = view(root, folder)["incident"]
            if (incident and incident.get("status") == "queued"
                    and incident.get("not_before", 0) <= time.time()
                    and not any(row.get("status") == "unknown" for row in _load(folder).get("receipts", []))):
                unattempted_install, _ = _prepared_install_unattempted(folder, incident)
                if unattempted_install and not _agent_lock_free():
                    continue
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
    execution_first = payload.get("execution_first", False)
    if not isinstance(execution_first, bool):
        raise ValueError("invalid_execution_first")
    guard = _metadata_locked(root, folder) if mode == "investigation" or execution_first else _locked(root, folder, runner=True)
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
                        snapshot=_snapshot(folder, incident["stage"]), execution_process=process,
                        execution_first=execution_first)
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
            if execution_first:
                incident.update(recovery_ready=True, recovery_protocol="operations-recovery/v1")
        _event(incident, "claim", "claimed", session)
        _save(folder, state)
        return dict(incident)


def _acknowledge_restart(folder, incident):
    process = _process_identity()
    record = _incident_code_record(folder, incident)
    if (record and record.get("status") == "installed_pending_restart"
            and record.get("install_process") and record["install_process"] != process
            and record.get("install_attempt_id") != incident["attempt_id"]):
        record.update(status="restarted_pending_verify", restart_process=process,
                      restart_attempt_id=incident["attempt_id"], restarted_at=mf.now_iso())
        _save_code_record(folder, record)
        incident["code_repair"] = {**(incident.get("code_repair") or {}),
                                   "status": record["status"], "restarted_at": record["restarted_at"]}
        _event(incident, "restart", "acknowledged", "新执行进程已重新认领；仍须原检查复验")


_EXTRA_BATCH_EVIDENCE = frozenset((
    "01_source/material-roles.json",
    "01_source/document-meta.json",
    "01_source/images.json",
    "01_source/tables.json",
    "01_source_ans/answers.raw.json",
    "02_audio/audio.json",
    "02_audio/transcripts.json",
    "05_explanations/explanations.json",
    "06_batch/batch_underlines.json",
    "06_batch/preassemble.json",
    "07_review/manual-review-queue.json",
    "07_review/review-round-1.json",
    "07_review/review-round-2.json",
    "07_review/final-audit.json",
    "workflow/delivery.json",
    "workflow/batch-fill-state.json",
    "workflow/self-heal.json",
    "workflow/code-repair.json",
    "workflow/metadata-ai.json",
))
_SECRET_EVIDENCE_MARKERS = ("secret", "credential", ".pem", ".key", "id_rsa", "password", "token")


def _read_evidence(folder, data):
    relative = data.get("path")
    if not isinstance(relative, str) or "\\" in relative or any(p in {"", ".", ".."} for p in relative.split("/")):
        raise ValueError("unsafe_evidence_path")
    # Exact evidence filenames plus indexed candidates for the active stage only.
    # Do not expose arbitrary inputs, prompts, another stage or another batch.
    allowed = set(self_heal.SOURCE_FILES) | set(self_heal.REPORTS) | _EXTRA_BATCH_EVIDENCE
    if data.get("scope") == "plugin":
        if not (re.fullmatch(r"app/workflow/[a-z0-9_]+\.py", relative)
                or re.fullmatch(r"app/scripts/(?:[a-z0-9_][a-z0-9_-]*/)*[a-z_][a-z0-9_]*\.py", relative)
                or re.fullmatch(r"app/workbench/[a-z0-9_]+\.py", relative)
                or re.fullmatch(r"app/prompts/roles/[a-z0-9_-]+\.md", relative)
                or re.fullmatch(r"app/references/[a-z0-9_-]+\.(?:json|md)", relative)):
            raise ValueError(
                f"source_not_allowlisted: {relative!r} 不在只读源码白名单内"
                "（允许 app/workflow/*.py、app/scripts/**/*.py、app/workbench/*.py、app/prompts/roles/*.md、app/references/*.json）"
            )
        path = supervisor._regular_file(Path(__file__).resolve().parents[2], relative)
    else:
        current = _load(folder).get("incident") or {}
        candidate_paths = {row["path"] for row in _candidate_evidence(folder, current.get("stage"))}
        stage_ids = {s.id for s in contracts.STAGES}
        is_stage_log = relative in {"workflow/logs/" + sid + ".log" for sid in stage_ids}
        is_stage_feedback = relative in {"workflow/repair-feedback/" + sid + ".json" for sid in stage_ids}
        is_material = (
            relative.startswith("01_source/materials/")
            and relative.count("/") == 2
            and relative.lower().endswith((".txt", ".json"))
            and not any(marker in relative.lower() for marker in _SECRET_EVIDENCE_MARKERS)
        )
        if (relative not in allowed and relative not in candidate_paths
                and not is_stage_log and not is_stage_feedback and not is_material):
            sample = sorted(candidate_paths)[:6]
            hint = f"；当前阶段可读候选={sample}" if sample else ""
            raise ValueError(f"evidence_not_allowlisted: {relative!r} 不在白名单内{hint}")
        path = supervisor._regular_file(folder, relative)
    if path is None:
        raise ValueError("evidence_missing")
    offset = max(0, min(int(data.get("offset", 0)), path.stat().st_size))
    limit = max(1, min(int(data.get("limit", 12000)), 24000))
    with path.open("r", encoding="utf-8", errors="replace", newline="\n") as stream:
        stream.seek(offset)
        text = stream.read(limit)
        next_offset = stream.tell()
        eof = not stream.read(1)
    return {"path": relative, "offset": offset, "text": _redact(text), "limit": limit,
            "next_offset": next_offset, "eof": eof}


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


def _incident_code_record(folder, incident):
    record = _code_record(folder)
    incident_id = incident.get("incident_id")
    return record if incident_id and record and record.get("incident_id") == incident_id else None


def _settle_code_repair(folder, incident, outcome):
    """Close the repair record with the incident's final disposition.

    An install stays `installed_pending_restart` until the incident is really
    settled, so a missing verification cannot silently look like a fixed batch.
    """
    record = _incident_code_record(folder, incident)
    if not record or record.get("status") != "restarted_pending_verify":
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


def _code_job(folder, incident):
    record = _incident_code_record(folder, incident)
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
    if existing:
        try:
            existing_job = dev.load(existing["path"])
            stale_workspace = (existing.get("incident_id") != incident.get("incident_id")
                               or not incident.get("incident_id")
                               or existing.get("status") in {"failed", "verified", "needs_human", "rolled_back", "tests_failed"}
                               or existing_job.get("status") in {"failed", "verified", "needs_human", "tests_failed", "rolled_back"})
        except (OSError, ValueError, KeyError):
            stale_workspace = True
        if not stale_workspace:
            record, job = _code_job(folder, incident)
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


# A code-repair workspace is an explicit escalation boundary. Read-only source
# inspection must not create one as a side effect, because ordinary diagnosis can
# inspect source evidence without authorizing a repair job or consuming disk.
_AUTO_INIT_CODE_OPS = frozenset()


def _normalize_test_names(tests):
    if not isinstance(tests, list):
        return tests
    normalized = []
    for entry in tests:
        if isinstance(entry, str):
            cleaned = entry.strip().replace("\\", "/")
            match = re.fullmatch(r"(?:(?:app/)?workflow/)?(test_[a-z0-9_]+)\.py", cleaned)
            normalized.append("workflow." + match.group(1) if match else cleaned)
        else:
            normalized.append(entry)
    return normalized


def _code_repair(folder, incident, data):
    from . import operations_development as dev
    operation = data.get("operation")
    auto_initialized = False
    if _incident_code_record(folder, incident) is None and operation in _AUTO_INIT_CODE_OPS:
        _request_code_repair(folder, incident, {
            "summary": f"自动初始化隔离代码修复工作区（{incident.get('stage') or 'unknown'}）",
        })
        auto_initialized = True
    record, job = _code_job(folder, incident)

    # Search returns a real workspace-relative path and a continuation cursor.
    # Accepting that cursor directly for read_lines keeps the intended
    # search -> read_lines flow deterministic while still delegating strict
    # path/link/allowlist checks to operations_development.
    cursor = data.get("cursor")
    if cursor is not None and (not isinstance(cursor, dict)
                               or set(cursor) != {"path", "start_line"}
                               or not isinstance(cursor.get("path"), str)
                               or not isinstance(cursor.get("start_line"), int)):
        raise ValueError("search_cursor_invalid")
    path = data.get("path")
    if path == "":
        path = None
    if operation == "read_lines" and path is None and cursor is not None:
        path = cursor["path"]
    start_line = data.get("start_line", 1)
    if operation == "read_lines" and "start_line" not in data and cursor is not None:
        start_line = cursor["start_line"]
    if operation in {"read", "read_lines", "write", "patch", "patch_lines", "multi_patch", "restore_base"} and not isinstance(path, str):
        raise ValueError("source_path_required")
    if operation == "status":
        status_out = _code_status(record, job)
        if auto_initialized:
            status_out["workspace_auto_initialized"] = True
        return status_out
    if record.get("status") in {"installed_pending_restart", "restarted_pending_verify", "failed", "verified", "needs_human"}:
        raise ValueError("code_repair_closed_or_pending_restart: 先重启后端并复验，再 request_code_repair")
    result = {}
    if operation == "read":
        result = dev.read(job, path, offset=data.get("offset", 0), limit=data.get("limit", 12000))
    elif operation == "read_lines":
        result = dev.read_lines(job, path, start_line=start_line,
                                limit=data.get("limit", 200))
    elif operation == "search":
        result = dev.search(job, data.get("query"), relative=path,
                            start_line=start_line, max_matches=data.get("max_matches", 20),
                            context=data.get("context", 2), cursor=cursor)
    elif operation == "write":
        if any(key in data for key in ('start_line', 'context', 'offset', 'cursor', 'old_text', 'new_text')):
            raise ValueError('write_requires_complete_file: 局部修改请用 patch 和唯一 old_text/new_text')
        result = dev.write(job, path, data.get("content"), expected_sha=data.get("expected_sha"))
    elif operation == 'patch':
        result = dev.patch_source(job, path, data.get('old_text'), data.get('new_text'),
                                  expected_sha=data.get('expected_sha'))
    elif operation == 'patch_lines':
        result = dev.patch_lines(job, path, data.get('start_line'), data.get('end_line'),
                                 data.get('new_text'), expected_sha=data.get('expected_sha'))
    elif operation == 'multi_patch':
        result = dev.multi_patch(job, path, data.get('edits'), expected_sha=data.get('expected_sha'))
    elif operation == 'restore_base':
        result = dev.restore_base(job, path, expected_sha=data.get('expected_sha'))
    elif operation == "test":
        fast = bool(data.get("fast"))
        normalized_tests = _normalize_test_names(data.get("tests"))
        evidence = _hash({"code": dev.fingerprint(job), "tests": normalized_tests, "fast": fast})
        _block_repeat(incident, "code_test", "isolated_tests", evidence)
        _check_budget(incident, "repair")
        result = dev.run_tests(job, normalized_tests, fast=fast)
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
    if operation in {"write", "patch", "patch_lines", "multi_patch", "restore_base", "test", "prepare"}:
        record["status"] = {"write": "workspace_ready", "patch": "workspace_ready", "patch_lines": "workspace_ready",
                            "multi_patch": "workspace_ready", 'restore_base': 'workspace_ready',
                            "test": record.get("status"), "prepare": "prepared"}[operation]
    record["workspace_fingerprint"] = dev.fingerprint(job)
    _save_code_record(folder, record)
    dev.save(job, record["path"])
    result["status"] = record["status"]
    if auto_initialized:
        result["workspace_auto_initialized"] = True
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
    record, job = _code_job(folder, incident)
    if record.get("status") != "prepared":
        raise ValueError("current_install_plan_required")
    plan = job.get("plan") or {}
    if not plan or plan.get("code_fingerprint") != dev.fingerprint(job):
        raise ValueError("current_install_plan_required")
    incident["_install_started"] = True
    installed = dev.install(job, backup_dir=_code_root(folder), allow=True, runtime_lock_held=True)
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


def _background_workspace_test(root, folder, payload):
    """Reserve an exact request, run isolated tests without batch/ledger locks,
    then settle against the same fence. Unknown reservations are never replayed.
    """
    request = _text(payload.get('request_id'), 'request_id')
    fingerprint = _hash(payload)
    with _metadata_locked(root, folder):
        state = _load(folder)
        incident = state.get('incident') or {}
        old = next((r for r in state.get('receipts', []) if r['request_id'] == request), None)
        if old:
            if old.get('fingerprint') != fingerprint:
                raise ValueError('request_id_conflict')
            return dict(old)
        _expire(incident)
        if (incident.get('status') != 'claimed' or incident.get('mode', 'repair') != 'repair'
                or any(incident.get(k) != payload.get(k) for k in ('incident_id', 'attempt_id', 'session_id'))
                or incident.get('execution_process') != _process_identity()):
            raise ValueError('stale_lease')
        if any(r.get('status') == 'unknown' for r in state.get('receipts', [])):
            raise ValueError('unknown_receipt_quarantine')
        if incident.get('snapshot') != _snapshot(folder, incident['stage']):
            raise ValueError('inputs_changed: release then claim a fresh snapshot')
        receipt = {'request_id': request, 'status': 'unknown', 'fingerprint': fingerprint, 'evidence': None}
        state.setdefault('receipts', []).append(receipt)
        identity = state['identity']
        _save(folder, state)
    try:
        result = _code_repair(folder, incident, payload['input'])
        status = 'completed'
    except Exception as exc:
        result, status = {'error': _redact(str(exc))[:1000]}, 'failed'
    deadline = time.monotonic() + 5
    while True:
        try:
            return _settle_workspace_test(root, folder, payload, identity, receipt, incident, status, result)
        except OperationsBusy:
            # Admission already occurred. Retry only the receipt write.
            if time.monotonic() >= deadline:
                return dict(receipt)
            time.sleep(0.05)


def _settle_workspace_test(root, folder, payload, identity, receipt, tested, status, result):
    with _metadata_locked(root, folder):
        state = _load(folder)
        if state.get('identity') != identity:
            raise ValueError('identity_changed: 拒绝迟到写入')
        current = state.get('incident') or {}
        saved = next((r for r in state.get('receipts', []) if r['request_id'] == receipt['request_id']), None)
        if saved is None or saved.get('fingerprint') != receipt['fingerprint']:
            raise ValueError('workspace_test_receipt_changed')
        if saved['status'] != 'unknown':
            return dict(saved)
        same_incident = current.get('incident_id') == payload.get('incident_id')
        if same_incident:
            for key in ('attempts', 'attempt_count', 'install_count', 'regeneration_count', 'escalation'):
                if key in tested:
                    current[key] = tested[key]
        live = (same_incident and current.get('status') == 'claimed'
                and current.get('lease_expires_at', 0) > time.time()
                and current.get('execution_process') == _process_identity()
                and all(current.get(k) == payload.get(k) for k in ('attempt_id', 'session_id')))
        if not live:
            saved.update(status='failed', evidence={'error': 'stale_lease: 隔离测试已结束，旧租约不能推进安装'})
            _save(folder, state)
            return dict(saved)
        saved.update(status=status, evidence=result)
        current['repair_progress'] = {'phase': 'code_test', 'at': mf.now_iso(), 'passed': result.get('passed')}
        _event(current, 'code_repair', status, 'isolated test', result)
        _save(folder, state)
        return dict(saved)


def action(root, folder, payload, *, enqueue=None):
    folder = Path(folder)
    name = payload.get("action")
    if name not in ACTIONS:
        raise ValueError("unsupported_action")
    request = _text(payload.get("request_id"), "request_id")
    data = payload.get("input") or {}
    if not isinstance(data, dict):
        raise ValueError("invalid_input")
    if name == 'code_repair' and data.get('operation') == 'test':
        return _background_workspace_test(root, folder, payload)
    resume = False
    writes_artifacts = name in {"apply_repair", "install_code_repair", "regenerate_units"} or (
        name == "resume" and bool(data.get("strategy")))
    global_lock = name == "install_code_repair"
    batch_lock = name == "apply_repair"
    # Read-only tools may run alongside the batch. Only global codebase installs
    # hold the cross-batch agent lock; batch-local repairs/resumes use batch locks.
    metadata_only = (name in INVESTIGATION_ACTIONS
                     and ((_load(folder).get("incident") or {}).get("mode") == "investigation"))
    guard = (_metadata_locked(root, folder) if metadata_only else
             _locked(root, folder, execution=global_lock, batch_execution=batch_lock,
                     runner=writes_artifacts or name in {"preview_repair", "verify", "finish", "promote", "recover"}))
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
        if name == "install_code_repair":
            from . import operations_development as dev
            if dev.release_awaiting_restart(_source_root()):
                raise OperationsBusy("worker_busy: 上一次代码发布仍在等待宿主重启")
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
        if writes_artifacts:
            incident['write_active'] = True
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
            elif name == "recover":
                checked = _verify(folder, incident)
                incident["verification"] = checked
                record = _incident_code_record(folder, incident) or {}
                if checked.get("verified") and record.get("status") != "installed_pending_restart":
                    incident.update(status="resolved", eligible=False, attempt_id=None, lease_expires_at=0,
                                    resolution_source="pipeline_progress", resolved_by_operations=False)
                    _settle_code_repair(folder, incident, "resolved")
                    result = {"resolved": True, "verified": True, "verification": checked}
                else:
                    result = {"resolved": False, "verified": False, "phase": "repair_required",
                              "next_action": "检查当前候选并直接执行可验证修复；代码缺陷使用隔离修复",
                              "packet": _packet(folder, incident), "verification": checked}
                    stage_rec = (mf.load(folder).get("stages") or {}).get(incident["stage"]) or {}
                    if record.get("status") == "restarted_pending_verify":
                        evidence = _evidence(folder, incident)
                        budgets = _budgets(incident)
                        can_auto_retry = (
                            budgets["attempts"] < budgets["attempts_limit"]
                            and not _repeated(incident, "resume", "retry_stage_with_fresh_context", evidence)
                        )
                        if can_auto_retry:
                            try:
                                self_heal._retry_admission(
                                    folder, incident["stage"], incident["detail"], "retry_stage_with_fresh_context"
                                )
                            except ValueError:
                                can_auto_retry = False
                        if can_auto_retry:
                            result.update(
                                phase="restarted_auto_resume",
                                repair="post_restart_reverify",
                                resume_required=True,
                                resume_input={
                                    "strategy": "retry_stage_with_fresh_context",
                                    "reason": "代码修复已安装并重启生效，自动重跑原失败阶段以复验",
                                },
                            )
                    if (not result.get("resume_required")
                            and incident.get("trigger") in {"stale_running", "manual_takeover"}
                            and stage_rec.get("status") == mf.RUNNING):
                        incident['write_active'] = True
                        _save(folder, state)
                        mutating = True
                        cur_manifest = mf.load(folder)
                        mf.touch_stage(cur_manifest, incident["stage"], mf.PENDING,
                                       notes="AI运维已确认无活跃执行进程，安全复位僵死运行态等待续跑")
                        mf.save(folder, cur_manifest)
                        _attempt(incident, "repair", "reset_stale_running", _evidence(folder, incident), "applied")
                        result.update(phase="repair_applied", repair="reset_stale_running",
                                      resume_required=True, resume_input={})
                    elif incident['stage'] in ('assemble', 'validate', 'format_gate', 'preassemble'):
                        from . import legacy_format, local_agent, self_heal_actions
                        evidence = _evidence(folder, incident)
                        snapshot = self_heal._snapshot(folder, mf.load(folder), incident['stage'])
                        expected = {k: v for k, v in snapshot['files'].items() if not k.startswith('input:')}
                        applied_fast = False
                        candidates = (
                            ("repair_source_options", self_heal_actions._prepare_options),
                            ("repair_source_tables", self_heal_actions._prepare_tables),
                            ("repair_upload_format", legacy_format.prepare),
                        ) if incident['stage'] in ('assemble', 'validate', 'format_gate') else ()
                        for repair, prepare in candidates:
                            try:
                                proposal = prepare(folder)
                            except (OSError, ValueError, TypeError, KeyError):
                                continue
                            if not proposal.get('passed') or not proposal.get('changes'):
                                continue
                            _block_repeat(incident, 'repair', repair, evidence)
                            _check_budget(incident, 'repair')
                            try:
                                with local_agent._batch_lock(folder, 0):
                                    incident['write_active'] = True
                                    _save(folder, state)
                                    mutating = True
                                    outcome = getattr(self_heal_actions, REPAIRS[repair])(folder, expected_inputs=expected)
                            except local_agent.AgentError as exc:
                                if exc.code == 'lock_timeout':
                                    raise OperationsBusy('worker_busy: live model/import worker') from exc
                                raise
                            _attempt(incident, 'repair', repair, evidence,
                                     'applied' if outcome.get('installed') else 'refused')
                            if outcome.get('installed'):
                                applied_fast = True
                                result.update(phase='repair_applied', repair=repair,
                                              resume_required=True, resume_input={})
                            break
                        if not applied_fast:
                            subtype_fn = getattr(self_heal, '_subtype_count_repair_units', None)
                            subtype_units = subtype_fn(folder) if callable(subtype_fn) else []
                            skeleton_fn = getattr(self_heal, '_skeleton_refresh_units', None)
                            refresh_units = skeleton_fn(folder) if callable(skeleton_fn) else []
                            if subtype_units:
                                _block_repeat(incident, 'repair', 'preassembly_type_repair', evidence)
                                _check_budget(incident, 'repair')
                                incident['write_active'] = True
                                _save(folder, state)
                                mutating = True
                                self_heal._feedback(
                                    folder, 'types',
                                    '上传格式子类题数/选项数不符（如选择N题误计入☆拓展设问），局部重判题型子类',
                                    incident['detail'], 1, unit_ids=subtype_units,
                                )
                                self_heal._requeue(
                                    folder, mf.load(folder), 'preassemble',
                                    'AI运维自动修复：上传格式子类计数不符，返回前置组装局部修正：' + '、'.join(subtype_units),
                                )
                                _attempt(incident, 'repair', 'preassembly_type_repair', evidence, 'applied')
                                result.update(phase='repair_applied', repair='preassembly_type_repair',
                                              refreshed_units=list(subtype_units)[:20],
                                              resume_required=True, resume_input={})
                            elif refresh_units:
                                _block_repeat(incident, 'repair', 'refresh_preassemble', evidence)
                                _check_budget(incident, 'repair')
                                incident['write_active'] = True
                                _save(folder, state)
                                mutating = True
                                self_heal._requeue(
                                    folder, mf.load(folder), 'preassemble',
                                    'AI运维自动修复：按未变Word原文局部刷新题目格式：' + '、'.join(refresh_units),
                                )
                                _attempt(incident, 'repair', 'refresh_preassemble', evidence, 'applied')
                                result.update(phase='repair_applied', repair='refresh_preassemble',
                                              refreshed_units=list(refresh_units)[:20],
                                              resume_required=True, resume_input={})
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
                record = _incident_code_record(folder, incident)
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
                repair_record = _incident_code_record(folder, incident) or {}
                if outcome == "resolved" and repair_record.get("status") in {"installed_pending_restart", "restarted_pending_verify"}:
                    raise ValueError("restart_and_verification_required")
                if outcome == "resolved" and not (previous.get("verified") is True and previous.get("evidence_id")
                        and previous.get("snapshot") == incident["snapshot"] and _verify(folder, incident)["verified"]):
                    raise ValueError("verification_required")
                if outcome == "resolved":
                    incident.update(status=outcome, eligible=False, attempt_id=None, lease_expires_at=0)
                    _settle_code_repair(folder, incident, outcome)
                else:
                    # Queue the bounded recovery decision only. Creating a code
                    # workspace is an explicit request_code_repair action, so a
                    # finish call cannot allocate a new repair job as a side effect.
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
                        review_issues = self_heal._issues(folder)
                        evidence_blob = (str(incident.get("detail") or "") + " " + json.dumps(review_issues, ensure_ascii=False)).lower()
                        source_binding_proven = any(tok in evidence_blob for tok in (
                            "review_sample_source_unverified", "source_binding", "source_unverified"))
                        if not source_binding_proven:
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
                    busy_retry = (data.get("busy_retry") is True
                                  or data.get("reason_code") == "worker_busy"
                                  or data.get("state") == "worker_busy"
                                  or str(data.get("reason") or "").startswith("worker_busy"))
                    repair_rec = _incident_code_record(folder, incident) or {}
                    restart_wait = (repair_rec.get("status") == "installed_pending_restart"
                                    or data.get("state") == "awaiting_restart"
                                    or "awaits host restart" in str(data.get("reason") or ""))
                    if busy_retry or restart_wait:
                        if busy_retry:
                            rec = _incident_code_record(folder, incident) or {}
                            if rec.get("status") == "prepared":
                                incident["busy_wait_plan_id"] = rec.get("plan_id") or "__prepared__"
                        incident["not_before"] = time.time() + (1 if restart_wait else 2)
                    else:
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
                         'write': 'code_write', 'patch': 'code_write', 'patch_lines': 'code_write',
                         'multi_patch': 'code_write', 'restore_base': 'code_write',
                         'test': 'code_test', 'prepare': 'code_prepare'}.get(data.get('operation'))
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
        finally:
            incident.pop('write_active', None)
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
        record = _incident_code_record(folder, incident) or {}
        if record.get("status") == "restarted_pending_verify":
            _settle_code_repair(folder, incident, "needs_deep_repair")
        incident.update(status="queued", eligible=True,
                        queue_generation=incident.get("queue_generation", 0) + 1,
                        not_before=0 if changed else time.time() + 5)
        incident["recovery"] = {**_recovery_plan(folder, incident), "state": "original_check_failed"}
    outbox = state.pop("resume_outbox", None)
    if outbox:
        for receipt in state.get("receipts", []):
            if receipt["request_id"] == outbox["request_id"]:
                receipt.update(status="completed", evidence={**(receipt.get("evidence") or {}), "executed": True})
    _event(incident, "resume", "executed", "原流水线本地执行已结束；须重新认领并核验")
    _save(folder, state)


def unfreeze(root, folder):
    """Manually unfreeze a waiting_evidence/waiting_external incident and reset budget baselines."""
    folder = Path(folder)
    with _metadata_locked(root, folder):
        state = _load(folder)
        incident = state.get("incident")
        if not incident:
            return {"ok": True, "unfrozen": False, "status": None}
        if any(r.get("status") == "unknown" for r in state.get("receipts", [])):
            raise ValueError("unknown_receipt_quarantine")
        if incident.get("status") not in {"waiting_evidence", "waiting_external"}:
            return {"ok": True, "unfrozen": False, "status": incident.get("status")}
        budgets = _budgets(incident)
        incident.update(
            status="queued",
            eligible=True,
            not_before=0,
            release_count=0,
            budget_baseline=budgets["attempts_total"],
            install_baseline=budgets["installs_total"],
            regeneration_baseline=budgets["regenerations_total"],
            queue_generation=incident.get("queue_generation", 0) + 1,
        )
        if isinstance(incident.get("recovery"), dict):
            incident["recovery"] = {**incident["recovery"], "state": "unfrozen", "at": mf.now_iso()}
        _event(incident, "unfreeze", "queued", "人工解除冻结并重置当前预算基线，重新进入运维队列")
        _save(folder, state)
        return {"ok": True, "unfrozen": True, "incident": dict(incident)}


def takeover(root, folder):
    """Manually trigger immediate AI operations takeover, clearing cooldowns/freezes."""
    folder = Path(folder)
    with _metadata_locked(root, folder):
        state = _load(folder) or {"identity": _identity(folder), "incident": None, "receipts": [], "failures": {}}
        state.setdefault("identity", _identity(folder))
        state.setdefault("incident", None)
        state.setdefault("receipts", [])
        state.setdefault("failures", {})
        if any(r.get("status") == "unknown" for r in state.get("receipts", [])):
            raise ValueError("unknown_receipt_quarantine")
        incident = state.get("incident")
        if incident:
            _expire(incident)
            if incident.get("status") == "claimed":
                _save(folder, state)
                return {"ok": True, "triggered": False, "already_claimed": True, "incident": dict(incident)}
            if incident.get("status") in {"queued", "waiting_evidence", "waiting_external",
                                          "transient_deferred", "needs_human", "needs_deep_repair",
                                          "awaiting_execution"}:
                prior = incident.get("status")
                if prior in {"waiting_evidence", "waiting_external"}:
                    budgets = _budgets(incident)
                    incident.update(
                        budget_baseline=budgets["attempts_total"],
                        install_baseline=budgets["installs_total"],
                        regeneration_baseline=budgets["regenerations_total"],
                    )
                incident.update(
                    status="queued",
                    eligible=True,
                    not_before=0,
                    release_count=0,
                    trigger="manual_takeover",
                    queue_generation=incident.get("queue_generation", 0) + 1,
                )
                if isinstance(incident.get("recovery"), dict):
                    incident["recovery"] = {**incident["recovery"], "state": "manual_takeover", "at": mf.now_iso()}
                _event(incident, "takeover", "queued", "人工触发立即接手，清空冷却并重新入队")
                _save(folder, state)
                return {"ok": True, "triggered": True, "incident": dict(incident)}
        state = _discover(folder, force=True)
        incident = state.get("incident")
        if incident and incident.get("status") == "queued":
            incident.update(
                eligible=True,
                not_before=0,
                release_count=0,
                trigger="manual_takeover",
            )
            _event(incident, "takeover", "queued", "人工触发立即接手")
            _save(folder, state)
            return {"ok": True, "triggered": True, "incident": dict(incident)}
        _save(folder, state)
        return {"ok": True, "triggered": False, "reason": "当前批次没有待接手的异常或卡点阶段"}


def reconcile_unknown(root, folder, *, note=""):
    """Manually reconcile quarantined unknown receipts without replaying old actions."""
    folder = Path(folder)
    with _metadata_locked(root, folder):
        state = _load(folder)
        if not state:
            return {"ok": True, "reconciled": 0, "incident": None}
        receipts = state.get("receipts") or []
        unknown_rows = [r for r in receipts if isinstance(r, dict) and r.get("status") == "unknown"]
        incident = state.get("incident")
        if not unknown_rows:
            return {"ok": True, "reconciled": 0, "incident": dict(incident) if incident else None}
        for row in unknown_rows:
            row["status"] = "failed"
            row["reconciliation"] = {
                "original_status": "unknown",
                "reason": "reconciled_by_human",
                "note": _redact(str(note or ""))[:500],
                "at": mf.now_iso(),
                "replayed": False,
            }
        state.pop("resume_outbox", None)
        if incident and incident.get("status") not in {"resolved", "cancelled", "claimed"}:
            incident.update(
                status="queued",
                eligible=True,
                not_before=0,
                release_count=0,
                queue_generation=incident.get("queue_generation", 0) + 1,
            )
            _event(incident, "reconcile_unknown", "queued",
                   f"人工核对并解除 {len(unknown_rows)} 条未确认回执隔离（不重放旧动作）")
        _save(folder, state)
        return {"ok": True, "reconciled": len(unknown_rows), "incident": dict(incident) if incident else None}


def code_repair_diff(root, folder):
    """Return exportable diff bundle for the active incident's isolated code repair workspace."""
    folder = Path(folder)
    _active(root, folder)
    state = _load(folder)
    incident = state.get("incident") or {}
    record = _incident_code_record(folder, incident)
    if not record or not record.get("path"):
        return {"available": False, "files": [], "patch": "", "non_hot_reloadable": False}
    from . import operations_development as dev
    try:
        job = dev.load(record["path"])
        return dev.export_patch_bundle(job)
    except (OSError, ValueError, KeyError):
        return {"available": False, "files": [], "patch": "", "non_hot_reloadable": False}


def performance_summary(root, folder):
    """Return structured operational metrics and recent events for the current incident."""
    folder = Path(folder)
    _active(root, folder)
    state = _load(folder)
    incident = state.get("incident")
    if not incident:
        return {"has_incident": False, "status": None, "budgets": None, "events": []}
    unknown_receipts = sum(1 for r in state.get("receipts", []) if r.get("status") == "unknown")
    return {
        "has_incident": True,
        "incident_id": incident.get("incident_id"),
        "stage": incident.get("stage"),
        "status": incident.get("status"),
        "budgets": _budgets(incident),
        "attempts_count": len(incident.get("attempts") or []),
        "findings_count": len(incident.get("findings") or []),
        "repair_progress": incident.get("repair_progress"),
        "recovery": incident.get("recovery"),
        "unknown_receipts": unknown_receipts,
        "events": list(incident.get("events") or [])[-10:],
    }


def recover_resumes(root, enqueue):
    """Replay only FIFO admission after a crash; never replay a repair strategy."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        return
    for folder in root.iterdir():
        if supervisor._active_folder(root, folder):
            try:
                state = _load(folder)
                if not (isinstance(state, dict) and state.get("resume_outbox")):
                    continue
                _active(root, folder)
                _dispatch_resume(root, folder, lambda expected: enqueue(folder.name, folder, operations_expectation=expected))
            except (ValueError, OSError, RuntimeError):
                continue
