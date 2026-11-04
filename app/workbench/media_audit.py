# -*- coding: utf-8 -*-
"""媒体归属体检：区分题目图片与卷头广告、水印、没有被正文引用的图片。

判据只有三条，都是可复核的事实，不做图像内容识别：

1. 正文有没有 `[IMG 文件名]` 引用（没有引用 → 不可能属于某道题）；
2. 图片出现在第一道题之前还是之后（卷头横幅、水印一般在前面）；
3. 长宽比是否像横幅（>= 5:1）。

结论是“候选判定”，不是人工结论：工作台把三类可疑图片列出来，由人勾选排除；
排除清单写在 `workflow/media-exclusions.json`，上传阶段应跳过这些文件。
"""
from __future__ import annotations

import json
import re
import struct
from pathlib import Path

IMG_REF = re.compile(r"\[IMG\s+([^\]]+)\]")
EXCLUSIONS = "media-exclusions.json"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

try:  # preview 里有题号识别规则，复用同一套，避免两套题号口径
    from workflow.preview import _find  # type: ignore[attr-defined]
except Exception:  # pragma: no cover - 直接运行且路径没配好时退化为不判定卷头
    _find = None


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


def image_size(path: Path):
    """只读文件头拿宽高，不引入 Pillow 依赖；认不出就返回 None。"""
    try:
        with path.open("rb") as handle:
            head = handle.read(64 * 1024)
    except OSError:
        return None
    for reader in (_png_size, _gif_size, _jpeg_size, _webp_size):
        size = reader(head)
        if size and size[0] and size[1]:
            return int(size[0]), int(size[1])
    return None


def load_exclusions(folder: Path):
    path = folder / "workflow" / EXCLUSIONS
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"excluded": [], "notes": {}}
    if not isinstance(data, dict):
        return {"excluded": [], "notes": {}}
    data.setdefault("excluded", [])
    data.setdefault("notes", {})
    return data


def save_exclusions(folder: Path, data: dict):
    path = folder / "workflow" / EXCLUSIONS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _question_starts(lines: list[str], numbers):
    starts = []
    if _find is None:
        return starts
    for number in sorted({int(n) for n in numbers if str(n).isdigit()}):
        index = _find(lines, number)
        if index is not None:
            starts.append((index, number))
    return sorted(starts)


def _owner(index: int, starts):
    owner = None
    for start, number in starts:
        if start <= index:
            owner = number
        else:
            break
    return owner


def audit(folder: Path, numbers=None):
    """列出媒体文件、正文引用位置与判定。只读，不改文件。"""
    media_dir = folder / "01_source" / "media"
    source = folder / "01_source" / "正文.txt"
    lines = source.read_text(encoding="utf-8-sig", errors="replace").splitlines() if source.is_file() else []
    refs = {}
    for index, line in enumerate(lines):
        for match in IMG_REF.finditer(line):
            name = Path(match.group(1).strip()).name
            refs.setdefault(name, []).append(index)
    starts = _question_starts(lines, numbers or [])
    exclusions = load_exclusions(folder)
    excluded = {str(x) for x in exclusions.get("excluded", [])}
    items = []
    files = sorted(p for p in media_dir.iterdir() if p.is_file()) if media_dir.is_dir() else []
    for path in files:
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        size = image_size(path)
        ratio = round(size[0] / size[1], 2) if size else None
        positions = refs.get(path.name, [])
        owners = sorted({n for n in (_owner(i, starts) for i in positions) if n})
        if path.name in excluded:
            verdict, reason = "excluded", "已标记为非题目图片，上传时跳过"
        elif not positions:
            verdict, reason = "unused", "正文没有 [IMG] 引用，不属于任何小题（常见于卷头广告、水印）"
        elif positions and starts and min(positions) < starts[0][0]:
            verdict, reason = "header", "只出现在第一道题之前，属于卷头/说明区"
        elif ratio is not None and ratio >= 5:
            verdict, reason = "banner", f"长宽比 {ratio}:1，像横幅广告而不是题目配图"
        else:
            verdict, reason = "question", "被正文引用且落在题目范围内"
        items.append({"name": path.name, "bytes": path.stat().st_size,
                      "width": size[0] if size else None, "height": size[1] if size else None,
                      "ratio": ratio, "lines": [i + 1 for i in positions], "numbers": owners,
                      "verdict": verdict, "reason": reason})
    summary = {key: sum(1 for item in items if item["verdict"] == key)
               for key in ("question", "unused", "header", "banner", "excluded")}
    summary["total"] = len(items)
    summary["suspicious"] = summary["unused"] + summary["header"] + summary["banner"]
    return {"folder": folder.name, "items": items, "summary": summary,
            "exclusions": exclusions, "media_dir": str(media_dir)}
