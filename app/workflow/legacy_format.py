"""Read-only, source-proven encoding candidates for legacy upload batches.

This module never installs candidates, changes tags, calls an agent, or writes
reports. ``passed`` means the candidates passed source checks and the final
batch passed the existing *local* format checks; it is not review/quality,
media-deployment, manifest-registration, or upload approval.
"""
from __future__ import annotations

from copy import deepcopy
import re
from pathlib import Path

from . import denoise, format_gate, question_content, structure_gate, upload_payload

BATCH_PATHS = tuple("06_batch/" + name for name in
                    ("batch.json", "batch_meta.base.json", "batch_meta.json"))
SOURCE_PATHS = ("01_source/正文.txt", "01_source/structure.json",
                "01_source/structure.confirmed.json")
RULE = "legacy-source-proven-format-v1"


def _flat(text):
    # Only layout whitespace and a literal line-break tag are interchangeable.
    # Do not erase punctuation, case, underlining, images, or other markup.
    return re.sub(r"\s+", " ", re.sub(r"<br\s*/?>", "\n", text)).strip()


def _key(row):
    numbers = row.get("numbers")
    if (not isinstance(numbers, list) or not numbers
            or any(type(n) is not int or n <= 0 for n in numbers)):
        raise ValueError("单元缺少可靠正整数题号数组")
    return tuple(numbers)


def _identity_key(obj):
    """跨大题重号时的唯一身份键：题号作用域 + 题号数组。

    同步练习/课时材料每个大写罗马数字大题题号都从 1 重新开始（如 Ⅰ.1 与 Ⅱ.1
    都是 (1,)），只按题号做键会把不同大题的同一题号误判为「来源单元题号重复」。
    section_id 由 sync_practice 依来源位置确定性生成（不是印刷题号），候选行、
    确认单元与原文重建单元三侧都携带，可作题号作用域。缺失 section_id 时退回
    纯题号键，保持旧行为与来源可证明边界。
    """
    section = obj.get("section_id")
    scope = section if isinstance(section, str) and section else None
    return (scope, _key(obj))


def _source_units(confirmed, structure, source):
    """Require the edited units to match deterministic reconstruction exactly.

    AI-only or otherwise unprovable grouping is intentionally unsupported here;
    the normal source audit still runs for every unit before and after editing.
    """
    cleaned = denoise.clean_body(source)[0]
    fresh = structure_gate._build_units(structure.get("sections") or [],
                                       structure.get("groups") or [], cleaned.splitlines())
    confirmed_units = confirmed.get("units")
    if not isinstance(confirmed_units, list) or not confirmed_units:
        raise ValueError("确认结构缺少 units")
    by_key, rebuilt = {}, {}
    for units, target in ((confirmed_units, by_key), (fresh, rebuilt)):
        for unit in units:
            key = _identity_key(unit)
            if key in target:
                raise ValueError(f"来源单元题号重复：{key[1]}（section {key[0]}）")
            target[key] = unit
    return by_key, rebuilt, cleaned


def _bound_unit(row, confirmed, rebuilt):
    key = _identity_key(row)
    unit, fresh = confirmed.get(key), rebuilt.get(key)
    if not unit or not fresh:
        raise ValueError("确认结构与原文重建不能唯一绑定本单元")
    if (unit.get("stem") != fresh.get("stem") or unit.get("type") != fresh.get("type")
            or unit.get("unit_kind") != fresh.get("unit_kind")):
        raise ValueError("确认结构与当前原文重建不同；需正式结构核验")
    if (row.get("unit_kind") != unit.get("unit_kind")
            or row.get("question_type", row.get("type")) != unit.get("type")):
        raise ValueError("候选题型或单元身份与确认源不同")
    return unit


def _paragraphs(row, unit, field):
    blocks = row.get(field)
    if not isinstance(blocks, list):
        raise ValueError(f"{field} 不是数组")
    keep, proofs = [], []
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            raise ValueError("材料块不是对象")
        if block.get("type") == "table":
            keep.append(block)  # Never rebuild tables or their attachments.
            continue
        if block.get("type") not in {"paragraph", "text"}:
            raise ValueError("不支持的非表格块；保留以免丢附件或内容")
        if set(block) - {"type", "rows", "text", "content", "value"}:
            raise ValueError("非表格块含未知字段/附件，不能无损删除")
        if "rows" in block and block["rows"] != []:
            raise ValueError("非表格块含非空或无效 rows，不能无损删除")
        texts = []
        for name in ("text", "content", "value"):
            if name in block:
                if not isinstance(block[name], str):
                    raise ValueError("非表格块文本不是字符串")
                if _flat(block[name]):
                    texts.append(block[name])
        if len(texts) > 1:
            raise ValueError("非表格块有多个文本载体，归属不明确")
        proof = {"field": field, "block_index": index, "before": deepcopy(block)}
        if not texts:
            proof.update(action="remove_empty_block", evidence="仅含已识别字段，文本及 rows 均为空")
        else:
            text = _flat(texts[0])
            hits = []
            for owner in ("material", "stem"):
                value = row.get(owner, "")
                if not isinstance(value, str):
                    raise ValueError("主文本不是字符串")
                count = _flat(value).count(text)
                hits.extend([owner] * count)
            if len(hits) != 1 or _flat(unit["stem"]).count(text) != 1:
                # Missing prose is never silently dropped or appended in a guessed
                # order. A dedicated source-bound merge may be added separately.
                raise ValueError("非表格块文字未唯一完整存在 material/stem 及确认源；拒绝丢弃或猜测合并")
            proof.update(action="remove_duplicate_block", text=texts[0],
                         evidence={"unique_owner": hits[0], "confirmed_source_occurrences": 1})
        proofs.append(proof)
    return keep, proofs


