"""Native table blanks require current scoped source proof in both A9 consumers."""
import copy
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import (agent_tasks, assemble_local, assemble_precheck, batch_fill, denoise, extract, question_content,
                      question_draft, source_objects, structure_gate, table_structure)


class TableBlankStructureTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="table-blank-proof-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.table_text = ("About Mia<br>Her friend is 6.<u>　</u> years old and is 7.<u>　</u>.<br>"
                           "Her 8.<u>　</u> are fun.<br>Her favourite 9.<u>　</u> is a cat.<br>"
                           "She has 10.<u>　</u> caps.")
        self.marker = "[TABLE 2]\n" + self.table_text + "\n[/TABLE]"
        text = ("Starter Unit 3 Welcome!\n第四课时 Project\n任务型阅读\nMia introduces her friends.\n"
                + "\n".join(f"{n}.<u>　</u> This is paragraph {n}." for n in range(1, 6))
                + "\n任务一:选出主旨大意。\nA.Friend　B.Teacher　C.Farm　D.Cat　E.Colour　F.Other\n"
                + "任务二:补全下面的表格,每空一词。\n" + self.marker)
        structure, body, _ = extract.infer_structure(text)
        body = denoise.clean_body(body)[0]
        units = structure_gate._build_units(structure["sections"], structure["groups"], body.splitlines())
        self.assertEqual(len(units), 1)
        self.unit = units[0]
        self.assertEqual([int(n) for n in self.unit["numbers"]], list(range(1, 11)))
        table = {"type": "table", "number": 2, "text": self.marker,
                 "grid": [[self.table_text]], "rows": [{"cells": [
                     {"text": self.table_text.replace("<br>", "\n"), "rowspan": 1, "colspan": 1}]}]}
        extraction = {"blocks": [table], "tables": [table]}
        source_objects.attach(extraction, "a" * 64)
        self.write("01_source/正文.txt", body)
        self.write("01_source/structure.json", structure)
        self.write("01_source/structure.confirmed.json", {"identity_version": "scoped-v1", "units": units})
        self.write("01_source/extraction.json", extraction)
        draft = question_draft.build(self.root)
        self.row = copy.deepcopy(draft["items"][0])
        self.row.update(question_type="任务型阅读", knowledge=["语篇体裁/说明文", "主题/人与自我"])
        self.row["filterMetadata"]["difficulty"] = "容易"
        for child in self.row["subquestions"]:
            child["answer"] = "synthetic"
        self.write("06_batch/batch.json", {"items": [self.row]})

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def verified(self):
        return table_structure.verified_groups(self.root / "06_batch/batch.json")

    def script(self, name):
        path = Path(__file__).parents[1] / "scripts/06-upload" / (name + ".py")
        spec = importlib.util.spec_from_file_location("table_blanks_" + name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_complete_precheck_preserves_all_content_and_per_blank_source_proof(self):
        verified = self.verified()
        self.assertEqual(question_content.table_blank_stem_numbers(self.row, verified), set(range(6, 11)))
        proof = verified[self.row["unit_id"]]
        for n in range(6, 11):
            evidence = proof["blanks"][n]
            self.assertEqual(evidence["table_number"], 2)
            self.assertTrue(evidence["source_ref"].startswith("src-"))
            self.assertIn(f"{n}.<u>", evidence["marker"])
            self.assertEqual(evidence["source_line"], self.unit["source_span"]["end_line"] - 1)
        before = copy.deepcopy(self.row)
        hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob("*") if p.is_file()}
        result = assemble_precheck.precheck({"items": [self.row]}, batch_dir=self.root)
        self.assertTrue(result["passed"], result)
        self.assertEqual(self.row, before)
        self.assertEqual(hashes, {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob("*") if p.is_file()})
        self.assertTrue(all(not c["stem"] for c in self.row["subquestions"][5:]))
        self.assertIn("10.______", self.row["materialBlocks"][0]["rows"][0]["cells"][0]["text"])

    def test_real_managed_merge_and_accept_result_precheck_accept_only_br_representation(self):
        uid = self.unit["unit_id"]
        # Start the real managed lifecycle before a batch exists (setUp's standalone
        # precheck candidate is synthetic and owned by this TemporaryDirectory).
        target = (self.root / "06_batch/batch.json").resolve()
        self.assertEqual(target.parent, self.root / "06_batch")
        target.unlink()
        batch_fill.initialize(self.root)
        child_labels = [{"number": n, "knowledge": [], "knowledgeIds": []} for n in range(1, 11)]
        artifacts = {
            "types": {"items": [{"unit_id": uid, "type": "任务型阅读", "subtypes": ["阅读综合"],
                                  "words": 40, "contentComplete": True, "evidence": ["Synthetic source"]}]},
            "knowledge": {"items": [{"unit_id": uid,
                "knowledge": ["语篇体裁/说明文/介绍类短文", "主题/人与自我/生活与学习/日常活动"],
                "knowledgeIds": [], "subquestions": child_labels, "evidence": ["Synthetic source"]}]},
            "explanations": {"items": [{"unit_id": uid, "explanation": "无", "articleSummary": "无", "evidence": ["Synthetic source"],
                "subquestions": [{"number": n, "explanation": "无", "evidence": ["Synthetic source"]} for n in range(1, 11)]}]},
            "answers": {"answers": {qid: "synthetic" for qid in self.unit["question_ids"].values()},
                        "difficulty": {qid: "容易" for qid in self.unit["question_ids"].values()}}
        }
        for stage, artifact in artifacts.items():
            self.write(agent_tasks.ARTIFACTS[stage], artifact)
            batch_fill.sync(self.root, stage, artifact)
        checks, candidates = [], []
        original = assemble_precheck.precheck

        def capture(payload, *args, **kwargs):
            candidates.append(copy.deepcopy(payload))
            result = original(payload, *args, **kwargs)
            checks.append(result)
            return result

        output = io.StringIO()
        with patch.object(assemble_precheck, "precheck", side_effect=capture), redirect_stdout(output):
            code = assemble_local.try_assemble(self.root)
        self.assertEqual(code, 0, output.getvalue())
        self.assertEqual(len(checks), 2)  # validate_only and actual accepted import
        self.assertTrue(all(r["passed"] for r in checks), checks)
        merged = candidates[0]["items"][0]
        self.assertTrue(any("<br>" in str(o) for o in merged["subquestions"][4]["options"]))
        self.assertEqual(question_content.table_blank_stem_numbers(merged, self.verified()), set(range(6, 11)))
        original_blocks = copy.deepcopy(merged["materialBlocks"])
        for transform in (lambda s: s.replace("Other", "Altered"),
                          lambda s: s.split("任务二:")[0],
                          lambda s: s.split("[TABLE 2]")[0]):
            broken = copy.deepcopy(merged)
            options = broken["subquestions"][4]["options"]
            for option in options:
                if isinstance(option, dict):
                    option["text"] = transform(option["text"])
            self.assertEqual(question_content.table_blank_stem_numbers(broken, self.verified()), set())
            self.assertEqual(broken["materialBlocks"], original_blocks)
            result = original({"items": [broken]}, batch_dir=self.root)
            self.assertFalse(result["passed"], result)

    def test_bare_data_does_not_grant_stem_exception(self):
        self.assertEqual(question_content.table_blank_stem_numbers(self.row), set())
        result = assemble_precheck.precheck({"items": [self.row]})
        self.assertEqual(sum("[A9]" in p for p in result["problems"]), 5)

    def test_missing_table_middle_blank_text_or_scope_and_identity_still_reject(self):
        verified = self.verified()
        variants = []
        for key, value in (("materialBlocks", []), ("material", "Changed"), ("stem", "Invented"),
                           ("section_id", "other"), ("numbers", list(range(1, 10))),
                           ("source_section", {}), ("question_ids", {})):
            row = copy.deepcopy(self.row)
            row[key] = value
            variants.append(row)
        row = copy.deepcopy(self.row)
        row["materialBlocks"][0]["rows"][0]["cells"][0]["text"] = self.table_text.replace("8.<u>　</u>", "")
        variants.append(row)
        row = copy.deepcopy(self.row)
        row["subquestions"][0]["stem"] = ""
        variants.append(row)
        row = copy.deepcopy(self.row)
        row["subquestions"][5]["question_id"] = "wrong-scope"
        variants.append(row)
        row = copy.deepcopy(self.row)
        row["subquestions"][6]["number"] = 6
        variants.append(row)
        row = copy.deepcopy(self.row)
        row["subquestions"].pop(7)
        variants.append(row)
        for malformed in ([123], [{"key": "A"}], [{"key": "A", "text": 123}], "A. unexpected"):
            row = copy.deepcopy(self.row)
            row["subquestions"][5]["options"] = malformed
            variants.append(row)
        row = copy.deepcopy(self.row)
        row["subquestions"][4]["options"].reverse()
        variants.append(row)
        row = copy.deepcopy(self.row)
        row["materialBlocks"][0]["rows"][0]["cells"][0]["text"] = row["materialBlocks"][0]["rows"][0]["cells"][0]["text"].replace("are fun", "are altered")
        variants.append(row)
        for row in variants:
            with self.subTest(row=row):
                self.assertEqual(question_content.table_blank_stem_numbers(row, verified), set())
                problems = self.script("check_structure")._check_unit(row, table_verified=verified)
                self.assertTrue(any("[A9]" in p for p in problems), problems)

    def test_answer_and_material_requirement_remain_independent(self):
        row = copy.deepcopy(self.row)
        row["subquestions"][7]["answer"] = ""
        problems = self.script("check_structure")._check_unit(row, table_verified=self.verified())
        self.assertTrue(any("answer 为空" in p for p in problems), problems)
        row["material"] = ""
        problems = self.script("check_structure")._check_unit(row, table_verified=self.verified())
        self.assertTrue(any("[A8]" in p for p in problems), problems)

    def test_current_source_or_native_object_change_invalidates_proof(self):
        source = self.root / "01_source/正文.txt"
        original = source.read_text()
        source.write_text(original.replace("8.<u>　</u>", ""))
        self.assertEqual(self.verified(), {})
        source.write_text(original)
        extraction = json.loads((self.root / "01_source/extraction.json").read_text())
        extraction["tables"][0]["rows"][0]["cells"][0]["text"] += " altered"
        self.write("01_source/extraction.json", extraction)
        self.assertEqual(self.verified(), {})

    def test_duplicate_native_table_and_wrong_source_question_line_cannot_grant_proof(self):
        confirmed_path = self.root / "01_source/structure.confirmed.json"
        confirmed = json.loads(confirmed_path.read_text())
        altered = copy.deepcopy(confirmed)
        altered["units"][0]["question_ids"]["8"] = self.unit["section_id"] + "-l4-q8"
        self.write("01_source/structure.confirmed.json", altered)
        # A single incorrect source identity invalidates this group's unresolved blank.
        self.assertEqual(self.verified(), {})
        self.write("01_source/structure.confirmed.json", confirmed)
        source = self.root / "01_source/正文.txt"
        body = source.read_text() + "\n" + self.marker
        source.write_text(body)
        duplicate = copy.deepcopy(confirmed)
        unit = duplicate["units"][0]
        unit["stem"] += "\n" + self.marker
        unit["source_span"]["end_line"] = len(body.splitlines())
        unit["source_section"]["end_line"] = len(body.splitlines())
        self.write("01_source/structure.confirmed.json", duplicate)
        self.assertEqual(self.verified(), {})

    def test_publish_consumer_uses_same_proof_and_rejects_bare_or_changed_data(self):
        target = self.root / "06_batch/batch.json"
        output = io.StringIO()
        with redirect_stdout(output):
            code = self.script("publish_check").main(["--batch", str(target)])
        self.assertEqual(code, 0, output.getvalue())
        row = copy.deepcopy(self.row)
        row["materialBlocks"] = []
        self.write("06_batch/batch.json", {"items": [row]})
        output = io.StringIO()
        with redirect_stdout(output):
            code = self.script("publish_check").main(["--batch", str(target)])
        self.assertNotEqual(code, 0)
        self.assertIn("A9", output.getvalue())
        bare = self.root / "bare.json"
        bare.write_text(json.dumps({"items": [self.row]}))
        # Different root has no source proof, even when the candidate embeds IDs.
        with tempfile.TemporaryDirectory(prefix="bare-table-") as temp:
            other = Path(temp) / "batch.json"
            other.write_bytes(bare.read_bytes())
            output = io.StringIO()
            with redirect_stdout(output):
                code = self.script("publish_check").main(["--batch", str(other)])
            self.assertNotEqual(code, 0)
            self.assertIn("A9", output.getvalue())


if __name__ == "__main__":
    unittest.main()
