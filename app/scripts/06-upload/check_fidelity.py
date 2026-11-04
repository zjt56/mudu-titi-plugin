# -*- coding: utf-8 -*-
"""正文保真体检（E15，仅报告、非阻断）。

被 workflow/checks.py 以 `python check_fidelity.py --batch <batch_meta.json> --source <原卷>` 调用。
即使 workflow 把它当阻断项也不能阻断（本脚本自身退出码恒为 0），问题逐行 print 到 stdout。

规则：若 --source 提供，对每个 unit 的 material 取 40 字母滑窗与 source 文本比对，
统计不在 source 中的片段数，超过阈值报提示（非阻断）。无 --source 则通过。
已知例外：含 `[IMG` 或 `（材料见附图）` 的图片材料天然对不上，跳过。
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _common import arg_parser, load_batch, plain, units  # noqa: E402

WINDOW = 40          # 40 字母滑窗
# 片段数阈值：超此值报提示
MISS_RATIO_THRESHOLD = 0.5
IMG_MARKERS = ("[IMG", "（材料见附图）", "(材料见附图)")


def read_source_text(path):
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


def letters_only(text):
    """只保留字母（含中文按字保留？E15 是字母滑窗，故只取英文/数字字母）。"""
    return re.sub(r"[^A-Za-z]", "", text or "")


def has_img_marker(unit):
    material = unit.get("material") or ""
    if any(m in material for m in IMG_MARKERS):
        return True
    # 也检查 materialBlocks / attachments 里是否有图
    if unit.get("attachments"):
        return True
    blocks = unit.get("materialBlocks") or []
    for b in blocks if isinstance(blocks, list) else []:
        if isinstance(b, dict) and b.get("type") == "image":
            return True
    return False


def main(argv=None):
    args = arg_parser("正文保真体检（E15，仅报告）").parse_args(argv)
    try:
        batch = load_batch(args.batch)
    except Exception as exc:  # noqa: BLE001
        print(f"无法读取批次：{exc}；正文保真检查跳过（非阻断）")
        return 0  # 非阻断：恒 0

    if args.source is None:
        print("[OK] 未提供 --source，正文保真检查跳过")
        return 0

    src_text = read_source_text(args.source)
    if src_text is None:
        print("[OK] 未能读取原卷文本，正文保真检查跳过")
        return 0

    src_letters = letters_only(src_text)
    if not src_letters:
        print("[OK] 原卷文本无英文字母，正文保真检查跳过")
        return 0

    notes = []
    for unit in units(batch):
        if not isinstance(unit, dict):
            continue
        uid = unit.get("unit_id") or unit.get("id") or "<无 unit_id>"
        if has_img_marker(unit):
            continue  # 图片材料天然对不上，已知例外
        material = plain(unit.get("material") or "").strip()
        if not material:
            continue
        mat_letters = letters_only(material)
        if len(mat_letters) < WINDOW:
            continue  # 太短不比对
        total = 0
        miss = 0
        for i in range(0, len(mat_letters) - WINDOW + 1, WINDOW):
            seg = mat_letters[i:i + WINDOW]
            total += 1
            if seg not in src_letters:
                miss += 1
        if total == 0:
            continue
        ratio = miss / total
        if ratio > MISS_RATIO_THRESHOLD:
            notes.append(
                f"{uid}: material 有 {miss}/{total} 个 40 字母滑窗在原卷中找不到"
                f"（{ratio:.0%}），请人工确认正文保真（E15，非阻断）")

    if notes:
        for n in notes:
            print(n)
        print(f"[WARN] {len(notes)} 条正文保真提示（非阻断，需人工确认）")
    else:
        print("[OK] 正文保真检查通过（material 与原卷基本一致）")
    return 0  # 恒非阻断


if __name__ == "__main__":
    sys.exit(main())
