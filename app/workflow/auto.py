# -*- coding: utf-8 -*-
"""批量自动化驱动：扫描收件箱、初始化批次、推进到下一个需要判断的节点。

三种运行方式：

    python -m workflow.auto scan  --inbox inbox
    python -m workflow.auto run   --inbox inbox --once
    python -m workflow.auto run   --inbox inbox --interval 300

确定性阶段（脚本、体检、机器闸门）由程序执行。代理阶段默认使用通用入口：

    python -m workflow.agent run --stage <阶段> --batch-dir <批次>

没有本机 CLI 配置时导出自包含任务包，等待任意 Agent 交回结果；有 local-agent.json
时使用通用命令或 DSH/Codex 可选适配器。agent-bridge.json 可覆盖各阶段调用。
上传、发布仍需人工确认；结构和审核机器闸门必须通过实际检查。
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import dataclasses
from datetime import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import contracts, manifest as mf, report as report_mod, runner as rn

SKILL_ROOT = rn.SKILL_ROOT
DEFAULT_INBOX = SKILL_ROOT / "inbox"
DEFAULT_WORKFLOWS = SKILL_ROOT / "workflows"
BRIDGE_FILE = SKILL_ROOT / "workflow" / "agent-bridge.json"
QUEUE_FILE = "_queue.json"
# 默认桥+已配置本地 Agent 时，可把就绪的 answers/audio/types 只读模型调用并行；
# 导出、校验和导入仍在 _batch_auto_lock 内串行，互不依赖（都只依赖 structure_gate）。
PARALLEL_AGENTS_ENABLED = True
# 零人工续跑：生产由宿主注入 WF_ZERO_TOUCH=1。开启后不再因 needs_review、
# 人工闸门或体检/审核闸门停住；仍不做远端上传/发布（外部不可逆）。
ZERO_TOUCH = str(os.environ.get("WF_ZERO_TOUCH", "1")).strip().lower() not in ("0", "false", "no", "off")
# 限流/额度失败后的默认冷却秒数；可用 --rate-limit-backoff 覆盖。
RATE_LIMIT_BACKOFF_SECONDS = 300.0

DOCX_SUFFIXES = (".docx", ".doc")
PDF_SUFFIXES = (".pdf",)
AUDIO_SUFFIXES = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")

ANSWER_HINTS = ("答案", "answer", "key", "解析")


# ---------------------------------------------------------------- 发现批次

def _pick(files: list[Path], suffixes: tuple, exclude=()) -> Path | None:
    for path in files:
        if path.suffix.lower() in suffixes and path not in exclude:
            return path
    return None


def _guess(folder: Path) -> dict | None:
    files = [p for p in folder.iterdir() if p.is_file()]
    if not files:
        return None
    docs = [p for p in files if p.suffix.lower() in DOCX_SUFFIXES + PDF_SUFFIXES]
    answers = [p for p in docs if any(h in p.stem.lower() for h in ANSWER_HINTS)]
    sources = [p for p in docs if p not in answers]
    source = sources[0] if sources else (docs[0] if docs else None)
    if source is None:
        return None
    audio_files = [p for p in files if p.suffix.lower() in AUDIO_SUFFIXES]
    answer = next((p for p in answers if p != source), None)
    if answer is None:
        answer = next((p for p in docs if p != source), None)
    return {
        "source": str(source),
        "answers": str(answer) if answer else None,
        "audio": str(audio_files[0]) if audio_files else None,
        "extra_audio": [str(path) for path in audio_files[1:]],
        "extra_documents": [str(path) for path in docs if path != source and path != answer],
    }


def discover(inbox: Path) -> list[dict]:
    inbox = Path(inbox)
    if not inbox.is_dir():
        return []
    found = []
    for folder in sorted(p for p in inbox.iterdir() if p.is_dir()):
        descriptor = folder / "batch.json"
        if descriptor.is_file():
            try:
                data = json.loads(descriptor.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError as exc:
                found.append({"name": folder.name, "folder": str(folder), "error": f"batch.json 无法解析：{exc}"})
                continue
            candidate = {"name": data.get("workflow_id") or folder.name,
                         "folder": str(folder), "year": data.get("year"),
                         "region": data.get("region"), "subject": data.get("subject", "english"),
                         "title": data.get("title"), "source_type": data.get("source_type"),
                         "grade": data.get("grade")}
            for key in ("source", "answers", "audio", "docx"):
                value = data.get(key)
                candidate[key] = str((folder / value).resolve()) if value else None
            candidate['extra_audio'] = [str((folder / item['path']).resolve())
                                        for item in data.get('extra_audio', [])
                                        if isinstance(item, dict) and item.get('path')]
            candidate['extra_documents'] = [str((folder / item['path']).resolve())
                                            for item in data.get('extra_documents', [])
                                            if isinstance(item, dict) and item.get('path')]
            found.append(candidate)
            continue
        guessed = _guess(folder)
        if not guessed:
            continue
        found.append({"name": folder.name, "folder": str(folder),
                      "year": None, "region": None, "subject": "english", **guessed})
    return found


# ---------------------------------------------------------------- 初始化

def ensure_batch(candidate: dict, workflows_root: Path) -> Path:
    batch_dir = Path(workflows_root) / candidate["name"]
    if mf.manifest_path(batch_dir).is_file():
        descriptor_path = batch_dir / 'attachments/batch.json'
        if descriptor_path.is_file():
            try:
                prior = json.loads(descriptor_path.read_text(encoding='utf-8-sig'))
            except (OSError, ValueError) as exc:
                raise ValueError('批次附件描述文件不可读取，无法确认试卷归属') from exc
            origin = prior.get('origin_folder') if isinstance(prior, dict) else None
            if origin and candidate.get('folder') and Path(origin).resolve() != Path(candidate['folder']).resolve():
                raise ValueError('批次名已属于另一份上传，拒绝复用已有试卷数据')
        state = mf.load(batch_dir)
        old_source = state.get('inputs', {}).get('source', {}).get('path')
        new_source = candidate.get('source')
        if old_source and new_source and Path(new_source).is_file() and (
                not Path(old_source).is_file() or mf.sha256_file(old_source) != mf.sha256_file(new_source)):
            raise ValueError('批次名已存在且原卷不同，拒绝复用已有试卷数据')
        return batch_dir
    attachment_dir = batch_dir / "attachments"
    attachment_dir.mkdir(parents=True, exist_ok=True)
    localized = {}
    by_source = {}
    extra_values = candidate.get('extra_documents') or []
    extra_audio_values = candidate.get('extra_audio') or []
    input_values = [(key, candidate.get(key)) for key in ("source", "answers", "audio", "docx")]
    input_values += [(f'attachment_{index:03d}', value) for index, value in enumerate(extra_values, 1)]
    input_values += [(f'audio_{index:03d}', value) for index, value in enumerate(extra_audio_values, 1)]
    for key, value in input_values:
        if not value:
            localized[key] = None
            continue
        source = Path(value).expanduser().resolve(strict=True)
        if not source.is_file():
            raise ValueError(f"附件不是文件：{source}")
        if source in by_source:
            localized[key] = by_source[source]
            continue
        if source.parent == attachment_dir.resolve():
            target = source
        else:
            target = attachment_dir / (key + source.suffix.lower())
            if target.exists():
                if not target.is_file() or mf.sha256_file(target) != mf.sha256_file(source):
                    raise ValueError(f"批次附件已存在且内容不同：{target.name}")
            else:
                with tempfile.NamedTemporaryFile(prefix='.attachment-', dir=attachment_dir, delete=False) as stream:
                    temporary = Path(stream.name)
                    try:
                        with source.open('rb') as original:
                            shutil.copyfileobj(original, stream)
                        stream.flush()
                    except Exception:
                        temporary.unlink(missing_ok=True)
                        raise
                try:
                    if mf.sha256_file(temporary) != mf.sha256_file(source):
                        raise ValueError(f'附件复制时发生变化：{source.name}')
                    temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)
        by_source[source] = str(target.resolve())
        localized[key] = by_source[source]
    descriptor_path = attachment_dir / "batch.json"
    if not descriptor_path.exists():
        descriptor = {"workflow_id": candidate["name"], "title": candidate.get("title"),
                      "origin_folder": str(Path(candidate['folder']).resolve()) if candidate.get('folder') else None,
                      "year": candidate.get("year"), "region": candidate.get("region"),
                      "subject": candidate.get("subject") or "english",
                      "source_type": candidate.get("source_type"), "grade": candidate.get("grade"),
                      "original_filenames": {key: Path(candidate[key]).name if candidate.get(key) else None
                                             for key in ("source", "answers", "audio")}}
        for key in ("source", "answers", "audio", "docx"):
            descriptor[key] = Path(localized[key]).name if localized.get(key) else None
        descriptor['extra_audio'] = [
            {'path': Path(localized[f'audio_{index:03d}']).name,
             'original_filename': Path(value).name}
            for index, value in enumerate(extra_audio_values, 1)]
        descriptor['extra_documents'] = [
            {'path': Path(localized[f'attachment_{index:03d}']).name,
             'original_filename': Path(value).name}
            for index, value in enumerate(extra_values, 1)]
        descriptor_path.write_text(json.dumps(descriptor, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args = argparse.Namespace(
        batch_dir=str(batch_dir),
        source=localized.get("source"),
        answers=localized.get("answers"),
        audio=localized.get("audio"),
        docx=localized.get("docx"),
        year=candidate.get("year"),
        region=candidate.get("region"),
        subject=candidate.get("subject") or "english",
        title=candidate.get("title"),
        source_type=candidate.get("source_type"),
        grade=candidate.get("grade"),
        extra_documents=[localized[f'attachment_{index:03d}'] for index in range(1, len(extra_values) + 1)],
        extra_audio=[localized[f'audio_{index:03d}'] for index in range(1, len(extra_audio_values) + 1)],
        workflow_id=candidate["name"],
    )
    code = rn.cmd_init(args)
    if code != 0:
        raise RuntimeError(f"初始化失败：{candidate['name']}")
    return batch_dir


# ---------------------------------------------------------------- 代理桥

def load_bridge(path: Path | None = None) -> dict:
    target = Path(path) if path else BRIDGE_FILE
    if not target.is_file():
        if path is not None:
            return {}
        # 新安装无需配置厂商 CLI：通用入口默认导出任务，等待外部 Agent 交回结果。
        return {stage.id: ["{python}", "-m", "workflow.agent", "run", "--stage", stage.id,
                           "--batch-dir", "{batch_dir}"]
                for stage in contracts.STAGES if stage.kind == contracts.AGENT}
    try:
        data = json.loads(target.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}
    bridge = {k: v for k, v in data.items() if isinstance(v, list) and v}
    legacy_types = ["{python}", "-m", "workflow.agent", "run", "--stage", "types",
                    "--batch-dir", "{batch_dir}"]
    if "preassemble" not in bridge and bridge.get("types") == legacy_types:
        # Migrate the previously shipped bridge without changing any configured
        # model or custom command. Explicit new assembly overrides always win.
        bridge["preassemble"] = ["{python}", "-m", "workflow.agent", "run", "--stage", "preassemble",
                                  "--batch-dir", "{batch_dir}"]
    return bridge


def _bridge_stage(stage: contracts.Stage, command: list) -> contracts.Stage:
    return dataclasses.replace(stage, kind=contracts.COMMAND, command=tuple(command))


def _default_agent_bridge(stage_id, bridge):
    return bridge.get(stage_id) == ["{python}", "-m", "workflow.agent", "run", "--stage", stage_id,
                                    "--batch-dir", "{batch_dir}"]


def local_ready_stages(batch_dir, manifest, bridge=None):
    """Read-only local candidates, not proof of successful delivery.

    The supervisor may grant one dispatch per input binding during cooldown.
    Execution still rechecks dependencies, cancellation and importer contracts.
    Custom agent bridges are never replaced by a built-in local path.
    """
    from . import answers_direct, explanations_direct, batch_fill
    batch = Path(batch_dir)
    bridge = load_bridge() if bridge is None else bridge
    ready = []
    for stage in contracts.STAGES:
        if manifest.get('stages', {}).get(stage.id, {}).get('status') in mf.TERMINAL_OK:
            continue
        if not all(mf.dependency_satisfied(manifest, dep, allow_unqualified=ZERO_TOUCH)
                   for dep in stage.depends_on):
            continue
        if stage.id == 'audio' and (mf.skip_reason(manifest, stage)
                                    or not ((manifest.get('inputs') or {}).get('audio') or {}).get('exists')):
            ready.append(stage.id)
            continue
        if stage.id in {'types', 'meta', 'underlines', 'validate', 'format_gate'}:
            ready.append(stage.id)
        elif stage.kind == contracts.AGENT and _default_agent_bridge(stage.id, bridge):
            try:
                available = (
                    stage.id == 'assemble' and (batch / batch_fill.STATE).is_file()
                    or stage.id == 'audio' and (batch / '02_audio/segment-evidence.json').is_file()
                    or stage.id == 'answers' and answers_direct.build_payload(batch) is not None
                    or stage.id == 'explanations' and explanations_direct.build_payload(batch) is not None)
                if available:
                    ready.append(stage.id)
            except (OSError, ValueError, KeyError, TypeError):
                pass
    return tuple(ready)


def _cooldown_preflight(batch_dir, stage):
    """Run only known model-free paths; never dispatch/fall back to a model."""
    from . import stage_preflight, batch_fill, assemble_local
    if stage.id != 'assemble':
        return stage_preflight.run(batch_dir, stage.id)
    if not (Path(batch_dir) / batch_fill.STATE).is_file():
        return None
    code = assemble_local.try_assemble(batch_dir)
    if code == 0:
        from .local_agent import _agent_lock
        with _agent_lock(1500):
            current = mf.load(batch_dir)
            if not rn._record_success(batch_dir, current, stage):
                code = 3
            current['status'] = mf.derive_status(current)
            mf.save(batch_dir, current)
    return (code if code is not None else 3), {'stage': stage.id, 'model_calls': 0}


# ---------------------------------------------------------------- 推进

def _parallel_ready_agents(batch_dir: Path, stages: list, config: dict, *, pending_audio=None) -> dict:
    """Export and import serially; only read-only model calls overlap."""
    from . import agent_protocol, agent_tasks, local_agent, stage_preflight
    from .agent_prepare import prepare_answers

    state = mf.load(batch_dir)
    unready = {stage.id for stage in stages
               if any(not mf.dependency_satisfied(state, dep, allow_unqualified=False)
                      for dep in stage.depends_on)}
    # Leave dependent agents for the next topological scheduler iteration.
    stages = [stage for stage in stages if stage.id not in unready]
    if not stages:
        return {}

    attempts = batch_dir / "workflow" / "agent-runs"
    attempts.mkdir(parents=True, exist_ok=True)
    prepared, outcomes, deterministic = {}, {}, set()
    # Windows byte-range locks are process-owned and do not exclude sibling
    # threads. Keep bookkeeping serialized locally as well as across processes;
    # release both locks before any model call so independent workers overlap.
    bookkeeping_lock = threading.RLock()
    for stage in stages:
        try:
            preflight = stage_preflight.run(batch_dir, stage.id)
            if preflight is not None:
                code, report = preflight
                outcomes[stage.id] = (code, json.dumps(report, ensure_ascii=False))
                deterministic.add(stage.id)
                continue
            if stage.id == "answers":
                prepare_answers(batch_dir)
            # 解析/大意与知识点需要整卷题干、材料和答案附录做题号匹配。
            # 180k 的通用传输预算会把正常的大卷误判为“证据不足”；提高本阶段
            # 的上下文预算，仍由模型返回校验和导入器做最终覆盖/分片约束。
            stage_budget = config["max_input_chars"]
            if stage.id in {"explanations", "knowledge"}:
                stage_budget = max(stage_budget, 600000)
            if stage.id in {"answers", "explanations"}:
                # This package binds inputs; only bounded partitions reach the model.
                stage_budget = sys.maxsize
            exported = agent_protocol.export_task(batch_dir, stage.id,
                                                  max_input_chars=stage_budget)
            task = agent_tasks.build_task(batch_dir, stage.id, max_input_chars=stage_budget)
            if exported["status"] != "ready" or task["status"] != "ready":
                outcomes[stage.id] = (3, "任务输入或证据不足")
                continue
            portable = json.loads((Path(exported["package_dir"]) / "task.json").read_text(encoding="utf-8"))
            expected = task["prompt"].replace("The outer object has exactly four sibling keys:",
                                              "The result object has exactly four sibling keys:")
            expected = expected.replace("notes and findings belong INSIDE the outer object",
                                        "notes and findings belong INSIDE the result object")
            if not portable["prompt"].startswith(expected + "\nFILE PROTOCOL:"):
                raise ValueError("Task context changed between export and execution")
            run_dir = Path(tempfile.mkdtemp(prefix=stage.id + "-", dir=attempts))
            prepared[stage.id] = (exported, task, run_dir)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            outcomes[stage.id] = (1, str(exc))

    def invoke(item):
        stage_id, (_, task, run_dir) = item
        # Persist actual worker activity so the UI need not pretend every ready
        # stage is already running (idle/queued/cooldown must remain distinct).
        with bookkeeping_lock, agent_protocol._agent_lock(5):
            current = mf.load(batch_dir)
            mf.touch_stage(current, stage_id, mf.RUNNING, notes="")
            mf.save(batch_dir, current)
        invoke_config = dict(config, _content_batch_dir=str(batch_dir),
                             _content_input_hashes=task.get("input_hashes", {}))
        if stage_id in {"explanations", "knowledge"}:
            invoke_config["max_input_chars"] = max(int(invoke_config.get("max_input_chars", 180000)), 600000)
        if stage_id in {"answers", "explanations"}:
            from . import label_execution
            return label_execution.run(batch_dir, stage_id, invoke_config, run_dir, coordinated_imports=True)
        return local_agent.invoke(task["prompt"], run_dir, invoke_config, permit_parallel=True, coordinated_imports=True)

    def import_outcome(stage):
        code, value = outcomes[stage.id]
        imported = False
        if code == 0 and stage.id in deterministic:
            # The shared preflight already validated and recorded delivery.
            outcomes[stage.id] = 0
            return
        if code == 0:
            try:
                exported, task, _ = prepared[stage.id]
                response_path = value
                for retry in range(1 if stage.id in {"answers", "explanations"} else int(config.get("retries", 0)) + 1):
                    envelope = json.loads(response_path.read_text(encoding="utf-8"))
                    try:
                        with bookkeeping_lock, agent_protocol._agent_lock(5):
                            agent_tasks.accept_result(batch_dir, stage.id, envelope["result"], validate_only=True)
                        break
                    except ValueError as exc:
                        if stage.id in {"answers", "explanations"} or retry >= int(config.get("retries", 0)):
                            raise
                        feedback = ("\n\n上一次返回未通过校验，错误："
                                    + str(exc)[:int(config.get("retry_feedback_chars", 1200))]
                                    + "\n请修正后重新完整返回一个 JSON 对象；不要省略单元或题号。")
                        prompt = (task["prompt"] + feedback)[:config["max_input_chars"]]
                        run_dir = Path(tempfile.mkdtemp(prefix=stage.id + "-retry-", dir=attempts))
                        response = local_agent.invoke(prompt, run_dir, config)["response"]
                        response_path = run_dir / "portable-result.json"
                        response_path.write_text(json.dumps({"protocol": agent_protocol.PROTOCOL,
                            "task_id": exported["task_id"], "result": response}, ensure_ascii=False), encoding="utf-8")
                with bookkeeping_lock:
                    receipt = agent_protocol.import_result(batch_dir, exported["task_id"], response_path)
                code = 0 if receipt["completed"] else 3
                imported = True
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
                code, value = 1, str(exc)
        if code and not imported:
            # Coordinate with protocol imports and do not overwrite a stage another
            # participant completed while the model call was running.
            limited = code == 4 or _is_rate_limited(str(value))
            if not limited and code == 5 and stage.id in prepared:
                limited = not _has_deterministic_partition_failure(prepared[stage.id][2])
            admission_cooldown = code == 4
            if code == 5:
                code = 1
            with bookkeeping_lock, agent_protocol._agent_lock(5):
                manifest = mf.load(batch_dir)
                if manifest["stages"].get(stage.id, {}).get("status") not in mf.TERMINAL_OK:
                    if limited:
                        # 429/额度是“稍后可用”，不是永久失败：回到 pending 并登记冷却，
                        # 避免 UI 上出现一片 failed、也避免自动循环立刻再撞限额。
                        if not admission_cooldown:
                            _set_cooldown(batch_dir, RATE_LIMIT_BACKOFF_SECONDS)
                        mf.touch_stage(manifest, stage.id, mf.PENDING,
                                       notes="限流/额度或模型调用异常冷却中：" + str(value)[:200])
                    else:
                        mf.touch_stage(manifest, stage.id, mf.NEEDS_REVIEW if code == 3 else mf.FAILED,
                                       exit_code=code, notes=str(value)[:300])
                    mf.save(batch_dir, manifest)
            if limited:
                outcomes[stage.id] = 4
                return
        outcomes[stage.id] = code

    from concurrent.futures import as_completed
    by_id = {stage.id: stage for stage in stages}
    executor = ThreadPoolExecutor(max_workers=min(4, max(1, len(prepared))))
    futures = {executor.submit(invoke, (sid, item)): sid for sid, item in prepared.items()}

    def collect(future):
        sid = futures[future]
        exported, _, run_dir = prepared[sid]
        try:
            response = future.result()["response"]
            envelope = {"protocol": agent_protocol.PROTOCOL, "task_id": exported["task_id"],
                        "result": response}
            result_path = run_dir / "portable-result.json"
            result_path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
            outcomes[sid] = (0, result_path)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            code = (4 if getattr(exc, 'code', '') == 'model_cooldown' else
                    5 if isinstance(exc, local_agent.AgentError) and (
                        exc.code == 'exit' and _is_upstream_call_failure(str(exc))
                        or _is_native_upstream_failure(exc.code, str(exc), exc.artifacts,
                                                       batch_dir, sid, run_dir)) else 1)
            outcomes[sid] = (code, str(exc))
        import_outcome(by_id[sid])

    audio = next((future for future, sid in futures.items() if sid == 'audio'), None)
    deferred = pending_audio is not None and audio is not None
    if deferred:
        collected = False

        def collect_audio(wait=False):
            nonlocal collected
            if collected or not wait and not audio.done():
                return {}
            try:
                collect(audio)
                return {'audio': outcomes['audio']}
            finally:
                collected = True
                executor.shutdown(wait=True)

        # Retain the exact exported task and its importer, not just a stage ID.
        # Only the lock-owning scheduler calls this closure and writes delivery.
        pending_audio.append(collect_audio)
    try:
        for future in as_completed([f for f in futures if not deferred or f is not audio]):
            collect(future)
        for stage in stages:
            if stage.id not in prepared:
                import_outcome(stage)
        return {sid: code for sid, code in outcomes.items() if not deferred or sid != 'audio'}
    finally:
        if not deferred:
            executor.shutdown(wait=True)


@contextmanager
def _batch_auto_lock(batch_dir: Path):
    """Prevent overlapping auto schedulers for one batch, including across processes."""
    key = hashlib.sha256(str(batch_dir.resolve()).encode("utf-8")).hexdigest()
    path = Path(tempfile.gettempdir()) / ("mudu-auto-" + key + ".lock")
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.seek(0, os.SEEK_END)
            if not handle.tell():
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


AUDIO_HEARTBEAT_TIMEOUT_SECONDS = 15 * 60


def _segment_lock_held(batch_dir: Path) -> bool:
    """Whether a live segmenter process still owns this batch's audio lock.

    The job status file survives a backend restart, so a dead worker otherwise
    blocks the batch for the whole heartbeat timeout. Probing the OS lock is
    exact on both POSIX (flock) and Windows (msvcrt byte-range lock); the old
    fcntl-only probe reported "held" forever on Windows, which is why a killed
    segmenter kept the batch waiting the full 15 minutes.
    """
    path = Path(batch_dir) / "02_audio" / ".segment.lock"
    if not path.is_file():
        return False
    try:
        from audio.segment import lock_is_held
    except (ImportError, OSError):
        # 探针不可用时宁可多等，也不并发启动第二个切段进程。
        return True
    try:
        return lock_is_held(Path(batch_dir))
    except OSError:
        return True


def _auto_prepare_audio(batch_dir: Path, manifest: dict) -> dict:
    """Run the real audio splitter once when a batch is advanced.

    Uploaded papers should not require a manual "生成切段" click: after the
    structure gate is confirmed, the first advance prepares cuts, then the
    audio agent stage can map them. Existing evidence is never overwritten.
    """
    stage = contracts.STAGE_BY_ID["audio"]
    record = manifest["stages"].get("audio", {})
    if record.get("status") in mf.TERMINAL_OK:
        return {"status": "done"}
    if mf.skip_reason(manifest, stage):
        return {"status": "skip"}
    inputs = manifest.get("inputs") or {}
    if not (inputs.get("audio") or {}).get("exists"):
        return {"status": "no_audio"}
    if any(manifest["stages"].get(dep, {}).get("status") not in mf.TERMINAL_OK
           for dep in stage.depends_on):
        return {"status": "waiting"}
    multiple_audio = any(re.fullmatch(r'audio_\d{3,}', key) and value.get('exists')
                         for key, value in inputs.items() if isinstance(value, dict))
    if not (inputs.get("audio") or {}).get("exists"):
        return {"status": "no_audio"}
    evidence = batch_dir / "02_audio" / "segment-evidence.json"
    if evidence.is_file() and not multiple_audio:
        from audio.consistency import pair_is_consistent, read_pair
        if pair_is_consistent(batch_dir, read_pair(batch_dir)):
            return {"status": "evidence"}
    try:
        from audio import bootstrap as audio_boot
        from audio import config as audio_config
        from audio import segment as audio_segment
    except (ImportError, OSError) as exc:
        return {"status": "multiple_audio" if multiple_audio else "unavailable",
                "message": "听力切段模块不可用：" + str(exc)[:160]}
    cfg = audio_config.load()
    if not cfg.get("enabled"):
        return {"status": "multiple_audio" if multiple_audio else "disabled",
                "message": "听力切段未启用；请在「听力切分」打开启用开关"}
    # 不强制要求 READY 标记与当前模型完全一致：模型缓存已在时切换小模型也能直接切；
    # 真正缺 venv/依赖时 segment.run 会给出明确错误，而不是在这里假装未安装。
    if not Path(audio_boot.venv_python(cfg)).is_file():
        return {"status": "multiple_audio" if multiple_audio else "not_ready",
                "message": "听力运行时未安装；请先在「听力切分」完成安装"}
    data_dir = Path(audio_config.data_dir(cfg))
    batch_name = batch_dir.name
    current = audio_boot.read_status(data_dir, batch_name)
    if current.get("running"):
        try:
            heartbeat_age = time.time() - float(current.get("updated") or 0)
        except (TypeError, ValueError):
            heartbeat_age = float("inf")
        # A job status file can outlive its worker (backend/app restart). The
        # flock is authoritative: held means really running; free means stale.
        if heartbeat_age < AUDIO_HEARTBEAT_TIMEOUT_SECONDS and _segment_lock_held(batch_dir):
            return {"status": "running", "message": "该批次已有切段任务在运行，请等它结束"}
        # A dead worker must not hold the batch forever. The next call below
        # starts a fresh run and the splitter keeps any atomic prior outputs.
        current = {"status": "stale", "heartbeat_age": heartbeat_age}

    def progress(phase, pct, detail):
        audio_boot.write_status(data_dir, batch=batch_name, job="segment", running=True, ok=None,
                                phase=phase, pct=pct, detail=detail, error="")

    audio_boot.write_status(data_dir, batch=batch_name, job="segment", running=True, ok=None,
                            phase="start", pct=0, detail="上传后自动切段", error="")
    try:
        if multiple_audio:
            analysis = audio_segment.inspect_multiple_audio(batch_dir, inputs, cfg, progress)
            if not analysis.get('complete'):
                detail = '多音频题组对应存在缺失或冲突，详见批次分析记录'
                audio_boot.write_status(data_dir, batch=batch_name, job='segment', running=False, ok=False,
                                        phase='needs_review', pct=100, detail=detail, error='')
                return {'status': 'multiple_audio', 'message': detail}
            result = audio_segment.slice_multiple_audio(batch_dir, analysis, progress)
            pending = result.get('needs_review_units') or []
            passed = result.get('ok') is True and not pending
            detail = '多音频切段已完成' if passed else '多音频候选切片已保存，边界待核对：' + '、'.join(pending[:12])
            audio_boot.write_status(data_dir, batch=batch_name, job='segment', running=False, ok=passed,
                                    phase='done' if passed else 'needs_review', pct=100, detail=detail, error='')
            return {'status': 'ready' if passed else 'needs_review', 'pending': pending}
        result = audio_segment.run(batch_dir, cfg, None, progress)
    except Exception as exc:  # noqa: BLE001 - 切段失败不能让整批推进崩溃
        audio_boot.write_status(data_dir, batch=batch_name, job="segment", running=False, ok=False,
                                phase="error", detail="", error=str(exc))
        return {"status": "multiple_audio" if multiple_audio else "error",
                "message": "自动切段失败：" + str(exc)[:200]}
    pending = result.get("needs_review_units") or []
    passed = result.get("ok") is True and not pending
    audio_boot.write_status(data_dir, batch=batch_name, job="segment", running=False, ok=passed,
                            phase="done" if passed else "needs_review", pct=100,
                            detail=("听力切段已完成" if passed else
                                    "候选切片已保存，边界待核对：" + "、".join(pending[:12])), error="")
    return {"status": "ready" if passed else "needs_review", "pending": pending}


def cancel_marker_path(batch_dir: Path) -> Path:
    """用户删除批次前写入的取消标记文件（批次目录内）。

    运行中的推进会在一阶段一开始检查它并干净退出，随后由删除流程真正清除目录。
    标记写在 workflow/ 内、随批次目录一起删除，不触碰共享缓存或用户其它数据。
    """
    return Path(batch_dir) / "workflow" / ".delete-requested"


def cancel_requested(batch_dir: Path) -> bool:
    try:
        return cancel_marker_path(batch_dir).is_file()
    except OSError:
        return False


def request_cancel(batch_dir: Path) -> None:
    """幂等地写入取消标记；正在跑的 worker 在下一阶段边界读到后停止。"""
    path = cancel_marker_path(batch_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(int(__import__("time").time())), encoding="utf-8")


def clear_cancel(batch_dir: Path) -> None:
    try:
        cancel_marker_path(batch_dir).unlink()
    except OSError:
        pass


def advance(batch_dir: Path, bridge: dict, stop_at: str | None = None) -> dict:
    batch_dir = Path(batch_dir)
    with _batch_auto_lock(batch_dir):
        return _advance_locked(batch_dir, bridge, stop_at)


def _refresh_completed_review(batch_dir, manifest):
    """Requeue invalid review evidence, never quality-only differences."""
    record = manifest["stages"].get("review", {})
    if record.get("status") not in mf.TERMINAL_OK:
        return False
    rounds_file = Path(batch_dir) / "07_review" / "review-rounds.json"
    try:
        payload = json.loads(rounds_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    # 没有实际轮次证据（旧批次/测试夹具）时不把完成态改判为失效；
    # 只有“确实执行过、但已不满足当前契约”的记录才补审。
    if not isinstance(payload, dict) or not payload.get("rounds"):
        return False
    # 上游重建后，旧审核的绑定输入已经变化：必须在进入新一轮自动推进时立刻
    # 作废旧结论，而不是等审核闸门或下次实际调用时才发现（后者会白等数小时）。
    changed = mf.review_bound_inputs_changed(batch_dir, manifest)
    if changed:
        mf.invalidate_stage(batch_dir, manifest, "review",
                            "审核绑定输入已变化，旧审核结论作废：" + "、".join(changed[:8]))
        mf.save(batch_dir, manifest)
        return True
    from . import review
    if review.evaluate_rounds(batch_dir, payload).get("execution_completed"):
        return False
    mf.invalidate_stage(batch_dir, manifest, "review", "审核记录不再满足当前审核契约，自动补审")
    mf.save(batch_dir, manifest)
    return True


def _advance_locked(batch_dir: Path, bridge: dict, stop_at: str | None = None) -> dict:
    # Web calls this lock-owned entry directly. Every entry therefore owns the
    # same bounded worker lifetime; no caller can accidentally use sync cuts.
    pending_audio = []
    with ThreadPoolExecutor(max_workers=1) as audio_worker:
        try:
            return _advance_impl(batch_dir, bridge, stop_at, audio_worker=audio_worker,
                                 pending_audio=pending_audio)
        finally:
            # Cancellation/stop/error cannot release the batch execution lock
            # while its model still owns an exported task or can write a result.
            for collect in pending_audio:
                collect(wait=True)


def _advance_impl(batch_dir: Path, bridge: dict, stop_at: str | None = None, *, audio_worker,
                  pending_audio=None) -> dict:
    from . import operations
    if operations.lease_active(batch_dir):
        return {"action": "operations_leased", "status": "waiting", "reason": "Harness 运维修复租约生效中"}
    manifest = mf.load(batch_dir)
    cooldown = _read_cooldown(batch_dir)
    recovery_wait = {}
    # Reconcile the durable hand-off checklist before selecting work. This
    # migrates valid legacy outputs and invalidates only stale descendants.
    mf.reconcile_deliveries(batch_dir, manifest)
    mf.save(batch_dir, manifest)
    manifest = mf.load(batch_dir)
    if _requeue_historical_upstream(batch_dir, manifest, bridge):
        cooldown = _read_cooldown(batch_dir)
        manifest = mf.load(batch_dir)
    # 卡点自愈：先收集诊断证据，再由已配置模型选择受限的重试动作。
    # 模型不可用或返回非法计划时只留痕，不阻断本轮自动调度。
    try:
        from . import self_heal
        healed = operations.handoff_if_available(batch_dir)
        if healed is None:
            healed = (self_heal.recover(batch_dir) if cooldown <= 0 else
                      {"changed": False, "status": "model_cooldown"})
        if healed.get("changed"):
            manifest = mf.load(batch_dir)
            print(f"  · {healed.get('stage')} AI自愈已执行动作：{healed.get('status', 'requeued')}，继续校验")
        elif healed.get("stage") and healed.get("status") in {"cooldown", "busy", "waiting_external", "failed", "recorded", "retry_budget_exhausted", "operations_pending"}:
            # A wait recorded against older inputs must not outrank a real upstream
            # repair: if the failed stage is now stale, schedule it again instead
            # of stopping this round and showing a cooldown to the user.
            spec = contracts.STAGE_BY_ID.get(healed["stage"])
            # is_stale also returns True merely because a stage is failed.
            # That is not an input change and must not bypass recovery cooldown
            # into an endless rerun of the same deterministic validation error.
            saved_inputs = (self_heal._load(batch_dir).get('last_inputs') or {}).get(healed['stage'])
            current_inputs = mf.input_hashes(batch_dir, manifest, spec) if spec else None
            stale = (healed.get('status') != 'operations_pending'
                     and saved_inputs is not None and current_inputs != saved_inputs)
            reason_text = '上游输入内容已更新' if stale else ''
            if stale:
                print(f"  · {healed['stage']} 输入已变化，忽略旧的自愈等待并重新调度（{reason_text[:60]}）")
            else:
                recovery_wait[healed["stage"]] = healed
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(f"  · 模型自愈记录失败，不影响主流程：{str(exc)[:160]}")
    blockers = []
    provisional_result = None
    parallel_done = {}

    def blocked(stage_id, reason, action="", **details):
        blockers.append({"stage": stage_id, "action": action, "reason": reason, **details})

    def dep_ready(stage_id):
        return mf.dependency_satisfied(manifest, stage_id, allow_unqualified=ZERO_TOUCH)

    # 契约已按拓扑排列：每阶段只尝试一次，阻断只影响真正依赖它的阶段。
    audio_prep = {"status": "waiting"}
    audio_future = None
    deferred_audio = False
    deferred_audio_model = False
    audio_allowed = (stop_at is None or stop_at in contracts.STAGE_ORDER
                     and contracts.STAGE_ORDER.index(stop_at) > contracts.STAGE_ORDER.index("audio"))
    stages = list(contracts.STAGES)
    for stage in stages:
        # 用户删除请求：只在阶段边界检查，让本轮干净退出、释放锁，绝不中断
        # 正在执行的阶段（那样会留下半写产物）。删除流程随后会真正清除目录。
        if cancel_requested(batch_dir):
            return _result(batch_dir, manifest, stage.id,
                           "用户请求删除批次，已在阶段边界停止", "cancelled")
        # Publish a blocker at the next safe node boundary, before a slow
        # independent branch is joined. Investigation may read frozen evidence;
        # artifact repair still requires exclusive admission in operations.
        if blockers:
            try:
                operations.handoff_if_available(batch_dir)
            except (OSError, ValueError, RuntimeError, KeyError, TypeError):
                pass  # Optional operations must never stop ordinary execution.
        if pending_audio:
            for collect in pending_audio:
                parallel_done.update(collect(wait=stage.id == 'audio' and deferred_audio_model))
            manifest = mf.load(batch_dir)
        if stage.id == 'audio' and pending_audio and 'audio' not in parallel_done:
            review_index = stages.index(contracts.STAGE_BY_ID['review'])
            stages.insert(review_index, stage)
            deferred_audio_model = True
            continue
        # Defer only the audio stage, preserving the existing consumer/import
        # checks. A slow cutter joins immediately before review, after validate.
        if stage.id == 'audio' and deferred_audio:
            audio_prep = audio_future.result()
            deferred_audio = False
        if cooldown <= 0 and audio_allowed and audio_future is None and audio_prep.get("status") == "waiting":
            deps = contracts.STAGE_BY_ID["audio"].depends_on
            if all(dep_ready(d) for d in deps):
                if audio_worker is None:
                    audio_prep = _auto_prepare_audio(batch_dir, manifest)
                else:
                    snapshot = json.loads(json.dumps(manifest))
                    audio_future = audio_worker.submit(_auto_prepare_audio, batch_dir, snapshot)
        if audio_future is not None and audio_future.done():
            audio_prep = audio_future.result()
        elif audio_future is not None:
            audio_prep = {'status': 'running', 'message': '听力切段运行中；文字流程继续'}
        if stage.id == 'audio' and audio_future is not None and not audio_future.done():
            # A second visit occurs before review; neither old files nor merely
            # scheduling a worker count as an accepted audio delivery.
            review_index = stages.index(contracts.STAGE_BY_ID['review'])
            stages.insert(review_index, stage)
            deferred_audio = True
            continue
        if stop_at and stage.id == stop_at:
            break
        if (stage.id == 'review' and manifest['stages'].get('validate', {}).get('audio_projection_refresh')
                and manifest['stages']['validate'].get('status') not in mf.TERMINAL_OK):
            # Audio-only view projection preserves existing metadata/underlines,
            # but the changed final bytes require a fresh real local validation.
            if all(dep_ready(dep) for dep in contracts.STAGE_BY_ID['validate'].depends_on):
                rn.do_validate(batch_dir, manifest)
                manifest = mf.load(batch_dir)
                if mf.dependency_satisfied(manifest, 'validate', allow_unqualified=False):
                    manifest['stages']['validate'].pop('audio_projection_refresh', None)
                    mf.save(batch_dir, manifest)
        if stage.id in recovery_wait:
            # 听力切段能力缺失（未安装/未启用）属于能力受限，不是内容错误：
            # 不能被自愈冷却长期挂住，按既有规则跳过并继续，其它阶段照常推进。
            capability_limited_audio = (
                stage.id == "audio" and ZERO_TOUCH
                and audio_prep.get("status") in {"disabled", "not_ready", "unavailable", "error",
                                                 "no_audio", "running"})
            if not capability_limited_audio:
                wait = recovery_wait[stage.id]
                reason = wait.get('reason') or (wait.get("event") or {}).get("error") or (wait.get("event") or {}).get("reason") or "AI修复已记录，等待自动重试窗口"
                wait_action = 'operations_pending' if wait.get('status') == 'operations_pending' else 'cooldown'
                blocked(stage.id, str(reason)[:300], wait_action, next_retry_at=wait.get("next_retry_at"),
                        retry_after=wait.get("retry_after"), self_heal_status=wait.get("status"))
                continue
        if stage.id in parallel_done:
            code = parallel_done[stage.id]
            if code == 4:
                cooldown = _read_cooldown(batch_dir)
                blocked(stage.id,
                        "模型调用冷却中，约 " + str(int(cooldown or RATE_LIMIT_BACKOFF_SECONDS)) + "s 后重试",
                        "cooldown", retry_after=cooldown)
            elif code:
                blocked(stage.id, "代理任务等待接单、补充证据或处理" if code == 3 else "代理桥执行失败",
                        "review" if code == 3 else "failed")
            continue
        record = manifest["stages"].get(stage.id, {})
        status = record.get("status")
        if status == mf.SKIPPED and not mf.skip_reason(manifest, stage):
            mf.touch_stage(manifest, stage.id, mf.PENDING, notes="跳过条件已消失，恢复执行")
            mf.save(batch_dir, manifest)
            status = mf.PENDING
        incremental_requeued = False
        if stage.id == "review" and _refresh_completed_review(batch_dir, manifest):
            record = manifest["stages"][stage.id]
            status = record.get("status")
        if status in mf.TERMINAL_OK:
            # 阶段级增量审计：终态但仍有缺口时只重跑当前阶段，
            # 不让下游整体失效，也不重复已完成的题目。
            if stage.kind == contracts.AGENT:
                try:
                    from . import agent_tasks
                    gaps = agent_tasks.stage_gaps(batch_dir, stage.id)
                    missing_work = [value for value in gaps.values() if value]
                except (OSError, ValueError, TypeError, KeyError):
                    gaps, missing_work = {}, []
                if missing_work:
                    retries = int(record.get("incremental_retries") or 0)
                    if retries < 2:
                        mf.invalidate_stage(batch_dir, manifest, stage.id, "增量补缺：待补题目或字段")
                        mf.touch_stage(manifest, stage.id, mf.PENDING,
                                       notes="增量补缺：" + json.dumps(gaps, ensure_ascii=False)[:500])
                        manifest["stages"][stage.id]["incremental_retries"] = retries + 1
                        mf.save(batch_dir, manifest)
                        print(f"  · {stage.id} 发现未完成部分，增量重跑：{json.dumps(gaps, ensure_ascii=False)}")
                        status = mf.PENDING
                        record = manifest["stages"][stage.id]
                        incremental_requeued = True
                    else:
                        mf.touch_stage(manifest, stage.id, mf.NEEDS_REVIEW,
                                       quality_status="incomplete", release_ready=False,
                                       notes="增量补缺达到重试上限；产物不交付下游：" + json.dumps(gaps, ensure_ascii=False)[:500])
                        mf.save(batch_dir, manifest)
                        print(f"  · {stage.id} 保留未完成字段，达到增量重试上限：{json.dumps(gaps, ensure_ascii=False)}")
                        blocked(stage.id, "增量补缺达到重试上限", "review")
                        continue
            # 内容变更只重排当前阶段；它完成后根据实际交付内容逐个检查消费者。
            # 重写同内容文件或报告时间不能让所有下游陪跑。
            if not incremental_requeued and stage.kind != contracts.HUMAN and (record.get("input_hashes") or record.get("output_hashes")):
                stale, why = mf.is_stale(batch_dir, manifest, stage)
                if stale:
                    mf.invalidate_stage(batch_dir, manifest, stage.id, why)
                    mf.save(batch_dir, manifest)
                    manifest = mf.load(batch_dir)
                    record = manifest["stages"].get(stage.id, {})
                    status = record.get("status")
                    print(f"  · {stage.id} 产物/输入已变化，失效并重跑：{why}")
                else:
                    continue
            elif not incremental_requeued:
                continue
        # An explicit/structural skip does not depend on upstream completion, so
        # honour it before reporting an upstream wait.
        if stage.kind == contracts.AGENT and rn._skip_agent_stage(batch_dir, manifest, stage):
            continue
        unmet = [d for d in stage.depends_on if not dep_ready(d)]
        if unmet:
            blocked(stage.id, "等待上游：" + "、".join(unmet), unmet_dependencies=unmet)
            continue

        if stage.kind == contracts.HUMAN:
            if ZERO_TOUCH:
                # 全自动：不让人工闸门停住流程。远端上传/发布不自动执行，
                # 只把这两个闸门标成 skipped，本地批次仍可完整产出。
                if stage.id in {"upload_gate", "publish_gate"}:
                    note = "全自动：未提交远端题库（外部不可逆，保留人工上传入口）"
                    mf.touch_stage(manifest, stage.id, mf.SKIPPED, notes=note)
                    mf.save(batch_dir, manifest)
                    print(f"  · {stage.id} 跳过（{note}）")
                    continue
                mf.touch_stage(manifest, stage.id, mf.APPROVED,
                               notes="全自动：自动确认（零人工模式）")
                mf.save(batch_dir, manifest)
                print(f"  · {stage.id} 自动确认")
                continue
            blocked(stage.id, f"等待人工确认（{stage.title}）", "human")
            continue

        if stage.kind == contracts.AGENT:
            if cooldown > 0:
                if stage.id == 'audio' and not ((manifest.get('inputs') or {}).get('audio') or {}).get('exists'):
                    # Match the existing no_audio preparation decision without
                    # starting transcription or invoking an overridden bridge.
                    mf.touch_stage(manifest, 'audio', mf.SKIPPED,
                                   notes='未提供音频，按无音频继续（audio_absent）')
                    mf.save(batch_dir, manifest)
                    continue
                local = None
                if _default_agent_bridge(stage.id, bridge):
                    try:
                        local = _cooldown_preflight(batch_dir, stage)
                    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
                        local = (3, {'reason': str(exc)})
                # The local importer owns its manifest writes. Never save the
                # scheduler's pre-import snapshot over a new delivery.
                manifest = mf.load(batch_dir)
                if local is not None:
                    code, report = local
                    if code or not mf.dependency_satisfied(manifest, stage.id, allow_unqualified=False):
                        blocked(stage.id, str(report.get('reason') or report.get('detail')
                                              or '本地候选尚未通过交付校验'), 'review')
                    continue
                blocked(stage.id, f"模型调用冷却中，约 {int(cooldown)}s 后重试", "cooldown",
                        retry_after=cooldown)
                continue
            if stage.id == "audio":
                prep_status = audio_prep.get("status")
                if prep_status == "no_audio":
                    # 用户规则：听力卷没有音频也继续，不切段、不阻断，只记缺音频。
                    mf.touch_stage(manifest, "audio", mf.SKIPPED,
                                   notes="未提供音频，按无音频继续（audio_absent）")
                    mf.save(batch_dir, manifest)
                    print("  · audio 跳过（未提供音频，继续）")
                    continue
                if prep_status == "running":
                    # 正在切段是正常等待，不能写成 needs_review 让自愈反复重排。
                    blocked("audio", str(audio_prep.get("message") or "听力切段运行中"), "waiting")
                    continue
                if prep_status in {"disabled", "not_ready", "error", "unavailable", "needs_review", "multiple_audio"}:
                    if ZERO_TOUCH and prep_status == "needs_review":
                        # 候选切片已经写出，只是边界证据不足。零人工模式下直接采用候选，
                        # 不再停在“待回听”，把未验证边界记录在阶段备注里。
                        pending = audio_prep.get("pending") or []
                        note = "全自动：采用候选切片；未验证边界 " + str(len(pending)) + " 个"
                        if pending:
                            note += "（" + "、".join(pending[:12]) + "）"
                        if (batch_dir / "02_audio" / "audio_map.json").is_file():
                            mf.touch_stage(manifest, "audio", mf.COMPLETED,
                                           input_hashes=mf.input_hashes(batch_dir, manifest, contracts.STAGE_BY_ID["audio"]),
                                           output_hashes=mf.output_hashes(batch_dir, contracts.STAGE_BY_ID["audio"]),
                                           quality_status="unverified", release_ready=False,
                                           notes=note[:600])
                            if (batch_dir / "02_audio" / "audio_map.json").is_file():
                                mf.record_delivery(batch_dir, manifest, contracts.STAGE_BY_ID["audio"])
                            mf.save(batch_dir, manifest)
                            print("  · audio 自动采用候选切片（零人工）")
                            continue
                    if ZERO_TOUCH and prep_status in {"disabled", "not_ready", "unavailable", "error"}:
                        # 切段能力不可用时不阻断整卷；后续审核会按“无音频”记录。
                        note = "全自动：听力切段跳过（" + str(audio_prep.get("message") or prep_status) + "）"
                        mf.touch_stage(manifest, "audio", mf.SKIPPED, notes=note[:600])
                        mf.save(batch_dir, manifest)
                        print("  · audio 跳过（零人工）")
                        continue
                    note = audio_prep.get("message") or (
                        "听力切段完成，但以下边界需人工回听确认：" + "、".join((audio_prep.get("pending") or [])[:20])
                        if prep_status == "needs_review" else "听力切段未完成：" + str(prep_status))
                    mf.touch_stage(manifest, "audio", mf.NEEDS_REVIEW, notes=note[:600])
                    mf.save(batch_dir, manifest)
                    blocked("audio", note, "review")
                    continue
            # 通用就绪并行批：把当前所有依赖已满足、且走默认桥的 AGENT 阶段一次性
            # 并发调用模型，再按阶段顺序导入。这样 types/answers/audio 一波、
            # explanations/knowledge 一波，四个打标签智能体都能并行参与，而不再
            # 严格四步串行（assemble 是确定性合并，单独处理，不进入并行批）。
            if PARALLEL_AGENTS_ENABLED and stage.id not in parallel_done and stage.id != "assemble":
                from . import agent, local_agent
                eligible = []
                for candidate in contracts.STAGES:
                    if candidate.kind != contracts.AGENT or candidate.id == "assemble":
                        continue
                    if contracts.STAGE_ORDER.index(candidate.id) < contracts.STAGE_ORDER.index(stage.id):
                        continue
                    if stop_at and contracts.STAGE_ORDER.index(candidate.id) >= contracts.STAGE_ORDER.index(stop_at):
                        continue
                    if candidate.id == "audio" and (pending_audio or audio_prep.get("status") not in {"ready", "evidence", "done"}):
                        continue
                    default = ["{python}", "-m", "workflow.agent", "run", "--stage", candidate.id,
                               "--batch-dir", "{batch_dir}"]
                    if (bridge.get(candidate.id) == default
                            and manifest["stages"].get(candidate.id, {}).get("status") not in mf.TERMINAL_OK
                            and candidate.id not in parallel_done
                            and candidate.id not in recovery_wait
                            and not mf.skip_reason(manifest, candidate)
                            and all(dep_ready(dep) for dep in candidate.depends_on)):
                        eligible.append(candidate)
                if (len(eligible) > 1 or stage.id == 'audio' and eligible) and agent.configured():
                    config = local_agent.load_config()
                    try:
                        if any(item.id == 'audio' for item in eligible):
                            results = _parallel_ready_agents(batch_dir, eligible, config,
                                                             pending_audio=pending_audio)
                        else:
                            results = _parallel_ready_agents(batch_dir, eligible, config)
                    except Exception as exc:  # lock contention is "busy", not a failure
                        if getattr(exc, "code", "") != "lock_timeout":
                            raise
                        return _result(batch_dir, manifest, stage.id,
                                       "另一个执行器正在处理该批次；稍后自动继续", "busy")
                    parallel_done.update(results)
                    manifest = mf.load(batch_dir)
                    if stage.id == 'audio' and pending_audio and 'audio' not in results:
                        review_index = stages.index(contracts.STAGE_BY_ID['review'])
                        stages.insert(review_index, stage)
                        deferred_audio_model = True
                        continue
                    if stage.id in results:
                        code = results[stage.id]
                        if code == 4:
                            cooldown = _read_cooldown(batch_dir)
                            blocked(stage.id,
                                    "模型调用冷却中，约 " + str(int(cooldown or RATE_LIMIT_BACKOFF_SECONDS)) + "s 后重试",
                                    "cooldown", retry_after=cooldown)
                            continue
                        if code:
                            blocked(stage.id, "代理任务等待接单、补充证据或处理" if code == 3 else "代理桥执行失败",
                                    "review" if code == 3 else "failed")
                        continue
            command = bridge.get(stage.id)
            if not command:
                blocked(stage.id, f"等待代理执行（{stage.title}）", "agent")
                continue
            print(f"  · {stage.id} 通过代理桥执行")
            executable = _bridge_stage(stage, command)
            attempts = batch_dir / 'workflow' / 'agent-runs'
            prior_runs = set(attempts.glob(stage.id + '-*')) if attempts.is_dir() else set()
            code = rn._execute_command(batch_dir, manifest, executable)
            if code != 0:
                pending = code == 3 and manifest["stages"][stage.id].get("status") == mf.NEEDS_REVIEW
                note = ""
                try:
                    note = str(mf.load(batch_dir)["stages"].get(stage.id, {}).get("notes", ""))
                except (OSError, KeyError, ValueError):
                    note = ""
                new_runs = set(attempts.glob(stage.id + '-*')) - prior_runs if attempts.is_dir() else set()
                native_upstream = False
                if not pending and _default_agent_bridge(stage.id, bridge) and new_runs:
                    try:
                        stderr = rn._log_path(batch_dir, stage.id).read_text(encoding='utf-8').rsplit('[stderr]\n', 1)[1]
                        payload = json.loads(stderr.strip().splitlines()[-1])
                        native_upstream = any(_is_native_upstream_failure(
                            payload.get('code'), payload.get('error'), payload.get('artifacts'),
                            batch_dir, stage.id, path) for path in new_runs)
                    except (OSError, ValueError, IndexError, AttributeError, TypeError):
                        pass
                upstream = (not pending and _default_agent_bridge(stage.id, bridge)
                            and (_is_upstream_call_failure(note) or native_upstream)
                            and not any(_has_deterministic_partition_failure(path) for path in new_runs))
                if not pending and (_is_rate_limited(note) or upstream):
                    delay = RATE_LIMIT_BACKOFF_SECONDS
                    _set_cooldown(batch_dir, delay)
                    cooldown = delay
                    if upstream:
                        from .agent_protocol import _agent_lock
                        with _agent_lock(5):
                            current = mf.load(batch_dir)
                            if current['stages'].get(stage.id, {}).get('status') not in mf.TERMINAL_OK:
                                mf.touch_stage(current, stage.id, mf.PENDING,
                                               notes='模型调用暂时不可用，冷却中：' + note[:200])
                                mf.save(batch_dir, current)
                            manifest = current
                    blocked(stage.id, f"模型调用暂时不可用，约 {int(delay)}s 后重试", "cooldown",
                            retry_after=delay)
                else:
                    blocked(stage.id, "代理任务等待接单、补充证据或处理" if pending else "代理桥执行失败",
                            "review" if pending else "failed")
            continue

        if stage.id == "upload":
            if ZERO_TOUCH:
                mf.touch_stage(manifest, "upload", mf.SKIPPED,
                               notes="全自动：不提交远端题库（外部不可逆）")
                mf.save(batch_dir, manifest)
                print("  · upload 跳过（零人工，不提交远端）")
                continue
            blocked(stage.id, "上传须人工通过专用命令显式确认", "human")
            continue

        if stage.kind == contracts.COMMAND:
            from .agent_protocol import _agent_lock
            with _agent_lock(1500):
                manifest = mf.load(batch_dir)
                record = manifest["stages"].get(stage.id, {})
                if record.get("status") in mf.TERMINAL_OK:
                    continue
                unmet = [d for d in stage.depends_on if not dep_ready(d)]
                if unmet:
                    blocked(stage.id, "等待上游：" + "、".join(unmet), unmet_dependencies=unmet)
                    continue
                reason = mf.skip_reason(manifest, stage)
                if not reason and stage.requires:
                    missing = [k for k in stage.requires
                               if not manifest.get("inputs", {}).get(k, {}).get("exists")]
                    if missing:
                        reason = "缺少输入：" + "、".join(missing)
                if stage.id == "meta" and not (batch_dir / "06_batch" / "batch.json").is_file():
                    blocked(stage.id, "等待组装产物 06_batch/batch.json", "agent")
                    continue
                if reason:
                    print(f"  · {stage.id} 跳过（{reason}）")
                    mf.touch_stage(manifest, stage.id, mf.SKIPPED, notes=reason)
                    mf.save(batch_dir, manifest)
                    continue
            print(f"  · {stage.id} 执行脚本")
            executable = _bridge_stage(stage, bridge[stage.id]) if stage.id == "extract" and bridge.get(stage.id) else stage
            code = rn._execute_command(batch_dir, manifest, executable)
            if code != 0:
                blocked(stage.id, "脚本执行失败", "failed")
            continue

        if stage.kind == contracts.GATE:
            print(f"  · {stage.id} 运行闸门")
            if stage.id == "structure_gate":
                code = rn.do_structure_gate(batch_dir, manifest)
            elif stage.id == "validate":
                code = rn.do_validate(batch_dir, manifest)
            else:
                code = rn.do_review_gate(batch_dir, manifest)
            manifest = mf.load(batch_dir)
            if stage.id == 'validate' and code == 0:
                manifest['stages']['validate'].pop('audio_projection_refresh', None)
                mf.save(batch_dir, manifest)
            if code != 0:
                blocked(stage.id, "闸门未通过", "gate")
                if stage.id == "structure_gate":
                    from . import provisional
                    if provisional.configured():
                        try:
                            provisional_result = provisional.run(batch_dir)
                        except (OSError, ValueError, TypeError) as exc:
                            provisional_result = {"status": "needs_review", "reason": str(exc), "attempted": 0}

    manifest = mf.load(batch_dir)
    first = blockers[0] if blockers else {}
    # 本地推进不代表模型已恢复；只有原冷却到期后才清掉旧标记。
    if cooldown <= 0 and not any(item["action"] == "cooldown" for item in blockers):
        _clear_cooldown(batch_dir)
    outcome = _result(batch_dir, manifest, first.get("stage"),
                      first.get("reason", "自动阶段已全部完成"), first.get("action", ""))
    # 等待只进入调度结果；不改写依赖状态，更不能把阻断闸门登记成完成或批准。
    outcome["failed_stages"] = [item for item in blockers if item["action"] in ("failed", "gate")]
    outcome["pending_stages"] = [item for item in blockers if item["action"] not in ("failed", "gate")]
    outcome["cooldown_seconds"] = _read_cooldown(batch_dir)
    if provisional_result is not None:
        outcome["provisional"] = provisional_result
    return outcome


def _result(batch_dir: Path, manifest: dict, stage_id, reason: str,
            blocked_by: str = "") -> dict:
    # Imports also use this lock; reload to avoid replacing a just-imported stage.
    # Contention means another executor owns the batch right now: report the round
    # without fighting for the lock or turning a transient wait into a failure.
    from .agent_protocol import _agent_lock
    try:
        with _agent_lock(5):
            manifest = mf.load(batch_dir)
            manifest["status"] = mf.derive_status(manifest)
            mf.save(batch_dir, manifest)
    except Exception as exc:  # noqa: BLE001
        if getattr(exc, "code", "") != "lock_timeout":
            raise
        return {"status": "busy", "stopped_at": stage_id, "action": "busy",
                "reason": "另一个执行器正在处理该批次；稍后自动继续",
                "failed_stages": [], "pending_stages": []}
    observed = {}
    try:
        from . import self_heal
        observed = self_heal.observe_outcomes(batch_dir, manifest)
        from . import operations
        # Publish a new blocker before releasing the execution claim. The Host
        # can claim on its next scan instead of waiting through a heal-model turn.
        operations.handoff_if_available(batch_dir)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    performance = {}
    try:
        from . import operations_performance
        performance = operations_performance.observe(batch_dir, mf.load(batch_dir))
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        pass  # A read-only performance report cannot block normal delivery.
    return {
        "performance_observation": performance,
        "self_heal_verification": observed,
        "batch": manifest.get("workflow_id", batch_dir.name),
        "batch_dir": str(batch_dir),
        "status": manifest["status"],
        "stopped_at": stage_id,
        "action": blocked_by,
        "reason": reason,
    }


# ---------------------------------------------------------------- 队列

# 429/quota 属"可恢复但需等待"的失败：空转重试只会继续撞同一限额。
# 记一个批次级的冷却到期时间，期间不重新调用模型，也不把阶段误标为永久失败。
RATE_LIMIT_MARKERS = (
    "429", "RATE_LIMIT", "rate limit", "rate_limit",
    "DAILY_LIMIT_EXCEEDED", "daily usage limit", "usage limit has been reached",
    "QUOTA", "quota", "too many requests",
)


def _is_rate_limited(text: str) -> bool:
    from .local_agent import classify_failure
    # The queue must use the same structured transport evidence as the bridge;
    # words quoted by a model or ordinary validation messages are not HTTP errors.
    if classify_failure(text or "") in ("rate_limit", "quota"):
        return True
    # local_agent raises `Agent subprocess failed (exit 1, rate_limit)` when the
    # structured stderr summary is unavailable to the caller. The code inside
    # this machine-generated wrapper is still authoritative, unlike arbitrary
    # model text.
    return bool(re.search(r"\(exit -?\d+, (?:rate_limit|quota)\)", text or ""))


def _is_upstream_call_failure(text: str) -> bool:
    """Recognize only the local agent's failed subprocess wrapper and stderr line."""
    text = text or ''
    # The CLI serializes AgentError as a JSON object; unwrap only its error field.
    try:
        obj = json.loads(text)
    except (TypeError, ValueError):
        obj = None
    if isinstance(obj, dict):
        if obj.get('code') != 'exit' or not isinstance(obj.get('error'), str):
            return False
        text = obj['error']
    match = re.match(r'^Agent subprocess failed \(exit -?\d+, exit\)：', text)
    if not match:
        return False
    detail = text[match.end():].split('；完整日志见 stderr.log', 1)[0]
    return any(re.fullmatch(r'(?:Error: )?(?:Upstream HTTP/2 stream failed|Upstream service temporarily unavailable)[.!]?',
                            line.strip()) for line in detail.splitlines())


