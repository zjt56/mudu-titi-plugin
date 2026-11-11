# -*- coding: utf-8 -*-
"""结构自动闸门测试：独立题逐题、组合题成组、去噪、元信息、覆盖与放行。"""
import json
import tempfile
import unittest
from pathlib import Path

from workflow import structure_gate


def make_batch(root: Path, structure: dict, body: str) -> Path:
    batch = root / "batch"
    (batch / "01_source").mkdir(parents=True)
    (batch / "01_source" / "structure.json").write_text(
        json.dumps(structure, ensure_ascii=False), encoding="utf-8")
    (batch / "01_source" / "正文.txt").write_text(body, encoding="utf-8")
    return batch


class StructureGateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # 结构 AI 是生产主路径；这些用例验证确定性闸门，不能因为本机
        # 配置了模型路由就发起真实调用（会把测试拖慢并占用执行锁）。
        import os as _os
        from unittest.mock import patch as _patch
        _offline = _patch.dict(_os.environ, {'WF_STRUCTURE_AI': '0'})
        _offline.start(); self.addCleanup(_offline.stop)

    def test_is_group_type(self):
        self.assertTrue(structure_gate.is_group_type("完形填空"))
        self.assertTrue(structure_gate.is_group_type("阅读理解"))
        self.assertFalse(structure_gate.is_group_type("单项填空"))
        self.assertFalse(structure_gate.is_group_type("单词拼写"))
        self.assertIsNone(structure_gate.is_group_type(""))

    def test_standalone_split_one_by_one_and_group_merged(self):
        structure = {
            "sections": [
                {"type": "单项填空", "questions": [[1, 3]]},
                {"type": "完形填空", "questions": [[10, 12]]},
            ]
        }
        body = "\n".join([
            "2026年安徽省初中学业水平考试",
            "( )1. A. a  B. b",
            "( )2. A. a  B. b",
            "( )3. A. a  B. b",
            "完形填空",
            "There __10__ a boy. He __11__ tall. He __12__ runs.",
            "10. A. is B. are C. am D. be",
            "11. A. is B. are C. am D. be",
            "12. A. often B. very C. much D. more",
        ])
        batch = make_batch(self.root, structure, body)
        report = structure_gate.run_gate(batch)
        self.assertTrue(report["passed"], report["blockers"])
        conf = json.loads((batch / "01_source" / "structure.confirmed.json").read_text(encoding="utf-8"))
        units = conf["units"]
        standalone = [u for u in units if u["unit_kind"] == "standalone"]
        group = [u for u in units if u["unit_kind"] == "group"]
        self.assertEqual(len(standalone), 3)          # 单选逐题
        self.assertEqual(len(group), 1)               # 完形整组一个单元
        self.assertEqual(group[0]["numbers"], [10, 11, 12])
        self.assertEqual(report["unit_counts"]["standalone"], 3)
        self.assertEqual(report["unit_counts"]["group"], 1)
        # 元信息已抽到 meta，正文不再含卷名
        self.assertEqual(conf["meta"].get("year"), 2026)

    def test_group_without_printed_question_numbers_keeps_section_source(self):
        sections = [{"type": "听力短文理解", "questions": [11, 12, 13, 14, 15],
                     "source_line": 1, "source_end_line": 3}]
        groups = [{"questions": [11, 12, 13, 14, 15]}]
        units = structure_gate._build_units(sections, groups, [
            "听下面一段短文。", "The museum opens at nine.", "Please arrive early."
        ])
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]["unit_kind"], "group")
        self.assertIn("The museum opens at nine.", units[0]["stem"])
        self.assertEqual(units[0]["source_span"]["start_line"], 1)

    def test_image_only_standalone_retains_source_and_missing_unit_blocks(self):
        from copy import deepcopy
        from workflow import extract
        text = ("第一课时 Section A\nⅠ.完成句子\n[IMG image2.jpeg]\n"
                "Ⅱ.单项填空\n1.Who?\nA.a B.b C.c D.d")
        structure, body, _ = extract.infer_structure(text)
        batch = make_batch(self.root, structure, body)
        report = structure_gate.run_gate(batch)
        self.assertTrue(report["passed"], report["blockers"])
        confirmed = json.loads((batch / "01_source/structure.confirmed.json").read_text(encoding="utf-8"))
        units = confirmed["units"]
        self.assertEqual(len(units), 2)
        image = units[0]
        self.assertEqual(image["unit_kind"], "standalone")
        self.assertEqual(image["stem"], "[IMG image2.jpeg]")
        self.assertEqual(image["numbers"], [1])
        self.assertEqual(image["source_span"], {
            "start_line": 3, "end_line": 3, "basis": "cleaned_source",
            "number_binding": "unnumbered_section", "printed_numbers": []})
        self.assertIsNone(image["original_number"])
        self.assertFalse(image["printed_number"])
        self.assertNotEqual(image["question_ids"]["1"], units[1]["question_ids"]["1"])
        # bare 完成句子不是规则标题，单项填空是：部分候选不能丢掉前一节身份。
        self.assertEqual(image["source_section"]["title"], "Ⅰ.完成句子")
        self.assertEqual(image["source_section"]["start_line"], 2)
        self.assertEqual(units[1]["source_section"]["title"], "Ⅱ.单项填空")
        self.assertEqual(units[1]["source_section"]["occurrence"], 1)
        errors = structure_gate._scoped_source_errors(units[1:], structure["sections"], body.splitlines())
        self.assertIn("SOURCE_SECTION_UNCOVERED", {issue["code"] for issue in errors})
        damaged = deepcopy(units)
        damaged[0]["source_span"]["start_line"] = 5
        errors = structure_gate._scoped_source_errors(damaged, structure["sections"], body.splitlines())
        self.assertIn("SOURCE_SECTION_UNCOVERED", {issue["code"] for issue in errors})

    def test_image_only_group_cannot_be_omitted_by_capability_warning(self):
        from workflow import extract
        text = ("第一课时 Section A\nⅠ.按照字母表顺序,写出空白处所缺字母\n[IMG image2.jpeg]\n"
                "Ⅱ.单项填空\n1.Who?\nA.a B.b C.c D.d")
        structure, body, _ = extract.infer_structure(text)
        units = structure_gate._build_units(structure["sections"], structure["groups"], body.splitlines())
        self.assertEqual(len(units), 2)
        self.assertEqual(units[0]["unit_kind"], "group")
        errors = structure_gate._scoped_source_errors(units[1:], structure["sections"], body.splitlines())
        self.assertIn("SOURCE_SECTION_UNCOVERED", {issue["code"] for issue in errors})

    def test_original_website_content_survives_confirmed_structure(self):
        from workflow import extract
        text = ("2024年安徽省中考\nⅠ.单项填空(共1小题)\n"
                "1. Where can you get more information?\n"
                "Visit www.example.com and enter your school name.\n"
                "A. At home.\nB. On the website.\nC. In the park.\nD. At the station.\n")
        structure, body, _ = extract.infer_structure(text)
        batch = make_batch(self.root, structure, body)
        original = self.root / "original.txt"
        original.write_text(text, encoding="utf-8")
        (batch / "workflow").mkdir()
        (batch / "workflow/manifest.json").write_text(json.dumps({
            "inputs": {"source": {"path": str(original)}}}), encoding="utf-8")
        report = structure_gate.run_gate(batch)
        self.assertTrue(report["passed"], report["blockers"])
        confirmed = json.loads((batch / "01_source/structure.confirmed.json").read_text())
        self.assertEqual(confirmed["units"][0]["stem"], "\n".join(text.splitlines()[2:]))
        self.assertEqual(original.read_text(), text)

    def test_numbered_year_question_is_kept_but_exact_exam_header_is_removed(self):
        from workflow import extract, preview
        text = ("1.2024年安徽省中考\nⅠ.单词拼写(共1小题)\n"
                "1.2024年我参加安徽省中考。\n")
        structure, body, _ = extract.infer_structure(text)
        batch = make_batch(self.root, structure, body)
        report = structure_gate.run_gate(batch)
        self.assertTrue(report["passed"], report["blockers"])
        confirmed = json.loads((batch / "01_source/structure.confirmed.json").read_text())
        self.assertEqual(confirmed["units"][0]["stem"], "1.2024年我参加安徽省中考。")
        self.assertEqual(confirmed["meta"]["source"], "2024年安徽省中考")
        self.assertEqual(preview.slice_text(text.splitlines(), 1), ["1.2024年我参加安徽省中考。"])
        marked = text.replace("1.2024年安徽省中考", "  **1.2024年安徽省中考**")
        self.assertEqual(preview.slice_text(marked.splitlines(), 1), ["1.2024年我参加安徽省中考。"])

    def test_empty_source_blocks(self):
        structure = {"sections": [{"type": "单项填空", "questions": [1]}]}
        batch = make_batch(self.root, structure, "")
        report = structure_gate.run_gate(batch)
        self.assertFalse(report["passed"])
        self.assertIn("SOURCE_EMPTY", [b["code"] for b in report["blockers"]])

    def test_no_sections_blocks(self):
        batch = make_batch(self.root, {"sections": []}, "( )1. A. a")
        report = structure_gate.run_gate(batch)
        self.assertFalse(report["passed"])
        self.assertIn("NO_SECTIONS", [b["code"] for b in report["blockers"]])

    def test_group_out_of_section_blocks(self):
        structure = {
            "sections": [{"type": "阅读理解", "questions": [[30, 32]]}],
            "groups": [{"questions": [31, 99]}],   # 99 不属于任何大题
        }
        body = "阅读\n30. q\n31. q\n32. q"
        batch = make_batch(self.root, structure, body)
        report = structure_gate.run_gate(batch)
        self.assertFalse(report["passed"])
        self.assertIn("GROUP_OUT_OF_SECTION", [b["code"] for b in report["blockers"]])

    def test_outputs_written(self):
        structure = {"sections": [{"type": "单项填空", "questions": [1]}]}
        batch = make_batch(self.root, structure, "( )1. A. a  B. b")
        structure_gate.run_gate(batch)
        self.assertTrue((batch / "01_source" / "structure.confirmed.json").is_file())
        self.assertTrue((batch / "07_review" / "structure-gate.json").is_file())

    def test_unnumbered_group_window_without_image_keeps_section_unit(self):
        # 同步练习：题组大题（如字母歌补全）既无印刷题号也无图片时，
        # 仍须保留一个绑定原文的来源单元，不能判成 SOURCE_SECTION_UNCOVERED。
        from workflow import structure_gate as sg
        lines = [
            "七上 · 人教版 · 课时A计划",
            "Starter Unit 1　Hello!",
            "第二课时　Pronunciation",
            "Ⅰ.将下列字母按照其所含音素进行正确归类",
            "1./eɪ/",
            "[TABLE 1]",
            "[/TABLE]",
            "Ⅱ.按照字母表顺序,写出下面英文字母歌中所缺的大写字母",
            "A,B,<u>　　　　</u>,D,E,F,<u>　　　　</u>,",
            "H,<u>　　　　</u>,J,K,<u>　　　　</u>,M,",
            "Ⅳ.单项填空",
            "( )1. A. a  B. b",
        ]
        sections = [
            {"type": "语音题", "title": "Ⅰ.将下列字母按照其所含音素进行正确归类",
             "source_line": 4, "source_end_line": 7, "sync_occurrence": 1},
            {"type": "语音题", "title": "Ⅱ.按照字母表顺序,写出下面英文字母歌中所缺的大写字母",
             "source_line": 8, "source_end_line": 10, "sync_occurrence": 2},
            {"type": "单项填空", "title": "Ⅳ.单项填空",
             "source_line": 11, "source_end_line": 12, "sync_occurrence": 3},
        ]
        units = sg._build_units(sections, [], lines)
        spans = [(u.get("source_span") or {}).get("start_line") for u in units]
        # 第Ⅱ大题的来源窗口必须有一个来源单元，不能被静默跳过。
        self.assertTrue(any(8 < s <= 10 for s in spans), spans)
        errors = sg._scoped_source_errors(units, sections, lines)
        self.assertNotIn("SOURCE_SECTION_UNCOVERED", [e["code"] for e in errors])

    def test_blank_group_window_still_reports_uncovered(self):
        # 纯空白题组窗口没有可绑定原文，保留跳空，避免造出空单元掩盖真实漏题。
        from workflow import structure_gate as sg
        lines = [
            "Ⅰ.语音", "1./eɪ/", "[TABLE 1]", "[/TABLE]",
            "Ⅱ.空题", "", "", "Ⅳ.单项填空", "( )1. A. a",
        ]
        sections = [
            {"type": "语音题", "title": "Ⅰ.语音", "source_line": 1, "source_end_line": 4},
            {"type": "语音题", "title": "Ⅱ.空题", "source_line": 5, "source_end_line": 7},
            {"type": "单项填空", "title": "Ⅳ.单项填空", "source_line": 8, "source_end_line": 9},
        ]
        units = sg._build_units(sections, [], lines)
        self.assertFalse(any(u.get("source_span") and u["source_span"]["start_line"] == 5 for u in units))


if __name__ == "__main__":
    unittest.main()
