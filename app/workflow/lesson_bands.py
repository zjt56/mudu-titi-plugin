"""Native-text lesson banners (A/B) only. No images, rendering or models.

The A基础巩固/B能力提升 banners in most worksheets are plain text lines such as
``A 基础巩固`` or ``B能力提升``. Only those printed lines are recognized here;
there is no visual column ownership, page rendering or image semantic analysis.
Image-only banners are simply not recognized and the consumer falls back to its
normal section/heading layout, never blocked for the missing visual evidence.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import re

VERSION = "lesson-text-bands-v1"
ARTIFACT = "01_source/lesson-text-bands.json"
IMG = re.compile(r"\[IMG\s+([^\]]+)\]")


def canonical_label(text):
    text = IMG.sub("", str(text or ""))
    text = re.sub(r"<[^>]+>|\*|\s", "", text).strip("：:[]【】")
    match = re.fullmatch(r"(?:([ABab])[.．、:：]?)?(基础巩固|能力提升)", text)
    if not match:
        return ""
    marker, label = match.groups()
    if marker and marker.upper() != ("A" if label == "基础巩固" else "B"):
        return ""
    return label


def _stop(line):
    from . import sync_practice
    plain = sync_practice.plain_text(line).strip()
    return (sync_practice.is_section_title(plain)
            or bool(sync_practice._LESSON.match(plain) or sync_practice._UNIT.match(plain))
            or bool(re.fullmatch(r"(?:参考答案|答案与解析|参考答案与解析)", plain)))


def _boundaries(text):
    lines = text.splitlines()
    from .sync_practice import answer_key_start
    appendix = answer_key_start(text)
    rows = []
    for index, line in enumerate(lines, 1):
        if appendix and index >= appendix:
            break
        label = canonical_label(line)
        if not label:
            continue
        rows.append({"label": label, "marker": "A" if label == "基础巩固" else "B",
                     "start_line": index, "image_name": None, "image_sha256": None,
                     "evidence": {"method": "native_text", "text": line}})
    for pos, row in enumerate(rows):
        end = rows[pos + 1]["start_line"] - 1 if pos + 1 < len(rows) else (appendix - 1 if appendix else len(lines))
        for cursor in range(row["start_line"], end):
            if _stop(lines[cursor]) or canonical_label(lines[cursor]):
                end = cursor
                break
        row["end_line"] = end
        row["band_id"] = "band-" + hashlib.sha256(
            f"{row['start_line']}:{row['label']}".encode()).hexdigest()[:16]
    return rows


def for_text(root, text):
    """Return native-text bands for the given body view; always safe to call."""
    text = text if isinstance(text, str) else ""
    return {"version": VERSION, "status": "native_text", "bands": _boundaries(text),
            "features": [], "coordinate_basis": "consumer_text"}


def load_verified(root, text=None):
    """Kept for callers that only need the current native-text bands."""
    if text is None:
        try:
            text = (root / "01_source/正文.txt").read_text(encoding="utf-8-sig")
        except OSError:
            return {"version": VERSION, "status": "unavailable", "bands": [], "features": [], "issues": []}
    return for_text(root, text)


def annotate_sections(sections, evidence):
    rows = deepcopy(sections)
    bands = (evidence or {}).get("bands") or []
    for row in rows:
        start = row.get("start_line") or row.get("source_line")
        owners = [b for b in bands if type(start) is int and b["start_line"] <= start <= b["end_line"]]
        if len(owners) == 1:
            band = owners[0]
            row["lesson_band"] = {k: deepcopy(band.get(k)) for k in
                                  ("band_id", "label", "start_line", "end_line", "image_name", "image_sha256")}
        if type(start) is int:
            stops = [b["start_line"] - 1 for b in bands if b["start_line"] > start]
            if owners:
                stops.append(owners[0]["end_line"])
            if stops:
                end = row.get("end_line") or row.get("source_end_line")
                stop = min(stops + ([end] if type(end) is int else []))
                row["lesson_container_end_line"] = stop
                if "start_line" in row:
                    row["end_line"] = stop
                if "source_line" in row:
                    row["source_end_line"] = stop
    return rows


def build(root, *, force=False, identify=None):
    """No-op compatibility shim: native text needs no prebuilt artifact."""
    try:
        text = (root / "01_source/正文.txt").read_text(encoding="utf-8-sig")
    except OSError:
        return {"status": "unavailable", "bands": 0, "model_calls": 0}
    return {"status": "native_text", "bands": len(_boundaries(text)), "model_calls": 0}
