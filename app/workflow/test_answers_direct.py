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

    def test_legacy_and_scoped_repair_do_not_use_full_stage_fast_path(self):
        document = {'units': copy.deepcopy(self.units)}
        for unit in document['units']:
            unit.pop('identity_version')
        # Legacy confirmed loader rejects duplicate numbers; still decline safely.
        self.put('01_source/structure.confirmed.json', document)
        self.assertIsNone(answers_direct.build_payload(self.batch))
        self.fixture.write_sources()
        with patch.object(scoped_regeneration, 'requested', return_value=True):
            self.assertIsNone(stage_preflight.run(self.batch, 'answers'))
            self.assertIsNone(answers_direct.install(self.batch))

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
