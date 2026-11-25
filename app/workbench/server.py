# -*- coding: utf-8 -*-
"""Local-only review workbench API. Upload writes to the cloud 上传暂存 only; no publish.

题目列表按“入库单元”合并：一个独立题一行，一个题组（完形、任务型阅读、
听力对话组等）一行，前端一次显示整组材料与全部小题。
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import uuid
from functools import wraps
import base64
import shutil
import stat
import subprocess
import sys
import threading
from time import monotonic, time, time_ns
from datetime import datetime
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from workbench import anomaly_views, media_audit, taxonomy

from .batch_overview import (
    _flow_timing, _is_batch_uploaded, _is_queued_unfinished, _parse_iso,
    batch_list_payload,
)

try:
    from workflow.preview import (batch_rows as preview_batch_rows, slice_range, slice_text,
                                  source_slice, source_group_candidates, section_leadin)
except ImportError:  # 直接双击运行时给出手动路径
    preview_batch_rows = slice_range = slice_text = source_slice = source_group_candidates = section_leadin = None

try:
    from workflow import auto as auto_flow
    from workflow import contracts as wf_contracts
    from workflow import manifest as wf_manifest
    from workflow import execution_state as wf_execution_state
    from workflow import source_policy as wf_source_policy
except ImportError:  # 看台可以脱离流水线单独跑
    auto_flow = wf_contracts = wf_manifest = wf_execution_state = None
    wf_source_policy = None

ROOT = Path(__file__).resolve().parents[1]
_SERVER_STARTED_AT = time()
_WINDOWS = os.name == "nt"
# 插件版：宿主编译生效配置后，让本进程拉起的 runner/auto 子进程也走插件模型路由。
# 独立运行 app（没有 backend/.local-agent.effective.json）时保持原行为。
_plugin_agent_config = ROOT.parent / "backend" / ".local-agent.effective.json"
if _plugin_agent_config.is_file() and not os.environ.get("MUDU_AGENT_CONFIG"):
    os.environ["MUDU_AGENT_CONFIG"] = str(_plugin_agent_config)
# 数据目录可用环境变量指定（默认仍是本仓库下的 workflows/inbox）。
# 插件化独立开发时，代码放在插件版/app，数据仍指向用户真实批次目录，
# 无需软链接（软链接会被 batch_dir 拒绝），只覆盖数据根即可。
WORKFLOWS = Path(os.environ.get("WF_WORKFLOWS_DIR") or (ROOT / "workflows")).resolve()
INBOX = Path(os.environ.get("WF_INBOX_DIR") or (ROOT / "inbox")).resolve()
MEDIA_KINDS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml"}
DOC_SUFFIXES = (".docx", ".pdf")
AUDIO_SUFFIXES = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac")
SIDE_EFFECTS = ("upload", "publish", "delete")
_lock = threading.RLock()
_batch_locks_guard = threading.Lock()
_batch_locks: dict[str, threading.RLock] = {}
_batches_snapshot = None
_batches_epoch = 0
_batches_read_lock = threading.Lock()
_remote_worker_client = None
_remote_worker_lock = threading.Lock()


def _batch_lock(name: str) -> threading.RLock:
    with _batch_locks_guard:
        lock = _batch_locks.get(name)
        if lock is None:
            lock = threading.RLock()
            _batch_locks[name] = lock
        return lock


def _is_split_worker() -> bool:
    from workbench import execution_client
    return execution_client.is_split_mode()


def _remote_worker():
    global _remote_worker_client
    from workbench import execution_client
    with _remote_worker_lock:
        if _remote_worker_client is None or _remote_worker_client._workflows_dir != WORKFLOWS:
            _remote_worker_client = execution_client.RemoteWorkerClient(WORKFLOWS)
        return _remote_worker_client


def _is_advance_running(name: str) -> bool:
    if _is_split_worker():
        return bool((_remote_worker().advance_state(name) or {}).get("running"))
    return bool((_advance_state.get(name) or {}).get("running"))
# 听力切段后台任务（安装 / 切段共用一把闸，避免并发下载与切片）。
_audio_job = {"running": False, "kind": "", "batch": "", "lock": threading.Lock()}
# CHANGELOG 保留：超过两天的变更记录定期自动删除（用户 2026-11-19 要求）。
CHANGELOG_DIR = Path(os.environ.get("WF_CHANGELOG_DIR") or (ROOT.parent / "CHANGELOG")).resolve()
CHANGELOG_KEEP_DAYS = 2
CHANGELOG_PRUNE_INTERVAL_SECONDS = int(os.environ.get("WF_CHANGELOG_PRUNE_INTERVAL") or 6 * 60 * 60)
_changelog_prune_halt = threading.Event()
_changelog_prune_state: dict = {"last_result": None, "last_run": None}


def serialized(function):
    """同服务的建批次、推进注册、删除与写入共享临界区。"""
    @wraps(function)
    def wrapped(*args, **kwargs):
        global _batches_snapshot, _batches_epoch
        # Broker receipts and read-only operations heartbeats own their own
        # locks/atomic files. Exempting /api/operations/service avoids holding
        # the global write lock and busting _batches_snapshot every 5 seconds.
        if function.__name__ == "do_POST" and args:
            post_path = unquote(urlparse(getattr(args[0], "path", "")).path)
            if post_path.startswith("/api/content-agent/") or post_path == "/api/operations/service":
                return function(*args, **kwargs)
            # Single-batch mutations (resolve/edit/advance) use a fine-grained
            # per-batch lock so reviewing Batch A never blocks Batch B.
            parts = [p for p in post_path.split("/") if p]
            if len(parts) >= 4 and parts[0] == "api" and parts[1] == "batches" and parts[3] not in ("delete",):
                try:
                    with _batch_lock(parts[2]):
                        return function(*args, **kwargs)
                finally:
                    with _lock:
                        _batches_epoch += 1
                        _batches_snapshot = None
        with _lock:
            try:
                return function(*args, **kwargs)
            finally:
                # In-flight reads cannot republish a snapshot from before a write.
                _batches_epoch += 1
                _batches_snapshot = None
    return wrapped


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load(path, fallback=None):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return fallback


def save_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _validate_batch_name(name):
    if not isinstance(name, str) or not name or Path(name).name != name or name in (".", "..") or any(c in name for c in ("\\", "/", "\x00")):
        raise ValueError("非法批次名")


def batch_dir(name):
    _validate_batch_name(name)
    if WORKFLOWS.is_symlink() or (WORKFLOWS / name).is_symlink():
        raise ValueError("批次目录不能是软链接")
    target = (WORKFLOWS / name).resolve()
    if target.parent != WORKFLOWS.resolve() or not target.is_dir():
        raise ValueError("批次不存在")
    return target


def report_for(folder):
    report = load(folder / "07_review" / "report.json", {}) or {}
    manifest = load(folder / "workflow" / "manifest.json", {}) or {}
    queue = load(folder / "07_review" / "manual-review-queue.json", {}) or {}
    return report, manifest, queue


def question_rows(folder, report):
    """逐小题行：report.json 优先，其次候选文件。"""
    rows = report.get("rows") if isinstance(report, dict) else None
    if rows:
        return [r for r in rows if isinstance(r, dict)]
    result = []
    candidates = folder / "candidates"
    for path in sorted(candidates.glob("*.json")) if candidates.is_dir() else []:
        item = load(path, {}) or {}
        result.append({"unit_id": item.get("unit_id", path.stem), "number": item.get("number", ""),
                       "type": item.get("type", ""), "answer": item.get("answer", ""),
                       "difficulty": item.get("difficulty", ""), "knowledge": item.get("knowledge", []),
                       "state": "unverified", "open": [], "resolved": []})
    return result


def merged_rows(folder, report):
    """按入库单元合并：题组一行，带 numbers / subquestions / material。"""
    base = question_rows(folder, report)
    if preview_batch_rows is not None:
        try:
            rows = preview_batch_rows(folder, base)
        except (OSError, ValueError, TypeError, KeyError):
            rows = None
        if rows:
            return rows
    return base


def number_of_unit(rows):
    mapping = {}
    for row in rows:
        unit = str(row.get("unit_id") or "")
        number = str(row.get("number") or "").strip()
        if unit and number:
            mapping[unit] = number
    return mapping


def candidates_by_number(folder, *, unit_id=None):
    mapping = {}
    directory = folder / "candidates"
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        item = load(path, {}) or {}
        number = str(item.get("number") or "").strip()
        if unit_id is not None and str(item.get("unit_id") or "") != str(unit_id):
            continue
        if number.isdigit():
            mapping[number] = (item, path)
    return mapping


def _batch_units_map(folder):
    batch = load(folder / "06_batch" / "batch_meta.json") or load(folder / "06_batch" / "batch.json")
    units = (batch if isinstance(batch, list) else batch.get("units", batch.get("items", []))) if batch else []
    if not units:
        units = anomaly_views.load_preview_batch_items(folder)
    # Current source partitions take precedence over obsolete merged batches
    # for the review display; formal data is not silently rewritten.
    if isinstance(units, list) and source_group_candidates is not None:
        units = source_group_candidates(folder, units)
    mapping = {}
    for item in units if isinstance(units, list) else []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("unit_id") or item.get("id"))
        if key not in mapping:
            mapping[key] = item
    return mapping


def candidate(folder, unit_id, *, batch_map=None):
    safe = str(unit_id).replace("/", "_").replace("\\", "_")
    path = folder / "candidates" / f"{safe}.json"
    draft = load(path)
    if batch_map is None:
        batch_map = _batch_units_map(folder)
    item = batch_map.get(str(unit_id))
    if item is not None:
        if isinstance(draft, dict):
            draft_fields = {key: value for key, value in draft.items()
                            if key not in ("explanation", "articleSummary")}
            return {**item, **draft_fields}, path
        return item, None
    return (draft, path) if isinstance(draft, dict) else ({}, None)


from .preview_render import (  # noqa: E402
    AUDIO_EXTS,
    _cell_text_images,
    _clean_preview_band_title,
    _clean_preview_heading,
    _extract_preview_lesson_bands,
    _extract_preview_section_meta,
    _locate_source_docx,
    attach_media,
    audio_blocks,
    audio_map,
    paper_preview,
    paper_uses_bulk_bold,
    read_source_docx,
    source_blocks,
    source_lines,
    structured_rows,
    table_structure,
)


from .stage_views import (  # noqa: E402
    FORMAT_FIELDS,
    LABEL_FIELDS,
    RESOLVED_STATES,
    SEVERITY_ORDER,
    STAGE_KINDS,
    STAGE_ORDER,
    STAGE_TITLES,
    TERMINAL_OK,
    active_stage_ids,
    as_number,
    finding_bucket,
    findings_for,
    group_duplicate_findings,
    is_open,
    manifest_dependency_satisfied,
    mark_row,
    paper_info,
    split_findings,
    stage_cards,
)



_advance_state: dict[str, dict] = {}

# 用户要求：多份试卷上传后必须“依次排队”跑，同一时刻只允许一个批次在推进，
# 进程级 FIFO 队列 + 可配置并发度调度线程（默认 1 保持严格串行，插件端通过
# WF_MAX_CONCURRENT_BATCHES=2 启用双批并发）；_advance_state[name] 记录状态。
_advance_queue: list[str] = []
_advance_queue_lock = threading.Lock()
_advance_worker_running = False
_advance_worker_thread = None
_advance_worker_threads: list[threading.Thread] = []
_advance_worker_start_error = None


def _max_concurrent_batches() -> int:
    """Max concurrent advancing batches (1 = strict serial; capped at 4 for memory safety)."""
    raw = os.environ.get("WF_MAX_CONCURRENT_BATCHES", "1")
    try:
        value = int(str(raw).strip())
    except (ValueError, TypeError):
        value = 1
    return max(1, min(value, 4))


def _alive_advance_workers() -> list:
    """Prune dead worker threads and keep _advance_worker_thread synced (hold _advance_queue_lock)."""
    global _advance_worker_thread
    if _advance_worker_thread is None:
        _advance_worker_threads.clear()
        return []
    if _advance_worker_thread not in _advance_worker_threads:
        _advance_worker_threads[:] = [_advance_worker_thread]
    alive = [t for t in _advance_worker_threads if t is not None and t.is_alive()]
    _advance_worker_threads[:] = alive
    _advance_worker_thread = alive[0] if alive else None
    return alive


QUEUE_REMOVED = "workflow/.queue-removed"


def queue_removed(folder) -> bool:
    return (Path(folder) / QUEUE_REMOVED).is_file()


def remove_from_queue(name: str, reason: str = "") -> dict:
    """Move one batch out of 排队区 without deleting or editing its data."""
    folder = batch_dir(name)
    if _is_split_worker():
        res = _remote_worker().remove_from_queue(name, reason=reason)
        _invalidate_overview_batch(folder, name)
        return res
    with _lock:
        with _advance_queue_lock:
            _advance_queue[:] = [entry for entry in _advance_queue
                                if (entry.get("name") if isinstance(entry, dict) else entry) != name]
        state = _advance_state.get(name) or {}
        if state.get("running"):
            return {"removed": False, "error": "批次正在推进当前阶段，请等阶段结束后再移出"}
        _advance_state.pop(name, None)
        marker = folder / QUEUE_REMOVED
        marker.parent.mkdir(parents=True, exist_ok=True)
        temporary = marker.with_suffix(".tmp")
        temporary.write_text(json.dumps({"removed_at": now(), "reason": str(reason)[:300]},
                                        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(marker)
    _invalidate_overview_batch(folder, name)
    return {"removed": True, "batch": name, "batch_dir": str(folder)}


def restore_queue(name: str) -> dict:
    """Put one batch back into 排队区; data is untouched either way."""
    folder = batch_dir(name)
    marker = folder / QUEUE_REMOVED
    if marker.is_file():
        marker.unlink()
    with _lock:
        _advance_state.pop(name, None)
    _invalidate_overview_batch(folder, name)
    _wake_supervisor()
    return {"restored": True, "batch": name, "batch_dir": str(folder)}


_last_upload_order_ns = 0


def _next_upload_order_ns() -> int:
    global _last_upload_order_ns
    now_ns = time_ns()
    if now_ns <= _last_upload_order_ns:
        now_ns = _last_upload_order_ns + 1
    _last_upload_order_ns = now_ns
    return now_ns


def _batch_upload_order_ns(folder: Path, manifest: dict | None = None) -> int:
    """Stable upload-order timestamp (ns): descriptor uploaded_at_ns > manifest.created_at > folder stat."""
    desc = load(folder / 'attachments' / 'batch.json', {}) or {}
    if isinstance(desc, dict):
        raw = desc.get('uploaded_at_ns')
        if isinstance(raw, int) and raw > 0:
            return raw
    if isinstance(manifest, dict):
        dt = _parse_iso(manifest.get('created_at'))
        if dt is not None:
            return int(dt.timestamp() * 1_000_000_000)
    try:
        st = folder.stat()
        birth = getattr(st, 'st_birthtime', None)
        if birth:
            return int(birth * 1_000_000_000)
        return int(st.st_ctime_ns)
    except OSError:
        return 0


def _queue_batch_order() -> list[str]:
    """Batches still in 排队区 (not uploaded, not flow_done), earliest uploaded first.

    Serial execution must follow this order: a later batch may not start until
    every earlier queued batch is fully complete and has left the queue. Without
    this, a batch that stalls on a blocker still lets the next one run.
    """
    order = []
    if WORKFLOWS.is_symlink() or not WORKFLOWS.is_dir():
        return order
    for folder in WORKFLOWS.iterdir():
        try:
            if folder.is_symlink() or not folder.is_dir() or folder.name.startswith('.'):
                continue
            if (folder / QUEUE_REMOVED).is_file():
                continue  # 用户显式移出排队区；批次数据保留，可再点继续处理恢复
            manifest_path = folder / 'workflow' / 'manifest.json'
            if manifest_path.is_symlink() or not manifest_path.is_file():
                continue
        except OSError:
            continue
        manifest = load(manifest_path, {}) or {}
        if not isinstance(manifest, dict):
            continue
        upload = (manifest.get('stages') or {}).get('upload') or {}
        receipt = load(folder / 'receipt.json', {}) or {}
        pending = bool(isinstance(receipt, dict) and (receipt.get('remote_confirmation_required')
                       or receipt.get('stage') == 'pending_confirmation'))
        if str(upload.get('status')) == 'completed' and not pending:
            continue  # uploaded to cloud → left the queue
        try:
            if wf_execution_state is not None and wf_execution_state.local_complete(folder, manifest):
                continue  # automated flow finished → removed from 排队区
        except (OSError, ValueError, KeyError, TypeError):
            pass
        order.append((_batch_upload_order_ns(folder, manifest), str(manifest.get('created_at') or ''), folder.name))
    order.sort()
    return [name for _, _, name in order]


def _queue_head():
    order = _queue_batch_order()
    return order[0] if order else None


def _waiting_for_predecessor(name):
    """Return the blocking head name when `name` must wait beyond the concurrency window, else None."""
    order = _queue_batch_order()
    if not order:
        return None
    limit = _max_concurrent_batches()
    if name in order[:limit]:
        return None
    return order[0]


def finding_current(finding: dict, labels: dict, subs: list[dict], numbers: list) -> str:
    """把这条意见指向的字段当前值捞出来，让人不用翻页也知道在说什么。"""
    key = str(finding.get("field") or "").lower()
    number = as_number(finding.get("unit_id"))
    sub = next((s for s in subs if as_number(s.get("number")) == number), None) if number else None
    if len(numbers) > 1 and number and number not in [as_number(n) for n in numbers]:
        sub = None

    def join(value):
        if isinstance(value, (list, tuple)):
            values = [str(v).strip() for v in value if str(v).strip()]
            return "、".join(values) if values else "（空）"
        text = str(value or "").strip()
        return text if text else "（空）"

    if key in ("knowledge", "knowledgeids"):
        return join((sub or {}).get("knowledge") or labels.get("knowledge"))
    if key == "type":
        return join((sub or {}).get("type") or labels.get("type"))
    if key in ("subtype", "subtypes"):
        return join(labels.get("subtypes") or (sub or {}).get("subtype"))
    if key in ("difficulty", "difficultyvalue"):
        return join((sub or {}).get("difficulty") or labels.get("difficulty"))
    if key in ("categories", "category"):
        return join(labels.get("categories"))
    if key == "source_type":
        return join(labels.get("source_type"))
    if key == "source":
        return join(labels.get("source"))
    if key == "year":
        return join(labels.get("year"))
    if key in ("location", "region"):
        return join(labels.get("location"))
    if key in ("stem", "options", "answer"):
        text = join((sub or {}).get(key) or ("" if sub else (labels.get(key) or "")))
        return text[:160] + ("…" if len(text) > 160 else "")
    return "—"


def sync_review_decisions(folder: Path, queue=None):
    """Apply attributed queue decisions to the same finding in every gate file."""
    if queue is None:
        queue = load(folder / "07_review/manual-review-queue.json", {"items": []})
    decisions = {x.get("finding_id"): x for x in queue.get("items", [])
                 if x.get("finding_id") and x.get("decided_at") and x.get("decided_by")}
    changed = 0
    for name in ("content-review.json", "metadata-review.json"):
        path = folder / "07_review" / name
        payload = load(path, {"findings": []})
        dirty = False
        for finding in payload.get("findings", []):
            decision = decisions.get(finding.get("finding_id"))
            if not decision:
                continue
            for key in ("status", "note", "resolution_note", "decided_by", "decided_at"):
                if key in decision and finding.get(key) != decision[key]:
                    finding[key] = decision[key]
                    dirty = True
            # The queue may contain an explicitly translated explanation;
            # preserve the old source wording for traceability.
            if decision.get("reason") and finding.get("reason") != decision["reason"]:
                finding.setdefault("original_reason", finding.get("reason", ""))
                finding["reason"] = decision["reason"]
                dirty = True
        if dirty:
            save_json(path, payload)
            changed += 1
    return changed


@serialized
def apply_decision(folder: Path, item: dict, actor: str | None = None) -> dict:
    """写一条处理意见。只允许改状态/备注，原问题的字段一律保留。"""
    if not item.get("finding_id") or item.get("status") not in ("accepted", "rejected", "fixed", "hold"):
        raise ValueError("决策需要 finding_id 和合法 status")
    path = folder / "07_review" / "manual-review-queue.json"
    queue = load(path, {"queue_version": "1.0", "items": []})
    queue.setdefault("items", [])
    target = next((x for x in queue["items"] if x.get("finding_id") == item["finding_id"]), None)
    if target is None:
        # Older versions could drop queue rows while leaving gate findings.
        # Recover the actual record by ID so it can receive a logged decision.
        for name in ("content-review.json", "metadata-review.json"):
            records = load(folder / "07_review" / name, {"findings": []})
            original = next((x for x in records.get("findings", [])
                             if x.get("finding_id") == item["finding_id"]), None)
            if original:
                target = dict(original)
                queue["items"].append(target)
                break
    if target is None:
        raise ValueError(f"队列里没有 {item['finding_id']}")
    for key in ("status", "note", "resolution_note"):
        if key in item:
            target[key] = item[key]
    target.update({"decided_by": actor or "workbench", "decided_at": now()})
    decision = dict(target)
    with _lock:
        if path.exists():
            path.with_suffix(".json.bak").write_bytes(path.read_bytes())
        save_json(path, queue)
        sync_review_decisions(folder, queue)
        (folder / "workflow").mkdir(parents=True, exist_ok=True)
        with (folder / "workflow" / "decisions.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(decision, ensure_ascii=False) + "\n")
    if str(target.get("review_type") or "").lower() == "structure" and target.get("status") in ("fixed", "rejected"):
        _resolve_structure_issue(folder, target)
    return target


def _resolve_structure_issue(folder: Path, finding: dict) -> None:
    """人在工作台确认结构疑点后，按当前原卷/正文哈希绑定放行依据，供结构闸门复核。"""
    structure_path = folder / "01_source" / "structure.json"
    source_path = folder / "01_source" / "正文.txt"
    if not structure_path.is_file() or not source_path.is_file():
        raise ValueError("缺少结构/正文，无法记录结构确认")
    structure = load(structure_path, {}) or {}
    code = str(finding.get("structure_code") or "").strip()
    reason = str(finding.get("reason") or "")
    issues = structure.get("unresolved") or []
    matches = [x for x in issues if isinstance(x, dict) and x.get("code") == code]
    if len(matches) > 1:
        matches = [x for x in matches if str(x.get("message") or "") == reason]
    if not code:
        matches = [x for x in issues if isinstance(x, dict) and reason
                   and reason == str(x.get("message") or "")]
    line = finding.get("source_line")
    if line is None:
        locations = [re.fullmatch(r"正文第(\d+)行", str(ref)) for ref in finding.get("evidence", [])]
        locations = [int(match.group(1)) for match in locations if match]
        if len(set(locations)) == 1:
            line = locations[0]
    if line is not None:
        matches = [x for x in matches if x.get("line") == line]
    target = matches[0] if len(matches) == 1 else None
    if target is None:
        raise ValueError("结构疑点已不存在或定位不唯一，请刷新后重试")
    resolved = finding.get("status") == "fixed"
    target["resolved"] = resolved
    target["resolution"] = str(finding.get("resolution_note") or finding.get("note") or "工作台人工确认").strip()
    target["resolution_evidence"] = {
        "source_sha256": wf_manifest.sha256_file(source_path),
        "original_sha256": (wf_manifest.sha256_file(wf_manifest.load(folder).get("inputs", {}).get("source", {}).get("path") or "")
                            if (folder / "workflow" / "manifest.json").is_file() else ""),
        "evidence": list(finding.get("evidence") or []) or [f"工作台确认：{reason[:80]}"],
        "decided_by": finding.get("decided_by") or "workbench",
    }
    if structure_path.exists():
        structure_path.with_suffix(".json.bak").write_bytes(structure_path.read_bytes())
    save_json(structure_path, structure)


def _run_advance_now(name: str, folder: Path, *, operations_expectation=None) -> dict:
    """Execute one batch's advance pass synchronously (called by advance workers)."""
    from workflow import supervisor as wf_supervisor
    result = {"error": "后台执行器意外退出，等待监督器重试"}
    claim = (wf_supervisor.advance_claim(folder)
             if _max_concurrent_batches() <= 1
             else wf_supervisor.advance_claim(folder, include_global_agent=False))
    if not claim.__enter__():
        claim.__exit__(None, None, None)
        with _lock:
            _advance_state[name] = {"running": False, "external": True, "finished_at": now()}
        return {"started": False, "running": True, "external": True}
    if operations_expectation is not None:
        from workflow import operations
        current = operations._load(folder)
        if (operations_expectation.get("identity") != operations._identity(folder)
                or operations_expectation.get("incident_id") != (current.get("incident") or {}).get("incident_id")):
            claim.__exit__(None, None, None)
            with _lock:
                _advance_state[name] = {"running": False, "result": {"error": "stale_operations_identity"}, "finished_at": now()}
            return {"started": False, "error": "stale_operations_identity"}
    try:
        bridge = auto_flow.load_bridge()
        # The claim already owns auto.advance's per-batch lock. Do not acquire
        # it again. Remote upload/publish is never a watchdog task.
        result = auto_flow._advance_locked(folder, bridge, stop_at="upload_gate")
    except Exception as exc:  # noqa: BLE001 - background worker; surfaced to UI
        result = {"error": str(exc)}
    finally:
        try:
            try:
                from workflow import operations
                try:
                    operations.observe_execution(folder)
                except (ValueError, OSError, RuntimeError):
                    pass  # The durable outbox can reconcile on the next read.
                wf_supervisor.record_result(folder, result)
            finally:
                claim.__exit__(None, None, None)
        finally:
            # A failed ledger write must never leave a dead worker marked
            # running forever. A restart can reconstruct from the manifest.
            with _lock:
                _advance_state[name] = {"running": False, "result": result, "finished_at": now()}
    return {"started": True, "running": True}


