"""Read-only projections of batch records for workbench views."""
from __future__ import annotations

from datetime import datetime

def _parse_iso(value):
    """Parse a manifest ISO timestamp; return None instead of raising."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


_POST_FLOW_TIMING_STAGES = frozenset({"upload_gate", "upload", "publish_gate"})


def _flow_timing(manifest):
    """Flow start/finish and elapsed minutes from real stage timestamps.

    The completion time is the moment the automated flow finished (the completed
    format_gate, or the latest finished automated-flow stage), not later manual
    upload/publish stages or manifest re-exports. Elapsed is rounded to whole
    minutes from the earliest automated-flow start.
    """
    stages = (manifest or {}).get("stages") or {}
    flow_started, flow_finished = [], []
    all_started, all_finished = [], []
    for stage_id, record in stages.items():
        if not isinstance(record, dict):
            continue
        s = _parse_iso(record.get("started_at"))
        f = _parse_iso(record.get("finished_at"))
        if s:
            all_started.append(s)
            if stage_id not in _POST_FLOW_TIMING_STAGES:
                flow_started.append(s)
        if f:
            all_finished.append(f)
            if stage_id not in _POST_FLOW_TIMING_STAGES:
                flow_finished.append(f)
    if not all_finished:
        return None, None, None
    fg = stages.get("format_gate") or {}
    fg_finished = _parse_iso(fg.get("finished_at")) if fg.get("status") == "completed" else None
    if fg_finished is not None:
        finish = fg_finished
    elif flow_finished:
        finish = max(flow_finished)
    else:
        finish = max(all_finished)
    started = flow_started or all_started
    extract_record = stages.get("extract") if isinstance(stages.get("extract"), dict) else {}
    extract_start = _parse_iso(extract_record.get("started_at"))
    if extract_start is not None and finish >= extract_start:
        current_started = [s for s in started if s >= extract_start]
        if current_started:
            started = current_started
    start = min(started) if started else _parse_iso((manifest or {}).get("created_at"))
    minutes = None
    if start is not None and finish is not None and finish >= start:
        intervals = []
        for stage_id, record in stages.items():
            if not isinstance(record, dict) or stage_id in _POST_FLOW_TIMING_STAGES:
                continue
            s = _parse_iso(record.get("started_at"))
            f = _parse_iso(record.get("finished_at"))
            if s is not None and f is not None and f >= s:
                if s >= start and f <= finish:
                    intervals.append((s, f))
            elif f is not None and start <= f <= finish:
                intervals.append((f, f))
            elif s is not None and start <= s <= finish:
                intervals.append((s, s))
        if intervals:
            intervals.sort(key=lambda pair: (pair[0], pair[1]))
            merged = [intervals[0]]
            for s, f in intervals[1:]:
                prev_s, prev_f = merged[-1]
                # Gaps longer than 2 hours indicate an idle pause between sessions,
                # not active pipeline execution time.
                if (s - prev_f).total_seconds() <= 7200:
                    merged[-1] = (prev_s, max(prev_f, f))
                else:
                    merged.append((s, f))
            active_seconds = sum((e - b).total_seconds() for b, e in merged)
            minutes = int(round(active_seconds / 60.0))
        else:
            minutes = int(round((finish - start).total_seconds() / 60.0))
    return (start.isoformat(timespec="seconds") if start else None,
            finish.isoformat(timespec="seconds"), minutes)


def _is_batch_uploaded(batch: dict) -> bool:
    """判断批次是否已上传到远端（含已写入云端上传暂存、待远端确认发布）。"""
    if not isinstance(batch, dict):
        return False
    if batch.get("uploaded") or batch.get("upload_pending_confirmation"):
        return True
    stages = batch.get("stages")
    if isinstance(stages, list) and any(
        isinstance(s, dict) and s.get("id") == "upload" and s.get("status") == "completed"
        for s in stages
    ):
        return True
    upload_status = batch.get("upload_status")
    rec = upload_status.get("receipt") if isinstance(upload_status, dict) else None
    return bool(
        isinstance(rec, dict)
        and (rec.get("state") == "staged" or rec.get("submitted_at") or rec.get("remote_confirmation_required"))
    )


def _is_queued_unfinished(batch: dict) -> bool:
    """判断批次是否仍在「上传试卷 → 排队区」且自动流程尚未完成。

    若批次已在当前阶段发生失败/阻断且当前无活跃推进任务（stage_blocked=True），
    同步透出到「试卷审核」卡片页标红展示，不隐藏在后台。
    """
    if not isinstance(batch, dict):
        return False
    if batch.get("flow_done") is True:
        return False
    if _is_batch_uploaded(batch):
        return False
    if batch.get("queue_removed"):
        return False
    adv = batch.get("advance") or {}
    if batch.get("stage_blocked") and not (adv.get("running") or adv.get("queued") or adv.get("waiting")):
        return False
    return batch.get("flow_done") is False


def batch_list_payload(batches, *, unuploaded=False, is_lite=False, is_review_list=False):
    """Build the requested list view without mutating the cached batch records."""
    if unuploaded:
        batches = [item for item in batches if not (isinstance(item, dict) and item.get("uploaded"))]
    has_active_work = False
    if is_review_list:
        has_active_work = any(
            isinstance(item, dict) and (
                _is_queued_unfinished(item)
                or bool((item.get("advance") or {}).get("running"))
                or bool((item.get("advance") or {}).get("queued"))
                or bool((item.get("advance") or {}).get("waiting"))
                or bool((item.get("upload_status") or {}).get("running"))
            )
            for item in batches
        )
        # 上传试卷排队区中尚未完成的批次不进入「试卷审核」列表。
        batches = [item for item in batches if not _is_queued_unfinished(item)]
    if is_lite or is_review_list:
        drop_keys = ("rows", "extraction", "structure_gate", "diagnostics") if is_review_list else ("rows", "extraction")
        slim_batches = []
        for item in batches:
            if not isinstance(item, dict):
                slim_batches.append(item)
                continue
            rows = item.get("rows")
            first_uid = ""
            if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                first_uid = str(rows[0].get("unit_id") or rows[0].get("number") or "")
            slim = {k: v for k, v in item.items() if k not in drop_keys}
            if is_review_list and isinstance(slim.get("stage"), dict):
                slim["stage"] = {"hint": slim["stage"].get("hint", "")}
            if first_uid:
                slim["first_unit_id"] = first_uid
            slim_batches.append(slim)
        batches = slim_batches
    payload = {"batches": batches}
    if is_review_list:
        payload["has_active_work"] = has_active_work
    return payload
