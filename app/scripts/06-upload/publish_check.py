# -*- coding: utf-8 -*-
"""发布前闸门（A8 / A9 / A3 / A6）。

被 workflow/checks.py 以 `python publish_check.py --batch <batch_meta.json>` 调用。
退出码 0=通过，1=有问题；汇总所有问题，问题逐行 print 到 stdout。

检查项：
- A6：所有 unit 的 question_type 必须是 27 主类之一。
- A3：difficulty ∈ {容易, 适中, 困难}（取 filterMetadata.difficulty）。
- A8：group 必须有 material + subquestions。
- A9：group 每个子题必须有非空 stem + answer。
- standalone 不应有 subquestions。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _common import arg_parser, load_batch, report, units  # noqa: E402

try:
    from workbench import taxonomy  # type: ignore

    MAIN_TYPES = set(taxonomy.types())
    DIFFICULTIES = set(taxonomy.difficulties())
except Exception:  # noqa: BLE001 - 容错：退化为内置常量
    MAIN_TYPES = {
        "听力短对话理解", "听力长对话理解", "听力短文理解", "听力信息转换",
        "听力判断", "听力排序", "听力问答", "听力关键词语选择", "听力其他",
        "语音题", "单项填空", "完形填空", "语法选择", "阅读理解",
        "任务型阅读", "教材语篇填空", "短文填空", "选词填空", "补全对话",
        "单词拼写", "完成句子", "情景运用", "翻译", "改错", "读写综合",
        "书面表达", "其他",
    }
    DIFFICULTIES = {"容易", "适中", "困难"}


def _is_blank(v):
    return not (isinstance(v, str) and v.strip())


def _difficulty(unit):
    fm = unit.get("filterMetadata")
    if isinstance(fm, dict):
        return fm.get("difficulty")
    return unit.get("difficulty")


def main(argv=None):
    args = arg_parser("发布前闸门（A8/A9/A3/A6）").parse_args(argv)
    try:
        batch = load_batch(args.batch)
    except Exception as exc:  # noqa: BLE001
        print(f"无法读取批次：{exc}")
        return 1

    from workflow.phonetic_structure import verified_groups, shape
    verified = verified_groups(args.batch)
    from workflow.table_structure import verified_groups as verified_tables
    from workflow.question_content import table_blank_stem_numbers
    table_verified = verified_tables(args.batch)
    problems = []
    for unit in units(batch):
        if not isinstance(unit, dict):
            problems.append(f"存在非对象的 unit 条目，已跳过：{unit!r}")
            continue
        uid = unit.get("unit_id") or unit.get("id") or "<无 unit_id>"
        qt = unit.get("question_type")
        kind = unit.get("unit_kind")

        # A6：主类
        if _is_blank(qt):
            problems.append(f"{uid}: question_type 为空（A6）")
        elif qt not in MAIN_TYPES:
            problems.append(f"{uid}: question_type「{qt}」不在 27 主类中（A6）")

        # A3：难度（书面表达不强制；作文难有客观特征，缺失不阻断）
        diff = _difficulty(unit)
        if diff is None:
            if qt != '书面表达':
                problems.append(f"{uid}: 缺少 difficulty（A3）")
        elif diff not in DIFFICULTIES:
            problems.append(f"{uid}: difficulty「{diff}」不合法，须为 容易/适中/困难（A3）")

        subs = unit.get("subquestions")
        has_subs = isinstance(subs, list) and len(subs) > 0

        if kind == "group":
            phonetic_material, phonetic_alias = shape(unit, verified)
            table_blanks = table_blank_stem_numbers(unit, table_verified)
            # A8：题组必须有 material 与 subquestions
            # 听力组题的材料是音频，跳过听力时允许 material 为空（不伪造文本）。
            # 「每小题目包含内容、无共享材料」的合法形态（四线三格空白网格/看图题）不要求
            # material；判据与结构体检共用 question_content.self_contained_group 同一口径。
            from workflow.question_content import self_contained_group
            if (_is_blank(unit.get("material")) and not str(qt or "").startswith("听力")
                    and not phonetic_material and not self_contained_group(unit)):
                problems.append(f"{uid}: 题组缺少 material（A8）")
            if not has_subs:
                problems.append(f"{uid}: 题组缺少 subquestions（A8）")
            # A9：每个子题 stem + answer 非空
            for sub in (subs or []):
                if not isinstance(sub, dict):
                    problems.append(f"{uid}: 存在非对象子题（A9）")
                    continue
                num = sub.get("number")
                if _is_blank(sub.get("stem")) and not phonetic_alias and num not in table_blanks:
                    problems.append(f"{uid} 第{num}题: 子题 stem 为空（A9）")
                if _is_blank(sub.get("answer")):
                    problems.append(f"{uid} 第{num}题: 子题 answer 为空（A9）")
        elif kind == "standalone":
            # standalone 无 subquestions
            if has_subs:
                problems.append(f"{uid}: standalone 单元不应有 subquestions（A8）")
        else:
            # 未标注 unit_kind：按是否带 subquestions 兜底
            if not kind:
                problems.append(f"{uid}: 缺少 unit_kind（应按 group/standalone 标注）")

    return report(problems, ok_msg="发布前闸门通过（主类/难度/题组结构均合规）")


if __name__ == "__main__":
    sys.exit(main())
