"""Structure cache routes and independent optional visual evidence builders."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from workflow import runner, structure_ai


class StructureVisualBindingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        source = self.batch / '01_source'
        source.mkdir()
        (source / '正文.txt').write_text('一、单项选择\n1. Test? A. Yes B. No\n', encoding='utf-8')
        (source / 'structure.json').write_text('{"sections": []}', encoding='utf-8')
        self.invoke = Mock(return_value={'status': 'completed', 'artifacts': {'structure': {'units': []}}})

    def classify(self, config=None, **kwargs):
        return structure_ai.auto_classify(self.batch, invoke_fn=self.invoke, config=config, force=True, **kwargs)

    def test_injected_invoker_binds_full_config_without_external_route(self):
        with patch('workflow.visual_transport.route_identity', side_effect=AssertionError('no bridge for injected invoker')):
            self.classify({'model': 'A', 'settings': {'temperature': 0, 'endpoint': 'local'}})
            same = self.classify({'settings': {'endpoint': 'local', 'temperature': 0}, 'model': 'A'})
            changed = self.classify({'model': 'B', 'settings': {'temperature': 0, 'endpoint': 'local'}})
            changed_settings = self.classify({'model': 'B', 'settings': {'temperature': 1, 'endpoint': 'local'}})
        self.assertTrue(same['reused'])
        self.assertFalse(changed['reused'])
        self.assertFalse(changed_settings['reused'])
        self.assertEqual(self.invoke.call_count, 3)
        self.assertTrue(self.invoke.call_args.args[0].startswith('Stage: structure_gate\nAgent: structure_gate\n'))
        marker = json.loads((self.batch / structure_ai.CLASSIFY_MARKER).read_text())
        self.assertEqual(len(marker['route_sha256']), 64)

    def test_default_route_changes_reject_cache_and_route_errors_never_fallback(self):
        config = {'provider': 'command', 'command': ['/test/wf-router.sh']}
        route = {'provider': 'test', 'model': 'A', 'profile': 'device'}
        with patch('workflow.agent.configured', return_value=True), \
             patch('workflow.local_agent.load_config', return_value=config), \
             patch('workflow.local_agent.invoke', self.invoke), \
             patch('workflow.visual_transport.route_identity', return_value=route) as selected:
            structure_ai.auto_classify(self.batch, force=True)
            same = structure_ai.auto_classify(self.batch, force=True)
            self.assertTrue(same['reused'])
            route['model'] = 'B'
            changed = structure_ai.auto_classify(self.batch, force=True)
            self.assertFalse(changed['reused'])
            self.assertEqual(self.invoke.call_count, 2)
            selected.assert_called_with(stage='structure_gate')
            marker_before = (self.batch / structure_ai.CLASSIFY_MARKER).read_bytes()
            selected.side_effect = RuntimeError('invalid explicit route')
            rejected = structure_ai.auto_classify(self.batch, force=True)
            self.assertFalse(rejected['ran'])
            self.assertIn('invalid explicit route', rejected['reason'])
            self.assertEqual(self.invoke.call_count, 2)
            self.assertEqual((self.batch / structure_ai.CLASSIFY_MARKER).read_bytes(), marker_before)

    def test_generic_command_runs_real_protocol_without_harness_route(self):
        from contextlib import nullcontext
        import sys
        script = self.batch / 'fake_agent.py'
        script.write_text('import sys\nsys.stdin.read()\nprint(' + repr(json.dumps(
            self.invoke.return_value)) + ')\n', encoding='utf-8')
        config = {'provider': 'command', 'command': [sys.executable, str(script)], 'timeout_seconds': 10}
        with patch('workflow.visual_transport.route_identity', side_effect=RuntimeError('Harness unavailable')) as route, \
             patch('workflow.local_agent._agent_lock', side_effect=lambda *a, **kw: nullcontext()):
            first = structure_ai.auto_classify(self.batch, config=config, force=True)
            second = structure_ai.auto_classify(self.batch, config=config, force=True)
        self.assertTrue(first['ran'])
        self.assertFalse(first['reused'])
        self.assertTrue(second['reused'])
        route.assert_not_called()

    def test_direct_codex_uses_config_binding_without_harness_route(self):
        import sys
        config = {'provider': 'codex', 'executable': sys.executable, 'model': 'explicit-model'}
        with patch('workflow.visual_transport.route_identity', side_effect=RuntimeError('Harness unavailable')) as route, \
             patch('workflow.local_agent.invoke', self.invoke):
            first = structure_ai.auto_classify(self.batch, config=config, force=True)
            second = structure_ai.auto_classify(self.batch, config=config, force=True)
        self.assertTrue(first['ran'])
        self.assertTrue(second['reused'])
        route.assert_not_called()

    def test_direct_dsh_binds_actual_harness_route(self):
        import sys
        config = {'provider': 'dsh', 'executable': sys.executable}
        with patch('workflow.visual_transport.route_identity', return_value={
                'provider': 'test', 'model': 'A', 'profile': 'device'}) as route, \
             patch('workflow.local_agent.invoke', self.invoke):
            result = structure_ai.auto_classify(self.batch, config=config, force=True)
        self.assertTrue(result['ran'])
        route.assert_called_once_with(stage='structure_gate')

    def test_explicit_bad_config_does_not_load_default_or_accept_cached_result(self):
        self.classify()
        with patch('workflow.local_agent.load_config') as load, \
             patch('workflow.local_agent.invoke') as invoke, \
             patch('workflow.visual_transport.route_identity') as route:
            result = structure_ai.auto_classify(self.batch, config={'provider': 'invalid'}, force=True)
        self.assertFalse(result['ran'])
        load.assert_not_called()
        invoke.assert_not_called()
        route.assert_not_called()

    def test_legacy_marker_without_route_cannot_claim_current_route(self):
        self.classify({'model': 'A'})
        marker_path = self.batch / structure_ai.CLASSIFY_MARKER
        marker = json.loads(marker_path.read_text())
        marker.pop('route_sha256')
        marker_path.write_text(json.dumps(marker), encoding='utf-8')
        result = self.classify({'model': 'B'})
        self.assertFalse(result['reused'])
        self.assertEqual(self.invoke.call_count, 2)

    def test_failed_band_builder_does_not_skip_other_visual_builders(self):
        with patch('workflow.lesson_bands.build', side_effect=RuntimeError('band failed')), \
             patch('workflow.visual_review.build') as review, \
             patch('workflow.visual_crosscheck.build') as cross:
            runner._ensure_visual_evidence(self.batch)
        review.assert_called_once_with(self.batch)
        cross.assert_called_once_with(self.batch)

    def test_failed_image_review_does_not_skip_crosscheck(self):
        with patch('workflow.lesson_bands.build') as bands, \
             patch('workflow.visual_review.build', side_effect=RuntimeError('image review failed')), \
             patch('workflow.visual_crosscheck.build') as cross:
            runner._ensure_visual_evidence(self.batch)
        bands.assert_called_once_with(self.batch)
        cross.assert_called_once_with(self.batch)

    def test_existing_visual_artifacts_remain_reusable(self):
        for name in ('image_semantics.json', 'source_crosscheck.json'):
            (self.batch / '01_source' / name).write_text('{}', encoding='utf-8')
        with patch('workflow.lesson_bands.build') as bands, \
             patch('workflow.visual_review.build') as review, \
             patch('workflow.visual_crosscheck.build') as cross:
            runner._ensure_visual_evidence(self.batch)
        bands.assert_called_once_with(self.batch)
        review.assert_not_called()
        cross.assert_not_called()


if __name__ == '__main__':
    unittest.main()
