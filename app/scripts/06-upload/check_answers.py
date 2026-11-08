#!/usr/bin/env python3
"""Read-only comparison to explicit independent answers; never solves questions.

JSON supports a numeric answer map, {answers: map/list}, or explicit
{number,answer} rows (including grouped subquestions). An answer array is a
multi-part answer, not evidence for alternative acceptable answers. TXT/DOCX
must provide explicit numbered answers: 40.D, 40: D, or 81. followed by writing
text. Ambiguous duplicate numbers, range tables and unnumbered keys fail.
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


def collect_json(data):
    answers = {}

    def put(n, answer):
        key = number(n)
        normalized = normalize(answer)
        if key in answers:
            raise AnswerError(f"答案题号重复或冲突：{key}")
        answers[key] = {"value": answer, "normalized": normalized}

    def visit(value):
        if isinstance(value, dict):
            children = value.get("subquestions")
            if children:
                visit(children)
            elif "number" in value and "answer" in value:
                put(value["number"], value["answer"])
            elif "answers" in value:
                visit(value["answers"])
            elif "items" in value:
                visit(value["items"])
            elif value and all(re.fullmatch(r"\d+", str(key)) for key in value):
                for key, answer in value.items():
                    put(key, answer)
            else:
                raise AnswerError("答案 JSON 需要题号 map、answers 或 number/answer 数组")
        elif isinstance(value, list) and value:
            if all(isinstance(row, dict) for row in value):
                for row in value:
                    visit(row)
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


def parse_text(text):
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
    return collect_json(rows)


def load_answers(path):
    if path.suffix.lower() == ".json":
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise AnswerError(f"JSON键重复：{key}")
                result[key] = value
            return result
        return collect_json(json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique))
    if path.suffix.lower() == ".docx":
        with tempfile.TemporaryDirectory(prefix="answer-docx-") as folder:
            result = extract_docx(path, Path(folder))
        if result.get("warnings"):
            raise AnswerError("DOCX答案提取存在未核对警告：" + "; ".join(result["warnings"]))
        if "[TABLE" in result["text"] or "[IMG " in result["text"]:
            raise AnswerError("DOCX答案含表格/图片，须先核对并提供明确题号的答案JSON")
        return parse_text(result["text"])
    if path.suffix.lower() == ".txt":
        return parse_text(path.read_text(encoding="utf-8-sig"))
    raise AnswerError("独立答案仅支持 JSON、带明确题号的 TXT 或原生 DOCX")


def inspect(batch, answers):
    actual = collect_json({"items": batch.get("items")})
    issues = []
    placeholders = {"无答案", "无解析"}
    for n in sorted(actual, key=int):
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
        report = inspect(batch, load_answers(args.answers))
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report["passed"] else 1
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        print(json.dumps({"passed": False, "issues": [{"code": "ANSWER_INPUT_ERROR", "message": str(exc)}]}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
