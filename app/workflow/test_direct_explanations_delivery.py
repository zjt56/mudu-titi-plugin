"""Direct extraction must pass the ordinary bound import and consumer checks."""
import copy
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from workflow import agent, agent_tasks, explanations_direct, manifest as mf, upload_content


class DirectExplanationDeliveryTest(unittest.TestCase):
    def setUp(self):
        from workflow.test_agent_protocol import AgentProtocolTest
        self.fixture = AgentProtocolTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.batch = self.fixture.batch
        self.fixture.write('01_source/附录.txt',
                           '1. A [IMG left.jpeg]解析[IMG right.jpeg]主语是第三人称单数，谓语用 reads。\n')
        self.fixture.mark_chain(('preassemble', 'types', 'answers'))

    def run_direct(self):
        with redirect_stdout(io.StringIO()):
            return agent.main(['run', '--stage', 'explanations', '--batch-dir', str(self.batch)])

    def test_icon_heading_runs_through_bound_import_without_model(self):
        with patch('workflow.local_agent.invoke', side_effect=AssertionError('must not call model')):
            self.assertEqual(self.run_direct(), 0)
        path = self.batch / agent_tasks.ARTIFACTS['explanations']
        payload = json.loads(path.read_text())
        self.assertEqual(payload['items'][0]['explanation'], '主语是第三人称单数，谓语用 reads。')
        report = upload_content.inspect_content(self.batch, {
            'items': [dict(payload['items'][0], question_type='单项填空', numbers=[1])]}, strict=True)
        self.assertTrue(report['passed'], report)
        self.assertEqual(mf.load(self.batch)['stages']['explanations']['status'], mf.COMPLETED)
        bindings = [json.loads(p.read_text()) for p in
                    (self.batch / 'workflow/agent-bindings').glob('*.json')]
        self.assertTrue(any(b.get('stage') == 'explanations' and b.get('status') == 'accepted'
                            for b in bindings), bindings)

    def test_direct_copy_marker_cannot_authorize_omitting_original_content(self):
        candidate = explanations_direct.build_payload(self.batch)
        candidate['items'][0]['explanation'] = '无'
        before = mf.load(self.batch)['stages']['explanations']['status']
        with patch.object(explanations_direct, 'build_payload', return_value=copy.deepcopy(candidate)), \
             self.assertRaisesRegex(ValueError, 'ORIGINAL_EXPLANATION_OMITTED'):
            self.run_direct()
        self.assertFalse((self.batch / agent_tasks.ARTIFACTS['explanations']).exists())
        self.assertEqual(mf.load(self.batch)['stages']['explanations']['status'], before)

    def test_old_metadata_projection_is_rejected_after_explanation_refill(self):
        from workflow import handoff
        self.assertEqual(self.run_direct(), 0)
        # A structurally present old final view must not count as delivery when
        # it still omits an explanation accepted by the producer.
        self.fixture.write('06_batch/batch_meta.base.json', {'items': [{
            'unit_id': 'q-1', 'explanation': '无', 'articleSummary': '无', 'subquestions': []}]})
        report = handoff.inspect_stage(self.batch, mf.load(self.batch), 'meta')
        self.assertFalse(report['passed'])
        self.assertIn('preserve the explanation artifact field explanation', report['errors'][0]['message'])
        from workflow import contracts
        current = json.loads((self.batch / agent_tasks.ARTIFACTS['explanations']).read_text())
        assembled = json.loads((self.batch / '06_batch/batch.base.json').read_text())
        assembled['items'][0].update(current['items'][0])
        self.fixture.write('06_batch/batch.json', assembled)
        manifest = mf.load(self.batch)
        for sid in contracts.upstream_of('meta'):
            mf.touch_stage(manifest, sid, mf.COMPLETED)
        mf.touch_stage(manifest, 'meta', mf.COMPLETED)
        stale, reason = mf.is_stale(self.batch, manifest, contracts.STAGE_BY_ID['meta'])
        self.assertTrue(stale)
        self.assertIn('解析/大意交付未同步', reason)
        changed = mf.invalidate_changed_consumers(self.batch, manifest, 'explanations')
        self.assertIn('meta', changed)
        current = json.loads((self.batch / agent_tasks.ARTIFACTS['explanations']).read_text())
        self.fixture.write('06_batch/batch_meta.base.json', current)
        mf.touch_stage(manifest, 'meta', mf.COMPLETED)
        self.assertEqual(mf.is_stale(self.batch, manifest, contracts.STAGE_BY_ID['meta']), (False, ''))

    def test_changed_source_after_binding_refuses_installation(self):
        original = explanations_direct.build_payload
        count = 0

        def changing_source(batch):
            nonlocal count
            count += 1
            if count == 2:
                self.fixture.write('01_source/附录.txt', '1. A 【解析】已改变的原文。\n')
            return original(batch)

        with patch.object(explanations_direct, 'build_payload', side_effect=changing_source), \
             self.assertRaisesRegex(ValueError, 'stale'):
            self.run_direct()
        self.assertFalse((self.batch / agent_tasks.ARTIFACTS['explanations']).exists())
