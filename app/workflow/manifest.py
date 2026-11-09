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

from . import contracts, execution_state

MANIFEST_VERSION = "1.0"
DELIVERY_VERSION = "1.0"
DELIVERY_FILENAME = "delivery.json"

# Hashes are requested repeatedly while the scheduler inspects dependencies and
# review evidence. Cache only by filesystem metadata; any size/mtime change
# automatically forces a fresh digest, so validation semantics stay unchanged.
_FILE_DIGEST_CACHE = {}
_FILE_DIGEST_CACHE_LIMIT = 4096

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

# Public aliases keep every existing importer and on-disk status compatible.
PENDING = execution_state.PENDING
RUNNING = execution_state.RUNNING
COMPLETED = execution_state.COMPLETED
APPROVED = execution_state.APPROVED
NEEDS_REVIEW = execution_state.NEEDS_REVIEW
BLOCKED = execution_state.BLOCKED
FAILED = execution_state.FAILED
INVALIDATED = execution_state.INVALIDATED
SKIPPED = execution_state.SKIPPED

TERMINAL_OK = execution_state.TERMINAL_OK
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
    try:
        stat_result = path.stat()
    except OSError:
        return ""
    if not path.is_file():
        return ""
    key = (str(path.resolve()), stat_result.st_size, stat_result.st_mtime_ns)
    cached = _FILE_DIGEST_CACHE.get(key)
    if cached is not None:
        return cached
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    # Keep only a bounded process-local cache. Old metadata keys are harmless,
    # but clearing prevents a long-lived backend from retaining every revision.
    if len(_FILE_DIGEST_CACHE) >= _FILE_DIGEST_CACHE_LIMIT:
        _FILE_DIGEST_CACHE.clear()
    _FILE_DIGEST_CACHE[key] = value
    return value


def load(batch_dir) -> dict:
    path = manifest_path(batch_dir)
    if not path.is_file():
        raise FileNotFoundError(f"找不到批次清单：{path}（先运行 init）")
    value = json.loads(path.read_text(encoding="utf-8"))
    # New local stages are pending on older manifests; never infer completion
    # from pre-existing files or erase any historical stage/decision.
    stages = value.setdefault("stages", {})
    for stage in contracts.STAGES:
        historical_upload = (stage.id in ("preassemble", "format_gate")
                             and stages.get("upload", {}).get("status") == COMPLETED)
        historical_assembly = (stage.id == "preassemble"
                               and stages.get("assemble", {}).get("status") == COMPLETED
                               and stages.get("review", {}).get("status") == COMPLETED
                               and (Path(batch_dir) / "06_batch/batch.json").is_file()
                               and not (Path(batch_dir) / "workflow/batch-fill-state.json").is_file())
        skip = historical_upload or historical_assembly
        note = ("历史批次已提交远端，不补跑新增本地步骤" if historical_upload else
                "历史批次已完成组装与审核执行，沿用现有批次，不补跑前置组装" if historical_assembly else "")
        stages.setdefault(stage.id, {"status": SKIPPED if skip else PENDING,
                                    "input_hashes": {}, "output_hashes": {}, "notes": note})
    return value


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


def input_hashes(batch_dir, manifest: dict, stage: contracts.Stage, *, raw: bool = False) -> dict:
    """阶段声明依赖的输入哈希：源文件 + 上游产物。

    ``raw=True`` returns exact SHA-256 only, used to compare legacy ledger
    records that predate normalized content digests.
    """
    return _input_fingerprint(batch_dir, manifest, stage, use_mtime=False, raw=raw)


def input_mtimes(batch_dir, manifest: dict, stage: contracts.Stage) -> dict:
    """与 input_hashes 相同的输入枚举，但取文件修改时间（廉价过期判断用）。"""
    return _input_fingerprint(batch_dir, manifest, stage, use_mtime=True)


