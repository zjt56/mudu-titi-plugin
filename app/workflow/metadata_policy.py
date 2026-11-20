"""Local placeholders are absence markers, never evidence or invented labels."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re

MISSING = "未提供"
PAPER_TEXT_FIELDS = ("paper_title", "source", "source_type")
SOURCE_ALIAS_FIELDS = ("source", "source_type", "year")
# 试卷名/来源缺证据时的最后一档：原卷 Word 文件名。它是用户自己的文件身份，
# 不是编造标签；只有连文件名都没有时才落到「未提供」占位。
FILE_NAME_FIELDS = ("paper_title", "source")
PAPER_FILE_SUFFIXES = ("docx", "doc", "pdf")


def parse_year(value) -> int | None:
    """Return a valid 4-digit year integer (1990..2100), else None."""
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    if not text.isdigit():
        return None
    number = int(text)
    return number if 1990 <= number <= 2100 else None


def extract_filename_year(value) -> int | None:
    """从原卷文件名提取明确年份：支持四位年份与「两位+年/春/秋」，不把序号前缀当年份。

    例：2025年七上... -> 2025，2025-2026学年... -> 2025，15年安徽中考... -> 2015，
    11 26秋 七上... -> 2026，02七上... / 22九上... -> None。
    """
    name = filename_name(value)
    if not name:
        return None
    match = re.search(r"(?<!\d)(199\d|20\d{2})(?!\d)", name)
    if match:
        return parse_year(match.group(1))
    match = re.search(r"(?<!\d)(\d{2})\s*(?:年|[春秋])", name)
    if match:
        yy = int(match.group(1))
        year = 1900 + yy if 90 <= yy <= 99 else (2000 + yy if 0 <= yy <= 50 else None)
        return parse_year(year)
    return None


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


def upload_entry_year(batch_dir) -> int | None:
    """上传入口表单填写的年份（attachments/batch.json），填写了就直接使用。"""
    payload = _load_json(Path(batch_dir) / "attachments" / "batch.json")
    return parse_year(payload.get("year"))


def _manifest_meta_year(batch_dir) -> int | None:
    state = _load_json(Path(batch_dir) / "workflow" / "manifest.json")
    meta = state.get("meta") if isinstance(state.get("meta"), dict) else {}
    return parse_year(meta.get("year"))


def identified_paper_meta(batch_dir) -> dict:
    """`01_source/metadata.json` 里已识别并通过证据落盘的卷面字段。"""
    payload = _load_json(Path(batch_dir) / "01_source" / "metadata.json")
    result = {key: payload[key].strip() for key in PAPER_TEXT_FIELDS
              if known(payload.get(key))}
    year = parse_year(payload.get("year"))
    if year is not None:
        result["year"] = year
    return result


def paper_meta(metadata: dict | None, batch_dir=None) -> dict:
    """卷面来源文本与年份：
    - 文本字段：已有值 → 已识别元数据 → 原卷文件名 → 占位；
    - 年份：上传入口填写值（最高优先级）→ 已有值 → 已识别元数据 → manifest.meta → 原卷文件名；缺省留空不编造。

    结构闸门只从正文卷头取 meta，课时拆分件正文没有卷头，若直接占位就会把
    已识别的来源和文件名一起丢掉；这里按证据强度补齐，绝不反过来覆盖识别值。
    """
    result = dict(metadata or {})
    current_year = parse_year(result.get("year"))
    if current_year is not None:
        result["year"] = current_year
    elif "year" in result:
        result.pop("year", None)
    if batch_dir is not None:
        entry_year = upload_entry_year(batch_dir)
        if entry_year is not None:
            result["year"] = entry_year
        identified = identified_paper_meta(batch_dir)
        for key in PAPER_TEXT_FIELDS:
            if key in identified and not known(result.get(key)):
                result[key] = identified[key]
        if "year" not in result and "year" in identified:
            result["year"] = identified["year"]
        if "year" not in result:
            manifest_year = _manifest_meta_year(batch_dir)
            if manifest_year is not None:
                result["year"] = manifest_year
        name = original_filename(batch_dir)
        if name:
            for key in FILE_NAME_FIELDS:
                if not known(result.get(key)):
                    result[key] = name
            if "year" not in result:
                file_year = extract_filename_year(name)
                if file_year is not None:
                    result["year"] = file_year
    return with_placeholders(result)


def project_source_metadata(payload: dict) -> dict:
    """Project local absence markers into optional upload fields at meta time.

    Reconcile only an absent alias with a supplied value. Two substantive values
    remain untouched so the upload mapper still rejects genuine conflicts.
    This does not infer provenance or a year from the batch name or parent.
    """
    result = deepcopy(payload)

    def absent(value):
        return value is None or (isinstance(value, str)
                                 and value.strip() in ("", MISSING))

    def visit(node):
        if not isinstance(node, dict):
            return
        filters = node.get("filterMetadata")
        # Invalid structures belong to the hard validator, not this projection.
        if filters is not None and not isinstance(filters, dict):
            return
        filters = filters if isinstance(filters, dict) else {}
        for key in SOURCE_ALIAS_FIELDS:
            for owner in (node, filters):
                if key in owner and absent(owner[key]):
                    del owner[key]
                elif key == "year" and isinstance(owner.get(key), str) and owner[key].strip().isdigit():
                    owner[key] = int(owner[key].strip())
            top, nested = key in node, key in filters
            if top and not nested:
                filters[key] = deepcopy(node[key])
            elif nested and not top:
                node[key] = deepcopy(filters[key])
        if filters or "filterMetadata" in node:
            node["filterMetadata"] = filters
        for child in node.get("subquestions") or []:
            visit(child)

    for item in result.get("items") or []:
        visit(item)
    return result


def resolved_paper_meta(metadata: dict | None, batch_dir=None) -> dict:
    """要写进题目字段的版本：占位只用于展示，不得塞进每道题的 filterMetadata。"""
    resolved = paper_meta(metadata, batch_dir)
    return {key: value for key, value in resolved.items()
            if value not in (None, "", [], {}, MISSING)}
