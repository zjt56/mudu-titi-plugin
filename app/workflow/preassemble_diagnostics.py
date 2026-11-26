# -*- coding: utf-8 -*-
"""Preassemble 只读局部诊断（A8 / A9 / E20 / D27 前置观测）。

设计原则（不把校验前移成整卷硬门禁）：
- 完全只读、确定性，不修改题目数据，不抛出阻断异常；
- 按 `unit_id` 输出 `findings` 与来源证据，供局部修复（`regenerate_units` / 金丝雀观测）消费；
- 最终交付仍由现有消费端校验（`validate` / `format_gate`）裁决。
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re

REPORT_REL = "06_batch/preassemble-diagnostics.json"

_EXPECTED_OPTION_COUNTS = {
    "7选5": 7,
    "七选五": 7,
    "6选5": 6,
    "六选五": 6,
    "5选5": 5,
    "五选五": 5,
    "四个选项": 4,
    "三个选项": 3,
}
_PROMPT_MARK_RE = re.compile(
    r"(?:每空一词|根据首字母|根据汉语|用所给词的适当形式|☆[^\n]{1,20}|共\s*\d+\s*小题)"
)


def _load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _unit_id(item: dict, index: int) -> str:
    uid = item.get("unit_id") or item.get("id") or item.get("question_id")
    if isinstance(uid, str) and uid.strip():
        return uid.strip()
    return f"unit-{index + 1}"


def _has_visual_or_table(text: str, obj: dict) -> bool:
    if "[IMG" in text or "<img" in text.lower() or "<table" in text.lower():
        return True
    for key in ("images", "attachments", "media"):
        val = obj.get(key)
        if isinstance(val, list) and val:
            return True
    return False


def _extract_subtypes(item: dict) -> list[str]:
    out = []
    for key in ("subtypes", "subtype"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            out.append(val.strip())
        elif isinstance(val, list):
            out.extend(str(v).strip() for v in val if isinstance(v, str) and v.strip())
    return out


def _diagnose_unit(item: dict, index: int) -> list[dict]:
    uid = _unit_id(item, index)
    findings: list[dict] = []
    subs = item.get("subquestions") or item.get("subQuestions") or item.get("sub_questions") or []
    is_group = bool(item.get("unit_kind") == "group" or item.get("kind") == "group" or subs)
    material = str(item.get("material") or "").strip()
    stem = str(item.get("stem") or "").strip()
    qtype = str(item.get("type") or item.get("question_type") or "").strip()
    subtypes = _extract_subtypes(item)
    legitimate_shape = item.get("legitimate_source_shape") or item.get("source_shape")

    # 1. A8：题组共享材料/导语缺失观测
    if is_group and not material and not _has_visual_or_table(material, item) and not legitimate_shape:
        if not (qtype == "补全对话" and any("填写单词" in s for s in subtypes)):
            findings.append({
                "unit_id": uid,
                "code": "PRE_A8_MISSING_SHARED_MATERIAL",
                "severity": "advisory",
                "message": "题组 material 为空且无合法无材料形态证明，建议核对原卷共享导语/材料绑定",
                "evidence": {
                    "type": qtype,
                    "subtypes": subtypes,
                    "sub_count": len(subs),
                },
            })

    # 2. A9：子题题干空内容观测
    if is_group and isinstance(subs, list):
        for s_idx, sub in enumerate(subs):
            if not isinstance(sub, dict):
                continue
            s_stem = str(sub.get("stem") or "").strip()
            s_opts = sub.get("options") or []
            s_shape = sub.get("legitimate_source_shape")
            if not s_stem and not s_opts and not _has_visual_or_table(s_stem, sub) and not s_shape:
                findings.append({
                    "unit_id": uid,
                    "code": "PRE_A9_EMPTY_SUBQUESTION",
                    "severity": "advisory",
                    "message": f"第 {s_idx + 1} 个子题题干与选项均为空且无图片/表格证据",
                    "evidence": {
                        "sub_index": s_idx,
                        "number": sub.get("number"),
                    },
                })

    # 3. E20：题型子类与选项数冲突观测
    expected_opt = None
    matched_sub = None
    for s in subtypes:
        if s in _EXPECTED_OPTION_COUNTS:
            expected_opt = _EXPECTED_OPTION_COUNTS[s]
            matched_sub = s
            break
    if expected_opt is not None:
        raw_options = item.get("options")
        actual_options = len(raw_options) if isinstance(raw_options, list) else 0
        if not actual_options and isinstance(subs, list):
            child_counts = [len(s.get("options") or []) for s in subs if isinstance(s, dict)]
            if child_counts:
                actual_options = max(child_counts)
        if actual_options != expected_opt:
            findings.append({
                "unit_id": uid,
                "code": "PRE_E20_OPTION_COUNT_CONFLICT",
                "severity": "advisory",
                "message": f"子类「{matched_sub}」预期 {expected_opt} 个选项，当前有 {len(raw_options)} 个",
                "evidence": {
                    "subtype": matched_sub,
                    "expected": expected_opt,
                    "actual": actual_options,
                },
            })

    # 4. D27：提示标记归属证据观测（仅记录，不误报阻断）
    combined = "\n".join(filter(None, [material, stem]))
    marks = _PROMPT_MARK_RE.findall(combined)
    if marks and not is_group and stem:
        findings.append({
            "unit_id": uid,
            "code": "PRE_D27_PROMPT_MARK_EVIDENCE",
            "severity": "info",
            "message": "独立题题干包含原卷提示标记，请确认归属为题内提示而非大题栏目横幅",
            "evidence": {
                "marks": marks[:5],
            },
        })

    return findings


def diagnose(batch_dir, items=None, *, write_report: bool = False) -> dict:
    """对批次候选题目执行只读局部诊断；可选落盘 06_batch/preassemble-diagnostics.json。"""
    batch = Path(batch_dir).resolve()
    if items is None:
        for rel in ("06_batch/batch.base.json", "06_batch/batch.json"):
            doc = _load_json(batch / rel)
            if doc and isinstance(doc.get("items"), list):
                items = doc["items"]
                break
        if items is None:
            items = []

    findings: list[dict] = []
    by_unit: dict[str, list[dict]] = {}
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        unit_findings = _diagnose_unit(item, idx)
        if unit_findings:
            uid = _unit_id(item, idx)
            by_unit[uid] = unit_findings
            findings.extend(unit_findings)

    report = {
        "schema": "preassemble-diagnostics-v1",
        "advisory_only": True,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "unit_count": len(items),
        "finding_count": len(findings),
        "by_unit": by_unit,
        "findings": findings,
    }
    if write_report:
        target = batch / REPORT_REL
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".preassemble-diag.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(target)
    return report
