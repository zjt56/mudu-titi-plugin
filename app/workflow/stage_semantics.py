"""Content identities for reuse; raw task bindings and upload hashes stay exact.

Scheduling dependencies say when work may run. They do not mean that every field
of every producer report is a semantic input to the consumer.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

VERSION = 1
# These are execution envelopes, never question content. Do not strip arbitrary
# status/evidence/notes fields: they can contain actual unresolved content issues.
CLOCK_KEYS = frozenset(("created_at", "updated_at", "generated_at", "finished_at", "started_at"))
REPORTS = frozenset(("07_review/structure-gate.json", "07_review/extraction-report.json"))
CONTENT_STAGES = frozenset(("answers", "types", "explanations", "knowledge", "assemble"))
FILL_STAGES = frozenset(("answers", "audio", "types", "explanations", "knowledge"))
DIRECT_INPUTS = {
    "preassemble": ("01_source/正文.txt", "01_source/structure.confirmed.json", "01_source/structure.json", "01_source/extraction.json", "01_source/pdf-ocr.json"),
    "format_gate": ("06_batch/batch_meta.json",),
    "audio": ("01_source/正文.txt", "01_source/structure.confirmed.json", "03_types/types.json", "02_audio/segment-evidence.json"),
    "answers": ("01_source/正文.txt", "01_source/structure.confirmed.json", "01_source/附录.txt", "01_source_ans/正文.txt"),
    "types": ("01_source/正文.txt", "01_source/structure.confirmed.json"),
    "explanations": ("01_source/正文.txt", "01_source/structure.confirmed.json", "01_source/structure.json", "01_source/附录.txt", "01_source_ans/正文.txt", "03_types/types.json", "01_source_ans/answers.json"),
    "knowledge": ("01_source/正文.txt", "01_source/structure.confirmed.json", "01_source/structure.json", "01_source/metadata.json", "01_source/附录.txt", "01_source_ans/正文.txt", "03_types/types.json"),
    "assemble": ("01_source/正文.txt", "01_source/structure.confirmed.json", "01_source/metadata.json", "01_source/extraction.json", "01_source_ans/answers.json"),
    "validate": ("01_source/正文.txt", "01_source/structure.json", "01_source/structure.confirmed.json", "01_source/extraction.json", "01_source/附录.txt", "01_source_ans/正文.txt", "01_source_ans/附录.txt", "01_source_ans/answers.json", "02_audio/SKIP_LISTENING"),
}


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def without_clocks(value):
    if isinstance(value, dict):
        return {k: without_clocks(v) for k, v in value.items() if k not in CLOCK_KEYS}
    if isinstance(value, list):
        return [without_clocks(v) for v in value]
    return value


def file_value(path):
    path = Path(path)
    if not path.is_file():
        return {"missing": True}
    raw = path.read_bytes()
    if path.suffix.lower() == ".json":
        try:
            # JSON whitespace/key order is not a change to the data.
            return without_clocks(json.loads(raw.decode("utf-8-sig")))
        except (UnicodeError, ValueError):
            pass
    return {"bytes_sha256": hashlib.sha256(raw).hexdigest()}


def output_signatures(batch, stage):
    # Preserve all question fields; only JSON representation and clocks are
    # irrelevant. In particular an edited stem can never migrate as equivalent.
    return {rel: digest(file_value(Path(batch) / rel)) for rel in stage.produces}


def _projection(stage, rel, value):
    if not isinstance(value, dict):
        return value
    if rel == "06_batch/batch.base.json" and stage in (CONTENT_STAGES | {"audio"}):
        if stage == "assemble":
            return value
        from .review import semantic_review_value
        return semantic_review_value(value)
    if rel == "01_source/structure.confirmed.json" and stage in CONTENT_STAGES:
        # Source line/hash/diagnostic envelope changes are not a new question.
        units = value.get("units")
        if not isinstance(units, list):
            return value
        ignored = {"source_span", "source_sha256", "source_hash", "source_hashes", "evidence", "warnings", "notes"}
        projected = {"units": [{k: v for k, v in row.items() if k not in ignored}
                               if isinstance(row, dict) else row for row in units]}
        if stage == "assemble":
            projected["meta"] = value.get("meta", {})
        return projected
    if rel == "03_types/types.json" and stage in ("knowledge", "explanations"):
        rows = value.get("items")
        if isinstance(rows, list):
            return {"items": [{k: row[k] for k in ("unit_id", "type", "subtypes", "contentComplete") if k in row}
                              if isinstance(row, dict) else row for row in rows]}
    if rel == "01_source_ans/answers.json" and stage == "explanations":
        return {k: v for k, v in value.items() if k not in ("difficulty", "difficultyEvidence", "difficulty_evidence")}
    return value


def _source_identity(draft):
    """Use freshly proven Word fragments/media, never rendered skeleton fields."""
    if not isinstance(draft, dict) or not draft.get("items"):
        return None
    bindings = draft.get("source_bindings")
    if not isinstance(bindings, dict):
        return None
    result = []
    seen = set()
    def valid_hash(value):
        return (isinstance(value, str) and len(value) == 64
                and all(char in "0123456789abcdef" for char in value)
                and value != hashlib.sha256(b"").hexdigest())
    for row in draft["items"]:
        if not isinstance(row, dict):
            return None
        uid = row.get("unit_id")
        if not isinstance(uid, str) or not uid or uid in seen:
            return None
        seen.add(uid)
        binding = bindings.get(uid)
        if (not isinstance(binding, dict) or binding.get("source") != "01_source/正文.txt"
                or type(binding.get("start_line")) is not int
                or type(binding.get("end_line")) is not int
                or not 1 <= binding["start_line"] <= binding["end_line"]
                or not valid_hash(binding.get("source_text_sha256"))):
            return None
        media = binding.get("media_hashes", {})
        if not isinstance(media, dict) or any(not valid_hash(value) for value in media.values()):
            return None
        result.append({"unit_id": uid, "unit_kind": row.get("unit_kind"), "numbers": row.get("numbers"),
                       "source_text_sha256": binding["source_text_sha256"], "media": media})
    if seen != set(bindings):
        return None
    return {"source_units": result}


def input_signatures(batch, manifest, stage, raw_hashes):
    batch = Path(batch)
    # Review owns its field scope and per-unit evidence rules. Its raw plan still
    # protects exported tasks; this signature only determines whether to schedule.
    if stage.id == "review":
        from . import review
        try:
            return review.stage_semantic_inputs(batch)
        except (OSError, ValueError, TypeError, KeyError):
            # Partial/legacy batches have no proven unit scopes yet. Preserve
            # exact input freshness rather than silently discard evidence.
            return dict(raw_hashes, **{"review-scope": "unavailable"})
    draft = None
    source_identity = None
    if stage.id in (CONTENT_STAGES | FILL_STAGES):
        from . import question_draft
        try:
            draft = question_draft.build(batch)
            if stage.id in FILL_STAGES:
                source_identity = _source_identity(draft)
        except (OSError, ValueError, TypeError, KeyError):
            pass
    result = {}
    if stage.id in FILL_STAGES:
        source = (manifest.get("inputs", {}).get("source") or {}).get("path")
        if source:
            original = Path(source).expanduser()
            original = original if original.is_absolute() else batch / original
            result["content:original_source"] = digest(file_value(original))
        else:
            result["content:original_source"] = digest({"missing": True})
    for key, raw in raw_hashes.items():
        if key.startswith("input:"):
            # Original binary files keep exact byte identities.
            result[key] = raw
            continue
        rel = key.split(":", 1)[-1]
        if stage.id in (CONTENT_STAGES | {"audio", "preassemble"}) and rel in REPORTS:
            continue
        if stage.id == "review_gate" and key.startswith("review:"):
            continue
        if key.startswith("rule:"):
            root = Path(__file__).resolve().parents[1]
            result[key] = digest(file_value(root / rel))
        elif key == "material:roles":
            result[key] = digest(file_value(batch / "01_source/material-roles.json"))
        elif key.startswith("material:"):
            result[key] = digest(file_value(batch / "01_source/materials" / (rel + ".txt")))
        elif rel == "06_batch/batch.base.json" and source_identity is not None:
            # Producer integrity still checks the base output exactly. Fill
            # scheduling consumes its proven source, not parser presentation.
            result[key] = digest(source_identity)
        else:
            result[key] = digest(_projection(stage.id, rel, file_value(batch / rel)))
    if stage.id == "audio" and source_identity is not None:
        result["content:source_units"] = digest(source_identity)
    for rel in DIRECT_INPUTS.get(stage.id, ()):
        result["content:" + rel] = digest(_projection(stage.id, rel, file_value(batch / rel)))
    if stage.id == "audio":
        from . import agent_tasks
        result["rule:task"] = digest(agent_tasks.RULES[stage.id])
        for rel in ("02_audio/segment-evidence.json", "02_audio/audio_map.json"):
            value = file_value(batch / rel)
            for row in value.get("items", []) if isinstance(value, dict) else []:
                if not isinstance(row, dict) or not isinstance(row.get("file"), str):
                    continue
                name = row["file"]
                try:
                    path = agent_tasks._safe_path(batch, name)
                    result["audio-cut:" + name] = digest(file_value(path))
                except (OSError, ValueError):
                    result["audio-cut:" + name] = "invalid-path"
    if stage.id in CONTENT_STAGES:
        from . import agent_tasks
        try:
            # The draft above was rebuilt from current source, never loaded
            # from an editable saved candidate.
            if draft is None:
                raise ValueError("Source draft unavailable")
            items = draft["items"]
            if stage.id != "assemble":
                metadata_keys = {"source", "source_type", "source_name", "sourceName", "sourceType",
                                 "year", "region", "categories", "category", "location", "difficulty",
                                 "difficultyValue", "authorization", "version"}
                def content_only(row):
                    if isinstance(row, dict):
                        return {k: content_only(v) for k, v in row.items() if k not in metadata_keys}
                    if isinstance(row, list):
                        return [content_only(v) for v in row]
                    return row
                items = content_only(items)
            result["content:question_objects"] = digest(source_identity if source_identity is not None else {
                "items": items, "media": {
                    uid: binding.get("media_hashes", {}) for uid, binding in draft["source_bindings"].items()}})
        except (OSError, ValueError, TypeError, KeyError):
            result["content:question_objects"] = "unavailable"
        result["rule:task"] = digest(agent_tasks.RULES[stage.id])
        root = Path(__file__).resolve().parents[1]
        rules = ["skills-reference/README.md", "references/filter-framework.json"]
        if stage.id in ("types", "assemble"):
            rules += ["skills-reference/type-taxonomy.md", "skills-reference/constraints.md", "skills-reference/lessons-learned.md"]
        if stage.id in ("explanations", "knowledge", "assemble"):
            rules += ["docs/content-labeling.md"]
        if stage.id in ("knowledge", "assemble"):
            rules += ["skills-reference/knowledge-catalog.md", "skills-reference/knowledge-matching-rules.md",
                      "skills-reference/type-knowledge-map.md", "docs/knowledge-tagging.md",
                      "references/knowledge-catalog.json", "references/type-knowledge-map.json"]
        for rel in rules:
            result["rule:" + rel] = digest(file_value(root / rel))
    if stage.id == "preassemble":
        root = Path(__file__).resolve().parent
        for name in ("batch_fill.py", "question_draft.py", "question_content.py", "wire_format.py", "preassembly.py"):
            result["preassemble-rule:" + name] = digest(file_value(root / name))
        from . import agent_tasks
        result["rule:task"] = digest(agent_tasks.RULES["types"])
        for rel in ("references/filter-framework.json", "references/type-subtypes.md",
                    "skills-reference/type-taxonomy.md", "prompts/roles/types.md"):
            result["rule:" + rel] = digest(file_value(root.parent / rel))
    if stage.id == "format_gate":
        from . import format_gate
        for path in format_gate.RULE_FILES:
            result["format-rule:" + str(path)] = digest(file_value(path))
        metadata = manifest.get("meta") or {}
        result["format:batch_identity"] = digest({"workflow_id": manifest.get("workflow_id"),
            "meta": {key: metadata.get(key) for key in ("paper_title", "name", "title")}})
    if stage.id == "validate":
        from . import checks, runner
        # Include the same source/answer evidence used by the source-audit guard.
        result.update({"validation:" + key: value for key, value in runner._validate_inputs(batch, manifest).items()})
        for key in ("source", "answers", "docx"):
            source = manifest.get("inputs", {}).get(key) or {}
            path = source.get("path")
            result["validation-original:" + key] = digest(file_value(path)) if path else digest({"missing": True})
        root = Path(__file__).resolve().parents[1]
        scripts = [check.script for check in checks.CHECKS]
        scripts += ["workflow/checks.py", "workflow/question_content.py", "workflow/source_structure.py",
                    "workflow/content_rules.py", "workflow/label_audit.py", "scripts/_common.py",
                    "references/knowledge-catalog.json", "references/filter-framework.json"]
        for rel in scripts:
            result["validation-rule:" + rel] = digest(file_value(root / rel))
        materials = batch / "01_source/materials"
        if materials.is_dir():
            for path in sorted(materials.glob("attachment_*.txt")):
                result["validation-material:" + path.name] = digest(file_value(path))
    if stage.id == "knowledge":
        from workbench import textbook
        metadata = file_value(batch / "01_source/metadata.json")
        submitted = manifest.get("submitted_meta") or manifest.get("meta") or {}
        source_type = submitted.get("source_type") if isinstance(submitted, dict) else None
        if not source_type and isinstance(metadata, dict):
            source_type = metadata.get("source_type")
        matching = textbook.needs_matching(source_type)
        result["metadata:textbook_matching"] = digest(bool(matching))
        if matching:
            result["rule:textbook_catalog"] = digest(file_value(textbook.CATALOG_PATH))
    if stage.id == "answers":
        answer = manifest.get("inputs", {}).get("answers") or {}
        path = answer.get("path")
        result["content:original_answers"] = digest(file_value(path)) if path else digest({"missing": True})
    if stage.id == "assemble":
        result["metadata:batch"] = digest(manifest.get("meta", {}))
    return result
