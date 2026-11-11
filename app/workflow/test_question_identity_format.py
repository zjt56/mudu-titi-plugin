"""Scoped final format and real payload-builder regressions; no remote calls."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from workflow import format_gate, upload_payload
from workflow.test_format_gate import question, group
from workflow.test_upload_payload import catalog_snapshot


def scoped(item, scope):
    item = deepcopy(item)
    item.update(id=f"unit-{scope}", unit_id=f"unit-{scope}", identity_version="scoped-v1",
                section_id=f"sec-{scope}",
                source_section={"title": f"{scope}. 原卷原标题", "ordinal": f"{scope}.",
                                "context": "基础巩固", "occurrence": len(scope)})
    item["question_ids"] = {str(n): f"sec-{scope}-q{n}" for n in item["numbers"]}
    if item["unit_kind"] == "group":
        item["question_id"] = ""
        for child in item["subquestions"]:
            child["question_id"] = item["question_ids"][str(child["number"])]
    else:
        item["question_id"] = item["question_ids"][str(item["number"])]
    return item


def final(items=None):
    return {"batch_id": "scope-format", "name": "原卷明确标题",
            "items": items if items is not None else [scoped(question(), "I"), scoped(question(), "II")]}


class QuestionIdentityFormatTest(unittest.TestCase):
    def codes(self, errors):
        return {error["code"] for error in errors}

    def run_final(self, data):
        with tempfile.TemporaryDirectory(prefix="scoped-format-") as folder:
            root = Path(folder)
            path = root / format_gate.FINAL_PATH
            path.parent.mkdir()
            raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
            path.write_bytes(raw)
            report = format_gate.run(root)
            self.assertEqual(path.read_bytes(), raw)
            return report

    def build(self, data):
        result = upload_payload.build_payload(data, catalog_snapshot())
        return result, result["errors"]

    def test_three_same_printed_numbers_pass_shape_gate_and_native_builder(self):
        data = final([scoped(question(), scope) for scope in ("I", "II", "III")])
        self.assertEqual(format_gate._shape(data), [])
        report = self.run_final(data)
        self.assertTrue(report["passed"], report["errors"])
        built, errors = self.build(data)
        self.assertEqual(errors, [])
        self.assertEqual(built["summary"]["questions"], 3)
        for item in built["payload"]["items"]:
            self.assertFalse(set(item) & {"identity_version", "section_id", "source_section", "question_ids", "question_id", "unit_id"})
        self.assertEqual(data["items"][1]["source_section"]["ordinal"], "II.")

    def test_group_same_printed_numbers_in_distinct_windows_pass(self):
        data = final([scoped(group(), "I"), scoped(group(), "II")])
        self.assertEqual(format_gate._shape(data), [])
        report = self.run_final(data)
        self.assertTrue(report["passed"], report["errors"])
        built, errors = self.build(data)
        self.assertEqual(errors, [])
        self.assertEqual(built["summary"]["questions"], 6)
        for unit in built["payload"]["items"]:
            self.assertEqual([c["number"] for c in unit["subquestions"]], [2, 3, 4])
            self.assertTrue(all("question_id" not in c for c in unit["subquestions"]))

    def test_same_question_id_rejected_even_when_map_and_leaf_agree(self):
        data = final()
        qid = data["items"][0]["question_id"]
        data["items"][1].update(question_id=qid, question_ids={"1": qid})
        self.assertIn("QUESTION_ID_DUPLICATE", self.codes(format_gate._shape(data)))
        self.assertIn("QUESTION_ID_DUPLICATE", self.codes(self.build(data)[1]))

    def test_legacy_same_number_rejected_by_shape_without_changing_old_builder(self):
        first, second = question(), question()
        second.update(id="another", unit_id="another")
        data = final([first, second])
        self.assertIn("QUESTION_NUMBER_DUPLICATE", self.codes(format_gate._shape(data)))
        self.assertNotIn("QUESTION_NUMBER_DUPLICATE", self.codes(self.build(data)[1]))

    def test_missing_mixed_unknown_version_and_partial_identity_rejected(self):
        cases = []
        data = final(); data["items"][1] = question(2); cases.append(data)
        data = final(); del data["items"][0]["identity_version"]; cases.append(data)
        data = final(); data["items"][0]["identity_version"] = "future-v2"; cases.append(data)
        data = final([question()]); data["items"][0]["question_ids"] = {"1": "orphan-q1"}; cases.append(data)
        for data in cases:
            with self.subTest(data=data):
                self.assertIn("SCOPED_IDENTITY_REQUIRED", self.codes(format_gate._shape(data)))
                self.assertIn("SCOPED_IDENTITY_REQUIRED", self.codes(self.build(data)[1]))

    def test_missing_map_keys_unsafe_qids_and_tampered_child_rejected(self):
        mutations = [
            (lambda q: q.update(question_ids={}), "QUESTION_ID_MAP_INVALID"),
            (lambda q: q["question_ids"].update({"99": "extra-q99"}), "QUESTION_ID_MAP_INVALID"),
            (lambda q: q["question_ids"].update({"2": "bad/q2"}), "QUESTION_ID_MAP_INVALID"),
            (lambda q: q["subquestions"][0].update(question_id="wrong-q2"), "QUESTION_ID_MISMATCH"),
            (lambda q: q["subquestions"][0].pop("question_id"), "QUESTION_ID_MISMATCH"),
            (lambda q: q["subquestions"][0].update(unit_id="another-unit"), "CHILD_IDENTITY_CONFLICT"),
            (lambda q: q.update(question_id=q["question_ids"]["2"]), "GROUP_QUESTION_ID_INVALID"),
            (lambda q: q.update(source_section={}), "SOURCE_SECTION_INVALID"),
        ]
        for mutation, code in mutations:
            with self.subTest(code=code):
                data = final([scoped(group(), "I")])
                mutation(data["items"][0])
                self.assertIn(code, self.codes(format_gate._shape(data)))
                self.assertIn(code, self.codes(self.build(data)[1]))

    def test_real_duplicate_child_numbers_still_rejected(self):
        data = final([scoped(group(), "I")])
        data["items"][0]["subquestions"][1] = deepcopy(data["items"][0]["subquestions"][0])
        report = self.run_final(data)
        self.assertFalse(report["passed"])
        self.assertIn("SUBQUESTION_NUMBER_DUPLICATE", self.codes(report["errors"]))
        self.assertIn("GROUP_NUMBER_COVERAGE", self.codes(report["errors"]))

    def test_conflicting_section_or_duplicate_unit_rejected(self):
        data = final()
        data["items"][1]["unit_id"] = data["items"][0]["unit_id"]
        self.assertIn("UNIT_ID_DUPLICATE", self.codes(self.build(data)[1]))
        data = final()
        data["items"][1]["section_id"] = data["items"][0]["section_id"]
        self.assertIn("SOURCE_SECTION_CONFLICT", self.codes(self.build(data)[1]))


if __name__ == "__main__":
    unittest.main()
