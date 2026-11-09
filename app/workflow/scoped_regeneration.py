"""Explicit content repair scopes shared by CLI and portable task import.

Only selected units are model work. Untouched rows come from the hash-bound
canonical artifact, are validated, and cannot be rewritten by the model.
"""
import copy
from pathlib import Path

STAGES = frozenset(('answers', 'explanations', 'types', 'knowledge', 'audio'))


def selected(stage, context, units):
    repair = context.get('repair_feedback') or {}
    if not repair.get('scoped_units'):
        return None
    if stage not in STAGES:
        raise ValueError('Scoped regeneration is limited to content stages')
    ids = set(repair['unit_ids'])
    chosen = [u for u in units if u['unit_id'] in ids]
    if not ids or {u['unit_id'] for u in chosen} != ids:
        raise ValueError('Scoped regeneration requires known nonempty unit IDs')
    if stage == 'audio' and any(not str(u.get('type', '')).startswith('听力') for u in chosen):
        raise ValueError('Scoped audio repair requires listening units')
    return chosen


def requested(batch, stage):
    """Validate on-disk feedback before deciding whether to bypass fast paths."""
    from . import agent_tasks
    if stage not in STAGES:
        return False
    relative = f'workflow/repair-feedback/{stage}.json'
    if not agent_tasks._safe_path(batch, relative).is_file():
        return False
    units = agent_tasks.confirmed_units(batch)
    repair = agent_tasks._repair_feedback(batch, stage, units, {})
    return selected(stage, {'repair_feedback': repair}, units) is not None


def project(stage, context, units):
    from . import agent_tasks, unit_context
    value = unit_context.project_indexed(context, units)
    ids = {u['unit_id'] for u in units}
    # Old labels are retained by the importer, not sent as work to the model.
    value.pop(agent_tasks.ARTIFACTS[stage], None)
    for name in ('types', 'explanations', 'knowledge', 'audio'):
        document = value.get(agent_tasks.ARTIFACTS[name])
        if isinstance(document, dict) and isinstance(document.get('items'), list):
            document['items'] = [r for r in document['items'] if r.get('unit_id') in ids]
    return value


def _subset(stage, payload, units):
    ids = {u['unit_id'] for u in units}
    if stage == 'answers':
        nums = {str(n) for u in units for n in u['numbers']}
        return {key: {n: copy.deepcopy(v) for n, v in mapping.items() if n in nums}
                for key, mapping in payload.items()}
    result = {'items': [copy.deepcopy(r) for r in payload['items'] if r.get('unit_id') in ids]}
    if stage == 'audio':
        result.update({k: copy.deepcopy(payload[k]) for k in ('unit_binding', 'generation') if k in payload})
    return result


def audio_validation(payload, context):
    """Adapt the real cutter schema without changing its stored provenance."""
    from . import agent_tasks
    agent_tasks._fields(payload, ('items', 'unit_binding', 'generation'), ('items', 'unit_binding'))
    parent = context.get('02_audio/segment-evidence.json') or {}
    if not parent.get('unit_binding') or payload['unit_binding'] != parent['unit_binding']:
        raise ValueError('Audio map binding differs from verified cuts')
    if payload.get('generation') != parent.get('generation'):
        raise ValueError('Audio map generation differs from verified cuts')
    rows = []
    for row in payload['items']:
        agent_tasks._fields(row, ('unit_id', 'file', 'start', 'end', 'evidence',
                                 'source_audio', 'source_path', 'source_sha256'),
                            ('unit_id', 'file', 'start', 'end'))
        matching = [r for r in parent.get('items', []) if all(
            r.get(k) == row[k] for k in ('unit_id', 'file', 'start', 'end'))]
        if len(matching) != 1 or not matching[0].get('evidence'):
            raise ValueError('Audio map lacks unique parent segment evidence')
        if any(agent_tasks._sanitized(row[k]) != matching[0].get(k)
               for k in ('source_audio', 'source_path', 'source_sha256') if k in row):
            raise ValueError('Audio map source differs from verified cuts')
        normalized = {k: copy.deepcopy(row[k]) for k in ('unit_id', 'file', 'start', 'end')}
        normalized['evidence'] = copy.deepcopy(row.get('evidence', matching[0]['evidence']))
        rows.append(normalized)
    return {'items': rows}