def _input_fingerprint(batch_dir, manifest: dict, stage: contracts.Stage, *, use_mtime: bool,
                       raw: bool = False) -> dict:
    def _fp(path, missing="missing"):
        if not path or not Path(path).is_file():
            return missing
        if use_mtime:
            try:
                return Path(path).stat().st_mtime
            except OSError:
                return missing
        return sha256_file(path) if raw else _content_digest(path)
    hashes = {}
    for key in stage.requires:
        item = manifest.get("inputs", {}).get(key)
        if item:
            path = item.get("path")
            hashes[f"input:{key}"] = _fp(path)
    extra_inputs = {key: value for key, value in manifest.get('inputs', {}).items()
                    if re.fullmatch(r'attachment_\d{3,}', key)}
    if stage.id == 'audio':
        for key, item in sorted(manifest.get('inputs', {}).items()):
            if re.fullmatch(r'audio_\d{3,}', key) and isinstance(item, dict):
                path = item.get('path')
                hashes[f'input:{key}'] = _fp(path)
        # 切片证据与切片文件是本阶段自身产出的一部分，内容变了要重切。
        for rel in ('02_audio/segment-evidence.json',):
            full = Path(batch_dir) / rel
            if full.is_file():
                hashes[f'source:{rel}'] = _fp(full)
        acoustic = Path(batch_dir) / '02_audio'
        if acoustic.is_dir():
            for cut in sorted(acoustic.glob('*.mp3')):
                hashes[f'source:02_audio/{cut.name}'] = _fp(cut)
    if stage.id == 'extract':
        answer = manifest.get('inputs', {}).get('answers') or {}
        path = answer.get('path')
        hashes['input:answers'] = _fp(path)
        for key, item in sorted(extra_inputs.items()):
            path = item.get('path') if isinstance(item, dict) else None
            hashes[f'input:{key}'] = _fp(path)
    if stage.id in ('structure_gate', 'answers', 'types', 'explanations', 'knowledge', 'assemble', 'review', 'review_gate'):
        pdf_ocr = Path(batch_dir) / '01_source/pdf-ocr.json'
        hashes['direct:01_source/pdf-ocr.json'] = _fp(pdf_ocr)
    if stage.id in ('answers', 'types', 'explanations', 'knowledge', 'assemble', 'review', 'review_gate'):
        roles_path = Path(batch_dir) / '01_source/material-roles.json'
        hashes['material:roles'] = _fp(roles_path)
        for key in sorted(extra_inputs):
            path = Path(batch_dir) / '01_source' / 'materials' / (key + '.txt')
            hashes[f'material:{key}'] = _fp(path)
    if stage.id == "underlines":
        docx = manifest.get("inputs", {}).get("docx", {}).get("path")
        hashes["input:docx"] = _fp(docx)
    for dep in stage.depends_on:
        stage_spec = contracts.STAGE_BY_ID.get(dep)
        if not stage_spec:
            continue
        for rel in stage_spec.produces:
            full = Path(batch_dir) / rel
            if full.exists():
                hashes[f"artifact:{rel}"] = _fp(full)
    for rel in EXTRA_STAGE_INPUTS.get(stage.id, ()):
        full = Path(batch_dir) / rel
        hashes[f"direct:{rel}"] = _fp(full)
    # 填字段阶段直接绑定原始正文与已确认结构：换原卷/改正文/改结构立即让这些
    # 阶段失效（只对少量文件取指纹，开销可忽略），无需逐单元语义签名。
    if stage.id in ("answers", "types", "explanations", "knowledge", "audio", "assemble"):
        for rel in ("01_source/正文.txt", "01_source/structure.confirmed.json"):
            full = Path(batch_dir) / rel
            if full.is_file():
                hashes[f"source:{rel}"] = _fp(full)
        # 原始上传件（Word/PDF/答案/图片等）也直接绑定：换原卷即全链失效。
        for key, item in sorted((manifest.get("inputs") or {}).items()):
            path = item.get("path") if isinstance(item, dict) else None
            if path and Path(path).is_file():
                hashes[f"source_input:{key}"] = _fp(path)
        media_dir = Path(batch_dir) / "01_source/media"
        if media_dir.is_dir():
            for media in sorted(media_dir.glob("*")):
                if media.is_file():
                    hashes[f"source:01_source/media/{media.name}"] = _fp(media)
    if stage.id in ("explanations", "assemble"):
        # 解析/装配需要答案来源；knowledge 只需题型/结构（答案在装配合并）。
        for rel in ("01_source_ans/正文.txt", "01_source_ans/answers.json"):
            full = Path(batch_dir) / rel
            if full.is_file():
                hashes[f"source:{rel}"] = _fp(full)
    if stage.id == "assemble":
        # 装配合并结构元数据、抽取表格等；这些内容变化必须重装。
        for rel in ("01_source/extraction.json",):
            full = Path(batch_dir) / rel
            if full.is_file():
                hashes[f"source:{rel}"] = _fp(full)
    if stage.id in ("validate",):
        # 本地体检必须重核原卷：正文/结构/最终批次任何变化都要重跑 validate。
        for rel in ("01_source/正文.txt", "01_source/structure.confirmed.json",
                    "06_batch/batch_meta.json"):
            full = Path(batch_dir) / rel
            if full.is_file():
                hashes[f"source:{rel}"] = _fp(full)
    if stage.id in ("review", "review_gate"):
        from . import review
        root = Path(__file__).resolve().parents[1]
        for rel in review.ROUND_INPUTS:
            path = Path(batch_dir) / rel
            hashes[f"review:{rel}"] = _fp(path)
        for rel in review.ROUND_RULES:
            path = root / rel
            hashes[f"rule:{rel}"] = _fp(path)
        audio_sources = Path(batch_dir) / '02_audio/sources'
        if audio_sources.is_dir():
            for path in sorted(audio_sources.glob('*/full-transcript.json')):
                if path.parent.name == 'audio' or re.fullmatch(r'audio_\d{3,}', path.parent.name):
                    hashes[f'review:{path.relative_to(batch_dir).as_posix()}'] = _fp(path)
        observations = Path(batch_dir) / 'workflow/media-observations.json'
        if observations.is_file():
            try:
                from .agent_tasks import _safe_path
                data = json.loads(observations.read_text(encoding='utf-8'))
                for entry in data.get('observations', []):
                    rel = entry['path']
                    path = _safe_path(Path(batch_dir), rel)
                    hashes[f"media:{rel}"] = _fp(path)
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                hashes['media:invalid'] = 'invalid'
    return hashes


