"""确定性解析/大意抽取：按题号从用户提供的 Word 原文直接抄录。

用户明确要求：上传的文件本身就把「试卷 + 答案 + 解析（+ 文章大意）」放在一起，
Word 提取出的文本就是权威原文；确定正文与题号后，**按题号把对应原文抄进去即可**。
有就抄、没有就留空；答案/解析书写形式多样，不做繁复格式校验，也不与 PDF 比对、
不为抄写差异反复重跑模型。

本模块独立实现轻量抽取，不依赖严格“逐字绑定原题”的来源校验：
- 权威来源：Word 提取文本 `01_source/materials/source.txt`（若缺失退回 `01_source/正文.txt`）；
- 题号行：`58. ...【解析】...` / `74. ...` 后的孤立 `【解析】` 段；
- 文章大意：`【文章大意】...`，按其后首个题号（或上一个大题题号范围）归属。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

PLACEHOLDER = "无"
DIRECT_COPY_EVIDENCE = "按题号从 Word 原文直接抄录。"

_NUM_PREFIX = re.compile(r"^\s*(?:\*\*\s*)?(\d{1,3})\s*(?:\*\*)?\s*[.．、]")
_EXPL_LABEL = re.compile(r"(?:【\s*解析\s*】|【\s*答案\s*】)")
_SUMMARY_LABEL = re.compile(r"【\s*文章大意\s*】(.*)$")


def _summary_key(text):
    from .upload_content import _norm
    return _norm(text or "")


def _clean(line):
    """去 OCR 星号与行内空白，保留实词标点。"""
    return re.sub(r"\*{1,3}", "", line).strip()


def _word_sources(batch):
    """按优先级返回 Word 提取文本路径列表（不含 PDF OCR 附件）。"""
    batch = Path(batch)
    candidates = [batch / "01_source/materials/source.txt", batch / "01_source/正文.txt"]
    return [p for p in candidates if p.is_file()]


def _pdf_sources(batch):
    """PDF/OCR 提取文本，仅在 Word 缺少该题解析或该篇文章大意时兜底。"""
    batch = Path(batch)
    material_dir = batch / "01_source/materials"
    if not material_dir.is_dir():
        return []
    import re as _re
    return [p for p in sorted(material_dir.glob("attachment_*.txt"))
            if _re.fullmatch(r"attachment_\d{3,}\.txt", p.name) and p.is_file()]


def _parse(lines):
    """从 Word 文本行抽取 {题号: 解析原文} 与文章大意列表。"""
    expl = {}
    summaries = []
    pending_expl_owner = None
    for raw in lines:
        line = _clean(raw)
        if not line:
            continue
        # 文章大意
        m = _SUMMARY_LABEL.search(line)
        if m:
            summaries.append(m.group(1).strip())
            continue
        num = _NUM_PREFIX.match(line)
        if num:
            number = int(num.group(1))
            if _EXPL_LABEL.search(line):
                body = _EXPL_LABEL.split(line, 1)[1].strip()
                if body and number not in expl:
                    expl[number] = body
                pending_expl_owner = number
            else:
                # 有题号但无解析：可能是“答案独立成行”，随后可能是孤立【解析】。
                pending_expl_owner = number
            continue
        if _EXPL_LABEL.search(line):
            # 孤立【解析】行：归属上一条有题号的答案行。
            body = _EXPL_LABEL.split(line, 1)[1].strip()
            if body and pending_expl_owner is not None and pending_expl_owner not in expl:
                expl[pending_expl_owner] = body
            continue
    return expl, summaries


def _units(batch_dir):
    from .agent_tasks import confirmed_units
    return confirmed_units(Path(batch_dir))


def _assign_summaries(units, summaries):
    """把文章大意按顺序分配给有整组阅读/完形的单元（无题号锚点时按出现顺序）。"""
    by_uid = {}
    summary_units = [u for u in units
                     if u["unit_kind"] == "group"
                     and str(u.get("type", "") or "").strip() in
                     ("阅读理解", "任务型阅读", "完形填空", "读写综合", "语法选择")]
    for unit, text in zip(summary_units, summaries):
        by_uid[unit["unit_id"]] = text
    return by_uid


def build_payload(batch_dir):
    batch = Path(batch_dir)
    paths = _word_sources(batch)
    pdf_paths = _pdf_sources(batch)
    if not paths and not pdf_paths:
        return None
    lines = []
    for path in paths:
        lines.extend(path.read_text(encoding="utf-8-sig", errors="ignore").splitlines())
    expl, summaries = _parse(lines)
    used_pdf = []
    # 用户规则：Word 为准；Word 确实没有该题解析/该篇文章大意时，优先取 PDF，
    # 再没有才留空占位。PDF 只补充 Word 缺失的字段，绝不覆盖 Word 原文。
    if pdf_paths:
        seen = {_summary_key(item) for item in summaries}
        for path in pdf_paths:
            try:
                label = str(path.relative_to(batch))
            except ValueError:
                label = path.name
            extra_expl, extra_summaries = _parse(
                path.read_text(encoding="utf-8-sig", errors="ignore").splitlines())
            for number, text in extra_expl.items():
                if number not in expl:
                    expl[number] = text
                    used_pdf.append(label)
            for text in extra_summaries:
                key = _summary_key(text)
                if key and key not in seen:
                    summaries.append(text)
                    seen.add(key)
                    used_pdf.append(label + "（文章大意）")
    units = _units(batch)
    summary_by_uid = _assign_summaries(units, summaries)
    items = []
    for unit in units:
        numbers = [int(n) for n in unit["numbers"]]
        uid = unit["unit_id"]
        if unit["unit_kind"] == "group":
            children = [{"number": n, "explanation": expl.get(n, PLACEHOLDER)} for n in numbers]
            group_expl = PLACEHOLDER
        else:
            children = []
            group_expl = expl.get(numbers[0], PLACEHOLDER)
        items.append({
            "unit_id": uid,
            "explanation": group_expl,
            "articleSummary": summary_by_uid.get(uid, PLACEHOLDER),
            "subquestions": children,
            "evidence": [DIRECT_COPY_EVIDENCE] + (
                ["PDF OCR 兜底：" + "、".join(sorted(set(used_pdf)))] if used_pdf else []),
        })
    return {"items": items}


def install(batch_dir):
    payload = build_payload(batch_dir)
    if payload is None:
        return None
    path = Path(batch_dir) / "04_knowledge/explanations.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
