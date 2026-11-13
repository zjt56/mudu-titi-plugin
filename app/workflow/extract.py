# -*- coding: utf-8 -*-
"""复合原卷提取：Word 原生读取、PDF 逐页路由、本地视觉 OCR，产出可追溯候选。"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workflow import manifest as mf

VERSION = "composite-extract-1"
QUESTION = re.compile(r"^\s*(?:[（(]\s*[）)]\s*)?(\d{1,4})\s*[.．、)]")
HEADING = re.compile(r"^\s*([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫIVX]+|[一二三四五六七八九十]+|\d+)\s*[.．、]\s*(.+)")
ANSWER_HEADING = re.compile(r"^\s*【?\s*(?:参考答案及解析|参考答案与解析|答案与解析|答案及解析|参考答案|选择题答案|答案速查表|答案速查|答案)\s*】?\s*[:：]?\s*$")
TYPE_HINTS = (
    ("关键词语选择", "听力关键词语选择"), ("短对话理解", "听力短对话理解"),
    ("长对话理解", "听力长对话理解"), ("短文理解", "听力短文理解"),
    ("信息转换", "听力信息转换"), ("教材语篇", "教材语篇填空"),
    ("任务型阅读", "任务型阅读"), ("读写综合", "读写综合"),
    ("完形填空", "完形填空"), ("语法选择", "语法选择"),
    ("阅读理解", "阅读理解"), ("补全对话", "补全对话"),
    ("单项填空", "单项填空"), ("单项选择", "单项填空"),
    ("单词拼写", "单词拼写"), ("词汇运用", "单词拼写"),
    ("完成句子", "完成句子"), ("选词填空", "选词填空"),
    ("短文填空", "短文填空"), ("书面表达", "书面表达"),
    ("情景运用", "情景运用"), ("翻译", "翻译"), ("语音", "语音题"),
)
GROUP_TYPES = {"完形填空", "语法选择", "阅读理解", "任务型阅读", "读写综合", "补全对话",
               "听力长对话理解", "听力短文理解", "听力信息转换", "选词填空", "短文填空", "教材语篇填空"}


def _plain(line):
    return re.sub(r"</?(?:u|sup|sub)>", "", line).replace("**", "")


def blank_numbers(line: str) -> list[int]:
    """Read numbers that sit inside an underline/blank, tolerating NBSP padding.

    Word exports sometimes pad the blank with ``\xa0`` instead of the ideographic
    space, so a blank like ``<u>\u300066\xa0</u>`` must still be recognized.
    """
    normalized = line.replace("\xa0", "\u3000")
    return [int(n) for n in re.findall(
        r"(?:_{2,}|\u3000+)\s*(\d{1,3})\s*(?=_{2,}|\u3000)", normalized)]


# 对话补全题的小题号常带说话人前缀（`C:1.<u>…`），行首不是数字。
DIALOGUE_QUESTION = re.compile(r"^\s*[A-Z]{1,2}\s*[:：]\s*(\d{1,4})\s*[.．、)]")
# 同一行并排两个小题（`1.U　v (　　)　　　2.W　x (　　)`）：题号前面是空白、后面紧跟
# 文字/括号/图片标记。只在这种「一行多小题」的版面里追加，避免把题干里的数字当题号。
INLINE_ITEM = re.compile(r"(?:^|[\s\u3000])(\d{1,4})\s*[.．、]\s*(?=[A-Za-z(\[（])")


def same_line_numbers(line: str, first: int) -> list[int]:
    """读出与首个小题并排在同一行的后续小题号。

    语音/判断类同步练习把 8 个小题排成 4 行、每行 2 个；只认行首会让 2/4/6/8
    完全丢失，结构闸门随即报「题号未归属」。这里要求该行确实有成组空档
    （括号或下划线），且题号严格递增，避免误读题干中的数字。
    """
    if not re.search(r"(?:_{2,}|\u3000{2,}|[(（]\s*[)）])", line):
        return []
    # Match the same real column boundaries as scoped source reconciliation.
    # A single prose space before an age (``She is 15.She ...``) is not a column.
    from workflow.source_anchors import printed_tokens
    extra: list[int] = []
    for _offset, number in printed_tokens(line):
        if number in extra or number == first and not extra:
            continue
        if number <= 0:
            continue
        extra.append(number)
    if not extra:
        return []
    sequence = [first, *extra]
    if sequence != sorted(sequence) or len(set(sequence)) != len(sequence):
        return []
    return extra


def guide_numbers(line: str) -> list[int]:
    """Read only explicit answering ranges/lists in a group lead-in."""
    match = re.search(r"回答\s*第?\s*([\d\s、,，和及至到\-—–～~第]+)\s*(?:小)?题", _plain(line))
    if not match:
        return []
    value = match.group(1)
    nums = [int(n) for n in re.findall(r"\d+", value)]
    if len(nums) == 2 and re.search(r"至|到|[\-—–～~]", value):
        return list(range(nums[0], nums[1] + 1)) if nums[0] <= nums[1] else []
    return nums


_CN_COUNT = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _declared_count(line: str) -> int | None:
    """Read Arabic or Chinese counts in section/instruction lines."""
    plain = _plain(line)
    match = re.search(r"(?:共\s*)?(\d+)\s*小题", plain)
    if match:
        return int(match.group(1))
    match = re.search(r"([一二两三四五六七八九十])\s*个\s*小题", plain)
    return _CN_COUNT.get(match.group(1)) if match else None


def material_label(line: str) -> bool:
    # 段落标签可能带插图，如 `A[IMG image11.jpeg]`：去掉尾部图片标记再判定。
    text = re.sub(r"\[IMG[^\]]*\]", "", _plain(line)).strip()
    return bool(re.fullmatch(r"[A-H][.．、]?", text))


def material_start(lines: list[str], index: int) -> int:
    """A new article's answering instruction may precede its A/B/F label."""
    cursor = index - 1
    while cursor >= 0 and not lines[cursor].strip():
        cursor -= 1
    if cursor >= 0 and re.match(r"^\s*(?:阅读下列|阅读下面|根据短文|根据材料|根据对话|从方框内)", _plain(lines[cursor])):
        return cursor
    return index


