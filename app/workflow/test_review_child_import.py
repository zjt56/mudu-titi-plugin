"""Synthetic real export/import coverage for per-child source attribution."""
import copy
import json
import unittest

from workflow import review, review_rounds
from workflow import test_review_rounds as fixtures


class ChildImportTest(unittest.TestCase):
    """非整组题型仍按小题抽样，本测试覆盖小题级来源归属的隔离。

    阅读理解/完形等整组题型二审已改为整组抽取、只做组级核对（见
    test_review_consensus），逐小题归属只用于仍按小题抽样的题型。
    """

    def setUp(self):
        self.fixture = fixtures.ReviewRoundsTest('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.batch = self.fixture.batch
        self.knowledge = {'knowledge': ['语用/情景交际']}
        self.blocks = {n: f'{n}. Which item belongs to number {n}?\nA. apple{n}\nB. banana{n}\nC. cat{n}\nD. dog{n}' for n in range(2, 16)}
        self.stem = 'Shared passage.\nTable | 1. ____ | name\n' + '\n'.join(self.blocks.values())
        self.fixture.write('01_source/structure.confirmed.json', {'units': [{'unit_id': 'g', 'unit_kind': 'group', 'numbers': list(range(1, 16)), 'stem': self.stem}]})
        self.fixture.write('03_types/types.json', {'items': [{'unit_id': 'g', 'type': '单项填空', 'subtypes': ['四个选项']}]})
        self.fixture.write('04_knowledge/knowledge.json', {'items': [{'unit_id': 'g', 'knowledge': self.knowledge['knowledge'], 'subquestions': [{'number': str(n), 'knowledge': []} for n in range(1, 16)]}]})
        self.fixture.write('01_source_ans/answers.json', {'answers': {str(n): 'A' for n in range(1, 16)}, 'difficulty': {str(n): '容易' for n in range(1, 16)}})
        # Sampling is deliberately independent of source bytes. Vary the
        # synthetic unit topology, not irrelevant paper text, to include table 1.
        for salt in range(100):
            self.uid = f'g-{salt}'
            self.fixture.write('01_source/structure.confirmed.json', {'units': [{'unit_id': self.uid, 'unit_kind': 'group', 'numbers': list(range(1, 16)), 'stem': self.stem}]})
            self.fixture.write('03_types/types.json', {'items': [{'unit_id': self.uid, 'type': '单项填空', 'subtypes': ['四个选项']}]})
            self.fixture.write('04_knowledge/knowledge.json', {'items': [{'unit_id': self.uid, 'knowledge': self.knowledge['knowledge'], 'subquestions': [{'number': str(n), 'knowledge': []} for n in range(1, 16)]}]})
            self.fixture.write('01_source/正文.txt', 'Paper\n' + self.stem + '\n')
            plan = review.prepare_round_plan(self.batch)
            if any(q['number'] == '1' for q in plan['sample']):
                self.plan = plan
                break
        else:
            self.fail('Synthetic deterministic sample did not include table')

    def assessment(self, numbers):
        return {'type': '单项填空', 'subtypes': ['四个选项'], 'knowledge': self.knowledge['knowledge'],
                'subquestions': [{'number': n, 'knowledge': []} for n in numbers],
                'difficulty': [{'number': n, 'value': '容易'} for n in numbers]}

    def test_real_import_isolates_unproven_children_and_retains_response(self):
        sample = self.plan['sample']
        valid = next(q for q in sample if q['number'] != '1')
        wrong = next(q for q in sample if q['number'] not in ('1', valid['number']))
        for rid in review.ROUND_IDS:
            task = review_rounds.export_task(self.batch)
            self.assertEqual(task.get('round_id'), rid, task)
            checks = []
            for target in (sample if rid == 'sample' else [{'unit_id': self.uid}]):
                numbers = [target['number']] if rid == 'sample' else [str(n) for n in range(1, 16)]
                assessment = self.assessment(numbers)
                quote = 'Shared passage.'
                if rid == 'sample':
                    assessment['source_comparison'] = '对照原文。'
                    if target == valid:
                        quote = f"Which item belongs to number {valid['number']}?"
                        assessment['difficulty'][0]['value'] = '困难'
                    elif target == wrong:
                        quote = f"Which item belongs to number {valid['number']}?"
                        assessment['difficulty'][0]['value'] = '适中'
                    else:
                        assessment['difficulty'][0]['value'] = '适中'
                checks.append({**target, 'verdict': 'ok', 'rationale': '根据原文核对。',
                               'evidence': [{'path': '01_source/正文.txt', 'locator': '正文', 'quote': quote}],
                               'assessment': assessment})
            response = {'status': 'completed', 'artifacts': {'review_round': {'checks': checks}}, 'notes': [], 'findings': []}
            report = review_rounds.import_result(self.batch, task['task_id'], response)
        self.assertTrue(report['completed'])
        self.assertFalse(report['quality_passed'])
        evaluated = review.evaluate_rounds(self.batch)
        unchecked = [x for x in evaluated['unchecked'] if x['code'] == 'REVIEW_SAMPLE_SOURCE_UNVERIFIED']
        self.assertEqual({x['number'] for x in unchecked}, {'1', wrong['number']})
        self.assertTrue(all(x['source_binding']['source_sha256'] == self.plan['input_hashes']['01_source/正文.txt'] for x in unchecked))
        # 难度不参与审核比对：即使难度被改成困难，也不产生 difficulty finding。
        findings = [f for f in evaluated['findings'] if f['field'] == 'difficulty']
        self.assertEqual(findings, [])
        retained = list((self.batch / 'workflow/review-results' / task['task_id']).glob('*.json'))
        self.assertTrue(any(json.loads(p.read_text()) == response for p in retained))
        self.assertEqual(json.loads((self.batch / '07_review/review-rounds.json').read_text())['rounds'][-1]['checks'], checks)
        self.assertFalse(review.evaluate(self.batch)['passed'])


if __name__ == '__main__':
    unittest.main()
