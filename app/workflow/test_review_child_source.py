"""Strict child evidence extraction, using synthetic source only."""
import unittest

import hashlib

from copy import deepcopy

from workflow.question_identity import assign_scoped
from workflow.review_child_source import (prove_child, quote_belongs_to_child,
                                         quote_belongs_to_child_loose,
                                         quote_belongs_to_unit_material)


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

    def test_material_span_maps_cleaned_lines_to_raw_and_rejects_stale(self):
        source = ('2025年上海市中考\r\nShared passage.\r\n第 1 页 共 2 页\r\n'
                  '1. Question?\r\nA. Answer\r\nNext group passage.\r\n2. Other?\r\n')
        unit = {'stem': 'Shared passage.\n1. Question?\nA. Answer',
                'source_span': {'start_line': 1, 'end_line': 3, 'basis': 'ai_classified',
                                'text_sha256': hashlib.sha256(source.encode()).hexdigest()}}
        self.assertTrue(quote_belongs_to_unit_material(source, unit, 'Shared passage.'))
        self.assertTrue(quote_belongs_to_unit_material(source, unit, 'A. Answer'))
        self.assertFalse(quote_belongs_to_unit_material(source, unit, 'Next group passage.'))
        self.assertFalse(quote_belongs_to_unit_material(source + 'changed', unit, 'Shared passage.'))
        for basis in ('section_source', 'cleaned_source'):
            unit['source_span']['basis'] = basis
            self.assertTrue(quote_belongs_to_unit_material(source, unit, 'Shared passage.'))
        unit['source_span']['basis'] = 'original_pdf'
        self.assertFalse(quote_belongs_to_unit_material(source, unit, 'Shared passage.'))

    def test_missing_or_duplicate_unit_or_question_remains_uncertain(self):
        self.assertEqual(prove_child(self.source + self.stem, self.unit, 1)['status'], 'uncertain')
        self.assertEqual(prove_child(self.source, self.unit, 3)['status'], 'uncertain')
        self.assertEqual(prove_child('changed', self.unit, 1)['status'], 'uncertain')
        duplicate = '1. Q?\nA. a\nB. b\nC. c\nD. d\n1. Q2?\nA. a\nB. b\nC. c\nD. d'
        self.assertEqual(prove_child(duplicate, {'numbers': [1], 'stem': duplicate},
                                     1)['status'], 'uncertain')