def source_group_partitions(lines: list[str], numbers: list[int]) -> list[dict]:
    """Locate complete source-backed partitions, even for legacy structures."""
    wanted = set(numbers)
    boundaries = [material_start(lines, i) if material_label(line) else i
                  for i, line in enumerate(lines) if material_label(line) or guide_numbers(line)]
    parts = []
    for offset, start in enumerate(boundaries):
        end = boundaries[offset + 1] if offset + 1 < len(boundaries) else len(lines)
        for cursor in range(start + 1, end):
            heading = HEADING.match(_plain(lines[cursor]).strip())
            if heading and any(hint in heading.group(2) for hint, _ in TYPE_HINTS):
                end = cursor
                break
        found = set()
        for line in lines[start + 1:end]:
            plain = _plain(line)
            match = QUESTION.match(plain)
            if match:
                found.add(int(match.group(1)))
            found.update(blank_numbers(plain))
        members = sorted(found & wanted)
        refs = guide_numbers(lines[start])
        if members and found.issubset(wanted) and (not refs or set(refs) == set(members)):
            parts.append({"questions": members, "source_start_line": start + 1,
                          "source_end_line": end, "boundary_method": "group_leadin" if refs else "material_heading"})
    flattened = [n for part in parts for n in part["questions"]]
    return parts if set(flattened) == wanted and len(flattened) == len(wanted) else []


def looks_like_answers(lines: list[str]) -> bool:
    """Duplicate headline tail is an answer key when it opens with number+letter rows or 解析."""
    head = [(_plain(line)).strip() for line in lines[:30] if line.strip()]
    if not head:
        return False
    answer_row = re.compile(r"^\s*\d+\s*[~～\-–—]?\s*\d*\s*[.:：]?\s*[A-H]{2,}\b")
    explain = re.compile(r"【解析】|【答案】|【分析】|参考答案|答案与解析")
    hits = sum(1 for line in head if answer_row.search(line) or explain.search(line))
    return hits >= 2


def _next_unseen_heading(lines: list[str], start: int, seen: set) -> int | None:
    """重复大题之后，第一个还没在正文里出现过的大题标题位置。"""
    for index in range(start, len(lines)):
        heading = HEADING.match(_plain(lines[index]).strip())
        if not heading:
            continue
        kind = next((name for hint, name in TYPE_HINTS if hint in heading.group(2)), None)
        if kind and (heading.group(1), kind) not in seen:
            return index
    return None


def _question_body_follows(lines: list[str], start: int, window: int = 40) -> bool:
    """标题之后是否出现“题号 + 至少两个选项”的真实题目（纯答案行不算）。"""
    tail = lines[start:start + window]
    for offset, raw in enumerate(tail):
        if not QUESTION.match(_plain(raw).strip()):
            continue
        for follow in tail[offset + 1:offset + 6]:
            if len(re.findall(r"[A-H][.．]\s*\S", _plain(follow).strip())) >= 2:
                return True
    return False


def _is_inline_answer_key(lines: list[str], start: int, next_new: int) -> bool:
    """重复大题后是分段答案、随后又有新大题：答案收进附录，正文继续解析。"""
    block = [line.strip() for line in lines[start:next_new] if line.strip()]
    if not block or len(block) > 40:
        return False
    if not looks_like_answers(lines[start:next_new]):
        return False
    return _question_body_follows(lines, next_new)


def _is_answer_explanation_line(line: str) -> bool:
    """解析版逐题答案行：`31.B 【解析】…` 或 `96.useful 97.duty…`，不是新题。"""
    # 选项行（A.… B.… C.…）永远不是“一行多题答案”。例如
    # “A.Between 8 and 12.  B.Between 11 and 15.  C.Between 12 and 15.”
    # 里面的 12. 15. 会被旧正则误判成题号，进而把整行选项删掉。
    if re.match(r"^\s*[A-Ha-h]\s*[.．、)]", line):
        return False
    # 逐题解析：题号开头、同行带【解析】/【答案】等标记（不要求紧跟选项字母）。
    if (re.match(r"^\s*\d{1,4}\s*[.．、]", line)
            and re.search(r"【解析】|【答案】|【分析】|参考答案", line)):
        return True
    # 一行多个“题号.答案”（96.useful 97.duty 98.brave）是答案汇总行，不是题干。
    # 必须以题号开头；否则只是选项/正文里恰好出现“12. B”这类文本。
    return (bool(re.match(r"^\s*\d{1,4}\s*[.．、]", line))
            and len(re.findall(r"(?:^|\s)\d{1,4}\s*[.．]\s*[A-Za-z(（]", line)) >= 2)


def _is_answer_line_for_body(line: str) -> bool:
    """正文里夹带的答案行：逐题解析行，或 `71~75 BFEDG` 这类题号区间汇总行。"""
    if _is_answer_explanation_line(line):
        return True
    return bool(re.match(r"^\s*\d{1,4}\s*[~～\-–—]\s*\d{1,4}\s*[A-H]{2,}\s*$", line))


def _is_numbered_letter_answer(line: str, seen_numbers: set) -> bool:
    """`1.A`/`2.B` 这种同题号字母答案：只有该题号前面已出现过题目时才算答案。

    避免把极端的“题干本身只有一个字母”的题（如某些听力标记行）误删。
    """
    match = QUESTION.match(line)
    if not match or int(match.group(1)) not in seen_numbers:
        return False
    rest = _plain(line)[match.end():].strip()
    return bool(re.fullmatch(r"[A-Ha-h]\s*[.．]?", rest))


def _is_inline_answer_after_question(line: str, previous: str) -> bool:
    """同一题号紧跟在题目下一行的答案：`91.Mary…` 后的 `91.nature`。

    只认“上一行正文就是同题号题目、当前行没有问句/选项/填空”这种
    解析版最常见的答案写法；长度不限（阅读理解答案常常是一整句）。
    其他情况保持原样，交给结构闸门或 AI 复核。
    """
    current = QUESTION.match(line)
    prior = QUESTION.match(previous or "")
    if not current or not prior or current.group(1) != prior.group(1):
        return False
    rest = _plain(line)[current.end():].strip()
    if not rest or len(rest) > 200:
        return False
    if re.search(r"[?？]|\t|(?:^|\s)[A-H][.．]\s*\S", rest):
        return False
    if re.search(r"<u>|___|\u3000{2,}", line):
        return False
    return bool(re.search(r"[A-Za-z0-9\u4e00-\u9fff]", rest))


def _title_key(line: str) -> str:
    plain = re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", _plain(line)))
    # 卷名行可能带列表前缀（如 **1**.**2017**年…），那不是题号，也不能让文末重复卷名漏删。
    return re.sub(r"^\d{1,3}[.．、]", "", plain)


