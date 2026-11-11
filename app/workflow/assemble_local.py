"""组装阶段的确定性合并（首次来源完整草稿，或结构未变的重跑）。

上次组装被接受时会登记结构指纹与批次指纹（`06_batch/assemble-guard.json`）。
只要结构确认文件或批次产物在那之后发生过变化，就判定不可用并交回模型组装，
绝不沿用旧题干、旧选项或旧材料。

可用时，题干、选项、材料、表格、附件与子题结构原样沿用已审核批次，只用最新
四路产物覆盖：题型、子类、词数、答案、难度、解析、大意与知识标签。产物仍走
`agent_tasks.accept_result` 整批校验与导入，校验不过不安装、不伪装成功。
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

BATCH = "06_batch/batch.json"
GUARD = "06_batch/assemble-guard.json"
FALLBACK = "06_batch/deterministic-fallback.json"
STRUCTURE = "01_source/structure.confirmed.json"
KNOWLEDGE = "04_knowledge/knowledge.json"
EXPLANATIONS = "04_knowledge/explanations.json"
TYPES = "03_types/types.json"
ANSWERS = "01_source_ans/answers.json"


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _unit_id(item: dict) -> str:
    return str(item.get("id") or item.get("unit_id"))


# 重新打标签/换解析时这些字段会变，但它们不是结构；结构字段（题干/选项/
# 材料/子题骨架）一旦被改动就必须交回模型，不能沿用。
_LABEL_KEYS = frozenset((
    "knowledge", "knowledgeIds", "knowledge_ids", "answer", "answers",
    "explanation", "explanations", "articleSummary", "article_summary",
    "difficulty", "difficultyEvidence", "difficulty_evidence",
    "filterMetadata", "subtypes", "categories", "tags", "words",
    "contentComplete", "textbook", "evidence", "review", "reviewNote",
    "status", "notes", "findings",
))


def _structure_digest(payload) -> str:
    """SHA-256 over the payload with label-only fields removed, recursively."""
    def strip(value):
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if k not in _LABEL_KEYS}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value
    return hashlib.sha256(json.dumps(strip(payload), ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def record_guard(batch_dir) -> None:
    """在每次组装被接受后登记基线；下一次重跑凭它判断结构是否未变。"""
    batch = Path(batch_dir)
    structure, assembled = batch / STRUCTURE, batch / BATCH
    if not structure.is_file() or not assembled.is_file():
        return
    try:
        payload = _load(assembled)
        items = payload.get("items") or []
    except (OSError, ValueError):
        return
    from . import stage_semantics
    guard = {"structure_sha256": _sha(structure), "batch_sha256": _sha(assembled),
             "confirmed_content_digest": stage_semantics.digest(stage_semantics._projection("knowledge", STRUCTURE, _load(structure))),
             "unit_ids": sorted(_unit_id(i) for i in items),
             "structure_digest": _structure_digest(payload)}
    target = batch / GUARD
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(guard, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    temporary.replace(target)


def _initial_payload(batch_dir):
    """Promote only a complete source-bound draft into an assembly candidate.

    This is not installation or approval: the normal importer still validates
    all labels, source fidelity, children, media and format before writing batch.
    """
    from . import question_draft
    batch = Path(batch_dir)
    for rel in (STRUCTURE, KNOWLEDGE, EXPLANATIONS, TYPES, ANSWERS):
        if not (batch / rel).is_file():
            raise ValueError("首次确定性组装缺少 " + rel)
    draft = question_draft.ensure(batch)
    if not draft.get("items") or draft.get("issues"):
        raise ValueError("前置题目对象仍有来源或结构疑点，不能确定性组装")
    rows = copy.deepcopy(draft["items"])
    bindings = draft.get("source_bindings") or {}
    if any(not bindings.get(_unit_id(row), {}).get("start_line") for row in rows):
        raise ValueError("前置题目对象缺少逐单元来源绑定")
    meta = _load(batch / STRUCTURE).get("meta") or {}
    from .metadata_policy import resolved_paper_meta
    meta = resolved_paper_meta(meta, batch)
    from .wire_format import _year
    for row in rows:
        filters = row.setdefault("filterMetadata", {})
        for key in ("source", "source_type", "year", "region"):
            if meta.get(key) not in (None, "", []):
                filters[key] = _year(meta[key]) if key == "year" else meta[key]
    return {"items": rows}


def guard_status(batch_dir):
    """返回 (是否可确定性组装, 原因)。任何不确定都交回模型。"""
    batch = Path(batch_dir)
    from . import batch_fill
    if (batch / batch_fill.STATE).is_file():
        try:
            batch_fill.initialize(batch)
            return True, "前置批次已建立，仅核对并固化同一批次的填充结果"
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return False, "前置批次存在冲突：" + str(exc)
    if not (batch / BATCH).exists():
        try:
            _initial_payload(batch)
            return True, "前置标准题目对象已完整绑定原文，可直接合并标签"
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return False, str(exc)
    for rel in (BATCH, STRUCTURE, KNOWLEDGE, EXPLANATIONS, TYPES, ANSWERS):
        if not (batch / rel).is_file():
            return False, f"缺少 {rel}"
    baseline = batch / GUARD
    if not baseline.is_file():
        # 旧/半成品批次没有 guard：只要现有 batch.json 能逐题回指当前确认结构，
        # 就先把它的题干/选项当作可信结构补登记基线，之后同结构重跑即可走
        # 确定性路径。回指核对不过则不登记，交回模型（不沿用来历不明的旧题干）。
        ok, problems = verify_against_structure(batch)
        if not ok:
            return False, ("尚无组装基线且现有批次无法回指当前结构：需模型重新组装"
                           + (f"（{problems[0]}）" if problems else ""))
        record_guard(batch)
        baseline = batch / GUARD
        if not baseline.is_file():
            return False, "尚无组装基线（无法登记）"
    try:
        guard = _load(baseline)
    except (OSError, ValueError):
        return False, "组装基线不可读"
    if guard.get("structure_sha256") != _sha(batch / STRUCTURE):
        from . import stage_semantics
        current_structure = stage_semantics.digest(stage_semantics._projection("knowledge", STRUCTURE, _load(batch / STRUCTURE)))
        if not guard.get("confirmed_content_digest") or guard["confirmed_content_digest"] != current_structure:
            return False, "结构确认已变化：不能沿用旧题干/选项/材料"
    # 标签产物（types/answers/explanations/knowledge）更新后批次指纹必然变化，
    # 这正是确定性重组装要覆盖的场景；只有"结构字段"被改动才交回模型。
    assembled = _load(batch / BATCH)
    if guard.get("batch_sha256") != _sha(batch / BATCH):
        current = _structure_digest(assembled)
        recorded = guard.get("structure_digest")
        if recorded:
            if recorded != current:
                return False, "批次结构在基线之后被改动：需重新组装"
        else:
            # 旧基线没有结构指纹：与确认结构重建结果比对一次，等价才放行。
            try:
                rebuilt = _structure_digest(build(batch))
            except (OSError, ValueError, KeyError, TypeError):
                return False, "批次产物在基线之后被改动且无法确定性重组装"
            if rebuilt != current:
                return False, "批次结构与确认结构不一致：需重新组装"
    from . import agent_tasks
    confirmed = {u["unit_id"] for u in agent_tasks.confirmed_units(batch)}
    existing = {_unit_id(i) for i in assembled.get("items", [])}
    if not confirmed or confirmed != existing:
        return False, "确认单元集合与已审核批次不一致"
    return True, "结构未变，可确定性重组装"


def available(batch_dir) -> bool:
    """True only when deterministic reassembly is safe for the current batch."""
    try:
        ok, _reason = guard_status(batch_dir)
        return bool(ok)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _overlay_labels(target: dict, source: dict) -> None:
    if "knowledge" not in source:
        return
    target["knowledge"] = list(source.get("knowledge") or [])
    ids = list(source.get("knowledgeIds") or [])
    if not ids and target["knowledge"]:
        from workbench import taxonomy
        resolved = taxonomy.resolve_knowledge(target["knowledge"])
        if not resolved["unknown"] and not resolved["non_leaf"]:
            ids = [point["id"] for point in resolved["resolved"]]
    target["knowledgeIds"] = ids
    target["knowledge_ids"] = list(ids)


def _overlay_text(target: dict, primary: dict, secondary: dict, key: str) -> None:
    # 解析/大意以 explanations 为准，缺失时回落 knowledge 里的同名注入字段。
    if key in primary:
        target[key] = primary[key]
    elif key in secondary:
        target[key] = secondary[key]


def build(batch_dir) -> dict:
    """用已接受结构或首次来源绑定草稿，确定性叠加上游标签。"""
    batch = Path(batch_dir)
    from . import batch_fill
    if (batch / batch_fill.STATE).is_file():
        # Validate ownership/source freshness before touching the existing objects.
        data = copy.deepcopy(batch_fill.initialize(batch)["payload"])
    else:
        data = _load(batch / BATCH) if (batch / BATCH).exists() else _initial_payload(batch)
    types = {r["unit_id"]: r for r in _load(batch / TYPES).get("items", [])}
    know = {r["unit_id"]: r for r in _load(batch / KNOWLEDGE).get("items", [])}
    expl = {r["unit_id"]: r for r in _load(batch / EXPLANATIONS).get("items", [])}
    answer_data = _load(batch / ANSWERS) if (batch / ANSWERS).is_file() else {"answers": {}, "difficulty": {}}
    answers = answer_data.get("answers") or {}
    difficulty = answer_data.get("difficulty") or {}
    from .agent_tasks import confirmed_units
    from .question_identity import question_key, fields as identity_fields
    units = {u["unit_id"]: u for u in confirmed_units(batch)}
    meta = _load(batch / STRUCTURE).get("meta") or {}
    from .metadata_policy import resolved_paper_meta
    meta = resolved_paper_meta(meta, batch)
    from .wire_format import _year

    def _meta_value(key, value):
        # Confirmed paper metadata is often a digit string; the wire shape needs
        # an integer year, and overwriting the normalized value would make the
        # final upload check reject an otherwise valid batch.
        return _year(value) if key == "year" else value

    for item in data.get("items", []):
        for key in ("source", "source_type", "year", "region"):
            if meta.get(key) not in (None, "", []):
                item.setdefault("filterMetadata", {})[key] = _meta_value(key, meta[key])
                if key in item:
                    item[key] = _meta_value(key, meta[key])
        uid = _unit_id(item)
        unit = units.get(uid, item)
        item.update(identity_fields(unit))
        if unit.get("identity_version") == "scoped-v1":
            if item.get("unit_kind") == "standalone":
                item["question_id"] = question_key(unit, item["number"])
            for child in item.get("subquestions") or []:
                child["question_id"] = question_key(unit, child["number"])
        row_type = types.get(uid) or {}
        if row_type.get("type"):
            item["question_type"] = row_type["type"]
        metadata = item.get("filterMetadata")
        if isinstance(metadata, dict) and "subtypes" in row_type:
            metadata["subtypes"] = list(row_type.get("subtypes") or [])
        if row_type.get("words") is not None:
            item["wordCount"] = row_type["words"]

        number = item.get("number")
        # 题组的答案挂在子题上，单元级答案保持原样，避免把首题答案当成整组答案。
        if item.get("unit_kind") == "standalone" and number is not None:
            key = question_key(units.get(uid, item), number)
            if key in answers:
                item["answer"] = answers[key]
            if key in difficulty:
                item["difficulty"] = difficulty[key]

        row_know = know.get(uid) or {}
        row_expl = expl.get(uid) or {}
        _overlay_labels(item, row_know)
        _overlay_text(item, row_expl, row_know, "explanation")
        _overlay_text(item, row_expl, row_know, "articleSummary")

        kids_know = {str(c.get("number")): c for c in row_know.get("subquestions") or []}
        kids_expl = {str(c.get("number")): c for c in row_expl.get("subquestions") or []}
        for child in item.get("subquestions") or []:
            key = str(child.get("number"))
            _overlay_labels(child, kids_know.get(key) or {})
            _overlay_text(child, kids_expl.get(key) or {}, kids_know.get(key) or {}, "explanation")
            answer_key = question_key(units.get(uid, item), child.get("number"))
            if answer_key in answers:
                child["answer"] = answers[answer_key]
            if answer_key in difficulty and "difficulty" in child:
                child["difficulty"] = difficulty[answer_key]
    return data


def build_image_option_repair(batch_dir, payload: dict | None = None) -> dict:
    """Build (never install) narrowly proven repairs for empty image-only options.

    Existing nonempty content is retained. Ambiguous rows stay unchanged with a
    reason; duplicate/unknown unit IDs reject the whole request. The
    candidate must still pass the normal assemble export/import validation.
    """
    from . import agent_tasks, denoise, structure_gate
    from .question_content import IMAGE, _candidate_options, _field_text, _image_refs, question_parts

    root = Path(batch_dir).resolve()
    candidate = copy.deepcopy(_load(root / BATCH) if payload is None else payload)
    units = agent_tasks.confirmed_units(root)
    by_id = {u["unit_id"]: u for u in units}
    items = candidate.get("items") or []
    ids = [_unit_id(row) for row in items]
    if len(ids) != len(set(ids)) or set(ids) != set(by_id):
        raise ValueError("图片选项修复要求批次单元唯一且与确认结构完全对应")
    source = (root / "01_source/正文.txt").read_text(encoding="utf-8-sig")
    structure = _load(root / "01_source/structure.json")
    cleaned, _, _ = denoise.clean_body(source)
    fresh_units = structure_gate._build_units(structure.get("sections") or [], structure.get("groups") or [], cleaned.splitlines())
    fresh = {}
    for unit in fresh_units:
        key = tuple(map(str, unit["numbers"]))
        if key in fresh:
            raise ValueError("当前原文有重复单元边界，不能恢复图片选项")
        fresh[key] = unit
    # A source filename reused anywhere else cannot prove exclusive ownership.
    image_uses = {}
    source_images = [match.group(1).strip() for match in IMAGE.finditer(source)]
    for unit in units:
        for match in IMAGE.finditer(unit.get("stem") or ""):
            name = match.group(1).strip()
            image_uses[name] = image_uses.get(name, 0) + 1
    media = root / "01_source/media"
    repairs, unresolved = [], []

    def blocked(uid, number, reason):
        unresolved.append({"unit_id": uid, "number": number, "reason": reason})

    for row in items:
        uid = _unit_id(row)
        unit = by_id[uid]
        key = tuple(map(str, unit["numbers"]))
        if tuple(map(str, row.get("numbers") or [])) != key or row.get("unit_kind") != unit["unit_kind"]:
            blocked(uid, None, "批次题号或单元类型与确认结构不一致")
            continue
        current = fresh.get(key)
        if current is None or current.get("stem") != unit.get("stem"):
            blocked(uid, None, "确认源片段与当前原文切片不一致")
            continue
        children = row.get("subquestions") or [] if unit["unit_kind"] == "group" else [row]
        numbers = [str(c.get("number")) for c in children]
        if len(numbers) != len(set(numbers)) or set(numbers) != set(key):
            blocked(uid, None, "子题题号缺失、重复或归属不一致")
            continue
        for child in children:
            number = child["number"]
            options = child.get("options") or []
            # Never rewrite strings, attachments, blocks, or existing words.
            empty = [i for i, o in enumerate(options) if isinstance(o, dict)
                     and (o.get("text") is None or isinstance(o.get("text"), str))
                     and not _field_text(o).strip() and not _image_refs(o, str(o.get("key") or o.get("label") or ""))
                     and all(not o.get(k) for k in ("attachments", "blocks", "content", "value", "materialBlocks", "material_blocks"))]
            if not empty:
                continue
            part = question_parts(unit["stem"].splitlines(), int(number))
            expected = part["options"]
            normalized = _candidate_options(options)
            labels = [o["label"] for o in expected]
            if (not part["found"] or not labels or len(options) != len(expected)
                    or len(labels) != len(set(labels)) or labels != [o["label"] for o in normalized]):
                blocked(uid, number, "源题号或完整选项字母序列不能唯一对应")
                continue
            names = [IMAGE.findall(o["text"]) for o in expected]
            if any(len(refs) != 1 or not IMAGE.fullmatch(o["text"].strip()) for o, refs in zip(expected, names)):
                blocked(uid, number, "源选项并非每项一个明确图片引用的纯图片选项")
                continue
            names = [refs[0].strip() for refs in names]
            if (len(set(names)) != len(names)
                    or any(image_uses.get(n) != 1 or source_images.count(n) != 1 for n in names)):
                blocked(uid, number, "源图片在选项或别题重复，归属不能一一证明")
                continue
            if any(i not in empty and normalized[i]["text"].strip() != expected[i]["text"].strip()
                   for i in range(len(expected))):
                blocked(uid, number, "已有非空选项与源图片文字不一致；保留原内容")
                continue
            if (media.resolve() != media or any(Path(n).name != n or "\\" in n
                    or not (media / n).is_file() or (media / n).is_symlink()
                    or (media / n).stat().st_size == 0 for n in names)):
                blocked(uid, number, "源图片在当前01_source/media缺失、为空或路径不安全")
                continue
            for i in empty:
                options[i]["text"] = expected[i]["text"]
                repairs.append({"unit_id": uid, "number": number, "option": labels[i],
                                "source_image": names[i], "media_path": "01_source/media/" + names[i],
                                "media_sha256": _sha(media / names[i])})
    return {"batch": candidate, "repairs": repairs, "unresolved": unresolved}


def _record_fallback(batch_dir, reason: str) -> None:
    try:
        target = Path(batch_dir) / FALLBACK
        target.write_text(json.dumps({"reason": reason}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    except OSError:
        pass


def _norm(text) -> str:
    return "".join(str(text or "").split())


def verify_against_structure(batch_dir):
    """遗留批次建立基线前的逐题核对：单元、题号与每个选项都必须回指当前结构。

    这一步只用于给“模型时代”留下的旧批次补登记基线，避免把来路不明的旧题干
    当成可复用结构。核对不过就不建立基线，交回模型重新组装。
    """
    from . import agent_tasks
    batch = Path(batch_dir)
    units = {u["unit_id"]: u for u in agent_tasks.confirmed_units(batch)}
    items = _load(batch / BATCH).get("items", [])
    if {_unit_id(i) for i in items} != set(units):
        return False, ["单元集合与结构确认不一致"]
    problems = []
    for item in items:
        uid = _unit_id(item)
        unit = units[uid]
        if [str(n) for n in item.get("numbers") or []] != [str(n) for n in unit.get("numbers") or []]:
            problems.append(f"{uid}：题号集合不一致")
        stem = _norm(unit.get("stem"))
        for row in [item] + list(item.get("subquestions") or []):
            for option in row.get("options") or []:
                text = _norm(option.get("text"))
                if text and text not in stem:
                    problems.append(f"{uid}：选项 {option.get('label')} 无法回指结构题干")
    return not problems, problems


def try_assemble(batch_dir, config=None):
    """可确定性重组装时完成导入并返回退出码；否则返回 None 交回模型路径。"""
    from . import batch_fill
    managed = (Path(batch_dir) / batch_fill.STATE).is_file()
    ok, reason = guard_status(batch_dir)
    if not ok:
        print(f"  确定性填充汇总不可用：{reason}", flush=True)
        return 3 if managed else None
    from . import agent_tasks
    try:
        candidate = {"status": "completed",
                     "artifacts": {agent_tasks.ARTIFACTS["assemble"]: build(batch_dir)},
                     "notes": ["确定性组装：沿用来源绑定的前置题目对象或既有结构，合并上游标签，正常执行整批保真校验，未调用模型"],
                     "findings": []}
        agent_tasks.accept_result(batch_dir, "assemble", candidate, validate_only=True)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        _record_fallback(batch_dir, str(exc))
        if managed:
            print(f"  前置批次填充结果未通过整批校验，保留同批次等待修正：{exc}", flush=True)
            return 3
        print(f"  确定性产物未通过整批校验，交回模型组装：{exc}", flush=True)
        return None
    from .local_agent import _agent_lock
    timeout = 900
    if isinstance(config, dict):
        timeout = config.get("timeout_seconds", 900)
    with _agent_lock(timeout):
        report = agent_tasks.accept_result(batch_dir, "assemble", candidate)
    if report.get("completed"):
        record_guard(batch_dir)
        fallback = Path(batch_dir) / FALLBACK
        if fallback.exists():
            fallback.unlink()
    print(json.dumps({"deterministic": True, "model_calls": 0, "report": report}, ensure_ascii=False), flush=True)
    return 0 if report.get("completed") else 3


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-dir", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="只报告守卫状态与字段差异，不写任何文件")
    parser.add_argument("--seed", action="store_true", help="为旧批次补登记基线：先逐题回指结构，核对不过不登记")
    args = parser.parse_args(argv)
    batch = args.batch_dir.expanduser().resolve()
    if args.seed:
        ok, problems = verify_against_structure(batch)
        print(json.dumps({"verified": ok, "problems": problems[:20], "problem_count": len(problems)}, ensure_ascii=False))
        if not ok:
            return 3
        record_guard(batch)
        print("已登记组装基线")
    ok, reason = guard_status(batch)
    print(json.dumps({"reusable": ok, "reason": reason}, ensure_ascii=False))
    if ok and args.check:
        before = _load(batch / BATCH) if (batch / BATCH).is_file() else {"items": []}
        after = build(batch)
        diffs = []
        for old, new in zip(before.get("items", []), after.get("items", [])):
            for key in new:
                if old.get(key) != new.get(key):
                    diffs.append({"unit_id": _unit_id(new), "field": key})
        print(json.dumps({"field_differences": diffs, "count": len(diffs)}, ensure_ascii=False))
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
