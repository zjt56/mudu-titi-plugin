"""Ordered, offline DOCX extraction using only the standard library.

``extract_docx`` returns JSON-compatible data and writes only referenced image
bytes below output_dir/media (never source text or a modified source archive).
Blocks are top-level body paragraphs/tables in XML order; ``block_index`` is
zero based, including empty paragraphs. Tables expose physical cells, their
zero-based grid columns, spans, vertical merge evidence and an expanded text
grid: covered coordinates are empty, never fabricated/repeated cell content.
Nested content lives in cell ``blocks``. Text uses preview-compatible markers,
HTML inline formatting and <br> for cell newlines; unmodified cell text remains
in metadata. Page break records are XML evidence, not physical page counts.
Invalid/unsafe packages raise ValueError (ordinary file I/O errors propagate).
"""
from __future__ import annotations

import posixpath
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
V = "urn:schemas-microsoft-com:vml"
NS = {"w": W}
MAX_MEMBERS = 10000
MAX_TOTAL = 512 * 1024 * 1024
MAX_PART = 64 * 1024 * 1024
MAX_XML = 16 * 1024 * 1024
MAX_GRID = 100000


def _w(name):
    return "{" + W + "}" + name


def _val(node, default=None):
    return node.get(_w("val"), default) if node is not None else default


