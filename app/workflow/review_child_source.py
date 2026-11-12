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
_IMAGE_ONLY_LINE = re.compile(r'^\[IMG\s+[^\]]+\]$')


def _norm(text):
    return re.sub(r'[\s\u00a0\u3000]+', ' ', text).strip()


def _source_lines(source):
    lines, offset = [], 0
    for raw in source.splitlines(keepends=True):
        lines.append((offset, raw.rstrip('\r\n')))
        offset += len(raw)
    return lines


def _section_end_matches(lines, section, owner):
    """Section end must equal the current parse, or be an explained lesson clip.

    A lesson band ends before the next heading: the coarse heading parser then
    counts the band's trailing image placeholder (and a blank spacer line) as
    part of the previous section, while the confirmed section stops at the band
    boundary. Such a clip is acceptable only when the persisted end is exactly
    the recorded lesson container end and every extra line is provably not
    section content. A clipped content line still fails.
    """
    from .sync_practice import _LESSON, _UNIT, is_section_title, plain_text

    end, parsed = section.get('end_line'), owner.get('end_line')
    if type(end) is not int or type(parsed) is not int:
        return False
    if end == parsed:
        return True
    if not 0 < end < parsed <= len(lines):
        return False
    if type(section.get('lesson_container_end_line')) is not int:
        return False
    if section['lesson_container_end_line'] != end:
        return False
    for text in lines[end:parsed]:
        plain = plain_text(text).strip()
        if not plain or _IMAGE_ONLY_LINE.match(plain):
            continue
        if _LESSON.match(plain) or _UNIT.match(plain) or is_section_title(plain):
            continue
        return False
    return True


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
    if unit.get('identity_version') == 'scoped-v1':
        return _prove_scoped_child(source, unit, number, result)
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


def _scoped_context(source, unit, *, strict_end=True):
    """Rebuild the current section and map verbatim projected stem to raw chars.

    Persisted coordinates alone are not evidence: every section identity field
    and its end must agree with the current independently parsed source.
    ``strict_end=False`` reproduces the older byte-exact end comparison; it
    exists only so already accepted review bindings can be replayed.
    Inline projection is parsing only; returned offsets always index raw text.
    """
    from . import denoise, question_identity
    from .sync_practice import heading_candidates, plain_text

    if not question_identity.validate_units([unit]):
        return None
    span, section = unit.get('source_span'), unit.get('source_section')
    if not isinstance(span, dict) or not isinstance(section, dict):
        return None
    if (span.get('basis', 'cleaned_source') not in ('cleaned_source', 'ai_classified', 'section_source')
            or span.get('path', '01_source/正文.txt') != '01_source/正文.txt'
            or span.get('source_path', '01_source/正文.txt') != '01_source/正文.txt'
            or 'source_sha256' in span):
        return None
    if ('text_sha256' in span and span['text_sha256'] !=
            hashlib.sha256(source.encode('utf-8')).hexdigest()):
        return None
    first, last = span.get('start_line'), span.get('end_line')
    heading, end = section.get('start_line'), section.get('end_line')
    if any(type(n) is not int for n in (first, last, heading, end, section.get('occurrence'))):
        return None
    cleaned, _, removed = denoise.clean_body(source)
    lines = cleaned.splitlines()
    raw_lines = _source_lines(source)
    removed_lines = {row['line_no'] for row in removed}
    mapping = [i for i in range(len(raw_lines)) if i + 1 not in removed_lines]
    if (not 1 <= heading <= first <= last <= end <= len(lines)
            or len(mapping) < len(lines)
            or [raw_lines[i][1] for i in mapping[:len(lines)]] != lines):
        return None
    owners = [row for row in heading_candidates(cleaned) if row['start_line'] == heading]
    if len(owners) != 1:
        return None
    owner = owners[0]
    if (any(section.get(key) != owner.get(key) for key in
            ('title', 'context', 'occurrence', 'start_line'))
            or not (_section_end_matches(lines, section, owner) if strict_end
                    else section.get('end_line') == owner.get('end_line'))):
        return None
    ordinal = re.match(r'^([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫIVX]+[.．、]?)', plain_text(owner['title']).strip())
    if section.get('ordinal') != (ordinal.group(1) if ordinal else ''):
        return None
    if lines[heading - 1] != owner['title']:
        return None
    # Preserve the common identity parser's inline/speaker rules, while mapping
    # every projected piece back to an exact contiguous substring of its line.
    projected = []
    for index in range(heading - 1, end):
        offset, raw = raw_lines[mapping[index]]
        cursor = 0
        for piece in question_identity.numbered_projection([lines[index]]):
            piece = piece.strip()
            if not piece:
                continue
            left = raw.find(piece, cursor)
            if left < 0:
                return None
            right = left + len(piece)
            projected.append((index, offset + left, offset + right, piece))
            cursor = right
    stem = unit.get('stem')
    if not isinstance(stem, str) or not stem.strip():
        return None
    wanted = [line.strip() for line in question_identity.numbered_projection(stem.splitlines())
              if line.strip()]
    values = [row[3] for row in projected]
    matches = [i for i in range(len(values)) if wanted and values[i:i + len(wanted)] == wanted]
    if len(matches) != 1:
        return None
    matched = projected[matches[0]:matches[0] + len(wanted)]
    if not first - 1 <= matched[0][0] <= matched[-1][0] <= last - 1:
        return None
    return {'lines': lines, 'mapping': mapping, 'matched': matched,
            'first': first, 'last': last, 'heading': heading, 'end': end,
            'scope_start': raw_lines[mapping[heading - 1]][0],
            'scope_end': raw_lines[mapping[end - 1]][0] + len(raw_lines[mapping[end - 1]][1])}


