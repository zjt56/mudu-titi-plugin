"""Local OCR and native PDF evidence adapter (no network or model imports).

Configure ``python`` and ``script`` in MUDU_OCR_CONFIG or ocr-config.json beside
this module. Relative executable/script paths resolve beside that config file.
``recognize`` accepts one page or an inclusive range (e.g. ``pages='2-4'``).
``read_pdf`` runs PyMuPDF in that same Python environment without loading OCR;
its ``needs_ocr`` pages can be passed individually to recognize(force_ocr=True).
Each call creates a unique attempt directory under output_dir. Returned artifact
paths are absolute; native PDF boxes use PDF page coordinates in points.
OCR text never implies bounding boxes or confidence scores.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time


class OCRBackendError(RuntimeError):
    """Terminal configuration, execution or result failure; safe to report."""


def _number(value, name, low, high, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OCRBackendError(f"{name} must be a number")
    if not math.isfinite(value) or not low <= value <= high:
        raise OCRBackendError(f"{name} must be between {low} and {high}")
    if integer and int(value) != value:
        raise OCRBackendError(f"{name} must be an integer")
    return int(value) if integer else float(value)


def _validate_config(data, base):
    if not isinstance(data, dict):
        raise OCRBackendError("OCR configuration must be a JSON object")
    config = dict(data)
    for key in ("python", "script"):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            raise OCRBackendError(f"OCR configuration requires a nonempty {key} file path")
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = base / path
        # Keep the venv executable's symlink path: resolving it loses the venv.
        path = Path(os.path.abspath(path))
        if not path.is_file():
            raise OCRBackendError(f"OCR {key} file does not exist: {path}")
        if key == "python" and os.name != "nt" and not os.access(path, os.X_OK):
            raise OCRBackendError(f"OCR python is not executable: {path}")
        config[key] = str(path)
    config["timeout_seconds"] = _number(config.get("timeout_seconds", 1800), "timeout_seconds", 1, 1800)
    config["lock_timeout_seconds"] = _number(config.get("lock_timeout_seconds", 1800), "lock_timeout_seconds", 1, 1800)
    config["dpi"] = _number(config.get("dpi", 200), "dpi", 36, 300, integer=True)
    config["preview_dpi"] = _number(config.get("preview_dpi", 120), "preview_dpi", 36, 150, integer=True)
    for key in ("max_new_tokens", "max_pixels"):
        if key in config:
            config[key] = _number(config[key], key, 1, 2**31 - 1, integer=True)
    return config


def load_config(config_path=None) -> dict:
    """Load explicit path, then MUDU_OCR_CONFIG, then module-local config.

    Missing/invalid explicit configuration is terminal; no implicit model search.
    """
    chosen = config_path if config_path is not None else os.environ.get("MUDU_OCR_CONFIG")
    path = Path(chosen).expanduser() if chosen else Path(__file__).with_name("ocr-config.json")
    path = path.absolute()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # 仅当"默认配置文件不存在"时降级（新装机器常见）：用当前 Python + 本脚本直接解析 PDF 文本。
        # 文件存在但内容非法、或显式指定了配置，都保持原契约：直接报错，不掩盖配置问题。
        if chosen is None and isinstance(exc, FileNotFoundError):
            fallback = {"python": sys.executable, "script": str(Path(__file__).resolve()),
                        "fallback": True}
            try:
                return _validate_config(fallback, Path(__file__).resolve().parent)
            except OCRBackendError:
                pass
        raise OCRBackendError(f"Cannot load OCR configuration {path}: {exc}") from exc
    return _validate_config(data, path.parent)


def _config(config):
    return load_config() if config is None else _validate_config(config, Path.cwd())


def _source(source):
    path = Path(source).expanduser().resolve()
    if not path.is_file():
        raise OCRBackendError(f"Source file does not exist: {path}")
    return path


def _attempt(output_dir, prefix):
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix + "-", dir=root))


@contextmanager
def _model_lock(config):
    """OS advisory lock: survives concurrent batches, releases on process death.

    Never delete the lock file or infer staleness from age/PIDs: replacing an
    inode can admit two owners. All calls for the same configured script share
    this lock; unrelated external CLI callers must use the same coordination.
    """
    identity = str(Path(config["script"]).resolve()).encode("utf-8")
    key = hashlib.sha256(identity).hexdigest()
    lock_path = Path(tempfile.gettempdir()) / ("mudu-ocr-" + key + ".lock")
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + config["lock_timeout_seconds"]
        acquired = False
        while not acquired:
            try:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                import errno
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise OCRBackendError(f"Cannot acquire OCR model lock: {exc}") from exc
                if time.monotonic() >= deadline:
                    raise OCRBackendError(f"OCR model lock timed out after {config['lock_timeout_seconds']:g}s") from exc
                time.sleep(0.1)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _run(command, config, log):
    with log.open("w", encoding="utf-8") as capture:
        try:
            result = subprocess.run(command, stdout=capture, stderr=subprocess.STDOUT,
                                    timeout=config["timeout_seconds"], shell=False,
                                    cwd=str(Path(config["script"]).parent))
        except subprocess.TimeoutExpired as exc:
            message = f"OCR subprocess timed out after {config['timeout_seconds']:g}s; log: {log}"
            capture.write("\n" + message + "\n")
            raise OCRBackendError(message) from exc
        except OSError as exc:
            message = f"Cannot start OCR subprocess: {exc}; log: {log}"
            capture.write("\n" + message + "\n")
            raise OCRBackendError(message) from exc
        if result.returncode != 0:
            raise OCRBackendError(f"OCR subprocess failed (exit {result.returncode}); log: {log}")


def _read_result(path):
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OCRBackendError(f"Missing or invalid OCR result {path}: {exc}") from exc
    if not isinstance(result, dict) or not isinstance(result.get("pages"), list) or not result["pages"]:
        raise OCRBackendError(f"OCR result must contain a nonempty pages list: {path}")
    seen = set()
    for page in result["pages"]:
        if not isinstance(page, dict) or type(page.get("page")) is not int or page["page"] < 1 or not isinstance(page.get("text"), str):
            raise OCRBackendError(f"Malformed OCR page in {path}")
        if page["page"] in seen:
            raise OCRBackendError(f"Duplicate OCR page in {path}")
        seen.add(page["page"])
    return result


def recognize(source: Path, output_dir: Path, *, force_ocr=False, pages=None,
              task="ocr", config=None) -> dict:
    """Run local CLI once under a shared model lock, returning pages/artifacts.

    Failure and timeout raise OCRBackendError (with log path), never retry or
    return success. Empty recognized text remains empty; no content is invented.
    """
    settings = _config(config)
    if settings.get("fallback"):
        raise OCRBackendError(
            "OCR 识别未配置：当前只有 PDF 文本解析可用（未找到 ocr-config.json）。"
            "如需识别扫描件/图片型试卷，请配置 OCR 服务（app/workflow/ocr-config.json 或环境变量 MUDU_OCR_CONFIG）。"
        )
    source = _source(source)
    if source.suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
        raise OCRBackendError(f"Unsupported OCR source format: {source.suffix}")
    if task not in {"ocr", "table", "formula", "chart", "spotting", "seal"}:
        raise OCRBackendError(f"Unsupported OCR task: {task}")
    selection = None if pages is None else str(pages)
    if selection is not None:
        if not re.fullmatch(r"[1-9]\d*(?:-[1-9]\d*)?", selection):
            raise OCRBackendError("pages must be a positive page number or inclusive range, e.g. 2-4")
        bounds = list(map(int, selection.split("-")))
        if bounds[-1] < bounds[0]:
            raise OCRBackendError("pages range must be ascending")
    attempt = _attempt(output_dir, "ocr")
    markdown, result_path, log = attempt / "result.md", attempt / "result.json", attempt / "subprocess.log"
    command = [settings["python"], settings["script"], str(source), "-o", str(markdown),
               "--task", task, "--dpi", str(settings["dpi"]), "--no-stdout"]
    if force_ocr:
        command.append("--force-ocr")
    if selection is not None:
        command.extend(["--pages", selection])
    for key in ("max_new_tokens", "max_pixels"):
        if key in settings:
            command.extend(["--" + key.replace("_", "-"), str(settings[key])])
    with _model_lock(settings):
        _run(command, settings, log)
    raw = _read_result(result_path)
    if not markdown.is_file():
        raise OCRBackendError(f"OCR subprocess did not produce Markdown: {markdown}; log: {log}")
    normalized = []
    for page in raw["pages"]:
        method = page.get("method")
        if method not in {"text", "ocr"}:
            raise OCRBackendError(f"Unsupported OCR result page method: {method!r}; log: {log}")
        normalized.append({"page": page["page"], "text": page["text"], "method": method,
                           "seconds": page.get("seconds"), "blocks": [],
                           "warnings": ["coordinates_absent"]})
    return {"source": str(source), "pages": normalized,
            "artifacts": {"attempt_dir": str(attempt), "markdown": str(markdown),
                          "json": str(result_path), "log": str(log)},
            "warnings": ["coordinates_absent: local OCR CLI returns page text without bounding boxes"],
            "provenance": {"backend": "local_ocr", "python": settings["python"],
                           "script": settings["script"], "model": raw.get("model"),
                           "device": raw.get("device"), "task": task, "force_ocr": bool(force_ocr),
                           "pages_requested": selection, "command": command}}


def read_pdf(source: Path, output_dir: Path, config=None) -> dict:
    """Extract PDF text, boxes, image evidence and previews for suspect pages.

    Model-free worker runs in configured OCR venv (which supplies PyMuPDF).
    ``quality_reasons`` are routing heuristics, not confidence assessments.
    Embedded image bytes are retained, with every occurrence's page bbox.
    """
    settings = _config(config)
    source = _source(source)
    if source.suffix.lower() != ".pdf":
        raise OCRBackendError("read_pdf requires a PDF source")
    attempt = _attempt(output_dir, "pdf-native")
    log, result_path = attempt / "subprocess.log", attempt / "result.json"
    command = [settings["python"], str(Path(__file__).resolve()), "--inspect-pdf",
               str(source), str(attempt), str(settings["preview_dpi"])]
    _run(command, settings, log)
    result = _read_result(result_path)
    result["artifacts"] = {"attempt_dir": str(attempt), "json": str(result_path), "log": str(log),
                           "images": sorted({image["path"] for page in result["pages"] for image in page["images"]}),
                           "previews": [page["preview"] for page in result["pages"] if page.get("preview")]}
    result["provenance"] = {"backend": "pymupdf", "python": settings["python"], "command": command,
                            "coordinate_system": "PDF page coordinates in points, top-left origin"}
    return result


inspect_pdf = read_pdf


def _quality_reasons(text):
    reasons = []
    if len(text.strip()) < 80:
        reasons.append("short_text")
    if "\ufffd" in text or "\x00" in text:
        reasons.append("suspicious_characters")
    # Latin/alphanumeric runs only: continuous CJK paragraphs are legitimate.
    if re.search(r"[A-Za-z0-9]{40,}", text):
        reasons.append("long_glued_run")
    return reasons


def _pdf_worker(source, output, preview_dpi):
    import pymupdf

    output = Path(output)
    pages = []
    with pymupdf.open(str(source)) as document:
        if document.needs_pass:
            raise OCRBackendError("PDF is encrypted and requires a password")
        for number, page in enumerate(document, 1):
            text = page.get_text("text").strip()
            reasons = _quality_reasons(text)
            blocks = []
            for block in page.get_text("blocks"):
                if len(block) >= 7 and block[6] == 0:
                    blocks.append({"bbox": list(block[:4]), "text": block[4], "type": "text"})
            images = []
            inline_blocks = None
            for index, image in enumerate(page.get_image_info(xrefs=True), 1):
                xref = image.get("xref", 0)
                if xref:
                    extracted = document.extract_image(xref)
                    if not extracted or not extracted.get("image"):
                        raise OCRBackendError(f"Cannot export embedded image xref {xref} on page {number}")
                    image_bytes, extension = extracted["image"], extracted["ext"]
                else:
                    if inline_blocks is None:
                        inline_blocks = [b for b in page.get_text("dict")["blocks"] if b.get("type") == 1]
                    matching = next((b for b in inline_blocks if tuple(b["bbox"]) == tuple(image["bbox"])), None)
                    if matching is None:
                        raise OCRBackendError(f"Cannot export inline image on page {number}")
                    image_bytes, extension = matching["image"], matching["ext"]
                if not re.fullmatch(r"[A-Za-z0-9]+", extension):
                    raise OCRBackendError("Invalid embedded image extension")
                path = output / f"page-{number:04d}-image-{index:03d}.{extension}"
                path.write_bytes(image_bytes)
                images.append({"path": str(path), "bbox": list(image["bbox"]), "xref": xref,
                               "width": image.get("width"), "height": image.get("height")})
            preview = None
            if reasons:
                path = output / f"page-{number:04d}-preview.png"
                # Bound longest edge as well as DPI for unusually large PDF pages.
                scale = min(preview_dpi / 72, 2400 / max(page.rect.width, page.rect.height, 1))
                page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB,
                                alpha=False).save(str(path))
                preview = str(path)
            pages.append({"page": number, "text": text, "method": "text", "blocks": blocks,
                          "images": images, "width": page.rect.width, "height": page.rect.height,
                          "needs_ocr": bool(reasons), "quality_reasons": reasons, "preview": preview})
    (output / "result.json").write_text(json.dumps({"source": str(source), "pages": pages,
                                                   "warnings": []}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 5 or sys.argv[1] != "--inspect-pdf":
        raise SystemExit("Usage: ocr_backend.py --inspect-pdf SOURCE OUTPUT_DIR PREVIEW_DPI")
    _pdf_worker(sys.argv[2], sys.argv[3], int(sys.argv[4]))
