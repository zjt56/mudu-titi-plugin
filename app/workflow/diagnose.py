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


def _tail_file(path: Path, limit: int = 1200) -> str:
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError:
        pass
    return ""


def _last_error_line(text: str) -> str:
    """从日志里挑最后一条真正的错误行（跳过 '调用模型/已校验' 等进度行）。"""
    noise = ("调用模型", "已校验并落盘", "复用已校验快照", "执行：", "$ ", "[stderr]")
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    for line in reversed(lines):
        if any(marker in line for marker in noise):
            continue
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
    return lines[-1] if lines else ""


def _agent_error(batch: Path, stage_id: str) -> str:
    """取该阶段最近一次 agent 尝试的具体错误：优先 import-report，其次响应外框。"""
    runs = batch / "workflow" / "agent-runs"
    if not runs.is_dir():
        return ""
    cands = sorted((p for p in runs.glob(f"{stage_id}-*") if p.is_dir()),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for run in cands:
        report = _read(run / "import-report.json")
        if isinstance(report, dict):
            err = report.get("error") or report.get("message")
            if err:
                return str(err)
        # 分片失败时错误在阶段日志，而不是这里；继续看下一个
    return ""


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


def _open_manual_items(batch: Path) -> int:
    queue = _read(batch / "07_review" / "manual-review-queue.json")
    if not isinstance(queue, dict):
        return 0
    closed = ("resolved", "rejected", "approved", "fixed", "dismissed")
    return sum(1 for x in (queue.get("items") or [])
               if isinstance(x, dict) and str(x.get("status") or "").lower() not in closed)


# 每个阶段「怎么修」的通用指引：优先给出可粘贴命令，再给一句人话。
def _remedy(stage: contracts.Stage, batch_dir: str) -> dict:
    base = f"python -m workflow.runner"
    if stage.kind == contracts.HUMAN:
        return {"hint": "这是人工闸门，需你本人确认（不代签）：核对批次/来源/目标/查重后标记批准。",
                "command": f"{base} mark --batch-dir {batch_dir} --stage {stage.id} --status approved"}
    if stage.kind == contracts.GATE:
        return {"hint": "确定性检查未过：按上面列出的阻断项修复对应问题后重跑该阶段。",
                "command": f"{base} run --batch-dir {batch_dir} --stage {stage.id}"}
    if stage.kind == contracts.COMMAND:
        return {"hint": "确定性脚本执行失败：按日志报错修复输入或环境后重跑。",
                "command": f"{base} run --batch-dir {batch_dir} --stage {stage.id} --force"}
    if stage.id == "audio":
        return {"hint": "听力切段需要先准备切片：在「模型与设置 → 听力切段」启用并切段；"
                        "如果这批次不需要听力音频，可在音频设置里明确跳过听力。",
                "command": ""}
    if stage.kind == contracts.AGENT:
        return {"hint": "代理阶段未产出合格产物：先看具体校验错误；多为内容抄写/格式可自动修复，"
                        "重跑一次通常即过。若反复失败，缩小分片（assemble_part_units）或检查模型配置。",
                "command": f"{base} run --batch-dir {batch_dir} --stage {stage.id} --force"}
    return {"hint": "", "command": ""}


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
    items = []
    first_blocked = None
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
        # 只把「真正卡住当前推进」的阶段列为卡点：已失败/待复核/阻断，或第一个未完成的。
        note = str(record.get("notes") or "")
        unmet = [d for d in stage.depends_on
                 if (stages.get(d) or {}).get("status") not in mf.TERMINAL_OK]
        missing_inputs = [k for k in stage.requires
                          if not (inputs.get(k) or {}).get("exists")]

        reason, detail = "", ""
        if status in (mf.FAILED, mf.NEEDS_REVIEW, mf.BLOCKED, mf.INVALIDATED):
            if status == mf.INVALIDATED:
                reason = "上游已变化，本阶段产物失效，需重跑"
            elif status == mf.NEEDS_REVIEW:
                reason = "代理产出待复核：需要修正后重跑或人工判断"
            elif status == mf.BLOCKED:
                reason = "阶段被阻断"
            else:
                reason = "阶段失败"
            # 逐层找最具体的原因
            if stage.id == "structure_gate":
                bl = _structure_blockers(batch)
                if bl:
                    detail = "；".join(bl)
            if stage.id in ("validate",):
                bl = _validation_blockers(batch)
                if bl:
                    detail = "；".join(bl[:6])
            if stage.id == "review_gate":
                open_items = _open_manual_items(batch)
                if open_items:
                    detail = f"人工队列仍有 {open_items} 项未处理"
            if not detail:
                err = _agent_error(batch, stage.id)
                if err:
                    detail = err
            if not detail:
                log_err = _last_error_line(_tail_file(batch / "workflow" / "logs" / f"{stage.id}.log"))
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
        if unmet:
            reason = reason or "等待上游阶段完成"
            detail = (detail + "；" if detail else "") + "上游未完成：" + "、".join(unmet)
        if missing_inputs:
            reason = reason or "缺少必需输入"
            detail = (detail + "；" if detail else "") + "缺少输入：" + "、".join(missing_inputs)

        remedy = _remedy(stage, str(batch))
        # 真正的「卡点」是本阶段自身有问题（失败/待复核/阻断/失效，或它自己
        # 缺必需输入且没有被上游挡住），而不是单纯被上游拖住等着的下游阶段。
        own_problem = (status in (mf.FAILED, mf.NEEDS_REVIEW, mf.BLOCKED, mf.INVALIDATED)
                       or (bool(missing_inputs) and not unmet))
        entry = {"stage": stage.id, "title": stage.title, "status": status, "kind": stage.kind,
                 "reason": reason or (f"等待执行（{note}）" if note else "等待执行"),
                 "detail": detail, "hint": remedy["hint"], "command": remedy["command"],
                 "depends_on": list(stage.depends_on), "unmet": unmet,
                 "missing_inputs": missing_inputs, "note": note,
                 "blocking": own_problem}
        items.append(entry)
        if first_blocked is None and own_problem:
            first_blocked = stage.id

    # 优先展示自身有问题的卡点；下游等待项按契约顺序排在后面。
    ordered = ([it for it in items if it["blocking"]]
               + [it for it in items if not it["blocking"]])
    head = ordered[0] if ordered else None
    if head:
        summary = f"卡在「{head['title']}」：{head['reason']}"
        if head["detail"]:
            summary += "——" + head["detail"][:120]
    else:
        summary = "流程已完成"
    return {"batch": manifest.get("workflow_id") or batch.name,
            "batch_dir": str(batch),
            "status": manifest.get("status") or mf.derive_status(manifest),
            "blocked_at": first_blocked,
            "items": ordered,
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
