# -*- coding: utf-8 -*-
"""Normalize a locally assembled unit to the question-bank wire shape.

The assembler and the upload mapper used to disagree: provenance fields lived in
``filterMetadata``, options were ``{key,text}`` objects, categories contained the
local placeholder ``未提供``, and media stayed as ``[IMG name]`` markers.  The
upload mapper correctly refuses such payloads, but that made every batch fail at
the last step.  This module performs the deterministic conversion once, at
assembly time, so the stored batch is already in the format the server accepts.

It never invents data: missing provenance fields are lifted from identified paper
metadata or left empty (and reported by the upload mapper), never guessed.
"""
from __future__ import annotations

from copy import deepcopy
import re

from workbench import taxonomy

SOURCE_KEYS = ("source", "source_type", "year", "location", "version",
               "authorization", "institution", "school", "regional_version")
FILTER_KEYS = {"subtypes", "categories", "region", "city", "district",
               "semesters", "difficultyValue"}
IMAGE = re.compile(r"\[IMG\s+([^\]]+)\]")
OPTION_PREFIX = re.compile(r"^\s*([A-Z])\s*[.．、)]\s*")


BLOCK_KEYS = {"type", "variant"}


def safe_blocks(blocks):
    """Keep only the materialBlocks fields the server understands."""
    def list_items(items):
        if not isinstance(items, list):
            return []
        return [({k: (list_items(v) if k == "items" else deepcopy(v))
                  for k, v in item.items() if k in {"text", "items"}})
                if isinstance(item, dict) else deepcopy(item) for item in items]

    if not isinstance(blocks, list):
        return []
    result = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        safe = {k: deepcopy(v) for k, v in block.items() if k in BLOCK_KEYS}
        safe["rows"] = []
        for row in block.get("rows", []) if isinstance(block.get("rows"), list) else []:
            if not isinstance(row, dict):
                continue
            cells = []
            for cell in row.get("cells", []) if isinstance(row.get("cells"), list) else []:
                if isinstance(cell, dict):
                    cells.append({k: (list_items(v) if k == "items" else deepcopy(v))
                                  for k, v in cell.items()
                                  if k in {"text", "items", "rowspan", "colspan", "header"}})
            safe["rows"].append({"cells": cells})
        result.append(safe)
    return result


def _walk_images(node, media_map, attachments, skip=("attachments", "subquestions")):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in skip:
                continue
            node[key] = _walk_images(value, media_map, attachments, skip)
        return node
    if isinstance(node, list):
        return [_walk_images(value, media_map, attachments, skip) for value in node]
    if isinstance(node, str):
        return replace_images(node, "question", None, media_map, attachments)
    return node


