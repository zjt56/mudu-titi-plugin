# -*- coding: utf-8 -*-
"""录题自动化任务工作流执行器。

用法（在技能根目录运行）：

    python -m workflow.runner init --batch-dir workflows/AH-2022 --source 原卷.docx \\
        --answers 答案.docx --audio 听力.mp3 --year 2022 --region 安徽
    python -m workflow.runner status  --batch-dir workflows/AH-2022
    python -m workflow.runner run     --batch-dir workflows/AH-2022
    python -m workflow.runner validate --batch-dir workflows/AH-2022
    python -m workflow.runner review-gate --batch-dir workflows/AH-2022
    python -m workflow.runner mark --batch-dir workflows/AH-2022 \\
        --stage upload_gate --status approved --actor editor --note "上传前已核"
    python -m workflow.runner upload --batch-dir workflows/AH-2022 --yes

执行器只做三件事：调度阶段、记录状态、判断闸门。
structure_gate 已改为自动机器闸门（去噪+元信息抽取+按题型结构化+自检），无需人工标记；
它不会自动接受 AI 候选，不会自动通过人工闸门，也不会自动发布。
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from workflow import (activity, checks, contracts, doctor, ingest, inventory,  # type: ignore
                          manifest as mf, report, review)
else:
    from . import activity, checks, contracts, doctor, ingest, inventory, independent_review, manifest as mf, report, review

if __package__ in (None, ""):
    from workflow import independent_review

if __package__ in (None, ""):
    from workflow import structure_checks, structure_gate
else:
    from . import structure_checks, structure_gate

SKILL_ROOT = Path(__file__).resolve().parent.parent
TERMINAL = mf.TERMINAL_OK


# ---------------------------------------------------------------- 控制台编码
#
# Windows 上 Python 的标准输出默认是 cp936(GBK) 管道。只要输出里有一个 GBK
# 编不出的字符（U+FFFD、emoji、部分符号），print 就抛 UnicodeEncodeError。
# 阶段命令明明已经成功落盘，父进程却在回显日志那一行崩掉，阶段就永远停在
# running，监督器还会每 30 秒把整卷（含 OCR）重跑一遍。这里做两件事：
#   1) 本进程 stdio 改成 errors="replace"：任何字符都不会再让流水线崩溃；
#   2) 阶段子进程强制 UTF-8 输出：父进程按 UTF-8 解码，日志不再是乱码。

def _harden_stdio() -> None:
    """Make our own stdout/stderr unable to raise on unencodable characters."""
    import sys as _sys
    for stream in (_sys.stdout, _sys.stderr):
        if stream is None:
            continue
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _stage_env() -> dict:
    """Environment for stage subprocesses: stdout/stderr in UTF-8.

    Without this the child writes GBK bytes into the pipe while this process
    decodes UTF-8, so every Chinese line becomes U+FFFD (and then cannot be
    printed back to a GBK console at all).
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _echo(text: str) -> None:
    """Echo one line; a console that cannot encode it must never abort a stage."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"))
    except ValueError:
        pass


_harden_stdio()


# ---------------------------------------------------------------- 小工具

def _skill_version() -> str:
    path = SKILL_ROOT / "SKILL.md"
    if not path.is_file():
        return "unknown"
    match = re.search(r"^\s*version:\s*(\S+)", path.read_text(encoding="utf-8"), re.M)
    return match.group(1).strip() if match else "unknown"


def _print_header(title: str) -> None:
    print(f"\n== {title} ==")


def _render(argv: tuple, batch_dir: Path) -> list[str]:
    mapping = {
        "{batch_dir}": str(batch_dir),
        "{batch_json}": str(batch_dir / "06_batch" / "batch.json"),
        "{batch_meta}": str(batch_dir / "06_batch" / "batch_meta.json"),
        "{batch_meta_base}": str(batch_dir / "06_batch" / "batch_meta.base.json"),
        "{media_dir}": str(batch_dir / "media_upload"),
        "{docx}": "",
        "{python}": sys.executable,
    }
    manifest = mf.load(batch_dir) if mf.manifest_path(batch_dir).is_file() else {}
    docx = manifest.get("inputs", {}).get("docx", {}).get("path")
    mapping["{docx}"] = docx or ""
    return [mapping.get(token, token) for token in argv]


def _log_path(batch_dir: Path, stage_id: str) -> Path:
    path = batch_dir / "workflow" / "logs" / f"{stage_id}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _record_success(batch_dir: Path, manifest: dict, stage: contracts.Stage) -> bool:
    """Record a real success; a failing ownership hook is a stage failure, not a crash."""
    from . import batch_fill
    if (batch_dir / batch_fill.STATE).is_file():
        if stage.id == "meta":
            metadata = json.loads((batch_dir / "06_batch/batch_meta.base.json").read_text(encoding="utf-8-sig"))
            batch_fill.sync(batch_dir, "metadata", metadata)
            # This controlled metadata enrichment belongs to the same batch.
            # It changes no accepted question body or semantic labels.
            assembled = manifest.get("stages", {}).get("assemble", {})
            if assembled.get("status") in mf.TERMINAL_OK:
                spec = contracts.STAGE_BY_ID["assemble"]
                assembled["output_hashes"] = mf.output_hashes(batch_dir, spec)
                mf.record_semantics(batch_dir, manifest, spec)
        elif stage.id == "format_gate":
            try:
                batch_fill.mark_finalized(batch_dir)
            except ValueError as exc:
                # Sealing needs the confirmed structure and the batch objects.
                # A corrupt structure must surface as a diagnosable blocker so
                # the structure gate can rerun, never as a crash that hides it.
                mf.touch_stage(manifest, stage.id, mf.FAILED, exit_code=1,
                               notes="固化批次失败：" + str(exc)[:300])
                manifest.setdefault("stages", {}).setdefault("structure_gate", {})[
                    "rebuild_required"] = True
                print("  ✗ 固化批次失败：" + str(exc)[:160])
                return False
    mf.touch_stage(
        manifest, stage.id, mf.COMPLETED,
        input_hashes=mf.input_hashes(batch_dir, manifest, stage),
        output_hashes=mf.output_hashes(batch_dir, stage),
        exit_code=0, kind=contracts.STAGE_BY_ID.get(stage.id, stage).kind,
        # Clear any stale failure note so a genuinely successful re-run does not
        # keep reporting a previous "passed: false" in the status table.
        notes="",
    )
    mf.record_delivery(batch_dir, manifest, stage)
    mf.mark_delivery_consumed(batch_dir, stage.id)
    return True


def _mark_crashed(batch_dir: Path, manifest: dict, stage: contracts.Stage,
                  exc: BaseException) -> None:
    """Un-wedge a stage left in `running` because the executor itself blew up.

    Only `running` is touched: a stage that already reached a terminal state
    must keep its verdict, and a stage that never started must stay pending.
    """
    from .local_agent import _agent_lock
    try:
        with _agent_lock(30):
            current = mf.load(batch_dir)
            status = current.get("stages", {}).get(stage.id, {}).get("status")
            if status != mf.RUNNING:
                return
            mf.touch_stage(current, stage.id, mf.FAILED, exit_code=1,
                           notes="执行器异常：" + str(exc)[:300])
            mf.save(batch_dir, current)
            if current is not manifest:
                manifest.clear()
                manifest.update(current)
    except Exception:  # noqa: BLE001 - the original exception is what matters
        pass


def _execute_command(batch_dir: Path, manifest: dict, stage: contracts.Stage,
                     timeout: int = 1800) -> int:
    """Run one stage command without ever leaving the stage wedged in `running`.

    The costly failure this guards: `touch_stage(RUNNING)` is already saved when
    the executor crashes, so the stage looks live forever, the supervisor keeps
    re-dispatching it, and every attempt redoes the whole extraction (and its
    PDF OCR). The crash must become a diagnosable `failed` instead.
    """
    try:
        # Streams may have been replaced since import (worker threads, harnesses
        # that capture stdout); re-assert the non-throwing policy before any echo.
        _harden_stdio()
        return _execute_command_stage(batch_dir, manifest, stage, timeout)
    except Exception as exc:  # noqa: BLE001 - re-raised after making it visible
        _mark_crashed(batch_dir, manifest, stage, exc)
        raise


def _execute_command_stage(batch_dir: Path, manifest: dict, stage: contracts.Stage,
                           timeout: int = 1800) -> int:
    rendered = _render(stage.command, batch_dir)
    # 契约里的命令首项是仓库内脚本路径，需要补解释器；
    # 代理桥等外部命令自带解释器，不能重复加 python。
    argv = [sys.executable] + rendered if rendered and rendered[0].lower().endswith(".py") else rendered
    log = _log_path(batch_dir, stage.id)
    print(f"  执行：{' '.join(argv[1:])}")
    from .local_agent import _agent_lock
    with _agent_lock(timeout):
        current = mf.load(batch_dir)
        mf.touch_stage(current, stage.id, mf.RUNNING, log=str(log.relative_to(batch_dir)))
        mf.save(batch_dir, current)
        if current is not manifest:  # 同一对象时 clear+update 会把自己清空
            manifest.clear()
            manifest.update(current)
    try:
        proc = subprocess.run(argv, cwd=str(SKILL_ROOT), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, env=_stage_env())
        code, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        code, out, err = 124, "", f"超时 {timeout}s"
    except OSError as exc:
        code, out, err = 127, "", f"无法启动命令：{exc}"
    log.write_text(f"$ {' '.join(argv)}\n\n{out}\n\n[stderr]\n{err}", encoding="utf-8")
    for line in (out or "").splitlines()[-5:]:
        _echo("    " + line.strip()[:120])
    # Source partitioning is part of extraction ownership; publish its semantic
    # baseline only after this deterministic and model-assisted stage is final.
    if code == 0 and stage.id == "extract":
        try:
            from . import structure_ai
            reviewed = structure_ai.auto_review(batch_dir)
            if reviewed.get("ran") and reviewed.get("skipped"):
                print(f"  · 结构 AI 复核：已将 {len(reviewed['skipped'])} 行答案/解析移入附录")
        except (OSError, ValueError, RuntimeError, TypeError) as exc:
            print(f"  · 结构 AI 复核跳过：{str(exc)[:160]}")
    if code == 0 and stage.id == "extract":
        # Metadata is an extract product: finish enrichment before recording its
        # hashes, otherwise a fresh extraction immediately invalidates itself.
        try:
            from . import metadata_ai
            with activity.track(batch_dir, "extract", "正在识别卷头与试卷元信息"):
                metadata_ai.auto_identify(batch_dir)
        except (OSError, ValueError, RuntimeError, TypeError) as exc:
            print(f"  · 元信息识别跳过：{str(exc)[:160]}")
    with _agent_lock(timeout):
        current = mf.load(batch_dir)
        # Another actor may have completed this stage while the command ran.
        # Never replace its terminal result with a late failure or success.
        if current.get("stages", {}).get(stage.id, {}).get("status") in mf.TERMINAL_OK:
            manifest.clear()
            manifest.update(current)
            return code
        if code != 0:
            source_kind = contracts.STAGE_BY_ID.get(stage.id, stage).kind
            failed_status = mf.NEEDS_REVIEW if code == 3 and source_kind == contracts.AGENT else mf.FAILED
            mf.touch_stage(current, stage.id, failed_status, exit_code=code,
                           notes=(err or out).strip().splitlines()[-1][:300] if (err or out) else "执行失败")
            label = "待复核" if failed_status == mf.NEEDS_REVIEW else "失败"
            print(f"  {'⏸' if failed_status == mf.NEEDS_REVIEW else '✗'} {label}（exit {code}），日志：{log.relative_to(batch_dir)}")
        else:
            missing = mf.missing_produces(batch_dir, stage)
            if missing:
                mf.touch_stage(current, stage.id, mf.FAILED, exit_code=1,
                               notes="命令退出成功但缺少产物：" + "、".join(missing))
                print("  ✗ 缺少阶段产物：" + "、".join(missing))
                code = 1
            else:
                try:
                    accepted = _record_success(batch_dir, current, stage)
                except (ValueError, OSError, KeyError, TypeError) as exc:
                    # A process exit of zero is only a candidate delivery. A
                    # rejected source/body sync must remain a recoverable stage
                    # failure, not escape and leave the whole driver 'running'.
                    accepted = False
                    mf.touch_stage(current, stage.id, mf.FAILED, exit_code=1,
                                   notes='阶段产物校验/回填失败：' + str(exc)[:500])
                    with log.open('a', encoding='utf-8') as handle:
                        handle.write('\n[delivery-rejected]\n' + str(exc) + '\n')
                if accepted:
                    print(f"  ✓ 完成，日志：{log.relative_to(batch_dir)}")
                else:
                    print(f"  ✗ 产物未通过交付校验，日志：{log.relative_to(batch_dir)}")
                    code = 1
        mf.save(batch_dir, current)
        if current is not manifest:  # 同一对象时 clear+update 会把自己清空
            manifest.clear()
            manifest.update(current)
    return code


# ---------------------------------------------------------------- 闸门实现

def _validate_inputs(batch_dir: Path, manifest: dict) -> tuple:
    """校验实际读取的证据：正式批次、原卷正文与结构确认（audit_batch_source 依赖）。"""
    inputs = manifest.get("inputs", {})
    source = inputs.get("source", {})
    original = source.get("path") or ""
    evidence = {"source": json.dumps(source, sort_keys=True, ensure_ascii=False),
                "original": mf.sha256_file(original) if original else ""}
    for path in ("06_batch/batch_meta.json", "01_source/正文.txt",
                 "01_source/structure.json", "01_source/structure.confirmed.json"):
        evidence[path] = mf.sha256_file(batch_dir / path)
    evidence["answers_json"] = mf.sha256_file(batch_dir / "01_source_ans" / "answers.json")
    return evidence


@activity.observed("validate", "正在执行本地内容体检")
def do_validate(batch_dir: Path, manifest: dict) -> int:
    batch_json = batch_dir / "06_batch" / "batch_meta.json"
    if not batch_json.is_file():
        report = {"passed": False, "error": "最终校验对象 batch_meta.json 不存在",
                  "checks": [], "blocking_failures": ["FINAL_BATCH_MISSING"], "warnings": [], "skipped": []}
        target = checks.write_report(batch_dir, report)
        mf.touch_stage(manifest, "validate", mf.FAILED, notes=report["error"])
        mf.save(batch_dir, manifest)
        print(f"  ✗ {report['error']}；报告：{target.relative_to(batch_dir)}")
        return 1
    final_batch_hash = mf.sha256_file(batch_json)
    stage = contracts.STAGE_BY_ID["validate"]
    mf.touch_stage(manifest, "validate", mf.RUNNING)
    evidence_before = _validate_inputs(batch_dir, manifest)
    report = checks.run_all(SKILL_ROOT, batch_json, manifest.get("inputs", {}))
    # 上传前本地预检前移到 validate：不访问网络、不冻结文件，但提前暴露最终批次、
    # 解析来源、媒体引用和 payload 大小问题，避免用户点击上传后才第一次发现。
    try:
        from . import upload as upload_flow
        report["upload_local_preflight"] = upload_flow.local_preflight(batch_dir)
    except (OSError, ValueError, TypeError) as exc:
        report["upload_local_preflight"] = {"passed": False, "network_checked": False,
                                             "errors": [{"code": "LOCAL_PREFLIGHT_ERROR", "message": str(exc)}]}
    # 这是提前提示，不替代现有 validate 阻断契约；真正上传时仍由 prepare/verify 严格拦截。
    if not report["upload_local_preflight"].get("passed"):
        report.setdefault("warnings", []).append("UPLOAD_LOCAL_PREFLIGHT")
    # 脚本的字段/目录体检不能代替原卷逐题覆盖与材料归属复核。
    try:
        audit = source_audit_for_batch(batch_dir, json.loads(batch_json.read_text(encoding="utf-8-sig")))
    except (OSError, ValueError, TypeError, ImportError) as exc:
        audit = {"passed": False, "issues": [{"code": "SOURCE_AUDIT_UNAVAILABLE", "message": str(exc)}]}
    # 跳过听力的批次只豁免听力单元的题图/选项内容；非听力单元的题干、选项、
    # 题图与共享材料仍逐项对照原卷。
    report["source_audit"] = audit
    # 图片/音频引用缺失属 AI 能力受限项：只作提示，不阻断本地体检；
    # 结构、题号、题干、选项、材料归属等文本类问题仍是硬阻断。
    media_codes = {"SOURCE_IMAGE_MISSING", "OPTION_CONTENT_MISMATCH",
                   "SOURCE_OPTIONS_INCOMPLETE", "SHARED_OPTIONS_MISMATCH"}
    text_issues = [issue for issue in audit.get("issues", [])
                   if issue.get("code") not in media_codes]
    media_issues = [issue for issue in audit.get("issues", [])
                    if issue.get("code") in media_codes]
    if media_issues:
        report.setdefault("warnings", []).append("SOURCE_MEDIA_UNVERIFIED")
    report.setdefault("checks", []).append({
        "check": "原卷逐题覆盖与材料归属 source_audit", "blocking": True,
        "passed": not text_issues,
        "output": "；".join(issue.get("message", "") for issue in (text_issues or media_issues)[:5]),
        "note": "图片/音频能力受限项只报告，不阻断" if media_issues and not text_issues else "",
    })
    if text_issues:
        report.setdefault("blocking_failures", []).append("SOURCE_SLICE_MISMATCH")
        report["passed"] = False
    report["checked_at"] = mf.now_iso()
    report["batch_sha256"] = final_batch_hash
    if not batch_json.is_file() or mf.sha256_file(batch_json) != final_batch_hash:
        report["passed"] = False
        report["error"] = "体检期间批次文件发生变化，请重新体检"
    print(f"  体检项目 {len(report.get('checks', []))} 个；"
          f"阻断失败 {len(report.get('blocking_failures', []))} 个；"
          f"提示 {len(report.get('warnings', []))} 个；"
          f"跳过 {len(report.get('skipped', []))} 个")
    for item in report.get("checks", []):
        mark = "✓" if item["passed"] else ("⚠" if not item["blocking"] else "✗")
        print(f"    {mark} {item['check']}")
    for item in report.get("skipped", []):
        print(f"    · {item['check']} 跳过（{item['reason']}）")
    # 提交前在锁内重读台账并复核实际证据；并发的无关更新必须保留。
    from .local_agent import _agent_lock
    with _agent_lock(1500):
        try:
            current = mf.load(batch_dir)
        except FileNotFoundError:
            current = manifest
        if _validate_inputs(batch_dir, current) != evidence_before:
            if current is not manifest:
                manifest.clear()
                manifest.update(current)
            print("  ✗ 体检期间原卷/批次/答案发生变化，请重新体检")
            return 1
        if report.get("error"):
            target = checks.write_report(batch_dir, report)
            mf.touch_stage(current, "validate", mf.FAILED, notes=report["error"])
            mf.save(batch_dir, current)
            manifest.clear()
            manifest.update(current)
            print(f"  ✗ {report['error']}")
            return 1
        # 只在证据未变时发布报告，避免用过期结论覆盖并发结果。
        target = checks.write_report(batch_dir, report)
        if report.get("passed"):
            _record_success(batch_dir, current, stage)
            current["stages"]["validate"]["notes"] = f"报告：{target.relative_to(batch_dir)}"
        else:
            mf.touch_stage(current, "validate", mf.FAILED,
                           notes="阻断项：" + "、".join(report.get("blocking_failures", [])))
        mf.save(batch_dir, current)
        if current is not manifest:  # 同一对象时 clear+update 会把自己清空
            manifest.clear()
            manifest.update(current)
    if report.get("passed"):
        print("  ✓ 本地体检通过")
        return 0
    print("  ✗ 本地体检未通过，见 07_review/validation-report.json")
    return 1


# 听力单元按用户规则整体跳过、不进入题库；其内容来自音频与题图，无法也不应
# 按文字保真复核。仅豁免这些"音频派生内容"项，结构、题号、材料归属仍检查。
_LISTENING_SKIP_CODES = frozenset((
    "SOURCE_IMAGE_MISSING", "OPTION_CONTENT_MISMATCH",
    "SOURCE_OPTIONS_INCOMPLETE", "SHARED_OPTIONS_MISMATCH",
))


def source_audit_for_batch(batch_dir: Path, payload: dict) -> dict:
    """原卷逐题覆盖审计；显式跳过听力的批次不要求听力单元的题图/选项内容。"""
    from .question_content import audit_batch_source
    audit = audit_batch_source(batch_dir, payload)
    try:
        if (Path(batch_dir) / "02_audio" / "SKIP_LISTENING").is_file():
            from . import agent_tasks
            listening = {u["unit_id"] for u in agent_tasks.confirmed_units(Path(batch_dir))
                         if str(u.get("type", "")).startswith("听力")}
            if listening:
                issues = [issue for issue in audit.get("issues", [])
                          if not (issue.get("unit_id") in listening
                                  and issue.get("code") in _LISTENING_SKIP_CODES)]
                audit = dict(audit, issues=issues, passed=not issues)
    except (OSError, ValueError, KeyError, TypeError, ImportError):
        pass
    return audit


def flowchart_checks(batch_dir: Path, manifest: dict) -> dict:
    """复核真实切题证据与正式批次；日常审核不要求人工姓名或密钥。"""
    errors = []
    try:
        actual = batch_dir / "06_batch" / "batch_meta.json"
        final = json.loads(actual.read_text(encoding="utf-8-sig"))
        audit = source_audit_for_batch(batch_dir, final)
        if not audit.get("passed"):
            errors.extend({"code": issue.get("code", "SOURCE_SLICE_MISMATCH"),
                           "message": issue.get("message", "切题或正式批次与原卷不一致"),
                           "unit_id": issue.get("unit_id", "")}
                          for issue in audit.get("issues", []))
            if not audit.get("issues"):
                errors.append({"code": "SOURCE_SLICE_MISMATCH", "message": "原卷切题复核未完成"})
        gate_path = batch_dir / "07_review" / "structure-gate.json"
        structural = json.loads(gate_path.read_text(encoding="utf-8-sig"))
        if structural.get("passed") is not True:
            errors.append({"code": "STRUCTURE_REVIEW_INCOMPLETE", "message": "源头切题复核尚未通过"})
        # 输入绑定由结构闸门生成，旧版没有绑定的报告必须重跑。
        binding = structural.get("input_hashes", {})
        for rel in ("01_source/正文.txt", "01_source/structure.json", "01_source/structure.confirmed.json"):
            if binding.get(rel) != mf.sha256_file(batch_dir / rel) or not binding.get(rel):
                errors.append({"code": "STRUCTURE_EVIDENCE_STALE", "message": f"切题证据缺失或过期：{rel}"})
        original = manifest.get("inputs", {}).get("source", {}).get("path")
        if not original or not structural.get("original_sha256") or structural["original_sha256"] != mf.sha256_file(original):
            errors.append({"code": "ORIGINAL_EVIDENCE_STALE", "message": "原卷不存在或原件与切题证据不一致"})
    except (OSError, ValueError, TypeError, ImportError) as exc:
        errors.append({"code": "STRUCTURE_REVIEW_MISSING", "message": str(exc)})
    # 兼容显式启用的独立验签；不再要求用户为普通审核配置审核人身份。
    config = os.environ.get("MUDU_REVIEW_CONFIG")
    if config:
        try:
            conf = json.loads(Path(config).read_text(encoding="utf-8-sig"))
            actual = batch_dir / "06_batch" / "batch_meta.json"
            if not actual.is_file():
                raise ValueError("实际上传文件 batch_meta.json 不存在")
            configured = (batch_dir / conf["batch_file"]).resolve()
            if configured != actual.resolve():
                raise ValueError("审核批次与实际上传文件不一致")
            independent = independent_review.evaluate_directory(batch_dir, config)
            if not independent.get("passed"):
                errors.extend({"code": e.get("code", "INDEPENDENT_REVIEW_FAILED"),
                               "message": e.get("detail", "独立审核未完成")}
                              for e in independent.get("errors", []))
                if not independent.get("errors"):
                    errors.append({"code": "INDEPENDENT_REVIEW_FAILED", "message": "独立审核未通过"})
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors.append({"code": "INDEPENDENT_CONFIG_INVALID", "message": str(exc)})
    return {"passed": not errors, "errors": errors}


def _record_structure_findings(batch_dir: Path, report: dict) -> None:
    """把结构闸门的真实阻断作为可见结构疑点写入工作台队列，供人工确认或修正。"""
    path = batch_dir / "07_review" / "manual-review-queue.json"
    queue = {}
    if path.is_file():
        try:
            queue = json.loads(path.read_text(encoding="utf-8-sig")) or {}
        except (ValueError, OSError):
            queue = {}
    items = queue.get("items") if isinstance(queue.get("items"), list) else []
    structure = {}
    structure_path = batch_dir / "01_source" / "structure.json"
    if structure_path.is_file():
        try:
            structure = json.loads(structure_path.read_text(encoding="utf-8-sig")) or {}
        except (ValueError, OSError):
            structure = {}
    unresolved = [issue for issue in structure.get("unresolved") or [] if isinstance(issue, dict)]
    blockers = report.get("blockers") or []
    existing = {item.get("finding_id"): item for item in items if isinstance(item, dict)}
    changed = False
    for blocker in blockers:
        code = str(blocker.get("code") or "")
        if code in ("ANSWER_APPENDIX_DETECTED",):
            continue
        reason = str(blocker.get("message") or code)
        matching = next((u for u in unresolved if u.get("code") == code and u.get("message") == reason), {})
        line = blocker.get("source_line") or matching.get("line")
        import hashlib
        identity = json.dumps([code, line, reason], ensure_ascii=False, separators=(",", ":"))
        fid = f"STRUCT-{code}-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
        evidence = [f"正文第{line}行"] if line else ["原卷结构核对"]
        current = existing.get(fid)
        if current is None:
            # Reuse an exact legacy identity without depending on how many
            # same-code findings happen to remain in this run.
            legacy = [x for x in items if x.get("structure_code") == code and x.get("reason") == reason
                      and (x.get("source_line") == line if "source_line" in x else x.get("evidence") == evidence)]
            if len(legacy) == 1:
                current = legacy[0]
        if current is not None and current.get("source_line") != line:
            current["source_line"] = line
            changed = True
        if current is not None:
            # 结构重新提取后确认证据可能失效：当前结构仍未解决就重置为待处理，避免过期 fixed 隐藏真实阻断。
            if current.get("status") not in ("open", "pending", "needs_review", "blocked"):
                current["status"] = "open"
                current["reason"] = reason
                current["evidence"] = evidence
                changed = True
            continue
        items.append({
            "finding_id": fid,
            "unit_id": "",
            "review_type": "structure",
            "structure_code": code,
            "source_line": line,
            "field": "number" if code == "QUESTION_NUMBER_INFERRED" else "structure",
            "severity": "major",
            "reason": reason,
            "question": "结构确认",
            "evidence": evidence,
            "status": "open",
            "default_action": "核对原卷后确认或修正，再重跑结构闸门",
        })
        existing[fid] = items[-1]
        changed = True
    if changed:
        queue.setdefault("queue_version", "1.0")
        queue["items"] = items
        if path.exists():
            path.with_suffix(".json.bak").write_bytes(path.read_bytes())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _reconcile_structure_findings(batch_dir: Path, report: dict) -> int:
    """Close historical structure tasks only after a fresh, bound gate pass."""
    if report.get("passed") is not True:
        return 0
    binding = report.get("input_hashes", {})
    for relative in ("01_source/正文.txt", "01_source/structure.json", "01_source/structure.confirmed.json"):
        if not binding.get(relative) or binding[relative] != mf.sha256_file(batch_dir / relative):
            return 0
    manifest = mf.load(batch_dir)
    original = manifest.get("inputs", {}).get("source", {}).get("path")
    if not original or not report.get("original_sha256") or mf.sha256_file(original) != report["original_sha256"]:
        return 0
    path = batch_dir / "07_review/manual-review-queue.json"
    if not path.is_file():
        return 0
    queue = json.loads(path.read_text(encoding="utf-8-sig"))
    changed = []
    for item in queue.get("items", []):
        if item.get("review_type") != "structure" or item.get("status") in ("fixed", "closed", "resolved", "accepted"):
            continue
        item.update(status="fixed", decided_by="automatic-structure-gate", decided_at=mf.now_iso(),
                    resolution_note="当前原卷、正文与结构已重新运行结构闸门并通过；旧结构疑点由当前绑定证据确认消除，非人工核验声明。",
                    resolution_evidence={"method": "fresh_structure_gate_pass", "input_hashes": binding,
                                         "original_sha256": report["original_sha256"]})
        changed.append(dict(item))
    if changed:
        path.with_suffix(".json.bak").write_bytes(path.read_bytes())
        path.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with (batch_dir / "workflow/decisions.jsonl").open("a", encoding="utf-8") as stream:
            for item in changed:
                stream.write(json.dumps(item, ensure_ascii=False) + "\n")
    return len(changed)


def _structure_inputs(batch_dir: Path, manifest: dict) -> tuple:
    """Capture the actual evidence read by the structure gate, including absences."""
    source = manifest.get("inputs", {}).get("source", {})
    original = source.get("path") or ""
    paths = ("01_source/正文.txt", "01_source/structure.json", "01_source/原始正文.txt")
    return (json.dumps(source, sort_keys=True, ensure_ascii=False),
            tuple(mf.sha256_file(batch_dir / path) for path in paths),
            mf.sha256_file(original) if original else "")


@activity.observed("structure_gate", "正在确认题目结构与来源边界")
def do_structure_gate(batch_dir: Path, manifest: dict) -> int:
    """Check the immutable extraction delivery and publish only a gate result."""
    from .local_agent import _agent_lock
    with _agent_lock(1500):
        current = mf.load(batch_dir)
        binding = _structure_inputs(batch_dir, current)
        stage_before = json.dumps(current["stages"]["structure_gate"], sort_keys=True)
    # Put temporary output on the same filesystem, outside the official paths.
    with tempfile.TemporaryDirectory(prefix=".structure-gate-", dir=batch_dir / "workflow") as tmp:
        staged = Path(tmp)
        report = structure_gate.run_gate(batch_dir, output_dir=staged)
        counts = report.get("unit_counts", {})
        print(f"  结构化单元：共 {counts.get('total', 0)}（独立题 {counts.get('standalone', 0)}，"
              f"组合题 {counts.get('group', 0)}）；去除无关行 {report.get('removed_count', 0)} 行")
        for blocker in report.get("blockers", [])[:10]:
            print(f"    ✗ {blocker.get('code')}: {blocker.get('message')}")
        for note in report.get("resolved_evidence", [])[:10]:
            print(f"    ✓ 已核对 {note.get('code')}: {note.get('resolution')}")
        def fresh(state):
            report_binding = (report.get("source_sha256") or "",
                              report.get("structure_sha256") or "",
                              report.get("original_sha256") or "")
            expected_binding = (binding[1][0], binding[1][1], binding[2])
            input_hashes = report.get("input_hashes", {})
            return (_structure_inputs(batch_dir, state) == binding and
                    json.dumps(state["stages"]["structure_gate"], sort_keys=True) == stage_before and
                    report_binding == expected_binding and
                    (input_hashes.get("01_source/正文.txt") or "") == binding[1][0] and
                    (input_hashes.get("01_source/structure.json") or "") == binding[1][1])

        def reject_stale(state):
            manifest.clear()
            manifest.update(state)
            print("  ✗ 结构闸门运行期间输入或阶段状态发生变化，请重试")
            return 1

        if not report.get("passed") and report.get("layout_required"):
            # Avoid dispatching OCR for evidence already known to be stale. OCR
            # can acquire other locks, so it must run outside the agent lock.
            with _agent_lock(1500):
                current = mf.load(batch_dir)
                if not fresh(current):
                    return reject_stale(current)
            from .layout_dispatch import prepare_layout_fallback
            try:
                fallback = prepare_layout_fallback(batch_dir, current, report)
            except (OSError, ValueError, TypeError) as exc:
                fallback = {"status": "needs_review", "dispatch_state": "unavailable", "reason": str(exc)}
            report["layout_fallback"] = fallback
            print(f"  · 版面复核：{fallback.get('dispatch_state', 'needs_review')}；证据未完成前不放行")
        with _agent_lock(1500):
            current = mf.load(batch_dir)
            if not fresh(current):
                return reject_stale(current)
            confirmed = staged / "01_source/structure.confirmed.json"
            if report.get("passed"):
                # Never publish a claimed pass without its corresponding artifact.
                if not confirmed.is_file() or mf.sha256_file(confirmed) != report.get("confirmed_sha256"):
                    print("  ✗ 结构确认产物缺失或不匹配，请重试")
                    manifest.clear()
                    manifest.update(current)
                    return 1
                detected = json.loads(confirmed.read_text(encoding="utf-8")).get("meta", {})
                if isinstance(detected, dict):
                    old_meta = current.get("meta", {})
                    current.setdefault("submitted_meta", dict(old_meta))
                    current["meta"] = {**old_meta, **{k: v for k, v in detected.items()
                                                        if v not in (None, "", [], {})}}
            if confirmed.is_file():
                target = batch_dir / "01_source/structure.confirmed.json"
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(confirmed, target)
            target = batch_dir / "07_review/structure-gate.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            staged_report = staged / "07_review/structure-gate.json"
            staged_report.parent.mkdir(parents=True, exist_ok=True)
            staged_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(staged_report, target)
            if report.get("passed"):
                _reconcile_structure_findings(batch_dir, report)
                _record_success(batch_dir, current, contracts.STAGE_BY_ID["structure_gate"])
                current["stages"]["structure_gate"]["notes"] = (
                    f"自动确认：{counts.get('total', 0)} 单元"
                    f"（独立{counts.get('standalone', 0)}/组合{counts.get('group', 0)}）")
            else:
                _record_structure_findings(batch_dir, report)
                mf.touch_stage(current, "structure_gate", mf.BLOCKED,
                               notes=f"{len(report.get('blockers', []))} 条未解决")
            mf.save(batch_dir, current)
            manifest.clear()
            manifest.update(current)
        if report.get("passed"):
            print("  ✓ 结构自动确认通过，可进入题型判定")
            return 0
        print("  ✗ 结构自动闸门未通过")
        return 1


def _review_inputs(batch_dir: Path, manifest: dict) -> dict:
    """审核闸门实际读取的审核文件、正式批次与原卷证据。"""
    evidence = {}
    for name in ("content-review.json", "metadata-review.json",
                 "manual-review-queue.json", "unchecked.json", "review-rounds.json"):
        evidence["07_review/" + name] = mf.sha256_file(batch_dir / "07_review" / name)
    for path in ("06_batch/batch_meta.json", "01_source/正文.txt",
                 "01_source/structure.confirmed.json"):
        evidence[path] = mf.sha256_file(batch_dir / path)
    original = manifest.get("inputs", {}).get("source", {}).get("path") or ""
    evidence["original"] = mf.sha256_file(original) if original else ""
    evidence["review_config"] = os.environ.get("MUDU_REVIEW_CONFIG", "")
    return evidence


def unreviewed_summary(batch_dir: Path, manifest: dict) -> dict:
    """Read-only description and revision of the review state being confirmed."""
    result = review.evaluate(batch_dir)
    evidence = _review_inputs(batch_dir, manifest)
    for rel in review.ROUND_INPUTS:
        evidence[rel] = mf.sha256_file(batch_dir / rel)
    for rel in review.ROUND_RULES:
        evidence['rule:' + rel] = mf.sha256_file(SKILL_ROOT / rel)
    config = os.environ.get('MUDU_REVIEW_CONFIG', '')
    evidence['review_config_sha256'] = mf.sha256_file(config) if config else ''
    description = {'passed': result.get('passed') is True,
                   'counts': result.get('counts', {}), 'errors': result.get('errors', [])}
    raw = json.dumps({'evidence': evidence, 'review': description}, sort_keys=True,
                     ensure_ascii=False, default=str).encode('utf-8')
    return {**description, 'revision': hashlib.sha256(raw).hexdigest()}


def do_review_gate(batch_dir: Path, manifest: dict, *, lock_held=False) -> int:
    evidence_before = _review_inputs(batch_dir, manifest)
    result = review.evaluate(batch_dir)
    # 放行时重新检查实际批次，不能仅凭旧审核文件宣称标签正确。
    from workflow.label_audit import inspect_file
    labels = inspect_file(batch_dir / "06_batch" / "batch_meta.json")
    result["label_audit"] = labels
    result["errors"].extend(labels["errors"])
    result["passed"] = result["passed"] and labels["passed"]
    flow = flowchart_checks(batch_dir, manifest)
    result["errors"].extend(flow["errors"])
    result["passed"] = result["passed"] and flow["passed"]
    print(f"  阻断项 {result['counts']['blocking_findings']} 个；"
          f"人工队列 {result['counts']['open_manual_items']} 项；"
          f"必检未完成 {result['counts']['required_unchecked']} 项")
    for err in result["errors"][:10]:
        print(f"    ✗ {err.get('code')}: {err.get('message') or err.get('reason') or ''}")
    if len(result["errors"]) > 10:
        print(f"    …另有 {len(result['errors']) - 10} 条")
    # 提交前锁内重读台账并复核审核证据；并发的无关更新必须保留，过期判断不放行。
    from .local_agent import _agent_lock
    with (contextlib.nullcontext() if lock_held else _agent_lock(1500)):
        try:
            current = mf.load(batch_dir)
        except FileNotFoundError:
            current = manifest
        if _review_inputs(batch_dir, current) != evidence_before:
            if current is not manifest:  # 同一对象时 clear+update 会把自己清空
                manifest.clear()
                manifest.update(current)
            print("  ✗ 审核闸门运行期间审核文件或原卷发生变化，请重新审核")
            return 1
        # 记录当前最终批次摘要，供下游 handoff 判定 review-gate 报告是否仍对应当前批次。
        try:
            final_meta = batch_dir / "06_batch" / "batch_meta.json"
            result["batch_sha256"] = mf.sha256_file(final_meta) if final_meta.is_file() else None
        except OSError:
            result["batch_sha256"] = None
        target = review.write_gate(batch_dir, result)
        if result["passed"]:
            _record_success(batch_dir, current, contracts.STAGE_BY_ID["review_gate"])
            current["stages"]["review_gate"]["notes"] = f"报告：{target.relative_to(batch_dir)}"
        elif result.get("manual_only"):
            # 用户流程目的：一审/二审找出差异交人工。除「待人工审核的差异」外无
            # 其它硬错时，视为审核已执行完成、可继续组装待上传批次；但绝不冒充
            # 质量通过：release_ready=False，quality_status=pending，交付为
            # delivered_unqualified，上传仍须人工在差异处理后显式确认。
            n = result["counts"]["open_manual_items"]
            mf.touch_stage(current, "review_gate", mf.NEEDS_REVIEW,
                           execution_completed=True,
                           continue_unqualified=True,
                           quality_status="pending",
                           release_ready=False,
                           notes=f"审核已执行完成，{n} 项差异待人工审核（未放行，未上传）")
            mf.record_delivery(batch_dir, current, contracts.STAGE_BY_ID["review_gate"])
            mf.save(batch_dir, current)
            if current is not manifest:
                manifest.clear()
                manifest.update(current)
            print(f"  · 审核闸门：已执行完成，{n} 项差异待人工审核；继续组装待上传批次（未放行）")
            return 0
        else:
            mf.touch_stage(current, "review_gate", mf.BLOCKED,
                           notes=f"{len(result['errors'])} 条未解决")
        mf.save(batch_dir, current)
        if current is not manifest:  # 同一对象时 clear+update 会把自己清空
            manifest.clear()
            manifest.update(current)
    if result["passed"]:
        print("  ✓ 审核闸门通过，可进入人工上传确认")
        return 0
    print("  ✗ 审核闸门未通过")
    return 1


# ---------------------------------------------------------------- 子命令

def cmd_init(args) -> int:
    batch_dir = Path(args.batch_dir).expanduser().resolve()
    batch_dir.mkdir(parents=True, exist_ok=True)
    conflicts = []
    def register(key, value, required):
        if not value:
            return
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            conflicts.append(f"{key} 不存在：{path}")
            return
        inputs[key] = {
            "path": str(path),
            "sha256": mf.sha256_file(path),
            "required": required,
            "exists": True,
        }
    inputs: dict = {}
    register("source", args.source, True)
    register("answers", args.answers, False)
    register("audio", args.audio, False)
    register("docx", args.docx or args.source, False)
    for index, path in enumerate(getattr(args, 'extra_documents', None) or [], 1):
        register(f'attachment_{index:03d}', path, False)
    for index, path in enumerate(getattr(args, 'extra_audio', None) or [], 1):
        register(f'audio_{index:03d}', path, False)
    if "source" not in inputs:
        print("✗ 必须提供存在的 --source 原卷"); return 2
    if conflicts:
        print("✗ 输入有误："); [print("   " + c) for c in conflicts]; return 2

    workflow_id = args.workflow_id or batch_dir.name
    meta = {"year": args.year, "region": args.region, "subject": args.subject or "english"}
    for key in ("title", "source_type", "grade"):
        value = getattr(args, key, None)
        if value:
            meta[key] = value
    manifest = mf.new_manifest(workflow_id, batch_dir, inputs,
                               {"skill_version": _skill_version(),
                                "constraints": "references/constraints.md"}, meta)
    for stage in contracts.STAGES:
        record = manifest["stages"][stage.id]
        record["kind"] = stage.kind
        if stage.requires and all(not inputs.get(key, {}).get("exists") for key in stage.requires):
            if stage.optional:
                mf.touch_stage(manifest, stage.id, mf.SKIPPED,
                               notes="缺少可选输入：" + "、".join(stage.requires))
    mf.save(batch_dir, manifest)
    mf.append_log(batch_dir, {"event": "init", "workflow_id": workflow_id})
    print(f"✓ 批次已初始化：{batch_dir}")
    print(f"  工作流：{workflow_id}  技能版本：{manifest['rules']['skill_version']}")
    for key, item in inputs.items():
        print(f"  {key:8s} {item['sha256'][:12]}  {item['path']}")
    print("\n下一步：python -m workflow.runner run --batch-dir", batch_dir)
    return 0


def cmd_status(args) -> int:
    batch_dir = Path(args.batch_dir).expanduser().resolve()
    manifest = mf.load(batch_dir)
    if args.json:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    print(f"批次：{manifest['workflow_id']}  （{manifest['status']} → {mf.derive_status(manifest)}）")
    print(f"目录：{batch_dir}")
    print(f"技能版本：{manifest['rules'].get('skill_version')}")
    print(f"\n{'阶段':<16}{'类型':<10}{'状态':<14}{'交付':<12}说明")
    print("-" * 92)
    for stage in contracts.STAGES:
        record = manifest["stages"].get(stage.id, {})
        status = record.get("status", "pending")
        stale, why = mf.is_stale(batch_dir, manifest, stage) if status in TERMINAL else (False, "")
        suffix = f"  ← 需重跑（{why}）" if stale else ""
        delivered = mf.delivery_status(batch_dir, stage.id)
        print(f"{stage.id:<16}{stage.kind:<10}{status:<14}{delivered:<12}{(record.get('notes') or '')[:28]}{suffix}")
    blocked = mf.blocking_stages(manifest)
    if blocked:
        print("\n阻断：")
        for item in blocked:
            print(f"  ✗ {item['stage']}（{item['status']}）：{item['reason']}")
    lacking = mf.input_blockers(manifest)
    if lacking:
        print("\n缺输入（补齐后重新 init 或手工登记）：")
        for item in lacking:
            print(f"  ⏸ {item['stage']}：缺少 {'、'.join(item['missing'])}")
    ready = mf.ready_stages(batch_dir, manifest)
    print("\n可执行：" + ("、".join(ready) or "无"))

    # 卡点诊断：把分散在日志/报告里的真实原因翻译成一句人话，直接给出修复命令。
    try:
        from workflow import diagnose as diag
        diag_result = diag.diagnose(batch_dir)
        head = next((it for it in diag_result["items"] if it["blocking"]), None)
        if head:
            print(f"\n卡点诊断 → {head['title']}（{head['stage']}）：{head['reason']}")
            if head["detail"]:
                print(f"  原因：{head['detail'][:300]}")
            if head["hint"]:
                print(f"  怎么办：{head['hint']}")
            if head["command"]:
                print(f"  修复：{head['command']}")
            print("  更多：python -m workflow.runner diagnose --batch-dir "
                  f"{batch_dir} --stage {head['stage']}")
    except (OSError, ValueError, KeyError):
        pass
    return 0


def cmd_diagnose(args) -> int:
    from workflow import diagnose as diag
    batch_dir = Path(args.batch_dir).expanduser().resolve()
    if args.stage:
        result = diag.explain(batch_dir, args.stage)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
        print(f"阶段：{result.get('stage')}  状态：{result.get('status')}")
        print(f"原因：{result.get('reason')}")
        if result.get("detail"):
            print(f"细节：{result['detail']}")
        if result.get("hint"):
            print(f"怎么办：{result['hint']}")
        if result.get("command"):
            print(f"修复：{result['command']}")
        return 0
    result = diag.diagnose(batch_dir)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    print(f"批次：{result['batch']}  状态：{result['status']}")
    print(f"概览：{result['summary']}")
    blocking = [it for it in result["items"] if it["blocking"]]
    waiting = [it for it in result["items"] if not it["blocking"]]
    if not blocking and not waiting:
        print("\n✓ 流程已完成，无卡点")
        return 0
    if blocking:
        print(f"\n真正卡点（{len(blocking)} 个）：")
        for it in blocking:
            print(f"\n  ● {it['title']}（{it['stage']}，{it['status']}）")
            print(f"    原因：{it['reason']}")
            if it["detail"]:
                print(f"    细节：{it['detail'][:300]}")
            if it["hint"]:
                print(f"    怎么办：{it['hint']}")
            if it["command"]:
                print(f"    修复：{it['command']}")
    if waiting:
        print(f"\n被上游阻塞、暂不处理（{len(waiting)} 个）："
              + "、".join(it["stage"] for it in waiting))
    return 0


def _run_batch_locked(batch_dir: Path, fn) -> int:
    """Serialize a complete public batch operation with protocol imports."""
    from .local_agent import _agent_lock
    with _agent_lock(1500):
        return fn()


@contextlib.contextmanager
def _single_run_lock(batch_dir: Path, timeout: float = 2.0):
    """Only one `runner run` may drive a batch at a time.

    The agent lock guards imports and is held by the very subprocesses a run
    spawns, so it cannot serialize the run itself: two concurrent runs would
    either time out on it or interleave manifest writes. This separate lock
    makes a second invocation fail fast instead of wasting model calls and
    corrupting the batch.
    """
    import time
    from . import supervisor
    lock_path = batch_dir / "workflow" / ".run.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        with supervisor._try_file_lock(lock_path) as acquired:
            if acquired:
                yield
                return
        if time.monotonic() >= deadline:
            raise RuntimeError("该批次已有运行中的流水线（runner run），请等待其结束")
        time.sleep(0.1)


def cmd_mark(args) -> int:
    batch_dir = Path(args.batch_dir).expanduser().resolve()
    return _run_batch_locked(batch_dir, lambda: _cmd_mark_locked(batch_dir, args))


def _cmd_mark_locked(batch_dir: Path, args) -> int:
    manifest = mf.load(batch_dir)
    stage = contracts.STAGE_BY_ID.get(args.stage)
    if not stage:
        print("✗ 未知阶段：" + args.stage); return 2
    status = args.status
    if stage.kind == contracts.GATE and status in (mf.COMPLETED, mf.APPROVED, mf.SKIPPED):
        print("✗ 机器闸门必须运行检查，不能手工标记通过或跳过"); return 2
    if stage.kind == contracts.HUMAN and status not in (mf.APPROVED, mf.BLOCKED, mf.NEEDS_REVIEW):
        print(f"✗ 人工闸门 {stage.id} 只能标记 approved / blocked / needs_review"); return 2
    if status == mf.COMPLETED or status == mf.APPROVED:
        missing = mf.missing_produces(batch_dir, stage)
        if missing and not args.allow_missing:
            print("✗ 缺少产物：" + "、".join(missing) + "（确认后加 --allow-missing 强记）"); return 2
    if stage.id == "upload_gate" and status == mf.APPROVED:
        if not args.actor or args.actor == "unknown":
            print("✗ 上传确认必须填写人工 --actor"); return 2
        unreviewed_revision = getattr(args, 'unreviewed_revision', '')
        attempt_token = getattr(args, 'unreviewed_attempt', '')
        if bool(unreviewed_revision) != bool(attempt_token):
            print('✗ 未审核上传须同时提供本次确认的版本与尝试标识'); return 2
        if unreviewed_revision:
            if unreviewed_summary(batch_dir, manifest)['revision'] != unreviewed_revision:
                print('✗ 审核状态已变化，请重新查看并确认'); return 2
            from workflow.label_audit import inspect_file
            labels = inspect_file(batch_dir / '06_batch' / 'batch_meta.json')
            flow = flowchart_checks(batch_dir, manifest)
            stale, reason = mf.is_stale(batch_dir, manifest, contracts.STAGE_BY_ID['validate'])
            if (not labels['passed'] or not flow['passed'] or stale
                    or manifest['stages'].get('validate', {}).get('status') != mf.COMPLETED):
                print('✗ 数据、结构或校验未通过：' + str(reason if stale else (labels.get('errors') or flow.get('errors')))); return 2
        elif do_review_gate(batch_dir, manifest, lock_held=True) != 0:
            print("✗ 审核未通过，不能确认上传"); return 2
        upload_file = batch_dir / "06_batch" / "batch_meta.json"
        if not upload_file.is_file():
            print("✗ 实际上传文件不存在"); return 2
        from workflow import upload as upload_flow, upload_client
        try:
            binding = upload_flow.approval_binding(batch_dir)
        except (OSError, ValueError, upload_client.APIError) as exc:
            print(f"✗ 上传冻结预检未通过：{exc}"); return 2
        manifest["stages"][stage.id].update(binding)
        manifest["stages"][stage.id]["approved_batch_sha256"] = mf.sha256_file(upload_file)
        manifest["stages"][stage.id].pop('unreviewed_confirmation', None)
        if unreviewed_revision:
            manifest["stages"][stage.id]['unreviewed_confirmation'] = {
                'revision': unreviewed_revision,
                'attempt_sha256': hashlib.sha256(attempt_token.encode()).hexdigest()}
    mf.touch_stage(manifest, stage.id, status, notes=args.note or "")
    if stage.kind == contracts.HUMAN:
        mf.record_decision(manifest, stage.id, status, args.actor or "unknown",
                           note=args.note or "")
    if status in (mf.COMPLETED, mf.APPROVED):
        record = manifest["stages"][stage.id]
        record["input_hashes"] = mf.input_hashes(batch_dir, manifest, stage)
        record["output_hashes"] = mf.output_hashes(batch_dir, stage)
    if args.invalidate_downstream and status in (mf.COMPLETED, mf.APPROVED):
        changed = mf.invalidate_downstream(batch_dir, manifest, stage.id,
                                           f"上游 {stage.id} 重新确认")
        downstream = [s for s in changed if s != stage.id]
        if downstream:
            print("  连带下游失效：" + "、".join(downstream))
    mf.save(batch_dir, manifest)
    mf.append_log(batch_dir, {"event": "mark", "stage": stage.id, "status": status,
                              "actor": args.actor})
    manifest["status"] = mf.derive_status(manifest)
    mf.save(batch_dir, manifest)
    print(f"✓ {stage.id} → {status}")
    return 0


def cmd_invalidate(args) -> int:
    batch_dir = Path(args.batch_dir).expanduser().resolve()
    return _run_batch_locked(batch_dir, lambda: _cmd_invalidate_locked(batch_dir, args))


def _cmd_invalidate_locked(batch_dir: Path, args) -> int:
    manifest = mf.load(batch_dir)
    changed = mf.invalidate_downstream(batch_dir, manifest, args.stage, args.reason or "人工失效")
    mf.save(batch_dir, manifest)
    mf.append_log(batch_dir, {"event": "invalidate", "stage": args.stage,
                              "downstream": changed})
    downstream = [s for s in changed if s != args.stage]
    print(f"✓ 已失效：{args.stage}" + (f"；连带下游 {'、'.join(downstream)}" if downstream else ""))
    return 0


def _skip_agent_stage(batch_dir: Path, manifest: dict, stage: contracts.Stage,
                      *, dry_run: bool = False) -> bool:
    """Apply an input-based AGENT skip before dispatching its bridge."""
    reason = mf.skip_reason(manifest, stage)
    if not reason:
        return False
    if dry_run:
        print(f"  · {stage.id} 将跳过（{reason}）")
        return True
    from .local_agent import _agent_lock
    with _agent_lock(1500):
        current = mf.load(batch_dir)
        reason = mf.skip_reason(current, stage)
        if not reason:
            return False
        if current["stages"].get(stage.id, {}).get("status") not in mf.TERMINAL_OK:
            if stage.id == "answers":
                # An empty-answer decision requires confirmed question coverage.
                # Without structure, leave this producer waiting for its real prerequisite.
                if not (batch_dir / "01_source/structure.confirmed.json").is_file():
                    return False
                from . import empty_answers
                try:
                    empty_answers.write_empty_answers(batch_dir)
                except (OSError, ValueError, TypeError):
                    # Structure not usable yet: keep waiting instead of failing.
                    return False
                mf.touch_stage(current, stage.id, mf.COMPLETED,
                               notes="未提供答案来源；为全部确认题号登记无答案决定")
                mf.save(batch_dir, current)
                mf.record_delivery(batch_dir, current, stage)
                mf.save(batch_dir, current)
                if current is not manifest:
                    manifest.clear()
                    manifest.update(current)
                print(f"  · {stage.id} 已登记确定性无答案决定（{reason}）")
                return True
            if stage.id == "explanations":
                # 跳过解析提取时必须留下确定性空产物，装配仍可继续。
                from .optional_inputs import write_empty_explanations
                # 结构尚未产出时只记录跳过；正式结构完成后才生成逐题空解析产物。
                if (batch_dir / "01_source/structure.confirmed.json").is_file():
                    try:
                        write_empty_explanations(batch_dir)
                    except (OSError, ValueError, TypeError):
                        # Structure unusable: keep waiting, do not mark skipped.
                        return False
                    from . import batch_fill
                    if (batch_dir / batch_fill.STATE).is_file():
                        batch_fill.sync(batch_dir, "explanations")
            mf.touch_stage(current, stage.id, mf.SKIPPED, notes=reason)
            mf.save(batch_dir, current)
        if current is not manifest:
            manifest.clear()
            manifest.update(current)
    print(f"  · {stage.id} 跳过（{reason}）")
    return True


def cmd_run(args) -> int:
    batch_dir = Path(args.batch_dir).expanduser().resolve()
    try:
        with _single_run_lock(batch_dir):
            return _cmd_run_locked(batch_dir, args)
    except RuntimeError as exc:
        print(f"✗ {exc}")
        return 3


def _cmd_run_locked(batch_dir: Path, args) -> int:
    manifest = mf.load(batch_dir)
    if not args.dry_run:
        invalidated = mf.reconcile_deliveries(batch_dir, manifest)
        if invalidated:
            mf.save(batch_dir, manifest)
            manifest = mf.load(batch_dir)
    order = list(contracts.STAGE_ORDER)
    if args.stage:
        order = [args.stage]
    elif args.until:
        if args.until not in contracts.STAGE_BY_ID:
            print("✗ 未知阶段：" + args.until); return 2
        order = order[:order.index(args.until) + 1]

    for stage_id in order:
        stage = contracts.STAGE_BY_ID[stage_id]
        record = manifest["stages"].get(stage_id, {})
        status = record.get("status")
        if status == mf.SKIPPED:
            # 跳过理由可能已经消失（例如用户删掉了 02_audio/SKIP_LISTENING 想重新切听力）。
            # 这时不能一直沿用历史 skipped，否则该阶段永远跑不起来。
            still = mf.skip_reason(manifest, stage)
            if still:
                print(f"· {stage_id} 跳过（{still}）")
                continue
            if args.dry_run:
                print(f"· {stage_id} 跳过条件已消失，将恢复执行")
                continue
            mf.touch_stage(manifest, stage_id, mf.PENDING,
                           notes="跳过条件已消失，自动恢复执行")
            mf.save(batch_dir, manifest)
            manifest = mf.load(batch_dir)
            print(f"· {stage_id} 跳过条件已消失，恢复执行")
            status = mf.PENDING
        if status in TERMINAL and not args.force:
            # A known changed input/output is not reusable merely because the
            # old manifest still says completed. No model call is needed to see it.
            if stage.kind != contracts.HUMAN and (record.get("input_hashes") or record.get("output_hashes")):
                stale, why = mf.is_stale(batch_dir, manifest, stage)
                if stale and not args.dry_run:
                    mf.invalidate_stage(batch_dir, manifest, stage_id, why)
                    mf.save(batch_dir, manifest)
                    status = mf.INVALIDATED
                elif stale:
                    print(f"· {stage_id} 已过期，将重跑：{why}")
                else:
                    continue
            else:
                continue
        # An explicit/structural skip does not require upstream completion and
        # must be honoured before reporting an upstream wait.
        if stage.kind == contracts.AGENT and _skip_agent_stage(batch_dir, manifest, stage, dry_run=args.dry_run):
            continue
        unmet = [d for d in stage.depends_on
                 if not mf.dependency_satisfied(manifest, d, allow_unqualified=True)]
        if unmet:
            print(f"⏸ {stage_id} 等待上游：{'、'.join(unmet)}")
            return 0 if args.dry_run else 1

        # 解析与知识点两路打标共享同一已校验上游、互不依赖，并行调用压缩墙钟时间。
        # 导出/导入仍在批次锁内串行，导入前重新核对输入哈希与上游状态。
        if (stage.id in ("explanations", "knowledge") and not args.dry_run
                and not args.stage):
            pair_ids = ("explanations", "knowledge")
            ready = []
            for pid in pair_ids:
                pstage = contracts.STAGE_BY_ID[pid]
                prec = manifest["stages"].get(pid, {})
                if prec.get("status") in TERMINAL and not args.force:
                    continue
                if all(manifest["stages"].get(d, {}).get("status") in TERMINAL
                       for d in pstage.depends_on):
                    ready.append(pstage)
            if len(ready) == 2:
                from workflow import auto as auto_flow
                from workflow import local_agent
                try:
                    config = local_agent.load_config(getattr(args, "config", None))
                except (OSError, ValueError):
                    config = None
                if config:
                    _print_header("explanations / knowledge · 打标签并行")
                    outcomes = auto_flow._parallel_ready_agents(batch_dir, ready, config)
                    manifest = mf.load(batch_dir)
                    if any(code not in (0, 3) for code in outcomes.values()):
                        print("  · 并行打标签存在失败，继续按顺序处理其余阶段")
                    continue

        _print_header(f"{stage.id} · {stage.title}（{stage.kind}）")
        if stage.kind == contracts.AGENT:
            if _skip_agent_stage(batch_dir, manifest, stage, dry_run=args.dry_run):
                continue
            # 组装是确定性合并：结构不变时直接搬运四路产物，不调用模型。
            if stage.id == "assemble" and not args.dry_run:
                from workflow import assemble_local, agent_protocol, batch_fill
                if (batch_dir / batch_fill.STATE).is_file():
                    from workflow import auto as auto_flow
                    executable = auto_flow._bridge_stage(stage, ["{python}", "-m", "workflow.agent", "run", "--stage", "assemble", "--batch-dir", "{batch_dir}"])
                    code = _execute_command(batch_dir, manifest, executable)
                    if code:
                        return code
                    manifest = mf.load(batch_dir)
                    continue
                if assemble_local.available(batch_dir):
                    try:
                        payload = assemble_local.build(batch_dir)
                        with tempfile.TemporaryDirectory() as tmp:
                            result = Path(tmp) / "result.json"
                            exported = agent_protocol.export_task(batch_dir, "assemble", max_input_chars=900000)
                            result.write_text(json.dumps({"protocol": agent_protocol.PROTOCOL,
                                "task_id": exported["task_id"],
                                "result": {"status": "completed",
                                           "artifacts": {contracts.STAGE_BY_ID["assemble"].produces[0]: payload},
                                           "notes": [], "findings": []}}, ensure_ascii=False), encoding="utf-8")
                            report = agent_protocol.import_result(batch_dir, exported["task_id"], result)
                        if report.get("completed"):
                            print("  ✓ 确定性组装完成（未调用模型）")
                            manifest = mf.load(batch_dir)
                            continue
                        print("  · 确定性组装未通过，改用代理：" + str(report.get("error", ""))[:120])
                    except (ValueError, OSError, KeyError) as exc:
                        print(f"  · 确定性组装不可用（{exc}），改用代理")
            # 与网页后台推进共用同一代理桥，单步 CLI 也能执行本地 Agent。
            from workflow import auto as auto_flow
            command = auto_flow.load_bridge().get(stage.id)
            if command:
                executable = auto_flow._bridge_stage(stage, command)
                if args.dry_run:
                    print("  将调用本地代理：" + " ".join(_render(executable.command, batch_dir)))
                    continue
                code = _execute_command(batch_dir, manifest, executable)
                if code != 0 and stage.id == "assemble":
                    # 模型组装未通过源审计：先用确认结构做确定性重建（零模型调用），
                    # 通过整批校验才安装，避免为一次失败再付一遍模型调用。
                    from workflow import assemble_local, agent_protocol
                    try:
                        payload = assemble_local.build(batch_dir)
                        with tempfile.TemporaryDirectory() as tmp:
                            result = Path(tmp) / "result.json"
                            exported = agent_protocol.export_task(batch_dir, "assemble", max_input_chars=900000)
                            result.write_text(json.dumps({"protocol": agent_protocol.PROTOCOL,
                                "task_id": exported["task_id"],
                                "result": {"status": "completed",
                                           "artifacts": {contracts.STAGE_BY_ID["assemble"].produces[0]: payload},
                                           "notes": ["模型组装未通过源审计，改用确认结构的确定性重建"],
                                           "findings": []}}, ensure_ascii=False), encoding="utf-8")
                            report = agent_protocol.import_result(batch_dir, exported["task_id"], result)
                        if report.get("completed"):
                            assemble_local.record_guard(batch_dir)
                            print("  ✓ 模型组装失败后改用确定性重建并通过（未再调用模型）")
                            manifest = mf.load(batch_dir)
                            continue
                    except (ValueError, OSError, KeyError, TypeError) as exc:
                        print("  · 确定性重建也不可用：" + str(exc)[:120])
                if code != 0:
                    return code
                continue
        if stage.kind in (contracts.AGENT, contracts.HUMAN):
            print(f"  该阶段需要{'人工决定' if stage.kind == contracts.HUMAN else '代理执行'}：")
            print(f"  {stage.description}")
            if stage.docs:
                print("  依据：" + "、".join(stage.docs))
            print(f"  完成后：python -m workflow.runner mark --batch-dir {batch_dir} "
                  f"--stage {stage.id} --status "
                  f"{'approved' if stage.kind == contracts.HUMAN else 'completed'}")
            if args.dry_run:
                continue
            return 0 if stage.kind == contracts.AGENT else 0

        if stage.id == "upload":
            print("⏸ 上传须通过 upload 子命令并显式确认；普通运行不会上传")
            return 0

        if stage.kind == contracts.COMMAND:
            skip = mf.skip_reason(manifest, stage)
            if skip:
                print(f"  · {stage.id} 跳过（{skip}）")
                mf.touch_stage(manifest, stage.id, mf.SKIPPED, notes=skip)
                mf.save(batch_dir, manifest)
                continue
            if args.dry_run:
                print("  将执行：" + " ".join(_render(stage.command, batch_dir)))
                continue
            code = _execute_command(batch_dir, manifest, stage)
            if code != 0:
                return code
            continue

        if stage.kind == contracts.GATE:
            if args.dry_run:
                print("  将运行确定性检查")
                continue
            if stage.id == "structure_gate":
                code = do_structure_gate(batch_dir, manifest)
            elif stage.id == "validate":
                code = do_validate(batch_dir, manifest)
            else:
                code = do_review_gate(batch_dir, manifest)
            if code != 0:
                return code
            continue

    if not args.dry_run:
        # 重读台账：期间可能已有其它协作写入（例如协议导入）完成阶段。
        from .local_agent import _agent_lock
        with _agent_lock(1500):
            fresh = mf.load(batch_dir)
            fresh["status"] = mf.derive_status(fresh)
            mf.save(batch_dir, fresh)
            manifest.clear()
            manifest.update(fresh)
    print("\n完成本轮可自动执行的阶段。用 status 查看下一步。")
    return 0


def cmd_validate(args) -> int:
    batch_dir = Path(args.batch_dir).expanduser().resolve()
    manifest = mf.load(batch_dir)
    return do_validate(batch_dir, manifest)


def cmd_review_gate(args) -> int:
    batch_dir = Path(args.batch_dir).expanduser().resolve()
    manifest = mf.load(batch_dir)
    return do_review_gate(batch_dir, manifest)


def cmd_prepare_upload(args) -> int:
    from workflow import upload as upload_flow, upload_client
    try:
        report = upload_flow.prepare(Path(args.batch_dir).expanduser().resolve(),
            catalog=upload_flow._read(args.catalog) if args.catalog else None,
            config=upload_client.load_config(args.config), upload_mode=args.upload_mode,
            upload_target=args.upload_target, name=args.name)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["passed"] else 1
    except (OSError, ValueError, upload_client.APIError) as exc:
        print(f"✗ 上传准备未完成：{exc}")
        return 2


def cmd_upload(args) -> int:
    batch_dir = Path(args.batch_dir).expanduser().resolve()
    return _run_batch_locked(batch_dir, lambda: _cmd_upload_locked(batch_dir, args))


def _cmd_upload_locked(batch_dir: Path, args) -> int:
    manifest = mf.load(batch_dir)
    dry_run = getattr(args, "dry_run", False)
    gate = manifest["stages"].get("upload_gate", {}).get("status")
    # 预演只读，不需要人工批准；正式提交才要求 upload_gate 已批准。
    if dry_run:
        from workflow import upload as upload_flow
        try:
            frozen = upload_flow.verify(batch_dir)
        except (OSError, ValueError, upload_client.APIError) as exc:
            print(f"✗ 本地预演未通过：{exc}")
            return 2
        print("✓ 本地上传预演通过，冻结摘要：" + frozen["plan"]["payload_sha256"]
              + "；远端连通性、目录及媒体将在提交前复核")
        return 0
    if gate != mf.APPROVED:
        print("✗ 上传前必须由人工确认：mark --stage upload_gate --status approved --actor <人>")
        return 2
    if not args.yes:
        print("✗ 上传会写入远端上传暂存，需要显式加 --yes")
        return 2
    approved_hash = manifest["stages"]["upload_gate"].get("approved_batch_sha256")
    actual_hash = mf.sha256_file(batch_dir / "06_batch" / "batch_meta.json")
    if not approved_hash or not actual_hash or approved_hash != actual_hash:
        print("✗ 上传文件与人工批准时不一致，须重新审核并批准")
        return 2
    confirmation = manifest['stages']['upload_gate'].get('unreviewed_confirmation')
    attempt_token = getattr(args, 'unreviewed_attempt', '')
    unreviewed = bool(confirmation and attempt_token and
                      confirmation.get('attempt_sha256') == hashlib.sha256(attempt_token.encode()).hexdigest())
    if confirmation and not unreviewed:
        print('✗ 未审核上传确认不属于本次尝试，请重新确认'); return 2
    if unreviewed and unreviewed_summary(batch_dir, manifest)['revision'] != confirmation.get('revision'):
        print('✗ 审核状态已变化，请重新查看并确认'); return 2
    for stage_id in (("validate", "upload_gate") if unreviewed else ("validate", "review_gate", "upload_gate")):
        stale, reason = mf.is_stale(batch_dir, manifest, contracts.STAGE_BY_ID[stage_id])
        if stale:
            print(f"✗ {stage_id} 不可复用：{reason}，请重新检查和确认")
            return 2
    result = review.evaluate(batch_dir)
    flow = flowchart_checks(batch_dir, manifest)
    if not flow["passed"]:
        for error in flow["errors"]:
            print(f"✗ {error['code']}: {error['message']}")
        return 1
    if not result["passed"] and not unreviewed:
        print("✗ 审核闸门未通过，拒绝上传；先处理 07_review/manual-review-queue.json")
        return 1
    if unreviewed:
        from workflow.label_audit import inspect_file
        if (manifest['stages'].get('validate', {}).get('status') != mf.COMPLETED
                or not inspect_file(batch_dir / '06_batch' / 'batch_meta.json')['passed']):
            print('✗ 数据校验或标签审计未通过'); return 1
        # Consume before submitting: a failed or interrupted attempt needs a new click.
        manifest['stages']['upload_gate'].pop('unreviewed_confirmation', None)
        mf.save(batch_dir, manifest)
    from workflow import upload as upload_flow, upload_client
    try:
        receipt = upload_flow.submit_approved(batch_dir, manifest, lock_held=True)
    except (OSError, ValueError, upload_client.APIError) as exc:
        mf.touch_stage(manifest, "upload", mf.BLOCKED, notes=str(exc))
        manifest["status"] = mf.derive_status(manifest)
        mf.save(batch_dir, manifest)
        print(f"✗ 上传暂存未完成：{exc}")
        return 2
    _record_success(batch_dir, manifest, contracts.STAGE_BY_ID["upload"])
    manifest["status"] = mf.derive_status(manifest)
    manifest["side_effects"].append({"at": mf.now_iso(), "action": "upload_staging",
                                     "id": receipt["id"], "payload_sha256": receipt["payload_sha256"]})
    mf.save(batch_dir, manifest)
    mf.append_log(batch_dir, {"event": "upload_staging", "id": receipt["id"],
                              "payload_sha256": receipt["payload_sha256"]})
    print(f"✓ 已写入上传暂存，ID：{receipt['id']}；等待远端编辑人工确认，尚未生成草稿或发布")
    return 0


def cmd_ingest(args) -> int:
    batch_dir = Path(args.batch_dir).expanduser().resolve()
    try:
        report = ingest.ingest(batch_dir, result_path=args.result, stage=args.stage,
                               actor=args.actor or "workflow", note=args.note or "")
    except (FileNotFoundError, ValueError) as exc:
        print(f"✗ {exc}")
        return 2
    print(f"  候选文件 {report['candidates']} 个")
    if report["review"]:
        r = report["review"]
        print(f"  审核文件：新增内容 {r['content']} 条、标签 {r['metadata']} 条；"
              f"待办 {r['queue']} 项（阻断 {r['blocking']}）")
        if r.get("kept"):
            print(f"  已保留 {r['kept']} 条既往决定，未覆盖")
    for item in report["skipped"]:
        print(f"  · 跳过 {item['stage']}：缺少产物 {', '.join(item['missing'])}")
    if report["marked"]:
        print(f"  ✓ 已登记：{'、'.join(report['marked'])}")
    else:
        print("  · 没有可登记的阶段（正式产物或审核文件尚未生成）")
    return 0


def cmd_identify_meta(args) -> int:
    """上传后识别题源分类/卷名/地区/年份；识别不出留空，不阻断流程。"""
    from . import metadata_ai
    batch_dir = Path(args.batch_dir).expanduser().resolve()
    try:
        result = metadata_ai.auto_identify(batch_dir, force=True)
    except (OSError, ValueError, TypeError) as exc:
        print(f"✗ 自动识别失败：{exc}")
        return 2
    if not result.get("ran"):
        print(f"  · 未识别：{result.get('reason') or '无可用正文'}")
        return 0
    print("  识别结果：" + json.dumps(result.get("identified") or {}, ensure_ascii=False))
    applied = result.get("applied") or []
    print("  ✓ 已写入：" + ("、".join(applied) if applied else "无（已有值优先保留）"))
    if result.get("kept"):
        print("  · 保留原值：" + "、".join(result["kept"]))
    return 0


def cmd_resolve(args) -> int:
    batch_dir = Path(args.batch_dir).expanduser().resolve()
    result = review.resolve(batch_dir, finding_ids=args.finding,
                            all_minor=args.all_minor, action=args.action,
                            actor=args.actor or "unknown", note=args.note or "",
                            value=args.value, force=args.force)
    if result.get("error"):
        print(f"✗ {result['error']}")
        return 2
    print(f"  {result['action']} {result['resolved']} 条：{'、'.join(result['finding_ids'])}")
    print("  决定已写入 07_review/decisions.jsonl")
    return 0


def cmd_inventory(args) -> int:
    batch_dir = Path(args.batch_dir).expanduser().resolve()
    try:
        data = inventory.scan(batch_dir)
    except FileNotFoundError as exc:
        print(f"✗ {exc}")
        return 2
    print(f"整卷清点：{data['total']} 题，{len(data['sections'])} 个大题")
    print(f"  题号：{'、'.join(str(n) for n in data['numbers'][:20])}"
          + ("…" if data["total"] > 20 else ""))
    for item in data["sections"]:
        print(f"  L{item['line']:<4} {item['title']}")
    print(f"  已写入 01_source/inventory.json")
    return 0


def cmd_report(args) -> int:
    batch_dir = Path(args.batch_dir).expanduser().resolve()
    data = report.write(batch_dir)
    text = report.render_notification(data)
    print(text)
    out = batch_dir / "07_review"
    print(f"已写出：{out / 'notification.txt'}")
    print(f"         {out / 'report.md'}")
    print(f"         {out / 'report.json'}")
    if args.strict and (data["counts"][report.BLOCKED] or data["counts"][report.ATTENTION]):
        return 1
    return 0


def cmd_doctor(args) -> int:
    result = doctor.run(SKILL_ROOT)
    doctor.print_report(result)
    return 0 if result["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workflow.runner", description="录题自动化任务工作流")
    sub = parser.add_subparsers(dest="command", required=True)

    def batch_arg(p):
        p.add_argument("--batch-dir", required=True, help="批次目录")

    p_init = sub.add_parser("init", help="初始化批次并记录原始输入哈希")
    batch_arg(p_init)
    p_init.add_argument("--source", required=True, help="原卷 docx/pdf")
    p_init.add_argument("--answers", help="答案册/答案文件")
    p_init.add_argument("--audio", help="整卷听力音频")
    p_init.add_argument("--docx", help="画线来源 docx（默认用 --source）")
    p_init.add_argument("--year")
    p_init.add_argument("--region")
    p_init.add_argument("--subject", default="english")
    p_init.add_argument("--workflow-id")
    p_init.set_defaults(func=cmd_init)

    p_status = sub.add_parser("status", help="查看阶段状态与可执行项")
    batch_arg(p_status)
    p_status.add_argument("--json", action="store_true")
    p_status.set_defaults(func=cmd_status)

    p_diag = sub.add_parser("diagnose", help="统一卡点诊断：一句话原因 + 解决办法 + 修复命令")
    batch_arg(p_diag)
    p_diag.add_argument("--stage", help="只看某个阶段")
    p_diag.add_argument("--json", action="store_true", help="机器可读输出")
    p_diag.set_defaults(func=cmd_diagnose)

    p_run = sub.add_parser("run", help="按依赖顺序执行可自动完成的阶段")
    batch_arg(p_run)
    p_run.add_argument("--stage", help="只执行指定阶段")
    p_run.add_argument("--until", help="执行到指定阶段为止")
    p_run.add_argument("--force", action="store_true", help="已完成阶段也重新执行")
    p_run.add_argument("--config", type=Path)
    p_run.add_argument("--dry-run", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_mark = sub.add_parser("mark", help="记录人工/代理阶段结果")
    batch_arg(p_mark)
    p_mark.add_argument("--stage", required=True)
    p_mark.add_argument("--status", required=True,
                        choices=[mf.COMPLETED, mf.APPROVED, mf.NEEDS_REVIEW, mf.BLOCKED,
                                 mf.FAILED, mf.SKIPPED])
    p_mark.add_argument("--note", default="")
    p_mark.add_argument("--actor", default="")
    p_mark.add_argument("--allow-missing", action="store_true")
    p_mark.add_argument("--invalidate-downstream", action="store_true")
    p_mark.add_argument('--unreviewed-revision', default='')
    p_mark.add_argument('--unreviewed-attempt', default='')
    p_mark.set_defaults(func=cmd_mark)

    p_inv = sub.add_parser("invalidate", help="让阶段及其下游失效")
    batch_arg(p_inv)
    p_inv.add_argument("--stage", required=True)
    p_inv.add_argument("--reason", default="")
    p_inv.set_defaults(func=cmd_invalidate)

    p_val = sub.add_parser("validate", help="运行本地确定性体检")
    batch_arg(p_val)
    p_val.set_defaults(func=cmd_validate)

    p_gate = sub.add_parser("review-gate", help="判断审核是否放行")
    batch_arg(p_gate)
    p_gate.set_defaults(func=cmd_review_gate)

    p_ing = sub.add_parser("ingest", help="登记多代理工作流的结果与审核发现")
    batch_arg(p_ing)
    p_ing.add_argument("--result", help="工作流返回值的 JSON 文件")
    p_ing.add_argument("--stage", default="auto",
                       choices=["auto", "types", "explanations", "knowledge", "review"])
    p_ing.add_argument("--actor", default="workflow")
    p_ing.add_argument("--note", default="")
    p_ing.set_defaults(func=cmd_ingest)

    p_res = sub.add_parser("review-resolve", help="处理审核发现并记录人工决定")
    batch_arg(p_res)
    p_res.add_argument("--finding", action="append", default=[], help="指定 finding_id，可重复")
    p_res.add_argument("--all-minor", action="store_true", help="只处理 minor 且非阻断项")
    p_res.add_argument("--action", default="accept",
                       choices=["accept", "edit", "reject", "hold"])
    p_res.add_argument("--value", help="edit 时的新值")
    p_res.add_argument("--actor", required=True, help="处理人")
    p_res.add_argument("--note", default="")
    p_res.add_argument("--force", action="store_true", help="允许处理 blocker/major")
    p_res.set_defaults(func=cmd_resolve)

    p_meta = sub.add_parser("identify-meta", help="用模型识别题源分类/卷名/地区/年份（识别不出留空）")
    batch_arg(p_meta)
    p_meta.add_argument("--force", action="store_true", help="即使当前正文已识别过也重新识别")
    p_meta.set_defaults(func=cmd_identify_meta)

    p_inv = sub.add_parser("inventory", help="机械清点整卷题号与板块")
    batch_arg(p_inv)
    p_inv.set_defaults(func=cmd_inventory)

    p_rep = sub.add_parser("report", help="生成整套卷的审核通报")
    batch_arg(p_rep)
    p_rep.add_argument("--strict", action="store_true",
                       help="有阻断或有疑问时返回非零退出码")
    p_rep.set_defaults(func=cmd_report)

    p_doc = sub.add_parser("doctor", help="自检：环境、规则、契约、闸门、批次")
    p_doc.set_defaults(func=cmd_doctor)

    p_prep = sub.add_parser("prepare-upload", help="读取远端目录、校验并冻结上传暂存请求（不提交题目）")
    batch_arg(p_prep)
    p_prep.add_argument("--catalog", type=Path, help="离线远端目录快照；提交前仍重新读取线上目录")
    p_prep.add_argument("--config", type=Path)
    p_prep.add_argument("--upload-mode", choices=["source", "knowledge", "manual"])
    p_prep.add_argument("--upload-target")
    p_prep.add_argument("--name")
    p_prep.set_defaults(func=cmd_prepare_upload)

    p_up = sub.add_parser("upload", help="写入上传暂存（需人工闸门已批准）")
    batch_arg(p_up)
    p_up.add_argument("--yes", action="store_true", help="确认会写入远端上传暂存")
    p_up.add_argument("--dry-run", action="store_true")
    p_up.add_argument('--unreviewed-attempt', default='')
    p_up.set_defaults(func=cmd_upload)
    return parser


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
