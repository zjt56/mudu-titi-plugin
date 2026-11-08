"""Randomized, isolated end-to-end stress lab for the recovery pipeline.

This is a harness, not a production module: it generates synthetic batches from
real parsers, injects defects/faults, drives the real supervisor + self-heal +
repair executors, and returns measurements. It never touches user batches,
never calls the network, and never installs anything outside its own root.
"""
from __future__ import annotations

import copy
import hashlib
import json
import random
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from workbench import taxonomy
from workflow import (assemble_local, auto, batch_fill, contracts, denoise, extract, format_gate,
                      legacy_format, manifest as mf, question_content, question_draft,
                      review, review_legacy, review_rounds, self_heal, self_heal_actions,
                      structure_gate, supervisor)

LISTEN_TYPES = ("听力关键词语选择",)
AGENT_STAGES = {"types", "answers", "explanations", "knowledge", "assemble", "review", "audio"}
LOCAL_STAGES = supervisor.LOCAL_STAGES


# --------------------------------------------------------------------------- helpers

def _write(root: Path, relative: str, value) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
    elif isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _read(root: Path, relative: str, default=None):
    path = root / relative
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


_LEAVES = sorted(taxonomy.knowledge_index()[1])
_LABEL_CACHE: dict[tuple, list[str]] = {}


def valid_knowledge(main_type: str, subtypes=None, unit_kind: str = "standalone") -> list[str]:
    """Pick catalog leaves that the real validator accepts for this unit shape."""
    key = (main_type, tuple(subtypes or ()), unit_kind)
    if key in _LABEL_CACHE:
        return _LABEL_CACHE[key]
    genre = [leaf for leaf in _LEAVES if leaf.startswith("语篇体裁/")]
    others = [leaf for leaf in _LEAVES if not leaf.startswith("语篇体裁/")]
    candidates = []
    for leaf in (genre if unit_kind == "group" else []) + others:
        candidates.append([leaf])
    if unit_kind == "group":
        candidates += [[g, o] for g in genre[:12] for o in others[:60:6]]
    for paths in candidates:
        probe = {"question_type": main_type, "unit_kind": unit_kind,
                 "filterMetadata": {"subtypes": list(subtypes or [])}, "knowledge": paths}
        if not taxonomy.check_question_labels(probe):
            _LABEL_CACHE[key] = list(paths)
            return list(paths)
    raise AssertionError("no valid leaf for " + main_type + "/" + unit_kind)


def valid_subtypes(main_type: str, option_count: int = 0, children: int = 0) -> list[str]:
    """Choose the taxonomy subtype that matches the real option/child count."""
    options = taxonomy.subtypes(main_type) or []
    if not options:
        return []
    wanted = {2: "2", 3: "三", 4: "四"}.get(children or option_count)
    if children and any(str(children) in value for value in options):
        return [next(value for value in options if str(children) in value)]
    for value in options:
        if wanted and wanted in value:
            return [value]
    return options[:1]


# --------------------------------------------------------------------------- fixture

@dataclass
class Fixture:
    root: Path
    seed: int
    units: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    build_ms: float = 0.0


def reading_child_count(rng: random.Random) -> int:
    """Pick a child count that a real reading subtype actually names."""
    options = taxonomy.subtypes("阅读理解") or []
    allowed = sorted({int(match.group(1)) for value in options
                      for match in [re.match(r"选择(\d+)题", value)] if match})
    return rng.choice(allowed) if allowed else rng.randint(2, 4)


