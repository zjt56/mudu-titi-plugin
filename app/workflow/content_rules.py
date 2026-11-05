"""子智能体1：解析匹配、文章大意匹配（缺失填占位，不生成）。

独立阶段 `explanations`，独立产物 `04_knowledge/explanations.json`，与知识点
子智能体（`knowledge`）分开。本模块只提供提示规则与校验，不执行模型、不写台账。
"""
from __future__ import annotations

import json
import re

AI_MARK = '（AI生成，待校对）'
SUMMARY_TYPES = frozenset(('阅读理解', '任务型阅读', '读写综合', '完形填空', '语法选择'))
LISTENING_FILLED = '答案由AI生成（AI生成，待校对）'
# 用户规则：试卷没有解析/文章大意时不再使用 AI 生成，直接跳过，对应字段一律填「无」。
# 历史批次里的其它占位写法仍按缺失识别，避免旧产物被误判为编造。
EMPTY_PLACEHOLDER = '无'
PLACEHOLDER_TEXTS = frozenset(('无', '待补充', '暂无', '暂无解析', '暂无文章大意', '原卷无解析', '原卷无文章大意'))
AI_MAX_CJK = 50
AI_MAX_WORDS = 50


def is_placeholder(text) -> bool:
    """缺失占位：明确标注"没有"，不作为可溯源内容，也不触发 AI 标记校验。"""
    value = str(text or '').strip()
    return value == '' or value in PLACEHOLDER_TEXTS


RULES = """Artifact: {items:[{unit_id,explanation:string,articleSummary:string,subquestions:[{number,explanation:string}],evidence:[nonempty location strings]}]}.
你只做匹配与定位，不判题型、不打知识点、不改答案、不改结构，也**不撰写、不生成、不推断**任何解析或文章大意。
1) 解析匹配：按真实题号、章节和材料边界，在原卷正文、附录、答案册中定位该题原解析，逐字保留（去掉“【解析】”等字段标题），evidence 写明来源文件与行号/题号。
2) 文章大意匹配：按实际篇章及其所属题号匹配原【文章大意】，逐字保留并记录来源位置；重复的 A/B 篇号不是唯一标识。大意只写组级一份，不在每个子题重复。
3) 缺失处理（用户规则）：确认已提供来源确实没有该题解析或该篇文章大意时，对应字段一律只填字符串「无」，直接跳过；严禁用 AI 生成或自行补写任何内容，也不加任何来源标记。
约束：有原解析/原大意时逐字复制，不得替换；不得串用其它材料的解析或大意（按题号与材料边界判定）。
听力不写内容解析：答案来自附录时 explanation 同样填「无」。
非听力题组每个子题各自填（缺失填「无」）；独立题缺失也填「无」；articleSummary 仅在需要大意的题型填写，缺失填「无」。"""


def _number(value):
    if isinstance(value, bool) or not str(value).isdigit() or int(value) < 1:
        raise ValueError(f"Invalid confirmed question number: {value!r}")
    return str(int(value))


def _coverage(payload, units):
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("Artifact must contain items array")
    rows = payload["items"]
    if any(not isinstance(r, dict) or not isinstance(r.get("unit_id"), str) for r in rows):
        raise ValueError("Every item needs a stable unit_id")
    ids = [r["unit_id"] for r in rows]
    if len(ids) != len(set(ids)) or set(ids) != {u["unit_id"] for u in units}:
        raise ValueError("Artifact must have unique exact confirmed unit coverage")
    return {r["unit_id"]: r for r in rows}


def _fields(obj, allowed, required=()):
    if not isinstance(obj, dict) or set(obj) - set(allowed) or set(required) - set(obj):
        raise ValueError(f"Invalid fields; allowed={sorted(allowed)}, required={sorted(required)}")


def _children(row, unit):
    children = row.get("subquestions")
    if not isinstance(children, list):
        raise ValueError("subquestions must be an array")
    numbers = [_number(c.get("number")) if isinstance(c, dict) else "" for c in children]
    expected = [_number(n) for n in unit["numbers"]] if unit["unit_kind"] == "group" else []
    if len(numbers) != len(set(numbers)) or set(numbers) != set(expected):
        raise ValueError("Subquestions must have exact confirmed child number coverage")
    return children


