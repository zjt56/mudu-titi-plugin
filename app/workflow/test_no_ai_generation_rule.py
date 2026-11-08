# -*- coding: utf-8 -*-
"""用户规则（2026-11-05）：试卷没有解析/文章大意时不再使用 AI 生成，
直接跳过，对应字段一律填「无」；留空可归一，自撰或标记生成内容拒绝。

覆盖：提示规则、子智能体1校验、来源体检、上传来源检查、任务示例。
"""
import json
import tempfile
import unittest
from pathlib import Path

from workflow import agent_tasks as tasks, content_rules, checks, agent_protocol
from workflow.structure_gate import _build_units


ABSENT = '无'


class NoAiGenerationRuleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        self.write('01_source/正文.txt', '1. She reads a book. A. reads B. read')
        self.write('01_source/附录.txt', '1.A')
        self.write('01_source/structure.json', {'sections': [{'type': '单项填空', 'questions': [1]}], 'groups': []})
        self.write('01_source/structure.confirmed.json',
                   {'version': '1.0', 'units': _build_units([{'type': '单项填空', 'questions': [1]}], [], ['1. She reads a book.'])})
        self.write('07_review/structure-gate.json', {'passed': True})
        self.write(tasks.ARTIFACTS['types'], {'items': [{'unit_id': 'q-1', 'type': '单项填空',
                                                           'subtypes': ['四个选项'], 'words': 5,
                                                           'contentComplete': True, 'evidence': ['正文 L1']}]})
        self.write(tasks.ARTIFACTS['answers'], {'answers': {'1': 'A'},
                                                  'evidence': {'1': ['附录 L1']},
                                                  'difficulty': {'1': '容易'},
                                                  'difficultyEvidence': {'1': ['单句且选项等长']}})
        self.units = tasks.confirmed_units(self.batch)
        self.typed = {'q-1': {'type': '单项填空'}}

    def write(self, rel, value):
        path = self.batch / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def explanation(self, **changes):
        row = {'unit_id': 'q-1', 'explanation': ABSENT, 'articleSummary': ABSENT,
               'subquestions': [], 'evidence': ['正文 L1；附录 L1 仅给答案，无原解析/原大意']}
        row.update(changes)
        return {'items': [row]}

    def test_canonical_placeholder_is_wu(self):
        self.assertEqual(content_rules.EMPTY_PLACEHOLDER, ABSENT)
        self.assertTrue(content_rules.is_placeholder(ABSENT))
        self.assertFalse(content_rules.is_placeholder('自撰的解析没有来源'))

    def test_rules_forbid_generation_and_require_wu(self):
        self.assertIn('「' + ABSENT + '」', content_rules.RULES)
        self.assertIn('不生成', content_rules.RULES)
        self.assertNotIn('若用户明确要求补写', content_rules.RULES)
        self.assertNotIn('生成2–3句', content_rules.RULES)

    def test_validate_accepts_wu_for_explanation_and_summary(self):
        rows = content_rules.validate(self.explanation(), self.units, self.typed, self.batch)
        self.assertEqual(rows['q-1']['explanation'], ABSENT)
        self.assertEqual(rows['q-1']['articleSummary'], ABSENT)

    def test_upload_source_check_accepts_wu(self):
        from workflow.upload_content import inspect_content
        report = inspect_content(self.batch, self.explanation())
        self.assertTrue(report['passed'], report)

    def test_check_script_accepts_wu(self):
        spec = next(c for c in checks.CHECKS if 'check_explanations' in c.script)
        batch = self.batch / 'batch.json'
        batch.write_text(json.dumps({'items': [{'unit_id': 'q-1', 'question_type': '单项填空',
                                                'answer': 'A', 'explanation': ABSENT,
                                                'articleSummary': ABSENT}]}, ensure_ascii=False), encoding='utf-8')
        result = checks.run_check(spec, Path(__file__).resolve().parents[1], batch, {})
        self.assertTrue(result['passed'], result)

    def test_check_script_rejects_generated_content_with_or_without_marker(self):
        spec = next(c for c in checks.CHECKS if 'check_explanations' in c.script)
        for bad in ('自己写的解析没有来源也没有标注。', '本题根据上下文判断。' + tasks.AI_MARK, tasks.AI_MARK):
            with self.subTest(bad=bad):
                batch = self.batch / ('bad-' + str(abs(hash(bad))) + '.json')
                batch.write_text(json.dumps({'items': [{'unit_id': 'q-1', 'question_type': '单项填空',
                                                        'answer': 'A', 'explanation': bad}]}, ensure_ascii=False), encoding='utf-8')
                result = checks.run_check(spec, Path(__file__).resolve().parents[1], batch, {})
                self.assertFalse(result['passed'])

    def test_upload_strict_check_rejects_unsourced_and_ai_content(self):
        from workflow.upload_content import inspect_content
        for value in ('自己写的解析。', '这是生成内容' + tasks.AI_MARK):
            with self.subTest(value=value):
                payload = self.explanation(explanation=value)
                before = json.dumps(payload, ensure_ascii=False)
                self.assertFalse(inspect_content(self.batch, payload, strict=True)['passed'])
                self.assertEqual(json.dumps(payload, ensure_ascii=False), before)

    def test_missing_values_are_normalized_but_existing_original_cannot_be_erased(self):
        for value in ('', None, '   '):
            payload = self.explanation(explanation=value, articleSummary=value)
            rows = content_rules.validate(payload, self.units, self.typed, self.batch)
            self.assertEqual(rows['q-1']['explanation'], ABSENT)
            self.assertEqual(rows['q-1']['articleSummary'], ABSENT)
        self.write('01_source/附录.txt', '1.A 【解析】这里是原附录逐字给出的解析。')
        with self.assertRaisesRegex(ValueError, 'ORIGINAL_EXPLANATION_OMITTED'):
            content_rules.validate(self.explanation(), self.units, self.typed, self.batch)

    def test_validate_restores_unique_word_fragment_in_original_payload(self):
        source = 'A项:开始;B项:结束。'
        self.write('01_source/附录.txt', '1.A 【解析】' + source)
        self.write('01_source/materials/attachment_001.txt', '1.A 【解析】A项：开始；B项：结束。')
        payload = self.explanation(explanation='A项：开始；B项：结束。')
        rows = content_rules.validate(payload, self.units, self.typed, self.batch)
        self.assertEqual(rows['q-1']['explanation'], source)
        self.assertEqual(payload['items'][0]['explanation'], source)
        self.assertTrue(any('原文恢复：01_source/附录.txt:1' in e for e in payload['items'][0]['evidence']))

    def test_validate_restores_group_child_and_summary_from_authority(self):
        self.write('01_source/正文.txt', '1. First question\n2. Second question\n')
        self.write('01_source/附录.txt', '【文章大意】主题:旅行。\n1.A 【解析】原因:天气。\n2.B 【解析】时间:今天。\n')
        self.write('01_source/materials/attachment_001.txt', '【文章大意】主题：旅行。\n1.A 【解析】原因：天气。\n2.B 【解析】时间：今天。\n')
        units = [{'unit_id': 'group-1-2', 'unit_kind': 'group', 'numbers': [1, 2]}]
        payload = {'items': [{'unit_id': 'group-1-2', 'explanation': '无', 'articleSummary': '主题：旅行。',
                             'subquestions': [{'number': 1, 'explanation': '原因：天气。'},
                                              {'number': 2, 'explanation': '时间：今天。'}], 'evidence': ['附录'] }]}
        content_rules.validate(payload, units, {'group-1-2': {'type': '阅读理解'}}, self.batch)
        row = payload['items'][0]
        self.assertEqual(row['articleSummary'], '主题:旅行。')
        self.assertEqual([c['explanation'] for c in row['subquestions']], ['原因:天气。', '时间:今天。'])
        self.assertEqual(sum('原文恢复：' in e for e in row['evidence']), 3)

    def test_explanation_import_installs_restored_source_not_pdf_candidate(self):
        self.write('01_source/附录.txt', '1.A 【解析】原因:天气;时间:今天。')
        self.write('01_source/materials/attachment_001.txt', '1.A 【解析】原因：天气；时间：今天。')
        payload = self.explanation(explanation='原因：天气；时间：今天。')
        result = tasks.accept_result(self.batch, 'explanations', {
            'status': 'completed', 'artifacts': {tasks.ARTIFACTS['explanations']: payload},
            'notes': [], 'findings': []})
        self.assertTrue(result['completed'], result)
        installed = json.loads((self.batch / tasks.ARTIFACTS['explanations']).read_text())
        self.assertEqual(installed['items'][0]['explanation'], '原因:天气;时间:今天。')
        self.assertTrue(any('原文恢复：' in e for e in installed['items'][0]['evidence']))

    def test_validate_does_not_normalize_substantive_rewrite(self):
        self.write('01_source/附录.txt', '1.A 【解析】原因:天气。')
        payload = self.explanation(explanation='原因：时间。')
        with self.assertRaisesRegex(ValueError, 'EXPLANATION_SOURCE_UNVERIFIED'):
            content_rules.validate(payload, self.units, self.typed, self.batch)

    def test_task_examples_use_wu_not_ai_generation(self):
        # 导出任务会争用运行中的 agent 锁，这里直接核对示例载荷源码。
        src = Path(__file__).with_name('agent_tasks.py').read_text(encoding='utf-8')
        block = src[src.index('example_payloads = {'):src.index('example = {')]
        for stage in ('explanations', 'knowledge', 'assemble'):
            line = next(l for l in block.splitlines() if l.strip().startswith('"' + stage + '"'))
            self.assertNotIn('AI_MARK', line, stage)
        self.assertIn('"explanation": "无"', block)
        self.assertIn('"articleSummary": "无"', block)


if __name__ == '__main__':
    unittest.main()
