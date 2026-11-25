"""Quarantine fallback policy & soft-dependency helpers (isolated to avoid file bloat).

Centralizes three policies so `local_agent.py`, `auto.py`, and `unit_anomalies.py`
stay focused and below the Rule 49 size guardrail:
1. Deterministic type-fallback classification when a single unit fails type validation
   during `types` / `preassemble` (`fallback_type_row`).
2. Per-type soft dependency for `knowledge` -> `answers` (`knowledge_should_wait_answers`):
   only wait when the batch actually has answer-driven types (e.g. 完成句子) AND
   `answers` is still pending/running; never stall unrelated batches or failed answers.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from workbench import taxonomy
from . import manifest as mf, types_direct, unit_anomalies

# 强依赖已验收结构化答案反推考点的题型（其余题型可直接并行打知识点，无需阻塞等 answers）。
ANSWER_DEPENDENT_KNOWLEDGE_TYPES = frozenset(("完成句子",))

_SECTION_TYPE_ALIASES = (
    ("听力关键词语", "听力关键词语选择"),
    ("关键词语选择", "听力关键词语选择"),
    ("短对话", "听力短对话理解"),
    ("长对话", "听力长对话理解"),
    ("听力短文", "听力短文理解"),
    ("信息转换", "听力信息转换"),
    ("信息获取", "听力信息转换"),
    ("单项选择", "单项填空"),
    ("单项填空", "单项填空"),
    ("完形填空", "完形填空"),
    ("语法选择", "语法选择"),
    ("阅读理解", "阅读理解"),
    ("任务型阅读", "任务型阅读"),
    ("选词填空", "选词填空"),
    ("短文填空", "短文填空"),
    ("语篇填空", "短文填空"),
    ("补全对话", "补全对话"),
    ("单词拼写", "单词拼写"),
    ("词汇运用", "单词拼写"),
    ("完成句子", "完成句子"),
    ("书面表达", "书面表达"),
    ("作文", "书面表达"),
    ("语音", "语音题"),
)


def _load_draft_by_id(batch_dir: Path) -> dict[str, dict]:
    draft_path = Path(batch_dir) / "06_batch" / "question-draft.json"
    if not draft_path.is_file():
        return {}
    try:
        data = json.loads(draft_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return {
        str(row["unit_id"]): row
        for row in (data.get("items") or [])
        if isinstance(row, dict) and isinstance(row.get("unit_id"), str)
    }


def _resolve_main_type(unit: dict, draft: dict | None) -> str:
    catalog_types = set(taxonomy.types())
    raw = str(unit.get("type") or "").strip()
    if raw in catalog_types:
        return raw
    for keyword, target in _SECTION_TYPE_ALIASES:
        if keyword in raw and target in catalog_types:
            return target
    # 按单元形态与选项数做保守反推，确保落入合法目录题型。
    kind = str(unit.get("unit_kind") or "")
    opts = types_direct._options(draft) if isinstance(draft, dict) else []
    kids = types_direct._children(draft) if isinstance(draft, dict) else []
    if kind == "group":
        if kids and all(len(types_direct._options(c)) >= 3 for c in kids):
            if "阅读理解" in catalog_types:
                return "阅读理解"
        if "任务型阅读" in catalog_types:
            return "任务型阅读"
    else:
        if len(opts) >= 3 and "单项填空" in catalog_types:
            return "单项填空"
    if "其他" in catalog_types:
        return "其他"
    return sorted(catalog_types)[0]


def _default_subtypes(main_type: str, unit: dict, draft: dict | None) -> list[str]:
    allowed = list(taxonomy.subtypes(main_type))
    if not allowed:
        return []
    opts = types_direct._options(draft) if isinstance(draft, dict) else []
    kids = types_direct._children(draft) if isinstance(draft, dict) else []
    n_kids = len(kids) or len(unit.get("numbers") or [])
    if main_type == "单项填空":
        if len(opts) == 3 and "三个选项" in allowed:
            return ["三个选项"]
        if "四个选项" in allowed:
            return ["四个选项"]
    if main_type == "完形填空":
        for count, label in ((5, "5空"), (10, "10空"), (15, "15空")):
            if n_kids == count and label in allowed:
                return [label]
        if "其他空数" in allowed:
            return ["其他空数"]
    if main_type == "阅读理解":
        for count, label in ((3, "选择3题"), (4, "选择4题"), (5, "选择5题")):
            if n_kids == count and label in allowed:
                return [label]
    if main_type == "听力长对话理解":
        for count, label in ((2, "选择2题"), (3, "选择3题")):
            if n_kids == count and label in allowed:
                return [label]
    if main_type == "补全对话":
        if n_kids == 5 and len(opts) == 7 and "7选5" in allowed:
            return ["7选5"]
    if main_type == "书面表达" and "文字提示" in allowed:
        return ["文字提示"]
    return [allowed[0]]


def fallback_type_row(batch_dir: Path | str, unit: dict, error: object) -> dict:
    """Build a valid, explicitly quarantined type row from section heading / draft."""
    batch = Path(batch_dir)
    uid = str(unit["unit_id"])
    clean = unit_anomalies._clean_error(error, limit=180)
    draft = _load_draft_by_id(batch).get(uid)
    classified = types_direct._classify_one(unit, draft) if isinstance(draft, dict) else None
    if classified is not None:
        main_type, subs = classified
    else:
        main_type = _resolve_main_type(unit, draft)
        subs = _default_subtypes(main_type, unit, draft)
    if isinstance(draft, dict):
        words = types_direct.count_english_words(draft)
    else:
        stem = str(unit.get("stem") or "")
        words = len(re.findall(r"[A-Za-z]+(?:['’-][A-Za-z]+)*", stem))
    row = {
        "unit_id": uid,
        "type": main_type,
        "subtypes": subs,
        "words": words,
        "contentComplete": True,
        "quarantined": True,
        "evidence": [f"大题标题确定性兜底反推（题型校验异常已隔离，待人工核对：{clean}）"],
    }
    if taxonomy.check_labels(row):
        fallback_main = "其他" if "其他" in taxonomy.types() else sorted(taxonomy.types())[0]
        allowed = list(taxonomy.subtypes(fallback_main))
        row["type"] = fallback_main
        row["subtypes"] = [allowed[0]] if allowed else []
    return row


def quarantined_type_response(batch_dir: Path | str, unit: dict, error: object) -> dict:
    """Wrap a fallback type row into a valid single-unit types artifact response."""
    uid = str(unit["unit_id"])
    clean = unit_anomalies._clean_error(error, limit=180)
    row = fallback_type_row(batch_dir, unit, error)
    return {
        "status": "completed",
        "artifacts": {"03_types/types.json": {"items": [row]}},
        "notes": [f"单元 {uid} 题型校验未过，已按大题标题兜底并隔离标红：{clean}"],
        "findings": [],
    }


def quarantine_type_partition(
    batch_dir: Path | str,
    part: dict,
    exc: Exception,
    *,
    run_dir: Path,
    part_index: int,
    config: dict,
    invoke,
    invocation_options: dict,
    strict_hashes: dict,
) -> dict:
    """Salvage valid units in a failed `types` partition and quarantine only bad units.

    Only called after partition retries exhaust on deterministic validation/response errors
    (never on timeout/rate_limit/quota/authentication/provider outages).
    """
    from . import agent_tasks, local_agent, unit_tasks

    batch = Path(batch_dir)
    unit_ids = list(part.get("unit_ids") or [])
    by_id = {u["unit_id"]: u for u in agent_tasks.confirmed_units(batch)}
    sub_parts, sub_responses = [], []
    passed_ids: list[str] = []
    failed_map: dict[str, object] = {}
    deterministic: dict[str, dict] = {}

    if len(unit_ids) == 1:
        uid = unit_ids[0]
        failed_map[uid] = exc
        deterministic[uid] = {"diagnostics": [str(exc)]}
        unit_anomalies.record_anomalies(batch, "types", failed_map, deterministic)
        return quarantined_type_response(batch, by_id[uid], exc)

    for s_idx, uid in enumerate(unit_ids):
        _, built = unit_tasks.build_parts(
            batch, "types", config["max_input_chars"], max_units=1, selected_ids=[uid],
        )
        single = built[0]
        unit_tasks.check_part_inputs(batch, single)
        local_agent._check_input_hashes(batch, strict_hashes)
        sub_dir = run_dir / f"part-{part_index + 1:04d}-isolate-{s_idx + 1:02d}"
        try:
            res = invoke(single["prompt"], sub_dir, config, **invocation_options)
            local_agent._check_input_hashes(batch, strict_hashes)
            unit_tasks.check_part_inputs(batch, single)
            cand = res["response"]
            unit_tasks.validate_part(batch, "types", single, cand)
            passed_ids.append(uid)
            sub_parts.append(single)
            sub_responses.append(cand)
        except (local_agent.AgentError, ValueError) as unit_exc:
            if (isinstance(unit_exc, local_agent.AgentError)
                    and unit_exc.code not in ("response", "integration")):
                raise
            failed_map[uid] = unit_exc
            deterministic[uid] = {"diagnostics": [str(unit_exc)]}
            sub_parts.append(single)
            sub_responses.append(quarantined_type_response(batch, by_id[uid], unit_exc))

    if passed_ids:
        unit_anomalies.clear_stage_anomalies(batch, "types", passed_ids)
    if failed_map:
        unit_anomalies.record_anomalies(batch, "types", failed_map, deterministic)
    return unit_tasks.merge("types", sub_parts, sub_responses)


def batch_has_answer_dependent_knowledge_types(batch_dir: Path | str) -> bool:
    """Return True when 03_types/types.json contains a type that needs verified answers."""
    types_path = Path(batch_dir) / "03_types" / "types.json"
    if not types_path.is_file():
        return False
    try:
        data = json.loads(types_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    for row in (data.get("items") or []):
        if not isinstance(row, dict):
            continue
        if str(row.get("type") or "").strip() in ANSWER_DEPENDENT_KNOWLEDGE_TYPES:
            return True
    return False


def knowledge_should_wait_answers(batch_dir: Path | str, manifest: dict) -> bool:
    """Soft dependency: wait for `answers` only when needed and still in flight/pending.

    - If the batch has no answer-driven types (e.g. no 完成句子), run `knowledge` in parallel.
    - If `answers` already finished, was skipped, or reached a terminal failure/needs_review,
      never block `knowledge` (it falls back to raw appendix/answer source text).
    - Wait only while `answers` is pending/running on a batch that actually needs it.
    """
    if not batch_has_answer_dependent_knowledge_types(batch_dir):
        return False
    stages = manifest.get("stages") if isinstance(manifest, dict) else {}
    ans = stages.get("answers") if isinstance(stages, dict) else {}
    status = str((ans or {}).get("status") or mf.PENDING)
    if status in mf.TERMINAL_OK or status in (mf.FAILED, mf.BLOCKED, mf.NEEDS_REVIEW, mf.SKIPPED):
        return False
    return True
