"""A sole-topic Roman appendix may omit its topic, never its ownership proof."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from workflow import agent_tasks, answers_direct
from workflow.question_identity import assign_scoped
from workflow.upload_content import resolve_scoped_answer, validate_scoped_answer, validate_scoped_missing


class SoleTopicAnswersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        (self.batch / '01_source').mkdir()
        self.body = ('Unit 8 Our Town\n综合阅读提升\nⅠ.完形填空\n'
                     + ''.join(f'({chr(0x3000)}){n}. A.choice B.choice\n' for n in range(1, 11))
                     + 'Ⅱ.任务型阅读\nA shared passage.\n'
                     + ''.join(f'{n}. Read question {n}?\n' for n in range(1, 4))
                     + '☆拓展设问\n4. Your favourite place?\n')
        self.appendix = ('参考答案\nⅠ.1~5 ABCDA　6~10 BCDBA\n'
                         'Ⅱ.1.In the library.\n2.Forty people.\n'
                         '3.They can plant trees.\n☆拓展设问\n'
                         '4.The park.Because it is quiet.(言之有理即可)\n')
        self.units = assign_scoped([
            {'unit_kind': 'group', 'type': '完形填空', 'numbers': list(range(1, 11)),
             'source_span': {'start_line': 4, 'end_line': 13, 'basis': 'cleaned_source'}},
            {'unit_kind': 'group', 'type': '任务型阅读', 'numbers': [1, 2, 3, 4],
             'source_span': {'start_line': 15, 'end_line': 20, 'basis': 'cleaned_source'}},
        ], self.body)
        self.write_sources()

    def write_sources(self):
        (self.batch / '01_source/正文.txt').write_text(self.body, encoding='utf-8')
        (self.batch / '01_source/附录.txt').write_text(self.appendix, encoding='utf-8')
        (self.batch / '01_source/structure.confirmed.json').write_text(
            json.dumps({'identity_version': 'scoped-v1', 'units': self.units}), encoding='utf-8')

    def test_complete_fourteen_answers_and_retained_candidate_validate(self):
        payload = answers_direct.build_payload(self.batch)
        self.assertIsNotNone(payload)
        self.assertEqual(len(payload['answers']), 14)
        self.assertEqual(payload['missing'], {})
        for unit in self.units:
            for number in unit['numbers']:
                key = unit['question_ids'][str(number)]
                validate_scoped_answer(self.batch, unit, number,
                                       payload['answers'][key], payload['evidence'][key])
        key = self.units[1]['question_ids']['1']
        self.assertEqual(payload['answers'][key], 'In the library.')
        self.assertEqual(payload['evidence'][key], ['01_source/附录.txt:3'])
        agent_tasks._validate(self.batch, 'answers', copy.deepcopy(payload),
                              {'01_source/附录.txt': self.appendix}, self.units)
        self.assertEqual(answers_direct.build_payload(self.batch, payload), payload)
        with self.assertRaisesRegex(ValueError, 'missing contradicts'):
            validate_scoped_missing(self.batch, self.units[1], 1)

    def test_partial_peer_cannot_prove_any_section_by_elimination(self):
        self.appendix = self.appendix.replace('1~5 ABCDA　6~10 BCDBA', '1~5 ABCDA')
        self.write_sources()
        self.assertIsNone(answers_direct.build_payload(self.batch))
        self.assertIsNone(resolve_scoped_answer(self.batch, self.units[1], 1))
        with self.assertRaises(ValueError):
            validate_scoped_missing(self.batch, self.units[1], 1)

    def test_explicit_wrong_topic_and_repeated_topic_reject(self):
        original = self.appendix
        for label in ('Pronunciation', '单元话题写作'):
            with self.subTest(label=label):
                self.appendix = label + '\n' + original
                self.write_sources()
                self.assertIsNone(answers_direct.build_payload(self.batch))
        self.appendix = original
        for label in ('综合阅读提升', 'Pronunciation'):
            with self.subTest(body_topic=label):
                original_body = self.body
                self.body = self.body.replace('Ⅱ.任务型阅读', label + '\nⅡ.任务型阅读')
                # Rebind to current coordinates so rejection proves the topic
                # rule, rather than an unrelated stale span/section identity.
                from workflow.sync_practice import heading_candidates
                sections = heading_candidates(self.body)
                self.units = assign_scoped([
                    dict(unit, source_span={'start_line': section['start_line'] + 1,
                                            'end_line': section['end_line'],
                                            'basis': 'cleaned_source'})
                    for unit, section in zip(self.units, sections)
                ], self.body)
                self.write_sources()
                self.assertIsNone(answers_direct.build_payload(self.batch))
                self.body = original_body

    def test_explicit_matching_topic_still_binds(self):
        self.appendix = '综合阅读提升\n' + self.appendix
        self.write_sources()
        self.assertEqual(len(answers_direct.build_payload(self.batch)['answers']), 14)

    def test_coordinate_value_and_scope_drift_still_reject(self):
        unit = self.units[1]
        for answer, evidence in (('In the library.', ['附录:4']),
                                 ('In the park.', ['附录:3'])):
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                validate_scoped_answer(self.batch, unit, 1, answer, evidence)
        drifted = copy.deepcopy(unit)
        drifted['source_section']['occurrence'] += 1
        self.assertIsNone(resolve_scoped_answer(self.batch, drifted, 1))


if __name__ == '__main__':
    unittest.main()
