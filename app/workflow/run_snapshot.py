# -*- coding: utf-8 -*-
"""只读运行快照、变更归类与安装互斥检测。

每次批次执行启动时记录当前代码提交、规则文件摘要、Python 版本与源材料摘要，
写入 `workflow/run-snapshot.json`。同时提供：
- `classify_input_change(relative)`：区分 source_material / rules / code_version / runtime_config；
- `detect_active_batches(source_root)`：安装/热更新前检测活跃执行锁，防止覆盖运行中批次引用的源码。
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

SNAPSHOT_REL = "workflow/run-snapshot.json"
_RULE_FILES = (
    "app/references/sync-practice-rules.json",
    "app/references/boundary-guidelines.json",
    "config/model-router.json",
)
_SKIP_BATCH_DIRS = frozenset({"archives", "trash", ".workbench-trash"})


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _repo_root(source_root=None) -> Path:
    if source_root is not None:
        return Path(source_root).resolve()
    return Path(__file__).resolve().parents[2]


def _git_head(root: Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        return out.decode("utf-8", errors="replace").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def classify_input_change(relative: str) -> str:
    """将变更文件路径归类为 source_material / rules / code_version / runtime_config。"""
    norm = str(relative or "").replace("\\", "/").strip()
    lower = norm.lower()
    if lower.startswith(("01_source/", "02_audio/", "source:")):
        return "source_material"
    if (
        lower.startswith(("rules:", "app/references/", "references/"))
        or "/references/" in lower
        or lower.endswith(("sync-practice-rules.json", "boundary-guidelines.json", "model-router.json"))
    ):
        return "rules"
    if (
        lower.startswith(("app/workflow/", "app/scripts/", "lib/", "backend/", "src/"))
        or lower.endswith((".py", ".js", ".mjs", ".cjs"))
    ):
        return "code_version"
    return "runtime_config"


def capture(batch_dir, *, source_root=None) -> dict:
    """捕获批次运行的只读快照并原子写入 workflow/run-snapshot.json。"""
    batch = Path(batch_dir).resolve()
    root = _repo_root(source_root)
    rule_hashes = {}
    for rel in _RULE_FILES:
        candidate = root / rel
        if candidate.is_file():
            try:
                rule_hashes[rel] = _sha256_file(candidate)
            except OSError:
                pass
    source_hashes = {}
    src_dir = batch / "01_source"
    if src_dir.is_dir():
        for entry in sorted(src_dir.iterdir()):
            if not entry.is_file() or entry.is_symlink():
                continue
            if len(source_hashes) >= 50:
                break
            try:
                source_hashes["01_source/" + entry.name] = _sha256_file(entry)
            except OSError:
                pass
    snapshot = {
        "schema": "run-snapshot-v1",
        "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "python_version": sys.version.split()[0],
        "code_commit": _git_head(root),
        "rule_hashes": rule_hashes,
        "source_hashes": source_hashes,
    }
    target = batch / SNAPSHOT_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".run-snapshot.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)
    return snapshot


def diff_snapshots(before: dict | None, after: dict | None) -> list[dict]:
    """比较两次运行快照，返回带 change_kind 的差异列表。"""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return []
    diffs = []
    if before.get("code_commit") and after.get("code_commit") and before.get("code_commit") != after.get("code_commit"):
        diffs.append({
            "change_kind": "code_version",
            "field": "code_commit",
            "before": before.get("code_commit"),
            "after": after.get("code_commit"),
        })
    if before.get("python_version") and after.get("python_version") and before.get("python_version") != after.get("python_version"):
        diffs.append({
            "change_kind": "runtime_config",
            "field": "python_version",
            "before": before.get("python_version"),
            "after": after.get("python_version"),
        })
    for section, kind in (("rule_hashes", "rules"), ("source_hashes", "source_material")):
        old_map = before.get(section) or {}
        new_map = after.get(section) or {}
        for key in sorted(set(old_map) | set(new_map)):
            if old_map.get(key) != new_map.get(key):
                diffs.append({
                    "change_kind": kind,
                    "field": key,
                    "before": old_map.get(key),
                    "after": new_map.get(key),
                })
    return diffs


def detect_active_batches(source_root) -> list[str]:
    """检测 source_root/workflows 下持有活动执行锁的批次，供安装前互斥保护。"""
    from . import supervisor

    workflows = Path(source_root) / "workflows"
    if not workflows.is_dir() or workflows.is_symlink():
        return []
    active = []
    for entry in sorted(workflows.iterdir()):
        if entry.name in _SKIP_BATCH_DIRS or entry.name.startswith(".") or entry.is_symlink() or not entry.is_dir():
            continue
        wf_dir = entry / "workflow"
        if not wf_dir.is_dir() or wf_dir.is_symlink():
            continue
        busy = False
        for lock_path in (supervisor._auto_lock_path(entry), wf_dir / ".run.lock"):
            if not lock_path.exists():
                continue
            try:
                with supervisor._try_file_lock(lock_path) as free:
                    if not free:
                        busy = True
                        break
            except OSError:
                busy = True
                break
        if busy:
            active.append(entry.name)
    return active
