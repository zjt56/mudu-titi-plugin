#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题型版面体检（E20-选项条数 / D7 / D10）。

调用：python scripts/06-upload/audit_type_layout.py --batch <batch_meta.json>
退出码 0=通过，非0=有问题；问题逐行 print 到 stdout。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _common import arg_parser, load_batch, plain, report, units  # noqa: E402

# E20 选项条数基准。key 为题型主类，value=(期望条数, 检查位置)。
#   位置 "unit"        = 检查单元 options（standalone 或 group 的单元级选项表）
#   位置 "sub"         = 检查每个子题 options（完形选项挂在子题）
#   位置 "sub_or_unit" = 子题有选项就查子题，否则查单元级
OPTION_RULES = {
    "听力短对话理解": (3, "unit"),
    "单项填空": (4, "unit"),
    "阅读理解": (4, "sub_or_unit"),
    "补全对话": (7, "unit"),
    "完形填空": (4, "sub"),
    # 听力长对话/短文理解：选项按 A/B/C 三条；信息转换是直接填空，无选项
    "听力长对话理解": (3, "sub_or_unit"),
    "听力短文理解": (3, "sub_or_unit"),
}

# D7：材料已带齐题号时，子题 stem 不应重复题号（允许纯 '______'）。
# 覆盖：补全对话、听力信息转换、短文/选词/教材语篇填空。
D7_TYPES = ("补全对话", "听力信息转换", "短文填空", "选词填空", "教材语篇填空")

NUMBER_PREFIX = re.compile(r"^\s*(\d+)\s*[\.．]")


def _sub_numbers(u):
    """单元内所有子题号（int 优先，退回 numbers 字段）。"""
    nums = []
    for c in u.get("subquestions") or []:
        n = c.get("number")
        try:
            nums.append(int(n))
        except (TypeError, ValueError):
            pass
    if not nums:
        for n in u.get("numbers") or []:
            try:
                nums.append(int(n))
            except (TypeError, ValueError):
                pass
    return nums


def _material_has_number(text, n):
    """材料中是否出现该题号（数字作为独立 token，允许 __N__、　N　、N. 等）。"""
    if not text or n is None:
        return False
    # 数字前后不是别的数字，即独立出现
    return re.search(rf"(?<!\d){n}(?!\d)", text) is not None


def _check_option_count(problems, uid, qt, kind, u, subs, want, where):
    """按 (期望条数, 位置) 检查 options 数量。"""
    def _unit_count():
        cn = len(u.get("options") or [])
        if cn != want:
            problems.append(f"[E20] {uid} ({qt}): 单元选项应为 {want} 条，现有 {cn} 条")

    def _sub_counts():
        for c in subs:
            n = c.get("number")
            cn = len(c.get("options") or [])
            if cn != want:
                problems.append(f"[E20] {uid} ({qt}) 子题 {n}: 选项应为 {want} 条，现有 {cn} 条")

    if where == "unit":
        _unit_count()
    elif where == "sub":
        _sub_counts()
    elif where == "sub_or_unit":
        # 子题存在独立选项则查子题，否则查单元级
        has_sub_opts = any(c.get("options") for c in subs)
        if has_sub_opts:
            _sub_counts()
        else:
            _unit_count()


def _check_unit(u):
    problems = []
    uid = u.get("unit_id") or u.get("id") or "?"
    qt = (u.get("question_type") or "").strip()
    kind = (u.get("unit_kind") or "").strip()
    subs = u.get("subquestions") or []

    # D10：group 判定——有 subquestions 必须 unit_kind=group
    if subs and kind != "group":
        problems.append(f"[D10] {uid} ({qt}): 含 subquestions 但 unit_kind={kind!r}，应为 group")
    if kind == "group" and not subs:
        problems.append(f"[D10] {uid} ({qt}): unit_kind=group 但无 subquestions")

    # E20 选项条数
    if qt in OPTION_RULES:
        want, where = OPTION_RULES[qt]
        _check_option_count(problems, uid, qt, kind, u, subs, want, where)

    # D7：材料已带齐题号时，子题 stem 不应是重复题号（允许纯 ______）
    if qt in D7_TYPES:
        mat = plain(u.get("material") or "")
        nums = _sub_numbers(u)
        all_present = bool(nums) and all(_material_has_number(mat, n) for n in nums)
        if all_present:
            for c in subs:
                stem = plain(c.get("stem") or "").strip()
                if not stem:
                    continue  # 空串由结构检查 A9/D3 负责
                if stem.replace("_", "").strip() == "":
                    continue  # 纯 ______ 允许
                m = NUMBER_PREFIX.match(stem)
                if m:
                    problems.append(
                        f"[D7] {uid} ({qt}) 子题 {c.get('number')}: 材料已含题号，子题 stem 不应重复题号「{m.group(0).strip()}」")

    return problems


def main(argv=None):
    args = arg_parser("题型版面体检 E20-选项条数/D7/D10").parse_args(argv)
    try:
        batch = load_batch(args.batch)
    except Exception as exc:
        print(f"[ERROR] 无法读取批次文件 {args.batch}: {exc}")
        return 2

    problems = []
    for u in units(batch):
        if not isinstance(u, dict):
            problems.append(f"[格式] 非对象单元被忽略：{type(u).__name__}")
            continue
        try:
            problems.extend(_check_unit(u))
        except Exception as exc:
            uid = u.get("unit_id") or u.get("id") or "?"
            problems.append(f"[ERROR] 单元 {uid} 体检异常：{exc}")

    return report(problems, ok_msg=f"题型版面体检通过（{len(units(batch))} 个单元）")


if __name__ == "__main__":
    sys.exit(main())
