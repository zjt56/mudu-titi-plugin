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
SUMMARY = re.compile(rf'(?:【文章大意】|\[文章大意\]|{IMG}\s*文章大意\s*{IMG})\s*(.*)')
ICON_HEADING = re.compile(rf'^\s*{IMG}[^\n]*?{IMG}')
SECTION_NUMBER = re.compile(r'^\s*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫIVX]+|[一二三四五六七八九十]+)[.．、]')
SECTION_NAME = re.compile(r'^\s*(短对话理解|长对话理解|短文理解|信息转换|单项填空|完形填空|补全对话|阅读理解|任务型阅读|单词拼写|书面表达)(?=\s|$|[（(：:])')
# A bare letter, a Chinese passage heading, or an explicit “Passage B”
# marks a boundary; an English sentence beginning with the article “A” does not.
PASSAGE_HEADING = re.compile(
    r'^\s*(?:[（(][A-H][）)]|第[一二三四五六七八九十\d]+篇|[A-H](?:$|篇|[.．、:：]|\s+[\u3400-\u9fff])|(?i:Passage)\s+[A-H](?=$|\s|[.．、:：]))')


def _inline_numbers(text):
    text = re.sub(IMG, ' ', text)
    return re.finditer(r'(?<!\d)\d{1,3}[.．、](?!\d)', text)


def _question(line):
    match = QUESTION.match(line)
    if match and next(_inline_numbers(line[match.end(1):]), None):
        return None  # A second question on this line makes the label ambiguous.
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


def _source_line(value):
    """Remove DOCX inline style markers before recognizing structural boundaries."""
    return re.sub(r'\*+|<[^>]+>', '', value).strip()


def _norm(value):
    value = re.sub(r'</?(?:u|b|strong|em|i|sup|sub)(?:\s[^>]*)?>', '', value, flags=re.I)
    value = re.sub(r'\*{1,3}', '', value)
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value))


