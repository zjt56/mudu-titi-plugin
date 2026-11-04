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


def report(problems, ok_msg="通过"):
    for p in problems:
        print(p)
    if problems:
        print(f"[FAIL] {len(problems)} 条问题", file=sys.stderr)
        return 1
    print(f"[OK] {ok_msg}")
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
