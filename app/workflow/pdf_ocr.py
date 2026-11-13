"""Acquire OCR evidence for every PDF registered in a batch manifest.

Call ``run(batch_dir, manifest, config=None)`` after registering uploaded inputs.
The report and snapshots are batch-local; OCR evidence does not certify layout or
content. Paddle jobs persist receipts in a batch-local source scope; subsequent
calls resume or reuse the existing task instead of resubmitting it.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import os
from pathlib import Path
import tempfile
import time

from . import ocr_backend

REPORT = "01_source/pdf-ocr.json"
MARKDOWN = "01_source/pdf-ocr.md"


def _load_report(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def page_evidence(batch_dir, source_sha256=None):
    """Recover per-page method/images from the retained service result.

    Older ``pdf-ocr.json`` records kept only page text, so the projection treated
    OCR pages as native text and left ``<div>/###/LaTeX`` in 正文. The acquisition
    JSON next to the result is the service's own evidence, so re-reading it
    repairs metadata without calling the service again.
    """
    batch = Path(batch_dir)
    report = _load_report(batch / REPORT)
    rows = {}
    for item in report.get("sources") or []:
        if not isinstance(item, dict) or item.get("status") != "completed":
            continue
        digest = str(item.get("sha256") or "")
        if source_sha256 and digest and digest != source_sha256:
            continue
        stored = {}
        result_path = item.get("result_path")
        if isinstance(result_path, str):
            try:
                payload = json.loads((batch / result_path).read_text(encoding="utf-8-sig"))
            except (OSError, ValueError, TypeError):
                payload = {}
            result = payload.get("result") if isinstance(payload, dict) and isinstance(
                payload.get("result"), dict) else payload
            for page in (result.get("pages") or []) if isinstance(result, dict) else []:
                if isinstance(page, dict) and isinstance(page.get("page"), int):
                    stored[page["page"]] = page
        for page in item.get("pages") or []:
            if not isinstance(page, dict) or not isinstance(page.get("page"), int):
                continue
            evidence = stored.get(page["page"]) or {}
            method = page.get("method") or evidence.get("method") or (
                "ocr" if page.get("blocks") or page.get("images") else None)
            images = page.get("images") or evidence.get("images") or {}
            row = rows.setdefault(page["page"], {})
            if method:
                row["method"] = method
            if images:
                row["images"] = images
    return rows


def _write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".pdf-ocr-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _write_markdown(path, text):
    """Persist service-ordered Markdown atomically, without rewriting its content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".pdf-ocr-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def run(batch, manifest, config=None):
    """Return and persist a per-input report, submitting each distinct SHA once.

    Manifest paths may be absolute or batch-relative. A supplied input SHA must
    match the copied bytes. A failed or uncertain call is recorded and never
    automatically resubmitted during this invocation. Results contain the full
    backend response, including page text, blocks, images and warnings.
    """
    batch = Path(batch).expanduser().resolve()
    if not isinstance(manifest, dict) or not isinstance(manifest.get("inputs"), dict):
        raise ValueError("manifest.inputs must be an object")
    root = batch / "01_source" / "pdf-ocr"
    root.mkdir(parents=True, exist_ok=True)
    report = {"version": 2, "status": "running", "report_path": REPORT,
              "markdown_path": MARKDOWN, "updated_at": time.time(), "sources": []}

    def publish():
        report["updated_at"] = time.time()
        _write_report(batch / REPORT, report)

    publish()
    seen = {}
    for key, entry in manifest["inputs"].items():
        if not isinstance(entry, dict):
            continue
        value = entry.get("path")
        if not isinstance(value, str) or Path(value).suffix.lower() != ".pdf":
            continue
        item = {"input": key, "path": value, "status": "failed", "sha256": None}
        report["sources"].append(item)
        try:
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = batch / path
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            item["sha256"] = digest
            expected = entry.get("sha256")
            if expected and expected != digest:
                raise ValueError("registered SHA-256 does not match source")
            if digest in seen:
                previous = seen[digest]
                item.update({k: v for k, v in previous.items()
                             if k not in ("input", "path")})
                item["duplicate_of"] = previous["input"]
                continue
            # Reserve the digest before submitting, including on uncertain failure.
            seen[digest] = item
            source_dir = root / digest
            source_dir.mkdir(parents=True, exist_ok=True)
            if source_dir.is_symlink():
                raise ValueError("unsafe PDF evidence directory")
            snapshot = source_dir / "source.pdf"
            if snapshot.exists():
                if snapshot.is_symlink() or hashlib.sha256(snapshot.read_bytes()).hexdigest() != digest:
                    raise ValueError("registered SHA-256 does not match source")
            else:
                fd = os.open(snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
            item["snapshot"] = snapshot.relative_to(batch).as_posix()
            item["result_dir"] = source_dir.relative_to(batch).as_posix()
            # Every uploaded PDF is OCR'd in full. JSON and service-ordered
            # Markdown are acquired even with OCR assist off or a clean text
            # layer. Word stays authoritative when supplied; PDF-only extraction
            # consumes this OCR body without requiring native PDF parsing.
            def on_progress(progress):
                if not isinstance(progress, dict):
                    return
                safe = {}
                for field in ("phase", "state", "job_id", "batch_id", "created_at", "updated_at",
                              "total_pages", "extracted_pages", "retry_after_seconds", "poll_attempts"):
                    value = progress.get(field)
                    if field in {"phase", "state", "job_id", "batch_id"}:
                        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value):
                            safe[field] = value
                    elif type(value) in (int, float) and math.isfinite(value) and value >= 0:
                        safe[field] = value
                item["progress"] = safe
                item["status"] = "running"
                publish()

            item["status"] = "running"
            publish()
            result = ocr_backend.recognize(snapshot, source_dir, force_ocr=True,
                                           config=config, mandatory_pdf=True,
                                           progress_callback=on_progress)
            if not isinstance(result, dict) or not isinstance(result.get("pages"), list) or not result["pages"]:
                raise ValueError("OCR returned no pages")
            if any(not isinstance(page, dict) or not isinstance(page.get("text"), str)
                   for page in result["pages"]):
                raise ValueError("OCR returned malformed page text")
            # 强制 OCR 不接受“原生文字层冒充识别结果”，也不接受整卷空文字：
            # 这两种都证明识别没有真正完成，不能当通过（硬规则28）。
            if any(page.get("method") == "text" for page in result["pages"]):
                raise ValueError("OCR returned native text instead of recognition")
            if not any(page.get("text", "").strip() for page in result["pages"]):
                raise ValueError("OCR returned empty document text")
            result_path = source_dir / "acquisition.json"
            markdown_path = source_dir / "acquisition.md"
            markdown = "\n\n".join(page["text"] for page in result["pages"])
            _write_markdown(markdown_path, markdown)
            _write_report(result_path, {"source_sha256": digest,
                                       "markdown_path": markdown_path.relative_to(batch).as_posix(),
                                       "result": result})
            item.update(status="completed", result_path=result_path.relative_to(batch).as_posix(),
                        markdown_path=markdown_path.relative_to(batch).as_posix(),
                        # method/images 是投影的必要证据：缺 method 时调用方会把
                        # OCR 页当成原生文字层，跳过 <div>/###/$\underline$ 投影，
                        # 正文里就会残留版面代码；缺 images 时图片无法落盘成 [IMG]。
                        pages=[{"page": page.get("page"), "text": page.get("text", ""),
                                "method": page.get("method") or (
                                    "ocr" if page.get("blocks") or page.get("images") else None),
                                "images": page.get("images") or {},
                                "blocks": page.get("blocks") or [],
                                "warnings": page.get("warnings") or []}
                               for page in result["pages"]],
                        warnings=result.get("warnings") or [])
        except Exception as exc:
            # Backend exceptions can contain private configuration; expose a safe
            # category, rather than serializing arbitrary exception messages.
            item["error"] = ocr_backend.safe_error(exc)
            details = item["error"].get("details", {})
            if item["error"].get("code") == "waiting" and details.get("job_id"):
                item["status"] = "waiting"
                item["progress"] = {**item.get("progress", {}), "job_id": details["job_id"],
                                    "phase": details.get("phase", "waiting"),
                                    "retry_after_seconds": 15}
            else:
                item["status"] = "failed"
        publish()
    if any(item["status"] == "failed" for item in report["sources"]):
        report["status"] = "failed"
    elif any(item["status"] == "waiting" for item in report["sources"]):
        report["status"] = "waiting"
    elif not report["sources"]:
        report["status"] = "no_pdfs"
    else:
        report["status"] = "completed"
    # Aggregate only successful, distinct sources in manifest/service order.
    # Failed sources stay explicit in JSON rather than masquerading as OCR text.
    sections = []
    for item in report["sources"]:
        if item["status"] == "completed" and "duplicate_of" not in item:
            markdown = (batch / item["markdown_path"]).read_text(encoding="utf-8")
            sections.append(f"## {item['input']}\n\n{markdown}")
    _write_markdown(batch / MARKDOWN, "\n\n".join(sections))
    publish()
    return report
