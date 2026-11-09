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
import time
from pathlib import Path

# 人工审核范围（产品规则，2026-11）：
#   只把“标签差异”和“基本差异”送人工，用户不需要全文重审：
#     ① 知识点标签差异：原标签 / 一审 / 二审 打的知识点不同；
#     ② 基本差异：题型/子类等基础标签不同，或答案/解析/题面与原文不符等实质差异。
#   以下不属于人工审核范畴，只作提示（informational），不进队列、不阻断闸门：
#     · 图片/音频等视觉听觉能力受限导致“看不到图、无法确认图文对应”的不确定。
#   依据：AI 无法看图/听音是能力边界，不是标签分歧；审这些只是浪费人工时间。
MANUAL_REVIEW_SCOPE = "label_and_basic_differences_only"
_CAPABILITY_GAP = re.compile(r"图片|占位|图像|音频|听不|看不见|看不到|无法(?:核对|确认|查看).{0,8}(?:图|音频|图图)")
_LABEL_CONFLICT = re.compile(r"不一致|不同|冲突|不符|错误|矛盾|判断有出入|与原(?:标)?注")
ROUND_FILE = "review-rounds.json"
# 两轮审核：一审全量盲判 + 二审抽样复核（不再单独跑全量知识点轮）。
# 抽样口径：整组题型（阅读/完形/补全对话/信息转换等）按整组抽、只做组级核对；
# 其余题型按小题抽，抽样数 ceil(小题数 × 20%)。
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
    # Supplied as auxiliary context by agent_tasks; citations need the same binding.
    "01_source/pdf-ocr.json", "01_source/material-roles.json",
)
# 只绑定审核提示词实际使用的规则；装配版式/上传约束变化不应让审核轮次
# 无条件过期，除非它们确实改变审核语义。
ROUND_RULES = ("references/filter-framework.json", "references/knowledge-catalog.json",
               "references/type-knowledge-map.json", "skills-reference/knowledge-matching-rules.md",
               "skills-reference/README.md", "skills-reference/type-taxonomy.md",
               "skills-reference/knowledge-catalog.md", "skills-reference/type-knowledge-map.md",
               "docs/knowledge-tagging.md", "docs/content-labeling.md")

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


_LOAD_CACHE = {}


def _load(path: Path):
    """Parse a JSON artifact once per process per (mtime, size).

    Review evaluation re-reads the same bindings/transactions/partitions dozens of
    times per request; parsing them each time was the measured hotspot on a
    48-unit batch (~10s per overview). Reads stay read-only; a changed file is
    re-parsed because the cache key includes mtime and size.
    """
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    hit = _LOAD_CACHE.get(key)
    if hit is not None:
        return hit[0]
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        value = {"__parse_error__": True}
    except OSError:
        return None
    if len(_LOAD_CACHE) > 4096:
        _LOAD_CACHE.clear()
    _LOAD_CACHE[key] = (value,)
    return value


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


