"""通用入口默认不依赖任何厂商 CLI，仍兼容本机配置。"""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import agent, agent_protocol, agent_tasks, auto, contracts, ingest, local_agent, manifest as mf, review, review_rounds


class AgentEntryTests(unittest.TestCase):
    def test_unconfigured_run_exports_instead_of_loading_cli(self):
        with patch.object(agent, 'configured', return_value=False), \
             patch.object(agent_protocol, 'main', return_value=3) as dispatch, \
             patch.object(local_agent, 'main') as local:
            self.assertEqual(agent.main(['run', '--stage', 'types', '--batch-dir', '/tmp/a batch']), 3)
        self.assertEqual(dispatch.call_args.args[0], ['dispatch', '--stage', 'types', '--batch-dir', '/tmp/a batch'])
        local.assert_not_called()

    def test_configured_run_preserves_optional_automatic_transport(self):
        with patch.object(agent, 'configured', return_value=True), \
             patch.object(local_agent, 'main', return_value=0) as local, \
             patch.object(agent_protocol, 'main') as handoff:
            self.assertEqual(agent.main(['run', '--stage', 'types', '--batch-dir', 'batch']), 0)
        local.assert_called_once_with(['--stage', 'types', '--batch-dir', 'batch'])
        handoff.assert_not_called()

    def test_explicit_config_and_dry_run_are_forwarded(self):
        with patch.object(local_agent, 'main', return_value=0) as local:
            agent.main(['run', '--stage', 'types', '--batch-dir', 'batch', '--config', 'my.json', '--dry-run'])
        self.assertEqual(local.call_args.args[0], ['--stage', 'types', '--batch-dir', 'batch', '--config', 'my.json', '--dry-run'])

    def test_file_protocol_is_available_even_with_cli_configuration(self):
        with patch.object(agent_protocol, 'main', return_value=0) as handoff, \
             patch.object(agent, 'configured', side_effect=AssertionError('must not inspect config')):
            self.assertEqual(agent.main(['next', '--batch-dir', 'batch']), 0)
        handoff.assert_called_once_with(['next', '--batch-dir', 'batch'])

    def test_protocol_missing_batch_value_reports_argument_error(self):
        for action in ('next', 'export', 'import'):
            with self.subTest(action=action), contextlib.redirect_stderr(io.StringIO()) as errors:
                with self.assertRaises(SystemExit):
                    agent.main([action, '--batch-dir'])
                self.assertNotIn('Traceback', errors.getvalue())
                self.assertIn('expected one argument', errors.getvalue())

    def test_unconfigured_dry_run_only_exports(self):
        with patch.object(agent, 'configured', return_value=False), \
             patch.object(agent_protocol, 'main', return_value=0) as handoff:
            self.assertEqual(agent.main(['run', '--stage', 'types', '--batch-dir', 'batch', '--dry-run']), 0)
        self.assertEqual(handoff.call_args.args[0][0], 'export')

    def test_review_run_ignores_legacy_state_and_never_invokes_precheck(self):
        batch = self._review_batch()
        old_state = batch / 'workflow/review-ai-state.json'
        old_state.parent.mkdir(parents=True)
        old_state.write_text('{"report":{"completed":true}}', encoding='utf-8')
        output = io.StringIO()
        with patch.object(agent, 'configured', return_value=True), \
             patch.object(agent, '_review_inputs_ready', return_value=True), \
             patch.object(local_agent, 'main') as precheck, \
             patch.object(review_rounds, 'run', return_value={'action': 'blocked', 'completed': False}), \
             contextlib.redirect_stdout(output):
            self.assertEqual(agent.main(['run', '--stage', 'review', '--batch-dir', str(batch)]), 3)
        precheck.assert_not_called()
        report = json.loads(output.getvalue())
        self.assertFalse(report['completed'])
        self.assertFalse(report['execution_completed'])
        self.assertFalse(report['quality_passed'])
        self.assertEqual(json.loads(old_state.read_text()), {'report': {'completed': True}})
        self.assertFalse((batch / 'workflow/review-ai-binding.json').exists())

    def _review_batch(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        batch = Path(temp.name).resolve()
        files = {
            "01_source/正文.txt": "1. She reads a book. A. reads B. read",
            "01_source/structure.confirmed.json": {"units": [{"unit_id": "q-1", "unit_kind": "standalone", "numbers": [1], "stem": "1. She reads a book. A. reads B. read"}]},
            "03_types/types.json": {"items": [{"unit_id": "q-1", "type": "单项填空", "subtypes": ["四个选项"]}]},
            "01_source_ans/answers.json": {"answers": {"1": "A"}, "difficulty": {"1": "容易"}},
            "04_knowledge/explanations.json": {"items": [{
                "unit_id": "q-1", "explanation": "主语是第三人称单数，谓语用 reads。（AI生成，待校对）",
                "articleSummary": "", "evidence": ["正文第1题；答案第1题"], "subquestions": []}]},
            "04_knowledge/knowledge.json": {"items": [{
                "unit_id": "q-1", "knowledge": ["词汇/词汇辨析/动词辨析"], "answer": "A",
                "explanation": "主语是第三人称单数，谓语用 reads。（AI生成，待校对）",
                "articleSummary": "", "evidence": ["正文第1题"], "subquestions": []}]},
            "06_batch/batch.json": {"items": []},
        }
        for rel, value in files.items():
            path = batch / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return batch

    def _partial_ai(self, batch):
        finding = {"unit_id": "q-1", "severity": "major", "field": "answer",
                   "reason": "Source answer requires further evidence", "evidence": ["正文 第1题"]}
        candidate = {"status": "needs_review", "notes": ["第1题音图证据未核验"], "findings": [],
                     "artifacts": {agent_tasks.ARTIFACTS["review"]: {
                         "findings": [finding], "checked_units": ["q-1"],
                         "unchecked": [{"unit_id": "q-1", "field": "media", "reason": "未核验图片"}]}}}
        attempt = batch / "workflow/agent-runs/review-fixture"
        attempt.mkdir(parents=True)
        (attempt / "response.json").write_text(json.dumps(candidate), encoding="utf-8")
        return {"attempt_dir": str(attempt), "status": "needs_review",
                "report": agent_tasks.accept_result(batch, "review", candidate)}

    def test_completed_ai_and_rounds_do_not_confuse_quality_with_execution(self):
        batch = self._review_batch()
        manifest = mf.new_manifest("review", batch, {}, {})
        mf.save(batch, manifest)
        def completed():
            print(json.dumps({"report": {"completed": True}}))
            return 0
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(review_rounds, "run", return_value={
                "action": "complete", "completed": True, "quality_passed": False,
                "summary": {"report_json": "07_review/overall-review-report.json"}}):
            self.assertEqual(agent._review_after(completed, batch), 0)
        report = json.loads(output.getvalue())
        self.assertTrue(report["completed"])
        self.assertTrue(report["execution_completed"])
        self.assertFalse(report["quality_passed"])
        self.assertEqual(report["summary"]["report_json"], "07_review/overall-review-report.json")
        self.assertEqual(mf.load(batch)["stages"]["review"]["status"], mf.COMPLETED)

    @patch.dict(os.environ, {"WF_MODEL_FIRST": "0"})
    def test_partial_ai_continues_rounds_without_installing_completed_ai(self):
        batch = self._review_batch()
        first = self._partial_ai(batch)
        def partial():
            print(json.dumps(first))
            return 3
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(review_rounds, "run", return_value={
                "action": "complete", "completed": True, "quality_passed": True}) as rounds:
            self.assertEqual(agent._review_after(partial, batch), 3)
        rounds.assert_called_once()
        report = json.loads(output.getvalue())
        self.assertTrue(report["execution_completed"])
        self.assertFalse(report["completed"])
        self.assertFalse(report["ai_review_completed"])
        self.assertFalse(report["quality_passed"])
        self.assertEqual(report["summary"]["open_manual_items"], 1)
        self.assertFalse((batch / agent_tasks.ARTIFACTS["review"]).exists())
        unchecked = json.loads((batch / "07_review/unchecked.json").read_text())
        self.assertTrue(any(row["field"] == "review_ai" and row["required"] for row in unchecked))
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(review_rounds, "run", return_value={"action": "complete", "completed": True}):
            self.assertEqual(agent._resume_review(batch), 3)
        self.assertFalse(json.loads(output.getvalue())["ai_review_completed"])
        items = json.loads((batch / "07_review/manual-review-queue.json").read_text())["items"]
        self.assertEqual(len(items), 1)

    def test_notes_only_partial_ai_does_not_block_independent_reviews(self):
        batch = self._review_batch()
        attempt = batch / 'workflow/agent-runs/notes-only'
        attempt.mkdir(parents=True)
        candidate = {'status': 'needs_review', 'artifacts': {}, 'notes': ['初审未能给出完整结论，继续独立分轮审核'], 'findings': []}
        (attempt / 'response.json').write_text(json.dumps(candidate), encoding='utf-8')
        first = {'attempt_dir': str(attempt), 'report': {'completed': False}}
        def partial():
            print(json.dumps(first))
            return 3
        with contextlib.redirect_stdout(io.StringIO()), patch.object(review_rounds, 'run', return_value={'action': 'complete', 'completed': True}) as rounds:
            self.assertEqual(agent._review_after(partial, batch), 3)
        rounds.assert_called_once()
        self.assertTrue(any(x['field'] == 'review_ai' for x in json.loads((batch / '07_review/unchecked.json').read_text())))

    def test_partial_ai_executes_all_three_real_round_invocations(self):
        batch = self._review_batch()
        first = self._partial_ai(batch)
        seen = []
        def partial():
            print(json.dumps(first))
            return 3
        def invoke(prompt, run_dir, config):
            round_id = review.ROUND_IDS[len(seen)]
            self.assertIn("ROUND: " + round_id, prompt)
            seen.append(run_dir)
            assessment = {"type": "单项填空", "subtypes": ["四个选项"],
                          "knowledge": ["词汇/词汇辨析/动词辨析"], "subquestions": [],
                          "difficulty": [{"number": "1", "value": "容易"}]}
            if round_id == "sample":
                assessment["source_comparison"] = "Compared source words, options, answer and current labels."
            check = {"unit_id": "q-1", "verdict": "ok", "rationale": "Source sentence matches current annotation.",
                     "assessment": assessment, "evidence": [{"path": "01_source/正文.txt", "locator": "第1题", "quote": "She reads a book."}]}
            if round_id == "sample":
                check["number"] = "1"
            return {"response": {"status": "completed", "artifacts": {"review_round": {"checks": [check]}}, "notes": [], "findings": []}}
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(local_agent, "load_config", return_value={"provider": "fixture", "max_input_chars": 600000}), \
             patch.object(review_rounds, "invoke", side_effect=invoke):
            self.assertEqual(agent._review_after(partial, batch, Path("fixture-config")), 3)
        self.assertEqual(len(set(seen)), 2)
        report = json.loads(output.getvalue())
        self.assertTrue(report["execution_completed"])
        self.assertFalse(report["ai_review_completed"])
        self.assertFalse(report["quality_passed"])
        self.assertEqual(report["summary"]["open_manual_items"], 1)
        self.assertEqual(review.evaluate_rounds(batch)["checked_rounds"], list(review.ROUND_IDS))

    @patch.dict(os.environ, {"WF_MODEL_FIRST": "0"})
    def test_partial_protocol_ai_continues_without_changing_receipt(self):
        batch = self._review_batch()
        local = self._partial_ai(batch)
        candidate = json.loads((Path(local["attempt_dir"]) / "response.json").read_text())
        response = batch / "workflow/agent-bindings/review-fixture.response.json"
        response.parent.mkdir(parents=True)
        response.write_text(json.dumps({"result": candidate}), encoding="utf-8")
        first = {"task_id": "review-fixture", "stage": "review", **local["report"]}
        def partial():
            print(json.dumps(first))
            return 3
        with contextlib.redirect_stdout(io.StringIO()), patch.object(review_rounds, "run", return_value={"action": "complete", "completed": True}) as rounds:
            self.assertEqual(agent._review_after(partial, batch), 3)
        rounds.assert_called_once()
        self.assertFalse(first["completed"])
        self.assertFalse((batch / agent_tasks.ARTIFACTS["review"]).exists())

    def test_partial_ai_with_missing_safe_inputs_does_not_start_rounds(self):
        batch = self._review_batch()
        first = self._partial_ai(batch)
        source = batch / "01_source/正文.txt"
        source.write_text("", encoding="utf-8")
        def partial():
            print(json.dumps(first))
            return 3
        with contextlib.redirect_stdout(io.StringIO()), patch.object(review_rounds, "run") as rounds:
            self.assertEqual(agent._review_after(partial, batch), 3)
        rounds.assert_not_called()
        self.assertFalse((batch / "workflow/review-ai-binding.json").exists())

    def test_needs_review_without_candidate_records_failure_and_continues(self):
        batch = self._review_batch()
        calls = []
        def partial():
            calls.append(True)
            print(json.dumps({"report": {"completed": False, "notes": ["No usable review output"]}}))
            return 3
        with contextlib.redirect_stdout(io.StringIO()), patch.object(review_rounds, "run", return_value={"action": "complete", "completed": True}) as rounds:
            self.assertEqual(agent._review_after(partial, batch), 3)
        rounds.assert_called_once()
        self.assertEqual(len(calls), 3)
        self.assertFalse((batch / "workflow/review-ai-candidate.json").exists())
        unchecked = json.loads((batch / "07_review/unchecked.json").read_text())
        self.assertTrue(any(row.get("code") == "AI_EXECUTION_FAILED" and row["required"] for row in unchecked))

    def test_ai_exit_two_retries_a_new_call_and_can_succeed(self):
        batch = self._review_batch()
        calls = []
        def callback():
            calls.append(True)
            if len(calls) == 1:
                print(json.dumps({"error": "Invalid fields: extra", "code": "integration"}), file=agent.sys.stderr)
                return 2
            print(json.dumps({"report": {"completed": True}}))
            return 0
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(review_rounds, "run", return_value={"action": "complete", "completed": True}) as rounds:
            self.assertEqual(agent._review_after(callback, batch), 0)
        self.assertEqual(len(calls), 2)
        rounds.assert_called_once()
        self.assertTrue(json.loads(output.getvalue())["ai_review_completed"])
        logs = list((batch / "workflow/review-ai-failures").glob("*.json"))
        self.assertEqual(len(logs), 1)
        self.assertIn("Invalid fields", json.loads(logs[0].read_text())["stderr"])
        self.assertFalse((batch / "workflow/review-ai-candidate.json").exists())

    def test_ai_three_protocol_failures_still_execute_three_independent_rounds(self):
        batch = self._review_batch()
        calls, seen = [], []
        def callback():
            calls.append(True)
            print(json.dumps({"error": "Invalid fields: extra", "code": "integration"}), file=agent.sys.stderr)
            return 2
        def invoke(prompt, run_dir, config):
            round_id = review.ROUND_IDS[len(seen)]
            self.assertIn("ROUND: " + round_id, prompt)
            seen.append(run_dir)
            assessment = {"type": "单项填空", "subtypes": ["四个选项"],
                          "knowledge": ["词汇/词汇辨析/动词辨析"], "subquestions": [],
                          "difficulty": [{"number": "1", "value": "容易"}]}
            if round_id == "sample":
                assessment["source_comparison"] = "Compared source words, options, answer and current labels."
            check = {"unit_id": "q-1", "verdict": "ok", "rationale": "Source sentence matches current annotation.",
                     "assessment": assessment, "evidence": [{"path": "01_source/正文.txt", "locator": "第1题", "quote": "She reads a book."}]}
            if round_id == "sample":
                check["number"] = "1"
            return {"response": {"status": "completed", "artifacts": {"review_round": {"checks": [check]}}, "notes": [], "findings": []}}
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(local_agent, "load_config", return_value={"provider": "fixture", "max_input_chars": 600000}), \
             patch.object(review_rounds, "invoke", side_effect=invoke):
            self.assertEqual(agent._review_after(callback, batch, Path("fixture-config")), 3)
        report = json.loads(output.getvalue())
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(set(seen)), 2)
        self.assertTrue(report["execution_completed"])
        self.assertFalse(report["completed"])
        self.assertFalse(report["ai_review_completed"])
        self.assertFalse(report["quality_passed"])
        self.assertEqual(report["ai_review"]["report"]["code"], "AI_EXECUTION_FAILED")
        overall = json.loads((batch / "07_review/overall-review-report.json").read_text())
        self.assertFalse(overall["quality_passed"])
        self.assertFalse(overall["release_ready"])
        self.assertGreaterEqual(overall["required_unchecked_count"], 1)
        self.assertTrue(any(error["code"] == "REQUIRED_CHECK_UNCHECKED" for error in overall["gate_errors"]))
        self.assertEqual(len(list((batch / "workflow/review-ai-failures").glob("*.json"))), 3)
        self.assertFalse((batch / "workflow/review-ai-candidate.json").exists())
        self.assertFalse((batch / agent_tasks.ARTIFACTS["review"]).exists())
        self.assertTrue(any(row.get("code") == "AI_EXECUTION_FAILED" and row["required"]
                            for row in json.loads((batch / "07_review/unchecked.json").read_text())))
        with contextlib.redirect_stdout(io.StringIO()), patch.object(review_rounds, "run", return_value={"action": "complete", "completed": True}):
            self.assertEqual(agent._resume_review(batch), 3)
        saved = json.loads((batch / "workflow/review-ai-state.json").read_text())
        self.assertFalse(saved["report"]["completed"])
        self.assertEqual(saved["execution_attempts"], 3)

    def test_large_portable_review_uses_real_round_task_and_retains_incomplete_result(self):
        batch = self._review_batch()
        source = "START_SOURCE " + "x" * 650000 + " END_SOURCE"
        (batch / "01_source/正文.txt").write_text(source, encoding="utf-8")
        manifest = mf.new_manifest("large-review", batch, {}, {})
        for dep in contracts.STAGE_BY_ID["review"].depends_on:
            mf.touch_stage(manifest, dep, mf.COMPLETED)
        mf.save(batch, manifest)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(agent.main(["export", "--batch-dir", str(batch), "--stage", "review", "--max-input-chars", "1000000"]), 0)
        exported = json.loads(output.getvalue())
        self.assertEqual(exported["round_id"], "labels")
        task = json.loads((Path(exported["package_dir"]) / "task.json").read_text())
        self.assertIn(source, task["prompt"])
        self.assertGreater(len(task["prompt"]), 600000)
        response = batch / "result.json"
        response.write_text(json.dumps({"status": "needs_review", "artifacts": {"review_round": {"checks": []}},
                                        "notes": ["待核验"], "findings": []}), encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(agent.main(["import", "--batch-dir", str(batch), "--task-id", exported["task_id"], "--result", str(response)]), 3)
        report = json.loads(output.getvalue())
        self.assertEqual(report["action"], "blocked")
        self.assertFalse(report["completed"])
        self.assertTrue((batch / report["candidate"]).is_file())
        self.assertFalse((batch / "workflow/review-ai-state.json").exists())
        self.assertFalse((batch / "07_review/review-rounds.json").exists())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(agent.main(["import", "--batch-dir", str(batch), "--task-id", exported["task_id"], "--result", str(response)]), 3)
        self.assertEqual(json.loads(output.getvalue()), report)

    def test_resume_legacy_state_without_binding_uses_default_budget(self):
        batch = self._review_batch()
        state = batch / "workflow/review-ai-state.json"
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps({"completed": True, "task_id": "c374a490-7fb5-4ea1-a2fb-555555555555", "result_sha256": "old"}))
        with contextlib.redirect_stdout(io.StringIO()), patch.object(review_rounds, "run", return_value={"action": "exported", "completed": False}) as rounds:
            self.assertEqual(agent._resume_review(batch), 3)
        self.assertIsNone(rounds.call_args.kwargs["portable_max_input_chars"])

    def test_resume_missing_state_does_not_claim_ai_completed(self):
        batch = self._review_batch()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(review_rounds, "run", return_value={"action": "exported", "completed": False}):
            self.assertEqual(agent._resume_review(batch), 3)
        report = json.loads(output.getvalue())
        self.assertFalse(report["ai_review_completed"])
        self.assertFalse(report["completed"])
        self.assertTrue(any(row.get("code") == "AI_EXECUTION_FAILED" for row in json.loads((batch / "07_review/unchecked.json").read_text())))

    def test_rejected_protocol_binding_cannot_use_model_failure_fallback(self):
        batch = self._review_batch()
        calls = []
        def rejected():
            calls.append(True)
            print(json.dumps({"error": "Task inputs or binding are stale"}), file=agent.sys.stderr)
            return 2
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), patch.object(review_rounds, "run") as rounds:
            self.assertEqual(agent._review_after(rejected, batch, max_attempts=1, allow_failure_fallback=False), 2)
        self.assertEqual(len(calls), 1)
        rounds.assert_not_called()
        self.assertFalse((batch / "workflow/review-ai-state.json").exists())
        self.assertFalse((batch / "07_review/unchecked.json").exists())

    def test_ai_execution_failure_with_missing_inputs_does_not_retry_or_run_rounds(self):
        batch = self._review_batch()
        (batch / "01_source/正文.txt").write_text("", encoding="utf-8")
        calls = []
        def callback():
            calls.append(True)
            print(json.dumps({"error": "Invalid fields", "code": "integration"}), file=agent.sys.stderr)
            return 2
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), patch.object(review_rounds, "run") as rounds:
            self.assertEqual(agent._review_after(callback, batch), 2)
        self.assertEqual(len(calls), 1)
        rounds.assert_not_called()
        self.assertFalse((batch / "workflow/review-ai-state.json").exists())

    def test_ingest_marks_executed_rounds_even_with_quality_findings(self):
        batch = self._review_batch()
        manifest = mf.new_manifest("review", batch, {}, {})
        spec = contracts.STAGE_BY_ID["review"]
        for rel in spec.produces:
            path = batch / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
        mf.save(batch, manifest)
        with patch.object(review, "evaluate_rounds", return_value={
                "execution_completed": True, "passed": False,
                "errors": [{"code": "REVIEW_ROUND_FINDING"}]}):
            report = ingest.ingest(batch, stage="review")
        self.assertIn("review", report["marked"])
        self.assertEqual(mf.load(batch)["stages"]["review"]["status"], mf.COMPLETED)

    def test_ingest_does_not_mark_incomplete_rounds(self):
        batch = self._review_batch()
        mf.save(batch, mf.new_manifest("review", batch, {}, {}))
        for rel in contracts.STAGE_BY_ID["review"].produces:
            path = batch / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
        with patch.object(review, "evaluate_rounds", return_value={
                "execution_completed": False, "passed": False,
                "errors": [{"code": "REVIEW_ROUND_UNCHECKED"}]}):
            report = ingest.ingest(batch, stage="review")
        self.assertNotIn("review", report["marked"])

    def test_missing_implicit_bridge_has_portable_defaults(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(auto, 'BRIDGE_FILE', Path(temp) / 'missing.json'):
            bridge = auto.load_bridge()
            self.assertEqual(set(bridge), {s.id for s in contracts.STAGES if s.kind == contracts.AGENT})
            for command in bridge.values():
                self.assertEqual(command[:4], ['{python}', '-m', 'workflow.agent', 'run'])
            self.assertEqual(auto.load_bridge(Path(temp) / 'explicit-missing.json'), {})
