"""Execution observability without changing stage acceptance or user data."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
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
        with patch.object(activity, '_process_alive', side_effect=ProcessLookupError):
            self.assertEqual(activity.read(self.batch), {})
        p.write_text('not-json')
        self.assertEqual(activity.read(self.batch), {})

    def test_windows_query_never_sends_console_control_signal(self):
        p = self.batch / activity.FILE
        p.write_text(json.dumps({'pid': 321, 'stage': 'extract', 'token': 'owner'}))
        native = Mock(SYNCHRONIZE=0x100000, WAIT_TIMEOUT=258)
        native.OpenProcess.return_value = 123
        with patch.object(activity, '_IS_WINDOWS', True), \
             patch.dict('sys.modules', {'_winapi': native}), \
             patch.object(activity.os, 'kill') as kill:
            for wait_result, alive in ((258, True), (0, False), (0xFFFFFFFF, False)):
                native.WaitForSingleObject.return_value = wait_result
                self.assertEqual(bool(activity.read(self.batch)), alive)
            kill.assert_not_called()
        native.OpenProcess.assert_called_with(0x100000, False, 321)
        native.WaitForSingleObject.assert_called_with(123, 0)
        self.assertEqual(native.CloseHandle.call_count, 3)

    def test_windows_query_failure_is_inactive_and_closes_open_handle(self):
        p = self.batch / activity.FILE
        p.write_text(json.dumps({'pid': 321, 'stage': 'extract', 'token': 'owner'}))
        native = Mock(SYNCHRONIZE=0x100000, WAIT_TIMEOUT=258)
        native.OpenProcess.return_value = 123
        native.WaitForSingleObject.side_effect = OSError('query failed')
        with patch.object(activity, '_IS_WINDOWS', True), \
             patch.dict('sys.modules', {'_winapi': native}), \
             patch.object(activity.os, 'kill') as kill:
            self.assertEqual(activity.read(self.batch), {})
            native.CloseHandle.assert_called_once_with(123)
            native.CloseHandle.reset_mock()
            native.OpenProcess.side_effect = PermissionError('access denied')
            self.assertEqual(activity.read(self.batch), {})
            native.CloseHandle.assert_not_called()
            kill.assert_not_called()

    def test_posix_query_preserves_signal_zero_semantics(self):
        with patch.object(activity, '_IS_WINDOWS', False), \
             patch.object(activity.os, 'kill') as kill:
            self.assertTrue(activity._process_alive(321))
            kill.assert_called_once_with(321, 0)
            kill.side_effect = ProcessLookupError
            with self.assertRaises(ProcessLookupError):
                activity._process_alive(321)

    @unittest.skipUnless(os.name == 'nt', 'native Windows process-handle integration')
    def test_native_windows_live_and_exited_processes_without_kill(self):
        import subprocess
        import sys
        child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'],
                                 stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        try:
            with patch.object(activity.os, 'kill') as kill:
                self.assertTrue(activity._process_alive(child.pid))
                child.communicate(timeout=10)
                try:
                    alive = activity._process_alive(child.pid)
                except OSError:
                    alive = False
                self.assertFalse(alive)
                kill.assert_not_called()
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=10)

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
