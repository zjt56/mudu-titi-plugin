"""Compact lesson keys require a complete ordered section proof and true coordinates."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from workflow.question_identity import assign_scoped
from workflow.upload_content import resolve_scoped_answer, validate_scoped_answer, validate_scoped_missing


class CompactLessonAnswersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        (self.batch / '01_source').mkdir()
        self.body = ('第一课时 Section A (1a-2d)\nⅠ.单项填空\n1. First?\n2. Second?\n'
                     'Ⅱ.根据首字母及汉语提示补全单词\n1. Spell?\n2. Spell again?\n'
                     'Ⅰ.单项填空\n1. Extension first?\n2. Extension second?\n')
        self.appendix = '参考答案\nⅠ.1~2 AB\nⅡ.1.nice　2.Unit\nⅠ.1.C　2.D\n'
        self.units = assign_scoped([
            {'numbers': [1, 2], 'unit_kind': 'group', 'type': '单项填空',
             'source_span': {'start_line': 3, 'end_line': 4}},
            {'numbers': [1, 2], 'unit_kind': 'group', 'type': '单词拼写',
             'source_span': {'start_line': 6, 'end_line': 7}},
            {'numbers': [1, 2], 'unit_kind': 'group', 'type': '单项填空',
             'source_span': {'start_line': 9, 'end_line': 10}},
        ], self.body)
        self.write_sources()

    def write_sources(self):
        (self.batch / '01_source/正文.txt').write_text(self.body, encoding='utf-8')
        (self.batch / '01_source/附录.txt').write_text(self.appendix, encoding='utf-8')
        (self.batch / '01_source/structure.confirmed.json').write_text(
            json.dumps({'identity_version': 'scoped-v1', 'units': self.units}), encoding='utf-8')

    def assert_unbound(self, unit=0, number=1, answer='A'):
        self.assertIsNone(resolve_scoped_answer(self.batch, self.units[unit], number, answer))
        with self.assertRaisesRegex(ValueError, 'scoped answer source'):
            validate_scoped_answer(self.batch, self.units[unit], number, answer, ['附录:2'])

    def test_complete_repeated_roman_skeleton_binds_by_section_coordinates(self):
        for i, n, value, line in ((0, 1, 'A', 2), (0, 2, 'B', 2), (1, 1, 'nice', 3),
                                  (1, 2, 'Unit', 3), (2, 1, 'C', 4), (2, 2, 'D', 4)):
            with self.subTest(unit=i, number=n):
                result = resolve_scoped_answer(self.batch, self.units[i], n, value)
                self.assertEqual(result, {'answer': value, 'evidence': [f'01_source/附录.txt:{line}']})
                validate_scoped_answer(self.batch, self.units[i], n, value, result['evidence'])
        self.assert_unbound(0, 1, 'C')

    def test_coordinates_and_retained_value_remain_mandatory(self):
        for value, evidence in (('A', []), ('A', ['附录:4']), ('C', ['附录:2'])):
            with self.subTest(value=value, evidence=evidence), self.assertRaises(ValueError):
                validate_scoped_answer(self.batch, self.units[0], 1, value, evidence)
        self.assertIsNone(resolve_scoped_answer(self.batch, self.units[0], 1, 'C'))

    def test_inline_items_preserve_semicolons_decimals_and_continuation_coordinates(self):
        self.appendix = 'Ⅰ.1~2 AB\nⅡ.1.How do;greet\n2. 3.14\nⅠ.1.C　2.D\n'
        self.write_sources()
        for n, value, line in ((1, 'How do;greet', 2), (2, '3.14', 3)):
            result = resolve_scoped_answer(self.batch, self.units[1], n)
            self.assertEqual(result['answer'], value)
            self.assertEqual(result['evidence'], [f'01_source/附录.txt:{line}'])

    def test_missing_extra_or_reordered_section_cannot_borrow_global_numbers(self):
        for appendix in ('Ⅰ.1.A\nⅡ.1.nice\n', 'Ⅰ.1.A\nⅡ.1.nice\nⅠ.1.C\nⅠ.1.A\n',
                         'Ⅱ.1.nice\nⅠ.1.A\nⅠ.1.C\n', '1.A\n1.nice\n1.C\n'):
            with self.subTest(appendix=appendix):
                self.appendix = appendix
                self.write_sources()
                self.assert_unbound()
                with self.assertRaisesRegex(ValueError, 'scope unbound'):
                    validate_scoped_missing(self.batch, self.units[0], 1)

    def test_two_lessons_or_wrong_explicit_context_reject(self):
        self.appendix = '第二课时 Section A (1a-2d)\n' + self.appendix
        self.write_sources()
        self.assert_unbound()
        self.appendix = 'Ⅰ.1.A\nⅡ.1.nice\nⅠ.1.C\n'
        self.body = self.body.replace('Ⅰ.单项填空\n1. Extension', '第二课时 Section A (1a-2d)\nⅠ.单项填空\n1. Extension')
        self.write_sources()
        self.assert_unbound()

    def test_drifted_body_identity_or_coordinate_reject(self):
        for key, value in (('occurrence', 9), ('title', 'Ⅰ.阅读理解'), ('start_line', 1)):
            unit = copy.deepcopy(self.units[0])
            unit['source_section'][key] = value
            self.assertIsNone(resolve_scoped_answer(self.batch, unit, 1))
        unit = copy.deepcopy(self.units[0])
        unit['source_span']['start_line'] = 9
        self.assertIsNone(resolve_scoped_answer(self.batch, unit, 1))

    def test_duplicate_compact_numbers_reject_even_equal_values_and_block_missing(self):
        self.appendix = 'Ⅰ.1.A\n1.A\nⅡ.1.nice　2.Unit\nⅠ.1.C　2.D\n'
        self.write_sources()
        self.assert_unbound()
        with self.assertRaisesRegex(ValueError, 'scope unbound'):
            validate_scoped_missing(self.batch, self.units[0], 1)

    def test_conflicting_authorities_reject_answers_and_missing(self):
        other = self.batch / '01_source_ans'
        other.mkdir()
        (other / '正文.txt').write_text(self.appendix.replace('AB', 'CB'), encoding='utf-8')
        self.assert_unbound()
        with self.assertRaisesRegex(ValueError, 'missing contradicts'):
            validate_scoped_missing(self.batch, self.units[0], 1)

    def test_missing_present_compact_answer_rejects(self):
        with self.assertRaisesRegex(ValueError, 'missing contradicts'):
            validate_scoped_missing(self.batch, self.units[1], 2)
        self.appendix = self.appendix.replace('　2.Unit', '')
        self.write_sources()
        validate_scoped_missing(self.batch, self.units[1], 2)

    def test_equal_authorities_are_content_equivalent_with_real_coordinates(self):
        other = self.batch / '01_source_ans'
        other.mkdir()
        (other / '正文.txt').write_text(self.appendix, encoding='utf-8')
        result = resolve_scoped_answer(self.batch, self.units[0], 1)
        self.assertEqual(len(result['evidence']), 2)
        validate_scoped_answer(self.batch, self.units[0], 1, 'A', result['evidence'])

    def test_pdf_fallback_cannot_override_word_and_reference_copy_does_not_compete(self):
        materials = self.batch / '01_source/materials'
        materials.mkdir()
        (materials / 'attachment_001.txt').write_text(self.appendix.replace('AB', 'CB'), encoding='utf-8')
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[0], 1)['answer'], 'A')
        self.assertIsNone(resolve_scoped_answer(self.batch, self.units[0], 1, 'C'))
        (self.batch / '01_source/附录.txt').unlink()
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[0], 1)['answer'], 'C')
        (self.batch / '01_source/material-roles.json').write_text(json.dumps({'documents': [
            {'text_path': '01_source/materials/attachment_001.txt', 'content_authority': False}]}), encoding='utf-8')
        self.assertIsNone(resolve_scoped_answer(self.batch, self.units[0], 1))

    def test_unrecognized_roman_body_heading_and_same_label_two_lessons_reject(self):
        self.body += 'Ⅲ.无法识别的说明\n1. Extra?\n'
        self.write_sources()
        self.assert_unbound()
        self.body = self.body.replace('Ⅲ.无法识别的说明\n1. Extra?\n', '')
        self.body = self.body.replace('Ⅰ.单项填空\n1. Extension',
                                      '第一课时 Section A (1a-2d)\nⅠ.单项填空\n1. Extension')
        self.write_sources()
        self.assert_unbound()

    def test_malformed_continuation_cannot_hide_conflicting_duplicate(self):
        self.appendix = self.appendix.replace('Ⅱ.1.nice　2.Unit', 'Ⅱ.1.nice　2.Unit\n1.bad　1.other')
        self.write_sources()
        self.assert_unbound(1, 1, 'nice')
        with self.assertRaisesRegex(ValueError, 'scope unbound'):
            validate_scoped_missing(self.batch, self.units[1], 1)

    def test_unnumbered_continuation_declines_whole_compact_block(self):
        from workflow import answers_direct
        for continuation in ('Test', '2. hidden next answer', '[IMG answer.png]',
                             '[IMG answer.png]能力提升[IMG decoration.png]extra',
                             '[IMG answer.png]答案[IMG decoration.png]'):
            with self.subTest(continuation=continuation):
                self.appendix = ('Ⅰ.1~2 AB\nⅡ.1.nice　2.Unit\n' + continuation
                                 + '\nⅠ.1.C　2.D\n')
                self.write_sources()
                self.assert_unbound(1, 1, 'nice')
                self.assertIsNone(resolve_scoped_answer(self.batch, self.units[1], 2))
                self.assertIsNone(answers_direct.build_payload(self.batch))

    def test_complete_lesson_band_banners_end_blocks_without_losing_answers(self):
        from workflow import answers_direct
        self.appendix = ('[IMG image158.jpeg]基础巩固[IMG image159.jpeg]\n'
                         'Ⅰ.1~2 AB\nⅡ.1.nice　2.Unit\n'
                         '[IMG image158.jpeg]能力提升[IMG image159.jpeg]\nⅠ.1.C　2.D\n')
        self.write_sources()
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[1], 2)['answer'], 'Unit')
        self.assertEqual(len(answers_direct.build_payload(self.batch)['answers']), 6)

    def test_malformed_range_and_inline_duplicate_do_not_prove_answer(self):
        for replacement in ('Ⅰ.1~2 A', 'Ⅰ.1.A　1.B'):
            self.appendix = 'Ⅰ.1~2 AB\nⅡ.1.nice　2.Unit\nⅠ.1.C　2.D\n'.replace('Ⅰ.1~2 AB', replacement)
            self.write_sources()
            self.assert_unbound()


if __name__ == '__main__':
    unittest.main()
