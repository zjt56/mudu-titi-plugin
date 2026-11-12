"""Read-only, whole-paper structure evidence adapter for the native broker.

Windows only bound reads, never the validation scope. No official artifact is
written here; the ordinary structure classifier owns candidate quarantine.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _extraction_media(batch):
    """Read declarations written by extract, never infer from directory contents."""
    from . import content_media
    path = batch / "01_source/extraction.json"
    if not path.is_file():
        return [], None
    # The original execution path is not the stable media directory. DOCX's
    # name and PDF's preview_name are the exact copy targets in extract.run.
    encoded = content_media._read(batch.resolve(), "01_source/extraction.json")
    extraction = json.loads(encoded.decode("utf-8-sig"))
    if not isinstance(extraction, dict):
        raise ValueError("Invalid extraction media inventory")
    declarations = []
    def declared(name, source):
        if (not isinstance(name, str) or not name or "/" in name or "\\" in name
                or name in (".", "..")):
            raise ValueError("Invalid extraction media name")
        declarations.append({"path": "01_source/media/" + name, "declaration": source})
    for row in extraction.get("media") or []:
        if not isinstance(row, dict):
            raise ValueError("Invalid extraction media record")
        declared(row.get("name"), "extraction.media.name")
    for page in extraction.get("pages") or []:
        if not isinstance(page, dict):
            raise ValueError("Invalid extraction page record")
        for row in page.get("images") or []:
            if not isinstance(row, dict):
                raise ValueError("Invalid extraction page image record")
            if "preview_name" in row:
                declared(row["preview_name"], "extraction.pages.images.preview_name")
    return declarations, hashlib.sha256(encoded).hexdigest()


def prepare(*, batch_dir, raw, candidate, pdf_ocr=None, prompt=None):
    """Freeze all evidence, exposing a directory without claiming confirmation."""
    from . import structure_ai, unit_tasks
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("Structure body must be nonempty")
    if not isinstance(candidate, dict):
        raise ValueError("Structure candidate must be an object")
    batch = Path(batch_dir)
    snapshot = json.loads(_json({"raw": raw, "candidate": candidate, "pdf_ocr": pdf_ocr}))
    directory = []
    for key in ("units", "sections", "groups"):
        for index, row in enumerate(snapshot["candidate"].get(key) or [], 1):
            if isinstance(row, dict):
                directory.append({"directory_id": f"candidate-{key}:{index}",
                                  "candidate_kind": key, "candidate": copy.deepcopy(row)})
    snapshot["directory"] = directory
    snapshot["scope"] = "whole_paper"
    paths = ("01_source/正文.txt", "01_source/structure.json", "01_source/pdf-ocr.json",
             "01_source/source-policy.json", "01_source/lesson-bands.json", "01_source/extraction.json")
    snapshot["file_binding"] = {relative: (hashlib.sha256((batch / relative).read_bytes()).hexdigest()
                                         if (batch / relative).is_file() else None) for relative in paths}
    declarations, extraction_sha = _extraction_media(batch)
    if extraction_sha != snapshot["file_binding"]["01_source/extraction.json"]:
        raise ValueError("Extraction media inventory changed during snapshot")
    from . import content_media
    media_snapshot = content_media.build_snapshot(batch, {"media": declarations}, {})
    media = media_snapshot["media"]
    snapshot["file_binding"].update(media_snapshot["input_hashes"])
    snapshot["media"] = media
    snapshot["snapshot_sha256"] = _sha(_json(snapshot))
    context = {"content_structure": snapshot,
               "confirmed_units_with_stable_ids": [],
               "structure_directory": directory, "media": copy.deepcopy(media)}
    binding = {relative: sha for relative, sha in snapshot["file_binding"].items() if sha is not None}
    # Other evidence fields may themselves contain media-like keys. They must
    # not silently expand the trusted extraction whitelist in the broker.
    broker_media = content_media.build_snapshot(batch, context, binding)
    if broker_media["input_hashes"] != media_snapshot["input_hashes"]:
        raise ValueError("Structure context declares media outside extraction inventory")
    rules = (prompt if prompt is not None else structure_ai._classify_prompt("", {}, None).partition(
        "候选结构仅供参考，不能覆盖原文证据：")[0])
    from . import sync_practice
    if sync_practice.looks_like_sync_practice(raw):
        rules += "\n同步练习必须遵守同源口径：\n" + sync_practice.prompt_block()
    header = ("Stage: structure_gate\nAgent: structure_gate\n" + rules +
              "\n原生取证规则：content_structure 是不可变整卷快照。目录仅是候选，不是全集或已确认单元。"
              "read_source 可读取任意原文行及相邻行、pdf_ocr 完整版面JSON；read_unit 仅辅助定位。"
              "所有窗口使用全卷1基行号，只限制一次读取大小，不能局部交付或局部通过。"
              "必须核对全卷每个大题、全部题号与共享材料，最后一次 submit_candidate 提交整卷 units。"
              "有Word时正文文字权威，PDF仅核对版面，保留服务顺序与原始块，不重排或补写。"
              "失败保留候选与具体行号诊断，不得宣称通过。\n")
    framed = header + unit_tasks._BEGIN + _json(context) + unit_tasks._END
    return {"prompt": framed, "context": context, "input_binding": binding,
            "input_hashes": binding, "snapshot_sha256": snapshot["snapshot_sha256"]}


def _snapshot(prepared):
    context = prepared.get("context", prepared)
    snapshot = context.get("content_structure") if isinstance(context, dict) else None
    if not isinstance(snapshot, dict):
        raise ValueError("Missing whole-paper structure snapshot")
    original = {k: v for k, v in snapshot.items() if k != "snapshot_sha256"}
    if snapshot.get("snapshot_sha256") != _sha(_json(original)):
        raise ValueError("Structure snapshot changed")
    return snapshot


def verify_binding(prepared, batch_dir):
    """Reject any changed, removed, or newly appearing structural evidence."""
    snapshot = _snapshot(prepared)
    batch = Path(batch_dir)
    media_paths = {row["path"] for row in snapshot.get("media", [])}
    for relative, expected in snapshot["file_binding"].items():
        path = batch / relative
        if relative in media_paths and path.is_file():
            from . import content_media
            actual = hashlib.sha256(content_media._read(batch.resolve(), relative)).hexdigest()
        else:
            actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if actual != expected:
            raise ValueError("Structure input changed: " + relative)
    return True


def read_source(prepared, source="body", start_line=1, end_line=None, around=0):
    """Read bounded windows against the full frozen body or PDF JSON evidence."""
    snapshot = _snapshot(prepared)
    if source in ("body", "raw", "01_source/正文.txt"):
        text = snapshot["raw"]
    elif source in ("pdf_ocr", "01_source/pdf-ocr.json"):
        text = json.dumps(snapshot.get("pdf_ocr"), ensure_ascii=False, indent=2)
    elif source in ("candidate", "01_source/structure.json"):
        text = json.dumps(snapshot["candidate"], ensure_ascii=False, indent=2)
    else:
        raise ValueError("Unknown structure source")
    lines = text.splitlines()
    end_line = min(len(lines), start_line + 99) if end_line is None and type(start_line) is int else end_line
    if (type(start_line) is not int or type(end_line) is not int or type(around) is not int
            or start_line < 1 or end_line < start_line or start_line > len(lines)
            or around < 0 or around > 100):
        raise ValueError("Invalid source window")
    lo, hi = max(1, start_line - around), min(len(lines), end_line + around)
    if hi - lo + 1 > 500:
        raise ValueError("Source window exceeds 500 lines; request adjacent windows")
    return {"source": source, "start_line": lo, "end_line": hi, "total_lines": len(lines),
            "lines": [{"line": n, "text": lines[n - 1]} for n in range(lo, hi + 1)],
            "scope": "whole_paper", "snapshot_sha256": snapshot["snapshot_sha256"]}


def read_unit(prepared, unit_id):
    snapshot = _snapshot(prepared)
    matches = [row for row in snapshot["directory"] if row["directory_id"] == unit_id]
    if len(matches) != 1:
        raise ValueError("Unknown candidate directory ID")
    row = copy.deepcopy(matches[0])
    row.update(scope="whole_paper", candidate_only=True)
    return row


def _ordinary_coverage(units, candidate, lines):
    """Use the gate's own source anchors and independent source-bound proof."""
    from . import structure_gate as gate
    from .question_content import _validated_ai_units
    sections = candidate.get("sections") or []
    fresh = gate._build_units(sections, candidate.get("groups") or [], lines) if sections else []
    errors = []
    if _validated_ai_units(units, fresh or units, lines) is None:
        errors.append({"check": "WHOLE_PAPER_BINDING_FAILED",
                       "message": "整卷单元未通过原结构闸门的完整来源归属校验"})
    # Mirror the ordinary gate's explicit writing-points exclusion. Numbered
    #作文内容要点 are not extra paper questions, and section titles are not anchors.
    from .preview import SECTION, _strong_number
    from .extract import _plain
    import re
    excluded = {i for i, line in enumerate(lines) if SECTION.match(_plain(line))}
    for section in sections:
        if "书面表达" not in str(section.get("type")):
            continue
        title = _plain(str(section.get("title") or "")).strip()
        starts = [i for i, line in enumerate(lines) if title and _plain(line).strip() == title]
        if len(starts) != 1:
            continue
        start = starts[0]
        end = next((i for i in range(start + 1, len(lines)) if SECTION.match(_plain(lines[i]))), len(lines))
        first = next((i for i in range(start + 1, end) if _plain(lines[i]).strip()), None)
        if first is None or _strong_number(lines[first]) not in gate._section_numbers(section):
            continue
        if not re.search(r"(?:\bwrite\s+\S.+|请[^\n]*写[^\n]*\S|写一[篇封].+)", _plain(lines[first]), re.I):
            continue
        points = [(i, _strong_number(lines[i])) for i in range(first + 1, end)
                  if _strong_number(lines[i]) is not None]
        if [n for _, n in points] == list(range(1, len(points) + 1)):
            excluded.update(i for i, _ in points)
    anchors = [(at, n) for at, n in gate._scope_anchors(lines) if at not in excluded]
    # The candidate directory may itself omit a question. Check actual source
    # occurrences too, with the gate's shared blank/inline-number normalization.
    for offset, number in anchors:
        owners = [u for u in units if number in u["numbers"] and
                  u["source_span"]["start_line"] <= offset + 1 <= u["source_span"]["end_line"]]
        if len(owners) != 1:
            errors.append({"check": "SOURCE_NUMBERS_UNDECLARED" if not owners else "DUPLICATE_NUMBERS",
                           "source_line": offset + 1, "number": number,
                           "message": f"原文题号{number}须唯一归属，当前{len(owners)}个单元"})
    if not fresh and not gate._scope_anchors(lines):
        errors.append({"check": "WHOLE_PAPER_COVERAGE_UNPROVEN",
                       "message": "无可独立复验的全卷目录或原文题号，不能确认整卷覆盖"})
    return errors


