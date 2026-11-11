"""Read-only tooltip projection. Evidence is observed, never executed or repaired.

A successful parent delivery proves its responsibilities, not optional work (OCR)
actually ran. Pending/rebuilt parents never inherit completion from leftover files.
Reports may locate individual outcomes only after a stage has actually started.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .checks import CHECKS
from .contracts import STAGES

# Stable ids belong to the tooltip API, not to the scheduler's stage contract.
STEPS = {
    "extract": (("source", "主卷提取"), ("answers", "答案材料准备"),
                ("media", "图片表格提取"), ("ocr", "OCR辅助"),
                ("header", "卷头识别"), ("provenance", "来源登记")),
    "structure_gate": (("denoise", "去噪"), ("separation", "答案解析分离"),
                       ("coverage", "题号覆盖"), ("grouping", "切题分组"),
                       ("materials", "共享材料"), ("acceptance", "结构验收")),
    "preassemble": (("source_objects", "来源对象"), ("types", "题型子类"), ("completeness", "完整性")),
    "types": (("handoff", "题型交接"),),
    "audio": (("transcription", "转写"), ("segments", "切段"), ("mapping", "映射"), ("evidence", "证据验收")),
    "answers": (("matching", "原答案匹配"), ("format", "格式"), ("missing", "缺失"), ("delivery", "交付")),
    "explanations": (("copy", "解析直抄"), ("summary", "大意"), ("source", "来源"),
                     ("missing", "缺失"), ("delivery", "交付")),
    "knowledge": (("knowledge", "知识点"), ("catalog", "知识点目录"),
                  ("coverage", "覆盖"), ("delivery", "交付")),
    "assemble": (("merge", "合并"), ("fidelity", "保真")),
    "meta": (("metadata", "元数据整理"),),
    "underlines": (("underlines", "画线补全"),),
    "validate": tuple((Path(c.script).stem, c.name) for c in CHECKS)
                + (("source_audit", "原卷逐题覆盖与材料归属 source_audit"), ("local_preflight", "本地上传预检 local preflight")),
    "review": (("labels", "第一轮标签审核"), ("sample", "第二轮抽检"), ("coverage", "审核覆盖")),
    "review_gate": (("differences", "差异归集"), ("conclusion", "执行质量结论")),
    "format_gate": (("fields", "字段"), ("media", "媒体"), ("serialization", "序列化"),
                    ("consistency", "一致性"), ("freeze", "固化")),
}
OK = {"completed", "approved"}
FAIL = {"failed", "blocked"}


def _object(value):
    return value if isinstance(value, dict) else {}


def _rows(value):
    return value if isinstance(value, list) else []


def _time(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def _report(root, relative, record):
    if root is None:
        return {}
    path = root / relative
    try:
        # No traversal through symlinked report directories, including root.
        if root.is_symlink() or any((root / parent).is_symlink() for parent in (Path(relative), *Path(relative).parents)):
            return {}
        stat = path.stat()
        if stat.st_size > 4 * 1024 * 1024:
            return {}  # Display must stay cheap; oversized evidence remains unknown.
        started = _time(record.get("started_at"))
        if started is not None and stat.st_mtime < started:
            return {}
        return _object(json.loads(path.read_text(encoding="utf-8-sig")))
    except (OSError, ValueError, TypeError, RecursionError):
        return {}


def _outcome(value):
    if isinstance(value, dict):
        if value.get("skipped") is True or value.get("status") == "skipped":
            return "skipped"
        if value.get("passed") is True or value.get("completed") is True:
            return "completed"
        if value.get("passed") is False:
            return "failed"
        value = value.get("status")
    return {"passed": "completed", "completed": "completed", "approved": "completed",
            "failed": "failed", "blocked": "failed", "skipped": "skipped",
            "warning": "completed", "placeholder": "completed"}.get(str(value))


def _details(sid, report, read):
    """Only explicit report outcomes; absence is never a pass."""
    result = {}
    if sid == "validate":
        for row in _rows(report.get("checks")):
            if not isinstance(row, dict):
                continue
            for key, title in STEPS[sid]:
                if row.get("check") == title or (row.get("script") and Path(str(row["script"])).stem == key):
                    outcome = _outcome(row)
                    check = next((c for c in CHECKS if Path(c.script).stem == key), None)
                    result[key] = ("warning" if outcome == "failed" and
                                   (row.get("blocking") is False or (check is not None and not check.blocking))
                                   else outcome)
        for row in _rows(report.get("skipped")):
            if isinstance(row, dict):
                for key, title in STEPS[sid]:
                    if row.get("check") == title:
                        result[key] = "skipped"
        result.setdefault("source_audit", _outcome(report.get("source_audit")))
        result["local_preflight"] = _outcome(report.get("upload_local_preflight") or read("07_upload/local-preflight.json"))
    elif sid == "structure_gate":
        checks = _object(report.get("checks"))
        mapping = {"denoise": ("denoise",), "coverage": ("coverage", "source_number_coverage"),
                   "grouping": ("grouping",), "materials": ("source_content", "group_guides")}
        for key, names in mapping.items():
            outcomes = [_outcome(checks.get(n)) for n in names]
            if "failed" in outcomes:
                result[key] = "failed"
            elif outcomes and all(s == "completed" for s in outcomes):
                result[key] = "completed"
        result["acceptance"] = _outcome(report)
    elif sid == "extract":
        result["source"] = "completed" if report.get("passed") is True else None
        roles = _object(report.get("material_roles"))
        for row in _rows(roles.get("documents")):
            if isinstance(row, dict) and row.get("input") == "answers":
                result["answers"] = "failed" if row.get("error") else _outcome(row)
        extraction = read("01_source/extraction.json")
        pdf = _object(report.get("pdf_ocr")) or read("01_source/pdf-ocr.json")
        if pdf.get("status") == "failed":
            result["ocr"] = "failed"
        # A PDF acquisition can use native text only: it does not prove OCR ran.
        if (extraction.get("image_ocr") or any(isinstance(p, dict) and p.get("method") == "ocr"
                                               for p in _rows(extraction.get("pages")))):
            result["ocr"] = "completed"
        elif extraction.get("ocr_assist_enabled") is False:
            result["ocr"] = "skipped"
        elif extraction and not extraction.get("ocr_auto") and not pdf.get("sources"):
            result["ocr"] = "skipped"
    elif sid == "review":
        for row in _rows(report.get("rounds")):
            if isinstance(row, dict) and row.get("round_id") in {"labels", "sample"}:
                result[row["round_id"]] = "completed" if row.get("completed") is True else "pending"
        for rid in _object(report.get("execution_failures")):
            if rid in {"labels", "sample"}:
                result[rid] = "failed"
        if report.get("execution_completed") is True:
            result["coverage"] = "completed"
    elif sid == "review_gate":
        if report.get("execution_completed") is True or _object(report.get("rounds")).get("execution_completed") is True:
            result["differences"] = "completed"
            result["conclusion"] = "needs_review" if report.get("passed") is False else _outcome(report)
    elif sid == "format_gate":
        for issue in _rows(report.get("errors")):
            if not isinstance(issue, dict):
                continue
            code = str(issue.get("code") or "")
            key = ("consistency" if code in {"FINAL_BATCH_CHANGED", "LOCAL_ATTACHMENT_CHANGED"}
                   else "media" if "ATTACHMENT" in code or "MEDIA" in code
                   else "serialization" if code == "FORMAT_INPUT_INVALID"
                   else "freeze" if code == "FORMAT_REPORT_WRITE_FAILED"
                   else "fields")
            result[key] = "failed"
        # Passed format does not certify the later source-freeze hook.
        if report.get("passed") is True:
            result.update({key: "completed" for key in ("fields", "media", "serialization", "consistency")})
    return result


REPORTS = {"extract": "07_review/extraction-report.json", "structure_gate": "07_review/structure-gate.json",
           "validate": "07_review/validation-report.json", "review": "07_review/overall-review-report.json",
           "review_gate": "07_review/review-gate.json", "format_gate": "07_review/upload-format.json"}

# Specific progress terms only; generic stage titles must not mark all children running.
TERMS = {
    "extract": {"source": ("主卷提取",), "answers": ("答案材料", "答案卷准备"), "media": ("图片表格",),
                "ocr": ("ocr",), "header": ("卷头",), "provenance": ("来源登记",)},
    "structure_gate": {"denoise": ("去噪",), "separation": ("答案解析分离",), "coverage": ("题号覆盖",),
                       "grouping": ("切题分组",), "materials": ("共享材料",)},
    "audio": {"transcription": ("transcrib", "转写", "asr", "align"), "segments": ("cut", "segment", "切段", "切片"),
              "mapping": ("映射", "mapping"), "evidence": ("证据验收",)},
    "review": {"labels": ("labels", "一审", "第一轮"), "sample": ("sample", "二审", "第二轮"),
               "coverage": ("审核覆盖",)},
}


def project(manifest, batch_dir=None, activity=None, audio_state=None):
    """Return fresh ``{stage_id: [{id, title, status}]}``, with no side effects.

    ``activity`` must already be process-liveness checked. ``audio_state`` must be
    scoped to this batch and checked for a live worker by its caller.
    """
    manifest = _object(manifest)
    stages, inputs = _object(manifest.get("stages")), _object(manifest.get("inputs"))
    root = Path(batch_dir) if batch_dir is not None else None
    activity, audio_state = _object(activity), _object(audio_state)
    result = {}
    for stage in STAGES:
        sid = stage.id
        record = _object(stages.get(sid))
        status = str(record.get("status") or "pending")
        invalid = bool(record.get("rebuild_required")) or status == "invalidated"
        good = status in OK and not invalid
        specs = STEPS.get(sid, ((sid, stage.title),))
        rows = [{"id": key, "title": title, "status": "completed" if good else "pending"} for key, title in specs]
        by_id = {row["id"]: row for row in rows}
        read = lambda relative: _report(root, relative, record)
        observed = not invalid and status not in {"pending", "invalidated"}
        details = _details(sid, read(REPORTS[sid]) if sid in REPORTS else {}, read) if observed else {}
        # Registered success certifies accepted fields. Never infer accepted
        # fields from candidate files (even nonempty candidates).
        for key, outcome in details.items():
            if key in by_id and outcome:
                if good and outcome in {"failed", "pending", "needs_review"}:
                    # A registered accepted delivery outranks an older failed
                    # attempt's report; auxiliary checks may still have warnings.
                    if (sid, key) in {("validate", "local_preflight"), ("extract", "ocr")} and outcome == "failed":
                        by_id[key]["status"] = "warning"
                    continue
                by_id[key]["status"] = outcome
        if sid == "extract":
            # Explicit source collection has no separate answer material.
            if "inputs" in manifest and not _object(inputs.get("answers")).get("path"):
                by_id["answers"]["status"] = "skipped"
            # Parent completion proves native extraction, never OCR execution.
            by_id["ocr"]["status"] = ("warning" if good and details.get("ocr") == "failed"
                                           else details.get("ocr") or "pending")
        if status == "skipped" and not invalid:
            for row in rows:
                row["status"] = "skipped"
        # Missing required optional input has a real skip condition, not success.
        if stage.optional and stage.requires and "inputs" in manifest:
            if all(not _object(inputs.get(key)).get("path") for key in stage.requires):
                for row in rows:
                    row["status"] = "skipped"
        if sid == "underlines" and "inputs" in manifest:
            source = str(_object(inputs.get("docx")).get("path") or _object(inputs.get("source")).get("path") or "")
            if not source.lower().endswith(".docx"):
                rows[0]["status"] = "skipped"
        own_work = activity.get("stage") == sid
        audio_work = sid == "audio" and audio_state.get("running") is True and not audio_state.get("stale")
        running = own_work or audio_work or status == "running"
        if running:
            detail = str(activity.get("detail") or "") if own_work else ""
            if audio_work:
                detail += " " + str(audio_state.get("phase") or "") + " " + str(audio_state.get("detail") or "")
            matches = [key for key, terms in TERMS.get(sid, {}).items()
                       if any(term in detail.lower() for term in terms)]
            if len(matches) == 1 and by_id[matches[0]]["status"] != "skipped":
                by_id[matches[0]]["status"] = "running"
            else:
                rows.append({"id": "stage_execution", "title": "阶段执行/验收", "status": "running"})
        elif status in FAIL and not any(r["status"] == "failed" for r in rows):
            rows.append({"id": "stage_execution", "title": "阶段执行/验收", "status": "failed"})
        # Human/remote stages retain their actual ledger state, never inherit
        # local automated flow completion.
        if sid not in STEPS:
            rows[0]["status"] = "pending" if invalid else status
        result[sid] = rows
    return result