# 参与内容指纹时忽略的字段：
#   - 时间戳（每次重写都变，不代表内容变化）；
#   - 词数 / 难度等“基本标签”（用户规则：难度、词数不参与重算，以首次结果为准）。
VOLATILE_KEYS = {"generated_at", "created_at", "updated_at", "checked_at", "finished_at", "started_at",
                 "words", "word_count", "wordCount", "difficulty", "difficultyEvidence"}


def _canonical_json(value):
    """递归去掉易变时间戳字段，产出稳定可比的规范形式（键排序）。"""
    if isinstance(value, dict):
        return {k: _canonical_json(v) for k, v in sorted(value.items()) if k not in VOLATILE_KEYS}
    if isinstance(value, list):
        return [_canonical_json(v) for v in value]
    return value


def _content_digest(path) -> str:
    """内容指纹：JSON 文件按规范化结构哈希（忽略 generated_at/键序/空白），其余裸哈希。"""
    path = Path(path)
    if not path.is_file():
        return "missing"
    try:
        if path.suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            canon = json.dumps(_canonical_json(data), ensure_ascii=False, sort_keys=True)
            return "json:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()
    except (OSError, UnicodeError, ValueError):
        pass
    return "raw:" + sha256_file(path)


def output_hashes(batch_dir, stage: contracts.Stage) -> dict:
    # 保持裸 SHA256（记录/审计/上传批准对比沿用原有语义）；过期判断已简化为不做内容比对。
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
            new = {"status": "skipped", "artifacts": _delivery_artifacts(batch, stage),
                   "consumed_by": entry.get("consumed_by", []), "updated_at": now_iso()}
        elif status in TERMINAL_OK:
            missing = missing_produces(batch, stage)
            current = output_hashes(batch, stage)
            saved = record.get("output_hashes") or {}
            mismatch = bool(saved) and any(current.get(path) != digest for path, digest in saved.items())
            if mismatch and record.get("semantic_version"):
                from . import stage_semantics
                if record.get("semantic_version") == stage_semantics.VERSION:
                    mismatch = stage_semantics.output_signatures(batch, stage) != record.get("semantic_outputs")
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
                if not record.get("semantic_version"):
                    stale, _ = is_stale(batch, manifest, stage)
                    if not stale:
                        record_semantics(batch, manifest, stage)
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
        if record.get("status") in TERMINAL_OK:
            invalidate_stage(batch, manifest, stage_id,
                             "交付清单校验失败，局部重跑：" + delivery["stages"][stage_id].get("reason", "产物过期"))
            changed = True
    if changed:
        _save_delivery(batch, delivery)
    return invalidated


