"""Native source object identities and read-only table resolution.

These are extraction-local objects, not question IDs or a second reading order.
A reference binds the bytes of the native DOCX, its ordered structural path and
its extracted content. Legacy extraction remains readable through exact content
binding (layout whitespace only); an invalid new reference never falls back.
"""
from __future__ import annotations

import hashlib
import json
import re

VERSION = "native-source-objects/v1"
TABLE = re.compile(r"\[TABLE(?:\s+(\d+))?\](.*?)\[/TABLE\]", re.S)
IMAGE = re.compile(r"\[IMG\s+([^\]]+)\]")


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _content(value):
    if isinstance(value, dict):
        return {key: _content(item) for key, item in value.items() if key != "source_ref"}
    if isinstance(value, list):
        return [_content(item) for item in value]
    return value


def attach(extraction, source_sha256):
    """Annotate native objects in place without changing text/grid/order.

    Paths are ordered extracted-body paths; they do not claim physical pages or
    XML XPath positions. A shared media file has a separate object per occurrence.
    """
    objects = []
    media = iter(extraction.get("media") or [])
    pending_media = next(media, None)

    def record(node, kind, path, parent=None):
        identity = "src-" + _digest([source_sha256, "word/document.xml", path, kind])
        node["source_ref"] = identity
        objects.append({"id": identity, "kind": kind, "path": path,
                        "parent": parent, "content_sha256": _digest(_content(node))})
        return identity

    def blocks(items, path, parent=None):
        nonlocal pending_media
        for index, block in enumerate(items):
            location = f"{path}/blocks/{index}"
            kind = block.get("type")
            if kind == "table":
                identity = record(block, kind, location, parent)
                for ri, row in enumerate(block.get("rows") or []):
                    for ci, cell in enumerate(row.get("cells") or []):
                        cell_path = f"{location}/rows/{ri}/cells/{ci}"
                        cell_id = record(cell, "cell", cell_path, identity)
                        blocks(cell.get("blocks") or [], cell_path, cell_id)
            else:
                identity = record(block, kind or "paragraph", location, parent)
                # Resolved image entries are already in extraction order. Missing
                # relationships produce unresolved markers but no media object.
                for mi, match in enumerate(IMAGE.finditer(str(block.get("text") or ""))):
                    if pending_media is not None and pending_media.get("name") == match.group(1).strip():
                        record(pending_media, "image", f"{location}/images/{mi}", identity)
                        pending_media = next(media, None)

    blocks(extraction.get("blocks") or [], "body")
    extraction["source_objects"] = {"version": VERSION, "source_sha256": source_sha256,
                                    "part": "word/document.xml", "objects": objects}
    return extraction


def table_markers(text):
    """Yield complete top-level table markers, preserving nested tables verbatim."""
    depth, start = 0, None
    for match in re.finditer(r"\[TABLE(?:\s+\d+)?\]|\[/TABLE\]", text):
        if match.group(0) == "[/TABLE]":
            if depth:
                depth -= 1
                if depth == 0:
                    yield text[start:match.end()]
        else:
            if depth == 0:
                start = match.start()
            depth += 1


def _compact(value):
    # Only layout whitespace is insignificant. Digits, punctuation, markup,
    # literal pipes and every non-whitespace source character remain binding.
    # Preserve word boundaries: "a b" must never bind to "ab". Reflow and
    # spacing around the serialization separator are presentation-only.
    return re.sub(r"\s*\|\s*", "|", re.sub(r"\s+", " ", value).strip())


