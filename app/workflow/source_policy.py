# -*- coding: utf-8 -*-
"""提取来源优先级：Word(.docx) > 带文字层 PDF > 扫描件/图片。

用户 2026-11-11 确认的规则，落地为纯函数，供三处共用同一口径：

1. `app/workbench/server.py::handle_upload`：同一批上传同时含 Word 与 PDF 时，
   Word 一定是 `source`（提取主源），PDF 只作附件供版面核对；
2. `app/workflow/extract.py::run`：把本次实际使用的来源与 PDF 文字层/OCR 页数
   记录进 `01_source/source-policy.json` 与 `07_review/extraction-report.json` 的
   `source_policy` 字段，让“用哪份原文”可复核；
3. `app/workflow/material_classify.py`：Word 权威时 PDF 的 role 仍是
   `structure_reference` 且 `content_authority=false`，副本不参与唯一绑定竞争。

为什么 Word 优先（不是偏好，是能力差异）：
- .docx 由标准库直接解析 OOXML，段落/表格网格/合并跨度/画线/图片关系都是结构化
  数据，不需要 OCR 环境，逐字位置可信；
- 文字层 PDF 需要运行环境提供 PyMuPDF，且文字层质量只能靠启发式判断
  （`workflow/ocr_backend._quality_reasons`：短文本、可疑字符、长串粘连、
  可打印比例过低），不合格页才转 OCR；OCR 文字没有逐字坐标与可信度；
- 扫描件/纯图片 Word 只能走 OCR，属于最后手段。

本模块只做判定与记录，不读文件、不改批次数据。
"""

from __future__ import annotations

import os
from pathlib import Path

RULE_ID = "source-priority-v1"
RULE = "word > text_layer_pdf > scan_ocr"

WORD_KIND = "word"
LEGACY_WORD_KIND = "legacy_word"
PDF_KIND = "pdf"
OTHER_KIND = "other"

WORD_SUFFIXES = (".docx",)
LEGACY_WORD_SUFFIXES = (".doc",)
PDF_SUFFIXES = (".pdf",)

# 数值越小优先级越高；旧版 .doc 需要先转 .docx（可能丢图），排在 .docx 之后。
_RANK = {WORD_KIND: 0, LEGACY_WORD_KIND: 1, PDF_KIND: 2, OTHER_KIND: 3}

_PDF_MODE_TEXT = "text_layer"
_PDF_MODE_OCR = "ocr"
_PDF_MODE_MIXED = "mixed"
_PDF_MODE_UNKNOWN = "unknown"

_MODE_LABEL = {
    _PDF_MODE_TEXT: "文字层",
    _PDF_MODE_OCR: "OCR",
    _PDF_MODE_MIXED: "文字层 + OCR 混合",
    _PDF_MODE_UNKNOWN: "未记录",
}


def _name(item) -> str:
    """兼容 str / Path / 上传文件 dict（取 filename）。"""
    if isinstance(item, dict):
        return str(item.get("filename") or item.get("name") or "")
    return str(item)


def kind(item) -> str:
    suffix = Path(_name(item)).suffix.lower()
    if suffix in WORD_SUFFIXES:
        return WORD_KIND
    if suffix in LEGACY_WORD_SUFFIXES:
        return LEGACY_WORD_KIND
    if suffix in PDF_SUFFIXES:
        return PDF_KIND
    return OTHER_KIND


def rank(item) -> int:
    return _RANK[kind(item)]


def select_primary(candidates):
    """按优先级选出提取主源；同级保持上传顺序（稳定）。无候选返回 None。"""
    items = [item for item in candidates if item is not None]
    if not items:
        return None
    best = min(range(len(items)), key=lambda index: (rank(items[index]), index))
    return items[best]


def pdf_usage(pages) -> dict:
    """统计 PDF 各页实际用法：原生文字层 / OCR 兜底 / OCR 缺失。"""
    rows = [page for page in (pages or []) if isinstance(page, dict)]
    ocr_pages = [page for page in rows if str(page.get("method") or "") == "ocr"]
    skipped = [page for page in rows if page.get("ocr_skipped")]
    if not rows:
        mode = _PDF_MODE_UNKNOWN
    elif ocr_pages and len(ocr_pages) == len(rows):
        mode = _PDF_MODE_OCR
    elif ocr_pages:
        mode = _PDF_MODE_MIXED
    else:
        mode = _PDF_MODE_TEXT
    return {"mode": mode, "label": _MODE_LABEL[mode],
            "pages": len(rows), "ocr_pages": len(ocr_pages),
            "ocr_skipped_pages": len(skipped)}