def record_delivery(batch_dir, manifest: dict, stage: contracts.Stage) -> None:
    """Write a delivered hand-off after a stage has installed its outputs."""
    record = manifest.get("stages", {}).get(stage.id, {})
    if (record.get("status") in TERMINAL_OK or
            record.get("execution_completed") and record.get("continue_unqualified")):
        record.pop("rebuild_required", None)
    # The hard gate is dependency_satisfied/inspect_stage at dispatch time.
    # Delivery recording must not silently accept an invalid artefact, but it
    # also must not abort the installer's bookkeeping: record the rejection.
    from .handoff import inspect_stage
    checked = inspect_stage(batch_dir, manifest, stage.id)
    if not checked.get("passed"):
        record["handoff_errors"] = checked.get("errors", [])[:8]
    if stage.id == "types" and checked.get("passed"):
        # A controlled type-only repair also updates preassembly's shared output.
        # Preserve its source delivery/status; otherwise a valid repair would make
        # preassemble stale forever because both contracts own the same types.json.
        assembled = manifest.get("stages", {}).get("preassemble", {})
        if (assembled.get("status") in (COMPLETED, APPROVED)
                and not assembled.get("rebuild_required")
                and inspect_stage(batch_dir, manifest, "preassemble").get("passed")):
            base_spec = contracts.STAGE_BY_ID["preassemble"]
            base_name = "06_batch/batch.base.json"
            saved_base = (assembled.get("semantic_outputs") or {}).get(base_name)
            if saved_base:
                from . import stage_semantics
                source_current = stage_semantics.output_signatures(batch_dir, base_spec)[base_name] == saved_base
            else:
                source_current = ((assembled.get("output_hashes") or {}).get(base_name)
                                  == sha256_file(Path(batch_dir) / base_name))
            if source_current:
                assembled["output_hashes"] = output_hashes(batch_dir, base_spec)
                record_semantics(batch_dir, manifest, base_spec)
    if stage.id in ("answers", "audio", "types", "explanations", "knowledge"):
        from . import batch_fill
        if (Path(batch_dir) / batch_fill.STATE).is_file() and (Path(batch_dir) / batch_fill.ARTIFACTS[stage.id]).is_file():
            batch_fill.sync(batch_dir, stage.id)
    record_semantics(batch_dir, manifest, stage)
    invalidate_changed_consumers(batch_dir, manifest, stage.id)
    if stage.id != "review":
        changed_review = review_bound_inputs_changed(batch_dir, manifest)
        if changed_review:
            invalidate_stage(batch_dir, manifest, "review",
                             "审核绑定输入已变化，旧审核结论作废：" + "、".join(changed_review[:8]))
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
    if status in TERMINAL_OK:
        record.pop("rebuild_required", None)
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


