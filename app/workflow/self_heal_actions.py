"""Transactional, source-proven local format repair. Never grants review approval.

Only an explicitly supplied current batch is written. Original review plans,
executions and source files are immutable. Each replacement is atomic; a private
journal and cooperative lock make the multi-file operation rollback as a unit.
A prepared/interrupted journal is never compatibility evidence.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid

from . import batch_fill, contracts, denoise, extract, format_gate, legacy_format
from . import manifest as mf
from . import question_content, question_draft, structure_gate

VERSION = "upload-workflow.source-format-repair/v1"
MIGRATIONS = "workflow/format-migrations"
CONTROL_PATHS = ("workflow/manifest.json", "workflow/delivery.json",
                 batch_fill.STATE, batch_fill.BATCH_BASE, question_draft.PATH,
                 format_gate.REPORT_PATH, "07_review/validation-report.json")


def _canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def _path(root, relative):
    if (not isinstance(relative, str) or not relative or chr(92) in relative
            or Path(relative).is_absolute()
            or any(p in ("", ".", "..") for p in relative.split("/"))):
        raise ValueError("Expected a safe batch-relative path")
    path = format_gate._safe(root / relative)
    if not path.is_relative_to(root) or (path.exists() and not path.is_file()):
        raise ValueError("Repair expects regular files inside the batch")
    return path


def _raw(root, relative):
    path = _path(root, relative)
    return path.read_bytes() if path.is_file() else None


def _atomic_raw(root, relative, raw):
    target = _path(root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(target.stat().st_mode) if target.is_file() else 0o600
    fd, name = tempfile.mkstemp(prefix=".format-repair-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.chmod(name, mode) if not hasattr(os, "fchmod") else os.fchmod(stream.fileno(), mode)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        _path(root, relative)
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _rule_hashes():
    from . import label_audit
    from workbench import taxonomy
    paths = {*format_gate.RULE_FILES, *label_audit.RULE_FILES, Path(taxonomy.__file__),
             Path(__file__), Path(__file__).with_name("self_heal.py"), Path(__file__).with_name("preview.py"),
             *(Path(module.__file__) for module in
               (legacy_format, question_content, question_draft, structure_gate, denoise, extract))}
    base = Path(__file__).resolve().parents[1]
    return {path.relative_to(base).as_posix(): format_gate._sha(path.read_bytes())
            for path in sorted(paths)}


def _source_hashes(root):
    """Discover actual source evidence, including registered original uploads.

    Discovery is repeated, so a forged receipt cannot omit an original and new
    source evidence cannot be added unnoticed. External originals are read only.
    """
    paths = {str(_path(root, rel)): rel for rel in legacy_format.SOURCE_PATHS}
    source_dir = format_gate._safe(root / "01_source")
    if source_dir.is_dir():
        for path in source_dir.rglob("*"):
            format_gate._safe(path)
            if path.is_file():
                paths[str(path)] = path.relative_to(root).as_posix()
    manifest_raw = _raw(root, "workflow/manifest.json")
    if manifest_raw is not None:
        manifest = format_gate._load(manifest_raw)
        inputs = manifest.get("inputs", {})
        if not isinstance(inputs, dict):
            raise ValueError("Invalid registered source inputs")
        for key, value in inputs.items():
            if not isinstance(value, dict) or not value.get("path"):
                if key in ("source", "docx"):
                    raise ValueError("Missing registered original path")
                continue
            path = Path(value["path"])
            if not path.is_absolute():
                path = root / path
            path = format_gate._safe(path)
            if not path.is_file():
                raise ValueError("Registered original input is missing: " + str(key))
            label = path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)
            paths[str(path)] = label
    return {label: format_gate._sha(Path(path).read_bytes())
            for path, label in sorted(paths.items())}


def _check_sources(root, sources, rules):
    if _source_hashes(root) != sources or _rule_hashes() != rules:
        raise ValueError("Source evidence or format rules changed during repair")


def _assert_snapshot(root, snapshots):
    for relative, raw in snapshots.items():
        path = Path(relative)
        if path.is_absolute():
            path = format_gate._safe(path)
            current = path.read_bytes() if path.is_file() else None
        else:
            current = _raw(root, relative)
        if current != raw:
            raise ValueError("Input changed during format repair: " + relative)


def _check_expected(root, expected):
    if expected is None:
        return
    if not isinstance(expected, dict):
        raise ValueError("expected_inputs must map paths to raw SHA256")
    for key, digest in expected.items():
        if (not isinstance(key, str) or not isinstance(digest, str)
                or digest != "missing" and not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise ValueError("Invalid expected input path/hash")
        path = Path(key)
        path = format_gate._safe(path) if path.is_absolute() else _path(root, key)
        if path.exists() and not path.is_file():
            raise ValueError("Expected input is not a regular file: " + key)
        actual = format_gate._sha(path.read_bytes()) if path.is_file() else "missing"
        if actual != digest:
            raise ValueError("Expected input changed: " + key)


def _seal_receipt(receipt):
    value = deepcopy(receipt)
    value.pop("receipt_sha256", None)
    receipt["receipt_sha256"] = format_gate._sha(_canonical(value))
    return receipt


def _refresh_manifest(root, manifest, *, preserve_validated_format=False, receipt_path=None):
    """Refresh only validated format producers and deterministic consumers."""
    refreshed, invalidated = [], []
    for stage_id in ("assemble", "meta", "underlines"):
        record = manifest.get("stages", {}).get(stage_id)
        if not record or not (record.get("status") in (mf.COMPLETED, mf.APPROVED)
                              or record.get("execution_completed")):
            continue
        stage = contracts.STAGE_BY_ID[stage_id]
        if mf.missing_produces(root, stage):
            raise ValueError("Cannot refresh incomplete producer: " + stage_id)
        record["input_hashes"] = mf.input_hashes(root, manifest, stage)
        record["output_hashes"] = mf.output_hashes(root, stage)
        mf.record_semantics(root, manifest, stage)
        refreshed.append(stage_id)
    for stage_id in ("validate", "format_gate"):
        record = manifest.get("stages", {}).get(stage_id)
        if not record:
            continue
        if stage_id == "validate" and preserve_validated_format and record.get("status") == mf.COMPLETED:
            raw = _raw(root, "07_review/validation-report.json")
            try:
                prior = format_gate._load(raw) if raw else {}
            except (ValueError, TypeError):
                prior = {}
            recorded = (record.get("output_hashes") or {}).get("07_review/validation-report.json")
            if (isinstance(prior, dict) and prior.get("passed") is True
                    and isinstance(prior.get("source_audit"), dict)
                    and prior["source_audit"].get("passed") is True
                    and recorded and recorded == format_gate._sha(raw)):
                # Only empty representation/printed-locator normalization is
                # permitted here. Real after-source and after-format checks have
                # just passed; keep the unchanged original validation evidence.
                spec = contracts.STAGE_BY_ID["validate"]
                record["input_hashes"] = mf.input_hashes(root, manifest, spec)
                mf.record_semantics(root, manifest, spec)
                record["format_equivalence_revalidation"] = {
                    "receipt_path": receipt_path, "checks": ["source_audit", "upload_format"],
                    "original_report_preserved": True}
                refreshed.append(stage_id)
                continue
        # Invalidating an execution is not permission to erase its quality facts.
        quality = {key: deepcopy(record[key]) for key in
                   ("quality_status", "quality_passed", "release_ready",
                    "execution_completed", "continue_unqualified") if key in record}
        invalidated.extend(mf.invalidate_stage(root, manifest, stage_id,
            "Source-proven local format repair; rerun deterministic check"))
        record.update(quality)
    return refreshed, invalidated


def _refresh_delivery(root, manifest, refreshed, invalidated):
    raw = _raw(root, "workflow/delivery.json")
    if raw is None:
        return None
    delivery = format_gate._load(raw)
    if not isinstance(delivery, dict) or not isinstance(delivery.get("stages"), dict):
        raise ValueError("Invalid durable delivery state")
    for stage_id in refreshed:
        entry = delivery["stages"].get(stage_id)
        if not isinstance(entry, dict):
            continue
        stage = contracts.STAGE_BY_ID[stage_id]
        entry["artifacts"] = mf._delivery_artifacts(root, stage)
        entry["input_hashes"] = mf.input_hashes(root, manifest, stage)
        entry["updated_at"] = mf.now_iso()
    for stage_id in invalidated:
        entry = delivery["stages"].get(stage_id)
        if isinstance(entry, dict):
            entry.update(status="pending", reason="Source-proven local format repair",
                         updated_at=mf.now_iso())
    return _canonical(delivery)


def _prepare_tables(root):
    result = {"passed": False, "files": {}, "changes": [], "refused": [],
              "input_sha256": {}, "checks": {}}
    source = _path(root, legacy_format.SOURCE_PATHS[0]).read_text(encoding="utf-8-sig")
    structure, confirmed = (format_gate._load(_path(root, rel).read_bytes())
                            for rel in legacy_format.SOURCE_PATHS[1:])
    by_key, rebuilt, _ = legacy_format._source_units(confirmed, structure, source)
    for relative in (*legacy_format.SOURCE_PATHS, *legacy_format.BATCH_PATHS):
        raw = _raw(root, relative)
        if raw is not None:
            result["input_sha256"][relative] = format_gate._sha(raw)
    for relative in legacy_format.BATCH_PATHS:
        raw = _raw(root, relative)
        if raw is None:
            continue
        original = format_gate._load(raw)
        before = question_content.audit_batch_source(root, original)
        candidate = deepcopy(original)
        count = question_content.restore_table_blocks_from_structure(root, candidate)
        # Earlier drafts dropped Word gridBefore/gridAfter while retaining the
        # other cells/spans. Repair only an exact replay of that known projection;
        # never infer missing positions from unequal row widths alone.
        extraction_raw = _raw(root, "01_source/extraction.json")
        extraction = format_gate._load(extraction_raw) if extraction_raw else {}
        padded = set()
        for index, (old, current) in enumerate(zip(original["items"], candidate["items"])):
            if old != current:
                continue
            field = "materialBlocks" if old.get("materialBlocks") else "material_blocks"
            blocks = old.get(field)
            if not isinstance(blocks, list) or not blocks:
                continue
            unit = legacy_format._bound_unit(old, by_key, rebuilt)
            expected_blocks = deepcopy(blocks)
            changed = False
            for position, block in enumerate(blocks):
                if not isinstance(block, dict) or block.get("type") != "table":
                    continue
                source_tables = {"\n".join(line.rstrip() for line in m.group(0).strip().splitlines())
                                 for m in question_draft._TABLE.finditer(unit["stem"])}
                matches = [t for t in extraction.get("tables", []) if isinstance(t, dict)
                    and "\n".join(line.rstrip() for line in str(t.get("text") or "").strip().splitlines()) in source_tables
                    and question_draft._source_table_rows(t, include_omitted=False) == block.get("rows")]
                if len(matches) != 1:
                    continue
                full = question_draft._source_table_rows(matches[0])
                if full != block["rows"]:
                    expected_blocks[position]["rows"] = full
                    changed = True
            if changed:
                if "materialBlocks" in old and "material_blocks" in old:
                    raise ValueError("Ambiguous table material aliases")
                source_path = extraction.get("source")
                if not isinstance(source_path, str) or not source_path:
                    raise ValueError("Word table grid needs original source binding")
                source_path = format_gate._safe(Path(source_path) if Path(source_path).is_absolute() else root / source_path)
                if (not source_path.is_file() or
                        format_gate._sha(source_path.read_bytes()) != extraction.get("source_sha256")):
                    raise ValueError("Word table grid extraction does not match current original source")
                current[field] = expected_blocks
                padded.add(index)
        for index, (old, current) in enumerate(zip(original["items"], candidate["items"])):
            if old == current:
                continue
            if index in padded:
                result["changes"].append({"file": relative, "item_index": index,
                    "unit_id": old.get("unit_id") or old.get("id"),
                    "numbers": list(legacy_format._key(old)),
                    "action": "restore_word_omitted_grid_columns"})
                continue
            unit = legacy_format._bound_unit(old, by_key, rebuilt)
            matches = re.findall(r"\[TABLE\s+\d+\](.*?)\[/TABLE\]", unit["stem"], re.S)
            if ("materialBlocks" in old and "material_blocks" in old or len(matches) != 1):
                raise ValueError("Ambiguous table source or material aliases")
            field = "materialBlocks" if old.get("materialBlocks") else "material_blocks"
            blocks = old.get(field)
            tables = [i for i, block in enumerate(blocks)
                      if isinstance(block, dict) and block.get("type") == "table"]
            if len(tables) != 1 or blocks[tables[0]].get("rows"):
                raise ValueError("Table target is not uniquely empty")
            rows = []
            for line in matches[0].splitlines():
                if not line.strip() or "|" not in line:
                    continue
                rows.append({"cells": [{"text": re.sub(r"[ \t]+", " ",
                    re.sub(r"<br\s*/?>", "\n", cell, flags=re.I)).strip(),
                    "items": []} for cell in line.strip().split("|")]})
            exact = deepcopy(old)
            exact[field][tables[0]]["rows"] = rows
            if not rows or _canonical(exact) != _canonical(current):
                raise ValueError("Table restoration changed fields beyond exact source rows")
            result["changes"].append({"file": relative, "item_index": index,
                "unit_id": old.get("unit_id") or old.get("id"),
                "numbers": list(legacy_format._key(old)), "action": "restore_source_table_rows"})
        after = question_content.audit_batch_source(root, candidate)
        result["checks"][relative] = {"source_before": before, "source_after": after,
                                      "restored_tables": count}
        if not after.get("passed"):
            result["refused"].append({"file": relative, "reason": "Table candidate source audit failed",
                                      "issues": after.get("issues", [])})
        if candidate != original:
            result["files"][relative] = candidate
    result["passed"] = not result["refused"] and bool(result["checks"])
    if not result["passed"]:
        result["files"] = {}
    return result


def _unique_source_part(source, number, expected, unit=None):
    """Bind a real printed question, independent of inherited group instructions.

    同步练习/课时材料每个大题题号都从 1 重排（Ⅰ.1 与 Ⅱ.1 都是 1），全卷按题号
    找“唯一”必然失败。唯一性只在单元证明的来源窗口（source_span）内成立，
    与硬规则 27 的 unit_id / 题号作用域一致；无 span 时退回全卷口径。
    """
    from .extract import _plain
    from .preview import _strong_number
    lines = source.splitlines()
    span = (unit or {}).get("source_span") or {}
    start_line, end_line = span.get("start_line"), span.get("end_line")
    if (type(start_line) is int and type(end_line) is int
            and 1 <= start_line <= end_line <= len(lines)):
        scoped_lines = lines[start_line - 1:end_line]
    else:
        scoped_lines = lines
    if sum(_strong_number(_plain(line).strip()) == number for line in scoped_lines) != 1:
        raise ValueError("Option/source question number is not unique in current text")
    actual = question_content.question_parts(scoped_lines, number)
    if (not actual["found"] or legacy_format._flat(actual["stem"]) != legacy_format._flat(expected["stem"])
            or [(o["label"], legacy_format._flat(o["text"])) for o in actual["options"]] !=
               [(o["label"], legacy_format._flat(o["text"])) for o in expected["options"]]):
        raise ValueError("Full source question differs from its bound unit")


def _restore_options(original, by_key, rebuilt, source):
    """Only extend an ordered prefix of uniquely bound, complete source options."""
    candidate, changes = deepcopy(original), []
    for index, row in enumerate(candidate["items"]):
        unit = legacy_format._bound_unit(row, by_key, rebuilt)
        audit = question_content.audit_source_unit(unit)
        if not audit["passed"]:
            raise ValueError("Option source is incomplete")
        if audit["shared_options"]:
            continue  # Shared-option repair is unsupported; final source audit still checks it.
        children = row.get("subquestions") if unit["unit_kind"] == "group" else [row]
        if (not isinstance(children, list) or len(children) != len(unit["numbers"])
                or sorted(c.get("number") for c in children) != sorted(unit["numbers"])):
            raise ValueError("Option owner numbers are missing, duplicated or ambiguous")
        for child in children:
            number = child["number"]
            expected = audit["questions"][number]["options"]
            current = child.get("options", [])
            if not isinstance(current, list):
                raise ValueError("Option target must be an array")
            actual = question_content._candidate_options(current)
            if [(o["label"], legacy_format._flat(o["text"])) for o in actual] == [
                    (o["label"], legacy_format._flat(o["text"])) for o in expected]:
                continue
            _unique_source_part(source, number, audit["questions"][number], unit)
            # Do not bless incomplete sources merely because their own count agrees.
            labels = list("ABC" if str(unit["type"]).startswith("听力") else "ABCD")
            if ([o["label"] for o in expected] != labels
                    or any(not legacy_format._flat(o["text"]) for o in expected)
                    or any(re.search(r"\[IMG\b|<img\b|!\[", o["text"], re.I) for o in expected)):
                raise ValueError("No complete supported text-only source option block")
            if len(actual) > len(expected) or [o["label"] for o in actual] != labels[:len(actual)]:
                raise ValueError("Candidate options are not an ordered source prefix")
            strings = all(isinstance(o, str) for o in current)
            dictionaries = bool(current) and all(isinstance(o, dict) for o in current)
            label_key = None
            if dictionaries:
                keys = [set(o) for o in current]
                if any(k not in ({"key", "text"}, {"label", "text"}) for k in keys) or any(k != keys[0] for k in keys):
                    raise ValueError("Option objects have ambiguous fields or attachments")
                label_key = "key" if "key" in keys[0] else "label"
            elif not strings:
                raise ValueError("Mixed option encodings cannot be restored losslessly")
            restored = deepcopy(current)
            for position, (old, full) in enumerate(zip(actual, expected)):
                before, after = legacy_format._flat(old["text"]), legacy_format._flat(full["text"])
                if before == after:
                    continue
                # Only the final retained option may itself have been cut short.
                if position != len(actual) - 1 or not after.startswith(before):
                    raise ValueError("Existing option differs from source beyond a terminal truncation")
                if strings:
                    restored[position] = f"{full['label']}. {full['text']}"
                else:
                    restored[position]["text"] = full["text"]
            for full in expected[len(actual):]:
                restored.append(f"{full['label']}. {full['text']}" if strings else
                                {label_key: full["label"], "text": full["text"]})
            child["options"] = restored
            changes.append({"item_index": index, "unit_id": row.get("unit_id") or row.get("id"),
                "numbers": list(legacy_format._key(row)), "number": number,
                "action": "restore_truncated_source_options", "before": deepcopy(current),
                "after": deepcopy(restored), "evidence": {"source_options": expected,
                    "source_occurrences": 1, "source_unit": unit["stem"]}})
    return candidate, changes


def _prepare_options(root):
    result = {"passed": False, "files": {}, "changes": [], "refused": [],
              "input_sha256": {}, "checks": {}}
    source = _path(root, legacy_format.SOURCE_PATHS[0]).read_text(encoding="utf-8-sig")
    structure, confirmed = (format_gate._load(_path(root, rel).read_bytes())
                            for rel in legacy_format.SOURCE_PATHS[1:])
    by_key, rebuilt, cleaned = legacy_format._source_units(confirmed, structure, source)
    for relative in (*legacy_format.SOURCE_PATHS, *legacy_format.BATCH_PATHS):
        raw = _raw(root, relative)
        if raw is not None:
            result["input_sha256"][relative] = format_gate._sha(raw)
    for relative in legacy_format.BATCH_PATHS:
        raw = _raw(root, relative)
        if raw is None:
            continue
        original = format_gate._load(raw)
        before = question_content.audit_batch_source(root, original)
        candidate, changes = _restore_options(original, by_key, rebuilt, cleaned)
        after = question_content.audit_batch_source(root, candidate)
        result["checks"][relative] = {"source_before": before, "source_after": after}
        if not after.get("passed"):
            result["refused"].append({"file": relative, "reason": "Option candidate source audit failed",
                                      "issues": after.get("issues", [])})
        if changes:
            result["files"][relative] = candidate
            result["changes"].extend({"file": relative, **change} for change in changes)
    result["passed"] = not result["refused"] and bool(result["checks"])
    if not result["passed"]:
        result["files"] = {}
    return result


def knowledge_repair_targets(batch_dir):
    """Find current illegal knowledge owners; never choose replacement labels.

    The knowledge stage runs before ``assemble``, so ``06_batch/batch_meta.json``
    does not exist yet when its output first fails validation. Requiring the final
    artifact here made every knowledge-stage self-heal crash with
    ``KNOWLEDGE_REPAIR_FAILED: batch_meta.json missing``. When the final batch is
    absent we validate the current knowledge artifact instead; with neither we
    return no targets (the normal stage retry still runs with its error feedback)
    rather than raising.
    """
    from .agent_tasks import ARTIFACTS, confirmed_units
    from workbench import taxonomy
    root = format_gate._safe(batch_dir)
    final = _path(root, format_gate.FINAL_PATH)
    knowledge_path = _path(root, "04_knowledge/knowledge.json")
    if not final.is_file():
        if not knowledge_path.is_file():
            return {}
        payload = format_gate._load(knowledge_path.read_bytes())
        types = {row.get("unit_id"): row for row in
                 (format_gate._load(_path(root, ARTIFACTS["types"]).read_bytes()).get("items") or [])}
        units = {unit["unit_id"]: unit for unit in confirmed_units(root)}
        targets = {}
        for item in payload.get("items") or []:
            unit_id = item.get("unit_id")
            meta, unit = types.get(unit_id) or {}, units.get(unit_id) or {}
            problems = taxonomy.check_question_labels(dict(
                item, type=meta.get("type"), subtypes=meta.get("subtypes"),
                unit_kind=unit.get("unit_kind")))
            if problems:
                targets[unit_id] = list(dict.fromkeys(problems))
        return targets
    payload = format_gate._load(final.read_bytes())
    audit = question_content.audit_batch_source(root, payload)
    if not audit.get("passed"):
        raise ValueError("Knowledge rerun requires a complete source-proven batch")
    source = _path(root, legacy_format.SOURCE_PATHS[0]).read_text(encoding="utf-8-sig")
    structure, confirmed = (format_gate._load(_path(root, rel).read_bytes())
                            for rel in legacy_format.SOURCE_PATHS[1:])
    by_key, rebuilt, cleaned = legacy_format._source_units(confirmed, structure, source)
    stable = {legacy_format._key(unit): unit["unit_id"] for unit in confirmed_units(root)}
    targets = {}
    for row in payload["items"]:
        unit = legacy_format._bound_unit(row, by_key, rebuilt)
        problems = []
        owners = [(row, False)] + [(child, True) for child in row.get("subquestions", [])]
        for owner, child in owners:
            paths = owner.get("knowledge", [])
            problems.extend(taxonomy.check_labels({"type": unit["type"], "knowledge": paths}))
            if not child and not paths:
                problems.append("knowledge 必须是非空目录叶子路径数组")
            if not child and unit["unit_kind"] == "group":
                problems.extend(taxonomy.check_unit_dims(unit["type"], paths))
        if problems:
            parts = question_content.audit_source_unit(unit)["questions"]
            for number, part in parts.items():
                _unique_source_part(cleaned, number, part)
            targets[stable[legacy_format._key(row)]] = list(dict.fromkeys(problems))
    return targets


def schedule_knowledge_repair(batch_dir, *, expected_inputs=None, reason="目录值非法，按原文局部重新判定", max_attempts=3):
    """Atomically queue real knowledge work with local feedback and stale reviews.

    This installs only control data, not model output or a completed stage. The
    normal knowledge agent/import path must execute and validate the candidates.
    """
    root = format_gate._safe(batch_dir)
    result = {"action": "rejudge_knowledge", "passed": False, "installed": False,
              "status": "refused", "verified": False, "quality_passed": False,
              "target_stage": "knowledge", "refused": [], "errors": [], "receipt_path": None}
    written, snapshots, expected, receipt, lock = [], {}, {}, None, None
    feedback_path, budget_path = "workflow/repair-feedback/knowledge.json", "workflow/knowledge-repair.json"
    try:
        if any(p.lower() in {".workbench-trash", ".trash", "archive", "archives", "archived", "归档"} for p in root.parts):
            raise ValueError("Knowledge repair requires a current non-archived batch")
        _check_expected(root, expected_inputs)
        sources, rules = _source_hashes(root), _rule_hashes()
        watched = set((*legacy_format.BATCH_PATHS, *CONTROL_PATHS, feedback_path, budget_path,
                       "04_knowledge/knowledge.json", "03_types/types.json"))
        snapshots = {rel: _raw(root, rel) for rel in watched}
        expected = dict(snapshots)
        for key in expected_inputs or {}:
            path = format_gate._safe(Path(key) if Path(key).is_absolute() else root / key)
            label = path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)
            expected.setdefault(label, path.read_bytes() if path.is_file() else None)
        targets = knowledge_repair_targets(root)
        _assert_snapshot(root, expected)
        _check_sources(root, sources, rules)
        if not targets:
            result.update(status="noop", passed=True)
            return result
        result["input_sha256"] = {rel: format_gate._sha(raw) if raw is not None else "missing"
                                   for rel, raw in expected.items()}
        manifest = format_gate._load(snapshots["workflow/manifest.json"])
        if "knowledge" not in manifest.get("stages", {}):
            raise ValueError("Knowledge stage is not registered")
        _assert_snapshot(root, expected)
        _check_sources(root, sources, rules)
        _check_expected(root, expected_inputs)
        signature = format_gate._sha(_canonical({"source": sources, "rules": rules, "targets": targets}))
        budget = format_gate._load(snapshots[budget_path]) if snapshots[budget_path] else {"attempts": {}}
        counts = budget["attempts"]
        attempt = int(counts.get(signature, 0)) + 1
        if attempt > max_attempts:
            result.update(status="retry_budget_exhausted", unit_ids=list(targets))
            result["refused"].append({"reason": "同一来源、目录与非法知识点已达到局部重判上限"})
            return result
        reason = str(reason).strip()[:500] or "目录值非法，按原文局部重新判定"
        feedback = {"version": 1, "stage": "knowledge", "reason": reason,
            "error": "目录检查发现非法知识点；只重新判断列出的单元，不能猜值或重写题文。",
            "unit_ids": list(targets), "all_units": False, "scoped_units": True, "attempt": attempt,
            "feedback_by_unit": {uid: {"reason": reason, "error": "; ".join(errors)[:6000]}
                                 for uid, errors in targets.items()}}
        invalidated = []
        for target in ("knowledge", *contracts.downstream_of("knowledge")):
            invalidated.extend(mf.invalidate_stage(root, manifest, target,
                "Illegal knowledge labels require a real local agent rejudgment"))
        mf.touch_stage(manifest, "knowledge", mf.PENDING, notes=reason)
        counts[signature] = attempt
        budget["last_signature"] = signature
        updates = {feedback_path: _canonical(feedback), budget_path: _canonical(budget),
                   "workflow/manifest.json": _canonical(manifest)}
        delivery = _refresh_delivery(root, manifest, [], invalidated)
        if delivery is not None:
            updates["workflow/delivery.json"] = delivery
        lock = _path(root, "workflow/.source-format-repair.lock")
        try:
            fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            lock = None
            raise ValueError("Another repair holds the batch lock")
        os.close(fd)
        _assert_snapshot(root, expected)
        _check_sources(root, sources, rules)
        _check_expected(root, expected_inputs)
        directory = f"{MIGRATIONS}/{uuid.uuid4().hex}"
        journal = format_gate._safe(root / directory)
        journal.mkdir(parents=True, mode=0o700)
        os.chmod(journal, 0o700)
        backups = {}
        for rel in updates:
            if snapshots[rel] is not None:
                backups[rel] = f"{directory}/original/{rel}"
                _atomic_raw(root, backups[rel], snapshots[rel])
            _atomic_raw(root, f"{directory}/candidates/{rel}", updates[rel])
        result["receipt_path"] = f"{directory}/receipt.json"
        receipt = {"version": VERSION, "rule": "knowledge-local-rejudgment-v1", "status": "prepared",
            "passed": False, "source_sha256": sources, "rule_sha256": rules,
            "input_sha256": result["input_sha256"],
            "rollback_backups": backups, "unit_ids": list(targets), "attempt": attempt,
            "files": {rel: {"before_sha256": format_gate._sha(snapshots[rel]) if snapshots[rel] else "missing",
                            "after_sha256": format_gate._sha(raw)} for rel, raw in updates.items()},
            "quality_passed": False, "agent_execution_required": True}
        _atomic_raw(root, result["receipt_path"], _canonical(_seal_receipt(receipt)))
        for rel, raw in updates.items():
            _assert_snapshot(root, expected)
            _check_sources(root, sources, rules)
            _atomic_raw(root, rel, raw)
            written.append(rel)
            expected[rel] = raw
        _assert_snapshot(root, expected)
        _check_sources(root, sources, rules)
        receipt.update(status="scheduled", passed=True, invalidated_stages=invalidated)
        _atomic_raw(root, result["receipt_path"], _canonical(_seal_receipt(receipt)))
        _assert_snapshot(root, expected)
        _check_sources(root, sources, rules)
        if _raw(root, result["receipt_path"]) != _canonical(receipt):
            raise ValueError("Knowledge scheduling receipt changed")
        result.update(passed=True, installed=True, status="scheduled", feedback_path=feedback_path,
                      unit_ids=list(targets), invalidated_stages=invalidated, attempt=attempt)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        result["errors"].append({"code": "KNOWLEDGE_REPAIR_FAILED", "message": str(exc)})
        rollback_errors = []
        for rel in reversed(written):
            try:
                if _raw(root, rel) != expected[rel]:
                    raise ValueError("Concurrent edit prevents rollback: " + rel)
                if snapshots[rel] is None:
                    _path(root, rel).unlink()
                else:
                    _atomic_raw(root, rel, snapshots[rel])
            except (OSError, ValueError) as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        if written:
            result.update(status="rollback_failed" if rollback_errors else "rolled_back",
                          rollback_complete=not rollback_errors)
        result["errors"].extend({"code": "ROLLBACK_FAILED", "message": error} for error in rollback_errors)
        if receipt is not None:
            receipt.update(status=result["status"], passed=False, errors=result["errors"])
            try:
                _atomic_raw(root, result["receipt_path"], _canonical(_seal_receipt(receipt)))
            except (OSError, ValueError) as journal_exc:
                result["errors"].append({"code": "RECEIPT_WRITE_FAILED", "message": str(journal_exc)})
    finally:
        if lock is not None:
            try:
                lock.unlink()
            except OSError as lock_exc:
                result["errors"].append({"code": "LOCK_RELEASE_FAILED", "message": str(lock_exc)})
    return result


def apply_source_option_repair(batch_dir, *, expected_inputs=None) -> dict:
    """Restore only source-proven option prefixes via the existing transaction."""
    return _apply(batch_dir, expected_inputs=expected_inputs, options=True)


def apply_source_format_repair(batch_dir, *, expected_inputs=None) -> dict:
    """Apply only source-proven legacy format normalization to this batch."""
    return _apply(batch_dir, expected_inputs=expected_inputs)


def apply_source_table_repair(batch_dir, expected_inputs=None) -> dict:
    """Restore source-proven table rows; unrelated format errors remain errors."""
    return _apply(batch_dir, expected_inputs=expected_inputs, tables=True)


def _apply(batch_dir, *, expected_inputs=None, tables=False, options=False) -> dict:
    """Install proven candidates, validate, and commit or restore original bytes.

    expected_inputs maps relative/absolute paths to raw SHA256 or "missing".
    passed certifies this local repair, never quality. For table restoration,
    format_passed separately reports remaining format defects. No-op creates
    neither a migration nor a report.
    """
    result = {"action": "repair_source_options" if options else "repair_source_tables" if tables else "repair_upload_format", "version": VERSION,
              "passed": False, "installed": False, "status": "refused",
              "changes": [], "changed_files": [], "input_sha256": {},
              "before_sha256": {}, "after_sha256": {}, "receipt_path": None,
              "checks": {}, "format_passed": None, "refreshed_stages": [], "invalidated_stages": [],
              "refused": [], "errors": [], "quality_passed": False,
              "prepare_upload_required": True}
    lock = None
    receipt = None
    snapshots, expected, written = {}, {}, []
    try:
        root = format_gate._safe(batch_dir)
        if not root.is_dir() or any(p.lower() in {".workbench-trash", ".trash",
                "archive", "archives", "archived", "归档"} for p in root.parts):
            raise ValueError("Repair requires a current non-archived batch directory")
        _check_expected(root, expected_inputs)
        sources, rules = _source_hashes(root), _rule_hashes()
        snapshots = {relative: _raw(root, relative)
                     for relative in (*legacy_format.BATCH_PATHS, *CONTROL_PATHS)}
        expected = dict(snapshots)
        # Watch every explicitly bound input and every producer dependency, not
        # just the three output files that this action will replace.
        for key in expected_inputs or {}:
            path = format_gate._safe(Path(key) if Path(key).is_absolute() else root / key)
            label = path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)
            expected.setdefault(label, path.read_bytes() if path.is_file() else None)
        for stage_id in ("assemble", "meta", "underlines"):
            for dependency in contracts.STAGE_BY_ID[stage_id].depends_on:
                for relative in contracts.STAGE_BY_ID[dependency].produces:
                    expected.setdefault(relative, _raw(root, relative))
        # A managed state must already be valid; never adopt an unmanaged batch.
        managed, _ = batch_fill._managed(root)
        prepared = _prepare_options(root) if options else _prepare_tables(root) if tables else legacy_format.prepare(root)
        result.update(changes=prepared.get("changes", []),
                      input_sha256=prepared.get("input_sha256", {}),
                      checks={"prepare": prepared.get("checks", {})},
                      refused=prepared.get("refused", []))
        _assert_snapshot(root, expected)
        _check_sources(root, sources, rules)
        _check_expected(root, expected_inputs)
        if not prepared.get("passed"):
            return result
        files = prepared.get("files")
        if not prepared.get("changes"):
            if files:
                raise ValueError("Candidate files without proven changes")
            result.update(passed=True, status="noop",
                          format_passed=True if not (tables or options) else None)
            return result
        if not files or not set(files) <= set(legacy_format.BATCH_PATHS):
            raise ValueError("Invalid canonical repair candidates")
        canonical = {relative: _canonical(value) for relative, value in files.items()}
        # Independently replay prepare before installing any bytes.
        source = _path(root, legacy_format.SOURCE_PATHS[0]).read_text(encoding="utf-8-sig")
        structure, confirmed = (format_gate._load(_path(root, rel).read_bytes())
                                for rel in legacy_format.SOURCE_PATHS[1:])
        for relative, raw in (() if tables else canonical.items()):
            if options:
                by_key, rebuilt, cleaned = legacy_format._source_units(confirmed, structure, source)
                candidate, changes = _restore_options(format_gate._load(snapshots[relative]), by_key, rebuilt, cleaned)
                replay = {"passed": True, "candidate": candidate, "changes": changes}
            else:
                replay = legacy_format.normalize_candidate(format_gate._load(snapshots[relative]),
                    source=source, structure=structure, confirmed=confirmed)
            if (not replay["passed"] or not replay["changes"]
                    or _canonical(replay["candidate"]) != raw):
                raise ValueError("Prepared candidate differs from source-proven replay")
        workflow = format_gate._safe(root / "workflow")
        workflow.mkdir(parents=True, exist_ok=True)
        lock = _path(root, "workflow/.source-format-repair.lock")
        try:
            fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            lock = None
            raise ValueError("Another format repair holds the batch lock")
        with os.fdopen(fd, "w") as stream:
            stream.write(str(os.getpid()))
        _assert_snapshot(root, expected)
        _check_sources(root, sources, rules)
        _check_expected(root, expected_inputs)
        identity = uuid.uuid4().hex
        directory = f"{MIGRATIONS}/{identity}"
        journal = format_gate._safe(root / directory)
        journal.mkdir(parents=True, mode=0o700)
        os.chmod(journal, 0o700)
        backups = {}
        for relative, raw in snapshots.items():
            if raw is not None:
                backup = f"{directory}/original/{relative}"
                _atomic_raw(root, backup, raw)
                backups[relative] = backup
        for relative, raw in canonical.items():
            _atomic_raw(root, f"{directory}/candidates/{relative}", raw)
        result["before_sha256"] = {rel: format_gate._sha(snapshots[rel]) for rel in canonical}
        result["after_sha256"] = {rel: format_gate._sha(raw) for rel, raw in canonical.items()}
        result["receipt_path"] = f"{directory}/receipt.json"
        receipt = {"version": VERSION, "rule": "source-option-prefix-v1" if options else "source-table-rows-v1" if tables else legacy_format.RULE, "migration_id": identity,
            "status": "prepared", "passed": False, "created_at": mf.now_iso(),
            "source_sha256": sources, "rule_sha256": rules,
            "input_sha256": result["input_sha256"], "changes": result["changes"],
            "files": {rel: {"before_sha256": result["before_sha256"][rel],
                "after_sha256": result["after_sha256"][rel], "backup_path": backups[rel],
                "candidate_path": f"{directory}/candidates/{rel}"} for rel in canonical},
            "rollback_backups": backups, "quality_passed": False,
            "prepare_upload_required": True}
        _atomic_raw(root, result["receipt_path"], _canonical(_seal_receipt(receipt)))
        for relative, raw in canonical.items():
            _assert_snapshot(root, expected)
            _check_sources(root, sources, rules)
            _atomic_raw(root, relative, raw)
            written.append(relative)
            expected[relative] = raw
        audits = {}
        for relative in legacy_format.BATCH_PATHS:
            if (tables or options) and _raw(root, relative) is None:
                continue
            audit = question_content.audit_batch_source(root,
                format_gate._load(_path(root, relative).read_bytes()))
            audits[relative] = audit
        result["checks"]["source_audit"] = audits
        if not all(audit.get("passed") for audit in audits.values()):
            raise ValueError("Installed source audit failed")
        _assert_snapshot(root, expected)
        _check_sources(root, sources, rules)
        # run() performs the real format checks and writes its real report.
        try:
            report = format_gate.run(root)
        finally:
            if _raw(root, format_gate.REPORT_PATH) != snapshots[format_gate.REPORT_PATH]:
                written.append(format_gate.REPORT_PATH)
                expected[format_gate.REPORT_PATH] = _raw(root, format_gate.REPORT_PATH)
        result["checks"]["format"] = report
        result["format_passed"] = bool(report.get("passed"))
        if not (tables or options) and not report.get("passed"):
            raise ValueError("Installed local format check failed")
        _assert_snapshot(root, expected)
        _check_sources(root, sources, rules)
        if managed is not None:
            try:
                state_result = batch_fill.mark_finalized(root)
                result["batch_fill_status"] = state_result["status"]
                batch_fill._managed(root)  # Read back the sealed ownership/body.
            finally:
                for rel in (batch_fill.STATE, question_draft.PATH):
                    if _raw(root, rel) != snapshots[rel]:
                        written.append(rel)
                        expected[rel] = _raw(root, rel)
        else:
            result["batch_fill_status"] = "unmanaged"
        _assert_snapshot(root, expected)
        _check_sources(root, sources, rules)
        if snapshots["workflow/manifest.json"] is not None:
            manifest = format_gate._load(snapshots["workflow/manifest.json"])
            refreshed, invalidated = _refresh_manifest(root, manifest,
                preserve_validated_format=not (tables or options) and bool(result.get("format_passed")),
                receipt_path=result["receipt_path"])
            if options:
                # Added source content changes what reviewers saw. This is never
                # a format-equivalence migration, even when local format passes.
                for target in contracts.downstream_of("validate"):
                    invalidated.extend(mf.invalidate_stage(root, manifest, target,
                        "Source options restored; prior review and authorization are stale"))
            invalidated = list(dict.fromkeys(invalidated))
            delivery = _refresh_delivery(root, manifest, refreshed, invalidated)
            manifest["updated_at"] = mf.now_iso()
            updates = {"workflow/manifest.json": _canonical(manifest)}
            if delivery is not None:
                updates["workflow/delivery.json"] = delivery
            for relative, raw in updates.items():
                _assert_snapshot(root, expected)
                _check_sources(root, sources, rules)
                _atomic_raw(root, relative, raw)
                written.append(relative)
                expected[relative] = raw
            result.update(refreshed_stages=refreshed, invalidated_stages=invalidated)
        _assert_snapshot(root, expected)
        _check_sources(root, sources, rules)
        receipt.update(status="applied", passed=True, finished_at=mf.now_iso(),
                       checks=result["checks"], refreshed_stages=result["refreshed_stages"],
                       invalidated_stages=result["invalidated_stages"],
                       batch_fill_status=result["batch_fill_status"])
        _atomic_raw(root, result["receipt_path"], _canonical(_seal_receipt(receipt)))
        _assert_snapshot(root, expected)
        _check_sources(root, sources, rules)
        if _raw(root, result["receipt_path"]) != _canonical(receipt):
            raise ValueError("Committed receipt changed during repair")
        result.update(passed=True, installed=True, status="applied",
                      changed_files=sorted(canonical))
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        result["errors"].append({"code": "FORMAT_REPAIR_FAILED", "message": str(exc)})
        rollback_errors = []
        for relative in reversed(list(dict.fromkeys(written))):
            try:
                # Never overwrite somebody else's concurrent edit during rollback.
                if _raw(root, relative) != expected[relative]:
                    raise ValueError("Concurrent edit prevents rollback: " + relative)
                if snapshots[relative] is None:
                    path = _path(root, relative)
                    if path.exists():
                        path.unlink()
                else:
                    _atomic_raw(root, relative, snapshots[relative])
                if _raw(root, relative) != snapshots[relative]:
                    raise OSError("Restored raw bytes do not match original: " + relative)
            except (OSError, ValueError) as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        if written:
            result.pop("batch_fill_status", None)
            result.update(status="rolled_back" if not rollback_errors else "rollback_failed",
                          rollback_complete=not rollback_errors, format_passed=None,
                          refreshed_stages=[], invalidated_stages=[])
        result["errors"].extend({"code": "ROLLBACK_FAILED", "message": message}
                                for message in rollback_errors)
        if receipt is not None:
            receipt.update(status=result["status"], passed=False, errors=result["errors"],
                           finished_at=mf.now_iso())
            try:
                _atomic_raw(root, result["receipt_path"], _canonical(_seal_receipt(receipt)))
            except (OSError, ValueError) as journal_exc:
                result["errors"].append({"code": "RECEIPT_WRITE_FAILED", "message": str(journal_exc)})
    finally:
        if lock is not None:
            try:
                lock.unlink()
            except OSError as exc:
                result["errors"].append({"code": "LOCK_RELEASE_FAILED", "message": str(exc)})
    return result
