"""装配产物与题库线上格式的确定性映射。"""
import unittest

from workflow.wire_format import normalize_item


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


if __name__ == "__main__":
    unittest.main()
