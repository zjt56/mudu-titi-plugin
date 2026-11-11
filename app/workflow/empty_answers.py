"""Deterministic empty answer decisions for batches with no answer material."""
from __future__ import annotations

import json
from pathlib import Path

from .agent_tasks import confirmed_units
from .question_identity import question_key


def write_empty_answers(batch_dir):
    root = Path(batch_dir)
    if not (root / "01_source/structure.confirmed.json").is_file():
        raise ValueError("Cannot decide answer coverage before structure is confirmed")
    missing = {}
    for unit in confirmed_units(root):
        for number in unit["numbers"]:
            missing[question_key(unit, number)] = ["未提供答案原文；按无答案占位继续"]
    value = {"answers": {}, "evidence": {}, "missing": missing}
    path = root / "01_source_ans/answers.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
