# -*- coding: utf-8 -*-
"""把多代理工作流的结果登记进批次清单。

解决的问题：workflow 工具跑完只留下 candidates/ 和一份返回值，
manifest.json 里的阶段仍是 pending，闸门和状态显示都看不到这次运行。

ingest 做三件事：
1. 扫描 candidates/，记录哪些单元已有标注候选；
2. 把工作流返回的 findings / summary 落成 07_review/ 的三份审核文件；
3. 对已经有正式产物的阶段（types / explanations / knowledge / review）写入 completed 与哈希。

它不生成正式批次，也不替人做判断：findings 一律以 needs_review 落盘，
人工队列一律以 open 落盘，必须有人处理才可能通过 review-gate。
"""
from __future__ import annotations

import json
from pathlib import Path

from . import contracts, manifest as mf

REVIEW_DIR = "07_review"

# 归到标签审核的字段；其余算内容审核
METADATA_FIELDS = {"knowledge", "difficulty", "categories", "subtype", "type",
                   "source", "year", "location", "knowledgeids"}


def _norm_field(field: str) -> str:
    text = str(field or "").strip().lower()
    for sep in ("（", "(", "/", " "):
        if sep in text:
            text = text.split(sep)[0]
    return text.strip()


def classify_finding(finding: dict) -> str:
    return "metadata" if _norm_field(finding.get("field")) in METADATA_FIELDS else "content"


def scan_candidates(batch_dir: Path) -> list[dict]:
    folder = Path(batch_dir) / "candidates"
    if not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            out.append({"file": path.name, "error": "JSON 无法解析"})
            continue
        out.append({
            "file": path.name,
            "unit_id": str(data.get("unit_id") or path.stem),
            "number": str(data.get("number") or ""),
            "type": str(data.get("type") or ""),
            "answer": str(data.get("answer") or ""),
            "knowledge": data.get("knowledge") or [],
        })
    return out


def _blocking_keys(summary: dict | None) -> set:
    keys = set()
    if not isinstance(summary, dict):
        return keys
    for item in summary.get("blocking") or []:
        if isinstance(item, dict):
            keys.add((str(item.get("unit_id") or ""), str(item.get("field") or "")))
    return keys


def _key(record: dict) -> tuple:
    return (str(record.get("unit_id") or ""), str(record.get("field") or ""),
            str(record.get("reason") or ""))


def _current_label_row(batch_dir: Path, unit_id: str) -> dict | None:
    """Read the current formal row used by the review UI, if available."""
    for relative in ("06_batch/batch_meta.json", "06_batch/batch.json"):
        path = Path(batch_dir) / relative
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, UnicodeError):
            continue
        for row in payload.get("items", []) if isinstance(payload, dict) else []:
            if isinstance(row, dict) and str(row.get("unit_id") or row.get("id") or "") == str(unit_id):
                return row
    return None


def _stale_missing_label_finding(batch_dir: Path, finding: dict) -> bool:
    """Reject an old blind-review false positive after labels were filled.

    Blind review intentionally removes labels from its prompt. Older review
    executions sometimes reported that redaction as a real missing-label issue.
    Retire only that exact failure mode, and only when the current formal row
    now has both a type and a non-empty knowledge label.
    """
    field = _norm_field(finding.get("field"))
    reason = str(finding.get("reason") or "")
    if field not in {"labels", "label", "knowledge"}:
        return False
    if "草稿" not in reason or ("为空" not in reason and "未完成标注" not in reason):
        return False
    row = _current_label_row(batch_dir, str(finding.get("unit_id") or ""))
    if not isinstance(row, dict):
        return False
    has_type = bool(str(row.get("question_type") or row.get("type") or "").strip())
    knowledge = row.get("knowledge")
    return has_type and isinstance(knowledge, list) and bool(knowledge)


def _round_identity(record: dict):
    """Only exact, bound observations establish cross-round identity."""
    sources = record.get("round_sources")
    if not isinstance(sources, list) or not sources or not isinstance(sources[0], dict):
        return None
    source = sources[0]
    required = ("round_id", "execution_id", "input_binding", "verdict", "rationale", "evidence", "issue_key", "resolution_key", "assessment")
    if any(not source.get(k) for k in required) or "target_number" not in source:
        return None
    return (record.get("unit_id"), source["input_binding"], source["target_number"],
            source["issue_key"], source["resolution_key"], source["verdict"], source["rationale"],
            json.dumps(source["evidence"], ensure_ascii=False, sort_keys=True))


