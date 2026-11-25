# -*- coding: utf-8 -*-
"""Cross-batch paginated type-review query and filtering helpers.

Extracted from app/workbench/server.py to keep the HTTP server module focused.
All public symbols are re-exported by workbench.server for full compatibility.
"""
from __future__ import annotations

from . import taxonomy


def _srv():
    from . import server
    return server


TYPE_REVIEW_MAX_PAGE_SIZE = 20
TYPE_REVIEW_DEFAULT_PAGE_SIZE = 20
UNTYPED_TYPE_VALUE = "__untyped__"
UNTYPED_TYPE_LABEL = "未判定"


def _batch_sort_ts(batch: dict) -> tuple:
    """与前端「试卷审核」一致的最新时间排序键（降序）。"""
    for field in ("flow_finished_at", "updated_at", "created_at"):
        val = str(batch.get(field) or "").strip()
        if val:
            return (val, str(batch.get("name") or ""))
    return ("", str(batch.get("name") or ""))


def _type_review_is_pending(row: dict) -> bool:
    """统一待审口径：与前端红点及 status=open 筛选保持严格一致。"""
    if bool(row.get("excluded")):
        return False
    return bool(row.get("needs_human")) or str(row.get("state") or "") in ("attention", "blocked")


def _type_review_row(batch: dict, row: dict) -> dict:
    b_name = str(batch.get("name") or "")
    paper = batch.get("paper") if isinstance(batch.get("paper"), dict) else {}
    paper_title = str(paper.get("title") or b_name)
    uploaded = bool(batch.get("uploaded") or batch.get("upload_pending_confirmation"))
    uid = str(row.get("unit_id") or row.get("number") or "")
    main_type = str(row.get("type") or "").strip()
    subtypes = [str(s).strip() for s in (row.get("subtypes") or []) if str(s).strip()]
    needs_human = bool(row.get("needs_human"))
    excluded = bool(row.get("excluded"))
    section = row.get("source_section") if isinstance(row.get("source_section"), dict) else None
    return {
        "batch_name": b_name,
        "paper_title": paper_title,
        "uploaded": uploaded,
        "unit_id": uid,
        "number": row.get("number"),
        "numbers": list(row.get("numbers") or []),
        "printed_numbers": row.get("printed_numbers"),
        "label": row.get("label") if row.get("label") not in (None, "") else (row.get("number") or ""),
        "unit_kind": str(row.get("unit_kind") or ""),
        "type": main_type,
        "subtypes": subtypes,
        "state": str(row.get("state") or ""),
        "needs_human": needs_human,
        "excluded": excluded,
        "open_summary": str(row.get("open_summary") or ""),
        "open_findings": row.get("open_findings"),
        "difficulty": row.get("difficulty"),
        "source_section": section,
        "knowledge": [str(k).strip() for k in (row.get("knowledge") or []) if str(k).strip()],
    }


def _match_type_review_raw(batch: dict, row: dict, main_type: str, tokens: list[str]) -> bool:
    if not tokens:
        return True
    b_name = str(batch.get("name") or "")
    paper = batch.get("paper") if isinstance(batch.get("paper"), dict) else {}
    paper_title = str(paper.get("title") or b_name)
    section = row.get("source_section") if isinstance(row.get("source_section"), dict) else {}
    label = row.get("label") if row.get("label") not in (None, "") else (row.get("number") or "")
    parts = [
        main_type or UNTYPED_TYPE_LABEL,
        " ".join(str(s).strip() for s in (row.get("subtypes") or []) if str(s).strip()),
        paper_title,
        b_name,
        str(label),
        str(row.get("number") or ""),
        str(section.get("title") or ""),
        str(section.get("context") or ""),
        " ".join(str(k).strip() for k in (row.get("knowledge") or []) if str(k).strip()),
    ]
    hay = " ".join(parts).lower()
    return all(tok in hay for tok in tokens)


