#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""结构体检（A8/A9/C11/C12/C15/D3/D8/A11）。

调用：python scripts/06-upload/check_structure.py --batch <batch_meta.json>
退出码 0=通过，非0=有问题；问题逐行 print 到 stdout。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _common import arg_parser, load_batch, plain, report, units  # noqa: E402
from workflow import question_content  # noqa: E402

# 完形填空：选项挂在子题，每个子题恰好 4 个选项
CLOZE = "完形填空"
# 选词/短文/教材语篇填空：子题不应有选项
NO_SUB_OPTIONS = ("选词填空", "短文填空", "教材语篇填空")
# 阅读类：组级需 语篇体裁* + 主题*，每子题需 设题角度/阅读*
READING_GROUP = ("阅读理解", "任务型阅读", "读写综合")
# 完形/语法选择：组级需 语篇体裁* + 主题*
CLOZE_GRAMMAR_GROUP = ("完形填空", "语法选择")
# D3/D8：完形子题允许两种合法表示：
# 1) 题号已在共享材料中时只写非空作答空位（如 "______"）；
# 2) 旧版/特殊版式保留题号前缀（如 "31." 或 "31. ______"）。
# 不应因采用当前 D3 的空位表示而误报格式错误。
NUMBER_PREFIX = re.compile(r"^\s*(\d+)\s*[\.．]")
BLANK_ONLY = re.compile(r"^\s*(?:_{2,}|[＿－—\-\s]{2,})\s*$")


def _has_prefix(knowledge, prefix):
    """knowledge 是否包含以 prefix 开头的维度标签。"""
    for k in knowledge or []:
        if isinstance(k, str) and k.startswith(prefix):
            return True
    return False