def record_semantics(batch_dir, manifest: dict, stage: contracts.Stage) -> None:
    """Record exact audit hashes and normalized output identity for reuse checks.

    Exact hashes remain available for task bindings and audit. Scheduling compares
    normalized JSON content so serialization-only rewrites do not reopen a stage.
    """
    from . import stage_semantics
    record = manifest["stages"][stage.id]
    record["semantic_version"] = stage_semantics.VERSION
    record["semantic_inputs"] = input_hashes(batch_dir, manifest, stage)
    record["semantic_outputs"] = stage_semantics.output_signatures(batch_dir, stage)


def invalidate_stage(batch_dir, manifest: dict, stage_id: str, reason: str) -> list[str]:
    """Rebuild one stage, retaining descendants until their consumed content changes.

    The marker blocks dependent dispatch during repair even if an intermediate
    stage still has reusable completed outputs. Explicit deep resets use
    invalidate_downstream instead.
    """
    record = manifest["stages"].get(stage_id)
    if not record:
        return []
    record["rebuild_required"] = True
    record["invalidation"] = {"scope": "stage", "reason": reason, "at": now_iso()}
    touch_stage(manifest, stage_id, INVALIDATED, notes=reason)
    return [stage_id]


def invalidate_changed_consumers(batch_dir, manifest: dict, stage_id: str) -> list[str]:
    """Recheck consumers against actual installed data, never mere ancestry."""
    changed = []
    for target in contracts.downstream_of(stage_id):
        record = manifest["stages"].get(target, {})
        if record.get("status") not in TERMINAL_OK or record.get("status") == SKIPPED:
            continue
        stale, reason = is_stale(batch_dir, manifest, contracts.STAGE_BY_ID[target])
        if stale:
            changed.extend(invalidate_stage(batch_dir, manifest, target, reason))
    return changed


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


def review_bound_inputs_changed(batch_dir, manifest: dict) -> list[str]:
    """Return changed content inputs for an already-completed review, or [].

    Review is the quality gate: a completed review that was produced from
    different source/batch bytes must not keep claiming the current batch was
    reviewed. This deliberately checks only review's own recorded binding, not
    every downstream ancestor, so re-running ordinary fill stages never causes
    a blanket cascade (see ``test_changed_producer_does_not_auto_invalidate_descendants``).
    Rule files and keys absent from the old ledger are ignored; legacy records
    stored exact SHA-256 while current records store normalized content digests,
    so each key is compared in the representation it was recorded with.
    """
    record = manifest.get("stages", {}).get("review", {})
    if record.get("status") not in TERMINAL_OK or record.get("status") == SKIPPED:
        return []
    saved = record.get("input_hashes") or {}
    if not saved:
        return []
    stage = contracts.STAGE_BY_ID["review"]
    current = input_hashes(batch_dir, manifest, stage)
    legacy = input_hashes(batch_dir, manifest, stage, raw=True)
    changed = []
    for key, old in saved.items():
        if key.startswith("rule:") or key not in current:
            continue
        expected = current[key] if str(old).startswith(("json:", "raw:")) else legacy[key]
        if old != expected:
            changed.append(key)
    return sorted(changed)


def is_stale(batch_dir, manifest: dict, stage: contracts.Stage) -> tuple[bool, str]:
    """Check reusable stage state without treating serialization as content change.

    New records compare normalized output signatures. Legacy records retain exact
    hash checks until their next accepted delivery establishes a versioned signature.
    Consumer validation and final gates remain authoritative for content correctness.
    """
    record = manifest["stages"].get(stage.id, {})
    if stage.id == "upload_gate" and record.get("status") == APPROVED:
        approved = record.get("approved_batch_sha256")
        if not approved or approved != sha256_file(Path(batch_dir) / "06_batch/batch_meta.json"):
            return True, "上传批准与冻结的最终批次不一致"
    if record.get("status") not in TERMINAL_OK:
        return True, f"状态为 {record.get('status')}"
    missing = missing_produces(batch_dir, stage)
    if missing:
        return True, f"缺少产物 {', '.join(missing)}"
    # Exact output hashes remain in the ledger for audit. Reuse compares a
    # normalized content signature so JSON formatting and clock-only changes
    # cannot create a scheduling loop; actual payload changes still require a rerun.
    saved_outputs = record.get("output_hashes") or {}
    if saved_outputs:
        from . import stage_semantics
        if record.get("semantic_version") == stage_semantics.VERSION:
            current_outputs = stage_semantics.output_signatures(batch_dir, stage)
            saved_semantics = record.get("semantic_outputs")
            if (stage.id == "preassemble" and isinstance(saved_semantics, dict)
                    and "03_types/types.json" not in saved_semantics):
                # Legacy preassembly recorded only its source skeleton. Presence
                # and consumer validation still check the newly required labels.
                current_outputs = {key: value for key, value in current_outputs.items()
                                   if key in saved_semantics}
            if current_outputs != saved_semantics:
                return True, "阶段产物内容已变化"
        else:
            for rel, digest in saved_outputs.items():
                if sha256_file(Path(batch_dir) / rel) != digest:
                    return True, f"产物已变化 {rel}"
    return False, ""


