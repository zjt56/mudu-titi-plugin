"""Source-bound question objects before labeling; never a completed batch.

The items use the assembled/wire field shapes. Answers and labels are deliberately
empty pending their own stages. Word text is authoritative; PDF OCR is fingerprinted
as separate layout evidence and never supplies wording. No manifest is read or changed.
"""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path

from . import denoise, question_content, wire_format

PATH = "06_batch/question-draft.json"
VERSION = "upload-workflow.question-draft/v1"
_INPUTS = ("01_source/正文.txt", "01_source/structure.confirmed.json",
           "01_source/structure.json", "01_source/extraction.json", "01_source/pdf-ocr.json")
_MARKUP = re.compile(r"</?(?:u|sup|sub)>|\*\*")
_TABLE = re.compile(r"\[TABLE(?:\s+(\d+))?\](.*?)\[/TABLE\]", re.S)
_INSTRUCTION = re.compile(
    r"^(?:听下面|听一段|听两遍|你将听到|阅读下列|阅读下面|根据对话内容|根据短文内容"
    r"|补全对话|从方框中选择|用所给词的适当形式|根据汉语意思|将下列句子)"
    r"|(?:选出最佳选项|将答案填写|答案写在)")


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _safe(root, relative):
    path = root / relative
    if not path.resolve().is_relative_to(root):
        raise ValueError("Question draft path escapes batch")
    for item in (path, *path.parents):
        if item == root:
            break
        if item.is_symlink():
            raise ValueError("Question draft paths cannot contain symlinks")
    if path.exists() and not path.is_file():
        raise ValueError("Question draft expects regular files")
    return path