def compose_source(rng: random.Random, plan) -> tuple[str, dict]:
    """Build paper text and the matching meta from a random section plan."""
    body, labels, number = [], [], 0
    roman = "ⅠⅡⅢⅣⅤⅥⅦⅧ"
    index = 0
    for kind, count in plan:
        if kind == "listening":
            start = number + 1
            body.append(f"{roman[index]}.关键词语选择(共{count}小题)")
            body.append("你将听到若干句子。请在每小题所给的A、B、C三个选项中选出一个你所听到的单词或短语。")
            for k in range(count):
                number += 1
                word = rng.choice(["look", "knock", "cook", "dream", "cream", "team", "part", "pass", "place"])
                body.append(f"{number}.A.{word}a{k}\tB.{word}b{k}\tC.{word}c{k}\t()")
            labels.append((kind, start, number))
            index += 1
        elif kind == "mc":
            body.append(f"{roman[index]}.单项填空(共{count}小题)")
            for k in range(count):
                number += 1
                verb = rng.choice(["reads", "writes", "sings", "draws"])
                body.append(f"{number}.She ______ a book {number} and the {verb} part.\n"
                            f"A.{verb} B.read C.reading D.to read")
            labels.append((kind, number - count + 1, number))
            index += 1
        else:
            body.append(f"{roman[index]}.阅读理解(共{count}小题)")
            body.append("阅读下列短文，从每小题所给的A、B、C、D四个选项中选出最佳选项。")
            letter = rng.choice(["A", "B", "C"])
            body.append(letter)
            body.append(f"The passage {index} title about {rng.choice(['school', 'family', 'sports'])}.")
            body.append(f"Some supporting text for passage {index}.")
            start = number + 1
            for k in range(count):
                number += 1
                body.append(f"{number}.Question {number} about passage {index}?\n"
                            f"A.Alpha{k} B.Beta{k} C.Gamma{k} D.Delta{k}")
            labels.append((kind, start, number))
            index += 1
    return "\n".join(body), {"sections": labels}


