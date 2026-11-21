"""Evidence-bound, model-free copying of original explanations and summaries.

Word excerpts are authoritative; PDF/OCR may fill only the same missing field.
Ambiguous ownership or competing original prose declines the fast path so the
normal task/import protocol can report the source problem. Nothing is invented.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import upload_content as source

PLACEHOLDER = "无"
DIRECT_COPY_EVIDENCE = "按题号从 Word 原文直接抄录。"


def _word_sources(batch):
    batch = Path(batch)
    paths = [batch / name for name in source.SOURCES if (batch / name).is_file()]
    # source.txt is the full extraction, often a duplicate of body + appendix.
    # Never let that projection compete with the canonical excerpts.
    if not paths:
        path = batch / "01_source/materials/source.txt"
        if path.is_file():
            paths.append(path)
    return paths


def _pdf_sources(batch):
    material_dir = Path(batch) / "01_source/materials"
    listening_only = source.listening_script_sources(batch)
    return [p for p in sorted(material_dir.glob("attachment_*.txt"))
            if re.fullmatch(r"attachment_\d{3,}\.txt", p.name) and p.is_file()
            and p.relative_to(Path(batch)).as_posix() not in listening_only]


_WRITING_START = re.compile(r"^【(?:思路点拨|参考范文)】")


def _writing_explanation(batch):
    """书面表达专用：逐字抄取答案区的【思路点拨】/【参考范文】/【范文点评】整段作为解析。

    书面表达不是客观题（规则 23），附录常有“思路点拨＋参考范文＋范文点评”这类写作说明，
    却不在题号解析里，导致解析留空「无」（用户 2026-11-19 明确要求写入，不再留空）。
    只在源文件里逐字存在这些标题段时抄录，绝不自写范文或编造；找不到保持占位。
    返回 (相对路径, 文本) 或 (None, None)。
    """
    batch = Path(batch)
    for name in ("01_source/附录.txt", "01_source_ans/正文.txt"):
        path = batch / name
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        starts = [j for j, line in enumerate(lines) if _WRITING_START.match(line.strip())]
        if not starts:
            continue
        # 有书面表达大题标题时只取标题之后的写作说明；无标题（附录只有孤立范文）用首个。
        head = None
        for i, line in enumerate(lines):
            sec = source._section(line)
            if sec and sec[1] == "书面表达":
                head = i
                break
        candidates = [j for j in starts if head is None or j > head]
        start = (candidates or starts)[0]
        collected = []
        strip_ans_img = name.startswith("01_source_ans/")
        for k in range(start, len(lines)):
            line = lines[k]
            sec = source._section(line)
            if k > start and sec and sec[1] != "书面表达":
                break
            if re.match(r"^\s*附[:：]\s*听力", line.strip()):
                break
            if strip_ans_img:
                line = re.sub(r"\[IMG\s+[^\]]+\]", "", line)
            part = source._source_line(line) if strip_ans_img else line.strip()
            collected.append(part)
        text = "\n".join(part for part in collected if part).strip()
        if text:
            return name, text
    return None, None


def _boundary(line):
    return (source._section(line) or source._answer_range_boundary(line)
            or source.ICON_HEADING.match(line)
            or source.ANNOTATION.match(line) or source.PASSAGE_HEADING.match(line)
            or source.PART_HEADING.match(line) or source.PASSAGE_INSTRUCTION.match(line)
            or re.fullmatch(r"\[/?TABLE[^\]]*\]", line))


def _parse(lines, path="01_source/materials/source.txt"):
    """Parse a full extraction using the consumer's explicit label boundaries.

    Return located rows, not a first-wins dictionary. Keeping every occurrence
    allows callers to reject competing originals and unbound passage summaries.
    """
    strip_ans_img = str(path).startswith("01_source_ans/")
    lines = [source._source_line(line) for line in lines]

    def _clean_text(parts):
        joined = "\n".join(parts).strip()
        if strip_ans_img:
            joined = re.sub(r"\[IMG\s+[^\]]+\]", "", joined).strip()
        return joined
    questions, summaries = [], []
    owner = None
    prior = []
    for index, line in enumerate(lines):
        summary = source.SUMMARY.search(line)
        question = source._question(line)
        numbered = source.QUESTION_BOUNDARY.match(line)
        stray = source.STRAY_EXPLANATION.match(line)
        if numbered and re.search(source.EXPLANATION_LABEL, line) and not question:
            raise ValueError("multiple question anchors on an explanation line")
        if summary:
            parts = [summary.group(1)]
            members = []
            for later in lines[index + 1:]:
                if source.SUMMARY.search(later) or _boundary(later):
                    break
                number = source.QUESTION_BOUNDARY.match(later)
                if number:
                    members.append(int(number.group(1)))
                elif not members:
                    parts.append(later)
            # A trailing summary belongs to the bounded passage before it;
            # a leading summary belongs to the bounded following question set.
            members = members or prior[:]
            text = _clean_text(parts)
            summaries.append({"source_text": text,
                              "members": members, "owner": members[0] if members else None,
                              "path": path, "line": index + 1})
            owner, prior = None, []
            continue
        if numbered:
            owner = int(numbered.group(1))
            prior.append(owner)
        elif not stray and _boundary(line):
            owner, prior = None, []
            continue
        if question or stray:
            if owner is None:
                continue
            match = question or stray
            parts = [line[match.end():]]
            for later in lines[index + 1:]:
                if (source.QUESTION_BOUNDARY.match(later) or source.SUMMARY.search(later)
                        or source.STRAY_EXPLANATION.match(later) or _boundary(later)):
                    break
                parts.append(later)
            text = _clean_text(parts)
            if text:
                questions.append({"number": owner, "source_text": text,
                                  "path": path, "line": index + 1})
    return questions, summaries


def _units(batch_dir):
    from .agent_tasks import confirmed_units
    return confirmed_units(Path(batch_dir))


def _unique(rows, *, allow_conflict=False):
    """Select one real occurrence; equivalent copies are preferred, but a
    question with two authoritative explanations may use either complete
    source occurrence without synthesizing or merging text."""
    if not rows:
        return None
    values = {source._verbatim(row["source_text"]) for row in rows}
    if len(values) > 1 and not allow_conflict:
        raise ValueError("conflicting original content")
    return rows[0]


def _assign_summaries(units, summaries):
    """Bind only a complete, uniquely owned question set; never zip passages."""
    candidates = {}
    for row in summaries:
        if not row.get("source_text"):
            continue
        members = set(row.get("members") or [])
        matches = [unit for unit in units if unit["unit_kind"] == "group"
                   and members and members == {int(n) for n in unit["numbers"]}]
        if len(matches) != 1:
            raise ValueError("summary has no unique complete passage ownership")
        candidates.setdefault(matches[0]["unit_id"], []).append(row)
    return {uid: _unique(rows) for uid, rows in candidates.items()}


def build_payload(batch_dir):
    batch = Path(batch_dir)
    units = _units(batch)
    # The legacy extractor indexes by printed number. Scoped units deliberately
    # use the normal model/source_ref path until direct extraction is scope-aware.
    if any(unit.get('identity_version') == 'scoped-v1' for unit in units):
        return None
    paths, pdf_paths = _word_sources(batch), _pdf_sources(batch)
    if not paths and not pdf_paths:
        return None
    # Canonical excerpts use precisely the extraction consumed by upload_content.
    # Full extraction fallback is needed for older batches without those excerpts.
    report = source.inspect_content(batch, {"items": []})
    word_questions = [r for r in report["all_questions"] if r["path"] in source.SOURCES]
    word_summaries = [r for r in report["all_summaries"] if r["path"] in source.SOURCES]
    try:
        if paths and paths[0].name == "source.txt":
            word_questions, word_summaries = _parse(
                paths[0].read_text(encoding="utf-8-sig").splitlines())
        else:
            # The consumer deliberately ignores a second stray label after an
            # already-explained answer. The fast path must still reject two
            # different explicitly labelled originals instead of choosing one.
            for path in paths:
                rows, _ = _parse(path.read_text(encoding="utf-8-sig").splitlines(),
                                 path.relative_to(batch).as_posix())
                for number in {r["number"] for r in rows}:
                    _unique([r for r in rows if r["number"] == number], allow_conflict=True)
    except ValueError:
        return None
    pdf_questions = [r for r in report["all_questions"] if r["path"] not in source.SOURCES]
    pdf_summaries = [r for r in report["all_summaries"] if r["path"] not in source.SOURCES]
    units = _units(batch)
    try:
        word_by_uid = _assign_summaries(units, word_summaries)
        # PDF fallback is decided by the same unit, before inspecting conflicts
        # in fields already supplied by Word.
        pdf_needed = []
        for row in pdf_summaries:
            members = set(row.get("members") or [])
            if any(members == {int(n) for n in u["numbers"]}
                   for u in units if u["unit_id"] in word_by_uid):
                continue
            pdf_needed.append(row)
        summary_by_uid = {**_assign_summaries(units, pdf_needed), **word_by_uid}
        explanations = {}
        for number in {int(n) for u in units for n in u["numbers"]}:
            rows = [r for r in word_questions if r["number"] == number]
            if not rows:
                rows = [r for r in pdf_questions if r["number"] == number]
            explanations[number] = _unique(rows, allow_conflict=True)
    except ValueError:
        return None
    from . import listening_script
    scripts = listening_script.extract(batch, units)
    items = []
    for unit in units:
        numbers = [int(n) for n in unit["numbers"]]
        uid = unit["unit_id"]
        summary = summary_by_uid.get(uid)
        used = [explanations[n] for n in numbers if explanations[n]]
        if summary:
            used.append(summary)
        evidence = [DIRECT_COPY_EVIDENCE]
        for row in used:
            prefix = "PDF OCR 兜底：" if row["path"] not in source.SOURCES and "attachment_" in row["path"] else "原文："
            location = f"{prefix}{row['path']}:{row['line']}"
            if location not in evidence:
                evidence.append(location)
        def text(number):
            row = explanations[number]
            return row["source_text"] if row else PLACEHOLDER
        grouped = unit["unit_kind"] == "group"
        script = scripts.get(uid)
        explanation = PLACEHOLDER if grouped else text(numbers[0])
        # 书面表达：解析即答案区的写作说明（思路点拨/参考范文/范文点评）。题号解析
        # 无法绑定这些段落时，按来源逐字补入；仍找不到就保持「无」，不编造（规则 23）。
        if unit.get("type") == "书面表达" and explanation == PLACEHOLDER:
            anchor_path, writing_text = _writing_explanation(batch)
            if writing_text:
                explanation = writing_text
                location = f"原文：{anchor_path}"
                if location not in evidence:
                    evidence.append(location)
        if script:
            explanation = listening_script.combine(explanation, script[0])
            evidence.append(f"听力录音材料：{script[1]}:{script[2]}")
        items.append({"unit_id": uid,
                      "explanation": explanation,
                      "articleSummary": summary["source_text"] if summary else PLACEHOLDER,
                      "subquestions": [{"number": n, "explanation": text(n)} for n in numbers] if grouped else [],
                      "evidence": evidence})
    return {"items": items}


def install(batch_dir):
    """Install only through the normal source-bound protocol and stage validator."""
    import sys
    from . import agent_protocol
    batch = Path(batch_dir)
    exported = agent_protocol.export_task(batch, 'explanations', max_input_chars=sys.maxsize)
    if exported.get('status') != 'ready':
        raise ValueError('解析直抄前置交付未满足：' + json.dumps(exported, ensure_ascii=False))
    payload = build_payload(batch)
    if payload is None:
        return None
    response = {'protocol': agent_protocol.PROTOCOL, 'task_id': exported['task_id'],
                'result': {'status': 'completed',
                           'artifacts': {'04_knowledge/explanations.json': payload},
                           'notes': ['按来源坐标直接抄录原解析与文章大意，未调用模型'],
                           'findings': []}}
    response_path = Path(exported['package_dir']) / 'direct-result.json'
    agent_protocol._atomic(response_path, agent_protocol._encoded(response))
    report = agent_protocol.import_result(batch, exported['task_id'], response_path)
    if report.get('completed') is not True:
        raise ValueError('解析直抄候选未通过交付校验：' + json.dumps(report, ensure_ascii=False))
    return batch / '04_knowledge/explanations.json'
