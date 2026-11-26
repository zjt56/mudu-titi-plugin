# -*- coding: utf-8 -*-
"""六维批次健康统计口径（以 manifest + 校验/格式/审核报告为当前事实，operations 日志为诊断历史）。

区分 6 项独立维度，避免把“流程继续”“有审核差异”与“根因已修复”混为一谈：
1. hard_validation_passed：validate 与 format_gate 实际报告均通过；
2. review_execution_completed：review 是否真实执行完（含仅留审核差异的 needs_review + execution_completed）；
3. quality_released：真实审核且无未裁决差异（release_ready=True）；
4. first_pass_no_blocker：首次执行未触发运维 incident / blocker；
5. self_heal_verified：曾触发 incident 且经真实修复后复验通过（非 pipeline_progress）；
6. pipeline_progress_only：incident 仅由流水线自行继续收口（resolution_source=pipeline_progress），不计为根因修复。
"""
from __future__ import annotations

import json
from pathlib import Path
import sys


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def evaluate_batch(batch_dir) -> dict:
    batch = Path(batch_dir).resolve()
    manifest = _read_json(batch / "workflow/manifest.json")
    stages = manifest.get("stages") or {}
    val_report = _read_json(batch / "07_review/validation-report.json")
    fmt_report = _read_json(batch / "07_review/upload-format.json")
    ops = _read_json(batch / "workflow/operations.json")

    val_ok = bool(val_report.get("passed") is True and (stages.get("validate") or {}).get("status") == "completed")
    fmt_ok = bool(fmt_report.get("passed") is True and (stages.get("format_gate") or {}).get("status") == "completed")
    hard_validation_passed = val_ok and fmt_ok

    rg = stages.get("review_gate") or {}
    rg_status = str(rg.get("status") or "")
    review_execution_completed = bool(
        rg_status == "completed"
        or (rg_status == "needs_review" and rg.get("execution_completed") is True)
    )
    quality_released = bool(rg_status == "completed" and rg.get("release_ready") is True)

    incident = ops.get("incident") if isinstance(ops.get("incident"), dict) else None
    receipts = ops.get("receipts") if isinstance(ops.get("receipts"), list) else []
    had_blocker = bool(incident is not None or receipts)
    first_pass_no_blocker = not had_blocker

    resolution_source = str((incident or {}).get("resolution_source") or "")
    inc_status = str((incident or {}).get("status") or "")
    pipeline_progress_only = bool(
        had_blocker
        and inc_status == "resolved"
        and resolution_source == "pipeline_progress"
    )
    self_heal_verified = bool(
        had_blocker
        and inc_status == "resolved"
        and resolution_source not in ("", "pipeline_progress", "quality_differences")
    )

    return {
        "batch_name": manifest.get("batch_name") or batch.name,
        "batch_dir": str(batch),
        "manifest_status": manifest.get("status") or "",
        "review_gate_status": rg_status,
        "hard_validation_passed": hard_validation_passed,
        "review_execution_completed": review_execution_completed,
        "quality_released": quality_released,
        "first_pass_no_blocker": first_pass_no_blocker,
        "self_heal_verified": self_heal_verified,
        "pipeline_progress_only": pipeline_progress_only,
        "incident_status": inc_status,
        "resolution_source": resolution_source,
    }


def summarize_batches(batch_dirs) -> dict:
    rows = [evaluate_batch(d) for d in batch_dirs]
    total = len(rows)

    def _rate(key: str) -> dict:
        count = sum(1 for r in rows if r.get(key))
        return {"count": count, "total": total, "rate": round(count / total, 4) if total else 0.0}

    return {
        "total_batches": total,
        "metrics": {
            "hard_validation_passed": _rate("hard_validation_passed"),
            "review_execution_completed": _rate("review_execution_completed"),
            "quality_released": _rate("quality_released"),
            "first_pass_no_blocker": _rate("first_pass_no_blocker"),
            "self_heal_verified": _rate("self_heal_verified"),
            "pipeline_progress_only": _rate("pipeline_progress_only"),
        },
        "batches": rows,
    }


def main(argv=None) -> int:
    dirs = list(sys.argv[1:] if argv is None else argv)
    if not dirs:
        print("Usage: python3 -m workflow.batch_metrics <batch_dir> [...]", file=sys.stderr)
        return 2
    print(json.dumps(summarize_batches(dirs), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
