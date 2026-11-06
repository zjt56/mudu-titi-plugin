"""Original DOCX -> extractor -> gate regressions; never touch live batches."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from workflow import extract, manifest as mf, structure_gate
from workflow.docx_extract import extract_docx


class WritingNumberInferenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.original = self.root / "original.docx"
        self.batch = self.root / "batch"
        (self.batch / "01_source").mkdir(parents=True)
        (self.batch / "workflow").mkdir()
        (self.batch / "workflow/manifest.json").write_text(json.dumps({
            "inputs": {"source": {"path": str(self.original)}}}), encoding="utf-8")

    def paper(self, count=1):
        return "\n".join([
            "2022年安徽省初中学业水平考试", "Ⅰ.单词拼写(共80小题)",
            *[f"{n}. Complete word {n}." for n in range(1, 81)],
            f"Ⅹ.书面表达(共{count}小题;满分20分)",
            "假定你是李辉，请写一封邮件介绍你家乡。", "1. its people", "2. its parks",
            "Dear Mike,", "Yours,", "Li Hui",
        ])

    def original_docx(self, text):
        xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
               '<w:body>' + ''.join('<w:p><w:r><w:t>' + escape(line) + '</w:t></w:r></w:p>'
                                    for line in text.splitlines()) + '</w:body></w:document>')
        with zipfile.ZipFile(self.original, "w") as archive:
            archive.writestr("word/document.xml", xml)

    def prepare(self, text=None):
        self.original_docx(text or self.paper())
        native = extract_docx(self.original, self.root / "native")
        structure, body, _ = extract.infer_structure(native["text"])
        self.save(structure, body)
        return structure, body

    def save(self, structure, body):
        (self.batch / "01_source/structure.json").write_text(json.dumps(structure, ensure_ascii=False), encoding="utf-8")
        (self.batch / "01_source/正文.txt").write_text(body, encoding="utf-8")

    def assert_inferred_blocked(self):
        report = structure_gate.run_gate(self.batch)
        self.assertFalse(report["passed"], report)
        self.assertIn("QUESTION_NUMBER_INFERRED", {b["code"] for b in report["blockers"]})
        self.assertFalse(any(n.get("resolution_evidence", {}).get("method") == "deterministic_single_writing_number"
                             for n in report["resolved_evidence"]))
        return report

    def test_unique_final_writing_automatically_binds_current_original_and_source(self):
        structure, _ = self.prepare()
        before = (self.batch / "01_source/structure.json").read_bytes()
        report = structure_gate.run_gate(self.batch)
        self.assertTrue(report["passed"], report["blockers"])
        note = next(n for n in report["resolved_evidence"] if n["code"] == "QUESTION_NUMBER_INFERRED")
        self.assertTrue(note["resolved"])
        self.assertIn("未进行视觉核验", note["resolution"])
        evidence = note["resolution_evidence"]
        self.assertEqual(evidence["source_sha256"], mf.sha256_file(self.batch / "01_source/正文.txt"))
        self.assertEqual(evidence["original_sha256"], mf.sha256_file(self.original))
        self.assertEqual(evidence["original_text_method"], "docx-native")
        self.assertIn("共1小题", " ".join(evidence["evidence"]))
        self.assertNotIn("actor", note)
        self.assertEqual(before, (self.batch / "01_source/structure.json").read_bytes())
        self.assertFalse(structure["unresolved"][0].get("resolved", False))
        confirmed = json.loads((self.batch / "01_source/structure.confirmed.json").read_text())
        self.assertEqual(confirmed["units"][-1]["numbers"], [81])
        self.assertIn("its parks", confirmed["units"][-1]["stem"])
        self.assertEqual(report["resolved_evidence"], structure_gate.run_gate(self.batch)["resolved_evidence"])

    def test_unproven_writing_is_scoped_only_when_other_structure_is_clean(self):
        structure, body = self.prepare(self.paper().replace('2. its parks', '3. its parks'))
        report = self.assert_inferred_blocked()
        self.assertEqual(report['blockers'][0].get('affected_numbers'), [81])
        structure['unresolved'].append({'code': 'DUPLICATE_QUESTION_NUMBER', 'line': 3})
        self.save(structure, body)
        report = self.assert_inferred_blocked()
        self.assertTrue(all('affected_numbers' not in b for b in report['blockers']))

    def test_inferred_writing_scope_rejects_changed_original(self):
        self.prepare()
        self.original_docx(self.paper().replace('37. Complete word 37.', '37. Different question.'))
        report = self.assert_inferred_blocked()
        self.assertNotIn('affected_numbers', report['blockers'][0])

    def test_inferred_writing_scope_rejects_wrong_source_line(self):
        structure, body = self.prepare()
        structure['unresolved'][0]['line'] = 3
        self.save(structure, body)
        report = self.assert_inferred_blocked()
        self.assertNotIn('affected_numbers', report['blockers'][0])

    def test_original_txt_uses_same_proof_without_claiming_visual_review(self):
        self.prepare()
        self.original = self.root / "original.txt"
        self.original.write_text(self.paper(), encoding="utf-8")
        (self.batch / "workflow/manifest.json").write_text(json.dumps({
            "inputs": {"source": {"path": str(self.original)}}}), encoding="utf-8")
        report = structure_gate.run_gate(self.batch)
        self.assertTrue(report["passed"], report["blockers"])
        self.assertEqual(report["resolved_evidence"][0]["resolution_evidence"]["original_text_method"], "original-text")

    def test_real_year_question_counts_toward_writing_number_proof(self):
        self.prepare(self.paper().replace("37. Complete word 37.", "37.2024年我参加安徽省中考。"))
        report = structure_gate.run_gate(self.batch)
        self.assertTrue(report["passed"], report["blockers"])
        note = next(n for n in report["resolved_evidence"] if n["code"] == "QUESTION_NUMBER_INFERRED")
        self.assertEqual(note["resolution_evidence"]["method"], "deterministic_single_writing_number")
        confirmed = json.loads((self.batch / "01_source/structure.confirmed.json").read_text())
        self.assertEqual(next(u for u in confirmed["units"] if u["numbers"] == [37])["stem"],
                         "37.2024年我参加安徽省中考。")

    def test_preceding_numbered_blank_is_independent_source_evidence(self):
        self.prepare(self.paper().replace("21. Complete word 21.", "The answer is __21__."))
        report = structure_gate.run_gate(self.batch)
        self.assertTrue(report["passed"], report["blockers"])

    def test_legacy_candidate_without_issue_still_requires_original_proof(self):
        structure, body = self.prepare()
        structure["unresolved"] = []
        self.save(structure, body)
        report = structure_gate.run_gate(self.batch)
        self.assertTrue(report["passed"], report["blockers"])
        self.assertEqual(report["resolved_evidence"][0]["code"], "QUESTION_NUMBER_INFERRED")
        self.original_docx(self.paper().replace("介绍你家乡", "介绍你的学校"))
        self.assert_inferred_blocked()

    def test_legacy_candidate_without_source_line_cannot_self_prove(self):
        structure, body = self.prepare()
        structure["unresolved"] = []
        structure["sections"][-1].pop("source_line")
        self.save(structure, body)
        self.assert_inferred_blocked()

    def test_writing_content_point_matching_next_number_is_not_printed_number(self):
        text = ("2022年安徽省初中学业水平考试\nⅠ.单词拼写(共1小题)\n1. Complete word.\n"
                "Ⅹ.书面表达(共1小题)\nWrite about your town.\n1. its people\n2. its parks\n")
        structure, _ = self.prepare(text)
        self.assertEqual(structure["sections"][-1]["questions"], [2])
        self.assertTrue(structure["sections"][-1]["numbers_inferred"])
        report = structure_gate.run_gate(self.batch)
        self.assertTrue(report["passed"], report["blockers"])
        confirmed = json.loads((self.batch / "01_source/structure.confirmed.json").read_text())
        self.assertIn("Write about your town", confirmed["units"][-1]["stem"])
        self.assertIn("its parks", confirmed["units"][-1]["stem"])

    def test_explicit_writing_number_at_task_start_is_not_inferred(self):
        text = self.paper().replace("假定你是李辉", "81. 假定你是李辉").replace("1. its people\n2. its parks\n", "")
        structure, _ = self.prepare(text)
        self.assertEqual(structure["sections"][-1]["questions"], [81])
        self.assertNotIn("QUESTION_NUMBER_INFERRED", {x["code"] for x in structure["unresolved"]})
        report = structure_gate.run_gate(self.batch)
        self.assertTrue(report["passed"], report["blockers"])

    def test_explicit_writing_number_preserves_numbered_content_points(self):
        text = self.paper().replace("假定你是李辉", "81. 假定你是李辉")
        structure, _ = self.prepare(text)
        self.assertNotIn("QUESTION_NUMBER_INFERRED", {x["code"] for x in structure["unresolved"]})
        report = structure_gate.run_gate(self.batch)
        self.assertTrue(report["passed"], report["blockers"])

    def test_explicit_writing_with_nonconsecutive_number_stays_blocked(self):
        text = self.paper().replace("假定你是李辉", "81. 假定你是李辉") + "\n82. Another task."
        self.prepare(text)
        report = structure_gate.run_gate(self.batch)
        self.assertFalse(report["passed"])
        self.assertIn("SOURCE_NUMBERS_UNDECLARED", {x["code"] for x in report["blockers"]})

    def test_legacy_candidate_with_no_original_must_block(self):
        structure, body = self.prepare()
        structure["unresolved"] = []
        self.save(structure, body)
        self.original.unlink()
        self.assert_inferred_blocked()

    def test_labeled_writing_lists_can_restart_without_claiming_visual_review(self):
        text = self.paper().replace("1. its people\n2. its parks", "要点：\n1. its people\n2. its parks\n注意：\n1. include details\n2. use 80 words")
        self.prepare(text)
        report = structure_gate.run_gate(self.batch)
        self.assertTrue(report["passed"], report["blockers"])
        note = next(x for x in report["resolved_evidence"] if x["code"] == "QUESTION_NUMBER_INFERRED")
        self.assertIn("未进行视觉核验", note["resolution"])

    def test_unlabeled_or_unrelated_list_restart_remains_blocked(self):
        for label in ("", "另一题：", "注意文字说明"):
            with self.subTest(label=label):
                text = self.paper().replace("2. its parks", "2. its parks\n" + label + "\n1. another item")
                self.prepare(text)
                self.assert_inferred_blocked()

    def test_labeled_restart_does_not_hide_other_exam_numbers(self):
        text = self.paper().replace("2. its parks", "2. its parks\n注意：\n1. use 80 words\n82. another task")
        self.prepare(text)
        self.assert_inferred_blocked()

    def test_two_writing_sections_block(self):
        self.prepare(self.paper() + "\nⅪ.书面表达(共1小题)\nWrite about your school.")
        self.assert_inferred_blocked()

    def test_non_one_or_missing_original_declaration_cannot_be_overridden_by_candidate(self):
        for title in ("共2小题", "共0小题", "写作任务", "共1小题，共2小题"):
            with self.subTest(title=title):
                structure, body = self.prepare(self.paper().replace("共1小题;", title + ";"))
                structure["sections"][-1].update(questions=[81], declared_count=1)
                structure["unresolved"] = [{"code": "QUESTION_NUMBER_INFERRED", "line": structure["sections"][-1]["source_line"]}]
                self.save(structure, body)
                self.assert_inferred_blocked()

    def test_preceding_gap_even_matching_candidate_cannot_self_prove(self):
        self.prepare(self.paper().replace("37. Complete word 37.\n", "").replace("共80小题", "共79小题"))
        self.assert_inferred_blocked()

    def test_non_adjacent_candidate_blocks(self):
        structure, body = self.prepare()
        structure["sections"][-1]["questions"] = [82]
        self.save(structure, body)
        self.assert_inferred_blocked()

    def test_changed_original_not_matching_source_blocks_and_does_not_reuse_note(self):
        self.prepare()
        self.assertTrue(structure_gate.run_gate(self.batch)["passed"])
        self.original_docx(self.paper().replace("37. Complete word 37.\n", ""))
        self.assert_inferred_blocked()

    def test_missing_or_unreadable_original_blocks(self):
        self.prepare()
        self.original.write_bytes(b"not a docx")
        self.assert_inferred_blocked()
        self.original = self.root / "missing.docx"
        (self.batch / "workflow/manifest.json").write_text(json.dumps({
            "inputs": {"source": {"path": str(self.original)}}}), encoding="utf-8")
        self.assert_inferred_blocked()

    def test_empty_or_non_writing_content_blocks(self):
        for content in ("", "Dear Mike,\nYours,\nLi Hui"):
            with self.subTest(content=content):
                self.prepare(self.paper().split("假定")[0] + content)
                self.assert_inferred_blocked()

    def test_extra_exam_number_in_writing_body_blocks(self):
        self.prepare(self.paper() + "\n79. Another writing task.")
        self.assert_inferred_blocked()

    def test_source_change_cannot_be_certified_from_candidate_alone(self):
        structure, body = self.prepare()
        self.save(structure, body.replace("介绍你家乡", "介绍你的学校"))
        self.assert_inferred_blocked()

    def test_writing_not_final_section_blocks(self):
        self.prepare(self.paper() + "\nⅪ.单词拼写(共1小题)\n82. Another word.")
        self.assert_inferred_blocked()

    def test_other_unresolved_or_structural_conflicts_stay_blocking(self):
        for issue in ("DUPLICATE_QUESTION_NUMBER", "QUESTION_NUMBER_INFERRED"):
            with self.subTest(issue=issue):
                structure, body = self.prepare()
                structure["unresolved"].append({"code": issue, "message": "independent uncertainty", "line": 3})
                self.save(structure, body)
                self.assert_inferred_blocked()

    def test_duplicate_or_out_of_order_original_numbers_block(self):
        for text in (self.paper().replace("37. Complete word 37.", "36. Duplicate word."),
                     self.paper().replace("37. Complete word 37.\n38. Complete word 38.",
                                          "38. Complete word 38.\n37. Complete word 37.")):
            with self.subTest(text=text[:80]):
                self.prepare(text)
                self.assert_inferred_blocked()

    def test_explicit_answer_appendix_excluded_from_original_number_proof(self):
        self.prepare(self.paper() + "\nⅠ.单词拼写\n1.A 【解析】example\n2.B 【解析】example\nⅩ.书面表达\n参考范文：Example")
        report = structure_gate.run_gate(self.batch)
        self.assertTrue(report["passed"], report["blockers"])
        self.assertEqual({n["code"] for n in report["resolved_evidence"]},
                         {"QUESTION_NUMBER_INFERRED", "ANSWER_APPENDIX_DETECTED"})

    def test_existing_bound_confirmation_still_supported(self):
        structure, body = self.prepare()
        issue = structure["unresolved"][0]
        issue.update(resolved=True, resolution="原卷依据已记录", resolution_evidence={
            "source_sha256": mf.sha256_file(self.batch / "01_source/正文.txt"),
            "original_sha256": mf.sha256_file(self.original), "evidence": ["原卷编号证据"]})
        self.save(structure, body)
        report = structure_gate.run_gate(self.batch)
        self.assertTrue(report["passed"], report["blockers"])
        self.assertEqual(report["resolved_evidence"][0]["resolution"], "原卷依据已记录")


if __name__ == "__main__":
    unittest.main()