def _is_native_upstream_failure(code, message, artifacts, batch_dir, stage_id, run_dir):
    """Authenticate the native host's failed task, never its candidate text."""
    from . import content_agent
    if (code != 'failed' or message not in ('Upstream HTTP/2 stream failed',
                                            'Upstream service temporarily unavailable')
            or not isinstance(artifacts, dict) or 'candidate' in artifacts):
        return False
    try:
        attempt = Path(artifacts['attempt_dir']).resolve(strict=True)
        attempt.relative_to(Path(run_dir).resolve(strict=True))
        binding = json.loads((attempt / 'content-task.json').read_text(encoding='utf-8'))
        task_id = binding['task_id']
        if not isinstance(task_id, str) or not re.fullmatch(r'[0-9a-f]{32}', task_id):
            return False
        task_path = content_agent._task_path(task_id).resolve(strict=True)
        if task_path != Path(artifacts['task']).resolve(strict=True):
            return False
        task = json.loads(task_path.read_text(encoding='utf-8'))
        return (task.get('protocol') == content_agent.PROTOCOL
                and task.get('task_id') == task_id and task.get('identity') == binding.get('identity')
                and task.get('batch_dir') == str(Path(batch_dir).resolve())
                and task.get('stage') == stage_id and task.get('status') == 'failed'
                and task.get('error') == message and 'candidate' not in task)
    except (OSError, ValueError, TypeError, KeyError):
        return False


