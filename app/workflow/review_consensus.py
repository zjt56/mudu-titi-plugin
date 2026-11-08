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
    type_rows = {x['unit_id']: x for x in types.get('items', [])}
    knowledge_rows = {x['unit_id']: x for x in knowledge.get('items', [])}
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
            reused = next((ref for ref in record.get('reuse', [])
                           if review._check_key(ref, record['round_id']) == (uid, target)), None)
            origin = review._reuse_source(batch, reused, record['round_id'], plan)[1] if reused else record
            if record['round_id'] == 'sample':
                partition_id = next((part['execution_id'] for part in origin.get('parts', [])
                                     if {'unit_id': uid, 'number': target} in part['targets']),
                                    origin['execution_id'])
            else:
                partition_id = next((part['execution_id'] for part in origin.get('parts', [])
                                     if check.get('unit_id') in part['targets']),
                                    origin['execution_id'])
            source = {'round_id': record['round_id'], 'execution_id': partition_id,
                      'input_binding': origin['input_binding'], 'protocol': plan['protocol'],
                      'target_number': target, 'evidence': check['evidence'],
                      # 携带该轮审阅者的判断理由与裁决，供 review._is_capability_gap
                      # 正确识别「未实际听音/未查看图片」等能力受限，不把它们误当
                      # 知识点分歧进人工队列/阻断闸门。
                      'rationale': check.get('rationale'),
                      'verdict': check.get('verdict')}
            if binding is not None:
                source['source_binding'] = binding
            for field in ('type', 'subtypes', 'knowledge'):
                add((uid, None, field), source, assessment[field])
    findings = []
    def _knowledge_conflict(values):
        # 知识点差异就是要把「原标签 vs 一审 vs 二审」不同之处交给人工：
        # 任一来源的知识点路径集合与其它来源不完全相同即为差异
        # （包括换了维度根，如 语法/副词/… vs 词汇/词汇辨析/…；也包括少打/多打维度）。
        sets = {frozenset(v or []) for v in values}
        return len(sets) > 1

    def agree(field, values):
        if not values:
            return True
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
        findings.append({'unit_id': uid, 'field': field, 'severity': 'major', 'reason': reason,
                         'evidence': [], 'round_sources': sources})
    return findings
