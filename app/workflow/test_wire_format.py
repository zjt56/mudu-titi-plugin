"""装配产物与题库线上格式的确定性映射。"""
import unittest

from workflow.wire_format import normalize_item, normalize_blank_text


class WireFormatTest(unittest.TestCase):
    def test_options_key_alias_and_string_array(self):
        item = {"unit_kind": "standalone", "options": [{"key": "A", "text": "go"},
                                                       {"label": "B", "text": "see"}]}
        out = normalize_item(item)
        self.assertEqual(out["options"], ["A. go", "B. see"])

    def test_year_string_and_filter_alias_stay_synced(self):
        item = {"unit_kind": "standalone", "year": "2025",
                "filterMetadata": {"year": "2025", "categories": ["优选题", "未提供"]}}
        out = normalize_item(item)
        self.assertEqual(out["year"], 2025)
        self.assertEqual(out["filterMetadata"]["year"], 2025)
        self.assertEqual(out["filterMetadata"]["categories"], ["优选题"])

    def test_source_lifted_from_filter_without_guessing(self):
        item = {"unit_kind": "standalone", "filterMetadata": {"source": "2025 安徽卷",
                                                              "source_type": "中考真题"}}
        out = normalize_item(item, meta={"source": "不该使用", "source_type": "中考模拟"})
        self.assertEqual(out["source"], "2025 安徽卷")
        self.assertEqual(out["source_type"], "中考真题")
        self.assertEqual(out["filterMetadata"]["source"], "2025 安徽卷")

    def test_missing_source_uses_identified_metadata(self):
        item = {"unit_kind": "standalone"}
        out = normalize_item(item, meta={"source": "2025年安徽省初中学业水平考试",
                                         "source_type": "中考真题", "year": 2025})
        self.assertEqual(out["source"], "2025年安徽省初中学业水平考试")
        self.assertEqual(out["source_type"], "中考真题")
        self.assertEqual(out["year"], 2025)

    def test_image_markers_become_attachments_everywhere(self):
        item = {"unit_kind": "standalone", "source": "[IMG p1.jpeg]2025 安徽卷",
                "stem": "见下图 [IMG p1.jpeg]", "options": ["A. [IMG p2.jpeg]", "B. text"],
                "attachments": []}
        media = {"p1.jpeg": "/source-media/b/p1.jpeg", "p2.jpeg": "/source-media/b/p2.jpeg"}
        out = normalize_item(item, media_map=media)
        self.assertNotIn("[IMG", out["source"])
        self.assertNotIn("[IMG", out["stem"])
        self.assertEqual(out["options"], ["A.", "B. text"])
        kinds = {(a["url"], a.get("optionKey")) for a in out["attachments"]}
        self.assertIn(("/source-media/b/p2.jpeg", "A"), kinds)
        self.assertTrue(any(a["url"].endswith("p1.jpeg") for a in out["attachments"]))

    def test_audio_attachment_from_audio_map(self):
        item = {"unit_id": "q-1", "unit_kind": "standalone", "type": "听力短对话理解",
                "attachments": []}
        media = {"seg-q-1.mp3": "/source-media/b/seg-q-1.mp3"}
        out = normalize_item(item, media_map=media, audio={"q-1": "02_audio/seg-q-1.mp3"})
        self.assertEqual([(a["kind"], a["url"]) for a in out["attachments"]],
                         [("audio", "/source-media/b/seg-q-1.mp3")])

    def test_material_image_uses_question_placement(self):
        # 题库只渲染 question/option/answer/explanation；材料图片若带
        # placement=material，逐题确认会被整批拒绝为 invalid_attachment。
        item = {"unit_kind": "group", "stem": "阅读短文", "material": "见图 [IMG p1.jpeg]"}
        out = normalize_item(item, media_map={"p1.jpeg": "/source-media/b/p1.jpeg"})
        self.assertEqual([(a["kind"], a["placement"], a["url"]) for a in out["attachments"]],
                         [("image", "question", "/source-media/b/p1.jpeg")])
        self.assertNotIn("[IMG", out["material"])

    def test_group_material_filled_from_confirmed_by_numbers(self):
        item = {"unit_id": "g", "unit_kind": "group", "numbers": [6, 7],
                "stem": "听下面一段对话。", "material": ""}
        materials = {tuple([6, 7]): "听下面一段对话。\nM: Hello."}
        out = normalize_item(item, materials=materials)
        self.assertEqual(out["material"], "M: Hello.")


    def test_structured_option_dict_binds_image_by_key(self):
        # 图片前置选项题：options 为 {key,text:[IMG]}, 附件须按 key 绑到对应选项，
        # 而不是全部落到 None（旧 bug 导致 A 拿下一张图、D 空、整体错位一格）。
        item = {"unit_kind": "group", "subquestions": [],
                "options": [{"key": k, "text": f"[IMG image{n}.jpeg]"}
                            for k, n in (("A", 14), ("B", 15), ("C", 16), ("D", 17))]}
        mm = {f"image{n}.jpeg": f"01_source/media/image{n}.jpeg" for n in (14, 15, 16, 17)}
        out = normalize_item(item, media_map=mm)
        self.assertEqual(out["options"], ["A.", "B.", "C.", "D."])
        bound = {a["optionKey"]: a["name"] for a in out["attachments"] if a.get("optionKey")}
        self.assertEqual(bound, {"A": "image14.jpeg", "B": "image15.jpeg",
                                 "C": "image16.jpeg", "D": "image17.jpeg"})
        self.assertNotIn(None, [a.get("optionKey") for a in out["attachments"]])

    def test_blank_normalization_removes_inline_underline_ambiguity(self):
        # <u>词______</u>（词形填空）曾原样带着标签内连续下划线发出；任何把 __x__ 当
        # 下划线标记的宽松渲染器会在 <u> 内再套 <u>，显示成「很短、一粗一细两条线」。
        # 规范为 <u>词</u>______：词仍画线、空档是一条填空线，任何渲染器都只画一条线。
        self.assertEqual(normalize_blank_text("clear <u>i______</u>(想法) of"),
                         "clear <u>i</u>______(想法) of")
        self.assertEqual(normalize_blank_text("<u>r______</u>"), "<u>r</u>______")
        # 拆分后紧邻的 6+ 连续下划线合并成一处置空。
        self.assertEqual(normalize_blank_text("<u>w______</u>______(祝愿)"),
                         "<u>w</u>______(祝愿)")

    def test_blank_normalization_preserves_legitimate_forms(self):
        # 普通画线词、编号空、纯空档、词间空档都保持原口径，不被新规则破坏。
        self.assertEqual(normalize_blank_text("<u>fine</u>"), "<u>fine</u>")
        self.assertEqual(normalize_blank_text("<u>o</u>"), "<u>o</u>")
        self.assertEqual(normalize_blank_text("<u>26</u>"), "__26__")
        self.assertEqual(normalize_blank_text("<u> </u>"), "______")
        self.assertEqual(normalize_blank_text("______ are ______?"), "______ are ______?")
        self.assertEqual(normalize_blank_text("____1____"), "____1____")
        self.assertEqual(normalize_blank_text("to be __17__ and"), "to be __17__ and")
        # 结果本身不再含「<u> 内套下划线」的歧义形态。
        for raw in ["<u>i______</u>", "<u>t______</u>"]:
            self.assertNotRegex(normalize_blank_text(raw), r"<u>[^<]*_{3,}")

if __name__ == "__main__":
    unittest.main()
