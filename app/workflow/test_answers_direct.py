"""Scoped answer direct delivery: real protocol, temporary fixtures, no model."""
import contextlib
import copy
import json
import unittest
from unittest.mock import patch

from workflow import agent_protocol, agent_tasks, answers_direct, local_agent
from workflow import manifest as mf, scoped_regeneration, stage_preflight
from workflow import test_scoped_lesson_answers as fixtures


class AnswersDirectTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CompactLessonAnswersTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.batch, self.units = self.fixture.batch, self.fixture.units
        self.put('01_source/structure.json', {'sections': [], 'groups': []})
        mf.save(self.batch, mf.new_manifest('direct-answers', self.batch, {}, {}))
        guard = patch.object(mf, 'dependency_satisfied', return_value=True)
        guard.start()
        self.addCleanup(guard.stop)
        guard = patch.object(agent_protocol, '_agent_lock', side_effect=lambda *a, **k: contextlib.nullcontext())
        guard.start()
        self.addCleanup(guard.stop)
        guard = patch.object(local_agent, 'invoke', side_effect=AssertionError('unexpected model call'))
        guard.start()
        self.addCleanup(guard.stop)

    def put(self, relative, value):
        path = self.batch / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def snapshot(self):
        return {str(p.relative_to(self.batch)): p.read_bytes() for p in self.batch.rglob('*') if p.is_file()}

    def test_build_is_read_only_complete_and_evidence_bound(self):
        before = self.snapshot()
        payload = answers_direct.build_payload(self.batch)
        self.assertEqual(len(payload['answers']), 6)
        self.assertEqual(payload['missing'], {})
        self.assertEqual(self.snapshot(), before)
        context, _, missing, units = agent_tasks._collect(self.batch, 'answers')
        self.assertFalse(missing)
        agent_tasks._validate(self.batch, 'answers', payload, context, units)

    def test_wrapped_practice_key_banner_preserves_repeated_section_ownership(self):
        self.fixture.appendix = self.fixture.appendix.replace(
            'Ⅰ.1.C', '[IMG image85.jpg]课时精练[IMG image86.jpg]\nⅠ.1.C')
        self.fixture.write_sources()
        payload = answers_direct.build_payload(self.batch)
        self.assertIsNotNone(payload)
        self.assertEqual(list(payload['answers'].values()), ['A', 'B', 'nice', 'Unit', 'C', 'D'])

    def test_retained_candidate_keeps_value_but_not_claimed_coordinates(self):
        payload = answers_direct.build_payload(self.batch)
        key = next(iter(payload['answers']))
        payload['answers'][key] = ' **A**. '
        payload['evidence'][key] = ['invented scope line 999']
        recovered = answers_direct.build_payload(self.batch, payload)
        self.assertEqual(recovered['answers'][key], ' **A**. ')
        self.assertEqual(recovered['evidence'][key], ['01_source/附录.txt:2'])
        payload['answers'][key] = 'C'
        self.assertIsNone(answers_direct.build_payload(self.batch, payload))

    def test_retained_unknown_identity_and_absence_are_not_recovered(self):
        for payload in ({'answers': {'1': 'A'}}, {'answers': {}, 'missing': {'unknown': ['未提供']}}):
            self.assertIsNone(answers_direct.build_payload(self.batch, payload))

    def test_incomplete_conflicting_or_drifted_sources_decline_without_writes(self):
        for appendix in ('Ⅰ.1.A\nⅡ.1.nice\nⅠ.1.C\n', 'Ⅰ.1.A\n1.C\nⅡ.1.nice　2.Unit\nⅠ.1.C　2.D\n'):
            self.fixture.appendix = appendix
            self.fixture.write_sources()
            before = self.snapshot()
            self.assertIsNone(answers_direct.build_payload(self.batch))
            self.assertIsNone(stage_preflight.run(self.batch, 'answers'))
            self.assertEqual(self.snapshot(), before)

    def test_legacy_does_not_use_scoped_answer_fast_path(self):
        document = {'units': copy.deepcopy(self.units)}
        for unit in document['units']:
            unit.pop('identity_version')
        # Legacy confirmed loader rejects duplicate numbers; still decline safely.
        self.put('01_source/structure.confirmed.json', document)
        self.assertIsNone(answers_direct.build_payload(self.batch))
        self.fixture.write_sources()

    def test_legacy_section_proof_delivers_and_conflict_only_dispatches_unresolved_unit(self):
        from workflow import label_execution
        body = 'Ⅰ.关键词语选择(共2小题)\n1. Pick one.\n2. Pick another.\nⅡ.书面表达\n3. Write.'
        raw = self.batch / '01_source/正文.txt'
        raw.write_text(body, encoding='utf-8')
        original = self.batch / 'original.txt'
        original.write_text(body, encoding='utf-8')
        state = mf.load(self.batch)
        state['inputs']['source'] = {'path': str(original), 'exists': True}
        mf.save(self.batch, state)
        units = [{'unit_id': f'q-{n}', 'unit_kind': 'standalone', 'numbers': [n],
                  'type': '书面表达' if n == 3 else '听力关键词语选择'} for n in (1, 2, 3)]
        self.put('01_source/structure.confirmed.json', {'units': units})
        self.put('01_source/structure.json', {'sections': [
            {'source_line': 1, 'questions': [1, 2]}, {'source_line': 4, 'questions': [3]}]})
        self.put('07_review/structure-gate.json', {
            'passed': True, 'original_sha256': mf.sha256_file(original),
            'input_hashes': {key: mf.sha256_file(self.batch / key) for key in (
                '01_source/正文.txt', '01_source/structure.json', '01_source/structure.confirmed.json')},
        })
        appendix = self.batch / '01_source/附录.txt'
        appendix.write_text('Ⅰ.短文理解\n1.A\n2.B\nⅡ.书面表达\n1.D\n', encoding='utf-8')
        self.assertIsNone(answers_direct.build_payload(self.batch))
        appendix.write_text('Ⅰ.关键词语选择\n1.A\n2.B\nⅡ.书面表达\n1.D\n', encoding='utf-8')
        self.assertEqual(answers_direct.build_payload(self.batch)['answers'], {'1': 'A', '2': 'B'})
        code, _ = stage_preflight.run(self.batch, 'answers')
        self.assertEqual(code, 0)  # Real protocol import, no model.

        appendix.write_text('Ⅰ.关键词语选择\n1.A\n2.B\n2.C\nⅡ.书面表达\n1.D\n', encoding='utf-8')
        self.assertIsNone(answers_direct.build_payload(self.batch))

        def invoke(prompt, *_args, **_kwargs):
            self.assertIn('Return ONLY these exact unit IDs: ["q-2"]', prompt)
            return {'response': {'status': 'completed', 'notes': [], 'findings': [], 'artifacts': {
                agent_tasks.ARTIFACTS['answers']: {'answers': {'2': 'B'},
                    'evidence': {'2': ['01_source/附录.txt:3']}, 'missing': {}}}}}

        report = label_execution.run(self.batch, 'answers', {'max_input_chars': 180000},
                                     self.batch / 'workflow/test-legacy-partial', invoke=invoke)
        self.assertEqual(report['response']['artifacts'][agent_tasks.ARTIFACTS['answers']]['answers'],
                         {'1': 'A', '2': 'B'})

    def scope_first_unit(self):
        uid = self.units[0]['unit_id']
        self.put('workflow/repair-feedback/answers.json', {
            'version': 1, 'stage': 'answers', 'reason': '仅修首组证据', 'error': '坐标需恢复',
            'unit_ids': [uid], 'feedback_by_unit': {uid: {'reason': '仅修首组证据', 'error': '坐标需恢复'}},
            'all_units': False, 'scoped_units': True, 'attempt': 1})

    def test_scoped_direct_recovery_submits_only_selected_and_keeps_baseline(self):
        payload = answers_direct.build_payload(self.batch)
        self.put(agent_tasks.ARTIFACTS['answers'], payload)
        self.scope_first_unit()
        from workflow.question_identity import question_key
        chosen_keys = {question_key(self.units[0], n) for n in self.units[0]['numbers']}
        seen = []
        original = agent_protocol.import_result
        def imported(batch, task, path):
            response = json.loads(path.read_text())
            seen.append(set(response['result']['artifacts'][agent_tasks.ARTIFACTS['answers']]['answers']))
            return original(batch, task, path)
        with patch.object(agent_protocol, 'import_result', side_effect=imported):
            code, report = stage_preflight.run(self.batch, 'answers')
        self.assertEqual(code, 0)
        self.assertEqual(report['model_calls'], 0)
        self.assertEqual(seen, [chosen_keys])
        self.assertEqual(json.loads((self.batch / agent_tasks.ARTIFACTS['answers']).read_text()), payload)

    def test_scoped_direct_recovery_refuses_invalid_untouched_baseline(self):
        self.scope_first_unit()
        payload = answers_direct.build_payload(self.batch)
        key = next(key for key, value in payload['answers'].items() if value == 'nice')
        payload['answers'][key] = 'wrong value'
        self.put(agent_tasks.ARTIFACTS['answers'], payload)
        before = (self.batch / agent_tasks.ARTIFACTS['answers']).read_bytes()
        self.assertIsNone(answers_direct.install(self.batch))
        self.assertEqual((self.batch / agent_tasks.ARTIFACTS['answers']).read_bytes(), before)

    def test_dry_run_writes_nothing_and_does_not_export(self):
        before = self.snapshot()
        with patch.object(agent_protocol, 'export_task', side_effect=AssertionError('dry run exported')):
            self.assertIsNone(stage_preflight.run(self.batch, 'answers', dry_run=True))
        self.assertEqual(self.snapshot(), before)

    def test_real_protocol_import_delivers_once_with_zero_model_calls(self):
        before = (self.batch / '01_source/附录.txt').read_bytes()
        with patch.object(agent_protocol, 'import_result', wraps=agent_protocol.import_result) as importer:
            code, report = stage_preflight.run(self.batch, 'answers')
        self.assertEqual(code, 0)
        self.assertEqual(report['model_calls'], 0)
        self.assertEqual(importer.call_count, 1)
        installed = json.loads((self.batch / agent_tasks.ARTIFACTS['answers']).read_text(encoding='utf-8'))
        self.assertEqual(len(installed['answers']), 6)
        self.assertEqual(mf.load(self.batch)['stages']['answers']['status'], mf.COMPLETED)
        self.assertEqual((self.batch / '01_source/附录.txt').read_bytes(), before)

    def test_source_change_after_export_cannot_complete(self):
        export = agent_protocol.export_task
        def changed(*args, **kwargs):
            result = export(*args, **kwargs)
            self.fixture.appendix = self.fixture.appendix.replace('AB', 'CB')
            self.fixture.write_sources()
            return result
        with patch.object(agent_protocol, 'export_task', side_effect=changed):
            with self.assertRaises(ValueError):
                stage_preflight.run(self.batch, 'answers')
        self.assertFalse((self.batch / agent_tasks.ARTIFACTS['answers']).exists())
        self.assertNotEqual(mf.load(self.batch)['stages']['answers']['status'], mf.COMPLETED)


if __name__ == '__main__':
    unittest.main()
