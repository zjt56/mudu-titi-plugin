"""本地体检脚本共享内核：读批次、复用工作流已有的规则实现、统一退出码。

约定（与 workflow/checks.py 一致）：
- 命令行：<script> --batch <batch_meta.json> [--answers <path>] [--source <path>]
- 退出码 0 = 通过；1 = 有阻断问题；输出问题到 stdout（一行一条）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_batch(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def units(batch):
    return batch.get("items") or []


def iter_questions(batch):
    """逐个产出 (unit, child_or_None)。"""
    for u in units(batch):
        yield u, None
        for c in (u.get("subquestions") or []):
            yield u, c


def arg_parser(desc):
    ap = argparse.ArgumentParser(description=desc)
    ap.add_argument("--batch", required=True, type=Path)
    ap.add_argument("--answers", type=Path, default=None)
    ap.add_argument("--source", type=Path, default=None)
    return ap


# 小问题标记：以 [W 开头的行只是可修可不修的格式/标点/画线类提示，
# “题目、题干、题号对不上”这类实质错误才用 [E 开头并阻断。
SOFT_MARKERS = ("[W", "[INFO]", "[提示]", "[PREF]")


def _is_soft(line):
    text = str(line).strip()
    return text.startswith(SOFT_MARKERS)


def report(problems, ok_msg="通过", *, soft_default=False):
    """打印检查结果并按退出码表示是否阻断。

    soft_default=True：本检查只报告格式类问题，任何 findings 都只提示、不阻断。
    否则以 [W 开头的软提示不阻断，其余（[E / [B / [格式] / [ERROR）仍阻断。
    """
    for p in problems:
        print(p)
    if soft_default:
        soft, blocking = list(problems), []
    else:
        soft = [p for p in problems if _is_soft(p)]
        blocking = [p for p in problems if not _is_soft(p)]
    if soft:
        print(f"[WARN] {len(soft)} 条格式提示（不阻断流程）", file=sys.stderr)
    if blocking:
        print(f"[FAIL] {len(blocking)} 条问题", file=sys.stderr)
        return 1
    print(f"[OK] {ok_msg}" + (f"（{len(soft)} 条格式提示不阻断）" if soft else ""))
    return 0


def plain(text):
    """去掉 HTML 标签（<u></u>、<b> 等）后的纯文本。"""
    return re.sub(r"</?(?:u|b|i|sup|sub)>", "", text or "")


def effective_options(q, unit, is_cloze):
    """子题/单元的有效选项：完形子题选项在子题上。"""
    opts = q.get("options") if isinstance(q, dict) else None
    if isinstance(opts, list):
        return opts
    return []
