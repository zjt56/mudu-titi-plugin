# -*- coding: utf-8 -*-
"""Contract metadata keeps optional/deferred work explicit without weakening gates."""
import unittest

from workflow import contracts


class ContractMetadataTest(unittest.TestCase):
    def test_optional_and_external_wait_paths_are_declared(self):
        self.assertIn("missing_optional_input", contracts.defer_reasons("answers"))
        self.assertIn("external_wait", contracts.defer_reasons("audio"))
        self.assertIn("parallel", contracts.optimization_hints("knowledge"))

    def test_format_is_late_bound_and_not_an_optional_content_stage(self):
        metadata = contracts.stage_metadata("format_gate")
        self.assertIn("late_bound", metadata["optimization"])
        self.assertIn("external_wait", metadata["defer"])
        self.assertFalse(metadata["optional"])
        self.assertEqual(contracts.STAGE_BY_ID["format_gate"].depends_on, ("review_gate",))
        self.assertEqual(contracts.STAGE_BY_ID["meta"].command[2], "{content_snapshot}")

    def test_deferred_metadata_does_not_change_hard_dependencies(self):
        for stage in contracts.STAGES:
            for dependency in stage.depends_on:
                self.assertIn(dependency, contracts.STAGE_BY_ID)
        self.assertEqual(contracts.STAGE_BY_ID["assemble"].depends_on,
                         ("preassemble", "answers", "types", "knowledge", "explanations", "underlines"))
        self.assertEqual(contracts.STAGE_BY_ID["content_snapshot"].depends_on, ("preassemble",))
        self.assertEqual(contracts.STAGE_BY_ID["meta"].depends_on, ("content_snapshot",))
        self.assertEqual(contracts.STAGE_BY_ID["underlines"].depends_on, ("meta",))
        self.assertEqual(contracts.STAGE_BY_ID["assemble"].depends_on,
                         ("preassemble", "answers", "types", "knowledge", "explanations", "underlines"))
        self.assertEqual(contracts.STAGE_BY_ID["validate"].depends_on, ("assemble",))
        self.assertIn("audio", contracts.STAGE_BY_ID["review"].depends_on)

    def test_unknown_stage_metadata_is_empty(self):
        self.assertEqual(contracts.stage_metadata("missing"), {})
        self.assertEqual(contracts.defer_reasons("missing"), ())
        self.assertEqual(contracts.optimization_hints("missing"), ())

    def test_invalidation_follows_consumers_without_joining_independent_branches(self):
        self.assertEqual(contracts.downstream_of("extract"), contracts.STAGE_ORDER[1:])
        self.assertEqual(contracts.downstream_of("audio"),
                         ("review", "review_gate", "format_gate", "upload_gate", "upload", "publish_gate"))
        downstream = contracts.downstream_of("types")
        for consumer in ("answers", "knowledge", "audio", "assemble", "validate", "review"):
            self.assertIn(consumer, downstream)
        for independent in ("content_snapshot", "meta", "underlines"):
            self.assertNotIn(independent, downstream)


if __name__ == "__main__":
    unittest.main()
