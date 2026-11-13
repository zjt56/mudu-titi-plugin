"""只读核对上传解析/大意的来源；检查归属而不改写原文或伪造语义审核。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import unicodedata

from .agent_tasks import _safe_path
from .content_labels import IMG, EXPLANATION_LABEL, SUMMARY_LABEL

SOURCES = ('01_source/附录.txt', '01_source_ans/正文.txt', '01_source/正文.txt')
AI_MARK = '（AI生成，待校对）'
# Source-title patterns are shared with deterministic extraction and source_ref.
QUESTION_PREFIX = r'^\s*(?:[（(]\s*[）)]\s*)?(\d{1,3})[.．、]'
QUESTION = re.compile(rf'{QUESTION_PREFIX}.*?{EXPLANATION_LABEL}')
QUESTION_BOUNDARY = re.compile(QUESTION_PREFIX)
BODY_QUESTION = QUESTION_BOUNDARY
# An explanation paragraph printed without its own question number; it binds to
# the preceding numbered answer line (see the stray-explanation pass below).
STRAY_EXPLANATION = re.compile(rf'^\s*{EXPLANATION_LABEL}')
SUMMARY = re.compile(rf'{SUMMARY_LABEL}\s*(.*)')
# 附录在逐题解析后常跟独立附注小标题（如【熟词生义】【点拨】【思路】【拓展】【考点】）
# 及其说明行；这些不属于解析正文，解析提取必须在此处截断，否则会把附注吞进
# 解析文本，令模型逐字解析无法与原文解析行全等绑定。
ANNOTATION = re.compile(r'^\s*(?:\*\*\s*)?#*\s*【\s*(?:熟词生义|点拨|思路点拨|拓展|引申|考点|词义辨析|语法点|注意|易错点|重难点|词汇积累|难句分析)\s*】')
ICON_HEADING = re.compile(rf'^\s*{IMG}[^\n]*?{IMG}')
SECTION_NUMBER = re.compile(r'^\s*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫIVX]+|[一二三四五六七八九十]+)[.．、]')
SECTION_NAME = re.compile(r'^\s*(短对话理解|长对话理解|短文理解|信息转换|单项填空|完形填空|补全对话|阅读理解|任务型阅读|单词拼写|书面表达)(?=\s|$|[（(：:])')
# A bare letter, a Chinese passage heading, or an explicit “Passage B”
# marks a boundary; an English sentence beginning with the article “A” does not.
PASSAGE_HEADING = re.compile(
    r'^\s*(?:[（(][A-H][）)]|第[一二三四五六七八九十\d]+篇|[A-H](?:$|篇|[.．、:：]\s*$|[.．、:：]\s*第[一二三四五六七八九十\d]+篇|\s+[\u3400-\u9fff])|(?i:Passage)\s+[A-H](?=$|\s|[.．、:：]\s*$))')
# 「第三部分 阅读理解」「第四部分 写」这类分部标题，以及「阅读下面短文…」这种
# 题干说明，都是材料/大意的结束边界；否则末尾【文章大意】会把下面的说明和分部标题吞进去。
PART_HEADING = re.compile(r'^\s*第[一二三四五六七八九十\d]+部分')
PASSAGE_INSTRUCTION = re.compile(
    r'^\s*(?:阅读下列短文|阅读下面短文|读下面短文|阅读短文|阅读下列材料|阅读下面材料|请阅读)')


def _inline_numbers(text):
    text = re.sub(IMG, ' ', text)
    return re.finditer(r'(?<!\d)\d{1,3}[.．、](?!\d)', text)


def _answer_range_boundary(line):
    # A compact answer range ends the preceding explanation; it is never a
    # single-question owner. Share the existing answer-source grammar.
    from .optional_inputs import ANSWER_RANGE
    return ANSWER_RANGE.fullmatch(line) is not None


def _question(line):
    match = QUESTION.match(line)
    if not match:
        return None
    current = int(match.group(1))
    rest = line[match.end(1):]
    # A second *numbered question* on this line (e.g. "1.A ... 2.B ...") makes the
    # label ambiguous. Sentence numbers inside the explanation ("at age 5.",
    # "page 3.") are always smaller than the current question number, so they are
    # ignored; otherwise the row is dropped and its passage summary loses owner.
    for extra in _inline_numbers(rest):
        number = int(re.match(r'\d+', extra.group(0)).group())
        if number <= current:
            continue
        following = rest[extra.end():]
        if re.match(r'\s*(?:[A-Da-d](?=\s|　|[.．、]|$|【)|【\s*(?:解析|详解|分析)\s*】)', following):
            return None
    return match


def _section(line):
    number = SECTION_NUMBER.match(line)
    rest = line[number.end():] if number else line
    name = SECTION_NAME.match(rest)
    if not number and not name:
        return None
    return (_norm(number.group()) if number else None, name.group(1) if name else None)


def _same_section(left, right):
    if left is None or right is None:
        return left is right
    comparable = [(a, b) for a, b in zip(left, right) if a is not None and b is not None]
    return bool(comparable) and all(a == b for a, b in comparable)


def reference_only_sources(batch_dir):
    """Sources that are evidence copies, not content authority.

    material-roles.json marks the uploaded PDF/OCR copy as content_authority=false
    when a Word original is authoritative. Such a copy must never compete with the
    authoritative text for "unique binding"; it may only corroborate.
    """
    path = Path(batch_dir) / '01_source' / 'material-roles.json'
    if not path.is_file():
        return frozenset()
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError, UnicodeError):
        return frozenset()
    result = set()
    for doc in (data.get('documents') or []) if isinstance(data, dict) else []:
        if isinstance(doc, dict) and doc.get('content_authority') is False:
            text_path = doc.get('text_path')
            if isinstance(text_path, str) and text_path:
                result.add(text_path)
    return frozenset(result)


def _source_line(value, *, anchor_view=False):
    """Strip styles; only the body anchor view may normalize numbered blanks.

    An underlined number in an answer appendix is an ANSWER, not a question
    slot. Never normalize answer-column whitespace or rewrite answer values.
    """
    if anchor_view:
        from .wire_format import normalize_blank_text
        value = re.sub(r'<u>\s*(?:\d{1,3}\s*)?</u>',
                       lambda match: normalize_blank_text(match.group()), value)
    return re.sub(r'\*+|<[^>]+>', '', value).strip()


def _printed_item_anchors(lines):
    """Printed per-item anchors for a bounded section.

    Unified with ``structure_gate._scope_anchors`` so a cloze/short-answer
    material line whose inline ``<u>　n　</u>`` blank markers restate the same
    numbers as the ``( )n.`` option lines below is not double-counted as two
    printed anchors. Literal duplicates on one line remain duplicates.
    """
    from .structure_gate import _scope_anchors
    return sorted(_scope_anchors(lines), key=lambda row: row[0])


def _strip_answer_marker(value):
    """Drop a leading answer letter / 【解析】 marker from an appendix line."""
    text = re.sub(r"^\s*(?:[A-H]\s*[.、)）]?\s*)?", "", value or "")
    text = re.sub(r"^\s*【\s*(?:解析|答案|分析|详解|考点|点拨)\s*】\s*[:：]?\s*", "", text)
    return text or value


_QUOTE_MAP = str.maketrans({"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'",
                            "\u300c": '"', "\u300d": '"', "\u300e": '"', "\u300f": '"'})


def _verbatim(value):
    """只忽略排版标记、Markdown 星号、空白，以及引号的弯/直弯样式；
    实词、全半角逗号句号等标点严格保留——标点或措辞差异仍判为不同。"""
    value = re.sub(r'\[IMG\s+[^\]\r\n]+\]', '', value, flags=re.I)
    value = re.sub(r'</?(?:u|b|strong|em|i|sup|sub)(?:\s[^>]*)?>', '', value, flags=re.I)
    value = re.sub(r'\*{1,3}', '', value)
    value = value.translate(_QUOTE_MAP)
    return re.sub(r'\s+', '', value)


def _norm(value):
    value = re.sub(r'</?(?:u|b|strong|em|i|sup|sub)(?:\s[^>]*)?>', '', value, flags=re.I)
    value = re.sub(r'\*{1,3}', '', value)
    value = unicodedata.normalize('NFKC', value).translate(_QUOTE_MAP)
    return re.sub(r'\s+', '', value)


def _minor_edit(source, value, limit=2):
    """source 与 value 归一化后是否只差 ≤limit 处单字符增/删（无替换不同字）。

    用于识破"漏抄/多抄一个实词字"的抄写笔误；语义改写（换词、换句）不满足，
    仍会判为不一致。基于最长公共子序列长度判定，避免把错字当对。
    """
    a, b = _norm(source or ''), _norm(value or '')
    if not a or not b:
        return False
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > limit:
        return False
    # LCS 长度；a/b 长度接近，O(la*lb) 可接受（单题摘要）。
    import difflib
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    matched = sum(block.size for block in sm.get_matching_blocks())
    edits = (la - matched) + (lb - matched)
    return edits <= limit


def scoped_source_matches(unit, number, path, line, source_lines, *, equivalent_answers=False):
    """Prove a printed number belongs to this unit at actual source coordinates.

    A title/type alone is never evidence. Companion excerpts need the complete
    section occurrence/context identity, independently found in both sources.
    """
    from .question_identity import VERSION
    from .sync_practice import heading_candidates
    if unit.get('identity_version') != VERSION:
        return True
    if number not in (unit.get('numbers') or []):
        return False
    body_path = '01_source/正文.txt'
    body = source_lines.get(body_path, [])
    lines = source_lines.get(path, [])
    span, section = unit.get('source_span') or {}, unit.get('source_section') or {}
    start, end = span.get('start_line'), span.get('end_line')
    if (type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(body)
            or type(line) is not int or not 1 <= line <= len(lines)):
        return False
    def signature(row):
        return (_norm(str(row.get('title') or '')), _norm(str(row.get('context') or '')),
                row.get('occurrence'))
    body_sections = heading_candidates('\n'.join(body))
    owners = [row for row in body_sections
              if row['start_line'] == section.get('start_line')
              and row['end_line'] == section.get('end_line')
              and signature(row) == signature(section)]
    if len(owners) != 1 or not owners[0]['start_line'] <= start <= end <= owners[0]['end_line']:
        return False
    local_anchors = _printed_item_anchors(body[start - 1:end])
    body_hits = [start + index for index, n in local_anchors if n == int(number)]
    if not body_hits:
        # An explicit single-task alias is allowed only for a wholly unnumbered
        # section. It cannot convert an arbitrary missing number into a match.
        if (span.get('number_binding') not in {'unnumbered_section', 'single_writing_section'}
                or number != 1 or unit.get('numbers') != [1] or local_anchors
                or _printed_item_anchors(body[owners[0]['start_line'] - 1:owners[0]['end_line']])
                or any(row.strip() for row in (body[owners[0]['start_line']:start - 1]
                                               + body[end:owners[0]['end_line']]))):
            return False
    if path == body_path:
        left, right = start, end
    else:
        # The companion's single printed 1 cannot choose between A:1 and B:1
        # in the body. Section metadata alone does not prove passage identity.
        section_anchors = _printed_item_anchors(body[owners[0]['start_line'] - 1:owners[0]['end_line']])
        if body_hits and sum(n == int(number) for _, n in section_anchors) != 1:
            return False
        matches = [row for row in heading_candidates('\n'.join(lines))
                   if signature(row) == signature(section)]
        if len(matches) != 1:
            return False
        left, right = matches[0]['start_line'], matches[0]['end_line']
    if not left <= line <= right:
        return False
    anchors = [left + index for index, n in _printed_item_anchors(lines[left - 1:right]) if n == int(number)]
    if not body_hits and path == body_path:
        return not anchors  # Explicit singleton alias; no printed number is asserted.
    if len(anchors) == 1:
        return True
    if not equivalent_answers or path == body_path or not anchors or line not in anchors:
        return False
    # Repeated positions are equivalent only inside this already proven section,
    # with one verbatim printed row per position. Never merge across scopes or
    # accept two occurrences on one physical line.
    if len(anchors) != len(set(anchors)):
        return False
    return len({_verbatim(lines[at - 1]) for at in anchors}) == 1


# Compact lesson keys retain the printed Roman section, but omit its instruction.
# Keep this grammar answer-only: it must not weaken explanation/source_ref ownership.
_ROMAN_ANSWER = re.compile(r'^\s*([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫIVX]+)[.．、]\s*')
# A printed lesson band banner. 基础巩固/能力提升 are the canonical pair; the
# sync-practice lesson books also print 重要考点集训 (foundation/考点 block) and
# 创新阅读专练 (reading extension block) as the same kind of band container.
_LESSON_BAND = re.compile(rf'^\s*(?:{IMG}\s*)*(?:[ABＡＢ]\s*[.．、:：]?\s*)?(基础巩固|能力提升|重要考点集训|创新阅读专练)\s*(?:{IMG}\s*)*$')


def _answer_band(text):
    """An explicit printed answer container; images alone prove no label."""
    text = _source_line(text)
    from .lesson_bands import canonical_label
    label = canonical_label(text)
    if label:
        return {'基础巩固': 'foundation', '能力提升': 'extension'}[label]
    marker = _LESSON_BAND.fullmatch(text)
    if marker:
        return {'重要考点集训': 'foundation', '创新阅读专练': 'extension'}.get(marker[1], 'invalid')
    if re.fullmatch(r'\s*Pronunciation\s*', text, re.I):
        return 'pronunciation'
    return ''


def _answer_section_bands(sections, lines):
    """Read explicit source banners at real coordinates without guessing images."""
    from .sync_practice import _LESSON, _UNIT, is_section_title, plain_text
    result, active, cursor = [], '', 0
    for section in sections:
        while cursor < section['start_line']:
            text = lines[cursor]
            visible = plain_text(text).strip()
            if _LESSON.match(visible) or _UNIT.match(visible) or is_section_title(visible):
                active = ''  # A lesson/topic boundary cannot inherit an earlier band.
            active = _answer_band(text) or active
            cursor += 1
        result.append(active)
    return result

# Bands that introduce the extension (trailing) block of an otherwise-Roman key.
_EXTENSION_BANDS = ('能力提升', '创新阅读专练')
_LETTER_SEQUENCE = re.compile(r'[A-Za-z](?:\s*[;；]\s*[A-Za-z])+\s*')
# A printed whole-task word list (e.g. one,two,...,ten), not free-form prose.
# Ownership is proved later by the complete lesson skeleton and source span.
_WORD_SEQUENCE = re.compile(r'(?=.*[A-Za-z]{2})[A-Za-z]+(?:\s*[,，]\s*[A-Za-z]+)+\s*')
# A parenthesized (1)/(2) sub-blank marker that continues the preceding numbered
# answer item's material (multi-line task-reading / cloze keys).
_SUBITEM_CONT = re.compile(r'^[(（]\s*\d{1,3}\s*[)）]')
_ROMAN_TITLE = re.compile(r'^\s*([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫIVX]+)[.．、]')
_ANSWER_ITEM = re.compile(r'(?:^|(?<=\s))(\d{1,3})[.．、](?!\d)')
_COMPACT_RANGE = re.compile(r'^\s*(\d{1,3})\s*[~～—–-]\s*(\d{1,3})\s+([A-HFTa-hft]+)\s*$')
# One compact-key token: either a whole-task range ``1~5 ABC`` or a single
# numbered item ``6.D``. Used to parse Roman lines mixing both forms.
_ANSWER_TOKEN = re.compile(
    r'(?P<first>\d{1,3})\s*[~～—–-]\s*(?P<last>\d{1,3})(?:\s+(?P<letters>[A-HFTa-hft]+))?'
    r'|(?P<num>\d{1,3})[.．、](?P<val>[^\s\u3000]*)')


def _answer_items(text):
    """Return only explicitly numbered answers, including inline items/ranges.

    A compact lesson key may combine whole-task ranges and single numbered items
    on one Roman line: ``1~5 BDBCA　6.D`` or ``1~5 ACABD　6~10 DDBAB``. Each
    whitespace-separated segment must be either a ``first~last LETTERS`` range
    or a ``N.value`` item, and together they must cover the whole line. Range
    length, ordering and uniqueness are still enforced so a malformed or
    duplicated range never silently proves an answer.
    """
    text = re.split(EXPLANATION_LABEL, text, maxsplit=1)[0].strip()
    text = re.sub(r'^[(（]\s*[)）]\s*', '', text)
    if not text:
        return []
    interval = _COMPACT_RANGE.fullmatch(text)
    if interval:
        first, last = int(interval[1]), int(interval[2])
        values = interval[3]
        if first <= last and len(values) == last - first + 1:
            return list(zip(range(first, last + 1), values))
        return []
    # Plain numbered items (values may contain spaces) take precedence so a
    # multi-word answer such as ``4.do you spell`` is not truncated.
    matches = list(_ANSWER_ITEM.finditer(text))
    if matches and matches[0].start() == 0:
        numbers = [int(match[1]) for match in matches]
        if len(matches) == 1 and '~' in text and not _ANSWER_TOKEN.fullmatch(text.strip()):
            return []
        if numbers == sorted(set(numbers)):
            return [(int(match[1]), re.sub(r'^【答案】\s*', '', text[match.end():
                     matches[pos + 1].start() if pos + 1 < len(matches) else len(text)]).strip())
                    for pos, match in enumerate(matches)]
        return []
    if '~' not in text and not re.search(r'[~～—–-]', text):
        return []
    # A line drawing only on range forms (``1~5 BDBCA　6.D``): every whitespace
    # segment must be a full range or a bare item covering the whole line.
    items, pos = [], 0
    for match in _ANSWER_TOKEN.finditer(text):
        if text[pos:match.start()].strip(' \t\u3000'):
            return []  # A non-space gap means this is prose, not a compact key.
        first = match.group('first')
        if first is not None:
            lo, hi = int(first), int(match.group('last'))
            letters = match.group('letters')
            if letters is None or lo > hi or len(letters) != hi - lo + 1:
                return []
            items.extend((n, letter) for n, letter in zip(range(lo, hi + 1), letters))
        else:
            items.append((int(match.group('num')), match.group('val').strip()))
        pos = match.end()
    if text[pos:].strip(' \t\u3000'):
        return []
    numbers = [n for n, _ in items]
    if not items or numbers != sorted(set(numbers)):
        return []
    return items


def _answer_section_topics(sections, lines):
    from .sync_practice import _LESSON, _UNIT, is_section_title, plain_text
    result, active, cursor = [], '', 0
    for section in sections:
        while cursor < section['start_line']:
            text = plain_text(lines[cursor]).strip()
            if _LESSON.match(text) or _UNIT.match(text):
                active = ''
            elif is_section_title(text):
                active = _norm(text)
            cursor += 1
        result.append(active)
    return result


def _compact_answer_blocks(lines):
    blocks, active, band, topic = [], None, '', ''
    from .sync_practice import _LESSON, _UNIT, is_section_title, plain_text
    for line, text in enumerate(lines, 1):
        visible = plain_text(text).strip()
        marker = _LESSON_BAND.fullmatch(text)
        if _LESSON.match(visible) or _UNIT.match(visible):
            topic, band, active = '', '', None
            continue
        if is_section_title(visible):
            topic, band, active = _norm(visible), '', None
            continue
        if marker:
            band, active = marker[1] if _answer_band(text) != 'invalid' else 'invalid', None
            continue
        # ☆拓展设问 / 拓展设问 is a sub-heading that belongs to the preceding group
        # task (rule: it is not a new numbered question). It must not poison or
        # split the current answer block.
        if re.fullmatch(r'[☆★]?\s*拓展设问\s*', text.strip()):
            continue
        heading = _ROMAN_ANSWER.match(text)
        if heading:
            active = {'ordinal': _norm(heading[1]), 'rows': [], 'raw': [],
                      'line': line, 'band': band, 'topic': topic, 'invalid': False, 'literal': False,
                      'coverage': {}}
            blocks.append(active)
            text = text[heading.end():]
        elif active is None:
            if not _answer_items(text) and not _LETTER_SEQUENCE.fullmatch(text.strip()):
                if blocks and text.strip():
                    # Unattached prose/headings after a key starts are not an
                    # ownership proof for a later bare numeric block.
                    blocks[-1]['invalid'] = True
                continue
            # Bare numeric keys are retained so a partial Roman skeleton cannot
            # hide extra answers. Only a unique final extension block may bind.
            active = {'ordinal': None, 'rows': [], 'raw': [], 'line': line,
                      'band': band, 'topic': topic, 'invalid': False, 'literal': False, 'coverage': {}}
            blocks.append(active)
        items = _answer_items(text)
        if items and not active['literal']:
            active['rows'].extend((n, line, value) for n, value in items)
            for n, _ in items:
                active['coverage'].setdefault(n, []).append(line)
            active['raw'].append((line, text.strip()))
        elif _SUBITEM_CONT.match(text.strip()) and active['raw']:
            # A parenthesized (n) sub-blank continues the current numbered item's
            # material (e.g. task-reading 1.(1)…(4) / (5)… / (6)…(8)). It is not a
            # new top-level answer and must not poison the block.
            if active['rows']:
                n0, l0, v0 = active['rows'][-1]
                active['rows'][-1] = (n0, l0, v0 + '\n' + text.strip())
                active['coverage'].setdefault(n0, [l0]).append(line)
            active['raw'].append((line, text.strip()))
        elif ((heading or not active['raw']) and
              (_LETTER_SEQUENCE.fullmatch(text.strip())
               or heading and _WORD_SEQUENCE.fullmatch(text.strip()))):
            # A Roman whole-task letter/word list is one literal answer. A word
            # list requires the printed Roman prefix; bare prose remains invalid.
            active['literal'] = True
            active['raw'].append((line, text.strip()))
        elif text.strip():
            active['invalid'] = True
            active = None
    return blocks


def _compact_scoped_candidates(unit, number, source_lines, *, sections=None):
    """Bind a complete single-lesson Roman skeleton, never a global number.

    The body may have image-only foundation/extension banners. A complete ordered
    skeleton is usable only within one nonempty lesson context, with every Roman
    section present on both sides. A partial key or a repeated lesson stays unbound.
    """
    from .sync_practice import heading_candidates, plain_text, is_section_title, _LESSON, _UNIT
    body_path = '01_source/正文.txt'
    body = source_lines.get(body_path, [])
    sections = sections if sections is not None else heading_candidates('\n'.join(body))
    body_bands = _answer_section_bands(sections, body)
    if 'invalid' in body_bands:
        return []
    for index, section in enumerate(sections):
        verified = section.get('lesson_band') or {}
        if verified.get('label'):
            label = _answer_band(verified['label'])
            if body_bands[index] and body_bands[index] != label:
                return []  # Printed text and verified visual evidence disagree.
            body_bands[index] = label
    contexts = {_norm(row.get('context') or '') for row in sections}
    topics = _answer_section_topics(sections, body)
    lesson_markers = [text for text in body if _LESSON.match(plain_text(text).strip())]
    lesson_contexts = {_norm(plain_text(text).strip()) for text in lesson_markers}
    topic_contexts = {topic for topic in topics if topic}
    if (len(lesson_markers) > 1 or not contexts
            or (len(contexts) != 1 and not (len(lesson_markers) == 1
                and contexts <= lesson_contexts | topic_contexts))
            or not any(contexts)):
        return []
    ordinals = [_ROMAN_TITLE.match(_source_line(row['title'])) for row in sections]
    if not sections:
        return []
    # One otherwise unnumbered final section can have its own extension key.
    # It must follow the complete Roman prefix, not be guessed among several.
    trailing = bool(not ordinals[-1] and all(ordinals[:-1]) and len(ordinals) > 1)
    verified_extension = [i for i, ordinal in enumerate(ordinals) if not ordinal]
    verified_extension = bool(verified_extension and all(
        (sections[i].get('lesson_band') or {}).get('label') == '能力提升'
        for i in verified_extension))
    if not all(ordinals) and not trailing and not verified_extension:
        return []
    # An unrecognized printed Roman heading must not silently disappear from the
    # skeleton and shift every later foundation/extension occurrence.
    roman_lines = {line for line, text in enumerate(body, 1) if _ROMAN_TITLE.match(text)}
    if roman_lines != {row['start_line'] for row, ordinal in zip(sections, ordinals) if ordinal}:
        return []
    owner = unit.get('source_section') or {}
    positions = [i for i, row in enumerate(sections) if row['start_line'] == owner.get('start_line')
                 and row['end_line'] == owner.get('end_line')
                 and _norm(row['title']) == _norm(owner.get('title') or '')
                 and _norm(row.get('context') or '') == _norm(owner.get('context') or '')
                 and row['occurrence'] == owner.get('occurrence')]
    start = (unit.get('source_span') or {}).get('start_line')
    if len(positions) != 1 or not scoped_source_matches(unit, number, body_path, start, source_lines):
        return []
    # A companion row cannot disambiguate repeated passage numbers in one section.
    section = sections[positions[0]]
    anchors = _printed_item_anchors(body[section['start_line'] - 1:section['end_line']])
    if sum(n == int(number) for _, n in anchors) > 1:
        return []
    skeleton = [_norm(match[1]) if match else None for match in ordinals]
    span = unit.get('source_span') or {}
    # An explicitly source-bound standalone may also represent one whole task
    # without printed child numbers. Keep the older group path, and require the
    # stronger span/printed-number proof plus a narrow word-list value below for
    # standalone tasks; an empty number list or absent anchor alone is no proof.
    whole_standalone = (unit.get('unit_kind') == 'standalone'
                        and span.get('basis') in {'ai_classified', 'cleaned_source'}
                        and span.get('printed_numbers') == [])
    whole = (span.get('number_binding') == 'unnumbered_section'
             and unit.get('numbers') == [1] and int(number) == 1
             and (unit.get('unit_kind') == 'group' or whole_standalone) and not anchors)
    if whole and len(lesson_markers) != 1:
        return []  # A Unit heading alone cannot prove a whole unnumbered lesson task.
    result = []
    for path, lines in source_lines.items():
        if path == body_path:
            continue
        explicit_contexts = {_norm(plain_text(text).strip()) for text in lines
                             if _LESSON.match(plain_text(text).strip())
                             or _UNIT.match(plain_text(text).strip())}
        if explicit_contexts and explicit_contexts != (lesson_contexts or contexts):
            continue
        blocks = _compact_answer_blocks(lines)
        if [block['ordinal'] for block in blocks] != skeleton:
            continue
        # Explicit body/verified banners are stronger than the Roman sequence.
        # Even identical values cannot borrow another foundation/extension band.
        if any(topic != block['topic'] for topic, block in zip(topics, blocks)):
            # A whole-topic paper may print its sole topic only in the body and
            # start the appendix directly with the Roman keys. The complete
            # skeleton and all printed numbers below can prove that omission;
            # they cannot choose between multiple/repeated topics or override an
            # explicitly different appendix topic. Never erase topic boundaries
            # from a mixed lesson (e.g. a later Pronunciation section).
            sole_topics = set(topics)
            omitted_sole_topic = (len(sole_topics) == 1 and all(topics)
                and not any(block['topic'] for block in blocks)
                and sum(is_section_title(plain_text(text).strip())
                        and _norm(plain_text(text).strip()) in sole_topics
                        for text in body) == 1)
            if not omitted_sole_topic:
                continue
        if (any(block['band'] == 'invalid' for block in blocks)
                or any(band and band != 'pronunciation' and _answer_band(block['band']) != band
                       for band, block in zip(body_bands, blocks))):
            continue
        # The complete sequence is the ownership proof, so a malformed peer
        # cannot silently help assign a later same-number key by elimination.
        for block in blocks:
            numbers = [n for n, _, _ in block['rows']]
            duplicates = len(numbers) != len(set(numbers))
            if duplicates and not block['invalid']:
                # Only a repeated complete, verbatim key within this same block
                # is equivalent. A repeated isolated item is not coverage proof.
                owner_section = sections[blocks.index(block)]
                printed = [n for _, n in _printed_item_anchors(
                    body[owner_section['start_line'] - 1:owner_section['end_line']])]
                width = len(printed)
                chunks = [block['rows'][i:i + width] for i in range(0, len(numbers), width)] if width else []
                duplicates = not (printed and len(printed) == len(set(printed)) and chunks
                    and all([n for n, _, _ in chunk] == printed for chunk in chunks)
                    and all([(n, value) for n, _, value in chunk]
                            == [(n, value) for n, _, value in chunks[0]] for chunk in chunks))
            if block['invalid'] or duplicates:
                raise ValueError('scoped answer source scope unbound: duplicate or malformed compact answer block')
        if any(body_bands) or any(topics):
            complete = True
            for peer_section, peer in zip(sections, blocks):
                printed = [n for _, n in _printed_item_anchors(
                    body[peer_section['start_line'] - 1:peer_section['end_line']])]
                keyed = list(dict.fromkeys(n for n, _, _ in peer['rows']))
                if (printed and (len(printed) != len(set(printed)) or keyed != printed)
                        or not printed and not peer['raw']):
                    complete = False
                    break
            if not complete:
                continue  # Band-aware skeletons prove the complete printed coverage.
        if trailing:
            markers = [(i, _LESSON_BAND.fullmatch(text)) for i, text in enumerate(lines, 1)
                       if _LESSON_BAND.fullmatch(text)]
            extension = [i for i, marker in markers if marker[1] in _EXTENSION_BANDS]
            bands = [marker[1] for _, marker in markers]
            # Exactly one extension band must precede the tail block. The prefix
            # may be empty or a single foundation banner (基础巩固 / 重要考点集训);
            # duplicate or misplaced banners stay unbound.
            prefix = bands[:-1]
            prefix_ok = len(prefix) <= 1 and all(b not in _EXTENSION_BANDS for b in prefix)
            if (len(lesson_markers) != 1 or len(extension) != 1
                    or not bands or bands[-1] not in _EXTENSION_BANDS
                    or any(b in _EXTENSION_BANDS for b in bands[:-1])
                    or not prefix_ok
                    or (markers[0][0] >= blocks[0]['line'])
                    or not blocks[-2]['line'] < extension[0] < blocks[-1]['line']):
                continue
            # Residual ownership is only complete if every preceding printed
            # section has its entire unique number sequence in its own key.
            prefix_complete = True
            for owner_section, peer in zip(sections[:-1], blocks[:-1]):
                printed = [n for _, n in _printed_item_anchors(
                    body[owner_section['start_line'] - 1:owner_section['end_line']])]
                keyed = [n for n, _, _ in peer['rows']]
                if (printed and (len(printed) != len(set(printed)) or keyed != printed)
                        or not printed and not peer['raw']):
                    prefix_complete = False
                    break
            if not prefix_complete:
                continue
            tail = blocks[-1]
            last = sections[-1]
            expected = [n for _, n in _printed_item_anchors(body[last['start_line'] - 1:last['end_line']])]
            actual = [n for n, _, _ in tail['rows']]
            # Printed extension banner + exact unique tail numbers + full Roman
            # prefix is the proof. Body banner images are not treated as OCR.
            if (tail['band'] not in _EXTENSION_BANDS or not expected or len(expected) != len(set(expected))
                    or actual != expected or any(b['band'] in _EXTENSION_BANDS for b in blocks[:-1])):
                continue
        block = blocks[positions[0]]
        if whole:
            # An internal alias is the entire unnumbered source task, never the
            # first printed item of its answer key. Preserve all key text/lines.
            if any(re.search(EXPLANATION_LABEL + '|' + IMG, text) for _, text in block['raw']):
                continue  # Whole-task copying cannot swallow explanations/images.
            value = '\n'.join(text for _, text in block['raw'])
            if whole_standalone and not (block['literal'] and _WORD_SEQUENCE.fullmatch(value)):
                continue  # Do not generalize standalone aliases to arbitrary free answers.
            if value:
                result.extend((path, line, value) for line, _ in block['raw'])
        elif not block['literal'] and anchors:
            result.extend((path, covered, value) for n, line, value in block['rows'] if n == int(number)
                          for covered in block['coverage'].get(n, [line]))
    return result


def _scoped_answer_candidates(unit, number, lines, *, batch_dir=None):
    from .sync_practice import heading_candidates
    body_path = '01_source/正文.txt'
    body = lines.get(body_path, [])
    sections = heading_candidates('\n'.join(body))
    if batch_dir is not None:
        from .lesson_bands import load_verified, annotate_sections
        # Hash verification must use the untouched text, not the stripped answer
        # recognition view, whose formatting and numbered blanks may differ.
        evidence = load_verified(batch_dir)
        if ((Path(batch_dir) / '01_source/lesson-bands.json').is_file()
                and (evidence.get('status') == 'unavailable'
                     or evidence.get('status') == 'partial' and evidence.get('issues')
                     or any(row.get('code') in {'BAND_EVIDENCE_STALE', 'BAND_MEDIA_STALE', 'BAND_BOUNDARY_INVALID'}
                            for row in evidence.get('issues') or []))):
            return []  # A known stale/failed proof cannot fall back to Roman guessing.
        annotated = annotate_sections(sections, evidence)
        # Accept a confirmed window tightened exactly to a current verified band
        # boundary. Keep old identities valid too; never expand a unit span.
        owner = unit.get('source_section') or {}
        for original, tagged in zip(sections, annotated):
            if (tagged.get('lesson_band') and original['start_line'] == owner.get('start_line')
                    and owner.get('end_line') == tagged.get('end_line')
                    and tagged['end_line'] <= original['end_line']
                    and (unit.get('source_span') or {}).get('end_line', len(body) + 1) <= tagged['end_line']):
                unit = dict(unit, source_section=dict(owner, end_line=original['end_line']))
        sections = [dict(section, **({'lesson_band': tagged['lesson_band']}
                                    if tagged.get('lesson_band') else {}))
                    for section, tagged in zip(sections, annotated)]
    owners = [row for row in sections
              if row['start_line'] == (unit.get('source_section') or {}).get('start_line')]
    bands = _answer_section_bands(sections, body)
    owner_band = ''
    if len(owners) == 1:
        owner_band = bands[sections.index(owners[0])]
        visual = (owners[0].get('lesson_band') or {}).get('label')
        if visual:
            verified = _answer_band(visual)
            if owner_band and verified != owner_band:
                return []
            owner_band = verified
    candidates = []
    for path, rows in lines.items():
        for index, text in enumerate(rows, 1):
            if path == '01_source/正文.txt' and not re.search(EXPLANATION_LABEL + r'|【答案】', text):
                continue  # A stem/option line is not an independent answer.
            for n, value in _answer_items(text):
                if n == int(number) and scoped_source_matches(unit, number, path, index, lines, equivalent_answers=True):
                    companion_band = _answer_section_bands([{'start_line': index}], rows)[0]
                    if path == body_path or not owner_band or companion_band == owner_band:
                        candidates.append((path, index, value))
    primary_lines = {path: rows for path, rows in lines.items() if path in SOURCES}
    candidates.extend(_compact_scoped_candidates(unit, number, primary_lines, sections=sections))
    primary = list(dict.fromkeys(row for row in candidates if row[0] in SOURCES))
    if primary:
        return primary  # Malformed fallback material cannot poison real Word content.
    fallback_lines = {path: rows for path, rows in lines.items()
                      if path == '01_source/正文.txt' or path not in SOURCES}
    candidates.extend(_compact_scoped_candidates(unit, number, fallback_lines, sections=sections))
    return list(dict.fromkeys(candidates))


def resolve_scoped_answer(batch_dir, unit, number, answer=None):
    """Read-only deterministic recovery of a unique value and real coordinates.

    Consumers may add this independently verified citation to a retained candidate;
    no task artifact, manifest, or model claim is trusted or changed here.
    """
    if unit.get('identity_version') != 'scoped-v1' or unit.get('type') == '书面表达':
        return None
    from .agent_tasks import _answer_comparison_text as normalize_answer
    from .content_rules import is_placeholder
    batch = Path(batch_dir)
    paths = [*SOURCES, *(p.relative_to(batch).as_posix() for p in sorted(
        (batch / '01_source/materials').glob('attachment_*.txt'))
        if re.fullmatch(r'attachment_\d{3,}\.txt', p.name))]
    lines = {path: [_source_line(s, anchor_view=path == '01_source/正文.txt') for s in (batch / path).read_text(encoding='utf-8-sig').splitlines()]
             for path in paths if (batch / path).is_file() and path not in reference_only_sources(batch)}
    try:
        rows = _scoped_answer_candidates(unit, number, lines, batch_dir=batch)
    except ValueError:
        return None
    if not rows or len({normalize_answer(row[2]) for row in rows}) != 1:
        return None
    value = rows[0][2]
    if is_placeholder(value) or (answer is not None and normalize_answer(value) != normalize_answer(answer)):
        return None
    return {'answer': value, 'evidence': [f'{path}:{line}' for path, line, _ in rows]}


def validate_scoped_answer(batch_dir, unit, number, answer, evidence):
    """Raise on unbound scoped answers; legacy callers keep their existing checks.

    Evidence must locate an actual answer row, e.g. 01_source/附录.txt:12 or
    附录 L12. Numeric-only appendices cannot establish a section identity.
    """
    from .question_identity import VERSION
    if unit.get('identity_version') != VERSION or unit.get('type') == '书面表达':
        return
    from .agent_tasks import _answer_comparison_text as normalize_answer
    batch = Path(batch_dir)
    paths = list(SOURCES)
    paths.extend(p.relative_to(batch).as_posix() for p in sorted((batch / '01_source/materials').glob('attachment_*.txt'))
                 if re.fullmatch(r'attachment_\d{3,}\.txt', p.name))
    lines = {path: [_source_line(s, anchor_view=path == '01_source/正文.txt') for s in (batch / path).read_text(encoding='utf-8-sig').splitlines()]
             for path in paths if (batch / path).is_file() and path not in reference_only_sources(batch)}
    candidates = _scoped_answer_candidates(unit, number, lines, batch_dir=batch)
    def cited(path, line):
        stem = Path(path).stem
        aliases = (path, stem) if sum(Path(p).stem == stem for p in lines) == 1 else (path,)
        return any(any(re.search(re.escape(alias) + r'\s*(?::\s*|[Ll]\s*|第\s*)' + str(line) + r'(?!\d)', str(e))
                       for alias in aliases) for e in (evidence or []))
    bound = [row for row in candidates if normalize_answer(row[2]) == normalize_answer(answer) and cited(row[0], row[1])]
    if ((unit.get('source_span') or {}).get('number_binding') == 'unnumbered_section'
            or any('\n' in row[2] for row in candidates
                   if normalize_answer(row[2]) == normalize_answer(answer))):
        # Whole tasks and sub-blank continuations may occupy several original
        # lines. Citing only their first line cannot prove the complete value.
        paths = {path for path, _, _ in bound}
        bound = [row for row in bound if any(
            all(cited(p, line) for p, line, value in candidates
                if p == path and normalize_answer(value) == normalize_answer(answer))
            for path in paths)]
    if len({normalize_answer(row[2]) for row in candidates}) > 1:
        bound = []
    if not bound:
        raise ValueError(f"{unit.get('unit_id')}/题{number}: scoped answer source is not uniquely bound; "
                         "答案来源缺少可核验的大题scope/原文坐标，禁止按全局题号借用")


def validate_scoped_missing(batch_dir, unit, number, evidence=None):
    """Reject absence when a real answer exists or its scope remains unresolved."""
    if unit.get('identity_version') != 'scoped-v1' or unit.get('type') == '书面表达':
        return
    batch = Path(batch_dir)
    paths = [*SOURCES, *(p.relative_to(batch).as_posix() for p in (batch / '01_source/materials').glob('attachment_*.txt')
                        if re.fullmatch(r'attachment_\d{3,}\.txt', p.name))]
    source_lines = {path: [_source_line(s, anchor_view=path == '01_source/正文.txt') for s in (batch / path).read_text(encoding='utf-8-sig').splitlines()]
                    for path in paths if (batch / path).is_file() and path not in reference_only_sources(batch)}
    confirmed = batch / '01_source/structure.confirmed.json'
    units = json.loads(confirmed.read_text(encoding='utf-8-sig')).get('units', []) if confirmed.is_file() else [unit]
    from .content_rules import is_placeholder
    candidates = _scoped_answer_candidates(unit, number, source_lines, batch_dir=batch)
    found = [f'{path}:{line}' for path, line, value in candidates if not is_placeholder(value)]
    unbound = []
    # Missing is not a way to discard a conflicting or unbound compact source.
    for path, rows in source_lines.items():
        if path == '01_source/正文.txt':
            continue
        compact = _compact_answer_blocks(rows)
        parsed = [(n, line, value) for block in compact for n, line, value in block['rows']]
        # A printed whole-task literal still exists when its current identity
        # cannot bind. Do not relabel that unresolved original as absent.
        for block in compact:
            if block['literal']:
                for line, value in block['raw']:
                    if not is_placeholder(value) and not any(
                            any(p == path and l == line for p, l, _ in
                                _scoped_answer_candidates(other, n, source_lines, batch_dir=batch))
                            for other in units for n in other.get('numbers') or []):
                        unbound.append(f'{path}:{line}')
        parsed.extend((n, line, value) for line, text in enumerate(rows, 1)
                      for n, value in _answer_items(text))
        for n, line, value in parsed:
            if n != int(number) or is_placeholder(value):
                continue
            if not any(any(p == path and l == line for p, l, _ in
                           _scoped_answer_candidates(other, number, source_lines, batch_dir=batch))
                       for other in units if number in (other.get('numbers') or [])):
                unbound.append(f'{path}:{line}')
    if found:
        raise ValueError(f"{unit.get('unit_id')}/题{number}: missing contradicts an original scoped answer at " + ', '.join(found))
    if unbound:
        raise ValueError(f"{unit.get('unit_id')}/题{number}: answer scope unbound at " + ', '.join(unbound)
                         + '; 纯数字答案附录缺少大题scope证据，不能宣称绑定成功或缺失')


def _inspect_content(batch_dir, payload, *, strict=False, restore=False):
    """共享来源绑定；只有显式 restore 模式允许恢复内存候选，不写文件。"""
    batch = Path(batch_dir).resolve()
    errors, warnings, hashes, summaries, questions, anchors = [], [], {}, [], [], []
    restorations = []
    body_anchors = []
    source_lines, raw_source_lines = {}, {}
    material_dir = batch / '01_source/materials'
    reference_only = reference_only_sources(batch)
    extra_sources = [p.relative_to(batch).as_posix() for p in sorted(material_dir.glob('attachment_*.txt'))
                     if re.fullmatch(r'attachment_\d{3,}\.txt', p.name)] if material_dir.is_dir() else []
    for relative in (*SOURCES, *extra_sources):
        path = batch / relative
        if not path.is_file():
            continue
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != batch.parent):
            raise ValueError('内容来源不允许软链接')
        raw = path.read_bytes(); hashes[relative] = hashlib.sha256(raw).hexdigest()
        text = raw.decode('utf-8-sig')
        raw_source_lines[relative] = text.splitlines()
        lines = [_source_line(line, anchor_view=relative == '01_source/正文.txt') for line in raw_source_lines[relative]]
        source_lines[relative] = lines
        if relative in reference_only:
            continue  # Corroborating copies never supply or compete for content.
        section = None
        for index, line in enumerate(lines):
            heading = _section(line)
            if heading:
                section = heading
            boundary = (BODY_QUESTION if relative == '01_source/正文.txt' else QUESTION_BOUNDARY).match(line)
            if boundary:
                target = body_anchors if relative == '01_source/正文.txt' else anchors
                target.append((int(boundary.group(1)), relative, index + 1, section))
                # Extra inline numbers are ambiguous anchors too, including
                # repetitions and compact forms such as 1.A2.B.
                for extra in _inline_numbers(line[boundary.end(1):]):
                    target.append((int(extra.group()[:-1]), relative, index + 1, section))
        # Bound each explanation to its own question and stop at every
        # structural boundary, including headings whose meaning is unknown.
        for index, line in enumerate(lines):
            match = _question(line)
            if not match:
                continue
            head = line[match.end():]
            # _question already consumes the answer prefix through 【解析】.
            # The remaining first character may be substantive (e.g. “A项意为”),
            # and must never be stripped as though it were an answer letter.
            parts = [head]
            for later in lines[index + 1:]:
                if (QUESTION_BOUNDARY.match(later) or _answer_range_boundary(later) or SUMMARY.search(later)
                        or _section(later) or ICON_HEADING.match(later)
                        or ANNOTATION.match(later)
                        or PASSAGE_HEADING.match(later)
                        or re.fullmatch(r'\[/?TABLE[^\]]*\]', later)):
                    break
                parts.append(later)
            questions.append({'number': int(match.group(1)), 'text': _norm(''.join(parts)),
                              'verbatim': _verbatim(''.join(parts)), 'source_text': '\n'.join(parts).strip(),
                              'path': relative, 'line': index + 1, 'end_line': index + len(parts),
                              'anchor_line': index + 1})
        # Some answer keys print the answer on its own numbered line and the
        # 【解析】 in a following paragraph without a number (e.g. "74. It wasn't
        # easy." then "【解析】根据…"). Bind such a stray explanation to the most
        # recent numbered answer line that has no explanation yet. This never
        # crosses a structural boundary or attaches to an already-explained row.
        for index, line in enumerate(lines):
            match = STRAY_EXPLANATION.match(line)
            if not match:
                continue
            owner = None
            for back in range(index - 1, -1, -1):
                previous = lines[back]
                if (SUMMARY.search(previous) or _answer_range_boundary(previous) or _section(previous)
                        or PASSAGE_HEADING.match(previous) or ICON_HEADING.match(previous)
                        or ANNOTATION.match(previous) or STRAY_EXPLANATION.match(previous)):
                    break
                boundary = QUESTION_BOUNDARY.match(previous)
                if boundary and _question(previous) is None:
                    number = int(boundary.group(1))
                    if not any(row['number'] == number and row['path'] == relative
                               and back + 1 <= row['line'] <= index + 1 for row in questions):
                        owner = number
                    break
            if owner is None:
                continue
            head = line[match.end():]
            parts = [head]
            for later in lines[index + 1:]:
                if (QUESTION_BOUNDARY.match(later) or _answer_range_boundary(later) or SUMMARY.search(later)
                        or _section(later) or ICON_HEADING.match(later)
                        or ANNOTATION.match(later) or PASSAGE_HEADING.match(later)
                        or STRAY_EXPLANATION.match(later)
                        or re.fullmatch(r'\[/?TABLE[^\]]*\]', later)):
                    break
                parts.append(later)
            questions.append({'number': owner, 'text': _norm(''.join(parts)),
                              'verbatim': _verbatim(''.join(parts)), 'source_text': '\n'.join(parts).strip(),
                              'path': relative, 'line': index + 1, 'end_line': index + len(parts),
                              'anchor_line': back + 1})
        for index, line in enumerate(lines):
            match = SUMMARY.search(line)
            if not match:
                continue
            parts = [match.group(1)]
            owner = None
            for later in lines[index + 1:]:
                question = _question(later)
                if question:
                    owner = int(question.group(1)); break
                if (SUMMARY.search(later) or _answer_range_boundary(later) or _section(later)
                        or QUESTION_BOUNDARY.match(later) or ICON_HEADING.match(later)
                        or ANNOTATION.match(later)
                        or PASSAGE_HEADING.match(later) or PART_HEADING.match(later)
                        or PASSAGE_INSTRUCTION.match(later)
                        or re.fullmatch(r'\[/?TABLE[^\]]*\]', later)):
                    break
                if later.strip():
                    parts.append(later)
            value = _norm(''.join(parts))
            if value:
                members = []
                _exp = re.compile(r'^\s*【\s*(?:解析|详解|分析)\s*】')
                for j in range(index + 1, len(lines)):
                    later = lines[j]
                    if (SUMMARY.search(later) or _answer_range_boundary(later) or _section(later) or ICON_HEADING.match(later)
                            or PASSAGE_HEADING.match(later) or PART_HEADING.match(later)
                            or PASSAGE_INSTRUCTION.match(later)
                            or re.fullmatch(r'\[/?TABLE[^\]]*\]', later)):
                        break
                    question = QUESTION_BOUNDARY.match(later)
                    if question:
                        if not _question(later):
                            # 纯答案行（无【解析】）：若紧接下一行以【解析】开头，则它仍是
                            # 该题组的成员，不能在此中断（否则组级【文章大意】只覆盖到前几题）。
                            follow = lines[j + 1] if j + 1 < len(lines) else ''
                            if _exp.match(follow):
                                members.append(int(question.group(1)))
                                continue
                            break
                        members.append(int(question.group(1)))
                # 纯答案行（无【解析】）也能被 members 收集；此时直接用它所属的
                # 第一个题号作为 owner，而不是因为向前扫描遇到纯答案行就丢失归属。
                if owner is None and members:
                    owner = members[0]
                # 段落末尾的【文章大意】后面通常直接是下一篇材料，前面才是它概括的
                # 46–55 题。此时向前找本段起始边界，把大意归属到前面这组题号。
                if owner is None:
                    boundary = 0
                    for j in range(index):
                        if (_section(lines[j]) or PASSAGE_HEADING.match(lines[j])
                                or PART_HEADING.match(lines[j])
                                or PASSAGE_INSTRUCTION.match(lines[j])
                                or SUMMARY.search(lines[j])):
                            boundary = j + 1
                    source_anchors = body_anchors if relative == '01_source/正文.txt' else anchors
                    prior = []
                    for number, path, line_no, _section_name in source_anchors:
                        if path == relative and boundary < line_no < index + 1 and number not in prior:
                            prior.append(number)
                    if prior:
                        members = prior
                        owner = prior[0]
                summaries.append({'text': value, 'verbatim': _verbatim(''.join(parts)), 'source_text': '\n'.join(parts).strip(), 'owner': owner, 'members': members,
                                  'path': relative, 'line': index + 1, 'end_line': index + len(parts)})

    # A PDF supplied alongside Word is layout evidence, not a competing text
    # authority. Prefer the primary/answer Word excerpts per question or passage;
    # retain attachment-only content when the primary has no corresponding field.
    # Repeated anchors are equivalent only with complete, identical content proof.
    all_questions = list(questions)
    all_summaries = list(summaries)
    attachment_questions = [row for row in questions if row['path'] not in SOURCES]
    attachment_summaries = [row for row in summaries if row['path'] not in SOURCES]
    primary_numbers = {row['number'] for row in questions if row['path'] in SOURCES}
    questions = [row for row in questions
                 if row['path'] in SOURCES or row['number'] not in primary_numbers]
    primary_summary_owners = {row['owner'] for row in summaries
                              if row['path'] in SOURCES and row['owner'] is not None}
    summaries = [row for row in summaries
                 if row['path'] in SOURCES or row['owner'] not in primary_summary_owners]

    metadata_path = _safe_path(batch, '01_source/metadata.json')
    metadata = {}
    if metadata_path.is_file():
        metadata_raw = metadata_path.read_bytes()
        hashes['01_source/metadata.json'] = hashlib.sha256(metadata_raw).hexdigest()
        metadata = json.loads(metadata_raw.decode('utf-8-sig'))
        body = _safe_path(batch, '01_source/正文.txt')
        if body.is_file():
            hashes['01_source/正文.txt'] = hashlib.sha256(body.read_bytes()).hexdigest()
        if not body.is_file() or metadata.get('source_sha256') != hashes['01_source/正文.txt']:
            metadata = {}  # 过期元信息不能证明任何位置或来源。

    def error(code, message, uid, field, evidence=None):
        errors.append({'code': code, 'message': message, 'unit_id': uid, 'field': field,
                       'evidence': evidence or []})

    def warning(code, message, uid, field, evidence=None):
        warnings.append({'code': code, 'message': message, 'unit_id': uid, 'field': field,
                         'evidence': evidence or []})

    # explanations 产物按 unit_id 落盘，独立题本身不带 number；从已确认结构
    # 回填题号，否则会把它误判成“解析无法溯源”。
    confirmed_units = {}
    try:
        confirmed_path = _safe_path(batch, '01_source/structure.confirmed.json')
        if confirmed_path.is_file():
            confirmed_data = json.loads(confirmed_path.read_bytes().decode('utf-8-sig'))
            for unit in confirmed_data.get('units') or []:
                if not isinstance(unit, dict):
                    continue
                nums = unit.get('numbers') or []
                kind = unit.get('unit_kind')
                uid = unit.get('unit_id') or unit.get('id')
                if not uid and nums:
                    uid = ('q-' if kind == 'standalone' else 'group-') + '-'.join(str(n) for n in nums)
                if uid:
                    confirmed_units[str(uid)] = unit
    except (OSError, ValueError):
        confirmed_units = {}
    writing_number_set = {str(n) for unit in confirmed_units.values()
                          if unit.get('identity_version') != 'scoped-v1'
                          and str(unit.get('type') or '').strip() == '书面表达'
                          for n in (unit.get('numbers') or [])}

    def unit_numbers(uid, item):
        numbers = item.get('numbers') or [item.get('number')]
        if any(isinstance(n, int) for n in numbers):
            return numbers
        unit = confirmed_units.get(str(uid)) or {}
        declared = unit.get('numbers') or []
        return [int(n) for n in declared if str(n).isdigit()] or numbers

    active_unit = {}

    def anchor_problem(number, path, line, *, allow_answer_source=False, expected=None):
        """Return a source failure reason, or None after actual ownership proof.

        Legacy appendix duplicates are equivalent only if every physical anchor
        owns one complete explanation with the same verbatim content. Bare answer
        lines, inline repeated numbers and different sections remain ambiguous.
        Scoped identities retain their exact window/section rules.
        """
        if path in reference_only:
            return 'reference_only_source'
        if type(number) is not int:
            return 'invalid_question_number'
        if active_unit.get('identity_version') == 'scoped-v1':
            if not scoped_source_matches(active_unit, number, path, line, source_lines):
                return 'source_scope_mismatch'
            local = [row for row in questions if row['number'] == number
                     and scoped_source_matches(active_unit, number, row['path'], row['line'], source_lines)]
            if not allow_answer_source and len({row['verbatim'] for row in local}) > 1:
                return 'conflicting_original_content'
            return None
        candidates = body_anchors if path == '01_source/正文.txt' else anchors
        hits = [entry for entry in candidates if entry[0] == number and entry[1] == path]
        local = [row for row in all_questions if row['number'] == number and row['path'] == path]
        if not hits:
            return 'missing_source_anchor'
        if len(hits) > 1:
            if len({row['verbatim'] for row in local}) > 1:
                return 'conflicting_original_content'
            if (len(local) != len(hits) or not local or not local[0]['verbatim']
                    or any(sum(row['anchor_line'] == hit[2] for row in local) != 1 for hit in hits)):
                return 'ambiguous_repeated_anchors'
        if not allow_answer_source and not any(row['line'] == line for row in local):
            return 'explanation_coordinate_mismatch'
        # Separate originals still compete; only existing verbatim equivalence
        # is accepted. NFKC punctuation folding cannot hide different prose.
        if not allow_answer_source:
            wanted = _verbatim(expected) if expected is not None else next(
                (row['verbatim'] for row in local if row['line'] == line), None)
            competing = [row for row in questions if row['number'] == number
                         and row['path'] != path and row['path'] != '01_source/正文.txt'
                         and (wanted is None or row['verbatim'] != wanted)]
            if competing:
                return 'conflicting_original_content'
        if path != '01_source/正文.txt':
            body_hits = [entry for entry in body_anchors if entry[0] == number]
            if len(body_hits) != 1:
                return 'body_anchor_not_unique'
            # Bind this physical candidate's own section, never use an equal
            # copy under a different heading to prove its coordinates. Other
            # identical copies do not veto an independently valid candidate.
            current = next((row for row in local if row['line'] == line), None)
            current_line = current['anchor_line'] if current else line
            current_hits = [hit for hit in hits if hit[2] == current_line]
            if allow_answer_source:
                current_hits = hits  # Summary membership has separate proof.
            if not any(_same_section(hit[3], body_hits[0][3]) for hit in current_hits):
                return 'source_section_mismatch'
        elif any(not _same_section(hit[3], hits[0][3]) for hit in hits):
            return 'source_section_mismatch'
        return None

    def unique_anchor(number, path, line, *, allow_answer_source=False, expected=None):
        return anchor_problem(number, path, line, allow_answer_source=allow_answer_source,
                              expected=expected) is None

    def unique_summary(row):
        peers = [other for other in summaries if other['owner'] == row['owner']
                 and other['path'] == row['path']]
        return all(other['verbatim'] == row['verbatim']
                   and set(other['members']) == set(row['members']) for other in peers)

    def binding_evidence(numbers, value, field):
        pool = all_questions if field == 'explanation' else all_summaries
        wanted = _verbatim(value)
        rows = [row for row in pool if (row.get('number', row.get('owner')) in numbers
                                       or row['verbatim'] == wanted)]
        candidates = []
        for row in rows:
            number = row.get('number', row.get('owner'))
            problem = anchor_problem(number, row['path'], row['line'],
                                     allow_answer_source=field != 'explanation', expected=value)
            if number not in numbers:
                problem = 'question_number_mismatch'
            elif row['verbatim'] != wanted:
                problem = problem or 'content_mismatch'
            elif field != 'explanation' and (set(row['members']) != set(numbers) or not unique_summary(row)):
                problem = 'summary_ownership_conflict'
            candidates.append({'path': row['path'], 'line': row['line'], 'end_line': row['end_line'],
                               'number': number, 'content_match': row['verbatim'] == wanted,
                               'anchor_count': sum(n == number and p == row['path'] for n, p, _, _ in anchors + body_anchors),
                               'source_sha256': hashes[row['path']], 'reason': problem or 'candidate_bound'})
        reasons = sorted({row['reason'] for row in candidates if row['reason'] != 'candidate_bound'})
        return [{'reason': 'source_binding_unverified', 'numbers': numbers,
                 'candidate_count': len(candidates), 'reasons': reasons or ['no_matching_source_content'],
                 'candidates': candidates}]

    def unverified(code, message, uid, field, numbers, value, item, *, warn=False):
        evidence = binding_evidence(numbers, value, field)
        ref_problem = reference_problem(item, numbers, field, value)
        if ref_problem:
            evidence[0]['reasons'].append(ref_problem)
            evidence[0]['references'] = [entry for entry in item.get('evidence', [])
                                          if isinstance(entry, str) and entry.startswith('source_ref ')]
        (warning if warn or not strict else error)(code, message, uid, field, evidence)
        # Source conflicts cannot be repaired by asking a model to re-copy prose.
        # A wrong model string/coordinate is deliberately still retryable.
        if not warn and strict and not ref_problem and any(
                row['content_match'] and row['reason'] in {'conflicting_original_content',
                                                          'ambiguous_repeated_anchors', 'body_anchor_not_unique'}
                for row in evidence[0]['candidates']):
            errors[-1].update(retryable=False, failure_class='source_binding')

    def writing_source():
        # Only an already-passed, input-bound structure gate can supply the
        # number of an unprinted writing question. Never infer it here.
        paths = ('01_source/structure.json', '01_source/structure.confirmed.json',
                 '07_review/structure-gate.json')
        try:
            documents = []
            for relative in paths:
                path = _safe_path(batch, relative)
                raw = path.read_bytes()
                hashes[relative] = hashlib.sha256(raw).hexdigest()
                documents.append(json.loads(raw.decode('utf-8-sig')))
            structure, confirmed, gate = documents
            inputs = gate['input_hashes']
            if gate.get('passed') is not True or any(
                    inputs.get(path) != hashes.get(path)
                    for path in ('01_source/正文.txt', *paths[:2])):
                return None
            body = source_lines['01_source/正文.txt']
            appendix = source_lines['01_source/附录.txt']
            body_heads = [(i, _section(line)) for i, line in enumerate(body)
                          if _section(line) and _section(line)[1] == '书面表达']
            appendix_heads = [(i, _section(line)) for i, line in enumerate(appendix)
                              if _section(line) and _section(line)[1] == '书面表达']
            if len(body_heads) != 1 or len(appendix_heads) != 1:
                return None
            bi, bh = body_heads[0]; ai, ah = appendix_heads[0]
            if not _same_section(bh, ah) or not re.search(r'共\s*1\s*小题', body[bi]):
                return None
            body_end = next((i for i in range(bi + 1, len(body)) if _section(body[i])), len(body))
            writing_body = '\n'.join(body[bi + 1:body_end])
            tasks = re.findall(r'写(?:一)?[篇封]|\bwrite\s+(?:a|an)\s+', writing_body, re.I)
            if len(tasks) != 1:
                return None
            sections = [s for s in structure['sections'] if s.get('type') == '书面表达']
            units = [u for u in confirmed['units'] if u.get('type') == '书面表达']
            if len(sections) != 1 or len(units) != 1:
                return None
            section, unit = sections[0], units[0]
            numbers = unit.get('numbers')
            if (not isinstance(numbers, list) or len(numbers) != 1 or
                    type(numbers[0]) is not int or section.get('questions') != numbers or
                    section.get('source_line') != bi + 1 or section.get('declared_count') != 1 or
                    _norm(section.get('title', '')) != _norm(body[bi]) or
                    unit.get('unit_kind') != 'standalone' or
                    unit.get('source_span', {}).get('number_binding') != 'single_writing_section'):
                return None
            number = numbers[0]
            if sum(number in (u.get('numbers') or []) for u in confirmed['units']) != 1:
                return None
            if any(n == number for n, _, _, _ in body_anchors):
                return None
            # A competing writing section or hint in any other answer source
            # is ambiguous, even if its text happens to match.
            hint = re.compile(rf'^\s*(?:{IMG}\s*思路点拨\s*{IMG}|【思路点拨】)\s*$')
            hint_inline = re.compile(rf'^\s*(?:{IMG}\s*思路点拨\s*{IMG}|【思路点拨】)\s*(\S.*)$')
            essay_heading = re.compile(rf'^\s*(?:{IMG}\s*参考范文\s*{IMG}|【参考范文】)\s*$')
            if sum(bool(hint.match(line) or hint_inline.match(line))
                   for path, lines in source_lines.items()
                   if path in SOURCES and path != '01_source/正文.txt' for line in lines) != 1:
                return None
            if any(_section(line) and _section(line)[1] == '书面表达'
                   for line in source_lines.get('01_source_ans/正文.txt', [])):
                return None
            # Accepted shapes: four numbered original hint lines, or a single
            # original paragraph after 思路点拨. Both must be followed by the
            # explicit example-essay heading, with no other answer/explanation
            # label or embedded question number inside the copied text.
            following = [line for line in appendix[ai + 1:] if line]
            if len(following) < 3:
                return None
            inline = hint_inline.match(following[0])
            if inline:
                paragraph = inline.group(1).strip()
                if (not len(following) >= 2 or not essay_heading.fullmatch(following[1])
                        or not paragraph or next(_inline_numbers(paragraph), None)
                        or re.search(EXPLANATION_LABEL, paragraph)):
                    return None
                return number, (_verbatim(paragraph) if strict else _norm(paragraph))
            if len(following) < 6 or not hint.fullmatch(following[0]):
                return None
            rows = following[1:5]
            for index, row in enumerate(rows, 1):
                prefix = re.match(rf'{index}[.．、]\s*', row)
                if (not prefix or not re.fullmatch(rf'{index}[.．、]\s*[^\d\s].+', row)
                        or next(_inline_numbers(row[prefix.end():]), None)
                        or re.search(EXPLANATION_LABEL, row)):
                    return None
            if not essay_heading.fullmatch(following[5]):
                return None
            return number, (_verbatim(''.join(rows)) if strict else _norm(''.join(rows)))
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, AttributeError):
            return None

    def body_writing_source():
        # Some Word papers keep the printed hint after the writing task while
        # the example essay is extracted into the appendix. Bind the exact hint
        # to the one confirmed writing unit, without requiring both in one file.
        units = [u for u in confirmed_units.values() if u.get('type') == '书面表达']
        if len(units) != 1 or len(units[0].get('numbers', [])) != 1:
            return None
        body = source_lines.get('01_source/正文.txt', [])
        headings = [i for i, line in enumerate(body)
                    if _section(line) and _section(line)[1] == '书面表达']
        if len(headings) != 1:
            return None
        start = headings[0]
        end = next((i for i in range(start + 1, len(body)) if _section(body[i])), len(body))
        hints = []
        for i in range(start + 1, end):
            match = re.match(r'^【思路点拨】\s*(\S.*)$', body[i])
            if match:
                # Multiline hints require separate boundary handling; don't
                # absorb arbitrary task text after the labelled paragraph.
                if any(line.strip() for line in body[i + 1:end]):
                    return None
                hints.append(match.group(1))
        if len(hints) == 1:
            return units[0]['numbers'][0], (_verbatim(hints[0]) if strict else _norm(hints[0]))
        return None

    writing = writing_source() or body_writing_source()

    def has_explanation(item):
        return bool((item.get('explanation') or '').strip()) if isinstance(item.get('explanation'), str) else False

    def writing_hint():
        for lines in source_lines.values():
            for line in lines:
                match = re.match(r'^\s*(?:【思路点拨】|\[思路点拨\])\s*(.*)$', line)
                if match and match.group(1).strip():
                    return _norm(match.group(1))
        return None

    def reference_problem(item, numbers, field, value):
        # Expansion records the actual SHA/coordinates. Content equality alone
        # must never let a reference to another question borrow a valid row.
        from .content_fragments import _ANSWER_PREFIX, _EXPL_LABEL, _SUMMARY_LABEL
        for evidence in item.get('evidence') or []:
            if not isinstance(evidence, str):
                continue
            match = re.fullmatch(r'source_ref (.+):(\d+):(\d+)-(\d+):(\d+) sha256=([0-9a-f]{64})', evidence)
            if not match:
                continue
            path, start, left, end, right, digest = match.groups()
            start, left, end, right = map(int, (start, left, end, right))
            # source_ref columns address the original file, before style/space
            # cleanup. Replaying them against parser lines can silently shift a
            # cross-question reference out of the matching-field ownership check.
            lines = raw_source_lines.get(path, [])
            if not 1 <= start <= end <= len(lines):
                return 'source_ref_coordinate_mismatch'
            text = (lines[start - 1][left:right] if start == end else
                    '\n'.join([lines[start - 1][left:], *lines[start:end - 1], lines[end - 1][:right]]))
            text = (_EXPL_LABEL.sub('', _ANSWER_PREFIX.sub('', text, count=1), count=1)
                    if field == 'explanation' else _SUMMARY_LABEL.sub('', text, count=1))
            if _verbatim(text) != _verbatim(value):
                continue  # This evidence may belong to the other parent field.
            if hashes.get(path) != digest or path in reference_only:
                return 'source_ref_source_mismatch'
            pool = all_questions if field == 'explanation' else all_summaries
            rows = [row for row in pool if row['path'] == path and row['line'] <= start <= end <= row['end_line']
                    and row['verbatim'] == _verbatim(value)
                    and (row['number'] in numbers if field == 'explanation' else
                         row['owner'] in numbers and all(n in row['members'] for n in numbers))
                    and (active_unit.get('identity_version') != 'scoped-v1' or all(
                        scoped_source_matches(active_unit, n, path, start, source_lines)
                        and scoped_source_matches(active_unit, n, path, end, source_lines) for n in numbers))]
            if field == 'explanation' and active_unit.get('identity_version') != 'scoped-v1':
                rows = [row for row in rows if path == '01_source/正文.txt' or any(
                    _same_section(anchor[3], body[3]) for anchor in anchors for body in body_anchors
                    if anchor[0] == body[0] == row['number'] and anchor[1] == path
                    and anchor[2] == row['anchor_line'])]
            rows = [row for row in rows if anchor_problem(
                row.get('number', row.get('owner')), path, row['line'],
                allow_answer_source=field != 'explanation', expected=value)
                in {None, 'conflicting_original_content', 'ambiguous_repeated_anchors', 'body_anchor_not_unique'}]
            if not rows:
                return 'source_ref_ownership_mismatch'
        return None

    def restore_field(item, uid, numbers, field):
        value = item.get(field)
        if not restore or not isinstance(value, str) or AI_MARK in value:
            return
        candidates = []
        if active_unit.get('identity_version') == 'scoped-v1':
            all_rows = all_questions if field == 'explanation' else all_summaries
            local_rows = questions if field == 'explanation' else summaries
            if (any(row['text'] == _norm(value) for row in all_rows)
                    and not any(row['text'] == _norm(value) for row in local_rows)):
                return  # Exact content from another scope is not a copy typo.
        if field == 'explanation':
            candidates = [row for row in questions if row['path'] in SOURCES
                          and row['number'] in numbers and _norm(value) == row['text']
                          and unique_anchor(row['number'], row['path'], row['line'])]
            attachment_proofs = [row for row in attachment_questions if row['number'] in numbers and row['text'] == _norm(value)
                                 and row['verbatim'] == _verbatim(value)]
        elif numbers:
            candidates = [row for row in summaries if row['path'] in SOURCES
                          and row['owner'] in numbers and _norm(value) == row['text']
                          and all(n in row['members'] and unique_anchor(
                              n, row['path'], row['line'], allow_answer_source=True) for n in numbers)
                          and unique_summary(row)]
            attachment_proofs = [row for row in attachment_summaries
                                 if row['owner'] in numbers and row['text'] == _norm(value)
                                 and row['verbatim'] == _verbatim(value)
                                 and all(n in row['members'] for n in numbers)]
        else:
            attachment_proofs = []
        if active_unit.get('identity_version') == 'scoped-v1':
            attachment_proofs = [row for row in attachment_proofs if any(
                scoped_source_matches(active_unit, n, row['path'], row['line'], source_lines) for n in numbers)]
        # A punctuation/width correction is safe only when the candidate is
        # exactly the companion OCR excerpt and that excerpt uniquely matches
        # the authoritative fragment. NFKC alone is never sufficient evidence.
        source = None
        if len(attachment_proofs) == 1:
            # Candidate is the companion OCR excerpt verbatim; prefer the
            # authoritative SOURCES fragment when it exists and differs.
            if len(candidates) == 1 and candidates[0]['source_text']:
                source = candidates[0]
            elif attachment_proofs[0].get('source_text') and value != attachment_proofs[0]['source_text']:
                source = attachment_proofs[0]
        elif not candidates:
            # No authoritative SOURCES row matched, but the cleaned ATTACHMENT
            # fragment may normalize to exactly one row (punctuation / width
            # only). Restore that authoritative fragment verbatim.
            if field == 'explanation':
                pool = [row for row in attachment_questions
                        if row['number'] in numbers and row['text'] == _norm(value)]
            else:
                pool = [row for row in attachment_summaries
                        if row['owner'] in numbers and numbers
                        and all(n in row['members'] for n in numbers)
                        and row['text'] == _norm(value)]
            pool = [row for row in pool if row.get('source_text') and (
                active_unit.get('identity_version') != 'scoped-v1' or any(
                    scoped_source_matches(active_unit, n, row['path'], row['line'], source_lines) for n in numbers))]
            if len(pool) == 1:
                source = pool[0]
        # 单字漏抄/多抄的近似修复：文章大意的**唯一**权威候选（owner 属于该组、
        # 覆盖全部题号的 members、且同名 summary 唯一）与模型文本仅相差极少数
        # 字符（≤2 处增删）时，认定是抄写笔误，用原文 source_text 覆盖；这是"内容
        # 以原文为准"的确定性修复，绝不放过语义改写（差异大则不匹配，仍报错重跑）。
        if source is None and field == 'articleSummary' and numbers and isinstance(value, str):
            uniq = [row for row in summaries if row['path'] in SOURCES and row['owner'] in numbers
                    and all(n in row['members'] and unique_anchor(
                        n, row['path'], row['line'], allow_answer_source=True) for n in numbers)
                    and unique_summary(row)
                    and row.get('source_text')]
            if len(uniq) == 1 and _minor_edit(uniq[0]['source_text'], value):
                source = uniq[0]
        if source is not None and value != source['source_text']:
            item[field] = source['source_text']
            restorations.append({'unit_id': uid, 'field': field,
                                 'path': source['path'], 'line': source['line'],
                                 'source_sha256': hashes[source['path']]})

    def check(item, uid, numbers, child=False, listening=False, has_any_expl=False, unit_type=''):
        if not isinstance(item, dict):
            return
        # 书面表达不是客观题：没有确定答案，范文/写作指导可有可无，只要题目存在
        # 即可。不做答案/解析/大意的逐字来源绑定与“原文有却缺失”判定。
        writing_unit = (str(active_unit.get('type') or unit_type or '').strip() == '书面表达'
                        if active_unit.get('identity_version') == 'scoped-v1' else
                        (str(unit_type or '').strip() == '书面表达'
                         or str(item.get('question_type') or item.get('type') or '').strip() == '书面表达'
                         or any(str(n) in writing_number_set for n in numbers)))
        restore_field(item, uid, numbers, 'explanation')
        restore_field(item, uid, numbers, 'articleSummary')
        summary = item.get('articleSummary', item.get('article_summary', ''))
        explanation = item.get('explanation', '')
        from .content_rules import is_placeholder
        # 明确缺失占位（待补充等）不是可溯源内容，也不是未标注的 AI 生成。
        if summary is None or (isinstance(summary, str) and is_placeholder(summary)):
            summary = ''
        if explanation is None or (isinstance(explanation, str) and is_placeholder(explanation)):
            explanation = ''
        if strict:
            for field, value in (('explanation', explanation), ('articleSummary', summary)):
                if not isinstance(value, str):
                    error('CONTENT_TYPE_INVALID', '解析/大意必须是字符串', uid, field)
                elif AI_MARK in value:
                    error('AI_CONTENT_FORBIDDEN', '禁止 AI 生成解析或文章大意；不得用标记代替原文', uid, field)
            if not writing_unit and not listening and not explanation and (child or not item.get('subquestions')):
                originals = [row for row in questions if row['number'] in numbers and row['text'] and not is_placeholder(row['text'])
                             and unique_anchor(row['number'], row['path'], row['line'], expected=row['verbatim'])]
                if originals:
                    error('ORIGINAL_EXPLANATION_OMITTED', '原题有解析，不能用缺失占位替代', uid, 'explanation',
                          [f"{row['path']}:{row['line']}" for row in originals])
            if not writing_unit and not child and not summary:
                originals = [row for row in summaries if row['owner'] in numbers and numbers and not is_placeholder(row['text']) and all(
                    n in row['members'] and unique_anchor(n, row['path'], row['line'], allow_answer_source=True, expected=row['verbatim'])
                    for n in numbers)]
                if originals:
                    error('ORIGINAL_SUMMARY_OMITTED', '原材料有文章大意，不能用缺失占位替代', uid, 'articleSummary',
                          [f"{row['path']}:{row['line']}" for row in originals])
        if isinstance(summary, str) and summary.strip():
            normalized = _norm(summary)
            marked = AI_MARK in summary
            if marked and (not summary.strip().endswith(AI_MARK) or not _norm(summary.strip()[:-len(AI_MARK)])):
                warning('AI_PROVENANCE_IGNORED', '大意的 AI 来源标记无效；严格校验禁止生成内容', uid, 'articleSummary')
            matches = [row for row in summaries if (_verbatim(summary) == row['verbatim'] if strict else normalized == row['text'])]
            owned = any(row['owner'] in numbers and all(
                            number in row['members'] and unique_anchor(
                                number, row['path'], row['line'], allow_answer_source=True,
                                expected=row['verbatim'])
                            for number in numbers)
                        and unique_summary(row) for row in matches)
            # 允许两种：可逐字溯源到该题组材料，或明确标注为 AI 生成待校对（F3/F5）。
            owned = owned and reference_problem(item, numbers, 'articleSummary', summary) is None
            if not marked and not owned:
                unverified('SUMMARY_SOURCE_UNVERIFIED',
                           '书面表达的文章大意未逐字绑定原文（不阻断）' if writing_unit else
                           '文章大意未能唯一逐字绑定原材料；严格校验拒绝交付',
                           uid, 'articleSummary', numbers, summary, item, warn=writing_unit)
        if isinstance(explanation, str) and explanation.strip():
            value = _norm(explanation)
            marked = AI_MARK in explanation
            if marked and (not explanation.strip().endswith(AI_MARK) or not _norm(explanation.strip()[:-len(AI_MARK)])):
                warning('AI_PROVENANCE_IGNORED', '解析的 AI 来源标记无效；严格校验禁止生成内容', uid, 'explanation')
            traceable = bool(value) and any(row['number'] in numbers and (_verbatim(explanation) == row['verbatim'] if strict else value in row['text'])
                            and unique_anchor(row['number'], row['path'], row['line'], expected=explanation)
                            for row in questions)
            if (writing and not child and not item.get('subquestions')
                    and (active_unit.get('identity_version') != 'scoped-v1' or writing_unit)):
                traceable = traceable or (numbers == [writing[0]] and (_verbatim(explanation) if strict else value) == writing[1])
            # A group overview may quote an explicitly labelled summary; its
            # ownership is checked above and cross-material quotes are flagged.
            if not child and item.get('subquestions'):
                traceable = traceable or any(
                    (_verbatim(explanation) == '【文章大意】' + row['verbatim'] if strict else value == _norm('【文章大意】' + row['text'])) and
                    row['owner'] is not None and
                    (row['owner'] not in numbers or (row['owner'] in numbers and all(
                        number in row['members'] and unique_anchor(number, row['path'], row['line'],
                            expected=row['verbatim'])
                        for number in numbers)))
                    for row in summaries)
            # 书面表达的原始「解析」就是附录的【思路点拨】（写作指导），它必然能逐字
            # 定位到该行，但不是“题目行”。严格模式也要接受这条真实原文解析，否则
            # 书面表达会因无法绑定题目行被判 EXPLANATION_SOURCE_UNVERIFIED 而卡住。
            if not traceable and unit_type == '书面表达':
                hint = writing_hint()
                if hint and value == hint:
                    traceable = True
            traceable = traceable and reference_problem(item, numbers, 'explanation', explanation) is None
            if not marked and not traceable:
                unverified('EXPLANATION_SOURCE_UNVERIFIED',
                           '书面表达的解析/指导未逐字绑定原文（不阻断）' if writing_unit else
                           '解析未能唯一逐字绑定原题；严格校验拒绝交付',
                           uid, 'explanation', numbers, explanation, item, warn=writing_unit)
            for row in summaries:
                if (row['owner'] is not None and row['owner'] not in numbers
                        and row['text'] in value):
                    if writing_unit:
                        warning('EXPLANATION_CROSSES_MATERIAL', '书面表达的解析包含其它材料大意（不阻断）', uid, 'explanation',
                                [f"{row['path']}:{row['line']}"])
                    else:
                        error('EXPLANATION_CROSSES_MATERIAL', f"解析包含属于题 {row['owner']} 的另一材料大意，需按真实材料边界重新装配", uid, 'explanation',
                              [f"{row['path']}:{row['line']}"])
        for index, sub in enumerate(item.get('subquestions') or []):
            if isinstance(sub, dict):
                number = sub.get('number')
                check(sub, f'{uid}/{number if number is not None else index}', [number], True,
                      listening, has_any_expl)

    bound_questions = [row for row in questions if unique_anchor(
        row['number'], row['path'], row['line'], expected=row['verbatim'])]
    bound_summaries = [row for row in summaries if unique_summary(row) and all(
        unique_anchor(n, row['path'], row['line'], allow_answer_source=True)
        for n in row['members'])]
    question_pool, summary_pool = list(all_questions), list(all_summaries)
    legacy_questions, legacy_summaries = list(questions), list(summaries)
    legacy_attachment_questions, legacy_attachment_summaries = list(attachment_questions), list(attachment_summaries)
    for index, item in enumerate(payload.get('items', []) if isinstance(payload, dict) else []):
        if not isinstance(item, dict):
            continue
        uid = str(item.get('unit_id') or item.get('id') or index)
        active_unit = confirmed_units.get(uid) or item
        questions, summaries = legacy_questions, legacy_summaries
        attachment_questions, attachment_summaries = legacy_attachment_questions, legacy_attachment_summaries
        if active_unit.get('identity_version') == 'scoped-v1':
            def owned(row, summary=False):
                members = row.get('members') if summary else [row.get('number')]
                return bool(members) and all(scoped_source_matches(
                    active_unit, n, row['path'], row['line'], source_lines)
                    for n in members if n in active_unit.get('numbers', [])) and any(
                        n in active_unit.get('numbers', []) for n in members)
            scoped_questions = [row for row in question_pool if row['path'] not in reference_only and owned(row)]
            primary = {row['number'] for row in scoped_questions if row['path'] in SOURCES}
            questions = [row for row in scoped_questions if row['path'] in SOURCES or row['number'] not in primary]
            scoped_summaries = [row for row in summary_pool if row['path'] not in reference_only and owned(row, True)
                                and set(row.get('members') or []) == set(active_unit.get('numbers') or [])]
            primary_summary = any(row['path'] in SOURCES for row in scoped_summaries)
            summaries = [row for row in scoped_summaries if row['path'] in SOURCES or not primary_summary]
        numbers = unit_numbers(uid, item)
        if item.get('subquestions'):
            numbers = [sub.get('number') for sub in item['subquestions'] if isinstance(sub, dict)]
        filters = item.get('filterMetadata', item.get('filter_metadata', {}))
        location = item.get('location') or (filters.get('location') if isinstance(filters, dict) else None)
        if metadata.get('region') and location == metadata['region']:
            error('SOURCE_LOCATION_IS_REGION', 'location 仅填写了地理地区；应填写有原卷依据的题号、页码或章节位置，地区另存 filterMetadata.region',
                  uid, 'location', ['01_source/metadata.json'])
        qtype = item.get('question_type') or item.get('type') or ''
        listening = isinstance(qtype, str) and qtype.startswith('听力')
        # 用户要求：缺解析直接留空跳过，不再要求补写（F7 旧规则已按用户要求取消）。
        unit_type = str((confirmed_units.get(str(uid)) or {}).get('type') or '')
        if active_unit.get('identity_version') == 'scoped-v1' and unit_type != '书面表达' and not listening:
            primary_bound = {row['number'] for row in questions if row['path'] in SOURCES}
            unbound = [row for row in question_pool if row['number'] in numbers
                       and (row['path'] in SOURCES or row['number'] not in primary_bound)
                       and row['path'] not in reference_only
                       and not any(scoped_source_matches(other, row['number'], row['path'], row['line'], source_lines)
                                   for other in confirmed_units.values() if row['number'] in (other.get('numbers') or []))]
            unbound_summaries = [row for row in summary_pool if set(row.get('members') or []) == set(numbers)
                                and (row['path'] in SOURCES or not any(r['path'] in SOURCES for r in summaries))
                                and row['path'] not in reference_only
                                and not any(all(scoped_source_matches(other, n, row['path'], row['line'], source_lines)
                                                for n in numbers) for other in confirmed_units.values()
                                            if set(other.get('numbers') or []) == set(numbers))]
            if unbound or unbound_summaries:
                error('CONTENT_SCOPE_UNBOUND', '原解析/大意缺少可核验的大题scope及原文窗口，不能按纯数字题号绑定或宣称缺失',
                      uid, 'explanation', [f"{row['path']}:{row['line']}" for row in unbound + unbound_summaries])
        check(item, uid, numbers, listening=listening, unit_type=unit_type)
    return {'passed': not errors, 'errors': errors, 'warnings': warnings,
            'restorations': restorations, 'source_sha256': hashes,
            'questions': questions, 'summaries': summaries,
            'all_questions': all_questions, 'all_summaries': all_summaries,
            'bound_questions': bound_questions, 'bound_summaries': bound_summaries,
            'check': 'upload-content-source-v1',
            'scope': '解析/大意来源和明确材料归属；不替代逐题语义、视觉、听力和独立审核'}


def collect_source_index(batch_dir):
    """按题号从已确定正文/答案材料抽取解析与大意索引（只读，不调模型）。

    返回 (questions_by_number, summaries)：
      questions_by_number: {题号: 权威原文片段(source_text)}
      summaries: [{owner, members, source_text}]
    同题号/同组存在多条不同原文时不选中，交由调用方回落模型或标记缺失，
    绝不凭猜测挑一条。
    """
    report = _inspect_content(batch_dir, {'items': []}, strict=False)
    confirmed = Path(batch_dir) / '01_source/structure.confirmed.json'
    units = json.loads(confirmed.read_text(encoding='utf-8-sig')).get('units', []) if confirmed.is_file() else []
    if any(unit.get('identity_version') == 'scoped-v1' for unit in units):
        from .question_identity import question_key, validate_units
        if not validate_units(units):
            raise ValueError('Invalid scoped question identities')
        lines = {path: [_source_line(s, anchor_view=path == '01_source/正文.txt') for s in (Path(batch_dir) / path).read_text(encoding='utf-8-sig').splitlines()]
                 for path in report['source_sha256'] if (Path(batch_dir) / path).suffix == '.txt'}
        index, summaries = {}, []
        reference_only = reference_only_sources(batch_dir)
        for unit in units:
            for number in unit['numbers']:
                rows = [row for row in report['all_questions'] if row['number'] == number
                        and row['path'] not in reference_only
                        and scoped_source_matches(unit, number, row['path'], row['line'], lines)]
                primary = [row for row in rows if row['path'] in SOURCES]
                rows = primary or rows
                values = {row['source_text'] for row in rows if row.get('source_text')}
                if len(values) == 1:
                    index[question_key(unit, number)] = values.pop()
            rows = [row for row in report['all_summaries'] if row['path'] not in reference_only
                    and set(row.get('members') or []) == set(unit['numbers'])
                    and all(scoped_source_matches(unit, n, row['path'], row['line'], lines) for n in unit['numbers'])]
            primary = [row for row in rows if row['path'] in SOURCES]
            for row in primary or rows:
                summaries.append(dict(row, unit_id=unit['unit_id'],
                                      question_ids={str(n): question_key(unit, n) for n in unit['numbers']}))
        return index, summaries
    index = {}
    conflicted = set()
    for row in report.get('bound_questions', []):
        if not row.get('source_text'):
            continue
        n = row['number']
        if n in index and _verbatim(index[n]) != _verbatim(row['source_text']):
            conflicted.add(n)
        else:
            index[n] = row['source_text']
    for n in conflicted:
        index.pop(n, None)
    summaries = [{'owner': row.get('owner'), 'members': row.get('members') or [],
                  'source_text': row['source_text']}
                 for row in report.get('bound_summaries', []) if row.get('source_text')]
    return index, summaries


def inspect_content(batch_dir, payload, *, strict=False):
    """只读来源核验；strict 保持原文字符和标点校验，不修改候选。"""
    return _inspect_content(batch_dir, payload, strict=strict)


def restore_source_content(batch_dir, payload):
    """在内存候选中恢复唯一绑定的权威原片段，再严格核验；不写批次。

    全半角/排版等值仅用于寻找同题原片段，不放宽最终逐字校验。
    report.restorations 保留每处恢复的文件、行号和来源指纹。
    无来源、同源异文、跨题或实质改写均不能恢复，也不能通过严格核验。
    """
    return _inspect_content(batch_dir, payload, strict=True, restore=True)
