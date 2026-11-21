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

import hashlib
import posixpath
import re
import struct
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

# 私用区（PUA）字形按原码位保留：文本没有被替换或丢弃，只是缺少字体映射。
# 消费者（画线恢复/体检）可把这一类降级为提示；Symbol 字体映射与 w:sym 字形
# 仍可能改写字面，必须继续阻断并要求视觉核验。
RAW_PRESERVED_GLYPH_WARNING = "Private-use glyph encountered; raw character preserved without font mapping"


# 版面顺序提示：浮动对象/文本框只说明 XML 锚点顺序可能与视觉顺序不同，并不改写
# 字面。上游结构闸门已把这类告警降级为 layout_review_required 并交由整页版面复核，
# 画线恢复本身只接受唯一且完整的上下文匹配，因此这里与结构闸门口径一致：
# 保留原告警供版面复核，但不作为画线/体检的阻断项。
LAYOUT_ORDER_WARNINGS = (
    "Floating object encountered; XML anchor order may differ from visual order",
    "Textbox encountered; XML anchor order may differ from visual reading order",
)


def _warning_text(warning) -> str:
    """兼容字符串告警与结构告警，统一取可比较的说明文字。"""
    if isinstance(warning, str):
        return warning
    if isinstance(warning, dict):
        return str(warning.get("message") or warning.get("detail") or "")
    return ""


def layout_hint_warnings(warnings) -> list:
    """只返回版面顺序提示类告警（不阻断，需版面复核）。"""
    return [w for w in (warnings or []) if _warning_text(w) in LAYOUT_ORDER_WARNINGS]


def blocking_warnings(warnings) -> list:
    """过滤掉「原码位保留、仅缺字体映射」与版面顺序提示，只留下可能改写字面的项。"""
    return [w for w in (warnings or [])
            if _warning_text(w) != RAW_PRESERVED_GLYPH_WARNING
            and _warning_text(w) not in LAYOUT_ORDER_WARNINGS]


def _png_size(data: bytes):
    if data[:8] != b"\x89PNG\r\n\x1a\n" or len(data) < 24:
        return None
    return struct.unpack(">II", data[16:24])


def _gif_size(data: bytes):
    if not data.startswith((b"GIF87a", b"GIF89a")) or len(data) < 10:
        return None
    return struct.unpack("<HH", data[6:10])


def _jpeg_size(data: bytes):
    if not data.startswith(b"\xff\xd8"):
        return None
    index = 2
    length = len(data)
    while index + 9 < length:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        segment = struct.unpack(">H", data[index + 2:index + 4])[0]
        if segment < 2:
            break
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[index + 5:index + 9])
            return width, height
        index += 2 + segment
    return None


def _webp_size(data: bytes):
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP" or len(data) < 30:
        return None
    chunk = data[12:16]
    if chunk == b"VP8 ":
        width, height = struct.unpack("<HH", data[26:30])
        return width & 0x3FFF, height & 0x3FFF
    if chunk == b"VP8L":
        bits = struct.unpack("<I", data[21:25])[0]
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8X":
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return width, height
    return None


def image_dimensions_from_bytes(data: bytes) -> tuple[int, int] | None:
    """从图片字节头解析宽高（px），不引入 Pillow 依赖；认不出时返回 None。"""
    if not data:
        return None
    for reader in (_png_size, _gif_size, _jpeg_size, _webp_size):
        size = reader(data)
        if size and size[0] and size[1]:
            return int(size[0]), int(size[1])
    return None


def image_dimensions(path: Path) -> tuple[int, int] | None:
    """只读文件头前 64KB 拿宽高（px）；无法读取或认不出格式时返回 None。"""
    try:
        with path.open("rb") as handle:
            head = handle.read(64 * 1024)
    except OSError:
        return None
    return image_dimensions_from_bytes(head)


