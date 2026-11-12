"""Apply the documented original-number ranges to existing local batches.

This is a local, read-only-verified migration of derived metadata. It never
changes question content, source evidence, manifests, frozen upload bytes or
remote state. Every write is backed up and re-projected before it is kept.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

from . import upload_numbering

VERSION = "upload-workflow.numbering-migration/v1"
ARTIFACTS = ("06_batch/batch.json", "06_batch/batch_meta.base.json", "06_batch/batch_meta.json")
REPORT = "workflow/numbering-migration.json"
BACKUP_DIR = "workflow/numbering-migration"
RANGE_KEYS = ("numberStart", "numberEnd")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_atomic(path: Path, data) -> bytes:
    raw = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    temporary = path.with_name("." + path.name + ".numbering-migration")
    temporary.write_bytes(raw)
    os.replace(temporary, path)
    return raw


def _without_ranges(value):
    """Copy a batch with only top-level item ranges removed for comparison."""
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        return value
    items = []
    for row in value["items"]:
        items.append({k: deepcopy(v) for k, v in row.items() if k not in RANGE_KEYS}
                     if isinstance(row, dict) else row)
    return {**{k: deepcopy(v) for k, v in value.items() if k != "items"}, "items": items}


def _only_ranges_changed(before, after) -> bool:
    return _without_ranges(before) == _without_ranges(after)


def _range_summary(items):
    printed = unnumbered = 0
    for row in items:
        if not isinstance(row, dict):
            continue
        if row.get("numberStart") is None and row.get("numberEnd") is None:
            unnumbered += 1
        else:
            printed += 1
    return {"printed": printed, "unnumbered": unnumbered}


def _approved_final(batch: Path) -> bool:
    """An approved upload binds the exact final bytes; never rewrite them silently."""
    manifest_path = batch / "workflow/manifest.json"
    if not manifest_path.is_file():
        return False
    try:
        stages = (_load(manifest_path).get("stages") or {})
    except (OSError, ValueError):
        return False
    gate = stages.get("upload_gate") or {}
    uploaded = (stages.get("upload") or {}).get("status") in ("completed", "approved")
    return bool(gate.get("approved_batch_sha256") and uploaded)


def _backup(batch: Path, files, stamp: str):
    root = batch / BACKUP_DIR / f"backup-{stamp}"
    root.mkdir(parents=True, exist_ok=False)
    records = {}
    for relative in files:
        source = batch / relative
        raw = source.read_bytes()
        target = root / Path(relative).name
        target.write_bytes(raw)
        records[relative] = {"backup": str(target.relative_to(batch)), "sha256": _sha(raw),
                             "bytes": len(raw)}
    (root / "backup-manifest.json").write_text(
        json.dumps({"version": VERSION, "batch": batch.name, "created_at": stamp,
                    "files": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return root, records


def plan_batch(batch_dir, *, include_approved_final: bool = False) -> dict:
    """Compute the projection for one batch without writing anything."""
    batch = Path(batch_dir).resolve()
    result = {"batch": batch.name, "batch_dir": str(batch), "status": "planned",
              "reason": "", "artifacts": [], "diagnostics": [], "evidence": {}}
    if not (batch / "workflow/manifest.json").is_file():
        result.update(status="skipped", reason="不是工作流批次（缺少 workflow/manifest.json）")
        return result
    existing = [relative for relative in ARTIFACTS if (batch / relative).is_file()]
    if not existing:
        result.update(status="skipped", reason="没有可迁移的 06_batch 产物")
        return result
    approved = _approved_final(batch)
    projected = {}
    all_errors, files, units, diagnostics = [], {}, [], []
    for relative in existing:
        path = batch / relative
        try:
            original = _load(path)
        except (OSError, ValueError) as exc:
            all_errors.append({"code": "NUMBERING_MIGRATION_READ_FAILED",
                               "message": str(exc), "path": relative})
            continue
        if relative == "06_batch/batch_meta.json" and approved and not include_approved_final:
            result["artifacts"].append({"path": relative, "changed": False,
                                        "reason": "上传批准绑定的最终字节，未改写；下次准备上传时仍按同源投影补范围"})
            continue
        report = upload_numbering.project_batch(original, batch)
        all_errors.extend(report["errors"])
        files.update(report["evidence"].get("files") or {})
        units.extend(report["evidence"].get("units") or [])
        diagnostics.extend(report["evidence"].get("diagnostics") or [])
        migrated = {**original, "items": report["items"]}
        changed = migrated != original
        if changed and not _only_ranges_changed(original, migrated):
            all_errors.append({"code": "NUMBERING_MIGRATION_UNEXPECTED_CHANGE",
                               "message": "迁移改动了题号范围以外的字段", "path": relative})
            continue
        projected[relative] = migrated
        result["artifacts"].append({"path": relative, "changed": changed,
                                    "ranges": _range_summary(migrated["items"]),
                                    "before_sha256": _sha(path.read_bytes())})
    result["diagnostics"] = diagnostics
    result["evidence"] = {"version": upload_numbering.VERSION,
                          "files": dict(sorted(files.items())), "units": units}
    if all_errors:
        result.update(status="blocked", errors=all_errors,
                      reason="题号来源证据不完整或冲突，未写入任何文件")
        return result
    result["projected"] = projected
    result["approved_final_frozen"] = approved and not include_approved_final
    return result


def apply_batch(batch_dir, *, include_approved_final: bool = False, stamp: str | None = None) -> dict:
    """Verify, back up and atomically write one batch's projected artifacts."""
    from . import supervisor
    batch = Path(batch_dir).resolve()
    plan = plan_batch(batch, include_approved_final=include_approved_final)
    if plan.get("status") == "skipped":
        return plan
    if plan.get("status") == "blocked":
        return plan
    projected = plan.pop("projected", {})
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    changed_files = [relative for relative, data in projected.items()
                     if (batch / relative).read_bytes() !=
                     (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")]
    # Acquire the same per-batch lock the auto scheduler uses. Never wait behind a
    # live stage: leave that batch for a later explicit run.
    with supervisor._try_file_lock(supervisor._auto_lock_path(batch)) as acquired:
        if not acquired:
            plan.update(status="skipped", reason="该批次正在被自动流程处理，未写入")
            return plan
        if (batch / "workflow/.delete-requested").exists():
            plan.update(status="skipped", reason="该批次已请求删除，未写入")
            return plan
        from . import operations
        if operations.lease_active(batch):
            plan.update(status="skipped", reason="Harness 运维修复租约生效中，未写入")
            return plan
        if not changed_files:
            plan.update(status="unchanged", reason="题号范围已是当前口径")
            return plan
        backup_root, records = _backup(batch, changed_files, stamp)
        written = []
        try:
            for relative in changed_files:
                raw = _write_atomic(batch / relative, projected[relative])
                written.append({"path": relative, "sha256": _sha(raw), "bytes": len(raw),
                                "ranges": _range_summary(projected[relative]["items"])})
        except Exception:
            for relative in changed_files:
                backup = backup_root / Path(relative).name
                if backup.is_file():
                    os.replace(backup, batch / relative)
            raise
    plan.update(status="applied", backup=str(backup_root.relative_to(batch)),
                backup_files=records, written=written)
    # Re-read and re-project from disk; never trust the in-memory candidate.
    verification = []
    for relative in changed_files:
        again = upload_numbering.project_batch(_load(batch / relative), batch)
        rows = again["items"]
        verification.append({"path": relative, "errors": again["errors"],
                             "ranges": _range_summary(rows),
                             "ranges_present": all(
                                 isinstance(row, dict) and "numberStart" in row and "numberEnd" in row
                                 for row in rows)})
    plan["verification"] = verification
    final = batch / "06_batch/batch_meta.json"
    if final.is_file() and not plan.get("approved_final_frozen"):
        built = upload_numbering.project_batch(_load(final), batch)
        plan["payload_check"] = {"passed": not built["errors"],
                                 "ranges": _range_summary(built["items"]),
                                 "errors": built["errors"][:8]}
    _write_report(batch, plan)
    return plan


def _write_report(batch: Path, plan: dict) -> None:
    target = batch / REPORT
    target.parent.mkdir(parents=True, exist_ok=True)
    report = {key: value for key, value in plan.items() if key != "projected"}
    report.update(version=VERSION, applied_at=datetime.now(timezone.utc).isoformat())
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def find_batches(root) -> list[Path]:
    root = Path(root)
    return sorted({path.parent.parent.resolve() for path in root.rglob("workflow/manifest.json")})


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="workflows", help="批次根目录（默认 workflows）")
    parser.add_argument("--batch-dir", action="append", default=[], help="只处理指定批次，可重复")
    parser.add_argument("--apply", action="store_true", help="实际写入；默认只预演")
    parser.add_argument("--include-approved-final", action="store_true",
                        help="连已批准上传的最终 batch_meta.json 一并改写（会使上传批准失效）")
    parser.add_argument("--report", default="", help="额外写出汇总报告的路径")
    args = parser.parse_args(argv)
    batches = [Path(item) for item in args.batch_dir] or find_batches(args.root)
    results, failed = [], 0
    for batch in batches:
        try:
            result = (apply_batch(batch, include_approved_final=args.include_approved_final)
                      if args.apply else plan_batch(batch, include_approved_final=args.include_approved_final))
        except Exception as exc:  # keep one broken batch from hiding the rest
            result = {"batch": Path(batch).name, "batch_dir": str(batch), "status": "error",
                      "reason": f"{type(exc).__name__}: {exc}"}
        results.append(result)
        failed += result.get("status") in {"blocked", "error"}
        print(json.dumps({key: result.get(key) for key in
                          ("batch", "status", "reason", "backup", "approved_final_frozen")},
                         ensure_ascii=False))
    summary = {"version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
               "apply": bool(args.apply), "batches": len(results),
               "applied": sum(item.get("status") == "applied" for item in results),
               "unchanged": sum(item.get("status") == "unchanged" for item in results),
               "skipped": sum(item.get("status") == "skipped" for item in results),
               "blocked": sum(item.get("status") == "blocked" for item in results),
               "error": sum(item.get("status") == "error" for item in results),
               "results": results}
    if args.report:
        target = Path(args.report)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, target)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
