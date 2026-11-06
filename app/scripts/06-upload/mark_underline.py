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
from workflow.docx_extract import extract_docx
from workflow.extract import infer_structure

MARK = re.compile(r"<u>(.*?)</u>", re.S)
TAG = re.compile(r"<[^>]*>|\*\*")
# Explicit answer rules have equivalent layouts in DOCX and assembled JSON.
# Keep a token for the blank (never delete it), so an absent/moved blank still
# fails the complete-context comparison. No words or punctuation are folded.
LAYOUT = re.compile(r"<u>[^\S\r\n]+</u>|_{2,}|<[^>]*>|\*\*")


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


def contexts(text):
    """Retain full paragraph context, splitting only explicit labeled options."""
    for paragraph in text.splitlines():
        paragraph = re.sub(r"^\s*(?:[（(]\s*[）)]\s*)?\d{1,3}\s*[.．、)]", "", paragraph)
        # Label dots can be separated from text by inline formatting.
        labels = list(re.finditer(r"(?<![A-Za-z])(?:[A-H])[.．、]", paragraph))
        if not labels:
            yield paragraph
            continue
        if MARK.search(paragraph[:labels[0].start()]):
            yield paragraph[:labels[0].start()]
        for i, label in enumerate(labels):
            yield paragraph[label.end():labels[i + 1].start() if i + 1 < len(labels) else len(paragraph)]


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


def restore(batch, source_text):
    candidate = copy.deepcopy(batch)
    changes, issues = [], []
    targets = list(fields(candidate))
    # Use the exact body/appendix boundary used by formal source extraction.
    # An answer-book example can repeat a prompt verbatim with different marks;
    # it must never supply underlines to the question surface (or fail mapping).
    _, question_text, _ = infer_structure(source_text)
    for context in contexts(question_text):
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
                # A bare marked word must equal an entire field/option; it
                # cannot be searched globally inside arbitrary prose.
                if plain_context == marked_value:
                    if len(marked_value) == 1:
                        continue
                    unlabelled = re.sub(r"^[A-H][.．、]", "", value)
                    if unlabelled != plain_context:
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
                    matches.append((owner, key, path, left, right, inside, overlap))
            evidence = {"source_context": plain_context, "underlined": marked_plain}
            if not matches:
                issues.append(dict(evidence, code="UNDERLINE_MAPPING_MISSING",
                                   message="完整原文上下文对应 0 个位置，未自动改动"))
                continue
            if len(matches) > 1:
                # A unit may legitimately mirror the same passage in several of
                # its own text fields (e.g. stem repeats the opening paragraph of
                # material, and both already carry the mark). Multiple hits are
                # the same logical underline mirrored within one unit, so mark
                # all of them rather than refuse. Hits that land in different
                # units, or at genuinely different positions, stay ambiguous.
                if not _same_unit_mirror(matches):
                    issues.append(dict(evidence, code="UNDERLINE_MAPPING_AMBIGUOUS",
                                       message=f"完整原文上下文对应 {len(matches)} 个位置，未自动改动"))
                    continue
                mirror_overlap = False
                for owner, key, path, left, right, inside, overlap in matches:
                    if overlap:
                        mirror_overlap = True
                    elif not inside:
                        text = owner[key]
                        owner[key] = text[:left] + "<u>" + text[left:right] + "</u>" + text[right:]
                        changes.append(dict(evidence, field="/".join(map(str, path))))
                if mirror_overlap:
                    issues.append(dict(evidence, code="UNDERLINE_OVERLAP", message="已有画线与原卷画线范围冲突"))
                continue
            owner, key, path, left, right, inside, overlap = matches[0]
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
    parser.add_argument("--docx", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        original = args.batch.read_text(encoding="utf-8-sig")
        batch = json.loads(original)
        if not isinstance(batch, dict) or not isinstance(batch.get("items"), list) or not batch["items"]:
            raise ValueError("batch.items 必须为非空数组")
        with tempfile.TemporaryDirectory(prefix="underline-docx-") as directory:
            source = extract_docx(args.docx, Path(directory))
        candidate, report = restore(batch, source["text"])
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
            report["issues"].append({"code": "DOCX_EXTRACTION_UNCERTAIN", "message": source["warnings"],
                                     "blocking": not auto_ocr})
            if not auto_ocr:
                report["passed"] = False
        if report["passed"] and args.apply and report["changes"]:
            # Resolve the exact existing target and refuse source/batch aliasing.
            target = args.batch.resolve(strict=True)
            if args.batch.is_symlink() or target == args.docx.resolve(strict=True):
                raise ValueError("不允许写入软链接或覆盖原卷")
            if target.read_text(encoding="utf-8-sig") != original:
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
