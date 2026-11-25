# -*- coding: utf-8 -*-
"""Unit content editing, label saving, post-edit receipt sync & anomaly resolution.

Extracted from app/workbench/server.py (Rule 49) to keep the HTTP server module
focused. All public symbols are re-exported by workbench.server for compatibility.
"""
from __future__ import annotations

from contextlib import ExitStack
import copy
import json
from pathlib import Path

from workflow.workflow_primitives import read_json_safe as load

MANUAL_CONTENT_FIELDS = ("explanation", "articleSummary")


def _srv():
    from . import server
    return server


def _content_row(payload, unit_id):
    rows = payload.get("items") if isinstance(payload, dict) else None
    matches = [row for row in (rows if isinstance(rows, list) else [])
               if isinstance(row, dict) and (row.get("unit_id") or row.get("id")) == unit_id]
    if len(matches) != 1:
        raise ValueError(f"无法唯一定位正式单元 {unit_id}，未保存")
    return matches[0]


def _atomic_write_json_retry(path: Path, data: dict) -> None:
    """Atomically write JSON using manifest's Windows-safe retry replace."""
    from workflow import manifest as mf
    import os
    import threading
    import uuid

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex[:8]}.tmp"
    )
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        mf._replace_with_retry(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _update_stage_batch_hashes(folder: Path, current: dict, new_batch_sha: str, new_exp_sha: str | None = None) -> None:
    """Refresh on-disk output hashes for explanations/assemble/underlines without guessing line endings."""
    srv = _srv()
    folder = Path(folder).resolve()
    wf_manifest = srv.wf_manifest
    stages = current.get("stages")
    if not isinstance(stages, dict):
        return
    exp_artifact = "04_knowledge/explanations.json"
    batch_target = "06_batch/batch_meta.json"
    exp_rec = stages.get("explanations")
    if new_exp_sha and isinstance(exp_rec, dict) and exp_rec.get("status") == wf_manifest.COMPLETED:
        exp_rec["output_hashes"] = {exp_artifact: new_exp_sha}
    for sid in ("assemble", "underlines"):
        rec = stages.get(sid)
        if not isinstance(rec, dict) or rec.get("status") != wf_manifest.COMPLETED:
            continue
        out_hashes = dict(rec.get("output_hashes") or {})
        if batch_target in out_hashes:
            out_hashes[batch_target] = new_batch_sha
            rec["output_hashes"] = out_hashes


def _refresh_report_hash_if_bound(folder: Path, rel_path: str, field: str, old_sha: str | None, new_sha: str) -> bool:
    """Update a downstream gate receipt's batch/final sha256 only if it was bound to old_sha."""
    path = folder / rel_path
    if not path.is_file() or path.is_symlink():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    prev = data.get(field)
    if not prev:
        return False
    if old_sha and prev != old_sha and prev != new_sha:
        return False
    if prev == new_sha:
        return True
    data[field] = new_sha
    _atomic_write_json_retry(path, data)
    return True


def sync_manual_edit_receipts(
    folder: Path,
    *,
    old_batch_sha: str | None = None,
    run_validate: bool = True,
) -> dict:
    """Keep assemble/validate/review_gate/format_gate receipts consistent after a formal content edit.

    - Computes SHA-256 directly from the written files on disk (never guesses os.linesep).
    - Runs deterministic offline `validate` + `format_gate` when confirmed source structure exists
      (zero model calls) so manual edits cannot bypass hard validation or trip handoff/supervisor.
    - Updates `review-gate.json` `batch_sha256` when bound to the edited batch so handoff stays valid.
    """
    srv = _srv()
    folder = Path(folder).resolve()
    wf_manifest = srv.wf_manifest
    wf_contracts = srv.wf_contracts
    result = {"validate_passed": None, "format_gate_passed": None, "warnings": []}
    if wf_manifest is None or wf_contracts is None:
        return result
    manifest_path = folder / "workflow" / "manifest.json"
    batch_path = folder / "06_batch" / "batch_meta.json"
    if not manifest_path.is_file() or not batch_path.is_file():
        return result

    new_batch_sha = wf_manifest.sha256_file(batch_path)
    exp_path = folder / "04_knowledge" / "explanations.json"
    new_exp_sha = wf_manifest.sha256_file(exp_path) if exp_path.is_file() else None

    current = wf_manifest.load(folder)
    _update_stage_batch_hashes(folder, current, new_batch_sha, new_exp_sha)
    wf_manifest.save(folder, current)

    stages = current.get("stages") or {}
    was_validate_done = (stages.get("validate") or {}).get("status") == wf_manifest.COMPLETED
    was_format_done = (stages.get("format_gate") or {}).get("status") == wf_manifest.COMPLETED
    has_source_structure = (folder / "01_source" / "structure.confirmed.json").is_file()

    if run_validate and has_source_structure and (was_validate_done or was_format_done):
        from workflow import format_gate, runner
        prev_validate = copy.deepcopy(stages.get("validate")) if was_validate_done else None
        prev_format = copy.deepcopy(stages.get("format_gate")) if was_format_done else None
        validate_code = runner.do_validate(folder, current, lock_held=True, refresh_only=True)
        result["validate_passed"] = (validate_code == 0)
        fresh = wf_manifest.load(folder)
        _update_stage_batch_hashes(folder, fresh, new_batch_sha, new_exp_sha)

        if validate_code == 0:
            # Preserve original timestamps so post-completion edits do not inflate batch duration.
            if isinstance(prev_validate, dict):
                v_rec = fresh.setdefault("stages", {}).setdefault("validate", {})
                if prev_validate.get("started_at"):
                    v_rec["started_at"] = prev_validate["started_at"]
                if prev_validate.get("finished_at"):
                    v_rec["finished_at"] = prev_validate["finished_at"]
            # Refresh review_gate receipt binding so handoff recognizes the edited batch.
            if _refresh_report_hash_if_bound(folder, "07_review/review-gate.json", "batch_sha256", old_batch_sha, new_batch_sha):
                rg_spec = wf_contracts.STAGE_BY_ID.get("review_gate")
                rg_rec = fresh.get("stages", {}).get("review_gate")
                if rg_spec is not None and isinstance(rg_rec, dict) and rg_rec.get("output_hashes"):
                    rg_rec["output_hashes"] = wf_manifest.output_hashes(folder, rg_spec)
            if was_format_done:
                try:
                    fg_report = format_gate.run(folder, write_report=False)
                    deferred_write = True
                except TypeError:
                    fg_report = format_gate.run(folder)
                    deferred_write = False
                fg_passed = bool(fg_report.get("passed") is True)
                result["format_gate_passed"] = fg_passed
                if fg_passed:
                    if deferred_write:
                        format_gate.write_report_file(folder, fg_report)
                    fg_spec = wf_contracts.STAGE_BY_ID["format_gate"]
                    wf_manifest.touch_stage(
                        fresh, "format_gate", wf_manifest.COMPLETED,
                        input_hashes=wf_manifest.input_hashes(folder, fresh, fg_spec),
                        output_hashes=wf_manifest.output_hashes(folder, fg_spec),
                        exit_code=0, kind=fg_spec.kind, notes="",
                    )
                    if isinstance(prev_format, dict):
                        fg_rec = fresh.setdefault("stages", {}).setdefault("format_gate", {})
                        if prev_format.get("started_at"):
                            fg_rec["started_at"] = prev_format["started_at"]
                        if prev_format.get("finished_at"):
                            fg_rec["finished_at"] = prev_format["finished_at"]
                    wf_manifest.record_semantics(folder, fresh, fg_spec)
                else:
                    if isinstance(prev_format, dict):
                        fresh.setdefault("stages", {})["format_gate"] = prev_format
                    result["warnings"].append("修改已保存，但最终上传格式复检未通过，请在上传前检查格式报告")
        else:
            if isinstance(prev_validate, dict):
                fresh.setdefault("stages", {})["validate"] = prev_validate
            if isinstance(prev_format, dict):
                fresh.setdefault("stages", {})["format_gate"] = prev_format
            fresh["status"] = wf_manifest.derive_status(fresh)
            result["warnings"].append("修改已保存，但本地体检复验未通过，请在上传前检查体检报告")
        wf_manifest.save(folder, fresh)
        return result

    # Minimal / source-less batch: only keep already-bound receipt hashes in sync.
    fresh = wf_manifest.load(folder)
    _update_stage_batch_hashes(folder, fresh, new_batch_sha, new_exp_sha)
    for sid, rel, field in (
        ("validate", "07_review/validation-report.json", "batch_sha256"),
        ("review_gate", "07_review/review-gate.json", "batch_sha256"),
        ("format_gate", "07_review/upload-format.json", "final_sha256"),
    ):
        if _refresh_report_hash_if_bound(folder, rel, field, old_batch_sha, new_batch_sha):
            spec = wf_contracts.STAGE_BY_ID.get(sid)
            rec = fresh.get("stages", {}).get(sid)
            if spec is not None and isinstance(rec, dict) and rec.get("output_hashes"):
                rec["output_hashes"] = wf_manifest.output_hashes(folder, spec)
    wf_manifest.save(folder, fresh)
    return result


def apply_manual_content(folder: Path, unit_id, unit, expected, files, current,
                         payload, has_unit_edit) -> dict:
    """Stage the accepted text, provenance and gate changes for one install."""
    srv = _srv()
    folder = Path(folder).resolve()
    wf_manifest = srv.wf_manifest
    wf_contracts = srv.wf_contracts
    from workflow import manual_content, stage_semantics
    from workflow.agent_tasks import _safe_path

    unit = unit or {}
    edit = {key: unit[key] for key in MANUAL_CONTENT_FIELDS if key in unit}
    result = {"applied": False, "paths": [], "skipped": [], "reason": ""}
    if not edit:
        return result
    if any(not isinstance(value, str) for value in edit.values()):
        raise ValueError("解析和文章大意必须是文本")
    target = "06_batch/batch_meta.json"
    artifact = "04_knowledge/explanations.json"
    row = _content_row(payload, unit_id)
    if expected is not None and any(expected.get(key) != (row.get(key) or "") for key in edit):
        raise ValueError("正式内容已变化，请重新打开本题后再修改")
    edit = {key: value for key, value in edit.items() if row.get(key, "") != value}
    if not edit and not has_unit_edit:
        return result
    explanations = load(_safe_path(folder, artifact))
    explanation_row = _content_row(explanations, unit_id)
    if current is not None:
        stages = current["stages"]
        record = stages["explanations"]
        if (stages["upload_gate"].get("status") == wf_manifest.APPROVED
                or stages["upload"].get("status") == wf_manifest.COMPLETED):
            raise ValueError("此批次已批准上传或已上传，不能覆盖已绑定的内容")
        spec = wf_contracts.STAGE_BY_ID["explanations"]
        stale, _ = wf_manifest.is_stale(folder, current, spec)
        if record.get("status") != wf_manifest.COMPLETED or stale or record.get("rebuild_required"):
            raise ValueError("解析阶段未完成或产物已过期，不能同步正式批次")
    if has_unit_edit:
        files[target] = payload
        files[manual_content.RELATIVE] = manual_content.prepare(folder, unit_id, edit)
        result["paths"].append(manual_content.RELATIVE)
    if edit:
        explanation_row.update(edit)
        row.update(edit)
        files[artifact] = explanations
        files[target] = payload
        result.update(applied=True, paths=[artifact, target, *result["paths"]])
        if current is not None:
            if record.get("semantic_version") == stage_semantics.VERSION:
                record["semantic_outputs"] = {artifact: stage_semantics.digest(stage_semantics.without_clocks(explanations))}
            wf_manifest.record_decision(current, "explanations", "manual_edit", "workbench",
                                        f"{unit_id}: {', '.join(edit)}", result["paths"])
    return result


def _validate_submitted_type_and_subtypes(taxonomy_mod, labels: dict, main_type, subtypes, unit_kind: str) -> None:
    """Hard-validate submitted main type and subtypes against the catalog at save time."""
    if "type" in labels and str(labels.get("type") or "").strip():
        submitted_type = str(labels["type"]).strip()
        if submitted_type not in taxonomy_mod.types():
            raise ValueError(f"主类题型「{submitted_type}」不在目录中，请从正式目录选择")
    if any(k in labels for k in ("type", "subtype", "subtypes")) and subtypes:
        cleaned_type = str(main_type or "").strip()
        problems = taxonomy_mod.check_labels(
            {"type": cleaned_type, "subtypes": subtypes},
            unit_kind=unit_kind,
        )
        if problems:
            raise ValueError("；".join(problems))


def save_content_edit(folder: Path, unit_id: str, patch: dict) -> dict:
    from workflow import ai_supplement, supervisor
    from workflow.agent_tasks import _safe_path
    from workflow.local_agent import _batch_lock, AgentError

    if not folder.is_dir() or folder.is_symlink():
        raise ValueError("批次不存在或已归档，请刷新列表")
    run_lock = _safe_path(folder, "workflow/.run.lock")
    busy = f"批次「{folder.name}」正在运行（{folder}），本次未保存"
    with ExitStack() as locks:
        if not locks.enter_context(supervisor._try_file_lock(supervisor._auto_lock_path(folder))):
            raise ValueError(busy)
        run_lock.parent.mkdir(parents=True, exist_ok=True)
        if not locks.enter_context(supervisor._try_file_lock(run_lock)):
            raise ValueError(busy)
        try:
            locks.enter_context(_batch_lock(folder, 0))
        except AgentError as exc:
            raise ValueError(busy) from exc
        locks.enter_context(ai_supplement.content_write_lock(folder, timeout=5.0))
        result = ai_supplement.save_content_edit(folder, unit_id, patch, lock_held=True)
        from workflow import unit_anomalies
        if isinstance(patch, dict):
            if any(k in patch for k in ("explanation", "articleSummary", "subquestions")):
                unit_anomalies.resolve_stage_anomalies(folder, "explanations", [unit_id])
            if "answer" in patch or any(
                isinstance(sq, dict) and "answer" in sq
                for sq in (patch.get("subquestions") or [])
            ):
                unit_anomalies.resolve_stage_anomalies(folder, "answers", [unit_id])
        return result


def save_units(folder: Path, unit_id: str, items: list[dict], labels: dict | None = None,
               unit: dict | None = None, expected: dict | None = None) -> dict:
    from workflow import supervisor
    from workflow.agent_tasks import _safe_path
    from workflow.local_agent import _batch_lock, AgentError

    if not folder.is_dir() or folder.is_symlink():
        raise ValueError("批次不存在或已归档，请刷新列表")
    run_lock = _safe_path(folder, "workflow/.run.lock")
    busy = f"批次「{folder.name}」正在运行（{folder}），本次未保存"
    with ExitStack() as locks:
        if not locks.enter_context(supervisor._try_file_lock(supervisor._auto_lock_path(folder))):
            raise ValueError(busy)
        run_lock.parent.mkdir(parents=True, exist_ok=True)
        if not locks.enter_context(supervisor._try_file_lock(run_lock)):
            raise ValueError(busy)
        try:
            locks.enter_context(_batch_lock(folder, 0))
        except AgentError as exc:
            raise ValueError(busy) from exc
        return _save_units_locked(folder, unit_id, items, labels, unit, expected)


def _save_units_locked(folder, unit_id, items, labels, unit, expected):
    srv = _srv()
    folder = Path(folder).resolve()
    wf_manifest = srv.wf_manifest
    taxonomy = srv.taxonomy
    from workflow.agent_tasks import _install, _safe_path

    files = {}
    manifest_rel = "workflow/manifest.json"
    manifest_path = _safe_path(folder, manifest_rel)
    current = wf_manifest.load(folder) if wf_manifest is not None and manifest_path.is_file() else None
    batch_path = _safe_path(folder, "06_batch/batch_meta.json")
    old_batch_sha = (
        wf_manifest.sha256_file(batch_path)
        if wf_manifest is not None and batch_path.is_file()
        else None
    )
    if unit:
        try:
            payload = load(batch_path)
        except (OSError, ValueError) as exc:
            raise ValueError(f"批次「{folder.name}」缺少可编辑的正式批次（{exc}），未保存") from exc
    else:
        payload = None
    applied = srv.apply_manual_content(folder, unit_id, unit, expected, files, current, payload, bool(unit))
    rows = srv.merged_rows(folder, srv.report_for(folder))
    confirmed = load(folder / "01_source/structure.confirmed.json", {}) or {}
    scoped_units = [u for u in confirmed.get("units", []) if isinstance(u, dict)
                    and u.get("identity_version") == "scoped-v1"]
    scoped = bool(scoped_units) or any(r.get("identity_version") == "scoped-v1" for r in rows)
    selected = next((u for u in scoped_units if str(u.get("unit_id")) == str(unit_id)), None)
    if scoped and selected is None:
        selected = next((r for r in rows if r.get("identity_version") == "scoped-v1"
                         and str(r.get("unit_id")) == str(unit_id)), None)
    if scoped and selected is None:
        raise ValueError("分节重号批次必须指定有效 unit_id，不能按小题号保存")
    by_number = srv.candidates_by_number(folder, unit_id=unit_id if scoped else None)
    owner = {}
    for row in ([selected] if scoped else rows):
        for number in row.get("numbers") or []:
            owner[str(number)] = row.get("unit_id")
    if scoped:
        for item in items:
            number = str(item.get("number") or "").strip()
            if number not in owner:
                raise ValueError("小题号不属于指定 unit_id，拒绝跨大题保存")
            if number not in by_number and not (selected.get("question_ids") or {}).get(number):
                raise ValueError("指定 unit_id 缺少小题身份，拒绝按全局题号建草稿")
    saved = []
    warnings = []
    with srv._lock:
        if unit:
            allowed = {k: unit[k] for k in ("material",) if k in unit}
            if allowed:
                safe = str(unit_id).replace("/", "_").replace("\\", "_")
                target = folder / "candidates" / f"{safe}.json"
                merged = dict(load(target, {}))
                merged.setdefault("unit_id", str(unit_id))
                merged.pop("number", None)
                merged.update({k: ("" if v is None else str(v)) for k, v in allowed.items()})
                merged["updated_by"] = "workbench"
                merged["updated_at"] = srv.now()
                relative = target.relative_to(folder).as_posix()
                files[relative] = merged
                saved.append(relative)
        label_diff = {"type_changed": False, "kp_changed": False, "kp_nonempty": False, "ap_only": False}
        if labels:
            target = srv.unit_label_path(folder, unit_id)
            merged = dict(load(target, {}))
            matched_row = next((row for row in rows if str(row.get("unit_id")) == str(unit_id)), {})
            prev_type = str(merged.get("type") or matched_row.get("type") or matched_row.get("question_type") or "").strip()
            prev_sub_raw = merged.get("subtypes") or ([merged["subtype"]] if merged.get("subtype") else (matched_row.get("subtypes") or []))
            prev_subtypes = sorted(str(x).strip() for x in (prev_sub_raw or []) if str(x).strip())
            prev_kp_raw = merged.get("knowledge") if "knowledge" in merged else (matched_row.get("knowledge") or [])
            prev_knowledge = [str(x).strip() for x in (prev_kp_raw or []) if str(x).strip()]
            for key in ("type", "subtype", "subtypes", "difficulty", "difficultyValue", "categories",
                        "knowledge", "source", "source_type", "year", "location"):
                if key in labels:
                    merged[key] = labels[key]
            subtypes = merged.get("subtypes") or ([merged["subtype"]] if merged.get("subtype") else [])
            main_type = merged.get("type") or matched_row.get("type") or matched_row.get("question_type")
            numbers = [str(n) for n, own in owner.items() if str(own) == str(unit_id)]
            unit_kind = (matched_row.get("unit_kind") or labels.get("unit_kind")
                         or ("group" if len(numbers) > 1 else "standalone"))
            _validate_submitted_type_and_subtypes(taxonomy, labels, main_type, subtypes, unit_kind)
            knowledge = taxonomy.require_knowledge(merged.get("knowledge", []), main_type, unit_kind=unit_kind)
            new_type = str(main_type or "").strip()
            new_subtypes = sorted(str(x).strip() for x in (subtypes or []) if str(x).strip())
            label_diff["type_changed"] = (new_type != prev_type) or (new_subtypes != prev_subtypes)
            label_diff["kp_changed"] = [str(x).strip() for x in knowledge if str(x).strip()] != prev_knowledge
            label_diff["kp_nonempty"] = bool(knowledge)
            label_diff["ap_only"] = bool(taxonomy.answer_point_only_type(main_type, unit_kind))
            merged["knowledge"] = knowledge
            resolved = taxonomy.resolve_knowledge(knowledge, main_type, unit_kind=unit_kind)
            merged["knowledgeIds"] = [point["id"] for point in resolved["resolved"]]
            filters = dict(merged.get("filterMetadata") or {})
            filters["subtypes"] = subtypes
            filters["categories"] = [str(x) for x in (merged.get("categories") or []) if str(x).strip()]
            if "difficultyValue" in merged:
                filters["difficultyValue"] = merged["difficultyValue"]
            merged["filterMetadata"] = filters
            found = taxonomy.check_labels({
                "type": merged.get("type"), "subtypes": subtypes,
                "difficulty": merged.get("difficulty"), "categories": filters["categories"],
                "knowledge": knowledge, "unit_kind": unit_kind,
            }, unit_kind=unit_kind)
            if unit_kind == "group":
                found += taxonomy.check_unit_dims(merged.get("type"), knowledge, unit_kind="group")
            else:
                found += taxonomy.check_per_question_required(merged.get("type"), knowledge)
            merged["labelWarnings"] = found
            merged["unit_id"] = str(unit_id)
            merged["applies_to"] = "unit"
            merged["numbers"] = sorted(numbers, key=lambda x: int(x)) if numbers else []
            merged["inherit_subquestions"] = len(merged["numbers"]) > 1
            merged["updated_by"] = "workbench"
            merged["updated_at"] = srv.now()
            relative = target.relative_to(folder).as_posix()
            files[relative] = merged
            saved.append(relative)
            warnings.extend(f"单元 {unit_id}：{text}" for text in found)
        for item in items:
            number = str(item.get("number") or "").strip()
            if not number.isdigit():
                raise ValueError("缺少合法小题号")
            existing, path = by_number.get(number, ({}, None))
            if path is None:
                stem_name = (str((selected.get("question_ids") or {}).get(number) or "")
                             if scoped else f"LOCAL-Q{int(number):02d}")
                if not stem_name:
                    raise ValueError("指定 unit_id 缺少小题身份，拒绝按全局题号建草稿")
                stem_name = stem_name.replace("/", "_").replace("\\", "_")
                path = folder / "candidates" / f"{stem_name}.json"
                index = 2
                while path.exists():
                    path = folder / "candidates" / f"{stem_name}-{index}.json"
                    index += 1
                existing = {"unit_id": owner.get(number) or f"LOCAL-Q{int(number):02d}", "number": number}
            if number in by_number:
                merged = {key: item[key] for key in ("stem", "options", "answer") if key in item}
            else:
                merged = dict(existing)
                for key in ("stem", "options", "answer"):
                    if key in item:
                        merged[key] = item[key]
            merged["updated_by"] = "workbench"
            merged["updated_at"] = srv.now()
            relative = path.relative_to(folder).as_posix()
            files[relative] = merged
            saved.append(relative)
    if unit and "material" in unit:
        applied["skipped"].append("material")
        warnings.append("材料仅保存为草稿")
    if items:
        applied["skipped"].append("items")
        warnings.append("小题改动仅保存为草稿")
    if files and current is not None:
        current["updated_at"] = wf_manifest.now_iso()
        files[manifest_rel] = current
    if files:
        _install(folder, files)
        if applied.get("applied") and current is not None:
            sync_res = sync_manual_edit_receipts(folder, old_batch_sha=old_batch_sha, run_validate=True)
            warnings.extend(sync_res.get("warnings") or [])
        from workflow import unit_anomalies
        if (labels and not unit and not items
                and any(k in labels for k in ("type", "subtype", "subtypes"))
                and (label_diff["type_changed"] or not label_diff["kp_changed"])):
            unit_anomalies.resolve_stage_anomalies(folder, "types", [unit_id])
        if (labels and "knowledge" in labels
                and (label_diff["kp_changed"] or (not label_diff["type_changed"] and (label_diff["kp_nonempty"] or label_diff["ap_only"])))):
            unit_anomalies.resolve_stage_anomalies(folder, "knowledge", [unit_id])
        if applied.get("applied") or (unit and any(k in unit for k in ("explanation", "articleSummary"))):
            unit_anomalies.resolve_stage_anomalies(folder, "explanations", [unit_id])
        if (items and any(isinstance(item, dict) and "answer" in item for item in items)
                and unit_id in unit_anomalies.quarantined_unit_ids(folder, "answers")):
            warnings.append("答案改动仅为草稿，答案提取隔离仍保留；写入正式答案后才解除，解除前不能上传")
    return {"saved": saved, "warnings": warnings, "applied": applied}
