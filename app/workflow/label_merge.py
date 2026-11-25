"""Deterministic knowledge-tag merge ("标签合并") between original tags and Round 1 (一审).

Scope rules (explicit user contract):
- ONLY merges ``knowledge`` (知识点标签). Never modifies ``type``, ``difficulty``,
  ``subtypes``, ``categories``, or source fields.
- Deduplicated union: preserves original knowledge paths in order, then appends
  valid, non-duplicate knowledge paths produced by Round 1 (``round_id == "labels"``).
- Manual edits saved in ``candidates/units/<unit_id>.json`` (via 「修改标签」)
  always take precedence over automatic merge so human decisions are never
  overwritten.
- Whole-unit group types keep ``subquestions[].knowledge == []`` and merge only
  at unit/group level.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re

from workbench import taxonomy


def _clean_paths(values) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item or "").strip()
        if not text:
            continue
        text = re.sub(r"\s*/\s*", "/", text)
        if text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _safe_load_json(path: Path) -> dict:
    try:
        if not path.is_file() or path.is_symlink():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def unit_label_file(batch_dir: Path, unit_id) -> Path | None:
    safe = re.sub(r"[^0-9A-Za-z._-]", "_", str(unit_id or "").strip())
    if not safe:
        return None
    return Path(batch_dir) / "candidates" / "units" / f"{safe}.json"


def load_manual_unit_labels(batch_dir: Path, unit_id) -> dict:
    path = unit_label_file(batch_dir, unit_id)
    if path is None:
        return {}
    return _safe_load_json(path)


def _validated_round1_checks(folder: Path, data: dict) -> list[dict]:
    """Return Round 1 (``labels``) checks that still match current batch unit bindings."""
    rounds = data.get("rounds")
    if not isinstance(rounds, list):
        return []
    labels_round = next(
        (r for r in rounds if isinstance(r, dict) and r.get("round_id") == "labels"),
        None,
    )
    if not isinstance(labels_round, dict):
        return []
    raw_checks = [c for c in (labels_round.get("checks") or []) if isinstance(c, dict)]
    if not raw_checks:
        return []

    has_structure = (folder / "01_source" / "structure.confirmed.json").is_file()
    try:
        from . import review
        current_plan = review.prepare_round_plan(folder)
    except (OSError, ValueError, KeyError, TypeError):
        # Lightweight unit-test fixtures without 01_source/structure.confirmed.json.
        if not has_structure and not isinstance(data.get("plan"), dict) and not data.get("input_binding"):
            return raw_checks
        return []

    effective_plan, _ = review.resolve_round_plan(folder, current_plan, data)
    current_units = set(effective_plan.get("units") or [])

    rebased = data
    if isinstance(data, dict) and data.get("plan") != effective_plan:
        rebased = review.rebase_rounds(folder, data, effective_plan)
    if (
        not isinstance(rebased, dict)
        or set(rebased) != {"plan", "rounds"}
        or rebased.get("plan") != effective_plan
        or not isinstance(rebased.get("rounds"), list)
    ):
        return []
    matches = [r for r in rebased["rounds"] if isinstance(r, dict) and r.get("round_id") == "labels"]
    if len(matches) != 1:
        return []
    record = matches[0]
    try:
        if not isinstance(record.get("execution_id"), str) or not record["execution_id"].strip():
            return []
        present_uids = {
            str(c.get("unit_id") or "").strip()
            for c in (record.get("checks") or [])
            if isinstance(c, dict) and str(c.get("unit_id") or "").strip() in current_units
        }
        if not present_uids:
            return []
        check_plan = dict(effective_plan, units=[u for u in (effective_plan.get("units") or []) if u in present_uids])
        review._round_checks(record, "labels", check_plan, folder, lenient=True, require_coverage=False)
        review._verify_round_sources(folder, effective_plan, record)
    except (ValueError, OSError, KeyError, TypeError, AttributeError):
        return []
    return [
        c for c in (record.get("checks") or [])
        if isinstance(c, dict) and str(c.get("unit_id") or "").strip() in current_units
    ]


_ROUND1_ASSESSMENTS_CACHE: dict[tuple, dict[str, dict]] = {}
_ROUND1_FINGERPRINT_FILES = (
    "07_review/review-rounds.json",
    "07_review/review-comparison.json",
    "07_review/comparison-snapshot.json",
    "01_source/structure.confirmed.json",
    "01_source/正文.txt",
    "06_batch/batch.json",
    "06_batch/batch_meta.json",
    "workflow/media-observations.json",
)


def _round1_cache_key(folder: Path) -> tuple:
    import hashlib
    parts = []
    for rel in _ROUND1_FINGERPRINT_FILES:
        path = folder / rel
        try:
            raw = path.read_bytes()
            parts.append((rel, hashlib.sha256(raw).hexdigest()))
        except OSError:
            parts.append((rel, "missing"))
    from . import review
    fn_ids = (
        id(review.prepare_round_plan),
        id(review.resolve_round_plan),
        id(review.rebase_rounds),
        id(review._round_checks),
        id(review._verify_round_sources),
    )
    return (str(folder.resolve()), tuple(parts), fn_ids)


def load_round1_assessments(batch_dir: Path) -> dict[str, dict]:
    """Return validated ``{unit_id: assessment_dict}`` from Round 1 (``labels``) if present."""
    import copy
    folder = Path(batch_dir)
    selected_data = None
    for rel in ("07_review/review-rounds.json", "07_review/review-comparison.json", "07_review/comparison-snapshot.json"):
        data = _safe_load_json(folder / rel)
        if isinstance(data.get("rounds"), list):
            selected_data = data
            break
    if not isinstance(selected_data, dict):
        return {}
    cache_key = _round1_cache_key(folder)
    cached = _ROUND1_ASSESSMENTS_CACHE.get(cache_key)
    if cached is not None:
        return copy.deepcopy(cached)
    checks = _validated_round1_checks(folder, selected_data)
    result: dict[str, dict] = {}
    for check in checks:
        uid = str(check.get("unit_id") or "").strip()
        assessment = check.get("assessment")
        if uid and isinstance(assessment, dict):
            result[uid] = assessment
    if len(_ROUND1_ASSESSMENTS_CACHE) > 64:
        _ROUND1_ASSESSMENTS_CACHE.clear()
    _ROUND1_ASSESSMENTS_CACHE[cache_key] = copy.deepcopy(result)
    return result


def _is_valid_merged_candidate(canonical: str, main_type: str | None, *,
                               is_group: bool = False) -> bool:
    if not main_type or main_type not in taxonomy.types():
        return True
    unit_kind = "group" if is_group else "standalone"
    if not taxonomy.knowledge_path_allowed(canonical, main_type, unit_kind=unit_kind):
        return False
    if not taxonomy.has_answer_point_dim(main_type, unit_kind) and (
        taxonomy._under(canonical, "词汇/考察词汇") or taxonomy._under(canonical, "词汇/答案考点")
    ):
        return False
    if is_group:
        rule = taxonomy._unit_dims("group").get(main_type)
        if rule and any(taxonomy._under(canonical, dim) for dim in rule.get("forbid") or []):
            return False
    return True


def merge_knowledge_lists(
    orig_paths,
    reviewed_paths,
    main_type: str | None = None,
    *,
    is_group: bool = False,
) -> tuple[list[str], list[str]]:
    """Return ``(merged_paths, added_from_round1)`` preserving order and catalog validity."""
    base = _clean_paths(orig_paths)
    reviewed = _clean_paths(reviewed_paths)
    seen = set(base)
    merged = list(base)
    added: list[str] = []
    unit_kind = "group" if is_group else "standalone"
    for path in reviewed:
        if path in seen:
            continue
        # Only merge paths that resolve to valid catalog leaves for this question type.
        resolved = taxonomy.resolve_knowledge([path], main_type, unit_kind=unit_kind)
        if not resolved.get("resolved") or resolved.get("unknown") or resolved.get("non_leaf"):
            continue
        canonical = resolved["resolved"][0]["path"]
        if not _is_valid_merged_candidate(canonical, main_type, is_group=is_group):
            continue
        if canonical not in seen:
            seen.add(canonical)
            merged.append(canonical)
            added.append(canonical)
    return merged, added


def _sync_knowledge_ids(target: dict, paths: list[str], main_type: str | None) -> None:
    """Keep ``knowledgeIds`` / ``knowledge_ids`` consistent with ``knowledge``."""
    resolved = taxonomy.resolve_knowledge(paths, main_type)
    ids = list(dict.fromkeys(
        point["id"] for point in (resolved.get("resolved") or []) if point.get("id")
    ))
    for key in ("knowledgeIds", "knowledge_ids"):
        if key in target:
            target[key] = list(ids)


_UNSET = object()


def merged_unit_knowledge(
    batch_dir: Path | None,
    unit_id: str,
    orig_knowledge,
    *,
    main_type: str | None = None,
    manual_labels=_UNSET,
    round1_assessment=_UNSET,
    is_group: bool = False,
) -> dict:
    """Compute merged knowledge for a unit, honoring manual edits over auto-merge."""
    if manual_labels is _UNSET and batch_dir is not None and unit_id:
        manual_labels = load_manual_unit_labels(Path(batch_dir), unit_id)
    manual_labels = manual_labels if isinstance(manual_labels, dict) else {}

    if round1_assessment is _UNSET and batch_dir is not None and unit_id:
        round1_assessment = load_round1_assessments(Path(batch_dir)).get(str(unit_id))
    has_round1 = isinstance(round1_assessment, dict) and isinstance(round1_assessment.get("knowledge"), list)
    r1_knowledge = round1_assessment.get("knowledge") if has_round1 else []

    auto_merged, added = merge_knowledge_lists(orig_knowledge, r1_knowledge, main_type=main_type, is_group=is_group)
    explicit_manual = "knowledge" in manual_labels and isinstance(manual_labels.get("knowledge"), list)
    if explicit_manual:
        final_knowledge = _clean_paths(manual_labels["knowledge"])
    else:
        final_knowledge = list(auto_merged)

    return {
        "knowledge": final_knowledge,
        "auto_merged": auto_merged,
        "added_from_round1": added,
        "has_round1": has_round1,
        "manually_edited": explicit_manual,
    }


def _child_r1_knowledge_map(assessment: dict | None) -> dict[str, list[str]]:
    if not isinstance(assessment, dict):
        return {}
    out: dict[str, list[str]] = {}
    for row in assessment.get("subquestions") or []:
        if not isinstance(row, dict):
            continue
        num = str(row.get("number") or "").strip()
        if num.isdigit() and isinstance(row.get("knowledge"), list):
            out[str(int(num))] = _clean_paths(row["knowledge"])
    return out


def project_batch_merged_knowledge(batch: dict, batch_dir: Path | str | None) -> dict:
    """Return a copy of ``batch`` with 「标签合并」 knowledge applied for upload.

    Only updates ``knowledge`` (and matching ``knowledgeIds``/``knowledge_ids``),
    never ``type``, ``difficulty``, ``subtypes``, etc.
    Manual edits in ``candidates/units/<unit_id>.json`` override auto-merged tags.
    """
    result = deepcopy(batch)
    if batch_dir is None or not isinstance(result, dict) or not isinstance(result.get("items"), list):
        return result
    folder = Path(batch_dir)
    r1_map = load_round1_assessments(folder)
    for item in result["items"]:
        if not isinstance(item, dict):
            continue
        uid = str(item.get("unit_id") or item.get("id") or "").strip()
        if not uid:
            continue
        main_type = str(item.get("type") or item.get("question_type") or "").strip() or None
        manual = load_manual_unit_labels(folder, uid)
        r1 = r1_map.get(uid)
        children = item.get("subquestions")
        is_group = (item.get("unit_kind") == "group") or (isinstance(children, list) and bool(children))
        whole_unit = bool(main_type and taxonomy.whole_unit_type(main_type))

        # 1. Unit/group-level knowledge merge (or manual override).
        # 只要是组题，标签统一打在组题级，不在子题中呈现或合并。
        info = merged_unit_knowledge(
            folder,
            uid,
            item.get("knowledge"),
            main_type=main_type,
            manual_labels=manual,
            round1_assessment=r1,
            is_group=is_group,
        )
        if info["manually_edited"] or info["has_round1"]:
            item["knowledge"] = info["knowledge"]
            _sync_knowledge_ids(item, info["knowledge"], main_type)
        if is_group and isinstance(children, list) and children:
            for child in children:
                if not isinstance(child, dict):
                    continue
                if child.get("knowledge"):
                    child["knowledge"] = []
                    _sync_knowledge_ids(child, [], main_type)
    return result
