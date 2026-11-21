"""Deterministic knowledge tagging for types with a single exact catalog leaf.

When a question type's allowed knowledge rule in type-knowledge-map.json maps
uniquely to one fixed exact path (for example ``听力关键词语选择`` ->
``["语音/基本读音"]``), we can produce and validate its knowledge entry
deterministically without a model call. All multi-choice or context-dependent
types remain with the model.
"""
from __future__ import annotations

import json
from pathlib import Path

from workbench import taxonomy
from . import agent_tasks


def _fixed_path_for_type(main_type: str) -> str | None:
    """Return the sole allowed exact knowledge path when a type has only one."""
    if not main_type or main_type not in taxonomy.types():
        return None
    exact = taxonomy.exact_knowledge_paths(main_type)
    if len(exact) != 1:
        return None
    rule = taxonomy.type_knowledge_rules().get(main_type) or {}
    # Main knowledge dims (exclude separate answer-point dims which belong to answer_points).
    main_dims = [d for d in (rule.get("dims") or []) if not d.get("separate")]
    if len(main_dims) != 1 or main_dims[0].get("show") != "exact":
        return None
    only_path = next(iter(exact))
    if not taxonomy.knowledge_path_allowed(only_path, main_type):
        return None
    return only_path


def tag_units(batch_dir: Path | str, units: list[dict] | None = None) -> dict[str, dict]:
    """Deterministically tag units whose type has a single exact knowledge path."""
    batch = Path(batch_dir).resolve()
    types_path = batch / agent_tasks.ARTIFACTS["types"]
    if not types_path.is_file():
        return {}
    try:
        types_data = json.loads(types_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    type_by_id = {
        row["unit_id"]: str(row.get("type") or "").strip()
        for row in (types_data.get("items") or [])
        if isinstance(row, dict) and isinstance(row.get("unit_id"), str)
    }
    if units is None:
        units = agent_tasks.confirmed_units(batch)
    direct: dict[str, dict] = {}
    for unit in units:
        uid = unit["unit_id"]
        main_type = type_by_id.get(uid, "")
        fixed_path = _fixed_path_for_type(main_type)
        if not fixed_path:
            continue
        row = {
            "unit_id": uid,
            "knowledge": [fixed_path],
            "subquestions": (
                [{"number": str(n), "knowledge": []} for n in (unit.get("numbers") or [])]
                if unit.get("unit_kind") == "group"
                else []
            ),
            "evidence": [f"固定题型规则直标：{main_type} -> {fixed_path}"],
        }
        if taxonomy.check_labels({**row, "type": main_type}):
            continue
        direct[uid] = row
    return direct