def resolve_table(extraction, marker):
    """Return a uniquely bound native table and diagnostic, never guessed spans."""
    if not isinstance(extraction, dict) or not isinstance(marker, str):
        return None, "没有原生表格证据"
    match = TABLE.fullmatch(marker.strip())
    if not match or not match.group(1):
        return None, "表格标记没有明确编号"
    matches = [table for table in extraction.get("tables") or []
               if isinstance(table, dict) and str(table.get("number")) == match.group(1)
               and _compact(str(table.get("text") or "")) == _compact(marker)]
    if len(matches) != 1:
        return None, "表格编号与完整原文不能唯一绑定"
    table = matches[0]
    if "source_objects" in extraction:
        index = extraction["source_objects"]
        if not isinstance(index, dict) or index.get("version") != VERSION:
            return None, "原生来源对象索引版本无效"
        source_sha = index.get("source_sha256")
        if not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha):
            return None, "原生来源对象摘要无效"
        if extraction.get("source_sha256") and extraction["source_sha256"] != source_sha:
            return None, "原生来源对象摘要与提取原件不一致"
        if index.get("part") != "word/document.xml":
            return None, "原生来源对象部件无效"
        refs = [obj for obj in index.get("objects") or []
                if isinstance(obj, dict) and obj.get("id") == table.get("source_ref")]
        if len(refs) != 1:
            return None, "原生表格引用缺失或重复"
        ref = refs[0]
        expected = "src-" + _digest([source_sha, "word/document.xml", ref.get("path"), "table"])
        if (ref.get("kind") != "table" or ref.get("id") != expected
                or ref.get("content_sha256") != _digest(_content(table))):
            return None, "原生表格引用与当前结构内容不一致"
    return table, ""


def capabilities(table):
    """Report semantic layouts the current remote table contract cannot express.

    Span/grid whitespace are supported. Cell-local media placement and nested
    tables are not expressible by text/items/rowspan/colspan/header alone.
    These are final adapter diagnostics, not early format failures.
    """
    problems = []
    for ri, row in enumerate(table.get("rows") or []):
        for ci, cell in enumerate(row.get("cells") or []):
            if cell.get("merge_origin") is not None:
                continue
            location = {"row": ri, "column": cell.get("column", ci)}
            if IMAGE.search(str(cell.get("text") or "")):
                problems.append({**location, "code": "LAYOUT_CELL_MEDIA_UNSUPPORTED",
                                 "reason": "平台附件只支持题干/选项/答案/解析位置，不能表达图片所属表格单元格"})
            if any(block.get("type") == "table" for block in cell.get("blocks") or []):
                problems.append({**location, "code": "LAYOUT_NESTED_TABLE_UNSUPPORTED",
                                 "reason": "平台单元格不支持原生嵌套表格，不能以纯文本或拆列冒充保真"})
    return problems


# 单元格内图片：平台契约确实没有单元格级附件位置，但图片本身已按单元级
# placement=question 随题交付、单元格文字保持原文。属于能力受限，按硬规则 6
# 只作显式提示（保留错误码与 unit/row/column），不阻断最终格式闸门。
NON_BLOCKING_CAPABILITY_CODES = {"LAYOUT_CELL_MEDIA_UNSUPPORTED"}


def _table_projection(blocks):
    """Compare cell ownership/content and spans, excluding cosmetic table style."""
    from .wire_format import normalize_blank_text

    def text(value):
        value = normalize_blank_text(str(value or ""))
        value = re.sub(r"<br\s*/?>", " ", value, flags=re.I)
        value = re.sub(r"</?(?:b|i)\b[^>]*>", "", value, flags=re.I)
        return re.sub(r"\s+", " ", value).strip()

    def list_text(values):
        return " ".join(value if isinstance(value, str) else
                        str(value.get("text") or "") + " " + list_text(value.get("items") or [])
                        for value in values)

    return [[[ (text(str(cell.get("text") or "") + " " + list_text(cell.get("items") or [])),
                 cell.get("rowspan", 1), cell.get("colspan", 1))
               for cell in row["cells"]] for row in block["rows"]]
            for block in blocks if block.get("type") == "table"]


def upload_errors(batch_dir, items):
    """Rebuild current source bindings for the final adapter; never trust a report.

    No existing batch/draft is modified. Pure local extraction fixtures and old
    uploads without confirmed source keep their existing validator behavior.
    Returns blocking errors only; use :func:`layout_issues` for the capability
    warnings that must stay visible without stopping the final gate.
    """
    return layout_issues(batch_dir, items)["errors"]


def layout_issues(batch_dir, items):
    """Return ``{"errors": [...], "warnings": [...]}`` for the upload adapter.

    Capability limitations the remote contract cannot express (cell-local media)
    are warnings carrying their code/unit/row/column, so nothing disappears
    silently; structural problems (nested tables, unproven binding, projection
    mismatch) stay blocking.
    """
    errors, warnings = _layout_scan(batch_dir, items)
    return {"errors": errors, "warnings": warnings}


