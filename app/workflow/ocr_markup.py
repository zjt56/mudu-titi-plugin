# -*- coding: utf-8 -*-
"""Project OCR service Markdown onto the pipeline's canonical source markers.

The recognition service (PaddleOCR-VL and friends) returns layout Markdown:

    <div style="text-align: center;"><img src="imgs/img_in_image_box_52_617_158_734.jpg" /></div>
    $ \\underline{Wake} $ up,little $ \\underline{Kate} $!
    <table border=1><tr><td>...

Those tags are a *layout* notation, not the words of the paper. Feeding them
straight into 正文 made every downstream consumer (structure gate, assembly,
review preview, question stems) show HTML/LaTeX "code" instead of the paper.

This module is a deterministic projection only: it never edits wording, never
reorders the service output, never invents content.

* ``<img src=…>``  → ``[IMG <published media name>]`` — the same ``[IMG name]``
  convention the Word extractor uses; the image bytes are copied into
  ``01_source/media/`` by the caller. An image whose bytes are not available is
  left untouched (evidence over cosmetics) and reported as a warning.
* HTML ``<table>`` → ``[TABLE n] … [/TABLE]`` with `` | `` separated columns and
  colspan/rowspan kept as empty grid cells (no guessed merges).
* LaTeX underlines / text wrappers → ``<u>…</u>`` / plain text, but only when the
  whole ``$…$`` segment can be restored without loss; real math is left alone.
* Markdown heading/emphasis markers → plain text.

The raw service Markdown stays untouched in ``01_source/pdf-ocr.md`` and
``01_source/原始正文.txt`` (evidence); the projection is written to
``01_source/正文.txt`` for downstream stages.
"""
from __future__ import annotations

import re
from pathlib import Path

_IMG = re.compile(r"<img\b[^>]*>", re.I)
_SRC = re.compile(r"""src\s*=\s*["']([^"']+)["']""", re.I)
_WRAP_IMG = re.compile(
    r"<div\b[^>]*>\s*(?:<br\s*/?>\s*)*(\[IMG [^\]\r\n]+\])\s*(?:<br\s*/?>\s*)*</div>",
    re.I)
_STRAY_DIV = re.compile(r"^[ \t]*</?div\b[^>]*>[ \t]*$\n?", re.I | re.M)
_TABLE = re.compile(r"<table\b[^>]*>(.*?)</table>", re.S | re.I)
_TR = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<t[dh]\b([^>]*)>(.*?)</t[dh]>", re.S | re.I)
_COLSPAN = re.compile(r"colspan\s*=\s*[\"']?(\d+)", re.I)
_ROWSPAN = re.compile(r"rowspan\s*=\s*[\"']?(\d+)", re.I)
_TAG = re.compile(r"<[^>]*>")
_MATH = re.compile(r"\$\$(.+?)\$\$|\$(.+?)\$", re.S)
_LATEX_UNDERLINE = re.compile(r"\\underline\s*\{([^{}]*)\}")
_LATEX_TEXT = re.compile(
    r"\\(?:text|textrm|textit|mathrm|textnormal|textbf|operatorname)\s*\{([^{}]*)\}")
_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]+", re.M)
_BOLD = re.compile(r"\*\*([^*\r\n]+)\*\*")
_TABLE_NUMBER = re.compile(r"\[TABLE\s+(\d+)\]")
_PAGE = re.compile(r"\[PAGE\s+(\d+)\]")


def image_name(page, index, suffix):
    """Canonical media name, identical in shape to the native-PDF projection."""
    return f"pdf-page-{int(page)}-image-{int(index)}{suffix or '.jpg'}"


def _cell_text(html):
    """Flatten one table cell to a single line; keep literal breaks as <br>."""
    text = _TAG.sub(" ", html)
    # 图片标记与后文紧贴时补一个空格，避免单元格里出现 `[IMG x]19°C` 这种粘连。
    text = re.sub(r"(\[IMG [^\]\r\n]+\])(?=\S)", r"\1 ", text)
    text = " ".join(text.split())
    return text


