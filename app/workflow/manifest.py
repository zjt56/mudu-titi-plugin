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
import re
import stat
import uuid
from datetime import datetime
from pathlib import Path

from . import contracts

MANIFEST_VERSION = "1.0"
DELIVERY_VERSION = "1.0"
DELIVERY_FILENAME = "delivery.json"

# Inputs consumed directly by a stage but not represented by its immediate
# dependency's declared outputs. Keeping these bindings here prevents stale
# review conclusions when source/labels change without a stage rerun.
EXTRA_STAGE_INPUTS = {
    "review": (
        "01_source/正文.txt", "01_source/structure.confirmed.json",
        "03_types/types.json", "04_knowledge/knowledge.json",
        "04_knowledge/explanations.json", "01_source_ans/answers.json",
        "06_batch/batch_meta.json", "02_audio/full-transcript.json",
        "02_audio/audio_map.json", "02_audio/segment-evidence.json",
    ),
    "review_gate": (
        "07_review/review-rounds.json", "07_review/content-review.json",
        "07_review/metadata-review.json", "07_review/manual-review-queue.json",
        "07_review/unchecked.json", "07_review/validation-report.json",
    ),
}

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


def delivery_path(batch_dir: Path) -> Path:
    return manifest_dir(batch_dir) / DELIVERY_FILENAME


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
            path = item.get("path")
            hashes[f"input:{key}"] = (sha256_file(path) if path and Path(path).is_file() else "missing")
    extra_inputs = {key: value for key, value in manifest.get('inputs', {}).items()
                    if re.fullmatch(r'attachment_\d{3,}', key)}
    if stage.id == 'audio':
        for key, item in sorted(manifest.get('inputs', {}).items()):
            if re.fullmatch(r'audio_\d{3,}', key) and isinstance(item, dict):
                path = item.get('path')
                hashes[f'input:{key}'] = sha256_file(path) if path and Path(path).is_file() else 'missing'
    if stage.id == 'extract':
        for key, item in sorted(extra_inputs.items()):
            path = item.get('path') if isinstance(item, dict) else None
            hashes[f'input:{key}'] = sha256_file(path) if path and Path(path).is_file() else 'missing'
    if stage.id in ('structure_gate', 'answers', 'types', 'explanations', 'knowledge', 'assemble', 'review', 'review_gate'):
        pdf_ocr = Path(batch_dir) / '01_source/pdf-ocr.json'
        hashes['direct:01_source/pdf-ocr.json'] = sha256_file(pdf_ocr) if pdf_ocr.is_file() else 'missing'
    if stage.id in ('answers', 'types', 'explanations', 'knowledge', 'assemble', 'review', 'review_gate'):
        roles_path = Path(batch_dir) / '01_source/material-roles.json'
        hashes['material:roles'] = sha256_file(roles_path) if roles_path.is_file() else 'missing'
        for key in sorted(extra_inputs):
            path = Path(batch_dir) / '01_source' / 'materials' / (key + '.txt')
            hashes[f'material:{key}'] = sha256_file(path) if path.is_file() else 'missing'
    if stage.id == "underlines":
        docx = manifest.get("inputs", {}).get("docx", {}).get("path")
        hashes["input:docx"] = sha256_file(docx) if docx and Path(docx).is_file() else "missing"
    for dep in stage.depends_on:
        stage_spec = contracts.STAGE_BY_ID.get(dep)
        if not stage_spec:
            continue
        for rel in stage_spec.produces:
            full = Path(batch_dir) / rel
            if full.exists():
                hashes[f"artifact:{rel}"] = sha256_file(full)
    for rel in EXTRA_STAGE_INPUTS.get(stage.id, ()):
        full = Path(batch_dir) / rel
        hashes[f"direct:{rel}"] = sha256_file(full) if full.is_file() else "missing"
    if stage.id in ("review", "review_gate"):
        from . import review
        root = Path(__file__).resolve().parents[1]
        for rel in review.ROUND_INPUTS:
            path = Path(batch_dir) / rel
            hashes[f"review:{rel}"] = sha256_file(path) if path.is_file() else "missing"
        for rel in review.ROUND_RULES:
            path = root / rel
            hashes[f"rule:{rel}"] = sha256_file(path) if path.is_file() else "missing"
        audio_sources = Path(batch_dir) / '02_audio/sources'
        if audio_sources.is_dir():
            for path in sorted(audio_sources.glob('*/full-transcript.json')):
                if path.parent.name == 'audio' or re.fullmatch(r'audio_\d{3,}', path.parent.name):
                    hashes[f'review:{path.relative_to(batch_dir).as_posix()}'] = sha256_file(path)
        observations = Path(batch_dir) / 'workflow/media-observations.json'
        if observations.is_file():
            try:
                from .agent_tasks import _safe_path
                data = json.loads(observations.read_text(encoding='utf-8'))
                for entry in data.get('observations', []):
                    rel = entry['path']
                    path = _safe_path(Path(batch_dir), rel)
                    hashes[f"media:{rel}"] = sha256_file(path) if path.is_file() else "missing"
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                hashes['media:invalid'] = 'invalid'
    return hashes


