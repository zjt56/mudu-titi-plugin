# -*- coding: utf-8 -*-
"""Local-only review workbench API. Upload writes to the cloud 上传暂存 only; no publish.

题目列表按“入库单元”合并：一个独立题一行，一个题组（完形、任务型阅读、
听力对话组等）一行，前端一次显示整组材料与全部小题。
"""
from __future__ import annotations

import argparse
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
except ImportError:  # 看台可以脱离流水线单独跑
    auto_flow = wf_contracts = wf_manifest = None

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
# 听力切段后台任务（安装 / 切段共用一把闸，避免并发下载与切片）。
_audio_job = {"running": False, "kind": "", "batch": "", "lock": threading.Lock()}


def serialized(function):
    """同服务的建批次、推进注册、删除与写入共享临界区。"""
    @wraps(function)
    def wrapped(*args, **kwargs):
        global _batches_snapshot
        with _lock:
            try:
                return function(*args, **kwargs)
            finally:
                # Writes must invalidate the short-lived HTTP list snapshot.
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


def batch_dir(name):
    if not isinstance(name, str) or not name or Path(name).name != name or name in (".", "..") or any(c in name for c in ("\\", "/", "\x00")):
        raise ValueError("非法批次名")
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


def candidates_by_number(folder):
    mapping = {}
    directory = folder / "candidates"
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        item = load(path, {}) or {}
        number = str(item.get("number") or "").strip()
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
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[TABLE"):
            table = []
            continue
        if stripped.startswith("[/TABLE]"):
            if table is not None:
                blocks.append({"type": "table", "rows": table})
                table = None
            continue
        if table is not None:
            cells = [cell.strip() for cell in line.split("|")]
            if any(cells):
                table.append(cells)
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
    for block in blocks:
        if block.get("type") != "image":
            continue
        target = (media_root / block["name"]).resolve()
        block["url"] = f"/api/batches/{name}/media/{block['name']}" if target.parent == media_root and target.is_file() else None
    return blocks


AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac")


def audio_map(folder: Path) -> dict:
    """unit_id → 已切好的听力片段；读取 02_audio/audio_map.json，文件存在才纳入。"""
    path = folder / "02_audio" / "audio_map.json"
    mapping = {}
    if not path.is_file():
        return mapping
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig")) or {}
    except (ValueError, OSError):
        return mapping
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


def findings_for(queue, numbers, number_map):
    wanted = {as_number(n) for n in numbers if as_number(n)}
    result = []
    items = queue.get("items", []) if isinstance(queue, dict) else []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        unit = str(item.get("unit_id") or "")
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
    return row


def paper_info(folder: Path, report, manifest) -> dict:
    """试卷名取自入库批次；旧批次缺来源时从原卷卷头识别，绝不退回目录名。"""
    from workflow.metadata_policy import MISSING

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
    descriptor = load(INBOX / folder.name / "batch.json", {}) or {}
    title = known(batch.get("source"), meta.get("source"), meta.get("title"), meta.get("paper_title"),
                  detected.get("source"), descriptor.get("title"))
    return {"title": str(title or MISSING), "batch": str(batch.get("batch") or folder.name),
            "source": str(known(batch.get("source"), meta.get("source"), detected.get("source")) or MISSING),
            "source_type": str(known(batch.get("source_type"), meta.get("source_type"), detected.get("source_type")) or MISSING),
            "folder": folder.name, "year": batch.get("year") or meta.get("year") or detected.get("year"),
            "region": batch.get("region") or meta.get("region") or detected.get("region"),
            "subject": batch.get("subject") or meta.get("subject") or "english"}


def stage_cards(manifest, has_output: bool = False, advance_running: bool = False, diagnostics=None) -> dict:
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
    cards = []
    for sid in (STAGE_ORDER or list(stages)):
        record = stages.get(sid) or {}
        card = {"id": sid, "title": STAGE_TITLES.get(sid, sid),
                "kind": STAGE_KINDS.get(sid) or record.get("kind") or "",
                "status": record.get("status") or "pending",
                "notes": record.get("notes") or ""}
        diag = diag_by_stage.get(sid)
        if diag:
            card["reason"] = diag.get("reason") or ""
            card["detail"] = diag.get("detail") or ""
            card["hint"] = diag.get("hint") or ""
            card["command"] = diag.get("command") or ""
            card["blocking"] = bool(diag.get("blocking"))
        cards.append(card)
    done = sum(1 for card in cards if card["status"] in TERMINAL_OK)
    current = next((card for card in cards if card["status"] not in TERMINAL_OK), None)
    early = ("extract", "answers", "audio", "structure_gate")
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


@serialized
def start_advance(name: str) -> dict:
    """后台推进流水线：代理阶段会停住等桥，人工阶段停住等人。"""
    if auto_flow is None:
        raise ValueError("流水线模块不可用")
    folder = batch_dir(name)
    state = _advance_state.get(name) or {}
    if state.get("running"):
        return {"started": False, "running": True}
    _advance_state[name] = {"running": True, "started_at": now()}

    def work():
        try:
            result = auto_flow.advance(folder, auto_flow.load_bridge())
        except Exception as exc:  # noqa: BLE001 - 后台线程，结果回显给前端
            result = {"error": str(exc)}
        with _lock:
            _advance_state[name] = {"running": False, "result": result, "finished_at": now()}

    threading.Thread(target=work, daemon=True, name=f"workbench-advance-{name}").start()
    return {"started": True, "running": True}


def advance_state(name: str) -> dict:
    return _advance_state.get(name) or {"running": False}


# 重新标签 / 重新审核：从指定阶段起让其后全部失效，再后台推进到停住。
# - tag    （重新标签）：从「题型与子类 types」起，重跑题型/解析/知识点/组装/体检/审核全链路。
# - review （重新审核）：从「审核 review」起，只重跑两轮审核（一审全量 + 二审抽检）到审核结束。
RERUN_START = {"tag": "types", "review": "review"}
RERUN_LABEL = {"tag": "重新标签", "review": "重新审核"}


def start_rerun(name: str, mode: str) -> dict:
    """让 RERUN_START[mode] 及其下游失效，然后后台推进到停住。"""
    if auto_flow is None or wf_manifest is None:
        raise ValueError("流水线模块不可用")
    if mode not in RERUN_START:
        raise ValueError("不支持的重新执行类型")
    folder = batch_dir(name)
    if (_advance_state.get(name) or {}).get("running"):
        return {"started": False, "running": True}
    manifest = wf_manifest.load(folder)
    start_stage = RERUN_START[mode]
    changed = wf_manifest.invalidate_downstream(folder, manifest, start_stage, RERUN_LABEL[mode])
    wf_manifest.save(folder, manifest)
    _advance_state[name] = {"running": True, "started_at": now(), "mode": mode, "invalidated": changed}

    def work():
        try:
            result = auto_flow.advance(folder, auto_flow.load_bridge())
        except Exception as exc:  # noqa: BLE001 - 后台线程，结果回显给前端
            result = {"error": str(exc)}
        with _lock:
            _advance_state[name] = {"running": False, "result": result, "finished_at": now()}

    threading.Thread(target=work, daemon=True, name=f"workbench-rerun-{name}").start()
    return {"started": True, "running": True, "mode": mode, "from": start_stage, "invalidated": changed}


def _slug(text, fallback: str = "新试卷") -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "", str(text or "")).strip(".")
    return cleaned[:40] or fallback