def _requeue_historical_upstream(batch_dir, manifest, bridge):
    """Requeue only an exactly bound, wholly failed native answers attempt.

    Legacy attempts have no stage input snapshot; the native task's own strict
    input hashes, unit scope and timestamps are therefore all mandatory.
    Ambiguous, partial, active or unreadable evidence is left untouched.
    """
    from . import content_agent, local_agent, operations
    batch = Path(batch_dir).resolve()
    stage = contracts.STAGE_BY_ID['answers']
    record = manifest.get('stages', {}).get('answers', {})
    if (record.get('status') != mf.FAILED or operations.lease_active(batch)
            or not _default_agent_bridge('answers', bridge)):
        return False
    try:
        started = datetime.fromisoformat(record['started_at']).timestamp()
        finished = datetime.fromisoformat(record['finished_at']).timestamp()
        if finished < started:
            return False
        runs = list((batch / 'workflow' / 'agent-runs').glob('answers-*'))
        # A single attempt is required: filesystem order does not prove which of
        # multiple historical attempts produced the current manifest failure.
        if len(runs) != 1 or not runs[0].is_dir():
            return False
        run = runs[0]
        progress_paths = list(run.glob('repair-progress*.json'))
        bindings = list(run.glob('round-*-part-*/content-task.json'))
        if len(progress_paths) != 1 or len(bindings) != 1:
            return False
        progress = json.loads(progress_paths[0].read_text(encoding='utf-8'))
        binding = json.loads(bindings[0].read_text(encoding='utf-8'))
        task_id = binding['task_id']
        task_path = content_agent._task_path(task_id)
        task = json.loads(task_path.read_text(encoding='utf-8'))
        errors = ('Upstream HTTP/2 stream failed', 'Upstream service temporarily unavailable')
        failed = progress['failed_units']
        ids = task['unit_ids']
        if (progress.get('stage') != 'answers' or progress.get('validated_units') != []
                or progress.get('deterministic_failures') != {} or progress.get('calls') != []
                or not isinstance(failed, dict) or not failed
                or not isinstance(ids, list) or not ids or len(ids) != len(set(ids))
                or set(ids) != set(failed) or any(value != task.get('error') for value in failed.values())
                or task.get('protocol') != content_agent.PROTOCOL or task.get('task_id') != task_id
                or task.get('identity') != binding.get('identity') or not binding.get('identity')
                or task.get('batch_dir') != str(batch) or task.get('stage') != 'answers'
                or task.get('status') != 'failed' or task.get('error') not in errors
                or task.get('accepted') is not False or not task.get('session_id')
                or any(key in task for key in ('candidate', 'response', 'feedback', 'result'))
                or task.get('attempts') != [] or not isinstance(task.get('input_hashes'), dict)
                or not task['input_hashes']
                or not (started <= float(task['created_at']) < finished + 1
                        and started <= float(task['updated_at']) < finished + 1)):
            return False
        local_agent._check_input_hashes(batch, task['input_hashes'])
        # Another active or newer task can still write, or supersede this attempt.
        for other in content_agent._tasks():
            if (other.get('batch_dir') == str(batch) and other.get('stage') == 'answers'
                    and other.get('task_id') != task_id
                    and (other.get('status') in ('queued', 'running')
                         or float(other.get('created_at', 0)) >= started)):
                return False
        if operations.lease_active(batch):
            return False
        if _read_cooldown(batch) <= 0:
            _set_cooldown(batch, RATE_LIMIT_BACKOFF_SECONDS)
        mf.touch_stage(manifest, stage.id, mf.PENDING,
                       notes='已核验原生任务上游瞬时故障，冷却后按正常调度续跑')
        mf.save(batch_dir, manifest)
        return True
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError):
        return False


