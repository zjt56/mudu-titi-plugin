# -*- coding: utf-8 -*-
"""Local-only review workbench API. Upload writes to the cloud 上传暂存 only; no publish.

题目列表按“入库单元”合并：一个独立题一行，一个题组（完形、任务型阅读、
听力对话组等）一行，前端一次显示整组材料与全部小题。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import uuid
from functools import wraps
import base64
import shutil
import stat
import subprocess
import sys
import threading
from time import monotonic, time
from datetime import datetime
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from workbench import media_audit, taxonomy

try:
    from workflow.preview import (batch_rows as preview_batch_rows, slice_range, slice_text,
                                  source_slice, source_group_candidates, section_leadin)
except ImportError:  # 直接双击运行时给出手动路径
    preview_batch_rows = slice_range = slice_text = source_slice = source_group_candidates = section_leadin = None

try:
    from workflow import auto as auto_flow
    from workflow import contracts as wf_contracts
    from workflow import manifest as wf_manifest
    from workflow import execution_state as wf_execution_state
    from workflow import source_policy as wf_source_policy
except ImportError:  # 看台可以脱离流水线单独跑
    auto_flow = wf_contracts = wf_manifest = wf_execution_state = None
    wf_source_policy = None

ROOT = Path(__file__).resolve().parents[1]
# 插件版：宿主编译生效配置后，让本进程拉起的 runner/auto 子进程也走插件模型路由。
# 独立运行 app（没有 backend/.local-agent.effective.json）时保持原行为。
_plugin_agent_config = ROOT.parent / "backend" / ".local-agent.effective.json"
if _plugin_agent_config.is_file() and not os.environ.get("MUDU_AGENT_CONFIG"):
    os.environ["MUDU_AGENT_CONFIG"] = str(_plugin_agent_config)
# 数据目录可用环境变量指定（默认仍是本仓库下的 workflows/inbox）。
# 插件化独立开发时，代码放在插件版/app，数据仍指向用户真实批次目录，
# 无需软链接（软链接会被 batch_dir 拒绝），只覆盖数据根即可。
WORKFLOWS = Path(os.environ.get("WF_WORKFLOWS_DIR") or (ROOT / "workflows")).resolve()
INBOX = Path(os.environ.get("WF_INBOX_DIR") or (ROOT / "inbox")).resolve()
MEDIA_KINDS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml"}
DOC_SUFFIXES = (".docx", ".pdf")
AUDIO_SUFFIXES = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac")
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
SIDE_EFFECTS = ("upload", "publish", "delete")
_lock = threading.RLock()
_batches_snapshot = None
_batches_epoch = 0
_batches_read_lock = threading.Lock()
# 听力切段后台任务（安装 / 切段共用一把闸，避免并发下载与切片）。
_audio_job = {"running": False, "kind": "", "batch": "", "lock": threading.Lock()}


def serialized(function):
    """同服务的建批次、推进注册、删除与写入共享临界区。"""
    @wraps(function)
    def wrapped(*args, **kwargs):
        global _batches_snapshot, _batches_epoch
        with _lock:
            try:
                return function(*args, **kwargs)
            finally:
                # In-flight reads cannot republish a snapshot from before a write.
                _batches_epoch += 1
                _batches_snapshot = None
    return wrapped


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load(path, fallback=None):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return fallback


def save_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _validate_batch_name(name):
    if not isinstance(name, str) or not name or Path(name).name != name or name in (".", "..") or any(c in name for c in ("\\", "/", "\x00")):
        raise ValueError("非法批次名")


def batch_dir(name):
    _validate_batch_name(name)
    if WORKFLOWS.is_symlink() or (WORKFLOWS / name).is_symlink():
        raise ValueError("批次目录不能是软链接")
    target = (WORKFLOWS / name).resolve()
    if target.parent != WORKFLOWS.resolve() or not target.is_dir():
        raise ValueError("批次不存在")
    return target


def report_for(folder):
    report = load(folder / "07_review" / "report.json", {}) or {}
    manifest = load(folder / "workflow" / "manifest.json", {}) or {}
    queue = load(folder / "07_review" / "manual-review-queue.json", {}) or {}
    return report, manifest, queue


def question_rows(folder, report):
    """逐小题行：report.json 优先，其次候选文件。"""
    rows = report.get("rows") if isinstance(report, dict) else None
    if rows:
        return [r for r in rows if isinstance(r, dict)]
    result = []
    candidates = folder / "candidates"
    for path in sorted(candidates.glob("*.json")) if candidates.is_dir() else []:
        item = load(path, {}) or {}
        result.append({"unit_id": item.get("unit_id", path.stem), "number": item.get("number", ""),
                       "type": item.get("type", ""), "answer": item.get("answer", ""),
                       "difficulty": item.get("difficulty", ""), "knowledge": item.get("knowledge", []),
                       "state": "unverified", "open": [], "resolved": []})
    return result


def merged_rows(folder, report):
    """按入库单元合并：题组一行，带 numbers / subquestions / material。"""
    base = question_rows(folder, report)
    if preview_batch_rows is not None:
        try:
            rows = preview_batch_rows(folder, base)
        except (OSError, ValueError, TypeError, KeyError):
            rows = None
        if rows:
            return rows
    return base


def number_of_unit(rows):
    mapping = {}
    for row in rows:
        unit = str(row.get("unit_id") or "")
        number = str(row.get("number") or "").strip()
        if unit and number:
            mapping[unit] = number
    return mapping


def candidates_by_number(folder, *, unit_id=None):
    mapping = {}
    directory = folder / "candidates"
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        item = load(path, {}) or {}
        number = str(item.get("number") or "").strip()
        if unit_id is not None and str(item.get("unit_id") or "") != str(unit_id):
            continue
        if number.isdigit():
            mapping[number] = (item, path)
    return mapping


def candidate(folder, unit_id):
    safe = str(unit_id).replace("/", "_").replace("\\", "_")
    path = folder / "candidates" / f"{safe}.json"
    item = load(path)
    if item is not None:
        return item, path
    batch = load(folder / "06_batch" / "batch_meta.json") or load(folder / "06_batch" / "batch.json") or {}
    units = batch.get("units", batch.get("items", batch if isinstance(batch, list) else []))
    # Current source partitions take precedence over obsolete merged batches
    # for the review display; formal data is not silently rewritten.
    if isinstance(units, list) and source_group_candidates is not None:
        units = source_group_candidates(folder, units)
    for item in units if isinstance(units, list) else []:
        if str(item.get("id") or item.get("unit_id")) == str(unit_id):
            return item, None
    return {}, None


def source_lines(folder):
    path = folder / "01_source" / "正文.txt"
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8-sig", errors="replace").splitlines()


def source_blocks(lines):
    blocks = []
    table = None
    table_images = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[TABLE"):
            table = []
            table_images = []
            continue
        if stripped.startswith("[/TABLE]"):
            if table is not None:
                # table_images 与 rows 同形：table_images[行][列] = 该单元格的图片名列表。
                blocks.append({"type": "table", "rows": table, "cellImages": table_images})
                table = None
                table_images = []
            continue
        if table is not None:
            # 表格单元格里也可能嵌图片（如 `[IMG image10.jpeg]<br>Weather`）。
            # 图片必须留在它原本所在的单元格内，与同一单元格的文字一起渲染。
            # 因此按单元格切分后，逐单元格收集其图片名。
            cell_images = []
            cells = []
            for raw_cell in line.split("|"):
                names = [Path(m.group(1).strip()).name
                         for m in re.finditer(r"\[IMG\s+([^\]]+)\]", raw_cell)]
                cleaned = re.sub(r"\[IMG\s+[^\]]+\]", "", raw_cell).strip()
                cells.append(cleaned)
                cell_images.append(names)
            if any(cells) or any(cell_images):
                table.append(cells)
                table_images.append(cell_images)
            continue
        cursor = 0
        for match in re.finditer(r"\[IMG\s+([^\]]+)\]", line):
            if match.start() > cursor:
                blocks.append({"type": "text", "text": line[cursor:match.start()]})
            blocks.append({"type": "image", "name": Path(match.group(1).strip()).name})
            cursor = match.end()
        if cursor < len(line):
            text = line[cursor:].strip()
            if text:
                blocks.append({"type": "text", "text": text})
    return blocks


def attach_media(blocks, folder, name):
    media_root = (folder / "01_source" / "media").resolve()

    def media_url(image_name):
        target = (media_root / image_name).resolve()
        ok = target.parent == media_root and target.is_file()
        return f"/api/batches/{name}/media/{image_name}" if ok else None

    for block in blocks:
        if block.get("type") == "image":
            block["url"] = media_url(block["name"])
            continue
        if block.get("type") == "table":
            grid = block.get("cellImages") or []
            block["cellImages"] = [
                [
                    [{"name": image_name, "url": media_url(image_name)} for image_name in cell]
                    for cell in row
                ]
                for row in grid
            ]
    return blocks


AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac")


def audio_map(folder: Path) -> dict:
    """unit_id → 已切好的听力片段；读取 02_audio/audio_map.json，文件存在才纳入。"""
    path = folder / "02_audio" / "audio_map.json"
    mapping = {}
    from audio.consistency import pair_is_consistent, read_pair
    pair = read_pair(folder)
    if not pair_is_consistent(folder, pair):
        return mapping
    data = pair[0] or {}
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        unit = str(item.get("unit_id") or "").strip()
        rel = str(item.get("file") or "").strip()
        if not unit or not rel:
            continue
        target = (folder / rel).resolve()
        if target.parent == (folder / "02_audio").resolve() and target.is_file():
            mapping[unit] = {"name": target.name, "file": rel,
                             "start": item.get("start"), "end": item.get("end")}
    return mapping


def audio_blocks(folder: Path, name: str, unit_ids) -> list:
    """把单元的听力片段作为附件块，供题目详情播放与下载。"""
    mapping = audio_map(folder)
    blocks = []
    for uid in unit_ids:
        info = mapping.get(str(uid))
        if not info:
            continue
        block = {"type": "audio", "name": info["name"], "label": "听力音频附件",
                 "url": f"/api/batches/{name}/audio/{info['name']}",
                 "start": info.get("start"), "end": info.get("end")}
        if block not in blocks:
            blocks.append(block)
    return blocks


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
                  or load(INBOX / folder.name / "batch.json", {}) or {})
    # 课时拆分件正文没有卷头：来源仍要按证据还原，最后一档才是原卷文件名。
    resolved = paper_meta({
        "paper_title": known(batch.get("source"), meta.get("source"), meta.get("title"),
                             meta.get("paper_title"), detected.get("source"), descriptor.get("title")),
        "source": known(batch.get("source"), meta.get("source"), detected.get("source")),
        "source_type": known(batch.get("source_type"), meta.get("source_type"), detected.get("source_type")),
    }, folder)
    return {"title": str(resolved.get("paper_title") or MISSING),
            "batch": str(batch.get("batch") or folder.name),
            "source": str(resolved.get("source") or MISSING),
            "source_type": str(resolved.get("source_type") or MISSING),
            "folder": folder.name, "year": batch.get("year") or meta.get("year") or detected.get("year"),
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


_advance_state: dict[str, dict] = {}

# 用户要求：多份试卷上传后必须“依次排队”跑，同一时刻只允许一个批次在推进，
# 第一份跑完才轮到第二份，绝不并行。这里用一个进程级 FIFO 队列 + 单一调度线程
# 实现全局串行；_advance_state[name] 记录 running/queued 状态供前端展示。
_advance_queue: list[str] = []
_advance_queue_lock = threading.Lock()
_advance_worker_running = False


def _queue_batch_order() -> list[str]:
    """Batches still in 排队区 (not uploaded, not flow_done), earliest first.

    Serial execution must follow this order: a later batch may not start until
    every earlier queued batch is fully complete and has left the queue. Without
    this, a batch that stalls on a blocker still lets the next one run.
    """
    order = []
    if WORKFLOWS.is_symlink() or not WORKFLOWS.is_dir():
        return order
    for folder in WORKFLOWS.iterdir():
        try:
            if folder.is_symlink() or not folder.is_dir() or folder.name.startswith('.'):
                continue
            manifest_path = folder / 'workflow' / 'manifest.json'
            if manifest_path.is_symlink() or not manifest_path.is_file():
                continue
        except OSError:
            continue
        manifest = load(manifest_path, {}) or {}
        if not isinstance(manifest, dict):
            continue
        upload = (manifest.get('stages') or {}).get('upload') or {}
        receipt = load(folder / 'receipt.json', {}) or {}
        pending = bool(isinstance(receipt, dict) and (receipt.get('remote_confirmation_required')
                       or receipt.get('stage') == 'pending_confirmation'))
        if str(upload.get('status')) == 'completed' and not pending:
            continue  # uploaded to cloud → left the queue
        try:
            if wf_execution_state is not None and wf_execution_state.local_complete(folder, manifest):
                continue  # automated flow finished → removed from 排队区
        except (OSError, ValueError, KeyError, TypeError):
            pass
        order.append((str(manifest.get('created_at') or ''), folder.name))
    order.sort()
    return [name for _, name in order]


def _queue_head():
    order = _queue_batch_order()
    return order[0] if order else None


def _waiting_for_predecessor(name):
    """Return the blocking head name when `name` must wait, else None."""
    head = _queue_head()
    return head if (head is not None and head != name) else None


def finding_current(finding: dict, labels: dict, subs: list[dict], numbers: list) -> str:
    """把这条意见指向的字段当前值捞出来，让人不用翻页也知道在说什么。"""
    key = str(finding.get("field") or "").lower()
    number = as_number(finding.get("unit_id"))
    sub = next((s for s in subs if as_number(s.get("number")) == number), None) if number else None
    if len(numbers) > 1 and number and number not in [as_number(n) for n in numbers]:
        sub = None

    def join(value):
        if isinstance(value, (list, tuple)):
            values = [str(v).strip() for v in value if str(v).strip()]
            return "、".join(values) if values else "（空）"
        text = str(value or "").strip()
        return text if text else "（空）"

    if key in ("knowledge", "knowledgeids"):
        return join((sub or {}).get("knowledge") or labels.get("knowledge"))
    if key == "type":
        return join((sub or {}).get("type") or labels.get("type"))
    if key in ("subtype", "subtypes"):
        return join(labels.get("subtypes") or (sub or {}).get("subtype"))
    if key in ("difficulty", "difficultyvalue"):
        return join((sub or {}).get("difficulty") or labels.get("difficulty"))
    if key in ("categories", "category"):
        return join(labels.get("categories"))
    if key == "source_type":
        return join(labels.get("source_type"))
    if key == "source":
        return join(labels.get("source"))
    if key == "year":
        return join(labels.get("year"))
    if key in ("location", "region"):
        return join(labels.get("location"))
    if key in ("stem", "options", "answer"):
        text = join((sub or {}).get(key) or ("" if sub else (labels.get(key) or "")))
        return text[:160] + ("…" if len(text) > 160 else "")
    return "—"


def sync_review_decisions(folder: Path, queue=None):
    """Apply attributed queue decisions to the same finding in every gate file."""
    if queue is None:
        queue = load(folder / "07_review/manual-review-queue.json", {"items": []})
    decisions = {x.get("finding_id"): x for x in queue.get("items", [])
                 if x.get("finding_id") and x.get("decided_at") and x.get("decided_by")}
    changed = 0
    for name in ("content-review.json", "metadata-review.json"):
        path = folder / "07_review" / name
        payload = load(path, {"findings": []})
        dirty = False
        for finding in payload.get("findings", []):
            decision = decisions.get(finding.get("finding_id"))
            if not decision:
                continue
            for key in ("status", "note", "resolution_note", "decided_by", "decided_at"):
                if key in decision and finding.get(key) != decision[key]:
                    finding[key] = decision[key]
                    dirty = True
            # The queue may contain an explicitly translated explanation;
            # preserve the old source wording for traceability.
            if decision.get("reason") and finding.get("reason") != decision["reason"]:
                finding.setdefault("original_reason", finding.get("reason", ""))
                finding["reason"] = decision["reason"]
                dirty = True
        if dirty:
            save_json(path, payload)
            changed += 1
    return changed


@serialized
def apply_decision(folder: Path, item: dict, actor: str | None = None) -> dict:
    """写一条处理意见。只允许改状态/备注，原问题的字段一律保留。"""
    if not item.get("finding_id") or item.get("status") not in ("accepted", "rejected", "fixed", "hold"):
        raise ValueError("决策需要 finding_id 和合法 status")
    path = folder / "07_review" / "manual-review-queue.json"
    queue = load(path, {"queue_version": "1.0", "items": []})
    queue.setdefault("items", [])
    target = next((x for x in queue["items"] if x.get("finding_id") == item["finding_id"]), None)
    if target is None:
        # Older versions could drop queue rows while leaving gate findings.
        # Recover the actual record by ID so it can receive a logged decision.
        for name in ("content-review.json", "metadata-review.json"):
            records = load(folder / "07_review" / name, {"findings": []})
            original = next((x for x in records.get("findings", [])
                             if x.get("finding_id") == item["finding_id"]), None)
            if original:
                target = dict(original)
                queue["items"].append(target)
                break
    if target is None:
        raise ValueError(f"队列里没有 {item['finding_id']}")
    for key in ("status", "note", "resolution_note"):
        if key in item:
            target[key] = item[key]
    target.update({"decided_by": actor or "workbench", "decided_at": now()})
    decision = dict(target)
    with _lock:
        if path.exists():
            path.with_suffix(".json.bak").write_bytes(path.read_bytes())
        save_json(path, queue)
        sync_review_decisions(folder, queue)
        (folder / "workflow").mkdir(parents=True, exist_ok=True)
        with (folder / "workflow" / "decisions.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(decision, ensure_ascii=False) + "\n")
    if str(target.get("review_type") or "").lower() == "structure" and target.get("status") in ("fixed", "rejected"):
        _resolve_structure_issue(folder, target)
    return target


def _resolve_structure_issue(folder: Path, finding: dict) -> None:
    """人在工作台确认结构疑点后，按当前原卷/正文哈希绑定放行依据，供结构闸门复核。"""
    structure_path = folder / "01_source" / "structure.json"
    source_path = folder / "01_source" / "正文.txt"
    if not structure_path.is_file() or not source_path.is_file():
        raise ValueError("缺少结构/正文，无法记录结构确认")
    structure = load(structure_path, {}) or {}
    code = str(finding.get("structure_code") or "").strip()
    reason = str(finding.get("reason") or "")
    issues = structure.get("unresolved") or []
    matches = [x for x in issues if isinstance(x, dict) and x.get("code") == code]
    if len(matches) > 1:
        matches = [x for x in matches if str(x.get("message") or "") == reason]
    if not code:
        matches = [x for x in issues if isinstance(x, dict) and reason
                   and reason == str(x.get("message") or "")]
    line = finding.get("source_line")
    if line is None:
        locations = [re.fullmatch(r"正文第(\d+)行", str(ref)) for ref in finding.get("evidence", [])]
        locations = [int(match.group(1)) for match in locations if match]
        if len(set(locations)) == 1:
            line = locations[0]
    if line is not None:
        matches = [x for x in matches if x.get("line") == line]
    target = matches[0] if len(matches) == 1 else None
    if target is None:
        raise ValueError("结构疑点已不存在或定位不唯一，请刷新后重试")
    resolved = finding.get("status") == "fixed"
    target["resolved"] = resolved
    target["resolution"] = str(finding.get("resolution_note") or finding.get("note") or "工作台人工确认").strip()
    target["resolution_evidence"] = {
        "source_sha256": wf_manifest.sha256_file(source_path),
        "original_sha256": (wf_manifest.sha256_file(wf_manifest.load(folder).get("inputs", {}).get("source", {}).get("path") or "")
                            if (folder / "workflow" / "manifest.json").is_file() else ""),
        "evidence": list(finding.get("evidence") or []) or [f"工作台确认：{reason[:80]}"],
        "decided_by": finding.get("decided_by") or "workbench",
    }
    if structure_path.exists():
        structure_path.with_suffix(".json.bak").write_bytes(structure_path.read_bytes())
    save_json(structure_path, structure)


def _run_advance_now(name: str, folder: Path, *, operations_expectation=None) -> dict:
    """Execute one batch's advance pass synchronously (called only by the serial worker)."""
    from workflow import supervisor as wf_supervisor
    result = {"error": "后台执行器意外退出，等待监督器重试"}
    claim = wf_supervisor.advance_claim(folder)
    if not claim.__enter__():
        claim.__exit__(None, None, None)
        with _lock:
            _advance_state[name] = {"running": False, "external": True, "finished_at": now()}
        return {"started": False, "running": True, "external": True}
    if operations_expectation is not None:
        from workflow import operations
        current = operations._load(folder)
        if (operations_expectation.get("identity") != operations._identity(folder)
                or operations_expectation.get("incident_id") != (current.get("incident") or {}).get("incident_id")):
            claim.__exit__(None, None, None)
            with _lock:
                _advance_state[name] = {"running": False, "result": {"error": "stale_operations_identity"}, "finished_at": now()}
            return {"started": False, "error": "stale_operations_identity"}
    try:
        bridge = auto_flow.load_bridge()
        # The claim already owns auto.advance's per-batch lock. Do not acquire
        # it again. Remote upload/publish is never a watchdog task.
        result = auto_flow._advance_locked(folder, bridge, stop_at="upload_gate")
    except Exception as exc:  # noqa: BLE001 - background worker; surfaced to UI
        result = {"error": str(exc)}
    finally:
        try:
            try:
                from workflow import operations
                try:
                    operations.observe_execution(folder)
                except (ValueError, OSError, RuntimeError):
                    pass  # The durable outbox can reconcile on the next read.
                wf_supervisor.record_result(folder, result)
            finally:
                claim.__exit__(None, None, None)
        finally:
            # A failed ledger write must never leave a dead worker marked
            # running forever. A restart can reconstruct from the manifest.
            with _lock:
                _advance_state[name] = {"running": False, "result": result, "finished_at": now()}
    return {"started": True, "running": True}


