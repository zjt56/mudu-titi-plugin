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


if __name__ == "__main__":
    unittest.main()