def build_report(source, supporting=(), pages=None, visual_assist: bool | None = None) -> dict:
    """生成 `01_source/source-policy.json` 的内容。

    source: 本次提取主源路径；supporting: 其余上传文件路径（附件/答案卷）；
    pages: PDF 主源时的逐页结果（extraction.json 的 pages）。
    """
    source_name = os.path.basename(_name(source)) or None
    source_kind = kind(source)
    usage = pdf_usage(pages) if source_kind == PDF_KIND else None
    if source_kind == WORD_KIND:
        extraction = "docx_native"
        authority = "word"
        note = "Word 原卷用标准库原生解析，逐字位置与表格网格可信，不经过 OCR。"
    elif source_kind == LEGACY_WORD_KIND:
        extraction = "doc_legacy_converted"
        authority = "word"
        note = "旧版 .doc 先转为 .docx 再原生解析；转换可能丢失图片，需核对。"
    elif source_kind == PDF_KIND:
        authority = "pdf"
        extraction = "pdf_" + (usage or {}).get("mode", _PDF_MODE_UNKNOWN)
        if (usage or {}).get("mode") == _PDF_MODE_TEXT:
            note = "仅上传 PDF：文字层逐页直接解析，未触发 OCR。"
        elif (usage or {}).get("mode") == _PDF_MODE_MIXED:
            note = "仅上传 PDF：文字层可用页直接解析，质量不足页已转 OCR（OCR 无逐字坐标）。"
        elif (usage or {}).get("mode") == _PDF_MODE_OCR:
            note = "仅上传 PDF 且逐页质量不足，全部按 OCR 结果解析，需核对题号、音标、画线与表格顺序。"
        else:
            note = "仅上传 PDF，逐页用法以 extraction.json 的 pages 记录为准。"
    else:
        extraction = "unknown"
        authority = "unknown"
        note = "未识别的来源格式。"
    supporting_rows = []
    for item in supporting:
        if item is None:
            continue
        item_kind = kind(item)
        supporting_rows.append({
            "name": os.path.basename(_name(item)) or None,
            "kind": item_kind,
            "role": "structure_reference" if item_kind == PDF_KIND and source_kind in (WORD_KIND, LEGACY_WORD_KIND)
            else "supporting",
            "content_authority": item_kind == source_kind and item_kind in (WORD_KIND, LEGACY_WORD_KIND),
        })
    report = {
        "version": 1,
        "rule": RULE,
        "rule_id": RULE_ID,
        "source": source_name,
        "source_kind": source_kind,
        "authority": authority,
        "extraction": extraction,
        "note": note,
        "supporting": supporting_rows,
    }
    if usage is not None:
        report["pdf"] = usage
    report["recommendation"] = recommendation(source_kind, usage, supporting_rows)
    # 视觉理解参与结构判断的标记（用户 2026-11-11：有 PDF 才 OCR；纯 Word 直接文字提取，
    # 两种都配合视觉理解补图/判结构）。仅记录事实，不改变任何提取结果。
    if visual_assist is not None:
        report["visual_structure_assist"] = bool(visual_assist)
    return report


def recommendation(source_kind, usage, supporting_rows) -> str:
    """给工作台/日志的一句可执行提示，不改变任何提取结果。"""
    if source_kind == WORD_KIND:
        if any(row["kind"] == PDF_KIND for row in supporting_rows):
            return "同时上传了 PDF：Word 为唯一提取主源，PDF 仅作版面/层级核对。"
        return "Word 原生解析，无需 OCR。"
    if source_kind == LEGACY_WORD_KIND:
        return "旧版 .doc 已转 .docx 解析；如有图片请核对是否完整。"
    if source_kind == PDF_KIND:
        mode = (usage or {}).get("mode")
        if mode == _PDF_MODE_TEXT:
            return "PDF 文字层可用；如另有 Word 原卷，上传 Word 可获得更完整的表格与画线。"
        return "PDF 部分或全部页走了 OCR；如另有 Word 原卷，请上传 Word 以免 OCR 误差。"
    return "来源格式未识别，请确认上传的是 .docx 或 .pdf。"


def upload_notice(primary, others=()) -> str:
    """上传成功后返回给前端的来源提示；无需提示时返回空串。"""
    primary_kind = kind(primary)
    others = [item for item in others if item is not None]
    pdf_others = [item for item in others if kind(item) == PDF_KIND]
    if primary_kind == WORD_KIND:
        if pdf_others:
            return "已用 Word 原卷作为提取主源，PDF 附件只作版面核对（不覆盖 Word 原文）。"
        return "已用 Word 原卷作为提取主源：原生解析，无需 OCR。"
    if primary_kind == LEGACY_WORD_KIND:
        return "旧版 .doc 将先转 .docx 再解析；如含图片可能丢失，建议另存为 .docx 后重传。"
    if primary_kind == PDF_KIND:
        return "仅上传 PDF：文字层可用的页直接解析，质量不足的页自动转 OCR；若另有 Word 原卷，上传 Word 更完整。"
    return ""