def _advance_worker_loop():
    """Single global worker: run queued batches strictly one at a time (FIFO).

    One batch must finish before the next starts; never run two batches in
    parallel. When the queue empties the worker exits; the next enqueue restarts
    it. The queue itself survives between passes.
    """
    global _advance_worker_running
    while True:
        # Dequeue, validate and mark running under the mutation lock so purge
        # cannot delete a popped entry before the worker registers ownership.
        with _lock:
            with _advance_queue_lock:
                if not _advance_queue:
                    _advance_worker_running = False
                    return
                entry = _advance_queue.pop(0)
                name = entry["name"] if isinstance(entry, dict) else entry
                expectation = entry.get("operations_expectation") if isinstance(entry, dict) else None
        try:
            folder = batch_dir(name)
        except (ValueError, OSError):
            with _lock:
                _advance_state.pop(name, None)
            continue
        # Even if a later batch was queued, it must not run ahead of an earlier
        # unfinished batch still in 排队区.
        predecessor = _waiting_for_predecessor(name)
        if predecessor is not None:
            with _lock:
                _advance_state[name] = {"running": False, "waiting": True,
                                        "waiting_for": predecessor, "finished_at": now()}
            continue
        with _lock:
            _advance_state[name] = {"running": True, "started_at": now()}
        try:
            if expectation is None:
                _run_advance_now(name, folder)
            else:
                _run_advance_now(name, folder, operations_expectation=expectation)
        except Exception as exc:  # noqa: BLE001 - never kill the worker loop
            with _lock:
                _advance_state[name] = {"running": False, "result": {"error": str(exc)}, "finished_at": now()}


def _ensure_advance_worker():
    global _advance_worker_running
    with _advance_queue_lock:
        if _advance_worker_running:
            return
        _advance_worker_running = True
    threading.Thread(target=_advance_worker_loop, daemon=True, name="workbench-advance-worker").start()


def _operations_leased() -> bool:
    """An in-flight operations turn holds a lease: restarting voids that attempt."""
    try:
        from workflow import operations
        return operations.leased(WORKFLOWS)
    except (ImportError, OSError, ValueError, RuntimeError):
        return False


def work_in_flight() -> bool:
    """是否还有真正在跑的工作；插件重启后端前用它判断能否安全回收进程树。

    只统计“杀掉就会丢失真实工作”的状态：正在执行的 advance、排队中的批次、切段
    任务、上传任务、在途运维租约。上传中断会让远端回执变成 unknown（只能人工
    核实），比阶段重跑代价更高，所以同样不能在被回收时进行。探测不到状态时保守
    返回 True。
    """
    try:
        with _lock:
            if any(isinstance(row, dict) and row.get("running")
                   for row in _advance_state.values()):
                return True
            if any(isinstance(job, dict) and job.get("running")
                   for job in _upload_jobs.values()):
                return True
        with _advance_queue_lock:
            if _advance_queue:
                return True
    except (RuntimeError, TypeError):
        return True
    with _audio_job["lock"]:
        if _audio_job.get("running"):
            return True
    return _operations_leased()


def _launch_advance(name: str, folder: Path, *, prepare=None, operations_expectation=None,
                    reset_budget=False, **details) -> dict:
    """Enqueue one batch; only an explicit user action resets retry accounting."""
    from workflow import supervisor as wf_supervisor
    if operations_expectation is not None:
        from workflow import operations
        if (operations_expectation.get("identity") != operations._identity(folder)
                or operations_expectation.get("incident_id") != (operations._load(folder).get("incident") or {}).get("incident_id")):
            return {"started": False, "error": "stale_operations_identity"}
    # Strict queue serialization: only the earliest unfinished batch may start.
    # A later batch waits until every earlier one is complete and out of 排队区.
    predecessor = _waiting_for_predecessor(name)
    if predecessor is not None:
        with _lock:
            _advance_state[name] = {"running": False, "waiting": True,
                                    "waiting_for": predecessor, "updated_at": now()}
        return {"started": False, "waiting": True, "waiting_for": predecessor,
                "reason": f"需等待前一份试卷「{predecessor}」全部完成并移出排队区后再开始"}
    if (_advance_state.get(name) or {}).get("running"):
        return {"started": False, "running": True}
    with _advance_queue_lock:
        if any((entry.get("name") if isinstance(entry, dict) else entry) == name for entry in _advance_queue):
            return {"started": False, "running": True, "queued": True}
    # Preparation writes must participate in the same lock/lease as execution.
    with wf_supervisor.advance_claim(folder) as acquired:
        if not acquired:
            return {"started": False, "running": True, "external": True}
        if operations_expectation is not None:
            from workflow import operations
            if (operations_expectation.get("identity") != operations._identity(folder)
                    or operations_expectation.get("incident_id") != (operations._load(folder).get("incident") or {}).get("incident_id")):
                return {"started": False, "error": "stale_operations_identity"}
        if reset_budget:
            try:
                wf_supervisor.clear_exhausted(folder)
            except (OSError, ValueError, TypeError):
                pass
        if prepare is not None:
            details.update(prepare())
    with _lock:
        _advance_state[name] = {"running": False, "queued": True, "queued_at": now(), **details}
    with _advance_queue_lock:
        _advance_queue.append({"name": name, "operations_expectation": operations_expectation}
                              if operations_expectation is not None else name)
    _ensure_advance_worker()
    _wake_supervisor()
    return {"started": True, "queued": True, "running": False}


@serialized
def start_advance(name: str, *, reset_budget=True) -> dict:
    """Run one local pass; automatic callers preserve durable retry accounting."""
    if auto_flow is None:
        raise ValueError("流水线模块不可用")
    return _launch_advance(name, batch_dir(name), reset_budget=reset_budget)


def _dispatch_supervised(name):
    # HTTP overview reads may hold this lock while inspecting many media files.
    # The watchdog must remain bounded and never queue behind such a request.
    if not _lock.acquire(blocking=False):
        return {"started": False, "running": False, "busy": True}
    try:
        # The watchdog already decided this batch needs work and committed its
        # retry reservation. Enqueueing must NOT clear that budget (that would
        # reset no_progress_attempts and defeat exhaustion); only an explicit
        # user action may reset it.
        return start_advance(name, reset_budget=False)
    finally:
        _lock.release()


def advance_state(name: str) -> dict:
    state = dict(_advance_state.get(name) or {"running": False})
    try:
        folder = batch_dir(name)
        path = folder / "workflow/supervisor.json"
        ledger = load(path, {}) if not path.is_symlink() and not path.parent.is_symlink() else {}
        if isinstance(ledger, dict) and ledger:
            state["supervisor"] = {key: ledger[key] for key in
                ("status", "next_retry_at", "no_progress_attempts", "escalation", "last_result") if key in ledger}
        path = folder / "workflow/self-heal-status.json"
        healing = load(path, {}) if not path.is_symlink() and not path.parent.is_symlink() else {}
        if isinstance(healing, dict) and healing:
            # Do not surface a stale "refused/failed" heal card after the failing
            # stage has since completed on a later retry — it is not an active 卡点.
            healed_stage = healing.get("stage")
            manifest = load(folder / "workflow/manifest.json", {})
            stage_status = str(((manifest.get("stages") or {}).get(healed_stage) or {}).get("status") or "")
            if stage_status not in ("completed", "approved", "skipped"):
                state["self_heal"] = {key: healing[key] for key in
                    ("status", "stage", "next_retry_at", "attempt", "verified", "action", "reason", "error_code", "has_feedback") if key in healing}
    except ValueError:
        pass
    return state


# 重新标签 / 重新审核：从指定阶段起让其后全部失效，再后台推进到停住。
# - tag    （重新标签）：从「题型与子类 types」起，重跑题型/解析/知识点/组装/体检/审核全链路。
# - review （重新审核）：从「审核 review」起，只重跑两轮审核（一审全量 + 二审抽检）到审核结束。
RERUN_START = {"tag": "types", "review": "review"}
RERUN_LABEL = {"tag": "重新标签", "review": "重新审核"}


@serialized
def start_rerun(name: str, mode: str) -> dict:
    """An explicit user rerun invalidates only after owning the execution lock."""
    if auto_flow is None or wf_manifest is None:
        raise ValueError("流水线模块不可用")
    if mode not in RERUN_START:
        raise ValueError("不支持的重新执行类型")
    folder = batch_dir(name)
    start_stage = RERUN_START[mode]
    prepared = {}

    def prepare():
        manifest = wf_manifest.load(folder)
        changed = wf_manifest.invalidate_downstream(folder, manifest, start_stage, RERUN_LABEL[mode])
        wf_manifest.save(folder, manifest)
        prepared.update(mode=mode, invalidated=changed)
        return prepared

    result = _launch_advance(name, folder, prepare=prepare, reset_budget=True)
    if result.get("started"):
        result.update(prepared, **{"from": start_stage})
    return result


def _slug(text, fallback: str = "新试卷") -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "", str(text or "")).strip(".")
    return cleaned[:40] or fallback


def _unique_batch_name(base: str) -> str:
    name, index = base, 2
    while (WORKFLOWS / name).exists() or (INBOX / name).exists():
        name, index = f"{base}-{index}", index + 1
    return name


def _upload_part_sha(part):
    return hashlib.sha256(part.get("data") or b"").hexdigest() if isinstance(part, dict) else ""


def _batch_already_sent(state):
    """True once a batch reached the cloud upload stage (confirmed or pending)."""
    if not isinstance(state, dict):
        return False
    stages = state.get("stages")
    if not isinstance(stages, dict):
        return False
    upload = stages.get("upload")
    status = str((upload or {}).get("status") or "") if isinstance(upload, dict) else ""
    return status in ("completed", "approved")


def _existing_upload_batch(source, answer, audio, extra_docs, extra_audio):
    """Find an existing batch with the exact same uploaded content.

    A repeated upload must not create ``-2``/``-3`` copies of the same paper;
    that multiplies model runs and review queues. Only an exact content match
    (all meaningful source/answer/audio/extra inputs) reuses an existing batch.
    Different source or attachments still create a new batch.
    """
    if wf_manifest is None or WORKFLOWS.is_symlink() or not WORKFLOWS.is_dir():
        return None
    def signature(part):
        return (_upload_part_sha(part), Path(part["filename"]).suffix.lower()) if part else None

    expected = {
        "source": signature(source),
        "answers": signature(answer),
        "audio": signature(audio),
        "attachment": sorted(signature(item) for item in (extra_docs or [])),
        "audio_extra": sorted(signature(item) for item in (extra_audio or [])),
    }
    for folder in sorted(WORKFLOWS.iterdir()):
        if folder.is_symlink() or not folder.is_dir():
            continue
        folder = folder.resolve()
        manifest_path = folder / "workflow" / "manifest.json"
        if manifest_path.parent.is_symlink() or manifest_path.is_symlink():
            continue
        state = load(manifest_path, {})
        inputs = state.get("inputs") if isinstance(state, dict) else None
        if not isinstance(inputs, dict):
            continue
        # 已经发过（已提交云端，含待人工确认）的试卷不再参与去重比对或复用：
        # 用户明确要求不校验已发试卷，重新上传应当作为新任务出现，而不是复用
        # 一个在列表里被隐藏的批次（否则看起来“批次任务不见了”）。
        if _batch_already_sent(state):
            continue
        source_record = inputs.get("source")
        if not isinstance(source_record, dict) or source_record.get("sha256") != expected["source"][0]:
            continue

        def current_signature(record):
            if record is None:
                return None
            if not isinstance(record, dict) or not isinstance(record.get("path"), str):
                raise ValueError("Invalid upload input")
            path = Path(record["path"])
            if not path.is_absolute():
                path = folder / path
            relative = path.relative_to(folder)
            current = folder
            for part in relative.parts:
                current = current / part
                if part == ".." or current.is_symlink():
                    raise ValueError("Unsafe upload input")
            if not path.is_file():
                raise ValueError("Missing upload input")
            # Check the bytes now, not a potentially stale manifest/cache digest.
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1 << 20), b""):
                    digest.update(chunk)
            if digest.hexdigest() != record.get("sha256"):
                raise ValueError("Changed upload input")
            return digest.hexdigest(), path.suffix.lower()

        try:
            actual = {key: current_signature(inputs.get(key)) for key in ("source", "answers", "audio")}
            actual["attachment"] = sorted(current_signature(item) for key, item in inputs.items()
                if re.fullmatch(r"attachment_\d{3,}", str(key)))
            actual["audio_extra"] = sorted(current_signature(item) for key, item in inputs.items()
                if re.fullmatch(r"audio_\d{3,}", str(key)))
            if actual == expected:
                # Ensure the reused response can load the ledger as well.
                wf_manifest.load(folder)
                return folder
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return None


def _save_part(folder: Path, part: dict, stem: str) -> Path:
    path = folder / f"{stem}{Path(part['filename']).suffix.lower()}"
    path.write_bytes(part["data"])
    return path


