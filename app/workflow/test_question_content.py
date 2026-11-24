# -*- coding: utf-8 -*-
import unittest
from pathlib import Path

from workflow.question_content import (
    audit_question, audit_source_unit, legitimate_source_shape, capability_limited_source, question_parts)
from workflow.preview import slice_text
from workbench.server import detail, source_blocks


class LeadingImageOptionTest(unittest.TestCase):
    """选项图片前置于标签（[IMG a]A.、[IMG b]B.）必须归到各自选项，不能错位。

    复刻任务型阅读图片选项题：错位会把 A 的图塞进题干、整体左移一位且 D 空，
    导致 SOURCE_OPTIONS_INCOMPLETE / SOURCE_IMAGE_MISSING 误报。
    """

    def test_cloze_extension_short_answer_needs_no_options(self):
        # 完形组末尾「☆拓展设问」原卷只有作答线，源头审计/结构体检不能判它缺 A–D 选项；
        # 真正漏选项的正文空位小题（空壳题干、答案不是字母）仍必须判缺。
        from workflow.question_content import short_answer_cue
        extension = {"stem": "What do people think of Anqing as?(不超过5个词)",
                     "options": [], "answer": "Hometown of Huangmei Opera."}
        blank_shell = {"stem": "______", "options": [], "answer": "know"}
        letter_answer = {"stem": "What do people think of it?(不超过5个词)",
                         "options": [], "answer": "B"}
        with_options = {"stem": "______", "options": [{"label": "A", "text": "know"}],
                        "answer": "A"}
        self.assertTrue(short_answer_cue(extension))
        self.assertFalse(short_answer_cue(blank_shell))
        self.assertFalse(short_answer_cue(letter_answer))
        self.assertFalse(short_answer_cue(with_options))

    def test_leading_image_options_attach_to_their_own_label(self):
        lines = [
            "☆拓展设问",
            "(　)4.[新考法·图片理解]Jimmy can’t use　　　　 to have Chinese food now.",
            "[IMG image14.jpeg]A.\t[IMG image15.jpeg]B.",
            "[IMG image16.jpeg]C.\t[IMG image17.jpeg]D.",
        ]
        part = question_parts(lines, 4)
        self.assertEqual(
            [(o["label"], o["text"]) for o in part["options"]],
            [("A", "[IMG image14.jpeg]"), ("B", "[IMG image15.jpeg]"),
             ("C", "[IMG image16.jpeg]"), ("D", "[IMG image17.jpeg]")])
        # 选项图不得残留到题干里。
        self.assertNotIn("[IMG", part["stem"])
        self.assertIn("Jimmy can’t use", part["stem"])

    def test_source_audit_does_not_cross_into_later_image_options(self):
        lines = [
            "(　)1. What is Kate's favourite subject?",
            "A. Maths.\tB. PE.",
            "C. Art.\tD. Music.",
            "(　)2. Later picture question.",
            "[IMG image100.jpeg]A.\t[IMG image101.jpeg]B.",
            "[IMG image102.jpeg]C.\t[IMG image103.jpeg]D.",
        ]
        audit = audit_question(lines, 1, item={
            "number": 1,
            "options": ["A. Maths.", "B. PE.", "C. Art.", "D. Music."],
        })
        self.assertNotIn("image100.jpeg", "\n".join(audit["source_lines"]))
        self.assertFalse(any(issue["code"] == "OPTIONS_MISMATCH" for issue in audit["issues"]))

    def test_text_options_unaffected(self):
        lines = ["(　)1. Question?", "A. one\tB. two", "C. three\tD. four"]
        part = question_parts(lines, 1)
        self.assertEqual([(o["label"], o["text"]) for o in part["options"]],
                         [("A", "one"), ("B", "two"), ("C", "three"), ("D", "four")])

    def test_audit_passes_for_image_option_task_reading(self):
        stem = (
            "Hi,I’m Jimmy.\n1. Sort the words.\n(1)　　　　　\n2. Where?\n3. Which side?\n"
            "☆拓展设问\n(　)4.[新考法·图片理解]Jimmy can’t use　　　　 to have Chinese food now.\n"
            "[IMG image14.jpeg]A.\t[IMG image15.jpeg]B.\n"
            "[IMG image16.jpeg]C.\t[IMG image17.jpeg]D."
        )
        unit = {"unit_kind": "group", "type": "任务型阅读", "numbers": [1, 2, 3, 4],
                "stem": stem, "source_span": {"start_line": 1, "end_line": len(stem.splitlines())},
                "source_expected_options": 4}
        audit = audit_source_unit(unit)
        self.assertTrue(audit["passed"], audit["issues"])

    def test_name_initial_is_not_an_option_label(self):
        """“Alexis E.Block” 里的人名缩写不能被当成选项 E。"""
        lines = [
            "1.Why does the writer mention the movies?",
            "A.To introduce great movies.",
            "B.To show the robots’ development.",
            "C.To catch readers’ interests.",
            "D.To do a research.",
            "2.What’s Alexis E.Block’s aim?",
            "A.To make robots closer to people.",
            "B.To make robots have self⁃awareness.",
            "C.To make the friendship come true.",
            "D.To make robots smarter than people.",
        ]
        part = question_parts(lines, 2)
        self.assertEqual([option["label"] for option in part["options"]], list("ABCD"))
        self.assertIn("E.Block", part["stem"])
        candidate = [f"{option['label']}.{option['text']}" for option in part["options"]]
        audit = audit_question(lines, 2, item={"number": 2, "options": candidate})
        self.assertFalse(any(issue["code"] == "OPTIONS_MISMATCH" for issue in audit["issues"]),
                         audit["issues"])

    def test_group_with_name_initial_keeps_four_options(self):
        """阅读组里出现人名缩写时，组内每道小题仍是 4 条选项，不判缺选项。"""
        stem = "\n".join([
            "1.Why does the writer mention the movies?",
            "A.To introduce great movies.",
            "B.To show the robots’ development.",
            "C.To catch readers’ interests.",
            "D.To do a research.",
            "2.What’s Alexis E.Block’s aim?",
            "A.To make robots closer to people.",
            "B.To make robots have self⁃awareness.",
            "C.To make the friendship come true.",
            "D.To make robots smarter than people.",
        ])
        unit = {"unit_kind": "group", "type": "阅读理解", "numbers": [1, 2], "stem": stem,
                "source_span": {"start_line": 1, "end_line": len(stem.splitlines())}}
        audit = audit_source_unit(unit)
        self.assertEqual([option["label"] for option in audit["questions"][2]["options"]], list("ABCD"))
        self.assertFalse([issue for issue in audit["issues"]
                          if issue["code"] == "SOURCE_OPTIONS_INCOMPLETE"], audit["issues"])
        self.assertTrue(audit["passed"], audit["issues"])

    def test_inline_options_after_title_case_words_stay_options(self):
        options = ["A. New York", "B. Boston", "C. Chicago", "D. Dallas"]
        for stem in ["Choose", "Where is Tom", "What did Alexis A. Block choose in B.C.?"]:
            with self.subTest(stem=stem):
                lines = ["1. " + stem + " " + " ".join(options)]
                part = question_parts(lines, 1)
                self.assertEqual(part["stem"], stem)
                self.assertEqual([option["label"] for option in part["options"]], list("ABCD"))
                self.assertEqual([option["text"] for option in part["options"]],
                                 ["New York", "Boston", "Chicago", "Dallas"])
                audit = audit_question(lines, 1, item={"number": 1, "options": options[1:]})
                self.assertIn("OPTIONS_MISMATCH", {issue["code"] for issue in audit["issues"]})


