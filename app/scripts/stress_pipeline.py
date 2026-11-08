#!/usr/bin/env python3
"""Isolated fault campaign; simulation outcomes are distinct from live AI results."""
from __future__ import annotations
import argparse
import contextlib
import io
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow import stress_lab

DEFECTS = ("none", "empty_paragraph", "listening_stem", "stale_format_report",
           "nonrepairable_prose", "corrupt_report", "missing_final", "invalid_label",
           "missing_option", "duplicate_json_key", "blank_structure", "two_defects")
REPAIRABLE = {"empty_paragraph", "listening_stem", "two_defects", "missing_option"}
REBUILDABLE = {"none", "stale_format_report", "corrupt_report", "missing_final", "duplicate_json_key"}


def evaluate(row):
    kind, style = row["actual_defect"], row["style"]
    failures = []
    if row["error"]:
        failures.append("runtime_error")
    if kind != row["defect"]:
        failures.append("requested_fault_not_exercised")
    if not row["data_preserved"]:
        failures.append("unexpected_content_change")
    if not row["quality_preserved"]:
        failures.append("quality_boundary_changed")
    if row["fake_pass"]:
        failures.append("false_completion")
    if style == "honest":
        if kind in (REPAIRABLE | REBUILDABLE) - {"missing_option"} and not (row["format_passed"] and row["local_complete"]):
            failures.append("recovery_incomplete")
        if kind == "missing_option" and not (row["format_passed"] and row["review_invalidated"]):
            failures.append("option_repair_did_not_require_fresh_review")
        if kind in REPAIRABLE and not (row["repaired"] and row["verified"]):
            failures.append("repair_not_verified")
        if kind == "blank_structure" and not row["structure_restored"]:
            failures.append("structure_not_restored")
        if kind == "invalid_label" and not row["knowledge_requeued"]:
            failures.append("knowledge_work_not_requeued")
    if kind in {"invalid_label", "nonrepairable_prose"} and row["local_complete"]:
        failures.append("unresolved_content_claimed_complete")
    if kind == "blank_structure" and not row["structure_restored"] and row["local_complete"]:
        failures.append("empty_structure_claimed_complete")
    if kind in {"empty_paragraph", "listening_stem", "two_defects"} and row["review_reissued"]:
        failures.append("format_only_repair_reissued_review")
    if failures:
        outcome = "failed"
    elif row["repaired"] and row["verified"]:
        outcome = ("verified_option_repair_review_pending"
                   if kind == "missing_option" else "verified_repair")
    elif row["local_complete"]:
        outcome = "checked_or_rebuilt"
    elif kind == "blank_structure" and row["structure_restored"]:
        outcome = "structure_restored_downstream_pending"
    elif kind == "invalid_label" and row.get("knowledge_requeued"):
        outcome = "knowledge_requeued_pending_model"
    else:
        outcome = "safely_contained_unresolved"
    return outcome, failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=60)
    parser.add_argument("--seed-start", type=int, default=401)
    parser.add_argument("--adversarial-judge", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("runs must be positive")
    root = Path(tempfile.mkdtemp(prefix="stress-v2-", dir=Path(tempfile.gettempdir()).resolve())).resolve()
    os.environ["TMPDIR"] = str(root)
    os.environ["WF_STRUCTURE_AI"] = "0"
    tempfile.tempdir = str(root)
    started, rows = time.perf_counter(), []
    for i in range(args.runs):
        defect = DEFECTS[i % len(DEFECTS)]
        style = ("retry_only", "record_only")[i % 2] if args.adversarial_judge else "honest"
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            result = stress_lab.run_scenario(root / str(i), args.seed_start + i, defect, style, max_ticks=6)
        row = result.as_dict()
        row["wall_ms"] = result.total_ms
        row["outcome"], row["problems"] = evaluate(row)
        rows.append(row)
        (root / f"{i}.log").write_text(log.getvalue(), encoding="utf-8")
        print(f'{i+1}/{args.runs}: {defect} -> {row["outcome"]} {row["problems"]}', flush=True)
    times = [row["wall_ms"] for row in rows]
    summary = {"runs": len(rows), "judge": "deterministic_stub", "real_model_calls": 0,
               "wall_seconds": round(time.perf_counter()-started, 2),
               "mean_ms": round(statistics.mean(times), 1), "median_ms": statistics.median(times),
               "p95_ms": sorted(times)[max(0, int(len(times)*.95)-1)],
               "failed_runs": [r for r in rows if r["problems"]],
               "outcomes": {k: sum(r["outcome"] == k for r in rows) for k in sorted({r["outcome"] for r in rows})},
               "fixture_root": str(root), "scope": "generated text, parser, fill, gates, recovery and scheduler; no OCR/ASR or semantic model benchmark"}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return bool(summary["failed_runs"])

if __name__ == "__main__":
    raise SystemExit(main())
