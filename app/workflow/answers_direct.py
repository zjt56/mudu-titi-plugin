"""Model-free scoped answer copying through the ordinary task/import transaction.

Only a complete set of unique, coordinate-bound original answers qualifies. Missing,
conflicting or unbound content declines this path; it never becomes a placeholder.
"""
from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path

from . import upload_content as source


def legacy_verified_units(batch, units):
    """Copy only whole units with unique answers inside matching paper sections."""
    from .agent_tasks import _answer_comparison_text, _safe_path
    from .content_rules import is_placeholder
    from .listening_script import without_scripts
    batch = Path(batch)
    if not units or any(unit.get('identity_version') or unit.get('source_span', {}).get('number_binding')
                        == 'unnumbered_section' for unit in units):
        return {}
    try:
        def read(relative):
            return _safe_path(batch, relative).read_bytes()

        def sha(raw):
            return hashlib.sha256(raw).hexdigest()

        gate = json.loads(read('07_review/structure-gate.json'))
        if gate.get('passed') is not True:
            return {}
        manifest = json.loads(read('workflow/manifest.json'))
        inputs = manifest.get('inputs') or {}
        original = (inputs.get('source') or {}).get('path')
        if not original or sha(Path(original).read_bytes()) != gate.get('original_sha256'):
            return {}
        bindings = gate.get('input_hashes', {})
        source_files = ('01_source/正文.txt', '01_source/structure.json',
                        '01_source/structure.confirmed.json')
        body_raw, structure_raw, confirmed_raw = (read(name) for name in source_files)
        if any(sha(raw) != bindings.get(name) for name, raw in zip(
                source_files, (body_raw, structure_raw, confirmed_raw))):
            return {}
        body = body_raw.decode('utf-8-sig').splitlines()
        structure = json.loads(structure_raw)
        owners, headings = {}, set()
        for row in structure.get('sections', []):
            index = row.get('source_line', 0)
            if type(index) is not int or not 1 <= index <= len(body):
                return {}
            title = source._section(source._source_line(body[index - 1]))
            if not title or not all(title) or title in headings:
                return {}
            headings.add(title)
            for entry in row.get('questions', []):
                numbers = range(entry[0], entry[-1] + 1) if isinstance(entry, list) else (entry,)
                for number in numbers:
                    if str(number) in owners:
                        return {}
                    owners[str(number)] = title

        # Use the same material that the answer task exports and validates.
        prepared = '01_source_ans/正文.txt'
        has_prepared = _safe_path(batch, prepared).is_file()
        if not has_prepared and (inputs.get('answers') or {}).get('path'):
            return {}
        path = prepared if has_prepared else '01_source/附录.txt'
        if path in source.reference_only_sources(batch):
            return {}
        lines = list(without_scripts(map(source._source_line, read(path).decode('utf-8-sig').splitlines())))
        candidates, uncertain = {}, {}
        section = None
        for index, line in enumerate(lines, 1):
            title = source._section(line)
            if title:
                section = title if title in headings and title[1] != '书面表达' else None
            if section is None:
                continue
            parsed = source._answer_items(line)
            independent_key = (len(parsed) > 1 or re.search(source.EXPLANATION_LABEL, line)
                               or '【答案】' in line
                               or index < len(lines) and source.STRAY_EXPLANATION.match(lines[index]))
            for number, value in parsed:
                key = str(number)
                if owners.get(key) != section or not value or is_placeholder(value):
                    continue
                target = candidates if independent_key or re.fullmatch('[A-G]', value) else uncertain
                target.setdefault(key, []).append((value, f'{path}:{index}'))
        result = {}
        for unit in units:
            keys = list(map(str, unit['numbers']))
            if unit.get('type') == '书面表达':
                result[unit['unit_id']] = {'answers': {}, 'evidence': {}, 'missing': {}}
                continue
            if any(key not in candidates or len({_answer_comparison_text(value) for value, _ in
                                                 candidates[key] + uncertain.get(key, [])}) != 1
                   for key in keys):
                continue
            result[unit['unit_id']] = {
                'answers': {key: candidates[key][0][0] for key in keys},
                'evidence': {key: list(dict.fromkeys(ref for _, ref in candidates[key])) for key in keys},
                'missing': {},
            }
        return result
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError):
        return {}


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
        if not units:
            return None
        if all(not u.get('identity_version') for u in units):
            if retained_payload is not None:
                return None
            verified = legacy_verified_units(batch, units)
            if len(verified) != len(units):
                return None
            combined = {key: {number: value for row in verified.values() for number, value in row[key].items()}
                        for key in ('answers', 'evidence', 'missing')}
            return combined if combined['answers'] else None
        if any(u.get('identity_version') != 'scoped-v1' for u in units):
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
    complete = build_payload(batch)
    if complete is None:
        return None
    # Localized recovery must keep the accepted untouched baseline. Prove the
    # full original set first, then submit only selected identities to the same
    # scoped importer/merge validator used by model responses.
    context, _, missing, units = agent_tasks._collect(batch, 'answers')
    chosen = scoped_regeneration.selected('answers', context, units)
    if missing:
        return None
    if chosen is not None:
        candidate = scoped_regeneration._subset('answers', complete, chosen)
        try:
            scoped_regeneration.merge(batch, 'answers', candidate, context, units, notes=[])
        except (OSError, ValueError, KeyError, TypeError):
            return None
    exported = agent_protocol.export_task(batch, 'answers', max_input_chars=sys.maxsize)
    if exported.get('status') != 'ready':
        raise ValueError('答案直抄前置交付未满足：' + json.dumps(exported, ensure_ascii=False))
    payload = build_payload(batch)
    if payload is None:
        raise ValueError('答案直抄导出后来源已变化或不再唯一；候选未交付')
    if chosen is not None:
        payload = scoped_regeneration._subset('answers', payload, chosen)
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
