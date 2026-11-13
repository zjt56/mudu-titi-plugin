"""PaddleOCR asynchronous jobs adapter; Python standard library only.

Tokens are read from private runtime configuration/environment, never serialized
into artifacts or error messages. POST is never automatically retried.
"""
from __future__ import annotations

import json
import math
import mimetypes
import os
from pathlib import Path
import re
import socket
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from . import ocr_job_store

DEFAULTS = {
    "endpoint": "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs",
    "model": "PaddleOCR-VL-1.6",
    "useDocOrientationClassify": False,
    "useDocUnwarping": False,
    "useChartRecognition": False,
    "timeout_seconds": 300,
    "poll_interval_seconds": 2,
}
OPTION_KEYS = ("useDocOrientationClassify", "useDocUnwarping", "useChartRecognition")
MAX_RESPONSE = 64 * 1024 * 1024
MAX_FILE = 200 * 1024 * 1024


def _safe_message(value, token, limit=500):
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return None
    value = str(value)[:4096].replace(token, "[REDACTED]") if token else str(value)[:4096]
    value = re.sub(r"https?://[^\s\"'<>]+", "[URL REDACTED]", value, flags=re.I)
    value = re.sub(r"Bearer\s+[^\s\"'<>]+", "Bearer [REDACTED]", value, flags=re.I)
    value = re.sub(r"(?i)((?:api[_-]?key|access[_-]?token|token|authorization)\s*[=:]\s*)[^\s,;\"'<>]+", r"\1[REDACTED]", value)
    return re.sub(r"[\x00-\x1f\x7f]", " ", value).strip()[:limit] or None


class PaddleJobsError(RuntimeError):
    def __init__(self, code, *, http_status=None, error_code=None, error_message=None,
                 job_id=None, phase=None, retryable=None, progress=None):
        self.code, self.job_id, self.phase = code, job_id, phase
        self.retryable = retryable
        self.progress = progress
        self.details = {key: value for key, value in {"http_status": http_status, "error_code": error_code, "error_message": error_message,
                        "job_id": job_id, "phase": phase, "retryable": retryable, "progress": progress}.items() if value is not None}
        message = {
            "invalid_config": "Invalid OCR settings or source.",
            "missing_token": "An OCR token is required.",
            "http_error": "OCR server rejected the request.",
            "unsafe_redirect": "OCR redirect refused; use the final endpoint.",
            "network_error": "Cannot connect to OCR server.",
            "timeout": "OCR job deadline exceeded; submission is not retried.",
            "waiting": "OCR job is retained and can be queried again.",
            "submission_unknown": "OCR submission outcome is unknown; automatic resubmission is forbidden.",
            "submission_conflict": "OCR submission reconciliation returned conflicting jobs.",
            "receipt_corrupt": "OCR receipt is invalid; automatic resubmission is forbidden.",
            "storage_error": "OCR durable state could not be saved.",
            "job_not_found": "OCR job no longer exists; automatic resubmission is forbidden.",
            "job_expired": "OCR job expired; automatic resubmission is forbidden.",
            "job_failed": "OCR job failed.",
            "invalid_result": "OCR server returned an invalid or empty result.",
        }.get(code, "OCR request failed.")
        super().__init__(message + (" " + error_message if error_message else ""))


def _remote_details(payload, token):
    if not isinstance(payload, dict):
        return {}
    candidates = [payload]
    candidates.extend(payload[key] for key in ("data", "error") if isinstance(payload.get(key), dict))
    details = {}
    for item in candidates:
        for key in ("errorMsg", "errorMessage", "message"):
            if key in item and "error_message" not in details:
                value = _safe_message(item[key], token)
                if value:
                    details["error_message"] = value
        for key in ("errorCode", "code"):
            if key in item and "error_code" not in details:
                value = _safe_message(item[key], token, limit=128)
                if value:
                    details["error_code"] = value
    return details


