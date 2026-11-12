"""Synthetic whole-section source proofs; no real batches, answers, or model calls."""
import copy
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import (denoise, extract, phonetic_structure, preview, question_content,
                      question_draft, structure_gate, upload_payload)
from workflow.assemble_precheck import precheck


class NativeUnnumberedStructureTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="native-unnumbered-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.song = "A B __ D, sing with me.\nE F __ H, clap with me."
        text = ("Starter Unit 1 Hello!\n第二课时 Section A (3a-3d)\n"
                "Ⅰ.根据所缺字母补全下面的字母歌\n" + self.song + "\n"
                "Ⅱ.[情境题]根据图片提示补全单词\n[IMG words.png]\n"
                "Ⅲ.阅读理解\nRead the passage and choose the best answer.\n"
                "Mia has a blue bag. She walks to class.\n"
                "1. What does Mia have? A. A bag. B. A kite. C. A cup. D. A toy.\n"
                "2. Where does Mia go? A. Home. B. Class. C. The park. D. A shop.")
        structure, extracted, _ = extract.infer_structure(text)
        self.text = denoise.clean_body(extracted)[0]
        self.units = structure_gate._build_units(structure["sections"], structure["groups"],
                                                self.text.splitlines())
        self.write("01_source/正文.txt", self.text)
        self.write("01_source/structure.json", structure)
        self.save_units(self.units)
        self.image = self.root / "01_source/media/words.png"
        self.image.parent.mkdir(parents=True, exist_ok=True)
        self.image.write_bytes(b"synthetic-nonempty-image-evidence")
        self.draft = question_draft.build(self.root)
        self.rows = copy.deepcopy(self.draft["items"])
        for row, unit in zip(self.rows, self.units):
            row["question_type"] = unit["type"]
            row["difficulty"] = "容易"
            row.setdefault("filterMetadata", {})["difficulty"] = "容易"
            row["attachments"] = [dict(a, url="/source-media/" + self.root.name + "/" + a["name"])
                                  for a in row.get("attachments") or []]
            for child in row["subquestions"]:
                child["answer"] = "A"  # Synthetic filled field, not a claimed original answer.
        self.write("06_batch/batch.json", {"items": self.rows})

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
                        encoding="utf-8")

    def save_units(self, units):
        self.write("01_source/structure.confirmed.json", {"identity_version": "scoped-v1", "units": units})

    def verified(self):
        return phonetic_structure.verified_groups(self.root / "06_batch/batch.json")

    def script(self, name):
        path = Path(__file__).parents[1] / "scripts/06-upload" / (name + ".py")
        spec = importlib.util.spec_from_file_location("native_unnumbered_" + name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def publish_output(self, rows, with_source=True):
        if with_source:
            target = self.root / "06_batch/batch.json"
            target.write_text(json.dumps({"items": rows}, ensure_ascii=False), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                code = self.script("publish_check").main(["--batch", str(target)])
            return code, output.getvalue()
        with tempfile.TemporaryDirectory(prefix="bare-native-") as temporary:
            target = Path(temporary) / "batch.json"
            target.write_text(json.dumps({"items": rows}, ensure_ascii=False), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                code = self.script("publish_check").main(["--batch", str(target)])
            return code, output.getvalue()

    def test_inferred_whole_sections_preserve_exact_song_heading_and_native_picture(self):
        self.assertEqual(len(self.units), 3)
        self.assertEqual([u["type"] for u in self.units], ["语音题", "单词拼写", "阅读理解"])
        self.assertEqual(self.draft["issues"], [])
        for unit in self.units[:2]:
            span = unit["source_span"]
            self.assertEqual(unit["stem"], "\n".join(self.text.splitlines()[span["start_line"] - 1:span["end_line"]]))
            self.assertEqual(unit["source_section"]["title"], self.text.splitlines()[unit["source_section"]["start_line"] - 1])
            self.assertEqual(span["number_binding"], "unnumbered_section")
            self.assertEqual(span["printed_numbers"], [])
            self.assertEqual(unit["numbers"], [1])
        self.assertEqual(question_content.legitimate_source_shape(self.units[0]), "unnumbered_no_image")
        self.assertEqual(question_content.legitimate_source_shape(self.units[1]), "image_only")
        self.assertEqual(self.rows[0]["material"], self.song)
        self.assertEqual(self.rows[0]["stem"], self.units[0]["source_section"]["title"])
        self.assertEqual(self.rows[1]["stem"], self.units[1]["source_section"]["title"])
        self.assertEqual(self.rows[1]["material"], "")
        self.assertEqual(self.rows[1]["attachments"][0]["name"], "words.png")
        self.assertEqual(self.rows[1]["attachments"][0]["placement"], "question")

    def test_material_song_and_nonphonetic_image_alias_require_current_batch_proof(self):
        verified = self.verified()
        self.assertEqual(set(verified), {u["unit_id"] for u in self.units[:2]})
        for row in self.rows[:2]:
            self.assertEqual(phonetic_structure.shape(row, verified), (True, True))
            self.assertEqual(phonetic_structure.shape(row), (False, False))
        self.assertEqual(self.rows[1]["question_type"], "单词拼写")
        self.assertEqual(verified[self.rows[1]["unit_id"]]["_source_question_type"], "单词拼写")

    def test_forged_body_heading_span_child_or_missing_image_and_marker_cannot_use_alias(self):
        verified = self.verified()
        variants = []
        for index, key, value in ((0, "material", self.song + " invented"),
                                  (0, "stem", "编造导语"), (1, "attachments", [])):
            row = copy.deepcopy(self.rows[index]); row[key] = value; variants.append((key, row))
        row = copy.deepcopy(self.rows[0]); row["source_section"]["title"] = "Ⅱ.邻节导语"; variants.append(("title", row))
        row = copy.deepcopy(self.rows[0]); row["source_span"]["end_line"] -= 1; variants.append(("span", row))
        row = copy.deepcopy(self.rows[0]); row["subquestions"][0]["question_id"] = self.rows[2]["subquestions"][0]["question_id"]; variants.append(("foreign child", row))
        row = copy.deepcopy(self.rows[1]); row["stem"] = row["stem"].replace("[情境题]", ""); variants.append(("missing marker", row))
        for label, row in variants:
            with self.subTest(label=label):
                self.assertEqual(phonetic_structure.shape(row, verified), (False, False))
                problems = self.script("check_structure")._check_unit(row, verified)
                self.assertTrue(any("[A8]" in p or "[A9]" in p for p in problems), problems)
                codes = {e["code"] for e in upload_payload.build_payload({"items": [row]}, batch_dir=self.root)["errors"]}
                self.assertTrue(codes.intersection({"GROUP_INCOMPLETE", "MISSING_STEM"}), codes)

    def test_confirmed_source_proof_rejects_wrong_body_title_partial_or_cross_section_window(self):
        for mutation in ("body", "title", "partial", "cross_section", "section_start"):
            units = copy.deepcopy(self.units)
            song = units[0]
            if mutation == "body":
                song["stem"] += " forged"
            elif mutation == "title":
                song["source_section"]["title"] = units[1]["source_section"]["title"]
            elif mutation == "partial":
                song["source_span"]["end_line"] -= 1
                song["stem"] = self.song.splitlines()[0]
            elif mutation == "cross_section":
                song["source_span"]["end_line"] = units[1]["source_span"]["end_line"]
                song["source_section"]["end_line"] = units[1]["source_section"]["end_line"]
            else:
                song["source_section"]["start_line"] += 1
            self.save_units(units)
            with self.subTest(mutation=mutation):
                self.assertNotIn(song["unit_id"], self.verified())
        self.save_units(self.units)
        self.assertIn(self.units[0]["unit_id"], self.verified())

    def test_missing_media_or_current_source_marker_invalidates_proof(self):
        self.image.unlink()
        self.assertNotIn(self.units[1]["unit_id"], self.verified())
        self.image.write_bytes(b"")
        self.assertNotIn(self.units[1]["unit_id"], self.verified())
        self.image.write_bytes(b"restored-synthetic-source")
        self.assertIn(self.units[1]["unit_id"], self.verified())
        self.write("01_source/正文.txt", self.text.replace("[情境题]", ""))
        self.assertNotIn(self.units[1]["unit_id"], self.verified())

    def test_local_publish_precheck_and_payload_share_current_proof_without_catalog_relaxation(self):
        verified = self.verified()
        checker = self.script("check_structure")
        for row in self.rows:
            problems = checker._check_unit(row, verified)
            self.assertFalse(any("[A8]" in p or "[A9]" in p for p in problems), problems)
        code, output = self.publish_output(self.rows)
        self.assertEqual(code, 0, output)
        with patch("workflow.assemble_precheck.SCRIPTS", ("check_structure.py",)):
            checked = precheck({"items": self.rows}, batch_dir=self.root)
            self.assertFalse(any("[A8]" in p or "[A9]" in p for p in checked["problems"]), checked)
            # C15/catalog completeness is outside this source-shape regression.
            bare = precheck({"items": self.rows})
            self.assertFalse(bare["passed"])
            self.assertTrue(any("[A8]" in p or "[A9]" in p for p in bare["problems"]))
        for context in (None, self.root):
            errors = upload_payload.build_payload({"items": self.rows}, batch_dir=context)["errors"]
            codes = {e["code"] for e in errors}
            if context is None:
                self.assertTrue(codes.intersection({"GROUP_INCOMPLETE", "MISSING_STEM"}), codes)
            else:
                self.assertFalse(codes.intersection({"GROUP_INCOMPLETE", "MISSING_STEM"}), errors)
        code, output = self.publish_output(self.rows, with_source=False)
        self.assertNotEqual(code, 0)
        self.assertTrue("A8" in output or "A9" in output, output)

    def test_preview_blocks_and_upload_preserve_song_picture_and_original_fill_instruction(self):
        from workbench.server import source_blocks
        payload = upload_payload.build_payload({"items": self.rows}, batch_dir=self.root)["payload"]["items"]
        for index in (0, 1):
            unit = self.units[index]
            chunk = preview.source_slice(self.root, unit["numbers"], unit_id=unit["unit_id"])
            self.assertEqual(chunk[0], unit["source_section"]["title"])
            blocks = json.dumps(source_blocks(chunk), ensure_ascii=False)
            wire = payload[index]
            self.assertEqual(wire["stem"], unit["source_section"]["title"])
            self.assertIn(unit["source_section"]["title"], blocks)
            if index == 0:
                self.assertEqual(wire["material"], self.song)
                for line in self.song.splitlines():
                    self.assertIn(line, blocks)
            else:
                self.assertIn("[情境题]", wire["stem"])
                self.assertIn("words.png", blocks)
                self.assertEqual(wire["attachments"][0]["name"], "words.png")
                self.assertEqual(wire["attachments"][0]["placement"], "question")
        self.assertNotIn("source_span", payload[0])
        self.assertNotIn("printed_numbers", payload[1])

    def test_source_audit_detects_lost_song_marker_or_picture_and_answers_remain_required(self):
        self.assertEqual(question_content.audit_batch_source(self.root, {"items": self.rows})["issues"], [])
        for index, field, replacement, expected in (
                (0, "material", "", "SOURCE_CONTENT_MISSING"),
                (1, "stem", self.rows[1]["stem"].replace("[情境题]", ""), "SOURCE_SHARED_RUBRIC_MISSING"),
                (1, "attachments", [], "SOURCE_IMAGE_MISSING")):
            rows = copy.deepcopy(self.rows); rows[index][field] = replacement
            issues = question_content.audit_batch_source(self.root, {"items": rows})["issues"]
            codes = {issue["code"] for issue in issues}
            with self.subTest(field=field):
                if field == "material":
                    self.assertTrue(issues)
                else:
                    self.assertIn(expected, codes)
        row = copy.deepcopy(self.rows[0]); row["subquestions"][0]["answer"] = ""
        self.assertTrue(any("[A9]" in p for p in self.script("check_structure")._check_unit(row, self.verified())))

    def test_ordinary_numbered_reading_group_does_not_gain_general_empty_body_exemption(self):
        reading = copy.deepcopy(self.rows[2])
        self.assertNotIn(reading["unit_id"], self.verified())
        reading["material"] = ""
        reading["subquestions"][0]["stem"] = ""
        self.assertEqual(phonetic_structure.shape(reading, self.verified()), (False, False))
        problems = self.script("check_structure")._check_unit(reading, self.verified())
        self.assertTrue(any("[A8]" in p for p in problems), problems)
        self.assertTrue(any("[A9]" in p for p in problems), problems)
        codes = {e["code"] for e in upload_payload.build_payload({"items": [reading]}, batch_dir=self.root)["errors"]}
        self.assertTrue(codes.intersection({"GROUP_INCOMPLETE", "MISSING_STEM"}), codes)



class A8SelfContainedGroupTest(unittest.TestCase):
    """A8：每小题目包含内容、无共享材料的合法组题形态（四线三格空白网格）不误报。

    原卷视觉已核实 05 Starter Unit 2 四线三格：每条诗句下是空白三格，空 material 属
    硬规则 9 的「空白作答网格」合法形态，不是丢内容。阅读理解/完形等共享语篇仍须 material。
    """

    def script(self):
        path = Path(__file__).parents[1] / "scripts/06-upload" / "check_structure.py"
        spec = importlib.util.spec_from_file_location("a8_self_contained", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def a8(self, unit):
        return [p for p in self.script()._check_unit(unit) if "[A8]" in p]

    def test_four_line_grid_group_without_material_passes(self):
        # 四线三格：每个小题自带诗句 + 空白表格网格，组级无共享 material。
        unit = {"unit_id": "u1", "question_type": "单词拼写", "unit_kind": "group", "material": "",
                "subquestions": [
                    {"number": 1, "stem": "两个黄鹂鸣翠柳\n[TABLE 1]\n\n\n[/TABLE]", "answer": "a"},
                    {"number": 2, "stem": "近朱者赤\n[TABLE 2]\n\n\n[/TABLE]", "answer": "b"}]}
        self.assertEqual(self.a8(unit), [])

    def test_reading_group_without_material_still_blocks(self):
        unit = {"unit_id": "u2", "question_type": "阅读理解", "unit_kind": "group", "material": "",
                "subquestions": [
                    {"number": 1, "stem": "What is it?", "answer": "A",
                     "options": ["A.a", "B.b", "C.c", "D.d"]}]}
        self.assertTrue(self.a8(unit))

    def test_cloze_group_without_material_still_blocks(self):
        unit = {"unit_id": "u3", "question_type": "完形填空", "unit_kind": "group", "material": "",
                "subquestions": [{"number": 1, "stem": "1.", "answer": "A",
                                  "options": ["A.a", "B.b", "C.c", "D.d"]}]}
        self.assertTrue(self.a8(unit))

    def test_partial_grid_still_blocks(self):
        # 某个小题没有自带网格 → 视为不完整的自包含形态，仍报 A8。
        unit = {"unit_id": "u4", "question_type": "单词拼写", "unit_kind": "group", "material": "",
                "subquestions": [
                    {"number": 1, "stem": "诗句\n[TABLE 1]\n\n[/TABLE]", "answer": "a"},
                    {"number": 2, "stem": "诗句", "answer": "b"}]}
        self.assertTrue(self.a8(unit))


if __name__ == "__main__":
    unittest.main()
