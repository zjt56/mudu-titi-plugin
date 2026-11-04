#!/usr/bin/env python3
"""阅读类专项体检（C4/C15/C22-C27）。

规则针对“题组”（unit_kind=group 或有 subquestions）：

阅读类（阅读理解 / 任务型阅读 / 读写综合）：
- C4/C15：每个子题 knowledge 必须含 `设题角度/阅读/` 前缀下的叶子。
- C24：子题不得挂 `语篇体裁/` 前缀（体裁只挂组级）。
- C27：阅读类与完形/语法选择单元必须有文章大意 articleSummary（F3：原卷有【文章大意】就录原话，
  没有就自己生成 2–3 句中文概括并在末尾标「（AI生成，待校对）」）。
- C22：组级 knowledge 若含 `设题角度`（角度汇总），可接受；这里汇总组内
  实际出现的角度，若组级角度集合与子题角度集合不一致则提示（只对可读角度
  做并集比对；组级不强制含角度，缺角度由 check_tags 的组级维度规则负责）。

完形填空 / 语法选择（C25）：
- 组级 knowledge 不得含 `词汇/` 或 `语法/`（组级只体裁 + 主题）。
- 子题才挂词汇/语法（本规则不要求子题必带，只禁止组级挂）。

用法：audit_reading.py --batch <batch_meta.json>
退出码：0 = 通过；1 = 有问题。问题逐行打印到 stdout。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _common  # noqa: E402

_READING_TYPES = {"阅读理解", "任务型阅读", "读写综合"}
_CLOZE_TYPES = {"完形填空", "语法选择"}

_READING_ANGLE_PREFIX = "设题角度/阅读"
_GENRE_PREFIX = "语篇体裁"
_AI_MARK = "（AI生成，待校对）"


def _knowledge(obj):
    kp = obj.get("knowledge")
    if not isinstance(kp, list):
        return []
    return [x.strip() for x in kp if isinstance(x, str) and x.strip()]


def _under(path, prefix):
    return path == prefix or path.startswith(prefix + "/")


def _is_group(item):
    return bool(item.get("subquestions")) or item.get("unit_kind") == "group"


def _check_reading(uid, item, problems):
    """C4/C15/C22/C24/C27：阅读类题组。"""
    unit_paths = _knowledge(item)
    angle_set = set()
    for p in unit_paths:
        if _under(p, _READING_ANGLE_PREFIX):
            angle_set.add(p)

    _check_summary(uid, "阅读类", item, problems)

    children = [c for c in (item.get("subquestions") or []) if isinstance(c, dict)]
    child_angles = set()
    for child in children:
        num = child.get("number")
        paths = _knowledge(child)
        # C4/C15：整篇类题型子题留空是标准形态；显式给标签才要求阅读角度。
        child_reading = [p for p in paths if _under(p, _READING_ANGLE_PREFIX) and p != _READING_ANGLE_PREFIX]
        if paths and not child_reading:
            problems.append(f"单元 {uid} 子题 {num}：C15 阅读子题必须包含 设题角度/阅读/* 叶子（实际：{paths}）")
        for p in child_reading:
            child_angles.add(p)
        # C24：子题不挂语篇体裁。
        genre = [p for p in paths if _under(p, _GENRE_PREFIX)]
        if genre:
            problems.append(f"单元 {uid} 子题 {num}：C24 子题不得挂 语篇体裁/*（实际：{genre}）")

    # C22：组级若含角度，应等于组内实际出现角度集合（按出现次数排序等由装配负责，
    # 这里只校验集合一致，避免组级角度漏写/多写导致筛选错）。
    if angle_set and child_angles and angle_set != child_angles:
        missing = sorted(child_angles - angle_set)
        extra = sorted(angle_set - child_angles)
        detail = []
        if missing:
            detail.append(f"缺 {missing}")
        if extra:
            detail.append(f"多 {extra}")
        problems.append(f"单元 {uid}：C22 组级设题角度与子题角度不一致（{'；'.join(detail)}）")


def _check_summary(uid, label, item, problems):
    """C27：阅读类与完形/语法选择必须有文章大意（F3：原卷有就录原话，没有就生成并标注）。"""
    summary = item.get("articleSummary", item.get("article_summary", ""))
    if not (isinstance(summary, str) and summary.strip()):
        problems.append(f"单元 {uid}：C27 {label}缺少文章大意（F3：原卷没有就自己生成 2–3 句并标「{_AI_MARK}」）")


def _check_cloze(uid, item, problems):
    """C25/C27：完形/语法选择组级不挂词汇/语法，且必须有大意。"""
    _check_summary(uid, "完形/语法选择", item, problems)
    unit_paths = _knowledge(item)
    banned = [p for p in unit_paths if _under(p, "词汇") or _under(p, "语法")]
    if banned:
        problems.append(f"单元 {uid}：C25 完形/语法选择组级不挂 词汇/语法（实际：{banned}），这些维度请挂到子题")


def _check_unit(item, problems):
    uid = item.get("unit_id") or item.get("id") or "?"
    qtype = item.get("question_type") or item.get("type")
    if not isinstance(qtype, str):
        return
    qtype = qtype.strip()
    if not _is_group(item):
        return
    if qtype in _READING_TYPES:
        _check_reading(uid, item, problems)
    elif qtype in _CLOZE_TYPES:
        _check_cloze(uid, item, problems)


def main(argv=None):
    args = _common.arg_parser("阅读类专项体检（C4/C15/C22-C27）").parse_args(argv)
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

    return _common.report(problems, ok_msg=f"阅读类专项体检通过（{len(_common.units(batch))} 个单元）")


if __name__ == "__main__":
    raise SystemExit(main())
