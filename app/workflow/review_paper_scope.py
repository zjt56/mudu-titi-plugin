"""Derive question types from the paper's own section headings.

This uses only the bound source text: the paper declares its sections
("Ⅵ.单项填空(共15小题…)"), so scoping a catalog by section does not reveal any
model-produced label. Numbers that cannot be assigned to a declared section are
reported as unassigned so callers can fall back honestly instead of guessing.
"""
from __future__ import annotations

import re

_HEADING = re.compile(
    r'^\s*(?:\*{1,2}\s*)?(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]+|[IVX]{1,4}|[一二三四五六七八九十]+)\s*[.、]\s*(\S[^\n(（]*)'
)
_NUMBER_LINE = re.compile(r'^\s*(?:\(\s*\)\s*)?(\d+)\s*[.、]')
_BLANK = re.compile(r'<\s*u\s*>[^<]{0,20}?(?<!\d)(\d+)(?!\d)[^<]{0,20}?<\s*/\s*u\s*>'
                    r'|__\s*(\d+)\s*__')

# Longest names first so 短对话理解 is not shadowed by 对话理解 style matches.
TYPE_HINTS = (
    ('关键词语选择', '听力关键词语选择'),
    ('短对话理解', '听力短对话理解'),
    ('长对话理解', '听力长对话理解'),
    ('短文理解', '听力短文理解'),
    ('信息转换', '听力信息转换'),
    ('单项填空', '单项填空'),
    ('单项选择', '单项填空'),
    ('完形填空', '完形填空'),
    ('语法选择', '语法选择'),
    ('补全对话', '补全对话'),
    ('任务型阅读', '任务型阅读'),
    ('阅读理解', '阅读理解'),
    ('单词拼写', '单词拼写'),
    ('词语拼写', '单词拼写'),
    ('书面表达', '书面表达'),
)


def heading_type(text):
    if not isinstance(text, str):
        return None
    for name, main_type in TYPE_HINTS:
        if name in text:
            return main_type
    return None


def question_sections(source):
    """Return {number: type} from declared headings and per-number positions.

    Question numbers are taken from line-leading numbers and numbered blanks.
    Only the closest preceding declared section heading is used; numbers before
    any heading or absent from the source stay unassigned.
    """
    if not isinstance(source, str):
        raise ValueError('正文必须是文本')
    headings = []
    positions = {}
    lines = source.splitlines()
    for index, line in enumerate(lines):
        match = _HEADING.match(line)
        if match:
            # Record unrecognized headings as None too so an earlier section's
            # type cannot bleed across a section boundary.
            headings.append((index, heading_type(match.group(1))))
            continue
        start = _NUMBER_LINE.match(line)
        if start:
            positions.setdefault(start.group(1), []).append(index)
        for blank in _BLANK.finditer(line):
            number = blank.group(1) or blank.group(2)
            positions.setdefault(number, []).append(index)
    section_of, types, unassigned = {}, set(), []
    for number, indices in positions.items():
        choices = set()
        for index in indices:
            chosen = None
            for heading_index, main_type in headings:
                if heading_index > index:
                    break
                chosen = main_type
            choices.add(chosen)
        if len(choices) == 1:
            only = next(iter(choices))
            if only is not None:
                section_of[number] = only
                types.add(only)
                continue
        unassigned.append(number)
    return {'types': section_of, 'declared_types': sorted(types),
            'unassigned': sorted(unassigned, key=lambda n: int(n) if n.isdigit() else 0)}
