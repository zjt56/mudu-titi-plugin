# -*- coding: utf-8 -*-
"""结构自动闸门：去噪 → 元信息抽取 → 按题型结构化 → 机器自检。

替代原 `structure_gate` 的人工确认：拆分、去噪、按题型成组都由机器完成，
人只在机器报出「不确定/漏题/题组边界歧义」时才介入。

产出两份文件：
- `01_source/structure.confirmed.json`：确认后的结构化视图（题目边界、单元类型、
  题号覆盖、元信息），下游 types/knowledge/assemble 以此为唯一输入；
- `07_review/structure-gate.json`：闸门报告（逐项检查、阻断项、unresolved）。

关键口径（与 docs/structure.md 一致）：
- 独立题：一题一单元，逐题呈现；
- 组合题（完形/阅读/任务型阅读/听力信息转换/补全对话等）：整组是一个单元，
  以题组形式呈现，共享材料只出现一次。
"""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from . import denoise
from .extract import _plain, guide_numbers, material_label, source_group_partitions

# 组合题家族：这些题型整组作为一个单元，而不是拆成逐题
GROUP_TYPE_HINTS = (
    "完形", "阅读理解", "阅读", "任务型", "信息转换", "补全对话", "语法选择",
    "选词", "短文填空", "教材语篇", "听力长对话", "听力短文", "听力信息",
)
# 独立题家族：逐题一单元
STANDALONE_TYPE_HINTS = (
    "单项填空", "听力短对话", "关键词语选择", "单词拼写", "完成句子", "语音", "翻译",
    "改错", "情景运用", "书面表达",
)

QUESTION_START = re.compile(r"^\s*(?:[(（]\s*[)）]\s*)?(\d{1,3})\s*[.．]")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig") if path.is_file() else ""


def _section_type_label(type_name: str) -> str:
    if not isinstance(type_name, str) or not type_name.strip():
        return "未判定题型"
    return type_name.strip()


def is_group_type(type_name: str) -> bool | None:
    """判断某题型是否应成组。返回 True/False，无法判断返回 None（交人工）。"""
    name = _section_type_label(type_name)
    for hint in GROUP_TYPE_HINTS:
        if hint in name:
            return True
    for hint in STANDALONE_TYPE_HINTS:
        if hint in name:
            return False
    return None


def _expand_question_numbers(entry) -> list[int]:
    """把 sections[].questions 的一项展开成题号列表，支持单题或 [start, end]。"""
    if isinstance(entry, list) and len(entry) == 2:
        try:
            start, end = int(entry[0]), int(entry[1])
            return list(range(start, end + 1))
        except (ValueError, TypeError):
            return []
    if isinstance(entry, int):
        return [entry]
    if isinstance(entry, str) and entry.strip().isdigit():
        return [int(entry.strip())]
    return []


def _section_numbers(section: dict) -> list[int]:
    nums: list[int] = []
    for entry in section.get("questions") or []:
        nums.extend(_expand_question_numbers(entry))
    return sorted(set(nums))