def safe_url(value, *, endpoint=False):
    if not isinstance(value, str) or not value or len(value) > 8192 or re.search(r"[\s\\\x00-\x1f\x7f]", value):
        raise PaddleJobsError("invalid_config")
    try:
        url = urllib.parse.urlsplit(value)
        if url.username or url.password or not url.hostname or url.fragment or url.port == 0:
            raise ValueError()
        if url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError()
        if endpoint and (url.query or not url.path.endswith("/jobs") or "%" in url.path or "//" in url.path or any(p in {".", ".."} for p in url.path.split("/"))):
            raise ValueError()
    except ValueError:
        raise PaddleJobsError("invalid_config") from None
    return value


def validate_config(config):
    if not isinstance(config, dict):
        raise PaddleJobsError("invalid_config")
    result = {**DEFAULTS, **config}
    safe_url(result["endpoint"], endpoint=True)
    model = result["model"]
    if not isinstance(model, str) or not model.strip() or len(model) > 256 or re.search(r"[\x00-\x1f\x7f]", model):
        raise PaddleJobsError("invalid_config")
    for key in OPTION_KEYS:
        if type(result[key]) is not bool:
            raise PaddleJobsError("invalid_config")
    for key, low, high in (("timeout_seconds", 1, 1800), ("poll_interval_seconds", 0.01, 60)):
        value = result[key]
        if type(value) not in (float, int) or not math.isfinite(value) or not low <= value <= high:
            raise PaddleJobsError("invalid_config")
    if "token" in result and (not isinstance(result["token"], str) or len(result["token"]) > 4096 or re.search(r"[^\x21-\x7e]", result["token"])):
        raise PaddleJobsError("invalid_config")
    return result


def _token(config):
    value = config.get("token")
    if value is None:
        if config.get("token_file"):
            try:
                path = Path(config["token_file"])
                if path.is_symlink() or path.stat().st_size > 4096:
                    raise OSError()
                value = path.read_text(encoding="utf-8")
            except OSError:
                raise PaddleJobsError("missing_token") from None
        else:
            value = os.environ.get("MUDU_OCR_TOKEN", "")
    if not value:
        raise PaddleJobsError("missing_token")
    if not isinstance(value, str) or len(value) > 4096 or re.search(r"[^\x21-\x7e]", value):
        raise PaddleJobsError("invalid_config")
    return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _redact(value, token):
    """Keep response structure/content, but never persist credentials or URLs.

    URL values (including signed asset URLs embedded in Markdown and map keys)
    are usable only in memory while downloading. Even unknown signing schemes
    are covered by removing the entire remote URL rather than a query allowlist.
    """
    if isinstance(value, dict):
        return {_redact(str(key), token): (
            "[REDACTED]" if re.search(
                r"(?i)(token|authorization|api[_-]?key|password|secret|signature|credential|cookie|^ak$|^sk$)", str(key))
            else _redact(item, token)) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, token) for item in value]
    if isinstance(value, str):
        value = value.replace(token, "[REDACTED]") if token else value
        value = re.sub(r"https?://[^\s\"'<>]+", "[URL REDACTED]", value, flags=re.I)
        value = re.sub(r"Bearer\s+[^\s\"'<>]+", "Bearer [REDACTED]", value, flags=re.I)
        return re.sub(r"(?i)((?:api[_-]?key|access[_-]?token|token|authorization)\s*[=:]\s*)[^\s,;\"'<>]+", r"\1[REDACTED]", value)
    return value


def _layout_blocks(layout):
    """Adapt the documented PaddleOCR-VL prunedResult, never infer reading order.

    Official schema: PaddleOCR/docs/version3.x/pipeline_usage/PaddleOCR-VL.en.md,
    sections 2.2 and 4.3. parsing_res_list is already in service reading order;
    block_order may be None. Coordinates retain their service units/scale.
    """
    pruned = layout.get("prunedResult")
    raw_blocks = pruned.get("parsing_res_list", []) if isinstance(pruned, dict) else []
    blocks, warnings = [], []
    if not isinstance(raw_blocks, list):
        raw_blocks = []
        warnings.append("layout_blocks_invalid")
    for index, block in enumerate(raw_blocks):
        if not isinstance(block, dict):
            warnings.append("layout_blocks_invalid")
            continue
        # Keep every provider field, including IDs, polygons and future fields.
        item = dict(block)
        for source, target in (("block_label", "type"), ("block_content", "text"),
                               ("block_bbox", "bbox"), ("block_order", "order")):
            if source in block:
                item[target] = block[source]
        item["service_index"] = index
        blocks.append(item)
    missing = sum(block.get("bbox") is None or block.get("bbox") == [] for block in blocks)
    if not blocks or missing == len(blocks):
        warnings.append("coordinates_absent")
    elif missing:
        warnings.append("coordinates_partial")
    for block in blocks:
        bbox = block.get("bbox")
        if bbox is None or bbox == []:
            continue
        if (not isinstance(bbox, list) or len(bbox) != 4
                or any(type(n) not in (int, float) or not math.isfinite(n) for n in bbox)
                or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]):
            warnings.append("coordinates_invalid")
            break
    return blocks, list(dict.fromkeys(warnings))


