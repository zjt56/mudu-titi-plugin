# -*- coding: utf-8 -*-
"""Question-granularity content contract and deterministic source checks."""
from __future__ import annotations

import json
import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .preview import slice_text
from .question_identity import source_key

IMAGE = re.compile(r"\[IMG\s+([^\]]+)\]")
FORBIDDEN = re.compile(r"(?:考试时间|满分\s*\d+分|本试卷共|扫码练听力|答案链接|初中学业水平考试)")
# A phonetic target may touch option A: ``/e/A.eye``. Only a short
# slash-delimited IPA token permits that boundary; URL/path slashes stay excluded.
_IPA = r"[a-zɑɒæəɛɜɪʊʌɔθðʃʒŋɡːˈˌ]"
OPTION = re.compile(r"(?:(?<![A-Za-z0-9&/.\-])|(?<=/" + _IPA + r"/)|(?<=/" + _IPA + r"{2}/)|(?<=/" + _IPA + r"{3}/))([A-H])[.．、]\s*")

CONTRACT = {
    "standalone": ["题号", "题干/作答指令", "本题选项或作答空位（如有）", "答案及来源", "题内图片/图标引用（如有）"],
    "group": ["共享材料或表格", "组内每个小题的题号和题干/空位", "各小题选项（如有）", "材料与小题的归属关系", "题内图片/图标及归属（如有）"],
    "exclude": ["卷名、年份页眉、考试时长和总分", "整卷作答说明", "扫码提示、答案册链接、装饰图和页眉 logo", "与本题/本题组无关的相邻题"],
}


def _image_names(lines: list[str]) -> list[str]:
    return [match.group(1).strip() for line in lines for match in IMAGE.finditer(line)]


def _blank_table_grids(text: str) -> int:
    """Count [TABLE n] grids whose every cell line is empty (student answer grids)."""
    blank = 0
    for match in re.finditer(r"\[TABLE(?:\s+\d+)?\](.*?)\[/TABLE\]", str(text), re.S):
        body = [re.sub(r"\s+", "", row) for row in match.group(1).splitlines()]
        if not any(body):  # every non-empty content line missing => empty grid
            blank += 1
    return blank


# 原卷“合法的空/图形态”统一分类。凡属这里任一形态，都不表示题目内容缺失，
# 不得阻断结构确认或前置组装；真实截断/缺选项/题干错配仍按原校验阻断。
LEGITIMATE_SHAPES = ("image_only", "blank_answer_grid", "unnumbered_no_image")


def legitimate_source_shape(unit: dict, appendix_text: str | None = None) -> str | None:
    """Classify a legitimate source shape that must not be treated as missing content.

    Returns one of LEGITIMATE_SHAPES, or None when the unit is ordinary text (no
    excuse) / genuinely incomplete. Covers exactly three recurring patterns:
    1. ``image_only``: only image/table markers, no readable text stem (图片题);
    2. ``blank_answer_grid``: every table is an empty student answer grid and the
       unit's numbers appear in the answer appendix (答案见卷末);
    3. ``unnumbered_no_image``: a whole source section with no printed child
       number and no image (e.g. 字母歌补全), which still owns real body text.
    """
    stem = str(unit.get("stem") or "")
    numbers = [str(n) for n in (unit.get("numbers") or [])]
    lines = [line for line in stem.splitlines()]
    non_empty = [line.strip() for line in lines if line.strip()]
    has_image = any(IMAGE.search(line) for line in lines)
    non_image = [line for line in non_empty
                 if not IMAGE.search(line) and not re.fullmatch(r"\[/?TABLE[^\]]*\]", line)]
    if has_image and not non_image:
        return "image_only"
    table_count = len(re.findall(r"\[TABLE(?:\s+\d+)?\]", stem))
    if table_count and _blank_table_grids(stem) == table_count and appendix_text and numbers:
        if all(re.search(r"(?<![\d])" + re.escape(number) + r"\s*[.．、]", appendix_text)
               for number in numbers):
            return "blank_answer_grid"
    if non_image and not table_count and unit.get("numbers") == [1] and \
            (unit.get("source_span") or {}).get("number_binding") == "unnumbered_section":
        return "unnumbered_no_image"
    return None


def capability_limited_source(unit: dict, appendix_text: str | None = None) -> bool:
    """True when the unit's incompleteness is a capability/原卷形态 limit, not missing text.

    Thin boolean wrapper over :func:`legitimate_source_shape` for the preassemble
    ``contentComplete`` gate: image-only units and blank answer grids (answers in
    the appendix) are legitimate, everything else stays a real completeness block.
    """
    return legitimate_source_shape(unit, appendix_text) in ("image_only", "blank_answer_grid")


def self_contained_group(unit: dict) -> bool:
    """组题是否「每小题目包含内容、无共享 material」的合法形态。

    四线三格写单词（每小题目带空白作答网格）、看图题（每小题目带图）等，组级本就没有
    共享材料，空 material 不是丢内容（硬规则 9「空白作答网格」、硬规则 27）。判据严格：
    必须**每个**子题都有非空 stem，且每个子题的 stem 都自带表格(空白网格)或图片。
    阅读理解/完形/任务型等共享语篇的组题不满足（子题只有题目文字），仍须 material。
    结构体检与发布闸门的 A8 共用此口径，避免两处各写一套。
    """
    subs = unit.get("subquestions")
    if not isinstance(subs, list) or not subs:
        return False
    for child in subs:
        if not isinstance(child, dict):
            return False
        stem = str(child.get("stem") or "")
        if not stem.strip():
            return False
        if not re.search(r"\[TABLE|\[IMG", stem):
            return False
    return True


_IMG_REF = re.compile(r"\[IMG\s+[^\]]+?\]")
_OPTION_LABEL_CHARS = "ABCDEFGH"


def image_option_evidence(unit: dict, local_lines: list[str], image_semantics: dict | None = None) -> dict | None:
    """图片选项题的视觉/原文佐证：证明“缺的选项其实是图、且图都在”。

    用于修正正则对图片选项前置排版（`[IMG x]A.`）切割错位导致的
    SOURCE_OPTIONS_INCOMPLETE 误报。判定非常保守：
    - 只有该单元是“选项都是图”的题（窗口内出现 `[IMG]` 且紧邻选项标签 `X.`）才生效；
    - 窗口内 `[IMG]` 引用数必须 ≥ 该题应有选项数（source_expected_options 或看到的标签数）；
    - 若提供 image_semantics，这些图不能是纯装饰（icon），否则不豁免。
    返回 {is_image_option, image_count, option_labels, visual_kinds} 或 None（不适用）。
    """
    text = "\n".join(local_lines)
    imgs = _IMG_REF.findall(text)
    # 必须有“图紧贴选项标签”的形态，才认定为图片选项题（避免把普通带图题干当选项图）。
    if not imgs or not re.search(r"\[IMG\s+[^\]]+?\]\s*[A-H][.．、]", text):
        return None
    labels = sorted(set(re.findall(r"(?m)(?:^|\s)\s*([A-H])[.．、]", OPTION.sub(r"\1.", text))))
    # 期望选项数：优先 source_expected_options，否则用窗口里出现的选项标签数。
    expected = int(unit.get("source_expected_options") or 0) or len(labels)
    names = [re.sub(r"^\[IMG\s+|\]$", "", m).split("/")[-1] for m in imgs]
    kinds = {}
    if isinstance(image_semantics, dict):
        sem = {str(it.get("path") or "").split("/")[-1]: it for it in image_semantics.get("items") or []}
        kinds = {n: (sem.get(n) or {}).get("kind") for n in names}
        if kinds and all(k == "icon" for k in kinds.values() if k):
            return None  # 全是装饰图标，不当作选项图豁免
    if len(imgs) < max(expected, 2):
        return None  # 图不足以覆盖选项数，不豁免（真实缺项照报）
    return {"is_image_option": True, "image_count": len(imgs),
            "option_labels": labels, "expected": expected, "visual_kinds": kinds}



