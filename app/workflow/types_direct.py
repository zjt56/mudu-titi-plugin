"""Conservative deterministic type classification for standard exam sections.

Only classifies units whose main type, required catalog subtypes, English word
count and content completeness are 100% unambiguous from the confirmed structure
and source-bound question draft. Any ambiguous, incomplete, or non-standard unit
(e.g. writing prompts, task-based reading without options, sync-practice
sections, or unusual formats) is left to the model.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from workbench import taxonomy
from . import agent_tasks, sync_practice

_WORD_RE = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)*")
_TAG_RE = re.compile(r"<[^>]+>")
_MARKER_RE = re.compile(r"\[(?:IMG|TABLE|AUDIO)[^\]]*\]", re.I)
_CHINESE_HINT_RE = re.compile(r"[（(][^()（）]*[\u4e00-\u9fff][^()（）]*[）)]")
_INITIAL_BLANK_RE = re.compile(
    r"(?<![A-Za-z])(?:<u>\s*)?[A-Za-z](?:\s*</u>)?\s*(?:<u>\s*[_\u3000\s]+\s*</u>|_{2,}|\u3000{2,})"
)
_QA_READING_RUBRIC_RE = re.compile(r"用英语回答(?:下面|下列)?问题")
_WRITING_BULLET_LINE_RE = re.compile(r"^\s*(?:[1-9]\s*[.．、)]|[①②③④⑤]|[-•·])\s*(.+)$")
_WRITING_EXCLUDE_RE = re.compile(r"思维导图|mind\s*map|看图|漫画|表格|图表|图画", re.I)


def count_english_words(draft_row: dict) -> int:
    """Count English words in the unit's own stem/material/options/subquestions."""
    parts = []
    for key in ("stem", "material"):
        value = draft_row.get(key)
        if isinstance(value, str) and value:
            parts.append(value)
    for opt in draft_row.get("options") or []:
        if isinstance(opt, str):
            parts.append(opt)
        elif isinstance(opt, dict):
            text = opt.get("text") or opt.get("content") or ""
            if isinstance(text, str):
                parts.append(text)
    for child in draft_row.get("subquestions") or []:
        if not isinstance(child, dict):
            continue
        stem = child.get("stem")
        if isinstance(stem, str) and stem:
            parts.append(stem)
        for opt in child.get("options") or []:
            if isinstance(opt, str):
                parts.append(opt)
            elif isinstance(opt, dict):
                text = opt.get("text") or opt.get("content") or ""
                if isinstance(text, str):
                    parts.append(text)
    cleaned = _TAG_RE.sub(" ", _MARKER_RE.sub(" ", " ".join(parts)))
    return len(_WORD_RE.findall(cleaned))


def _options(row: dict) -> list:
    opts = row.get("options")
    return opts if isinstance(opts, list) else []


def _has_image(row: dict) -> bool:
    if any(isinstance(a, dict) and str(a.get("kind") or a.get("type") or "").startswith("image")
           for a in (row.get("attachments") or [])):
        return True
    texts = [str(row.get("stem") or ""), str(row.get("material") or "")]
    for opt in _options(row):
        texts.append(opt if isinstance(opt, str) else str((opt or {}).get("text") or ""))
    return any("[IMG" in t.upper() for t in texts)


def _options_have_image(row: dict) -> bool:
    for opt in _options(row):
        if isinstance(opt, dict) and (opt.get("image") or opt.get("attachments")):
            return True
        text = opt if isinstance(opt, str) else str((opt or {}).get("text") or "")
        if "[IMG" in text.upper():
            return True
    return False


def _children(row: dict) -> list[dict]:
    subs = row.get("subquestions")
    return [c for c in subs if isinstance(c, dict)] if isinstance(subs, list) else []


