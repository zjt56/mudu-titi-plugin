# -*- coding: utf-8 -*-
"""表格体检（E5 / E9 / E10 / E14）。

被 workflow/checks.py 以 `python check_tables.py --batch <batch_meta.json>` 调用。
退出码 0=通过，1=有问题；问题逐行 print 到 stdout。

通用、对无表格批次安全通过：
- 仅当 unit 带 `materialBlocks` 且其中含 `type=table` 时才检查表格。
- 无 `materialBlocks`（或没有 table 块）则不报任何问题。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _common import arg_parser, load_batch, report, units  # noqa: E402

# E10：空位的唯一合法写法是 `__N__`。
# 常见的错误写法：全角空档 `　16　`、带空格的 `__ 16 __`。
import re  # noqa: E402

_FULLWIDTH_BLANK = re.compile(r"\u3000\s*\d+\s*\u3000")      # 　16　
_SPACED_UNDERSCORE = re.compile(r"__+\s+\d+\s+__+")          # __ 16 __
_VALID_BLANK = re.compile(r"__(\d+)__")                      # __16__


def _cell_text(cell):
    """取单元格文本（单元格可能是 str、dict 或 {text/content}）。"""
    if cell is None:
        return ""
    if isinstance(cell, str):
        return cell
    if isinstance(cell, dict):
        for key in ("text", "content", "value", "html"):
            val = cell.get(key)
            if isinstance(val, str):
                return val
        return ""
    return str(cell)


def _iter_tables(unit):
    """产出 unit 的每个 type=table 块。"""
    blocks = unit.get("materialBlocks") or []
    if not isinstance(blocks, list):
        return
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "table":
            yield block


def _table_rows(block):
    for key in ("rows", "cells", "data", "grid"):
        rows = block.get(key)
        if isinstance(rows, list):
            return rows
    return []


def _cell_spans(cell):
    """返回 (rowspan, colspan)，缺省 1。"""
    if not isinstance(cell, dict):
        return 1, 1
    def _as_int(v):
        try:
            n = int(v)
        except (TypeError, ValueError):
            return 1
        return n if n >= 1 else 1
    return _as_int(cell.get("rowspan", 1)), _as_int(cell.get("colspan", 1))


def _check_rectangular(unit, block, idx, problems):
    """E6 / E14：整张表必须是矩形（每行逻辑列数一致，考虑 rowspan 与 colspan）。"""
    rows = _table_rows(block)
    if not rows:
        return
    widths = []
    active_until = {}
    for r, row in enumerate(rows):
        cells = row if isinstance(row, list) else (
            row.get("cells") if isinstance(row, dict) else [])
        if not isinstance(cells, list):
            cells = []
        occupied = {column for column, end in active_until.items() if end > r}
        cursor = 0
        for cell in cells:
            rowspan, colspan = _cell_spans(cell)
            while cursor in occupied:
                cursor += 1
            columns = set(range(cursor, cursor + colspan))
            if columns.intersection(occupied):
                problems.append(f"[W] {unit.get('unit_id')}: 第{idx + 1}张表 第{r + 1}行合并单元格重叠（提示，不阻断）")
            if r + rowspan > len(rows):
                problems.append(f"[W] {unit.get('unit_id')}: 第{idx + 1}张表 第{r + 1}行 rowspan 超出实际表格（提示，不阻断）")
            occupied.update(columns)
            for column in columns:
                active_until[column] = r + rowspan
            cursor += colspan
        widths.append(max(occupied) + 1 if occupied else 0)
    if not widths:
        return
    # 允许首行是跨整行标题（逻辑宽度可能为 1，或等于其它行宽），
    # 其余行的逻辑列数必须一致。
    body = widths[1:] if len(widths) > 1 else widths
    if len(set(body)) > 1:
        problems.append(
            f"[W] {unit.get('unit_id')}: 第{idx + 1}张表各行列数不一致 {widths}（提示，不阻断）")


def _check_cell_blanks(unit, block, idx, problems):
    """E10：单元格里的空位只允许 `__N__`。"""
    rows = _table_rows(block)
    for r, row in enumerate(rows):
        cells = row if isinstance(row, list) else (
            row.get("cells") if isinstance(row, dict) else [])
        if not isinstance(cells, list):
            continue
        for c, cell in enumerate(cells):
            text = _cell_text(cell)
            if not text:
                continue
            if _FULLWIDTH_BLANK.search(text):
                problems.append(
                    f"[W10] {unit.get('unit_id')}: 第{idx + 1}张表 第{r + 1}行第{c + 1}列 "
                    f"使用全角空档，建议写成 __N__（提示，不阻断）：{text!r}")
            if _SPACED_UNDERSCORE.search(text):
                problems.append(
                    f"[W10] {unit.get('unit_id')}: 第{idx + 1}张表 第{r + 1}行第{c + 1}列 "
                    f"空档含空格，建议写成 __N__（提示，不阻断）：{text!r}")


def _collect_blank_numbers(block):
    """收集整张表里 `__N__` 出现的子题号及次数。"""
    seen = {}
    rows = _table_rows(block)
    for row in rows:
        cells = row if isinstance(row, list) else (
            row.get("cells") if isinstance(row, dict) else [])
        if not isinstance(cells, list):
            continue
        for cell in cells:
            for m in _VALID_BLANK.finditer(_cell_text(cell)):
                n = m.group(1)
                seen[n] = seen.get(n, 0) + 1
    return seen


def _block_variant(block, unit):
    v = block.get("variant", unit.get("variant"))
    return v if isinstance(v, str) else ""


def _check_information_conversion(unit, block, idx, problems):
    """E9：信息转换表格 variant 必须 information-conversion，
    且每个子题号在表中出现且仅出现一次 `__N__`。"""
    uid = unit.get("unit_id")
    variant = _block_variant(block, unit)
    if variant != "information-conversion":
        problems.append(
            f"[W9] {uid}: 信息转换表格 variant 建议为 information-conversion，"
            f"实为 {variant!r}（提示，不阻断）")

    sub_numbers = []
    for sub in unit.get("subquestions") or []:
        if isinstance(sub, dict) and sub.get("number") is not None:
            sub_numbers.append(str(sub.get("number")).strip())
    if not sub_numbers:
        # 没有子题号时退回 numbers 字段
        sub_numbers = [str(n).strip() for n in (unit.get("numbers") or [])]

    seen = _collect_blank_numbers(block)
    for n in sub_numbers:
        cnt = seen.get(n, 0)
        if cnt == 0:
            problems.append(f"[W9] {uid}: 子题号 __{n}__ 未在信息转换表格中出现（提示，不阻断）")
        elif cnt > 1:
            problems.append(f"[W9] {uid}: 子题号 __{n}__ 在表格中出现 {cnt} 次，建议仅一次（提示，不阻断）")


def main(argv=None):
    args = arg_parser("表格体检（E5/E9/E10/E14）").parse_args(argv)
    try:
        batch = load_batch(args.batch)
    except Exception as exc:  # noqa: BLE001
        print(f"无法读取批次：{exc}")
        return 1

    problems = []
    for unit in units(batch):
        if not isinstance(unit, dict):
            continue
        tables = list(_iter_tables(unit))
        if not tables:
            continue
        is_info_conv = unit.get("question_type") == "听力信息转换"
        for idx, block in enumerate(tables):
            _check_rectangular(unit, block, idx, problems)
            _check_cell_blanks(unit, block, idx, problems)
            if is_info_conv:
                _check_information_conversion(unit, block, idx, problems)

    return report(problems, ok_msg="表格检查通过（无表格或表格合法）")


if __name__ == "__main__":
    sys.exit(main())
