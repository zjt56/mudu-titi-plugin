"""Word × 视觉交叉印证（候选留痕，不生成内容、不据此放行闸门）。

分工：
- 文字内容（题干/选项/答案/解析）永远以 Word 原文为准，本模块绝不改写；
- 视觉只解释 `[IMG name]` 占位"这张图是什么"（image_semantics.json），
  辅助判断图片题/材料表格/装饰图，交叉印证 Word 切出的结构与图片实际内容；
- 冲突只报告、留痕，不自动改原文、不升级闸门判定。

产物：01_source/source_crosscheck.json —— 逐条 {占位, 行号, 所属单元, 图片文件,
视觉语义, 与上下文是否吻合, 说明} + 汇总。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

VERSION = "visual-crosscheck-v1"
ARTIFACT = "01_source/source_crosscheck.json"
SEMANTICS = "01_source/image_semantics.json"
BODY = "01_source/正文.txt"
CONFIRMED = "01_source/structure.confirmed.json"

IMG = re.compile(r"\[IMG\s+([^\]]+?)\s*\]")

# 视觉语义 kind 与"所在上下文"是否吻合的粗判（只作提示，不硬判）。
_DECORATIVE = {"icon"}  # 标题栏/装饰小图标：出现在标题/题号前较正常
_MATERIAL = {"material_table", "chart", "page"}


def _safe(root: Path, rel: str) -> Path:
    return (root / rel)


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _unit_for_line(units, line_no):
    """占位所在行归属的单元（按 source_span 覆盖）；无则 None。"""
    for unit in units:
        span = unit.get("source_span") or {}
        lo, hi = span.get("start_line"), span.get("end_line")
        if type(lo) is int and type(hi) is int and lo <= line_no <= hi:
            return unit
    return None


def _verdict(img_kind, unit, line_text, img_name):
    """粗判视觉语义与所在上下文是否吻合；只提示，不下硬结论。"""
    kind = str(img_kind or "other")
    text = (line_text or "").strip()
    if kind in _DECORATIVE:
        # 标题栏/装饰图标：若该行几乎没有题目文字，通常属正常装饰/板块头。
        if not text:
            return "ok", "所在行为空/仅占位，装饰性图标/标题栏属正常"
        return "note", "所在行有文字，图标可能是装饰或配图，供参考"
    if kind in _MATERIAL:
        return "ok", "材料表格/图表/整页：应随所属题目材料呈现"
    if kind == "option_image":
        return "ok", "选项配图：应按选项归属"
    if kind == "other":
        return "note", "视觉未给出明确类别，需人工/后续核对"
    return "note", "供参考"


def build(batch_dir) -> dict:
    """生成 01_source/source_crosscheck.json；缺前置产物时返回 skipped，不阻断。"""
    root = Path(batch_dir).resolve()
    semantics = _load_json(_safe(root, SEMANTICS))
    if not isinstance(semantics, dict) or not semantics.get("items"):
        return {"status": "skipped", "reason": "缺少 image_semantics.json（先跑视觉复核）", "artifact": None}
    body_path = _safe(root, BODY)
    if not body_path.is_file():
        return {"status": "skipped", "reason": "缺少 01_source/正文.txt", "artifact": None}
    lines = body_path.read_text(encoding="utf-8-sig").splitlines()
    confirmed = _load_json(_safe(root, CONFIRMED)) or {}
    units = confirmed.get("units") or []

    # 图片语义按文件名索引
    by_name = {}
    for item in semantics.get("items") or []:
        name = str(item.get("path") or "").split("/")[-1]
        if name:
            by_name[name] = item

    rows, seen = [], set()
    for index, line in enumerate(lines, start=1):
        for match in IMG.finditer(line):
            raw = match.group(1).strip()
            name = raw.split("/")[-1]
            seen.add(name)
            sem = by_name.get(name) or {}
            unit = _unit_for_line(units, index)
            kind = sem.get("kind") or ""
            # 该行去掉占位后的可见文字，判断是否纯图行
            stripped = IMG.sub("", line).strip()
            verdict, reason = _verdict(kind, unit, stripped, name)
            rows.append({
                "img": raw,
                "file": name,
                "line": index,
                "unit_id": unit.get("unit_id") if unit else None,
                "numbers": unit.get("numbers") if unit else None,
                "unit_type": unit.get("type") if unit else None,
                "visual_kind": kind or None,
                "visual_title": sem.get("title") or "",
                "visual_has_text": bool(sem.get("contains_text")),
                "line_has_text": bool(stripped),
                "verdict": verdict,
                "note": reason,
            })

    # 视觉识别到、但正文无对应 [IMG] 的图片（可能是页眉/装饰/未引用）
    unused = sorted(name for name in by_name if name not in seen)

    # 汇总
    from collections import Counter
    verdicts = Counter(row["verdict"] for row in rows)
    payload = {
        "version": VERSION,
        "note": "Word 原文为文字权威来源；视觉语义仅解释 [IMG] 占位内容并交叉印证，"
                "冲突只留痕不自动修改、不据此放行任何闸门。",
        "image_refs": len(rows),
        "unreferenced_images": unused,
        "verdicts": dict(verdicts),
        "rows": rows,
    }
    out_path = _safe(root, ARTIFACT)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out_path)
    return {"status": "built", "artifact": ARTIFACT, "image_refs": len(rows),
            "verdicts": dict(verdicts), "unreferenced": len(unused)}


def report(batch_dir) -> str:
    """人读摘要；仅供展示。"""
    root = Path(batch_dir).resolve()
    data = _load_json(_safe(root, ARTIFACT))
    if not isinstance(data, dict):
        return "（无交叉印证产物）"
    out = [f"图片引用 {data.get('image_refs', 0)} 处；"
           f"判定分布 {data.get('verdicts', {})}；未引用图片 {len(data.get('unreferenced_images') or [])} 张"]
    for row in data.get("rows") or []:
        out.append(f"  行{row['line']} {row['img']} → {row.get('visual_kind') or '?'} "
                   f"[{row['verdict']}] {row['note']}")
    if data.get("unreferenced_images"):
        out.append("  未引用： " + "、".join(data["unreferenced_images"]))
    return "\n".join(out)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Word × 视觉交叉印证（候选留痕）")
    parser.add_argument("--batch-dir", required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.batch_dir), ensure_ascii=False, indent=2))
    print()
    print(report(args.batch_dir))
