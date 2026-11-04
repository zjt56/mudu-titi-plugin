"""Fail-closed, blind catalog scoping for independent semantic review.

This module does not dispatch models or install review evidence. The caller must
provide original-paper text only, and supply an independent model adapter. It
must bind successful results to the review task and validate final assessments.
"""
from __future__ import annotations

import hashlib
import json


class ScopeError(ValueError):
    """The available evidence cannot establish a safe catalog scope."""


def _roots(type_name, type_map, paths):
    spec = type_map.get('by_type', {}).get(type_name)
    if not isinstance(spec, dict) or not spec.get('dims'):
        raise ScopeError('题型无可用目录映射')
    roots = set()
    for dim in spec['dims']:
        branch = dim.get('branch')
        if branch == '词汇/语法':
            candidates = ('词汇', '语法')
        elif branch == '阅读技能':
            candidates = ('设题角度/阅读',)
        elif branch == '设题角度':
            scope = dim.get('scope')
            if scope not in ('听力', '阅读'):
                raise ScopeError('设题角度缺少可验证范围')
            candidates = ('设题角度/' + scope,)
        elif branch in ('主题', '词汇', '语法', '语用', '语篇体裁', '语音'):
            candidates = (branch,)
        else:
            raise ScopeError('存在未知目录映射')
        for candidate in candidates:
            if candidate not in paths:
                raise ScopeError('映射分支不在目录中')
            roots.add(candidate)
    return roots


def build_scope_request(source_text, unit_ids, type_map, catalog):
    """Construct a first-stage request with source only and shallow catalog names.

    Never pass batch objects, upstream labels, answer artifacts or diagnostics as
    source_text. The caller is responsible for sourcing the actual paper text and
    ensuring each unit_id and text match the immutable source binding.
    """
    if not isinstance(source_text, str) or not source_text.strip():
        raise ScopeError('缺少原卷文字')
    if not isinstance(unit_ids, list) or not unit_ids or any(not isinstance(x, str) or not x for x in unit_ids) or len(set(unit_ids)) != len(unit_ids):
        raise ScopeError('目标单元无效')
    points = catalog.get('points', [])
    paths = [p.get('path') for p in points if isinstance(p, dict)]
    if not paths or any(not isinstance(p, str) or not p for p in paths) or len(set(paths)) != len(paths):
        raise ScopeError('目录路径无效')
    # At most two levels are supplied: no leaf-level catalog or IDs.
    shallow = sorted(p for p in paths if len(p.split('/')) <= 2 and any(q.startswith(p + '/') for q in paths))
    types = sorted(type_map.get('by_type', {}))
    if not shallow or not types:
        raise ScopeError('题型或目录为空')
    return {'instruction': '依据原卷独立判断每个目标的题型和所需全部相关目录分支。仅选择给定的一级或二级分支；对无法可信覆盖的范围返回 uncertain，不得猜测。不得参考任何既有标签。每题给出 unit_id、status=determined|uncertain、types、branches、reason。',
            'source_text': source_text, 'unit_ids': list(unit_ids),
            'available_types': types, 'available_branches': shallow}


def resolve_scope(request, response, type_map, catalog):
    """Validate complete model selections and return per-unit allowed entries.

    A selected parent branch includes all its descendants. Every mapped root must
    be covered by at least one selected branch; the independent model is
    responsible for asserting its selection is complete for the source.
    """
    if not isinstance(response, dict) or set(response) != {'units'} or not isinstance(response['units'], list):
        raise ScopeError('范围判断响应格式无效')
    rows = response['units']
    wanted = request['unit_ids']
    if len(rows) != len(wanted) or any(not isinstance(r, dict) for r in rows):
        raise ScopeError('范围判断未覆盖目标')
    ids = [r.get('unit_id') for r in rows]
    if len(set(ids)) != len(ids) or set(ids) != set(wanted):
        raise ScopeError('范围判断目标不匹配')
    points = catalog['points']
    paths = {p['path'] for p in points}
    allowed_shallow = set(request['available_branches'])
    resolved = {}
    for row in rows:
        if set(row) != {'unit_id', 'status', 'types', 'branches', 'reason'} or row['status'] != 'determined':
            raise ScopeError('范围不确定或响应格式无效')
        types, branches = row['types'], row['branches']
        if (not isinstance(types, list) or not types or any(not isinstance(t, str) for t in types)
                or len(types) != len(set(types)) or any(t not in request['available_types'] for t in types)
                or not isinstance(branches, list) or not branches or any(not isinstance(b, str) for b in branches)
                or len(branches) != len(set(branches)) or any(b not in allowed_shallow for b in branches)
                or not isinstance(row['reason'], str) or not row['reason'].strip()):
            raise ScopeError('范围判断内容无效')
        roots = set().union(*(_roots(t, type_map, paths) for t in types))
        if any(not any(b == root or b.startswith(root + '/') for root in roots) for b in branches):
            raise ScopeError('所选分支超出题型允许范围')
        if any(not any(b == root or b.startswith(root + '/') for b in branches) for root in roots):
            raise ScopeError('所选分支未覆盖所有题型维度')
        selected = [dict(p) for p in points if any(p['path'] == b or p['path'].startswith(b + '/') or b.startswith(p['path'] + '/') for b in branches)]
        if not selected or len(selected) >= len(points):
            raise ScopeError('目录范围为空或未收敛')
        resolved[row['unit_id']] = {'types': list(types), 'branches': list(branches), 'points': selected}
    return resolved


