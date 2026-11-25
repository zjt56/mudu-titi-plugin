# -*- coding: utf-8 -*-
"""Per-unit anomaly quarantine ledger (`07_review/unit-anomalies.json`).

Keeps every existing validation rule intact while allowing a paper with
isolated unit failures in answers/explanations/knowledge to continue running
downstream stages (knowledge/assemble/validate/review/format_gate) normally.
Quarantined units remain explicitly recorded here and highlighted in RED on
both the paper card page and the question list until fixed.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re

from .question_identity import question_key

ANOMALIES_REL = "07_review/unit-anomalies.json"
QUARANTINE_STAGES = frozenset(("answers", "explanations", "knowledge"))


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _clean_error(value: object, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text or "校验未通过"


def anomalies_path(batch_dir) -> Path:
    return Path(batch_dir) / ANOMALIES_REL


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
    path = anomalies_path(batch_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def record_anomalies(
    batch_dir,
    stage: str,
    failed_units: dict[str, object],
    deterministic_failures: dict[str, dict] | None = None,
) -> dict:
    """Persist unit-scoped quarantined failures for one stage."""
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
    path = anomalies_path(batch_dir)
    if not path.is_file():
        return
    wanted = {str(uid).strip() for uid in (unit_ids or ()) if str(uid).strip()}
    if not wanted:
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


def quarantined_entries(batch_dir, stage: str | None = None) -> list[dict]:
    """Return active quarantined anomaly entries (`unit_id`, `stage`, `reason`, `deterministic`)."""
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


def quarantined_unit_ids(batch_dir, stage: str | None = None) -> set[str]:
    """Return unit_ids currently quarantined in 07_review/unit-anomalies.json."""
    return {row["unit_id"] for row in quarantined_entries(batch_dir, stage=stage)}


def should_quarantine(accepted: dict, pending: set, deterministic: dict) -> bool:
    """Quarantine only isolated failures when real units succeeded; never mask a full-batch outage."""
    return bool(pending) and bool(accepted)


def quarantined_response_for_unit(stage: str, unit: dict, error: object) -> dict:
    """Build a minimal, explicit quarantined single-unit artifact for downstream stages."""
    uid = str(unit["unit_id"])
    clean = _clean_error(error, limit=180)
    is_group = str(unit.get("unit_kind") or "") == "group"
    numbers = list(unit.get("numbers") or [])
    if stage == "answers":
        missing = {
            question_key(unit, n): [f"无（答案提取异常已隔离，待人工核对：{clean}）"]
            for n in numbers
        }
        return {
            "status": "completed",
            "artifacts": {"01_source_ans/answers.json": {"answers": {}, "evidence": {}, "missing": missing}},
            "notes": [f"单元 {uid} 答案校验未过，已隔离标红并继续下游流程：{clean}"],
            "findings": [],
        }
    if stage == "explanations":
        item = {
            "unit_id": uid,
            "explanation": "无",
            "articleSummary": "无",
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
