"""Read-only execution projections shared by CLI, supervision and the workbench.

Execution completion never grants quality approval or remote authorization.
Existing stage names, statuses and explicit unqualified-delivery policy are kept.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import contracts

RUNTIME_VERSION = "workflow-runtime-v2"

PENDING = "pending"
RUNNING = "running"
COMPLETED = "completed"
APPROVED = "approved"
NEEDS_REVIEW = "needs_review"
BLOCKED = "blocked"
FAILED = "failed"
INVALIDATED = "invalidated"
SKIPPED = "skipped"
TERMINAL_OK = (COMPLETED, APPROVED, SKIPPED)
REMOTE_STAGES = frozenset(("upload_gate", "upload", "publish_gate", "publish"))
LOCAL_STAGES = tuple(stage.id for stage in contracts.STAGES if stage.id not in REMOTE_STAGES)


def executed(record, *, allow_unqualified=False):
    """Whether a qualified stage result exists; partial execution is not a handoff.

    allow_unqualified=True additionally accepts a stage that executed fully but
    carries pending quality (needs_review + execution_completed + continue_unqualified),
    e.g. review_gate that found genuine manual-review differences. Such a stage may
    continue to assemble the upload batch while remaining release_ready=False and
    never being treated as quality approval.
    """
    if not isinstance(record, dict) or record.get("rebuild_required"):
        return False
    if record.get("status") in TERMINAL_OK:
        return True
    if (allow_unqualified and record.get("status") == NEEDS_REVIEW
            and record.get("execution_completed") is True
            and record.get("continue_unqualified") is True
            and not record.get("release_ready")):
        return True
    return False


def dependency_satisfied(manifest, stage_id, *, allow_unqualified=False):
    stages = manifest.get("stages") or {}
    if not executed(stages.get(stage_id), allow_unqualified=allow_unqualified):
        return False
    return not any((stages.get(dep) or {}).get("rebuild_required")
                   for dep in contracts.upstream_of(stage_id))


def projection(manifest, *, stage_ids=None, allow_unqualified=True):
    """Return bounded execution and quality facts without changing any ledger."""
    ids = tuple(contracts.STAGE_ORDER if stage_ids is None else stage_ids)
    stages = manifest.get("stages") or {}
    # Retained descendants remain executed while an ancestor is repaired. Their
    # consumption is guarded separately by dependency_satisfied.
    done = []
    for sid in ids:
        if executed(stages.get(sid), allow_unqualified=allow_unqualified) and all(
                executed(stages.get(dep), allow_unqualified=allow_unqualified) for dep in contracts.upstream_of(sid)):
            done.append(sid)
    differences = [sid for sid in ids if isinstance(stages.get(sid), dict) and
                   (stages[sid].get("status") in (BLOCKED, NEEDS_REVIEW, FAILED)
                    or stages[sid].get("quality_status") in ("failed", "incomplete", "unverified")
                    or stages[sid].get("release_ready") is False)]
    return {"done": len(done), "total": len(ids), "executed_stages": done,
            "current": next((sid for sid in ids if sid not in done), None),
            "execution_completed": len(done) == len(ids),
            "quality": "differences" if differences else "passed" if len(done) == len(ids) else "pending",
            "quality_stages": differences}


def _regular_file(folder, relative):
    path = folder
    for part in Path(relative).parts:
        path = path / part
        if path.is_symlink():
            return None
    return path if path.is_file() else None


def local_complete(folder, manifest):
    """The existing local completion contract, including exact final-file binding.

    An executed quality difference remains a difference. A format pass alone
    cannot manufacture missing execution, and a pending rebuild must finish.
    """
    folder = Path(folder)
    stages = manifest.get("stages") or {}
    if (stages.get("format_gate") or {}).get("status") != COMPLETED:
        return False
    # 本地流程完成 = 打包出可上传批次即可。审核闸门若「已执行完成、差异待人工」
    # (needs_review + execution_completed + continue_unqualified + release_ready=False)，
    # 仍算本地执行完成（用户流程：一审二审结束 + 打包完成 = 流程跑完，随后人工审核上传），
    # 但不等于质量放行、不自动上传。若用 allow_unqualified，则全部本地阶段执行完成即通过。
    for sid in LOCAL_STAGES:
        if not executed(stages.get(sid), allow_unqualified=True):
            return False
        if not all(executed(stages.get(dep), allow_unqualified=True) for dep in contracts.upstream_of(sid)):
            return False
    for sid in LOCAL_STAGES:
        record = stages.get(sid) or {}
        if record.get("status") in (BLOCKED, NEEDS_REVIEW):
            if any(_regular_file(folder, rel) is None for rel in contracts.STAGE_BY_ID[sid].produces):
                return False
    report_path = _regular_file(folder, "07_review/upload-format.json")
    final_path = _regular_file(folder, "06_batch/batch_meta.json")
    if report_path is None or final_path is None:
        return False
    try:
        report = json.loads(report_path.read_text(encoding="utf-8-sig"))
        if not isinstance(report, dict) or report.get("passed") is not True:
            return False
        digest = hashlib.sha256()
        with final_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return bool(report.get("final_sha256") and report["final_sha256"] == digest.hexdigest())
    except (OSError, ValueError, UnicodeError):
        return False


def derive_status(manifest):
    """Keep public batch status names; local readiness includes the final gate."""
    stages = manifest.get("stages") or {}
    statuses = {sid: rec.get("status") for sid, rec in stages.items()}
    if statuses.get("publish_gate") == APPROVED:
        return "published"
    if statuses.get("upload") == COMPLETED:
        return "staged"
    if FAILED in statuses.values():
        return "failed"
    # 审核差异是产出，不是卡点（AGENTS.md 规则 6）：一审/二审执行完成、
    # 差异待人工判断且未放行时，本地流程已经完成，应显示“待确认/可上传”，
    # 不能一直显示“审核中”。人工上传仍由用户确认。
    gate = stages.get("review_gate") or {}
    if (gate.get("status") == NEEDS_REVIEW and gate.get("execution_completed") is True
            and gate.get("continue_unqualified") is True and not gate.get("release_ready")
            and statuses.get("validate") == COMPLETED
            and statuses.get("format_gate") == COMPLETED):
        return "pending_confirmation"
    if projection(manifest, stage_ids=LOCAL_STAGES)["execution_completed"]:
        return "pending_confirmation"
    if BLOCKED in statuses.values():
        return "blocked"
    if statuses.get("review_gate") in TERMINAL_OK:
        return "validating"
    if statuses.get("validate") in TERMINAL_OK:
        return "reviewing"
    if statuses.get("assemble") in TERMINAL_OK:
        return "assembling" if statuses.get("meta") not in TERMINAL_OK else "validating"
    if statuses.get("preassemble") in TERMINAL_OK or statuses.get("types") in TERMINAL_OK:
        return "enriching"
    if statuses.get("structure_gate") in TERMINAL_OK:
        return "classifying"
    if statuses.get("extract") in TERMINAL_OK:
        return "structure_review"
    return "created"
