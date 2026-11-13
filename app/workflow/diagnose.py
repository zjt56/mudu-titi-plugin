# -*- coding: utf-8 -*-
"""统一的「卡点诊断」：把各阶段分散的失败证据汇成一句人话原因 + 解决办法。

背景：每个阶段失败时，manifest 的 notes 往往只写一句笼统的话（“闸门未通过”
“代理桥执行失败”“装配待重试”），真正的原因藏在阶段日志、agent-runs 下的
import-report、validation-report 或 structure-gate 报告里，用户要自己去翻。
本模块只读、不改任何状态：按契约顺序遍历阶段，对每个未完成的阶段汇总
「为什么卡 → 怎么解决 → 可直接复制的命令」，供 CLI 与工作台统一展示。

它不替代任何校验，也不放宽放行条件：只是一个把现有证据翻译成人话的视图。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import contracts
from . import manifest as mf


def _read(path: Path):
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        pass
    return None


def _tail_file(path: Path, limit: int | None = 1200) -> str:
    try:
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            return text[-limit:] if limit is not None else text
    except OSError:
        pass
    return ""


def _last_error_line(text: str) -> str:
    """从日志里挑最后一条真正的错误行（跳过 '调用模型/已校验' 等进度行）。"""
    noise = ("调用模型", "已校验并落盘", "复用已校验快照", "执行：", "$ ", "[stderr]")
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    clean = [line for line in lines if not any(marker in line for marker in noise)]
    # 带尝试身份的 JSON 外框由 _agent_error 校验身份并读取错误/报告；不能再次作为原始日志展示，
    # 否则会绕过阶段/尝试匹配，或把已被后续轮次修复的错误重新带回。
    clean = [line for line in clean if not ('"attempt_dir"' in line and line.startswith("{"))]
    for line in reversed(clean):
        if line.startswith("{") and '"error"' in line:
            try:
                obj = json.loads(line)
                return str(obj.get("error") or obj.get("message") or line)
            except ValueError:
                return line
        # 校验器的 raise ValueError 一行通常就是最有用的一行
        if ("错误" in line or "必须" in line or "要求" in line or "不一致" in line
                or "Error" in line or "failed" in line.lower() or "Traceback" in line):
            return line
    return clean[-1] if clean else ""


def _review_failure_detail(batch: Path, record: dict | None = None) -> str:
    """Read failures for current unfinished tasks; historical evidence stays history."""
    from datetime import datetime
    from . import review, review_rounds

    folder = batch / "workflow" / "review-failures"
    if not folder.is_dir():
        return ""
    record = record or {}
    try:
        evaluated = review.evaluate_rounds(batch)
    except (OSError, ValueError, KeyError, TypeError):
        evaluated = {}
    checked = set(evaluated.get("checked_rounds") or [])
    plan = evaluated.get("plan")
    current_task = record.get("task_id")
    current_task = current_task if isinstance(current_task, str) and current_task.startswith("round-") else None
    started = None
    if record.get("started_at"):
        try:
            started = datetime.fromisoformat(record["started_at"]).timestamp()
        except (ValueError, TypeError):
            return ""
    files = sorted((p for p in folder.glob("*.json") if p.is_file()),
                   key=lambda p: p.stat().st_mtime_ns, reverse=True)
    for path in files:
        report = _read(path)
        if (not isinstance(report, dict) or report.get("round_id") not in review.ROUND_IDS
                or report.get("round_id") in checked):
            continue
        error = report.get("error") or report.get("reason")
        if not error:
            continue
        task_id = report.get("task_id")
        if task_id is None and re.fullmatch(r"round-[0-9a-f]{32}", path.stem):
            task_id = path.stem
        if task_id is None:
            # Legacy records lack provenance. Never use them to override an
            # identified task or revive a failure from before this stage attempt.
            if (current_task or any(k in report for k in ("input_binding", "state_path", "part_index", "targets"))
                    or started is not None and path.stat().st_mtime < started):
                continue
            return str(error)[:1200]
        if (not isinstance(task_id, str) or not re.fullmatch(r"round-[0-9a-f]{32}", task_id)
                or current_task and task_id != current_task or not isinstance(plan, dict)):
            continue
        try:
            # This checks input hashes, protocol, effective plan and retired
            # generations without recovering installs or exporting new work.
            binding = review_rounds._check_bound_inputs(batch, task_id, plan)
            task = binding["task"]
            state = _read(review_rounds._path(batch, binding["state_path"]))
            index = binding["part_index"]
            if (not isinstance(state, dict) or state.get("plan") != plan
                    or not isinstance(index, int) or isinstance(index, bool) or index < 0
                    or index >= len(state.get("parts") or [])):
                continue
            part = state["parts"][index]
            if (task.get("task_id") != task_id or task.get("round_id") != report.get("round_id")
                    or part.get("task_id") != task_id
                    or any(row.get("task_id") == task_id for row in state.get("completed", []) if isinstance(row, dict))):
                continue
            identity = {"input_binding": plan["input_binding"], "state_path": binding["state_path"],
                        "part_index": index, "targets": part["targets"]}
            if any(key in report and report[key] != value for key, value in identity.items()):
                continue
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
            continue
        return str(error)[:1200]
    return ""


def _failed_units(report) -> dict:
    if not isinstance(report, dict):
        return {}
    failed = report.get("failed_units")
    if isinstance(failed, dict):
        return failed
    for note in report.get("notes", []) or []:
        prefix = "Label repair exhausted for units: "
        if isinstance(note, str) and note.startswith(prefix):
            try:
                failed = json.loads(note[len(prefix):])
            except ValueError:
                continue
            if isinstance(failed, dict):
                return failed
    return {}


def _unit_errors(failed: dict) -> str:
    return "；".join(f"{unit}：{error if isinstance(error, str) else json.dumps(error, ensure_ascii=False)}"
                    for unit, error in failed.items())


def _agent_error(batch: Path, stage_id: str, record: dict | None = None) -> str:
    """只解释当前阶段最近的绑定尝试，不从旧尝试中捞取失败。"""
    from datetime import datetime

    runs = batch / "workflow" / "agent-runs"
    if not runs.is_dir():
        return ""
    record = record or {}
    # CLI 日志中的 attempt_dir 是导入报告所属尝试的明确身份；mtime 不能覆盖它。
    bound = record.get("attempt_dir")
    log_path = batch / "workflow" / "logs" / f"{stage_id}.log"
    log = _tail_file(log_path, limit=None)
    envelope = {}
    log_bound = None
    for line in reversed(log.splitlines()):
        try:
            candidate = json.loads(line)
        except ValueError:
            continue
        if not isinstance(candidate, dict):
            continue
        artifacts = candidate.get("artifacts")
        candidate_bound = candidate.get("attempt_dir") or (
            artifacts.get("attempt_dir") if isinstance(artifacts, dict) else None)
        if candidate_bound:
            envelope, log_bound = candidate, candidate_bound
            break
    if not bound:
        bound = log_bound
    if bound:
        if not isinstance(bound, str):
            return ""
        run = Path(bound)
        run = (run if run.is_absolute() else batch / run).resolve()
        if run.parent != runs.resolve() or not run.name.startswith(stage_id + "-") or not run.is_dir():
            return ""
    else:
        cands = [p for p in runs.glob(f"{stage_id}-*") if p.is_dir() and p.name != stage_id + "-parts"]
        if not cands:
            return ""
        run = max(cands, key=lambda p: p.stat().st_mtime_ns).resolve()
        if run.parent != runs.resolve():
            return ""
    # 日志外框只有绑定到选中的尝试后才可读取；不能绕过显式 attempt_dir。
    if isinstance(log_bound, str):
        log_run = Path(log_bound)
        log_run = (log_run if log_run.is_absolute() else batch / log_run).resolve()
    else:
        log_run = None
    if log_run != run:
        envelope = {}
    report_path = run / "import-report.json"
    task_path = run / "task-report.json"
    progress = sorted((p for p in run.glob("repair-progress-*.json")
                       if re.fullmatch(r"repair-progress-\d+\.json", p.name)),
                      key=lambda p: int(p.stem.rsplit("-", 1)[1]))
    latest = progress[-1] if progress else None
    # 分别检查证据的新鲜度；新日志不能使旧报告重新有效。
    started = None
    if record.get("started_at"):
        try:
            started = datetime.fromisoformat(record["started_at"]).timestamp()
        except (ValueError, TypeError):
            return ""

    def current(path):
        return path is not None and path.is_file() and (
            started is None or path.stat().st_mtime >= started)

    report = _read(report_path) if current(report_path) else None
    if not current(latest):
        latest = None
    if isinstance(report, dict):
        if report.get("stage", stage_id) != stage_id or report.get("completed") is True:
            return ""
        err = report.get("error") or report.get("message")
        if err:
            return str(err)
    if latest:
        repair = _read(latest)
        if isinstance(repair, dict) and repair.get("stage") == stage_id:
            failed = _failed_units(repair)
            # 最新轮次明确没有失败时，不复活先前报告里的失败单元。
            if isinstance(repair.get("failed_units"), dict):
                return _unit_errors(failed)
    failed = _failed_units(report)
    if failed:
        return _unit_errors(failed)
    if isinstance(report, dict):
        notes = report.get("notes")
        if isinstance(notes, list):
            return "；".join(note for note in notes if isinstance(note, str))
    task = _read(task_path) if current(task_path) else None
    if isinstance(task, dict):
        if task.get("stage") != stage_id or task.get("status") != "needs_review":
            return ""
        preconditions = task.get("preconditions")
        if isinstance(preconditions, list):
            return "；".join(item for item in preconditions if isinstance(item, str))
    # invoke 启动/超时/协议失败未必写 import-report；实际错误在 stderr 外框。
    if (envelope.get("stage", stage_id) == stage_id and current(log_path)
            and envelope.get("error")):
        return str(envelope["error"])
    return ""


def _structure_report_passed(batch: Path) -> bool:
    gate = _read(batch / "07_review" / "structure-gate.json")
    return isinstance(gate, dict) and gate.get("passed") is True


def _validation_report_passed(batch: Path) -> bool:
    report = _read(batch / "07_review" / "validation-report.json")
    return isinstance(report, dict) and report.get("passed") is True


def _stale_gate_pass(batch: Path, stage_id: str) -> bool:
    """阶段标记 blocked，但当前检查报告已是 passed：状态陈旧，重跑即收敛。

    这类陈旧态不能当成真实卡点反复报旧检查（例如结构闸门曾 blocked、后按
    当前输入跑通却没回写 completed）。它不是“绕过检查”，而是让用户知道
    直接重跑该闸门即可收敛，无需排查已解决的历史阻断。
    """
    if stage_id == "structure_gate":
        return _structure_report_passed(batch)
    if stage_id == "validate":
        return _validation_report_passed(batch)
    return False


def _validation_blockers(batch: Path) -> list[str]:
    report = _read(batch / "07_review" / "validation-report.json")
    if not isinstance(report, dict):
        return []
    out = []
    for item in report.get("checks", []) or []:
        if item.get("blocking") and not item.get("passed"):
            msg = item.get("output") or item.get("error") or ""
            out.append(f"{item.get('check')}：{str(msg)[:160]}")
    for code in report.get("blocking_failures", []) or []:
        if not any(code in row for row in out):
            out.append(str(code))
    return out


def _structure_blockers(batch: Path) -> list[str]:
    gate = _read(batch / "07_review" / "structure-gate.json")
    if not isinstance(gate, dict):
        return []
    out = []
    for item in (gate.get("blockers") or [])[:8]:
        if isinstance(item, dict):
            code = item.get("code") or item.get("reason") or ""
            msg = item.get("message") or item.get("detail") or ""
            out.append(f"{code}：{msg}".strip("：")[:160])
        else:
            out.append(str(item)[:160])
    return out


def _format_errors(report) -> list[str] | None:
    """只解释 errors；None 表示报告缺损，空列表不代表可忽略台账失败。"""
    if not isinstance(report, dict) or not isinstance(report.get("errors"), list):
        return None
    out = []
    for item in report["errors"]:
        if not isinstance(item, dict) or not item.get("code") or not item.get("message"):
            return None
        unit = f" [{item['unit_id']}]" if item.get("unit_id") else ""
        out.append(f"{item['code']}{unit}：{item['message']}")
    return out


def _format_detail(batch: Path, note: str = "") -> str:
    report = _read(batch / "07_review" / "upload-format.json")
    errors = _format_errors(report)
    if errors is not None:
        if errors:
            return "；".join(errors)
        # 脚本报告只覆盖格式检查；后续固化钩子仍可能失败，台账记录才是本次阶段结论。
        if note:
            return note + "；现有格式报告 errors 为空，不证明本次阶段已完成。"
        return "格式报告 errors 为空；阶段失败原因未记录，需重新检查格式阶段日志。"
    text = _tail_file(batch / "workflow" / "logs" / "format_gate.log", limit=None)
    # CLI 输出的是带缩进的 JSON，日志尾部常为 deferred；即使外框截断也优先取 errors 数组。
    decoder = json.JSONDecoder()
    for match in reversed(list(re.finditer(r'"errors"\s*:\s*', text))):
        try:
            value, _ = decoder.raw_decode(text[match.end():])
        except ValueError:
            continue
        errors = _format_errors({"errors": value})
        if errors is not None:
            return "；".join(errors) if errors else "格式日志 errors 为空；阶段失败原因未记录。"
    # 缺损报告回退普通异常日志前，剔除非阻断数组；数组本身损坏时丢弃其后残片。
    for match in reversed(list(re.finditer(r'"(?:deferred|warnings)"\s*:\s*', text))):
        try:
            _, end = decoder.raw_decode(text[match.end():])
        except ValueError:
            text = text[:match.start()]
        else:
            text = text[:match.start()] + text[match.end() + end:]
    text = "\n".join(line for line in text.splitlines()
                     if not re.fullmatch(r'[\s{}\[\],]*', line))
    return _last_error_line(text) or "格式阶段失败，缺少可读取的 errors 报告及错误日志。"


def _open_manual_items(batch: Path) -> int:
    queue = _read(batch / "07_review" / "manual-review-queue.json")
    if not isinstance(queue, dict):
        return 0
    closed = ("resolved", "rejected", "approved", "fixed", "dismissed")
    return sum(1 for x in (queue.get("items") or [])
               if isinstance(x, dict) and str(x.get("status") or "").lower() not in closed)


# 人工审核差异的代码：它们是审核产出，不是闸门故障；不能当作卡点原因展示。
DIFFERENCE_CODES = {"REVIEW_ROUND_FINDING", "MANUAL_REVIEW_OPEN", "REVIEW_FINDING_OPEN"}


def _review_gate_blockers(batch: Path) -> list[str]:
    """只读 review-gate.json，挑出确定性阻断项；人工差异另行计数、不混入。"""
    report = _read(batch / "07_review" / "review-gate.json")
    if not isinstance(report, dict):
        return []
    rounds = report.get("rounds") if isinstance(report.get("rounds"), dict) else {}
    errors = report.get("errors")
    if not isinstance(errors, list):
        errors = rounds.get("errors")
    out, seen = [], set()
    for item in errors or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip()
        if not code or code in DIFFERENCE_CODES:
            continue
        unit = f" [{item['unit_id']}]" if item.get("unit_id") else ""
        reason = str(item.get("message") or item.get("reason") or "").strip()
        text = f"{code}{unit}" + (f"：{reason}" if reason else "")
        if text not in seen:
            seen.add(text)
            out.append(text[:200])
    return out


def _review_differences(record: dict) -> bool:
    """一审/二审差异的设计状态：已执行完成、按策略继续、但没有放行。

    这正是 ``runner.do_review_gate`` 在 ``manual_only`` 时写入的状态。差异属于
    审核产出，必须保留给人工判断；它不是故障，也不能靠自动改标签/改判通过消除。
    """
    return (record.get("status") == mf.NEEDS_REVIEW
            and record.get("execution_completed") is True
            and record.get("continue_unqualified") is True
            and not record.get("release_ready"))


# 常见阻断检查的“为什么+怎么修”。说明必须能指向具体判定口径，避免只给
# 一句“按阻断项修复”让用户反复试错。
CHECK_HINTS = {
    "解析 check_explanations": "解析/大意必须能在原卷（Word 正文或附录）逐字找到；"
        "PDF/OCR 副本 wording 不同不算冲突，但权威原文里同一题出现两份不同解析仍算冲突。"
        "查看 07_review/validation-report.json 中该检查的 output 定位题目。",
    "答案 check_answers": "答案值必须与答案来源一致，缺答案留空或「无」，不能改写。",
    "结构 check_structure": "题号/选项/材料边界与原卷不一致；对照原卷修正结构后重跑。",
    "标签 check_tags": "知识点必须是目录叶子且名称与 ID 对应；不要自由造词。",
    "题型标签 audit_type_tags": "题型/子类必须是目录值，且与原题一致。",
    "原卷逐题覆盖与材料归属 source_audit": "逐题回指原卷失败：题干/选项/材料必须来自对应切片。",
    "正文保真 check_fidelity": "图片型材料天然可能对不上，属能力受限提示；文本差异才需修。",
    "画线 check_underline": "画线/下划线/空档写法属格式提示，不阻断流程；如需美化可按提示删除答题线占位。",
    "空档 audit_underlines": "空档写法属格式提示，不阻断流程。",
    "表格 check_tables": "表格列数/空档写法属格式提示，不阻断流程；结构错位才是实质错误。",
    "完成句子 audit_sentence_blanks": "空档写法属格式提示，不阻断流程。",
}


def _check_hint(detail: str) -> str:
    for name, hint in CHECK_HINTS.items():
        if name in (detail or ""):
            return hint
    return ""


# 每个阶段「怎么修」的通用指引：优先给出可粘贴命令，再给一句人话。
def _remedy(stage: contracts.Stage, batch_dir: str, detail: str = "") -> dict:
    base = f"python -m workflow.runner"
    if stage.kind == contracts.HUMAN:
        return {"hint": "这是人工闸门，需你本人确认（不代签）：核对批次/来源/目标/查重后标记批准。",
                "command": f"{base} mark --batch-dir {batch_dir} --stage {stage.id} --status approved"}
    if stage.id == "review_gate":
        return {"hint": "只修复审核闸门的确定性阻断项（标签体检/流程图/协议失败）；"
                        "一审二审与原标签的差异是审核产出，保留给人工判断，不自动改判或改标签。",
                "command": ""}
    if stage.kind == contracts.GATE:
        specific = _check_hint(detail)
        if specific:
            return {"hint": "确定性检查未过。" + specific,
                    "command": f"{base} run --batch-dir {batch_dir} --stage {stage.id} --force"}
        return {"hint": "确定性检查未过：按上面列出的阻断项修复对应问题后重跑该阶段。",
                "command": f"{base} run --batch-dir {batch_dir} --stage {stage.id}"}
    if stage.id == "format_gate":
        if detail.startswith("固化批次失败："):
            return {"hint": "格式检查与来源固化是不同检查；先按具体异常恢复来源绑定及前置批次对象，"
                            "复验受影响内容和审核后再运行最终格式阶段。现有空 errors 不代表固化成功。",
                    "command": ""}
        hint = "查看 07_review/upload-format.json 的 errors，按 code、unit_id、message 定位本地格式错误后重跑。"
        if "MATERIAL_TABLE_" in detail:
            hint += ("定位 06_batch/batch_meta.json 中该 unit_id 的 materialBlocks[].rows[].cells，"
                     "对照原卷表格核对 rowspan/colspan 与单元格排列：合并后的各行须覆盖相同列，"
                     "不得有空洞、重叠或跨出末行；保留原文与真实合并关系，不补造单元格内容。")
        return {"hint": hint,
                "command": f"{base} run --batch-dir {batch_dir} --stage {stage.id} --force"}
    if stage.kind == contracts.COMMAND:
        return {"hint": "确定性脚本执行失败：按日志报错修复输入或环境后重跑。",
                "command": f"{base} run --batch-dir {batch_dir} --stage {stage.id} --force"}
    if stage.id == "audio":
        return {"hint": "听力切段需要先准备切片：在「模型与设置 → 听力切段」启用并切段；"
                        "如果这批次不需要听力音频，可在音频设置里明确跳过听力。",
                "command": ""}
    if stage.kind == contracts.AGENT:
        if "not verbatim-bound to supplied answer material" in detail:
            return {"hint": "答案来源逐字绑定检查未通过：按列出的失败单元和题号核对本次任务所供权威答案材料，"
                            "先排除加粗、空白、引号、全半角及句末句号等不影响正确性的格式差异，此类差异自动按等价处理。"
                            "保留原答案文字；归一化后仍不同才修正内容或来源绑定，再复验该检查。"
                            "仅修复受影响单元，保留已验证单元；不得编造答案或原样重试。",
                    "command": ""}
        return {"hint": "代理阶段未产出合格产物：按具体失败单元与校验错误定位输入、输出或校验器问题，"
                        "自动修正后用原检查复验；保留已验证单元，不原样重试。",
                "command": f"{base} run --batch-dir {batch_dir} --stage {stage.id} --force"}
    return {"hint": "", "command": ""}


def _ocr_wait_detail(waiting: dict) -> str:
    """Local fixed copy only: never display remote errors, URLs or tokens."""
    phases = {"submitted": "OCR任务已提交", "accepted": "OCR任务已提交",
              "queued": "OCR任务排队中", "pending": "OCR任务排队中",
              "running": "OCR任务运行中", "processing": "OCR任务运行中",
              "downloading": "OCR结果下载中", "download": "OCR结果下载中",
              "result_downloading": "OCR结果下载中"}
    descriptions = []
    for task in waiting.get("progress", []):
        description = phases.get(task.get("phase")) or phases.get(task.get("state")) or "OCR任务已提交"
        if description not in descriptions:
            descriptions.append(description)
    return "；".join(descriptions) + "；稍后续查原任务，提取尚未完成"


def diagnose(batch_dir) -> dict:
    """返回整个批次的卡点诊断。

    结构：{batch, status, blocked_at, items:[{stage,title,status,kind,reason,detail,hint,command}], summary}
    只用现成证据，不读取网络、不改文件。
    """
    batch = Path(batch_dir).expanduser().resolve()
    try:
        manifest = mf.load(batch)
    except (OSError, ValueError, KeyError) as exc:
        return {"batch": batch.name, "status": "unknown", "blocked_at": None,
                "items": [], "summary": f"无法读取批次台账：{exc}"}
    stages = manifest.get("stages") or {}
    inputs = manifest.get("inputs") or {}
    # One read-only observation shares each dependency's full acceptance result.
    # Keep only immutable booleans here (including failures), never reports or a
    # cache on the manifest: another diagnose or any write boundary must recheck.
    dependency_results: dict[str, bool] = {}

    def dependency_satisfied(stage_id: str) -> bool:
        if stage_id not in dependency_results:
            dependency_results[stage_id] = mf.dependency_satisfied(
                manifest, stage_id, allow_unqualified=True)
        return dependency_results[stage_id]

    items = []
    quality_notes = []
    first_blocked = None
    # 输入缺失导致的跳过（缺 audio/answers 等可选输入）不是“待处理”，诊断不应
    # 把它们当作卡点反复上报。跳过条件可能随后消失（补了音频），此时状态沿用
    # pending 等待正常调度，也无需报卡点。
    def _input_skip_reason(stage) -> str:
        try:
            return mf.skip_reason(manifest, stage)
        except (KeyError, TypeError, ValueError, OSError):
            return ""
    # 限流/额度冷却时，真正的卡点是“模型暂时不可用”，不是某个下游阶段的旧报错。
    # 把它放在最前面，避免用户看到一堆 failed/needs_review 却一直在重试模型。
    unfinished = any((record.get("status") or mf.PENDING) not in mf.TERMINAL_OK
                     for record in stages.values())
    if unfinished:
        try:
            from .auto import _read_cooldown
            cooldown = _read_cooldown(batch)
        except (ImportError, OSError, ValueError):
            cooldown = 0
        if cooldown > 0:
            items.append({
                "stage": "model", "title": "模型调用", "status": mf.BLOCKED, "kind": "agent",
                "reason": "限流/额度冷却中", "detail": f"约 {int(cooldown)} 秒后可再次尝试；实际额度以服务商返回为准",
                "hint": "等待额度恢复，或在「模型与设置」里换一个可用模型/Provider；不要连续点重试。",
                "command": "", "depends_on": [], "unmet": [], "missing_inputs": [],
                "note": "", "blocking": True,
            })
            first_blocked = "model"
    for stage in contracts.STAGES:
        record = stages.get(stage.id) or {}
        status = record.get("status") or mf.PENDING
        if status in mf.TERMINAL_OK:
            continue
        if stage.id == "extract":
            from .runner import ocr_wait_state
            waiting = ocr_wait_state(batch, manifest)
            if waiting:
                items.append({"stage": stage.id, "title": stage.title, "status": mf.PENDING,
                              "kind": stage.kind, "reason": "OCR任务等待结果，稍后续查原任务",
                              "detail": _ocr_wait_detail(waiting),
                              "hint": "按等待间隔自动续查已提交任务", "command": "",
                              "depends_on": [], "unmet": [], "missing_inputs": [],
                              "note": "提取未完成，下游不能使用部分OCR结果",
                              "failure_class": "", "next_action": "waiting_external",
                              "retry_after": waiting["retry_after"], "blocking": False})
                continue
        # 用户明确“课时试卷允许没有听力/答案”等：仅在阶段尚未实际执行（pending）
        # 且属可跳过输入时才视为正常可跳过；已 FAILED/needs_review/blocked 的阶段
        # 是真实执行结果，不能被“历史批次”之类的跳过理由掩盖。
        input_skip_reason = _input_skip_reason(stage) if status == mf.PENDING else ""
        # 只有 review_gate 会带 continue_unqualified；它是「审核已执行、差异待人工」
        # 的设计态，统一走下面的 review_differences 分支，不再因依赖复验结果不同
        # 而在“质量备注/卡点”两种展示间摇摆。
        # 只把「真正卡住当前推进」的阶段列为卡点：已失败/待复核/阻断，或第一个未完成的。
        note = str(record.get("notes") or "")
        unmet = [d for d in stage.depends_on if not dependency_satisfied(d)]
        missing_inputs = [k for k in stage.requires
                          if not (inputs.get(k) or {}).get("exists")]

        reason, detail = "", ""
        if input_skip_reason:
            # 可跳过阶段不是卡点：给出正常说明，不当作“缺少必需输入”或失败。
            reason = "可跳过（" + input_skip_reason + "）"
            detail = input_skip_reason
        review_differences = stage.id == "review_gate" and _review_differences(record)
        if review_differences:
            # 差异是审核产出（AGENTS.md 硬规则 6）：保留在人工队列与 review-gate.json，
            # 供人工判断模型/知识头的偏差；不是要修掉的故障，也不阻断手动上传。
            open_items = _open_manual_items(batch)
            reason = "审核已执行：差异待人工判断（设计状态，不是卡点）"
            detail = (f"审核差异队列有 {open_items} 项待人工判断"
                      if open_items else "审核差异明细保留在 07_review/review-gate.json")
            detail += "；未放行（release_ready=false），不阻断手动上传"
            # 审核已消费上游并执行完成，上游 review 不再作为未满足依赖展示。
            unmet = []
            quality_notes.append({"stage": stage.id, "status": status,
                                  "reason": "已执行，有质量差异（未放行）",
                                  "notes": note, "blocking": False})
        elif status in (mf.FAILED, mf.NEEDS_REVIEW, mf.BLOCKED, mf.INVALIDATED):
            if status == mf.INVALIDATED:
                reason = "当前阶段需按内容变化更新"
                invalidation = record.get("invalidation") or {}
                if invalidation.get("scope") == "stage":
                    detail = str(invalidation.get("reason") or note)
            elif status == mf.NEEDS_REVIEW:
                reason = "代理产出未通过校验：需定位具体检查并自动修正复验"
            elif status == mf.BLOCKED:
                reason = "阶段被阻断"
                if _stale_gate_pass(batch, stage.id):
                    # 检查报告已通过、只是状态陈旧：直接重跑即收敛，不是真实卡点。
                    reason = "阶段状态陈旧：当前检查已通过，重跑该闸门即收敛"
                    detail = "最近一次检查报告 passed=true，manifest 仍是旧的 blocked；直接重跑本阶段即可回写完成，无需排查已解决的历史阻断。"
            else:
                reason = "阶段失败"
            # 逐层找最具体的原因
            if stage.id == "structure_gate":
                bl = _structure_blockers(batch)
                if bl:
                    detail = "；".join(bl)
            if stage.id == "format_gate":
                detail = _format_detail(batch, note if note != "最终格式检查未通过" else "")
            if stage.id in ("validate",):
                bl = _validation_blockers(batch)
                if bl:
                    detail = "；".join(bl[:6])
            if stage.id == "review_gate" and status not in (mf.INVALIDATED, mf.FAILED):
                if status == mf.BLOCKED:
                    blockers = _review_gate_blockers(batch)
                    open_items = _open_manual_items(batch)
                    reason = ("审核闸门未通过：存在确定性阻断项" if blockers
                              else "审核闸门阻断原因待核验")
                    detail = ("；".join(blockers[:6]) if blockers
                              else "缺少具体非差异阻断码；不能把人工差异数量当成失败证据")
                    if open_items:
                        detail += (f"；另有 {open_items} 项人工审核差异待判断"
                                   "（差异是产出，不作为卡点）")
                else:
                    # 没有 continue_unqualified 的 needs_review/failed 不能拿“差异”
                    # 掩盖审核未执行完成；真正的失败原因由日志/结构化错误给出。
                    reason = "审核未执行完成，不能按差异放行"
                    blockers = _review_gate_blockers(batch)
                    detail = "；".join(blockers[:6])
            if not detail:
                err = _agent_error(batch, stage.id, record)
                if err:
                    detail = err
            if not detail and stage.id == "review":
                detail = _review_failure_detail(batch, record)
            if not detail:
                log_path = batch / "workflow" / "logs" / f"{stage.id}.log"
                current_log = True
                if stage.id == "review" and record.get("started_at"):
                    from datetime import datetime
                    try:
                        current_log = (log_path.is_file() and log_path.stat().st_mtime
                                       >= datetime.fromisoformat(record["started_at"]).timestamp())
                    except (OSError, ValueError, TypeError):
                        current_log = False
                log_err = _last_error_line(_tail_file(log_path, limit=None)) if current_log else ""
                if log_err:
                    detail = log_err
            if not detail:
                detail = note
            if stage.id == "audio":
                evidence = _read(batch / "02_audio" / "segment-evidence.json")
                if isinstance(evidence, dict) and evidence.get("needs_review_units"):
                    units = "、".join(str(u) for u in evidence["needs_review_units"][:12])
                    detail = "切片候选已生成，需核对边界的单元：" + units
                elif not (batch / "02_audio" / "segment-evidence.json").is_file():
                    detail = "缺少 02_audio/segment-evidence.json：听力切段尚未运行或未启用（可选能力）"
        if unmet and not review_differences:
            reason = reason or "等待上游交付满足条件"
            dependency_details = []
            for dep in unmet:
                dep_record = stages.get(dep) or {}
                dep_status = dep_record.get("status") or mf.PENDING
                if dep_status in mf.TERMINAL_OK:
                    state_detail = "已执行，当前产物或消费者校验未满足"
                elif dep_record.get("execution_completed"):
                    state_detail = "已执行，质量或交付条件未满足"
                else:
                    state_detail = "尚未完成执行"
                dependency_details.append(f"{dep}（{state_detail}；status={dep_status}）")
            detail = (detail + "；" if detail else "") + "上游交付未满足：" + "、".join(dependency_details)
        if missing_inputs and not input_skip_reason:
            reason = reason or "缺少必需输入"
            detail = (detail + "；" if detail else "") + "缺少输入：" + "、".join(missing_inputs)

        remedy = _remedy(stage, str(batch), detail)
        if review_differences:
            remedy = {"hint": "差异是审核产出：在工作台「审核」页逐项人工判断；"
                              "不要自动改标签或改判通过，也不必为了放行重跑审核。",
                      "command": ""}
        # 真正的「卡点」是本阶段自身有问题（失败/待复核/阻断/失效，或它自己
        # 缺必需输入且没有被上游挡住），而不是单纯被上游拖住等着的下游阶段。
        # 审核差异设计态虽然 status=needs_review，但属于已验证产出，不算卡点。
        own_problem = (not review_differences
                       and not input_skip_reason
                       and (status in (mf.FAILED, mf.NEEDS_REVIEW, mf.BLOCKED, mf.INVALIDATED)
                            or (bool(missing_inputs) and not unmet)))
        failure_class = ""
        if review_differences:
            failure_class = "quality_differences"
        elif status == mf.INVALIDATED:
            failure_class = "stale_input"
        elif status == mf.FAILED:
            failure_class = "agent_or_script_failure" if stage.kind in (contracts.AGENT, contracts.COMMAND) else "gate_failure"
        elif status == mf.NEEDS_REVIEW:
            failure_class = "agent_output_needs_retry"
        elif status == mf.BLOCKED:
            if stage.id == "review_gate":
                failure_class = "quality_findings"
            elif stage.kind == contracts.GATE:
                failure_class = "deterministic_gate_failure"
            else:
                failure_class = "blocked_dependency_or_evidence"
        if missing_inputs and not review_differences and not input_skip_reason:
            failure_class = "missing_required_input"
        elif input_skip_reason and not review_differences:
            failure_class = ""
        next_action = (
            input_skip_reason if input_skip_reason else
            "人工判断审核差异；未放行但不阻断手动上传" if failure_class == "quality_differences" else
            "重新导出并重跑当前阶段" if failure_class == "stale_input" else
            "修正代理输出后重试" if failure_class == "agent_output_needs_retry" else
            "修复审核闸门确定性阻断项后复验（人工差异保留，不自动改判）" if failure_class == "quality_findings" else
            "查看阻断码并修复源文件/批次产物" if failure_class in ("deterministic_gate_failure", "gate_failure") else
            "补齐必需输入" if failure_class == "missing_required_input" else
            "等待上游阶段完成" if unmet else
            "重跑当前阶段"
        )
        entry = {"stage": stage.id, "title": stage.title, "status": status, "kind": stage.kind,
                 "reason": reason or (f"等待执行（{note}）" if note else "等待执行"),
                 "detail": detail, "hint": remedy["hint"], "command": remedy["command"],
                 "depends_on": list(stage.depends_on), "unmet": unmet,
                 "missing_inputs": missing_inputs, "note": note,
                 "failure_class": failure_class, "next_action": next_action,
                 "blocking": own_problem}
        items.append(entry)
        if first_blocked is None and own_problem:
            first_blocked = stage.id

    # Blocking failures first, but ALWAYS in contract stage order, so an
    # upstream failure (e.g. format_gate) precedes a pure dependent wait
    # (review_gate) instead of the downstream symptom being diagnosed first.
    order = {sid: i for i, sid in enumerate(contracts.STAGE_ORDER)}
    ordered = ([it for it in items if it["blocking"]]
               + [it for it in items if not it["blocking"]])
    ordered.sort(key=lambda it: (0 if it["stage"] in ("model",) else 1,
                                  order.get(it["stage"], len(order))))
    # Prefer a real failure, then actual unfinished work, before quality output.
    head = next((it for it in ordered if it["blocking"]), None)
    if head is None:
        head = next((it for it in ordered
                     if it["stage"] not in ("upload_gate", "upload", "publish_gate")
                     and it.get("failure_class") != "quality_differences"), None)
    if head is None:
        head = ordered[0] if ordered else None
    differences = [it for it in items if it.get("failure_class") == "quality_differences"]
    from . import execution_state
    local_executed = all(execution_state.executed(stages.get(sid), allow_unqualified=True)
                         for sid in execution_state.LOCAL_STAGES)
    if not any(it["blocking"] for it in items) and differences and local_executed:
        open_items = _open_manual_items(batch)
        count = f"{open_items} 项" if open_items else ""
        summary = (f"批次「{batch.name}」：自动阶段已执行完成；"
                   f"{count}审核差异待人工判断（不阻断手动上传），未宣称放行")
    elif head:
        summary = f"批次「{batch.name}」：{'卡在' if head['blocking'] else '等待'}「{head['title']}」：{head['reason']}"
        if head["detail"]:
            summary += "——" + head["detail"][:120]
        if differences:
            summary += "；已记录的审核差异保留，不作为卡点或当前执行完成的证明"
    else:
        summary = "自动阶段已执行完成；存在质量差异，未宣称放行" if quality_notes else "流程已完成"
    return {"batch": manifest.get("workflow_id") or batch.name,
            "batch_name": batch.name,
            "batch_dir": str(batch),
            "status": manifest.get("status") or mf.derive_status(manifest),
            "blocked_at": first_blocked,
            "items": ordered, "quality_notes": quality_notes,
            "blocking_count": sum(1 for it in items if it["blocking"]),
            "waiting_count": sum(1 for it in items if not it["blocking"]),
            "summary": summary}


def explain(batch_dir, stage_id: str | None = None) -> dict:
    """单阶段诊断（给 CLI explain 用）；不给 stage 则返回整批诊断里该阶段的条目。"""
    result = diagnose(batch_dir)
    if stage_id is None:
        return result
    for item in result["items"]:
        if item["stage"] == stage_id:
            return item
    # 该阶段已完成或不存在
    return {"stage": stage_id, "status": "completed_or_unknown",
            "reason": "该阶段已完成或未登记", "detail": "", "hint": "", "command": ""}
