"""Shared entry paths with real import/validation and no external model calls."""
import contextlib
import io
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import agent, agent_protocol, agent_tasks, auto, contracts, explanations_direct
from workflow import label_execution, local_agent, manifest as mf, scoped_regeneration, stage_preflight
from workflow.test_audio_stage_fastpath import _batch
from workflow import test_label_execution as label_fixtures
import tempfile


def isolated_protocol_lock(test):
    """Keep fixture threads coordinated without the repository-wide live lock."""
    mutex = threading.RLock()
    @contextlib.contextmanager
    def lock(*args, **kwargs):
        with mutex:
            yield
    guard = patch.object(agent_protocol, '_agent_lock', side_effect=lock)
    guard.start()
    test.addCleanup(guard.stop)


class StagePreflightTest(unittest.TestCase):
    def setUp(self):
        isolated_protocol_lock(self)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.batch = _batch(Path(self.temp.name)).resolve()
        evidence = json.loads((self.batch / '02_audio/segment-evidence.json').read_text())
        self.put('02_audio/audio_map.json', {'unit_binding': evidence['unit_binding'], 'items': [
            {k: row[k] for k in ('unit_id', 'file', 'start', 'end')} for row in evidence['items']]})
        self.config = {'max_input_chars': 10000, 'retries': 0}
        # These tests exercise actual audio validation and protocol input binding.
        # Delivery readiness itself has independent coverage below.
        self.ready = patch.object(mf, 'dependency_satisfied', return_value=True)
        self.ready.start()
        self.addCleanup(self.ready.stop)
        for target in (local_agent, label_execution):
            name = 'invoke' if target is local_agent else 'run'
            guard = patch.object(target, name, side_effect=AssertionError('unexpected model call'))
            guard.start()
            self.addCleanup(guard.stop)

    def put(self, relative, value):
        path = self.batch / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def parallel(self, *stages):
        return auto._parallel_ready_agents(self.batch,
            [contracts.STAGE_BY_ID[stage] for stage in stages], self.config)

    def test_parallel_current_audio_is_delivered_without_export_or_model(self):
        paths = [self.batch / '02_audio' / name for name in
                 ('audio_map.json', 'segment-evidence.json', 'seg-q-1.mp3')]
        before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
        with patch.object(agent_protocol, 'export_task', side_effect=AssertionError('no model task')):
            self.assertEqual(self.parallel('audio'), {'audio': 0})
        self.assertEqual([(p.read_bytes(), p.stat().st_mtime_ns) for p in paths], before)
        self.assertEqual(mf.load(self.batch)['stages']['audio']['status'], mf.COMPLETED)
        self.assertTrue(mf.dependency_satisfied(mf.load(self.batch), 'audio'))

    def test_parallel_insufficient_audio_never_reaches_model_or_completion(self):
        path = self.batch / '02_audio/segment-evidence.json'
        document = json.loads(path.read_text())
        document['items'][0]['verified'] = False
        self.put('02_audio/segment-evidence.json', document)
        before = path.read_bytes()
        with patch.object(agent_protocol, 'export_task', side_effect=AssertionError('no model task')):
            self.assertEqual(self.parallel('audio'), {'audio': 3})
        record = mf.load(self.batch)['stages']['audio']
        self.assertEqual(record['status'], mf.NEEDS_REVIEW)
        self.assertEqual(record['output_hashes'], {})
        self.assertEqual(path.read_bytes(), before)

    def test_live_segmenter_prevents_completion_without_waiting_for_model(self):
        with stage_preflight._segment_snapshot_lock(self.batch) as acquired:
            self.assertTrue(acquired)
            self.assertEqual(self.parallel('audio'), {'audio': 3})
        self.assertNotEqual(mf.load(self.batch)['stages']['audio']['status'], mf.COMPLETED)

    def test_completed_audio_is_revalidated_on_explicit_repeat(self):
        self.assertEqual(stage_preflight.run(self.batch, 'audio')[0], 0)
        self.assertEqual(stage_preflight.run(self.batch, 'audio')[0], 0)
        (self.batch / '02_audio/seg-q-1.mp3').write_bytes(b'changed cut')
        self.assertEqual(stage_preflight.run(self.batch, 'audio')[0], 3)

    def test_missing_mapping_cannot_be_marked_completed(self):
        (self.batch / '02_audio/audio_map.json').unlink()
        self.assertTrue(stage_preflight.audio_evidence_ready(self.batch))
        code, report = stage_preflight.run(self.batch, 'audio')
        self.assertEqual(code, 3)
        self.assertIn('Missing required delivery', report['reason'])
        self.assertEqual(mf.load(self.batch)['stages']['audio']['status'], mf.PENDING)

    def test_unready_upstream_cannot_be_marked_completed(self):
        with patch.object(mf, 'dependency_satisfied', return_value=False):
            code, report = stage_preflight.run(self.batch, 'audio')
        self.assertEqual(code, 3)
        self.assertIn('Prerequisite', report['reason'])
        self.assertEqual(mf.load(self.batch)['stages']['audio']['status'], mf.PENDING)

    def test_current_input_change_during_validation_rejects_completion(self):
        validate = stage_preflight.audio_evidence_ready
        def changed(batch):
            result = validate(batch)
            with (self.batch / '01_source/正文.txt').open('a', encoding='utf-8') as stream:
                stream.write('\n新增原文')
            return result
        with patch.object(stage_preflight, 'audio_evidence_ready', side_effect=changed):
            code, report = stage_preflight.run(self.batch, 'audio')
        self.assertEqual(code, 3)
        self.assertIn('输入或映射已变化', report['reason'])
        self.assertEqual(mf.load(self.batch)['stages']['audio']['status'], mf.PENDING)

    def test_validation_and_recording_share_exclusive_lock(self):
        inside = []
        @contextlib.contextmanager
        def lock(*args, **kwargs):
            self.assertFalse(kwargs.get('shared', False))
            inside.append(True)
            try:
                yield
            finally:
                inside.pop()
        validate = stage_preflight.audio_evidence_ready
        save = mf.save
        def checked(batch):
            self.assertTrue(inside)
            return validate(batch)
        def saved(batch, state):
            self.assertTrue(inside)
            return save(batch, state)
        with patch.object(agent_protocol, '_agent_lock', side_effect=lock), \
             patch.object(stage_preflight, 'audio_evidence_ready', side_effect=checked), \
             patch.object(mf, 'save', side_effect=saved):
            self.assertEqual(stage_preflight.run(self.batch, 'audio')[0], 0)
        self.assertFalse(inside)

    def test_scoped_audio_and_dry_run_leave_normal_path_available(self):
        with patch.object(scoped_regeneration, 'requested', return_value=True), \
             patch.object(stage_preflight, 'audio_evidence_ready', side_effect=AssertionError('whole-map shortcut')):
            self.assertIsNone(stage_preflight.run(self.batch, 'audio'))
        with patch.object(scoped_regeneration, 'requested', side_effect=AssertionError('dry run')):
            self.assertIsNone(stage_preflight.run(self.batch, 'audio', dry_run=True))
        self.assertEqual(mf.load(self.batch)['stages']['audio']['status'], mf.PENDING)

    def test_ordinary_entry_uses_shared_preflight(self):
        with patch.object(stage_preflight, 'run', return_value=(3, {'completed': False})) as preflight, \
             patch.object(local_agent, 'main', side_effect=AssertionError('no CLI')), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(agent.main(['run', '--stage', 'audio', '--batch-dir', str(self.batch)]), 3)
        preflight.assert_called_once_with(self.batch, 'audio', dry_run=False)

    def test_ordinary_fallback_workers_still_overlap(self):
        barrier = threading.Barrier(2, timeout=3)
        entered = set()
        def exported(batch, stage, **kwargs):
            package = self.batch / 'packages' / stage
            package.mkdir(parents=True)
            (package / 'task.json').write_text(json.dumps({'prompt': stage + '\nFILE PROTOCOL: test'}))
            return {'status': 'ready', 'package_dir': str(package), 'task_id': stage}
        def invoke(prompt, directory, config, **kwargs):
            self.assertTrue(kwargs['permit_parallel'])
            entered.add(prompt)
            barrier.wait()
            return {'response': {'status': 'completed'}}
        # Without evidence, audio follows its existing normal fallback. Knowledge
        # is an independent ordinary model task in the same scheduler wave.
        (self.batch / '02_audio/segment-evidence.json').unlink()
        with patch.object(agent_protocol, 'export_task', side_effect=exported), \
             patch.object(agent_tasks, 'build_task', side_effect=lambda b, s, **kw: {'status': 'ready', 'prompt': s}), \
             patch.object(agent_tasks, 'accept_result'), \
             patch.object(agent_protocol, 'import_result', return_value={'completed': True}), \
             patch.object(local_agent, 'invoke', side_effect=invoke) as model:
            self.assertEqual(self.parallel('audio', 'knowledge'), {'audio': 0, 'knowledge': 0})
        self.assertEqual(entered, {'audio', 'knowledge'})
        self.assertEqual(model.call_count, 2)


