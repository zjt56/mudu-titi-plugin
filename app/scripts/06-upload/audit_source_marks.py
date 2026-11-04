# -*- coding: utf-8 -*-
"""提示标记体检（D12 / B9）。

被 workflow/checks.py 以 `python audit_source_marks.py --batch <batch_meta.json> --source <原卷>` 调用。
退出码 0=通过，1=有问题；问题逐行 print 到 stdout。

规则：若 --source 提供，读原卷文本，找形如 `[2022·安徽…]` 或 `【…】` 的来源/年份/地区标注，
检查题干是否保留：原卷有而题干丢失则报。无 --source 则通过。

实现说明（保守、避免误报）：
- 来源标记通常出现在原卷题干或大题区。我们归一化后，对每个“原卷来源标记”检查该标记
  或其核心 token（年份+地区）是否在批次任一 unit 的 stem/material 中出现；
  全部批次都找不到 → 认为标记整体丢失，报一条问题（逐标记一条）。
- `【解析】`/`【答案】` 等栏目名不算来源标记，予以排除。
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _common import arg_parser, load_batch, plain, report, units  # noqa: E402

# [2022·安徽…] / [2022.安徽…] / [2022 安徽 …]
_BRACKET_MARK = re.compile(r"\[[^\[\]]*\d{4}[^\[\]]*\]")
# 【…】
_CORNER_MARK = re.compile(r"【([^】]*)】")
# 非来源的方括号栏目名
_CORNER_SKIP = re.compile(
    r"^(解析|答案|详解|分析|考点|点睛|点评|思路|导语|文章大意|小题|备注|注|提示)")


def read_source_text(path):
    """读取原卷文本：支持 .docx（zip 内 document.xml）与纯文本。读不到返回 None。"""
    p = Path(path)
    if not p.exists():
        return None
    if p.suffix.lower() in (".docx", ".docm"):
        try:
            with zipfile.ZipFile(p) as z:
                xml = z.read("word/document.xml").decode("utf-8", errors="replace")
            xml = re.sub(r"</w:p>", "\n", xml)
            return re.sub(r"<[^>]+>", "", xml)
        except Exception:  # noqa: BLE001
            return None
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None


def _norm(s):
    return re.sub(r"\s+", "", s or "")


def collect_source_marks(text):
    """返回原卷来源标记的原始字符串集合。"""
    marks = []
    for m in _BRACKET_MARK.findall(text):
        marks.append(m)
    for m in _CORNER_MARK.findall(text):
        inner = m.strip()
        if not inner:
            continue
        if _CORNER_SKIP.match(inner):
            continue
        if re.search(r"\d{4}|安徽|省|市|中考|真题|·", inner):
            marks.append("【" + inner + "】")
    # 去重保序
    seen, out = set(), []
    for m in marks:
        key = _norm(m)
        if key not in seen:
            seen.add(key)
            out.append(m)
    return out


def _mark_tokens(mark):
    """提取标记中的识别 token：年份与中文字地区词。"""
    year = re.findall(r"\d{4}", mark)
    region = re.findall(r"[\u4e00-\u9fff]{2,}", mark)
    tokens = []
    for y in year:
        tokens.append(y)
    for r in region:
        tokens.append(r)
    if not tokens:
        tokens.append(_norm(mark))
    return tokens


def collect_batch_corpus(batch):
    """把全部 unit 的 stem/material/子题 stem 拼成一个大文本（归一化）。"""
    parts = []
    for u in units(batch):
        if not isinstance(u, dict):
            continue
        parts.append(plain(u.get("stem") or ""))
        parts.append(plain(u.get("material") or ""))
        for sub in (u.get("subquestions") or []):
            if isinstance(sub, dict):
                parts.append(plain(sub.get("stem") or ""))
    return _norm("\n".join(parts))


def main(argv=None):
    args = arg_parser("提示标记体检（D12/B9）").parse_args(argv)
    try:
        batch = load_batch(args.batch)
    except Exception as exc:  # noqa: BLE001
        print(f"无法读取批次：{exc}")
        return 1

    if args.source is None:
        return report([], ok_msg="未提供 --source，跳过提示标记检查")

    src_text = read_source_text(args.source)
    if src_text is None:
        return report([], ok_msg="未能读取原卷文本，跳过提示标记检查")

    src_marks = collect_source_marks(src_text)
    if not src_marks:
        return report([], ok_msg="原卷未见来源/年份标记，无需校验")

    corpus = collect_batch_corpus(batch)

    problems = []
    for mark in src_marks:
        tokens = _mark_tokens(mark)
        # 只要标记的任一年份或地区 token 在批次正文里出现过，即视为已保留
        kept = any(tok and tok in corpus for tok in tokens)
        if not kept:
            problems.append(
                f"原卷来源标记 {mark!r} 在批次题干/材料中未见保留（D12/B9 丢标记）")

    return report(problems, ok_msg="提示标记检查通过（原卷来源标记均有保留）")


if __name__ == "__main__":
    sys.exit(main())