def _is_trailing_answer_block(lines: list[str], start: int, seen_headings=None) -> bool:
    """“参考答案/答案”标题之后是否真的是整段答案，而不是正文里的一句标签。

    条件：后面不再出现新的大题标题；至少出现 2 个编号/区间答案行。这样论文中间
    的“答案”字样（后面还有新大题）不会被误切，文末整段答案会稳定归入附录。
    """
    tail = [line for line in lines[start + 1:start + 60] if _plain(line).strip()]
    if len(tail) < 2:
        return False
    for raw in tail:
        plain = _plain(raw).strip()
        heading = HEADING.match(plain)
        if heading:
            kind = next((name for hint, name in TYPE_HINTS if hint in heading.group(2)), None)
            # 答案册常重复已有大题标题；只有“没出现过的新大题”才说明后面还是试卷正文。
            if kind and (heading.group(1), kind) not in (seen_headings or set()):
                return False
    ordinal_answer = re.compile(
        r"^\s*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]+|[一二三四五六七八九十]+)\s*[.．、]\s*"
        r"\d{1,4}(?:\s*[~～\-–—]\s*\d{1,4})?\s*[.．、]?\s*\S")
    hits = 0
    for raw in tail:
        plain = _plain(raw).strip()
        if QUESTION.match(plain) or re.match(r"^\s*\d{1,4}\s*[~～\-–—]\s*\d{1,4}\b", plain):
            hits += 1
        elif re.match(r"^\s*\d{1,4}\s*[.．、]\s*[A-Ha-h]\s*$", plain):
            hits += 1
        # 同步练习答案册常以“罗马序号 + 行内答案”成行（Ⅰ.1.Bb;Dd…、Ⅴ.1~5 BACDD）。
        elif ordinal_answer.match(plain):
            hits += 1
    return hits >= 2


def _answer_summary_follows(lines: list[str]) -> bool:
    """Require an adjacent question-number/answer table, not later answer-like prose."""
    rows = iter(line.strip() for line in lines if line.strip())
    first = next(rows, "")
    if re.fullmatch(r"\[TABLE\s+\d+\]", first):
        first = next(rows, "")
    header = [_plain(cell).strip() for cell in first.strip("|").split("|")]
    if len(header) < 3 or header[0] != "题号":
        return False
    counts = []
    for cell in header[1:]:
        match = re.fullmatch(r"(\d+)\s*(?:[~～\-–—至]\s*(\d+))?", cell)
        if not match:
            return False
        start, end = int(match[1]), int(match[2] or match[1])
        if start < 1 or end < start:
            return False
        counts.append(end - start + 1)
    answer = next(rows, "")
    # Markdown table exports may insert a separator between header and answers.
    if re.fullmatch(r"[\s|:\-]+", answer):
        answer = next(rows, "")
    cells = [_plain(cell).strip() for cell in answer.strip("|").split("|")]
    return (len(cells) == len(header) and cells[0] == "答案"
            and all(re.fullmatch(r"[A-H]+", cell) and len(cell) == count
                    for cell, count in zip(cells[1:], counts)))


