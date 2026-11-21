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


def normalize_blank_text(text):
    """库侧空档写法：<u>　26　</u> → __26__，纯空档 → ______。

    连续多段下划线属于同一处空档（题号 + 两侧留白），必须先整体识别再归一。
    <u>词______</u> 这种标签内夹连续下划线的歧义形态会拆成 <u>词</u>______，
    避免下游宽松解析器在 <u> 内再套 <u> 渲染成双线（用户 2026-11-19 反馈）。
    """
    if not isinstance(text, str):
        return text
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
    # ____1____ 的编号空档或词间带空格的 ______。
    text = _U_INLINE_RUN.sub(_split_inline_underline, text)
    text = re.sub(r"_{6,}", "______", text)
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
        from workflow import docx_extract
        for path in media_dir.iterdir():
            if (not path.is_file() or path.name in protected or path.name in names
                    or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}):
                continue
            dim = docx_extract.image_dimensions(path)
            if dim and docx_extract.is_decorative_media(dim[0], dim[1], path.stat().st_size, 1):
                names.add(path.name)
    # 卷头「扫码练听力」等扫码行紧邻的二维码图标同样不属于试题图片。
    for rel in ("01_source/正文.txt", "01_source/原始正文.txt"):
        body_path = root / rel
        if not body_path.is_file():
            continue
        try:
            head_lines = body_path.read_text(encoding="utf-8-sig").splitlines()[:30]
        except OSError:
            continue
        for idx, line in enumerate(head_lines):
            if re.search(r"扫码(?:练|听|查看|观看|获取)", line):
                window = head_lines[max(0, idx - 1):min(len(head_lines), idx + 2)]
                for wline in window:
                    for match in IMAGE.finditer(wline):
                        nm = Path(match.group(1).strip()).name
                        if nm not in protected:
                            names.add(nm)
        break
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


def restore_table_cell_images(blocks, source_text):
    """Restore stripped `[IMG ...]` tags into table cells by exact cell-text match against source text."""
    if not isinstance(blocks, list) or not blocks or not isinstance(source_text, str) or "[IMG" not in source_text:
        return blocks
    def _norm(s):
        plain = IMAGE.sub("", str(s or ""))
        plain = re.sub(r"<br\s*/?>", "", plain, flags=re.I)
        return re.sub(r"[\s\u3000*]+", "", plain)

    table_maps = []
    for match in _TABLE_RE.finditer(source_text):
        if "[IMG" not in match.group(1):
            continue
        cells = []
        for line in match.group(1).strip().splitlines():
            cells.extend(line.split("|") if "|" in line else ([line] if line.strip() else []))
        cmap = {}
        for raw_cell in cells:
            if "[IMG" in raw_cell:
                key = _norm(raw_cell)
                if key:
                    cmap[key] = raw_cell
        if cmap:
            table_maps.append(cmap)
    if not table_maps:
        return blocks

    for blk in blocks:
        if not isinstance(blk, dict) or not isinstance(blk.get("rows"), list):
            continue
        if any("[IMG" in str(c.get("text") or "") for r in blk["rows"] for c in (r.get("cells") or []) if isinstance(c, dict)):
            continue
        best_map = None
        best_hits = 0
        for cmap in table_maps:
            hits = sum(
                1 for r in blk["rows"] for c in (r.get("cells") or [])
                if isinstance(c, dict) and _norm(c.get("text")) in cmap
            )
            if hits > best_hits:
                best_hits, best_map = hits, cmap
        if not best_map or best_hits == 0:
            continue
        for r in blk["rows"]:
            for c in r.get("cells") or []:
                if not isinstance(c, dict):
                    continue
                raw_cell = best_map.get(_norm(c.get("text")))
                if not raw_cell:
                    continue
                imgs = [m.group(0) for m in IMAGE.finditer(raw_cell)]
                if not imgs:
                    continue
                cur = str(c.get("text") or "")
                stripped_raw = raw_cell.strip()
                if stripped_raw.startswith("[IMG"):
                    c["text"] = "\n".join(imgs) + ("\n" + cur.lstrip("\n") if cur.strip() else "")
                else:
                    c["text"] = (cur.rstrip("\n") + "\n" if cur.strip() else "") + "\n".join(imgs)
    return blocks