def _hashes(root, media=()):
    result = {}
    for relative in (*_INPUTS, *sorted(set(media))):
        path = _safe(root, relative)
        result[relative] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"
    # Parser changes also invalidate a persisted draft, without mtime dependence.
    for name in ("question_draft.py", "question_content.py", "wire_format.py", "denoise.py", "question_identity.py", "shared_rubric.py", "phonetic_structure.py", "source_objects.py"):
        path = Path(__file__).with_name(name)
        result["parser:" + name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _json(root, relative, default=None):
    path = _safe(root, relative)
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else default


def _raw_fragment(raw, needle, after=0):
    """Recover original Word inline markup around an exact parsed fragment."""
    plain, offsets, cursor = [], [], 0
    for match in _MARKUP.finditer(raw):
        plain.extend(raw[cursor:match.start()])
        offsets.extend(range(cursor, match.start()))
        cursor = match.end()
    plain.extend(raw[cursor:])
    offsets.extend(range(cursor, len(raw)))
    plain = "".join(plain)
    start = plain.find(needle, after)
    if not needle:
        boundary = offsets[after] if after < len(offsets) else len(raw)
        blank = re.search(r"(?:<u>\s*</u>\s*)+$", raw[:boundary])
        return (blank.group(0).strip() if blank else ""), after
    if start < 0:
        return "", after
    end = start + len(needle)
    left = offsets[start - 1] + 1 if start else 0
    right = offsets[end] if end < len(offsets) else len(raw)
    # The source parser strips leading whitespace, including a Word blank's
    # contents. Recover its opening tag as well as the otherwise orphaned </u>.
    prefix = re.search(r"(?:<u>\s*</u>\s*)+$", raw[:offsets[start]])
    if prefix:
        left = min(left, prefix.start())
    return raw[left:right].strip(), end


def _image_prefixed_options(raw):
    """解析 `[IMG x]A. … [IMG y]B.` 前置图片选项排版，按顺序把图配到标签。

    仅在“选项标签组”整体为图片前置形态时生效（≥2 个 `[IMG]X.` 且标签连续），
    其余返回 None 交给原解析，避免影响普通文本选项、`A.[IMG]` 后置形态。
    返回 {"stem","options","found"} 或 None。
    """
    if not raw:
        return None
    # 逐对抓取 `[IMG ...]` 紧贴 `X.` 的形态。
    pairs = list(re.finditer(r"\[IMG\s+([^\]]+?)\]\s*([A-H])[.．、]", raw))
    if len(pairs) < 2:
        return None
    labels = [m.group(2) for m in pairs]
    # 标签必须从 A 起连续递增，且是这一段的选项组（不是分散的多题）。
    expected = list("ABCDEFGH"[:len(labels)])
    if labels != expected:
        return None
    # 同一行/相邻行的连续选项组：首尾跨度不应过大（防止把整卷多处 [IMG]A. 拼一起）。
    if pairs[-1].start() - pairs[0].start() > 600:
        return None
    stem = raw[:pairs[0].start()].strip()
    options = [{"key": m.group(2), "text": f"[IMG {m.group(1)}]"} for m in pairs]
    return {"stem": stem, "options": options, "found": True}


def _parts(lines, number, *, scoped=False):
    """Use the existing source parser, retaining Word markup rather than OCR."""
    if scoped:
        from .question_identity import numbered_projection
        lines = numbered_projection(lines)
    parsed = question_content.question_parts(lines, int(number))
    from .preview import _find
    start = _find(lines, int(number))
    # Restrict recovery to this question, not an identical phrase in shared material.
    raw = "\n".join(lines[start:]) if start is not None else ""
    # 图片选项前置排版 `[IMG x]A. [IMG y]B. …`：label 后的文本切割会错位（A 拿到下一张
    # 图、末项为空），图属于“紧贴其前的那个选项”。这里按原文顺序把“第 k 个 [IMG] → 第 k
    # 个选项标签”直接配对（视觉已确认这些是选项图），不走逐标签取文本的易错路径。
    img_pre = _image_prefixed_options(raw)
    if img_pre is not None:
        return img_pre
    stem, cursor = _raw_fragment(raw, parsed["stem"])
    options = []
    for option in parsed["options"]:
        # Include the label while locating the text to disambiguate repeated options.
        plain = _MARKUP.sub("", raw)
        label = re.search(r"(?<![A-Za-z0-9&/.\-])" + re.escape(option["label"]) + r"[.．、]\s*", plain[cursor:])
        offset = cursor + label.end() if label else cursor
        text, cursor = _raw_fragment(raw, option["text"], offset)
        options.append({"key": option["label"], "text": text})
    return {"stem": stem, "options": options, "found": parsed["found"]}


def _normalized_span(text, content):
    """Locate a whitespace-reflowed unit text and return its original line range.

    Confirmed structures from the model reflow source lines (option runs, tabs,
    full-width blanks) without changing any word. Binding may ignore layout
    whitespace only: never punctuation, digits, markup or letters.
    """
    def normalized(value):
        chars, index = [], []
        previous_space = False
        for position, char in enumerate(value):
            if char.isspace() or char in "\u3000\xa0":
                if previous_space:
                    continue
                chars.append(" ")
                previous_space = True
            else:
                chars.append(char)
                previous_space = False
            index.append(position)
        return "".join(chars), index

    plain, index = normalized(content)
    wanted = " ".join(text.split())
    if not wanted:
        return None
    start = plain.find(wanted)
    if start < 0 or plain.find(wanted, start + 1) >= 0:
        return None
    end = start + len(wanted) - 1
    raw_start, raw_end = index[start], index[end]
    line_start = content.rfind("\n", 0, raw_start) + 1
    line_end = content.find("\n", raw_end)
    if line_end < 0:
        line_end = len(content)
    # A source binding covers whole source lines, never just an option prefix
    # or a sentence fragment that hides the remainder of the same question.
    if content[line_start:raw_start].strip() or content[raw_end + 1:line_end].strip():
        return None
    return (content.count("\n", 0, raw_start) + 1,
            content.count("\n", 0, raw_end) + 1)


def _ordered_within(text, container):
    """Require complete equality after layout whitespace and material labels.

    Subsequence matching is unsafe: dropping 'not', an option, or a table cell
    would still match. Only standalone article labels removed by the independent
    slicer and whitespace may differ; table markup and every content character
    must remain identical.
    """
    from .extract import material_label
    def compact(value):
        lines, in_table = [], False
        for line in value.splitlines():
            if re.search(r"\[TABLE(?:\s|\])", line):
                in_table = True
            if in_table or not material_label(line):
                lines.append(line)
            if "[/TABLE]" in line:
                in_table = False
        return re.sub(r"\s+", "", "\n".join(lines))
    wanted, source = compact(text), compact(container)
    return bool(wanted) and wanted == source


def _locate_in_window(text: str, window: list[str], start_line: int):
    """Locate the unit text inside a declared span and return its exact range.

    The declared span may over-cover (section header, summary, next section) and
    may keep standalone article labels that the slicer drops. Accept only a
    unique contiguous match of the complete text after layout whitespace and
    material labels are ignored; a partial or subsequence match is rejected.
    """
    from .extract import material_label
    wanted = [line for line in text.splitlines() if line.strip()]
    if not wanted:
        return None
    plain = lambda rows: re.sub(r"\s+", "", "".join(rows))

    offsets = [k for k in range(len(window) - len(wanted) + 1)
               if "\n".join(window[k:k + len(wanted)]).strip() == text]
    if len(offsets) == 1:
        k = offsets[0]
        return start_line + k, start_line + k + len(wanted) - 1, False
    visible = [(index, line) for index, line in enumerate(window)
               if not material_label(line) and line.strip()]
    compact = [line for _, line in visible]
    found = [k for k in range(len(compact) - len(wanted) + 1)
             if plain(compact[k:k + len(wanted)]) == plain(wanted)]
    if len(found) == 1:
        k = found[0]
        return start_line + visible[k][0], start_line + visible[k + len(wanted) - 1][0], True
    # Line reflow is common between Word paragraphs and classified spans.
    # Compare the complete contiguous content while mapping back to whole
    # original lines; never accept a subsequence or a partial option prefix.
    span = _normalized_span(text, "\n".join(line for _, line in visible))
    if span is not None:
        first, last = span
        return start_line + visible[first - 1][0], start_line + visible[last - 1][0], True
    return None


def _bound_source(unit, source, cleaned, fresh):
    """Require exact source text, and prefer independently rebuilt boundaries."""
    text = str(unit.get("stem") or "").strip()
    span = unit.get("source_span") or {}
    lines = cleaned.splitlines()
    if "start_offset" in span or "end_offset" in span:
        start, end = span.get("start_line"), span.get("end_line")
        left, right = span.get("start_offset"), span.get("end_offset")
        if (type(start) is not int or start != end or not 1 <= start <= len(lines)
                or type(left) is not int or type(right) is not int
                or not 0 <= left < right <= len(lines[start - 1])):
            return "", {"reason": "同行题目的字符范围无效"}
        exact = lines[start - 1][left:right].strip()
        from .question_identity import source_key
        peers = [u for u in fresh if source_key(u) == source_key(unit)]
        if (exact != text or len(peers) != 1
                or peers[0].get("source_span") != span
                or str(peers[0].get("stem") or "").strip() != text):
            return "", {"reason": "同行题目字符范围与当前独立重建不一致"}
        return exact, {"basis": "source_character_window", "line_basis": "denoise.clean_body(01_source/正文.txt)",
                       "start_line": start, "end_line": end,
                       "start_offset": left, "end_offset": right, "boundary_rebuilt": True}
    # A grouped unit's material (article/table/rubric) lives between the section
    # start and its first question number. When the confirmed span is trusted,
    # bind the WHOLE span window as the source text so the leading material is
    # not dropped; the group branch in build() then splits it into material +
    # children. Restrict to groups: a standalone question owns only its own stem.
    if unit.get("unit_kind") == "group" and span.get("basis") in ("ai_classified", "cleaned_source"):
        start, end = span.get("start_line"), span.get("end_line")
        if type(start) is int and type(end) is int and 1 <= start <= end <= len(lines):
            window = lines[start - 1:end]
            located = _locate_in_window(text, window, start)
            if located is not None:
                first, last, skipped = located
                # Keep the full confirmed span window (material + questions);
                # locate only proves the stem is inside it, it must NOT shrink
                # the leading article/table material away.
                whole = "\n".join(lines[start - 1:end]).strip()
                proof = {"basis": "group_span_window",
                         "line_basis": "denoise.clean_body(01_source/正文.txt)",
                         "start_line": start, "end_line": end}
                if skipped:
                    proof["material_labels_skipped"] = True
                return whole, proof
    if span.get("basis") == "ai_classified":
        start, end = span.get("start_line"), span.get("end_line")
        if not (type(start) is int and type(end) is int and 1 <= start <= end <= len(lines)):
            return "", {"reason": "确认结构的行范围与当前Word正文不一致"}
        located = _locate_in_window(text, lines[start - 1:end], start)
        if located is None:
            return "", {"reason": "确认结构的行范围与当前Word正文不一致"}
        first, last, skipped = located
        proof = {"basis": "confirmed_source_span",
                 "line_basis": "denoise.clean_body(01_source/正文.txt)",
                 "start_line": first, "end_line": last}
        if skipped:
            proof["material_labels_skipped"] = True
        return text, proof
    from .question_identity import source_key
    matches = [u for u in fresh if source_key(u) == source_key(unit)
               and u.get("unit_kind") == unit.get("unit_kind")]
    if len(matches) == 1 and str(matches[0].get("stem") or "").strip() != text:
        return "", {"reason": "确认片段与当前原文独立切片不一致，不能沿用旧题干"}
    for basis, content in (("source_lines", source), ("cleaned_source_lines", cleaned)):
        rows = content.splitlines()
        wanted = text.splitlines()
        positions = [i for i in range(len(rows) - len(wanted) + 1)
                     if wanted and "\n".join(rows[i:i + len(wanted)]).strip() == text]
        if len(positions) == 1:
            start = positions[0]
            return text, {"basis": basis, "start_line": start + 1, "end_line": start + len(wanted),
                          "line_basis": ("01_source/正文.txt" if basis == "source_lines"
                                         else "denoise.clean_body(01_source/正文.txt)"),
                          "boundary_rebuilt": len(matches) == 1}
    # Legacy confirmed structures often have no span and reflowed whitespace.
    # Accept only a unique whitespace-insensitive location in the real text.
    for basis, content in (("source_lines", source), ("cleaned_source_lines", cleaned)):
        span = _normalized_span(text, content)
        if span is not None:
            return text, {"basis": basis, "start_line": span[0], "end_line": span[1],
                          "line_basis": ("01_source/正文.txt" if basis == "source_lines"
                                         else "denoise.clean_body(01_source/正文.txt)"),
                          "normalized_whitespace": True,
                          "boundary_rebuilt": len(matches) == 1}
    # Bind a supplied span only when its complete content matches after layout
    # whitespace and standalone article labels are removed. Never accept a
    # character subsequence: that could silently omit substantive source text.
    confirmed_span = unit.get("source_span") or {}
    start, end = confirmed_span.get("start_line"), confirmed_span.get("end_line")
    if type(start) is int and type(end) is int and 1 <= start <= end:
        for basis, content in (("source_lines", source), ("cleaned_source_lines", cleaned)):
            rows = content.splitlines()
            if end > len(rows) or not 1 <= start <= end:
                continue
            located = _locate_in_window(text, rows[start - 1:end], start)
            if located is None:
                continue
            first, last, skipped = located
            proof = {"basis": basis + "_span", "start_line": first, "end_line": last,
                     "line_basis": ("01_source/正文.txt" if basis == "source_lines"
                                    else "denoise.clean_body(01_source/正文.txt)"),
                     "boundary_rebuilt": len(matches) == 1}
            if skipped:
                proof["material_labels_skipped"] = True
            return text, proof
    return "", {"reason": "确认片段无法唯一回指当前Word正文"}


def _skeleton_text(text):
    """Normalize source layout blanks, retaining underlined nonblank words."""
    return wire_format.normalize_blank_text(re.sub(r"<br\s*/?>", "\n", text, flags=re.I))


INFO_TRANSFER_BLANK = re.compile(r"<u>\s*[\u3000\xa0]*\s*(\d{1,3})\s*[\u3000\xa0]*\s*</u>")


def _information_conversion(block_text: str, unit_type: str) -> bool:
    return "信息转换" in (unit_type or "")


def _table_blank_cells(rows, unit_type):
    """Rewrite source blank markers into the upload table format.

    The final upload contract expresses an information-transfer blank as
    ``__N__`` inside the table cell and requires the block variant. The source
    prints ``<u>　26　</u>``; copying that verbatim makes the table check fail, so
    only for that table type do we convert the marker while keeping cell text.
    """
    if not _information_conversion("", unit_type):
        return rows
    converted = []
    for row in rows:
        cells = []
        for cell in (row.get("cells") if isinstance(row, dict) else row) or []:
            if not isinstance(cell, dict):
                cells.append(cell)
                continue
            text = cell.get("text")
            if isinstance(text, str):
                cell = dict(cell, text=INFO_TRANSFER_BLANK.sub(lambda m: f"__{m.group(1)}__", text))
            cells.append(cell)
        converted.append({"cells": cells})
    return converted


def _source_table_rows(table, *, include_omitted=True):
    """Keep Word's explicitly omitted edge columns in the wire grid.

    gridBefore/gridAfter are empty layout positions, not missing text. The wire
    format has no row-offset fields, so encode those positions as empty cells.
    Existing vertical-merge continuations stay implicit via their origin spans.
    """
    rows = []
    for row in table.get("rows") or []:
        cells = []
        before, after = row.get("grid_before", 0), row.get("grid_after", 0)
        if any(type(value) is not int or not 0 <= value <= 1000 for value in (before, after)):
            raise ValueError("Invalid Word table omitted-column evidence")
        if include_omitted:
            cells.extend({"text": "", "rowspan": 1, "colspan": 1} for _ in range(before))
        for cell in row.get("cells") or []:
            if cell.get("merge_origin") is not None:
                continue
            value = {k: cell[k] for k in ("text", "rowspan", "colspan", "header") if k in cell}
            if isinstance(value.get("text"), str):
                value["text"] = _skeleton_text(value["text"])
            cells.append(value)
        if include_omitted:
            cells.extend({"text": "", "rowspan": 1, "colspan": 1} for _ in range(after))
        rows.append({"cells": cells})
    return rows


def _table_grid_equal(left, right):
    """Compare serialized grid rows, allowing only layout whitespace changes."""
    def lines(text):
        cleaned = []
        for line in str(text or "").splitlines():
            line = line.replace("\xa0", " ").replace("\u3000", " ")
            line = re.sub(r"\s+", " ", line).strip()
            cleaned.append(re.sub(r"\s*\|\s*", "|", line))
        return cleaned
    left_lines, right_lines = lines(left), lines(right)
    return bool(left_lines) and left_lines == right_lines


def _table_grid_matches(table, flat_rows, *, native=False):
    """Word 网格须与正文同形；已验证原生对象按真实单元格序列化核对。"""
    grid = table.get("grid")
    if not isinstance(grid, list) or not grid or not all(isinstance(row, list) for row in grid):
        # 旧协议/人工元数据没有逐格网格证据时只按文本判定，不额外猜形状。
        return True
    if native:
        # resolve_table has already verified the object identity/content digest.
        # A literal pipe or nested-table marker belongs to its native cell, so
        # splitting those strings on pipes would invent columns. Require the
        # complete native grid serialization instead; never waive shape checks.
        serialized = [" | ".join(str(value).replace("\n", "<br>") for value in row) for row in grid]
        return (len(serialized) == len(flat_rows)
                and _table_grid_equal("\n".join(["[TABLE]", *serialized, "[/TABLE]"]),
                                      "\n".join(["[TABLE]", *flat_rows, "[/TABLE]"])))
    flat_widths = [len(line.split("|")) for line in flat_rows]
    return len(grid) == len(flat_widths) and [len(row) for row in grid] == flat_widths


def _table_blocks(text, extraction, issues, uid, unit_type="", *, references=None, layout_diagnostics=None):
    from .source_objects import resolve_table, capabilities, table_markers
    blocks = []
    for marker in table_markers(text):
        match = _TABLE.fullmatch(marker)
        table, reason = resolve_table(extraction, marker)
        grid_text = re.sub(r"^[ \t\r]*\n", "", match.group(2), count=1)
        grid_text = re.sub(r"\n[ \t\r]*$", "", grid_text, count=1)
        # Remove only the marker-boundary newlines. Empty physical rows (even
        # at either edge) are real native grid positions and must survive.
        flat_rows = grid_text.split("\n")
        native = isinstance(extraction, dict) and "source_objects" in extraction
        if table is not None and not _table_grid_matches(table, flat_rows, native=native):
            table = None
            reason = "Word合并单元格网格与原表格形状不一致"
        rows = []
        if table is not None:
            rows = _source_table_rows(table)
            if references is not None:
                references.append({"source_ref": table.get("source_ref"),
                                   "table_number": table["number"],
                                   "binding": "native_object" if native else "legacy_exact_content"})
            if layout_diagnostics is not None:
                layout_diagnostics.extend(dict(problem, unit_id=uid, stage="format_gate")
                                          for problem in capabilities(table))
        else:
            # Physical newlines inside an underline belong to its cell, not to
            # a new table row. Keep them through row splitting, then normalize.
            grid = re.sub(r"<u>.*?</u>", lambda m: m.group(0).replace("\n", "<br>"),
                          match.group(2), flags=re.S)
            for line in grid.splitlines():
                if "|" in line:
                    rows.append({"cells": [{"text": _skeleton_text(cell.strip()), "items": []}
                                           for cell in line.split("|")]})
            issues.append({"unit_id": uid, "code": "TABLE_LAYOUT_UNVERIFIED",
                           "reason": "保留正文表格网格；" + reason})
            if layout_diagnostics is not None and native:
                layout_diagnostics.append({"unit_id": uid, "stage": "format_gate",
                                           "code": "LAYOUT_SOURCE_OBJECT_INVALID", "reason": reason})
        converted = _table_blank_cells(rows, unit_type)
        block = {"type": "table", "rows": converted}
        if _information_conversion(text, unit_type):
            block["variant"] = "information-conversion"
        blocks.append(block)
    return blocks


def _rubric_line(line, unit_type):
    """A real作答导语 line, never material, a numbered题号 or article prose."""
    text = line.strip()
    if not text:
        return False
    from .preview import _strong_number
    from .extract import material_label
    if _strong_number(line) is not None or material_label(line):
        return False
    from .extract import _plain
    plain = question_content.SECTION_LEAD.sub("", _plain(text).strip(), count=1)
    if question_content._generic_choice_instruction(line, unit_type):
        return True
    kind = str(unit_type or "")
    if "任务型阅读" in kind or "回答问题" in kind:
        return bool(re.search(r"阅读下面短文|回答问题|词数要求", plain))
    if "补全对话" in kind:
        return bool(re.search(r"补全对话|方框中选择|对话内容", plain))
    if kind:
        return bool(_INSTRUCTION.match(plain))
    return False


def _rubric_lines(lines, unit_type):
    """Return the leading verbatim section rubric, if the source states one.

    Only recognized answer instructions qualify; an article's opening sentence
    must stay in the shared material rather than become a fake stem.
    """
    rubric = []
    for line in lines:
        text = line.strip()
        if not text or not _rubric_line(line, unit_type):
            break
        rubric.append(line)
    return rubric


def _section_rubric(lines, unit):
    from .shared_rubric import section_rubric
    return section_rubric(lines, (unit.get("source_span") or {}).get("start_line"),
                          unit.get("type") or "")


def _standalone_marked_heading(unit, proof, source, cleaned_lines, fresh):
    """Recover a marked heading only for a fully rebuilt unnumbered section."""
    from .question_identity import VERSION as IDENTITY_VERSION
    from .sync_practice import source_marks
    span = unit.get("source_span") or {}
    if (unit.get("identity_version") != IDENTITY_VERSION
            or span.get("number_binding") not in {"unnumbered_section", "single_writing_section"}
            or span.get("printed_numbers") != []):
        return ""
    peers = [row for row in fresh if row.get("unit_id") == unit.get("unit_id")
             and row.get("section_id") == unit.get("section_id")
             and row.get("unit_kind") == "standalone"]
    if len(peers) != 1:
        return ""
    peer = peers[0]
    section = unit.get("source_section") or {}
    if (section != peer.get("source_section") or span != peer.get("source_span")
            or str(unit.get("stem") or "").strip() != str(peer.get("stem") or "").strip()):
        return ""
    rows = source.splitlines() if proof.get("line_basis") == "01_source/正文.txt" else cleaned_lines
    start, end = proof.get("start_line"), proof.get("end_line")
    heading_line, section_end = section.get("start_line"), section.get("end_line")
    if (not all(type(value) is int for value in (start, end, heading_line, section_end))
            or not 1 <= heading_line <= start <= end <= section_end <= len(rows)
            or (start, end) != (span.get("start_line"), span.get("end_line"))):
        return ""
    heading = rows[heading_line - 1].strip()
    if heading != str(section.get("title") or "").strip() or not source_marks(heading):
        return ""
    return heading


def _empty(number):
    return {"number": int(number), "stem": "", "material": "", "options": [],
            "knowledge": [], "knowledgeIds": [], "knowledge_ids": [],
            "answer": "", "explanation": "无", "articleSummary": "无", "subquestions": []}


def build(batch_dir, *, units=None):
    """Build a candidate only. Missing source scopes stay blank with explicit issues.

    The draft is a pure function of the source/confirmed files and the unit list;
    scheduling and semantic-signature checks rebuild it repeatedly per refresh.
    Cache by those exact inputs so a real edit still recomputes.
    """
    root = Path(batch_dir).resolve()
    if units is None:
        from .agent_tasks import confirmed_units
        units = confirmed_units(root)

    source = _safe(root, "01_source/正文.txt").read_text(encoding="utf-8-sig")
    confirmed = _json(root, "01_source/structure.confirmed.json", {})
    structure = _json(root, "01_source/structure.json", {})
    extraction = _json(root, "01_source/extraction.json", {})
    cleaned, _, _ = denoise.clean_body(source)
    cleaned_lines = cleaned.splitlines()
    from .structure_gate import _build_units
    from .preview import _find, _strong_number, _weak_numbers
    from . import lesson_bands
    evidence = lesson_bands.for_text(root, cleaned)
    sections = lesson_bands.annotate_sections(structure.get("sections") or [], evidence)
    fresh = _build_units(sections, structure.get("groups") or [], cleaned.splitlines())
    items, bindings, issues, media = [], {}, [], set()
    layout_diagnostics = []
    inspected_media = {}
    for unit in units:
        uid = unit["unit_id"]
        text, proof = _bound_source(unit, source, cleaned, fresh)
        row = dict(_empty(unit["numbers"][0]), id=uid, unit_id=uid, unit_kind=unit["unit_kind"],
                   numbers=[int(n) for n in unit["numbers"]], question_type="",
                   filterMetadata={"subtypes": []})
        proof.update(source="01_source/正文.txt", source_text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())
        bindings[uid] = proof
        if not text:
            issues.append({"unit_id": uid, "code": "SOURCE_SCOPE_UNPROVEN", "reason": proof["reason"]})
        else:
            lines = text.splitlines()
            audit = question_content.audit_source_unit(dict(unit, stem=text, numbers=[int(n) for n in unit["numbers"]]))
            issues.extend(dict(problem, unit_id=uid) for problem in audit["issues"])
            if unit["unit_kind"] == "standalone":
                if (unit.get("source_span") or {}).get("number_binding") == "single_writing_section":
                    row["stem"] = question_content._required_stem(text)
                else:
                    part = _parts(lines, unit["numbers"][0], scoped=unit.get('identity_version') == 'scoped-v1')
                    row.update(stem=part["stem"], options=part["options"])
                    # Listening word-choice items may print only a number and
                    # options. Retain that actual source token as the field's
                    # display text rather than inventing an unheard sentence.
                    if not part['stem'] and part['options'] and unit.get('type') == '听力关键词语选择':
                        printed = re.match(r'^\s*(?:[（(]\s*[）)]\s*)?(\d{1,3}[.．、)])', text)
                        if printed and int(re.match(r'\d+', printed.group(1)).group()) == unit['numbers'][0]:
                            row['stem'] = printed.group(1)
                    # Keep unsplit source rather than losing a nonstandard numbered task.
                    if not part["stem"] and not part["options"]:
                        row["stem"] = text
                heading = _standalone_marked_heading(unit, proof, source, cleaned_lines, fresh)
                if heading and heading not in row["stem"].splitlines():
                    row["stem"] = heading + "\n" + row["stem"]
                    proof["section_heading_rubric"] = heading
            else:
                first = min((i for i, line in enumerate(lines) if _strong_number(line) is not None), default=len(lines))
                option_start = question_content.option_block_start(lines) if audit["shared_options"] else None
                # The section rubric ("阅读下列短文…" / "听下面一段对话…") is a real
                # source line and belongs in the group's stem; leaving it empty
                # makes every grouped question fail the upload stem requirement.
                rubric = _rubric_lines(lines, unit.get("type") or "")
                own_rubric = bool(rubric)
                if not rubric:
                    # 完形 A/B、阅读各篇共用同一大题题干：后一篇的绑定窗口只含
                    # 自己的文章，必须补回同大题的原文导语，不能留空。
                    # 补入的导语不属于本组切片，绝不能据此截断材料正文。
                    from .shared_rubric import section_rubric, phonetic_section_rubric
                    rubric_source = (source.splitlines() if proof.get("line_basis") == "01_source/正文.txt"
                                     else cleaned_lines)
                    rubric = (phonetic_section_rubric(rubric_source, unit, band_evidence=evidence)
                              or section_rubric(rubric_source, proof.get("start_line"), unit.get("type") or ""))
                    if rubric:
                        proof["shared_rubric"] = rubric
                if not rubric:
                    # 本节标题行本身即作答指令且携带原卷提示标记（如
                    # 「Ⅰ.[跨学科]在下列四线三格中写出…」「Ⅲ.阅读理解[2025·滁州天长市月考]」）
                    # 时，必须原样留在题组作答指令里（硬规则 25：大题标记留在所属题组作答
                    # 指令）。这类组的 span 常从首题开始、标题行落在窗口之外，若不补回，
                    # 标记只存在于 source_section 元数据，D27/B9 判定为未交付。
                    heading = str((unit.get("source_section") or {}).get("title") or "").strip()
                    if heading:
                        from .sync_practice import source_marks as _source_marks
                        if _source_marks(heading):
                            rubric = [heading]
                            proof["section_heading_rubric"] = heading
                if rubric:
                    row["stem"] = "\n".join(rubric).strip()
                offset_lines = len(rubric) if own_rubric else 0
                body_lines = lines[offset_lines:] if offset_lines else lines
                body_first = max(0, first - offset_lines)
                if option_start is not None:
                    line, offset = option_start
                    line -= offset_lines
                    row["material"] = "\n".join([*body_lines[:line], body_lines[line][:offset]]).strip()
                    raw_options = "\n".join([body_lines[line][offset:], *body_lines[line + 1:]])
                    cursor = 0
                    plain_options = _MARKUP.sub("", raw_options)
                    for option in audit["shared_options"]:
                        label = re.search(r"(?<![A-Za-z0-9&/.\-])" + re.escape(option["label"])
                                          + r"[.．、]\s*", plain_options[cursor:])
                        offset = cursor + label.end() if label else cursor
                        value, cursor = _raw_fragment(raw_options, option["text"], offset)
                        # 清除 OCR/Markdown 星号与行内空白，保留原文标点字词。
                        value = re.sub(r"\*{1,3}", "", value).strip()
                        row["options"].append({"key": option["label"], "text": value})
                else:
                    row["material"] = "\n".join(body_lines[:body_first]).strip()
                for number in unit["numbers"]:
                    part = _parts(lines, number, scoped=unit.get('identity_version') == 'scoped-v1')
                    unnumbered = (unit.get('source_span') or {}).get('number_binding') == 'unnumbered_section'
                    if unnumbered:
                        part = {'stem': '', 'options': [], 'found': True}
                    elif audit['shared_options']:
                        # Dialogue blanks are already printed in the shared text.
                        part = {'stem': '______', 'options': [], 'found': True}
                    child = _empty(number)
                    child.update(stem=part["stem"], options=part["options"])
                    if not child["stem"] and any(int(number) in _weak_numbers(line) for line in lines[:first]):
                        child["stem"] = "______"  # Explicit blank exists in shared source, not generated prose.
                    elif not unnumbered and not child["stem"] and not child["options"]:
                        issues.append({"unit_id": uid, "number": number, "code": "CHILD_CONTENT_UNRESOLVED",
                                       "reason": "保留子题身份；当前原文不能可靠拆出独立题干"})
                    row["subquestions"].append(child)
            table_refs = []
            blocks = _table_blocks(text, extraction, issues, uid, unit.get("type") or "",
                                   references=table_refs, layout_diagnostics=layout_diagnostics)
            if table_refs:
                proof["source_objects"] = table_refs
            if blocks:
                if "信息转换" in str(unit.get("type") or ""):
                    for block in blocks:
                        block["variant"] = "information-conversion"
                row["materialBlocks"] = blocks
            # Bindings above retain the original Word text; only the generated
            # skeleton adopts the library's blank and line-break notation.
            for item in (row, *row["subquestions"]):
                for key in ("stem", "material"):
                    item[key] = _skeleton_text(item[key])
                for option in item["options"]:
                    option["text"] = _skeleton_text(option["text"])
            refs = set(question_content._image_names(lines))
            media_map = {}
            for name in sorted(refs):
                if Path(name).name != name or "\\" in name:
                    issues.append({"unit_id": uid, "code": "MEDIA_PATH_UNSAFE", "reason": "图片引用不是安全文件名"})
                    continue
                relative = "01_source/media/" + name
                path = _safe(root, relative)
                media.add(relative)
                digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"
                if relative in inspected_media and inspected_media[relative] != digest:
                    raise ValueError("Media changed during question draft build")
                inspected_media[relative] = digest
                proof.setdefault("media_hashes", {})[relative] = digest
                if digest != "missing":
                    media_map[name] = relative  # Local evidence only; never a deployed/verified URL.
                else:
                    issues.append({"unit_id": uid, "code": "MEDIA_MISSING", "reason": "原文图片文件缺失：" + name})
            row = wire_format.normalize_item(row, media_map=media_map, meta=confirmed.get("meta") or {})
        # Cover every child even when its source scope is unresolved.
        if unit["unit_kind"] == "group" and not row["subquestions"]:
            row["subquestions"] = [_empty(n) for n in unit["numbers"]]
        from .question_identity import fields, question_key, VERSION as IDENTITY_VERSION
        row.update(fields(unit))
        if unit.get('type') == '语音题' and unit.get('identity_version') == 'scoped-v1':
            row['source_span'] = dict(unit.get('source_span') or {})
        if (unit.get('source_span') or {}).get('printed_numbers') == []:
            row['printed_numbers'] = []
            row['source_span'] = dict(unit['source_span'])
        if unit.get('identity_version') == IDENTITY_VERSION:
            row['question_id'] = question_key(unit, unit['numbers'][0]) if unit['unit_kind'] == 'standalone' else ''
            for child in row.get('subquestions') or []:
                child['question_id'] = question_key(unit, child['number'])
        items.append(row)
        proof["object_sha256"] = _digest(row)
    hashes = _hashes(root, media)
    if any(hashes.get(path) != digest for path, digest in inspected_media.items()):
        raise ValueError("Media changed during question draft build")
    result = {"version": VERSION, "status": "candidate", "release_ready": False,
              "label_status": "not_started", "media_status": "local_evidence_only_unverified",
              "input_hashes": hashes, "items": items, "source_bindings": bindings,
              "issues": issues, "layout_diagnostics": layout_diagnostics, "notice": "来源绑定只证明机械回指；不代表结构、标签、视觉/听音、装配或审核通过。空答案及无解析/大意是待匹配占位，不证明原卷没有。"}
    return result


def _equivalent_parser_refresh(previous, current):
    """Retain an honest old receipt only after rebuilding equal source objects.

    Parser provenance records which code produced the persisted draft. It must
    not be rewritten just to acknowledge newer code with identical output: that
    would invalidate unrelated tasks holding the original bytes. Source/media
    hashes, parser key sets, objects and all diagnostics still compare exactly.
    """
    if not isinstance(previous, dict):
        return False
    old = dict(previous)
    receipt = old.pop("content_sha256", None)
    if receipt != _digest(old):
        return False
    new = dict(current)
    new.pop("content_sha256", None)
    old_hashes, new_hashes = old.get("input_hashes"), new.get("input_hashes")
    if not isinstance(old_hashes, dict) or not isinstance(new_hashes, dict) or old_hashes.keys() != new_hashes.keys():
        return False
    if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
           for key, value in old_hashes.items() if key.startswith("parser:")):
        return False
    old["input_hashes"] = {k: v for k, v in old_hashes.items() if not k.startswith("parser:")}
    new["input_hashes"] = {k: v for k, v in new_hashes.items() if not k.startswith("parser:")}
    return old == new


