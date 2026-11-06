# -*- coding: utf-8 -*-
"""本地确定性体检：只读、可并行、不接触网络或服务器。

这里的每个检查都调用项目已有脚本，工作流不重新实现业务规则，
只负责统一调度、收集退出码、区分阻断项与提示项。
"""
from __future__ import annotations

import concurrent.futures
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Check:
    name: str
    script: str
    blocking: bool = True
    needs: tuple = ()          # 需要 manifest 里的输入键：source / answers
    note: str = ""


CHECKS: tuple[Check, ...] = (
    Check("目录与匹配规则 label_audit", "workflow/label_audit.py"),
    Check("排版 check_text", "scripts/06-upload/check_text.py", blocking=False,
          note="只报告，不阻断"),
    Check("空档 audit_underlines", "scripts/06-upload/audit_underlines.py"),
    Check("画线 check_underline", "scripts/06-upload/check_underline.py"),
    Check("表格 check_tables", "scripts/06-upload/check_tables.py"),
    Check("题型版面 audit_type_layout", "scripts/06-upload/audit_type_layout.py"),
    Check("批次完整性 audit_batch_integrity", "scripts/06-upload/audit_batch_integrity.py"),
    Check("完成句子 audit_sentence_blanks", "scripts/06-upload/audit_sentence_blanks.py"),
    Check("结构 check_structure", "scripts/06-upload/check_structure.py"),
    Check("标签 check_tags", "scripts/04-knowledge/check_tags.py"),
    Check("题型标签 audit_type_tags", "scripts/06-upload/audit_type_tags.py"),
    Check("阅读 audit_reading", "scripts/06-upload/audit_reading.py"),
    Check("发布闸门 publish_check", "scripts/06-upload/publish_check.py"),
    Check("答案 check_answers", "scripts/06-upload/check_answers.py", needs=("answers",)),
    Check("难度一致性 check_difficulty", "scripts/06-upload/check_difficulty.py"),
    Check("解析 check_explanations", "scripts/06-upload/check_explanations.py", needs=("source",)),
    Check("提示标记 audit_source_marks", "scripts/06-upload/audit_source_marks.py", needs=("source",)),
    Check("正文保真 check_fidelity", "scripts/06-upload/check_fidelity.py", blocking=False,
          needs=("source",), note="图片材料天然对不上，交人工确认"),
)


def _tail(text: str, limit: int = 400) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return " | ".join(lines[-3:])[:limit]


def _argv(check: Check, skill_root: Path, batch_json: Path, inputs: dict) -> list[str]:
    if check.script == "workflow/label_audit.py":
        argv = [sys.executable, "-m", "workflow.label_audit", "--batch", str(batch_json)]
    else:
        argv = [sys.executable, str(skill_root / check.script), "--batch", str(batch_json)]
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
    return argv


def run_check(check: Check, skill_root: Path, batch_json: Path, inputs: dict,
              timeout: int = 180) -> dict:
    argv = _argv(check, skill_root, batch_json, inputs)
    try:
        proc = subprocess.run(argv, cwd=str(skill_root), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
        code, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return {"check": check.name, "script": check.script, "blocking": check.blocking,
                "passed": False, "exit_code": None, "output": "",
                "error": f"超时（{timeout}s）", "note": check.note}
    except OSError as exc:
        return {"check": check.name, "script": check.script, "blocking": check.blocking,
                "passed": False, "exit_code": None, "output": "",
                "error": f"无法执行：{exc}", "note": check.note}
    return {
        "check": check.name,
        "script": check.script,
        "blocking": check.blocking,
        "passed": code == 0,
        "exit_code": code,
        "output": _tail(out),
        "warnings": [line.strip() for line in out.splitlines() if line.startswith("提示：")],
        "error": _tail(err),
        "note": check.note,
    }


def run_all(skill_root: Path, batch_json: Path, inputs: dict,
            only: tuple = ()) -> dict:
    selected = [c for c in CHECKS if not only or c.name in only or c.script in only]
    if not batch_json.is_file():
        return {"passed": False, "error": f"批次文件不存在：{batch_json}",
                "checks": [], "blocking_failures": [], "warnings": [], "skipped": []}
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

    runnable, skipped = [], []
    for check in selected:
        missing = [key for key in check.needs if not effective_inputs.get(key, {}).get("exists")]
        if missing:
            skipped.append({"check": check.name, "reason": "缺少输入：" + "、".join(missing)})
            continue
        runnable.append(check)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(runnable))) as pool:
        results = list(pool.map(lambda c: run_check(c, skill_root, batch_json, effective_inputs), runnable))
    if pdf_answers and effective_inputs.get("answers", {}).get("path") != answer_input.get("path"):
        for result in results:
            if result["script"] == "scripts/06-upload/check_answers.py":
                result["input_basis"] = "原答案PDF经正式答案阶段提取的JSON；本项比较装配结果与提取答案，不声称直接解析PDF。"
                result["answer_artifact"] = effective_inputs["answers"]["path"]
    blocking_failures = [r for r in results if r["blocking"] and not r["passed"]]
    warnings = [r for r in results if (not r["blocking"] and not r["passed"]) or r.get("warnings")]
    return {
        "passed": not blocking_failures,
        "batch": str(batch_json),
        "checks": results,
        "blocking_failures": [r["check"] for r in blocking_failures],
        "warnings": [r["check"] for r in warnings],
        "skipped": skipped,
    }


def write_report(batch_dir: Path, report: dict) -> Path:
    target = Path(batch_dir) / "07_review" / "validation-report.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target
