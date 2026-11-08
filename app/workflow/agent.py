"""通用 Agent 入口：默认文件交接；配置后才自动调用本机 CLI。"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import uuid


PROTOCOL_COMMANDS = {'export', 'import', 'next', 'dispatch'}


def configured() -> bool:
    """只发现配置文件，不探测或绑定任何厂商 CLI。"""
    return bool(os.environ.get('MUDU_AGENT_CONFIG')) or Path(__file__).with_name('local-agent.json').is_file()


def _retain_partial_ai(first, batch_dir):
    """Retain a validated partial AI candidate without installing a completed review."""
    from . import agent_protocol, agent_tasks, ingest, manifest as mf, review_rounds
    batch = Path(batch_dir).resolve()
    candidate = None
    if first.get("task_id"):
        task_id = first["task_id"]
        path = agent_protocol._path(batch, f"workflow/agent-bindings/{task_id}.response.json")
        candidate = json.loads(path.read_text(encoding="utf-8"))["result"]
    elif first.get("attempt_dir"):
        attempt = Path(first["attempt_dir"]).resolve()
        if not attempt.is_relative_to(batch / "workflow/agent-runs"):
            raise ValueError("AI review attempt must belong to this batch")
        response_path = attempt / "response.json"
        if not response_path.is_file():
            return False
        candidate = json.loads(response_path.read_text(encoding="utf-8"))
    else:
        report = first.get("report", first)
        if report.get("findings"):
            candidate = {"status": "needs_review", "artifacts": {},
                         "notes": report.get("notes", []), "findings": report["findings"]}
    if not isinstance(candidate, dict) or candidate.get("status") != "needs_review":
        return False
    # Reuse the importer checks for paths, finding evidence, and candidate shape.
    agent_tasks.accept_result(batch, "review", candidate)
    _, _, missing, units = agent_tasks._collect(batch, "review")
    if missing:
        return False
    if mf.manifest_path(batch).is_file():
        manifest = mf.load(batch)
        from . import contracts
        if any(manifest.get("stages", {}).get(dep, {}).get("status") not in mf.TERMINAL_OK
               for dep in contracts.STAGE_BY_ID["review"].depends_on):
            return False
    payload = candidate["artifacts"].get(agent_tasks.ARTIFACTS["review"], {})
    findings = candidate["findings"] + payload.get("findings", [])
    if not (findings or payload.get("checked_units") or payload.get("unchecked") or candidate.get("notes")):
        return False
    ids = {u["unit_id"] for u in units}
    if not set(payload.get("checked_units", [])).issubset(ids):
        raise ValueError("Partial AI review checked an unknown unit")
    pending = [{"unit_id": "", "field": "review_ai", "required": True, "status": "unchecked",
                "reason": "; ".join(candidate["notes"]) or "AI初审返回needs_review，候选已保留，初审尚未完成"}]
    for item in payload.get("unchecked", []):
        if (not isinstance(item, dict) or item.get("unit_id") not in ids
                or not isinstance(item.get("reason"), str) or not item["reason"].strip()):
            raise ValueError("Partial AI unchecked work needs a known unit and actionable reason")
        pending.append(dict(item, required=True, status="unchecked"))
    review_rounds._save(batch, "workflow/review-ai-candidate.json", candidate)
    ingest.build_review_files(batch, {"findings": findings})
    target = review_rounds._path(batch, "07_review/unchecked.json")
    unchecked = json.loads(target.read_text(encoding="utf-8"))
    for item in pending:
        if item not in unchecked:
            unchecked.append(item)
    review_rounds._save(batch, "07_review/unchecked.json", unchecked)
    return True


def _review_inputs_ready(batch_dir):
    from . import agent_tasks, contracts, manifest as mf
    batch = Path(batch_dir)
    _, _, missing, _ = agent_tasks._collect(batch, "review")
    if missing:
        return False
    if mf.manifest_path(batch).is_file():
        manifest = mf.load(batch)
        if any(manifest.get("stages", {}).get(dep, {}).get("status") not in mf.TERMINAL_OK
               for dep in contracts.STAGE_BY_ID["review"].depends_on):
            return False
    return True


def _retain_ai_execution_failure(batch_dir, failures):
    """Record an orchestrator failure, never a fabricated AI review candidate."""
    from . import ingest, review_rounds
    ingest.build_review_files(batch_dir, {"findings": []})
    path = review_rounds._path(batch_dir, "07_review/unchecked.json")
    unchecked = json.loads(path.read_text(encoding="utf-8"))
    reason = failures[-1]["error"] if failures else "AI初审未获得合法结果"
    pending = {"code": "AI_EXECUTION_FAILED", "unit_id": "", "field": "review_ai",
               "required": True, "status": "unchecked",
               "reason": f"AI初审执行/协议失败（{len(failures)}次尝试）：{reason}"}
    unchecked = [row for row in unchecked if not (isinstance(row, dict) and row.get("code") == "AI_EXECUTION_FAILED")]
    unchecked.append(pending)
    review_rounds._save(batch_dir, "07_review/unchecked.json", unchecked)
    return {"report": {"completed": False, "status": "needs_review", "code": "AI_EXECUTION_FAILED",
                       "notes": [pending["reason"]]},
            "execution_attempts": len(failures), "execution_failures": failures}


def _parse_agent_output(raw):
    """从 Agent 回调输出中提取结果对象。

    分片/进度日志会与最终 JSON 混在同一条 stdout（如「分片 1/4：调用模型…」），
    直接 json.loads 整段必然失败，会把「AI 已如实返回 needs_review」误判成
    「执行失败」并原地重试。这里优先整段解析，失败则取最后一个合法 JSON 对象。
    """
    text = raw or ""
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except ValueError:
        pass
    decoder = json.JSONDecoder()
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _review_after(callback, batch_dir, config_path=None, max_attempts=3, allow_failure_fallback=True,
                  portable_task_id=None, ai_precheck=True):
    """Retry failed AI calls, then finish independent rounds with honest AI state.

    ai_precheck=False（正式 review 执行默认）：跳过独立的“AI 初审”兼容步骤，
    只跑一审（全量）与二审（抽20%）两轮。设计上 AI 审核＝两轮；agent-review.json 仅是旧协议
    兼容产物，不能代表两轮完成，跑了只会白白耗时并反复失败卡住 review。
    """
    if not ai_precheck:
        return _run_review_rounds(batch_dir, config_path)
    try:
        from . import review, review_rounds
        failures = []
        ai_completed = False
        quota_hit = False
        for attempt in range(0 if not ai_precheck else max_attempts):
            output, errors = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                try:
                    code = callback()
                except (ValueError, OSError, RuntimeError) as exc:
                    print(str(exc), file=sys.stderr)
                    code = 2
            raw, stderr = output.getvalue(), errors.getvalue()
            if not allow_failure_fallback and code not in (0, 3):
                print(raw, end="")
                print(stderr, end="", file=sys.stderr)
                return code
            first = _parse_agent_output(raw)
            report = first.get("report", first)
            if not isinstance(report, dict):
                report = {}
            if code == 0 and report.get("completed") is True:
                ai_completed = True
                break
            # True missing inputs and upstream gate failures must never be bypassed.
            if not _review_inputs_ready(batch_dir):
                print(raw, end="")
                print(stderr, end="", file=sys.stderr)
                return code or 3
            if report.get("code") == "AI_EXECUTION_FAILED" and first.get("resumed_ai_review"):
                failures = first.get("execution_failures", [])
                first = _retain_ai_execution_failure(batch_dir, failures)
                break
            try:
                if code in (0, 3) and _retain_partial_ai(first, batch_dir):
                    break
                error = stderr.strip() or raw.strip() or f"AI初审退出码 {code}，未返回合法审核结果"
            except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
                error = str(exc)
            if not allow_failure_fallback:
                print(raw, end="")
                print(stderr or error, end="", file=sys.stderr)
                return code or 3
            failure = {"attempt": attempt + 1, "exit_code": code, "error": error,
                       "stdout": raw, "stderr": stderr}
            failures.append(failure)
            review_rounds._save(batch_dir, "workflow/review-ai-failures/" + uuid.uuid4().hex + ".json", failure)
            # 额度/限流不是可重试错误：再试只会继续占用时间。
            from .local_agent import classify_failure
            if classify_failure(stderr + "\n" + error) in ("rate_limit", "quota"):
                quota_hit = True
                break
        else:
            first = _retain_ai_execution_failure(batch_dir, failures)
        from .local_agent import load_config
        review_rounds._save(batch_dir, "workflow/review-ai-binding.json", review.prepare_round_plan(batch_dir))
        review_rounds._save(batch_dir, "workflow/review-ai-state.json", first)
        portable_budget = None
        if not portable_task_id and first.get("resumed_ai_review"):
            portable_task_id = first.get("task_id")
        if portable_task_id and first.get("task_id") == portable_task_id:
            from . import agent_protocol
            try:
                if str(uuid.UUID(portable_task_id)) != portable_task_id:
                    raise ValueError("Noncanonical task ID")
                binding_path = agent_protocol._path(batch_dir, f"workflow/agent-bindings/{portable_task_id}.json")
                binding = json.loads(binding_path.read_text(encoding="utf-8"))
                budget = binding.get("max_input_chars")
                if (binding.get("task_id") == portable_task_id and binding.get("stage") == "review"
                        and binding.get("status") in ("accepted", "needs_review")
                        and binding.get("result_sha256") == first.get("result_sha256")
                        and isinstance(budget, int) and not isinstance(budget, bool) and budget > 0):
                    portable_budget = budget
            except (ValueError, OSError, TypeError, AttributeError):
                pass  # Older state can lack a private binding; retain the normal budget.
        if quota_hit:
            rounds = {"action": "blocked", "completed": False, "quality_passed": False,
                      "quota": True,
                      "reason": "代理额度/限流不可用，已停止重试；恢复额度后重跑 review 即可继续未完成轮次",
                      "review": review.evaluate_rounds(batch_dir),
                      "summary": review_rounds.write_summary(batch_dir)}
        else:
            rounds = review_rounds.run(batch_dir, config=load_config(config_path) if config_path else None,
                                       portable_max_input_chars=portable_budget)
        execution_completed = bool(rounds.get("completed"))
        completed = ai_completed and execution_completed
        gate = review.evaluate(batch_dir)
        quality_passed = ai_completed and gate["passed"]
        summary = dict(rounds.get("summary") or {})
        summary.update(ai_review_completed=ai_completed, execution_completed=execution_completed,
                       quality_passed=quality_passed,
                       open_manual_items=gate["counts"]["open_manual_items"])
        from . import contracts, manifest as mf
        batch = Path(batch_dir)
        if (batch / "workflow/manifest.json").is_file():
            from .local_agent import _agent_lock
            with _agent_lock(1500):
                manifest = mf.load(batch)
                spec = contracts.STAGE_BY_ID["review"]
                mf.touch_stage(manifest, "review", mf.COMPLETED if completed else mf.NEEDS_REVIEW,
                               input_hashes=mf.input_hashes(batch, manifest, spec),
                               output_hashes=mf.output_hashes(batch, spec),
                               notes=("AI初审已完成" if ai_completed else "AI初审待核验") + "；分轮审核：" + rounds["action"]
                                     + ("；代理额度用尽，恢复后重跑" if quota_hit else ""))
                if completed:
                    mf.record_delivery(batch, manifest, spec)
                manifest["status"] = mf.derive_status(manifest)
                mf.save(batch, manifest)
        print(json.dumps({"ai_review": first, "review_rounds": rounds,
                          "completed": completed, "ai_review_completed": ai_completed,
                          "execution_completed": execution_completed, "quality_passed": quality_passed,
                          "summary": summary,
                          "status": "completed" if completed else "needs_review"}, ensure_ascii=False))
        return 0 if completed else 3
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps({"completed": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


def _run_review_rounds(batch_dir, config_path=None):
    """Run the three review rounds without creating a separate AI precheck."""
    try:
        from . import contracts, manifest as mf, review, review_rounds
        from .local_agent import _agent_lock, load_config
        if not _review_inputs_ready(batch_dir):
            print(json.dumps({"action": "blocked", "completed": False,
                              "reason": "审核输入或上游阶段未就绪"}, ensure_ascii=False))
            return 3
        rounds = review_rounds.run(batch_dir, config=load_config(config_path) if config_path else None)
        execution_completed = bool(rounds.get("completed"))
        gate = review.evaluate(batch_dir)
        batch = Path(batch_dir)
        if (batch / "workflow/manifest.json").is_file():
            with _agent_lock(1500):
                manifest = mf.load(batch)
                spec = contracts.STAGE_BY_ID["review"]
                mf.touch_stage(manifest, "review", mf.COMPLETED if execution_completed else mf.NEEDS_REVIEW,
                               input_hashes=mf.input_hashes(batch, manifest, spec),
                               output_hashes=mf.output_hashes(batch, spec),
                               notes="分轮审核：" + rounds["action"])
                if execution_completed:
                    mf.record_delivery(batch, manifest, spec)
                manifest["status"] = mf.derive_status(manifest)
                mf.save(batch, manifest)
        print(json.dumps({"review_rounds": rounds, "completed": execution_completed,
                          "execution_completed": execution_completed, "quality_passed": gate["passed"],
                          "summary": rounds.get("summary"),
                          "status": "completed" if execution_completed else "needs_review"}, ensure_ascii=False))
        return 0 if execution_completed else 3
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps({"completed": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


def _audio_evidence_ready(batch_dir) -> bool:
    """切段产物已存在且通过 audio 阶段校验时，直接判定该阶段完成。

    听力切段由插件内置切段器（ffmpeg + WhisperX）确定性地产出
    `02_audio/segment-evidence.json`，再让模型"重新生成"一遍既慢又会重写产物、
    把已经算好的审核输入指纹改掉（审核随即作废、必须重跑）。所以只要产物能通过
    同一套契约校验，就走确定性快路，不调用模型、不重写文件。
    """
    from . import agent_tasks
    batch = Path(batch_dir)
    path = batch / "02_audio" / "segment-evidence.json"
    if not path.is_file():
        return False
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        items = document.get("items") if isinstance(document, dict) else None
        if not isinstance(items, list) or not items:
            return False
        if document.get("generator") == "audio.segment/v1":
            # Legacy auto-verification treated every non-micro/interpolated cut
            # as verified; regenerate evidence under the corrected contract.
            return False
        if any(item.get("quality") in ("micro", "interpolated", "coarse")
               or item.get("review_reasons") for item in items if isinstance(item, dict)):
            return False
        keys = ("unit_id", "file", "start", "end", "evidence")
        payload = {"items": [{key: item[key] for key in keys} for item in items
                             if isinstance(item, dict) and all(key in item for key in keys)]}
        if len(payload["items"]) != len(items):
            return False
        context, _, missing, units = agent_tasks._collect(batch, "audio")
        if missing or context.get('02_audio/segment-evidence.json') != document:
            return False
        agent_tasks._validate(batch, "audio", payload, context, units)
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return True


def _mark_stage_completed(batch_dir, stage_id):
    """Import via local CLI writes the artifact; record the stage too."""
    from . import contracts, manifest as mf, runner
    batch = Path(batch_dir)
    if not (batch / "workflow" / "manifest.json").is_file():
        return False
    stage = contracts.STAGE_BY_ID.get(stage_id)
    if stage is None:
        return False
    from .local_agent import _agent_lock
    with _agent_lock(1500):
        manifest = mf.load(batch)
        if manifest.get("stages", {}).get(stage_id, {}).get("status") in mf.TERMINAL_OK:
            return True
        if mf.missing_produces(batch, stage):
            return False
        runner._record_success(batch, manifest, stage)
        manifest["status"] = mf.derive_status(manifest)
        mf.save(batch, manifest)
        return True



def _ai_review_current(batch_dir):
    from . import review, review_rounds
    try:
        path = review_rounds._path(batch_dir, "workflow/review-ai-binding.json")
        return json.loads(path.read_text(encoding="utf-8")) == review.prepare_round_plan(batch_dir)
    except (ValueError, OSError):
        return False


def _resume_review(batch_dir, config_path=None):
    def previous():
        from . import review_rounds
        path = review_rounds._path(batch_dir, "workflow/review-ai-state.json")
        first = (json.loads(path.read_text(encoding="utf-8")) if path.is_file()
                 else {"report": {"completed": False, "code": "AI_EXECUTION_FAILED"},
                       "execution_failures": [{"error": "缺少已保存的AI初审状态"}]})
        first["resumed_ai_review"] = True
        print(json.dumps(first))
        return 0 if first.get("report", first).get("completed") is True else 3
    return _review_after(previous, batch_dir, config_path, max_attempts=1)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in PROTOCOL_COMMANDS:
        from . import agent_protocol
        return agent_protocol.main(args)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['run'])
    parser.add_argument('--stage', required=True)
    parser.add_argument('--batch-dir', type=Path, required=True)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    values = parser.parse_args(args)
    if values.stage == 'assemble' and not values.dry_run:
        from . import batch_fill, assemble_local
        if (values.batch_dir / batch_fill.STATE).is_file():
            # The early batch already owns its questions. Finalization never
            # falls back to a model that reconstructs the whole batch.
            code = assemble_local.try_assemble(values.batch_dir)
            if code == 0:
                _mark_stage_completed(values.batch_dir, 'assemble')
            return code if code is not None else 3
    if values.stage == 'explanations' and not values.dry_run:
        # 正文/答案材料已确定时，解析与大意按题号直接取原文，不调用模型。
        from . import explanations_direct
        if explanations_direct.install(values.batch_dir) is not None:
            _mark_stage_completed(values.batch_dir, 'explanations')
            print(json.dumps({"stage": "explanations", "status": "completed",
                              "detail": "按题号从已确定正文/答案材料直接取解析，未调用模型"},
                             ensure_ascii=False))
            return 0
    if values.stage == 'audio' and not values.dry_run and _audio_evidence_ready(values.batch_dir):
        # 切段器已产出并通过契约校验：不再调用模型，避免慢调用与无谓改写。
        _mark_stage_completed(values.batch_dir, 'audio')
        print(json.dumps({"stage": "audio", "status": "completed",
                          "detail": "切段产物已通过校验，未调用模型"}, ensure_ascii=False))
        return 0
    if values.stage == 'audio' and not values.dry_run:
        evidence_path = values.batch_dir / '02_audio' / 'segment-evidence.json'
        if evidence_path.is_file():
            print(json.dumps({"stage": "audio", "status": "needs_review", "completed": False,
                              "detail": "现有切片边界证据不足；候选已保留。重新切段或核对边界，不再让模型重写证据。"},
                             ensure_ascii=False))
            return 3
    if values.config is not None or configured():
        from . import local_agent
        local_args = ['--stage', values.stage, '--batch-dir', str(values.batch_dir)]
        if values.config is not None:
            local_args += ['--config', str(values.config)]
        if values.dry_run:
            local_args.append('--dry-run')
        if values.stage == 'review' and not values.dry_run:
            return _run_review_rounds(values.batch_dir, values.config)
        code = local_agent.main(local_args)
        # 直接调用也要登记台账，避免“产物已写、状态仍是 pending”导致重复导入。
        if code == 0 and not values.dry_run:
            _mark_stage_completed(values.batch_dir, values.stage)
        return code
    from . import agent_protocol
    # 不需要任何 Agent CLI 或登录状态。任务包可交给 GUI、IDE、CLI 中的任意 Agent。
    return agent_protocol.main(['export' if values.dry_run else 'dispatch',
                                '--stage', values.stage, '--batch-dir', str(values.batch_dir)])


if __name__ == '__main__':
    raise SystemExit(main())
