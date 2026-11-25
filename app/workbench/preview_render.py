# -*- coding: utf-8 -*-
"""Source-paper preview, source block rendering, media/audio attachment & DOCX lookup.

Extracted from app/workbench/server.py to keep the HTTP server module focused.
All public symbols are re-exported by workbench.server for full compatibility.
"""
from __future__ import annotations

import re
import stat
from pathlib import Path

from workflow.workflow_primitives import read_json_safe as load


def _batch_dir(name: str) -> Path:
    from . import server
    return server.batch_dir(name)


def source_lines(folder):
    path = folder / "01_source" / "正文.txt"
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8-sig", errors="replace").splitlines()


def table_structure(folder):
    """Word 抽出的表格结构（含合并跨度），按表号索引，供预览沿用原表格网格。"""
    data = load(folder / "01_source" / "extraction.json", {})
    tables = {}
    for table in (data.get("tables") if isinstance(data, dict) else None) or []:
        if isinstance(table, dict) and table.get("number") is not None:
            tables[str(table["number"])] = table
    return tables


def _cell_text_images(raw):
    names = [Path(m.group(1).strip()).name for m in re.finditer(r"\[IMG\s+([^\]]+)\]", raw)]
    text = re.sub(r"\[IMG\s+[^\]]+\]", "", raw).strip()
    # 图片原本占位在文字之前/之后时，去掉留下的空行，避免单元格开头多一个换行。
    text = re.sub(r"^(?:<br\s*/?>\s*)+", "", text, flags=re.I)
    text = re.sub(r"(?:\s*<br\s*/?>)+$", "", text, flags=re.I)
    return text.strip(), names


def structured_rows(table, flat_rows):
    """结构化表格行（含 rowspan/colspan）；与正文网格不同形时返回 None。

    预览按 01_source/正文.txt 切片，只有 Word 表格的行数与每行列宽都和正文
    网格一致，才能用它的合并跨度渲染；否则退回按 `|` 切分的平面表格，不猜。
    """
    if not isinstance(table, dict):
        return None
    rows = table.get("rows")
    if not isinstance(rows, list) or not rows or len(rows) != len(flat_rows):
        return None
    structured = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            return None
        before, after = row.get("grid_before", 0), row.get("grid_after", 0)
        if any(type(value) is not int or not 0 <= value <= 1000 for value in (before, after)):
            return None
        cells = []
        width = before + after
        for _ in range(before):
            cells.append({"text": "", "images": [], "rowspan": 1, "colspan": 1})
        for cell in row.get("cells") or []:
            if not isinstance(cell, dict):
                return None
            colspan, rowspan = cell.get("colspan", 1), cell.get("rowspan", 1)
            if (type(colspan) is not int or colspan < 1
                    or type(rowspan) is not int or rowspan < 1):
                return None
            width += colspan
            if cell.get("merge_origin") is not None:
                continue
            text, names = _cell_text_images(str(cell.get("text") or "").replace("\n", "<br>"))
            cells.append({"text": text, "images": [{"name": name} for name in names],
                          "rowspan": rowspan, "colspan": colspan,
                          "header": bool(cell.get("header"))})
        for _ in range(after):
            cells.append({"text": "", "images": [], "rowspan": 1, "colspan": 1})
        if width != len(flat_rows[index]):
            return None
        structured.append({"cells": cells})
    return structured


