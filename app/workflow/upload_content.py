"""只读核对上传解析/大意的来源；检查归属而不改写原文或伪造语义审核。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import unicodedata

from .agent_tasks import _safe_path

SOURCES = ('01_source/附录.txt', '01_source_ans/正文.txt', '01_source/正文.txt')
AI_MARK = '（AI生成，待校对）'
# IMG identifies a DOCX relationship, never the meaning of an image. Only the
# explicit text between two well-formed markers supplies the label.
IMG = r'\[IMG [^\]\[\r\n]+\]'
EXPLANATION_LABEL = rf'(?:【解析】|\[解析\]|{IMG}\s*解析\s*{IMG})'
QUESTION_PREFIX = r'^\s*(?:[（(]\s*[）)]\s*)?(\d{1,3})[.．、]'
QUESTION = re.compile(rf'{QUESTION_PREFIX}.*?{EXPLANATION_LABEL}')
QUESTION_BOUNDARY = re.compile(QUESTION_PREFIX)
BODY_QUESTION = QUESTION_BOUNDARY
# An explanation paragraph printed without its own question number; it binds to
# the preceding numbered answer line (see the stray-explanation pass below).
STRAY_EXPLANATION = re.compile(rf'^\s*{EXPLANATION_LABEL}')
SUMMARY = re.compile(rf'(?:【文章大意】|\[文章大意\]|{IMG}\s*文章大意\s*{IMG})\s*(.*)')
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
                              'path': relative, 'line': index + 1})
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
                    if not any(row['number'] == number and row['path'] == relative for row in questions):
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
                              'path': relative, 'line': index + 1})
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
                                  'path': relative, 'line': index + 1})

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

    def unit_numbers(uid, item):
        numbers = item.get('numbers') or [item.get('number')]
        if any(isinstance(n, int) for n in numbers):
            return numbers
        unit = confirmed_units.get(str(uid)) or {}
        declared = unit.get('numbers') or []
        return [int(n) for n in declared if str(n).isdigit()] or numbers

    def unique_anchor(number, path, line, *, allow_answer_source=False, expected=None):
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
            pool = [row for row in pool if row.get('source_text')]
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
            if not listening and not explanation and (child or not item.get('subquestions')):
                originals = [row for row in questions if row['number'] in numbers and row['text'] and not is_placeholder(row['text'])
                             and unique_anchor(row['number'], row['path'], row['line'], expected=row['verbatim'])]
                if originals:
                    error('ORIGINAL_EXPLANATION_OMITTED', '原题有解析，不能用缺失占位替代', uid, 'explanation',
                          [f"{row['path']}:{row['line']}" for row in originals])
            if not child and not summary:
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
                (error if strict else warning)('SUMMARY_SOURCE_UNVERIFIED', '文章大意未能唯一逐字绑定原材料；严格校验拒绝交付', uid, 'articleSummary',
                      [f"{row['path']}:{row['line']} -> 题 {row['owner']}" for row in matches])
        if isinstance(explanation, str) and explanation.strip():
            value = _norm(explanation)
            marked = AI_MARK in explanation
            if marked and (not explanation.strip().endswith(AI_MARK) or not _norm(explanation.strip()[:-len(AI_MARK)])):
                warning('AI_PROVENANCE_IGNORED', '解析的 AI 来源标记无效；严格校验禁止生成内容', uid, 'explanation')
            traceable = bool(value) and any(row['number'] in numbers and (_verbatim(explanation) == row['verbatim'] if strict else value in row['text'])
                            and unique_anchor(row['number'], row['path'], row['line'], expected=explanation)
                            for row in questions)
            if writing and not child and not item.get('subquestions'):
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
                (error if strict else warning)('EXPLANATION_SOURCE_UNVERIFIED', '解析未能唯一逐字绑定原题；严格校验拒绝交付', uid, 'explanation')
            for row in summaries:
                if row['owner'] is not None and row['owner'] not in numbers and row['text'] in value:
                    error('EXPLANATION_CROSSES_MATERIAL', f"解析包含属于题 {row['owner']} 的另一材料大意，需按真实材料边界重新装配", uid, 'explanation',
                          [f"{row['path']}:{row['line']}"])
        for index, sub in enumerate(item.get('subquestions') or []):
            if isinstance(sub, dict):
                number = sub.get('number')
                check(sub, f'{uid}/{number if number is not None else index}', [number], True,
                      listening, has_any_expl)

    for index, item in enumerate(payload.get('items', []) if isinstance(payload, dict) else []):
        if not isinstance(item, dict):
            continue
        uid = str(item.get('unit_id') or item.get('id') or index)
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
