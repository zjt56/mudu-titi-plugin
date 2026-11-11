"""Model-free scoped answer copying through the ordinary task/import transaction.

Only a complete set of unique, coordinate-bound original answers qualifies. Missing,
conflicting or unbound content declines this path; it never becomes a placeholder.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import upload_content as source


def build_payload(batch_dir, retained_payload=None):
    """Read current identities and originals; optionally recover same-value candidates.

    Retained values supply no ownership evidence. Their current question IDs, values
    and every recovered citation must independently satisfy the source validator.
    """
    from .agent_tasks import confirmed_units
    from .question_identity import question_key
    batch = Path(batch_dir)
    try:
        units = confirmed_units(batch)
        if not units or any(u.get('identity_version') != 'scoped-v1' for u in units):
            return None
        keys = {question_key(u, n) for u in units for n in u['numbers']}
        retained = {}
        if retained_payload is not None:
            if not isinstance(retained_payload, dict):
                return None
            retained = retained_payload.get('answers')
            missing = retained_payload.get('missing', {})
            if (not isinstance(retained, dict) or not isinstance(missing, dict)
                    or missing or not set(retained) <= keys
                    or any(not isinstance(v, str) or not v.strip() for v in retained.values())):
                return None
        answers, evidence = {}, {}
        for unit in units:
            if unit.get('type') == '书面表达':
                continue  # No fabricated model answer, and no absence assertion.
            for number in unit['numbers']:
                key = question_key(unit, number)
                row = source.resolve_scoped_answer(batch, unit, number, retained.get(key))
                if row is None:
                    return None
                value = retained.get(key, row['answer'])
                source.validate_scoped_answer(batch, unit, number, value, row['evidence'])
                answers[key], evidence[key] = value, row['evidence']
        if not answers:
            return None
        return {'answers': answers, 'evidence': evidence, 'missing': {}}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def install(batch_dir):
    """Deliver exclusively through export/import freshness and consumer validation."""
    import sys
    from . import agent_protocol, agent_tasks, scoped_regeneration
    batch = Path(batch_dir)
    if scoped_regeneration.requested(batch, 'answers') or build_payload(batch) is None:
        return None
    exported = agent_protocol.export_task(batch, 'answers', max_input_chars=sys.maxsize)
    if exported.get('status') != 'ready':
        raise ValueError('答案直抄前置交付未满足：' + json.dumps(exported, ensure_ascii=False))
    payload = build_payload(batch)
    if payload is None:
        raise ValueError('答案直抄导出后来源已变化或不再唯一；候选未交付')
    response = {'protocol': agent_protocol.PROTOCOL, 'task_id': exported['task_id'],
                'result': {'status': 'completed',
                           'artifacts': {agent_tasks.ARTIFACTS['answers']: payload},
                           'notes': ['按当前大题身份与原文坐标直接抄录完整答案，未调用模型'],
                           'findings': []}}
    response_path = Path(exported['package_dir']) / 'direct-result.json'
    agent_protocol._atomic(response_path, agent_protocol._encoded(response))
    report = agent_protocol.import_result(batch, exported['task_id'], response_path)
    if report.get('completed') is not True:
        raise ValueError('答案直抄候选未通过交付校验：' + json.dumps(report, ensure_ascii=False))
    return batch / agent_tasks.ARTIFACTS['answers']
