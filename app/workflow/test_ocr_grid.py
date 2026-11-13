# -*- coding: utf-8 -*-
"""OCR 版面块坐标 → 网格题组图文归属的回归。

用户 2026-11-13 批次「七上 人教版 课时A计划 Starter Unit 2 Keep Tidy! 课时二 Pronunciation」
的正文是识别服务 Markdown 投影，Ⅱ. 根据图片及所给音标写出下列单词所缺的字母 是双栏网格：
服务顺序把题号、图片、词形行交错展平，第一题窗口为空、第二题窗口同时收进前两题文字，
装配报 A8/A9。这里锁定"只用服务给出的页面坐标判定归属、不改正文顺序"的口径。
"""
import json
import tempfile
import unittest
from pathlib import Path

from workflow import ocr_grid, question_content

BODY = [
    "第二课时 Pronunciation",
    "Ⅱ. 根据图片及所给音标写出下列单词所缺的字母",
    "1. c ___ /eɪ/ke",
    "2. n _____/əʊ/se",
]


def _block(label, text, bbox, order):
    return {"type": label, "text": text, "bbox": bbox, "order": order}


def _page_blocks():
    # 双栏网格：左栏 1/3/5，右栏 2/4，图片在题号上方、词形行在题号下方。
    return [
        _block("doc_title", "第二课时 Pronunciation", [345, 51, 736, 91], 1),
        _block("paragraph_title", "Ⅱ. 根据图片及所给音标写出下列单词所缺的字母", [25, 580, 511, 609], 2),
        _block("image", "", [52, 617, 158, 734], 3),
        _block("text", "1.", [29, 663, 49, 690], 4),
        _block("text", "2.", [298, 663, 323, 690], 5),
        _block("image", "", [328, 623, 392, 727], 6),
        _block("text", "c ___ /eɪ/ke", [27, 744, 166, 772], 7),
        _block("text", "n _____/əʊ/se", [298, 742, 442, 770], 8),
        _block("text", "3.", [27, 824, 49, 850], 9),
        _block("image", "", [50, 782, 192, 891], 10),
        _block("text", "b __/aɪ/ke", [26, 902, 167, 930], 11),
        _block("text", "4.", [298, 824, 322, 851], 12),
        _block("image", "", [325, 793, 380, 888], 13),
        _block("text", "5.", [26, 984, 50, 1010], 14),
        _block("image", "", [446, 791, 500, 888], 15),
        _block("text", "m _____/iː/t", [298, 899, 423, 926], 16),
        _block("image", "", [55, 941, 184, 1055], 17),
        _block("text", "comp __/ju:/ter", [25, 1066, 218, 1093], 18),
        _block("paragraph_title", "Ⅲ. 将下面歌谣中画线的单词按所给音标归类", [27, 1100, 502, 1128], 19),
    ]


class SectionItemsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.batch = Path(self.tmp.name)
        source = self.batch / "01_source"
        media = source / "media"
        media.mkdir(parents=True)
        blocks = _page_blocks()
        for ordinal in range(1, sum(1 for block in blocks if block["type"] == "image") + 1):
            (media / f"pdf-page-1-image-{ordinal}.jpg").write_bytes(b"x")
        payload = {"sources": [{"status": "completed", "pages": [{"page": 1, "blocks": blocks}]}]}
        (source / "pdf-ocr.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_each_item_owns_its_own_picture_and_word_row(self):
        grid = ocr_grid.section_items(
            self.batch, section_title="Ⅱ. 根据图片及所给音标写出下列单词所缺的字母", numbers=[1, 2, 3, 4, 5])
        self.assertIsNotNone(grid)
        self.assertEqual(grid["basis"], "ocr_blocks_grid")
        items = grid["items"]
        self.assertEqual(sorted(items), [1, 2, 3, 4, 5])
        self.assertEqual(ocr_grid.item_stem(items[1]), "[IMG pdf-page-1-image-1.jpg]\nc ___ /eɪ/ke")
        self.assertEqual(ocr_grid.item_stem(items[2]), "[IMG pdf-page-1-image-2.jpg]\nn _____/əʊ/se")
        self.assertEqual(ocr_grid.item_stem(items[3]), "[IMG pdf-page-1-image-3.jpg]\nb __/aɪ/ke")
        # 第 4 格两张图都在它自己的格子里，不能分给别的题。
        self.assertEqual(items[4]["images"], ["pdf-page-1-image-5.jpg", "pdf-page-1-image-4.jpg"])
        self.assertEqual(items[4]["texts"], ["m _____/iː/t"])
        self.assertEqual(ocr_grid.item_stem(items[5]), "[IMG pdf-page-1-image-6.jpg]\ncomp __/ju:/ter")
        self.assertTrue(all(item["bbox"] for item in items.values()))

    def test_number_set_must_match_confirmed_unit(self):
        self.assertIsNone(ocr_grid.section_items(
            self.batch, section_title="Ⅱ. 根据图片及所给音标写出下列单词所缺的字母", numbers=[1, 2, 3]))

    def test_unknown_section_title_is_not_guessed(self):
        self.assertIsNone(ocr_grid.section_items(
            self.batch, section_title="Ⅲ. 将下面歌谣中画线的单词按所给音标归类", numbers=[1, 2, 3, 4, 5]))

    def test_single_column_layout_is_not_treated_as_grid(self):
        odd = [_block("paragraph_title", "Ⅰ. 单词拼写", [25, 100, 500, 130], 1)]
        odd += [_block("text", f"{n}. _____", [25, 140 + 40 * n, 300, 160 + 40 * n], n + 2) for n in range(1, 4)]
        odd += [_block("text", f"w{n}", [25, 160 + 40 * n, 300, 175 + 40 * n], n + 10) for n in range(1, 4)]
        payload = {"sources": [{"status": "completed", "pages": [{"page": 1, "blocks": odd}]}]}
        (self.batch / "01_source" / "pdf-ocr.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        self.assertIsNone(ocr_grid.section_items(
            self.batch, section_title="Ⅰ. 单词拼写", numbers=[1, 2, 3]))

    def test_risky_markup_text_is_not_bound(self):
        blocks = _page_blocks()
        blocks[6]["text"] = "<div><img src=\"imgs/a.jpg\"></div>"
        payload = {"sources": [{"status": "completed", "pages": [{"page": 1, "blocks": blocks}]}]}
        (self.batch / "01_source" / "pdf-ocr.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        self.assertIsNone(ocr_grid.section_items(
            self.batch, section_title="Ⅱ. 根据图片及所给音标写出下列单词所缺的字母", numbers=[1, 2, 3, 4, 5]))

    def test_repair_only_when_linear_parse_is_wrong(self):
        grid = ocr_grid.section_items(
            self.batch, section_title="Ⅱ. 根据图片及所给音标写出下列单词所缺的字母", numbers=[1, 2, 3, 4, 5])
        wrong = {1: "", 2: "c ___ /eɪ/ke\nn _____/əʊ/se"}
        self.assertTrue(ocr_grid.needs_repair(wrong, grid))
        right = {number: ocr_grid.item_stem(item) for number, item in grid["items"].items()}
        self.assertFalse(ocr_grid.needs_repair(right, grid))