def _build_units(sections: list[dict], groups: list[dict], source_lines: list[str]) -> list[dict]:
    """把 section / group 信息组织成入库单元：独立题逐题，组合题成组。"""
    group_of: dict[int, list[int]] = {}
    for group in groups:
        nums = _section_numbers({"questions": group.get("questions") or []})
        if nums:
            for n in nums:
                group_of[n] = nums

    units: list[dict] = []
    covered: set[int] = set()
    for section in sections:
        type_name = _section_type_label(section.get("type"))
        nums = _section_numbers(section)
        group_flag = is_group_type(type_name)
        # 该 section 里已被 groups 声明为组内的题号
        declared_groups = [sorted(set(group_of[n])) for n in nums if n in group_of]
        declared_groups = sorted({tuple(g) for g in declared_groups})
        # 原卷的分段导语/材料标题比“整节一组”的旧候选更精确。
        partitions = source_group_partitions(source_lines, nums) if group_flag else []
        if partitions:
            for part in partitions:
                unit = _make_unit("group", type_name, part["questions"], source_lines)
                chunk = source_lines[part["source_start_line"] - 1:part["source_end_line"]]
                unit["stem"] = "\n".join(line for line in chunk if not material_label(line)).strip()
                unit["source_span"] = {"start_line": part["source_start_line"], "end_line": part["source_end_line"],
                                       "basis": "cleaned_source"}
                units.append(unit)
            continue
        grouped = set().union(*[set(g) for g in declared_groups]) if declared_groups else set()
        loose = [n for n in nums if n not in grouped]

        if declared_groups:
            want_group = True
        elif group_flag is not None:
            want_group = group_flag
        else:
            # 题型未知：题号连续且数量 >= 2 才倾向于成组，否则独立
            want_group = len(loose) >= 2

        if want_group and len(loose) >= 1:
            # 未声明成组但按题型应成组的整段题号，合并成一个组
            if declared_groups:
                for g in declared_groups:
                    units.append(_make_unit("group", type_name, list(g), source_lines))
                    covered.update(g)
                if loose:
                    units.append(_make_unit("group", type_name, loose, source_lines))
                    covered.update(loose)
            else:
                units.append(_make_unit("group", type_name, loose, source_lines))
                covered.update(loose)
        else:
            for g in declared_groups:
                units.append(_make_unit("group", type_name, list(g), source_lines))
                covered.update(g)
            for n in loose:
                units.append(_make_unit("standalone", type_name, [n], source_lines))
                covered.update({n})
    # 无题号作文必须绑定唯一的书面表达节及唯一相邻题号，不能靠“比最大题号大”。
    from .preview import SECTION, _strong_number
    for section in sections:
        nums = _section_numbers(section)
        if "书面表达" not in str(section.get("type")) or len(nums) != 1:
            continue
        title = _plain(str(section.get("title") or "")).strip()
        starts = [i for i, line in enumerate(source_lines)
                  if (title and _plain(line).strip() == title)
                  or (not title and SECTION.match(_plain(line)) and "书面表达" in _plain(line))]
        if len(starts) != 1:
            continue
        start = starts[0]
        prior = [n for line in source_lines[:start] if (n := _strong_number(line)) is not None]
        if not prior or nums[0] != max(prior) + 1:
            continue
        end = next((i for i in range(start + 1, len(source_lines)) if SECTION.match(_plain(source_lines[i]))), len(source_lines))
        first = next((_plain(line).strip() for line in source_lines[start + 1:end] if _plain(line).strip()), "")
        explicit = (_strong_number(first) == nums[0]
                    and bool(re.search(r"(?:\bwrite\s+\S.+|请[^\n]*写[^\n]*\S|写一[篇封].+)", first, re.I)))
        for unit in units:
            if unit["numbers"] == nums and not explicit:
                unit["stem"] = "\n".join(source_lines[start + 1:end]).strip()
                unit["source_span"] = {"start_line": start + 2, "end_line": end,
                                       "basis": "cleaned_source", "number_binding": "single_writing_section"}
    from .question_content import question_parts
    for section in sections:
        numbers = _section_numbers(section)
        peer_count = max([len(question_parts(source_lines, n)["options"]) for n in numbers] or [0])
        for unit in units:
            if set(unit["numbers"]).issubset(numbers):
                if unit["unit_kind"] == "group":
                    # 同一阅读大题可同时含选择题与限词数问答，不能跨材料套用选项数。
                    unit["source_expected_options"] = max([len(question_parts(unit["stem"].splitlines(), n)["options"]) for n in unit["numbers"]] or [0])
                else:
                    unit["source_expected_options"] = peer_count
    return units


def _make_unit(unit_kind: str, type_name: str, numbers: list[int], source_lines: list[str]) -> dict:
    numbers = sorted(set(int(n) for n in numbers))
    label = f"{numbers[0]}–{numbers[-1]} {len(numbers)}小题" if len(numbers) > 1 else str(numbers[0])
    return {
        "unit_kind": unit_kind,
        "type": type_name,
        "numbers": numbers,
        "label": label,
        "stem": _slice_stem(source_lines, numbers, unit_kind),
    }