def infer_structure(text: str, skip_lines=None) -> tuple[dict, str, str]:
    """只生成证据明确的题号/分区候选；推断题号、材料边界列入 unresolved。

    `skip_lines`：已由 AI/规则确认的答案或解析行（1 基行号）。这些行不参与
    题号统计，正文循环也会跳过，最后统一并入附录。
    """
    lines = text.splitlines()
    from workflow import sync_practice
    is_sync = sync_practice.looks_like_sync_practice(text)
    sync_headings = sync_practice.heading_candidates(text) if is_sync else []
    sync_by_line = {row['start_line']: row for row in sync_headings}
    # 同步练习册的文末答案区不属于任何大题：即使答案行被 skip_lines 过滤掉、
    # 或答案区里没有题号行，也必须从最后一个大题的窗口里切出去。
    sync_answer = sync_practice.answer_key_start(text) if is_sync else None
    skip = {int(n) for n in (skip_lines or []) if str(n).strip().isdigit()}
    sections, unresolved, groups, image_questions = [], [], [], []
    active = None
    seen_headings = set()
    appendix_start = len(lines)
    appendix_ranges: list[tuple[int, int]] = []
    skip_until = -1
    from workflow.denoise import extract_meta
    paper_title = _title_key(extract_meta(lines).get("paper_title", ""))
    scan_numbers: set[int] = set()
    for index, raw in enumerate(lines):
        if index < skip_until:
            continue
        if sync_answer and index + 1 == sync_answer:
            appendix_start = index
            unresolved.append({"code": "ANSWER_APPENDIX_DETECTED", "line": index + 1,
                               "message": "文末参考答案已按课时来源边界分离，答案题号不参与正文统计"})
            break
        if index + 1 in skip:
            continue
        if (active is not None and paper_title and _title_key(raw) == paper_title
                and _answer_summary_follows(lines[index + 1:])):
            appendix_start = index
            unresolved.append({"code": "ANSWER_APPENDIX_DETECTED", "line": index + 1,
                               "message": "重复卷名后紧随题号与答案汇总表，已从卷名处分离为答案附录候选"})
            break
        plain_line = _plain(raw)
        line = plain_line.strip()
        if ANSWER_HEADING.match(line) and _is_trailing_answer_block(lines, index, seen_headings):
            appendix_start = index
            unresolved.append({"code": "ANSWER_APPENDIX_DETECTED", "line": index + 1,
                               "message": "文末答案标题之后是答案/解析块，已从标题处分离为答案附录候选"})
            break
        heading = HEADING.match(line)
        kind = next((name for hint, name in TYPE_HINTS if hint in heading.group(2)), None) if heading else None
        sync_heading = sync_by_line.get(index + 1)
        if sync_heading:
            kind = sync_heading['type']
        if (heading and kind) or sync_heading:
            # 书面表达里的“1.词数… 2.请不要逐句翻译…”是写作要求，不是新大题。
            if active is not None and active.get("type") == "书面表达" and heading and heading.group(1).isdigit():
                continue
            key = (heading.group(1) if heading else '', kind)
            # 同步练习每段重新编号，重复的是标题出现位置，不是答案附录证据。
            if key in seen_headings and not is_sync:
                # 重复大题标题可能是答案附录，也可能是另一份试卷，不能静默并入。
                # 解析版常把答案插在大题之间（听力答案后紧接“第二部分 笔试”）：
                # 若后面还有没出现过的新大题，且新大题带真实题干与选项，当前这段
                # 只是分段答案——收进附录，正文继续解析，不能把整后半卷当答案册。
                next_new = _next_unseen_heading(lines, index + 1, seen_headings)
                if next_new is not None and _is_inline_answer_key(lines, index, next_new):
                    appendix_ranges.append((index, next_new))
                    skip_until = next_new
                    unresolved.append({"code": "INLINE_ANSWER_BLOCK", "line": index + 1,
                                       "message": "重复大题标题后是分段答案，随后仍有新大题；答案保留在附录，正文继续解析"})
                    continue
                appendix_start = index
                if looks_like_answers(lines[index:]):
                    # 明确是答案册（题号+答案字母/解析），保留为可核对的答案附录，不当作第二份卷子。
                    unresolved.append({"code": "ANSWER_APPENDIX_DETECTED", "line": index + 1,
                                       "message": "文末重复标题之后是答案册（题号+答案字母/解析），已分离为答案附录候选"})
                else:
                    unresolved.append({"code": "REPEATED_SECTION_APPENDIX", "line": index + 1,
                                       "message": "大题标题重新出现；其后内容单独保留，需核对是答案附录还是另一份试卷"})
                break
            seen_headings.add(key)
            count = re.search(r"(?:共\s*)?(\d+)\s*小题", line)
            active = {"type": kind, "title": line, "source_line": index + 1,
                      "questions": [], "question_locations": [],
                      "declared_count": int(count.group(1)) if count else None,
                      "material_boundaries": []}
            if sync_heading:
                active['sync_occurrence'] = sync_heading['occurrence']
                active['source_context'] = sync_heading['context']
                active['candidate_unit_kind'] = sync_heading['kind']
            sections.append(active)
            if is_sync:
                scan_numbers.clear()
            continue
        if active is None:
            continue
        references = guide_numbers(line)
        if material_label(line) or references:
            boundary_index = material_start(lines, index) if material_label(line) else index
            active["material_boundaries"].append({"label": line, "line": boundary_index + 1,
                                                   "references": references})
        if _is_answer_explanation_line(line) and not is_sync:
            # 解析版逐题答案行（31.B 【解析】…）不是新题，也不该制造重复题号。
            continue
        # 单词拼写等题型把答案紧跟在题目下一行（91.题目 / 91.nature）：
        # 同题号短答案同样不能算第二个题目，否则会制造重复题号。
        if not is_sync and index and _is_inline_answer_after_question(line, _plain(lines[index - 1]).strip()):
            continue
        if not is_sync and _is_numbered_letter_answer(line, scan_numbers):
            continue
        match = QUESTION.match(line) or (DIALOGUE_QUESTION.match(line) if is_sync else None)
        if active["type"] == "书面表达":
            # 题号只可能在任务开头；正文里的数字列表不能仅凭连续性视为题号。
            prior = [n for section in sections[:-1] for n in section["questions"]]
            preceding = lines[active["source_line"]:index]
            if (not prior or not match or int(match.group(1)) != max(prior) + 1
                    or any(_plain(item).strip() for item in preceding)
                    or not re.search(r"(?:\bwrite\s+\S.+|请[^\n]*写[^\n]*\S|写一[篇封].+)",
                                     line[match.end():], re.I)):
                match = None
        numbers = [int(match.group(1))] if match else []
        # 一行并排两个小题时，行首题号之后的题号也要收进来，否则结构闸门会把
        # 这些真实存在的题号判成「未归属」并阻断整卷。
        if is_sync and numbers and not DIALOGUE_QUESTION.match(line):
            numbers.extend(same_line_numbers(plain_line, numbers[0]))
        # 表格/文章里的空位题号：只认明确下划线或全角空格边界，不扫普通数字。
        if not numbers:
            numbers = blank_numbers(plain_line)
        if (not numbers and is_sync and active.get('candidate_unit_kind') == 'group'
                and active['type'] in {'补全对话', '完形填空', '选词填空', '短文填空', '教材语篇填空', '听力信息转换'}):
            from workflow.preview import _weak_numbers
            numbers = _weak_numbers(raw)
        for number in numbers:
            active["questions"].append(number)
            active["question_locations"].append({"number": number, "line": index + 1,
                                                 "kind": "question" if match else "blank"})
            scan_numbers.add(int(number))
        if "[IMG " in raw and active["questions"]:
            image_questions.append(active["questions"][-1])
    if sync_answer:
        # 同步练习册的文末答案区不属于正文：最后一个大题的窗口必须止于答案区之前，
        # 否则答案里的题号会撞进正文题号、答案文字会混进题干与选项。
        appendix_start = min(appendix_start, sync_answer - 1)
    previous = 0
    for section_index, section in enumerate(sections):
        end_line = sections[section_index + 1]["source_line"] - 1 if section_index + 1 < len(sections) else appendix_start
        section["source_end_line"] = end_line
        nums = section["questions"]
        # 完形同一题号在文章空位和选项行各出现一次是正常结构。
        question_lines = [x["number"] for x in section["question_locations"] if x["kind"] == "question"]
        duplicates = [n for n, count in Counter(question_lines).items() if count > 1]
        if duplicates:
            unresolved.append({"code": "DUPLICATE_QUESTION_NUMBER", "line": section["source_line"],
                               "message": f"{section['title']} 内题号重复：{duplicates}"})
        section["questions"] = nums = sorted(set(nums))
        declared = section["declared_count"]
        if not nums:
            body = [line for line in lines[section["source_line"]:end_line] if _plain(line).strip()]
            # 先识别纯图片窗口，不能因上一节存在编号就猜出图片中的印刷题号。
            if (any("[IMG " in line for line in body)
                    and not any(re.sub(r"\[IMG\s+[^\]]*\]", "", _plain(line)).strip() for line in body)):
                section["image_only"] = True
                unresolved.append({"code": "IMAGE_SECTION_UNNUMBERED", "line": section["source_line"],
                                   "message": f"{section['title']} 的小题只存在于图片中，保留完整来源单元；图片识别能力限制不阻断"})
                continue
        # 听力稿常没有逐题行首编号：长对话用“回答第X~Y题”导语，
        # 短文用“有五个小题”说明题量，信息转换通常承接连续五个空位。
        if section["type"] in GROUP_TYPES and not nums:
            guide_refs = sorted({n for boundary in section["material_boundaries"]
                                 for n in boundary.get("references", [])})
            if guide_refs:
                nums = guide_refs
                section["questions"] = nums
                section["numbers_inferred"] = True
                for boundary in section["material_boundaries"]:
                    for number in boundary.get("references", []):
                        section["question_locations"].append(
                            {"number": number, "line": boundary["line"], "kind": "guide"})
            else:
                section_start = section["source_line"] - 1
                section_end = sections[section_index + 1]["source_line"] - 1 if section_index + 1 < len(sections) else appendix_start
                declared = next((_declared_count(line) for line in lines[section_start:section_end]
                                 if _declared_count(line) is not None), None)
                section["declared_count"] = declared
                if declared and previous:
                    nums = list(range(previous + 1, previous + declared + 1))
                    section["questions"] = nums
                    section["numbers_inferred"] = True
                elif section["type"] == "听力信息转换" and previous:
                    nums = list(range(previous + 1, previous + 6))
                    section["questions"] = nums
                    section["declared_count"] = 5
                    section["numbers_inferred"] = True
        declared = section["declared_count"]
        if not nums and declared and previous:
            nums = list(range(previous + 1, previous + declared + 1))
            section["questions"] = nums
            section["numbers_inferred"] = True
            unresolved.append({"code": "QUESTION_NUMBER_INFERRED", "line": section["source_line"],
                               "message": f"{section['title']} 未读到明确题号，候选编号 {nums} 须核对原卷"})
        if declared and declared != len(nums):
            unresolved.append({"code": "QUESTION_COUNT_MISMATCH", "line": section["source_line"],
                               "message": f"{section['title']} 声明 {declared} 小题，实际定位 {len(nums)} 个"})
        if not nums:
            unresolved.append({"code": "QUESTION_BOUNDARY_MISSING", "line": section["source_line"],
                               "message": f"{section['title']} 未能定位题号"})
            continue
        if previous and nums[0] <= previous:
            unresolved.append({"code": "NUMBERING_RESTART", "line": section["source_line"],
                               "message": "题号跨大题重置或重复；需要按原卷分区定位，不能自动按全卷题号合并"})
        previous = max(previous, max(nums))
        if section["type"] in GROUP_TYPES:
            boundaries = section["material_boundaries"]
            partitions = []
            if boundaries:
                for offset, boundary in enumerate(boundaries):
                    end = boundaries[offset + 1]["line"] if offset + 1 < len(boundaries) else end_line + 1
                    members = (sorted(set(boundary.get("references", [])))
                               if boundary.get("references") else
                               sorted({x["number"] for x in section["question_locations"] if boundary["line"] < x["line"] < end}))
                    if boundary.get("references") and members != sorted(set(boundary["references"])):
                        unresolved.append({"code": "GUIDE_QUESTION_MISMATCH", "line": boundary["line"],
                                           "message": f"分段导语题号 {boundary['references']} 与实际题号 {members} 不一致"})
                    if members:
                        partitions.append({"questions": members, "type": section["type"],
                                           "boundary_method": "group_leadin" if boundary.get("references") else "material_heading",
                                           "source_start_line": boundary["line"], "source_end_line": end - 1})
            if partitions and set(n for p in partitions for n in p["questions"]) == set(nums):
                groups.extend(partitions)
            else:
                groups.append({"questions": nums, "type": section["type"], "boundary_method": "section_candidate"})
                if section["type"] in {"完形填空", "阅读理解", "听力长对话理解"}:
                    unresolved.append({"code": "GROUP_BOUNDARY_REVIEW", "line": section["source_line"],
                                       "message": f"{section['title']} 的材料/对话分组需要核验，未将分区等同于最终题组"})
    if not sections:
        unresolved.append({"code": "NO_RELIABLE_SECTIONS", "message": "未定位可靠的大题标题，需要按原页整理结构"})
    structure = {"version": VERSION, "status": "candidate", "sections": sections, "groups": groups,
                 "image_questions": sorted(set(image_questions)), "unresolved": unresolved,
                 "requires_visual_review": "[IMG " in text or "[TABLE" in text,
                 "coverage_verified": False}
    if is_sync:
        structure['sync_practice'] = {'headings': sync_headings,
                                      'numbering_scope': 'section_occurrence',
                                      'lesson_audit': sync_practice.audit_lesson_shape(text),
                                      'confirmed': False}
    body_lines = []
    moved_answer_lines = []
    # 解析版常把书面表达的【参考范文】附在正文末尾：范文是答案，不是题目，
    # 移入附录既能保持正文干净，也让答案校验能在答案文本里找到它。
    essay_start = (next((i for i in range(min(appendix_start, len(lines)))
                         if re.match(r"^\s*【\s*(?:参考范文|范文|参考作文|作文范文)\s*】", _plain(lines[i]))), None)
                   if not is_sync else None)
    body_end = essay_start if essay_start is not None else appendix_start
    current_heading = None      # 当前所属大题标题行（用于在附录补回被移走答案的章节）
    last_answer_heading = None
    prev_body_plain = ""        # 上一行正文（非空），用于识别“同题号紧邻答案”
    body_numbers: set[int] = set()
    for i in range(body_end):
        if any(start <= i < end for start, end in appendix_ranges):
            continue
        if i + 1 in skip:
            continue
        plain = _plain(lines[i]).strip()
        heading = HEADING.match(plain)
        # 只有真正的大题标题（能识别题型）才更新章节；逐题答案行“31.B …”也会被
        # HEADING 误匹配成 (31,"B…")，不能当标题，否则答案行会被重复写入附录。
        if heading and next((n for hint, n in TYPE_HINTS if hint in heading.group(2)), None):
            current_heading = lines[i]
        is_answer = _is_answer_line_for_body(plain) if not is_sync else False
        if not is_answer and not is_sync:
            # 单词拼写等题型会把答案紧跟在题目下一行：91.题目 / 91.nature。
            # 同题号、紧邻上一行、内容很短且不含问句/选项/空格的，是答案不是重复题。
            match = QUESTION.match(plain)
            if match and _is_inline_answer_after_question(plain, prev_body_plain):
                is_answer = True
            if not is_answer and _is_numbered_letter_answer(plain, body_numbers):
                is_answer = True
        if is_answer:
            # 解析版把答案夹在正文里：移入附录，正文只留题目，避免题号/选项重复。
            # 补回所属大题标题，保证答案附录仍能按章节归属（否则溯源会错落到上一节）。
            if current_heading is not None and current_heading != last_answer_heading:
                moved_answer_lines.append(current_heading)
                last_answer_heading = current_heading
            moved_answer_lines.append(lines[i])
            continue
        body_lines.append(lines[i])
        if plain:
            prev_body_plain = plain
        match = QUESTION.match(plain)
        if match:
            body_numbers.add(int(match.group(1)))
    if essay_start is not None:
        moved_answer_lines.extend(lines[essay_start:appendix_start])
    # AI/规则确认的答案行：即使不在正文循环内（例如位于附录区间），也要保留到附录。
    skipped_extra = [lines[n - 1] for n in sorted(skip)
                     if 1 <= n <= len(lines)
                     and not any(start <= n - 1 < end for start, end in appendix_ranges)]
    if skipped_extra:
        moved_answer_lines.extend(skipped_extra)
    # 文末重复卷名属于答案册页眉，不能留在最后一题的正文/题干里。
    while body_lines and not body_lines[-1].strip():
        body_lines.pop()
    if body_lines and paper_title and _title_key(body_lines[-1]) == paper_title:
        body_lines.pop()
    appendix_blocks = ["\n".join(lines[start:end]).strip() for start, end in appendix_ranges]
    if moved_answer_lines:
        appendix_blocks.append("\n".join(moved_answer_lines).strip())
    if appendix_start < len(lines):
        appendix_blocks.append("\n".join(lines[appendix_start:]).strip())
    appendix_text = "\n\n".join(block for block in appendix_blocks if block)
    return structure, "\n".join(body_lines).rstrip() + "\n", appendix_text