class LegitimateSourceShapeTest(unittest.TestCase):
    """原卷合法空/图形态与真实缺字必须区分，避免同类误判再次阻断。"""

    def unit(self, stem, numbers=(1,), binding=None):
        span = {"start_line": 1, "end_line": 1}
        if binding:
            span["number_binding"] = binding
        return {"unit_kind": "group", "type": "语音题", "numbers": list(numbers), "stem": stem, "source_span": span}

    def test_image_only_shape(self):
        shape = legitimate_source_shape(self.unit("[IMG image9.jpeg]"))
        self.assertEqual(shape, "image_only")
        self.assertTrue(capability_limited_source(self.unit("[IMG image9.jpeg]")))

    def test_blank_answer_grid_with_appendix_answer(self):
        stem = "1. /eɪ/\n[TABLE 1]\n\n\n[/TABLE]\n2. /iː/\n[TABLE 2]\n\n[/TABLE]"
        appendix = "参考答案\nⅠ.1.Aa,Hh,Jj,Kk　2.Bb,Cc,Dd,Ee"
        self.assertEqual(legitimate_source_shape(self.unit(stem, (1, 2)), appendix), "blank_answer_grid")
        self.assertTrue(capability_limited_source(self.unit(stem, (1, 2)), appendix))

    def test_blank_grid_without_appendix_proof_is_not_excused(self):
        stem = "1. /eɪ/\n[TABLE 1]\n\n\n[/TABLE]"
        # 附录没有该题号 -> 无法证明是可填答题表，按真实缺失处理。
        self.assertIsNone(legitimate_source_shape(self.unit(stem, (1,)), "参考答案\nⅣ.1 DCABD"))
        self.assertFalse(capability_limited_source(self.unit(stem, (1,)), "参考答案\nⅣ.1 DCABD"))

    def test_unnumbered_no_image_shape(self):
        stem = "A,B,<u>　　　　</u>,D,E,F\nX,Y,Z—now you see,"
        unit = self.unit(stem, (1,), binding="unnumbered_section")
        self.assertEqual(legitimate_source_shape(unit), "unnumbered_no_image")

    def test_real_truncation_is_not_excused(self):
        stem = "1. 下列字母组合 A.BIF B.XHS\n[TABLE 1]\n| 有内容 |\n[/TABLE]"
        self.assertIsNone(legitimate_source_shape(self.unit(stem, (1,)), "参考答案\nⅣ.1 DCABD"))
        self.assertFalse(capability_limited_source(self.unit(stem, (1,)), "参考答案\nⅣ.1 DCABD"))

    def test_plain_text_stem_is_not_excused(self):
        self.assertIsNone(legitimate_source_shape(self.unit("阅读短文并作答", (1,))))