@serialized
def handle_upload(fields: dict, files: list[dict]) -> dict:
    """上传试卷 → 建收件箱条目 → 初始化批次 → 后台推进流水线。"""
    if auto_flow is None:
        raise ValueError("流水线模块不可用，无法自动建批次")
    docs = [f for f in files if Path(f["filename"]).suffix.lower() in DOC_SUFFIXES]
    if not docs:
        raise ValueError("请至少上传一个试卷文件（仅支持 .docx 或 .pdf）")
    unsupported = [f["filename"] for f in files
                   if Path(f["filename"]).suffix.lower() not in DOC_SUFFIXES + AUDIO_SUFFIXES]
    if unsupported:
        raise ValueError("不支持的附件：" + "、".join(unsupported) + "；请使用 DOCX、PDF 或音频，未保存任何文件")
    audio_files = [f for f in files if Path(f["filename"]).suffix.lower() in AUDIO_SUFFIXES]
    audio = audio_files[0] if audio_files else None
    extra_audio = audio_files[1:]
    answer_candidates = [f for f in docs if re.search(r"答案|answer|解析", Path(f["filename"]).stem, re.I)]
    answer = answer_candidates[0] if answer_candidates else None
    main_candidates = [f for f in docs if f is not answer]
    # 来源优先级（用户 2026-11-11 确认）：Word 原卷 > 带文字层 PDF > 扫描件。
    # 同一批上传同时含 Word 与 PDF 时，Word 一定是提取主源，PDF 只作版面核对。
    source = (wf_source_policy.select_primary(main_candidates)
              if wf_source_policy is not None else
              next((f for f in main_candidates if Path(f['filename']).suffix.lower() == '.docx'),
                   main_candidates[0] if main_candidates else docs[0]))
    if answer is source:
        answer = None
    if answer is None:
        answer = next((f for f in docs if f is not source
                       and Path(f['filename']).suffix.lower() == Path(source['filename']).suffix.lower()), None)
    year = str(fields.get("year") or "").strip()
    region = str(fields.get("region") or "").strip()
    title = str(fields.get("title") or "").strip()
    subject = str(fields.get("subject") or "english").strip() or "english"
    source_type = str(fields.get("source_type") or "").strip()
    grade = str(fields.get("grade") or "").strip()
    if source_type and source_type not in taxonomy.sources():
        raise ValueError("题源分类不在系统目录中")
    if grade and grade not in ("七年级", "八年级", "九年级"):
        raise ValueError("年级只能选择七年级、八年级或九年级")
    # 同内容重复上传直接复用已有批次，避免生成 -2/-3 副本并重复跑模型。
    existing = _existing_upload_batch(
        source, answer, audio,
        [f for f in docs if f is not source and f is not answer],
        extra_audio)
    if existing is not None:
        state = wf_manifest.load(existing) if wf_manifest else {}
        try:
            existing_uploaded = bool(overview(existing.name).get("uploaded"))
        except (ValueError, OSError, KeyError, TypeError):
            existing_uploaded = str(((state.get("stages") or {}).get("upload") or {}).get("status")) == "completed"
        # A reused batch that is already uploaded is hidden from both the queue
        # and the review list, so a silent reuse looks like the task vanished.
        message = ("该试卷已上传完成，无需重复上传（批次：" + existing.name + "）"
                   if existing_uploaded else
                   "该试卷已存在，已定位到已有任务（批次：" + existing.name + "）")
        return {"name": existing.name, "duplicate": True, "reused": True,
                "uploaded": existing_uploaded, "message": message,
                "inbox": "", "paper": paper_info(existing, {}, state),
                "stage": stage_cards(state), "files": []}
    # 未填写标题时保留用户上传文件的识别线索，不再一律变成「新试卷」。
    # 此名称仅用于展示/定位，正式来源仍由原卷卷头识别，不覆盖原文证据。
    name = _unique_batch_name(_slug(title or Path(source["filename"]).stem or f"{region}{year}"))
    batch_folder = WORKFLOWS / name
    batch_folder.mkdir(parents=True, exist_ok=False)
    folder = batch_folder / "attachments"
    folder.mkdir()
    saved_source = _save_part(folder, source, "source")
    saved_answer = _save_part(folder, answer, "answers") if answer else None
    saved_audio = _save_part(folder, audio, "audio") if audio else None
    saved_extra_audio = [_save_part(folder, item, f"audio_{index:03d}")
                         for index, item in enumerate(extra_audio, 1)]
    extra_docs = [f for f in docs if f is not source and f is not answer]
    saved_extras = [_save_part(folder, item, f"attachment_{index:03d}")
                    for index, item in enumerate(extra_docs, 1)]
    descriptor = {"workflow_id": name, "subject": subject, "title": title or None,
                  "year": int(year) if year.isdigit() else None, "region": region or None,
                  "source_type": source_type or None, "grade": grade or None,
                  "source": saved_source.name, "answers": saved_answer.name if saved_answer else None,
                  "audio": saved_audio.name if saved_audio else None,
                  "extra_audio": [{"path": path.name, "original_filename": Path(item['filename']).name}
                                  for path, item in zip(saved_extra_audio, extra_audio)],
                  "extra_documents": [{"path": path.name, "original_filename": Path(item['filename']).name}
                                      for path, item in zip(saved_extras, extra_docs)],
                  "original_filenames": {"source": Path(source["filename"]).name,
                                         "answers": Path(answer["filename"]).name if answer else None,
                                         "audio": Path(audio["filename"]).name if audio else None}}
    save_json(folder / "batch.json", descriptor)
    candidate = {"name": name, "folder": str(folder), "subject": subject,
                 "title": descriptor["title"], "year": descriptor["year"], "region": descriptor["region"],
                 "source_type": descriptor["source_type"], "grade": descriptor["grade"],
                 "source": str(saved_source), "answers": str(saved_answer) if saved_answer else None,
                 "audio": str(saved_audio) if saved_audio else None,
                 "extra_documents": [str(path) for path in saved_extras],
                 "extra_audio": [str(path) for path in saved_extra_audio]}
    batch_folder = auto_flow.ensure_batch(candidate, WORKFLOWS)
    start_advance(batch_folder.name)
    _wake_supervisor()
    manifest = wf_manifest.load(batch_folder) if wf_manifest else {}
    try:
        inbox_rel = str(folder.relative_to(ROOT))
    except ValueError:
        inbox_rel = str(folder)
    source_notice = (wf_source_policy.upload_notice(source, [f for f in docs if f is not source])
                     if wf_source_policy is not None else "")
    return {"name": batch_folder.name, "inbox": inbox_rel,
            "paper": paper_info(batch_folder, {}, manifest),
            "stage": stage_cards(manifest),
            "source_notice": source_notice,
            "source_kind": wf_source_policy.kind(source) if wf_source_policy is not None else None,
            "files": [path.name for path in (saved_source, saved_answer, saved_audio) if path]
                     + [path.name for path in saved_extras + saved_extra_audio]}


def _archive_preflight(path: Path):
    """归档只移动真实目录；拒绝所有软链接，避免递归读取/恢复越界。"""
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"归档目标不是本地真实目录：{path.name}")
    for current, dirs, files in os.walk(path, followlinks=False):
        if any((Path(current) / name).is_symlink() for name in dirs + files):
            raise ValueError(f"目录含软链接，请先人工检查：{path.name}")


@serialized
def archive_batch(name: str, confirm_name: str) -> dict:
    from workflow import supervisor as wf_supervisor
    folder = batch_dir(name)
    with wf_supervisor._try_file_lock(wf_supervisor._auto_lock_path(folder)) as acquired:
        if not acquired or wf_supervisor.execution_busy(folder, include_auto=False):
            raise ValueError("批次正在运行或执行运维动作，请稍后归档")
        return _archive_batch_locked(name, confirm_name)


def _archive_batch_locked(name: str, confirm_name: str) -> dict:
    """可恢复删除：原子重命名到工作区回收目录，不永久删除任何数据。"""
    if confirm_name != name:
        raise ValueError("确认批次名不匹配，请重新确认目标")
    folder = batch_dir(name)
    _archive_preflight(folder)
    manifest = load(folder / "workflow" / "manifest.json", {}) or {}
    if advance_state(name).get("running") or manifest.get("status") == "running" or any(
            stage.get("status") == "running" for stage in (manifest.get("stages") or {}).values()):
        raise ValueError("批次正在运行，请等待任务结束后再删除")
    # 数据根以 workflows/inbox 的共同父目录为准，而不是代码根 ROOT。
    # 插件版把代码放在 app/、数据放在插件目录（WF_WORKFLOWS_DIR/WF_INBOX_DIR 指过去），
    # 此时 ROOT 是 app/ 而数据根是插件目录，用 ROOT 当边界会把每次删除都误判成越界。
    root = WORKFLOWS.resolve().parent
    if INBOX.is_symlink() or INBOX.resolve().parent != root:
        raise ValueError("批次或收件箱根目录越出工作区")
    inbox = INBOX / name
    targets = [(folder, "workflows")]
    if inbox.is_symlink():
        raise ValueError("收件箱目录不能是软链接")
    if inbox.exists():
        targets.append((inbox.resolve(), "inbox"))
    for target, _ in targets:
        _archive_preflight(target)
    trash = root / ".workbench-trash"
    if trash.is_symlink() or trash.resolve().parent != root:
        raise ValueError("回收目录不能是软链接或越出工作区")
    trash.mkdir(exist_ok=True)
    archive = trash / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex)
    archive.mkdir()
    receipt = {"batch": name, "archived_at": now(), "status": "preparing",
               "original_paths": {kind: str(path.relative_to(root)) for path, kind in targets}}
    save_json(archive / "receipt.json", receipt)
    moved = []
    try:
        for source, kind in targets:
            source.rename(archive / kind)
            moved.append((source, archive / kind))
        receipt["status"] = "archived"
        save_json(archive / "receipt.json", receipt)
    except OSError:
        # 第二个移动失败时尽量回滚，收据仍保留供人工恢复。
        for original, archived in reversed(moved):
            if not original.exists():
                archived.rename(original)
        receipt["status"] = "rolled_back"
        save_json(archive / "receipt.json", receipt)
        raise
    _advance_state.pop(name, None)
    return {"ok": True, "name": name, "archive": str(archive.relative_to(root)),
            "recoverable": True}


_RECYCLE_DIRS = (".workbench-trash", ".trash", "archives", "archive")
# 「继续处理」区删除：请求批次在阶段边界停下后，最多等待多少秒让 worker 释放执行锁。
DELETE_STOP_WAIT_SECONDS = 90.0


def _purge_archive_targets(root: Path, name: str) -> list[Path]:
    """Recognize receipt archives and legacy named/nested workflows/inbox copies.

    Never use a substring match: deleting paper must preserve paper-2. A receipt
    for another batch bounds that container, even if it contains a matching name.
    """
    targets = []

    def visit(directory):
        for entry in sorted(directory.iterdir()):
            if entry.is_symlink():
                if entry.name == name:
                    raise ValueError("归档目录不能是软链接")
                continue
            if not entry.is_dir():
                continue
            receipt_path = entry / "receipt.json"
            if receipt_path.is_symlink():
                raise ValueError("归档回执不能是软链接")
            receipt = load(receipt_path, {})
            identity = receipt.get("batch") if isinstance(receipt, dict) else None
            if identity:
                if identity == name:
                    targets.append(entry)
                continue
            # workflows/inbox directly under a recycle root are aggregate
            # layout containers, even when a batch happens to have that name.
            aggregate = entry.name in ("workflows", "inbox") and directory.name in _RECYCLE_DIRS
            if entry.name == name and not aggregate:
                targets.append(entry)
                continue
            # A legacy archive may keep a batch under a timestamp or workflow ID.
            manifest_path = entry / "workflow" / "manifest.json"
            if not manifest_path.parent.is_symlink() and not manifest_path.is_symlink():
                state = load(manifest_path, {})
                if isinstance(state, dict) and state.get("workflow_id") == name:
                    targets.append(entry)
                    continue
                if manifest_path.is_file():
                    continue  # Do not descend into another batch's own artifacts.
            visit(entry)

    for base in (root, WORKFLOWS, INBOX):
        for directory in _RECYCLE_DIRS:
            trash = base / directory
            if trash.is_symlink():
                raise ValueError("回收目录不能是软链接")
            if trash.is_dir():
                visit(trash)
    return targets


def _purge_audio_targets(name):
    """Return only this batch's status files, never shared audio/model caches."""
    from audio import config as cfgmod, bootstrap as boot
    data_dir = Path(cfgmod.data_dir(cfgmod.load()))
    batch_status = boot.status_path(data_dir, name)
    paths = [batch_status, batch_status.with_suffix(".tmp")]
    global_status = boot.status_path(data_dir)
    global_paths = [global_status, global_status.with_suffix(".tmp")]
    # Reject symlinked status paths before reading their identity.
    for path in paths + global_paths:
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError("听力状态路径不能是软链接")
    for path in global_paths:
        state = load(path, {})
        if isinstance(state, dict) and state.get("batch") == name:
            paths.append(path)
    for path in paths:
        state = load(path, {})
        if isinstance(state, dict) and state.get("running"):
            raise ValueError("批次听力任务正在运行，请等待结束后再删除")
    return [path for path in paths if path.is_file()]


def _clear_purged_batch_state(name):
    _advance_state.pop(name, None)
    _upload_jobs.pop(name, None)
    with _audio_job["lock"]:
        if _audio_job.get("batch") == name:
            audio_lock = _audio_job["lock"]
            _audio_job.clear()
            _audio_job.update(running=False, kind="", batch="", lock=audio_lock)
    with _advance_queue_lock:
        _advance_queue[:] = [entry for entry in _advance_queue
                            if (entry.get("name") if isinstance(entry, dict) else entry) != name]
    # These small caches include question content, not just derived counters.
    # Clear opaque hash-keyed caches too, since they cannot be filtered by path.
    _OVERVIEW_CACHE.clear()
    for module_name, attributes in {
        "workflow.manifest": ("_FILE_DIGEST_CACHE",),
        "workflow.review": ("_LOAD_CACHE", "_DIGEST_CACHE", "_PLAN_CACHE",
                            "_IMMUTABLE_DIGEST_CACHE", "_EVALUATE_CACHE"),
        "workflow.agent_tasks": ("_TASK_TEMPLATE_CACHE",),
    }.items():
        module = sys.modules.get(module_name)
        if module is not None:
            for attribute in attributes:
                cache = getattr(module, attribute, None)
                if cache is not None:
                    cache.clear()


def _cancel_running_batch(name: str, folder: Path, wait_seconds: float = 90.0) -> dict:
    """Request a clean stop of a running/queued advance before deletion.

    Writes the batch-scoped cancel marker (see ``auto.request_cancel``), removes
    the batch from the pending advance queue, and — if this batch owns the audio
    job — stops the audio worker. Then waits a bounded time for the worker to
    release execution at the next stage boundary. It never interrupts a stage
    mid-flight; a stage that does not honor the marker keeps the batch protected
    and the caller gets ``stopped=False`` so nothing is deleted under a live run.
    """
    import time as _time
    from workflow import auto as wf_auto
    try:
        wf_auto.request_cancel(folder)
    except OSError:
        pass
    with _advance_queue_lock:
        _advance_queue[:] = [entry for entry in _advance_queue
                             if (entry.get("name") if isinstance(entry, dict) else entry) != name]
    with _lock:
        state = _advance_state.get(name) or {}
        was_running = bool(state.get("running"))
        was_queued = bool(state.get("queued") or state.get("waiting"))
        # 排队/等待中的批次并未真正执行：移除队列项后直接清状态即可立即删除。
        if was_queued and not was_running:
            _advance_state.pop(name, None)
            return {"stopped": True, "was_running": False, "was_queued": True}
        if not was_running:
            return {"stopped": True, "was_running": False}
    deadline = _time.monotonic() + max(0.0, wait_seconds)
    while _time.monotonic() < deadline:
        with _lock:
            state = _advance_state.get(name) or {}
        if not state.get("running"):
            return {"stopped": True, "was_running": True}
        _time.sleep(0.4)
    with _lock:
        still = _advance_state.get(name) or {}
    return {"stopped": not still.get("running"), "was_running": True}


@serialized
def delete_batch(name: str, confirm_name: str) -> dict:
    """Stop (if running/queued) and permanently delete one batch and all its traces.

    This is the entry used by the 「继续处理」area delete button: unlike a bare
    ``purge`` it first asks the running worker to stop at a clean stage boundary,
    then reuses ``purge_batch`` (which also clears the per-batch caches and shared
    in-memory projections). If the batch cannot be stopped in time we refuse and
    keep the data intact rather than deleting under a live run.
    """
    if confirm_name != name:
        raise ValueError("确认批次名不匹配，请重新确认目标")
    _validate_batch_name(name)
    try:
        folder = batch_dir(name)
    except ValueError:
        # No live directory (archive-only): nothing to stop, let purge handle it.
        return purge_batch(name, confirm_name)
    stop = _cancel_running_batch(name, folder, wait_seconds=DELETE_STOP_WAIT_SECONDS)
    if not stop.get("stopped"):
        raise ValueError("批次正在执行且未能在时限内停到阶段边界；为避免删除运行中的批次，已取消删除。请稍后重试或等待当前阶段结束。")
    return purge_batch(name, confirm_name)


@serialized
def purge_batch(name: str, confirm_name: str) -> dict:
    """Permanently delete one batch and every local trace of it.

    Unlike ``archive_batch`` this is not recoverable: it removes the live
    workflows/inbox directories plus any recycle archives for the same batch,
    then clears in-memory scheduling state. The caller must explicitly confirm
    the exact batch name.
    """
    if confirm_name != name:
        raise ValueError("确认批次名不匹配，请重新确认目标")
    _validate_batch_name(name)
    if WORKFLOWS.is_symlink():
        raise ValueError("批次根目录不能是软链接")
    folder = WORKFLOWS.resolve() / name
    if folder.exists() or folder.is_symlink():
        _archive_preflight(folder)
    root = WORKFLOWS.resolve().parent
    if INBOX.is_symlink() or INBOX.resolve().parent != root or INBOX.resolve() == WORKFLOWS.resolve():
        raise ValueError("收件箱根目录越出工作区")
    if name in _RECYCLE_DIRS:
        raise ValueError("拒绝删除共享回收目录")
    from workflow import supervisor as wf_supervisor
    with wf_supervisor.advance_claim(folder) as acquired:
        if not acquired:
            raise ValueError("批次正在运行或执行运维动作，请稍后删除")
        if ((_advance_state.get(name) or {}).get("running")
                or (_upload_jobs.get(name) or {}).get("running")
                or (_audio_job.get("running") and _audio_job.get("batch") == name)):
            raise ValueError("批次正在运行，请等待任务结束后再删除")
        targets = [path for path in (folder, INBOX / name) if path.exists() or path.is_symlink()]
        targets.extend(_purge_archive_targets(root, name))
        if not targets:
            raise ValueError("批次不存在")
        for target in targets:
            resolved = target.resolve()
            if resolved == root or root not in resolved.parents:
                raise ValueError("拒绝删除工作区之外的路径")
            _archive_preflight(target)
            manifest = load(target / "workflow" / "manifest.json", {}) or {}
            stages = manifest.get("stages") if isinstance(manifest, dict) else None
            stages = stages if isinstance(stages, dict) else {}
            if isinstance(manifest, dict) and (manifest.get("status") == "running" or any(
                    isinstance(stage, dict) and stage.get("status") == "running"
                    for stage in stages.values())):
                raise ValueError("批次正在运行，请等待任务结束后再删除")
        audio_targets = _purge_audio_targets(name)
        # Preflight every target before the first irreversible removal.
        removed = []
        unique = set(targets)
        for target in sorted(unique, key=lambda path: len(path.parts)):
            if any(parent in unique for parent in target.parents):
                continue
            shutil.rmtree(target)
            removed.append(str(target.relative_to(root)))
        for path in audio_targets:
            path.unlink()
        _clear_purged_batch_state(name)
        # Empty recycle shells are not batch data; prune only the known roots.
        for base in (root, WORKFLOWS, INBOX):
            for directory in _RECYCLE_DIRS:
                trash = base / directory
                try:
                    if trash.is_dir() and not trash.is_symlink() and not any(trash.iterdir()):
                        trash.rmdir()
                except OSError:
                    pass
        return {"ok": True, "name": name, "purged": True, "removed": removed}


