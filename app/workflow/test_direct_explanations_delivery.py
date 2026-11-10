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

    def test_omission_constraints_reach_full_portable_and_partition_prompts(self):
        from workflow import agent_protocol, unit_tasks
        from pathlib import Path
        task = agent_tasks.build_task(self.batch, 'explanations', max_input_chars=1000000)
        exported = agent_protocol.export_task(self.batch, 'explanations', max_input_chars=1000000)
        portable = json.loads((Path(exported['package_dir']) / 'task.json').read_text())
        _, parts = unit_tasks.build_parts(self.batch, 'explanations', 1000000, max_units=1)
        self.assertTrue(parts)
        for prompt in [task['prompt'], portable['prompt'], *[part['prompt'] for part in parts]]:
            head = prompt.split('BEGIN SUPPLIED CONTENT')[0]
            for constraint in ('防遗漏交付硬约束', '[IMG ...]解析[IMG ...]',
                               'ORIGINAL_EXPLANATION_OMITTED', 'ORIGINAL_SUMMARY_OMITTED',
                               '完整题号集合', '同一来源/归属/遗漏校验'):
                self.assertIn(constraint, head)
        self.assertIn('不得改答案、题型、知识点', parts[0]['prompt'])

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

    def test_scoped_repair_also_uses_model_free_direct_copy(self):
        # 局部修复请求（scoped）此前会绕过直抄路径，于是来源类失败（解析/大意未
        # 逐字绑定原题）只能交给模型反复重写；直抄既保真又零模型开销，必须优先。
        units = agent_tasks.confirmed_units(self.batch)
        unit_id = units[0]['unit_id']
        feedback = {'version': 1, 'stage': 'explanations',
                    'reason': '解析未逐字绑定原题',
                    'error': 'EXPLANATION_SOURCE_UNVERIFIED',
                    'unit_ids': [unit_id],
                    'feedback_by_unit': {unit_id: {
                        'reason': '解析未逐字绑定原题',
                        'error': 'EXPLANATION_SOURCE_UNVERIFIED'}},
                    'all_units': False, 'scoped_units': True, 'attempt': 2}
        (self.batch / 'workflow/repair-feedback').mkdir(parents=True, exist_ok=True)
        self.fixture.write('workflow/repair-feedback/explanations.json', feedback)
        with patch('workflow.local_agent.invoke', side_effect=AssertionError('must not call model')):
            self.assertEqual(self.run_direct(), 0)
        self.assertEqual(mf.load(self.batch)['stages']['explanations']['status'], mf.COMPLETED)
        payload = json.loads((self.batch / agent_tasks.ARTIFACTS['explanations']).read_text())
        self.assertTrue(payload['items'])

    def test_scoped_repair_falls_back_to_the_task_path_when_copy_is_unavailable(self):
        units = agent_tasks.confirmed_units(self.batch)
        unit_id = units[0]['unit_id']
        feedback = {'version': 1, 'stage': 'explanations',
                    'reason': '解析未逐字绑定原题',
                    'error': 'EXPLANATION_SOURCE_UNVERIFIED',
                    'unit_ids': [unit_id],
                    'feedback_by_unit': {unit_id: {
                        'reason': '解析未逐字绑定原题',
                        'error': 'EXPLANATION_SOURCE_UNVERIFIED'}},
                    'all_units': False, 'scoped_units': True, 'attempt': 2}
        (self.batch / 'workflow/repair-feedback').mkdir(parents=True, exist_ok=True)
        self.fixture.write('workflow/repair-feedback/explanations.json', feedback)
        # 直抄不接受（例如来源不可唯一绑定）时不得把局部修复变成硬失败，
        # 仍须回到通用任务路径让受影响题号带反馈继续。
        with patch.object(explanations_direct, 'install', return_value=None) as install:
            code = self.run_direct()
        # 走到通用任务路径即算通过：本机 fixture 没有可执行的 Agent CLI，返回码
        # 取决于本机配置；关键是直抄被判不可用后没有冒充成功。
        self.assertEqual(install.call_count, 1, '局部修复也必须先尝试直抄')
        # 直抄不可用时的返回码取决于本机 Agent 是否可执行；这里只钉住「不得假成功」：
        # 报成功就必须真有产物，报失败就不得留下半成品。
        artifact = self.batch / agent_tasks.ARTIFACTS['explanations']
        if code == 0:
            self.assertTrue(artifact.exists(), '返回成功必须真的交付产物')
        else:
            self.assertFalse(artifact.exists(), '失败时不得留下半成品')

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
