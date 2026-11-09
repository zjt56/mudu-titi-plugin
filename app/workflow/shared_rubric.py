"""Source-backed section instructions shared by cloze/reading groups."""
import re

SECTION_LEAD = re.compile(r"^\s*第[一二三四五六七八九十百零]+\s*(?:部分|节)\s*")
_TYPES = {"完形填空", "语法选择", "阅读理解", "任务型阅读"}


def instruction(line, kind=""):
    from .extract import _plain, guide_numbers, material_label
    from .preview import _strong_number
    text = SECTION_LEAD.sub("", _plain(line).strip(), count=1)
    if (kind and kind not in _TYPES) or _strong_number(line) is not None or material_label(line):
        return False
    # Numbered lead-ins belong exclusively to their explicitly named group.
    if guide_numbers(line) or re.search(r"第\s*\d+|\d+\s*(?:至|到|[-—~～])\s*\d+\s*题", text):
        return False
    return bool(re.match(r"阅读(?:下列|下面)短文", text)
                or re.match(r"(?:将答案填写|答案写在|从每小题所给)", text))


def section_rubric(lines, start_line, kind=""):
    """Return a complete instruction block before the first article in a section.

    Coordinates must refer to ``lines``. Never select instructions buried in a
    sibling's article/questions: only the block immediately following a section
    heading (or before the first A label) can be inherited.
    """
    from .extract import _plain
    from .preview import SECTION
    if kind and kind not in _TYPES:
        return []
    if type(start_line) is not int or not 1 <= start_line <= len(lines) + 1:
        return []
    stop = start_line - 1
    heading = None
    for i in range(stop - 1, -1, -1):
        if SECTION.match(_plain(lines[i]).strip()):
            heading = i
            break
    if heading is None:
        return []
    i = heading if instruction(lines[heading], kind) else heading + 1
    block = []
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if not instruction(line, kind):
            break
        block.append(line)
        i += 1
    # A numbered/local lead-in or article at section start is not a shared rubric.
    if not block or not re.search(r"阅读(?:下列|下面)短文", _plain(block[0])):
        return []
    return block
