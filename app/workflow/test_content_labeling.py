"""解析/大意只匹配原文，知识点阶段确定性沿用，整组题只挂组级标签。"""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from workflow import agent_tasks as tasks, content_rules, contracts
from workflow.structure_gate import _build_units

SUMMARY = '本文讲述校园生活中的一段经历，并说明了同学之间的互助。'
ORIGINAL = '根据主语与语境选 reads，原解析的标点、措辞均须保留。'


class ContentLabelingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        self.write('01_source/正文.txt', '1. She reads a book. A. reads B. read\n2. They help. A. helped B. help')
        self.write('01_source/附录.txt', '1.A\n2.B')
        sections = [{'type': '单项填空', 'questions': [1]}]
        self.write('01_source/structure.json', {'sections': sections, 'groups': []})
        self.write('01_source/structure.confirmed.json', {'version': '1.0', 'units': _build_units(sections, [], ['1. She reads a book.'])})
        self.units = tasks.confirmed_units(self.batch)
        self.write('07_review/structure-gate.json', {'passed': True})
        self.write(tasks.ARTIFACTS['types'], {'items': [{'unit_id': 'q-1', 'type': '单项填空',
            'subtypes': ['四个选项'], 'words': 5, 'contentComplete': True, 'evidence': ['正文 L1']}]})
        self.write(tasks.ARTIFACTS['answers'], {'answers': {'1': 'A'}, 'evidence': {'1': ['附录 L1']},
            'difficulty': {'1': '容易'}, 'difficultyEvidence': {'1': ['单句且选项等长']}})

    def write(self, rel, value):
        path = self.batch / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def accept(self, stage, payload):
        return tasks.accept_result(self.batch, stage, {'status': 'completed',
            'artifacts': {tasks.ARTIFACTS[stage]: payload}, 'notes': [], 'findings': []})

    def saved(self, stage):
        return json.loads((self.batch / tasks.ARTIFACTS[stage]).read_text(encoding='utf-8'))

    def explanation(self, **changes):
        row = {'unit_id': 'q-1', 'explanation': '无', 'articleSummary': '无',
               'subquestions': [], 'evidence': ['正文 L1；附录 L1']}
        row.update(changes)
        return {'items': [row]}

    def knowledge(self, explanation=None):
        return {'items': [{'unit_id': 'q-1', 'knowledge': ['词汇/词汇辨析/动词辨析'],
            'subquestions': [], 'evidence': ['正文 L1']}]}

    def test_explanations_collect_and_validate_without_answer_output(self):
        from workflow import manifest as mf
        answers = self.batch / tasks.ARTIFACTS['answers']
        self.assertEqual(answers.resolve(), (self.batch / '01_source_ans/answers.json').resolve())
        answers.unlink()
        self.write('01_source_ans/正文.txt', f'1.A 【解析】{ORIGINAL}')
        before = (self.batch / '01_source_ans/正文.txt').read_bytes()
        context, hashes, missing, units = tasks._collect(self.batch, 'explanations')
        self.assertEqual(missing, [])
        self.assertNotIn(tasks.ARTIFACTS['answers'], hashes)
        payload = self.explanation(explanation=ORIGINAL)
        tasks._validate(self.batch, 'explanations', payload, context, units)
        self.assertFalse(answers.exists())
        self.assertEqual((self.batch / '01_source_ans/正文.txt').read_bytes(), before)
        state = mf.new_manifest('x', self.batch, {}, {})
        raw_before = mf.input_hashes(self.batch, state, contracts.STAGE_BY_ID['explanations'])
        self.write(tasks.ARTIFACTS['answers'], {'answers': {'1': 'B'}})
        self.assertEqual(tasks._collect(self.batch, 'explanations')[1], hashes)
        self.assertEqual(mf.input_hashes(self.batch, state, contracts.STAGE_BY_ID['explanations']), raw_before)
        with self.assertRaisesRegex(ValueError, 'Content source/ownership'):
            tasks._validate(self.batch, 'explanations', self.explanation(explanation='捏造的解析'), context, units)

    def test_missing_optional_content_is_normalized_without_generating(self):
        row = {'unit_id': 'g', 'subquestions': [{'number': 1}, {'number': 2, 'explanation': ORIGINAL}]}
        content_rules.normalize_optional_content(row)
        self.assertEqual(row['explanation'], '无')
        self.assertEqual(row['articleSummary'], '无')
        self.assertEqual(row['subquestions'][0]['explanation'], '无')
        self.assertEqual(row['subquestions'][1]['explanation'], ORIGINAL)
        row['explanation'] = ['wrong type']
        content_rules.normalize_optional_content(row)
        self.assertEqual(row['explanation'], ['wrong type'])

    def test_normalization_does_not_hide_generated_or_unverified_content(self):
        row = {'explanation': '自撰解析', 'articleSummary': SUMMARY + tasks.AI_MARK,
               'subquestions': [{'number': 1, 'explanation': tasks.AI_MARK}]}
        before = copy.deepcopy(row)
        content_rules.normalize_optional_content(row)
        self.assertEqual(row, before)

    def test_split_stages_and_single_call_per_duty(self):
        ids = [stage.id for stage in contracts.STAGES]
        self.assertLess(ids.index('types'), ids.index('explanations'))
        self.assertLess(ids.index('explanations'), ids.index('knowledge'))
        self.assertEqual(ids.count('explanations'), 1)
        self.assertIn('解析匹配', tasks.RULES['explanations'])
        self.assertIn('subagent 2', tasks.RULES['knowledge'])
        self.assertIn('does not read answers or explanations', tasks.RULES['knowledge'])

    def test_prompts_carry_each_subagent_scope(self):
        self.write(tasks.ARTIFACTS['explanations'], self.explanation())
        for stage, needle in (('explanations', '解析匹配'), ('knowledge', 'textbook_index'),
                              ('types', 'contentComplete'), ('answers', 'difficulty')):
            with self.subTest(stage=stage):
                task = tasks.build_task(self.batch, stage)
                self.assertEqual(task['status'], 'ready', task['preconditions'])
                self.assertIn(needle, task['prompt'])
        self.assertIn('subquestions must carry knowledge=[]', tasks.RULES['knowledge'])

    def test_explanations_stage_accepts_original_content_verbatim(self):
        self.write('01_source/附录.txt', f'【文章大意】{SUMMARY}\n1.A 【解析】{ORIGINAL}\n2.B')
        self.assertTrue(self.accept('explanations', self.explanation(explanation=ORIGINAL, articleSummary=SUMMARY))['completed'])
        saved = self.saved('explanations')['items'][0]
        self.assertEqual(saved['explanation'], ORIGINAL)
        self.assertEqual(saved['articleSummary'], SUMMARY)

    def test_reading_and_cloze_missing_summary_is_wu_and_generation_is_forbidden(self):
        for qtype in sorted(content_rules.SUMMARY_TYPES):
            typed = {'q-1': {'type': qtype}}
            for value in ('无', '', None):
                with self.subTest(qtype=qtype, missing=value):
                    rows = content_rules.validate(self.explanation(articleSummary=value), self.units, typed, self.batch)
                    self.assertEqual(rows['q-1']['articleSummary'], '无')
            for value in (SUMMARY, SUMMARY + tasks.AI_MARK, '长大意' * 100 + tasks.AI_MARK):
                with self.subTest(qtype=qtype, generated=value[:20]):
                    with self.assertRaises(ValueError):
                        content_rules.validate(self.explanation(articleSummary=value), self.units, typed, self.batch)

    def test_long_original_summary_is_preserved_without_old_generation_limit(self):
        original = '这是原材料自带的完整大意，不能为了旧生成字数上限而截断。' * 5
        self.write('01_source/附录.txt', f'【文章大意】{original}\n1.A 【解析】{ORIGINAL}')
        rows = content_rules.validate(self.explanation(explanation=ORIGINAL, articleSummary=original),
            self.units, {'q-1': {'type': '阅读理解'}}, self.batch)
        self.assertEqual(rows['q-1']['articleSummary'], original)

    def test_missing_explanation_normalizes_but_generated_content_is_rejected(self):
        for value in ('无', '', None, '  '):
            self.assertTrue(self.accept('explanations', self.explanation(explanation=value))['completed'])
            self.assertEqual(self.saved('explanations')['items'][0]['explanation'], '无')
        for value in ('自己写的解析没有来源也没有标注。', tasks.AI_MARK, ORIGINAL + tasks.AI_MARK):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.accept('explanations', self.explanation(explanation=value))

    def test_original_explanation_cannot_be_rewritten_truncated_or_omitted(self):
        self.write('01_source/附录.txt', f'1.A 【解析】{ORIGINAL}')
        for value in ('无', '', '根据主语与语境选 reads', ORIGINAL.replace('reads', 'read'), ORIGINAL.replace('，', ',')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.accept('explanations', self.explanation(explanation=value))

    def test_listening_unit_may_omit_content_explanation(self):
        self.write(tasks.ARTIFACTS['types'], {'items': [{'unit_id': 'q-1', 'type': '听力短对话理解',
            'subtypes': ['选文字'], 'evidence': ['正文 L1']}]})
        self.assertTrue(self.accept('explanations', self.explanation(explanation=''))['completed'])

    def test_knowledge_importer_accepts_only_labels_and_ignores_echoes(self):
        self.write('01_source/附录.txt', f'【文章大意】{SUMMARY}\n1.A 【解析】{ORIGINAL}')
        upstream = self.explanation(explanation=ORIGINAL, articleSummary=SUMMARY)
        self.assertTrue(self.accept('explanations', upstream)['completed'])
        forged = self.knowledge(upstream)
        forged['items'][0].update(answer='B', explanation='改写后的解析。', articleSummary='自撰大意。')
        self.assertTrue(self.accept('knowledge', forged)['completed'])
        saved = self.saved('knowledge')['items'][0]
        for field in ('answer', 'explanation', 'articleSummary'):
            self.assertNotIn(field, saved)

    def test_whole_unit_knowledge_is_label_only(self):
        group = {'unit_id': 'group-1-2', 'unit_kind': 'group', 'numbers': [1, 2], 'type': '补全对话'}
        self.write('01_source/structure.confirmed.json', {'version': '1.0', 'units': [group]})
        self.write(tasks.ARTIFACTS['types'], {'items': [{'unit_id': group['unit_id'], 'type': '补全对话',
            'subtypes': ['7选5'], 'words': 12, 'contentComplete': True, 'evidence': ['正文 L1–2']}]})
        group_tags = ['语篇体裁/日常口语/日常对话', '主题/人与社会/社会服务与人际沟通/人际交往', '语用/情景交际']
        tags = {'items': [{'unit_id': group['unit_id'], 'knowledge': group_tags,
            'subquestions': [{'number': 1, 'knowledge': []}, {'number': 2, 'knowledge': []}], 'evidence': ['正文 L1–2']}]}
        self.assertTrue(self.accept('knowledge', tags)['completed'])
        saved = self.saved('knowledge')['items'][0]
        self.assertEqual(saved['knowledge'], group_tags)
        self.assertEqual([child['knowledge'] for child in saved['subquestions']], [[], []])
        self.assertTrue(all('explanation' not in child and 'answer' not in child
                            for child in saved['subquestions']))


    def test_explanation_cannot_borrow_another_material(self):
        self.write('01_source/正文.txt', 'Ⅷ.阅读理解\n【文章大意】南极文章。\n56.D 【解析】南极气温。\n'
            'B\n【文章大意】舞狮文章。\n59.B 【解析】祈愿好运。\n56. Question\n59. Question')
        units = [{'unit_id': 'group-56', 'unit_kind': 'group', 'numbers': [56]}]
        payload = {'items': [{'unit_id': 'group-56', 'explanation': '', 'articleSummary': '南极文章。',
            'evidence': ['正文 L1'], 'subquestions': [{'number': 56, 'explanation': '南极气温。\nB\n【文章大意】舞狮文章。'}]}]}
        with self.assertRaisesRegex(ValueError, 'EXPLANATION_CROSSES_MATERIAL'):
            content_rules.validate(payload, units, {'group-56': {'type': '阅读理解'}}, self.batch)


if __name__ == '__main__':
    unittest.main()
