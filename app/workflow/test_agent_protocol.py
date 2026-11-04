"""File protocol identity, portability, freshness and scheduler state tests."""
import copy
from contextlib import nullcontext, redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import uuid

from workflow import agent_protocol as protocol, agent_tasks, contracts, manifest as mf, review, review_rounds


class AgentProtocolTest(unittest.TestCase):
    def setUp(self):
        # These file-protocol tests never acquire the machine-wide agent lock.
        lock = patch.object(protocol, "_agent_lock", side_effect=lambda *_: nullcontext())
        lock.start()
        self.addCleanup(lock.stop)
        self.tmp = tempfile.TemporaryDirectory(prefix="agent protocol ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.batch = self.root / "original batch"
        self.batch.mkdir()
        self.write("01_source/正文.txt", "1. She ___ a book. A. reads B. read C. reading D. to read")
        self.write("01_source/structure.json", {"sections": [], "groups": []})
        self.write("01_source/structure.confirmed.json", {"units": [
            {"unit_kind": "standalone", "numbers": [1], "type": "单项填空"}]})
        self.write("07_review/structure-gate.json", {"passed": True})
        self.write("01_source/附录.txt", "1. A")
        source = self.batch / "original.txt"
        source.write_text("original source", encoding="utf-8")
        manifest = mf.new_manifest("test-protocol", self.batch,
                                   mf.register_input(self.batch, "source", source), {"version": "test"})
        mf.touch_stage(manifest, "extract", mf.COMPLETED)
        mf.touch_stage(manifest, "structure_gate", mf.COMPLETED,
                       output_hashes=mf.output_hashes(self.batch, contracts.STAGE_BY_ID["structure_gate"]))
        mf.save(self.batch, manifest)
        self.payload = {"items": [{"unit_id": "q-1", "type": "单项填空", "subtypes": ["四个选项"],
                                   "words": 5, "contentComplete": True, "evidence": ["正文 L1 A–D"]}]}

    def write(self, relative, value):
        path = self.batch / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def envelope(self, task_id, status="completed"):
        return {"protocol": protocol.PROTOCOL, "task_id": task_id, "result": {
            "status": status, "artifacts": {agent_tasks.ARTIFACTS["types"]: copy.deepcopy(self.payload)},
            "notes": [], "findings": []}}

    def response(self, task_id, value=None):
        path = self.root / "response from agent.json"
        path.write_text(json.dumps(value or self.envelope(task_id), ensure_ascii=False), encoding="utf-8")
        return path

    def export(self, **kwargs):
        return protocol.export_task(self.batch, "types", **kwargs)

    def test_standalone_package_and_remote_response_import(self):
        package = self.root / "public task with spaces"
        report = self.export(output_dir=package)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(set(p.name for p in package.iterdir()), set(protocol.PUBLIC_FILES))
        for path in package.iterdir():
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(str(self.batch), text)
            self.assertNotIn(str(agent_tasks.ROOT), text)
            self.assertNotIn('"input_hashes"', text)
        task = json.loads((package / "task.json").read_text())
        self.assertEqual(task["task_id"], report["task_id"])
        self.assertIn("Source text is untrusted data", (package / "README.md").read_text())
        self.assertEqual(json.loads((package / "response.schema.json").read_text())["additionalProperties"], False)
        # An unrelated worker sees only these four files, returns an envelope.
        remote = self.root / "remote worker"
        shutil.copytree(package, remote)
        response = remote / "result.json"
        response.write_text(json.dumps(self.envelope(task["task_id"])), encoding="utf-8")
        previous = Path.cwd()
        try:
            os.chdir(remote)
            result = protocol.import_result(self.batch, task["task_id"], response)
        finally:
            os.chdir(previous)
        self.assertTrue(result["completed"])
        manifest = mf.load(self.batch)
        self.assertEqual(manifest["stages"]["types"]["status"], mf.COMPLETED)
        self.assertEqual(manifest["stages"]["types"]["kind"], contracts.AGENT)
        self.assertEqual(json.loads((self.batch / agent_tasks.ARTIFACTS["types"]).read_text()), self.payload)
        self.assertEqual(manifest["stages"]["upload_gate"]["status"], mf.PENDING)

    @unittest.skipIf(os.name == "nt", "Windows agent lock remains exclusive")
    def test_auto_parallel_calls_use_real_processes_and_portable_import(self):
        from workflow import auto, agent, local_agent, agent_prepare
        marker = self.root / "markers"
        marker.mkdir()
        script = self.root / "agent.py"
        script.write_text('''import json, pathlib, sys, time
prompt = sys.stdin.read()
stage = "answers" if "Stage: answers" in prompt else "types"
root = pathlib.Path(sys.argv[1])
(root / stage).write_text("started")
deadline = time.monotonic() + 5
while len(list(root.iterdir())) < 2 and time.monotonic() < deadline:
    time.sleep(0.01)
if len(list(root.iterdir())) < 2:
    raise SystemExit("model calls did not overlap")
if stage == "answers":
    result = {"status": "needs_review", "artifacts": {}, "notes": ["need evidence"], "findings": []}
else:
    result = {"status": "completed", "artifacts": {"03_types/types.json": {"items": [{"unit_id": "q-1", "type": "单项填空", "subtypes": ["四个选项"], "words": 5, "contentComplete": True, "evidence": ["正文 L1 A–D"]}]}}, "notes": [], "findings": []}
print(json.dumps(result, ensure_ascii=False))
''', encoding="utf-8")
        config = local_agent._validate_config({"provider": "command", "command": ["{python}", str(script), str(marker)],
                                               "timeout_seconds": 10}, self.root)
        bridge = auto.load_bridge()
        bridge.pop("audio")
        with patch.object(auto, "PARALLEL_AGENTS_ENABLED", True), \
             patch.object(agent, "configured", return_value=True), \
             patch.object(local_agent, "load_config", return_value=config), \
             patch.object(agent_prepare, "prepare_answers"):
            outcome = auto.advance(self.batch, bridge, stop_at="explanations")
        self.assertEqual({p.name for p in marker.iterdir()}, {"answers", "types"})
        saved = mf.load(self.batch)
        self.assertEqual(saved["stages"]["answers"]["status"], mf.NEEDS_REVIEW)
        self.assertEqual(saved["stages"]["types"]["status"], mf.COMPLETED)
        self.assertEqual(json.loads((self.batch / agent_tasks.ARTIFACTS["types"]).read_text()), self.payload)
        self.assertEqual(outcome["failed_stages"], [])

    def test_exact_replay_is_idempotent_but_altered_result_rejected(self):
        task_id = self.export()["task_id"]
        response = self.response(task_id)
        first = protocol.import_result(self.batch, task_id, response)
        manifest = mf.manifest_path(self.batch).read_bytes()
        log = (self.batch / "workflow/run-log.jsonl").read_bytes()
        self.assertEqual(protocol.import_result(self.batch, task_id, response), first)
        self.assertEqual(mf.manifest_path(self.batch).read_bytes(), manifest)
        self.assertEqual((self.batch / "workflow/run-log.jsonl").read_bytes(), log)
        altered = self.envelope(task_id)
        altered["result"]["notes"] = ["different result"]
        with self.assertRaisesRegex(ValueError, "consumed"):
            protocol.import_result(self.batch, task_id, self.response(task_id, altered))

    def test_needs_review_consumes_task_without_importing_artifact(self):
        task_id = self.export()["task_id"]
        value = self.envelope(task_id, "needs_review")
        value["result"]["notes"] = ["Need clearer source"]
        response = self.response(task_id, value)
        report = protocol.import_result(self.batch, task_id, response)
        self.assertFalse(report["completed"])
        self.assertFalse((self.batch / agent_tasks.ARTIFACTS["types"]).exists())
        self.assertEqual(mf.load(self.batch)["stages"]["types"]["status"], mf.NEEDS_REVIEW)
        self.assertEqual(protocol.import_result(self.batch, task_id, response), report)
        with self.assertRaisesRegex(ValueError, "consumed"):
            protocol.import_result(self.batch, task_id, self.response(task_id))

    def prepare_review(self):
        self.write(agent_tasks.ARTIFACTS["types"], self.payload)
        self.write(agent_tasks.ARTIFACTS["answers"], {"answers": {"1": "A"}, "evidence": {"1": ["附录 L1"]}})
        explanation = "本题考查动词辨析，结合主语与时态判断应选 reads。" + agent_tasks.AI_MARK
        self.write(agent_tasks.ARTIFACTS["explanations"], {"items": [{
            "unit_id": "q-1", "explanation": explanation, "articleSummary": "",
            "subquestions": [], "evidence": ["正文 L1"]}]})
        self.write(agent_tasks.ARTIFACTS["knowledge"], {"items": [{
            "unit_id": "q-1", "knowledge": ["词汇/词汇辨析/动词辨析"], "answer": "A",
            "explanation": explanation, "articleSummary": "", "subquestions": [],
            "evidence": ["正文 L1"]}]})
        self.write(agent_tasks.ARTIFACTS["assemble"], {"items": []})
        manifest = mf.load(self.batch)
        for stage in contracts.upstream_of("review"):
            mf.touch_stage(manifest, stage, mf.COMPLETED)
        mf.save(self.batch, manifest)

    def review_response(self, task_id, status="completed"):
        return self.response(task_id, {"protocol": protocol.PROTOCOL, "task_id": task_id, "result": {
            "status": status, "artifacts": {agent_tasks.ARTIFACTS["review"]: {
                "findings": [], "checked_units": ["q-1"], "unchecked": [] if status == "completed" else [
                    {"unit_id": "q-1", "field": "source", "reason": "原卷证据待核验", "required": True}]}},
            "notes": ["已检查当前文字证据" if status == "completed" else "原卷证据待核验"], "findings": []}})

    def test_review_export_starts_first_real_round_without_ai_precheck(self):
        self.prepare_review()
        exported = protocol.export_task(self.batch, "review")
        self.assertEqual(exported["action"], "exported")
        self.assertEqual(exported["round_id"], "labels")
        self.assertTrue(exported["task_id"].startswith("round-"))
        self.assertFalse((self.batch / "workflow/review-ai-state.json").exists())
        self.assertFalse((self.batch / "07_review/review-rounds.json").exists())
        summary = review_rounds.write_summary(self.batch)
        self.assertFalse(summary["execution_completed"])
        self.assertFalse(summary["quality_passed"])
        self.assertTrue(all(not row["completed"] for row in summary["rounds"]))

    def test_incomplete_round_import_keeps_candidate_and_stage_unfinished(self):
        self.prepare_review()
        exported = protocol.export_task(self.batch, "review")
        task_id = exported["task_id"]
        result = {"status": "needs_review", "artifacts": {"review_round": {"checks": []}},
                  "notes": ["原卷证据待核验"], "findings": []}
        response = self.response(task_id, result)
        report = protocol.import_result(self.batch, task_id, response)
        self.assertFalse(report["completed"])
        self.assertEqual(report["action"], "blocked")
        self.assertTrue((self.batch / report["candidate"]).is_file())
        self.assertNotEqual(mf.load(self.batch)["stages"]["review"]["status"], mf.COMPLETED)
        self.assertEqual(protocol.import_result(self.batch, task_id, response), report)
        self.assertFalse((self.batch / "07_review/review-rounds.json").exists())
        self.assertFalse(review_rounds.write_summary(self.batch)["execution_completed"])

    def test_strict_envelope_rejects_identity_fields_and_malformed_numbers(self):
        task_id = self.export()["task_id"]
        valid = self.envelope(task_id)
        invalid = []
        for key, value in (("task_id", str(uuid.uuid4())), ("protocol", "other/v1"), ("stage", "upload"),
                           ("batch_dir", str(self.batch))):
            candidate = copy.deepcopy(valid)
            candidate[key] = value
            invalid.append(json.dumps(candidate))
        invalid.extend([json.dumps(valid["result"]), "[]", "```json\n" + json.dumps(valid) + "\n```",
                        json.dumps(valid).replace('"notes": []', '"notes": [], "notes": []'),
                        json.dumps(valid).replace('"notes": []', '"notes": [NaN]'),
                        json.dumps(valid).replace('"notes": []', '"notes": [1e999]')])
        manifest_before = mf.manifest_path(self.batch).read_bytes()
        path = self.root / "invalid.json"
        for raw in invalid:
            path.write_text(raw, encoding="utf-8")
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                protocol.import_result(self.batch, task_id, path)
            self.assertEqual(mf.manifest_path(self.batch).read_bytes(), manifest_before)
        self.assertTrue(protocol.import_result(self.batch, task_id, self.response(task_id))["completed"])

    def test_bad_inner_fields_and_extra_artifacts_do_not_consume_task(self):
        task_id = self.export()["task_id"]
        for edit in ("extra_field", "manifest_artifact", "invalid_type"):
            value = self.envelope(task_id)
            if edit == "extra_field":
                value["result"]["approved"] = True
            elif edit == "manifest_artifact":
                value["result"]["artifacts"]["workflow/manifest.json"] = {}
            else:
                value["result"]["artifacts"][agent_tasks.ARTIFACTS["types"]]["items"][0]["type"] = "invented"
            before = mf.manifest_path(self.batch).read_bytes()
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                protocol.import_result(self.batch, task_id, self.response(task_id, value))
            self.assertEqual(mf.manifest_path(self.batch).read_bytes(), before)
        self.assertTrue(protocol.import_result(self.batch, task_id, self.response(task_id))["completed"])

    def test_source_and_original_input_changes_are_stale(self):
        for relative in ("01_source/正文.txt", "original.txt", "01_source/structure.confirmed.json"):
            with self.subTest(relative=relative):
                task_id = self.export()["task_id"]
                path = self.batch / relative
                before = path.read_bytes()
                path.write_bytes(before + b" ")
                with self.assertRaisesRegex(ValueError, "stale"):
                    protocol.import_result(self.batch, task_id, self.response(task_id))
                path.write_bytes(before)

    def test_assemble_audio_sources_are_optional_in_context_and_hash_bound(self):
        self.write(agent_tasks.ARTIFACTS['types'], self.payload)
        self.write(agent_tasks.ARTIFACTS['answers'], {'answers': {'1': 'A'}})
        self.write(agent_tasks.ARTIFACTS['explanations'], {'items': []})
        self.write(agent_tasks.ARTIFACTS['knowledge'], {'items': []})
        state = mf.load(self.batch)
        for stage in ('audio', 'types', 'explanations', 'knowledge'):
            mf.touch_stage(state, stage, mf.COMPLETED)
        mf.save(self.batch, state)
        optional = ('02_audio/full-transcript.json', '02_audio/segment-evidence.json')
        initial = agent_tasks.build_task(self.batch, 'assemble')
        self.assertEqual(initial['status'], 'ready')
        self.assertTrue(all(name not in initial['input_hashes'] for name in optional))
        for name in optional:
            self.write(name, {'items': [{'unit_id': 'q-1', 'text': 'Actual AI transcript; pending verification.'}]})
        built = agent_tasks.build_task(self.batch, 'assemble')
        for name in optional:
            self.assertIn(name, built['prompt'])
            self.assertEqual(built['input_hashes'][name], mf.sha256_file(self.batch / name))
        for name in optional:
            exported = protocol.export_task(self.batch, 'assemble')
            task_id = exported['task_id']
            response = self.response(task_id, {'protocol': protocol.PROTOCOL, 'task_id': task_id,
                'result': {'status': 'needs_review', 'artifacts': {}, 'notes': ['Pending review'], 'findings': []}})
            path = self.batch / name
            before = path.read_bytes()
            path.write_bytes(before + b' ')
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'stale'):
                protocol.import_result(self.batch, task_id, response)
            path.write_bytes(before)

    def test_optional_content_inputs_portable_fingerprints_detect_changes(self):
        self.write(agent_tasks.ARTIFACTS['types'], self.payload)
        self.write(agent_tasks.ARTIFACTS['answers'], {'answers': {'1': 'A'}})
        self.write(agent_tasks.ARTIFACTS['knowledge'], {'items': []})
        for stage in ('knowledge', 'assemble'):
            state = mf.load(self.batch)
            for upstream in contracts.upstream_of(stage):
                mf.touch_stage(state, upstream, mf.COMPLETED)
            mf.save(self.batch, state)
            for name in agent_tasks.CONTENT_OPTIONAL_INPUTS:
                path = self.batch / name
                before = path.read_bytes() if path.is_file() else None
                for change in ('appear', 'delete'):
                    with self.subTest(stage=stage, name=name, change=change):
                        if change == 'appear':
                            if path.exists():
                                path.unlink()
                        else:
                            path.parent.mkdir(parents=True, exist_ok=True)
                            path.write_text('{"passed": true}' if name.endswith('.json') else 'evidence')
                        exported = protocol.export_task(self.batch, stage)
                        task_id = exported['task_id']
                        if change == 'appear':
                            path.parent.mkdir(parents=True, exist_ok=True)
                            path.write_text('{"passed": true}' if name.endswith('.json') else 'evidence')
                        else:
                            path.unlink()
                        response = self.response(task_id, {'protocol': protocol.PROTOCOL, 'task_id': task_id,
                            'result': {'status': 'needs_review', 'artifacts': {}, 'notes': ['待核'], 'findings': []}})
                        with self.assertRaisesRegex(ValueError, 'stale'):
                            protocol.import_result(self.batch, task_id, response)
                if before is None:
                    if path.exists():
                        path.unlink()
                else:
                    path.write_bytes(before)

    def test_missing_content_fingerprints_and_legacy_bindings(self):
        self.write(agent_tasks.ARTIFACTS['types'], self.payload)
        self.write(agent_tasks.ARTIFACTS['answers'], {'answers': {'1': 'A'}})
        state = mf.load(self.batch)
        for upstream in contracts.upstream_of('knowledge'):
            mf.touch_stage(state, upstream, mf.COMPLETED)
        mf.save(self.batch, state)
        name = '01_source/metadata.json'
        for legacy, appear in ((False, False), (True, False), (True, True)):
            with self.subTest(legacy=legacy, appear=appear):
                path = self.batch / name
                if path.exists():
                    path.unlink()
                task_id = protocol.export_task(self.batch, 'knowledge')['task_id']
                binding_path = self.batch / 'workflow/agent-bindings' / (task_id + '.json')
                binding = json.loads(binding_path.read_text())
                row = next(row for row in binding['fingerprints'] if row['scope'] == 'batch' and row['path'] == name)
                self.assertEqual(row, {'scope': 'batch', 'path': name, 'exists': False})
                if legacy:
                    binding['fingerprints'] = [r for r in binding['fingerprints'] if r.get('exists') is not False]
                    binding_path.write_text(json.dumps(binding))
                if appear:
                    self.write(name, {})
                response = self.response(task_id, {'protocol': protocol.PROTOCOL, 'task_id': task_id,
                    'result': {'status': 'needs_review', 'artifacts': {}, 'notes': ['待核'], 'findings': []}})
                if appear:
                    with self.assertRaisesRegex(ValueError, 'stale'):
                        protocol.import_result(self.batch, task_id, response)
                else:
                    self.assertFalse(protocol.import_result(self.batch, task_id, response)['completed'])

    def test_content_validation_structure_is_bound_for_knowledge_and_assemble(self):
        self.write(agent_tasks.ARTIFACTS['types'], self.payload)
        self.write(agent_tasks.ARTIFACTS['answers'], {'answers': {'1': 'A'}})
        # 子智能体1的解析产物与子智能体2的知识点产物分开登记。
        self.write(agent_tasks.ARTIFACTS['explanations'], {'items': []})
        self.write(agent_tasks.ARTIFACTS['knowledge'], {'items': []})
        structure = self.batch / '01_source/structure.json'
        for stage in ('knowledge', 'assemble'):
            state = mf.load(self.batch)
            for upstream in contracts.upstream_of(stage):
                mf.touch_stage(state, upstream, mf.COMPLETED)
            mf.save(self.batch, state)
            for change in ('modify', 'appear'):
                with self.subTest(stage=stage, change=change):
                    self.write('01_source/structure.json', {'sections': [], 'groups': []})
                    if change == 'appear':
                        structure.unlink()
                    exported = protocol.export_task(self.batch, stage)
                    task_id = exported['task_id']
                    if change == 'appear':
                        self.assertEqual(exported['status'], 'needs_review')
                        self.assertTrue(any('01_source/structure.json' in p for p in exported['preconditions']))
                    else:
                        self.assertEqual(exported['status'], 'ready')
                        built = agent_tasks.build_task(self.batch, stage)
                        self.assertEqual(built['input_hashes']['01_source/structure.json'], mf.sha256_file(structure))
                    self.write('01_source/structure.json', {'sections': [{'type': '书面表达'}], 'groups': []})
                    response = self.response(task_id, {'protocol': protocol.PROTOCOL, 'task_id': task_id,
                        'result': {'status': 'needs_review', 'artifacts': {}, 'notes': ['待核'], 'findings': []}})
                    with self.assertRaisesRegex(ValueError, 'stale'):
                        protocol.import_result(self.batch, task_id, response)

    def test_rule_change_is_stale(self):
        task_id = self.export()["task_id"]
        original = agent_tasks.build_task
        def altered(*args, **kwargs):
            built = original(*args, **kwargs)
            rule = next(name for name in built["input_hashes"] if str(agent_tasks.ROOT) in name)
            built["input_hashes"][rule] = "0" * 64
            return built
        with patch.object(agent_tasks, "build_task", side_effect=altered):
            with self.assertRaisesRegex(ValueError, "stale"):
                protocol.import_result(self.batch, task_id, self.response(task_id))

    def test_dependency_and_manifest_semantics_changes_are_stale(self):
        original = mf.load(self.batch)
        for change in ("meta", "rules", "dependency_status", "dependency_hash", "input_path", "decision"):
            task_id = self.export()["task_id"]
            manifest = copy.deepcopy(original)
            if change in ("meta", "rules"):
                manifest[change]["new"] = "changed"
            elif change == "dependency_status":
                manifest["stages"]["structure_gate"]["status"] = mf.PENDING
            elif change == "dependency_hash":
                manifest["stages"]["structure_gate"]["output_hashes"]["changed"] = "abc"
            elif change == "input_path":
                other = self.batch / "other.txt"
                other.write_text("original source")
                manifest["inputs"]["source"]["path"] = str(other)
            else:
                manifest["human_decisions"].append({"stage": "structure_gate", "action": "changed"})
            mf.save(self.batch, manifest)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "stale"):
                protocol.import_result(self.batch, task_id, self.response(task_id))
            mf.save(self.batch, copy.deepcopy(original))

    def test_dispatch_bookkeeping_does_not_invalidate_task(self):
        manifest = mf.load(self.batch)
        mf.touch_stage(manifest, "types", mf.RUNNING)
        mf.save(self.batch, manifest)
        task_id = self.export()["task_id"]
        manifest = mf.load(self.batch)
        mf.touch_stage(manifest, "types", mf.NEEDS_REVIEW, exit_code=3, log="dispatch.log", notes="waiting")
        manifest["status"] = "waiting"
        mf.save(self.batch, manifest)
        self.assertTrue(protocol.import_result(self.batch, task_id, self.response(task_id))["completed"])

    def test_canonical_task_tampering_rejected_but_public_copy_is_not_authority(self):
        package = self.root / "public"
        task_id = self.export(output_dir=package)["task_id"]
        (package / "task.json").write_text("public copy can be changed")
        canonical = self.batch / "workflow/agent-tasks" / task_id / "task.json"
        canonical.write_bytes(canonical.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "Authoritative"):
            protocol.import_result(self.batch, task_id, self.response(task_id))

    def test_task_cannot_be_imported_into_another_batch(self):
        task_id = self.export()["task_id"]
        other = self.root / "other batch"
        other.mkdir()
        with self.assertRaises(FileNotFoundError):
            protocol.import_result(other, task_id, self.response(task_id))

    def test_two_pending_tasks_cannot_overwrite_first_import(self):
        first = self.export()["task_id"]
        second = self.export()["task_id"]
        protocol.import_result(self.batch, first, self.response(first))
        with self.assertRaisesRegex(ValueError, "already complete"):
            protocol.import_result(self.batch, second, self.response(second))

    def test_preconditions_prevent_gate_bypass_even_with_files_present(self):
        manifest = mf.load(self.batch)
        manifest["stages"]["structure_gate"]["status"] = mf.PENDING
        mf.save(self.batch, manifest)
        report = self.export()
        self.assertEqual(report["status"], "needs_review")
        with self.assertRaisesRegex(ValueError, "preconditions"):
            protocol.import_result(self.batch, report["task_id"], self.response(report["task_id"]))
        self.assertFalse((self.batch / agent_tasks.ARTIFACTS["types"]).exists())

    def test_completed_stage_requires_explicit_invalidation(self):
        manifest = mf.load(self.batch)
        mf.touch_stage(manifest, "types", mf.COMPLETED)
        mf.save(self.batch, manifest)
        report = self.export()
        self.assertEqual(report["status"], "needs_review")
        with self.assertRaisesRegex(ValueError, "already complete"):
            protocol.import_result(self.batch, report["task_id"], self.response(report["task_id"]))

    def test_import_invalidates_downstream_but_preserves_human_history(self):
        manifest = mf.load(self.batch)
        mf.touch_stage(manifest, "knowledge", mf.COMPLETED)
        mf.touch_stage(manifest, "upload_gate", mf.APPROVED)
        mf.record_decision(manifest, "upload_gate", "approve", "human")
        mf.save(self.batch, manifest)
        task_id = self.export()["task_id"]
        protocol.import_result(self.batch, task_id, self.response(task_id))
        updated = mf.load(self.batch)
        self.assertEqual(updated["stages"]["knowledge"]["status"], mf.INVALIDATED)
        self.assertEqual(updated["stages"]["upload_gate"]["status"], mf.INVALIDATED)
        self.assertEqual(updated["human_decisions"], manifest["human_decisions"])

    def test_external_and_relative_input_fingerprints_are_portable(self):
        external = self.root / "external input.txt"
        external.write_text("external source")
        manifest = mf.load(self.batch)
        manifest["inputs"]["source"] = {"path": str(external), "exists": True, "sha256": mf.sha256_file(external)}
        manifest["inputs"]["relative"] = {"path": "original.txt", "exists": True}
        mf.save(self.batch, manifest)
        task_id = self.export()["task_id"]
        binding_path = self.batch / "workflow/agent-bindings" / (task_id + ".json")
        binding = json.loads(binding_path.read_text())
        self.assertNotIn(str(self.root), binding_path.read_text())
        self.assertTrue(any(row["scope"] == "input" and row["path"] == "source" for row in binding["fingerprints"]))
        external.write_text("changed external source")
        with self.assertRaisesRegex(ValueError, "stale"):
            protocol.import_result(self.batch, task_id, self.response(task_id))

    def test_output_directory_must_be_empty_and_not_symlink(self):
        occupied = self.root / "occupied"
        occupied.mkdir()
        (occupied / "keep.txt").write_text("preserve")
        with self.assertRaisesRegex(ValueError, "new or empty"):
            self.export(output_dir=occupied)
        link = self.root / "linked"
        link.symlink_to(occupied, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.export(output_dir=link / "new")
        self.assertEqual((occupied / "keep.txt").read_text(), "preserve")

    def test_unknown_external_builder_hash_rejected(self):
        original = agent_tasks.build_task
        def altered(*args, **kwargs):
            built = original(*args, **kwargs)
            built["input_hashes"][str(self.root / "unregistered.txt")] = "0" * 64
            return built
        with patch.object(agent_tasks, "build_task", side_effect=altered):
            with self.assertRaisesRegex(ValueError, "unregistered"):
                self.export()

    def test_cli_dispatch_exports_without_manifest_writes(self):
        before = mf.manifest_path(self.batch).read_bytes()
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = protocol.main(["dispatch", "--batch-dir", str(self.batch), "--stage", "types"])
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(stream.getvalue())["action"], "exported")
        self.assertEqual(mf.manifest_path(self.batch).read_bytes(), before)

    def test_cli_next_reports_deterministic_human_or_ready_work(self):
        manifest = mf.load(self.batch)
        for spec in contracts.STAGES:
            manifest["stages"][spec.id]["status"] = mf.PENDING
        mf.save(self.batch, manifest)
        self.assertEqual(protocol.next_task(self.batch), {"action": "run_workflow", "stage": "extract"})
        for spec in contracts.STAGES:
            if spec.id == "types":
                break
            manifest["stages"][spec.id]["status"] = mf.COMPLETED
        mf.save(self.batch, manifest)
        self.assertEqual(protocol.next_task(self.batch)["action"], "exported")
        for spec in contracts.STAGES:
            if spec.id == "upload_gate":
                break
            manifest["stages"][spec.id]["status"] = mf.COMPLETED
        mf.save(self.batch, manifest)
        self.assertEqual(protocol.next_task(self.batch), {"action": "human", "stage": "upload_gate"})

    def test_cli_errors_return_two_and_review_returns_three(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(protocol.main(["export", "--batch-dir", str(self.batch), "--stage", "upload"]), 2)
        task_id = self.export()["task_id"]
        value = self.envelope(task_id, "needs_review")
        response = self.response(task_id, value)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(protocol.main(["import", "--batch-dir", str(self.batch), "--task-id", task_id,
                                            "--result", str(response)]), 3)


if __name__ == "__main__":
    unittest.main()