def inspect_content(batch_dir, payload):
    """检查已有解释和摘要，不把目录合法或全局文字出现当作归属正确。"""
    batch = Path(batch_dir).resolve()
    errors, hashes, summaries, questions, anchors = [], {}, [], [], []
    body_anchors = []
    source_lines = {}
    for relative in SOURCES:
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
            parts = [line[match.end():]]
            for later in lines[index + 1:]:
                if (QUESTION_BOUNDARY.match(later) or SUMMARY.search(later)
                        or _section(later) or ICON_HEADING.match(later)
                        or PASSAGE_HEADING.match(later)
                        or re.fullmatch(r'\[/?TABLE[^\]]*\]', later)):
                    break
                parts.append(later)
            questions.append({'number': int(match.group(1)), 'text': _norm(''.join(parts)),
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
                        or PASSAGE_HEADING.match(later)
                        or re.fullmatch(r'\[/?TABLE[^\]]*\]', later)):
                    break
                if later.strip():
                    parts.append(later)
            value = _norm(''.join(parts))
            if value:
                members = []
                for later in lines[index + 1:]:
                    if (SUMMARY.search(later) or _section(later) or ICON_HEADING.match(later)
                            or PASSAGE_HEADING.match(later)
                            or re.fullmatch(r'\[/?TABLE[^\]]*\]', later)):
                        break
                    question = QUESTION_BOUNDARY.match(later)
                    if question:
                        if not _question(later):
                            break
                        members.append(int(question.group(1)))
                summaries.append({'text': value, 'owner': owner, 'members': members,
                                  'path': relative, 'line': index + 1})

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

    def unique_anchor(number, path, line):
        if not isinstance(number, int):
            return False
        candidates = body_anchors if path == '01_source/正文.txt' else anchors
        hits = [entry for entry in candidates if entry[0] == number and entry[1] == path]
        if len(hits) != 1:
            return False
        # A separate answer-only file may repeat a number without supplying a
        # competing explanation. A second labelled explanation is ambiguous.
        if any(row['number'] == number and row['path'] != path
               and row['path'] != '01_source/正文.txt' for row in questions):
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
                   if path != '01_source/正文.txt' for line in lines) != 1:
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
                return number, _norm(paragraph)
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
            return number, _norm(''.join(rows))
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, AttributeError):
            return None

    writing = writing_source()

    def has_explanation(item):
        return bool((item.get('explanation') or '').strip()) if isinstance(item.get('explanation'), str) else False

    def check(item, uid, numbers, child=False, listening=False, has_any_expl=False):
        if not isinstance(item, dict):
            return
        summary = item.get('articleSummary', item.get('article_summary', ''))
        explanation = item.get('explanation', '')
        from .content_rules import is_placeholder
        # 明确缺失占位（待补充等）不是可溯源内容，也不是未标注的 AI 生成。
        if is_placeholder(summary):
            summary = ''
        if is_placeholder(explanation):
            explanation = ''
        if isinstance(summary, str) and summary.strip():
            normalized = _norm(summary)
            marked = AI_MARK in summary
            if marked and (not summary.strip().endswith(AI_MARK) or not _norm(summary.strip()[:-len(AI_MARK)])):
                error('AI_PROVENANCE_INVALID', 'AI文章大意须包含实际内容并在末尾标记来源', uid, 'articleSummary')
            matches = [row for row in summaries if normalized == row['text']]
            owned = any(row['owner'] in numbers and all(
                            number in row['members'] and unique_anchor(number, row['path'], row['line'])
                            for number in numbers)
                        and sum(other['owner'] == row['owner'] and other['path'] == row['path']
                                for other in summaries) == 1 for row in matches)
            # 允许两种：可逐字溯源到该题组材料，或明确标注为 AI 生成待校对（F3/F5）。
            if not marked and not owned:
                error('SUMMARY_SOURCE_UNVERIFIED', '文章大意既未匹配到该题组的原始答案/解析来源，也未标注「' + AI_MARK + '」（F3/F5）；不得套用其他篇章大意', uid, 'articleSummary',
                      [f"{row['path']}:{row['line']} -> 题 {row['owner']}" for row in matches])
        if isinstance(explanation, str) and explanation.strip():
            value = _norm(explanation)
            marked = AI_MARK in explanation
            if marked and (not explanation.strip().endswith(AI_MARK) or not _norm(explanation.strip()[:-len(AI_MARK)])):
                error('AI_PROVENANCE_INVALID', 'AI解析须包含实际内容并在末尾标记来源', uid, 'explanation')
            traceable = bool(value) and any(row['number'] in numbers and value in row['text']
                            and unique_anchor(row['number'], row['path'], row['line'])
                            for row in questions)
            if writing and not child and not item.get('subquestions'):
                traceable = traceable or (numbers == [writing[0]] and value == writing[1])
            # A group overview may quote an explicitly labelled summary; its
            # ownership is checked above and cross-material quotes are flagged.
            if not child and item.get('subquestions'):
                traceable = traceable or any(
                    value == _norm('【文章大意】' + row['text']) and
                    row['owner'] is not None and
                    (row['owner'] not in numbers or (row['owner'] in numbers and all(
                        number in row['members'] and unique_anchor(number, row['path'], row['line'])
                        for number in numbers)))
                    for row in summaries)
            if not marked and not traceable:
                error('EXPLANATION_SOURCE_UNVERIFIED', '解析既未能在已提供的答案/解析原文中定位，也未标注「' + AI_MARK + '」（F2/F5）；不能用占位或未标注的生成内容替代', uid, 'explanation')
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
        numbers = item.get('numbers') or [item.get('number')]
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
        check(item, uid, numbers, listening=listening)
    return {'passed': not errors, 'errors': errors, 'source_sha256': hashes,
            'check': 'upload-content-source-v1',
            'scope': '解析/大意来源和明确材料归属；不替代逐题语义、视觉、听力和独立审核'}
