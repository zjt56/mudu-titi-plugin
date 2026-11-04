# -*- coding: utf-8 -*-
"""生成每套卷的审核通报：哪些题已核对无误，哪些题有疑问要人处理。

三类产物：
- 07_review/report.json        机器可读的统计
- 07_review/report.md          逐题明细，给人看
- 07_review/notification.txt   一段可直接发群里的短通知

判定口径：
- 无误    ：标注 + 独立审核都完成（rounds>=2）、verdict=ok、没有未解决发现
- 有疑问  ：存在未解决发现（minor），或审核结论是 suspect
- 阻断    ：存在未解决的 blocker / major / blocking 发现
- 未核对  ：没有候选，或没有审核结论
"""
from __future__ import annotations

import json
from pathlib import Path

from . import inventory, manifest as mf
from . import review

REVIEW_DIR = "07_review"

OK = "ok"
ATTENTION = "attention"
BLOCKED = "blocked"
UNVERIFIED = "unverified"

LABEL = {OK: "无误", ATTENTION: "有疑问", BLOCKED: "阻断", UNVERIFIED: "未核对"}


def _load(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return None


def _unit_status(batch_dir: Path) -> dict:
    payload = _load(batch_dir / REVIEW_DIR / "unit-status.json") or {}
    return payload.get("units", {}) if isinstance(payload, dict) else {}


def _all_findings(batch_dir: Path) -> list[dict]:
    rows = []
    for name in ("content-review.json", "metadata-review.json"):
        payload = _load(batch_dir / REVIEW_DIR / name)
        for item in review._findings(payload):
            rows.append({"source": name, **item})
    return rows


def collect(batch_dir) -> dict:
    batch_dir = Path(batch_dir)
    try:
        manifest = mf.load(batch_dir)
    except FileNotFoundError:
        manifest = {}
    units_meta = _unit_status(batch_dir)
    findings = _all_findings(batch_dir)

    candidate_files = {}
    folder = batch_dir / "candidates"
    if folder.is_dir():
        for path in sorted(folder.glob("*.json")):
            data = _load(path) or {}
            candidate_files[str(data.get("unit_id") or path.stem)] = data

    unit_ids = sorted(set(units_meta) | set(candidate_files)
                      | {str(f.get("unit_id")) for f in findings if f.get("unit_id")})

    rows = []
    for unit_id in unit_ids:
        meta = units_meta.get(unit_id, {})
        cand = candidate_files.get(unit_id, {})
        own = [f for f in findings if str(f.get("unit_id")) == unit_id]
        open_items = [f for f in own if review._is_open(f)]
        resolved = [f for f in own if not review._is_open(f)]
        severity = [str(f.get("severity") or "").lower() for f in open_items]
        blocking = any(f.get("blocking") is True or s in ("blocker", "major")
                       for f, s in zip(open_items, severity))
        verdict = str(meta.get("verdict") or "")
        rounds = int(meta.get("rounds") or 0)

        if not cand and not meta:
            state = UNVERIFIED
        elif blocking:
            state = BLOCKED
        elif open_items:
            state = ATTENTION
        elif verdict in ("ok",) and rounds >= 2:
            state = OK
        elif verdict in ("suspect", "error"):
            # 审核曾打问号，但相关发现都已人工处理 → 视为已澄清
            state = OK if resolved else ATTENTION
        elif verdict == "" or verdict == "missing":
            state = UNVERIFIED
        else:
            state = OK

        rows.append({
            "unit_id": unit_id,
            "number": str(meta.get("number") or cand.get("number") or ""),
            "type": str(meta.get("type") or cand.get("type") or ""),
            "answer": str(meta.get("answer") or cand.get("answer") or ""),
            "difficulty": str(meta.get("difficulty") or cand.get("difficulty") or ""),
            "knowledge": meta.get("knowledge") or cand.get("knowledge") or [],
            "verdict": verdict or "missing",
            "rounds": rounds,
            "state": state,
            "open": [{"field": f.get("field"), "severity": f.get("severity"),
                      "reason": f.get("reason"), "finding_id": f.get("finding_id")}
                     for f in open_items],
            "resolved": [{"field": f.get("field"), "severity": f.get("severity"),
                          "reason": f.get("reason"), "finding_id": f.get("finding_id")}
                         for f in resolved],
        })

    rows.sort(key=lambda r: (int(r["number"]) if r["number"].isdigit() else 10 ** 6,
                             r["unit_id"]))

    # 用机械清点补齐"整套卷里还没进入流程的题"
    inv = inventory.load(batch_dir) or {}
    inv_numbers = inv.get("numbers") or []
    covered = {r["number"] for r in rows if r["number"]}
    for number in inv_numbers:
        if str(number) in covered:
            continue
        rows.append({
            "unit_id": "-",
            "number": str(number),
            "type": "",
            "answer": "",
            "difficulty": "",
            "knowledge": [],
            "verdict": "missing",
            "rounds": 0,
            "state": UNVERIFIED,
            "open": [],
            "resolved": [],
        })
    rows.sort(key=lambda r: (int(r["number"]) if r["number"].isdigit() else 10 ** 6,
                             r["unit_id"]))
    coverage = {
        "paper_total": len(inv_numbers) or len(rows),
        "entered_workflow": len([r for r in rows if r["unit_id"] != "-"]),
        "verified": sum(1 for r in rows if r["state"] == OK),
        "pending": sum(1 for r in rows if r["state"] == UNVERIFIED),
        "unmatched_units": [r["unit_id"] for r in rows
                            if not r["number"] and r["unit_id"] != "-"],
    }

    gate = _load(batch_dir / REVIEW_DIR / "review-gate.json") or {}
    validation = _load(batch_dir / REVIEW_DIR / "validation-report.json") or {}
    counted = [r for r in rows if r["number"]]
    unmatched = [r for r in rows if not r["number"] and r["unit_id"] != "-"]
    counts = {state: sum(1 for r in counted if r["state"] == state) for state in LABEL}

    return {
        "batch": manifest.get("workflow_id", batch_dir.name),
        "batch_dir": str(batch_dir),
        "generated_at": mf.now_iso(),
        "meta": manifest.get("meta", {}),
        "status": manifest.get("status"),
        "total": len(counted),
        "counts": counts,
        "coverage": coverage,
        "unmatched": [{"unit_id": r["unit_id"], "state": r["state"],
                       "open": r["open"]} for r in unmatched],
        "rows": rows,
        "gate": {
            "passed": bool(gate.get("passed")),
            "blocking": len(gate.get("errors", [])),
            "reason": gate.get("rule", ""),
        },
        "validation": {
            "passed": bool(validation.get("passed")),
            "blocking_failures": validation.get("blocking_failures", []),
            "skipped": [s.get("check") for s in validation.get("skipped", [])],
            "warnings": validation.get("warnings", []),
        },
    }


def verdict_line(data: dict) -> str:
    counts = data["counts"]
    if counts[BLOCKED]:
        return "不可上传：还有阻断项"
    if counts[ATTENTION]:
        return "需人工处理后才能上传"
    if counts[UNVERIFIED]:
        return "尚有题未核对"
    return "可进入人工上传确认"


def render_notification(data: dict) -> str:
    counts = data["counts"]
    title = f"【{data['batch']}】审核通报"
    stamp = data["generated_at"][:16].replace("T", " ")
    lines = [
        f"{title}  {stamp}",
        f"共 {data['total']} 题：✅ {counts[OK]} 无误 ｜ ❓ {counts[ATTENTION]} 有疑问 "
        f"｜ ⛔ {counts[BLOCKED]} 阻断 ｜ ○ {counts[UNVERIFIED]} 未核对",
        f"结论：{verdict_line(data)}",
    ]
    cov = data.get("coverage") or {}
    if cov.get("paper_total") and cov["paper_total"] > cov.get("entered_workflow", 0):
        lines.insert(2, f"整卷 {cov['paper_total']} 题：已进入流程 {cov['entered_workflow']} 题，"
                        f"还有 {cov['paper_total'] - cov['entered_workflow']} 题未开始")
    pending = [r for r in data["rows"] if r["state"] in (ATTENTION, BLOCKED)]
    if pending:
        lines.append("需处理：")
        for row in pending[:10]:
            reason = row["open"][0]["reason"] if row["open"] else (
                "审核结论为 " + row["verdict"])
            lines.append(f"- 第{row['number'] or row['unit_id']}题（{row['unit_id']}）：{reason}")
        if len(pending) > 10:
            lines.append(f"- …另有 {len(pending) - 10} 题")
    unverified = [r for r in data["rows"] if r["state"] == UNVERIFIED]
    if unverified:
        nums = "、".join((r["number"] or r["unit_id"]) for r in unverified[:10])
        lines.append(f"未核对：{nums}" + ("…" if len(unverified) > 10 else ""))
    if data["validation"].get("blocking_failures"):
        lines.append("体检阻断：" + "、".join(data["validation"]["blocking_failures"]))
    return "\n".join(lines) + "\n"


def render_markdown(data: dict) -> str:
    counts = data["counts"]
    meta = data.get("meta") or {}
    head = [
        f"# {data['batch']} 审核通报",
        "",
        f"- 生成时间：{data['generated_at']}",
        f"- 年份/地区：{meta.get('year') or '未填'} / {meta.get('region') or '未填'}",
        f"- 题量：{data['total']}",
        f"- 结论：**{verdict_line(data)}**",
        f"- 分布：无误 {counts[OK]} ｜ 有疑问 {counts[ATTENTION]} ｜ "
        f"阻断 {counts[BLOCKED]} ｜ 未核对 {counts[UNVERIFIED]}",
    ]
    cov = data.get("coverage") or {}
    if cov.get("paper_total"):
        head.append(f"- 整卷覆盖：{cov['paper_total']} 题中已进入流程 "
                    f"{cov['entered_workflow']} 题，未开始 "
                    f"{max(0, cov['paper_total'] - cov['entered_workflow'])} 题")
    head += [
        "",
        "## 逐题状态",
        "",
        "| 题号 | 单元 | 题型 | 答案 | 核对轮次 | 状态 | 说明 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    body = []
    for row in data["rows"]:
        note = ""
        if row["open"]:
            note = "；".join(str(f["reason"])[:60] for f in row["open"][:2])
        elif row["resolved"]:
            note = f"已处理 {len(row['resolved'])} 条"
        body.append(
            f"| {row['number'] or '-'} | {row['unit_id']} | {row['type'] or '-'} | "
            f"{row['answer'] or '-'} | {row['rounds']} | {LABEL[row['state']]} | {note} |"
        )
    tail = ["", "## 检查情况", ""]
    if data["validation"].get("blocking_failures"):
        tail.append("- 本地体检阻断：" + "、".join(data["validation"]["blocking_failures"]))
    else:
        tail.append("- 本地体检：通过或未运行（无阻断项）")
    if data["validation"].get("skipped"):
        tail.append("- 跳过检查：" + "、".join(str(s) for s in data["validation"]["skipped"]))
    tail.append(f"- 审核闸门：{'通过' if data['gate']['passed'] else '未通过'}")
    return "\n".join(head + body + tail) + "\n"


def write(batch_dir) -> dict:
    batch_dir = Path(batch_dir)
    data = collect(batch_dir)
    out = batch_dir / REVIEW_DIR
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out / "report.md").write_text(render_markdown(data), encoding="utf-8")
    (out / "notification.txt").write_text(render_notification(data), encoding="utf-8")
    return data