def build_fixture(root: Path, seed: int, *, standalone_count=None, only_standalone=False) -> Fixture:
    """Create a fully labeled, upload-valid batch with real review evidence."""
    started = time.perf_counter()
    rng = random.Random(seed)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    # Every fault campaign must contain the shape named by its case. Never
    # silently replace a requested reading/listening defect with another fault.
    plan = [("listening", rng.randint(1, 4)),
            ("mc", standalone_count if standalone_count is not None else rng.randint(1, 8))]
    if only_standalone:
        plan = [("mc", standalone_count if standalone_count is not None else 2)]
    else:
        for _ in range(rng.randint(1, 2)):
            plan.append(("reading", reading_child_count(rng)))
    text, _ = compose_source(rng, plan)
    structure, body, _ = extract.infer_structure(text)
    cleaned = denoise.clean_body(body)[0]
    units = structure_gate._build_units(structure.get("sections") or [],
                                        structure.get("groups") or [], cleaned.splitlines())
    if not units:
        raise AssertionError("fixture produced no units")
    meta = {"source": f"{rng.randint(2005, 2025)}年测试卷{seed}",
            "source_type": "中考真题",
            "year": str(rng.randint(2005, 2025)),
            "region": rng.choice(["安徽省", "江苏省", "北京市"]),
            "paper_title": f"{seed}号合成试卷"}
    (root / "01_source").mkdir(parents=True, exist_ok=True)
    (root / "01_source/正文.txt").write_text(body, encoding="utf-8")
    _write(root, "01_source/structure.json", structure)
    _write(root, "01_source/structure.confirmed.json", {"units": units, "meta": meta})
    _write(root, "01_source/extraction.json", {})
    _write(root, "01_source/metadata.json", meta)
    (root / "01_source/media").mkdir(parents=True, exist_ok=True)
    from .agent_tasks import confirmed_units
    units = confirmed_units(root)
    draft = question_draft.ensure(root)
    base = batch_fill.initialize(root)
    rows = base["payload"]["items"]
    audio = {}
    for unit, row in zip(units, rows):
        if str(unit.get("type", "")).startswith("听力"):
            name = f"seg-{unit['unit_id']}.mp3"
            (root / "01_source/media" / name).write_bytes(b"ID3" + name.encode())
            audio[unit["unit_id"]] = f"/source-media/stress-{seed}/{name}"
    # Labels constrained by the real validator.
    type_rows, know_rows, exp_rows = [], [], []
    for unit, row in zip(units, rows):
        main = unit.get("type") or "单项填空"
        parsed = question_content.audit_source_unit(unit)["questions"]
        counts = [len(part.get("options") or []) for part in parsed.values() if isinstance(part, dict)]
        option_count = max(counts) if counts else 0
        subs = valid_subtypes(main, option_count,
                              len(unit["numbers"]) if unit.get("unit_kind") == "group" else 0)
        type_rows.append({"unit_id": unit["unit_id"], "type": main, "subtypes": subs,
                          "words": rng.randint(3, 120), "contentComplete": True, "evidence": ["正文"]})
        know_rows.append({"unit_id": unit["unit_id"], "knowledge": valid_knowledge(main, subs, unit.get("unit_kind") or "standalone"),
                          "subquestions": [{"number": child["number"], "knowledge": []}
                                           for child in row.get("subquestions") or []],
                          "evidence": ["正文"]})
        exp_rows.append({"unit_id": unit["unit_id"], "explanation": "无", "articleSummary": "无",
                         "subquestions": [{"number": child["number"], "explanation": "无"}
                                          for child in row.get("subquestions") or []],
                         "evidence": ["正文未提供解析"]})
    _write(root, "03_types/types.json", {"items": type_rows})
    _write(root, "04_knowledge/knowledge.json", {"items": know_rows})
    _write(root, "04_knowledge/explanations.json", {"items": exp_rows})
    answers = {str(n): rng.choice(["A", "B", "C"]) for unit in units for n in unit["numbers"]}
    _write(root, "01_source_ans/answers.json", {"answers": answers,
                                                "difficulty": {n: rng.choice(["容易", "适中", "困难"])
                                                               for n in answers}})
    for stage in ("types", "knowledge", "explanations", "answers"):
        batch_fill.sync(root, stage)
    final = assemble_local.build(root)
    _write(root, "06_batch/batch.json", final)
    batch_fill.mark_finalized(root)
    # Audio attachments use the deployed relative form the upload mapper accepts.
    for item in final["items"]:
        url = audio.get(item.get("unit_id"))
        if url:
            item["attachments"] = [{"kind": "audio", "url": url, "name": url.rsplit("/", 1)[-1]}]
    # batch_fill keeps its own copy; install the normalized final view explicitly.
    _write(root, "06_batch/batch.json", final)
    subprocess.run([sys.executable, "scripts/05-meta/assign_meta.py", "--batch",
                    str(root / "06_batch/batch.json"), "--out",
                    str(root / "06_batch/batch_meta.base.json"), "--report"],
                   capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[1]))
    subprocess.run([sys.executable, "scripts/06-upload/mark_underline.py", "--batch",
                    str(root / "06_batch/batch_meta.base.json"), "--out",
                    str(root / "06_batch/batch_meta.json"), "--docx", "", "--apply"],
                   capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[1]))
    for relative in ("06_batch/batch_meta.base.json", "06_batch/batch_meta.json"):
        if not (root / relative).is_file():
            raise AssertionError("meta output missing " + relative)
    final_view = _read(root, "06_batch/batch_meta.json")
    if not isinstance(final_view, dict) or not final_view.get("items"):
        raise AssertionError("meta output empty")
    final_view.setdefault("batch_id", f"batch-{seed}")
    final_view.setdefault("name", meta["paper_title"])
    _write(root, "06_batch/batch_meta.json", final_view)
    # Listening stems legitimately carry only the printed number; install the
    # same source-proven representation the repair path would produce.
    prepared = legacy_format.prepare(root)
    if prepared.get("passed") and prepared.get("changes"):
        for relative, payload in prepared["files"].items():
            _write(root, relative, payload)
    # Seal only after every controlled fixture normalization has finished.
    # Otherwise later initialize() rejects the fixture itself as externally edited.
    batch_fill.mark_finalized(root)
    final_view = _read(root, "06_batch/batch_meta.json")
    audit = question_content.audit_batch_source(root, final_view)
    if not audit.get("passed"):
        raise AssertionError({"audit": audit.get("issues", [])[:4]})
    _write(root, "07_review/validation-report.json",
           {"passed": True, "source_audit": audit, "checks": [], "warnings": [], "skipped": []})
    _write(root, "07_review/structure-gate.json", {"passed": True})
    _write(root, "07_review/extraction-report.json", {"passed": True})
    fixture = Fixture(root=root, seed=seed, units=units, rows=rows, meta=meta)
    _create_review_evidence(fixture)
    report = format_gate.run(root)
    if not report["passed"]:
        raise AssertionError({"format": report["errors"][:6]})
    _install_manifest(fixture)
    fixture.build_ms = round((time.perf_counter() - started) * 1000, 1)
    return fixture


