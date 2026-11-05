# -*- coding: utf-8 -*-
"""审核放行判断：把 07_review/ 的文件收敛成一个明确的通过/阻断结论。

规则（来自 references/review-workflow.md）：
1. 阻断项必须为零。
2. 人工审核队列必须为空。
3. 必检项不能停留在 unchecked。
4. 内容审核与标签审核都要有结论。

本模块只读审核产物，不修改题目、不接受 AI 候选、不上传。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROUND_FILE = "review-rounds.json"
# 两轮审核：一审全量盲判 + 二审 20% 小题抽样复核（不再单独跑全量知识点轮）。
ROUND_IDS = ("labels", "sample")
ROUND_PROTOCOL = "upload-workflow.review-rounds/v3"
ROUND_INPUTS = (
    "01_source/正文.txt", "01_source/structure.confirmed.json",
    "03_types/types.json", "04_knowledge/knowledge.json",
    "06_batch/batch.json", "06_batch/batch_meta.json", "04_knowledge/explanations.json",
    "01_source/附录.txt", "01_source_ans/正文.txt", "01_source_ans/answers.json",
    "01_source/extraction.json", "01_source/structure.json", "01_source/metadata.json", "02_audio/audio_map.json", "02_audio/segment-evidence.json",
    "02_audio/full-transcript.json", "workflow/media-observations.json",
    "07_review/structure-gate.json", "07_review/validation-report.json",
)
ROUND_RULES = ("references/filter-framework.json", "references/knowledge-catalog.json",
               "references/type-knowledge-map.json", "skills-reference/knowledge-matching-rules.md",
               "skills-reference/README.md", "skills-reference/constraints.md",
               "skills-reference/lessons-learned.md", "skills-reference/type-taxonomy.md",
               "skills-reference/knowledge-catalog.md", "skills-reference/type-knowledge-map.md",
               "docs/knowledge-tagging.md")

REVIEW_DIR = "07_review"

RESOLVED = {"reviewed", "resolved", "accepted", "fixed", "closed", "approved", "passed", "waived"}
OPEN = {"open", "pending", "needs_review", "blocked", "proposed", "unchecked", "hold"}


def compact_knowledge_catalog(types, catalog=None):
    """Filter only when every type has a usable mapping; otherwise keep everything."""
    from workbench import taxonomy
    if catalog is None:
        catalog = _load(taxonomy.KNOWLEDGE_PATH)
    if not isinstance(catalog, dict) or not isinstance(catalog.get("points"), list):
        return catalog
    result = dict(catalog)
    rules = taxonomy.type_knowledge_rules()
    if not types or any(not isinstance(ty, str) or ty not in rules
                        or not taxonomy._allowed_prefixes(ty) for ty in types):
        return result
    prefixes = [p for ty in types for p in taxonomy._allowed_prefixes(ty)]
    result["points"] = [point for point in catalog["points"]
                        if not isinstance(point, dict) or not isinstance(point.get("path"), str)
                        or any(taxonomy._under(point["path"], pre) for pre in prefixes)]
    return result


def _load(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {"__parse_error__": True}


def _findings(payload) -> list:
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("findings", "items", "issues", "results"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def _is_open(item: dict) -> bool:
    status = str(item.get("status", "")).strip().lower()
    if status in RESOLVED:
        return False
    if status in OPEN:
        return True
    return status == "" or status not in RESOLVED


def evaluate(batch_dir) -> dict:
    batch_dir = Path(batch_dir)
    review_dir = batch_dir / REVIEW_DIR
    content = _load(review_dir / "content-review.json")
    metadata = _load(review_dir / "metadata-review.json")
    queue = _load(review_dir / "manual-review-queue.json")
    unchecked = _load(review_dir / "unchecked.json")

    errors: list[dict] = []
    warnings: list[dict] = []

    def parsed_error(name, payload):
        if isinstance(payload, dict) and payload.get("__parse_error__"):
            errors.append({"code": "REVIEW_FILE_UNREADABLE", "file": name,
                           "message": "审核文件不是合法 JSON，无法判断放行"})

    parsed_error("content-review.json", content)
    parsed_error("metadata-review.json", metadata)
    parsed_error("manual-review-queue.json", queue)

    blocking_findings = []
    for name, payload in (("content-review.json", content), ("metadata-review.json", metadata)):
        for finding in _findings(payload):
            if not _is_open(finding):
                continue
            blocking = finding.get("blocking") is True
            severity = str(finding.get("severity", "")).lower()
            if blocking or severity in ("blocker", "major"):
                blocking_findings.append({
                    "file": name,
                    "finding_id": finding.get("finding_id", ""),
                    "unit_id": finding.get("unit_id", ""),
                    "field": finding.get("field", ""),
                    "severity": severity or ("blocker" if blocking else "major"),
                    "reason": finding.get("reason") or finding.get("required_action") or "",
                })

    for finding in blocking_findings:
        errors.append({"code": "REVIEW_FINDING_OPEN", **finding})

    queue_items = _findings(queue) if queue is not None else None
    if queue is None:
        errors.append({"code": "MANUAL_QUEUE_MISSING",
                       "message": "缺少 07_review/manual-review-queue.json，无法证明人工审核已完成"})
    else:
        open_items = [it for it in (queue_items or []) if _is_open(it)]
        for item in open_items:
            errors.append({
                "code": "MANUAL_REVIEW_OPEN",
                "finding_id": item.get("finding_id", ""),
                "unit_id": item.get("unit_id", ""),
                "field": item.get("field", ""),
                "message": item.get("question") or item.get("reason") or "待人工决定",
            })

    unchecked_items = unchecked if isinstance(unchecked, list) else _findings(unchecked)
    required_unchecked = [u for u in unchecked_items
                          if isinstance(u, dict) and (u.get("required") is True
                                                      or str(u.get("severity", "")).lower() in ("blocker", "major"))]
    for item in required_unchecked:
        errors.append({
            "code": "REQUIRED_CHECK_UNCHECKED",
            "unit_id": item.get("unit_id", ""),
            "field": item.get("field", ""),
            "message": item.get("reason") or "必检项未完成",
        })

    for name, payload in (("content-review.json", content), ("metadata-review.json", metadata)):
        if payload is None:
            errors.append({"code": "REVIEW_MISSING", "file": name,
                           "message": "缺少审核结论文件"})
        elif not _findings(payload):
            warnings.append({"code": "REVIEW_EMPTY", "file": name,
                             "message": "没有记录问题；完成情况由绑定当前输入的分轮记录判断"})

    rounds = evaluate_rounds(batch_dir)
    _counts_blocking = len(blocking_findings)
    _counts_open = len([it for it in (queue_items or []) if _is_open(it)])
    _counts_required = len(required_unchecked)
    # Round findings are materialised into the review files above, so the same
    # issue can appear both as a file finding and as a round error. Count each
    # finding once: drop file/queue errors whose finding_id the round already
    # reported.
    def _round_sig(e):
        return (e.get("unit_id"), e.get("field"),
                e.get("reason") or e.get("message") or "")
    round_sigs = {_round_sig(e) for e in rounds["errors"]
                  if e.get("code") == "REVIEW_ROUND_FINDING"}
    if round_sigs:
        def _file_sig(f):
            return (f.get("unit_id"), f.get("field"),
                    f.get("reason") or f.get("message") or "")
        errors = [e for e in errors
                  if e.get("code") not in ("REVIEW_FINDING_OPEN", "MANUAL_REVIEW_OPEN")
                  or _file_sig(e) not in round_sigs]
        blocking_findings = [b for b in blocking_findings if _file_sig(b) not in round_sigs]
        if queue is not None:
            queue_items = [it for it in (queue_items or [])
                           if _file_sig(it) not in round_sigs]
    # Deduplicate unchecked items that the round evaluation already reports
    # under a more specific code (same unit and field).
    round_typed = {(e.get("unit_id"), e.get("field")) for e in rounds["errors"]
                   if e.get("code") == "REVIEW_SAMPLE_SOURCE_UNVERIFIED"}
    if round_typed:
        errors = [e for e in errors
                  if e.get("code") != "REQUIRED_CHECK_UNCHECKED"
                  or (e.get("unit_id"), e.get("field")) not in round_typed]
    errors.extend(rounds["errors"])
    passed = not errors
    return {
        "rounds": rounds,
        "passed": passed,
        "batch_dir": str(batch_dir),
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "blocking_findings": _counts_blocking,
            "open_manual_items": _counts_open,
            "required_unchecked": _counts_required,
        },
        "rule": "阻断项=0 且 人工队列=0 且 必检项无 unchecked",
    }


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _quote_matches(quote: str, text: str) -> bool:
    """真实原文片段匹配：容忍空白/全半角标点差异，但不接受编造内容。"""
    if quote and quote in text:
        return True
    def norm(s):
        s = re.sub(r"[\s\u3000]+", "", s or "")
        s = s.translate(str.maketrans("（）：，。；！？、“”‘’【】", "():,.;!?,\"\"''[]"))
        return s.casefold()
    q = norm(quote)
    return bool(q) and q in norm(text)


_EN_PREFIX_ZH = (
    ("What the question tests:", "本题考点："),
    ("Answer-to-point relation:", "答案与考点关系："),
    ("Group/theme:", "组级/主题："),
    ("Core issue:", "核心问题："),
    ("Mechanical exact source mismatch:", "原文精确匹配不符："),
    ("Mechanical explicit section-boundary spill:", "解析越界到其他题/段落："),
    ("Mechanical missing merge metadata:", "缺少表格合并属性："),
    ("canonical", "规范值"),
)


def _zh_reason(text: str) -> str:
    """把审核发现的英文模板前缀替换为中文，减少阅读障碍；不影响中文内容。"""
    out = str(text or "")
    for en, zh in _EN_PREFIX_ZH:
        out = out.replace(en, zh)
    return out.strip()


def prepare_round_plan(batch_dir) -> dict:
    """Bind actual inputs and a reproducible 20% *question* sample; never review.

    No identity/signature is needed. Hashes detect stale input, not review quality.
    The caller must run three separate semantic tasks against this same plan.
    """
    from .agent_tasks import confirmed_units
    batch = Path(batch_dir)
    units = confirmed_units(batch)
    required = ROUND_INPUTS[:4]
    missing = [p for p in required if not (batch / p).is_file()]
    if not any((batch / p).is_file() for p in ROUND_INPUTS[4:6]):
        missing.append("06_batch/batch.json or batch_meta.json")
    if missing:
        raise ValueError("Missing round inputs: " + ", ".join(missing))
    root = Path(__file__).resolve().parents[1]
    hashes = {}
    for base, paths, prefix in ((batch, ROUND_INPUTS, ""), (root, ROUND_RULES, "rules:")):
        for relative in paths:
            path = base / relative
            if path.is_symlink():
                raise ValueError("Round inputs cannot be symlinks: " + relative)
            if path.is_file():
                hashes[prefix + relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    observations = _load(batch / "workflow/media-observations.json") or {}
    from .agent_tasks import _safe_path
    for item in observations.get("observations", []):
        relative = item["path"]
        path = _safe_path(batch, relative)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != item["sha256"]:
            raise ValueError("Media observation is stale: " + relative)
        hashes[relative] = digest
    questions = [{"unit_id": u["unit_id"], "number": str(n)} for u in units for n in u["numbers"]]
    binding = _digest({"input_hashes": hashes, "questions": questions})
    count = (len(questions) + 4) // 5
    # Fixed by inputs, not by a model choosing convenient questions. This is not
    # a secret/random sample or the legacy HMAC independent-review mechanism.
    sample = sorted(questions, key=lambda q: _digest([binding, q]))[:count]
    return {"protocol": ROUND_PROTOCOL, "input_binding": binding, "input_hashes": hashes,
            "units": [u["unit_id"] for u in units], "questions": questions,
            "sample": sample, "sample_method": "sha256-input-binding-question-order/v1"}


def _round_issue(round_id, reason):
    return {"code": "REVIEW_ROUND_UNCHECKED", "round_id": round_id,
            "field": "review_rounds." + round_id, "required": True,
            "status": "unchecked", "message": reason, "reason": reason}



def _short_paths(value):
    if isinstance(value, list):
        return "、".join(str(v).split("/")[-1] for v in value) or "空"
    return str(value)


def _label_changes(old, new):
    changes = []
    if old.get("type") != new.get("type"):
        changes.append(f"题型：{old.get('type')} → {new.get('type')}")
    if old.get("subtypes") != new.get("subtypes"):
        changes.append(f"子类：{_short_paths(old.get('subtypes'))} → {_short_paths(new.get('subtypes'))}")
    return changes


def _knowledge_changes(old, assessment):
    changes = []
    if old.get("knowledge") != assessment.get("knowledge"):
        changes.append(f"知识点：{_short_paths(old.get('knowledge'))} → {_short_paths(assessment.get('knowledge'))}")
    prior = {str(c["number"]): c.get("knowledge") for c in old.get("subquestions", [])}
    current = {str(c["number"]): c.get("knowledge") for c in assessment.get("subquestions", [])}
    for number in dict.fromkeys((*prior, *current)):
        if number not in prior or number not in current or prior[number] != current[number]:
            changes.append(f"第{number}题知识点：{_short_paths(prior.get(number))} → {_short_paths(current.get(number))}")
    return changes

def _sample_source_binding(batch, plan, check):
    """Derive target-level source attribution without changing model output."""
    from .agent_tasks import confirmed_units
    from .review_child_source import (prove_child, quote_belongs_to_child,
                                      quote_belongs_to_child_loose,
                                      quote_belongs_to_unit_material)
    source_path = Path(batch) / '01_source/正文.txt'
    raw = source_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    expected = plan.get('input_hashes', {}).get('01_source/正文.txt')
    if not expected or digest != expected:
        return {'status': 'uncertain', 'reason': '正文摘要与审核计划不一致',
                'source_path': '01_source/正文.txt', 'source_sha256': digest}
    text = raw.decode('utf-8-sig')
    unit = next((u for u in confirmed_units(batch) if u['unit_id'] == check.get('unit_id')), {})
    number = check.get('number')
    proof = prove_child(text, unit, number, source_sha256=digest)
    if proof['status'] == 'proven':
        quotes = [item.get('quote') for item in check.get('evidence', [])
                  if isinstance(item, dict) and item.get('path') == '01_source/正文.txt']
        methods = []
        for quote in quotes:
            if quote_belongs_to_child(text, unit, number, quote):
                methods.append('child-verbatim')
            elif quote_belongs_to_child_loose(text, unit, number, quote):
                methods.append('child-normalized')
            elif quote_belongs_to_unit_material(text, unit, quote):
                methods.append('group-material-verbatim')
        if not methods:
            proof = dict(proof, status='uncertain', reason='原文引证未唯一归属于目标小题或同组材料')
        else:
            proof = dict(proof, evidence_method=methods[0])
    return proof


def _sample_number(check):
    """Resolve a sample target number, tolerating a model that puts it only in
    assessment.difficulty (a single number) instead of the check top level."""
    number = check.get("number")
    if number is not None and str(number).strip():
        return str(number)
    assessment = check.get("assessment") if isinstance(check.get("assessment"), dict) else {}
    difficulty = assessment.get("difficulty")
    if isinstance(difficulty, list):
        numbers = {str(item.get("number")) for item in difficulty
                   if isinstance(item, dict) and item.get("number") is not None}
        if len(numbers) == 1:
            return numbers.pop()
    return None


class RoundCheckError(ValueError):
    """Round checks failed validation; ``issues`` lists the offending targets."""

    def __init__(self, message, issues=None):
        super().__init__(message)
        self.issues = list(issues or [])


def _localize_quote(quote, text, *, min_ratio=0.92):
    """Return an exact source line/window for an almost-verbatim quote.

    Only accepts a window whose similarity to the quote is at least min_ratio,
    so paraphrases are never silently accepted as evidence. The returned value
    is copied verbatim from the bound text.
    """
    if not isinstance(quote, str) or not isinstance(text, str) or not quote.strip():
        return None
    import difflib
    lines = text.splitlines()
    best, best_ratio = None, 0.0
    candidates = [[line] for line in lines if line.strip()]
    candidates += [[lines[i], lines[i + 1]] for i in range(len(lines) - 1)
                   if lines[i].strip() or lines[i + 1].strip()]
    for window in candidates:
        candidate = "\n".join(window)
        ratio = difflib.SequenceMatcher(None, quote, candidate, autojunk=False).ratio()
        if ratio > best_ratio:
            best, best_ratio = candidate, ratio
    if best is None or best_ratio < min_ratio:
        return None
    return best if best != quote else None


def _normalize_result_quotes(batch, result):
    """Replace near-verbatim quotes with the exact bound substring.

    Deterministic and conservative: only quotes whose best source window is at
    least 92% similar are rewritten, and the original is retained in
    ``model_quote`` for audit. Paraphrased quotes stay untouched so validation
    still fails closed.
    """
    import copy
    checks = ((result.get("artifacts") or {}).get("review_round") or {}).get("checks")
    if not isinstance(checks, list):
        return result
    out = copy.deepcopy(result)
    for check in out["artifacts"]["review_round"]["checks"]:
        if not isinstance(check, dict):
            continue
        for evidence in check.get("evidence") or []:
            if not isinstance(evidence, dict):
                continue
            quote, path = evidence.get("quote"), evidence.get("path")
            if not isinstance(quote, str) or not isinstance(path, str):
                continue
            try:
                target = batch / path
                if not target.is_file():
                    continue
                text = target.read_text(encoding="utf-8-sig")
            except (OSError, ValueError):
                continue
            if _quote_matches(quote, text):
                continue
            localized = _localize_quote(quote, text)
            if localized:
                evidence["model_quote"] = quote
                evidence["quote"] = localized
                evidence["normalized"] = True
    return out


def _round_checks(record, round_id, plan, batch, lenient=False, require_coverage=False):
    """收集每轮逐题判断，把与原标注的差异/问题转成人工 finding。

    每个目标必须有且仅有一条有效判断和可核验的原文引证。
    格式允许额外字段，但缺失判断、证据或比较结果不能记为已审核。
    """
    required_record = {"round_id", "input_binding", "execution_id", "checks"}
    if not isinstance(record, dict) or not required_record <= set(record) or set(record) - required_record - {"parts"}:
        raise ValueError("Each round needs round_id/input_binding/execution_id/checks and optional parts")
    if record["round_id"] != round_id or record["input_binding"] != plan["input_binding"]:
        raise ValueError("Round input binding mismatch")
    if not isinstance(record["execution_id"], str) or not record["execution_id"].strip():
        raise ValueError("Separate round execution_id required (not a human name)")
    checks = record["checks"]
    if not isinstance(checks, list):
        raise ValueError("Round checks must be a list")
    expected = ([(q["unit_id"], q["number"]) for q in plan["sample"]] if round_id == "sample"
                else [(uid, None) for uid in plan["units"]])
    expected_set = set(expected)
    if not expected or len(expected_set) != len(expected):
        raise ValueError("Review plan must have distinct nonempty targets")
    covered = set()
    source_path = batch / "01_source/正文.txt"
    source_text = source_path.read_text(encoding="utf-8-sig")
    from .agent_tasks import confirmed_units
    units = {u["unit_id"]: u for u in confirmed_units(batch)}
    for check in checks:
        if not isinstance(check, dict):
            raise ValueError("Every target needs a review judgment")
        uid = check.get("unit_id")
        number = _sample_number(check) if round_id == "sample" else None
        if round_id == "sample" and number is not None:
            # Normalise the resolved target number so every later reader
            # (coverage, source binding, consensus) sees a consistent value.
            check["number"] = number
        key = (uid, str(number) if number is not None else None)
        try:
            if key not in expected_set or key in covered:
                raise ValueError("Review checks must cover each target exactly once")
            covered.add(key)
        except TypeError:
            raise ValueError("Invalid review target") from None
        if check.get("verdict") not in ("ok", "error", "uncertain"):
            raise ValueError("Every target needs a valid verdict")
        if not isinstance(check.get("rationale"), str) or not check["rationale"].strip():
            raise ValueError("Every target needs a review rationale")
        evidence = check.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("Every target needs verifiable source evidence")
        unit = units.get(uid) or {}
        unit_text = unit.get("stem")
        if not isinstance(unit_text, str) or not unit_text.strip():
            raise ValueError("Target has no confirmed source text; review remains unchecked")
        has_source = False
        for item in evidence:
            if not isinstance(item, dict) or any(
                not isinstance(item.get(k), str) or not item[k].strip()
                for k in ("path", "locator", "quote")
            ):
                raise ValueError("Evidence needs a path, locator and quote")
            path = item["path"]
            if path not in plan["input_hashes"] or path.startswith("rules:"):
                raise ValueError("Evidence must cite a bound batch input")
            text = source_text if path == "01_source/正文.txt" else (batch / path).read_text(encoding="utf-8-sig")
            if not _quote_matches(item["quote"], text):
                raise ValueError("Evidence quote absent from bound input")
            if path == "01_source/正文.txt":
                # Bare numbers and punctuation cannot locate a substantive claim.
                quote = item["quote"].strip()
                meaningful = bool(re.search(r"[^\W\d_]", quote, re.UNICODE))
                has_source |= meaningful and _quote_matches(quote, unit_text)
        if not has_source:
            raise ValueError("Every target needs verifiable source evidence")
        assessment = check.get("assessment")
        if "assessment" in check and assessment is None and check.get("verdict") == "uncertain":
            # Preserve uncertainty as a human finding without guessing labels.
            continue
        if not isinstance(assessment, dict):
            raise ValueError("Every certain target needs a comparison assessment")
        def valid_strings(value):
            return isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value)
        if (not isinstance(assessment.get("type"), str)
                or not assessment["type"].strip() or not valid_strings(assessment.get("subtypes"))):
            raise ValueError("Every review needs type and subtypes")
        if True:
            difficulty = assessment.get("difficulty")
            expected_numbers = ({str(number)} if round_id == "sample" else
                                {str(q["number"]) for q in plan["questions"] if q["unit_id"] == uid})
            if (not isinstance(difficulty, list) or not expected_numbers
                    or any(not isinstance(item, dict) or set(item) != {"number", "value"}
                           or not isinstance(item["number"], str)
                           or item["value"] not in ("容易", "适中", "困难") for item in difficulty)):
                raise ValueError("Label review needs independent difficulty for every question")
            numbers = [item["number"] for item in difficulty]
            if len(numbers) != len(set(numbers)) or set(numbers) != expected_numbers:
                raise ValueError("Difficulty assessment must cover each question exactly once")
        if (not valid_strings(assessment.get("knowledge"))
                or not assessment["knowledge"] or not isinstance(assessment.get("subquestions"), list)):
            raise ValueError("Every review needs knowledge and subquestions")
        if True:
            children = assessment["subquestions"]
            if any(not isinstance(c, dict) or c.get("number") is None
                   or not valid_strings(c.get("knowledge")) for c in children):
                raise ValueError("Knowledge subquestions need numbers and knowledge")
            numbers = [str(c["number"]) for c in children]
            if len(numbers) != len(set(numbers)):
                raise ValueError("Duplicate knowledge subquestion")
            expected_children = expected_numbers if unit.get("unit_kind") == "group" else set()
            if set(numbers) != expected_children:
                raise ValueError("Knowledge subquestions must cover the selected children exactly")
            from workbench import taxonomy
            labels = {"type": assessment["type"], "subtypes": assessment["subtypes"],
                      "knowledge": assessment["knowledge"], "subquestions": children,
                      "unit_kind": unit.get("unit_kind")}
            problems = taxonomy.check_question_labels(labels)
            if problems:
                raise ValueError("Invalid review labels: " + "; ".join(problems))
        if round_id == "sample":
            comparison_text = assessment.get("source_comparison")
            if not (isinstance(comparison_text, str) and comparison_text.strip()):
                # The field is also accepted at the check top level: models
                # sometimes place it beside unit_id instead of inside assessment.
                comparison_text = check.get("source_comparison")
            if not (isinstance(comparison_text, str) and comparison_text.strip()):
                raise ValueError("Sample review needs a source comparison")
    if covered != expected_set:
        raise ValueError("Review checks must cover each target exactly once")
    part_executions = {}
    if "parts" in record:
        if not isinstance(record["parts"], list) or not record["parts"]:
            raise ValueError("Round parts must be nonempty")
        executions = set()
        for part in record["parts"]:
            if (not isinstance(part, dict) or set(part) != {"execution_id", "targets"}
                    or not isinstance(part["execution_id"], str) or not part["execution_id"].strip()
                    or part["execution_id"] in executions or not isinstance(part["targets"], list)
                    or not part["targets"]):
                raise ValueError("Invalid round partition provenance")
            executions.add(part["execution_id"])
            for target in part["targets"]:
                if round_id == "sample":
                    if not isinstance(target, dict) or set(target) != {"unit_id", "number"}:
                        raise ValueError("Invalid sample partition target")
                    key = (target["unit_id"], str(target["number"]))
                else:
                    if not isinstance(target, str):
                        raise ValueError("Invalid unit partition target")
                    key = (target, None)
                if key in part_executions:
                    raise ValueError("Overlapping round partitions")
                part_executions[key] = part["execution_id"]
        if set(part_executions) != set(expected):
            raise ValueError("Round partitions require exact target coverage")
    findings = []
    for check in checks:
        uid = str(check.get("unit_id")) if check.get("unit_id") is not None else ""
        num = _sample_number(check) if round_id == "sample" else None
        verdict = check.get("verdict")
        rationale = check.get("rationale") or ""
        delta = ""
        if verdict != "ok" or delta:
            evidence = check.get("evidence") if isinstance(check.get("evidence"), list) else []
            ev_text = [str(i.get("path", "")) + ":" + str(i.get("locator", "")) + ": " + str(i.get("quote", ""))
                       for i in evidence if isinstance(i, dict)]
            source = {"round_id": round_id, "execution_id": part_executions.get((uid, num), record["execution_id"]),
                      "input_binding": record["input_binding"], "target_number": num,
                      "verdict": verdict, "rationale": rationale,
                      "evidence": evidence, "assessment": check.get("assessment"),
                      "issue_key": check.get("issue_key"), "resolution_key": check.get("resolution_key")}
            base_reason = _zh_reason(rationale)
            if delta:
                prefix = "盲判结果与原标注不同：" + delta
                base_reason = prefix if verdict == "ok" else base_reason + "；" + prefix
            findings.append({"unit_id": uid, "field": round_id, "severity": "major",
                             "reason": base_reason, "evidence": ev_text, "round_sources": [source]})
    return findings


def frozen_partition_digest(state):
    """Digest of an immutable partition set; per-part task IDs may rotate."""
    stripped = dict(state, completed=[],
                    parts=[{k: v for k, v in part.items() if k != "task_id"}
                           for part in state.get("parts", []) if isinstance(part, dict)])
    return _digest(stripped)


def _verify_round_sources(batch, plan, record):
    """Verify v2 observations against their bound tasks and retained responses.

    The final receipt is written only after evaluation, so the durable install
    transaction is the authority for that part, including during recovery.
    """
    parts = record.get("parts")
    ids = [p["execution_id"] for p in parts] if parts else [record["execution_id"]]
    if any(not re.fullmatch(r"round-[0-9a-f]{32}", task_id) for task_id in ids):
        raise ValueError("Review execution has no valid task binding")
    bindings = [_load(batch / "workflow/review-bindings" / (task_id + ".json")) for task_id in ids]
    if any(not isinstance(b, dict) for b in bindings):
        raise ValueError("Review execution binding missing")
    state_path = bindings[0].get("state_path", "")
    if not isinstance(state_path, str) or not re.fullmatch(r"workflow/review-partitions/[0-9a-f]{64}\.json", state_path):
        raise ValueError("Invalid review partition binding path")
    state = _load(batch / state_path)
    if not isinstance(state, dict) or state.get("plan") != plan or state.get("round_id") != record["round_id"]:
        raise ValueError("Review partition state missing or mismatched")
    state_parts, completed = state.get("parts"), state.get("completed")
    if not isinstance(state_parts, list) or not isinstance(completed, list) or len(state_parts) != len(ids) or len(completed) != len(ids) - 1:
        raise ValueError("Review partition state incomplete")
    if (parts and (state.get("execution_id") != record["execution_id"]
                   or [p["targets"] for p in parts] != [p["targets"] for p in state_parts])):
        raise ValueError("Review partition targets or group mismatch")
    transaction = _load(batch / "workflow/review-transactions" / (ids[-1] + ".json"))
    if not isinstance(transaction, dict) or transaction.get("task_id") != ids[-1] or transaction.get("record") != record:
        raise ValueError("Review install transaction missing or mismatched")
    collected = []
    for index, (task_id, binding) in enumerate(zip(ids, bindings)):
        task = binding.get("task", {})
        expected_state_digest = (frozen_partition_digest(state) if binding.get("frozen_state")
                                 else _digest(dict(state, completed=completed[:index])))
        if (binding.get("state_path") != state_path or binding.get("part_index") != index
                or binding.get("plan") != plan or binding.get("input_hashes") != state.get("input_hashes")
                or binding.get("prior_rounds") != state.get("prior_rounds")
                or binding.get("state_digest") != expected_state_digest
                or binding.get("context_version") not in ("blind-context-v7", "blind-context-v8")
                or task.get("protocol") != ROUND_PROTOCOL or task.get("task_id") != task_id
                or task.get("round_id") != record["round_id"] or task.get("input_binding") != plan["input_binding"]
                or task.get("prompt") != state_parts[index].get("prompt")
                or task.get("part_index") != index + 1 or task.get("part_count") != len(ids)):
            raise ValueError("Review execution binding mismatch")
        entry = completed[index] if index < len(completed) else transaction
        digest = entry.get("result_digest")
        if index < len(completed) and entry.get("task_id") != task_id:
            raise ValueError("Review completed partition mismatch")
        responses = (batch / "workflow/review-results" / task_id).glob("*.json")
        candidates = [_load(path) for path in responses]
        candidates = [result for result in candidates if isinstance(result, dict) and _digest(result) == digest
                      and result.get("status") == "completed" and result.get("findings") == []]
        if len(candidates) < 1:
            raise ValueError("Review execution response missing or mismatched")
        artifacts = candidates[0].get("artifacts")
        if not isinstance(artifacts, dict) or set(artifacts) != {"review_round"}:
            raise ValueError("Review execution response artifacts mismatch")
        artifact = artifacts["review_round"]
        if not isinstance(artifact, dict):
            raise ValueError("Review execution response artifact invalid")
        checks = artifact.get("checks")
        if not isinstance(checks, list) or (index < len(completed) and checks != entry.get("checks")):
            raise ValueError("Review execution checks mismatch")
        targets = state_parts[index]["targets"]
        actual = ([{"unit_id": c.get("unit_id"), "number": _sample_number(c)} for c in checks]
                  if record["round_id"] == "sample" else [c.get("unit_id") for c in checks])
        if len(actual) != len(targets) or any(target not in actual for target in targets):
            raise ValueError("Review execution response targets mismatch")
        receipt = _load(batch / "workflow/review-bindings" / (task_id + ".receipt.json"))
        if receipt is not None and (not isinstance(receipt, dict) or receipt.get("result_digest") != digest):
            raise ValueError("Review receipt digest mismatch")
        collected.extend([dict(c, number=_sample_number(c))
                          for c in checks] if record["round_id"] == "sample" else checks)
    if collected != record["checks"]:
        raise ValueError("Review checks differ from retained responses")


def evaluate_rounds(batch_dir, payload=None) -> dict:
    """Fail closed on missing/stale/invalid rounds; emit no invented review data."""
    batch = Path(batch_dir)
    errors, findings, checked = [], [], []
    try:
        plan = prepare_round_plan(batch)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return {"passed": False, "execution_completed": False, "errors": [_round_issue(r, str(exc)) for r in ROUND_IDS],
                "unchecked": [_round_issue(r, str(exc)) for r in ROUND_IDS], "findings": [], "checked_rounds": []}
    if payload is None:
        payload = _load(batch / REVIEW_DIR / ROUND_FILE)
    valid = (isinstance(payload, dict) and set(payload) == {"plan", "rounds"}
             and payload.get("plan") == plan and isinstance(payload.get("rounds"), list))
    records = payload["rounds"] if valid else []
    executions = set()
    for round_id in ROUND_IDS:
        matches = [r for r in records if isinstance(r, dict) and r.get("round_id") == round_id]
        try:
            if not valid or len(matches) != 1:
                raise ValueError("Missing, stale or duplicate round record; real separate semantic review required")
            record = matches[0]
            problems = _round_checks(record, round_id, plan, batch, lenient=True)
            _verify_round_sources(batch, plan, record)
            round_executions = {record["execution_id"]} | {p["execution_id"] for p in record.get("parts", [])}
            if executions.intersection(round_executions):
                raise ValueError("Rounds must come from separate executions")
            executions.update(round_executions)
            findings.extend(problems)
            checked.append(round_id)
        except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
            errors.append(_round_issue(round_id, str(exc)))
    if any(not isinstance(r, dict) or r.get("round_id") not in ROUND_IDS for r in records):
        errors.append(_round_issue("unknown", "Unknown review round"))
    if checked == list(ROUND_IDS) and not errors:
        try:
            from .review_consensus import compare
            findings.extend(compare(batch, plan, records))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            errors.append(_round_issue("comparison", str(exc)))
    # Source attribution is a program conclusion, separate from model verdicts
    # and from manually resolved findings. Keep execution and other targets.
    sample_source_bindings = []
    if 'sample' in checked:
        record = next(r for r in records if r.get('round_id') == 'sample')
        for check in record['checks']:
            proof = _sample_source_binding(batch, plan, check)
            sample_source_bindings.append({**proof, 'evidence': check['evidence']})
            if proof['status'] != 'proven':
                number = str(check['number'])
                errors.append({'code': 'REVIEW_SAMPLE_SOURCE_UNVERIFIED', 'round_id': 'sample',
                               'unit_id': check['unit_id'], 'number': number,
                               'field': 'review_rounds.sample.source', 'required': True,
                               'status': 'unchecked', 'message': proof['reason'],
                               'reason': proof['reason'], 'source_binding': proof,
                               'evidence': check['evidence']})
    # Keep immutable round observations, but honor a subsequent attributed,
    # evidence-backed decision on that exact observation. A bare status or a
    # different reason must never hide a round finding.
    decisions = _findings(_load(batch / REVIEW_DIR / "manual-review-queue.json"))
    resolved_findings = []
    outstanding = []
    for finding in findings:
        decision = next((d for d in decisions if not _is_open(d)
                         and d.get("decided_at") and d.get("decided_by")
                         and str(d.get("resolution_note") or "").strip()
                         and d.get("unit_id") == finding.get("unit_id")
                         and d.get("reason") == finding.get("reason")
                         and d.get("field") == finding.get("field")
                          and d.get("round_sources") == finding.get("round_sources")), None)
        if decision:
            resolved_findings.append({**finding, "resolution_note": decision["resolution_note"]})
        else:
            outstanding.append(finding)
            errors.append({"code": "REVIEW_ROUND_FINDING", **finding})
    return {"passed": not errors, "resolved_findings": resolved_findings, "execution_completed": checked == list(ROUND_IDS) and not any(e["code"] == "REVIEW_ROUND_UNCHECKED" for e in errors), "errors": errors,
            "unchecked": [e for e in errors if e["code"] in ("REVIEW_ROUND_UNCHECKED", "REVIEW_SAMPLE_SOURCE_UNVERIFIED")],
            "findings": findings, "checked_rounds": checked, "plan": plan,
            "sample_source_bindings": sample_source_bindings}


def write_gate(batch_dir, result: dict) -> Path:
    target = Path(batch_dir) / REVIEW_DIR / "review-gate.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


# ---------------------------------------------------------------- 人工处理

def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def resolve(batch_dir, finding_ids=None, all_minor: bool = False, action: str = "accept",
            actor: str = "unknown", note: str = "", value: str | None = None,
            force: bool = False) -> dict:
    """处理审核发现，并把决定写入 decisions.jsonl。

    安全边界：
    - all_minor 只命中 severity=minor 且 blocking=false 的项；
    - blocker / major 必须显式给 --finding 且带 --force；
    - reject / hold 不会让条目变成已解决，闸门仍会拦住。
    """
    batch_dir = Path(batch_dir)
    review_dir = batch_dir / REVIEW_DIR
    files = {}
    for name in ("content-review.json", "metadata-review.json"):
        payload = _load(review_dir / name)
        files[name] = payload if isinstance(payload, dict) else {"findings": []}
    queue_payload = _load(review_dir / "manual-review-queue.json") or {"items": []}
    queue = queue_payload.get("items", []) if isinstance(queue_payload, dict) else []

    wanted = set(finding_ids or [])
    selected: list[tuple[str, dict]] = []
    for name, payload in files.items():
        for finding in payload.get("findings", []):
            fid = str(finding.get("finding_id") or "")
            severity = str(finding.get("severity") or "").lower()
            blocking = finding.get("blocking") is True
            if wanted:
                if fid not in wanted:
                    continue
                if (severity in ("blocker", "major") or blocking) and not force:
                    return {"error": f"{fid} 是 {severity or 'blocker'}，需要 --force 才能处理",
                            "resolved": 0}
            elif all_minor:
                if severity != "minor" or blocking:
                    continue
            else:
                return {"error": "必须给 --finding 或 --all-minor", "resolved": 0}
            if not _is_open(finding):
                continue
            selected.append((name, finding))

    if not selected:
        return {"error": "没有匹配到待处理的审核项", "resolved": 0}

    resolved_ids = []
    source_decisions = []
    for name, finding in selected:
        fid = str(finding.get("finding_id") or "")
        old = str(finding.get("status") or "")
        if action == "accept":
            finding["status"] = "reviewed"
            finding["resolution"] = "accepted"
        elif action == "edit":
            if not value:
                return {"error": "edit 需要 --value", "resolved": 0}
            finding["status"] = "reviewed"
            finding["resolution"] = f"edited: {value}"
        elif action == "reject":
            finding["status"] = "blocked"
            finding["resolution"] = "rejected"
        else:  # hold
            finding["status"] = "needs_review"
            finding["resolution"] = "held"
        finding["resolved_by"] = actor
        finding["resolved_at"] = _now()
        finding["resolution_note"] = note

        for item in queue:
            if str(item.get("finding_id") or "") == fid:
                item["status"] = ("accepted" if action == "accept"
                                  else "resolved" if action == "edit"
                                  else "blocked" if action == "reject" else "open")
                item["decided_by"] = actor
                item["decided_at"] = finding["resolved_at"]
                item["resolution_note"] = note
                sources = finding.get("round_sources")
                if (action in ("accept", "edit") and isinstance(sources, list) and len(sources) > 1
                        and item.get("round_sources") == sources
                        and item.get("unit_id") == finding.get("unit_id")
                        and item.get("reason") == finding.get("reason")
                        and all(isinstance(source, dict) and source.get("round_id") in ROUND_IDS
                                and not (source.get("protocol") == ROUND_PROTOCOL and source.get("field"))
                                for source in sources)):
                    for index, source in enumerate(sources):
                        source_decisions.append({**item, "finding_id": f"{fid}-source-{index + 1}",
                                                 "field": source["round_id"], "round_sources": [source]})
        resolved_ids.append(fid)
        _append_decision(review_dir, {
            "finding_id": fid, "unit_id": finding.get("unit_id"),
            "field": finding.get("field"), "action": action,
            "old_status": old, "new_status": finding["status"],
            "value": value, "reason": note, "decided_by": actor,
        })

    queue.extend(source_decisions)
    for name, payload in files.items():
        _write_json(review_dir / name, payload)
    _write_json(review_dir / "manual-review-queue.json",
                {"queue_version": "1.0", "items": queue})
    return {"resolved": len(resolved_ids), "finding_ids": resolved_ids, "action": action}


def _now() -> str:
    from .manifest import now_iso
    return now_iso()


def _append_decision(review_dir: Path, entry: dict) -> None:
    path = review_dir / "decisions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