def _write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    data = json.dumps(value, ensure_ascii=False, indent=2) + "\n" if not isinstance(value, str) else value
    path.write_text(data, encoding="utf-8")


def _pdf_ocr_for_source(batch, source, report):
    """Load only a completed acquisition bound to these exact source bytes."""
    batch, source = Path(batch).resolve(), Path(source).resolve()
    digest = mf.sha256_file(source)
    for item in (report or {}).get("sources", []):
        value = item.get("path")
        if not value:
            continue
        path = Path(value).expanduser()
        path = (path if path.is_absolute() else batch / path).resolve()
        if path != source:
            continue
        if item.get("status") != "completed":
            error = item.get("error") or {}
            return None, f"PDF OCR 获取失败：{error.get('message', '未知错误')}"
        if item.get("sha256") != digest:
            return None, "PDF OCR 来源摘要不匹配"
        try:
            result_path = (batch / item["result_path"]).resolve()
            result_path.relative_to(batch)
            data = json.loads(result_path.read_text(encoding="utf-8"))
            result = data["result"]
            if data.get("source_sha256") != digest or not isinstance(result.get("pages"), list):
                raise ValueError("来源或页面无效")
            if any(not isinstance(p, dict) or not isinstance(p.get("text"), str) for p in result["pages"]):
                raise ValueError("页面文字无效")
            return result, None
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return None, f"PDF OCR 结果不可用：{exc}"
    return None, "PDF OCR 报告中没有此来源"


