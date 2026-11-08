"""Regression tests for v2 independent comparisons and legacy isolation."""
import copy
import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import review, review_rounds


class ConsensusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # These cases use independent temporary batches and do not test locking.
        lock_patch = patch.object(review_rounds, '_agent_lock', side_effect=lambda *a, **k: contextlib.nullcontext())
        lock_patch.start()
        self.addCleanup(lock_patch.stop)
        self.batch = Path(self.tmp.name)
        self.knowledge = ['语篇体裁/记叙文/故事', '主题/人与自我/生活与学习/兴趣爱好']
        stem = 'She reads a book.\n' + '\n'.join(
            f'{i}. What happens in question {i}?\nA. book{i} B. pen{i} C. map{i} D. bag{i}' for i in range(1, 11))
        self.put('01_source/正文.txt', stem)
        self.put('01_source/structure.confirmed.json', {'units': [{'unit_id': 'g', 'unit_kind': 'group', 'numbers': list(range(1, 11)), 'stem': stem}]})
        self.put('03_types/types.json', {'items': [{'unit_id': 'g', 'type': '阅读理解', 'subtypes': ['选择5题']}]})
        self.put('04_knowledge/knowledge.json', {'items': [{'unit_id': 'g', 'knowledge': self.knowledge, 'subquestions': [{'number': str(i), 'knowledge': []} for i in range(1, 11)]}]})
        self.put('01_source_ans/answers.json', {'answers': {str(i): 'A' for i in range(1, 11)}, 'difficulty': {str(i): '容易' for i in range(1, 11)}})
        self.put('04_knowledge/explanations.json', {'items': [{'unit_id': 'g', 'explanation': '原文写明读书。'}]})
        self.put('06_batch/batch.json', {'items': []})
        self.plan = review.prepare_round_plan(self.batch)

    def put(self, path, value):
        target = self.batch / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def assessment(self, numbers):
        return {'type': '阅读理解', 'subtypes': ['选择5题'], 'knowledge': list(self.knowledge),
                'subquestions': [{'number': n, 'knowledge': []} for n in numbers],
                'difficulty': [{'number': n, 'value': '容易'} for n in numbers]}

    def payload(self):
        records = []
        for rid in review.ROUND_IDS:
            targets = self.plan['sample'] if rid == 'sample' else [{'unit_id': 'g'}]
            checks = []
            for target in targets:
                group_level = rid == 'sample' and target.get('number') is None
                numbers = ([str(i) for i in range(1, 11)] if (group_level or rid != 'sample')
                           else [str(target['number'])])
                assessment = self.assessment(numbers)
                if rid == 'sample':
                    assessment['source_comparison'] = '原文与答案一致'
                check = {'unit_id': target['unit_id'], 'verdict': 'ok',
                         'rationale': '原文说明她读书。',
                         'evidence': [{'path': '01_source/正文.txt', 'locator': 'L1',
                                       'quote': ('She reads a book.' if (group_level or rid != 'sample')
                                                 else f"What happens in question {target['number']}?")}],
                         'assessment': assessment}
                if rid == 'sample' and not group_level:
                    check['number'] = str(target['number'])
                checks.append(check)
            records.append({'round_id': rid, 'input_binding': self.plan['input_binding'], 'execution_id': rid, 'checks': checks})
        return {'plan': self.plan, 'rounds': records}

    @patch.object(review, '_verify_round_sources', return_value=None)
    def test_sample_and_agreement(self, _verify_sources):
        # 阅读理解整组题型：二审整组抽取一个目标，只做组级核对。
        self.assertEqual(len(self.plan['sample']), 1)
        self.assertIsNone(self.plan['sample'][0]['number'])
        result = review.evaluate_rounds(self.batch, self.payload())
        self.assertTrue(result['execution_completed'], result['errors'])
        self.assertTrue(result['passed'], result['errors'])
        self.assertEqual(result['findings'], [])

    @patch.object(review, '_verify_round_sources', return_value=None)
    def test_extra_knowledge_dimension_is_a_difference(self, _verify_sources):
        # 只要知识点集合与原标签不完全相同（含多打/少打一个维度），就是差异，必须进人工。
        payload = self.payload()
        payload['rounds'][0]['checks'][0]['assessment']['knowledge'] = [
            '语篇体裁/记叙文/故事', '主题/人与自我/生活与学习/兴趣爱好', '设题角度/阅读/细节理解']
        result = review.evaluate_rounds(self.batch, payload)
        findings = [f for f in result['findings'] if f['field'] == 'knowledge']
        self.assertEqual(len(findings), 1, result['errors'])
        self.assertFalse(result['passed'])

    @patch.object(review, '_verify_round_sources', return_value=None)
    def test_group_disagreement_one_finding_and_knowledge_order_ignored(self, _verify_sources):
        payload = self.payload()
        # 知识点顺序不是判断差异：反转后仍应视为一致。
        payload['rounds'][0]['checks'][0]['assessment']['knowledge'].reverse()
        self.assertTrue(review.evaluate_rounds(self.batch, payload)['passed'])
        for check in payload['rounds'][1]['checks']:
            check['assessment']['subtypes'] = ['选择4题']
        result = review.evaluate_rounds(self.batch, payload)
        findings = result['findings']
        self.assertEqual(len(findings), 1, result['errors'])
        self.assertEqual(findings[0]['field'], 'subtypes')
        self.assertFalse(result['passed'])

    @patch.object(review, '_verify_round_sources', return_value=None)
    def test_difficulty_is_not_a_manual_compare_finding(self, _verify_sources):
        # 难度不进入人工审核、不参与比对（用户 2026-11 规则）：即使一审难度与原标注相差很大，
        # 也不生成 difficulty 类 finding。
        payload = self.payload()
        check = payload['rounds'][1]['checks'][0]
        for item in check['assessment']['difficulty']:
            item['value'] = '困难' if item['number'] in {'3', '7'} else '容易'
        result = review.evaluate_rounds(self.batch, payload)
        findings = [f for f in result['findings'] if f['field'] == 'difficulty']
        self.assertEqual(findings, [])

    @patch.object(review, '_verify_round_sources', return_value=None)
    def test_listening_passage_group_compares_only_at_group_level(self, _verify_sources):
        # 听力短文是一材料带多题：小题知识点差异不应再生成逐题 finding。
        self.put('03_types/types.json', {'items': [{'unit_id': 'g', 'type': '听力短文理解', 'subtypes': ['选择5题']}]})
        self.plan = review.prepare_round_plan(self.batch)
        payload = self.payload()
        for record in payload['rounds']:
            for check in record['checks']:
                check['assessment']['type'] = '听力短文理解'
                check['assessment']['subtypes'] = []
        sample = payload['rounds'][1]['checks'][0]
        sample['assessment']['subquestions'] = [
            {'number': str(i), 'knowledge': (['设题角度/听力/推理判断'] if i == 1 else [])}
            for i in range(1, 11)]
        result = review.evaluate_rounds(self.batch, payload)
        self.assertTrue(result['execution_completed'], result['errors'])
        self.assertEqual([f for f in result['findings'] if f['field'] == 'child_knowledge'], [])

    @patch.object(review, '_verify_round_sources', return_value=None)
    def test_uncertain_without_labels_remains_open_but_other_rounds_finish(self, _verify_sources):
        payload = self.payload()
        check = payload['rounds'][1]['checks'][0]
        check['verdict'] = 'uncertain'
        check['assessment'] = None
        check['rationale'] = '无法确认该题标签。'
        result = review.evaluate_rounds(self.batch, payload)
        self.assertTrue(result['execution_completed'], result['errors'])
        self.assertFalse(result['passed'])
        self.assertEqual(result['findings'][0]['round_sources'][0]['verdict'], 'uncertain')
        check['verdict'] = 'ok'
        self.assertFalse(review.evaluate_rounds(self.batch, payload)['execution_completed'])

    @patch.object(review, '_verify_round_sources', return_value=None)
    def test_partition_sources_use_real_execution_ids(self, _verify_sources):
        payload = self.payload()
        sample = payload['rounds'][1]
        sample['execution_id'] = 'round-group'
        # 组题整组抽取只有一个目标，因此只有一个真实分片执行 ID。
        sample['parts'] = [{'execution_id': 'part-0', 'targets': [dict(self.plan['sample'][0])]}]
        for check in sample['checks']:
            check['assessment']['subtypes'] = ['选择4题']
        result = review.evaluate_rounds(self.batch, payload)
        self.assertTrue(result['execution_completed'], result['errors'])
        sources = result['findings'][0]['round_sources']
        self.assertEqual({s['execution_id'] for s in sources if s['round_id'] == 'sample'}, {'part-0'})

    @patch.object(review, '_verify_round_sources', return_value=None)
    def test_incomplete_or_old_payload_cannot_pass(self, _verify_sources):
        payload = self.payload()
        payload['rounds'][0]['checks'][0]['assessment'].pop('knowledge')
        self.assertFalse(review.evaluate_rounds(self.batch, payload)['execution_completed'])
        payload = self.payload()
        payload['plan']['protocol'] = 'upload-workflow.review-rounds/v1'
        self.assertFalse(review.evaluate_rounds(self.batch, payload)['execution_completed'])
        with patch('workflow.review_consensus.compare', side_effect=ValueError('missing')):
            result = review.evaluate_rounds(self.batch, self.payload())
            self.assertFalse(result['execution_completed'])
            self.assertFalse(result['passed'])

    def test_compare_failure_does_not_report_complete_on_import(self):
        payload = self.payload()
        for record in payload['rounds']:
            task = review_rounds.export_task(self.batch)
            response = {'status': 'completed', 'artifacts': {'review_round': {'checks': record['checks']}}, 'notes': [], 'findings': []}
            if record['round_id'] == 'sample':
                with patch('workflow.review_consensus.compare', side_effect=ValueError('comparison failed')):
                    report = review_rounds.import_result(self.batch, task['task_id'], response)
                    self.assertFalse(report['completed'])
                    self.assertFalse(report['quality_passed'])
                    self.assertNotEqual(report['action'], 'complete')
            else:
                review_rounds.import_result(self.batch, task['task_id'], response)

    def test_old_transaction_is_rejected_before_replay(self):
        task = 'round-' + 'b' * 32
        self.put('workflow/review-bindings/' + task + '.json', {'plan': self.plan, 'context_version': 'blind-context-v4', 'task': {'protocol': 'upload-workflow.review-rounds/v1'}})
        response = {'anything': True}
        self.put('workflow/review-transactions/' + task + '.json', {'task_id': task, 'result_digest': review._digest(response), 'record': {}})
        with self.assertRaises(ValueError):
            review_rounds.import_result(self.batch, task, response)
        with self.assertRaises(ValueError):
            review_rounds._finish_install(self.batch, {'task_id': task})
        self.assertFalse((self.batch / '07_review/review-rounds.json').exists())

    def test_export_isolates_old_transaction_and_recovers_current_one(self):
        old = 'round-' + '0' * 32
        old_binding = {'plan': self.plan, 'context_version': 'blind-context-v4',
                       'task': {'protocol': review.ROUND_PROTOCOL}}
        old_transaction = {'task_id': old, 'result_digest': 'old', 'record': {}}
        binding_path = 'workflow/review-bindings/' + old + '.json'
        transaction_path = 'workflow/review-transactions/' + old + '.json'
        self.put(binding_path, old_binding)
        self.put(transaction_path, old_transaction)
        old_bytes = [(self.batch / p).read_bytes() for p in (binding_path, transaction_path)]

        task = review_rounds.export_task(self.batch)
        self.assertEqual(task['action'], 'exported')
        record = self.payload()['rounds'][0]
        response = {'status': 'completed', 'artifacts': {'review_round': {'checks': record['checks']}},
                    'notes': [], 'findings': []}
        with patch.object(review_rounds, '_finish_install', side_effect=RuntimeError('interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                review_rounds.import_result(self.batch, task['task_id'], response)
        next_task = review_rounds.export_task(self.batch)
        self.assertEqual(next_task['action'], 'exported')
        self.assertNotEqual(next_task['round_id'], task['round_id'])
        self.assertTrue((self.batch / ('workflow/review-bindings/' + task['task_id'] + '.receipt.json')).is_file())
        self.assertFalse((self.batch / ('workflow/review-bindings/' + old + '.receipt.json')).exists())
        self.assertEqual(old_bytes, [(self.batch / p).read_bytes() for p in (binding_path, transaction_path)])

    def test_recovery_does_not_hide_damaged_current_transaction(self):
        task = 'round-' + 'a' * 32
        binding = {'plan': self.plan, 'context_version': 'blind-context-v7',
                   'task': {'protocol': review.ROUND_PROTOCOL}}
        self.put('workflow/review-bindings/' + task + '.json', binding)
        path = 'workflow/review-transactions/' + task + '.json'
        self.put(path, {'task_id': task})
        with self.assertRaises((KeyError, ValueError)):
            review_rounds.export_task(self.batch)
        self.put(path, '{invalid json')
        with self.assertRaises(ValueError):
            review_rounds.export_task(self.batch)
        self.put(path, {'task_id': 'round-' + 'b' * 32})
        with self.assertRaisesRegex(ValueError, 'does not match'):
            review_rounds.export_task(self.batch)

    def test_old_receipt_is_rejected_before_replay(self):
        task = 'round-' + 'a' * 32
        self.put('workflow/review-bindings/' + task + '.json', {'plan': self.plan, 'context_version': 'blind-context-v4', 'task': {'protocol': 'upload-workflow.review-rounds/v1'}})
        result = {'anything': True}
        self.put('workflow/review-bindings/' + task + '.receipt.json', {'result_digest': review._digest(result), 'report': {'completed': True}})
        with self.assertRaises(ValueError):
            review_rounds.import_result(self.batch, task, result)

    def test_real_imports_and_replay(self):
        payload = self.payload()
        for record in payload['rounds']:
            task = review_rounds.export_task(self.batch)
            self.assertEqual(task['action'], 'exported', task)
            self.assertEqual(task['round_id'], record['round_id'])
            response = {'status': 'completed', 'artifacts': {'review_round': {'checks': record['checks']}}, 'notes': [], 'findings': []}
            report = review_rounds.import_result(self.batch, task['task_id'], response)
            self.assertEqual(review_rounds.import_result(self.batch, task['task_id'], response), report)
        self.assertTrue(report['completed'], report)
        self.assertTrue(report['quality_passed'], report)

    def test_receipt_replay_reflects_removed_round_without_changing_receipt(self):
        for record in self.payload()['rounds']:
            task = review_rounds.export_task(self.batch)
            response = {'status': 'completed', 'artifacts': {'review_round': {'checks': record['checks']}},
                        'notes': [], 'findings': []}
            report = review_rounds.import_result(self.batch, task['task_id'], response)
        self.assertTrue(report['completed'])
        receipt = self.batch / ('workflow/review-bindings/' + task['task_id'] + '.receipt.json')
        original_receipt = receipt.read_bytes()
        path = self.batch / '07_review' / review.ROUND_FILE
        rounds = json.loads(path.read_text(encoding='utf-8'))
        rounds['rounds'].pop()
        path.write_text(json.dumps(rounds, ensure_ascii=False), encoding='utf-8')
        replay = review_rounds.import_result(self.batch, task['task_id'], response)
        self.assertFalse(replay['completed'])
        self.assertFalse(replay['quality_passed'])
        self.assertEqual(replay['action'], 'continue')
        self.assertFalse(replay['summary']['execution_completed'])
        self.assertNotIn('round_completed', replay)
        self.assertEqual(receipt.read_bytes(), original_receipt)
        self.assertEqual(review_rounds.export_task(self.batch)['round_id'], task['round_id'])

    def test_import_persists_cross_round_disagreement(self):
        payload = self.payload()
        for check in payload['rounds'][1]['checks']:
            check['assessment']['subtypes'] = ['选择4题']
        for record in payload['rounds']:
            task = review_rounds.export_task(self.batch)
            response = {'status': 'completed', 'artifacts': {'review_round': {'checks': record['checks']}}, 'notes': [], 'findings': []}
            report = review_rounds.import_result(self.batch, task['task_id'], response)
        self.assertTrue(report['completed'])
        self.assertFalse(report['quality_passed'])
        queue = json.loads((self.batch / '07_review/manual-review-queue.json').read_text())['items']
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]['field'], 'subtypes')
        # 一审一条组级来源 + 二审整组一条来源（组题不再按小题拆分来源）
        self.assertEqual(len(queue[0]['round_sources']), 2)
        self.assertEqual(review_rounds.import_result(self.batch, task['task_id'], response), report)
        self.assertEqual(len(json.loads((self.batch / '07_review/manual-review-queue.json').read_text())['items']), 1)

    @patch.object(review, '_verify_round_sources', return_value=None)
    def test_old_decision_does_not_hide_new_sources(self, _verify_sources):
        payload = self.payload()
        # 用知识点差异（真实审核范畴）验证“旧决定不能掩盖新来源”。
        payload['rounds'][1]['checks'][0]['assessment']['knowledge'] = ['语篇体裁/记叙文/故事', '主题/人与自我/生活与学习/日常活动']
        result = review.evaluate_rounds(self.batch, payload)
        finding = result['findings'][0]
        decision = {**copy.deepcopy(finding), 'status': 'resolved', 'decided_at': 'today', 'decided_by': 'human', 'resolution_note': '已核实'}
        decision['round_sources'] = decision['round_sources'][:1]
        self.put('07_review/manual-review-queue.json', {'items': [decision]})
        self.assertFalse(review.evaluate_rounds(self.batch, payload)['passed'])


if __name__ == '__main__':
    unittest.main()
