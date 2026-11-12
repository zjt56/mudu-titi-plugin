"""Original-number ranges, source-proven aliases and transport compatibility."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from workflow import upload_numbering, upload_payload
from workflow.test_upload_payload import batch, question, reading_group, catalog_snapshot


class UploadNumberingTest(unittest.TestCase):
    def project(self, *items, root=None):
        return upload_numbering.project_batch({"items": list(items)}, root)

    def codes(self, report):
        return {e["code"] for e in report["errors"]}

    def source(self, text):
        from workflow import denoise, extract, structure_gate
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        structure, text, _ = extract.infer_structure(text)
        cleaned = denoise.clean_body(text)[0]
        units = structure_gate._build_units(structure["sections"], structure["groups"], cleaned.splitlines())
        for unit in units:
            unit.setdefault("unit_id", ("q-" if unit["unit_kind"] == "standalone" else "group-") + "-".join(map(str, unit["numbers"])))
        self.write(root, "01_source/正文.txt", text)
        self.write(root, "01_source/structure.json", structure)
        self.write(root, "01_source/structure.confirmed.json", {"units": units})
        return root, units

    def write(self, root, relative, value):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def test_local_contract_derives_ranges_and_preserves_deep_copy_and_order(self):
        a, g, b = question(), reading_group(), question()
        a.update(id="later", number=81, numbers=[81])
        b.update(id="earlier", number=21, numbers=[21])
        g["subquestions"].reverse()
        data = {"items": [a, g, b]}
        before = deepcopy(data)
        report = upload_numbering.project_batch(data)
        self.assertEqual(report["errors"], [])
        self.assertEqual([(r["numberStart"], r["numberEnd"]) for r in report["items"]], [(81, 81), (56, 58), (21, 21)])
        self.assertEqual([r["id"] for r in report["items"]], [r["id"] for r in before["items"]])
        self.assertEqual(report["items"][1]["subquestions"], g["subquestions"])
        report["items"][0]["options"].clear()
        self.assertEqual(data, before)

    def test_number_alone_is_supported_for_legacy_standalone(self):
        q = question(); q.pop("numbers")
        self.assertEqual(self.project(q)["items"][0]["numberEnd"], 81)

    def test_type_location_and_empty_flags_never_prove_unnumbered(self):
        for changes in ({"question_type": "书面表达"}, {"location": "第1题"},
                        {"printed_numbers": []}, {"original_number": None, "printed_number": False}):
            q = question(); q.update(changes)
            report = self.project(q)
            self.assertEqual(report["errors"], [])
            self.assertEqual(report["items"][0]["numberStart"], 81)

    def test_explicit_range_types_pair_shape_and_standalone_equality(self):
        for changes in ({"numberStart": 81}, {"numberEnd": 81},
                        {"numberStart": True, "numberEnd": True}, {"numberStart": 81.0, "numberEnd": 81.0},
                        {"numberStart": "81", "numberEnd": "81"}, {"numberStart": 0, "numberEnd": 0},
                        {"numberStart": -1, "numberEnd": -1}, {"numberStart": 81, "numberEnd": None},
                        {"numberStart": 81, "numberEnd": 82}, {"numberStart": 82, "numberEnd": 81}):
            with self.subTest(changes=changes):
                q = question(); q.update(changes)
                self.assertIn("PAPER_NUMBER_RANGE_INVALID", self.codes(self.project(q)))

    def test_explicit_range_must_match_local_numbers_including_null(self):
        for bounds in ((1, 1), (None, None)):
            q = question(); q.update(numberStart=bounds[0], numberEnd=bounds[1])
            self.assertIn("PAPER_NUMBER_SOURCE_CONFLICT", self.codes(self.project(q)))
        q = question(); q.update(numberStart=81, numberEnd=81)
        self.assertEqual(self.project(q)["errors"], [])

    def test_group_requires_contiguous_complete_unique_child_numbers(self):
        for change in ("gap", "duplicate", "missing", "mismatch", "bool"):
            g = reading_group()
            if change == "gap":
                g["numbers"] = [56, 58, 59]
                for child, number in zip(g["subquestions"], g["numbers"]): child["number"] = number
            elif change == "duplicate": g["subquestions"][1]["number"] = 56
            elif change == "missing": g["subquestions"].pop()
            elif change == "mismatch": g["numbers"] = [56, 57, 59]
            else: g["subquestions"][0]["number"] = True
            report = self.project(g)
            with self.subTest(change=change):
                self.assertTrue(report["errors"])
                if change == "gap": self.assertIn("PAPER_NUMBER_GROUP_NONCONTIGUOUS", self.codes(report))

    def test_scoped_cross_section_repeated_numbers_remain_in_input_order(self):
        rows = []
        for uid, sid in (("uB", "sB"), ("uA", "sA")):
            q = question()
            q.update(id=uid, unit_id=uid, identity_version="scoped-v1", section_id=sid, number=1, numbers=[1],
                     question_id=uid + "q", question_ids={"1": uid + "q"},
                     source_section={"title": "Ⅰ. 单项填空", "ordinal": "Ⅰ", "context": sid, "occurrence": 1})
            rows.append(q)
        data = batch(rows[0]); data["items"] = rows
        report = upload_payload.build_payload(data, catalog_snapshot())
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual([(r["id"], r["numberStart"]) for r in report["payload"]["items"]], [("uB", 1), ("uA", 1)])

    def test_current_confirmed_writing_binding_reconnects_missing_local_marker(self):
        root, units = self.source("Ⅸ.单词拼写\n80.题目\nⅩ.书面表达（共1小题；满分20分）\n请写一篇英语短文。")
        unit = next(u for u in units if (u.get("source_span") or {}).get("number_binding") == "single_writing_section")
        q = question(); q.update(unit_id=unit["unit_id"], number=unit["numbers"][0], numbers=unit["numbers"])
        report = self.project(q, root=root)
        self.assertEqual(report["errors"], [])
        self.assertIsNone(report["items"][0]["numberStart"])
        self.assertIsNone(report["items"][0]["numberEnd"])
        self.assertEqual(report["items"][0]["number"], q["number"])
        self.assertEqual(report["evidence"]["units"][0]["state"], "proven-unnumbered")
        self.assertIn("01_source/正文.txt", report["evidence"]["files"])
        q.update(numberStart=q["number"], numberEnd=q["number"])
        self.assertIn("PAPER_NUMBER_SOURCE_CONFLICT", self.codes(self.project(q, root=root)))

    def test_unverified_or_stale_marker_cannot_hide_alias_with_null(self):
        q = question(); q.update(source_span={"number_binding": "single_writing_section"}, numberStart=None, numberEnd=None)
        self.assertIn("PAPER_NUMBER_EVIDENCE_REQUIRED", self.codes(self.project(q)))
        root, units = self.source("Ⅸ.单词拼写\n80.题目\nⅩ.书面表达（共1小题；满分20分）\n请写一篇英语短文。")
        unit = units[-1]
        q.update(unit_id=unit["unit_id"], number=unit["numbers"][0], numbers=unit["numbers"])
        q.pop("source_span")
        self.write(root, "01_source/正文.txt", "Ⅸ.单词拼写\n80.题目\nⅩ.书面表达（共1小题；满分20分）\n原来的题目已经改变。")
        self.assertIn("PAPER_NUMBER_EVIDENCE_REQUIRED", self.codes(self.project(q, root=root)))

    def test_duplicate_confirmed_identity_cannot_prove_null(self):
        root, units = self.source("Ⅸ.单词拼写\n80.题目\nⅩ.书面表达（共1小题；满分20分）\n请写一篇英语短文。")
        unit = units[-1]
        self.write(root, "01_source/structure.confirmed.json", {"units": [unit, unit]})
        q = question(); q.update(unit_id=unit["unit_id"], number=unit["numbers"][0], numbers=unit["numbers"])
        self.assertIn("PAPER_NUMBER_EVIDENCE_REQUIRED", self.codes(self.project(q, root=root)))

    def test_unnumbered_group_preserves_children_with_separate_contract_diagnostic(self):
        from workflow.test_phonetic_structure import PhoneticStructureTest
        fixture = PhoneticStructureTest(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        row = deepcopy(fixture.rows[0])
        before = deepcopy(row)
        report = self.project(row, root=fixture.root)
        self.assertEqual(report["errors"], [])
        self.assertIsNone(report["items"][0]["numberStart"])
        self.assertEqual(report["items"][0]["subquestions"], before["subquestions"])
        self.assertEqual(report["evidence"]["diagnostics"][0]["code"], "UNNUMBERED_SUBQUESTION_CONTRACT_UNRESOLVED")
        self.assertEqual(row, before)

    def test_proven_unnumbered_wire_does_not_emit_top_level_alias(self):
        root, units = self.source("Ⅸ.单词拼写\n80.题目\nⅩ.书面表达（共1小题；满分20分）\n请写一篇英语短文。")
        unit = units[-1]
        q = question(); q.update(unit_id=unit["unit_id"], number=81, numbers=[81], numberStart=None, numberEnd=None)
        report = upload_payload.build_payload(batch(q), catalog_snapshot(), batch_dir=root)
        self.assertTrue(report["passed"], report["errors"])
        wire = report["payload"]["items"][0]
        self.assertIsNone(wire["numberStart"])
        self.assertIsNone(wire["numberEnd"])
        self.assertNotIn("number", wire)
        self.assertNotIn("numbers", wire)
        self.assertEqual(wire["location"], q["location"])
        q["source_span"] = dict(unit["source_span"], start_line=1)
        self.assertIn("PAPER_NUMBER_SOURCE_CONFLICT", self.codes(self.project(q, root=root)))

    def test_confirmed_legacy_digit_strings_still_bind_unnumbered_evidence(self):
        root, units = self.source("Ⅸ.单词拼写\n80.题目\nⅩ.书面表达（共1小题；满分20分）\n请写一篇英语短文。")
        unit = deepcopy(units[-1]); unit.pop("unit_id"); unit["numbers"] = ["81"]
        self.write(root, "01_source/structure.confirmed.json", {"units": [unit]})
        q = question(); q.update(unit_id="q-81")
        report = self.project(q, root=root)
        self.assertEqual(report["errors"], [])
        self.assertIsNone(report["items"][0]["numberStart"])

    def test_wire_ranges_only_on_top_level_and_group_children_keep_order(self):
        g = reading_group(); g.update(numberStart=56, numberEnd=58)
        g["subquestions"].reverse()
        for child in g["subquestions"]: child.update(numberStart=1, numberEnd=2)
        data = batch(g); before = deepcopy(data)
        report = upload_payload.build_payload(data, catalog_snapshot())
        self.assertTrue(report["passed"], report["errors"])
        wire = report["payload"]["items"][0]
        self.assertEqual((wire["numberStart"], wire["numberEnd"]), (56, 58))
        self.assertEqual([c["number"] for c in wire["subquestions"]], [58, 57, 56])
        self.assertTrue(all("numberStart" not in c and "numberEnd" not in c for c in wire["subquestions"]))
        self.assertEqual(data, before)


if __name__ == "__main__":
    unittest.main()
