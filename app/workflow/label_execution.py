"""Bounded answers/explanations calls; retry only independently failed units.

Only private verified checkpoints are written here. The ordinary whole-stage
importer remains responsible for canonical artifacts and delivery.
"""
import copy
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import agent_tasks, local_agent, unit_tasks

STAGES = ("answers", "explanations")


def check_inputs(batch, stage, expected):
    """Detect both changed bytes and newly appeared optional source evidence."""
    _, hashes, missing, _ = agent_tasks._collect(batch, stage)
    if missing or hashes != expected:
        raise local_agent.AgentError("Label source inventory changed during execution", "context_changed")


def _unit_payload(stage, payload, unit):
    if stage == "answers":
        numbers = {str(n) for n in unit["numbers"]}
        return {key: {n: value for n, value in mapping.items() if n in numbers}
                for key, mapping in payload.items()}
    matches = [r for r in payload["items"] if r.get("unit_id") == unit["unit_id"]]
    return {"items": copy.deepcopy(matches)}


def _envelope(stage, response, units):
    """Reject unknown/duplicate output before salvaging any good unit."""
    agent_tasks._fields(response, ("status", "artifacts", "notes", "findings"),
                        ("status", "artifacts", "notes", "findings"))
    agent_tasks._strings(response["notes"], "notes")
    agent_tasks._findings(response["findings"], units)
    if response["status"] not in ("completed", "needs_review"):
        raise ValueError("Invalid partition status")
    if response["status"] == "needs_review" and not any(
            f["severity"] in ("blocker", "major") for f in response["findings"]):
        raise ValueError("Incomplete partition needs localized findings before salvaging units")
    path = agent_tasks.ARTIFACTS[stage]
    if not isinstance(response["artifacts"], dict) or set(response["artifacts"]) != {path}:
        raise ValueError("Partition may return only its stage artifact")
    payload = response["artifacts"][path]
    if stage == "answers":
        agent_tasks._fields(payload, ("answers", "evidence", "missing", "difficulty", "difficultyEvidence"),
                            ("answers", "evidence", "missing"))
        numbers = {str(n) for u in units for n in u["numbers"]}
        if any(not isinstance(m, dict) or not set(m) <= numbers for m in payload.values()):
            raise ValueError("Answer partition contains unknown numbers or invalid mappings")
    else:
        agent_tasks._fields(payload, ("items",), ("items",))
        rows = payload["items"]
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise ValueError("Explanation items must be objects")
        from .explanations_direct import DIRECT_COPY_EVIDENCE
        if any(DIRECT_COPY_EVIDENCE in (r.get("evidence") or []) for r in rows):
            raise ValueError("Model results cannot claim deterministic direct-copy evidence")
        ids = [r.get("unit_id") for r in rows]
        if any(not isinstance(uid, str) for uid in ids) or len(ids) != len(set(ids)) or not set(ids) <= {u["unit_id"] for u in units}:
            raise ValueError("Explanation partition has duplicate or foreign unit IDs")
    return payload