def run_job(config, *, file_url=None, file_path=None, pages=None, _image_consumer=None,
            job_store=None):
    """Submit once, poll to done, then fetch and normalize JSONL results.

    Result/image URLs receive no Authorization header. Returned remote references
    are redacted; recognize downloads assets before redaction via its internal
    consumer. Downloaded images remain visually unverified evidence.
    """
    config = validate_config(config)
    token = _token(config)
    if bool(file_url) == bool(file_path):
        raise PaddleJobsError("invalid_config")
    start = time.monotonic()
    deadline = start + config["timeout_seconds"]
    opener = urllib.request.build_opener(_NoRedirect())
    if job_store is not None and not job_store.locked:
        raise RuntimeError("OCR store lock required")

    def save(**changes):
        if job_store is not None:
            job_store.save(**changes)

    def request_once(url, data=None, content_type=None, *, auth=False, json_result=True):
        safe_url(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PaddleJobsError("timeout")
        headers = {"Accept": "application/json" if json_result else "application/jsonl, application/x-ndjson, text/plain"}
        if auth:
            headers["Authorization"] = "Bearer " + token
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
        try:
            with opener.open(req, timeout=min(remaining, 30)) as response:
                chunks, total = [], 0
                while True:
                    if time.monotonic() >= deadline:
                        raise PaddleJobsError("timeout")
                    chunk = response.read(min(65536, MAX_RESPONSE + 1 - total))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > MAX_RESPONSE:
                        raise PaddleJobsError("invalid_result")
                text = b"".join(chunks).decode("utf-8-sig")
            return json.loads(text) if json_result else text
        except urllib.error.HTTPError as exc:
            status, details = exc.code, {}
            # Only whitelisted structured service fields are exposed; never
            # return the raw body, headers, request URL or redirect target.
            if not 300 <= status < 400:
                try:
                    details = _remote_details(json.loads(exc.read(16384).decode("utf-8")), token)
                except (ValueError, UnicodeError, OSError):
                    pass
            exc.close()
            raise PaddleJobsError("unsafe_redirect" if 300 <= status < 400 else "http_error", http_status=status, **details) from None
        except (TimeoutError, socket.timeout):
            raise PaddleJobsError("timeout") from None
        except urllib.error.URLError as exc:
            raise PaddleJobsError("timeout" if isinstance(exc.reason, (TimeoutError, socket.timeout)) else "network_error") from None
        except (ValueError, UnicodeError):
            raise PaddleJobsError("invalid_result") from None
        except OSError:
            raise PaddleJobsError("network_error") from None

    def request(url, data=None, content_type=None, *, auth=False, json_result=True, kind="get"):
        # A request has its own finite retry budget; the overall deadline also
        # bounds every socket timeout and backoff. POST is attempted only once.
        for index in range(3 if data is None else 1):
            if job_store is not None:
                job_store.count(kind)
            try:
                return request_once(url, data, content_type, auth=auth, json_result=json_result)
            except PaddleJobsError as exc:
                transient = exc.code in {"network_error", "timeout"} or (exc.code == "http_error" and exc.details.get("http_status") in {408, 429, 500, 502, 503, 504})
                if data is not None or not transient or index == 2:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PaddleJobsError("timeout") from None
                time.sleep(min(.2 * (2 ** index), remaining))

    def remote_error(payload):
        details = _remote_details(payload, token)
        code = str(details.get("error_code", ""))
        if code in {"11001", "11002"}:
            raise PaddleJobsError("job_not_found" if code == "11001" else "job_expired", **details)
        if code not in {"", "0", "200"}:
            raise PaddleJobsError("http_error", **details)

    job_id = job_store.receipt.get("job_id") if job_store is not None else None
    if job_store is not None and not job_id and job_store.receipt["phase"] != "prepared":
        save(phase="submission_unknown", last_error="submission_unknown")
        # batchId is a reconciliation handle, not a promise of remote idempotency.
        try:
            reconciled = request(config["endpoint"] + "/batch/" + job_store.receipt["batch_id"], auth=True, kind="reconcile")
            remote_error(reconciled)
            data = reconciled.get("data") if isinstance(reconciled, dict) else None
            jobs = data.get("extractResult") if isinstance(data, dict) and data.get("batchId") == job_store.receipt["batch_id"] else None
            if not isinstance(jobs, list):
                raise PaddleJobsError("submission_unknown")
            if len(jobs) > 1:
                raise PaddleJobsError("submission_conflict")
            if len(jobs) == 1 and isinstance(jobs[0], dict):
                job_id = jobs[0].get("jobId")
                if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", job_id):
                    raise PaddleJobsError("submission_unknown")
                save(job_id=job_id, phase="submitted", last_error=None)
            else:
                raise PaddleJobsError("submission_unknown")
        except PaddleJobsError as exc:
            if exc.code == "submission_conflict":
                save(last_error="submission_conflict")
                raise
            save(last_error="submission_unknown")
            raise PaddleJobsError("submission_unknown") from None

    if not job_id:
        optional = {key: config[key] for key in OPTION_KEYS}
        fields = {"model": config["model"], "optionalPayload": optional}
        if job_store is not None:
            fields["batchId"] = job_store.receipt["batch_id"]
        if file_url:
            fields["fileUrl"] = safe_url(file_url)
            body, content_type = json.dumps(fields).encode(), "application/json"
        else:
            try:
                source = Path(file_path).expanduser().resolve()
                if not source.is_file() or source.suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"} or source.stat().st_size > MAX_FILE:
                    raise OSError()
                binary = source.read_bytes()
                if not binary or len(binary) > MAX_FILE:
                    raise OSError()
                if job_store is not None and (ocr_job_store.digest(binary) != job_store.receipt["source"]["sha256"] or len(binary) != job_store.receipt["source"]["size"]):
                    raise OSError()
            except (OSError, TypeError, ValueError):
                raise PaddleJobsError("invalid_config") from None
            boundary = "ocr-" + uuid.uuid4().hex
            parts = []
            for key, value in fields.items():
                value = json.dumps(value) if isinstance(value, dict) else value
                parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n').encode())
            mime = mimetypes.guess_type("source" + source.suffix)[0] or "application/octet-stream"
            parts.extend([(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="source{source.suffix.lower()}"\r\nContent-Type: {mime}\r\n\r\n').encode(), binary, f"\r\n--{boundary}--\r\n".encode()])
            body, content_type = b"".join(parts), "multipart/form-data; boundary=" + boundary
        if job_store is not None:
            save(phase="submitting", submitted_at=ocr_job_store._now(), last_error=None)
        try:
            submitted = request(config["endpoint"], body, content_type, auth=True, kind="submit")
            data = submitted.get("data") if isinstance(submitted, dict) else None
            job_id = data.get("jobId") if isinstance(data, dict) else None
            if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", job_id):
                raise PaddleJobsError("invalid_result", **_remote_details(submitted, token))
            # Publish the handle before interpreting anything else in a response.
            save(job_id=job_id, phase="submitted", last_error=None)
        except PaddleJobsError as exc:
            if job_store is not None:
                save(phase="submission_unknown", last_error="submission_unknown")
                raise PaddleJobsError("submission_unknown") from None
            raise
    save(phase="polling")
    while True:
        status = request(config["endpoint"] + "/" + urllib.parse.quote(job_id, safe=""), auth=True)
        remote_error(status)
        data = status.get("data") if isinstance(status, dict) else None
        state = data.get("state", data.get("status")) if isinstance(data, dict) else None
        updates = {"state": state} if state in {"pending", "running", "done", "failed"} else {}
        if isinstance(data, dict) and isinstance(data.get("extractProgress"), dict):
            for source_key, target in (("totalPages", "total_pages"), ("extractedPages", "extracted_pages")):
                value = data["extractProgress"].get(source_key)
                if isinstance(value, str) and re.fullmatch(r"\d{1,10}", value):
                    value = int(value)
                if type(value) is int and 0 <= value <= 1000000000:
                    updates[target] = value
        save(**updates)
        if state == "done":
            save(phase="downloading")
            result_url = data.get("resultUrl", {}).get("jsonUrl") if isinstance(data.get("resultUrl"), dict) else None
            if not isinstance(result_url, str):
                raise PaddleJobsError("invalid_result")
            raw = request(result_url, json_result=False, kind="download")
            break
        if state == "failed":
            raise PaddleJobsError("job_failed", **_remote_details(status, token))
        if state not in {"pending", "running"}:
            raise PaddleJobsError("invalid_result")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PaddleJobsError("timeout")
        time.sleep(min(config["poll_interval_seconds"], remaining))
    return normalize_jsonl(raw, token=token, pages=pages, job_id=job_id,
                           seconds=round(time.monotonic() - start, 3), model=config["model"],
                           _image_consumer=_image_consumer)


def normalize_jsonl(raw, *, token="", pages=None, job_id=None, seconds=None, model=None, _image_consumer=None):
    """Reprocess saved service JSONL without submitting or downloading a job.

    The returned raw_jsonl preserves every response field after credential/URL
    redaction. Page selection affects normalized pages only, never raw evidence.
    """
    if not isinstance(raw, str):
        raise PaddleJobsError("invalid_result")
    normalized, raw_rows = [], []
    try:
        for line in raw.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            raw_rows.append(_redact(row, token))
            result = row["result"]
            layouts = result["layoutParsingResults"]
            if not isinstance(layouts, list) or not layouts:
                raise ValueError()
            for layout_index, layout in enumerate(layouts):
                markdown = layout["markdown"]
                text = markdown["text"]
                if not isinstance(text, str):
                    raise ValueError()
                images = markdown.get("images", {})
                output_images = layout.get("outputImages", result.get("outputImages", row.get("outputImages", {})))
                if not isinstance(images, (dict, list)) or not isinstance(output_images, (dict, list)):
                    raise ValueError()
                blocks, warnings = _layout_blocks(layout)
                if images or output_images:
                    warnings.append("remote_images_unverified")
                normalized.append({"page": len(normalized) + 1, "text": text, "method": "ocr", "blocks": blocks,
                                   "images": images, "outputImages": output_images, "warnings": warnings,
                                   "layout_source": {"jsonl_row": len(raw_rows), "layout_index": layout_index}})
    except (ValueError, KeyError, TypeError):
        raise PaddleJobsError("invalid_result") from None
    if pages is not None:
        selection = str(pages)
        if not re.fullmatch(r"[1-9]\d*(?:-[1-9]\d*)?", selection):
            raise PaddleJobsError("invalid_config")
        bounds = list(map(int, selection.split("-")))
        first, last = bounds[0], bounds[-1]
        if first > last or last > len(normalized):
            raise PaddleJobsError("invalid_result")
        normalized = normalized[first - 1:last]
    if not normalized:
        raise PaddleJobsError("invalid_result")
    result = {"pages": normalized, "job_id": job_id,
              "seconds": seconds,
              "warnings": list(dict.fromkeys(w for page in normalized for w in page["warnings"])),
              "raw_jsonl": "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in raw_rows),
              "provenance": {"backend": "paddle_jobs", "model": model, "pages_requested": pages,
                             "block_order": "service_list_order", "coordinates": "service_units_unspecified",
                             "layout_verified": False}}
    # Only this internal consumer sees remote asset URLs, before redaction.
    if _image_consumer is not None:
        _image_consumer(result)
    result["markdown"] = "\n\n".join(page["text"] for page in normalized)
    return _redact(result, token)


def _download_images(result, attempt, deadline):
    """Retain image evidence with generated filenames, never remote paths.

    Image failures remain explicit warnings; a downloaded image still requires
    visual verification. No Authorization, redirects, arbitrary output paths,
    or unbounded response bodies are permitted.
    """
    opener = urllib.request.build_opener(_NoRedirect())
    paths, failures, total_bytes, count = [], [], 0, 0
    suffixes = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}

    def fetch_image(request):
        for retry in range(3):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            try:
                return opener.open(request, timeout=min(remaining, 30))
            except urllib.error.HTTPError as exc:
                transient = exc.code in {408, 429, 500, 502, 503, 504}
                exc.close()
                if not transient or retry == 2:
                    raise
            except (OSError, urllib.error.URLError):
                if retry == 2:
                    raise
            time.sleep(min(.2 * 2 ** retry, max(0, deadline - time.monotonic())))

    for page in result["pages"]:
        for category in ("images", "outputImages"):
            refs = page.get(category, {})
            entries = list(refs.items()) if isinstance(refs, dict) else list(enumerate(refs))
            updated = dict(refs) if isinstance(refs, dict) else list(refs)
            for index, (key, url) in enumerate(entries, 1):
                count += 1
                try:
                    if count > 64 or total_bytes >= 100 * 1024 * 1024:
                        raise ValueError()
                    safe_url(url)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError()
                    request = urllib.request.Request(url, headers={"Accept": "image/*"})
                    binary = bytearray()
                    with fetch_image(request) as response:
                        mime = response.headers.get_content_type()
                        # Ignore remote filenames entirely; suffix is a bounded
                        # allowlisted hint. SVG/HTML cannot become active media.
                        suffix = Path(urllib.parse.urlsplit(url).path).suffix.lower()
                        if suffix not in suffixes:
                            suffix = Path(str(key)).suffix.lower()
                        if suffix not in suffixes:
                            suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif", "image/bmp": ".bmp", "image/tiff": ".tiff"}.get(mime)
                        if suffix is None or mime in {"image/svg+xml", "text/html", "application/xhtml+xml"}:
                            raise ValueError()
                        while True:
                            if time.monotonic() >= deadline:
                                raise TimeoutError()
                            chunk = response.read(65536)
                            if not chunk:
                                break
                            binary.extend(chunk)
                            if len(binary) > 20 * 1024 * 1024 or total_bytes + len(binary) > 100 * 1024 * 1024:
                                raise ValueError()
                    if not binary:
                        raise ValueError()
                    relative = f"media/page-{page['page']:04d}-{category.lower()}-{index:03d}{suffix}"
                    target = attempt / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(binary)
                    total_bytes += len(binary)
                    paths.append(str(target))
                    updated[key] = relative
                    if category == "images":
                        # Replace destinations only, so untrusted keys cannot
                        # rewrite arbitrary prose or escape the attempt folder.
                        for original in (str(key), url):
                            page["text"] = page["text"].replace("](" + original + ")", "](" + relative + ")").replace("](<" + original + ">)", "](<" + relative + ">)")
                except (PaddleJobsError, OSError, ValueError, TypeError):
                    warning = f"media_download_failed: page={page['page']} category={category} item={index}"
                    page["warnings"].append(warning)
                    failures.append(warning)
            page[category] = updated
        page["warnings"].append("downloaded_images_require_visual_review")
    result["warnings"].extend(failures)
    return paths


def progress(output_dir):
    """Safe persisted state snapshots; no network requests or credential values."""
    return ocr_job_store.progress(output_dir)


def recognize(source, output_dir, *, config, pages=None, task="ocr", progress_callback=None, **kwargs):
    """Resume within this batch-local output scope, or reuse verified artifacts.

    waiting is retryable by calling recognize again. submission_unknown permits
    only batchId reconciliation, never an automatic POST. No exactly-once remote
    execution guarantee is claimed. Terminal failures retain their remote ID.
    """
    started = time.monotonic()
    config = validate_config(config)
    # Preserve configuration validation even when a prior result is reusable.
    _token(config)
    if pages is not None and (not re.fullmatch(r"[1-9]\d*(?:-[1-9]\d*)?", str(pages))
                              or int(str(pages).split("-")[0]) > int(str(pages).split("-")[-1])):
        raise PaddleJobsError("invalid_config")
    try:
        source = Path(source).expanduser().resolve()
        if source.suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"} or source.stat().st_size > MAX_FILE:
            raise OSError()
        binary = source.read_bytes()
        if not binary or len(binary) > MAX_FILE:
            raise OSError()
        identity = ocr_job_store.binding(binary, config, OPTION_KEYS, pages=pages,
                                         task=task, file_type=source.suffix)
    except (OSError, TypeError, ValueError):
        raise PaddleJobsError("invalid_config") from None
    store = ocr_job_store.JobStore(output_dir, identity, progress_callback=progress_callback)
    try:
        with store.lock(timeout=0):
            receipt = store.load()
            if receipt["phase"] == "succeeded":
                cached = store.verify_result()
                if cached is not None:
                    return cached
                store.save(phase="polling", last_error="cache_corrupt")
            if store.receipt["phase"] == "failed":
                raise PaddleJobsError(store.receipt["last_error"] or "job_failed")
            attempt = store.directory
            media = []
            def consume_images(result):
                media.extend(_download_images(result, attempt, started + config["timeout_seconds"]))
            try:
                result = run_job(config, file_path=str(source), pages=pages,
                                 _image_consumer=consume_images, job_store=store)
                markdown, output = attempt / "result.md", attempt / "result.json"
                raw_path = attempt / "raw-result.jsonl"
                ocr_job_store.atomic_write(raw_path, result.pop("raw_jsonl").encode())
                result.pop("markdown")
                ocr_job_store.atomic_write(markdown, "\n\n".join(page["text"] for page in result["pages"]).encode())
                result["source"] = str(source)
                result["artifacts"] = {"attempt_dir": str(attempt), "markdown": str(markdown), "json": str(output), "images": media,
                                       "raw_jsonl": str(raw_path)}
                ocr_job_store.atomic_write(output, json.dumps(result, ensure_ascii=False, indent=2).encode())
                store.complete(result)
                return result
            except PaddleJobsError as exc:
                code = exc.code
                if code in {"timeout", "network_error"} or (code == "http_error" and exc.details.get("http_status") in {408, 429, 500, 502, 503, 504}):
                    code = "waiting" if store.receipt.get("job_id") else "submission_unknown"
                if code == "http_error":
                    remote_code = str(exc.details.get("error_code", ""))
                    if exc.details.get("http_status") == 404 or remote_code == "11001":
                        code = "job_not_found"
                    elif remote_code == "11002":
                        code = "job_expired"
                terminal = code in {"job_failed", "job_not_found", "job_expired", "submission_conflict", "unsafe_redirect"}
                phase = "failed" if terminal else ("submission_unknown" if code == "submission_unknown" else store.receipt["phase"])
                store.save(phase=phase, last_error=code if code in ocr_job_store.ERROR_CODES else "invalid_result")
                snapshot = ocr_job_store._safe_progress(store.receipt)
                raise PaddleJobsError(code, job_id=store.receipt.get("job_id"), phase=phase,
                                      retryable=not terminal, progress=snapshot,
                                      **{key: value for key, value in exc.details.items() if key in {"http_status", "error_code", "error_message"}}) from None
    except ocr_job_store.StoreError as exc:
        receipt = store.receipt or {}
        raise PaddleJobsError(exc.code, job_id=receipt.get("job_id"), phase=receipt.get("phase"),
                              retryable=exc.code in {"waiting", "storage_error"},
                              progress=ocr_job_store._safe_progress(receipt) if receipt else None) from None
    except OSError:
        receipt = store.receipt or {}
        raise PaddleJobsError("storage_error", job_id=receipt.get("job_id"), phase=receipt.get("phase"), retryable=True) from None


def main():
    # Host sends transient draft/token through stdin, never argv or a temp file.
    import sys
    try:
        payload = json.load(sys.stdin)
        result = run_job(payload["config"], file_url=payload.get("file_url"), file_path=payload.get("file_path"))
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    except PaddleJobsError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "message": str(exc), **exc.details}))
    except Exception:
        print(json.dumps({"ok": False, "code": "invalid_result", "message": "OCR test failed."}))


if __name__ == "__main__":
    main()
