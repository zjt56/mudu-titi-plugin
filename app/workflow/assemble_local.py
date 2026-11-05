"""组装阶段的确定性重组装（仅“结构未变”的重跑场景）。

上次组装被接受时会登记结构指纹与批次指纹（`06_batch/assemble-guard.json`）。
只要结构确认文件或批次产物在那之后发生过变化，就判定不可用并交回模型组装，
绝不沿用旧题干、旧选项或旧材料。

可用时，题干、选项、材料、表格、附件与子题结构原样沿用已审核批次，只用最新
四路产物覆盖：题型、子类、词数、答案、难度、解析、大意与知识标签。产物仍走
`agent_tasks.accept_result` 整批校验与导入，校验不过不安装、不伪装成功。
"""
from __future__ import annotations

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
    guard = {"structure_sha256": _sha(structure), "batch_sha256": _sha(assembled),
             "unit_ids": sorted(_unit_id(i) for i in items),
             "structure_digest": _structure_digest(payload)}
    target = batch / GUARD
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(guard, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    temporary.replace(target)


def guard_status(batch_dir):
    """返回 (是否可确定性重组装, 原因)。任何不确定都交回模型。"""
    batch = Path(batch_dir)
    for rel in (BATCH, STRUCTURE, KNOWLEDGE, EXPLANATIONS, TYPES, ANSWERS):
        if not (batch / rel).is_file():
            return False, f"缺少 {rel}"
    baseline = batch / GUARD
    if not baseline.is_file():
        return False, "尚无组装基线：先由模型组装一次，之后同结构重跑才走确定性路径"
    try:
        guard = _load(baseline)
    except (OSError, ValueError):
        return False, "组装基线不可读"
    if guard.get("structure_sha256") != _sha(batch / STRUCTURE):
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
    target["knowledgeIds"] = ids
    target["knowledge_ids"] = list(ids)


def _overlay_text(target: dict, primary: dict, secondary: dict, key: str) -> None:
    # 解析/大意以 explanations 为准，缺失时回落 knowledge 里的同名注入字段。
    if key in primary:
        target[key] = primary[key]
    elif key in secondary:
        target[key] = secondary[key]


def build(batch_dir) -> dict:
    """用已审核批次的结构，叠加最新四路产物；不新增、不删改任何结构字段。"""
    batch = Path(batch_dir)
    data = _load(batch / BATCH)
    types = {r["unit_id"]: r for r in _load(batch / TYPES).get("items", [])}
    know = {r["unit_id"]: r for r in _load(batch / KNOWLEDGE).get("items", [])}
    expl = {r["unit_id"]: r for r in _load(batch / EXPLANATIONS).get("items", [])}
    answer_data = _load(batch / ANSWERS)
    answers = answer_data.get("answers") or {}
    difficulty = answer_data.get("difficulty") or {}

    for item in data.get("items", []):
        uid = _unit_id(item)
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
            key = str(number)
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
            if key in answers:
                child["answer"] = answers[key]
            if key in difficulty and "difficulty" in child:
                child["difficulty"] = difficulty[key]
    return data


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
    ok, reason = guard_status(batch_dir)
    if not ok:
        print(f"  确定性重组装不可用：{reason}", flush=True)
        return None
    from . import agent_tasks
    candidate = {"status": "completed",
                 "artifacts": {agent_tasks.ARTIFACTS["assemble"]: build(batch_dir)},
                 "notes": ["确定性重组装：结构未变，沿用已审核题干/选项/材料，仅覆盖四路产物字段，未调用模型"],
                 "findings": []}
    try:
        agent_tasks.accept_result(batch_dir, "assemble", candidate, validate_only=True)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        _record_fallback(batch_dir, str(exc))
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
        before = _load(batch / BATCH)
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