def _advance_worker_loop():
    """FIFO worker loop supporting 1..N concurrent batches (N = _max_concurrent_batches()).

    When the queue empties the worker exits cleanly and relinquishes its slot;
    finishing a batch wakes the supervisor immediately so waiting batches start
    without a 30s tick gap.
    """
    global _advance_worker_running, _advance_worker_thread
    while True:
        # Dequeue, validate and mark running under the mutation lock so purge
        # cannot delete a popped entry before the worker registers ownership.
        with _lock:
            with _advance_queue_lock:
                if not _advance_queue:
                    current = threading.current_thread()
                    if _advance_worker_thread is None or _advance_worker_thread not in _advance_worker_threads:
                        _advance_worker_threads.clear()
                    _advance_worker_threads[:] = [
                        t for t in _advance_worker_threads
                        if t is not None and t is not current and t.is_alive()
                    ]
                    if _advance_worker_threads:
                        _advance_worker_thread = _advance_worker_threads[0]
                        _advance_worker_running = True
                    else:
                        # Relinquish ownership under the same lock as enqueue/start;
                        # a thread still unwinding its return cannot strand new work.
                        _advance_worker_thread = None
                        _advance_worker_running = False
                    return
                entry = _advance_queue.pop(0)
                name = entry["name"] if isinstance(entry, dict) else entry
                expectation = entry.get("operations_expectation") if isinstance(entry, dict) else None
            try:
                folder = batch_dir(name)
            except (ValueError, OSError):
                _advance_state.pop(name, None)
                continue
            # Deletion/removal may have cancelled the queued batch. Never
            # recreate its state or let a recovered worker revive it.
            if queue_removed(folder) or (folder / "workflow/.delete-requested").exists():
                _advance_state.pop(name, None)
                continue
            # Even a later queued batch cannot pass outside the active concurrency window.
            predecessor = _waiting_for_predecessor(name)
            if predecessor is not None:
                _advance_state[name] = {"running": False, "waiting": True,
                                        "waiting_for": predecessor, "finished_at": now()}
                continue
            _advance_state[name] = {"running": True, "started_at": now()}
        try:
            if expectation is None:
                _run_advance_now(name, folder)
            else:
                _run_advance_now(name, folder, operations_expectation=expectation)
        except Exception as exc:  # noqa: BLE001 - never kill the worker loop
            with _lock:
                _advance_state[name] = {"running": False, "result": {"error": str(exc)}, "finished_at": now()}
        finally:
            # Wake supervisor right after a batch finishes so the next waiting batch
            # in 排队区 starts immediately instead of waiting for the next 30s tick.
            _wake_supervisor()


@serialized
def _ensure_advance_worker():
    """Reconcile queued work with up to _max_concurrent_batches() live worker threads."""
    global _advance_worker_running, _advance_worker_thread, _advance_worker_start_error
    # Start while holding the queue lock: another caller cannot mistake a
    # constructed-but-not-started thread for a dead worker and over-spawn.
    # The serialized wrapper preserves the worker's _lock -> queue-lock order.
    with _advance_queue_lock:
        alive = _alive_advance_workers()
        if not _advance_queue:
            _advance_worker_running = bool(alive)
            return bool(alive)
        running_count = sum(1 for state in _advance_state.values() if state.get("running"))
        if running_count > 0 and not alive:
            # A dead thread with a recorded owner is ambiguous: expose it for
            # diagnosis, never replay it or let another batch overtake it.
            _advance_worker_running = False
            return False
        limit = _max_concurrent_batches()
        desired = min(limit, running_count + len(_advance_queue))
        while len(alive) < desired:
            idx = len(alive) + 1
            worker_name = "workbench-advance-worker" if idx == 1 else f"workbench-advance-worker-{idx}"
            worker = None
            try:
                worker = threading.Thread(target=_advance_worker_loop, daemon=True,
                                          name=worker_name)
                _advance_worker_threads.append(worker)
                if _advance_worker_thread is None:
                    _advance_worker_thread = worker
                _advance_worker_running = True
                worker.start()
                alive.append(worker)
            except Exception as exc:  # noqa: BLE001 - retain queue for the next reconciliation
                if worker is not None and worker in _advance_worker_threads:
                    _advance_worker_threads.remove(worker)
                _advance_worker_thread = alive[0] if alive else None
                _advance_worker_running = bool(alive)
                _advance_worker_start_error = str(exc)
                return bool(alive)
        _advance_worker_running = bool(alive)
        _advance_worker_start_error = None
        return bool(alive)


def advance_worker_health() -> dict:
    """Cheap queue reconciliation/health; no disk scan or task cancellation."""
    if _is_split_worker():
        snap = _remote_worker().read_state()
        adv = snap.get("advance_state") or {}
        queue = snap.get("queue") or []
        running_batches = sorted(k for k, v in adv.items() if isinstance(v, dict) and v.get("running"))
        return {
            "worker_alive": bool(snap.get("alive")),
            "active_workers": len(running_batches),
            "max_concurrent_batches": _max_concurrent_batches(),
            "running_batches": running_batches,
            "running_without_worker": False,
            "queue_length": len(queue),
            "queue_oldest_age_seconds": None,
            "worker_start_error": None,
        }
    _ensure_advance_worker()
    with _lock:
        with _advance_queue_lock:
            names = [entry.get("name") if isinstance(entry, dict) else entry
                     for entry in _advance_queue]
            ages = []
            current = datetime.now().astimezone()
            for name in names:
                try:
                    queued = datetime.fromisoformat((_advance_state.get(name) or {}).get("queued_at", ""))
                    ages.append(max(0.0, (current - queued.astimezone()).total_seconds()))
                except (ValueError, TypeError, OverflowError):
                    continue
            alive_workers = _alive_advance_workers()
            alive = bool(alive_workers)
            running = [name for name, state in _advance_state.items() if state.get("running")]
            return {"worker_alive": alive,
                    "active_workers": len(alive_workers),
                    "max_concurrent_batches": _max_concurrent_batches(),
                    "running_batches": running,
                    "running_without_worker": bool(running and not alive),
                    "queue_length": len(names),
                    "queue_oldest_age_seconds": round(max(ages), 3) if ages else None,
                    "worker_start_error": _advance_worker_start_error}


def _operations_leased() -> bool:
    """An in-flight operations turn holds a lease: restarting voids that attempt."""
    try:
        from workflow import operations
        return operations.leased(WORKFLOWS)
    except (ImportError, OSError, ValueError, RuntimeError):
        return False


def work_in_flight() -> bool:
    """是否还有真正在跑的工作；插件重启后端前用它判断能否安全回收进程树。

    只统计“杀掉就会丢失真实工作”的状态：正在执行的 advance、排队中的批次、切段
    任务、上传任务、在途运维租约。上传中断会让远端回执变成 unknown（只能人工
    核实），比阶段重跑代价更高，所以同样不能在被回收时进行。探测不到状态时保守
    返回 True。
    """
    if _is_split_worker():
        # In split-worker mode, batch advances live in workbench.worker; only
        # local API-owned uploads or audio jobs block hot-restarting the API plane.
        if any(bool((j or {}).get("running")) for j in _upload_jobs.values()):
            return True
        if bool((_audio_job or {}).get("running")):
            return True
        return False
    try:
        # 不阻塞在全局写锁上：若 _lock 正被保存/复验长时间持有，说明后端正在忙，
        # 立刻返回 True，避免 /api/health 超时被宿主误判为空闲。
        acquired = _lock.acquire(timeout=0.05)
        if not acquired:
            return True
        try:
            if any(isinstance(row, dict) and row.get("running")
                   for row in _advance_state.values()):
                return True
            if any(isinstance(job, dict) and job.get("running")
                   for job in _upload_jobs.values()):
                return True
        finally:
            _lock.release()
        if not _advance_queue_lock.acquire(timeout=0.05):
            return True
        try:
            if _advance_queue:
                return True
        finally:
            _advance_queue_lock.release()
    except (RuntimeError, TypeError):
        return True
    with _audio_job["lock"]:
        if _audio_job.get("running"):
            return True
    try:
        from workflow import ai_supplement
        if ai_supplement.has_active_jobs():
            return True
    except (ImportError, OSError, RuntimeError):
        return True
    return _operations_leased()


_ADVANCE_STAGE_LABELS = {
    "extract": "内容提取",
    "structure_gate": "结构确认",
    "preassemble": "前置组装与判型",
    "types": "题型交接",
    "audio": "听力处理",
    "answers": "答案填充",
    "explanations": "解析填充",
    "knowledge": "知识标注",
    "content_snapshot": "内容快照",
    "meta": "元信息整理",
    "underlines": "画线检查",
    "assemble": "汇总整理",
    "validate": "本地体检",
    "review": "内容审核",
    "review_gate": "审核汇总",
    "format_gate": "格式检查",
    "upload_gate": "审核上传",
}


def _advance_context_info(folder: Path) -> dict:
    """Summarize live activity and next/blocked stage for clear 继续处理 feedback."""
    info = {}
    manifest = {}
    stages = {}
    try:
        manifest = load(folder / "workflow/manifest.json", {}) or {}
        stages = manifest.get("stages") if isinstance(manifest.get("stages"), dict) else {}
    except (OSError, ValueError, TypeError):
        manifest, stages = {}, {}
    upload_done = str((stages.get("upload") or {}).get("status") or "") == "completed"
    flow_finished = bool(stages and (final_format_complete(folder, manifest) or upload_done))
    try:
        from workflow import activity as wf_activity
        act = wf_activity.read(folder)
        if act:
            stage = str(act.get("stage") or "")
            stage_status = str((stages.get(stage) or {}).get("status") or "")
            # 批次已结束或该阶段已进入完成态时，不再继续累加活动计时，防止残留 activity.json 持续计时。
            stage_already_done = stage_status in ("completed", "approved", "skipped")
            batch_ended_for_stage = flow_finished and stage not in ("upload", "upload_gate", "publish_gate")
            if stage and not stage_already_done and not batch_ended_for_stage:
                info["active_stage"] = stage
                info["active_stage_label"] = _ADVANCE_STAGE_LABELS.get(stage, stage)
                if act.get("detail"):
                    info["active_detail"] = str(act["detail"])
                started_at = act.get("started_at")
                if isinstance(started_at, str) and started_at:
                    try:
                        dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                        elapsed = int(max(0, (datetime.now(dt.tzinfo) - dt).total_seconds()))
                        info["elapsed_seconds"] = elapsed
                    except (ValueError, TypeError):
                        pass
    except (ImportError, OSError, ValueError, TypeError):
        pass
    try:
        for st_name, st_val in stages.items():
            if st_name in ("upload_gate", "upload", "publish_gate"):
                continue
            status = str((st_val or {}).get("status") or "pending")
            if status in ("failed", "blocked") and "blocked_stage" not in info:
                info["blocked_stage"] = st_name
                info["blocked_stage_label"] = _ADVANCE_STAGE_LABELS.get(st_name, st_name)
                reason = str((st_val or {}).get("error") or (st_val or {}).get("notes") or "").strip()
                if reason:
                    info["blocked_reason"] = reason[:200]
            if status not in ("completed", "approved", "skipped") and "next_stage" not in info:
                info["next_stage"] = st_name
                info["next_stage_label"] = _ADVANCE_STAGE_LABELS.get(st_name, st_name)
    except (OSError, ValueError, TypeError):
        pass
    return info


def _cleanup_stale_activity(folder: Path) -> bool:
    """Remove workflow/activity.json left behind by a dead PID (display-only cleanup)."""
    path = folder / "workflow/activity.json"
    try:
        if not path.is_file() or path.is_symlink() or path.parent.is_symlink():
            return False
        from workflow import activity as wf_activity
        if not wf_activity.read(folder):
            path.unlink(missing_ok=True)
            return True
    except (ImportError, OSError, ValueError, TypeError):
        pass
    return False


@serialized
def _launch_advance(name: str, folder: Path, *, prepare=None, operations_expectation=None,
                    reset_budget=False, **details) -> dict:
    """Enqueue one batch; only an explicit user action resets retry accounting."""
    from workflow import supervisor as wf_supervisor
    if operations_expectation is not None:
        from workflow import operations
        if (operations_expectation.get("identity") != operations._identity(folder)
                or operations_expectation.get("incident_id") != (operations._load(folder).get("incident") or {}).get("incident_id")):
            return {"started": False, "error": "stale_operations_identity"}
    # A batch the user explicitly removed must not be restarted by the supervisor
    # or operations loop. Only an explicit 继续处理 (reset_budget) restores it.
    if (folder / "workflow/.delete-requested").exists() or not folder.is_dir():
        return {"started": False, "removed": True, "reason": "批次已删除或正在删除"}
    if not reset_budget and (folder / QUEUE_REMOVED).is_file():
        return {"started": False, "removed": True,
                "reason": "批次已由用户移出排队区；点继续处理才会恢复"}
    ctx_info = _advance_context_info(folder)
    # Strict queue serialization: only the earliest unfinished batch may start.
    # A later batch waits until every earlier one is complete and out of 排队区.
    predecessor = _waiting_for_predecessor(name)
    if predecessor is not None:
        with _lock:
            _advance_state[name] = {"running": False, "waiting": True,
                                    "waiting_for": predecessor, "updated_at": now()}
        return {"started": False, "waiting": True, "waiting_for": predecessor, **ctx_info,
                "reason": f"需等待前一份试卷「{predecessor}」全部完成并移出排队区后再开始"}
    if _is_advance_running(name):
        return {"started": False, "running": True, **ctx_info}
    with _advance_queue_lock:
        q_names = [(entry.get("name") if isinstance(entry, dict) else entry) for entry in _advance_queue]
        queued = name in q_names
        q_pos = (q_names.index(name) + 1) if queued else None
    if queued:
        _ensure_advance_worker()
        _wake_supervisor()
        return {"started": False, "running": True, "queued": True, "queue_position": q_pos, **ctx_info}
    cleared_cooldown = False
    cleared_stale_activity = False
    # Preparation writes must participate in the same lock/lease as execution.
    claim_cm = (wf_supervisor.advance_claim(folder)
                if _max_concurrent_batches() <= 1
                else wf_supervisor.advance_claim(folder, include_global_agent=False))
    with claim_cm as acquired:
        if not acquired:
            return {"started": False, "running": True, "external": True, **ctx_info}
        if operations_expectation is not None:
            from workflow import operations
            if (operations_expectation.get("identity") != operations._identity(folder)
                    or operations_expectation.get("incident_id") != (operations._load(folder).get("incident") or {}).get("incident_id")):
                return {"started": False, "error": "stale_operations_identity"}
        if reset_budget:
            cleared_stale_activity = _cleanup_stale_activity(folder)
            try:
                sup_before = load(folder / "workflow/supervisor.json", {}) or {}
                if sup_before.get("status") in {"exhausted", "waiting_external", "scheduled", "cooldown"} or float(sup_before.get("next_retry_at") or 0) > time():
                    cleared_cooldown = True
                wf_supervisor.clear_exhausted(folder)
            except (OSError, ValueError, TypeError):
                pass
            if ctx_info.get("blocked_stage"):
                try:
                    from workflow import operations
                    operations.observe_execution(folder)
                except (ImportError, OSError, ValueError, RuntimeError):
                    pass
        if prepare is not None:
            details.update(prepare())
    if _is_split_worker():
        if reset_budget:
            _invalidate_overview_batch(folder, name)
        return _remote_worker().enqueue(
            name,
            reset_budget=reset_budget,
            operations_expectation=operations_expectation,
            details=details,
        )
    with _lock:
        _advance_state[name] = {"running": False, "queued": True, "queued_at": now(), **details}
    with _advance_queue_lock:
        _advance_queue.append({"name": name, "operations_expectation": operations_expectation}
                              if operations_expectation is not None else name)
        q_pos = len(_advance_queue)
    if reset_budget:
        _invalidate_overview_batch(folder, name)
    _ensure_advance_worker()
    _wake_supervisor()
    return {
        "started": True, "queued": True, "running": False,
        "queue_position": q_pos,
        "cleared_cooldown": cleared_cooldown,
        "cleared_stale_activity": cleared_stale_activity,
        **ctx_info,
    }


@serialized
def start_advance(name: str, *, reset_budget=True) -> dict:
    """Run one local pass; automatic callers preserve durable retry accounting."""
    if _is_split_worker():
        folder = batch_dir(name)
        if reset_budget and (folder / QUEUE_REMOVED).is_file():
            restore_queue(name)
        return _remote_worker().enqueue(name, reset_budget=reset_budget)
    if auto_flow is None:
        raise ValueError("流水线模块不可用")
    folder = batch_dir(name)
    if reset_budget and (folder / QUEUE_REMOVED).is_file():
        # 用户点“继续处理”即显式恢复排队；自动调度不得自行恢复。
        restore_queue(name)
    return _launch_advance(name, folder, reset_budget=reset_budget)


def _dispatch_supervised(name):
    try:
        if queue_removed(batch_dir(name)):
            return {"started": False, "removed": True,
                    "reason": "批次已由用户移出排队区；点继续处理才会恢复"}
    except (ValueError, OSError):
        pass  # 目录不可用时交给 start_advance 给出真实错误，不伪装成已移出
    # HTTP overview reads may hold this lock while inspecting many media files.
    # The watchdog must remain bounded and never queue behind such a request.
    if not _lock.acquire(blocking=False):
        return {"started": False, "running": False, "busy": True}
    try:
        # The watchdog already decided this batch needs work and committed its
        # retry reservation. Enqueueing must NOT clear that budget (that would
        # reset no_progress_attempts and defeat exhaustion); only an explicit
        # user action may reset it.
        return start_advance(name, reset_budget=False)
    finally:
        _lock.release()


def advance_state(name: str) -> dict:
    if _is_split_worker():
        state = _remote_worker().advance_state(name)
    else:
        state = dict(_advance_state.get(name) or {"running": False})
    try:
        folder = batch_dir(name)
        path = folder / "workflow/supervisor.json"
        ledger = load(path, {}) if not path.is_symlink() and not path.parent.is_symlink() else {}
        if isinstance(ledger, dict) and ledger:
            state["supervisor"] = {key: ledger[key] for key in
                ("status", "next_retry_at", "no_progress_attempts", "escalation", "last_result") if key in ledger}
        path = folder / "workflow/self-heal-status.json"
        healing = load(path, {}) if not path.is_symlink() and not path.parent.is_symlink() else {}
        if isinstance(healing, dict) and healing:
            # Do not surface a stale "refused/failed" heal card after the failing
            # stage has since completed on a later retry — it is not an active 卡点.
            healed_stage = healing.get("stage")
            manifest = load(folder / "workflow/manifest.json", {})
            stage_status = str(((manifest.get("stages") or {}).get(healed_stage) or {}).get("status") or "")
            if stage_status not in ("completed", "approved", "skipped"):
                state["self_heal"] = {key: healing[key] for key in
                    ("status", "stage", "next_retry_at", "attempt", "verified", "action", "reason", "error_code", "has_feedback") if key in healing}
        # active_stage/elapsed_seconds only exist while workflow/activity.json is present.
        # Skipping _advance_context_info for idle batches avoids parsing manifest.json
        # and upload-format.json across 100+ batches on every list poll.
        if (folder / "workflow" / "activity.json").is_file():
            ctx_info = _advance_context_info(folder)
            if ctx_info.get("active_stage"):
                for k in ("active_stage", "active_stage_label", "active_detail", "elapsed_seconds"):
                    if k in ctx_info:
                        state[k] = ctx_info[k]
    except ValueError:
        pass
    return state


# 重新标签 / 重新审核：从指定阶段起让其后全部失效，再后台推进到停住。
# - tag    （重新标签）：从「题型与子类 types」起，重跑题型/解析/知识点/组装/体检/审核全链路。
# - review （重新审核）：从「审核 review」起，只重跑两轮审核（一审全量 + 二审抽检）到审核结束。
RERUN_START = {"tag": "types", "review": "review"}
RERUN_LABEL = {"tag": "重新标签", "review": "重新审核"}


@serialized
def start_rerun(name: str, mode: str) -> dict:
    """An explicit user rerun invalidates only after owning the execution lock."""
    if auto_flow is None or wf_manifest is None:
        raise ValueError("流水线模块不可用")
    if mode not in RERUN_START:
        raise ValueError("不支持的重新执行类型")
    folder = batch_dir(name)
    start_stage = RERUN_START[mode]
    prepared = {}

    def prepare():
        manifest = wf_manifest.load(folder)
        changed = wf_manifest.invalidate_downstream(folder, manifest, start_stage, RERUN_LABEL[mode])
        wf_manifest.save(folder, manifest)
        prepared.update(mode=mode, invalidated=changed)
        return prepared

    result = _launch_advance(name, folder, prepare=prepare, reset_budget=True)
    if result.get("started"):
        result.update(prepared, **{"from": start_stage})
    return result


