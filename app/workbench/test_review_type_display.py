"""审核页必须直接显示目录题型：缺题型时按同步课时规则回填，已判定的不覆盖。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workbench import server

LESSON_TEXT = """课时A计划 Starter Unit 1 Hello! 第一课时 Section A
Ⅰ.按照字母表顺序,写出空白处所缺字母
[IMG image2.jpeg]
Ⅱ.按照名字的字母顺序为下列人物排序,并按照性别进行分类
1.Boys' names:
2.Girls' names:
Ⅲ.根据首字母及汉语提示补全单词
1.好的 g____
Ⅳ.根据汉语意思完成句子,每空一词
1.这是我的书。 This is ____ book.
参考答案
Ⅲ.1.good
"""


class ReviewTypeDisplayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        source = self.folder / "01_source"
        source.mkdir(parents=True)
        (source / "正文.txt").write_text(LESSON_TEXT, encoding="utf-8")
        (self.folder / "workflow").mkdir()
        (self.folder / "02_audio").mkdir()

    def rows(self):
        return [
            {"unit_id": "u1", "numbers": [1],
             "source_section": {"title": "Ⅲ.根据首字母及汉语提示补全单词"}},
            {"unit_id": "u2", "numbers": [1], "type": "完成句子", "subtype": "汉语提示",
             "source_section": {"title": "Ⅳ.根据汉语意思完成句子,每空一词"}},
            {"unit_id": "u3", "numbers": [1],
             "source_section": {"title": "Ⅱ.按照名字的字母顺序为下列人物排序,并按照性别进行分类"}},
            {"unit_id": "u4", "numbers": [1], "type": "单词拼写",
             "source_section": {"title": "Ⅲ.根据首字母及汉语提示补全单词"}},
        ]

    def decorate(self, rows):
        with patch.object(server, "review_snapshot", return_value=({}, {})):
            server.decorate_review_rows(self.folder, rows)
        return rows

    def test_missing_type_and_subtype_come_from_the_section_rule(self):
        rows = self.decorate(self.rows())
        self.assertEqual(rows[0]["type"], "单词拼写")
        self.assertEqual(rows[0]["subtype"], "汉语及首字母")
        self.assertEqual(rows[0]["subtypes"], ["汉语及首字母"])
        self.assertEqual(rows[0]["type_from"], "section_rule")
        # 语音题在目录里没有子类，只能记题型本身，不得自造子类。
        self.assertEqual(rows[2]["type"], "语音题")
        self.assertEqual(rows[2]["subtypes"], [])

    def test_judged_type_is_never_overwritten_but_missing_subtype_is_filled(self):
        rows = self.decorate(self.rows())
        self.assertEqual(rows[1]["type"], "完成句子")
        self.assertEqual(rows[1]["subtype"], "汉语提示")
        self.assertNotIn("type_from", rows[1])
        # 题型已判定（来自批次），子类缺失时按同一大题标题补齐。
        self.assertEqual(rows[3]["type"], "单词拼写")
        self.assertEqual(rows[3]["subtype"], "汉语及首字母")
        self.assertNotIn("type_from", rows[3])

    def test_type_is_never_the_heading_itself(self):
        rows = self.decorate(self.rows())
        titles = {str((row.get("source_section") or {}).get("title") or "") for row in rows}
        for row in rows:
            self.assertTrue(str(row.get("type") or "").strip())
            self.assertNotIn(row["type"], titles)
            self.assertNotEqual(row["type"], row["source_section"]["title"])


if __name__ == "__main__":
    unittest.main()
