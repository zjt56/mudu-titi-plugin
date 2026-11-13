"""Read-only current-source proof for numbered response blanks inside native tables.

This proves that a blank exists and its complete table is retained. It does not
certify the semantic placement of other stems/options or resolve review findings.
"""
from collections import Counter
import json
from pathlib import Path
import re


def verified_groups(batch_path):
    """Rebuild evidence from the supplied batch location, never candidate flags."""
    from . import denoise, question_draft, source_objects
    from .agent_tasks import confirmed_units
    from .wire_format import normalize_blank_text
    path = Path(batch_path).resolve()
    root = path.parent.parent if path.parent.name == "06_batch" else path.parent
    try:
        units = confirmed_units(root)
        lines = denoise.clean_body((root / "01_source/正文.txt").read_text(encoding="utf-8-sig"))[0].splitlines()
        extraction = json.loads((root / "01_source/extraction.json").read_text(encoding="utf-8-sig"))
        eligible = {}
        for unit in units:
            span, section = unit.get("source_span") or {}, unit.get("source_section") or {}
            start, end = span.get("start_line"), span.get("end_line")
            heading, stop = section.get("start_line"), section.get("end_line")
            if (unit.get("identity_version") != "scoped-v1" or unit.get("unit_kind") != "group"
                    or span.get("basis") not in {"ai_classified", "cleaned_source"}
                    or any(type(n) is not int for n in (start, end, heading, stop))
                    or not 1 <= heading < start <= end <= stop <= len(lines)
                    or lines[heading - 1].strip() != str(section.get("title") or "").strip()):
                continue
            text = "\n".join(lines[start - 1:end]).strip()
            if text != str(unit.get("stem") or "").strip():
                continue
            markers = list(source_objects.table_markers(text))
            if not markers or text.count("[TABLE") != text.count("[/TABLE]"):
                continue
            proof, valid = {}, True
            blank_pattern = re.compile(r"(?<!\d)(\d+)[.．]\s*_{2,}")
            all_blanks = Counter(int(m.group(1)) for m in blank_pattern.finditer(normalize_blank_text(text)))
            for marker in markers:
                table, reason = source_objects.resolve_table(extraction, marker)
                if table is None or not table.get("source_ref"):
                    valid = False
                    break
                # Only literal numbered answer blanks count, never ordinary prose
                # numbers or an underlined word. Keep every occurrence for uniqueness.
                normalized = normalize_blank_text(marker)
                for match in blank_pattern.finditer(normalized):
                    number = int(match.group(1))
                    if all_blanks[number] != 1:
                        continue
                    source_lines = [start + i for i, line in enumerate(lines[start - 1:end])
                                    if any(int(m.group(1)) == number for m in blank_pattern.finditer(normalize_blank_text(line)))]
                    if len(source_lines) != 1:
                        continue
                    qid = (unit.get("question_ids") or {}).get(str(number), "")
                    if not re.fullmatch(re.escape(unit["section_id"]) + rf"-l{source_lines[0]}(?:-c\d+)?-q{number}", qid):
                        continue
                    proof.setdefault(number, []).append({"table_number": table["number"],
                        "source_ref": table["source_ref"], "source_line": source_lines[0], "marker": marker})
            if valid and proof:
                eligible[unit["unit_id"]] = (unit, proof)
        if not eligible:
            return {}
        draft = question_draft.build(root)
        result = {}
        for row in draft["items"]:
            uid = row["unit_id"]
            if uid not in eligible:
                continue
            unit, occurrences = eligible[uid]
            subs = row.get("subquestions") or []
            numbers = {int(n) for n in unit["numbers"]}
            blanks = {n: refs[0] for n, refs in occurrences.items()
                      if len(refs) == 1 and n in numbers}
            blanks = {n: ref for n, ref in blanks.items()
                      if any(c.get("number") == n and not c.get("stem") and not c.get("options") for c in subs)}
            if not blanks:
                continue
            issues = [p for p in draft["issues"] if p.get("unit_id") == uid]
            if any(p.get("code") != "CHILD_CONTENT_UNRESOLVED" or p.get("number") not in blanks for p in issues):
                continue
            binding = draft["source_bindings"][uid]
            if binding.get("basis") != "group_span_window" or not row.get("materialBlocks"):
                continue
            result[uid] = {"item": row, "blanks": blanks, "binding": binding}
        return result
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError, IndexError):
        return {}