def audit_question(source_lines: list[str], number: int, item: dict | None = None,
                   media_dir: str | Path | None = None) -> dict:
    """Check a source slice for obvious scope/structure defects; visual mapping stays human-reviewed."""
    item = item if isinstance(item, dict) else {}
    lines = slice_text(source_lines, int(number))
    text = "\n".join(lines)
    issues: list[dict] = []
    if not lines:
        issues.append({"code": "QUESTION_NOT_FOUND", "severity": "blocker", "message": f"原卷找不到第 {number} 题的可靠题号边界"})
    for line_no, line in enumerate(lines, 1):
        if FORBIDDEN.search(line):
            issues.append({"code": "IRRELEVANT_HEADER", "severity": "major", "message": f"切片包含卷头/整卷说明：第 {line_no} 行 {line.strip()}"})
    if text.count("[TABLE") != text.count("[/TABLE]"):
        issues.append({"code": "TABLE_UNBALANCED", "severity": "blocker", "message": "表格标记未成对，不能确认表格范围"})
    refs = _image_names(lines)
    missing = []
    if media_dir is not None:
        root = Path(media_dir)
        for name in refs:
            if Path(name).name != name or not (root / name).is_file():
                missing.append(name)
    if missing:
        issues.append({"code": "IMAGE_MISSING", "severity": "blocker", "message": "原文引用的图片文件缺失：" + ", ".join(missing)})
    options = item.get("options") or []
    source_letters = sorted(set(OPTION.findall(text)))
    candidate_letters = sorted(set(m.group(1) for option in options for m in OPTION.finditer(str(option)))) if isinstance(options, list) else []
    if item and source_letters and source_letters != candidate_letters:
        issues.append({"code": "OPTIONS_MISMATCH", "severity": "major", "message": f"原卷选项 {source_letters} 与候选选项 {candidate_letters} 不一致"})
    if item:
        item_number = str(item.get("number", "")).strip()
        if item_number and item_number != str(number):
            issues.append({"code": "QUESTION_NUMBER_MISMATCH", "severity": "blocker", "message": f"候选题号 {item_number} 与当前题号 {number} 不一致"})
    return {"number": int(number), "passed": not issues, "source_lines": lines,
            "image_refs": refs, "table_present": "[TABLE" in text, "issues": issues,
            "visual_review_required": bool(refs) or "[TABLE" in text}


def _canonical(value: str) -> str:
    """Ignore layout whitespace/markup, while retaining the source's words."""
    from .extract import _plain
    value = re.sub(r"\[/?TABLE[^\]]*\]", "", str(value))
    return re.sub(r"[\W_]", "", re.sub(r"<[^>]+>", "", _plain(value)), flags=re.UNICODE).casefold()


# 「第一节　阅读下列短文…」是同一大题下的分段标记，题干本身从「阅读下列」开始。
# 只用于识别与去重豁免，原文行仍按原样保存。
from .shared_rubric import SECTION_LEAD


def _generic_choice_instruction(line: str, question_type: str) -> bool:
    """Recognize a complete reusable rubric, never an article or numbered lead-in."""
    if question_type not in {"完形填空", "语法选择", "阅读理解"}:
        return False
    from .extract import _plain
    text = SECTION_LEAD.sub("", _plain(str(line)).strip(), count=1)
    return bool(re.fullmatch(
        r"阅读(?:下列|下面)短文从每小题所给的abcd四个选项中选出"
        r"(?:一个)?(?:可以填入空白处的)?最佳选项", _canonical(text)))


def _field_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_field_text(v) for v in value)
    if isinstance(value, dict):
        return "\n".join(_field_text(v) for k, v in value.items()
                         if k in {"text", "content", "value", "stem", "material", "rows", "cells", "blocks", "options", "subquestions"})
    return ""


# Only rendered content can satisfy source evidence, never answers/review metadata.
_IMAGE_CONTENT_FIELDS = {
    "text", "content", "value", "stem", "material", "materialBlocks",
    "material_blocks", "rows", "cells", "blocks",
}
_HTML_IMAGE = re.compile(r"<img\b[^>]*>", re.I)
_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\(([^\s)]+)(?:\s+[^)]*)?\)")


def _media_name(value: str) -> str:
    try:
        return unquote(urlsplit(str(value)).path).replace("\\", "/").rsplit("/", 1)[-1]
    except ValueError:
        return ""


class _ImageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.names = set()

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            src = dict(attrs).get("src")
            if src:
                self.names.add(_media_name(src))


def _image_refs(value, option_key: str | None = None) -> set[str]:
    """Collect one content owner's images; do not descend into sibling options/children."""
    if isinstance(value, str):
        parser = _ImageParser()
        parser.feed(value)
        return (set(_image_names(value.splitlines())) | parser.names
                | {_media_name(m.group(1)) for m in _MARKDOWN_IMAGE.finditer(value)})
    if isinstance(value, list):
        return set().union(*(_image_refs(v, option_key) for v in value)) if value else set()
    if not isinstance(value, dict):
        return set()
    refs = set()
    if value.get("type") == "image":
        refs.update(_media_name(value[k]) for k in ("src", "url", "path") if isinstance(value.get(k), str))
    for key in _IMAGE_CONTENT_FIELDS:
        refs.update(_image_refs(value.get(key), option_key))
    attachments = value.get("attachments") or []
    for attachment in attachments if isinstance(attachments, list) else []:
        if isinstance(attachment, str):
            refs.add(_media_name(attachment))
            continue
        if not isinstance(attachment, dict) or attachment.get("kind", "image") != "image":
            continue
        target = str(attachment.get("optionKey") or "").strip().rstrip(".．、")
        if target and target != option_key:
            continue
        if attachment.get("placement") == "option" and not option_key:
            continue
        refs.update(_media_name(attachment[k]) for k in ("url", "src", "path", "name")
                    if isinstance(attachment.get(k), str))
        refs.update(_image_refs(attachment, option_key))
    return refs


def _option_image_refs(option, owner: dict, label: str) -> set[str]:
    # A parent's explicitly labeled attachment can belong to its own option,
    # but an unbound parent image cannot stand in for an arbitrary option.
    bound = [a for a in owner.get("attachments") or [] if isinstance(a, dict)
             and str(a.get("optionKey") or "").strip().rstrip(".．、") == label]
    return _image_refs(option, label) | _image_refs({"attachments": bound}, label)


