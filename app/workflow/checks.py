# -*- coding: utf-8 -*-
"""本地确定性体检：只读、可并行、不接触网络或服务器。

这里的每个检查都调用项目已有脚本，工作流不重新实现业务规则，
只负责统一调度、收集退出码、区分阻断项与提示项。
"""
from __future__ import annotations

import concurrent.futures
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Check:
    name: str
    script: str
    blocking: bool = True
    needs: tuple = ()          # 需要 manifest 里的输入键：source / answers
    note: str = ""
    phase: str = "content"
    args: tuple = ()           # 脚本的额外只读检查参数；追加字段保持旧位置参数兼容


CHECKS: tuple[Check, ...] = (
    Check("目录与匹配规则 label_audit", "workflow/label_audit.py"),
    Check("排版 check_text", "scripts/06-upload/check_text.py", blocking=False,
          note="最终格式阶段报告，不阻断", phase="format"),
    # 画线/空档/表格属格式表达问题（下划线、全角空档、表格列数），脚本内以
    # [W… 提示输出；这里再兜底为不阻断，避免格式小问题卡住整卷流程。
    # 题目/题干/题号对不上等实质错误仍由结构、答案、来源审计等阻断项拦截。
    Check("空档 audit_underlines", "scripts/06-upload/audit_underlines.py", blocking=False,
          note="最终格式阶段报告，不阻断", phase="format"),
    Check("画线 check_underline", "scripts/06-upload/check_underline.py", blocking=False,
          note="最终格式阶段报告，不阻断", phase="format"),
    Check("表格 check_tables", "scripts/06-upload/check_tables.py", blocking=False,
          note="最终格式阶段报告，不阻断", phase="format"),
    # 前期仍检查题组归属、选项位置及字母连续；仅 E20 数量不足延后。
    Check("题型版面 audit_type_layout", "scripts/06-upload/audit_type_layout.py",
          args=("--content-only",)),
    Check("题型内容 audit_type_layout_final", "scripts/06-upload/audit_type_layout.py",
          note="最终重新检查选项数量及身份归属", phase="format", args=("--content-only",)),
    Check("题型重复显示 audit_type_layout_full", "scripts/06-upload/audit_type_layout.py",
          blocking=False, note="最终格式阶段完整题型版面观察，不阻断", phase="format"),
    Check("批次完整性 audit_batch_integrity", "scripts/06-upload/audit_batch_integrity.py"),
    Check("完成句子 audit_sentence_blanks", "scripts/06-upload/audit_sentence_blanks.py",
          blocking=False, note="最终格式阶段报告，不阻断", phase="format"),
    Check("结构 check_structure", "scripts/06-upload/check_structure.py"),
    Check("标签 check_tags", "scripts/04-knowledge/check_tags.py"),
    Check("题型标签 audit_type_tags", "scripts/06-upload/audit_type_tags.py"),
    Check("阅读 audit_reading", "scripts/06-upload/audit_reading.py"),
    Check("发布闸门 publish_check", "scripts/06-upload/publish_check.py"),
    Check("答案 check_answers", "scripts/06-upload/check_answers.py", needs=("answers",)),
    Check("难度一致性 check_difficulty", "scripts/06-upload/check_difficulty.py",
          phase="format", note="最终上传前核验难度档位与分值，保持阻断"),
    Check("解析 check_explanations", "scripts/06-upload/check_explanations.py", needs=("source",)),
    Check("提示标记 audit_source_marks", "scripts/06-upload/audit_source_marks.py", needs=("source",)),
    Check("正文保真 check_fidelity", "scripts/06-upload/check_fidelity.py", blocking=False,
          needs=("source",), note="图片材料天然对不上，交人工确认"),
)


CONTENT_CHECKS = tuple(c for c in CHECKS if c.phase == "content")
FINAL_FORMAT_CHECKS = tuple(c for c in CHECKS if c.phase == "format")


def _tail(text: str, limit: int = 400) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return " | ".join(lines[-3:])[:limit]


