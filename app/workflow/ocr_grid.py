# -*- coding: utf-8 -*-
"""按 OCR 版面块坐标判定"网格题组"里每个小题自己的图文归属。

背景（2026-11-13 用户批次「Starter Unit 2 课时二 Pronunciation」）：识别服务返回的
Markdown 把双栏网格题（Ⅱ. 根据图片及所给音标写出下列单词所缺的字母）按服务顺序
展平，题号、图片、词形行交错出现，正文里第一题窗口为空、第二题窗口同时收进前两
题的文字，装配报 A8/A9。规则 28 禁止自行重排识别服务正文，所以这里不改正文，只用
服务给出的 `block_bbox` 把"这个图片/这行字符属于哪一题"判定出来，作为内容归属证据。

判定口径（全部保守，任一不成立就返回 None，由调用方保留原样并留诊断）：

* 只能命中结构里真实存在的 section 标题块，且题号块数量、题号集合与确认单元一致；
* 版面必须真的是多栏网格（按块中心 x 的最大间隙切分，间隙太小视为单栏，不处理）；
* 每栏内必须是"图片在题号前、词形行在题号后"的可判定形态；
* 文字块不得含 HTML/LaTeX/图片标记（投影不完整的不猜）；
* 每张图片只能归属一个小题，不能有剩余图片或剩余文字。

这里只做绑定取证：不生成文字、不改写原文、不新增题号。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

VERSION = "ocr-grid-items/v1"

_NUMBER = re.compile(r"^\s*(\d{1,3})\s*[.．、]\s*$")
_HEADING_CLEAN = re.compile(r"^[#\s]+|[#\s]+$")
_RISKY_TEXT = re.compile(r"[<>$]|\[IMG")
_IMAGE_LABELS = {"image", "figure", "chart"}
_SPACE = re.compile(r"\s+")


def _norm(value: str) -> str:
    return _SPACE.sub("", _HEADING_CLEAN.sub("", str(value or ""))).lower()


def _pages(root: Path) -> list[dict]:
    path = Path(root) / "01_source" / "pdf-ocr.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    pages = []
    for source in data.get("sources") or []:
        if not isinstance(source, dict) or source.get("status") not in (
                None, "done", "completed", "succeeded", "ok"):
            continue
        for page in source.get("pages") or []:
            blocks = page.get("blocks")
            if isinstance(blocks, list) and blocks:
                pages.append({"page": int(page.get("page") or 1), "blocks": blocks})
    return pages


def _bbox(block: dict) -> list[float] | None:
    box = block.get("bbox") or block.get("block_bbox")
    if not isinstance(box, list) or len(box) != 4:
        return None
    try:
        values = [float(value) for value in box]
    except (TypeError, ValueError):
        return None
    if values[2] <= values[0] or values[3] <= values[1]:
        return None
    return values


def _kind(block: dict) -> str:
    return str(block.get("type") or block.get("block_label") or "").strip().lower()


def _text(block: dict) -> str:
    return str(block.get("text") or block.get("block_content") or "").strip()


def _media_name(root: Path, page: int, ordinal: int) -> str | None:
    media = Path(root) / "01_source" / "media"
    if not media.is_dir():
        return None
    matches = sorted(media.glob(f"pdf-page-{page}-image-{ordinal}.*"))
    return matches[0].name if matches else None


def _image_names(root: Path, page: int, blocks: list[dict]) -> dict[int, str]:
    """识别服务图片块顺序 → 已落盘媒体名（与正文投影同一顺序）。"""
    names, ordinal = {}, 0
    for index, block in enumerate(blocks):
        if _kind(block) in _IMAGE_LABELS:
            ordinal += 1
            name = _media_name(root, page, ordinal)
            if name:
                names[index] = name
    return names


def _largest_gap(values: list[float]) -> tuple[int, float]:
    best_index, best_gap = 0, 0.0
    for position in range(1, len(values)):
        gap = values[position] - values[position - 1]
        if gap > best_gap:
            best_index, best_gap = position, gap
    return best_index, best_gap


def section_items(root, *, section_title: str, numbers: list[int]) -> dict | None:
    """Return this grid section's per-item image/text ownership, else None."""
    target = _norm(section_title)
    wanted = {int(number) for number in numbers or []}
    if not target or not wanted:
        return None
    root = Path(root)
    for page in _pages(root):
        blocks = page["blocks"]
        boxes = [_bbox(block) for block in blocks]
        titles = [index for index, block in enumerate(blocks)
                  if _kind(block) in {"paragraph_title", "doc_title"} and _bbox(block)]
        title_index = next(
            (index for index in titles if _norm(_text(blocks[index])).startswith(target)), None)
        if title_index is None:
            continue
        title_box = boxes[title_index]
        top = title_box[3]
        below = [boxes[index][1] for index in titles
                 if index != title_index and boxes[index][1] > title_box[1]]
        bottom = min(below) if below else max(box[3] for box in boxes if box)
        # 多栏页面：栏目块与旁边栏目的正文在 y 上重叠，只取与本栏目标题同一水平带的块，
        # 否则会把邻栏的阅读理解正文当成这一节的图文。
        left = title_box[0] - max(30.0, (title_box[2] - title_box[0]) * 0.05)
        right = title_box[2] + max(30.0, (title_box[2] - title_box[0]) * 0.05)
        band = [index for index, box in enumerate(boxes)
                if box and top <= (box[1] + box[3]) / 2 < bottom
                and left <= (box[0] + box[2]) / 2 <= right]
        markers = {index: int(_NUMBER.match(_text(blocks[index])).group(1))
                   for index in band if _NUMBER.match(_text(blocks[index]))}
        if sorted(markers.values()) != sorted(wanted):
            return None
        images = _image_names(root, page["page"], blocks)
        centers = sorted((boxes[index][0] + boxes[index][2]) / 2 for index in band)
        split, gap = _largest_gap(centers)
        width = max(boxes[index][2] for index in band) - min(boxes[index][0] for index in band)
        if len(centers) < 4 or gap < max(40.0, width * 0.15):
            return None
        boundary = (centers[split - 1] + centers[split]) / 2
        columns: dict[int, list[int]] = {0: [], 1: []}
        for index in band:
            center = (boxes[index][0] + boxes[index][2]) / 2
            columns[0 if center < boundary else 1].append(index)
        items: dict[int, dict] = {}
        for indexes in columns.values():
            held: list[str] = []
            current: dict | None = None
            for index in sorted(indexes, key=lambda item: (boxes[item][1], boxes[item][0])):
                number = markers.get(index)
                if number is not None:
                    if number in items:
                        return None
                    current = {"number": number, "images": list(held),
                               "texts": [], "bbox": boxes[index]}
                    items[number] = current
                    held = []
                    continue
                name = images.get(index)
                if name:
                    if current is not None and not current["texts"]:
                        current["images"].append(name)
                    else:
                        held.append(name)
                    continue
                text = _text(blocks[index])
                if _RISKY_TEXT.search(text):
                    return None
                text = _SPACE.sub(" ", text).strip()
                if not text:
                    return None
                if current is None:
                    return None
                current["texts"].append(text)
            if held:
                return None
        if set(items) != wanted:
            return None
        for item in items.values():
            if not item["texts"]:
                return None
        return {"version": VERSION, "page": page["page"], "section": section_title,
                "basis": "ocr_blocks_grid", "items": items}
    return None


def item_stem(item: dict) -> str:
    """Render one cell: its own images first, then its own printed word rows."""
    parts = [f"[IMG {name}]" for name in item.get("images") or []]
    parts.extend(item.get("texts") or [])
    return "\n".join(part.strip() for part in parts if str(part).strip()).strip()


def text_key(value: str) -> str:
    """Compare ownership without depending on markup, blanks or line breaks."""
    plain = re.sub(r"\[IMG[^\]]*\]", "", str(value or ""))
    return _norm(re.sub(r"[_＿]+", "", plain))


def needs_repair(stems: dict[int, str], grid: dict) -> bool:
    """Only rewrite when the linear parse provably put content in the wrong cell."""
    if not grid or not grid.get("items"):
        return False
    for number, item in grid["items"].items():
        current = text_key(stems.get(int(number), ""))
        if not current:
            return True
        for other_number, other in grid["items"].items():
            if int(other_number) == int(number):
                continue
            if text_key("\n".join(other["texts"])) and current == text_key("\n".join(other["texts"])):
                return True
    return False