def source_blocks(lines, tables=None, *, extraction=None):
    # Keep the original table-number mapping API and the newer full extraction
    # positional API. Native evidence must retain its full-text/reference checks.
    if extraction is None and isinstance(tables, dict) and any(
            key in tables for key in ("tables", "source_objects", "blocks", "source_sha256")):
        extraction, tables = tables, None
    tables = tables if isinstance(tables, dict) else {}

    def cell_content(text):
        names = [Path(m.group(1).strip()).name
                 for m in re.finditer(r"\[IMG\s+([^\]]+)\]", text)]
        return re.sub(r"\[IMG\s+[^\]]+\]", "", text).strip(), names

    def native_block(marker):
        if not isinstance(extraction, dict):
            return None
        from workflow.source_objects import resolve_table
        try:
            native, _ = resolve_table(extraction, marker)
        except (AttributeError, TypeError, ValueError):
            return None
        if native is None:
            return None
        rows, images, spans = [], [], []
        try:
            for row in native.get("rows") or []:
                cells, cell_images, cell_spans = [], [], []
                before, after = row.get("grid_before", 0), row.get("grid_after", 0)
                if any(type(n) is not int or not 0 <= n <= 1000 for n in (before, after)):
                    return None
                def append_cell(text, rowspan=1, colspan=1):
                    cleaned, names = cell_content(text)
                    cells.append(cleaned)
                    cell_images.append(names)
                    cell_spans.append({"rowspan": rowspan, "colspan": colspan})
                for _ in range(before):
                    append_cell("")
                for cell in row.get("cells") or []:
                    if cell.get("merge_origin") is not None:
                        continue
                    rs, cs = cell.get("rowspan", 1), cell.get("colspan", 1)
                    if any(type(n) is not int or not 1 <= n <= 1000 for n in (rs, cs)):
                        return None
                    append_cell(str(cell.get("text") or ""), rs, cs)
                for _ in range(after):
                    append_cell("")
                rows.append(cells)
                images.append(cell_images)
                spans.append(cell_spans)
        except (AttributeError, TypeError, ValueError):
            return None
        return {"type": "table", "rows": rows, "cellImages": images, "cellSpans": spans}

    blocks = []
    table = None
    table_images = []
    table_lines = []
    table_depth = 0
    table_number = None
    for line in lines:
        stripped = line.strip()
        # Count inline markers too: nested Word tables are serialized inside a
        # cell with <br>, and an inner closing marker cannot close its parent.
        openings = len(re.findall(r"\[TABLE(?:\s[^\]]*)?\]", line))
        closings = len(re.findall(r"\[/TABLE\]", line))
        if table is None and stripped.startswith("[TABLE"):
            table = []
            table_images = []
            match = re.match(r"\[TABLE(?:\s+(\d+))?\]", stripped)
            table_number = match.group(1) if match else None
            table_lines = [line]
            table_depth = openings - closings
            continue
        if table is not None:
            table_lines.append(line)
            table_depth += openings - closings
            if table_depth <= 0:
                # Bind complete text, never a table number alone: a stale table
                # must not lend its spans or cell boundaries to another source.
                block = native_block("\n".join(table_lines))
                if block is None:
                    block = {"type": "table", "rows": table, "cellImages": table_images}
                    # Explicit legacy mappings have no native reference contract.
                    # Never use them to bypass rejected extraction evidence.
                    if extraction is None:
                        structured = structured_rows(tables.get(str(table_number)), table)
                        if structured is not None:
                            block["structured"] = structured
                blocks.append(block)
                table = None
                table_images = []
                table_lines = []
                table_number = None
                continue
            # Legacy text has no merge evidence. Preserve all rows (including
            # blank single-cell rows), and keep each image with its own text.
            values = [cell_content(raw_cell) for raw_cell in line.split("|")]
            table.append([text for text, _ in values])
            table_images.append([names for _, names in values])
            continue
        cursor = 0
        for match in re.finditer(r"\[IMG\s+([^\]]+)\]", line):
            if match.start() > cursor:
                blocks.append({"type": "text", "text": line[cursor:match.start()]})
            blocks.append({"type": "image", "name": Path(match.group(1).strip()).name})
            cursor = match.end()
        if cursor < len(line):
            text = line[cursor:].strip()
            if text:
                blocks.append({"type": "text", "text": text})
    return blocks


def attach_media(blocks, folder, name):
    media_root = (folder / "01_source" / "media").resolve()

    def media_url(image_name):
        target = (media_root / image_name).resolve()
        ok = target.parent == media_root and target.is_file()
        return f"/api/batches/{name}/media/{image_name}" if ok else None

    for block in blocks:
        if block.get("type") == "image":
            block["url"] = media_url(block["name"])
            continue
        if block.get("type") == "table":
            grid = block.get("cellImages") or []
            block["cellImages"] = [
                [
                    [{"name": image_name, "url": media_url(image_name)} for image_name in cell]
                    for cell in row
                ]
                for row in grid
            ]
            for row in block.get("structured") or []:
                for cell in row.get("cells") or []:
                    cell["images"] = [{"name": image.get("name"), "url": media_url(image.get("name"))}
                                      for image in cell.get("images") or []]
    return blocks


AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac")


def audio_map(folder: Path) -> dict:
    """unit_id → 已切好的听力片段；读取 02_audio/audio_map.json，文件存在才纳入。"""
    path = folder / "02_audio" / "audio_map.json"
    mapping = {}
    from audio.consistency import pair_is_consistent, read_pair
    pair = read_pair(folder)
    if not pair_is_consistent(folder, pair):
        return mapping
    data = pair[0] or {}
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        unit = str(item.get("unit_id") or "").strip()
        rel = str(item.get("file") or "").strip()
        if not unit or not rel:
            continue
        target = (folder / rel).resolve()
        if target.parent == (folder / "02_audio").resolve() and target.is_file():
            mapping[unit] = {"name": target.name, "file": rel,
                             "start": item.get("start"), "end": item.get("end")}
    return mapping