class ScopedChildSourceTest(unittest.TestCase):
    def setUp(self):
        self.heading = 'Ⅰ.判断下列各组字母的大小写是否一致(一致的在括号内写T,不一致的写F)'
        self.stem = ('1.U　v (　　　)　　　　2.W　x (　　　)\n'
                     '3.F　\tf\t(　　　)\t4.C　\tg\t(　　　)\n'
                     '5.D　\tb\t(　　　)\t6.G　\tj\t(　　　)\n'
                     '7.T　\tt\t(　　　)\t8.N　\tn\t(　　　)')
        self.source = self.heading + '\n' + self.stem + '\n\nⅡ.找出下列句子中书写错误的单词并改正\n1.My name is emma.\n'
        self.unit = self.scoped(self.source, self.stem, 2, 6, list(range(1, 9)))

    @staticmethod
    def scoped(source, stem, first, last, numbers):
        return assign_scoped([{'unit_kind': 'group', 'type': '语音题', 'numbers': numbers,
                               'stem': stem, 'source_span': {'basis': 'cleaned_source',
                                                           'start_line': first, 'end_line': last}}], source)[0]

    def test_inline_second_child_has_exact_character_span(self):
        proof = prove_child(self.source, self.unit, 8)
        self.assertEqual(proof['status'], 'proven', proof)
        self.assertEqual(proof['method'], 'scoped-numbered-stem-characters/v1')
        self.assertEqual(proof['text'], '8.N　\tn\t(　　　)')
        self.assertEqual(self.source[proof['start']:proof['end']], proof['text'])
        self.assertNotIn('7.T', proof['text'])
        self.assertTrue(quote_belongs_to_child(self.source, self.unit, 8, proof['text']))
        self.assertTrue(quote_belongs_to_child_loose(self.source, self.unit, 8, '8.N n ( )'))

    def test_inline_first_child_stops_before_same_line_neighbour(self):
        proof = prove_child(self.source, self.unit, 7)
        self.assertEqual(proof['status'], 'proven', proof)
        self.assertEqual(proof['text'], '7.T　\tt\t(　　　)')
        self.assertFalse(quote_belongs_to_child(self.source, self.unit, 7, '8.N　\tn'))
        self.assertFalse(quote_belongs_to_child_loose(self.source, self.unit, 7, '8.N n'))

    def test_neighbour_cannot_return_as_shared_material(self):
        for quote in ('7.T　\tt\t(　　　)', '1.U　v (　　　)', '8.N　\tn\t(　　　)'):
            with self.subTest(quote=quote):
                self.assertFalse(quote_belongs_to_unit_material(self.source, self.unit, quote))
                if quote.startswith('7.'):
                    self.assertFalse(quote_belongs_to_child(self.source, self.unit, 8, quote))

    def test_same_quote_and_numbers_in_other_section_are_scope_local(self):
        source = self.source + '\n' + self.heading + '\n' + self.stem + '\n'
        unit = self.scoped(source, self.stem, 2, 6, list(range(1, 9)))
        self.assertEqual(prove_child(source, unit, 8)['status'], 'proven')
        self.assertTrue(quote_belongs_to_child(source, unit, 8, '8.N　\tn\t(　　　)'))
        self.assertTrue(quote_belongs_to_child_loose(source, unit, 8, '8.N n ( )'))

    def test_duplicate_target_number_in_scope_rejects_even_narrow_span(self):
        source = self.source.replace('\n\nⅡ.', '\n8.Other stem\n\nⅡ.')
        unit = self.scoped(source, self.stem, 2, 5, list(range(1, 9)))
        self.assertEqual(prove_child(source, unit, 8)['status'], 'uncertain')
        self.assertFalse(quote_belongs_to_child(source, unit, 8, '8.N　\tn'))

    def test_duplicate_stem_in_scope_rejects(self):
        source = self.source.replace('\n\nⅡ.', '\n' + self.stem + '\n\nⅡ.')
        unit = self.scoped(source, self.stem, 2, 5, list(range(1, 9)))
        self.assertEqual(prove_child(source, unit, 8)['status'], 'uncertain')

    def test_duplicate_quote_in_scope_rejects_exact_and_loose(self):
        stem = '1.Same text\t2.Same text'
        source = self.heading + '\n' + stem
        unit = self.scoped(source, stem, 2, 2, [1, 2])
        self.assertEqual(prove_child(source, unit, 2)['status'], 'proven')
        self.assertFalse(quote_belongs_to_child(source, unit, 2, 'Same text'))
        self.assertFalse(quote_belongs_to_child_loose(source, unit, 2, 'Same text'))

    def test_stale_out_of_bounds_and_cross_section_span_reject(self):
        for span in ({'start_line': 3, 'end_line': 6},
                     {'start_line': 2, 'end_line': 999},
                     {'start_line': 2, 'end_line': 8},
                     {'start_line': True, 'end_line': 6},
                     {'start_line': 6, 'end_line': 2}):
            unit = deepcopy(self.unit)
            unit['source_span'].update(span)
            with self.subTest(span=span):
                self.assertEqual(prove_child(self.source, unit, 8)['status'], 'uncertain')
                self.assertFalse(quote_belongs_to_child_loose(self.source, unit, 8, '8.N n'))

    def test_forged_section_identity_fields_reject(self):
        for field, value in (('title', 'Ⅱ.语音题'), ('context', 'forged'),
                             ('ordinal', 'Ⅱ.'), ('occurrence', 2),
                             ('start_line', 2), ('end_line', 8)):
            unit = deepcopy(self.unit)
            unit['source_section'][field] = value
            with self.subTest(field=field):
                self.assertEqual(prove_child(self.source, unit, 8)['status'], 'uncertain')
                self.assertFalse(quote_belongs_to_unit_material(self.source, unit, '7.T'))

    def test_changed_source_or_stem_rejects(self):
        self.assertEqual(prove_child(self.source.replace('8.N', '8.M'), self.unit, 8)['status'], 'uncertain')
        unit = deepcopy(self.unit)
        unit['stem'] = unit['stem'].replace('8.N', '8.N ')
        self.assertEqual(prove_child(self.source, unit, 8)['status'], 'uncertain')
        unit = deepcopy(self.unit)
        unit['source_span']['text_sha256'] = hashlib.sha256(self.source.encode()).hexdigest()
        self.assertEqual(prove_child(self.source + '\nchanged', unit, 8)['status'], 'uncertain')

    def test_missing_scope_and_unsafe_source_span_reject(self):
        for key in ('source_section', 'source_span', 'section_id', 'question_ids'):
            unit = deepcopy(self.unit)
            del unit[key]
            with self.subTest(key=key):
                self.assertEqual(prove_child(self.source, unit, 8)['status'], 'uncertain')
        for field, value in (('basis', 'original_pdf'), ('path', 'other.txt'),
                             ('source_path', 'other.txt'), ('source_sha256', 'stale')):
            unit = deepcopy(self.unit)
            unit['source_span'][field] = value
            with self.subTest(field=field):
                self.assertEqual(prove_child(self.source, unit, 8)['status'], 'uncertain')

    def test_scoped_shared_prefix_only_and_duplicate_other_scope_allowed(self):
        heading = 'Ⅰ.阅读理解'
        stem = 'Shared passage.\n1.First?\nA. one\n2.Second?\nA. two'
        source = heading + '\n' + stem + '\nⅡ.阅读理解\n' + stem
        unit = self.scoped(source, stem, 2, 6, [1, 2])
        self.assertTrue(quote_belongs_to_unit_material(source, unit, 'Shared passage.'))
        self.assertFalse(quote_belongs_to_unit_material(source, unit, 'First?'))
        self.assertFalse(quote_belongs_to_unit_material(source, unit, 'A. two'))

    def test_cleaned_to_raw_mapping_preserves_crlf_character_offsets(self):
        source = '2025年上海市中考\r\n' + self.source.replace('\n', '\r\n')
        proof = prove_child(source, self.unit, 8)
        self.assertEqual(proof['status'], 'proven', proof)
        self.assertEqual(source[proof['start']:proof['end']], '8.N　\tn\t(　　　)')

    def test_scoped_numbered_blanks_retain_local_dialogue_and_table_evidence(self):
        cases = [('Ⅰ.补全对话', 'Li Lei:I am going home.\nBruce:<u>　66　</u>\n'
                  'See you.\nBruce:<u>　68　</u>', [66, 68], 66, 'Li Lei:I am going home.', 'See you.'),
                 ('Ⅰ.补全对话', 'Who | Class Eight,Grade<u>　26　</u>\nWhen | Last<u>　27　</u>',
                  [26, 27], 26, 'Class Eight', 'Last'),
                 ('Ⅰ.补全对话', 'Li Lei:I am going home.\nBruce:__66__\nBruce:__68__',
                  [66, 68], 66, 'Li Lei:I am going home.', 'Bruce:__68__')]
        for heading, stem, numbers, number, own, neighbour in cases:
            source = heading + '\n' + stem
            # Blank-only groups already have explicit confirmed identities;
            # identity assignment itself requires a printed anchor, so construct
            # that existing protocol directly rather than inventing a number.
            unit = {'identity_version': 'scoped-v1', 'unit_id': 'blank-group', 'section_id': 'blank-section',
                    'numbers': numbers, 'stem': stem,
                    'question_ids': {str(n): f'blank-q{n}' for n in numbers},
                    'source_span': {'basis': 'cleaned_source', 'start_line': 2,
                                    'end_line': len(source.splitlines())},
                    'source_section': {'title': heading, 'ordinal': 'Ⅰ.', 'context': '',
                                       'occurrence': 1, 'start_line': 1, 'end_line': len(source.splitlines())}}
            with self.subTest(heading=heading, stem=stem):
                proof = prove_child(source, unit, number)
                self.assertEqual(proof['status'], 'proven', proof)
                self.assertEqual(proof['method'], 'scoped-numbered-blank-marker/v1')
                self.assertTrue(quote_belongs_to_child(source, unit, number, own))
                self.assertFalse(quote_belongs_to_child(source, unit, number, neighbour))
                self.assertFalse(quote_belongs_to_child_loose(source, unit, number, neighbour))
                duplicate_source = source + f'\n{number}.Other printed target'
                duplicate_unit = deepcopy(unit)
                duplicate_unit['source_section']['end_line'] += 1
                self.assertEqual(prove_child(duplicate_source, duplicate_unit, number)['status'], 'uncertain')
                unit['source_section']['end_line'] += 1
                self.assertEqual(prove_child(source, unit, number)['status'], 'uncertain')

    def test_no_scoped_identity_keeps_legacy_inline_rejection(self):
        unit = {'numbers': list(range(1, 9)), 'stem': self.stem}
        self.assertEqual(prove_child(self.source, unit, 8)['status'], 'uncertain')

    def test_lesson_container_clip_keeps_band_image_outside_the_section(self):
        """A lesson band ends before the next heading, so the band's trailing
        image placeholder (and its blank spacer) is counted by the coarse heading
        parser as the previous section's last line. That explained clip must keep
        proving; a tail holding real content still must not."""
        source = ("第三课时　Section B (1a-1d)\nⅣ.单项填空\n"
                  "(　　)3.—Thank you very much for the flowers,Helen!\n"
                  "A.Yes,please B.No,thanks C.You are welcome D.That's right\n"
                  "\n[IMG image4.jpeg]\nⅠ.阅读理解\nPassage text\n")
        stem = ("(　　)3.—Thank you very much for the flowers,Helen!\n"
                "A.Yes,please B.No,thanks C.You are welcome D.That's right")
        unit = self.scoped(source, stem, 3, 4, [3])
        self.assertEqual(unit['source_section']['end_line'], 6)
        clipped = deepcopy(unit)
        clipped['source_section'].update({'end_line': 5, 'lesson_container_end_line': 5})
        proof = prove_child(source, clipped, 3)
        self.assertEqual(proof['status'], 'proven', proof)
        self.assertIn('Thank you very much for the flowers', proof['text'])
        self.assertTrue(quote_belongs_to_child(source, clipped, 3, '—Thank you very much'))
        # An unexplained clip, a non-container boundary and a content tail reject.
        for patch in ({'lesson_container_end_line': 6}, {'lesson_container_end_line': None}):
            forged = deepcopy(clipped)
            forged['source_section'].update(patch)
            with self.subTest(patch=patch):
                self.assertEqual(prove_child(source, forged, 3)['status'], 'uncertain')
        content_tail = source.replace('\n\n[IMG image4.jpeg]', '\n\nC.extra content line\n[IMG image4.jpeg]')
        self.assertEqual(prove_child(content_tail, clipped, 3)['status'], 'uncertain')

    def test_strict_end_off_replays_the_pre_clip_projection(self):
        """審核绑定必须能重放修复前的投影：同一单元在旧口径下定位不到窗口，
        新口径能定位。差异只影响绑定摘要，不重写任何任务/响应/计划。"""
        from workflow import review_rounds
        from workflow.review_child_source import _raw_unit_lines
        source = ("第三课时　Section B (1a-1d)\nⅣ.单项填空\n"
                  "(　　)3.—Thank you very much for the flowers,Helen!\n"
                  "A.Yes,please B.No,thanks C.You are welcome D.That's right\n"
                  "\n[IMG image4.jpeg]\nⅠ.阅读理解\nPassage text\n")
        stem = ("(　　)3.—Thank you very much for the flowers,Helen!\n"
                "A.Yes,please B.No,thanks C.You are welcome D.That's right")
        clipped = self.scoped(source, stem, 3, 4, [3])
        clipped['source_section'].update({'end_line': 5, 'lesson_container_end_line': 5})
        self.assertEqual(_raw_unit_lines(source, clipped)[0], 2)
        self.assertIsNone(_raw_unit_lines(source, clipped, strict_end=False))
        current = review_rounds._source_excerpts(source, [clipped], force=True, adjacent_units=[clipped])
        legacy = review_rounds._source_excerpts(source, [clipped], force=True,
                                                adjacent_units=[clipped], strict_end=False)
        self.assertIsInstance(current, dict)
        self.assertEqual(legacy, source)


if __name__ == '__main__':
    unittest.main()
