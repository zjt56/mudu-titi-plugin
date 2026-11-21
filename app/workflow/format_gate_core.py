"""Core pure helpers shared by format_gate, legacy_format, and review_legacy.

Extracted as a leaf module (depending only on stdlib and upload_payload) so
format_gate <-> legacy_format no longer form a circular import.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

from . import upload_payload

FINAL_PATH = "06_batch/batch_meta.json"
DEFERRED_CODES = frozenset({
    "CATALOG_REQUIRED",
    "TEXTBOOK_CATALOG_REQUIRED",
    "TEXTBOOK_CATALOG_UNSUPPORTED",
    "TEXTBOOK_KNOWLEDGE_INVALID",
})


def _safe(path):
    if ".." in Path(path).parts:
        raise ValueError("不允许包含父目录跳转的路径")
    path = Path(os.path.abspath(path))
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError(f"不允许软链接路径：{path}")
    return path


def _load(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError(f"JSON 重复键：{key}")
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f"JSON 不允许非有限数值：{value}")

    def number(value):
        result = float(value)
        return result if math.isfinite(result) else constant(value)

    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                      parse_constant=constant, parse_float=number)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _positive(value):
    return type(value) is int and value > 0


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _shape(batch):
    """Check final wire shape before the builder can default/convert values."""
    errors = []
    ids, units = {}, {}

    def error(code, message, path):
        errors.append({"code": code, "message": message, "path": path})

    def unique(value, seen, code, path):
        if value in seen:
            error(code, f"重复身份 {value}（首次位于 {seen[value]}）", path)
        else:
            seen[value] = path

    def visit(item, path, child=False):
        if not isinstance(item, dict):
            error("ITEM_INVALID", "题目/子题必须是对象", path)
            return
        required = ("number", "stem") if child else ("id", "unit_kind", "number", "numbers", "stem", "knowledge")
        for key in required:
            if key not in item:
                error("SCHEMA_REQUIRED", f"缺少必填字段 {key}", path)
        if not child and not any(key in item for key in ("type", "question_type")):
            error("SCHEMA_REQUIRED", "缺少必填字段 type/question_type", path)
        for key in ("id", "unit_id"):
            if key in item:
                if not _text(item[key]):
                    error("IDENTITY_INVALID", f"{key} 必须是非空字符串", path)
                else:
                    unique(item[key], ids if key == "id" else units,
                           "QUESTION_ID_DUPLICATE" if key == "id" else "UNIT_ID_DUPLICATE", path)
        kind = item.get("unit_kind")
        children = item.get("subquestions", [])
        if "number" in item and not _positive(item["number"]):
            error("QUESTION_NUMBER_INVALID", "number 必须是正整数", path)
        if not child and "numbers" in item:
            ns = item["numbers"]
            if (not isinstance(ns, list) or not ns or any(not _positive(n) for n in ns)
                    or len(ns) != len(set(n for n in ns if type(n) is int))):
                error("QUESTION_NUMBERS_INVALID", "numbers 必须是唯一正整数数组", path)
            elif kind == "standalone" and ns != [item.get("number")]:
                error("QUESTION_NUMBER_COVERAGE", "独立题 numbers 必须等于 [number]", path)
            elif kind == "group" and item.get("number") != min(ns):
                error("QUESTION_NUMBER_COVERAGE", "题组 number 必须为首个子题号", path)
        for key in ("stem", "answer", "explanation", "articleSummary"):
            if key in item and not isinstance(item[key], str):
                error("TEXT_FIELD_INVALID", f"{key} 必须是字符串；缺失内容可保留原占位", path)
        if "options" in item and (not isinstance(item["options"], list)
                                  or any(not isinstance(o, str) for o in item["options"])):
            error("OPTIONS_INVALID", "最终 options 必须是字符串数组", path)
        for key in ("knowledgeIds", "knowledge_ids"):
            if key in item:
                values = item[key]
                if (not isinstance(values, list) or any(not _text(v) for v in values)
                        or len(values) != len(set(v for v in values if isinstance(v, str)))):
                    error("KNOWLEDGE_IDS_INVALID", f"{key} 必须是无重复的字符串 ID 数组", path)
        if "knowledge_version_id" in item and not _text(item["knowledge_version_id"]):
            error("KNOWLEDGE_VERSION_INVALID", "knowledge_version_id 必须是非空字符串", path)
        mappings = item.get("textbookMappings", item.get("textbook_mappings"))
        if isinstance(mappings, list):
            for mapping in mappings:
                if isinstance(mapping, dict) and not _text(mapping.get("knowledgeVersionId")):
                    error("TEXTBOOK_VERSION_INVALID", "教材映射必须有字符串 knowledgeVersionId", path)
        if isinstance(children, list):
            for index, sub in enumerate(children):
                visit(sub, f"{path}.subquestions[{index}]", True)

    if isinstance(batch, dict) and isinstance(batch.get("items"), list):
        for index, item in enumerate(batch["items"]):
            visit(item, f"items[{index}]")
        errors.extend(upload_payload.identity_errors(batch["items"], legacy_numbers=True))
    return errors


def _options(root, batch, report):
    options = {}
    if not isinstance(batch, dict):
        return options
    need_id = "batch_id" not in batch and "id" not in batch
    need_name = "name" not in batch and "title" not in batch
    if not (need_id or need_name):
        return options
    path = _safe(root / "workflow/manifest.json")
    if not path.exists():
        return options
    raw = path.read_bytes()
    manifest = _load(raw)
    report["input_sha256"]["workflow/manifest.json"] = _sha(raw)
    if not isinstance(manifest, dict):
        raise ValueError("manifest 必须是对象")
    if need_id and _text(manifest.get("workflow_id")):
        options["batch_id"] = manifest["workflow_id"]
    meta = manifest.get("meta", {})
    if need_name and isinstance(meta, dict):
        for key in ("paper_title", "name", "title"):
            if _text(meta.get(key)):
                options["name"] = meta[key]
                break
    return options
