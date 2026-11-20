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


class CompactAnswerGrammarTest(unittest.TestCase):
    """Regression: Roman keys mixing whole-task ranges and single items, and
    cloze material inline blank markers must not double-count printed anchors.

    Real batch 03（七上·课时A计划 Starter Unit 1 课时3 Section B）regressed here:
    附录 ``Ⅳ.1~5 BDBCA　6.D`` and ``Ⅱ.1~5 ACABD　6~10 DDBAB`` were parsed as an
    invalid block, so every unit sharing that skeleton raised "scoped answer
    source scope unbound: duplicate or malformed compact answer block".
    """

    def test_range_plus_single_item_parses(self):
        from workflow.upload_content import _answer_items
        self.assertEqual(_answer_items('1~5 BDBCA\u30006.D'),
                         [(1, 'B'), (2, 'D'), (3, 'B'), (4, 'C'), (5, 'A'), (6, 'D')])
        self.assertEqual(_answer_items('1~5 ACABD\u30006~10 DDBAB'),
                         [(1, 'A'), (2, 'C'), (3, 'A'), (4, 'B'), (5, 'D'),
                          (6, 'D'), (7, 'D'), (8, 'B'), (9, 'A'), (10, 'B')])

    def test_pure_range_and_numbered_items_unchanged(self):
        from workflow.upload_content import _answer_items
        self.assertEqual(_answer_items('1~5 EFAGB'),
                         [(1, 'E'), (2, 'F'), (3, 'A'), (4, 'G'), (5, 'B')])
        self.assertEqual(_answer_items('1.great\u30002.spell\u30003.bell'),
                         [(1, 'great'), (2, 'spell'), (3, 'bell')])
        self.assertEqual(_answer_items('4.do you spell\u30005.How is;fine/OK;thank you'),
                         [(4, 'do you spell'), (5, 'How is;fine/OK;thank you')])

    def test_malformed_ranges_still_rejected(self):
        from workflow.upload_content import _answer_items
        for text in ('1~2 A', '1~5 A', '1.A\u30001.B', 'Hello world 3.x'):
            with self.subTest(text=text):
                self.assertEqual(_answer_items(text), [])

    def test_judgement_letter_ranges_parse(self):
        from workflow.upload_content import _answer_items
        # 判断题的答案在原卷写成 S/D（相同/不同），不是 A–D；
        # 只要字母数与区间长度一致就是合法紧凑答案块。
        self.assertEqual(_answer_items('1~5 DSDDS'),
                         [(1, 'D'), (2, 'S'), (3, 'D'), (4, 'D'), (5, 'S')])
        self.assertEqual(_answer_items('1~3 TFT'),
                         [(1, 'T'), (2, 'F'), (3, 'T')])
        # 词答案、长度不符的缩写仍拒绝。
        for text in ('1~5 cats', '1~4 DSDDS'):
            with self.subTest(text=text):
                self.assertEqual(_answer_items(text), [])

    def test_cloze_blank_markers_not_double_counted(self):
        from workflow.upload_content import _printed_item_anchors
        lines = [
            'Good morning.That\u2019s the <u>\u30001\u3000</u>.Let\u2019s have our <u>\u30002\u3000</u> now.',
            '(\u3000)1.A.bell\tB.section',
            '(\u3000)2.A.room\tB.water',
        ]
        counts = {}
        for _, n in _printed_item_anchors(lines):
            counts[n] = counts.get(n, 0) + 1
        self.assertEqual(counts, {1: 1, 2: 1})
        # Literal duplicates on one line must still be visible to the caller.
        dup = [n for _, n in _printed_item_anchors(['1.A\u30001.B'])]
        self.assertEqual(dup, [1, 1])