def _canonical_image_text(value: str) -> str:
    """Compare words separately from the independently checked image bindings."""
    value = IMAGE.sub("", str(value))
    value = _HTML_IMAGE.sub("", value)
    return _canonical(_MARKDOWN_IMAGE.sub("", value))


def question_parts(lines: list[str], number: int, *, scoped=False) -> dict:
    """Return this child's exact source stem and ordered labeled options."""
    if scoped:
        from .question_identity import numbered_projection
        lines = numbered_projection(lines)
    from .extract import _plain, material_label, guide_numbers
    from .preview import _find, _strong_number, SECTION, LEADIN
    start = _find(lines, number)
    if start is None:
        return {"stem": "", "options": [], "found": _find(lines, number, strong=False) is not None}
    end = len(lines)
    for i in range(start + 1, len(lines)):
        plain = _plain(lines[i]).strip()
        # 【文章大意】/【解析】等字段标题是材料末边界：不能并进最后一道题的选项/题干。
        if re.match(r"^[\[【]?\s*(?:文章大意|解析|答案|分析)\s*[\]】]?\s*[：:]?", plain):
            end = i
            break
        if (_strong_number(plain) is not None or SECTION.match(plain)
                or material_label(plain) or guide_numbers(plain) or LEADIN.match(plain)):
            end = i
            break
    text = "\n".join(lines[start:end])
    text = re.sub(r"^\s*(?:[（(]\s*[）)]\s*)?\d{1,3}\s*[.．、)]", "", _plain(text))
    matches = list(OPTION.finditer(text))
    # Some papers print an option's image *before* its label: "[IMG a.png]A.…".
    # The segment between label i and label i+1 then holds the *next* image, so a
    # naive split shifts every image one label left (and empties the last option).
    # Detect the leading-image layout and attribute each leading image to its own
    # label; the dangling pre-A image must leave the stem too.
    options = [{"label": m.group(1), "text": text[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)].strip()}
               for i, m in enumerate(matches)]
    if matches:
        stem_text = text[:matches[0].start()]
        leading, filled = _split_leading_option_images(text, matches)
        if leading is not None:
            # Pure image-option layout: each option's content is the image(s)
            # that sit immediately before its own label. The trailing segment is
            # the *next* option's leading image, so it must not be re-attached
            # here (only non-image trailing text, if any, is kept as the option's
            # own caption).
            shifted = []
            for i, opt in enumerate(options):
                lead = leading.get(opt["label"], "")
                tail_raw = text[matches[i].end():matches[i + 1].start() if i + 1 < len(matches) else len(text)]
                tail_text = _IMG_REF.sub("", tail_raw).strip()
                combined = lead if not tail_text else (lead + "\n" + tail_text if lead else tail_text)
                shifted.append({"label": opt["label"], "text": combined.strip()})
            options = shifted
            stem_text = filled
        return {"stem": stem_text.strip(), "options": options, "found": True}
    return {"stem": text.strip(), "options": options, "found": True}


def _split_leading_option_images(text: str, matches) -> tuple[dict | None, str]:
    """Return ({label: leading [IMG]}, stem_without_those_images) for `[IMG]X.` layouts.

    The pure image-option form prints each option's image immediately before its
    label ("[IMG a.png]A.\\t[IMG b.png]B."). We only treat this as the image-option
    layout when *every* label is directly preceded by at least one image marker
    with only whitespace between image and label. Otherwise return (None, '').
    """
    if len(matches) < 2:
        return None, ""
    leading: dict[str, str] = {}
    removals: list[tuple[int, int]] = []
    for m in matches:
        # Look back from the label start over whitespace, expecting image markers.
        j = m.start()
        gap_start = j
        imgs: list[str] = []
        # Walk backwards consuming whitespace then [IMG ...] tokens, possibly repeated.
        while True:
            k = gap_start
            while k > 0 and text[k - 1] in " \t\u3000\r\n":
                k -= 1
            mm = None
            for cand in _IMG_REF.finditer(text, 0, k):
                if cand.end() == k:
                    mm = cand
                    break
            if mm is None:
                break
            imgs.insert(0, mm.group(0))
            removals.append((mm.start(), mm.end()))
            gap_start = mm.start()
        if not imgs:
            return None, ""  # a label without an immediately-preceding image
        leading[m.group(1)] = "\n".join(imgs)
    # Remove the leading images from the stem region (they are option content now).
    stem_text = text[:matches[0].start()]
    for s, e in sorted(removals, reverse=True):
        if s < matches[0].start():
            stem_text = stem_text[:s] + stem_text[e:]
    return leading, stem_text


def strip_writing_answer_space(text: str) -> str:
    """Remove only empty writing space; keep words, images and meaningful marks.

    Writing response lines are drawn by the UI. They may be empty underlined
    runs, underscore-only lines, or a blank suffix after the supplied opening
    sentence. A marked word (including a spelling initial) is never empty space.
    This is deliberately scoped to writing; numbered cloze gaps stay required.
    """
    lines = []
    for line in str(text or "").splitlines():
        line = re.sub(r"<u>[\s_]*</u>", "", line)
        # Remove a trailing response rule, optionally wrapped in DOCX bold runs.
        # The word boundary guard keeps e.g. r______ as actual source content.
        line = re.sub(r"(?<![\w])(?:\*\*)?[_\s]*_{2,}[_\s]*(?:\*\*)?\s*$", "", line)
        visible = line.replace("**", "").strip()
        if visible:
            lines.append(line.rstrip())
    return "\n".join(lines)


def _required_stem(text: str) -> str:
    """Exclude writing response space and trailing pagination residue only.

    Interior instructions and supplied essay words remain required, including
    meaningful underlines; this never excuses a genuine stem rewrite.
    """
    lines = strip_writing_answer_space(text).splitlines()
    # Some source books append a repeated exam title plus a compact answer
    # index to the writing response area. Remove only a complete terminal
    # answer-index block, never a task table or an interior exam reference.
    from .extract import _plain
    for index, line in enumerate(lines):
        title = _plain(line).strip()
        if not re.fullmatch(r"\d{0,3}(?:19|20)\d{2}年[\u4e00-\u9fff]{2,12}(?:初中)?学业水平考试", title):
            continue
        tail = [row.strip() for row in lines[index + 1:] if row.strip()]
        if len(tail) < 4 or not re.fullmatch(r"\[TABLE(?:\s+\d+)?\]", tail[0]) or tail[-1] != "[/TABLE]":
            continue
        rows = tail[1:-1]
        headers = [row for row in rows if re.fullmatch(r"题号\s*\|[\s\d|~～—–至-]+", row)]
        answers = [row for row in rows if re.fullmatch(r"答案\s*\|[\sA-H|]+", row)]
        if headers and len(headers) == len(answers) and len(headers) + len(answers) == len(rows):
            lines = lines[:index]
            break
    while lines and (not lines[-1].strip() or FORBIDDEN.search(lines[-1])):
        lines.pop()
    return "\n".join(lines)