def _unique_batch_name(base: str) -> str:
    name, index = base, 2
    while (WORKFLOWS / name).exists() or (INBOX / name).exists():
        name, index = f"{base}-{index}", index + 1
    return name


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
    audio_files = [f for f in files if Path(f["filename"]).suffix.lower() in AUDIO_SUFFIXES]
    if len(audio_files) > 1:
        raise ValueError("一批最多上传一个音频文件；检测到多个音频，未保存任何文件")
    audio = audio_files[0] if audio_files else None
    answer_candidates = [f for f in docs if re.search(r"答案|answer|解析", Path(f["filename"]).stem, re.I)]
    if len(answer_candidates) > 1:
        raise ValueError("一批最多上传一个答案/解析文档；请合并后再上传，未保存任何文件")
    answer = answer_candidates[0] if answer_candidates else None
    if len(docs) > 2:
        names = "、".join(Path(f["filename"]).name for f in docs)
        raise ValueError("一批最多支持正文+答案/解析两个文档；检测到多余文档：" + names + "，未保存任何文件")
    source = next((f for f in docs if f is not answer), docs[0])
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
    # 未填写标题时保留用户上传文件的识别线索，不再一律变成「新试卷」。
    # 此名称仅用于展示/定位，正式来源仍由原卷卷头识别，不覆盖原文证据。
    name = _unique_batch_name(_slug(title or Path(source["filename"]).stem or f"{region}{year}"))
    folder = INBOX / name
    folder.mkdir(parents=True, exist_ok=True)
    saved_source = _save_part(folder, source, "source")
    saved_answer = _save_part(folder, answer, "answers") if answer else None
    saved_audio = _save_part(folder, audio, "audio") if audio else None
    descriptor = {"workflow_id": name, "subject": subject, "title": title or None,
                  "year": int(year) if year.isdigit() else None, "region": region or None,
                  "source_type": source_type or None, "grade": grade or None,
                  "source": saved_source.name, "answers": saved_answer.name if saved_answer else None,
                  "audio": saved_audio.name if saved_audio else None,
                  "original_filenames": {"source": Path(source["filename"]).name,
                                         "answers": Path(answer["filename"]).name if answer else None,
                                         "audio": Path(audio["filename"]).name if audio else None}}
    save_json(folder / "batch.json", descriptor)
    candidate = {"name": name, "folder": str(folder), "subject": subject,
                 "year": descriptor["year"], "region": descriptor["region"],
                 "source_type": descriptor["source_type"], "grade": descriptor["grade"],
                 "source": str(saved_source), "answers": str(saved_answer) if saved_answer else None,
                 "audio": str(saved_audio) if saved_audio else None}
    batch_folder = auto_flow.ensure_batch(candidate, WORKFLOWS)
    start_advance(batch_folder.name)
    manifest = wf_manifest.load(batch_folder) if wf_manifest else {}
    try:
        inbox_rel = str(folder.relative_to(ROOT))
    except ValueError:
        inbox_rel = str(folder)
    return {"name": batch_folder.name, "inbox": inbox_rel,
            "paper": paper_info(batch_folder, {}, manifest),
            "stage": stage_cards(manifest), "files": [saved_source.name]}


