#!/usr/bin/env python3
"""Read-only comparison to explicit independent answers; never solves questions.

JSON supports a numeric answer map, {answers: map/list}, or explicit
{number,answer} rows (including grouped subquestions). An answer array is a
multi-part answer, not evidence for alternative acceptable answers. TXT/DOCX
must provide explicit numbered answers: 40.D, 40: D, or 81. followed by writing
text. Scoped batches use question-ID maps or explicitly scoped rows; repeated
printed numbers without source identity fail rather than bind by order.
Ambiguous duplicate numbers, range tables and unnumbered keys fail.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workflow.docx_extract import extract_docx
from workflow.question_identity import VERSION as IDENTITY_VERSION, question_key


class AnswerError(ValueError):
    pass


def number(value):
    if isinstance(value, bool) or not re.fullmatch(r"\d+", str(value)) or int(value) < 1:
        raise AnswerError(f"非法题号：{value!r}")
    return str(int(value))


def normalize(value):
    if isinstance(value, list):
        if not value:
            raise AnswerError("答案数组不能为空")
        parts = [normalize(v) for v in value]
        return parts[0] if len(parts) == 1 else tuple(parts)
    if value is None or (isinstance(value, str) and not value.strip()):
        # 书面表达等题型原卷无“答案”字段（缺省/None/空串）。这不是格式错误，
        # 而是"该题没有可核对的独立答案"，按无答案处理，避免整卷体检判成输入错误。
        return None
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise AnswerError("答案必须是非空文字或非空答案数组")
    text = html.unescape(re.sub(r"<[^>]*>|\*\*", "", str(value)))
    text = re.sub(r"\s+", "", text).strip()
    if not text:
        raise AnswerError("答案不能为空")
    # Choice-letter typography may differ; prose punctuation/words must agree.
    if re.fullmatch(r"[（(]?[A-Ha-h][）).．、]?", text):
        return re.sub(r"[^A-Ha-h]", "", text).upper()
    return text


def collect_json(data, *, scoped=False):
    answers = {}
    # 正式“无答案”决定：原卷/答案材料确实没有答案时，答案阶段会为每个确认题号
    # 写 missing，而 answers 为空。它不是输入格式错误（硬规则5 空白语义 + 规则22），
    # 体检应据此判定“无独立答案可比”，不能报 ANSWER_INPUT_ERROR。
    if (isinstance(data, dict) and not data.get("answers")
            and isinstance(data.get("missing"), dict) and data["missing"]):
        return {}

    def put(n, answer):
        key = str(n) if scoped else number(n)
        if scoped and not re.fullmatch(r"[A-Za-z0-9_-]+", key):
            raise AnswerError(f"非法 question ID：{key!r}")
        normalized = normalize(answer)
        if key in answers:
            if scoped and key.isdigit():
                raise AnswerError(f"ANSWER_SOURCE_SCOPE_UNBOUND：原小题号 {key} 重复且缺少来源身份，不按顺序猜测")
            raise AnswerError(f"答案题号重复或冲突：{key}")
        answers[key] = {"value": answer, "normalized": normalized}

    def visit(value, owner=None):
        if isinstance(value, dict):
            if scoped and value.get("identity_version") == IDENTITY_VERSION:
                owner = value
            children = value.get("subquestions")
            if children:
                visit(children, owner)
            elif scoped and "question_id" in value and "answer" in value:
                put(value["question_id"], value["answer"])
            elif "number" in value and "answer" in value:
                key = question_key(owner, number(value["number"])) if owner is not None else value["number"]
                put(key, value["answer"])
            elif "answers" in value:
                visit(value["answers"])
            elif "items" in value:
                visit(value["items"])
            elif value and all(re.fullmatch(r"[A-Za-z0-9_-]+" if scoped else r"\d+", str(key)) for key in value):
                for key, answer in value.items():
                    put(key, answer)
            else:
                raise AnswerError("答案 JSON 需要题号 map、answers 或 number/answer 数组")
        elif isinstance(value, list) and value:
            if all(isinstance(row, dict) for row in value):
                for row in value:
                    visit(row, owner)
            else:
                # A flat source array is an explicit one-based answer sequence.
                for index, answer in enumerate(value, 1):
                    put(index, answer)
        else:
            raise AnswerError("独立答案内容为空或格式不明")

    visit(data)
    if not answers:
        raise AnswerError("未找到任何带题号的独立答案")
    return answers


def parse_text(text, *, scoped=False):
    text = re.sub(r"<[^>]*>|\*\*", "", text).replace("\r\n", "\n")
    # Each token starts at line/space boundaries. Numbered prose in a writing
    # answer remains ambiguous and is deliberately rejected via coverage.
    pattern = re.compile(r"(?:^|(?<=\s))(\d{1,4})\s*[.．、:：)]\s*", re.M)
    matches = list(pattern.finditer(text))
    if not matches:
        raise AnswerError("答案文字未找到明确的题号；不按位置猜测")
    rows = []
    for index, match in enumerate(matches):
        tail = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        answer = text[match.end():tail].strip()
        # A label alone followed by a choice string/answer table is not a key.
        if re.match(r"(?:答案|解析|参考答案)\s*[:：]?\s*$", answer):
            raise AnswerError(f"第{match.group(1)}题只有栏目名，缺少独立答案")
        rows.append({"number": match.group(1), "answer": answer})
    return collect_json(rows, scoped=scoped)


def load_answers(path, *, scoped=False):
    if path.suffix.lower() == ".json":
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise AnswerError(f"JSON键重复：{key}")
                result[key] = value
            return result
        return collect_json(json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique), scoped=scoped)
    if path.suffix.lower() == ".docx":
        with tempfile.TemporaryDirectory(prefix="answer-docx-") as folder:
            result = extract_docx(path, Path(folder))
        if result.get("warnings"):
            raise AnswerError("DOCX答案提取存在未核对警告：" + "; ".join(result["warnings"]))
        if "[TABLE" in result["text"] or "[IMG " in result["text"]:
            raise AnswerError("DOCX答案含表格/图片，须先核对并提供明确题号的答案JSON")
        return parse_text(result["text"], scoped=scoped)
    if path.suffix.lower() == ".txt":
        return parse_text(path.read_text(encoding="utf-8-sig"), scoped=scoped)
    raise AnswerError("独立答案仅支持 JSON、带明确题号的 TXT 或原生 DOCX")


def _writing_numbers(batch):
    """书面表达没有确定答案：范文可有可无，不参与答案覆盖/一致性核对。"""
    result = set()
    for item in batch.get("items") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("question_type") or item.get("type") or "").strip() != "书面表达":
            continue
        nums = item.get("numbers") or ([item.get("number")] if item.get("number") is not None else [])
        for n in nums:
            try:
                result.add(number(n))
            except AnswerError:
                pass
    return result


def _scoped(batch):
    return batch.get("identity_version") == IDENTITY_VERSION or any(
        isinstance(item, dict) and item.get("identity_version") == IDENTITY_VERSION
        for item in batch.get("items") or [])


def _inspect_scoped(batch, answers):
    actual, owners, writing = {}, {}, set()
    for item in batch.get("items") or []:
        if not isinstance(item, dict) or item.get("identity_version") != IDENTITY_VERSION:
            raise AnswerError("scoped 批次含缺少身份的题目，不能回退全局题号")
        nums = item.get("numbers") or ([item.get("number")] if item.get("number") is not None else [])
        children = item.get("subquestions") or []
        rows = {number(child.get("number")): child for child in children}
        expected = {number(n) for n in nums}
        if len(expected) != len(nums) or len(rows) != len(children):
            raise AnswerError("scoped 单元内小题号重复")
        if set(item.get("question_ids") or {}) != expected or (children and set(rows) != expected):
            raise AnswerError("scoped 单元题号、子题与 question ID 映射不一致")
        for n in nums:
            printed = number(n)
            qid = question_key(item, printed)
            if qid in actual:
                raise AnswerError(f"question ID 重复：{qid}")
            row = rows.get(printed) if children else item
            if row is None:
                raise AnswerError(f"{qid}：缺少本单元原号 {printed} 的小题")
            if row.get("question_id") not in (None, qid):
                raise AnswerError(f"{qid}：子题身份与单元映射不一致")
            actual[qid] = {"value": row.get("answer"), "normalized": normalize(row.get("answer"))}
            owners[qid] = {"unit_id": item.get("unit_id") or item.get("id"),
                           "question_id": qid, "number": int(printed)}
            if str(item.get("question_type") or item.get("type") or "").strip() == "书面表达":
                writing.add(qid)
    if not actual:
        raise AnswerError("scoped 批次未找到题目")
    numeric_owners = {}
    for qid, owner in owners.items():
        numeric_owners.setdefault(str(owner["number"]), []).append(qid)
    source, issues, unbound = {}, [], set()
    for key, entry in answers.items():
        if key in actual:
            if key in source:
                raise AnswerError(f"独立答案对同一 question ID 重复绑定：{key}")
            source[key] = entry
        elif str(key).isdigit() and str(int(key)) in numeric_owners:
            matches = numeric_owners[str(int(key))]
            if len(matches) != 1:
                unbound.update(matches)
                issues.append({"code": "ANSWER_SOURCE_SCOPE_UNBOUND", "number": int(key),
                               "message": "独立答案仅有原小题号，跨大题同号缺少来源身份；不能猜测对应关系"})
            elif matches[0] in source:
                raise AnswerError(f"独立答案对同一 question ID 重复绑定：{matches[0]}")
            else:
                source[matches[0]] = entry
        else:
            issues.append({"code": "ANSWER_EXTRA_NUMBERS", "question_id": str(key),
                           "message": "独立答案含批次外身份，须确认是否匹配原卷"})
    for qid, entry in actual.items():
        if qid in writing or qid in unbound:
            continue
        if entry["normalized"] is None or str(entry["value"] or "").strip() in {"无答案", "无解析"}:
            continue
        if qid not in source:
            issues.append(dict(owners[qid], code="ANSWER_SOURCE_MISSING", message="独立答案未覆盖此题身份"))
        elif entry["normalized"] != source[qid]["normalized"]:
            issues.append(dict(owners[qid], code="ANSWER_MISMATCH", message="正式答案与独立答案不一致",
                               candidate=entry["value"], source=source[qid]["value"]))
    return {"passed": not issues, "issues": issues, "checked_numbers": [o["number"] for o in owners.values()],
            "checked_question_ids": list(actual), "independent_source_required": True, "solved_questions": False}


def inspect(batch, answers):
    if _scoped(batch):
        return _inspect_scoped(batch, answers)
    actual = collect_json({"items": batch.get("items")})
    issues = []
    writing = _writing_numbers(batch)
    placeholders = {"无答案", "无解析"}
    for n in sorted(actual, key=int):
        if n in writing:
            continue
        entry = actual[n]
        # 以规范化结果判定"有没有答案"：None/空白/「无答案」都代表该题没有可
        # 核对的答案，不能要求独立答案覆盖，否则书面表达这类题会让整卷失败。
        if entry.get("normalized") is None or str(entry.get("value") or "").strip() in placeholders:
            # 用户规则：缺答案填「无答案」，保留待补，不作为答案冲突阻断。
            # 书面表达等原卷无独立答案时此字段为 None，同理不阻断。
            continue
        if n not in answers:
            issues.append({"code": "ANSWER_SOURCE_MISSING", "number": int(n), "message": "独立答案未覆盖此题号"})
        elif actual[n]["normalized"] != answers[n]["normalized"]:
            issues.append({"code": "ANSWER_MISMATCH", "number": int(n), "message": "正式答案与独立答案不一致",
                           "candidate": actual[n]["value"], "source": answers[n]["value"]})
    extra = sorted(set(answers) - set(actual), key=int)
    if extra:
        issues.append({"code": "ANSWER_EXTRA_NUMBERS", "message": f"独立答案含批次外题号：{extra}；须确认是否匹配原卷"})
    return {"passed": not issues, "issues": issues, "checked_numbers": sorted(map(int, actual)),
            "independent_source_required": True, "solved_questions": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True, type=Path)
    parser.add_argument("--answers", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.answers is None:
            raise AnswerError("缺少 --answers 独立答案输入，不能通过答案核验")
        if args.batch.resolve(strict=True) == args.answers.resolve(strict=True):
            raise AnswerError("不能把待检查批次本身作为独立答案")
        batch = json.loads(args.batch.read_text(encoding="utf-8-sig"))
        report = inspect(batch, load_answers(args.answers, scoped=_scoped(batch)))
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report["passed"] else 1
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        code = "ANSWER_SOURCE_SCOPE_UNBOUND" if str(exc).startswith("ANSWER_SOURCE_SCOPE_UNBOUND") else "ANSWER_INPUT_ERROR"
        print(json.dumps({"passed": False, "issues": [{"code": code, "message": str(exc)}]}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
