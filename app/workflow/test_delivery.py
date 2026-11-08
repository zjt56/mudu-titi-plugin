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

    def test_unqualified_delivery_is_distinct_from_completed_delivery(self):
        target = self.batch / "07_review/validation-report.json"
        target.parent.mkdir(parents=True)
        target.write_text('{"passed": false}\n', encoding="utf-8")
        stage = contracts.STAGE_BY_ID["validate"]
        mf.touch_stage(self.manifest, "validate", mf.BLOCKED,
                       execution_completed=True, continue_unqualified=True,
                       quality_status="failed", release_ready=False,
                       output_hashes=mf.output_hashes(self.batch, stage))
        mf.record_delivery(self.batch, self.manifest, stage)
        self.assertEqual(mf.delivery_status(self.batch, "validate"), "delivered_unqualified")

    def test_review_binds_direct_inputs(self):
        for relative in ("01_source/正文.txt", "06_batch/batch_meta.json"):
            path = self.batch / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x\n", encoding="utf-8")
        hashes = mf.input_hashes(self.batch, self.manifest, contracts.STAGE_BY_ID["review"])
        self.assertIn("direct:01_source/正文.txt", hashes)
        self.assertIn("direct:06_batch/batch_meta.json", hashes)

    def test_meta_and_underlines_keep_independent_stable_outputs(self):
        from workflow import runner
        folder = self.batch / '06_batch'
        folder.mkdir(parents=True)
        (folder / 'batch.json').write_text(json.dumps({'items': [{'id': 'q-1', 'stem': 'What is this?'}]}), encoding='utf-8')
        meta = contracts.STAGE_BY_ID['meta']
        underlines = contracts.STAGE_BY_ID['underlines']
        self.assertEqual(runner._execute_command(self.batch, self.manifest, meta), 0)
        self.assertEqual(runner._execute_command(self.batch, self.manifest, underlines), 0)
        state = mf.load(self.batch)
        self.assertFalse(mf.is_stale(self.batch, state, meta)[0])
        self.assertFalse(mf.is_stale(self.batch, state, underlines)[0])
        self.assertEqual(mf.reconcile_deliveries(self.batch, state), [])
        self.assertTrue((folder / 'batch_meta.base.json').is_file())
        self.assertTrue((folder / 'batch_meta.json').is_file())
        final = json.loads((folder / 'batch_meta.json').read_text(encoding='utf-8'))
        base = json.loads((folder / 'batch_meta.base.json').read_text(encoding='utf-8'))
        self.assertEqual(final['items'][0]['filterMetadata'], base['items'][0]['filterMetadata'])

    def test_legacy_shared_meta_is_rebuilt_not_silently_accepted(self):
        folder = self.batch / '06_batch'
        folder.mkdir(parents=True)
        final = folder / 'batch_meta.json'
        final.write_text('{"items": []}', encoding='utf-8')
        mf.touch_stage(self.manifest, 'meta', mf.COMPLETED,
                       output_hashes={'06_batch/batch_meta.json': mf.sha256_file(final)})
        mf.touch_stage(self.manifest, 'underlines', mf.SKIPPED)
        invalidated = mf.reconcile_deliveries(self.batch, self.manifest)
        self.assertIn('meta', invalidated)
        self.assertEqual(self.manifest['stages']['meta']['status'], mf.INVALIDATED)
        self.assertEqual(self.manifest['stages']['underlines']['status'], mf.SKIPPED)
        self.assertFalse(mf.dependency_satisfied(self.manifest, 'underlines'))
        self.assertTrue(final.is_file())

    def test_invalidated_candidate_cannot_satisfy_dependency(self):
        mf.touch_stage(self.manifest, "validate", mf.BLOCKED,
                       execution_completed=True, continue_unqualified=True, quality_status="failed")
        # An unqualified (needs_review/blocked) result is not a usable delivery.
        self.assertFalse(mf.dependency_satisfied(self.manifest, "validate", allow_unqualified=True))
        mf.invalidate_downstream(self.batch, self.manifest, "validate", "input changed")
        self.assertFalse(mf.dependency_satisfied(self.manifest, "validate", allow_unqualified=True))
        mf.touch_stage(self.manifest, "validate", mf.COMPLETED)
        self.assertNotIn("quality_status", self.manifest["stages"]["validate"])

    def test_review_detects_changed_direct_input(self):
        source = self.batch / "01_source/正文.txt"
        source.parent.mkdir(parents=True)
        source.write_text("original", encoding="utf-8")
        stage = contracts.STAGE_BY_ID["review"]
        for rel in stage.produces:
            path = self.batch / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
        mf.touch_stage(self.manifest, "review", mf.COMPLETED,
                       input_hashes=mf.input_hashes(self.batch, self.manifest, stage),
                       output_hashes=mf.output_hashes(self.batch, stage))
        self.assertFalse(mf.is_stale(self.batch, self.manifest, stage)[0])
        source.write_text("changed", encoding="utf-8")
        # 新规则：内容变化不再自动使已完成阶段失效（用户后续验证），is_stale 只判完成+产物在。
        self.assertFalse(mf.is_stale(self.batch, self.manifest, stage)[0])

    def test_delivery_records_consumers(self):
        target = self.batch / "03_types/types.json"
        target.parent.mkdir(parents=True)
        target.write_text('{"items": []}\n', encoding="utf-8")
        stage = contracts.STAGE_BY_ID["types"]
        mf.touch_stage(self.manifest, "types", mf.COMPLETED)
        mf.record_delivery(self.batch, self.manifest, stage)
        mf.mark_delivery_consumed(self.batch, "assemble")
        data = json.loads(mf.delivery_path(self.batch).read_text(encoding="utf-8"))
        self.assertEqual(data["stages"]["types"]["status"], "delivered")
        self.assertIn("assemble", data["stages"]["types"]["consumed_by"])


if __name__ == "__main__":
    unittest.main()
