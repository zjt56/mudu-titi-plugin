"""Execution observability without changing stage acceptance or user data."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from workflow import activity, runner
from workbench import server


class ActivityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        (self.batch / 'workflow').mkdir()

    def test_lifetime_cleanup_on_success_and_error(self):
        for fail in (False, True):
            try:
                with activity.track(self.batch, 'extract', '识别卷头'):
                    self.assertEqual(activity.read(self.batch)['stage'], 'extract')
                    if fail:
                        raise RuntimeError('test')
            except RuntimeError:
                pass
            self.assertEqual(activity.read(self.batch), {})

    def test_dead_process_and_corrupt_evidence_are_not_active(self):
        p = self.batch / activity.FILE
        p.write_text(json.dumps({'pid': os.getpid(), 'stage': 'extract', 'token': 'old'}))
        with patch.object(activity.os, 'kill', side_effect=ProcessLookupError):
            self.assertEqual(activity.read(self.batch), {})
        p.write_text('not-json')
        self.assertEqual(activity.read(self.batch), {})

    def test_old_owner_does_not_clear_new_owner(self):
        p = self.batch / activity.FILE
        with activity.track(self.batch, 'validate', '检查'):
            p.write_text(json.dumps({'pid': os.getpid(), 'stage': 'review_gate', 'token': 'next'}))
        self.assertEqual(activity.read(self.batch)['token'], 'next')

    def test_actual_gate_entry_is_visible_and_exception_cleans_activity(self):
        def gate(*args, **kwargs):
            self.assertEqual(activity.read(self.batch)['stage'], 'structure_gate')
            raise RuntimeError('gate-failure')
        # Decorator wraps before any transactional implementation; no manifest mutation.
        with patch('workflow.local_agent._agent_lock', side_effect=gate):
            with self.assertRaisesRegex(RuntimeError, 'gate-failure'):
                runner.do_structure_gate(self.batch, {})
        self.assertEqual(activity.read(self.batch), {})

    def test_cached_projection_gets_live_activity_and_runtime_without_mutation(self):
        cached = {'stage': {'items': [{'id': 'extract', 'status': 'completed', 'active': False}]}}
        with patch.object(server, 'advance_state', return_value={'running': True}), \
             patch.object(server, 'upload_state', return_value={'running': False}):
            with activity.track(self.batch, 'extract', '卷头识别仍在执行'):
                projected = server._live_overview(cached, 'fixture', self.batch)
                self.assertTrue(projected['stage']['items'][0]['active'])
                self.assertEqual(projected['stage']['hint'], '卷头识别仍在执行')
            projected = server._live_overview(cached, 'fixture', self.batch)
            self.assertFalse(projected['stage']['items'][0]['active'])
            self.assertFalse(cached['stage']['items'][0]['active'])
            self.assertNotIn('advance', cached)

    def test_cached_overview_refreshes_runtime_without_file_changes(self):
        cached = {'stage': {'items': []}}
        with patch.object(server, '_OVERVIEW_CACHE', {}), \
             patch.object(server, 'batch_dir', return_value=self.batch), \
             patch.object(server, '_overview_uncached', return_value=cached) as build, \
             patch.object(server, 'upload_state', return_value={}), \
             patch.object(server, 'advance_state', side_effect=[{'running': False}, {'running': True}]):
            self.assertFalse(server.overview('fixture')['advance']['running'])
            self.assertTrue(server.overview('fixture')['advance']['running'])
            build.assert_called_once()

    def test_format_report_and_receipt_are_in_overview_cache_key(self):
        before = server._overview_key(self.batch)
        (self.batch / '07_review').mkdir()
        (self.batch / '07_review/upload-format.json').write_text('{"passed":true}')
        after = server._overview_key(self.batch)
        self.assertNotEqual(before, after)
        (self.batch / 'receipt.json').write_text('{"stage":"pending_confirmation"}')
        self.assertNotEqual(after, server._overview_key(self.batch))