def _archive_preflight(path: Path):
    """归档只移动真实目录；拒绝所有软链接，避免递归读取/恢复越界。"""
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"归档目标不是本地真实目录：{path.name}")
    for current, dirs, files in os.walk(path, followlinks=False):
        if any((Path(current) / name).is_symlink() for name in dirs + files):
            raise ValueError(f"目录含软链接，请先人工检查：{path.name}")


@serialized
def archive_batch(name: str, confirm_name: str) -> dict:
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


def review_snapshot(folder):
    """只读投影真实分轮记录；不以旧 report.json 或流程状态冒充审核证据。"""
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
        rounds.append({"round_id": rid, "name": name, "completed": rid in checked,
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
    return {"execution_completed": bool(evaluated.get("execution_completed")),
            "quality_passed": bool(evaluated.get("passed")), "rounds": rounds,
            "question_total": len(plan.get("questions", [])),
            "open_findings": len(evaluated.get("findings", [])),
            "revision": "|".join(revision)}, records


def decorate_review_rows(folder, rows):
    """把真实分轮结果与“跳过听力”口径落到行上；总览与单元详情共用。"""
    try:
        review_status, records = review_snapshot(folder)
    except (ImportError, OSError, ValueError, TypeError, KeyError):
        review_status, records = {}, {}
    skip_listening = (folder / "02_audio" / "SKIP_LISTENING").is_file()
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


def overview(name):
    folder = batch_dir(name)
    report, manifest, queue = report_for(folder)
    base = question_rows(folder, report)
    number_map = number_of_unit(base)
    rows = [mark_row(row, findings_for(queue, row.get("numbers") or [row.get("number")], number_map))
            for row in merged_rows(folder, report)]
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
    # 「二审结束」唯一口径：review（两轮审核：一审全量 + 二审抽 20%）阶段进入完成态。
    # 上传页排队区据此移除已结束的试卷，并流转到试卷审核界面做人工确认；
    # 仍在处理 / 卡住的试卷保留在排队区并显示卡点诊断。
    review_status_value = str(((manifest.get("stages") or {}).get("review") or {}).get("status") or "")
    gate_status_value = str(((manifest.get("stages") or {}).get("review_gate") or {}).get("status") or "")
    flow_done = review_status_value in TERMINAL_OK or gate_status_value in TERMINAL_OK
    upload_stage_done = str(((manifest.get("stages") or {}).get("upload") or {}).get("status") or "") == "completed"
    receipt = load(folder / "receipt.json", {})
    upload_pending = bool(upload_stage_done and isinstance(receipt, dict) and receipt.get("remote_confirmation_required"))
    uploaded = bool(upload_stage_done and not upload_pending)
    stage_info = stage_cards(manifest, bool(rows), bool(advance.get("running")), diagnostics)
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
            # 自动流程是否结束（二审完成）；上传页排队区按此移除，并提示人工确认。
            "flow_done": flow_done,
            # 已写入云端上传暂存的批次视为完成，试卷审核列表不再展示。
            "uploaded": uploaded, "upload_pending_confirmation": upload_pending}


def list_batches():
    result = []
    if WORKFLOWS.is_dir():
        for folder in sorted(WORKFLOWS.iterdir()):
            if folder.is_dir() and (folder / "workflow" / "manifest.json").is_file():
                try:
                    result.append(overview(folder.name))
                except ValueError:
                    pass
    return result


def cached_list_batches():
    """Coalesce overlapping HTTP list reads; writes invalidate immediately.

    Older loaded hosts use this endpoint as a readiness probe. A short snapshot
    prevents those parallel probes from repeating all media audits per image.
    The uncached list_batches remains available to internal workflow callers.
    """
    global _batches_snapshot
    with _lock:
        key = (str(WORKFLOWS), str(INBOX))
        if (_batches_snapshot is not None and _batches_snapshot[0] == key
                and monotonic() < _batches_snapshot[1]):
            return _batches_snapshot[2]
        result = list_batches()
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
    return state


# 后台听力任务稳定性的时间参数（秒）：心跳在 progress 里刷新。
_AUDIO_STALE_SECONDS = 600          # 状态文件 running 但 >10min 无心跳：判定为僵死，允许重开
_AUDIO_WATCHDOG_SECONDS = 900       # 看门狗：>15min 无心跳则把任务判失败并清理，避免永久卡住
_AUDIO_WATCHDOG_TICK = 30


def _audio_has_live_worker(data_dir: Path) -> bool:
    """是否还有本机 audio/worker.py 子进程在跑（用于区分“真在跑”与“线程/进程已死”）。"""
    try:
        import subprocess
        out = subprocess.run(["ps", "-Axo", "command"], capture_output=True, text=True, timeout=5).stdout
        return "audio/worker.py" in out
    except Exception:  # noqa: BLE001
        return True  # 无法判断时保守地当作仍在跑，不误判重开


def _audio_reap_stale(kind: str, batch, data_dir: Path, boot) -> None:
    """清理“状态 running 但线程/进程已死”的僵死任务，避免永久卡住无法重开。

    只有当状态文件 running 且心跳超时、且本机确实没有 worker.py 在跑时才清理。
    """
    state = boot.read_status(data_dir, batch)
    if not state.get("running"):
        return
    age = time() - float(state.get("updated") or 0)
    if age < _AUDIO_STALE_SECONDS:
        return
    if _audio_has_live_worker(data_dir):
        return  # 有真实进程在跑，交给看门狗，不抢占
    boot.write_status(data_dir, batch=batch, job=state.get("job") or kind, running=False,
                      ok=False, phase="error",
                      detail="", error="切段任务异常中断（进程已退出、心跳超时），已自动重置，可重跑")


def start_audio_job(kind: str, options: dict) -> dict:
    cfgmod, boot, segment_mod = _audio_modules()
    cfg = cfgmod.load()
    data_dir = Path(cfgmod.data_dir(cfg))
    batch = Path(options["folder"]).name if kind == "segment" and options.get("folder") else None
    if batch:
        _audio_reap_stale(kind, batch, data_dir, boot)
        current = boot.read_status(data_dir, batch)
        if current.get("running") and time() - float(current.get("updated") or 0) < _AUDIO_STALE_SECONDS:
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


def _run_batch_upload(name, actor):
    job = _upload_jobs[name]

    def step(label, args):
        job["step"] = label
        proc = _run_runner(args)
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
        job["output"] = out[-4000:]
        return proc.returncode, out

    try:
        folder = batch_dir(name)
        rc, out = step("准备上传（冻结请求）", ["prepare-upload", "--batch-dir", str(folder)])
        if rc != 0:
            job.update(ok=False, error="上传准备未通过：" + (out.splitlines()[-1] if out else str(rc)))
            return
        rc, out = step("确认上传闸门", ["mark", "--batch-dir", str(folder), "--stage", "upload_gate",
                                        "--status", "approved", "--actor", actor,
                                        "--note", "通过插件「上传批次」按钮确认"])
        if rc != 0:
            job.update(ok=False, error="上传确认失败：" + (out.splitlines()[-1] if out else str(rc)))
            return
        rc, out = step("写入云端上传暂存", ["upload", "--batch-dir", str(folder), "--yes"])
        if rc != 0:
            job.update(ok=False, error="上传被拒绝：" + (out.splitlines()[-1] if out else str(rc)))
            return
        job.update(ok=True, step="已上传")
    except Exception as exc:  # noqa: BLE001 - 后台任务，错误回显给面板
        job.update(ok=False, error=str(exc))
    finally:
        job["running"] = False
        job["finished_at"] = time()


def start_batch_upload(name, actor="本机操作员"):
    batch_dir(name)  # 批次不存在直接抛错
    with _lock:
        job = _upload_jobs.get(name)
        if job and job.get("running"):
            raise ValueError("该批次正在上传中，请稍候")
        _upload_jobs[name] = {"running": True, "ok": None, "step": "开始", "output": "",
                              "error": "", "started_at": time()}
    threading.Thread(target=_run_batch_upload, args=(name, actor), daemon=True).start()
    return {"started": True}


def upload_state(name):
    job = _upload_jobs.get(name)
    if not job:
        return {"running": False, "ok": None, "step": "", "output": "", "error": ""}
    return dict(job)


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
    knowledge_sources = [unit_labels["knowledge"]] if explicit_knowledge else [item.get("knowledge"), row.get("knowledge")]
    for source in knowledge_sources:
        for path in source or []:
            if str(path).strip() and str(path).strip() not in knowledge:
                knowledge.append(str(path).strip())
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
                                        "assessment": assessment, "evidence": check.get("evidence")})
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
    row = next((r for r in rows if str(r.get("unit_id")) == str(unit_id)), None)
    if row is None:
        # Previously bookmarked merged group opens its first current partition.
        row = next((r for r in rows if r.get("source_parent_id") == str(unit_id)), None)
    if row is None:
        alias = re.sub(r"\D", "", str(unit_id))
        if alias:
            row = next((r for r in rows if int(alias) in (r.get("numbers") or [])), None)
    if row is None:
        row = next((r for r in rows if str(r.get("number")) == str(unit_id)), {})
    try:
        decorate_review_rows(folder, [row])
    except (ImportError, OSError, ValueError, TypeError, KeyError):
        pass
    numbers = [int(n) for n in (row.get("numbers") or [row.get("number")]) if str(n).isdigit()]
    if not numbers:
        fallback = number_map.get(str(row.get("unit_id") or unit_id))
        if str(fallback or "").strip().isdigit():
            numbers = [int(fallback)]
    lines = source_lines(folder)
    if source_slice is not None and lines and numbers:
        chunk = source_slice(folder, numbers, lines)
        # 独立题预览补上所属大题的标题+作答导语（「Ⅱ.短对话理解…」「你将听到十段对话…」），
        # 题组自己带材料/导语，无需重复。只影响预览，不改切题结果。
        if len(numbers) == 1 and str(row.get("unit_kind") or "") != "group" and section_leadin is not None:
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

    by_number = candidates_by_number(folder)
    batch_item, _ = candidate(folder, row.get("unit_id") or unit_id)
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
        item["finding_count"] = len(findings_for(queue, [number], number_map))
        label_items, fmt_items, _ = split_findings(findings_for(queue, [number], number_map))
        item["label_count"] = len([f for f in label_items if is_open(f)])
        item["format_count"] = len([f for f in fmt_items if is_open(f)])
        item["needs_human"] = bool(item["label_count"])
        subs.append(item)

    if not subs:
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
                "finding_count": len(findings_for(queue, numbers, number_map)),
                "needs_human": False,
            })

    findings = findings_for(queue, numbers, number_map)
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
                return self.send_json({"ok": True, "instance_id": os.environ.get("WF_BACKEND_INSTANCE_ID", "")})
            if decoded_path == "/api/audio/config":
                return self.send_json(audio_overview())
            if decoded_path == "/api/audio/status":
                query = parse_qs(parsed.query)
                return self.send_json(audio_status((query.get("batch") or [""])[0] or None))
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
                return self.send_json({"ok": True, **start_advance(parts[2])})
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "upload":
                return self.send_json({"ok": True, **start_batch_upload(parts[2])})
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("请求内容必须是对象")
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
        by_number = candidates_by_number(folder)
        rows = merged_rows(folder, report_for(folder))
        owner = {}
        for row in rows:
            for number in row.get("numbers") or []:
                owner[str(number)] = row.get("unit_id")
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
                    stem_name = f"LOCAL-Q{int(number):02d}"
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


def main():
    parser = argparse.ArgumentParser(description="木牍录题 REST API 后端（无页面）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8791)
    args = parser.parse_args()
    print(f"Workbench: http://{args.host}:{args.port}")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