def _substantive(text, minimum):
    body = text.replace(AI_MARK, "").strip()
    return len(re.sub(r"\s|[^\w\u4e00-\u9fff]", "", body)) >= minimum


def _ai_within_limit(text):
    """AI 补写上限：中文 ≤50 字，英文 ≤50 词（取较大者判断）。"""
    body = text.replace(AI_MARK, "").strip()
    cjk = len(re.findall(r"[\u4e00-\u9fff]", body))
    words = len(re.findall(r"[A-Za-z][A-Za-z'-]*", body))
    return cjk <= AI_MAX_CJK and words <= AI_MAX_WORDS


def _check_generated(text, label, minimum):
    """标注只能出现在末尾；标注过的必须有实质内容且不超过上限。"""
    if AI_MARK in text and not text.endswith(AI_MARK):
        raise ValueError(f"{label}: AI marker must be the trailing suffix")
    if text.endswith(AI_MARK):
        if not _substantive(text, minimum):
            raise ValueError(f"{label}: generated content is too short or generic")
        if not _ai_within_limit(text):
            raise ValueError(f"{label}: AI-generated content must be within 50 Chinese characters/words")


def validate(payload, units, typed, batch):
    """校验子智能体1的产物；返回按 unit_id 索引的行，供下游搬运与比对。"""
    rows = _coverage(payload, units)
    prepared = []
    for unit in units:
        uid, row = unit["unit_id"], rows[unit["unit_id"]]
        qtype = typed[uid]["type"]
        listening = str(qtype).startswith("听力")
        _fields(row, ("unit_id", "explanation", "articleSummary", "subquestions", "evidence"),
                ("unit_id", "explanation", "subquestions", "evidence"))
        if not isinstance(row.get("evidence"), list) or not row["evidence"] or any(
                not isinstance(x, str) or not x.strip() for x in row["evidence"]):
            raise ValueError("evidence must be a nonempty list of nonempty strings")
        explanation = row["explanation"]
        summary = row.get("articleSummary", "")
        if not isinstance(explanation, str) or not isinstance(summary, str):
            raise ValueError("explanation and articleSummary must be strings")
        children = _children(row, unit)
        for child in children:
            _fields(child, ("number", "explanation", "evidence"), ("number", "explanation"))
            if not isinstance(child["explanation"], str):
                raise ValueError("child explanation must be a string")
            if not is_placeholder(child["explanation"]):
                _check_generated(child["explanation"], "child explanation", 12)
            if "evidence" in child and (not isinstance(child["evidence"], list)
                                        or any(not isinstance(x, str) or not x.strip() for x in child["evidence"])):
                raise ValueError("child evidence must be a list of nonempty strings")
        if not listening and not is_placeholder(explanation):
            _check_generated(explanation, "explanation", 12)
        if listening:
            if AI_MARK in explanation and explanation.strip() != LISTENING_FILLED:
                raise ValueError("Listening units may only carry the single AI answer note, not generated explanations")
        # 缺解析/缺大意填占位（用户要求）：占位视为缺失标记，不要求溯源，也不要求 AI 标记。
        if qtype in SUMMARY_TYPES and not is_placeholder(summary):
            _check_generated(summary, "articleSummary", 1)
        prepared.append(dict(row, question_type=qtype, numbers=unit["numbers"]))
    audit_ownership(batch, prepared)
    return rows


def audit_ownership(batch, rows):
    """复用 upload_content 的题号锚点与跨材料归属检查（只读）。"""
    from .upload_content import inspect_content
    report = inspect_content(batch, {"items": rows})
    if not report["passed"]:
        raise ValueError("Content source/ownership check failed: "
                         + json.dumps(report["errors"], ensure_ascii=False))


def preserve(row, upstream):
    """知识点/装配阶段只能逐字搬运子智能体1的解析与大意，不能改写。"""
    for key, default in (("explanation", ""), ("articleSummary", "")):
        if row.get(key, default) != upstream.get(key, default):
            raise ValueError(f"Knowledge/assemble must preserve the explanation artifact field {key}")
    prior = {_number(c["number"]): c for c in upstream.get("subquestions") or []}
    for child in row.get("subquestions") or []:
        number = _number(child.get("number"))
        if number not in prior:
            raise ValueError("Explanation artifact is missing this child")
        if child.get("explanation", "") != prior[number].get("explanation", ""):
            raise ValueError("Knowledge/assemble must preserve each child explanation verbatim")