def _argv(check: Check, skill_root: Path, batch_json: Path, inputs: dict) -> list[str]:
    if check.script == "workflow/label_audit.py":
        argv = [sys.executable, "-X", "utf8", "-m", "workflow.label_audit", "--batch", str(batch_json)]
    else:
        argv = [sys.executable, "-X", "utf8", str(skill_root / check.script), "--batch", str(batch_json)]
    if "answers" in check.needs:
        answers = inputs.get("answers", {}).get("path")
        if answers:
            argv += ["--answers", str(answers)]
    if "source" in check.needs or check.script == "scripts/06-upload/check_underline.py":
        source = inputs.get("source", {}).get("path")
        if source:
            argv += ["--source", str(source)]
    # Native extraction separates appended explanations from the paper body.
    # Supply both canonical bodies and appendices, so original explanations
    # are not rejected or incorrectly relabeled as AI-generated.
    if check.script == "scripts/06-upload/check_explanations.py":
        batch_dir = batch_json.parent.parent
        for relative in ("01_source/正文.txt", "01_source/附录.txt", "01_source_ans/正文.txt", "01_source_ans/附录.txt"):
            text = batch_dir / relative
            if text.is_file() and str(text) not in argv:
                argv += ["--source", str(text)]
        materials = batch_dir / '01_source/materials'
        if materials.is_dir():
            for text in sorted(materials.glob('attachment_*.txt')):
                if text.is_file() and text.stem.removeprefix('attachment_').isdigit():
                    argv += ['--source', str(text)]
    argv.extend(check.args)
    return argv


def _finding(check: Check, message: str, *, code: str = "CHECK_FAILED",
             blocking: bool | None = None, severity: str | None = None,
             **evidence) -> dict:
    hard = check.blocking if blocking is None else blocking
    return {**evidence, "code": code, "check": check.name, "script": check.script,
            "message": message, "blocking": hard,
            "severity": severity or ("error" if hard else "warning"), "phase": check.phase}


def _output_findings(check: Check, stdout: str, stderr: str, exit_code: int) -> list[dict]:
    """Keep complete diagnostics; printed numbers never establish unit identity."""
    findings = []
    has_failure = False
    for stream, text in (("stdout", stdout), ("stderr", stderr)):
        if not text.strip():
            continue
        # Some scripts emit a complete JSON report (possibly pretty printed).
        # Preserve its explicit scoped identifiers and evidence without guessing
        # identities from legacy text output or numeric question labels.
        try:
            report = json.loads(text)
        except (ValueError, TypeError):
            report = None
        structured = False
        if isinstance(report, dict):
            for key in ("findings", "errors", "issues", "warnings"):
                rows = report.get(key)
                if not isinstance(rows, list):
                    continue
                structured = True
                for value in rows:
                    row = dict(value) if isinstance(value, dict) else {"message": str(value)}
                    warning = (key == "warnings" or row.get("blocking") is False
                               or row.get("severity") in {"warning", "info", "informational"})
                    message = str(row.pop("message", None) or row.pop("reason", None)
                                  or row.get("error") or "检查未通过")
                    code = str(row.pop("code", None) or ("CHECK_WARNING" if warning else "CHECK_FAILED"))
                    severity = row.pop("severity", None)
                    hard = check.blocking and not warning
                    has_failure = has_failure or not warning
                    row.pop("blocking", None)
                    for reserved in ("check", "script", "phase", "stream"):
                        row.pop(reserved, None)
                    findings.append(_finding(check, message, code=code, blocking=hard,
                                             severity=severity, stream=stream, **row))
            if structured:
                continue
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("[OK]"):
                continue
            marker = re.match(r"^\[([^\]]+)\]", line)
            code = marker.group(1) if marker else ""
            warning = line.startswith(("提示：", "[W", "[INFO]", "[提示]", "[PREF]"))
            # A successful script may print unmarked counters/context. These are
            # retained in stdout/stderr but are not defects by themselves.
            if exit_code == 0 and not warning and not marker:
                continue
            if code == "FAIL" and re.fullmatch(r"\[FAIL\]\s*\d+\s*条问题", line):
                continue  # aggregate count; each actual diagnostic remains above
            has_failure = has_failure or not warning
            findings.append(_finding(check, line,
                                     code=code or ("CHECK_WARNING" if warning else "CHECK_FAILED"),
                                     blocking=check.blocking and not warning,
                                     stream=stream))
    if exit_code and not has_failure:
        # An exit failure cannot disappear merely because output is empty or
        # contains only soft hints. Observation checks still remain nonblocking.
        findings.append(_finding(check, f"检查退出码为 {exit_code}，未提供具体失败原因",
                                 code="CHECK_EXIT_NONZERO", exit_code=exit_code))
    return findings


