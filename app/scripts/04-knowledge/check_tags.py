#!/usr/bin/env python3
"""标签体检：逐题校验主类/子类/知识点叶子/维度，并补充 A3、A22。

- 逐 item 调用 workbench.taxonomy.check_question_labels(item)，汇总问题。
- A3：filterMetadata.difficulty 若存在必须 ∈ {容易, 适中, 困难}。
- A22：item 有 knowledge 名称时，应同时给出 knowledgeIds（驼峰）与
  knowledge_ids（下划线），两者都必须与结构化目录逐项一致。
  正式批次缺 ID 或路径/ID 不一致均阻断，不能降级为 warning。

用法：check_tags.py --batch <batch_meta.json>
退出码：0 = 通过；1 = 有阻断问题。问题逐行打印到 stdout。
"""
from __future__ import annotations

import sys
from pathlib import Path

# scripts/_common.py 位于上一级目录，且会把仓库 ROOT 加入 sys.path。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _common  # noqa: E402
from workbench import taxonomy  # noqa: E402

_VALID_DIFFICULTY = ("容易", "适中", "困难")


def _label(item, child):
    if child is None:
        return f"单元 {item.get('unit_id') or item.get('id') or '?'}"
    return f"单元 {item.get('unit_id') or item.get('id') or '?'} 子题 {child.get('number')}"


def _check_difficulty(item, child, problems):
    """A3：filterMetadata.difficulty 若存在必须合法。子题没有独立 filterMetadata。"""
    if child is not None:
        return
    fm = item.get("filterMetadata")
    if not isinstance(fm, dict):
        fm = {}
    if "difficulty" in fm:
        value = fm.get("difficulty")
        if not isinstance(value, str) or value.strip() not in _VALID_DIFFICULTY:
            problems.append(
                f"{_label(item, child)}：A3 难度「{value}」不合法，只能是 {'/'.join(_VALID_DIFFICULTY)}"
            )


def _has_knowledge(obj):
    kp = obj.get("knowledge")
    return isinstance(kp, list) and any(isinstance(x, str) and x.strip() for x in kp)


def _check_knowledge_ids(item, child, problems, warnings):
    """A22：有 knowledge 名称时应成对给出两种字段名的 id。

    正式批次的名称和两种 ID 字段都必须对应目录；缺失即阻断。
    IDs 可以由装配器根据精确叶子路径确定性生成，不能猜测或手写。
    """
    obj = child if child is not None else item
    if not _has_knowledge(obj):
        return
    has_camel = "knowledgeIds" in obj
    has_snake = "knowledge_ids" in obj
    resolved = taxonomy.resolve_knowledge(obj["knowledge"])
    expected = [point["id"] for point in resolved["resolved"]]
    if not has_camel or not has_snake:
        problems.append(f"{_label(item, child)}：A22 知识点必须同时提供 knowledgeIds 和 knowledge_ids")
    for key in ("knowledgeIds", "knowledge_ids"):
        if key in obj and obj[key] != expected:
            problems.append(f"{_label(item, child)}：A22 {key} 与目录知识点路径不一致")


def main(argv=None):
    args = _common.arg_parser("标签体检（A3/A6/A7/A15/A22、C1-C10）").parse_args(argv)
    problems = []
    warnings = []
    try:
        batch = _common.load_batch(args.batch)
    except Exception as exc:  # 容错：读不到批次不崩，按失败处理。
        print(f"[FAIL] 无法读取批次：{exc}")
        return 1

    for item in _common.units(batch):
        if not isinstance(item, dict):
            problems.append(f"单元必须是对象：{item!r}")
            continue
        # 逐 item 完整标签校验（含子题）。
        try:
            for text in taxonomy.check_question_labels(item):
                problems.append(f"单元 {item.get('unit_id') or item.get('id') or '?'}：{text}")
        except Exception as exc:  # 容错：单个 item 异常不影响其它题。
            problems.append(f"单元 {item.get('unit_id') or item.get('id') or '?'}：校验异常 {exc}")

        _check_difficulty(item, None, problems)
        _check_knowledge_ids(item, None, problems, warnings)
        for child in (item.get("subquestions") or []):
            if not isinstance(child, dict):
                problems.append(f"单元 {item.get('unit_id') or item.get('id') or '?'}：子题必须是对象 {child!r}")
                continue
            _check_knowledge_ids(item, child, problems, warnings)

    for w in warnings:
        print(f"[WARN] {w}")
    return _common.report(problems, ok_msg=f"标签体检通过（{len(_common.units(batch))} 个单元，{len(warnings)} 条 warning）")


if __name__ == "__main__":
    raise SystemExit(main())
