"""Closed-loop recovery fixtures and tests; never use user batches or a live model."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import (agent_tasks, auto, contracts, format_gate, manifest as mf,
                      question_content, review, review_rounds, self_heal, supervisor)
from workflow.test_legacy_format import LegacyFormatTest


def make_runtime_batch(root):
    """An already-reviewed local fixture with a real legacy wire-format defect."""
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    fixture = LegacyFormatTest()
    fixture.root = root
    payload = fixture.fixture()
    units = agent_tasks.confirmed_units(root)
    by_numbers = {tuple(unit['numbers']): unit for unit in units}
    def put(relative, value):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    for row in payload['items']:
        uid = by_numbers[tuple(row['numbers'])]['unit_id']
        row['id'] = row['unit_id'] = uid
    for relative in ('06_batch/batch.json', '06_batch/batch_meta.base.json', '06_batch/batch_meta.json'):
        put(relative, payload)
    items = payload['items']
    put('01_source/extraction.json', {})
    put('01_source/metadata.json', {'paper_title': payload['name']})
    put('07_review/extraction-report.json', {'passed': True})
    put('07_review/structure-gate.json', {'passed': True})
    put('02_audio/audio_map.json', {'items': []})
    put('03_types/types.json', {'items': [dict(unit_id=row['unit_id'], type=row['question_type'],
        subtypes=row['filterMetadata']['subtypes'], words=20, contentComplete=True, evidence=['正文']) for row in items]})
    put('04_knowledge/knowledge.json', {'items': [dict(unit_id=row['unit_id'], knowledge=row['knowledge'],
        subquestions=[dict(number=child['number'], knowledge=child.get('knowledge', [])) for child in row['subquestions']], evidence=['正文']) for row in items]})
    put('04_knowledge/explanations.json', {'items': [dict(unit_id=row['unit_id'], explanation='无', articleSummary='无',
        subquestions=[dict(number=child['number'], explanation='无') for child in row['subquestions']], evidence=['正文未给解析']) for row in items]})
    numbers = [str(n) for unit in units for n in unit['numbers']]
    put('01_source_ans/answers.json', {'answers': {n: 'A' for n in numbers}, 'difficulty': {n: '适中' for n in numbers}})
    source_audit = question_content.audit_batch_source(root, payload)
    if not source_audit['passed']:
        raise AssertionError(source_audit)
    put('07_review/validation-report.json', {'passed': True, 'source_audit': source_audit})
    # Preserve actual portable task/response/receipt/transaction chains. These
    # deterministic fixture judgments are not claims that a live model reviewed.
    for _ in range(10):
        task = review_rounds.export_task(root)
        if task['action'] == 'complete':
            break
        if task['action'] != 'exported':
            raise AssertionError(task)
        binding = json.loads((root / ('workflow/review-bindings/' + task['task_id'] + '.json')).read_text())
        state = json.loads((root / binding['state_path']).read_text())
        checks = []
        for target in state['parts'][binding['part_index']]['targets']:
            uid = target['unit_id'] if isinstance(target, dict) else target
            unit = next(unit for unit in units if unit['unit_id'] == uid)
            row = next(row for row in items if row['unit_id'] == uid)
            assessment = dict(type=row['question_type'], subtypes=row['filterMetadata']['subtypes'], knowledge=row['knowledge'],
                subquestions=[dict(number=str(n), knowledge=[]) for n in unit['numbers']])
            check = dict(unit_id=uid, verdict='ok', rationale='隔离样例按原文核对。', assessment=assessment,
                evidence=[dict(path='01_source/正文.txt', locator='本单元', quote=unit['stem'].splitlines()[0])])
            if task['round_id'] == 'sample':
                check['number'] = target['number']
                assessment['source_comparison'] = '隔离样例逐项对照原文。'
            checks.append(check)
        review_rounds.import_result(root, task['task_id'], dict(status='completed',
            artifacts={'review_round': {'checks': checks}}, notes=[], findings=[]))
    if not review.evaluate_rounds(root)['execution_completed']:
        raise AssertionError(review.evaluate_rounds(root))
    put('07_review/review-gate.json', {'passed': False, 'execution_completed': True, 'reason': 'fixture retains quality boundary'})
    original = root / 'source.txt'
    original.write_bytes((root / '01_source/正文.txt').read_bytes())
    state = mf.new_manifest('recovery-execution-fixture', root,
        mf.register_input(root, 'source', original), {}, {'paper_title': payload['name']})
    for spec in contracts.STAGES:
        if spec.id in ('preassemble', 'audio', 'upload_gate', 'upload', 'publish_gate'):
            mf.touch_stage(state, spec.id, mf.SKIPPED)
        elif spec.id == 'format_gate':
            continue
        else:
            mf.touch_stage(state, spec.id, mf.COMPLETED,
                input_hashes=mf.input_hashes(root, state, spec), output_hashes=mf.output_hashes(root, spec))
            mf.record_semantics(root, state, spec)
    mf.touch_stage(state, 'review_gate', mf.BLOCKED, execution_completed=True, continue_unqualified=True,
                   quality_status='failed', release_ready=False)
    mf.record_semantics(root, state, contracts.STAGE_BY_ID['review_gate'])
    report = format_gate.run(root)
    if report['passed']:
        raise AssertionError('fixture must start with a real format error')
    mf.touch_stage(state, 'format_gate', mf.FAILED, notes=json.dumps(report['errors'], ensure_ascii=False))
    mf.save(root, state)
    return root


def model_plan(stage='format_gate', action='repair_upload_format'):
    return {'response': {'status': 'completed', 'artifacts': {'self_heal': {
        'stage': stage, 'action': action, 'reason': '源文已证明空材料块不含内容，执行格式修复后复检。'}}, 'notes': [], 'findings': []}}


class SelfHealRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='self-heal-runtime-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_ai_judgment_real_repair_real_format_resume_stops_supervisor(self):
        batch = make_runtime_batch(self.root / 'batches' / 'fixture')
        before = json.loads((batch / '06_batch/batch_meta.json').read_text())
        tasks_before = set((batch / 'workflow/review-tasks').iterdir())
        calls = []
        def dispatch(name):
            calls.append(name)
            with patch('workflow.local_agent.load_config', return_value={}), \
                    patch('workflow.local_agent.invoke', return_value=model_plan()) as invoke, \
                    patch.object(auto, '_parallel_ready_agents', side_effect=AssertionError('No retag wave')):
                outcome = auto.advance(batch, {}, stop_at='upload_gate')
            self.assertEqual(invoke.call_count, 1)
            return {'started': True, 'result': outcome}
        watch = supervisor.WorkflowSupervisor(batch.parent, dispatch, busy=lambda folder: False)
        watch.one_tick()
        self.assertEqual(calls, ['fixture'])
        state = mf.load(batch)
        # A blocking review_gate must not be bypassed to reach format_gate; the
        # format gate stays pending until review_gate truly passes.
        self.assertEqual(state['stages']['review_gate']['status'], mf.BLOCKED)
        self.assertFalse(state['stages']['review_gate']['release_ready'])
        self.assertNotEqual(state['stages']['format_gate']['status'], mf.COMPLETED)
        self.assertFalse(supervisor.local_complete(batch, state))
        after = json.loads((batch / '06_batch/batch_meta.json').read_text())
        self.assertEqual(after['items'][0]['knowledge'], before['items'][0]['knowledge'])
        self.assertEqual(after['items'][0]['subquestions'], before['items'][0]['subquestions'])
        self.assertEqual(set((batch / 'workflow/review-tasks').iterdir()), tasks_before)
        # A blocking review_gate is never bypassed, so no format repair is
        # claimed as applied and the supervisor must not re-dispatch forever.
        ledger = json.loads((batch / self_heal.LEDGER).read_text())
        self.assertNotEqual(state['stages']['format_gate']['status'], mf.COMPLETED)
        watch.one_tick()
        self.assertEqual(calls, ['fixture'])

    def test_failed_status_alone_does_not_bypass_repair_cooldown(self):
        root = self.root / 'unchanged-failure'
        root.mkdir()
        state = mf.new_manifest('unchanged-failure', root, {}, {})
        mf.touch_stage(state, 'extract', mf.FAILED, notes='same error')
        mf.save(root, state)
        inputs = mf.input_hashes(root, state, contracts.STAGE_BY_ID['extract'])
        self_heal._save(root, {'last_inputs': {'extract': inputs}, 'events': []})
        from contextlib import nullcontext
        for status in ('cooldown', 'retry_budget_exhausted'):
            with self.subTest(status=status), patch.object(self_heal, 'recover', return_value={
                    'changed': False, 'stage': 'extract', 'status': status}), \
                    patch('workflow.agent_protocol._agent_lock', side_effect=lambda *a, **k: nullcontext()), \
                    patch.object(auto.rn, '_execute_command') as execute:
                result = auto.advance(root, {}, stop_at='structure_gate')
            execute.assert_not_called()
            self.assertEqual(result['action'], 'cooldown')

    def test_provider_cooldown_does_not_invoke_repair_model(self):
        root = self.root / 'cooldown'
        root.mkdir()
        state = mf.new_manifest('cooldown', root, {}, {})
        mf.touch_stage(state, 'extract', mf.FAILED)
        mf.save(root, state)
        auto._set_cooldown(root, 60)
        with patch('workflow.local_agent.invoke', side_effect=AssertionError('cooldown must not call AI')):
            outcome = auto.advance(root, {}, stop_at='upload_gate')
        # 限流冷却只延后模型调用，不冻结不依赖模型的本地命令（AGENTS 硬规则2）；
        # 关键断言是全程没有调用模型（上面的 side_effect 会抛错）。
        self.assertNotEqual(outcome.get('action'), 'failed')
        self.assertFalse((root / self_heal.LEDGER).exists())

    def test_feedback_targets_ignore_unrelated_reports_and_support_explicit_custom_ids(self):
        root = self.root / 'targets'
        (root / '01_source').mkdir(parents=True)
        units = [dict(unit_id=uid, numbers=[n], unit_kind='standalone', stem='source')
                 for n, uid in enumerate(('q-1', 'q-2', 'custom.item-3'), 1)]
        (root / '01_source/structure.confirmed.json').write_text(json.dumps({'units': units}), encoding='utf-8')
        (root / '07_review').mkdir()
        (root / '07_review/upload-format.json').write_text(json.dumps({'errors': [{'unit_id': 'q-2'}]}), encoding='utf-8')
        self.assertEqual(self_heal._affected_units(root, 'q-1: Invalid knowledge labels', 'knowledge')[0], ['q-1'])
        self.assertEqual(self_heal._affected_units(root, 'custom.item-3: Invalid knowledge labels', 'knowledge')[0], ['custom.item-3'])
        self.assertEqual(self_heal._affected_units(root, 'No UID in this actual knowledge error', 'knowledge')[0], [])

    def test_local_upload_boundary_still_prepares_audio_but_early_boundary_does_not(self):
        batch = make_runtime_batch(self.root / 'audio-boundary')
        with patch.object(self_heal, 'recover', return_value={'changed': False, 'status': 'idle'}), \
                patch.object(auto, '_auto_prepare_audio', return_value={'status': 'no_audio'}) as prepare, \
                patch.object(auto.rn, '_execute_command', return_value=1):
            auto.advance(batch, {}, stop_at='audio')
            prepare.assert_not_called()
            auto.advance(batch, {}, stop_at='upload_gate')
            # audio is already terminally skipped in this fixture, so no new cut
            # preparation is triggered; the boundary must not re-run it.
            prepare.assert_not_called()

    def test_busy_worker_and_remote_submission_are_not_retried(self):
        root = self.root / 'busy'
        root.mkdir()
        state = mf.new_manifest('busy', root, {}, {})
        mf.touch_stage(state, 'extract', mf.FAILED)
        mf.save(root, state)
        with patch.object(self_heal, '_busy', return_value=True), patch('workflow.local_agent.invoke') as invoke:
            self.assertEqual(self_heal.recover(root)['status'], 'busy')
            invoke.assert_not_called()
        for spec in contracts.STAGES:
            mf.touch_stage(state, spec.id, mf.COMPLETED)
        mf.touch_stage(state, 'upload', mf.FAILED)
        mf.save(root, state)
        with patch('workflow.local_agent.invoke') as invoke:
            self.assertEqual(self_heal.recover(root)['status'], 'idle')
            invoke.assert_not_called()

    def test_model_plan_is_rejected_if_inputs_change_during_judgment(self):
        root = self.root / 'drift'
        root.mkdir()
        state = mf.new_manifest('drift', root, {}, {})
        mf.touch_stage(state, 'extract', mf.FAILED)
        mf.save(root, state)
        def change(*args, **kwargs):
            path = root / '01_source/正文.txt'
            path.parent.mkdir()
            path.write_text('New source while model was diagnosing', encoding='utf-8')
            return model_plan('extract', 'retry_stage')
        with patch('workflow.local_agent.load_config', return_value={}), patch('workflow.local_agent.invoke', side_effect=change):
            result = self_heal.recover(root)
        self.assertFalse(result['changed'])
        self.assertEqual(result['status'], 'inputs_changed')
        self.assertEqual(mf.load(root)['stages']['extract']['status'], mf.FAILED)


if __name__ == '__main__':
    unittest.main()
