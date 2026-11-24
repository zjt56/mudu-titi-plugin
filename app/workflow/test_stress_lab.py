"""Adversarial checks for the stress harness's own assertions."""
import copy
import unittest
from workflow import stress_lab


class StressOracleTest(unittest.TestCase):
    def payload(self):
        return {"items": [{"unit_id": "q-1", "question_type": "单项填空", "stem": "Do not run.",
                           "answer": "A", "explanation": "无", "knowledge": ["known"],
                           "options": ["A. stop", "B. run"], "material": "all source words",
                           "attachments": [], "subquestions": []}]}

    def test_unchanged_and_exact_repair_are_accepted(self):
        original = self.payload()
        injected = copy.deepcopy(original)
        injected["items"][0]["options"] = ["A. stop"]
        self.assertTrue(stress_lab._data_preserved(original, injected, injected=injected))
        self.assertTrue(stress_lab._data_preserved(original, original, injected=injected))

    def test_repair_cannot_introduce_third_content_even_in_damaged_field(self):
        original = self.payload()
        injected = copy.deepcopy(original)
        injected["items"][0]["options"] = ["A. stop"]
        wrong = copy.deepcopy(original)
        wrong["items"][0]["options"] = ["A. invented", "B. invented"]
        self.assertFalse(stress_lab._data_preserved(original, wrong, injected=injected))

    def test_every_protected_field_detects_unrelated_damage(self):
        original = self.payload()
        for key in ("answer", "explanation", "knowledge", "options", "stem", "material", "subquestions", "attachments"):
            with self.subTest(key=key):
                wrong = copy.deepcopy(original)
                wrong["items"][0][key] = "invented"
                self.assertFalse(stress_lab._data_preserved(original, wrong, injected=original))

    def test_material_block_images_and_extensions_are_protected(self):
        original = self.payload()
        original["items"][0]["materialBlocks"] = [{"type": "image", "src": "original.png"}]
        wrong = copy.deepcopy(original)
        wrong["items"][0]["materialBlocks"] = []
        self.assertFalse(stress_lab._data_preserved(original, wrong, injected=original))

    def test_missing_final_cannot_be_called_preserved_when_not_injected(self):
        self.assertFalse(stress_lab._data_preserved(self.payload(), None, injected=self.payload()))

    def test_item_removal_and_identity_change_are_detected(self):
        self.assertFalse(stress_lab._data_preserved(self.payload(), {"items": []}, injected=self.payload()))
        wrong = self.payload()
        wrong["items"][0]["unit_id"] = "foreign"
        self.assertFalse(stress_lab._data_preserved(self.payload(), wrong, injected=self.payload()))

    def test_build_fixture_supports_exact_listening_knowledge_and_bound_structure_evidence(self):
        import tempfile
        from pathlib import Path
        from workflow import format_gate
        self.assertEqual(stress_lab.valid_knowledge("听力关键词语选择", [], "standalone"), ["语音/基本读音"])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "synth"
            fixture = stress_lab.build_fixture(root, 912, standalone_count=2)
            self.assertTrue(fixture.units)
            (root / "07_review/upload-format.json").unlink(missing_ok=True)
            report = format_gate.run(root)
            self.assertTrue(report["passed"], report.get("errors"))


if __name__ == "__main__":
    unittest.main()
