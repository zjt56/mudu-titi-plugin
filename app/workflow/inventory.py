# -*- coding: utf-8 -*-
"""机械清点整套卷的题号与板块。

只做统计，不做结构判断：
- 题号来源：括号题号 `(　)21.`、行首题号 `21.`、信息转换空位 `__16__`；
- 板块来源：`第一部分` / `Ⅰ.` 这类大题标题。

写 01_source/inventory.json，供通报计算"整套卷还有多少题没核对"。
它不能替代 structure_gate 的人工确认，只是覆盖度统计。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

OUT = "01_source/inventory.json"

BRACKET = re.compile(r"[（(]\s*[）)]\s*(\d{1,3})\s*[.．]")
LINE_START = re.compile(r"^\s*(\d{1,3})\s*[.．]")
BLANK = re.compile(r"__\s*(\d{1,3})\s*__")
# 补全对话/信息转换里的空位常写成「　51　」「　16 」，用全角空格包着
FULLWIDTH = re.compile(r"\u3000\s*(\d{1,3})(?![0-9])")
# 表格单元格里的空位可能写成「17　to get started.」，数字在行首
FULLWIDTH_HEAD = re.compile(r"^\s*(\d{1,3})\u3000")
SECTION = re.compile(r"^\s*(第[一二三四五六七八九十]部分|[ⅠⅡⅢⅣⅤⅥⅦⅧ]+[.．]|[一二三四五六七八九十]+、)")
# 无题号大题：Ⅹ.书面表达(共1小题;满分20分)
UNNUMBERED = re.compile(r"^\s*[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[.．]\s*(书面表达|作文|写作)")


def scan(batch_dir) -> dict:
    batch_dir = Path(batch_dir)
    text_path = batch_dir / "01_source" / "正文.txt"
    if not text_path.is_file():
        raise FileNotFoundError(f"找不到正文：{text_path}")
    lines = text_path.read_text(encoding="utf-8").splitlines()

    questions: dict[int, dict] = {}
    sections: list[dict] = []
    for index, line in enumerate(lines, start=1):
        head = SECTION.match(line)
        if head:
            sections.append({"title": line.strip()[:40], "line": index})
        for match in BRACKET.finditer(line):
            number = int(match.group(1))
            questions.setdefault(number, {"number": number, "line": index, "source": "bracket"})
        for match in BLANK.finditer(line):
            number = int(match.group(1))
            questions.setdefault(number, {"number": number, "line": index, "source": "blank"})
        for match in FULLWIDTH.finditer(line):
            number = int(match.group(1))
            if 1 <= number <= 200:
                questions.setdefault(number, {"number": number, "line": index,
                                              "source": "fullwidth"})
        head_blank = FULLWIDTH_HEAD.match(line)
        if head_blank:
            number = int(head_blank.group(1))
            if 1 <= number <= 200:
                questions.setdefault(number, {"number": number, "line": index,
                                              "source": "fullwidth"})
        head_num = LINE_START.match(line)
        if head_num:
            number = int(head_num.group(1))
            # 排除年份、页眉等明显不是题号的数字
            rest = line[head_num.end():]
            year_like = 2000 <= number <= 2100 and ("年" in rest[:4])
            if not year_like and number not in questions:
                questions[number] = {"number": number, "line": index, "source": "line"}

    ordered = [questions[n] for n in sorted(questions)]

    # 书面表达/作文常常没有题号，只有「Ⅹ.书面表达」这样的大题标题，
    # 不清点出来会在组装和预览里整块消失。
    top = max(questions) if questions else 0
    for index, line in enumerate(lines, start=1):
        if UNNUMBERED.match(line):
            top += 1
            questions.setdefault(top, {"number": top, "line": index, "source": "unnumbered"})
    ordered = [questions[n] for n in sorted(questions)]

    payload = {
        "source": str(text_path),
        "total": len(ordered),
        "questions": ordered,
        "sections": sections,
        "numbers": [q["number"] for q in ordered],
        "note": "机械清点，仅供覆盖度统计；题号归属仍需 structure_gate 人工确认",
    }
    target = batch_dir / OUT
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return payload


def load(batch_dir) -> dict | None:
    path = Path(batch_dir) / OUT
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return None
