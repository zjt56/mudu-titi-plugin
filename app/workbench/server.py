# -*- coding: utf-8 -*-
"""Local-only review workbench API. No upload or publish endpoints.

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
import threading
from time import monotonic
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
    batch = load(folder / "06_batch" / "batch.json", {}) or {}
    meta = (report.get("meta") if isinstance(report, dict) else None) or (manifest or {}).get("meta") or {}
    detected = {}
    if not (batch.get("source") or meta.get("source") or meta.get("title")):
        text_path = folder / "01_source" / "正文.txt"
        if text_path.is_file():
            try:
                from workflow import denoise
                detected = denoise.extract_meta(text_path.read_text(encoding="utf-8-sig").splitlines()) or {}
            except (OSError, ValueError):
                detected = {}
    title = str(batch.get("source") or meta.get("source") or meta.get("title")
                or detected.get("source") or "").strip()
    return {"title": title or folder.name, "batch": str(batch.get("batch") or folder.name),
            "folder": folder.name, "year": batch.get("year") or meta.get("year") or detected.get("year"),
            "region": batch.get("region") or meta.get("region") or detected.get("region"),
            "subject": batch.get("subject") or meta.get("subject") or "english"}


def stage_cards(manifest, has_output: bool = False, advance_running: bool = False) -> dict:
    stages = (manifest or {}).get("stages") or {}
    recorded_running = (manifest or {}).get("status") == "running" or any(
        record.get("status") == "running" for record in stages.values() if isinstance(record, dict))
    running_unknown = bool(recorded_running and not advance_running)
    cards = []
    for sid in (STAGE_ORDER or list(stages)):
        record = stages.get(sid) or {}
        cards.append({"id": sid, "title": STAGE_TITLES.get(sid, sid),
                      "kind": STAGE_KINDS.get(sid) or record.get("kind") or "",
                      "status": record.get("status") or "pending",
                      "notes": record.get("notes") or ""})
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
            "hint": hint, "stale": stale, "running_unknown": running_unknown}


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
    audio = next((f for f in files if Path(f["filename"]).suffix.lower() in AUDIO_SUFFIXES), None)
    answer = next((f for f in docs if re.search(r"答案|answer|解析", Path(f["filename"]).stem, re.I)), None)
    source = next((f for f in docs if f is not answer), docs[0])
    year = str(fields.get("year") or "").strip()
    region = str(fields.get("region") or "").strip()
    title = str(fields.get("title") or "").strip()
    subject = str(fields.get("subject") or "english").strip() or "english"
    name = _unique_batch_name(_slug(title or f"{region}{year}"))
    folder = INBOX / name
    folder.mkdir(parents=True, exist_ok=True)
    saved_source = _save_part(folder, source, "source")
    saved_answer = _save_part(folder, answer, "answers") if answer else None
    saved_audio = _save_part(folder, audio, "audio") if audio else None
    descriptor = {"workflow_id": name, "subject": subject,
                  "year": int(year) if year.isdigit() else None, "region": region or None,
                  "source": saved_source.name, "answers": saved_answer.name if saved_answer else None,
                  "audio": saved_audio.name if saved_audio else None}
    save_json(folder / "batch.json", descriptor)
    candidate = {"name": name, "folder": str(folder), "subject": subject,
                 "year": descriptor["year"], "region": descriptor["region"],
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


def overview(name):
    folder = batch_dir(name)
    report, manifest, queue = report_for(folder)
    base = question_rows(folder, report)
    number_map = number_of_unit(base)
    rows = [mark_row(row, findings_for(queue, row.get("numbers") or [row.get("number")], number_map))
            for row in merged_rows(folder, report)]
    counts = {s: sum(1 for r in rows if r.get("state") == s) for s in ("ok", "attention", "blocked", "unverified")}
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
    batch_findings = [x for x in open_items if not str(x.get("unit_id") or "").strip()]
    advance = advance_state(name)
    return {"name": name, "meta": report.get("meta", manifest.get("meta", {})),
            "status": manifest.get("status", report.get("status", "unknown")),
            "paper": paper_info(folder, report, manifest),
            "stage": stage_cards(manifest, bool(rows), bool(advance.get("running"))), "advance": advance,
            "counts": counts, "total": len(rows), "unit_total": len(rows),
            "question_total": len(base) if base else report.get("total", len(rows)),
            "open_findings": len(label_open), "format_findings": len(format_open),
            "batch_findings": batch_findings,
            "needs_human": sum(1 for row in rows if row["needs_human"]),
            "format_units": sum(1 for row in rows if row.get("format_count")),
            "media": media_summary,
            "extraction": load(folder / "07_review" / "extraction-report.json", {}),
            "structure_gate": load(folder / "07_review" / "structure-gate.json", {}),
            "stages": stages, "rows": rows}


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
    values = {
        "source": item.get("source") or filters.get("source") or row.get("source") or "",
        "source_type": item.get("source_type") or filters.get("source_type") or row.get("source_type") or "",
        "year": str(item.get("year") or filters.get("year") or row.get("year") or ""),
        "location": item.get("location") or filters.get("location") or row.get("location") or "",
        "type": unit_labels.get("type") or item.get("question_type") or item.get("type") or row.get("question_type") or row.get("type") or "",
        "subtypes": filters.get("subtypes") or ([item.get("subtype")] if item.get("subtype") else []),
        "difficulty": (taxonomy.difficulty_name(item.get("difficulty"))
                       or taxonomy.difficulty_name(filters.get("difficulty"))
                       or taxonomy.difficulty_name(filters.get("difficultyValue"))
                       or taxonomy.difficulty_name(row.get("difficulty"))),
        "categories": filters.get("categories") or [],
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
    payload = load(folder / "07_review/review-rounds.json", {})
    records = payload.get("rounds", []) if isinstance(payload, dict) else []
    try:
        from workflow import review
        evaluated = review.evaluate_rounds(folder, payload)
        checked = set(evaluated.get("checked_rounds", []))
        plan = evaluated.get("plan") or {}
    except (ImportError, OSError, ValueError, TypeError, KeyError):
        checked, plan = set(), {}

    # Only use originals that are inputs to the validated plan; batch.json may
    # be a derived or subsequently edited representation of the same labels.
    hashes = plan.get("input_hashes") or {}
    def bound(relative):
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
                           "labels": labels, "subquestions": originals}, "rounds": []}
    wanted = {str(int(n)) for n in numbers if str(n).isdigit()}
    history_exists = has_unverified_review_history(folder)
    for rid, name in (("labels", "一审"), ("sample", "二审")):
        matches = [r for r in records if isinstance(r, dict) and r.get("round_id") == rid] if isinstance(records, list) else []
        entry = {"round_id": rid, "name": name,
                 "status": "待重新审核" if matches else ("历史来源未验证" if history_exists else "未提供"), "checks": []}
        if rid in checked and len(matches) == 1:
            record = matches[0]
            for check in record.get("checks", []):
                if not isinstance(check, dict) or str(check.get("unit_id") or "") != uid:
                    continue
                if rid == "sample" and (not str(check.get("number", "")).isdigit()
                                        or str(int(check["number"])) not in wanted):
                    continue
                assessment = check.get("assessment") if isinstance(check.get("assessment"), dict) else {}
                entry["checks"].append({"unit_id": uid, "number": check.get("number"),
                                        "verdict": check.get("verdict"), "rationale": check.get("rationale"),
                                        "assessment": assessment, "evidence": check.get("evidence")})
            entry["status"] = "已提供" if entry["checks"] else "未覆盖"
            entry["source"] = "07_review/review-rounds.json"
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
            if round_entry["round_id"] == "sample" and number and str(check.get("number")) != number:
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
            target_covered = any(str(c.get("number")) == number for c in round_entry["checks"])
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
                return self.send_json({"ok": True})
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
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("请求内容必须是对象")
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