ALLOWED_MATERIAL_ROLES = (
    "paper", "answers", "explanations", "listening_script",
    "mix", "structure_reference", "other",
)


def _batch_document_entries(folder: Path) -> tuple[dict, list[dict]]:
    """汇总批次内所有上传文档（去重），返回 (descriptor, entries)。"""
    def _load_json_safe(path: Path) -> dict:
        try:
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            pass
        return {}

    desc_path = folder / "attachments" / "batch.json"
    descriptor = _load_json_safe(desc_path)
    manifest = wf_manifest.load(folder) if wf_manifest else {}
    roles_data = _load_json_safe(folder / "01_source" / "material-roles.json")
    classified_by_name = {}
    classified_by_input = {}
    for row in roles_data.get("documents") or []:
        if not isinstance(row, dict):
            continue
        if row.get("path"):
            classified_by_name[Path(str(row["path"])).name] = row
        if row.get("input"):
            classified_by_input[str(row["input"])] = row
    raw_user_roles = descriptor.get("user_roles") if isinstance(descriptor.get("user_roles"), dict) else {}
    orig_map = descriptor.get("original_filenames") if isinstance(descriptor.get("original_filenames"), dict) else {}
    ordered: list[tuple[str, str, str]] = []  # (input_key, saved_name, original_filename)
    seen_files: set[str] = set()

    def _add(input_key: str, saved_name: str, orig_name: str = ""):
        if not saved_name:
            return
        fname = Path(str(saved_name)).name
        if Path(fname).suffix.lower() not in DOC_SUFFIXES or fname in seen_files:
            return
        seen_files.add(fname)
        ordered.append((input_key, fname, str(orig_name or fname)))

    if descriptor.get("source"):
        _add("source", descriptor["source"], orig_map.get("source") or descriptor["source"])
    if descriptor.get("answers"):
        _add("answers", descriptor["answers"], orig_map.get("answers") or descriptor["answers"])
    for idx, row in enumerate(descriptor.get("extra_documents") or [], 1):
        if isinstance(row, dict) and row.get("path"):
            _add(f"attachment_{idx:03d}", row["path"], row.get("original_filename") or row["path"])
    for key, entry in (manifest.get("inputs") or {}).items():
        if key == "docx" or not isinstance(entry, dict):
            continue
        p = str(entry.get("path") or "")
        if p:
            _add(str(key), p, entry.get("original_filename") or Path(p).name)
    for row in roles_data.get("documents") or []:
        if isinstance(row, dict) and row.get("path"):
            _add(str(row.get("input") or ""), str(row["path"]), row.get("original_filename") or Path(str(row["path"])).name)

    entries = []
    for input_key, saved_name, orig_name in ordered:
        crow = classified_by_name.get(saved_name) or classified_by_input.get(input_key) or {}
        detected_role = str(crow.get("role") or "")
        detected_source = str(crow.get("source") or "")
        user_role = str(raw_user_roles.get(saved_name) or raw_user_roles.get(input_key) or raw_user_roles.get(orig_name) or "").strip()
        if user_role not in ALLOWED_MATERIAL_ROLES:
            user_role = ""
        entries.append({
            "file": saved_name,
            "input": str(crow.get("input") or input_key),
            "original_filename": str(crow.get("original_filename") or orig_name or saved_name),
            "detected_role": detected_role,
            "detected_source": detected_source,
            "user_role": user_role,
            "effective_role": user_role or detected_role or "other",
            "chars": crow.get("chars"),
            "error": crow.get("error") or "",
        })
    return descriptor, entries


def get_material_roles(name: str) -> dict:
    """返回批次下全部上传文档的当前识别角色与用户显式角色。"""
    folder = batch_dir(name)
    descriptor, entries = _batch_document_entries(folder)
    raw_roles = descriptor.get("user_roles") if isinstance(descriptor.get("user_roles"), dict) else {}
    return {
        "ok": True,
        "name": name,
        "documents": entries,
        "user_roles": {str(k): str(v) for k, v in raw_roles.items() if str(v) in ALLOWED_MATERIAL_ROLES},
        "allowed_roles": list(ALLOWED_MATERIAL_ROLES),
    }


@serialized
def set_material_roles(name: str, roles: dict, rerun: bool = False) -> dict:
    """保存用户显式指定的材料角色，并可选从原卷提取阶段起重新执行。"""
    if not isinstance(roles, dict):
        raise ValueError("roles 参数必须为对象")
    folder = batch_dir(name)
    descriptor, entries = _batch_document_entries(folder)
    alias_to_file: dict[str, str] = {}
    for row in entries:
        fname = row["file"]
        alias_to_file[fname] = fname
        if row.get("input"):
            alias_to_file[row["input"]] = fname
        if row.get("original_filename"):
            alias_to_file[row["original_filename"]] = fname
    user_roles = dict(descriptor.get("user_roles") or {}) if isinstance(descriptor.get("user_roles"), dict) else {}
    for raw_key, raw_val in roles.items():
        key_str = str(raw_key or "").strip()
        if not key_str:
            continue
        target = alias_to_file.get(key_str, key_str)
        val = str(raw_val or "").strip()
        if not val or val == "auto":
            user_roles.pop(target, None)
            user_roles.pop(key_str, None)
            continue
        if val not in ALLOWED_MATERIAL_ROLES:
            raise ValueError(f"不支持的材料角色：{val}")
        user_roles[target] = val
    descriptor["user_roles"] = user_roles
    desc_path = folder / "attachments" / "batch.json"
    desc_path.parent.mkdir(parents=True, exist_ok=True)
    save_json(desc_path, descriptor)

    rerun_result = None
    if rerun:
        if auto_flow is None or wf_manifest is None:
            raise ValueError("流水线模块不可用")
        prepared = {}

        def prepare():
            manifest = wf_manifest.load(folder)
            changed = wf_manifest.invalidate_downstream(
                folder, manifest, "extract", "用户调整材料角色后重跑提取",
            )
            wf_manifest.save(folder, manifest)
            prepared.update(mode="extract", invalidated=changed)
            return prepared

        rerun_result = _launch_advance(name, folder, prepare=prepare, reset_budget=True)
        if rerun_result.get("started"):
            rerun_result.update(prepared, **{"from": "extract"})

    payload = get_material_roles(name)
    if rerun_result is not None:
        payload["rerun"] = rerun_result
    return payload


def _slug(text, fallback: str = "新试卷") -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "", str(text or "")).strip(".")
    return cleaned[:40] or fallback


def _unique_batch_name(base: str) -> str:
    name, index = base, 2
    while (WORKFLOWS / name).exists() or (INBOX / name).exists():
        name, index = f"{base}-{index}", index + 1
    return name


def _upload_part_sha(part):
    return hashlib.sha256(part.get("data") or b"").hexdigest() if isinstance(part, dict) else ""


def _batch_already_sent(state):
    """True once a batch reached the cloud upload stage (confirmed or pending)."""
    if not isinstance(state, dict):
        return False
    stages = state.get("stages")
    if not isinstance(stages, dict):
        return False
    upload = stages.get("upload")
    status = str((upload or {}).get("status") or "") if isinstance(upload, dict) else ""
    return status in ("completed", "approved")


def _existing_upload_batch(source, answer, audio, extra_docs, extra_audio):
    """Find an existing batch with the exact same uploaded content.

    A repeated upload must not create ``-2``/``-3`` copies of the same paper;
    that multiplies model runs and review queues. Only an exact content match
    (all meaningful source/answer/audio/extra inputs) reuses an existing batch.
    Different source or attachments still create a new batch.
    """
    if wf_manifest is None or WORKFLOWS.is_symlink() or not WORKFLOWS.is_dir():
        return None
    def signature(part):
        return (_upload_part_sha(part), Path(part["filename"]).suffix.lower()) if part else None

    expected = {
        "source": signature(source),
        "answers": signature(answer),
        "audio": signature(audio),
        "attachment": sorted(signature(item) for item in (extra_docs or [])),
        "audio_extra": sorted(signature(item) for item in (extra_audio or [])),
    }
    for folder in sorted(WORKFLOWS.iterdir()):
        if folder.is_symlink() or not folder.is_dir():
            continue
        folder = folder.resolve()
        manifest_path = folder / "workflow" / "manifest.json"
        if manifest_path.parent.is_symlink() or manifest_path.is_symlink():
            continue
        state = load(manifest_path, {})
        inputs = state.get("inputs") if isinstance(state, dict) else None
        if not isinstance(inputs, dict):
            continue
        # 已经发过（已提交云端，含待人工确认）的试卷不再参与去重比对或复用：
        # 用户明确要求不校验已发试卷，重新上传应当作为新任务出现，而不是复用
        # 一个在列表里被隐藏的批次（否则看起来“批次任务不见了”）。
        if _batch_already_sent(state):
            continue
        source_record = inputs.get("source")
        if not isinstance(source_record, dict) or source_record.get("sha256") != expected["source"][0]:
            continue

        def current_signature(record):
            if record is None:
                return None
            if not isinstance(record, dict) or not isinstance(record.get("path"), str):
                raise ValueError("Invalid upload input")
            path = Path(record["path"])
            if not path.is_absolute():
                path = folder / path
            relative = path.relative_to(folder)
            current = folder
            for part in relative.parts:
                current = current / part
                if part == ".." or current.is_symlink():
                    raise ValueError("Unsafe upload input")
            if not path.is_file():
                raise ValueError("Missing upload input")
            # Check the bytes now, not a potentially stale manifest/cache digest.
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1 << 20), b""):
                    digest.update(chunk)
            if digest.hexdigest() != record.get("sha256"):
                raise ValueError("Changed upload input")
            return digest.hexdigest(), path.suffix.lower()

        try:
            actual = {key: current_signature(inputs.get(key)) for key in ("source", "answers", "audio")}
            actual["attachment"] = sorted(current_signature(item) for key, item in inputs.items()
                if re.fullmatch(r"attachment_\d{3,}", str(key)))
            actual["audio_extra"] = sorted(current_signature(item) for key, item in inputs.items()
                if re.fullmatch(r"audio_\d{3,}", str(key)))
            if actual == expected:
                # Completed legacy extraction may predate mandatory PDF OCR.
                # Decline reuse without changing or rerunning the old batch;
                # pending/running tasks retain their existing deduplication.
                extraction = ((state.get("stages") or {}).get("extract") or {})
                if extraction.get("status") == "completed":
                    pdf_inputs = [record for record in inputs.values()
                                  if isinstance(record, dict)
                                  and Path(str(record.get("path") or "")).suffix.lower() == ".pdf"]
                    if pdf_inputs:
                        from workflow import extract
                        report = load(folder / "01_source" / "pdf-ocr.json", {})
                        usable = True
                        for record in pdf_inputs:
                            path = Path(record["path"]).expanduser()
                            if not path.is_absolute():
                                path = folder / path
                            acquired, _ = extract._pdf_ocr_for_source(folder, path, report)
                            pages = (acquired or {}).get("pages") or []
                            if (not any(page["text"].strip() for page in pages)
                                    or any(page.get("method") == "text" for page in pages)):
                                usable = False
                                break
                        if not usable:
                            continue
                # Ensure the reused response can load the ledger as well.
                wf_manifest.load(folder)
                return folder
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return None


def _save_part(folder: Path, part: dict, stem: str) -> Path:
    path = folder / f"{stem}{Path(part['filename']).suffix.lower()}"
    path.write_bytes(part["data"])
    return path


@serialized
def handle_upload(fields: dict, files: list[dict]) -> dict:
    """上传试卷 → 建收件箱条目 → 初始化批次 → 后台推进流水线。"""
    if auto_flow is None:
        raise ValueError("流水线模块不可用，无法自动建批次")
    docs = [f for f in files if Path(f["filename"]).suffix.lower() in DOC_SUFFIXES]
    if not docs:
        raise ValueError("请至少上传一个试卷文件（仅支持 .docx 或 .pdf）")
    unsupported = [f["filename"] for f in files
                   if Path(f["filename"]).suffix.lower() not in DOC_SUFFIXES + AUDIO_SUFFIXES]
    if unsupported:
        raise ValueError("不支持的附件：" + "、".join(unsupported) + "；请使用 DOCX、PDF 或音频，未保存任何文件")
    raw_file_roles = {}
    file_roles_text = str(fields.get("file_roles") or "").strip()
    if file_roles_text:
        try:
            parsed_roles = json.loads(file_roles_text)
            if isinstance(parsed_roles, dict):
                raw_file_roles = parsed_roles
        except ValueError as exc:
            raise ValueError("file_roles 格式无效，应为 JSON 对象") from exc
    explicit_by_id = {}
    for idx, item in enumerate(files):
        if item not in docs:
            continue
        chosen_role = ""
        for key in (Path(item["filename"]).name, item["filename"], str(idx)):
            val = str(raw_file_roles.get(key) or "").strip()
            if val and val != "auto":
                if val not in ALLOWED_MATERIAL_ROLES:
                    raise ValueError(f"不支持的材料角色：{val}")
                chosen_role = val
                break
        if chosen_role:
            explicit_by_id[id(item)] = chosen_role
    audio_files = [f for f in files if Path(f["filename"]).suffix.lower() in AUDIO_SUFFIXES]
    audio = audio_files[0] if audio_files else None
    extra_audio = audio_files[1:]
    explicit_answers = [f for f in docs if explicit_by_id.get(id(f)) in ("answers", "explanations")]
    answer_candidates = explicit_answers or [
        f for f in docs
        if not explicit_by_id.get(id(f)) and re.search(r"答案|answer|解析", Path(f["filename"]).stem, re.I)
    ]
    answer = answer_candidates[0] if answer_candidates else None
    main_pool = [f for f in docs if f is not answer]
    explicit_papers = [f for f in main_pool if explicit_by_id.get(id(f)) in ("paper", "mix")]
    if explicit_papers:
        main_candidates = explicit_papers
    else:
        non_aux = [f for f in main_pool if explicit_by_id.get(id(f)) not in ("listening_script", "structure_reference", "other")]
        main_candidates = non_aux or main_pool
    # 来源优先级（用户 2026-11-11 确认）：Word 原卷 > 带文字层 PDF > 扫描件。
    # 同一批上传同时含 Word 与 PDF 时，Word 一定是提取主源，PDF 只作版面核对。
    source = (wf_source_policy.select_primary(main_candidates)
              if wf_source_policy is not None else
              next((f for f in main_candidates if Path(f['filename']).suffix.lower() == '.docx'),
                   main_candidates[0] if main_candidates else docs[0]))
    if answer is source:
        answer = None
    # Filename hints are provisional only. All unhinted documents remain
    # attachments until extract classifies their actual content; a second file
    # of the same format is not evidence that it is an answer booklet.
    year = str(fields.get("year") or "").strip()
    region = str(fields.get("region") or "").strip()
    title = str(fields.get("title") or "").strip()
    subject = str(fields.get("subject") or "english").strip() or "english"
    source_type = str(fields.get("source_type") or "").strip()
    grade = str(fields.get("grade") or "").strip()
    if source_type and source_type not in taxonomy.sources():
        raise ValueError("题源分类不在系统目录中")
    if grade and grade not in ("七年级", "八年级", "九年级"):
        raise ValueError("年级只能选择七年级、八年级或九年级")
    # 同内容重复上传直接复用已有批次，避免生成 -2/-3 副本并重复跑模型。
    existing = _existing_upload_batch(
        source, answer, audio,
        [f for f in docs if f is not source and f is not answer],
        extra_audio)
    if existing is not None:
        state = wf_manifest.load(existing) if wf_manifest else {}
        try:
            existing_uploaded = bool(overview(existing.name).get("uploaded"))
        except (ValueError, OSError, KeyError, TypeError):
            existing_uploaded = str(((state.get("stages") or {}).get("upload") or {}).get("status")) == "completed"
        # A reused batch that is already uploaded is hidden from both the queue
        # and the review list, so a silent reuse looks like the task vanished.
        message = ("该试卷已上传完成，无需重复上传（批次：" + existing.name + "）"
                   if existing_uploaded else
                   "该试卷已存在，已定位到已有任务（批次：" + existing.name + "）")
        return {"name": existing.name, "duplicate": True, "reused": True,
                "uploaded": existing_uploaded, "message": message,
                "inbox": "", "paper": paper_info(existing, {}, state),
                "stage": stage_cards(state), "files": []}
    # 未填写标题时保留用户上传文件的识别线索，不再一律变成「新试卷」。
    # 此名称仅用于展示/定位，正式来源仍由原卷卷头识别，不覆盖原文证据。
    name = _unique_batch_name(_slug(title or Path(source["filename"]).stem or f"{region}{year}"))
    batch_folder = WORKFLOWS / name
    batch_folder.mkdir(parents=True, exist_ok=False)
    folder = batch_folder / "attachments"
    folder.mkdir()
    saved_source = _save_part(folder, source, "source")
    saved_answer = _save_part(folder, answer, "answers") if answer else None
    saved_audio = _save_part(folder, audio, "audio") if audio else None
    saved_extra_audio = [_save_part(folder, item, f"audio_{index:03d}")
                         for index, item in enumerate(extra_audio, 1)]
    extra_docs = [f for f in docs if f is not source and f is not answer]
    saved_extras = [_save_part(folder, item, f"attachment_{index:03d}")
                    for index, item in enumerate(extra_docs, 1)]
    user_roles = {}
    if explicit_by_id.get(id(source)):
        user_roles[saved_source.name] = explicit_by_id[id(source)]
    if answer and saved_answer and explicit_by_id.get(id(answer)):
        user_roles[saved_answer.name] = explicit_by_id[id(answer)]
    for path, item in zip(saved_extras, extra_docs):
        if explicit_by_id.get(id(item)):
            user_roles[path.name] = explicit_by_id[id(item)]
    descriptor = {"workflow_id": name, "subject": subject, "title": title or None,
                  "uploaded_at_ns": _next_upload_order_ns(),
                  "year": int(year) if year.isdigit() else None, "region": region or None,
                  "source_type": source_type or None, "grade": grade or None,
                  "source": saved_source.name, "answers": saved_answer.name if saved_answer else None,
                  "audio": saved_audio.name if saved_audio else None,
                  "extra_audio": [{"path": path.name, "original_filename": Path(item['filename']).name}
                                  for path, item in zip(saved_extra_audio, extra_audio)],
                  "extra_documents": [{"path": path.name, "original_filename": Path(item['filename']).name}
                                      for path, item in zip(saved_extras, extra_docs)],
                  "original_filenames": {"source": Path(source["filename"]).name,
                                         "answers": Path(answer["filename"]).name if answer else None,
                                         "audio": Path(audio["filename"]).name if audio else None}}
    if user_roles:
        descriptor["user_roles"] = user_roles
    save_json(folder / "batch.json", descriptor)
    candidate = {"name": name, "folder": str(folder), "subject": subject,
                 "title": descriptor["title"], "year": descriptor["year"], "region": descriptor["region"],
                 "source_type": descriptor["source_type"], "grade": descriptor["grade"],
                 "source": str(saved_source), "answers": str(saved_answer) if saved_answer else None,
                 "audio": str(saved_audio) if saved_audio else None,
                 "extra_documents": [str(path) for path in saved_extras],
                 "extra_audio": [str(path) for path in saved_extra_audio]}
    batch_folder = auto_flow.ensure_batch(candidate, WORKFLOWS)
    start_advance(batch_folder.name)
    _wake_supervisor()
    manifest = wf_manifest.load(batch_folder) if wf_manifest else {}
    try:
        inbox_rel = str(folder.relative_to(ROOT))
    except ValueError:
        inbox_rel = str(folder)
    source_notice = (wf_source_policy.upload_notice(source, [f for f in docs if f is not source])
                     if wf_source_policy is not None else "")
    adv_state = advance_state(batch_folder.name)
    return {"name": batch_folder.name, "inbox": inbox_rel,
            "paper": paper_info(batch_folder, {}, manifest),
            "stage": stage_cards(manifest, False, bool(adv_state.get("running")), None, batch_folder=batch_folder),
            "advance": adv_state,
            "flow_done": False, "uploaded": False, "queue_removed": False,
            "created_at": manifest.get("created_at"),
            "updated_at": manifest.get("updated_at"),
            "queue_order_ts": _batch_upload_order_ns(batch_folder, manifest),
            "source_notice": source_notice,
            "source_kind": wf_source_policy.kind(source) if wf_source_policy is not None else None,
            "files": [path.name for path in (saved_source, saved_answer, saved_audio) if path]
                     + [path.name for path in saved_extras + saved_extra_audio]}


def _archive_preflight(path: Path):
    """归档只移动真实目录；拒绝所有软链接，避免递归读取/恢复越界。"""
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"归档目标不是本地真实目录：{path.name}")
    for current, dirs, files in os.walk(path, followlinks=False):
        if any((Path(current) / name).is_symlink() for name in dirs + files):
            raise ValueError(f"目录含软链接，请先人工检查：{path.name}")


@serialized
def archive_batch(name: str, confirm_name: str) -> dict:
    from workflow import supervisor as wf_supervisor
    folder = batch_dir(name)
    with wf_supervisor._try_file_lock(wf_supervisor._auto_lock_path(folder)) as acquired:
        if not acquired or wf_supervisor.execution_busy(folder, include_auto=False, include_global_agent=False):
            raise ValueError("批次正在运行或执行运维动作，请稍后归档")
        return _archive_batch_locked(name, confirm_name)


def _archive_batch_locked(name: str, confirm_name: str) -> dict:
    """可恢复删除：原子重命名到工作区回收目录，不永久删除任何数据。"""
    if confirm_name != name:
        raise ValueError("确认批次名不匹配，请重新确认目标")
    folder = batch_dir(name)
    _archive_preflight(folder)
    manifest = load(folder / "workflow" / "manifest.json", {}) or {}
    if advance_state(name).get("running") or manifest.get("status") == "running" or any(
            stage.get("status") == "running" for stage in (manifest.get("stages") or {}).values()):
        raise ValueError("批次正在运行，请等待任务结束后再删除")
    # 数据根以 workflows/inbox 的共同父目录为准，而不是代码根 ROOT。
    # 插件版把代码放在 app/、数据放在插件目录（WF_WORKFLOWS_DIR/WF_INBOX_DIR 指过去），
    # 此时 ROOT 是 app/ 而数据根是插件目录，用 ROOT 当边界会把每次删除都误判成越界。
    root = WORKFLOWS.resolve().parent
    if INBOX.is_symlink() or INBOX.resolve().parent != root:
        raise ValueError("批次或收件箱根目录越出工作区")
    inbox = INBOX / name
    targets = [(folder, "workflows")]
    if inbox.is_symlink():
        raise ValueError("收件箱目录不能是软链接")
    if inbox.exists():
        targets.append((inbox.resolve(), "inbox"))
    for target, _ in targets:
        _archive_preflight(target)
    trash = root / ".workbench-trash"
    if trash.is_symlink() or trash.resolve().parent != root:
        raise ValueError("回收目录不能是软链接或越出工作区")
    trash.mkdir(exist_ok=True)
    archive = trash / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex)
    archive.mkdir()
    receipt = {"batch": name, "archived_at": now(), "status": "preparing",
               "original_paths": {kind: str(path.relative_to(root)) for path, kind in targets}}
    save_json(archive / "receipt.json", receipt)
    moved = []
    try:
        for source, kind in targets:
            source.rename(archive / kind)
            moved.append((source, archive / kind))
        receipt["status"] = "archived"
        save_json(archive / "receipt.json", receipt)
    except OSError:
        # 第二个移动失败时尽量回滚，收据仍保留供人工恢复。
        for original, archived in reversed(moved):
            if not original.exists():
                archived.rename(original)
        receipt["status"] = "rolled_back"
        save_json(archive / "receipt.json", receipt)
        raise
    _advance_state.pop(name, None)
    return {"ok": True, "name": name, "archive": str(archive.relative_to(root)),
            "recoverable": True}