def _create_review_evidence(fixture: Fixture) -> None:
    root = fixture.root
    for _ in range(40):
        task = review_rounds.export_task(root)
        if task["action"] == "complete":
            break
        if task["action"] != "exported":
            raise AssertionError({"export": task})
        binding = _read(root, "workflow/review-bindings/" + task["task_id"] + ".json")
        state = _read(root, binding["state_path"])
        units = {unit["unit_id"]: unit for unit in fixture.units}
        batch = _read(root, "06_batch/batch_meta.json")
        rows = {row["unit_id"]: row for row in batch["items"]}
        checks = []
        for target in state["parts"][binding["part_index"]]["targets"]:
            uid = target["unit_id"] if isinstance(target, dict) else target
            unit, row = units[uid], rows[uid]
            target_number = target.get("number") if isinstance(target, dict) else None
            if unit.get("unit_kind") != "group":
                children = []
            elif task["round_id"] == "sample" and target_number is not None:
                children = [target_number]
            else:
                children = list(unit["numbers"])
            assessment = {"type": row.get("question_type") or row.get("type"),
                          "subtypes": (row.get("filterMetadata") or {}).get("subtypes") or [],
                          "knowledge": row.get("knowledge") or [],
                          "subquestions": [{"number": str(n), "knowledge": []} for n in children]}
            check = {"unit_id": uid, "verdict": "ok",
                     "rationale": "合成数据：逐项核对原文、答案与组级考点。",
                     "assessment": assessment,
                     "evidence": [{"path": "01_source/正文.txt", "locator": "当前单元",
                                   "quote": unit["stem"].splitlines()[0]}]}
            if task["round_id"] == "sample":
                if target_number is not None:
                    check["number"] = target_number
                assessment["source_comparison"] = "合成数据：对照本单元原文与答案。"
            checks.append(check)
        review_rounds.import_result(root, task["task_id"], {
            "status": "completed", "artifacts": {"review_round": {"checks": checks}},
            "notes": [], "findings": []})
    else:
        raise AssertionError("review evidence did not complete")
    if not review.evaluate_rounds(root).get("execution_completed"):
        raise AssertionError({"review": review.evaluate_rounds(root).get("errors", [])[:3]})


def _install_manifest(fixture: Fixture) -> None:
    root = fixture.root
    original = root / "00_original/source.txt"
    original.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / "01_source/正文.txt", original)
    state = mf.new_manifest(f"stress-{fixture.seed}", root,
                            mf.register_input(root, "source", original), {}, fixture.meta)
    for spec in contracts.STAGES:
        if spec.id in ("upload_gate", "upload", "publish_gate"):
            mf.touch_stage(state, spec.id, mf.SKIPPED, notes="stress: 远端边界不参与")
        elif spec.id == "format_gate":
            continue
        else:
            mf.touch_stage(state, spec.id, mf.COMPLETED,
                           input_hashes=mf.input_hashes(root, state, spec),
                           output_hashes=mf.output_hashes(root, spec))
            mf.record_semantics(root, state, spec)
    mf.touch_stage(state, "review_gate", mf.BLOCKED, execution_completed=True,
                   continue_unqualified=True, quality_status="failed", release_ready=False,
                   notes="stress: 保留合成质量边界")
    mf.record_semantics(root, state, contracts.STAGE_BY_ID["review_gate"])
    report = _read(root, "07_review/upload-format.json", {})
    mf.touch_stage(state, "format_gate", mf.COMPLETED,
                   input_hashes=mf.input_hashes(root, state, contracts.STAGE_BY_ID["format_gate"]),
                   output_hashes=mf.output_hashes(root, contracts.STAGE_BY_ID["format_gate"]))
    mf.record_semantics(root, state, contracts.STAGE_BY_ID["format_gate"])
    mf.save(root, state)