def run(batch, stage, config, run_dir, *, invoke=None, coordinated_imports=False):
    if stage not in STAGES:
        raise ValueError("Unsupported bounded labeling stage")
    batch, run_dir = Path(batch), Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    config = dict(config)
    config.setdefault("_part_model_binding", local_agent._part_model_binding(config))
    invoke = invoke or local_agent.invoke
    budget = int(config["max_input_chars"])
    feedback_limit = min(int(config.get("retry_feedback_chars", 1600)), 4000)
    # Reserve space for error feedback rather than truncating supplied evidence.
    prompt_budget = budget - feedback_limit - 200
    task, singles = unit_tasks.build_parts(batch, stage, prompt_budget, max_units=1)
    if task.get("preconditions") or not singles:
        raise ValueError("Label stage inputs are not ready")
    # Strict batch content for every in-flight check; model-identity files only
    # join the snapshot key so a plugin self-update never invalidates a batch.
    strict_hashes = dict(task["input_hashes"])
    binding_files = (config.get("_part_model_binding") or {}).get("files") or {}
    hashes = {**strict_hashes, **binding_files}
    context, current_hashes, missing, units = agent_tasks._collect(batch, stage)
    if missing or any(current_hashes.get(k) != v for k, v in task["input_hashes"].items() if k in current_hashes):
        raise ValueError("Label stage context changed during preparation")
    by_id = {u["unit_id"]: u for u in units}
    singleton = {p["unit_ids"][0]: p for p in singles}
    accepted, errors = {}, {}
    path = agent_tasks.ARTIFACTS[stage]

    def validate(uid, payload):
        candidate = copy.deepcopy(payload)
        scoped = dict(context)
        if stage == "explanations":
            typed = context[agent_tasks.ARTIFACTS["types"]]
            scoped[agent_tasks.ARTIFACTS["types"]] = dict(typed, items=[r for r in typed["items"] if r.get("unit_id") == uid])
        agent_tasks._validate(batch, stage, candidate, scoped, [by_id[uid]])
        return {"status": "completed", "artifacts": {path: candidate}, "notes": [], "findings": []}

    for uid, part in singleton.items():
        if uid in accepted:
            continue
        unit_tasks.check_part_inputs(batch, part)
        local_agent._check_input_hashes(batch, strict_hashes)
        saved = local_agent._load_part_snapshot(batch, stage, part, hashes, config)
        if saved is not None:
            try:
                payload = _envelope(stage, saved, [by_id[uid]])
                if (saved["status"] != "completed"
                        or any(f["severity"] in ("blocker", "major") for f in saved["findings"])):
                    raise ValueError("Checkpoint is not a completed unit")
                accepted[uid] = validate(uid, payload)
            except (ValueError, TypeError, KeyError):
                pass  # Corrupt/invalid checkpoints never bypass source validation.

    rounds = []
    concurrency = local_agent._part_concurrency(config)
    for attempt in range(int(config.get("retries", 0)) + 1):
        pending = set(singleton) - set(accepted)
        if not pending:
            break
        _, parts = unit_tasks.build_parts(batch, stage, prompt_budget,
                                         max_units=8 if attempt == 0 else 1, selected_ids=pending)
        def call(index_part):
            index, part = index_part
            local_agent._check_input_hashes(batch, strict_hashes)
            unit_tasks.check_part_inputs(batch, part)
            feedback = ""
            if attempt:
                detail = {uid: errors.get(uid, "Missing unit") for uid in part["unit_ids"]}
                feedback = "\nRepair ONLY these failed units; validated units are retained by the importer.\n" + json.dumps(detail, ensure_ascii=False)[:feedback_limit]
            directory = run_dir / ("round-%02d-part-%04d" % (attempt, index))
            try:
                options = {"permit_parallel": True}
                if coordinated_imports:
                    options["coordinated_imports"] = True
                response = invoke(part["prompt"] + feedback, directory, config, **options)["response"]
            except local_agent.AgentError as exc:
                if exc.code not in ("response", "integration"):
                    raise  # Timeouts/rate limits are not content repair attempts.
                return part, None, str(exc)
            local_agent._check_input_hashes(batch, strict_hashes)
            unit_tasks.check_part_inputs(batch, part)
            return part, response, None
        fatal = None
        with ThreadPoolExecutor(max_workers=min(concurrency, len(parts))) as pool:
            futures = {pool.submit(call, item): item[1] for item in enumerate(parts)}
            for future in as_completed(futures):
                try:
                    part, response, call_error = future.result()
                except (RuntimeError, OSError) as exc:
                    fatal = fatal or exc
                    for uid in futures[future]["unit_ids"]:
                        errors[uid] = str(exc)
                    continue
                ids = part["unit_ids"]
                rounds.append({"attempt": attempt + 1, "unit_ids": ids, "prompt_chars": len(part["prompt"])})
                try:
                    if call_error:
                        raise ValueError(call_error)
                    payload = _envelope(stage, response, [by_id[uid] for uid in ids])
                except (ValueError, TypeError, KeyError) as exc:
                    for uid in ids:
                        errors[uid] = str(exc)
                    continue
                for uid in ids:
                    problems = [f for f in response["findings"] if f["unit_id"] == uid]
                    blocked = [f for f in problems if f["severity"] in ("blocker", "major")]
                    if blocked:
                        errors[uid] = json.dumps(blocked, ensure_ascii=False)
                        continue
                    try:
                        candidate = validate(uid, _unit_payload(stage, payload, by_id[uid]))
                        candidate["findings"] = problems
                        candidate["notes"] = list(response["notes"])
                    except (ValueError, TypeError, KeyError) as exc:
                        errors[uid] = str(exc)
                        continue
                    accepted[uid] = candidate
                    local_agent._store_part_snapshot(batch, stage, singleton[uid], hashes, config, candidate)
        local_agent._json_write(run_dir / ("repair-progress-%02d.json" % attempt), {
            "stage": stage, "validated_units": sorted(accepted),
            "failed_units": {uid: errors.get(uid) for uid in singleton if uid not in accepted}, "calls": rounds})
        if fatal is not None:
            raise fatal
    pending = set(singleton) - set(accepted)
    if pending:
        local_agent._check_input_hashes(batch, strict_hashes)
        unresolved = {"status": "needs_review", "artifacts": {}, "findings": [],
                      "notes": ["Label repair exhausted for units: " + json.dumps(
                          {uid: errors.get(uid) for uid in sorted(pending)}, ensure_ascii=False)]}
        local_agent._json_write(run_dir / "response.json", unresolved)
        return {"response": unresolved, "input_hashes": strict_hashes, "collected_hashes": current_hashes,
                "artifacts": {"response": str(run_dir / "response.json")}}
    local_agent._check_input_hashes(batch, strict_hashes)
    check_inputs(batch, stage, current_hashes)
    for part in singles:
        unit_tasks.check_part_inputs(batch, part)
    merged = unit_tasks.merge(stage, singles, [accepted[p["unit_ids"][0]] for p in singles])
    from . import scoped_regeneration
    complete = scoped_regeneration.merge(batch, stage, merged['artifacts'][path], context, units)
    agent_tasks._validate(batch, stage, complete, context, units)
    local_agent._json_write(run_dir / "response.json", merged)
    return {"response": merged, "input_hashes": strict_hashes, "collected_hashes": current_hashes,
            "artifacts": {"response": str(run_dir / "response.json")}}