def ensure(batch_dir, *, units=None):
    """Persist deterministically, leaving unchanged drafts untouched and no manifest edits."""
    root = Path(batch_dir).resolve()
    target = _safe(root, PATH)
    before = _hashes(root)
    result = build(root, units=units)
    after = _hashes(root)
    if before != after:
        raise ValueError("Source changed during question draft build")
    result["content_sha256"] = _digest(result)
    content = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    persisted = target.read_bytes() if target.is_file() else None
    if persisted == content.encode("utf-8"):
        return result
    # Inputs, including media, must still match before retaining or committing
    # a candidate. Do not make an equivalence decision on a changing source.
    media = [key for key in result["input_hashes"] if key.startswith("01_source/media/")]
    if result["input_hashes"] != _hashes(root, media):
        raise ValueError("Source or media changed during question draft build")
    if persisted is not None:
        try:
            previous = json.loads(persisted.decode("utf-8"))
        except (UnicodeError, ValueError):
            previous = None
        if _equivalent_parser_refresh(previous, result):
            # Keep both original provenance and exact original serialization.
            # A concurrent real change is never replaced by the stale receipt.
            if target.read_bytes() != persisted:
                raise ValueError("Question draft changed during equivalence check")
            return previous
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", prefix=".question-draft-", suffix=".tmp",
                                         dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
        _safe(root, PATH)
        temporary.replace(target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return result
