"""Writing response rules are presentation, not required prompt words."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow.question_content import (
    _required_stem, audit_batch_source, audit_source_unit,
    strip_writing_answer_space,
)


class WritingAnswerSpaceTest(unittest.TestCase):
    def unit(self, text):
        return {'unit_kind': 'standalone', 'type': '书面表达', 'numbers': [81],
                'stem': text, 'source_expected_options': 0,
                'source_span': {'number_binding': 'single_writing_section'}}

    def test_only_empty_rules_removed_and_given_sentence_kept(self):
        text = ('**请根据图片写短文。**\n**[IMG prompt.jpeg]**\n'
                '**My Experience of Making a Poster**\n'
                '**Last week,I made a poster.****<u>\u3000\u00a0</u>**\n'
                '**<u>\u00a0</u>**\n**<u>______</u>**\n______\n'
                'Given final words. ______\n')
        actual = strip_writing_answer_space(text)
        self.assertNotIn('<u>', actual)
        self.assertNotIn('______', actual)
        self.assertIn('Last week,I made a poster.', actual)
        self.assertIn('Given final words.', actual)
        self.assertIn('[IMG prompt.jpeg]', actual)
        self.assertIn('My Experience of Making a Poster', actual)
        self.assertNotIn('\n**\n', actual)
        self.assertEqual(strip_writing_answer_space(actual), actual)
        required = audit_source_unit(self.unit(text))['questions'][81]['stem']
        self.assertEqual(required, actual)

    def test_terminal_repeated_title_and_answer_index_are_not_writing_content(self):
        prompt = '请根据图片写短文。\n[IMG task.jpeg]\n1.活动目的；\n2.你的体会。\nLast week, we learned a lot.'
        index = '\n12024年安徽省初中学业水平考试\n[TABLE 3]\n题号 | 1~5 | 6~10\n答案 | CCAAB | BAABC\n[/TABLE]'
        self.assertEqual(_required_stem(prompt + index), prompt)
        # A table of task information must stay required.
        actual_task = '\n2024年安徽省初中学业水平考试\n[TABLE 3]\n项目 | 消防活动\n地点 | 学校\n[/TABLE]'
        self.assertEqual(_required_stem(prompt + actual_task), prompt + actual_task)
        # Even an answer index is not removed when real prompt words follow it.
        self.assertEqual(_required_stem(prompt + index + '\nDo not omit this scoring point.'),
                         prompt + index + '\nDo not omit this scoring point.')

    def test_real_words_underlines_and_initials_remain_required(self):
        text = ('Write about <u>school life</u>.\n'
                '<u>My Experience</u>\n'
                'Complete <u>r______</u> here.\n'
                'Use <u>2023</u> in the title.\n'
                'Keep the supplied opening.\n'
                'Write at least 80 words.')
        self.assertEqual(strip_writing_answer_space(text), text)
        self.assertEqual(_required_stem(text), text)
        # A line mentioning an exam inside the prompt must not disappear.
        text = '介绍初中学业水平考试的经历。\nActual ending.'
        self.assertEqual(_required_stem(text), text)

    def audit(self, source, candidate):
        unit = self.unit(source)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '01_source').mkdir()
            (root / '01_source/正文.txt').write_text(source)
            (root / '01_source/structure.json').write_text(json.dumps({'sections': [], 'groups': []}))
            (root / '01_source/structure.confirmed.json').write_text(json.dumps({'units': [unit]}))
            row = {'unit_id': 'q-81', 'unit_kind': 'standalone', 'number': 81,
                   'numbers': [81], 'stem': candidate, 'material': '',
                   'options': [], 'subquestions': []}
            # Isolate surface fidelity against a source unit, without running or
            # approving an unrelated structural gate for an unnumbered fixture.
            with patch('workflow.structure_gate._build_units', return_value=[copy.deepcopy(unit)]):
                return audit_batch_source(root, {'items': [row]})

    def test_surface_can_omit_rules_but_not_opening_words(self):
        source = ('Write your experience.\n[IMG prompt.jpeg]\n'
                  'My Experience\nLast week,I made a poster.<u>\u00a0</u>\n'
                  '<u>\u3000</u>\n______')
        candidate = strip_writing_answer_space(source)
        audit = self.audit(source, candidate)
        self.assertTrue(audit['passed'], audit['issues'])
        missing = self.audit(source, candidate.replace('Last week,I made a poster.', ''))
        self.assertIn('SOURCE_STEM_MISSING', {i['code'] for i in missing['issues']})
        missing_image = self.audit(source, candidate.replace('[IMG prompt.jpeg]', ''))
        self.assertIn('SOURCE_IMAGE_MISSING', {i['code'] for i in missing_image['issues']})

    def test_real_underlined_prompt_words_still_enforced(self):
        source = 'Write about <u>school life</u>.\nGiven opening.\n<u>\u00a0</u>'
        candidate = strip_writing_answer_space(source)
        self.assertTrue(self.audit(source, candidate)['passed'])
        unmarked = self.audit(source, candidate.replace('<u>school life</u>', 'school life'))
        self.assertIn('SOURCE_UNDERLINE_MISSING', {i['code'] for i in unmarked['issues']})
        removed = self.audit(source, candidate.replace('<u>school life</u>', ''))
        self.assertIn('SOURCE_STEM_MISSING', {i['code'] for i in removed['issues']})

    def test_nonwriting_numbered_gaps_unaffected(self):
        unit = {'unit_kind': 'standalone', 'type': '单项填空', 'numbers': [81],
                'stem': '81.Choose __81__ now.\nA.One B.Two C.Three D.Four',
                'source_expected_options': 4}
        self.assertIn('__81__', audit_source_unit(unit)['questions'][81]['stem'])


if __name__ == '__main__':
    unittest.main()
