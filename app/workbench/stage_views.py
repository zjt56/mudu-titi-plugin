# -*- coding: utf-8 -*-
"""Finding bucketing, stage card projection, and paper metadata views.

Extracted from app/workbench/server.py to keep the HTTP server module focused.
All public symbols are re-exported by workbench.server for full compatibility.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from workflow import contracts as wf_contracts, execution_state as wf_execution_state, manifest as wf_manifest
from workflow.workflow_primitives import read_json_safe as load


def _inbox():
    from . import server
    return server.INBOX

STAGE_TITLES = {s.id: s.title for s in wf_contracts.STAGES} if wf_contracts else {}
STAGE_KINDS = {s.id: s.kind for s in wf_contracts.STAGES} if wf_contracts else {}
STAGE_ORDER = [s.id for s in wf_contracts.STAGES] if wf_contracts else []
TERMINAL_OK = set(wf_manifest.TERMINAL_OK) if wf_manifest else {"ok", "skipped"}
# 只有“已修复/驳回”才算处理完；已接受的风险仍然要人持续看见，所以仍进“需人工”。
RESOLVED_STATES = ("fixed", "rejected")
SEVERITY_ORDER = {"blocker": 3, "major": 2, "minor": 1}
# 人工介入只处理“标签对不对”：知识点、来源、分类、题型、难度。
LABEL_FIELDS = {"knowledge", "child_knowledge", "knowledgeids", "knowledge_points", "type", "subtype", "subtypes", "sample", "labels",
                "difficulty", "difficultyvalue", "source", "source_type", "sourcetype", "year",
                "location", "region", "filtermetadata", "categories", "category", "textbook",
                "grade", "topic"}
# 文字、格式、图片、编号属于前置流程（提取/结构检查）该解决的问题，不进标签审核。
FORMAT_FIELDS = {"stem", "options", "option", "answer", "material", "materialblocks", "number",
                 "unit_id", "unit_kind", "kind", "evidence", "issues", "attachments", "attachment",
                 "image", "images", "media", "table", "structure", "grouping", "alignment", "blank",
                 "punctuation", "wording", "explanation", "summary", "材料", "解析"}


def as_number(value):
    """把 'LOCAL-01'、'Q001'、'AH-2025-031'、1 统一成 '31'/'1' 这样的纯题号。

    取最后一段数字：前缀（AH-2025）不参与比较，只认题号本身；
    前导零也一并去掉，避免 'LOCAL-01' 和 '1' 对不上。
    """
    groups = re.findall(r"\d+", str(value or ""))
    return str(int(groups[-1])) if groups else ""


def findings_for(queue, numbers, number_map, unit_id="", scoped=False):
    """挑出属于这一行的差异。

    scoped-v1 批次里同一题号会在每个大题重号（Ⅰ/Ⅱ/Ⅲ 都从 1 开始），只能按单元
    身份绑定；按数字绑定会把别的大题的差异算到本行，或者让本行真实差异漏掉，
    列表上就出现「队列里有待审差异、行上却没有待审核标签」。
    """
    wanted = {as_number(n) for n in numbers if as_number(n)}
    key = str(unit_id or "").strip()
    result = []
    items = queue.get("items", []) if isinstance(queue, dict) else []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        unit = str(item.get("unit_id") or "").strip()
        if scoped:
            # 单元级差异只归属本单元；批次级差异（无 unit_id）不标到具体题行。
            if unit and unit == key:
                result.append(item)
            continue
        number = as_number(number_map.get(unit) or unit)
        if number in wanted:
            result.append(item)
    return result


def is_open(finding):
    return str(finding.get("status") or "") not in RESOLVED_STATES


def finding_bucket(finding):
    """把一条发现分成 label（标签审核）/ format（前置流程）。

    label：知识点、来源、分类、题型、难度这类“入库标签对不对”；
    format：题干、选项、答案、编号、结构、图片这类内容与格式问题，
            应该在提取和结构检查阶段解决，不该混进人工标签审核队列。
    """
    field = str(finding.get("field") or "").strip().lower()
    # 结构闸门阻断的题号/分组归属确认需人工决断，必须可见可处理，不当作静默格式错误。
    if str(finding.get("review_type") or "").lower() == "structure":
        return "label"
    if field in LABEL_FIELDS:
        return "label"
    if field in FORMAT_FIELDS:
        return "format"
    return "other"


def split_findings(findings):
    label, fmt, other = [], [], []
    for finding in findings:
        bucket = finding_bucket(finding)
        {"label": label, "format": fmt}.get(bucket, other).append(finding)
    return label, fmt, other


def group_duplicate_findings(findings, whole_unit=False):
    """只合并可证明完全相同的未决意见；组级标签也不能跨字段或冲突合并。"""
    groups = []
    by_key = {}
    for finding in findings:
        if not is_open(finding):
            continue
        unit = str(finding.get("unit_id") or "").strip()
        field = str(finding.get("field") or "").strip().lower()
        reason = str(finding.get("reason") or finding.get("question") or "").strip()
        fid = str(finding.get("finding_id") or "").strip()
        # Identical wording alone is insufficient: evidence and structured
        # observations carry the target and the actual conflicting values.
        try:
            content = json.dumps({k: v for k, v in finding.items() if k != "finding_id"},
                                 sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            content = None
        key = ((unit, field, reason, content)
               if (unit and field and reason and fid and content is not None
                   and (finding.get("evidence") or finding.get("round_sources"))) else None)
        if key is not None and key in by_key:
            by_key[key].append(finding)
        else:
            members = [finding]
            groups.append(members)
            if key is not None:
                by_key[key] = members
    result = []
    for members in groups:
        item = dict(members[0])
        item["member_ids"] = [f.get("finding_id") for f in members]
        item["members"] = [dict(f) for f in members]
        result.append(item)
    return result


def mark_row(row, findings):
    opened = [f for f in findings if is_open(f)]
    label_open = [f for f in opened if finding_bucket(f) == "label"]
    fmt_open = [f for f in opened if finding_bucket(f) == "format"]
    row = dict(row)
    row["open_count"] = len(label_open)
    row["format_count"] = len(fmt_open)
    row["finding_count"] = len(findings)
    row["severity"] = max((str(f.get("severity") or "") for f in label_open),
                          key=lambda s: SEVERITY_ORDER.get(s, 0), default="")
    row["format_severity"] = max((str(f.get("severity") or "") for f in fmt_open),
                                 key=lambda s: SEVERITY_ORDER.get(s, 0), default="")
    row["needs_human"] = bool(label_open)
    # 待审差异必须能一眼看出「审的是哪一项、原标签是什么、本轮判了什么」，
    # 不能只显示一个数字让人以为“题目正常却被标待审核”。
    diffs = []
    for finding in label_open:
        text = str(finding.get("reason") or finding.get("question") or "").strip()
        if not text:
            continue
        field = str(finding.get("field") or "").strip()
        label = {"type": "题型", "subtypes": "子类", "knowledge": "知识点"}.get(field, field)
        diffs.append((f"{label}：" if label and not text.startswith(label) else "") + text)
    row["open_diffs"] = list(dict.fromkeys(diffs))[:8]
    row["open_summary"] = "；".join(row["open_diffs"])
    return row


def paper_info(folder: Path, report, manifest) -> dict:
    """试卷名取自入库批次；旧批次缺来源时依次用已识别元数据、原卷 Word 文件名。"""
    from workflow.metadata_policy import MISSING, paper_meta

    def known(*values):
        return next((value for value in values if value and str(value).strip() != MISSING), None)

    batch = load(folder / "06_batch" / "batch.json", {}) or {}
    meta = (report.get("meta") if isinstance(report, dict) else None) or (manifest or {}).get("meta") or {}
    detected = {}
    if not known(batch.get("source"), meta.get("source"), meta.get("title")):
        text_path = folder / "01_source" / "正文.txt"
        if text_path.is_file():
            try:
                from workflow import denoise
                detected = denoise.extract_meta(text_path.read_text(encoding="utf-8-sig").splitlines()) or {}
            except (OSError, ValueError):
                detected = {}
    descriptor = (load(folder / "attachments" / "batch.json", {})
                  or load(_inbox() / folder.name / "batch.json", {}) or {})
    # 课时拆分件正文没有卷头：来源仍要按证据还原，最后一档才是原卷文件名。
    resolved = paper_meta({
        "paper_title": known(batch.get("source"), meta.get("source"), meta.get("title"),
                             meta.get("paper_title"), detected.get("source"), descriptor.get("title")),
        "source": known(batch.get("source"), meta.get("source"), detected.get("source")),
        "source_type": known(batch.get("source_type"), meta.get("source_type"), detected.get("source_type")),
        "year": batch.get("year") or meta.get("year") or detected.get("year"),
    }, folder)
    title = str(resolved.get("paper_title") or MISSING)
    if title != MISSING and re.search(r"\bUnit\s+\d+$", title):
        text_path = folder / "01_source" / "正文.txt"
        head_text = ""
        if text_path.is_file():
            try:
                with text_path.open("r", encoding="utf-8-sig") as fh:
                    head_text = "".join(fh.readline() for _ in range(15))
            except OSError:
                head_text = ""
        from workflow.metadata_ai import extract_unit_subtopic
        subtopic, _ = extract_unit_subtopic(head_text, str(batch.get("batch") or folder.name))
        if subtopic and subtopic not in title:
            title = f"{title} {subtopic}"
    orig_files = descriptor.get("original_filenames") if isinstance(descriptor.get("original_filenames"), dict) else {}
    original_filename = str(orig_files.get("source") or "").strip()
    return {"title": title,
            "original_filename": original_filename,
            "batch": str(batch.get("batch") or folder.name),
            "source": str(resolved.get("source") or MISSING),
            "source_type": str(resolved.get("source_type") or MISSING),
            "folder": folder.name, "year": resolved.get("year"),
            "region": batch.get("region") or meta.get("region") or detected.get("region"),
            "subject": batch.get("subject") or meta.get("subject") or "english"}


def active_stage_ids(manifest, cards=None):
    """Actual running stages with satisfied dependencies (display only).

    Dependency readiness alone never implies that a worker has started. Parallel
    workers persist their running status before execution; project only those
    records as active without changing dispatch or ledger state.
    """
    if wf_contracts is None or wf_manifest is None:
        return set()
    stages = (manifest or {}).get("stages") or {}
    cards = cards if cards is not None else [
        {"id": sid, "status": (stages.get(sid) or {}).get("status")} for sid in STAGE_ORDER]
    active = set()
    for card in cards:
        sid = card.get("id")
        if card.get("status") != "running":
            continue
        spec = wf_contracts.STAGE_BY_ID.get(sid)
        if spec is None:
            continue
        if all(manifest_dependency_satisfied(manifest, dep) for dep in spec.depends_on):
            active.add(sid)
    return active


def manifest_dependency_satisfied(manifest, dep_id):
    """Execution-level readiness of one dependency, without consumer audit.

    Display only: a dependency counts as met once it is executed (or explicitly
    skipped) and not marked for rebuild. Dispatch still uses the strict
    ``manifest.dependency_satisfied`` including ``inspect_stage``.
    """
    record = ((manifest or {}).get("stages") or {}).get(dep_id) or {}
    if record.get("rebuild_required"):
        return False
    if record.get("status") in TERMINAL_OK:
        return True
    if (dep_id == "review_gate" and record.get("status") == "needs_review"
            and record.get("execution_completed") is True
            and record.get("continue_unqualified") is True
            and not record.get("release_ready")):
        return True
    return False


def stage_cards(manifest, has_output: bool = False, advance_running: bool = False, diagnostics=None,
                batch_folder=None, runtime_activity=None, audio_state=None) -> dict:
    stages = (manifest or {}).get("stages") or {}
    recorded_running = (manifest or {}).get("status") == "running" or any(
        record.get("status") == "running" for record in stages.values() if isinstance(record, dict))
    running_unknown = bool(recorded_running and not advance_running)
    # 统一卡点诊断：把各阶段分散的真实原因翻译成一句人话 + 修复命令（只读视图）。
    diag_by_stage = {}
    if isinstance(diagnostics, dict):
        for item in diagnostics.get("items") or []:
            if isinstance(item, dict) and item.get("stage"):
                diag_by_stage[item["stage"]] = item
    from workflow.substeps import project as project_substeps
    substeps = project_substeps(manifest, batch_folder, runtime_activity, audio_state)
    cards = []
    for sid in (STAGE_ORDER or list(stages)):
        record = stages.get(sid) or {}
        card = {"id": sid, "title": STAGE_TITLES.get(sid, sid),
                "kind": STAGE_KINDS.get(sid) or record.get("kind") or "",
                "status": record.get("status") or "pending",
                "execution_completed": bool(record.get("execution_completed")),
                "continue_unqualified": bool(record.get("continue_unqualified")),
                "quality_status": record.get("quality_status") or "",
                "release_ready": bool(record.get("release_ready")),
                "description": (wf_contracts.STAGE_BY_ID[sid].description if wf_contracts and sid in wf_contracts.STAGE_BY_ID else ""),
                "depends_on": list(wf_contracts.STAGE_BY_ID[sid].depends_on) if wf_contracts and sid in wf_contracts.STAGE_BY_ID else [],
                "notes": record.get("notes") or "",
                "substeps": substeps.get(sid, [])}
        if record.get("rebuild_required") and card["status"] in TERMINAL_OK:
            # A retained old result is not the result of the requested rebuild.
            card["status"] = "invalidated"
        diag = diag_by_stage.get(sid)
        if diag:
            card["reason"] = diag.get("reason") or ""
            card["detail"] = diag.get("detail") or ""
            card["hint"] = diag.get("hint") or ""
            card["command"] = diag.get("command") or ""
            card["blocking"] = bool(diag.get("blocking"))
        cards.append(card)
    if wf_execution_state is not None:
        progress = wf_execution_state.projection(manifest or {}, stage_ids=[card["id"] for card in cards])
        done = progress["done"]
        current = next((card for card in cards if card["id"] == progress["current"]), None)
    else:
        done = sum(1 for card in cards if card["status"] in TERMINAL_OK)
        current = next((card for card in cards if card["status"] not in TERMINAL_OK), None)
    # 并行显示（只读投影）：实际已登记 running 且依赖满足的阶段同时显示“处理中”。
    # pending 只代表等待，即使依赖已经满足也不能冒充执行，不改任何执行或台账语义。
    active_ids = active_stage_ids(manifest, cards)
    for card in cards:
        card["active"] = card["id"] in active_ids
    early = ("extract", "structure_gate", "preassemble")
    stale = bool(has_output and current and (done == 0 or current["id"] in early))
    if advance_running:
        hint = "工作台正在处理"
    elif running_unknown:
        hint = "台账记录运行中，实际状态未知；外部 CLI 可能仍在运行，请先核实"
    elif current is None:
        hint = "流程已完成"
    elif stale:
        hint = f"台账未同步（已有内容，标记停在「{current['title']}」）"
    else:
        waiting = {"agent": "等待代理执行", "human": "等待人工确认"}.get(current["kind"], "等待执行")
        hint = f"{waiting}（{current['title']}）"
    return {"items": cards, "done": done, "total": len(cards), "current": current,
            "hint": hint, "stale": stale, "running_unknown": running_unknown,
            "diagnostics": diagnostics if isinstance(diagnostics, dict) else None}
