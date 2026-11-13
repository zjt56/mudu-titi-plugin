"""Review evidence must belong to its confirmed target, including shared material."""
import json
import tempfile
import unittest
from pathlib import Path

from workflow import review


class ReviewEvidenceScopeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        self.source = '2017 examination\nShared passage about a library.\n1. Read the story.\n2. Name the book.\n3. Different question here.\n'
        self.save('01_source/正文.txt', self.source)
        self.save('01_source/structure.confirmed.json', {'units': [
            {'unit_id': 'group', 'unit_kind': 'group', 'numbers': [1, 2],
             'stem': 'Shared passage about a library.\n1. Read the story.\n2. Name the book.'},
            {'unit_id': 'other', 'unit_kind': 'standalone', 'numbers': [3],
             'stem': '3. Different question here.'}]})
        self.save('03_types/types.json', {'items': [
            {'unit_id': uid, 'type': '阅读理解', 'subtypes': ['选择5题']} for uid in ('group', 'other')]})
        self.plan = {'input_binding': 'binding', 'input_hashes': {'01_source/正文.txt': 'bound'},
                     'units': ['group', 'other'],
                     'questions': [{'unit_id': 'group', 'number': n} for n in ('1', '2')] + [{'unit_id': 'other', 'number': '3'}],
                     'sample': [{'unit_id': 'group', 'number': '2'}]}

    def save(self, path, value):
        target = self.batch / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value if isinstance(value, str) else json.dumps(value), encoding='utf-8')

    def check(self, uid, quote, number=None):
        numbers = [number] if number is not None else (['1', '2'] if uid == 'group' else ['3'])
        assessment = {'type': '阅读理解', 'subtypes': ['选择5题'],
                      'knowledge': ['语篇体裁/记叙文/故事', '主题/人与自我/生活与学习/日常起居'],
                      'subquestions': [{'number': n, 'knowledge': []} for n in numbers] if uid == 'group' else [],
                      'difficulty': [{'number': n, 'value': '容易'} for n in numbers]}
        item = {'unit_id': uid, 'verdict': 'ok', 'rationale': '根据题干核对选项',
                'evidence': [{'path': '01_source/正文.txt', 'locator': '原文', 'quote': quote}],
                'assessment': assessment}
        if number is not None:
            item['number'] = number
            assessment['source_comparison'] = '已对照共享文章和题干'
        return item

    def record(self, rid, checks):
        return {'round_id': rid, 'input_binding': 'binding', 'execution_id': 'exec', 'checks': checks}

    def test_supplied_ocr_and_authority_context_are_bound(self):
        import hashlib
        self.save('04_knowledge/knowledge.json', {'items': []})
        self.save('06_batch/batch.json', {'items': []})
        self.save('01_source/pdf-ocr.json', {'text': 'Shared passage about a library.'})
        self.save('01_source/material-roles.json', {'documents': []})
        plan = review.prepare_round_plan(self.batch)
        for relative in ('01_source/pdf-ocr.json', '01_source/material-roles.json'):
            self.assertEqual(plan['input_hashes'][relative],
                             hashlib.sha256((self.batch / relative).read_bytes()).hexdigest())
        self.plan['input_hashes'].update(plan['input_hashes'])
        check = self.check('group', 'Shared passage about a library.', '2')
        check['evidence'].append({'path': '01_source/pdf-ocr.json', 'locator': 'OCR',
                                  'quote': 'Shared passage about a library.'})
        self.assertEqual(review._round_checks(self.record('sample', [check]), 'sample', self.plan, self.batch), [])
        check['evidence'][-1]['quote'] = 'Invented quotation not in the OCR report.'
        with self.assertRaises(ValueError):
            review._round_checks(self.record('sample', [check]), 'sample', self.plan, self.batch)
        self.save('01_source/pdf-ocr.json', {'text': 'Changed auxiliary text.'})
        self.assertNotEqual(review.prepare_round_plan(self.batch)['input_binding'], plan['input_binding'])
        from workflow.local_agent import AgentError, _check_input_hashes
        with self.assertRaisesRegex(AgentError, 'Bundled input changed during agent task'):
            _check_input_hashes(self.batch, {'01_source/pdf-ocr.json': plan['input_hashes']['01_source/pdf-ocr.json']})
        (self.batch / '01_source/pdf-ocr.json').unlink()
        absent = review.prepare_round_plan(self.batch)
        self.assertNotIn('01_source/pdf-ocr.json', absent['input_hashes'])
        self.plan['input_hashes'] = absent['input_hashes']
        with self.assertRaisesRegex(ValueError, 'Evidence must cite a bound batch input'):
            review._round_checks(self.record('sample', [check]), 'sample', self.plan, self.batch)

    def test_year_or_another_unit_cannot_stand_in_for_target(self):
        for quote in ('2017', 'Different question here.'):
            with self.subTest(quote=quote), self.assertRaises(ValueError):
                review._round_checks(self.record('labels', [self.check('group', quote),
                    self.check('other', 'Different question here.')]), 'labels', self.plan, self.batch)

    def test_shared_passage_allowed_for_sample_child(self):
        check = self.check('group', 'Shared passage about a library.', '2')
        self.assertEqual(review._round_checks(self.record('sample', [check]), 'sample', self.plan, self.batch), [])

    def test_short_real_quote_is_accepted(self):
        self.save('01_source/正文.txt', '1. No.\n2. Yes.\n')
        self.save('01_source/structure.confirmed.json', {'units': [
            {'unit_id': 'group', 'unit_kind': 'group', 'numbers': [1, 2], 'stem': '1. No.\n2. Yes.'}]})
        self.assertEqual(review._round_checks(self.record('sample', [self.check('group', 'No.', '2')]),
                                              'sample', self.plan, self.batch), [])

    def test_group_rubric_and_window_count_as_verifiable_source(self):
        """组题的 stem 常常只有答案空位，大题作答指令在 source_section.title。

        真实原卷里题干属于该单元的可核验原文，只比对 stem 会把它误判成伪造引证。
        """
        self.save('01_source/正文.txt',
                  'I. Write the five vowels in order\n1. _____ _____\n2. _____ _____\n3. Different question here.\n')
        self.save('01_source/structure.confirmed.json', {'units': [
            {'unit_id': 'group', 'unit_kind': 'group', 'numbers': [1, 2],
             'stem': '1. _____ _____\n\n2. _____ _____',
             'source_span': {'start_line': 2, 'end_line': 3, 'basis': 'cleaned_source'},
             'source_section': {'title': 'I. Write the five vowels in order',
                                'start_line': 1, 'end_line': 3}},
            {'unit_id': 'other', 'unit_kind': 'standalone', 'numbers': [3],
             'stem': '3. Different question here.'}]})
        rubric = 'I. Write the five vowels in order'
        checks = [self.check('group', rubric), self.check('other', '3. Different question here.')]
        self.assertEqual(
            review._round_checks(self.record('labels', checks), 'labels', self.plan, self.batch), [])
        fabricated = [self.check('group', 'I. Write the seven vowels in order'),
                      self.check('other', '3. Different question here.')]
        with self.assertRaisesRegex(ValueError, 'Evidence quote absent from bound input'):
            review._round_checks(self.record('labels', fabricated), 'labels', self.plan, self.batch)

    def test_missing_confirmed_target_text_stays_unchecked(self):
        self.save('01_source/structure.confirmed.json', {'units': [
            {'unit_id': 'group', 'unit_kind': 'group', 'numbers': [1, 2]},
            {'unit_id': 'other', 'unit_kind': 'standalone', 'numbers': [3], 'stem': '3. Different question here.'}]})
        with self.assertRaisesRegex(ValueError, 'no confirmed source text'):
            review._round_checks(self.record('sample', [self.check('group', 'Shared passage about a library.', '2')]),
                                 'sample', self.plan, self.batch)


    def test_sampled_group_child_list_is_narrowed_to_the_sampled_number(self):
        """二审按小题抽中组题时，整组空的子题列表是抽样口径投影，不是标签差异。

        模型把同组 1、2 两小题都列进 assessment.subquestions（knowledge 全空），
        校验只接受被抽中的第 2 小题；这里应确定性地收窄，而不是让同一响应反复
        重跑模型（「确定性失败不得用重试消化」）。
        """
        check = self.check('group', 'Shared passage about a library.', '2')
        check['assessment']['subquestions'] = [
            {'number': '1', 'knowledge': []}, {'number': '2', 'knowledge': []}]
        self.assertEqual(
            review._round_checks(self.record('sample', [check]), 'sample', self.plan, self.batch), [])
        self.assertEqual([child['number'] for child in check['assessment']['subquestions']], ['2'])

    def test_sampled_group_child_with_real_knowledge_is_still_rejected(self):
        """子题带真实非空知识点时属于独立声明，收窄会丢弃它，必须照旧校验失败。"""
        check = self.check('group', 'Shared passage about a library.', '2')
        check['assessment']['subquestions'] = [
            {'number': '1', 'knowledge': ['主题/人与自我/生活与学习/日常起居']},
            {'number': '2', 'knowledge': []}]
        with self.assertRaisesRegex(ValueError,
                                    'Knowledge subquestions must cover the selected children exactly'):
            review._round_checks(self.record('sample', [check]), 'sample', self.plan, self.batch)

    def test_sample_round_prompt_states_subquestion_scope_for_single_child(self):
        """提示词必须与校验器同源：按小题抽中时只列该小题，避免模型反复越界。"""
        from workflow import review_rounds
        prompt = review_rounds._round_prompt(
            'sample', [{'unit_id': 'group', 'number': '2'}], '')
        self.assertIn('assessment.subquestions 只列出这个 number 本身', prompt)
        self.assertIn('二审按小题抽中时只列该小题', prompt)

    def test_stale_round_unchecked_rows_stop_blocking_without_rewriting_the_file(self):
        """分轮记录已复验的行不得继续阻断闸门，但闸门不得改写审核产物。

        闸门写 ``07_review/unchecked.json`` 会改变 review 阶段自己的交付指纹，
        监督器随后判 review 过期重跑，形成 review→gate→invalidate 死循环。
        这里按当前分轮记录做只读判定。
        """
        stale = {'code': 'REVIEW_SAMPLE_SOURCE_UNVERIFIED', 'unit_id': 'group',
                 'field': 'review_rounds.sample.source', 'required': True}
        live = {'code': 'REVIEW_SAMPLE_SOURCE_UNVERIFIED', 'unit_id': 'other',
                'field': 'review_rounds.sample.source', 'required': True}
        ai = {'code': 'AI_EXECUTION_FAILED', 'unit_id': 'group',
              'field': 'review_ai', 'required': True}
        rows = [stale, live, ai]
        rounds = {'execution_completed': True, 'unchecked': [live]}
        kept = review._prune_stale_round_unchecked(rows, rounds)
        self.assertEqual(kept, [live, ai])
        # 分轮还没跑完时不得放宽：旧行仍算未完成。
        pending = {'execution_completed': False, 'unchecked': [live]}
        self.assertEqual(review._prune_stale_round_unchecked(rows, pending), rows)
        # 非分轮条目（AI 执行失败、媒体）永远不受影响。
        done = {'execution_completed': True, 'unchecked': []}
        self.assertEqual(review._prune_stale_round_unchecked([ai], done), [ai])


if __name__ == '__main__':
    unittest.main()
