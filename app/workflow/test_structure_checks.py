# -*- coding: utf-8 -*-
import hashlib
import json
import unittest

from workflow import structure_checks as gate


def digest(value):
    return hashlib.sha256(value).hexdigest()


class StructureChecksTest(unittest.TestCase):
    def setUp(self):
        self.structure = json.dumps({
            "sections": [{"type": "听力", "questions": [[1, 2], [3, 3]]}],
            "groups": [{"id": "g1", "questions": [1, 2]}],
            "image_questions": [1],
        }, ensure_ascii=False).encode()
        self.source = b"( )1. Extracted question\n( )2. Extracted question\n( )3. Extracted question"
        self.original = b"original document"
        self.review = {"structure_sha256": digest(self.structure),
                       "source_sha256": digest(self.source),
                       "original_sha256": digest(self.original),
                       "coverage": {"status": "passed", "reviewer": "editor", "evidence": ["original all pages"]},
                       "items": {str(n): {k: {"status": "passed", "reviewer": "editor",
                                               "evidence": [f"original page 1, question {n}"]}
                                           for k in gate.CHECKS} for n in range(1, 4)}}

    def run_gate(self):
        return gate.inspect(self.structure, self.source, self.review, self.original)

    def test_documented_ranges_and_groups_pass_with_review(self):
        result = self.run_gate()
        self.assertTrue(result["passed"])
        self.assertEqual([q["number"] for q in result["questions"]], [1, 2, 3])
        self.assertEqual(len(result["questions"][0]["checks"]), 7)
        self.assertEqual(result["questions"][0]["checks"]["content_scope"]["status"], "passed")

    def test_missing_review_and_missing_original_block(self):
        result = gate.inspect(self.structure, self.source)
        self.assertFalse(result["passed"])
        self.assertTrue(all(c["status"] == "unchecked" for q in result["questions"]
                            for c in q["checks"].values()))
        self.assertFalse(gate.inspect(self.structure, self.source, self.review)["passed"])

    def test_stale_input_invalidates_all(self):
        self.source += b" changed"
        self.assertFalse(self.run_gate()["review_bound"])
        self.assertEqual(self.run_gate()["questions"][0]["checks"]["images"]["status"], "unchecked")

    def test_unknown_and_failed_with_evidence_reported_per_question(self):
        row = self.review["items"]["2"]
        row["images"]["status"] = "unchecked"
        row["alignment"].update(status="failed", reason="错行")
        result = self.run_gate()
        self.assertFalse(result["passed"])
        self.assertEqual({r["check"] for r in result["questions"][1]["rework"]}, {"images", "alignment"})
        self.assertEqual(result["questions"][1]["checks"]["alignment"]["evidence"], ["original page 1, question 2"])

    def test_missing_whole_paper_coverage_blocks(self):
        del self.review["coverage"]
        result = self.run_gate()
        self.assertFalse(result["passed"])
        self.assertTrue(any("漏题" in issue for issue in result["issues"]))

    def test_no_evidence_cannot_pass(self):
        self.review["items"]["1"]["stem_matches"]["evidence"] = []
        self.assertEqual(self.run_gate()["questions"][0]["checks"]["stem_matches"]["status"], "unchecked")

    def test_duplicates_and_bad_references_block(self):
        self.structure = json.dumps({"sections": [{"type": "x", "questions": [[1, 2], 2]}],
                                     "groups": [{"questions": [9]}]}).encode()
        result = self.run_gate()
        self.assertFalse(result["passed"])
        self.assertTrue(result["issues"])
        self.assertEqual(result["questions"][1]["checks"]["questions_options_complete"]["status"], "failed")

    def test_malformed_structure_and_empty_source_block(self):
        result = gate.inspect(b"not json", b"")
        self.assertFalse(result["passed"])
        self.assertTrue(result["issues"])


if __name__ == "__main__":
    unittest.main()
