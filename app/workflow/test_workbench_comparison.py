"""Read-only comparisons use current bound records and exact targets."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workbench.server import finding_bucket, finding_comparison, review_comparison, group_duplicate_findings


class ComparisonTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.write('06_batch/batch.json', {'items': [
            {'unit_id': 'group-1-2', 'question_type': '阅读',
             'filterMetadata': {'subtypes': ['选择']}, 'knowledge': ['组'],
             'subquestions': [{'number': 1, 'knowledge': ['甲']}, {'number': 2, 'knowledge': ['乙']}]},
            {'unit_id': 'other-2', 'knowledge': ['其他']} ]})
        self.write('07_review/review-rounds.json', {'rounds': [
            {'round_id': 'knowledge', 'execution_id': 'e2', 'checks': [
                {'unit_id': 'group-1-2', 'verdict': 'error', 'rationale': '证据', 'evidence': ['引用'],
                 'assessment': {'knowledge': ['新组'], 'subquestions': [
                     {'number': '1', 'knowledge': ['新甲']}, {'number': '2', 'knowledge': ['乙']}]}}]},
            {'round_id': 'sample', 'execution_id': 'e3', 'checks': [
                {'unit_id': 'other-2', 'number': '2', 'assessment': {'source_comparison': '别题'}},
                {'unit_id': 'group-1-2', 'number': '1', 'assessment': {'source_comparison': '实际判断'}}]}]})

    def write(self, path, value):
        path = self.folder / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def test_bound_rounds_show_unified_fields_and_exact_sample_targets(self):
        from workflow import review
        from workflow.test_review_rounds import ReviewRoundsTest

        fixture = ReviewRoundsTest(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        payload = fixture.payload()
        sampled = {str(c['number']) for c in payload['rounds'][2]['checks']}
        selected = next(iter(sampled))
        unsampled = next(str(n) for n in range(1, 7) if str(n) not in sampled)
        payload['rounds'][0]['checks'][0]['assessment']['difficulty'][int(selected) - 1]['value'] = '困难'
        payload['rounds'][1]['checks'][0]['assessment']['type'] = '阅读理解'
        # This display fixture synthesizes assessments, so only provenance
        # verification is stubbed; plan and input binding validation stay real.
        with patch('workflow.review._verify_round_sources'):
            self.assertEqual(review.evaluate_rounds(fixture.batch, payload)['checked_rounds'], ['labels', 'knowledge', 'sample'])
            fixture.write('07_review/review-rounds.json', payload)
            comparison = review_comparison(fixture.batch, 'group-1-6', list(range(1, 7)))
        finding = {'unit_id': 'group-1-6', 'field': 'difficulty', 'reason': '第%s题难度不一致' % selected}
        result = finding_comparison(finding, comparison, list(range(1, 7)), {})
        self.assertEqual(result['original'], '容易')
        self.assertEqual([r['checks'][0]['value'] for r in result['rounds']], ['困难', '容易', '容易'])
        self.assertEqual(str(result['rounds'][2]['checks'][0]['number']), selected)
        group = finding_comparison({'unit_id': 'group-1-6', 'field': 'knowledge'}, comparison, list(range(1, 7)), {})
        self.assertEqual(group['rounds'][0]['checks'][0]['value'], fixture.knowledge['knowledge'])
        self.assertEqual({str(c['number']) for c in group['rounds'][2]['checks']}, sampled)
        self.assertGreater(len(group['rounds'][2]['checks']), 1)
        self.assertEqual(finding_bucket({'field': 'child_knowledge'}), 'label')
        child = finding_comparison({'unit_id': 'group-1-6', 'field': 'child_knowledge',
                                    'reason': '第%s题知识点不一致' % unsampled}, comparison, list(range(1, 7)), {})
        self.assertEqual(child['original'], ['设题角度/阅读/细节理解'])
        self.assertEqual(child['rounds'][0]['checks'][0]['value'], child['original'])
        self.assertEqual(child['rounds'][2]['status'], '未覆盖')
        type_comparison = finding_comparison({'unit_id': 'group-1-6', 'field': 'type'}, comparison, list(range(1, 7)), {})
        self.assertEqual(type_comparison['rounds'][1]['checks'][0]['value'], '阅读理解')
        self.assertIsNone(finding_comparison({'unit_id': 'group-1-6', 'field': 'difficulty', 'reason': '无题号'}, comparison, list(range(1, 7)), {}))
        self.assertIsNone(finding_comparison({'unit_id': 'other', 'field': 'knowledge'}, comparison, list(range(1, 7)), {}))
        self.assertIsNone(finding_comparison({'unit_id': 'a', 'field': 'knowledge'}, comparison, list(range(1, 7)), {'a': '1', 'b': '1'}))

        # Changing any bound input makes the old rounds unusable as evidence.
        fixture.write('01_source/正文.txt', 'changed source')
        with patch('workflow.review._verify_round_sources'):
            stale = review_comparison(fixture.batch, 'group-1-6', list(range(1, 7)))
        self.assertEqual(stale['original']['labels'], {})
        self.assertTrue(all(r['status'] == '待重新审核' and not r['checks'] for r in stale['rounds']))

    def test_detail_api_reports_untrusted_history_without_exposing_results(self):
        from workbench import server
        from workflow.test_workbench_catalog_archive import invoke
        from workflow.test_workbench_group_save import make_batch

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = make_batch(root)
            (folder / '07_review').mkdir()
            old = {'rounds': [
                {'round_id': 'labels', 'checks': [{'unit_id': 'FX-001', 'assessment': {'type': '历史秘密值'}}]},
                {'round_id': 'sample', 'checks': [{'unit_id': 'FX-001', 'number': '31',
                                                   'assessment': {'type': '历史秘密值'}}]},
            ]}
            (folder / '07_review' / 'review-rounds.json').write_text(json.dumps(old, ensure_ascii=False))
            queue = {'items': [{'finding_id': 'f1', 'unit_id': 'FX-001', 'field': 'type',
                                'reason': '题型有疑点', 'status': 'open'}]}
            (folder / '07_review' / 'manual-review-queue.json').write_text(json.dumps(queue, ensure_ascii=False))
            with patch.object(server, 'WORKFLOWS', root):
                result = invoke('GET', '/api/batches/batch_fixture/units/FX-001')
            self.assertEqual([r['status'] for r in result['comparison']['rounds']],
                             ['待重新审核', '未提供', '待重新审核'])
            self.assertTrue(all(not r['checks'] for r in result['comparison']['rounds']))
            self.assertNotIn('历史秘密值', json.dumps(result, ensure_ascii=False))
            finding_rounds = result['label_findings'][0]['comparison']['rounds']
            self.assertEqual([r['status'] for r in finding_rounds], ['待重新审核', '未提供', '待重新审核'])

    def test_unverified_history_is_directory_wide_and_never_exposes_contents(self):
        from workbench import server
        from workflow.test_workbench_catalog_archive import invoke
        from workflow.test_workbench_group_save import make_batch

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = make_batch(root)
            (folder / '07_review').mkdir()
            (folder / '07_review' / 'manual-review-queue.json').write_text(json.dumps({'items': [
                {'finding_id': 'f1', 'unit_id': 'FX-001', 'field': 'type', 'reason': '题型疑点', 'status': 'open'}]}))
            history = folder / 'workflow' / 'review-history'
            history.mkdir(parents=True)
            archive = history / ('a' * 32 + '.json')
            for old in ({'plan': {'input_binding': '其他输入'}, 'rounds': []},
                        {'plan': {'input_binding': '不同批次'}, 'rounds': [
                            {'round_id': 'unrelated', 'checks': [{'unit_id': '别题', 'assessment': '历史秘密值'}]}]}):
                archive.write_text(json.dumps(old, ensure_ascii=False), encoding='utf-8')
                with patch.object(server, 'WORKFLOWS', root):
                    result = invoke('GET', '/api/batches/batch_fixture/units/FX-001')
                self.assertEqual([r['status'] for r in result['comparison']['rounds']], ['历史来源未验证'] * 3)
                self.assertEqual([r['status'] for r in result['label_findings'][0]['comparison']['rounds']], ['历史来源未验证'] * 3)
                self.assertNotIn('历史秘密值', json.dumps(result, ensure_ascii=False))
                self.assertTrue(all(not r['checks'] for r in result['comparison']['rounds']))

    def test_corrupt_and_outside_history_are_ignored(self):
        from workbench.server import has_unverified_review_history
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 'batch'
            history = folder / 'workflow' / 'review-history'
            history.mkdir(parents=True)
            target = root / 'outside.json'
            target.write_text(json.dumps({'plan': {}, 'rounds': []}))
            (history / ('a' * 32 + '.json')).symlink_to(target)
            (history / ('b' * 32 + '.json')).write_text('{broken')
            (history / ('c' * 32 + '.json')).write_bytes(b'\\xff')
            (history / ('d' * 32 + '.json')).write_text(json.dumps({'plan': {}, 'rounds': {}}))
            (history / 'not-an-archive.json').write_text(target.read_text())
            self.assertFalse(has_unverified_review_history(folder))
            other = root / 'other'
            (other / 'workflow').mkdir(parents=True)
            (other / 'workflow' / 'review-history').symlink_to(history, target_is_directory=True)
            self.assertFalse(has_unverified_review_history(other))
            (history / ('e' * 32 + '.json')).write_text(target.read_text())
            self.assertTrue(has_unverified_review_history(folder))
            self.assertFalse(has_unverified_review_history(other))

    def test_frontend_round_status_text(self):
        import shutil
        import subprocess
        node = shutil.which('node')
        if not node:
            self.skipTest('node unavailable')
        app = (Path(__file__).resolve().parents[1] / 'workbench/static/app.js').read_text(encoding='utf-8')
        function = app[app.index('    function roundText('):app.index('    // 只给一句话结论', app.index('    function roundText('))]
        script = function + "\nconsole.log(JSON.stringify([roundText({status:'待重新审核'}), roundText({status:'历史来源未验证'}), roundText({status:'未提供'}), roundText({status:'未覆盖',round_id:'sample'}), roundText({status:'已提供',round_id:'sample',checks:[{number:'31',value:'真实值'}]})]));"
        script = "function comparisonText(v) { return String(v); }\n" + script
        result = subprocess.run([node, '-e', script], check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout), ['历史记录已过期或无法验证，待重新审核', '当前无有效记录；目录有来源未验证的历史文件', '未执行', '未抽中', '抽中第31题：真实值'])

    def test_only_identical_open_findings_are_grouped(self):
        import copy
        base = {'finding_id': 'a', 'unit_id': 'g', 'field': 'knowledge', 'reason': '题组知识不一致',
                'evidence': ['原文'], 'status': 'open', 'round_sources': [
                    {'round_id': 'labels', 'target_number': None, 'field': 'knowledge', 'value': ['甲']} ]}
        def variant(fid, **changes):
            item = copy.deepcopy(base)
            item.update(finding_id=fid, **changes)
            return item
        records = [base, variant('same'), variant('resolved', status='fixed'),
                   variant('field', field='type'), variant('unit', unit_id='other'),
                   variant('reason', reason='题组另一知识冲突'),
                   variant('target1', field='child_knowledge', reason='第1题知识不一致'),
                   variant('target2', field='child_knowledge', reason='第2题知识不一致'),
                   variant('value', round_sources=[{'round_id': 'labels', 'target_number': None,
                                                    'field': 'knowledge', 'value': ['乙']}]),
                   variant('structured-target', round_sources=[{'round_id': 'labels', 'target_number': '2',
                                                                 'field': 'knowledge', 'value': ['甲']}]),
                   variant('sample', field='sample'),
                   variant('no-evidence1', evidence=[], round_sources=[]),
                   variant('no-evidence2', evidence=[], round_sources=[])]
        for whole_unit in (False, True):
            with self.subTest(whole_unit=whole_unit):
                result = group_duplicate_findings(records, whole_unit=whole_unit)
                self.assertEqual(result[0]['member_ids'], ['a', 'same'])
                self.assertEqual(result[0]['members'], records[:2])
                self.assertEqual([r['member_ids'] for r in result[1:]],
                                 [[r['finding_id']] for r in records[3:]])
                self.assertNotIn('resolved', [fid for group in result for fid in group['member_ids']])
                self.assertEqual(records[2]['status'], 'fixed')

    def test_stale_rounds_and_missing_original_are_not_invented(self):
        with patch('workflow.review.evaluate_rounds', return_value={'checked_rounds': []}):
            result = review_comparison(self.folder, 'group-1', [1])
        self.assertIsNone(result['original']['source'])
        self.assertEqual(result['original']['labels'], {})
        self.assertEqual([r['status'] for r in result['rounds']], ['未提供', '待重新审核', '待重新审核'])
        self.assertTrue(all(not r['checks'] for r in result['rounds']))


if __name__ == '__main__':
    unittest.main()
