#!/usr/bin/env python3
"""Randomized module-level invariants for the upload workflow.

No network, no user batches: each property is checked against generated data
inside an isolated temp root.
"""
from __future__ import annotations

import copy
import json
import os
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workflow import (batch_fill, format_gate, legacy_format, manifest as mf,
                      stage_semantics, supervisor, upload_payload)  # noqa: E402

FAILURES: list[dict] = []


def check(name: str, condition: bool, detail=None) -> None:
    if not condition:
        FAILURES.append({"check": name, "detail": str(detail)[:400]})
        print("  !", name, str(detail)[:200], flush=True)


def temp_root() -> Path:
    base = "/private/tmp" if Path("/private/tmp").is_dir() else None
    return Path(tempfile.mkdtemp(prefix="stress-mod-", dir=base)).resolve()


def random_item(rng: random.Random, index: int) -> dict:
    kind = rng.choice(["standalone", "group"])
    item = {"unit_id": f"u-{index}", "unit_kind": kind,
            "question_type": rng.choice(["单项填空", "阅读理解", "听力关键词语选择", ""]),
            "stem": rng.choice(["She ______ a book.", "", "   ", "Read and answer."]),
            "options": [{"key": label, "text": rng.choice(["A", "B", "", None])}
                        for label in "ABCD"[:rng.randint(0, 4)]],
            "filterMetadata": {"subtypes": rng.choice([[], ["四个选项"], ["选择3题"]]),
                               "difficulty": rng.choice(["", "容易", "weird"])},
            "difficulty": rng.choice(["", "容易", "困难", None]),
            "knowledge": rng.choice([[], ["主题"], ["不存在/叶子"]]),
            "answer": rng.choice(["", "A", "AB", None]),
            "explanation": rng.choice(["", "无", None]),
            "attachments": rng.choice([[], [{"kind": "audio", "url": "x"}]]),
            "material": rng.choice(["", "Passage text."]), "subquestions": []}
    if kind == "group":
        item["subquestions"] = [{"number": index * 10 + offset,
                                 "stem": rng.choice(["", "Child?"])}
                                for offset in range(rng.randint(0, 3))]
    if rng.random() < 0.2:
        item["paper_title"] = "should not survive"
    if rng.random() < 0.2:
        item["contentComplete"] = True
    return item


def fuzz_format_gate(rng: random.Random, root: Path, rounds: int = 40) -> None:
    for index in range(rounds):
        payload = {"items": [random_item(rng, index) for _ in range(rng.randint(0, 4))]}
        path = root / f"batch-{index}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        # Mirror what the gate does: the wire builder must never raise on junk,
        # and must never report a clean pass while emitting errors.
        try:
            data = json.loads(path.read_text())
            result = upload_payload.build_payload(data, media_map=None)
        except (ValueError, TypeError, KeyError) as exc:
            check("format_gate raises only on unreadable payload", "items" not in payload or not payload["items"],
                  f"{type(exc).__name__}: {exc}")
            continue
        errors = result.get("errors") or []
        passed = bool(result.get("passed"))
        check("format_gate pass implies no errors", not (passed and errors), result)
        check("format_gate never passes empty batch", not (passed and not payload["items"]), result)


def fuzz_batch_fill(rng: random.Random, root: Path, rounds: int = 30) -> None:
    for index in range(rounds):
        batch = root / f"fill-{index}"
        batch.mkdir(parents=True, exist_ok=True)
        (batch / "01_source").mkdir(exist_ok=True)
        count = rng.randint(2, 5)
        blocks = [f"{n}.She ______ a book {n}.\nA.read B.reads C.reading" for n in range(1, count + 1)]
        (batch / "01_source/正文.txt").write_text("\n".join(blocks) + "\n", encoding="utf-8")
        units = [{"unit_id": f"q-{n}", "unit_kind": "standalone", "numbers": [n],
                  "type": "单项填空", "stem": blocks[n - 1],
                  "source_span": {"start_line": 2 * n - 1, "end_line": 2 * n, "basis": "cleaned_source"}}
                 for n in range(1, count + 1)]
        (batch / "01_source/structure.json").write_text(json.dumps({"sections": [], "groups": []}),
                                                       encoding="utf-8")
        (batch / "01_source/structure.confirmed.json").write_text(
            json.dumps({"units": units, "meta": {"source": "x", "year": "2020", "region": "安徽省"}},
                       ensure_ascii=False), encoding="utf-8")
        (batch / "01_source/extraction.json").write_text("{}", encoding="utf-8")
        (batch / "01_source/metadata.json").write_text("{}", encoding="utf-8")
        try:
            from workflow import question_draft
            question_draft.ensure(batch)
            first = batch_fill.initialize(batch)
            second = batch_fill.initialize(batch)
        except (ValueError, KeyError, TypeError) as exc:
            check("batch_fill initialize handles generated units", False, f"{type(exc).__name__}: {exc}")
            continue
        check("batch_fill initialize is idempotent",
              json.dumps(first["payload"], sort_keys=True, ensure_ascii=False) ==
              json.dumps(second["payload"], sort_keys=True, ensure_ascii=False)
              and second["status"] == "unchanged" and not second["changed_units"])
        rows = first["payload"]["items"]
        for row in rows:
            for node in (row, *row.get("subquestions", [])):
                check("batch_fill drops upload-only fields",
                      "paper_title" not in node and "contentComplete" not in node, node)
                check("batch_fill keeps region in filters only",
                      "region" not in node, node)
                if node is not row:
                    check("batch_fill does not force empty child type",
                          node.get("question_type") != "", node)


