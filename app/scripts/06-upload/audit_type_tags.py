#!/usr/bin/env python3
"""题型与子类体检（A6/A7/C1）：只查题型与子类目录值，不查知识点。

- A6：question_type ∈ taxonomy.types()（27 个固定主类）。
- A7：filterMetadata.subtypes ⊆ taxonomy.subtypes(question_type)；
      该题型若有子类目录则不能为空，无子类题型不强制。
- C1：题型/子类不得新增、改名（等价于 A6/A7 的目录校验）。

用法：audit_type_tags.py --batch <batch_meta.json>
退出码：0 = 通过；1 = 有问题。问题逐行打印到 stdout。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _common  # noqa: E402
from workbench import taxonomy  # noqa: E402


def _subtypes_of(item):
    """读取单元的 subtypes，兼容 filterMetadata.subtypes 与顶层 subtypes。

    返回 (列表, 来源字段名)。
    """
    fm = item.get("filterMetadata")
    if isinstance(fm, dict) and "subtypes" in fm:
        return fm.get("subtypes"), "filterMetadata.subtypes"
    if "subtypes" in item:
        return item.get("subtypes"), "subtypes"
    return None, None


def _check_unit(item, problems):
    uid = item.get("unit_id") or item.get("id") or "?"
    qtype = item.get("question_type")
    if qtype is None and "type" in item:
        qtype = item.get("type")

    # A6：题型必须是 27 个固定主类之一。
    if not isinstance(qtype, str) or not qtype.strip():
        problems.append(f"单元 {uid}：A6 question_type 缺失或非字符串：{qtype!r}")
        return
    qtype = qtype.strip()
    if qtype not in taxonomy.types():
        problems.append(f"单元 {uid}：A6 题型「{qtype}」不在 27 个固定主类里")
        return

    # A7：subtypes ⊆ 目录子类；有子类目录则不能为空。
    subs, field = _subtypes_of(item)
    allowed = taxonomy.subtypes(qtype)
    if subs is None:
        subs = []
    if not isinstance(subs, list):
        problems.append(f"单元 {uid}：A7 {field or 'subtypes'} 必须是字符串列表：{subs!r}")
        return
    clean = []
    for s in subs:
        if not isinstance(s, str) or not s.strip():
            problems.append(f"单元 {uid}：A7 子类列表项必须是非空字符串：{s!r}")
        else:
            clean.append(s.strip())
    if allowed and not clean:
        problems.append(f"单元 {uid}：A7 题型「{qtype}」有子类目录，subtypes 不能为空")
    bad = [s for s in clean if allowed and s not in allowed]
    if bad:
        problems.append(f"单元 {uid}：A7 子类 {bad} 不属于「{qtype}」，可选：{allowed}")


def main(argv=None):
    args = _common.arg_parser("题型与子类体检（A6/A7/C1）").parse_args(argv)
    problems = []
    try:
        batch = _common.load_batch(args.batch)
    except Exception as exc:
        print(f"[FAIL] 无法读取批次：{exc}")
        return 1

    for item in _common.units(batch):
        if not isinstance(item, dict):
            problems.append(f"单元必须是对象：{item!r}")
            continue
        try:
            _check_unit(item, problems)
        except Exception as exc:  # 容错：单条异常不影响其它题。
            problems.append(f"单元 {item.get('unit_id') or item.get('id') or '?'}：校验异常 {exc}")

    return _common.report(problems, ok_msg=f"题型与子类体检通过（{len(_common.units(batch))} 个单元）")


if __name__ == "__main__":
    raise SystemExit(main())
