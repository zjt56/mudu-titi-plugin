"""Guarded artifact reuse: only presentation changes; everything else refuses."""
import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from workflow import contracts, manifest as mf, redeliver


class RedeliverTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="redeliver-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.artifact = self.root / "03_types/types.json"
        self.artifact.parent.mkdir(parents=True)
        self.artifact.write_text(json.dumps({"items": [{"unit_id": "q-1", "type": "单项填空"}]}), encoding="utf-8")
        manifest = mf.new_manifest("batch", self.root, {}, {})
        mf.touch_stage(manifest, "types", mf.INVALIDATED, notes="消费内容已变化")
        manifest["stages"]["types"]["input_hashes"] = {
            "artifact:01_source/structure.confirmed.json": "a",
            "artifact:06_batch/batch.base.json": "old",
            "material:roles": "b",
        }
        manifest["stages"]["types"]["output_hashes"] = {
            "03_types/types.json": mf.sha256_file(self.artifact)}
        mf.save(self.root, manifest)

    def run_redeliver(self, inputs=None, bindings=None, validate=None):
        inputs = inputs if inputs is not None else {
            "artifact:01_source/structure.confirmed.json": "a",
            "artifact:06_batch/batch.base.json": "new",
            "material:roles": "b",
        }
        bindings = [] if bindings is None else bindings
        validate = validate or (lambda *args, **kwargs: None)
        with patch.object(redeliver.mf, "input_hashes", return_value=inputs), \
             patch.object(redeliver, "_binding_mismatches", return_value=bindings), \
             patch.object(redeliver.agent_tasks, "_collect", return_value=({}, {}, [], [])), \
             patch.object(redeliver.agent_tasks, "_validate", side_effect=validate), \
             patch.object(redeliver, "_agent_lock", side_effect=lambda *a, **k: nullcontext()), \
             patch.object(redeliver.mf, "record_delivery"):
            return redeliver.redeliver(self.root, "types")

    def status(self):
        return mf.load(self.root)["stages"]["types"]["status"]

    def test_presentation_only_change_is_revalidated_and_reaccepted(self):
        result = self.run_redeliver()
        self.assertEqual(result["action"], "redelivered")
        self.assertEqual(self.status(), mf.COMPLETED)
        ledger = (self.root / "workflow/redeliver-log.jsonl").read_text(encoding="utf-8")
        self.assertIn("presentation-only", ledger)

    def test_any_raw_input_change_refuses_reuse(self):
        with self.assertRaises(ValueError) as caught:
            self.run_redeliver(inputs={
                "artifact:01_source/structure.confirmed.json": "changed",
                "artifact:06_batch/batch.base.json": "new",
                "material:roles": "b",
            })
        self.assertIn("非表现层输入已变化", str(caught.exception))
        self.assertEqual(self.status(), mf.INVALIDATED)

    def test_changed_word_binding_refuses_reuse(self):
        with self.assertRaises(ValueError) as caught:
            self.run_redeliver(bindings=["q-1:source"])
        self.assertIn("来源绑定", str(caught.exception))
        self.assertEqual(self.status(), mf.INVALIDATED)

    def test_artifact_revalidated_against_current_inputs(self):
        def reject(*args, **kwargs):
            raise ValueError("Explanation not found in supplied source material")
        with self.assertRaises(ValueError):
            self.run_redeliver(validate=reject)
        self.assertEqual(self.status(), mf.INVALIDATED)

    def test_edited_artifact_after_delivery_refuses_reuse(self):
        self.artifact.write_text(json.dumps({"items": []}), encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            self.run_redeliver()
        self.assertIn("产物在交付后被改动", str(caught.exception))

    def test_terminal_stage_is_left_alone(self):
        manifest = mf.load(self.root)
        mf.touch_stage(manifest, "types", mf.COMPLETED)
        mf.save(self.root, manifest)
        result = redeliver.redeliver(self.root, "types")
        self.assertEqual(result["action"], "unchanged")

    def test_non_fill_stage_is_refused(self):
        with self.assertRaises(ValueError):
            redeliver.redeliver(self.root, "assemble")


if __name__ == "__main__":
    unittest.main()