def _next_index(records: list) -> int:
    top = 0
    for record in records:
        fid = str(record.get("finding_id") or "")
        if fid.startswith("WF-") and fid[3:].isdigit():
            top = max(top, int(fid[3:]))
    return top + 1


def build_review_files(batch_dir: Path, result: dict, *, reconcile_rounds=False) -> dict:
    """把工作流返回值落成三份审核文件；已处理过的发现保留原状态。

    普通发现使用原有键；分轮发现只合并相同绑定、目标、判断、原文和证据。
    """
    batch_dir = Path(batch_dir)
    review_dir = batch_dir / REVIEW_DIR
    review_dir.mkdir(parents=True, exist_ok=True)

    existing = {}
    for name in ("content-review.json", "metadata-review.json"):
        payload = _load(review_dir / name) or {"findings": []}
        existing[name] = payload.get("findings", [])
    queue_path = review_dir / "manual-review-queue.json"
    queue_payload = _load(queue_path) or {"queue_version": "1.0", "items": []}
    queue = queue_payload.get("items", [])

    # 同一批次重新做了完整审查（新的计划绑定）时，上一轮遗留的未处理发现已被
    # 当前判定取代：继续留在队列/文件里，会把同一单元重复算成多条，人工队列
    # 越跑越长。已有人工结论的记录保留，作为历史与决定依据。
    from . import review
    rounds_payload = result.get("review_rounds")
    if not isinstance(rounds_payload, dict):
        stored = _load(review_dir / review.ROUND_FILE)
        if isinstance(stored, dict):
            rounds_payload = stored
    current_binding = None
    if isinstance(rounds_payload, dict):
        plan = rounds_payload.get("plan")
        if isinstance(plan, dict):
            current_binding = plan.get("input_binding")
    current_protocol = review.ROUND_PROTOCOL if isinstance(rounds_payload, dict) else None

    def _superseded(record):
        sources = record.get("round_sources") or []
        if not sources or not (current_binding or current_protocol):
            return False
        rows = [src for src in sources if isinstance(src, dict)]
        if not rows:
            return False
        # 轮次集合变化时，旧观察已不存在于当前协议。
        if any(src.get("round_id") not in review.ROUND_IDS for src in rows):
            return True
        if current_binding:
            return all(src.get("input_binding") != current_binding for src in rows)
        return all(src.get("protocol") != current_protocol for src in rows)

    def _open(item):
        status = str(item.get("status", "")).strip().lower()
        return not item.get("decided_by") and status in ("", "open", "needs_review")

    stale_missing_labels = [item for item in queue
                            if _open(item) and _stale_missing_label_finding(batch_dir, item)]
    stale_existing = [item for name in existing for item in existing[name]
                      if _open(item) and _stale_missing_label_finding(batch_dir, item)]
    if stale_missing_labels or stale_existing:
        archive_path = review_dir / "retired-round-findings.json"
        archive = _load(archive_path) or {"records": []}
        records = archive.setdefault("records", [])
        for item in [*stale_missing_labels, *stale_existing]:
            retired = dict(item, retired_reason="旧审核快照与当前正式标签不一致；当前正式批次已重新核对")
            if retired not in records:
                records.append(retired)
        _write_json(archive_path, archive)
        queue = [item for item in queue if item not in stale_missing_labels]
        for name in existing:
            existing[name] = [item for item in existing[name] if item not in stale_existing]

    if current_binding:
        # 旧轮次绑定过期不等于“这条观察从未存在”：先归档再丢弃，否则两轮之间
        # 出现又消失的差异会无声蒸发，而用户要的正是这些跨轮差异（看哪类知识点、
        # 哪个模型头容易误判）。归档只追加，重复导入不会重复记账。
        superseded = [(item, "manual-review-queue.json") for item in queue
                      if _superseded(item) and _open(item)]
        superseded += [(row, name) for name in existing for row in existing[name]
                       if _superseded(row) and _open(row)]
        if superseded:
            archive_path = review_dir / "retired-round-findings.json"
            archive = _load(archive_path) or {"records": []}
            saved = archive.setdefault("records", [])
            for item, name in superseded:
                retired = dict(item, retired_from=name,
                               retired_reason="旧轮次输入绑定已过期；原判据保留，供跨轮比对")
                if retired not in saved:
                    saved.append(retired)
            _write_json(archive_path, archive)
        for name in existing:
            existing[name] = [r for r in existing[name] if not _superseded(r)]
        queue = [item for item in queue if not (_superseded(item) and _open(item))]

    # 完整且绑定有效的两轮可重新投影当前问题队列；已取消的检查不得永久残留。
    # 仅退休派生比较发现，保留人工决定和非轮次发现，并将原记录存档。
    if reconcile_rounds:
        evaluated = review.evaluate_rounds(batch_dir, rounds_payload)
        if not evaluated.get("execution_completed"):
            raise ValueError("Cannot reconcile incomplete review rounds")
        def signature(item):
            return (str(item.get("unit_id", "")), str(item.get("field", "")),
                    str(item.get("reason") or item.get("question") or ""),
                    json.dumps(item.get("round_sources") or [], sort_keys=True, ensure_ascii=False))
        active = {signature(f) for f in [*evaluated.get("findings", []),
                                        *evaluated.get("resolved_findings", [])]}
        def obsolete(item):
            sources = item.get("round_sources") or []
            if not (_open(item) and sources):
                return False
            # 抽样发现可能没有 field，只用 round_id 标识来源；按 round_id 或 field
            # 绑定到当前输入即可判定是否属于本轮可重投影的范围。
            def bound(src):
                return (isinstance(src, dict) and (src.get("field") or src.get("round_id"))
                        and src.get("input_binding") == current_binding)
            return all(bound(s) for s in sources) and signature(item) not in active
        retired = [dict(item, retired_from=name) for name, entries in existing.items()
                   for item in entries if obsolete(item)]
        retired.extend(dict(item, retired_from="manual-review-queue.json")
                       for item in queue if obsolete(item))
        if retired:
            archive_path = review_dir / "retired-round-findings.json"
            archive = _load(archive_path) or {"records": []}
            saved = archive.setdefault("records", [])
            for item in retired:
                if item not in saved:
                    saved.append(item)
            _write_json(archive_path, archive)
            existing = {name: [item for item in entries if not obsolete(item)]
                        for name, entries in existing.items()}
            queue = [item for item in queue if not obsolete(item)]

    # 去重看的是"这条发现是否已存在"，与它有没有被处理无关；
    # 否则重复 ingest 会给未处理的发现再插一条，闸门出现重复阻断。
    handled_keys = {_key(r) for name in existing for r in existing[name]}
    previous_count = len(handled_keys)
    queue_by_key = {_key(item): item for item in queue}
    records = [r for name in existing for r in existing[name]]
    round_records = {_round_identity(r): r for r in records if _round_identity(r) is not None}
    queue_by_id = {item.get("finding_id"): item for item in queue}
    index = _next_index(records + queue)

    findings = [finding for finding in (result.get("findings") or [])
                if not _stale_missing_label_finding(batch_dir, finding)]
    if "review_rounds" in result:
        rounds = review.evaluate_rounds(batch_dir, result["review_rounds"])
        if rounds["unchecked"]:
            raise ValueError("Invalid/incomplete review_rounds: " + str(rounds["unchecked"]))
        # Persist only supplied real records; never manufacture ok verdicts.
        _write_json(review_dir / review.ROUND_FILE, result["review_rounds"])
        findings.extend(finding for finding in rounds["findings"]
                        if not _stale_missing_label_finding(batch_dir, finding))
    summary = result.get("summary") or {}
    blocking_keys = _blocking_keys(summary)

    fresh = {}
    for finding in findings:
        unit_id = str(finding.get("unit_id") or "")
        field = str(finding.get("field") or "")
        reason = str(finding.get("reason") or "")
        key = (unit_id, field, reason)
        identity = _round_identity(finding)
        is_round = bool(finding.get("round_sources"))
        comparison = is_round and all(s.get('protocol') == review.ROUND_PROTOCOL and s.get('field')
                                      for s in finding['round_sources'])
        if comparison:
            # A new participant, value, field, or input invalidates an earlier
            # decision; only an exact replay can reuse its queue entry.
            identity = None
        replay = next((r for r in records if is_round and r.get("unit_id") == unit_id
                       and ((r.get('field') == field and r.get('reason') == reason
                             and r.get('round_sources') == finding['round_sources']) if comparison
                            else finding["round_sources"][0] in r.get("round_sources", []))), None)
        if replay is not None:
            item = queue_by_id.get(replay.get("finding_id"))
            if item is None:
                item = {"finding_id": replay["finding_id"], "unit_id": unit_id,
                        "field": replay.get("field", ""), "severity": replay.get("severity", "major"),
                        "status": "open", "question": replay.get("reason", ""),
                        "reason": replay.get("reason", ""), "evidence": replay.get("evidence", []),
                        "round_sources": list(replay["round_sources"])}
                queue.append(item)
                queue_by_id[item["finding_id"]] = item
            elif item.get("round_sources") != replay["round_sources"]:
                # A previous write stopped after the review record but before
                # the queue. Never extend an existing decision to unseen sources.
                if not review._is_open(item):
                    raise ValueError("Review queue decision predates unrecorded round sources")
                item["round_sources"] = list(replay["round_sources"])
            continue
        if identity is not None and identity in round_records:
            prior = round_records[identity]
            # A missing queue entry or differing same-round execution cannot safely
            # inherit a decision. Exact replays are still idempotent.
            sources = prior["round_sources"]
            incoming = finding["round_sources"][0]
            queue_item = queue_by_id.get(prior.get("finding_id"))
            if incoming in sources:
                continue
            if (queue_item is not None and review._is_open(queue_item)
                    and review._is_open(prior)
                    and all(s.get("round_id") != incoming["round_id"] for s in sources)):
                sources.append(incoming)
                queue_item.setdefault("round_sources", []).append(incoming)
                continue
        if not is_round and key in handled_keys:
            continue
        handled_keys.add(key)
        severity = str(finding.get("severity") or "minor")
        blocking = (unit_id, field) in blocking_keys or severity in ("blocker", "major")
        record = {
            "finding_id": f"WF-{index:04d}",
            "unit_id": unit_id,
            "field": field,
            "severity": severity,
            "blocking": blocking,
            "status": "informational" if severity == "info" and not blocking else "needs_review",
            "reason": reason,
            "evidence": [str(e) for e in (finding.get("evidence") or [])],
        }
        if is_round:
            record["round_sources"] = list(finding["round_sources"])
            if identity is not None:
                round_records[identity] = record
            records.append(record)
        index += 1
        bucket = "metadata" if classify_finding(finding) == "metadata" else "content"
        fresh.setdefault(bucket, []).append(record)
        # Informational observations are retained in review history, not turned
        # into a human decision merely because the model returned them here.
        if (is_round or key not in queue_by_key) and (severity != "info" or blocking):
            item = {
                "finding_id": record["finding_id"],
                "unit_id": unit_id,
                "field": field,
                "severity": severity,
                "status": "open",
                "question": reason,
                "reason": reason,
                "evidence": record["evidence"],
            }
            if is_round:
                item["round_sources"] = list(record["round_sources"])
            queue.append(item)
            queue_by_key[key] = item
            queue_by_id[item["finding_id"]] = item

    merged = {
        "content-review.json": existing["content-review.json"] + fresh.get("content", []),
        "metadata-review.json": existing["metadata-review.json"] + fresh.get("metadata", []),
    }
    for name, rows in merged.items():
        _write_json(review_dir / name, {"findings": rows})
    _write_json(queue_path, {"queue_version": "1.0", "items": queue})

    old_unchecked = _load(review_dir / "unchecked.json")
    preserved = old_unchecked if isinstance(old_unchecked, list) else []
    preserved = [u for u in preserved if not (isinstance(u, dict) and
                 str(u.get("field", "")).startswith("review_rounds."))]
    _write_json(review_dir / "unchecked.json", preserved + review.evaluate_rounds(batch_dir)["unchecked"])

    added = sum(len(v) for v in fresh.values())
    return {
        "content": len(fresh.get("content", [])),
        "metadata": len(fresh.get("metadata", [])),
        "queue": len([q for q in queue if str(q.get("status")) == "open"]),
        "blocking": len([q for q in queue
                         if str(q.get("status")) == "open"
                         and str(q.get("severity")) in ("blocker", "major")]),
        "added": added,
        "kept": previous_count,
    }