def _rebaseline(fixture: Fixture) -> dict:
    """Record the injected defect as the batch's own current state.

    This models a legacy batch that the old flow accepted while it is not
    upload-valid: the manifest hashes match the files on disk, and the format
    gate honestly failed. It is not a claim that the batch passed.
    """
    root = fixture.root
    report = format_gate.run(root)
    state = mf.load(root)
    for spec in contracts.STAGES:
        if spec.id in ("format_gate", "upload_gate", "upload", "publish_gate"):
            continue
        record = state["stages"].get(spec.id, {})
        if record.get("status") in (mf.COMPLETED, mf.APPROVED, mf.SKIPPED):
            try:
                record["input_hashes"] = mf.input_hashes(root, state, spec)
                record["output_hashes"] = mf.output_hashes(root, spec)
                mf.record_semantics(root, state, spec)
            except ValueError:
                # A deliberately broken structure cannot produce semantic
                # signatures; the stage stays stale, which is the honest state.
                record.pop("semantic_signatures", None)
    spec = contracts.STAGE_BY_ID["format_gate"]
    mf.touch_stage(state, "format_gate", mf.COMPLETED if report.get("passed") else mf.FAILED,
                   input_hashes=mf.input_hashes(root, state, spec),
                   output_hashes=mf.output_hashes(root, spec),
                   notes="" if report.get("passed") else "本地格式检查未通过：" +
                   ",".join(error.get("code", "") for error in report.get("errors") or [])[:300])
    mf.save(root, state)
    return report


# --------------------------------------------------------------------------- defects

DEFECTS = (
    "none", "empty_paragraph", "listening_stem", "stale_format_report",
    "nonrepairable_prose", "two_defects", "corrupt_report", "missing_final",
    "invalid_label", "missing_option", "duplicate_json_key", "blank_structure",
)


POST_DEFECTS = {"corrupt_report", "missing_final", "duplicate_json_key"}
BODY_DEFECTS = {"empty_paragraph", "listening_stem", "two_defects", "nonrepairable_prose",
                "invalid_label", "missing_option"}