def _pdf(source, out, config, ocr_result=None, ocr_warning=None, ocr_wait=None):
    from workflow import ocr_backend
    result = ocr_backend.read_pdf(source, out / "pdf", config=config)
    if ocr_wait is not None:
        ocr_result, ocr_warning = ocr_wait()
    pages = result["pages"]
    warnings = list(result.get("warnings", []))
    if ocr_warning:
        warnings.append(ocr_warning)
    if ocr_result is not None:
        result["pdf_ocr"] = ocr_result
        warnings.extend(ocr_result.get("warnings", []))
    ocr_pages = {p.get("page"): p for p in (ocr_result or {}).get("pages", [])}
    for page in pages:
        if not page.get("needs_ocr"):
            continue
        recognized = ocr_pages.get(page.get("page"))
        if not recognized or not recognized.get("text", "").strip():
            page["ocr_skipped"] = True
            warnings.append(f"第 {page['page']} 页文字层质量不足，且没有可用的 OCR 文字；已保留原文字层内容")
            continue
        page["native_text"] = page.get("text", "")
        page["native_blocks"] = page.get("blocks", [])
        # OCR boxes belong to the OCR image coordinate system, never to the
        # native PDF points. Preserve both sources without relabeling boxes.
        page["blocks"] = recognized.get("blocks") or []
        page["ocr_coordinate_system"] = recognized.get("coordinate_system", "unspecified")
        for key in ("layout", "outputImages", "warnings"):
            if key in recognized:
                page["ocr_" + key] = recognized[key]
        page["text"] = recognized["text"]
        page["method"] = "ocr"
    result["warnings"] = warnings
    result["text"] = "\n\n".join(f"[PAGE {p['page']}]\n{p.get('text', '')}" for p in pages)
    return result


def _doc_has_image_bytes(source: Path) -> bool:
    """粗略探测旧版 .doc 内是否含图片数据（JPEG/PNG/GIF/EMF/WMF/PICT/BMP 魔数）。
    仅用于在 textutil 转换可能丢图时给出提醒，不做精确定位。"""
    try:
        data = Path(source).read_bytes()
    except OSError:
        return False
    sigs = (b"\xff\xd8\xff",            # JPEG
            b"\x89PNG\r\n\x1a\n",        # PNG
            b"GIF87a", b"GIF89a",        # GIF
            b" EMF", b"\x01\x00\x09\x00",# EMF 粗判
            b"\xd7\xcd\xc6\x9a",         # WMF
            b"BM")                       # BMP
    return any(s in data for s in sigs)


def _doc_to_docx(source: Path, evidence: Path):
    """把旧版 Word(.doc) 转为 .docx。优先 textutil(macOS，系统自带、无需安装，但可能丢图)，
    其次 soffice(LibreOffice，保真但需安装)。返回 (docx路径, 方法名)。失败则抛错。"""
    import subprocess
    out = evidence / (source.stem + ".docx")
    errors = []
    # 1) macOS textutil（零安装）
    if shutil.which("textutil"):
        try:
            subprocess.run(["textutil", "-convert", "docx", "-output", str(out), str(source)],
                           check=True, capture_output=True, timeout=120)
            if out.is_file() and out.stat().st_size > 0:
                return out, "textutil"
        except Exception as exc:  # noqa: BLE001
            errors.append("textutil: " + str(exc))
    # 2) LibreOffice / soffice（保真，但需安装）
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice:
        try:
            subprocess.run([soffice, "--headless", "--convert-to", "docx", "--outdir", str(evidence), str(source)],
                           check=True, capture_output=True, timeout=180)
            cand = evidence / (source.stem + ".docx")
            if cand.is_file() and cand.stat().st_size > 0:
                return cand, "soffice"
        except Exception as exc:  # noqa: BLE001
            errors.append("soffice: " + str(exc))
    raise ValueError(
        "无法转换 .doc 文件（需要系统 textutil 或 LibreOffice）："
        + ("; ".join(errors) if errors else "未找到可用的转换工具")
        + "。请将文件另存为 .docx 后再上传。"
    )


