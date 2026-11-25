# -*- coding: utf-8 -*-
"""Per-unit anomaly quarantine ledger (`07_review/unit-anomalies.json`).

Keeps every existing validation rule intact while allowing a paper with
isolated deterministic unit failures in types/answers/explanations/knowledge to
continue running downstream stages normally. Quarantined units are recorded both
in this ledger (under a process+thread lock with unique temp files) and inside
the quarantined artifact rows (`quarantined: true` / `quarantined: {...}`), and
blocked at upload time unless explicitly fixed and cleared.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

from .question_identity import question_key

ANOMALIES_REL = "07_review/unit-anomalies.json"
QUARANTINE_STAGES = frozenset(("types", "answers", "explanations", "knowledge"))

_THREAD_LOCK = threading.Lock()
_TRANSIENT_ERROR_RE = re.compile(
    r"timeout|timed\s*out|rate[_\s-]?limit|429|quota|insufficient[_\s-]?balance|"
    r"authentication|invalid[_\s-]?api[_\s-]?key|401|403|connection|network|"
    r"temporarily\s+unavailable|service\s+unavailable|502|503|504|context_changed|"
    r"changed\s+during|provider\b|interrupted|aborted|cancelled",
    re.I,
)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _clean_error(value: object, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text or "校验未通过"


def anomalies_path(batch_dir) -> Path:
    return Path(batch_dir) / ANOMALIES_REL


@contextmanager
def _anomaly_lock(batch_dir, timeout: float = 15.0):
    """Serialize read-modify-write across both threads and processes."""
    lock_path = Path(batch_dir) / "07_review" / ".unit-anomalies.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _THREAD_LOCK:
        handle = None
        locked = False
        try:
            handle = lock_path.open("a+b")
            deadline = time.monotonic() + max(0.5, float(timeout))
            while True:
                try:
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
                    locked = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Timed out acquiring anomaly ledger lock: {lock_path}")
                    time.sleep(0.02)
            yield
        finally:
            if handle is not None:
                if locked:
                    try:
                        if os.name == "nt":
                            import msvcrt
                            handle.seek(0)
                            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
                try:
                    handle.close()
                except OSError:
                    pass


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex[:8]}.tmp"
    )
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def load_anomalies(batch_dir) -> dict:
    path = anomalies_path(batch_dir)
    if not path.is_file():
        return {"version": 1, "units": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {"version": 1, "units": {}}
    if not isinstance(data, dict) or not isinstance(data.get("units"), dict):
        return {"version": 1, "units": {}}
    return data


def _save_anomalies(batch_dir, data: dict) -> None:
    _atomic_write_json(anomalies_path(batch_dir), data)


def record_anomalies(
    batch_dir,
    stage: str,
    failed_units: dict[str, object],
    deterministic_failures: dict[str, dict] | None = None,
) -> dict:
    """Persist unit-scoped quarantined failures for one stage (concurrency-safe)."""
    with _anomaly_lock(batch_dir):
        data = load_anomalies(batch_dir)
        units = dict(data.get("units") or {})
        det = deterministic_failures or {}
        stamp = _now_iso()
        for uid, raw_err in (failed_units or {}).items():
            key = str(uid or "").strip()
            if not key:
                continue
            existing = [
                e for e in (units.get(key) or [])
                if isinstance(e, dict) and str(e.get("stage") or "") != stage
            ]
            det_info = det.get(key) if isinstance(det.get(key), dict) else None
            existing.append({
                "stage": stage,
                "error": _clean_error(raw_err),
                "deterministic": bool(det_info),
                "quarantined": True,
                "recorded_at": stamp,
            })
            units[key] = existing
        updated = {"version": 1, "updated_at": stamp, "units": units}
        _save_anomalies(batch_dir, updated)
        return updated


def clear_stage_anomalies(batch_dir, stage: str, unit_ids) -> None:
    """Remove resolved anomaly entries for units that passed strict validation."""
    wanted = {str(uid).strip() for uid in (unit_ids or ()) if str(uid).strip()}
    if not wanted:
        return
    with _anomaly_lock(batch_dir):
        path = anomalies_path(batch_dir)
        if not path.is_file():
            return
        data = load_anomalies(batch_dir)
        units = dict(data.get("units") or {})
        changed = False
        for uid in list(units):
            if uid not in wanted:
                continue
            before = units.get(uid) or []
            after = [e for e in before if isinstance(e, dict) and str(e.get("stage") or "") != stage]
            if len(after) != len(before):
                changed = True
                if after:
                    units[uid] = after
                else:
                    units.pop(uid, None)
        if changed:
            _save_anomalies(batch_dir, {"version": 1, "updated_at": _now_iso(), "units": units})


def clear_artifact_quarantine_flag(batch_dir, stage: str, unit_ids) -> None:
    """Clear `quarantined` flag from the stage artifact for manually fixed units."""
    wanted = {str(uid).strip() for uid in (unit_ids or ()) if str(uid).strip()}
    if not wanted:
        return
    batch = Path(batch_dir)
    rel_map = {
        "types": "03_types/types.json",
        "explanations": "04_knowledge/explanations.json",
        "knowledge": "04_knowledge/knowledge.json",
        "answers": "01_source_ans/answers.json",
    }
    rel = rel_map.get(stage)
    if not rel:
        return
    target = batch / rel
    if not target.is_file():
        return
    with _anomaly_lock(batch):
        try:
            payload = json.loads(target.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        changed = False
        if stage == "answers":
            qmap = payload.get("quarantined")
            if isinstance(qmap, dict) and qmap:
                # Resolve question keys belonging to the wanted unit_ids via confirmed structure.
                keys_to_drop = set()
                confirmed = batch / "01_source/structure.confirmed.json"
                if confirmed.is_file():
                    try:
                        units = json.loads(confirmed.read_text(encoding="utf-8-sig")).get("units") or []
                        for unit in units:
                            if isinstance(unit, dict) and str(unit.get("unit_id") or "") in wanted:
                                for n in (unit.get("numbers") or []):
                                    keys_to_drop.add(question_key(unit, n))
                    except (OSError, ValueError):
                        pass
                for k in list(qmap):
                    if k in keys_to_drop or k in wanted:
                        qmap.pop(k, None)
                        changed = True
                if changed and not qmap:
                    payload.pop("quarantined", None)
        else:
            items = payload.get("items")
            if isinstance(items, list):
                for row in items:
                    if isinstance(row, dict) and str(row.get("unit_id") or "") in wanted:
                        if row.pop("quarantined", None) is not None:
                            changed = True
        if changed:
            _atomic_write_json(target, payload)


def quarantined_entries(batch_dir, stage: str | None = None) -> list[dict]:
    """Return active quarantined anomaly entries from `07_review/unit-anomalies.json`."""
    data = load_anomalies(batch_dir)
    rows: list[dict] = []
    for uid, entries in sorted((data.get("units") or {}).items()):
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("quarantined"):
                continue
            entry_stage = str(entry.get("stage") or "")
            if stage is not None and entry_stage != stage:
                continue
            rows.append({
                "unit_id": str(uid),
                "stage": entry_stage,
                # record_anomalies 写入的是 error；reason 仅兼容旧台账。
                "reason": str(entry.get("reason") or entry.get("error") or ""),
                "deterministic": bool(entry.get("deterministic")),
            })
    return rows


def artifact_quarantined_entries(batch_dir, stage: str | None = None) -> list[dict]:
    """Scan stage artifacts for explicit `quarantined` markers as a second defense layer."""
    batch = Path(batch_dir)
    rows: list[dict] = []
    item_files = (
        ("types", "03_types/types.json"),
        ("explanations", "04_knowledge/explanations.json"),
        ("knowledge", "04_knowledge/knowledge.json"),
    )
    for st, rel in item_files:
        if stage is not None and st != stage:
            continue
        path = batch / rel
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        for item in (payload.get("items") or []) if isinstance(payload, dict) else []:
            if isinstance(item, dict) and item.get("quarantined") is True:
                uid = str(item.get("unit_id") or "").strip()
                if uid:
                    ev = item.get("evidence")
                    reason = str(ev[0]) if isinstance(ev, list) and ev else "产物带有未解除隔离标记"
                    rows.append({"unit_id": uid, "stage": st, "reason": _clean_error(reason), "deterministic": True})
    if stage is None or stage == "answers":
        ans_path = batch / "01_source_ans/answers.json"
        if ans_path.is_file():
            try:
                ans = json.loads(ans_path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                ans = {}
            qmap = ans.get("quarantined") if isinstance(ans, dict) else None
            if isinstance(qmap, dict) and qmap:
                key_to_uid: dict[str, str] = {}
                confirmed = batch / "01_source/structure.confirmed.json"
                if confirmed.is_file():
                    try:
                        for unit in (json.loads(confirmed.read_text(encoding="utf-8-sig")).get("units") or []):
                            if isinstance(unit, dict) and unit.get("unit_id"):
                                for n in (unit.get("numbers") or []):
                                    key_to_uid[question_key(unit, n)] = str(unit["unit_id"])
                    except (OSError, ValueError):
                        pass
                seen_uids: set[str] = set()
                for qkey, reason in sorted(qmap.items()):
                    uid = key_to_uid.get(str(qkey), str(qkey))
                    if uid in seen_uids:
                        continue
                    seen_uids.add(uid)
                    rows.append({
                        "unit_id": uid,
                        "stage": "answers",
                        "reason": _clean_error(reason or "答案产物带有未解除隔离标记"),
                        "deterministic": True,
                    })
    return rows


def all_quarantined_entries(batch_dir, stage: str | None = None) -> list[dict]:
    """Union of ledger entries and artifact-level `quarantined` markers (deduped by unit+stage)."""
    merged: dict[tuple[str, str], dict] = {}
    for entry in quarantined_entries(batch_dir, stage=stage):
        merged[(entry["unit_id"], entry["stage"])] = entry
    for entry in artifact_quarantined_entries(batch_dir, stage=stage):
        merged.setdefault((entry["unit_id"], entry["stage"]), entry)
    return [merged[k] for k in sorted(merged)]


def quarantined_unit_ids(batch_dir, stage: str | None = None) -> set[str]:
    """Return unit_ids currently quarantined in ledger or artifacts."""
    return {row["unit_id"] for row in all_quarantined_entries(batch_dir, stage=stage)}


def _is_transient_error(err: object) -> bool:
    if err is None:
        return False
    code = getattr(err, "code", None)
    if code in ("timeout", "rate_limit", "quota", "authentication", "provider", "context_changed", "lock_timeout"):
        return True
    return bool(_TRANSIENT_ERROR_RE.search(str(err)))


def quarantinable_units(
    accepted: dict,
    pending: set,
    deterministic: dict,
    errors: dict | None = None,
) -> set[str]:
    """Return the subset of `pending` units safe to quarantine.

    Never quarantines when 0 units succeeded (prevents masking a whole-batch/validator outage),
    and never quarantines units whose failure is transient (timeout/rate-limit/auth/provider).
    """
    if not pending or not accepted:
        return set()
    det_ids = set(deterministic or ())
    err_map = errors or {}
    eligible: set[str] = set()
    for uid in pending:
        if uid in det_ids:
            eligible.add(uid)
        elif not _is_transient_error(err_map.get(uid)):
            eligible.add(uid)
    return eligible


def should_quarantine(
    accepted: dict,
    pending: set,
    deterministic: dict,
    errors: dict | None = None,
) -> bool:
    """Quarantine only when real units succeeded AND no pending unit is a transient outage.

    If a batch has 1 deterministic failure mixed with 9 transient timeouts, returns False
    so the 9 transient units retry instead of being mis-quarantined.
    """
    if not pending or not accepted:
        return False
    return quarantinable_units(accepted, pending, deterministic, errors) == set(pending)


def quarantined_response_for_unit(stage: str, unit: dict, error: object, *, batch_dir=None) -> dict:
    """Build a minimal, explicit quarantined single-unit artifact for downstream stages."""
    uid = str(unit["unit_id"])
    clean = _clean_error(error, limit=180)
    is_group = str(unit.get("unit_kind") or "") == "group"
    numbers = list(unit.get("numbers") or [])
    if stage == "types":
        from . import quarantine_policy
        return quarantine_policy.quarantined_type_response(batch_dir or Path("."), unit, error)
    if stage == "answers":
        missing = {
            question_key(unit, n): [f"无（答案提取异常已隔离，待人工核对：{clean}）"]
            for n in numbers
        }
        quarantined_map = {question_key(unit, n): clean for n in numbers}
        return {
            "status": "completed",
            "artifacts": {
                "01_source_ans/answers.json": {
                    "answers": {},
                    "evidence": {},
                    "missing": missing,
                    "quarantined": quarantined_map,
                }
            },
            "notes": [f"单元 {uid} 答案校验未过，已隔离标红并继续下游流程：{clean}"],
            "findings": [],
        }
    if stage == "explanations":
        item = {
            "unit_id": uid,
            "explanation": "无",
            "articleSummary": "无",
            "quarantined": True,
            "evidence": [f"未提供解析材料（解析提取异常已隔离，待人工核对：{clean}）"],
            "subquestions": [{"number": n, "explanation": "无"} for n in numbers] if is_group else [],
        }
        return {
            "status": "completed",
            "artifacts": {"04_knowledge/explanations.json": {"items": [item]}},
            "notes": [f"单元 {uid} 解析校验未过，已隔离标红并继续下游流程：{clean}"],
            "findings": [],
        }
    if stage == "knowledge":
        item = {
            "unit_id": uid,
            "knowledge": [],
            "quarantined": True,
            "evidence": [f"知识点标签异常已隔离，待人工核对：{clean}"],
            "subquestions": [{"number": n, "knowledge": []} for n in numbers] if is_group else [],
        }
        return {
            "status": "completed",
            "artifacts": {"04_knowledge/knowledge.json": {"items": [item]}},
            "notes": [f"单元 {uid} 知识点校验未过，已隔离标红并继续下游流程：{clean}"],
            "findings": [],
        }
    raise ValueError(f"Unsupported quarantine stage: {stage}")