# Inline option A may follow whitespace or a question-number separator (e.g. ``39.A.first``).
# Keep the boundary non-word so ordinary prose such as ``DATA.`` is not mistaken for an option.
INLINE_OPTION = re.compile(r"(?<!\w)([A-H])[.．、]\s*\S")


def option_block_start(lines: list[str]) -> tuple[int, int] | None:
    """Locate a labeled option block, including an option that starts inline.

    Some papers put option A at the end of the last dialogue line
    （"Li Lei:You are welcome.　A.Well done!"）and continue B–G on their own
    lines. The material ends at that marker; options run from there.
    """
    from .preview import BARE_OPTION
    for index, line in enumerate(lines):
        stripped = line.strip()
        # OCR/Markdown 会把选项写成 "**A. Let's...**"；识别前先去掉星号，
        # 但返回的 offset 仍指向原始行，供后续按原文切片。
        probe = re.sub(r"\*{1,3}", "", stripped)
        if BARE_OPTION.match(probe):
            m = re.search(r"[A-H]\s*[.．、]", probe)
            offset = stripped.find(m.group()) if m else 0
            return index, len(line) - len(line.lstrip()) + max(offset, 0)
        match = INLINE_OPTION.search(re.sub(r"\*{1,3}", "", line))
        if match and match.group(1) == "A":
            return index, match.start()
    return None


def audit_source_unit(unit: dict, source_lines: list[str] | None = None) -> dict:
    """Check every source child and option block, rather than trusting counts alone.

    Confirmed structure keeps options in ``source_parts`` instead of copying them
    into the formal ``stem``. When the original cleaned lines are available, use
    the unit's proven source span for auditing only; the stored structure shape
    remains unchanged.
    """
    lines = str(unit.get("stem") or "").splitlines()
    span = unit.get("source_span") or {}
    start, end = span.get("start_line"), span.get("end_line")
    if (isinstance(source_lines, list) and type(start) is int and type(end) is int
            and 1 <= start <= end <= len(source_lines)):
        lines = source_lines[start - 1:end]
        if start == end and all(type(span.get(key)) is int for key in ("start_offset", "end_offset")):
            left, right = span["start_offset"], span["end_offset"]
            if not 0 <= left < right <= len(lines[0]):
                return {"passed": False, "issues": [{"code": "SOURCE_OFFSET_INVALID", "message": "同行来源偏移越界"}], "questions": {}, "shared_options": []}
            lines = [lines[0][left:right]]
    numbers = unit.get("numbers") or []
    scoped = unit.get('identity_version') == 'scoped-v1'
    unnumbered = span.get('number_binding') == 'unnumbered_section'
    parts = {n: question_parts(lines, n, scoped=scoped) for n in numbers}
    if unnumbered and len(numbers) == 1:
        parts[numbers[0]] = {'stem': '', 'options': [], 'found': True}
    issues = []
    scoped_shared = []
    if scoped and unit.get('unit_kind') == 'group' and unit.get('type') == '补全对话':
        located = option_block_start(lines)
        if located is not None:
            first_option, offset = located
            scoped_shared = question_parts(['0.' + '\n'.join([lines[first_option][offset:]] + lines[first_option + 1:])], 0)['options']
            for part in parts.values():
                part['options'] = []
    max_options = 0 if scoped_shared else max([int(unit.get("source_expected_options") or 0), *[len(p["options"]) for p in parts.values()]])
    choice_group = any(hint in str(unit.get("type") or "") for hint in ("完形", "语法选择"))
    if len(numbers) == 1 and unit.get("source_span", {}).get("number_binding") == "single_writing_section":
        # A writing section is bounded only by its own section, so its raw stem
        # runs to the section end and can drag in trailing blank lines plus a
        # repeated paper-title / page-footer (e.g. "2022年安徽省初中学业水平考试").
        # That footer is pagination residue, not required question content: a
        # candidate that legitimately trims it must not be judged "题干改写".
        parts[numbers[0]] = {"stem": _required_stem(unit.get("stem") or ""), "options": [], "found": True}
    # 组内小题类型可混排（任务型阅读/阅读综合常见：填空/问答小题本就无选项，
    # 只有个别小题是图片选择题）。此时不能拿组内最大选项数对每个小题一刀切
    # 要求 ABCD，否则合法的无选项小题被误判 SOURCE_OPTIONS_INCOMPLETE，连带让
    # 结构闸门的 NUMBERING_RESTART 无法按分区消解。choice_group（完形/语法选择）
    # 与 standalone 仍是"每题都必须有完整选项"，保持原样。
    mixed_group = unit.get("unit_kind") == "group" and not choice_group
    for n, part in parts.items():
        if not part["found"] and not (len(numbers) == 1 and unit.get("source_span", {}).get("number_binding") == "single_writing_section"):
            issues.append({"code": "QUESTION_NOT_FOUND", "number": n,
                           "message": f"源头单元缺少第 {n} 题的实际题号/空位"})
        labels = [o["label"] for o in part["options"]]
        expected = list("ABCDEFGH"[:max_options])
        # 混排 group 里没有任何选项的小题：本就不该有选项，不判缺选项。
        # 有选项时仍按本组应有选项数校验齐全与内容非空，避免漏掉真实缺项。
        lacks_own_options = mixed_group and not labels
        if lacks_own_options:
            continue
        if (choice_group and not labels) or (max_options and (labels != expected or any(not _canonical(o["text"]) for o in part["options"]))):
            issues.append({"code": "SOURCE_OPTIONS_INCOMPLETE", "number": n,
                           "message": f"第 {n} 题源头选项 {labels}，本组应完整包含 {expected} 且内容非空"})
    from .preview import BARE_OPTION, _strong_number
    shared_options = scoped_shared
    if not shared_options and unit.get("unit_kind") == "group" and not any(_strong_number(line) is not None for line in lines):
        located = option_block_start(lines)
        if located is not None:
            first, offset = located
            shared_options = question_parts(["0." + "\n".join([lines[first][offset:]] + lines[first + 1:])], 0)["options"]
            # 选项文本清除 OCR/Markdown 星号与首尾空白，避免污染正式批次与上传格式。
            for opt in shared_options:
                if isinstance(opt, dict) and isinstance(opt.get("text"), str):
                    opt["text"] = re.sub(r"\*{1,3}", "", opt["text"]).strip()
    return {"passed": not issues, "issues": issues, "questions": parts, "shared_options": shared_options}


def _candidate_options(options) -> list[dict]:
    normalized = []
    for option in options or []:
        if isinstance(option, dict):
            label = str(option.get("label") or option.get("key") or "").strip().rstrip(".．、")
            normalized.append({"label": label, "text": _field_text(option)})
        elif isinstance(option, str):
            text = option.strip()
            match = OPTION.match(text)
            normalized.append({"label": match.group(1) if match else "", "text": text[match.end():] if match else text})
    return normalized


