"""Real isolated preassembly handoff; no live models, user batches or network."""
import copy
import io
import json
import tempfile
import unittest
from contextlib import nullcontext, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from workflow import agent, agent_protocol, agent_tasks, batch_fill, contracts, handoff, local_agent, manifest as mf, preassembly


class PreassemblyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="preassembly-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        text = "1. She reads a book. A. Yes B. No C. Good D. Bad"
        original = self.put("original.txt", text)
        self.put("01_source/正文.txt", text)
        self.put("01_source/structure.json", {"sections": [], "groups": []})
        self.put("01_source/extraction.json", {})
        self.put("07_review/extraction-report.json", {})
        self.put("01_source/metadata.json", {"original_sha256": mf.sha256_file(original)})
        self.put("01_source/structure.confirmed.json", {"units": [{
            "unit_kind": "standalone", "numbers": [1], "type": "单项填空", "stem": text,
            "source_span": {"basis": "ai_classified", "start_line": 1, "end_line": 1}}]})
        self.put("07_review/structure-gate.json", {"passed": True,
            "original_sha256": mf.sha256_file(original),
            "input_hashes": {name: mf.sha256_file(self.root / name) for name in (
                "01_source/正文.txt", "01_source/structure.json", "01_source/structure.confirmed.json")}})
        state = mf.new_manifest("preassembly-test", self.root, mf.register_input(self.root, "source", original), {})
        for sid in ("extract", "structure_gate"):
            mf.touch_stage(state, sid, mf.COMPLETED, output_hashes=mf.output_hashes(self.root, contracts.STAGE_BY_ID[sid]))
        mf.save(self.root, state)
        self.payload = {"items": [{"unit_id": "q-1", "type": "单项填空", "subtypes": ["四个选项"],
            "words": 10, "contentComplete": True, "evidence": ["正文 L1"]}]}
        self.response = {"status": "completed", "artifacts": {"03_types/types.json": self.payload}, "notes": [], "findings": []}
        for owner in (agent_protocol, local_agent):
            lock = patch.object(owner, "_agent_lock", side_effect=lambda *a, **k: nullcontext())
            lock.start()
            self.addCleanup(lock.stop)

    def put(self, rel, value):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def export(self):
        return agent_protocol.export_task(self.root, "preassemble")

    def import_response(self, package, response=None):
        value = {"protocol": agent_protocol.PROTOCOL, "task_id": package["task_id"],
                 "result": self.response if response is None else response}
        return agent_protocol.import_result(self.root, package["task_id"], self.put("result.json", value))

    def test_legacy_default_bridge_adds_assembly_without_overwriting_overrides(self):
        from workflow import auto
        legacy = ["{python}", "-m", "workflow.agent", "run", "--stage", "types", "--batch-dir", "{batch_dir}"]
        path = self.put("old-bridge.json", {"types": legacy, "answers": ["custom-answer"]})
        migrated = auto.load_bridge(path)
        self.assertEqual(migrated["preassemble"][5], "preassemble")
        self.assertEqual(migrated["answers"], ["custom-answer"])
        self.put("old-bridge.json", {"types": legacy, "preassemble": ["custom-assembly"]})
        self.assertEqual(auto.load_bridge(path)["preassemble"], ["custom-assembly"])

    def test_export_owns_source_and_labels_and_import_unlocks_answers(self):
        package = self.export()
        self.assertEqual(package["status"], "ready")
        self.assertTrue((self.root / batch_fill.BATCH_BASE).is_file())
        self.assertFalse(mf.dependency_satisfied(mf.load(self.root), "preassemble"))
        task = json.loads((Path(package["package_dir"]) / "task.json").read_text())
        self.assertEqual(task["stage"], "preassemble")
        self.assertEqual(task["expected_artifacts"], ["03_types/types.json"])
        base = (self.root / batch_fill.BATCH_BASE).read_bytes()
        self.assertTrue(self.import_response(package)["completed"])
        state = mf.load(self.root)
        self.assertTrue(mf.dependency_satisfied(state, "preassemble"))
        self.assertEqual(state["stages"]["types"]["status"], mf.PENDING)
        self.assertEqual((self.root / batch_fill.BATCH_BASE).read_bytes(), base)
        batch = json.loads((self.root / batch_fill.BATCH).read_text())
        self.assertEqual(batch["items"][0]["question_type"], "单项填空")
        with patch.object(local_agent, "invoke", side_effect=AssertionError("checkpoint must not call a model")):
            self.assertTrue(preassembly.check(self.root)["passed"])
        self.assertEqual(mf.load(self.root)["stages"]["preassemble"]["status"], mf.COMPLETED)

    def test_fresh_scheduler_delivers_types_before_three_label_workers(self):
        from workflow import auto, runner
        self.assertTrue(self.import_response(self.export())['completed'])
        self.put('01_source/附录.txt', '参考答案\n1.A 【解析】原卷解析。')
        dispatched = []
        def execute(batch, state, spec):
            self.assertEqual(spec.id, 'types')
            self.assertTrue(preassembly.check(batch)['passed'])
            runner._record_success(batch, state, spec)
            mf.save(batch, state)
            return 0
        def labels(batch, stages, config):
            self.assertTrue(mf.dependency_satisfied(mf.load(batch), 'types'))
            dispatched.extend(s.id for s in stages)
            return {s.id: 3 for s in stages}
        with patch('workflow.self_heal.recover', return_value={}), \
             patch.object(agent, 'configured', return_value=True), \
             patch.object(local_agent, 'load_config', return_value={}), \
             patch.object(runner, '_execute_command', side_effect=execute), \
             patch.object(auto, '_parallel_ready_agents', side_effect=labels):
            auto._advance_locked(self.root, auto.load_bridge(), stop_at='assemble')
        self.assertCountEqual(dispatched, ['answers', 'explanations', 'knowledge'])
        self.assertEqual(mf.load(self.root)['stages']['types']['status'], mf.COMPLETED)

    def test_explanation_task_accepts_original_material_after_answer_peer_delivers(self):
        from workflow import runner
        self.assertTrue(self.import_response(self.export())['completed'])
        state = mf.load(self.root)
        self.assertTrue(preassembly.check(self.root)['passed'])
        runner._record_success(self.root, state, contracts.STAGE_BY_ID['types'])
        mf.save(self.root, state)
        self.put('01_source_ans/正文.txt', '1.A 【解析】原答案材料解析。')
        package = agent_protocol.export_task(self.root, 'explanations')
        self.assertEqual(package['status'], 'ready')
        # A concurrent answer worker may now install output and progress. Neither
        # is a consumed explanation input; all original source bytes stay bound.
        self.put('01_source_ans/answers.json', {'answers': {'1': 'A'}, 'evidence': {'1': ['原文']}, 'missing': {}})
        state = mf.load(self.root)
        mf.touch_stage(state, 'answers', mf.COMPLETED)
        mf.save(self.root, state)
        result = {'status': 'completed', 'artifacts': {'04_knowledge/explanations.json': {'items': [{
            'unit_id': 'q-1', 'explanation': '原答案材料解析。', 'articleSummary': '无',
            'subquestions': [], 'evidence': ['01_source_ans/正文.txt L1']}] }}, 'notes': [], 'findings': []}
        self.assertTrue(self.import_response(package, result)['completed'])
        self.assertTrue(mf.dependency_satisfied(mf.load(self.root), 'explanations'))

    def test_late_audio_delivery_preserves_views_and_requires_real_revalidation(self):
        self._late_audio_delivery('fastpath')

    def test_portable_audio_import_projects_views_and_revalidates(self):
        self._late_audio_delivery('portable')

    def test_scoped_audio_import_projects_views_and_revalidates(self):
        self._late_audio_delivery('scoped')

    def _late_audio_delivery(self, mode):
        import hashlib
        from workflow import runner, assemble_local, stage_preflight
        from audio.binding import listening_context
        # Retain the real source/structure delivery; only its catalog type differs.
        from workflow import structure_gate
        raw = 'Ⅰ.关键词语选择\n1. She reads a book. A. Yes B. No C. Good D. Bad'
        self.put('original.txt', raw)
        self.put('01_source/正文.txt', raw)
        self.put('01_source/metadata.json', {'original_sha256': mf.sha256_file(self.root / 'original.txt')})
        sections = [{'type': '听力关键词语选择', 'questions': [1]}]
        self.put('01_source/structure.json', {'sections': sections, 'groups': []})
        structure = {'units': structure_gate._build_units(sections, [], raw.splitlines())}
        self.put('01_source/structure.confirmed.json', structure)
        gate = json.loads((self.root / '07_review/structure-gate.json').read_text())
        gate['input_hashes'] = {rel: mf.sha256_file(self.root / rel) for rel in ('01_source/正文.txt', '01_source/structure.json', '01_source/structure.confirmed.json')}
        gate['original_sha256'] = mf.sha256_file(self.root / 'original.txt')
        self.put('07_review/structure-gate.json', gate)
        state = mf.load(self.root)
        for sid in ('extract', 'structure_gate'):
            state['stages'][sid]['output_hashes'] = mf.output_hashes(self.root, contracts.STAGE_BY_ID[sid])
        mf.save(self.root, state)
        self.payload['items'][0].update(type='听力关键词语选择', subtypes=[])
        self.assertTrue(self.import_response(self.export())['completed'])
        state = mf.load(self.root)
        runner._record_success(self.root, state, contracts.STAGE_BY_ID['types'])
        self.put('01_source_ans/正文.txt', '1.A')
        mf.save(self.root, state)
        values = {
            'answers': {'answers': {'1': 'A'}, 'evidence': {'1': ['答案原文']}, 'missing': {}},
            'explanations': {'items': [{'unit_id': 'q-1', 'explanation': '无', 'articleSummary': '无', 'subquestions': [], 'evidence': ['未提供解析']}]},
            'knowledge': {'items': [{'unit_id': 'q-1', 'knowledge': ['词汇/词汇辨析/动词辨析'], 'subquestions': [], 'evidence': ['原文']}]}}
        for sid, value in values.items():
            self.put(agent_tasks.ARTIFACTS[sid], value)
            batch_fill.sync(self.root, sid)
            runner._record_success(self.root, state, contracts.STAGE_BY_ID[sid])
        mf.save(self.root, state)
        self.assertEqual(assemble_local.try_assemble(self.root), 0)
        runner._record_success(self.root, state, contracts.STAGE_BY_ID['assemble'])
        mf.save(self.root, state)
        self.assertEqual(runner._execute_command(self.root, state, contracts.STAGE_BY_ID['meta']), 0)
        self.assertEqual(runner._execute_command(self.root, state, contracts.STAGE_BY_ID['underlines']), 0)
        # Final-only markup/metadata is a legitimate existing view; media
        # projection must not regenerate this file from the intermediate batch.
        final = json.loads((self.root / '06_batch/batch_meta.json').read_text())
        final['items'][0]['stem'] = '<u>' + final['items'][0]['stem'] + '</u>'
        final['items'][0]['localExtension'] = {'preserve': 'exactly'}
        self.put('06_batch/batch_meta.json', final)
        runner._record_success(self.root, state, contracts.STAGE_BY_ID['underlines'])
        mf.save(self.root, state)
        self.assertEqual(runner.do_validate(self.root, state), 0)
        views = {rel: json.loads((self.root / rel).read_text()) for rel in ('06_batch/batch.json', '06_batch/batch_meta.base.json', '06_batch/batch_meta.json')}
        clip = self.put('02_audio/seg-q-1.mp3', 'ID3cut')
        binding, _ = listening_context(self.root)
        item = {'unit_id': 'q-1', 'file': '02_audio/seg-q-1.mp3', 'start': 0.0, 'end': 1.0, 'evidence': ['real fixture cut'], 'verified': True, 'file_sha256': hashlib.sha256(clip.read_bytes()).hexdigest()}
        self.put('02_audio/segment-evidence.json', {'generator': 'audio.segment/v2', 'unit_binding': binding, 'items': [item]})
        self.put('02_audio/audio_map.json', {'unit_binding': binding, 'items': [{k: item[k] for k in ('unit_id', 'file', 'start', 'end')}]})
        protected = (batch_fill.BATCH, batch_fill.BATCH_BASE, batch_fill.STATE,
                     '06_batch/batch_meta.base.json', '06_batch/batch_meta.json')
        previous = {rel: (self.root / rel).read_bytes() for rel in protected}
        inspect = handoff.inspect_stage
        def reject_view(batch, current, sid, **kwargs):
            if sid == 'underlines':
                return {'passed': False, 'errors': ['test consumer rejection']}
            return inspect(batch, current, sid, **kwargs)
        if mode == 'fastpath':
            with patch.object(handoff, 'inspect_stage', side_effect=reject_view):
                self.assertEqual(stage_preflight.run(self.root, 'audio')[0], 3)
            self.assertEqual(previous, {rel: (self.root / rel).read_bytes() for rel in protected})
            audio_result = stage_preflight.run(self.root, 'audio')
            self.assertEqual(audio_result[0], 0, audio_result)
        else:
            if mode == 'scoped':
                from workflow import self_heal
                self_heal._feedback(self.root, 'audio', '局部修复', 'q-1 切片复验', 1, unit_ids=['q-1'])
            package = agent_protocol.export_task(self.root, 'audio')
            self.assertEqual(package['status'], 'ready', package)
            payload = {'items': [{k: item[k] for k in ('unit_id', 'file', 'start', 'end', 'evidence')}]}
            if mode != 'scoped':
                payload['unit_binding'] = binding
            response = {'status': 'completed', 'artifacts': {'02_audio/audio_map.json': payload}, 'notes': [], 'findings': []}
            agent_tasks.accept_result(self.root, 'audio', response, validate_only=True)
            self.assertEqual(previous, {rel: (self.root / rel).read_bytes() for rel in protected})
            # The real importer installs output, records delivery once, projects
            # media and invalidates validation. No fastpath or sync is mocked.
            with patch.object(batch_fill, 'sync', wraps=batch_fill.sync) as sync:
                self.assertTrue(self.import_response(package, response)['completed'])
            audio_syncs = [call for call in sync.call_args_list if call.args[1] == 'audio']
            self.assertEqual(len(audio_syncs), 1)
        state = mf.load(self.root)
        self.assertEqual(state['stages']['validate']['status'], mf.INVALIDATED)
        self.assertTrue(agent_protocol._preconditions(state, contracts.STAGE_BY_ID['review']))
        for rel, before in views.items():
            after = json.loads((self.root / rel).read_text())
            self.assertEqual(after['items'][0]['attachments'][-1]['url'], '02_audio/seg-q-1.mp3')
            if 'attachments' in before['items'][0]:
                after['items'][0]['attachments'] = before['items'][0]['attachments']
            else:
                after['items'][0].pop('attachments')
            self.assertEqual(after, before)
        for sid in ('assemble', 'meta', 'underlines'):
            self.assertFalse(mf.is_stale(self.root, state, contracts.STAGE_BY_ID[sid])[0], sid)
        from workflow import auto
        with patch('workflow.self_heal.recover', return_value={}), \
             patch.object(runner, 'do_validate', wraps=runner.do_validate) as validate:
            auto._advance_locked(self.root, {}, stop_at='review_gate')
        validate.assert_called_once()
        state = mf.load(self.root)
        self.assertEqual(agent_protocol._preconditions(state, contracts.STAGE_BY_ID['review']), [])
        saved = mf.sha256_file(self.root / '06_batch/batch_meta.json')
        if mode == 'fastpath':
            self.assertEqual(stage_preflight.run(self.root, 'audio')[0], 0)
        else:
            self.assertTrue(self.import_response(package, response)['completed'])
        self.assertEqual(mf.load(self.root)['stages']['validate']['status'], mf.COMPLETED)
        self.assertEqual(saved, mf.sha256_file(self.root / '06_batch/batch_meta.json'))

    def test_invalid_labels_never_complete_or_unlock_downstream(self):
        package = self.export()
        invalid = copy.deepcopy(self.response)
        invalid["artifacts"]["03_types/types.json"]["items"][0]["contentComplete"] = False
        with self.assertRaisesRegex(ValueError, "contentComplete|incomplete"):
            self.import_response(package, invalid)
        self.assertFalse(mf.dependency_satisfied(mf.load(self.root), "preassemble"))
        self.assertFalse((self.root / "03_types/types.json").exists())

    def test_source_base_changed_during_model_call_rejects_import(self):
        package = self.export()
        base = json.loads((self.root / batch_fill.BATCH_BASE).read_text())
        base["items"][0]["stem"] = "Changed"
        self.put(batch_fill.BATCH_BASE, base)
        with self.assertRaisesRegex(ValueError, "stale"):
            self.import_response(package)

    def test_skeleton_alone_is_not_a_delivered_assembly(self):
        preassembly.prepare(self.root)
        state = mf.load(self.root)
        mf.touch_stage(state, "preassemble", mf.COMPLETED)
        checked = handoff.inspect_stage(self.root, state, "preassemble")
        self.assertFalse(checked["passed"])
        self.assertIn("types.json", str(checked["errors"]))
        self.assertFalse(mf.dependency_satisfied(state, "preassemble"))

    def test_local_agent_completes_preassembly_with_one_type_model_call(self):
        config = {"max_input_chars": 180000, "timeout_seconds": 30, "retries": 0,
                  "label_split": False, "unit_split": False}
        with patch.object(local_agent, "load_config", return_value=config), \
             patch.object(local_agent, "_part_model_binding", return_value=None), \
             patch.object(local_agent, "invoke", return_value={"response": self.response}) as invoke, \
             redirect_stdout(io.StringIO()):
            code = agent.main(["run", "--stage", "preassemble", "--batch-dir", str(self.root), "--config", "unused.json"])
        self.assertEqual(code, 0)
        self.assertEqual(invoke.call_count, 1)
        self.assertTrue(mf.dependency_satisfied(mf.load(self.root), "preassemble"))
        self.assertEqual(contracts.STAGE_BY_ID["types"].kind, contracts.COMMAND)

    def test_plugin_router_update_during_model_call_does_not_fail_stage(self):
        plugin = self.root / "plugin"
        (plugin / "backend").mkdir(parents=True)
        (plugin / "config").mkdir(parents=True)
        router = plugin / "backend/wf-router.mjs"
        router.write_text("// router v1\n", encoding="utf-8")
        (plugin / "config/model-router.json").write_text(json.dumps(
            {"default": {"provider": "official", "model": "flash"}}), encoding="utf-8")
        config = {"max_input_chars": 180000, "timeout_seconds": 30, "retries": 0,
                  "label_split": False, "unit_split": False, "candidate_cache": False,
                  "command": ["node", str(router), "{prompt_file}", "{response_file}"]}
        def update_plugin(*args, **kwargs):
            # The plugin updating its own source is not a change to this batch.
            router.write_text("// router v2 with an updated default\n", encoding="utf-8")
            return {"response": self.response}
        with patch.object(local_agent, "load_config", return_value=config), \
             patch.object(local_agent, "invoke", side_effect=update_plugin), \
             redirect_stdout(io.StringIO()):
            code = agent.main(["run", "--stage", "preassemble", "--batch-dir", str(self.root), "--config", "unused.json"])
        self.assertEqual(code, 0)
        self.assertTrue(mf.dependency_satisfied(mf.load(self.root), "preassemble"))

    def test_local_original_changed_during_model_call_rejects_install(self):
        config = {"max_input_chars": 180000, "timeout_seconds": 30, "retries": 0,
                  "label_split": False, "unit_split": False}
        def mutate_original(*args, **kwargs):
            self.put("original.txt", "A different original paper")
            return {"response": self.response}
        with patch.object(local_agent, "load_config", return_value=config), \
             patch.object(local_agent, "_part_model_binding", return_value=None), \
             patch.object(local_agent, "invoke", side_effect=mutate_original), \
             redirect_stdout(io.StringIO()):
            code = agent.main(["run", "--stage", "preassemble", "--batch-dir", str(self.root), "--config", "unused.json"])
        self.assertNotEqual(code, 0)
        self.assertFalse((self.root / "03_types/types.json").exists())
        self.assertNotEqual(mf.load(self.root)["stages"]["preassemble"]["status"], mf.COMPLETED)

    def test_type_only_repair_keeps_shared_assembly_delivery_current(self):
        self.import_response(self.export())
        state = mf.load(self.root)
        mf.touch_stage(state, "types", mf.PENDING)
        mf.save(self.root, state)
        package = agent_protocol.export_task(self.root, "types")
        repaired = copy.deepcopy(self.response)
        repaired["artifacts"]["03_types/types.json"]["items"][0]["words"] = 11
        self.import_response(package, repaired)
        self.assertTrue(mf.dependency_satisfied(mf.load(self.root), "preassemble"))

    def test_rejected_handoff_keeps_actual_diagnostic_message(self):
        preassembly.prepare(self.root)
        with self.assertRaisesRegex(ValueError, "types.json"):
            preassembly.check(self.root)
        # A malformed unowned source object must produce the real source error,
        # not TypeError from joining structured diagnostic rows.
        with patch.object(preassembly, "require_current_source"), \
             patch("workflow.handoff.inspect_stage", return_value={"passed": False,
                "errors": [{"code": "HANDOFF_INVALID", "message": "q-1 stem is not a string"}]}):
            with self.assertRaisesRegex(ValueError, "q-1 stem"):
                agent_tasks.accept_result(self.root, "preassemble", self.response)

    def test_assembly_retry_feedback_reaches_internal_type_task(self):
        from workflow import self_heal
        preassembly.prepare(self.root)
        path = self_heal._feedback(self.root, "preassemble", "修正题型字段", "q-1 题型不合规", 1)
        self.assertEqual(path, "workflow/repair-feedback/types.json")
        task = agent_tasks.build_task(self.root, "preassemble")
        self.assertIn("q-1 题型不合规", task["prompt"])
        self.assertIn(path, task["input_hashes"])

    def test_failed_compatibility_check_returns_to_real_producer(self):
        from workflow import self_heal
        self.import_response(self.export())
        state = mf.load(self.root)
        mf.touch_stage(state, "types", mf.FAILED)
        mf.save(self.root, state)
        outcome = self_heal._apply(self.root, state, "types", "q-1 题型不合规",
            {"action": "retry_stage", "reason": "修正题型"}, 1, {"files": {}})
        self.assertEqual(outcome["target_stage"], "preassemble")
        self.assertEqual(mf.load(self.root)["stages"]["preassemble"]["status"], mf.PENDING)
        self.assertFalse(outcome["verified"])

    def test_legacy_source_only_task_can_finish_without_bypassing_new_contract(self):
        preassembly.prepare(self.root)
        state = mf.load(self.root)
        mf.touch_stage(state, "preassemble", mf.COMPLETED,
                       output_hashes={batch_fill.BATCH_BASE: mf.sha256_file(self.root / batch_fill.BATCH_BASE)})
        mf.save(self.root, state)
        self.assertFalse(mf.dependency_satisfied(state, "preassemble"))
        package = agent_protocol.export_task(self.root, "types")
        self.assertEqual(package["status"], "ready")
        self.assertTrue(self.import_response(package)["completed"])
        self.assertTrue(mf.dependency_satisfied(mf.load(self.root), "preassemble"))


if __name__ == "__main__":
    unittest.main()
