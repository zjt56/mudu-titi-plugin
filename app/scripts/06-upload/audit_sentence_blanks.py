# -*- coding: utf-8 -*-
"""完成句子一词一空体检（C14 / D11）。

被 workflow/checks.py 以 `python audit_sentence_blanks.py --batch <batch_meta.json>` 调用。
退出码 0=通过，1=有问题；问题逐行 print 到 stdout。

规则：question_type=完成句子 的题，答案词数（按空格分词）应等于题干
`______`（连续下划线块）的个数。不等则报错。无完成句子题则通过。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _common import arg_parser, iter_questions, load_batch, plain, report  # noqa: E402

# 连续下划线块（至少 2 个下划线视为空档）。完成句子的空档标准是 6 个下划线，
# 这里接受 2+ 以容忍录入误差，但仍按“块”计数。
_BLANK = re.compile(r"_{2,}")


def _blank_count(text):
    return len(_BLANK.findall(plain(text)))


def _word_count(answer):
    text = plain(answer or "").strip()
    if not text:
        return 0
    return len([w for w in text.split() if w.strip()])


def main(argv=None):
    args = arg_parser("完成句子一词一空体检（C14/D11）").parse_args(argv)
    try:
        batch = load_batch(args.batch)
    except Exception as exc:  # noqa: BLE001
        print(f"无法读取批次：{exc}")
        return 1

    problems = []
    for unit, child in iter_questions(batch):
        if not isinstance(unit, dict):
            continue
        if unit.get("question_type") != "完成句子":
            continue
        q = child if child is not None else unit
        label = unit.get("unit_id")
        if child is not None and child.get("number") is not None:
            label = f"{label} 第{child.get('number')}题"
        stem = q.get("stem") or ""
        answer = q.get("answer") or ""
        blanks = _blank_count(stem)
        words = _word_count(answer)
        if blanks == 0:
            problems.append(f"{label}: 完成句子题干缺少 ______ 空档（D11）")
            continue
        if blanks != words:
            problems.append(
                f"{label}: 完成句子空档数 {blanks} ≠ 答案词数 {words}"
                f"（answer={answer!r}，C14 一词一空）")

    return report(problems, ok_msg="完成句子空档检查通过（无完成句子题或均匹配）", soft_default=True)


if __name__ == "__main__":
    sys.exit(main())
