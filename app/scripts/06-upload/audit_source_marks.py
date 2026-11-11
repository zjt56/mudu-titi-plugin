# -*- coding: utf-8 -*-
"""只读提示标记体检（D27/B9）：完整标记比对，可信同步 scoped 来源再验证归属。

无来源输入保持兼容；旧批次仅查完整标记存在，不以年份/地区片段冒充原文。
不改写题目、来源或上传状态。
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _common import arg_parser, load_batch, report, units  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workflow.sync_practice import (  # noqa: E402
    source_marks, audit_batch_source_marks, _unit_text, _mark_key,
)


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


def collect_source_marks(text):
    """兼容脚本调用者；识别与结构留痕共用同一口径。"""
    return list(dict.fromkeys(source_marks(text)))


def collect_batch_corpus(batch):
    return _mark_key("\n".join(_unit_text(u) for u in units(batch) if isinstance(u, dict)))


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

    findings = audit_batch_source_marks(list(units(batch)), src_text)
    problems = [f"{row.get('unit_id') or row.get('numbers') or '批次'}: {problem}（D27/B9）"
                for row in findings for problem in row.get("problems") or []]
    return report(problems, ok_msg="提示标记检查通过（原卷完整标记及可验证归属均有保留）")


if __name__ == "__main__":
    sys.exit(main())
