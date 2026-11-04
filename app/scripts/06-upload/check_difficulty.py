#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""难度一致性体检（D12）：difficulty 档位与 difficultyValue 分值必须同档。

规则来源：references/difficulty-category.json 与上传接口的 DIFFICULTY_CONFLICT。
0.8 及以上=容易，0.5 及以上=适中，其余=困难；difficultyValue 为空表示未评分，
不与标签冲突。只报告，不改写数据。

调用：python scripts/06-upload/check_difficulty.py --batch <batch_meta.json>
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _common import arg_parser, load_batch, report, units  # noqa: E402

LEVELS = {"容易", "适中", "困难"}


def bucket(value: float) -> str:
    return "容易" if value >= 0.8 else ("适中" if value >= 0.5 else "困难")


def problems(batch: dict) -> list[str]:
    found = []
    for unit in units(batch):
        filters = unit.get("filterMetadata") if isinstance(unit.get("filterMetadata"), dict) else {}
        level = filters.get("difficulty", unit.get("difficulty"))
        value = filters.get("difficultyValue")
        uid = unit.get("unit_id") or unit.get("id") or "?"
        if level is not None and level not in LEVELS:
            found.append(f"{uid}: difficulty={level!r} 不是 容易/适中/困难")
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            found.append(f"{uid}: difficultyValue={value!r} 必须是 0–1 数值或 null")
            continue
        if not 0 <= float(value) <= 1:
            found.append(f"{uid}: difficultyValue={value!r} 超出 0–1")
            continue
        if level in LEVELS and bucket(float(value)) != level:
            found.append(f"{uid}: difficulty={level} 与 difficultyValue={value}（{bucket(float(value))}档）冲突")
    return found


def main(argv=None) -> int:
    args = arg_parser("难度一致性体检 D12").parse_args(argv)
    try:
        batch = load_batch(args.batch)
    except Exception as exc:  # noqa: BLE001
        print(f"无法读取批次：{exc}")
        return 2
    return report(problems(batch), f"难度一致性通过（{len(units(batch))} 个单元）")


if __name__ == "__main__":
    raise SystemExit(main())
