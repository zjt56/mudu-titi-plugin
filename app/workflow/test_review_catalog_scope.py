import json
from pathlib import Path
import unittest

from workflow import review_catalog_scope as scope

ROOT = Path(__file__).resolve().parent.parent


class ScopedReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mapping = json.loads((ROOT / 'references/type-knowledge-map.json').read_text())
        cls.catalog = json.loads((ROOT / 'references/knowledge-catalog.json').read_text())

    def request(self):
        return scope.build_scope_request('原卷：选择正确答案。', ['q1'], self.mapping, self.catalog)

    def decision(self, types, branches, status='determined'):
        return {'units': [{'unit_id': 'q1', 'status': status, 'types': types,
                           'branches': branches, 'reason': '根据原卷题干判断'}]}

    def test_blind_two_calls_and_reduced_catalog(self):
        seen = []
        def classify(request):
            seen.append(request)
            return self.decision(['听力短对话理解'], ['主题/人与自我', '设题角度/听力'])
        def review(request):
            seen.append(request)
            return {'status': 'completed', 'checks': []}
        result = scope.run_scoped_review('原卷：选择正确答案。', ['q1'], self.mapping, self.catalog, classify, review)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['model_calls'], 2)
        self.assertLess(result['catalog_entries_sent'], len(self.catalog['points']))
        self.assertNotIn('points', seen[0])
        paths = {p['path'] for p in seen[1]['units']['q1']['points']}
        self.assertIn('设题角度/听力/细节理解', paths)
        self.assertNotIn('设题角度/阅读/细节理解', paths)
        self.assertNotIn('主题/人与社会', paths)

    def test_uncertainty_missing_dimension_and_exception_fail_closed(self):
        for decision in [self.decision(['听力短对话理解'], ['主题'], 'uncertain'),
                         self.decision(['听力短对话理解'], ['主题'])]:
            called = []
            result = scope.run_scoped_review('原文', ['q1'], self.mapping, self.catalog,
                                             lambda _: decision, lambda x: called.append(x))
            self.assertEqual(result['status'], 'needs_review')
            self.assertEqual(result['model_calls'], 1)
            self.assertEqual(called, [])
        result = scope.run_scoped_review('原文', ['q1'], self.mapping, self.catalog,
                                         lambda _: 1 / 0, lambda _: self.fail('review called'))
        self.assertEqual(result['status'], 'needs_review')
        self.assertNotIn('division', result['reason'])

    def test_unknown_and_out_of_scope_rejected(self):
        for decision in [self.decision(['听力短对话理解'], ['主题', '语法']),
                         self.decision(['不存在'], ['主题']),
                         self.decision(['听力短对话理解'], ['主题', '设题角度/阅读'])]:
            with self.assertRaises(scope.ScopeError):
                scope.resolve_scope(self.request(), decision, self.mapping, self.catalog)

    def test_compound_and_alias_mapping(self):
        paths = {p['path'] for p in self.catalog['points']}
        self.assertEqual(scope._roots('听力信息转换', self.mapping, paths),
                         {'语篇体裁', '主题', '设题角度/听力', '词汇', '语法'})
        aliases = {'by_type': {'示例': {'dims': [{'branch': '阅读技能'}]}}}
        self.assertEqual(scope._roots('示例', aliases, paths), {'设题角度/阅读'})

    def test_failed_final_call_not_completed(self):
        result = scope.run_scoped_review('原文', ['q1'], self.mapping, self.catalog,
                                         lambda _: self.decision(['听力短对话理解'], ['主题', '设题角度/听力']),
                                         lambda _: {'status': 'needs_review'})
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(result['model_calls'], 2)

    def test_target_failures_are_isolated_and_bind_round(self):
        calls = []
        def classify(request):
            target = request['unit_ids'][0]
            calls.append(('scope', target))
            return {'units': [{'unit_id': target, 'status': 'uncertain' if target == 'bad' else 'determined',
                               'types': ['听力短对话理解'], 'branches': ['主题/人与自我', '设题角度/听力'], 'reason': '原卷'}]}
        def review(request):
            target = next(iter(request['units']))
            calls.append(('review', target))
            return {'status': 'completed', 'artifacts': {'review_round': {'checks': [
                {'unit_id': target, 'verdict': 'ok', 'assessment': {'type': '听力短对话理解',
                 'knowledge': ['设题角度/听力/细节理解'], 'subquestions': []}}]}}}
        result = scope.run_independent_targets('原卷', ['bad', 'good'], 'labels', self.mapping, self.catalog, classify, review)
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(result['targets']['bad']['status'], 'needs_review')
        self.assertEqual(result['targets']['good']['status'], 'completed')
        self.assertEqual(calls, [('scope', 'bad'), ('scope', 'good'), ('review', 'good')])
        self.assertEqual(result['targets']['good']['binding']['round_id'], 'labels')
        next_round = scope.run_independent_targets('原卷', ['good'], 'sample', self.mapping, self.catalog, classify, review)
        self.assertNotEqual(result['targets']['good']['binding'], next_round['targets']['good']['binding'])

    def test_assessment_cannot_escape_scope_or_use_parent(self):
        request = self.request()
        decision = self.decision(['听力短对话理解'], ['主题/人与自我', '设题角度/听力'])
        allowed = scope.resolve_scope(request, decision, self.mapping, self.catalog)['q1']
        for point in ['设题角度/阅读/细节理解', '主题/人与自我', '捏造']:
            check = {'unit_id': 'q1', 'verdict': 'ok', 'assessment': {'type': '听力短对话理解',
                     'knowledge': [point], 'subquestions': []}}
            with self.assertRaises(scope.ScopeError):
                scope.validate_assessment(check, 'q1', allowed, self.catalog)


if __name__ == '__main__':
    unittest.main()
