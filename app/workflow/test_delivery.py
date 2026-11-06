# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
from pathlib import Path

from workflow import contracts, manifest as mf


class DeliveryChecklistTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name) / "batch"
        self.manifest = mf.new_manifest("batch", self.batch, {}, {})
        mf.save(self.batch, self.manifest)

    def test_legacy_completed_outputs_are_migrated_without_rerun(self):
        target = self.batch / "03_types/types.json"
        target.parent.mkdir(parents=True)
        target.write_text('{"items": []}\n', encoding="utf-8")
        mf.touch_stage(self.manifest, "types", mf.COMPLETED)
        mf.save(self.batch, self.manifest)

        invalidated = mf.reconcile_deliveries(self.batch, self.manifest)
        self.assertEqual(invalidated, [])
        self.assertEqual(mf.delivery_status(self.batch, "types"), "delivered")
        self.assertEqual(self.manifest["stages"]["types"]["status"], mf.COMPLETED)
        self.assertEqual(self.manifest["stages"]["types"]["output_hashes"]["03_types/types.json"],
                         mf.sha256_file(target))

    def test_missing_completed_output_invalidates_only_descendants(self):
        for relative in contracts.STAGE_BY_ID["structure_gate"].produces:
            path = self.batch / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n", encoding="utf-8")
        mf.touch_stage(self.manifest, "structure_gate", mf.COMPLETED,
                       output_hashes=mf.output_hashes(self.batch, contracts.STAGE_BY_ID["structure_gate"]))
        mf.touch_stage(self.manifest, "types", mf.COMPLETED,
                       output_hashes={"03_types/types.json": "missing"})
        mf.touch_stage(self.manifest, "knowledge", mf.COMPLETED)
        mf.touch_stage(self.manifest, "assemble", mf.COMPLETED)
        mf.save(self.batch, self.manifest)

        invalidated = mf.reconcile_deliveries(self.batch, self.manifest)
        self.assertIn("types", invalidated)
        self.assertEqual(self.manifest["stages"]["structure_gate"]["status"], mf.COMPLETED)
        self.assertEqual(self.manifest["stages"]["types"]["status"], mf.INVALIDATED)
        self.assertEqual(self.manifest["stages"]["knowledge"]["status"], mf.INVALIDATED)
        self.assertEqual(self.manifest["stages"]["assemble"]["status"], mf.INVALIDATED)

    def test_delivery_records_consumers(self):
        target = self.batch / "03_types/types.json"
        target.parent.mkdir(parents=True)
        target.write_text('{"items": []}\n', encoding="utf-8")
        stage = contracts.STAGE_BY_ID["types"]
        mf.touch_stage(self.manifest, "types", mf.COMPLETED)
        mf.record_delivery(self.batch, self.manifest, stage)
        mf.mark_delivery_consumed(self.batch, "explanations")
        data = json.loads(mf.delivery_path(self.batch).read_text(encoding="utf-8"))
        self.assertEqual(data["stages"]["types"]["status"], "delivered")
        self.assertIn("explanations", data["stages"]["types"]["consumed_by"])


if __name__ == "__main__":
    unittest.main()