_RECYCLE_DIRS = (".workbench-trash", ".trash", "archives", "archive")
# 「继续处理」区删除：请求批次在阶段边界停下后，最多等待多少秒让 worker 释放执行锁。
DELETE_STOP_WAIT_SECONDS = 90.0


def _purge_archive_targets(root: Path, name: str) -> list[Path]:
    """Recognize receipt archives and legacy named/nested workflows/inbox copies.

    Never use a substring match: deleting paper must preserve paper-2. A receipt
    for another batch bounds that container, even if it contains a matching name.
    """
    targets = []

    def visit(directory):
        for entry in sorted(directory.iterdir()):
            if entry.is_symlink():
                if entry.name == name:
                    raise ValueError("归档目录不能是软链接")
                continue
            if not entry.is_dir():
                continue
            receipt_path = entry / "receipt.json"
            if receipt_path.is_symlink():
                raise ValueError("归档回执不能是软链接")
            receipt = load(receipt_path, {})
            identity = receipt.get("batch") if isinstance(receipt, dict) else None
            if identity:
                if identity == name:
                    targets.append(entry)
                continue
            # workflows/inbox directly under a recycle root are aggregate
            # layout containers, even when a batch happens to have that name.
            aggregate = entry.name in ("workflows", "inbox") and directory.name in _RECYCLE_DIRS
            if entry.name == name and not aggregate:
                targets.append(entry)
                continue
            # A legacy archive may keep a batch under a timestamp or workflow ID.
            manifest_path = entry / "workflow" / "manifest.json"
            if not manifest_path.parent.is_symlink() and not manifest_path.is_symlink():
                state = load(manifest_path, {})
                if isinstance(state, dict) and state.get("workflow_id") == name:
                    targets.append(entry)
                    continue
                if manifest_path.is_file():
                    continue  # Do not descend into another batch's own artifacts.
            visit(entry)

    for base in (root, WORKFLOWS, INBOX):
        for directory in _RECYCLE_DIRS:
            trash = base / directory
            if trash.is_symlink():
                raise ValueError("回收目录不能是软链接")
            if trash.is_dir():
                visit(trash)
    return targets


def _purge_audio_targets(name):
    """Return only this batch's status files, never shared audio/model caches."""
    from audio import config as cfgmod, bootstrap as boot
    data_dir = Path(cfgmod.data_dir(cfgmod.load()))
    batch_status = boot.status_path(data_dir, name)
    paths = [batch_status, batch_status.with_suffix(".tmp")]
    global_status = boot.status_path(data_dir)
    global_paths = [global_status, global_status.with_suffix(".tmp")]
    # Reject symlinked status paths before reading their identity.
    for path in paths + global_paths:
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError("听力状态路径不能是软链接")
    for path in global_paths:
        state = load(path, {})
        if isinstance(state, dict) and state.get("batch") == name:
            paths.append(path)
    for path in paths:
        state = load(path, {})
        if isinstance(state, dict) and state.get("running"):
            raise ValueError("批次听力任务正在运行，请等待结束后再删除")
    return [path for path in paths if path.is_file()]


def _clear_purged_batch_state(name):
    _advance_state.pop(name, None)
    _upload_jobs.pop(name, None)
    with _audio_job["lock"]:
        if _audio_job.get("batch") == name:
            audio_lock = _audio_job["lock"]
            _audio_job.clear()
            _audio_job.update(running=False, kind="", batch="", lock=audio_lock)
    with _advance_queue_lock:
        _advance_queue[:] = [entry for entry in _advance_queue
                            if (entry.get("name") if isinstance(entry, dict) else entry) != name]
    # These small caches include question content, not just derived counters.
    # Clear opaque hash-keyed caches too, since they cannot be filtered by path.
    _OVERVIEW_CACHE.clear()
    _REVIEW_SNAPSHOT_CACHE.clear()
    _UNITS_BUNDLE_CACHE.clear()
    _WORK_STATE_CACHE.clear()
    try:
        folder = WORKFLOWS / name
        disk_cache = _units_bundle_disk_path(folder)
        if disk_cache.is_file():
            disk_cache.unlink()
    except OSError:
        pass
    for module_name, attributes in {
        "workflow.manifest": ("_FILE_DIGEST_CACHE",),
        "workflow.review": ("_LOAD_CACHE", "_DIGEST_CACHE", "_PLAN_CACHE",
                            "_IMMUTABLE_DIGEST_CACHE", "_EVALUATE_CACHE"),
        "workflow.agent_tasks": ("_TASK_TEMPLATE_CACHE",),
    }.items():
        module = sys.modules.get(module_name)
        if module is not None:
            for attribute in attributes:
                cache = getattr(module, attribute, None)
                if cache is not None:
                    cache.clear()


def _cancel_running_batch(name: str, folder: Path, wait_seconds: float = 90.0) -> dict:
    """Request a clean stop of a running/queued advance before deletion.

    Writes the batch-scoped cancel marker (see ``auto.request_cancel``), removes
    the batch from the pending advance queue, and — if this batch owns the audio
    job — stops the audio worker. Then waits a bounded time for the worker to
    release execution at the next stage boundary. It never interrupts a stage
    mid-flight; a stage that does not honor the marker keeps the batch protected
    and the caller gets ``stopped=False`` so nothing is deleted under a live run.
    """
    if _is_split_worker():
        return _remote_worker().cancel_batch(name, wait_seconds=wait_seconds)
    import time as _time
    from workflow import auto as wf_auto
    try:
        wf_auto.request_cancel(folder)
    except OSError:
        pass
    with _advance_queue_lock:
        _advance_queue[:] = [entry for entry in _advance_queue
                             if (entry.get("name") if isinstance(entry, dict) else entry) != name]
    with _lock:
        state = _advance_state.get(name) or {}
        was_running = bool(state.get("running"))
        was_queued = bool(state.get("queued") or state.get("waiting"))
        # 排队/等待中的批次并未真正执行：移除队列项后直接清状态即可立即删除。
        if was_queued and not was_running:
            _advance_state.pop(name, None)
            return {"stopped": True, "was_running": False, "was_queued": True}
        if not was_running:
            return {"stopped": True, "was_running": False}
    deadline = _time.monotonic() + max(0.0, wait_seconds)
    while _time.monotonic() < deadline:
        with _lock:
            state = _advance_state.get(name) or {}
        if not state.get("running"):
            return {"stopped": True, "was_running": True}
        _time.sleep(0.4)
    with _lock:
        still = _advance_state.get(name) or {}
    return {"stopped": not still.get("running"), "was_running": True}


@serialized
def delete_batch(name: str, confirm_name: str) -> dict:
    """Stop (if running/queued) and permanently delete one batch and all its traces.

    This is the entry used by the 「继续处理」area delete button: unlike a bare
    ``purge`` it first asks the running worker to stop at a clean stage boundary,
    then reuses ``purge_batch`` (which also clears the per-batch caches and shared
    in-memory projections). If the batch cannot be stopped in time we refuse and
    keep the data intact rather than deleting under a live run.
    """
    if confirm_name != name:
        raise ValueError("确认批次名不匹配，请重新确认目标")
    _validate_batch_name(name)
    try:
        folder = batch_dir(name)
    except ValueError:
        # No live directory (archive-only): nothing to stop, let purge handle it.
        return purge_batch(name, confirm_name)
    stop = _cancel_running_batch(name, folder, wait_seconds=DELETE_STOP_WAIT_SECONDS)
    if not stop.get("stopped"):
        raise ValueError("批次正在执行且未能在时限内停到阶段边界；为避免删除运行中的批次，已取消删除。请稍后重试或等待当前阶段结束。")
    return purge_batch(name, confirm_name)


@serialized
def purge_batch(name: str, confirm_name: str) -> dict:
    """Permanently delete one batch and every local trace of it.

    Unlike ``archive_batch`` this is not recoverable: it removes the live
    workflows/inbox directories plus any recycle archives for the same batch,
    then clears in-memory scheduling state. The caller must explicitly confirm
    the exact batch name.
    """
    if confirm_name != name:
        raise ValueError("确认批次名不匹配，请重新确认目标")
    _validate_batch_name(name)
    if WORKFLOWS.is_symlink():
        raise ValueError("批次根目录不能是软链接")
    folder = WORKFLOWS.resolve() / name
    if folder.exists() or folder.is_symlink():
        _archive_preflight(folder)
    root = WORKFLOWS.resolve().parent
    if INBOX.is_symlink() or INBOX.resolve().parent != root or INBOX.resolve() == WORKFLOWS.resolve():
        raise ValueError("收件箱根目录越出工作区")
    if name in _RECYCLE_DIRS:
        raise ValueError("拒绝删除共享回收目录")
    from workflow import ai_supplement, supervisor as wf_supervisor
    with wf_supervisor.advance_claim(folder, include_global_agent=False) as acquired:
        if not acquired:
            raise ValueError("批次正在运行或执行运维动作，请稍后删除")
        if (_is_advance_running(name)
                or (_upload_jobs.get(name) or {}).get("running")
                or (_audio_job.get("running") and _audio_job.get("batch") == name)
                or ai_supplement.is_running(folder)):
            raise ValueError("批次正在运行，请等待任务结束后再删除")
        targets = [path for path in (folder, INBOX / name) if path.exists() or path.is_symlink()]
        targets.extend(_purge_archive_targets(root, name))
        if not targets:
            raise ValueError("批次不存在")
        for target in targets:
            resolved = target.resolve()
            if resolved == root or root not in resolved.parents:
                raise ValueError("拒绝删除工作区之外的路径")
            _archive_preflight(target)
            manifest = load(target / "workflow" / "manifest.json", {}) or {}
            stages = manifest.get("stages") if isinstance(manifest, dict) else None
            stages = stages if isinstance(stages, dict) else {}
            if isinstance(manifest, dict) and (manifest.get("status") == "running" or any(
                    isinstance(stage, dict) and stage.get("status") == "running"
                    for stage in stages.values())):
                raise ValueError("批次正在运行，请等待任务结束后再删除")
        audio_targets = _purge_audio_targets(name)
        # Preflight every target before the first irreversible removal. The
        # native host must erase its bound persisted sessions before these
        # directories disappear; its acknowledgement bypasses this write lock.
        from workflow import content_agent
        content_agent.purge_batch(folder)
        removed = []
        unique = set(targets)
        for target in sorted(unique, key=lambda path: len(path.parts)):
            if any(parent in unique for parent in target.parents):
                continue
            shutil.rmtree(target)
            removed.append(str(target.relative_to(root)))
        for path in audio_targets:
            path.unlink()
        _clear_purged_batch_state(name)
        # Empty recycle shells are not batch data; prune only the known roots.
        for base in (root, WORKFLOWS, INBOX):
            for directory in _RECYCLE_DIRS:
                trash = base / directory
                try:
                    if trash.is_dir() and not trash.is_symlink() and not any(trash.iterdir()):
                        trash.rmdir()
                except OSError:
                    pass
        return {"ok": True, "name": name, "purged": True, "removed": removed}


MAX_JSON_BYTES = 2 * 1024 * 1024        # 2 MiB
MAX_UPLOAD_BYTES = 200 * 1024 * 1024    # 200 MiB


class PayloadTooLargeError(ValueError):
    """Raised when an incoming HTTP request exceeds allowed size limits."""


def parse_multipart(handler) -> tuple[dict, list[dict]]:
    try:
        length = int(handler.headers.get("Content-Length", "0") or "0")
    except ValueError as exc:
        raise ValueError("无效的 Content-Length") from exc
    if length < 0:
        raise ValueError("无效的 Content-Length")
    if length > MAX_UPLOAD_BYTES:
        raise PayloadTooLargeError(f"上传体积超过上限（{MAX_UPLOAD_BYTES // (1024 * 1024)} MB）")
    body = handler.rfile.read(length) if length else b""
    content_type = handler.headers.get("Content-Type", "")
    if "multipart/form-data" not in content_type.lower():
        raise ValueError("上传需要 multipart/form-data")
    message = BytesParser(policy=email_policy).parsebytes(
        b"Content-Type: " + content_type.encode("utf-8", "replace") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
    if not message.is_multipart():
        raise ValueError("上传内容无法解析")
    fields, files = {}, []
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition") or ""
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename:
            files.append({"field": name, "filename": Path(filename).name, "data": payload})
        else:
            charset = part.get_content_charset() or "utf-8"
            fields[name] = payload.decode(charset, "replace")
    return fields, files


_REVIEW_SNAPSHOT_CACHE = {}
_REVIEW_SNAPSHOT_LOCKS_GUARD = threading.Lock()
_REVIEW_SNAPSHOT_LOCKS: dict[str, threading.Lock] = {}


def _folder_lock(table: dict[str, threading.Lock], key: str) -> threading.Lock:
    with _REVIEW_SNAPSHOT_LOCKS_GUARD:
        lock = table.get(key)
        if lock is None:
            lock = threading.Lock()
            if len(table) > 256:
                table.clear()
            table[key] = lock
        return lock


def _review_inputs_key(folder):
    """Project the evaluator's declared dependencies and retained evidence.

    Hash contents rather than mtime/size: same-length replacements and restored
    timestamps must not leave a page showing the previous review generation.
    """
    import hashlib
    from workflow import review
    paths = {folder / relative for relative in review.ROUND_INPUTS}
    paths.update(folder / relative for relative in (
        "07_review/review-rounds.json", "07_review/manual-review-queue.json",
        "07_review/review-gate.json", "07_review/report.json",
        "07_review/content-review.json", "07_review/metadata-review.json",
        "workflow/manifest.json", "workflow/media-observations.json",
        "02_audio/SKIP_LISTENING", "06_batch/batch.json", "06_batch/batch_meta.json"))
    rule_root = Path(review.__file__).resolve().parents[1]
    paths.update(rule_root / relative for relative in review.ROUND_RULES)
    paths.add(rule_root / "prompts/roles/review.md")
    paths.update(rule_root / "workflow" / name for name in (
        "review.py", "review_consensus.py", "review_rounds.py", "agent_tasks.py"))
    # Verification also consumes frozen bindings, responses and generation state.
    # Candidates are recursive: unit editor drafts live in candidates/units/.
    for directory in (folder / "07_review", folder / "workflow"):
        paths.update(directory.glob("*.json"))
    for relative in ("candidates", "01_source/materials", "02_audio/sources",
                     "workflow/review-transactions", "workflow/review-bindings", "workflow/review-results",
                     "workflow/review-attempts", "workflow/review-partitions",
                     "workflow/review-generations", "workflow/review-history",
                     "workflow/review-reuse"):
        directory = folder / relative
        if directory.is_dir():
            paths.update(p for p in directory.rglob("*")
                         if p.is_file() and p.suffix in (".json", ".txt")
                         and not (relative == "workflow/review-attempts" and p.name == "input.txt"))
    observations = load(folder / "workflow/media-observations.json", {}) or {}
    for item in observations.get("observations", []):
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            target = (folder / item["path"]).resolve()
            if target.is_relative_to(folder.resolve()):
                paths.add(target)
    parts = []
    for path in sorted(paths):
        try:
            parts.append((str(path), hashlib.sha256(path.read_bytes()).hexdigest()))
        except OSError:
            parts.append((str(path), "missing"))
    # An evaluator replacement in tests/hot reload must not reuse old projections.
    parts.append(("evaluator", id(review.evaluate_rounds)))
    return hashlib.sha256(repr(parts).encode("utf-8")).hexdigest()


def review_snapshot(folder):
    """只读投影真实分轮记录；不以旧 report.json 或流程状态冒充审核证据。"""
    import copy
    folder_key = str(folder)
    key = (folder_key, _review_inputs_key(folder))
    hit = _cache_get_lru(_REVIEW_SNAPSHOT_CACHE, key)
    if hit is not None:
        return copy.deepcopy(hit)
    # Single-flight per batch so concurrent detail() + units_bundle() requests
    # never run duplicate 500ms review.evaluate_rounds() computations under the GIL.
    with _folder_lock(_REVIEW_SNAPSHOT_LOCKS, folder_key):
        hit = _cache_get_lru(_REVIEW_SNAPSHOT_CACHE, key)
        if hit is not None:
            return copy.deepcopy(hit)
        value = _review_snapshot_uncached(folder)
        _cache_put_lru(_REVIEW_SNAPSHOT_CACHE, key, value, _REVIEW_SNAPSHOT_CACHE_MAX)
        return copy.deepcopy(value)


def _review_snapshot_uncached(folder):
    from workflow import review
    payload = load(folder / "07_review/review-rounds.json", {}) or {}
    evaluated = review.evaluate_rounds(folder, payload)
    checked = set(evaluated.get("checked_rounds", []))
    records = {r.get("round_id"): r for r in payload.get("rounds", [])
               if isinstance(r, dict) and r.get("round_id") in checked}
    plan = evaluated.get("plan") or {}
    rounds = []
    for rid, name in (("labels", "一审"),):
        checks = records.get(rid, {}).get("checks", [])
        historical = [r for r in payload.get("rounds", [])
                      if isinstance(r, dict) and r.get("round_id") == rid
                      and r.get("execution_id") and isinstance(r.get("checks"), list)
                      and r["checks"]]
        previous_checks = [check for record in historical for check in record["checks"]]
        current = rid in checked
        rounds.append({"round_id": rid, "name": name, "completed": current,
                       "state": "completed" if current else "needs_refresh" if previous_checks else "not_started",
                       "historical_reviewed": len(previous_checks),
                       "reviewed": len(checks),
                       "ok": sum(c.get("verdict") == "ok" for c in checks),
                       "scope": "全量单元" if rid == "labels" else "抽查小题"})
    revision = []
    for rel in ("workflow/manifest.json", "07_review/review-rounds.json",
                "07_review/manual-review-queue.json", "07_review/report.json"):
        try:
            info = (folder / rel).stat()
            revision.append(f"{rel}:{info.st_mtime_ns}:{info.st_size}")
        except OSError:
            revision.append(rel + ":missing")
    queue_open = 0
    for item in (load(folder / "07_review/manual-review-queue.json", {}) or {}).get("items") or []:
        if isinstance(item, dict) and str(item.get("status")) == "open":
            queue_open += 1
    return {"execution_completed": bool(evaluated.get("execution_completed")),
            "quality_passed": bool(evaluated.get("passed")), "rounds": rounds,
            "question_total": len(plan.get("questions", [])),
            # 人工要判断的是活动队列里的全部未裁决差异：历史轮次恢复回来的差异
            # 不在当前两轮 plan 里，只看 evaluated.findings 会少算，卡片数字就对不上。
            "open_findings": queue_open or len(evaluated.get("findings", [])),
            "revision": "|".join(revision)}, records


def _row_subtype(row):
    """行上现有的子类（subtypes 列表优先，其次单值），空串表示没有。"""
    subtypes = row.get("subtypes")
    if isinstance(subtypes, list):
        for item in subtypes:
            text = str(item or "").strip()
            if text:
                return text
    return str(row.get("subtype") or "").strip()


def decorate_review_rows(folder, rows, *, snapshot=None, source_text=None, is_sync_practice=None):
    """把真实分轮结果与“跳过听力”口径落到行上；总览与单元详情共用。"""
    if snapshot is not None:
        review_status, records = snapshot
    else:
        try:
            review_status, records = review_snapshot(folder)
        except (ImportError, OSError, ValueError, TypeError, KeyError):
            review_status, records = {}, {}
    skip_listening = (folder / "02_audio" / "SKIP_LISTENING").is_file()
    # 审核页要直接展示目录题型（语音题/单词拼写/阅读理解…），不能让人对着题干猜。
    # 只有题型/子类确实缺失时，才按同步练习册的确定性口径从本行大题标题反查一次；
    # 已判定过的题型（批次 question_type / 人工改过的标签）一律不覆盖。
    if any(not str(row.get("type") or "").strip()
           or (str(row.get("type") or "").strip() and not _row_subtype(row)) for row in rows):
        try:
            from workflow import sync_practice
            if is_sync_practice is None:
                text = source_text if source_text is not None else "\n".join(source_lines(folder))
                is_sync_practice = sync_practice.looks_like_sync_practice(text)
            if is_sync_practice:
                for row in rows:
                    title = str((row.get("source_section") or {}).get("title") or "")
                    hit = sync_practice.classify(title) if title else None
                    if not hit:
                        continue
                    current = str(row.get("type") or "").strip()
                    changed = False
                    if not current and hit.get("type"):
                        row["type"] = hit["type"]
                        row["type_from"] = "section_rule"
                        current = hit["type"]
                        changed = True
                    if current == hit.get("type") and hit.get("subtype") and not _row_subtype(row):
                        row["subtype"] = hit["subtype"]
                        changed = True
                    if changed:
                        row["subtypes"] = [row["subtype"]] if row.get("subtype") else []
        except (ImportError, OSError, ValueError, TypeError, KeyError):
            pass
    for row in rows:
        if not isinstance(row.get("subtypes"), list):
            row["subtypes"] = [row["subtype"]] if row.get("subtype") else []
    for row in rows:
        checks = [c for record in records.values() for c in record.get("checks", [])
                  if str(c.get("unit_id")) == str(row.get("unit_id"))]
        covered = [rid for rid, record in records.items()
                   if any(str(c.get("unit_id")) == str(row.get("unit_id")) for c in record.get("checks", []))]
        row["rounds"] = len(covered)
        row["review_rounds"] = covered
        if checks:
            row["verdict"] = ("error" if any(c.get("verdict") == "error" for c in checks)
                                else "uncertain" if any(c.get("verdict") == "uncertain" for c in checks) else "ok")
            row["state"] = "attention" if row.get("needs_human") or row["verdict"] != "ok" else "ok"
        # 按规则跳过的听力单元不参与语义审核与完整性判定，不能显示成“未核对”或“待审核”。
        if skip_listening and str(row.get("type") or "").startswith("听力"):
            row["excluded"] = True
            row["excluded_reason"] = "按规则跳过听力，不参与审核与完整性判定"
            row["state"] = "excluded"
            row["needs_human"] = False
            row["review_rounds"] = row.get("review_rounds") or []
    return review_status


def final_format_complete(folder, manifest):
    """Use the same real-format completion rule as background supervision."""
    if wf_execution_state is None:
        return False
    return wf_execution_state.local_complete(Path(folder), manifest)


_OVERVIEW_CACHE = {}

# 冷投影一个批次要 0.5-1s（来源正文、审核轮次、逐单元摘要），13 个批次就是
# 8-9s，逼近前端 15s 的请求上限；而列表内容只在批次文件变化时才变。把按内容
# 指纹校验的投影快照存到进程外，重启后首屏直接复用，指纹不符的批次照常重算。
_OVERVIEW_SNAPSHOT = None
_OVERVIEW_SNAPSHOT_DIRTY = False
_CACHE_GUARD = threading.Lock()
_OVERVIEW_CACHE_MAX = 512
_REVIEW_SNAPSHOT_CACHE_MAX = 512
_UNITS_BUNDLE_CACHE_MAX = 64
_DETAIL_CTX_CACHE_MAX = 32


def _cache_get_lru(cache: dict, key):
    with _CACHE_GUARD:
        value = cache.get(key)
        if value is not None:
            cache.pop(key, None)
            cache[key] = value
        return value


def _cache_put_lru(cache: dict, key, value, max_size: int) -> None:
    with _CACHE_GUARD:
        cache.pop(key, None)
        cache[key] = value
        while len(cache) > max_size:
            oldest = next(iter(cache), None)
            if oldest is None:
                break
            cache.pop(oldest, None)


def _invalidate_overview_batch(folder: Path, name: str) -> None:
    """Evict a single batch's overview cache entry without thrashing unrelated batches."""
    global _OVERVIEW_SNAPSHOT_DIRTY
    folder_str = str(folder)
    with _CACHE_GUARD:
        stale_keys = [k for k in _OVERVIEW_CACHE if isinstance(k, tuple) and k and k[0] == folder_str]
        for k in stale_keys:
            _OVERVIEW_CACHE.pop(k, None)
        if isinstance(_OVERVIEW_SNAPSHOT, dict):
            _OVERVIEW_SNAPSHOT.pop(name, None)
        _OVERVIEW_SNAPSHOT_DIRTY = True


def _overview_snapshot_path():
    base = os.environ.get("WF_CACHE_DIR")
    if base:
        root = Path(base)
    else:
        local = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
        root = (Path(local) if local else Path.home() / ".cache") / "md-upload-tiku"
    return root / "overview-snapshot.json"


def _load_overview_snapshot():
    global _OVERVIEW_SNAPSHOT
    if _OVERVIEW_SNAPSHOT is not None:
        return _OVERVIEW_SNAPSHOT
    items = {}
    try:
        raw = json.loads(_overview_snapshot_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = None
    if isinstance(raw, dict) and raw.get("version") == 1 and isinstance(raw.get("batches"), dict):
        items = raw["batches"]
    _OVERVIEW_SNAPSHOT = items
    return _OVERVIEW_SNAPSHOT


def _save_overview_snapshot(*, only_if_dirty: bool = False):
    """Persist this workflows dir's projections; merge with existing snapshot so entries aren't lost."""
    global _OVERVIEW_SNAPSHOT, _OVERVIEW_SNAPSHOT_DIRTY
    path = _overview_snapshot_path()
    if only_if_dirty and not _OVERVIEW_SNAPSHOT_DIRTY and path.is_file():
        return
    items = {}
    workflows_root = WORKFLOWS.resolve()
    existing = _OVERVIEW_SNAPSHOT if isinstance(_OVERVIEW_SNAPSHOT, dict) else {}
    for bname, entry in list(existing.items()):
        if not isinstance(entry, dict):
            continue
        try:
            ep = Path(entry.get("folder") or "")
            if ep.parent.resolve() != workflows_root or not ep.is_dir():
                continue
        except (OSError, TypeError):
            continue
        items[bname] = entry
    with _CACHE_GUARD:
        cache_entries = list(_OVERVIEW_CACHE.items())
    for (folder, key), value in cache_entries:
        try:
            ep = Path(folder)
            if ep.parent.resolve() != workflows_root:
                continue
        except (OSError, TypeError):
            continue
        items[ep.name] = {"folder": str(ep), "key": key, "value": value}
    if not items:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"version": 1, "batches": items}, ensure_ascii=False)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        return
    _OVERVIEW_SNAPSHOT = items
    _OVERVIEW_SNAPSHOT_DIRTY = False