def run_check(check: Check, skill_root: Path, batch_json: Path, inputs: dict,
              timeout: int = 180) -> dict:
    argv = _argv(check, skill_root, batch_json, inputs)
    try:
        proc = subprocess.run(argv, cwd=str(skill_root), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
        code, out, err = proc.returncode, proc.stdout, proc.stderr
    except (subprocess.TimeoutExpired, OSError) as exc:
        timed_out = isinstance(exc, subprocess.TimeoutExpired)
        message = f"超时（{timeout}s）" if timed_out else f"无法执行：{exc}"
        out = getattr(exc, "stdout", None) or ""
        err = getattr(exc, "stderr", None) or ""
        out = out.decode("utf-8", errors="replace") if isinstance(out, bytes) else out
        err = err.decode("utf-8", errors="replace") if isinstance(err, bytes) else err
        findings = [row for row in _output_findings(check, out, err, 1)
                    if row["code"] != "CHECK_EXIT_NONZERO"]
        findings.append(_finding(check, message,
                                 code="CHECK_TIMEOUT" if timed_out else "CHECK_EXECUTION_ERROR"))
        return {"check": check.name, "script": check.script, "blocking": check.blocking,
                "phase": check.phase, "passed": False, "exit_code": None,
                "output": _tail(out), "error": message, "stdout": out, "stderr": err,
                "warnings": [row["message"] for row in findings if not row["blocking"]],
                "findings": findings, "note": check.note}
    findings = _output_findings(check, out, err, code)
    # Only the well-formed option count diagnostics are deferrable. E20 also
    # includes letter continuity, which must remain an early identity check.
    # Nonzero exits with unrecognized diagnostics remain hard failures.
    if (check.script == 'scripts/06-upload/audit_type_layout.py'
            and check.phase == 'content' and check.args == ('--content-only',)):
        for row in findings:
            if (row.get('code') == 'E20' and re.search(
                    r'(?:单元)?选项应为\s*\d+\s*条，现有\s*\d+\s*条', row.get('message', ''))):
                row.update(blocking=False, severity='warning', disposition='defer_final',
                           deferred_to='format_gate')
        if code == 1 and findings and all(not row['blocking'] for row in findings):
            code_passed = True
        else:
            code_passed = code == 0
            if code not in (0, 1) and not any(row['blocking'] for row in findings):
                findings.append(_finding(check, f"检查异常退出码为 {code}",
                                         code='CHECK_EXIT_NONZERO', exit_code=code))
    else:
        code_passed = code == 0
    return {
        "check": check.name,
        "script": check.script,
        "blocking": check.blocking,
        "phase": check.phase,
        "passed": code_passed,
        "exit_code": code,
        "output": _tail(out),
        "stdout": out,
        "stderr": err,
        "warnings": [row["message"] for row in findings if not row["blocking"]],
        "error": _tail(err),
        "findings": findings,
        "note": check.note,
    }


def run_all(skill_root: Path, batch_json: Path, inputs: dict,
            only: tuple = (), profile: str = "content") -> dict:
    if profile not in {"content", "format", "all"}:
        raise ValueError(f"未知检查阶段：{profile}")
    # 显式单项诊断兼容旧入口；正常 validate 不启动格式子进程。
    if only:
        selected = [c for c in CHECKS if c.name in only or c.script in only]
    else:
        selected = [c for c in CHECKS if profile == "all" or c.phase == profile]
    if not batch_json.is_file():
        message = f"批次文件不存在：{batch_json}"
        finding = _finding(Check("批次文件", "workflow/checks.py", phase=profile),
                           message, code="BATCH_INPUT_MISSING")
        return {"passed": False, "error": message,
                "checks": [], "blocking_failures": [], "warnings": [], "skipped": [],
                "findings": [finding]}
    # 没有单独上传答案卷时仍检查已提取的原卷附录答案；不能静默跳过答案比对。
    effective_inputs = dict(inputs)
    answer_input = inputs.get("answers", {})
    answer_suffix = Path(str(answer_input.get("path") or "")).suffix.lower()
    pdf_answers = answer_suffix == ".pdf"
    # 独立答案 DOCX/PDF 可能含表格、图片或浮动对象；答案阶段已经经过文字层/OCR
    # 与题号校验并产出 answers.json，后续确定性核验应使用该正式产物，不能再次把原 DOCX
    # 当作纯文本解析并误报“含表格/图片”。
    use_prepared_answers = (not answer_input.get("exists") or answer_suffix in {".pdf", ".docx"})
    if use_prepared_answers:
        prepared_answers = batch_json.parent.parent / "01_source_ans" / "answers.json"
        if prepared_answers.is_file():
            effective_inputs["answers"] = {"path": str(prepared_answers), "exists": True}
            if answer_suffix == ".docx":
                pdf_answers = True  # 复用同一 input_basis 说明：使用正式提取 JSON

    runnable, skipped, missing_findings = [], [], []
    for check in selected:
        missing = [key for key in check.needs if not effective_inputs.get(key, {}).get("exists")]
        if missing:
            message = "缺少输入：" + "、".join(missing)
            skipped.append({"check": check.name, "reason": message})
            for key in missing:
                missing_findings.append(_finding(check, f"缺少输入：{key}，无法完成本项检查",
                                                 code=f"{key.upper()}_INPUT_MISSING", input=key))
            continue
        runnable.append(check)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(runnable))) as pool:
        results = list(pool.map(lambda c: run_check(c, skill_root, batch_json, effective_inputs), runnable))
    if pdf_answers and effective_inputs.get("answers", {}).get("path") != answer_input.get("path"):
        for result in results:
            if result["script"] == "scripts/06-upload/check_answers.py":
                result["input_basis"] = "原答案PDF经正式答案阶段提取的JSON；本项比较装配结果与提取答案，不声称直接解析PDF。"
                result["answer_artifact"] = effective_inputs["answers"]["path"]
    # 需要原文的检查（画线/解析/提示标记/正文保真）拿不到 source 时会被跳过；
    # 跳过不能按“没报错=通过”处理，否则单项体检会静默放行。
    source_required = [c for c in selected if "source" in c.needs]
    source_missing = (not inputs.get("source", {}).get("exists")
                      and bool(source_required))
    blocking_failures = [r for r in results if r["blocking"] and not r["passed"]]
    # Missing required inputs are evidence too, including hard checks deferred
    # to final format. Keep the source compatibility summary below.
    for finding in missing_findings:
        if finding["blocking"] and finding["input"] != "source":
            blocking_failures.append(finding)
    if source_missing:
        if not any(row["input"] == "source" and row["blocking"] for row in missing_findings):
            missing_findings.append(_finding(
                Check("原卷来源缺失（SOURCE_INPUT_MISSING）", "workflow/checks.py", phase=profile),
                "需要原文的检查无 source，无法完成逐题核对", code="SOURCE_INPUT_MISSING", input="source"))
        blocking_failures.append({"check": "原卷来源缺失（SOURCE_INPUT_MISSING）",
                                  "blocking": True, "passed": False,
                                  "output": "需要原文的检查无 source，无法完成逐题核对",
                                  "note": "直接跑单项体检时也要提供 --source"})
    warnings = [r for r in results if (not r["blocking"] and not r["passed"]) or r.get("warnings")]
    return {
        "passed": not blocking_failures,
        "batch": str(batch_json),
        "checks": results,
        "blocking_failures": [r["check"] for r in blocking_failures],
        "warnings": [r["check"] for r in warnings] + (["SOURCE_INPUT_MISSING"] if source_missing else []),
        "skipped": skipped,
        "findings": [finding for result in results for finding in result.get("findings", [])]
                    + missing_findings,
    }


def write_report(batch_dir: Path, report: dict) -> Path:
    target = Path(batch_dir) / "07_review" / "validation-report.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target
