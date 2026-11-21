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

    def test_partial_group_match_does_not_leak_into_another_section(self):
        sections = [
            {"type": "听力短对话理解", "questions": [1, 2, 3, 4, 5],
             "source_line": 2, "source_end_line": 6},
            {"type": "阅读理解", "questions": [56, 57, 58],
             "source_line": 7, "source_end_line": 8},
        ]
        # Stale reading group still carries mis-counted 2/5/6; only its complete
        # member set may define a group, and only inside its own section.
        groups = [{"type": "阅读理解", "questions": [2, 5, 6, 56, 57, 58]}]
        lines = ["卷名", "1. Q", "2. Q", "3. Q", "4. Q", "5. Q", "A", "56. Q", "57. Q", "58. Q"]
        units = structure_gate._build_units(sections, groups, lines)
        listening = [u for u in units if u["type"] == "听力短对话理解"]
        self.assertEqual(len(listening), 5)
        self.assertTrue(all(u["unit_kind"] == "standalone" and u["numbers"] == [n]
                            for u, n in zip(sorted(listening, key=lambda u: u["numbers"]), range(1, 6))))
        reading = [u for u in units if u["type"] == "阅读理解"]
        self.assertFalse([u["numbers"] for u in reading if set(u["numbers"]) != {56, 57, 58}])

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

    def test_empty_source_yields_no_units_and_no_pass(self):
        # 结构确认不做阻断校验；但正文为空 -> 拆不出任何单元 -> 无可交付结构。
        structure = {"sections": [{"type": "单项填空", "questions": [1]}]}
        batch = make_batch(self.root, structure, "")
        report = structure_gate.run_gate(batch)
        self.assertFalse(report["passed"])
        self.assertEqual(report["unit_counts"]["total"], 0)
        codes = [w["code"] for w in report["warnings"]]
        self.assertIn("SOURCE_EMPTY", codes)
        self.assertEqual(report["blockers"], [])

    def test_no_sections_is_warning_not_blocking(self):
        # 结构确认不做阻断校验；无 sections 只留痕，不阻断阶段。
        batch = make_batch(self.root, {"sections": []}, "( )1. A. a")
        report = structure_gate.run_gate(batch)
        codes = [w["code"] for w in report["structure_warnings"]]
        self.assertIn("NO_SECTIONS", codes)
        self.assertEqual(report["blockers"], [])

    def test_group_out_of_section_is_warning_not_blocking(self):
        # 结构确认不做阻断校验：组题越界只作为 warning 留痕，仍产出单元继续下游。
        structure = {
            "sections": [{"type": "阅读理解", "questions": [[30, 32]]}],
            "groups": [{"questions": [31, 99]}],   # 99 不属于任何大题
        }
        body = "阅读\n30. q\n31. q\n32. q"
        batch = make_batch(self.root, structure, body)
        report = structure_gate.run_gate(batch)
        codes = [w["code"] for w in report["warnings"]]
        self.assertIn("GROUP_OUT_OF_SECTION", codes)
        self.assertEqual(report["blockers"], [])
        self.assertTrue(report["unit_counts"]["total"] > 0)

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

    def test_option_letters_do_not_split_numbered_group_window(self):
        # 阅读理解的选项单独成行（A. / B. / C. / D. + 图片）时，选项字母不能被
        # 当成文章标签切开；否则题组只剩最后一个选项窗口，题号 1~3 与材料全丢。
        from workflow import structure_gate as sg
        lines = [
            "七上 · 人教版 · 课时A计划",
            "Starter Unit 2 Keep Tidy!",
            "第二课时　Pronunciation",
            "## B 能力提升",
            "## 阅读理解",
            "I'm Linda. In my home, there is a black and white thing.",
            "( ) 1. What colour is Neo?",
            "A. White and green. B. Red and black.",
            "( ) 2. What does Neo say when you call its name?",
            "A. I'm here! B. Hello!",
            "( ) 3. [新考法·图片理解] What does Linda's brother think Neo is?",
            "A.",
            "[IMG imgs/img_in_image_box_640_1284_720_1381.jpg]",
            "B.",
            "[IMG imgs/img_in_image_box_854_1294_973_1370.jpg]",
            "C.",
            "[IMG imgs/img_in_image_box_639_1393_721_1483.jpg]",
            "D.",
            "[IMG imgs/img_in_image_box_855_1406_989_1466.jpg]",
        ]
        sections = [{"type": "阅读理解", "title": "## 阅读理解", "source_line": 5,
                     "source_end_line": 19, "sync_occurrence": 1, "kind": "group",
                     "questions": [1, 2, 3]}]
        units = sg._build_units(sections, [{"questions": [1, 2, 3], "type": "阅读理解",
                                           "boundary_method": "section_candidate"}], lines)
        self.assertEqual(len(units), 1, [u.get("unit_id") for u in units])
        unit = units[0]
        self.assertEqual(unit["numbers"], [1, 2, 3])
        span = unit["source_span"]
        # 完整窗口：材料 + 三道题都在内，而不是只剩选项块
        self.assertLessEqual(span["start_line"], 6)
        self.assertEqual(span["end_line"], 19)
        self.assertIn("I'm Linda", unit["stem"])
        self.assertIn("What does Linda's brother think Neo is?", unit["stem"])
        self.assertNotEqual(unit["stem"].strip(), "A.\n[IMG imgs/img_in_image_box_640_1284_720_1381.jpg]")
        errors = sg._scoped_source_errors(units, sections, lines)
        self.assertEqual([e["code"] for e in errors], [])

    def test_blank_group_window_still_reports_uncovered(self):
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

    def test_section_numbers_ignore_material_blank_digits_when_questions_are_bound(self):
        from workflow import structure_gate as sg
        section = {
            "questions": [2, 5, 6, 56, 57, 58, 59],
            "question_locations": [
                {"number": 2, "line": 12, "kind": "blank"},
                {"number": 5, "line": 12, "kind": "blank"},
                {"number": 56, "line": 20, "kind": "question"},
                {"number": 57, "line": 22, "kind": "question"},
                {"number": 58, "line": 24, "kind": "question"},
                {"number": 59, "line": 30, "kind": "question"},
            ],
        }
        self.assertEqual(sg._section_numbers(section), [56, 57, 58, 59])

    def test_cloze_blank_markers_are_not_printed_anchors(self):
        # 完形/短文填空材料行内的 <u>　n　</u> 是编号空格（即子题号），不是印刷题号锚点；
        # 不能被当成与下方 ( )n. 选项并列的重复锚点，否则报 SOURCE_DUPLICATE_NUMBERS / QUESTION_NOT_FOUND。
        lines = [
            "Ⅱ.完形填空",
            "Good <u>　1　</u>.Let’s <u>　2　</u> now to <u>　9　</u>.After this <u>　10　</u> done.",
            "(　)1.A.bell\tB.section",
            "C.name\tD.unit",
            "(　)2.A.room\tB.water",
            "C.class\tD.time",
            "(　)9.A.me\tB.you",
            "C.it\tD.her",
            "(　)10.A.farm\tB.conversation",
            "C.door\tD.photo",
        ]
        from workflow import structure_gate as sg
        anchors = sg._scope_anchors(lines)
        numbers = [n for _, n in anchors]
        # 材料行的 1/2/9/10 空格只应作为 weak（题号本体）出现一次，不与选项行重复计数。
        self.assertEqual(sorted(numbers), [1, 2, 9, 10], numbers)
        self.assertEqual(len(numbers), len(set(numbers)), numbers)

    def test_table_numbered_blanks_with_initials_cover_every_slot(self):
        from workflow import extract, structure_gate as sg
        lines = ["Unit 2 第四课时", "[IMG header.jpeg]教材语篇回顾[IMG end.jpeg]", "[TABLE 1]",
                 "One 1.<u>　　</u> and 2.<u>h　　</u>; then 3.<u>　　</u>",
                 "Her 4.<u>l　　</u>, 5.<u>　　</u> and 6.<u>　　</u>",
                 "7.<u>　　</u>, 8.<u>　　</u>, 9.<u>　　</u>,10.<u>b　　</u>", "[/TABLE]",
                 "Ⅰ.根据首字母及汉语提示补全单词", "1.A <u>h　　</u> (帽子)"]
        self.assertEqual(sorted(n for _, n in sg._scope_anchors(lines[2:7])), list(range(1, 11)))
        structure, _, _ = extract.infer_structure("七上 · 人教版 · 课时A计划\n**Unit 2　We’re Family!**\n**第四课时** Section B\n" + "\n".join(lines[1:]))
        self.assertEqual(structure['sections'][0]['questions'], list(range(1, 11)))
        self.assertEqual(sg._scope_anchors(['Age 12.5, section 3.Home, number 4.hat']), [])

    def test_table_image_coordinates_are_not_printed_anchors(self):
        # OCR/Word 表格里 img_in_image_box_<x>_<y>_<w>_<h>.jpg 的坐标数字和温度数字
        # 是版面数据，不是小题号。实际批次因此把 563/566/…/995 误当问答题号，
        # 导致该组 numbers 膨胀、题号覆盖报错。锚点扫描必须先剥掉表格/图片标记。
        from workflow import structure_gate as sg
        lines = [
            "Here is the weather report.",
            "<table border=1><tr><td>10 Jan.</td><td>11 Jan.</td></tr>"
            "<tr><td><img src=\"imgs/img_in_image_box_566_714_638_765.jpg\" />19°C~27°C</td>"
            "<td><img src=\"imgs/img_in_image_box_647_717_726_766.jpg\" />20°C~27°C</td></tr></table>",
            "( ) 1. How long will Cathy stay?",
            "A. Five. B. Six. C. Seven. D. Eight.",
            "( ) 2. What will the weather be like?",
            "A. Sunny. B. Snowy. C. Cloudy. D. Rainy.",
            "( ) 3. Which way is mentioned?",
            "A. Hotline. B. Internet. C. TV. D. Radio.",
        ]
        numbers = sorted(n for _, n in sg._scope_anchors(lines))
        self.assertEqual(numbers, [1, 2, 3], numbers)

    def test_cloze_group_with_inline_blank_numbers_passes_scoped_gate(self):
        lines = [
            "Ⅱ.完形填空",
            "Good <u>　1　</u>.Let’s <u>　2　</u> now.Then <u>　9　</u>.After <u>　10　</u> done.",
            "(　)1.A.bell\tB.section",
            "(　)2.A.room\tB.water",
            "(　)9.A.me\tB.you",
            "(　)10.A.farm\tB.conversation",
        ]
        sections = [{"type": "完形填空", "title": "Ⅱ.完形填空", "source_line": 1,
                     "source_end_line": len(lines), "sync_occurrence": 1, "candidate_unit_kind": "group"}]
        from workflow import structure_gate as sg
        units = sg._build_units(sections, [], lines)
        codes = [e["code"] for e in sg._scoped_source_errors(units, sections, lines)]
        self.assertNotIn("SOURCE_DUPLICATE_NUMBERS", codes)
        self.assertNotIn("QUESTION_NOT_FOUND", codes)

    def test_side_by_side_fullwidth_space_questions_preserve_all_anchors(self):
        # 同步课时语音题常把 1/2/3 或 4/5 用两个全角空格并排在同一行；
        # 规范化空档后不能吃掉全角列分隔，否则 2/3/5 题号丢失，导致整课时答案无法唯一绑定。
        from workflow import structure_gate as sg
        lines = [
            "**Ⅰ. 根据图片及所给音标写出下列单词所缺的字母**",
            "**1.**[IMG image35.jpeg]",
            "c____/æ/t\u3000\u30002.[IMG image36.jpeg]",
            "____/e/gg\u3000\u30003.[IMG image37.jpeg]",
            "p____/ɪ/g",
            "**4.**[IMG image38.jpeg]",
            "b____/ɒ/x\u3000\u30005.[IMG image39.jpeg]",
            "s____/ʌ/n",
        ]
        anchors = sg._scope_anchors(lines)
        self.assertEqual([n for _, n in anchors], [1, 2, 3, 4, 5], anchors)

    def test_denoise_preserves_numbered_question_with_exam_source_tag(self):
        from workflow import denoise, extract
        text = (
            "八上 · 人教版 · 课时A计划\n"
            "**Unit 1　Happy Holiday**\n"
            "**第三课时**　Grammar Focus\n"
            "**Ⅰ. 单项填空**\n"
            "**1.**—Where did you go?\n"
            "A.Home.\tB.School.\n"
            "**2****.**[2025·蚌埠模拟]—Did you buy anything special?\n"
            "A.Yes.\tB.No.\n"
        )
        structure, body, _ = extract.infer_structure(text)
        raw_body = body if body.endswith("\n") else body + "\n"
        cleaned, _, _ = denoise.clean_body(raw_body)
        projected = denoise.project_structure(structure, raw_body, cleaned)
        self.assertEqual(projected["sections"][0]["questions"], [1, 2])

    def test_blank_before_ordinal_date_or_number_is_not_printed_anchor(self):
        from workflow import preview, structure_gate as sg
        self.assertEqual(preview._weak_numbers("My birthday is on ________12th March."), [])
        self.assertEqual(preview._weak_numbers("He only __10__ 15 dollars for lunch."), [10])
        self.assertEqual(preview._weak_numbers("They can ________________________8,000 ________."), [])
        anchors = sg._scope_anchors([
            "**1.**My birthday is on ________12th March.",
            "**2.**They can ________________________8,000 ________.",
        ])
        self.assertEqual([n for _, n in anchors], [1, 2])

    def test_unnumbered_imitation_and_quoted_cloze_word_meaning_pass_scoped_gate(self):
        from workflow import extract, denoise, structure_gate as sg
        text = (
            "九上 · 人教版 · 课时A计划\n"
            "**Unit 4　Our Memory**\n"
            "**第四课时**　Section B (1a-1e)\n"
            "**Ⅰ. 单项填空**\n"
            "**1.**He has a good memory.\n"
            "A.Yes\tB.No\n"
            "**☆****佳句仿写**\n"
            "As I was looking at it,the __<u>2</u>__ memory of that day came back to me.\n"
            "仿写:当我听到这首老歌时,校园生活的美好回忆涌上心头。\n"
            "___________________________________________________________________________________\n"
            "**熟词生义**\n"
            "**写出下面画线词的汉语意思**\n"
            "Then they __<u>3</u>__ the students 40 <u>seconds</u> to memorize them.\n"
        )
        structure, body, _ = extract.infer_structure(text)
        raw_body = body if body.endswith("\n") else body + "\n"
        cleaned, _, _ = denoise.clean_body(raw_body)
        ps = denoise.project_structure(structure, raw_body, cleaned)
        lines = cleaned.splitlines()
        units = sg._build_units(ps["sections"], ps.get("groups") or [], lines)
        errors = sg._scoped_source_errors(units, ps["sections"], lines)
        self.assertEqual(errors, [])

    def test_single_line_writing_prompt_before_guide_heading_passes_scoped_gate(self):
        from workflow import extract, denoise, structure_gate as sg
        text = (
            "七上 · 人教版 · 课时A计划\n"
            "**Unit 2　We’re Family!**\n"
            "**单元话题写作**\n"
            "**【第3步 典例分析】**\n"
            "家是我们最熟悉的地方。假如你是李华,请写一篇60词左右的英语短文向校英语报投稿。\n"
            "[IMG image59.jpeg]审题思路[IMG image60.jpeg]\n"
            "**1.**体裁:说明文。\n"
        )
        structure, body, _ = extract.infer_structure(text)
        raw_body = body if body.endswith("\n") else body + "\n"
        cleaned, _, _ = denoise.clean_body(raw_body)
        ps = denoise.project_structure(structure, raw_body, cleaned)
        lines = cleaned.splitlines()
        units = sg._build_units(ps["sections"], ps.get("groups") or [], lines)
        errors = sg._scoped_source_errors(units, ps["sections"], lines)
        self.assertEqual(errors, [])

    def test_clean_standard_exam_skips_redundant_structure_ai(self):
        import os
        from unittest.mock import patch
        structure = {
            "sections": [
                {"type": "单项填空", "questions": [1, 2]},
            ]
        }
        body = "2024年安徽省初中学业水平考试\n1. First?\nA. a  B. b\n2. Second?\nA. c  B. d\n"
        batch = make_batch(self.root, structure, body)
        with patch.dict(os.environ, {"WF_STRUCTURE_AI": "1"}), \
             patch("workflow.structure_ai.auto_classify") as ai_call:
            report = structure_gate.run_gate(batch)
        ai_call.assert_not_called()
        self.assertTrue(report["passed"], report["blockers"])
        self.assertIn("AI_STRUCTURE_SKIPPED", {w.get("code") for w in report["warnings"]})
        with patch.dict(os.environ, {"WF_STRUCTURE_AI": "1", "WF_FORCE_STRUCTURE_AI": "1"}), \
             patch("workflow.structure_ai.auto_classify", return_value={"ran": False}) as ai_forced:
            structure_gate.run_gate(batch)
        ai_forced.assert_called_once()

    def test_nonblocking_floating_object_extraction_warning_still_skips_structure_ai(self):
        import os
        from unittest.mock import patch
        structure = {
            "sections": [{"type": "单项填空", "questions": [1, 2]}],
            "unresolved": [{"code": "EXTRACTION_WARNING", "message": "原生/OCR 提取有警告，详见 extraction.json"}],
        }
        body = "2025年安徽省初中学业水平考试\n1. First?\nA. a  B. b\n2. Second?\nA. c  B. d\n"
        batch = make_batch(self.root, structure, body)
        (batch / "01_source" / "extraction.json").write_text(
            json.dumps({
                "warnings": ["Floating object encountered; XML anchor order may differ from visual order"]
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        with patch.dict(os.environ, {"WF_STRUCTURE_AI": "1"}), \
             patch("workflow.structure_ai.auto_classify") as ai_call:
            report = structure_gate.run_gate(batch)
        ai_call.assert_not_called()
        self.assertTrue(report["passed"], report["blockers"])
        codes = {w.get("code") for w in report["warnings"]}
        self.assertIn("AI_STRUCTURE_SKIPPED", codes)
        self.assertIn("EXTRACTION_WARNING", codes)
        self.assertTrue(report["layout_required"])


if __name__ == "__main__":
    unittest.main()
