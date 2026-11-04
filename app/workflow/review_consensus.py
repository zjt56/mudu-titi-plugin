"""Compare independently assessed structured labels, retaining exact provenance."""
from __future__ import annotations


def _normal(value):
    if isinstance(value, list):
        return sorted(set(value))
    return value


def compare(batch, plan, records):
    from . import review
    from .agent_tasks import confirmed_units
    units = {u['unit_id']: u for u in confirmed_units(batch)}
    types = review._load(batch / '03_types/types.json')
    knowledge = review._load(batch / '04_knowledge/knowledge.json')
    answers = review._load(batch / '01_source_ans/answers.json')
    type_rows = {x['unit_id']: x for x in types.get('items', [])}
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
        for number in map(str, unit['numbers']):
            value = difficulty.get(number)
            if value not in ('容易', '适中', '困难'):
                raise ValueError('Missing original difficulty for ' + number)
            add((uid, number, 'difficulty'), {'origin': 'original', 'input_binding': plan['input_binding']}, value)
            if unit.get('unit_kind') == 'group':
                child = by_number.get(number)
                if not isinstance(child, dict) or not isinstance(child.get('knowledge'), list):
                    raise ValueError('Missing original child knowledge for ' + number)
                add((uid, number, 'child_knowledge'), {'origin': 'original', 'input_binding': plan['input_binding']}, child['knowledge'])
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
            target = str(check['number']) if record['round_id'] == 'sample' else None
            partition_id = next((part['execution_id'] for part in record.get('parts', [])
                                 if (check.get('unit_id') in part['targets'] if target is None else
                                     {'unit_id': uid, 'number': target} in part['targets'])),
                                record['execution_id'])
            source = {'round_id': record['round_id'], 'execution_id': partition_id,
                      'input_binding': plan['input_binding'], 'protocol': plan['protocol'],
                      'target_number': target, 'evidence': check['evidence']}
            if binding is not None:
                source['source_binding'] = binding
            for field in ('type', 'subtypes', 'knowledge'):
                add((uid, None, field), source, assessment[field])
            for item in assessment['difficulty']:
                add((uid, str(item['number']), 'difficulty'), source, item['value'])
            for item in assessment['subquestions']:
                add((uid, str(item['number']), 'child_knowledge'), source, item['knowledge'])
    findings = []
    for (uid, number, field), entries in observations.items():
        if len({repr(value) for _, value in entries}) <= 1:
            continue
        sources = [{**source, 'field': field, 'value': value} for source, value in entries if source.get('round_id')]
        target = ('第%s题' % number) if number is not None else '题组'
        reason = '%s%s判断不一致：%s' % (target, field, '；'.join(
            ('原标注' if source.get('origin') else source['round_id']) + '=' + str(value)
            for source, value in entries))
        findings.append({'unit_id': uid, 'field': field, 'severity': 'major', 'reason': reason,
                         'evidence': [], 'round_sources': sources})
    return findings
