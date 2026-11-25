# -*- coding: utf-8 -*-
"""Workbench on-demand AI supplement for missing/AI-generated articleSummary & explanation."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack, contextmanager, nullcontext
from pathlib import Path

from . import (
    content_rules,
    contracts,
    format_gate,
    listening_script,
    local_agent,
    manifest as mf,
    manual_content,
    operations,
    runner,
    stage_semantics,
    supervisor,
    upload_content,
)
from .workflow_primitives import write_text_atomic as _primitives_write_text_atomic

SUMMARY_ELIGIBLE_TYPES = frozenset({
    "完形填空", "阅读理解", "任务型阅读", "短文填空",
    "选词填空", "语法选择", "读写综合", "教材语篇填空",
})
MIN_SUMMARY_WORDS_GENERAL = 50
MIN_SUMMARY_WORDS_ELIGIBLE = 20
EXCLUDED_EXPLANATION_TYPE_HINTS = ("听力", "书面表达")
MAX_BODY_CHARS = 50
PROGRESS_REL = "workflow/.ai-supplement-progress.json"
PENDING_REL = "workflow/.ai-supplement-pending.json"
SUPPLEMENT_LOCK_REL = "workflow/.ai-supplement.lock"
CONTENT_WRITE_LOCK_REL = "workflow/.content-write.lock"
DEFAULT_SUPPLEMENT_CONCURRENCY = 3
AGENT_BY_MODE = {
    "articleSummary": "ai_summary",
    "explanation": "ai_explanation",
    "listeningScript": "ai_listening_script",
}

_ENGLISH_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_PREFIX_STRIP = re.compile(
    r"^\s*(?:【\s*(?:解析|参考解析|文章大意|大意|导读)\s*】|(?:解析|参考解析|文章大意|大意|explanation|summary)\s*[:：])\s*",
    re.I,
)
_ACTIVE_JOBS: dict[str, threading.Thread] = {}
_RUNNING_BATCHES: set[str] = set()
_JOBS_LOCK = threading.Lock()


class ContentEditConflictError(ValueError):
    """Raised when a content edit targets fields modified by another session."""

    def __init__(self, message: str, *, conflicts: list[dict] | None = None):
        super().__init__(message)
        self.conflicts = list(conflicts or [])


def _is_absent(value) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    text = value.strip()
    return not text or content_rules.is_placeholder(text)


def _is_replaceable(value) -> bool:
    """Missing/placeholder OR previously AI-generated content can be generated/regenerated."""
    if _is_absent(value):
        return True
    return isinstance(value, str) and (
        upload_content.AI_MARK in value or upload_content.AI_TRANSCRIPT_MARK in value
    )


def _is_listening_script_replaceable(value) -> bool:
    """True when a listening unit has no original listening script (or only an AI-transcribed one)."""
    if _is_absent(value):
        return True
    if not isinstance(value, str):
        return False
    if listening_script.MARK in value:
        script_part = value.split(listening_script.MARK, 1)[1]
        return upload_content.AI_TRANSCRIPT_MARK in script_part
    return True


def _word_count(text: str) -> int:
    return len(_ENGLISH_WORD.findall(str(text or "")))


def _clamp_body(text: str, limit: int = MAX_BODY_CHARS) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    cut = cleaned[:limit]
    for sep in ("。", "；", "，", ".", ";", ","):
        idx = cut.rfind(sep)
        if idx >= limit // 2:
            return cut[: idx + 1].strip()
    return cut.rstrip(" ，,;；:：") + "。"


def _strip_mark_and_prefix(text: str) -> str:
    cleaned = str(text or "").strip()
    if cleaned.endswith(upload_content.AI_MARK):
        cleaned = cleaned[:-len(upload_content.AI_MARK)].strip()
    cleaned = _PREFIX_STRIP.sub("", cleaned).strip()
    return cleaned


def _format_ai_text(text: str) -> str:
    body = _clamp_body(_strip_mark_and_prefix(text), MAX_BODY_CHARS)
    if not body:
        return ""
    return f"{body}{upload_content.AI_MARK}"


def _clean_listening_script_body(text: str) -> str:
    cleaned = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    cleaned = re.sub(r"^(?:听力录音材料|听力原文|录音原文)\s*[:：]\s*", "", cleaned)
    for mark in (upload_content.AI_TRANSCRIPT_MARK, upload_content.AI_MARK, "（AI转录）", "(AI转录)"):
        if cleaned.endswith(mark):
            cleaned = cleaned[:-len(mark)].strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def _format_listening_script(raw_text: str, existing_explanation: str = "") -> str:
    body = _clean_listening_script_body(raw_text)
    if not body:
        return ""
    marked_script = f"{body}\n{upload_content.AI_TRANSCRIPT_MARK}"
    prefix = ""
    if isinstance(existing_explanation, str) and not _is_absent(existing_explanation):
        if listening_script.MARK in existing_explanation:
            prefix = existing_explanation.split(listening_script.MARK, 1)[0].strip()
        elif upload_content.AI_TRANSCRIPT_MARK not in existing_explanation:
            prefix = existing_explanation.strip()
    return listening_script.combine(prefix, marked_script)


def _extract_unit_transcript(batch_dir: Path, unit_id: str) -> dict | None:
    """Extract timed ASR transcript segments for a listening unit from 02_audio/."""
    batch = Path(batch_dir)
    ev_path = batch / "02_audio" / "segment-evidence.json"
    if not ev_path.is_file():
        return None
    try:
        ev_doc = json.loads(ev_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    rows = ev_doc.get("items") if isinstance(ev_doc, dict) else None
    if not isinstance(rows, list):
        return None
    matched = next((r for r in rows if isinstance(r, dict) and str(r.get("unit_id") or "") == unit_id), None)
    if not isinstance(matched, dict):
        return None
    start, end = matched.get("start"), matched.get("end")
    if any(isinstance(n, bool) or not isinstance(n, (int, float)) for n in (start, end)) or not (0 <= start < end):
        return None

    tx_path = None
    sources = ev_doc.get("sources") if isinstance(ev_doc.get("sources"), dict) else {}
    for key_field in ("source_key", "source_audio", "source_path"):
        sval = matched.get(key_field)
        if isinstance(sval, str) and sval in sources and isinstance(sources[sval], dict):
            rel = sources[sval].get("transcript")
            if isinstance(rel, str) and (batch / rel).is_file():
                tx_path = batch / rel
                break
    if tx_path is None:
        default_tx = batch / "02_audio" / "full-transcript.json"
        if default_tx.is_file():
            tx_path = default_tx
    if tx_path is None:
        return None
    try:
        tx_doc = json.loads(tx_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    segments = tx_doc.get("segments") if isinstance(tx_doc, dict) else (tx_doc if isinstance(tx_doc, list) else None)
    lines: list[str] = []
    if isinstance(segments, list):
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            s0, s1 = seg.get("start"), seg.get("end")
            if any(isinstance(n, bool) or not isinstance(n, (int, float)) for n in (s0, s1)):
                continue
            if s0 < end and s1 > start:
                txt = str(seg.get("text") or "").strip()
                if txt:
                    lines.append(f"[{s0:.1f}s-{s1:.1f}s] {txt}")
    raw_text = "\n".join(lines).strip()
    if not raw_text and isinstance(tx_doc, dict) and len(rows) == 1:
        raw_text = str(tx_doc.get("text") or "").strip()
    if not raw_text:
        return None
    return {
        "raw_text": raw_text,
        "start": float(start),
        "end": float(end),
        "verified": bool(matched.get("verified") is True),
    }


def _atomic_write_text(path: Path, text: str) -> None:
    _primitives_write_text_atomic(path, text)


def _pid_alive(pid) -> bool:
    return bool(operations._pid_alive(pid))


def _batch_identity(batch: Path) -> tuple[int, int] | None:
    try:
        if batch.is_symlink() or not batch.is_dir():
            return None
        st = batch.stat()
        return (st.st_dev, st.st_ino)
    except OSError:
        return None


def _batch_still_same(batch: Path, expected_identity: tuple[int, int] | None) -> bool:
    current = _batch_identity(batch)
    if current is None:
        return False
    if expected_identity is not None and current != expected_identity:
        return False
    return True


def is_running(batch_dir: Path) -> bool:
    """Whether an AI supplement task is actively running for this batch."""
    batch = Path(batch_dir).resolve()
    job_key = str(batch)
    with _JOBS_LOCK:
        thread = _ACTIVE_JOBS.get(job_key)
        if (thread is not None and thread.is_alive()) or (job_key in _RUNNING_BATCHES):
            return True
    lock_path = batch / SUPPLEMENT_LOCK_REL
    if lock_path.is_file():
        try:
            with supervisor._try_file_lock(lock_path) as acquired:
                if not acquired:
                    return True
        except OSError:
            return False
    return False


def has_active_jobs() -> bool:
    """Whether any AI supplement task is currently in flight in this backend process."""
    with _JOBS_LOCK:
        if _RUNNING_BATCHES:
            return True
        return any(thread.is_alive() for thread in _ACTIVE_JOBS.values())


def _target_input_fingerprint(mode: str, item: dict, sub: dict | None = None, *, transcript_text: str = "") -> str:
    uid = str(item.get("unit_id") or item.get("id") or "").strip()
    qtype = str(item.get("question_type") or item.get("type") or "").strip()
    material = str(item.get("material") or "").strip()
    if mode == "articleSummary":
        body = {
            "mode": mode,
            "unit_id": uid,
            "question_type": qtype,
            "material": material,
            "stem": str(item.get("stem") or "").strip(),
        }
    elif mode == "listeningScript":
        subs = [
            {
                "number": s.get("number"),
                "stem": str(s.get("stem") or "").strip(),
                "options": s.get("options") or [],
                "answer": s.get("answer") or "",
            }
            for s in (item.get("subquestions") or [])
            if isinstance(s, dict)
        ]
        body = {
            "mode": mode,
            "unit_id": uid,
            "question_type": qtype,
            "stem": str(item.get("stem") or "").strip(),
            "options": item.get("options") or [],
            "answer": item.get("answer") or "",
            "subquestions": subs,
            "transcript": transcript_text,
        }
    else:
        number = sub.get("number") if isinstance(sub, dict) else item.get("number")
        qid = _item_question_id(item, sub, uid, number)
        body = {
            "mode": mode,
            "unit_id": uid,
            "question_id": qid,
            "number": number,
            "question_type": qtype,
            "material": material,
            "stem": str((sub.get("stem") if isinstance(sub, dict) else None) or item.get("stem") or "").strip(),
            "options": (sub.get("options") if isinstance(sub, dict) else None) or item.get("options") or [],
            "answer": (sub.get("answer") if isinstance(sub, dict) else item.get("answer")) or "",
        }
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _valid_route(entry) -> bool:
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("provider"), str) and bool(entry["provider"].strip())
        and isinstance(entry.get("model"), str) and bool(entry["model"].strip())
    )


def resolve_model_info(mode: str) -> dict:
    """Resolve the effective provider/model for articleSummary or explanation."""
    agent = AGENT_BY_MODE.get(mode, "")
    plugin_root = Path(__file__).resolve().parents[2]
    router_path = local_agent._router_file(plugin_root)
    data = {}
    try:
        if router_path.is_file():
            parsed = json.loads(router_path.read_text(encoding="utf-8-sig"))
            if isinstance(parsed, dict):
                data = parsed
    except (OSError, ValueError):
        data = {}
    roles = data.get("roles") if isinstance(data.get("roles"), dict) else {}
    stages = data.get("stages") if isinstance(data.get("stages"), dict) else {}
    selected = None
    source = ""
    if agent and _valid_route(roles.get(agent)):
        selected = roles[agent]
        source = "custom"
    elif _valid_route(stages.get("ai_supplement")):
        selected = stages["ai_supplement"]
        source = "custom"
    elif _valid_route(data.get("default")):
        selected = data["default"]
        source = "default"
    else:
        try:
            route, _ = local_agent._harness_default_route()
            if _valid_route(route):
                selected = route
                source = "harness"
        except (OSError, ValueError, UnicodeError):
            selected = None
    provider = str(selected.get("provider") or "").strip() if isinstance(selected, dict) else ""
    model = str(selected.get("model") or "").strip() if isinstance(selected, dict) else ""
    display = f"{model} · {provider}" if (model and provider) else model
    return {
        "agent": agent,
        "provider": provider,
        "model": model,
        "model_display": display,
        "model_source": source,
    }


def _load_agent_config() -> dict:
    """Load plugin effective router config when available so model-router.json is honored."""
    if os.environ.get("MUDU_AGENT_CONFIG"):
        return local_agent.load_config()
    plugin_root = Path(__file__).resolve().parents[2]
    effective = plugin_root / "backend" / ".local-agent.effective.json"
    if effective.is_file():
        try:
            return local_agent.load_config(effective)
        except local_agent.AgentError:
            pass
    return local_agent.load_config()


@contextmanager
def content_write_lock(batch_dir: Path, timeout: float = 5.0, *, batch_identity: tuple[int, int] | None = None):
    """Short per-batch lock serializing millisecond commits of batch_meta/manual-content."""
    batch = Path(batch_dir).resolve()
    if not _batch_still_same(batch, batch_identity):
        raise ValueError(f"批次「{batch.name}」已被删除或替换，本次未写入")
    lock_path = batch / CONTENT_WRITE_LOCK_REL
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        if not _batch_still_same(batch, batch_identity):
            raise ValueError(f"批次「{batch.name}」已被删除或替换，本次未写入")
        with supervisor._try_file_lock(lock_path) as acquired:
            if acquired:
                if not _batch_still_same(batch, batch_identity):
                    raise ValueError(f"批次「{batch.name}」已被删除或替换，本次未写入")
                yield
                return
        if time.monotonic() >= deadline:
            raise ValueError(f"批次「{batch.name}」正在保存内容，请稍后重试")
        time.sleep(0.05)


def _progress_path(batch_dir: Path) -> Path:
    return Path(batch_dir) / PROGRESS_REL


def _pending_path(batch_dir: Path) -> Path:
    return Path(batch_dir) / PENDING_REL


def _load_pending(batch: Path, mode: str, binding: dict) -> dict[str, dict]:
    path = _pending_path(batch)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict) or data.get("mode") != mode or data.get("source_binding") != binding:
        return {}
    items = data.get("items")
    return items if isinstance(items, dict) else {}


def _save_pending(batch: Path, mode: str, binding: dict, generated: dict[str, str],
                  fingerprints: dict[str, str], *, batch_identity: tuple[int, int] | None = None) -> None:
    if not _batch_still_same(batch, batch_identity):
        return
    payload = {
        "mode": mode,
        "source_binding": binding,
        "items": {
            key: {"text": text, "input_fingerprint": fingerprints.get(key, "")}
            for key, text in generated.items()
            if key in fingerprints and text
        },
        "saved_at": mf.now_iso(),
    }
    _atomic_write_text(_pending_path(batch), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _clear_pending(batch: Path) -> None:
    path = _pending_path(batch)
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _write_progress(batch_dir: Path, state: dict, *, batch_identity: tuple[int, int] | None = None) -> dict:
    batch = Path(batch_dir).resolve()
    payload = {"pid": os.getpid(), **state, "updated_at": mf.now_iso()}
    if not _batch_still_same(batch, batch_identity):
        return payload
    path = _progress_path(batch)
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return payload


def read_progress(batch_dir: Path) -> dict:
    batch = Path(batch_dir).resolve()
    path = _progress_path(batch)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            if data.get("status") == "running":
                job_key = str(batch)
                with _JOBS_LOCK:
                    thread = _ACTIVE_JOBS.get(job_key)
                    alive_here = (thread is not None and thread.is_alive()) or (job_key in _RUNNING_BATCHES)
                if not alive_here:
                    pid = data.get("pid")
                    stale = (not isinstance(pid, int)) or (pid == os.getpid()) or (not _pid_alive(pid))
                    if stale:
                        return _write_progress(batch, {
                            **data,
                            "status": "error",
                            "message": "上次生成任务已中断",
                            "error": "后台生成任务已退出，请重新点击生成",
                        })
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"status": "idle", "mode": "", "total": 0, "completed": 0, "applied": 0, "message": "", "error": ""}


def _has_scoped_structure(batch_dir: Path) -> bool:
    path = Path(batch_dir) / "01_source" / "structure.confirmed.json"
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    units = data.get("units") if isinstance(data, dict) else None
    return isinstance(units, list) and any(
        isinstance(u, dict) and u.get("identity_version") == "scoped-v1" for u in units
    )


def _source_coverage(batch_dir: Path) -> dict:
    """Return original explanation/summary coverage keyed by unit/question_id (scoped-v1) or int number (legacy)."""
    empty = {"scoped": False, "expl_keys": set(), "summ_units": set(), "expl_nums": set(), "summ_nums": set()}
    scoped = _has_scoped_structure(batch_dir)
    try:
        index, summaries = upload_content.collect_source_index(Path(batch_dir))
    except (OSError, ValueError, KeyError, TypeError):
        return empty
    expl_keys: set[str] = set()
    expl_nums: set[int] = set()
    for key, text in (index or {}).items():
        text_s = str(text or "").strip()
        if not text_s or content_rules.is_placeholder(text_s):
            continue
        if isinstance(key, str):
            expl_keys.add(key)
        elif isinstance(key, int) or str(key).isdigit():
            expl_nums.add(int(key))
    summ_units: set[str] = set()
    summ_nums: set[int] = set()
    for row in (summaries or []):
        if not isinstance(row, dict):
            continue
        text_s = str(row.get("source_text") or row.get("text") or "").strip()
        if not text_s or content_rules.is_placeholder(text_s):
            continue
        uid = str(row.get("unit_id") or "").strip()
        if uid:
            summ_units.add(uid)
        for n in (row.get("members") or []):
            if str(n).isdigit():
                summ_nums.add(int(n))
    return {
        "scoped": scoped or bool(expl_keys or summ_units),
        "expl_keys": expl_keys,
        "summ_units": summ_units,
        "expl_nums": expl_nums,
        "summ_nums": summ_nums,
    }


def _item_question_id(item: dict, sub: dict | None, uid: str, number) -> str:
    if isinstance(sub, dict) and sub.get("question_id"):
        return str(sub["question_id"])
    if sub is None and item.get("question_id"):
        return str(item["question_id"])
    qids = item.get("question_ids")
    if isinstance(qids, dict) and number is not None:
        matched = qids.get(str(number))
        if matched:
            return str(matched)
    if number is not None:
        return f"{uid}#{number}"
    return uid


def eligible_targets(batch_dir: Path, mode: str) -> list[dict]:
    """Identify eligible missing or AI-generated targets in ``06_batch/batch_meta.json``."""
    if mode not in ("articleSummary", "explanation", "listeningScript"):
        raise ValueError("不支持的补充模式：" + str(mode))
    batch_path = Path(batch_dir) / "06_batch" / "batch_meta.json"
    if not batch_path.is_file():
        raise ValueError("正式批次文件 06_batch/batch_meta.json 不存在，无法补充")
    payload = json.loads(batch_path.read_text(encoding="utf-8-sig"))
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError("06_batch/batch_meta.json 格式无效")

    coverage = _source_coverage(Path(batch_dir))
    original_listening_scripts: dict = {}
    if mode == "listeningScript":
        try:
            from .workflow_primitives import confirmed_units as load_confirmed_units
            original_listening_scripts = listening_script.extract(Path(batch_dir), load_confirmed_units(Path(batch_dir)))
        except (OSError, ValueError, KeyError, TypeError):
            original_listening_scripts = {}
    targets: list[dict] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        uid = str(item.get("unit_id") or item.get("id") or "").strip()
        if not uid:
            continue
        qtype = str(item.get("question_type") or item.get("type") or "").strip()
        material = str(item.get("material") or "").strip()
        item_scoped = coverage["scoped"] or item.get("identity_version") == "scoped-v1"

        if mode == "listeningScript":
            if not qtype.startswith("听力"):
                continue
            if uid in original_listening_scripts:
                continue
            cur_expl = item.get("explanation", "")
            if not _is_listening_script_replaceable(cur_expl):
                continue
            excerpt = _extract_unit_transcript(Path(batch_dir), uid)
            if not excerpt:
                continue
            subs_ref = [
                {
                    "number": s.get("number"),
                    "stem": str(s.get("stem") or "").strip()[:400],
                    "options": s.get("options") or [],
                    "answer": s.get("answer") or "",
                }
                for s in (item.get("subquestions") or [])
                if isinstance(s, dict)
            ]
            targets.append({
                "key": uid,
                "unit_id": uid,
                "number": item.get("number"),
                "question_type": qtype,
                "stem": str(item.get("stem") or "").strip()[:600],
                "options": item.get("options") or [],
                "answer": item.get("answer") or "",
                "subquestions": subs_ref,
                "transcript": excerpt["raw_text"],
                "transcript_verified": excerpt["verified"],
                "existing_explanation": str(cur_expl or ""),
                "input_fingerprint": _target_input_fingerprint(
                    "listeningScript", item, None, transcript_text=excerpt["raw_text"]
                ),
            })
        elif mode == "articleSummary":
            if item.get("unit_kind") != "group" and not item.get("subquestions"):
                continue
            words = _word_count(material)
            min_words = MIN_SUMMARY_WORDS_ELIGIBLE if qtype in SUMMARY_ELIGIBLE_TYPES else MIN_SUMMARY_WORDS_GENERAL
            if words < min_words:
                continue
            current = item.get("articleSummary", item.get("article_summary", ""))
            if not _is_replaceable(current):
                continue
            if item_scoped:
                if uid in coverage["summ_units"]:
                    continue
            else:
                nums = [int(n) for n in (item.get("numbers") or []) if str(n).isdigit()]
                if not nums and isinstance(item.get("subquestions"), list):
                    nums = [int(s["number"]) for s in item["subquestions"] if isinstance(s, dict) and str(s.get("number")).isdigit()]
                if nums and all(n in coverage["summ_nums"] for n in nums):
                    continue
            targets.append({
                "key": uid,
                "unit_id": uid,
                "number": None,
                "question_type": qtype,
                "material": material,
                "stem": str(item.get("stem") or "").strip(),
                "input_fingerprint": _target_input_fingerprint("articleSummary", item, None),
            })
        else:
            if any(hint in qtype for hint in EXCLUDED_EXPLANATION_TYPE_HINTS):
                continue
            subs = item.get("subquestions")
            if isinstance(subs, list) and subs:
                for idx, sub in enumerate(subs):
                    if not isinstance(sub, dict):
                        continue
                    number = sub.get("number")
                    if item_scoped:
                        qid = _item_question_id(item, sub, uid, number)
                        if qid in coverage["expl_keys"]:
                            continue
                    elif number is not None and str(number).isdigit() and int(number) in coverage["expl_nums"]:
                        continue
                    if not _is_replaceable(sub.get("explanation")):
                        continue
                    sub_key = f"{uid}/{number if number is not None else idx}"
                    targets.append({
                        "key": sub_key,
                        "unit_id": uid,
                        "number": number,
                        "question_type": qtype,
                        "material": material,
                        "stem": str(sub.get("stem") or item.get("stem") or "").strip(),
                        "options": sub.get("options") or item.get("options") or [],
                        "answer": sub.get("answer") or "",
                        "input_fingerprint": _target_input_fingerprint("explanation", item, sub),
                    })
            else:
                number = item.get("number")
                if item_scoped:
                    qid = _item_question_id(item, None, uid, number)
                    if qid in coverage["expl_keys"]:
                        continue
                elif number is not None and str(number).isdigit() and int(number) in coverage["expl_nums"]:
                    continue
                if not _is_replaceable(item.get("explanation")):
                    continue
                targets.append({
                    "key": uid,
                    "unit_id": uid,
                    "number": number,
                    "question_type": qtype,
                    "material": material,
                    "stem": str(item.get("stem") or "").strip(),
                    "options": item.get("options") or [],
                    "answer": item.get("answer") or "",
                    "input_fingerprint": _target_input_fingerprint("explanation", item, None),
                })
    return targets


def _build_prompt(mode: str, targets: list[dict]) -> str:
    agent = AGENT_BY_MODE.get(mode, "ai_supplement")
    if mode == "listeningScript":
        instruction = (
            "Stage: ai_supplement\n"
            f"Agent: {agent}\n"
            "任务：根据本地语音转写片段（transcript），为以下缺少听力原文的中考英语听力题整理干净、完整的英文听力原文。\n"
            "硬性要求：\n"
            "1. 只返回严格 JSON 对象：{\"status\": \"completed\", \"artifacts\": {}, \"result\": {\"items\": [{\"key\": \"...\", \"text\": \"...\"}]}}\n"
            "2. **读两遍去重（核心）**：中考听力录音通常会把同一段对话或短文连续朗读两遍，且两遍转写可能略有词差。请比对两遍转写，合并去重为完整的一遍原文，择优保留更准确的一遍，绝不能输出重复两遍的内容。\n"
            "3. **去除考场播报与时间戳**：删除所有中英文指令播报（如「第一节」「听下面一段对话」「回答第X至Y小题」「Question N」「Now you have 5 seconds...」「下面请听第二遍」等）及时间戳标记。\n"
            "4. **规范排版**：短对话/长对话按说话人换行整理（如 M: ... / W: ...，无法确认性别时可按轮次换行）；短文/信息转换整理为通顺自然段并补全标点。不要截断正文。\n"
            "5. **忠实于录音转写**：严禁脱离转写凭空编造句子；可结合题干/选项/答案对人名、地名、数字、同音词做最小纠错，确保与录音原意一致。若 transcript_verified 为 false（切片边界为等分推算），请重点依据题干/选项剔除混入的邻题句子。\n"
            "6. 只输出整理后的英文听力原文正文，不要输出「听力录音材料：」前缀，也不要输出「（AI转录，待校对）」后缀（程序会统一标注）。\n\n"
            "BEGIN SUPPLIED CONTENT\n"
        )
        payload = [
            {
                "key": t["key"],
                "question_type": t["question_type"],
                "number": t.get("number"),
                "stem": t.get("stem") or "",
                "options": t.get("options") or [],
                "answer": t.get("answer") or "",
                "subquestions": t.get("subquestions") or [],
                "transcript_verified": bool(t.get("transcript_verified")),
                "transcript": str(t.get("transcript") or "")[:6000],
            }
            for t in targets
        ]
    elif mode == "articleSummary":
        instruction = (
            "Stage: ai_supplement\n"
            f"Agent: {agent}\n"
            f"任务：为以下英语长材料阅读/完形题组生成简明中文【文章大意】。\n"
            f"硬性要求：\n"
            f"1. 只返回严格 JSON 对象：{{\"status\": \"completed\", \"artifacts\": {{}}, \"result\": {{\"items\": [{{\"key\": \"...\", \"text\": \"...\"}}]}}}}\n"
            f"2. text 必须简洁直白，**严格不超过 {MAX_BODY_CHARS} 个汉字/字符**（1 句话概括主旨即可，绝不要写长）。\n"
            f"3. 只写正文，不要包含「文章大意：」「【文章大意】」等前缀。\n"
            f"4. 严格依据所给材料概括，不得编造材料中没有的细节。\n\n"
            "BEGIN SUPPLIED CONTENT\n"
        )
        payload = [
            {"key": t["key"], "question_type": t["question_type"], "material": t["material"][:3500]}
            for t in targets
        ]
    else:
        instruction = (
            "Stage: ai_supplement\n"
            f"Agent: {agent}\n"
            f"任务：为以下英语题目生成简洁直白的中文解析。\n"
            f"硬性要求：\n"
            f"1. 只返回严格 JSON 对象：{{\"status\": \"completed\", \"artifacts\": {{}}, \"result\": {{\"items\": [{{\"key\": \"...\", \"text\": \"...\"}}]}}}}\n"
            f"2. text 必须简单明了，**严格不超过 {MAX_BODY_CHARS} 个汉字/字符**（只点出核心考点与答案依据，不要复杂长句）。\n"
            f"3. 只写正文，不要包含「解析：」「【解析】」等前缀。\n"
            f"4. 必须与所给参考答案一致，不得编造矛盾结论。\n\n"
            "BEGIN SUPPLIED CONTENT\n"
        )
        payload = [
            {
                "key": t["key"],
                "question_type": t["question_type"],
                "number": t["number"],
                "material": t["material"][:2500] if t["material"] else "",
                "stem": t["stem"][:1200],
                "options": t.get("options") or [],
                "answer": t.get("answer") or "",
            }
            for t in targets
        ]
    return instruction + json.dumps({"targets": payload}, ensure_ascii=False, indent=2) + "\n"


def _unwrap_payload(resp):
    cur = resp
    for _ in range(4):
        if isinstance(cur, str):
            text = cur.strip()
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
            try:
                cur = json.loads(text)
            except ValueError:
                break
        if isinstance(cur, dict):
            if "items" in cur:
                return cur
            if "response" in cur and cur["response"] is not None:
                cur = cur["response"]
                continue
            if "result" in cur and cur["result"] is not None:
                cur = cur["result"]
                continue
        break
    return cur


def _parse_generated(result: dict, mode: str = "explanation") -> dict[str, str]:
    rows = result.get("items") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        raise ValueError("模型返回格式无效：缺少 items 列表")
    out: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "").strip()
        raw_val = str(row.get("text") or "")
        text = _clean_listening_script_body(raw_val) if mode == "listeningScript" else _format_ai_text(raw_val)
        if key and text:
            out[key] = text
    return out


def _sync_explanations_artifact(batch: Path, updates: dict[str, str], mode: str) -> None:
    exp_path = batch / "04_knowledge" / "explanations.json"
    if not exp_path.is_file():
        return
    try:
        data = json.loads(exp_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return
    by_uid = {str(it.get("unit_id")): it for it in items if isinstance(it, dict) and it.get("unit_id")}
    target_field = "explanation" if mode == "listeningScript" else mode
    changed = False
    for key, text in updates.items():
        if "/" in key:
            uid, num_str = key.split("/", 1)
            row = by_uid.get(uid)
            if not row:
                continue
            for idx, sub in enumerate(row.get("subquestions") or []):
                if isinstance(sub, dict) and str(sub.get("number") if sub.get("number") is not None else idx) == num_str:
                    sub["explanation"] = text
                    changed = True
        else:
            row = by_uid.get(key)
            if row:
                row[target_field] = text
                changed = True
    if not changed:
        return
    raw = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    exp_path.write_text(raw, encoding="utf-8")
    manifest_path = batch / "workflow" / "manifest.json"
    if manifest_path.is_file():
        current = mf.load(batch)
        record = current.get("stages", {}).get("explanations", {})
        artifact = "04_knowledge/explanations.json"
        if record.get("status") in mf.TERMINAL_OK:
            record["output_hashes"] = {artifact: hashlib.sha256(raw.replace("\n", os.linesep).encode("utf-8")).hexdigest()}
            if record.get("semantic_version") == stage_semantics.VERSION:
                record["semantic_outputs"] = {artifact: stage_semantics.digest(stage_semantics.without_clocks(data))}
            mf.record_decision(current, "explanations", "ai_supplement", "workbench",
                               f"{mode}: {len(updates)} 项", [artifact, manual_content.RELATIVE])
            mf.save(batch, current)


def _check_upload_editable(batch: Path) -> None:
    manifest_path = batch / "workflow" / "manifest.json"
    if manifest_path.is_file():
        current = mf.load(batch)
        stages = current.get("stages", {})
        if (stages.get("upload_gate", {}).get("status") == mf.APPROVED
                or stages.get("upload", {}).get("status") == mf.COMPLETED):
            raise ValueError("此批次已批准上传或已上传，不能补充覆盖已绑定内容")


def _sync_batch_and_review_receipt_hashes(batch: Path, fresh: dict, *, old_batch_sha: str | None = None) -> None:
    """Keep assemble/underlines output_hashes and review-gate.json batch_sha256 bound to batch_meta.json."""
    batch_path = batch / "06_batch" / "batch_meta.json"
    if not batch_path.is_file():
        return
    new_batch_sha = mf.sha256_file(batch_path)
    stages = fresh.get("stages") if isinstance(fresh, dict) else None
    if isinstance(stages, dict):
        for sid in ("assemble", "underlines"):
            rec = stages.get(sid)
            if not isinstance(rec, dict) or rec.get("status") != mf.COMPLETED:
                continue
            out_hashes = dict(rec.get("output_hashes") or {})
            if "06_batch/batch_meta.json" in out_hashes:
                out_hashes["06_batch/batch_meta.json"] = new_batch_sha
                rec["output_hashes"] = out_hashes
    rg_path = batch / "07_review" / "review-gate.json"
    if rg_path.is_file() and not rg_path.is_symlink():
        try:
            rg = json.loads(rg_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            rg = None
        if isinstance(rg, dict) and rg.get("batch_sha256"):
            prev_sha = rg.get("batch_sha256")
            if (not old_batch_sha or prev_sha == old_batch_sha) and prev_sha != new_batch_sha:
                rg["batch_sha256"] = new_batch_sha
                _atomic_write_text(rg_path, json.dumps(rg, ensure_ascii=False, indent=2) + "\n")
                if isinstance(stages, dict) and isinstance(stages.get("review_gate"), dict) and stages["review_gate"].get("output_hashes"):
                    stages["review_gate"]["output_hashes"] = mf.output_hashes(batch, contracts.STAGE_BY_ID["review_gate"])


def _refresh_post_edit_gates(batch: Path, *, lock_held: bool = False, old_batch_sha: str | None = None) -> tuple[int | None, bool | None]:
    """Re-run validate and refresh format_gate when already completed so local_complete stays intact.

    Uses only the per-batch `.run.lock` (never the global cross-batch `_agent_lock`)
    so model runs on unrelated batches never stall post-edit validation.
    Post-completion AI supplements must never demote an already-finished batch back
    into the processing queue (排队区) when validation/format_gate hits an unrelated
    or pre-existing check failure.
    """
    manifest_path = batch / "workflow" / "manifest.json"
    if not manifest_path.is_file():
        return None, None
    try:
        lock_ctx = nullcontext() if lock_held else runner._single_run_lock(batch, timeout=5.0)
        with lock_ctx:
            current = mf.load(batch)
            stages = current.get("stages") or {}
            was_validate_done = (stages.get("validate") or {}).get("status") == mf.COMPLETED
            was_format_done = (stages.get("format_gate") or {}).get("status") == mf.COMPLETED
            prev_validate = copy.deepcopy(stages.get("validate")) if was_validate_done else None
            prev_format = copy.deepcopy(stages.get("format_gate")) if was_format_done else None
            validate_code = runner.do_validate(batch, current, lock_held=True, refresh_only=True)
            format_passed: bool | None = None
            if was_format_done:
                # 先在内存跑 format_gate，校验通过再覆盖落盘；失败时绝不覆写磁盘上已通过的
                # 07_review/upload-format.json，避免 execution_state 把已完成批次误判回排队区。
                report_file = batch / format_gate.REPORT_PATH
                prev_report_bytes = report_file.read_bytes() if report_file.is_file() else None
                deferred_write = False
                try:
                    report = format_gate.run(batch, write_report=False)
                    deferred_write = True
                except TypeError:
                    report = format_gate.run(batch)
                format_passed = bool(report.get("passed") is True)
                fresh = mf.load(batch)
                spec = contracts.STAGE_BY_ID["format_gate"]
                if format_passed and validate_code == 0:
                    _sync_batch_and_review_receipt_hashes(batch, fresh, old_batch_sha=old_batch_sha)
                    if deferred_write:
                        format_gate.write_report_file(batch, report)
                    mf.touch_stage(
                        fresh, "format_gate", mf.COMPLETED,
                        input_hashes=mf.input_hashes(batch, fresh, spec),
                        output_hashes=mf.output_hashes(batch, spec),
                        exit_code=0, kind=spec.kind, notes="",
                    )
                    # 批次已完成后的人工编辑/AI补充只刷新校验哈希，不推迟原流程完成时间与耗时。
                    if isinstance(prev_format, dict):
                        fg_rec = fresh.setdefault("stages", {}).setdefault("format_gate", {})
                        if prev_format.get("finished_at"):
                            fg_rec["finished_at"] = prev_format["finished_at"]
                        if prev_format.get("started_at"):
                            fg_rec["started_at"] = prev_format["started_at"]
                    mf.record_semantics(batch, fresh, spec)
                else:
                    # Keep already-completed batch in review workbench instead of kicking it back to queue.
                    if not deferred_write and prev_report_bytes is not None:
                        _atomic_write_bytes(report_file, prev_report_bytes)
                    if prev_validate is not None and validate_code != 0:
                        fresh.setdefault("stages", {})["validate"] = prev_validate
                    if prev_format is not None:
                        fresh.setdefault("stages", {})["format_gate"] = prev_format
                    fresh["status"] = mf.derive_status(fresh)
                mf.save(batch, fresh)
            elif prev_validate is not None and validate_code != 0:
                fresh = mf.load(batch)
                fresh.setdefault("stages", {})["validate"] = prev_validate
                fresh["status"] = mf.derive_status(fresh)
                mf.save(batch, fresh)
            elif validate_code == 0:
                fresh = mf.load(batch)
                _sync_batch_and_review_receipt_hashes(batch, fresh, old_batch_sha=old_batch_sha)
                mf.save(batch, fresh)
            return validate_code, format_passed
    except RuntimeError:
        return "deferred", None


def supplement(batch_dir: Path, mode: str, *, invoke_fn=None, job_id: str | None = None,
               batch_identity: tuple[int, int] | None = None) -> dict:
    """Generate/regenerate and apply AI articleSummary or explanation independently from main pipeline."""
    batch = Path(batch_dir).resolve()
    identity = batch_identity if batch_identity is not None else _batch_identity(batch)
    if identity is None:
        raise ValueError(f"批次「{batch.name}」不存在或已被删除")
    _check_upload_editable(batch)
    manifest_path = batch / "workflow" / "manifest.json"
    model_info = resolve_model_info(mode)
    meta_fields = {
        "job_id": job_id or uuid.uuid4().hex[:12],
        "model": model_info["model"],
        "provider": model_info["provider"],
        "model_display": model_info["model_display"],
        "model_source": model_info["model_source"],
    }

    targets = eligible_targets(batch, mode)
    total = len(targets)
    if not targets:
        res = {**meta_fields, "applied": 0, "mode": mode, "keys": [], "total": 0, "completed": 0,
               "status": "done", "message": "当前批次没有需要补充或重新生成的题目", "error": ""}
        _write_progress(batch, res, batch_identity=identity)
        return res

    initial_binding = manual_content.source_binding(batch)
    target_fingerprints = {t["key"]: t["input_fingerprint"] for t in targets}

    # Reuse bound pending candidates from a prior busy-commit attempt if inputs match.
    pending_items = _load_pending(batch, mode, initial_binding)
    generated: dict[str, str] = {}
    remaining_targets: list[dict] = []
    for t in targets:
        cached = pending_items.get(t["key"])
        if (isinstance(cached, dict) and cached.get("input_fingerprint") == t["input_fingerprint"]
                and isinstance(cached.get("text"), str) and cached["text"].strip()):
            generated[t["key"]] = cached["text"]
        else:
            remaining_targets.append(t)

    job_key = str(batch)
    with _JOBS_LOCK:
        _RUNNING_BATCHES.add(job_key)
    try:
        model_hint = f"（{model_info['model']}）" if model_info["model"] else ""
        done_cnt = len(generated)
        _write_progress(batch, {
            **meta_fields, "status": "running", "mode": mode, "total": total, "completed": done_cnt,
            "applied": len(generated), "message": f"正在准备生成{model_hint}（共 {total} 项）…", "error": "",
        }, batch_identity=identity)

        if remaining_targets:
            caller = invoke_fn
            max_workers = DEFAULT_SUPPLEMENT_CONCURRENCY
            if caller is None:
                config = _load_agent_config()
                config = dict(config, _content_batch_dir=str(batch))
                max_workers = max(1, min(DEFAULT_SUPPLEMENT_CONCURRENCY, int(config.get("part_concurrency", DEFAULT_SUPPLEMENT_CONCURRENCY) or DEFAULT_SUPPLEMENT_CONCURRENCY)))

                def caller(prompt_text: str, run_dir: Path) -> dict:
                    # Run as an independent task: never acquire the main pipeline's global _agent_lock.
                    return local_agent.invoke(
                        prompt_text, run_dir, config,
                        permit_parallel=True, coordinated_imports=True,
                    )

            chunk_size = 10 if mode == "explanation" else (4 if mode == "listeningScript" else 5)
            chunks = [remaining_targets[idx:idx + chunk_size] for idx in range(0, len(remaining_targets), chunk_size)]
            progress_lock = threading.Lock()
            try:
                with tempfile.TemporaryDirectory(prefix="ai_supplement_") as tmp:
                    def _run_chunk(chunk_idx: int, chunk: list[dict]) -> tuple[int, dict[str, str]]:
                        prompt = _build_prompt(mode, chunk)
                        run_dir = Path(tmp) / f"chunk_{chunk_idx}"
                        resp = caller(prompt, run_dir)
                        payload = _unwrap_payload(resp)
                        return len(chunk), _parse_generated(payload, mode=mode)

                    if len(chunks) <= 1 or max_workers <= 1:
                        for chunk_idx, chunk in enumerate(chunks):
                            if not _batch_still_same(batch, identity):
                                raise ValueError(f"批次「{batch.name}」已被删除或替换，已停止生成")
                            size, items_out = _run_chunk(chunk_idx, chunk)
                            generated.update(items_out)
                            done_cnt += size
                            _write_progress(batch, {
                                **meta_fields, "status": "running", "mode": mode, "total": total,
                                "completed": done_cnt, "applied": len(generated),
                                "message": f"正在生成{model_hint} {done_cnt}/{total} 项…", "error": "",
                            }, batch_identity=identity)
                    else:
                        workers = min(max_workers, len(chunks))
                        with ThreadPoolExecutor(max_workers=workers) as pool:
                            futures = [pool.submit(_run_chunk, idx, ch) for idx, ch in enumerate(chunks)]
                            for fut in as_completed(futures):
                                size, items_out = fut.result()
                                with progress_lock:
                                    generated.update(items_out)
                                    done_cnt += size
                                    _write_progress(batch, {
                                        **meta_fields, "status": "running", "mode": mode, "total": total,
                                        "completed": done_cnt, "applied": len(generated),
                                        "message": f"正在生成{model_hint} {done_cnt}/{total} 项…", "error": "",
                                    }, batch_identity=identity)
            except Exception as exc:  # noqa: BLE001
                _write_progress(batch, {
                    **meta_fields, "status": "error", "mode": mode, "total": total, "completed": done_cnt,
                    "applied": len(generated), "message": "生成失败", "error": str(exc),
                }, batch_identity=identity)
                raise

        if not _batch_still_same(batch, identity):
            return {**meta_fields, "applied": 0, "mode": mode, "keys": [], "total": total, "completed": done_cnt,
                    "status": "cancelled", "message": "批次已删除，已放弃写回", "error": ""}

        if not generated:
            res = {**meta_fields, "applied": 0, "mode": mode, "keys": [], "total": total, "completed": total,
                   "status": "done", "message": "模型未返回有效补充内容", "error": ""}
            _write_progress(batch, res, batch_identity=identity)
            return res

        # Short commit critical section: serialize with content-edit & avoid racing with main pipeline on the same batch.
        applied_keys: list[str] = []
        skipped_changed = 0
        with ExitStack() as locks:
            locks.enter_context(content_write_lock(batch, timeout=5.0, batch_identity=identity))
            run_lock = batch / "workflow" / ".run.lock"
            if run_lock.exists():
                # Try briefly to acquire .run.lock; if busy, persist bound candidates so work is not lost.
                acquired_run = False
                for attempt in range(4):
                    if locks.enter_context(supervisor._try_file_lock(run_lock)):
                        acquired_run = True
                        break
                    if attempt < 3:
                        time.sleep(0.1)
                if not acquired_run:
                    _save_pending(batch, mode, initial_binding, generated, target_fingerprints, batch_identity=identity)
                    err_msg = f"批次「{batch.name}」正在执行主流程写入，已暂存 {len(generated)} 项生成结果，稍后重新点击可直接落盘"
                    _write_progress(batch, {
                        **meta_fields, "status": "error", "mode": mode, "total": total, "completed": total,
                        "applied": 0, "pending_saved": len(generated),
                        "message": "主流程写入中，结果已暂存", "error": err_msg,
                    }, batch_identity=identity)
                    raise ValueError(err_msg)
            if not _batch_still_same(batch, identity):
                return {**meta_fields, "applied": 0, "mode": mode, "keys": [], "total": total, "completed": total,
                        "status": "cancelled", "message": "批次已删除，已放弃写回", "error": ""}
            _check_upload_editable(batch)

            current_binding = manual_content.source_binding(batch)
            if current_binding != initial_binding:
                _clear_pending(batch)
                res = {**meta_fields, "applied": 0, "mode": mode, "keys": [], "total": total, "completed": total,
                       "status": "done", "skipped_changed": len(generated),
                       "message": "生成期间批次原卷/答案来源已变化，已跳过写入旧结果", "error": ""}
                _write_progress(batch, res, batch_identity=identity)
                return res

            batch_path = batch / "06_batch" / "batch_meta.json"
            batch_data = json.loads(batch_path.read_text(encoding="utf-8-sig"))
            by_uid = {
                str(it.get("unit_id") or it.get("id")): it
                for it in (batch_data.get("items") or [])
                if isinstance(it, dict) and (it.get("unit_id") or it.get("id"))
            }
            applied_updates: dict[str, str] = {}
            manual_data = manual_content.load(batch)
            binding = current_binding

            for target in targets:
                key = target["key"]
                text = generated.get(key)
                if not text:
                    continue
                if "/" in key:
                    uid, num_str = key.split("/", 1)
                    item = by_uid.get(uid)
                    if not item:
                        continue
                    updated_sub = False
                    for idx, sub in enumerate(item.get("subquestions") or []):
                        if isinstance(sub, dict) and str(sub.get("number") if sub.get("number") is not None else idx) == num_str:
                            if _target_input_fingerprint("explanation", item, sub) != target["input_fingerprint"]:
                                skipped_changed += 1
                            elif _is_replaceable(sub.get("explanation")):
                                sub["explanation"] = text
                                updated_sub = True
                            break
                    if not updated_sub:
                        continue
                    field = "explanation"
                else:
                    item = by_uid.get(key)
                    if not item:
                        continue
                    if mode == "listeningScript":
                        field = "explanation"
                        if _target_input_fingerprint("listeningScript", item, None, transcript_text=target.get("transcript", "")) != target["input_fingerprint"]:
                            skipped_changed += 1
                            continue
                        cur_val = item.get("explanation", "")
                        if not _is_listening_script_replaceable(cur_val):
                            continue
                        text = _format_listening_script(text, cur_val)
                        if not text:
                            continue
                        item[field] = text
                    else:
                        field = mode
                        if _target_input_fingerprint(field, item, None) != target["input_fingerprint"]:
                            skipped_changed += 1
                            continue
                        cur_val = item.get("articleSummary", item.get("article_summary", "")) if field == "articleSummary" else item.get("explanation", "")
                        if not _is_replaceable(cur_val):
                            continue
                        item[field] = text
                previous = manual_data["edits"].get(key)
                fields = {k: previous[k] for k in manual_content.FIELDS if k in previous} if (
                    isinstance(previous, dict) and previous.get("source_binding") == binding) else {}
                fields[field] = text
                manual_data["edits"][key] = {
                    **fields,
                    "source_binding": binding,
                    "updated_by": "workbench",
                    "ai_supplement": True,
                    "updated_at": mf.now_iso(),
                }
                applied_keys.append(key)
                applied_updates[key] = text

            _clear_pending(batch)
            if not applied_keys:
                msg = (f"生成期间 {skipped_changed} 项题目内容或答案已变化，已跳过写入旧结果"
                       if skipped_changed else "无可应用的新增补充内容")
                res = {**meta_fields, "applied": 0, "mode": mode, "keys": [], "total": total, "completed": total,
                       "skipped_changed": skipped_changed, "status": "done", "message": msg, "error": ""}
                _write_progress(batch, res, batch_identity=identity)
                return res

            old_batch_sha = mf.sha256_file(batch_path) if batch_path.is_file() else None
            _atomic_write_text(batch_path, json.dumps(batch_data, ensure_ascii=False, indent=2) + "\n")
            manual_path = batch / manual_content.RELATIVE
            _atomic_write_text(manual_path, json.dumps(manual_data, ensure_ascii=False, indent=2) + "\n")
            _sync_explanations_artifact(batch, applied_updates, mode)

        # Run validation & format_gate refresh outside the short write lock so waiting never blocks content edits.
        validate_code = None
        format_passed = None
        if _batch_still_same(batch, identity) and manifest_path.is_file():
            _write_progress(batch, {
                **meta_fields, "status": "running", "mode": mode, "total": total,
                "completed": total, "applied": len(applied_keys),
                "message": f"已写入 {len(applied_keys)} 项，正在刷新本地体检与格式检查…", "error": "",
            }, batch_identity=identity)
            validate_code, format_passed = _refresh_post_edit_gates(batch, lock_held=False, old_batch_sha=old_batch_sha)

        recheck_deferred = validate_code == "deferred"
        label = "听力材料转录" if mode == "listeningScript" else ("文章大意" if mode == "articleSummary" else "解析")
        skip_note = f"（跳过 {skipped_changed} 项已变更题目）" if skipped_changed else ""
        recheck_note = "（后台复验因主流程占锁暂未执行，内容已写回）" if recheck_deferred else ""
        done_msg = (
            f"已完成 {len(applied_keys)} 项听力材料 AI 转录补全并标注（AI转录）{skip_note}{recheck_note}"
            if mode == "listeningScript"
            else f"已完成 {len(applied_keys)} 项 AI {label}生成并标注{skip_note}{recheck_note}"
        )
        res = {
            **meta_fields,
            "status": "done",
            "applied": len(applied_keys),
            "skipped_changed": skipped_changed,
            "total": total,
            "completed": total,
            "mode": mode,
            "keys": applied_keys,
            "validate_passed": (validate_code == 0) if isinstance(validate_code, int) else None,
            "format_gate_passed": format_passed,
            "recheck_deferred": recheck_deferred,
            "message": done_msg,
            "error": "",
        }
        _write_progress(batch, res, batch_identity=identity)
        return res
    finally:
        with _JOBS_LOCK:
            _RUNNING_BATCHES.discard(job_key)


def start_background(batch_dir: Path, mode: str, *, invoke_fn=None) -> dict:
    """Launch independent background AI supplement task without holding main pipeline locks."""
    batch = Path(batch_dir).resolve()
    identity = _batch_identity(batch)
    if identity is None:
        raise ValueError(f"批次「{batch.name}」不存在或已被删除")
    _check_upload_editable(batch)
    model_info = resolve_model_info(mode)
    meta_fields = {
        "model": model_info["model"],
        "provider": model_info["provider"],
        "model_display": model_info["model_display"],
        "model_source": model_info["model_source"],
    }
    targets = eligible_targets(batch, mode)
    total = len(targets)
    if not targets:
        return _write_progress(batch, {
            **meta_fields, "status": "done", "mode": mode, "total": 0, "completed": 0,
            "applied": 0, "keys": [], "message": "当前批次没有需要补充或重新生成的题目", "error": "",
        }, batch_identity=identity)

    job_key = str(batch)
    already_running = False
    with _JOBS_LOCK:
        existing = _ACTIVE_JOBS.get(job_key)
        if existing is not None and existing.is_alive():
            already_running = True
        else:
            job_id = uuid.uuid4().hex[:12]
            model_hint = f"（{model_info['model']}）" if model_info["model"] else ""
            initial = _write_progress(batch, {
                **meta_fields, "job_id": job_id,
                "status": "running", "mode": mode, "total": total, "completed": 0,
                "applied": 0, "message": f"正在启动独立生成任务{model_hint}（共 {total} 项）…", "error": "",
            }, batch_identity=identity)

            def _worker():
                try:
                    if not _batch_still_same(batch, identity):
                        return
                    supp_lock = batch / SUPPLEMENT_LOCK_REL
                    with supervisor._try_file_lock(supp_lock) as acquired:
                        if not acquired:
                            raise ValueError(f"批次「{batch.name}」已有 AI 补充任务正在运行")
                        supplement(batch, mode, invoke_fn=invoke_fn, job_id=job_id, batch_identity=identity)
                except Exception as exc:  # noqa: BLE001
                    cur = read_progress(batch) if _batch_still_same(batch, identity) else {}
                    if cur.get("status") != "error" or cur.get("job_id") != job_id:
                        _write_progress(batch, {
                            **meta_fields, "job_id": job_id,
                            "status": "error", "mode": mode, "total": total,
                            "completed": int(cur.get("completed") or 0),
                            "applied": 0, "message": "生成失败", "error": str(exc),
                        }, batch_identity=identity)
                finally:
                    with _JOBS_LOCK:
                        if _ACTIVE_JOBS.get(job_key) is threading.current_thread():
                            _ACTIVE_JOBS.pop(job_key, None)

            thread = threading.Thread(target=_worker, name=f"ai-supplement-{batch.name}", daemon=True)
            _ACTIVE_JOBS[job_key] = thread
            thread.start()
            return initial
    if already_running:
        return read_progress(batch)
    return read_progress(batch)


def save_content_edit(batch_dir: Path, unit_id: str, patch: dict, *, lock_held: bool = False) -> dict:
    """Directly persist workbench edits to articleSummary / explanation / answer."""
    batch = Path(batch_dir).resolve()
    _check_upload_editable(batch)
    uid = str(unit_id or "").strip()
    if not uid:
        raise ValueError("缺少 unit_id")
    if not isinstance(patch, dict):
        raise ValueError("修改内容必须是对象")

    batch_path = batch / "06_batch" / "batch_meta.json"
    if not batch_path.is_file():
        raise ValueError("正式批次文件 06_batch/batch_meta.json 不存在")
    batch_data = json.loads(batch_path.read_text(encoding="utf-8-sig"))
    items = batch_data.get("items") if isinstance(batch_data, dict) else None
    if not isinstance(items, list):
        raise ValueError("06_batch/batch_meta.json 格式无效")
    item = next(
        (it for it in items if isinstance(it, dict) and str(it.get("unit_id") or it.get("id")) == uid),
        None,
    )
    if item is None:
        raise ValueError("未找到对应题目单元：" + uid)

    expected = patch.get("expected")
    if expected is not None and not isinstance(expected, dict):
        raise ValueError("expected 必须是对象")
    sub_patches = patch.get("subquestions")
    by_num = {
        str(sub.get("number") if sub.get("number") is not None else idx): sub
        for idx, sub in enumerate(item["subquestions"])
        if isinstance(sub, dict)
    } if isinstance(item.get("subquestions"), list) else {}

    # Optimistic concurrency check: verify every submitted field with an expected baseline
    # before mutating anything, so stale snapshots cannot silently overwrite newer edits.
    conflicts: list[dict] = []
    field_labels = {"articleSummary": "文章大意", "explanation": "解析", "answer": "答案"}
    if isinstance(expected, dict):
        for field in ("articleSummary", "explanation", "answer"):
            if field in patch and field in expected:
                exp_val = "" if expected[field] is None else str(expected[field])
                cur_val = "" if item.get(field) is None else str(item.get(field))
                if cur_val != exp_val:
                    conflicts.append({
                        "field": field,
                        "number": None,
                        "expected": exp_val,
                        "actual": cur_val,
                        "label": field_labels.get(field, field),
                    })
    expected_subs = (expected.get("subquestions") if isinstance(expected, dict)
                     and isinstance(expected.get("subquestions"), dict) else {})
    if isinstance(sub_patches, list):
        for sp in sub_patches:
            if not isinstance(sp, dict):
                continue
            num_key = str(sp.get("number") if sp.get("number") is not None else "").strip()
            sub = by_num.get(num_key)
            if sub is None:
                continue
            sp_expected = sp.get("expected") if isinstance(sp.get("expected"), dict) else expected_subs.get(num_key)
            if not isinstance(sp_expected, dict):
                continue
            for field in ("answer", "explanation"):
                if field in sp and field in sp_expected:
                    exp_val = "" if sp_expected[field] is None else str(sp_expected[field])
                    cur_val = "" if sub.get(field) is None else str(sub.get(field))
                    if cur_val != exp_val:
                        conflicts.append({
                            "field": field,
                            "number": num_key,
                            "expected": exp_val,
                            "actual": cur_val,
                            "label": f"第{num_key}题{field_labels.get(field, field)}",
                        })
    if conflicts:
        summary = "、".join(c["label"] for c in conflicts[:4])
        raise ContentEditConflictError(
            f"内容已被其他会话修改，请刷新后再保存（冲突字段：{summary}）",
            conflicts=conflicts,
        )

    manual_data = manual_content.load(batch)
    binding = manual_content.source_binding(batch)
    exp_summary_updates: dict[str, str] = {}
    exp_expl_updates: dict[str, str] = {}
    changed = False

    def _record_manual(key: str, field: str, val: str) -> None:
        previous = manual_data["edits"].get(key)
        fields = {k: previous[k] for k in manual_content.FIELDS if k in previous} if (
            isinstance(previous, dict) and previous.get("source_binding") == binding) else {}
        fields[field] = val
        manual_data["edits"][key] = {
            **fields,
            "source_binding": binding,
            "updated_by": "workbench",
            "updated_at": mf.now_iso(),
        }

    for field in ("articleSummary", "explanation", "answer"):
        if field in patch:
            val = "" if patch[field] is None else str(patch[field])
            cur = "" if item.get(field) is None else str(item.get(field))
            if cur != val:
                item[field] = val
                changed = True
                if field == "articleSummary":
                    _record_manual(uid, field, val)
                    exp_summary_updates[uid] = val
                elif field == "explanation":
                    _record_manual(uid, field, val)
                    exp_expl_updates[uid] = val

    if isinstance(sub_patches, list) and by_num:
        for sp in sub_patches:
            if not isinstance(sp, dict):
                continue
            num_key = str(sp.get("number") if sp.get("number") is not None else "").strip()
            sub = by_num.get(num_key)
            if sub is None:
                continue
            for field in ("answer", "explanation"):
                if field in sp:
                    val = "" if sp[field] is None else str(sp[field])
                    cur = "" if sub.get(field) is None else str(sub.get(field))
                    if cur != val:
                        sub[field] = val
                        changed = True
                        if field == "explanation":
                            sub_key = f"{uid}/{num_key}"
                            _record_manual(sub_key, "explanation", val)
                            exp_expl_updates[sub_key] = val

    if not changed:
        return {"applied": False, "message": "内容未变化"}

    old_batch_sha = mf.sha256_file(batch_path) if batch_path.is_file() else None
    batch_path.write_text(json.dumps(batch_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if exp_summary_updates or exp_expl_updates:
        manual_path = batch / manual_content.RELATIVE
        manual_path.parent.mkdir(parents=True, exist_ok=True)
        manual_path.write_text(json.dumps(manual_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if exp_summary_updates:
            _sync_explanations_artifact(batch, exp_summary_updates, "articleSummary")
        if exp_expl_updates:
            _sync_explanations_artifact(batch, exp_expl_updates, "explanation")

    validate_code, format_passed = _refresh_post_edit_gates(batch, lock_held=lock_held, old_batch_sha=old_batch_sha)

    return {
        "applied": True,
        "validate_passed": validate_code == 0 if validate_code is not None else None,
        "format_gate_passed": format_passed,
        "message": "已保存修改",
    }

