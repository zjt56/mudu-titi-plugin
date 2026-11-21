# -*- coding: utf-8 -*-
"""结构自动闸门：去噪 → 元信息抽取 → 按题型结构化 → 机器自检。

替代原 `structure_gate` 的人工确认：拆分、去噪、按题型成组都由机器完成，
人只在机器报出「不确定/漏题/题组边界歧义」时才介入。

产出两份文件：
- `01_source/structure.confirmed.json`：确认后的结构化视图（题目边界、单元类型、
  题号覆盖、元信息），下游 types/knowledge/assemble 以此为唯一输入；
- `07_review/structure-gate.json`：闸门报告（逐项检查、阻断项、unresolved）。

关键口径（与 docs/structure.md 一致）：
- 独立题：一题一单元，逐题呈现；
- 组合题（完形/阅读/任务型阅读/听力信息转换/补全对话等）：整组是一个单元，
  以题组形式呈现，共享材料只出现一次。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path

from . import denoise
from .extract import _plain, guide_numbers, material_label, source_group_partitions

# 组合题家族：这些题型整组作为一个单元，而不是拆成逐题
GROUP_TYPE_HINTS = (
    "完形", "阅读理解", "阅读", "任务型", "信息转换", "补全对话", "语法选择",
    "选词", "短文填空", "教材语篇", "听力长对话", "听力短文", "听力信息",
)
# 独立题家族：逐题一单元
STANDALONE_TYPE_HINTS = (
    "单项填空", "听力短对话", "关键词语选择", "单词拼写", "完成句子", "语音", "翻译",
    "改错", "情景运用", "书面表达",
)

QUESTION_START = re.compile(r"^\s*(?:[(（]\s*[)）]\s*)?(\d{1,3})\s*[.．]")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig") if path.is_file() else ""


def _section_type_label(type_name: str) -> str:
    if not isinstance(type_name, str) or not type_name.strip():
        return "未判定题型"
    return type_name.strip()


def is_group_type(type_name: str) -> bool | None:
    """判断某题型是否应成组。返回 True/False，无法判断返回 None（交人工）。"""
    name = _section_type_label(type_name)
    for hint in GROUP_TYPE_HINTS:
        if hint in name:
            return True
    for hint in STANDALONE_TYPE_HINTS:
        if hint in name:
            return False
    return None


def _expand_question_numbers(entry) -> list[int]:
    """把 sections[].questions 的一项展开成题号列表，支持单题或 [start, end]。"""
    if isinstance(entry, list) and len(entry) == 2:
        try:
            start, end = int(entry[0]), int(entry[1])
            return list(range(start, end + 1))
        except (ValueError, TypeError):
            return []
    if isinstance(entry, int):
        return [entry]
    if isinstance(entry, str) and entry.strip().isdigit():
        return [int(entry.strip())]
    return []


def _section_numbers(section: dict) -> list[int]:
    """Return question numbers owned by this section, excluding material digits.

    Composite extraction can also record blank markers from a table/article in
    ``question_locations``.  For a section that has real question anchors,
    those anchors are authoritative; otherwise (for blank-only sections such
    as information transfer) the recorded blank locations remain the source.
    Inline dialogue slots (``A:Hello,I am Tony.1.______``) are printed child
    numbers too, but they are recorded as blank markers; when the section's own
    recorded ``questions`` list covers every anchor, that list is the section's
    full printed sequence and supplies the missing inline slots.
    """
    locations = section.get("question_locations")
    if isinstance(locations, list):
        question_numbers = [row.get("number") for row in locations
                            if isinstance(row, dict) and row.get("kind") == "question"
                            and type(row.get("number")) is int and row.get("number") >= 1]
        if question_numbers:
            owned = set(question_numbers)
            recorded = sorted({n for entry in section.get("questions") or []
                               for n in _expand_question_numbers(entry)})
            # 补全对话把空位号印在行内（`A:Hello,I am Tony.1.______`），任务型阅读/
            # 读写综合也常在同一题组中混排“行首题号 + 表格/摘要内空位号（如 6.________）”，
            # 在 question_locations 里记作 kind="blank"，与行首题号同属一个题组。
            # 提取侧记录的 sections[].questions 覆盖了全部行首锚点时，它就是该
            # 分区的题号全集；只取行首锚点会把行内/表格空位号漏成未声明题号。
            if (recorded and owned.issubset(recorded)
                    and (_section_type_label(section.get("type")) in {
                        "补全对话", "任务型阅读", "读写综合", "短文填空",
                        "选词填空", "教材语篇填空", "阅读理解", "听力信息转换",
                    } or recorded == list(range(min(recorded), max(recorded) + 1)))):
                owned |= set(recorded)
            return sorted(owned)
    return sorted({n for entry in section.get("questions") or []
                   for n in _expand_question_numbers(entry)})


def _scope_windows(sections: list[dict], source_lines: list[str], visual_columns=None) -> list[dict]:
    """Resolve section occurrences by source position, never by printed numbers."""
    from . import sync_practice
    headings = sync_practice.heading_candidates("\n".join(source_lines))
    if headings:
        known = {row["start_line"] for row in headings}
        headings.extend(dict(s, start_line=s["source_line"], end_line=s.get("source_end_line") or len(source_lines))
                        for s in sections if type(s.get("source_line")) is int and s["source_line"] not in known)
        headings.sort(key=lambda s: s["start_line"])
        for index, heading in enumerate(headings[:-1]):
            heading["end_line"] = min(heading["end_line"], headings[index + 1]["start_line"] - 1)
    if not headings:
        headings = [dict(s, start_line=s.get("source_line")) for s in sections
                    if type(s.get("source_line")) is int]
        headings.sort(key=lambda s: s["start_line"])
        for index, heading in enumerate(headings):
            heading["end_line"] = min(heading.get("source_end_line") or len(source_lines),
                                      headings[index + 1]["start_line"] - 1 if index + 1 < len(headings) else len(source_lines))
    rows = []
    for heading in headings:
        start, end = heading["start_line"], heading["end_line"]
        matching = [s for s in sections if s.get("source_line") == start]
        section = matching[0] if len(matching) == 1 else {}
        if type(section.get("lesson_container_end_line")) is int:
            end = min(end, section["lesson_container_end_line"])
        if section.get("lesson_band"):
            end = min(end, section.get("source_end_line") or end, section["lesson_band"]["end_line"])
        stops = [(c.get("heading_line") or c["start_line"]) - 1 for c in visual_columns or []
                 if c.get("role") == "question" and c.get("printed_numbers") == []
                 and c["start_line"] > start and c.get("column_id") != section.get("visual_column_id")]
        if stops:
            end = min(end, min(stops))
        rows.append({**section, **heading, "source_line": start, "source_end_line": end,
                     "type": section.get("type") or heading.get("type"),
                     "questions": section.get("questions") or []})
    return rows


def _inline_unnumbered_writing(section, lines):
    from .sync_practice import writing_prompt
    return (_section_type_label(section.get("type")) == "书面表达"
            and not _section_numbers(section)
            and writing_prompt(lines[section["source_line"] - 1]))


def _scope_anchors(lines: list[str]) -> list[tuple[int, int]]:
    """Printed item anchors, including dialogue slots and same-line exercises.

    空位号识别统一走 `preview._weak_numbers`，避免各处各写一套正则。
    Cloze/短文填空 材料行内的 `<u>　n　</u>` 是编号空格（子题号本体），先按库侧
    空档写法规范化再扫锚点，让 `__n__` 不再被“数字+句点”误判成印刷题号，也不会
    与下方 `( )n.` 选项行并列重复计数。
    """
    from .preview import _strong_number, _weak_numbers
    from .wire_format import normalize_blank_text
    strong, weak = [], []
    for index, line in enumerate(lines):
        normalized = _anchor_text(normalize_blank_text(line))
        from .source_anchors import printed_tokens
        nums = [number for _offset, number in printed_tokens(normalized)]
        first = _strong_number(normalized)
        if first is not None and first not in nums:
            nums.insert(0, first)
        strong.extend((index, number) for number in nums)
        weak.extend((index, number) for number in _weak_numbers(normalized))
    printed = {number for _, number in strong}
    return strong + [row for row in weak if row[1] not in printed]


# 表格/图片 HTML（`<table>…`、`img_in_image_box_566_714_638_765.jpg`）里的坐标数字、
# 温度/日期数字是版面数据，不是印刷题号。题号锚点在扫描前先剥掉这些标记与文件名，
# 避免 `.jpg` 前的坐标被“数字+句点”误识别成小题号，或温度数字被当成空位号。
# 与提取侧共用 source_anchors.strip_visual_markup，避免两处各自写一套。
def _anchor_text(line: str) -> str:
    from .source_anchors import strip_visual_markup
    return strip_visual_markup(line)


def _source_members(lines: list[str]) -> set[int]:
    return {number for _, number in _scope_anchors(lines)}


def _material_windows(lines: list[str], start: int, end: int) -> list[tuple[int, int]]:
    """Each explicit article/answering lead-in owns a separate source window."""
    from .extract import material_start
    boundaries = sorted({material_start(lines, i) if material_label(lines[i]) else i
                         for i in range(start - 1, end) if material_label(lines[i]) or guide_numbers(lines[i])})
    # Adjacent instruction and A/B markers describe the same article.
    merged = []
    for boundary in boundaries:
        if merged and not _source_members(lines[merged[-1] + 1:boundary]):
            continue
        merged.append(boundary)
    if not merged:
        return [(start, end)]
    return [(boundary + 1, merged[index + 1] if index + 1 < len(merged) else end)
            for index, boundary in enumerate(merged)]


def _visual_column_for_unit(unit, columns, lines):
    """Match one whole unnumbered task against already source-verified evidence.

    视觉理解已停用，columns 恒为空；保留该函数仅为兼容旧调用签名。
    """
    span = unit.get("source_span") or {}
    matches = [c for c in columns or [] if c.get("column_id") == unit.get("column_id")
               and c.get("role") == "question" and c.get("printed_numbers") == []
               and type(c.get("start_line")) is int and type(c.get("end_line")) is int
               and 1 <= c["start_line"] <= c["end_line"] <= len(lines)
               and (span.get("start_line"), span.get("end_line")) == (c["start_line"], c["end_line"])]
    if len(matches) != 1 or unit.get("numbers") != [1]:
        return None
    column = matches[0]
    chunk = lines[column["start_line"] - 1:column["end_line"]]
    if (_source_members(chunk) or not any(line.strip() for line in chunk)
            or unit.get("stem") != "\n".join(chunk).strip()):
        return None
    return column


def _merge_visual_units(fresh, units, columns, lines, sections=None):
    """Keep printed sources; replace only fully proven visual task windows."""
    from .question_identity import assign_scoped
    visual = [u for u in units or [] if _visual_column_for_unit(u, columns, lines)]
    if not visual:
        return fresh
    ranges = [(u["source_span"]["start_line"], u["source_span"]["end_line"]) for u in visual]
    retained = []
    for unit in fresh:
        span = unit.get("source_span") or {}
        lo, hi = span.get("start_line", 0), span.get("end_line", 0)
        overlaps = [(a, b) for a, b in ranges if max(lo, a) <= min(hi, b)]
        if overlaps:
            # Existing numbered content cannot disappear behind a new no-number
            # claim. An enclosing unnumbered fallback may be refined by evidence.
            if _source_members(lines[lo - 1:hi]) or not all(lo <= a <= b <= hi for a, b in overlaps):
                return fresh
        else:
            retained.append(unit)
    rows = sorted(retained + visual, key=lambda u: (u["source_span"]["start_line"], u["source_span"].get("start_offset", 0)))
    try:
        return assign_scoped(rows, lines, sections=sections, visual_columns=columns)
    except ValueError:
        return fresh


def _scoped_source_errors(units: list[dict], sections: list[dict], lines: list[str], visual_columns=None) -> list[dict]:
    """Reconcile each printed occurrence with exactly one local source owner."""
    from .question_identity import validate_units
    errors, anchors = [], []
    try:
        if any(u.get("identity_version") != "scoped-v1" for u in units) or not validate_units(units):
            errors.append({"code": "QUESTION_IDENTITY_INVALID", "message": "单元身份字段缺失、重复或混用"})
    except ValueError as exc:
        errors.append({"code": "QUESTION_IDENTITY_INVALID", "message": str(exc)})
    spans = [(u.get("source_span", {}).get("start_line", 0), u.get("source_span", {}).get("end_line", 0)) for u in units]
    def disjoint_columns(left, right):
        a, b = left.get("source_span", {}), right.get("source_span", {})
        return (a.get("start_line") == a.get("end_line") == b.get("start_line") == b.get("end_line")
                and all(type(s.get(k)) is int for s in (a, b) for k in ("start_offset", "end_offset"))
                and min(a["end_offset"], b["end_offset"]) <= max(a["start_offset"], b["start_offset"]))
    for index, (start, end) in enumerate(spans):
        if any(max(start, lo) <= min(end, hi) and not disjoint_columns(units[index], units[prior])
               for prior, (lo, hi) in enumerate(spans[:index])):
            errors.append({"code": "SOURCE_WINDOWS_OVERLAP", "source_line": start,
                           "message": "不同来源单元窗口重叠，无法证明重复题号各自独立"})
    windows = _scope_windows(sections, lines, visual_columns)
    for section in windows:
        start, end = section["source_line"], section["source_end_line"]
        group = section.get("kind") == "group" if section.get("kind") else is_group_type(_section_type_label(section.get("type")))
        declared = set(_section_numbers(section))
        if group:
            numbers = declared or sorted(_source_members(lines[start:end]))
            articles = _material_windows(lines, start + 1, end)
            if articles and numbers:
                # 与 _build_scoped_units 同一归一化：选项字母不能切裂题组窗口，
                # 否则这里会拿伪窗口复核出 GROUP_CROSSES_SECTION/QUESTION_NOT_FOUND。
                articles = _numbered_windows(articles, lines, numbers, start, end)
        else:
            articles = [(start + 1, end)]
        if not any((start <= u.get("source_span", {}).get("start_line", 0)
                    if (u.get("source_span") or {}).get("number_binding") == "single_writing_section"
                    else start < u.get("source_span", {}).get("start_line", 0))
                   and u.get("source_span", {}).get("start_line", 0) <= u.get("source_span", {}).get("end_line", 0) <= end
                   for u in units):
            # 图片理解能力受限不等于可以漏掉整节；仍必须保留绑定原文的来源单元。
            errors.append({"code": "SOURCE_SECTION_UNCOVERED", "source_line": start,
                           "message": "原文大题窗口未产生任何来源单元"})
        if _inline_unnumbered_writing(section, lines):
            from .sync_practice import writing_prompt_end
            tail_len = writing_prompt_end(lines[start:end])
            expected_start = start + 1 if tail_len > 0 else start
            task_end = start + tail_len
            owners = [u for u in units
                      if expected_start <= (u.get("source_span") or {}).get("start_line", 0) <= end]
            if (len(owners) != 1 or owners[0].get("unit_kind") != "standalone"
                    or owners[0]["source_span"].get("printed_numbers") != []
                    or owners[0]["source_span"]["start_line"] != expected_start
                    or owners[0]["source_span"].get("end_line") != task_end):
                errors.append({"code": "SOURCE_WRITING_WINDOW_MISMATCH", "source_line": start,
                               "message": "无号作文须单独覆盖写作指令后的作答窗口"})
            # Numbers inside the task are instructions, e.g. 注意:1. … 2. …
            continue
        observed = _source_members(lines[start:end])
        unnumbered_standalone = False
        if not group and not declared:
            # 独立无号文字作答题（如熟词生义引用完形原句含 __<u>1</u>__）没有印刷小题号，
            # 不把句内引用的弱空位号当成印刷题号阻塞无号作答区重建。
            from .preview import _strong_number
            if not any(_strong_number(line) is not None for line in lines[start:end]):
                observed = set()
                unnumbered_standalone = True
        if not group and not observed:
            from .question_content import unnumbered_text_responses
            for lo, hi in unnumbered_text_responses(lines[start:end]):
                expected_span = (start + lo + 1, start + hi + 1)
                owners = [u for u in units if
                          ((u.get('source_span') or {}).get('start_line'),
                           (u.get('source_span') or {}).get('end_line')) == expected_span
                          and (u.get('source_span') or {}).get('number_binding') == 'unnumbered_section']
                if len(owners) != 1:
                    errors.append({'code': 'SOURCE_UNNUMBERED_RESPONSE_UNCOVERED', 'source_line': expected_span[0],
                                   'message': '当前无号作答区域须完整且唯一归属，不能遗漏、截断或合并相邻题'})
        writing_bound = {n for u in units if u.get("source_span", {}).get("number_binding") == "single_writing_section"
                         and start <= u["source_span"]["start_line"] <= u["source_span"]["end_line"] <= end for n in u["numbers"]}
        if declared - observed - writing_bound:
            errors.append({"code": "SOURCE_NUMBERS_MISSING", "source_line": start,
                           "message": f"本大题声明题号未在局部原文定位：{sorted(declared - observed - writing_bound)}"})
        for lo, hi in articles:
            raw_anchors = [] if unnumbered_standalone else _scope_anchors(lines[lo - 1:hi])
            # A cloze/material blank (weak anchor) and its explicit question
            # line can carry the same number.  The explicit line is the unique
            # source owner; retain weak-only anchors when no strong occurrence
            # exists, and keep duplicate strong anchors as a real defect.
            from .preview import _strong_number
            from .source_anchors import printed_tokens
            strong_numbers = set()
            for at, number in raw_anchors:
                source_line = lines[lo + at - 1]
                if printed_tokens(_anchor_text(source_line)) or _strong_number(source_line) is not None:
                    strong_numbers.add(number)
            local_anchors = [(lo + at, number) for at, number in raw_anchors
                             if number not in strong_numbers
                             or printed_tokens(_anchor_text(lines[lo + at - 1]))
                             or _strong_number(lines[lo + at - 1]) is not None]
            counts = Counter(number for _, number in local_anchors)
            duplicate = sorted(number for number, count in counts.items() if count > 1)
            if duplicate:
                errors.append({"code": "SOURCE_DUPLICATE_NUMBERS", "source_line": lo,
                               "message": f"同一来源材料窗口内题号重复：{duplicate}"})
            anchors.extend(local_anchors)
            first_anchor = min([at for at, _ in local_anchors] or [hi + 1])
            prefix_lines = [at for at in range(lo, min(first_anchor, hi + 1)) if lines[at - 1].strip()]
            for unit in units:
                span = unit.get("source_span") or {}
                if (unit.get("unit_kind") == "group" and lo <= span.get("start_line", 0) <= span.get("end_line", 0) <= hi
                        and prefix_lines and span["start_line"] > prefix_lines[0]):
                    errors.append({"code": "SOURCE_MATERIAL_OMITTED", "source_line": prefix_lines[0],
                                   "message": "题组窗口遗漏本材料题号前的原文/表格/导语"})
            for at, number in local_anchors:
                owners = [u for u in units if number in u.get("numbers", []) and
                          u.get("source_span", {}).get("start_line", 0) <= at <= u.get("source_span", {}).get("end_line", 0)]
                if len(owners) != 1:
                    errors.append({"code": "SOURCE_NUMBERS_UNDECLARED" if not owners else "DUPLICATE_NUMBERS",
                                   "source_line": at, "number": number,
                                   "message": f"原文第{at}行题号{number}须唯一归属，当前{len(owners)}个单元"})
            for at in range(lo - 1, hi):
                refs = guide_numbers(lines[at])
                if refs and not any(set(u.get("numbers", [])) == set(refs) and
                                    lo <= u.get("source_span", {}).get("start_line", 0) <= at + 1 <=
                                    u.get("source_span", {}).get("end_line", 0) <= hi for u in units):
                    errors.append({"code": "GUIDE_GROUP_MISMATCH", "source_line": at + 1,
                                   "message": f"局部分段导语题号未完整绑定：{lines[at].strip()}"})
        for unit in units:
            span = unit.get("source_span") or {}
            lo, hi = span.get("start_line", 0), span.get("end_line", 0)
            if start < lo <= end and not any(a <= lo <= hi <= b for a, b in articles):
                errors.append({"code": "GROUP_CROSSES_SECTION", "source_line": lo,
                               "message": "单元来源窗口跨大题或跨独立材料"})
    for unit in units:
        span = unit.get("source_span") or {}
        lo, hi = span.get("start_line", 0), span.get("end_line", 0)
        if any(c.get("role") in {"guidance", "extension"}
               and c["start_line"] <= lo <= hi <= c["end_line"] for c in visual_columns or []):
            errors.append({"code": "COLUMN_ROLE_INVALID", "source_line": lo,
                           "message": "栏目提示或父题附属设问不得作为独立来源单元"})
        for column in visual_columns or []:
            if (column.get("role") == "question" and column.get("printed_numbers") == []
                    and max(lo, column["start_line"]) <= min(hi, column["end_line"])
                    and not _visual_column_for_unit(unit, [column], lines)):
                errors.append({"code": "VISUAL_QUESTION_BOUNDARY_INVALID", "source_line": lo,
                               "column_id": column["column_id"],
                               "message": "来源单元跨入或截断了整页已证明的独立无号题窗口"})
        for number in unit.get("numbers", []):
            if span.get("number_binding") in ("single_writing_section", "unnumbered_section"):
                if unit.get("column_id") and not _visual_column_for_unit(unit, visual_columns, lines):
                    errors.append({"code": "UNNUMBERED_BINDING_INVALID", "source_line": lo,
                                   "message": "无号栏目题缺少当前整页视觉来源证明"})
                continue
            if (span.get("number_binding") == "answer_inferred_image_group"
                    and span.get("printed_numbers") == []
                    and span.get("inferred_numbers") == list(unit.get("numbers") or [])
                    and span.get("inferred_evidence")):
                continue
            if sum(1 for at, n in anchors if n == number and lo <= at <= hi) != 1:
                errors.append({"code": "QUESTION_NOT_FOUND", "number": number, "source_line": lo,
                               "message": f"单元题号{number}在本来源窗口没有唯一实际位置"})
        if (not _visual_column_for_unit(unit, visual_columns, lines)
                and not any(((s["source_line"] <= lo)
                             if span.get("number_binding") == "single_writing_section"
                             else (s["source_line"] < lo))
                            and lo <= hi <= s["source_end_line"] for s in windows)):
            errors.append({"code": "GROUP_CROSSES_SECTION", "source_line": lo,
                           "message": "单元未绑定唯一大题来源窗口"})
    for column in visual_columns or []:
        if column.get("role") != "question" or column.get("printed_numbers") != []:
            continue
        owners = [u for u in units if _visual_column_for_unit(u, [column], lines)]
        if len(owners) != 1:
            errors.append({"code": "VISUAL_QUESTION_UNCOVERED", "source_line": column["start_line"],
                           "column_id": column["column_id"],
                           "message": "整页证实的无号作答单元必须唯一完整归属，不能漏题或借相邻题覆盖"})
    return errors


def _proven_unnumbered_scope(unit: dict, sections: list[dict], lines: list[str], visual_columns=None) -> bool:
    from .question_identity import assign_scoped, validate_units
    if unit.get("identity_version") != "scoped-v1" or not validate_units([unit]):
        return False
    if unit.get("column_id"):
        if not _visual_column_for_unit(unit, visual_columns, lines):
            return False
        bare = {k: v for k, v in unit.items() if k not in
                {"identity_version", "section_id", "unit_id", "question_ids", "source_section"}}
        try:
            expected = assign_scoped([bare], lines, sections=sections, visual_columns=visual_columns)[0]
        except ValueError:
            return False
        return unit.get("section_id") == expected["section_id"] and unit.get("source_section") == expected["source_section"]
    bare = {k: v for k, v in unit.items() if k not in
            {"identity_version", "section_id", "unit_id", "question_ids", "source_section"}}
    try:
        expected = assign_scoped([bare], lines, sections=sections)[0]
    except ValueError:
        return False
    span, section = unit.get("source_span") or {}, expected["source_section"]
    if (unit.get("section_id") != expected["section_id"] or unit.get("source_section") != section
            or unit.get("stem") != "\n".join(lines[span["start_line"] - 1:span["end_line"]]).strip()):
        return False
    if (span.get("start_line") == section["start_line"] + 1
            and span.get("end_line") == section["end_line"]):
        return True
    from .question_content import unnumbered_text_responses
    windows = unnumbered_text_responses(lines[section["start_line"]:section["end_line"]])
    return any((span.get("start_line"), span.get("end_line")) ==
               (section["start_line"] + lo + 1, section["start_line"] + hi + 1) for lo, hi in windows)


def _proven_scoped_restart(at: int, units: list[dict], sections: list[dict], lines: list[str], problems: list[dict], visual_columns=None) -> bool:
    """Resolve only a restart whose current section occurrences have local proof."""
    from .question_identity import assign_scoped, validate_units
    windows = _scope_windows(sections, lines, visual_columns)
    target = next((s for s in windows if s["source_line"] == at), None)
    if target is None:
        return False
    members = _source_members(lines[at:target["source_end_line"]])
    peers = [s for s in windows if s["source_line"] < at
             and members.intersection(_source_members(lines[s["source_line"]:s["source_end_line"]]))]
    if not members or not peers:
        return False
    for section in [*peers, target]:
        start, end = section["source_line"], section["source_end_line"]
        local = [u for u in units if start < (u.get("source_span") or {}).get("start_line", 0)
                 <= (u.get("source_span") or {}).get("end_line", 0) <= end]
        if not local or not validate_units(local):
            return False
        if any(type(p.get("source_line")) is int and start <= p["source_line"] <= end for p in problems):
            return False
        # Reconstruct occurrence/context from current headings, without trusting
        # the retained IDs or a stale source_section supplied by a candidate.
        bare = [{k: v for k, v in u.items() if k not in
                 {"identity_version", "section_id", "unit_id", "question_ids", "source_section"}} for u in local]
        try:
            expected = assign_scoped(bare, lines, sections=sections, visual_columns=visual_columns)
        except ValueError:
            return False
        if any(u.get("identity_version") != "scoped-v1" or u.get("section_id") != proof["section_id"]
               or u.get("source_section") != proof["source_section"] for u, proof in zip(local, expected)):
            return False
    return True


def _numbered_windows(windows: list[tuple[int, int]], lines: list[str],
                      numbers: list[int], start: int, end: int) -> list[tuple[int, int]]:
    """Keep option letters from splitting a numbered group into fake windows.

    `material_label` cannot tell an article label (`A`/`B` 篇文章) from an option
    label (`A.`/`B.` 选项), so a question whose options sit on their own lines
    looks like a new article and yields a window without any printed number.
    Such windows belong to the neighbouring numbered window; when no window owns
    a printed number the whole section is one complete group window.
    """
    enriched: list[list[int]] = []
    pending = None
    for lo, hi in windows:
        if pending is not None:
            lo, pending = pending, None
        if _source_members(lines[lo - 1:hi]).intersection(numbers):
            enriched.append([lo, hi])
        elif enriched:
            enriched[-1][1] = hi
        else:
            pending = lo
    if pending is not None:
        if not enriched:
            return [(start + 1, end)]
        enriched[0][0] = pending
    return [(lo, hi) for lo, hi in enriched]


def _build_scoped_units(sections: list[dict], groups: list[dict], source_lines: list[str], visual_columns=None, *, batch_dir=None) -> list[dict]:
    from .question_content import question_parts
    from .question_identity import assign_scoped
    from .preview import _strong_number
    units = []
    for section in _scope_windows(sections, source_lines, visual_columns):
        if section.get("visual_column_id"):
            # The visual model proves a real task, not its catalog type or kind.
            # Those come from the independent whole-paper structure response.
            continue
        start, end = section["source_line"], section["source_end_line"]
        type_name = _section_type_label(section.get("type"))
        local = source_lines[start:end]
        declared_numbers = _section_numbers(section)
        # 书面表达常无印刷题号：审题思路/注意事项/参考答案里的 1.2.3. 是内容
        # 要点或答案序号，不是试卷题号。没有显式声明题号时不能回退为
        # “本节所有数字”，否则整篇作文会被切成多个伪小题。
        writing_unnumbered = "书面表达" in type_name and not declared_numbers
        # When extraction supplied explicit question/blank locations, do not
        # union every digit found inside article/table material: dates, prices,
        # footnotes and table values are not printed question numbers.  Fall
        # back to source anchors only for legacy sections without locations.
        group_flag = section.get("kind") == "group" if section.get("kind") else is_group_type(type_name)
        # 独立题大题（如熟词生义/翻译）若未声明题号且正文无行首强题号，不把句内引用的
        # 完形编号空位（__<u>1</u>__）当成印刷小题号，使其正常进入无号文字作答题通道。
        if writing_unnumbered:
            numbers = []
        elif declared_numbers:
            numbers = declared_numbers
        elif not group_flag and not any(_strong_number(line) is not None for line in local):
            numbers = []
        else:
            numbers = sorted(_source_members(local))
        windows = _material_windows(source_lines, start + 1, end) if group_flag else []
        if group_flag and windows and numbers:
            windows = _numbered_windows(windows, source_lines, numbers, start, end)
        if group_flag:
            for lo, hi in windows:
                chunk = source_lines[lo - 1:hi]
                members = sorted(_source_members(chunk).intersection(numbers))
                has_body = any(_plain(line).strip() for line in chunk)
                # 整节既无印刷题号也无图片时（如字母歌补全），仍须保留一个
                # 绑定原文的来源单元；否则该大题窗口未产生单元而被误判为漏题。
                # 与前置组装的“合法来源形态”共用同一分类，避免两处口径漂移。
                unnumbered = not members and (not _source_members(chunk))
                if not members and not unnumbered:
                    continue
                if not members and not has_body:
                    # 纯空白/空行窗口没有可绑定原文，跳过以免造出空单元。
                    continue
                image_only_chunk = (any("[IMG " in line for line in chunk)
                                    and all(not _plain(line).strip() or "[IMG " in line or "[TABLE" in line for line in chunk))
                inferred = None
                if unnumbered and image_only_chunk and batch_dir is not None and lo == start + 1 and hi == end:
                    from .upload_content import infer_image_group_numbers
                    inferred = infer_image_group_numbers(batch_dir, start, end)
                if inferred and inferred.get("numbers"):
                    inferred_nums = list(inferred["numbers"])
                    unit = _make_unit("group", type_name, inferred_nums, chunk)
                    unit["stem"] = "\n".join(chunk).strip()
                    unit["source_span"] = {
                        "start_line": lo, "end_line": hi, "basis": "cleaned_source",
                        "number_binding": "answer_inferred_image_group",
                        "printed_numbers": [],
                        "inferred_numbers": list(inferred_nums),
                        "inferred_evidence": list(inferred.get("evidence") or []),
                    }
                    unit["original_number"] = inferred_nums[0] if len(inferred_nums) == 1 else None
                    unit["printed_number"] = False
                else:
                    unit = _make_unit("group", type_name, members or [1], chunk)
                    unit["stem"] = "\n".join(chunk).strip()
                    unit["source_span"] = {"start_line": lo, "end_line": hi, "basis": "cleaned_source"}
                    if unnumbered:
                        unit["source_span"]["number_binding"] = "unnumbered_section"
                        unit["source_span"]["printed_numbers"] = []
                        unit["original_number"] = None
                        unit["printed_number"] = False
                units.append(unit)
        else:
            from .source_anchors import printed_tokens
            from .preview import _strong_number

            def _line_tokens(line):
                # A workbook item like "4.30 <u>…</u>" glues the printed number
                # to answer content, so printed_tokens (which rejects "N." before
                # a digit to avoid decimals) returns empty. The leading-item rule
                # _strong_number owns that case; union it in (strong only, never
                # weak blank-slot numbers) so the item still gets an anchor.
                tokens = printed_tokens(line)
                leading = _strong_number(line)
                if leading is not None and not any(n == leading for _o, n in tokens):
                    tokens = [(0, leading), *tokens]
                return tokens

            anchors = [(i, number, offset, tokens) for i in range(start, end)
                       for tokens in [_line_tokens(source_lines[i])]
                       for offset, number in tokens if number in numbers]
            image_only = (section.get("image_only") and any("[IMG " in line for line in local)
                          and all(not _plain(line).strip() or "[IMG " in line or "[TABLE" in line for line in local))
            unnumbered_text = []
            if not anchors and not numbers and local and not writing_unnumbered:
                from .question_content import legitimate_source_shape
                candidate = _make_unit("standalone", type_name, [1], local)
                candidate["stem"] = "\n".join(local).strip()
                candidate["source_span"] = {"start_line": start + 1, "end_line": end,
                                            "basis": "cleaned_source", "number_binding": "unnumbered_section",
                                            "printed_numbers": []}
                candidate["source_section"] = {"start_line": start, "end_line": end}
                # An absent number alone proves nothing: retain a full real body
                # with an explicit response blank, using the common shape rule.
                readable = re.sub(r"<[^>]*>|\[/?TABLE[^\]]*\]|\s|_", "", candidate["stem"])
                shape = legitimate_source_shape(candidate)
                if shape == "unnumbered_no_image":
                    from .question_content import unnumbered_text_responses
                    for lo, hi in unnumbered_text_responses(local):
                        response = _make_unit("standalone", type_name, [1], local[lo:hi + 1])
                        response["stem"] = "\n".join(local[lo:hi + 1]).strip()
                        response["source_span"] = {"start_line": start + lo + 1, "end_line": start + hi + 1,
                                                   "basis": "cleaned_source", "number_binding": "unnumbered_section",
                                                   "printed_numbers": []}
                        response.update(original_number=None, printed_number=False)
                        unnumbered_text.append(response)
                elif (readable and re.search(r"<u>\s*</u>|_{2,}", candidate["stem"])
                      and shape == "unnumbered_table_response"):
                    candidate.update(original_number=None, printed_number=False)
                    unnumbered_text = [candidate]
            if unnumbered_text:
                units.extend(unnumbered_text)
            elif not anchors and not numbers and image_only and not writing_unnumbered:
                inferred = None
                if batch_dir is not None:
                    from .upload_content import infer_image_group_numbers
                    inferred = infer_image_group_numbers(batch_dir, start, end)
                if inferred and inferred.get("numbers"):
                    inferred_nums = list(inferred["numbers"])
                    inferred_kind = "group" if (len(inferred_nums) > 1 or group_flag) else "standalone"
                    unit = _make_unit(inferred_kind, type_name, inferred_nums, local)
                    unit["stem"] = "\n".join(local).strip()
                    unit["source_span"] = {"start_line": start + 1, "end_line": end,
                                           "basis": "cleaned_source",
                                           "number_binding": "answer_inferred_image_group",
                                           "printed_numbers": [],
                                           "inferred_numbers": list(inferred_nums),
                                           "inferred_evidence": list(inferred.get("evidence") or [])}
                    unit["original_number"] = inferred_nums[0] if len(inferred_nums) == 1 else None
                    unit["printed_number"] = False
                else:
                    # 没有可读印刷题号且无法从同大题答案确定性反推时，只保留整节一个来源对象。
                    # [1] 是兼容字段内部别名，不能展示成原卷第 1 题。
                    unit = _make_unit("standalone", type_name, [1], local)
                    unit["stem"] = "\n".join(local).strip()
                    unit["source_span"] = {"start_line": start + 1, "end_line": end,
                                           "basis": "cleaned_source", "number_binding": "unnumbered_section",
                                           "printed_numbers": []}
                    unit["original_number"] = None
                    unit["printed_number"] = False
                units.append(unit)
            elif not anchors and "书面表达" in type_name and not numbers:
                # Writing sections commonly have one task without a printed
                # question number. Keep the internal alias only for binding;
                # upload numbering must project this as null/null.
                chunk = source_lines[start:end]
                inline_prompt = _inline_unnumbered_writing(section, source_lines)
                span_start = start + 1
                span_end = start + len(chunk)
                if inline_prompt:
                    from .sync_practice import writing_prompt_end
                    tail_len = writing_prompt_end(chunk)
                    if tail_len > 0 and any(line.strip() for line in chunk[:tail_len]):
                        chunk = chunk[:tail_len]
                        span_end = start + len(chunk)
                    elif 1 <= start <= len(source_lines) and source_lines[start - 1].strip():
                        chunk = [source_lines[start - 1]]
                        span_start = start
                        span_end = start
                    else:
                        continue
                unit = _make_unit("standalone", type_name, [1], chunk)
                if inline_prompt:
                    unit["stem"] = "\n".join(chunk).strip()
                unit["source_span"] = {"start_line": span_start, "end_line": span_end,
                                       "basis": "cleaned_source", "number_binding": "single_writing_section",
                                       "printed_numbers": []}
                unit["original_number"] = None
                unit["printed_number"] = False
                units.append(unit)
            elif not anchors and "书面表达" in type_name and len(numbers) == 1:
                chunk = source_lines[start:end]
                unit = _make_unit("standalone", type_name, numbers, chunk)
                unit["source_span"] = {"start_line": start + 1, "end_line": end,
                                       "basis": "cleaned_source", "number_binding": "single_writing_section"}
                units.append(unit)
            for index, (at, number, offset, tokens) in enumerate(anchors):
                if len(tokens) > 1:
                    position = next(j for j, token in enumerate(tokens) if token == (offset, number))
                    left = 0 if position == 0 else offset
                    right = tokens[position + 1][0] if position + 1 < len(tokens) else len(source_lines[at])
                    chunk = [source_lines[at][left:right]]
                    unit = _make_unit("standalone", type_name, [number], chunk)
                    unit["source_span"] = {"start_line": at + 1, "end_line": at + 1, "basis": "cleaned_source",
                                           "start_offset": left, "end_offset": right}
                else:
                    stop = anchors[index + 1][0] if index + 1 < len(anchors) else end
                    chunk = source_lines[at:stop]
                    unit = _make_unit("standalone", type_name, [number], chunk)
                    unit["source_span"] = {"start_line": at + 1, "end_line": stop, "basis": "cleaned_source"}
                units.append(unit)
        section_units = [u for u in units if start < u["source_span"]["start_line"] <= end]
        peer_options = max([len(question_parts(local, n)["options"]) for n in numbers] or [0])
        for unit in section_units:
            unit["source_expected_options"] = (max([len(question_parts(unit["stem"].splitlines(), n)["options"])
                                                    for n in unit["numbers"]] or [0]) if group_flag else peer_options)
    try:
        return assign_scoped(units, source_lines, sections=sections, visual_columns=visual_columns) if units else []
    except ValueError:
        # Preserve invalid candidates for a diagnostic report; never confirm them.
        return units


def _build_units(sections: list[dict], groups: list[dict], source_lines: list[str], visual_columns=None, *, batch_dir=None) -> list[dict]:
    """把 section / group 信息组织成入库单元：独立题逐题，组合题成组。"""
    from . import sync_practice
    if visual_columns or sync_practice.looks_like_sync_practice("\n".join(source_lines)):
        return _build_scoped_units(sections, groups, source_lines, visual_columns, batch_dir=batch_dir)
    group_of: dict[int, list[int]] = {}
    for group in groups:
        nums = _section_numbers({"questions": group.get("questions") or []})
        if nums:
            for n in nums:
                group_of[n] = nums

    units: list[dict] = []
    covered: set[int] = set()

    def make_group(type_name, numbers, source_start=None, source_end=None):
        unit = _make_unit("group", type_name, list(numbers), source_lines)
        if not str(unit.get("stem") or "").strip() and isinstance(source_start, int):
            end = source_end if isinstance(source_end, int) else len(source_lines)
            if 1 <= source_start <= end <= len(source_lines):
                chunk = source_lines[source_start - 1:end]
                unit["stem"] = "\n".join(line for line in chunk if not material_label(line)).strip()
                unit["source_span"] = {"start_line": source_start, "end_line": end,
                                       "basis": "section_source"}
        return unit

    group_spans = {}
    for group in groups:
        nums = tuple(_section_numbers({"questions": group.get("questions") or []}))
        if nums:
            group_spans[nums] = (group.get("source_start_line"), group.get("source_end_line"))
    for section in sections:
        type_name = _section_type_label(section.get("type"))
        nums = _section_numbers(section)
        group_flag = is_group_type(type_name)
        # 该 section 里已被 groups 声明为组内的题号。A group belongs to exactly
        # one section: adopting it on a single shared number would attach a stale
        # group (e.g. a mis-counted reading group carrying 2/5/6) to an unrelated
        # listening section. Require the group to be fully contained in this
        # section; a partial match is a candidate-level defect, not evidence.
        declared_groups = []
        for n in nums:
            members = group_of.get(n)
            if members and set(members) <= set(nums):
                declared_groups.append(sorted(set(members)))
        declared_groups = sorted({tuple(g) for g in declared_groups})
        # 原卷的分段导语/材料标题比“整节一组”的旧候选更精确。
        partitions = source_group_partitions(source_lines, nums) if group_flag else []
        if partitions:
            for part in partitions:
                start_line = part.get("source_start_line") or section.get("source_line")
                end_line = part.get("source_end_line") or section.get("source_end_line")
                unit = make_group(type_name, part["questions"], start_line, end_line)
                if isinstance(part.get("source_start_line"), int) and isinstance(part.get("source_end_line"), int):
                    unit["source_span"] = {"start_line": part["source_start_line"],
                                           "end_line": part["source_end_line"],
                                           "basis": "cleaned_source"}
                units.append(unit)
            continue
        grouped = set().union(*[set(g) for g in declared_groups]) if declared_groups else set()
        loose = [n for n in nums if n not in grouped]

        if declared_groups:
            want_group = True
        elif group_flag is not None:
            want_group = group_flag
        else:
            # 题型未知：题号连续且数量 >= 2 才倾向于成组，否则独立
            want_group = len(loose) >= 2

        if want_group and len(loose) >= 1:
            # 未声明成组但按题型应成组的整段题号，合并成一个组
            if declared_groups:
                for g in declared_groups:
                    span = group_spans.get(tuple(g))
                    if not span or not all(isinstance(value, int) for value in span):
                        span = (section.get("source_line"), section.get("source_end_line"))
                    units.append(make_group(type_name, list(g), *span))
                    covered.update(g)
                if loose:
                    units.append(make_group(type_name, loose,
                                            section.get("source_line"), section.get("source_end_line")))
                    covered.update(loose)
            else:
                units.append(make_group(type_name, loose,
                                        section.get("source_line"), section.get("source_end_line")))
                covered.update(loose)
        else:
            for g in declared_groups:
                span = group_spans.get(tuple(g))
                if not span or not all(isinstance(value, int) for value in span):
                    span = (section.get("source_line"), section.get("source_end_line"))
                units.append(make_group(type_name, list(g), *span))
                covered.update(g)
            for n in loose:
                units.append(_make_unit("standalone", type_name, [n], source_lines))
                covered.update({n})
    # 无题号作文必须绑定唯一的书面表达节及唯一相邻题号，不能靠“比最大题号大”。
    from .preview import SECTION, _strong_number
    for section in sections:
        nums = _section_numbers(section)
        if "书面表达" not in str(section.get("type")) or len(nums) != 1:
            continue
        title = _plain(str(section.get("title") or "")).strip()
        starts = [i for i, line in enumerate(source_lines)
                  if (title and _plain(line).strip() == title)
                  or (not title and SECTION.match(_plain(line)) and "书面表达" in _plain(line))]
        if len(starts) != 1:
            continue
        start = starts[0]
        prior = [n for line in source_lines[:start] if (n := _strong_number(line)) is not None]
        if not prior or nums[0] != max(prior) + 1:
            continue
        end = next((i for i in range(start + 1, len(source_lines)) if SECTION.match(_plain(source_lines[i]))), len(source_lines))
        first = next((_plain(line).strip() for line in source_lines[start + 1:end] if _plain(line).strip()), "")
        explicit = (_strong_number(first) == nums[0]
                    and bool(re.search(r"(?:\bwrite\s+\S.+|请[^\n]*写[^\n]*\S|写一[篇封].+)", first, re.I)))
        for unit in units:
            if unit["numbers"] == nums and not explicit:
                unit["stem"] = "\n".join(source_lines[start + 1:end]).strip()
                unit["source_span"] = {"start_line": start + 2, "end_line": end,
                                       "basis": "cleaned_source", "number_binding": "single_writing_section"}
    from .question_content import question_parts
    for section in sections:
        numbers = _section_numbers(section)
        peer_count = max([len(question_parts(source_lines, n)["options"]) for n in numbers] or [0])
        for unit in units:
            if set(unit["numbers"]).issubset(numbers):
                if unit["unit_kind"] == "group":
                    # 同一阅读大题可同时含选择题与限词数问答，不能跨材料套用选项数。
                    unit["source_expected_options"] = max([len(question_parts(unit["stem"].splitlines(), n)["options"]) for n in unit["numbers"]] or [0])
                else:
                    unit["source_expected_options"] = peer_count
    return units


def _make_unit(unit_kind: str, type_name: str, numbers: list[int], source_lines: list[str]) -> dict:
    numbers = sorted(set(int(n) for n in numbers))
    label = f"{numbers[0]}–{numbers[-1]} {len(numbers)}小题" if len(numbers) > 1 else str(numbers[0])
    return {
        "unit_kind": unit_kind,
        "type": type_name,
        "numbers": numbers,
        "label": label,
        "stem": _slice_stem(source_lines, numbers, unit_kind),
    }


def _slice_stem(source_lines: list[str], numbers: list[int], unit_kind: str = "standalone") -> str:
    """用现有切片逻辑取该单元的原文片段，只作为结构化呈现的文本。"""
    try:
        from .preview import slice_text, slice_range
        if len(numbers) == 1 and unit_kind != "group":
            chunk = slice_text(source_lines, numbers[0])
        else:
            chunk = slice_range(source_lines, numbers)
        return "\n".join(chunk)
    except Exception:
        return ""


def _load_structure(structure_path: Path) -> dict:
    if not structure_path.is_file():
        return {}
    try:
        return json.loads(structure_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}


def _prove_single_writing_number(original_path: Path | None, source_text: str,
                                 sections: list[dict], issue: dict, hashes: dict,
                                 *, allow_warnings: bool = False) -> dict | None:
    """Prove a narrow text-only inference from the original, never candidate counts alone."""
    from .extract import infer_structure
    from .preview import SECTION, _strong_number, _weak_numbers

    if not original_path or not hashes["source_sha256"] or not hashes["original_sha256"]:
        return None
    try:
        if original_path.suffix.lower() == ".docx":
            from .docx_extract import extract_docx
            with tempfile.TemporaryDirectory(prefix="structure-number-proof-") as temp:
                native = extract_docx(original_path, Path(temp))
            if native["warnings"] and not allow_warnings:
                return None
            original_text, method = native["text"], "docx-native"
        elif original_path.suffix.lower() == ".txt":
            original_text, method = _read_text(original_path), "original-text"
        else:
            return None
    except (OSError, ValueError, UnicodeError):
        return None
    # Only the extractor's explicit answer-key classification may exclude an appendix.
    original_candidate, original_body, appendix = infer_structure(original_text)
    if appendix and not any(x.get("code") in ("ANSWER_APPENDIX_DETECTED", "INLINE_ANSWER_BLOCK")
                            for x in original_candidate.get("unresolved", [])):
        return None
    original_cleaned = denoise.clean_body(original_body)[0]
    normalize = lambda text: [_plain(line).strip() for line in text.splitlines() if _plain(line).strip()]
    # 两侧都按同一去噪规则比较；正文里的卷名/注意事项属于页眉噪声，不能只清一侧。
    current_cleaned = denoise.clean_body(source_text)[0]
    if normalize(original_cleaned) != normalize(current_cleaned):
        return None
    lines = original_cleaned.splitlines()
    headings = [i for i, line in enumerate(lines) if SECTION.match(_plain(line))]
    writing = [i for i in headings if "书面表达" in _plain(lines[i])]
    candidates = [s for s in sections if "书面表达" in str(s.get("type"))]
    if len(writing) != 1 or len(candidates) != 1 or candidates[0] is not sections[-1]:
        return None
    start, section = writing[0], candidates[0]
    title = _plain(lines[start]).strip()
    counts = re.findall(r"共\s*(\d+)\s*小题", title)
    if headings[-1] != start or counts != ["1"] or title != _plain(str(section.get("title") or "")).strip():
        return None
    if issue.get("line") != section.get("source_line") or not isinstance(issue.get("line"), int):
        return None
    prior, strong, order = set(), [], []
    for line in lines[:start]:
        if SECTION.match(_plain(line)):
            continue
        n = _strong_number(line)
        if n is not None:
            strong.append(n)
        for found in ([n] if n is not None else []) + _weak_numbers(line):
            if found not in prior:
                order.append(found)
                prior.add(found)
    if not prior or order != list(range(1, max(prior) + 1)) or len(strong) != len(set(strong)):
        return None
    number = max(prior) + 1
    if _section_numbers(section) != [number]:
        return None
    body = "\n".join(lines[start + 1:]).strip()
    # Multiple lists may restart only after an explicit content/requirements label.
    # Other numbered lines remain ambiguous and must not be hidden by this proof.
    previous, last_nonempty, points_valid = 0, "", True
    label_only = r"(?:写作)?(?:要点|注意|注意事项|要求|提示)\s*[:：]"
    for line in lines[start + 1:]:
        plain = _plain(line).strip()
        point = _strong_number(line)
        labeled = False
        # 要点列表可能和标签同一行：注意:1.词数80~100; 仍按新列表的起点处理。
        if point is None and re.match(label_only + r"\s*1\s*[.．、]", plain):
            point, labeled = 1, True
        if point is not None:
            restart = (point == 1 and previous > 0 and (labeled or re.fullmatch(label_only, last_nonempty)))
            if point != previous + 1 and not restart:
                points_valid = False
            previous = point
        if plain:
            last_nonempty = plain
    if (not body or not re.search(r"(?:\bwrite\s+\S.+|请[^\n]*写[^\n]*\S|写一[篇封].+|(?:写|回)一[篇封][^\n]*(?:电子邮件|邮件)|[^\n]*电子邮件[^\n]*(?:要点|词数))", _plain(body), re.I)
            or not points_valid
            or any(_weak_numbers(line) for line in lines[start + 1:])):
        return None
    from . import manifest as mf
    if mf.sha256_file(original_path) != hashes["original_sha256"]:
        return None
    return {"code": "QUESTION_NUMBER_INFERRED", "message": issue.get("message", "作文无印刷题号"),
            "resolved": True, "resolution": f"确定性文字结构推定为第{number}题；未进行视觉核验",
            "resolution_evidence": {
                "method": "deterministic_single_writing_number", "original_text_method": method,
                "source_sha256": hashes["source_sha256"], "original_sha256": hashes["original_sha256"],
                "evidence": [f"原件正文（{method}，去噪后）第{start + 1}行：{title}",
                             f"原件正文前序实际题号/空位完整覆盖1–{number - 1}，无重复显式题号",
                             f"唯一书面表达为末节，标题原文声明共1小题，正文有写作任务；候选编号{number}连续",
                             "原件正文文字与当前正文去噪后逐行一致；仅核对文字序列，未核验图像、版面或听力"]}}


def _original_matches_cleaned(original_path, source_text, original_hash):
    """Require independently readable original text before localizing uncertainty."""
    from . import manifest as mf
    from .extract import infer_structure
    if not original_path or not original_hash:
        return False
    try:
        if original_path.suffix.lower() == ".txt":
            text = _read_text(original_path)
        elif original_path.suffix.lower() == ".docx":
            from .docx_extract import extract_docx
            with tempfile.TemporaryDirectory(prefix="structure-scope-") as temp:
                native = extract_docx(original_path, Path(temp))
            if native["warnings"]:
                return False
            text = native["text"]
        else:
            return False
        _, body, _ = infer_structure(text)
        cleaned = denoise.clean_body(body)[0]
        normalize = lambda value: [_plain(line).strip() for line in value.splitlines() if _plain(line).strip()]
        return normalize(cleaned) == normalize(source_text) and mf.sha256_file(original_path) == original_hash
    except (OSError, ValueError, UnicodeError, KeyError, TypeError):
        return False


def run_gate(batch_dir: Path, *, output_dir: Path | None = None) -> dict:
    """执行结构自动闸门，写产物并返回报告。

    报告 `passed` 为真才放行；不通过时列出阻断项供人工或重跑处理。
    """
    batch_dir = Path(batch_dir)
    source_path = batch_dir / "01_source" / "正文.txt"
    structure_path = batch_dir / "01_source" / "structure.json"

    raw_text = _read_text(source_path)
    # Some extracted bodies record an inclusive terminal boundary one line past
    # splitlines(); normalize only that boundary, never question/source anchors.
    if raw_text and not raw_text.endswith(("\n", "\r")):
        raw_text += "\n"
    cleaned_text, meta, removed = denoise.clean_body(raw_text) if raw_text else ("", {}, [])
    source_lines = cleaned_text.splitlines()

    structure = denoise.project_structure(_load_structure(structure_path), raw_text, cleaned_text)
    sections = structure.get("sections") if isinstance(structure.get("sections"), list) else []
    groups = structure.get("groups") if isinstance(structure.get("groups"), list) else []
    image_questions = structure.get("image_questions") if isinstance(structure.get("image_questions"), list) else []
    # 听力稿可能只含材料/导语，题干与选项由后续模型从其他材料合并；
    # 普通整卷仍使用原有确定性完整性闸门。
    # This flag is enabled only after a complete, evidence-bound AI structure
    # candidate is accepted; the mere presence of a listening section must not
    # weaken checks for unrelated written sections.
    model_structure_only = False
    visual_notes: list[dict] = []
    from . import sync_practice
    # 视觉理解已停用：不再加载整页栏目归属；columns 恒为空即“无视觉证据”。
    columns = []
    sections = structure.get("sections") or []
    scoped = bool(columns) or sync_practice.looks_like_sync_practice(cleaned_text)
    from . import lesson_bands
    band_evidence = lesson_bands.for_text(batch_dir, cleaned_text) if scoped else {}
    if band_evidence.get("bands") or band_evidence.get("features"):
        sections = lesson_bands.annotate_sections(sections, band_evidence)

    from . import manifest as mf
    original_path = None
    try:
        state = mf.load(batch_dir)
        original_path = Path(state.get("inputs", {}).get("source", {}).get("path", ""))
    except (OSError, ValueError, KeyError):
        pass
    hashes = {"source_sha256": mf.sha256_file(source_path) if source_path.is_file() else None,
              "structure_sha256": mf.sha256_file(structure_path) if structure_path.is_file() else None,
              "original_sha256": mf.sha256_file(original_path) if original_path and original_path.is_file() else None,
              "visual_columns_sha256": None}
    base_sections = list(sections)
    base_meta = dict(meta) if isinstance(meta, dict) else meta

    def _run_structural_pass(ai_classified=None, initial_warnings=None):
        sections = list(base_sections)
        meta = dict(base_meta) if isinstance(base_meta, dict) else base_meta
        blockers: list[dict] = []
        checks: dict[str, str] = {"column_ownership": "not_required"}
        resolved_notes: list[dict] = []
        warnings: list[dict] = list(initial_warnings or [])
        deferred: list[dict] = []
        source_diagnostic = None
        # 复合提取器显式提出的 OCR/题号/分组歧义不能被数量自检抹掉。
        # 已经人工/原卷核对、并附带 resolution 依据的条目可放行，但仍记入报告留痕。
        for issue in structure.get("unresolved") or []:
            if isinstance(issue, dict):
                if issue.get("code") == "SOURCE_METADATA_MISSING":
                    # 兼容旧提取报告：来源缺失采用占位，不作为结构错误或人工必审项。
                    warnings.append({**issue, "blocking": False, "status": "placeholder"})
                    continue
                evidence = issue.get("resolution_evidence") or {}
                bound = (isinstance(evidence, dict) and hashes["source_sha256"] and hashes["original_sha256"]
                         and evidence.get("source_sha256") == hashes["source_sha256"]
                         and evidence.get("original_sha256") == hashes["original_sha256"]
                         and isinstance(evidence.get("evidence"), list)
                         and any(isinstance(ref, str) and ref.strip() for ref in evidence["evidence"]))
                if issue.get("resolved") is True and str(issue.get("resolution") or "").strip() and bound:
                    resolved_notes.append({"code": issue.get("code", "EXTRACTION_UNRESOLVED"),
                                           "message": issue.get("message", "提取结果待核验"),
                                           "resolution": issue.get("resolution")})
                    continue
                if issue.get("code") == "ANSWER_APPENDIX_DETECTED":
                    # 答案册特征（题号+答案字母/解析）是确定性事实：分离为附录候选，不阻断正文切题。
                    resolved_notes.append({"code": issue.get("code"),
                                           "message": issue.get("message", "文末答案附录"),
                                           "resolution": "文末重复标题之后按题号+答案字母/解析判定为答案册，保留为答案附录候选"})
                    continue
                if issue.get("code") in ("EXTRACTION_WARNING", "OCR_VISUAL_REVIEW", "IMAGE_SECTION_UNNUMBERED"):
                    # 提取告警/OCR复核结果/图片题都是“版面与能力”层面，不是结构（题号/选项/材料）错误：
                    # 保留为非阻断告警，并强制 layout_required 走整页渲染/版面复核（上传前必过），
                    # 不让 OCR 补充证据反过来阻断结构切题。
                    warnings.append({**issue, "blocking": False, "status": "layout_review_required"})
                    continue
                if issue.get("code") == "INLINE_ANSWER_BLOCK":
                    # 解析版把答案插在大题之间（听力答案后接笔试）：答案移入附录、
                    # 正文继续解析是确定性切分，不构成结构歧义。
                    resolved_notes.append({"code": issue.get("code"),
                                           "message": issue.get("message", "分段答案"),
                                           "resolution": "重复大题后是分段答案、随后仍有新大题；答案移入附录，正文继续解析"})
                    continue
                # 听力稿只负责保留材料和诊断；边界由后续模型结构化阶段判断。
                if model_structure_only:
                    warnings.append({**issue, "blocking": False, "status": "model_structure_pending"})
                else:
                    blockers.append({"code": issue.get("code", "EXTRACTION_UNRESOLVED"),
                                     "message": issue.get("message", "提取结果待核验"),
                                     "source_line": issue.get("line")})
        checks["extraction_evidence"] = "passed" if model_structure_only or not blockers else "failed"

        if not raw_text.strip():
            blockers.append({"code": "SOURCE_EMPTY", "message": "正文为空，无法结构化"})
            checks["source_present"] = "failed"
            sections = []
        else:
            checks["source_present"] = "passed"

        if not sections and not any(c.get("role") == "question" for c in columns):
            blockers.append({"code": "NO_SECTIONS", "message": "structure.json 缺少 sections，无法拆分题组"})
            checks["sections_present"] = "failed"
            sections = []
        else:
            checks["sections_present"] = "passed"

        for section in sections:
            nums = _section_numbers(section)
            count = section.get("declared_count")
            unnumbered_writing = "书面表达" in str(section.get("type") or "") and not nums
            if count is not None and count != len(nums) and not unnumbered_writing:
                issue = {"code": "QUESTION_COUNT_MISMATCH", "message": f"大题 {section.get('title') or section.get('type')} 声明 {count} 小题，当前提取定位 {len(nums)} 题；交模型结构化"}
                if model_structure_only:
                    warnings.append({**issue, "blocking": False, "status": "model_structure_pending"})
                else:
                    blockers.append(issue)
        units = _build_units(sections, groups, source_lines, columns, batch_dir=batch_dir) if sections else []
        if isinstance(ai_classified, dict) and ai_classified.get("units"):
            units = _merge_visual_units(units, ai_classified["units"], columns, source_lines, sections)
        # A stale candidate group may differ from the final AI grouping. Only the
        # same complete Word-bound proof used by the downstream source audit can
        # make that candidate boundary comparison obsolete.
        ai_bound = False
        if isinstance(ai_classified, dict) and ai_classified.get("units"):
            from .question_content import _validated_ai_units
            ai_units = ai_classified["units"]
            if _validated_ai_units(ai_units, units, source_lines) is not None:
                ai_bound = True
                units = ai_units
            else:
                # An AI grouping that cannot be bound to the source per question must
                # never become the confirmed structure: keep the deterministic units
                # and let the per-question source audit judge them. Downstream still
                # re-checks every unit, so this is a fallback, not a silent approval.
                warnings.append({"code": "AI_STRUCTURE_REJECTED", "blocking": False,
                                 "status": "fallback",
                                 "message": "AI 结构单元无法逐题绑定原文；已改用确定性结构，后续仍逐题核对原卷"})

        declared = sorted({n for section in sections for n in _section_numbers(section)})
        from .preview import _strong_number
        if scoped:
            from . import question_content
            from .question_content import audit_source_unit
            scoped_problems = _scoped_source_errors(units, sections, source_lines, columns)
            blockers.extend(scoped_problems)
            checks["coverage"] = checks["source_number_coverage"] = checks["grouping"] = "failed" if scoped_problems else "passed"
            content_problems = []
            _visual_sem = None
            for unit in units:
                span = unit.get("source_span") or {}
                lo, hi = span.get("start_line", 0), span.get("end_line", 0)
                if not 1 <= lo <= hi <= len(source_lines):
                    content_problems.append({"code": "SOURCE_SPAN_INVALID", "message": "单元缺少有效局部来源窗口"})
                    continue
                local = source_lines[lo - 1:hi]
                local_unit = {**unit, "source_span": {**span, "start_line": 1, "end_line": len(local)}}
                unit_issues = audit_source_unit(local_unit, local)["issues"]
                # 图片选项题：正则对 `[IMG x]A.` 前置排版切割错位会误报缺选项；用原文
                # `[IMG]` 计数 + 视觉语义佐证证明“选项是图、图都在”时降级为留痕，不阻断。
                if _visual_sem is not None:
                    kept = []
                    for issue in unit_issues:
                        if issue.get("code") == "SOURCE_OPTIONS_INCOMPLETE":
                            ev = question_content.image_option_evidence(local_unit, local, _visual_sem)
                            if ev and ev.get("is_image_option"):
                                visual_notes.append({
                                    "unit_id": unit.get("unit_id"), "number": issue.get("number"),
                                    "resolution": "图片选项题：窗口内图片数与选项匹配（视觉佐证），非缺项",
                                    "image_count": ev["image_count"], "expected": ev["expected"]})
                                continue
                        kept.append(issue)
                    unit_issues = kept
                content_problems.extend(unit_issues)
            blockers.extend(content_problems)
            checks["source_content"] = "failed" if content_problems else "passed"
            checks["group_guides"] = "failed" if any(p["code"] == "GUIDE_GROUP_MISMATCH" for p in scoped_problems) else "passed"
            checks["denoise"] = "passed"
            # A valid section restart is independent of an unrelated section's
            # failure. Each participating occurrence must still prove its own scope.
            from .question_content import legitimate_source_shape
            unnumbered_boundaries = []
            for issue in blockers:
                if issue.get("code") != "QUESTION_BOUNDARY_MISSING":
                    continue
                at = issue.get("source_line")
                owned = [u for u in units if (u.get("source_section") or {}).get("start_line") == at]
                if (owned and all(legitimate_source_shape(u)
                                  and u.get("source_span", {}).get("number_binding") == "unnumbered_section"
                                  and _proven_unnumbered_scope(u, sections, source_lines) for u in owned)
                        and not any(type(p.get("source_line")) is int
                                    and at <= p["source_line"] <= max(u["source_span"]["end_line"] for u in owned)
                                    for p in scoped_problems)
                        and all(_proven_unnumbered_scope(u, sections, source_lines, columns) for u in owned)):
                    unnumbered_boundaries.append(issue)
            # Full reconciliation above still retains every other section's errors.
            blockers[:] = [b for b in blockers if b not in unnumbered_boundaries]
            resolved_notes.extend({**b, "resolution": "完整无编号来源窗口及合法原卷作答形态已验证，内部别名不作为印刷题号"} for b in unnumbered_boundaries)
            restarts = [b for b in blockers if b.get("code") == "NUMBERING_RESTART"
                        and _proven_scoped_restart(b.get("source_line"), units, sections, source_lines, scoped_problems, columns)]
            blockers[:] = [b for b in blockers if b not in restarts]
            resolved_notes.extend({**b, "resolution": "当前大题及前序同号大题的来源位置、出现序次和身份均已独立重建验证"} for b in restarts)
            # Only positional source proof can resolve extraction's global-number assumptions.
            if not scoped_problems and not content_problems:
                positional_codes = {"DUPLICATE_QUESTION_NUMBER", "GROUP_BOUNDARY_REVIEW", "QUESTION_BOUNDARY_MISSING"}
                resolved = [b for b in blockers if b.get("code") in positional_codes]
                blockers[:] = [b for b in blockers if b.get("code") not in positional_codes]
                resolved_notes.extend({**b, "resolution": "每个原题位置已唯一绑定到本大题/材料局部窗口"} for b in resolved)
                checks["extraction_evidence"] = "failed" if any(b.get("source_line") for b in blockers) else "passed"
        else:
            # 题号覆盖：section 声明的题号应被 units 完整覆盖，无重复
            declared = sorted({n for s in sections for n in _section_numbers(s)})
            unit_numbers = [n for u in units for n in u["numbers"]]
            dup = {n for n in unit_numbers if unit_numbers.count(n) > 1}
            missing = sorted(set(declared) - set(unit_numbers))
            if dup:
                blockers.append({"code": "DUPLICATE_NUMBERS", "message": "题号重复归属：" + ", ".join(map(str, sorted(dup)))})
                checks["coverage"] = "failed"
            elif missing:
                blockers.append({"code": "MISSING_NUMBERS", "message": "题号未覆盖：" + ", ".join(map(str, missing))})
                checks["coverage"] = "failed"
            elif declared:
                checks["coverage"] = "passed"

            # 对照实际原文题号/空位，而不是仅比较结构声明与由声明生成的单元。
            from .preview import _strong_number, _weak_numbers, SECTION
            from .question_content import audit_source_unit
            writing_lines = set()
            for unit in units:
                span = unit.get("source_span") or {}
                if span.get("number_binding") == "single_writing_section":
                    writing_lines.update(range(span["start_line"] - 1, span["end_line"]))
            # 显式编号作文的连续内容要点也不是试卷题号；异常序列仍参与对账。
            writing_points = set()
            for section in sections:
                if "书面表达" not in str(section.get("type")):
                    continue
                title = _plain(str(section.get("title") or "")).strip()
                starts = [i for i, line in enumerate(source_lines) if title and _plain(line).strip() == title]
                if len(starts) != 1:
                    continue
                start = starts[0]
                end = next((i for i in range(start + 1, len(source_lines)) if SECTION.match(_plain(source_lines[i]))), len(source_lines))
                first = next((i for i in range(start + 1, end) if _plain(source_lines[i]).strip()), None)
                if first is None or _strong_number(source_lines[first]) not in _section_numbers(section):
                    continue
                if not re.search(r"(?:\bwrite\s+\S.+|请[^\n]*写[^\n]*\S|写一[篇封].+)", _plain(source_lines[first]), re.I):
                    continue
                points = [(i, _strong_number(source_lines[i])) for i in range(first + 1, end)
                          if _strong_number(source_lines[i]) is not None]
                if [n for _, n in points] == list(range(1, len(points) + 1)):
                    writing_points.update(i for i, _ in points)
            observed = set()
            strong_occurrences = {}
            for index, line in enumerate(source_lines):
                if index in writing_lines or SECTION.match(_plain(line)):
                    continue
                n = _strong_number(line)
                if n is not None and index not in writing_points:
                    observed.add(n)
                    strong_occurrences[n] = strong_occurrences.get(n, 0) + 1
                observed.update(_weak_numbers(line))
            writing_numbers = {n for u in units if u.get("source_span", {}).get("number_binding") == "single_writing_section" for n in u["numbers"]}
            duplicate_source = sorted(n for n, count in strong_occurrences.items() if count > 1)
            if duplicate_source:
                blockers.append({"code": "SOURCE_DUPLICATE_NUMBERS", "message": f"原文实际题号重复：{duplicate_source}"})
            unseen = sorted(set(declared) - observed - writing_numbers)
            extra = sorted(observed - set(declared))
            if unseen:
                issue = {"code": "SOURCE_NUMBERS_MISSING", "message": f"结构声明但原文未定位到实际题号/空位：{unseen}"}
                (warnings if model_structure_only else blockers).append({**issue, "blocking": False, "status": "model_structure_pending"} if model_structure_only else issue)
            if extra:
                issue = {"code": "SOURCE_NUMBERS_UNDECLARED", "message": f"原文实际题号未纳入结构：{extra}"}
                (warnings if model_structure_only else blockers).append({**issue, "blocking": False, "status": "model_structure_pending"} if model_structure_only else issue)
            checks["source_number_coverage"] = "passed" if model_structure_only and not duplicate_source else ("warning" if duplicate_source else "failed" if unseen or extra else "passed")
            content_problems = []
            for unit in units:
                if not unit["stem"].strip():
                    content_problems.append({"code": "SOURCE_UNIT_EMPTY", "message": f"源头单元 {unit['numbers']} 内容为空"})
                content_problems.extend(audit_source_unit(unit, source_lines)["issues"])
            # 听力稿可能只有材料；题干/选项由模型从其他材料合并。
            if model_structure_only:
                for problem in content_problems:
                    warnings.append({**problem, "blocking": False, "status": "model_structure_pending"})
                checks["source_content"] = "passed"
            else:
                blockers.extend(content_problems)
                checks["source_content"] = "failed" if content_problems else "passed"

            # 导语里的范围也必须被完整定位，不能把缺少第 10 题的 8–10 导语当成 8–9 一组。
            guide_problems = []
            for line in source_lines:
                refs = guide_numbers(line)
                if refs and (not set(refs).issubset(observed) or not any(set(u["numbers"]) == set(refs) for u in units)):
                    guide_problems.append({"code": "GUIDE_GROUP_MISMATCH", "message": f"分段导语与实际独立题组不一致：{line.strip()}"})
            if model_structure_only:
                for problem in guide_problems:
                    warnings.append({**problem, "blocking": False, "status": "model_structure_pending"})
                checks["group_guides"] = "passed"
            else:
                blockers.extend(guide_problems)
                checks["group_guides"] = "failed" if guide_problems else "passed"

            # 去噪自检：切片里不应再残留元信息/噪声 行
            residual = []
            if source_lines:
                from .question_content import audit_question
                for number in declared:
                    audit = audit_question(source_lines, number)
                    for issue in audit.get("issues") or []:
                        if issue.get("code") == "IRRELEVANT_HEADER":
                            residual.append(issue)
            if residual:
                for item in residual:
                    blockers.append({"code": "RESIDUAL_HEADER", "message": item["message"]})
                checks["denoise"] = "failed"
            elif declared:
                checks["denoise"] = "passed"

            # 组合题自检：声明的 group 必须由合法 section 题号组成
            bad_group = []
            seen_group_numbers = set()
            for group in groups:
                nums = _section_numbers(group)
                overlap = seen_group_numbers.intersection(nums)
                if overlap:
                    blockers.append({"code": "DUPLICATE_GROUP_NUMBERS", "message": f"题号重复归属题组：{sorted(overlap)}"})
                seen_group_numbers.update(nums)
                outside = [n for n in nums if n not in declared]
                if outside:
                    bad_group.append(outside)
                elif nums and not any(set(nums).issubset(_section_numbers(section)) for section in sections):
                    blockers.append({"code": "GROUP_CROSSES_SECTION", "message": f"题组跨越不同大题：{nums}"})
                for section in sections:
                    section_nums = _section_numbers(section)
                    partitions = source_group_partitions(source_lines, section_nums) if is_group_type(section.get("type")) else []
                    if (not ai_bound and partitions and set(nums).issubset(section_nums)
                            and not any(set(nums) == set(p["questions"]) for p in partitions)):
                        issue = {"code": "GROUP_BOUNDARY_MISMATCH", "message": f"题组 {nums} 与原文材料/分段导语的独立范围不一致"}
                        if model_structure_only:
                            warnings.append({**issue, "blocking": False, "status": "model_structure_pending"})
                        else:
                            blockers.append(issue)
            if bad_group:
                blockers.append({"code": "GROUP_OUT_OF_SECTION",
                                 "message": "题组题号不属于任何大题：" + str(bad_group)})
                checks["grouping"] = "failed"
            elif groups:
                checks["grouping"] = "passed"

        for conflict in meta.get("conflicts") or []:
            blockers.append({"code": "SOURCE_METADATA_CONFLICT", "message": str(conflict)})
        from .metadata_policy import missing_source_fields, paper_meta
        # 正文卷头只是证据之一：课时拆分件没有卷头，已识别来源和原卷文件名必须补回，
        # 不能因为正文没写就把试卷名/来源整体变成占位。
        meta = paper_meta(meta, batch_dir)
        metadata_missing = missing_source_fields(meta)
        checks["source_metadata"] = "placeholder" if metadata_missing else "passed"
        if metadata_missing:
            warnings.append({"code": "SOURCE_METADATA_MISSING", "blocking": False,
                             "status": "placeholder", "fields": metadata_missing,
                             "message": "试卷来源信息未可靠识别，缺失文本字段使用「未提供」占位；继续后续打标签和审核。"})
        from .source_answer_only import detect_source_answer_only
        raw_path = batch_dir / "01_source" / "原始正文.txt"
        source_diagnostic = detect_source_answer_only(_read_text(raw_path)) if raw_path.is_file() else None
        if source_diagnostic is not None:
            source_diagnostic["source_sha256"] = mf.sha256_file(raw_path)
            if source_diagnostic["classification"] == "source_answer_only":
                blockers.append({"code": "SOURCE_ANSWER_ONLY", "message": source_diagnostic["message"]})
                checks["source_paper_presence"] = "failed"
        # Legacy candidates can omit the extractor's inference issue entirely.
        # A section-bound unprinted number still requires independent proof.
        bound_writing = [u for u in units if u.get("source_span", {}).get("number_binding") == "single_writing_section"]
        for section in sections:
            if "书面表达" not in str(section.get("type")):
                continue
            nums = _section_numbers(section)
            if (type(section.get("source_line")) is int and 1 <= section["source_line"] <= len(source_lines)
                    and _inline_unnumbered_writing(section, source_lines)
                    and any(u["source_span"]["start_line"] in (section["source_line"], section["source_line"] + 1)
                            and u["source_span"].get("printed_numbers") == [] for u in bound_writing)):
                continue
            title = _plain(str(section.get("title") or "")).strip()
            starts = [i for i, line in enumerate(source_lines) if title and _plain(line).strip() == title]
            first = next((_plain(line).strip() for line in source_lines[starts[0] + 1:] if _plain(line).strip()), "") if len(starts) == 1 else ""
            explicit = (len(nums) == 1 and _strong_number(first) == nums[0]
                        and bool(re.search(r"(?:\bwrite\s+\S.+|请[^\n]*写[^\n]*\S|写一[篇封].+)", first, re.I)))
            if not explicit and not any(u["numbers"] == nums for u in bound_writing):
                blockers.append({"code": "WRITING_NUMBER_UNBOUND", "message": "作文题号未可靠定位，须核对原卷及节边界"})
        inferred = [x for x in structure.get("unresolved") or []
                    if isinstance(x, dict) and x.get("code") == "QUESTION_NUMBER_INFERRED"]
        if any(u["source_span"].get("printed_numbers") != [] for u in bound_writing) and not inferred:
            writing_sections = [s for s in sections if "书面表达" in str(s.get("type"))]
            inferred = [{"code": "QUESTION_NUMBER_INFERRED",
                         "line": writing_sections[0].get("source_line") if len(writing_sections) == 1 else None,
                         "message": "作文没有可核实的印刷题号，顺延编号须核对原卷"}]
            blockers.append({"code": "QUESTION_NUMBER_INFERRED", "message": inferred[0]["message"],
                             "source_line": inferred[0]["line"]})
            checks["extraction_evidence"] = "failed"
        # Resolve only this one deterministic ambiguity after all independent gate checks pass.
        if len(blockers) == 1 and blockers[0]["code"] == "QUESTION_NUMBER_INFERRED" and len(inferred) == 1:
            note = _prove_single_writing_number(original_path, raw_text, sections, inferred[0], hashes,
                                               allow_warnings=True)
            if note and mf.sha256_file(source_path) == hashes["source_sha256"]:
                resolved_notes.append(note)
                blockers.clear()
                checks["extraction_evidence"] = "passed"
        # A locally scoped candidate is not a resolution or gate approval.
        # Do not localize an inference while any other structural issue remains.
        if len(blockers) == 1 and blockers[0].get("code") == "QUESTION_NUMBER_INFERRED" and len(inferred) == 1:
            from .preview import SECTION
            writing_sections = [s for s in sections if "书面表达" in str(s.get("type"))]
            section = writing_sections[0] if len(writing_sections) == 1 else None
            line = blockers[0].get("source_line")
            raw_lines = raw_text.splitlines()
            headings = [i for i, text in enumerate(raw_lines) if SECTION.match(_plain(text))]
            nums = _section_numbers(section) if section else []
            matched = [u for u in units if u.get("numbers") == nums]
            if (section is not None and sections[-1] is section and type(line) is int
                    and 1 <= line <= len(raw_lines) and headings and headings[-1] == line - 1
                    and _plain(raw_lines[line - 1]).strip() == _plain(str(section.get("title") or "")).strip()
                    and re.findall(r"共\s*(\d+)\s*小题", _plain(raw_lines[line - 1])) == ["1"]
                    and line == section.get("source_line") and len(nums) == 1 and nums[0] > 0
                    and len(matched) == 1 and matched[0].get("unit_kind") == "standalone"
                    and matched[0].get("source_span", {}).get("number_binding") == "single_writing_section"
                    and all(value != "failed" for key, value in checks.items() if key != "extraction_evidence")
                    and _original_matches_cleaned(original_path, cleaned_text, hashes["original_sha256"])):
                blockers[0]["affected_numbers"] = nums
        return blockers, checks, resolved_notes, warnings, deferred, units, meta, metadata_missing, source_diagnostic

    extraction_warnings_nonblocking = False
    ext_path = batch_dir / "01_source" / "extraction.json"
    if ext_path.is_file():
        try:
            ext_data = json.loads(ext_path.read_text(encoding="utf-8-sig"))
            ext_warn_list = ext_data.get("warnings") if isinstance(ext_data, dict) else None
            if isinstance(ext_warn_list, list):
                from . import docx_extract
                extraction_warnings_nonblocking = not docx_extract.blocking_warnings(ext_warn_list)
        except (OSError, ValueError, TypeError):
            extraction_warnings_nonblocking = False

    force_ai = str(os.environ.get("WF_FORCE_STRUCTURE_AI", "")).strip().lower() in ("1", "true", "yes")
    has_ai_marker = (batch_dir / "01_source" / "structure.ai.classified.json").is_file()
    ai_enabled = str(os.environ.get("WF_STRUCTURE_AI", "1")).strip().lower() not in ("0", "false", "no")
    unresolved_codes = {
        item.get("code") for item in (structure.get("unresolved") or [])
        if isinstance(item, dict) and not item.get("resolved")
    }
    clean_allowed = {
        "SOURCE_METADATA_MISSING", "ANSWER_APPENDIX_DETECTED",
        "INLINE_ANSWER_BLOCK", "QUESTION_NUMBER_INFERRED",
    }
    if extraction_warnings_nonblocking:
        clean_allowed.add("EXTRACTION_WARNING")
    non_clean_unresolved = unresolved_codes - clean_allowed
    chosen = None
    if not force_ai and not scoped and not has_ai_marker and ai_enabled and not non_clean_unresolved:
        det = _run_structural_pass(ai_classified=None)
        det_blockers, _, _, det_warnings, _, det_units, _, _, _ = det
        blocking_layout_warnings = [
            w for w in det_warnings
            if w.get("status") == "layout_review_required"
            and not (extraction_warnings_nonblocking and w.get("code") == "EXTRACTION_WARNING")
        ]
        # 仅在确定性结构无任何未解决阻断项（含未核实的 QUESTION_NUMBER_INFERRED）时走直过快路径。
        if det_units and not det_blockers and not blocking_layout_warnings:
            det[3].append({
                "code": "AI_STRUCTURE_SKIPPED",
                "blocking": False,
                "status": "deterministic_clean",
                "message": "确定性结构提取与全卷核验已完整通过，直接采用确定性结构",
            })
            chosen = det
    if chosen is None:
        ai_classified = None
        ai_warnings = []
        try:
            from . import structure_ai
            ai_classified = structure_ai.auto_classify(batch_dir)
            if ai_classified.get("units"):
                ai_warnings.append({"code": "AI_STRUCTURE_CLASSIFIED", "blocking": False,
                                    "status": "candidate", "message": ai_classified.get("reason", "AI结构归类已采用")})
        except (OSError, ValueError, RuntimeError, TypeError) as exc:
            ai_warnings.append({"code": "AI_STRUCTURE_UNAVAILABLE", "blocking": False,
                                "status": "fallback", "message": str(exc)[:160]})
        chosen = _run_structural_pass(ai_classified=ai_classified, initial_warnings=ai_warnings)
    blockers, checks, resolved_notes, warnings, deferred, units, meta, metadata_missing, source_diagnostic = chosen
    layout_required = ("[IMG " in raw_text or "[TABLE" in raw_text
                       or any(w.get("code") in {"OCR_VISUAL_REVIEW", "EXTRACTION_WARNING"}
                              for w in warnings)
                       or any(issue.get("code") in {"OCR_VISUAL_REVIEW", "EXTRACTION_WARNING"}
                              for issue in blockers))
    checks["grouping"] = "failed" if any("GROUP" in b["code"] for b in blockers) else "passed"
    # 用户 2026-11-17 明确：结构确认只负责产出结构化字段并填入，不做阻断校验；
    # 验证后移到最终格式闸门。因此任何结构疑点只作为 warning 留痕，不阻断阶段；
    # 只有“完全没有拆出任何单元”才算这一步无法产出（下一阶段无从消费）。
    structure_warnings = []
    if blockers:
        for blocker in blockers:
            structure_warnings.append({**blocker, "blocking": False, "status": "structure_warning"})
        warnings.extend(structure_warnings)
        blockers = []
    passed = bool(units)

    if band_evidence.get("bands") or band_evidence.get("features"):
        # Rebind current visual metadata even for reused source-valid AI units;
        # never let a unit's own stale band claim become evidence.
        for unit in units:
            source_section = unit.get("source_section") or {}
            annotated = lesson_bands.annotate_sections([source_section], band_evidence)[0]
            if annotated != source_section:
                unit["source_section"] = annotated
    confirmed = {
        "version": "1.0",
        "lesson_bands": band_evidence,
        "meta": meta,
        "units": units,
        "image_questions": image_questions,
        "removed_lines": removed,
        "source_sha256_note": "以 01_source/正文.txt 去噪后为准",
    }
    if units and all(u.get("identity_version") == "scoped-v1" for u in units):
        confirmed["identity_version"] = "scoped-v1"
    report = {
        "passed": passed,
        "input_hashes": {"01_source/正文.txt": hashes["source_sha256"],
                         "01_source/structure.json": hashes["structure_sha256"]},
        **hashes,
        "metadata_missing": metadata_missing,
        "warnings": warnings,
        "structure_warnings": structure_warnings,
        "deferred": deferred,
        "source_diagnostic": source_diagnostic,
        "layout_required": layout_required,
        "layout_review_command": "python -m workflow.layout_review --help" if layout_required else None,
        "checks": checks,
        "blockers": blockers,
        "unit_counts": {
            "total": len(units),
            "standalone": sum(1 for u in units if u["unit_kind"] == "standalone"),
            "group": sum(1 for u in units if u["unit_kind"] == "group"),
        },
        "removed_count": len(removed),
        "resolved_evidence": resolved_notes,
        "meta": meta,
    }
    output_root = Path(output_dir) if output_dir is not None else batch_dir
    confirmed_path = output_root / "01_source" / "structure.confirmed.json"
    _write_json(confirmed_path, confirmed)
    report["confirmed_sha256"] = mf.sha256_file(confirmed_path)
    report["input_hashes"]["01_source/structure.confirmed.json"] = report["confirmed_sha256"]
    _write_json(output_root / "07_review" / "structure-gate.json", report)
    return report


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