def _printed_number(row, unit, source):
    number = row.get("number")
    if (row.get("unit_kind") != "standalone" or _key(row) != (number,)
            or unit.get("type") != "听力关键词语选择"):
        raise ValueError("空题干不属于可证明的独立听力关键词语选择题")
    text = unit.get("stem")
    if not isinstance(text, str) or len(text.splitlines()) != 1:
        raise ValueError("印刷题号与选项未在单行完整绑定")
    # This requires the actual printed token, not a generated instruction.
    match = re.match(r"^\s*(\d{1,3}\s*[.．、)])\s*(?=A[.．、])", text)
    if not match or int(re.match(r"\d+", match.group(1)).group()) != number:
        raise ValueError("原文不是题号直接连接 A 选项；可能确实缺失问句")
    lines = source.splitlines()
    if lines.count(text) != 1:
        raise ValueError("原文题号行不存在或重复，不能唯一绑定")
    from .extract import _plain
    from .preview import SECTION
    preceding = [_plain(line).strip() for line in lines[:lines.index(text)]]
    headings = [line for line in preceding if SECTION.match(line)]
    if not headings or "关键词语选择" not in headings[-1]:
        raise ValueError("原卷实际章节不能证明本题属于关键词语选择")
    audit = question_content.audit_source_unit(unit)
    part = audit.get("questions", {}).get(number, {})
    if (not audit["passed"] or not part.get("found") or part.get("stem") != ""
            or [o["label"] for o in part.get("options", [])] != list("ABC")):
        raise ValueError("原文无问句与完整 A/B/C 选项未获共同证明")
    if any(not _flat(o["text"]) for o in part["options"]):
        raise ValueError("原文选项为空")
    options = row.get("options")
    if not isinstance(options, list) or len(options) != 3 or any(not isinstance(o, str) for o in options):
        raise ValueError("候选选项不完整")
    actual = question_content._candidate_options(options)
    if [(o["label"], _flat(o["text"])) for o in actual] != [
            (o["label"], _flat(o["text"])) for o in part["options"]]:
        raise ValueError("候选选项与原文不逐字一致（仅允许排版空白差异）")
    return match.group(1), {"source_line": text, "source_line_number": lines.index(text) + 1,
                            "source_section": headings[-1], "source_occurrences": 1,
                            "source_stem": "", "option_labels": list("ABC")}


def _local_format(root, batch):
    report = {"input_sha256": {}}
    options = format_gate._options(root, batch, report)
    built = upload_payload.build_payload(batch, catalog=None, **options)
    errors = format_gate._shape(batch) + [e for e in built["errors"]
                                            if e.get("code") not in format_gate.DEFERRED_CODES]
    return {"passed": not errors, "errors": errors, "warnings": built["warnings"],
            "deferred": [e for e in built["errors"] if e.get("code") in format_gate.DEFERRED_CODES],
            "scope": "local_upload_format", "prepare_upload_required": True}


def normalize_candidate(original, *, source, structure, confirmed) -> dict:
    """Pure replay of the existing source-proven row normalization.

    The caller supplies real source snapshots, not receipt claims. This performs
    no filesystem access or quality approval; prepare() remains unchanged.
    A failed proof returns no candidate, including when earlier rows were safe.
    """
    result = {"rule": RULE, "passed": False, "candidate": None,
              "changes": [], "refused": []}
    try:
        if not isinstance(source, str):
            raise ValueError("Source text must be a string")
        if not isinstance(original, dict) or not isinstance(original.get("items"), list):
            raise ValueError("Candidate requires an items array")
        by_key, rebuilt, _ = _source_units(confirmed, structure, source)
        candidate = deepcopy(original)
        for index, row in enumerate(candidate["items"]):
            fields = []
            for field in ("materialBlocks", "material_blocks"):
                if field in row:
                    blocks = row[field]
                    if not isinstance(blocks, list):
                        raise ValueError("材料块字段不是数组")
                    if any(not isinstance(b, dict) or b.get("type") != "table" for b in blocks):
                        fields.append(field)
            empty_stem = row.get("stem") == ""
            if not fields and not empty_stem:
                continue
            unit = _bound_unit(row, by_key, rebuilt)
            if fields and "materialBlocks" in row and "material_blocks" in row:
                raise ValueError("两种材料字段同时存在，拒绝猜测显示优先级")
            edits = []
            for field in fields:
                row[field], proofs = _paragraphs(row, unit, field)
                edits.extend(proofs)
            if empty_stem:
                token, proof = _printed_number(row, unit, source)
                row["stem"] = token
                edits.append({"field": "stem", "action": "retain_printed_number",
                              "before": "", "after": token, "evidence": proof})
            result["changes"].extend({"item_index": index,
                "unit_id": row.get("unit_id") or row.get("id"),
                "numbers": list(_key(row)), **edit} for edit in edits)
        result.update(passed=True, candidate=candidate)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        result["refused"].append({"reason": str(exc)})
    return result


