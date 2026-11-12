"""Answer scope keeps inline blank proof and repairs only proven same-value citations."""
import copy
import unittest

from workflow import agent_tasks, answers_direct
from workflow.question_identity import assign_scoped, question_key
from workflow.upload_content import _source_line, resolve_scoped_answer, validate_scoped_answer
from workflow import test_scoped_lesson_answers as fixtures


class AnswerBlankEvidenceTest(unittest.TestCase):
    write_sources = fixtures.CompactLessonAnswersTest.write_sources

    def setUp(self):
        fixture = fixtures.CompactLessonAnswersTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.batch = fixture.batch
        self.body = ('第一课时 Section A\nⅠ.补全对话\n'
                     'A:Hello,Andy.1.<u>\u3000\u3000\u3000\xa0</u>\n'
                     'B:Thanks.\nA:2.<u>\u3000\u3000</u>\n'
                     'A.First. B.Second.\n'
                     'Ⅱ.根据首字母及汉语提示补全单词\n1.Spell?\n')
        self.appendix = '参考答案\nⅠ.1~2 AB\nⅡ.1.bottle\n'
        self.units = assign_scoped([
            {'unit_kind': 'group', 'type': '补全对话', 'numbers': [1, 2],
             'source_span': {'start_line': 3, 'end_line': 6}},
            {'unit_kind': 'standalone', 'type': '单词拼写', 'numbers': [1],
             'source_span': {'start_line': 8, 'end_line': 8}},
        ], self.body)
        self.write_sources()

    def test_inline_html_blank_survives_style_removal_and_scope_validation(self):
        line = self.body.splitlines()[2]
        self.assertEqual(_source_line(line, anchor_view=True), 'A:Hello,Andy.1.______')
        found = resolve_scoped_answer(self.batch, self.units[0], 1)
        self.assertEqual(found, {'answer': 'A', 'evidence': ['01_source/附录.txt:2']})
        validate_scoped_answer(self.batch, self.units[0], 1, 'A', found['evidence'])
        self.assertEqual(_source_line(_source_line(line, anchor_view=True), anchor_view=True),
                         _source_line(line, anchor_view=True))

    def test_compact_answer_spacing_and_verbatim_values_are_not_normalized_to_blanks(self):
        self.assertEqual(_source_line('Ⅰ.1.red\u3000\u30002.black'), 'Ⅰ.1.red\u3000\u30002.black')
        self.assertEqual(_source_line('1.<u>yellow</u>'), '1.yellow')
        self.assertEqual(_source_line('1.<u>  2  </u>', anchor_view=True), '1.__2__')
        self.assertEqual(_source_line('Ⅰ.1.<u>5</u>\u3000\u30002.<u>10</u>'), 'Ⅰ.1.5\u3000\u30002.10')
        payload = answers_direct.build_payload(self.batch)
        self.assertEqual(len(payload['answers']), 3)
        self.assertEqual(payload['missing'], {})

    def test_underlined_numeric_answer_is_not_rewritten_as_a_question_blank(self):
        self.appendix = '参考答案\nⅠ.1. <u>5</u>\u3000\u30002. <u>10</u>\nⅡ.1.bottle\n'
        self.write_sources()
        found = resolve_scoped_answer(self.batch, self.units[0], 1, '5')
        self.assertEqual(found['answer'], '5')
        validate_scoped_answer(self.batch, self.units[0], 1, '5', found['evidence'])
        payload = answers_direct.build_payload(self.batch)
        self.assertEqual(payload['answers'][question_key(self.units[0], 2)], '10')

    def test_acceptance_recovers_current_same_value_coordinates_and_preserves_raw_candidate(self):
        payload = answers_direct.build_payload(self.batch)
        key = question_key(self.units[0], 1)
        payload['evidence'][key] = ['Ⅰ.补全对话第1题，原卷答案A']
        response = {'status': 'completed', 'artifacts': {agent_tasks.ARTIFACTS['answers']: payload},
                    'notes': [], 'findings': []}
        original = copy.deepcopy(response)
        report = agent_tasks.accept_result(self.batch, 'answers', response)
        self.assertTrue(report['completed'])
        self.assertEqual(response, original)
        import json
        installed = json.loads((self.batch / agent_tasks.ARTIFACTS['answers']).read_text())
        self.assertEqual(installed['answers'], payload['answers'])
        self.assertIn('01_source/附录.txt:2', installed['evidence'][key])
        self.assertTrue(any('补齐坐标' in note for note in report['notes']))

    def test_per_unit_execution_recovers_citations_without_semantic_retry(self):
        import json
        from workflow import label_execution, unit_tasks
        payload = answers_direct.build_payload(self.batch)
        calls = []
        def invoke(prompt, directory, config, **kwargs):
            data = json.loads(prompt.split(unit_tasks._BEGIN, 1)[1].split(unit_tasks._END, 1)[0])
            selected = data['confirmed_units_with_stable_ids']
            calls.extend(u['unit_id'] for u in selected)
            keys = {question_key(u, n) for u in selected for n in u['numbers']}
            candidate = {'answers': {key: value for key, value in payload['answers'].items() if key in keys},
                         'evidence': {key: ['仅大题说明，无精确坐标'] for key in keys}, 'missing': {}}
            return {'response': {'status': 'completed', 'artifacts': {agent_tasks.ARTIFACTS['answers']: candidate},
                                 'notes': [], 'findings': []}}
        config = {'max_input_chars': 600000, 'retries': 1, 'part_concurrency': 1,
                  'model_cache_identity': 'fixture-model'}
        result = label_execution.run(self.batch, 'answers', config, self.batch / 'run', invoke=invoke)
        self.assertEqual(sorted(calls), sorted(u['unit_id'] for u in self.units))
        accepted = result['response']['artifacts'][agent_tasks.ARTIFACTS['answers']]
        self.assertTrue(any('补齐坐标' in note for note in result['response']['notes']))
        self.assertEqual(accepted['answers'], payload['answers'])
        self.assertTrue(all(any('01_source/附录.txt:' in text for text in evidence)
                            for evidence in accepted['evidence'].values()))

    def test_wrong_value_or_wrong_section_is_never_repaired(self):
        context, _, _, units = agent_tasks._collect(self.batch, 'answers')
        for wrong in ('B', 'bottle'):
            with self.subTest(wrong=wrong):
                payload = answers_direct.build_payload(self.batch)
                key = question_key(self.units[0], 1)
                payload['answers'][key] = wrong
                payload['evidence'][key] = ['不带坐标的说明']
                with self.assertRaises(ValueError):
                    agent_tasks._validate(self.batch, 'answers', payload, context, units, notes=[])

    def test_read_only_validation_stays_strict_without_recovery_notes(self):
        context, _, _, units = agent_tasks._collect(self.batch, 'answers')
        payload = answers_direct.build_payload(self.batch)
        payload['evidence'][question_key(self.units[0], 1)] = ['仅大题描述']
        with self.assertRaises(ValueError):
            agent_tasks._validate(self.batch, 'answers', payload, context, units)


if __name__ == '__main__':
    unittest.main()
