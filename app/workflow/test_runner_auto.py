# -*- coding: utf-8 -*-
import argparse
from contextlib import nullcontext
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import auto, contracts, manifest as mf, runner


class RunnerAutoTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.batch = Path(self.temp.name) / "batch"
        self.manifest = mf.new_manifest("batch", self.batch, {}, {})
        mf.save(self.batch, self.manifest)
        # Scheduler fixtures must stay offline even when this host has a real
        # model configured. Recovery-specific tests override this nested patch.
        recovery = patch('workflow.self_heal.recover',
                         return_value={'changed': False, 'status': 'offline'})
        recovery.start()
        self.addCleanup(recovery.stop)
        # 结构闸门的 AI 归类是生产路径；这些用例只验证调度/闸门语义，
        # 不能因为本机配置了模型路由就发起真实调用（曾把并发用例拖过 5 秒）。
        from workflow import structure_ai
        offline = patch.object(structure_ai, "auto_classify",
                               return_value={"ran": False, "reason": "测试离线，未调用模型"})
        offline.start()
        self.addCleanup(offline.stop)
        for target in ('workflow.metadata_ai.auto_identify', 'workflow.lesson_bands.build',
                       'workflow.visual_review.build', 'workflow.visual_columns.build'):
            offline_model = patch(target, return_value={'status': 'unavailable', 'model_calls': 0})
            offline_model.start()
            self.addCleanup(offline_model.stop)

    def test_cooldown_local_answers_and_managed_assemble_continue_to_validate(self):
        self._ready_parallel_labels()
        from workflow import batch_fill
        (self.batch / batch_fill.STATE).write_text('{}', encoding='utf-8')
        for stage_id in ('knowledge', 'explanations'):
            mf.touch_stage(self.manifest, stage_id, mf.COMPLETED)
        mf.save(self.batch, self.manifest)
        calls, local_calls = [], []
        def preflight(batch, stage):
            local_calls.append(stage)
            if stage != 'answers':
                return None
            current = mf.load(batch)
            mf.touch_stage(current, stage, mf.COMPLETED)
            mf.save(batch, current)
            return 0, {'completed': True}
        def assembled(batch):
            target = batch / '06_batch/batch.json'
            target.parent.mkdir(exist_ok=True)
            target.write_text('{"items": []}', encoding='utf-8')
            return 0
        def record(batch, current, stage):
            mf.touch_stage(current, stage.id, mf.COMPLETED)
            return True
        def validate(batch, current):
            calls.append('validate')
            mf.touch_stage(current, 'validate', mf.COMPLETED)
            mf.save(batch, current)
            return 0
        auto._set_cooldown(self.batch, 300)
        with patch('workflow.stage_preflight.run', side_effect=preflight), \
             patch('workflow.assemble_local.try_assemble', side_effect=assembled) as assemble, \
             patch.object(runner, '_record_success', side_effect=record), \
             patch.object(runner, '_execute_command', side_effect=self._execute_offline({}, calls)), \
             patch.object(runner, 'do_validate', side_effect=validate), \
             patch.object(auto, '_parallel_ready_agents') as parallel, \
             patch('workflow.local_agent.invoke') as invoke, \
             patch.object(auto, '_auto_prepare_audio') as audio:
            result = auto.advance(self.batch, auto.load_bridge(), stop_at='review')
        self.assertEqual(local_calls, ['answers'])
        assemble.assert_called_once_with(self.batch)
        self.assertEqual(calls, ['meta', 'underlines', 'validate'])
        self.assertEqual(mf.load(self.batch)['stages']['validate']['status'], mf.COMPLETED)
        self.assertEqual(mf.load(self.batch)['stages']['audio']['status'], mf.SKIPPED)
        self.assertFalse(result['failed_stages'])
        self.assertGreater(auto._read_cooldown(self.batch), 0)
        invoke.assert_not_called()
        parallel.assert_not_called()
        audio.assert_not_called()

    def test_cooldown_unhandled_or_failed_local_candidate_never_dispatches_model(self):
        self._ready_parallel_labels()
        auto._set_cooldown(self.batch, 300)
        with patch('workflow.stage_preflight.run', side_effect=lambda b, s: (
                (3, {'reason': 'candidate rejected'}) if s == 'answers' else None)), \
             patch.object(runner, '_execute_command') as execute, \
             patch.object(auto, '_parallel_ready_agents') as parallel, \
             patch('workflow.local_agent.invoke') as invoke:
            result = auto.advance(self.batch, auto.load_bridge(), stop_at='assemble')
        self.assertEqual(mf.load(self.batch)['stages']['answers']['status'], mf.PENDING)
        self.assertTrue(any(row['stage'] == 'answers' and row['action'] == 'review'
                            for row in result['pending_stages']))
        self.assertTrue(any(row['stage'] == 'knowledge' and row['action'] == 'cooldown'
                            for row in result['pending_stages']))
        execute.assert_not_called()
        parallel.assert_not_called()
        invoke.assert_not_called()

    def test_self_heal_wait_keeps_failed_stage_out_of_independent_dispatch(self):
        self._ready_parallel_labels()
        calls = []
        with patch('workflow.self_heal.recover', return_value={
                'stage': 'knowledge', 'status': 'cooldown', 'next_retry_at': 9999999999}), \
             patch('workflow.self_heal._load', return_value={}), \
             patch.object(auto, 'PARALLEL_AGENTS_ENABLED', False), \
             patch.object(runner, '_execute_command', side_effect=self._execute_offline({}, calls)):
            result = auto.advance(self.batch, auto.load_bridge(), stop_at='assemble')
        self.assertIn('answers', calls)
        self.assertNotIn('knowledge', calls)
        self.assertEqual(mf.load(self.batch)['stages']['knowledge']['status'], mf.PENDING)
        wait = next(row for row in result['pending_stages'] if row['stage'] == 'knowledge')
        self.assertEqual(wait['action'], 'cooldown')
        self.assertEqual(wait['next_retry_at'], 9999999999)

    def test_cooldown_managed_assemble_decline_does_not_mark_success_or_fallback(self):
        from workflow import batch_fill
        (self.batch / batch_fill.STATE).write_text('{}', encoding='utf-8')
        with patch('workflow.assemble_local.try_assemble', return_value=None) as assemble, \
             patch.object(runner, '_record_success') as record, \
             patch('workflow.local_agent.invoke') as invoke:
            code, report = auto._cooldown_preflight(self.batch, contracts.STAGE_BY_ID['assemble'])
        self.assertEqual(code, 3)
        self.assertEqual(report['model_calls'], 0)
        self.assertEqual(mf.load(self.batch)['stages']['assemble']['status'], mf.PENDING)
        assemble.assert_called_once_with(self.batch)
        record.assert_not_called()
        invoke.assert_not_called()

    def test_cooldown_does_not_replace_custom_bridge_with_local_preflight(self):
        self._ready_parallel_labels()
        auto._set_cooldown(self.batch, 300)
        with patch('workflow.stage_preflight.run') as preflight, \
             patch.object(runner, '_execute_command') as execute:
            result = auto.advance(self.batch, {'answers': ['custom']}, stop_at='audio')
        preflight.assert_not_called()
        execute.assert_not_called()
        self.assertEqual(result['action'], 'cooldown')

    def test_local_ready_candidates_are_read_only_and_respect_bridge_and_dependencies(self):
        self._ready_parallel_labels()
        from workflow import batch_fill
        (self.batch / batch_fill.STATE).write_text('{}', encoding='utf-8')
        before = mf.manifest_path(self.batch).read_bytes()
        with patch('workflow.answers_direct.build_payload', return_value={'answers': {'q': 'A'}}), \
             patch('workflow.explanations_direct.build_payload', return_value=None), \
             patch('workflow.stage_preflight.run') as run:
            ready = auto.local_ready_stages(self.batch, self.manifest, auto.load_bridge())
            custom = auto.local_ready_stages(self.batch, self.manifest, {'answers': ['custom']})
        self.assertIn('answers', ready)
        self.assertIn('audio', ready)
        self.assertNotIn('assemble', ready)
        self.assertNotIn('answers', custom)
        self.assertEqual(mf.manifest_path(self.batch).read_bytes(), before)
        run.assert_not_called()

    def test_second_run_is_rejected_while_one_holds_the_batch(self):
        from workflow import runner as runner_mod
        with runner_mod._single_run_lock(self.batch):
            with self.assertRaises(RuntimeError):
                with runner_mod._single_run_lock(self.batch, timeout=0.2):
                    pass
        with runner_mod._single_run_lock(self.batch, timeout=0.2):
            pass

    def test_stop_at_passes_from_cli_through_process_and_stops_before_stage(self):
        candidate = {"name": "batch"}
        with patch.object(auto, "discover", return_value=[candidate]), \
             patch.object(auto, "ensure_batch", return_value=self.batch), \
             patch.object(auto.report_mod, "write", return_value={"counts": {
                 "ok": 0, "attention": 0, "blocked": 0, "unverified": 0}}), \
             patch.object(runner, "_execute_command") as execute:
            self.assertEqual(auto.main(["run", "--inbox", str(self.batch),
                                        "--workflows", str(self.batch.parent),
                                        "--stop-at", "extract"]), 0)
        execute.assert_not_called()
        self.assertEqual(mf.load(self.batch)["stages"]["extract"]["status"], mf.PENDING)
        queue = json.loads((self.batch.parent / auto.QUEUE_FILE).read_text(encoding="utf-8"))
        self.assertEqual(queue["batches"][0]["reason"], "自动阶段已全部完成")

    def test_invalid_stop_at_rejected_before_processing(self):
        with patch.object(auto, "process") as process:
            self.assertEqual(auto.main(["run", "--stop-at", "not-a-stage"]), 2)
        process.assert_not_called()

    def _isolate_scheduler(self):
        from workflow import agent_tasks
        def status_ready(manifest, stage_id, **_kwargs):
            stages = manifest.get('stages') or {}
            return ( (stages.get(stage_id) or {}).get('status') in mf.TERMINAL_OK
                     and all((stages.get(dep) or {}).get('status') in mf.TERMINAL_OK
                             and not (stages.get(dep) or {}).get('rebuild_required')
                             for dep in contracts.upstream_of(stage_id)))
        for patcher in (patch('workflow.self_heal.recover', return_value={}),
                        patch('workflow.local_agent._agent_lock', side_effect=lambda *_a, **_kw: nullcontext()),
                        patch('workflow.agent_protocol._agent_lock', side_effect=lambda *_a, **_kw: nullcontext()),
                        patch.object(auto, '_auto_prepare_audio', return_value={'status': 'ready'}),
                        patch.object(mf, 'reconcile_deliveries', return_value=[]),
                        patch.object(mf, 'is_stale', return_value=(False, '')),
                        patch.object(mf, 'dependency_satisfied', side_effect=status_ready),
                        patch.object(agent_tasks, 'stage_gaps', return_value={})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _ready_after_structure(self):
        self._isolate_scheduler()
        source = self.batch / '01_source'
        source.mkdir(parents=True, exist_ok=True)
        # Keep optional agents eligible: scheduling, not absent-input skipping,
        # is the subject of these tests.
        (source / '附录.txt').write_text('参考答案\n1. A\n【解析】原卷已有解析。', encoding='utf-8')
        # Scheduling tests start after preassembly has delivered source objects
        # and labels; artifact validation is isolated from scheduler behavior.
        for stage_id in ("extract", "structure_gate", "preassemble"):
            mf.touch_stage(self.manifest, stage_id, mf.COMPLETED)
        mf.save(self.batch, self.manifest)

    def _ready_parallel_labels(self):
        self._ready_after_structure()
        # Preassembly already classified types; this deterministic checkpoint
        # must finish before audio/knowledge can be real model peers of answers.
        mf.touch_stage(self.manifest, "types", mf.COMPLETED)
        mf.save(self.batch, self.manifest)

    def _execute_offline(self, outcomes, calls):
        def execute(batch_dir, manifest, stage):
            calls.append(stage.id)
            status, code = outcomes.get(stage.id, (mf.COMPLETED, 0))
            mf.touch_stage(manifest, stage.id, status, exit_code=code)
            mf.save(batch_dir, manifest)
            return code
        return execute

    def test_parallel_wave_overlaps_calls_and_imports_ready_workers(self):
        from workflow import agent, agent_protocol, agent_tasks, local_agent, agent_prepare
        self._ready_parallel_labels()
        barrier = threading.Barrier(2, timeout=5)
        completed = set()
        imports = []
        retries = []
        validation = []
        lock = threading.Lock()
        saving = threading.Lock()
        original_save = mf.save
        default = auto.load_bridge()

        def save(batch, manifest):
            # Simulate Windows process-owned file locks: sibling threads can
            # both enter _agent_lock, but must never read/replace this ledger
            # concurrently. The model barrier below still requires overlap.
            if not saving.acquire(blocking=False):
                raise RuntimeError("concurrent manifest save")
            try:
                threading.Event().wait(0.02)
                return original_save(batch, manifest)
            finally:
                saving.release()

        def export(batch, stage, **kwargs):
            package = self.batch / "test-packages" / stage
            package.mkdir(parents=True)
            (package / "task.json").write_text(json.dumps({"prompt": stage + "\nFILE PROTOCOL: example"}))
            return {"status": "ready", "task_id": stage, "package_dir": str(package)}

        def build(batch, stage, **kwargs):
            return {"status": "ready", "prompt": stage}

        def invoke(prompt, run_dir, config, permit_parallel=False, coordinated_imports=False):
            if permit_parallel and prompt in {"answers", "audio"}:
                barrier.wait()
                with lock:
                    completed.add(prompt)
            elif permit_parallel:
                with lock:
                    completed.add(prompt)
            else:
                self.assertIn("上一次返回未通过校验", prompt)
                retries.append(prompt)
            return {"response": {"status": "completed", "artifacts": {}, "notes": [], "findings": []}}

        def validate(batch, stage, response, validate_only=False):
            validation.append(stage)
            if stage == "audio" and validation.count(stage) == 1:
                raise ValueError("test validation failure")

        def execute(batch, manifest, stage):
            mf.touch_stage(manifest, stage.id, mf.COMPLETED)
            mf.save(batch, manifest)
            return 0

        def import_result(batch, task_id, path):
            self.assertIn(task_id, completed)
            imports.append(task_id)
            current = mf.load(batch)
            mf.touch_stage(current, task_id, mf.COMPLETED)
            mf.save(batch, current)
            return {"completed": True, "notes": []}

        with patch.object(auto, "PARALLEL_AGENTS_ENABLED", True), \
             patch.object(agent_protocol, "_agent_lock", side_effect=lambda *a, **k: nullcontext()), \
             patch.object(mf, "save", side_effect=save), \
             patch.object(agent, "configured", return_value=True), \
             patch.object(local_agent, "load_config", return_value={"max_input_chars": 10000, "retries": 1}), \
             patch.object(agent_prepare, "prepare_answers"), \
             patch.object(runner, "_execute_command", side_effect=execute), \
             patch.object(agent_protocol, "export_task", side_effect=export), \
             patch.object(agent_tasks, "build_task", side_effect=build), \
             patch.object(agent_tasks, "accept_result", side_effect=validate), \
             patch.object(local_agent, "invoke", side_effect=invoke), \
             patch("workflow.label_execution.run", side_effect=lambda b, s, c, r, **kw: invoke(s, r, c, permit_parallel=True)), \
             patch.object(agent_protocol, "import_result", side_effect=import_result):
            outcome = auto.advance(self.batch, default, stop_at="explanations")
        # Both peers are ready after the read-only types checkpoint.
        self.assertCountEqual(imports, ["answers", "audio"])
        self.assertEqual(completed, {"answers", "audio"})
        self.assertEqual(mf.load(self.batch)["stages"]["explanations"]["status"], mf.PENDING)
        self.assertEqual(len(retries), 1)
        self.assertIn("test validation failure", retries[0])
        self.assertEqual(outcome["failed_stages"], [])
        for stage in imports:
            self.assertEqual(mf.load(self.batch)["stages"][stage]["status"], mf.COMPLETED)

    def test_three_label_workers_start_together_without_answer_delivery(self):
        from workflow import agent_protocol, agent_tasks, local_agent
        self._ready_parallel_labels()
        stages = [contracts.STAGE_BY_ID[stage] for stage in ("answers", "knowledge", "explanations")]
        barrier = threading.Barrier(3, timeout=5)
        entered = set()
        lock = threading.Lock()

        def export(batch, stage, **kwargs):
            package = self.batch / "packages" / stage
            package.mkdir(parents=True)
            (package / "task.json").write_text(json.dumps({"prompt": stage + "\nFILE PROTOCOL: test"}))
            return {"status": "ready", "task_id": stage, "package_dir": str(package)}

        def invoke(prompt, run_dir, config, permit_parallel=False, coordinated_imports=False):
            self.assertTrue(permit_parallel)
            with lock:
                entered.add(prompt)
            barrier.wait()
            return {"response": {"status": "completed"}}

        def import_result(batch, task_id, path):
            self.assertEqual(len(entered), 3)
            return {"completed": True}

        with patch("workflow.stage_preflight.run", return_value=None), \
             patch("workflow.agent_prepare.prepare_answers"), \
             patch.object(agent_protocol, "export_task", side_effect=export), \
             patch.object(agent_tasks, "build_task", side_effect=lambda batch, stage, **kw: {"status": "ready", "prompt": stage}), \
             patch.object(agent_tasks, "accept_result"), \
             patch.object(local_agent, "invoke", side_effect=invoke), \
             patch("workflow.label_execution.run", side_effect=lambda b, s, c, r, **kw: invoke(s, r, c, permit_parallel=True)), \
             patch.object(agent_protocol, "import_result", side_effect=import_result):
            outcomes = auto._parallel_ready_agents(self.batch, stages, {"max_input_chars": 10000, "retries": 0})
        self.assertEqual(outcomes, {"answers": 0, "knowledge": 0, "explanations": 0})
        self.assertEqual(entered, {"answers", "knowledge", "explanations"})
        # No answer result was installed by this fixture before workers started.
        self.assertFalse((self.batch / '01_source_ans/answers.json').exists())

    def test_ready_stage_is_imported_before_slow_peer_finishes(self):
        from workflow import agent_protocol, agent_tasks, local_agent
        self._ready_parallel_labels()
        stages = [contracts.STAGE_BY_ID[s] for s in ('answers', 'knowledge')]
        imported = threading.Event()
        def export(batch, stage, **kw):
            package = self.batch / 'early-packages' / stage
            package.mkdir(parents=True)
            (package / 'task.json').write_text(json.dumps({'prompt': stage + '\nFILE PROTOCOL: test'}))
            return {'status': 'ready', 'task_id': stage, 'package_dir': str(package)}
        def slow(prompt, *a, **kw):
            self.assertTrue(imported.wait(3), 'Fast stage was held behind slow peer')
            return {'response': {'status': 'completed'}}
        def install(batch, task_id, path):
            if task_id == 'answers':
                imported.set()
            return {'completed': True}
        with patch('workflow.agent_prepare.prepare_answers'), \
             patch.object(agent_protocol, 'export_task', side_effect=export), \
             patch.object(agent_tasks, 'build_task', side_effect=lambda b, s, **kw: {'status': 'ready', 'prompt': s}), \
             patch.object(agent_tasks, 'accept_result'), \
             patch('workflow.label_execution.run', return_value={'response': {'status': 'completed'}}), \
             patch.object(local_agent, 'invoke', side_effect=slow), \
             patch.object(agent_protocol, 'import_result', side_effect=install):
            self.assertEqual(auto._parallel_ready_agents(self.batch, stages, {'max_input_chars': 10000, 'retries': 0}),
                             {'answers': 0, 'knowledge': 0})

    def test_slow_audio_preparation_allows_text_chain_through_validate(self):
        self._ready_after_structure()
        started, validated = threading.Event(), threading.Event()
        calls = []
        self.manifest['inputs']['audio'] = {'exists': True, 'path': str(self.batch / 'audio.mp3')}
        mf.save(self.batch, self.manifest)
        def cutter(batch, state):
            started.set()
            self.assertTrue(validated.wait(5), 'Cutter blocked text validation')
            return {'status': 'ready'}
        execute = self._execute_offline({}, calls)
        def command(batch, state, stage):
            if stage.id in ('answers', 'explanations', 'knowledge', 'assemble', 'meta', 'underlines'):
                self.assertTrue(started.wait(2))
            if stage.id == 'assemble':
                target = batch / '06_batch/batch.json'
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('{}')
            return execute(batch, state, stage)
        def validate(batch, state):
            self.assertNotIn('audio', calls)
            calls.append('validate')
            mf.touch_stage(state, 'validate', mf.COMPLETED)
            mf.save(batch, state)
            validated.set()
            return 0
        with patch.object(auto, '_auto_prepare_audio', side_effect=cutter) as prepare, \
             patch.object(runner, '_execute_command', side_effect=command), \
             patch.object(runner, 'do_validate', side_effect=validate):
            auto.advance(self.batch, {s.id: ['offline'] for s in contracts.STAGES}, stop_at='review')
        prepare.assert_called_once()
        self.assertLess(calls.index('validate'), calls.index('audio'))
        self.assertEqual(mf.load(self.batch)['stages']['review']['status'], mf.PENDING)

    def test_audio_model_does_not_hold_text_chain_but_review_joins_delivery(self):
        self._exercise_audio_model_delivery(True)

    def test_unaccepted_audio_model_does_not_release_review(self):
        self._exercise_audio_model_delivery(False)

    def _exercise_audio_model_delivery(self, audio_completed):
        from workflow import agent, agent_protocol, agent_tasks, local_agent
        self._ready_parallel_labels()
        self.manifest = mf.load(self.batch)
        self.manifest['inputs']['audio'] = {'exists': True, 'path': str(self.batch / 'audio.mp3')}
        mf.save(self.batch, self.manifest)
        audio_started, validated, audio_imported = (threading.Event() for _ in range(3))
        calls = []
        def export(batch, stage, **kw):
            package = batch / 'model-packages' / stage
            package.mkdir(parents=True)
            (package / 'task.json').write_text(json.dumps({'prompt': stage + '\nFILE PROTOCOL: test'}))
            return {'status': 'ready', 'task_id': stage, 'package_dir': str(package)}
        def invoke(prompt, *args, **kw):
            if prompt == 'audio':
                audio_started.set()
                self.assertTrue(validated.wait(5), 'Audio model blocked text validation')
            return {'response': {'status': 'completed'}}
        def install(batch, task_id, path):
            if task_id == 'audio':
                self.assertTrue(validated.is_set())
                audio_imported.set()
            calls.append(task_id)
            state = mf.load(batch)
            accepted = task_id != 'audio' or audio_completed
            mf.touch_stage(state, task_id, mf.COMPLETED if accepted else mf.NEEDS_REVIEW)
            mf.save(batch, state)
            return {'completed': accepted}
        def command(batch, state, stage):
            if stage.id == 'assemble':
                self.assertTrue(audio_started.wait(2))
                self.assertFalse(audio_imported.is_set())
                target = batch / '06_batch/batch.json'
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('{}')
            if stage.id == 'review':
                self.assertTrue(audio_imported.is_set(), 'Review ran before audio delivery')
            calls.append(stage.id)
            mf.touch_stage(state, stage.id, mf.COMPLETED)
            mf.save(batch, state)
            return 0
        def validate(batch, state):
            self.assertFalse(audio_imported.is_set())
            calls.append('validate')
            mf.touch_stage(state, 'validate', mf.COMPLETED)
            mf.save(batch, state)
            validated.set()
            return 0
        bridge = auto.load_bridge()
        bridge['assemble'] = bridge['review'] = ['offline']
        with patch.object(auto, 'PARALLEL_AGENTS_ENABLED', True), \
             patch.object(auto, '_auto_prepare_audio', return_value={'status': 'ready'}), \
             patch.object(agent, 'configured', return_value=True), \
             patch.object(local_agent, 'load_config', return_value={'max_input_chars': 10000, 'retries': 0}), \
             patch('workflow.stage_preflight.run', return_value=None), \
             patch('workflow.agent_prepare.prepare_answers'), \
             patch.object(agent_protocol, 'export_task', side_effect=export), \
             patch.object(agent_tasks, 'build_task', side_effect=lambda b, s, **kw: {'status': 'ready', 'prompt': s}), \
             patch.object(agent_tasks, 'accept_result'), \
             patch.object(local_agent, 'invoke', side_effect=invoke), \
             patch('workflow.label_execution.run', side_effect=lambda b, s, c, r, **kw: invoke(s)), \
             patch.object(agent_protocol, 'import_result', side_effect=install), \
             patch.object(runner, '_execute_command', side_effect=command), \
             patch.object(runner, 'do_validate', side_effect=validate):
            auto.advance(self.batch, bridge, stop_at='review_gate')
        self.assertLess(calls.index('assemble'), calls.index('audio'))
        self.assertLess(calls.index('validate'), calls.index('audio'))
        if audio_completed:
            self.assertLess(calls.index('audio'), calls.index('review'))
        else:
            self.assertNotIn('review', calls)
            self.assertEqual(mf.load(self.batch)['stages']['review']['status'], mf.PENDING)
        self.assertEqual(calls.count('audio'), 1)

    def test_parallel_rejects_workers_without_validated_types_delivery(self):
        self._ready_after_structure()
        with patch('workflow.agent_protocol.export_task') as export:
            result = auto._parallel_ready_agents(self.batch,
                [contracts.STAGE_BY_ID[s] for s in ('answers', 'knowledge', 'explanations')], {})
        self.assertEqual(result, {})
        export.assert_not_called()

    def test_rate_limit_wrapper_code_is_authoritative(self):
        self.assertTrue(auto._is_rate_limited(
            "Agent subprocess failed (exit 1, rate_limit); inspect stderr.log"))
        self.assertTrue(auto._is_rate_limited(
            'wf-agent-result: {"error_code": "rate_limit", "http_status": 429}'))
        self.assertFalse(auto._is_rate_limited("validation failed: rate_limit is not a field"))

    def test_parallel_admission_cooldown_preserves_existing_deadline(self):
        from workflow import agent_protocol, agent_tasks, local_agent
        self._ready_parallel_labels()
        auto._set_cooldown(self.batch, 300)
        marker = auto._cooldown_path(self.batch).read_bytes()
        class AdmissionWait(RuntimeError):
            code = 'model_cooldown'
        def export(batch, stage, **kw):
            package = batch / 'cooldown-packages' / stage
            package.mkdir(parents=True)
            (package / 'task.json').write_text(json.dumps({'prompt': stage + '\nFILE PROTOCOL: test'}))
            return {'status': 'ready', 'task_id': stage, 'package_dir': str(package)}
        with patch('workflow.stage_preflight.run', return_value=None), \
             patch.object(agent_protocol, 'export_task', side_effect=export), \
             patch.object(agent_tasks, 'build_task', return_value={'status': 'ready', 'prompt': 'knowledge'}), \
             patch.object(local_agent, 'invoke', side_effect=AdmissionWait('MODEL_COOLDOWN')):
            result = auto._parallel_ready_agents(self.batch, [contracts.STAGE_BY_ID['knowledge']],
                                                 {'max_input_chars': 10000, 'retries': 0})
        self.assertEqual(result, {'knowledge': 4})
        self.assertEqual(auto._cooldown_path(self.batch).read_bytes(), marker)
        self.assertEqual(mf.load(self.batch)['stages']['knowledge']['status'], mf.PENDING)

    def test_parallel_rate_limit_becomes_cooldown_not_failed(self):
        from workflow import agent_protocol, agent_tasks, local_agent
        self._ready_parallel_labels()
        stages = [contracts.STAGE_BY_ID[stage] for stage in ("answers", "knowledge")]

        def export(batch, stage, **kwargs):
            package = self.batch / "packages" / stage
            package.mkdir(parents=True)
            (package / "task.json").write_text(json.dumps({"prompt": stage + "\nFILE PROTOCOL: test"}))
            return {"status": "ready", "task_id": stage, "package_dir": str(package)}

        def invoke(prompt, run_dir, config, permit_parallel=False, coordinated_imports=False):
            raise RuntimeError(
                "Agent subprocess failed (exit 1, rate_limit); inspect stderr.log\n"
                'wf-agent-result: {"error_code": "rate_limit", "http_status": 429}')

        with patch("workflow.agent_prepare.prepare_answers"), \
             patch.object(agent_protocol, "export_task", side_effect=export), \
             patch.object(agent_tasks, "build_task", side_effect=lambda batch, stage, **kw: {"status": "ready", "prompt": stage}), \
             patch.object(agent_tasks, "accept_result"), \
             patch.object(local_agent, "invoke", side_effect=invoke), \
             patch("workflow.label_execution.run", side_effect=lambda b, s, c, r, **kw: invoke(s, r, c, permit_parallel=True)), \
             patch.object(agent_protocol, "import_result"):
            outcomes = auto._parallel_ready_agents(self.batch, stages, {"max_input_chars": 10000, "retries": 0})
        self.assertEqual(outcomes, {stage.id: 4 for stage in stages})
        manifest = mf.load(self.batch)
        for stage in stages:
            self.assertEqual(manifest["stages"][stage.id]["status"], mf.PENDING)
            self.assertIn("限流", manifest["stages"][stage.id]["notes"])
        self.assertGreater(auto._read_cooldown(self.batch), 0)

    def test_rate_limit_cooldown_does_not_block_local_gates(self):
        self._isolate_scheduler()
        for stage in contracts.STAGES:
            if stage.id == "validate":
                break
            mf.touch_stage(self.manifest, stage.id, mf.COMPLETED)
        mf.touch_stage(self.manifest, "validate", mf.PENDING)
        mf.save(self.batch, self.manifest)
        auto._set_cooldown(self.batch, 300)
        calls = []

        def validate(batch_dir, manifest):
            calls.append("validate")
            mf.touch_stage(manifest, "validate", mf.COMPLETED)
            mf.save(batch_dir, manifest)
            return 0

        with patch.object(runner, "do_validate", side_effect=validate), patch(
                "workflow.self_heal.recover") as recover:
            outcome = auto.advance(self.batch, {stage.id: ["offline"] for stage in contracts.STAGES})

        recover.assert_not_called()
        self.assertEqual(calls, ["validate"])
        self.assertEqual(mf.load(self.batch)["stages"]["validate"]["status"], mf.COMPLETED)
        pending = {row["stage"]: row for row in outcome["pending_stages"]}
        self.assertEqual(pending["review"]["action"], "cooldown")
        self.assertEqual(pending["review_gate"]["unmet_dependencies"], ["review"])

    def test_new_blocker_is_published_before_next_independent_stage(self):
        self._isolate_scheduler()
        for spec in contracts.STAGES:
            mf.touch_stage(self.manifest, spec.id, mf.COMPLETED)
        for sid in ('meta', 'underlines'):
            mf.touch_stage(self.manifest, sid, mf.PENDING)
        mf.save(self.batch, self.manifest)
        (self.batch / '06_batch').mkdir(parents=True, exist_ok=True)
        (self.batch / '06_batch/batch.json').write_text('{}', encoding='utf-8')
        events = []
        def execute(batch, state, stage):
            if stage.id == 'meta':
                mf.touch_stage(state, 'meta', mf.FAILED)
                mf.save(batch, state)
                events.append('failed')
                return 1
            events.append('independent')
            self.assertIn('published', events)
            return 0
        def handoff(batch):
            if mf.load(batch)['stages']['meta']['status'] == mf.FAILED:
                events.append('published')
            return None
        # Treat the second synthetic command as independent; this test isolates
        # publication order, not the production meta -> underlines dependency.
        with patch('workflow.operations.handoff_if_available', side_effect=handoff), \
             patch.object(mf, 'dependency_satisfied', return_value=True), \
             patch.object(runner, '_execute_command', side_effect=execute):
            auto.advance(self.batch, {}, stop_at='validate')
        self.assertLess(events.index('failed'), events.index('published'))
        self.assertLess(events.index('published'), events.index('independent'))

    @unittest.skipIf(__import__("os").name == "nt", "Windows byte locks have different thread semantics")
    def test_auto_serializes_two_schedulers_for_same_batch(self):
        first_entered, release, second_entered = threading.Event(), threading.Event(), threading.Event()
        calls = []
        def advance_locked(batch, bridge, stop_at, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                first_entered.set()
                self.assertTrue(release.wait(5))
            else:
                second_entered.set()
            return {}
        with patch.object(auto, "_advance_locked", side_effect=advance_locked):
            first = threading.Thread(target=auto.advance, args=(self.batch, {}))
            second = threading.Thread(target=auto.advance, args=(self.batch, {}))
            first.start()
            self.assertTrue(first_entered.wait(5))
            second.start()
            try:
                self.assertFalse(second_entered.wait(0.1))
            finally:
                release.set()
                first.join(5)
                second.join(5)
            self.assertTrue(second_entered.is_set())
            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())

    @unittest.skipIf(__import__("os").name == "nt", "POSIX flock semantics differ on Windows")
    def test_parallel_wave_does_not_deadlock_concurrent_mark(self):
        """A separate process marking another stage must not deadlock or lose updates."""
        import multiprocessing as mp
        from workflow import agent, agent_protocol, agent_tasks, local_agent, agent_prepare
        self._ready_parallel_labels()
        default = auto.load_bridge()
        marker = threading.Event()

        def mark_worker(batch_dir, started, done):
            started.set()
            # A concurrent writer touches an unrelated metadata stage; extract is
            # already delivered, and this check is only about lock/lost-update
            # safety, not about certifying a fresh extract delivery.
            runner.cmd_mark(argparse.Namespace(
                batch_dir=str(batch_dir), stage="meta", status=mf.COMPLETED,
                note="并发标记", actor="worker", allow_missing=True,
                invalidate_downstream=False))
            done.set()

        def export(batch, stage, **kwargs):
            package = self.batch / "test-packages" / stage
            package.mkdir(parents=True)
            (package / "task.json").write_text(json.dumps({"prompt": stage + "\nFILE PROTOCOL: example"}))
            return {"status": "ready", "task_id": stage, "package_dir": str(package)}

        def build(batch, stage, **kwargs):
            return {"status": "ready", "prompt": stage}

        def invoke(prompt, run_dir, config, permit_parallel=False, coordinated_imports=False):
            self.assertTrue(marker.wait(10))
            return {"response": {"status": "completed", "artifacts": {}, "notes": [], "findings": []}}

        def import_result(batch, task_id, path):
            current = mf.load(batch)
            mf.touch_stage(current, task_id, mf.COMPLETED, exit_code=0)
            mf.save(batch, current)
            return {"completed": True, "notes": []}

        ctx = mp.get_context("fork")
        started, done = ctx.Event(), ctx.Event()
        proc = ctx.Process(target=mark_worker, args=(self.batch, started, done))
        proc.start()
        try:
            with patch.object(auto, "PARALLEL_AGENTS_ENABLED", True), \
                 patch.object(agent, "configured", return_value=True), \
                 patch.object(local_agent, "load_config", return_value={"max_input_chars": 10000, "retries": 0}), \
                 patch.object(agent_prepare, "prepare_answers"), \
                 patch.object(agent_protocol, "export_task", side_effect=export), \
                 patch.object(agent_tasks, "build_task", side_effect=build), \
                 patch.object(agent_tasks, "accept_result", return_value={"completed": True}), \
                 patch.object(local_agent, "invoke", side_effect=invoke), \
             patch("workflow.label_execution.run", side_effect=lambda b, s, c, r, **kw: invoke(s, r, c, permit_parallel=True)), \
                 patch.object(agent_protocol, "import_result", side_effect=import_result):
                self.assertTrue(started.wait(10))
                # Model calls are blocked until the marker is set, so a real
                # overlap with the marking process is required to finish.
                early = threading.Timer(0.5, marker.set)
                early.start()
                auto.advance(self.batch, default, stop_at="explanations")
                early.join()
            self.assertTrue(done.wait(15))
        finally:
            proc.join(15)
            if proc.is_alive():
                proc.terminate()
        self.assertFalse(proc.is_alive())
        manifest = mf.load(self.batch)
        self.assertEqual(manifest["stages"]["meta"]["status"], mf.COMPLETED)
        for stage in ("answers", "audio"):
            self.assertEqual(manifest["stages"][stage]["status"], mf.COMPLETED)

    def test_answers_failure_continues_audio_types_but_waits_for_real_downstream(self):
        self._isolate_scheduler()
        self._ready_after_structure()
        calls = []
        with patch.object(runner, "_execute_command", side_effect=self._execute_offline(
                {"answers": (mf.FAILED, 1)}, calls)):
            outcome = auto.advance(self.batch, {s.id: ["offline"] for s in contracts.STAGES})
        self.assertEqual(calls, ["types", "answers", "audio", "explanations", "knowledge"])
        self.assertEqual((outcome["stopped_at"], outcome["action"]), ("answers", "failed"))
        self.assertEqual([s["stage"] for s in outcome["failed_stages"]], ["answers"])
        pending = {row["stage"]: row for row in outcome["pending_stages"]}
        self.assertEqual(pending["assemble"]["unmet_dependencies"], ["answers"])
        manifest = mf.load(self.batch)
        self.assertEqual(manifest["stages"]["assemble"]["status"], mf.PENDING)
        for stage_id in ("audio", "types", "knowledge"):
            self.assertEqual(manifest["stages"][stage_id]["status"], mf.COMPLETED)
        self.assertEqual(manifest["stages"]["explanations"]["status"], mf.COMPLETED)
        self.assertEqual(manifest["stages"]["upload"]["status"], mf.PENDING)

    def test_zero_touch_does_not_promote_missing_agent_artifact(self):
        self._ready_after_structure()
        calls = []
        with patch.object(runner, "_execute_command", side_effect=self._execute_offline(
                {"assemble": (mf.NEEDS_REVIEW, 3)}, calls)):
            outcome = auto.advance(self.batch, {s.id: ["offline"] for s in contracts.STAGES})
        self.assertEqual(calls[-1], "assemble")
        self.assertEqual(mf.load(self.batch)["stages"]["assemble"]["status"], mf.NEEDS_REVIEW)
        self.assertEqual(outcome["stopped_at"], "assemble")
        self.assertEqual(outcome["action"], "review")
        self.assertFalse((self.batch / "06_batch/batch.json").exists())

    def test_failed_preassemble_blocks_assembly_with_completed_labels(self):
        self._ready_after_structure()
        for stage_id in ("answers", "types", "knowledge", "explanations"):
            mf.touch_stage(self.manifest, stage_id, mf.COMPLETED)
        mf.touch_stage(self.manifest, "preassemble", mf.FAILED,
                       notes="Batch fill正文 conflict for group-6-7; existing content preserved")
        mf.save(self.batch, self.manifest)
        self.assertIn("preassemble", contracts.STAGE_BY_ID["assemble"].depends_on)
        args = argparse.Namespace(stage="assemble", until=None, force=False, dry_run=False)
        with patch.object(runner, "_execute_command") as execute:
            self.assertNotEqual(runner._cmd_run_locked(self.batch, args), 0)
        execute.assert_not_called()
        self.assertEqual(mf.load(self.batch)["stages"]["assemble"]["status"], mf.PENDING)

    def test_audio_needs_review_does_not_block_assemble_but_blocks_review(self):
        """听力待复核不阻断解析/知识点/组装；只有内容审核(review)必须等听力完成。"""
        self._isolate_scheduler()
        self._ready_after_structure()
        patcher = patch.object(auto, 'ZERO_TOUCH', False)
        patcher.start(); self.addCleanup(patcher.stop)
        calls = []
        with patch.object(runner, "_execute_command", side_effect=self._execute_offline(
                {"audio": (mf.NEEDS_REVIEW, 3)}, calls)):
            outcome = auto.advance(self.batch, {s.id: ["offline"] for s in contracts.STAGES})
        # 听力不再挡组装：assemble 会执行；但 review 因 audio 未完成而等待。
        self.assertIn("assemble", calls)
        self.assertNotIn("review", calls)
        self.assertIn("audio", calls)
        pending = {s["stage"]: s for s in outcome["pending_stages"]}
        self.assertIn("audio", pending["review"]["unmet_dependencies"])
        manifest = mf.load(self.batch)
        self.assertEqual(manifest["stages"]["audio"]["status"], mf.NEEDS_REVIEW)
        self.assertEqual(manifest["stages"]["assemble"]["status"], mf.COMPLETED)
        self.assertEqual(manifest["stages"]["review"]["status"], mf.PENDING)

    def test_multiple_failures_reported_once_without_retry(self):
        self._isolate_scheduler()
        self._ready_after_structure()
        calls = []
        with patch.object(runner, "_execute_command", side_effect=self._execute_offline(
                {"answers": (mf.FAILED, 1), "audio": (mf.FAILED, 2)}, calls)):
            outcome = auto.advance(self.batch, {s.id: ["offline"] for s in contracts.STAGES})
        self.assertEqual(calls, ["types", "answers", "audio", "explanations", "knowledge"])
        self.assertEqual([s["stage"] for s in outcome["failed_stages"]], ["answers", "audio"])
        self.assertEqual(outcome["stopped_at"], "answers")

    def test_missing_agent_bridge_does_not_stop_independent_agents(self):
        self._ready_after_structure()
        calls = []
        with patch.object(runner, "_execute_command", side_effect=self._execute_offline({}, calls)):
            outcome = auto.advance(self.batch, {"audio": ["offline"], "types": ["offline"]})
        self.assertEqual(calls, ["types", "audio"])
        self.assertEqual((outcome["stopped_at"], outcome["action"]), ("answers", "agent"))
        self.assertEqual(outcome["pending_stages"][0]["stage"], "answers")
        self.assertEqual(mf.load(self.batch)["stages"]["knowledge"]["status"], mf.PENDING)

    def test_stop_at_remains_hard_boundary_even_after_failure(self):
        self._ready_after_structure()
        calls = []
        with patch.object(runner, "_execute_command", side_effect=self._execute_offline(
                {"answers": (mf.FAILED, 1)}, calls)):
            outcome = auto.advance(self.batch, {s.id: ["offline"] for s in contracts.STAGES},
                                   stop_at="audio")
        self.assertEqual(calls, ["types", "answers"])
        self.assertEqual(outcome["stopped_at"], "answers")
        self.assertEqual(outcome["pending_stages"], [])
        self.assertEqual(mf.load(self.batch)["stages"]["audio"]["status"], mf.PENDING)

    def test_structure_gate_block_preserves_provisional_without_authorizing_downstream(self):
        self._isolate_scheduler()
        patcher = patch.object(auto, 'ZERO_TOUCH', False)
        patcher.start(); self.addCleanup(patcher.stop)
        mf.touch_stage(self.manifest, "extract", mf.COMPLETED)
        mf.save(self.batch, self.manifest)

        def gate(batch_dir, manifest):
            mf.touch_stage(manifest, "structure_gate", mf.BLOCKED)
            mf.save(batch_dir, manifest)
            return 1

        candidate = {"status": "needs_review", "attempted": 1}
        with patch.object(runner, "do_structure_gate", side_effect=gate) as run_gate, \
             patch("workflow.provisional.configured", return_value=True), \
             patch("workflow.provisional.run", return_value=candidate) as provisional, \
             patch.object(runner, "_execute_command") as execute:
            outcome = auto.advance(self.batch, {s.id: ["offline"] for s in contracts.STAGES})
        run_gate.assert_called_once()
        provisional.assert_called_once_with(self.batch)
        execute.assert_not_called()
        self.assertEqual(outcome["provisional"], candidate)
        self.assertEqual((outcome["stopped_at"], outcome["action"]), ("structure_gate", "gate"))
        self.assertEqual([s["stage"] for s in outcome["failed_stages"]], ["structure_gate"])
        manifest = mf.load(self.batch)
        self.assertEqual(manifest["stages"]["structure_gate"]["status"], mf.BLOCKED)
        for stage_id in ("answers", "audio", "types", "knowledge", "assemble", "upload"):
            self.assertEqual(manifest["stages"][stage_id]["status"], mf.PENDING)

    def test_command_failure_continues_independent_validation_and_reports_later_gate(self):
        self._isolate_scheduler()
        patcher = patch.object(auto, 'ZERO_TOUCH', False)
        patcher.start(); self.addCleanup(patcher.stop)
        for stage in contracts.STAGES:
            if stage.id == "underlines":
                break
            mf.touch_stage(self.manifest, stage.id, mf.COMPLETED)
        self.manifest["inputs"]["docx"] = {"exists": True, "path": "offline.docx"}
        mf.save(self.batch, self.manifest)
        calls = []

        def validate(batch_dir, manifest):
            calls.append("validate")
            mf.touch_stage(manifest, "validate", mf.COMPLETED)
            mf.save(batch_dir, manifest)
            return 0

        def review_gate(batch_dir, manifest):
            calls.append("review_gate")
            mf.touch_stage(manifest, "review_gate", mf.BLOCKED)
            mf.save(batch_dir, manifest)
            return 1

        with patch.object(runner, "_execute_command", side_effect=self._execute_offline(
                {"underlines": (mf.FAILED, 1)}, calls)), \
             patch.object(runner, "do_validate", side_effect=validate), \
             patch.object(runner, "do_review_gate", side_effect=review_gate):
            outcome = auto.advance(self.batch, {"review": ["offline"]})
        self.assertEqual(calls, ["underlines"])
        self.assertEqual((outcome["stopped_at"], outcome["action"]), ("underlines", "failed"))
        self.assertEqual([s["stage"] for s in outcome["failed_stages"]], ["underlines"])
        manifest = mf.load(self.batch)
        self.assertEqual(manifest["stages"]["review_gate"]["status"], mf.PENDING)
        self.assertEqual(manifest["stages"]["upload_gate"]["status"], mf.PENDING)
        self.assertEqual(manifest["stages"]["upload"]["status"], mf.PENDING)

    def test_no_human_confirmation_never_uploads(self):
        self._isolate_scheduler()
        patcher = patch.object(auto, 'ZERO_TOUCH', False)
        patcher.start(); self.addCleanup(patcher.stop)
        for stage in contracts.STAGES:
            if stage.id == "upload_gate":
                break
            mf.touch_stage(self.manifest, stage.id, mf.COMPLETED)
        mf.save(self.batch, self.manifest)
        with patch.object(runner, "_execute_command") as execute:
            outcome = auto.advance(self.batch, {"upload": ["offline"]})
        execute.assert_not_called()
        self.assertEqual((outcome["stopped_at"], outcome["action"]), ("upload_gate", "human"))
        pending = {s["stage"]: s for s in outcome["pending_stages"]}
        self.assertEqual(pending["upload_gate"]["action"], "human")
        self.assertIn("等待上游", pending["upload"]["reason"])
        manifest = mf.load(self.batch)
        self.assertEqual(manifest["stages"]["upload_gate"]["status"], mf.PENDING)
        self.assertEqual(manifest["stages"]["upload"]["status"], mf.PENDING)
        self.assertEqual(manifest["human_decisions"], [])

    def test_auto_never_submits_upload_even_with_preexisting_approval(self):
        self._isolate_scheduler()
        patcher = patch.object(auto, 'ZERO_TOUCH', False)
        patcher.start(); self.addCleanup(patcher.stop)
        for stage in contracts.STAGES:
            if stage.id == "upload":
                break
            mf.touch_stage(self.manifest, stage.id,
                           mf.APPROVED if stage.kind == contracts.HUMAN else mf.COMPLETED)
        mf.save(self.batch, self.manifest)
        with patch.object(runner, "_execute_command") as execute:
            outcome = auto.advance(self.batch, {})
        execute.assert_not_called()
        self.assertEqual((outcome["stopped_at"], outcome["action"]), ("upload", "human"))
        self.assertEqual(mf.load(self.batch)["stages"]["upload"]["status"], mf.PENDING)

    def test_command_preserves_manifest_changes_made_by_child(self):
        stage = contracts.Stage(id="extract", title="test", kind=contracts.COMMAND,
                                command=("offline",))
        source = self.batch / "01_source"
        source.mkdir(parents=True, exist_ok=True)
        (source / "正文.txt").write_text("1. Sample question A. first", encoding="utf-8")
        for relative in ("structure.json", "extraction.json", "metadata.json"):
            (source / relative).write_text("{}", encoding="utf-8")
        report = self.batch / "07_review/extraction-report.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("{}", encoding="utf-8")

        def run(*args, **kwargs):
            current = mf.load(self.batch)
            current["child_marker"] = {"receipt": "retained"}
            mf.touch_stage(current, "audio", mf.NEEDS_REVIEW, notes="child result")
            mf.save(self.batch, current)
            return argparse.Namespace(returncode=0, stdout="", stderr="")

        with patch.object(runner.subprocess, "run", side_effect=run):
            self.assertEqual(runner._execute_command(self.batch, self.manifest, stage), 0)
        saved = mf.load(self.batch)
        self.assertEqual(saved["child_marker"], {"receipt": "retained"})
        self.assertEqual(saved["stages"]["audio"]["notes"], "child result")
        self.assertEqual(self.manifest["child_marker"], saved["child_marker"])

    def test_extract_metadata_finishes_before_delivery_hashes(self):
        spec = contracts.STAGE_BY_ID['extract']
        for relative in spec.produces:
            path = self.batch / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('Question' if relative.endswith('.txt') else '{}')
        seen = []
        def identify(batch):
            self.assertEqual(mf.load(batch)['stages']['extract']['status'], mf.RUNNING)
            (batch / '01_source/metadata.json').write_text('{"source":"identified"}')
            seen.append('identified')
            return {'ran': True}
        with patch.object(runner.subprocess, 'run', return_value=argparse.Namespace(returncode=0, stdout='', stderr='')), \
             patch('workflow.structure_ai.auto_review', return_value={}), \
             patch('workflow.metadata_ai.auto_identify', side_effect=identify):
            self.assertEqual(runner._execute_command(self.batch, self.manifest, spec), 0)
        self.assertEqual(seen, ['identified'])
        state = mf.load(self.batch)
        self.assertEqual(state['stages']['extract']['output_hashes']['01_source/metadata.json'],
                         mf.sha256_file(self.batch / '01_source/metadata.json'))
        self.assertFalse(mf.is_stale(self.batch, state, spec)[0])

    def test_late_command_failure_does_not_erase_concurrent_completion(self):
        stage = contracts.Stage(id="extract", title="test", kind=contracts.COMMAND,
                                command=("offline",))
        def run(*args, **kwargs):
            current = mf.load(self.batch)
            mf.touch_stage(current, "extract", mf.COMPLETED, notes="other worker")
            mf.save(self.batch, current)
            return argparse.Namespace(returncode=1, stdout="", stderr="late failure")
        with patch.object(runner.subprocess, "run", side_effect=run):
            self.assertEqual(runner._execute_command(self.batch, self.manifest, stage), 1)
        self.assertEqual(mf.load(self.batch)["stages"]["extract"]["status"], mf.COMPLETED)
        self.assertEqual(self.manifest["stages"]["extract"]["notes"], "other worker")

    def test_subprocess_oserror_is_persisted_as_failed(self):
        stage = contracts.Stage(id="extract", title="test", kind=contracts.COMMAND,
                                command=("missing-command",))
        with patch.object(runner.subprocess, "run", side_effect=OSError("launch failed")):
            self.assertEqual(runner._execute_command(self.batch, self.manifest, stage), 127)
        record = mf.load(self.batch)["stages"]["extract"]
        self.assertEqual(record["status"], mf.FAILED)
        self.assertEqual(record["exit_code"], 127)
        self.assertIn("launch failed", record["notes"])
        self.assertIn("launch failed", (self.batch / record["log"]).read_text(encoding="utf-8"))

    def test_upload_entry_requires_frozen_request_even_after_local_gates_pass(self):
        target = self.batch / "06_batch" / "batch_meta.json"
        target.parent.mkdir(parents=True)
        target.write_text("{}", encoding="utf-8")
        self.manifest["stages"]["upload_gate"].update(
            status=mf.APPROVED, approved_batch_sha256=mf.sha256_file(target))
        mf.save(self.batch, self.manifest)
        args = argparse.Namespace(batch_dir=str(self.batch), yes=True, dry_run=False)
        with patch.object(mf, "is_stale", return_value=(False, "")), \
             patch.object(runner.review, "evaluate", return_value={"passed": True}) as review, \
             patch.object(runner, "flowchart_checks", return_value={"passed": True, "errors": []}) as flow, \
             patch.object(runner, "_execute_command") as execute, \
             patch.object(runner.subprocess, "run") as subprocess_run:
            self.assertEqual(runner.cmd_upload(args), 2)
        review.assert_called_once()
        flow.assert_called_once()
        execute.assert_not_called()
        subprocess_run.assert_not_called()
        self.assertEqual(mf.load(self.batch)["stages"]["upload"]["status"], mf.BLOCKED)

    def test_missing_final_batch_fails_validation_and_writes_report(self):
        with patch.object(runner.checks, "run_all") as run_all:
            self.assertEqual(runner.do_validate(self.batch, self.manifest), 1)
        run_all.assert_not_called()
        record = mf.load(self.batch)["stages"]["validate"]
        self.assertEqual(record["status"], mf.FAILED)
        self.assertIn("batch_meta.json", record["notes"])
        report = json.loads((self.batch / "07_review" / "validation-report.json").read_text(encoding="utf-8"))
        self.assertFalse(report["passed"])
        self.assertIn("FINAL_BATCH_MISSING", report["blocking_failures"])

    def _write_structure(self, structure: dict, body: str):
        src = self.batch / "01_source"
        src.mkdir(parents=True, exist_ok=True)
        (src / "structure.json").write_text(json.dumps(structure, ensure_ascii=False), encoding="utf-8")
        (src / "正文.txt").write_text(body, encoding="utf-8")

    def test_structure_gate_is_automatic_gate_not_human(self):
        self.assertEqual(contracts.STAGE_BY_ID["structure_gate"].kind, contracts.GATE)

    def test_do_structure_gate_passes_and_marks_completed(self):
        self._write_structure(
            {"sections": [{"type": "单项填空", "questions": [[1, 2]]}]},
            "2026年安徽省初中学业水平考试\n( )1. A. a  B. b\n( )2. A. c  B. d",
        )
        code = runner.do_structure_gate(self.batch, self.manifest)
        self.assertEqual(code, 0)
        self.assertEqual(mf.load(self.batch)["stages"]["structure_gate"]["status"], mf.COMPLETED)
        self.assertTrue((self.batch / "01_source" / "structure.confirmed.json").is_file())

    def test_structure_gate_rejects_concurrent_source_edit_without_publishing(self):
        self._write_structure(
            {"sections": [{"type": "单项填空", "questions": [[1, 2]]}]},
            "2026年安徽省初中学业水平考试\n( )1. A. a  B. b\n( )2. A. c  B. d",
        )
        confirmed = self.batch / "01_source/structure.confirmed.json"
        confirmed.write_text("old confirmation", encoding="utf-8")
        started, resume = threading.Event(), threading.Event()
        original = runner.structure_gate.run_gate
        def paused(*args, **kwargs):
            result = original(*args, **kwargs)
            started.set()
            if not resume.wait(5):
                raise AssertionError("test did not resume gate")
            return result
        result = []
        with patch.object(runner.structure_gate, "run_gate", side_effect=paused):
            thread = threading.Thread(target=lambda: result.append(runner.do_structure_gate(self.batch, self.manifest)))
            thread.start()
            self.assertTrue(started.wait(5))
            (self.batch / "01_source/正文.txt").write_text("changed", encoding="utf-8")
            resume.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, [1])
        self.assertEqual(confirmed.read_text(encoding="utf-8"), "old confirmation")
        self.assertEqual(mf.load(self.batch)["stages"]["structure_gate"]["status"], mf.PENDING)
        self.assertFalse((self.batch / "07_review/structure-gate.json").exists())

    def test_structure_gate_preserves_concurrent_unrelated_manifest_update(self):
        from workflow.local_agent import _agent_lock
        self._write_structure(
            {"sections": [{"type": "单项填空", "questions": [[1, 2]]}]},
            "2026年安徽省初中学业水平考试\n( )1. A. a  B. b\n( )2. A. c  B. d",
        )
        started, resume = threading.Event(), threading.Event()
        original = runner.structure_gate.run_gate
        def paused(*args, **kwargs):
            result = original(*args, **kwargs)
            started.set()
            if not resume.wait(5):
                raise AssertionError("test did not resume gate")
            return result
        result = []
        with patch.object(runner.structure_gate, "run_gate", side_effect=paused):
            thread = threading.Thread(target=lambda: result.append(runner.do_structure_gate(self.batch, self.manifest)))
            thread.start()
            self.assertTrue(started.wait(5))
            with _agent_lock(1):
                current = mf.load(self.batch)
                mf.touch_stage(current, "extract", mf.COMPLETED, notes="concurrent update")
                mf.save(self.batch, current)
            resume.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, [0])
        current = mf.load(self.batch)
        self.assertEqual(current["stages"]["extract"]["notes"], "concurrent update")
        self.assertEqual(current["stages"]["structure_gate"]["status"], mf.COMPLETED)

    def test_structure_gate_does_not_overwrite_concurrent_gate_result(self):
        from workflow.local_agent import _agent_lock
        self._write_structure(
            {"sections": [{"type": "单项填空", "questions": [[1, 2]]}]},
            "2026年安徽省初中学业水平考试\n( )1. A. a  B. b\n( )2. A. c  B. d",
        )
        original = runner.structure_gate.run_gate
        def competing(*args, **kwargs):
            report = original(*args, **kwargs)
            with _agent_lock(1):
                current = mf.load(self.batch)
                mf.touch_stage(current, "structure_gate", mf.BLOCKED, notes="other gate")
                mf.save(self.batch, current)
            return report
        with patch.object(runner.structure_gate, "run_gate", side_effect=competing):
            self.assertEqual(runner.do_structure_gate(self.batch, self.manifest), 1)
        self.assertEqual(mf.load(self.batch)["stages"]["structure_gate"]["notes"], "other gate")
        self.assertFalse((self.batch / "01_source/structure.confirmed.json").exists())

    def test_structure_gate_rejects_mismatched_nested_report_binding(self):
        self._write_structure({"sections": [{"type": "单项填空", "questions": [1]}]}, "( )1. A. a")
        original = runner.structure_gate.run_gate
        def corrupt(*args, **kwargs):
            report = original(*args, **kwargs)
            report["input_hashes"]["01_source/正文.txt"] = "stale"
            return report
        with patch.object(runner.structure_gate, "run_gate", side_effect=corrupt):
            self.assertEqual(runner.do_structure_gate(self.batch, self.manifest), 1)
        self.assertEqual(mf.load(self.batch)["stages"]["structure_gate"]["status"], mf.PENDING)
        self.assertFalse((self.batch / "07_review/structure-gate.json").exists())

    def test_stale_structure_does_not_dispatch_layout(self):
        self._write_structure({"sections": [{"type": "单项填空", "questions": [1]}]}, "[IMG x] ( )1. A. a")
        original = runner.structure_gate.run_gate
        def stale(*args, **kwargs):
            report = original(*args, **kwargs)
            report["passed"] = False
            report["layout_required"] = True
            (self.batch / "01_source/正文.txt").write_text("changed", encoding="utf-8")
            return report
        with patch.object(runner.structure_gate, "run_gate", side_effect=stale), \
             patch("workflow.layout_dispatch.prepare_layout_fallback") as dispatch:
            self.assertEqual(runner.do_structure_gate(self.batch, self.manifest), 1)
        dispatch.assert_not_called()
        self.assertEqual(mf.load(self.batch)["stages"]["structure_gate"]["status"], mf.PENDING)

    def test_structure_change_during_layout_does_not_publish(self):
        self._write_structure({"sections": [{"type": "单项填空", "questions": [1]}]}, "[IMG x] ( )1. A. a")
        original = runner.structure_gate.run_gate
        def needs_layout(*args, **kwargs):
            report = original(*args, **kwargs)
            report["passed"] = False
            report["layout_required"] = True
            return report
        def dispatch(*args):
            (self.batch / "01_source/正文.txt").write_text("changed", encoding="utf-8")
            return {"dispatch_state": "requires_render"}
        with patch.object(runner.structure_gate, "run_gate", side_effect=needs_layout), \
             patch("workflow.layout_dispatch.prepare_layout_fallback", side_effect=dispatch):
            self.assertEqual(runner.do_structure_gate(self.batch, self.manifest), 1)
        self.assertEqual(mf.load(self.batch)["stages"]["structure_gate"]["status"], mf.PENDING)
        self.assertFalse((self.batch / "07_review/structure-gate.json").exists())

    def test_do_structure_gate_blocks_on_empty_source(self):
        self._write_structure({"sections": [{"type": "单项填空", "questions": [1]}]}, "")
        code = runner.do_structure_gate(self.batch, self.manifest)
        self.assertEqual(code, 1)
        self.assertEqual(mf.load(self.batch)["stages"]["structure_gate"]["status"], mf.BLOCKED)

    def test_mark_rejects_manual_structure_gate_approval(self):
        self._write_structure({"sections": [{"type": "单项填空", "questions": [1]}]}, "( )1. A. a")
        args = argparse.Namespace(batch_dir=str(self.batch), stage="structure_gate",
                                  status=mf.APPROVED, actor="editor", note="",
                                  allow_missing=False, invalidate_downstream=False)
        self.assertEqual(runner.cmd_mark(args), 2)

    def test_single_run_lock_blocks_second_concurrent_run(self):
        """两个 runner run 同时驱动同一批次时，第二个必须快速失败，避免重复模型调用与台账交叉写。"""
        acquired = threading.Event()
        release = threading.Event()
        (self.batch / "workflow").mkdir(parents=True, exist_ok=True)

        def hold():
            with runner._single_run_lock(self.batch, timeout=0.3):
                acquired.set()
                release.wait(3)

        thread = threading.Thread(target=hold)
        thread.start()
        self.assertTrue(acquired.wait(2))
        try:
            with self.assertRaisesRegex(RuntimeError, "已有运行中的流水线"):
                with runner._single_run_lock(self.batch, timeout=0.3):
                    pass
        finally:
            release.set()
            thread.join(3)
        self.assertTrue((self.batch / "workflow" / ".run.lock").is_file())
        with runner._single_run_lock(self.batch, timeout=0.3):
            pass

    def test_cmd_run_accepts_config_argument(self):
        args = argparse.Namespace(batch_dir=str(self.batch), stage=None, until=None,
                                  force=False, dry_run=True, config=None)
        self.assertEqual(runner.cmd_run(args), 0)


    def test_partial_audio_candidate_does_not_authorize_downstream(self):
        self._ready_after_structure()
        calls = []
        with patch.object(auto, "ZERO_TOUCH", True), \
             patch.object(runner, "_execute_command", side_effect=self._execute_offline(
                 {"audio": (mf.NEEDS_REVIEW, 3)}, calls)):
            outcome = auto.advance(self.batch, {s.id: ["offline"] for s in contracts.STAGES})
        manifest = mf.load(self.batch)
        self.assertEqual(manifest["stages"]["audio"]["status"], mf.NEEDS_REVIEW)
        self.assertFalse(manifest["stages"]["audio"].get("execution_completed"))
        self.assertEqual(mf.delivery_status(self.batch, "audio"), "pending")
        self.assertEqual((outcome["stopped_at"], outcome["action"]), ("audio", "review"))


    def test_parallel_first_candidate_retains_needs_review_and_continues(self):
        from workflow import agent, local_agent
        self._ready_parallel_labels()
        dispatched = []
        def dispatch(batch, stages, config, **kwargs):
            dispatched.extend(stage.id for stage in stages)
            state = mf.load(batch)
            for stage in stages:
                if stage.id == 'answers':
                    path = batch / stage.produces[0]
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text('{}', encoding='utf-8')
                    mf.touch_stage(state, stage.id, mf.NEEDS_REVIEW)
                else:
                    mf.touch_stage(state, stage.id, mf.COMPLETED)
            mf.save(batch, state)
            return {stage.id: 3 if stage.id == 'answers' else 0 for stage in stages}
        with patch.object(auto, 'ZERO_TOUCH', True), \
             patch.object(auto, 'PARALLEL_AGENTS_ENABLED', True), \
             patch.object(agent, 'configured', return_value=True), \
             patch.object(local_agent, 'load_config', return_value={}), \
             patch.object(auto, '_parallel_ready_agents', side_effect=dispatch):
            auto.advance(self.batch, auto.load_bridge(), stop_at='explanations')
        self.assertCountEqual(dispatched, ['answers', 'audio'])
        self.assertEqual(mf.load(self.batch)['stages']['audio']['status'], mf.COMPLETED)
        record = mf.load(self.batch)['stages']['answers']
        self.assertEqual(record['status'], mf.NEEDS_REVIEW)
        self.assertFalse(record.get('continue_unqualified'))
        self.assertEqual(mf.delivery_status(self.batch, 'answers'), 'pending')

    def test_zero_touch_does_not_continue_without_structure_candidate(self):
        self._isolate_scheduler()
        mf.touch_stage(self.manifest, 'extract', mf.COMPLETED)
        mf.save(self.batch, self.manifest)
        def failed_gate(batch, manifest):
            mf.touch_stage(manifest, 'structure_gate', mf.BLOCKED)
            mf.save(batch, manifest)
            return 1
        with patch.object(auto, 'ZERO_TOUCH', True), \
             patch.object(runner, 'do_structure_gate', side_effect=failed_gate), \
             patch.object(runner, '_execute_command') as execute, \
             patch('workflow.provisional.configured', return_value=False):
            auto.advance(self.batch, {s.id: ['offline'] for s in contracts.STAGES})
        execute.assert_not_called()
        record = mf.load(self.batch)['stages']['structure_gate']
        self.assertEqual(record['status'], mf.BLOCKED)
        self.assertFalse(record.get('continue_unqualified', False))

    def test_zero_touch_never_executes_remote_upload(self):
        self._isolate_scheduler()
        for stage in contracts.STAGES:
            if stage.id == "upload_gate":
                break
            mf.touch_stage(self.manifest, stage.id, mf.COMPLETED)
        mf.save(self.batch, self.manifest)
        with patch.object(auto, "ZERO_TOUCH", True), \
             patch.object(runner, "_execute_command") as execute:
            auto.advance(self.batch, {"upload": ["offline"]})
        execute.assert_not_called()
        manifest = mf.load(self.batch)
        self.assertEqual(manifest["stages"]["upload_gate"]["status"], mf.SKIPPED)
        self.assertEqual(manifest["stages"]["upload"]["status"], mf.SKIPPED)
        self.assertEqual(manifest["stages"]["publish_gate"]["status"], mf.SKIPPED)
