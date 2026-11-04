# -*- coding: utf-8 -*-
import unittest
from pathlib import Path

from workflow.question_content import audit_question, audit_source_unit
from workflow.preview import slice_text
from workbench.server import detail, source_blocks


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


if __name__ == "__main__":
    unittest.main()
