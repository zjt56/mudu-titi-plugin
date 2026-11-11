#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""空档可见性体检（B11/D11）。

调用：python scripts/06-upload/audit_underlines.py --batch <batch_meta.json>
退出码 0=通过；非0=有阻断问题；问题逐行 print 到 stdout。

B11：material 里的空位必须写成 `__N__`（也接受提取阶段的 `<u>　N　</u>`）；
     不得出现裸编号空位（全角空格包纯数字 `　51　`）、`__ 51 __`、
     或裸 `51` 紧跟下划线等不可见写法。
W11：单词拼写/完成句子/单项填空 的题干无 `______` 只提示，不改原卷合法问句/图片表达。
D11：实际作答单元 answer 不得为空或只剩 `; ; ;`。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _common import arg_parser, load_batch, report, units

# W11 题型：题干未见 ______ 只作格式提示；不据此认定原卷缺内容。
BLANK_STEM_TYPES = ("单词拼写", "完成句子", "单项填空")

# 合法空位：__N__
OK_BLANK = re.compile(r"_{2,}\s*\d{1,3}\s*_{2,}")
# 合法空位（提取阶段保留的 <u>　N　</u>）
OK_BLANK_U = re.compile(r"<u>\s*\u3000*\s*\d{1,3}\s*\u3000*\s*</u>")

# B11 违规：全角空格包裹纯数字（裸编号空位），如 　51　
BAD_FULLWIDTH = re.compile(r"\u3000\s*(\d{1,3})\s*\u3000")
# B11 违规：__ 51 __（下划线中间夹空格和数字）
BAD_SPACED_UNDERSCORE = re.compile(r"_{2,}\s+\d{1,3}\s+_{2,}")
# B11 违规：裸数字紧跟下划线，如 51__
BAD_NAKED_DIGIT_UNDERSCORE = re.compile(r"(?<![\d_])\d{1,3}(?=_{2,})")

# D11 违规：answer 只剩分号
ANSWER_ONLY_SEMI = re.compile(r"^[;；\s]+$")


def _covered_by_u(text, start, end):
    """匹配区间是否已被 <u>…</u> 包裹。"""
    before = text.rfind("<u>", 0, start)
    close_before = text.rfind("</u>", 0, start)
    after = text.find("</u>", end)
    return before > close_before and after != -1


def _check_material(uid, material):
    problems = []
    text = str(material or "")
    if not text:
        return problems

    # 全角空格包纯数字
    for m in BAD_FULLWIDTH.finditer(text):
        if _covered_by_u(text, m.start(), m.end()):
            continue
        problems.append(
            f"[B11] 裸编号空位: {uid}（材料「{m.group(0)!r}」应为 __{m.group(1)}__ 或 <u>　{m.group(1)}　</u>）"
        )

    # __ 51 __
    for m in BAD_SPACED_UNDERSCORE.finditer(text):
        problems.append(
            f"[B11] 空位写法错误: {uid}（材料「{m.group(0)!r}」应为 __N__）")
    
    # 裸数字紧跟下划线
    for m in BAD_NAKED_DIGIT_UNDERSCORE.finditer(text):
        # 若前后已被 <u> 包裹则跳过
        end = m.end() + 2
        if _covered_by_u(text, m.start(), end):
            continue
        problems.append(
            f"[B11] 空位写法错误: {uid}（材料「{m.group(0)}__」应为 __{m.group(0)}__）")

    return problems


def _check_unit(u):
    problems = []
    uid = u.get("unit_id") or u.get("id") or "?"
    qt = (u.get("question_type") or "").strip()

    # B11：材料空位可见性
    problems.extend(_check_material(uid, u.get("material")))

    # W11 仅检查空档写法：原卷问句或图片填词可没有文本下划线。
    # 答案内容仍单独按 D11 阻断，不能让格式软化掩盖真实缺失。
    if qt in BLANK_STEM_TYPES:
        stem = str(u.get("stem") or "")
        native_spelling_blank = (qt == "单词拼写" and re.search(
            r"<u>(?:[A-Za-z])?(?:\s|&nbsp;|&#160;|&#xA0;)+</u>", stem, re.I))
        if "______" not in stem and not native_spelling_blank:
            problems.append(f"[W11] {qt} 题干缺空档: {uid}（未见 ______ 或单词拼写原生空白画线；保留原卷表达）")
        children = u.get("subquestions") or []
        # 题组答案在子题上；没有聚合 answer 不等于缺答案。
        targets = [(f"{uid}/{child.get('number', index)}", child)
                   for index, child in enumerate(children, 1) if isinstance(child, dict)] if children else [(uid, u)]
        if children and str(u.get("answer") or "").strip():
            targets.append((uid, u))  # 已提供的聚合答案仍不能只有分号。
        for target_id, question in targets:
            answer = str(question.get("answer") if question.get("answer") is not None else "").strip()
            if not answer:
                problems.append(f"[D11] {qt} 答案为空: {target_id}（应补实际答案）")
            elif ANSWER_ONLY_SEMI.match(answer):
                problems.append(f"[D11] {qt} 答案只剩分号: {target_id}（answer={answer!r}，应补实际答案）")

    return problems


def main(argv=None):
    args = arg_parser("空档可见性体检 B11/D11").parse_args(argv)
    try:
        batch = load_batch(args.batch)
    except Exception as exc:  # 容错：读不到批次也不抛 Traceback
        print(f"[ERROR] 无法读取批次文件 {args.batch}: {exc}")
        return 2

    problems = []
    for u in units(batch):
        if not isinstance(u, dict):
            problems.append(f"[格式] 非对象单元被忽略：{type(u).__name__}")
            continue
        try:
            problems.extend(_check_unit(u))
        except Exception as exc:  # 单个单元异常不中断整批
            uid = u.get("unit_id") or u.get("id") or "?"
            problems.append(f"[ERROR] 单元 {uid} 体检异常：{exc}")

    return report(problems, ok_msg=f"空档体检通过（{len(units(batch))} 个单元）")


if __name__ == "__main__":
    sys.exit(main())