def _layout_scan(batch_dir, items):
    from pathlib import Path
    root = Path(batch_dir)
    required = ("01_source/extraction.json", "01_source/正文.txt", "01_source/structure.confirmed.json")
    if not (root / required[0]).is_file():
        return [], []  # Legacy uploads without native evidence retain existing checks.
    owners = {str(item.get("unit_id") or item.get("id") or "") for item in items if isinstance(item, dict)}
    try:
        extraction = json.loads((root / required[0]).read_text(encoding="utf-8-sig"))
        if not isinstance(extraction, dict):
            raise ValueError("extraction.json 必须是对象")
        indexed = "source_objects" in extraction
        if indexed:
            index = extraction["source_objects"]
            if not isinstance(index, dict) or index.get("version") != VERSION:
                raise ValueError("原生来源对象索引无效")
            native_tables = [obj for obj in index.get("objects") or []
                             if isinstance(obj, dict) and obj.get("kind") == "table"]
            if native_tables and not extraction.get("tables"):
                raise ValueError("原生来源对象含表格但提取表格集合缺失")
        if not extraction.get("tables"):
            return [], []
        missing = [name for name in required[1:] if not (root / name).is_file()]
        if missing:
            if not indexed:
                return [], []
            raise ValueError("原生表格缺少当前绑定证据：" + ", ".join(missing))
        from . import question_draft
        from .agent_tasks import confirmed_units
        units = [unit for unit in confirmed_units(root) if unit["unit_id"] in owners]
        if indexed and {unit["unit_id"] for unit in units} != owners:
            raise ValueError("上传单元与当前确认来源身份不一致")
        draft = question_draft.build(root, units=units)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        return [{"code": "LAYOUT_SOURCE_EVIDENCE_INVALID",
                 "message": "不能复建当前来源表格证据：" + str(exc),
                 "path": "01_source/extraction.json"}], []
    errors = []
    warnings = []
    for issue in draft.get("layout_diagnostics", []):
        if issue.get("unit_id") not in owners:
            continue
        row = {"unit_id": issue["unit_id"], "code": issue["code"],
               "message": issue["reason"], "row": issue.get("row"), "column": issue.get("column")}
        if issue["code"] in NON_BLOCKING_CAPABILITY_CODES:
            row["non_blocking"] = True
            row["message"] = row["message"] + "；图片已按单元级附件随题交付，表格文字保持原文，不丢图也不改字"
            warnings.append(row)
        else:
            errors.append(row)
    if indexed:
        errors.extend({"unit_id": issue["unit_id"], "code": "LAYOUT_SOURCE_BINDING_UNPROVEN",
                       "message": "无法证明当前单元的原生表格归属：" + issue["reason"]}
                      for issue in draft.get("issues", [])
                      if issue.get("unit_id") in owners and issue.get("code") == "SOURCE_SCOPE_UNPROVEN")
    if indexed:
        expected_items = {item["unit_id"]: item for item in draft.get("items", [])}
        for item in items:
            if not isinstance(item, dict):
                continue
            uid = str(item.get("unit_id") or item.get("id") or "")
            expected = expected_items.get(uid)
            if expected is None:
                errors.append({"unit_id": uid, "code": "LAYOUT_SOURCE_BINDING_UNPROVEN",
                               "message": "当前上传单元没有唯一的来源重建对象"})
                continue
            try:
                wanted = _table_projection(expected.get("materialBlocks") or [])
                actual = _table_projection(item.get("materialBlocks", item.get("material_blocks")) or [])
                if wanted != actual:
                    errors.append({"unit_id": uid, "code": "LAYOUT_TABLE_PROJECTION_MISMATCH",
                                   "message": "上传表格的单元格文字、顺序或合并跨度与当前原生来源投影不一致"})
            except (TypeError, KeyError, AttributeError):
                errors.append({"unit_id": uid, "code": "LAYOUT_TABLE_PROJECTION_INVALID",
                               "message": "上传表格结构无法与当前原生来源逐格核对"})
    return errors, warnings
