"""Semantic identity of the validated type/confirmed-unit inputs consumed by cuts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def listening_context(batch):
    # Use the same ID, coverage and catalog validation as the downstream importer.
    # Never fall back to a second, more permissive interpretation of structure.
    from workflow.agent_tasks import confirmed_units, _types
    root = Path(batch)
    units = confirmed_units(root)
    try:
        payload = json.loads((root / '03_types/types.json').read_text(encoding='utf-8-sig'))
        types = _types(payload, units)
        for uid, row in types.items():
            if ('words' not in row or isinstance(row['words'], bool)
                    or not isinstance(row['words'], int) or row['words'] < 0
                    or not isinstance(row.get('contentComplete'), bool)):
                raise ValueError('types row %s needs nonnegative integer words and boolean contentComplete' % uid)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ValueError('听力切段等待有效且完整覆盖确认单元的 types.json：' + str(exc)) from exc
    semantic = []
    listening = []
    for unit in units:
        uid = unit['unit_id']
        row = types[uid]
        numbers = [int(number) for number in unit['numbers']]
        semantic.append({'unit': unit, 'type': row['type'], 'subtypes': sorted(row['subtypes'])})
        if row['type'].startswith('听力'):
            listening.append({'unit_id': uid, 'numbers': numbers, 'type': row['type']})
    semantic.sort(key=lambda row: row['unit']['unit_id'])
    digest = hashlib.sha256(json.dumps(semantic, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
    binding = {'version': 1, 'sha256': digest,
               'listening_units': sorted(listening, key=lambda row: row['numbers'][0])}
    return binding, binding['listening_units']


def assert_current(batch, expected):
    actual, _ = listening_context(batch)
    if actual != expected:
        raise ValueError('切片证据已过期：题型或确认单元版本已改变，必须重新切段')
    return actual