class ExplanationsPreflightTest(unittest.TestCase):
    def setUp(self):
        isolated_protocol_lock(self)
        self.fixture = label_fixtures.LabelExecutionTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.batch = self.fixture.batch.resolve()
        self.fixture.put(agent_tasks.ARTIFACTS['answers'], {
            'answers': {'1': 'A', '2': 'B'}, 'evidence': {'1': ['附录第1题'], '2': ['附录第2题']}, 'missing': {}})
        mf.save(self.batch, mf.new_manifest('direct-test', self.batch, {}, {}))
        ready = patch.object(mf, 'dependency_satisfied', return_value=True)
        ready.start()
        self.addCleanup(ready.stop)
        for target, name in ((local_agent, 'invoke'), (local_agent, 'main'), (label_execution, 'run')):
            guard = patch.object(target, name, side_effect=AssertionError('unexpected model call'))
            guard.start()
            self.addCleanup(guard.stop)

    def test_parallel_direct_copy_uses_real_protocol_with_zero_model_calls(self):
        original = self.batch / '01_source/附录.txt'
        before = original.read_bytes()
        with patch.object(agent_protocol, 'import_result', wraps=agent_protocol.import_result) as importer:
            outcome = auto._parallel_ready_agents(self.batch, [contracts.STAGE_BY_ID['explanations']],
                                                 {'max_input_chars': 10000, 'retries': 0})
        self.assertEqual(outcome, {'explanations': 0})
        self.assertEqual(importer.call_count, 1)
        items = json.loads((self.batch / agent_tasks.ARTIFACTS['explanations']).read_text())['items']
        self.assertEqual([item['explanation'] for item in items], ['第一题原解析。', '第二题原解析。'])
        self.assertEqual(original.read_bytes(), before)
        self.assertEqual(mf.load(self.batch)['stages']['explanations']['status'], mf.COMPLETED)

    def test_mixed_parallel_wave_submits_only_model_work(self):
        real_export = agent_protocol.export_task
        real_build = agent_tasks.build_task
        real_import = agent_protocol.import_result
        real_accept = agent_tasks.accept_result
        exports = []
        def export(batch, stage, **kwargs):
            exports.append(stage)
            if stage == 'explanations':
                return real_export(batch, stage, **kwargs)
            package = self.batch / 'mixed-packages' / stage
            package.mkdir(parents=True)
            (package / 'task.json').write_text(json.dumps({'prompt': stage + '\nFILE PROTOCOL: test'}))
            return {'status': 'ready', 'task_id': stage, 'package_dir': str(package)}
        def build(batch, stage, **kwargs):
            return (real_build(batch, stage, **kwargs) if stage == 'explanations'
                    else {'status': 'ready', 'prompt': stage})
        def accept(batch, stage, *args, **kwargs):
            if stage == 'explanations':
                return real_accept(batch, stage, *args, **kwargs)
        def install(batch, task_id, path):
            return ({'completed': True} if task_id == 'knowledge'
                    else real_import(batch, task_id, path))
        with patch.object(agent_protocol, 'export_task', side_effect=export), \
             patch.object(agent_tasks, 'build_task', side_effect=build), \
             patch.object(agent_tasks, 'accept_result', side_effect=accept), \
             patch.object(agent_protocol, 'import_result', side_effect=install), \
             patch.object(local_agent, 'invoke', return_value={'response': {'status': 'completed'}}) as model:
            outcome = auto._parallel_ready_agents(self.batch,
                [contracts.STAGE_BY_ID[s] for s in ('explanations', 'knowledge')],
                {'max_input_chars': 10000, 'retries': 0})
        self.assertEqual(outcome, {'explanations': 0, 'knowledge': 0})
        self.assertEqual(exports, ['explanations', 'knowledge'])
        model.assert_called_once()
        self.assertEqual(model.call_args.args[0], 'knowledge')

    def test_ordinary_direct_copy_shares_real_import_path(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(agent.main(['run', '--stage', 'explanations', '--batch-dir', str(self.batch)]), 0)
        self.assertEqual(json.loads(output.getvalue())['model_calls'], 0)

    def test_source_change_after_export_is_rejected_before_completion(self):
        export = agent_protocol.export_task
        def changed(*args, **kwargs):
            result = export(*args, **kwargs)
            self.fixture.put('01_source/附录.txt', '1. A 【解析】改变的原解析。\n2. B 【解析】第二题原解析。')
            return result
        with patch.object(agent_protocol, 'export_task', side_effect=changed):
            outcome = auto._parallel_ready_agents(self.batch, [contracts.STAGE_BY_ID['explanations']],
                                                 {'max_input_chars': 10000, 'retries': 0})
        self.assertEqual(outcome, {'explanations': 1})
        self.assertFalse((self.batch / agent_tasks.ARTIFACTS['explanations']).exists())
        self.assertNotEqual(mf.load(self.batch)['stages']['explanations']['status'], mf.COMPLETED)

    def test_scoped_direct_rejection_falls_back_without_swallowing_full_errors(self):
        with patch.object(scoped_regeneration, 'requested', return_value=True), \
             patch.object(explanations_direct, 'install', side_effect=ValueError('scoped importer rejected')):
            self.assertIsNone(stage_preflight.run(self.batch, 'explanations'))
        with patch.object(scoped_regeneration, 'requested', return_value=False), \
             patch.object(explanations_direct, 'install', side_effect=ValueError('source invalid')):
            with self.assertRaisesRegex(ValueError, 'source invalid'):
                stage_preflight.run(self.batch, 'explanations')


if __name__ == '__main__':
    unittest.main()