def _slice_stem(source_lines: list[str], numbers: list[int], unit_kind: str = "standalone") -> str:
    """用现有切片逻辑取该单元的原文片段，只作为结构化呈现的文本。"""
    try:
        from .preview import slice_text, slice_range
        if len(numbers) == 1 and unit_kind != "group":
            chunk = slice_text(source_lines, numbers[0])
        else:
            chunk = slice_range(source_lines, numbers)
        return "\n".join(chunk)
    except Exception:
        return ""


def _load_structure(structure_path: Path) -> dict:
    if not structure_path.is_file():
        return {}
    try:
        return json.loads(structure_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}


def _prove_single_writing_number(original_path: Path | None, source_text: str,
                                 sections: list[dict], issue: dict, hashes: dict) -> dict | None:
    """Prove a narrow text-only inference from the original, never candidate counts alone."""
    from .extract import infer_structure
    from .preview import SECTION, _strong_number, _weak_numbers

    if not original_path or not hashes["source_sha256"] or not hashes["original_sha256"]:
        return None
    try:
        if original_path.suffix.lower() == ".docx":
            from .docx_extract import extract_docx
            with tempfile.TemporaryDirectory(prefix="structure-number-proof-") as temp:
                native = extract_docx(original_path, Path(temp))
            if native["warnings"]:
                return None
            original_text, method = native["text"], "docx-native"
        elif original_path.suffix.lower() == ".txt":
            original_text, method = _read_text(original_path), "original-text"
        else:
            return None
    except (OSError, ValueError, UnicodeError):
        return None
    # Only the extractor's explicit answer-key classification may exclude an appendix.
    original_candidate, original_body, appendix = infer_structure(original_text)
    if appendix and not any(x.get("code") == "ANSWER_APPENDIX_DETECTED"
                            for x in original_candidate.get("unresolved", [])):
        return None
    original_cleaned = denoise.clean_body(original_body)[0]
    normalize = lambda text: [_plain(line).strip() for line in text.splitlines() if _plain(line).strip()]
    if normalize(original_cleaned) != normalize(source_text):
        return None
    lines = original_cleaned.splitlines()
    headings = [i for i, line in enumerate(lines) if SECTION.match(_plain(line))]
    writing = [i for i in headings if "书面表达" in _plain(lines[i])]
    candidates = [s for s in sections if "书面表达" in str(s.get("type"))]
    if len(writing) != 1 or len(candidates) != 1 or candidates[0] is not sections[-1]:
        return None
    start, section = writing[0], candidates[0]
    title = _plain(lines[start]).strip()
    counts = re.findall(r"共\s*(\d+)\s*小题", title)
    if headings[-1] != start or counts != ["1"] or title != _plain(str(section.get("title") or "")).strip():
        return None
    if issue.get("line") != section.get("source_line") or not isinstance(issue.get("line"), int):
        return None
    prior, strong, order = set(), [], []
    for line in lines[:start]:
        if SECTION.match(_plain(line)):
            continue
        n = _strong_number(line)
        if n is not None:
            strong.append(n)
        for found in ([n] if n is not None else []) + _weak_numbers(line):
            if found not in prior:
                order.append(found)
                prior.add(found)
    if not prior or order != list(range(1, max(prior) + 1)) or len(strong) != len(set(strong)):
        return None
    number = max(prior) + 1
    if _section_numbers(section) != [number]:
        return None
    body = "\n".join(lines[start + 1:]).strip()
    # Multiple lists may restart only after an explicit content/requirements label.
    # Other numbered lines remain ambiguous and must not be hidden by this proof.
    previous, last_nonempty, points_valid = 0, "", True
    label_only = r"(?:写作)?(?:要点|注意|注意事项|要求|提示)\s*[:：]"
    for line in lines[start + 1:]:
        plain = _plain(line).strip()
        point = _strong_number(line)
        labeled = False
        # 要点列表可能和标签同一行：注意:1.词数80~100; 仍按新列表的起点处理。
        if point is None and re.match(label_only + r"\s*1\s*[.．、]", plain):
            point, labeled = 1, True
        if point is not None:
            restart = (point == 1 and previous > 0 and (labeled or re.fullmatch(label_only, last_nonempty)))
            if point != previous + 1 and not restart:
                points_valid = False
            previous = point
        if plain:
            last_nonempty = plain
    if (not body or not re.search(r"(?:\bwrite\s+\S.+|请[^\n]*写[^\n]*\S|写一[篇封].+)", _plain(body), re.I)
            or not points_valid
            or any(_weak_numbers(line) for line in lines[start + 1:])):
        return None
    from . import manifest as mf
    if mf.sha256_file(original_path) != hashes["original_sha256"]:
        return None
    return {"code": "QUESTION_NUMBER_INFERRED", "message": issue.get("message", "作文无印刷题号"),
            "resolved": True, "resolution": f"确定性文字结构推定为第{number}题；未进行视觉核验",
            "resolution_evidence": {
                "method": "deterministic_single_writing_number", "original_text_method": method,
                "source_sha256": hashes["source_sha256"], "original_sha256": hashes["original_sha256"],
                "evidence": [f"原件正文（{method}，去噪后）第{start + 1}行：{title}",
                             f"原件正文前序实际题号/空位完整覆盖1–{number - 1}，无重复显式题号",
                             f"唯一书面表达为末节，标题原文声明共1小题，正文有写作任务；候选编号{number}连续",
                             "原件正文文字与当前正文去噪后逐行一致；仅核对文字序列，未核验图像、版面或听力"]}}


