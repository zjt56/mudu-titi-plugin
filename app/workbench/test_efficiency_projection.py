"""Temporary fixtures only: live state must not rebuild heavy projections."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workbench import server
from workflow import contracts, operations


class EfficiencyProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.batch = self.root / 'paper'
        (self.batch / 'workflow').mkdir(parents=True)
        self.manifest = {'batch_dir': str(self.batch), 'stages': {
            s.id: {'status': 'completed'} for s in contracts.STAGES}}
        self.write('workflow/manifest.json', self.manifest)
        self.addCleanup(patch.stopall)
        patch.object(server, 'WORKFLOWS', self.root).start()

    def write(self, name, value):
        path = self.batch / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def test_runtime_updates_keep_heavy_projection_and_overlay_fresh(self):
        with patch.object(server, 'batch_dir', return_value=self.batch), \
             patch.object(server, '_OVERVIEW_CACHE', {}), \
             patch.object(server, '_overview_uncached', return_value={'rows': [], 'diagnostics': None}) as build, \
             patch.object(server, 'advance_state', side_effect=lambda *_: {'running': False, 'self_heal': server.load(self.batch / 'workflow/self-heal-status.json', {})}), \
             patch.object(server, 'upload_state', return_value={}), \
             patch('workflow.activity.read', return_value=None):
            before = server.overview('paper')
            self.write('workflow/self-heal-status.json', {'status': 'diagnosing'})
            self.write('workflow/supervisor.json', {'next_retry_at': 20})
            self.write('workflow/activity.json', {'stage': 'knowledge'})
            after = server.overview('paper')
            self.assertEqual(build.call_count, 1)
            self.assertEqual(before['advance']['self_heal'], {})
            self.assertEqual(after['advance']['self_heal']['status'], 'diagnosing')
            self.write('06_batch/batch_meta.json', {'items': [{'changed': True}]})
            server.overview('paper')
            self.assertEqual(build.call_count, 2, 'content still invalidates the projection')

    def test_work_hint_is_read_only_and_ignores_terminal_incidents(self):
        identity = operations._identity(self.batch)
        with patch.object(server.wf_execution_state, 'local_complete', return_value=True), \
             patch.object(operations, 'view', side_effect=AssertionError('discovery forbidden')), \
             patch.object(operations, '_save', side_effect=AssertionError('ledger write forbidden')):
            for status in ['resolved', 'cancelled', 'needs_human', 'needs_deep_repair']:
                self.write('workflow/operations.json', {'identity': identity, 'incident': {'status': status}})
                self.assertFalse(server.operations_work_state()['has_work'], status)
            for status in ['queued', 'claimed', 'awaiting_execution']:
                self.write('workflow/operations.json', {'identity': identity, 'incident': {'status': status}})
                self.assertTrue(server.operations_work_state()['has_work'], status)

    def test_pending_batch_needs_inspection_without_hashing_final_or_discovering(self):
        self.manifest['stages']['extract']['status'] = 'pending'
        self.write('workflow/manifest.json', self.manifest)
        with patch.object(server.wf_execution_state, 'local_complete', side_effect=AssertionError('unnecessary hash')), \
             patch.object(operations, 'view', side_effect=AssertionError('discovery forbidden')):
            self.assertEqual(server.operations_work_state(), {'has_work': True, 'batches': ['paper']})
            self.manifest['status'] = 'stopped'
            self.write('workflow/manifest.json', self.manifest)
            self.assertFalse(server.operations_work_state()['has_work'])


if __name__ == '__main__':
    unittest.main()