def parse_multipart(handler) -> tuple[dict, list[dict]]:
    length = int(handler.headers.get("Content-Length", "0"))
    body = handler.rfile.read(length) if length else b""
    content_type = handler.headers.get("Content-Type", "")
    if "multipart/form-data" not in content_type:
        raise ValueError("上传需要 multipart/form-data")
    message = BytesParser(policy=email_policy).parsebytes(
        b"Content-Type: " + content_type.encode("utf-8", "replace") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
    if not message.is_multipart():
        raise ValueError("上传内容无法解析")
    fields, files = {}, []
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition") or ""
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename:
            files.append({"field": name, "filename": Path(filename).name, "data": payload})
        else:
            charset = part.get_content_charset() or "utf-8"
            fields[name] = payload.decode(charset, "replace")
    return fields, files


_REVIEW_SNAPSHOT_CACHE = {}


def _review_inputs_key(folder):
    """Project the evaluator's declared dependencies and retained evidence.

    Hash contents rather than mtime/size: same-length replacements and restored
    timestamps must not leave a page showing the previous review generation.
    """
    import hashlib
    from workflow import review
    paths = {folder / relative for relative in review.ROUND_INPUTS}
    paths.update(folder / relative for relative in (
        "07_review/review-rounds.json", "07_review/manual-review-queue.json",
        "07_review/review-gate.json", "07_review/report.json",
        "07_review/content-review.json", "07_review/metadata-review.json",
        "workflow/manifest.json", "workflow/media-observations.json",
        "02_audio/SKIP_LISTENING", "06_batch/batch.json", "06_batch/batch_meta.json"))
    rule_root = Path(review.__file__).resolve().parents[1]
    paths.update(rule_root / relative for relative in review.ROUND_RULES)
    paths.add(rule_root / "prompts/roles/review.md")
    paths.update(rule_root / "workflow" / name for name in (
        "review.py", "review_consensus.py", "review_rounds.py", "agent_tasks.py"))
    # Verification also consumes frozen bindings, responses and generation state.
    # Candidates are recursive: unit editor drafts live in candidates/units/.
    for directory in (folder / "07_review", folder / "workflow"):
        paths.update(directory.glob("*.json"))
    for relative in ("candidates", "01_source/materials", "02_audio/sources",
                     "workflow/review-transactions", "workflow/review-bindings", "workflow/review-results",
                     "workflow/review-attempts", "workflow/review-partitions",
                     "workflow/review-generations", "workflow/review-history",
                     "workflow/review-reuse"):
        directory = folder / relative
        if directory.is_dir():
            paths.update(p for p in directory.rglob("*")
                         if p.is_file() and p.suffix in (".json", ".txt")
                         and not (relative == "workflow/review-attempts" and p.name == "input.txt"))
    observations = load(folder / "workflow/media-observations.json", {}) or {}
    for item in observations.get("observations", []):
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            target = (folder / item["path"]).resolve()
            if target.is_relative_to(folder.resolve()):
                paths.add(target)
    parts = []
    for path in sorted(paths):
        try:
            parts.append((str(path), hashlib.sha256(path.read_bytes()).hexdigest()))
        except OSError:
            parts.append((str(path), "missing"))
    # An evaluator replacement in tests/hot reload must not reuse old projections.
    parts.append(("evaluator", id(review.evaluate_rounds)))
    return hashlib.sha256(repr(parts).encode("utf-8")).hexdigest()


def review_snapshot(folder):
    """只读投影真实分轮记录；不以旧 report.json 或流程状态冒充审核证据。"""
    key = (str(folder), _review_inputs_key(folder))
    hit = _REVIEW_SNAPSHOT_CACHE.get(key)
    if hit is not None:
        import copy
        return copy.deepcopy(hit)
    value = _review_snapshot_uncached(folder)
    if len(_REVIEW_SNAPSHOT_CACHE) > 64:
        _REVIEW_SNAPSHOT_CACHE.clear()
    _REVIEW_SNAPSHOT_CACHE[key] = value
    import copy
    return copy.deepcopy(value)


def _review_snapshot_uncached(folder):
    from workflow import review
    payload = load(folder / "07_review/review-rounds.json", {}) or {}
    evaluated = review.evaluate_rounds(folder, payload)
    checked = set(evaluated.get("checked_rounds", []))
    records = {r.get("round_id"): r for r in payload.get("rounds", [])
               if isinstance(r, dict) and r.get("round_id") in checked}
    plan = evaluated.get("plan") or {}
    rounds = []
    for rid, name in (("labels", "一审"), ("sample", "二审")):
        checks = records.get(rid, {}).get("checks", [])
        historical = [r for r in payload.get("rounds", [])
                      if isinstance(r, dict) and r.get("round_id") == rid
                      and r.get("execution_id") and isinstance(r.get("checks"), list)
                      and r["checks"]]
        previous_checks = [check for record in historical for check in record["checks"]]
        current = rid in checked
        rounds.append({"round_id": rid, "name": name, "completed": current,
                       "state": "completed" if current else "needs_refresh" if previous_checks else "not_started",
                       "historical_reviewed": len(previous_checks),
                       "reviewed": len(checks),
                       "ok": sum(c.get("verdict") == "ok" for c in checks),
                       "scope": "全量单元" if rid == "labels" else "抽查小题"})
    revision = []
    for rel in ("workflow/manifest.json", "07_review/review-rounds.json",
                "07_review/manual-review-queue.json", "07_review/report.json"):
        try:
            info = (folder / rel).stat()
            revision.append(f"{rel}:{info.st_mtime_ns}:{info.st_size}")
        except OSError:
            revision.append(rel + ":missing")
    queue_open = 0
    for item in (load(folder / "07_review/manual-review-queue.json", {}) or {}).get("items") or []:
        if isinstance(item, dict) and str(item.get("status")) == "open":
            queue_open += 1
    return {"execution_completed": bool(evaluated.get("execution_completed")),
            "quality_passed": bool(evaluated.get("passed")), "rounds": rounds,
            "question_total": len(plan.get("questions", [])),
            # 人工要判断的是活动队列里的全部未裁决差异：历史轮次恢复回来的差异
            # 不在当前两轮 plan 里，只看 evaluated.findings 会少算，卡片数字就对不上。
            "open_findings": queue_open or len(evaluated.get("findings", [])),
            "revision": "|".join(revision)}, records


def _row_subtype(row):
    """行上现有的子类（subtypes 列表优先，其次单值），空串表示没有。"""
    subtypes = row.get("subtypes")
    if isinstance(subtypes, list):
        for item in subtypes:
            text = str(item or "").strip()
            if text:
                return text
    return str(row.get("subtype") or "").strip()


def decorate_review_rows(folder, rows):
    """把真实分轮结果与“跳过听力”口径落到行上；总览与单元详情共用。"""
    try:
        review_status, records = review_snapshot(folder)
    except (ImportError, OSError, ValueError, TypeError, KeyError):
        review_status, records = {}, {}
    skip_listening = (folder / "02_audio" / "SKIP_LISTENING").is_file()
    # 审核页要直接展示目录题型（语音题/单词拼写/阅读理解…），不能让人对着题干猜。
    # 只有题型/子类确实缺失时，才按同步练习册的确定性口径从本行大题标题反查一次；
    # 已判定过的题型（批次 question_type / 人工改过的标签）一律不覆盖。
    if any(not str(row.get("type") or "").strip()
           or (str(row.get("type") or "").strip() and not _row_subtype(row)) for row in rows):
        try:
            from workflow import sync_practice
            text = "\n".join(source_lines(folder))
            if sync_practice.looks_like_sync_practice(text):
                for row in rows:
                    title = str((row.get("source_section") or {}).get("title") or "")
                    hit = sync_practice.classify(title) if title else None
                    if not hit:
                        continue
                    current = str(row.get("type") or "").strip()
                    changed = False
                    if not current and hit.get("type"):
                        row["type"] = hit["type"]
                        row["type_from"] = "section_rule"
                        current = hit["type"]
                        changed = True
                    if current == hit.get("type") and hit.get("subtype") and not _row_subtype(row):
                        row["subtype"] = hit["subtype"]
                        changed = True
                    if changed:
                        row["subtypes"] = [row["subtype"]] if row.get("subtype") else []
        except (ImportError, OSError, ValueError, TypeError, KeyError):
            pass
    for row in rows:
        if not isinstance(row.get("subtypes"), list):
            row["subtypes"] = [row["subtype"]] if row.get("subtype") else []
    for row in rows:
        checks = [c for record in records.values() for c in record.get("checks", [])
                  if str(c.get("unit_id")) == str(row.get("unit_id"))]
        covered = [rid for rid, record in records.items()
                   if any(str(c.get("unit_id")) == str(row.get("unit_id")) for c in record.get("checks", []))]
        row["rounds"] = len(covered)
        row["review_rounds"] = covered
        if checks:
            row["verdict"] = ("error" if any(c.get("verdict") == "error" for c in checks)
                                else "uncertain" if any(c.get("verdict") == "uncertain" for c in checks) else "ok")
            row["state"] = "attention" if row.get("needs_human") or row["verdict"] != "ok" else "ok"
        # 按规则跳过的听力单元不参与语义审核与完整性判定，不能显示成“未核对”或“待审核”。
        if skip_listening and str(row.get("type") or "").startswith("听力"):
            row["excluded"] = True
            row["excluded_reason"] = "按规则跳过听力，不参与审核与完整性判定"
            row["state"] = "excluded"
            row["needs_human"] = False
            row["review_rounds"] = row.get("review_rounds") or []
    return review_status


def final_format_complete(folder, manifest):
    """Use the same real-format completion rule as background supervision."""
    if wf_execution_state is None:
        return False
    return wf_execution_state.local_complete(Path(folder), manifest)


_OVERVIEW_CACHE = {}

# 冷投影一个批次要 0.5-1s（来源正文、审核轮次、逐单元摘要），13 个批次就是
# 8-9s，逼近前端 15s 的请求上限；而列表内容只在批次文件变化时才变。把按内容
# 指纹校验的投影快照存到进程外，重启后首屏直接复用，指纹不符的批次照常重算。
_OVERVIEW_SNAPSHOT = None


def _overview_snapshot_path():
    base = os.environ.get("WF_CACHE_DIR")
    if base:
        root = Path(base)
    else:
        local = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
        root = (Path(local) if local else Path.home() / ".cache") / "md-upload-tiku"
    return root / "overview-snapshot.json"


def _load_overview_snapshot():
    global _OVERVIEW_SNAPSHOT
    if _OVERVIEW_SNAPSHOT is not None:
        return _OVERVIEW_SNAPSHOT
    items = {}
    try:
        raw = json.loads(_overview_snapshot_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = None
    if isinstance(raw, dict) and raw.get("version") == 1 and isinstance(raw.get("batches"), dict):
        items = raw["batches"]
    _OVERVIEW_SNAPSHOT = items
    return _OVERVIEW_SNAPSHOT


def _save_overview_snapshot():
    """Persist only this workflows dir's projections; best effort, never fatal."""
    items = {}
    for (folder, key), value in list(_OVERVIEW_CACHE.items()):
        try:
            path = Path(folder)
            if path.parent != WORKFLOWS:
                continue
        except (OSError, TypeError):
            continue
        items[path.name] = {"folder": str(path), "key": key, "value": value}
    if not items:
        return
    path = _overview_snapshot_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"version": 1, "batches": items}, ensure_ascii=False)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        return
    global _OVERVIEW_SNAPSHOT
    _OVERVIEW_SNAPSHOT = items



