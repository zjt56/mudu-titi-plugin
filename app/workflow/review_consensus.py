"""Compare independently assessed structured labels, retaining exact provenance."""
from __future__ import annotations


def _normal(value):
    if isinstance(value, list):
        return sorted(set(value))
    return value


def compare(batch, plan, records):
    from . import review
    from .agent_tasks import confirmed_units
    from workbench import taxonomy
    units = {u['unit_id']: u for u in confirmed_units(batch)}
    types = review._load(batch / '03_types/types.json')
    knowledge = review._load(batch / '04_knowledge/knowledge.json')
    answers = review._load(batch / '01_source_ans/answers.json')
    type_rows = {x['unit_id']: x for x in types.get('items', [])}
    whole_units = {uid for uid, row in type_rows.items()
                   if taxonomy.whole_unit_type(row.get('type'))}
    knowledge_rows = {x['unit_id']: x for x in knowledge.get('items', [])}
    difficulty = answers.get('difficulty') if isinstance(answers, dict) else None
    if not isinstance(difficulty, dict):
        raise ValueError('Missing original question difficulty')
    observations = {}
    def add(key, source, value):
        observations.setdefault(key, []).append((source, _normal(value)))
    for uid, unit in units.items():
        ty, kn = type_rows.get(uid), knowledge_rows.get(uid)
        if not isinstance(ty, dict) or not isinstance(kn, dict):
            raise ValueError('Missing original labels for ' + uid)
        for field, value in {'type': ty.get('type'), 'subtypes': ty.get('subtypes'),
                             'knowledge': kn.get('knowledge')}.items():
            if value is None:
                raise ValueError('Missing original label ' + field + ' for ' + uid)
            add((uid, None, field), {'origin': 'original', 'input_binding': plan['input_binding']}, value)
        children = kn.get('subquestions')
        if not isinstance(children, list):
            raise ValueError('Missing original knowledge children for ' + uid)
        by_number = {str(c['number']): c for c in children}
        group_difficulty = []
        for number in map(str, unit['numbers']):
            value = difficulty.get(number)
            if value not in ('容易', '适中', '困难'):
                raise ValueError('Missing original difficulty for ' + number)
            if uid in whole_units:
                # Whole-passage groups compare difficulty at group level only.
                group_difficulty.append((number, value))
            else:
                add((uid, number, 'difficulty'), {'origin': 'original', 'input_binding': plan['input_binding']}, value)
            # 以组题为准的题型只比组级知识点：group 单元子题 knowledge 不参与一致性比对
            # （补全对话/完形/阅读/信息转换等子题可为空，不要求）。
        if uid in whole_units and group_difficulty:
            # Group-level difficulty is compared per question number, so a round
            # that only sampled some children is not flagged for missing others.
            add((uid, None, 'difficulty'),
                {'origin': 'original', 'input_binding': plan['input_binding']},
                dict(group_difficulty))
    for record in records:
        for check in record['checks']:
            uid = check['unit_id']
            binding = review._sample_source_binding(batch, plan, check) if record['round_id'] == 'sample' else None
            if binding is not None and binding['status'] != 'proven':
                # Keep the response, but do not compare unattributed judgments.
                continue
            assessment = check['assessment']
            if assessment is None:
                # Uncertainty is already a blocking human finding from the
                # round validator; it is never treated as an agreement.
                continue
            raw_number = check.get('number') if record['round_id'] == 'sample' else None
            target = str(raw_number) if raw_number is not None else None
            if record['round_id'] == 'sample':
                partition_id = next((part['execution_id'] for part in record.get('parts', [])
                                     if {'unit_id': uid, 'number': target} in part['targets']),
                                    record['execution_id'])
            else:
                partition_id = next((part['execution_id'] for part in record.get('parts', [])
                                     if check.get('unit_id') in part['targets']),
                                    record['execution_id'])
            source = {'round_id': record['round_id'], 'execution_id': partition_id,
                      'input_binding': plan['input_binding'], 'protocol': plan['protocol'],
                      'target_number': target, 'evidence': check['evidence']}
            if binding is not None:
                source['source_binding'] = binding
            for field in ('type', 'subtypes', 'knowledge'):
                add((uid, None, field), source, assessment[field])
            if uid in whole_units:
                add((uid, None, 'difficulty'), source,
                    {str(item['number']): str(item['value']) for item in assessment['difficulty']
                     if isinstance(item, dict) and item.get('number') is not None and item.get('value')})
            else:
                for item in assessment['difficulty']:
                    add((uid, str(item['number']), 'difficulty'), source, item['value'])
            # 组题（含补全对话等）子题 knowledge 不参与比对，只比组级。
    findings = []
    _DIFF = {'容易': 0, '适中': 1, '困难': 2}

    def _difficulty_numbers(values):
        """Numbers whose grade span reaches conflict level (容易 vs 困难)."""
        by_number = {}
        for v in values:
            pairs = v.items() if isinstance(v, dict) else [(None, v)]
            for n, val in pairs:
                by_number.setdefault(n, set()).add(_DIFF.get(str(val), None))
        return sorted(str(n) for n, grades in by_number.items()
                      if n is not None and len({g for g in grades if g is not None}) >= 2
                      and max(g for g in grades if g is not None) - min(g for g in grades if g is not None) >= 2)

    def _difficulty_conflict(values):
        # Compare per number; a difference is only reported across two grades
        # (容易 vs 困难), never between adjacent grades (容易/适中, 适中/困难).
        by_number = {}
        for v in values:
            pairs = v.items() if isinstance(v, dict) else [(None, v)]
            for n, val in pairs:
                by_number.setdefault(n, set()).add(_DIFF.get(str(val), None))
        for grades in by_number.values():
            grades = {g for g in grades if g is not None}
            if grades and max(grades) - min(grades) >= 2:
                return True
        return False

    def _knowledge_conflict(values):
        # Path lists agree unless two sources give different values under the
        # same dimension root. An extra legal dimension on one side is not a
        # conflict, and a missing dimension is never a conflict.
        by_root = {}
        for v in values:
            roots = {}
            for path in (v or []):
                roots.setdefault(str(path).split('/')[0], set()).add(str(path))
            for r, vals in roots.items():
                by_root.setdefault(r, []).append(frozenset(vals))
        for sets in by_root.values():
            if len(sets) >= 2 and len(set(sets)) > 1:
                return True
        return False

    def agree(field, values):
        if not values:
            return True
        if field == 'difficulty':
            return not _difficulty_conflict(values)
        if field == 'knowledge':
            return not _knowledge_conflict(values)
        return len({repr(v) for v in values}) <= 1

    for (uid, number, field), entries in observations.items():
        if agree(field, [value for _, value in entries]):
            continue
        sources = [{**source, 'field': field, 'value': value} for source, value in entries if source.get('round_id')]
        target = ('第%s题' % number) if number is not None else '题组'
        def _fmt(v):
            if isinstance(v, dict):
                return ','.join('%s:%s' % (k, v[k]) for k in sorted(v))
            return str(v)
        reason = '%s%s判断不一致：%s' % (target, field, '；'.join(
            ('原标注' if source.get('origin') else source['round_id']) + '=' + _fmt(value)
            for source, value in entries))
        if field == 'difficulty' and number is None:
            # 组级难度聚合成 {小题:等级} 后仍要点明是哪些小题拉开的差距，
            # 否则人工只看到一长串 1:容易,10:容易,… 无法下手。
            numbers = _difficulty_numbers([value for _, value in entries])
            if numbers:
                reason += '（差异小题：' + '、'.join('第%s题' % n for n in numbers) + '）'
        findings.append({'unit_id': uid, 'field': field, 'severity': 'major', 'reason': reason,
                         'evidence': [], 'round_sources': sources})
    return findings