def _year(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return value


def normalize_blank_text(text):
    """库侧空档写法：<u>　26　</u> → __26__，纯空档 → ______。"""
    if not isinstance(text, str):
        return text
    text = re.sub(r"<u>\s*[\u3000\s]*(\d{1,3})[\u3000\s]*\s*</u>", r"__\1__", text)
    text = re.sub(r"\u3000\s*(\d{1,3})\s*\u3000", r"__\1__", text)
    text = re.sub(r"<u>\s*[\u3000\s]*\s*</u>", "______", text)
    text = re.sub(r"\u3000{2,}", "______", text)
    return text


def option_strings(values):
    """Convert string / {label|key,text} options to ``["A. text", ...]``."""
    result = []
    for index, value in enumerate(values):
        if isinstance(value, str):
            result.append(value)
            continue
        if isinstance(value, dict):
            label = value.get("label") or value.get("key") or chr(65 + index)
            text = value.get("text")
            if text is None:
                text = value.get("content", "")
            label = str(label).strip() or chr(65 + index)
            result.append(f"{label}. {text}".rstrip())
            continue
        result.append(str(value))
    return result


def replace_images(text, placement, option_key, media_map, attachments):
    if not isinstance(text, str) or not media_map:
        return text

    def repl(match):
        name = match.group(1).strip()
        url = media_map.get(name)
        if not url:
            return match.group(0)
        entry = {"kind": "image", "url": url, "name": name, "placement": placement}
        if option_key:
            entry["optionKey"] = option_key
        attachments.append(entry)
        return ""

    return IMAGE.sub(repl, text)


def normalize_item(item, media_map=None, meta=None, materials=None, audio=None):
    """Return a copy converted to the server's field names and value shapes."""
    if not isinstance(item, dict):
        return item
    data = deepcopy(item)
    filters = data.get("filterMetadata")
    if not isinstance(filters, dict):
        filters = {}

    # Provenance belongs at the top level. Lift from filterMetadata, then from
    # the identified paper metadata, without inventing values.
    for key in SOURCE_KEYS:
        if data.get(key) in (None, ""):
            value = filters.get(key)
            if value in (None, "") and isinstance(meta, dict):
                value = meta.get(key)
            if value not in (None, ""):
                data[key] = deepcopy(value)

    if data.get("year") is not None:
        data["year"] = _year(data["year"])
    if "year" in filters and data.get("year") is not None:
        # 顶层与 filterMetadata 必须同型，否则上传映射会报 ALIAS_CONFLICT。
        filters["year"] = data["year"]

    if "categories" in filters:
        kept = [c for c in filters["categories"] or []
                if isinstance(c, str) and c in taxonomy.categories()]
        filters["categories"] = kept
    # 保留来源字段/难度在 filterMetadata 中，兼容本地读取与既有测试；
    # 上传映射会把它们提升到顶层并从 filterMetadata 中剥离。
    filters = {k: v for k, v in filters.items()
               if k in FILTER_KEYS or k in SOURCE_KEYS or k == "difficulty"}

    if isinstance(data.get("options"), list):
        data["options"] = option_strings(data["options"])

    if isinstance(data.get("materialBlocks"), list):
        data["materialBlocks"] = safe_blocks(data["materialBlocks"])
    if isinstance(data.get("material_blocks"), list):
        data["materialBlocks"] = safe_blocks(data["material_blocks"])
        data.pop("material_blocks", None)

    # 听力/阅读题组的共享材料若缺失，用已确认结构的原文补回（不生成内容）。
    if (data.get("unit_kind") == "group" and not str(data.get("material") or "").strip()
            and isinstance(materials, dict)):
        source = materials.get(data.get("unit_id") or data.get("id"))
        if source is None and data.get('identity_version') != 'scoped-v1':
            numbers = data.get("numbers")
            if isinstance(numbers, list):
                try:
                    source = materials.get(tuple(int(n) for n in numbers))
                except (TypeError, ValueError):
                    source = None
        if isinstance(source, str) and source.strip():
            stem = str(data.get("stem") or "").strip()
            text = source.strip()
            if stem and text.startswith(stem):
                text = text[len(stem):].strip()
            data["material"] = text or source.strip()

    # attachments 去重：每次 normalize 都从现有附件起步并 append IMG/音频映射，
    # 重复调用会把同样的 image/audio 反复追加（q-6 修复前一次囤到 13 条）。
    # 保留原有附件（不预清空）——preassemble 已把 [IMG] 转成 attachments、文本里
    # 不再有 [IMG] 标记，若在此清空 image/audio 再"从文本重建"会把它们整批丢掉
    # （导致上传报 SOURCE_IMAGE_MISSING）。改为：保留原附件 + 从文本/音频追加，最后
    # 统一按身份去重，既幂等去重又不会误删已挂好的图片。
    existing = [a for a in (data.get("attachments") or []) if isinstance(a, dict)]
    attachments = list(existing)
    # 题库只认 question/option/answer/explanation；材料里的图没有独立位置，
    # 上传后随题干显示（放在 material 位置会被服务端判 invalid_attachment）。
    for key, placement in (("stem", "question"), ("material", "question")):
        if isinstance(data.get(key), str):
            data[key] = replace_images(data[key], placement, None, media_map, attachments)

    if isinstance(data.get("options"), list):
        result = []
        for option in data["options"]:
            match = OPTION_PREFIX.match(option)
            if match:
                text = replace_images(option[match.end():], "option", match.group(1),
                                       media_map, attachments)
                result.append(f"{match.group(1)}. {text}".rstrip())
            else:
                result.append(replace_images(option, "option", None, media_map, attachments))
        data["options"] = result

    if filters:
        data["filterMetadata"] = filters
    else:
        data.pop("filterMetadata", None)

    # 其余文本字段里的 [IMG] 标记（来源行、解析、答案、大意等）一并转换。
    if media_map:
        data = _walk_images(data, media_map, attachments)

    # 听力单元：装配时按 audio_map 关联已切分音频，避免上传时缺音频附件。
    if isinstance(audio, dict) and media_map:
        unit_id = data.get("unit_id") or data.get("id")
        filename = audio.get(unit_id)
        kind = str(data.get("type") or data.get("question_type") or "")
        if filename and kind.startswith("听力"):
            url = media_map.get(filename.split("/")[-1])
            if url and not any(a.get("kind") == "audio" for a in attachments):
                attachments.append({"kind": "audio", "url": url, "name": filename.split("/")[-1]})

    # 按身份去重，保持首次出现顺序，幂等。同一张图片/同一音频文件（kind+name 相同）
    # 无论出现在哪个字段或 placement，都只是同一媒体的一份引用，只保留首次出现的那条
    # （通常是 preassemble 已挂好的权威附件）；避免既有附件与文本 [IMG] 各存一份导致重复。
    deduped = []
    seen_att = set()
    for a in attachments:
        name = a.get("name") or str(a.get("url", "")).rsplit("/", 1)[-1]
        kind = a.get("kind")
        key = (kind, name) if kind in ("image", "audio") else (
            kind, name, a.get("placement"), a.get("optionKey"))
        if key in seen_att:
            continue
        seen_att.add(key)
        deduped.append(a)
    attachments = deduped
    if attachments:
        data["attachments"] = attachments
    elif "attachments" in data:
        data["attachments"] = attachments

    children = data.get("subquestions")
    if isinstance(children, list):
        data["subquestions"] = [normalize_item(child, media_map, meta, materials, audio) for child in children]
    return data
