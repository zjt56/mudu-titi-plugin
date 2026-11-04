"""Conservative, deterministic source spans for sampled child questions.

A span is located by matching the assembled stem line by line against the
bound source, ignoring only whitespace differences (indentation, NBSP). The
match must start at the target's numbered line, be unique in the source, and is
truncated at the next numbered question, so a neighbouring question's text can
never count as evidence for this target. Anything that cannot be bounded this
way stays uncertain. Offsets refer to the decoded text, not page coordinates,
and are not visual verification.
"""
from __future__ import annotations

import hashlib
import re

_QUESTION_START = re.compile(r'^\s*(?:\(\s*\)\s*)?(\d+)\s*[.、]')
_SECTION_START = re.compile(r'^\s*(?:第[一二三四五六七八九十]+部分|[一二三四五六七八九十]+、)')


def _norm(text):
    return re.sub(r'[\s\u00a0\u3000]+', ' ', text).strip()


def _source_lines(source):
    lines, offset = [], 0
    for raw in source.splitlines(keepends=True):
        lines.append((offset, raw.rstrip('\r\n')))
        offset += len(raw)
    return lines


def prove_child(source, unit, number, *, source_sha256=None):
    """Return a source binding or an explicit program-level uncertainty.

    source_sha256, when provided, is the caller's SHA-256 of the actual source
    file bytes; text_sha256 identifies the decoded text used for offsets.
    """
    result = {'status': 'uncertain', 'reason': '', 'source_path': '01_source/正文.txt',
              'source_sha256': source_sha256,
              'text_sha256': hashlib.sha256(source.encode('utf-8')).hexdigest() if isinstance(source, str) else None,
              'unit_id': unit.get('unit_id') if isinstance(unit, dict) else None,
              'number': str(number)}
    def unresolved(reason):
        return dict(result, reason=reason)
    stem = unit.get('stem') if isinstance(unit, dict) else None
    if not isinstance(source, str) or not isinstance(stem, str) or not stem.strip():
        return unresolved('缺少确认单元原文')
    number = str(number)
    if number not in {str(n) for n in unit.get('numbers', [])}:
        return unresolved('目标题号不属于确认单元')
    stem_lines = stem.splitlines()
    starts = []
    for index, line in enumerate(stem_lines):
        match = _QUESTION_START.match(line)
        if match and match.group(1) == number:
            starts.append(index)
    if len(starts) != 1:
        # Numbered blanks such as <u>　66　</u> or __66__ anchor the target
        # without a leading question number (dialogue completion, tables).
        blank = _blank_marker_span(source, unit, number, stem_lines,
                                   source_sha256=source_sha256)
        return blank if blank is not None else unresolved('题号行缺失或不唯一')
    first = starts[0]
    block = [stem_lines[first]]
    for line in stem_lines[first + 1:]:
        match = _QUESTION_START.match(line)
        if match and match.group(1) != number:
            break
        if _SECTION_START.match(line):
            break
        block.append(line)
    while block and not block[-1].strip():
        block.pop()
    norm_block = [_norm(line) for line in block]
    lines = _source_lines(source)
    candidates = []
    for index, (_, line) in enumerate(lines):
        match = _QUESTION_START.match(line)
        if match and match.group(1) == number and _norm(line) == norm_block[0]:
            candidates.append(index)
    if len(candidates) != 1:
        return unresolved('题号行缺失或不唯一')
    start_index = candidates[0]
    cursor = start_index
    for expected in norm_block:
        if cursor >= len(lines) or _norm(lines[cursor][1]) != expected:
            return unresolved('题干与原文行不完全一致')
        cursor += 1
    last_index = cursor - 1
    # Never claim text belonging to a later numbered question inside the block.
    for index in range(start_index + 1, last_index + 1):
        match = _QUESTION_START.match(lines[index][1])
        if match and match.group(1) != number:
            last_index = index - 1
            break
    if last_index < start_index:
        return unresolved('目标题号行内没有可归属的原文')
    start = lines[start_index][0]
    end = lines[last_index][0] + len(lines[last_index][1])
    return dict(result, status='proven', reason='', start=start, end=end,
                text=source[start:end], method='numbered-stem-lines-verbatim/v2')


def _blank_marker(number):
    escaped = re.escape(number)
    return re.compile(r'<\s*u\s*>[^<]{0,20}?(?<!\d)' + escaped + r'(?!\d)[^<]{0,20}?<\s*/\s*u\s*>'
                      r'|__\s*' + escaped + r'\s*__')


def _blank_marker_span(source, unit, number, stem_lines, *, source_sha256=None):
    """Bind a target whose only anchor is a numbered blank inside shared text.

    The blank line (plus the immediately preceding context line when it is not
    itself a numbered question) is the target's own range; a quote outside it,
    including other shared material, is not target evidence.
    """
    marker = _blank_marker(number)
    stem_hits = [index for index, line in enumerate(stem_lines) if marker.search(line)]
    if len(stem_hits) != 1:
        return None
    index = stem_hits[0]
    lines = _source_lines(source)
    anchor = _norm(stem_lines[index])
    candidates = [i for i, (_, line) in enumerate(lines)
                  if marker.search(line) and _norm(line) == anchor]
    if len(candidates) != 1:
        return None
    start_index = candidates[0]
    context_index = start_index
    if index > 0:
        previous = stem_lines[index - 1]
        if (previous.strip() and not _QUESTION_START.match(previous)
                and start_index > 0 and _norm(lines[start_index - 1][1]) == _norm(previous)):
            context_index = start_index - 1
    start = lines[context_index][0]
    end = lines[start_index][0] + len(lines[start_index][1])
    result = {'status': 'proven', 'reason': '', 'source_path': '01_source/正文.txt',
              'source_sha256': source_sha256,
              'text_sha256': hashlib.sha256(source.encode('utf-8')).hexdigest(),
              'unit_id': unit.get('unit_id') if isinstance(unit, dict) else None,
              'number': str(number), 'start': start, 'end': end,
              'text': source[start:end], 'method': 'numbered-blank-marker/v1'}
    return result


def child_span(source, unit, number):
    result = prove_child(source, unit, number)
    return (result['start'], result['end']) if result['status'] == 'proven' else None


def quote_belongs_to_child(source, unit, number, quote):
    """True only for a substantive exact quote uniquely located in child span."""
    span = child_span(source, unit, number)
    if (span is None or not isinstance(quote, str) or not quote.strip()
            or not re.search(r'[^\W\d_]', quote, re.UNICODE)):
        return False
    start = source.find(quote)
    return (start >= span[0] and start + len(quote) <= span[1]
            and source.find(quote, start + 1) < 0)
