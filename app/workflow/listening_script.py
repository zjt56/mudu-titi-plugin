"""Copy source listening scripts only when their ownership is provable."""
import json
import re
from pathlib import Path

HEADER = re.compile(r'^\s*(?:附(?:录)?\s*[:：]?\s*)?(?:听力(?:录音)?(?:材料|原文|文本|稿|文稿)|录音(?:材料|原文|稿)|listening\s+(?:scripts?|texts?|materials?)|tapescripts?)\s*[:：]?\s*$', re.I)
SECTION = re.compile(r'^\s*(?:(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩIVX]+|[一二三四五六七八九十]+|\d+)[.．、:：)）]\s*|(?:第[一二三四五六七八九十\d]+(?:节|部分|大题))\s*[:：、.．]?\s*)(\S.*)$', re.I)
NUMBER = re.compile(r'^\s*(?:[（(]\s*(\d{1,3})\s*[）)]|(\d{1,3})[.．、)）])\s*(.*)$')
RANGE = re.compile(r'(?:回答|完成|questions?\s*)\s*(?:第\s*)?(\d+)\s*(?:至|到|[-—～~–])\s*(\d+)\s*(?:小)?题?', re.I)
END = re.compile(r'^\s*(?:参考答案|答案与解析|答案及解析|答案解析|试题解析|书面表达|笔试部分|笔试|阅读理解)\s*[:：]?\s*$', re.I)
MARK = '听力录音材料：\n'
SOURCES = ('01_source/附录.txt', '01_source/正文.txt', '01_source_ans/正文.txt')


def _type_map(batch):
    try:
        data = json.loads((Path(batch) / '03_types/types.json').read_text(encoding='utf-8-sig'))
        return {row['unit_id']: row.get('type', '') for row in data.get('items', [])}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _section_type(title):
    for key, value in (('关键词语选择', '听力关键词语选择'), ('短对话理解', '听力短对话理解'),
                       ('长对话理解', '听力长对话理解'), ('短文理解', '听力短文理解'),
                       ('信息转换', '听力信息转换'), ('听力判断', '听力判断'),
                       ('听力排序', '听力排序'), ('听力问答', '听力问答')):
        if key in title:
            return value
    for pattern, value in ((r'(?:听|录音).*短对话', '听力短对话理解'),
                           (r'(?:听|录音).*长对话', '听力长对话理解'),
                           (r'(?:听|录音).*短文', '听力短文理解'),
                           (r'(?:听|录音).*(?:填表|填空|信息)', '听力信息转换')):
        if re.search(pattern, title):
            return value
    return ''


def extract(batch, units):
    """Return uid -> (verbatim text, source, first line). Reject ambiguity."""
    batch = Path(batch)
    types = _type_map(batch)
    listening = [u for u in units if str(types.get(u['unit_id']) or u.get('type') or '').startswith('听力')]
    if not listening:
        return {}
    all_numbers = [str(n) for u in listening for n in u.get('numbers', [])]
    if len(all_numbers) != len(set(all_numbers)):
        return {}
    found = {}
    from .upload_content import reference_only_sources
    reference_only = reference_only_sources(batch)
    for name in SOURCES:
        path = batch / name
        if name in reference_only or not path.is_file() or path.is_symlink():
            continue
        lines = path.read_text(encoding='utf-8-sig').splitlines()
        starts = [i for i, line in enumerate(lines) if HEADER.fullmatch(line)]
        if len(starts) != 1:
            continue
        blocks, current, category = [], None, ''
        def flush():
            nonlocal current
            if current:
                current['text'] = '\n'.join(current.pop('lines')).strip()
                if current['text']:
                    blocks.append(current)
            current = None
        for i in range(starts[0] + 1, len(lines)):
            line = lines[i]
            if END.fullmatch(line):
                flush(); break
            section = SECTION.match(line)
            if section:
                if END.fullmatch(section.group(1)):
                    flush(); break
                classified = _section_type(section.group(1))
                if classified:
                    flush(); category = classified; continue
                # An unknown numbered section ends the previous ownership
                # window; never absorb it into an already-bound recording.
                if not NUMBER.match(line):
                    flush(); category = ''; continue
            elif (len(line.strip()) <= 24 and not re.search(r'[，,。.!！？?]', line)
                  and not re.match(r'^\s*[A-Za-z]+\s*[:：]', line)):
                classified = _section_type(line.strip().strip('：:'))
                if classified:
                    flush(); category = classified; continue
            match = RANGE.search(line)
            if match:
                flush(); a, b = map(int, match.groups())
                if a <= b <= a + 30:
                    current = {'numbers': tuple(str(n) for n in range(a, b + 1)), 'type': category, 'lines': [], 'line': i + 2}
                continue
            match = NUMBER.match(line)
            if match:
                flush(); current = {'numbers': (str(int(match.group(1) or match.group(2))),), 'type': category, 'lines': [match.group(3)], 'line': i + 1}
            elif line.strip() and current is None and category:
                current = {'numbers': (), 'type': category, 'lines': [line], 'line': i + 1}
            elif current is not None:
                current['lines'].append(line)
        flush()
        for block in blocks:
            eligible = [u for u in listening if not block['type'] or (types.get(u['unit_id']) or u.get('type')) == block['type']]
            if block['numbers']:
                eligible = [u for u in eligible if tuple(map(str, u['numbers'])) == block['numbers']]
            elif not block['type'] or len(eligible) != 1:
                eligible = []
            if len(eligible) == 1:
                found.setdefault(eligible[0]['unit_id'], []).append((block['text'], name, block['line']))
    return {uid: rows[0] for uid, rows in found.items() if len({row[0] for row in rows}) == 1}


def combine(original, script):
    prefix = '' if not original or original.strip() in ('无', '无解析', '未提供') else original.rstrip() + '\n\n'
    return prefix + MARK + script
