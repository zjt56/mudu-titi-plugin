#!/usr/bin/env python3
"""Restore DOCX word underlines only through unique, complete source contexts.

Without --apply this is a read-only plan. Ambiguous/missing source spans fail
without changing the batch; numbered/answer-space blanks are left untouched.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workflow.docx_extract import extract_docx, blocking_warnings, layout_hint_warnings
from workflow.extract import infer_structure

MARK = re.compile(r"<u>(.*?)</u>", re.S)
# Inline [IMG ...] tokens from DOCX extraction are promoted to native attachments
# (or stripped when decorative) during draft/wire normalization, so they are
# non-text layout markup just like <br>/<u>/**. Ordinary bracketed source marks
# (e.g. [传统文化], [新考法·…]) never match [IMG ...] and remain part of context.
TAG = re.compile(r"<[^>]*>|\*\*|\[IMG\s+[^\]]+\]")
# Explicit answer rules have equivalent layouts in DOCX and assembled JSON.
# Keep a token for the blank (never delete it), so an absent/moved blank still
# fails the complete-context comparison. No words or punctuation are folded.
LAYOUT = re.compile(r"<u>[^\S\r\n]+</u>|_{2,}|<[^>]*>|\*\*|\[IMG\s+[^\]]+\]")


def extraction_gate(warnings, auto_ocr: bool) -> dict:
    """原生 DOCX 提取告警是否阻断画线恢复。

    「原码位保留、仅缺字体映射」的私用区字形与浮动对象/文本框的版面顺序提示都
    不构成字面改动，不阻断（顺序核对由 layout_review_required 的整页版面复核负责）；
    Symbol 字体/符号字形可能改写字面，仍阻断，除非 OCR 自动复核已留下补充证据。
    """
    blocking = blocking_warnings(warnings)
    hints = layout_hint_warnings(warnings)
    gate = {"code": "DOCX_EXTRACTION_UNCERTAIN", "message": list(warnings or []),
            "blocking": bool(blocking) and not auto_ocr}
    if hints:
        gate["layout_hints"] = hints
        gate["status"] = "layout_review_required"
    return gate


def visible(text, *, normalize_blanks=False):
    """Visible characters and original offsets; optionally normalize answer rules.

    A blank is represented by two underscores, with offsets to its first and
    last characters. This preserves both ends even when a marked sentence spans
    a rule whose original width differs from its comparison token.
    """
    chars, offsets = [], []
    cursor = 0
    for match in (LAYOUT if normalize_blanks else TAG).finditer(text):
        for index in range(cursor, match.start()):
            if not text[index].isspace():
                chars.append(text[index]); offsets.append(index)
        if normalize_blanks:
            token = match.group()
            if token.startswith("<u>") and token.endswith("</u>"):
                chars.extend("__")
                offsets.extend((match.start() + 3, match.end() - 5))
            elif token.startswith("__"):
                chars.extend("__")
                offsets.extend((match.start(), match.end() - 1))
        cursor = match.end()
    for index in range(cursor, len(text)):
        if not text[index].isspace():
            chars.append(text[index]); offsets.append(index)
    return "".join(chars), offsets


def fields(value, path=()):
    """Yield mutable text-field locations; answer/explanation/metadata excluded."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"stem", "material", "text", "content", "value"} and isinstance(child, str):
                yield value, key, path + (key,)
            elif key in {"items", "subquestions", "options", "materialBlocks", "material_blocks", "rows", "cells", "blocks"}:
                if isinstance(child, list) and key == "options":
                    for i, option in enumerate(child):
                        if isinstance(option, str):
                            yield child, i, path + (key, i)
                        elif isinstance(option, dict):
                            yield from fields(option, path + (key, i))
                else:
                    yield from fields(child, path + (key,))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from fields(child, path + (index,))


def contexts(text, *, include_role=False, include_label=False):
    """Retain full paragraph context, splitting only explicit labeled options."""
    def _emit(item, is_option, label_letter=None, siblings=None):
        if include_label:
            return (item, is_option, label_letter, siblings or ())
        return (item, is_option) if include_role else item

    for paragraph in text.splitlines():
        # Word can wrap the printed number (and its choice slot / dot) in separate bold runs,
        # e.g. "**1****.**..." or "**(** **)** **3.**...".
        paragraph = re.sub(
            r"^\s*(?:\*\*)*\s*(?:[（(](?:\s|\*\*)*[）)]\s*(?:\*\*)*\s*)?\d{1,3}(?:\s*\*\*)*\s*(?:[.．](?!\s*(?:\*\*\s*)?\d)|[、)])(?:\s*\*\*)*",
            "", paragraph)
        # Label dots can be separated from text by inline formatting.
        labels = list(re.finditer(r"(?<![A-Za-z])([A-H])[.．、]", paragraph))
        if not labels:
            yield _emit(paragraph, False)
            continue
        if MARK.search(paragraph[:labels[0].start()]):
            prefix = paragraph[:labels[0].start()]
            yield _emit(prefix, False)
        parsed_opts = []
        for i, label in enumerate(labels):
            option = paragraph[label.end():labels[i + 1].start() if i + 1 < len(labels) else len(paragraph)]
            plain_opt, _ = visible(option, normalize_blanks=True)
            parsed_opts.append((option, label.group(1), plain_opt))
        siblings = tuple((lbl, txt) for _, lbl, txt in parsed_opts)
        for option, lbl, _txt in parsed_opts:
            yield _emit(option, True, lbl, siblings)