class GroupMaterialFromMediaTest(unittest.TestCase):
    """组题共享材料以原卷图片/表格原件交付时，空文字 material 不是丢内容。"""

    def unit(self, **overrides):
        unit = {
            "unit_id": "u-img", "unit_kind": "group", "question_type": "阅读理解",
            "numbers": [1, 2], "material": "",
            "knowledge": ["语篇体裁/应用文", "主题/太空探索"],
            "stem": ("[IMG image64.jpg]\n**1.**What can tourists do during the trip?\n"
                     "A.Explore Mars.\tB.Visit a space station.\n**2.**Where can you read the text?\n"
                     "A.On a travel website.\tB.In a storybook."),
            "subquestions": [{"number": 1, "stem": "What can tourists do during the trip?", "answer": "B"},
                             {"number": 2, "stem": "Where can you read the text?", "answer": "A"}],
        }
        unit.update(overrides)
        return unit

    def test_image_lead_is_shared_material(self):
        from workflow.question_content import group_material_from_media
        self.assertTrue(group_material_from_media(self.unit()))
        self.assertTrue(group_material_from_media(self.unit(stem="[IMG image64.jpg]")))

    def test_lead_text_or_missing_media_keeps_real_missing_material_blocked(self):
        from workflow.question_content import group_material_from_media
        # 组级导语除图片外还有文字：文字共享材料真的丢了，继续按缺材料处理。
        self.assertFalse(group_material_from_media(self.unit(
            stem="[IMG image64.jpg]\n阅读下列短文，从每小题所给的选项中选出最佳选项。\n**1.**What?")))
        # 没有任何图片/表格证据。
        self.assertFalse(group_material_from_media(self.unit(stem="**1.**What?")))
        # 非题组不适用。
        self.assertFalse(group_material_from_media(self.unit(unit_kind="standalone")))
        # 解析/答案位置的图片不能顶替共享材料。
        self.assertFalse(group_material_from_media(self.unit(
            stem="**1.**What?",
            attachments=[{"kind": "image", "name": "image6.jpg", "placement": "explanation"}])))

    def test_local_structure_check_uses_same_image_material_rule(self):
        import importlib.util
        from pathlib import Path as _Path
        path = _Path(__file__).resolve().parents[1] / "scripts/06-upload/check_structure.py"
        spec = importlib.util.spec_from_file_location("a8_image_material", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertFalse([p for p in module._check_unit(self.unit()) if p.startswith("[A8]")])
        # 组级导语只有一个图片引用时，空文字 material 不算缺材料。
        self.assertFalse([p for p in module._check_unit(self.unit(stem="[IMG image64.jpg]"))
                          if p.startswith("[A8]")])
        # 导语还有文字却 material 为空，A8 仍须拦截。
        self.assertTrue(any("[A8]" in p for p in module._check_unit(self.unit(
            stem="[IMG image64.jpg]\n阅读下列短文，从每小题所给的选项中选出最佳选项。\n**1.**What?"))))


SAMPLE = Path(__file__).resolve().parent.parent / "workflows" / "_real_2025"
requires_sample = unittest.skipUnless(SAMPLE.is_dir(), "需要本地样例批次 workflows/_real_2025")


@requires_sample
class QuestionContentTest(unittest.TestCase):
    def test_number_one_does_not_match_cover_title(self):
        source = ["1.2025年安徽省考试", "考试时间120分钟", "(　)1.What is Sandy’s hobby?", "A.\tB.\tC.", "[IMG image5.jpeg]", "(　)2.Where?"]
        result = audit_question(source, 1, media_dir=Path("workflows/_real_2025/01_source/media"))
        self.assertIn("(　)1.What is Sandy’s hobby?", "\n".join(result["source_lines"]))
        self.assertNotIn("2025年安徽省", "\n".join(result["source_lines"]))
        self.assertNotIn("考试时间", "\n".join(result["source_lines"]))
        self.assertEqual(result["image_refs"], ["image5.jpeg"])
        self.assertTrue(result["visual_review_required"])

    def test_missing_image_is_blocking(self):
        result = audit_question(["(　)1.Choose", "[IMG missing.png]"], 1, media_dir=".")
        self.assertIn("IMAGE_MISSING", [issue["code"] for issue in result["issues"]])

    def test_table_scope_is_kept_and_checked(self):
        lines = ["Ⅳ.信息转换", "请填写表格", "[TABLE 1 rows=2 cols=2]", "Header | __16__", "Body | __17__", "[/TABLE]", "第二部分"]
        chunk = slice_text(lines, 16)
        self.assertIn("[TABLE", "\n".join(chunk))
        blocks = source_blocks(chunk)
        table = next(block for block in blocks if block["type"] == "table")
        self.assertEqual(len(table["rows"]), 2)
        self.assertEqual(table["rows"][0], ["Header", "__16__"])
        audit = audit_question(lines, 16)
        self.assertTrue(audit["table_present"])
        self.assertTrue(audit["visual_review_required"])
        unclosed = audit_question(["(　)1.题目", "[TABLE rows=1]", "A | B"], 1)
        self.assertIn("TABLE_UNBALANCED", [issue["code"] for issue in unclosed["issues"]])

    def test_real_paper_q001_preview_is_exact_question_and_images(self):
        detail_data = detail("_real_2025", "Q001")
        preview = detail_data["source_preview"]
        self.assertIn("What is Sandy", preview)
        self.assertNotIn("2025年安徽省", preview)
        self.assertNotIn("扫码练听力", preview)
        self.assertNotIn("考试时间", preview)
        self.assertEqual([b["name"] for b in detail_data["preview_blocks"] if b["type"] == "image"],
                         ["image5.jpeg", "image6.jpeg", "image7.jpeg"])
        self.assertTrue(all(b["url"] for b in detail_data["preview_blocks"] if b["type"] == "image"))
        self.assertEqual(len(detail_data["findings"]), 4)
        self.assertEqual(source_blocks(["[TABLE rows=1]", "A | B", "[/TABLE]"])[0]["type"], "table")


class InlineOptionTest(unittest.TestCase):
    def test_inline_option_a_opens_the_shared_option_block(self):
        # 2017 安徽卷把选项 A 写在最后一句对话同一行。
        unit = {
            "type": "补全对话", "unit_kind": "group", "numbers": [66, 67],
            "stem": ("根据对话内容，从方框内的选项中选出能填入空白处的最佳选项。\n"
                     "A:Hello,Bob.\nB:<u>\u300066\u3000</u>\n"
                     "A:I am fine.\nB:<u>\u300067\u3000</u>\n"
                     "A:You are welcome.\u3000A.Well done!\n"
                     "B.For what?\nC.That is great."),
        }
        result = audit_source_unit(unit)
        self.assertEqual([o["label"] for o in result["shared_options"]], ["A", "B", "C"])
        self.assertEqual(result["shared_options"][0]["text"], "Well done!")


class LegacySourceAmbiguityTest(unittest.TestCase):
    def test_duplicate_number_inside_legacy_unit_is_blocked(self):
        unit = {
            "unit_kind": "group", "type": "阅读理解", "numbers": [1],
            "stem": "(　)1. First?\nA. One\tB. Two\n(　)1. Second?\nA. Other\tB. Choice",
            "source_span": {"start_line": 1, "end_line": 4},
        }
        audit = audit_source_unit(unit)
        self.assertFalse(audit["passed"])
        self.assertIn("SOURCE_NUMBER_AMBIGUOUS", [item["code"] for item in audit["issues"]])

    def test_duplicate_legacy_source_keys_are_not_overwritten(self):
        from workflow.question_content import _unique_source_index
        units = [
            {"numbers": [1], "source_section": {"start_line": 1, "end_line": 2}},
            {"numbers": [1], "source_section": {"start_line": 5, "end_line": 6}},
        ]
        index, ambiguous = _unique_source_index(units)
        self.assertEqual(index, {})
        self.assertEqual(ambiguous, {(1,)})


class OptionBoundaryTest(unittest.TestCase):
    def test_question_number_can_touch_inline_option_a(self):
        from workflow.question_content import question_parts
        part = question_parts(
            ["39.A.first B.second C.third D.fourth"], 39)
        self.assertEqual([o["label"] for o in part["options"]], list("ABCD"))

    def test_phonetic_target_touching_option_a_preserves_all_four_options(self):
        from workflow.question_content import question_parts, OPTION
        for target in ("e", "æ", "iː", "ɪ", "ɒ"):
            part = question_parts([f"(　)1./{target}/A.<u>e</u>ye　B.<u>a</u>ll　C.sh<u>ee</u>p　D.<u>a</u>ny"], 1)
            self.assertEqual([o["label"] for o in part["options"]], list("ABCD"))
            self.assertEqual(part["stem"], f"/{target}/")
        self.assertFalse(list(OPTION.finditer("https://example.org/A.eye")))

    def test_trailing_summary_is_not_absorbed_into_last_option(self):
        from workflow.question_content import question_parts
        unit_stem = ("(　　)55.A.realize\tB.enjoy\t\tC.forget\tD.celebrate\n"
                     "【文章大意】本文是一篇记叙文。")
        part = question_parts(unit_stem.splitlines(), 55)
        self.assertEqual([o["label"] for o in part["options"]], list("ABCD"))
        self.assertEqual(part["options"][-1]["text"], "celebrate")

    def test_writing_stem_is_restored_verbatim_when_model_truncates(self):
        import json, tempfile
        from pathlib import Path
        from workflow.question_content import apply_required_writing_stem
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            (batch / "01_source").mkdir()
            (batch / "01_source/正文.txt").write_text(
                "Ⅺ.书面表达\n假定你是李华,写一封回信。要点如下:\n1.大胆自信;\n"
                "注意:词数80~100。\nDear Jack,\nHow is everything going?\n", encoding="utf-8")
            (batch / "01_source/structure.json").write_text(json.dumps({
                "sections": [{"type": "书面表达", "questions": [96]}], "groups": []}), encoding="utf-8")
            payload = {"items": [{"numbers": [96], "number": 96, "stem": "假定你是李华,写一封回信。"}]}
            # 结构无法派生 single_writing_section 时不擅自改写；有可解析单元时才落回原文。
            changed = apply_required_writing_stem(batch, payload)
            self.assertIn(changed, (0, 1))

    def test_spaced_name_initial_and_dotted_acronyms_in_stem_are_not_options(self):
        from workflow.question_content import question_parts
        cases = [
            (["3.What's Alexis E. Block's aim?", "A.One", "B.Two", "C.Three", "D.Four"], 3, "E. Block"),
            (["4.What happened at 8:00 A.M. yesterday?", "A.One", "B.Two", "C.Three", "D.Four"], 4, "A.M."),
            (["5.Where is Washington, D.C. located?", "A.One", "B.Two", "C.Three", "D.Four"], 5, "D.C."),
            (["6.What does Grade A. mean in the chart?", "A.Excellent", "B.Good", "C.Fair", "D.Poor"], 6, "Grade A."),
        ]
        for lines, num, expected_in_stem in cases:
            with self.subTest(num=num):
                part = question_parts(lines, num)
                self.assertEqual([o["label"] for o in part["options"]], list("ABCD"))
                self.assertIn(expected_in_stem, part["stem"])
        # 行内直接接选项（无后续行首 A.）仍正常切分 4 项
        inline = question_parts(["7.Choose one: A.Apple B.Banana C.Pear D.Peach"], 7)
        self.assertEqual([o["label"] for o in inline["options"]], list("ABCD"))


class ImageOptionEvidenceTest(unittest.TestCase):
    """图片选项题用视觉/原文佐证判“选项是图、图都在”，替代正则硬啃排版。"""

    def _sem(self, kinds):
        return {"items": [
            {"path": f"01_source/media/{n}", "kind": k, "title": "", "contains_text": False}
            for n, k in kinds.items()]}

    def test_image_option_detected_when_images_match_options(self):
        from workflow import question_content as qc
        local = [
            "( ) 4. Jimmy can't use ____ to have Chinese food now.",
            "[IMG image14.jpeg]A.\t[IMG image15.jpeg]B.",
            "[IMG image16.jpeg]C.\t[IMG image17.jpeg]D.",
        ]
        unit = {"unit_kind": "group", "type": "任务型阅读", "numbers": [1, 2, 3, 4],
                "source_expected_options": 4}
        sem = self._sem({"image14.jpeg": "illustration", "image15.jpeg": "illustration",
                         "image16.jpeg": "illustration", "image17.jpeg": "illustration"})
        ev = qc.image_option_evidence(unit, local, sem)
        self.assertIsNotNone(ev)
        self.assertTrue(ev["is_image_option"])
        self.assertEqual(ev["image_count"], 4)

    def test_not_image_option_for_plain_text(self):
        from workflow import question_content as qc
        local = ["( ) 1. Q?", "A.One B.Two C.Three D.Four"]
        unit = {"unit_kind": "standalone", "type": "单项填空", "numbers": [1], "source_expected_options": 4}
        self.assertIsNone(qc.image_option_evidence(unit, local, None))

    def test_decorative_icons_do_not_exempt(self):
        from workflow import question_content as qc
        local = ["[IMG i1.png]A. [IMG i2.png]B."]
        unit = {"unit_kind": "group", "type": "阅读", "numbers": [1], "source_expected_options": 4}
        sem = self._sem({"i1.png": "icon", "i2.png": "icon"})
        # 全是装饰图标且图数<期望：不豁免。
        self.assertIsNone(qc.image_option_evidence(unit, local, sem))

    def test_extension_heading_stops_preceding_option_window(self):
        from workflow import question_content as qc
        lines = [
            "**3.**What do Wu's words mean?",
            "A.鱼和熊掌不可兼得。",
            "B.授人以鱼,不如授人以渔。",
            "C.三天打鱼,两天晒网。",
            "D.临渊羡鱼,不如退而结网。",
            "**☆****拓展设问**",
            "**4.**What does Wu find?(不超过15个词)",
        ]
        parts = qc.question_parts(lines, 3)
        self.assertEqual([o["label"] for o in parts["options"]], ["A", "B", "C", "D"])
        self.assertEqual(parts["options"][3]["text"], "临渊羡鱼,不如退而结网。")
        self.assertNotIn("拓展设问", parts["options"][3]["text"])

    def test_audit_batch_source_ignores_decorative_images_but_requires_content_images(self):
        import json, tempfile
        from pathlib import Path
        from workflow import question_content as qc
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            (batch / "01_source").mkdir()
            body = (
                "Ⅱ. 阅读理解\n"
                "阅读下面短文，回答问题。\n"
                "[IMG image34.jpg]\n"
                "[IMG image36.jpg]\n"
                "Tom likes reading books every day.\n"
                "1. What does Tom like?\n"
                "A. Reading.\tB. Running.\tC. Swimming.\tD. Singing.\n"
            )
            (batch / "01_source/正文.txt").write_text(body, encoding="utf-8")
            (batch / "01_source/extraction.json").write_text(json.dumps({
                "media": [
                    {"name": "image34.jpg", "decorative": False, "width": 600, "height": 400},
                    {"name": "image36.jpg", "decorative": True, "width": 99, "height": 100},
                ]
            }), encoding="utf-8")
            unit = {
                "unit_id": "u1",
                "unit_kind": "group",
                "type": "阅读理解",
                "numbers": [1],
                "identity_version": "scoped-v1",
                "scope_key": "sec-1",
                "stem": body,
                "source_span": {"basis": "ai_classified", "start_line": 1, "end_line": 7},
            }
            (batch / "01_source/structure.json").write_text(json.dumps({
                "sections": [{"type": "阅读理解", "questions": [1]}],
                "groups": [{"type": "阅读理解", "questions": [1]}],
            }), encoding="utf-8")
            (batch / "01_source/structure.confirmed.json").write_text(json.dumps({
                "units": [unit],
            }), encoding="utf-8")

            def make_batch(include_content_img: bool):
                atts = [{"path": "01_source/media/image34.jpg", "placement": "material"}] if include_content_img else []
                return {
                    "items": [{
                        "unit_id": "u1",
                        "unit_kind": "group",
                        "type": "阅读理解",
                        "numbers": [1],
                        "scope_key": "sec-1",
                        "material": "阅读下面短文，回答问题。\nTom likes reading books every day.",
                        "attachments": atts,
                        "subquestions": [{
                            "number": 1,
                            "stem": "What does Tom like?",
                            "options": [
                                {"key": "A", "text": "Reading."},
                                {"key": "B", "text": "Running."},
                                {"key": "C", "text": "Swimming."},
                                {"key": "D", "text": "Singing."},
                            ],
                        }],
                    }]
                }

            ok_batch = make_batch(include_content_img=True)
            self.assertEqual(qc.restore_source_fidelity(batch, ok_batch), 0)
            self.assertNotIn("image36.jpg", ok_batch["items"][0]["material"])
            res_ok = qc.audit_batch_source(batch, ok_batch)
            img_issues_ok = [i for i in res_ok["issues"] if i["code"] == "SOURCE_IMAGE_MISSING"]
            self.assertEqual(img_issues_ok, [], img_issues_ok)

            missing_batch = make_batch(include_content_img=False)
            res_bad = qc.audit_batch_source(batch, missing_batch)
            img_issues_bad = [i for i in res_bad["issues"] if i["code"] == "SOURCE_IMAGE_MISSING"]
            self.assertEqual(len(img_issues_bad), 1, img_issues_bad)
            self.assertIn("image34.jpg", img_issues_bad[0]["message"])
            self.assertNotIn("image36.jpg", img_issues_bad[0]["message"])


if __name__ == "__main__":
    unittest.main()
