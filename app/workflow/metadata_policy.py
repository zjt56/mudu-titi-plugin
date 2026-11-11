"""Local placeholders are absence markers, never evidence or invented labels."""
from __future__ import annotations

import json
from pathlib import Path

MISSING = "未提供"
PAPER_TEXT_FIELDS = ("paper_title", "source", "source_type")
# 试卷名/来源缺证据时的最后一档：原卷 Word 文件名。它是用户自己的文件身份，
# 不是编造标签；只有连文件名都没有时才落到「未提供」占位。
FILE_NAME_FIELDS = ("paper_title", "source")
PAPER_FILE_SUFFIXES = ("docx", "doc", "pdf")


def with_placeholders(metadata: dict | None) -> dict:
    """Keep every identified value; fill only missing paper/source text fields.

    Year remains numeric when known and absent when unknown. In particular, an
    absence marker must not become a fabricated year, province, or exam class.
    """
    result = dict(metadata or {})
    for key in PAPER_TEXT_FIELDS:
        value = result.get(key)
        if not isinstance(value, str) or not value.strip():
            result[key] = MISSING
    return result


def missing_source_fields(metadata: dict) -> list[str]:
    return [key for key in ("source", "source_type", "year", "region")
            if metadata.get(key) in (None, "", MISSING)]


def known(value) -> bool:
    """True only for real evidence: an absence marker is not a value."""
    return isinstance(value, str) and bool(value.strip()) and value.strip() != MISSING


def filename_name(value) -> str:
    """原卷文件名 → 名称：取最后一段路径并去掉扩展名。"""
    text = str(value or "").strip()
    if not text:
        return ""
    name = text.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if "." in name:
        stem, ext = name.rsplit(".", 1)
        if stem and len(ext) <= 5 and ext.isalnum():
            name = stem
    return name.strip()


def _load_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _paper_suffix(raw: str) -> bool:
    """只有原卷类文件（Word/PDF）的文件名才算试卷名证据，内部转换件不算。"""
    name = str(raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    return "." in name and name.rsplit(".", 1)[1].lower() in PAPER_FILE_SUFFIXES


def original_filename(batch_dir) -> str:
    """原卷文件名：优先批次登记的 original_filenames，其次来源路径。"""
    batch = Path(batch_dir)
    payload = _load_json(batch / "attachments" / "batch.json")
    names = payload.get("original_filenames")
    if isinstance(names, dict):
        for key in ("source", "answers"):
            raw = names.get(key)
            if _paper_suffix(raw):
                name = filename_name(raw)
                if name:
                    return name
    state = _load_json(batch / "workflow" / "manifest.json")
    source = (state.get("inputs") or {}).get("source") if isinstance(state.get("inputs"), dict) else {}
    if isinstance(source, dict) and _paper_suffix(source.get("path")):
        return filename_name(source.get("path"))
    return ""


def identified_paper_meta(batch_dir) -> dict:
    """`01_source/metadata.json` 里已识别并通过证据落盘的卷面文字字段。"""
    payload = _load_json(Path(batch_dir) / "01_source" / "metadata.json")
    return {key: payload[key].strip() for key in PAPER_TEXT_FIELDS
            if known(payload.get(key))}


def paper_meta(metadata: dict | None, batch_dir=None) -> dict:
    """卷面来源文本：已有值 → 已识别元数据 → 原卷文件名 → 占位。

    结构闸门只从正文卷头取 meta，课时拆分件正文没有卷头，若直接占位就会把
    已识别的来源和文件名一起丢掉；这里按证据强度补齐，绝不反过来覆盖识别值。
    """
    result = dict(metadata or {})
    if batch_dir is not None:
        for key, value in identified_paper_meta(batch_dir).items():
            if not known(result.get(key)):
                result[key] = value
        name = original_filename(batch_dir)
        if name:
            for key in FILE_NAME_FIELDS:
                if not known(result.get(key)):
                    result[key] = name
    return with_placeholders(result)


def resolved_paper_meta(metadata: dict | None, batch_dir=None) -> dict:
    """要写进题目字段的版本：占位只用于展示，不得塞进每道题的 filterMetadata。"""
    resolved = paper_meta(metadata, batch_dir)
    return {key: value for key, value in resolved.items()
            if value not in (None, "", [], {}, MISSING)}
