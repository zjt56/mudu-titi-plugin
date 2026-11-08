#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文本格式体检（B1/B2/B4/B9/B10）。

调用：python scripts/06-upload/check_text.py --batch <batch_meta.json>
退出码 0=通过；非0=有问题；问题逐行 print 到 stdout。
注意：workflow/checks.py 里本检查为 blocking=False（只报告，不阻断），
但脚本本身仍按退出码返回：有问题返回 1，一行文字正常返回 0。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _common import arg_parser, iter_questions, load_batch, plain, report, units

# B1/B2：全角空格（U+3000）连续空档被当作空位；真实空位应为 `______`。
U3000_RUN = re.compile(r"\u3000{2,}")

# B10：应使用真实换行 \n，不写 <br>。
BR = re.compile(r"<br\s*/?>", re.IGNORECASE)

# B4/B9：首字母提示应紧贴空档 `c______`；若写成 `c ______` 且后面跟随中文提示括号则报错。
# 形如：单个英文字母 + 空格 + 下划线空档 + （可选空格）+ 中文提示括号
LOOSE_INITIAL = re.compile(
    r"(?<![A-Za-z])([A-Za-z])\s+(_{2,})\s*[\(（][^()（）]*[\u4e00-\u9fff][^()（）]*[\)）]"
)


def _texts(unit, child):
    """产出本条题目需要检查的 (字段名, 文本)。"""
    if child is None:
        yield "stem", unit.get("stem") or ""
        yield "material", unit.get("material") or ""
    else:
        yield "stem", child.get("stem") or ""
        yield "material", child.get("material") or ""


def _check_unit(unit, child):
    problems = []
    uid = unit.get("unit_id") or unit.get("id") or "?"
    if child is not None:
        num = child.get("number")
        loc = f"{uid} 子题 {num}" if num is not None else f"{uid} 子题"
    else:
        loc = uid

    for field, raw in _texts(unit, child):
        text = str(raw or "")

        # B1/B2：U+3000 连续空档
        for _m in U3000_RUN.finditer(text):
            problems.append(f"[W] U+3000空档（提示，不阻断）: {uid}（{loc} {field}）")
            break  # 同一字段只报一次，避免刷屏

        # B10：<br>
        if BR.search(text):
            problems.append(f"[W] <br>换行（提示，不阻断）: {loc}（{field} 建议使用真实换行）")

        # B4/B9：首字母与空档被空格隔开，且后面有中文提示括号
        m = LOOSE_INITIAL.search(text)
        if m:
            problems.append(
                f"[W] 首字母提示未紧贴空档: {loc}（提示，不阻断）"
                f"（{field}「{m.group(1)} {m.group(2)}」建议「{m.group(1)}{m.group(2)}」）"
            )

    return problems


def main(argv=None):
    args = arg_parser("文本格式体检 B1/B2/B4/B9/B10（只报告）").parse_args(argv)
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
            problems.extend(_check_unit(u, None))
        except Exception as exc:  # 单个单元异常不中断整批
            uid = u.get("unit_id") or u.get("id") or "?"
            problems.append(f"[ERROR] 单元 {uid} 体检异常：{exc}")
        for c in (u.get("subquestions") or []):
            if not isinstance(c, dict):
                continue
            try:
                problems.extend(_check_unit(u, c))
            except Exception as exc:
                uid = u.get("unit_id") or u.get("id") or "?"
                problems.append(f"[ERROR] 单元 {uid} 子题体检异常：{exc}")

    return report(problems, ok_msg=f"文本格式体检通过（{len(units(batch))} 个单元）")


if __name__ == "__main__":
    sys.exit(main())
