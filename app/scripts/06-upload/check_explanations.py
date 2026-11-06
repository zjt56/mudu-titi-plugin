# -*- coding: utf-8 -*-
"""解析 / 文章大意来源体检：原卷有就逐字录入，没有就填「无」，不再使用 AI 生成。

    python check_explanations.py --batch 06_batch/b.json --source 01_source/正文.txt \
        --source 01_source_ans/正文.txt
    python check_explanations.py --batch b.json --source-dir 01_source --source-dir 01_source_ans

被 workflow/checks.py 以 `python check_explanations.py --batch <batch_meta.json> [--source <path>]`
调用；--source 可重复，也可用 --source-dir 给目录。

编辑口径（用户 2026-11-06 规则）
--------------------------------
解析和文章大意都是**可选字段**：原卷有就逐字提取，没有就留空或填「无」。
不生成、不核对 AI 标记、不因缺失或不可溯源而阻断；本脚本只做统计与提示。
退出码 0=可继续（除非批次 JSON 无法读取）。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _common import load_batch, units  # noqa: E402

AI_MARK = "（AI生成，待校对）"
LISTENING_NOTE = "答案由AI生成" + AI_MARK        # 历史产物兼容，不再新产生
# 用户规则：缺失统一填「无」；其它写法兼容旧批次，均视为明确缺失占位。
ABSENT = "无"
PLACEHOLDER_TEXTS = frozenset(("无", "无解析", "无答案", "待补充", "暂无", "暂无解析", "暂无文章大意", "原卷无解析", "原卷无文章大意"))


def is_placeholder(text):
    return str(text or "").strip() in PLACEHOLDER_TEXTS
DROP = re.compile(r"[\s\u3000!-/:-@\[-`{-~，。、；：？！…—－·“”‘’（）《》【】〈〉「」『』]+")
MIN_LEN = 12
LISTENING_PREFIX = "听力"
TRANSCRIPT = ("听力原文", "听力材料", "录音原文", "录音材料")


def norm(text):
    return DROP.sub("", text or "")


def iter_explanations(item):
    yield "单元", (item.get("explanation") or "").strip()
    for s in item.get("subquestions") or []:
        if isinstance(s, dict):
            yield f"子题{s.get('number')}", (s.get("explanation") or "").strip()


def article_summary(item):
    return str(item.get("articleSummary") or item.get("article_summary") or "").strip()


def load_sources(paths):
    chunks = []
    for p in paths:
        if os.path.isdir(p):
            for name in sorted(os.listdir(p)):
                if name.endswith(".txt"):
                    chunks.append(open(os.path.join(p, name), encoding="utf-8",
                                       errors="replace").read())
        elif os.path.exists(p):
            chunks.append(open(p, encoding="utf-8", errors="replace").read())
    return norm("\n".join(chunks))


def audit(items, source):
    """只统计，不判违规：解析/大意是可选字段，缺失、不可溯源、AI 旧标记都不阻断。"""
    stat = {"source": 0, "ai": 0, "transcript": 0, "absent": 0, "unverified": 0}
    bad = []
    for it in items:
        uid = it.get("unit_id") or it.get("id") or "<无 unit_id>"
        for _where, text in iter_explanations(it):
            if not text:
                stat["absent"] += 1
                continue
            if is_placeholder(text):
                stat["absent"] += 1
                continue
            if text.startswith(TRANSCRIPT):
                stat["transcript"] += 1
                continue
            if AI_MARK in text:
                # 历史 AI 内容不采用，也不再校验标记格式。
                stat["ai"] += 1
                continue
            if norm(text) and norm(text) in source:
                stat["source"] += 1
            else:
                stat["unverified"] += 1
        summary = article_summary(it)
        if not summary or is_placeholder(summary):
            if summary:
                stat["absent"] += 1
        elif AI_MARK in summary:
            stat["ai"] += 1
        elif norm(summary) in source:
            stat["source"] += 1
        else:
            stat["unverified"] += 1
    return stat, bad


def _parser():
    # 自行构建，保证与 workflow/checks.py 的 `--batch ... [--source ...]` 调用兼容：
    # --source 可重复（原卷 + 答案册），另支持 --source-dir 目录与 --allow-extra 放行。
    import argparse
    ap = argparse.ArgumentParser(description="解析 / 文章大意来源体检（F1/F2/F3/F5/F6/F7）")
    ap.add_argument("--batch", required=True, type=Path)
    ap.add_argument("--answers", type=Path, default=None)
    ap.add_argument("--source", action="append", default=[], help="原卷/答案册文本文件或目录，可多次给")
    ap.add_argument("--source-dir", action="append", default=[], help="同上，兼容旧调用")
    ap.add_argument("--allow-extra", action="store_true",
                    help="放行既不可溯源也无标记的解析（仍会逐条列出，须在交接单写明）")
    return ap


def main(argv=None):
    ap = _parser()
    args = ap.parse_args(argv)
    try:
        batch = load_batch(args.batch)
    except Exception as exc:  # noqa: BLE001
        print(f"无法读取批次：{exc}")
        return 1
    items = [it for it in units(batch) if isinstance(it, dict)]

    paths = list(args.source) + list(args.source_dir)
    source = load_sources(paths) if paths else ""
    stat, bad = audit(items, source)
    print(f"检查 {len(items)} 个单元 | 可溯源 {stat['source']} 条 | 历史 AI 内容 {stat['ai']} 条 | "
          f"缺失/占位 {stat['absent']} 条 | 未溯源保留 {stat['unverified']} 条 | "
          f"听力稿 {stat['transcript']} 条 | 违规 {len(bad)} 条")
    for line in bad[:20]:
        print(f"  ✗ {line}")
    if len(bad) > 20:
        print(f"  … 其余 {len(bad) - 20} 条")
    print(f"✓ 解析与文章大意为可选字段：有原文就用原文，没有就留空/「{ABSENT}」；不生成、不校验 AI 标记，也不阻断流程")
    return 0


if __name__ == "__main__":
    sys.exit(main())
