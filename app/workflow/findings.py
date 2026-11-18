"""One read-only defect vocabulary for checks, handoffs and existing recovery.

Reports remain the evidence of record. This module does not maintain a second
repair queue, infer question identities, mutate batches or resolve review findings.
"""
from __future__ import annotations

import hashlib
import json


REVIEW_CODES = frozenset({"REVIEW_ROUND_FINDING", "MANUAL_REVIEW_OPEN", "REVIEW_FINDING_OPEN"})
DEFERRED_SOURCE_CONTENT = frozenset({'SOURCE_STEM_MISSING', 'OPTIONS_MISMATCH',
                                     'OPTION_CONTENT_MISMATCH'})


def split_early_source(issues):
    """Defer only scoped content comparisons; unknown identity stays blocking."""
    hard, soft = source_partition(issues)
    deferred = [row for row in hard if row.get('code') in DEFERRED_SOURCE_CONTENT
                and row.get('unit_id')]
    return [row for row in hard if row not in deferred], deferred, soft


def source_partition(issues):
    """Only explicit capability limitations are informational.

    Option mismatch codes describe real comparisons and must not be exempted
    just because an option might contain an image. Unknown evidence stays strict.
    """
    blocking, informational = [], []
    for issue in issues:
        limited = (issue.get("code") == "SOURCE_IMAGE_MISSING"
                   or issue.get("failure_class") == "capability_limited"
                   and issue.get("comparison_performed") is False)
        (informational if limited else blocking).append(issue)
    return blocking, informational


def normalize(value, *, report="", stage="", blocking=True):
    row = dict(value) if isinstance(value, dict) else {"message": str(value)}
    row.setdefault("report", report)
    row.setdefault("stage", stage)
    row.setdefault("code", "CHECK_FAILED")
    row.setdefault("message", row.get("reason") or row.get("error") or row.get("output") or "检查未通过")
    row.setdefault("blocking", blocking)
    if row["code"] in REVIEW_CODES:
        row["blocking"] = False
        row["disposition"] = "retain_review_difference"
    else:
        row.setdefault("disposition", "repair" if row["blocking"] else "observe")
    row.setdefault("severity", "error" if row["blocking"] else "warning")
    # No timestamps, key order or report traversal order in a defect identity.
    identity = {key: row.get(key) for key in
                ("report", "code", "unit_id", "question_id", "number", "field", "check", "script", "message")}
    row["finding_id"] = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
                                                  default=str).encode("utf-8")).hexdigest()[:24]
    return row


def collect(report, *, path="", stage=""):
    """Collect complete evidence; display/context budgets belong to the caller."""
    rows = []
    rows.extend(report.get("findings") or [])
    rows.extend(report.get("errors") or [])
    rows.extend(report.get("blockers") or [])
    for value in report.get("warnings") or []:
        # Warnings remain visible in the same evidence stream but never acquire
        # repair authority simply because they were collected with hard errors.
        row = dict(value) if isinstance(value, dict) else {"message": str(value)}
        row["blocking"] = False
        rows.append(row)
    audit = report.get("source_audit") or {}
    if stage == 'validate':
        hard, deferred, soft = split_early_source(audit.get("issues") or [])
        rows.extend(dict(row, blocking=False, disposition='defer_to_format_gate',
                         deferred_to='format_gate') for row in deferred)
    else:
        hard, soft = source_partition(audit.get("issues") or [])
    rows.extend(dict(row, blocking=True) for row in hard)
    rows.extend(dict(row, blocking=False) for row in soft)
    for check in report.get("checks") or []:
        if not isinstance(check, dict):
            continue
        evidence = check.get("findings")
        if evidence:
            rows.extend(evidence)
        elif check.get("passed") is False:
            rows.append({"check": check.get("script") or check.get("check"),
                         "blocking": check.get("blocking", True),
                         "message": check.get("error") or check.get("output") or "检查未通过"})
    result, seen = [], set()
    for value in rows:
        row = normalize(value, report=path, stage=stage)
        if row["finding_id"] not in seen:
            result.append(row)
            seen.add(row["finding_id"])
    return result


def attach(report, *, path="", stage=""):
    # Rebuilding is idempotent, including when a report already carries findings.
    report["findings"] = collect(report, path=path, stage=stage)
    report["finding_summary"] = {
        "total": len(report["findings"]),
        "blocking": sum(bool(row["blocking"]) for row in report["findings"]),
        "unit_ids": sorted({str(row["unit_id"]) for row in report["findings"] if row.get("unit_id")}),
        "unscoped": sum(not row.get("unit_id") for row in report["findings"]),
    }
    return report
