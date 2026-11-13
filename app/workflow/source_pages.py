"""Source-bound full-page images for visual layout decisions; never OCR text.

``prepare(batch, config=None)`` returns page records; ``load_verified(batch)``
rechecks the current Word/PDF, body text and every saved PNG before returning a
``status='verified'`` manifest. Missing rendering is explicit ``unavailable``.
Only this module's 01_source/visual-pages directory is written.

Config: pass a dict or set MUDU_SOURCE_PAGES_CONFIG to a JSON file containing
``{"command": ["renderer", "--input", "{input}", "--output-dir", "{output_dir}"]}``.
Commands run without a shell and must create output_dir/manifest.json containing
``{source_sha256, full_page: true, page_count, pages: [{page, path}]}``; page
numbers must cover 1..page_count. The official dsoffice manifest is also accepted.
Use {dpi} as an optional argument. Explicit config is strict: no auto fallback.

Auto mode uses dsoffice on PATH, or the documented public libreoffice-kit API
resolved from DSH_INSTALL_DIR by DSH_NODE_EXECUTABLE. Electron native engines use
its documented EnginePackageResolver for .asar.unpacked assets. No installation,
profile inspection, private service calls, OCR or package downloads are performed.
Existing current PDF-native/PDF-OCR *page.preview* evidence can be copied when no
renderer is available; embedded page.images / media are never page substitutes.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import zlib

VERSION = "source-pages-v1"
ROOT = "01_source/visual-pages"
MANIFEST = ROOT + "/manifest.json"
TEXT = "01_source/正文.txt"

# Public API from @deepseek-ai/libreoffice-kit README (0.2.3); resolver matches
# the documented Electron packaging contract, not a private DSH service endpoint.
_OFFICE_SCRIPT = r"""
const {createRequire} = require('node:module');
const {pathToFileURL} = require('node:url');
const fs = require('node:fs');
(async () => {
  const entry = require.resolve('@deepseek-ai/libreoffice-kit', {
    paths: [process.env.DSH_INSTALL_DIR]
  });
  const local = createRequire(entry);
  const kit = await import(pathToFileURL(entry).href);
  const converter = await kit.createConverter({
    timeoutMs: Number(process.argv[4]),
    resolveEnginePackage(name) {
      const file = local.resolve(name + '/package.json');
      return name.endsWith('-wasm') ? file :
        file.replace(/\.asar([\\/])/i, '.asar.unpacked$1');
    }
  });
  try {
    await converter.renderImages({inputPath: process.argv[1],
      outputDir: process.argv[2], pages: 'all', dpi: Number(process.argv[3]),
      maxPages: 1000});
  } finally { await converter.dispose(); }
})().catch(() => { process.stderr.write('office-kit render unavailable\n'); process.exitCode = 1; });
"""


class SourcePagesError(ValueError):
    """Safe, bounded diagnostic; subprocess output is deliberately not exposed."""


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _path(batch, value):
    if not isinstance(value, str) or not value:
        raise SourcePagesError("source_path_missing")
    path = Path(value).expanduser()
    return path if path.is_absolute() else batch / path


def _identity(batch):
    try:
        state = _read(batch / "workflow/manifest.json")
    except FileNotFoundError:
        state = {}
    except (OSError, ValueError):
        raise SourcePagesError("batch_manifest_invalid") from None
    if not isinstance(state, dict):
        raise SourcePagesError("batch_manifest_invalid")
    entries = state.get("inputs", {})
    if not isinstance(entries, dict):
        raise SourcePagesError("batch_inputs_invalid")
    # Only original/source keys; answer attachments never become the exam source.
    candidates = []
    for key in ("docx", "source"):
        row = entries.get(key)
        if isinstance(row, dict) and isinstance(row.get("path"), str):
            candidates.append(row)
    if not candidates:
        try:
            extraction = _read(batch / "01_source/extraction.json")
        except (OSError, ValueError):
            extraction = {}
        value = extraction.get("source") or extraction.get("source_path")
        if isinstance(value, str) and value:
            candidates.append({"path": value, "sha256": extraction.get("source_sha256")})
        else:
            for name in ("source.docx", "source.doc", "source.pdf"):
                if (batch / "attachments" / name).is_file():
                    candidates.append({"path": "attachments/" + name})
    candidates.sort(key=lambda row: 0 if Path(row["path"]).suffix.lower() in {".docx", ".doc", ".odt"} else 1)
    if not candidates:
        raise SourcePagesError("source_missing")
    row = candidates[0]
    source = _path(batch, row["path"]).resolve()
    if source.suffix.lower() not in {".docx", ".doc", ".odt", ".pdf"}:
        raise SourcePagesError("source_format_unsupported")
    if not source.is_file():
        raise SourcePagesError("source_missing")
    data = source.read_bytes()
    digest = _sha(data)
    if row.get("sha256") and row["sha256"] != digest:
        raise SourcePagesError("source_sha256_mismatch")
    text = (batch / TEXT).read_bytes()
    return source, data, {"source_sha256": digest, "text_sha256": _sha(text),
                          "source_kind": source.suffix.lower().lstrip(".")}


def _settings(config):
    if config is None:
        chosen = os.environ.get("MUDU_SOURCE_PAGES_CONFIG")
        if chosen:
            try:
                config = _read(Path(chosen).expanduser())
            except (OSError, ValueError):
                raise SourcePagesError("renderer_config_unreadable") from None
        else:
            config = {}
    if not isinstance(config, dict):
        raise SourcePagesError("renderer_config_invalid")
    settings = dict(config)
    for key, default, low, high in (("dpi", 144, 72, 300),
                                    ("timeout_seconds", 180, 1, 1800)):
        value = settings.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise SourcePagesError("renderer_" + key + "_invalid")
        settings[key] = value
    if "command" in settings:
        command = settings["command"]
        if not isinstance(command, list) or not command or any(not isinstance(x, str) or not x for x in command):
            raise SourcePagesError("renderer_command_invalid")
        if not any("{input}" in arg for arg in command) or not any("{output_dir}" in arg for arg in command):
            raise SourcePagesError("renderer_command_requires_input_and_output_dir")
    return settings


def _run(command, settings, env=None):
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=settings["timeout_seconds"], env=env, check=False)
    except subprocess.TimeoutExpired:
        raise SourcePagesError("renderer_timeout") from None
    except OSError:
        raise SourcePagesError("renderer_unavailable") from None
    if result.returncode:
        raise SourcePagesError("renderer_failed")


def _render(source, output, settings):
    if "command" in settings:
        replacements = {"{input}": str(source), "{output_dir}": str(output), "{dpi}": str(settings["dpi"])}
        command = []
        for item in settings["command"]:
            for key, value in replacements.items():
                item = item.replace(key, value)
            command.append(item)
        _run(command, settings)
        return "command"
    executable = shutil.which("dsoffice")
    if executable:
        _run([executable, "render", "--input", str(source), "--output-dir", str(output),
              "--pages", "all", "--dpi", str(settings["dpi"])], settings)
        return "dsoffice"
    runtime, anchor = os.environ.get("DSH_NODE_EXECUTABLE"), os.environ.get("DSH_INSTALL_DIR")
    if runtime and anchor and Path(runtime).is_file():
        env = dict(os.environ, ELECTRON_RUN_AS_NODE="1")
        _run([runtime, "-e", _OFFICE_SCRIPT, str(source), str(output), str(settings["dpi"]),
              str(settings["timeout_seconds"] * 1000)], settings, env)
        return "office-kit"
    raise SourcePagesError("renderer_unavailable")


def _png(path):
    # Check the whole PNG container, including CRC and IEND, without optional
    # Pillow/PyMuPDF. A truncated/header-only file must not become page evidence.
    if path.stat().st_size > 100 * 1024 * 1024:
        raise SourcePagesError("page_image_too_large")
    data = path.read_bytes()
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise SourcePagesError("page_image_invalid")
    width, height = struct.unpack(">II", data[16:24])
    if width < 1 or height < 1 or width * height > 100_000_000:
        raise SourcePagesError("page_image_dimensions_invalid")
    offset, image_data = 8, False
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        end = offset + 12 + length
        if end > len(data):
            break
        kind = data[offset + 4:offset + 8]
        content = data[offset + 8:offset + 8 + length]
        checksum = struct.unpack(">I", data[end - 4:end])[0]
        if zlib.crc32(kind + content) != checksum:
            break
        if offset == 8 and (kind != b"IHDR" or length != 13):
            break
        image_data |= kind == b"IDAT" and length > 0
        if kind == b"IEND":
            if length == 0 and end == len(data) and image_data:
                return width, height
            break
        offset = end
    raise SourcePagesError("page_image_invalid")


def _contained(directory, value):
    path = Path(value)
    if not path.is_absolute():
        path = directory / path
    resolved = path.resolve()
    if not resolved.is_relative_to(directory.resolve()) or path.is_symlink() or not path.is_file():
        raise SourcePagesError("page_image_outside_output")
    return resolved


def _rendered(output, digest):
    try:
        result = _read(output / "manifest.json")
    except (OSError, ValueError):
        raise SourcePagesError("renderer_manifest_missing_or_invalid") from None
    if not isinstance(result, dict):
        raise SourcePagesError("renderer_manifest_invalid")
    official = result.get("schemaVersion") == 1 and result.get("rasterEngine") in {"libreoffice", "pdfium"}
    if not official and result.get("full_page") is not True:
        raise SourcePagesError("renderer_full_page_unproven")
    if result.get("sourceSha256", result.get("source_sha256")) != digest:
        raise SourcePagesError("rendered_source_sha256_mismatch")
    rows = result.get("images") if official else result.get("pages")
    count = result.get("pageCount", result.get("page_count"))
    if type(count) is not int or count < 1 or not isinstance(rows, list) or len(rows) != count:
        raise SourcePagesError("renderer_page_coverage_incomplete")
    normalized = []
    for number, row in enumerate(rows, 1):
        if not isinstance(row, dict) or type(row.get("page")) is not int or row["page"] != number:
            raise SourcePagesError("renderer_page_coverage_incomplete")
        value = row.get("path")
        if not isinstance(value, str):
            raise SourcePagesError("page_image_path_missing")
        normalized.append({"page": number, "path": str(_contained(output, value))})
    return normalized, result.get("missingFonts", [])


def _previews(batch, source_sha):
    """Only native acquisition.pages[].preview, never layout/embedded images."""
    try:
        extraction = _read(batch / "01_source/extraction.json")
    except (OSError, ValueError):
        extraction = {}
    candidates = []
    if extraction.get("source_sha256", extraction.get("sha256")) == source_sha:
        candidates.append(extraction)
    try:
        report = _read(batch / "01_source/pdf-ocr.json")
        for row in report.get("sources", []):
            if row.get("status") == "completed" and row.get("sha256") == source_sha:
                acquisition = _read(_path(batch, row["result_path"]))
                if acquisition.get("source_sha256") == source_sha:
                    candidates.append(acquisition.get("result", {}))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    for candidate in candidates:
        pages = candidate.get("pages")
        if not isinstance(pages, list) or not pages:
            continue
        result = []
        for number, row in enumerate(pages, 1):
            if not isinstance(row, dict) or row.get("page") != number or not isinstance(row.get("preview"), str):
                break
            path = _path(batch, row["preview"])
            if not path.is_file():
                break
            result.append({"page": number, "path": str(path)})
        if len(result) == len(pages):
            return result
    raise SourcePagesError("full_page_previews_unavailable")


def _write(path, payload):
    fd, temporary = tempfile.mkstemp(prefix=".manifest-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_verified(batch):
    """Read only: verify current bytes and complete page coverage, else unavailable."""
    batch = Path(batch).expanduser().resolve()
    try:
        _, _, identity = _identity(batch)
        report = _read(batch / MANIFEST)
        if not isinstance(report, dict) or report.get("version") != VERSION:
            raise SourcePagesError("page_manifest_invalid")
        if any(report.get(key) != value for key, value in identity.items()):
            raise SourcePagesError("page_manifest_stale")
        if report.get("status") != "verified":
            return {**identity, "status": "unavailable", "reason": report.get("reason", "render_unavailable"), "pages": []}
        directory = batch / ROOT
        snapshot = _contained(directory, report["source_snapshot"])
        if _sha(snapshot.read_bytes()) != identity["source_sha256"]:
            raise SourcePagesError("source_snapshot_sha256_mismatch")
        text = _contained(directory, report["text_snapshot"])
        if _sha(text.read_bytes()) != identity["text_sha256"]:
            raise SourcePagesError("text_snapshot_sha256_mismatch")
        pages = report["pages"]
        count = report["page_count"]
        if type(count) is not int or count < 1 or not isinstance(pages, list) or len(pages) != count:
            raise SourcePagesError("page_manifest_coverage_incomplete")
        for number, row in enumerate(pages, 1):
            if type(row.get("page")) is not int or row["page"] != number or row.get("full_page") is not True:
                raise SourcePagesError("page_manifest_coverage_incomplete")
            # Stored page paths are batch-relative, snapshots are ROOT-relative.
            value = row["path"]
            if Path(value).is_absolute():
                raise SourcePagesError("page_image_path_not_portable")
            path = _contained(directory, str(batch / value))
            if _sha(path.read_bytes()) != row["image_sha256"]:
                raise SourcePagesError("page_image_sha256_mismatch")
            if _png(path) != (row["width"], row["height"]):
                raise SourcePagesError("page_image_dimensions_mismatch")
        return report
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        reason = str(exc) if isinstance(exc, SourcePagesError) else "page_evidence_unavailable"
        return {"version": VERSION, "status": "unavailable", "reason": reason, "pages": []}


def prepare(batch, config=None):
    """Prepare complete page PNGs and manifest; unavailable returns [] with reason.

    Rendering uses an immutable source snapshot. No partial set is ever returned.
    Word originals are preferred, even when a companion PDF has usable previews.
    Explicit configuration always executes that renderer, so stale implicit cache
    cannot silently supersede a user's renderer choice.
    """
    batch = Path(batch).expanduser().resolve()
    # Do not create a ghost batch for an incorrect path.
    if not batch.is_dir():
        return []
    directory = batch / ROOT
    directory.mkdir(parents=True, exist_ok=True)
    report = {"version": VERSION, "status": "unavailable", "pages": []}
    try:
        source, data, identity = _identity(batch)
        report.update(identity)
        settings = _settings(config)
        explicit = config is not None or bool(os.environ.get("MUDU_SOURCE_PAGES_CONFIG"))
        if not explicit:
            verified = load_verified(batch)
            if verified.get("status") == "verified":
                return verified["pages"]
        # A unique generation preserves previously verified files if a renderer
        # fails, while the top-level manifest makes the failed attempt explicit.
        attempt = Path(tempfile.mkdtemp(prefix="render-", dir=directory))
        snapshot = attempt / ("source" + source.suffix.lower())
        snapshot.write_bytes(data)
        text = attempt / "body.txt"
        text.write_bytes((batch / TEXT).read_bytes())
        output = attempt / "pages"
        try:
            renderer = _render(snapshot, output, settings)
            pages, missing_fonts = _rendered(output, identity["source_sha256"])
        except SourcePagesError:
            if explicit or source.suffix.lower() != ".pdf":
                raise
            pages = _previews(batch, identity["source_sha256"])
            renderer, missing_fonts = "pdf-page-previews", []
        result = []
        for row in pages:
            image = Path(row["path"])
            width, height = _png(image)
            target = attempt / ("page-%04d.png" % row["page"])
            image_data = image.read_bytes()
            target.write_bytes(image_data)
            result.append({"page": row["page"], "path": target.relative_to(batch).as_posix(),
                           "image_sha256": _sha(image_data), "width": width, "height": height,
                           "full_page": True})
        # Identity is checked again after rendering, including body text changes.
        if _identity(batch)[2] != identity:
            raise SourcePagesError("source_or_text_changed_during_render")
        if not isinstance(missing_fonts, list) or any(not isinstance(font, str) for font in missing_fonts):
            raise SourcePagesError("renderer_font_metadata_invalid")
        report.update(status="verified", pages=result, page_count=len(result), renderer=renderer,
                      source_name=source.name, missing_fonts=missing_fonts,
                      requested_dpi=settings["dpi"],
                      warnings=[{"code": "MISSING_FONTS", "fonts": missing_fonts,
                                 "message": "Requested fonts are missing; layout fidelity may differ."}]
                      if missing_fonts else [],
                      source_snapshot=snapshot.relative_to(directory).as_posix(),
                      text_snapshot=text.relative_to(directory).as_posix())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report.update(status="unavailable", pages=[],
                      reason=str(exc) if isinstance(exc, SourcePagesError) else "page_evidence_unavailable")
    _write(batch / MANIFEST, report)
    verified = load_verified(batch)
    return verified["pages"] if verified.get("status") == "verified" else []
