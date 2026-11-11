import json
import tempfile
import unittest
import zipfile
from xml.sax.saxutils import escape
from pathlib import Path
from unittest.mock import patch

from workflow import manifest as mf, provisional


class ProvisionalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        self.put('original.txt', 'original')
        self.put('01_source/正文.txt', '1. First question.\n2. Second question.\nA. one\nB. two\nC. three\nD. four\n3. Next question.\n')
        self.put('original.txt', (self.batch / '01_source/正文.txt').read_text())
        self.put('01_source/structure.json', {'sections': []})
        self.put('01_source/structure.confirmed.json', {'units': [
            {'unit_kind': 'standalone', 'numbers': [1], 'stem': '1. A'},
            {'unit_kind': 'standalone', 'type': '单项填空', 'numbers': [2], 'stem': '2. Second question.\nA. one\nB. two\nC. three\nD. four'}]})
        self.put('workflow/manifest.json', {'inputs': {'source': {'path': str(self.batch / 'original.txt')}}, 'stages': {}})
        self.report = {'passed': False, 'blockers': [{'code': 'ISSUE', 'affected_numbers': [1]}],
                       'original_sha256': mf.sha256_file(self.batch / 'original.txt'),
                       'input_hashes': {p: mf.sha256_file(self.batch / p) for p in
                           ('01_source/正文.txt', '01_source/structure.json', '01_source/structure.confirmed.json')}}
        self.put('07_review/structure-gate.json', self.report)
        self.manifest_before = (self.batch / 'workflow/manifest.json').read_bytes()

    def assert_formal_stages_untouched(self):
        # load() migrates absent stages in memory; provisional work must neither
        # persist that migration nor mark any formal stage as executed.
        self.assertEqual((self.batch / 'workflow/manifest.json').read_bytes(), self.manifest_before)
        self.assertTrue(all(row['status'] == mf.PENDING
                            for row in mf.load(self.batch)['stages'].values()))

    def put(self, name, value):
        path = self.batch / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding='utf-8')

    def test_scoped_units_do_not_enter_legacy_number_candidates(self):
        self.put('01_source/structure.confirmed.json', {'identity_version': 'scoped-v1', 'units': []})
        with patch('workflow.provisional.local_agent.invoke', side_effect=AssertionError('No model calls')):
            outcome = provisional.run(self.batch)
        self.assertEqual(outcome['code'], 'unsupported_scoped_identity')
        self.assertEqual(outcome['attempted'], 0)
        self.assertFalse((self.batch / 'workflow/provisional').exists())
        self.assert_formal_stages_untouched()

    def test_executes_only_unaffected_candidate_without_formal_outputs(self):
        result = {'status': 'completed', 'findings': [], 'notes': [], 'artifacts': {
            '03_types/types.json': {'items': [{'unit_id': 'q-2', 'type': '单项填空',
                                               'subtypes': ['四个选项'], 'evidence': ['source']}]}}}
        with patch('workflow.provisional.local_agent.invoke', return_value={'response': result}) as invoke:
            outcome = provisional.run(self.batch)
        self.assertEqual((outcome['status'], outcome['candidate_count']), ('needs_review', 1))
        self.assertEqual(invoke.call_count, 1)
        self.assertIn('Second question.', invoke.call_args.args[0])
        self.assertFalse((self.batch / '03_types/types.json').exists())
        self.assert_formal_stages_untouched()
        summary = json.loads((self.batch / outcome['summary']).read_text())
        self.assertFalse(summary['authoritative'])
        self.assertEqual(summary['units'][0]['unit_id'], 'q-2')

    def test_one_candidate_failure_does_not_stop_next_candidate(self):
        text = '1. First.\n2. Second.\nA. one\nB. two\nC. three\nD. four\n3. Third.\nA. five\nB. six\nC. seven\nD. eight\n4. Next.'
        self.put('01_source/正文.txt', text)
        self.put('original.txt', text)
        self.report['original_sha256'] = mf.sha256_file(self.batch / 'original.txt')
        self.put('01_source/structure.confirmed.json', {'units': [
            {'unit_kind': 'standalone', 'numbers': [1], 'stem': '1. First.'},
            {'unit_kind': 'standalone', 'type': '单项填空', 'numbers': [2], 'stem': '2. Second.\nA. one\nB. two\nC. three\nD. four'},
            {'unit_kind': 'standalone', 'type': '单项填空', 'numbers': [3], 'stem': '3. Third.\nA. five\nB. six\nC. seven\nD. eight'}]})
        self.report['input_hashes'] = {p: mf.sha256_file(self.batch / p) for p in self.report['input_hashes']}
        self.put('07_review/structure-gate.json', self.report)
        response = {'status': 'completed', 'findings': [], 'notes': [], 'artifacts': {
            '03_types/types.json': {'items': [{'unit_id': 'q-3', 'type': '单项填空',
                'subtypes': ['四个选项'], 'evidence': ['正文第3题']}]}}}
        with patch('workflow.provisional.local_agent.invoke', side_effect=[ValueError('第2题调用失败'), {'response': response}]) as invoke:
            result = provisional.run(self.batch)
        self.assertEqual(invoke.call_count, 2)
        self.assertEqual(result['candidate_count'], 1)
        rows = json.loads((self.batch / result['summary']).read_text())['units']
        self.assertEqual([x['status'] for x in rows], ['needs_review', 'candidate'])
        self.assertFalse((self.batch / '03_types/types.json').exists())
        original_slice = provisional.preview.source_slice
        def slice_with_failure(batch, numbers):
            if int(numbers[0]) == 2:
                raise OSError('模拟单题读取失败')
            return original_slice(batch, numbers)
        with patch('workflow.provisional.preview.source_slice', side_effect=slice_with_failure), \
                patch('workflow.provisional.local_agent.invoke', return_value={'response': response}):
            outcome = provisional.run(self.batch)
        rows = json.loads((self.batch / outcome['summary']).read_text())['units']
        self.assertIn('单题读取失败', rows[0]['reason'])
        self.assertEqual(rows[1]['status'], 'candidate')

    def test_original_txt_mismatch_and_unsupported_source_reject_types(self):
        self.put('original.txt', (self.batch / '01_source/正文.txt').read_text().replace('B. two', 'B. altered'))
        self.report['original_sha256'] = mf.sha256_file(self.batch / 'original.txt')
        self.put('07_review/structure-gate.json', self.report)
        with patch('workflow.provisional.local_agent.invoke') as invoke:
            result = provisional.run(self.batch)
        self.assertEqual(result['candidate_count'], 0)
        invoke.assert_not_called()
        self.put('paper.pdf', 'not a supported source')
        manifest = mf.load(self.batch)
        manifest['inputs']['source']['path'] = str(self.batch / 'paper.pdf')
        self.put('workflow/manifest.json', manifest)
        self.report['original_sha256'] = mf.sha256_file(self.batch / 'paper.pdf')
        self.put('07_review/structure-gate.json', self.report)
        with patch('workflow.provisional.local_agent.invoke') as invoke:
            self.assertEqual(provisional.run(self.batch)['candidate_count'], 0)
        invoke.assert_not_called()

    def test_redaction_skips_only_affected_question(self):
        text = ('1. First.\n2. token=abc\nA. one\nB. two\nC. three\nD. four\n'
                '3. Third.\nA. one\nB. two\nC. three\nD. four\n4. Next.')
        self.put('01_source/正文.txt', text)
        self.put('original.txt', text)
        self.put('01_source/structure.confirmed.json', {'units': [
            {'unit_kind': 'standalone', 'numbers': [1], 'stem': '1. First.'},
            *[{'unit_kind': 'standalone', 'type': '单项填空', 'numbers': [n],
               'stem': f'{n}. {stem}\nA. one\nB. two\nC. three\nD. four'}
              for n, stem in ((2, 'token=abc'), (3, 'Third.'))]]})
        self.report['original_sha256'] = mf.sha256_file(self.batch / 'original.txt')
        self.report['input_hashes'] = {p: mf.sha256_file(self.batch / p) for p in self.report['input_hashes']}
        self.put('07_review/structure-gate.json', self.report)
        response = {'status': 'completed', 'findings': [], 'notes': [], 'artifacts': {
            '03_types/types.json': {'items': [{'unit_id': 'q-3', 'type': '单项填空',
                'subtypes': ['四个选项'], 'evidence': ['正文']}]}}}
        with patch('workflow.provisional.local_agent.invoke', return_value={'response': response}) as invoke:
            result = provisional.run(self.batch)
        rows = json.loads((self.batch / result['summary']).read_text())['units']
        self.assertEqual([row['status'] for row in rows], ['needs_review', 'candidate'])
        self.assertNotIn('token=abc', invoke.call_args.args[0])
        self.assertEqual(invoke.call_count, 1)

    def test_truncated_txt_appendix_rejected(self):
        body = (self.batch / '01_source/正文.txt').read_text()
        self.put('01_source/附录.txt', '参考答案\n2. B\n')
        self.put('original.txt', body + '参考答案\n2. B\n2. A【解析】矛盾\n')
        self.assertEqual(provisional._answer_sources(self.batch, mf.load(self.batch)), [])

    def test_txt_weak_number_in_other_section_blocks_answer_not_type(self):
        for blank in ('_2', '__2', '__2__'):
            with self.subTest(blank=blank):
                body = ('Ⅰ.单项填空\n1. First.\n2. Second question.\nA. one\nB. two\nC. three\nD. four\n'
                        f'3. Next question.\nⅡ.完形填空\nA blank {blank} occurs here.\n')
                self.put('01_source/正文.txt', body)
                self.put('01_source/附录.txt', '参考答案\n2. B\n')
                self.put('original.txt', body + '参考答案\n2. B\n')
                self.report['original_sha256'] = mf.sha256_file(self.batch / 'original.txt')
                self.report['input_hashes']['01_source/正文.txt'] = mf.sha256_file(self.batch / '01_source/正文.txt')
                self.put('07_review/structure-gate.json', self.report)
                response = {'status': 'completed', 'findings': [], 'notes': [], 'artifacts': {
                    '03_types/types.json': {'items': [{'unit_id': 'q-2', 'type': '单项填空',
                        'subtypes': ['四个选项'], 'evidence': ['正文']}]}}}
                with patch('workflow.provisional.local_agent.invoke', return_value={'response': response}) as invoke:
                    result = provisional.run(self.batch)
                row = json.loads((self.batch / result['summary']).read_text())['units'][0]
                self.assertEqual(invoke.call_count, 1)
                self.assertEqual(row['types']['status'], 'candidate')
                self.assertEqual(row['answers']['status'], 'needs_review')
                self.assertEqual(row['knowledge']['status'], 'needs_review')

    def test_deleted_required_input_invalidates_saved_candidates(self):
        response = {'status': 'completed', 'findings': [], 'notes': [], 'artifacts': {
            '03_types/types.json': {'items': [{'unit_id': 'q-2', 'type': '单项填空',
                'subtypes': ['四个选项'], 'evidence': ['正文']}]}}}
        def remove_source(*args):
            (self.batch / '01_source/正文.txt').unlink()
            return {'response': response}
        with patch('workflow.provisional.local_agent.invoke', side_effect=remove_source):
            result = provisional.run(self.batch)
        summary = json.loads((self.batch / result['summary']).read_text())
        self.assertIn('输入已变化', summary['invalidated'])
        self.assertNotIn('artifact', summary['units'][0])
        self.assertEqual(result['candidate_count'], 0)

    def test_unknown_scope_does_not_invoke(self):
        self.report['blockers'] = [{'code': 'ISSUE'}]
        self.put('07_review/structure-gate.json', self.report)
        with patch('workflow.provisional.local_agent.invoke') as invoke:
            self.assertEqual(provisional.run(self.batch)['attempted'], 0)
            invoke.assert_not_called()

    def test_empty_source_slice_does_not_invoke(self):
        with patch('workflow.provisional.preview.source_slice', return_value=[]), \
                patch('workflow.provisional.local_agent.invoke') as invoke:
            outcome = provisional.run(self.batch)
        self.assertEqual(outcome['candidate_count'], 0)
        invoke.assert_not_called()
        summary = json.loads((self.batch / outcome['summary']).read_text())
        self.assertEqual(summary['units'][0]['reason'], '原文片段不可用')

    def test_incomplete_source_never_invokes(self):
        with patch('workflow.provisional.local_agent.invoke') as invoke:
            for lines, unit in [
                (['2.', 'A. one', 'B. two', 'C. three', 'D. four', '3. Next'], {'unit_kind': 'standalone', 'type': '单项填空', 'numbers': [2], 'stem': '2.\nA. one\nB. two\nC. three\nD. four'}),
                (['2. Question', 'A. one', 'B. two', 'C. three', '3. Next'], {'unit_kind': 'standalone', 'type': '单项填空', 'numbers': [2], 'stem': '2. Question\nA. one\nB. two\nC. three'}),
                (['2. Question', 'A. one', 'B. two', 'C. three', 'D. four'], {'unit_kind': 'standalone', 'type': '单项填空', 'numbers': [2], 'stem': '2. Question\nA. one\nB. two\nC. three\nD. four'}),
                (['2. Question', 'A. one', 'B. two', 'C. three', 'D. four', '3. Next'], {'unit_kind': 'group', 'numbers': [2, 3], 'stem': '2. Question'}),
            ]:
                self.put('01_source/正文.txt', '\n'.join(lines))
                self.assertFalse(provisional._complete_source(self.batch, unit, lines[:-1]))
            invoke.assert_not_called()

    def test_bound_answer_and_knowledge_candidates_are_isolated(self):
        # 先准备可绑定的答案附录，再验证读取后被改写会使快照失效。
        self.put('01_source/附录.txt', '参考答案\n2. B\n')
        self.put('original.txt', (self.batch / '01_source/正文.txt').read_text() + '\n参考答案\n2. B\n')
        self.report['original_sha256'] = mf.sha256_file(self.batch / 'original.txt')
        self.put('07_review/structure-gate.json', self.report)
        before = (self.batch / 'workflow/manifest.json').read_bytes()
        snapshot = provisional._answer_sources
        def race(batch, manifest):
            result = snapshot(batch, manifest)
            self.put('01_source/附录.txt', '参考答案\n2. C\n')
            return result
        with patch('workflow.provisional._answer_sources', side_effect=race), \
                self.assertRaisesRegex(ValueError, '快照失效'):
            provisional.run(self.batch)
        self.put('01_source/附录.txt', '参考答案\n2. B\n')
        typed = {'status': 'completed', 'findings': [], 'notes': [], 'artifacts': {
            '03_types/types.json': {'items': [{'unit_id': 'q-2', 'type': '单项填空', 'subtypes': ['四个选项'],
                                               'words': 5, 'contentComplete': True, 'evidence': ['正文第2题']}]}}}
        # A proven answer-only appendix permits placeholders, not invented explanations.
        body = '无'
        explanation = {'status': 'completed', 'findings': [], 'notes': [], 'artifacts': {
            '04_knowledge/explanations.json': {'items': [{'unit_id': 'q-2', 'explanation': body,
                'articleSummary': '', 'subquestions': [], 'evidence': ['正文第2题与附录第2行']}]}}}
        knowledge = {'status': 'completed', 'findings': [], 'notes': [], 'artifacts': {
            '04_knowledge/knowledge.json': {'items': [{'unit_id': 'q-2',
                'knowledge': ['词汇/词汇辨析/动词辨析'], 'answer': 'B',
                'explanation': body,
                'articleSummary': '', 'subquestions': [], 'evidence': ['正文第2题']}]}}}
        with patch('workflow.provisional.local_agent.invoke',
                   side_effect=[{'response': typed}, {'response': explanation}, {'response': knowledge}]) as invoke:
            outcome = provisional.run(self.batch)
        self.assertEqual(invoke.call_count, 3)
        self.assertIn('subagent 2', invoke.call_args.args[0])
        row = json.loads((self.batch / outcome['summary']).read_text())['units'][0]
        self.assertEqual(row['answers']['artifact']['evidence']['2'], ['01_source/附录.txt L2'])
        self.assertEqual(row['explanations']['status'], 'candidate')
        self.assertEqual(row['knowledge']['status'], 'candidate')
        self.assertTrue(provisional.validate_saved_summary(self.batch, outcome['summary'])['source_fresh'])
        for altered in ('one', provisional.agent_tasks.AI_MARK,
                        '第2题答案为B，对应选项two，结合题干判断其用法。' + provisional.agent_tasks.AI_MARK):
            candidate = json.loads(json.dumps(explanation, ensure_ascii=False))
            candidate['artifacts']['04_knowledge/explanations.json']['items'][0]['explanation'] = altered
            with patch('workflow.provisional.local_agent.invoke', side_effect=[{'response': typed}, {'response': candidate}]):
                rejected = provisional.run(self.batch)
            rejected_row = json.loads((self.batch / rejected['summary']).read_text())['units'][0]
            self.assertEqual(rejected_row['explanations']['status'], 'needs_review')
        vague = json.loads(json.dumps(explanation, ensure_ascii=False))
        vague['artifacts']['04_knowledge/explanations.json']['items'][0]['explanation'] = '依据题意选择。' + provisional.agent_tasks.AI_MARK
        with patch('workflow.provisional.local_agent.invoke', side_effect=[{'response': typed}, {'response': vague}]):
            rejected = provisional.run(self.batch)
        self.assertEqual(json.loads((self.batch / rejected['summary']).read_text())['units'][0]['explanations']['status'], 'needs_review')
        forged = json.loads(json.dumps(knowledge, ensure_ascii=False))
        forged['artifacts']['04_knowledge/knowledge.json']['items'][0]['explanation'] = 'one'
        with patch('workflow.provisional.local_agent.invoke',
                   side_effect=[{'response': typed}, {'response': explanation}, {'response': forged}]) as invoke:
            rejected = provisional.run(self.batch)
        self.assertEqual(invoke.call_count, 3)
        corrected_row = json.loads((self.batch / rejected['summary']).read_text())['units'][0]
        self.assertEqual(corrected_row['knowledge']['status'], 'candidate')
        # Knowledge workers own labels only; content merges at assembly, so the
        # knowledge candidate must contain no answer/explanation echo at all.
        corrected = corrected_row['knowledge']['artifact']['items'][0]
        self.assertNotIn('answer', corrected)
        self.assertNotIn('explanation', corrected)
        self.assertNotIn('one', json.dumps(corrected_row, ensure_ascii=False))
        self.assertFalse((self.batch / '04_knowledge/knowledge.json').exists())
        self.put('01_source_ans/正文.txt', '2. B 【解析】存在未验证来源的解析')
        with patch('workflow.provisional.local_agent.invoke', return_value={'response': typed}) as invoke:
            blocked = provisional.run(self.batch)
        blocked_row = json.loads((self.batch / blocked['summary']).read_text())['units'][0]
        self.assertEqual(invoke.call_count, 1)
        self.assertEqual(blocked_row['types']['status'], 'candidate')
        self.assertEqual(blocked_row['answers']['status'], 'candidate')
        self.assertEqual(blocked_row['knowledge']['status'], 'needs_review')
        (self.batch / '01_source_ans/正文.txt').unlink()
        real_hash = mf.sha256_file
        def changed_rule(path):
            if Path(path).name == 'content_rules.py':
                return 'changed-rule'
            return real_hash(path)
        with patch('workflow.provisional.mf.sha256_file', side_effect=changed_rule):
            self.assertFalse(provisional.validate_saved_summary(self.batch, outcome['summary'])['source_fresh'])
        def change_during_labeling(prompt, workdir):
            if str(workdir).endswith('-knowledge'):
                self.put('01_source/metadata.json', '{}')
                return {'response': knowledge}
            if str(workdir).endswith('-explanations'):
                return {'response': explanation}
            return {'response': typed}
        with patch('workflow.provisional.local_agent.invoke', side_effect=change_during_labeling):
            stale = provisional.run(self.batch)
        stale_row = json.loads((self.batch / stale['summary']).read_text())['units'][0]
        self.assertEqual(stale_row['knowledge']['status'], 'needs_review')
        self.assertIn('输入已变化', stale_row['reason'])
        self.assertFalse(provisional.validate_saved_summary(self.batch, outcome['summary'])['source_fresh'])
        self.put('01_source/附录.txt', '参考答案\n2. C\n')
        self.assertEqual((self.batch / 'workflow/manifest.json').read_bytes(), before)

    def test_knowledge_failure_does_not_stop_next_unit(self):
        body = ('1. First question.\n2. Second question.\nA. one\nB. two\nC. three\nD. four\n'
                '3. Third question.\nA. one\nB. two\nC. three\nD. four\n4. Boundary question.\n')
        self.put('01_source/正文.txt', body)
        self.put('01_source/structure.confirmed.json', {'units': [
            {'unit_kind': 'standalone', 'type': '单项填空', 'numbers': [1], 'stem': '1. First question.'},
            {'unit_kind': 'standalone', 'type': '单项填空', 'numbers': [2],
             'stem': '2. Second question.\nA. one\nB. two\nC. three\nD. four'},
            {'unit_kind': 'standalone', 'type': '单项填空', 'numbers': [3],
             'stem': '3. Third question.\nA. one\nB. two\nC. three\nD. four'}]})
        self.report['input_hashes']['01_source/正文.txt'] = mf.sha256_file(self.batch / '01_source/正文.txt')
        self.report['input_hashes']['01_source/structure.confirmed.json'] = mf.sha256_file(
            self.batch / '01_source/structure.confirmed.json')
        self.put('01_source/附录.txt', '参考答案\n2. A\n3. B\n')
        self.put('original.txt', body + '\n参考答案\n2. A\n3. B\n')
        self.report['original_sha256'] = mf.sha256_file(self.batch / 'original.txt')
        self.put('07_review/structure-gate.json', self.report)
        def typed(n):
            return {'response': {'status': 'completed', 'findings': [], 'notes': [], 'artifacts': {
                '03_types/types.json': {'items': [{'unit_id': f'q-{n}', 'type': '单项填空',
                    'subtypes': ['四个选项'], 'words': 6, 'contentComplete': True, 'evidence': ['正文']}]}}}}
        text = '无'
        def explanation(n):
            return {'response': {'status': 'completed', 'findings': [], 'notes': [], 'artifacts': {
                '04_knowledge/explanations.json': {'items': [{'unit_id': f'q-{n}', 'explanation': text,
                    'articleSummary': '', 'subquestions': [], 'evidence': ['正文与附录']}]}}}}
        def knowledge(n, tags, answer):
            return {'response': {'status': 'completed', 'findings': [], 'notes': [], 'artifacts': {
                '04_knowledge/knowledge.json': {'items': [{'unit_id': f'q-{n}', 'knowledge': tags, 'answer': answer,
                    'explanation': text, 'articleSummary': '',
                    'subquestions': [], 'evidence': ['正文']}]}}}}
        invalid = knowledge(2, ['虚构/知识'], 'A')
        valid = knowledge(3, ['词汇/词汇辨析/动词辨析'], 'B')
        with patch('workflow.provisional.local_agent.invoke',
                   side_effect=[typed(2), explanation(2), invalid, typed(3), explanation(3), valid]):
            result = provisional.run(self.batch)
        rows = [r for r in json.loads((self.batch / result['summary']).read_text())['units'] if 'types' in r]
        self.assertEqual(len(rows), 2)
        self.assertEqual([r['answers']['status'] for r in rows], ['candidate', 'candidate'])
        self.assertEqual([r['explanations']['status'] for r in rows], ['candidate', 'candidate'])
        self.assertEqual([r['knowledge']['status'] for r in rows], ['needs_review', 'candidate'])
        bad = explanation(2)
        # 缺解析现在允许归一化为「无」；用错误字段类型测试真正的失败隔离。
        bad['response']['artifacts']['04_knowledge/explanations.json']['items'][0]['explanation'] = ['invalid type']
        with patch('workflow.provisional.local_agent.invoke',
                   side_effect=[typed(2), bad, typed(3), explanation(3), valid]) as invoke:
            result = provisional.run(self.batch)
        rows = [r for r in json.loads((self.batch / result['summary']).read_text())['units'] if 'types' in r]
        self.assertEqual(invoke.call_count, 5)
        self.assertEqual([r['explanations']['status'] for r in rows], ['needs_review', 'candidate'])
        self.assertEqual([r['knowledge']['status'] for r in rows], ['needs_review', 'candidate'])

    def test_answer_ambiguity_and_input_change_reject_candidates(self):
        self.assertIsNone(provisional._answer_for(2, [('bound', '2. A\n2. B')]))
        self.assertIsNone(provisional._answer_for(2, [('bound', '2. A explanation')]))
        self.assertIsNone(provisional._answer_for(2, [('bound', '2. A\n02. B 解析')]))
        self.put('answers.txt', '2. B\n')
        manifest = mf.load(self.batch)
        manifest['inputs']['answers'] = {'path': str(self.batch / 'answers.txt')}
        self.put('workflow/manifest.json', manifest)
        typed = {'status': 'completed', 'findings': [], 'notes': [], 'artifacts': {
            '03_types/types.json': {'items': [{'unit_id': 'q-2', 'type': '单项填空',
                'subtypes': ['四个选项'], 'evidence': ['正文第2题']}]}}}
        def change(*args):
            self.put('answers.txt', '2. C\n')
            return {'response': typed}
        with patch('workflow.provisional.local_agent.invoke', side_effect=change):
            result = provisional.run(self.batch)
        summary = json.loads((self.batch / result['summary']).read_text())
        self.assertIn('输入已变化', summary['invalidated'])
        self.assertEqual(result['candidate_count'], 0)

    def test_answer_body_snapshot_must_match_bound_hash(self):
        body = (self.batch / '01_source/正文.txt').read_text()
        self.put('01_source/附录.txt', '参考答案\n2. B\n')
        self.put('original.txt', body + '\nAlternate body\n参考答案\n2. B\n')
        self.report['original_sha256'] = mf.sha256_file(self.batch / 'original.txt')
        self.put('07_review/structure-gate.json', self.report)
        snapshot = provisional._answer_sources
        def swap(batch, manifest):
            self.put('01_source/正文.txt', 'Alternate body')
            result = snapshot(batch, manifest)
            self.put('01_source/正文.txt', body)
            return result
        with patch('workflow.provisional._answer_sources', side_effect=swap), \
                patch('workflow.provisional.local_agent.invoke') as invoke, \
                self.assertRaisesRegex(ValueError, '快照失效'):
            provisional.run(self.batch)
        invoke.assert_not_called()

    def test_observed_change_stays_invalid_after_restore(self):
        self.put('answers.txt', 'original')
        manifest = mf.load(self.batch)
        manifest['inputs']['answers'] = {'path': str(self.batch / 'answers.txt')}
        self.put('workflow/manifest.json', manifest)
        typed = {'status': 'completed', 'findings': [], 'notes': [], 'artifacts': {
            '03_types/types.json': {'items': [{'unit_id': 'q-2', 'type': '单项填空',
                'subtypes': ['四个选项'], 'evidence': ['正文']} ]}}}
        original_hash = mf.sha256_file
        def hash_then_restore(path):
            result = original_hash(path)
            if Path(path) == self.batch / 'answers.txt' and result == changed_hash:
                self.put('answers.txt', 'original')
            return result
        import hashlib
        changed_hash = hashlib.sha256(b'changed').hexdigest()
        def change(*args):
            self.put('answers.txt', 'changed')
            return {'response': typed}
        with patch('workflow.provisional.mf.sha256_file', side_effect=hash_then_restore), \
                patch('workflow.provisional.local_agent.invoke', side_effect=change) as invoke:
            result = provisional.run(self.batch)
        summary = json.loads((self.batch / result['summary']).read_text())
        self.assertEqual(invoke.call_count, 1)
        self.assertIn('输入已变化', summary['invalidated'])
        self.assertEqual(result['candidate_counts'], {'types': 0, 'answers': 0, 'explanations': 0, 'knowledge': 0})
        self.assertFalse(provisional.validate_saved_summary(self.batch, result['summary'])['source_fresh'])

    def docx_fixture(self, tail='Ⅰ.单项填空 参考答案\n参考答案\n2. B', warning=False):
        body = 'Ⅰ.单项填空\n' + (self.batch / '01_source/正文.txt').read_text()
        paragraphs = body.splitlines() + tail.splitlines()
        xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        xml += ''.join('<w:p><w:r><w:t>' + escape(line) + '</w:t></w:r></w:p>' for line in paragraphs)
        if warning:
            xml += '<w:altChunk/>'
        xml += '</w:body></w:document>'
        path = self.batch / 'original.docx'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('word/document.xml', xml)
        self.put('01_source/正文.txt', body)
        self.put('01_source/附录.txt', tail)
        manifest = mf.load(self.batch)
        manifest['inputs']['source']['path'] = str(path)
        self.put('workflow/manifest.json', manifest)
        self.report['original_sha256'] = mf.sha256_file(path)
        self.report['input_hashes']['01_source/正文.txt'] = mf.sha256_file(self.batch / '01_source/正文.txt')
        self.put('07_review/structure-gate.json', self.report)
        return path

    def test_docx_independent_valid_source_and_freshness(self):
        path = self.docx_fixture()
        sources = provisional._answer_sources(self.batch, mf.load(self.batch))
        self.assertEqual(provisional._answer_for(2, [(a, b) for a, b, *_ in sources])['answers'], {'2': 'B'})
        typed = {'status': 'completed', 'findings': [], 'notes': [], 'artifacts': {
            '03_types/types.json': {'items': [{'unit_id': 'q-2', 'type': '单项填空',
                'subtypes': ['四个选项'], 'evidence': ['正文']}]}}}
        knowledge = {'status': 'completed', 'findings': [], 'notes': [], 'artifacts': {
            '04_knowledge/knowledge.json': {'items': [{'unit_id': 'q-2', 'knowledge': ['词汇/词汇辨析/动词辨析'],
                'answer': 'B', 'explanation': '无',
                'subquestions': [], 'evidence': ['正文']}]}}}
        explanation = {'status': 'completed', 'findings': [], 'notes': [], 'artifacts': {
            '04_knowledge/explanations.json': {'items': [{'unit_id': 'q-2',
                'explanation': '无',
                'articleSummary': '', 'subquestions': [], 'evidence': ['正文']}]}}}
        with patch('workflow.provisional.local_agent.invoke',
                   side_effect=[{'response': typed}, {'response': explanation}, {'response': knowledge}]):
            outcome = provisional.run(self.batch)
        self.assertEqual(outcome['candidate_counts'], {'types': 1, 'answers': 1, 'explanations': 1, 'knowledge': 1})
        self.assertTrue(provisional.validate_saved_summary(self.batch, outcome['summary'])['source_fresh'])
        path.write_bytes(path.read_bytes() + b'changed')
        self.assertFalse(provisional.validate_saved_summary(self.batch, outcome['summary'])['source_fresh'])

    def test_docx_rejects_mismatch_warning_ambiguity_and_fake_candidates(self):
        for tail, warning in [('Ⅰ.单项填空 参考答案\n参考答案\n2. B\n02. C', False),
                              ('Ⅰ.单项填空 参考答案\n参考答案\n2. B explanation', False),
                              ('Ⅰ.单项填空 参考答案\n参考答案\n2. B\n9. A', False),
                              ('Ⅰ.单项填空 参考答案\n参考答案\n2. B', True)]:
            with self.subTest(tail=tail, warning=warning):
                # Reset the body because docx_fixture adds a section heading.
                self.put('01_source/正文.txt', '1. First question.\n2. Second question.\nA. one\nB. two\nC. three\nD. four\n3. Next question.\n')
                self.docx_fixture(tail, warning)
                self.assertEqual(provisional._answer_sources(self.batch, mf.load(self.batch)), [])
        self.put('01_source/正文.txt', '1. First question.\n2. Second question.\nA. one\nB. two\nC. three\nD. four\n3. Next question.\n')
        self.docx_fixture()
        self.put('01_source/附录.txt', 'Ⅰ.单项填空 参考答案\n参考答案\n2. C')
        self.assertEqual(provisional._answer_sources(self.batch, mf.load(self.batch)), [])
        self.put('01_source/附录.txt', 'Ⅰ.单项填空 参考答案\n参考答案\n2. B')
        self.put('01_source/正文.txt', 'forged body')
        self.assertEqual(provisional._answer_sources(self.batch, mf.load(self.batch)), [])

    def test_docx_weak_number_in_other_section_cannot_bind_answer(self):
        self.put('01_source/正文.txt', '1. First question.\nA blank __2__ here.\n'
                 'Ⅱ.单项选择\n2. Second question.\nA. one\nB. two\nC. three\nD. four\n3. Next question.\n')
        self.docx_fixture()
        self.assertEqual(provisional._answer_sources(self.batch, mf.load(self.batch)), [])
        typed = {'status': 'completed', 'findings': [], 'notes': [], 'artifacts': {
            '03_types/types.json': {'items': [{'unit_id': 'q-2', 'type': '单项填空',
                'subtypes': ['四个选项'], 'evidence': ['正文']}]}}}
        with patch('workflow.provisional.local_agent.invoke', return_value={'response': typed}) as invoke:
            result = provisional.run(self.batch)
        self.assertEqual(result['candidate_counts']['answers'], 0)
        self.assertEqual(result['candidate_counts']['knowledge'], 0)

    def test_docx_rejects_removed_question_text(self):
        body = '1. First question.\n2. Second question.\n扫码查看作答说明：不要选择 B。\nA. one\nB. two\nC. three\nD. four\n3. Next question.\n'
        self.put('01_source/正文.txt', body)
        self.docx_fixture()
        cleaned, _, removed = provisional.denoise.clean_body((self.batch / '01_source/正文.txt').read_text())
        self.assertTrue(removed)
        self.put('01_source/正文.txt', cleaned)
        self.assertEqual(provisional._answer_sources(self.batch, mf.load(self.batch)), [])

    def test_docx_type_checks_each_original_question_independently(self):
        body = ('1. First.\n2. Second.\n扫码查看作答说明：不要选择 B。\nA. one\nB. two\nC. three\nD. four\n'
                '3. Third.\nA. five\nB. six\nC. seven\nD. eight\n4. Next.\n')
        self.put('01_source/正文.txt', body)
        self.docx_fixture(tail='')
        cleaned = provisional.denoise.clean_body((self.batch / '01_source/正文.txt').read_text())[0]
        self.put('01_source/正文.txt', cleaned)
        self.put('01_source/structure.confirmed.json', {'units': [
            {'unit_kind': 'standalone', 'numbers': [1], 'stem': '1. First.'},
            {'unit_kind': 'standalone', 'type': '单项填空', 'numbers': [2], 'stem': '2. Second.\nA. one\nB. two\nC. three\nD. four'},
            {'unit_kind': 'standalone', 'type': '单项填空', 'numbers': [3], 'stem': '3. Third.\nA. five\nB. six\nC. seven\nD. eight'}]})
        self.report['input_hashes'] = {p: mf.sha256_file(self.batch / p) for p in self.report['input_hashes']}
        self.put('07_review/structure-gate.json', self.report)
        typed = {'status': 'completed', 'findings': [], 'notes': [], 'artifacts': {
            '03_types/types.json': {'items': [{'unit_id': 'q-3', 'type': '单项填空',
                'subtypes': ['四个选项'], 'evidence': ['正文']}]}}}
        with patch('workflow.provisional.local_agent.invoke', return_value={'response': typed}) as invoke:
            result = provisional.run(self.batch)
        self.assertEqual(invoke.call_count, 1)
        rows = json.loads((self.batch / result['summary']).read_text())['units']
        self.assertEqual([row['status'] for row in rows], ['needs_review', 'candidate'])

    def test_docx_rejects_hidden_direct_and_style_markup(self):
        for style in (False, True):
            with self.subTest(style=style):
                self.put('01_source/正文.txt', '1. First question.\n2. Second question.\nA. one\nB. two\nC. three\nD. four\n3. Next question.\n')
                path = self.docx_fixture()
                with zipfile.ZipFile(path) as archive:
                    xml = archive.read('word/document.xml').decode()
                if not style:
                    xml = xml.replace('<w:r><w:t>2. B</w:t>', '<w:r><w:rPr><w:vanish/></w:rPr><w:t>2. B</w:t>')
                with zipfile.ZipFile(path, 'w') as archive:
                    archive.writestr('word/document.xml', xml)
                    if style:
                        archive.writestr('word/styles.xml', '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:style><w:rPr><w:webHidden/></w:rPr></w:style></w:styles>')
                self.assertEqual(provisional._answer_sources(self.batch, mf.load(self.batch)), [])

    def test_docx_source_snapshot_races_rejected(self):
        path = self.docx_fixture()
        snapshot = provisional._answer_sources
        def race(batch, manifest):
            result = snapshot(batch, manifest)
            path.write_bytes(path.read_bytes() + b'changed')
            return result
        with patch('workflow.provisional._answer_sources', side_effect=race), \
             patch('workflow.provisional.local_agent.invoke') as invoke:
            result = provisional.run(self.batch)
        invoke.assert_not_called()
        summary = json.loads((self.batch / result['summary']).read_text())
        self.assertIn('输入已变化', summary['invalidated'])
        self.assertEqual(result['candidate_counts'], {'types': 0, 'answers': 0, 'explanations': 0, 'knowledge': 0})

    def spelling_fixture(self, second='2. I like ____ (苹果).', third='3. She has a c____ (猫).', heading='Ⅱ.单词拼写'):
        text = f'Ⅰ.其他\n1. First.\n{heading}\n{second}\n{third}\nⅢ.下一节\n4. Next.\n'
        self.put('01_source/正文.txt', text)
        self.put('original.txt', text + '参考答案\n2. B\n3. A\n')
        self.put('01_source/附录.txt', '参考答案\n2. B\n3. A\n')
        self.put('01_source/structure.confirmed.json', {'units': [
            {'unit_kind': 'standalone', 'numbers': [1], 'stem': '1. First.'},
            *[{'unit_kind': 'standalone', 'type': '单词拼写', 'numbers': [n], 'stem': stem}
              for n, stem in ((2, second), (3, third))]]})
        self.report['original_sha256'] = mf.sha256_file(self.batch / 'original.txt')
        self.report['input_hashes'] = {p: mf.sha256_file(self.batch / p) for p in self.report['input_hashes']}
        self.put('07_review/structure-gate.json', self.report)

    def test_spelling_types_only_and_failure_is_isolated(self):
        self.spelling_fixture()
        def respond(prompt, workdir):
            if workdir.name == 'unit-1':
                raise ValueError('单题失败')
            return {'response': {'status': 'completed', 'findings': [], 'notes': [], 'artifacts': {
                '03_types/types.json': {'items': [{'unit_id': 'q-3', 'type': '单词拼写',
                    'subtypes': ['汉语及首字母'], 'evidence': ['正文第3题']}]}}}}
        with patch('workflow.provisional.local_agent.invoke', side_effect=respond) as invoke:
            result = provisional.run(self.batch)
        rows = json.loads((self.batch / result['summary']).read_text())['units']
        self.assertEqual(invoke.call_count, 2)
        self.assertEqual([r['status'] for r in rows], ['needs_review', 'candidate'])
        self.assertEqual(rows[1]['answers']['status'], 'needs_review')
        self.assertEqual(rows[1]['knowledge']['status'], 'needs_review')
        self.assertIn('不能证明句子语法或语义完整', rows[1]['review'])
        self.assertEqual(result['status'], 'needs_review')
        self.assertFalse((self.batch / '03_types/types.json').exists())
        self.assertFalse((self.batch / '04_knowledge/knowledge.json').exists())
        self.assert_formal_stages_untouched()

    def test_spelling_requires_strict_structure_and_original(self):
        for second, heading in [
            ('2. I like apples.', 'Ⅱ.单词拼写'),
            ('2. I like ____ and ____ (苹果).', 'Ⅱ.单词拼写'),
            ('2. I like ____ (苹果). Another sentence.', 'Ⅱ.单词拼写'),
            ('2. I like ____ (苹果) 4. extra', 'Ⅱ.单词拼写'),
            ('2. I like ____ (苹果). [IMG x]', 'Ⅱ.单词拼写'),
            ('2. I like ____ (苹果).', 'Ⅱ.选词填空'),
            ('2. I like ____ (苹果).', 'Ⅱ.单词拼写 方框词库'),
            ('2. I like ____ (苹果).\nword bank: cat dog', 'Ⅱ.单词拼写'),
        ]:
            with self.subTest(second=second, heading=heading):
                self.spelling_fixture(second=second, heading=heading)
                with patch('workflow.provisional.local_agent.invoke') as invoke:
                    provisional.run(self.batch)
                self.assertFalse(any('I like' in call.args[0] for call in invoke.call_args_list))
        self.spelling_fixture()
        original = (self.batch / 'original.txt').read_text().replace('苹果', '香蕉')
        self.put('original.txt', original)
        self.report['original_sha256'] = mf.sha256_file(self.batch / 'original.txt')
        self.put('07_review/structure-gate.json', self.report)
        with patch('workflow.provisional.local_agent.invoke') as invoke:
            provisional.run(self.batch)
        self.assertFalse(any('I like' in call.args[0] for call in invoke.call_args_list))

    def test_spelling_rejects_forged_catalog_stem_and_successor(self):
        self.spelling_fixture()
        invalid = {'status': 'completed', 'findings': [], 'notes': [], 'artifacts': {
            '03_types/types.json': {'items': [{'unit_id': 'q-2', 'type': '单词拼写',
                                               'subtypes': ['伪造子类'], 'evidence': ['正文']}]}}}
        with patch('workflow.provisional.local_agent.invoke', return_value={'response': invalid}):
            result = provisional.run(self.batch)
        rows = json.loads((self.batch / result['summary']).read_text())['units']
        self.assertEqual(rows[0]['types']['status'], 'needs_review')
        confirmed = json.loads((self.batch / '01_source/structure.confirmed.json').read_text())
        confirmed['units'][1]['stem'] = '2. forged ____ (苹果).'
        self.put('01_source/structure.confirmed.json', confirmed)
        self.report['input_hashes']['01_source/structure.confirmed.json'] = mf.sha256_file(self.batch / '01_source/structure.confirmed.json')
        self.put('07_review/structure-gate.json', self.report)
        with patch('workflow.provisional.local_agent.invoke', return_value={'response': invalid}) as invoke:
            provisional.run(self.batch)
        self.assertFalse(any('I like' in call.args[0] for call in invoke.call_args_list))
        self.spelling_fixture()
        original = (self.batch / 'original.txt').read_text().replace('3. She has', '5. She has')
        self.put('original.txt', original)
        self.report['original_sha256'] = mf.sha256_file(self.batch / 'original.txt')
        self.put('07_review/structure-gate.json', self.report)
        with patch('workflow.provisional.local_agent.invoke', return_value={'response': invalid}) as invoke:
            provisional.run(self.batch)
        invoke.assert_not_called()

    def test_spelling_rejects_fragment_and_numbered_shared_instruction(self):
        for second, third in [('2. I like ____ (苹果).', '3. Choose from apple banana orange')]:
            with self.subTest(second=second, third=third):
                self.spelling_fixture(second, third)
                with patch('workflow.provisional.local_agent.invoke') as invoke:
                    provisional.run(self.batch)
                invoke.assert_not_called()

    def test_spelling_fragment_does_not_block_other_question(self):
        for third in ('3. The ____ (苹果)', '3. I like the ____ and (苹果).',
                      '3. She is ____ because (高兴).', '3. I like ____ so (苹果).',
                      '3. I like ____ although (苹果).', '3. She has ____ (猫)'):
            with self.subTest(third=third):
                self.spelling_fixture(third=third)
                response = {'status': 'completed', 'findings': [], 'notes': [], 'artifacts': {
                    '03_types/types.json': {'items': [{'unit_id': 'q-2', 'type': '单词拼写',
                                                      'subtypes': ['汉语提示'], 'evidence': ['正文']}]}}}
                with patch('workflow.provisional.local_agent.invoke', return_value={'response': response}) as invoke:
                    result = provisional.run(self.batch)
                rows = json.loads((self.batch / result['summary']).read_text())['units']
                self.assertEqual(invoke.call_count, 1)
                self.assertEqual([row['status'] for row in rows], ['candidate', 'needs_review'])

    def test_spelling_rejects_duplicate_swapped_sections(self):
        self.spelling_fixture()
        body = (self.batch / '01_source/正文.txt').read_text()
        body += 'Ⅱ.单词拼写\n5. They like ____ (苹果).\nⅣ.结束\n'
        original = body.replace('2. I like ____ (苹果).\n3. She has a c____ (猫).', 'PLACEHOLDER')
        original = original.replace('5. They like ____ (苹果).', '2. I like ____ (苹果).\n3. She has a c____ (猫).').replace('PLACEHOLDER', '5. They like ____ (苹果).')
        self.put('01_source/正文.txt', body)
        self.put('original.txt', original)
        self.report['original_sha256'] = mf.sha256_file(self.batch / 'original.txt')
        self.report['input_hashes']['01_source/正文.txt'] = mf.sha256_file(self.batch / '01_source/正文.txt')
        self.put('07_review/structure-gate.json', self.report)
        with patch('workflow.provisional.local_agent.invoke') as invoke:
            provisional.run(self.batch)
        invoke.assert_not_called()

    def test_bad_answer_encoding_does_not_block_spelling(self):
        self.spelling_fixture()
        (self.batch / '01_source/附录.txt').write_bytes(b'\xff\xfe\xff')
        def response(prompt, workdir):
            number = '2' if workdir.name == 'unit-1' else '3'
            return {'response': {'status': 'completed', 'findings': [], 'notes': [], 'artifacts': {
                '03_types/types.json': {'items': [{'unit_id': 'q-' + number, 'type': '单词拼写',
                    'subtypes': ['汉语提示'], 'evidence': ['正文']}]}}}}
        with patch('workflow.provisional.local_agent.invoke', side_effect=response):
            result = provisional.run(self.batch)
        self.assertEqual(result['candidate_counts'], {'types': 2, 'answers': 0, 'explanations': 0, 'knowledge': 0})
        self.assertTrue(provisional.validate_saved_summary(self.batch, result['summary'])['source_fresh'])

    def test_stale_evidence_rejected(self):
        self.put('01_source/正文.txt', 'changed')
        with self.assertRaisesRegex(ValueError, 'stale'):
            provisional.run(self.batch)