def merge(batch, stage, payload, context, units, *, notes=None):
    """Validate scoped output, merge a bound baseline and validate full coverage."""
    from . import agent_tasks
    chosen = selected(stage, context, units)
    if chosen is None:
        return payload
    ids = {u['unit_id'] for u in chosen}
    eligible = [u for u in units if stage != 'audio' or str(u.get('type', '')).startswith('听力')]
    untouched = [u for u in eligible if u['unit_id'] not in ids]
    candidate = copy.deepcopy(payload)
    if stage == 'answers':
        allowed = {str(n) for u in chosen for n in u['numbers']}
        agent_tasks._fields(candidate, ('answers', 'evidence', 'missing', 'difficulty', 'difficultyEvidence'),
                            ('answers', 'evidence', 'missing'))
        if any(not isinstance(v, dict) or not set(v) <= allowed for v in candidate.values()):
            raise ValueError('Scoped answer repair contains unselected numbers')
    else:
        agent_tasks._fields(candidate, ('items',), ('items',))
        agent_tasks._coverage(candidate, chosen)
    agent_tasks._validate(Path(batch), stage, candidate, project(stage, context, chosen), chosen, notes=notes)
    if stage == 'audio':
        parent = agent_tasks._json(agent_tasks._safe_path(batch, '02_audio/segment-evidence.json'))
        if agent_tasks._sanitized(parent) != context.get('02_audio/segment-evidence.json'):
            raise ValueError('Audio parent evidence changed during scoped merge')
        candidate.update({k: copy.deepcopy(parent[k]) for k in ('unit_binding', 'generation') if k in parent})
        for row in candidate['items']:
            origin = next(r for r in parent['items'] if all(
                r.get(k) == row[k] for k in ('unit_id', 'file', 'start', 'end')))
            row.update({k: copy.deepcopy(origin[k]) for k in ('source_audio', 'source_path', 'source_sha256') if k in origin})
    if not untouched:
        agent_tasks._validate(Path(batch), stage, candidate, context, units, notes=notes)
        return candidate
    path = agent_tasks._safe_path(batch, agent_tasks.ARTIFACTS[stage])
    try:
        prior = agent_tasks._json(path)
        # Reject malformed/foreign baseline rows, including duplicate targets.
        if stage == 'answers':
            agent_tasks._fields(prior, ('answers', 'evidence', 'missing', 'difficulty', 'difficultyEvidence'),
                                ('answers', 'evidence', 'missing'))
            if any(not isinstance(v, dict) for v in prior.values()):
                raise ValueError('Invalid baseline answer mapping')
        else:
            allowed = ('items', 'unit_binding', 'generation') if stage == 'audio' else ('items',)
            agent_tasks._fields(prior, allowed, ('items',))
            rows = prior['items']
            if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
                raise ValueError('Invalid baseline items')
            prior_ids = [r.get('unit_id') for r in rows]
            known = {u['unit_id'] for u in eligible}
            if len(prior_ids) != len(set(prior_ids)) or not set(prior_ids) <= known:
                raise ValueError('Foreign or duplicate baseline unit IDs')
        old = _subset(stage, prior, untouched)
        checked = copy.deepcopy(old)
        agent_tasks._validate(Path(batch), stage, checked, project(stage, context, untouched), untouched)
        if checked != old:
            raise ValueError('Untouched units require content changes')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError('Scoped repair cannot validate untouched units; refusing whole-paper regeneration: ' + str(exc)) from exc
    if stage == 'answers':
        result = copy.deepcopy(old)
        for key, values in candidate.items():
            result.setdefault(key, {}).update(values)
    else:
        replacements = {r['unit_id']: r for r in candidate['items']}
        result = copy.deepcopy(prior) if stage == 'audio' else {}
        merged_rows = []
        for row in prior['items']:
            replacement = replacements.pop(row['unit_id'], None)
            if replacement is not None and stage == 'audio':
                replacement = dict(row, **replacement)
            merged_rows.append(copy.deepcopy(row) if replacement is None else replacement)
        result['items'] = merged_rows
        result['items'].extend(replacements[u['unit_id']] for u in chosen if u['unit_id'] in replacements)
        if stage == 'audio':
            result.update({k: copy.deepcopy(candidate[k]) for k in ('unit_binding', 'generation') if k in candidate})
    agent_tasks._validate(Path(batch), stage, result, context, units, notes=notes)
    retained = _subset(stage, result, untouched)
    retained_items = retained.get('items')
    old_items = old.get('items')
    changed = retained_items != old_items if stage == 'audio' else retained != old
    if changed:
        raise ValueError('Scoped repair attempted to change untouched units')
    return result