def _has_deterministic_partition_failure(run_dir: Path) -> bool:
    """Partition checkpoints are authoritative; unreadable evidence fails closed."""
    for path in Path(run_dir).glob('repair-progress*.json'):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or not isinstance(data.get('deterministic_failures'), dict):
                return True
            if data['deterministic_failures']:
                return True
        except (OSError, ValueError, TypeError):
            return True
    return False


def _cooldown_path(batch_dir: Path) -> Path:
    return Path(batch_dir) / "workflow" / "agent-runs" / ".rate-limit-cooldown"


def _read_cooldown(batch_dir: Path) -> float:
    """Seconds remaining before this batch may call the model again; 0 if ready."""
    try:
        until = float(_cooldown_path(batch_dir).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0.0
    remaining = until - time.time()
    return remaining if remaining > 0 else 0.0


def _set_cooldown(batch_dir: Path, seconds: float) -> None:
    path = _cooldown_path(batch_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(time.time() + seconds) + "\n", encoding="utf-8")
    except OSError:
        pass


def _clear_cooldown(batch_dir: Path) -> None:
    try:
        _cooldown_path(batch_dir).unlink()
    except OSError:
        pass


def _stage_has_outputs(batch_dir: Path, stage) -> bool:
    """A needs_review agent result is continuable only with its declared files."""
    return bool(stage.produces) and all((Path(batch_dir) / relative).is_file()
                                        for relative in stage.produces)


def _first_unfinished_stage(manifest: dict) -> str:
    """First stage not in a terminal-ok state, for cooldown reporting."""
    for stage in contracts.STAGES:
        if manifest.get("stages", {}).get(stage.id, {}).get("status") not in mf.TERMINAL_OK:
            return stage.id
    return contracts.STAGES[-1].id


def poll_batch(batch_dir: Path, bridge: dict, *, interval: float = 5.0,
               max_polls: int = 0, stop_at: str | None = None) -> dict:
    """Poll one existing batch and wake the next ready stage.

    ``advance`` is synchronous for each launched command, but an agent may leave
    a stage pending/needs_review/failed after returning. Re-running this loop
    makes completion of one stage a durable trigger for its dependants. Terminal
    stages are never launched again; the batch lock prevents concurrent polls.
    ``max_polls=0`` means keep polling until every stage reaches a terminal state.
    """
    batch_dir = Path(batch_dir)
    if stop_at is not None and stop_at not in contracts.STAGE_BY_ID:
        raise ValueError("Unknown stop stage: " + str(stop_at))
    scope = contracts.STAGES[:contracts.STAGE_ORDER.index(stop_at)] if stop_at else contracts.STAGES
    polls = 0
    outcome = {}
    while max_polls <= 0 or polls < max_polls:
        outcome = advance(batch_dir, bridge, stop_at=stop_at)
        polls += 1
        manifest = mf.load(batch_dir)
        if all(mf.dependency_satisfied(manifest, stage.id, allow_unqualified=ZERO_TOUCH)
               for stage in scope):
            break
        # Every unfinished pass yields, including failed/gate/busy outcomes.
        # Otherwise an unchanged deterministic failure spins and blocks its owner.
        if interval > 0 and (max_polls <= 0 or polls < max_polls):
            time.sleep(interval)
    outcome["polls"] = polls
    return outcome


def process(inbox: Path, workflows_root: Path, bridge: dict,
            limit: int | None = None, stop_at: str | None = None) -> list[dict]:
    candidates = discover(inbox)
    results = []
    seen_batches = set()
    # Also poll existing workflow directories. A batch may already have passed
    # inbox discovery while its next agent is still pending or retryable.
    existing = []
    root = Path(workflows_root)
    if root.is_dir():
        for folder in sorted(root.iterdir()):
            if folder.is_dir() and (folder / "workflow" / "manifest.json").is_file():
                existing.append(folder)
    for batch_dir in existing:
        name = batch_dir.name
        if name in {str(item.get("name")) for item in candidates}:
            continue
        if limit and len(results) >= limit:
            break
        print(f"\n== {name}（轮询已有批次）==")
        try:
            outcome = advance(batch_dir, bridge, stop_at=stop_at)
            results.append(outcome)
            seen_batches.add(name)
        except Exception as exc:
            results.append({"batch": name, "error": str(exc)})
    for candidate in candidates[:limit] if limit else candidates:
        if candidate.get("name") in seen_batches:
            continue
        if candidate.get("error"):
            results.append({"batch": candidate["name"], "error": candidate["error"]})
            continue
        print(f"\n== {candidate['name']} ==")
        try:
            batch_dir = ensure_batch(candidate, workflows_root)
            outcome = advance(batch_dir, bridge, stop_at=stop_at)
            try:
                data = report_mod.write(batch_dir)
                counts = data["counts"]
                outcome["report"] = str(Path(batch_dir) / "07_review" / "notification.txt")
                outcome["digest"] = (f"无误 {counts['ok']} / 有疑问 {counts['attention']}"
                                     f" / 阻断 {counts['blocked']} / 未核对 {counts['unverified']}")
            except Exception as exc:  # 通报失败不影响批次推进结果
                outcome["report_error"] = str(exc)
            results.append(outcome)
        except Exception as exc:  # 单个批次失败不影响其它批次
            results.append({"batch": candidate["name"], "error": str(exc)})
    write_queue(workflows_root, results)
    return results


def write_queue(workflows_root: Path, results: list[dict]) -> Path:
    target = Path(workflows_root) / QUEUE_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"generated_at": mf.now_iso(), "batches": results},
                                 ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def print_results(results: list[dict]) -> None:
    if not results:
        print("收件箱里没有可处理的批次。")
        return
    print(f"\n{'批次':<24}{'状态':<22}{'停在':<16}说明")
    print("-" * 96)
    for item in results:
        if item.get("error"):
            print(f"{item['batch']:<24}{'error':<22}{'-':<16}{item['error'][:40]}")
            continue
        print(f"{item['batch']:<24}{item['status']:<22}"
              f"{str(item.get('stopped_at') or '-'):<16}{item['reason'][:38]}")
        if item.get("digest"):
            print(f"{'':<24}{item['digest']}")
        if item.get("report"):
            print(f"{'':<24}通报：{item['report']}")