def inject(fixture: Fixture, kind: str) -> dict:
    """Return expectations plus an actual mutation; unavailable shapes degrade."""
    root = fixture.root
    info = {"defect": kind, "repairable": None, "expect_format_pass": None,
            "legacy_unmanaged": False}
    batch = _read(root, "06_batch/batch_meta.json")
    groups = [row for row in batch["items"] if row.get("unit_kind") == "group"]
    listening = [row for row in batch["items"] if str(row.get("question_type", "")).startswith("听力")]
    if kind in {"empty_paragraph", "two_defects", "nonrepairable_prose"} and not groups:
        kind = "listening_stem" if listening else "stale_format_report"
    if kind == "listening_stem" and not listening:
        kind = "stale_format_report"
    info["defect"] = kind

    def mutate_batch(mutator, files=("06_batch/batch.json", "06_batch/batch_meta.base.json",
                                     "06_batch/batch_meta.json")):
        payload = copy.deepcopy(batch)
        mutator(payload)
        for relative in files:
            _write(root, relative, payload)

    if kind == "none":
        info.update(repairable=False, expect_format_pass=True)
    elif kind == "empty_paragraph":
        def add_empty(payload):
            target = next(row for row in payload["items"] if row.get("unit_kind") == "group")
            target.setdefault("materialBlocks", []).insert(0, {"type": "paragraph", "rows": []})
        mutate_batch(add_empty)
        info.update(repairable=True, expect_format_pass=True)
    elif kind == "listening_stem":
        if not listening:
            return inject(fixture, "stale_format_report")
        def blank_stem(payload):
            for row in payload["items"]:
                if str(row.get("question_type", "")).startswith("听力"):
                    row["stem"] = ""
        mutate_batch(blank_stem)
        info.update(repairable=True, expect_format_pass=True)
    elif kind == "stale_format_report":
        stale = _read(root, "07_review/upload-format.json")
        stale["final_sha256"] = "0" * 64
        _write(root, "07_review/upload-format.json", stale)
        info.update(repairable=False, expect_format_pass=True)
    elif kind == "nonrepairable_prose":
        def add_prose(payload):
            target = next(row for row in payload["items"] if row.get("unit_kind") == "group")
            target.setdefault("materialBlocks", []).insert(
                0, {"type": "paragraph", "text": "完全不在原文里的新增段落内容。"})
        mutate_batch(add_prose)
        info.update(repairable=False, expect_format_pass=False)
    elif kind == "two_defects":
        def both(payload):
            target = next(row for row in payload["items"] if row.get("unit_kind") == "group")
            target.setdefault("materialBlocks", []).insert(0, {"type": "paragraph", "rows": []})
            for row in payload["items"]:
                if str(row.get("question_type", "")).startswith("听力"):
                    row["stem"] = ""
        mutate_batch(both)
        info.update(repairable=True, expect_format_pass=True)
    elif kind == "corrupt_report":
        (root / "07_review/upload-format.json").write_bytes(b'{"passed": true, "passed": true}')
        # A forged report must be re-verified against real files, not trusted.
        info.update(repairable=False, expect_format_pass=True)
    elif kind == "missing_final":
        (root / "06_batch/batch_meta.json").unlink()
        # The deterministic meta/underline stages rebuild this file, so a real
        # pass afterwards is honest; only a forged report would be unacceptable.
        info.update(repairable=False, expect_format_pass=True)
    elif kind == "invalid_label":
        def bad_label(payload):
            payload["items"][0]["knowledge"] = ["完全不存在的知识点/子级"]
        mutate_batch(bad_label)
        info.update(repairable=False, expect_format_pass=False)
    elif kind == "missing_option":
        def drop_option(payload):
            for row in payload["items"]:
                if row.get("unit_kind") == "standalone" and len(row.get("options") or []) >= 3:
                    row["options"] = row["options"][:1]
                    break
        mutate_batch(drop_option)
        info.update(repairable=False, expect_format_pass=False)
    elif kind == "duplicate_json_key":
        path = root / "06_batch/batch_meta.json"
        path.write_text('{"items": [], "items": []}', encoding="utf-8")
        # The deterministic meta/underline stages rebuild this derived file.
        info.update(repairable=False, expect_format_pass=True)
    elif kind == "blank_structure":
        _write(root, "01_source/structure.confirmed.json", {"units": [], "meta": fixture.meta})
        # Rerunning the structure gate restores the confirmations deterministically.
        info.update(repairable=False, expect_format_pass=True)
    else:
        raise ValueError("unknown defect " + kind)
    if kind in BODY_DEFECTS and (root / batch_fill.STATE).is_file():
        # An externally edited batch is no longer owned by the incremental
        # writer. Real legacy batches arrive exactly like this: labelled files
        # with no fill state, which is the path the repair must handle.
        (root / batch_fill.STATE).unlink()
        info["legacy_unmanaged"] = True
    return info


# --------------------------------------------------------------------------- judge

def deterministic_judge(root: Path, stage_id: str, detail: str, rng: random.Random,
                        style: str = "honest") -> str:
    """Stand-in for the model: classify from the real evidence, not from expectations."""
    report = _read(root, "07_review/upload-format.json", {}) or {}
    codes = {error.get("code") for error in report.get("errors") or []}
    legacy_ok = False
    if stage_id in {"format_gate", "validate"} and codes:
        try:
            candidate = legacy_format.prepare(root)
            legacy_ok = bool(candidate.get("passed") and candidate.get("changes"))
        except (OSError, ValueError, TypeError):
            legacy_ok = False
    if style == "honest" and stage_id in {"format_gate", "validate"}:
        try:
            candidate = self_heal_actions._prepare_options(root)
            if candidate.get("passed") and candidate.get("changes"):
                return "repair_source_options"
        except (ValueError, OSError, KeyError):
            pass  # unavailable evidence is never a repair candidate
        try:
            if self_heal_actions.knowledge_repair_targets(root):
                return "rejudge_knowledge"
        except (ValueError, OSError, KeyError):
            pass
    if style == "retry_only":
        return "retry_stage"
    if style == "record_only":
        return "record_only"
    if legacy_ok:
        return "repair_upload_format"
    if "MATERIAL_BLOCKS_INVALID" in codes or "TABLE_MISSING" in codes:
        return "repair_source_tables"
    if style == "honest" and stage_id in AGENT_STAGES:
        return "retry_stage_with_fresh_context"
    if style == "honest" and not (root / "06_batch/batch_meta.json").is_file():
        return "record_only"
    return "retry_stage"