def _evaluate_uncached(batch_dir) -> dict:
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
            if _is_capability_gap(finding):
                continue
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
    open_items = []
    if queue is None:
        errors.append({"code": "MANUAL_QUEUE_MISSING",
                       "message": "缺少 07_review/manual-review-queue.json，无法证明人工审核已完成"})
    else:
        open_items = [it for it in (queue_items or [])
                      if _is_open(it) and not _is_capability_gap(it)]
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
    _counts_open = len(open_items)
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
    # 区分「真实人工差异」与「结构性硬错」：
    # 用户流程目的是一审/二审找出差异交给人工，这些差异是本流程的产出，不是
    # 缺陷。若除「人工待审差异」外没有其它硬错（缺轮次、必检未完成、文件不可
    # 读、来源不可证、标签审计失败等），则允许继续组装待上传批次，但必须标记
    # 为「已执行、待人工审核、未放行(release_ready=False)」，绝不算质量通过。
    MANUAL_CODES = {"REVIEW_ROUND_FINDING", "MANUAL_REVIEW_OPEN", "REVIEW_FINDING_OPEN"}
    manual_only = (
        bool(errors)
        and all(e.get("code") in MANUAL_CODES for e in errors)
        and rounds.get("execution_completed") is True
    )
    return {
        "rounds": rounds,
        "passed": passed,
        "manual_only": manual_only,
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


_DIGEST_CACHE = {}


def _digest(value) -> str:
    """Hash an immutable dict/list; the same partition is digested many times."""
    try:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except TypeError:
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
    hit = _DIGEST_CACHE.get(serialized)
    if hit is not None:
        return hit
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    if len(_DIGEST_CACHE) > 2048:
        _DIGEST_CACHE.clear()
    _DIGEST_CACHE[serialized] = digest
    return digest


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


def _bound_source_texts(batch, plan):
    """Read every bound review input once; evidence paths are often mislabeled."""
    root = Path(__file__).resolve().parents[1]
    texts = {}
    for key in (plan.get("input_hashes") or {}):
        path = (root / key[len("rules:"):]) if key.startswith("rules:") else (Path(batch) / key)
        try:
            if path.is_file():
                texts[key] = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            continue
    return texts


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


# Fields that do not participate in label/content review. Keep this projection
# separate from raw task bindings: in-flight imports still require exact bytes.
_REVIEW_METADATA = frozenset({
    "difficulty", "difficultyValue", "difficultyEvidence",
    "source_type", "source_name", "sourceName", "sourceType", "year", "region",
    "categories", "category", "location", "paper_name", "province", "city",
    "created_at", "updated_at", "generated_at", "checked_at", "timestamp",
    "source_span", "source_slice", "evidence", "notes", "status",
})


def semantic_review_value(value):
    """Project review semantics, excluding provenance/difficulty bookkeeping."""
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if key in _REVIEW_METADATA or key in {"words", "word_count", "wordCount"} and isinstance(child, (int, float)):
                continue
            if key == "filterMetadata" and isinstance(child, dict):
                child = {label: data for label, data in child.items()
                         if label in {"subtypes", "knowledge", "knowledgeIds", "knowledge_ids", "type", "question_type"}}
                if not child:
                    continue
            if key in {"materialBlocks", "material_blocks"} and isinstance(child, list):
                # An empty legacy paragraph placeholder contains no material.
                # Removing it for wire-schema compatibility is not new content.
                child = [block for block in child if not (isinstance(block, dict)
                         and block.get("type") in {"paragraph", "text"}
                         and set(block) <= {"type", "rows", "text", "content"}
                         and block.get("rows") in (None, [])
                         and block.get("text") in (None, "") and block.get("content") in (None, "", []))]
            result[key] = semantic_review_value(child)
        if (value.get("question_type", value.get("type")) == "听力关键词语选择"
                and value.get("unit_kind") == "standalone" and isinstance(value.get("options"), list)
                and len(value["options"]) >= 3 and isinstance(value.get("stem"), str)
                and re.fullmatch(r"\s*" + re.escape(str(value.get("number"))) + r"[.．、]\s*", value["stem"])):
            # The printed number alone is a locator, just as the same row's
            # explicit number field; source-proven wire normalization adds no question.
            result["stem"] = ""
        return result
    if isinstance(value, list):
        return [semantic_review_value(v) for v in value]
    return value


def _unit_bindings(batch, units, hashes):
    """Bind whole review units; unprovable source scopes remain global."""
    from .review_rounds import _source_excerpts, _transcript_excerpt, CONTEXT_VERSION, PROMPT_VERSION
    source = (batch / "01_source/正文.txt").read_text(encoding="utf-8-sig")
    rows = {name: _load(batch / name) for name in (
        "03_types/types.json", "04_knowledge/knowledge.json", "04_knowledge/explanations.json",
        "06_batch/batch.json", "06_batch/batch_meta.json", "02_audio/audio_map.json",
        "02_audio/segment-evidence.json")}
    answers = _load(batch / "01_source_ans/answers.json")
    global_hashes = {k: v for k, v in hashes.items() if k.startswith("rules:") or k in (
        "01_source/附录.txt", "01_source_ans/正文.txt") or k.startswith("01_source/materials/")}
    # Extraction warnings/media are not reconstructed from the question stem.
    extraction = _load(batch / "01_source/extraction.json")
    if isinstance(extraction, dict):
        from .unit_tasks import compact_extraction
        extraction = {key: value for key, value in compact_extraction(extraction).items()
                      if key not in {"source", "source_sha256", "text_sha256", "evidence_dir", "version",
                                     "structure_status", "characters", "char_count", "ocr_assist_enabled"}}
    result = {}
    for unit in units:
        uid, numbers = unit["unit_id"], {str(n) for n in unit["numbers"]}
        text = _source_excerpts(source, [unit], force=True, adjacent_units=units)
        if isinstance(text, dict):
            text = [span["text"] for span in text["spans"]]
        payload = {"unit": semantic_review_value(unit), "source": text,
                   "contract": [CONTEXT_VERSION, PROMPT_VERSION], "global": global_hashes, "extraction": semantic_review_value(extraction)}
        for name, value in rows.items():
            if name.startswith("02_audio/"):
                if unit.get("type") and not str(unit["type"]).startswith("听力"):
                    continue
            if isinstance(value, dict) and isinstance(value.get("items"), list):
                value = [row for row in value["items"] if isinstance(row, dict)
                         and (row.get("unit_id") or row.get("id")) == uid]
            payload[name] = semantic_review_value(value)
        if isinstance(answers, dict):
            selected = {}
            for key, value in answers.items():
                if isinstance(value, dict):
                    value = {k: v for k, v in value.items() if not str(k).isdigit() or str(k) in numbers}
                selected[key] = value
            payload["answers"] = semantic_review_value(selected)
        payload["observations"] = semantic_review_value(_load(batch / "workflow/media-observations.json"))
        payload["media"] = {k: v for k, v in hashes.items()
                            if k not in ROUND_INPUTS and not k.startswith("rules:")
                            and not k.startswith(("01_source/materials/", "02_audio/"))}
        if not unit.get("type") or str(unit["type"]).startswith("听力"):
            for name in hashes:
                if name.startswith("02_audio/") and name.endswith("full-transcript.json"):
                    payload[name] = _transcript_excerpt(_load(batch / name),
                        rows["02_audio/segment-evidence.json"], {uid})
        result[uid] = _digest(payload)
    return result


_PLAN_CACHE = {}


def _file_digest(path):
    import hashlib
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "missing"


# 审核口径版本：仅当“什么算差异 / 如何比对”的规则真的改变时手工递增。
# 不要用源码哈希——那样每改一次实现（包括修 bug、改注释）都会把刚跑完的
# 审核轮次判成过期，队列里的真实差异被整批丢掉。
REVIEW_RULES_VERSION = "review-rules/v2"


def _review_rule_revision():
    """Stable revision of the review口径 plus the role prompt bytes."""
    here = Path(__file__).resolve()
    role = here.parents[1] / "prompts" / "roles" / "review.md"
    return _digest([REVIEW_RULES_VERSION, _file_digest(role)])


def _plan_input_key(batch, units):
    """Fingerprint every file the plan binds by CONTENT, not mtime.

    mtime is not a safe staleness signal (same-tick writes collide), and the plan
    digests are the review proof, so a wrong cache hit would hide changed inputs.
    Files here are small (source/rules/reports), so hashing is affordable.
    """
    parts = []
    for relative in (*ROUND_INPUTS, *ROUND_RULES):
        for base in (batch, Path(__file__).resolve().parents[1]):
            path = base / relative
            if path.is_file():
                parts.append((str(path), _file_digest(path)))
                break
        else:
            parts.append((relative, "missing"))
    for relative in ("06_batch/batch.json", "06_batch/batch_meta.json", "workflow/media-observations.json"):
        parts.append((relative, _file_digest(batch / relative)))
    materials = batch / "01_source/materials"
    if materials.is_dir():
        for path in sorted(materials.glob("attachment_*.txt")):
            parts.append((str(path), _file_digest(path)))
    # Observed media bytes are bound by the plan; the key must include them so a
    # changed image still fails the stale-observation check instead of reusing a
    # plan computed from the previous bytes.
    observations = _load(batch / "workflow/media-observations.json") or {}
    for item in observations.get("observations", []) if isinstance(observations, dict) else []:
        relative = item.get("path") if isinstance(item, dict) else None
        if isinstance(relative, str) and relative:
            parts.append(("media:" + relative, _file_digest(batch / relative)))
    parts.append(tuple((u["unit_id"], tuple(u["numbers"])) for u in units))
    parts.append(("rule-revision", _review_rule_revision()))
    return _digest(parts)


def prepare_round_plan(batch_dir) -> dict:
    """Bind actual inputs and a reproducible 20% *question* sample; never review.

    No identity/signature is needed. Hashes detect stale input, not review quality.
    The caller must run three separate semantic tasks against this same plan.

    A per-unit binding requires semantic projection of the whole unit and its
    source excerpt, which is the measured cost on big papers. The plan is a pure
    function of the bound files and confirmed units, so it is memoized by that
    fingerprint (and would be recomputed for any change) instead of per request.
    """
    from .agent_tasks import confirmed_units
    batch = Path(batch_dir)
    units = confirmed_units(batch)
    cache_key = (str(batch), _plan_input_key(batch, units))
    cached = _PLAN_CACHE.get(cache_key)
    if cached is not None:
        # Callers (rebase/patch/tests) may mutate the returned plan. A deep copy in
        # BOTH directions keeps the cache immutable: the stored plan is never
        # handed out, and a mutated returned plan can never leak back in.
        import copy
        return copy.deepcopy(cached[0])
    # 跳过听力的批次：听力单元没有音频/原文可核，语义审核不覆盖它们，
    # 否则一审/二审会因"无法核对听力"永久 uncertain。
    if (batch / "02_audio" / "SKIP_LISTENING").is_file():
        units = [u for u in units if not str(u.get("type", "")).startswith("听力")]
    required = ROUND_INPUTS[:4]
    missing = [p for p in required if not (batch / p).is_file()]
    if not any((batch / p).is_file() for p in ROUND_INPUTS[4:6]):
        missing.append("06_batch/batch.json or batch_meta.json")
    if missing:
        raise ValueError("Missing round inputs: " + ", ".join(missing))
    root = Path(__file__).resolve().parents[1]
    hashes = {}
    materials = batch / '01_source/materials'
    extra_inputs = tuple(path.relative_to(batch).as_posix()
                         for path in sorted(materials.glob('attachment_*.txt'))
                         if re.fullmatch(r'attachment_\d{3,}\.txt', path.name)) if materials.is_dir() else ()
    audio_sources = batch / '02_audio/sources'
    audio_transcripts = tuple(path.relative_to(batch).as_posix()
                              for path in sorted(audio_sources.glob('*/full-transcript.json'))
                              if path.parent.name == 'audio' or re.fullmatch(r'audio_\d{3,}', path.parent.name)) if audio_sources.is_dir() else ()
    for base, paths, prefix in ((batch, (*ROUND_INPUTS, *extra_inputs, *audio_transcripts), ""), (root, ROUND_RULES, "rules:")):
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
    binding = _digest({"input_hashes": hashes, "questions": questions,
                       "rule_revision": _review_rule_revision()})
    # 二审抽样口径：以组题为单位的题型（阅读/完形/补全对话/信息转换等）整组作为一个
    # 抽样单位，number=None 表示"抽整组、只做组级核对"，不再逐小题抽查、不做小题二审；
    # 独立单题仍按单题（number=题号）抽样。题型取自 03_types/types.json（结构单元行本身
    # 不带题型）；缺类型时按独立单题处理，保持保守。
    pool = round_sample_pool(batch, units)
    count = (len(pool) + 4) // 5
    # Fixed by inputs, not by a model choosing convenient questions. This is not
    # a secret/random sample or the legacy HMAC independent-review mechanism.
    sample = sorted(pool, key=lambda q: _digest([ROUND_PROTOCOL, pool, q]))[:max(1, count)]
    plan = {"protocol": ROUND_PROTOCOL, "input_binding": binding, "input_hashes": hashes,
            "units": [u["unit_id"] for u in units], "questions": questions,
            "sample": sample, "sample_method": "sha256-unit-topology-order/v2",
            "unit_bindings": _unit_bindings(batch, units, hashes)}
    if len(_PLAN_CACHE) > 64:
        _PLAN_CACHE.clear()
    import copy
    _PLAN_CACHE[cache_key] = (copy.deepcopy(plan), time.time())
    return plan


def round_sample_pool(batch_dir, units=None):
    """Actual sampling targets, shared by current and retained-plan validation."""
    from .agent_tasks import confirmed_units
    from workbench import taxonomy
    batch = Path(batch_dir)
    if units is None:
        units = confirmed_units(batch)
        if (batch / "02_audio/SKIP_LISTENING").is_file():
            units = [u for u in units if not str(u.get("type", "")).startswith("听力")]
    loaded = _load(batch / "03_types/types.json") or {}
    types = {x.get("unit_id"): x.get("type")
             for x in loaded.get("items", []) if isinstance(x, dict)}
    pool = []
    for unit in units:
        if unit.get("unit_kind") == "group" and taxonomy.whole_unit_type(types.get(unit["unit_id"])):
            pool.append({"unit_id": unit["unit_id"], "number": None})
        else:
            pool.extend({"unit_id": unit["unit_id"], "number": str(n)} for n in unit["numbers"])
    return pool


def resolve_round_plan(batch_dir, current_plan=None, payload=None):
    """Select an original whole plan only with complete read-only proof.

    Callers preparing/resuming work must use the effective plan for comparisons.
    This does not rebind tasks or change their frozen input hashes.
    """
    current = prepare_round_plan(batch_dir) if current_plan is None else current_plan
    if payload is None:
        payload = _load(Path(batch_dir) / REVIEW_DIR / ROUND_FILE)
    if not isinstance(payload, dict) or payload.get("plan") == current:
        return current, None
    from .review_legacy import compatible_plan
    compatibility = compatible_plan(batch_dir, current, payload)
    return (payload["plan"] if compatibility["accepted"] else current), compatibility


def stage_semantic_inputs(batch_dir):
    """Read-only stage fingerprints; never include report timestamps/raw bindings."""
    plan = prepare_round_plan(batch_dir)
    return {**{"review-unit:" + uid: digest for uid, digest in plan["unit_bindings"].items()},
            "review:sample": _digest(plan["sample"])}


def _check_key(check, round_id):
    return (check.get("unit_id"), _sample_number(check) if round_id == "sample" else None)


def _completed_part_source(batch, task_id, binding):
    """Verify a genuinely accepted part before its whole round has completed."""
    relative = binding.get("state_path", "")
    if not isinstance(relative, str) or not re.fullmatch(r"workflow/review-partitions/[0-9a-f]{64}\.json", relative):
        raise ValueError("Invalid completed review part state")
    state = _load(batch / relative)
    index, task = binding.get("part_index"), binding.get("task", {})
    if not isinstance(state, dict) or isinstance(index, bool) or not isinstance(index, int):
        raise ValueError("Completed review part state missing")
    parts, completed = state.get("parts", []), state.get("completed", [])
    if not 0 <= index < len(parts) or not index < len(completed):
        raise ValueError("Review part was never accepted")
    entry, part = completed[index], parts[index]
    expected_digest = (frozen_partition_digest(state) if binding.get("frozen_state")
                       else _digest(dict(state, completed=completed[:index])))
    if (entry.get("task_id") != task_id or part.get("task_id") != task_id
            or binding.get("plan") != state.get("plan")
            or binding.get("input_hashes") != state.get("input_hashes")
            or binding.get("prior_rounds") != state.get("prior_rounds")
            or binding.get("state_digest") != expected_digest
            or binding.get("context_version") not in ("blind-context-v10", "blind-context-v11")
            or task.get("protocol") != ROUND_PROTOCOL or task.get("task_id") != task_id
            or task.get("input_binding") != state["plan"]["input_binding"]
            or task.get("round_id") != state.get("round_id")
            or task.get("prompt") != part.get("prompt")
            or task.get("part_index") != index + 1 or task.get("part_count") != len(parts)):
        raise ValueError("Completed review part binding mismatch")
    receipt = _load(batch / "workflow/review-bindings" / (task_id + ".receipt.json"))
    if not isinstance(receipt, dict) or receipt.get("result_digest") != entry.get("result_digest"):
        raise ValueError("Completed review part receipt missing or mismatched")
    candidates = [_load(path) for path in (batch / "workflow/review-results" / task_id).glob("*.json")]
    candidates = [r for r in candidates if isinstance(r, dict) and _digest(r) == entry.get("result_digest")
                  and r.get("status") == "completed" and r.get("findings") == []]
    artifact = candidates[0].get("artifacts") if candidates else None
    if not isinstance(artifact, dict) or set(artifact) != {"review_round"}:
        raise ValueError("Completed review part response missing or mismatched")
    checks = artifact["review_round"].get("checks") if isinstance(artifact["review_round"], dict) else None
    if state["round_id"] == "sample" and isinstance(checks, list):
        checks = [dict(c, number=_sample_number(c)) for c in checks]
    if checks != entry.get("checks"):
        raise ValueError("Completed review part checks differ from retained response")
    actual = ([{"unit_id": c.get("unit_id"), "number": _sample_number(c)} for c in checks]
              if state["round_id"] == "sample" else [c.get("unit_id") for c in checks])
    if len(actual) != len(part["targets"]) or any(target not in actual for target in part["targets"]):
        raise ValueError("Completed review part targets mismatch")
    return {"round_id": state["round_id"], "input_binding": state["plan"]["input_binding"],
            "execution_id": task_id, "checks": checks}


def _reuse_source(batch, ref, round_id, plan):
    """Resolve one original executed check, without rebinding its task/response."""
    task_id = ref.get("task_id")
    if not isinstance(task_id, str) or not re.fullmatch(r"round-[0-9a-f]{32}", task_id):
        raise ValueError("Invalid review reuse execution")
    transaction = _load(batch / "workflow/review-transactions" / (task_id + ".json"))
    binding = _load(batch / "workflow/review-bindings" / (task_id + ".json"))
    if not isinstance(binding, dict):
        raise ValueError("Review reuse original execution missing")
    if ref.get("part") is True:
        record = _completed_part_source(batch, task_id, binding)
    else:
        if not isinstance(transaction, dict) or transaction.get("task_id") != task_id:
            raise ValueError("Review reuse original transaction missing or mismatched")
        record = transaction.get("record")
        if isinstance(record, dict):
            origin_ids = [part.get("execution_id") for part in record.get("parts", [])] or [record.get("execution_id")]
            if origin_ids[-1] != task_id:
                raise ValueError("Review reuse reference is not its original install transaction")
    old_plan = binding.get("plan")
    if (not isinstance(old_plan, dict) or not isinstance(record, dict)
            or record.get("round_id") != round_id
            or record.get("input_binding") != old_plan.get("input_binding")):
        raise ValueError("Review reuse original round/input binding mismatch")
    key = _check_key(ref, round_id)
    uid = key[0]
    if (not plan.get("unit_bindings", {}).get(uid)
            or old_plan.get("unit_bindings", {}).get(uid) != plan["unit_bindings"][uid]):
        raise ValueError("Review reuse unit content changed")
    if key in {_check_key(row, round_id) for row in record.get("reuse", [])}:
        raise ValueError("Reuse must point directly to an original executed target")
    if ref.get("part") is not True:
        _verify_round_sources(batch, old_plan, record, verify_reuse=False)
    matches = [c for c in record["checks"] if _check_key(c, round_id) == key]
    if len(matches) != 1:
        raise ValueError("Review reuse original target missing or duplicated")
    return matches[0], dict(record, input_binding=old_plan["input_binding"])


def _payload_reusable_checks(batch_dir, payload, plan, round_id):
    """Return valid per-unit checks and direct original execution references."""
    batch = Path(batch_dir)
    if not isinstance(payload, dict) or not isinstance(payload.get("plan"), dict):
        return [], []
    old_plan = payload["plan"]
    if not isinstance(payload.get("rounds"), list):
        return [], []
    records = [r for r in payload["rounds"] if isinstance(r, dict) and r.get("round_id") == round_id]
    if len(records) != 1:
        return [], []
    record = records[0]
    # Historical context contracts are deliberately not upgraded to v11.
    expected = ({_check_key(q, round_id) for q in plan["sample"]} if round_id == "sample"
                else {(uid, None) for uid in plan["units"]})
    references = {_check_key(r, round_id): r for r in record.get("reuse", [])}
    ids = [p["execution_id"] for p in record.get("parts", [])] or [record.get("execution_id")]
    original = ids[-1]
    checks, refs, seen = [], [], set()
    for check in record.get("checks", []):
        key = _check_key(check, round_id)
        if key not in expected or key in seen:
            continue
        seen.add(key)
        uid, number = key
        if old_plan.get("unit_bindings", {}).get(uid) != plan.get("unit_bindings", {}).get(uid):
            continue
        ref = references.get(key) or {"unit_id": uid, "number": number, "task_id": original}
        try:
            saved, _ = _reuse_source(batch, ref, round_id, plan)
            if saved != check:
                raise ValueError("Review reuse check differs from original response")
            single = dict(plan, **{"sample" if round_id == "sample" else "units":
                [{"unit_id": uid, "number": number}] if round_id == "sample" else [uid]})
            _round_checks({"round_id": round_id, "input_binding": plan["input_binding"],
                           "execution_id": original, "checks": [check]}, round_id, single, batch)
        except (ValueError, OSError, KeyError, TypeError, AttributeError):
            continue
        checks.append(check)
        refs.append(ref)
    return checks, refs


def reusable_checks(batch_dir, payload, plan, round_id):
    """Recover unaffected targets even after another round replaced the ledger."""
    batch = Path(batch_dir)
    checks, refs = _payload_reusable_checks(batch, payload, plan, round_id)
    from .review_rounds import _review_generation
    if (_review_generation(batch, plan) is not None
            or isinstance(payload, dict) and not payload.get("plan", {}).get("unit_bindings")
            or isinstance(payload, dict) and payload.get("plan") == plan
            and any(isinstance(r, dict) and r.get("round_id") == round_id for r in payload.get("rounds", []))):
        return checks, refs
    expected = len(plan["sample"] if round_id == "sample" else plan["units"])
    seen = {_check_key(c, round_id) for c in checks}
    # Installation archives the previous ledger before replacing it. It remains
    # evidence, not a new execution; every candidate still verifies its original
    # immutable task, response and transaction before reuse.
    folder = batch / "workflow/review-history"
    for path in sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True):
        if len(checks) == expected:
            break
        if path.is_symlink():
            continue
        old_checks, old_refs = _payload_reusable_checks(batch, _load(path), plan, round_id)
        for check, ref in zip(old_checks, old_refs):
            key = _check_key(check, round_id)
            if key not in seen:
                checks.append(check)
                refs.append(ref)
                seen.add(key)
    for path in sorted((batch / "workflow/review-partitions").glob("*.json")):
        if len(checks) == expected:
            break
        if path.is_symlink():
            continue
        state = _load(path)
        if (not isinstance(state, dict) or state.get("round_id") != round_id
                or state.get("plan") == plan or not isinstance(state.get("plan"), dict)):
            continue
        entries = [entry for entry in state.get("completed", []) if isinstance(entry, dict)]
        saved_checks, saved_refs = [], []
        for entry in entries:
            for check in entry.get("checks", []):
                saved_checks.append(check)
                uid, number = _check_key(check, round_id)
                saved_refs.append({"unit_id": uid, "number": number,
                                   "task_id": entry.get("task_id"), "part": True})
        synthetic = {"plan": state["plan"], "rounds": [{"round_id": round_id,
            "execution_id": "accepted-parts", "checks": saved_checks, "reuse": saved_refs}]}
        old_checks, old_refs = _payload_reusable_checks(batch, synthetic, plan, round_id)
        for check, ref in zip(old_checks, old_refs):
            key = _check_key(check, round_id)
            if key not in seen:
                checks.append(check)
                refs.append(ref)
                seen.add(key)
    return checks, refs


