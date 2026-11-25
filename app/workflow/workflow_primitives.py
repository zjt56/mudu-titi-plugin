# -*- coding: utf-8 -*-
"""Shared low-level workflow primitives with no dependency on agent_tasks.

Extracted from agent_tasks.py to break circular imports between agent_tasks and
core workflow modules (batch_fill, manifest, question_draft, review,
upload_content, handoff, etc.). All symbols remain re-exported by agent_tasks
for full backward compatibility.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from pathlib import Path, PurePosixPath

from .question_identity import validate_units


def read_json_safe(path, fallback=None):
    """Read a UTF-8 (with optional BOM) JSON file, returning fallback on missing/corrupt."""
    p = Path(path)
    if not p.is_file():
        return fallback
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError):
        return fallback


def write_json_atomic(path, value, *, indent=2, sort_keys=False) -> Path:
    """Atomically write JSON to path using a same-directory temp file + replace."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=indent, sort_keys=sort_keys) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
        os.replace(tmp_path, target)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return target


def write_json(path, value, *, indent=2) -> None:
    """Non-atomic JSON write: create parents, UTF-8, ensure_ascii=False, trailing newline.

    Byte-identical to the historical per-module helpers (structure_gate/ingest _write_json).
    Prefer write_json_atomic for new files: switching an existing writer to it changes
    file mode (mkstemp 0600) and inode replacement semantics.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=indent) + "\n", encoding="utf-8")


def write_text_atomic(path, text) -> None:
    """Atomically replace a UTF-8 text file via a per-process/thread temp name; the temp is removed on failure."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".{target.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_sha256(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"Unreadable required JSON {Path(path).name}: {exc}") from exc


def _number(value):
    if isinstance(value, bool) or not str(value).isdigit() or int(value) < 1:
        raise ValueError(f"Invalid confirmed question number: {value!r}")
    return str(int(value))


def confirmed_units(batch_dir):
    """Preserve explicit IDs; gate v1 units derive IDs from their full number set."""
    data = _json(Path(batch_dir) / "01_source/structure.confirmed.json")
    rows = data.get("units") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("Confirmed structure requires nonempty units; run structure_gate first")
    units, ids, seen, question_ids = [], set(), set(), set()
    scoped = data.get("identity_version") == "scoped-v1"
    for row in rows:
        if isinstance(row, dict) and scoped:
            row = dict(row, identity_version="scoped-v1")
        if not isinstance(row, dict) or not isinstance(row.get("numbers"), list) or not row["numbers"]:
            raise ValueError("Confirmed unit needs numbers array")
        nums = [_number(n) for n in row["numbers"]]
        kind = row.get("unit_kind")
        if kind not in ("standalone", "group") or (kind == "standalone" and len(nums) != 1):
            raise ValueError("Invalid confirmed unit_kind/numbers")
        if len(set(nums)) != len(nums) or (not scoped and seen.intersection(nums)):
            raise ValueError("Confirmed units have duplicate question numbers")
        if scoped:
            mapping = row.get("question_ids")
            if not isinstance(mapping, dict) or set(mapping) != set(nums):
                raise ValueError("Scoped confirmed units need question_ids for every original question number")
            values = list(mapping.values())
            if (any(not isinstance(qid, str) or not re.fullmatch(r"[\w.-]+", qid) for qid in values)
                    or len(set(values)) != len(values) or question_ids.intersection(values)):
                raise ValueError("Scoped question IDs must be unique safe strings")
            question_ids.update(values)
            if not row.get("unit_id"):
                raise ValueError("Scoped confirmed units need explicit unit_id")
        uid = row.get("unit_id") or row.get("id") or (("q-" if kind == "standalone" else "group-") + "-".join(nums))
        if not isinstance(uid, str) or not re.fullmatch(r"[\w.-]+", uid) or uid in ids:
            raise ValueError("Confirmed unit IDs must be unique safe strings")
        units.append(dict(row, unit_id=uid))
        ids.add(uid)
        seen.update(nums)
    if scoped and not validate_units(units):
        raise ValueError("Invalid scoped confirmed unit identities or section ownership")
    return units


def _safe_path(root, relative):
    if not isinstance(relative, str) or "\\" in relative:
        raise ValueError("Artifact path must be a relative POSIX path")
    bits = PurePosixPath(relative)
    if bits.is_absolute() or ".." in bits.parts or ":" in relative or str(bits) != relative:
        raise ValueError(f"Illegal artifact path: {relative!r}")
    root = Path(root).resolve()
    target = root / relative
    # Reject symlink components even if they currently point back into the batch.
    for component in [target, *target.parents]:
        if component == root:
            break
        if component.is_symlink():
            raise ValueError(f"Symlink is not allowed for artifact/context {relative}")
    if not target.resolve().is_relative_to(root):
        raise ValueError(f"Artifact escapes batch: {relative}")
    if target.exists() and not target.is_file():
        raise ValueError(f"Expected regular file: {relative}")
    return target


def _sanitized(value):
    """Exclude host paths/credentials, never reduce ordinary paper content."""
    if isinstance(value, dict):
        return {k: _sanitized(v) for k, v in value.items()
                if not re.search(r"(?i)(secret|password|token|api[_-]?key|authorization|cookie)", k)}
    if isinstance(value, list):
        return [_sanitized(v) for v in value]
    if isinstance(value, str):
        text = re.sub(r"(?<![\w:/\\])(?:/Users/|/home/|/private/|/tmp/|[A-Za-z]:[\\/])[^\s\"'<>]+", "[host-path-redacted]", value)
        text = re.sub(r"(?i)(?:bearer\s+|(?:api[_-]?key|password|secret|token)\s*[=:]\s*)[^\s\"'<>]+", "[credential-redacted]", text)
        return text
    return value


def _fields(obj, allowed, required=()):
    if not isinstance(obj, dict) or set(obj) - set(allowed) or set(required) - set(obj):
        known = set(obj) if isinstance(obj, dict) else set()
        raise ValueError(f"Invalid fields; allowed={sorted(allowed)}, required={sorted(required)}; "
                         f"absent={sorted(set(required) - known)}, unexpected={sorted(known - set(allowed))}")


def _coverage(payload, units):
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("Artifact must contain items array")
    rows = payload["items"]
    if any(not isinstance(r, dict) or not isinstance(r.get("unit_id"), str) for r in rows):
        raise ValueError("Every item needs a stable unit_id")
    ids = [r["unit_id"] for r in rows]
    expected = {u["unit_id"] for u in units}
    if len(ids) != len(set(ids)) or set(ids) != expected:
        seen, duplicates = set(), []
        for uid in ids:
            if uid in seen and uid not in duplicates:
                duplicates.append(uid)
            seen.add(uid)
        missing = [uid for uid in (u["unit_id"] for u in units) if uid not in seen]
        extra = [uid for uid in dict.fromkeys(ids) if uid not in expected]
        detail = []
        if missing:
            detail.append("缺少单元 %d 个：%s" % (len(missing), "、".join(missing[:12])))
        if extra:
            detail.append("多出单元 %d 个：%s" % (len(extra), "、".join(extra[:12])))
        if duplicates:
            detail.append("重复单元 %d 个：%s" % (len(duplicates), "、".join(duplicates[:12])))
        detail.append("候选 %d 行，确认单元 %d 个，必须逐单元恰好一行" % (len(ids), len(expected)))
        raise ValueError("Artifact must have unique exact confirmed unit coverage: " + "；".join(detail))
    return {r["unit_id"]: r for r in rows}


def _children(row, unit):
    children = row.get("subquestions")
    if not isinstance(children, list):
        raise ValueError("subquestions must be an array")
    numbers = [_number(c.get("number")) if isinstance(c, dict) else "" for c in children]
    expected = [_number(n) for n in unit["numbers"]] if unit["unit_kind"] == "group" else []
    if len(numbers) != len(set(numbers)) or set(numbers) != set(expected):
        raise ValueError("Subquestions must have exact confirmed child number coverage")
    return children


def _answer_comparison_text(value):
    """Compare answer content without extraction/layout-only differences.

    Preserve words, case, digits, negation, signs and internal punctuation. Do
    not use broad Unicode compatibility normalization: it changes exponents.
    """
    text = value.replace('**', '')
    text = re.sub(r'</?(?:u|b|strong|em|i)(?:\s[^>]*)?>', '', text, flags=re.I)
    for tag, encoded, plain in (
        ('sup', '⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ', '0123456789+-=()ni'),
        ('sub', '₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎', '0123456789+-=()'),
    ):
        translation = str.maketrans(encoded, plain)
        text = re.sub(r'<' + tag + r'(?:\s[^>]*)?>\s*(.*?)\s*</' + tag + r'\s*>',
                      lambda match: '<' + tag + '>' + match[1].translate(translation) + '</' + tag + '>', text,
                      flags=re.I | re.S)
        text = re.sub(r'<(?:sup|sub)\b[^>]*>.*?</(?:sup|sub)\s*>|[' + encoded + ']+',
                      lambda match: match[0] if match[0].startswith('<') else
                      '<' + tag + '>' + match[0].translate(translation) + '</' + tag + '>', text,
                      flags=re.I | re.S)
        text = text.replace('</' + tag + '><' + tag + '>', '')
        text = text.replace('<' + tag + '></' + tag + '>', '')
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.I)
    fullwidth = {code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F)}
    text = text.translate(fullwidth).translate(str.maketrans(
        {"‘": "'", "’": "'", "“": '"', "”": '"', "，": ",", "：": ":", "；": ";", "。": "."}))
    text = re.sub(r"\.(?=\s*[\"']?\s*(?:/|【|$))", '', text)
    text = re.sub(r'\s*/\s*', '/', text)
    return " ".join(text.split())
