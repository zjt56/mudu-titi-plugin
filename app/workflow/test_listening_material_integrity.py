"""Listening material regressions; synthetic temporary batches only."""
import copy
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from workflow import denoise, structure_gate
from workflow.question_content import audit_batch_source


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts/06-upload"


def load_script(name):
    spec = importlib.util.spec_from_file_location(f"listening_material_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ListeningMaterialIntegrityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.integrity = load_script("audit_batch_integrity")
        cls.answers = load_script("check_answers")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / "06_batch/batch_meta.json"
        self.target.parent.mkdir()

    def run_integrity(self, batch):
        self.target.write_text(json.dumps(batch, ensure_ascii=False), encoding="utf-8")
        before = self.target.read_bytes()
        output = io.StringIO()
        with redirect_stdout(output):
            code = self.integrity.main(["--batch", str(self.target)])
        self.assertEqual(self.target.read_bytes(), before)
        return code, output.getvalue()

    def listening_batch(self, transcript=""):
        instruction = "听下面一段对话，回答第18至20小题。"
        material = "\n".join(filter(None, [instruction, transcript]))
        children = [
            {"number": 18, "stem": "What does Ben want to buy?", "answer": "A"},
            {"number": 19, "stem": "What color does Ben like best?", "answer": "B"},
            {"number": 20, "stem": "Who will Ann buy a gift for?", "answer": "C"},
        ]
        for child in children:
            child["options"] = [{"label": label, "text": text} for label, text in
                                zip("ABC", ["First choice", "Second choice", "Third choice"])]
        batch = {"items": [{"unit_id": "group-18-19-20", "unit_kind": "group",
                            "question_type": "听力长对话理解", "numbers": [18, 19, 20],
                            "stem": instruction, "material": transcript, "subquestions": children}]}
        body = material + "\n" + "\n".join(
            f"{c['number']}. {c['stem']}\n" + " ".join(f"{o['label']}.{o['text']}" for o in c["options"])
            for c in children)
        structure = {"sections": [{"type": "听力长对话理解", "questions": [[18, 20]]}]}
        source = self.root / "01_source"
        source.mkdir(exist_ok=True)
        (source / "正文.txt").write_text(body, encoding="utf-8")
        (source / "structure.json").write_text(json.dumps(structure), encoding="utf-8")
        cleaned, _, _ = denoise.clean_body(body)
        confirmed = {"units": structure_gate._build_units(structure["sections"], [], cleaned.splitlines())}
        self.assertEqual([u["numbers"] for u in confirmed["units"]], [[18, 19, 20]])
        (source / "structure.confirmed.json").write_text(json.dumps(confirmed), encoding="utf-8")
        return batch

    def test_source_questions_without_transcript_pass_both_text_audits(self):
        batch = self.listening_batch()
        # No audio, ASR or skip marker: this text audit must not invent a transcript dependency.
        self.assertFalse((self.root / "02_audio").exists())
        self.assertEqual(self.run_integrity(batch)[0], 0)
        audit = audit_batch_source(self.root, batch)
        self.assertTrue(audit["passed"], audit["issues"])
        self.assertEqual(batch["items"][0]["material"], "")

    def test_original_transcript_preserved_without_requiring_literal_heading(self):
        for heading in ("听力原文", "录音材料", "Listening script"):
            with self.subTest(heading=heading):
                batch = self.listening_batch(f"{heading}：\nBen wants a cotton T-shirt. Ann buys a gift for her friend.")
                self.assertEqual(self.run_integrity(batch)[0], 0)
                audit = audit_batch_source(self.root, batch)
                self.assertTrue(audit["passed"], audit["issues"])
                for altered in (heading + "：", "",
                                batch["items"][0]["material"].split("\n", 1)[1],
                                batch["items"][0]["material"].replace("cotton", "silk")):
                    with self.subTest(altered=altered):
                        broken = copy.deepcopy(batch)
                        broken["items"][0]["material"] = altered
                        audit = audit_batch_source(self.root, broken)
                        self.assertFalse(audit["passed"])
                        self.assertIn("SOURCE_MATERIAL_MISSING", {i["code"] for i in audit["issues"]})

    def test_missing_instruction_child_or_option_stays_blocking(self):
        batch = self.listening_batch()
        mutations = (
            ("SOURCE_MATERIAL_MISSING", lambda row: row.update(stem="")),
            ("SOURCE_STEM_MISSING", lambda row: row["subquestions"][0].update(stem="")),
            ("CHILD_NUMBER_COVERAGE", lambda row: row["subquestions"].pop()),
            ("OPTIONS_MISMATCH", lambda row: row["subquestions"][0]["options"].pop()),
            ("OPTION_CONTENT_MISMATCH", lambda row: row["subquestions"][0]["options"][0].update(text="Altered")),
        )
        for code, change in mutations:
            with self.subTest(code=code):
                broken = copy.deepcopy(batch)
                change(broken["items"][0])
                self.assertIn(code, {i["code"] for i in audit_batch_source(self.root, broken)["issues"]})

    def test_information_conversion_keeps_source_table_without_transcript(self):
        instruction = "你将听到一篇短文。请根据短文内容，写出下面表格中所缺的单词，每空仅填一词。"
        rows = [["Spring break", ""],
                ["How long", "Spring break lasts one __26__."],
                ["What", "Students watch TV or do __27__."],
                ["Whom", "We helped some __28__."],
                ["Where", "We went to a __29__."],
                ["What", "I learned to __30__."]]
        table = "\n".join(" | ".join(row) for row in rows)
        body = f"{instruction}\n[TABLE 1]\n{table}\n[/TABLE]"
        structure = {"sections": [{"type": "听力信息转换", "questions": [[26, 30]]}]}
        source = self.root / "01_source"
        source.mkdir()
        (source / "正文.txt").write_text(body, encoding="utf-8")
        (source / "structure.json").write_text(json.dumps(structure), encoding="utf-8")
        cleaned, _, _ = denoise.clean_body(body)
        confirmed = {"units": structure_gate._build_units(structure["sections"], [], cleaned.splitlines())}
        self.assertEqual([u["numbers"] for u in confirmed["units"]], [[26, 27, 28, 29, 30]])
        (source / "structure.confirmed.json").write_text(json.dumps(confirmed), encoding="utf-8")
        batch = {"items": [{"unit_id": "group-26-30", "unit_kind": "group",
                            "question_type": "听力信息转换", "numbers": list(range(26, 31)),
                            "stem": instruction, "material": table,
                            "materialBlocks": [{"type": "table", "rows": rows}],
                            "subquestions": [{"number": n, "stem": row[1]} for n, row in zip(range(26, 31), rows[1:])]}]}
        self.assertEqual(self.run_integrity(batch)[0], 0)
        audit = audit_batch_source(self.root, batch)
        self.assertTrue(audit["passed"], audit["issues"])
        del batch["items"][0]["materialBlocks"]
        self.assertIn("TABLE_STRUCTURE_MISSING", {i["code"] for i in audit_batch_source(self.root, batch)["issues"]})
        batch["items"][0]["material"] = ""
        self.assertIn("SOURCE_MATERIAL_MISSING", {i["code"] for i in audit_batch_source(self.root, batch)["issues"]})

    def test_empty_option_watermark_and_adjacent_material_checks_remain(self):
        batch = self.listening_batch()
        row = batch["items"][0]
        row["material"] = "The original shared listening exercise instructions."
        row["subquestions"][0]["options"][0]["text"] = " "
        row["subquestions"][1]["options"][1]["text"] = None
        row["subquestions"][2]["stem"] += "[来源: watermark]"
        other = copy.deepcopy(row)
        other["unit_id"] = "group-21-25"
        other["material"] += " Additional unrelated paragraph mistakenly copied here."
        batch["items"].append(other)
        code, output = self.run_integrity(batch)
        self.assertNotEqual(code, 0)
        for marker in ("E20-空选项", "E20-残留", "E20-包含"):
            self.assertIn(marker, output)

    def test_answers_still_require_independent_complete_matching_content(self):
        batch = self.listening_batch()
        answers = self.answers.collect_json({"18": "A", "19": "B", "20": "C"})
        self.assertTrue(self.answers.inspect(batch, answers)["passed"])
        broken = copy.deepcopy(batch)
        broken["items"][0]["subquestions"][0]["answer"] = "B"
        self.assertIn("ANSWER_MISMATCH", {i["code"] for i in self.answers.inspect(broken, answers)["issues"]})
        # 空白答案不再抛输入错误（用户规则：缺答案不作为输入错误阻断）。它应按
        # "该题无答案"处理——不与独立答案比较，也不会让整卷被判为格式错误。
        broken["items"][0]["subquestions"][0]["answer"] = ""
        report = self.answers.inspect(broken, answers)
        codes = {i["code"] for i in report["issues"]}
        self.assertNotIn("ANSWER_MISMATCH", codes)
        self.assertNotIn("ANSWER_SOURCE_MISSING", codes)
        del answers["20"]
        self.assertIn("ANSWER_SOURCE_MISSING", {i["code"] for i in self.answers.inspect(batch, answers)["issues"]})


if __name__ == "__main__":
    unittest.main()
