"""Revalidate and re-accept an existing stage artifact without a model call.

This is not a quality pass, review, upload or release. It exists for one narrow
case: a parser/presentation change rewrote the rendered batch object while the
authoritative Word source and every raw evidence input stayed byte-identical.
In that case re-deriving the same labels costs many model calls for no gain.

The command refuses unless all of these hold:

* the canonical artifact file still matches the delivery hash recorded for it;
* the per-unit source bindings (Word fragment hash and media hashes) recorded by
  the managed batch are identical to the current source build;
* every recorded raw input is unchanged; only rendered ``06_batch/`` objects may
  differ, because those are presentation, not label content;
* the artifact passes the stage's ordinary validation against current inputs.

Every condition is deterministic and read-only; only the final accepted delivery
is written, through the normal manifest recording path.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from . import agent_tasks, batch_fill, contracts, manifest as mf, question_draft
from .local_agent import _agent_lock

FILL_STAGES = ("audio", "types", "explanations", "knowledge")
PRESENTATION_PREFIX = "artifact:06_batch/"


def _presentation_only(saved, current):
    """Return changed keys that are not rendered batch presentation."""
    changed = []
    for key in sorted(set(saved) | set(current)):
        if key.startswith(PRESENTATION_PREFIX):
            continue
        if saved.get(key) != current.get(key):
            changed.append(key)
    return changed


def _binding_mismatches(batch):
    """Prove the managed fill was built from today's Word source, not an older one."""
    state, _ = batch_fill._managed(batch)
    if not state:
        return []
    draft = question_draft.build(batch)
    problems = []
    units = state.get("units") or {}
    if set(units) != {row["unit_id"] for row in draft["items"]}:
        problems.append("unit-set")
    for row in draft["items"]:
        uid = row["unit_id"]
        old = units.get(uid)
        if not old:
            problems.append(uid + ":missing")
            continue
        current = draft["source_bindings"].get(uid) or {}
        saved = old.get("binding") or {}
        if saved.get("source_text_sha256") != current.get("source_text_sha256"):
            problems.append(uid + ":source")
        if saved.get("media_hashes", {}) != current.get("media_hashes", {}):
            problems.append(uid + ":media")
    return problems


def _validate_artifact(batch, stage, artifact_path):
    payload = agent_tasks._json(batch / artifact_path)
    if payload is None:
        raise ValueError("产物不存在：" + artifact_path)
    context, hashes, missing, units = agent_tasks._collect(batch, stage)
    if missing:
        raise ValueError("阶段前置条件不满足：" + "; ".join(missing))
    notes = []
    agent_tasks._validate(batch, stage, copy.deepcopy(payload), context, units, notes=notes)
    return payload, hashes


def redeliver(batch_dir, stage_id) -> dict:
    batch = Path(batch_dir).expanduser().resolve()
    if stage_id not in FILL_STAGES:
        raise ValueError("只支持复用填充阶段产物：" + "、".join(FILL_STAGES))
    manifest = mf.load(batch)
    record = manifest["stages"].get(stage_id) or {}
    status = record.get("status")
    if status in mf.TERMINAL_OK:
        return {"stage": stage_id, "status": status, "action": "unchanged",
                "reason": "阶段已为终态，无需复用"}
    stage = contracts.STAGE_BY_ID[stage_id]
    artifact_path = agent_tasks.ARTIFACTS[stage_id]
    if not (batch / artifact_path).is_file():
        raise ValueError("产物文件不存在：" + artifact_path)
    saved_outputs = record.get("output_hashes") or {}
    saved_inputs = record.get("input_hashes") or {}
    if not saved_outputs or not saved_inputs:
        raise ValueError("缺少既有交付指纹，不能证明可复用")
    current_outputs = mf.output_hashes(batch, stage)
    for path, digest in saved_outputs.items():
        if current_outputs.get(path) != digest:
            raise ValueError("产物在交付后被改动：" + path)
    current_inputs = mf.input_hashes(batch, manifest, stage)
    changed = _presentation_only(saved_inputs, current_inputs)
    if changed:
        raise ValueError("非表现层输入已变化，必须真正重跑：" + "、".join(changed[:8]))
    problems = _binding_mismatches(batch)
    if problems:
        raise ValueError("题目来源绑定与原填充不一致，必须真正重跑：" + "、".join(problems[:8]))
    payload, _ = _validate_artifact(batch, stage_id, artifact_path)
    with _agent_lock(30):
        current = mf.load(batch)
        latest = current["stages"].get(stage_id) or {}
        if latest.get("status") in mf.TERMINAL_OK:
            return {"stage": stage_id, "status": latest.get("status"), "action": "unchanged",
                    "reason": "另一执行器已登记终态"}
        if (latest.get("input_hashes") or {}) != saved_inputs:
            raise ValueError("复用期间阶段输入发生变化，放弃复用")
        mf.touch_stage(current, stage_id, mf.COMPLETED,
                       notes="复用同一原文绑定的既有产物；已按当前输入重新校验，未调用模型")
        mf.record_delivery(batch, current, stage)
        mf.save(batch, current)
    ledger = batch / "workflow/redeliver-log.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "stage": stage_id, "artifact": artifact_path,
            "saved_inputs": saved_inputs, "saved_outputs": saved_outputs,
            "note": "presentation-only change; raw inputs byte-identical; revalidated",
        }, ensure_ascii=False, sort_keys=True) + "\n")
    return {"stage": stage_id, "status": "completed", "action": "redelivered",
            "artifact": artifact_path,
            "reason": "非表现层输入与题目来源绑定均未变化，产物通过当前校验"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-dir", type=Path, required=True)
    parser.add_argument("--stage", action="append", required=True,
                        help="填充阶段，可重复：audio/types/explanations/knowledge")
    parser.add_argument("--dry-run", action="store_true", help="只校验，不写台账")
    args = parser.parse_args(argv)
    results = []
    for stage_id in args.stage:
        try:
            if args.dry_run:
                batch = args.batch_dir.expanduser().resolve()
                manifest = mf.load(batch)
                stage = contracts.STAGE_BY_ID[stage_id]
                current_inputs = mf.input_hashes(batch, manifest, stage)
                record = manifest["stages"].get(stage_id) or {}
                changed = _presentation_only(record.get("input_hashes") or {}, current_inputs)
                problems = _binding_mismatches(batch) if stage_id in FILL_STAGES else []
                _validate_artifact(batch, stage_id, agent_tasks.ARTIFACTS[stage_id])
                results.append({"stage": stage_id, "action": "would-redeliver",
                                "changed_inputs": changed, "binding_problems": problems})
            else:
                results.append(redeliver(args.batch_dir, stage_id))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            results.append({"stage": stage_id, "action": "refused", "reason": str(exc)[:500]})
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    return 0 if all(item["action"] != "refused" for item in results) else 3


if __name__ == "__main__":
    raise SystemExit(main())