def restore_source_fidelity(batch_dir: str | Path, batch: dict) -> int:
    """把原卷的实词下划线与图片引用补回组装产物，避免模型把 `n____` 或表格图标磨掉。

    只在来源确实有、而候选缺失时补：题干按 `question_parts` 逐字还原（保留
    `<u>…</u>`），图片引用 `[IMG name]` 追加到该单元 material，保证归属到本单元。
    只补结构保真标记，不改答案/标签，也不猜测任何未出现的内容。返回修正计数。
    """
    from . import denoise, structure_gate
    root = Path(batch_dir)
    try:
        source = (root / "01_source/正文.txt").read_text(encoding="utf-8-sig")
        structure = json.loads((root / "01_source/structure.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return 0
    cleaned, _, _ = denoise.clean_body(source)
    lines = cleaned.splitlines()
    fresh = structure_gate._build_units(structure.get("sections") or [], structure.get("groups") or [], lines)
    # Confirmed units carry the AI-checked grouping and Word line spans. Prefer
    # those for assembly, so an OCR-informed grouping is not discarded by a
    # second deterministic reconstruction of the old candidate structure.
    confirmed_path = root / '01_source/structure.confirmed.json'
    try:
        confirmed = json.loads(confirmed_path.read_text(encoding='utf-8-sig'))
        confirmed_units = confirmed.get('units') if isinstance(confirmed, dict) else None
        if isinstance(confirmed_units, list) and confirmed_units:
            fresh = [unit for unit in confirmed_units if isinstance(unit, dict) and unit.get('numbers')]
    except (OSError, ValueError, TypeError):
        pass
    counts = Counter(source_key(u) for u in fresh)
    by_numbers = {source_key(u): u for u in fresh if counts[source_key(u)] == 1}
    changed = 0
    for row in batch.get("items") or []:
        key = source_key(row)
        unit = by_numbers.get(key)
        if not unit:
            continue
        span = unit.get('source_span') or {}
        start, end = span.get('start_line'), span.get('end_line')
        if span.get('basis') == 'ai_classified':
            if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
                continue
            unit_lines = lines[start - 1:end]
        else:
            unit_lines = str(unit.get('stem') or '').splitlines()
        # Child-specific images belong to their own question; only remaining
        # source images are shared material. Never take markers from OCR output.
        children = {str(c.get("number")): c for c in (row.get("subquestions") or []) if isinstance(c, dict)}
        child_images = set()
        for number in unit.get("numbers") or []:
            try:
                part = question_parts(unit_lines, int(number), scoped=unit.get('identity_version') == 'scoped-v1')
            except (TypeError, ValueError):
                continue
            source_images = set(_image_names(str(part.get("stem") or "").splitlines()))
            for option in part.get("options") or []:
                source_images.update(_image_names(str(option.get("text") or "").splitlines()))
            child_images.update(source_images)
            if not source_images:
                continue
            target = children.get(str(number)) if children else row
            if target is None:
                continue
            missing_child = sorted(name for name in source_images if name not in _image_refs(target))
            if missing_child:
                extra = "\n".join(f"[IMG {name}]" for name in missing_child)
                target["stem"] = (_field_text(target.get("stem")).rstrip() + "\n" + extra).strip()
                changed += 1
        shared_images = set(_image_names(unit_lines)) - child_images
        missing_shared = sorted(shared_images - _image_refs(row))
        if missing_shared:
            extra = '\n'.join(f'[IMG {name}]' for name in missing_shared)
            row['material'] = (_field_text(row.get('material')).rstrip() + '\n' + extra).strip()
            changed += 1
    return changed


def apply_required_writing_stem(batch_dir: str | Path, batch: dict) -> int:
    """写作题题干以原卷为准：模型常把给定开头句当成范文正文而截断。

    结构里唯一、单题号、single_writing_section 的写作题，题干直接落回原卷
    `_required_stem` 的逐字内容（去掉作答空行/页脚残留），不猜测、不补写。
    返回被修正的题数。
    """
    from . import denoise, structure_gate
    root = Path(batch_dir)
    try:
        source = (root / "01_source/正文.txt").read_text(encoding="utf-8-sig")
        structure = json.loads((root / "01_source/structure.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return 0
    cleaned, _, _ = denoise.clean_body(source)
    fresh = structure_gate._build_units(structure.get("sections") or [], structure.get("groups") or [], cleaned.splitlines())
    required = {}
    for unit in fresh:
        if (unit.get("source_span", {}).get("number_binding") == "single_writing_section"
                and len(unit.get("numbers") or []) == 1):
            required[source_key(unit)] = _required_stem(unit.get("stem") or "")
    if not required:
        return 0
    changed = 0
    for row in batch.get("items") or []:
        key = source_key(row)
        want = required.get(key)
        if not want:
            continue
        current = _field_text(row.get("stem"))
        if _canonical_image_text(want) not in _canonical_image_text(current + "\n" + _field_text(row.get("material"))):
            row["stem"] = want
            changed += 1
    return changed


_BLANK_NUMBER = re.compile(r"(?:__(\d+)__|(?<!\d)(\d+)\s*[.．]\s*_+|[\u3000\s](\d+)[\u3000\s]*_+)")


def _material_blank_numbers(material) -> set:
    """材料里作为「空位题号」出现的数字集合（`__N__`、`N.______`、`　N　____`）。"""
    found = set()
    for match in _BLANK_NUMBER.finditer(str(material or "")):
        for group in match.groups():
            if group:
                try:
                    found.add(int(group))
                except ValueError:
                    pass
                break
    return found


def restore_source_stems(batch_dir: str | Path, batch: dict) -> int:
    """按原卷解析结果回填题干与选项，避免模型把相邻题内容复制错。

    只回填原卷确实解析到的题干/选项；不改材料、答案、解析和标签。
    """
    root = Path(batch_dir)
    try:
        confirmed = json.loads((root / "01_source/structure.confirmed.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return 0
    by_numbers = {source_key(u): u for u in confirmed.get("units") or [] if isinstance(u, dict)}
    changed = 0

    def lookup(parsed, number):
        for key in (number, str(number)):
            if key in parsed:
                return parsed[key]
        return None

    def fill(target, parsed):
        nonlocal changed
        if not isinstance(parsed, dict) or not parsed.get("found"):
            return
        stem = parsed.get("stem")
        if isinstance(stem, str) and stem.strip() and target.get("stem") != stem:
            target["stem"] = stem
            changed += 1
        options = parsed.get("options")
        if options:
            filled = [{"key": option.get("label"), "text": option.get("text")} for option in options]
            if target.get("options") != filled:
                target["options"] = filled
                changed += 1

    for row in batch.get("items") or []:
        key = source_key(row)
        unit = by_numbers.get(key)
        if not unit:
            continue
        parsed = audit_source_unit(unit).get("questions") or {}
        # The audit parser strips presentation marks; using its stem as the
        # restored payload can erase a leading whitespace underline. Recover
        # Word markup with the same parser used by preassembly, then serialize
        # actual blanks without changing underlined words.
        from .question_draft import _parts
        from .wire_format import normalize_blank_text
        for number in unit.get('numbers') or []:
            original = _parts(str(unit.get('stem') or '').splitlines(), int(number),
                              scoped=unit.get('identity_version') == 'scoped-v1')
            entry = lookup(parsed, number)
            if isinstance(entry, dict) and original.get('found'):
                if original.get('stem'):
                    entry['stem'] = normalize_blank_text(original['stem'])
                if original.get('options'):
                    entry['options'] = [{'label': opt['key'],
                                         'text': normalize_blank_text(opt['text'])}
                                        for opt in original['options']]
        if unit.get("unit_kind") == "standalone":
            fill(row, lookup(parsed, unit["numbers"][0]))
        else:
            # 共享选项/对话类组题（补全对话等）材料里已带齐每个空位题号时，
            # 子题题干只能是空壳 `______`（D3/D25）：解析出的「后一行对白」
            # 属于 material，不能抄进子题题干，否则与材料重复、且会被 D7 拦。
            shared_blank_numbers = _material_blank_numbers(row.get("material") or "")
            row_numbers = {int(n) for n in unit.get("numbers") or [] if str(n).isdigit()}
            shell_group = bool(row_numbers) and row_numbers.issubset(shared_blank_numbers)
            for child in row.get("subquestions") or []:
                if shell_group:
                    entry = child
                    if entry.get("stem") != "______":
                        entry["stem"] = "______"
                        changed += 1
                    continue
                fill(child, lookup(parsed, child.get("number")))
    return changed


def restore_table_variant(batch: dict) -> int:
    """信息转换表格缺 variant 时按题型确定性补 information-conversion。"""
    changed = 0
    for row in batch.get("items") or []:
        qtype = str(row.get("question_type") or row.get("type") or "")
        if "信息转换" not in qtype:
            continue
        for block in (row.get("materialBlocks") or row.get("material_blocks") or []):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "table" and not block.get("variant"):
                block["variant"] = "information-conversion"
                changed += 1
            for table_row in block.get("rows") or []:
                cells = table_row.get("cells") if isinstance(table_row, dict) else table_row
                for cell in cells or []:
                    if not isinstance(cell, dict) or cell.get("text") or not isinstance(cell.get("items"), list):
                        continue
                    # 服务端表格单元格只认 text；把 items 要点合并成文本，保留空档标记。
                    cell["text"] = "\n".join(str(item) for item in cell["items"])
                    cell.pop("items", None)
                    changed += 1
    return changed


def _validated_ai_units(units, fresh, lines):
    """Accept AI grouping only when its complete Word-source bindings are provable."""
    from .extract import guide_numbers
    from .preview import _find, _strong_number, _weak_numbers

    if not isinstance(units, list) or not units:
        return None
    expected = [n for unit in fresh for n in unit.get("numbers", [])]
    if not expected or any(type(n) is not int or n < 1 for n in expected):
        return None
    from .question_identity import VERSION, validate_units, assign_scoped
    scoped = any(u.get("identity_version") == VERSION for u in fresh)
    if scoped:
        if not validate_units(units) or not validate_units(fresh):
            return None
    elif len(set(expected)) != len(expected):
        return None
    expected_set = set(expected)
    seen, occupied, result = set(), set(), []
    for unit in units:
        if not isinstance(unit, dict):
            return None
        nums, span, stem = unit.get("numbers"), unit.get("source_span"), unit.get("stem")
        if (not isinstance(nums, list) or not nums or
                any(type(n) is not int or n < 1 for n in nums) or
                len(set(nums)) != len(nums) or (not scoped and seen.intersection(nums)) or
                unit.get("unit_kind") not in ("standalone", "group") or
                (unit.get("unit_kind") == "standalone" and len(nums) != 1) or
                not isinstance(span, dict) or span.get("basis") != "ai_classified" or
                not isinstance(stem, str) or not stem.strip()):
            return None
        start, end = span.get("start_line"), span.get("end_line")
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
            return None
        positions = set(range(start, end + 1))
        chunk = lines[start - 1:end]
        if occupied.intersection(positions) or stem not in "\n".join(chunk):
            return None
        # A number must be evidenced in both the declared span and the retained
        # stem, not merely invented by the classifier or borrowed from elsewhere.
        stem_lines = stem.splitlines()
        from .question_identity import question_position, printed_anchors
        unnumbered = span.get('number_binding') in {'single_writing_section', 'unnumbered_section'}
        if scoped:
            if not unnumbered and any(question_position(stem_lines, n) is None for n in nums):
                return None
        elif any(_find(stem_lines, n) is None and _find(stem_lines, n, strong=False) is None for n in nums):
            return None
        visible = set(n for _, n in printed_anchors(chunk)) if scoped else set()
        guides = []
        for line in chunk:
            number = _strong_number(line)
            if number is not None:
                visible.add(number)
            visible.update(_weak_numbers(line))
            guide = set(guide_numbers(line)) & expected_set
            if guide:
                guides.append(guide)
        if (visible & expected_set) - set(nums):
            return None
        # Explicit, independent source guides are stronger than an AI merge.
        if any(guide - set(nums) for guide in guides) or len({tuple(sorted(g)) for g in guides}) > 1:
            return None
        seen.update(nums)
        occupied.update(positions)
        # Audit the whole bound source span so a shortened but exact substring
        # cannot hide material, options, images or instructions from the audit.
        result.append({**unit, "stem": "\n".join(chunk).strip()})
    if scoped:
        def anchors(rows):
            positions = []
            for row in rows:
                span = row.get('source_span') or {}
                start, end = span.get('start_line'), span.get('end_line')
                if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
                    return None
                local = lines[start - 1:end]
                for n in row.get('numbers') or []:
                    pos = question_position(local, n)
                    if pos is None and span.get('number_binding') in {'single_writing_section', 'unnumbered_section'}:
                        positions.append((n, start, 'unnumbered'))
                        continue
                    if pos is None:
                        return None
                    positions.append((n, start + pos))
            return Counter(positions)
        # Compare actual printed occurrences, not the set of numeric labels.
        # Dropping a second “1” or moving it into another section is a failure.
        actual, wanted = anchors(result), anchors(fresh)
        if actual is None or wanted is None or actual != wanted:
            return None
        return result
    return result if seen == expected_set else None


def restore_table_blocks_from_structure(batch_dir: str | Path, batch: dict) -> int:
    """Restore empty table blocks from the confirmed Word structure.

    The model may preserve only the table marker while omitting its cells. The
    confirmed Word slice is authoritative, so this is a deterministic repair,
    not generated content or an audit waiver.
    """
    root = Path(batch_dir)
    confirmed = json.loads((root / "01_source/structure.confirmed.json").read_text(encoding="utf-8-sig"))
    by_numbers = {source_key(u): u for u in confirmed.get("units") or []}
    repaired = 0
    for item in batch.get("items") or []:
        blocks = item.get("materialBlocks") or item.get("material_blocks")
        if not isinstance(blocks, list) or not any(isinstance(b, dict) and b.get("type") == "table" and not b.get("rows") for b in blocks):
            continue
        unit = by_numbers.get(source_key(item))
        if not unit:
            continue
        stem = str(unit.get("stem") or "")
        match = re.search(r"\[TABLE\s+\d+\](.*?)\[/TABLE\]", stem, re.S)
        if not match:
            continue
        rows = []
        for raw in match.group(1).splitlines():
            line = raw.strip()
            if not line or "|" not in line:
                continue
            cells = []
            for cell in line.split("|"):
                text = re.sub(r"<br\s*/?>", "\n", cell, flags=re.I)
                text = re.sub(r"[ \t]+", " ", text).strip()
                cells.append({"text": text, "items": []})
            if cells:
                rows.append({"cells": cells})
        if rows:
            for block in blocks:
                if isinstance(block, dict) and block.get("type") == "table" and not block.get("rows"):
                    block["rows"] = rows
                    repaired += 1
                    break
    return repaired


def audit_batch_source(batch_dir: str | Path, batch: dict) -> dict:
    """Read-only final artifact audit against freshly sliced source units.

    Shared material/instructions are checked at group level; child stems and
    every labeled option are checked at the corresponding actual number.
    This is deterministic text evidence, never a claim of visual review.
    """
    from . import denoise, structure_gate
    from .extract import _plain, material_label
    from .preview import _strong_number, BARE_OPTION, SECTION
    root = Path(batch_dir)
    source = (root / "01_source/正文.txt").read_text(encoding="utf-8-sig")
    structure = json.loads((root / "01_source/structure.json").read_text(encoding="utf-8-sig"))
    confirmed = json.loads((root / "01_source/structure.confirmed.json").read_text(encoding="utf-8-sig"))
    cleaned, _, _ = denoise.clean_body(source)
    fresh = structure_gate._build_units(structure.get("sections") or [], structure.get("groups") or [], cleaned.splitlines())
    by_numbers = {source_key(u): u for u in fresh}
    items = batch.get("items") or []
    issues, checked = [], []

    def issue(code, message, uid="", number=None):
        entry = {"code": code, "severity": "blocker", "unit_id": uid, "message": message}
        if number is not None:
            entry["number"] = number
        issues.append(entry)

    expected_units = confirmed.get("units") or []
    expected_sets = [source_key(u) for u in expected_units]
    actual_sets = [source_key(row) for row in items]
    if Counter(expected_sets) != Counter(actual_sets):
        issue("BATCH_NUMBER_COVERAGE", "正式批次单元题号及分组与确认结构不一致")
    # AI grouping is usable only after rechecking its bindings against the
    # current cleaned Word source; a failed AI claim never falls back silently.
    ai_claimed = any(isinstance(u, dict) and isinstance(u.get("source_span"), dict)
                     and u["source_span"].get("basis") == "ai_classified" for u in expected_units)
    if ai_claimed:
        validated = _validated_ai_units(expected_units, fresh, cleaned.splitlines())
        if validated is None:
            issue("SOURCE_GROUP_BOUNDARY_MISMATCH", "AI确认结构的原卷行号、题号或分组证据无效；须重跑结构闸门")
        else:
            by_numbers = {source_key(u): u for u in validated}
    elif Counter(expected_sets) != Counter(list(by_numbers)):
        # A merged legacy group cannot conceal independently guided source groups.
        issue("SOURCE_GROUP_BOUNDARY_MISMATCH", "确认结构与原卷材料/分段导语的实际分组不一致；须重跑结构闸门")
    materials = {}
    for key, unit in by_numbers.items():
        material = []
        located = option_block_start(unit["stem"].splitlines())
        for index, line in enumerate(unit["stem"].splitlines()):
            if SECTION.match(_plain(line)):
                continue
            if _strong_number(line) is not None:
                break
            if located is not None and index == located[0]:
                prefix = line[:located[1]].rstrip()
                if prefix and not material_label(prefix):
                    material.append(prefix)
                break
            if not material_label(line):
                material.append(line)
        materials[key] = material
    for item, key in zip(items, actual_sets):
        uid = item.get("unit_id") or item.get("id") or ""
        unit = by_numbers.get(key)
        if unit is None:
            issue("SOURCE_UNIT_NOT_FOUND", f"源头找不到正式单元题号 {list(key)} 的独立边界", uid)
            continue
        if (item.get('numbers') or [item.get('number')]) != unit.get('numbers'):
            issue('BATCH_NUMBER_COVERAGE', '单元身份对应的原始小题号不一致', uid)
        if unit.get('identity_version') == 'scoped-v1':
            from .question_identity import fields, question_key
            if any(item.get(k) != v for k, v in fields(unit).items()):
                issue('BATCH_IDENTITY_MISMATCH', '正式题目与确认结构的大题/题目身份不一致', uid)
            targets = item.get('subquestions') or [] if unit['unit_kind'] == 'group' else [item]
            if any(child.get('question_id') != question_key(unit, child.get('number')) for child in targets
                   if child.get('number') in unit['numbers']):
                issue('BATCH_IDENTITY_MISMATCH', '子题身份与所属单元不一致', uid)
        checked.append(uid)
        candidate_body = "\n".join(_field_text(item.get(k)) for k in
                                   ("stem", "material", "materialBlocks", "material_blocks", "options", "subquestions"))
        def has_table(value):
            if isinstance(value, dict):
                return value.get("type") == "table" or any(has_table(v) for v in value.values())
            return isinstance(value, list) and any(has_table(v) for v in value)
        required_source = (_required_stem(unit["stem"]) if unit.get("source_span", {}).get("number_binding") == "single_writing_section"
                           else unit["stem"])
        if "[TABLE" in required_source and not has_table(item.get("materialBlocks") or item.get("material_blocks")):
            issue("TABLE_STRUCTURE_MISSING", "原卷含表格，正式单元缺少保留网格结构的 materialBlocks 表格", uid)
        for marked in re.findall(r"<u>(.*?)</u>", unit["stem"], flags=re.S):
            # 空档（<u>n　　　　</u>、<u>　26　</u>）会被空档规范化成 n______/__26__，
            # 下划线本就不该保留；只校验真正的实词下划线（≥2 个字母/汉字，如 trekked）。
            if len(re.findall(r"[A-Za-z\u4e00-\u9fff]", marked)) >= 2:
                preserved = [_canonical(x) for x in re.findall(r"<u>(.*?)</u>", candidate_body, flags=re.S)]
                if _canonical(marked) not in preserved:
                    issue("SOURCE_UNDERLINE_MISSING", "原卷实词下划线未保留：" + marked[:80], uid)
        # Keep final batch material semantics based on the confirmed unit stem;
        # structure-gate performs the separate raw-span option audit.
        source_audit = audit_source_unit(unit)

        def check_images(required, present, number=None, label=""):
            for name in sorted(set(_image_names(str(required).splitlines())) - present):
                scope = f"第 {number} 题" if number is not None else "本单元共享材料"
                if label:
                    scope += f" {label} 选项"
                issue("SOURCE_IMAGE_MISSING", f"原卷图片引用缺失或未映射到{scope}：{name}", uid, number)

        def check_option_images(expected, owner, number=None):
            options = owner.get("options") or []
            actual = {o["label"]: raw for o, raw in zip(_candidate_options(options), options)}
            for option in expected:
                label = option["label"]
                check_images(option["text"], _option_image_refs(actual.get(label), owner, label), number, label)

        # Partition source refs before looking at candidates: each child/option
        # must prove its own mapping rather than borrowing another child's image.
        owned = set()
        for part in source_audit["questions"].values():
            owned.update(_image_names(part["stem"].splitlines()))
            for option in part["options"]:
                owned.update(_image_names(option["text"].splitlines()))
        for option in source_audit["shared_options"]:
            owned.update(_image_names(option["text"].splitlines()))
        shared_refs = (set(_image_names(materials[key]))
                       | (set(_image_names(unit["stem"].splitlines())) - owned))
        check_images("\n".join(f"[IMG {name}]" for name in shared_refs), _image_refs(item))
        for problem in source_audit["issues"]:
            issue(problem["code"], problem["message"], uid, problem.get("number"))
        shared_options = source_audit["shared_options"]
        if shared_options:
            actual_shared = _candidate_options(item.get("options"))
            check_option_images(shared_options, item)
            if [(o["label"], _canonical_image_text(o["text"])) for o in shared_options] != [(o["label"], _canonical_image_text(o["text"])) for o in actual_shared]:
                issue("SHARED_OPTIONS_MISMATCH", "共享选项标签、数量或内容与原卷不一致，必须归于当前题组", uid)
        candidate_material = "\n".join(
            _field_text(item.get(k)) for k in ("material", "materialBlocks", "material_blocks", "stem"))
        # 补全对话/信息匹配的 A–G 选项在正式批次里归入 options，
        # 但原卷里它们常与材料同段；审计必须把 options 也视为“已呈现”。
        candidate_material += "\n" + "\n".join(
            _canonical_image_text(f"{option['label']}. {option['text']}" if option['label'] else option['text'])
            for option in _candidate_options(item.get("options")))
        # 表格材料在正式批次里是 materialBlocks 的单元格；按原卷的“|”列分隔还原，
        # 否则整行比对会因为单元格被拆开而误报缺失。
        for block in (item.get("materialBlocks") or item.get("material_blocks") or []):
            for row in (block.get("rows") or []) if isinstance(block, dict) else []:
                cells = row.get("cells") if isinstance(row, dict) else row
                if not isinstance(cells, list):
                    continue
                texts = []
                for cell in cells:
                    if not isinstance(cell, dict):
                        continue
                    text = cell.get("text")
                    if not text and isinstance(cell.get("items"), list):
                        text = " ".join(str(item) for item in cell["items"])
                    if text:
                        texts.append(str(text))
                if texts:
                    candidate_material += "\n" + " | ".join(texts)
        if unit["unit_kind"] == "group":
            candidate_text = _canonical_image_text(candidate_material)
            from .question_draft import _bound_source, _rubric_lines
            from .shared_rubric import section_rubric, phonetic_section_rubric
            confirmed_unit = next((u for u in expected_units if source_key(u) == key), unit)
            bound, proof = _bound_source(confirmed_unit, source, cleaned, fresh)
            rubric_lines = source.splitlines() if proof.get("line_basis") == "01_source/正文.txt" else cleaned.splitlines()
            shared_rubric = (_rubric_lines(bound.splitlines(), unit.get("type") or "")
                             or (phonetic_section_rubric(rubric_lines, confirmed_unit) if bound else [])
                             or section_rubric(rubric_lines, proof.get("start_line"), unit.get("type") or ""))
            for required in shared_rubric:
                if _canonical_image_text(required) not in candidate_text:
                    issue("SOURCE_SHARED_RUBRIC_MISSING", "同节共享作答导语缺失：" + required[:100], uid)
            for line in materials[key]:
                # 表格行常把多个单元格/要点用 | 或 <br> 写在一行；逐段比对，
                # 不能要求整行作为一个连续字符串出现。
                parts = [part for part in re.split(r"\s*\|\s*|<br\s*/?>", line)
                         if _canonical_image_text(part)]
                if len(parts) > 1:
                    missing = [part for part in parts if _canonical_image_text(part) not in candidate_text]
                    if missing:
                        issue("SOURCE_MATERIAL_MISSING",
                              "共享材料/分段作答指令缺失或改写：" + "；".join(p[:60] for p in missing[:3]), uid)
                    continue
                text = _canonical_image_text(line)
                if text and text not in candidate_text:
                    issue("SOURCE_MATERIAL_MISSING", "共享材料/分段作答指令缺失或改写：" + line[:100], uid)
            for other, lines in materials.items():
                if other == key:
                    continue
                for line in lines:
                    if any(_canonical(line) == _canonical(r) for r in shared_rubric):
                        continue
                    # A section rubric may be repeated on every article in that
                    # section. Keep requiring it on its original unit above,
                    # but do not treat that exact rubric as exclusive prose.
                    if (by_numbers[other].get("type") == unit.get("type")
                            and _generic_choice_instruction(line, unit.get("type", ""))):
                        continue
                    # 通用选择题作答导语（阅读下列短文…选出最佳选项）在不同大题里逐字相同，
                    # 不构成"混入另一题组正文"；只要本单元自己也要这条导语即可，跨题型也豁免。
                    if _generic_choice_instruction(line, "阅读理解"):
                        continue
                    text = _canonical(line)
                    if len(text) >= 20 and text not in _canonical(unit["stem"]) and text in _canonical(candidate_material):
                        issue("FOREIGN_GROUP_MATERIAL", f"材料混入另一题组 {list(other)} 的正文：{line[:100]}", uid)
        children = item.get("subquestions") or [] if unit["unit_kind"] == "group" else [item]
        child_numbers = [c.get("number") for c in children]
        if Counter(map(str, child_numbers)) != Counter(map(str, unit['numbers'])):
            issue("CHILD_NUMBER_COVERAGE", "组内子题实际题号缺失、重复或归属错误", uid)
        by_child = {str(c.get("number")): c for c in children}
        for n, part in source_audit["questions"].items():
            child = by_child.get(str(n), {})
            # 同一道题可把题干与表格分别放在 stem/materialBlocks，不能要求
            # 原卷整段都挤进 stem；仅在本题自己的内容字段查找，不借答案/解析补缺。
            child_content = "\n".join(_field_text(child.get(k)) for k in
                                      ("stem", "material", "materialBlocks", "material_blocks"))
            check_images(part["stem"], _image_refs(child), n)
            check_option_images(part["options"], child, n)
            stem_present = _canonical_image_text(child_content)
            if shared_options and unit.get('identity_version') == 'scoped-v1' and unit.get('type') == '补全对话':
                stem_present += _canonical_image_text(candidate_material)
            if part["stem"] and _canonical_image_text(part["stem"]) not in stem_present:
                issue("SOURCE_STEM_MISSING", f"第 {n} 题题干缺失或改写", uid, n)
            options = child.get("options") or []
            normalized = _candidate_options(options)
            expected_options = part["options"]
            if expected_options and [o["label"] for o in expected_options] != [o["label"] for o in normalized]:
                issue("OPTIONS_MISMATCH", f"第 {n} 题选项标签/数量与原卷不一致", uid, n)
            else:
                for expected, actual in zip(expected_options, normalized):
                    if _canonical_image_text(expected["text"]) != _canonical_image_text(actual["text"]):
                        issue("OPTION_CONTENT_MISMATCH", f"第 {n} 题 {expected['label']} 选项内容缺失或改写", uid, n)
    return {"passed": not issues, "issues": issues, "checked_units": checked,
            "visual_review_claimed": False}
