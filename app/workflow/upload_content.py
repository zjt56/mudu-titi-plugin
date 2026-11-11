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


def _source_line(value):
    """Remove DOCX inline style markers before recognizing structural boundaries."""
    return re.sub(r'\*+|<[^>]+>', '', value).strip()


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


def scoped_source_matches(unit, number, path, line, source_lines):
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
    from .question_identity import printed_anchors
    local_anchors = printed_anchors(body[start - 1:end])
    body_hits = [start + index for index, n in local_anchors if n == int(number)]
    if not body_hits:
        # An explicit single-task alias is allowed only for a wholly unnumbered
        # section. It cannot convert an arbitrary missing number into a match.
        if (span.get('number_binding') not in {'unnumbered_section', 'single_writing_section'}
                or number != 1 or unit.get('numbers') != [1] or local_anchors
                or printed_anchors(body[owners[0]['start_line'] - 1:owners[0]['end_line']])
                or any(row.strip() for row in (body[owners[0]['start_line']:start - 1]
                                               + body[end:owners[0]['end_line']]))):
            return False
    if path == body_path:
        left, right = start, end
    else:
        # The companion's single printed 1 cannot choose between A:1 and B:1
        # in the body. Section metadata alone does not prove passage identity.
        section_anchors = printed_anchors(body[owners[0]['start_line'] - 1:owners[0]['end_line']])
        if body_hits and sum(n == int(number) for _, n in section_anchors) != 1:
            return False
        matches = [row for row in heading_candidates('\n'.join(lines))
                   if signature(row) == signature(section)]
        if len(matches) != 1:
            return False
        left, right = matches[0]['start_line'], matches[0]['end_line']
    if not left <= line <= right:
        return False
    anchors = [left + index for index, n in printed_anchors(lines[left - 1:right]) if n == int(number)]
    if not body_hits and path == body_path:
        return not anchors  # Explicit singleton alias; no printed number is asserted.
    return len(anchors) == 1


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
    lines = {path: [_source_line(s) for s in (batch / path).read_text(encoding='utf-8-sig').splitlines()]
             for path in paths if (batch / path).is_file() and path not in reference_only_sources(batch)}
    candidates = []
    for path, rows in lines.items():
        for index, text in enumerate(rows, 1):
            hit = QUESTION_BOUNDARY.match(text)
            if not hit or int(hit.group(1)) != int(number):
                continue
            if path == '01_source/正文.txt' and not re.search(EXPLANATION_LABEL + r'|【答案】', text):
                continue  # A stem/option line is not an independent answer.
            value = re.split(EXPLANATION_LABEL, text[hit.end():], maxsplit=1)[0].strip()
            value = re.sub(r'^【答案】\s*', '', value)
            if scoped_source_matches(unit, number, path, index, lines):
                candidates.append((path, index, value))
    primary = [row for row in candidates if row[0] in SOURCES]
    candidates = primary or candidates
    def cited(path, line):
        stem = Path(path).stem
        aliases = (path, stem) if sum(Path(p).stem == stem for p in lines) == 1 else (path,)
        return any(any(re.search(re.escape(alias) + r'\s*(?::\s*|[Ll]\s*|第\s*)' + str(line) + r'(?!\d)', str(e))
                       for alias in aliases) for e in (evidence or []))
    bound = [row for row in candidates if normalize_answer(row[2]) == normalize_answer(answer) and cited(row[0], row[1])]
    if len({normalize_answer(row[2]) for row in candidates}) > 1:
        bound = []
    if len(bound) != 1:
        raise ValueError(f"{unit.get('unit_id')}/题{number}: scoped answer source is not uniquely bound; "
                         "答案来源缺少可核验的大题scope/原文坐标，禁止按全局题号借用")


