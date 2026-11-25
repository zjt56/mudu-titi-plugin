# -*- coding: utf-8 -*-
"""Review comparison, answer-point tagging, and unit label payload helpers.

Extracted from app/workbench/server.py to keep the HTTP server module focused.
All public symbols are re-exported by workbench.server for full compatibility.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path

from workflow import contracts as wf_contracts, manifest as wf_manifest
from workflow.workflow_primitives import read_json_safe as load
from . import taxonomy

_WINDOWS = os.name == 'nt'


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


def _knowledge_projection(path):
    """「二级/末级」短名投影，与 preview 快速核对标签的写法一致。"""
    parts = [p for p in str(path or "").split("/") if p.strip()]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return parts[1]
    return parts[1] + "/" + parts[-1]


def drop_short_knowledge_copies(paths):
    """去掉同一知识点的短名副本，保留目录全路径。

    旧批次/预览行会把 `设题角度/阅读/细节理解` 记成 `阅读/细节理解`、
    把 `主题/人与社会/历史、社会与文化/文化习俗` 记成 `人与社会/文化习俗`
    这类「二级/末级」写法；它与全路径指向同一叶子，混在一起会在修改标签
    弹窗里显示两遍，并让短名被校验成目录外路径。
    """
    unique = []
    for raw in paths or []:
        path = str(raw or "").strip()
        if path and path not in unique:
            unique.append(path)
    return [path for path in unique
            if not any(other != path and (other.endswith("/" + path)
                                          or _knowledge_projection(other) == path)
                       for other in unique)]


def _pending_answer_point_tags(folder, unit_id, *, number=None, review_hashes=None):
    """Read historical candidates only while their original inputs still match."""
    pending = load(folder / "04_knowledge/answer-point-tags.pending.json", {})
    if (not isinstance(pending, dict) or pending.get("status") != "pending_catalog_binding"
            or pending.get("batch") != folder.name or not isinstance(pending.get("labels"), list)):
        return []
    rules = taxonomy.type_knowledge_rules()
    candidates = dict.fromkeys(
        entry["name"] for entry in pending["labels"]
        if isinstance(entry, dict) and entry.get("unit_id") == unit_id
        and isinstance(entry.get("name"), str) and entry["name"].strip()
        and entry.get("proposed_path") == "词汇/答案考点/" + entry["name"]
        and isinstance(entry.get("question_type"), str)
        and any(dim.get("dim") == "答案考点"
                for dim in rules.get(entry["question_type"], {}).get("dims", []))
        and (number is None or any(isinstance(source, dict) and str(source.get("number")) == str(number)
                                  for source in (entry.get("sources") or [])))
    )
    if not candidates:
        return []
    hashes = pending.get("source_sha256")
    if not isinstance(hashes, dict):
        return []
    for relative in ("06_batch/batch_meta.json", "01_source_ans/answers.json"):
        if review_hashes is not None and review_hashes.get(relative) != hashes.get(relative):
            return []
        try:
            if hashlib.sha256((folder / relative).read_bytes()).hexdigest() != hashes.get(relative):
                return []
        except OSError:
            return []
    return list(candidates)


def _answer_point_terms(answer, options):
    from workflow.content_rules import is_placeholder
    from workflow.wire_format import OPTION_PREFIX, option_strings

    if is_placeholder(answer):
        return []
    text = re.sub(r'</?(?:u|b|strong|em|i)>|\*\*', '', answer, flags=re.I).strip()
    if options:
        if not re.fullmatch(r'[A-Z]', text):
            return []
        choices = [option[match.end():] for option in option_strings(options)
                   if (match := OPTION_PREFIX.match(option)) and match.group(1) == text]
        if len(choices) != 1:
            return []
        text = re.sub(r'</?(?:u|b|strong|em|i)>|\*\*', '', choices[0], flags=re.I)
        text = re.sub(r'\s+[（(]\s*[）)]\s*$', '', text)
    text = text.strip()
    numbered = list(re.finditer(r'(?<!\S)(\d+)[.．、]\s*(\S.*?)(?=\s+\d+[.．、]|$)', text, re.S))
    if (len(numbered) > 1 and numbered[0].start() == 0
            and [part[1] for part in numbered] == [str(index + 1) for index in range(len(numbered))]):
        text = ';'.join(part[2] for part in numbered)
    terms = []
    for piece in re.split(r'[/／;；]', text):
        term = ' '.join(piece.split())
        if not re.fullmatch(r"[A-Za-z]+(?:['’\-‐‑⁃][A-Za-z]+)*(?: [A-Za-z]+(?:['’\-‐‑⁃][A-Za-z]+)*)*", term):
            continue
        if re.match(r"(?i)^(?:(?:I|he|she|it|we|you|they|this|that|there)(?:['’](?:m|re|s|ve|d|ll) | (?:am|is|are|was|were|have|has|had|do|does|did|will|can|must|should)\b))", term):
            continue
        terms.append(term)
    return terms


def _prepare_answer_point_ctx(folder):
    """Pre-load batch-level inputs for answer_point_tags so bulk detail avoids N*disk reads."""
    from workflow.agent_tasks import confirmed_units

    answer_path = '01_source_ans/answers.json'
    type_path = '03_types/types.json'
    batch_path = '06_batch/batch_meta.json'
    try:
        manifest = wf_manifest.load(folder)
        record = manifest['stages']['answers']
        if (record.get('status') != wf_manifest.COMPLETED or record.get('rebuild_required')
                or not (record.get('output_hashes') or {}).get(answer_path)
                or wf_manifest.is_stale(folder, manifest, wf_contracts.STAGE_BY_ID['answers'])[0]):
            return {'status': 'empty'}
        c_units = confirmed_units(folder)
        batch = load(folder / batch_path, {})
        type_data = load(folder / type_path, {})
        answers_obj = load(folder / answer_path, {})
        rows = batch.get('items', []) if isinstance(batch, dict) else []
        type_rows = type_data.get('items', []) if isinstance(type_data, dict) else []
        by_uid = {}
        for item in rows:
            if isinstance(item, dict) and item.get('unit_id') is not None:
                by_uid.setdefault(item.get('unit_id'), []).append(item)
        types_by_uid = {}
        for item in type_rows:
            if isinstance(item, dict) and item.get('unit_id') is not None:
                types_by_uid.setdefault(item.get('unit_id'), []).append(item)
        units_by_uid = {}
        for row in c_units:
            if isinstance(row, dict) and row.get('unit_id') not in units_by_uid:
                units_by_uid[row.get('unit_id')] = row
        file_hashes = {}
        for relative in (answer_path, type_path, batch_path):
            try:
                file_hashes[relative] = wf_manifest.sha256_file(folder / relative)
            except OSError:
                file_hashes[relative] = None
        return {
            'status': 'ready',
            'units_by_uid': units_by_uid,
            'by_uid': by_uid,
            'types_by_uid': types_by_uid,
            'answers': answers_obj.get('answers', {}) if isinstance(answers_obj, dict) else {},
            'rules': taxonomy.type_knowledge_rules(),
            'file_hashes': file_hashes,
        }
    except FileNotFoundError:
        return {'status': 'pending'}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {'status': 'empty'}


def answer_point_tags(folder, unit_id, *, number=None, review_hashes=None, ap_ctx=None):
    """Project original answer points from the accepted keys and final options."""
    from workflow.agent_tasks import _answer_comparison_text, confirmed_units
    from workflow.question_identity import question_key

    answer_path = '01_source_ans/answers.json'
    type_path = '03_types/types.json'
    batch_path = '06_batch/batch_meta.json'
    try:
        if ap_ctx is not None:
            if ap_ctx.get('status') == 'pending':
                return _pending_answer_point_tags(folder, unit_id, number=number, review_hashes=review_hashes)
            if ap_ctx.get('status') != 'ready':
                return []
            if review_hashes is not None and any(
                    not review_hashes.get(relative)
                    or review_hashes[relative] != ap_ctx['file_hashes'].get(relative)
                    for relative in (answer_path, type_path, batch_path)):
                return []
            unit = ap_ctx['units_by_uid'].get(unit_id)
            matches = ap_ctx['by_uid'].get(unit_id, [])
            type_matches = ap_ctx['types_by_uid'].get(unit_id, [])
            rules = ap_ctx['rules']
            answers = ap_ctx['answers']
        else:
            manifest = wf_manifest.load(folder)
            record = manifest['stages']['answers']
            if (record.get('status') != wf_manifest.COMPLETED or record.get('rebuild_required')
                    or not (record.get('output_hashes') or {}).get(answer_path)
                    or wf_manifest.is_stale(folder, manifest, wf_contracts.STAGE_BY_ID['answers'])[0]):
                return []
            if review_hashes is not None and any(
                    not review_hashes.get(relative)
                    or review_hashes[relative] != wf_manifest.sha256_file(folder / relative)
                    for relative in (answer_path, type_path, batch_path)):
                return []
            unit = next((row for row in confirmed_units(folder) if row['unit_id'] == unit_id), None)
            batch = load(folder / batch_path, {})
            type_data = load(folder / type_path, {})
            rows = batch.get('items', []) if isinstance(batch, dict) else []
            type_rows = type_data.get('items', []) if isinstance(type_data, dict) else []
            matches = [item for item in rows if isinstance(item, dict) and item.get('unit_id') == unit_id]
            type_matches = [item for item in type_rows if isinstance(item, dict) and item.get('unit_id') == unit_id]
            rules = taxonomy.type_knowledge_rules()
            answers = load(folder / answer_path, {}).get('answers', {})
        if unit is None or len(matches) != 1 or len(type_matches) != 1:
            return []
        item = matches[0]
        type_item = type_matches[0]
        main_type = type_item.get('type') or type_item.get('question_type')
        if (item.get('question_type') or item.get('type')) != main_type:
            return []
        if not any(dim.get('dim') == '答案考点'
                   for dim in rules.get(main_type, {}).get('dims', [])):
            return []
        children = item.get('subquestions') if unit['unit_kind'] == 'group' else [item]
        terms = []
        for child_number in unit['numbers']:
            if number is not None and str(number) != str(child_number):
                continue
            key = question_key(unit, child_number)
            children_at_number = [child for child in children or [] if isinstance(child, dict)
                                  and str(child.get('number')) == str(child_number)]
            if len(children_at_number) != 1:
                continue
            child = children_at_number[0]
            if unit.get('identity_version') == 'scoped-v1' and child.get('question_id') != key:
                continue
            value, current = answers.get(key), child.get('answer')
            if (isinstance(value, str) and isinstance(current, str)
                    and _answer_comparison_text(value) == _answer_comparison_text(current)):
                terms.extend(_answer_point_terms(value, child.get('options') or item.get('options') or []))
        return list(dict.fromkeys(terms))
    except FileNotFoundError:
        return _pending_answer_point_tags(folder, unit_id, number=number, review_hashes=review_hashes)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return []


def label_payload(row, batch_item, subs, folder, unit_id=None, *, ap_ctx=None, r1_assessments=None):
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
    if explicit_knowledge:
        knowledge_sources = [unit_labels["knowledge"]]
    else:
        knowledge_sources = [item.get("knowledge")]
        # 预览行里的知识点是「二级/末级」短名投影（preview 的 knowledge_projection /
        # 重新分组候选的 source_projection），只能用于卷面速览；与批次全路径合并
        # 会让同一条知识点出现两遍，且短名会被判成「目录外路径」。
        if not row.get("source_projection") and not row.get("knowledge_projection"):
            knowledge_sources.append(row.get("knowledge"))
    for source in knowledge_sources:
        for path in source or []:
            if str(path).strip() and str(path).strip() not in knowledge:
                knowledge.append(str(path).strip())
    knowledge = drop_short_knowledge_copies(knowledge)
    if not knowledge and not explicit_knowledge:
        for sub in subs:
            for path in sub.get("knowledge") or []:
                if str(path).strip() and str(path).strip() not in knowledge:
                    knowledge.append(str(path).strip())
    main_type = (unit_labels.get("type") or item.get("question_type") or item.get("type")
                 or row.get("question_type") or row.get("type") or "")
    from workflow import label_merge
    uid_key = str(unit_id or row.get("unit_id") or "")
    r1_kwargs = {"round1_assessment": r1_assessments.get(uid_key)} if isinstance(r1_assessments, dict) else {}
    merged_info = label_merge.merged_unit_knowledge(
        folder, uid_key, knowledge, main_type=main_type, manual_labels=unit_labels, **r1_kwargs
    ) if uid_key else {"knowledge": knowledge, "auto_merged": knowledge, "added_from_round1": [], "has_round1": False, "manually_edited": explicit_knowledge}
    if not explicit_knowledge and merged_info.get("has_round1"):
        knowledge = list(merged_info["knowledge"])
    resolved = taxonomy.resolve_knowledge(knowledge, main_type)
    # 旧批次可能保存不带顶级维度的叶子路径；展示和维度校验统一使用
    # 唯一解析出的当前目录全路径，避免加载时误报目录外路径。
    if resolved["resolved"] and not resolved["unknown"] and not resolved["non_leaf"]:
        knowledge = list(dict.fromkeys(point["path"] for point in resolved["resolved"]))
        resolved = taxonomy.resolve_knowledge(knowledge, main_type)
    from workflow.metadata_policy import MISSING
    values = {
        "source": item.get("source") or filters.get("source") or row.get("source") or MISSING,
        "source_type": item.get("source_type") or filters.get("source_type") or row.get("source_type") or MISSING,
        "year": str(item.get("year") or filters.get("year") or row.get("year") or ""),
        "location": item.get("location") or filters.get("location") or row.get("location") or "",
        "type": main_type,
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
    values["answer_point_tags"] = answer_point_tags(folder, values["unit_id"], ap_ctx=ap_ctx)
    values["subquestion_count"] = len([s for s in subs if s.get("stem") or s.get("options")])
    values["knowledge_editable"] = True
    values["knowledge_manually_saved"] = explicit_knowledge
    values["knowledge_merged_from_round1"] = merged_info.get("added_from_round1", [])
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
    # Windows lacks POSIX directory-fd flags. Keep the same bounded, symlink-free
    # check using resolved paths and identity snapshots instead of disabling it.
    if _WINDOWS:
        try:
            root = Path(folder).resolve(strict=True)
            workflow = root / "workflow"
            history = workflow / "review-history"
            if not workflow.is_dir() or workflow.is_symlink() or not history.is_dir() or history.is_symlink():
                return False
            for entry in history.iterdir():
                if not re.fullmatch(r"[0-9a-f]{32}\.json", entry.name) or entry.is_symlink():
                    continue
                try:
                    resolved = entry.resolve(strict=True)
                    resolved.relative_to(history)
                    info = resolved.stat()
                    if not stat.S_ISREG(info.st_mode) or info.st_size > 32 * 1024 * 1024:
                        continue
                    with resolved.open("rb") as stream:
                        data = json.load(stream)
                    if isinstance(data, dict) and isinstance(data.get("plan"), dict) and isinstance(data.get("rounds"), list):
                        return True
                except (OSError, UnicodeError, ValueError, RecursionError):
                    continue
        except OSError:
            return False
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


def _prepare_review_comparison_ctx(folder: Path):
    """Load batch-level review snapshot, bound inputs, and round indices once."""
    snapshot = load(folder / "07_review/review-comparison.json", {})
    snapshot_valid = (isinstance(snapshot, dict) and snapshot.get("version") == 1
                      and snapshot.get("execution_completed") is True
                      and isinstance(snapshot.get("plan"), dict)
                      and isinstance(snapshot.get("rounds"), list))
    if snapshot_valid:
        records = snapshot.get("rounds", [])
        checked = {"labels"}
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

    hashes = plan.get("input_hashes") or {}

    def bound(relative):
        if snapshot_valid:
            key = {"03_types/types.json": "types", "04_knowledge/knowledge.json": "knowledge",
                   "01_source_ans/answers.json": "answers"}.get(relative)
            return snapshot_original.get(key, {}) if key else {}
        return load(folder / relative, {}) if checked and relative in hashes else {}

    def index_unique(data):
        rows = data.get("items", []) if isinstance(data, dict) else []
        grouped = {}
        for row in rows:
            if isinstance(row, dict):
                key = str(row.get("unit_id") or "")
                grouped.setdefault(key, []).append(row)
        return {k: v[0] for k, v in grouped.items() if len(v) == 1}

    return {
        "records": records,
        "checked": checked,
        "hashes": hashes,
        "snapshot_stale": bool(snapshot_stale),
        "source_name": source_name,
        "types_by_uid": index_unique(bound("03_types/types.json")),
        "knowledge_by_uid": index_unique(bound("04_knowledge/knowledge.json")),
        "answers": bound("01_source_ans/answers.json"),
        "history_exists": has_unverified_review_history(folder),
        "rules": taxonomy.type_knowledge_rules(),
    }


def review_comparison(folder: Path, unit_id, numbers, *, rc_ctx=None, ap_ctx=None):
    """读取输入绑定有效的两轮判断及其原始标签，保留逐题抽样范围。"""
    uid = str(unit_id or "")
    if rc_ctx is None:
        rc_ctx = _prepare_review_comparison_ctx(folder)
    records = rc_ctx["records"]
    checked = rc_ctx["checked"]
    hashes = rc_ctx["hashes"]
    snapshot_stale = rc_ctx["snapshot_stale"]
    source_name = rc_ctx["source_name"]
    types = rc_ctx["types_by_uid"].get(uid, {})
    knowledge = rc_ctx["knowledge_by_uid"].get(uid, {})
    answers = rc_ctx["answers"]
    rules = rc_ctx["rules"]
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
    history_exists = rc_ctx["history_exists"]
    for rid, name in (("labels", "一审"),):
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
                expected_answers = answer_point_tags(folder, uid, number=check.get("number"), review_hashes=hashes, ap_ctx=ap_ctx)
                reviewed_answers = assessment.get("answer_points")
                if reviewed_answers is None and assessment and check.get("verdict") == "ok" and expected_answers:
                    # Older runs already stated some answers in their reasons.
                    # Only recover those exact terms against the same bound inputs.
                    rationale = str(check.get("rationale") or "")
                    reviewed_answers = [value for value in expected_answers
                                        if re.search(r"(?<![A-Za-z'-])" + re.escape(value) + r"(?![A-Za-z'-])",
                                                     rationale)]
                if isinstance(reviewed_answers, list):
                    assessment = {**assessment, "answer_points": reviewed_answers}
                def _ap_disabled(t):
                    rule = rules.get(t.strip()) if isinstance(t, str) and t.strip() else None
                    return bool(rule and not any(dim.get("dim") == "答案考点" for dim in rule.get("dims", [])))
                if _ap_disabled(labels.get("type")) or _ap_disabled(assessment.get("type")):
                    assessment = {key: value for key, value in assessment.items() if key != "answer_points"}
                    expected_answers = []
                elif not expected_answers and not answer_point_tags(folder, uid, number=check.get("number"), ap_ctx=ap_ctx):
                    assessment = {key: value for key, value in assessment.items() if key != "answer_points"}
                entry["checks"].append({"unit_id": uid, "number": check.get("number"),
                                        "scope": ("subquestion" if str(check.get("number") or "").strip()
                                                  else "unit"),
                                        "verdict": check.get("verdict"), "rationale": check.get("rationale"),
                                        "assessment": assessment, "evidence": check.get("evidence"),
                                        "expected_answer_points": expected_answers,
                                        "differences": [
                                            {"field": field, "original": labels[field], "reviewed": assessment[field]}
                                            for field in ("type", "subtypes", "knowledge")
                                            if field in labels and field in assessment
                                            and (sorted(set(labels[field])) if isinstance(labels[field], list) else labels[field])
                                            != (sorted(set(assessment[field])) if isinstance(assessment[field], list) else assessment[field])]})
            entry["status"] = "已提供" if entry["checks"] else "未覆盖"
            entry["source"] = source_name
            entry["execution_id"] = record.get("execution_id")
        result["rounds"].append(entry)
    from workflow import label_merge
    r1_entry = next((r for r in result["rounds"] if r.get("round_id") == "labels"), None)
    r1_unit_check = next((c for c in (r1_entry or {}).get("checks", []) if isinstance(c, dict) and isinstance(c.get("assessment"), dict)), None)
    result["merged"] = label_merge.merged_unit_knowledge(
        folder,
        uid,
        labels.get("knowledge") or [],
        main_type=labels.get("type"),
        round1_assessment=(r1_unit_check or {}).get("assessment"),
    )
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