class BoldWrappedLessonBandBannerTest(unittest.TestCase):
    """答案册板块横幅带 **粗体** 与中文序号前缀（一、/三、）时的识别回归。

    真实批次 12七上人教版…StarterUnit3Welcome!第四课时Proj 的附录把
    `**[IMG …]****一、重要考点集训****[IMG …]**` 当作板块横幅；旧 _LESSON_BAND 只认
    `[IMG …]重要考点集训[IMG …]`，横幅被当游离文字把前一块判为畸形，整卷答案卡在
    "duplicate or malformed compact answer block"。
    """

    def test_bold_and_chinese_ordinal_banners_are_recognized(self):
        from workflow.upload_content import _LESSON_BAND, _answer_band
        foundation = '**[IMG image158.jpeg]****一、重要考点集训****[IMG image159.jpeg]**'
        extension = '**[IMG image158.jpeg]****三、创新阅读专练****[IMG image159.jpeg]**'
        self.assertEqual(_LESSON_BAND.fullmatch(foundation)[1], '重要考点集训')
        self.assertEqual(_LESSON_BAND.fullmatch(extension)[1], '创新阅读专练')
        self.assertEqual(_answer_band(foundation), 'foundation')
        self.assertEqual(_answer_band(extension), 'extension')
        # 普通与 **B 能力提升** 形态仍识别；无关文字不误判。
        self.assertEqual(_answer_band('基础巩固'), 'foundation')
        self.assertEqual(_answer_band('**B 能力提升**'), 'extension')
        self.assertEqual(_LESSON_BAND.fullmatch('随便一行文字'), None)

    def test_band_label_and_canonical_recognize_all_wrapped_forms(self):
        from workflow.lesson_bands import band_label, canonical_label, normalize_band_text
        # 四类板块标签都能在 **粗体**/IMG/中文序号包裹下识别。
        self.assertEqual(band_label('**[IMG x]****一、重要考点集训****[IMG y]**'), '重要考点集训')
        self.assertEqual(band_label('**[IMG x]****三、创新阅读专练****[IMG y]**'), '创新阅读专练')
        self.assertEqual(band_label('**B 能力提升**'), '能力提升')
        self.assertEqual(band_label('A 基础巩固'), '基础巩固')
        self.assertEqual(normalize_band_text('**[IMG x]****二、创新阅读专练****[IMG y]**'), '创新阅读专练')
        # canonical_label 只认 A/B 一对（正文层级），不返回答案册容器标签。
        self.assertEqual(canonical_label('**B 能力提升**'), '能力提升')
        self.assertEqual(canonical_label('一、重要考点集训'), '')
        # A/B 与标签冲突时 canonical 判空（语义兜底）。
        self.assertEqual(canonical_label('A 能力提升'), '')

    def test_section_ordinal_tolerates_bold_and_img_decoration(self):
        from workflow.upload_content import _section, _ROMAN_ANSWER, _ROMAN_TITLE
        # 粗体包裹的序号标题不再是“简单漏识别”。
        self.assertEqual(_section('**Ⅰ. 写出下列单词**')[0], 'I.')
        self.assertEqual(_section('**Ⅲ.** 完成句子')[0], 'III.')
        self.assertEqual(_ROMAN_ANSWER.match('**Ⅲ.** 完成句子').group(1), 'Ⅲ')
        self.assertEqual(_ROMAN_TITLE.match('**Ⅱ. 选词**').group(1), 'Ⅱ')
        # 普通形态与中文序号仍照常。
        self.assertEqual(_section('Ⅰ. 单词')[0], 'I.')
        self.assertEqual(_section('三、创新阅读专练')[0], '三、')

    def test_banner_between_blocks_does_not_poison_preceding_block(self):
        from workflow.upload_content import _compact_answer_blocks
        appendix = [
            '****参考答案**',
            '**[IMG image158.jpeg]****一、重要考点集训****[IMG image159.jpeg]**',
            'Ⅰ.one,two,three,four,five,six,seven,eight,nine,ten',
            'Ⅱ.1.another　2.Those　3.fun　4.count　5.his　6.sheep',
            'Ⅲ.1.are these;They’re some boxes　2.How many apple trees　3.Look at　4.What else　5.many kinds of',
            '**[IMG image158.jpeg]****三、创新阅读专练****[IMG image159.jpeg]**',
            '1~5 CEDBF',
            '6.fifteen/15　7.tidy　8.classes　9.animal　10.two/2',
        ]
        blocks = _compact_answer_blocks(appendix, features=())
        # _norm 把罗马序号归一为 ASCII（Ⅰ→I）。
        self.assertEqual([b['ordinal'] for b in blocks], ['I', 'II', 'III', None])
        # 三、创新阅读专练 横幅夹在 Ⅲ 与尾部裸块之间，不得把 Ⅲ 判为畸形。
        self.assertFalse(any(b['invalid'] for b in blocks), [b['invalid'] for b in blocks])
        self.assertEqual(blocks[0]['band'], '重要考点集训')
        self.assertEqual(blocks[3]['band'], '创新阅读专练')
        # Ⅲ 五小题、尾部裸块十项都完整解析。
        self.assertEqual([n for n, _, _ in blocks[2]['rows']], [1, 2, 3, 4, 5])
        self.assertEqual([n for n, _, _ in blocks[3]['rows']], list(range(1, 11)))


