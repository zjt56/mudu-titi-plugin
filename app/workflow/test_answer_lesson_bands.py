"""Answer consumers isolate lesson containers using actual source coordinates."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import agent_tasks, answers_direct
from workflow.question_identity import assign_scoped, question_key
from workflow.upload_content import resolve_scoped_answer, validate_scoped_answer, validate_scoped_missing


class AnswerLessonBandsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / '01_source').mkdir()
        self.body = ('第一课时 Section A\nA 基础巩固\nⅠ.单项填空\n1. First?\n'
                     'B 能力提升\nⅠ.单项填空\n1. Second?\n')
        self.appendix = 'A 基础巩固\nⅠ.1.A\nB 能力提升\nⅠ.1.B\n'
        self.units = assign_scoped([
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '单项填空',
             'source_span': {'start_line': 4, 'end_line': 4}},
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '单项填空',
             'source_span': {'start_line': 7, 'end_line': 7}},
        ], self.body)
        self.save()

    def save(self):
        for relative, text in [('01_source/正文.txt', self.body), ('01_source/附录.txt', self.appendix)]:
            (self.root / relative).write_text(text, encoding='utf-8')
        (self.root / '01_source/structure.confirmed.json').write_text(
            json.dumps({'identity_version': 'scoped-v1', 'units': self.units}), encoding='utf-8')

    def assert_rejected(self, unit=0, value='A', line=2):
        self.assertIsNone(resolve_scoped_answer(self.root, self.units[unit], 1))
        with self.assertRaisesRegex(ValueError, 'scoped answer source'):
            validate_scoped_answer(self.root, self.units[unit], 1, value, [f'附录:{line}'])
        with self.assertRaisesRegex(ValueError, 'scope unbound'):
            validate_scoped_missing(self.root, self.units[unit], 1)
        self.assertIsNone(answers_direct.build_payload(self.root))

    def test_actual_direct_consumer_delivers_disjoint_question_ids_and_real_lines(self):
        payload = answers_direct.build_payload(self.root)
        self.assertEqual(payload['answers'], {question_key(self.units[0], 1): 'A', question_key(self.units[1], 1): 'B'})
        for index, value, line in [(0, 'A', 2), (1, 'B', 4)]:
            evidence = [f'01_source/附录.txt:{line}']
            self.assertEqual(payload['evidence'][question_key(self.units[index], 1)], evidence)
            validate_scoped_answer(self.root, self.units[index], 1, value, evidence)

    def test_swapped_identical_or_different_bands_never_borrow_roman_sequence(self):
        for value in ['A', 'B']:
            with self.subTest(value=value):
                self.appendix = f'B 能力提升\nⅠ.1.A\nA 基础巩固\nⅠ.1.{value}\n'
                self.save()
                self.assert_rejected()

    def test_inconsistent_a_b_letter_and_label_is_not_silently_repaired(self):
        for banner in ['A 能力提升', 'B 基础巩固']:
            with self.subTest(banner=banner):
                self.appendix = banner + '\nⅠ.1.A\nB 能力提升\nⅠ.1.B\n'
                self.save()
                self.assert_rejected()

    def test_full_title_companion_must_match_band_even_when_value_is_equal(self):
        self.appendix = ('第一课时 Section A\nB 能力提升\nⅠ.单项填空\n1.A\n'
                         'A 基础巩固\nⅠ.单项填空\n1.B\n')
        self.save()
        self.assert_rejected(line=4)

    def test_absent_appendix_band_is_not_proof_for_explicit_body_band(self):
        self.appendix = 'Ⅰ.1.A\nⅠ.1.B\n'
        self.save()
        self.assert_rejected(line=1)

    def test_pronunciation_cannot_supply_foundation_even_with_equal_answer(self):
        self.appendix = 'Pronunciation\nⅠ.1.A\nB 能力提升\nⅠ.1.B\n'
        self.save()
        self.assert_rejected()

    def test_explicit_pronunciation_topic_full_sequence_binds_without_cross_borrow(self):
        self.body += 'Pronunciation\nⅠ.单项填空\n1. Sound?\n'
        raw = [{k: copy.deepcopy(v) for k, v in u.items()
                if k in {'numbers', 'unit_kind', 'type', 'source_span'}} for u in self.units]
        raw.append({'numbers': [1], 'unit_kind': 'group', 'type': '语音题',
                    'source_span': {'start_line': 10, 'end_line': 10}})
        self.units = assign_scoped(raw, self.body)
        self.appendix += 'Pronunciation\nⅠ.1.C\n'
        self.save()
        payload = answers_direct.build_payload(self.root)
        self.assertEqual(len(payload['answers']), 3)
        self.assertEqual(payload['answers'][question_key(self.units[-1], 1)], 'C')
        validate_scoped_answer(self.root, self.units[-1], 1, 'C', ['附录:6'])
        self.appendix = self.appendix.replace('Pronunciation\n', '')
        self.save()
        self.assert_rejected()

    def test_real_verified_visual_artifact_controls_direct_and_validator_consumers(self):
        from workflow import lesson_bands
        self.body = self.body.replace('A 基础巩固', '[IMG a.png]').replace('B 能力提升', '[IMG b.png]')
        self.save()
        source = self.root / 'attachments/source.docx'
        source.parent.mkdir()
        source.write_bytes(b'isolated original source bytes')
        media = self.root / '01_source/media'
        media.mkdir()
        recognized = {}
        for name, label in [('a.png', '基础巩固'), ('b.png', '能力提升')]:
            (media / name).write_bytes(name.encode())
            recognized[name] = {'label': label, 'image_sha256': hashlib.sha256(name.encode()).hexdigest(),
                                'evidence': {'method': 'harness_visual', 'visible_text': label}}
        artifact = {'version': lesson_bands.VERSION, 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                    'text_sha256': hashlib.sha256(self.body.encode()).hexdigest(),
                    'bands': lesson_bands._boundaries(self.body, recognized), 'issues': [], 'status': 'verified'}
        (self.root / lesson_bands.ARTIFACT).write_text(json.dumps(artifact), encoding='utf-8')
        self.assertEqual(lesson_bands.load_verified(self.root)['status'], 'verified')
        before = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        payload = answers_direct.build_payload(self.root)
        self.assertEqual(len(payload['answers']), 2)
        validate_scoped_answer(self.root, self.units[1], 1, 'B', ['附录:4'])
        # The confirmed section may already exclude the next verified banner.
        self.units[0]['source_section']['end_line'] = 4
        self.save()
        self.assertEqual(len(answers_direct.build_payload(self.root)['answers']), 2)
        validate_scoped_answer(self.root, self.units[0], 1, 'A', ['附录:2'])
        self.units[0]['source_section']['end_line'] = 3
        self.save()
        self.assert_rejected()
        self.units[0]['source_section']['end_line'] = 5
        self.save()
        after = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        artifact_path = self.root / lesson_bands.ARTIFACT
        baseline = copy.deepcopy(artifact)
        for broken in ('source', 'text', 'media', 'partial'):
            with self.subTest(broken=broken):
                artifact = copy.deepcopy(baseline)
                if broken == 'source':
                    artifact['source_sha256'] = 'stale'
                elif broken == 'text':
                    artifact['text_sha256'] = 'stale'
                elif broken == 'media':
                    (media / 'a.png').write_bytes(b'changed')
                else:
                    artifact.update(status='partial', issues=[{'code': 'BAND_VISUAL_UNAVAILABLE'}])
                artifact_path.write_text(json.dumps(artifact), encoding='utf-8')
                self.assert_rejected()
                (media / 'a.png').write_bytes(b'a.png')
        artifact_path.write_text(json.dumps(baseline), encoding='utf-8')
        self.appendix = self.appendix.replace('A 基础巩固', 'swap').replace('B 能力提升', 'A 基础巩固').replace('swap', 'B 能力提升')
        self.save()
        self.assert_rejected()

    def test_verified_visual_band_is_consumed_and_stale_claim_is_not_trusted(self):
        self.body = self.body.replace('A 基础巩固', '[IMG a.png]').replace('B 能力提升', '[IMG b.png]')
        self.save()
        from workflow import lesson_bands
        def annotated(sections, evidence):
            result = copy.deepcopy(sections)
            if evidence.get('status') == 'verified':
                result[0]['lesson_band'] = {'label': '能力提升'}
                result[1]['lesson_band'] = {'label': '基础巩固'}
            return result
        with patch.object(lesson_bands, 'load_verified', return_value={'status': 'verified'}) as loaded, \
                patch.object(lesson_bands, 'annotate_sections', side_effect=annotated):
            self.assert_rejected()
            self.assertEqual(loaded.call_args.args, (self.root,))
        # A saved unit claim alone must not stand in for current evidence.
        self.units[0]['source_section']['lesson_band'] = {'label': '能力提升'}
        with patch.object(lesson_bands, 'load_verified', return_value={'status': 'unavailable'}), \
                patch.object(lesson_bands, 'annotate_sections', side_effect=annotated):
            self.assertEqual(resolve_scoped_answer(self.root, self.units[0], 1)['answer'], 'A')

    def test_band_skeleton_requires_every_peer_printed_number(self):
        self.body = self.body.replace('1. Second?', '1. Second?\n2. Third?')
        self.units = assign_scoped([
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '单项填空',
             'source_span': {'start_line': 4, 'end_line': 4}},
            {'numbers': [1, 2], 'unit_kind': 'group', 'type': '单项填空',
             'source_span': {'start_line': 7, 'end_line': 8}},
        ], self.body)
        self.save()
        self.assert_rejected()
        self.appendix += '2.C\n'
        self.save()
        payload = answers_direct.build_payload(self.root)
        self.assertEqual(len(payload['answers']), 3)

    def test_compact_repeated_complete_same_scope_rows_are_equivalent(self):
        self.appendix = 'A 基础巩固\nⅠ.1.A\n1.A\nB 能力提升\nⅠ.1.B\n'
        self.save()
        result = resolve_scoped_answer(self.root, self.units[0], 1)
        self.assertEqual(result, {'answer': 'A', 'evidence': ['01_source/附录.txt:2', '01_source/附录.txt:3']})
        validate_scoped_answer(self.root, self.units[0], 1, 'A', ['附录:3'])

    def test_full_title_same_scope_duplicate_verbatim_rows_are_equivalent(self):
        self.appendix = ('第一课时 Section A\nA 基础巩固\nⅠ.单项填空\n1.A\n1.A\n'
                         'B 能力提升\nⅠ.单项填空\n1.B\n')
        self.save()
        result = resolve_scoped_answer(self.root, self.units[0], 1)
        self.assertEqual(result, {'answer': 'A', 'evidence': ['01_source/附录.txt:4', '01_source/附录.txt:5']})
        validate_scoped_answer(self.root, self.units[0], 1, 'A', ['附录:5'])
        self.appendix = self.appendix.replace('1.A\n1.A', '1.A\n1.C')
        self.save()
        self.assert_rejected(line=4)


class CompactAnswerCoverageTest(unittest.TestCase):
    def test_printed_anchor_order_is_source_order_not_strong_then_weak(self):
        from workflow.upload_content import _printed_item_anchors
        rows = ['Amy:Hi.1.<u>　　　</u>', 'B:2.____', 'Amy:Bye.3.<u>　　　</u>']
        self.assertEqual([n for _, n in _printed_item_anchors(rows)], [1, 2, 3])

    def test_verified_unnumbered_extension_before_pronunciation_is_bound(self):
        from workflow import lesson_bands
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '01_source').mkdir()
            body = ('第一课时 Section A\n[IMG a.png]\nⅠ.单项填空\n1.First?\n'
                    '[IMG b.png]\n补全对话\nAmy:Hello.1.<u>　　　</u>\n'
                    'B:2.____\nPronunciation\nⅠ.单项填空\n1.Sound?\n')
            units = assign_scoped([
                {'numbers': [1], 'unit_kind': 'standalone', 'type': '单项填空', 'source_span': {'start_line': 4, 'end_line': 4}},
                {'numbers': [1, 2], 'unit_kind': 'group', 'type': '补全对话', 'source_span': {'start_line': 7, 'end_line': 8}},
                {'numbers': [1], 'unit_kind': 'group', 'type': '语音题', 'source_span': {'start_line': 11, 'end_line': 11}},
            ], body)
            (root / '01_source/正文.txt').write_text(body, encoding='utf-8')
            (root / '01_source/附录.txt').write_text('基础巩固\nⅠ.1.A\n能力提升\n1~2 BC\nPronunciation\nⅠ.1.D\n', encoding='utf-8')
            (root / '01_source/structure.confirmed.json').write_text(json.dumps({'identity_version': 'scoped-v1', 'units': units}), encoding='utf-8')
            evidence = {'status': 'verified', 'bands': [
                {'band_id': 'a', 'label': '基础巩固', 'start_line': 2, 'end_line': 4},
                {'band_id': 'b', 'label': '能力提升', 'start_line': 5, 'end_line': 8}]}
            with patch.object(lesson_bands, 'load_verified', return_value=evidence):
                payload = answers_direct.build_payload(root)
                self.assertEqual(len(payload['answers']), 4)
                validate_scoped_answer(root, units[1], 2, 'C', ['附录:4'])
            self.assertIsNone(answers_direct.build_payload(root))

    def test_subblank_continuation_requires_every_source_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '01_source').mkdir()
            body = '第一课时 Section A\nⅠ.任务型阅读\n1. Read and fill.\n'
            unit = assign_scoped([{'numbers': [1], 'unit_kind': 'group', 'type': '任务型阅读',
                                   'source_span': {'start_line': 3, 'end_line': 3}}], body)[0]
            (root / '01_source/正文.txt').write_text(body, encoding='utf-8')
            (root / '01_source/附录.txt').write_text('Ⅰ.1.(1)one\n(2)two\n(3)three\n', encoding='utf-8')
            answer = '(1)one\n(2)two\n(3)three'
            result = resolve_scoped_answer(root, unit, 1)
            self.assertEqual(result, {'answer': answer, 'evidence': [f'01_source/附录.txt:{n}' for n in [1, 2, 3]]})
            for retained in (answer, answer.replace('\n', ' ')):
                with self.assertRaisesRegex(ValueError, 'scoped answer source'):
                    validate_scoped_answer(root, unit, 1, retained, ['附录:1'])
            validate_scoped_answer(root, unit, 1, answer, result['evidence'])


if __name__ == '__main__':
    unittest.main()
