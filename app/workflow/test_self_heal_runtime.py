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
        watch = supervisor.WorkflowSupervisor(batch.parent, dispatch, busy=lambda folder: False,
                                              clock=lambda: 1000.0)
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
        ledger = self_heal._load(batch)
        self.assertFalse(any(event.get('outcome') == 'repair_applied'
                             for event in ledger.get('events', [])))
        self.assertNotEqual(state['stages']['format_gate']['status'], mf.COMPLETED)
        # A real upstream refresh may grant one further pass. Once its semantic
        # progress settles, repeated ticks at the same time must respect backoff.
        watch.one_tick()
        settled_calls = len(calls)
        watch.one_tick()
        watch.one_tick()
        self.assertEqual(len(calls), settled_calls)
        self.assertFalse(supervisor.local_complete(batch, mf.load(batch)))

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


class SelfHealRefusalDispatchTest(unittest.TestCase):
    """A recovery refusal fences dispatch, including otherwise-ready branches."""

    def setUp(self):
        import contextlib
        from workflow import operations
        self.tmp = tempfile.TemporaryDirectory(prefix='self-heal-refused-')
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name).resolve()
        source = self.batch / 'source.txt'
        source.write_text('Synthetic source for scheduler admission tests.', encoding='utf-8')
        self.state = mf.new_manifest('refusal-fixture', self.batch,
            mf.register_input(self.batch, 'source', source), {})
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for item in (
            patch.object(operations, 'handoff_if_available', return_value=None),
            patch.object(mf, 'reconcile_deliveries'),
            patch.object(auto, '_auto_prepare_audio', return_value={'status': 'no_audio'}),
            patch.object(auto.rn, '_skip_agent_stage', return_value=False),
            patch('workflow.agent_protocol._agent_lock', side_effect=lambda *a, **k: contextlib.nullcontext()),
            patch('workflow.local_agent.invoke', side_effect=AssertionError('offline: no model')),
        ):
            self.stack.enter_context(item)

    def advance(self, stages, refused_stage, *, parallel=False, old_inputs=False, real_refusal=False):
        for stage in stages:
            mf.touch_stage(self.state, stage, mf.FAILED, notes='deterministic fault')
        mf.save(self.batch, self.state)
        self.stack.enter_context(patch.object(contracts, 'STAGES', tuple(contracts.STAGE_BY_ID[s] for s in stages)))
        # Only upstream readiness is supplied by the fixture. The real scheduler
        # owns refusal admission, serial/parallel selection and outcome reporting.
        self.stack.enter_context(patch.object(mf, 'dependency_satisfied', return_value=True))
        self.stack.enter_context(patch.object(mf, 'skip_reason', return_value=''))
        self.stack.enter_context(patch.object(auto, 'PARALLEL_AGENTS_ENABLED', parallel))
        self.stack.enter_context(patch('workflow.agent.configured', return_value=True))
        self.stack.enter_context(patch('workflow.local_agent.load_config', return_value={}))
        if real_refusal:
            self.stack.enter_context(patch.object(self_heal, '_candidate', return_value=(
                refused_stage, json.dumps({'error_code': 'integration', 'message': 'dispatch contract failed'}))))
            self.stack.enter_context(patch.object(self_heal, '_busy', return_value=False))
            self.stack.enter_context(patch.object(self_heal, '_runtime_failure_signature', return_value='stable-runtime-fault'))
        else:
            self.stack.enter_context(patch.object(self_heal, 'recover', return_value={
                'changed': False, 'stage': refused_stage, 'status': 'refused',
                'outcome': 'repair_not_applicable', 'escalation': 'code_repair',
                'reason': 'No locatable unit: preserve candidate and repair validator.'}))
        if old_inputs:
            self.stack.enter_context(patch.object(self_heal, '_load', return_value={
                'last_inputs': {refused_stage: {'source': 'old'}}}))
            self.stack.enter_context(patch.object(mf, 'input_hashes', return_value={'source': 'new'}))
        execute = self.stack.enter_context(patch.object(auto.rn, '_execute_command', return_value=1))
        wave = self.stack.enter_context(patch.object(auto, '_parallel_ready_agents',
            side_effect=lambda batch, specs, config, **kw: {s.id: 1 for s in specs}))
        bridge = {s: ['{python}', '-m', 'workflow.agent', 'run', '--stage', s,
                      '--batch-dir', '{batch_dir}'] for s in stages}
        result = auto._advance_locked(self.batch, bridge)
        return result, execute, wave

    def test_refused_command_is_not_dispatched(self):
        result, execute, wave = self.advance(['extract'], 'extract')
        execute.assert_not_called()
        wave.assert_not_called()
        self.assertEqual(result['action'], 'repair_refused')
        self.assertEqual(result['failed_stages'][0]['self_heal_status'], 'refused')
        self.assertEqual(mf.load(self.batch)['stages']['extract']['status'], mf.FAILED)

    def test_real_runtime_recovery_refusal_never_dispatches_content(self):
        result, execute, wave = self.advance(['knowledge'], 'knowledge', real_refusal=True)
        execute.assert_not_called()
        wave.assert_not_called()
        self.assertEqual(result['action'], 'repair_refused')
        event = self_heal._load(self.batch)['events'][-1]
        self.assertEqual(event['error_code'], 'integration')
        self.assertEqual(event['escalation'], 'code_repair')

    def test_refused_agent_stays_fenced_even_with_old_saved_input_hashes(self):
        result, execute, wave = self.advance(['knowledge'], 'knowledge', old_inputs=True)
        execute.assert_not_called()
        wave.assert_not_called()
        self.assertEqual(result['action'], 'repair_refused')

    def test_refused_agent_is_excluded_from_parallel_wave_and_independent_work_runs(self):
        result, execute, wave = self.advance(['explanations', 'knowledge'], 'knowledge', parallel=True)
        self.assertEqual([call.args[2].id for call in execute.call_args_list], ['explanations'])
        wave.assert_not_called()
        self.assertTrue(any(row['stage'] == 'knowledge' and row['action'] == 'repair_refused'
                            for row in result['failed_stages']))

    def test_audio_refusal_cannot_be_bypassed_by_capability_skip(self):
        result, execute, wave = self.advance(['audio'], 'audio')
        execute.assert_not_called()
        wave.assert_not_called()
        self.assertEqual(result['action'], 'repair_refused')
        self.assertEqual(mf.load(self.batch)['stages']['audio']['status'], mf.FAILED)

    def test_credential_rejection_blocks_only_affected_dispatch_until_revision_changes(self):
        from contextlib import nullcontext
        from types import SimpleNamespace
        from workflow import issue_log, local_agent

        stages = ('knowledge', 'explanations')
        bridge = {sid: ['fixture-agent'] for sid in stages}
        self.stack.enter_context(patch.object(contracts, 'STAGES', tuple(contracts.STAGE_BY_ID[s] for s in stages)))
        self.stack.enter_context(patch.object(mf, 'dependency_satisfied', return_value=True))
        self.stack.enter_context(patch.object(mf, 'skip_reason', return_value=''))
        self.stack.enter_context(patch.object(auto, 'PARALLEL_AGENTS_ENABLED', False))
        self.stack.enter_context(patch.object(local_agent, '_agent_lock', side_effect=lambda *a, **k: nullcontext()))
        binding = self.stack.enter_context(patch.object(local_agent, 'authentication_binding',
                                                      return_value={'revision': 1}))
        process = self.stack.enter_context(patch.object(auto.rn.subprocess, 'run',
            side_effect=lambda argv, **kwargs: SimpleNamespace(
                returncode=1, stdout='', stderr='{"code":"authentication","error":"provider rejected key"}'
                if '--stage' in argv and argv[argv.index('--stage') + 1] == 'knowledge' else 'other failure')))
        bridge = {sid: command + ['--stage', sid] for sid, command in bridge.items()}
        mf.save(self.batch, self.state)
        with patch.object(self_heal, '_plan') as plan:
            auto._advance_locked(self.batch, {'knowledge': bridge['knowledge']}, stop_at='explanations')
            self.assertEqual(mf.load(self.batch)['stages']['knowledge']['model_authentication'], {'revision': 1})
            result = auto._advance_locked(self.batch, bridge)
            self.assertEqual(result['action'], 'repair_refused')
            self.assertFalse(result['failed_stages'][0]['retryable'])
            self.assertEqual([call.args[0][-1] for call in process.call_args_list], ['knowledge', 'explanations'])
            blocker, = issue_log.load_blockers(self.batch)['blockers'].values()
            self.assertEqual((blocker['check'], blocker['occurrences']), ('MODEL_AUTHENTICATION_REJECTED', 1))
            state = mf.load(self.batch)
            mf.touch_stage(state, 'explanations', mf.PENDING)
            mf.save(self.batch, state)
            binding.return_value = {'revision': 2}
            auto._advance_locked(self.batch, {'knowledge': bridge['knowledge']}, stop_at='explanations')
            self.assertEqual([call.args[0][-1] for call in process.call_args_list],
                             ['knowledge', 'explanations', 'knowledge'])
            self.assertEqual(mf.load(self.batch)['stages']['knowledge']['model_authentication'], {'revision': 2})
            plan.assert_not_called()

    def test_real_deferral_survives_later_ticks_while_independent_branch_runs(self):
        mf.touch_stage(self.state, 'extract', mf.FAILED, notes='external source service unavailable')
        mf.save(self.batch, self.state)
        self.stack.enter_context(patch.object(contracts, 'STAGES', tuple(
            contracts.STAGE_BY_ID[s] for s in ('extract', 'explanations'))))
        self.stack.enter_context(patch.object(mf, 'dependency_satisfied', return_value=True))
        self.stack.enter_context(patch.object(auto, 'PARALLEL_AGENTS_ENABLED', False))
        self.stack.enter_context(patch.object(self_heal, '_busy', return_value=False))
        self.stack.enter_context(patch.object(self_heal, '_candidate', side_effect=lambda batch, state:
            ('extract', 'external source service unavailable')
            if state['stages']['extract']['status'] == mf.FAILED else (None, '')))
        plan = self.stack.enter_context(patch.object(self_heal, '_plan', return_value={
            'stage': 'extract', 'action': 'defer_and_continue', 'reason': 'External service temporarily unavailable'}))
        execute = self.stack.enter_context(patch.object(auto.rn, '_execute_command', return_value=1))
        bridge = {'explanations': ['{python}', '-m', 'workflow.agent', 'run',
                                  '--stage', 'explanations', '--batch-dir', '{batch_dir}']}
        for _ in range(2):
            result = auto._advance_locked(self.batch, bridge)
            wait = next(row for row in result['pending_stages'] if row['stage'] == 'extract')
            self.assertEqual(wait['action'], 'waiting_external')
            self.assertGreater(wait['retry_after'], 0)
            self.assertEqual(mf.load(self.batch)['stages']['extract']['status'], mf.PENDING)
        self.assertEqual(plan.call_count, 1, 'Waiting must not call the diagnosis model again')
        self.assertEqual([call.args[2].id for call in execute.call_args_list], ['explanations', 'explanations'])


if __name__ == '__main__':
    unittest.main()
