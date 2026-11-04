#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批次完整性体检（E20 四条 + D/F 残留）。

E20 四条：
1. 相邻单元 material 不得互相包含（一个是另一个的子串且长度差很大）。
2. 字段不得残留 `[来源:` / `[来源：` 或学科网水印字样。
3. 听力组题（question_type 以"听力"开头且 unit_kind=group）material 必须包含「听力原文」。
4. 所有 options text 不得为空字符串。

调用：python scripts/06-upload/audit_batch_integrity.py --batch <batch_meta.json>
退出码 0=通过，非0=有问题；问题逐行 print 到 stdout。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _common import arg_parser, load_batch, plain, report, units  # noqa: E402

# 相邻单元 material 子串包含判定的长度差门槛（相差超过该值才算"长度差很大"）
CONTAIN_LEN_DIFF = 20

# 学科网水印 / 来源残留字样
WATERMARKS = ("[来源:", "[来源：", "学科网", "zxxk.com", "www.zxxk.com", "版权所有")


def _str_fields(u, c):
    """需要扫描残留的字符串字段：unit 与子题的文本字段。"""
    fields = []
    for src in (u, c):
        if not isinstance(src, dict):
            continue
        for key in ("stem", "material", "answer", "explanation"):
            v = src.get(key)
            if isinstance(v, str):
                fields.append((key, v))
        for opt in src.get("options") or []:
            if isinstance(opt, dict):
                t = opt.get("text")
                if isinstance(t, str):
                    fields.append(("option.text", t))
    return fields


def _check_residuals(u):
    problems = []
    uid = u.get("unit_id") or u.get("id") or "?"
    for c in [None] + list(u.get("subquestions") or []):
        loc = uid if c is None else f"{uid} 子题 {c.get('number')}"
        for key, text in _str_fields(u, c):
            for wm in WATERMARKS:
                if wm in text:
                    problems.append(f"[E20-残留] {loc}.{key}: 残留疑似水印/来源字样「{wm}」")
    return problems


def _check_option_text(u):
    problems = []
    uid = u.get("unit_id") or u.get("id") or "?"
    for c in [None] + list(u.get("subquestions") or []):
        loc = uid if c is None else f"{uid} 子题 {c.get('number')}"
        for opt in (u.get("options") if c is None else c.get("options")) or []:
            if not isinstance(opt, dict):
                continue
            text = opt.get("text")
            if text is None:
                problems.append(f"[E20-空选项] {loc}: 选项 {opt.get('label')!r} text 缺失（None）")
            elif isinstance(text, str) and text.strip() == "":
                problems.append(f"[E20-空选项] {loc}: 选项 {opt.get('label')!r} text 为空字符串")
    return problems


def _check_listening(u):
    problems = []
    uid = u.get("unit_id") or u.get("id") or "?"
    qt = (u.get("question_type") or "").strip()
    kind = (u.get("unit_kind") or "").strip()
    if qt.startswith("听力") and kind == "group":
        mat = plain(u.get("material") or "")
        if "听力原文" not in mat:
            problems.append(f"[E20-听力] {uid} ({qt}): 听力组题 material 缺少「听力原文」段")
    return problems


def _check_adjacent_materials(unit_list):
    """相邻单元 material 不得互相包含（子串且长度差很大）。"""
    problems = []
    mats = []
    for u in unit_list:
        uid = u.get("unit_id") or u.get("id") or "?"
        mats.append((uid, plain(u.get("material") or "").strip()))
    for i in range(len(mats) - 1):
        (ida, a), (idb, b) = mats[i], mats[i + 1]
        if not a or not b:
            continue
        short, long_ = (a, b) if len(a) <= len(b) else (b, a)
        sid, lid = (ida, idb) if len(a) <= len(b) else (idb, ida)
        if len(long_) - len(short) >= CONTAIN_LEN_DIFF and short in long_:
            problems.append(
                f"[E20-包含] 相邻单元 {sid} 的 material 是 {lid} 的 material 的子串"
                f"（长度 {len(short)}/{len(long_)}），疑似重复材料")
    return problems


def main(argv=None):
    args = arg_parser("批次完整性体检 E20 四条 + 残留").parse_args(argv)
    try:
        batch = load_batch(args.batch)
    except Exception as exc:
        print(f"[ERROR] 无法读取批次文件 {args.batch}: {exc}")
        return 2

    problems = []
    unit_list = [u for u in units(batch) if isinstance(u, dict)]

    # E20-1 相邻单元材料互相包含
    try:
        problems.extend(_check_adjacent_materials(unit_list))
    except Exception as exc:
        problems.append(f"[ERROR] 相邻材料包含检查异常：{exc}")

    for u in units(batch):
        if not isinstance(u, dict):
            problems.append(f"[格式] 非对象单元被忽略：{type(u).__name__}")
            continue
        uid = u.get("unit_id") or u.get("id") or "?"
        try:
            problems.extend(_check_residuals(u))
            problems.extend(_check_option_text(u))
            problems.extend(_check_listening(u))
        except Exception as exc:
            problems.append(f"[ERROR] 单元 {uid} 体检异常：{exc}")

    return report(problems, ok_msg=f"批次完整性体检通过（{len(unit_list)} 个单元）")


if __name__ == "__main__":
    sys.exit(main())