class _Extractor:
    def __init__(self, archive, output_dir):
        self.archive = archive
        self.output_dir = output_dir
        self.warnings = []
        self.media = []
        self.tables = []
        self.saved = {}
        self.styles = {}
        self.defaults = {}
        self.default_paragraph = None
        self.rels = {}
        self.block_index = 0
        infos = archive.infolist()
        if len(infos) > MAX_MEMBERS or sum(i.file_size for i in infos) > MAX_TOTAL:
            raise ValueError("DOCX archive exceeds resource limits")
        names = [i.filename for i in infos]
        if len(names) != len(set(names)):
            raise ValueError("DOCX contains duplicate archive members")
        for info in infos:
            if info.file_size > MAX_PART or info.flag_bits & 1:
                raise ValueError("DOCX member is too large or encrypted")
            if info.file_size > 1024 * 1024 and info.file_size / max(info.compress_size, 1) > 1000:
                raise ValueError("DOCX compression ratio exceeds resource limit")
        self.names = set(names)
        styles = self.xml("word/styles.xml", optional=True)
        if styles is not None:
            self.defaults = self.props(styles.find("w:docDefaults/w:rPrDefault/w:rPr", NS))
            for style in styles.findall("w:style", NS):
                self.styles[style.get(_w("styleId"))] = style
                if style.get(_w("type")) == "paragraph" and style.get(_w("default")) in ("1", "true", "on"):
                    self.default_paragraph = style.get(_w("styleId"))
        rels = self.xml("word/_rels/document.xml.rels", optional=True)
        if rels is not None:
            for rel in rels:
                self.rels[rel.get("Id")] = dict(rel.attrib)

    def warn(self, message):
        if message not in self.warnings:
            self.warnings.append(message)

    def xml(self, name, optional=False):
        if name not in self.names:
            if optional:
                return None
            raise ValueError("Missing DOCX part: " + name)
        if self.archive.getinfo(name).file_size > MAX_XML:
            raise ValueError("DOCX XML part exceeds resource limit")
        data = self.archive.read(name)
        # Reject UTF-16/32 XML as well, so byte-level DTD detection is complete.
        if b"\x00" in data or re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", data, re.I):
            raise ValueError("Unsafe or unsupported DOCX XML declaration")
        try:
            return ET.fromstring(data)
        except ET.ParseError as exc:
            raise ValueError("Malformed DOCX XML: " + name) from exc

    @staticmethod
    def props(node):
        result = {}
        if node is None:
            return result
        for child in node:
            key = child.tag.rsplit("}", 1)[-1]
            if key in ("b", "bCs", "u", "vertAlign"):
                value = _val(child, "1")
                result[key] = value not in ("0", "false", "off", "none") if key != "vertAlign" else value
            elif key == "rFonts":
                result["font"] = " ".join(child.attrib.values())
        return result

    def style_chain(self, style_id, seen=None):
        seen = set() if seen is None else seen
        if not style_id:
            return []
        if style_id in seen:
            self.warn("Style inheritance cycle: " + style_id)
            return []
        style = self.styles.get(style_id)
        if style is None:
            self.warn("Unresolved style: " + style_id)
            return []
        seen.add(style_id)
        return self.style_chain(_val(style.find("w:basedOn", NS)), seen) + [style]

    def formatting(self, paragraph_style, run):
        result = dict(self.defaults)
        rpr = run.find("w:rPr", NS)
        for style_id in (paragraph_style, _val(rpr.find("w:rStyle", NS)) if rpr is not None else None):
            for style in self.style_chain(style_id):
                for key, value in self.props(style.find("w:rPr", NS)).items():
                    # OOXML bold is a toggle in styles; direct formatting sets it.
                    if key in ("b", "bCs"):
                        if value:
                            result[key] = not result.get(key, False)
                    else:
                        result[key] = value
        result.update(self.props(rpr))
        if re.search(r"symbol|wingdings|webdings", result.get("font", ""), re.I):
            self.warn("Symbol font encountered; glyph/font mapping was not guessed")
        return result

    def image(self, rel_id):
        rel = self.rels.get(rel_id)
        if not rel or not rel.get("Type", "").endswith("/image"):
            self.warn("Unresolved image relationship: " + str(rel_id))
            return "[IMG unresolved]"
        if rel.get("TargetMode", "").lower() == "external":
            self.warn("External image relationship ignored: " + str(rel_id))
            return "[IMG unresolved]"
        target = unquote(rel.get("Target", ""))
        try:
            parsed = urlsplit(target)
        except ValueError:
            self.warn("Unresolved or unsafe image target: " + str(rel_id))
            return "[IMG unresolved]"
        member = posixpath.normpath(posixpath.join("word", target))
        if (parsed.scheme or parsed.netloc or "\\" in target or target.startswith("/")
                or member == ".." or member.startswith("../") or member not in self.names):
            self.warn("Unresolved or unsafe image target: " + str(rel_id))
            return "[IMG unresolved]"
        if member not in self.saved:
            base = re.sub(r"[^\w.\-]", "_", posixpath.basename(member), flags=re.UNICODE).lstrip(".") or "image.bin"
            base = base[:160]
            directory = self.output_dir / "media"
            directory.mkdir(parents=True, exist_ok=True)
            if directory.is_symlink():
                raise ValueError("Media output directory must not be a symlink")
            number = 1
            while True:
                name = base if number == 1 else f"{Path(base).stem}_{number}{Path(base).suffix}"
                path = directory / name
                try:
                    with path.open("xb") as handle:
                        handle.write(self.archive.read(member))
                    break
                except FileExistsError:
                    number += 1
            self.saved[member] = (name, str(path), self.archive.getinfo(member).file_size)
        name, path, size = self.saved[member]
        self.media.append({"name": name, "path": path, "source_part": member,
                           "relationship_id": rel_id, "block_index": self.block_index,
                           "size": size, "occurrence": len(self.media)})
        return f"[IMG {name}]"

    def inline(self, node, style, breaks):
        if node.tag == _w("r"):
            fmt = self.formatting(style, node)
            text = "".join(self.inline(child, style, breaks) for child in node if child.tag != _w("rPr"))
            if not text:
                return text
            if fmt.get("vertAlign") in ("superscript", "subscript"):
                tag = "sup" if fmt["vertAlign"] == "superscript" else "sub"
                text = f"<{tag}>{text}</{tag}>"
            if fmt.get("u"):
                text = "<u>" + text + "</u>"
            if fmt.get("b"):
                text = "**" + text + "**"
            return text
        if node.tag == _w("t"):
            text = node.text or ""
            if any(0xE000 <= ord(c) <= 0xF8FF or 0xF0000 <= ord(c) <= 0xFFFFD or 0x100000 <= ord(c) <= 0x10FFFD for c in text):
                self.warn("Private-use glyph encountered; raw character preserved without font mapping")
            return text
        if node.tag in (_w("tab"), _w("ptab")):
            return "\t"
        if node.tag in (_w("br"), _w("cr")):
            kind = node.get(_w("type"), "textWrapping")
            if kind in ("page", "column"):
                breaks.append({"type": kind, "source": "explicit"})
            return "\n"
        if node.tag == _w("lastRenderedPageBreak"):
            breaks.append({"type": "page", "source": "lastRenderedPageBreak"})
            return ""
        if node.tag == _w("noBreakHyphen"):
            return "\u2011"
        if node.tag == _w("softHyphen"):
            return "\u00ad"
        if node.tag == _w("sym"):
            self.warn("Symbol glyph encountered; font mapping was not guessed")
            try:
                char = chr(int(node.get(_w("char"), ""), 16))
                self.warn("Private-use/symbol glyph requires visual verification")
                return char
            except (ValueError, OverflowError):
                return "[SYMBOL unresolved]"
        if node.tag == "{" + A + "}blip":
            return self.image(node.get("{" + R + "}embed") or node.get("{" + R + "}link"))
        if node.tag == "{" + V + "}imagedata":
            return self.image(node.get("{" + R + "}id"))
        if (node.tag.rsplit("}", 1)[-1] == "anchor"
                or node.tag == _w("object")
                or node.tag == "{" + V + "}shape" and "position:absolute" in node.get("style", "").replace(" ", "").lower()):
            self.warn("Floating object encountered; XML anchor order may differ from visual order")
        if node.tag == _w("txbxContent"):
            self.warn("Textbox encountered; XML anchor order may differ from visual reading order")
            return "\n".join(block["text"] for block in self.content(node))
        if node.tag in (_w("pPr"), _w("rPr"), _w("del"), _w("instrText")):
            return ""
        if node.tag.endswith("}AlternateContent"):
            children = list(node)
            chosen = next((c for c in children if c.tag.endswith("}Choice")), children[0] if children else None)
            self.warn("Alternate drawing content uses its first XML choice; visual verification may be needed")
            return self.inline(chosen, style, breaks) if chosen is not None else ""
        return "".join(self.inline(child, style, breaks) for child in node)

    def paragraph(self, node):
        style = _val(node.find("w:pPr/w:pStyle", NS), self.default_paragraph)
        breaks = []
        before = node.find("w:pPr/w:pageBreakBefore", NS)
        if before is not None and _val(before, "1") not in ("0", "false", "off"):
            breaks.append({"type": "page", "source": "pageBreakBefore"})
        text = self.inline(node, style, breaks)
        return {"type": "paragraph", "text": text, "page_breaks": breaks}

    @staticmethod
    def integer(node, default=0):
        try:
            value = int(_val(node, str(default)))
        except ValueError as exc:
            raise ValueError("Invalid table grid dimension") from exc
        if not 0 <= value <= MAX_GRID:
            raise ValueError("Table grid dimension exceeds resource limit")
        return value

    def table(self, node):
        table = {"type": "table", "number": len(self.tables) + 1,
                 "block_index": self.block_index, "rows": [], "grid": [],
                 "grid_columns": len(node.findall("w:tblGrid/w:gridCol", NS))}
        self.tables.append(table)
        active = {}
        width = table["grid_columns"]
        for row_number, row in enumerate(node.findall("w:tr", NS)):
            offset = self.integer(row.find("w:trPr/w:gridBefore", NS))
            cells, grid, next_active = [], [""] * offset, {}
            col = offset
            for cell in row.findall("w:tc", NS):
                span = self.integer(cell.find("w:tcPr/w:gridSpan", NS), 1)
                if span < 1 or col + span > MAX_GRID:
                    raise ValueError("Invalid table cell span")
                merge = cell.find("w:tcPr/w:vMerge", NS)
                merge_value = _val(merge, "continue") if merge is not None else None
                blocks = self.content(cell)
                text = "\n".join(block["text"] for block in blocks)
                entry = {"text": text, "column": col, "colspan": span, "rowspan": 1,
                         "vmerge": merge_value, "blocks": blocks}
                if merge_value == "continue":
                    anchor = active.get((col, span))
                    if anchor is None:
                        self.warn("Vertical merge continuation has no matching grid anchor")
                    else:
                        anchor["rowspan"] += 1
                        entry["merge_origin"] = anchor["origin"]
                        next_active[(col, span)] = anchor
                elif merge_value == "restart":
                    entry["origin"] = [row_number, col]
                    next_active[(col, span)] = entry
                cells.append(entry)
                grid.extend([text] + [""] * (span - 1))
                col += span
            after = self.integer(row.find("w:trPr/w:gridAfter", NS))
            grid.extend([""] * after)
            active = next_active
            width = max(width, len(grid))
            table["rows"].append({"cells": cells, "grid_before": offset, "grid_after": after})
            table["grid"].append(grid)
            if width * len(table["rows"]) > MAX_GRID:
                raise ValueError("Table expanded grid exceeds resource limit")
        for grid in table["grid"]:
            grid.extend([""] * (width - len(grid)))
        table["grid_columns"] = width
        # A single physical row per line; metadata retains literal cell newlines.
        rows = [" | ".join(value.replace("\n", "<br>") for value in grid) for grid in table["grid"]]
        table["text"] = "\n".join([f"[TABLE {table['number']}]", *rows, "[/TABLE]"])
        return table

    def content(self, parent):
        blocks = []
        for child in parent:
            if child.tag == _w("p"):
                blocks.append(self.paragraph(child))
            elif child.tag == _w("tbl"):
                blocks.append(self.table(child))
            elif child.tag in (_w("sdt"), _w("sdtContent"), _w("customXml"), _w("ins")):
                blocks.extend(self.content(child))
            elif child.tag == _w("altChunk"):
                self.warn("Embedded alternate document content was not extracted")
        return blocks