def validate_scoped_missing(batch_dir, unit, number, evidence=None):
    """Reject an absence claim only when a real scoped answer is provable."""
    if unit.get('identity_version') != 'scoped-v1' or unit.get('type') == '书面表达':
        return
    batch = Path(batch_dir)
    paths = [*SOURCES, *(p.relative_to(batch).as_posix() for p in (batch / '01_source/materials').glob('attachment_*.txt')
                        if re.fullmatch(r'attachment_\d{3,}\.txt', p.name))]
    source_lines = {path: [_source_line(s) for s in (batch / path).read_text(encoding='utf-8-sig').splitlines()]
                    for path in paths if (batch / path).is_file() and path not in reference_only_sources(batch)}
    confirmed = batch / '01_source/structure.confirmed.json'
    units = json.loads(confirmed.read_text(encoding='utf-8-sig')).get('units', []) if confirmed.is_file() else [unit]
    found, unbound = [], []
    for path in paths:
        file = batch / path
        if not file.is_file() or path in reference_only_sources(batch):
            continue
        for line, raw in enumerate(file.read_text(encoding='utf-8-sig').splitlines(), 1):
            text = _source_line(raw)
            hit = QUESTION_BOUNDARY.match(text)
            if not hit or int(hit.group(1)) != int(number):
                continue
            value = re.split(EXPLANATION_LABEL, text[hit.end():], maxsplit=1)[0].strip()
            value = re.sub(r'^【答案】\s*', '', value)
            from .content_rules import is_placeholder
            if is_placeholder(value):
                continue
            if path != '01_source/正文.txt' and not any(
                    scoped_source_matches(other, number, path, line, source_lines)
                    for other in units if number in (other.get('numbers') or [])):
                unbound.append(f'{path}:{line}')
            try:
                validate_scoped_answer(batch, unit, number, value, [f'{path}:{line}'])
            except ValueError:
                continue
            found.append(f'{path}:{line}')
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
    source_lines = {}
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
        text = raw.decode('utf-8-sig'); lines = [_source_line(line) for line in text.splitlines()]
        source_lines[relative] = lines
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
                if (QUESTION_BOUNDARY.match(later) or SUMMARY.search(later)
                        or _section(later) or ICON_HEADING.match(later)
                        or ANNOTATION.match(later)
                        or PASSAGE_HEADING.match(later)
                        or re.fullmatch(r'\[/?TABLE[^\]]*\]', later)):
                    break
                parts.append(later)
            questions.append({'number': int(match.group(1)), 'text': _norm(''.join(parts)),
                              'verbatim': _verbatim(''.join(parts)), 'source_text': '\n'.join(parts).strip(),
                              'path': relative, 'line': index + 1, 'end_line': index + len(parts)})
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
                if (SUMMARY.search(previous) or _section(previous)
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
                if (QUESTION_BOUNDARY.match(later) or SUMMARY.search(later)
                        or _section(later) or ICON_HEADING.match(later)
                        or ANNOTATION.match(later) or PASSAGE_HEADING.match(later)
                        or STRAY_EXPLANATION.match(later)
                        or re.fullmatch(r'\[/?TABLE[^\]]*\]', later)):
                    break
                parts.append(later)
            questions.append({'number': owner, 'text': _norm(''.join(parts)),
                              'verbatim': _verbatim(''.join(parts)), 'source_text': '\n'.join(parts).strip(),
                              'path': relative, 'line': index + 1, 'end_line': index + len(parts)})
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
                if (SUMMARY.search(later) or _section(later)
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
                    if (SUMMARY.search(later) or _section(later) or ICON_HEADING.match(later)
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
    # Repeated anchors within the authoritative file still fail unique_anchor.
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

    def unique_anchor(number, path, line, *, allow_answer_source=False, expected=None):
        if active_unit.get('identity_version') == 'scoped-v1':
            if not scoped_source_matches(active_unit, number, path, line, source_lines):
                return False
            # Distinct originals inside this scope still conflict. Equivalent
            # extraction copies do not create another question identity.
            competing = [row for row in questions if row['number'] == number
                         and row['path'] not in reference_only
                         and scoped_source_matches(active_unit, number, row['path'], row['line'], source_lines)
                         and (expected is None or row['text'] != _norm(expected))]
            return allow_answer_source or not competing
        if not isinstance(number, int):
            return False
        candidates = body_anchors if path == '01_source/正文.txt' else anchors
        hits = [entry for entry in candidates if entry[0] == number and entry[1] == path]
        if len(hits) != 1:
            return False
        # A separate answer-only file may repeat a number without supplying a
        # competing explanation. A second labelled explanation is ambiguous.
        if not allow_answer_source:
            competing = [row for row in questions
                         if row['number'] == number and row['path'] != path
                         and row['path'] != '01_source/正文.txt'
                         and row['path'] not in reference_only]
            if expected is not None:
                # 同一份卷被重复提取（PDF 附件 / OCR 副本 / materials/*.txt）时，
                # 同文解析的副本不是竞争者；只有措辞不同的解析才算冲突。
                # 副本间常有全/半角标点差异，这里按规范化文本比较（_norm 做 NFKC），
                # 不放松对“措辞被改写”的判定（那仍会因 text 不同而冲突）。
                wanted = _norm(expected)
                competing = [row for row in competing if row['text'] != wanted]
            if competing:
                return False
        # When a separate body is available, the same number must also have a
        # unique body anchor. Repeated numbers need human section verification.
        if path != '01_source/正文.txt':
            body_hits = [entry for entry in body_anchors if entry[0] == number]
            if len(body_hits) != 1:
                return False
            if not _same_section(hits[0][3], body_hits[0][3]):
                return False
        return True

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
                          and sum(other['owner'] == row['owner'] and other['path'] == row['path']
                                  for other in summaries) == 1]
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
                    and sum(other['owner'] == row['owner'] and other['path'] == row['path']
                            for other in summaries) == 1
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
                        and sum(other['owner'] == row['owner'] and other['path'] == row['path']
                                for other in summaries) == 1 for row in matches)
            # 允许两种：可逐字溯源到该题组材料，或明确标注为 AI 生成待校对（F3/F5）。
            if not marked and not owned:
                detail = [f"{row['path']}:{row['line']} -> 题 {row['owner']}" for row in matches]
                if writing_unit:
                    # 书面表达不是客观题：不要求答案/大意逐字绑定，只提示便于核对。
                    warning('SUMMARY_SOURCE_UNVERIFIED', '书面表达的文章大意未逐字绑定原文（不阻断）', uid, 'articleSummary', detail)
                else:
                    (error if strict else warning)('SUMMARY_SOURCE_UNVERIFIED', '文章大意未能唯一逐字绑定原材料；严格校验拒绝交付', uid, 'articleSummary',
                          detail)
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
            if not marked and not traceable:
                if writing_unit:
                    # 书面表达：范文/写作指导可有可无；有内容但不逐字绑定只提示，不阻断。
                    warning('EXPLANATION_SOURCE_UNVERIFIED', '书面表达的解析/指导未逐字绑定原文（不阻断）', uid, 'explanation')
                else:
                    (error if strict else warning)('EXPLANATION_SOURCE_UNVERIFIED', '解析未能唯一逐字绑定原题；严格校验拒绝交付', uid, 'explanation')
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
        lines = {path: [_source_line(s) for s in (Path(batch_dir) / path).read_text(encoding='utf-8-sig').splitlines()]
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
    for row in report.get('questions', []):
        if not row.get('source_text'):
            continue
        n = row['number']
        if n in index and _norm(index[n]) != _norm(row['source_text']):
            conflicted.add(n)
        else:
            index[n] = row['source_text']
    for n in conflicted:
        index.pop(n, None)
    summaries = [{'owner': row.get('owner'), 'members': row.get('members') or [],
                  'source_text': row['source_text']}
                 for row in report.get('summaries', []) if row.get('source_text')]
    return index, summaries


def inspect_content(batch_dir, payload, *, strict=False):
    """只读来源核验；strict 保持原文字符和标点校验，不修改候选。"""
    return _inspect_content(batch_dir, payload, strict=strict)


def restore_source_content(batch_dir, payload):
    """在内存候选中恢复唯一绑定的权威原片段，再严格核验；不写批次。

    全半角/排版等值仅用于寻找同题原片段，不放宽最终逐字校验。
    report.restorations 保留每处恢复的文件、行号和来源指纹。
    无来源、同源重复、跨题或实质改写均不能恢复，也不能通过严格核验。
    """
    return _inspect_content(batch_dir, payload, strict=True, restore=True)
