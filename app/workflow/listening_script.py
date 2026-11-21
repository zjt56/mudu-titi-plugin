"""Copy source listening scripts only when their ownership is provable."""
from difflib import SequenceMatcher
import json
import re
from pathlib import Path

HEADER = re.compile(r'^\s*(?:附(?:录)?\s*[:：]?\s*)?(?:听力(?:录音)?(?:材料|原文|文本|稿|文稿)|录音(?:材料|原文|稿)|listening\s+(?:scripts?|texts?|materials?)|tapescripts?)\s*[:：]?\s*$', re.I)
SECTION = re.compile(r'^\s*(?:(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩIVX]+|[一二三四五六七八九十]+|\d+)[.．、:：)）]\s*|(?:第[一二三四五六七八九十\d]+(?:节|部分|大题))\s*[:：、.．]?\s*)(\S.*)$', re.I)
NUMBER = re.compile(r'^\s*(?:[（(]\s*(\d{1,3})\s*[）)]|(\d{1,3})[.．、)）])\s*(.*)$')
RANGE = re.compile(r'(?:回答|完成|questions?\s*)\s*(?:第\s*)?(\d+)\s*(?:至|到|[-—～~–])\s*(\d+)\s*(?:小)?题?', re.I)
PAIR_RANGE = re.compile(r'(?:回答|完成|questions?\s*)\s*(?:第\s*)?(\d+(?:\s*(?:[、,，和与]|\band\b)\s*\d+)+)\s*(?:小)?题?', re.I)
RUBRIC = re.compile(r'^\s*(?:你将听到.+?读[一两三]遍|每段(?:对话|短文|材料)读[一两三]遍)')
END = re.compile(r'^\s*(?:参考答案|答案与解析|答案及解析|答案解析|试题解析|书面表达|笔试部分|笔试|阅读理解)\s*[:：]?\s*$', re.I)
MARK = '听力录音材料：\n'
SOURCES = ('01_source/附录.txt', '01_source/正文.txt', '01_source_ans/正文.txt')


def _plain(line):
    return re.sub(r'\*+|<[^>]+>', '', line).strip()


def is_end(line):
    plain = _plain(line)
    section = SECTION.match(plain)
    return bool(END.fullmatch(plain) or section and END.fullmatch(section.group(1)))


def without_scripts(lines):
    """Keep source coordinates while excluding transcript-only numbering."""
    inside = False
    for line in lines:
        if HEADER.fullmatch(line):
            inside = True
            yield line  # Keep the heading as the preceding explanation's boundary.
        elif inside and not is_end(line):
            yield ''
        else:
            inside = False
            yield line


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
    from .upload_content import listening_script_sources, reference_only_sources
    reference_only = reference_only_sources(batch)
    role_scripts = listening_script_sources(batch)
    candidates = list(SOURCES) + [p for p in sorted(role_scripts) if p not in SOURCES]
    for name in candidates:
        path = batch / name
        if name in reference_only or not path.is_file() or path.is_symlink():
            continue
        lines = path.read_text(encoding='utf-8-sig').splitlines()
        starts = [i for i, line in enumerate(lines) if HEADER.fullmatch(_plain(line))]
        if name in role_scripts:
            if len(starts) > 1:
                continue
            start_idx = starts[0] + 1 if len(starts) == 1 else 0
        else:
            if len(starts) != 1:
                continue
            start_idx = starts[0] + 1
        blocks, current, category = [], None, ''
        def flush():
            nonlocal current
            if current:
                current['text'] = '\n'.join(current.pop('lines')).strip()
                if current['text']:
                    blocks.append(current)
            current = None
        for i in range(start_idx, len(lines)):
            line = lines[i]
            plain = _plain(line)
            if is_end(plain):
                flush(); break
            section = SECTION.match(plain)
            if section:
                classified = _section_type(section.group(1))
                if classified:
                    flush(); category = classified; continue
                # An unknown numbered section ends the previous ownership
                # window; never absorb it into an already-bound recording.
                if not NUMBER.match(plain):
                    flush(); category = ''; continue
            elif (len(plain) <= 24 and not re.search(r'[，,。.!！？?]', plain)
                  and not re.match(r'^\s*[A-Za-z]+\s*[:：]', plain)):
                classified = _section_type(plain.strip('：:'))
                if classified:
                    flush(); category = classified; continue
            match = RANGE.search(plain)
            if match:
                flush(); a, b = map(int, match.groups())
                if a <= b <= a + 30:
                    current = {'numbers': tuple(str(n) for n in range(a, b + 1)), 'type': category, 'lines': [], 'line': i + 2}
                continue
            pair = PAIR_RANGE.search(plain)
            if pair:
                nums = [int(x) for x in re.findall(r'\d+', pair.group(1))]
                if 2 <= len(nums) <= 30 and nums == list(range(nums[0], nums[0] + len(nums))):
                    flush()
                    current = {'numbers': tuple(str(n) for n in nums), 'type': category, 'lines': [], 'line': i + 2}
                    continue
            match = NUMBER.match(plain)
            if match:
                flush(); current = {'numbers': (str(int(match.group(1) or match.group(2))),), 'type': category, 'lines': [match.group(3)], 'line': i + 1}
            elif plain and current is None and category:
                if RUBRIC.match(plain):
                    continue
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
    def _source_rank(src_name: str) -> tuple[int, int]:
        # 优先采用原生文本来源（显式 listening_script 角色或原生正文/附录），
        # 仅在同份听力稿出现轻微 OCR/标点差异时按优先级择优，避免整段丢弃。
        lower = src_name.lower()
        is_ocr = 'ocr' in lower or lower.endswith('.ocr.txt')
        if src_name in role_scripts and not is_ocr:
            return (0, 0)
        if src_name in SOURCES:
            return (1, SOURCES.index(src_name))
        return (2 if is_ocr else 1, 99)

    def _norm_cmp(txt: str) -> str:
        return re.sub(r'[\s\u3000]+', '', txt or '')

    resolved = {}
    for uid, rows in found.items():
        unique_texts = {row[0] for row in rows}
        if len(unique_texts) == 1:
            resolved[uid] = rows[0]
            continue
        ordered = sorted(rows, key=lambda r: _source_rank(r[1]))
        best = ordered[0]
        best_norm = _norm_cmp(best[0])
        # 仅当其余来源与最高优先级来源属于同一段听力稿的轻微差异（如 OCR 错一字）时择优保留。
        if best_norm and all(SequenceMatcher(None, best_norm, _norm_cmp(r[0])).ratio() >= 0.85 for r in ordered[1:]):
            resolved[uid] = best
    return resolved


def combine(original, script):
    prefix = '' if not original or original.strip() in ('无', '无解析', '未提供') else original.rstrip() + '\n\n'
    return prefix + MARK + script