def _classify_one(unit: dict, draft: dict) -> tuple[str, list[str]] | None:
    """Return (main_type, subtypes) only when 100% unambiguous; else None."""
    main_type = str(unit.get("type") or "").strip()
    kind = unit.get("unit_kind")
    nums = unit.get("numbers") or []
    if main_type not in taxonomy.types():
        return None
    opts = _options(draft)
    kids = _children(draft)
    stem = str(draft.get("stem") or unit.get("stem") or "").strip()
    material = str(draft.get("material") or "").strip()

    if kind == "standalone":
        if not stem and not opts and not _has_image(draft):
            return None
        if main_type == "听力关键词语选择":
            if len(opts) >= 3:
                return main_type, []
            return None
        if main_type == "听力短对话理解":
            if _options_have_image(draft) or (_has_image(draft) and len(opts) < 3):
                return main_type, ["选图片"]
            if len(opts) >= 3 and not _has_image(draft):
                return main_type, ["选文字"]
            return None
        if main_type == "单项填空":
            if len(opts) == 4:
                return main_type, ["四个选项"]
            if len(opts) == 3:
                return main_type, ["三个选项"]
            return None
        if main_type == "单词拼写":
            if _CHINESE_HINT_RE.search(stem) and _INITIAL_BLANK_RE.search(stem):
                return main_type, ["汉语及首字母"]
            return None
        if main_type == "书面表达":
            if (not opts and not _has_image(draft)
                    and not (draft.get("attachments") or [])
                    and "[TABLE" not in stem.upper() and "[TABLE" not in material.upper()
                    and not _WRITING_EXCLUDE_RE.search(stem)
                    and not _WRITING_EXCLUDE_RE.search(material)):
                bullet_bodies = []
                for raw_line in stem.splitlines():
                    line = raw_line.strip()
                    if re.match(r"^(?:注意|要求)\s*[:：]", line):
                        break
                    m = _WRITING_BULLET_LINE_RE.match(line)
                    if m:
                        bullet_bodies.append(m.group(1).strip())
                if (len(bullet_bodies) >= 2
                        and all(re.search(r"[\u4e00-\u9fff]", body) for body in bullet_bodies)):
                    return main_type, ["列中文要点"]
            return None
        return None

    if kind == "group":
        if not kids or len(kids) != len(nums):
            return None
        all_have_choices = all(len(_options(c)) >= 3 for c in kids)
        if main_type == "听力长对话理解":
            if all_have_choices and len(kids) == 2:
                return main_type, ["选择2题"]
            if all_have_choices and len(kids) == 3:
                return main_type, ["选择3题"]
            return None
        if main_type == "听力短文理解":
            if all_have_choices:
                return main_type, []
            return None
        if main_type == "听力信息转换":
            if not any(_options(c) for c in kids) and (material or stem or draft.get("materialBlocks")):
                return main_type, []
            return None
        if main_type == "完形填空":
            if not all_have_choices or not material:
                return None
            count_map = {5: "5空", 10: "10空", 15: "15空"}
            return main_type, [count_map.get(len(kids), "其他空数")]
        if main_type in ("阅读理解", "任务型阅读"):
            if all_have_choices and main_type == "阅读理解" and material:
                count_map = {3: "选择3题", 4: "选择4题", 5: "选择5题"}
                sub = count_map.get(len(kids))
                if sub:
                    return main_type, [sub]
                return None
            if (not any(_options(c) for c in kids)
                    and material and "[TABLE" not in material.upper()
                    and _QA_READING_RUBRIC_RE.search(stem)):
                return "任务型阅读", ["阅读问答"]
            return None
        if main_type == "补全对话":
            if len(kids) == 5 and len(opts) == 7:
                return main_type, ["7选5"]
            return None
        return None

    return None


def classify_units(batch_dir: Path | str, units: list[dict] | None = None) -> dict[str, dict]:
    """Deterministically classify unambiguous standard exam units.

    Returns {unit_id: validated_type_row}. Skips sync-practice papers so their
    specialized heading rules continue through the model path untouched.
    """
    batch = Path(batch_dir).resolve()
    body_path = batch / "01_source" / "正文.txt"
    if not body_path.is_file():
        return {}
    body = body_path.read_text(encoding="utf-8-sig")
    if sync_practice.looks_like_sync_practice(body):
        return {}
    if units is None:
        units = agent_tasks.confirmed_units(batch)
    draft_path = batch / "06_batch" / "question-draft.json"
    if not draft_path.is_file():
        return {}
    try:
        draft_data = json.loads(draft_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    draft_by_id = {
        row["unit_id"]: row
        for row in (draft_data.get("items") or [])
        if isinstance(row, dict) and isinstance(row.get("unit_id"), str)
    }
    direct: dict[str, dict] = {}
    for unit in units:
        uid = unit["unit_id"]
        draft = draft_by_id.get(uid)
        if not isinstance(draft, dict):
            continue
        classified = _classify_one(unit, draft)
        if classified is None:
            continue
        main_type, subs = classified
        row = {
            "unit_id": uid,
            "type": main_type,
            "subtypes": subs,
            "words": count_english_words(draft),
            "contentComplete": True,
            "evidence": [f"标准大题规则直判：structure.confirmed.json={main_type}"],
        }
        if taxonomy.check_labels(row):
            continue
        direct[uid] = row
    return direct