def output_hashes(batch_dir, stage: contracts.Stage) -> dict:
    hashes = {}
    for rel in stage.produces:
        full = Path(batch_dir) / rel
        if full.is_file():
            hashes[rel] = sha256_file(full)
    return hashes


def missing_produces(batch_dir, stage: contracts.Stage) -> list[str]:
    return [rel for rel in stage.produces if not (Path(batch_dir) / rel).is_file()]


def _delivery_artifacts(batch_dir, stage: contracts.Stage) -> list[dict]:
    root = Path(batch_dir)
    artifacts = []
    for relative in stage.produces:
        path = root / relative
        artifacts.append({
            "path": relative,
            "sha256": sha256_file(path),
            "size": path.stat().st_size if path.is_file() else 0,
            "required": True,
            "exists": path.is_file(),
        })
    return artifacts


def _load_delivery(batch_dir) -> dict:
    path = delivery_path(batch_dir)
    if not path.is_file():
        return {"version": DELIVERY_VERSION, "stages": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return {"version": DELIVERY_VERSION, "stages": {}}
    return value if isinstance(value, dict) and isinstance(value.get("stages"), dict) else {
        "version": DELIVERY_VERSION, "stages": {}
    }


def _save_delivery(batch_dir, delivery: dict) -> None:
    path = delivery_path(batch_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(delivery, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def delivery_status(batch_dir, stage_id: str) -> str:
    return str(_load_delivery(batch_dir).get("stages", {}).get(stage_id, {}).get("status") or "pending")


def reconcile_deliveries(batch_dir, manifest: dict) -> list[str]:
    """Reconcile the durable hand-off checklist without rerunning valid stages.

    Legacy manifests are migrated from their recorded output hashes. A completed
    stage with missing or changed declared outputs is invalidated locally and
    only its dependency descendants are invalidated.
    """
    batch = Path(batch_dir)
    delivery = _load_delivery(batch)
    delivery["version"] = DELIVERY_VERSION
    delivery["workflow_id"] = manifest.get("workflow_id", batch.name)
    delivery.setdefault("stages", {})
    invalidated = []
    changed = False
    for stage in contracts.STAGES:
        record = manifest.get("stages", {}).get(stage.id, {})
        status = record.get("status")
        entry = delivery["stages"].setdefault(stage.id, {})
        if status == SKIPPED:
            new = {"status": "skipped", "artifacts": [], "consumed_by": [], "updated_at": now_iso()}
        elif status in TERMINAL_OK:
            missing = missing_produces(batch, stage)
            current = output_hashes(batch, stage)
            saved = record.get("output_hashes") or {}
            mismatch = bool(saved) and any(current.get(path) != digest for path, digest in saved.items())
            if missing or mismatch:
                reason = ("交付清单发现缺少产物：" + ", ".join(missing)) if missing else "交付清单发现产物指纹变化"
                if stage.id not in invalidated:
                    invalidated.append(stage.id)
                new = {"status": "stale", "reason": reason, "artifacts": _delivery_artifacts(batch, stage),
                       "consumed_by": entry.get("consumed_by", []), "updated_at": now_iso()}
            else:
                # Legacy completed stages with files are accepted and indexed once.
                if not saved:
                    record["output_hashes"] = current
                new = {"status": "delivered", "artifacts": _delivery_artifacts(batch, stage),
                       "consumed_by": entry.get("consumed_by", []), "updated_at": entry.get("updated_at") or now_iso()}
        elif record.get("execution_completed") and record.get("continue_unqualified"):
            new = {"status": "delivered_unqualified", "artifacts": _delivery_artifacts(batch, stage),
                   "quality_status": record.get("quality_status", "pending"),
                   "release_ready": bool(record.get("release_ready")),
                   "consumed_by": entry.get("consumed_by", []), "updated_at": entry.get("updated_at") or now_iso()}
        else:
            new = {"status": "pending", "artifacts": entry.get("artifacts", []),
                   "consumed_by": entry.get("consumed_by", []), "updated_at": entry.get("updated_at") or now_iso()}
        if entry != new:
            delivery["stages"][stage.id] = new
            changed = True
    for stage_id in invalidated:
        record = manifest["stages"].get(stage_id, {})
        if record.get("status") in TERMINAL_OK and record.get("status") != SKIPPED:
            touch_stage(manifest, stage_id, INVALIDATED,
                        notes="交付清单校验失败，局部重跑：" + delivery["stages"][stage_id].get("reason", "产物过期"))
            invalidate_downstream(batch, manifest, stage_id, "上游阶段交付失效")
            changed = True
    if changed:
        _save_delivery(batch, delivery)
    return invalidated


def record_delivery(batch_dir, manifest: dict, stage: contracts.Stage) -> None:
    """Write a delivered hand-off after a stage has installed its outputs."""
    delivery = _load_delivery(batch_dir)
    delivery["version"] = DELIVERY_VERSION
    delivery["workflow_id"] = manifest.get("workflow_id", Path(batch_dir).name)
    stage_record = manifest.get("stages", {}).get(stage.id, {})
    qualified = stage_record.get("status") in TERMINAL_OK
    delivery.setdefault("stages", {})[stage.id] = {
        "status": "delivered" if qualified else "delivered_unqualified",
        "quality_status": stage_record.get("quality_status", "passed" if qualified else "pending"),
        "release_ready": bool(stage_record.get("release_ready", qualified)),
        "artifacts": _delivery_artifacts(batch_dir, stage),
        "input_hashes": input_hashes(batch_dir, manifest, stage),
        "consumed_by": [],
        "updated_at": now_iso(),
    }
    _save_delivery(batch_dir, delivery)


def mark_delivery_consumed(batch_dir, stage_id: str) -> None:
    """Record that a downstream stage has consumed each delivered dependency."""
    delivery = _load_delivery(batch_dir)
    for dependency in contracts.STAGE_BY_ID.get(stage_id, contracts.Stage(stage_id, "", "")).depends_on:
        entry = delivery.get("stages", {}).get(dependency)
        if not isinstance(entry, dict):
            continue
        consumers = entry.setdefault("consumed_by", [])
        if stage_id not in consumers:
            consumers.append(stage_id)
    _save_delivery(batch_dir, delivery)


def touch_stage(manifest: dict, stage_id: str, status: str, **fields) -> None:
    record = manifest["stages"].setdefault(stage_id, {})
    previous = record.get("status")
    record["status"] = status
    if status not in (BLOCKED, NEEDS_REVIEW):
        record.pop("execution_completed", None)
        record.pop("continue_unqualified", None)
        record.pop("quality_status", None)
        record.pop("release_ready", None)
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
    if record.get("status") not in TERMINAL_OK and not dependency_satisfied(manifest, stage.id, allow_unqualified=True):
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


def dependency_satisfied(manifest: dict, stage_id: str, *, allow_unqualified: bool = False) -> bool:
    record = manifest.get("stages", {}).get(stage_id, {})
    if record.get("status") in TERMINAL_OK:
        return True
    return bool(allow_unqualified and record.get("status") in (BLOCKED, NEEDS_REVIEW)
                and record.get("execution_completed") and record.get("continue_unqualified"))


def ready_stages(batch_dir, manifest: dict, *, allow_unqualified: bool = False) -> list[str]:
    """依赖全部满足、可以开始执行的阶段。"""
    ready = []
    for stage in contracts.STAGES:
        record = manifest["stages"].get(stage.id, {})
        if record.get("status") in TERMINAL_OK:
            continue
        if any(not dependency_satisfied(manifest, dep, allow_unqualified=allow_unqualified)
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
    if stage.id == "audio":
        # 用户显式选择跳过听力：批次内放 02_audio/SKIP_LISTENING 标记即可，
        # 不污染全局配置，也不影响其它批次；听力音频以后可单独补切。
        batch_dir = manifest.get("batch_dir")
        if batch_dir and (Path(batch_dir) / "02_audio" / "SKIP_LISTENING").is_file():
            return "用户选择跳过听力（02_audio/SKIP_LISTENING 标记）"
        if batch_dir:
            # 只有已确认结构中的全部单元都有明确题型，才能证明本卷无听力。
            # 缺文件、空结构、非法成员或未知题型不能被当作无听力证据。
            from .agent_tasks import confirmed_units
            try:
                units = confirmed_units(Path(batch_dir))
            except (OSError, ValueError, TypeError):
                units = []
            types = [unit.get("type") for unit in units]
            if (types and all(isinstance(value, str) and value.strip() for value in types)
                    and not any(value.strip().startswith("听力") for value in types)):
                return "确认结构无听力题，无需音频切段"
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
    blocked_unqualified = [sid for sid, status in statuses.items()
                           if status in (BLOCKED, NEEDS_REVIEW)
                           and manifest.get("stages", {}).get(sid, {}).get("continue_unqualified")]
    if blocked_unqualified and all(dependency_satisfied(manifest, stage.id, allow_unqualified=True)
                                   for stage in contracts.STAGES if stage.id not in
                                   ("upload_gate", "upload", "publish_gate")):
        return "pending_confirmation"
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
