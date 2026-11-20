"""Inline dialogue blanks printed after prose must stay numbered slots.

Batch 03 七上人教版 Starter Unit 1 课时3 Ⅰ.补全对话 prints
`A:Hello,I am Tony.1.______` and `A:I am in Class 3.4.______`:
the number trails the speaker line instead of starting it.  Both blanks are
real child numbers 1 and 4, so a group declared as 2,3,5 is non-contiguous and
the structure gate reports SOURCE_NUMBERS_UNDECLARED for lines 78/84.
"""
import unittest

from workflow.extract import infer_structure


DIALOGUE = (
    'A:Hello,I am Tony.1.______\n'
    'B:My name is Linda.\n'
    'A:Nice to meet you.\n'
    'B:2.______\n'
    'A:How are you?\n'
    'B:3.______\n'
    'A:I am in Class 3.4.______\n'
    'B:I am in Class 1.\n'
    'A:Is Ms Liu your teacher?\n'
    'B:5.______\n'
    'A.I’m fine.\nB.Yes,she is.\nC.Are you Linda?\n'
    'D.Can you help me?\nE.What’s your name?\n'
    'F.Nice to meet you too.\nG.What class are you in?\n'
)


class InlineDialogueBlanksTest(unittest.TestCase):
    def test_weak_numbers_see_ascii_underscore_blanks_after_prose(self):
        from workflow.preview import _weak_numbers
        self.assertEqual(_weak_numbers('A:Hello,I am Tony.1.______'), [1])
        self.assertEqual(_weak_numbers('A:I am in Class 3.4.______'), [4])
        self.assertEqual(_weak_numbers('B:2.______'), [2])

    def test_section_numbers_keep_inline_blank_slots_of_dialogue_group(self):
        from workflow.structure_gate import _section_numbers
        section = {
            'type': '补全对话',
            'questions': [1, 2, 3, 4, 5],
            'question_locations': [
                {'number': 1, 'line': 78, 'kind': 'blank'},
                {'number': 2, 'line': 81, 'kind': 'question'},
                {'number': 3, 'line': 83, 'kind': 'question'},
                {'number': 4, 'line': 84, 'kind': 'blank'},
                {'number': 5, 'line': 87, 'kind': 'question'},
            ],
        }
        self.assertEqual(_section_numbers(section), [1, 2, 3, 4, 5])
        # 行首锚点之外、提取侧也未记录的空位号不得进入题号集合。
        junk = {'type': '补全对话', 'questions': [1],
                'question_locations': [{'number': 1, 'line': 5, 'kind': 'question'},
                                       {'number': 7, 'line': 6, 'kind': 'blank'}]}
        self.assertEqual(_section_numbers(junk), [1])

    def test_sync_dialogue_group_declares_every_printed_slot(self):
        text = 'Unit 1\n第一课时 Section A\n补全对话\n' + DIALOGUE
        structure, _, _ = infer_structure(text)
        section = structure['sections'][0]
        self.assertEqual(section['questions'], [1, 2, 3, 4, 5])
        self.assertEqual(sorted(structure['groups'][0]['questions']), [1, 2, 3, 4, 5])
        self.assertFalse(any(r['code'] == 'SOURCE_NUMBERS_UNDECLARED'
                             for r in structure['unresolved']))

    def test_task_reading_table_blanks_are_declared_and_bound_without_undeclared_error(self):
        from workflow.structure_gate import _build_scoped_units, _scoped_source_errors, _section_numbers
        text = '\n'.join((
            'Starter Unit 3　Welcome!',
            '第四课时　Project',
            '**【三、B 创新阅读专练】**',
            '**任务型阅读**',
            'Hello,everyone.My name is Linda.',
            '**1.**________ First paragraph.',
            '**2.**________ Second paragraph.',
            '**3.**________ Third paragraph.',
            '**4.**________ Fourth paragraph.',
            '**5.**________ Fifth paragraph.',
            '任务一:阅读短文,选出每段的主旨大意。',
            '任务二:再次阅读短文,补全下面的表格,每空一词。',
            '[TABLE 2]',
            'About Linda<br>◆Friend is 6.________ years old and not 7.________.<br>'
            '◆Her 8.________ are interesting.<br>'
            '◆Favourite 9.________ is a cat.<br>'
            '◆She has 10.________ yellow caps.',
            '[/TABLE]',
        )) + '\n'
        structure, body, _ = infer_structure(text)
        section = structure['sections'][0]
        self.assertEqual(section['type'], '任务型阅读')
        self.assertEqual(section['questions'], list(range(1, 11)))
        self.assertEqual(_section_numbers(section), list(range(1, 11)))
        lines = body.splitlines()
        units = _build_scoped_units(structure['sections'], structure['groups'], lines)
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]['numbers'], list(range(1, 11)))
        errors = _scoped_source_errors(units, structure['sections'], lines)
        self.assertFalse(any(e['code'] == 'SOURCE_NUMBERS_UNDECLARED' for e in errors), errors)
        # 未记录进 section['questions'] 的 blank 仍不能混入。
        junk = {'type': '任务型阅读', 'questions': [1, 2],
                'question_locations': [{'number': 1, 'line': 6, 'kind': 'question'},
                                       {'number': 2, 'line': 7, 'kind': 'question'},
                                       {'number': 9, 'line': 14, 'kind': 'blank'}]}
        self.assertEqual(_section_numbers(junk), [1, 2])


if __name__ == '__main__':
    unittest.main()
