# -*- coding: utf-8 -*-
"""解析 / 文章大意来源体检：原卷没有时可以自己写，但**必须标注**；听力类不写内容解析。

    python check_explanations.py --batch 06_batch/b.json --source 01_source/正文.txt \
        --source 01_source_ans/正文.txt
    python check_explanations.py --batch b.json --source-dir 01_source --source-dir 01_source_ans

被 workflow/checks.py 以 `python check_explanations.py --batch <batch_meta.json> [--source <path>]`
调用；--source 可重复，也可用 --source-dir 给目录。

编辑口径（constraints.md F1/F2/F3/F5/F6/F7）
------------------------------------------
1. 原卷/答案册**有**解析或【文章大意】 → 逐字录入，能在这里被定位到（忽略空白与标点）。
2. 原卷**没有** → **必须自己写**，末尾带标记 `（AI生成，待校对）`，方便编辑筛出来校对；
   没有标记的自撰内容一律判违规。非听力单元一条解析都没有也拦停（F7：缺就自补并标 AI）。
3. **听力类题型（`听力*`）不写内容解析**：原卷没给就不写；答案若是自补的，
   `explanation` 只允许一行 `答案由AI生成（AI生成，待校对）`，多写按「编解析」拦（F6）。
   听力稿（`听力原文：…`）属于材料，不是解析。
4. 文章大意（`articleSummary` / `article_summary`）同样查：可溯源或带标记，否则拦停（F3/F5）。
退出码 0=通过，1=有问题；问题逐行 print 到 stdout。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _common import load_batch, units  # noqa: E402

AI_MARK = "（AI生成，待校对）"
LISTENING_NOTE = "答案由AI生成" + AI_MARK        # 听力单元答案自补时唯一允许的一行
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
    """返回 (统计, 违规文本列表)。统计: 可溯源 / AI 标注 / 听力稿。"""
    stat = {"source": 0, "ai": 0, "transcript": 0}
    bad = []
    for it in items:
        uid = it.get("unit_id") or it.get("id") or "<无 unit_id>"
        qtype = it.get("question_type") or it.get("type") or ""
        listening = isinstance(qtype, str) and qtype.startswith(LISTENING_PREFIX)
        if not listening:
            children = it.get("subquestions") or []
            targets = [(f"子题{child.get('number')}", str(child.get('explanation') or '').strip())
                       for child in children if isinstance(child, dict)] if children else [("单元", str(it.get('explanation') or '').strip())]
            for where, explanation in targets:
                if not explanation:
                    bad.append(f"{uid} [{where}] 解析为空（F7 要求自己补并标 AI）")
        for where, text in iter_explanations(it):
            if not text:
                continue
            if text.startswith(TRANSCRIPT):
                stat["transcript"] += 1
                continue
            if AI_MARK in text:
                if not text.endswith(AI_MARK) or not text[:-len(AI_MARK)].strip():
                    bad.append(f"{uid} [{where}] AI解析须有实际内容且来源标记位于末尾")
                    continue
                stat["ai"] += 1
                if listening and norm(text) != norm(LISTENING_NOTE):
                    bad.append(f"{uid} [{where}] 听力类写了内容解析（F6）；答案自补时只允许一行"
                               f"「{LISTENING_NOTE}」 — {text[:60]!r}")
                continue
            if norm(text) and norm(text) in source:
                stat["source"] += 1
                continue
            bad.append(f"{uid} [{where}] 既不在原卷里、也没有 AI 标记 — {text[:60]!r}")
        summary = article_summary(it)
        if summary:
            if AI_MARK in summary:
                if not summary.endswith(AI_MARK) or not summary[:-len(AI_MARK)].strip():
                    bad.append(f"{uid} [文章大意] AI大意须有实际内容且来源标记位于末尾")
                    continue
                stat["ai"] += 1
            elif norm(summary) in source:
                stat["source"] += 1
            else:
                bad.append(f"{uid} [文章大意] 既不在原卷/答案册里、也没有 AI 标记（F3/F5） — {summary[:60]!r}")
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
    print(f"检查 {len(items)} 个单元 | 可溯源 {stat['source']} 条 | AI 标注 {stat['ai']} 条 | "
          f"听力稿 {stat['transcript']} 条 | 违规 {len(bad)} 条")
    for line in bad[:20]:
        print(f"  ✗ {line}")
    if len(bad) > 20:
        print(f"  … 其余 {len(bad) - 20} 条")
    if bad and not args.allow_extra:
        print("\n口径（F1/F2/F3/F5/F6/F7）：原卷有解析/文章大意就逐字录；原卷没有**必须自己写**，"
              f"末尾带 `{AI_MARK}`；听力类不写内容解析，答案自补时只留一行「{LISTENING_NOTE}」。")
        print(f"[FAIL] {len(bad)} 条问题", file=sys.stderr)
        return 1
    if bad:
        print("\n（--allow-extra：以上按放行处理，请在交接单注明）")
    else:
        print("✓ 解析与文章大意均为「原卷原文」或「已标注的 AI 生成」，且听力题未编解析")
    return 0


if __name__ == "__main__":
    sys.exit(main())
