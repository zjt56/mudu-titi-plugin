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
        # These file-protocol tests never acquire the machine-wide agent lock,
        # including the lock taken inside review_rounds for review export/import.
        from workflow import local_agent, review_rounds
        for owner in (protocol, review_rounds):
            lock = patch.object(owner, "_agent_lock", side_effect=lambda *_a, **_k: nullcontext())
            lock.start()
            self.addCleanup(lock.stop)
        fallback = patch.object(local_agent, "_agent_lock", side_effect=lambda *_a, **_k: nullcontext())
        fallback.start()
        self.addCleanup(fallback.stop)
        self.tmp = tempfile.TemporaryDirectory(prefix="agent protocol ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.batch = self.root / "original batch"
        self.batch.mkdir()
        self.write("01_source/正文.txt", "1. She ______ a book. A. reads B. read C. reading D. to read")
        self.write("01_source/structure.json", {"sections": [{"type": "单项填空", "questions": [1]}], "groups": []})
        self.write("01_source/extraction.json", {})
        self.write("01_source/metadata.json", {})
        self.write("07_review/extraction-report.json", {})
        self.write("01_source/structure.confirmed.json", {"units": [
            {"unit_kind": "standalone", "numbers": [1], "type": "单项填空"}]})
        self.write("07_review/structure-gate.json", {"passed": True})
        self.write("01_source/附录.txt", "1. A")
        source = self.batch / "original.txt"
        source.write_text("original source", encoding="utf-8")
        self.write("01_source/metadata.json", {
            "original_sha256": mf.sha256_file(source),
            "source_sha256": mf.sha256_file(self.batch / "01_source/正文.txt"),
        })
        gate_report = {
            "passed": True,
            "input_hashes": {
                "01_source/正文.txt": mf.sha256_file(self.batch / "01_source/正文.txt"),
                "01_source/structure.json": mf.sha256_file(self.batch / "01_source/structure.json"),
                "01_source/structure.confirmed.json": mf.sha256_file(self.batch / "01_source/structure.confirmed.json"),
            },
            "original_sha256": mf.sha256_file(source),
        }
        self.write("07_review/structure-gate.json", gate_report)
        manifest = mf.new_manifest("test-protocol", self.batch,
                                   mf.register_input(self.batch, "source", source), {"version": "test"})
        mf.touch_stage(manifest, "extract", mf.COMPLETED)
        mf.touch_stage(manifest, "structure_gate", mf.COMPLETED,
                       output_hashes=mf.output_hashes(self.batch, contracts.STAGE_BY_ID["structure_gate"]))
        self.write("06_batch/batch.base.json", {"items": [{
            "unit_id": "q-1", "unit_kind": "standalone", "number": 1, "numbers": [1],
            "stem": "She ______ a book.", "material": "", "options": ["A. reads", "B. read", "C. reading", "D. to read"],
            "subquestions": []}], "name": "test"})
        types = {"items": [{"unit_id": "q-1", "type": "单项填空", "subtypes": ["四个选项"],
                             "words": 5, "contentComplete": True, "evidence": ["正文 L1"]}]}
        self.write(agent_tasks.ARTIFACTS["types"], types)
        answers = {"answers": {"1": "A"}, "evidence": {"1": ["附录 L1"]}, "missing": {}}
        self.write(agent_tasks.ARTIFACTS["answers"], answers)
        mf.touch_stage(manifest, "preassemble", mf.COMPLETED,
                       output_hashes=mf.output_hashes(self.batch, contracts.STAGE_BY_ID["preassemble"]))
        mf.save(self.batch, manifest)
        self.payload = {"items": [{"unit_id": "q-1", "type": "单项填空", "subtypes": ["四个选项"],
                                   "words": 5, "contentComplete": True, "evidence": ["正文 L1 A–D"]}]}

    def mark_delivered(self, stage):
        manifest = mf.load(self.batch)
        spec = contracts.STAGE_BY_ID[stage]
        mf.touch_stage(manifest, stage, mf.COMPLETED,
                       input_hashes=mf.input_hashes(self.batch, manifest, spec),
                       output_hashes=mf.output_hashes(self.batch, spec))
        mf.record_delivery(self.batch, manifest, spec)
        mf.save(self.batch, manifest)

    def mark_chain(self, stages, **overrides):
        """Mark a chain as really delivered, with recorded input bindings."""
        for stage_id in stages:
            spec = contracts.STAGE_BY_ID[stage_id]
            manifest = mf.load(self.batch)
            touch = overrides.get(stage_id, {})
            status = touch.pop('status', mf.COMPLETED) if isinstance(touch, dict) else mf.COMPLETED
            mf.touch_stage(manifest, stage_id, status,
                           input_hashes=mf.input_hashes(self.batch, manifest, spec),
                           output_hashes=mf.output_hashes(self.batch, spec),
                           **touch)
            mf.save(self.batch, manifest)
            if status in mf.TERMINAL_OK:
                mf.record_delivery(self.batch, manifest, spec)
                mf.save(self.batch, manifest)

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
                                               "timeout_seconds": 30, "max_input_chars": 200000}, self.root)
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

    @patch.dict(os.environ, {"WF_MODEL_FIRST": "0"})
    def test_needs_review_consumes_task_without_importing_artifact(self):
        task_id = self.export()["task_id"]
        value = self.envelope(task_id, "needs_review")
        value["result"]["notes"] = ["Need clearer source"]
        response = self.response(task_id, value)
        types_before = (self.batch / agent_tasks.ARTIFACTS["types"]).read_bytes()
        report = protocol.import_result(self.batch, task_id, response)
        self.assertFalse(report["completed"])
        self.assertEqual((self.batch / agent_tasks.ARTIFACTS["types"]).read_bytes(), types_before)
        self.assertEqual(mf.load(self.batch)["stages"]["types"]["status"], mf.NEEDS_REVIEW)
        self.assertEqual(protocol.import_result(self.batch, task_id, response), report)
        with self.assertRaisesRegex(ValueError, "consumed"):
            protocol.import_result(self.batch, task_id, self.response(task_id))

    def prepare_review(self):
        self.write(agent_tasks.ARTIFACTS["types"], self.payload)
        self.write(agent_tasks.ARTIFACTS["answers"], {"answers": {"1": "A"}, "evidence": {"1": ["附录 L1"]}, "missing": {}})
        explanation = "无"
        self.write(agent_tasks.ARTIFACTS["explanations"], {"items": [{
            "unit_id": "q-1", "explanation": explanation, "articleSummary": "无",
            "subquestions": [], "evidence": ["未提供原解析"]}]})
        from workbench import taxonomy
        knowledge = ["词汇/词汇辨析/动词辨析"]
        ids = [row["id"] for row in taxonomy.resolve_knowledge(knowledge)["resolved"]]
        self.write(agent_tasks.ARTIFACTS["knowledge"], {"items": [{
            "unit_id": "q-1", "knowledge": knowledge, "knowledgeIds": ids,
            "subquestions": [], "evidence": ["正文 L1"]}]})
        batch = {"name": "test", "items": [{
            "id": "q-1", "unit_id": "q-1", "unit_kind": "standalone", "number": 1, "numbers": [1],
            "question_type": "单项填空", "filterMetadata": {"subtypes": ["四个选项"]},
            "stem": "1. She ______ a book. A. reads B. read C. reading D. to read", "material": "",
            "options": ["A. reads", "B. read", "C. reading", "D. to read"],
            "knowledge": knowledge, "knowledgeIds": ids, "knowledge_ids": ids,
            "answer": "A", "explanation": "无", "articleSummary": "无", "subquestions": []}]}
        self.write("06_batch/batch.json", batch)
        self.write("06_batch/batch_meta.base.json", batch)
        self.write("06_batch/batch_meta.json", batch)
        self.write("07_review/validation-report.json", {
            "passed": True, "batch_sha256": mf.sha256_file(self.batch / "06_batch/batch_meta.json")})
        for stage in contracts.upstream_of("review"):
            if stage == "audio":
                self.mark_chain([stage], **{stage: {'status': mf.SKIPPED, 'notes': '未提供音频'}})
            else:
                self.mark_delivered(stage)

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

    @patch.dict(os.environ, {"WF_MODEL_FIRST": "0"})
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

    def test_assemble_does_not_bind_full_transcript_or_segments(self):
        # 组装只搬运四路产物，合法性由导入器程序校验；完整听力转写与切片证据
        # 只在语义审核阶段需要，不再进入组装上下文，也不再让组装任务因它们变化而失效。
        self.write(agent_tasks.ARTIFACTS['types'], self.payload)
        self.write(agent_tasks.ARTIFACTS['answers'], {'answers': {'1': 'A'}, 'evidence': {'1': ['附录 L1']}, 'missing': {}})
        self.write(agent_tasks.ARTIFACTS['explanations'], {'items': [{
            'unit_id': 'q-1', 'explanation': '无', 'articleSummary': '无',
            'subquestions': [], 'evidence': ['原卷无解析']}]})
        self.write(agent_tasks.ARTIFACTS['knowledge'], {'items': [{
            'unit_id': 'q-1', 'knowledge': ['词汇/词汇辨析/动词辨析'],
            'subquestions': [], 'evidence': ['正文 L1']}]})
        self.write("02_audio/SKIP_LISTENING", "no original audio")
        for stage in contracts.STAGE_BY_ID['assemble'].depends_on:
            if stage == "audio":
                self.mark_chain([stage], **{stage: {'status': mf.SKIPPED, 'notes': '未提供音频'}})
            else:
                self.mark_delivered(stage)
        optional = ('02_audio/full-transcript.json', '02_audio/segment-evidence.json')
        for name in optional:
            self.write(name, {'items': [{'unit_id': 'q-1', 'text': 'Actual AI transcript; pending verification.'}]})
        built = agent_tasks.build_task(self.batch, 'assemble')
        self.assertEqual(built['status'], 'ready')
        for name in optional:
            self.assertNotIn(name, built['input_hashes'])
            self.assertNotIn(name, built['prompt'])
        # 这些文件变化不应让组装任务失效为 stale。
        for name in optional:
            exported = protocol.export_task(self.batch, 'assemble')
            task_id = exported['task_id']
            response = self.response(task_id, {'protocol': protocol.PROTOCOL, 'task_id': task_id,
                'result': {'status': 'needs_review', 'artifacts': {}, 'notes': ['Pending review'], 'findings': []}})
            path = self.batch / name
            before = path.read_bytes()
            path.write_bytes(before + b' ')
            try:
                protocol.import_result(self.batch, task_id, response)
            finally:
                path.write_bytes(before)

    def test_review_still_binds_full_transcript_and_segments(self):
        # 一审/二审需要完整听力转写与切片证据做语义核对，仍必须进入审核上下文。
        self.write(agent_tasks.ARTIFACTS['types'], self.payload)
        self.write(agent_tasks.ARTIFACTS['answers'], {'answers': {'1': 'A'}, 'evidence': {'1': ['附录 L1']}, 'missing': {}})
        self.write(agent_tasks.ARTIFACTS['knowledge'], {'items': []})
        self.write("02_audio/SKIP_LISTENING", "test isolation")
        optional = ('02_audio/full-transcript.json', '02_audio/segment-evidence.json')
        for name in optional:
            self.write(name, {'items': [{'unit_id': 'q-1', 'text': 'transcript', 'verified': True}]})
        contexts, hashes, missing, units = agent_tasks._collect(self.batch, 'review')
        for name in optional:
            self.assertIn(name, hashes)
            self.assertIn(name, contexts)

    def test_optional_content_inputs_portable_fingerprints_detect_changes(self):
        """A genuinely optional input appearing/disappearing makes an exported task stale."""
        self.write(agent_tasks.ARTIFACTS['types'], self.payload)
        self.write(agent_tasks.ARTIFACTS['answers'], {'answers': {'1': 'A'}, 'evidence': {'1': ['附录 L1']}, 'missing': {}})
        self.write(agent_tasks.ARTIFACTS['explanations'], {'items': [{
            'unit_id': 'q-1', 'explanation': '无', 'articleSummary': '无',
            'subquestions': [], 'evidence': ['未提供原解析']}]})
        self.write(agent_tasks.ARTIFACTS['knowledge'], {'items': [{
            'unit_id': 'q-1', 'knowledge': ['词汇/词汇辨析/动词辨析'],
            'subquestions': [], 'evidence': ['正文 L1']}]})
        for upstream in contracts.upstream_of('knowledge'):
            self.mark_delivered(upstream)
        mf.touch_stage(mf.load(self.batch), 'knowledge', mf.PENDING)
        state = mf.load(self.batch)
        mf.touch_stage(state, 'knowledge', mf.PENDING)
        mf.save(self.batch, state)
        name = 'workflow/repair-feedback/knowledge.json'
        path = self.batch / name
        feedback = json.dumps({'version': 1, 'stage': 'knowledge', 'unit_ids': [],
                               'feedback_by_unit': {}, 'all_units': True, 'attempt': 1,
                               'reason': 'AI诊断', 'error': '实际错误摘要'}, ensure_ascii=False)
        for change in ('appear', 'delete'):
            with self.subTest(name=name, change=change):
                reset = mf.load(self.batch)
                mf.touch_stage(reset, 'knowledge', mf.PENDING)
                mf.save(self.batch, reset)
                Path(path.parent).mkdir(parents=True, exist_ok=True)
                if change == 'appear':
                    if path.exists():
                        path.unlink()
                else:
                    path.write_text(feedback, encoding='utf-8')
                task_id = protocol.export_task(self.batch, 'knowledge')['task_id']
                if change == 'appear':
                    path.write_text(feedback, encoding='utf-8')
                else:
                    path.unlink()
                response = self.response(task_id, {'protocol': protocol.PROTOCOL, 'task_id': task_id,
                    'result': {'status': 'needs_review', 'artifacts': {}, 'notes': ['待核'], 'findings': []}})
                with self.assertRaisesRegex(ValueError, 'stale'):
                    protocol.import_result(self.batch, task_id, response)

    def test_missing_content_fingerprints_and_legacy_bindings(self):
        self.write(agent_tasks.ARTIFACTS['types'], self.payload)
        self.write(agent_tasks.ARTIFACTS['answers'], {'answers': {'1': 'A'}, 'evidence': {'1': ['附录 L1']}, 'missing': {}})
        self.write(agent_tasks.ARTIFACTS['explanations'], {"items": [{
            "unit_id": "q-1", "explanation": "无", "articleSummary": "无",
            "subquestions": [], "evidence": ["未提供原解析"]}]})
        self.write(agent_tasks.ARTIFACTS['knowledge'], {"items": [{
            "unit_id": "q-1", "knowledge": ["词汇/词汇辨析/动词辨析"],
            "subquestions": [], "evidence": ["正文 L1"]}]})
        self.mark_delivered('types')
        self.mark_delivered('answers')
        state = mf.load(self.batch)
        mf.touch_stage(state, 'knowledge', mf.PENDING)
        mf.save(self.batch, state)
        # repair feedback is a real optional binding; metadata/附录 are both
        # required extract-or-answer products now and cannot be toggled here.
        name = 'workflow/repair-feedback/knowledge.json'
        for legacy, appear in ((False, False), (True, False), (True, True)):
            with self.subTest(legacy=legacy, appear=appear):
                reset = mf.load(self.batch)
                mf.touch_stage(reset, 'knowledge', mf.PENDING)
                mf.save(self.batch, reset)
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
                    self.write(name, {'version': 1, 'stage': 'knowledge', 'unit_ids': [],
                                      'feedback_by_unit': {}, 'all_units': True, 'attempt': 1,
                                      'reason': 'AI诊断', 'error': '实际错误摘要'})
                response = self.response(task_id, {'protocol': protocol.PROTOCOL, 'task_id': task_id,
                    'result': {'status': 'needs_review', 'artifacts': {}, 'notes': ['待核'], 'findings': []}})
                if appear:
                    with self.assertRaisesRegex(ValueError, 'stale'):
                        protocol.import_result(self.batch, task_id, response)
                else:
                    self.assertFalse(protocol.import_result(self.batch, task_id, response)['completed'])

    def test_content_validation_structure_is_bound_for_knowledge_and_assemble(self):
        self.write(agent_tasks.ARTIFACTS['types'], self.payload)
        self.write(agent_tasks.ARTIFACTS['answers'], {'answers': {'1': 'A'}, 'evidence': {'1': ['附录 L1']}, 'missing': {}})
        self.write(agent_tasks.ARTIFACTS['explanations'], {'items': [{
            'unit_id': 'q-1', 'explanation': '无', 'articleSummary': '无',
            'subquestions': [], 'evidence': ['未提供原解析']}]})
        self.write(agent_tasks.ARTIFACTS['knowledge'], {'items': [{
            'unit_id': 'q-1', 'knowledge': ['词汇/词汇辨析/动词辨析'],
            'subquestions': [], 'evidence': ['正文 L1']}]})
        structure = self.batch / '01_source/structure.json'
        for stage in ('knowledge', 'assemble'):
            for upstream in contracts.upstream_of(stage):
                if upstream == "audio":
                    self.mark_chain([upstream], **{upstream: {'status': mf.SKIPPED, 'notes': '未提供音频'}})
                else:
                    self.mark_delivered(upstream)
            for change in ('modify', 'appear'):
                with self.subTest(stage=stage, change=change):
                    self.write('01_source/structure.json', {'sections': [], 'groups': []})
                    if change == 'appear':
                        structure.unlink()
                    exported = protocol.export_task(self.batch, stage)
                    # A previously completed upstream gate owns this structure
                    # binding. A changed/missing source structure blocks export;
                    # it is not sent downstream to fail during import.
                    self.assertEqual(exported["action"], "blocked")
                    self.assertIn("structure_gate", " ".join(exported.get("preconditions", [])))
                    self.write('01_source/structure.json', {'sections': [{'type': '单项填空', 'questions': [1]}], 'groups': []})

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
        self.assertEqual(report["action"], "blocked")
        self.assertTrue(any("structure_gate" in reason for reason in report["preconditions"]))
        self.assertTrue((self.batch / agent_tasks.ARTIFACTS["types"]).is_file())

    def test_completed_stage_requires_explicit_invalidation(self):
        manifest = mf.load(self.batch)
        mf.touch_stage(manifest, "types", mf.COMPLETED)
        mf.save(self.batch, manifest)
        report = self.export()
        self.assertEqual(report["action"], "blocked")
        self.assertTrue(any("already complete" in reason for reason in report["preconditions"]))

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
        report = self.export()
        self.assertEqual(report["action"], "blocked")
        self.assertTrue(any("extract" in reason.lower() or "source" in reason.lower()
                            or "original" in reason.lower()
                            for reason in report["preconditions"]))

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
        result = protocol.next_task(self.batch)
        self.assertEqual(result["action"], "blocked")
        self.assertEqual(result["stage"], "upload_gate")
        self.assertTrue(any("format_gate" in reason for reason in result["preconditions"]))

    @patch.dict(os.environ, {"WF_MODEL_FIRST": "0"})
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