def _fake_invoke(decision: str, stage_id: str):
    def invoke(prompt, run_dir, config=None, **kwargs):
        return {"response": {"status": "completed", "artifacts": {"self_heal": {
            "action": decision, "stage": stage_id,
            "reason": f"压力测试替身判断：{decision}"}}, "notes": [], "findings": []}}
    return invoke


# --------------------------------------------------------------------------- run

@dataclass
class Outcome:
    seed: int
    defect: str
    style: str
    ticks: int = 0
    dispatch_ms: list = field(default_factory=list)
    total_ms: float = 0.0
    final_stage: str = ""
    repaired: bool = False
    verified: bool = False
    format_passed: bool = False
    local_complete: bool = False
    review_reissued: bool = False
    quality_preserved: bool = False
    data_preserved: bool = True
    fake_pass: bool = False
    structure_restored: bool = False
    knowledge_requeued: bool = False
    review_invalidated: bool = False
    error: str = ""

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "seed", "defect", "style", "ticks", "total_ms", "final_stage", "repaired",
            "verified", "format_passed", "local_complete", "review_reissued",
            "quality_preserved", "data_preserved", "fake_pass", "structure_restored",
            "knowledge_requeued", "review_invalidated", "actual_defect", "error")}


def run_scenario(root: Path, seed: int, defect: str, style: str = "honest",
                 max_ticks: int = 4) -> Outcome:
    """Build → inject → supervise with a stand-in judge → measure + check invariants."""
    from unittest.mock import patch
    root = Path(root)
    started_at = time.perf_counter()
    outcome = Outcome(seed=seed, defect=defect, style=style)
    try:
        fixture = build_fixture(root, seed)
        before = {
            "batch": _read(root, "06_batch/batch_meta.json"),
            "tasks": sorted(p.name for p in (root / "workflow/review-tasks").iterdir())
            if (root / "workflow/review-tasks").is_dir() else [],
            "rounds": _read(root, "07_review/review-rounds.json"),
        }
        post = defect in POST_DEFECTS
        info = {"defect": defect} if post else inject(fixture, defect)
        baseline = _rebaseline(fixture)
        if post:
            info.update(inject(fixture, defect))
        info["baseline_passed"] = bool(baseline.get("passed"))
        outcome.actual_defect = info["defect"]
        injected_batch = _read(root, "06_batch/batch_meta.json")
        if info["defect"] != defect:
            raise AssertionError("Requested fault was silently replaced")
        stage_id, detail = None, ""
        last_progress = None
        for _ in range(max_ticks):
            state = mf.load(root)
            if supervisor.local_complete(root, state):
                break
            stage_id, detail = self_heal._candidate(root, state)
            if stage_id and not self_heal._busy(root):
                decision = deterministic_judge(root, stage_id, detail, random.Random(seed),
                                               style=style)
                with patch("workflow.local_agent.load_config", return_value={}), \
                        patch("workflow.local_agent.invoke", _fake_invoke(decision, stage_id)):
                    healed = self_heal.recover(root)
                outcome.repaired = outcome.repaired or healed.get("status") == "repaired"
                outcome.verified = outcome.verified or bool(healed.get("verified"))
                if (healed.get("status") == "requeued" and
                        (root / "workflow/repair-feedback/knowledge.json").is_file()):
                    break  # a real semantic model is intentionally outside this simulation
                if healed.get("status") in ("cooldown", "busy", "waiting_external", "failed"):
                    break
            # The real scheduler continues after a real repair, and also surfaces
            # stale artefacts left by an external edit.
            started = time.perf_counter()
            with patch("workflow.self_heal.recover", return_value={"changed": False, "status": "idle"}), \
                    patch.object(auto, "_parallel_ready_agents",
                                 side_effect=AssertionError("no retag wave")):
                auto.advance(root, {}, stop_at="upload_gate")
            outcome.dispatch_ms.append(round((time.perf_counter() - started) * 1000, 1))
            outcome.ticks += 1
            stages = mf.load(root)["stages"]
            signature = json.dumps({name: record.get("status") for name, record in stages.items()},
                                   sort_keys=True)
            if signature == last_progress:
                break
            last_progress = signature
        state = mf.load(root)
        report = _read(root, "07_review/upload-format.json", {}) or {}
        outcome.final_stage = state["stages"].get("format_gate", {}).get("status", "?")
        outcome.format_passed = bool(report.get("passed")) and report.get("final_sha256") == \
            mf.sha256_file(root / "06_batch/batch_meta.json")
        outcome.local_complete = supervisor.local_complete(root, state)
        review_record = state["stages"].get("review", {})
        outcome.review_invalidated = (review_record.get("status") == mf.INVALIDATED
                                      or bool(review_record.get("rebuild_required")))
        gate = state["stages"].get("review_gate", {})
        # Quality may need re-evaluation after a structural change; what must
        # never happen is an unearned release or approval.
        outcome.quality_preserved = gate.get("release_ready") is not True \
            and gate.get("status") not in (mf.APPROVED,)
        after_tasks = sorted(p.name for p in (root / "workflow/review-tasks").iterdir()) \
            if (root / "workflow/review-tasks").is_dir() else []
        outcome.review_reissued = after_tasks != before["tasks"]
        after = _read(root, "06_batch/batch_meta.json")
        outcome.data_preserved = _data_preserved(before["batch"], after, injected=injected_batch)
        confirmed = _read(root, "01_source/structure.confirmed.json") or {}
        outcome.structure_restored = bool(confirmed.get("units"))
        feedback = _read(root, "workflow/repair-feedback/knowledge.json") or {}
        knowledge = state["stages"].get("knowledge", {})
        outcome.knowledge_requeued = bool(feedback.get("unit_ids")) and knowledge.get("status") in (
            mf.PENDING, mf.INVALIDATED, mf.RUNNING)
        # A defect that must not pass must never be reported as locally complete.
        if info["expect_format_pass"] is False:
            outcome.fake_pass = outcome.local_complete
        outcome.total_ms = round((time.perf_counter() - started_at) * 1000, 1)
        return outcome
    except Exception as exc:  # the harness reports, it does not hide
        outcome.error = f"{type(exc).__name__}: {exc}"[:600]
        return outcome


