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
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
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
    return {k: v for k, v in data.items() if isinstance(v, list) and v}


def _bridge_stage(stage: contracts.Stage, command: list) -> contracts.Stage:
    return dataclasses.replace(stage, kind=contracts.COMMAND, command=tuple(command))


# ---------------------------------------------------------------- 推进

def _parallel_ready_agents(batch_dir: Path, stages: list, config: dict) -> dict:
    """Export and import serially; only read-only model calls overlap."""
    from . import agent_protocol, agent_tasks, local_agent
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
    prepared, outcomes = {}, {}
    for stage in stages:
        try:
            if stage.id == "answers":
                prepare_answers(batch_dir)
            # 解析/大意与知识点需要整卷题干、材料和答案附录做题号匹配。
            # 180k 的通用传输预算会把正常的大卷误判为“证据不足”；提高本阶段
            # 的上下文预算，仍由模型返回校验和导入器做最终覆盖/分片约束。
            stage_budget = config["max_input_chars"]
            if stage.id in {"explanations", "knowledge"}:
                stage_budget = max(stage_budget, 600000)
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
        invoke_config = dict(config)
        if stage_id in {"explanations", "knowledge"}:
            invoke_config["max_input_chars"] = max(int(invoke_config.get("max_input_chars", 180000)), 600000)
        return local_agent.invoke(task["prompt"], run_dir, invoke_config, permit_parallel=True)

    with ThreadPoolExecutor(max_workers=min(4, max(1, len(prepared)))) as executor:
        futures = {stage_id: executor.submit(invoke, (stage_id, item)) for stage_id, item in prepared.items()}
        for stage in stages:
            if stage.id not in futures:
                continue
            exported, _, run_dir = prepared[stage.id]
            try:
                response = futures[stage.id].result()["response"]
                envelope = {"protocol": agent_protocol.PROTOCOL, "task_id": exported["task_id"],
                            "result": response}
                result_path = run_dir / "portable-result.json"
                result_path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
                outcomes[stage.id] = (0, result_path)
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
                outcomes[stage.id] = (1, str(exc))

    for stage in stages:
        code, value = outcomes[stage.id]
        imported = False
        if code == 0:
            try:
                exported, task, _ = prepared[stage.id]
                response_path = value
                for retry in range(int(config.get("retries", 0)) + 1):
                    envelope = json.loads(response_path.read_text(encoding="utf-8"))
                    try:
                        with agent_protocol._agent_lock(5):
                            agent_tasks.accept_result(batch_dir, stage.id, envelope["result"], validate_only=True)
                        break
                    except ValueError as exc:
                        if retry >= int(config.get("retries", 0)):
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
                receipt = agent_protocol.import_result(batch_dir, exported["task_id"], response_path)
                code = 0 if receipt["completed"] else 3
                imported = True
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
                code, value = 1, str(exc)
        if code and not imported:
            # Coordinate with protocol imports and do not overwrite a stage another
            # participant completed while the model call was running.
            limited = _is_rate_limited(str(value))
            with agent_protocol._agent_lock(5):
                manifest = mf.load(batch_dir)
                if manifest["stages"].get(stage.id, {}).get("status") not in mf.TERMINAL_OK:
                    if limited:
                        # 429/额度是“稍后可用”，不是永久失败：回到 pending 并登记冷却，
                        # 避免 UI 上出现一片 failed、也避免自动循环立刻再撞限额。
                        _set_cooldown(batch_dir, RATE_LIMIT_BACKOFF_SECONDS)
                        mf.touch_stage(manifest, stage.id, mf.PENDING,
                                       notes="限流/额度冷却中：" + str(value)[:200])
                    else:
                        mf.touch_stage(manifest, stage.id, mf.NEEDS_REVIEW if code == 3 else mf.FAILED,
                                       exit_code=code, notes=str(value)[:300])
                    mf.save(batch_dir, manifest)
            if limited:
                outcomes[stage.id] = 4
                continue
        outcomes[stage.id] = code
    return outcomes


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
    blocks the batch for the whole heartbeat timeout. Probing the flock is exact.
    """
    path = Path(batch_dir) / "02_audio" / ".segment.lock"
    if not path.is_file():
        return False
    try:
        import fcntl
    except ImportError:  # Windows: fall back to the heartbeat timeout.
        return True
    try:
        with path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return False
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
    from . import review
    if review.evaluate_rounds(batch_dir, payload).get("execution_completed"):
        return False
    mf.invalidate_stage(batch_dir, manifest, "review", "审核记录不再满足当前审核契约，自动补审")
    mf.save(batch_dir, manifest)
    return True


def _advance_locked(batch_dir: Path, bridge: dict, stop_at: str | None = None) -> dict:
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
    # 卡点自愈：先收集诊断证据，再由已配置模型选择受限的重试动作。
    # 模型不可用或返回非法计划时只留痕，不阻断本轮自动调度。
    try:
        from . import self_heal
        healed = (self_heal.recover(batch_dir) if cooldown <= 0 else
                  {"changed": False, "status": "model_cooldown"})
        if healed.get("changed"):
            manifest = mf.load(batch_dir)
            print(f"  · {healed.get('stage')} AI自愈已执行动作：{healed.get('status', 'requeued')}，继续校验")
        elif healed.get("stage") and healed.get("status") in {"cooldown", "busy", "waiting_external", "failed", "recorded"}:
            # A wait recorded against older inputs must not outrank a real upstream
            # repair: if the failed stage is now stale, schedule it again instead
            # of stopping this round and showing a cooldown to the user.
            spec = contracts.STAGE_BY_ID.get(healed["stage"])
            # is_stale also returns True merely because a stage is failed.
            # That is not an input change and must not bypass recovery cooldown
            # into an endless rerun of the same deterministic validation error.
            saved_inputs = (self_heal._load(batch_dir).get('last_inputs') or {}).get(healed['stage'])
            current_inputs = mf.input_hashes(batch_dir, manifest, spec) if spec else None
            stale = saved_inputs is not None and current_inputs != saved_inputs
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
    audio_allowed = (stop_at is None or stop_at in contracts.STAGE_ORDER
                     and contracts.STAGE_ORDER.index(stop_at) > contracts.STAGE_ORDER.index("audio"))
    for stage in contracts.STAGES:
        # 结构闸门一确认就先切听力：赶在并行代理批把 audio 当成“缺证据”之前，
        # 让上传后的流程真正自动完成切段，而不是等用户点按钮。
        if audio_allowed and audio_prep.get("status") == "waiting":
            deps = contracts.STAGE_BY_ID["audio"].depends_on
            if all(dep_ready(d) for d in deps):
                audio_prep = _auto_prepare_audio(batch_dir, manifest)
        if stop_at and stage.id == stop_at:
            break
        if stage.id in recovery_wait:
            # 听力切段能力缺失（未安装/未启用）属于能力受限，不是内容错误：
            # 不能被自愈冷却长期挂住，按既有规则跳过并继续，其它阶段照常推进。
            capability_limited_audio = (
                stage.id == "audio" and ZERO_TOUCH
                and audio_prep.get("status") in {"disabled", "not_ready", "unavailable", "error",
                                                 "no_audio", "running"})
            if not capability_limited_audio:
                wait = recovery_wait[stage.id]
                reason = (wait.get("event") or {}).get("error") or (wait.get("event") or {}).get("reason") or "AI修复已记录，等待自动重试窗口"
                blocked(stage.id, str(reason)[:300], "cooldown", next_retry_at=wait.get("next_retry_at"),
                        retry_after=wait.get("retry_after"), self_heal_status=wait.get("status"))
                continue
        if stage.id in parallel_done:
            code = parallel_done[stage.id]
            if code == 4:
                cooldown = _read_cooldown(batch_dir)
                blocked(stage.id,
                        "限流/额度冷却中，约 " + str(int(cooldown or RATE_LIMIT_BACKOFF_SECONDS)) + "s 后重试",
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
                blocked(stage.id, f"模型限流/额度冷却中，约 {int(cooldown)}s 后重试", "cooldown",
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
                    if candidate.id == "audio" and audio_prep.get("status") == "multiple_audio":
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
                if len(eligible) > 1 and agent.configured():
                    config = local_agent.load_config()
                    try:
                        results = _parallel_ready_agents(batch_dir, eligible, config)
                    except Exception as exc:  # lock contention is "busy", not a failure
                        if getattr(exc, "code", "") != "lock_timeout":
                            raise
                        return _result(batch_dir, manifest, stage.id,
                                       "另一个执行器正在处理该批次；稍后自动继续", "busy")
                    parallel_done.update(results)
                    manifest = mf.load(batch_dir)
                    if stage.id in results:
                        code = results[stage.id]
                        if code == 4:
                            cooldown = _read_cooldown(batch_dir)
                            blocked(stage.id,
                                    "限流/额度冷却中，约 " + str(int(cooldown or RATE_LIMIT_BACKOFF_SECONDS)) + "s 后重试",
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
            code = rn._execute_command(batch_dir, manifest, executable)
            if code != 0:
                pending = code == 3 and manifest["stages"][stage.id].get("status") == mf.NEEDS_REVIEW
                note = ""
                try:
                    note = str(mf.load(batch_dir)["stages"].get(stage.id, {}).get("notes", ""))
                except (OSError, KeyError, ValueError):
                    note = ""
                if not pending and _is_rate_limited(note):
                    delay = RATE_LIMIT_BACKOFF_SECONDS
                    _set_cooldown(batch_dir, delay)
                    cooldown = delay
                    blocked(stage.id, f"限流/额度冷却中，约 {int(delay)}s 后重试", "cooldown",
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
            elif stage.id == "extract":
                # 上传后自动识别题源分类/卷名/地区/年份；失败只记录原因，不阻断流程。
                try:
                    from . import metadata_ai
                    identified = metadata_ai.auto_identify(batch_dir)
                except (OSError, ValueError, TypeError) as exc:
                    identified = {"ran": False, "reason": str(exc)}
                if identified.get("ran"):
                    print("  · 已自动识别试卷元信息：" + "、".join(identified.get("applied") or []))
                manifest = mf.load(batch_dir)
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
    # 本轮若没有任何限流/额度冷却阻断就说明已恢复，清掉旧冷却标记（成功推进后不残留）。
    if not any(item["action"] == "cooldown" for item in blockers):
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
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return {
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
