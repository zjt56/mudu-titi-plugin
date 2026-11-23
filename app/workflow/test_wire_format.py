"""装配产物与题库线上格式的确定性映射。"""
import json
from pathlib import Path
import struct
import tempfile
import unittest

from workflow.wire_format import decorative_names, normalize_item, normalize_blank_text


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

    def test_blank_normalization_preserves_fullwidth_column_separators(self):
        # 多栏并排的小题号与选项之间的全角空格是列分隔符，不能改写成 ______，
        # 否则下游 source_anchors 会丢失同行第 2/3/5 题锚点，引发 scoped answer 绑定失败。
        self.assertEqual(
            normalize_blank_text("c____/æ/t\u3000\u30002.[IMG image36.jpeg]"),
            "c____/æ/t\u3000\u30002.[IMG image36.jpeg]",
        )
        self.assertEqual(
            normalize_blank_text("____/e/gg\u3000\u3000**3.**[IMG image37.jpeg]"),
            "____/e/gg\u3000\u3000**3.**[IMG image37.jpeg]",
        )
        self.assertEqual(
            normalize_blank_text("1. foo\u3000\u3000(　)2. bar"),
            "1. foo\u3000\u3000(　)2. bar",
        )
        self.assertEqual(
            normalize_blank_text("A. map\u3000\u3000B. apple\u3000\u3000C. cat"),
            "A. map\u3000\u3000B. apple\u3000\u3000C. cat",
        )
        # 真正的句中全角空格空档仍正常归一为 ______，不影响同行后面的题号分隔。
        self.assertEqual(
            normalize_blank_text("1. My name is \u3000\u3000.\u3000\u30002. Her name is \u3000\u3000."),
            "1. My name is ______.\u3000\u30002. Her name is ______.",
        )

    def test_metadata_img_markers_are_stripped_without_creating_question_attachments(self):
        item = {
            "unit_kind": "standalone",
            "source": "[IMG image3.jpeg]1.2025年安徽省初中学业水平考试",
            "filterMetadata": {"source": "[IMG image3.jpeg]1.2025年安徽省初中学业水平考试"},
            "stem": "How long does it take John to get home?",
            "explanation": "见配图 [IMG image6.jpeg]",
        }
        media = {
            "image3.jpeg": "/source-media/b/image3.jpeg",
            "image6.jpeg": "/source-media/b/image6.jpeg",
        }
        out = normalize_item(item, media_map=media)
        self.assertEqual(out["source"], "1.2025年安徽省初中学业水平考试")
        self.assertEqual(out["filterMetadata"]["source"], "1.2025年安徽省初中学业水平考试")
        # image3.jpeg 只在元信息 source 中出现，绝不能生成 placement=question 附件；
        # image6.jpeg 在 explanation 中出现，保留为 placement=explanation 附件。
        atts = out.get("attachments") or []
        self.assertEqual([(a["name"], a["placement"]) for a in atts], [("image6.jpeg", "explanation")])

    def test_decorative_banners_pills_and_qr_filtered_across_all_placements_and_stale_attachments(self):
        def fake_png(width, height, total_bytes):
            head = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", width, height)
            return head + b"\x00" * max(0, total_bytes - len(head))

        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            source = batch / "01_source"
            media = source / "media"
            media.mkdir(parents=True)
            (media / "image3.png").write_bytes(fake_png(1091, 191, 13450))
            (media / "image4.png").write_bytes(fake_png(130, 130, 7956))
            (media / "image11.png").write_bytes(fake_png(260, 43, 4002))
            (media / "image10.png").write_bytes(fake_png(199, 69, 4500))
            (media / "image_pure.png").write_bytes(fake_png(1000, 100, 12000))
            (source / "metadata.json").write_text(json.dumps({
                "source": "[IMG image3.png]1.2025年安徽省初中学业水平考试",
                "paper_title": "[IMG image3.png]1.2025年安徽省初中学业水平考试",
            }), encoding="utf-8")
            (source / "正文.txt").write_text(
                "[IMG image4.png]\n扫码练听力\nⅠ.语音题(共1小题)\n[IMG image_pure.png]\n",
                encoding="utf-8")
            (source / "structure.confirmed.json").write_text(json.dumps({
                "units": [{"unit_id": "u-pure", "stem": "[IMG image_pure.png]", "numbers": [1]}]
            }), encoding="utf-8")

            decor = decorative_names(batch)
            self.assertEqual(decor, {"image3.png", "image4.png", "image11.png"})
            self.assertNotIn("image10.png", decor)
            self.assertNotIn("image_pure.png", decor)

            media_map = {p.name: f"/source-media/b/{p.name}" for p in media.iterdir()}
            item = {
                "unit_kind": "standalone",
                "stem": "[IMG image11.png]Which picture is on page 15?",
                "material": "A[IMG image11.png]",
                "options": ["A.[IMG image10.png]", "B.[IMG image11.png]"],
                "attachments": [
                    {"kind": "image", "name": "image3.png", "url": "/source-media/b/image3.png", "placement": "question"},
                    {"kind": "image", "name": "image11.png", "url": "/source-media/b/image11.png", "placement": "question"},
                ],
            }
            out = normalize_item(item, media_map=media_map, decorative_names=decor)
            self.assertEqual(out["stem"], "Which picture is on page 15?")
            self.assertEqual(out["material"], "A")
            self.assertEqual(out["options"], ["A.", "B."])
            atts = [(a["name"], a["placement"], a.get("optionKey")) for a in out.get("attachments") or []]
            self.assertEqual(atts, [("image10.png", "option", "A")])

    def test_restore_table_cell_images_pure_image_cells_and_merges(self):
        from workflow.wire_format import restore_table_cell_images

        source = (
            "[TABLE 1]\n"
            "[IMG image2.jpeg] | Jack is my classmate.\n"
            "[IMG image3.jpeg] | This girl is Sue.\n"
            "[IMG image4.jpeg] | Anna is 25.\n"
            "[/TABLE]\n"
        )
        blocks = [{
            "type": "table",
            "rows": [
                {"cells": [{"text": "", "rowspan": 1, "colspan": 1}, {"text": "Jack is my classmate.", "rowspan": 1, "colspan": 1}]},
                {"cells": [{"text": "", "rowspan": 1, "colspan": 1}, {"text": "This girl is Sue.", "rowspan": 1, "colspan": 1}]},
                {"cells": [{"text": "", "rowspan": 1, "colspan": 1}, {"text": "Anna is 25.", "rowspan": 1, "colspan": 1}]},
            ],
        }]
        restore_table_cell_images(blocks, source, allowed_images={"image2.jpeg", "image3.jpeg", "image4.jpeg"})
        self.assertEqual(blocks[0]["rows"][0]["cells"][0]["text"], "[IMG image2.jpeg]")
        self.assertEqual(blocks[0]["rows"][1]["cells"][0]["text"], "[IMG image3.jpeg]")
        self.assertEqual(blocks[0]["rows"][2]["cells"][0]["text"], "[IMG image4.jpeg]")

        # Merged cells (rowspan in col 0 shifts row 1 cells to col 1)
        merged_source = (
            "[TABLE 2]\n"
            "Group | [IMG a.png] | Alpha\n"
            " | [IMG b.png] | Beta\n"
            "[/TABLE]\n"
        )
        merged_blocks = [{
            "type": "table",
            "rows": [
                {"cells": [{"text": "Group", "rowspan": 2, "colspan": 1}, {"text": ""}, {"text": "Alpha"}]},
                {"cells": [{"text": ""}, {"text": "Beta"}]},
            ],
        }]
        restore_table_cell_images(merged_blocks, merged_source, allowed_images={"a.png", "b.png"})
        self.assertEqual(merged_blocks[0]["rows"][0]["cells"][1]["text"], "[IMG a.png]")
        self.assertEqual(merged_blocks[0]["rows"][1]["cells"][0]["text"], "[IMG b.png]")

        # Pure-image table (no text cells at all) matched via allowed_images + row shape
        pic_source = "[TABLE 3]\n[IMG p1.png] | [IMG p2.png]\n[/TABLE]\n"
        pic_blocks = [{"type": "table", "rows": [{"cells": [{"text": ""}, {"text": ""}]}]}]
        restore_table_cell_images(pic_blocks, pic_source, allowed_images={"p1.png", "p2.png"})
        self.assertEqual(pic_blocks[0]["rows"][0]["cells"][0]["text"], "[IMG p1.png]")
        self.assertEqual(pic_blocks[0]["rows"][0]["cells"][1]["text"], "[IMG p2.png]")

        # Decorative/unlisted images must not be resurrected when allowed_images is provided
        decor_blocks = [{"type": "table", "rows": [{"cells": [{"text": ""}, {"text": "Jack is my classmate."}]}]}]
        restore_table_cell_images(decor_blocks, source, allowed_images={"other.jpeg"})
        self.assertEqual(decor_blocks[0]["rows"][0]["cells"][0]["text"], "")

if __name__ == "__main__":
    unittest.main()