def _original_matches_cleaned(original_path, source_text, original_hash):
    """Require independently readable original text before localizing uncertainty."""
    from . import manifest as mf
    from .extract import infer_structure
    if not original_path or not original_hash:
        return False
    try:
        if original_path.suffix.lower() == ".txt":
            text = _read_text(original_path)
        elif original_path.suffix.lower() == ".docx":
            from .docx_extract import extract_docx
            with tempfile.TemporaryDirectory(prefix="structure-scope-") as temp:
                native = extract_docx(original_path, Path(temp))
            if native["warnings"]:
                return False
            text = native["text"]
        else:
            return False
        _, body, _ = infer_structure(text)
        cleaned = denoise.clean_body(body)[0]
        normalize = lambda value: [_plain(line).strip() for line in value.splitlines() if _plain(line).strip()]
        return normalize(cleaned) == normalize(source_text) and mf.sha256_file(original_path) == original_hash
    except (OSError, ValueError, UnicodeError, KeyError, TypeError):
        return False


def run_gate(batch_dir: Path, *, output_dir: Path | None = None) -> dict:
    """执行结构自动闸门，写产物并返回报告。

    报告 `passed` 为真才放行；不通过时列出阻断项供人工或重跑处理。
    """
    batch_dir = Path(batch_dir)
    source_path = batch_dir / "01_source" / "正文.txt"
    structure_path = batch_dir / "01_source" / "structure.json"

    raw_text = _read_text(source_path)
    cleaned_text, meta, removed = denoise.clean_body(raw_text) if raw_text else ("", {}, [])
    source_lines = cleaned_text.splitlines()

    structure = _load_structure(structure_path)
    sections = structure.get("sections") if isinstance(structure.get("sections"), list) else []
    groups = structure.get("groups") if isinstance(structure.get("groups"), list) else []
    image_questions = structure.get("image_questions") if isinstance(structure.get("image_questions"), list) else []

    from . import manifest as mf
    original_path = None
    try:
        state = mf.load(batch_dir)
        original_path = Path(state.get("inputs", {}).get("source", {}).get("path", ""))
    except (OSError, ValueError, KeyError):
        pass
    hashes = {"source_sha256": mf.sha256_file(source_path) if source_path.is_file() else None,
              "structure_sha256": mf.sha256_file(structure_path) if structure_path.is_file() else None,
              "original_sha256": mf.sha256_file(original_path) if original_path and original_path.is_file() else None}
    blockers: list[dict] = []
    checks: dict[str, str] = {}
    resolved_notes: list[dict] = []
    # 复合提取器显式提出的 OCR/题号/分组歧义不能被数量自检抹掉。
    # 已经人工/原卷核对、并附带 resolution 依据的条目可放行，但仍记入报告留痕。
    for issue in structure.get("unresolved") or []:
        if isinstance(issue, dict):
            evidence = issue.get("resolution_evidence") or {}
            bound = (isinstance(evidence, dict) and hashes["source_sha256"] and hashes["original_sha256"]
                     and evidence.get("source_sha256") == hashes["source_sha256"]
                     and evidence.get("original_sha256") == hashes["original_sha256"]
                     and isinstance(evidence.get("evidence"), list)
                     and any(isinstance(ref, str) and ref.strip() for ref in evidence["evidence"]))
            if issue.get("resolved") is True and str(issue.get("resolution") or "").strip() and bound:
                resolved_notes.append({"code": issue.get("code", "EXTRACTION_UNRESOLVED"),
                                       "message": issue.get("message", "提取结果待核验"),
                                       "resolution": issue.get("resolution")})
                continue
            if issue.get("code") == "ANSWER_APPENDIX_DETECTED":
                # 答案册特征（题号+答案字母/解析）是确定性事实：分离为附录候选，不阻断正文切题。
                resolved_notes.append({"code": issue.get("code"),
                                       "message": issue.get("message", "文末答案附录"),
                                       "resolution": "文末重复标题之后按题号+答案字母/解析判定为答案册，保留为答案附录候选"})
                continue
            blockers.append({"code": issue.get("code", "EXTRACTION_UNRESOLVED"),
                             "message": issue.get("message", "提取结果待核验"),
                             "source_line": issue.get("line")})
    checks["extraction_evidence"] = "failed" if blockers else "passed"

    if not raw_text.strip():
        blockers.append({"code": "SOURCE_EMPTY", "message": "正文为空，无法结构化"})
        checks["source_present"] = "failed"
    else:
        checks["source_present"] = "passed"

    if not sections:
        blockers.append({"code": "NO_SECTIONS", "message": "structure.json 缺少 sections，无法拆分题组"})
        checks["sections_present"] = "failed"
        sections = []
    else:
        checks["sections_present"] = "passed"

    for section in sections:
        nums = _section_numbers(section)
        count = section.get("declared_count")
        if count is not None and count != len(nums):
            blockers.append({"code": "QUESTION_COUNT_MISMATCH", "message": f"大题 {section.get('title') or section.get('type')} 声明 {count} 小题，结构定位 {len(nums)} 题"})
    units = _build_units(sections, groups, source_lines) if sections else []

    # 题号覆盖：section 声明的题号应被 units 完整覆盖，无重复
    declared = sorted({n for s in sections for n in _section_numbers(s)})
    unit_numbers = [n for u in units for n in u["numbers"]]
    dup = {n for n in unit_numbers if unit_numbers.count(n) > 1}
    missing = sorted(set(declared) - set(unit_numbers))
    if dup:
        blockers.append({"code": "DUPLICATE_NUMBERS", "message": "题号重复归属：" + ", ".join(map(str, sorted(dup)))})
        checks["coverage"] = "failed"
    elif missing:
        blockers.append({"code": "MISSING_NUMBERS", "message": "题号未覆盖：" + ", ".join(map(str, missing))})
        checks["coverage"] = "failed"
    elif declared:
        checks["coverage"] = "passed"

    # 对照实际原文题号/空位，而不是仅比较结构声明与由声明生成的单元。
    from .preview import _strong_number, _weak_numbers, SECTION
    from .question_content import audit_source_unit
    writing_lines = set()
    for unit in units:
        span = unit.get("source_span") or {}
        if span.get("number_binding") == "single_writing_section":
            writing_lines.update(range(span["start_line"] - 1, span["end_line"]))
    # 显式编号作文的连续内容要点也不是试卷题号；异常序列仍参与对账。
    writing_points = set()
    for section in sections:
        if "书面表达" not in str(section.get("type")):
            continue
        title = _plain(str(section.get("title") or "")).strip()
        starts = [i for i, line in enumerate(source_lines) if title and _plain(line).strip() == title]
        if len(starts) != 1:
            continue
        start = starts[0]
        end = next((i for i in range(start + 1, len(source_lines)) if SECTION.match(_plain(source_lines[i]))), len(source_lines))
        first = next((i for i in range(start + 1, end) if _plain(source_lines[i]).strip()), None)
        if first is None or _strong_number(source_lines[first]) not in _section_numbers(section):
            continue
        if not re.search(r"(?:\bwrite\s+\S.+|请[^\n]*写[^\n]*\S|写一[篇封].+)", _plain(source_lines[first]), re.I):
            continue
        points = [(i, _strong_number(source_lines[i])) for i in range(first + 1, end)
                  if _strong_number(source_lines[i]) is not None]
        if [n for _, n in points] == list(range(1, len(points) + 1)):
            writing_points.update(i for i, _ in points)
    observed = set()
    strong_occurrences = {}
    for index, line in enumerate(source_lines):
        if index in writing_lines or SECTION.match(_plain(line)):
            continue
        n = _strong_number(line)
        if n is not None and index not in writing_points:
            observed.add(n)
            strong_occurrences[n] = strong_occurrences.get(n, 0) + 1
        observed.update(_weak_numbers(line))
    writing_numbers = {n for u in units if u.get("source_span", {}).get("number_binding") == "single_writing_section" for n in u["numbers"]}
    duplicate_source = sorted(n for n, count in strong_occurrences.items() if count > 1)
    if duplicate_source:
        blockers.append({"code": "SOURCE_DUPLICATE_NUMBERS", "message": f"原文实际题号重复：{duplicate_source}"})
    unseen = sorted(set(declared) - observed - writing_numbers)
    extra = sorted(observed - set(declared))
    if unseen:
        blockers.append({"code": "SOURCE_NUMBERS_MISSING", "message": f"结构声明但原文未定位到实际题号/空位：{unseen}"})
    if extra:
        blockers.append({"code": "SOURCE_NUMBERS_UNDECLARED", "message": f"原文实际题号未纳入结构：{extra}"})
    checks["source_number_coverage"] = "failed" if unseen or extra or duplicate_source else "passed"
    content_problems = []
    for unit in units:
        if not unit["stem"].strip():
            content_problems.append({"code": "SOURCE_UNIT_EMPTY", "message": f"源头单元 {unit['numbers']} 内容为空"})
        content_problems.extend(audit_source_unit(unit)["issues"])
    blockers.extend(content_problems)
    checks["source_content"] = "failed" if content_problems else "passed"

    # 导语里的范围也必须被完整定位，不能把缺少第 10 题的 8–10 导语当成 8–9 一组。
    guide_problems = []
    for line in source_lines:
        refs = guide_numbers(line)
        if refs and (not set(refs).issubset(observed) or not any(set(u["numbers"]) == set(refs) for u in units)):
            guide_problems.append({"code": "GUIDE_GROUP_MISMATCH", "message": f"分段导语与实际独立题组不一致：{line.strip()}"})
    blockers.extend(guide_problems)
    checks["group_guides"] = "failed" if guide_problems else "passed"

    # 去噪自检：切片里不应再残留元信息/噪声 行
    residual = []
    if source_lines:
        from .question_content import audit_question
        for number in declared:
            audit = audit_question(source_lines, number)
            for issue in audit.get("issues") or []:
                if issue.get("code") == "IRRELEVANT_HEADER":
                    residual.append(issue)
    if residual:
        for item in residual:
            blockers.append({"code": "RESIDUAL_HEADER", "message": item["message"]})
        checks["denoise"] = "failed"
    elif declared:
        checks["denoise"] = "passed"

    # 组合题自检：声明的 group 必须由合法 section 题号组成
    bad_group = []
    seen_group_numbers = set()
    for group in groups:
        nums = _section_numbers(group)
        overlap = seen_group_numbers.intersection(nums)
        if overlap:
            blockers.append({"code": "DUPLICATE_GROUP_NUMBERS", "message": f"题号重复归属题组：{sorted(overlap)}"})
        seen_group_numbers.update(nums)
        outside = [n for n in nums if n not in declared]
        if outside:
            bad_group.append(outside)
        elif nums and not any(set(nums).issubset(_section_numbers(section)) for section in sections):
            blockers.append({"code": "GROUP_CROSSES_SECTION", "message": f"题组跨越不同大题：{nums}"})
        for section in sections:
            section_nums = _section_numbers(section)
            partitions = source_group_partitions(source_lines, section_nums) if is_group_type(section.get("type")) else []
            if partitions and set(nums).issubset(section_nums) and not any(set(nums) == set(p["questions"]) for p in partitions):
                blockers.append({"code": "GROUP_BOUNDARY_MISMATCH", "message": f"题组 {nums} 与原文材料/分段导语的独立范围不一致"})
    if bad_group:
        blockers.append({"code": "GROUP_OUT_OF_SECTION",
                         "message": "题组题号不属于任何大题：" + str(bad_group)})
        checks["grouping"] = "failed"
    elif groups:
        checks["grouping"] = "passed"

    for conflict in meta.get("conflicts") or []:
        blockers.append({"code": "SOURCE_METADATA_CONFLICT", "message": str(conflict)})
    metadata_missing = [key for key in ("source", "source_type", "year", "region") if not meta.get(key)]
    if original_path and original_path.is_file() and metadata_missing:
        blockers.append({"code": "SOURCE_METADATA_MISSING", "message": f"原卷来源字段尚未可靠定位：{metadata_missing}；需复核原卷卷头"})
    from .source_answer_only import detect_source_answer_only
    raw_path = batch_dir / "01_source" / "原始正文.txt"
    source_diagnostic = detect_source_answer_only(_read_text(raw_path)) if raw_path.is_file() else None
    if source_diagnostic is not None:
        source_diagnostic["source_sha256"] = mf.sha256_file(raw_path)
        if source_diagnostic["classification"] == "source_answer_only":
            blockers.append({"code": "SOURCE_ANSWER_ONLY", "message": source_diagnostic["message"]})
            checks["source_paper_presence"] = "failed"
    # Legacy candidates can omit the extractor's inference issue entirely.
    # A section-bound unprinted number still requires independent proof.
    bound_writing = [u for u in units if u.get("source_span", {}).get("number_binding") == "single_writing_section"]
    for section in sections:
        if "书面表达" not in str(section.get("type")):
            continue
        nums = _section_numbers(section)
        title = _plain(str(section.get("title") or "")).strip()
        starts = [i for i, line in enumerate(source_lines) if title and _plain(line).strip() == title]
        first = next((_plain(line).strip() for line in source_lines[starts[0] + 1:] if _plain(line).strip()), "") if len(starts) == 1 else ""
        explicit = (len(nums) == 1 and _strong_number(first) == nums[0]
                    and bool(re.search(r"(?:\bwrite\s+\S.+|请[^\n]*写[^\n]*\S|写一[篇封].+)", first, re.I)))
        if not explicit and not any(u["numbers"] == nums for u in bound_writing):
            blockers.append({"code": "WRITING_NUMBER_UNBOUND", "message": "作文题号未可靠定位，须核对原卷及节边界"})
    inferred = [x for x in structure.get("unresolved") or []
                if isinstance(x, dict) and x.get("code") == "QUESTION_NUMBER_INFERRED"]
    if bound_writing and not inferred:
        writing_sections = [s for s in sections if "书面表达" in str(s.get("type"))]
        inferred = [{"code": "QUESTION_NUMBER_INFERRED",
                     "line": writing_sections[0].get("source_line") if len(writing_sections) == 1 else None,
                     "message": "作文没有可核实的印刷题号，顺延编号须核对原卷"}]
        blockers.append({"code": "QUESTION_NUMBER_INFERRED", "message": inferred[0]["message"],
                         "source_line": inferred[0]["line"]})
        checks["extraction_evidence"] = "failed"
    # Resolve only this one deterministic ambiguity after all independent gate checks pass.
    if len(blockers) == 1 and blockers[0]["code"] == "QUESTION_NUMBER_INFERRED" and len(inferred) == 1:
        note = _prove_single_writing_number(original_path, cleaned_text, sections, inferred[0], hashes)
        if note and mf.sha256_file(source_path) == hashes["source_sha256"]:
            resolved_notes.append(note)
            blockers.clear()
            checks["extraction_evidence"] = "passed"
    # A locally scoped candidate is not a resolution or gate approval.
    # Do not localize an inference while any other structural issue remains.
    if len(blockers) == 1 and blockers[0].get("code") == "QUESTION_NUMBER_INFERRED" and len(inferred) == 1:
        from .preview import SECTION
        writing_sections = [s for s in sections if "书面表达" in str(s.get("type"))]
        section = writing_sections[0] if len(writing_sections) == 1 else None
        line = blockers[0].get("source_line")
        raw_lines = raw_text.splitlines()
        headings = [i for i, text in enumerate(raw_lines) if SECTION.match(_plain(text))]
        nums = _section_numbers(section) if section else []
        matched = [u for u in units if u.get("numbers") == nums]
        if (section is not None and sections[-1] is section and type(line) is int
                and 1 <= line <= len(raw_lines) and headings and headings[-1] == line - 1
                and _plain(raw_lines[line - 1]).strip() == _plain(str(section.get("title") or "")).strip()
                and re.findall(r"共\s*(\d+)\s*小题", _plain(raw_lines[line - 1])) == ["1"]
                and line == section.get("source_line") and len(nums) == 1 and nums[0] > 0
                and len(matched) == 1 and matched[0].get("unit_kind") == "standalone"
                and matched[0].get("source_span", {}).get("number_binding") == "single_writing_section"
                and all(value != "failed" for key, value in checks.items() if key != "extraction_evidence")
                and _original_matches_cleaned(original_path, cleaned_text, hashes["original_sha256"])):
            blockers[0]["affected_numbers"] = nums
    layout_required = ("[IMG " in raw_text or "[TABLE" in raw_text
                       or any(issue.get("code") in {"OCR_VISUAL_REVIEW", "EXTRACTION_WARNING"} for issue in blockers))
    checks["grouping"] = "failed" if any("GROUP" in b["code"] for b in blockers) else "passed"
    passed = not blockers and bool(units)

    confirmed = {
        "version": "1.0",
        "meta": meta,
        "units": units,
        "image_questions": image_questions,
        "removed_lines": removed,
        "source_sha256_note": "以 01_source/正文.txt 去噪后为准",
    }
    report = {
        "passed": passed,
        "input_hashes": {"01_source/正文.txt": hashes["source_sha256"],
                         "01_source/structure.json": hashes["structure_sha256"]},
        **hashes,
        "metadata_missing": metadata_missing,
        "source_diagnostic": source_diagnostic,
        "layout_required": layout_required,
        "layout_review_command": "python -m workflow.layout_review --help" if layout_required else None,
        "checks": checks,
        "blockers": blockers,
        "unit_counts": {
            "total": len(units),
            "standalone": sum(1 for u in units if u["unit_kind"] == "standalone"),
            "group": sum(1 for u in units if u["unit_kind"] == "group"),
        },
        "removed_count": len(removed),
        "resolved_evidence": resolved_notes,
        "meta": meta,
    }
    output_root = Path(output_dir) if output_dir is not None else batch_dir
    confirmed_path = output_root / "01_source" / "structure.confirmed.json"
    _write_json(confirmed_path, confirmed)
    report["confirmed_sha256"] = mf.sha256_file(confirmed_path)
    report["input_hashes"]["01_source/structure.confirmed.json"] = report["confirmed_sha256"]
    _write_json(output_root / "07_review" / "structure-gate.json", report)
    return report


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
