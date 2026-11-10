"""Shared-state regressions: no live batches, services, or model calls."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import auto, contracts, execution_state as state, manifest as mf
from workbench import server


class ExecutionStateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='execution-state-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = mf.new_manifest('state', self.root, {}, {})

    def finish_local(self):
        for sid in state.LOCAL_STAGES:
            self.manifest['stages'][sid]['status'] = mf.COMPLETED
        final = self.root / '06_batch/batch_meta.json'
        final.parent.mkdir(parents=True)
        final.write_text('{"items": []}')
        report = self.root / '07_review/upload-format.json'
        report.parent.mkdir(parents=True)
        report.write_text(json.dumps({'passed': True, 'final_sha256': hashlib.sha256(final.read_bytes()).hexdigest()}))
        return final

    def test_legacy_public_statuses_never_certify_without_artifact_context(self):
        self.assertEqual(mf.TERMINAL_OK, ('completed', 'approved', 'skipped'))
        for status in ('pending', 'running', 'completed', 'approved', 'needs_review', 'blocked', 'failed', 'invalidated', 'skipped'):
            record = dict(status=status, execution_completed=True, continue_unqualified=True)
            self.manifest['stages']['extract'] = record
            self.assertEqual(state.executed(record), status in mf.TERMINAL_OK)
            self.assertFalse(mf.dependency_satisfied(self.manifest, 'extract'))
            self.assertFalse(mf.dependency_satisfied(self.manifest, 'extract', allow_unqualified=True))

    def test_review_differences_show_pending_confirmation_not_reviewing(self):
        # 规则 6：审核差异是产出。执行完成 + 差异待人工 + 未放行时，本地流程
        # 已完成，入口状态应显示“待确认/可上传”，而不是永久“审核中”。
        self.finish_local()
        self.manifest['stages']['review_gate'].update(
            status=mf.NEEDS_REVIEW, execution_completed=True,
            continue_unqualified=True, release_ready=False)
        (self.root / '07_review/review-gate.json').write_text('{"passed": false}')
        self.assertEqual(mf.derive_status(self.manifest), 'pending_confirmation')
        self.assertTrue(state.local_complete(self.root, self.manifest))
        before = copy.deepcopy(self.manifest)
        progress = state.projection(self.manifest, stage_ids=state.LOCAL_STAGES)
        self.assertEqual(progress['done'], len(state.LOCAL_STAGES))
        self.assertIsNone(progress['current'])
        strict = state.projection(self.manifest, stage_ids=state.LOCAL_STAGES, allow_unqualified=False)
        self.assertFalse(strict['execution_completed'])
        self.assertEqual(strict['current'], 'review_gate')
        self.assertEqual(before, self.manifest)
        # 差异没有被当作质量通过：release_ready 仍为 False。
        self.assertFalse(self.manifest['stages']['review_gate'].get('release_ready'))

    def test_review_success_does_not_finish_a_pending_format_gate(self):
        self.finish_local()
        self.manifest['stages']['format_gate']['status'] = mf.PENDING
        self.assertEqual(mf.derive_status(self.manifest), 'validating')
        self.assertFalse(state.local_complete(self.root, self.manifest))

    def test_quality_difference_does_not_unlock_downstream_or_confirmation(self):
        self.finish_local()
        self.manifest['stages']['review_gate'].update(status=mf.BLOCKED, execution_completed=True,
            continue_unqualified=True, quality_status='failed', release_ready=False)
        (self.root / '07_review/review-gate.json').write_text('{"passed": false}')
        before = copy.deepcopy(self.manifest)
        projection = state.projection(self.manifest, stage_ids=state.LOCAL_STAGES)
        self.assertFalse(projection['execution_completed'])
        self.assertEqual(projection['quality'], 'differences')
        self.assertEqual(projection['quality_stages'], ['review_gate'])
        self.assertFalse(state.local_complete(self.root, self.manifest))
        self.assertFalse(server.final_format_complete(self.root, self.manifest))
        self.assertEqual(mf.derive_status(self.manifest), 'blocked')
        self.assertEqual(before, self.manifest)

    def test_final_report_requires_exact_current_file_and_all_local_stages(self):
        final = self.finish_local()
        self.assertTrue(state.local_complete(self.root, self.manifest))
        final.write_bytes(final.read_bytes() + b' ')
        self.assertFalse(state.local_complete(self.root, self.manifest))
        self.manifest['stages'].pop('review')
        self.assertFalse(state.local_complete(self.root, self.manifest))

    def test_retained_descendants_are_not_discarded_during_upstream_repair(self):
        self.finish_local()
        self.manifest['stages']['types'].update(status=mf.PENDING, rebuild_required=True)
        self.assertEqual(self.manifest['stages']['knowledge']['status'], mf.COMPLETED)
        self.assertFalse(mf.dependency_satisfied(self.manifest, 'knowledge'))
        projection = state.projection(self.manifest, stage_ids=state.LOCAL_STAGES)
        before_types = state.LOCAL_STAGES[:state.LOCAL_STAGES.index('types')]
        self.assertEqual(projection['done'], len(before_types))
        self.assertFalse(state.local_complete(self.root, self.manifest))

    def test_ui_and_scheduler_agree_on_rebuild_even_with_retained_completed_status(self):
        self.finish_local()
        self.manifest['stages']['structure_gate']['rebuild_required'] = True
        before = copy.deepcopy(self.manifest)
        cards = server.stage_cards(self.manifest)
        projection = state.projection(self.manifest)
        self.assertEqual(cards['done'], projection['done'])
        self.assertEqual(cards['current']['id'], 'structure_gate')
        self.assertEqual(cards['current']['status'], mf.INVALIDATED)
        self.assertEqual(self.manifest, before)
        self.assertFalse(state.local_complete(self.root, self.manifest))

    def test_uploaded_batch_keeps_its_remote_status(self):
        self.manifest['stages']['upload']['status'] = mf.COMPLETED
        self.assertEqual(mf.derive_status(self.manifest), 'staged')
        self.manifest['stages']['publish_gate']['status'] = mf.APPROVED
        self.assertEqual(mf.derive_status(self.manifest), 'published')


class BoundedPollingTest(unittest.TestCase):
    def test_stop_boundary_finishes_without_touching_remote_stages(self):
        manifest = {'stages': {sid: {'status': mf.COMPLETED} for sid in state.LOCAL_STAGES}}
        with patch.object(auto, 'advance', return_value={'status': 'pending_confirmation'}) as advance, \
             patch.object(auto.mf, 'load', return_value=manifest), \
             patch.object(auto.mf, 'dependency_satisfied', return_value=True), \
             patch.object(auto.time, 'sleep') as sleep:
            result = auto.poll_batch(Path('/unused'), {}, stop_at='upload_gate', max_polls=3)
        self.assertEqual(result['polls'], 1)
        advance.assert_called_once()
        sleep.assert_not_called()
        self.assertNotIn('upload', manifest['stages'])

    def test_failed_gate_and_busy_passes_yield_instead_of_spinning(self):
        for action in ('failed', 'gate', 'busy', 'cooldown'):
            with self.subTest(action=action), \
                 patch.object(auto, 'advance', return_value={'status': 'failed', 'action': action}) as advance, \
                 patch.object(auto.mf, 'load', return_value={'stages': {}}), \
                 patch.object(auto.time, 'sleep') as sleep:
                result = auto.poll_batch(Path('/unused'), {}, interval=5, max_polls=3)
                self.assertEqual(result['polls'], 3)
                self.assertEqual(advance.call_count, 3)
                self.assertEqual(sleep.call_count, 2)

    def test_unknown_boundary_fails_before_any_execution(self):
        with patch.object(auto, 'advance') as advance:
            with self.assertRaises(ValueError):
                auto.poll_batch(Path('/unused'), {}, stop_at='not-a-stage')
        advance.assert_not_called()


if __name__ == '__main__':
    unittest.main()