def validate(prepared, response):
    """Validate original candidates; never convert a partial result into success."""
    from . import structure_ai, sync_practice
    snapshot = _snapshot(prepared)
    reply = response.get("response", response) if isinstance(response, dict) else response
    artifacts = reply.get("artifacts") if isinstance(reply, dict) else None
    structured = artifacts.get("structure") if isinstance(artifacts, dict) else reply
    diagnostics, quarantine = [], []
    if isinstance(reply, dict) and "status" in reply and reply["status"] != "completed":
        diagnostics.append({"check": "RESPONSE_INCOMPLETE", "message": "结构响应未完成"})
    units = structure_ai._sanitize_classified(structured, snapshot["raw"], snapshot["candidate"],
                                              diagnostics=diagnostics, quarantine=quarantine)
    if units and not sync_practice.looks_like_sync_practice(snapshot["raw"]):
        diagnostics.extend(_ordinary_coverage(units, snapshot["candidate"], snapshot["raw"].splitlines()))
    passed = bool(units) and not diagnostics
    return {"passed": passed, "structured": copy.deepcopy(structured),
            "units": units if passed else [], "quarantine_units": [] if passed else quarantine,
            "diagnostics": diagnostics, "retryable": False,
            "failure_class": "" if passed else "source_binding"}
