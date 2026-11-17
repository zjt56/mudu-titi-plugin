"""Complete read-only check evidence and content/final phase boundaries."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from workflow import checks


ROOT = Path(__file__).resolve().parents[1]


class CheckFindingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.batch = Path(self.temp.name) / "06_batch/batch_meta.json"
        self.batch.parent.mkdir()
        self.batch.write_text('{"items": []}', encoding="utf-8")

    def run_output(self, stdout="", stderr="", code=1, check=None):
        check = check or checks.Check("test check", "check.py")
        proc = subprocess.CompletedProcess([], code, stdout, stderr)
        with patch.object(checks.subprocess, "run", return_value=proc):
            return checks.run_check(check, ROOT, self.batch, {})

    def test_complete_failure_lines_survive_display_tail(self):
        lines = [f"[E20] 第{number}题选项不完整 " + "x" * 100 for number in range(1, 7)]
        result = self.run_output("\n".join(lines), "[FAIL] 6 条问题\n")
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["findings"]), 6)
        self.assertEqual([row["message"] for row in result["findings"]], lines)
        self.assertEqual(result["stdout"], "\n".join(lines))
        self.assertLessEqual(len(result["output"]), 400)
        self.assertNotIn(lines[0], result["output"])
        for row in result["findings"]:
            self.assertEqual(row["code"], "E20")
            self.assertEqual(row["check"], "test check")
            self.assertEqual(row["script"], "check.py")
            self.assertEqual(row["phase"], "content")
            self.assertTrue(row["blocking"])
            self.assertEqual(row["severity"], "error")
            self.assertNotIn("unit_id", row)
            self.assertNotIn("number", row)

    def test_structured_report_preserves_explicit_identity_and_all_errors(self):
        errors = [{"code": "ANSWER_MISMATCH", "unit_id": "scope-a", "question_id": f"q-{n}",
                   "number": 1, "message": f"真实差异 {n}", "candidate": "A", "source": "B"}
                  for n in range(6)]
        output = json.dumps({"passed": False, "issues": errors}, ensure_ascii=False, indent=2)
        result = self.run_output(output)
        self.assertEqual(len(result["findings"]), 6)
        for finding, original in zip(result["findings"], errors):
            for key, value in original.items():
                self.assertEqual(finding[key], value)

    def test_warning_success_is_observation_without_blocking(self):
        result = self.run_output("[W19] 第1题答题线提示\n提示：保留原页核验\n[OK] 通过\n",
                                 "[WARN] 1 条格式提示（不阻断流程）\n", 0)
        self.assertTrue(result["passed"])
        self.assertEqual(len(result["findings"]), 3)
        self.assertTrue(all(not row["blocking"] for row in result["findings"]))
        self.assertTrue(all(row["severity"] == "warning" for row in result["findings"]))
        self.assertIn("[W19] 第1题答题线提示", result["warnings"])

    def test_nonzero_empty_or_only_warnings_never_becomes_clean(self):
        for output in ("", "[W19] 格式提示\n"):
            with self.subTest(output=output):
                result = self.run_output(output, code=7)
                self.assertFalse(result["passed"])
                fallback = [row for row in result["findings"] if row["code"] == "CHECK_EXIT_NONZERO"]
                self.assertEqual(len(fallback), 1)
                self.assertTrue(fallback[0]["blocking"])
                self.assertEqual(fallback[0]["exit_code"], 7)

    def test_observation_failure_retains_explicit_nonblocking_evidence(self):
        spec = checks.Check("final observation", "check.py", blocking=False, phase="format")
        result = self.run_output(code=2, check=spec)
        self.assertFalse(result["passed"])
        self.assertEqual(result["findings"][0]["code"], "CHECK_EXIT_NONZERO")
        self.assertFalse(result["findings"][0]["blocking"])
        self.assertEqual(result["findings"][0]["phase"], "format")

    def test_execution_errors_and_timeout_keep_evidence(self):
        failures = [
            (OSError("script unavailable"), "CHECK_EXECUTION_ERROR"),
            (subprocess.TimeoutExpired([], 1, output=b"[E20] partial failure\n"), "CHECK_TIMEOUT"),
        ]
        for exc, expected in failures:
            with self.subTest(code=expected), patch.object(checks.subprocess, "run", side_effect=exc):
                result = checks.run_check(checks.Check("test", "check.py"), ROOT, self.batch, {}, timeout=1)
            self.assertFalse(result["passed"])
            self.assertIn(expected, [row["code"] for row in result["findings"]])
            self.assertTrue(all(row["blocking"] for row in result["findings"]))
            if expected == "CHECK_TIMEOUT":
                self.assertIn("[E20] partial failure", [row["message"] for row in result["findings"]])

    def test_args_preserve_old_positionals_and_reach_subprocess(self):
        spec = checks.Check("test", "check.py", True, ("source",), "note", "content", ("--content-only",))
        argv = checks._argv(spec, ROOT, self.batch, {"source": {"path": "paper.txt"}})
        self.assertEqual(argv[1:3], ["-X", "utf8"])
        self.assertEqual(argv[-3:], ["--source", "paper.txt", "--content-only"])
        module = checks._argv(checks.CHECKS[0], ROOT, self.batch, {})
        self.assertEqual(module[1:5], ["-X", "utf8", "-m", "workflow.label_audit"])
        self.assertEqual(spec.args, ("--content-only",))

    def test_type_layout_and_difficulty_have_distinct_phase_contracts(self):
        content = [c for c in checks.CONTENT_CHECKS if c.script.endswith("audit_type_layout.py")]
        final = [c for c in checks.FINAL_FORMAT_CHECKS if c.script.endswith("audit_type_layout.py")]
        self.assertEqual(len(content), 1)
        self.assertEqual(content[0].args, ("--content-only",))
        self.assertTrue(content[0].blocking)
        self.assertEqual(len(final), 1)
        self.assertEqual(final[0].args, ())
        self.assertFalse(final[0].blocking)
        self.assertNotEqual(content[0].name, final[0].name)
        self.assertFalse(any(c.script.endswith("check_difficulty.py") for c in checks.CONTENT_CHECKS))
        difficulty = [c for c in checks.FINAL_FORMAT_CHECKS if c.script.endswith("check_difficulty.py")]
        self.assertEqual(len(difficulty), 1)
        self.assertTrue(difficulty[0].blocking)

    def test_real_type_layout_keeps_missing_options_hard_and_duplicate_display_late(self):
        self.batch.write_text(json.dumps({"items": [{"id": "scope-a", "unit_kind": "group",
            "question_type": "短文填空", "material": "1. The story continues.",
            "subquestions": [{"number": 1, "stem": "1. The story continues."}]}]}), encoding="utf-8")
        content = next(c for c in checks.CONTENT_CHECKS if c.script.endswith("audit_type_layout.py"))
        final = next(c for c in checks.FINAL_FORMAT_CHECKS if c.script.endswith("audit_type_layout.py"))
        self.assertTrue(checks.run_check(content, ROOT, self.batch, {})["passed"])
        late = checks.run_check(final, ROOT, self.batch, {})
        self.assertFalse(late["passed"])
        self.assertEqual(late["findings"][0]["code"], "D7")
        self.assertFalse(late["findings"][0]["blocking"])
        self.batch.write_text(json.dumps({"items": [{"id": "scope-b", "unit_kind": "standalone",
            "question_type": "单项填空", "options": ["A. One", "B. Two"]}]}), encoding="utf-8")
        hard = checks.run_check(content, ROOT, self.batch, {})
        self.assertFalse(hard["passed"])
        self.assertTrue(hard["findings"][0]["blocking"])
        self.assertEqual(hard["findings"][0]["code"], "E20")

    def test_run_all_aggregates_all_findings_and_hard_final_failure(self):
        hard = checks.Check("difficulty", "check_difficulty.py", phase="format")
        soft = checks.Check("layout", "audit_type_layout.py", blocking=False, phase="format")
        proc = subprocess.CompletedProcess([], 1, "[D12] difficulty mismatch\n[W9] hint\n", "")
        with patch.object(checks, "CHECKS", (hard, soft)), patch.object(checks.subprocess, "run", return_value=proc):
            report = checks.run_all(ROOT, self.batch, {}, profile="format")
        self.assertFalse(report["passed"])
        self.assertEqual(report["blocking_failures"], [hard.name])
        self.assertEqual(len(report["findings"]), 4)
        self.assertEqual(report["findings"], [row for result in report["checks"] for row in result["findings"]])
        self.assertIn(soft.name, report["warnings"])

    def test_missing_inputs_and_missing_batch_are_top_level_findings(self):
        hard = checks.Check("hard final", "final.py", needs=("answers",), phase="format")
        with patch.object(checks, "CHECKS", (hard,)), patch.object(checks.subprocess, "run") as run:
            report = checks.run_all(ROOT, self.batch, {}, profile="format")
        run.assert_not_called()
        self.assertFalse(report["passed"])
        self.assertEqual(report["blocking_failures"], [hard.name])
        self.assertEqual(report["findings"][0]["code"], "ANSWERS_INPUT_MISSING")
        self.assertTrue(report["findings"][0]["blocking"])
        self.assertEqual(report["findings"][0]["phase"], "format")
        source = checks.Check("source check", "source.py", needs=("source",))
        with patch.object(checks, "CHECKS", (source,)):
            report = checks.run_all(ROOT, self.batch, {})
        self.assertFalse(report["passed"])
        self.assertEqual(report["findings"][0]["code"], "SOURCE_INPUT_MISSING")
        missing = checks.run_all(ROOT, self.batch.with_name("absent.json"), {})
        self.assertFalse(missing["passed"])
        self.assertEqual(missing["findings"][0]["code"], "BATCH_INPUT_MISSING")
        self.assertTrue(missing["findings"][0]["blocking"])


if __name__ == "__main__":
    unittest.main()
