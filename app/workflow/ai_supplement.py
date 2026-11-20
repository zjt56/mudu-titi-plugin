# -*- coding: utf-8 -*-
"""Workbench on-demand AI supplement for missing/AI-generated articleSummary & explanation."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from contextlib import ExitStack
from pathlib import Path

from . import (
    content_rules,
    local_agent,
    manifest as mf,
    manual_content,
    runner,
    stage_semantics,
    supervisor,
    upload_content,
)

SUMMARY_ELIGIBLE_TYPES = frozenset({
    "完形填空", "阅读理解", "任务型阅读", "短文填空",
    "选词填空", "语法选择", "读写综合", "教材语篇填空",
})
MIN_SUMMARY_WORDS_GENERAL = 50
MIN_SUMMARY_WORDS_ELIGIBLE = 20
EXCLUDED_EXPLANATION_TYPE_HINTS = ("听力", "书面表达")
MAX_BODY_CHARS = 50
PROGRESS_REL = "workflow/.ai-supplement-progress.json"

_ENGLISH_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_PREFIX_STRIP = re.compile(
    r"^\s*(?:【\s*(?:解析|参考解析|文章大意|大意|导读)\s*】|(?:解析|参考解析|文章大意|大意|explanation|summary)\s*[:：])\s*",
    re.I,
)
_ACTIVE_JOBS: dict[str, threading.Thread] = {}
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
    return isinstance(value, str) and upload_content.AI_MARK in value


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


def _progress_path(batch_dir: Path) -> Path:
    return Path(batch_dir) / PROGRESS_REL


def _write_progress(batch_dir: Path, state: dict) -> dict:
    payload = {**state, "updated_at": mf.now_iso()}
    path = _progress_path(batch_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def read_progress(batch_dir: Path) -> dict:
    path = _progress_path(batch_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
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
    if mode not in ("articleSummary", "explanation"):
        raise ValueError("不支持的补充模式：" + str(mode))
    batch_path = Path(batch_dir) / "06_batch" / "batch_meta.json"
    if not batch_path.is_file():
        raise ValueError("正式批次文件 06_batch/batch_meta.json 不存在，无法补充")
    payload = json.loads(batch_path.read_text(encoding="utf-8-sig"))
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError("06_batch/batch_meta.json 格式无效")

    coverage = _source_coverage(Path(batch_dir))
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

        if mode == "articleSummary":
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
                })
    return targets


def _build_prompt(mode: str, targets: list[dict]) -> str:
    if mode == "articleSummary":
        instruction = (
            "Stage: ai_supplement\n"
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


def _parse_generated(result: dict) -> dict[str, str]:
    rows = result.get("items") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        raise ValueError("模型返回格式无效：缺少 items 列表")
    out: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "").strip()
        text = _format_ai_text(str(row.get("text") or ""))
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
                row[mode] = text
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


def supplement(batch_dir: Path, mode: str, *, invoke_fn=None) -> dict:
    """Generate/regenerate and apply AI articleSummary or explanation for eligible items."""
    batch = Path(batch_dir).resolve()
    _check_upload_editable(batch)
    manifest_path = batch / "workflow" / "manifest.json"

    targets = eligible_targets(batch, mode)
    total = len(targets)
    if not targets:
        res = {"applied": 0, "mode": mode, "keys": [], "total": 0, "completed": 0,
               "status": "done", "message": "当前批次没有需要补充或重新生成的题目"}
        _write_progress(batch, res)
        return res

    _write_progress(batch, {
        "status": "running", "mode": mode, "total": total, "completed": 0,
        "applied": 0, "message": f"正在准备生成（共 {total} 项）…", "error": "",
    })

    caller = invoke_fn
    if caller is None:
        config = local_agent.load_config()
        config = dict(config, _content_batch_dir=str(batch))

        def caller(prompt_text: str, run_dir: Path) -> dict:
            return local_agent.invoke(prompt_text, run_dir, config)

    generated: dict[str, str] = {}
    chunk_size = 10 if mode == "explanation" else 5
    try:
        with tempfile.TemporaryDirectory(prefix="ai_supplement_") as tmp:
            for idx in range(0, total, chunk_size):
                chunk = targets[idx:idx + chunk_size]
                prompt = _build_prompt(mode, chunk)
                run_dir = Path(tmp) / f"chunk_{idx // chunk_size}"
                resp = caller(prompt, run_dir)
                payload = _unwrap_payload(resp)
                generated.update(_parse_generated(payload))
                done_cnt = min(idx + len(chunk), total)
                _write_progress(batch, {
                    "status": "running", "mode": mode, "total": total, "completed": done_cnt,
                    "applied": len(generated),
                    "message": f"正在生成 {done_cnt}/{total} 项…", "error": "",
                })
    except Exception as exc:  # noqa: BLE001
        _write_progress(batch, {
            "status": "error", "mode": mode, "total": total, "completed": 0,
            "applied": 0, "message": "生成失败", "error": str(exc),
        })
        raise

    if not generated:
        res = {"applied": 0, "mode": mode, "keys": [], "total": total, "completed": total,
               "status": "done", "message": "模型未返回有效补充内容"}
        _write_progress(batch, res)
        return res

    batch_path = batch / "06_batch" / "batch_meta.json"
    batch_data = json.loads(batch_path.read_text(encoding="utf-8-sig"))
    by_uid = {
        str(it.get("unit_id") or it.get("id")): it
        for it in (batch_data.get("items") or [])
        if isinstance(it, dict) and (it.get("unit_id") or it.get("id"))
    }
    applied_keys: list[str] = []
    applied_updates: dict[str, str] = {}
    manual_data = manual_content.load(batch)
    binding = manual_content.source_binding(batch)

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
                    if _is_replaceable(sub.get("explanation")):
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
            field = mode
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

    if not applied_keys:
        res = {"applied": 0, "mode": mode, "keys": [], "total": total, "completed": total,
               "status": "done", "message": "无可应用的新增补充内容"}
        _write_progress(batch, res)
        return res

    batch_path.write_text(json.dumps(batch_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manual_path = batch / manual_content.RELATIVE
    manual_path.parent.mkdir(parents=True, exist_ok=True)
    manual_path.write_text(json.dumps(manual_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _sync_explanations_artifact(batch, applied_updates, mode)

    validate_code = None
    if manifest_path.is_file():
        current = mf.load(batch)
        validate_code = runner.do_validate(batch, current)

    label = "文章大意" if mode == "articleSummary" else "解析"
    res = {
        "status": "done",
        "applied": len(applied_keys),
        "total": total,
        "completed": total,
        "mode": mode,
        "keys": applied_keys,
        "validate_passed": validate_code == 0 if validate_code is not None else None,
        "message": f"已完成 {len(applied_keys)} 项 AI {label}生成并标注",
        "error": "",
    }
    _write_progress(batch, res)
    return res


def start_background(batch_dir: Path, mode: str) -> dict:
    """Launch background AI supplement task so UI modal can poll live progress."""
    batch = Path(batch_dir).resolve()
    _check_upload_editable(batch)
    targets = eligible_targets(batch, mode)
    total = len(targets)
    if not targets:
        return _write_progress(batch, {
            "status": "done", "mode": mode, "total": 0, "completed": 0,
            "applied": 0, "keys": [], "message": "当前批次没有需要补充或重新生成的题目", "error": "",
        })

    job_key = str(batch)
    with _JOBS_LOCK:
        existing = _ACTIVE_JOBS.get(job_key)
        if existing is not None and existing.is_alive():
            return read_progress(batch)
        initial = _write_progress(batch, {
            "status": "running", "mode": mode, "total": total, "completed": 0,
            "applied": 0, "message": f"正在启动生成（共 {total} 项）…", "error": "",
        })

        def _worker():
            try:
                from workflow.agent_tasks import _safe_path
                run_lock = _safe_path(batch, "workflow/.run.lock")
                with ExitStack() as locks:
                    if not locks.enter_context(supervisor._try_file_lock(supervisor._auto_lock_path(batch))):
                        raise ValueError(f"批次「{batch.name}」正在运行，无法同时补充")
                    run_lock.parent.mkdir(parents=True, exist_ok=True)
                    if not locks.enter_context(supervisor._try_file_lock(run_lock)):
                        raise ValueError(f"批次「{batch.name}」正在运行，无法同时补充")
                    locks.enter_context(local_agent._agent_lock(0))
                    supplement(batch, mode)
            except Exception as exc:  # noqa: BLE001
                _write_progress(batch, {
                    "status": "error", "mode": mode, "total": total, "completed": 0,
                    "applied": 0, "message": "生成失败", "error": str(exc),
                })

        thread = threading.Thread(target=_worker, name=f"ai-supplement-{batch.name}", daemon=True)
        _ACTIVE_JOBS[job_key] = thread
        thread.start()
        return initial


def save_content_edit(batch_dir: Path, unit_id: str, patch: dict) -> dict:
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

    batch_path.write_text(json.dumps(batch_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if exp_summary_updates or exp_expl_updates:
        manual_path = batch / manual_content.RELATIVE
        manual_path.parent.mkdir(parents=True, exist_ok=True)
        manual_path.write_text(json.dumps(manual_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if exp_summary_updates:
            _sync_explanations_artifact(batch, exp_summary_updates, "articleSummary")
        if exp_expl_updates:
            _sync_explanations_artifact(batch, exp_expl_updates, "explanation")

    manifest_path = batch / "workflow" / "manifest.json"
    validate_code = None
    if manifest_path.is_file():
        current = mf.load(batch)
        validate_code = runner.do_validate(batch, current)

    return {
        "applied": True,
        "validate_passed": validate_code == 0 if validate_code is not None else None,
        "message": "已保存修改",
    }