def run_scoped_review(source_text, unit_ids, type_map, catalog, classify, review):
    """Run at most one classifier and one review call; never retry or fall back.

    Adapters accept a JSON-serializable request and return a JSON object. Review
    receives per-unit catalogs, never the full catalog or upstream labels.
    Caller must independently validate assessments and all ordinary review gates.
    """
    calls = 0
    try:
        request = build_scope_request(source_text, unit_ids, type_map, catalog)
        calls += 1
        response = classify(request)
        scope = resolve_scope(request, response, type_map, catalog)
        review_request = {'source_text': source_text, 'units': scope}
        # Reject a union that silently reconstructs the entire catalog.
        union = {p['path'] for u in scope.values() for p in u['points']}
        if len(union) >= len(catalog['points']):
            raise ScopeError('合并目录未收敛')
        calls += 1
        result = review(review_request)
        if not isinstance(result, dict) or result.get('status') != 'completed':
            raise ScopeError('审核调用未完成')
        return {'status': 'completed', 'result': result, 'model_calls': calls,
                'scope_request_chars': len(json.dumps(request, ensure_ascii=False)),
                'review_request_chars': len(json.dumps(review_request, ensure_ascii=False)),
                'catalog_entries_sent': len(union)}
    except Exception as exc:
        return {'status': 'needs_review', 'reason': str(exc) if isinstance(exc, ScopeError) else '模型调用或范围判断失败', 'model_calls': calls}


def validate_assessment(check, unit_id, allowed, catalog):
    """Reject assessments outside the independent scope, including non-leaves."""
    if not isinstance(check, dict) or check.get('unit_id') != unit_id:
        raise ScopeError('审核目标不匹配')
    assessment = check.get('assessment')
    if check.get('verdict') == 'uncertain' and assessment is None:
        return
    if check.get('verdict') not in ('ok', 'error') or not isinstance(assessment, dict):
        raise ScopeError('审核判断无效')
    if assessment.get('type') not in allowed['types']:
        raise ScopeError('审核题型超出独立判断范围')
    paths = {p['path'] for p in catalog['points']}
    leaves = {p for p in paths if not any(q.startswith(p + '/') for q in paths)}
    allowed_paths = {p['path'] for p in allowed['points']} & leaves
    entries = assessment.get('knowledge')
    children = assessment.get('subquestions')
    if not isinstance(entries, list) or not isinstance(children, list):
        raise ScopeError('审核知识点格式无效')
    for child in children:
        if not isinstance(child, dict) or not isinstance(child.get('knowledge'), list):
            raise ScopeError('子题知识点格式无效')
        entries = entries + child['knowledge']
    if any(not isinstance(p, str) or p not in allowed_paths for p in entries):
        raise ScopeError('审核知识点超出独立判断范围或不是目录叶子')


def _digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def run_independent_targets(source_text, target_ids, round_id, type_map, catalog, classify, review):
    """Prototype: isolate failures per target and bind both calls to their inputs.

    This returns candidates only; it does not install formal review records. The
    caller must load source_text from the hashed original paper and verify target
    binding, ordinary review evidence, independent executions and final coverage.
    Up to two calls per target, without retries. Scope failures never skip later
    targets; the overall status remains incomplete until all targets complete.
    """
    if round_id not in ('labels', 'knowledge', 'sample') or not isinstance(target_ids, list) or not target_ids or any(not isinstance(x, str) or not x for x in target_ids) or len(set(target_ids)) != len(target_ids):
        raise ScopeError('轮次或目标无效')
    results = {}
    for target in target_ids:
        calls = 0
        try:
            request = build_scope_request(source_text, [target], type_map, catalog)
            calls += 1
            decision = classify(request)
            allowed = resolve_scope(request, decision, type_map, catalog)[target]
            review_request = {'source_text': source_text, 'units': {target: allowed}}
            calls += 1
            response = review(review_request)
            checks = response['artifacts']['review_round']['checks']
            if response.get('status') != 'completed' or not isinstance(checks, list) or len(checks) != 1:
                raise ScopeError('审核调用未完成或目标覆盖不符')
            validate_assessment(checks[0], target, allowed, catalog)
            if checks[0].get('verdict') == 'uncertain':
                raise ScopeError('审核结果不确定')
            binding = {'round_id': round_id, 'unit_id': target, 'source_digest': _digest(source_text),
                       'catalog_digest': _digest(catalog), 'mapping_digest': _digest(type_map),
                       'scope_request_digest': _digest(request), 'scope_decision_digest': _digest(decision),
                       'review_request_digest': _digest(review_request), 'review_result_digest': _digest(response)}
            results[target] = {'status': 'completed', 'binding': binding, 'scope': allowed,
                               'result': response, 'model_calls': calls}
        except Exception as exc:
            results[target] = {'status': 'needs_review', 'model_calls': calls,
                               'reason': str(exc) if isinstance(exc, ScopeError) else '模型调用或响应验证失败'}
    return {'status': 'completed' if all(r['status'] == 'completed' for r in results.values()) else 'needs_review',
            'targets': results}
