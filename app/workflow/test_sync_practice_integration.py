# -*- coding: utf-8 -*-
"""同步课时原生格式、来源窗口与流程接线回归；不调用真实模型。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import extract, structure_ai, sync_practice as sp, agent_tasks


class SyncSourceRegressionTest(unittest.TestCase):
    def test_native_bold_lesson_and_embedded_exam_sources(self):
        text = '**第一课时**　Section** **A** **(1a-2d)\nⅠ.单项填空\n1.[2025·期末]x\n2.[2025·月考]y\n3.[2025·期中]z'
        self.assertTrue(sp.looks_like_sync_practice(text))
        self.assertTrue(sp.looks_like_sync_practice('第一课时 Pronunciation\nⅠ.音素分类'))
        self.assertFalse(sp.looks_like_sync_practice('2026年期末测试卷\n第一课时复习\nⅠ.阅读理解'))
        self.assertFalse(sp.looks_like_sync_practice('一、阅读理解\n1.基础提升课时活动'))

    def test_same_lesson_repeated_sections_are_not_answer_appendix(self):
        text = ('**第一课时** Section** **A\nⅠ.单项填空\n(　)1.Which?\nA.a B.b C.c D.d\n'
                'Ⅱ.阅读理解\nA story.\n(　)1.Who?\nA.a B.b C.c D.d\n'
                'Ⅰ.单项填空\n(　)1.What?\nA.a B.b C.c D.d\n'
                '阅读理解\nAnother story.\n(　)1.When?\nA.a B.b C.c D.d')
        structure, body, appendix = extract.infer_structure(text)
        self.assertEqual(body.rstrip(), text.rstrip())
        self.assertEqual(appendix, '')
        self.assertEqual(len(structure['sections']), 4)
        self.assertEqual([x['sync_occurrence'] for x in structure['sections']], [1,2,3,4])
        self.assertFalse(structure['coverage_verified'])
        self.assertTrue(any(x['code'] == 'NUMBERING_RESTART' for x in structure['unresolved']))

    def test_instruction_headings_are_bounded_not_keywords_in_body(self):
        text = ('第一课时 Pronunciation\nⅠ.将下列字母按音素分类\n1.A\n'
                'Ⅱ.单项填空\n1.This reading mentions 阅读理解 and 完形填空.\n'
                '阅读理解\nStory mentions 单项填空.\n(　)1.Who?\nA.a B.b C.c D.d')
        headings = sp.heading_candidates(text)
        self.assertEqual([x['kind'] for x in headings], ['group','standalone','group'])
        self.assertEqual([x['start_line'] for x in headings], [2,4,6])
        self.assertEqual(sp.audit_units([{'type':'单项填空','unit_kind':'standalone','numbers':[1],
                                          'source_span':{'start_line':5,'end_line':5}}], text), [])

    def test_mid_book_model_essay_does_not_remove_later_exercises(self):
        text = ('第一课时 Section A\nⅠ.根据首字母补全单词\n1.A\n2.B\n'
                '【参考范文】\nHello everyone.\nⅡ.单项填空\n(　)1.Which?\nA.a B.b C.c D.d')
        _, body, appendix = extract.infer_structure(text)
        self.assertEqual(body.rstrip(), text)
        self.assertEqual(appendix, '')

    def test_real_tail_answer_block_is_still_separated(self):
        text = '第一课时 Section A\nⅠ.单项填空\n(　)1.Who?\nA.a B.b C.c D.d\n(　)2.What?\nA.a B.b C.c D.d\n参考答案\n1.A\n2.B'
        _, body, appendix = extract.infer_structure(text)
        self.assertIn('(　)1.Who?', body)
        self.assertIn('参考答案', appendix)

    def test_answer_key_is_not_inside_the_last_heading_window(self):
        """答案区不算最后一个大题的来源窗口。

        同步练习册的答案紧跟在最后一道题后面；若窗口算到全文末尾，答案里的题号会
        和正文题号撞车（SOURCE_DUPLICATE_NUMBERS），答案文字还会被当成题干/选项。
        """
        text = ('第一课时　Section A (1a-2d)\n'
                'Ⅳ.阅读理解\n'
                'Teacher:Good afternoon,class!\n'
                '(　)1.When is this class?\n'
                'A.In the morning.\tB.In the afternoon.\n'
                '参考答案\n'
                'Ⅰ.1.Bb;Dd\n'
                'Ⅳ.1~3 BAC\n')
        headings = sp.heading_candidates(text)
        last = headings[-1]
        self.assertEqual((last['start_line'], last['end_line']), (2, 5))
        self.assertEqual(sp.answer_key_start(text), 6)
        structure, body, appendix = extract.infer_structure(text)
        section = structure['sections'][-1]
        self.assertEqual(section['source_end_line'], 5)
        self.assertEqual(section['questions'], [1])
        self.assertTrue(appendix.lstrip().startswith('参考答案'))
        self.assertNotIn('参考答案', body)

    def test_group_prefix_gap_is_located_not_automatically_rewritten(self):
        text = '第一课时 Section A\nⅠ.阅读理解\nRead this story.\n[TABLE 1]\nFacts\n[/TABLE]\n(　)1.Who?\nA.a B.b C.c D.d'
        unit = {'unit_kind':'group','type':'阅读理解','numbers':[1],
                'source_span':{'start_line':7,'end_line':8}}
        original = json.dumps(unit)
        found = sp.audit_units([unit], text)
        self.assertEqual(found[0]['heading_line'], 2)
        self.assertIn('共享材料', found[0]['problems'][0])
        self.assertEqual(json.dumps(unit), original)
        unit['source_span']['start_line'] = 2
        self.assertEqual(sp.audit_units([unit], text), [])

    def test_exam_has_no_sync_audit(self):
        self.assertEqual(sp.audit_units([{'unit_kind':'standalone','type':'单项填空','numbers':[1],
            'source_span':{'start_line':2,'end_line':3}}], '2026期末测试卷\nⅠ.阅读理解\n1.Who?'), [])

    def test_body_refinement_requires_evidence_and_native_markup(self):
        self.assertIsNone(sp.classify_with_body('任务型阅读', '')['subtype'])
        five = '\n'.join(f'{x}.Something' for x in 'ABCDE')
        self.assertIsNone(sp.classify_with_body('补全对话', five)['subtype'])
        seven = five + '\nF.Something\nG.Something'
        self.assertEqual(sp.classify_with_body('补全对话', seven)['subtype'], '7选5')
        body = '\n'.join(f'(　)**{n}.**A.x B.y C.z D.w' for n in range(1, 6))
        self.assertEqual(sp.classify_with_body('完形填空', body)['subtype'], '5空')

    def test_all_rule_conditions_are_in_prompt(self):
        block = sp.prompt_block()
        self.assertIn('四线三格', block)
        self.assertIn('写一份', block)
        self.assertIn('同时含 填空', block)
        self.assertIn('排除 ☆拓展设问', block)

    def test_structure_cache_binds_sync_policy_and_returns_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            source = batch/'01_source'
            source.mkdir()
            (source/'正文.txt').write_text('第一课时 Section A\nⅠ.单项填空\n1.Who?\nA.Yes.', encoding='utf-8')
            (source/'structure.json').write_text('{"sections":[]}', encoding='utf-8')
            calls = []
            def invoke(prompt, *_):
                calls.append(prompt)
                return {'response':{'status':'completed','artifacts':{'structure':{'units':[
                    {'unit_kind':'standalone','type':'单项填空','numbers':[1], 'stem':'1.Who?',
                     'source_span':{'start_line':3,'end_line':4}}
                ]}}}}
            with patch.object(sp, 'policy_fingerprint', return_value='policy-1'):
                structure_ai.auto_classify(batch, invoke_fn=invoke, force=True)
                reused = structure_ai.auto_classify(batch, invoke_fn=invoke, force=True)
                self.assertTrue(reused['reused'])
                self.assertIn('sync_audit', reused)
            with patch.object(sp, 'policy_fingerprint', return_value='policy-2'):
                structure_ai.auto_classify(batch, invoke_fn=invoke, force=True)
            self.assertEqual(len(calls), 2)
            self.assertIn('大题出现位置候选', calls[0])

    def test_type_task_receives_rules_and_binds_rule_files(self):
        context = {'01_source/正文.txt':'第一课时 Section A\nⅠ.单项填空\n1.Who?'}
        with patch.object(agent_tasks, '_collect', return_value=(context, {}, [], [])):
            task = agent_tasks.build_task('.', 'types', max_input_chars=250000)
        self.assertIn('sync_practice_rules', task['prompt'])
        self.assertIn('sync_practice_heading_candidates', task['prompt'])
        self.assertIn(str(sp.RULES_PATH.resolve()), task['input_hashes'])


if __name__ == '__main__':
    unittest.main()