def rebase_rounds(batch_dir, payload, plan):
    """Read-only reuse: complete rounds only; partial checks are exported later."""
    rounds = []
    for round_id in ROUND_IDS:
        checks, refs = reusable_checks(batch_dir, payload, plan, round_id)
        count = len(plan["sample"] if round_id == "sample" else plan["units"])
        if checks and len(checks) == count:
            rounds.append({"round_id": round_id, "input_binding": plan["input_binding"],
                           "execution_id": "reused-" + _digest(refs), "checks": checks, "reuse": refs})
    return {"plan": plan, "rounds": rounds}


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

    def _stem_proof(method):
        """引证逐字且唯一地落在该单元的确认原文里就算可归属。

        整组抽样（number=None）和「原卷里没有题号行」的独立题（如书面表达，
        卷面只有「Ⅹ.书面表达」而没有「101.」）都走这条路径，否则它们会被
        判成「题号行缺失或不唯一」，永远无法完成二审。
        """
        quotes = [item.get('quote') for item in check.get('evidence', [])
                  if isinstance(item, dict) and item.get('path') == '01_source/正文.txt']
        stem = unit.get('stem') if isinstance(unit, dict) else None
        base = {'status': 'uncertain', 'reason': '', 'source_path': '01_source/正文.txt',
                'source_sha256': digest, 'unit_id': unit.get('unit_id'), 'number': number}
        if not isinstance(stem, str) or not stem.strip():
            return dict(base, reason='缺少确认单元原文')
        for quote in quotes:
            if not isinstance(quote, str) or not quote.strip():
                continue
            if not re.search(r'[^\W\d_]', quote, re.UNICODE):
                continue
            if len(re.findall(re.escape(quote), stem)) == 1:
                return dict(base, status='proven', reason='', evidence_method=method)
        return dict(base, reason='原文引证未唯一落在该单元确认原文内')

    if number is None:
        # 抽整组的二审：以整组为单位核对，不再绑定某个小题。
        return _stem_proof('group-stem-verbatim')
    proof = prove_child(text, unit, number, source_sha256=digest)
    if (proof['status'] != 'proven' and unit.get('unit_kind') == 'standalone'
            and proof.get('reason') in ('题号行缺失或不唯一', '缺少确认单元原文')):
        # 独立题在原卷里本来就没有题号行（书面表达、整段填空等）：退回到单元原文归属。
        # 组题的子题不走这条退路——表格空位等没有题号行的小题，仍必须能唯一归属到
        # 该小题，否则就等于把别的子题的引证当成本子题的证据。
        return _stem_proof('unit-stem-verbatim')
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


