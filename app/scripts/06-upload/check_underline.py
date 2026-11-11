#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""画线体检（E16/E19）。

调用：python scripts/06-upload/check_underline.py --batch <batch_meta.json>
退出码 0=通过；非0=有阻断问题；问题逐行 print 到 stdout。

E16：语音辨音选项 / 音标词表 / 划线提问短语，原文该画线的词必须写成 <u>…</u>。
     依据原文明确 <u> 标记或问句明确提到 underlined word/phrase；普通引号不代表画线。
     信息转换/完形空位应为 <u>　N　</u> 或 __N__。
E19：书面表达题干不宜含下划线占位 <u>　　</u> 或连续 ______（答题线由前端画）。
     这属于格式小问题：以 [W19] 输出为提示，不阻断流程（题目/题干/题号对不上才是实质错误）。
"""
from __future__ import annotations

import re
import sys
import tempfile
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _common import arg_parser, load_batch, report, units
from workflow.docx_extract import blocking_warnings

U3000 = "\u3000"

# 信息转换 / 完形空位：<u>　N　</u> 或 __N__ 都算有效空位。
VALID_BLANK_U = re.compile(r"<u>\s*\u3000*\s*\d{1,3}\s*\u3000*\s*</u>")
VALID_BLANK_US = re.compile(r"_{2,}\s*\d{1,3}\s*_{2,}")
# 应画线却漏画（残留的裸编号空位），如 　51　（全角空格包数字）。
LEAK_BLANK = re.compile(r"\u3000\s*\d{1,3}\s*\u3000")

# E19：书面表达题干里的下划线占位 / 连续下划线。
E19_U_PLACEHOLDER = re.compile(r"<u>\s*\u3000*\s*</u>")
E19_LONG_UNDERLINE = re.compile(r"_{4,}")

# info-transfer / cloze 类题型：材料里的空位必须是 <u>　N　</u> 或 __N__。
BLANK_TYPES = ("听力信息转换", "完形填空")


def _check_unit(u):
    problems = []
    uid = u.get("unit_id") or u.get("id") or "?"
    qt = (u.get("question_type") or "").strip()
    kind = (u.get("unit_kind") or "").strip()
    stem = str(u.get("stem") or "")
    material = str(u.get("material") or "")

    # Only explicit underline references identify required marked words. Ordinary
    # quotation marks (programme names, titles, speech) carry no such implication.
    question_texts = [stem] + [str(c.get("stem") or "") for c in u.get("subquestions") or [] if isinstance(c, dict)]
    passage = stem + "\n" + material
    marked = {re.sub(r"<[^>]+>|\*\*", "", m).strip().casefold()
              for m in re.findall(r"<u>(.*?)</u>", passage, re.S)}
    for text in question_texts:
        clean = re.sub(r"<[^>]+>|\*\*", "", text)
        terms = re.findall(r'''\bunderlined\s+(?:word|phrase)\s*[“"']([^”"']+)[”"']''', clean, re.I)
        terms += re.findall(r'''[“"']([^”"']+)[”"']\s+underlined\b''', clean, re.I)
        for term in terms:
            if term.strip().casefold() not in marked:
                problems.append(f"[E16] 问句明确指向的画线词缺失: {uid}（{term.strip()}）")

    # E16：信息转换/完形空位，材料里不得残留裸编号空位（应为 <u>　N　</u> 或 __N__）
    if qt in BLANK_TYPES:
        for m in LEAK_BLANK.finditer(material):
            # 若整个匹配已被 <u>...</u> 包裹则有效
            around = material[max(0, m.start() - 6): m.end() + 6]
            if "<u>" in around and "</u>" in around:
                continue
            problems.append(f"[W16] 空位未画线（提示，不阻断）: {uid}"
                            f"（材料「{m.group(0)!r}」建议 <u>　N　</u> 或 __N__）")

    # E19：书面表达题干不得含下划线占位 / 连续下划线
    if qt == "书面表达":
        if E19_U_PLACEHOLDER.search(stem):
            problems.append(f"[W19] 书面表达题干含下划线占位（不阻断）: {uid}（<u>　　</u> 建议删除，答题线由前端画）")
        if E19_LONG_UNDERLINE.search(stem):
            problems.append(f"[W19] 书面表达题干含连续下划线（不阻断）: {uid}（_____ 建议删除，答题线由前端画）")

        # 子题也检查（若有）
        for c in (u.get("subquestions") or []):
            cstem = str(c.get("stem") or "")
            cn = c.get("number")
            if E19_U_PLACEHOLDER.search(cstem) or E19_LONG_UNDERLINE.search(cstem):
                problems.append(f"[W19] 书面表达子题 {cn} 题干含答题下划线（不阻断）: {uid}（建议删除）")

    return problems


def _check_source_underlines(batch, source, batch_path=None):
    """Compare explicit source runs without mutating the candidate or source."""
    if source is None:
        print("提示：未提供原文，画线仅依据明确问句检查；不能据此宣称已核对原文画线。")
        return []
    if source.suffix.lower() not in {".docx", ".txt", ".md"}:
        print("提示：当前原文格式无法在本检查读取画线，须保留原页视觉核验。")
        return []
    # Reuse the context-aware restoration planner in read-only mode: a proposed
    # restoration is evidence of a missing mark, never permission to apply it.
    import importlib.util
    spec = importlib.util.spec_from_file_location("underline_source_planner", Path(__file__).with_name("mark_underline.py"))
    planner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(planner)
    warnings = []
    if source.suffix.lower() == ".docx":
        from workflow.docx_extract import extract_docx
        with tempfile.TemporaryDirectory(prefix="check-underline-") as directory:
            extracted = extract_docx(source, Path(directory))
        text = extracted["text"]
        warnings = extracted.get("warnings") or []
    else:
        text = source.read_text(encoding="utf-8-sig")
    _, result = planner.restore(batch, text)
    # 批次条目里已写成 <u>词</u> 的划线词，即使未能在 docx 中自动定位（例如空档归一化后
    # 上下文与原文不再逐字相同），也应视为已保留，不报"映射缺失"的假阳性。
    already = set()
    def _collect(obj):
        if isinstance(obj, dict):
            for v in obj.values():
                if isinstance(v, str):
                    for m in re.findall(r"<u>(.*?)</u>", v, re.S):
                        already.add(re.sub(r"<[^>]+>|\*\*", "", m).strip().casefold())
                else:
                    _collect(v)
        elif isinstance(obj, list):
            for v in obj:
                _collect(v)
    try:
        _collect(batch if isinstance(batch, dict) else load_batch(batch))
    except Exception:
        pass
    problems = [f"[E16] 原文画线未保留: {change['underlined']}（{change['field']}）"
                for change in result["changes"]]
    for issue in result["issues"]:
        word = str(issue.get("underlined", "")).strip().casefold()
        if issue.get("code") == "UNDERLINE_MAPPING_MISSING" and word and word in already:
            continue
        problems.append(f"[E16] {issue['code']}: {issue['underlined']}；{issue['message']}")
    if warnings:
        # OCR auto 复核已产生图像补充证据时，原生 docx 浮动/符号告警转为
        # 版面复核提示，不再阻断下游结构与题目体检。
        evidence = (Path(batch_path).resolve().parent.parent / "01_source" / "extraction.json" if batch_path else None)
        auto_ocr = False
        try:
            data = json.loads(evidence.read_text(encoding="utf-8-sig")) if evidence else {}
            auto_ocr = bool(data.get("ocr_auto") and data.get("image_ocr"))
        except (OSError, ValueError, TypeError):
            auto_ocr = False
        if not auto_ocr and blocking_warnings(warnings):
            problems.append(f"[E16] 原文提取有不确定项：{warnings}")
    return problems


def main(argv=None):
    args = arg_parser("画线体检 E16/E19").parse_args(argv)
    try:
        batch = load_batch(args.batch)
    except Exception as exc:  # 容错：读不到批次也不抛 Traceback
        print(f"[ERROR] 无法读取批次文件 {args.batch}: {exc}")
        return 2

    problems = []
    try:
        problems.extend(_check_source_underlines(batch, args.source, args.batch))
    except (OSError, ValueError, TypeError) as exc:
        problems.append(f"[E16] 无法核对原文画线：{exc}")
    for u in units(batch):
        if not isinstance(u, dict):
            problems.append(f"[格式] 非对象单元被忽略：{type(u).__name__}")
            continue
        try:
            problems.extend(_check_unit(u))
        except Exception as exc:  # 单个单元异常不中断整批
            uid = u.get("unit_id") or u.get("id") or "?"
            problems.append(f"[ERROR] 单元 {uid} 体检异常：{exc}")

    return report(problems, ok_msg=f"画线体检通过（{len(units(batch))} 个单元）")


if __name__ == "__main__":
    sys.exit(main())
