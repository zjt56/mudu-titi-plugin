# -*- coding: utf-8 -*-
"""Conservative, text-only detection of an answer booklet supplied as a paper.

Pass the complete locally extracted text, before appendix splitting or denoising.
This is a diagnostic, not proof that the original pages contain no questions.
"""
from __future__ import annotations

import re

_TITLE = re.compile(r"(?:参考答案|答案\s*[及与和]\s*解析|答案解析|试题答案)")
_SUPPLEMENT = re.compile(r"听力(?:录音)?(?:材料|原文|录音稿|文本)|录音(?:材料|原文|稿)|听力稿|评分(?:标准|细则|规则|说明)|(?:作文|写作|书面表达)(?:参考)?范文|参考范文|范文")
_INDEX = re.compile(r"(?<![\w\d])(\d{1,3})\s*[.．、:：]\s*([A-H])(?![A-Za-z])")
_RANGE = re.compile(r"(?<!\d)(\d{1,3})\s*[-~～—–至]\s*(\d{1,3})\s*[.．、:：]?\s*([A-H]{2,})(?![A-Za-z])")
_OPTION = re.compile(r"(?:^|\s)([A-H])[.．、)）]\s*\S")
_QUESTION = re.compile(r"^\s*(?:[（(]\s*[）)]\s*)?\d{1,3}\s*[.．、)）]\s*(.+)")
_TASK = re.compile(r"(?:[?？]|请选择|选择正确|根据.{0,20}(?:回答|选择)|请.{0,15}(?:写|回答)|\b(?:what|which|where|when|why|how|who)\b)", re.I)
_ANSWER_START = re.compile(r"^(?:[A-H](?:\b|[.．、:：])|【(?:答案|解析|分析)】|答案\s*[:：])")


def detect_source_answer_only(raw_text: str) -> dict:
    """Return a non-mutating diagnostic with 1-based original-text evidence.

    A positive requires three independent signals and no recognizable question
    or option text. A negative is deliberately *not* a classification as a paper.
    """
    lines = raw_text.splitlines()
    titles, supplements, indices, questions, options = [], [], [], [], []
    for number, raw in enumerate(lines, 1):
        line = re.sub(r"<[^>]+>|\*\*", "", raw).strip()
        if not line:
            continue
        if _TITLE.search(line):
            titles.append(number)
        if _SUPPLEMENT.search(line):
            supplements.append(number)
        entries = {int(match[1]) for match in _INDEX.finditer(line)}
        for match in _RANGE.finditer(line):
            start, end, answers = int(match[1]), int(match[2]), match[3]
            if 1 <= start <= end <= 999 and end - start + 1 == len(answers):
                entries.update(range(start, end + 1))
        if entries:
            indices.append((number, entries))
        option_labels = {m[1] for m in _OPTION.finditer(line)}
        if option_labels:
            options.append((number, option_labels))
        match = _QUESTION.match(line)
        if match:
            body = match[1].strip()
            if not _ANSWER_START.match(body) and not re.match(r"^[MW]:\s", body) and _TASK.search(body):
                questions.append(number)
    unique_indices = set().union(*(item[1] for item in indices)) if indices else set()
    # Distinct answer entries must be locally dense, not scattered in a paper.
    dense = len(unique_indices) >= 6 and any(
        len(set().union(*(entry for line_no, entry in indices if start <= line_no <= start + 8))) >= 6
        for start, _ in indices
    )
    option_labels = set().union(*(item[1] for item in options)) if options else set()
    question_evidence = bool(questions) or len(option_labels) >= 2
    detected = bool(titles and supplements and dense and not question_evidence)
    return {
        "classification": "source_answer_only" if detected else "undetermined",
        "evidence": {
            "answer_heading_lines": titles,
            "answer_index_lines": [line for line, _ in indices],
            "answer_index_count": len(unique_indices),
            "supplement_lines": supplements,
            "question_prompt_lines": questions,
            "option_lines": [line for line, _ in options],
        },
        "message": ("完整提取文本呈现答案及解析、密集答案索引和答案附加材料，未识别到题干或选项；"
                    "疑似误上传答案卷，缺少试题，请提供原试卷并核对原页；不得据答案补造题目。"
                    if detected else "文字证据不足以判定仅有答案；这不代表试题完整。"),
        "scope": "full_extracted_text_only; not visual verification",
    }
