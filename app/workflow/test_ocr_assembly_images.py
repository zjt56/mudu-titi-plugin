"""Regression coverage for Word-backed image ownership during assembly."""
import json
import tempfile
import unittest
from pathlib import Path

from workflow.question_content import restore_source_fidelity


class OcrAssemblyImagesTest(unittest.TestCase):
    def make_source(self, body, units, ocr_text=""):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        source = root / "01_source"
        source.mkdir()
        (source / "正文.txt").write_text(body, encoding="utf-8")
        # The deterministic candidate intentionally has no units: assembly must
        # honor the confirmed grouping, whose spans refer to the Word text.
        (source / "structure.json").write_text('{"sections": [], "groups": []}', encoding="utf-8")
        (source / "structure.confirmed.json").write_text(
            json.dumps({"units": units}, ensure_ascii=False), encoding="utf-8")
        (source / "pdf-ocr.json").write_text(
            json.dumps({"documents": [{"page": 1, "text": ocr_text}]}), encoding="utf-8")
        return root

    @staticmethod
    def unit(numbers, stem, start, end):
        return {"unit_kind": "group" if len(numbers) > 1 else "standalone",
                "type": "阅读理解", "numbers": numbers, "stem": stem,
                "source_span": {"start_line": start, "end_line": end, "basis": "ai_classified"}}

    @staticmethod
    def group(numbers):
        return {"numbers": numbers, "material": "", "subquestions": [
            {"number": number, "stem": f"Question {number}"} for number in numbers]}

    def test_confirmed_ai_group_restores_each_word_image_to_its_child(self):
        body = ("Shared passage. [IMG shared.png]\n"
                "6.First question? [IMG first.png]\n"
                "7.Second question?\nA.[IMG second.png] B.No\n")
        root = self.make_source(body, [self.unit([6, 7], body.strip(), 1, 4)])
        row = self.group([6, 7])
        # A sibling and the parent cannot satisfy the first child's evidence.
        row["material"] = "[IMG first.png]"
        row["subquestions"][1]["stem"] += " [IMG first.png]"
        restore_source_fidelity(root, {"items": [row]})
        first, second = row["subquestions"]
        self.assertIn("[IMG first.png]", first["stem"])
        self.assertNotIn("[IMG second.png]", first["stem"])
        self.assertIn("[IMG second.png]", second["stem"])
        self.assertIn("[IMG shared.png]", row["material"])
        # Child-specific pictures must not be added to shared material.
        self.assertNotIn("[IMG second.png]", row["material"])

    def test_overlapping_groups_keep_images_with_their_own_child(self):
        first = "1.First? [IMG one.png]\n2.Second? [IMG two-a.png]"
        second = "2.Another second? [IMG two-b.png]\n3.Third? [IMG three.png]"
        root = self.make_source(first + "\n" + second + "\n", [
            self.unit([1, 2], first, 1, 2), self.unit([2, 3], second, 3, 4)])
        rows = [self.group([1, 2]), self.group([2, 3])]
        restore_source_fidelity(root, {"items": rows})
        self.assertIn("[IMG two-a.png]", rows[0]["subquestions"][1]["stem"])
        self.assertNotIn("[IMG two-b.png]", rows[0]["subquestions"][1]["stem"])
        self.assertIn("[IMG two-b.png]", rows[1]["subquestions"][0]["stem"])
        self.assertNotIn("[IMG two-a.png]", rows[1]["subquestions"][0]["stem"])

    def test_repeated_number_without_unit_identity_does_not_guess_section(self):
        first = "1.First section? [IMG first.png]"
        second = "1.Second section? [IMG second.png]"
        root = self.make_source(first + "\n" + second + "\n", [
            self.unit([1], first, 1, 1), self.unit([1], second, 2, 2)])
        rows = [{"number": 1, "stem": "First section?"},
                {"number": 1, "stem": "Second section?"}]
        restore_source_fidelity(root, {"items": rows})
        self.assertNotIn("[IMG second.png]", str(rows[0]))
        self.assertNotIn("[IMG first.png]", str(rows[1]))

    def test_ocr_only_image_in_confirmed_stem_is_not_word_evidence(self):
        word = "6.First? [IMG word.png]\n7.Second?\n"
        suggested = "6.First? [IMG word.png]\n7.Second? [IMG ocr-only.png]"
        root = self.make_source(word, [self.unit([6, 7], suggested, 1, 2)], suggested)
        row = self.group([6, 7])
        restore_source_fidelity(root, {"items": [row]})
        self.assertIn("[IMG word.png]", row["subquestions"][0]["stem"])
        self.assertNotIn("[IMG ocr-only.png]", str(row))


if __name__ == "__main__":
    unittest.main()