def fuzz_legacy_format(rng: random.Random, root: Path, rounds: int = 30) -> None:
    blocks = [
        {"type": "paragraph", "rows": []},
        {"type": "paragraph", "text": ""},
        {"type": "paragraph", "text": "新增散文"},
        {"type": "paragraph", "text": "", "attachments": []},
        {"type": "text", "text": ""},
        {"type": "image", "src": "x.png"},
        {"type": "paragraph", "rows": [{"cells": []}]},
    ]
    for index in range(rounds):
        batch = root / f"legacy-{index}"
        (batch / "06_batch").mkdir(parents=True, exist_ok=True)
        (batch / "01_source").mkdir(parents=True, exist_ok=True)
        (batch / "01_source/正文.txt").write_text("A\nPassage.\n1.Question?\nA.x B.y\n", encoding="utf-8")
        payload = {"items": [{"unit_id": "g-1", "unit_kind": "group", "question_type": "阅读理解",
                              "stem": "Passage.", "material": "Passage.\n1.Question?",
                              "materialBlocks": [copy.deepcopy(rng.choice(blocks))
                                                 for _ in range(rng.randint(0, 2))],
                              "subquestions": [{"number": 1, "stem": "Question?",
                                                "options": [{"key": "A", "text": "x"},
                                                            {"key": "B", "text": "y"}]}]}]}
        for relative in legacy_format.BATCH_PATHS:
            path = batch / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        before = {relative: (batch / relative).read_bytes() for relative in legacy_format.BATCH_PATHS}
        result = legacy_format.prepare(batch)
        after = {relative: (batch / relative).read_bytes() for relative in legacy_format.BATCH_PATHS}
        check("legacy prepare is read-only", before == after, index)
        check("legacy prepare never claims changes on refusal",
              not (result.get("changes") and not result.get("passed")), result)
        check("legacy prepare is deterministic",
              json.dumps(result, sort_keys=True, ensure_ascii=False)[:2000] ==
              json.dumps(legacy_format.prepare(batch), sort_keys=True, ensure_ascii=False)[:2000], index)


def fuzz_stage_semantics(rng: random.Random, rounds: int = 20) -> None:
    for index in range(rounds):
        payload = {"items": [random_item(rng, index)], "meta": {"a": rng.randint(0, 9)},
                   "updated_at": f"{index}:00"}
        original = stage_semantics.digest(stage_semantics.without_clocks(payload))
        reformatted = stage_semantics.digest(stage_semantics.without_clocks(
            json.loads(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))))
        check("semantic value ignores JSON formatting", original == reformatted, index)
        changed = copy.deepcopy(payload)
        changed["items"][0]["stem"] = "changed content"
        check("semantic value notices content change",
              stage_semantics.digest(stage_semantics.without_clocks(changed)) != original, index)


def fuzz_supervisor(rng: random.Random, root: Path, rounds: int = 40) -> None:
    for index in range(rounds):
        batch = root / f"sup-{index}"
        batch.mkdir(parents=True, exist_ok=True)
        state = {"version": 1, "batch_id": f"b{index}", "stages": {}}
        for spec in supervisor.LOCAL_STAGES:
            state["stages"][spec] = {"status": rng.choice(
                ["completed", "completed", "failed", "pending", "unknown", "skipped"])}
        state["stages"]["format_gate"] = {"status": rng.choice(["completed", "failed"])}
        checks = [supervisor.local_complete(batch, state) for _ in range(3)]
        check("supervisor.local_complete is deterministic", len(set(checks)) == 1, index)
        statuses = [state["stages"][spec]["status"] for spec in supervisor.LOCAL_STAGES]
        degraded = any(value in {"failed", "pending", "unknown"} for value in statuses)
        check("supervisor rejects unfinished local stages", not (checks[0] and degraded), statuses)


def main() -> int:
    root = temp_root()
    os.environ["WF_STRUCTURE_AI"] = "0"
    rng = random.Random(20261107)
    started = time.perf_counter()
    print("fuzz: format/upload payload")
    fuzz_format_gate(rng, root)
    print("fuzz: batch fill")
    fuzz_batch_fill(rng, root)
    print("fuzz: legacy format")
    fuzz_legacy_format(rng, root)
    print("fuzz: stage semantics")
    fuzz_stage_semantics(rng)
    print("fuzz: supervisor")
    fuzz_supervisor(rng, root)
    elapsed = round(time.perf_counter() - started, 2)
    summary = {"failures": FAILURES, "failure_count": len(FAILURES), "seconds": elapsed,
               "cases": 160, "seed": 20261107,
               "by_module": {"upload_payload": 40, "batch_fill": 30, "legacy_format": 30,
                             "stage_semantics": 20, "supervisor": 40},
               "scope": "module invariants; no semantic models, OCR/ASR or concurrent load"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    Path("/tmp/stress-modules.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