def _reject_blind_label_absence(check):
    """A redacted label is not evidence of missing data: request a fresh judgment.

    Do not turn a non-ok verdict into ok or discard a semantic finding. Invalid
    claims go through the existing per-target repair path; the original response
    remains evidence and unaffected targets can still be reused.
    """
    if check.get("verdict") == "ok":
        return
    label = r"(?:\b(?:type|question_type|subtypes|knowledge|filterMetadata)\b|标签|题型|知识点|子类)"
    absent = r"(?:未见|缺失|缺少|未提供|未填写|未完成标注|为空|不存在|未包含)"
    # Match a label field/list directly, not arbitrary nearby prose such as
    # “知识点一致，但答案缺失” or “原文缺失，无法判断知识点”. Negated
    # absence claims (知识点并非缺失) must not trigger repair either.
    separator = r"(?:[\s、,，/与和及`'\"：:（）()\[\]]|字段|标注|组级|的|ID)"
    fields = rf"{label}(?:{separator}|{label})*"
    pattern = rf"(?:{fields}{absent}|{absent}(?:{separator})*{label})"
    assessment = check.get("assessment")
    claims = [check.get("rationale"), check.get("issue_key"), check.get("resolution_key")]
    if isinstance(assessment, dict):
        claims.append(assessment.get("source_comparison"))
    for text in claims:
        text = str(text or "")
        for match in re.finditer(pattern, text, flags=re.I):
            if re.search(r"(?:未发现|没有|并无|并非|不是|不代表|不意味着)(?:任何)?\s*$",
                         text[:match.start()]):
                continue
            if re.match(r"(?:符合(?:协议|规则)|是合规|合规|是允许)", text[match.end():]):
                continue
            raise RoundCheckError(
                "BLIND_LABEL_ABSENCE: 盲审中的 type/question_type、subtypes、knowledge、"
                "filterMetadata 被刻意隐藏，不能据此断言正式标签缺失。请重新独立判断 assessment；"
                "仅报告可见题文/答案/解析的实质问题，标签差异由程序比较。",
                [{"unit_id": check.get("unit_id"), "number": check.get("number")}])