def _prove_scoped_child(source, unit, number, result):
    from .question_identity import printed_anchors

    context = _scoped_context(source, unit)
    if context is None:
        return dict(result, reason='scoped 来源窗口或逐字题干未验证')
    # A duplicate printed target anywhere in this section is ambiguous, even if
    # one occurrence happens to match the persisted stem or narrower unit span.
    if _blank_marker(number).search(source[context['scope_start']:context['scope_end']]):
        return _prove_scoped_blank(source, unit, number, result, context)
    anchors = printed_anchors(context['lines'][context['heading'] - 1:context['end']])
    count = sum(str(n) == number for _, n in anchors)
    if count == 0:
        return _prove_scoped_blank(source, unit, number, result, context)
    if count != 1:
        return dict(result, reason='scoped 目标题号缺失或不唯一')
    matched = context['matched']
    starts = [i for i, row in enumerate(matched)
              if any(str(n) == number for _, n in printed_anchors([row[3]]))]
    if len(starts) != 1:
        return dict(result, reason='scoped 目标题号未唯一落在确认题干内')
    first = starts[0]
    last = first
    for index in range(first + 1, len(matched)):
        if printed_anchors([matched[index][3]]) or _SECTION_START.match(matched[index][3]):
            break
        last = index
    start, end = matched[first][1], matched[last][2]
    return dict(result, status='proven', reason='', start=start, end=end,
                text=source[start:end], method='scoped-numbered-stem-characters/v1',
                scope_start=context['scope_start'], scope_end=context['scope_end'])


def _prove_scoped_blank(source, unit, number, result, context):
    """Retain numbered-blank evidence, bounded by the same validated scope."""
    from .question_identity import printed_anchors

    marker = _blank_marker(number)
    scope = source[context['scope_start']:context['scope_end']]
    if (len(list(marker.finditer(scope))) != 1
            or any(str(n) == number for _, n in printed_anchors(marker.sub('', scope).splitlines()))):
        return dict(result, reason='scoped 编号空档缺失或不唯一')
    matched = context['matched']
    hits = [i for i, row in enumerate(matched) if marker.search(row[3])]
    if len(hits) != 1:
        return dict(result, reason='scoped 编号空档未唯一落在确认题干内')
    index = hits[0]
    # A line with several blanks cannot authorize the neighbour's words. Keep
    # this conservative until an independently bounded character split exists.
    any_marker = re.compile(r'<\s*u\s*>[^<]*\d+[^<]*<\s*/\s*u\s*>|__\s*\d+\s*__')
    if len(list(any_marker.finditer(matched[index][3]))) != 1:
        return dict(result, reason='scoped 同行编号空档无法独立归属')
    first = index
    if index > 0:
        previous = matched[index - 1]
        if (previous[0] + 1 == matched[index][0]
                and not printed_anchors([previous[3]])
                and not any_marker.search(previous[3])):
            first = index - 1
    start, end = matched[first][1], matched[index][2]
    return dict(result, status='proven', reason='', start=start, end=end,
                text=source[start:end], method='scoped-numbered-blank-marker/v1',
                scope_start=context['scope_start'], scope_end=context['scope_end'])


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
    proof = prove_child(source, unit, number)
    if (proof['status'] != 'proven' or not isinstance(quote, str) or not quote.strip()
            or not re.search(r'[^\W\d_]', quote, re.UNICODE)):
        return False
    left, right = proof.get('scope_start', 0), proof.get('scope_end', len(source))
    start = source.find(quote, left, right)
    return (start >= proof['start'] and start + len(quote) <= proof['end']
            and source.find(quote, start + 1, right) < 0)


def _loose(text):
    """Whitespace/marker-insensitive form used only to compare spans."""
    value = re.sub(r'<\s*/?\s*u\s*>', ' ', str(text or ''), flags=re.I)
    return re.sub(r'[\s\u00a0\u3000]+', ' ', value).strip()


