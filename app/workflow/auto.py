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
# 限流/额度失败后的默认冷却秒数；可用 --rate-limit-backoff 覆盖。
RATE_LIMIT_BACKOFF_SECONDS = 300.0

DOCX_SUFFIXES = (".docx", ".doc")
PDF_SUFFIXES = (".pdf",)
AUDIO_SUFFIXES = (".mp3", ".wav", ".m4a", ".aac")

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
    return {
        "source": str(source),
        "answers": str(answers[0]) if answers else None,
        "audio": str(_pick(files, AUDIO_SUFFIXES)) if _pick(files, AUDIO_SUFFIXES) else None,
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
                         "region": data.get("region"), "subject": data.get("subject", "english")}
            for key in ("source", "answers", "audio", "docx"):
                value = data.get(key)
                candidate[key] = str((folder / value).resolve()) if value else None
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
        return batch_dir
    args = argparse.Namespace(
        batch_dir=str(batch_dir),
        source=candidate.get("source"),
        answers=candidate.get("answers"),
        audio=candidate.get("audio"),
        docx=candidate.get("docx"),
        year=candidate.get("year"),
        region=candidate.get("region"),
        subject=candidate.get("subject") or "english",
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

    attempts = batch_dir / "workflow" / "agent-runs"
    attempts.mkdir(parents=True, exist_ok=True)
    prepared, outcomes = {}, {}
    for stage in stages:
        try:
            if stage.id == "answers":
                prepare_answers(batch_dir)
            exported = agent_protocol.export_task(batch_dir, stage.id,
                                                  max_input_chars=config["max_input_chars"])
            task = agent_tasks.build_task(batch_dir, stage.id, max_input_chars=config["max_input_chars"])
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
        _, task, run_dir = item
        return local_agent.invoke(task["prompt"], run_dir, config, permit_parallel=True)

    with ThreadPoolExecutor(max_workers=min(4, max(1, len(prepared)))) as executor:
        futures = {stage_id: executor.submit(invoke, item) for stage_id, item in prepared.items()}
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
            with agent_protocol._agent_lock(5):
                manifest = mf.load(batch_dir)
                if manifest["stages"].get(stage.id, {}).get("status") not in mf.TERMINAL_OK:
                    mf.touch_stage(manifest, stage.id, mf.NEEDS_REVIEW if code == 3 else mf.FAILED,
                                   exit_code=code, notes=str(value)[:300])
                    mf.save(batch_dir, manifest)
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


def advance(batch_dir: Path, bridge: dict, stop_at: str | None = None) -> dict:
    batch_dir = Path(batch_dir)
    with _batch_auto_lock(batch_dir):
        return _advance_locked(batch_dir, bridge, stop_at)


def _advance_locked(batch_dir: Path, bridge: dict, stop_at: str | None = None) -> dict:
    manifest = mf.load(batch_dir)
    blockers = []
    provisional_result = None
    parallel_done = {}

    def blocked(stage_id, reason, action="", **details):
        blockers.append({"stage": stage_id, "action": action, "reason": reason, **details})

    # 若上一轮撞到限流/额度并登记了冷却，且当前仍在冷却期，本轮不再调用模型：
    # 只报告等待，不读写阶段状态，避免把可恢复的限额失败当成永久失败反复重试。
    cooldown = _read_cooldown(batch_dir)
    if cooldown > 0:
        blocked(_first_unfinished_stage(manifest), f"限流/额度冷却中，约 {int(cooldown)}s 后重试", "cooldown")
        return _result(batch_dir, manifest, blockers[0].get("stage"),
                       blockers[0]["reason"], "cooldown")

    # 契约已按拓扑排列：每阶段只尝试一次，阻断只影响真正依赖它的阶段。
    for stage in contracts.STAGES:
        if stop_at and stage.id == stop_at:
            break
        if stage.id in parallel_done:
            code = parallel_done[stage.id]
            if code:
                blocked(stage.id, "代理任务等待接单、补充证据或处理" if code == 3 else "代理桥执行失败",
                        "review" if code == 3 else "failed")
            continue
        record = manifest["stages"].get(stage.id, {})
        status = record.get("status")
        if status == mf.SKIPPED and not mf.skip_reason(manifest, stage):
            mf.touch_stage(manifest, stage.id, mf.PENDING, notes="跳过条件已消失，恢复执行")
            mf.save(batch_dir, manifest)
            status = mf.PENDING
        if status in mf.TERMINAL_OK:
            continue
        unmet = [d for d in stage.depends_on
                 if manifest["stages"].get(d, {}).get("status") not in mf.TERMINAL_OK]
        if unmet:
            blocked(stage.id, "等待上游：" + "、".join(unmet), unmet_dependencies=unmet)
            continue

        if stage.kind == contracts.HUMAN:
            blocked(stage.id, f"等待人工确认（{stage.title}）", "human")
            continue

        if stage.kind == contracts.AGENT:
            if rn._skip_agent_stage(batch_dir, manifest, stage):
                continue
            # 通用就绪并行批：把当前所有依赖已满足、且走默认桥的 AGENT 阶段一次性
            # 并发调用模型，再按阶段顺序导入。这样 types/answers/audio 一波、
            # explanations/knowledge 一波，四个打标签智能体都能并行参与，而不再
            # 严格四步串行（assemble 是确定性合并，单独处理，不进入并行批）。
            if PARALLEL_AGENTS_ENABLED and stage.id not in parallel_done and stage.id != "assemble":
                from . import agent, local_agent
                eligible = []
                for candidate in contracts.STAGES:
                    if stop_at and candidate.id == stop_at:
                        break
                    if candidate.id == "assemble" or candidate.kind != contracts.AGENT:
                        continue
                    default = ["{python}", "-m", "workflow.agent", "run", "--stage", candidate.id,
                               "--batch-dir", "{batch_dir}"]
                    if (bridge.get(candidate.id) == default
                            and manifest["stages"].get(candidate.id, {}).get("status") not in mf.TERMINAL_OK
                            and candidate.id not in parallel_done
                            and not mf.skip_reason(manifest, candidate)
                            and all(manifest["stages"].get(dep, {}).get("status") in mf.TERMINAL_OK
                                    for dep in candidate.depends_on)):
                        eligible.append(candidate)
                if len(eligible) > 1 and agent.configured():
                    config = local_agent.load_config()
                    results = _parallel_ready_agents(batch_dir, eligible, config)
                    parallel_done.update(results)
                    manifest = mf.load(batch_dir)
                    if stage.id in results:
                        code = results[stage.id]
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
                # Distinguish a recoverable rate-limit/quota stop from a real failure:
                # set a cooldown so the outer loop waits instead of re-hitting 429.
                note = ""
                try:
                    note = str(mf.load(batch_dir)["stages"].get(stage.id, {}).get("notes", ""))
                except (OSError, KeyError, ValueError):
                    note = ""
                if not pending and _is_rate_limited(note):
                    delay = RATE_LIMIT_BACKOFF_SECONDS
                    _set_cooldown(batch_dir, delay)
                    blocked(stage.id, f"限流/额度冷却中，约 {int(delay)}s 后重试", "cooldown")
                else:
                    blocked(stage.id, "代理任务等待接单、补充证据或处理" if pending else "代理桥执行失败",
                            "review" if pending else "failed")
            continue

        if stage.id == "upload":
            blocked(stage.id, "上传须人工通过专用命令显式确认", "human")
            continue

        if stage.kind == contracts.COMMAND:
            from .agent_protocol import _agent_lock
            with _agent_lock(1500):
                manifest = mf.load(batch_dir)
                record = manifest["stages"].get(stage.id, {})
                if record.get("status") in mf.TERMINAL_OK:
                    continue
                unmet = [d for d in stage.depends_on
                         if manifest["stages"].get(d, {}).get("status") not in mf.TERMINAL_OK]
                if unmet:
                    blocked(stage.id, "等待上游：" + "、".join(unmet), unmet_dependencies=unmet)
                    continue
                reason = mf.skip_reason(manifest, stage)
                if not reason and stage.requires:
                    missing = [k for k in stage.requires
                               if not manifest.get("inputs", {}).get(k, {}).get("exists")]
                    if missing:
                        reason = "缺少输入：" + "、".join(missing)
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
    from .agent_protocol import _agent_lock
    with _agent_lock(5):
        manifest = mf.load(batch_dir)
        manifest["status"] = mf.derive_status(manifest)
        mf.save(batch_dir, manifest)
    return {
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
    return classify_failure(text or "") in ("rate_limit", "quota")


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


def _first_unfinished_stage(manifest: dict) -> str:
    """First stage not in a terminal-ok state, for cooldown reporting."""
    for stage in contracts.STAGES:
        if manifest.get("stages", {}).get(stage.id, {}).get("status") not in mf.TERMINAL_OK:
            return stage.id
    return contracts.STAGES[-1].id


def process(inbox: Path, workflows_root: Path, bridge: dict,
            limit: int | None = None, stop_at: str | None = None) -> list[dict]:
    candidates = discover(inbox)
    results = []
    for candidate in candidates[:limit] if limit else candidates:
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