def _check_unit(u, verified=None, table_verified=None):
    problems = []
    uid = u.get("unit_id") or u.get("id") or "?"
    qt = (u.get("question_type") or "").strip()
    kind = (u.get("unit_kind") or "").strip()
    material = plain(u.get("material") or "").strip()
    subs = u.get("subquestions") or []
    opts = u.get("options") or []

    from workflow.phonetic_structure import shape
    phonetic_material, phonetic_alias = shape(u, verified)

    # 「每小题目包含内容、无共享材料」的合法组题形态：四线三格(空白作答网格)、看图题等。
    # 判据统一在 workflow.question_content.self_contained_group，与发布闸门 publish_check
    # 共用同一口径。原卷视觉已核实（05 Starter Unit 2 四线三格：三条诗句下均为空白三格），
    # 属硬规则 9 的「空白作答网格」合法形态，空 material 不是丢内容。
    self_contained_group = question_content.self_contained_group(u)

    # A8：group 必须 material 非空 且 subquestions 非空
    # 例外：听力组题的材料是音频，没有文字 material；跳过听力时允许为空，不伪造文本。
    #       语音组题（phonetic_material）与每小题目包含内容的合法形态(self_contained_group)
    #       同样允许空 material，但都必须有子题。
    if kind == "group":
        if (not material and not str(qt).startswith("听力")
                and not phonetic_material and not self_contained_group):
            problems.append(f"[A8] {uid} ({qt}): 题组 unit_kind=group 但 material 为空")
        if not subs:
            problems.append(f"[A8] {uid} ({qt}): 题组 unit_kind=group 但 subquestions 为空")

    # Current native-table response blanks have their stem in the retained table.
    table_blanks = question_content.table_blank_stem_numbers(u, table_verified)
    # A9：group 每个子题 stem 非空、answer 非空
    if kind == "group":
        for c in subs:
            n = c.get("number")
            if not plain(c.get("stem") or "").strip() and not phonetic_alias and n not in table_blanks:
                problems.append(f"[A9] {uid} 子题 {n}: stem 为空")
            if not str(c.get("answer") or "").strip():
                problems.append(f"[A9] {uid} 子题 {n}: answer 为空")

    # A11：standalone 不应有 subquestions
    if kind == "standalone" and subs:
        problems.append(f"[A11] {uid} ({qt}): standalone 单元不应有 subquestions（现有 {len(subs)} 个）")

    # C11：完形填空单元 options 应为空，每个子题恰好 4 个选项
    if qt == CLOZE:
        if opts:
            problems.append(f"[C11] {uid}: 完形填空单元 options 应为空（选项在子题），现有 {len(opts)} 个")
        for c in subs:
            n = c.get("number")
            cn = len(c.get("options") or [])
            if cn != 4:
                problems.append(f"[C11] {uid} 完形子题 {n}: 选项应为恰好 4 个，现有 {cn} 个")

    # C12：选词/短文/教材语篇填空 子题不应有 options
    if qt in NO_SUB_OPTIONS:
        for c in subs:
            n = c.get("number")
            if c.get("options"):
                problems.append(f"[C12] {uid} ({qt}) 子题 {n}: 不应有 options，现有 {len(c.get('options') or [])} 个")

    # D3/D8：共享材料已带题号时，子题 stem 写非空空位即可；保留题号前缀的
    # 旧版格式也兼容，但若存在前缀必须与子题号一致。
    if qt == CLOZE:
        for c in subs:
            n = c.get("number")
            stem = c.get("stem")
            if stem is None or not str(stem).strip():
                problems.append(f"[D3] {uid} 完形子题 {n}: stem 为空串，应为 '______' 或 '31.'")
                continue
            s = plain(str(stem)).strip()
            m = NUMBER_PREFIX.match(s)
            if m:
                if n is not None and str(n).strip() and m.group(1) != str(n).strip():
                    problems.append(f"[D8] {uid} 完形子题 {n}: stem 题号「{m.group(1)}.」与子题号 {n} 不一致")
            elif not BLANK_ONLY.match(s):
                problems.append(f"[D3] {uid} 完形子题 {n}: stem「{s[:20]}」不是合法空位或题号格式（应为 '______' 或 '31.'）")

    # C15：组级 / 子题必带维度
    if kind == "group" and qt in READING_GROUP:
        if not _has_prefix(u.get("knowledge"), "语篇体裁"):
            problems.append(f"[C15] {uid} ({qt}): 组级 knowledge 缺「语篇体裁*」")
        if not _has_prefix(u.get("knowledge"), "主题"):
            problems.append(f"[C15] {uid} ({qt}): 组级 knowledge 缺「主题*」")
        for c in subs:
            n = c.get("number")
            # 整篇类题型只做组级标签；子题留空是标准形态，显式给标签才要求阅读角度。
            if c.get("knowledge") and not _has_prefix(c.get("knowledge"), "设题角度/阅读"):
                problems.append(f"[C15] {uid} 子题 {n}: knowledge 缺「设题角度/阅读*」")
    elif kind == "group" and qt in CLOZE_GRAMMAR_GROUP:
        if not _has_prefix(u.get("knowledge"), "语篇体裁"):
            problems.append(f"[C15] {uid} ({qt}): 组级 knowledge 缺「语篇体裁*」")
        if not _has_prefix(u.get("knowledge"), "主题"):
            problems.append(f"[C15] {uid} ({qt}): 组级 knowledge 缺「主题*」")

    return problems


def main(argv=None):
    parser = arg_parser("结构体检 A8/A9/C11/C12/C15/D3/D8/A11")
    parser.add_argument("--source-batch-dir", help="当前候选的显式来源批次目录（不从候选字段读取）")
    args = parser.parse_args(argv)
    try:
        batch = load_batch(args.batch)
    except Exception as exc:  # 容错：读不到批次也要给出可读信息，不崩
        print(f"[ERROR] 无法读取批次文件 {args.batch}: {exc}")
        return 2

    from workflow.phonetic_structure import verified_groups
    source_path = Path(args.source_batch_dir) / "06_batch/batch.json" if args.source_batch_dir else args.batch
    verified = verified_groups(source_path)
    from workflow.table_structure import verified_groups as verified_tables
    table_verified = verified_tables(source_path)
    problems = []
    for u in units(batch):
        if not isinstance(u, dict):
            problems.append(f"[格式] 非对象单元被忽略：{type(u).__name__}")
            continue
        try:
            problems.extend(_check_unit(u, verified, table_verified))
        except Exception as exc:  # 单个单元异常不中断整批
            uid = u.get("unit_id") or u.get("id") or "?"
            problems.append(f"[ERROR] 单元 {uid} 体检异常：{exc}")

    return report(problems, ok_msg=f"结构体检通过（{len(units(batch))} 个单元）")


if __name__ == "__main__":
    sys.exit(main())