class DynamicOutlineBandAlignmentTest(unittest.TestCase):
    """基于正文目录树动态对齐答案板块（不再依赖写死的四词白名单）。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.batch = Path(tmp.name)
        (self.batch / '01_source').mkdir()
        self.body = '\n'.join((
            '第四课时　Project',
            '**【一、A 基础达标】**',
            '**【二、考点小结】**',
            '知识梳理，不是题目。',
            'Ⅰ.根据句意用所给词的适当形式填空',
            '1. First?',
            '2. Second?',
            'Ⅱ.单项填空',
            '(　)1. Choice one?',
            '(　)2. Choice two?',
            '**【三、B 素养提升】**',
            '任务型阅读',
            'Read the passage.',
            '1. Task one?',
            '2. Task two?',
        )) + '\n'
        self.appendix = '\n'.join((
            '参考答案',
            '**[IMG image158.jpeg]****一、基础达标****[IMG image159.jpeg]**',
            'Ⅰ.1.apple　2.pear',
            'Ⅱ.1~2 AB',
            '**[IMG image158.jpeg]****三、素养提升****[IMG image159.jpeg]**',
            '1.Yes, he does.　2.In the yard.',
        )) + '\n'
        lines = self.body.splitlines()

        def line(text):
            return lines.index(text) + 1

        raw = [
            {'numbers': [1, 2], 'unit_kind': 'group', 'type': '单词拼写',
             'source_span': {'start_line': line('1. First?'), 'end_line': line('2. Second?')}},
            {'numbers': [1, 2], 'unit_kind': 'group', 'type': '单项填空',
             'source_span': {'start_line': line('(　)1. Choice one?'), 'end_line': line('(　)2. Choice two?')}},
            {'numbers': [1, 2], 'unit_kind': 'group', 'type': '任务型阅读',
             'source_span': {'start_line': line('Read the passage.'), 'end_line': len(lines)}},
        ]
        self.units = assign_scoped(raw, self.body)
        self.write_sources()

    def write_sources(self):
        (self.batch / '01_source/正文.txt').write_text(self.body, encoding='utf-8')
        (self.batch / '01_source/附录.txt').write_text(self.appendix, encoding='utf-8')
        (self.batch / '01_source/structure.confirmed.json').write_text(
            json.dumps({'identity_version': 'scoped-v1', 'units': self.units}, ensure_ascii=False),
            encoding='utf-8')

    def test_non_whitelist_body_outline_banners_align_and_skip_empty_summary(self):
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[0], 1)['answer'], 'apple')
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[0], 2)['answer'], 'pear')
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[1], 1)['answer'], 'A')
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[1], 2)['answer'], 'B')
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[2], 1)['answer'], 'Yes, he does.')
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[2], 2)['answer'], 'In the yard.')
        payload = answers_direct.build_payload(self.batch)
        self.assertIsNotNone(payload)
        self.assertEqual(len(payload['answers']), 6)

    def test_bracketed_and_markdown_header_banners_parse_consistently(self):
        from workflow.upload_content import _compact_answer_blocks
        appendix = [
            '参考答案',
            '## 【一、基础达标】',
            'Ⅰ.1.apple　2.pear',
            'Ⅱ.1~2 AB',
            '【三、B 素养提升】',
            '1.Yes, he does.　2.In the yard.',
        ]
        blocks = _compact_answer_blocks(appendix, outline_bands=('基础达标', '素养提升'))
        self.assertFalse(any(b['invalid'] for b in blocks))
        self.assertEqual([b['band'] for b in blocks], ['基础达标', '基础达标', '素养提升'])

    def test_unknown_banner_not_in_body_outline_or_with_stray_text_still_rejected(self):
        original = self.appendix
        for bad in (
            original.replace('三、素养提升', '三、课外拓展'),
            original.replace('三、素养提升', '三、素养提升 extra'),
            original.replace('三、素养提升', '二、考点小结'),
        ):
            with self.subTest(bad=bad):
                self.appendix = bad
                self.write_sources()
                self.assertIsNone(resolve_scoped_answer(self.batch, self.units[2], 1))
                self.assertIsNone(answers_direct.build_payload(self.batch))


class TestChineseOutlineSectionWithClassicBands(unittest.TestCase):
    """正文含中文一级大题标题（一、教材语篇回顾）+ 经典板块（二、基础巩固 Ⅰ/Ⅱ + 三、能力提升）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.batch = Path(self.tmp.name)
        (self.batch / '01_source').mkdir(parents=True)
        self.body = '\n'.join((
            '九上 · 人教版 · 课时A计划',
            '**Unit 2　Inspiring People**',
            '**第四课时**　Section B (1a-1f)',
            '**[IMG image9.jpg]****一、教材语篇回顾****[IMG image10.jpg]**',
            '[IMG image28.jpg]',
            '',
            '**【二、A 基础巩固】**',
            '**Ⅰ. 根据句意及汉语提示完成单词**',
            '**1.**Drop me off near the ________(银行).',
            '**2.**Happy and healthy ________(永远).',
            '**Ⅱ. 单项填空**',
            '**1.**First choice?',
            'A.a  B.b',
            '**2.**Second choice?',
            'A.a  B.b',
            '**【三、B 能力提升】**',
            '**阅读理解[2025·四川广元改编]**',
            'A short passage.',
            '**1.**Question one?',
            'A.a  B.b',
            '**2.**Question two?',
            'A.a  B.b',
        )) + '\n'
        self.appendix = '\n'.join((
            '****参考答案**',
            '**[IMG image85.jpg]****一、教材语篇回顾****[IMG image86.jpg]**',
            '1.was born in　2.researching',
            '3.lives on',
            '**[IMG image85.jpg]****二、基础巩固****[IMG image86.jpg]**',
            'Ⅰ.1.bank　2.forever',
            'Ⅱ.1~2 BC',
            '**[IMG image85.jpg]****三、能力提升****[IMG image86.jpg]**',
            '1~2 CD',
        )) + '\n'
        lines = self.body.splitlines()

        def line(text):
            return lines.index(text) + 1

        raw = [
            {'numbers': [1], 'unit_kind': 'group', 'type': '教材语篇填空',
             'source_span': {'start_line': line('[IMG image28.jpg]'), 'end_line': line('[IMG image28.jpg]') + 1,
                             'basis': 'cleaned_source', 'number_binding': 'unnumbered_section', 'printed_numbers': []}},
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '单词拼写',
             'source_span': {'start_line': line('**1.**Drop me off near the ________(银行).'),
                             'end_line': line('**1.**Drop me off near the ________(银行).')}},
            {'numbers': [2], 'unit_kind': 'standalone', 'type': '单词拼写',
             'source_span': {'start_line': line('**2.**Happy and healthy ________(永远).'),
                             'end_line': line('**2.**Happy and healthy ________(永远).')}},
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '单项填空',
             'source_span': {'start_line': line('**1.**First choice?'), 'end_line': line('**1.**First choice?') + 1}},
            {'numbers': [2], 'unit_kind': 'standalone', 'type': '单项填空',
             'source_span': {'start_line': line('**2.**Second choice?'), 'end_line': line('**2.**Second choice?') + 1}},
            {'numbers': [1, 2], 'unit_kind': 'group', 'type': '阅读理解',
             'source_span': {'start_line': line('A short passage.'), 'end_line': len(lines)}},
        ]
        self.units = assign_scoped(raw, self.body)
        self.write_sources()

    def tearDown(self):
        self.tmp.cleanup()

    def write_sources(self):
        (self.batch / '01_source/正文.txt').write_text(self.body, encoding='utf-8')
        (self.batch / '01_source/附录.txt').write_text(self.appendix, encoding='utf-8')
        (self.batch / '01_source/structure.confirmed.json').write_text(
            json.dumps({'identity_version': 'scoped-v1', 'units': self.units}, ensure_ascii=False),
            encoding='utf-8')

    def test_leading_chinese_outline_section_binds_with_classic_bands(self):
        first = resolve_scoped_answer(self.batch, self.units[0], 1)
        self.assertEqual(first, {
            'answer': '1.was born in　2.researching\n3.lives on',
            'evidence': ['01_source/附录.txt:3', '01_source/附录.txt:4'],
        })
        validate_scoped_answer(self.batch, self.units[0], 1, first['answer'], first['evidence'])
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[1], 1)['answer'], 'bank')
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[2], 2)['answer'], 'forever')
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[3], 1)['answer'], 'B')
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[4], 2)['answer'], 'C')
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[5], 1)['answer'], 'C')
        self.assertEqual(resolve_scoped_answer(self.batch, self.units[5], 2)['answer'], 'D')
        payload = answers_direct.build_payload(self.batch)
        self.assertIsNotNone(payload)
        self.assertEqual(len(payload['answers']), 7)

    def test_missing_or_mismatched_outline_or_extension_banner_stays_rejected(self):
        original = self.appendix
        for bad in (
            original.replace('**[IMG image85.jpg]****一、教材语篇回顾****[IMG image86.jpg]**\n', ''),
            original.replace('一、教材语篇回顾', '一、词汇梳理'),
            original.replace('**[IMG image85.jpg]****三、能力提升****[IMG image86.jpg]**\n', ''),
        ):
            with self.subTest(bad=bad):
                self.appendix = bad
                self.write_sources()
                self.assertIsNone(resolve_scoped_answer(self.batch, self.units[0], 1))
                self.assertIsNone(resolve_scoped_answer(self.batch, self.units[5], 1))
                self.assertIsNone(answers_direct.build_payload(self.batch))

    def test_two_unnumbered_sections_in_same_band_still_rejected(self):
        self.body += '\n'.join((
            '**任务型阅读**',
            'Another passage.',
            '**1.**Task one?',
            '**2.**Task two?',
        )) + '\n'
        self.appendix += '1.yes　2.no\n'
        lines = self.body.splitlines()
        raw = [
            {'numbers': [1], 'unit_kind': 'group', 'type': '教材语篇填空',
             'source_span': {'start_line': lines.index('[IMG image28.jpg]') + 1,
                             'end_line': lines.index('[IMG image28.jpg]') + 2,
                             'basis': 'cleaned_source', 'number_binding': 'unnumbered_section', 'printed_numbers': []}},
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '单词拼写',
             'source_span': {'start_line': lines.index('**1.**Drop me off near the ________(银行).') + 1,
                             'end_line': lines.index('**1.**Drop me off near the ________(银行).') + 1}},
            {'numbers': [2], 'unit_kind': 'standalone', 'type': '单词拼写',
             'source_span': {'start_line': lines.index('**2.**Happy and healthy ________(永远).') + 1,
                             'end_line': lines.index('**2.**Happy and healthy ________(永远).') + 1}},
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '单项填空',
             'source_span': {'start_line': lines.index('**1.**First choice?') + 1,
                             'end_line': lines.index('**1.**First choice?') + 2}},
            {'numbers': [2], 'unit_kind': 'standalone', 'type': '单项填空',
             'source_span': {'start_line': lines.index('**2.**Second choice?') + 1,
                             'end_line': lines.index('**2.**Second choice?') + 2}},
            {'numbers': [1, 2], 'unit_kind': 'group', 'type': '阅读理解',
             'source_span': {'start_line': lines.index('A short passage.') + 1,
                             'end_line': lines.index('**任务型阅读**')}},
            {'numbers': [1, 2], 'unit_kind': 'group', 'type': '任务型阅读',
             'source_span': {'start_line': lines.index('Another passage.') + 1, 'end_line': len(lines)}},
        ]
        self.units = assign_scoped(raw, self.body)
        self.write_sources()
        self.assertIsNone(resolve_scoped_answer(self.batch, self.units[5], 1))
        self.assertIsNone(resolve_scoped_answer(self.batch, self.units[6], 1))


if __name__ == '__main__':
    unittest.main()