def _overview_key(folder):
    """Fingerprint the files overview reads so a cached projection is never stale."""
    import hashlib
    parts = []
    for relative in ("workflow/manifest.json", "07_review/report.json",
                     "07_review/manual-review-queue.json", "07_review/review-rounds.json",
                     "07_review/content-review.json", "07_review/metadata-review.json",
                     "07_review/unchecked.json", "06_batch/batch_meta.json", "06_batch/batch.json",
                     "01_source/structure.confirmed.json",
                     "07_review/upload-format.json", "07_review/review-gate.json",
                     "receipt.json", "07_upload/receipt.json"):
        path = folder / relative
        try:
            stat = path.stat()
            parts.append((relative, stat.st_mtime_ns, stat.st_size))
        except OSError:
            parts.append((relative, "missing"))
    # 投影代码本身变了（例如标签归属、题型反查修正）就必须重算：
    # 落盘快照只按批次文件指纹复用，否则升级后仍读到旧行的差别归属。
    try:
        code = Path(__file__).stat()
        parts.append(("server-code", code.st_mtime_ns, code.st_size))
    except OSError:
        parts.append(("server-code", "unknown"))
    audio = folder / "02_audio" / "SKIP_LISTENING"
    parts.append(("skip_listening", audio.is_file()))
    raw = repr(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def overview(name):
    folder = batch_dir(name)
    key = (str(folder), _overview_key(folder))
    hit = _OVERVIEW_CACHE.get(key)
    if hit is not None:
        return _live_overview(hit, name, folder)
    saved = _load_overview_snapshot().get(name)
    if (isinstance(saved, dict) and saved.get("folder") == str(folder)
            and saved.get("key") == key[1] and isinstance(saved.get("value"), dict)):
        # Same content fingerprint as when the snapshot was written: reuse it.
        _OVERVIEW_CACHE[key] = saved["value"]
        return _live_overview(saved["value"], name, folder)
    value = _overview_uncached(name, folder)
    if len(_OVERVIEW_CACHE) > 64:
        _OVERVIEW_CACHE.clear()
    _OVERVIEW_CACHE[key] = value
    return _live_overview(value, name, folder)


def _live_overview(value, name, folder):
    """Overlay runtime ownership on the cached document projection, without mutation."""
    from workflow import activity
    value = dict(value)
    advance = advance_state(name)
    value["advance"] = advance
    value["upload_status"] = upload_state(name)
    manifest = load(folder / "workflow/manifest.json", {})
    work = activity.read(folder)
    audio = {}
    if ((manifest.get("inputs") or {}).get("audio") or {}).get("path"):
        try:
            if _audio_has_live_worker(name):
                audio = audio_status(name)  # A leftover heartbeat alone is not live work.
        except (OSError, ValueError, TypeError, ImportError):
            audio = {}
    # Always re-read the small outcome reports here. These substeps must not
    # inherit the cached document projection's older report contents.
    stage = (stage_cards(manifest, bool(value.get("rows")), bool(advance.get("running")), value.get("diagnostics"),
                         batch_folder=folder, runtime_activity=work, audio_state=audio)
             if manifest.get("stages") else dict(value.get("stage") or {}))
    stage["items"] = [dict(card) for card in stage.get("items") or []]
    if not manifest.get("stages"):
        from workflow.substeps import project as project_substeps
        projected = project_substeps(manifest, folder, work, audio)
        for card in stage["items"]:
            card["substeps"] = projected.get(card.get("id"), [])
    if audio.get("running") and not audio.get("stale"):
        for card in stage["items"]:
            if card.get("id") == "audio":
                card["active"] = True
    if work:
        for card in stage["items"]:
            if card.get("id") == work["stage"]:
                card.update(active=True, activity_detail=work.get("detail", ""))
                stage["current"] = card
                stage["hint"] = work.get("detail") or "工作台正在处理"
        stage["activity"] = work
    stage["running_unknown"] = bool((manifest.get("status") == "running"
                                    or any(c.get("status") == "running" for c in stage["items"]))
                                    and not advance.get("running") and not work)
    value["stage"] = stage
    from datetime import timezone
    value["observed_at"] = datetime.now(timezone.utc).isoformat()
    return value


def _parse_iso(value):
    """Parse a manifest ISO timestamp; return None instead of raising."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


def _flow_timing(manifest):
    """Flow start/finish and elapsed minutes from real stage timestamps.

    The completion time is the moment the automated flow finished (the final
    format check, or the latest finished stage), not the manifest's re-export
    mtime. Elapsed is rounded to whole minutes from the earliest start.
    """
    stages = (manifest or {}).get("stages") or {}
    started, finished = [], []
    for record in stages.values():
        if not isinstance(record, dict):
            continue
        s = _parse_iso(record.get("started_at"))
        f = _parse_iso(record.get("finished_at"))
        if s:
            started.append(s)
        if f:
            finished.append(f)
    if not finished:
        return None, None, None
    finish = _parse_iso((stages.get("format_gate") or {}).get("finished_at")) or max(finished)
    start = min(started) if started else _parse_iso((manifest or {}).get("created_at"))
    minutes = None
    if start is not None and finish is not None and finish >= start:
        minutes = int(round((finish - start).total_seconds() / 60.0))
    return (start.isoformat(timespec="seconds") if start else None,
            finish.isoformat(timespec="seconds"), minutes)


def _overview_uncached(name, folder):
    report, manifest, queue = report_for(folder)
    base = question_rows(folder, report)
    number_map = number_of_unit(base)
    merged = merged_rows(folder, report)
    scoped_rows = any(str(r.get("identity_version") or "") == "scoped-v1" for r in merged)
    rows = [mark_row(row, findings_for(queue, row.get("numbers") or [row.get("number")], number_map,
                                       unit_id=row.get("unit_id"), scoped=scoped_rows))
            for row in merged]
    review_status = decorate_review_rows(folder, rows)
    skip_listening = (folder / "02_audio" / "SKIP_LISTENING").is_file()
    counts = {s: sum(1 for r in rows if r.get("state") == s)
              for s in ("ok", "attention", "blocked", "unverified", "excluded")}
    review_status = dict(review_status, skip_listening=skip_listening,
                         excluded_units=counts.get("excluded", 0)) if review_status else review_status
    open_items = [x for x in (queue.get("items", []) if isinstance(queue, dict) else [])
                  if isinstance(x, dict) and is_open(x)]
    label_open = [x for x in open_items if finding_bucket(x) == "label"]
    format_open = [x for x in open_items if finding_bucket(x) == "format"]
    try:
        media = media_audit.audit(folder, [n for row in rows for n in (row.get("numbers") or [])])
        media_summary = media["summary"]
    except (OSError, ValueError, KeyError):
        media_summary = {"total": 0, "suspicious": 0, "question": 0, "unused": 0,
                         "header": 0, "banner": 0, "excluded": 0}
    stages = [{"id": k, "status": v.get("status"), "notes": v.get("notes", ""), "kind": v.get("kind")}
              for k, v in (manifest.get("stages") or {}).items()]
    audio_input = (manifest.get("inputs") or {}).get("audio") or {}
    try:
        has_audio = bool(audio_input.get("path") and Path(str(audio_input["path"])).expanduser().is_file())
    except (OSError, ValueError, TypeError):
        has_audio = False
    batch_findings = [x for x in open_items if not str(x.get("unit_id") or "").strip()]
    advance = advance_state(name)
    # 统一卡点诊断（只读视图）：把各阶段真实原因+修复命令汇到一处。
    diagnostics = None
    try:
        from workflow import diagnose as _diagnose
        diagnostics = _diagnose.diagnose(folder)
    except (OSError, ValueError, KeyError, ImportError):
        diagnostics = None
    # 内容审核之后仍须完成最终格式检查。不能在二审结束时提前移出上传队列。
    flow_done = final_format_complete(folder, manifest)
    upload = upload_state(name)
    upload_stage_done = str(((manifest.get("stages") or {}).get("upload") or {}).get("status") or "") == "completed"
    flow_done = flow_done or upload_stage_done  # 已提交远端的历史批次不因新增本地步骤重新排队。
    receipt = upload.get("receipt") or load(folder / "receipt.json", {})
    upload_pending = bool(isinstance(receipt, dict) and (receipt.get("remote_confirmation_required")
                          or receipt.get("stage") == "pending_confirmation"))
    uploaded = bool(upload_stage_done and not upload_pending)
    stage_info = stage_cards(manifest, bool(rows), bool(advance.get("running")), diagnostics)
    flow_started_at, flow_finished_at, flow_minutes = _flow_timing(manifest)
    # 审核已真实执行、只剩人工决定时，卡点提示要指向“去处理差异”，不能写成“等待执行”。
    if (stage_info.get("current") or {}).get("id") == "review_gate" and label_open:
        stage_info["hint"] = f"审核已执行；{len(label_open)} 项标签差异待人工决定"
    return {"name": name, "meta": report.get("meta", manifest.get("meta", {})),
            "status": manifest.get("status", report.get("status", "unknown")),
            "paper": paper_info(folder, report, manifest),
            "stage": stage_info,
            "diagnostics": diagnostics,
            "advance": advance,
            "counts": counts, "total": len(rows), "unit_total": len(rows),
            "question_total": sum(len(row.get("numbers") or [row.get("number")]) for row in rows),
            "review_status": review_status, "review_revision": review_status.get("revision", ""),
            "open_findings": len(label_open), "format_findings": len(format_open),
            "batch_findings": batch_findings,
            "needs_human": sum(1 for row in rows if row["needs_human"]),
            "format_units": sum(1 for row in rows if row.get("format_count")),
            "media": media_summary,
            "extraction": load(folder / "07_review" / "extraction-report.json", {}),
            "structure_gate": load(folder / "07_review" / "structure-gate.json", {}),
            "stages": stages, "rows": rows, "has_audio": has_audio,
            # 自动流程是否结束（审核后最终格式检查通过）；上传页排队区按此移除。
            "flow_done": flow_done,
            # 流程开始/完成绝对时间与总耗时（分钟）；审核页卡片展示完成时间与耗时。
            "flow_started_at": flow_started_at, "flow_finished_at": flow_finished_at,
            "flow_minutes": flow_minutes,
            # 已写入云端上传暂存的批次视为完成，试卷审核列表不再展示。
            "uploaded": uploaded, "upload_pending_confirmation": upload_pending,
            "upload_status": upload}


def list_batches():
    result = []
    if WORKFLOWS.is_dir():
        for folder in sorted(WORKFLOWS.iterdir()):
            if folder.is_dir() and (folder / "workflow" / "manifest.json").is_file():
                try:
                    result.append(overview(folder.name))
                except ValueError:
                    pass
    _save_overview_snapshot()
    return result


def warm_batch_list():
    """Warm the first page load in the background.

    A cold projection of every batch costs several seconds (source text + review
    rounds + digests per batch). Building it once at startup keeps the review
    page's first list request well inside the client's 15s request budget, and
    later polls hit the per-batch cache. Read-only; any failure just leaves the
    cache cold.
    """
    try:
        cached_list_batches()
    except (OSError, ValueError, TypeError, KeyError, ImportError):
        pass


def operations_work_state():
    """Cheap read-only admission: current local work or a live incident.

    Do not run incident discovery (which can write ledgers) during this hint.
    Terminal history and stopped batches must not keep idle polling active.
    """
    from workflow import operations
    active = []
    if WORKFLOWS.is_symlink() or not WORKFLOWS.is_dir():
        return {"has_work": False, "batches": []}
    for folder in WORKFLOWS.iterdir():
        if (folder.is_symlink() or not folder.is_dir()
                or not (folder / "workflow/manifest.json").is_file()):
            continue
        try:
            manifest = wf_manifest.load(folder) if wf_manifest else {}
            if manifest.get('status') in {'stopped', 'cancelled', 'archived', 'published', 'staged'}:
                continue
            stages = manifest.get('stages') or {}
            local_pending = bool(wf_execution_state) and any(
                not wf_execution_state.executed(stages.get(sid), allow_unqualified=True)
                for sid in wf_execution_state.LOCAL_STAGES)
            # Only hash the final artifact when the local records appear done.
            if not local_pending and wf_execution_state:
                local_pending = not wf_execution_state.local_complete(folder, manifest)
            if local_pending or operations.pending_incident(folder):
                active.append(folder.name)
        except (OSError, ValueError, TypeError):
            continue
    return {"has_work": bool(active), "batches": active[:40]}


def cached_list_batches():
    """Coalesce overlapping HTTP list reads; writes invalidate immediately.

    Older loaded hosts use this endpoint as a readiness probe. A short snapshot
    prevents those parallel probes from repeating all media audits per image.
    The uncached list_batches remains available to internal workflow callers.
    """
    global _batches_snapshot
    # Coalesce list readers without holding the scheduler/mutation lock during
    # full question/media projection. HTTP polling must not starve dispatch.
    with _batches_read_lock:
        with _lock:
            key = (str(WORKFLOWS), str(INBOX))
            if (_batches_snapshot is not None and _batches_snapshot[0] == key
                    and monotonic() < _batches_snapshot[1]):
                return _batches_snapshot[2]
            epoch = _batches_epoch
        result = list_batches()
        with _lock:
            if epoch == _batches_epoch and key == (str(WORKFLOWS), str(INBOX)):
                _batches_snapshot = (key, monotonic() + 2.0, result)
        return result


# ---------------------------------------------------------------------------
# 批次上传：把本批题目写入云端题库的“上传暂存”。
#
# 这在设计上仍是一个显式人工动作：面板点「上传批次」即代表本次上传获得批准，
# 但内部完整走三步（步骤与 CLI 一致，闸门由 workflow.runner 自己校验，不绕过）：
#   1) prepare-upload  读取远端目录、校验并冻结上传请求
#   2) mark upload_gate approved  绑定冻结文件与目标（actor = 本机操作员）
#   3) upload --yes    真正写入上传暂存
# ---------------------------------------------------------------------------
# 英语听力切段（可选能力）：适配器 + 懒下载。
# 不点击「安装」不会下载任何依赖；安装与切段都放后台线程，不占用服务锁。
# 详见 docs/audio.md。
# ---------------------------------------------------------------------------
def _audio_modules():
    from audio import bootstrap as audio_bootstrap
    from audio import config as audio_config
    from audio import segment as audio_segment
    return audio_config, audio_bootstrap, audio_segment


def audio_overview():
    cfgmod, boot, _ = _audio_modules()
    cfg = cfgmod.load()
    data_dir = Path(cfgmod.data_dir(cfg))
    return {"ok": True, "config": cfg, "config_path": str(cfgmod.user_config_path()),
            "defaults_path": str(cfgmod.config_path()),
            "data_dir": str(data_dir), "venv_python": str(boot.venv_python(cfg)),
            "hf_endpoint": cfgmod.resolve_hf_endpoint(cfg),
            "python_version": sys.version.split()[0],
            "platform_key": boot.platform_key(),
            "vendor_python": bool(boot.vendor_python(cfg)),
            "vendor_ffmpeg": bool((boot.VENDOR_DIR / "ffmpeg" / boot.platform_key() / "bin").is_dir()),
            "ready": boot.ready(cfg),
            "ffmpeg": boot.tool_path("ffmpeg"), "ffprobe": boot.tool_path("ffprobe"),
            "busy": _audio_job["running"], "job": _audio_job["kind"]}


def audio_status(batch: str | None = None):
    cfgmod, boot, _ = _audio_modules()
    cfg = cfgmod.load()
    state = boot.read_status(Path(cfgmod.data_dir(cfg)), batch)
    # 不覆盖 state["ok"]：它是后台任务自己的成功/失败标记。
    state.update({"request_ok": True, "ready": boot.ready(cfg), "busy": _audio_job["running"],
                  "job": _audio_job["kind"]})
    if batch:
        state["batch"] = batch
        if state.get("running") and time() - float(state.get("updated") or 0) > _AUDIO_STALE_GRACE \
                and not _audio_has_live_worker(batch):
            # 状态文件残留（进程已被杀、后端重启过）：界面不能再显示“正在运行”，
            # 否则用户会一直等一个不存在的任务。下次点切段/自动推进时会真正重跑。
            state["stale"] = True
            state["running"] = False
            state["detail"] = "切段进程已中断（状态为残留），可重新开始"
    return state


# 后台听力任务稳定性的时间参数（秒）：心跳在 progress 里刷新。
_AUDIO_STALE_GRACE = 5             # 状态刚写入时的宽限，避免误报残留
_AUDIO_WATCHDOG_SECONDS = 900       # 看门狗：>15min 无心跳则把任务判失败并清理，避免永久卡住
_AUDIO_WATCHDOG_TICK = 30


def _audio_batch_folder(batch: str | None) -> Path | None:
    """把批次名解析成在 workflows 下的真实目录；越界或不存在时返回 None。"""
    if not batch:
        return None
    try:
        root = WORKFLOWS.resolve()
        folder = (WORKFLOWS / batch).resolve()
    except OSError:
        return None
    if folder.parent != root or not folder.is_dir():
        return None
    return folder


def _audio_has_live_worker(batch: str | None) -> bool:
    """切段任务是否真的还活着（用于区分“真在跑”与“进程已死的残留状态”）。

    状态文件会活过后端重启：进程被强杀时线程的 finally 不会执行，running=True
    会一直留着。因此不能用状态文件当存活证据，也不能依赖 `ps`（Windows 没有，
    旧实现在 Windows 上恒返回 True，导致僵死状态永远清不掉）。权威证据只有两个：
    本进程的后台任务仍在跑，或本批次的 02_audio/.segment.lock 仍被某个进程持有。
    """
    if _audio_job["running"] and (not batch or _audio_job.get("batch") == batch):
        return True
    folder = _audio_batch_folder(batch)
    if folder is None:
        return False
    try:
        from audio.segment import lock_is_held
    except (ImportError, OSError):
        return True  # 探针不可用时保守等待，不误判重开
    try:
        return lock_is_held(folder)
    except OSError:
        return True


def _audio_reap_stale(kind: str, batch, data_dir: Path, boot) -> None:
    """清理“状态 running 但进程已死”的僵死任务，避免永久卡住无法重开。

    只要有存活证据（本进程任务或批次锁）就不动；没有就立即重置，不再等 10 分钟
    心跳——残留状态本身不是运行证据。
    """
    state = boot.read_status(data_dir, batch)
    if not state.get("running"):
        return
    if _audio_has_live_worker(batch):
        return  # 有真实进程在跑，交给看门狗，不抢占
    boot.write_status(data_dir, batch=batch, job=state.get("job") or kind, running=False,
                      ok=False, phase="error",
                      detail="", error="切段任务已中断（无存活进程，状态文件为残留），已自动重置，可重跑")


@serialized
def start_audio_job(kind: str, options: dict) -> dict:
    cfgmod, boot, segment_mod = _audio_modules()
    cfg = cfgmod.load()
    data_dir = Path(cfgmod.data_dir(cfg))
    batch = Path(options["folder"]).name if kind == "segment" and options.get("folder") else None
    if batch:
        _audio_reap_stale(kind, batch, data_dir, boot)
        if _audio_has_live_worker(batch):
            raise ValueError("该批次已有切段任务在运行，请等它结束")
    with _audio_job["lock"]:
        if _audio_job["running"]:
            raise ValueError("已有听力任务在运行，请等它结束后再试")
        _audio_job.update({"running": True, "kind": kind, "batch": batch})

    # 每个任务独立的“最后心跳”标记，供看门狗判断是否卡死。
    beat = {"at": time()}

    def worker():
        watchdog_stop = threading.Event()

        def watchdog():
            while not watchdog_stop.wait(_AUDIO_WATCHDOG_TICK):
                if time() - beat["at"] > _AUDIO_WATCHDOG_SECONDS:
                    try:
                        boot.write_status(data_dir, batch=batch, job=kind, running=False, ok=False,
                                          phase="error", detail="",
                                          error=f"切段任务超过 {_AUDIO_WATCHDOG_SECONDS}s 无进展，判定僵死并清理；可重跑")
                    except Exception:  # noqa: BLE001
                        pass
                    return

        threading.Thread(target=watchdog, daemon=True).start()
        try:
            def progress(phase, pct, detail):
                beat["at"] = time()
                boot.write_status(data_dir, batch=batch, job=kind, running=True, ok=None,
                                  phase=phase, pct=pct, detail=detail, error="")
            beat["at"] = time()
            boot.write_status(data_dir, batch=batch, job=kind, running=True, ok=None,
                              phase="start", pct=0, detail="任务开始", error="")
            if kind == "bootstrap":
                boot.bootstrap(cfg, force=bool(options.get("force")))
                boot.write_status(data_dir, batch=batch, job=kind, running=False, ok=True,
                                  phase="done", pct=100, detail="运行时就绪", error="")
            else:
                result = segment_mod.run(Path(options["folder"]), cfg, options.get("audio"), progress)
                pending = result.get("needs_review_units") or []
                passed = result.get("ok") is True and not pending
                boot.write_status(data_dir, batch=batch, job=kind, running=False, ok=passed,
                                  phase="done" if passed else "needs_review", pct=100,
                                  detail=("听力切段已完成" if passed else
                                          "候选切片已保存，边界待核对：" + "、".join(pending[:12])), error="")
        except Exception as exc:  # noqa: BLE001 - 后台任务须把失败写进状态文件
            boot.write_status(data_dir, batch=batch, job=kind, running=False, ok=False,
                              phase="error", detail="", error=str(exc))
        finally:
            watchdog_stop.set()
            with _audio_job["lock"]:
                _audio_job.update({"running": False, "kind": "", "batch": ""})

    threading.Thread(target=worker, daemon=True).start()
    return {"started": True, "kind": kind, "batch": batch}


# 结构闸门/审核闸门未过时 runner 会拒绝，错误原样回显。
#
# 上传可能持续数分钟（网络 + 远端校验），因此放在后台线程执行，
# 不占用服务锁，避免阻塞其他请求。
# ---------------------------------------------------------------------------
_upload_jobs = {}


def _run_runner(args):
    return subprocess.run([sys.executable, "-m", "workflow.runner", *args],
                          cwd=str(ROOT), capture_output=True, text=True, timeout=1800,
                          env=dict(os.environ))


def _prepare_failure_summary(report):
    """Summarize blocking findings without letting advisory warnings bury them."""
    if not isinstance(report, dict):
        return ""
    errors = report.get("errors") or ([report["error"]] if report.get("error") else [])
    if not isinstance(errors, list):
        errors = [errors]
    groups = {}
    for item in errors:
        if isinstance(item, dict):
            message = str(item.get("message") or item.get("code") or "上传预检错误")
            code = str(item.get("code") or "")
            field = str(item.get("field") or "")
            unit = str(item.get("unit_id") or "")
        else:
            message, code, field, unit = str(item), "", "", ""
        key = (code, message, field)
        group = groups.setdefault(key, {"count": 0, "units": []})
        group["count"] += 1
        if unit and unit not in group["units"] and len(group["units"]) < 3:
            group["units"].append(unit)
    lines = [f"上传准备未通过：{len(errors)} 项阻断错误"] if errors else ["上传预检未通过"]
    for (code, message, field), group in groups.items():
        detail = message + (f" [{code}]" if code and code != message else "")
        detail += f"（{group['count']} 项"
        if field:
            detail += f"，字段：{field}"
        if group["units"]:
            detail += "，示例单元：" + "、".join(group["units"])
        lines.append("- " + detail + "）")
    warnings = report.get("warnings")
    content = report.get("content_source")
    content_warnings = content.get("warnings") if isinstance(content, dict) else None
    count = (len(warnings) if isinstance(warnings, list) else 0) + (len(content_warnings) if isinstance(content_warnings, list) else 0)
    if count:
        lines.append(f"另有 {count} 项非阻断警告。")
    return "\n".join(lines)


def _check_upload_preconditions(folder, unreviewed_revision, attempt_token):
    """Check existing evidence before any remote media is written."""
    from workflow import contracts, manifest, runner
    from workflow.label_audit import inspect_file
    state = manifest.load(folder)
    summary = runner.unreviewed_summary(folder, state)
    # 用户明确要求：未完成审核 / 有审核差异 都不阻碍上传到题库服务器。
    # 只在“已带确认令牌但审核revision变了”时提示重新确认，不做硬拦截。
    if attempt_token and (not unreviewed_revision or summary.get('revision') != unreviewed_revision):
        raise ValueError('审核状态已变化，请重新查看并确认上传')
    # 用户要求：内容过期/变化（is_stale）不阻碍上传——只要 validate 曾通过（completed）
    # 即可上传；内容正确性由用户自己把关。标签/结构等“内容对不对”的硬校验仍在下面执行。
    if state.get('stages', {}).get('validate', {}).get('status') != manifest.COMPLETED:
        raise ValueError('批次校验未通过：请先完成本地体检（validate）')
    labels = inspect_file(folder / '06_batch' / 'batch_meta.json')
    if not labels.get('passed'):
        raise ValueError('批次标签校验未通过，请先检查标签')
    flow = runner.flowchart_checks(folder, state)
    if not flow.get('passed'):
        first = next(iter(flow.get('errors') or []), {})
        raise ValueError('试卷结构校验未通过：' + str(first.get('message') or '请检查原卷'))


def _stage_upload_media(folder):
    """Stage only media referenced by a stable final batch, after the upload click."""
    import hashlib
    from workflow import media_inventory, media_transfer, upload
    from workflow.local_agent import _agent_lock

    source = folder / '06_batch' / 'batch_meta.json'
    if not source.exists():
        return None
    # Serialize with workflow imports. Workbench and independent external edits
    # are detected by the stable reads and digest comparisons below.
    with _agent_lock(1500):
        root_fd = media_transfer._open_root(folder)
        try:
            raw = media_transfer._read_file(root_fd, '06_batch/batch_meta.json')
        finally:
            os.close(root_fd)
        from workflow.agent_protocol import _load
        metadata = _load(raw)
        # 若媒体已按内容寻址部署（07_upload/media-map.json 存在且覆盖批次全部附件
        # 文件名），直接复用该映射，避免重复 SSH 部署；也避免 referenced_media 对
        # 已部署 flat-hash 附件（/source-media/<sha>-…，非批次目录名前缀）误报
        # “Attachment URL is not owned by this batch”。仅当映射完整覆盖时复用。
        existing_map = folder / '07_upload' / 'media-map.json'
        if existing_map.is_file():
            try:
                deployed = json.loads(existing_map.read_text(encoding='utf-8-sig'))
            except (OSError, ValueError):
                deployed = None
            if isinstance(deployed, dict):
                needed = set()
                def _collect(node):
                    if isinstance(node, dict):
                        for a in (node.get('attachments') or []):
                            if isinstance(a, dict) and a.get('name'):
                                needed.add(a['name'])
                        for c in (node.get('subquestions') or []):
                            _collect(c)
                for item in (metadata.get('items') or []):
                    _collect(item)
                if needed and all(
                        isinstance(deployed.get(n), str)
                        and deployed[n].startswith('/source-media/')
                        for n in needed):
                    # 与正常路径保持同构：(mapping, source_hash)。缺少元组会导致
                    # 调用方 `mapping, source_hash = staged` 解包失败。
                    return {n: deployed[n] for n in needed}, hashlib.sha256(raw).hexdigest()
        refs = media_inventory.referenced_media(folder, metadata)
        if not refs:
            return None
        destination = media_transfer._settings()
        if destination[0] != 'ubuntu@101.43.1.57':
            raise ValueError('此 SSH 目标没有经过验证的媒体目录，无法自动暂存媒体')
        batch_id = metadata.get('batch_id') or wf_manifest.load(folder)['workflow_id']
    # Do not hold the UI/workflow locks while SSH is transferring files: status
    # requests must remain responsive. Revalidate both metadata and every file.
    mapping = media_transfer.stage_media(folder, refs,
        static_root='/home/ubuntu/mudu-quiz/static/source-media', batch_id=batch_id,
        settings=destination)
    with _agent_lock(1500):
        if media_inventory.referenced_media(folder, metadata) != refs:
            raise ValueError('批次媒体在暂存期间发生变化，请重新上传')
        root_fd = media_transfer._open_root(folder)
        try:
            if media_transfer._read_file(root_fd, '06_batch/batch_meta.json') != raw:
                raise ValueError('正式批次在媒体暂存期间发生变化，请重新上传')
        finally:
            os.close(root_fd)
        upload._write(upload._path(folder, 'media-map.json'), upload._bytes(mapping))
        return mapping, hashlib.sha256(raw).hexdigest()


def _run_batch_upload(name, actor, unreviewed_revision='', attempt_token=''):
    job = _upload_jobs[name]

    def step(label, args):
        job["step"] = label
        proc = _run_runner(args)
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
        job["output"] = out[-4000:]
        return proc.returncode, out, proc.stdout or ""

    try:
        folder = batch_dir(name)
        job["step"] = "检查连接，必要时启动 SSH 隧道"
        from workbench import ssh_tunnel
        job["output"] = ssh_tunnel.ensure_upload_connection()
        job['step'] = '检查批次与确认状态'
        _check_upload_preconditions(folder, unreviewed_revision, attempt_token)
        job["step"] = "暂存引用的媒体"
        staged = _stage_upload_media(folder)
        if staged:
            from workflow import upload as upload_flow
            mapping, source_hash = staged
            job["step"] = "准备上传（冻结请求）"
            report = upload_flow.prepare(folder, media_map=mapping)
            if report.get('source_sha256') != source_hash:
                raise ValueError('正式批次在媒体暂存后发生变化，请重新上传')
            rc = 0 if report.get('passed') is True else 1
            stdout = json.dumps(report, ensure_ascii=False)
            out = stdout
            job["output"] = out[-4000:]
        else:
            rc, out, stdout = step("准备上传（冻结请求）", ["prepare-upload", "--batch-dir", str(folder)])
        if rc != 0:
            try:
                report = json.loads(stdout)
            except (ValueError, TypeError):
                report = None
            if isinstance(report, dict) and (report.get("passed") is False or report.get("errors")):
                job.update(ok=False, error=_prepare_failure_summary(report), output="")
            else:
                job.update(ok=False, error="上传准备未通过：" + (out.splitlines()[-1] if out else str(rc)))
            return
        mark_args = ["mark", "--batch-dir", str(folder), "--stage", "upload_gate",
                     "--status", "approved", "--actor", actor,
                     "--note", "通过插件「上传批次」按钮确认" + ("（含未审核内容）" if attempt_token else "")]
        if attempt_token:
            mark_args += ['--unreviewed-revision', unreviewed_revision, '--unreviewed-attempt', attempt_token]
        rc, out, _ = step("确认上传闸门", mark_args)
        if rc != 0:
            job.update(ok=False, error="上传确认失败：" + (out.splitlines()[-1] if out else str(rc)))
            return
        upload_args = ["upload", "--batch-dir", str(folder), "--yes"]
        if attempt_token:
            upload_args += ['--unreviewed-attempt', attempt_token]
        rc, out, _ = step("写入云端上传暂存", upload_args)
        if rc != 0:
            job.update(ok=False, error="上传未完成：" + (out.splitlines()[-1] if out else str(rc)))
            return
        job.update(ok=True, step="已上传")
    except Exception as exc:  # noqa: BLE001 - 后台任务，错误回显给面板
        job.update(ok=False, error=str(exc))
    finally:
        job["running"] = False
        job["finished_at"] = time()


@serialized
def start_batch_upload(name, actor="本机操作员", *, allow_unreviewed=False, unreviewed_revision=''):
    folder = batch_dir(name)  # 批次不存在直接抛错
    if type(allow_unreviewed) is not bool:
        raise ValueError('allow_unreviewed 必须是布尔值')
    if allow_unreviewed:
        from workflow.runner import unreviewed_summary
        current = unreviewed_summary(folder, wf_manifest.load(folder))
        if not isinstance(unreviewed_revision, str) or not unreviewed_revision or current['revision'] != unreviewed_revision:
            raise ValueError('审核状态已变化，请重新查看并确认')
    elif unreviewed_revision:
        raise ValueError('未明确确认不能指定未审核版本')
    attempt_token = uuid.uuid4().hex if allow_unreviewed else ''
    existing = upload_state(name)
    if existing.get('state') == 'unknown':
        raise ValueError('上次上传结果未知，请先在远端核实，不能重复上传')
    if existing.get('state') == 'accepted':
        raise ValueError('该批次已有上传暂存回执，请勿重复上传')
    with _lock:
        job = _upload_jobs.get(name)
        if job and job.get("running"):
            raise ValueError("该批次正在上传中，请稍候")
        _upload_jobs[name] = {"running": True, "ok": None, "step": "开始", "output": "",
                              "error": "", "started_at": time()}
    threading.Thread(target=_run_batch_upload, args=(name, actor, unreviewed_revision, attempt_token), daemon=True).start()
    return {"started": True}


def upload_state(name):
    """Project in-memory progress and durable upload evidence after a restart."""
    folder = batch_dir(name)
    with _lock:
        job = dict(_upload_jobs.get(name) or {})
    result = {"running": False, "ok": None, "step": "", "output": "", "error": "",
              "state": "idle", "receipt": None}
    result.update(job)
    try:
        from workflow.runner import unreviewed_summary
        result['unreviewed'] = unreviewed_summary(folder, wf_manifest.load(folder))
        if result['unreviewed'].get('revision'):
            result['requires_unreviewed_confirmation'] = not result['unreviewed']['passed']
    except (OSError, ValueError, KeyError, TypeError, ImportError) as exc:
        result['unreviewed'] = {'passed': False, 'revision': '', 'counts': {},
                                'errors': [{'message': str(exc)}]}
    receipt = load(folder / "07_upload" / "receipt.json")
    receipt = receipt if isinstance(receipt, dict) and receipt.get("id") else None
    attempts = []
    for path in (folder / "07_upload" / "attempts").glob("*.json"):
        item = load(path)
        if isinstance(item, dict):
            attempts.append(item)
    if not receipt:
        receipt = next((a.get("receipt") for a in attempts if a.get("state") == "accepted"
                        and isinstance(a.get("receipt"), dict) and a["receipt"].get("id")), None)
    result["receipt"] = receipt
    # A receipt is written before its attempt becomes accepted. It settles that
    # attempt, but cannot settle a different in-flight payload.
    uncertain = next((a for a in attempts if a.get("state") in ("submitting", "unknown")
                      and not (receipt and a.get("payload_sha256")
                               and a.get("payload_sha256") == receipt.get("payload_sha256"))), None)
    rejected = next((a for a in attempts if a.get("state") == "rejected"), None)
    preflight = load(folder / "07_upload" / "preflight.json")
    if result["running"]:
        result["state"] = "running"
    elif uncertain:
        result.update(state="unknown", ok=False)
        result["step"] = result["step"] or "提交结果未知"
        result["error"] = result["error"] or str(uncertain.get("details") or uncertain.get("error_code")
                                                or "提交结果未知，请先在远端工作台核实")
    elif receipt:
        # A later preflight failure cannot erase evidence of an accepted upload.
        # Keep its diagnostic visible without enabling a duplicate submission.
        if receipt.get('state') in ('staged_attachments_pending', 'attachment_rejected'):
            result.update(state="failed", ok=False)
            result["step"] = "审核附件未追加完成"
            error = receipt.get('attachment_error') or {}
            result["error"] = str(error.get('details') or error.get('code')
                                  or "审核附件未追加完成，请重试或检查远端暂存")
        else:
            result.update(state="accepted", ok=True)
            result["step"] = "已暂存，等待远端确认"
    elif job.get("ok") is False:
        result["state"] = "failed"
    elif rejected:
        result.update(state="failed", ok=False)
        result["step"] = result["step"] or "上传被拒绝"
        result["error"] = result["error"] or str(rejected.get("details") or rejected.get("error_code")
                                                or "上传被拒绝")
    elif isinstance(preflight, dict) and preflight.get("passed") is False:
        result.update(state="failed", ok=False)
        result["step"] = result["step"] or "上传准备未通过"
        result["error"] = result["error"] or _prepare_failure_summary(preflight)
    elif job.get("ok") is True:
        result.update(state="unknown", ok=False, error="上传完成但未找到暂存回执，请先在远端核实")
    return result


def unit_label_path(folder: Path, unit_id) -> Path:
    """标签按“单元”存一份：组题整体打标，子题不各存一份。"""
    safe = re.sub(r"[^0-9A-Za-z._-]", "_", str(unit_id or "").strip())
    if not safe:
        raise ValueError("缺少单元 ID")
    return folder / "candidates" / "units" / f"{safe}.json"


def load_unit_labels(folder: Path, unit_id) -> dict:
    try:
        data = load(unit_label_path(folder, unit_id), {})
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _knowledge_projection(path):
    """「二级/末级」短名投影，与 preview 快速核对标签的写法一致。"""
    parts = [p for p in str(path or "").split("/") if p.strip()]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return parts[1]
    return parts[1] + "/" + parts[-1]


def drop_short_knowledge_copies(paths):
    """去掉同一知识点的短名副本，保留目录全路径。

    旧批次/预览行会把 `设题角度/阅读/细节理解` 记成 `阅读/细节理解`、
    把 `主题/人与社会/历史、社会与文化/文化习俗` 记成 `人与社会/文化习俗`
    这类「二级/末级」写法；它与全路径指向同一叶子，混在一起会在修改标签
    弹窗里显示两遍，并让短名被校验成目录外路径。
    """
    unique = []
    for raw in paths or []:
        path = str(raw or "").strip()
        if path and path not in unique:
            unique.append(path)
    return [path for path in unique
            if not any(other != path and (other.endswith("/" + path)
                                          or _knowledge_projection(other) == path)
                       for other in unique)]


def label_payload(row, batch_item, subs, folder, unit_id=None):
    """当前标签值 + 目录校验结果，供人工核对/修改。

    优先级：单元标签草稿（人工改过的）> 批次单元 > 行汇总 > 子题。
    """
    item = batch_item if isinstance(batch_item, dict) else {}
    unit_labels = load_unit_labels(folder, unit_id or row.get("unit_id") or "") if unit_id or row.get("unit_id") else {}
    if unit_labels:
        item = {**item, **unit_labels}
    filters = item.get("filterMetadata") if isinstance(item.get("filterMetadata"), dict) else {}
    knowledge = []
    explicit_knowledge = "knowledge" in unit_labels
    if explicit_knowledge:
        knowledge_sources = [unit_labels["knowledge"]]
    else:
        knowledge_sources = [item.get("knowledge")]
        # 预览行里的知识点是「二级/末级」短名投影（preview 的 knowledge_projection /
        # 重新分组候选的 source_projection），只能用于卷面速览；与批次全路径合并
        # 会让同一条知识点出现两遍，且短名会被判成「目录外路径」。
        if not row.get("source_projection") and not row.get("knowledge_projection"):
            knowledge_sources.append(row.get("knowledge"))
    for source in knowledge_sources:
        for path in source or []:
            if str(path).strip() and str(path).strip() not in knowledge:
                knowledge.append(str(path).strip())
    knowledge = drop_short_knowledge_copies(knowledge)
    if not knowledge and not explicit_knowledge:
        for sub in subs:
            for path in sub.get("knowledge") or []:
                if str(path).strip() and str(path).strip() not in knowledge:
                    knowledge.append(str(path).strip())
    resolved = taxonomy.resolve_knowledge(knowledge)
    # 旧批次可能保存不带顶级维度的叶子路径；展示和维度校验统一使用
    # 唯一解析出的当前目录全路径，避免加载时误报目录外路径。
    if resolved["resolved"] and not resolved["unknown"] and not resolved["non_leaf"]:
        knowledge = list(dict.fromkeys(point["path"] for point in resolved["resolved"]))
        resolved = taxonomy.resolve_knowledge(knowledge)
    from workflow.metadata_policy import MISSING
    values = {
        "source": item.get("source") or filters.get("source") or row.get("source") or MISSING,
        "source_type": item.get("source_type") or filters.get("source_type") or row.get("source_type") or MISSING,
        "year": str(item.get("year") or filters.get("year") or row.get("year") or ""),
        "location": item.get("location") or filters.get("location") or row.get("location") or "",
        "type": unit_labels.get("type") or item.get("question_type") or item.get("type") or row.get("question_type") or row.get("type") or "",
        "subtypes": filters.get("subtypes") or ([item.get("subtype")] if item.get("subtype") else []),
        "difficulty": (taxonomy.difficulty_name(item.get("difficulty"))
                       or taxonomy.difficulty_name(filters.get("difficulty"))
                       or taxonomy.difficulty_name(filters.get("difficultyValue"))
                       or taxonomy.difficulty_name(row.get("difficulty"))),
        "categories": filters.get("categories") or [MISSING],
        "difficultyValue": filters.get("difficultyValue"),
        "knowledge": knowledge,
        "knowledgeIds": [point["id"] for point in resolved["resolved"]],
    }
    warnings = taxonomy.check_labels(values)
    warnings += taxonomy.check_unit_dims(values["type"], knowledge)
    if item.get("source_notice") or row.get("source_notice"):
        warnings.append(item.get("source_notice") or row["source_notice"])
    values["scope"] = "unit"
    values["unit_id"] = str(unit_id or row.get("unit_id") or "")
    values["subquestion_count"] = len([s for s in subs if s.get("stem") or s.get("options")])
    values["knowledge_editable"] = True
    values["stored_in"] = (str(unit_label_path(folder, unit_id or row.get("unit_id")).relative_to(folder))
                           if (unit_id or row.get("unit_id")) else None)
    values["resolvedKnowledge"] = [{"path": p["path"], "id": p["id"], "name": p["name"]}
                                   for p in resolved["resolved"]]
    values["warnings"] = warnings
    return values


def preview_labels(labels: dict, subs: list, batch_item: dict, row: dict) -> dict:
    """原卷预览旁的快速核对标签：来源类型、知识点（二级与末级）、难度、词数。"""
    def _words(text):
        return len(re.findall(r"[A-Za-z][A-Za-z'’\-]*", str(text or "")))
    def _short_knowledge(path):
        # 只显示二级与最后一级（最多两个）：如 主题/人与自然/自然生态/地理环境 → 人与自然/地理环境
        parts = [p for p in str(path or "").split("/") if p.strip()]
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0]
        if len(parts) == 2:
            return parts[1]
        return "/".join([parts[1], parts[-1]])
    parts = [batch_item.get("material") or row.get("material") or batch_item.get("stem") or row.get("stem") or ""]
    for sub in subs or []:
        parts.append(sub.get("stem") or "")
        for opt in sub.get("options") or []:
            parts.append(opt.get("text") if isinstance(opt, dict) else opt)
    words = _words("\n".join(str(p) for p in parts))
    source_type = str(labels.get("source_type") or "").strip()
    knowledge = [_short_knowledge(p) for p in (labels.get("knowledge") or []) if str(p).strip()]
    return {
        "source_type": source_type,
        "type": str(labels.get("type") or "").strip(),
        "subtypes": list(labels.get("subtypes") or []),
        "knowledge": knowledge,
        "difficulty": str(labels.get("difficulty") or "").strip(),
        "word_count": words,
    }


def has_unverified_review_history(folder: Path) -> bool:
    """仅判断目录中是否存在格式像归档的文件，不推断来源或审核范围。"""
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        return False
    opened = []
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        root_fd = os.open(folder, flags)
        opened.append(root_fd)
        workflow_fd = os.open("workflow", flags, dir_fd=root_fd)
        opened.append(workflow_fd)
        history_fd = os.open("review-history", flags, dir_fd=workflow_fd)
        opened.append(history_fd)
        for name in os.listdir(history_fd):
            if not re.fullmatch(r"[0-9a-f]{32}\.json", name):
                continue
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=history_fd)
                with os.fdopen(fd, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size > 32 * 1024 * 1024:
                        continue
                    data = json.load(stream)
                if isinstance(data, dict) and isinstance(data.get("plan"), dict) and isinstance(data.get("rounds"), list):
                    return True
            except (OSError, UnicodeError, ValueError, RecursionError):
                continue
    except OSError:
        return False
    finally:
        for fd in reversed(opened):
            os.close(fd)
    return False


def review_comparison(folder: Path, unit_id, numbers):
    """读取输入绑定有效的两轮判断及其原始标签，保留逐题抽样范围。"""
    uid = str(unit_id or "")
    snapshot = load(folder / "07_review/review-comparison.json", {})
    snapshot_valid = (isinstance(snapshot, dict) and snapshot.get("version") == 1
                      and snapshot.get("execution_completed") is True
                      and isinstance(snapshot.get("plan"), dict)
                      and isinstance(snapshot.get("rounds"), list))
    if snapshot_valid:
        payload = snapshot
        records = snapshot.get("rounds", [])
        checked = {"labels", "sample"}
        plan = snapshot.get("plan") or {}
        snapshot_original = snapshot.get("original") or {}
        source_name = "07_review/review-comparison.json"
        try:
            from workflow import review as _review
            current_plan = _review.prepare_round_plan(folder)
            snapshot_stale = current_plan.get("input_binding") != snapshot.get("input_binding")
        except (ImportError, OSError, ValueError, TypeError, KeyError):
            snapshot_stale = False
    else:
        snapshot_stale = False
        payload = load(folder / "07_review/review-rounds.json", {})
        records = payload.get("rounds", []) if isinstance(payload, dict) else []
        try:
            from workflow import review
            evaluated = review.evaluate_rounds(folder, payload)
            checked = set(evaluated.get("checked_rounds", []))
            plan = evaluated.get("plan") or {}
        except (ImportError, OSError, ValueError, TypeError, KeyError):
            checked, plan = set(), {}
        snapshot_original = {}
        source_name = "07_review/review-rounds.json"

    # Prefer the last successful immutable snapshot. Current review rounds may
    # be stale/in-progress after an input change; that must not blank the UI.
    hashes = plan.get("input_hashes") or {}
    def bound(relative):
        if snapshot_valid:
            key = {"03_types/types.json": "types", "04_knowledge/knowledge.json": "knowledge",
                   "01_source_ans/answers.json": "answers"}.get(relative)
            return snapshot_original.get(key, {}) if key else {}
        return load(folder / relative, {}) if checked and relative in hashes else {}
    def unique_row(data):
        rows = data.get("items", []) if isinstance(data, dict) else []
        matches = [row for row in rows if isinstance(row, dict) and str(row.get("unit_id") or "") == uid]
        return matches[0] if len(matches) == 1 else {}
    types = unique_row(bound("03_types/types.json"))
    knowledge = unique_row(bound("04_knowledge/knowledge.json"))
    answers = bound("01_source_ans/answers.json")
    labels = {key: row[key] for key, row in (("type", types), ("subtypes", types), ("knowledge", knowledge)) if key in row}
    children = {}
    for child in knowledge.get("subquestions", []) if isinstance(knowledge.get("subquestions"), list) else []:
        if isinstance(child, dict) and str(child.get("number", "")).isdigit():
            children.setdefault(str(int(child["number"])), []).append(child)
    originals = {n: {"child_knowledge": rows[0]["knowledge"]} for n, rows in children.items()
                 if len(rows) == 1 and "knowledge" in rows[0]}
    difficulty = answers.get("difficulty", {}) if isinstance(answers, dict) else {}
    if isinstance(difficulty, dict):
        for n in numbers:
            if str(n).isdigit() and str(int(n)) in difficulty:
                originals.setdefault(str(int(n)), {})["difficulty"] = difficulty[str(int(n))]
    result = {"original": {"source": "审核输入" if checked else None, "unit_id": uid,
                           "labels": labels, "subquestions": originals}, "rounds": [],
              "stale": bool(snapshot_stale),
              "snapshot_source": source_name}
    wanted = {str(int(n)) for n in numbers if str(n).isdigit()}
    history_exists = has_unverified_review_history(folder)
    for rid, name in (("labels", "一审"), ("sample", "二审")):
        matches = [r for r in records if isinstance(r, dict) and r.get("round_id") == rid] if isinstance(records, list) else []
        entry = {"round_id": rid, "name": name,
                 "status": ("上一版审核快照" if snapshot_stale else
                            ("待重新审核" if matches else ("历史来源未验证" if history_exists else "未提供"))),
                 "checks": []}
        if rid in checked and len(matches) == 1:
            record = matches[0]
            for check in record.get("checks", []):
                if not isinstance(check, dict) or str(check.get("unit_id") or "") != uid:
                    continue
                if rid == "sample":
                    raw = check.get("number")
                    if raw is not None and str(raw).strip():
                        # 逐题抽样：只保留抽中的该小题。
                        if not str(raw).isdigit() or str(int(raw)) not in wanted:
                            continue
                    # 整组抽样：number 为空表示整组核对，覆盖本单元全部小题。
                assessment = check.get("assessment") if isinstance(check.get("assessment"), dict) else {}
                entry["checks"].append({"unit_id": uid, "number": check.get("number"),
                                        "scope": ("subquestion" if str(check.get("number") or "").strip()
                                                  else "unit"),
                                        "verdict": check.get("verdict"), "rationale": check.get("rationale"),
                                        "assessment": assessment, "evidence": check.get("evidence"),
                                        "differences": [
                                            {"field": field, "original": labels[field], "reviewed": assessment[field]}
                                            for field in ("type", "subtypes", "knowledge")
                                            if field in labels and field in assessment
                                            and (sorted(set(labels[field])) if isinstance(labels[field], list) else labels[field])
                                            != (sorted(set(assessment[field])) if isinstance(assessment[field], list) else assessment[field])]})
            entry["status"] = "已提供" if entry["checks"] else "未覆盖"
            entry["source"] = source_name
            entry["execution_id"] = record.get("execution_id")
        result["rounds"].append(entry)
    return result


def finding_comparison(finding, comparison, numbers, number_map):
    """以字段和精确题号读取各轮判断，不将抽样结论外推到其他小题。"""
    uid = comparison["original"]["unit_id"]
    target = str(finding.get("unit_id") or "")
    field = str(finding.get("field") or "")
    child_match = re.fullmatch(r"subquestions\[number=(\d+)\]\.(\w+)", field)
    number = str(int(child_match.group(1))) if child_match else None
    key = child_match.group(2) if child_match else field
    key = {"knowledgeids": "knowledge", "knowledge_points": "knowledge", "subtype": "subtypes"}.get(key.lower(), key)
    if child_match and key == "knowledge":
        key = "child_knowledge"
    if target != uid:
        mapped = str(number_map.get(target) or "")
        if not mapped.isdigit() or sum(str(v) == mapped for v in number_map.values()) != 1:
            return None
        number = str(int(mapped))
    if key in ("difficulty", "child_knowledge") and number is None:
        matched = re.search(r"第\s*(\d{1,3})\s*题", str(finding.get("reason") or finding.get("question") or ""))
        number = str(int(matched.group(1))) if matched else None
    if key == "sample" and number is None:
        matched = re.search(r"第\s*(\d{1,3})\s*题", str(finding.get("reason") or finding.get("question") or ""))
        number = str(int(matched.group(1))) if matched else None
    wanted = {str(int(n)) for n in numbers if str(n).isdigit()}
    if number and number not in wanted:
        return None
    if key in ("difficulty", "child_knowledge") and number is None:
        return None
    original = comparison["original"]["subquestions"].get(number, {}) if number and key != "sample" else comparison["original"]["labels"]
    out = {"scope": "subquestion" if number else "unit", "number": number, "field": key,
           "original": original.get(key), "original_provided": key in original,
           "source": comparison["original"]["source"], "rounds": []}
    for round_entry in comparison["rounds"]:
        item = {k: v for k, v in round_entry.items() if k != "checks"}
        checks = []
        for check in round_entry["checks"]:
            raw_number = str(check.get("number") or "").strip()
            if round_entry["round_id"] == "sample" and number and raw_number and raw_number != number:
                # 逐题抽样的二审只取抽中的该小题；整组抽样（无题号）覆盖整组。
                continue
            assessment = check.get("assessment") or {}
            if key in ("difficulty", "child_knowledge"):
                collection = assessment.get("difficulty" if key == "difficulty" else "subquestions")
                matching = [v for v in collection if isinstance(v, dict) and str(v.get("number")) == number] if isinstance(collection, list) else []
                value_key = "value" if key == "difficulty" else "knowledge"
                present = len(matching) == 1 and value_key in matching[0]
                value = matching[0].get(value_key) if present else None
            else:
                present = key in assessment
                value = assessment.get(key)
            if not present:
                continue
            selected = dict(check)
            selected["value"] = value
            def comparable(v):
                return sorted(set(v)) if isinstance(v, list) and all(isinstance(x, str) for x in v) else v
            selected["differences"] = ([{"scope": out["scope"], "number": number, "field": key,
                                         "original": out["original"], "reviewed": value}]
                                       if out["original_provided"] and comparable(value) != comparable(out["original"]) else [])
            checks.append(selected)
        item["checks"] = checks
        if item["status"] == "已提供" and not checks:
            target_covered = any(
                not str(c.get("number") or "").strip() or str(c.get("number")) == number
                for c in round_entry["checks"])
            item["status"] = ("未覆盖" if round_entry["round_id"] == "sample" and number
                              and not target_covered else "未涉及")
        out["rounds"].append(item)
    return out


def detail(name, unit_id):
    folder = batch_dir(name)
    report, manifest, queue = report_for(folder)
    base = question_rows(folder, report)
    number_map = number_of_unit(base)
    rows = merged_rows(folder, report)
    confirmed = load(folder / "01_source/structure.confirmed.json", {}) or {}
    scoped_units = [u for u in confirmed.get("units", []) if isinstance(u, dict)
                    and u.get("identity_version") == "scoped-v1"]
    has_scoped = bool(scoped_units) or any(r.get("identity_version") == "scoped-v1" for r in rows)
    row = next((r for r in rows if str(r.get("unit_id")) == str(unit_id)), None)
    if row is None:
        # Previously bookmarked merged group opens its first current partition.
        row = next((r for r in rows if r.get("source_parent_id") == str(unit_id)), None)
    if row is None and not has_scoped:
        alias = re.sub(r"\D", "", str(unit_id))
        if alias:
            row = next((r for r in rows if int(alias) in (r.get("numbers") or [])), None)
    if row is None:
        row = {} if has_scoped else next((r for r in rows if str(r.get("number")) == str(unit_id)), {})
    bound_units = [u for u in scoped_units if str(u.get("unit_id")) == str(row.get("unit_id") or unit_id)]
    bound_unit = bound_units[0] if len(bound_units) == 1 else {}
    scoped = has_scoped or row.get("identity_version") == "scoped-v1" or bool(bound_unit)
    selected_uid = str(row.get("unit_id") or unit_id)
    if scoped:
        row = dict(row)
        for key in ("identity_version", "section_id", "source_section", "question_ids", "printed_numbers"):
            if key in bound_unit:
                row[key] = bound_unit[key]
        printed_numbers = bound_unit.get("printed_numbers", (bound_unit.get("source_span") or {}).get("printed_numbers"))
        if printed_numbers is not None:
            row["printed_numbers"] = printed_numbers
        if row.get("printed_numbers") == []:
            row["label"] = str((row.get("source_section") or {}).get("title") or "无印刷题号")
        # Numeric-only findings have no unique owner in a scoped batch.
        queue = dict(queue, items=[f for f in queue.get("items", [])
                                  if isinstance(f, dict) and str(f.get("unit_id") or "") == selected_uid])
        base = [r for r in base if str(r.get("unit_id") or "") == selected_uid]
    try:
        decorate_review_rows(folder, [row])
    except (ImportError, OSError, ValueError, TypeError, KeyError):
        pass
    numbers = [int(n) for n in (row.get("numbers") or [row.get("number")]) if str(n).isdigit()]
    if not numbers:
        fallback = number_map.get(str(row.get("unit_id") or unit_id))
        if str(fallback or "").strip().isdigit():
            numbers = [int(fallback)]
    def detail_findings(wanted_numbers):
        if not scoped:
            return findings_for(queue, wanted_numbers, number_map)
        wanted = {str(n) for n in wanted_numbers}
        return [f for f in queue.get("items", [])
                if not str(f.get("number") or "").strip() or str(f.get("number")) in wanted]

    lines = source_lines(folder)
    if source_slice is not None and lines and numbers:
        chunk = source_slice(folder, numbers, lines, unit_id=selected_uid)
        if scoped and chunk:
            title = str((row.get("source_section") or {}).get("title") or "")
            if title and chunk[0] != title:
                chunk = [title, *chunk]
        # 独立题预览补上所属大题的标题+作答导语（「Ⅱ.短对话理解…」「你将听到十段对话…」），
        # 题组自己带材料/导语，无需重复。只影响预览，不改切题结果。
        if not scoped and len(numbers) == 1 and str(row.get("unit_kind") or "") != "group" and section_leadin is not None:
            try:
                lead = section_leadin(lines, numbers, chunk[0] if chunk else None)
                if lead:
                    # 书面表达等无印刷题号时，chunk 首行本身就是导语/要求，避免与之重复。
                    if chunk and lead[-1] == chunk[0]:
                        lead = lead[:-1]
                    chunk = list(lead) + list(chunk)
            except (ValueError, KeyError, TypeError, IndexError):
                pass
    elif slice_text is not None and lines and numbers:
        chunk = slice_text(lines, numbers[0])
    else:
        chunk = []
    blocks = attach_media(source_blocks(chunk), folder, name)
    audio_units = [row.get("unit_id") or unit_id]
    if row.get("source_parent_id"):
        audio_units.append(row["source_parent_id"])
    audio_blocks_list = audio_blocks(folder, name, audio_units)
    if audio_blocks_list:
        blocks = audio_blocks_list + blocks
    text = "\n".join(chunk)
    if not blocks and text:
        blocks = [{"type": "text", "text": text}]

    by_number = candidates_by_number(folder, unit_id=selected_uid if scoped else None)
    valid_identity = not scoped or bool(row.get("unit_id") or bound_unit)
    batch_item, _ = candidate(folder, row.get("unit_id") or unit_id) if valid_identity else ({}, None)
    subs = []
    for number in numbers:
        cand, path = by_number.get(str(number), ({}, None))
        base_sub = next((s for s in (row.get("subquestions") or [])
                         if str(s.get("number")) == str(number)), {})
        if not base_sub and len(numbers) == 1:
            base_sub = batch_item
        source = base_sub or cand
        state = next((r.get("state") for r in base if str(r.get("number")) == str(number)), "unverified")
        item = {
            "number": number,
            "stem": cand.get("stem") or base_sub.get("stem") or "",
            "options": cand.get("options") or base_sub.get("options") or [],
            "answer": cand.get("answer") or base_sub.get("answer") or "",
            "type": cand.get("type") or row.get("type") or "",
            "subtype": cand.get("subtype") or base_sub.get("subtype") or "",
            "knowledge": cand.get("knowledge") or row.get("knowledge") or [],
            "difficulty": cand.get("difficulty") or row.get("difficulty") or "",
            "state": state,
            "candidate_path": str(path.relative_to(folder)) if path else None,
        }
        item["finding_count"] = len(detail_findings([number]))
        label_items, fmt_items, _ = split_findings(detail_findings([number]))
        item["label_count"] = len([f for f in label_items if is_open(f)])
        item["format_count"] = len([f for f in fmt_items if is_open(f)])
        item["needs_human"] = bool(item["label_count"])
        subs.append(item)

    if not subs and valid_identity:
        fallback_cand, fallback_path = candidate(folder, row.get("unit_id") or unit_id)
        if fallback_cand:
            fallback_number = fallback_cand.get("number")
            if not numbers and str(fallback_number or "").strip().isdigit():
                numbers = [int(fallback_number)]
            state = next((r.get("state") for r in base if str(r.get("number")) == str(fallback_number)), "unverified")
            subs.append({
                "number": numbers[0] if numbers else None,
                "stem": fallback_cand.get("stem") or "", "options": fallback_cand.get("options") or [],
                "answer": fallback_cand.get("answer") or "", "type": fallback_cand.get("type") or row.get("type") or "",
                "subtype": fallback_cand.get("subtype") or "", "knowledge": fallback_cand.get("knowledge") or [],
                "difficulty": fallback_cand.get("difficulty") or "", "state": state,
                "candidate_path": str(fallback_path.relative_to(folder)) if fallback_path else None,
                "finding_count": len(detail_findings(numbers)),
                "needs_human": False,
            })

    findings = detail_findings(numbers)
    row = mark_row(row, findings)
    label_findings, format_findings, other_findings = split_findings(findings)
    whole_unit = taxonomy.whole_unit_type(row.get("type") or batch_item.get("type") or "")
    label_open = group_duplicate_findings(label_findings, whole_unit=whole_unit)
    format_open = [f for f in format_findings if is_open(f)] + [f for f in other_findings if is_open(f)]
    labels = label_payload(row, batch_item, subs, folder, row.get("unit_id") or unit_id)
    comparison = review_comparison(folder, row.get("unit_id") or unit_id, numbers)
    # 每条意见带上“这是哪道题、当前值是什么”，人不用再翻原卷猜问题指的是什么。
    for finding in label_open:
        finding["scope"] = (f"第 {row.get('label') or '-'.join(str(n) for n in numbers)} 题组（{len(numbers)} 小题）"
                            if len(numbers) > 1 else f"第 {numbers[0] if numbers else '?'} 题")
        if len(finding["member_ids"]) > 1:
            # 同一疑点的多条记录合并成一条展示与处理，不再逐条罗列 ID。
            finding["merged_count"] = len(finding["member_ids"])
        finding["current"] = finding_current(finding, labels, subs, numbers)
        finding["comparison"] = finding_comparison(finding, comparison, numbers, number_map)
    try:
        all_numbers = [int(n) for r in rows for n in (r.get("numbers") or []) if str(n).isdigit()]
        media = media_audit.audit(folder, all_numbers)
        shown = {Path(m.group(1).strip()).name for line in chunk for m in media_audit.IMG_REF.finditer(line)}
        media_items = [m for m in media["items"] if m["name"] in shown]
    except (OSError, ValueError, KeyError):
        media_items = []
    # 卷头、栏目图、已排除的图不是题目内容，预览里不显示，避免被当成题目图片保存。
    drop = {item["name"] for item in media_items
            if item["verdict"] in ("header", "banner", "unused", "excluded")}
    if drop:
        blocks = [block for block in blocks if not (block.get("type") == "image" and block.get("name") in drop)]
    if scoped and row.get("printed_numbers") == []:
        subs = [dict(sub, display_number=None, internal_number=sub.get("number")) for sub in subs]
    return {"batch": name, "meta": report.get("meta", manifest.get("meta", {})),
            "paper": paper_info(folder, report, manifest),
            "stage": stage_cards(manifest, bool(numbers), bool(advance_state(name).get("running"))),
            "preview_blocks": blocks, "source_preview": text,
            "audio": audio_blocks_list,
            "hidden_media": sorted(drop),
            "manifest": {"status": manifest.get("status", "unknown"), "stages": manifest.get("stages", {})},
            "row": row, "item": batch_item,
            "unit_id": row.get("unit_id", unit_id), "label": row.get("label") or "-".join(str(n) for n in numbers),
            "unit_kind": row.get("unit_kind", "standalone"), "numbers": numbers,
            "type": row.get("type") or batch_item.get("type", ""),
            "subtypes": row.get("subtypes") if isinstance(row.get("subtypes"), list)
                        else ([_row_subtype(row)] if _row_subtype(row) else []),
            "source_section": row.get("source_section") or {},
            "printed_numbers": row.get("printed_numbers"),
            "material": batch_item.get("material") or row.get("material") or "",
            "stem": row.get("stem") or batch_item.get("stem") or "",
            "labels": labels, "comparison": comparison, "label_warnings": labels.get("warnings", []),
            "preview_labels": preview_labels(labels, subs, batch_item, row),
            "media": media_items,
            "subquestions": subs, "findings": findings,
            "label_findings": label_open, "format_findings": format_open,
            "open_findings": len(label_open), "format_open": len(format_open),
            "needs_human": bool(label_open),
            "taxonomy": {"types": taxonomy.types(), "subtypes": taxonomy.subtype_map(),
                         "difficulties": taxonomy.difficulties(), "categories": taxonomy.categories(),
                         "sources": taxonomy.sources(), "years": taxonomy.years(),
                         "regions": taxonomy.regions(), "knowledge": taxonomy.knowledge_version()}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def send_json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        decoded_path = unquote(parsed.path)
        try:
            if decoded_path == "/api/health":
                return self.send_json({"ok": True, "instance_id": os.environ.get("WF_BACKEND_INSTANCE_ID", ""),
                                       "busy": work_in_flight(),
                                       "workflow_runtime_version": wf_execution_state.RUNTIME_VERSION if wf_execution_state else None,
                                       "self_heal_supervisor": _workflow_supervisor.health() if _workflow_supervisor else {"running": False}})
            if decoded_path == "/api/audio/config":
                return self.send_json(audio_overview())
            if decoded_path == "/api/audio/status":
                query = parse_qs(parsed.query)
                return self.send_json(audio_status((query.get("batch") or [""])[0] or None))
            if decoded_path == "/api/operations/incidents":
                from workflow import operations
                operations.recover_resumes(WORKFLOWS, _launch_advance)
                return self.send_json(operations.incidents(WORKFLOWS))
            if decoded_path == "/api/operations/work-state":
                return self.send_json(operations_work_state())
            if decoded_path == "/api/batches":
                return self.send_json({"batches": cached_list_batches()})
            if decoded_path == "/api/taxonomy":
                return self.send_json(taxonomy.taxonomy())
            if decoded_path == "/api/knowledge/tree":
                return self.send_json(taxonomy.knowledge_tree())
            if decoded_path == "/api/knowledge":
                query = parse_qs(parsed.query).get("q", [""])[0]
                limit = parse_qs(parsed.query).get("limit", ["40"])[0]
                try:
                    limit = int(limit)
                except ValueError:
                    limit = 40
                return self.send_json({"version": taxonomy.knowledge_version(),
                                       "items": taxonomy.knowledge_search(query, limit)})
            parts = decoded_path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "operations":
                from workflow import operations
                return self.send_json(operations.view(WORKFLOWS, batch_dir(parts[2])))
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "media":
                folder = batch_dir(parts[2])
                report, manifest, queue = report_for(folder)
                numbers = [int(n) for r in merged_rows(folder, report)
                           for n in (r.get("numbers") or []) if str(n).isdigit()]
                return self.send_json(media_audit.audit(folder, numbers))
            if len(parts) == 5 and parts[0] == "api" and parts[1] == "batches" and parts[3] == "media":
                folder = batch_dir(parts[2])
                filename = Path(parts[4]).name
                media = (folder / "01_source" / "media" / filename).resolve()
                if media.parent != (folder / "01_source" / "media").resolve() or not media.is_file():
                    raise ValueError("媒体文件不存在")
                content_type = MEDIA_KINDS.get(media.suffix.lower(), "application/octet-stream")
                data = media.read_bytes()
                # Desktop fetch bridges may transport text only. Keep raw media for
                # browsers/downloads and provide a JSON data URL for the review UI.
                if parse_qs(parsed.query).get("format") == ["data-url"]:
                    if not content_type.startswith("image/"):
                        raise ValueError("该文件不是图片")
                    return self.send_json({"src": "data:" + content_type + ";base64," +
                                           base64.b64encode(data).decode("ascii")})
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                return self.wfile.write(data)
            if len(parts) == 5 and parts[:2] == ["api", "batches"] and parts[3] == "audio":
                folder = batch_dir(parts[2])
                filename = Path(parts[4]).name
                target = (folder / "02_audio" / filename).resolve()
                if target.parent != (folder / "02_audio").resolve() or not target.is_file():
                    raise ValueError("音频文件不存在")
                content_type = MEDIA_KINDS.get(target.suffix.lower(), "audio/mpeg")
                data = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                return self.wfile.write(data)
            if len(parts) == 5 and parts[:2] == ["api", "batches"] and parts[3] == "units":
                return self.send_json(detail(parts[2], parts[4]))
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "pipeline":
                return self.send_json({"name": parts[2], **advance_state(parts[2])})
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "upload-status":
                return self.send_json(upload_state(parts[2]))
            self.send_json({"error": "not found"}, 404)
        except (ValueError, KeyError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:  # noqa: BLE001 - 本地服务，错误直接回显
            self.send_json({"error": str(exc)}, 500)

    @serialized
    def do_POST(self):
        parsed = urlparse(self.path)
        parts = unquote(parsed.path).strip("/").split("/")
        try:
            if parts == ["api", "uploads"]:
                fields, files = parse_multipart(self)
                return self.send_json({"ok": True, **handle_upload(fields, files)})
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "advance":
                return self.send_json({"ok": True, **start_advance(parts[2], reset_budget=True)})
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("请求内容必须是对象")
            if parts == ["api", "operations", "service"]:
                from workflow import operations
                return self.send_json(operations.service_heartbeat(payload.get('available')))
            if len(parts) == 5 and parts[:2] == ["api", "batches"] and parts[3] == "operations":
                from workflow import operations
                folder = batch_dir(parts[2])
                try:
                    if parts[4] == "claim":
                        return self.send_json(operations.claim(WORKFLOWS, folder, payload))
                    if parts[4] in {"action", "heartbeat", "progress", "release", "cancel"}:
                        if parts[4] != "action":
                            payload = {**payload, "action": parts[4]}
                        return self.send_json(operations.action(WORKFLOWS, folder, payload,
                            enqueue=lambda expected: _launch_advance(parts[2], folder, operations_expectation=expected)))
                except operations.OperationsBusy as exc:
                    return self.send_json({"code": "worker_busy", "admitted": False,
                        "retryable": True, "retry_after_ms": 500,
                        "batch_name": folder.name, "batch_dir": str(folder), "error": str(exc)}, 409)
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "upload":
                return self.send_json({"ok": True, **start_batch_upload(
                    parts[2], allow_unreviewed=payload.get('allow_unreviewed', False),
                    unreviewed_revision=payload.get('unreviewed_revision', ''))})
            if parts == ["api", "audio", "config"]:
                cfgmod, _, _ = _audio_modules()
                return self.send_json({"ok": True, "config": cfgmod.save(payload.get("config") or payload)})
            if parts == ["api", "audio", "bootstrap"]:
                return self.send_json({"ok": True, **start_audio_job("bootstrap", payload)})
            if parts == ["api", "audio", "segment"]:
                name = str(payload.get("name") or "").strip()
                if not name:
                    raise ValueError("请指定批次名称")
                folder = batch_dir(name)
                return self.send_json({"ok": True, **start_audio_job(
                    "segment", {"folder": str(folder), "audio": payload.get("audio")})})
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "rerun":
                return self.send_json({"ok": True, **start_rerun(parts[2], str(payload.get("mode") or ""))})
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "archive":
                return self.send_json(archive_batch(parts[2], payload.get("confirm_name")))
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "purge":
                return self.send_json(purge_batch(parts[2], payload.get("confirm_name")))
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "delete":
                return self.send_json(delete_batch(parts[2], payload.get("confirm_name")))
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "decisions":
                folder = batch_dir(parts[2])
                return self.send_json({"ok": True, "item": apply_decision(folder, payload.get("item", {}), payload.get("actor"))})
            if len(parts) == 5 and parts[:2] == ["api", "batches"] and parts[3] == "media" and parts[4] == "exclusions":
                folder = batch_dir(parts[2])
                name = Path(str(payload.get("name") or "")).name
                if not name:
                    raise ValueError("缺少图片文件名")
                data = media_audit.load_exclusions(folder)
                excluded = [str(x) for x in data.get("excluded", [])]
                if payload.get("excluded"):
                    if name not in excluded:
                        excluded.append(name)
                else:
                    excluded = [x for x in excluded if x != name]
                data["excluded"] = sorted(excluded)
                if payload.get("reason"):
                    data.setdefault("notes", {})[name] = str(payload["reason"])[:200]
                data["updated_by"] = "workbench"
                data["updated_at"] = now()
                with _lock:
                    media_audit.save_exclusions(folder, data)
                return self.send_json({"ok": True, "exclusions": data})
            if len(parts) == 5 and parts[:2] == ["api", "batches"] and parts[3] == "units":
                folder = batch_dir(parts[2])
                unit_id = parts[4]
                items = payload.get("items")
                if not isinstance(items, list) or not items:
                    items = [payload.get("item")] if payload.get("item") else []
                if not all(isinstance(x, dict) for x in items):
                    raise ValueError("item/items 必须是对象")
                labels = payload.get("labels")
                if labels is not None and not isinstance(labels, dict):
                    raise ValueError("labels 必须是对象")
                unit_edit = payload.get("unit")
                if unit_edit is not None and not isinstance(unit_edit, dict):
                    raise ValueError("unit 必须是对象")
                if not items and not labels and not unit_edit:
                    raise ValueError("没有可保存的内容")
                saved = self.save_units(folder, unit_id, items, labels, unit_edit)
                return self.send_json({"ok": True, **saved})
            self.send_json({"error": "not found"}, 404)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:  # noqa: BLE001
            self.send_json({"error": str(exc)}, 500)

    @staticmethod
    @serialized
    def save_units(folder: Path, unit_id: str, items: list[dict], labels: dict | None = None,
                   unit: dict | None = None) -> dict:
        """标签写一份（单元级），题面按小题各写各的候选。

        组题（完形/阅读等）整体打标一次，子题不重复存标签，避免同一套标签散在
        5–10 个文件里、改一处漏九处。写入的仍是 candidates 草稿，不是入库。
        """
        if not folder.is_dir():
            raise ValueError("批次不存在或已归档，请刷新列表")
        if labels is not None and "knowledge" in labels:
            labels = {**labels, "knowledge": taxonomy.require_knowledge(labels["knowledge"])}
        rows = merged_rows(folder, report_for(folder))
        confirmed = load(folder / "01_source/structure.confirmed.json", {}) or {}
        scoped_units = [u for u in confirmed.get("units", []) if isinstance(u, dict)
                        and u.get("identity_version") == "scoped-v1"]
        scoped = bool(scoped_units) or any(r.get("identity_version") == "scoped-v1" for r in rows)
        selected = next((u for u in scoped_units if str(u.get("unit_id")) == str(unit_id)), None)
        if scoped and selected is None:
            selected = next((r for r in rows if r.get("identity_version") == "scoped-v1"
                             and str(r.get("unit_id")) == str(unit_id)), None)
        if scoped and selected is None:
            raise ValueError("分节重号批次必须指定有效 unit_id，不能按小题号保存")
        by_number = candidates_by_number(folder, unit_id=unit_id if scoped else None)
        owner = {}
        for row in ([selected] if scoped else rows):
            for number in row.get("numbers") or []:
                owner[str(number)] = row.get("unit_id")
        if scoped:
            for item in items:
                number = str(item.get("number") or "").strip()
                if number not in owner:
                    raise ValueError("小题号不属于指定 unit_id，拒绝跨大题保存")
                if number not in by_number and not (selected.get("question_ids") or {}).get(number):
                    raise ValueError("指定 unit_id 缺少小题身份，拒绝按全局题号建草稿")
        saved = []
        warnings = []
        with _lock:
            if unit:
                # 单元级正文/解析：写入单元草稿候选，candidate() 优先读取它；
                # 不直接改 batch.json 正式清单。
                allowed = {k: unit[k] for k in ("material", "explanation") if k in unit}
                if allowed:
                    safe = str(unit_id).replace("/", "_").replace("\\", "_")
                    target = folder / "candidates" / f"{safe}.json"
                    merged = dict(load(target, {}))
                    merged.setdefault("unit_id", str(unit_id))
                    # 不带 number：单元级正文/解析文件不应被 candidates_by_number 当作某道
                    # 小题的候选索引，避免覆盖该小题的题面草稿。
                    merged.pop("number", None)
                    merged.update({k: ("" if v is None else str(v)) for k, v in allowed.items()})
                    merged["updated_by"] = "workbench"
                    merged["updated_at"] = now()
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.is_file():
                        shutil.copy2(target, target.with_suffix(target.suffix + ".bak"))
                    save_json(target, merged)
                    saved.append(str(target.relative_to(folder)))
            if labels:
                target = unit_label_path(folder, unit_id)
                merged = dict(load(target, {}))
                for key in ("type", "subtype", "subtypes", "difficulty", "difficultyValue", "categories",
                            "knowledge", "source", "source_type", "year", "location"):
                    if key in labels:
                        merged[key] = labels[key]
                subtypes = merged.get("subtypes") or ([merged["subtype"]] if merged.get("subtype") else [])
                knowledge = taxonomy.require_knowledge(merged.get("knowledge", []))
                merged["knowledge"] = knowledge
                resolved = taxonomy.resolve_knowledge(knowledge)
                # 清空或改成未知路径时也同步清空旧 ID，避免名字与关联脱节。
                merged["knowledgeIds"] = [point["id"] for point in resolved["resolved"]]
                filters = dict(merged.get("filterMetadata") or {})
                filters["subtypes"] = subtypes
                filters["categories"] = [str(x) for x in (merged.get("categories") or []) if str(x).strip()]
                if "difficultyValue" in merged:
                    filters["difficultyValue"] = merged["difficultyValue"]
                merged["filterMetadata"] = filters
                found = taxonomy.check_labels({
                    "type": merged.get("type"), "subtypes": subtypes,
                    "difficulty": merged.get("difficulty"), "categories": filters["categories"],
                    "knowledge": knowledge,
                })
                found += taxonomy.check_unit_dims(merged.get("type"), knowledge)
                merged["labelWarnings"] = found
                merged["unit_id"] = str(unit_id)
                merged["applies_to"] = "unit"
                numbers = [str(n) for n, own in owner.items() if str(own) == str(unit_id)]
                merged["numbers"] = sorted(numbers, key=lambda x: int(x)) if numbers else []
                merged["inherit_subquestions"] = len(merged["numbers"]) > 1
                merged["updated_by"] = "workbench"
                merged["updated_at"] = now()
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.is_file():
                    shutil.copy2(target, target.with_suffix(target.suffix + ".bak"))
                save_json(target, merged)
                saved.append(str(target.relative_to(folder)))
                warnings.extend(f"单元 {unit_id}：{text}" for text in found)
            for item in items:
                number = str(item.get("number") or "").strip()
                if not number.isdigit():
                    raise ValueError("缺少合法小题号")
                existing, path = by_number.get(number, ({}, None))
                if path is None:
                    # 新建草稿用题号命名，绝不复用 candidates 里已有的 LOCAL-NN（那是序号，不是题号）
                    stem_name = (str((selected.get("question_ids") or {}).get(number) or "")
                                 if scoped else f"LOCAL-Q{int(number):02d}")
                    if not stem_name:
                        raise ValueError("指定 unit_id 缺少小题身份，拒绝按全局题号建草稿")
                    stem_name = stem_name.replace("/", "_").replace("\\", "_")
                    path = folder / "candidates" / f"{stem_name}.json"
                    index = 2
                    while path.exists():
                        path = folder / "candidates" / f"{stem_name}-{index}.json"
                        index += 1
                    existing = {"unit_id": owner.get(number) or f"LOCAL-Q{int(number):02d}", "number": number}
                merged = dict(existing)
                for key in ("stem", "options", "answer"):
                    if key in item:
                        merged[key] = item[key]
                merged["updated_by"] = "workbench"
                merged["updated_at"] = now()
                if path.is_file():
                    shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
                save_json(path, merged)
                saved.append(str(path.relative_to(folder)))
        # Any题面/标签编辑 invalidates review and downstream upload gates. Keep the
        # previous comparison snapshot for display, but never reuse it for approval.
        if saved and wf_manifest is not None and (folder / "workflow" / "manifest.json").is_file():
            current = wf_manifest.load(folder)
            wf_manifest.invalidate_downstream(folder, current, "review", "工作台编辑了题面或标签，旧审核结果失效")
            wf_manifest.save(folder, current)
        return {"saved": saved, "warnings": warnings}


_workflow_supervisor = None


def _wake_supervisor():
    """Resume the idle-paused watchdog when new work appears (never raises)."""
    try:
        if _workflow_supervisor is not None:
            _workflow_supervisor.wake()
    except Exception:  # noqa: BLE001 - a wake failure must not block uploads/advance
        pass


def serve(http_server):
    """Start background recovery only with an actual server lifecycle."""
    global _workflow_supervisor
    from workflow.supervisor import WorkflowSupervisor
    supervisor = WorkflowSupervisor(WORKFLOWS, _dispatch_supervised,
        is_active=lambda name: bool((_advance_state.get(name) or {}).get("running")
                                   or (_upload_jobs.get(name) or {}).get("running")))
    _workflow_supervisor = supervisor
    try:
        supervisor.start()
        http_server.serve_forever()
    finally:
        supervisor.stop()
        _workflow_supervisor = None
        http_server.server_close()


def main():
    parser = argparse.ArgumentParser(description="木牍录题 REST API 后端（无页面）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8791)
    args = parser.parse_args()
    print(f"Workbench: http://{args.host}:{args.port}")
    # 内置 SSH 配置：后端启动即尝试建立本机隧道，让目录读取和上传都不再要求
    # 用户手工执行 ssh -L。失败不阻断后端，上传时会再次尝试并返回安全错误。
    def _warm_tunnel():
        try:
            from workbench import ssh_tunnel
            ssh_tunnel.ensure_upload_connection(timeout=8)
        except Exception:  # noqa: BLE001 - 启动预热失败不影响本地工作台
            pass
    threading.Thread(target=_warm_tunnel, daemon=True).start()
    # 页面第一次拉 /api/batches 时不要再现算 13 批投影（冷启约 9s，接近客户端 15s
    # 上限）；启动后在后台预热一次，首屏直接命中缓存。
    threading.Thread(target=warm_batch_list, daemon=True, name="warm-batches").start()
    serve(ThreadingHTTPServer((args.host, args.port), Handler))


if __name__ == "__main__":
    main()
