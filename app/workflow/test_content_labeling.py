"""子智能体1（解析/大意）独立成阶段，子智能体2（知识点）只搬运不生成。"""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from workflow import agent_tasks as tasks, content_rules, contracts
from workflow.structure_gate import _build_units

SUMMARY = '本文讲述校园生活中的一段经历，并说明了同学之间的互助。'


class ContentLabelingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        self.write('01_source/正文.txt', '1. She reads a book. A. reads B. read\n2. They help. A. helped B. help')
        self.write('01_source/附录.txt', '1.A\n2.B')
        self.units = tasks.confirmed_units(self.batch) if False else None
        self.write('01_source/structure.json', {'sections': [{'type': '单项填空', 'questions': [1]}], 'groups': []})
        self.write('01_source/structure.confirmed.json', {'version': '1.0', 'units': _build_units([{'type': '单项填空', 'questions': [1]}], [], ['1. She reads a book.'])})
        self.units = tasks.confirmed_units(self.batch)
        self.write('07_review/structure-gate.json', {'passed': True})
        self.write(tasks.ARTIFACTS['types'], {'items': [{'unit_id': 'q-1', 'type': '单项填空',
                                                          'subtypes': ['四个选项'], 'words': 5,
                                                          'contentComplete': True, 'evidence': ['正文 L1']}]})
        self.write(tasks.ARTIFACTS['answers'], {'answers': {'1': 'A'},
                                                 'evidence': {'1': ['附录 L1']},
                                                 'difficulty': {'1': '容易'},
                                                 'difficultyEvidence': {'1': ['单句且选项等长']}})

    def write(self, rel, value):
        path = self.batch / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def result(self, stage, payload):
        return {'status': 'completed', 'artifacts': {tasks.ARTIFACTS[stage]: payload}, 'notes': [], 'findings': []}

    def accept(self, stage, payload):
        return tasks.accept_result(self.batch, stage, self.result(stage, payload))

    def explanation(self, **changes):
        row = {'unit_id': 'q-1', 'explanation': '根据主语与语境选 reads。' + tasks.AI_MARK,
               'articleSummary': '', 'subquestions': [], 'evidence': ['正文 L1；附录 L1 仅给答案']}
        row.update(changes)
        return {'items': [row]}

    def knowledge(self, explanation):
        prior = explanation['items'][0]
        return {'items': [{'unit_id': 'q-1', 'knowledge': ['词汇/词汇辨析/动词辨析'], 'answer': 'A',
                           'explanation': prior['explanation'], 'articleSummary': prior['articleSummary'],
                           'subquestions': [], 'evidence': ['正文 L1']}]}

    def test_split_stages_and_single_call_per_duty(self):
        ids = [s.id for s in contracts.STAGES]
        self.assertLess(ids.index('types'), ids.index('explanations'))
        self.assertLess(ids.index('explanations'), ids.index('knowledge'))
        self.assertEqual(ids.count('explanations'), 1)
        self.assertIn('解析匹配', tasks.RULES['explanations'])
        self.assertIn('subagent 2', tasks.RULES['knowledge'])
        self.assertIn('importer injects them verbatim', tasks.RULES['knowledge'])

    def test_prompts_carry_each_subagent_scope(self):
        self.write(tasks.ARTIFACTS['explanations'], self.explanation())
        for stage, needle in (('explanations', '解析匹配'), ('knowledge', 'textbookMappings'),
                              ('types', 'contentComplete'), ('answers', 'difficulty')):
            with self.subTest(stage=stage):
                task = tasks.build_task(self.batch, stage)
                self.assertEqual(task['status'], 'ready', task['preconditions'])
                self.assertIn(needle, task['prompt'])

    def test_explanations_stage_accepts_grounded_and_marked_content(self):
        self.assertTrue(self.accept('explanations', self.explanation())['completed'])
        self.assertTrue(self.accept('explanations', self.explanation(articleSummary=SUMMARY + tasks.AI_MARK))['completed'])

    def test_reading_and_cloze_units_allow_placeholder_summary(self):
        # 用户规则：缺文章大意填占位（待补充），不生成；有 AI 补写时必须标注且不超过 50 字。
        for qtype in sorted(content_rules.SUMMARY_TYPES):
            with self.subTest(qtype=qtype):
                typed = {'q-1': {'unit_id': 'q-1', 'type': qtype, 'subtypes': [], 'evidence': ['正文 L1']}}
                content_rules.validate(self.explanation(articleSummary=content_rules.EMPTY_PLACEHOLDER),
                                       self.units, typed, self.batch)
                content_rules.validate(self.explanation(articleSummary=''), self.units, typed, self.batch)
                content_rules.validate(self.explanation(articleSummary=SUMMARY + tasks.AI_MARK),
                                       self.units, typed, self.batch)
                too_long = '这是一段很长的文章大意' * 20 + tasks.AI_MARK
                with self.assertRaisesRegex(ValueError, 'within 50'):
                    content_rules.validate(self.explanation(articleSummary=too_long),
                                           self.units, typed, self.batch)

    def test_missing_explanation_uses_placeholder_and_unsourced_generated_is_rejected(self):
        # 缺解析填「待补充」或留空都合法（用户要求）；来源不明且未标注的自写解析仍拒绝。
        self.assertTrue(self.accept('explanations', self.explanation(explanation=content_rules.EMPTY_PLACEHOLDER))['completed'])
        self.assertTrue(self.accept('explanations', self.explanation(explanation=''))['completed'])
        with self.assertRaises(ValueError):
            self.accept('explanations', self.explanation(explanation='自己写的解析没有来源也没有标注。'))
        with self.assertRaises(ValueError):
            self.accept('explanations', self.explanation(explanation=tasks.AI_MARK))

    def test_listening_unit_may_omit_content_explanation(self):
        self.write(tasks.ARTIFACTS['types'], {'items': [{'unit_id': 'q-1', 'type': '听力短对话理解',
            'subtypes': ['选文字'], 'evidence': ['正文 L1']}]})
        self.assertTrue(self.accept('explanations', self.explanation(explanation=''))['completed'])

    def test_knowledge_must_copy_content_verbatim(self):
        explanation = self.explanation()
        self.write(tasks.ARTIFACTS['explanations'], explanation)
        self.assertTrue(self.accept('knowledge', self.knowledge(explanation))['completed'])
        forged = self.knowledge(explanation)
        forged['items'][0]['explanation'] = '改写后的解析。' + tasks.AI_MARK
        with self.assertRaisesRegex(ValueError, 'preserve'):
            self.accept('knowledge', forged)
        missing = copy.deepcopy(self.knowledge(explanation))
        missing['items'][0]['articleSummary'] = SUMMARY + tasks.AI_MARK
        with self.assertRaisesRegex(ValueError, 'preserve'):
            self.accept('knowledge', missing)

    def test_generated_explanation_cannot_borrow_another_material(self):
        self.write('01_source/正文.txt', 'Ⅷ.阅读理解\n【文章大意】南极文章。\n56.D 【解析】南极气温。\n'
                                          'B\n【文章大意】舞狮文章。\n59.B 【解析】祈愿好运。\n'
                                          '56. Question\n59. Question')
        units = [{'unit_id': 'group-56', 'unit_kind': 'group', 'numbers': [56]}]
        typed = {'group-56': {'type': '阅读理解'}}
        payload = {'items': [{'unit_id': 'group-56', 'explanation': '', 'articleSummary': '南极文章。',
                              'evidence': ['正文 L1'],
                              'subquestions': [{'number': 56, 'explanation': '南极气温。\nB\n【文章大意】舞狮文章。'}]}]}
        with self.assertRaisesRegex(ValueError, 'EXPLANATION_CROSSES_MATERIAL'):
            content_rules.validate(payload, units, typed, self.batch)


if __name__ == '__main__':
    unittest.main()
