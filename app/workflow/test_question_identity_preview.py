"""Lesson-local numbers must retain section ownership through the preview API."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import denoise, preview
from workbench import server


class QuestionIdentityPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.batch = self.root / "lesson"
        self.source = self.batch / "01_source"
        self.source.mkdir(parents=True)
        self.lines = [
            "Unit 1 课时1", "Ⅰ.阅读理解", "阅读第一篇材料，完成各题。", "A",
            "First passage about apples.", "[TABLE 1]", "Fruit | Apples", "[/TABLE]",
            "1.Which fruit is red?", "A. apples B. bananas", "2.Where are the apples?", "A. desk B. bag",
            "Ⅱ.阅读理解", "阅读第二篇材料，完成各题。", "B",
            "Second passage about buses.", "[TABLE 2]", "Transport | Bus", "[/TABLE]",
            "1.Which bus is blue?", "A. bus two B. bus three", "2.Where is the bus?", "A. road B. park",
        ]
        self.write(self.source / "正文.txt", "\n".join(self.lines))
        cleaned, _, _ = denoise.clean_body("\n".join(self.lines))
        self.cleaned = cleaned.splitlines()
        self.units = []
        self.items = []
        for index, (title, first, last, stem) in enumerate((
                ("Ⅰ.阅读理解", "阅读第一篇材料，完成各题。", "A. desk B. bag", "1.Which fruit is red?"),
                ("Ⅱ.阅读理解", "阅读第二篇材料，完成各题。", "A. road B. park", "1.Which bus is blue?")), 1):
            uid = f"sec-{index}-u1"
            start, end = self.cleaned.index(first) + 1, self.cleaned.index(last) + 1
            unit = {"unit_id": uid, "identity_version": "scoped-v1", "section_id": f"sec-{index}",
                    "source_section": {"title": title, "ordinal": "Ⅰ" if index == 1 else "Ⅱ",
                                       "occurrence": index, "start_line": start - 1, "end_line": end},
                    "question_ids": {str(n): f"{uid}-q{n}" for n in (1, 2)},
                    "numbers": [1, 2], "unit_kind": "group", "type": "阅读理解", "stem": stem,
                    "source_span": {"basis": "ai_classified", "start_line": start, "end_line": end}}
            self.units.append(unit)
            self.items.append({**unit, "id": uid, "question_type": "阅读理解",
                               "material": f"material {index}", "knowledge": [],
                               "subquestions": [{"number": 1, "stem": stem, "answer": f"answer {index}", "options": []},
                                                {"number": 2, "stem": f"second {index}", "options": []}]})
        self.save_confirmed()
        for name in ("batch.json", "batch_meta.json"):
            self.write(self.batch / "06_batch" / name, {"items": self.items})
        self.reviews = [{"unit_id": self.units[0]["unit_id"], "number": "1", "state": "blocked", "rounds": 1,
                         "open": [{"id": "first-only"}]},
                        {"unit_id": self.units[1]["unit_id"], "number": "1", "state": "ok", "rounds": 2,
                         "open": []}]
        self.write(self.batch / "07_review/report.json", {"rows": self.reviews})

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def save_confirmed(self):
        self.write(self.source / "structure.confirmed.json", {"units": self.units})

    def detail(self, uid):
        with patch.object(server, "WORKFLOWS", self.root):
            return server.detail("lesson", uid)

    def test_scoped_window_keeps_own_material_table_and_source_blocks(self):
        for index, unit in enumerate(self.units):
            with self.subTest(uid=unit["unit_id"]):
                chunk = preview.source_slice(self.batch, [1, 2], self.lines, unit_id=unit["unit_id"])
                self.assertEqual(chunk, self.cleaned[unit["source_span"]["start_line"] - 1:unit["source_span"]["end_line"]])
                blocks = server.source_blocks(chunk)
                table = next(b for b in blocks if b["type"] == "table")
                self.assertEqual(table["rows"], [["Fruit", "Apples"]] if index == 0 else [["Transport", "Bus"]])
                self.assertNotIn("Second passage" if index == 0 else "First passage", "\n".join(chunk))

    def test_number_only_ambiguity_and_unknown_identity_fail_closed(self):
        self.assertEqual(preview.source_slice(self.batch, [1]), [])
        self.assertEqual(preview.source_slice(self.batch, [1, 2]), [])
        self.assertEqual(preview.source_slice(self.batch, [1], unit_id="missing"), [])
        self.write(self.batch / "candidates/1.json", {"unit_id": self.units[0]["unit_id"], "number": "1", "stem": "first-only"})
        invalid = self.detail("1")
        self.assertEqual(invalid["source_preview"], "")
        self.assertEqual(invalid["subquestions"], [])
        self.assertEqual(invalid["item"], {})

    def test_scoped_batch_without_confirmed_binding_never_uses_legacy_first_hit(self):
        self.write(self.source / "structure.confirmed.json", {"units": []})
        self.assertEqual(preview.source_slice(self.batch, [1]), [])
        self.assertEqual(preview.source_slice(self.batch, [1], unit_id=self.units[1]["unit_id"]), [])
        self.assertEqual(self.detail(self.units[1]["unit_id"])["source_preview"], "")

    def test_span_uses_cleaned_coordinates_and_proves_stem_in_window(self):
        # Add a removable header: the confirmed coordinates are cleaned-body coordinates.
        raw = ["2022年安徽省初中学业水平考试", *self.lines]
        self.write(self.source / "正文.txt", "\n".join(raw))
        for basis in ("ai_classified", "cleaned_source"):
            self.units[1]["source_span"]["basis"] = basis
            self.units[1]["stem"] = "1.Which bus\n is blue?"
            self.save_confirmed()
            self.assertIn("Second passage about buses.", "\n".join(
                preview.source_slice(self.batch, [1], unit_id=self.units[1]["unit_id"])))
        self.units[1]["stem"] = self.units[0]["stem"]
        self.save_confirmed()
        self.assertEqual(preview.source_slice(self.batch, [1], unit_id=self.units[1]["unit_id"]), [])
        self.units[1]["source_span"]["start_line"] = 9999
        self.save_confirmed()
        self.assertEqual(preview.source_slice(self.batch, [1], unit_id=self.units[1]["unit_id"]), [])

    def test_detail_preserves_roman_titles_and_filters_candidate_state_findings(self):
        for index, unit in enumerate(self.units, 1):
            self.write(self.batch / "candidates" / f"draft-{index}.json",
                       {"unit_id": unit["unit_id"], "number": "1", "stem": f"draft {index}", "answer": f"draft answer {index}"})
        self.write(self.batch / "07_review/manual-review-queue.json", {"items": [
            {"id": f"finding-{index}", "unit_id": unit["unit_id"], "number": "1", "field": "answer"}
            for index, unit in enumerate(self.units, 1)]})
        before = {p: p.read_bytes() for p in (self.source / "正文.txt", self.batch / "06_batch/batch.json")}
        for index, unit in enumerate(self.units, 1):
            result = self.detail(unit["unit_id"])
            self.assertEqual(result["source_preview"].splitlines()[0], unit["source_section"]["title"])
            self.assertNotIn(self.units[2 - index]["source_section"]["title"], result["source_preview"])
            self.assertEqual(result["subquestions"][0]["stem"], f"draft {index}")
            self.assertEqual(result["subquestions"][0]["answer"], f"draft answer {index}")
            self.assertEqual(result["subquestions"][0]["state"], "blocked" if index == 1 else "ok")
            self.assertEqual([f["id"] for f in result["findings"]], [f"finding-{index}"])
            self.assertEqual(sum(b["type"] == "table" for b in result["preview_blocks"]), 1)
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_batch_rows_review_ownership_and_source_order(self):
        rows = preview.batch_rows(self.batch, self.reviews)
        self.assertEqual([r["unit_id"] for r in rows], [u["unit_id"] for u in self.units])
        self.assertEqual([r["source_section"]["title"] for r in rows], ["Ⅰ.阅读理解", "Ⅱ.阅读理解"])
        self.assertEqual([r["state"] for r in rows], ["blocked", "ok"])
        self.assertEqual(rows[1]["open"], [])
        self.assertEqual(rows[1]["rounds"], 2)

    def test_scoped_save_only_changes_selected_owner_and_rejects_alias(self):
        for index, unit in enumerate(self.units, 1):
            self.write(self.batch / "candidates" / f"draft-{index}.json",
                       {"unit_id": unit["unit_id"], "number": "1", "stem": f"draft {index}"})
        first = self.batch / "candidates/draft-1.json"
        before = first.read_bytes()
        server.Handler.save_units(self.batch, self.units[1]["unit_id"], [{"number": 1, "stem": "changed second"}])
        self.assertEqual(first.read_bytes(), before)
        self.assertEqual(json.loads((self.batch / "candidates/draft-2.json").read_text())["stem"], "changed second")
        server.Handler.save_units(self.batch, self.units[1]["unit_id"], [{"number": 2, "answer": "B"}])
        created = self.batch / "candidates" / f"{self.units[1]['question_ids']['2']}.json"
        self.assertEqual(json.loads(created.read_text())["unit_id"], self.units[1]["unit_id"])
        files = {p: p.read_bytes() for p in (self.batch / "candidates").glob("*.json")}
        for uid, number in (("1", 1), ("missing", 1), (self.units[1]["unit_id"], 3)):
            with self.subTest(uid=uid, number=number), self.assertRaises(ValueError):
                server.Handler.save_units(self.batch, uid, [{"number": number, "stem": "wrong"}], unit={"material": "wrong"})
        self.assertEqual({p: p.read_bytes() for p in (self.batch / "candidates").glob("*.json")}, files)

    def test_unprinted_question_uses_original_title_without_fake_question_one(self):
        unit = dict(self.units[0], unit_kind="standalone", type="书面表达", numbers=[1],
                    stem="Write about your family.")
        unit["source_section"] = dict(unit["source_section"], title="Ⅲ.书面表达")
        unit["source_span"] = {"basis": "cleaned_source", "start_line": 2, "end_line": 2,
                               "printed_numbers": []}
        self.units = [unit]
        self.save_confirmed()
        self.write(self.source / "正文.txt", "Ⅲ.书面表达\nWrite about your family.")
        item = dict(unit, id=unit["unit_id"], subquestions=[], question_type="书面表达")
        for name in ("batch.json", "batch_meta.json"):
            self.write(self.batch / "06_batch" / name, {"items": [item]})
        rows = preview.batch_rows(self.batch, [])
        self.assertEqual(rows[0]["label"], "Ⅲ.书面表达")
        self.assertEqual(rows[0]["numbers"], [1])
        result = self.detail(unit["unit_id"])
        self.assertEqual(result["label"], "Ⅲ.书面表达")
        self.assertEqual(result["source_preview"], "Ⅲ.书面表达\nWrite about your family.")
        self.assertEqual(result["subquestions"][0]["number"], 1)
        self.assertIsNone(result["subquestions"][0]["display_number"])
        self.assertEqual(result["subquestions"][0]["internal_number"], 1)

    def test_legacy_global_number_preview_and_review_behavior_remains(self):
        self.write(self.source / "structure.confirmed.json", {"units": []})
        lines = ["Ⅰ.单项选择", "1.First question?", "A. a B. b", "2.Second question?", "A. c B. d"]
        items = [{"id": "q-2", "numbers": [2], "stem": "second"},
                 {"id": "q-1", "numbers": [1], "stem": "first"}]
        self.write(self.batch / "06_batch/batch.json", {"items": items})
        self.assertEqual(preview.source_slice(self.batch, [1], lines, unit_id="q-1"), lines[1:3])
        rows = preview.batch_rows(self.batch, [{"number": "1", "state": "ok"}, {"number": "2", "state": "attention"}])
        self.assertEqual([r["unit_id"] for r in rows], ["q-1", "q-2"])
        self.assertEqual([r["state"] for r in rows], ["ok", "attention"])


if __name__ == "__main__":
    unittest.main()
