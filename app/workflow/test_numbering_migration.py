"""Safe local migration of derived original-number ranges."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import numbering_migration, upload_numbering


class NumberingMigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.batch = self.root / "batch"
        (self.batch / "workflow").mkdir(parents=True)
        (self.batch / "06_batch").mkdir()

    def write(self, relative, value):
        path = self.batch / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
                        encoding="utf-8")
        return path

    def source(self):
        from workflow import denoise, extract, structure_gate
        text = "Ⅸ.单词拼写\n80.题目\nⅩ.书面表达（共1小题；满分20分）\n请写一篇英语短文。"
        structure, text, _ = extract.infer_structure(text)
        cleaned = denoise.clean_body(text)[0]
        units = structure_gate._build_units(structure["sections"], structure["groups"], cleaned.splitlines())
        for unit in units:
            unit.setdefault("unit_id", ("q-" if unit["unit_kind"] == "standalone" else "group-")
                            + "-".join(map(str, unit["numbers"])))
        self.write("01_source/正文.txt", text)
        self.write("01_source/structure.json", structure)
        self.write("01_source/structure.confirmed.json", {"units": units})
        return units

    def payload(self, units):
        rows = []
        for unit in units:
            row = {"id": unit["unit_id"], "unit_id": unit["unit_id"], "unit_kind": unit["unit_kind"],
                   "number": unit["numbers"][0], "numbers": unit["numbers"],
                   "stem": "题干", "answer": "A", "explanation": "无"}
            rows.append(row)
        return {"items": rows}

    def manifest(self, **stages):
        data = {"workflow_id": "batch", "stages": {}}
        for key, value in stages.items():
            data["stages"][key] = value
        self.write("workflow/manifest.json", data)

    def test_apply_adds_ranges_backs_up_and_is_idempotent(self):
        units = self.source()
        payload = self.payload(units)
        for relative in ("06_batch/batch.json", "06_batch/batch_meta.base.json", "06_batch/batch_meta.json"):
            self.write(relative, payload)
        self.write("06_batch/batch.base.json", payload)
        self.manifest(assemble={"status": "completed"})
        before_base = (self.batch / "06_batch/batch.base.json").read_bytes()

        plan = numbering_migration.plan_batch(self.batch)
        self.assertEqual(plan["status"], "planned", plan)
        self.assertTrue(all(row["changed"] for row in plan["artifacts"]))

        result = numbering_migration.apply_batch(self.batch, stamp="20260101T000000Z")
        self.assertEqual(result["status"], "applied", result)
        self.assertTrue((self.batch / result["backup"] / "batch.json").is_file())
        self.assertTrue((self.batch / "workflow/numbering-migration.json").is_file())
        self.assertEqual((self.batch / "06_batch/batch.base.json").read_bytes(), before_base)

        writing = [row for row in self.payload(units)["items"]
                   if row["unit_kind"] == "standalone" and len(row["numbers"]) == 1][-1]
        rows = json.loads((self.batch / "06_batch/batch_meta.json").read_text())["items"]
        by_id = {row["id"]: row for row in rows}
        self.assertEqual((by_id[writing["id"]]["numberStart"], by_id[writing["id"]]["numberEnd"]),
                         (None, None))
        self.assertIn("source_span", json.loads(
            (self.batch / "01_source/structure.confirmed.json").read_text())["units"][-1])
        for row in rows:
            if row["id"] != writing["id"]:
                self.assertEqual(row["numberStart"], row["number"])
        self.assertTrue(all(item["ranges_present"] for item in result["verification"]))

        again = numbering_migration.apply_batch(self.batch, stamp="20260101T000000Z")
        self.assertEqual(again["status"], "unchanged")

    def test_conflicting_explicit_range_blocks_without_writing(self):
        units = self.source()
        payload = self.payload(units)
        payload["items"][0].update(numberStart=99, numberEnd=99)
        path = self.write("06_batch/batch_meta.json", payload)
        self.manifest(assemble={"status": "completed"})
        before = path.read_bytes()
        plan = numbering_migration.plan_batch(self.batch)
        self.assertEqual(plan["status"], "blocked")
        self.assertEqual({error["code"] for error in plan["errors"]}, {"PAPER_NUMBER_SOURCE_CONFLICT"})
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((self.batch / "workflow/numbering-migration").exists())

    def test_approved_upload_final_bytes_stay_frozen(self):
        units = self.source()
        payload = self.payload(units)
        for relative in ("06_batch/batch.json", "06_batch/batch_meta.base.json", "06_batch/batch_meta.json"):
            self.write(relative, payload)
        final = (self.batch / "06_batch/batch_meta.json").read_bytes()
        self.manifest(upload_gate={"status": "approved", "approved_batch_sha256": "abc"},
                      upload={"status": "completed"})
        result = numbering_migration.apply_batch(self.batch, stamp="20260101T000000Z")
        self.assertEqual(result["status"], "applied", result)
        self.assertTrue(result["approved_final_frozen"])
        self.assertEqual((self.batch / "06_batch/batch_meta.json").read_bytes(), final)
        self.assertNotEqual((self.batch / "06_batch/batch.json").read_bytes(), final)

        forced = numbering_migration.apply_batch(self.batch, include_approved_final=True,
                                                 stamp="20260101T000001Z")
        self.assertEqual(forced["status"], "applied", forced)
        self.assertFalse(forced["approved_final_frozen"])
        self.assertIn("numberStart", json.loads(
            (self.batch / "06_batch/batch_meta.json").read_text())["items"][0])

    def test_live_batch_lock_is_skipped_without_writes(self):
        units = self.source()
        payload = self.payload(units)
        path = self.write("06_batch/batch.json", payload)
        before = path.read_bytes()
        with patch.object(numbering_migration, "plan_batch",
                          wraps=numbering_migration.plan_batch):
            from workflow import supervisor
            with patch.object(supervisor, "_try_file_lock") as lock:
                lock.return_value.__enter__.return_value = False
                lock.return_value.__exit__.return_value = False
                result = numbering_migration.apply_batch(self.batch)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(path.read_bytes(), before)

    def test_projection_is_read_only_for_source_evidence(self):
        units = self.source()
        payload = self.payload(units)
        confirmed = (self.batch / "01_source/structure.confirmed.json").read_bytes()
        text = (self.batch / "01_source/正文.txt").read_bytes()
        report = upload_numbering.project_batch(payload, self.batch)
        self.assertEqual(report["errors"], [])
        self.assertEqual((self.batch / "01_source/structure.confirmed.json").read_bytes(), confirmed)
        self.assertEqual((self.batch / "01_source/正文.txt").read_bytes(), text)
        self.assertTrue(report["evidence"]["files"])

    def test_content_digest_ignores_only_top_level_ranges(self):
        from workflow import manifest as mf
        path = self.write("06_batch/batch_meta.json", {"items": [{"number": 1, "stem": "题干"}]})
        before = mf._content_digest(path)
        self.write("06_batch/batch_meta.json", {"items": [{"number": 1, "stem": "题干",
                                                           "numberStart": 1, "numberEnd": 1}]})
        self.assertEqual(before, mf._content_digest(path))
        # A nested evidence range is substantive, never projected away.
        evidence = self.write("07_review/review-gate.json", {"evidence": {"numberStart": 1, "numberEnd": 2}})
        first = mf._content_digest(evidence)
        self.write("07_review/review-gate.json", {"evidence": {"numberStart": 1, "numberEnd": 3}})
        self.assertNotEqual(first, mf._content_digest(evidence))
        # Question content changes still reopen exact content digests.
        self.write("06_batch/batch_meta.json", {"items": [{"number": 1, "stem": "改过的题干",
                                                           "numberStart": 1, "numberEnd": 1}]})
        self.assertNotEqual(before, mf._content_digest(path))


if __name__ == "__main__":
    unittest.main()
