# -*- coding: utf-8 -*-
"""Review 分片级有效进展追踪（供 Watchdog 精确判定卡死，避免仅靠心跳无限续期）。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
from contextlib import contextmanager

PROGRESS_REL = "workflow/review-progress.json"
_LOCK = threading.Lock()


def _path(batch_dir) -> Path:
    return Path(batch_dir) / PROGRESS_REL


def load(batch_dir) -> dict | None:
    p = _path(batch_dir)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _save(batch_dir, data: dict) -> None:
    p = _path(batch_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".review-progress.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


def record_part_start(batch_dir, task_id: str, round_id: str, target_count: int, lease_seconds: float = 600.0, *, generation: str | None = None) -> None:
    """记录单个审核分片开始执行与调用租约。"""
    now = time.time()
    with _LOCK:
        doc = load(batch_dir) or {"schema": "review-progress-v1", "parts": {}}
        generation = str(generation or doc.get("generation") or "")
        if generation:
            doc["generation"] = generation
            doc["parts"] = {
                key: value for key, value in doc.setdefault("parts", {}).items()
                if isinstance(value, dict) and str(value.get("generation") or "") == generation
                and value.get("status") == "running"
            }
        parts = doc.setdefault("parts", {})
        parts[str(task_id)] = {
            "task_id": str(task_id),
            "generation": str(generation or doc.get("generation") or ""),
            "round_id": str(round_id),
            "target_count": int(target_count),
            "started_at": now,
            "last_progress_at": now,
            "installed_targets": 0,
            "active_lease": True,
            "lease_expires_at": now + max(30.0, float(lease_seconds)),
            "status": "running",
        }
        doc["updated_at"] = now
        _save(batch_dir, doc)


def record_part_progress(
    batch_dir,
    task_id: str,
    *,
    installed_targets: int | None = None,
    status: str | None = None,
    active_lease: bool | None = None,
    lease_seconds: float | None = None,
    generation: str | None = None,
) -> None:
    """记录单个审核分片的有效进展（如模型返回、目标安装或终态收口）。"""
    now = time.time()
    with _LOCK:
        doc = load(batch_dir) or {"schema": "review-progress-v1", "parts": {}}
        parts = doc.setdefault("parts", {})
        entry = parts.setdefault(str(task_id), {"task_id": str(task_id), "started_at": now})
        if generation is not None:
            entry["generation"] = str(generation)
        entry["last_progress_at"] = now
        if active_lease and lease_seconds is not None:
            entry["lease_expires_at"] = now + max(30.0, float(lease_seconds))
        if installed_targets is not None:
            entry["installed_targets"] = int(installed_targets)
        if status is not None:
            entry["status"] = str(status)
        if active_lease is not None:
            entry["active_lease"] = bool(active_lease)
            if not active_lease:
                entry["lease_expires_at"] = now
        doc["updated_at"] = now
        _save(batch_dir, doc)


@contextmanager
def lease(batch_dir, task_id: str, *, lease_seconds: float, generation: str | None = None, interval: float = 30.0):
    """Renew a review part lease while the model call is alive."""
    stop = threading.Event()
    def renew():
        while not stop.wait(max(1.0, float(interval))):
            try:
                record_part_progress(batch_dir, task_id, active_lease=True, lease_seconds=lease_seconds, generation=generation)
            except OSError:
                pass
    thread = threading.Thread(target=renew, daemon=True, name="review-lease")
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2.0)


def has_active_progress(batch_dir, window_seconds: float, *, now: float | None = None, generation: str | None = None) -> bool | None:
    """检查 review-progress.json 中是否存在仍在有效推进或持有有效租约的分片。

    返回 None 表示尚无分片进度文件（调用方回退到既有目录 mtime 检测）；
    返回 True 表示有分片在窗口内取得有效进展或仍持有未过期调用租约；
    返回 False 表示所有 running 分片均既无有效进展又无有效租约，可视为卡死。
    """
    doc = load(batch_dir)
    if doc is None:
        return None
    parts = doc.get("parts")
    if not isinstance(parts, dict) or not parts:
        return None
    ts = time.time() if now is None else float(now)
    active_generation = str(generation or doc.get("generation") or "")
    for entry in parts.values():
        if not isinstance(entry, dict):
            continue
        if active_generation and str(entry.get("generation") or "") != active_generation:
            continue
        if entry.get("status") != "running":
            continue
        last = float(entry.get("last_progress_at") or entry.get("started_at") or 0.0)
        if ts - last < float(window_seconds):
            return True
        if entry.get("active_lease") and ts < float(entry.get("lease_expires_at") or 0.0):
            return True
    return False
