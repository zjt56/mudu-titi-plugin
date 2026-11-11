"""Optional source detection and deterministic placeholders for absent inputs."""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import agent_tasks

ANSWER_ROW = re.compile(r"^\s*\d{1,3}\s*[.．、:：)]\s*(?:[A-Ha-h](?:\s*[-/、,，]\s*[A-Ha-h])*|[A-Za-z][A-Za-z'’/ -]{0,80})\s*$")
ANSWER_SEQUENCE = re.compile(r"^\s*\d{1,3}\s*[.．、:：)]\s*[A-Ha-h](?:\s*\d{1,3}\s*[.．、:：)]\s*[A-Ha-h])\s*$")
ANSWER_RANGE = re.compile(r"^\s*\d{1,3}\s*[~～—-]\s*\d{1,3}\s+[A-Ha-h]+\s*$")
ANSWER_HEADER = re.compile(r"(?:参考答案|答案与解析|答案|Answer\s*Key)", re.I)
EXPLANATION_MARK = re.compile(r"(?:【解析】|\[解析\]|【文章大意】|文章大意|解析\s*[:：])")
ANSWER_FILES = ("01_source_ans/正文.txt", "01_source/附录.txt")
SOURCE_DIRS = ("01_source_ans", "01_source")


def _text_files(batch_dir: Path):
    root = Path(batch_dir)
    for relative in ANSWER_FILES:
        path = root / relative
        if path.is_file():
            yield path
    materials = root / "01_source/materials"
    if materials.is_dir():
        yield from sorted(materials.glob("attachment_*.txt"))
    body = root / "01_source/正文.txt"
    if body.is_file():
        yield body


def _nonempty(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig", errors="ignore").strip()
    except OSError:
        return ""


def _answer_text_files(batch_dir: Path):
    root = Path(batch_dir)
    for relative in ANSWER_FILES:
        path = root / relative
        if path.is_file():
            yield path
    materials = root / "01_source/materials"
    if materials.is_dir():
        yield from sorted(materials.glob("attachment_*.txt"))


def _has_explicit_answers(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if any(ANSWER_HEADER.search(line) for line in lines):
        return True
    return any(ANSWER_ROW.fullmatch(line) or ANSWER_RANGE.fullmatch(line)
               or ANSWER_SEQUENCE.fullmatch(line) for line in lines)


def has_answer_source(batch_dir) -> bool:
    return any(_has_explicit_answers(_nonempty(path)) for path in _answer_text_files(Path(batch_dir)))


def has_explanation_source(batch_dir) -> bool:
    """True when a supplied answer source actually contains explanation markers."""
    return any(EXPLANATION_MARK.search(_nonempty(path)) for path in _answer_text_files(Path(batch_dir)))



def write_empty_explanations(batch_dir) -> Path:
    """Write a deterministic all-「无」 explanations artifact for batches without source."""
    root = Path(batch_dir)
    if has_explanation_source(root):
        raise ValueError("Original explanation source exists; cannot install empty placeholders")
    path = root / "04_knowledge/explanations.json"
    if path.is_file():
        from .content_rules import is_placeholder
        previous = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(previous, dict) or not isinstance(previous.get("items"), list):
            raise ValueError("Existing explanation artifact cannot be safely replaced")
        for row in previous["items"]:
            if not isinstance(row, dict) or not isinstance(row.get("subquestions", []), list):
                raise ValueError("Existing explanation row cannot be safely replaced")
            for item in [row, *row.get("subquestions", [])]:
                if not isinstance(item, dict):
                    raise ValueError("Existing explanation child cannot be safely replaced")
                if any(not is_placeholder(item.get(field))
                       for field in ("explanation", "articleSummary")):
                    raise ValueError("Existing original explanation content must be preserved")
    units = agent_tasks.confirmed_units(root)
    items = []
    for unit in units:
        children = []
        if unit.get("unit_kind") == "group":
            children = [{"number": int(number), "explanation": "无"} for number in unit["numbers"]]
        items.append({
            "unit_id": unit["unit_id"],
            "explanation": "无",
            "articleSummary": "无",
            "subquestions": children,
            "evidence": ["未提供原解析/原大意，按空值记录（可选字段）"],
        })
    payload = {"items": items}
    typed_path = root / agent_tasks.ARTIFACTS["types"]
    context = {agent_tasks.ARTIFACTS["types"]: json.loads(typed_path.read_text(encoding="utf-8-sig"))}
    agent_tasks._validate(root, "explanations", payload, context, units)
    agent_tasks._install(root, {agent_tasks.ARTIFACTS["explanations"]: payload})
    return path
