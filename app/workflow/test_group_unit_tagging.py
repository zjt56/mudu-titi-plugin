"""组题只在组级打标签与审核（用户口径 2026-11-05）的回归。

完形填空、语法选择、阅读理解、任务型阅读、读写综合、补全对话、听力信息转换、
选词填空、短文填空、教材语篇填空、听力短文理解、听力长对话理解这些以组题为单位的
题型：整组是唯一的打标签与审核单位——只在组级落知识点，子题 knowledge=[]，
不逐小题打标签、不做小题二审；二审整组抽取并只做组级核对。
"""
import json
import tempfile
import unittest
from pathlib import Path

from workbench import taxonomy
from workflow import review


GROUP_TYPES = (
    "完形填空", "语法选择", "阅读理解", "任务型阅读", "读写综合", "补全对话",
    "听力信息转换", "选词填空", "短文填空", "教材语篇填空",
    "听力短文理解", "听力长对话理解",
)


class GroupUnitTypesTest(unittest.TestCase):
    def test_all_group_types_are_whole_unit(self):
        for name in GROUP_TYPES:
            with self.subTest(type=name):
                self.assertTrue(taxonomy.whole_unit_type(name))

    def test_per_question_types_are_not_whole_unit(self):
        for name in ("单项填空", "单词拼写", "完成句子", "翻译", "语音题", "书面表达", "情景运用"):
            with self.subTest(type=name):
                self.assertFalse(taxonomy.whole_unit_type(name))

    def test_group_children_must_not_carry_knowledge(self):
        # 组题只在组级落标签；子题 knowledge=[] 是合规写法，不产生校验问题。
        item = {"unit_kind": "group", "question_type": "补全对话",
                "subtypes": ["7选5"],
                "knowledge": ["语篇体裁/日常口语/日常对话",
                              "主题/人与社会/社会服务与人际沟通/人际交往",
                              "语用/情景交际"],
                "subquestions": [{"number": 71, "knowledge": []}]}
        self.assertEqual(taxonomy.check_question_labels(item), [])


class GroupSamplePlanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)

    def write(self, relative, value):
        path = self.batch / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
                        encoding="utf-8")

    def build(self, unit_kind, type_name, numbers):
        stem = 'Shared passage.\n' + '\n'.join(
            f'{n}. What happens in question {n}?' for n in numbers)
        self.write('01_source/正文.txt', stem)
        self.write('01_source/structure.confirmed.json', {'units': [
            {'unit_id': 'u', 'unit_kind': unit_kind, 'numbers': numbers, 'stem': stem}]})
        self.write('03_types/types.json', {'items': [
            {'unit_id': 'u', 'type': type_name, 'subtypes': [], 'evidence': ['L1']}]})
        self.write('04_knowledge/knowledge.json', {'items': [
            {'unit_id': 'u', 'knowledge': ['语用/情景交际'],
             'subquestions': ([{'number': str(n), 'knowledge': []} for n in numbers]
                              if unit_kind == 'group' else [])}]})
        self.write('01_source_ans/answers.json', {
            'answers': {str(n): 'A' for n in numbers},
            'difficulty': {str(n): '容易' for n in numbers}})
        self.write('04_knowledge/explanations.json', {'items': [
            {'unit_id': 'u', 'explanation': '', 'explanation_origin': 'not_applicable',
             'articleSummary': '', 'summary_origin': 'not_applicable', 'evidence': ['L1']}]})
        self.write('06_batch/batch.json', {'items': []})
        return review.prepare_round_plan(self.batch)

    def test_group_type_is_sampled_as_one_whole_group_target(self):
        plan = self.build('group', '完形填空', list(range(1, 11)))
        self.assertEqual(len(plan['sample']), 1)
        self.assertIsNone(plan['sample'][0]['number'])
        self.assertEqual(plan['sample'][0]['unit_id'], 'u')

    def test_completion_dialogue_is_also_whole_group(self):
        plan = self.build('group', '补全对话', list(range(71, 76)))
        self.assertEqual(len(plan['sample']), 1)
        self.assertIsNone(plan['sample'][0]['number'])

    def test_standalone_unit_of_group_named_type_still_samples_per_question(self):
        # 独立单题即使题型名属于整组题型（如单句阅读判断）仍按题号抽样。
        plan = self.build('standalone', '阅读理解', [7])
        self.assertEqual(len(plan['sample']), 1)
        self.assertEqual(plan['sample'][0]['number'], '7')

    def test_non_whole_unit_group_samples_each_child_question(self):
        numbers = list(range(1, 11))
        plan = self.build('group', '单项填空', numbers)
        self.assertEqual(len(plan['sample']), (len(numbers) + 4) // 5)
        self.assertTrue(all(q['number'] is not None for q in plan['sample']))


if __name__ == '__main__':
    unittest.main()
