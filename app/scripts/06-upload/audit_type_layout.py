#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题型版面体检（E20-选项条数 / D7 子题不重复编号 / D10 组题粒度 / C11 完形选项 / 空壳单元）。

调用：python scripts/06-upload/audit_type_layout.py --batch <batch_meta.json>
退出码 0=通过，非0=有问题；问题逐行 print 到 stdout。

「一错就整屏变形」的几个点都在这里兜：补全对话正文缺题号→底部重复列题号；
完形子题没写题号或选项数不对→题号与选项分家；填空类子题重复整句→重复。
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
# 原卷选项基准：听力短对话 3；单项填空/阅读理解/完形 4；补全对话 7；听力长/短文 3。
# 只有这些题型才有「必须恰好 N 个选项」的硬基准；其余题型（填空/翻译/写作/语音……）
# 不在此表，不做条数要求（C12：选词/短文/教材语篇填空的子题没有选项）。
OPTION_RULES = {
    "听力短对话理解": (3, "unit"),
    "单项填空": (4, "unit"),
    "阅读理解": (4, "sub_or_unit"),
    "补全对话": (7, "unit"),
    "完形填空": (4, "sub"),
    "听力长对话理解": (3, "sub_or_unit"),
    "听力短文理解": (3, "sub_or_unit"),
}

# 选项必须 A→… 连续且不重复的题型（C11/E20 批次完整性的一部分）。
CONTIGUOUS_OPTION_TYPES = tuple(OPTION_RULES)

# 内容最常「题号单独起一行」/「重复整句」的填空题：子题 stem 必须是空壳
# （`______` 或纯数字题号），不能把材料里已有的整句再抄一遍。
SHELL_SUB_TYPES = ("补全对话", "听力信息转换", "短文填空", "选词填空", "教材语篇填空")

NUMBER_PREFIX = re.compile(r"^\s*(\d+)\s*[\.．]")
# 选项字母前缀 A. / A．/ A、/ A:
_OPTION_LETTER = re.compile(r"^\s*([A-Ha-h])\s*[\.．、:：]")
# 素材里的空位/题号写法：__N__、　N　、（N）、(N)、N.、N.N
_MATERIAL_NUMBER = re.compile(r"(?:__(\d+)__|[\u3000\s（(](\d+)[\u3000\s）)]|(?<!\d)(\d+)\s*[\.．])")
_SHELL_STEM = re.compile(r"^[\s_]*\d*[\s_.]*$")


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


def _material_numbers(text):
    """材料里出现过的全部题号（int 集合），认 __N__ / 　N　 / （N） / N. 等写法。"""
    found = set()
    for m in _MATERIAL_NUMBER.finditer(plain(text or "")):
        for group in m.groups():
            if group:
                try:
                    found.add(int(group))
                except ValueError:
                    pass
                break
    return found


def _material_has_number(text, n):
    """材料中是否出现该题号（数字作为独立 token，允许 __N__、　N　、N. 等）。"""
    if not text or n is None:
        return False
    try:
        return int(n) in _material_numbers(text)
    except (TypeError, ValueError):
        return re.search(rf"(?<!\d){n}(?!\d)", text) is not None


def _option_letters(options):
    """从选项列表里抽出字母前缀（A/B/C…）；没有前缀的返回 None 表示不判连续性。"""
    letters = []
    for opt in options or []:
        m = _OPTION_LETTER.match(plain(str(opt)))
        if not m:
            return None
        letters.append(m.group(1).upper())
    return letters


def _check_option_continuity(problems, uid, qt, where, options, label):
    """C11/E20：选项必须从 A 连续到最后一个字母，不重复、不跳号。"""
    letters = _option_letters(options)
    if not letters:
        return  # 无字母前缀（如纯图选项）不按此判，避免误报
    expected = [chr(ord("A") + i) for i in range(len(letters))]
    if letters != expected:
        problems.append(
            f"[E20] {uid} ({qt}) {label}: 选项字母应 A→{'ABCDEFGH'[len(letters) - 1]} 连续，"
            f"实际为 {'/'.join(letters)}")