def _parse_iso(value) -> float | None:
    if not value:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return None


def dependency_satisfied(manifest: dict, stage_id: str, *, allow_unqualified: bool = False) -> bool:
    """A dependency must be complete, current, and accepted by its next consumer."""
    if not execution_state.dependency_satisfied(manifest, stage_id, allow_unqualified=allow_unqualified):
        return False
    batch_dir = manifest.get("batch_dir")
    if not batch_dir:
        return False
    stage = contracts.STAGE_BY_ID.get(stage_id)
    if stage is None:
        return False
    record = manifest.get("stages", {}).get(stage_id, {})
    if record.get("status") in (COMPLETED, APPROVED):
        stale, _reason = is_stale(batch_dir, manifest, stage)
        if stale:
            return False
    if (allow_unqualified and stage_id == "review_gate"
            and record.get("status") == execution_state.NEEDS_REVIEW
            and record.get("execution_completed") is True
            and record.get("continue_unqualified") is True
            and not record.get("release_ready")):
        return True
    # 上游都已完成才能消费（未完成/重建中→输入未稳定）。
    for dep in contracts.upstream_of(stage_id):
        dep_rec = manifest.get("stages", {}).get(dep, {})
        if dep_rec.get("status") not in TERMINAL_OK or dep_rec.get("rebuild_required"):
            return False
    # 用消费者自己的校验器确认上游交付「题号/内容对应正确、格式合规」才放行下游——
    # 这是保证“第 1 题答案填到第 1 题、组装格式正确”的正确性关卡，不是可省的哈希校验。
    # （过期判断已由 is_stale 简化为“完成+产物在”，此处不再重复内容哈希比对。）
    from .handoff import inspect_stage
    return inspect_stage(batch_dir, manifest, stage_id).get("passed") is True


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
    if (stage.id in ("preassemble", "format_gate")
            and manifest.get("stages", {}).get("upload", {}).get("status") == COMPLETED):
        return "历史批次已提交远端，不补跑新增本地步骤"
    if stage.id == "preassemble":
        batch = Path(manifest.get("batch_dir") or ".")
        stages = manifest.get("stages", {})
        if (stages.get("assemble", {}).get("status") == COMPLETED
                and stages.get("review", {}).get("status") == COMPLETED
                and (batch / "06_batch/batch.json").is_file()
                and not (batch / "workflow/batch-fill-state.json").is_file()):
            return "历史批次已完成组装与审核执行，沿用现有批次，不补跑前置组装"
    if stage.id == "answers":
        batch_dir = manifest.get("batch_dir")
        if batch_dir:
            from .optional_inputs import has_answer_source
            if not has_answer_source(batch_dir):
                return "未提供答案来源；答案按空值继续，不调用答案提取"
    if stage.id == "explanations":
        batch_dir = manifest.get("batch_dir")
        if batch_dir:
            from .optional_inputs import has_explanation_source
            if not has_explanation_source(batch_dir):
                return "未提供原解析/文章大意；按空值继续，不调用解析提取"
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
    """Compatibility entrypoint for the shared, read-only batch projection."""
    return execution_state.derive_status(manifest)


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