def _unit_index(path):
    """The owning unit index for an items[N] path, else None."""
    for i, part in enumerate(path):
        if part == "items" and i + 1 < len(path) and isinstance(path[i + 1], int):
            return path[i + 1]
    return None


# A mirror may only live in the unit's own direct passage fields (stem/material)
# or in that unit's own materialBlocks table cells. Subquestions/options are
# separate questions and are never a mirror of the shared passage.
def _is_mirror_field(path):
    uid_index = _unit_index(path)
    if uid_index is None:
        return False
    rest = path[2:]  # after ('items', N)
    if not rest:
        return False
    # Direct stem/material on the unit itself.
    if rest == ("stem",) or rest == ("material",):
        return True
    # Material blocks / table cells owned by the same unit (not subquestions/options).
    if rest[0] in ("materialBlocks", "material_blocks"):
        if "subquestions" in rest or "options" in rest:
            return False
        return True
    return False


def _same_unit_mirror(matches):
    """True only when the hits are the same logical underline mirrored across a
    single unit's own passage fields (stem/material, and optionally its material
    blocks table cells). Strict requirements:
    - exactly one owning unit;
    - every hit sits in a permissible direct passage field (stem/material or that
      unit's material blocks), never a subquestion or option of the unit;
    - each field contributes at most one hit (a field repeated, or the same
      paragraph matched twice inside one field, is NOT a mirror -> ambiguous).
    The shared full paragraph context is guaranteed because every hit was located
    by matching the same plain_context at the same relative (lo, hi) span.
    Anything else (cross-unit, subquestions/options, repeated field) stays
    ambiguous."""
    units = {_unit_index(path) for _, _, path, *_ in matches}
    if len(units) != 1 or None in units:
        return False
    # Every hit must sit in a permissible direct passage field.
    if not all(_is_mirror_field(path) for _, _, path, *_ in matches):
        return False
    # Each field may appear at most once (no repeated occurrence within a field).
    field_keys = [path for _, _, path, *_ in matches]
    if len(field_keys) != len(set(field_keys)):
        return False
    return True


def _exact_paragraph_unit(matches):
    """The single unit whose own passage field is exactly this source paragraph.

    A unit may quote another unit's passage sentence verbatim (a 熟词生义 item
    repeating a cloze line), so the same full context exists in several units
    while only the source paragraph that actually carries the underline owns it.
    The owning field equals the source paragraph in full; quoted copies are only
    substrings of a longer passage and must never be marked. Several exact
    fields, none, or an exact non-passage field (subquestion/option) stay
    ambiguous.
    """
    units = set()
    for _owner, _key, path, _left, _right, _inside, _overlap, exact in matches:
        if not exact:
            continue
        if not _is_mirror_field(path):
            return None
        unit = _unit_index(path)
        if unit is None:
            return None
        units.add(unit)
    if len(units) != 1:
        return None
    return units.pop()


def _sibling_option_match(matches, siblings):
    """Narrow ambiguous option matches when only one option list owns all siblings from the same line."""
    if len(siblings) < 2:
        return None
    sibling_set = set(siblings)
    full_hits = []
    for m in matches:
        owner, _key, path = m[:3]
        if not isinstance(owner, list) or "options" not in path:
            continue
        owner_opts = set()
        for item in owner:
            if not isinstance(item, str):
                continue
            v, _ = visible(item, normalize_blanks=True)
            m_opt = re.match(r"^([A-H])[.．、]", v)
            if m_opt:
                owner_opts.add((m_opt.group(1), v[m_opt.end():]))
        if sibling_set.issubset(owner_opts):
            full_hits.append(m)
    if len(full_hits) == 1:
        return full_hits
    return None


