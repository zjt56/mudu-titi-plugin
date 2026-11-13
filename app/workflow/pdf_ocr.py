"""Acquire OCR evidence for every PDF registered in a batch manifest.

Call ``run(batch_dir, manifest, config=None)`` after registering uploaded inputs.
The report and snapshots are batch-local; OCR evidence does not certify layout or
content. Re-running explicitly submits again; no submission is retried in a run.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

from . import ocr_backend

REPORT = "01_source/pdf-ocr.json"
MARKDOWN = "01_source/pdf-ocr.md"


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
    report = {"version": 1, "status": "completed", "report_path": REPORT,
              "markdown_path": MARKDOWN, "sources": []}
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
            source_dir = Path(tempfile.mkdtemp(prefix=digest[:16] + "-", dir=root))
            snapshot = source_dir / "source.pdf"
            snapshot.write_bytes(data)
            item["snapshot"] = snapshot.relative_to(batch).as_posix()
            item["result_dir"] = source_dir.relative_to(batch).as_posix()
            # Every uploaded PDF is OCR'd in full. JSON and service-ordered
            # Markdown are acquired even with OCR assist off or a clean text
            # layer. Word stays authoritative when supplied; PDF-only extraction
            # consumes this OCR body without requiring native PDF parsing.
            result = ocr_backend.recognize(snapshot, source_dir, force_ocr=True,
                                           config=config, mandatory_pdf=True)
            if not isinstance(result, dict) or not isinstance(result.get("pages"), list) or not result["pages"]:
                raise ValueError("OCR returned no pages")
            if any(not isinstance(page, dict) or not isinstance(page.get("text"), str)
                   for page in result["pages"]):
                raise ValueError("OCR returned malformed page text")
            if any(page.get('method') == 'text' for page in result['pages']):
                raise ValueError("OCR returned native text instead of recognition")
            if not any(page['text'].strip() for page in result['pages']):
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
                        pages=[{"page": page.get("page"), "text": page.get("text", ""),
                                "blocks": page.get("blocks") or [],
                                "warnings": page.get("warnings") or []}
                               for page in result["pages"]],
                        warnings=result.get("warnings") or [])
        except Exception as exc:
            # Backend exceptions can contain private configuration; expose a safe
            # category, rather than serializing arbitrary exception messages.
            item["error"] = {"type": type(exc).__name__, "message": (
                str(exc) if isinstance(exc, ValueError) and str(exc) in {
                    "registered SHA-256 does not match source", "OCR returned no pages",
                    "OCR returned malformed page text", "OCR returned empty document text",
                    "OCR returned native text instead of recognition"} else "PDF acquisition or OCR failed; inspect configuration and source")}
    if any(item["status"] != "completed" for item in report["sources"]):
        report["status"] = "failed"
    elif not report["sources"]:
        report["status"] = "no_pdfs"
    # Aggregate only successful, distinct sources in manifest/service order.
    # Failed sources stay explicit in JSON rather than masquerading as OCR text.
    sections = []
    for item in report["sources"]:
        if item["status"] == "completed" and "duplicate_of" not in item:
            markdown = (batch / item["markdown_path"]).read_text(encoding="utf-8")
            sections.append(f"## {item['input']}\n\n{markdown}")
    _write_markdown(batch / MARKDOWN, "\n\n".join(sections))
    _write_report(batch / REPORT, report)
    return report