def prepare(batch_dir) -> dict:
    """Return changed payloads and exact evidence; perform no filesystem writes.

    ``files`` is an in-memory mapping of batch-relative JSON paths to payloads.
    On any failed proof/check it is empty, even if other proposals were safe.
    The caller must recheck input hashes, validate/install atomically through the
    existing workflow, and register the manifest. No input status is altered.
    """
    result = {"rule": RULE, "passed": False, "installed": False, "files": {},
              "changes": [], "refused": [], "input_sha256": {},
              "checks": {}, "quality_passed": False, "prepare_upload_required": True}
    snapshots, candidates = {}, {}
    try:
        root = format_gate._safe(batch_dir)
        paths = (*SOURCE_PATHS, *BATCH_PATHS)
        manifest = "workflow/manifest.json"
        if format_gate._safe(root / manifest).exists():
            paths += (manifest,)
        for relative in paths:
            snapshots[relative] = format_gate._safe(root / relative).read_bytes()
        result["input_sha256"] = {p: format_gate._sha(raw) for p, raw in snapshots.items()}
        source = snapshots[SOURCE_PATHS[0]].decode("utf-8-sig")
        structure = format_gate._load(snapshots[SOURCE_PATHS[1]])
        confirmed = format_gate._load(snapshots[SOURCE_PATHS[2]])
        by_key, rebuilt, _ = _source_units(confirmed, structure, source)
        for relative in BATCH_PATHS:
            original = format_gate._load(snapshots[relative])
            if not isinstance(original, dict) or not isinstance(original.get("items"), list):
                raise ValueError(f"{relative} 缺少 items 数组")
            before = question_content.audit_batch_source(root, original)
            result["checks"][relative] = {"source_before": before}
            if not before["passed"]:
                result["refused"].append({"file": relative, "reason": "原批次源头审计未通过",
                                           "issues": before["issues"]})
                continue
            candidate = deepcopy(original)
            file_changes = []
            for index, row in enumerate(candidate["items"]):
                needs_blocks = []
                for field in ("materialBlocks", "material_blocks"):
                    if field not in row:
                        continue
                    blocks = row[field]
                    if not isinstance(blocks, list):
                        needs_blocks.append("invalid")
                    elif any(not isinstance(b, dict) or b.get("type") != "table" for b in blocks):
                        needs_blocks.append(field)
                empty_stem = row.get("stem") == ""
                if not needs_blocks and not empty_stem:
                    continue
                uid = row.get("unit_id") or row.get("id")
                try:
                    unit = _bound_unit(row, by_key, rebuilt)
                    # Both aliases present would make renderer precedence ambiguous.
                    if needs_blocks and "materialBlocks" in row and "material_blocks" in row:
                        raise ValueError("两种材料字段同时存在，拒绝猜测显示优先级")
                    edits = []
                    for field in needs_blocks:
                        if field == "invalid":
                            raise ValueError("材料块字段不是数组")
                        blocks, proofs = _paragraphs(row, unit, field)
                        row[field] = blocks
                        edits.extend(proofs)
                    if empty_stem:
                        token, proof = _printed_number(row, unit, source)
                        row["stem"] = token
                        edits.append({"field": "stem", "action": "retain_printed_number",
                                      "before": "", "after": token, "evidence": proof})
                    file_changes.extend({"file": relative, "item_index": index, "unit_id": uid,
                                         "numbers": list(_key(row)), **edit} for edit in edits)
                except (ValueError, TypeError, KeyError) as exc:
                    result["refused"].append({"file": relative, "unit_id": uid, "reason": str(exc)})
            after = question_content.audit_batch_source(root, candidate)
            result["checks"][relative]["source_after"] = after
            if not after["passed"]:
                result["refused"].append({"file": relative, "reason": "候选源头审计未通过",
                                           "issues": after["issues"]})
            result["changes"].extend(file_changes)
            if candidate != original:
                candidates[relative] = candidate
            if relative == format_gate.FINAL_PATH:
                final_check = _local_format(root, candidate)
                result["checks"]["final_format"] = final_check
                if not final_check["passed"]:
                    result["refused"].append({"file": relative, "reason": "最终候选本地格式未通过",
                                               "issues": final_check["errors"]})
        for relative, raw in snapshots.items():
            if format_gate._safe(root / relative).read_bytes() != raw:
                result["refused"].append({"file": relative, "reason": "检查期间输入变化"})
        if not result["refused"] and result["checks"].get("final_format", {}).get("passed"):
            result["passed"] = True
            result["files"] = candidates
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        result["refused"].append({"reason": str(exc)})
    return result