def restore(batch, source_text):
    candidate = copy.deepcopy(batch)
    changes, issues = [], []
    targets = list(fields(candidate))
    # Use the exact body/appendix boundary used by formal source extraction.
    # An answer-book example can repeat a prompt verbatim with different marks;
    # it must never supply underlines to the question surface (or fail mapping).
    _, question_text, _ = infer_structure(source_text)
    for context, option_context, option_label, option_siblings in contexts(question_text, include_role=True, include_label=True):
        plain_context, _ = visible(context, normalize_blanks=True)
        for mark in MARK.finditer(context):
            marked_plain, _ = visible(mark.group(1))
            # Blank runs, numbered blanks and spelling initials are not words.
            if (not re.search(r"[A-Za-z\u4e00-\u9fff]", marked_plain)
                    or re.fullmatch(r"[A-Za-z]", marked_plain) and re.search(r"[\u3000_]{2,}", mark.group(1))):
                continue
            before, _ = visible(context[:mark.start()], normalize_blanks=True)
            marked_value, _ = visible(mark.group(1), normalize_blanks=True)
            lo, hi = len(before), len(before) + len(marked_value)
            matches = []
            for owner, key, path in targets:
                text = owner[key]
                value, offsets = visible(text, normalize_blanks=True)
                # A source option is a complete option, never a substring of an
                # unrelated article (we/weather, put/computer, old/years old).
                # Repeated complete options still compete and remain ambiguous.
                if option_context:
                    if 'options' not in path:
                        continue
                    m_opt = re.match(r'^([A-H])[.．、]', value)
                    unlabelled = value[m_opt.end():] if m_opt else value
                    if unlabelled != plain_context:
                        continue
                    if option_label and m_opt and m_opt.group(1) != option_label:
                        continue
                # A bare marked word must equal an entire field/option; it
                # cannot be searched globally inside arbitrary prose.
                if plain_context == marked_value:
                    if len(marked_value) == 1:
                        continue
                    # A whole-line label (e.g. a table cell "Testing Dates:") is
                    # equal to its own context, so the equality above always
                    # holds. It may still live inside one merged passage field;
                    # uniqueness and mirror checks below decide whether that is
                    # safe. Embedded labels carrying an answer rule stay
                    # refused: they would match arbitrary prose blanks, while an
                    # exact field match is a genuine standalone label.
                    unlabelled = re.sub(r"^[A-H][.．、]", "", value)
                    if unlabelled != plain_context and ("__" in marked_value or len(marked_value) < 3):
                        continue
                # Options may carry their own label, which is outside context.
                for found in re.finditer(re.escape(plain_context), value):
                    start, end = found.start() + lo, found.start() + hi
                    if end > len(offsets) or start == end:
                        continue
                    left, right = offsets[start], offsets[end - 1] + 1
                    covers = [(m.start(), m.end()) for m in MARK.finditer(text)]
                    inside = any(a <= left and right <= b for a, b in covers)
                    # Partial intersections would create overlapping markup.
                    overlap = any(a < right and left < b for a, b in covers) and not inside
                    # Full paragraph context is identical for every hit because
                    # each was located by matching the same plain_context; the mark's
                    # relative position within it is the shared (lo, hi) span.
                    matches.append((owner, key, path, left, right, inside, overlap,
                                    value == plain_context))
            evidence = {"source_context": plain_context, "underlined": marked_plain}
            if not matches:
                issues.append(dict(evidence, code="UNDERLINE_MAPPING_MISSING",
                                   message="完整原文上下文对应 0 个位置，未自动改动"))
                continue
            if len(matches) > 1 and option_context and option_siblings:
                narrowed = _sibling_option_match(matches, option_siblings)
                if narrowed is not None:
                    matches = narrowed
            if len(matches) > 1:
                # A unit may legitimately mirror the same passage in several of
                # its own text fields (e.g. stem repeats the opening paragraph of
                # material, and both already carry the mark). Multiple hits are
                # the same logical underline mirrored within one unit, so mark
                # all of them rather than refuse.
                if _same_unit_mirror(matches):
                    mirror_overlap = False
                    for owner, key, path, left, right, inside, overlap, _exact in matches:
                        if overlap:
                            mirror_overlap = True
                        elif not inside:
                            text = owner[key]
                            owner[key] = text[:left] + "<u>" + text[left:right] + "</u>" + text[right:]
                            changes.append(dict(evidence, field="/".join(map(str, path))))
                    if mirror_overlap:
                        issues.append(dict(evidence, code="UNDERLINE_OVERLAP", message="已有画线与原卷画线范围冲突"))
                    continue
                # Several units may hold the same sentence because one of them
                # quotes the other's passage line (e.g. 熟词生义 repeating a
                # cloze sentence). Only the unit whose own field is exactly this
                # source paragraph owns the mark; quoted copies belong to source
                # lines that carry no underline. Genuinely competing positions
                # (several exact fields, or none) stay ambiguous.
                owned = _exact_paragraph_unit(matches)
                if (owned is None and option_context
                        and all('options' in p and not ovl for _, _, p, _, _, _, ovl, _ in matches)
                        and sum(1 for _, _, _, _, _, ins, _, _ in matches if ins) == 1):
                    # Two questions share the same option label+word (e.g. B. school),
                    # while only one already carries the exact underline from source.
                    continue
                if owned is None:
                    ranked = []
                    for _owner, _key, p, left, right, ins, ovl, exact in matches:
                        score = (2 if exact else 0) + (1 if ins else 0) + (0 if ovl else 1)
                        ranked.append({
                            "field": "/".join(map(str, p)),
                            "unit_index": _unit_index(p),
                            "span": [left, right],
                            "exact_paragraph": bool(exact),
                            "already_underlined": bool(ins),
                            "overlap": bool(ovl),
                            "score": score,
                        })
                    ranked.sort(key=lambda c: (-c["score"], c["field"]))
                    issues.append(dict(evidence, code="UNDERLINE_MAPPING_AMBIGUOUS",
                                       message=f"完整原文上下文对应 {len(matches)} 个位置，未自动改动",
                                       candidates=ranked))
                    continue
                owned_overlap = False
                for owner, key, path, left, right, inside, overlap, _exact in matches:
                    if _unit_index(path) != owned:
                        continue
                    if overlap:
                        owned_overlap = True
                    elif not inside:
                        text = owner[key]
                        owner[key] = text[:left] + "<u>" + text[left:right] + "</u>" + text[right:]
                        changes.append(dict(evidence, field="/".join(map(str, path))))
                if owned_overlap:
                    issues.append(dict(evidence, code="UNDERLINE_OVERLAP", message="已有画线与原卷画线范围冲突"))
                continue
            owner, key, path, left, right, inside, overlap, _exact = matches[0]
            if overlap:
                issues.append(dict(evidence, code="UNDERLINE_OVERLAP", message="已有画线与原卷画线范围冲突"))
            elif not inside:
                text = owner[key]
                owner[key] = text[:left] + "<u>" + text[left:right] + "</u>" + text[right:]
                changes.append(dict(evidence, field="/".join(map(str, path))))
    return candidate, {"passed": not issues, "changes": changes, "issues": issues,
                       "visual_review_claimed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True, type=Path)
    parser.add_argument("--docx", default="")
    parser.add_argument("--out", type=Path, help="独立输出文件；无 DOCX 时保留原批次")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        original = args.batch.read_text(encoding="utf-8-sig")
        batch = json.loads(original)
        if not isinstance(batch, dict) or not isinstance(batch.get("items"), list) or not batch["items"]:
            raise ValueError("batch.items 必须为非空数组")
        docx = Path(args.docx) if args.docx and args.docx.lower().endswith('.docx') else None
        if docx:
            with tempfile.TemporaryDirectory(prefix="underline-docx-") as directory:
                source = extract_docx(docx, Path(directory))
            candidate, report = restore(batch, source["text"])
        else:
            source = {}
            candidate = batch
            report = {"passed": True, "changes": [], "issues": [],
                      "visual_review_claimed": False, "note": "无 DOCX，保留原有画线"}
        # OCR auto 复核已经为浮动对象/符号字体留下补充证据时，原生提取告警
        # 不再阻断画线机械恢复；版面核对仍由 layout_required/上传前复核负责。
        extraction_meta = args.batch.parent.parent / "01_source" / "extraction.json"
        auto_ocr = False
        try:
            data = json.loads(extraction_meta.read_text(encoding="utf-8-sig"))
            auto_ocr = bool(data.get("ocr_auto") and data.get("image_ocr"))
        except (OSError, ValueError):
            auto_ocr = False
        if source.get("warnings"):
            issue = extraction_gate(source["warnings"], auto_ocr)
            report["issues"].append(issue)
            if issue["blocking"]:
                report["passed"] = False
        if report["passed"] and args.apply and (report["changes"] or args.out):
            target_path = args.out or args.batch
            if target_path.is_symlink():
                raise ValueError("不允许写入软链接")
            target = target_path.resolve()
            if docx and target == docx.resolve():
                raise ValueError("不允许覆盖原卷")
            if args.batch.read_text(encoding="utf-8-sig") != original:
                raise ValueError("批次在校验期间发生变化，请重试")
            fd, name = tempfile.mkstemp(prefix=".underline-", suffix=".json", dir=str(target.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(candidate, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.replace(name, target)
        report["applied"] = bool(report["passed"] and args.apply and report["changes"])
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report["passed"] else 1
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({"passed": False, "issues": [{"code": "UNDERLINE_INPUT_ERROR", "message": str(exc)}]}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