def _check_shell_subquestions(problems, uid, qt, kind, mat_numbers, subs):
    """SHELL_SUB：组题材料已带齐题号时，子题 stem 只能空壳，不得重复整句或已含的题号。"""
    if not mat_numbers:
        return
    for c in subs:
        stem = plain(c.get("stem") or "").strip()
        if not stem:
            continue  # 空串由 A9/D3 负责
        if _SHELL_STEM.match(stem):
            continue  # 纯 ______ 或纯题号允许
        # 非空壳：要么重复了材料里已有的题号，要么把整句抄了下来
        m = NUMBER_PREFIX.match(stem)
        if m and int(m.group(1)) in mat_numbers:
            problems.append(
                f"[D7] {uid} ({qt}) 子题 {c.get('number')}: 材料已含题号，子题 stem 不应重复题号「{m.group(0).strip()}」")
        elif len(stem) > 12 and not stem.replace("_", "").strip() == "":
            problems.append(
                f"[D7] {uid} ({qt}) 子题 {c.get('number')}: 材料已带齐题号，子题 stem 应只写空壳「______」，"
                f"不应重复整句「{stem[:20]}…」")


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


def _check_unit(u, *, content_only=False):
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

    # E20 选项条数 + C11/E20 选项字母连续
    if qt in OPTION_RULES:
        want, where = OPTION_RULES[qt]
        _check_option_count(problems, uid, qt, kind, u, subs, want, where)
        if where == "unit":
            _check_option_continuity(problems, uid, qt, "单元选项", u.get("options") or [], "单元选项")
        else:
            has_sub_opts = any(c.get("options") for c in subs)
            if has_sub_opts:
                for c in subs:
                    _check_option_continuity(
                        problems, uid, qt, "子题选项", c.get("options") or [], f"子题 {c.get('number')} 选项")
            else:
                _check_option_continuity(problems, uid, qt, "单元选项", u.get("options") or [], "单元选项")

    # C12：填空类子题不该有选项（选词/短文/教材语篇填空/补全对话子题）
    if qt in ("短文填空", "选词填空", "教材语篇填空") and subs:
        for c in subs:
            if c.get("options"):
                problems.append(
                    f"[C12] {uid} ({qt}) 子题 {c.get('number')}: 该题型子题不应带选项（C12）")

    # C11：完形填空单元级 options 应留空，选项挂各子题
    if qt == "完形填空" and kind == "group" and u.get("options"):
        problems.append(f"[C11] {uid} ({qt}): 完形单元级 options 应留空，选项挂在各子题")

    # D7 / SHELL_SUB：材料已带齐题号时，子题 stem 只能空壳，不重复整句/题号
    if not content_only and qt in SHELL_SUB_TYPES:
        mat_numbers = _material_numbers(u.get("material") or "")
        nums = set(_sub_numbers(u))
        # 只有当材料里确实带齐了每个子题号才按「材料已编号」判定
        if nums and nums.issubset(mat_numbers):
            _check_shell_subquestions(problems, uid, qt, kind, mat_numbers, subs)

    return problems


def main(argv=None):
    parser = arg_parser("题型版面体检 E20-选项条数·选项连续/D7-子题空壳·D10-组题粒度/C11-完形选项/C12-填空无选项")
    parser.add_argument("--content-only", action="store_true", help="前期只查内容结构，跳过重复显示检查")
    args = parser.parse_args(argv)
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
            problems.extend(_check_unit(u, content_only=args.content_only))
        except Exception as exc:
            uid = u.get("unit_id") or u.get("id") or "?"
            problems.append(f"[ERROR] 单元 {uid} 体检异常：{exc}")

    return report(problems, ok_msg=f"题型版面体检通过（{len(units(batch))} 个单元）")


if __name__ == "__main__":
    sys.exit(main())