def _overview_key(folder):
    """Fingerprint the files overview reads so a cached projection is never stale."""
    import hashlib
    parts = []
    for relative in ("workflow/manifest.json", "07_review/report.json",
                     "07_review/manual-review-queue.json", "07_review/review-rounds.json",
                     "07_review/content-review.json", "07_review/metadata-review.json",
                     "07_review/unchecked.json", "06_batch/batch_meta.json", "06_batch/batch.json",
                     "06_batch/batch.base.json", "06_batch/question-draft.json", "03_types/types.json",
                     "01_source/structure.confirmed.json",
                     "07_review/extraction-report.json", "07_review/structure-gate.json",
                     "07_review/validation-report.json", "07_review/overall-review-report.json",
                     "07_review/upload-format.json", "07_review/review-gate.json",
                     "07_review/unit-anomalies.json",
                     "02_audio/audio_map.json",
                     "receipt.json", "07_upload/receipt.json", "07_upload/preflight.json"):
        path = folder / relative
        try:
            stat = path.stat()
            parts.append((relative, stat.st_mtime_ns, stat.st_size))
        except OSError:
            parts.append((relative, "missing"))
    # 升级后须重算旧投影，包含从入口迁出的耗时计算。
    for filename in ("server.py", "batch_overview.py", "anomaly_views.py"):
        try:
            code = Path(__file__).with_name(filename).stat()
            parts.append((filename, code.st_mtime_ns, code.st_size))
        except OSError:
            parts.append((filename, "unknown"))
    audio = folder / "02_audio" / "SKIP_LISTENING"
    parts.append(("skip_listening", audio.is_file()))
    parts.append(("queue_removed", (folder / QUEUE_REMOVED).is_file()))
    attempts_dir = folder / "07_upload" / "attempts"
    if attempts_dir.is_dir():
        attempt_parts = []
        try:
            for p in sorted(attempts_dir.iterdir()):
                if p.suffix != ".json":
                    continue
                try:
                    st = p.stat()
                    attempt_parts.append((p.name, st.st_mtime_ns, st.st_size))
                except OSError:
                    attempt_parts.append((p.name, "missing"))
        except OSError:
            pass
        parts.append(("upload_attempts", tuple(attempt_parts)))
    raw = repr(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


_LIVE_OVERVIEW_CTX = threading.local()


def _call_live_overview_matched(value, name, folder):
    prev = getattr(_LIVE_OVERVIEW_CTX, "matched_folder", None)
    _LIVE_OVERVIEW_CTX.matched_folder = str(folder)
    try:
        return _live_overview(value, name, folder)
    finally:
        _LIVE_OVERVIEW_CTX.matched_folder = prev


def overview(name):
    global _OVERVIEW_SNAPSHOT_DIRTY
    folder = batch_dir(name)
    key = (str(folder), _overview_key(folder))
    hit = _cache_get_lru(_OVERVIEW_CACHE, key)
    if hit is not None:
        return _call_live_overview_matched(hit, name, folder)
    saved = _load_overview_snapshot().get(name)
    saved_folder = saved.get("folder") if isinstance(saved, dict) else None
    same_folder = bool(saved_folder and (saved_folder == str(folder) or Path(saved_folder).resolve() == folder.resolve()))
    if (isinstance(saved, dict) and same_folder
            and saved.get("key") == key[1] and isinstance(saved.get("value"), dict)):
        # Same content fingerprint as when the snapshot was written: reuse it.
        _cache_put_lru(_OVERVIEW_CACHE, key, saved["value"], _OVERVIEW_CACHE_MAX)
        return _call_live_overview_matched(saved["value"], name, folder)
    value = _overview_uncached(name, folder)
    _cache_put_lru(_OVERVIEW_CACHE, key, value, _OVERVIEW_CACHE_MAX)
    _OVERVIEW_SNAPSHOT_DIRTY = True
    return _call_live_overview_matched(value, name, folder)


def _live_overview(value, name, folder):
    """Overlay runtime ownership on the cached document projection, without mutation."""
    from datetime import timezone
    from workflow import activity
    value = dict(value)
    advance = advance_state(name)
    value["advance"] = advance
    # Idle fast-path: when _overview_key() matched every disk file (manifest,
    # upload-format, batch_meta, receipts, attempts, queue_removed) and there is
    # no live advance/upload/audio/activity worker, reuse cached stage/upload/
    # flow_done fields instead of re-reading & parsing 4+ JSONs per batch on
    # every 2s list poll across 100+ idle batches.
    if getattr(_LIVE_OVERVIEW_CTX, "matched_folder", None) == str(folder):
        with _lock:
            has_upload_job = name in _upload_jobs
        if (not has_upload_job
                and not (advance.get("running") or advance.get("queued") or advance.get("waiting"))
                and not (folder / "workflow" / "activity.json").is_file()
                and not _audio_has_live_worker(name)
                and isinstance(value.get("stage"), dict) and value["stage"].get("items")
                and isinstance(value.get("upload_status"), dict)
                and "flow_done" in value):
            value["observed_at"] = datetime.now(timezone.utc).isoformat()
            return value
    upload = upload_state(name, include_unreviewed=False)
    value["upload_status"] = upload
    manifest = load(folder / "workflow/manifest.json", {}) or {}
    work = activity.read(folder)
    audio = {}
    if ((manifest.get("inputs") or {}).get("audio") or {}).get("path"):
        try:
            if _audio_has_live_worker(name):
                audio = audio_status(name)  # A leftover heartbeat alone is not live work.
        except (OSError, ValueError, TypeError, ImportError):
            audio = {}
    # Fast path for idle cached batches: _overview_key() already fingerprints
    # manifest.json and every stage report, so when no live worker/activity/audio
    # is running we can reuse the cached stage cards instead of re-reading 15+
    # report JSONs per batch on every 2.5s list_batches() poll.
    cached_stage = value.get("stage")
    if (not work and not advance.get("running") and not audio.get("running")
            and isinstance(cached_stage, dict) and cached_stage.get("items")):
        stage = dict(cached_stage)
        stage["items"] = [dict(card) for card in stage.get("items") or []]
    else:
        stage = (stage_cards(manifest, bool(value.get("rows")), bool(advance.get("running")), value.get("diagnostics"),
                             batch_folder=folder, runtime_activity=work, audio_state=audio)
                 if manifest.get("stages") else dict(cached_stage or {}))
        stage["items"] = [dict(card) for card in stage.get("items") or []]
        if not manifest.get("stages"):
            from workflow.substeps import project as project_substeps
            projected = project_substeps(manifest, folder, work, audio)
            for card in stage["items"]:
                card["substeps"] = projected.get(card.get("id"), [])
    if audio.get("running") and not audio.get("stale"):
        for card in stage["items"]:
            if card.get("id") == "audio" and card.get("status") not in TERMINAL_OK:
                card["active"] = True
    if work:
        matched_active = False
        for card in stage["items"]:
            if card.get("id") == work["stage"] and card.get("status") not in TERMINAL_OK:
                card.update(active=True, activity_detail=work.get("detail", ""))
                stage["current"] = card
                stage["hint"] = work.get("detail") or "工作台正在处理"
                matched_active = True
        if matched_active:
            stage["activity"] = work
    if (stage.get("current") or {}).get("id") == "review_gate" and value.get("open_findings"):
        stage["hint"] = f"审核已执行；{value['open_findings']} 项标签差异待人工决定"
    stage["running_unknown"] = bool((manifest.get("status") == "running"
                                    or any(c.get("status") == "running" for c in stage["items"]))
                                    and not advance.get("running") and not work)
    value["stage"] = stage
    # 始终用最新 manifest / upload_status 刷新顶层完成态，避免 stage 已完成而 flow_done/status 仍停在旧缓存。
    if manifest.get("stages"):
        upload_stage_done = str(((manifest.get("stages") or {}).get("upload") or {}).get("status") or "") == "completed"
        receipt = (upload or {}).get("receipt") or load(folder / "receipt.json", {})
        upload_pending = bool(isinstance(receipt, dict) and (receipt.get("remote_confirmation_required")
                              or receipt.get("stage") == "pending_confirmation"))
        value["status"] = manifest.get("status", value.get("status", "unknown"))
        value["flow_done"] = bool(final_format_complete(folder, manifest) or upload_stage_done)
        value["uploaded"] = bool(upload_stage_done and not upload_pending)
        value["upload_pending_confirmation"] = upload_pending
        flow_started_at, flow_finished_at, flow_minutes = _flow_timing(manifest)
        value["flow_started_at"] = flow_started_at
        value["flow_finished_at"] = flow_finished_at
        value["flow_minutes"] = flow_minutes
        if manifest.get("updated_at"):
            value["updated_at"] = manifest.get("updated_at")
    adv_busy = bool(advance.get("running") or advance.get("queued") or advance.get("waiting") or work)
    raw_stage_blocked = any(
        isinstance(rec, dict)
        and sid not in ("upload_gate", "upload", "publish_gate")
        and not (sid == "review_gate" and rec.get("status") == "needs_review")
        and str(rec.get("status") or "") in ("failed", "blocked")
        for sid, rec in ((manifest.get("stages") or {}).items())
    )
    if adv_busy:
        value["stage_blocked"] = False
    elif raw_stage_blocked:
        value["stage_blocked"] = True
        value["has_anomaly"] = True
        if not value.get("anomaly_count"):
            value["anomaly_count"] = 1
    value["queue_removed"] = queue_removed(folder)
    value["observed_at"] = datetime.now(timezone.utc).isoformat()
    return value


def _overview_uncached(name, folder):
    report, manifest, queue = report_for(folder)
    base = question_rows(folder, report)
    number_map = number_of_unit(base)
    merged = merged_rows(folder, report)
    scoped_rows = any(str(r.get("identity_version") or "") == "scoped-v1" for r in merged)
    rows = [mark_row(row, findings_for(queue, row.get("numbers") or [row.get("number")], number_map,
                                       unit_id=row.get("unit_id"), scoped=scoped_rows))
            for row in merged]
    review_status = decorate_review_rows(folder, rows)
    skip_listening = (folder / "02_audio" / "SKIP_LISTENING").is_file()
    counts = {s: sum(1 for r in rows if r.get("state") == s)
              for s in ("ok", "attention", "blocked", "unverified", "excluded")}
    review_status = dict(review_status, skip_listening=skip_listening,
                         excluded_units=counts.get("excluded", 0)) if review_status else review_status
    open_items = [x for x in (queue.get("items", []) if isinstance(queue, dict) else [])
                  if isinstance(x, dict) and is_open(x)]
    label_open = [x for x in open_items if finding_bucket(x) == "label"]
    format_open = [x for x in open_items if finding_bucket(x) == "format"]
    try:
        media = media_audit.audit(folder, [n for row in rows for n in (row.get("numbers") or [])])
        media_summary = media["summary"]
    except (OSError, ValueError, KeyError):
        media_summary = {"total": 0, "suspicious": 0, "question": 0, "unused": 0,
                         "header": 0, "banner": 0, "excluded": 0}
    stages = [{"id": k, "status": v.get("status"), "notes": v.get("notes", ""), "kind": v.get("kind")}
              for k, v in (manifest.get("stages") or {}).items()]
    audio_input = (manifest.get("inputs") or {}).get("audio") or {}
    try:
        has_audio = bool(audio_input.get("path") and Path(str(audio_input["path"])).expanduser().is_file())
    except (OSError, ValueError, TypeError):
        has_audio = False
    batch_findings = [x for x in open_items if not str(x.get("unit_id") or "").strip()]
    advance = advance_state(name)
    # 统一卡点诊断（只读视图）：把各阶段真实原因+修复命令汇到一处。
    diagnostics = None
    try:
        from workflow import diagnose as _diagnose
        diagnostics = _diagnose.diagnose(folder)
    except (OSError, ValueError, KeyError, ImportError):
        diagnostics = None
    anomaly_info = anomaly_views.decorate_anomaly_rows(
        folder, rows, manifest=manifest, diagnostics=diagnostics, advance=advance
    )
    counts["anomaly"] = anomaly_info["anomaly_unit_count"]
    # 内容审核之后仍须完成最终格式检查，随后才移出上传队列。
    flow_done = final_format_complete(folder, manifest)
    upload = upload_state(name, include_unreviewed=False)
    upload_stage_done = str(((manifest.get("stages") or {}).get("upload") or {}).get("status") or "") == "completed"
    flow_done = flow_done or upload_stage_done  # 已提交远端的历史批次不因新增本地步骤重新排队。
    receipt = upload.get("receipt") or load(folder / "receipt.json", {})
    upload_pending = bool(isinstance(receipt, dict) and (receipt.get("remote_confirmation_required")
                          or receipt.get("stage") == "pending_confirmation"))
    uploaded = bool(upload_stage_done and not upload_pending)
    stage_info = stage_cards(manifest, bool(rows), bool(advance.get("running")), diagnostics, batch_folder=folder)
    flow_started_at, flow_finished_at, flow_minutes = _flow_timing(manifest)
    # 审核已真实执行、只剩人工决定时，卡点提示要指向“去处理差异”，不能写成“等待执行”。
    if (stage_info.get("current") or {}).get("id") == "review_gate" and label_open:
        stage_info["hint"] = f"审核已执行；{len(label_open)} 项标签差异待人工决定"
    return {"name": name, "meta": report.get("meta", manifest.get("meta", {})),
            "status": manifest.get("status", report.get("status", "unknown")),
            "paper": paper_info(folder, report, manifest),
            "stage": stage_info,
            "diagnostics": diagnostics,
            "advance": advance,
            "counts": counts, "total": len(rows), "unit_total": len(rows),
            "question_total": sum(len(row.get("numbers") or [row.get("number")]) for row in rows),
            "review_status": review_status, "review_revision": review_status.get("revision", ""),
            "open_findings": len(label_open), "format_findings": len(format_open),
            "batch_findings": batch_findings,
            "needs_human": sum(1 for row in rows if row["needs_human"]),
            "format_units": sum(1 for row in rows if row.get("format_count")),
            "has_anomaly": anomaly_info["has_anomaly"],
            "anomaly_count": anomaly_info["anomaly_count"],
            "anomaly_unit_count": anomaly_info["anomaly_unit_count"],
            "stage_blocked": anomaly_info["stage_blocked"],
            "anomaly_reasons": anomaly_info["anomaly_reasons"],
            "anomaly_summary": anomaly_info["anomaly_summary"],
            "media": media_summary,
            "extraction": load(folder / "07_review" / "extraction-report.json", {}),
            "structure_gate": load(folder / "07_review" / "structure-gate.json", {}),
            "stages": stages, "rows": rows, "has_audio": has_audio,
            # 自动流程是否结束（审核后最终格式检查通过）；上传页排队区按此移除。
            "flow_done": flow_done,
            # 用户显式移出排队区：只影响排队展示与自动串行，不改批次数据或完成态。
            "queue_removed": queue_removed(folder),
            # 流程开始/完成绝对时间与总耗时（分钟）；审核页卡片展示完成时间与耗时。
            "flow_started_at": flow_started_at, "flow_finished_at": flow_finished_at,
            "flow_minutes": flow_minutes,
            # 试卷审核列表按“最新”排序：完成/最近进度优先，其次批次更新时间；排队区严格按上传顺序排序。
            "queue_order_ts": _batch_upload_order_ns(folder, manifest),
            "created_at": manifest.get("created_at"),
            "updated_at": manifest.get("updated_at"),
            # 已写入云端上传暂存的批次视为完成，试卷审核列表不再展示。
            "uploaded": uploaded, "upload_pending_confirmation": upload_pending,
            "upload_status": upload}


def list_batches():
    result = []
    if WORKFLOWS.is_dir():
        for folder in sorted(WORKFLOWS.iterdir()):
            if folder.is_dir() and (folder / "workflow" / "manifest.json").is_file():
                try:
                    result.append(overview(folder.name))
                except ValueError:
                    pass
    _save_overview_snapshot(only_if_dirty=True)
    return result


def warm_batch_list():
    """Warm the first page load and top unuploaded review bundles in the background.

    A cold projection of every batch costs several seconds (source text + review
    rounds + digests per batch). Building it once at startup keeps the review
    page's first list request well inside the client's 15s request budget, and
    pre-warming the top 3 unuploaded batches makes clicking into review instant.
    Read-only; any failure just leaves the cache cold.
    """
    try:
        batches = cached_list_batches()
    except (OSError, ValueError, TypeError, KeyError, ImportError):
        return
    try:
        pending_review = [
            b for b in (batches or [])
            if isinstance(b, dict) and b.get("name") and not _is_batch_uploaded(b)
            and not _is_queued_unfinished(b) and b.get("rows")
        ]
        pending_review.sort(
            key=lambda b: str(b.get("completed_at") or b.get("updated_at") or b.get("created_at") or ""),
            reverse=True,
        )
        for b in pending_review[:15]:
            try:
                units_bundle(b["name"], _readonly=True)
            except (OSError, ValueError, TypeError, KeyError, ImportError):
                continue
    except (OSError, ValueError, TypeError, KeyError, ImportError):
        pass


_WORK_STATE_CACHE: dict[str, tuple[tuple, bool]] = {}
_WORK_STATE_CACHE_MAX = 512


def _work_state_stat_key(folder: Path, adv_running: bool, ledger: str) -> tuple:
    stats = [adv_running]
    for rel in ("workflow/manifest.json", "07_review/upload-format.json",
                "06_batch/batch_meta.json", "workflow/" + ledger):
        try:
            st = (folder / rel).stat()
            stats.append((st.st_ino, st.st_mtime_ns, st.st_size))
        except OSError:
            stats.append(None)
    return tuple(stats)


def operations_work_state():
    """Cheap read-only admission: current local work or a live incident.

    Do not run incident discovery (which can write ledgers) during this hint.
    Terminal history and stopped batches must not keep idle polling active.
    """
    from workflow import operations
    active = []
    if WORKFLOWS.is_symlink() or not WORKFLOWS.is_dir():
        return {"has_work": False, "batches": []}
    for folder in WORKFLOWS.iterdir():
        if (folder.is_symlink() or not folder.is_dir()
                or not (folder / "workflow/manifest.json").is_file()):
            continue
        try:
            adv_running = _is_advance_running(folder.name)
            stat_key = _work_state_stat_key(folder, adv_running, operations.LEDGER)
            folder_key = str(folder)
            with _CACHE_GUARD:
                cached = _WORK_STATE_CACHE.get(folder_key)
            if cached is not None and cached[0] == stat_key:
                if cached[1]:
                    active.append(folder.name)
                continue
            manifest = wf_manifest.load(folder) if wf_manifest else {}
            if manifest.get('status') in {'stopped', 'cancelled', 'archived', 'published', 'staged'}:
                is_active = False
            else:
                stages = manifest.get('stages') or {}
                local_pending = bool(wf_execution_state) and any(
                    not wf_execution_state.executed(stages.get(sid), allow_unqualified=True)
                    for sid in wf_execution_state.LOCAL_STAGES)
                # Only hash the final artifact when the local records appear done.
                if not local_pending and wf_execution_state:
                    local_pending = not wf_execution_state.local_complete(folder, manifest)
                if not local_pending:
                    has_running_stage = any(
                        isinstance(rec, dict) and rec.get('status') == 'running'
                        for rec in stages.values())
                    if has_running_stage and not adv_running:
                        local_pending = True
                is_active = bool(local_pending or operations.pending_incident(folder))
            with _CACHE_GUARD:
                if len(_WORK_STATE_CACHE) >= _WORK_STATE_CACHE_MAX:
                    _WORK_STATE_CACHE.clear()
                _WORK_STATE_CACHE[folder_key] = (stat_key, is_active)
            if is_active:
                active.append(folder.name)
        except (OSError, ValueError, TypeError):
            continue
    return {"has_work": bool(active), "batches": active[:40]}


def _batches_live_fingerprint():
    """Cheap stat + in-memory state fingerprint so status changes bust the 2s list snapshot immediately."""
    disk = []
    try:
        if not WORKFLOWS.is_symlink() and WORKFLOWS.is_dir():
            for folder in sorted(WORKFLOWS.iterdir()):
                if folder.is_symlink() or not folder.is_dir() or folder.name.startswith("."):
                    continue
                if not (folder / "workflow" / "manifest.json").is_file():
                    continue
                row = [folder.name]
                for rel in ("workflow/manifest.json", "workflow/activity.json",
                            "workflow/self-heal-status.json", "workflow/supervisor.json",
                            "07_review/upload-format.json", "07_review/review-gate.json",
                            "07_upload/receipt.json", "receipt.json", QUEUE_REMOVED):
                    try:
                        st = (folder / rel).stat()
                        row.append((st.st_mtime_ns, st.st_size))
                    except OSError:
                        row.append(None)
                disk.append(tuple(row))
    except OSError:
        pass
    with _lock:
        adv = tuple(sorted(
            (k, bool(v.get("running")), bool(v.get("queued")), bool(v.get("waiting")), v.get("finished_at"))
            for k, v in _advance_state.items() if isinstance(v, dict)))
        upl = tuple(sorted(
            (k, bool(v.get("running")), v.get("ok"), v.get("step"), v.get("finished_at"))
            for k, v in _upload_jobs.items() if isinstance(v, dict)))
        aud = (bool(_audio_job.get("running")), _audio_job.get("kind"), _audio_job.get("batch"))
    worker_seq = _remote_worker().state_seq() if _is_split_worker() else 0
    return (str(WORKFLOWS), str(INBOX), tuple(disk), adv, upl, aud, worker_seq)


def cached_list_batches():
    """Coalesce overlapping HTTP list reads; any stage/status change invalidates immediately.

    Older loaded hosts use this endpoint as a readiness probe. A short snapshot
    prevents those parallel probes from repeating all media audits per image.
    The uncached list_batches remains available to internal workflow callers.
    """
    global _batches_snapshot
    # Coalesce list readers without holding the scheduler/mutation lock during
    # full question/media projection. HTTP polling must not starve dispatch.
    with _batches_read_lock:
        key = _batches_live_fingerprint()
        with _lock:
            if (_batches_snapshot is not None and _batches_snapshot[0] == key
                    and monotonic() < _batches_snapshot[1]):
                return _batches_snapshot[2]
            epoch = _batches_epoch
        result = list_batches()
        with _lock:
            if epoch == _batches_epoch and key == _batches_live_fingerprint():
                _batches_snapshot = (key, monotonic() + 2.0, result)
        return result


from .type_review_api import (  # noqa: E402
    TYPE_REVIEW_DEFAULT_PAGE_SIZE,
    TYPE_REVIEW_MAX_PAGE_SIZE,
    UNTYPED_TYPE_LABEL,
    UNTYPED_TYPE_VALUE,
    _batch_sort_ts,
    _match_type_review_raw,
    _type_review_is_pending,
    _type_review_row,
    type_review_units,
)



# ---------------------------------------------------------------------------
# 批次上传：把本批题目写入云端题库的“上传暂存”。
#
# 这在设计上仍是一个显式人工动作：面板点「上传批次」即代表本次上传获得批准，
# 但内部完整走三步（步骤与 CLI 一致，闸门由 workflow.runner 自己校验，不绕过）：
#   1) prepare-upload  读取远端目录、校验并冻结上传请求
#   2) mark upload_gate approved  绑定冻结文件与目标（actor = 本机操作员）
#   3) upload --yes    真正写入上传暂存
# ---------------------------------------------------------------------------
# 英语听力切段与云端题库上传后台任务（已拆至 audio_jobs.py / batch_upload.py）
# ---------------------------------------------------------------------------
from .audio_jobs import (  # noqa: E402
    _AUDIO_STALE_GRACE,
    _AUDIO_WATCHDOG_SECONDS,
    _AUDIO_WATCHDOG_TICK,
    _audio_batch_folder,
    _audio_has_live_worker,
    _audio_modules,
    _audio_reap_stale,
    audio_overview,
    audio_status,
    start_audio_job_impl as _start_audio_job_impl,
)


@serialized
def start_audio_job(kind: str, options: dict) -> dict:
    return _start_audio_job_impl(kind, options)


_upload_jobs = {}
_UNREVIEWED_SUMMARY_CACHE: dict[tuple, dict] = {}

from .batch_upload import (  # noqa: E402
    _cached_unreviewed_summary,
    _check_upload_preconditions,
    _prepare_failure_summary,
    _run_batch_upload,
    _run_runner,
    _stage_upload_media,
    _validate_ever_passed,
    start_batch_upload_impl as _start_batch_upload_impl,
    upload_state,
)


@serialized
def start_batch_upload(name, actor="本机操作员", *, allow_unreviewed=False, unreviewed_revision=''):
    return _start_batch_upload_impl(
        name, actor, allow_unreviewed=allow_unreviewed, unreviewed_revision=unreviewed_revision
    )


from .review_views import (  # noqa: E402
    _answer_point_terms,
    _knowledge_projection,
    _pending_answer_point_tags,
    _prepare_answer_point_ctx,
    _prepare_review_comparison_ctx,
    answer_point_tags,
    drop_short_knowledge_copies,
    finding_comparison,
    has_unverified_review_history,
    label_payload,
    load_unit_labels,
    preview_labels,
    review_comparison,
    unit_label_path,
)

def _prepare_batch_detail_context(folder: Path, name: str):
    """Load whole-paper shared inputs once for detail() and units_bundle()."""
    report, manifest, queue = report_for(folder)
    base = question_rows(folder, report)
    number_map = number_of_unit(base)
    rows = merged_rows(folder, report)
    confirmed = load(folder / "01_source/structure.confirmed.json", {}) or {}
    scoped_units = [u for u in confirmed.get("units", []) if isinstance(u, dict)
                    and u.get("identity_version") == "scoped-v1"]
    scoped_by_uid = {}
    for u in scoped_units:
        scoped_by_uid.setdefault(str(u.get("unit_id")), []).append(u)
    has_scoped = bool(scoped_units) or any(r.get("identity_version") == "scoped-v1" for r in rows)
    lines = source_lines(folder)
    source_text = "\n".join(lines)
    try:
        from workflow import sync_practice
        is_sync_practice = sync_practice.looks_like_sync_practice(source_text)
    except (ImportError, OSError, ValueError, TypeError, KeyError):
        is_sync_practice = False
    try:
        extraction = load(folder / "01_source" / "extraction.json", {})
    except (UnicodeError, ValueError, TypeError):
        extraction = {}
    audio_mapping = audio_map(folder)
    all_numbers = [int(n) for r in rows for n in (r.get("numbers") or []) if str(n).isdigit()]
    # 延迟计算整卷媒体归属：仅在当前单元确实包含图片引用时才执行 media_audit.audit，
    # 避免纯文本首题在打开时先做全卷图片核查。
    media = None
    try:
        snapshot_pair = review_snapshot(folder)
    except (ImportError, OSError, ValueError, TypeError, KeyError):
        snapshot_pair = ({}, {})
    batch_map = _batch_units_map(folder)
    all_cands_by_num = {}
    scoped_cands_by_uid = {}
    cand_dir = folder / "candidates"
    for path in sorted(cand_dir.glob("*.json")) if cand_dir.is_dir() else []:
        item = load(path, {}) or {}
        number = str(item.get("number") or "").strip()
        if number.isdigit():
            all_cands_by_num[number] = (item, path)
            uid_key = str(item.get("unit_id") or "")
            scoped_cands_by_uid.setdefault(uid_key, {})[number] = (item, path)
    ap_ctx = _prepare_answer_point_ctx(folder)
    rc_ctx = _prepare_review_comparison_ctx(folder)
    r1_record = (snapshot_pair[1] or {}).get("labels") if isinstance(snapshot_pair, tuple) and len(snapshot_pair) == 2 else None
    r1_round_meta = next(
        (r for r in ((snapshot_pair[0] or {}).get("rounds") or [])
         if isinstance(r, dict) and r.get("round_id") == "labels"),
        None,
    ) if isinstance(snapshot_pair, tuple) and len(snapshot_pair) == 2 else None
    if isinstance(r1_record, dict) and isinstance(r1_record.get("checks"), list):
        r1_assessments = {
            str(c.get("unit_id") or "").strip(): c["assessment"]
            for c in r1_record["checks"]
            if isinstance(c, dict) and str(c.get("unit_id") or "").strip()
            and isinstance(c.get("assessment"), dict)
        }
    elif isinstance(r1_round_meta, dict) and r1_round_meta.get("state") == "not_started":
        r1_assessments = {}
    else:
        try:
            from workflow import label_merge
            r1_assessments = label_merge.load_round1_assessments(folder)
        except (ImportError, OSError, ValueError, TypeError, KeyError):
            r1_assessments = {}
    adv_running = bool(advance_state(name).get("running"))
    try:
        from workflow import diagnose as _diagnose
        detail_diag = _diagnose.diagnose(folder)
    except (OSError, ValueError, KeyError, ImportError):
        detail_diag = None
    precollected_anomalies = anomaly_views.collect_unit_stage_anomalies(folder, manifest, detail_diag, rows)
    anomaly_views.decorate_anomaly_rows(
        folder, rows, manifest=manifest, diagnostics=detail_diag,
        advance={"running": adv_running}, precollected=precollected_anomalies,
    )
    shared = {
        "batch": name,
        "meta": report.get("meta", manifest.get("meta", {})),
        "paper": paper_info(folder, report, manifest),
        "stage": stage_cards(manifest, bool(all_numbers), adv_running),
        "paper_bulk_bold": paper_uses_bulk_bold(lines),
        "manifest": {"status": manifest.get("status", "unknown"), "stages": manifest.get("stages", {})},
        "taxonomy": {
            "types": taxonomy.types(), "subtypes": taxonomy.subtype_map(),
            "difficulties": taxonomy.difficulties(), "categories": taxonomy.categories(),
            "sources": taxonomy.sources(), "years": taxonomy.years(),
            "regions": taxonomy.regions(), "knowledge": taxonomy.knowledge_version(),
        },
    }
    return {
        "report": report, "manifest": manifest, "queue": queue,
        "base": base, "number_map": number_map, "rows": rows,
        "scoped_units": scoped_units, "scoped_by_uid": scoped_by_uid, "has_scoped": has_scoped,
        "lines": lines, "source_text": source_text, "is_sync_practice": is_sync_practice,
        "extraction": extraction, "audio_mapping": audio_mapping,
        "all_numbers": all_numbers, "media": media, "media_loaded": False,
        "snapshot_pair": snapshot_pair, "batch_map": batch_map,
        "all_cands_by_num": all_cands_by_num, "scoped_cands_by_uid": scoped_cands_by_uid,
        "ap_ctx": ap_ctx, "rc_ctx": rc_ctx, "r1_assessments": r1_assessments,
        "adv_running": adv_running, "precollected_anomalies": precollected_anomalies, "shared": shared,
    }


def _build_unit_detail(folder: Path, name: str, unit_id, ctx: dict, *, row_override=None, for_bundle: bool = False):
    manifest = ctx["manifest"]
    queue = ctx["queue"]
    base = ctx["base"]
    number_map = ctx["number_map"]
    rows = ctx["rows"]
    scoped_by_uid = ctx["scoped_by_uid"]
    has_scoped = ctx["has_scoped"]
    lines = ctx["lines"]
    extraction = ctx["extraction"]
    audio_mapping = ctx["audio_mapping"]
    media = ctx["media"]
    batch_map = ctx["batch_map"]
    ap_ctx = ctx["ap_ctx"]
    rc_ctx = ctx["rc_ctx"]

    row = row_override
    if row is None:
        row = next((r for r in rows if str(r.get("unit_id")) == str(unit_id)), None)
        if row is None:
            # Previously bookmarked merged group opens its first current partition.
            row = next((r for r in rows if r.get("source_parent_id") == str(unit_id)), None)
        if row is None and not has_scoped:
            alias = re.sub(r"\D", "", str(unit_id))
            if alias:
                row = next((r for r in rows if int(alias) in (r.get("numbers") or [])), None)
        if row is None:
            row = {} if has_scoped else next((r for r in rows if str(r.get("number")) == str(unit_id)), {})
    row = dict(row)
    selected_uid = str(row.get("unit_id") or unit_id)
    bound_units = scoped_by_uid.get(selected_uid, [])
    bound_unit = bound_units[0] if len(bound_units) == 1 else {}
    scoped = has_scoped or row.get("identity_version") == "scoped-v1" or bool(bound_unit)
    if scoped:
        for key in ("identity_version", "section_id", "source_section", "question_ids", "printed_numbers"):
            if key in bound_unit:
                row[key] = bound_unit[key]
        printed_numbers = bound_unit.get("printed_numbers", (bound_unit.get("source_span") or {}).get("printed_numbers"))
        binding = (bound_unit.get("source_span") or row.get("source_span") or {}).get("number_binding")
        if printed_numbers is not None:
            row["printed_numbers"] = printed_numbers
        if row.get("printed_numbers") == [] and binding != "answer_inferred_image_group":
            row["label"] = str((row.get("source_section") or {}).get("title") or "无印刷题号")
        # Numeric-only findings have no unique owner in a scoped batch.
        queue = dict(queue, items=[f for f in queue.get("items", [])
                                  if isinstance(f, dict) and str(f.get("unit_id") or "") == selected_uid])
        base = [r for r in base if str(r.get("unit_id") or "") == selected_uid]
    try:
        decorate_review_rows(folder, [row], snapshot=ctx["snapshot_pair"],
                             source_text=ctx["source_text"], is_sync_practice=ctx["is_sync_practice"])
    except (ImportError, OSError, ValueError, TypeError, KeyError):
        pass
    numbers = [int(n) for n in (row.get("numbers") or [row.get("number")]) if str(n).isdigit()]
    if not numbers:
        fallback = number_map.get(selected_uid)
        if str(fallback or "").strip().isdigit():
            numbers = [int(fallback)]

    def detail_findings(wanted_numbers):
        if not scoped:
            return findings_for(queue, wanted_numbers, number_map)
        wanted = {str(n) for n in wanted_numbers}
        return [f for f in queue.get("items", [])
                if not str(f.get("number") or "").strip() or str(f.get("number")) in wanted]

    if source_slice is not None and lines and numbers:
        chunk = source_slice(folder, numbers, lines, unit_id=selected_uid)
        if scoped and chunk:
            title = str((row.get("source_section") or {}).get("title") or "")
            if title and chunk[0] != title:
                chunk = [title, *chunk]
        # 独立题预览补上所属大题的标题+作答导语（「Ⅱ.短对话理解…」「你将听到十段对话…」），
        # 题组自己带材料/导语，无需重复。只影响预览，不改切题结果。
        if not scoped and len(numbers) == 1 and str(row.get("unit_kind") or "") != "group" and section_leadin is not None:
            try:
                lead = section_leadin(lines, numbers, chunk[0] if chunk else None)
                if lead:
                    # 书面表达等无印刷题号时，chunk 首行本身就是导语/要求，避免与之重复。
                    if chunk and lead[-1] == chunk[0]:
                        lead = lead[:-1]
                    chunk = list(lead) + list(chunk)
            except (ValueError, KeyError, TypeError, IndexError):
                pass
    elif slice_text is not None and lines and numbers:
        chunk = slice_text(lines, numbers[0])
    else:
        chunk = []
    blocks = attach_media(source_blocks(chunk, extraction=extraction), folder, name)
    audio_units = [row.get("unit_id") or unit_id]
    if row.get("source_parent_id"):
        audio_units.append(row["source_parent_id"])
    audio_blocks_list = audio_blocks(folder, name, audio_units, mapping=audio_mapping)
    if audio_blocks_list:
        blocks = audio_blocks_list + blocks
    text = "\n".join(chunk)
    if not blocks and text:
        blocks = [{"type": "text", "text": text}]

    by_number = ctx["scoped_cands_by_uid"].get(selected_uid, {}) if scoped else ctx["all_cands_by_num"]
    valid_identity = not scoped or bool(row.get("unit_id") or bound_unit)
    batch_item, _ = candidate(folder, row.get("unit_id") or unit_id, batch_map=batch_map) if valid_identity else ({}, None)
    subs = []
    for number in numbers:
        cand, path = by_number.get(str(number), ({}, None))
        base_sub = next((s for s in (row.get("subquestions") or [])
                         if str(s.get("number")) == str(number)), {})
        if not base_sub and len(numbers) == 1:
            base_sub = batch_item
        state = next((r.get("state") for r in base if str(r.get("number")) == str(number)), "unverified")
        item = {
            "number": number,
            "stem": cand.get("stem") or base_sub.get("stem") or "",
            "options": cand.get("options") or base_sub.get("options") or [],
            "answer": cand.get("answer") or base_sub.get("answer") or "",
            "type": cand.get("type") or row.get("type") or "",
            "subtype": cand.get("subtype") or base_sub.get("subtype") or "",
            "knowledge": cand.get("knowledge") or row.get("knowledge") or [],
            "difficulty": cand.get("difficulty") or row.get("difficulty") or "",
            "state": state,
            "candidate_path": str(path.relative_to(folder)) if path else None,
        }
        item["finding_count"] = len(detail_findings([number]))
        label_items, fmt_items, _ = split_findings(detail_findings([number]))
        item["label_count"] = len([f for f in label_items if is_open(f)])
        item["format_count"] = len([f for f in fmt_items if is_open(f)])
        item["needs_human"] = bool(item["label_count"])
        subs.append(item)

    if not subs and valid_identity:
        fallback_cand, fallback_path = candidate(folder, row.get("unit_id") or unit_id, batch_map=batch_map)
        if fallback_cand:
            fallback_number = fallback_cand.get("number")
            if not numbers and str(fallback_number or "").strip().isdigit():
                numbers = [int(fallback_number)]
            state = next((r.get("state") for r in base if str(r.get("number")) == str(fallback_number)), "unverified")
            subs.append({
                "number": numbers[0] if numbers else None,
                "stem": fallback_cand.get("stem") or "", "options": fallback_cand.get("options") or [],
                "answer": fallback_cand.get("answer") or "", "type": fallback_cand.get("type") or row.get("type") or "",
                "subtype": fallback_cand.get("subtype") or "", "knowledge": fallback_cand.get("knowledge") or [],
                "difficulty": fallback_cand.get("difficulty") or "", "state": state,
                "candidate_path": str(fallback_path.relative_to(folder)) if fallback_path else None,
                "finding_count": len(detail_findings(numbers)),
                "needs_human": False,
            })

    findings = detail_findings(numbers)
    row = mark_row(row, findings)
    anomaly_views.decorate_anomaly_rows(
        folder, [row], manifest=manifest,
        advance={"running": ctx["adv_running"]},
        precollected=ctx.get("precollected_anomalies"),
    )
    label_findings, format_findings, other_findings = split_findings(findings)
    whole_unit = taxonomy.whole_unit_type(row.get("type") or batch_item.get("type") or "")
    label_open = group_duplicate_findings(label_findings, whole_unit=whole_unit)
    format_open = [f for f in format_findings if is_open(f)] + [f for f in other_findings if is_open(f)]
    labels = label_payload(
        row, batch_item, subs, folder, row.get("unit_id") or unit_id,
        ap_ctx=ap_ctx, r1_assessments=ctx.get("r1_assessments"),
    )
    comparison = review_comparison(folder, row.get("unit_id") or unit_id, numbers, rc_ctx=rc_ctx, ap_ctx=ap_ctx)
    # 每条意见带上“这是哪道题、当前值是什么”，人不用再翻原卷猜问题指的是什么。
    for finding in label_open:
        finding["scope"] = (f"第 {row.get('label') or '-'.join(str(n) for n in numbers)} 题组（{len(numbers)} 小题）"
                            if len(numbers) > 1 else f"第 {numbers[0] if numbers else '?'} 题")
        if len(finding["member_ids"]) > 1:
            # 同一疑点的多条记录合并成一条展示与处理，不再逐条罗列 ID。
            finding["merged_count"] = len(finding["member_ids"])
        finding["current"] = finding_current(finding, labels, subs, numbers)
        finding["comparison"] = finding_comparison(finding, comparison, numbers, number_map)
    shown = {Path(m.group(1).strip()).name for line in chunk for m in media_audit.IMG_REF.finditer(line)}
    if shown:
        if not ctx.get("media_loaded"):
            try:
                ctx["media"] = media_audit.audit(folder, ctx["all_numbers"])
            except (OSError, ValueError, KeyError):
                ctx["media"] = None
            ctx["media_loaded"] = True
        media = ctx.get("media")
        media_items = [m for m in media["items"] if m["name"] in shown] if media is not None else []
    else:
        media_items = []
    # 卷头、栏目图、已排除的图不是题目内容，预览里不显示，避免被当成题目图片保存。
    drop = {item["name"] for item in media_items
            if item["verdict"] in ("header", "banner", "unused", "excluded")}
    if drop:
        blocks = [block for block in blocks if not (block.get("type") == "image" and block.get("name") in drop)]
    if scoped and row.get("printed_numbers") == [] and (bound_unit.get("source_span") or row.get("source_span") or {}).get("number_binding") != "answer_inferred_image_group":
        subs = [dict(sub, display_number=None, internal_number=sub.get("number")) for sub in subs]
    unit_payload = {
        "preview_blocks": blocks, "source_preview": text,
        "audio": audio_blocks_list,
        "hidden_media": sorted(drop),
        "row": row, "item": batch_item,
        "unit_id": row.get("unit_id", unit_id), "label": row.get("label") or "-".join(str(n) for n in numbers),
        "unit_kind": row.get("unit_kind", "standalone"), "numbers": numbers,
        "type": row.get("type") or batch_item.get("type", ""),
        "subtypes": (labels.get("subtypes") if isinstance(labels.get("subtypes"), list) and labels.get("subtypes")
                     else row.get("subtypes") if isinstance(row.get("subtypes"), list)
                     else ([_row_subtype(row)] if _row_subtype(row) else [])),
        "source_section": row.get("source_section") or {},
        "printed_numbers": row.get("printed_numbers"),
        "material": batch_item.get("material") or row.get("material") or "",
        "stem": row.get("stem") or batch_item.get("stem") or "",
        "labels": labels, "comparison": comparison, "label_warnings": labels.get("warnings", []),
        "preview_labels": preview_labels(labels, subs, batch_item, row),
        "media": media_items,
        "subquestions": subs, "findings": findings,
        "label_findings": label_open, "format_findings": format_open,
        "open_findings": len(label_open), "format_open": len(format_open),
        "needs_human": bool(label_open),
        "is_anomaly": bool(row.get("is_anomaly")),
        "anomaly_reasons": list(row.get("anomaly_reasons") or []),
        "anomaly_summary": str(row.get("anomaly_summary") or ""),
    }
    if bool(numbers) != bool(ctx["all_numbers"]):
        unit_payload["stage"] = stage_cards(manifest, bool(numbers), ctx["adv_running"])
    if for_bundle:
        return unit_payload
    result = dict(ctx["shared"])
    result.update(unit_payload)
    return result


_UNITS_BUNDLE_CACHE: dict[tuple, dict] = {}
_DETAIL_CTX_CACHE: dict[tuple, dict] = {}
_UNITS_BUNDLE_LOCKS: dict[str, threading.Lock] = {}


def _units_bundle_disk_path(folder: Path) -> Path:
    digest = hashlib.sha256(str(folder.resolve()).encode("utf-8")).hexdigest()[:24]
    return _overview_snapshot_path().parent / "units-bundles" / f"{digest}.json"


def _units_bundle_key_str(folder: Path, name: str) -> str:
    """Content/stat fingerprint for RAM + disk cached units_bundle() & detail()."""
    parts = [
        _review_inputs_key(folder),
        bool(advance_state(name).get("running")),
        (folder / "02_audio/SKIP_LISTENING").is_file(),
    ]
    try:
        code = Path(__file__).stat()
        parts.append(("server-code", code.st_mtime_ns, code.st_size))
    except OSError:
        parts.append(("server-code", "unknown"))
    for rel in ("01_source/extraction.json", "02_audio/audio_map.json",
                "01_source/正文.txt", "01_source/structure.confirmed.json",
                "06_batch/batch_meta.json", "06_batch/batch.json",
                "06_batch/batch.base.json", "06_batch/question-draft.json", "03_types/types.json",
                "07_review/validation-report.json", "07_review/upload-format.json",
                "07_review/unit-anomalies.json"):
        p = folder / rel
        try:
            st = p.stat()
            parts.append((rel, st.st_mtime_ns, st.st_size))
        except OSError:
            parts.append((rel, "missing"))
    cand_dir = folder / "candidates"
    if cand_dir.is_dir():
        for p in sorted(cand_dir.rglob("*.json")):
            try:
                st = p.stat()
                parts.append(("cand:" + str(p.relative_to(folder)), st.st_mtime_ns, st.st_size))
            except OSError:
                continue
    return hashlib.sha256(repr(parts).encode("utf-8")).hexdigest()


def _load_units_bundle_disk(folder: Path, key_str: str) -> dict | None:
    try:
        raw = json.loads(_units_bundle_disk_path(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (isinstance(raw, dict) and raw.get("version") == 1
            and raw.get("folder") == str(folder)
            and raw.get("key") == key_str
            and isinstance(raw.get("bundle"), dict)):
        return raw["bundle"]
    return None


def _save_units_bundle_disk(folder: Path, key_str: str, bundle: dict) -> None:
    try:
        target = _units_bundle_disk_path(folder)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        tmp.write_text(
            json.dumps({"version": 1, "folder": str(folder), "key": key_str, "bundle": bundle},
                       ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        tmp.replace(target)
    except (OSError, TypeError, ValueError):
        pass


def _get_cached_bundle(folder: Path, name: str) -> tuple[tuple, dict | None]:
    key_str = _units_bundle_key_str(folder, name)
    cache_key = (str(folder), key_str)
    hit = _cache_get_lru(_UNITS_BUNDLE_CACHE, cache_key)
    if hit is not None:
        return cache_key, hit
    disk_hit = _load_units_bundle_disk(folder, key_str)
    if disk_hit is not None:
        _cache_put_lru(_UNITS_BUNDLE_CACHE, cache_key, disk_hit, _UNITS_BUNDLE_CACHE_MAX)
        return cache_key, disk_hit
    return cache_key, None


def _get_or_prepare_detail_ctx(folder: Path, name: str, cache_key: tuple) -> dict:
    ctx = _cache_get_lru(_DETAIL_CTX_CACHE, cache_key)
    if ctx is not None:
        return ctx
    ctx = _prepare_batch_detail_context(folder, name)
    _cache_put_lru(_DETAIL_CTX_CACHE, cache_key, ctx, _DETAIL_CTX_CACHE_MAX)
    return ctx


def detail(name, unit_id, *, _readonly: bool = False):
    folder = batch_dir(name)
    cache_key, cached_bundle = _get_cached_bundle(folder, name)
    if cached_bundle is not None:
        unit_payload = cached_bundle["units"].get(str(unit_id))
        if unit_payload is not None:
            if _readonly:
                result = dict(cached_bundle["shared"])
                result.update(unit_payload)
                return result
            import copy
            result = copy.deepcopy(cached_bundle["shared"])
            result.update(copy.deepcopy(unit_payload))
            return result
    # 与 units_bundle 共享按批次锁与整卷上下文缓存：若预取正在计算则直接复用其产物，
    # 若首题先到则只算首题并把上下文留给随后的 units_bundle 复用，彻底消除双份重算。
    with _folder_lock(_UNITS_BUNDLE_LOCKS, str(folder)):
        cache_key, cached_bundle = _get_cached_bundle(folder, name)
        if cached_bundle is not None:
            unit_payload = cached_bundle["units"].get(str(unit_id))
            if unit_payload is not None:
                if _readonly:
                    result = dict(cached_bundle["shared"])
                    result.update(unit_payload)
                    return result
                import copy
                result = copy.deepcopy(cached_bundle["shared"])
                result.update(copy.deepcopy(unit_payload))
                return result
        ctx = _get_or_prepare_detail_ctx(folder, name, cache_key)
        return _build_unit_detail(folder, name, unit_id, ctx, for_bundle=False)


def units_bundle(name, *, _readonly: bool = False):
    """一次性返回整份试卷全部单元详情（共享字段只传一次），供前端切题零延迟命中缓存。"""
    import copy
    folder = batch_dir(name)
    cache_key, cached_bundle = _get_cached_bundle(folder, name)
    if cached_bundle is not None:
        return cached_bundle if _readonly else copy.deepcopy(cached_bundle)
    with _folder_lock(_UNITS_BUNDLE_LOCKS, str(folder)):
        cache_key, cached_bundle = _get_cached_bundle(folder, name)
        if cached_bundle is not None:
            return cached_bundle if _readonly else copy.deepcopy(cached_bundle)
        ctx = _get_or_prepare_detail_ctx(folder, name, cache_key)
        units = {}
        order = []
        for row in ctx["rows"]:
            uid = str(row.get("unit_id") or row.get("number") or "")
            if not uid or uid in units:
                continue
            units[uid] = _build_unit_detail(folder, name, uid, ctx, row_override=row, for_bundle=True)
            order.append(uid)
        bundle = {
            "batch": name,
            "shared": ctx["shared"],
            "units": units,
            "order": order,
        }
        _cache_put_lru(_UNITS_BUNDLE_CACHE, cache_key, bundle, _UNITS_BUNDLE_CACHE_MAX)
        _DETAIL_CTX_CACHE.pop(cache_key, None)
    # 落盘放在批次锁外，避免阻塞并发的单题 detail() 命中内存缓存。
    _save_units_bundle_disk(folder, cache_key[1], bundle)
    return bundle if _readonly else copy.deepcopy(bundle)


MANUAL_CONTENT_FIELDS = ("explanation", "articleSummary")


def _content_row(payload, unit_id):
    rows = payload.get("items") if isinstance(payload, dict) else None
    matches = [row for row in (rows if isinstance(rows, list) else [])
               if isinstance(row, dict) and (row.get("unit_id") or row.get("id")) == unit_id]
    if len(matches) != 1:
        raise ValueError(f"无法唯一定位正式单元 {unit_id}，未保存")
    return matches[0]


def apply_manual_content(folder: Path, unit_id, unit, expected, files, current,
                         payload, has_unit_edit) -> dict:
    """Stage the accepted text, provenance and gate changes for one install."""
    import hashlib
    from workflow import manual_content, stage_semantics
    from workflow.agent_tasks import _safe_path

    unit = unit or {}
    edit = {key: unit[key] for key in MANUAL_CONTENT_FIELDS if key in unit}
    result = {"applied": False, "paths": [], "skipped": [], "reason": ""}
    if not edit:
        return result
    if any(not isinstance(value, str) for value in edit.values()):
        raise ValueError("解析和文章大意必须是文本")
    target = "06_batch/batch_meta.json"
    artifact = "04_knowledge/explanations.json"
    row = _content_row(payload, unit_id)
    if expected is not None and any(expected.get(key) != (row.get(key) or "") for key in edit):
        raise ValueError("正式内容已变化，请重新打开本题后再修改")
    edit = {key: value for key, value in edit.items() if row.get(key, "") != value}
    if not edit and not has_unit_edit:
        return result
    explanations = load(_safe_path(folder, artifact))
    explanation_row = _content_row(explanations, unit_id)
    if current is not None:
        stages = current["stages"]
        record = stages["explanations"]
        if (stages["upload_gate"].get("status") == wf_manifest.APPROVED
                or stages["upload"].get("status") == wf_manifest.COMPLETED):
            raise ValueError("此批次已批准上传或已上传，不能覆盖已绑定的内容")
        spec = wf_contracts.STAGE_BY_ID["explanations"]
        stale, _ = wf_manifest.is_stale(folder, current, spec)
        if record.get("status") != wf_manifest.COMPLETED or stale or record.get("rebuild_required"):
            raise ValueError("解析阶段未完成或产物已过期，不能同步正式批次")
    if has_unit_edit:
        files[target] = payload
        files[manual_content.RELATIVE] = manual_content.prepare(folder, unit_id, edit)
        result["paths"].append(manual_content.RELATIVE)
    if edit:
        explanation_row.update(edit)
        row.update(edit)
        files[artifact] = explanations
        files[target] = payload
        result.update(applied=True, paths=[artifact, target, *result["paths"]])
        if current is not None:
            raw = json.dumps(explanations, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            record["output_hashes"] = {artifact: hashlib.sha256(raw.replace("\n", os.linesep).encode("utf-8")).hexdigest()}
            if record.get("semantic_version") == stage_semantics.VERSION:
                record["semantic_outputs"] = {artifact: stage_semantics.digest(stage_semantics.without_clocks(explanations))}
            wf_manifest.record_decision(current, "explanations", "manual_edit", "workbench",
                                        f"{unit_id}: {', '.join(edit)}", result["paths"])
            wf_manifest.invalidate_downstream(folder, current, "assemble", "工作台人工修改解析/大意，旧检查与审核结果失效")
    return result

_ALLOWED_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _extract_host_only(raw_host: str) -> str:
    host = (raw_host or "").strip().lower()
    if not host:
        return ""
    if host.startswith("["):
        end = host.find("]")
        return host[1:end] if end > 0 else host
    return host.split(":", 1)[0]


class Handler(BaseHTTPRequestHandler):
    # Prevent stalled/half-open client sockets from wedging worker threads or _lock.
    timeout = 60

    def log_message(self, fmt, *args):
        return

    def _check_request_security(self, *, is_post: bool, decoded_path: str) -> bool:
        headers = getattr(self, "headers", None) or {}
        host_header = headers.get("Host", "")
        if host_header:
            host_only = _extract_host_only(host_header)
            if host_only not in _ALLOWED_LOOPBACK_HOSTS:
                self.send_json({"error": "拒绝非本机 Host 请求"}, 403)
                return False
        origin = (headers.get("Origin") or "").strip()
        if origin:
            parsed_origin = urlparse(origin)
            origin_host = (parsed_origin.hostname or "").lower()
            if parsed_origin.scheme not in ("http", "https") or origin_host not in _ALLOWED_LOOPBACK_HOSTS:
                self.send_json({"error": "拒绝跨站 Origin 请求"}, 403)
                return False
        expected_token = os.environ.get("WF_WORKBENCH_TOKEN", "").strip()
        if expected_token and decoded_path != "/api/health":
            provided = headers.get("X-Workbench-Token", "")
            if not provided or not hmac.compare_digest(provided, expected_token):
                self.send_json({"error": "缺少或无效的访问令牌"}, 401)
                return False
        if is_post:
            ct = (headers.get("Content-Type") or "").strip().lower()
            if decoded_path == "/api/uploads":
                if "multipart/form-data" not in ct:
                    self.send_json({"error": "上传接口要求 multipart/form-data"}, 415)
                    return False
            else:
                try:
                    content_len = int(self.headers.get("Content-Length", "0") or "0")
                except ValueError:
                    self.send_json({"error": "无效的 Content-Length"}, 400)
                    return False
                if content_len > MAX_JSON_BYTES:
                    self.send_json({"error": f"请求体超过上限（{MAX_JSON_BYTES // (1024 * 1024)} MB）"}, 413)
                    return False
                if (content_len > 0 or ct) and "application/json" not in ct:
                    self.send_json({"error": "POST 接口要求 Content-Type: application/json"}, 415)
                    return False
        return True

    def send_json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        etag = getattr(self, "_pending_etag", None)
        if etag is not None:
            self._pending_etag = None
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("X-Content-Type-Options", "nosniff")
            if etag:
                self.send_header("ETag", etag)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            # Client aborted/disconnected (e.g. rapid question switching or tab close).
            return

    def do_GET(self):
        parsed = urlparse(self.path)
        decoded_path = unquote(parsed.path)
        if not self._check_request_security(is_post=False, decoded_path=decoded_path):
            return
        try:
            if decoded_path == "/api/health":
                split_mode = _is_split_worker()
                worker_busy = _remote_worker().is_busy() if split_mode else False
                return self.send_json({"ok": True, "instance_id": os.environ.get("WF_BACKEND_INSTANCE_ID", ""),
                                       "started_at": _SERVER_STARTED_AT,
                                       "split_worker": split_mode,
                                       "busy": work_in_flight(),
                                       "worker_busy": worker_busy,
                                       "advance_worker": advance_worker_health(),
                                       "workflow_runtime_version": wf_execution_state.RUNTIME_VERSION if wf_execution_state else None,
                                       "self_heal_supervisor": _workflow_supervisor.health() if _workflow_supervisor else {"running": False}})
            if decoded_path.startswith("/api/content-agent/"):
                from workflow import content_agent
                return self.send_json(content_agent.dispatch("GET", decoded_path, {}))
            if decoded_path == "/api/audio/config":
                return self.send_json(audio_overview())
            if decoded_path == "/api/audio/status":
                query = parse_qs(parsed.query)
                return self.send_json(audio_status((query.get("batch") or [""])[0] or None))
            if decoded_path == "/api/operations/incidents":
                from workflow import operations
                launcher = (
                    (lambda name, folder, **kw: _remote_worker().enqueue(
                        name,
                        operations_expectation=kw.get("operations_expectation"),
                        reset_budget=bool(kw.get("reset_budget", False)),
                    ))
                    if _is_split_worker() else _launch_advance
                )
                operations.recover_resumes(WORKFLOWS, launcher)
                return self.send_json(operations.incidents(WORKFLOWS))
            if decoded_path == "/api/operations/work-state":
                return self.send_json(operations_work_state())
            if decoded_path == "/api/batches":
                qs = parse_qs(parsed.query)
                payload = batch_list_payload(
                    cached_list_batches(),
                    unuploaded=(qs.get("unuploaded") or [""])[0] == "1",
                    is_lite=(qs.get("lite") or [""])[0] == "1",
                    is_review_list=(qs.get("review_list") or [""])[0] == "1",
                )
                worker_seq = _remote_worker().state_seq() if _is_split_worker() else 0
                raw_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                etag = f'W/"{_batches_epoch}-{worker_seq}-{hashlib.sha1(raw_json).hexdigest()[:16]}"'
                req_headers = getattr(self, "headers", None)
                if req_headers and req_headers.get("If-None-Match") == etag and hasattr(self, "wfile"):
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    return
                if type(self).send_json is Handler.send_json and hasattr(self, "wfile"):
                    try:
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json; charset=utf-8")
                        self.send_header("X-Content-Type-Options", "nosniff")
                        self.send_header("ETag", etag)
                        self.send_header("Cache-Control", "no-cache")
                        self.send_header("Content-Length", str(len(raw_json)))
                        self.end_headers()
                        self.wfile.write(raw_json)
                    except (BrokenPipeError, ConnectionResetError, TimeoutError):
                        pass
                    return
                self._pending_etag = etag
                return self.send_json(payload)
            if decoded_path == "/api/taxonomy":
                return self.send_json(taxonomy.taxonomy())
            if decoded_path == "/api/type-review/units":
                qs = parse_qs(parsed.query)
                raw_type = (qs.get("type") or [""])[0]
                raw_q = (qs.get("q") or [""])[0]
                raw_status = (qs.get("status") or ["all"])[0]
                raw_uploaded = (qs.get("uploaded") or ["all"])[0]
                try:
                    raw_page = int((qs.get("page") or ["1"])[0])
                except ValueError:
                    raw_page = 1
                try:
                    raw_size = int((qs.get("page_size") or [str(TYPE_REVIEW_DEFAULT_PAGE_SIZE)])[0])
                except ValueError:
                    raw_size = TYPE_REVIEW_DEFAULT_PAGE_SIZE
                return self.send_json(type_review_units(
                    question_type=raw_type,
                    query=raw_q,
                    status=raw_status,
                    uploaded=raw_uploaded,
                    page=raw_page,
                    page_size=raw_size,
                ))
            if decoded_path == "/api/knowledge/tree":
                qs = parse_qs(parsed.query)
                main_type = (qs.get("type") or [""])[0].strip() or None
                unit_kind = (qs.get("unit_kind") or [""])[0].strip() or None
                return self.send_json(taxonomy.knowledge_tree(main_type=main_type, unit_kind=unit_kind))
            if decoded_path == "/api/knowledge":
                qs = parse_qs(parsed.query)
                query = qs.get("q", [""])[0]
                limit = qs.get("limit", ["40"])[0]
                main_type = (qs.get("type") or [""])[0].strip() or None
                unit_kind = (qs.get("unit_kind") or [""])[0].strip() or None
                try:
                    limit = int(limit)
                except ValueError:
                    limit = 40
                return self.send_json({"version": taxonomy.knowledge_version(),
                                       "items": taxonomy.knowledge_search(query, limit, main_type=main_type,
                                                                          unit_kind=unit_kind)})
            parts = decoded_path.strip("/").split("/")
            if len(parts) == 3 and parts[:2] == ["api", "batches"]:
                return self.send_json(overview(parts[2]))
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "operations":
                from workflow import operations
                return self.send_json(operations.view(WORKFLOWS, batch_dir(parts[2])))
            if len(parts) == 5 and parts[:2] == ["api", "batches"] and parts[3] == "operations" and parts[4] == "diff":
                from workflow import operations
                return self.send_json(operations.code_repair_diff(WORKFLOWS, batch_dir(parts[2])))
            if len(parts) == 5 and parts[:2] == ["api", "batches"] and parts[3] == "operations" and parts[4] == "performance":
                from workflow import operations
                return self.send_json(operations.performance_summary(WORKFLOWS, batch_dir(parts[2])))
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "media":
                folder = batch_dir(parts[2])
                report, manifest, queue = report_for(folder)
                numbers = [int(n) for r in merged_rows(folder, report)
                           for n in (r.get("numbers") or []) if str(n).isdigit()]
                return self.send_json(media_audit.audit(folder, numbers))
            if len(parts) == 5 and parts[0] == "api" and parts[1] == "batches" and parts[3] == "media":
                folder = batch_dir(parts[2])
                filename = Path(parts[4]).name
                media = (folder / "01_source" / "media" / filename).resolve()
                if media.parent != (folder / "01_source" / "media").resolve() or not media.is_file():
                    raise ValueError("媒体文件不存在")
                content_type = MEDIA_KINDS.get(media.suffix.lower(), "application/octet-stream")
                data = media.read_bytes()
                # Desktop fetch bridges may transport text only. Keep raw media for
                # browsers/downloads and provide a JSON data URL for the review UI.
                if parse_qs(parsed.query).get("format") == ["data-url"]:
                    if not content_type.startswith("image/"):
                        raise ValueError("该文件不是图片")
                    return self.send_json({"src": "data:" + content_type + ";base64," +
                                           base64.b64encode(data).decode("ascii")})
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("X-Content-Type-Options", "nosniff")
                if content_type == "image/svg+xml":
                    self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                return self.wfile.write(data)
            if len(parts) == 5 and parts[:2] == ["api", "batches"] and parts[3] == "audio":
                folder = batch_dir(parts[2])
                filename = Path(parts[4]).name
                target = (folder / "02_audio" / filename).resolve()
                if target.parent != (folder / "02_audio").resolve() or not target.is_file():
                    raise ValueError("音频文件不存在")
                content_type = MEDIA_KINDS.get(target.suffix.lower(), "audio/mpeg")
                data = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                return self.wfile.write(data)
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "units":
                return self.send_json(units_bundle(parts[2], _readonly=True))
            if len(parts) == 5 and parts[:2] == ["api", "batches"] and parts[3] == "units":
                return self.send_json(detail(parts[2], parts[4], _readonly=True))
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "pipeline":
                return self.send_json({"name": parts[2], **advance_state(parts[2])})
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "upload-status":
                lite = parse_qs(parsed.query).get("lite") == ["1"]
                return self.send_json(upload_state(parts[2], include_unreviewed=not lite))
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "ai-supplement":
                from workflow import ai_supplement
                return self.send_json({"ok": True, **ai_supplement.read_progress(batch_dir(parts[2]))})
            if len(parts) == 3 and parts[:2] == ["api", "vendor"]:
                fname = Path(parts[2]).name
                if fname not in {"jszip.min.js", "docx-preview.min.js"}:
                    return self.send_json({"error": "not found"}, 404)
                vendor_dir = (ROOT.parent / "lib" / "vendor").resolve()
                target = (vendor_dir / fname).resolve()
                if target.parent != vendor_dir or not target.is_file():
                    return self.send_json({"error": "vendor asset missing"}, 404)
                data = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("Cache-Control", "public, max-age=86400")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                return self.wfile.write(data)
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "paper-preview":
                return self.send_json(paper_preview(parts[2]))
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "source-docx":
                info, raw = read_source_docx(parts[2])
                if parse_qs(parsed.query).get("format") == ["raw"]:
                    if raw is None:
                        return self.send_json(info, 404)
                    self.send_response(200)
                    self.send_header(
                        "Content-Type",
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    return self.wfile.write(raw)
                if raw is None:
                    return self.send_json(info)
                return self.send_json({
                    **info,
                    "base64": base64.b64encode(raw).decode("ascii"),
                })
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "material-roles":
                return self.send_json(get_material_roles(parts[2]))
            self.send_json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except (ValueError, KeyError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:  # noqa: BLE001 - 本地服务，错误直接回显
            self.send_json({"error": str(exc)}, 500)

    @serialized
    def do_POST(self):
        parsed = urlparse(self.path)
        decoded_path = unquote(parsed.path)
        parts = decoded_path.strip("/").split("/")
        if not self._check_request_security(is_post=True, decoded_path=decoded_path):
            return
        try:
            if parts == ["api", "uploads"]:
                try:
                    fields, files = parse_multipart(self)
                except PayloadTooLargeError as exc:
                    return self.send_json({"error": str(exc)}, 413)
                return self.send_json({"ok": True, **handle_upload(fields, files)})
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "advance":
                return self.send_json({"ok": True, **start_advance(parts[2], reset_budget=True)})
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length > MAX_JSON_BYTES:
                return self.send_json({"error": f"请求体超过上限（{MAX_JSON_BYTES // (1024 * 1024)} MB）"}, 413)
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("请求内容必须是对象")
            if parts[:2] == ["api", "content-agent"]:
                from workflow import content_agent
                return self.send_json(content_agent.dispatch("POST", unquote(parsed.path), payload))
            if parts == ["api", "operations", "service"]:
                from workflow import operations
                result = operations.service_heartbeat(payload.get('available'))
                if payload.get('include_work_state'):
                    result.update(operations_work_state())
                return self.send_json(result)
            if len(parts) == 5 and parts[:2] == ["api", "batches"] and parts[3] == "operations":
                from workflow import operations
                folder = batch_dir(parts[2])
                try:
                    if parts[4] == "claim":
                        return self.send_json(operations.claim(WORKFLOWS, folder, payload))
                    if parts[4] == "unfreeze":
                        return self.send_json(operations.unfreeze(WORKFLOWS, folder))
                    if parts[4] == "takeover":
                        return self.send_json(operations.takeover(WORKFLOWS, folder))
                    if parts[4] == "reconcile-unknown":
                        return self.send_json(operations.reconcile_unknown(WORKFLOWS, folder, note=payload.get("note", "")))
                    if parts[4] in {"action", "heartbeat", "progress", "release", "cancel"}:
                        if parts[4] != "action":
                            payload = {**payload, "action": parts[4]}
                        return self.send_json(operations.action(WORKFLOWS, folder, payload,
                            enqueue=lambda expected: _launch_advance(parts[2], folder, operations_expectation=expected)))
                except operations.OperationsBusy as exc:
                    return self.send_json({"code": "worker_busy", "admitted": False,
                        "retryable": True, "retry_after_ms": 500,
                        "batch_name": folder.name, "batch_dir": str(folder), "error": str(exc)}, 409)
            if len(parts) == 5 and parts[:2] == ["api", "batches"] and parts[3] == "queue" and parts[4] == "remove":
                return self.send_json({"ok": True, **remove_from_queue(parts[2], str(payload.get("reason") or ""))})
            if len(parts) == 5 and parts[:2] == ["api", "batches"] and parts[3] == "queue" and parts[4] == "restore":
                return self.send_json({"ok": True, **restore_queue(parts[2])})
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "upload":
                return self.send_json({"ok": True, **start_batch_upload(
                    parts[2], allow_unreviewed=payload.get('allow_unreviewed', False),
                    unreviewed_revision=payload.get('unreviewed_revision', ''))})
            if parts == ["api", "audio", "config"]:
                cfgmod, _, _ = _audio_modules()
                return self.send_json({"ok": True, "config": cfgmod.save(payload.get("config") or payload)})
            if parts == ["api", "audio", "bootstrap"]:
                return self.send_json({"ok": True, **start_audio_job("bootstrap", payload)})
            if parts == ["api", "audio", "segment"]:
                name = str(payload.get("name") or "").strip()
                if not name:
                    raise ValueError("请指定批次名称")
                folder = batch_dir(name)
                return self.send_json({"ok": True, **start_audio_job(
                    "segment", {"folder": str(folder), "audio": payload.get("audio")})})
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "rerun":
                return self.send_json({"ok": True, **start_rerun(parts[2], str(payload.get("mode") or ""))})
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "material-roles":
                return self.send_json(set_material_roles(parts[2], payload.get("roles") or {}, bool(payload.get("rerun"))))
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "archive":
                return self.send_json(archive_batch(parts[2], payload.get("confirm_name")))
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "purge":
                return self.send_json(purge_batch(parts[2], payload.get("confirm_name")))
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "delete":
                return self.send_json(delete_batch(parts[2], payload.get("confirm_name")))
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "decisions":
                folder = batch_dir(parts[2])
                return self.send_json({"ok": True, "item": apply_decision(folder, payload.get("item", {}), payload.get("actor"))})
            if len(parts) == 5 and parts[:2] == ["api", "batches"] and parts[3] == "media" and parts[4] == "exclusions":
                folder = batch_dir(parts[2])
                name = Path(str(payload.get("name") or "")).name
                if not name:
                    raise ValueError("缺少图片文件名")
                data = media_audit.load_exclusions(folder)
                excluded = [str(x) for x in data.get("excluded", [])]
                if payload.get("excluded"):
                    if name not in excluded:
                        excluded.append(name)
                else:
                    excluded = [x for x in excluded if x != name]
                data["excluded"] = sorted(excluded)
                if payload.get("reason"):
                    data.setdefault("notes", {})[name] = str(payload["reason"])[:200]
                data["updated_by"] = "workbench"
                data["updated_at"] = now()
                with _lock:
                    media_audit.save_exclusions(folder, data)
                return self.send_json({"ok": True, "exclusions": data})
            if len(parts) == 5 and parts[:2] == ["api", "batches"] and parts[3] == "units":
                folder = batch_dir(parts[2])
                unit_id = parts[4]
                items = payload.get("items")
                if not isinstance(items, list) or not items:
                    items = [payload.get("item")] if payload.get("item") else []
                if not all(isinstance(x, dict) for x in items):
                    raise ValueError("item/items 必须是对象")
                labels = payload.get("labels")
                if labels is not None and not isinstance(labels, dict):
                    raise ValueError("labels 必须是对象")
                unit_edit = payload.get("unit")
                if unit_edit is not None and not isinstance(unit_edit, dict):
                    raise ValueError("unit 必须是对象")
                if unit_edit is not None and not any(
                        key in unit_edit for key in ("material", *MANUAL_CONTENT_FIELDS)):
                    raise ValueError("unit 没有可保存的字段")
                if not items and not labels and not unit_edit:
                    raise ValueError("没有可保存的内容")
                expected = payload.get("expected")
                checkable = [key for key in MANUAL_CONTENT_FIELDS if unit_edit and key in unit_edit]
                if expected is not None and not isinstance(expected, dict):
                    raise ValueError("expected 必须是对象")
                if checkable and (expected is None or any(
                        not isinstance(expected.get(key), str) for key in checkable)):
                    raise ValueError("缺少这些字段修改前的正式内容，请重新打开本题")
                saved = self.save_units(folder, unit_id, items, labels, unit_edit, expected)
                return self.send_json({"ok": True, **saved})
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "ai-supplement":
                folder = batch_dir(parts[2])
                mode = str(payload.get("mode") or "").strip()
                if mode not in ("articleSummary", "explanation", "listeningScript"):
                    raise ValueError("mode 必须是 articleSummary、explanation 或 listeningScript")
                result = self.run_ai_supplement(folder, mode)
                return self.send_json({"ok": True, **result})
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "content-edit":
                from workflow import ai_supplement
                folder = batch_dir(parts[2])
                unit_id = str(payload.get("unit_id") or "").strip()
                try:
                    result = self.save_content_edit(folder, unit_id, payload)
                except ai_supplement.ContentEditConflictError as exc:
                    return self.send_json({
                        "error": str(exc),
                        "code": "content_edit_conflict",
                        "conflicts": exc.conflicts,
                    }, 409)
                return self.send_json({"ok": True, **result})
            self.send_json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:  # noqa: BLE001
            self.send_json({"error": str(exc)}, 500)

    @staticmethod
    def run_ai_supplement(folder: Path, mode: str) -> dict:
        from workflow import ai_supplement
        return ai_supplement.start_background(folder, mode)

    @staticmethod
    @serialized
    def save_content_edit(folder: Path, unit_id: str, patch: dict) -> dict:
        from contextlib import ExitStack
        from workflow import ai_supplement, supervisor
        from workflow.agent_tasks import _safe_path
        from workflow.local_agent import _batch_lock, AgentError

        if not folder.is_dir() or folder.is_symlink():
            raise ValueError("批次不存在或已归档，请刷新列表")
        run_lock = _safe_path(folder, "workflow/.run.lock")
        busy = f"批次「{folder.name}」正在运行（{folder}），本次未保存"
        with ExitStack() as locks:
            if not locks.enter_context(supervisor._try_file_lock(supervisor._auto_lock_path(folder))):
                raise ValueError(busy)
            run_lock.parent.mkdir(parents=True, exist_ok=True)
            if not locks.enter_context(supervisor._try_file_lock(run_lock)):
                raise ValueError(busy)
            try:
                locks.enter_context(_batch_lock(folder, 0))
            except AgentError as exc:
                raise ValueError(busy) from exc
            locks.enter_context(ai_supplement.content_write_lock(folder, timeout=5.0))
            result = ai_supplement.save_content_edit(folder, unit_id, patch, lock_held=True)
            from workflow import unit_anomalies
            if isinstance(patch, dict):
                if any(k in patch for k in ("explanation", "articleSummary", "subquestions")):
                    unit_anomalies.clear_stage_anomalies(folder, "explanations", [unit_id])
                if "answer" in patch or any(
                    isinstance(sq, dict) and "answer" in sq
                    for sq in (patch.get("subquestions") or [])
                ):
                    unit_anomalies.clear_stage_anomalies(folder, "answers", [unit_id])
            return result

    @staticmethod
    @serialized
    def save_units(folder: Path, unit_id: str, items: list[dict], labels: dict | None = None,
                   unit: dict | None = None, expected: dict | None = None) -> dict:
        from contextlib import ExitStack
        from workflow import supervisor
        from workflow.agent_tasks import _safe_path
        from workflow.local_agent import _batch_lock, AgentError

        if not folder.is_dir() or folder.is_symlink():
            raise ValueError("批次不存在或已归档，请刷新列表")
        run_lock = _safe_path(folder, "workflow/.run.lock")
        busy = f"批次「{folder.name}」正在运行（{folder}），本次未保存"
        with ExitStack() as locks:
            if not locks.enter_context(supervisor._try_file_lock(supervisor._auto_lock_path(folder))):
                raise ValueError(busy)
            run_lock.parent.mkdir(parents=True, exist_ok=True)
            if not locks.enter_context(supervisor._try_file_lock(run_lock)):
                raise ValueError(busy)
            try:
                locks.enter_context(_batch_lock(folder, 0))
            except AgentError as exc:
                raise ValueError(busy) from exc
            return Handler._save_units_locked(folder, unit_id, items, labels, unit, expected)

    @staticmethod
    def _save_units_locked(folder, unit_id, items, labels, unit, expected):
        from workflow.agent_tasks import _install, _safe_path

        files = {}
        manifest_rel = "workflow/manifest.json"
        manifest_path = _safe_path(folder, manifest_rel)
        current = wf_manifest.load(folder) if wf_manifest is not None and manifest_path.is_file() else None
        if unit:
            try:
                payload = load(_safe_path(folder, "06_batch/batch_meta.json"))
            except (OSError, ValueError) as exc:
                raise ValueError(f"批次「{folder.name}」缺少可编辑的正式批次（{exc}），未保存") from exc
        else:
            payload = None
        applied = apply_manual_content(folder, unit_id, unit, expected, files, current, payload, bool(unit))
        rows = merged_rows(folder, report_for(folder))
        confirmed = load(folder / "01_source/structure.confirmed.json", {}) or {}
        scoped_units = [u for u in confirmed.get("units", []) if isinstance(u, dict)
                        and u.get("identity_version") == "scoped-v1"]
        scoped = bool(scoped_units) or any(r.get("identity_version") == "scoped-v1" for r in rows)
        selected = next((u for u in scoped_units if str(u.get("unit_id")) == str(unit_id)), None)
        if scoped and selected is None:
            selected = next((r for r in rows if r.get("identity_version") == "scoped-v1"
                             and str(r.get("unit_id")) == str(unit_id)), None)
        if scoped and selected is None:
            raise ValueError("分节重号批次必须指定有效 unit_id，不能按小题号保存")
        by_number = candidates_by_number(folder, unit_id=unit_id if scoped else None)
        owner = {}
        for row in ([selected] if scoped else rows):
            for number in row.get("numbers") or []:
                owner[str(number)] = row.get("unit_id")
        if scoped:
            for item in items:
                number = str(item.get("number") or "").strip()
                if number not in owner:
                    raise ValueError("小题号不属于指定 unit_id，拒绝跨大题保存")
                if number not in by_number and not (selected.get("question_ids") or {}).get(number):
                    raise ValueError("指定 unit_id 缺少小题身份，拒绝按全局题号建草稿")
        saved = []
        warnings = []
        with _lock:
            if unit:
                # 单元级正文/解析/文章大意：写入单元草稿候选，candidate() 优先读取它；
                # 不直接改 batch.json 正式清单。
                # 解析/大意直接落到正式批次与解析产物；草稿只保留材料，避免同一字段两份来源。
                allowed = {k: unit[k] for k in ("material",) if k in unit}
                if allowed:
                    safe = str(unit_id).replace("/", "_").replace("\\", "_")
                    target = folder / "candidates" / f"{safe}.json"
                    merged = dict(load(target, {}))
                    merged.setdefault("unit_id", str(unit_id))
                    # 不带 number：单元级正文/解析文件不应被 candidates_by_number 当作某道
                    # 小题的候选索引，避免覆盖该小题的题面草稿。
                    merged.pop("number", None)
                    merged.update({k: ("" if v is None else str(v)) for k, v in allowed.items()})
                    merged["updated_by"] = "workbench"
                    merged["updated_at"] = now()
                    relative = target.relative_to(folder).as_posix()
                    files[relative] = merged
                    saved.append(relative)
            if labels:
                target = unit_label_path(folder, unit_id)
                merged = dict(load(target, {}))
                for key in ("type", "subtype", "subtypes", "difficulty", "difficultyValue", "categories",
                            "knowledge", "source", "source_type", "year", "location"):
                    if key in labels:
                        merged[key] = labels[key]
                subtypes = merged.get("subtypes") or ([merged["subtype"]] if merged.get("subtype") else [])
                matched_row = next((row for row in rows if str(row.get("unit_id")) == str(unit_id)), {})
                main_type = merged.get("type") or matched_row.get("type") or matched_row.get("question_type")
                numbers = [str(n) for n, own in owner.items() if str(own) == str(unit_id)]
                unit_kind = (matched_row.get("unit_kind") or labels.get("unit_kind")
                             or ("group" if len(numbers) > 1 else "standalone"))
                knowledge = taxonomy.require_knowledge(merged.get("knowledge", []), main_type, unit_kind=unit_kind)
                merged["knowledge"] = knowledge
                resolved = taxonomy.resolve_knowledge(knowledge, main_type, unit_kind=unit_kind)
                # 清空或改成未知路径时也同步清空旧 ID，避免名字与关联脱节。
                merged["knowledgeIds"] = [point["id"] for point in resolved["resolved"]]
                filters = dict(merged.get("filterMetadata") or {})
                filters["subtypes"] = subtypes
                filters["categories"] = [str(x) for x in (merged.get("categories") or []) if str(x).strip()]
                if "difficultyValue" in merged:
                    filters["difficultyValue"] = merged["difficultyValue"]
                merged["filterMetadata"] = filters
                found = taxonomy.check_labels({
                    "type": merged.get("type"), "subtypes": subtypes,
                    "difficulty": merged.get("difficulty"), "categories": filters["categories"],
                    "knowledge": knowledge, "unit_kind": unit_kind,
                }, unit_kind=unit_kind)
                if unit_kind == "group":
                    found += taxonomy.check_unit_dims(merged.get("type"), knowledge, unit_kind="group")
                else:
                    found += taxonomy.check_per_question_required(merged.get("type"), knowledge)
                merged["labelWarnings"] = found
                merged["unit_id"] = str(unit_id)
                merged["applies_to"] = "unit"
                merged["numbers"] = sorted(numbers, key=lambda x: int(x)) if numbers else []
                merged["inherit_subquestions"] = len(merged["numbers"]) > 1
                merged["updated_by"] = "workbench"
                merged["updated_at"] = now()
                relative = target.relative_to(folder).as_posix()
                files[relative] = merged
                saved.append(relative)
                warnings.extend(f"单元 {unit_id}：{text}" for text in found)
            for item in items:
                number = str(item.get("number") or "").strip()
                if not number.isdigit():
                    raise ValueError("缺少合法小题号")
                existing, path = by_number.get(number, ({}, None))
                if path is None:
                    # 新建草稿用题号命名，绝不复用 candidates 里已有的 LOCAL-NN（那是序号，不是题号）
                    stem_name = (str((selected.get("question_ids") or {}).get(number) or "")
                                 if scoped else f"LOCAL-Q{int(number):02d}")
                    if not stem_name:
                        raise ValueError("指定 unit_id 缺少小题身份，拒绝按全局题号建草稿")
                    stem_name = stem_name.replace("/", "_").replace("\\", "_")
                    path = folder / "candidates" / f"{stem_name}.json"
                    index = 2
                    while path.exists():
                        path = folder / "candidates" / f"{stem_name}-{index}.json"
                        index += 1
                    existing = {"unit_id": owner.get(number) or f"LOCAL-Q{int(number):02d}", "number": number}
                if number in by_number:
                    # 新建草稿时 existing 只是索引占位，不能当作旧值比较。
                    merged = {key: item[key] for key in ("stem", "options", "answer") if key in item}
                else:
                    merged = dict(existing)
                    for key in ("stem", "options", "answer"):
                        if key in item:
                            merged[key] = item[key]
                merged["updated_by"] = "workbench"
                merged["updated_at"] = now()
                relative = path.relative_to(folder).as_posix()
                files[relative] = merged
                saved.append(relative)
        if unit and "material" in unit:
            applied["skipped"].append("material")
            warnings.append("材料仅保存为草稿")
        if items:
            applied["skipped"].append("items")
            warnings.append("小题改动仅保存为草稿")
        if files and current is not None:
            if not applied["applied"] and (unit or items):
                wf_manifest.invalidate_downstream(folder, current, "review", "工作台编辑了题面，旧审核结果失效")
            current["updated_at"] = wf_manifest.now_iso()
            files[manifest_rel] = current
        if files:
            _install(folder, files)
            from workflow import unit_anomalies
            if labels and "knowledge" in labels:
                unit_anomalies.clear_stage_anomalies(folder, "knowledge", [unit_id])
            if applied.get("applied") or (unit and any(k in unit for k in ("explanation", "articleSummary"))):
                unit_anomalies.clear_stage_anomalies(folder, "explanations", [unit_id])
            # 小题改动（含 answer）只落 candidates/ 草稿：未经答案来源绑定与原文一致性校验，
            # 也未写回正式答案产物。此处绝不解除 answers 隔离，否则上传闸门会放过「无答案」占位；
            # answers 隔离只在正式答案写入（save_content_edit）或阶段重新导入通过校验后清除。
            if (items and any(isinstance(item, dict) and "answer" in item for item in items)
                    and unit_id in unit_anomalies.quarantined_unit_ids(folder, "answers")):
                warnings.append("答案改动仅为草稿，答案提取隔离仍保留；写入正式答案后才解除，解除前不能上传")
        return {"saved": saved, "warnings": warnings, "applied": applied}


_workflow_supervisor = None


def prune_changelog(now_ts=None):
    """删除超过保留期的 CHANGELOG 记录（默认 2 天），返回结构化结果。

    只删除形如 YYYY-MM-DD-*.md 的记录文件；README.md / RULES.md 与其它非
    日期文件永不删除。结果记入 _changelog_prune_state 便于诊断。删除失败只
    记录，不抛出，避免后台线程或接口因个别文件失败而中断。
    """
    from workflow import changelog_retention
    result = changelog_retention.prune(
        CHANGELOG_DIR, keep_days=CHANGELOG_KEEP_DAYS,
        tz=os.environ.get("WF_CHANGELOG_TZ") or "Asia/Shanghai",
    )
    _changelog_prune_state["last_result"] = result
    _changelog_prune_state["last_run"] = now_ts if now_ts is not None else now()
    return result


def _changelog_prune_loop():
    """后台周期清理：启动即清一次，之后按间隔重复，直到服务器停止。"""
    while not _changelog_prune_halt.is_set():
        try:
            prune_changelog()
        except Exception:  # noqa: BLE001 - 清理失败绝不拖垮工作台
            pass
        _changelog_prune_halt.wait(CHANGELOG_PRUNE_INTERVAL_SECONDS)


def _wake_supervisor():
    """Resume the idle-paused watchdog when new work appears (never raises)."""
    try:
        if _is_split_worker():
            _remote_worker().wake_supervisor()
            return
        if _workflow_supervisor is not None:
            _workflow_supervisor.wake()
    except Exception:  # noqa: BLE001 - a wake failure must not block uploads/advance
        pass


def serve(http_server):
    """Start background recovery only with an actual server lifecycle."""
    global _workflow_supervisor
    if _is_split_worker():
        try:
            http_server.serve_forever()
        finally:
            http_server.server_close()
        return
    from dataclasses import replace as _dc_replace
    from workflow import supervisor as wf_supervisor
    from workflow.supervisor import SupervisorConfig, WorkflowSupervisor
    max_concurrent = _max_concurrent_batches()
    sup_cfg = SupervisorConfig.from_env()
    if max_concurrent > sup_cfg.max_dispatch_per_tick:
        sup_cfg = _dc_replace(sup_cfg, max_dispatch_per_tick=max_concurrent)
    supervisor = WorkflowSupervisor(WORKFLOWS, _dispatch_supervised,
        is_active=lambda name: bool((_advance_state.get(name) or {}).get("running")
                                   or (_upload_jobs.get(name) or {}).get("running")),
        config=sup_cfg,
        busy=lambda f: wf_supervisor.execution_busy(f, include_global_agent=(max_concurrent <= 1)))
    _workflow_supervisor = supervisor
    # CHANGELOG 保留：随服务器生命周期启停周期清理线程（超过两天自动删除）。
    _changelog_prune_halt.clear()
    prune_thread = threading.Thread(target=_changelog_prune_loop, daemon=True,
                                    name="workbench-changelog-prune")
    prune_thread.start()
    try:
        supervisor.start()
        http_server.serve_forever()
    finally:
        _changelog_prune_halt.set()
        supervisor.stop()
        _workflow_supervisor = None
        http_server.server_close()


def main():
    parser = argparse.ArgumentParser(description="木牍录题 REST API 后端（无页面）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8791)
    args = parser.parse_args()
    print(f"Workbench: http://{args.host}:{args.port}")
    # 内置 SSH 配置：后端启动即尝试建立本机隧道，让目录读取和上传都不再要求
    # 用户手工执行 ssh -L。失败不阻断后端，上传时会再次尝试并返回安全错误。
    def _warm_tunnel():
        try:
            from workbench import ssh_tunnel
            ssh_tunnel.ensure_upload_connection(timeout=8)
        except Exception:  # noqa: BLE001 - 启动预热失败不影响本地工作台
            pass
    threading.Thread(target=_warm_tunnel, daemon=True).start()
    # 页面第一次拉 /api/batches 时不要再现算 13 批投影（冷启约 9s，接近客户端 15s
    # 上限）；启动后在后台预热一次，首屏直接命中缓存。
    threading.Thread(target=warm_batch_list, daemon=True, name="warm-batches").start()
    class WorkbenchHTTPServer(ThreadingHTTPServer):
        request_queue_size = 64
        allow_reuse_address = True
        daemon_threads = True
    serve(WorkbenchHTTPServer((args.host, args.port), Handler))


if __name__ == "__main__":
    main()
