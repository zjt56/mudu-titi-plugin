"""Scoped question identities survive the local independent-answer check."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from workflow import checks


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/06-upload/check_answers.py"
spec = importlib.util.spec_from_file_location("scoped_check_answers", SCRIPT)
answers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(answers)


def item(uid, qid, answer, question_type="单项填空", grouped=False):
    row = {"identity_version": "scoped-v1", "unit_id": uid, "id": uid,
           "section_id": "section-" + uid, "source_section": {"title": "Ⅰ. " + uid},
           "question_ids": {"1": qid}, "numbers": [1], "number": 1,
           "question_type": question_type, "answer": answer, "subquestions": []}
    if grouped:
        row["subquestions"] = [{"number": 1, "question_id": qid, "answer": answer}]
    else:
        row["question_id"] = qid
    return row


class ScopedAnswerCheckTest(unittest.TestCase):
    def setUp(self):
        self.batch = {"items": [item("u-a", "qid-a", "A"), item("u-b", "qid-b", "B", grouped=True)]}

    def source(self, mapping):
        return answers.collect_json({"answers": mapping}, scoped=True)

    def test_same_printed_number_compares_by_question_identity(self):
        report = answers.inspect(self.batch, self.source({"qid-a": "A", "qid-b": "B"}))
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["checked_question_ids"], ["qid-a", "qid-b"])
        self.assertEqual(report["checked_numbers"], [1, 1])

    def test_mismatch_keeps_unit_and_printed_number(self):
        report = answers.inspect(self.batch, self.source({"qid-a": "A", "qid-b": "C"}))
        self.assertEqual(report["issues"], [{"code": "ANSWER_MISMATCH", "unit_id": "u-b",
            "question_id": "qid-b", "number": 1, "message": "正式答案与独立答案不一致",
            "candidate": "B", "source": "C"}])

    def test_numeric_source_with_repeated_number_fails_scope_binding(self):
        report = answers.inspect(self.batch, self.source({"1": "A"}))
        self.assertFalse(report["passed"])
        self.assertEqual([issue["code"] for issue in report["issues"]], ["ANSWER_SOURCE_SCOPE_UNBOUND"])

    def test_writing_exemption_does_not_exempt_other_section_same_number(self):
        self.batch["items"][0] = item("u-a", "qid-a", None, "书面表达")
        report = answers.inspect(self.batch, self.source({"qid-b": "C"}))
        self.assertFalse(report["passed"])
        self.assertEqual(report["issues"][0]["question_id"], "qid-b")

    def test_independent_scoped_rows_inherit_unit_mapping(self):
        source = answers.collect_json(self.batch, scoped=True)
        self.assertEqual(set(source), {"qid-a", "qid-b"})
        self.assertTrue(answers.inspect(self.batch, source)["passed"])

    def test_mixed_batch_does_not_fall_back_to_numeric_identity(self):
        self.batch["items"][1].pop("identity_version")
        with self.assertRaisesRegex(answers.AnswerError, "不能回退全局题号"):
            answers.inspect(self.batch, self.source({"qid-a": "A", "qid-b": "B"}))

    def test_repeated_unscoped_text_answer_reports_missing_scope(self):
        with self.assertRaisesRegex(answers.AnswerError, "ANSWER_SOURCE_SCOPE_UNBOUND"):
            answers.parse_text("1. A\n1. B", scoped=True)

    def test_extra_group_child_cannot_be_hidden_by_unit_numbers(self):
        self.batch["items"][1]["subquestions"].append({"number": 2, "answer": "C"})
        with self.assertRaisesRegex(answers.AnswerError, "映射不一致"):
            answers.inspect(self.batch, self.source({"qid-a": "A", "qid-b": "B"}))

    def test_one_unambiguous_printed_number_keeps_source_compatibility(self):
        self.batch["items"] = self.batch["items"][:1]
        self.assertTrue(answers.inspect(self.batch, self.source({"1": "A"}))["passed"])

    def test_legacy_numeric_contract_is_unchanged(self):
        report = answers.inspect({"items": [{"number": 1, "answer": "A"}]},
                                 answers.collect_json({"answers": {"1": "A"}}))
        self.assertTrue(report["passed"])
        self.assertEqual(report["checked_numbers"], [1])
        with self.assertRaises(answers.AnswerError):
            answers.collect_json({"answers": {"qid-a": "A"}})

    def test_real_checks_runner_consumes_prepared_scoped_answers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            batch_path = root / "06_batch/batch.json"
            source_path = root / "01_source_ans/answers.json"
            batch_path.parent.mkdir()
            source_path.parent.mkdir()
            batch_path.write_text(json.dumps(self.batch), encoding="utf-8")
            source_path.write_text(json.dumps({"answers": {"qid-a": "A", "qid-b": "B"}}), encoding="utf-8")
            report = checks.run_all(SCRIPT.parents[2], batch_path, {},
                                    only=("scripts/06-upload/check_answers.py",))
            self.assertTrue(report["passed"], report)
            self.assertEqual(len(report["checks"]), 1)
            result = json.loads(report["checks"][0]["output"])
            self.assertEqual(result["checked_question_ids"], ["qid-a", "qid-b"])


if __name__ == "__main__":
    unittest.main()
