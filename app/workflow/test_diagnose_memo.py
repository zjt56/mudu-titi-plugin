"""Request-local diagnosis acceptance, using isolated files and real validators."""
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from workflow import contracts, diagnose, handoff, manifest as mf


class DiagnoseMemoTest(unittest.TestCase):
    CONSUMERS = ("answers", "types", "audio", "explanations", "knowledge")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="diagnose-memo-")
        self.addCleanup(temporary.cleanup)
        self.batch = Path(temporary.name).resolve()
        self.put("01_source/正文.txt", "1. Choose one. A. Yes B. No C. Good D. Bad")
        self.put("01_source/structure.json", {"sections": [], "groups": []})
        self.put("01_source/structure.confirmed.json", {"units": [{
            "unit_kind": "standalone", "numbers": [1], "type": "单项填空",
            "stem": "1. Choose one. A. Yes B. No C. Good D. Bad",
            "source_span": {"basis": "ai_classified", "start_line": 1, "end_line": 1},
        }]})
        self.report = {"passed": True, "input_hashes": {
            relative: mf.sha256_file(self.batch / relative) for relative in (
                "01_source/正文.txt", "01_source/structure.json",
                "01_source/structure.confirmed.json")}}
        self.put("07_review/structure-gate.json", self.report)
        self.state = mf.new_manifest("memo-test", self.batch, {}, {})
        for record in self.state["stages"].values():
            record["status"] = mf.COMPLETED
        for stage_id in self.CONSUMERS:
            self.state["stages"][stage_id]["status"] = mf.PENDING
        mf.save(self.batch, self.state)

    def put(self, relative, value):
        target = self.batch / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
                          encoding="utf-8")

    def snapshot(self):
        return {path.relative_to(self.batch).as_posix(): path.read_bytes()
                for path in self.batch.rglob("*") if path.is_file()}

    def structure_calls(self, inspect):
        return [call for call in inspect.call_args_list if call.args[2] == "structure_gate"]

    def assert_structure_dependency(self, result, satisfied):
        consumers = {item["stage"]: item for item in result["items"]}
        for stage_id in self.CONSUMERS:
            with self.subTest(consumer=stage_id):
                self.assertEqual("structure_gate" not in consumers[stage_id]["unmet"], satisfied)

    def test_shared_dependency_runs_full_consumer_acceptance_once(self):
        before = self.snapshot()
        with patch.object(handoff, "inspect_stage", wraps=handoff.inspect_stage) as inspect, \
             patch.object(mf, "dependency_satisfied", wraps=mf.dependency_satisfied) as dependency:
            result = diagnose.diagnose(self.batch)
        self.assert_structure_dependency(result, True)
        self.assertEqual(len(self.structure_calls(inspect)), 1)
        # Cover the actual dependency fan-out, including a missing-output result.
        expected = {dep for sid in self.CONSUMERS for dep in contracts.STAGE_BY_ID[sid].depends_on}
        self.assertEqual(Counter(call.args[1] for call in dependency.call_args_list),
                         Counter({stage_id: 1 for stage_id in expected}))
        self.assertEqual(self.snapshot(), before)

    def test_negative_consumer_acceptance_is_shared_without_retry(self):
        self.put("07_review/structure-gate.json", self.report | {"passed": False})
        with patch.object(handoff, "inspect_stage", wraps=handoff.inspect_stage) as inspect:
            result = diagnose.diagnose(self.batch)
        self.assert_structure_dependency(result, False)
        self.assertEqual(len(self.structure_calls(inspect)), 1)

    def test_negative_dependency_before_consumer_acceptance_is_shared(self):
        self.state["stages"]["structure_gate"]["status"] = mf.PENDING
        mf.save(self.batch, self.state)
        with patch.object(handoff, "inspect_stage", wraps=handoff.inspect_stage) as inspect, \
             patch.object(mf, "dependency_satisfied", wraps=mf.dependency_satisfied) as dependency:
            result = diagnose.diagnose(self.batch)
        self.assert_structure_dependency(result, False)
        self.assertEqual(len(self.structure_calls(inspect)), 0)
        self.assertEqual(sum(call.args[1] == "structure_gate" for call in dependency.call_args_list), 1)

    def test_next_diagnose_and_explain_recheck_changed_files_in_both_directions(self):
        with patch.object(handoff, "inspect_stage", wraps=handoff.inspect_stage) as inspect:
            self.assert_structure_dependency(diagnose.diagnose(self.batch), True)
            self.put("07_review/structure-gate.json", self.report | {"passed": False})
            # explain is a separate observation and must see the new rejection.
            self.assertIn("structure_gate", diagnose.explain(self.batch, "answers")["unmet"])
            self.put("07_review/structure-gate.json", self.report)
            self.assert_structure_dependency(diagnose.diagnose(self.batch), True)
        self.assertEqual(len(self.structure_calls(inspect)), 3)

    def test_mutating_diagnosis_cannot_change_other_consumers_or_later_observations(self):
        expected_dependencies = contracts.STAGE_BY_ID["answers"].depends_on
        first = diagnose.diagnose(self.batch)
        answers = next(item for item in first["items"] if item["stage"] == "answers")
        answers["unmet"].append("structure_gate")
        answers["depends_on"].clear()
        first["quality_notes"].append({"stage": "fake"})
        types = next(item for item in first["items"] if item["stage"] == "types")
        self.assertNotIn("structure_gate", types["unmet"])
        self.assertEqual(contracts.STAGE_BY_ID["answers"].depends_on, expected_dependencies)
        later = diagnose.diagnose(self.batch)
        self.assert_structure_dependency(later, True)
        self.assertEqual(later["quality_notes"], [])

    def test_diagnosis_result_is_not_reused_by_direct_or_write_acceptance(self):
        with patch.object(handoff, "inspect_stage", wraps=handoff.inspect_stage) as inspect:
            self.assert_structure_dependency(diagnose.diagnose(self.batch), True)
            self.put("07_review/structure-gate.json", self.report | {"passed": False})
            self.assertFalse(mf.dependency_satisfied(self.state, "structure_gate", allow_unqualified=True))
            # Delivery recording must inspect again even after a successful view.
            mf.record_delivery(self.batch, self.state, contracts.STAGE_BY_ID["structure_gate"])
        errors = self.state["stages"]["structure_gate"]["handoff_errors"]
        self.assertIn("Structure gate has not passed", errors[0]["message"])
        self.assertEqual(len(self.structure_calls(inspect)), 3)


if __name__ == "__main__":
    unittest.main()
