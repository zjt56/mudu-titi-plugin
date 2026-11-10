"""Hash-bound, read-only source slices for explanation-stage input values.

References are a copying convenience, never a proof of question ownership. The
caller must run the usual content/ownership validation after expansion.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from .content_labels import (REFERENCE_EXPLANATION_LABEL, REFERENCE_SUMMARY_LABEL,
                             REFERENCE_FIELD_LABEL)

FORMAT = 'source-fragment/v1'
_REQUIRED = frozenset(('path', 'sha256', 'start_line', 'end_line'))
_OPTIONAL = frozenset(('start_column', 'end_column'))
_EXPL_LABEL = re.compile(rf'^\s*{REFERENCE_EXPLANATION_LABEL}\s*[:：]?\s*')
_SUMMARY_LABEL = re.compile(rf'^\s*{REFERENCE_SUMMARY_LABEL}\s*[:：]?\s*')
_ANSWER_PREFIX = re.compile(
    rf'^\s*\d{{1,3}}\s*[.．、]\s*[A-H]?\s*(?={REFERENCE_EXPLANATION_LABEL})')
# A field label such as 【解析】/【答案】/【文章大意】. Models sometimes set
# start_column a character or two into this label, slicing off part of it
# (e.g. "析】"). Snap such a start to the body text right after the closing 】.
_FIELD_LABEL = re.compile(REFERENCE_FIELD_LABEL)


def _snap_label_start(first, left):
    """Move a start column that lands inside a field label to the label end."""
    label = _FIELD_LABEL.search(first)
    if label is None:
        return left
    begin, end = label.span()
    if begin < left < end:
        return end
    return left


# Sentence/clause boundaries: a short quote that ends here is intentional.
_BOUNDARY_CHARS = frozenset('。．！？!?；;，,、.…—）)」』”"\' \u3000\t')


def _clean_boundary(line, right):
    """True when the character just before ``right`` ends a clause/sentence.

    A short quote that stops right after a boundary is intentional; stopping mid
    word (e.g. after 知, before a comma) means the width was miscounted.
    """
    if right >= len(line):
        return True
    return line[right - 1:right] in _BOUNDARY_CHARS


def _allowed_path(relative):
    from .upload_content import SOURCES
    return isinstance(relative, str) and (
        relative in SOURCES or re.fullmatch(r'01_source/materials/attachment_\d{3,}\.txt', relative) is not None)


def _read_source(batch, relative):
    from .upload_content import reference_only_sources
    if not _allowed_path(relative):
        raise ValueError('Source fragment path is not an allowed content source')
    if relative in reference_only_sources(batch):
        raise ValueError('Source fragment cannot use a reference-only content source')
    # Check the original path before resolve(), including parent-directory links.
    path = batch / relative
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError('Source fragment paths must not contain symlinks')
    if not path.is_file() or not path.resolve().is_relative_to(batch.resolve()):
        raise ValueError('Source fragment must name an existing file inside the batch')
    try:
        raw = path.read_bytes()
        lines = raw.decode('utf-8-sig').splitlines(keepends=True)
    except (OSError, UnicodeError) as exc:
        raise ValueError('Source fragment source is not readable UTF-8 text') from exc
    return hashlib.sha256(raw).hexdigest(), lines


def source_index(batch):
    """Return allowed authoritative sources, raw-file hashes and numbered lines.

    Text excludes each line terminator and a UTF-8 BOM. No content is truncated;
    consumers can choose which source entries to include in their task context.
    """
    from .upload_content import SOURCES, reference_only_sources
    batch = Path(batch).resolve()
    materials = batch / '01_source/materials'
    paths = list(SOURCES)
    if materials.is_dir():
        paths.extend(f'01_source/materials/{path.name}' for path in sorted(materials.glob('attachment_*.txt'))
                     if re.fullmatch(r'attachment_\d{3,}\.txt', path.name))
    reference_only = reference_only_sources(batch)
    sources = []
    for relative in paths:
        if relative in reference_only or not (batch / relative).exists():
            continue
        digest, lines = _read_source(batch, relative)
        sources.append({'path': relative, 'sha256': digest,
                        'lines': [{'number': number, 'text': line.rstrip('\r\n')}
                                  for number, line in enumerate(lines, 1)]})
    return {'format': FORMAT, 'sources': sources}


def expand_references(rows, batch):
    """Expand reference objects in place; return whether any were present.

    Only explanation/articleSummary on rows and explanation on subquestions are
    supported. Resolve all references before applying any replacement. Existing
    strings and malformed non-object values are left for the existing validator.
    """
    batch = Path(batch).resolve()
    rows = list(rows)
    cache, replacements = {}, []

    def resolve(value, field):
        if set(value) != {'source_ref'} or not isinstance(value['source_ref'], dict):
            raise ValueError('Content reference must be an object containing only source_ref')
        ref = value['source_ref']
        if not _REQUIRED <= set(ref) or set(ref) - (_REQUIRED | _OPTIONAL):
            raise ValueError('Invalid source_ref fields')
        relative = ref['path']
        if not _allowed_path(relative):
            raise ValueError('Source fragment path is not an allowed content source')
        digest = ref['sha256']
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ValueError('source_ref.sha256 must be a lowercase full-file SHA256')
        if relative not in cache:
            cache[relative] = _read_source(batch, relative)
        actual, lines = cache[relative]
        if actual != digest:
            raise ValueError('Source fragment hash is stale or forged')
        start, end = ref['start_line'], ref['end_line']
        if (type(start) is not int or type(end) is not int
                or not 1 <= start <= end <= len(lines)):
            raise ValueError('Source fragment line range is out of bounds')
        first = lines[start - 1].rstrip('\r\n')
        last = lines[end - 1].rstrip('\r\n')
        left, right = ref.get('start_column', 0), ref.get('end_column', len(last))
        if type(left) is not int or type(right) is not int or left < 0 or left > len(first) or right < 0:
            raise ValueError('Source fragment column range is out of bounds')
        # Models frequently report display-column widths (CJK glyphs as two
        # columns) or an inclusive end. A right edge beyond the physical line
        # can only mean "through the end of this line"; clamping preserves the
        # exact source text and avoids discarding otherwise valid references.
        if right > len(last):
            right = len(last)
        # A start column inside a field label (【解析】/【答案】/【文章大意】)
        # usually means "start at the body", since the label is not part of the
        # body. Snap to the label end; do not touch starts already after it.
        if start == end and left < right:
            snapped = _snap_label_start(first, left)
            if snapped != left:
                left = snapped
        # An end column that stops mid-text (not on a sentence/clause boundary)
        # is a miscounted width, not an intentional short quote: the fragment
        # would be cut mid-sentence. Extend only then to the physical line end.
        if start == end and right < len(last) and left < right and not _clean_boundary(last, right):
            right = len(last)
        if start == end and left > right:
            raise ValueError('Source fragment column range is out of bounds')
        if start == end:
            text = first[left:right]
        else:
            # Preserve internal CRLF/LF line endings and all source whitespace.
            text = lines[start - 1][left:] + ''.join(lines[start:end - 1]) + last[:right]
        # Source refs often start at the field label (【解析】/【答案】) or
        # include 【文章大意】. The field contract stores only the body text;
        # strip a leading label deterministically rather than failing the
        # entire round on a fixed, non-semantic prefix.
        if field == 'explanation':
            # A missing start_column may include the printed answer number/letter
            # prefix; it is structural, not part of the explanation body.
            text = _ANSWER_PREFIX.sub('', text, count=1)
            text = _EXPL_LABEL.sub('', text, count=1)
        elif field == 'articleSummary':
            text = _SUMMARY_LABEL.sub('', text, count=1)
        if not text.strip():
            raise ValueError('Source fragment must not be empty')
        evidence = (f"source_ref {relative}:{start}:{left}-{end}:{right} "
                    f"sha256={digest}")
        return text, evidence

    def visit(obj, fields, row):
        if not isinstance(obj, dict):
            return
        for field in fields:
            if isinstance(obj.get(field), dict):
                text, evidence = resolve(obj[field], field)
                replacements.append((obj, field, text, evidence, row))

    for row in rows:
        if not isinstance(row, dict):
            continue
        visit(row, ('explanation', 'articleSummary'), row)
        children = row.get('subquestions')
        if isinstance(children, list):
            for child in children:
                visit(child, ('explanation',), row)
    if replacements:
        from .explanations_direct import DIRECT_COPY_EVIDENCE
        if any(isinstance(row.get('evidence'), list) and DIRECT_COPY_EVIDENCE in row['evidence']
               for row in rows):
            raise ValueError('Source references cannot claim deterministic direct-copy evidence')
    for obj, field, text, evidence, row in replacements:
        obj[field] = text
        # Do not fix malformed/missing evidence: the normal validator rejects it.
        for owner in (row,) if row is obj else (row, obj):
            if (isinstance(owner.get('evidence'), list) and owner['evidence']
                    and evidence not in owner['evidence']):
                owner['evidence'].append(evidence)
    return bool(replacements)