def _round_checks(record, round_id, plan, batch, lenient=False, require_coverage=False):
    """收集每轮逐题判断，把与原标注的差异/问题转成人工 finding。

    每个目标必须有且仅有一条有效判断和可核验的原文引证。
    格式允许额外字段，但缺失判断、证据或比较结果不能记为已审核。
    """
    required_record = {"round_id", "input_binding", "execution_id", "checks"}
    if not isinstance(record, dict) or not required_record <= set(record) or set(record) - required_record - {"parts", "reuse"}:
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
    bound_texts = _bound_source_texts(batch, plan)
    from .agent_tasks import confirmed_units
    units = {u["unit_id"]: u for u in confirmed_units(batch)}
    for check in checks:
        if not isinstance(check, dict):
            raise ValueError("Every target needs a review judgment")
        uid = check.get("unit_id")
        number = _sample_number(check) if round_id == "sample" else None
        if round_id == "sample":
            # Normalise the resolved target number so every later reader
            # (coverage, source binding, consensus) sees a consistent value.
            # 整组目标显式写回 None，使留存响应与安装记录逐字段一致。
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
        _reject_blind_label_absence(check)
        evidence = check.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("Every target needs verifiable source evidence")
        unit = units.get(uid) or {}
        unit_text = unit.get("stem")
        if not isinstance(unit_text, str) or not unit_text.strip():
            raise ValueError("Target has no confirmed source text; review remains unchecked")
        has_source = False
        for item in evidence:
            # A real appendix/transcript quote may name the wrong bound input.
            # Repair that attribution only when another bound input proves it;
            # malformed, unbound or fabricated evidence still needs local repair.
            if not isinstance(item, dict) or any(
                not isinstance(item.get(k), str) or not item[k].strip()
                for k in ("path", "locator", "quote")
            ):
                raise ValueError("Evidence needs a path, locator and quote")
            quote = item["quote"]
            path = item["path"]
            if path in plan["input_hashes"]:
                key = path
            elif "rules:" + path in plan["input_hashes"]:
                key = "rules:" + path
            else:
                raise ValueError("Evidence must cite a bound batch input")
            matched = None
            if key is not None and _quote_matches(quote, bound_texts.get(key, "")):
                matched = key
            else:
                # The quote may be real but attributed to the wrong bound file.
                # Search every bound source; only an exact/normalized quote hits.
                for candidate, text in bound_texts.items():
                    if _quote_matches(quote, text):
                        matched = candidate
                        break
            if matched is None:
                raise ValueError("Evidence quote absent from bound input")
            item["path"] = matched
            if matched == "01_source/正文.txt":
                # Bare numbers and punctuation cannot locate a substantive claim.
                meaningful = bool(re.search(r"[^\W\d_]", quote.strip(), re.UNICODE))
                has_source |= meaningful and _quote_matches(quote, unit_text)
        if not has_source:
            raise ValueError("Every target needs verifiable source evidence")
        assessment = check.get("assessment")
        if "assessment" in check and assessment is None and check.get("verdict") == "uncertain":
            # Preserve uncertainty as a human finding without guessing labels.
            continue
        if not isinstance(assessment, dict):
            raise ValueError("Every certain target needs a comparison assessment")
        from workbench import taxonomy
        def valid_strings(value):
            return isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value)
        # subtypes must be a list of strings; an empty list is tolerated when
        # the catalog type requires a subtype and the model omitted it (see the
        # recoverable subtype-gap handling below). A malformed list still fails.
        subtype_gap_type = taxonomy.subtypes_required_but_missing(
            assessment.get("type"), assessment.get("subtypes"))
        if (not isinstance(assessment.get("type"), str)
                or not assessment["type"].strip()
                or not (valid_strings(assessment.get("subtypes")) or subtype_gap_type)):
            raise ValueError("Every review needs type and subtypes")
        if True:
            difficulty = assessment.get("difficulty")
            if round_id == "sample":
                if number is not None:
                    expected_numbers = {str(number)}
                else:
                    # 抽整组：组题按组内全部题号给组级难度。
                    expected_numbers = {str(n) for n in (unit.get("numbers") or [])}
            else:
                expected_numbers = {str(q["number"]) for q in plan["questions"] if q["unit_id"] == uid}
            if difficulty is not None and (not isinstance(difficulty, list) or not expected_numbers
                    or any(not isinstance(item, dict) or set(item) != {"number", "value"}
                           or not isinstance(item["number"], str)
                           or item["value"] not in ("容易", "适中", "困难") for item in difficulty)):
                raise ValueError("Label review needs independent difficulty for every question")
            numbers = [item["number"] for item in difficulty] if difficulty is not None else None
            if numbers is not None and (len(numbers) != len(set(numbers)) or set(numbers) != expected_numbers):
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
            labels = {"type": assessment["type"], "subtypes": assessment["subtypes"],
                      "knowledge": assessment["knowledge"], "subquestions": children,
                      "unit_kind": unit.get("unit_kind")}
            problems = taxonomy.check_question_labels(labels)
            # A model occasionally omits the catalog subtype for a type that
            # requires one (e.g. 书面表达). That is a recoverable label gap, not
            # a fatal invalid round: keep it out of the fatal problem list so the
            # remaining targets/rounds continue. The gap still surfaces as a
            # real "子类：原值 → 空" difference via evaluate_rounds/consensus and
            # enters the human queue. Any other structural problem (wrong or
            # unknown subtype, bad knowledge path, missing children) still fails.
            if taxonomy.subtypes_required_but_missing(assessment["type"], assessment["subtypes"]):
                problems = [p for p in problems if "必须填写目录子类" not in p]
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
                    key = (target["unit_id"],
                           None if target["number"] is None else str(target["number"]))
                else:
                    if not isinstance(target, str):
                        raise ValueError("Invalid unit partition target")
                    key = (target, None)
                if key in part_executions:
                    raise ValueError("Overlapping round partitions")
                part_executions[key] = part["execution_id"]
        if set(part_executions) | {_check_key(r, round_id) for r in record.get("reuse", [])} != set(expected):
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
            reused = next((ref for ref in record.get("reuse", []) if _check_key(ref, round_id) == (uid, num)), None)
            origin = _reuse_source(batch, reused, round_id, plan)[1] if reused else record
            origin_execution = next((p["execution_id"] for p in origin.get("parts", [])
                if ({"unit_id": uid, "number": num} if round_id == "sample" else uid) in p["targets"]), origin["execution_id"])
            source = {"round_id": round_id, "execution_id": origin_execution if reused else part_executions.get((uid, num), record["execution_id"]),
                      "input_binding": origin["input_binding"], "target_number": num,
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
    return _immutable_digest(stripped)


_IMMUTABLE_DIGEST_CACHE = {}
_IMMUTABLE_DIGEST_SEEN = {}


def _freeze(value):
    """Canonical immutable form so equal partition states share one cache entry."""
    if isinstance(value, dict):
        return ("o", tuple(sorted((str(k), _freeze(v)) for k, v in value.items())))
    if isinstance(value, (list, tuple)):
        return ("a", tuple(_freeze(v) for v in value))
    if isinstance(value, (str, int, float, bool)) or value is None:
        return ("s", value)
    return ("r", repr(value))


def _immutable_digest(value):
    """Digest a partition state once; identical states reuse the result.

    Frozen partition states are hashed for every part of every round and the
    same states recur within one request. Freezing is far cheaper than
    re-serializing to JSON, and the digest is a pure function of the frozen form.
    """
    key = _freeze(value)
    digest = _IMMUTABLE_DIGEST_CACHE.get(key)
    if digest is not None:
        return digest
    digest = _digest(value)
    if len(_IMMUTABLE_DIGEST_CACHE) > 8192:
        _IMMUTABLE_DIGEST_CACHE.clear()
    _IMMUTABLE_DIGEST_CACHE[key] = digest
    return digest


def _verify_round_sources(batch, plan, record, verify_reuse=True):
    """Verify v2 observations against their bound tasks and retained responses.

    The final receipt is written only after evaluation, so the durable install
    transaction is the authority for that part, including during recovery.
    """
    if (not isinstance(record, dict) or record.get("round_id") not in ROUND_IDS
            or not isinstance(plan.get("input_binding"), str)
            or record.get("input_binding") != plan["input_binding"]):
        raise ValueError("Review original round/input binding mismatch")
    reuse = record.get("reuse", [])
    if not isinstance(reuse, list):
        raise ValueError("Invalid review reuse references")
    reused_keys = {_check_key(ref, record["round_id"]) for ref in reuse}
    if len(reused_keys) != len(reuse):
        raise ValueError("Duplicate review reuse references")
    if verify_reuse:
        for ref in reuse:
            saved, _ = _reuse_source(batch, ref, record["round_id"], plan)
            if [c for c in record["checks"] if _check_key(c, record["round_id"]) == _check_key(ref, record["round_id"])] != [saved]:
                raise ValueError("Review reuse check differs from original response")
    fresh_checks = [c for c in record["checks"] if _check_key(c, record["round_id"]) not in reused_keys]
    if not fresh_checks:
        if not reuse or "parts" in record or record["execution_id"] != "reused-" + _digest(reuse):
            raise ValueError("Review has no original executed checks")
        return
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
    if (not isinstance(state, dict) or state.get("plan") != plan or state.get("round_id") != record["round_id"]
            or state.get("reuse", []) != record.get("reuse", [])):
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
                or binding.get("context_version") not in ("blind-context-v8", "blind-context-v9", "blind-context-v10", "blind-context-v11")
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
        normalized = ([dict(c, number=_sample_number(c)) for c in checks]
                      if isinstance(checks, list) and record["round_id"] == "sample" else checks)
        if not isinstance(checks, list) or (index < len(completed) and normalized != entry.get("checks")):
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
    if collected != fresh_checks:
        raise ValueError("Review checks differ from retained responses")


def _is_capability_gap(finding) -> bool:
    """This finding is only the model admitting it cannot see an image / hear audio.

    Such uncertainty is a capability boundary, not a label/basic difference, so it
    must not enter manual review or block the gate. A finding that actually names a
    label/answer conflict still counts even if it also mentions an image.
    """
    sources = finding.get("round_sources") or []
    # compare() emits structured field/value observations only after proving a
    # real original/labels/sample difference. Reviewer prose about unseen media
    # cannot erase that program-derived disagreement, even with verdict="ok".
    if finding.get("field") in {"type", "subtypes", "knowledge"} and any(
            isinstance(s, dict) and s.get("field") == finding["field"] and "value" in s
            for s in sources):
        return False
    source = sources[0] if sources else {}
    text = " ".join(str(source.get("rationale") or "").split())
    reason = str(finding.get("reason") or "")
    blob = text + " " + reason
    if not _CAPABILITY_GAP.search(blob):
        return False
    conflict_text = (text.replace("不矛盾", "")
                     .replace("无矛盾", "")
                     .replace("未发现标签冲突", ""))
    if _LABEL_CONFLICT.search(conflict_text):
        return False
    if source.get("verdict") == "error":
        return False
    return True


COMPARISON_SNAPSHOT_FILE = "07_review/review-comparison.json"


def write_comparison_snapshot(batch_dir, payload=None) -> dict | None:
    """Atomically persist the last fully executed comparison for UI consumption.

    This snapshot is deliberately independent of the current review binding: a
    later rerun may be in progress or stale while the UI must continue showing
    the last successful original/labels/sample comparison. A newly completed
    review replaces it atomically.
    """
    batch = Path(batch_dir)
    payload = payload if isinstance(payload, dict) else _load(batch / REVIEW_DIR / ROUND_FILE)
    if not isinstance(payload, dict) or not isinstance(payload.get("plan"), dict):
        return None
    records = payload.get("rounds")
    if not isinstance(records, list) or {r.get("round_id") for r in records if isinstance(r, dict)} != set(ROUND_IDS):
        return None
    try:
        plan, _ = resolve_round_plan(batch, payload=payload)
        evaluated = evaluate_rounds(batch, payload)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not evaluated.get("execution_completed") or set(evaluated.get("checked_rounds", [])) != set(ROUND_IDS):
        return None
    def read(relative):
        path = batch / relative
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return {}
    snapshot = {
        "version": 1,
        "input_binding": plan.get("input_binding"),
        "input_hashes": plan.get("input_hashes", {}),
        "plan": plan,
        "original": {
            "types": read("03_types/types.json"),
            "knowledge": read("04_knowledge/knowledge.json"),
            "answers": read("01_source_ans/answers.json"),
        },
        "rounds": records,
        "execution_completed": True,
        "quality_passed": bool(evaluated.get("passed")),
        "saved_at": time.time(),
    }
    target = batch / COMPARISON_SNAPSHOT_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    temporary.replace(target)
    return snapshot


_EVALUATE_CACHE = {}


def evaluate(batch_dir) -> dict:
    """Memoize the review verdict by the review directory fingerprint.

    Callers (overview, upload state, review gate) evaluate the same directory
    several times per refresh. The result is a pure function of the files read,
    so an unchanged directory returns the cached verdict; any file edit changes
    the key and forces a real re-evaluation.
    """
    batch = Path(batch_dir)
    key = (str(batch), _review_dir_key(batch, None))
    hit = _EVALUATE_CACHE.get(key)
    if hit is not None:
        return hit
    result = _evaluate_uncached(batch)
    if len(_EVALUATE_CACHE) > 32:
        _EVALUATE_CACHE.clear()
    _EVALUATE_CACHE[key] = result
    return result


def _review_dir_key(batch, payload):
    review_dir = batch / REVIEW_DIR
    parts = []
    names = sorted({p.name for p in review_dir.glob("*.json")}) if review_dir.is_dir() else []
    for name in names:
        parts.append((name, _file_digest(review_dir / name)))
    # Everything the verdict depends on must invalidate it: bound artifacts,
    # review rules, and every task binding/partition/transaction written under
    # workflow/. Scan those directories so a new or edited artifact is never
    # masked by a stale cache (tests and live runs both rely on this).
    for relative in (*ROUND_INPUTS, "01_source/structure.confirmed.json"):
        parts.append((relative, _file_digest(batch / relative)))
    root = Path(__file__).resolve().parents[1]
    for relative in ROUND_RULES:
        parts.append(("rule:" + relative, _file_digest(root / relative)))
    for directory in ("workflow/review-bindings", "workflow/review-transactions",
                      "workflow/review-partitions", "workflow"):
        target = batch / directory
        if not target.is_dir():
            parts.append((directory, "missing"))
            continue
        for path in sorted(target.glob("*.json")):
            parts.append((str(path.relative_to(batch)), _file_digest(path)))
    if payload is not None:
        try:
            parts.append(("payload", _digest(payload)))
        except (TypeError, ValueError):
            parts.append(("payload", "unhashable"))
    return _digest(parts)


def evaluate_rounds(batch_dir, payload=None) -> dict:
    """Fail closed on missing/stale/invalid rounds; emit no invented review data.

    No result memoization: any cached verdict risks hiding changed evidence, and
    this path is not the hot spot for the review page (the plan/digest/load caches
    below are). Keeping it a pure direct call is safer and still fast enough.
    """
    batch = Path(batch_dir)
    errors, findings, checked = [], [], []
    try:
        plan = prepare_round_plan(batch)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return {"passed": False, "execution_completed": False, "errors": [_round_issue(r, str(exc)) for r in ROUND_IDS],
                "unchecked": [_round_issue(r, str(exc)) for r in ROUND_IDS], "findings": [], "checked_rounds": []}
    if payload is None:
        payload = _load(batch / REVIEW_DIR / ROUND_FILE)
    plan, compatibility = resolve_round_plan(batch, plan, payload)
    if isinstance(payload, dict) and payload.get("plan") != plan:
        payload = rebase_rounds(batch, payload, plan)
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
            for ref in record.get("reuse", []):
                origin = _reuse_source(batch, ref, round_id, plan)[1]
                round_executions.add(origin["execution_id"])
                round_executions.update(p["execution_id"] for p in origin.get("parts", []))
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
    # 用户规则：只有“与原标注不一致”才送人工。抽查/一审模型常因看不到图片占位
    # 而给出 uncertain，但它的判断和原标签完全一致——这种不是标签分歧，不应进
    # 人工队列（也不会阻断闸门）。真正的差异/错误仍然保留为待审发现。
    # 能力受限（看不到图/听不到音频）不是标签差异：只作提示，不进人工、不阻断。
    informational_findings = [f for f in findings if _is_capability_gap(f)]
    # A review snapshot can legitimately be older than the current formal batch
    # after a label correction. Do not project that historical comparison back
    # into the current manual queue; keep real current-vs-review differences.
    # 一审/二审与原标签不一致就是差异，必须进待审核；不能因为“审核判断与当前
    # 正式标签不同”就把它当过期结论丢掉（那正好丢掉了真实分歧）。
    findings = [f for f in findings if not _is_capability_gap(f)]
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
                entry = {'code': 'REVIEW_SAMPLE_SOURCE_UNVERIFIED', 'round_id': 'sample',
                         'unit_id': check['unit_id'], 'number': number,
                         'field': 'review_rounds.sample.source', 'required': True,
                         'status': 'unchecked', 'message': proof['reason'],
                         'reason': proof['reason'], 'source_binding': proof,
                         'evidence': check['evidence']}
                # 选项是图片等导致“无法用文字证明图文对应”，属能力受限，不阻断人工。
                if _CAPABILITY_GAP.search(" ".join(str(check.get('rationale') or '').split())) and not _LABEL_CONFLICT.search(str(check.get('rationale') or '')):
                    informational_findings.append(entry)
                else:
                    errors.append(entry)
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
            "informational": informational_findings,
            "compatibility": compatibility,
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
