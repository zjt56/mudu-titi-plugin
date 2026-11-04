"""Whole-page OCR evidence helper; never approves structure or updates a manifest.

POST once, persist the job id, GET to resume. Network ambiguity without an id
requires operator reconciliation rather than automatic resubmission.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid

VERSION = "upload-workflow.layout-review/v1"
IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


class LayoutReviewError(ValueError):
    """Safe, actionable error (never includes connection settings)."""


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_config(config_path=None, *, client_path=None):
    """Resolve HTTP config without importing/executing a vendor client.

    Explicit config > OCR_CONFIG > sibling config of explicit client; individual
    OCR_BASE_URL / OCR_API_KEY environment variables override file values.
    No local CLI/model config is treated as an HTTP layout service.
    """
    chosen = config_path or os.environ.get("OCR_CONFIG")
    client = client_path or os.environ.get("OCR_WORKBENCH_CLIENT")
    if chosen is None and client:
        chosen = Path(client).expanduser().parent / "ocr-config.json"
    data = {}
    if chosen:
        try:
            data = json.loads(Path(chosen).expanduser().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise LayoutReviewError("Cannot read OCR HTTP configuration") from None
        if not isinstance(data, dict):
            raise LayoutReviewError("OCR HTTP configuration must be an object")
    base = os.environ.get("OCR_BASE_URL") or data.get("base_url")
    key = os.environ.get("OCR_API_KEY") or data.get("api_key") or ""
    if not isinstance(base, str) or not isinstance(key, str):
        raise LayoutReviewError("Configure OCR_BASE_URL or an ocr-workbench HTTP config")
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise LayoutReviewError("OCR service must be an HTTP(S) base URL without embedded credentials")
    return {"base_url": base.rstrip("/"), "api_key": key}


def _redact(value, key=""):
    # Raw service evidence is retained, with credentials removed if echoed.
    if isinstance(value, dict):
        return {k: ("***" if re.search(r"api.?key|authorization|password|secret|token", k, re.I)
                    else _redact(v, key)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item, key) for item in value]
    if isinstance(value, str) and key:
        return value.replace(key, "***")
    return value


class HTTPWorkbench:
    """One HTTP request per call; deliberately no POST retry or health probe."""

    def __init__(self, config=None, *, timeout=30):
        settings = config if config is not None else load_config()
        self._base = settings["base_url"].rstrip("/")
        self._key = settings.get("api_key", "")
        self.timeout = timeout

    def _request(self, method, route, data=None, headers=None):
        request = urllib.request.Request(self._base + route, data=data, method=method,
                                         headers=headers or {})
        if self._key:
            request.add_header("X-API-Key", self._key)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status, body = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, body = exc.code, exc.read()
        # Network errors deliberately propagate to durable needs_review handling.
        try:
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeError):
            payload = {"response_format": "non_json"}
        return status, _redact(payload, self._key)

    def submit(self, source):
        boundary = "----layout" + uuid.uuid4().hex
        filename = Path(source).name.encode("ascii", "replace").decode().replace('"', "_").replace("\r", "_").replace("\n", "_")
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        data = bytearray()
        for name, value in (("engine", "paddle"), ("layout", "true")):
            data.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        data.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'.encode())
        data.extend(Path(source).read_bytes())
        data.extend(f"\r\n--{boundary}--\r\n".encode())
        return self._request("POST", "/api/jobs", bytes(data),
                             {"Content-Type": f"multipart/form-data; boundary={boundary}"})

    def status(self, job_id):
        return self._request("GET", "/api/jobs/" + urllib.parse.quote(job_id, safe=""))


def _write(path, data):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix=".layout-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def _lock(report_path):
    # Exclusive create prevents concurrent submissions, including on Windows.
    # A crash leaves the lock in place: operator reconciles it, never auto-retry.
    path = Path(str(report_path) + ".lock")
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise LayoutReviewError("Review is locked; reconcile any in-flight submission before removing its lock") from None
    os.close(descriptor)
    try:
        yield
    finally:
        path.unlink()


def _snapshot(path):
    path = Path(path).expanduser().resolve(strict=True)
    return {"path": str(path), "sha256": _hash(path)}


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise LayoutReviewError("Cannot read review input JSON") from None


def build_review_targets(structure, *, source_text=None):
    """Link existing doubts to explicit pages / raw [PAGE n] line markers.

    A page link is a search aid, never a resolution or visual-review signature.
    Pass the original text underlying structure line numbers, not denoised text.
    """
    page_by_line = {}
    current = None
    for number, line in enumerate((source_text or "").splitlines(), 1):
        match = re.fullmatch(r"\s*\[PAGE ([1-9]\d*)\]\s*", line)
        if match:
            current = int(match.group(1))
        if current:
            page_by_line[number] = current
    targets = []
    for index, issue in enumerate(structure.get("unresolved") or []):
        if not isinstance(issue, dict):
            continue
        pages = issue.get("pages", [issue.get("page")])
        pages = pages if isinstance(pages, list) else []
        pages = [p for p in pages if type(p) is int and p > 0]
        line = issue.get("line", issue.get("source_line"))
        if not pages and type(line) is int and line in page_by_line:
            pages = [page_by_line[line]]
        targets.append({"target_id": f"issue-{index + 1:04d}", "issue": issue,
                        "pages_requested": sorted(set(pages)), "status": "needs_review",
                        "association": "page_candidate" if pages else "unmapped",
                        "evidence_refs": []})
    return targets


def _raw(report_path, report, kind, status, payload):
    number = len(report["raw_results"]) + 1
    path = report_path.parent / f"raw-{number:04d}-{kind}-{uuid.uuid4().hex[:8]}.json"
    # Separate files preserve every response rather than replacing the last job.
    _write(path, {"http_status": status, "payload": payload})
    report["raw_results"].append({"path": str(path), "sha256": _hash(path)})


def _verify_inputs(report):
    for item in [report["source"], report["input"], *report.get("bindings", {}).values()]:
        try:
            if _hash(item["path"]) != item["sha256"]:
                return False
        except OSError:
            return False
    return True


def _job_id(payload):
    if not isinstance(payload, dict):
        return None
    candidate = payload.get("job_id") or payload.get("id")
    jobs = payload.get("jobs")
    if not candidate and isinstance(jobs, list) and len(jobs) == 1 and isinstance(jobs[0], dict):
        candidate = jobs[0].get("id")
    return candidate if isinstance(candidate, str) and candidate.strip() else None


def _normalize(report, job):
    result = job.get("result")
    pages = result.get("pages") if isinstance(result, dict) else None
    engine = job.get("engine_used") or (result.get("engine") if isinstance(result, dict) else None) or job.get("engine")
    report["engine_used"] = engine
    reasons = []
    if engine != "paddle":
        reasons.append("document_engine_not_confirmed")
    if not isinstance(pages, list) or not pages:
        reasons.append("pages_missing")
        pages = []
    seen = set()
    normalized = []
    page_map = report.get("page_map", {})
    for page in pages:
        if not isinstance(page, dict) or type(page.get("page")) is not int or page["page"] < 1 or page["page"] in seen:
            reasons.append("invalid_or_duplicate_page")
            continue
        number = page["page"]
        seen.add(number)
        blocks = page.get("blocks")
        valid_blocks = isinstance(blocks, list) and bool(blocks)
        for block in blocks if isinstance(blocks, list) else []:
            box = block.get("bbox") if isinstance(block, dict) else None
            if (not isinstance(box, list) or len(box) != 4 or
                any(type(n) not in (float, int) or not math.isfinite(n) for n in box) or
                box[2] <= box[0] or box[3] <= box[1]):
                valid_blocks = False
        if not valid_blocks:
            reasons.append(f"page_{number}_layout_blocks_missing_or_invalid")
        source_page = page_map.get(str(number)) if page_map else number
        if source_page is None:
            reasons.append("source_page_mapping_incomplete")
        normalized.append({"page": number, "source_page": source_page,
                           "markdown": page.get("markdown", ""),
                           "blocks": blocks if isinstance(blocks, list) else [],
                           "layout_available": valid_blocks})
    expected = report.get("expected_pages")
    # Even without a declared count, non-contiguous page numbers are incomplete.
    if seen and seen != set(range(1, max(seen) + 1)):
        reasons.append("page_sequence_incomplete")
    if expected is not None and seen != set(range(1, expected + 1)):
        reasons.append("page_coverage_incomplete")
    report["pages"] = normalized
    report["evidence_state"] = "layout_available" if not reasons else "incomplete"
    report["warnings"] = sorted(set(reasons + ["ocr_is_candidate_evidence_not_review_approval"]))
    for target in report["targets"]:
        requested = target["pages_requested"]
        target["evidence_refs"] = [
            {"page": p["page"], "source_page": p["source_page"],
             "block_indexes": list(range(len(p["blocks"]))), "scope": "page_candidate"}
            for p in normalized if requested and p["source_page"] in requested]
        if requested and not target["evidence_refs"]:
            target["association"] = "page_missing"


def document_engine_readiness(health):
    """Interpret an already obtained health response without making requests.

    Idle/loading are valid cold-start/queue states; error/disabled/stopped are
    unavailable. Public health may hide engines, which remains unknown.
    """
    if not isinstance(health, dict) or not isinstance(health.get("engines"), list):
        return "unknown"
    engine = next((e for e in health["engines"] if isinstance(e, dict) and e.get("name") == "paddle"), None)
    if engine is None:
        return "unavailable"
    if engine.get("state") in {"error", "disabled", "stopped"}:
        return "unavailable"
    if engine.get("ready") is True or engine.get("state") == "ready":
        return "ready"
    return "cold" if engine.get("state") in {"idle", "loading"} else "unknown"


def start_review(source, output_dir, *, rendered_source=None, whole_page=False,
                 page_map=None, expected_pages=None, structure_path=None,
                 extraction_path=None, source_text_path=None, client=None, config=None,
                 health=None):
    """Create durable evidence; reuse the same output directory to avoid POSTs.

    source: original PDF/image/DOCX. DOCX requires rendered_source (PDF or one
    full page image) and whole_page=True. Cropped/embedded images are not pages.
    The caller must explicitly attest images represent whole pages as well.
    """
    source = Path(source).expanduser().resolve(strict=True)
    if source.suffix.lower() == ".docx" and (rendered_source is None or not whole_page):
        raise LayoutReviewError("DOCX requires explicit rendered PDF/full-page image input and whole_page=True")
    if source.suffix.lower() not in {".pdf", ".docx", *IMAGES}:
        raise LayoutReviewError("Original source must be PDF, DOCX or a full-page image")
    if rendered_source is not None and source.suffix.lower() != ".docx":
        raise LayoutReviewError("rendered_source is only used to bind a DOCX render")
    submitted = Path(rendered_source).expanduser().resolve(strict=True) if rendered_source else source
    if submitted.suffix.lower() not in {".pdf", *IMAGES}:
        raise LayoutReviewError("Submit a rendered PDF or full-page image, never DOCX or embedded text")
    if submitted.suffix.lower() in IMAGES and not whole_page:
        raise LayoutReviewError("Images require whole_page=True; an embedded picture/crop is not page evidence")
    if expected_pages is not None and (type(expected_pages) is not int or expected_pages < 1):
        raise LayoutReviewError("expected_pages must be a positive integer")
    if page_map is not None and not isinstance(page_map, dict):
        raise LayoutReviewError("page_map must be a JSON object")
    mapping = {str(k): v for k, v in (page_map or {}).items()}
    if any(not re.fullmatch(r"[1-9]\d*", k) or type(v) is not int or v < 1 for k, v in mapping.items()) or len(set(mapping.values())) != len(mapping):
        raise LayoutReviewError("page_map must map unique positive service pages to original pages")
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    path = output / "layout-review.json"
    with _lock(path):
        if path.exists():
            report = _read_json(path)
            if report.get("source", {}).get("sha256") != _hash(source) or report.get("input", {}).get("sha256") != _hash(submitted):
                raise LayoutReviewError("Existing review belongs to different input; choose a new output directory")
            for label, item in (("structure", structure_path), ("extraction", extraction_path), ("source_text", source_text_path)):
                if item is not None and report.get("bindings", {}).get(label, {}).get("sha256") != _hash(item):
                    raise LayoutReviewError("Existing review has different bindings; choose a new output directory")
            if (page_map is not None and mapping != report.get("page_map") or
                    expected_pages is not None and expected_pages != report.get("expected_pages")):
                raise LayoutReviewError("Existing review has different page scope; choose a new output directory")
            if not _verify_inputs(report):
                report.update(evidence_state="stale", status="needs_review", passed=False)
                report["warnings"] = sorted(set(report["warnings"] + ["input_changed"]))
                _write(path, report)
            # Re-running start is safe: it neither re-submits nor implicitly polls.
            return report
        bindings = {}
        for label, item in (("structure", structure_path), ("extraction", extraction_path), ("source_text", source_text_path)):
            if item is not None:
                bindings[label] = _snapshot(item)
        structure = _read_json(structure_path) if structure_path else {}
        if not isinstance(structure, dict):
            raise LayoutReviewError("Structure must be a JSON object")
        source_info, input_info = _snapshot(source), _snapshot(submitted)
        if structure.get("source_sha256") and structure["source_sha256"] != source_info["sha256"]:
            raise LayoutReviewError("Structure source hash does not match original input")
        text = Path(source_text_path).read_text(encoding="utf-8-sig") if source_text_path else None
        report = {"protocol": VERSION, "status": "needs_review", "passed": False,
                  "coverage_verified": False, "source": source_info, "input": input_info,
                  "input_scope": "rendered_document" if rendered_source else "whole_page",
                  "bindings": bindings, "page_map": mapping, "expected_pages": expected_pages,
                  "engine_requested": "paddle", "layout_requested": True,
                  "job_id": None, "job_status": None, "submission_state": "request_started",
                  "evidence_state": "pending", "pages": [], "raw_results": [],
                  "targets": build_review_targets(structure, source_text=text), "warnings": [],
                  "note": "OCR evidence requires whole-page comparison; existing structure doubts remain unresolved"}
        report["engine_readiness"] = document_engine_readiness(health)
        if report["engine_readiness"] == "unavailable":
            report.update(submission_state="not_submitted", evidence_state="failed")
            report["warnings"].append("document_engine_unavailable_from_supplied_health")
            _write(path, report)
            return report
        _write(path, report)  # Persist ambiguity before any potentially accepted POST.
        try:
            adapter = client or HTTPWorkbench(config)
            status, payload = adapter.submit(submitted)
            _raw(path, report, "submit", status, payload)
            report["job_id"] = _job_id(payload)
            report["submission_state"] = "submitted" if report["job_id"] else "submission_unknown"
            if status not in (200, 201, 202) or not report["job_id"]:
                report["warnings"].append(f"submission_http_{status}_resume_with_job_id" if report["job_id"] else "submission_not_confirmed_do_not_resubmit")
        except Exception:
            # Exception text can contain URLs/keys/local config, so never record it.
            report["submission_state"] = "submission_unknown"
            report["warnings"].append("submission_unconfirmed_check_service_before_any_new_upload")
        if not _verify_inputs(report):
            report["evidence_state"] = "stale"
            report["warnings"].append("input_changed")
        _write(path, report)
        return report


def resume_review(report_path, *, client=None, config=None, job_id=None):
    """Query once, never upload. An operator may attach a reconciled job id."""
    path = Path(report_path).expanduser().resolve(strict=True)
    with _lock(path):
        report = _read_json(path)
        if report.get("protocol") != VERSION:
            raise LayoutReviewError("Unsupported layout-review report")
        if job_id:
            if report.get("job_id") and report["job_id"] != job_id:
                raise LayoutReviewError("Cannot replace an existing job id")
            report["job_id"] = job_id
            report["submission_state"] = "submitted"
        if not _verify_inputs(report):
            report.update(evidence_state="stale", status="needs_review", passed=False)
            report["warnings"] = sorted(set(report["warnings"] + ["input_changed"]))
            _write(path, report)
            return report
        if not report.get("job_id"):
            raise LayoutReviewError("No job id; reconcile submission with service before starting a new upload")
        try:
            adapter = client or HTTPWorkbench(config)
            status, job = adapter.status(report["job_id"])
            _raw(path, report, "status", status, job)
            if status != 200 or not isinstance(job, dict):
                report["evidence_state"] = "unavailable"
                report["warnings"].append(f"status_http_{status}_retry_query_only")
            elif job.get("id") and job["id"] != report["job_id"]:
                report["warnings"].append("job_id_mismatch")
                report["evidence_state"] = "incomplete"
            else:
                report["job_status"] = job.get("status")
                if job.get("status") == "done":
                    _normalize(report, job)
                elif job.get("status") in {"error", "interrupted"}:
                    report["evidence_state"] = "failed"
                    report["warnings"].append("job_failed_requires_review_no_automatic_resubmit")
                else:
                    report["evidence_state"] = "pending"
        except Exception:
            report["evidence_state"] = "unavailable"
            report["warnings"].append("status_unavailable_resume_same_job")
        report["status"], report["passed"], report["coverage_verified"] = "needs_review", False, False
        _write(path, report)
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="ocr-workbench HTTP config (never local CLI config)")
    parser.add_argument("--client", type=Path, help="skill ocr.py path: discover sibling HTTP config only")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("submit")
    start.add_argument("source", type=Path)
    start.add_argument("--out-dir", required=True, type=Path)
    start.add_argument("--rendered-source", type=Path)
    start.add_argument("--whole-page", action="store_true")
    start.add_argument("--structure", type=Path)
    start.add_argument("--extraction", type=Path)
    start.add_argument("--source-text", type=Path)
    start.add_argument("--expected-pages", type=int)
    start.add_argument("--page-map", type=Path, help='JSON object, e.g. {"1": 7}')
    resume = commands.add_parser("resume")
    resume.add_argument("report", type=Path)
    resume.add_argument("--job-id", help="attach a job id after service-side reconciliation")
    args = parser.parse_args(argv)
    try:
        # Do not require connectivity or config merely to inspect an existing run.
        config = load_config(args.config, client_path=args.client)
        if args.command == "submit":
            result = start_review(args.source, args.out_dir, config=config,
                                  rendered_source=args.rendered_source, whole_page=args.whole_page,
                                  page_map=_read_json(args.page_map) if args.page_map else None,
                                  structure_path=args.structure, extraction_path=args.extraction,
                                  source_text_path=args.source_text, expected_pages=args.expected_pages)
        else:
            result = resume_review(args.report, config=config, job_id=args.job_id)
    except (LayoutReviewError, OSError):
        print(json.dumps({"status": "needs_review", "passed": False,
                          "error": "Invalid input/configuration; verify source, render, bindings and resume job id"}))
        return 2
    print(json.dumps({"status": result["status"], "passed": False,
                      "job_id": result["job_id"], "submission_state": result["submission_state"],
                      "evidence_state": result["evidence_state"]}, ensure_ascii=False))
    return 0 if result["evidence_state"] == "layout_available" else 2


if __name__ == "__main__":
    raise SystemExit(main())