class PostfixImageOptionTest(unittest.TestCase):
    """图片选项的后置版式（`A.` 下一行才是图片）不能被当成材料标签截断窗口。"""

    LINES = [
        "## 阅读理解",
        "I'm Linda. In my home, there is a black and white thing.",
        "( ) 3. [新考法·图片理解] What does Linda's brother think Neo is?",
        "A.",
        "[IMG pdf-page-1-image-8.jpg]",
        "B.",
        "[IMG pdf-page-1-image-9.jpg]",
        "C.",
        "[IMG pdf-page-1-image-10.jpg]",
        "D.",
        "[IMG pdf-page-1-image-11.jpg]",
    ]

    def test_postfix_image_options_keep_four_options(self):
        parts = question_content.question_parts(self.LINES, 3)
        self.assertIn("What does Linda's brother think Neo is?", parts["stem"])
        self.assertEqual([option["label"] for option in parts["options"]], ["A", "B", "C", "D"])
        self.assertEqual(parts["options"][0]["text"], "[IMG pdf-page-1-image-8.jpg]")
        self.assertEqual(parts["options"][3]["text"], "[IMG pdf-page-1-image-11.jpg]")

    def test_material_label_still_ends_the_window(self):
        lines = self.LINES[:3] + ["A.", "Another passage starts here.", "1. next"]
        parts = question_content.question_parts(lines, 3)
        self.assertIn("What does Linda's brother think Neo is?", parts["stem"])
        self.assertNotIn("Another passage", parts["stem"])


class GridAwareRestoreTest(unittest.TestCase):
    """装配回填不得把网格归位后的正确题干改回线性错位内容。

    真实批次里 `restore_source_stems` 用线性 `_parts` 回填，第 4 格被改成只剩
    `[IMG …4.jpg]`，随后来源审计按网格判 `SOURCE_STEM_MISSING`，整批装配置为
    needs_review。回填与审计必须使用同一网格投影。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.batch = Path(self.tmp.name)
        source = self.batch / "01_source"
        media = source / "media"
        media.mkdir(parents=True)
        blocks = _page_blocks()
        for ordinal in range(1, sum(1 for block in blocks if block["type"] == "image") + 1):
            (media / f"pdf-page-1-image-{ordinal}.jpg").write_bytes(b"x")
        payload = {"sources": [{"status": "completed", "pages": [{"page": 1, "blocks": blocks}]}]}
        (source / "pdf-ocr.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        linear = "\n".join(BODY)
        unit = {
            "unit_id": "sec-grid-u1",
            "identity_version": "scoped-v1",
            "unit_kind": "group",
            "numbers": [1, 2, 3, 4, 5],
            "stem": linear,
            "source_span": {"start_line": 2, "end_line": 3},
            "source_section": {"title": "Ⅱ. 根据图片及所给音标写出下列单词所缺的字母"},
        }
        (source / "structure.confirmed.json").write_text(
            json.dumps({"units": [unit]}, ensure_ascii=False), encoding="utf-8")
        children = []
        for number in range(1, 6):
            children.append({
                "number": number,
                "question_id": f"sec-grid-l{number}-q{number}",
                "stem": "",
                "options": [{"key": "A", "text": "线性错位选项"}],
                "attachments": [{"kind": "image",
                                 "url": f"/source-media/x/pdf-page-1-image-{number}.jpg",
                                 "placement": "question"}],
            })
        self.payload = {"items": [{"unit_id": "sec-grid-u1", "identity_version": "scoped-v1",
                                   "numbers": [1, 2, 3, 4, 5],
                                   "unit_kind": "group", "subquestions": children}]}

    def tearDown(self):
        self.tmp.cleanup()

    def test_restore_uses_grid_cells_instead_of_linear_parse(self):
        # 线性正文里没有可解析的 1./2. 题号锚点：回填仍必须按网格建立题干，
        # 这正是真实批次第 4 格被改坏的那条路径。
        changed = question_content.restore_source_stems(self.batch, self.payload)
        self.assertGreaterEqual(changed, 5)
        stems = {child["number"]: child["stem"]
                 for child in self.payload["items"][0]["subquestions"]}
        self.assertEqual(stems[1], "c ___ /eɪ/ke")
        self.assertEqual(stems[2], "n _____/əʊ/se")
        self.assertEqual(stems[3], "b __/aɪ/ke")
        self.assertEqual(stems[4], "m _____/iː/t")
        self.assertEqual(stems[5], "comp __/ju:/ter")
        self.assertEqual([child["options"] for child in self.payload["items"][0]["subquestions"]],
                         [[] for _ in range(5)])


if __name__ == "__main__":
    unittest.main()