def quote_belongs_to_child_loose(source, unit, number, quote):
    """Same uniqueness rule as quote_belongs_to_child, tolerating blank markers.

    Word-spelling and listening-table targets are reproduced in the source with
    underline/blank markers that a model legitimately re-quotes without the
    markers; the offsets still have to stay inside the proven child span.
    """
    if not isinstance(quote, str) or not quote.strip():
        return False
    proof = prove_child(source, unit, number)
    if proof['status'] != 'proven':
        return False
    wanted = _loose(quote)
    if len(wanted) < 2 or not re.search(r'[^\W\d_]', wanted, re.UNICODE):
        return False
    if wanted not in _loose(source[proof['start']:proof['end']]):
        return False
    left, right = proof.get('scope_start', 0), proof.get('scope_end', len(source))
    normalized = _loose(source[left:right])
    start = normalized.find(wanted)
    return start >= 0 and normalized.find(wanted, start + 1) < 0


def _raw_unit_lines(source, unit, *, strict_end=True):
    """Map confirmed cleaned-source lines to raw line indices, or fail closed."""
    from . import denoise
    from .extract import material_label

    if isinstance(unit, dict) and unit.get('identity_version') == 'scoped-v1':
        context = (_scoped_context(source, unit, strict_end=strict_end)
                   if isinstance(source, str) else None)
        return ((context['mapping'][context['first'] - 1],
                 context['mapping'][context['last'] - 1]) if context else None)
    span = unit.get('source_span') if isinstance(unit, dict) else None
    if not isinstance(source, str) or not isinstance(span, dict):
        return None
    if (span.get('basis', 'cleaned_source') not in ('cleaned_source', 'ai_classified', 'section_source')
            or span.get('path', '01_source/正文.txt') != '01_source/正文.txt'
            or span.get('source_path', '01_source/正文.txt') != '01_source/正文.txt'):
        return None
    # File-byte hashes cannot be reconstructed reliably from decoded text (BOM,
    # newline translation); do not silently ignore one attached to the span.
    if 'source_sha256' in span:
        return None
    if ('text_sha256' in span and span['text_sha256'] !=
            hashlib.sha256(source.encode('utf-8')).hexdigest()):
        return None
    first, last = span.get('start_line'), span.get('end_line')
    if any(isinstance(v, bool) or not isinstance(v, int) for v in (first, last)):
        return None
    cleaned, _, removed = denoise.clean_body(source)
    raw_lines = source.splitlines(keepends=True)
    removed_lines = {row['line_no'] for row in removed}
    mapping = [i for i in range(len(raw_lines)) if i + 1 not in removed_lines]
    clean_lines = cleaned.splitlines()
    if not 1 <= first <= last <= len(clean_lines) or len(mapping) < len(clean_lines):
        return None
    if [raw_lines[i].rstrip('\r\n') for i in mapping[:len(clean_lines)]] != clean_lines:
        return None
    stem = unit.get('stem')
    if not isinstance(stem, str) or not stem.strip():
        return None
    needle = [line.strip() for line in stem.splitlines() if line.strip() and not material_label(line)]
    indexed = [(i, line.strip()) for i, line in enumerate(clean_lines)
               if line.strip() and not material_label(line)]
    values = [line for _, line in indexed]
    matches = [i for i, value in enumerate(values)
               if needle and value == needle[0] and values[i:i + len(needle)] == needle]
    if len(matches) != 1:
        return None
    start = indexed[matches[0]][0]
    end = indexed[matches[0] + len(needle) - 1][0]
    if not first - 1 <= start <= end <= last - 1:
        return None
    return mapping[first - 1], mapping[last - 1]


def _unit_material_span(source, unit):
    """Character range of a group's material in the raw source text."""
    indices = _raw_unit_lines(source, unit)
    if indices is None:
        return None
    lines = _source_lines(source)
    first, last = indices
    return lines[first][0], lines[last + 1][0] if last + 1 < len(lines) else len(source)


def quote_belongs_to_unit_material(source, unit, quote):
    """A shared passage quote that provably sits inside this group's material.

    Whole-passage groups (阅读理解/完形填空/任务型阅读…) keep the supporting
    sentence in the shared material, outside the child's own stem span. The
    quote is accepted only when it is an exact, unique substring of the source
    and inside the group's confirmed material lines, never another group's.
    """
    if not isinstance(quote, str) or not quote.strip():
        return False
    if not re.search(r'[^\W\d_]', quote, re.UNICODE):
        return False
    if isinstance(unit, dict) and unit.get('identity_version') == 'scoped-v1':
        from .question_identity import printed_anchors
        context = _scoped_context(source, unit) if isinstance(source, str) else None
        if context is None:
            return False
        matched = context['matched']
        # Only the shared prefix is material. Numbered children (including
        # same-line neighbours) cannot re-enter through the material fallback.
        boundary = next((row[1] for row in matched if printed_anchors([row[3]])), None)
        if boundary is None:
            return False
        start = source.find(quote, context['scope_start'], context['scope_end'])
        return (matched[0][1] <= start and start + len(quote) <= boundary
                and source.find(quote, start + 1, context['scope_end']) < 0)
    span = _unit_material_span(source, unit)
    if span is None:
        return False
    start = source.find(quote)
    if start < 0 or source.find(quote, start + 1) >= 0:
        return False
    return span[0] <= start and start + len(quote) <= span[1]
