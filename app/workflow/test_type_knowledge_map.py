"""锁定《题型-知识点标签对照表1.0》的机器可读投影。

标签不能跨题型串用：每个题型只能打 by_type.dims 列出的维度。
这里只做纯标准库断言，不引入 openpyxl（插件后端依赖里没有它）。
原表 xlsx 与它逐字对应，改表时必须同步本 JSON。
"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAP_PATH = ROOT / "references" / "type-knowledge-map.json"
XLSX_PATH = ROOT / "references" / "题型-知识点标签对照表1.0.xlsx"

# 对照表 1.0 的 27 个题型
EXPECTED_TYPES = {
    "听力短对话理解", "听力长对话理解", "听力短文理解", "听力信息转换", "听力判断",
    "听力排序", "听力问答", "听力关键词语选择", "听力其他", "语音题", "单项填空",
    "完形填空", "语法选择", "阅读理解", "任务型阅读", "教材语篇填空", "短文填空",
    "选词填空", "补全对话", "单词拼写", "完成句子", "情景运用", "翻译", "改错",
    "读写综合", "书面表达", "其他",
}

# 允许出现的维度（表的「考察知识点」列只会用到这些）
KNOWN_DIMS = {"语篇体裁", "主题", "设题角度", "答案考点", "语音", "词汇", "语法", "语用", "阅读技能"}


class TypeKnowledgeMapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(MAP_PATH.read_text(encoding="utf-8"))
        cls.by_type = cls.data["by_type"]

    def dims(self, main_type):
        return [d.get("dim") for d in self.by_type[main_type]["dims"]]

    def test_covers_every_type_in_the_table(self):
        self.assertEqual(set(self.by_type), EXPECTED_TYPES)

    def test_dims_only_use_known_dimensions(self):
        for main_type, rule in self.by_type.items():
            for dim in [d.get("dim") for d in rule["dims"]]:
                self.assertIn(dim, KNOWN_DIMS, main_type)

    def test_every_type_declares_at_least_one_dim(self):
        for main_type, rule in self.by_type.items():
            self.assertTrue(rule["dims"], main_type)

    def test_whole_passage_types_have_no_question_angle(self):
        # 完形填空/语法选择只到「语篇体裁+主题」，不含设题角度/词汇/语法。
        for main_type in ("完形填空", "语法选择"):
            self.assertEqual(self.dims(main_type), ["语篇体裁", "主题"], main_type)

    def test_reading_types_keep_question_angle(self):
        for main_type in ("阅读理解", "任务型阅读", "读写综合"):
            self.assertEqual(self.dims(main_type), ["语篇体裁", "主题", "设题角度"], main_type)

    def test_listening_dialog_types_only_theme_and_angle(self):
        for main_type in ("听力短对话理解", "听力长对话理解"):
            self.assertEqual(self.dims(main_type), ["主题", "设题角度"], main_type)

    def test_lexical_types_do_not_carry_passage_dims(self):
        for main_type in ("单词拼写", "完成句子", "翻译", "改错"):
            dims = self.dims(main_type)
            self.assertNotIn("语篇体裁", dims, main_type)
            self.assertNotIn("主题", dims, main_type)
            self.assertNotIn("设题角度", dims, main_type)

    def test_source_workbook_is_shipped(self):
        # 原表随插件分发，便于核对与后续升版。
        self.assertTrue(XLSX_PATH.is_file(), "references 下应保留对照表原表")


if __name__ == "__main__":
    unittest.main()
