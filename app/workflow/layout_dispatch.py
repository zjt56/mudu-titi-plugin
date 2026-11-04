"""Connect a blocked structure gate to durable, optional layout OCR evidence.

Never changes the manifest, gate verdict or structure. No health requests.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from . import layout_review as layout

PROTOCOL = "upload-workflow.layout-request/v1"


def _entry_path(batch_dir, entry):
    value = entry.get("path") if isinstance(entry, dict) else entry
    if not isinstance(value, str) or not value.strip():
        raise layout.LayoutReviewError("Missing input path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = batch_dir / path
    path = path.resolve(strict=True)
    if not path.is_file():
        raise layout.LayoutReviewError("Input is not a file")
    if isinstance(entry, dict) and entry.get("sha256") and entry["sha256"] != layout._hash(path):
        raise layout.LayoutReviewError("Registered input changed")
    return path


def _issues(structure, gate):
    result, seen = [], set()
    for items in [structure.get("unresolved", []), *[gate.get(key, []) for key in
                   ("blockers", "unresolved", "errors", "blocking_failures")]]:
        if not isinstance(items, list):
            continue
        for issue in items:
            if isinstance(issue, str):
                issue = {"code": "GATE_BLOCKED", "message": issue}
            if not isinstance(issue, dict):
                continue
            marker = (str(issue.get("code")), str(issue.get("message")),
                      str(issue.get("line", issue.get("source_line"))))
            if marker not in seen:
                seen.add(marker)
                result.append(issue)
    return {"unresolved": result}


def _prepare(batch_dir, manifest, gate_report):
    batch_dir = Path(batch_dir).expanduser().resolve()
    if gate_report.get("passed") is True:
        return {"protocol": PROTOCOL, "status": "not_required", "dispatch_state": "not_required",
                "request_path": None, "report_path": None, "job_id": None}
    review_dir = batch_dir / "07_review"
    review_dir.mkdir(parents=True, exist_ok=True)
    request_path = review_dir / "layout-request.json"
    output_dir = review_dir / "layout-ocr"
    report_path = output_dir / "layout-review.json"
    request = {"protocol": PROTOCOL, "status": "needs_review", "dispatch_state": "unavailable",
               "request_path": str(request_path), "report_path": None, "job_id": None,
               "source": None, "rendered_source": None, "bindings": {}, "targets": [],
               "reason": "", "next_steps": [],
               "gate_sha256": hashlib.sha256(json.dumps(gate_report, ensure_ascii=False,
                 sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()}

    def finish(state, reason):
        request["dispatch_state"], request["reason"] = state, reason
        layout._write(request_path, request)
        return request

    # Preserve an already submitted id even if configuration later disappears.
    existing = None
    if report_path.is_file():
        existing = layout._read_json(report_path)
        if not isinstance(existing, dict) or existing.get("protocol") != layout.VERSION:
            return finish("unavailable", "invalid_existing_report_no_new_submission")
        request["report_path"], request["job_id"] = str(report_path), existing.get("job_id")

    structure_path = batch_dir / "01_source" / "structure.json"
    structure = layout._read_json(structure_path) if structure_path.is_file() else {}
    if not isinstance(structure, dict):
        return finish("unavailable", "invalid_structure")
    if structure_path.is_file():
        request["bindings"]["structure"] = layout._snapshot(structure_path)
    extraction_path = batch_dir / "01_source" / "extraction.json"
    if extraction_path.is_file():
        request["bindings"]["extraction"] = layout._snapshot(extraction_path)
    # Use raw text only. Denoised/trimmed 正文.txt has different line numbers.
    text_path = batch_dir / "01_source" / "原始正文.txt"
    raw_text = text_path.read_text(encoding="utf-8-sig") if text_path.is_file() else None
    if text_path.is_file():
        request["bindings"]["source_text"] = layout._snapshot(text_path)
    request["targets"] = layout.build_review_targets(_issues(structure, gate_report), source_text=raw_text)
    try:
        inputs = manifest.get("inputs", {})
        source_entry = inputs.get("source")
        source = _entry_path(batch_dir, source_entry)
        request["source"] = layout._snapshot(source)
    except (OSError, ValueError, TypeError, AttributeError):
        return finish("unavailable", "source_missing_or_registered_hash_changed")
    if not structure_path.is_file():
        return finish("unavailable", "structure_missing")
    if structure.get("source_sha256") and structure["source_sha256"] != request["source"]["sha256"]:
        return finish("stale", "structure_source_hash_mismatch")

    render = None
    if source.suffix.lower() == ".docx":
        entry = inputs.get("layout_pdf") or inputs.get("rendered_source")
        if not entry:
            request["next_steps"] = [
                "Render the original DOCX to a full-document PDF using a trusted Office renderer; keep pagination/font warnings.",
                "Register inputs.layout_pdf or inputs.rendered_source with an explicit PDF path and optional sha256; rerun the blocked gate.",
                "Alternatively: python -m workflow.layout_review submit <original.docx> --rendered-source <render.pdf> --whole-page --out-dir <new-evidence-directory>"]
            return finish("requires_render", "docx_requires_explicit_full_document_render")
        try:
            render = _entry_path(batch_dir, entry)
            request["rendered_source"] = layout._snapshot(render)
        except (OSError, ValueError, TypeError):
            return finish("requires_render", "render_missing_or_registered_hash_changed")
        if render.suffix.lower() != ".pdf":
            request["next_steps"] = ["Use an explicit full-document PDF for automatic dispatch; complete page images may be submitted manually with --whole-page and --page-map."]
            return finish("requires_render", "automatic_dispatch_requires_rendered_pdf_not_embedded_picture")
    elif source.suffix.lower() != ".pdf":
        request["next_steps"] = ["For a verified complete page image, use workflow.layout_review submit --whole-page with an explicit original page mapping; do not use embedded question pictures."]
        return finish("unavailable", "automatic_dispatch_requires_pdf")

    # Persist the request before attempting an upload, including all gate doubts.
    layout._write(request_path, request)
    if not any(os.environ.get(name, "").strip() for name in ("OCR_CONFIG", "OCR_BASE_URL", "OCR_WORKBENCH_CLIENT")):
        return finish("unavailable", "ocr_http_not_configured")
    try:
        config = layout.load_config()
    except (OSError, ValueError, TypeError):
        return finish("unavailable", "ocr_http_configuration_invalid")
    try:
        report = layout.start_review(
            source, output_dir, config=config, rendered_source=render, whole_page=render is not None,
            structure_path=structure_path,
            extraction_path=extraction_path if extraction_path.is_file() else None,
            source_text_path=text_path if text_path.is_file() else None)
        # Existing runs are validated above by start_review, which never re-POSTs.
        # One GET per runner invocation, only when a prior report already exists.
        if existing is not None and report.get("job_id") and report.get("evidence_state") != "stale":
            report = layout.resume_review(report_path, config=config)
        request["report_path"], request["job_id"] = str(report_path), report.get("job_id")
        request["submission_state"] = report.get("submission_state")
        request["evidence_state"] = report.get("evidence_state")
        request["job_status"] = report.get("job_status")
        state = report.get("evidence_state", "pending")
        if report.get("submission_state") in {"request_started", "submission_unknown"}:
            state = "submission_unknown"
        return finish(state, "ocr_evidence_only_structure_gate_remains_blocked")
    except Exception:
        # Existing report or lock may reflect a POST accepted by the service.
        # Never hide/recreate it or try a new output directory automatically.
        if report_path.is_file():
            try:
                report = layout._read_json(report_path)
                request["report_path"], request["job_id"] = str(report_path), report.get("job_id")
            except (OSError, ValueError, AttributeError):
                pass
        return finish("unavailable", "layout_dispatch_failed_reconcile_existing_report_no_resubmit")


def prepare_layout_fallback(batch_dir, manifest, gate_report):
    """Create the blocked-gate request and submit/resume optional HTTP OCR.

    All inputs are read-only. Relative manifest input paths resolve under the
    batch. Automatic DOCX dispatch accepts only an explicitly registered PDF.
    No exception text, endpoint, credentials or config contents are returned.
    """
    try:
        return _prepare(batch_dir, manifest, gate_report)
    except Exception:
        # A missing/unwritable directory or malformed optional evidence must not
        # replace the runner's actual gate failure with an unrelated exception.
        result = {"protocol": PROTOCOL, "status": "needs_review", "dispatch_state": "unavailable",
                  "request_path": None, "report_path": None, "job_id": None,
                  "reason": "layout_request_unavailable_gate_remains_blocked"}
        try:
            directory = Path(batch_dir).expanduser().resolve() / "07_review"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / "layout-request.json"
            existing = directory / "layout-ocr" / "layout-review.json"
            if existing.is_file():
                report = layout._read_json(existing)
                result["report_path"], result["job_id"] = str(existing), report.get("job_id")
            result["request_path"] = str(path)
            layout._write(path, result)
        except Exception:
            pass
        return result