def extract_docx(source: Path, output_dir: Path) -> dict:
    """Extract main-body text/structure and referenced images, without networking.

    Headers, footers, comments and footnotes are excluded from main-body input.
    Each media entry represents an occurrence (shared bytes may share a name).
    Existing output files are never overwritten. See module docstring for grid
    and page-break semantics. This API does not write text/JSON output files.
    """
    source, output_dir = Path(source), Path(output_dir)
    try:
        with zipfile.ZipFile(source) as archive:
            extractor = _Extractor(archive, output_dir)
            root = extractor.xml("word/document.xml")
            body = root.find("w:body", NS)
            if body is None:
                raise ValueError("DOCX main document has no body")
            blocks = []
            # Flatten block wrappers while assigning indices only at body level.
            def append_content(parent):
                for child in parent:
                    if child.tag in (_w("sdt"), _w("sdtContent"), _w("customXml"), _w("ins")):
                        append_content(child)
                    elif child.tag in (_w("p"), _w("tbl")):
                        extractor.block_index = len(blocks)
                        block = extractor.paragraph(child) if child.tag == _w("p") else extractor.table(child)
                        block["block_index"] = len(blocks)
                        blocks.append(block)
                    elif child.tag == _w("altChunk"):
                        extractor.warn("Embedded alternate document content was not extracted")
            append_content(body)
            return {"text": "\n".join(block["text"] for block in blocks),
                    "blocks": blocks, "tables": extractor.tables, "media": extractor.media,
                    "warnings": extractor.warnings, "method": "docx-native"}
    except zipfile.BadZipFile as exc:
        raise ValueError("Invalid DOCX ZIP archive") from exc
