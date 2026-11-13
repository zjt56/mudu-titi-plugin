"""Stable internal identities for lesson-local numbering; printed numbers stay intact.

Only explicitly scoped units use question IDs as flat answer keys. Legacy batches
retain numeric keys. Source coordinates, not answers or predicted types, identify
new units. Existing IDs are carried forward on a unique source-anchor match.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import re

VERSION = "scoped-v1"
FIELDS = ("identity_version", "section_id", "source_section", "question_ids")


def question_key(unit: dict, number) -> str:
    key = str(number)
    if unit.get("identity_version") == VERSION:
        value = (unit.get("question_ids") or {}).get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{unit.get('unit_id', '')}: missing question identity for {key}")
        return value
    return key


def unit_key(unit: dict):
    """Explicit IDs win; a tuple is retained for old source-only units."""
    uid = unit.get("unit_id") or unit.get("id")
    return str(uid) if uid else tuple(unit.get("numbers") or [])


def source_key(unit: dict):
    return unit_key(unit) if unit.get("identity_version") == VERSION else tuple(unit.get("numbers") or ([unit["number"]] if unit.get("number") else []))


def fields(unit: dict) -> dict:
    return {key: deepcopy(unit[key]) for key in FIELDS if key in unit}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _range(unit):
    span = unit.get("source_span") or {}
    return span.get("start_line"), span.get("end_line")


def printed_anchors(lines):
    """Printed item positions in an already bounded lesson section (zero based)."""
    from .preview import _strong_number, _weak_numbers
    result = []
    for index, line in enumerate(lines):
        nums = []
        strong = _strong_number(line)
        if strong is not None:
            nums.append(strong)
        nums.extend(n for n in _weak_numbers(line) if n not in nums)
        from .source_anchors import printed_tokens
        literal = [number for _offset, number in printed_tokens(line)]
        for n in literal:
            if n not in nums:
                nums.append(n)
        # Preserve repeated printed occurrences; strong/weak views of the same
        # token are one occurrence, two literal inline tokens are not.
        for n in set(literal):
            nums.extend([n] * max(0, literal.count(n) - nums.count(n)))
        result.extend((index, n) for n in nums)
    return result


def question_position(lines, number):
    from .preview import _find
    pos = _find(lines, int(number))
    if pos is not None:
        return pos
    return next((i for i, n in printed_anchors(lines) if n == int(number)), None)


def numbered_projection(lines):
    """Split inline printed items for parsing only; never rewrite source evidence."""
    from .source_anchors import project_numbered
    return project_numbered(lines)


def _anchor(unit, lines):
    start, end = _range(unit)
    if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
        raise ValueError("scoped identity requires a valid source span")
    wanted = set(unit.get("numbers") or [])
    for index, number in printed_anchors(lines[start - 1:end]):
        if number in wanted:
            return start + index
    # Unnumbered source tasks are bound to an explicit original section window.
    if (unit.get("source_span") or {}).get("number_binding") in {"single_writing_section", "unnumbered_section"}:
        return start
    raise ValueError("scoped identity requires a printed question anchor")


def assign_scoped(units, source_lines, sections=None, previous=None, visual_columns=None) -> list[dict]:
    from .sync_practice import heading_candidates, plain_text
    lines = source_lines.splitlines() if isinstance(source_lines, str) else list(source_lines)
    candidates = heading_candidates("\n".join(lines))
    # 标题规则可能只识别部分大题；原提取器已定位的其它来源窗口也须参与归属。
    # 同一坐标仍优先使用规则候选，不覆盖其标题、课时上下文与 occurrence。
    known_starts = {row["start_line"] for row in candidates}
    for index, row in enumerate(sections or [], 1):
        start = row.get("start_line") or row.get("source_line") or row.get("line")
        if type(start) is int and row.get("lesson_band"):
            for candidate in candidates:
                if candidate["start_line"] == start:
                    candidate["lesson_band"] = deepcopy(row["lesson_band"])
                    candidate["end_line"] = min(candidate.get("end_line", len(lines)),
                                                 row.get("end_line") or row.get("source_end_line") or len(lines))
        if type(start) is int and start not in known_starts:
            candidates.append(dict(row, start_line=start, occurrence=index,
                                   title=row.get("title") or row.get("heading") or ""))
            known_starts.add(start)
    candidates.sort(key=lambda row: row["start_line"])
    for index, row in enumerate(candidates):
        boundary = candidates[index + 1]["start_line"] - 1 if index + 1 < len(candidates) else len(lines)
        row["end_line"] = min(row.get("end_line") or row.get("source_end_line") or len(lines), boundary)
    prior = {}
    for row in previous or []:
        if row.get("identity_version") == VERSION:
            try:
                prior.setdefault(_anchor(row, lines), []).append(row)
            except ValueError:
                pass
    result, used_units, used_questions = [], set(), set()
    for original in units:
        row = deepcopy(original)
        anchor = _anchor(row, lines)
        # A verified visual question window can own a task directly, without a
        # printed heading. Its label is metadata, never invented Word content.
        column_id = row.get("column_id")
        visual = [c for c in visual_columns or [] if c.get("column_id") == column_id
                  and c.get("role") == "question"
                  and (c.get("start_line"), c.get("end_line")) == _range(row)] if column_id else []
        if len(visual) == 1:
            column = visual[0]
            owner = {"title": column.get("label") or "", "context": "visual-column:" + column_id,
                     "start_line": column.get("heading_line") or column["start_line"],
                     "end_line": column["end_line"], "occurrence": 1,
                     "visual_column_id": column_id, "visual_column": deepcopy(column)}
            owners = [owner]
        else:
            owners = [c for c in candidates if c["start_line"] <= anchor <= c.get("end_line", len(lines))]
        if len(owners) != 1:
            raise ValueError(f"question at source line {anchor} has no unique section")
        owner = owners[0]
        title = str(owner.get("title") or "")
        context = str(owner.get("context") or "")
        occurrence = owner.get("occurrence") or candidates.index(owner) + 1
        plain = plain_text(title).strip()
        ordinal = re.match(r"^([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫIVX]+[.．、]?)", plain)
        sid = "sec-" + _digest(f"{context}\n{title}\n{occurrence}")
        old = prior.get(anchor, [])
        old = old[0] if len(old) == 1 else {}
        sid = row.get("section_id") or old.get("section_id") or sid
        # The printed anchor is independent of the retained window and type.
        uid = row.get("unit_id") if row.get("identity_version") == VERSION else None
        offset = (row.get("source_span") or {}).get("start_offset")
        column_suffix = f"-c{offset}" if type(offset) is int else ""
        uid = uid or old.get("unit_id") or f"{sid}-u{anchor}{column_suffix}"
        row.update(identity_version=VERSION, section_id=sid, unit_id=uid,
                   source_section={"title": title, "ordinal": ordinal.group(1) if ordinal else "",
                                   "context": context, "occurrence": occurrence,
                                   "start_line": owner["start_line"], "end_line": owner.get("end_line", len(lines))})
        if owner.get("lesson_band"):
            row["source_section"]["lesson_band"] = deepcopy(owner["lesson_band"])
        if owner.get("visual_column_id"):
            row["source_section"]["visual_column_id"] = owner["visual_column_id"]
            row["source_section"]["visual_column"] = deepcopy(owner["visual_column"])
        start, end = _range(row)
        covering = [c for c in visual_columns or []
                    if c.get("role") != "extension"
                    and c.get("start_line", 0) <= start <= end <= c.get("end_line", 0)]
        if covering:
            narrowest = min(covering, key=lambda c: (c["end_line"] - c["start_line"],
                                                   0 if c.get("role") == "question" else 1))
            by_id = {c["column_id"]: c for c in visual_columns}
            path, current, visited = [], narrowest, set()
            while current and current["column_id"] not in visited:
                visited.add(current["column_id"])
                path.append({k: current[k] for k in ("column_id", "label", "role")})
                current = by_id.get(current.get("parent_id"))
            row["source_section"]["column_path"] = list(reversed(path))
        existing = row.get("question_ids") or old.get("question_ids") or {}
        start, end = _range(row)
        qids = {}
        for n in row.get('numbers') or []:
            local = lines[start - 1:end]
            pos = question_position(local, n)
            qanchor = start + pos if pos is not None else anchor
            qids[str(n)] = existing.get(str(n)) or f'{sid}-l{qanchor}{column_suffix}-q{n}'
        row['question_ids'] = qids
        keys = list(row["question_ids"].values())
        if uid in used_units or used_questions.intersection(keys) or len(set(keys)) != len(keys):
            raise ValueError("duplicate scoped unit/question identity")
        used_units.add(uid)
        used_questions.update(keys)
        result.append(row)
    return result


def validate_units(units) -> bool:
    """Reject partial/mixed scoped identities rather than falling back to numbers."""
    if not any(u.get("identity_version") == VERSION for u in units):
        return True
    seen_units, seen_questions = set(), set()
    for unit in units:
        nums = unit.get("numbers") or []
        qids = unit.get("question_ids") or {}
        uid = unit.get("unit_id")
        if (unit.get("identity_version") != VERSION or not uid or uid in seen_units
                or not unit.get("section_id") or not isinstance(unit.get("source_section"), dict)
                or set(qids) != {str(n) for n in nums} or len(set(nums)) != len(nums)):
            return False
        seen_units.add(uid)
        for value in qids.values():
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value) or value in seen_questions:
                return False
            seen_questions.add(value)
    return True
