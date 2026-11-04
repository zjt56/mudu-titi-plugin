# -*- coding: utf-8 -*-
"""Read-only gate for extracted structure; not a final-batch validator.

Each review is bound to exact structure and source bytes. Reviews must be made
against the source (including the original visual document where needed): this
module does not infer semantic or visual correctness from extracted text.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from .question_content import audit_question

CHECKS = {
    "content_complete": "题目内容完整",
    "stem_matches": "题干与题目匹配",
    "questions_options_complete": "缺题漏题及选项完整",
    "alignment": "错行及上下题错位",
    "images": "图片完整且正确引用",
    "grouping": "按题型结构批次合并",
    "content_scope": "逐题内容范围及媒体/表格完整",
}


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 0 < value <= 10000:
        return value
    if isinstance(value, str) and value.isdecimal() and 0 < int(value) <= 10000:
        return int(value)
    return None


def _expand(entries, *, ranges):
    if not isinstance(entries, list) or not entries:
        return [], ["题号列表缺失或为空"]
    numbers, errors = [], []
    for entry in entries:
        if ranges and isinstance(entry, list) and len(entry) == 2:
            start, end = map(_number, entry)
            if start is not None and end is not None and start <= end and end - start <= 10000:
                numbers.extend(range(start, end + 1))
                continue
        number = _number(entry)
        if number is None:
            errors.append(f"无法识别的题号或区间: {entry!r}")
        else:
            numbers.append(number)
    return numbers, errors


def inspect(structure_bytes: bytes, source_bytes: bytes, review: dict | None = None,
            original_bytes: bytes | None = None, media_dir: str | Path | None = None) -> dict:
    """Return a blocking report, without changing any input.

    review: {structure_sha256, source_sha256, original_sha256, items: {"1": {check_key:
      {status: "passed"|"failed"|"unchecked", reviewer: str,
       evidence: [str, ...], reason: str}}}, coverage: {status: "passed",
       reviewer: str, evidence: [str, ...]}}. Coverage attests the whole original
    has been checked for omitted questions. Evidence should locate source
    and extraction; visual checks must identify the original page/image.
    An unchecked or unbound review never passes. The review is an attestation,
    not an automated proof that its evidence is true.
    """
    hashes = {"structure_sha256": _hash(structure_bytes), "source_sha256": _hash(source_bytes),
              "original_sha256": _hash(original_bytes) if original_bytes else None}
    issues = []
    try:
        structure = json.loads(structure_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        structure = None
        issues.append(f"结构 JSON 无法读取: {exc}")
    if not isinstance(structure, dict):
        structure = {}
        issues.append("结构必须是对象")
    sections = structure.get("sections")
    counts = Counter()
    locations = {}
    if not isinstance(sections, list) or not sections:
        issues.append("sections 缺失或为空")
        sections = []
    for index, section in enumerate(sections):
        path = f"sections[{index}]"
        if not isinstance(section, dict):
            issues.append(f"{path} 不是对象")
            continue
        nums, errors = _expand(section.get("questions"), ranges=True)
        issues.extend(f"{path}.questions: {error}" for error in errors)
        if not isinstance(section.get("type"), str) or not section["type"].strip():
            issues.append(f"{path}.type 缺失")
        for n in nums:
            counts[n] += 1
            locations.setdefault(n, []).append(path)
    groups = structure.get("groups", [])
    if not isinstance(groups, list):
        issues.append("groups 不是列表")
        groups = []
    memberships = Counter()
    for index, group in enumerate(groups):
        if not isinstance(group, dict):
            issues.append(f"groups[{index}] 不是对象")
            continue
        nums, errors = _expand(group.get("questions"), ranges=False)
        issues.extend(f"groups[{index}].questions: {error}" for error in errors)
        for n in nums:
            memberships[n] += 1
            if n not in counts:
                issues.append(f"groups[{index}] 的题号 {n} 不属于任何 section")
    images = structure.get("image_questions", [])
    if not isinstance(images, list):
        issues.append("image_questions 不是列表")
    elif images:
        nums, errors = _expand(images, ranges=False)
        issues.extend(f"image_questions: {error}" for error in errors)
        for n in nums:
            if n not in counts:
                issues.append(f"image_questions 的题号 {n} 不属于任何 section")
    if not source_bytes.strip():
        issues.append("提取正文为空")
    review = review if isinstance(review, dict) else {}
    bound = bool(original_bytes) and all(review.get(key) == value for key, value in hashes.items())
    items = review.get("items") if isinstance(review.get("items"), dict) else {}
    coverage = review.get("coverage") if isinstance(review.get("coverage"), dict) else {}
    evidence = coverage.get("evidence")
    coverage_ok = (bound and coverage.get("status") == "passed"
                   and isinstance(coverage.get("reviewer"), str) and bool(coverage["reviewer"].strip())
                   and isinstance(evidence, list) and bool(evidence)
                   and all(isinstance(e, str) and e.strip() for e in evidence))
    if not coverage_ok:
        issues.append("整卷题号覆盖未经绑定原卷和结构的人工核对，可能漏题")
    results = []
    try:
        source_lines = source_bytes.decode("utf-8-sig", errors="replace").splitlines()
    except Exception:
        source_lines = []
    for number in sorted(counts):
        per_question = items.get(str(number))
        per_question = per_question if isinstance(per_question, dict) else {}
        checks = {}
        item_audit = audit_question(source_lines, number, media_dir=media_dir)
        for key, label in CHECKS.items():
            raw = per_question.get(key)
            raw = raw if isinstance(raw, dict) else {}
            evidence = raw.get("evidence")
            valid_evidence = isinstance(evidence, list) and bool(evidence) and all(
                isinstance(e, str) and e.strip() for e in evidence)
            reviewer = raw.get("reviewer")
            valid = bound and valid_evidence and isinstance(reviewer, str) and bool(reviewer.strip())
            status = raw.get("status") if valid else "unchecked"
            if status not in ("passed", "failed", "unchecked"):
                status = "unchecked"
            reason = raw.get("reason") if isinstance(raw.get("reason"), str) else ""
            if status == "failed" and not reason.strip():
                reason = "核对发现问题，需补充具体返工原因"
            if status == "unchecked":
                reason = "缺少与当前输入哈希绑定的核对人或证据，或无法判断；需人工复核"
            if key == "content_scope" and item_audit["visual_review_required"] and not valid:
                status, reason = "unchecked", "含图片或表格，缺少绑定原卷的逐题视觉核对证据"
            machine_issues = item_audit["issues"] if key == "content_scope" else []
            if key == "questions_options_complete" and counts[number] > 1:
                status, reason = "failed", "题号在结构中重复归属"
            if key == "grouping" and memberships[number] > 1:
                status, reason = "failed", "题号重复归属题组"
            checks[key] = {"label": label, "status": status, "reason": reason,
                           "evidence": evidence if valid_evidence else [],
                           "reviewer": reviewer if isinstance(reviewer, str) else "",
                            "machine_issues": machine_issues}
        rework = [{"check": key, **value} for key, value in checks.items() if value["status"] != "passed"]
        for issue in item_audit["issues"]:
            rework.append({"check": "content_scope", "status": "failed", "reason": issue["message"],
                           "evidence": [], "reviewer": "", "machine_issue": issue})
        results.append({"number": number, "locations": locations[number], "checks": checks,
                        "content_audit": item_audit, "rework": rework, "passed": not rework})
    return {"passed": bool(results) and not issues and all(q["passed"] for q in results),
            "hashes": hashes, "review_bound": bound, "issues": issues, "questions": results}


def inspect_files(structure_path, source_path, review_path=None, original_path=None,
                  media_dir=None) -> dict:
    """Read input files and return a report; never write or modify them."""
    structure_bytes = Path(structure_path).read_bytes()
    source_bytes = Path(source_path).read_bytes()
    review = None
    if review_path is not None:
        review = json.loads(Path(review_path).read_text(encoding="utf-8-sig"))
    original_bytes = Path(original_path).read_bytes() if original_path is not None else None
    return inspect(structure_bytes, source_bytes, review, original_bytes, media_dir)
