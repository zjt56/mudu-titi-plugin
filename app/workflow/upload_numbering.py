"""Read-only original-number projection shared by upload and local migrations.

Local numbers remain the compatibility contract for numbered items. Only current,
uniquely bound source evidence can turn an internal alias into a null wire range.
No source/candidate files are created or changed here.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

VERSION = "upload-workflow.upload-numbering/v1"
_UNNUMBERED = {"single_writing_section", "unnumbered_section"}


def _positive(value):
    return type(value) is int and value > 0


def _binding(unit):
    span = unit.get("source_span")
    value = span.get("number_binding") if isinstance(span, dict) else None
    return value if isinstance(value, str) else None


def _numbers(unit):
    values = unit.get("numbers")
    if values is None and _positive(unit.get("number")):
        return [unit["number"]]
    return values


def _valid_numbers(values):
    return (isinstance(values, list) and bool(values)
            and all(_positive(n) for n in values) and len(set(values)) == len(values))


def _context(batch_dir):
    """Load evidence without introducing a whole-paper source acceptance gate."""
    evidence = {"version": VERSION, "files": {}, "units": [], "diagnostics": []}
    if batch_dir is None:
        return {}, evidence, None
    root = Path(batch_dir).resolve()
    def read(relative):
        path = root / relative
        if not path.resolve().is_relative_to(root) or any(p.is_symlink() for p in (path, path.parent)):
            raise ValueError("编号证据路径不安全：" + relative)
        content = path.read_bytes()
        evidence["files"][relative] = hashlib.sha256(content).hexdigest()
        return content.decode("utf-8-sig")
    try:
        confirmed = json.loads(read("01_source/structure.confirmed.json"))
        rows = confirmed.get("units") if isinstance(confirmed, dict) else None
        if not isinstance(rows, list):
            return {}, evidence, None
    except (OSError, ValueError, TypeError):
        return {}, evidence, None
    by_id = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        row = deepcopy(row)
        if confirmed.get("identity_version") == "scoped-v1":
            row["identity_version"] = "scoped-v1"
        # Confirmed structures also support digit strings under the established
        # local reader contract; wire/local item ranges still require real ints.
        values = row.get("numbers")
        if (isinstance(values, list) and values and all(type(n) is int and n > 0
                or isinstance(n, str) and n.isdigit() and int(n) > 0 for n in values)):
            row["numbers"] = [int(n) for n in values]
        # Same established legacy-ID derivation as agent_tasks.confirmed_units;
        # matching still uses the complete unit identity, never a global number.
        uid = row.get("unit_id") or row.get("id")
        if not uid and _valid_numbers(row.get("numbers")):
            uid = ("q-" if row.get("unit_kind") == "standalone" else "group-") + "-".join(map(str, row["numbers"]))
        if uid:
            by_id.setdefault(str(uid), []).append(row)
    context = {"root": root, "read": read, "fresh": None, "source": None, "cleaned": None}
    return by_id, evidence, context


def _prove_unnumbered(unit, context):
    if context is None:
        return None, "缺少 batch_dir，无法验证当前无号来源绑定"
    from . import denoise, question_content, question_draft
    from .question_identity import source_key
    try:
        if context["fresh"] is None:
            source = context["read"]("01_source/正文.txt")
            structure = json.loads(context["read"]("01_source/structure.json"))
            cleaned, _, _ = denoise.clean_body(source)
            context.update(source=source, cleaned=cleaned,
                           fresh=question_content._rebuild_source_units(context["root"], structure, cleaned))
        matches = [fresh for fresh in context["fresh"]
                   if source_key(fresh) == source_key(unit)
                   and fresh.get("unit_kind") == unit.get("unit_kind")
                   and _binding(fresh) == _binding(unit)]
        if len(matches) != 1:
            return None, "当前来源不能唯一重建同一无号单元"
        text, proof = question_draft._bound_source(unit, context["source"], context["cleaned"], context["fresh"])
        rebuilt_text, _ = question_draft._bound_source(matches[0], context["source"], context["cleaned"], context["fresh"])
        if not text or not rebuilt_text or "".join(text.split()) != "".join(rebuilt_text.split()):
            return None, "确认无号窗口与当前来源重建不一致"
        if _binding(unit) == "unnumbered_section" and (unit.get("source_span") or {}).get("printed_numbers") != []:
            return None, "unnumbered_section 必须明确 printed_numbers=[]"
        proof["source_text_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return proof, None
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        return None, "当前无号来源验证失败：" + str(exc)


def project_batch(batch_dict, batch_dir=None):
    """Return deep-copied ``items``, structured ``errors`` and numbering ``evidence``.

    Input and child order are preserved. Diagnostics about the unconfirmed remote
    child-alias contract are informational; local child numbers stay untouched.
    """
    items = deepcopy(batch_dict.get("items", [])) if isinstance(batch_dict, dict) else []
    by_id, evidence, context = _context(batch_dir)
    errors = []
    ids = Counter(str(item.get("unit_id") or item.get("id") or "") for item in items if isinstance(item, dict)) if isinstance(items, list) else Counter()
    def error(code, message, index, item, field=""):
        errors.append({"code": code, "message": message, "unit_id": str(item.get("unit_id") or item.get("id") or ""),
                       "path": f"items[{index}]" + ("." + field if field else ""),
                       "batch_dir": str(Path(batch_dir)) if batch_dir is not None else ""})
    for index, item in enumerate(items if isinstance(items, list) else []):
        if not isinstance(item, dict):
            continue
        uid = str(item.get("unit_id") or item.get("id") or "")
        children = item.get("subquestions") or []
        kind = item.get("unit_kind", "group" if children else "standalone")
        values = _numbers(item)
        candidates = by_id.get(uid, [])
        marked = _binding(item) in _UNNUMBERED or any(_binding(u) in _UNNUMBERED for u in candidates)
        unnumbered = False
        proof = None
        if marked:
            if ids[uid] != 1 or len(candidates) != 1:
                error("PAPER_NUMBER_EVIDENCE_REQUIRED", "无号来源必须按唯一 unit_id 回接当前确认结构", index, item, "source_span")
            else:
                confirmed = candidates[0]
                same = (confirmed.get("unit_kind") == kind and _numbers(confirmed) == values
                        and all(key not in item or item[key] == confirmed.get(key)
                                for key in ("section_id", "question_ids"))
                        and ("source_span" not in item or item["source_span"] == confirmed.get("source_span"))
                        and ("printed_numbers" not in item or item["printed_numbers"] == [])
                        and (_binding(item) not in _UNNUMBERED or _binding(item) == _binding(confirmed)))
                if not same:
                    error("PAPER_NUMBER_SOURCE_CONFLICT", "本地身份/题号/无号标记与确认单元不一致", index, item)
                else:
                    proof, reason = _prove_unnumbered(confirmed, context)
                    if reason:
                        error("PAPER_NUMBER_EVIDENCE_REQUIRED", reason, index, item, "source_span")
                    else:
                        unnumbered = True
        valid = _valid_numbers(values)
        if kind == "standalone":
            valid = valid and len(values) == 1 and ("number" not in item or type(item["number"]) is int and item["number"] == values[0])
        elif kind == "group":
            child_numbers = [c.get("number") for c in children if isinstance(c, dict)] if isinstance(children, list) else []
            valid = (valid and isinstance(children, list) and len(child_numbers) == len(children)
                     and _valid_numbers(child_numbers) and set(child_numbers) == set(values))
        if not valid:
            error("PAPER_NUMBER_SOURCE_CONFLICT", "本地 number/numbers 与全部唯一子题号必须完整一致", index, item, "numbers")
        if valid and kind == "group" and not unnumbered and max(values) - min(values) + 1 != len(values):
            error("PAPER_NUMBER_GROUP_NONCONTIGUOUS", f"题组原号必须连续，实际题号 {values}", index, item, "numbers")
            valid = False
        projected = (None, None) if unnumbered else (min(values), max(values)) if valid else None
        supplied = "numberStart" in item or "numberEnd" in item
        if supplied:
            start, end = item.get("numberStart"), item.get("numberEnd")
            shape = ("numberStart" in item and "numberEnd" in item
                     and ((start is None and end is None) or (_positive(start) and _positive(end) and start <= end
                          and (kind != "standalone" or start == end))))
            if not shape:
                error("PAPER_NUMBER_RANGE_INVALID", "范围必须成对为正整数或 null；独立题两端相同，且不得反向", index, item, "numberStart/numberEnd")
            elif projected is not None and (start, end) != projected:
                error("PAPER_NUMBER_SOURCE_CONFLICT", f"显式范围 {(start, end)} 与来源投影 {projected} 冲突", index, item, "numberStart/numberEnd")
        elif projected is not None and not (marked and not unnumbered):
            item["numberStart"], item["numberEnd"] = projected
        evidence["units"].append({"unit_id": uid, "state": "proven-unnumbered" if unnumbered else "unknown" if marked or not valid else "printed",
                                  "range": list(projected) if projected is not None else None, "binding": proof})
        if unnumbered and kind == "group":
            evidence["diagnostics"].append({"code": "UNNUMBERED_SUBQUESTION_CONTRACT_UNRESOLVED", "unit_id": uid,
                "path": f"items[{index}].subquestions", "message": "无号子题远端关联契约待核实；暂保留现有 child.number 内部别名及答案传输行为"})
    return {"items": items, "errors": errors, "evidence": evidence}