def type_review_units(*, question_type: str = "", query: str = "", status: str = "all",
                      uploaded: str = "all", page: int = 1,
                      page_size: int = TYPE_REVIEW_DEFAULT_PAGE_SIZE) -> dict:
    """按题型分页返回跨批次轻量题组列表，每页最多 20 题，仅对本页 20 题构造完整行对象。"""
    ps = max(1, min(TYPE_REVIEW_MAX_PAGE_SIZE, int(page_size)))
    status_norm = "open" if str(status or "").strip().lower() == "open" else "all"
    raw_uploaded = str(uploaded or "").strip().lower()
    uploaded_norm = raw_uploaded if raw_uploaded in ("unuploaded", "uploaded") else "all"
    raw_selected = str(question_type or "").strip()
    if raw_selected in ("", "all", "全部", "全部题型"):
        selected = ""
    elif raw_selected in (UNTYPED_TYPE_VALUE, UNTYPED_TYPE_LABEL):
        selected = UNTYPED_TYPE_VALUE
    else:
        selected = raw_selected
    q_text = str(query or "").strip()
    q_tokens = [tok for tok in q_text.lower().split() if tok]

    batches = [b for b in (_srv().cached_list_batches() or []) if isinstance(b, dict)]
    batches.sort(key=_batch_sort_ts, reverse=True)

    all_total = 0
    all_pending = 0
    counts_by_type: dict[str, int] = {}
    pending_by_type: dict[str, int] = {}
    extra_types: list[str] = []
    extra_set: set[str] = set()
    catalog_types = list(taxonomy.types())
    catalog_set = set(catalog_types)
    matched_pairs: list[tuple[dict, dict]] = []

    for b in batches:
        if _srv()._is_queued_unfinished(b):
            continue
        is_uploaded = _srv()._is_batch_uploaded(b)
        if uploaded_norm == "unuploaded" and is_uploaded:
            continue
        if uploaded_norm == "uploaded" and not is_uploaded:
            continue
        rows = b.get("rows")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            all_total += 1
            main_type = str(row.get("type") or "").strip()
            t_key = main_type or UNTYPED_TYPE_VALUE
            counts_by_type[t_key] = counts_by_type.get(t_key, 0) + 1
            is_pending = _type_review_is_pending(row)
            if is_pending:
                all_pending += 1
                pending_by_type[t_key] = pending_by_type.get(t_key, 0) + 1
            if main_type and main_type not in catalog_set and main_type not in extra_set:
                extra_set.add(main_type)
                extra_types.append(main_type)
            if status_norm == "open" and not is_pending:
                continue
            if selected == UNTYPED_TYPE_VALUE:
                if main_type:
                    continue
            elif selected and main_type != selected:
                continue
            if q_tokens and not _match_type_review_raw(b, row, main_type, q_tokens):
                continue
            matched_pairs.append((b, row))

    type_entries = [
        {
            "name": t_name,
            "value": t_name,
            "count": counts_by_type.get(t_name, 0),
            "pending": pending_by_type.get(t_name, 0),
        }
        for t_name in (*catalog_types, *extra_types)
    ]
    if counts_by_type.get(UNTYPED_TYPE_VALUE, 0) > 0:
        type_entries.append({
            "name": UNTYPED_TYPE_LABEL,
            "value": UNTYPED_TYPE_VALUE,
            "count": counts_by_type[UNTYPED_TYPE_VALUE],
            "pending": pending_by_type.get(UNTYPED_TYPE_VALUE, 0),
        })

    total = len(matched_pairs)
    total_pages = max(1, (total + ps - 1) // ps)
    current_page = max(1, min(total_pages, int(page)))
    start = (current_page - 1) * ps
    page_items = [_type_review_row(b, row) for b, row in matched_pairs[start : start + ps]]

    return {
        "items": page_items,
        "total": total,
        "page": current_page,
        "page_size": ps,
        "max_page_size": TYPE_REVIEW_MAX_PAGE_SIZE,
        "total_pages": total_pages,
        "selected_type": selected,
        "query": q_text,
        "status": status_norm,
        "uploaded": uploaded_norm,
        "all_total": all_total,
        "all_pending": all_pending,
        "types": type_entries,
    }
