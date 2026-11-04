# -*- coding: utf-8 -*-
"""批次状态清单：manifest.json 的读写、产物哈希、状态迁移与失效。

设计原则：
1. manifest.json 是唯一状态源，判断"阶段完成"不看文件猜，看这里。
2. 任何阶段完成后记录输入哈希、产物哈希与规则版本；对不上就不复用。
3. 上游变化必须让下游失效，不能留下"看起来有文件、其实是旧结果"的混合批次。
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from datetime import datetime
from pathlib import Path

from . import contracts

MANIFEST_VERSION = "1.0"

PENDING = "pending"
RUNNING = "running"
COMPLETED = "completed"
APPROVED = "approved"
NEEDS_REVIEW = "needs_review"
BLOCKED = "blocked"
FAILED = "failed"
INVALIDATED = "invalidated"
SKIPPED = "skipped"

TERMINAL_OK = (COMPLETED, APPROVED, SKIPPED)
DONE_STATES = (COMPLETED, APPROVED, SKIPPED, FAILED, BLOCKED, INVALIDATED)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def manifest_dir(batch_dir: Path) -> Path:
    return Path(batch_dir) / "workflow"


def manifest_path(batch_dir: Path) -> Path:
    return manifest_dir(batch_dir) / "manifest.json"


def sha256_file(path) -> str:
    path = Path(path)
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(batch_dir) -> dict:
    path = manifest_path(batch_dir)
    if not path.is_file():
        raise FileNotFoundError(f"找不到批次清单：{path}（先运行 init）")
    return json.loads(path.read_text(encoding="utf-8"))


def save(batch_dir, manifest: dict) -> Path:
    manifest["updated_at"] = now_iso()
    path = manifest_path(batch_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    # Preserve write_text's behavior for symlinks, including dangling ones.
    target = path.resolve() if path.is_symlink() else path
    try:
        mode = stat.S_IMODE(target.stat().st_mode)
    except FileNotFoundError:
        mode = None
    temporary = target.parent / ("." + target.name + "." + uuid.uuid4().hex + ".tmp")
    fd = None
    try:
        # A new manifest keeps normal 0666-and-umask creation semantics.
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        if mode is not None:
            os.chmod(temporary, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = None
            handle.write(content)
        os.replace(temporary, target)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return path


def new_manifest(workflow_id: str, batch_dir, inputs: dict, rules: dict,
                 meta: dict | None = None) -> dict:
    batch_dir = Path(batch_dir)
    stages = {}
    for stage in contracts.STAGES:
        stages[stage.id] = {
            "status": PENDING,
            "started_at": None,
            "finished_at": None,
            "input_hashes": {},
            "output_hashes": {},
            "exit_code": None,
            "log": None,
            "notes": "",
        }
    return {
        "workflow_id": workflow_id,
        "manifest_version": MANIFEST_VERSION,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "status": PENDING,
        "batch_dir": str(batch_dir),
        "meta": meta or {},
        "inputs": inputs,
        "rules": rules,
        "stages": stages,
        "human_decisions": [],
        "side_effects": [],
    }


def register_input(batch_dir, key: str, path, required: bool = True) -> dict:
    batch_dir = Path(batch_dir)
    record = {
        "path": str(path),
        "sha256": sha256_file(path),
        "required": required,
        "exists": Path(path).is_file(),
    }
    return {key: record}


def input_hashes(batch_dir, manifest: dict, stage: contracts.Stage) -> dict:
    """阶段声明依赖的输入哈希：源文件 + 上游产物。"""
    hashes = {}
    for key in stage.requires:
        item = manifest.get("inputs", {}).get(key)
        if item:
            hashes[f"input:{key}"] = item.get("sha256") or ""
    for dep in stage.depends_on:
        stage_spec = contracts.STAGE_BY_ID.get(dep)
        if not stage_spec:
            continue
        for rel in stage_spec.produces:
            full = Path(batch_dir) / rel
            if full.exists():
                hashes[f"artifact:{rel}"] = sha256_file(full)
    return hashes


def output_hashes(batch_dir, stage: contracts.Stage) -> dict:
    hashes = {}
    for rel in stage.produces:
        full = Path(batch_dir) / rel
        if full.is_file():
            hashes[rel] = sha256_file(full)
    return hashes


def missing_produces(batch_dir, stage: contracts.Stage) -> list[str]:
    return [rel for rel in stage.produces if not (Path(batch_dir) / rel).exists()]


def touch_stage(manifest: dict, stage_id: str, status: str, **fields) -> None:
    record = manifest["stages"].setdefault(stage_id, {})
    previous = record.get("status")
    record["status"] = status
    if status == RUNNING:
        record["started_at"] = now_iso()
    if status in (COMPLETED, APPROVED, FAILED, BLOCKED, SKIPPED, INVALIDATED):
        record["finished_at"] = now_iso()
    for key, value in fields.items():
        record[key] = value
    if previous != status:
        record.setdefault("history", []).append({"from": previous, "to": status, "at": now_iso()})


def record_decision(manifest: dict, stage_id: str, action: str, actor: str,
                    note: str = "", evidence=None) -> None:
    manifest.setdefault("human_decisions", []).append({
        "stage": stage_id,
        "action": action,
        "actor": actor,
        "note": note,
        "evidence": list(evidence or []),
        "at": now_iso(),
    })


def invalidate_downstream(batch_dir, manifest: dict, stage_id: str, reason: str) -> list[str]:
    """让指定阶段及其全部下游失效，返回被失效的阶段。"""
    targets = (stage_id,) + contracts.downstream_of(stage_id)
    changed = []
    for target in targets:
        record = manifest["stages"].get(target)
        if not record:
            continue
        if record.get("status") in (INVALIDATED, PENDING):
            continue
        touch_stage(manifest, target, INVALIDATED, notes=reason)
        changed.append(target)
    return changed


def is_stale(batch_dir, manifest: dict, stage: contracts.Stage) -> tuple[bool, str]:
    """已完成阶段是否还能复用。"""
    record = manifest["stages"].get(stage.id, {})
    if record.get("status") == SKIPPED:
        return False, ""
    if record.get("status") not in TERMINAL_OK:
        return True, f"状态为 {record.get('status')}"
    missing = missing_produces(batch_dir, stage)
    if missing:
        return True, f"缺少产物 {', '.join(missing)}"
    saved_outputs = record.get("output_hashes") or {}
    for rel, digest in saved_outputs.items():
        current = sha256_file(Path(batch_dir) / rel)
        if current != digest:
            return True, f"产物已变化 {rel}"
    saved_inputs = record.get("input_hashes") or {}
    current_inputs = input_hashes(batch_dir, manifest, stage)
    for key, digest in saved_inputs.items():
        if current_inputs.get(key) != digest:
            return True, f"输入已变化 {key}"
    return False, ""


def ready_stages(batch_dir, manifest: dict) -> list[str]:
    """依赖全部满足、可以开始执行的阶段。"""
    ready = []
    for stage in contracts.STAGES:
        record = manifest["stages"].get(stage.id, {})
        if record.get("status") in TERMINAL_OK:
            continue
        if any(manifest["stages"].get(dep, {}).get("status") not in TERMINAL_OK
               for dep in stage.depends_on):
            continue
        if stage.requires:
            missing = [k for k in stage.requires if not manifest.get("inputs", {}).get(k, {}).get("exists")]
            if missing:
                # 缺必需输入的阶段不可执行；缺可选输入的阶段在 init 时已标记 skipped
                continue
        ready.append(stage.id)
    return ready


def skip_reason(manifest: dict, stage: contracts.Stage) -> str:
    """按输入形态判断阶段是否应当跳过（不是失败）。"""
    if stage.id == "underlines":
        docx = manifest.get("inputs", {}).get("docx", {})
        path = str(docx.get("path") or "")
        if not path.lower().endswith(".docx"):
            return "来源不是 .docx，无法从字符格式补画线"
    return ""


def input_blockers(manifest: dict) -> list[dict]:
    """因缺少必需输入而无法开始的阶段。"""
    out = []
    for stage in contracts.STAGES:
        record = manifest["stages"].get(stage.id, {})
        if record.get("status") in TERMINAL_OK:
            continue
        missing = [k for k in stage.requires
                   if not manifest.get("inputs", {}).get(k, {}).get("exists")]
        if missing and not stage.optional:
            out.append({"stage": stage.id, "title": stage.title, "missing": missing})
    return out


def blocking_stages(manifest: dict) -> list[dict]:
    """当前阻断流程的阶段及原因。"""
    blocked = []
    for stage in contracts.STAGES:
        record = manifest["stages"].get(stage.id, {})
        status = record.get("status")
        if status in (FAILED, BLOCKED):
            blocked.append({
                "stage": stage.id,
                "title": stage.title,
                "status": status,
                "reason": record.get("notes") or "无说明",
            })
    return blocked


def derive_status(manifest: dict) -> str:
    """把阶段状态收敛为批次总状态。"""
    statuses = {sid: rec.get("status") for sid, rec in manifest.get("stages", {}).items()}
    if statuses.get("publish_gate") == APPROVED:
        return "published"
    if statuses.get("upload") == COMPLETED:
        return "staged"
    if any(s in (FAILED,) for s in statuses.values()):
        return "failed"
    if any(s in (BLOCKED,) for s in statuses.values()):
        return "blocked"
    if statuses.get("review_gate") in TERMINAL_OK:
        return "pending_confirmation"
    if statuses.get("validate") in TERMINAL_OK:
        return "reviewing"
    if statuses.get("assemble") in TERMINAL_OK:
        return "assembling" if statuses.get("meta") not in TERMINAL_OK else "validating"
    if statuses.get("types") in TERMINAL_OK:
        return "enriching"
    if statuses.get("structure_gate") in TERMINAL_OK:
        return "classifying"
    if statuses.get("extract") in TERMINAL_OK:
        return "structure_review"
    return "created"


def append_log(batch_dir, entry: dict) -> None:
    path = manifest_dir(batch_dir) / "run-log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"at": now_iso(), **entry}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def resolve_media_dir(batch_dir, manifest: dict) -> str:
    custom = manifest.get("meta", {}).get("media_dir")
    if custom:
        return custom
    candidate = Path(batch_dir) / "media_upload"
    return str(candidate)


def relative(batch_dir, path) -> str:
    try:
        return os.path.relpath(str(path), str(batch_dir))
    except ValueError:
        return str(path)
