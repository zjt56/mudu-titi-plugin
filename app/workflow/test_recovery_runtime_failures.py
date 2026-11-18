"""Regression of 2019 route failure: no content writes or diagnostic model loop."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import manifest as mf, self_heal


class RuntimeRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name).resolve() / 'batch'
        self.batch.mkdir()
        self.manifest = mf.new_manifest('batch', self.batch, {}, {})
        mf.touch_stage(self.manifest, 'answers', mf.FAILED, notes='route failure')
        mf.save(self.batch, self.manifest)

    def test_runtime_failure_rejects_all_retry_aliases_even_with_unit_ids(self):
        detail = json.dumps({'error_code': 'native_route_identity_missing',
                             'error': 'route identity missing', 'unit_id': 'q-1'})
        before = (self.batch / 'workflow/manifest.json').read_bytes()
        for action in ('record_only', 'retry_stage', 'retry_stage_with_fresh_context',
                       'refresh_inputs', 'upstream_rederive', 'defer_and_continue'):
            with self.subTest(action=action), self.assertRaisesRegex(
                    ValueError, 'content_runtime_requires_code_repair'):
                self_heal._apply(self.batch, self.manifest, 'answers', detail,
                                 {'action': action, 'reason': 'repair route'}, 1, {'files': {}})
        self.assertEqual((self.batch / 'workflow/manifest.json').read_bytes(), before)
        self.assertFalse((self.batch / 'workflow/repair-feedback').exists())

    def test_structured_config_failure_is_not_a_content_retry(self):
        for code, expected in (('native_route_identity_missing', 'content_runtime_requires_code_repair'),
                               ('integration', 'content_runtime_requires_code_repair'),
                               ('config', 'content_runtime_requires_code_repair')):
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, expected):
                self_heal._content_retry_scope(self.batch, 'knowledge', json.dumps(
                    {'error_code': code, 'error': 'dispatch diagnostic'}), unit_ids=['q-1'])

    def test_record_only_with_a_real_scope_does_not_requeue_content(self):
        with patch.object(self_heal, '_retry_admission', return_value=['q-1']), \
             patch.object(self_heal, '_feedback') as feedback, \
             patch.object(self_heal, '_requeue') as requeue:
            result = self_heal._apply(self.batch, self.manifest, 'answers', 'q-1 unclear',
                {'action': 'record_only', 'reason': 'no proven repair'}, 1, {'files': {}})
        self.assertFalse(result['changed'])
        self.assertEqual(result['escalation'], 'code_repair')
        feedback.assert_not_called()
        requeue.assert_not_called()

    def test_repeated_runtime_failure_escalates_without_diagnostic_models(self):
        route = {'provider': 'provider-a', 'model': 'model-a'}
        with patch.object(self_heal, '_candidate', return_value=(
                'answers', json.dumps({'error_code': 'native_route_identity_missing',
                                       'error': 'route identity missing', 'unit_id': 'q-1'}))), \
             patch.object(self_heal, '_busy', return_value=False), \
             patch('workflow.local_agent._selected_content_route', return_value=route), \
             patch('workflow.operations._code_fingerprint', return_value='code-a'), \
             patch.object(self_heal, '_plan') as plan:
            first = self_heal.recover(self.batch)
            second = self_heal.recover(self.batch)
            with patch('workflow.local_agent._selected_content_route', return_value={'provider': 'provider-b', 'model': 'model-b'}):
                changed = self_heal.recover(self.batch)
        self.assertEqual(first['outcome'], 'repair_not_applicable')
        self.assertEqual(second['escalation'], 'code_repair')
        self.assertFalse(second['changed'])
        self.assertNotEqual(first['signature'], changed['signature'])
        plan.assert_not_called()
        self.assertEqual(mf.load(self.batch)['stages']['answers']['status'], mf.FAILED)
        self.assertEqual(len(self_heal._load(self.batch)['events']), 2)
