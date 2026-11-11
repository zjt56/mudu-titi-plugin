"""Mixed compact lesson keys: whole-task aliases and one explicit extension tail.

Temporary text fixtures only. These regressions do not invoke a model, protocol,
flock, stage runner, upload, or any real batch.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from workflow import agent_tasks, answers_direct
from workflow.question_identity import assign_scoped, question_key
from workflow.upload_content import (
    resolve_scoped_answer, validate_scoped_answer, validate_scoped_missing,
)


class MixedCompactLessonAnswersTest(unittest.TestCase):
    LESSON = '第二课时 Pronunciation'
    FIRST_TITLE = 'Ⅰ.按所含音素给字母归类'
    SONG_TITLE = 'Ⅱ.按字母表顺序补全字母歌'
    GAME_TITLE = 'Ⅲ.填词游戏'
    CHOICE_TITLE = 'Ⅳ.单项选择'
    TAIL_TITLE = '阅读理解'
    FIRST_ANSWERS = (
        'Aa,Hh,Jj,Kk', 'Bb,Cc,Dd,Ee,Gg,Pp,Tt,Vv', 'Ff,Ll,Mm,Nn,Ss,Xx,Zz',
        'Ii,Yy', 'Oo', 'Qq,Uu,Ww', 'Rr',
    )
    SONG_ANSWER = 'C;G;I;L;N;O;Q;S;V;Y'
    GAME_ANSWER = '1.VR　2.PRC　3.USA\n4.UN　5.UK　6.CHINA'

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.batch = Path(temporary.name)
        self.body = '\n'.join((
            self.LESSON,
            self.FIRST_TITLE,
            '1. /eɪ/ ____', '2. /iː/ ____', '3. /e/ ____', '4. /aɪ/ ____',
            '5. /əʊ/ ____', '6. /juː/ ____', '7. /ɑː/ ____',
            self.SONG_TITLE,
            'A B __ D E F __', 'H __ J K __ M __',
            self.GAME_TITLE, '[IMG game.png]',
            self.CHOICE_TITLE,
            '1. First? A. one B. two C. three D. four',
            '2. Second? A. one B. two C. three D. four',
            '3. Third? A. one B. two C. three D. four',
            '4. Fourth? A. one B. two C. three D. four',
            '5. Fifth? A. one B. two C. three D. four',
            self.TAIL_TITLE,
            'Read the passage about the English alphabet.',
            '1. Reading first? A. one B. two C. three D. four',
            '2. Reading second? A. one B. two C. three D. four',
            '3. Reading third? A. one B. two C. three D. four',
        )) + '\n'
        self.appendix = '\n'.join((
            '参考答案', '基础巩固',
            'I.1.Aa,Hh,Jj,Kk　2.Bb,Cc,Dd,Ee,Gg,Pp,Tt,Vv　3.Ff,Ll,Mm,Nn,Ss,Xx,Zz　4.Ii,Yy',
            '5.Oo　6.Qq,Uu,Ww　7.Rr',
            'II.' + self.SONG_ANSWER,
            'III.1.VR　2.PRC　3.USA', '4.UN　5.UK　6.CHINA',
            'IV.1~5 DCABD', '能力提升', '1~3 BAC',
        )) + '\n'
        self.assign_units()
        self.write_sources()

    def assign_units(self):
        """Always derive IDs from the fixture's real body coordinates."""
        lines = self.body.splitlines()

        def line(text):
            return lines.index(text) + 1

        def unit(numbers, kind, name, start, end, alias=False):
            span = {'start_line': start, 'end_line': end, 'basis': 'ai_classified'}
            row = {'numbers': numbers, 'unit_kind': kind, 'type': name, 'source_span': span}
            if alias:
                span.update(number_binding='unnumbered_section', printed_numbers=[])
                row['printed_numbers'] = []
            return row

        raw = [
            unit(list(range(1, 8)), 'group', '语音题', line('1. /eɪ/ ____'), line('7. /ɑː/ ____')),
            unit([1], 'group', '语音题', line('A B __ D E F __'), line('H __ J K __ M __'), True),
            unit([1], 'group', '单词拼写', line('[IMG game.png]'), line('[IMG game.png]'), True),
        ]
        for number, word in enumerate(('First', 'Second', 'Third', 'Fourth', 'Fifth'), 1):
            at = line(f'{number}. {word}? A. one B. two C. three D. four')
            raw.append(unit([number], 'standalone', '单项填空', at, at))
        extra = '1. Extra spelling?'
        if extra in lines:
            raw.append(unit([1], 'group', '单词拼写', line(extra), line(extra)))
        raw.append(unit([1, 2, 3], 'group', '阅读理解',
                        line('Read the passage about the English alphabet.'), len(lines)))
        self.units = assign_scoped(raw, self.body)

    def put_json(self, relative, value):
        path = self.batch / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def write_sources(self):
        (self.batch / '01_source').mkdir(exist_ok=True)
        (self.batch / '01_source/正文.txt').write_text(self.body, encoding='utf-8')
        (self.batch / '01_source/附录.txt').write_text(self.appendix, encoding='utf-8')
        self.put_json('01_source/structure.confirmed.json',
                      {'identity_version': 'scoped-v1', 'units': self.units})
        # _collect can prepare its ordinary draft without a real workflow/protocol.
        self.put_json('01_source/structure.json', {'sections': [], 'groups': []})

    def snapshot(self):
        return {p.relative_to(self.batch).as_posix(): p.read_bytes()
                for p in self.batch.rglob('*') if p.is_file()}

    def expected_rows(self):
        rows = [(0, n, value, [3 if n <= 4 else 4])
                for n, value in enumerate(self.FIRST_ANSWERS, 1)]
        rows.extend(((1, 1, self.SONG_ANSWER, [5]), (2, 1, self.GAME_ANSWER, [6, 7])))
        rows.extend((3 + i, i + 1, value, [8]) for i, value in enumerate('DCABD'))
        rows.extend((len(self.units) - 1, n, value, [10]) for n, value in enumerate('BAC', 1))
        return rows

    def assert_unbound(self, index=0, number=1, answer=None, evidence=None):
        unit = self.units[index]
        if answer is None:
            answer = self.FIRST_ANSWERS[0]
        self.assertIsNone(resolve_scoped_answer(self.batch, unit, number, answer))
        with self.assertRaisesRegex(ValueError, 'scoped answer source'):
            validate_scoped_answer(self.batch, unit, number, answer, evidence or ['附录:3'])

    def assert_no_missing_escape(self, index=0, number=1):
        with self.assertRaisesRegex(ValueError, 'scope unbound|missing contradicts'):
            validate_scoped_missing(self.batch, self.units[index], number, ['未见可绑定答案'])

    def test_fixture_has_two_explicit_internal_aliases_and_seventeen_question_ids(self):
        self.assertEqual(len({question_key(u, n) for u in self.units for n in u['numbers']}), 17)
        self.assertEqual([u['source_section']['ordinal'] for u in self.units[:3]], ['Ⅰ.', 'Ⅱ.', 'Ⅲ.'])
        for index in (1, 2):
            with self.subTest(index=index):
                unit = self.units[index]
                self.assertEqual(unit['numbers'], [1])
                self.assertEqual(unit['printed_numbers'], [])
                self.assertEqual(unit['source_span']['printed_numbers'], [])
                self.assertEqual(unit['source_span']['number_binding'], 'unnumbered_section')
        self.assertEqual(self.units[-1]['source_section']['ordinal'], '')

    def test_complete_mixed_lesson_preserves_all_values_and_actual_source_lines(self):
        for index, number, answer, lines in self.expected_rows():
            with self.subTest(index=index, number=number):
                found = resolve_scoped_answer(self.batch, self.units[index], number)
                self.assertEqual(found, {'answer': answer, 'evidence': [
                    f'01_source/附录.txt:{line}' for line in lines]})
                validate_scoped_answer(self.batch, self.units[index], number, answer, found['evidence'])

    def test_direct_payload_has_all_seventeen_answers_and_passes_collect_validate(self):
        before = self.snapshot()
        payload = answers_direct.build_payload(self.batch)
        self.assertIsNotNone(payload)
        self.assertEqual(self.snapshot(), before)
        expected = {question_key(self.units[i], n): value for i, n, value, _ in self.expected_rows()}
        self.assertEqual(payload['answers'], expected)
        self.assertEqual(payload['missing'], {})
        for i, n, _, lines in self.expected_rows():
            self.assertEqual(payload['evidence'][question_key(self.units[i], n)],
                             [f'01_source/附录.txt:{line}' for line in lines])
        context, _, missing, units = agent_tasks._collect(self.batch, 'answers')
        self.assertFalse(missing, missing)
        agent_tasks._validate(self.batch, 'answers', payload, context, units)
        # Collection may write a temporary draft, but never rewrites its inputs.
        self.assertTrue(all((self.batch / name).read_bytes() == content for name, content in before.items()))

    def test_whole_game_retains_printed_numbers_and_every_original_answer_line(self):
        found = resolve_scoped_answer(self.batch, self.units[2], 1)
        self.assertEqual(found, {'answer': self.GAME_ANSWER,
                                 'evidence': ['01_source/附录.txt:6', '01_source/附录.txt:7']})
        for truncated in ('VR', '1.VR', '1.VR　2.PRC　3.USA', '1.VR　2.PRC　3.USA\n4.UN　5.UK'):
            with self.subTest(truncated=truncated):
                self.assert_unbound(2, answer=truncated, evidence=['附录:6', '附录:7'])
        with self.assertRaisesRegex(ValueError, 'scoped answer source'):
            validate_scoped_answer(self.batch, self.units[2], 1, self.GAME_ANSWER, ['附录:6'])

    def test_whole_alias_requires_group_and_explicit_unnumbered_binding(self):
        for index in (1, 2):
            for defect in ('standalone', 'missing_marker'):
                with self.subTest(index=index, defect=defect):
                    unit = copy.deepcopy(self.units[index])
                    if defect == 'standalone':
                        unit['unit_kind'] = 'standalone'
                    else:
                        unit['source_span'].pop('number_binding')
                    self.assertIsNone(resolve_scoped_answer(self.batch, unit, 1))
                    with self.assertRaises(ValueError):
                        validate_scoped_answer(self.batch, unit, 1,
                                               self.SONG_ANSWER if index == 1 else self.GAME_ANSWER,
                                               ['附录:5', '附录:6', '附录:7'])

    def test_letter_literal_accepts_only_semicolon_separated_english_letters(self):
        original = self.appendix
        for literal in ('C,G,I,L,N,O,Q,S,V,Y', 'C/G/I/L', 'C;Green;I', 'C;1;I',
                        'C;Ｇ;I', 'C', 'C;G;[IMG answer.png]', 'C;G;I trailing'):
            with self.subTest(literal=literal):
                self.appendix = original.replace('II.' + self.SONG_ANSWER, 'II.' + literal)
                self.write_sources()
                self.assertIsNone(resolve_scoped_answer(self.batch, self.units[1], 1))
                self.assertIsNone(answers_direct.build_payload(self.batch))
        self.appendix = original.replace(self.SONG_ANSWER, 'C；G；I；L；N；O；Q；S；V；Y')
        self.write_sources()
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[1], 1)['answer'],
                         'C；G；I；L；N；O；Q；S；V；Y')

    def test_missing_or_reordered_roman_block_rejects_complete_proof(self):
        original = self.appendix
        for appendix in (original.replace('II.' + self.SONG_ANSWER + '\n', ''),
                         original.replace('II.', 'IX.'),
                         original.replace('II.' + self.SONG_ANSWER + '\nIII.1.VR',
                                          'III.' + self.SONG_ANSWER + '\nII.1.VR')):
            with self.subTest(appendix=appendix):
                self.appendix = appendix
                self.write_sources()
                self.assert_unbound()
                self.assert_no_missing_escape()
                self.assertIsNone(answers_direct.build_payload(self.batch))

    def test_wrong_explicit_lesson_cannot_borrow_the_complete_mixed_key(self):
        self.appendix = '第三课时 Pronunciation\n' + self.appendix
        self.write_sources()
        self.assert_unbound()
        self.assert_no_missing_escape()

    def test_unit_only_body_without_any_lesson_is_not_single_lesson_proof(self):
        self.body = self.body.replace(self.LESSON, 'Unit 1')
        self.assign_units()
        self.write_sources()
        self.assert_unbound()
        self.assert_no_missing_escape()

    def test_repeated_identical_lesson_banner_does_not_merge_two_lessons(self):
        self.body = self.body.replace(self.CHOICE_TITLE, self.LESSON + '\n' + self.CHOICE_TITLE)
        self.assign_units()
        self.write_sources()
        self.assert_unbound()
        self.assert_no_missing_escape()

    def test_two_nonroman_sections_cannot_choose_a_numeric_tail_by_position(self):
        self.body = self.body.replace(self.TAIL_TITLE,
                                      '根据首字母及汉语提示补全单词\n1. Extra spelling?\n' + self.TAIL_TITLE)
        self.assign_units()
        self.write_sources()
        self.assertEqual(len({u['section_id'] for u in self.units}), 6)
        self.assert_unbound()
        self.assert_no_missing_escape()

    def test_tail_requires_one_explicit_extension_banner(self):
        original = self.appendix
        for replacement in ('', '基础巩固\n', '能力提升\n能力提升\n', '基础巩固\n能力提升\n'):
            with self.subTest(replacement=replacement):
                self.appendix = original.replace('能力提升\n', replacement)
                self.write_sources()
                self.assert_unbound()
                self.assert_no_missing_escape()

    def test_duplicate_foundation_banner_cannot_be_hidden_before_first_block(self):
        self.appendix = self.appendix.replace('基础巩固\n', '基础巩固\n基础巩固\n')
        self.write_sources()
        self.assert_unbound()
        self.assert_no_missing_escape()

    def test_unknown_titles_and_continuations_do_not_prove_the_tail(self):
        original = self.appendix
        for continuation in ('阅读理解', '参考答案', 'Test', '[IMG hidden.png]',
                             '[IMG a.png]能力提升[IMG b.png]extra'):
            with self.subTest(continuation=continuation):
                self.appendix = original.replace('能力提升\n', '能力提升\n' + continuation + '\n')
                self.write_sources()
                self.assert_unbound()
                self.assert_no_missing_escape()

    def test_tail_anchors_are_exact_not_a_subset_or_duplicate_number_list(self):
        original = self.appendix
        for tail in ('1~2 BA', '1.B　2.A　4.C', '1.B　2.A　3.C　4.D', '1.B　2.A　3.C　3.C'):
            with self.subTest(tail=tail):
                self.appendix = original.replace('1~3 BAC', tail)
                self.write_sources()
                self.assert_unbound()
                self.assert_no_missing_escape()

    def test_duplicate_tail_body_anchor_invalidates_exact_tail_proof(self):
        self.body += '3. Duplicated reading question?\n'
        self.assign_units()
        self.write_sources()
        self.assert_unbound()
        self.assert_no_missing_escape()

    def test_drifted_section_identity_or_source_span_does_not_reuse_current_key(self):
        for field, value in (('title', 'Ⅲ.阅读理解'), ('occurrence', 99),
                             ('context', '第三课时 Pronunciation'), ('start_line', 1), ('end_line', 25)):
            with self.subTest(field=field):
                unit = copy.deepcopy(self.units[2])
                unit['source_section'][field] = value
                self.assertIsNone(resolve_scoped_answer(self.batch, unit, 1))
        unit = copy.deepcopy(self.units[2])
        unit['source_span']['start_line'] -= 2  # Cross into the preceding song.
        self.assertIsNone(resolve_scoped_answer(self.batch, unit, 1))
        unit = copy.deepcopy(self.units[2])
        unit['source_span']['end_line'] += 1
        self.assertIsNone(resolve_scoped_answer(self.batch, unit, 1))
        unit = copy.deepcopy(self.units[1])
        unit['source_span']['end_line'] -= 1  # Truncating an unnumbered task is drift too.
        self.assertIsNone(resolve_scoped_answer(self.batch, unit, 1))

    def test_unbound_letter_literal_cannot_be_discarded_as_missing(self):
        self.appendix = self.appendix.replace('II.' + self.SONG_ANSWER, self.SONG_ANSWER)
        self.write_sources()
        self.assertIsNone(resolve_scoped_answer(self.batch, self.units[1], 1))
        self.assert_no_missing_escape(1)

    def test_same_answer_text_in_another_section_is_not_valid_coordinate_evidence(self):
        # IV.2 and the tail's 3 both print C; identical text cannot cross scopes.
        validate_scoped_answer(self.batch, self.units[4], 2, 'C', ['附录:8'])
        validate_scoped_answer(self.batch, self.units[-1], 3, 'C', ['附录:10'])
        for index, number, wrong_line in ((4, 2, 10), (-1, 3, 8)):
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, 'scoped answer source'):
                validate_scoped_answer(self.batch, self.units[index], number, 'C', [f'附录:{wrong_line}'])

    def test_word_answers_win_pdf_and_reference_only_copies_never_compete(self):
        path = self.batch / '01_source/materials/attachment_001.txt'
        path.parent.mkdir()
        path.write_text(self.appendix.replace('IV.1~5 DCABD', 'IV.1~5 ACABD'), encoding='utf-8')
        found = resolve_scoped_answer(self.batch, self.units[3], 1)
        self.assertEqual(found, {'answer': 'D', 'evidence': ['01_source/附录.txt:8']})
        self.assertIsNone(resolve_scoped_answer(self.batch, self.units[3], 1, 'A'))
        self.put_json('01_source/material-roles.json', {'documents': [
            {'text_path': '01_source/materials/attachment_001.txt', 'content_authority': False}]})
        payload = answers_direct.build_payload(self.batch)
        self.assertIsNotNone(payload)
        self.assertEqual(len(payload['answers']), 17)
        self.assertEqual(payload['answers'][question_key(self.units[3], 1)], 'D')
        (self.batch / '01_source/附录.txt').unlink()
        self.assertIsNone(resolve_scoped_answer(self.batch, self.units[3], 1))
        self.put_json('01_source/material-roles.json', {'documents': []})
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[3], 1),
                         {'answer': 'A', 'evidence': ['01_source/materials/attachment_001.txt:8']})

    def test_lone_unbound_song_literal_is_not_evidence_of_absence(self):
        self.body = self.LESSON + '\nⅠ.按字母表顺序补全字母歌\nA B __ D E F __\nH __ J\n'
        self.units = assign_scoped([{'numbers': [1], 'unit_kind': 'group', 'type': '语音题',
            'source_span': {'start_line': 3, 'end_line': 4, 'basis': 'ai_classified',
                            'number_binding': 'unnumbered_section', 'printed_numbers': []}}], self.body)
        self.appendix = '参考答案\nC;G;I\n'
        self.write_sources()
        self.assertIsNone(resolve_scoped_answer(self.batch, self.units[0], 1))
        self.assert_no_missing_escape()

    def test_all_roman_whole_alias_still_requires_an_actual_lesson(self):
        self.body = 'Unit 1\nⅠ.按字母表顺序补全字母歌\nA B __ D E F __\nH __ J\n'
        self.units = assign_scoped([{'numbers': [1], 'unit_kind': 'group', 'type': '语音题',
            'source_span': {'start_line': 3, 'end_line': 4, 'basis': 'ai_classified',
                            'number_binding': 'unnumbered_section', 'printed_numbers': []}}], self.body)
        self.appendix = '参考答案\nI.C;G;I\n'
        self.write_sources()
        self.assertIsNone(resolve_scoped_answer(self.batch, self.units[0], 1))
        self.assert_no_missing_escape()

    def test_malformed_pdf_peer_cannot_poison_a_complete_word_original(self):
        path = self.batch / '01_source/materials/attachment_001.txt'
        path.parent.mkdir()
        path.write_text(self.appendix.replace('IV.1~5 DCABD', 'IV.1.D　1.A'), encoding='utf-8')
        payload = answers_direct.build_payload(self.batch)
        self.assertIsNotNone(payload)
        self.assertEqual(len(payload['answers']), 17)
        validate_scoped_answer(self.batch, self.units[0], 1, self.FIRST_ANSWERS[0], ['附录:3'])
        # An invalid fallback still cannot become an answer when Word is absent.
        (self.batch / '01_source/附录.txt').unlink()
        self.assertIsNone(answers_direct.build_payload(self.batch))


if __name__ == '__main__':
    unittest.main()