# ---------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workflow.auto", description="录题批量自动化驱动")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="查看收件箱里能识别出的批次")
    p_scan.add_argument("--inbox", default=str(DEFAULT_INBOX))

    p_run = sub.add_parser("run", help="自动初始化并推进每个批次")
    p_run.add_argument("--inbox", default=str(DEFAULT_INBOX))
    p_run.add_argument("--workflows", default=str(DEFAULT_WORKFLOWS))
    p_run.add_argument("--once", action="store_true", help="只跑一轮（默认）")
    p_run.add_argument("--interval", type=int, default=0, help="常驻间隔秒数")
    p_run.add_argument("--limit", type=int, default=0, help="本轮最多处理几个批次")
    p_run.add_argument("--agent-bridge", default=None, help="代理桥 JSON 路径")
    p_run.add_argument("--stop-at", default=None, help="推进到指定阶段前停下")
    p_run.add_argument("--batch-dir", default=None, help="直接轮询一个已有批次目录")
    p_run.add_argument("--poll-interval", type=float, default=5.0,
                       help="已有批次阶段轮询间隔秒数（默认 5）")
    p_run.add_argument("--max-polls", type=int, default=0,
                       help="最多轮询次数，0 表示直到批次全部结束")
    p_run.add_argument("--rate-limit-backoff", type=int, default=int(RATE_LIMIT_BACKOFF_SECONDS),
                       help="撞到限流/额度后的冷却秒数（默认 300）")
    return parser


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass
    args = build_parser().parse_args(argv)
    inbox = Path(args.inbox)
    if args.command == "scan":
        candidates = discover(inbox)
        if not candidates:
            print(f"没有从 {inbox} 识别出批次。")
            return 0
        for item in candidates:
            if item.get("error"):
                print(f"✗ {item['name']}：{item['error']}")
                continue
            print(f"· {item['name']}")
            for key in ("source", "answers", "audio"):
                if item.get(key):
                    print(f"    {key:8s} {item[key]}")
        return 0

    if args.stop_at and args.stop_at not in contracts.STAGE_BY_ID:
        print(f"未知停止阶段：{args.stop_at}")
        return 2
    if args.rate_limit_backoff and args.rate_limit_backoff > 0:
        globals()["RATE_LIMIT_BACKOFF_SECONDS"] = float(args.rate_limit_backoff)
    workflows_root = Path(args.workflows)
    bridge = load_bridge(args.agent_bridge)
    if bridge:
        print(f"代理桥：{len(bridge)} 个阶段（{'、'.join(sorted(bridge))}）")
    else:
        print("代理桥未配置：代理阶段会停在原地，等人工或主代理处理。")

    if args.batch_dir:
        result = poll_batch(Path(args.batch_dir), bridge,
                            interval=max(0.0, args.poll_interval),
                            max_polls=max(0, args.max_polls),
                            stop_at=args.stop_at)
        print_results([result])
        return 0 if result.get("status") not in {"failed", "blocked"} else 1

    while True:
        results = process(inbox, workflows_root, bridge,
                          limit=args.limit or None, stop_at=args.stop_at)
        print_results(results)
        if not args.interval or args.once:
            return 0
        # 若本轮有批次因限流/额度在冷却，等到最早冷却是最短的那批到期再扫，
        # 避免用固定短间隔空转、反复撞同一个限额。
        cooldowns = [r.get("cooldown_seconds", 0) for r in results if isinstance(r, dict)]
        wait = args.interval
        if any(c and c > 0 for c in cooldowns):
            wait = max(args.interval, min(c for c in cooldowns if c and c > 0))
        print(f"\n{int(wait)}s 后重新扫描…（Ctrl+C 结束）")
        try:
            time.sleep(wait)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
