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

    def test_matching_unit_and_lesson_headers_bind_but_other_unit_rejects(self):
        self.body = 'Unit 1\n' + self.body
        for unit in self.units:
            unit['source_span']['start_line'] += 1
            unit['source_span']['end_line'] += 1
        self.units = assign_scoped(self.units, self.body)
        self.appendix = 'Unit 1\n第一课时 Section A (1a-2d)\n' + self.appendix
        self.write_sources()
        result = resolve_scoped_answer(self.batch, self.units[0], 1, 'A')
        self.assertEqual(result['answer'], 'A')
        self.appendix = self.appendix.replace('Unit 1', 'Unit 2')
        self.write_sources()
        self.assert_unbound()

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


class TestPointBannerAnswersTest(unittest.TestCase):
    """重要考点集训 / 创新阅读专练 banners + multi-line task-reading key.

    Mirrors the real Starter Unit 1 课时4 Project appendix: foundation container
    banner 重要考点集训, reading container banner 创新阅读专练, a (n)-subblank
    task-reading key spanning several lines, and a ☆拓展设问 sub-heading that must
    not split or poison the trailing block.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        (self.batch / '01_source').mkdir()
        self.body = (
            '第四课时　Project\n'
            'Ⅰ.根据句意用所给词的适当形式填空\n'
            '1.—______ you Lily?\n2.Nice ______(meet) you.\n3.—How are you?\n'
            'Ⅱ.单项填空\n'
            '(　)1.First?\n(　)2.Second?\n'
            '任务型阅读\n'
            'Read.\n'
            '1. Sort the words.\n2. Where is Jimmy from?\n'
            '3. Which side?\n☆拓展设问\n'
            '(　)4. Jimmy can’t use ______ .\n'
        )
        self.appendix = (
            '参考答案\n'
            '[IMG image158.jpeg]重要考点集训[IMG image159.jpeg]\n'
            'Ⅰ.1.Are;am　2.to meet　3.thanks\n'
            'Ⅱ.1~2 AB\n'
            '[IMG image158.jpeg]创新阅读专练[IMG image159.jpeg]\n'
            '1.(1)British英国的　(2)chopsticks筷子\n'
            '(3)different不同的\n'
            '2.He is from the UK.\n3.The right side.\n☆拓展设问\n4.A\n'
        )
        (self.batch / '01_source/正文.txt').write_text(self.body, encoding='utf-8')
        (self.batch / '01_source/附录.txt').write_text(self.appendix, encoding='utf-8')
        # Unit identities derived from the real body coordinates.
        from workflow.question_identity import assign_scoped
        lines = self.body.splitlines()

        def line(text):
            return lines.index(text) + 1

        raw = []
        for n in (1, 2):  # Ⅰ items 1-2 (item 2 spans two body lines; keep single-line for test)
            pass
        raw.append({'numbers': [1, 2, 3], 'unit_kind': 'group', 'type': '单词拼写',
                    'source_span': {'start_line': line('1.—______ you Lily?'),
                                    'end_line': line('Ⅱ.单项填空') - 1}})
        raw.append({'numbers': [1, 2], 'unit_kind': 'group', 'type': '单项填空',
                    'source_span': {'start_line': line('(　)1.First?'),
                                    'end_line': line('任务型阅读') - 1}})
        raw.append({'numbers': [1, 2, 3, 4], 'unit_kind': 'group', 'type': '任务型阅读',
                    'source_span': {'start_line': line('Read.'), 'end_line': len(lines)}})
        self.units = assign_scoped(raw, self.body)
        (self.batch / '01_source/structure.confirmed.json').write_text(
            json.dumps({'identity_version': 'scoped-v1', 'units': self.units}), encoding='utf-8')

    def test_point_and_reading_banners_bind_every_answer(self):
        from workflow import answers_direct
        # Ⅰ.1-2 (unit 0), Ⅱ.1-2 (unit 1), 任务型阅读 1-4 (unit 2)
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[0], 1)['answer'], 'Are;am')
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[0], 2)['answer'], 'to meet')
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[1], 1)['answer'], 'A')
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[1], 2)['answer'], 'B')
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[2], 2)['answer'], 'He is from the UK.')
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[2], 3)['answer'], 'The right side.')
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[2], 4)['answer'], 'A')
        payload = answers_direct.build_payload(self.batch)
        self.assertIsNotNone(payload)
        self.assertEqual(len(payload["answers"]), 9)

    def test_cleaned_question_span_projects_when_section_heading_stays_raw(self):
        from workflow import denoise, upload_content
        raw = ['Unit 1', '第一课时 Project', 'Ⅰ.单项填空', '1. First?',
               '--------------------', '2. Second?', '--------------------',
               'Ⅱ.阅读理解', '1. Again?', '--------------------']
        _raw_to_clean, clean_to_raw = denoise.deletion_map(raw)
        self.assertTrue(clean_to_raw)
        sections = [
            {'title': 'Ⅰ.单项填空', 'ordinal': 'Ⅰ.', 'context': '第一课时 Project',
             'occurrence': 1, 'start_line': 3, 'end_line': 6},
            {'title': 'Ⅱ.阅读理解', 'ordinal': 'Ⅱ.', 'context': '第一课时 Project',
             'occurrence': 2, 'start_line': 7, 'end_line': 8},
        ]
        unit = {'identity_version': 'scoped-v1', 'numbers': [2],
                'source_span': {'start_line': 5, 'end_line': 5, 'basis': 'cleaned_source'},
                'source_section': sections[0]}
        projected = upload_content._project_unit_to_body_file(unit, clean_to_raw, raw)
        self.assertEqual(projected['source_span']['start_line'], 6)
        self.assertEqual(projected['source_span']['end_line'], 6)
        source_lines = {'01_source/正文.txt': raw}
        self.assertTrue(upload_content.scoped_source_matches(
            projected, 2, '01_source/正文.txt', 6, source_lines, _file_coordinates=True))
        self.assertFalse(upload_content.scoped_source_matches(
            projected, 1, '01_source/正文.txt', 6, source_lines, _file_coordinates=True))

    def test_duplicate_point_banner_stays_unbound(self):
        self.appendix = self.appendix.replace(
            '[IMG image158.jpeg]重要考点集训[IMG image159.jpeg]',
            '[IMG image158.jpeg]重要考点集训[IMG image159.jpeg]\n[IMG image158.jpeg]重要考点集训[IMG image159.jpeg]')
        (self.batch / '01_source/附录.txt').write_text(self.appendix, encoding='utf-8')
        self.assertIsNone(resolve_scoped_answer(self.batch, self.units[0], 1, 'Are;am'))


class WholeBookCompanionWindowTest(unittest.TestCase):
    """A whole-book answer key must be cut to the body's own lesson before proving.

    Starter unit lesson titles repeat (`第二课时 Pronunciation` appears under
    Starter Unit 1/2/3), so the enclosing unit named in the original source file
    name is the only allowed discriminator. Without it the lesson stays unbound.
    """

    BODY = ('第一课时 Section A (1a-2d)\nⅠ.单项填空\n1. First?\n2. Second?\n'
            'Ⅱ.根据首字母及汉语提示补全单词\n1. Spell?\n2. Spell again?\n'
            'Ⅰ.单项填空\n1. Extension first?\n2. Extension second?\n')
    APPENDIX = ('Starter Unit 1　Hello!\n第一课时 Section A (1a-2d)\nⅠ.1~2 AB\nⅡ.1.nice　2.Unit\nⅠ.1.C　2.D\n'
                'Starter Unit 2　Keep Tidy!\n第一课时 Section A (1a-2d)\nⅠ.1~2 CD\nⅡ.1.other　2.word\nⅠ.1.X　2.Y\n')

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.batch = Path(tmp.name)
        (self.batch / '01_source').mkdir()
        (self.batch / 'workflow').mkdir()
        self.units = assign_scoped([
            {'numbers': [1, 2], 'unit_kind': 'group', 'type': '单项填空',
             'source_span': {'start_line': 3, 'end_line': 4}},
            {'numbers': [1, 2], 'unit_kind': 'group', 'type': '单词拼写',
             'source_span': {'start_line': 6, 'end_line': 7}},
            {'numbers': [1, 2], 'unit_kind': 'group', 'type': '单项填空',
             'source_span': {'start_line': 9, 'end_line': 10}},
        ], self.BODY)
        self.save()

    def save(self, source_name='七上 人教版 课时A计划 Starter Unit 1 Hello! 第一课时 Section A.pdf'):
        (self.batch / '01_source/正文.txt').write_text(self.BODY, encoding='utf-8')
        (self.batch / '01_source/附录.txt').write_text(self.APPENDIX, encoding='utf-8')
        (self.batch / '01_source/structure.confirmed.json').write_text(
            json.dumps({'identity_version': 'scoped-v1', 'units': self.units}), encoding='utf-8')
        manifest = {'inputs': {'source': {'original_filename': source_name}}} if source_name else {'inputs': {}}
        (self.batch / 'workflow/manifest.json').write_text(json.dumps(manifest), encoding='utf-8')

    def test_source_file_unit_selects_the_matching_lesson_block(self):
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[0], 1),
                         {'answer': 'A', 'evidence': ['01_source/附录.txt:3']})
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[0], 2)['answer'], 'B')
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[1], 1)['answer'], 'nice')
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[2], 1)['answer'], 'C')

    def test_repeated_lesson_without_source_unit_identity_stays_unbound(self):
        self.save(source_name=None)
        for unit in self.units:
            self.assertIsNone(resolve_scoped_answer(self.batch, unit, 1))
        self.save(source_name='26秋课时七上英语RJ答案.docx')  # answer file name proves nothing
        for unit in self.units:
            self.assertIsNone(resolve_scoped_answer(self.batch, unit, 1))

    def test_other_unit_values_never_bind(self):
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[0], 1)['answer'], 'A')
        self.assertIsNone(resolve_scoped_answer(self.batch, self.units[0], 1, 'C'))
        self.assertIsNone(resolve_scoped_answer(self.batch, self.units[1], 1, 'other'))

    def test_companion_unit_conflicting_with_body_unit_rejects(self):
        body = 'Unit 2\n' + self.BODY
        self.units = assign_scoped([
            {'numbers': [1, 2], 'unit_kind': 'group', 'type': '单项填空',
             'source_span': {'start_line': 4, 'end_line': 5}},
            {'numbers': [1, 2], 'unit_kind': 'group', 'type': '单词拼写',
             'source_span': {'start_line': 7, 'end_line': 8}},
            {'numbers': [1, 2], 'unit_kind': 'group', 'type': '单项填空',
             'source_span': {'start_line': 10, 'end_line': 11}},
        ], body)
        (self.batch / '01_source/正文.txt').write_text(body, encoding='utf-8')
        (self.batch / '01_source/structure.confirmed.json').write_text(
            json.dumps({'identity_version': 'scoped-v1', 'units': self.units}), encoding='utf-8')
        self.save(source_name='七上 人教版 Starter Unit 1 Hello!.pdf')
        (self.batch / '01_source/正文.txt').write_text(body, encoding='utf-8')
        self.assertIsNone(resolve_scoped_answer(self.batch, self.units[0], 1))


class TrailingSubColumnAnswerTest(unittest.TestCase):
    """Appendix ☆熟词生义 sub-column must not poison preceding Roman answer blocks."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.batch = Path(self.tmp.name)
        (self.batch / '01_source').mkdir(parents=True)
        self.body = (
            'Unit 1 The Changing World\n'
            '第二课时　Section A (3a-3d)\n'
            '**【一、A 基础巩固】**\n'
            '**Ⅰ. 根据句意用所给词的适当形式填空**\n'
            '**1.**Many ________(railway) pass through here.\n'
            '**2.**His way interests us ________(great).\n'
            '**Ⅱ. 单项填空**\n'
            '**1.**I want to know your ________.\n'
            'A.secret B.opinion\n'
            '**【二、B 能力提升】**\n'
            '**Ⅰ. 完形填空**\n'
            'He was sent to China as a sports __<u>1</u>__.\n'
            '**1.**A.doctor B.player C.reporter D.fan\n'
            '**Ⅱ. 阅读理解**\n'
            'Some passage.\n'
            '**1.**When was it?\n'
            'A.In 1805. B.In 1825.\n'
            '**【三、熟词生义】**\n'
            '**写出下面画线词的汉语意思**\n'
            'In 2014,he was sent to China as a sports __<u>1</u>__ to <u>cover</u> the games.\t_________\n'
        )
        self.appendix = (
            '****参考答案**\n'
            '**[IMG image85.jpg]****一、基础巩固****[IMG image86.jpg]**\n'
            'Ⅰ.1.railways　2.greatly\n'
            'Ⅱ.1.B\n'
            '**[IMG image85.jpg]****二、能力提升****[IMG image86.jpg]**\n'
            'Ⅰ.1.C\n'
            'Ⅱ.1.B\n'
            '**☆****熟词生义**\n'
            '报道\n'
        )
        (self.batch / '01_source/正文.txt').write_text(self.body, encoding='utf-8')
        (self.batch / '01_source/附录.txt').write_text(self.appendix, encoding='utf-8')
        self.units = assign_scoped([
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '单词拼写',
             'source_span': {'start_line': 5, 'end_line': 5, 'basis': 'cleaned_source'}},
            {'numbers': [2], 'unit_kind': 'standalone', 'type': '单词拼写',
             'source_span': {'start_line': 6, 'end_line': 6, 'basis': 'cleaned_source'}},
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '单项填空',
             'source_span': {'start_line': 8, 'end_line': 9, 'basis': 'cleaned_source'}},
            {'numbers': [1], 'unit_kind': 'group', 'type': '完形填空',
             'source_span': {'start_line': 12, 'end_line': 13, 'basis': 'cleaned_source'}},
            {'numbers': [1], 'unit_kind': 'group', 'type': '阅读理解',
             'source_span': {'start_line': 15, 'end_line': 17, 'basis': 'cleaned_source'}},
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '翻译',
             'source_span': {'start_line': 20, 'end_line': 20, 'basis': 'cleaned_source',
                             'number_binding': 'unnumbered_section', 'printed_numbers': []}},
        ], self.body)
        (self.batch / '01_source/structure.confirmed.json').write_text(
            json.dumps({'identity_version': 'scoped-v1', 'units': self.units}), encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def test_trailing_word_meaning_sub_column_binds_all_sections_including_itself(self):
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[0], 1)['answer'], 'railways')
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[1], 2)['answer'], 'greatly')
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[2], 1)['answer'], 'B')
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[3], 1)['answer'], 'C')
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[4], 1)['answer'], 'B')
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[5], 1),
                         {'answer': '报道', 'evidence': ['01_source/附录.txt:9']})

    def test_trailing_word_meaning_in_appendix_alone_does_not_poison_roman_blocks(self):
        body_without_tail = '\n'.join(self.body.splitlines()[:-3]) + '\n'
        units = assign_scoped([
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '单词拼写',
             'source_span': {'start_line': 5, 'end_line': 5, 'basis': 'cleaned_source'}},
            {'numbers': [2], 'unit_kind': 'standalone', 'type': '单词拼写',
             'source_span': {'start_line': 6, 'end_line': 6, 'basis': 'cleaned_source'}},
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '单项填空',
             'source_span': {'start_line': 8, 'end_line': 9, 'basis': 'cleaned_source'}},
            {'numbers': [1], 'unit_kind': 'group', 'type': '完形填空',
             'source_span': {'start_line': 12, 'end_line': 13, 'basis': 'cleaned_source'}},
            {'numbers': [1], 'unit_kind': 'group', 'type': '阅读理解',
             'source_span': {'start_line': 15, 'end_line': 17, 'basis': 'cleaned_source'}},
        ], body_without_tail)
        (self.batch / '01_source/正文.txt').write_text(body_without_tail, encoding='utf-8')
        (self.batch / '01_source/structure.confirmed.json').write_text(
            json.dumps({'identity_version': 'scoped-v1', 'units': units}), encoding='utf-8')
        self.assertEqual(resolve_scoped_answer(self.batch, units[0], 1)['answer'], 'railways')
        self.assertEqual(resolve_scoped_answer(self.batch, units[4], 1)['answer'], 'B')

    def test_arbitrary_named_sub_column_literal_and_numbered_answers_bind_generically(self):
        # 1) 任意新栏目名（无号文字答案）：☆词义猜测
        body_lit = self.body.replace('【三、熟词生义】', '【三、词义猜测】')
        app_lit = self.appendix.replace('**☆****熟词生义**', '**☆****词义猜测**')
        units_lit = assign_scoped([
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '单词拼写',
             'source_span': {'start_line': 5, 'end_line': 5, 'basis': 'cleaned_source'}},
            {'numbers': [2], 'unit_kind': 'standalone', 'type': '单词拼写',
             'source_span': {'start_line': 6, 'end_line': 6, 'basis': 'cleaned_source'}},
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '单项填空',
             'source_span': {'start_line': 8, 'end_line': 9, 'basis': 'cleaned_source'}},
            {'numbers': [1], 'unit_kind': 'group', 'type': '完形填空',
             'source_span': {'start_line': 12, 'end_line': 13, 'basis': 'cleaned_source'}},
            {'numbers': [1], 'unit_kind': 'group', 'type': '阅读理解',
             'source_span': {'start_line': 15, 'end_line': 17, 'basis': 'cleaned_source'}},
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '翻译',
             'source_span': {'start_line': 20, 'end_line': 20, 'basis': 'cleaned_source',
                             'number_binding': 'unnumbered_section', 'printed_numbers': []}},
        ], body_lit)
        (self.batch / '01_source/正文.txt').write_text(body_lit, encoding='utf-8')
        (self.batch / '01_source/附录.txt').write_text(app_lit, encoding='utf-8')
        (self.batch / '01_source/structure.confirmed.json').write_text(
            json.dumps({'identity_version': 'scoped-v1', 'units': units_lit}), encoding='utf-8')
        self.assertEqual(resolve_scoped_answer(self.batch, units_lit[5], 1),
                         {'answer': '报道', 'evidence': ['01_source/附录.txt:9']})

        # 2) 任意新栏目名（带编号答案）：☆微练 -> 1.carefully 2.useful
        body_num = '\n'.join(self.body.splitlines()[:-3]) + (
            '\n**【三、词形微练】**\n'
            '**根据句意用所给词的适当形式填空**\n'
            '**1.**Listen ________(careful).\n'
            '**2.**It is ________(use).\n'
        )
        app_num = '\n'.join(self.appendix.splitlines()[:-2]) + '\n**☆****词形微练**\n1.carefully　2.useful\n'
        units_num = assign_scoped([
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '单词拼写',
             'source_span': {'start_line': 5, 'end_line': 5, 'basis': 'cleaned_source'}},
            {'numbers': [2], 'unit_kind': 'standalone', 'type': '单词拼写',
             'source_span': {'start_line': 6, 'end_line': 6, 'basis': 'cleaned_source'}},
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '单项填空',
             'source_span': {'start_line': 8, 'end_line': 9, 'basis': 'cleaned_source'}},
            {'numbers': [1], 'unit_kind': 'group', 'type': '完形填空',
             'source_span': {'start_line': 12, 'end_line': 13, 'basis': 'cleaned_source'}},
            {'numbers': [1], 'unit_kind': 'group', 'type': '阅读理解',
             'source_span': {'start_line': 15, 'end_line': 17, 'basis': 'cleaned_source'}},
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '单词拼写',
             'source_span': {'start_line': 20, 'end_line': 20, 'basis': 'cleaned_source'}},
            {'numbers': [2], 'unit_kind': 'standalone', 'type': '单词拼写',
             'source_span': {'start_line': 21, 'end_line': 21, 'basis': 'cleaned_source'}},
        ], body_num)
        (self.batch / '01_source/正文.txt').write_text(body_num, encoding='utf-8')
        (self.batch / '01_source/附录.txt').write_text(app_num, encoding='utf-8')
        (self.batch / '01_source/structure.confirmed.json').write_text(
            json.dumps({'identity_version': 'scoped-v1', 'units': units_num}), encoding='utf-8')
        self.assertEqual(resolve_scoped_answer(self.batch, units_num[5], 1)['answer'], 'carefully')
        self.assertEqual(resolve_scoped_answer(self.batch, units_num[6], 2)['answer'], 'useful')

    def test_reverse_lookup_from_appendix_sub_column_works_when_roman_skeleton_is_incomplete(self):
        # 即使其他大题罗马骨架不完整且小栏目位于卷中（非末尾 trailing），
        # 路径 4 仍可根据附录 ☆熟词生义 反向唯一匹配正文中的【熟词生义】位置。
        body_mid = (
            'Unit 1 The Changing World\n'
            '第二课时　Section A (3a-3d)\n'
            '**Ⅰ. 单项填空**\n'
            '**1.**I want to know your ________.\n'
            'A.secret B.opinion\n'
            '**【熟词生义】**\n'
            '**写出下面画线词的汉语意思**\n'
            'He went to <u>cover</u> the games.\t_________\n'
            '**Ⅱ. 阅读理解**\n'
            'Some passage.\n'
            '**1.**When was it?\n'
            'A.In 1805. B.In 1825.\n'
        )
        # 刻意缺少 Ⅱ. 阅读理解 答案，令整卷罗马骨架路径（路径 3）不成立
        app_partial = (
            '****参考答案**\n'
            'Ⅰ.1.B\n'
            '**☆****熟词生义**\n'
            '报道\n'
        )
        units = assign_scoped([
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '单项填空',
             'source_span': {'start_line': 4, 'end_line': 5, 'basis': 'cleaned_source'}},
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '翻译',
             'source_span': {'start_line': 8, 'end_line': 8, 'basis': 'cleaned_source',
                             'number_binding': 'unnumbered_section', 'printed_numbers': []}},
            {'numbers': [1], 'unit_kind': 'group', 'type': '阅读理解',
             'source_span': {'start_line': 10, 'end_line': 12, 'basis': 'cleaned_source'}},
        ], body_mid)
        (self.batch / '01_source/正文.txt').write_text(body_mid, encoding='utf-8')
        (self.batch / '01_source/附录.txt').write_text(app_partial, encoding='utf-8')
        (self.batch / '01_source/structure.confirmed.json').write_text(
            json.dumps({'identity_version': 'scoped-v1', 'units': units}), encoding='utf-8')
        self.assertEqual(resolve_scoped_answer(self.batch, units[1], 1),
                         {'answer': '报道', 'evidence': ['01_source/附录.txt:4']})

        # 附录出现两个同名 ☆熟词生义（歧义）时必须拒绝绑定
        app_dup = app_partial + '**☆****熟词生义**\n覆盖\n'
        (self.batch / '01_source/附录.txt').write_text(app_dup, encoding='utf-8')
        self.assertIsNone(resolve_scoped_answer(self.batch, units[1], 1))

    def test_stacked_parent_and_child_banners_accept_either_in_appendix(self):
        # 正文同一大题前连续叠加【三、B 能力提升】+【四、时文阅读】，附录只写【三、能力提升】时，
        # 父子板块等价集（accepted_bands）应同时接受父板块，不再导致整卷全盘失效。
        body_stacked = (
            'Unit 1 The Changing World\n'
            '第四课时　Section B (1a-1f)\n'
            '**[IMG image9.jpg]****一、教材语篇回顾****[IMG image10.jpg]**\n'
            '[IMG image20.jpg]\n'
            '**【二、A 基础巩固】**\n'
            '**Ⅰ. 根据汉语意思完成句子,每空一词**\n'
            '**1.**We ________ ________ it.\n'
            '**Ⅱ. 单项填空**\n'
            '**1.**She is happy.\n'
            'A.yes B.no\n'
            '**【三、B 能力提升】**\n'
            '**【四、时文阅读】**\n'
            '**阅读理解**\n'
            'On 12 June 2025, CR450 was tested.\n'
            '**1.**When was it tested?\n'
            'A.2024 B.2025\n'
        )
        app_parent_only = (
            '****参考答案**\n'
            '**[IMG image85.jpg]****一、教材语篇回顾****[IMG image86.jpg]**\n'
            '1.capital　2.slow\n'
            '**[IMG image85.jpg]****二、基础巩固****[IMG image86.jpg]**\n'
            'Ⅰ.1.benefit from\n'
            'Ⅱ.1.A\n'
            '**[IMG image85.jpg]****三、能力提升****[IMG image86.jpg]**\n'
            '1.B\n'
        )
        units = assign_scoped([
            {'numbers': [1], 'unit_kind': 'group', 'type': '教材语篇填空',
             'source_span': {'start_line': 4, 'end_line': 4, 'basis': 'cleaned_source',
                             'number_binding': 'unnumbered_section', 'printed_numbers': []}},
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '完成句子',
             'source_span': {'start_line': 7, 'end_line': 7, 'basis': 'cleaned_source'}},
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '单项填空',
             'source_span': {'start_line': 9, 'end_line': 10, 'basis': 'cleaned_source'}},
            {'numbers': [1], 'unit_kind': 'group', 'type': '阅读理解',
             'source_span': {'start_line': 14, 'end_line': 16, 'basis': 'cleaned_source'}},
        ], body_stacked)
        (self.batch / '01_source/正文.txt').write_text(body_stacked, encoding='utf-8')
        (self.batch / '01_source/附录.txt').write_text(app_parent_only, encoding='utf-8')
        (self.batch / '01_source/structure.confirmed.json').write_text(
            json.dumps({'identity_version': 'scoped-v1', 'units': units}), encoding='utf-8')
        self.assertEqual(resolve_scoped_answer(self.batch, units[0], 1)['answer'], '1.capital\u30002.slow')
        self.assertEqual(resolve_scoped_answer(self.batch, units[1], 1)['answer'], 'benefit from')
        self.assertEqual(resolve_scoped_answer(self.batch, units[2], 1)['answer'], 'A')
        self.assertEqual(resolve_scoped_answer(self.batch, units[3], 1)['answer'], 'B')

    def test_sibling_outline_banner_continuing_parent_roman_sequence_accepts_parent_band(self):
        # 正文【二、B 能力提升】下先有 Ⅰ.完形填空，接着插【三、时文阅读】+ Ⅱ.阅读理解（罗马序号顺延）；
        # 附录省略【时文阅读】而将 Ⅰ/Ⅱ 一并归入【二、能力提升】时应全部唯一绑定。
        body_cont = (
            'Starter Unit 3 Welcome!\n'
            '第三课时　Section B (1a-1e)\n'
            '**【一、A 基础巩固】**\n'
            '**Ⅰ. 根据首字母及汉语提示补全单词**\n'
            '**1.**I can c________(数数) from one to ten.\n'
            '**Ⅱ. 根据汉语意思完成句子,每空一词**\n'
            '**1.**This is my uncle’s ________(农场).\n'
            '**【二、B 能力提升】**\n'
            '**Ⅰ. 完形填空**\n'
            'Look at the __<u>1</u>__.\n'
            '**1.**A.dog B.cat\n'
            '**【三、时文阅读】**\n'
            '**Ⅱ. 阅读理解**\n'
            'A short passage.\n'
            '**1.**What is it?\n'
            'A.A farm. B.A zoo.\n'
        )
        app_cont = (
            '参考答案\n'
            '**[IMG image158.jpeg]****一、基础巩固****[IMG image159.jpeg]**\n'
            'Ⅰ.1.count\n'
            'Ⅱ.1.farm\n'
            '**[IMG image158.jpeg]****二、能力提升****[IMG image159.jpeg]**\n'
            'Ⅰ.1.A\n'
            'Ⅱ.1.B\n'
        )
        units = assign_scoped([
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '单词拼写',
             'source_span': {'start_line': 5, 'end_line': 5, 'basis': 'cleaned_source'}},
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '完成句子',
             'source_span': {'start_line': 7, 'end_line': 7, 'basis': 'cleaned_source'}},
            {'numbers': [1], 'unit_kind': 'group', 'type': '完形填空',
             'source_span': {'start_line': 10, 'end_line': 11, 'basis': 'cleaned_source'}},
            {'numbers': [1], 'unit_kind': 'group', 'type': '阅读理解',
             'source_span': {'start_line': 14, 'end_line': 16, 'basis': 'cleaned_source'}},
        ], body_cont)
        (self.batch / '01_source/正文.txt').write_text(body_cont, encoding='utf-8')
        (self.batch / '01_source/附录.txt').write_text(app_cont, encoding='utf-8')
        (self.batch / '01_source/structure.confirmed.json').write_text(
            json.dumps({'identity_version': 'scoped-v1', 'units': units}), encoding='utf-8')
        self.assertEqual(resolve_scoped_answer(self.batch, units[0], 1)['answer'], 'count')
        self.assertEqual(resolve_scoped_answer(self.batch, units[1], 1)['answer'], 'farm')
        self.assertEqual(resolve_scoped_answer(self.batch, units[2], 1)['answer'], 'A')
        self.assertEqual(resolve_scoped_answer(self.batch, units[3], 1)['answer'], 'B')

        # 正例2：即使子栏目横幅下罗马序号从 Ⅰ 重新起算，只要附录省略该子栏目且全卷顺序骨架+题号1:1匹配，
        # 仍应回落到其所属一级经典容器（能力提升）并唯一绑定。
        body_restart = body_cont.replace('**Ⅱ. 阅读理解**', '**Ⅰ. 阅读理解**')
        app_restart = app_cont.replace('Ⅱ.1.B', 'Ⅰ.1.B')
        units_restart = assign_scoped([
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '单词拼写',
             'source_span': {'start_line': 5, 'end_line': 5, 'basis': 'cleaned_source'}},
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '完成句子',
             'source_span': {'start_line': 7, 'end_line': 7, 'basis': 'cleaned_source'}},
            {'numbers': [1], 'unit_kind': 'group', 'type': '完形填空',
             'source_span': {'start_line': 10, 'end_line': 11, 'basis': 'cleaned_source'}},
            {'numbers': [1], 'unit_kind': 'group', 'type': '阅读理解',
             'source_span': {'start_line': 14, 'end_line': 16, 'basis': 'cleaned_source'}},
        ], body_restart)
        (self.batch / '01_source/正文.txt').write_text(body_restart, encoding='utf-8')
        (self.batch / '01_source/附录.txt').write_text(app_restart, encoding='utf-8')
        (self.batch / '01_source/structure.confirmed.json').write_text(
            json.dumps({'identity_version': 'scoped-v1', 'units': units_restart}), encoding='utf-8')
        self.assertEqual(resolve_scoped_answer(self.batch, units_restart[3], 1)['answer'], 'B')

        # 反例：当附录显式印出【时文阅读】却错位标在基础巩固题上时，或基础巩固/能力提升颠倒时，仍严格拒绝。
        app_swapped = (
            '参考答案\n'
            '**[IMG image158.jpeg]****一、能力提升****[IMG image159.jpeg]**\n'
            'Ⅰ.1.count\n'
            'Ⅱ.1.farm\n'
            '**[IMG image158.jpeg]****二、基础巩固****[IMG image159.jpeg]**\n'
            'Ⅰ.1.A\n'
            'Ⅱ.1.B\n'
        )
        (self.batch / '01_source/正文.txt').write_text(body_cont, encoding='utf-8')
        (self.batch / '01_source/附录.txt').write_text(app_swapped, encoding='utf-8')
        (self.batch / '01_source/structure.confirmed.json').write_text(
            json.dumps({'identity_version': 'scoped-v1', 'units': units}), encoding='utf-8')
        self.assertIsNone(resolve_scoped_answer(self.batch, units[3], 1))


if __name__ == '__main__':
    unittest.main()