def is_decorative_media(width: int | None, height: int | None, size: int, count: int = 1) -> bool:
    """识别页眉横幅、栏目装饰胶囊（新考法/熟词生义等）及高频小图标，供提取与下游过滤共用。"""
    if count >= 2 and 0 < size <= 20480 and (height is None or height <= 120):
        return True
    if width and height and height > 0:
        ratio = width / height
        # 横向小胶囊/栏目徽章（如 新考法 260x43、熟词生义 194x58、记笔记 151x58），
        # 实质听力选项图最小高度通常 >= 69px，62px 以下扁长图签稳定属于装饰徽章。
        if height <= 62 and ratio >= 2.4 and size <= 32768:
            return True
        # 卷头水印横幅或分隔条幅（如 木牍中考 46套 1091x191，长宽比 >= 4.8）。
        if ratio >= 4.8 and (height <= 220 or size <= 300000):
            return True
    return False


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
        self.abstract_nums = {}
        self.nums = {}
        self.num_counters = {}
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
        numbering = self.xml("word/numbering.xml", optional=True)
        if numbering is not None:
            for abstract in numbering.findall("w:abstractNum", NS):
                aid = abstract.get(_w("abstractNumId"))
                if not aid:
                    continue
                levels = {}
                for lvl in abstract.findall("w:lvl", NS):
                    ilvl = lvl.get(_w("ilvl"), "0")
                    levels[ilvl] = self._parse_lvl(lvl)
                self.abstract_nums[aid] = levels
            for num in numbering.findall("w:num", NS):
                nid = num.get(_w("numId"))
                aid = _val(num.find("w:abstractNumId", NS))
                if not nid or not aid:
                    continue
                overrides = {}
                for ov in num.findall("w:lvlOverride", NS):
                    ilvl = ov.get(_w("ilvl"), "0")
                    start_ov = _val(ov.find("w:startOverride", NS))
                    lvl_node = ov.find("w:lvl", NS)
                    overrides[ilvl] = {
                        "startOverride": int(start_ov) if start_ov is not None and start_ov.lstrip("-").isdigit() else None,
                        "lvl": self._parse_lvl(lvl_node) if lvl_node is not None else None,
                    }
                self.nums[nid] = {"abstractNumId": aid, "overrides": overrides}

    @staticmethod
    def _parse_lvl(node):
        start_raw = _val(node.find("w:start", NS), "1")
        start = int(start_raw) if str(start_raw).lstrip("-").isdigit() else 1
        return {
            "start": start,
            "numFmt": _val(node.find("w:numFmt", NS), "decimal"),
            "lvlText": _val(node.find("w:lvlText", NS), "%1."),
            "suff": _val(node.find("w:suff", NS), "tab"),
        }

    @staticmethod
    def _format_num_value(value: int, fmt: str):
        if fmt in ("bullet", "none") or value < 0:
            return None
        if fmt == "decimal":
            return str(value)
        if fmt == "decimalZero":
            return f"{value:02d}"
        if fmt in ("upperRoman", "lowerRoman") and 1 <= value <= 3999:
            pairs = ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
                     (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
                     (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))
            n, out = value, []
            for num, sym in pairs:
                while n >= num:
                    out.append(sym)
                    n -= num
            roman = "".join(out)
            return roman if fmt == "upperRoman" else roman.lower()
        if fmt in ("upperLetter", "lowerLetter") and value >= 1:
            base = ord("A") if fmt == "upperLetter" else ord("a")
            return chr(base + (value - 1) % 26) * ((value - 1) // 26 + 1)
        return None

    def _effective_lvl(self, num_id: str, ilvl: str):
        info = self.nums.get(num_id)
        if not info:
            return None
        base = dict(self.abstract_nums.get(info["abstractNumId"], {}).get(ilvl) or {})
        ov = info["overrides"].get(ilvl) or {}
        if ov.get("lvl"):
            base.update(ov["lvl"])
        if ov.get("startOverride") is not None:
            base["start"] = ov["startOverride"]
        return base or None

    def _paragraph_numbering(self, node, style_id: str, text: str) -> str:
        if not text.strip():
            return ""
        num_pr = node.find("w:pPr/w:numPr", NS)
        if num_pr is None and style_id:
            for st in reversed(self.style_chain(style_id)):
                candidate = st.find("w:pPr/w:numPr", NS)
                if candidate is not None:
                    num_pr = candidate
                    break
        if num_pr is None:
            return ""
        num_id = _val(num_pr.find("w:numId", NS))
        if not num_id or num_id == "0" or num_id not in self.nums:
            return ""
        ilvl = str(_val(num_pr.find("w:ilvl", NS), "0"))
        lvl_def = self._effective_lvl(num_id, ilvl)
        if not lvl_def:
            return ""
        cur_lvl_int = int(ilvl) if ilvl.isdigit() else 0
        # Reset deeper levels when advancing an outer level within the same list.
        for key_nid, key_ilvl in list(self.num_counters):
            if key_nid == num_id and key_ilvl.isdigit() and int(key_ilvl) > cur_lvl_int:
                del self.num_counters[(key_nid, key_ilvl)]
        prev = self.num_counters.get((num_id, ilvl))
        value = lvl_def["start"] if prev is None else prev + 1
        self.num_counters[(num_id, ilvl)] = value
        formatted = self._format_num_value(value, lvl_def.get("numFmt", "decimal"))
        if formatted is None:
            return ""
        lvl_text = lvl_def.get("lvlText", "%1.")
        if "%" not in lvl_text:
            return ""

        def _replace_placeholder(match):
            idx = int(match.group(1)) - 1
            lvl_key = str(idx)
            if lvl_key == ilvl:
                return formatted
            other_def = self._effective_lvl(num_id, lvl_key)
            other_val = self.num_counters.get((num_id, lvl_key), (other_def or {}).get("start", 1))
            other_fmt = (other_def or {}).get("numFmt", "decimal")
            return self._format_num_value(other_val, other_fmt) or ""

        prefix = re.sub(r"%(\d+)", _replace_placeholder, lvl_text)
        if not prefix:
            return ""
        suff = lvl_def.get("suff", "tab")
        sep = "" if (suff == "nothing" or text.startswith("[IMG ")) else (" " if suff == "space" else "\t")
        return prefix + sep

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
            raw_bytes = self.archive.read(member)
            number = 1
            while True:
                name = base if number == 1 else f"{Path(base).stem}_{number}{Path(base).suffix}"
                path = directory / name
                try:
                    with path.open("xb") as handle:
                        handle.write(raw_bytes)
                    break
                except FileExistsError:
                    number += 1
            dim = image_dimensions_from_bytes(raw_bytes)
            self.saved[member] = (name, str(path), self.archive.getinfo(member).file_size, dim)
        name, path, size, dim = self.saved[member]
        entry = {"name": name, "path": path, "source_part": member,
                 "relationship_id": rel_id, "block_index": self.block_index,
                 "size": size, "occurrence": len(self.media)}
        if dim:
            entry["width"], entry["height"] = dim
        self.media.append(entry)
        return f"[IMG {name}]"

    def inline(self, node, style, breaks):
        if node.tag == _w("r"):
            fmt = self.formatting(style, node)
            text = "".join(self.inline(child, style, breaks) for child in node if child.tag != _w("rPr"))
            if not text:
                return text
            # 符号字体（Symbol/Wingdings/Webdings 等）只可能在两种情况下改变字面：
            # ① 把 ASCII 字母/数字按字形表改写成图符（如 Wingdings 的 l→●）；
            # ② 承载私用区(PUA)码位，该码位本身需字体映射才有确定字形。
            # 若 run 文本已是明确的 Unicode 符号（如 ★ U+2605，Segoe UI Symbol 只是承载
            # 字体），原码位即真实字符，不存在映射歧义，不告警、不阻断——与「私用区字形
            # 不阻断」保持同一口径，也避免装饰星号把整卷误判为提取不确定。
            _pua = any(0xE000 <= ord(c) <= 0xF8FF or 0xF0000 <= ord(c) <= 0xFFFFD
                       or 0x100000 <= ord(c) <= 0x10FFFD for c in text)
            if (re.search(r"symbol|wingdings|webdings", str(fmt.get("font") or ""), re.I)
                    and (re.search(r"[A-Za-z0-9]", text) or _pua)):
                self.warn("Symbol font encountered; glyph/font mapping was not guessed")
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
        prefix = self._paragraph_numbering(node, style, text)
        if prefix:
            text = prefix + text
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
            # 源头标记高频小图标、扁平栏目徽章（如 新考法/熟词生义）及横幅水印，
            # 供下游在提干/材料/解析/标题剥离时与实质内容配图区分，不删原文件。
            counts: dict[str, int] = {}
            for entry in extractor.media:
                nm = entry.get("name") or ""
                counts[nm] = counts.get(nm, 0) + 1
            for entry in extractor.media:
                nm = entry.get("name") or ""
                size = entry.get("size") or 0
                if isinstance(size, int) and is_decorative_media(
                        entry.get("width"), entry.get("height"), size, counts.get(nm, 0)):
                    entry["decorative"] = True
            from .source_objects import attach
            result = {"text": "\n".join(block["text"] for block in blocks),
                      "blocks": blocks, "tables": extractor.tables, "media": extractor.media,
                      "warnings": extractor.warnings, "method": "docx-native"}
            return attach(result, hashlib.sha256(source.read_bytes()).hexdigest())
    except zipfile.BadZipFile as exc:
        raise ValueError("Invalid DOCX ZIP archive") from exc
