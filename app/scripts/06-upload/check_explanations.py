# -*- coding: utf-8 -*-
"""解析/大意可缺失；已有内容必须来自原题，禁止 AI 生成与原文改写。

缺失和空白不触发补写，也不改写批次文件。来源体检不替代语义或视觉审核。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _common import load_batch, units  # noqa: E402
from workflow.content_rules import AI_MARK, is_placeholder  # noqa: E402
from workflow.upload_content import inspect_content  # noqa: E402

ABSENT = '无'


def norm(text):
    # 忽略布局空白，但标点仍是原文的一部分。
    return re.sub(r'\s+', '', text or '')


def iter_explanations(item):
    yield '单元', item.get('explanation')
    for sub in item.get('subquestions') or []:
        if isinstance(sub, dict):
            yield f"子题{sub.get('number')}", sub.get('explanation')


def article_summary(item):
    return item.get('articleSummary', item.get('article_summary'))


def load_sources(paths):
    chunks = []
    for value in paths:
        path = Path(value)
        files = sorted(path.glob('*.txt')) if path.is_dir() else [path]
        for file in files:
            if file.is_file() and file.suffix == '.txt':
                chunks.append(file.read_text(encoding='utf-8-sig', errors='replace'))
    return norm('\n'.join(chunks))


def audit(items, source):
    """缺失可继续；自撰、AI 标记和非字符串值不能伪装成缺失。"""
    stat = {'source': 0, 'ai': 0, 'transcript': 0, 'absent': 0, 'unverified': 0}
    bad = []
    for item in items:
        uid = item.get('unit_id') or item.get('id') or '<无 unit_id>'
        # 书面表达不是客观题：范文/写作指导可有可无，不要求逐字溯源。
        if str(item.get('question_type') or item.get('type') or '').strip() == '书面表达':
            continue
        fields = list(iter_explanations(item)) + [('文章大意', article_summary(item))]
        for where, text in fields:
            if text is None or (isinstance(text, str) and is_placeholder(text)):
                stat['absent'] += 1
            elif not isinstance(text, str):
                bad.append(f'{uid} {where}: 解析/大意必须是字符串')
            elif AI_MARK in text:
                stat['ai'] += 1
                bad.append(f'{uid} {where}: 禁止 AI 生成解析/文章大意')
            elif norm(text) and norm(text) in source:
                stat['source'] += 1
            else:
                stat['unverified'] += 1
                bad.append(f'{uid} {where}: 未能在原文逐字定位')
    return stat, bad


def _parser():
    import argparse
    parser = argparse.ArgumentParser(description='解析/文章大意来源与保真体检')
    parser.add_argument('--batch', required=True, type=Path)
    parser.add_argument('--answers', type=Path, default=None)
    parser.add_argument('--source', action='append', default=[])
    parser.add_argument('--source-dir', action='append', default=[])
    # 保留旧命令行兼容，但不允许绕过真实内容保真约束。
    parser.add_argument('--allow-extra', action='store_true', help='兼容旧参数；不会放行无来源内容')
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        batch = load_batch(args.batch)
        items = [item for item in units(batch) if isinstance(item, dict)]
        paths = list(args.source) + list(args.source_dir)
        source = load_sources(paths)
        stat, bad = audit(items, source)
        # 标准批次按题号、材料边界及章节核验。单独 JSON 没有标准来源布局时，
        # 保留上面的只读来源检查，不把全局出现当作原题归属证明。
        roots = [args.batch.resolve().parent, args.batch.resolve().parent.parent]
        roots.extend(Path(path).resolve().parent.parent for path in paths
                     if Path(path).parent.name in ('01_source', '01_source_ans'))
        root = next((root for root in roots if (root / '01_source').is_dir()), None)
        if root is not None:
            report = inspect_content(root, {'items': items}, strict=True)
            # 标准来源核验比全局文字包含更准确，支持原文格式标记。
            bad = [f"{row['unit_id']} {row['field']}: {row['code']} {row['message']}"
                   for row in report['errors']]
    except (OSError, ValueError, TypeError) as exc:
        print(f'无法核验批次：{exc}')
        return 1
    print(f"检查 {len(items)} 个单元 | 可溯源 {stat['source']} 条 | AI 内容 {stat['ai']} 条 | "
          f"缺失/占位 {stat['absent']} 条 | 未溯源 {stat['unverified']} 条 | 违规 {len(bad)} 条")
    for line in bad[:20]:
        print(f'  ✗ {line}')
    if len(bad) > 20:
        print(f'  … 其余 {len(bad) - 20} 条')
    print(f'解析与文章大意可缺失并归一为「{ABSENT}」；有原文须逐字保留，不生成')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
