"""Conservative printed item boundaries; source offsets always refer to raw text."""
from __future__ import annotations
import re

# A later item needs a real column boundary, never an ordinary prose space.
_COLUMN = re.compile(r"(?:[\t\u3000]+|[ \xa0]{2,})(?:\*{1,3})?(?:[（(][ \u3000]*[）)][ \u3000]*)?([1-9]\d{0,2})\s*[.．、](?!\d)")
_LEADING = re.compile(r"^\s*(?:\*{1,3})?(?:[（(][ \u3000]*[）)]\s*)?([1-9]\d{0,2})\s*[.．、](?!\d)")


def printed_tokens(line: str) -> list[tuple[int, int]]:
    """Return (raw offset, printed number), including tab-separated columns.

    Sentences such as ``Emma is in Class 2.`` and ``No. 5.`` do not
    create a second question. Dialogue blanks use the shared weak-number parser.
    """
    result = []
    first = _LEADING.search(line)
    if first:
        result.append((first.start(1), int(first.group(1))))
    for match in _COLUMN.finditer(line):
        point = (match.start(1), int(match.group(1)))
        if point not in result:
            result.append(point)
    return sorted(result)


def project_numbered(lines):
    """Split only proven same-line printed columns for parsing, not evidence."""
    projected = []
    for line in lines:
        tokens = printed_tokens(line)
        if len(tokens) < 2:
            projected.append(line)
            continue
        for index, (offset, _number) in enumerate(tokens):
            left = 0 if index == 0 else offset
            right = tokens[index + 1][0] if index + 1 < len(tokens) else len(line)
            projected.append(line[left:right].strip())
    return projected