def _html_tables(text, start_number=1):
    """Convert HTML tables to ``[TABLE n]`` blocks, keeping grid shape."""
    number = start_number

    def convert(match):
        nonlocal number
        rows = []
        occupied = {}
        for row_index, row_html in enumerate(_TR.findall(match.group(1))):
            cells = _CELL.findall(row_html)
            row = []
            col = 0
            if not cells:
                plain = _cell_text(row_html)
                if plain:
                    rows.append(plain)
                continue
            for attrs, content in cells:
                while (row_index, col) in occupied:
                    row.append("")
                    col += 1
                colspan = max(1, int((_COLSPAN.search(attrs) or [None, "1"])[1]))
                rowspan = max(1, int((_ROWSPAN.search(attrs) or [None, "1"])[1]))
                value = _cell_text(content)
                for offset in range(colspan):
                    row.append(value if offset == 0 else "")
                    for down in range(1, rowspan):
                        occupied[(row_index + down, col + offset)] = ""
                col += colspan
            while (row_index, col) in occupied:
                row.append("")
                col += 1
            rows.append(" | ".join(row))
        if not rows:
            return match.group(0)
        block = "\n".join([f"[TABLE {number}]", *rows, "[/TABLE]"])
        number += 1
        return block

    return _TABLE.sub(convert, text)


def _latex_segment(inner):
    """Restore a `$…$` segment to plain/underline text, or None if it is math."""
    value = inner.strip()
    if not value:
        return None
    for _ in range(6):
        updated = _LATEX_UNDERLINE.sub(r"<u>\1</u>", value)
        updated = _LATEX_TEXT.sub(r"\1", updated)
        if updated == value:
            break
        value = updated
    if "\\" in value or "{" in value or "}" in value or "^" in value or "_" in value:
        return None
    return value


def _project_math(text):
    def convert(match):
        segment = match.group(0)
        inner = match.group(1) if match.group(1) is not None else match.group(2)
        value = _latex_segment(inner)
        return segment if value is None else value

    return _MATH.sub(convert, text)


def _project_structure_markers(text):
    text = _HEADING.sub("", text)
    return _BOLD.sub(r"\1", text)


def normalize_page(text, names):
    """Project one page of service Markdown; ``names`` maps src → media name."""
    if not text:
        return text
    by_basename = {Path(str(src)).name: name for src, name in (names or {}).items()}

    def replace_image(match):
        tag = match.group(0)
        found = _SRC.search(tag)
        if not found:
            return tag
        src = found.group(1)
        name = (names or {}).get(src) or by_basename.get(Path(src).name)
        return f"[IMG {name}]" if name else tag

    text = _IMG.sub(replace_image, text)
    text = _WRAP_IMG.sub(r"\1", text)
    text = _STRAY_DIV.sub("", text)
    existing = [int(value) for value in _TABLE_NUMBER.findall(text)]
    text = _html_tables(text, (max(existing) + 1) if existing else 1)
    text = _project_math(text)
    return _project_structure_markers(text)


def unresolved_images(text, names):
    """Return src values that could not be projected to a published media name."""
    by_basename = {Path(str(src)).name for src in (names or {})}
    missing = []
    for tag in _IMG.findall(text):
        found = _SRC.search(tag)
        if not found:
            continue
        src = found.group(1)
        if src in (names or {}) or Path(src).name in by_basename:
            continue
        if src not in missing:
            missing.append(src)
    return missing


def normalize_document(text, page_images):
    """Project a whole OCR text using per-page image name maps.

    ``page_images`` is ``{page_number: {src: media_name}}``. Text split by
    ``[PAGE n]`` markers is projected with that page's map; a document without
    markers, or a page without a map, falls back to every known mapping so the
    images still resolve instead of dumping markup into the review page.
    """
    if not text:
        return text
    merged = {}
    for mapping in (page_images or {}).values():
        if isinstance(mapping, dict):
            merged.update(mapping)
    parts = re.split(r"(\[PAGE\s+\d+\])", text)
    output = []
    current = None
    for part in parts:
        marker = _PAGE.fullmatch(part.strip())
        if marker:
            current = int(marker.group(1))
            output.append(part)
            continue
        names = (page_images or {}).get(current) or merged
        output.append(normalize_page(part, names))
    return "".join(output)