def audio_blocks(folder: Path, name: str, unit_ids, *, mapping=None) -> list:
    """把单元的听力片段作为附件块，供题目详情播放与下载。"""
    if mapping is None:
        mapping = audio_map(folder)
    blocks = []
    for uid in unit_ids:
        info = mapping.get(str(uid))
        if not info:
            continue
        block = {"type": "audio", "name": info["name"], "label": "听力音频附件",
                 "url": f"/api/batches/{name}/audio/{info['name']}",
                 "start": info.get("start"), "end": info.get("end")}
        if block not in blocks:
            blocks.append(block)
    return blocks



def paper_uses_bulk_bold(lines):
    """Detect document-wide bold runs for display, without touching source data."""
    body = []
    for line in lines:
        text = re.sub(r'<(?:/?(?:u|sup|sub|em)|br\s*/?)>', '', line.strip(), flags=re.I)
        if not text or text.startswith(('[IMG ', '[TABLE ', '[/TABLE]')):
            continue
        body.append(text)
    return len(body) >= 20 and sum(
        bool(re.fullmatch(r'(?:\s*\*\*[^*]+\*\*\s*)+', line))
        for line in body
    ) >= 0.85 * len(body)


def _clean_preview_heading(text: str) -> str:
    s = re.sub(r"\[IMG\s+[^\]]+\]", "", str(text or ""), flags=re.I)
    s = re.sub(r"\*{2,}", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _extract_preview_section_meta(source_txt: str, sections: list) -> list[dict]:
    """Attach optional part header and section-level instruction from 正文.txt to each section."""
    if not sections:
        return []
    raw_lines = [ln.strip() for ln in str(source_txt or "").replace("\r\n", "\n").split("\n")]
    clean_lines = [_clean_preview_heading(ln) for ln in raw_lines]
    norm_lines = [re.sub(r"[\s\u3000]+", "", cl) for cl in clean_lines]
    part_re = re.compile(r"^第[一二三四五六七八九十\d]+部分")
    q_start_re = re.compile(r"^(?:[（(]\s*[)）]\s*)?\d{1,3}\s*[.．、)]")
    group_lead_re = re.compile(r"^(?:听下面|阅读(?:下列|下面)|根据(?:对话|短文)|[A-E]$)")
    sec_head_re = re.compile(r"^(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]|[IVX]{1,4}|[一二三四五六七八九十]+)\s*[.．、]")

    enriched = []
    cursor = 0
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        title = _clean_preview_heading(sec.get("title") or "")
        if not title:
            continue
        target_norm = re.sub(r"[\s\u3000]+", "", title)
        hit = -1
        for idx in range(cursor, len(norm_lines)):
            if norm_lines[idx] == target_norm or (target_norm and target_norm in norm_lines[idx]):
                hit = idx
                break
        part_title = ""
        instruction_lines = []
        if hit >= 0:
            cursor = hit + 1
            for back in range(hit - 1, max(-1, hit - 5), -1):
                if not clean_lines[back]:
                    continue
                if part_re.match(clean_lines[back]):
                    part_title = clean_lines[back]
                    break
                if sec_head_re.match(clean_lines[back]) or q_start_re.match(clean_lines[back]):
                    break
            if "书面表达" not in title:
                for fwd in range(hit + 1, min(len(raw_lines), hit + 8)):
                    raw_ln = raw_lines[fwd]
                    cl = clean_lines[fwd]
                    if not cl or cl == "扫码练听力":
                        continue
                    if "[TABLE" in raw_ln.upper():
                        break
                    if "[IMG" in raw_ln.upper():
                        continue
                    if not re.search(r"[\u4e00-\u9fff]", cl):
                        break
                    if (q_start_re.match(cl) or part_re.match(cl) or sec_head_re.match(cl)
                            or group_lead_re.match(cl)):
                        break
                    instruction_lines.append(cl)
        qs = [int(n) for n in (sec.get("questions") or []) if str(n).isdigit()]
        enriched.append({
            "title": title,
            "part": part_title,
            "instruction": "\n".join(instruction_lines).strip(),
            "questions": qs,
        })
    return enriched


def _clean_preview_band_title(text: str) -> str:
    s = _clean_preview_heading(text)
    while len(s) >= 2 and (
        (s[0] == "【" and s[-1] == "】")
        or (s[0] == "[" and s[-1] == "]")
        or (s[0] == "(" and s[-1] == ")")
        or (s[0] == "（" and s[-1] == "）")
    ):
        s = s[1:-1].strip()
    return s


def _extract_preview_lesson_bands(source_txt: str) -> list[tuple[int, str]]:
    """Extract lesson-practice band headers (e.g. 【一、A 基础巩固】/【二、B 能力提升】/【二、B 创新阅读专练】) with 1-based line numbers."""
    if not source_txt:
        return []
    bracket_re = re.compile(r"^(?:【[^】]{2,32}】|\[[^\]]{2,32}\]|（[^）]{2,32}）|\([^)]{2,32}\))$")
    bracket_skip_re = re.compile(r"^(?:答案|参考答案|解析|文章大意|听力原文|评分标准|提示|注意)$")
    plain_band_re = re.compile(
        r"^(?:(?:[一二三四五六七八九十]+[、.．]\s*)?(?:[A-Z]\s*[.．、]?\s*)?)?"
        r"(?:基础巩固|基础过关|基础达标|基础训练|能力提升|能力突破|能力训练|"
        r"创新性训练|创新训练|创新阅读专练|重要考点集训|考点集训|素养提升|"
        r"拓展提升|拓展训练|综合提升|综合训练|语篇提能|培优提升|思维拓展|达标训练|核心素养)$"
    )
    bands = []
    for idx, raw_ln in enumerate(str(source_txt).replace("\r\n", "\n").split("\n"), 1):
        cl = _clean_preview_heading(raw_ln)
        if not cl:
            continue
        stripped = _clean_preview_band_title(cl)
        if not stripped or bracket_skip_re.match(stripped):
            continue
        if (bracket_re.match(cl) and re.search(r"[\u4e00-\u9fff]", stripped)) or plain_band_re.match(stripped):
            bands.append((idx, stripped))
    return bands


def paper_preview(name: str) -> dict:
    """Return full-paper preview data (title, metadata tags, ordered assembled items)."""
    from workflow.metadata_policy import MISSING
    from workflow import wire_format
    folder = _batch_dir(name)
    batch = load(folder / "06_batch" / "batch_meta.json") or load(folder / "06_batch" / "batch.json") or {}
    meta = load(folder / "01_source" / "metadata.json", {}) or {}
    raw_items = batch.get("items") if isinstance(batch.get("items"), list) else []
    src_path = folder / "01_source" / "正文.txt"
    source_txt = src_path.read_text(encoding="utf-8", errors="replace") if src_path.is_file() else ""
    src_lines = source_txt.replace("\r\n", "\n").split("\n") if source_txt else []
    structure = load(folder / "01_source" / "structure.json", {}) or {}
    sec_list = _extract_preview_section_meta(source_txt, structure.get("sections") or [])
    lesson_bands = _extract_preview_lesson_bands(source_txt)
    num_to_sec = {}
    for sec in sec_list:
        for qn in sec["questions"]:
            num_to_sec.setdefault(qn, sec)

    items = []
    for raw_it in raw_items:
        if not isinstance(raw_it, dict):
            continue
        it = dict(raw_it)
        if source_txt and "[IMG" in source_txt and isinstance(it.get("materialBlocks"), list):
            att_imgs = None
            if isinstance(it.get("attachments"), list):
                att_imgs = {
                    str(a.get("name") or str(a.get("url") or "").rsplit("/", 1)[-1]).strip()
                    for a in it["attachments"]
                    if isinstance(a, dict) and a.get("kind") == "image"
                }
            wire_format.restore_table_cell_images(it["materialBlocks"], source_txt, allowed_images=att_imgs)
        if isinstance(it.get("material"), str) and wire_format.is_long_material_group(it):
            it["material"] = wire_format.format_long_material_indent(it["material"])
        existing_sec = it.get("source_section")
        if isinstance(existing_sec, dict) and str(existing_sec.get("title") or "").strip():
            sec_copy = dict(existing_sec)
            band_title = ""
            lb = sec_copy.get("lesson_band")
            if isinstance(lb, dict):
                sl = lb.get("start_line")
                if isinstance(sl, int) and 1 <= sl <= len(src_lines):
                    cand = _clean_preview_band_title(src_lines[sl - 1])
                    if cand:
                        band_title = cand
                if not band_title:
                    band_title = _clean_preview_band_title(lb.get("label") or "")
            elif lesson_bands:
                sec_start = sec_copy.get("start_line")
                if isinstance(sec_start, int):
                    for b_line, b_title in lesson_bands:
                        if b_line <= sec_start:
                            band_title = b_title
                        else:
                            break
            if band_title:
                sec_copy["band"] = band_title
            it["source_section"] = sec_copy
        else:
            first_num = None
            if str(it.get("number") or "").isdigit():
                first_num = int(it["number"])
            elif isinstance(it.get("numbers"), list):
                for n in it["numbers"]:
                    if str(n).isdigit():
                        first_num = int(n)
                        break
            if first_num is None and isinstance(it.get("subquestions"), list):
                for sub in it["subquestions"]:
                    if isinstance(sub, dict) and str(sub.get("number") or "").isdigit():
                        first_num = int(sub["number"])
                        break
            matched_sec = num_to_sec.get(first_num) if first_num is not None else None
            if matched_sec:
                inst = matched_sec["instruction"]
                stem_norm = re.sub(r"[\s\u3000]+", "", _clean_preview_heading(it.get("stem") or ""))
                inst_norm = re.sub(r"[\s\u3000]+", "", inst)
                if inst_norm and stem_norm and (inst_norm in stem_norm or stem_norm in inst_norm):
                    inst = ""
                it["source_section"] = {
                    "title": matched_sec["title"],
                    "part": matched_sec["part"],
                    "instruction": inst,
                }
        items.append(it)

    first = next((it for it in items if isinstance(it, dict)), {})
    filters = first.get("filterMetadata") if isinstance(first.get("filterMetadata"), dict) else {}
    title = str(batch.get("title") or meta.get("paper_title") or meta.get("title") or folder.name).strip()
    source_type = str(first.get("source_type") or filters.get("source_type") or batch.get("source_type") or meta.get("source_type") or "").strip()
    year = str(first.get("year") or filters.get("year") or batch.get("year") or meta.get("year") or "").strip()
    categories = filters.get("categories") or batch.get("categories") or meta.get("categories") or []
    if isinstance(categories, str):
        categories = [c.strip() for c in categories.split(",") if c.strip()]
    return {
        "title": title,
        "source_type": source_type,
        "year": year,
        "categories": [str(c) for c in categories if str(c).strip() and str(c).strip() != MISSING],
        "items": items,
        "has_source_docx": _locate_source_docx(folder) is not None,
    }


def _locate_source_docx(folder: Path) -> Path | None:
    """Locate the original .docx file inside a batch's attachments directory."""
    att_dir = (folder / "attachments").resolve()
    if not att_dir.is_dir():
        return None
    primary = (att_dir / "source.docx").resolve()
    if primary.parent == att_dir and primary.is_file():
        return primary
    for cand in sorted(att_dir.iterdir(), key=lambda p: p.name.lower()):
        if cand.name.startswith(".") or not cand.is_file():
            continue
        if cand.suffix.lower() == ".docx" and cand.resolve().parent == att_dir:
            return cand.resolve()
    return None


def read_source_docx(name: str) -> tuple[dict, bytes | None]:
    """Return metadata + raw bytes for the batch's original Word (.docx) file."""
    folder = _batch_dir(name)
    target = _locate_source_docx(folder)
    if target is None:
        return (
            {
                "ok": False,
                "kind": "missing",
                "reason": "该批次未附带 Word（.docx）原文件",
            },
            None,
        )
    data = target.read_bytes()
    stat = target.stat()
    display_name = target.name
    att_meta = load(folder / "attachments" / "batch.json", {}) or {}
    if isinstance(att_meta, dict):
        for key in ("original_name", "source_filename", "filename", "name"):
            val = str(att_meta.get(key) or "").strip()
            if val.lower().endswith(".docx"):
                display_name = Path(val).name
                break
    if display_name == target.name:
        meta = load(folder / "01_source" / "metadata.json", {}) or {}
        ev = meta.get("source_evidence") if isinstance(meta, dict) else None
        if isinstance(ev, dict):
            for info in ev.values():
                if isinstance(info, dict) and info.get("origin") == "filename":
                    txt = str(info.get("text") or "").strip()
                    if txt.lower().endswith(".docx"):
                        display_name = Path(txt).name
                        break
    return (
        {
            "ok": True,
            "filename": display_name,
            "size": len(data),
            "mtime": int(stat.st_mtime),
        },
        data,
    )


