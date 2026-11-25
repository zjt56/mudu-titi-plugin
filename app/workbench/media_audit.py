# -*- coding: utf-8 -*-
"""媒体归属体检：区分题目图片与卷头广告、水印、没有被正文引用的图片。

已确认 scoped 单元的有效来源切片优先证明图片归属；其余图片使用可复核的启发式，不做图像内容识别：

1. 正文有没有 `[IMG 文件名]` 引用（没有引用 → 不可能属于某道题）；
2. 图片出现在第一道题之前还是之后（卷头横幅、水印一般在前面）；
3. 长宽比是否像横幅（>= 5:1）。

结论是“候选判定”，不是人工结论：工作台把三类可疑图片列出来，由人勾选排除；
排除清单写在 `workflow/media-exclusions.json`，上传阶段应跳过这些文件。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from workflow.docx_extract import (
    image_dimensions as image_size,
    is_decorative_media,
    option_image_names,
)

IMG_REF = re.compile(r"\[IMG\s+([^\]]+)\]")
EXCLUSIONS = "media-exclusions.json"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

try:  # preview 里有题号识别规则，复用同一套，避免两套题号口径
    from workflow.preview import _find  # type: ignore[attr-defined]
except Exception:  # pragma: no cover - 直接运行且路径没配好时退化为不判定卷头
    _find = None


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


def _section_title(value) -> str:
    """Strip decorative [IMG ...] markers from headings to match question_identity.source_section.title."""
    raw = str(value or "")
    return re.sub(r"\s*\[IMG\s+[^\]]*\]", "", raw).strip() or raw


def _scoped_image_owners(folder: Path, lines: list[str]):
    """Use the preview's checked source window, never an unchecked span or stem alone."""
    try:
        confirmed = json.loads((folder / "01_source" / "structure.confirmed.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    if not isinstance(confirmed, dict) or not isinstance(confirmed.get("units"), list):
        return {}
    from workflow.preview import _scoped_source_slice
    from workflow.denoise import clean_body
    from workflow.lesson_bands import annotate_sections, for_text
    from workflow.sync_practice import heading_candidates
    cleaned_text = clean_body("\n".join(lines))[0]
    sections = annotate_sections(heading_candidates(cleaned_text), for_text(folder, cleaned_text))
    units = [unit for unit in confirmed["units"] if isinstance(unit, dict)
             and unit.get("identity_version") == "scoped-v1"]
    ids = [str(unit.get("unit_id") or "") for unit in units]
    owners = {}
    for unit, uid in zip(units, ids):
        span = unit.get("source_span")
        if not uid or ids.count(uid) != 1 or not isinstance(span, dict):
            continue
        if (span.get("basis") not in ("ai_classified", "cleaned_source")
                or unit.get("unit_kind") not in ("group", "standalone")):
            continue
        section = unit.get("source_section")
        if not isinstance(section, dict):
            continue
        matches = [row for row in sections
                   if all(row.get(key) == section.get(key)
                          for key in ("start_line", "end_line"))
                   and _section_title(row.get("title")) == _section_title(section.get("title"))
                   and all(row.get(key) == section[key]
                           for key in ("context", "occurrence") if key in section)]
        start, end = span.get("start_line"), span.get("end_line")
        if (len(matches) != 1 or type(start) is not int or type(end) is not int
                or not matches[0]["start_line"] <= start <= end <= matches[0]["end_line"]):
            continue
        # Keep the full group window, including images before its first number,
        # only after proving that window still belongs to the current section.
        chunk = _scoped_source_slice(unit, lines)
        for line in chunk:
            for match in IMG_REF.finditer(line):
                name = Path(match.group(1).strip()).name
                owners.setdefault(name, set()).add(uid)
    return {name: sorted(uids) for name, uids in owners.items() if len(uids) == 1}


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
    scoped_owners = _scoped_image_owners(folder, lines)
    option_imgs = option_image_names(lines)
    exclusions = load_exclusions(folder)
    excluded = {str(x) for x in exclusions.get("excluded", [])}
    items = []
    files = sorted(p for p in media_dir.iterdir() if p.is_file()) if media_dir.is_dir() else []
    for path in files:
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        size = image_size(path)
        file_bytes = path.stat().st_size
        ratio = round(size[0] / size[1], 2) if size else None
        positions = refs.get(path.name, [])
        owners = sorted({n for n in (_owner(i, starts) for i in positions) if n})
        if path.name in excluded:
            verdict, reason = "excluded", "已标记为非题目图片，上传时跳过"
        elif not positions:
            verdict, reason = "unused", "正文没有 [IMG] 引用，不属于任何小题（常见于卷头广告、水印）"
        elif path.name in scoped_owners:
            verdict, reason = "question", "已由 scoped 单元的有效来源切片证明图片归属"
        elif path.name in option_imgs and len(positions) == 1:
            verdict, reason = "question", "正文中作为题目选项图片引用"
        elif positions and starts and min(positions) < starts[0][0]:
            verdict, reason = "header", "只出现在第一道题之前，属于卷头/说明区"
        elif is_decorative_media(size[0] if size else None, size[1] if size else None, file_bytes, len(positions)):
            reason = (f"装饰胶囊/徽章（{size[0]}×{size[1]}）"
                      if size and size[1] <= 62 else
                      f"高频小图标（{len(positions)} 次）"
                      if len(positions) >= 2 and (not size or size[1] <= 120) else
                      f"长宽比 {ratio}:1，像横幅广告/水印而不是题目配图")
            verdict = "banner"
        else:
            verdict, reason = "question", "被正文引用且落在题目范围内"
        items.append({"name": path.name, "bytes": file_bytes,
                      "width": size[0] if size else None, "height": size[1] if size else None,
                      "ratio": ratio, "lines": [i + 1 for i in positions], "numbers": owners,
                      "unit_ids": scoped_owners.get(path.name, []),
                      "verdict": verdict, "reason": reason})
    summary = {key: sum(1 for item in items if item["verdict"] == key)
               for key in ("question", "unused", "header", "banner", "excluded")}
    summary["total"] = len(items)
    summary["suspicious"] = summary["unused"] + summary["header"] + summary["banner"]
    return {"folder": folder.name, "items": items, "summary": summary,
            "exclusions": exclusions, "media_dir": str(media_dir)}
