"""Strict child evidence extraction, using synthetic source only."""
import unittest

from workflow.review_child_source import prove_child, quote_belongs_to_child


class ChildSourceTest(unittest.TestCase):
    def setUp(self):
        self.stem = ('Shared library passage.\n'
                     '1. What is borrowed?\nA. A book\nB. A pen\nC. A map\nD. A key\n'
                     '2. Who visits?\nA. Peter\nB. Alice\nC. Henry\nD. Jane')
        self.source = 'Paper title\n' + self.stem + '\nNext section\n'
        self.unit = {'unit_id': 'group', 'numbers': [1, 2], 'stem': self.stem}

    def test_unique_question_span_and_exact_quote(self):
        proof = prove_child(self.source, self.unit, '2', source_sha256='original-file-hash')
        self.assertEqual(proof['status'], 'proven')
        self.assertEqual(proof['source_sha256'], 'original-file-hash')
        self.assertEqual(proof['text'], '2. Who visits?\nA. Peter\nB. Alice\nC. Henry\nD. Jane')
        self.assertEqual(self.source[proof['start']:proof['end']], proof['text'])
        self.assertTrue(quote_belongs_to_child(self.source, self.unit, 2, 'Who visits?'))
        for quote in ('Shared library passage.', 'What is borrowed?', 'A', '2.'):
            self.assertFalse(quote_belongs_to_child(self.source, self.unit, 2, quote))
        # The first child's span is truncated before the second question.
        first = prove_child(self.source, self.unit, '1')
        self.assertEqual(first['status'], 'proven')
        self.assertNotIn('Who visits?', first['text'])
        self.assertFalse(quote_belongs_to_child(self.source, self.unit, 1, 'A. Peter'))

    def test_options_on_one_or_two_lines_and_whitespace_differences(self):
        cases = ('A. one B. two C. three D. four', 'A. one B. two\nC. three D. four')
        for options in cases:
            stem = '(　　)31. Choose one.\n' + options
            with self.subTest(options=options):
                unit = {'unit_id': 'g', 'numbers': [31], 'stem': stem}
                proof = prove_child(stem, unit, 31)
                self.assertEqual(proof['status'], 'proven')
                self.assertEqual(proof['text'], stem)
                self.assertTrue(quote_belongs_to_child(stem, unit, 31, 'Choose one.'))
        # Assembled stems differ from the source by trailing NBSP only.
        stem = '(　　)32.—Is this iPad yours?\nA. one B. two C. three D. four'
        source = '(　　)32.—Is this iPad yours?\xa0\nA. one B. two C. three D. four\n'
        proof = prove_child(source, {'unit_id': 'g', 'numbers': [32], 'stem': stem}, 32)
        self.assertEqual(proof['status'], 'proven')
        self.assertTrue(quote_belongs_to_child(source, {'unit_id': 'g', 'numbers': [32],
                                                        'stem': stem}, 32, 'Is this iPad'))

    def test_numbered_option_lines_are_bound_to_that_question(self):
        cloze = 'Story __46__ and __47__.\n(　　)46.A. one B. two C. three D. four\n' \
                '(　　)47.A. five B. six C. seven D. eight'
        unit = {'unit_id': 'g', 'numbers': [46, 47], 'stem': cloze}
        proof = prove_child(cloze, unit, 46)
        self.assertEqual(proof['status'], 'proven')
        self.assertIn('46.', proof['text'])
        self.assertNotIn('47.', proof['text'])
        self.assertTrue(quote_belongs_to_child(cloze, unit, 46, 'A. one B. two C. three D. four'))
        self.assertFalse(quote_belongs_to_child(cloze, unit, 46, 'A. five'))
        # Shared material is context, not target-specific evidence.
        self.assertFalse(quote_belongs_to_child(cloze, unit, 46, 'Story __46__ and __47__.'))

    def test_question_line_inside_group_stem_is_located(self):
        stem = '听下面一段对话,回答第18至20小题。\n18.What is wrong?\nA. fever\nB. cough\nC. headache\n' \
               '19.Where is she?\nA. school\nB. hospital\nC. park'
        source = 'Ⅲ.长对话理解\n' + stem + '\nⅣ.短文理解\n'
        unit = {'unit_id': 'g', 'numbers': [18, 19, 20], 'stem': stem}
        proof = prove_child(source, unit, 19)
        self.assertEqual(proof['status'], 'proven')
        self.assertTrue(proof['text'].startswith('19.Where is she?'))
        self.assertNotIn('What is wrong?', proof['text'])

    def test_numbered_blank_marker_anchors_dialogue_and_table_target(self):
        dialogue = 'Li Lei:I am going home.\nBruce:<u>\u300066 \u3000</u>\nSee you.\n' \
                   'Bruce:<u>\u300068 </u>\nOK.'
        unit = {'unit_id': 'g', 'numbers': [66, 68], 'stem': dialogue}
        proof = prove_child(dialogue, unit, 66)
        self.assertEqual(proof['status'], 'proven')
        self.assertEqual(proof['method'], 'numbered-blank-marker/v1')
        self.assertIn('66', proof['text'])
        self.assertNotIn('68', proof['text'])
        self.assertTrue(quote_belongs_to_child(dialogue, unit, 66, 'Li Lei:I am going home.'))
        self.assertFalse(quote_belongs_to_child(dialogue, unit, 66, 'See you.'))
        self.assertFalse(quote_belongs_to_child(dialogue, unit, 66, 'OK.'))
        table = 'Who | The students of Class Eight,Grade<u>\u300026\u3000 </u>\nWhen | Last<u>\u300027\u3000 </u>'
        unit = {'unit_id': 't', 'numbers': [26, 27], 'stem': table}
        proof = prove_child(table, unit, 26)
        self.assertEqual(proof['status'], 'proven')
        self.assertIn('26', proof['text'])
        self.assertNotIn('27', proof['text'])

    def test_unbounded_or_ambiguous_targets_remain_uncertain(self):
        table = 'Table | 1. ____ | 2. ____'
        self.assertEqual(prove_child(table, {'numbers': [1, 2], 'stem': table},
                                     1)['status'], 'uncertain')
        # A stem line absent from the source is never treated as proven.
        stem = '1. Question?\nA. one\nB. two\nC. three\nD. four\nE. extra'
        source = '1. Question?\nA. one\nB. two\nC. three\nD. four'
        proof = prove_child(source, {'numbers': [1], 'stem': stem}, 1)
        self.assertEqual(proof['status'], 'uncertain')
        self.assertTrue(proof['reason'])

    def test_missing_or_duplicate_unit_or_question_remains_uncertain(self):
        self.assertEqual(prove_child(self.source + self.stem, self.unit, 1)['status'], 'uncertain')
        self.assertEqual(prove_child(self.source, self.unit, 3)['status'], 'uncertain')
        self.assertEqual(prove_child('changed', self.unit, 1)['status'], 'uncertain')
        duplicate = '1. Q?\nA. a\nB. b\nC. c\nD. d\n1. Q2?\nA. a\nB. b\nC. c\nD. d'
        self.assertEqual(prove_child(duplicate, {'numbers': [1], 'stem': duplicate},
                                     1)['status'], 'uncertain')


if __name__ == '__main__':
    unittest.main()