def extract_source(source: Path, destination: Path, config=None, ocr_result=None, ocr_warning=None, pdf_ocr_wait=None) -> dict:
    """原件只读，原生/OCR 结果留存；不伪造答案、题号覆盖或审核通过。"""
    source = Path(source).expanduser().resolve(strict=True)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    original_hash = mf.sha256_file(source)
    # 每次执行独立证据目录；不覆盖上一轮图片/OCR 文件。
    evidence = Path(tempfile.mkdtemp(prefix="extract-", dir=str(destination)))
    # 旧版二进制 Word(.doc) 无法直接解析：先转成 .docx 再走既有 DOCX 提取。
    # textutil 为系统自带（无需安装）但可能丢图；soffice 保真但需安装。
    doc_convert = None
    if source.suffix.lower() == ".doc":
        orig_doc = source
        source, doc_convert = _doc_to_docx(source, evidence)
    if source.suffix.lower() == ".docx":
        from workflow.docx_extract import extract_docx
        result = extract_docx(source, evidence)
        result.setdefault("pages", [])
        # 正常 Word 保留原生文字/格式。用户 2026-11-11 策略：纯 Word 直接文字提取、
        # 不跑本地 OCR；图片（含符号字体/浮动对象/图片型）语义交给 extract 之后的
        # 视觉理解复核（visual_review → image_semantics.json，可读图内文字），不再
        # 对每张嵌入图串行跑本地 paddle OCR（慢：13 张可达 4 分钟）。仅保留触发提示。
        low_text = len(re.sub(r"\[IMG[^\]]*\]|\s", "", result["text"])) < 80
        warning_text = " ".join(result.get("warnings", []))
        triggers = []
        if low_text and result.get("media"):
            triggers.append("image_only")
        if "Floating object" in warning_text:
            triggers.append("floating_object")
        if "Symbol font" in warning_text:
            triggers.append("symbol_font")
        if triggers and result.get("media"):
            # 只记录“本应复核但改由视觉理解补”的触发，绝不本地 OCR、不阻断，也不提示。
            result["ocr_auto"] = {"mode": "word_visual_only", "triggers": triggers,
                                  "skipped": "word_embedded_images_deferred_to_visual_review"}
        # 给既有预览器兼容的稳定媒体位置，旧同名图片先备份。
        for item in result.get("media", []):
            name = item["name"]
            if Path(name).name != name:
                raise ValueError("不合法媒体文件名")
            target = destination / "media" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                shutil.copy2(target, target.with_name(target.name + ".bak"))
            shutil.copy2(evidence / "media" / name, target)
    elif source.suffix.lower() == ".pdf":
        result = _pdf(source, evidence, config, ocr_result, ocr_warning, pdf_ocr_wait)
        # 文字型 PDF 的题图保留为可预览引用；坐标在证据中，图文归属不凭猜测放行。
        for page in result.get("pages", []):
            if page.get("method") == "ocr":
                continue  # 扫描整页图保留在证据中，不再作为题图重复插入。
            refs = []
            for index, image in enumerate(page.get("images", []), 1):
                original = Path(image["path"])
                name = f"pdf-page-{page['page']}-image-{index}{original.suffix.lower()}"
                target = destination / "media" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    shutil.copy2(target, target.with_name(target.name + ".bak"))
                shutil.copy2(original, target)
                image["preview_name"] = name
                refs.append(f"[IMG {name}]")
            if refs:
                page["text"] += "\n" + "\n".join(refs)
                result.setdefault("warnings", []).append(f"PDF 第 {page['page']} 页图片已保留；正文中的引用为页级候选，题目归属和图文位置须按 bbox 核对")
        result["text"] = "\n\n".join(f"[PAGE {p['page']}]\n{p.get('text', '')}" for p in result["pages"])
    elif source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
        from workflow import ocr_backend
        if not ocr_backend.ocr_needed(config, ("image_upload",)):
            raise ValueError(
                "这是图片型试卷，但 OCR 辅助已关闭，无法识别。"
                "请在「模型与设置 → OCR 识别」把模式设为「自动」或「开启」后重新上传。")
        result = ocr_backend.recognize(source, evidence / "ocr", config=config)
        result["text"] = "\n\n".join(p.get("text", "") for p in result["pages"])
    else:
        raise ValueError("原卷只支持 docx、pdf 或图片；旧 doc 请先转换为 docx")
    # .doc 经 textutil 转换可能丢图：若原 .doc 疑似含图、而转换后无媒体，给出明确提醒。
    if doc_convert == "textutil" and not (result.get("media") or []):
        if _doc_has_image_bytes(orig_doc):
            result.setdefault("warnings", []).append(
                "此 .doc 由系统转换（textutil）而来，疑似含图片但转换后未保留图片；"
                "如需保留题图，请用 Word/WPS 另存为 .docx 后重新上传（.docx 保真且无需安装任何工具）。")
        result.setdefault("warnings", []).append("该文件为旧版 .doc，已用系统工具转换为 .docx 解析（不含图片保真）。")
    text = result.get("text", "")
    if not re.sub(r"\[PAGE\s+\d+\]", "", text).strip():
        hint = ""
        if source.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
            hint = "。如果这是扫描件或图片型 PDF，请在「模型与设置 → OCR 识别」开启 OCR 辅助后重试"
        raise ValueError("提取正文为空，不能完成解析阶段" + hint)
    if mf.sha256_file(source) != original_hash:
        raise ValueError("提取期间原文件发生变化，请重试")
    from workflow import denoise
    from workflow.metadata_policy import with_placeholders
    metadata = with_placeholders(denoise.extract_meta(text.splitlines()))
    structure, body, appendix = infer_structure(text)
    for conflict in metadata.get("conflicts") or []:
        structure["unresolved"].append({"code": "SOURCE_METADATA_CONFLICT", "message": str(conflict)})
    structure["source_sha256"] = original_hash
    structure["layout_required"] = bool(structure.get("requires_visual_review") or
                                         any(issue.get("code") in {"GROUP_BOUNDARY_REVIEW", "QUESTION_BOUNDARY_MISSING"}
                                             for issue in structure["unresolved"]))
    structure["layout_review_command"] = "python -m workflow.layout_review --help" if structure["layout_required"] else None
    warnings = result.get("warnings", [])
    if any(p.get("method") == "ocr" for p in result.get("pages", [])) or result.get("image_ocr"):
        structure["unresolved"].append({"code": "OCR_VISUAL_REVIEW", "message": "OCR 结果需核对题号、音标、画线、表格和图文顺序，未提供逐字可靠性证明"})
    if any(issue.get("code") == "OCR_VISUAL_REVIEW" for issue in structure["unresolved"]) or warnings:
        structure["layout_required"] = True
        structure["layout_review_command"] = "python -m workflow.layout_review --help"
    if warnings:
        structure["extraction_warnings"] = warnings
        structure["unresolved"].append({"code": "EXTRACTION_WARNING", "message": "原生/OCR 提取有警告，详见 extraction.json"})
    result.update({"version": VERSION, "source": str(source), "source_sha256": original_hash,
                   "evidence_dir": str(evidence.relative_to(destination)), "structure_status": "candidate"})
    try:
        from workflow import ocr_backend
        result["ocr_assist_enabled"] = ocr_backend.assist_enabled(config)
    except Exception:  # noqa: BLE001 - evidence field is optional; parsing must not fail here
        pass
    _write(destination / "原始正文.txt", text)
    _write(destination / "正文.txt", body)
    _write(destination / "附录.txt", appendix)
    _write(destination / "extraction.json", result)
    _write(destination / "structure.json", structure)
    _write(destination / "metadata.json", dict(metadata, original_sha256=original_hash,
                                               source_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest()))
    return {"passed": True, "version": VERSION, "source_sha256": original_hash,
            "characters": len(body), "sections": len(structure["sections"]),
            "questions": sum(len(s["questions"]) for s in structure["sections"]),
            "unresolved": structure["unresolved"], "evidence_dir": result["evidence_dir"]}


