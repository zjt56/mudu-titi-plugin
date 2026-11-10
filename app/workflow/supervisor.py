# -*- coding: utf-8 -*-
"""Durable, local-only workflow watchdog. Importing this module starts no threads.

Only direct children of the active workflows root are examined. A tick reads a
small manifest/result ledger, never question images or a full review snapshot.
The dispatch callback owns recovery + execution (auto.advance already calls
self_heal.recover); invoking recovery here too would duplicate model calls.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import math
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import contracts, execution_state

LOGGER = logging.getLogger(__name__)
LEDGER = "supervisor.json"
RESULT = "supervisor-result.json"
REMOTE_STAGES = execution_state.REMOTE_STAGES
LOCAL_STAGES = execution_state.LOCAL_STAGES
OK = set(execution_state.TERMINAL_OK)
EXCLUDED = {"trash", "archive", "archives", "archived", ".workbench-trash"}


def _number(value, default, minimum):
    try:
        parsed = float(value)
        return max(minimum, parsed) if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class SupervisorConfig:
    interval: float = 30.0
    retry_base: float = 60.0
    retry_max: float = 900.0
    exhausted_cooldown: float = 900.0
    max_no_progress: int = 5
    max_dispatch_per_tick: int = 1

    @classmethod
    def from_env(cls):
        return cls(
            interval=_number(os.getenv("WF_SUPERVISOR_INTERVAL_SECONDS"), 30.0, 1.0),
            retry_base=_number(os.getenv("WF_SUPERVISOR_RETRY_SECONDS"), 60.0, 1.0),
            retry_max=_number(os.getenv("WF_SUPERVISOR_MAX_RETRY_SECONDS"), 900.0, 1.0),
            exhausted_cooldown=_number(os.getenv("WF_SUPERVISOR_EXHAUSTED_SECONDS"), 900.0, 60.0),
            max_no_progress=int(_number(os.getenv("WF_SUPERVISOR_MAX_NO_PROGRESS"), 5, 1)),
        )


def _read(path):
    try:
        if path.is_symlink():
            return {}
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _active_folder(root, folder):
    # Do not resolve a symlink into an archive, nor recreate a removed batch.
    try:
        return bool(not root.is_symlink() and root.is_dir()
                    and folder.parent == root and not folder.is_symlink() and folder.is_dir()
                    and not folder.name.startswith(".")
                    and folder.name.lower().strip("_-") not in EXCLUDED
                    and not (folder / "workflow").is_symlink()
                    and not (folder / "workflow/manifest.json").is_symlink()
                    and (folder / "workflow/manifest.json").is_file())
    except OSError:
        return False


def _write_existing(folder, name, value):
    parent = folder / "workflow"
    target = parent / name
    if folder.is_symlink() or parent.is_symlink() or not parent.is_dir() or target.is_symlink():
        return False
    temporary = parent / ("." + name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(target)
        return True
    except OSError:
        return False
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


def record_result(folder, result):
    """Persist a compact worker outcome, including failures with no manifest write."""
    if not isinstance(result, dict):
        result = {"error": str(result)[:800]}
    compact = {key: result[key] for key in ("action", "status", "stage", "error", "reason",
               "message", "cooldown_seconds", "error_kind", "retryable") if key in result}
    # Keep exception/model output bounded; never persist full raw responses.
    for key, value in tuple(compact.items()):
        if isinstance(value, str):
            compact[key] = value[:1000]
    return _write_existing(Path(folder), RESULT, compact)


@contextlib.contextmanager
def _try_file_lock(path):
    """Acquire a nonblocking OS lock, never remove its inode."""
    handle = None
    acquired = False
    if path.is_symlink():
        yield False
        return
    try:
        handle = path.open("a+b")
        if os.name == "nt":
            import msvcrt
            handle.seek(0, os.SEEK_END)
            if not handle.tell():
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        acquired = True
    except OSError:
        pass
    try:
        yield acquired
    finally:
        if handle is not None:
            if acquired:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


def _auto_lock_path(folder):
    key = hashlib.sha256(str(Path(folder).resolve()).encode("utf-8")).hexdigest()
    return Path(tempfile.gettempdir()) / ("mudu-auto-" + key + ".lock")


def _agent_lock_busy(path):
    """True when any live agent holds the lock, including Windows shared slots.

    Windows has no shared flock: ``local_agent`` gives exclusive callers bytes
    0..1023 and each shared caller one byte in 1..1023. Probing a single byte
    therefore misses a running shared model call, and the supervisor would
    dispatch a duplicate advance on top of it. Probe the whole exclusive range
    so both layouts conflict.
    """
    path = Path(path)
    if not path.exists() or path.is_symlink():
        return False
    if os.name != "nt":
        with _try_file_lock(path) as acquired:
            return not acquired
    import msvcrt
    from .local_agent import WINDOWS_LOCK_RANGE
    try:
        handle = path.open("a+b")
    except OSError:
        return True
    try:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        if size < WINDOWS_LOCK_RANGE:
            handle.write(b"\0" * (WINDOWS_LOCK_RANGE - size))
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, WINDOWS_LOCK_RANGE)
        except OSError:
            return True
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, WINDOWS_LOCK_RANGE)
        except OSError:
            pass
        return False
    finally:
        handle.close()


def execution_busy(folder, *, include_auto=True):
    """Probe the same locks used by auto, runner and live model/import workers.

    A stale running manifest alone is deliberately not a live-worker signal.
    An orphaned child surviving a backend restart still holds the agent lock.
    """
    from . import local_agent
    key = hashlib.sha256(str(Path(local_agent.__file__).resolve().parent).encode()).hexdigest()
    paths = [Path(folder) / "workflow/.run.lock"]
    if include_auto:
        paths.insert(0, _auto_lock_path(folder))
    for path in paths:
        if not path.exists():
            continue
        with _try_file_lock(path) as acquired:
            if not acquired:
                return True
    return _agent_lock_busy(Path(tempfile.gettempdir()) / ("mudu-local-agent-" + key + ".lock"))


def clear_exhausted(folder):
    """手动点“继续处理”时清掉 exhausted 长冷却，使工作从当前阶段立即续跑。

    只清冷却/暂定标记，不动阶段状态与产物；不影响质量结论。
    """
    folder = Path(folder)
    state = _read(folder / "workflow" / LEDGER)
    if not isinstance(state, dict) or not state:
        return
    if state.get("status") in {"exhausted", "waiting_external", "scheduled", "cooldown"}:
        state.update(status="progress", next_retry_at=0, retry_reservation_at=0,
                     no_progress_attempts=0)
        state.pop("escalation", None)
        _write_existing(folder, LEDGER, state)


@contextlib.contextmanager
def advance_claim(folder):
    """Hold auto's existing per-batch lock before a workbench worker is started.

    The owner must call auto._advance_locked, not reacquire auto.advance's lock.
    Transfer between the dispatching thread and worker is safe: ownership lives
    on the open file handle. Busy external work is never queued behind a lock.
    """
    folder = Path(folder)
    from . import operations
    with _try_file_lock(_auto_lock_path(folder)) as acquired:
        yield bool(acquired and not operations.lease_active(folder)
                   and not execution_busy(folder, include_auto=False))


def _regular_file(folder, relative):
    path = folder
    for part in Path(relative).parts:
        path = path / part
        if path.is_symlink():
            return None
    return path if path.is_file() else None


def _digest(path):
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ""


DEFER_MARK = "已延后"


def _deferred_stages(manifest):
    """延后（外部依赖暂不可用）且并非质量阻断的阶段：调度时应先跑其它阶段。"""
    out = []
    for stage_id, record in (manifest.get("stages") or {}).items():
        if not isinstance(record, dict):
            continue
        if record.get("status") == "pending" and DEFER_MARK in str(record.get("notes") or ""):
            out.append(stage_id)
    return out


def local_complete(folder, manifest):
    """Compatibility entrypoint shared with the workbench and CLI projections."""
    return execution_state.local_complete(folder, manifest)


def _remote_boundary(folder, manifest):
    stages = manifest.get("stages") or {}
    # Actual remote submission/receipt is an authorization boundary. An old
    # overall pending_confirmation/review_gate alone is NOT local completion.
    upload = stages.get("upload") or {}
    if upload.get("status") in {"completed", "approved", "running"}:
        return True
    for relative in ("07_upload/receipt.json", "receipt.json"):
        path = _regular_file(folder, relative)
        if path is not None:
            receipt = _read(path)
            if receipt.get("remote_confirmation_required") or receipt.get("stage") == "pending_confirmation" \
                    or receipt.get("id") or receipt.get("upload_id") or receipt.get("remote_id"):
                return True
    return False


def _progress(manifest):
    stages = manifest.get("stages") or {}
    # Status/notes/attempt timestamps can oscillate on every failed invocation.
    # Only validated terminal progress and bound input/output changes reset the
    # no-progress budget; different error wording cannot evade exhaustion.
    data = {"inputs": manifest.get("inputs") or {}, "stages": {}}
    for stage_id in LOCAL_STAGES:
        record = stages.get(stage_id) or {}
        data["stages"][stage_id] = {
            "status": record.get("status") if record.get("status") in OK else "unfinished",
            "output_hashes": record.get("output_hashes") or {},
            "input_hashes": record.get("input_hashes") or {},
        }
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _cooldown(folder):
    try:
        path = _regular_file(folder, "workflow/agent-runs/.rate-limit-cooldown")
        if path is None:
            return 0.0
        value = float(path.read_text().strip())
        return value if math.isfinite(value) else 0.0
    except (OSError, ValueError):
        return 0.0


def _transient(result, healing):
    # Structured provider failures only; never infer rate limits from arbitrary
    # model prose containing an HTTP number. Content/plan errors still exhaust.
    codes = {"config", "spawn", "timeout", "lock_timeout", "rate_limit", "quota",
             "network", "transport", "unavailable", "service_unavailable", "upstream"}
    events = healing.get("events") or []
    latest = events[-1] if isinstance(events, list) and events and isinstance(events[-1], dict) else {}
    return bool(result.get("retryable") is True or result.get("action") == "cooldown"
                or result.get("error_kind") in codes or latest.get("error_code") in codes)


def _healing_input_change(folder, manifest, healing):
    """Only byte-bound evidence for the diagnosed stage invalidates its cooldown."""
    stage_id = healing.get("last_stage")
    if not stage_id:
        events = healing.get("events")
        latest = events[-1] if isinstance(events, list) and events else None
        stage_id = latest.get("stage") if isinstance(latest, dict) else None
    if not isinstance(stage_id, str) or stage_id not in LOCAL_STAGES:
        return False
    last_inputs = healing.get("last_inputs")
    recorded = last_inputs.get(stage_id) if isinstance(last_inputs, dict) else None
    if not isinstance(recorded, dict) or not recorded:
        # Legacy/missing evidence is not proof that the failed input changed.
        return False
    from . import manifest as mf
    try:
        current = mf.input_hashes(folder, manifest, contracts.STAGE_BY_ID[stage_id])
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return False
    if not current or current == recorded:
        return False
    # Persist the evidence identity so the same stale diagnosis cannot reset
    # the supervisor budget repeatedly if a worker has not updated self-heal.
    return hashlib.sha256(json.dumps(
        [stage_id, recorded, current], sort_keys=True).encode()).hexdigest()


def _new_deferred_work(folder, manifest, deferred, state):
    """Grant one opportunity per newly ready independent stage/input binding."""
    from . import manifest as mf
    seen = state.get("deferred_work")
    seen = dict(seen) if isinstance(seen, dict) else {}
    new_work = False
    for stage_id in mf.ready_stages(folder, manifest, allow_unqualified=True):
        record = manifest["stages"].get(stage_id) or {}
        if (stage_id not in LOCAL_STAGES or stage_id in deferred
                or record.get("status") not in {"pending", "invalidated"}):
            continue
        inputs = mf.input_hashes(folder, manifest, contracts.STAGE_BY_ID[stage_id])
        binding = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
        if seen.get(stage_id) != binding:
            new_work = True
            seen[stage_id] = binding
    state["deferred_work"] = seen
    return new_work


def _dispatch_order(folder):
    # Durable ordering also survives restarts; absent/invalid timestamps go first.
    state = _read(folder / "workflow" / LEDGER)
    return (_number(state.get("last_dispatched_at"), -1, 0), folder.name)


class WorkflowSupervisor:
    def __init__(self, workflows, dispatch, *, is_active=None, clock=None, config=None, busy=None):
        self.workflows = Path(workflows).absolute()
        self.dispatch = dispatch
        self.is_active = is_active or (lambda name: False)
        self.clock = clock or time.time
        self.config = config or SupervisorConfig.from_env()
        self.busy = busy or execution_busy
        self._stop = threading.Event()
        self._thread = None
        self._tick_lock = threading.Lock()
        self._last_tick_at = None

    def health(self):
        """Expose actual watchdog liveness separately from any batch's success."""
        return {"running": bool(self._thread and self._thread.is_alive()),
                "last_tick_at": self._last_tick_at, "interval_seconds": self.config.interval}

    def _tick_batch(self, folder, now):
        manifest = _read(folder / "workflow/manifest.json")
        if not isinstance(manifest.get("stages"), dict):
            return "invalid_manifest"
        if _remote_boundary(folder, manifest):
            return "remote_boundary"
        if self.is_active(folder.name) or self.busy(folder):
            return "active"
        state = _read(folder / "workflow" / LEDGER)
        if local_complete(folder, manifest):
            if state.get("status") != "complete":
                state.update(status="complete", next_retry_at=0, updated_at=now)
                state.pop("escalation", None)
                _write_existing(folder, LEDGER, state)
            return "complete"
        progress = _progress(manifest)
        changed = bool(state.get("progress") and state["progress"] != progress)
        # next_retry_at is the displayed maximum of independent cooldowns. Keep
        # the dispatch reservation separate so a copied self-heal deadline does
        # not survive after its diagnosed inputs have demonstrably changed.
        reservation = _number(state.get("retry_reservation_at", state.get("next_retry_at")), 0, 0)
        if changed:
            state.update(no_progress_attempts=0, status="progress")
            state.pop("escalation", None)
            reservation = 0
        healing = _read(folder / "workflow/self-heal.json")
        healing_retry = _number(healing.get("next_retry_at"), 0, 0)
        inputs_changed = healing_retry > now and _healing_input_change(folder, manifest, healing)
        new_input_evidence = bool(inputs_changed and state.get("healing_inputs_rechecked") != inputs_changed)
        if inputs_changed:
            healing_retry = 0
        if new_input_evidence:
            state.update(no_progress_attempts=0, status="progress",
                         healing_inputs_rechecked=inputs_changed)
            state.pop("escalation", None)
        # Progress metadata alone cannot cancel a self-heal diagnosis, and a
        # provider rate limit always wins, even when actual input bytes changed.
        retry_at = max(reservation, _cooldown(folder), healing_retry)
        if retry_at > now:
            if (retry_at != state.get("next_retry_at") or state.get("progress") != progress
                    or reservation != state.get("retry_reservation_at") or new_input_evidence):
                state.update(progress=progress, next_retry_at=retry_at,
                             retry_reservation_at=reservation, updated_at=now)
                _write_existing(folder, LEDGER, state)
            return "cooldown"
        # A deferred marker alone cannot replenish a failed batch's budget.
        # New independent ready work gets one chance per actual input binding.
        deferred = _deferred_stages(manifest)
        new_work = _new_deferred_work(folder, manifest, deferred, state) if deferred else False
        if new_work and state.get("status") in {"exhausted", "waiting_external"}:
            state.update(status="scheduled", no_progress_attempts=0,
                         escalation="被延后的阶段：%s；新增独立工作继续推进" % "、".join(sorted(deferred)),
                         updated_at=now)
            _write_existing(folder, LEDGER, state)
        attempts = int(_number(state.get("no_progress_attempts"), 0, 0))
        result = _read(folder / "workflow" / RESULT)
        temporary_failure = _transient(result, healing)
        exhausted = attempts >= self.config.max_no_progress and not temporary_failure
        if exhausted and state.get("status") != "exhausted":
            state.update(status="exhausted", next_retry_at=now + self.config.exhausted_cooldown,
                         retry_reservation_at=now + self.config.exhausted_cooldown,
                         escalation="连续调度无有效本地进展；已降频，保留质量阻断，等待输入/配置修正或长冷却探测",
                         updated_at=now)
            _write_existing(folder, LEDGER, state)
            return "exhausted"
        delay = self.config.exhausted_cooldown if exhausted else min(
            self.config.retry_max, self.config.retry_base * (2 ** min(attempts, 20)))
        state.update(version=1,
                     status="exhausted" if exhausted else ("waiting_external" if temporary_failure else "scheduled"),
                     progress=progress, no_progress_attempts=attempts + 1,
                     next_retry_at=now + delay, retry_reservation_at=now + delay,
                      last_dispatched_at=now, updated_at=now)
        detail = json.dumps(result, sort_keys=True, ensure_ascii=False)[:2000]
        state["error_signature"] = hashlib.sha256(detail.encode()).hexdigest()[:16]
        state["last_result"] = result
        # Commit the retry reservation BEFORE dispatch so a crash cannot lose it.
        if not _active_folder(self.workflows, folder) or not _write_existing(folder, LEDGER, state):
            return "removed"
        try:
            dispatched = self.dispatch(folder.name)
            if isinstance(dispatched, dict) and dispatched.get("started") is False:
                state.update(status="active", no_progress_attempts=attempts,
                             next_retry_at=now + self.config.interval,
                             retry_reservation_at=now + self.config.interval)
                _write_existing(folder, LEDGER, state)
                return "active"
        except Exception as exc:  # a single broken batch must not kill supervision
            state["last_result"] = {"error": str(exc)[:800]}
            _write_existing(folder, LEDGER, state)
            return "dispatch_error"
        return "dispatched"

    def one_tick(self):
        """Run one bounded scan without sleeping or invoking any model directly."""
        if not self._tick_lock.acquire(blocking=False):
            return {"status": "busy", "batches": {}}
        outcomes = {}
        try:
            root = self.workflows
            if root.is_symlink() or not root.is_dir():
                return {"status": "idle", "batches": outcomes}
            dispatched = 0
            folders = [folder for folder in root.iterdir() if _active_folder(root, folder)]
            for folder in sorted(folders, key=_dispatch_order):
                if self._stop.is_set() or not _active_folder(root, folder):
                    continue
                if dispatched >= self.config.max_dispatch_per_tick:
                    break
                try:
                    with _try_file_lock(folder / "workflow/.supervisor.lock") as acquired:
                        outcome = self._tick_batch(folder, self.clock()) if acquired else "active"
                    outcomes[folder.name] = outcome
                    dispatched += outcome in {"dispatched", "dispatch_error"}
                except (OSError, ValueError, TypeError) as exc:
                    outcomes[folder.name] = "error"
                    LOGGER.warning("Workflow supervisor skipped %s: %s", folder.name, exc)
            return {"status": "ok", "batches": outcomes}
        finally:
            self._last_tick_at = self.clock()
            self._tick_lock.release()

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return False
        self._stop.clear()
        def watch():
            while not self._stop.is_set():
                try:
                    self.one_tick()
                except Exception:
                    LOGGER.exception("Workflow watchdog tick failed; will retry")
                self._stop.wait(self.config.interval)
        self._thread = threading.Thread(target=watch, daemon=True, name="workflow-supervisor")
        self._thread.start()
        return True

    def stop(self, timeout=5.0):
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        return thread is None or not thread.is_alive()
