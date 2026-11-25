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
import json
from pathlib import Path
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


_METADATA_IMAGE_SKIP = frozenset((
    "attachments", "subquestions", "filterMetadata", "paper_title",
    "source_section", "source_span", "evidence", "difficultyEvidence",
    *SOURCE_KEYS,
))


def _strip_metadata_img_tags(value):
    """剥离元信息字段里残留的 [IMG ...] 装饰锚点，不生成题目附件。"""
    if isinstance(value, str):
        return IMAGE.sub("", value).strip()
    if isinstance(value, dict):
        return {k: _strip_metadata_img_tags(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_metadata_img_tags(v) for v in value]
    return value


def _walk_images(node, media_map, attachments, skip=_METADATA_IMAGE_SKIP,
                 placement="question", decorative_names=None):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in skip:
                continue
            child_placement = key if key in ("answer", "explanation") else placement
            node[key] = _walk_images(value, media_map, attachments, skip, child_placement, decorative_names)
        return node
    if isinstance(node, list):
        return [_walk_images(value, media_map, attachments, skip, placement, decorative_names) for value in node]
    if isinstance(node, str):
        return replace_images(node, placement, None, media_map, attachments, decorative_names=decorative_names)
    return node


def _year(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return value


# 同一个空档在 Word 里常被拆进连续多段下划线：`<u>　</u><u>26</u><u>　</u><u> </u>`。
# 只按单段 `<u>` 归一，会把它写成一串并列的下划线（`______  __26__  ______`），
# 既读不出“一处空档带题号”，也会在窄列里换行成孤立横线。
BLANK_RUN = re.compile(
    r"(?:<u>[\s\u3000\xa0]*</u>|<u>\s*\d{1,3}\s*</u>)"
    r"(?:(?:<u>[\s\u3000\xa0]*</u>)|(?:<u>\s*\d{1,3}\s*</u>))+"
)


def _blank_run(match):
    numbers = re.findall(r"<u>\s*(\d{1,3})\s*</u>", match.group(0))
    if not numbers:
        return "______"
    return "".join(f"__{number}__" for number in numbers)


# <u>标签内部夹着连续下划线（如词形填空 <u>i______</u>）是歧义形态：任何宽松把
# __x__ 当下划线标记的渲染器都会在其内部再套一层 <u>，显示成「很短、一粗一细两条线」。
# 规范为：标签内保留非下划线文字（<u>i</u>），连续下划线移出标签成为库侧 ______，
# 语义等价（词仍画线、空档是一条填空线），且在任何渲染器下都只画一条线。
_U_INLINE_RUN = re.compile(r"<u>([^<]*?)</u>")


def _split_inline_underline(match):
    inner = match.group(1)
    if "_" not in inner:
        return match.group(0)
    out = []
    buf = ""
    for part in re.split(r"(_+)", inner):
        if part and set(part) == {"_"} and len(part) >= 3:
            if buf:
                out.append("<u>" + buf + "</u>")
                buf = ""
            out.append("______")
        else:
            buf += part
    if buf:
        out.append("<u>" + buf + "</u>")
    return "".join(out)


# 独立整行答题横线（翻译/仿写/任务型阅读简答等）默认占一整行，避免被截短成 6 个 `_` 传到远端题库后过短。
FULL_LINE_BLANK = "_" * 60
_FULL_LINE_SENTINEL = "\x00WF_FULL_LINE_BLANK\x00"
_STANDALONE_LONG_RULE = re.compile(
    r"(?im)(^|\n)[ \t\u3000\xa0]*"
    r"((?:<u>[\s\u3000\xa0_\uff3f]+</u>|[_\uff3f]{3,})"
    r"(?:[ \t\u3000\xa0]*(?:<u>[\s\u3000\xa0_\uff3f]+</u>|[_\uff3f]{3,}))*)"
    r"[ \t\u3000\xa0]*(?=\n|$)"
)
_STANDALONE_SHORT_RULE = re.compile(r"(?m)^[ \t]*[_\uff3f]{3,}[ \t]*$")


def _rule_visual_units(raw: str) -> int:
    plain = re.sub(r"</?u>", "", raw or "", flags=re.I)
    return sum(2 if ch in ("\u3000", "\uff3f") else 1 for ch in plain)


def _protect_full_line_blank(match):
    if _rule_visual_units(match.group(2)) >= 12:
        return match.group(1) + _FULL_LINE_SENTINEL
    return match.group(0)


def expand_standalone_answer_lines(text):
    """Expand standalone answer-line blanks (after prompt text) to a full-line rule for remote upload.

    Preserves shell-only subquestion stems (bare ``______`` with no prompt text) and inline blanks.
    """
    if not isinstance(text, str) or "\n" not in text:
        return text
    lines = text.splitlines()
    has_prompt = False
    out = []
    for line in lines:
        if has_prompt and _STANDALONE_SHORT_RULE.fullmatch(line):
            out.append(FULL_LINE_BLANK)
        else:
            out.append(line)
            if line.strip() and not _STANDALONE_SHORT_RULE.fullmatch(line):
                has_prompt = True
    return "\n".join(out)


# 完形/阅读/语篇等大长材料组题：正文段落统一采用首行缩进两字符（两个全角空格 \u3000\u3000）。
LONG_MATERIAL_GROUP_TYPES = frozenset({
    "完形填空",
    "阅读理解",
    "任务型阅读",
    "语法选择",
    "短文填空",
    "选词填空",
    "教材语篇填空",
    "读写综合",
})
NON_LONG_MATERIAL_GROUP_TYPES = frozenset({
    "补全对话",
    "听力短对话理解",
    "听力长对话理解",
    "听力判断",
    "听力排序",
    "语音题",
    "情景运用",
})
_INDENT_SKIP_IMG_ONLY = re.compile(r"^(?:\[IMG\s+[^\]]+\]\s*)+$", re.I)
_INDENT_SKIP_SECTION_TAG = re.compile(
    r"^(?:[（(]?\s*[A-Z]\s*[)）]?|(?:Passage|Text|Section|Part)\s*[A-Z0-9]+)$",
    re.I,
)
_INDENT_SKIP_NOTE = re.compile(r"^(?:※\s*Notes?|Notes?|注|提示|【提示】|\*\s*[A-Za-z\u4e00-\u9fff])", re.I)
_INDENT_SKIP_SPEAKER = re.compile(r"^(?:[A-Z](?:&[A-Z])?|[A-Z][a-z]{1,15}(?:\s+[A-Z][a-z]{1,12})?|Woman|Man|Boy|Girl|Host|Interviewer|Reporter)\s*[：:]")
_INDENT_SKIP_OPTION = re.compile(r"^(?:[A-G][\.\、\)\s]|①|②|③|④|⑤|⑥|⑦)")


def _looks_like_dialogue_material(text: str) -> bool:
    lines = [ln.strip() for ln in str(text or "").replace("\r\n", "\n").splitlines() if ln.strip()]
    if len(lines) < 2:
        return False
    speaker_lines = sum(1 for ln in lines if _INDENT_SKIP_SPEAKER.match(ln))
    return speaker_lines >= 2 and speaker_lines >= len(lines) * 0.4


def is_long_material_group(item) -> bool:
    """Return True if item is a grouped question with long prose/passage material needing 2-char indent."""
    if not isinstance(item, dict):
        return False
    material = item.get("material")
    if not isinstance(material, str) or not material.strip():
        return False
    is_group = (
        item.get("unit_kind") == "group"
        or (isinstance(item.get("subquestions"), list) and len(item["subquestions"]) > 0)
    )
    if not is_group:
        return False
    qtype = str(item.get("type") or item.get("question_type") or "").strip()
    if qtype in NON_LONG_MATERIAL_GROUP_TYPES:
        return False
    if _looks_like_dialogue_material(material):
        return False
    if qtype in LONG_MATERIAL_GROUP_TYPES:
        return True
    # Fallback for untyped or generic grouped passage items: require substantial prose length outside tables/images.
    plain = re.sub(r"\[TABLE(?:\s+\d+)?\][\s\S]*?\[/TABLE\]", "", material, flags=re.I)
    plain = re.sub(r"\[IMG\s+[^\]]+\]", "", plain, flags=re.I).strip()
    if len(plain) < 120:
        return False
    # Exclude short word-bank / alphabet-song lists (many very short lines without prose sentences).
    prose_lines = [ln.strip() for ln in plain.splitlines() if ln.strip()]
    if prose_lines and max(len(ln) for ln in prose_lines) < 45:
        return False
    return True


def format_long_material_indent(text: str) -> str:
    """Apply idempotent 2-fullwidth-space (``\\u3000\\u3000``) first-line indent to prose paragraphs.

    Skips ``[TABLE ...]...[/TABLE]`` blocks, blank lines, standalone ``[IMG ...]`` lines,
    passage labels (``A`` / ``Passage A``), notes (``※Notes:``), dialogue turns, option lines,
    and obvious short centered headings without sentence punctuation.
    """
    if not isinstance(text, str) or not text.strip():
        return text
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    in_table = False
    seen_prose = False
    for line in lines:
        stripped = line.strip()
        if re.match(r"^\[TABLE(?:\s+\d+)?\]", stripped, re.I):
            in_table = True
            out.append(line)
            if re.search(r"\[/TABLE\]\s*$", stripped, re.I):
                in_table = False
            continue
        if in_table:
            out.append(line)
            if re.search(r"\[/TABLE\]\s*$", stripped, re.I):
                in_table = False
            continue
        if not stripped:
            out.append("")
            continue
        if (
            _INDENT_SKIP_IMG_ONLY.match(stripped)
            or _INDENT_SKIP_SECTION_TAG.match(stripped)
            or _INDENT_SKIP_NOTE.match(stripped)
            or _INDENT_SKIP_SPEAKER.match(stripped)
            or _INDENT_SKIP_OPTION.match(stripped)
        ):
            out.append(line)
            continue
        # Preserve an obvious short title at the very start of a passage (e.g. "A Trip to Beijing")
        # when it has no sentence-ending punctuation and no cloze blank.
        clean_title = re.sub(r"\*{2,}", "", stripped).strip()
        if (
            not seen_prose
            and len(clean_title) <= 42
            and not re.search(r"[.。!?！？:：,，;；]|_{2,}|<u>", clean_title)
            and len(lines) > 1
        ):
            out.append(stripped)
            continue
        seen_prose = True
        body = line.lstrip(" \t\u3000\xa0")
        out.append("\u3000\u3000" + body)
    return "\n".join(out)


def normalize_blank_text(text):
    """库侧空档写法：<u>　26　</u> → __26__，行内纯空档 → ______，独立整行答题横线保留为整行。

    连续多段下划线属于同一处空档（题号 + 两侧留白），必须先整体识别再归一。
    <u>词______</u> 这种标签内夹连续下划线的歧义形态会拆成 <u>词</u>______，
    避免下游宽松解析器在 <u> 内再套 <u> 渲染成双线（用户 2026-11-19 反馈）。
    """
    if not isinstance(text, str):
        return text
    text = _STANDALONE_LONG_RULE.sub(_protect_full_line_blank, text)
    text = BLANK_RUN.sub(_blank_run, text)
    text = re.sub(r"<u>\s*[\u3000\s]*(\d{1,3})[\u3000\s]*\s*</u>", r"__\1__", text)
    text = re.sub(r"\u3000\s*(\d{1,3})\s*\u3000", r"__\1__", text)
    text = re.sub(r"<u>\s*[\u3000\s]*\s*</u>", "______", text)
    # 行首/单元格首段首缩进（如 `　　注意:`、`　　McArdle...`、`Jane | 　　My room...`）与多栏并排题/选项间的全角分隔
    # （如 `c____/æ/t　　2.[IMG ...]`、`A. map　　B. apple`）不是填空空档，保留原空格；
    # 其余位置的连续全角空格（句中空位、括号/英文前空位）统一归一为 ______。
    text = re.sub(
        r"(?m)((?:^|\|\s*)\u3000{2}(?!\u3000)(?=[“\"‘'《\u4e00-\u9fff]|[A-Z][a-z])"
        r"|\u3000{2,}(?=(?:\*{1,3})?(?:[（(][ \u3000]*[）)][ \u3000]*)?(?:\*{1,3})?[1-9]\d{0,2}\s*[.．、](?!\d))"
        r"|\u3000{2,}(?=(?:\*{1,3})?[A-H]\s*[.．、](?![A-Za-z]\.)))"
        r"|\u3000{2,}",
        lambda m: m.group(1) if m.group(1) else "______",
        text,
    )
    # 最后一步：拆掉标签内夹连续下划线的歧义形态（<u>i______</u> → <u>i</u>______）。
    # 只合并「拆分产生的 6+ 连续下划线」（如 <u>w</u>____________），不触碰形如
    # ____1____ 的编号空档、词间带空格的 ______ 或已保护的独立整行答题横线。
    text = _U_INLINE_RUN.sub(_split_inline_underline, text)
    text = re.sub(r"_{6,}", "______", text)
    if _FULL_LINE_SENTINEL in text:
        text = text.replace(_FULL_LINE_SENTINEL, FULL_LINE_BLANK)
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


def _attachment_name(entry: dict) -> str:
    return str(entry.get("name") or str(entry.get("url", "")).rsplit("/", 1)[-1] or "").strip()


def _safe_json(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def decorative_names(batch_dir, extraction=None) -> set[str]:
    """收集批次里的装饰图片文件名（花边图标、新考法/熟词生义胶囊、卷头横幅/水印与扫码图），统一过滤。"""
    names: set[str] = set()
    if extraction is None and batch_dir is not None:
        extraction = _safe_json(Path(batch_dir) / "01_source" / "extraction.json")
    if isinstance(extraction, dict):
        for media_entry in extraction.get("media") or []:
            if isinstance(media_entry, dict) and media_entry.get("decorative") and media_entry.get("name"):
                names.add(str(media_entry["name"]))
    if batch_dir is None:
        return names
    root = Path(batch_dir)
    protected: set[str] = set()
    confirmed = _safe_json(root / "01_source" / "structure.confirmed.json")
    if isinstance(confirmed, dict):
        for unit in confirmed.get("units") or []:
            if not isinstance(unit, dict):
                continue
            match = re.fullmatch(r"\[IMG\s+([^\]]+)\]", str(unit.get("stem") or "").strip())
            if match:
                protected.add(Path(match.group(1).strip()).name)
    from workflow import docx_extract
    body_lines: list[str] = []
    for rel in ("01_source/正文.txt", "01_source/原始正文.txt"):
        body_path = root / rel
        if not body_path.is_file():
            continue
        try:
            body_lines = body_path.read_text(encoding="utf-8-sig").splitlines()
        except OSError:
            continue
        break
    if body_lines:
        img_counts: dict[str, int] = {}
        for line in body_lines:
            if "[IMG" not in line:
                continue
            for match in IMAGE.finditer(line):
                nm = Path(match.group(1).strip()).name
                if nm:
                    img_counts[nm] = img_counts.get(nm, 0) + 1
        for nm in docx_extract.option_image_names(body_lines):
            if img_counts.get(nm, 0) == 1:
                protected.add(nm)
    meta = _safe_json(root / "01_source" / "metadata.json")
    if isinstance(meta, dict):
        for key in ("source", "paper_title", "evidence"):
            val = meta.get(key)
            texts = val if isinstance(val, list) else ([val] if isinstance(val, str) else [])
            for text in texts:
                if isinstance(text, str):
                    for match in IMAGE.finditer(text):
                        nm = Path(match.group(1).strip()).name
                        if nm not in protected:
                            names.add(nm)
    media_dir = root / "01_source" / "media"
    if media_dir.is_dir():
        for path in media_dir.iterdir():
            if (not path.is_file() or path.name in protected or path.name in names
                    or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}):
                continue
            dim = docx_extract.image_dimensions(path)
            if dim and docx_extract.is_decorative_media(dim[0], dim[1], path.stat().st_size, 1):
                names.add(path.name)
    # 卷头「扫码练听力」等扫码行紧邻的二维码图标同样不属于试题图片。
    head_lines = body_lines[:30]
    for idx, line in enumerate(head_lines):
        if re.search(r"扫码(?:练|听|查看|观看|获取)", line):
            window = head_lines[max(0, idx - 1):min(len(head_lines), idx + 2)]
            for wline in window:
                for match in IMAGE.finditer(wline):
                    nm = Path(match.group(1).strip()).name
                    if nm not in protected:
                        names.add(nm)
    # 受保护图片（纯图题干、选项位图）一律优先于旧 extraction.json 中的装饰标记。
    names -= protected
    return names


def replace_images(text, placement, option_key, media_map, attachments, *, decorative_names=None):
    if not isinstance(text, str) or not media_map:
        return text

    def repl(match):
        name = match.group(1).strip()
        url = media_map.get(name)
        if not url:
            return match.group(0)
        # 装饰花边图标（如 审题思路/熟词生义）、栏目胶囊（新考法）与页眉水印：
        # 在任意展示位（题干/材料/选项/解析）只剥离文本占位，不挂入正式附件。
        if decorative_names and name in decorative_names:
            return ""
        entry = {"kind": "image", "url": url, "name": name, "placement": placement}
        if option_key:
            entry["optionKey"] = option_key
        attachments.append(entry)
        return ""

    return IMAGE.sub(repl, text)


def normalize_item(item, media_map=None, meta=None, materials=None, audio=None, *, decorative_names=None):
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

    # Blank source fields are absence markers, not explicit values: drop them so
    # an optional field (year/location/version/...) is either a real value or
    # omitted, never an empty string the wire check must special-case.
    for key in SOURCE_KEYS:
        if isinstance(data.get(key), str) and not data[key].strip():
            data.pop(key, None)
            filters.pop(key, None)

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
    # 现有附件优先，避免既有附件与同归属的文本 [IMG] 各存一份导致重复；
    # 同时剔除装饰图标/横幅水印发出的陈旧附件。
    decor = {str(n) for n in (decorative_names or ()) if n}
    existing = [a for a in (data.get("attachments") or [])
                if isinstance(a, dict) and _attachment_name(a) not in decor]
    attachments = list(existing)
    # 题库只认 question/option/answer/explanation；材料里的图没有独立位置，
    # 上传后随题干显示（放在 material 位置会被服务端判 invalid_attachment）。
    for key, placement in (("stem", "question"), ("material", "question")):
        if isinstance(data.get(key), str):
            data[key] = replace_images(data[key], placement, None, media_map, attachments,
                                       decorative_names=decor or None)
    if isinstance(data.get("material"), str) and is_long_material_group(data):
        data["material"] = format_long_material_indent(data["material"])

    if isinstance(data.get("options"), list):
        result = []
        for option in data["options"]:
            # 选项可能是字符串（"A. text"）或结构化 {key,text}（图片前置选项题等）。
            # 后者以 key 作为 optionKey 绑定图片，再输出为字符串标签，保持既有形态。
            if isinstance(option, dict):
                key = str(option.get("key") or option.get("label") or "").strip().rstrip(".．、")
                text = replace_images(str(option.get("text") or ""), "option", key or None,
                                      media_map, attachments, decorative_names=decor or None)
                result.append(f"{key}. {text}".rstrip() if key else text)
                continue
            match = OPTION_PREFIX.match(option)
            if match:
                text = replace_images(option[match.end():], "option", match.group(1),
                                       media_map, attachments, decorative_names=decor or None)
                result.append(f"{match.group(1)}. {text}".rstrip())
            else:
                result.append(replace_images(option, "option", None, media_map, attachments,
                                             decorative_names=decor or None))
        data["options"] = result

    # 来源元信息字段（source/location/filterMetadata）不是题面正文字段：
    # 若旧批次卷头残留 [IMG ...] 图标锚点，仅剥离标记、绝不转成 placement=question 附件。
    for key in SOURCE_KEYS:
        if isinstance(data.get(key), str) and "[IMG" in data[key]:
            data[key] = _strip_metadata_img_tags(data[key])
    if filters:
        for key in SOURCE_KEYS:
            if isinstance(filters.get(key), str) and "[IMG" in filters[key]:
                filters[key] = _strip_metadata_img_tags(filters[key])
        data["filterMetadata"] = filters
    else:
        data.pop("filterMetadata", None)

    # 其余正文字段里的 [IMG] 标记（materialBlocks、解析、答案、大意等）一并转换；
    # 元信息字段由 _METADATA_IMAGE_SKIP 排除，不生成题目附件。
    if media_map:
        data = _walk_images(data, media_map, attachments, decorative_names=decorative_names)

    # 听力单元：装配时按 audio_map 关联已切分音频，避免上传时缺音频附件。
    if isinstance(audio, dict) and media_map:
        unit_id = data.get("unit_id") or data.get("id")
        filename = audio.get(unit_id)
        kind = str(data.get("type") or data.get("question_type") or "")
        if filename and kind.startswith("听力"):
            url = media_map.get(filename.split("/")[-1])
            if url and not any(a.get("kind") == "audio" for a in attachments):
                attachments.append({"kind": "audio", "url": url, "name": filename.split("/")[-1]})

    # 按媒体及显示归属去重，保持首次出现顺序，幂等。同图可同时属于不同选项或
    # question/answer/explanation；仅同一 placement/optionKey 内的重复引用合并。
    # 现有附件优先，避免既有附件与同归属的文本 [IMG] 各存一份导致重复。
    deduped = []
    seen_att = set()
    for a in attachments:
        name = a.get("name") or str(a.get("url", "")).rsplit("/", 1)[-1]
        kind = a.get("kind")
        key = (kind, name, a.get("placement"), a.get("optionKey"))
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
        data["subquestions"] = [normalize_item(child, media_map, meta, materials, audio, decorative_names=decorative_names) for child in children]
    return data


_TABLE_RE = re.compile(r"\[TABLE(?:\s+\d+)?\]([\s\S]*?)\[/TABLE\]", re.I)


def restore_table_cell_images(blocks, source_text, allowed_images=None):
    """Restore stripped `[IMG ...]` tags into table cells for local preview.

    Matches each `materialBlocks` table against `[TABLE ...]` blocks in `source_text`
    using both non-empty cell text overlap and (when provided) `allowed_images`
    overlap, then restores images via:
    1. Grid `(row, col)` alignment (accounting for `rowspan`/`colspan`), which
       restores pure-image cells whose text became `""` after `[IMG]` stripping
       as well as mixed text+image cells.
    2. Fallback exact non-empty cell-text match for tables whose row count differs.
    """
    if not isinstance(blocks, list) or not blocks or not isinstance(source_text, str) or "[IMG" not in source_text:
        return blocks

    allowed = None
    if allowed_images is not None:
        allowed = {str(n).strip().rsplit("/", 1)[-1] for n in allowed_images if str(n).strip()}
        if not allowed:
            return blocks

    def _norm(s):
        plain = IMAGE.sub("", str(s or ""))
        plain = re.sub(r"<br\s*/?>", "", plain, flags=re.I)
        return re.sub(r"[\s\u3000*]+", "", plain)

    def _cell_imgs(raw_cell):
        tags = []
        for m in IMAGE.finditer(raw_cell):
            fname = m.group(1).strip().rsplit("/", 1)[-1]
            if allowed is not None and fname not in allowed:
                continue
            tags.append(m.group(0))
        return tags

    def _apply_imgs(cell, raw_cell):
        imgs = _cell_imgs(raw_cell)
        if not imgs:
            return False
        cur = str(cell.get("text") or "")
        stripped_raw = raw_cell.strip()
        if stripped_raw.startswith("[IMG"):
            cell["text"] = "\n".join(imgs) + ("\n" + cur.lstrip("\n") if cur.strip() else "")
        else:
            cell["text"] = (cur.rstrip("\n") + "\n" if cur.strip() else "") + "\n".join(imgs)
        return True

    src_tables = []
    for match in _TABLE_RE.finditer(source_text):
        inner = match.group(1)
        if "[IMG" not in inner:
            continue
        lines = [ln for ln in inner.strip().splitlines() if ln.strip()]
        src_rows = [ln.split("|") if "|" in ln else [ln] for ln in lines]
        all_norms = set()
        img_cmap = {}
        img_names = set()
        for row in src_rows:
            for raw_cell in row:
                key = _norm(raw_cell)
                if key:
                    all_norms.add(key)
                if "[IMG" in raw_cell:
                    for m in IMAGE.finditer(raw_cell):
                        img_names.add(m.group(1).strip().rsplit("/", 1)[-1])
                    if key:
                        img_cmap[key] = raw_cell
        if img_names:
            src_tables.append({
                "rows": src_rows,
                "all_norms": all_norms,
                "img_cmap": img_cmap,
                "img_names": img_names,
            })
    if not src_tables:
        return blocks

    for blk in blocks:
        if not isinstance(blk, dict) or not isinstance(blk.get("rows"), list):
            continue
        if any("[IMG" in str(c.get("text") or "") for r in blk["rows"] for c in (r.get("cells") or []) if isinstance(c, dict)):
            continue
        blk_norms = {
            _norm(c.get("text"))
            for r in blk["rows"]
            for c in (r.get("cells") or [])
            if isinstance(c, dict) and _norm(c.get("text"))
        }
        best_tbl = None
        best_score = (0, 0, 0)
        for st in src_tables:
            img_overlap = len(st["img_names"] & allowed) if allowed is not None else 0
            if allowed is not None and img_overlap == 0:
                continue
            text_hits = sum(1 for n in blk_norms if n in st["all_norms"])
            shape_match = 1 if len(blk["rows"]) == len(st["rows"]) else 0
            # Match either by shared cell text, or for 100% pure-image tables by exact row count + item attachments
            if text_hits == 0 and not (not blk_norms and shape_match and allowed is not None and img_overlap > 0):
                continue
            score = (text_hits, img_overlap, shape_match)
            if score > best_score:
                best_score, best_tbl = score, st
        if not best_tbl:
            continue

        # Pass 1: exact (row, col) grid alignment (handles pure-image cells and merged cells)
        if len(blk["rows"]) == len(best_tbl["rows"]):
            occupied = set()
            for ri, r in enumerate(blk["rows"]):
                col = 0
                src_row = best_tbl["rows"][ri]
                for c in r.get("cells") or []:
                    if not isinstance(c, dict):
                        continue
                    while (ri, col) in occupied:
                        col += 1
                    rs = c.get("rowspan") if isinstance(c.get("rowspan"), int) and c.get("rowspan") > 0 else 1
                    cs = c.get("colspan") if isinstance(c.get("colspan"), int) and c.get("colspan") > 0 else 1
                    for dr in range(rs):
                        for dc in range(cs):
                            occupied.add((ri + dr, col + dc))
                    if col < len(src_row):
                        raw_cell = src_row[col]
                        if "[IMG" in raw_cell and _norm(raw_cell) == _norm(c.get("text")):
                            _apply_imgs(c, raw_cell)
                    col += cs

        # Pass 2: fallback exact non-empty text match for any remaining text+image cells
        if best_tbl["img_cmap"]:
            for r in blk["rows"]:
                for c in r.get("cells") or []:
                    if not isinstance(c, dict) or "[IMG" in str(c.get("text") or ""):
                        continue
                    key = _norm(c.get("text"))
                    if not key:
                        continue
                    raw_cell = best_tbl["img_cmap"].get(key)
                    if raw_cell:
                        _apply_imgs(c, raw_cell)
    return blocks