def run(batch_dir: Path) -> dict:
    batch_dir = Path(batch_dir)
    manifest = mf.load(batch_dir)
    source = manifest.get("inputs", {}).get("source", {}).get("path")
    if not source:
        raise ValueError("批次缺少 source 输入")
    from workflow import pdf_ocr
    source_path = Path(source).expanduser()
    if not source_path.is_absolute():
        source_path = batch_dir / source_path

    def acquire():
        try:
            return pdf_ocr.run(batch_dir, manifest)
        except Exception as exc:  # acquisition failure must remain visible; do not retry
            return {"status": "failed", "sources": [], "error": str(exc)}

    # Acquisition and native extraction write to distinct evidence directories.
    # Only the main thread merges PDF pages and writes the extraction outputs.
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(acquire)
        def pdf_ocr_wait():
            return _pdf_ocr_for_source(batch_dir, source_path, future.result())

        try:
            if source_path.suffix.lower() == ".pdf":
                result = extract_source(source_path, batch_dir / "01_source", pdf_ocr_wait=pdf_ocr_wait)
            else:
                result = extract_source(source_path, batch_dir / "01_source")
        except Exception as exc:
            ocr_report = future.result()
            ocr_warning = pdf_ocr_wait()[1] if source_path.suffix.lower() == ".pdf" else None
            if ocr_warning or ocr_report.get("status") == "failed":
                raise ValueError(f"{exc}；PDF OCR 获取失败：{ocr_warning or ocr_report.get('error', '详见 PDF OCR 报告')}") from exc
            raise
        ocr_report = future.result()
    result["pdf_ocr"] = ocr_report
    if ocr_report.get("status") == "failed":
        message = "PDF OCR 获取失败，请查看 pdf_ocr 报告：" + str(ocr_report.get("error", "部分来源获取失败"))
        if source_path.suffix.lower() == '.docx':
            result.setdefault('warnings', []).append(message + '；继续使用 Word 原文处理')
        else:
            result["passed"] = False
            result["error"] = message
    # 材料分类（用户 2026-11 重设计）：逐份上传文件提取文字层后判定角色
    # （正文/答案/解析/混排/其他）。非阻断：模型不可用时用确定性兜底，只留证据。
    try:
        try:
            from . import material_classify, local_agent
        except ImportError:
            from workflow import material_classify, local_agent
        invoke = None
        cfg = None
        try:
            from . import agent
            if agent.configured():
                cfg = local_agent.load_config()
                invoke = lambda prompt, run_dir, config: local_agent.invoke(
                    prompt, run_dir, config)["response"]
        except Exception:  # noqa: BLE001
            invoke = None
        roles = material_classify.classify_documents(batch_dir, invoke_fn=invoke, config=cfg,
                                                     ocr_report=ocr_report)
        result["material_roles"] = roles
        failed_rows = [row for row in roles.get('documents', [])
                       if isinstance(row, dict) and row.get('error')]
        auxiliary_failures = [row for row in failed_rows
                              if source_path.suffix.lower() == '.docx'
                              and Path(str(row.get('path') or '')).suffix.lower() == '.pdf']
        if auxiliary_failures:
            names = '、'.join(row.get('input', 'unknown') for row in auxiliary_failures)
            result.setdefault('warnings', []).append('PDF 材料提取失败：' + names + '；继续使用 Word 原文处理')
        failures = [row.get('input', 'unknown') for row in failed_rows if row not in auxiliary_failures]
        if failures:
            result['passed'] = False
            result['error'] = '附件解析失败，请重试：' + '、'.join(failures)
        # 明确上传了答案/解析卷、而正文附录为空时，把答案卷文字补到 01_source_ans，
        # 供 answers 阶段使用（不依赖模型，直接取该文件文字层）。
        ans_item = (manifest.get("inputs", {}).get("answers") or {})
        ans_path = str(ans_item.get("path") or "")
        ans_text = batch_dir / "01_source_ans" / "正文.txt"
        if ans_path and not ans_text.is_file():
            try:
                if Path(ans_path).suffix.lower() == ".pdf":
                    answer = next((row for row in roles.get("documents", [])
                                   if row.get("input") == "answers" and row.get("text_path")), None)
                    if answer:
                        ans_text.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(batch_dir / answer["text_path"], ans_text)
                        answer_source = Path(ans_path).expanduser()
                        if not answer_source.is_absolute():
                            answer_source = batch_dir / answer_source
                        _write(ans_text.parent / "preparation.json", {
                            "source": str(answer_source.resolve()),
                            "source_sha256": mf.sha256_file(answer_source),
                            "text_sha256": mf.sha256_file(ans_text),
                            "extraction_report": {"source": "material_classify"}})
                else:
                    try:
                        from . import agent_prepare
                    except ImportError:
                        from workflow import agent_prepare
                    agent_prepare.prepare_answers(batch_dir)
            except Exception as exc:  # noqa: BLE001
                result["passed"] = False
                result["error"] = f"答案卷准备失败：{exc}"
    except Exception as exc:  # noqa: BLE001 - 分类失败不影响提取/结构
        result["material_roles"] = {"error": str(exc)}
    # 来源优先级证据（用户 2026-11-11 确认）：Word > 带文字层 PDF > 扫描件。
    # 只记录本次实际用了哪份原文、PDF 哪些页走 OCR，不改变提取结果。
    try:
        from workflow import source_policy
        inputs = manifest.get("inputs") or {}
        supporting = [str((inputs.get(key) or {}).get("path") or "")
                      for key in sorted(inputs)
                      if key == "answers" or re.fullmatch(r"attachment_\d{3,}", key)]
        # extract_source returns a stage summary, not the page evidence. Read
        # the actual saved pages so a completed OCR page is never reported as
        # 'pdf_unknown / 0 pages'.
        page_evidence = json.loads((batch_dir / "01_source" / "extraction.json").read_text(encoding="utf-8"))
        # 视觉理解是否可用（决定报告里记录"配合视觉理解判结构"这一维）；判定失败不阻断。
        try:
            from workflow import visual_review
            visual_assist = bool(visual_review._load_key())
        except Exception:  # noqa: BLE001
            visual_assist = False
        policy = source_policy.build_report(source_path, [path for path in supporting if path],
                                            pages=page_evidence.get("pages"),
                                            visual_assist=visual_assist)
        result["source_policy"] = policy
        _write(batch_dir / "01_source" / "source-policy.json", policy)
    except Exception as exc:  # noqa: BLE001 - 证据记录失败不阻断提取
        result["source_policy"] = {"error": str(exc)}
    _write(batch_dir / "07_review" / "extraction-report.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = run(args.batch_dir)
    except Exception as exc:
        _write(args.batch_dir / "07_review" / "extraction-report.json", {"passed": False, "error": str(exc)})
        print(f"原卷提取失败：{exc}", file=sys.stderr)
        return 1
    print(f"复合提取完成：{report['characters']} 字符，{report['sections']} 个分区，{report['questions']} 个候选题号；{len(report['unresolved'])} 项待核验")
    if not report.get('passed'):
        print(report.get('error', '附件解析未完成'), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