def _data_preserved(before, after, injected=None) -> bool:
    if not isinstance(after, dict):
        # Missing/corrupt final output is contained, not a successful repair.
        return after == injected
    if not isinstance(before, dict):
        return False
    old = {row.get("unit_id"): row for row in before.get("items") or [] if isinstance(row, dict)}
    new = {row.get("unit_id"): row for row in after.get("items") or [] if isinstance(row, dict)}
    if set(old) != set(new):
        return False
    damaged = {row.get("unit_id"): row for row in (injected or {}).get("items", [])
               if isinstance(row, dict)}
    for uid, row in old.items():
        fresh = new[uid]
        fault = damaged.get(uid, row)
        # Each checked field may remain exactly as injected (contained failure),
        # or return exactly to the known clean fixture. No blanket field skip.
        for key in ("answer", "explanation", "articleSummary", "knowledge", "subquestions",
                    "options", "stem", "material", "attachments", "question_type",
                    "id", "unit_kind", "number", "numbers"):
            if fresh.get(key) != row.get(key) and fresh.get(key) != fault.get(key):
                return False
        if (_blocks_text(fresh) != _blocks_text(row)
                and _blocks_text(fresh) != _blocks_text(fault)):
            return False
    return True


def _blocks_text(row) -> str:
    """Comparable material-block text; removal of an empty block is a repair."""
    parts = []
    for block in row.get("materialBlocks") or row.get("material_blocks") or []:
        if block in ({"type": "paragraph", "rows": []}, {"type": "paragraph", "text": ""}):
            continue
        # Preserve image ownership, row/cell structure and unknown extensions,
        # not just their displayed text.
        parts.append(json.dumps(block, sort_keys=True, ensure_ascii=False))
    return "|".join(parts)


def _normalize(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()