def _load(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return None


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def record_units(batch_dir: Path, result: dict) -> int:
    """记录候选观察次数；导入次数不能证明发生了独立语义审核。"""
    review_dir = Path(batch_dir) / REVIEW_DIR
    path = review_dir / "unit-status.json"
    payload = _load(path) or {"units": {}}
    units = payload.setdefault("units", {})
    annotations = result.get("annotations") or []
    for item in annotations:
        unit_id = str(item.get("unit_id") or "")
        if not unit_id:
            continue
        previous = units.get(unit_id, {})
        units[unit_id] = {
            "number": str(item.get("number") or previous.get("number") or ""),
            "type": str(item.get("type") or previous.get("type") or ""),
            "subtype": str(item.get("subtype") or previous.get("subtype") or ""),
            "answer": str(item.get("answer") or previous.get("answer") or ""),
            "knowledge": item.get("knowledge") or previous.get("knowledge") or [],
            "difficulty": str(item.get("difficulty") or previous.get("difficulty") or ""),
            "verdict": str(item.get("verdict") or previous.get("verdict") or "missing"),
            # Preserve old counters as historical data, never increment them as
            # independent reviews merely because the same result was reimported.
            "rounds": previous.get("rounds", 0),
            "observations": int(previous.get("observations") or 0) + 1,
            "last_at": mf.now_iso(),
        }
    _write_json(path, payload)
    return len(annotations)


def ingest(batch_dir, result_path=None, stage: str = "auto", actor: str = "workflow",
           note: str = "") -> dict:
    batch_dir = Path(batch_dir)
    manifest = mf.load(batch_dir)
    candidates = scan_candidates(batch_dir)
    report = {"candidates": len(candidates), "marked": [], "skipped": [], "review": None,
              "units": 0}

    result = None
    if result_path:
        path = Path(result_path)
        if not path.is_file():
            raise FileNotFoundError(f"找不到工作流结果：{path}")
        result = json.loads(path.read_text(encoding="utf-8-sig"))
        report["review"] = build_review_files(batch_dir, result)
        report["units"] = record_units(batch_dir, result)

    targets = []
    if stage == "auto":
        targets = ["types", "explanations", "knowledge", "review"]
    elif stage in ("types", "explanations", "knowledge", "review"):
        targets = [stage]
    else:
        raise ValueError(f"未知阶段：{stage}")

    for stage_id in targets:
        spec = contracts.STAGE_BY_ID[stage_id]
        missing = mf.missing_produces(batch_dir, spec)
        if missing:
            report["skipped"].append({"stage": stage_id, "missing": missing})
            continue
        if stage_id == "review":
            from .review import evaluate_rounds
            rounds = evaluate_rounds(batch_dir)
            if not rounds["execution_completed"]:
                report["skipped"].append({"stage": stage_id, "review_rounds": rounds["errors"]})
                continue
        mf.touch_stage(
            manifest, stage_id, mf.COMPLETED,
            input_hashes=mf.input_hashes(batch_dir, manifest, spec),
            output_hashes=mf.output_hashes(batch_dir, spec),
            notes=note or f"由工作流登记（{len(candidates)} 个候选）",
        )
        report["marked"].append(stage_id)

    manifest.setdefault("workflow_runs", []).append({
        "at": mf.now_iso(),
        "actor": actor,
        "batch_dir": str(batch_dir),
        "candidates": [c["file"] for c in candidates],
        "result_path": str(result_path) if result_path else None,
        "annotations": len(result.get("annotations") or []) if result else 0,
        "findings": len(result.get("findings") or []) if result else 0,
        "note": note,
    })
    manifest["status"] = mf.derive_status(manifest)
    mf.save(batch_dir, manifest)
    mf.append_log(batch_dir, {"event": "ingest", "marked": report["marked"],
                              "candidates": report["candidates"]})
    return report
