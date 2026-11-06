# -*- coding: utf-8 -*-
"""Audio skips waive media work, never original-question fidelity checks."""
import argparse
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import auto, contracts, manifest as mf, runner


class AudioSkipTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.batch = Path(self.temp.name) / "batch"
        self.manifest = mf.new_manifest("batch", self.batch, {
            "audio": {"exists": True, "path": "unused-test-recording.mp3"}}, {})
        for rel in (*contracts.STAGE_BY_ID['extract'].produces,
                    *contracts.STAGE_BY_ID['structure_gate'].produces):
            path = self.batch / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}', encoding='utf-8')
        (self.batch / "01_source/附录.txt").write_text("1.A", encoding="utf-8")
        for stage in ("extract", "structure_gate"):
            mf.touch_stage(self.manifest, stage, mf.COMPLETED)
        mf.save(self.batch, self.manifest)

    def _structure(self, types):
        rows = [{"unit_id": f"q-{n}", "numbers": [n],
                 "unit_kind": "standalone", "type": kind}
                for n, kind in enumerate(types, 1)]
        path = self.batch / "01_source/structure.confirmed.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"units": rows}), encoding="utf-8")
        return rows

    def _skip_marker(self):
        path = self.batch / "02_audio/SKIP_LISTENING"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("explicit user skip\n", encoding="utf-8")

    def _reason(self):
        return mf.skip_reason(self.manifest, contracts.STAGE_BY_ID["audio"])

    def _runner_args(self, dry_run=False):
        return argparse.Namespace(batch_dir=str(self.batch), stage="audio",
                                  until=None, force=False, dry_run=dry_run)

    def test_no_listening_requires_valid_nonempty_confirmed_typed_units(self):
        self.assertEqual(self._reason(), "")
        for types in ([], [None], [""], ["单项填空", "听力短对话"],
                      ["单项填空", " 听力长对话 "], ["单项填空", None]):
            with self.subTest(types=types):
                self._structure(types)
                self.assertEqual(self._reason(), "")
        path = self.batch / "01_source/structure.confirmed.json"
        path.write_text('{"units":[{"type":"单项填空"}]}', encoding="utf-8")
        self.assertEqual(self._reason(), "")
        self._structure(["单项填空", "阅读理解"])
        self.assertIn("无听力", self._reason())

    def test_explicit_skip_does_not_require_structure_or_skip_other_agent_stages(self):
        self._skip_marker()
        self.assertIn("用户选择", self._reason())
        for stage in ("answers", "types", "knowledge", "assemble"):
            self.assertEqual(mf.skip_reason(self.manifest, contracts.STAGE_BY_ID[stage]), "")

    def test_runner_skips_agent_audio_before_any_bridge_or_model_dispatch(self):
        for marker in (False, True):
            with self.subTest(marker=marker):
                self._structure(["听力短对话"] if marker else ["单项填空"])
                if marker:
                    self._skip_marker()
                mf.touch_stage(self.manifest, "audio", mf.PENDING)
                mf.save(self.batch, self.manifest)
                with patch.object(auto, "load_bridge") as bridge, \
                     patch.object(runner, "_execute_command") as execute:
                    self.assertEqual(runner.cmd_run(self._runner_args()), 0)
                bridge.assert_not_called()
                execute.assert_not_called()
                self.assertEqual(mf.load(self.batch)["stages"]["audio"]["status"], mf.SKIPPED)

    def test_runner_dry_run_reports_skip_without_mutating_manifest(self):
        self._structure(["单项填空"])
        before = mf.manifest_path(self.batch).read_bytes()
        with patch.object(runner, "_execute_command") as execute:
            self.assertEqual(runner.cmd_run(self._runner_args(dry_run=True)), 0)
        execute.assert_not_called()
        self.assertEqual(mf.manifest_path(self.batch).read_bytes(), before)

    def test_auto_skips_audio_even_without_bridge(self):
        self._structure(["单项填空"])
        with patch.object(auto, "PARALLEL_AGENTS_ENABLED", False), \
             patch.object(runner, "_execute_command") as execute:
            result = auto.advance(self.batch, {}, stop_at="explanations")
        execute.assert_not_called()
        self.assertEqual(mf.load(self.batch)["stages"]["audio"]["status"], mf.SKIPPED)
        self.assertNotIn("audio", [row["stage"] for row in result["pending_stages"]])

    def test_auto_explicit_skip_recovers_failed_audio_and_retains_concurrent_notes(self):
        self._structure(["听力短对话"])
        self._skip_marker()
        fresh = mf.load(self.batch)
        fresh["concurrent_marker"] = "keep"
        mf.touch_stage(fresh, "audio", mf.FAILED, notes="old audio failure")
        mf.save(self.batch, fresh)
        with patch.object(auto, "PARALLEL_AGENTS_ENABLED", False):
            result = auto.advance(self.batch, {}, stop_at="explanations")
        saved = mf.load(self.batch)
        self.assertEqual(saved["stages"]["audio"]["status"], mf.SKIPPED)
        self.assertEqual(saved["concurrent_marker"], "keep")
        self.assertNotIn("audio", [row["stage"] for row in result["failed_stages"]])

    def test_auto_parallel_wave_excludes_audio_before_its_own_turn(self):
        from workflow import agent, local_agent
        for marker in (False, True):
            with self.subTest(marker=marker):
                self._structure(["听力短对话"] if marker else ["单项填空"])
                if marker:
                    self._skip_marker()
                mf.touch_stage(self.manifest, "audio", mf.PENDING)
                mf.save(self.batch, self.manifest)
                dispatched = []

                def offline(batch, stages, config):
                    dispatched.extend(stage.id for stage in stages)
                    current = mf.load(batch)
                    for stage in stages:
                        mf.touch_stage(current, stage.id, mf.COMPLETED)
                    mf.save(batch, current)
                    return {stage.id: 0 for stage in stages}

                with patch.object(auto, "PARALLEL_AGENTS_ENABLED", True), \
                     patch.object(agent, "configured", return_value=True), \
                     patch.object(local_agent, "load_config", return_value={}), \
                     patch.object(auto, "_parallel_ready_agents", side_effect=offline), \
                     patch.object(runner, "_execute_command") as execute:
                    auto.advance(self.batch, auto.load_bridge(), stop_at="explanations")
                self.assertEqual(dispatched, ["answers", "types"])
                execute.assert_not_called()
                self.assertEqual(mf.load(self.batch)["stages"]["audio"]["status"], mf.SKIPPED)

    def test_listening_without_explicit_skip_still_dispatches_audio(self):
        self._structure(["听力短对话"])
        with patch.object(auto, "load_bridge", return_value={"audio": ["offline"]}), \
             patch.object(runner, "_execute_command", return_value=3) as execute:
            self.assertEqual(runner.cmd_run(self._runner_args()), 3)
        self.assertEqual(execute.call_args.args[2].id, "audio")
        self.assertEqual(mf.load(self.batch)["stages"]["audio"]["status"], mf.PENDING)

    def test_skip_excludes_listening_media_content_but_keeps_other_units(self):
        # 听力按规则整体跳过、不收录：其题图/选项来自音频与题图，不做文字保真要求。
        # 非听力单元的题干缺失仍然阻断，过滤不能变成整批放水。
        from workflow import question_content
        self._structure(["听力短对话", "书面表达"])
        self._skip_marker()
        target = self.batch / "06_batch/batch_meta.json"
        target.parent.mkdir(parents=True)
        target.write_text('{}', encoding="utf-8")
        issues = [{"unit_id": "q-1", "code": code, "message": code} for code in (
            "SOURCE_IMAGE_MISSING", "OPTION_CONTENT_MISMATCH",
            "SOURCE_OPTIONS_INCOMPLETE", "SHARED_OPTIONS_MISMATCH")]
        issues.append({"unit_id": "q-2", "code": "SOURCE_STEM_MISSING",
                       "message": "missing writing task"})
        report = {"passed": True, "checks": [], "blocking_failures": [],
                  "warnings": [], "skipped": []}
        with patch.object(runner.checks, "run_all", return_value=report), \
             patch.object(question_content, "audit_batch_source", return_value={
                 "passed": False, "issues": issues}):
            self.assertEqual(runner.do_validate(self.batch, self.manifest), 1)
        saved = json.loads((self.batch / "07_review/validation-report.json").read_text())
        kept = saved["source_audit"]["issues"]
        self.assertEqual([(i["unit_id"], i["code"]) for i in kept],
                         [("q-2", "SOURCE_STEM_MISSING")])
        self.assertIn("SOURCE_SLICE_MISMATCH", saved["blocking_failures"])
        self.assertEqual(mf.load(self.batch)["stages"]["validate"]["status"], mf.FAILED)

    def test_skip_off_keeps_listening_media_issues(self):
        from workflow import question_content
        self._structure(["听力短对话", "书面表达"])
        issues = [{"unit_id": "q-1", "code": "SOURCE_IMAGE_MISSING", "message": "img"}]
        with patch.object(question_content, "audit_batch_source", return_value={
                "passed": False, "issues": issues}):
            with patch.object(runner, "source_audit_for_batch", wraps=runner.source_audit_for_batch):
                audit = runner.source_audit_for_batch(self.batch, {})
        self.assertEqual(audit["issues"], issues)


class BatchIntegrityAudioSkipTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / "scripts/06-upload/audit_batch_integrity.py"
        spec = importlib.util.spec_from_file_location("audio_skip_integrity_test", path)
        cls.audit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.audit)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.batch = Path(self.temp.name) / "batch"
        self.target = self.batch / "06_batch/batch_meta.json"
        self.target.parent.mkdir(parents=True)

    def _run(self, items, skip):
        self.target.write_text(json.dumps({"items": items}), encoding="utf-8")
        if skip:
            marker = self.batch / "02_audio/SKIP_LISTENING"
            marker.parent.mkdir(parents=True)
            marker.write_text("explicit user skip", encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            code = self.audit.main(["--batch", str(self.target)])
        return code, output.getvalue()

    def test_listening_source_without_transcript_is_valid_with_or_without_skip(self):
        unit = {"unit_id": "group-1-2", "unit_kind": "group",
                "question_type": "听力长对话", "material": "听录音，回答问题。",
                "subquestions": [{"number": 1, "options": [{"label": "A", "text": "one"}]}]}
        for skip in (False, True):
            with self.subTest(skip=skip):
                code, output = self._run([unit], skip=skip)
                self.assertEqual(code, 0, output)
                self.assertNotIn("E20-听力", output)

    def test_skip_retains_empty_option_checks_for_listening_and_nonlistening(self):
        listening = {"unit_id": "group-1-2", "unit_kind": "group",
                     "question_type": "听力长对话", "subquestions": [
                         {"number": 1, "options": [{"label": "A", "text": " "}]}]}
        reading = {"unit_id": "q-3", "unit_kind": "standalone",
                   "question_type": "单项填空", "options": [{"label": "B", "text": None}]}
        code, output = self._run([listening, reading], skip=True)
        self.assertNotEqual(code, 0)
        self.assertIn("E20-空选项] group-1-2 子题 1", output)
        self.assertIn("E20-空选项] q-3", output)
        self.assertNotIn("E20-听力", output)

    def test_skip_retains_shared_material_and_watermark_checks(self):
        material = "This is the shared original reading passage."
        units = [{"unit_id": "group-1-2", "material": material},
                 {"unit_id": "group-3-4", "material": material + " additional next passage text repeated incorrectly",
                  "stem": "[来源: forbidden watermark"}]
        code, output = self._run(units, skip=True)
        self.assertNotEqual(code, 0)
        self.assertIn("E20-包含", output)
        self.assertIn("E20-残留", output)


if __name__ == "__main__":
    unittest.main()
