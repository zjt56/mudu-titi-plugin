"""Conservative, source-proven input projection for reusable labeling parts.

This is a cache/prompt scope, never an importer binding. The caller retains all
original task hashes and validates the merged response against the current batch.
Unknown evidence stays global; source spans must be unique and non-overlapping.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from . import agent_tasks

STAGES = ("types", "explanations", "knowledge")
VERSION = "label-unit-context/v3"
DRAFT = "06_batch/question-draft.json"
_SOURCE = "01_source/正文.txt"
_CONFIRMED = "01_source/structure.confirmed.json"
_STRUCTURE = "01_source/structure.json"
_GATE = "07_review/structure-gate.json"
_GATE_DIGESTS = {"input_hashes", "source_sha256", "structure_sha256", "confirmed_sha256", "original_sha256"}
_GLOBAL_FILES = ("01_source/extraction.json", "01_source/附录.txt", "01_source_ans/正文.txt",
                 "01_source/pdf-ocr.json", "01_source/material-roles.json")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _read(root, relative):
    path = agent_tasks._safe_path(root, relative)
    return path.read_bytes() if path.is_file() else None


def _structure(value, ids, numbers, known_ids, known_numbers):
    """Reduce only explicitly indexed rows; preserve unknown/global evidence."""
    if isinstance(value, list):
        result = []
        for row in value:
            if isinstance(row, dict):
                uid = row.get("unit_id")
                nums = row.get("numbers")
                if isinstance(uid, str) and uid in known_ids and uid not in ids:
                    continue
                if isinstance(nums, list) and nums and all(str(n).isdigit() for n in nums):
                    indexed = {str(n) for n in nums}
                    if indexed <= known_numbers and not numbers.intersection(indexed):
                        continue
            result.append(_structure(row, ids, numbers, known_ids, known_numbers))
        return result
    if not isinstance(value, dict):
        return value
    result = {}
    for key, child in value.items():
        if key == "questions" and isinstance(child, list) and all(str(n).isdigit() for n in child):
            result[key] = [n for n in child if str(n) in numbers or str(n) not in known_numbers]
        else:
            result[key] = _structure(child, ids, numbers, known_ids, known_numbers)
    return result


def project_indexed(context, selected):
    """Scope explicit unit/number indexes even when raw source ownership is unproved.

    Raw documents are deliberately untouched here. A failed source-span proof must
    retain them in full, but must not restore unrelated preassembled question rows.
    """
    value = copy.deepcopy(context)
    ids = {u["unit_id"] for u in selected}
    numbers = {str(n) for u in selected for n in u["numbers"]}
    units = value.get("confirmed_units_with_stable_ids", [])
    known_ids = {u["unit_id"] for u in units}
    known_numbers = {str(n) for u in units for n in u["numbers"]}
    value["confirmed_units_with_stable_ids"] = copy.deepcopy(selected)
    for path in (_CONFIRMED, _STRUCTURE):
        if path in value:
            value[path] = _structure(value[path], ids, numbers, known_ids, known_numbers)
    draft = value.get(DRAFT)
    if isinstance(draft, dict):
        draft.pop("input_hashes", None)
        draft.pop("content_sha256", None)
        draft["items"] = [r for r in draft.get("items", [])
                          if isinstance(r, dict) and r.get("unit_id") in ids]
        if isinstance(draft.get("source_bindings"), dict):
            draft["source_bindings"] = {k: v for k, v in draft["source_bindings"].items() if k in ids}
        if isinstance(draft.get("issues"), list):
            draft["issues"] = [r for r in draft["issues"] if not isinstance(r, dict)
                               or not r.get("unit_id") or r["unit_id"] in ids]
    # Exact selected coverage is already stated in the partition instruction.
    value.pop("incremental_gaps", None)
    return value


class Projection:
    def __init__(self, context, spans, guards, freshness_guards):
        self.context, self.spans, self.guards = context, spans, guards
        self.freshness_guards = freshness_guards

    def project(self, selected):
        ids = {u["unit_id"] for u in selected}
        value = project_indexed(self.context, selected)
        lines = value[_SOURCE].splitlines(keepends=True)
        # Preserve every unassigned line (headers, instructions, appendices).
        # Omitted ranges retain their real line locations; evidence never rebases.
        chunks, cursor = [], 0
        for uid, start, end in self.spans:
            chunks.extend(lines[cursor:start])
            if uid in ids:
                chunks.extend(lines[start:end])
            else:
                chunks.append(f"[OTHER UNIT OMITTED: {uid}; source lines {start + 1}-{end}]\n")
            cursor = end
        chunks.extend(lines[cursor:])
        value[_SOURCE] = "".join(chunks)
        gate = value.get(_GATE)
        if isinstance(gate, dict):
            scoped_gate = {k: v for k, v in gate.items() if k not in _GATE_DIGESTS}
            bindings = gate.get("input_hashes")
            if isinstance(bindings, dict):
                unknown = {k: v for k, v in bindings.items() if k not in (_SOURCE, _CONFIRMED, _STRUCTURE)}
                if unknown:
                    scoped_gate["input_hashes"] = unknown
            if not self.guards.get("original_inputs", {}).get("source") and "original_sha256" in gate:
                scoped_gate["original_sha256"] = gate["original_sha256"]
            value[_GATE] = scoped_gate
        value["partition_source_scope"] = {
            "version": VERSION, "source_line_numbers": "original, not rebased",
            "selected_spans": [{"unit_id": uid, "path": _SOURCE,
                                "start_line": start + 1, "end_line": end}
                               for uid, start, end in self.spans if uid in ids],
            "notice": "仅省略已唯一定位的其它题组；未归属原文、附件和警告保留。分片不代表整卷核验。"}
        return value


def prepare(batch, stage, context, hashes):
    """Return a proved projection, or None to retain full-batch invalidation.

    The raw original is a global guard unless it is the exact UTF-8 source TXT.
    DOCX/PDF, external answers, extraction metadata and every source media byte
    conservatively invalidate all labeling parts when changed.
    """
    if stage not in STAGES:
        return None
    root = Path(batch).resolve()
    try:
        source = context.get(_SOURCE)
        units = context.get("confirmed_units_with_stable_ids")
        if not isinstance(source, str) or not isinstance(units, list) or not units:
            return None
        raw = _read(root, _SOURCE)
        if raw is None or raw.decode("utf-8-sig") != source:
            return None
        # Sanitized text cannot establish a raw source span.
        lines = source.splitlines()
        spans = []
        for unit in units:
            text = unit.get("stem")
            if not isinstance(text, str) or not text.strip():
                return None
            wanted = text.strip().splitlines()
            positions = [i for i in range(len(lines) - len(wanted) + 1)
                         if "\n".join(lines[i:i + len(wanted)]).strip() == text.strip()]
            if len(positions) != 1:
                return None
            start = positions[0]
            spans.append((unit["unit_id"], start, start + len(wanted)))
        spans.sort(key=lambda row: row[1])
        if any(a[2] > b[1] for a, b in zip(spans, spans[1:])):
            return None
        data = copy.deepcopy(context)
        # Non-prompt structure still influences import/content validation. Read it
        # into the actual part input before replacing its whole-file cache hash.
        structure_raw = _read(root, _STRUCTURE)
        if structure_raw is not None:
            if _STRUCTURE in hashes and _sha(structure_raw) != hashes[_STRUCTURE]:
                return None
            data[_STRUCTURE] = agent_tasks._sanitized(json.loads(structure_raw.decode("utf-8-sig")))
        guards = {k: v for k, v in hashes.items() if k not in data
                  and k not in agent_tasks.REPAIR_FEEDBACK_INPUTS
                  and k not in ("workflow/manifest.json", "manifest.json")}
        # Feedback is bound by the selected prompt, never by a batch-wide cache
        # guard. Its complete raw hash/absence remains in the task input_hashes.
        freshness_guards, extra_hashes = {}, {}
        for relative in _GLOBAL_FILES:
            content = _read(root, relative)
            digest = _sha(content) if content is not None else "missing"
            if relative in hashes and hashes[relative] != digest:
                return None
            freshness_guards[relative] = digest
            if content is not None:
                extra_hashes[relative] = digest
            if relative == "01_source/extraction.json" and content is not None:
                # Text/layout duplicates must not drag other source-proven units
                # into cache identity. Keep actual evidence (tables/media/warnings
                # and every unknown field) using the existing prompt compaction.
                # The complete raw bytes remain in freshness_guards/input_hashes.
                from .unit_tasks import compact_extraction
                extraction = json.loads(content.decode("utf-8-sig"))
                compact = compact_extraction(extraction)
                guards[relative] = {"compact_sha256": _sha(json.dumps(
                    compact, ensure_ascii=False, sort_keys=True, allow_nan=False,
                    separators=(",", ":")).encode("utf-8"))}
                if relative in data:
                    data[relative] = agent_tasks._sanitized(compact)
            else:
                guards[relative] = digest
        manifest_relative = "workflow/manifest.json"
        manifest_raw = _read(root, manifest_relative)
        if manifest_raw is None:
            manifest_relative = "manifest.json"
            manifest_raw = _read(root, manifest_relative)
        if manifest_raw is not None:
            if manifest_relative in hashes and _sha(manifest_raw) != hashes[manifest_relative]:
                return None
            manifest = json.loads(manifest_raw.decode("utf-8-sig"))
            inputs = manifest.get("inputs", {})
            if not isinstance(inputs, dict):
                return None
            identities = {}
            for key, entry in sorted(inputs.items()):
                if not isinstance(entry, dict) or not entry.get("path"):
                    return None
                path = Path(entry["path"]).expanduser()
                # Keep the declared path for freshness checks: resolving a source
                # symlink once would miss a later switch to a different target.
                path = (root / path).absolute() if not path.is_absolute() else path.absolute()
                original = path.read_bytes()
                extra_hashes[str(path)] = _sha(original)
                # Only an exact TXT copy of this authoritative source can have
                # its changes localized through the proved line spans above.
                equivalent = False
                if key == "source" and path.suffix.lower() == ".txt":
                    equivalent = original.decode("utf-8-sig") == source
                if equivalent:
                    guards.pop(str(path), None)
                    guards.pop(str(path.resolve()), None)
                identities[key] = {"path": str(path), "sha256": "localized-source" if equivalent else _sha(original)}
            guards["original_inputs"] = identities
            # Source classification is a semantic dependency; execution status,
            # mtimes and completed-stage receipts are deliberately not.
            for key in ("meta", "submitted_meta"):
                if key in manifest:
                    guards["manifest:" + key] = agent_tasks._sanitized(manifest[key])
        media_root = root / "01_source" / "media"
        media_hashes = {}
        if media_root.exists():
            if media_root.is_symlink() or not media_root.is_dir():
                return None
            for path in sorted(media_root.rglob("*")):
                if path.is_symlink():
                    return None
                if path.is_file():
                    rel = path.relative_to(root).as_posix()
                    media_hashes[rel] = _sha(_read(root, rel))
        guards["source_media"] = media_hashes
        # Add real original/media bytes to freshness checks, not to semantic
        # scope. The ordinary task still retains EVERY original batch hash.
        hashes.update(extra_hashes)
        hashes.update(media_hashes)
        if structure_raw is not None:
            hashes[_STRUCTURE] = _sha(structure_raw)
        return Projection(data, spans, guards, freshness_guards)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        return None


def check_guards(batch, part):
    """Check optional/global evidence inventories even for newly arrived files."""
    scope = part.get("cache_scope")
    if not isinstance(scope, dict):
        return
    root = Path(batch).resolve()
    guards = scope["guards"]
    freshness = part.get("freshness_guards", guards)
    for relative in _GLOBAL_FILES:
        content = _read(root, relative)
        current = _sha(content) if content is not None else "missing"
        if current != freshness[relative]:
            raise ValueError("Partition global evidence changed during agent task")
    media = {}
    media_root = root / "01_source" / "media"
    if media_root.exists():
        if media_root.is_symlink() or not media_root.is_dir():
            raise ValueError("Partition media inventory changed during agent task")
        for path in sorted(media_root.rglob("*")):
            if path.is_symlink():
                raise ValueError("Partition media inventory changed during agent task")
            if path.is_file():
                relative = path.relative_to(root).as_posix()
                media[relative] = _sha(_read(root, relative))
    if media != guards["source_media"]:
        raise ValueError("Partition media inventory changed during agent task")
