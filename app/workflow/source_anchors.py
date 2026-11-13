"""Conservative printed item boundaries; source offsets always refer to raw text."""
from __future__ import annotations
import re

# 版面数据不是印刷题号：`<img …>` 标签属性（width/height）和 `img_in_image_box_X_Y_W_H.jpg`
# 这样由 OCR 生成的图片文件名里带着坐标数字，直接扫「数字+句点/下划线」会把它们误认成
# 小题号（实测：一张 52_617_158_734 的插图号被收成 52/617/158/734 四个假题号，并把
# 整段大题的分题集合污染成 20+ 个号）。剥掉标签与文件名，但保留 `[IMG …]` 这类正文占位，
# 因为「图注空」`N.[IMG …](提示)` 依赖它才是合法小题号。
_HTML_TAG = re.compile(r"<[^>]*>")
_VISUAL_NAME = re.compile(r"img_in_image_box[\w.]*\.(?:jpe?g|png|gif|bmp|webp|tiff?)", re.I)


def strip_visual_markup(line: str) -> str:
    """Remove layout-only markup whose numbers are coordinates, not printed items."""
    if "<" not in line and "img_in_image_box" not in line:
        return line
    return _VISUAL_NAME.sub(" ", _HTML_TAG.sub(" ", line))


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
        # A continuation line can have no leading question number and still
        # introduce the next item after a real column separator. Keep its prefix
        # with the preceding item, then expose the column's printed number.
        leading = _LEADING.search(line)
        if not tokens or len(tokens) == 1 and leading:
            projected.append(line)
            continue
        if not leading and line[:tokens[0][0]].strip():
            projected.append(line[:tokens[0][0]].rstrip())
        for index, (offset, _number) in enumerate(tokens):
            left = 0 if index == 0 and leading else offset
            right = tokens[index + 1][0] if index + 1 < len(tokens) else len(line)
            projected.append(line[left:right].strip())
    return projected
