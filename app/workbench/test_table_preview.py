"""Word 表格的合并跨度必须进入预览，且只在网格同形时才采用。"""
import tempfile
import unittest
from pathlib import Path

from workbench import server

HEADER = "It’s important to learn how to <u>　</u><u>16</u><u>　</u><u> </u>"
LINES = [
    "Ⅳ.信息转换",
    "你将听到一篇短文。请根据短文内容，写出下面表格中所缺的单词，每空仅填一词。",
    "[TABLE 1]",
    f"{HEADER} |  | ",
    "Reasons | It helps us to make friends. | ●to help others to feel cared about<br>"
    "●to show that we are <u>　</u><u>17</u><u>　</u> and friendly ",
    " | It teaches us how to be fair. | ●to think about the <u>　</u><u>18</u><u>　</u> "
    "of other people and your own ",
    "[/TABLE]",
]


def word_table():
    return {
        "number": 1,
        "rows": [
            {"cells": [{"text": HEADER, "colspan": 3, "rowspan": 1}]},
            {"cells": [{"text": "Reasons", "colspan": 1, "rowspan": 3},
                       {"text": "It helps us to make friends."},
                       {"text": "●to help others to feel cared about<br>"
                                "●to show that we are <u>　</u><u>17</u><u>　</u> and friendly"}]},
            {"cells": [{"text": "", "colspan": 1, "rowspan": 1, "merge_origin": [1, 0]},
                       {"text": "It teaches us how to be fair."},
                       {"text": "●to think about the <u>　</u><u>18</u><u>　</u> of other people "
                                "and your own"}]},
        ],
    }


class TablePreviewTest(unittest.TestCase):
    def test_word_merges_reach_preview(self):
        blocks = server.source_blocks(LINES[2:], {"1": word_table()})
        table = blocks[0]
        self.assertEqual(table["structured"][0]["cells"][0]["colspan"], 3)
        self.assertEqual(table["structured"][1]["cells"][0]["rowspan"], 3)
        self.assertEqual(len(table["structured"][2]["cells"]), 2)
        self.assertIn("●to show that we are", table["structured"][1]["cells"][2]["text"])

    def test_flat_rows_remain_when_word_structure_missing(self):
        blocks = server.source_blocks(LINES[2:])
        table = blocks[0]
        self.assertNotIn("structured", table)
        self.assertEqual(table["rows"][0], [HEADER, "", ""])
        self.assertEqual(len(table["rows"]), 3)

    def test_grid_shape_mismatch_does_not_guess_merges(self):
        table = word_table()
        table["rows"] = table["rows"][:2]
        blocks = server.source_blocks(LINES[2:], {"1": table})
        self.assertNotIn("structured", blocks[0])
        self.assertEqual(blocks[0]["rows"][0], [HEADER, "", ""])

    def test_images_inside_structured_cells_keep_url_and_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            media = batch / "01_source" / "media"
            media.mkdir(parents=True)
            (media / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
            lines = ["[TABLE 1]", "[IMG pic.png]<br>Weather | B", "[/TABLE]"]
            table = {"number": 1, "rows": [{"cells": [
                {"text": "[IMG pic.png]<br>Weather", "colspan": 1, "rowspan": 1},
                {"text": "B", "colspan": 1, "rowspan": 1}]}]}
            blocks = server.attach_media(server.source_blocks(lines, {"1": table}), batch, "batch")
            cell = blocks[0]["structured"][0]["cells"][0]
            self.assertEqual(cell["text"], "Weather")
            self.assertEqual(cell["images"][0]["name"], "pic.png")
            self.assertTrue(cell["images"][0]["url"].endswith("/media/pic.png"))

    def test_paper_preview_restores_pure_image_table_cells(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            wf_root = Path(tmp)
            batch = wf_root / "b1"
            (batch / "01_source").mkdir(parents=True)
            (batch / "06_batch").mkdir(parents=True)
            (batch / "01_source" / "正文.txt").write_text(
                "Ⅲ. 阅读理解\n"
                "[TABLE 1]\n"
                "[IMG image2.jpeg] | Jack is 12.\n"
                "[IMG image3.jpeg] | Sue is 13.\n"
                "[/TABLE]\n",
                encoding="utf-8",
            )
            batch_data = {
                "name": "b1",
                "items": [{
                    "id": "u1",
                    "number": 1,
                    "material": "[TABLE 1]\n | Jack is 12.\n | Sue is 13.\n[/TABLE]",
                    "materialBlocks": [{
                        "type": "table",
                        "rows": [
                            {"cells": [{"text": "", "rowspan": 1, "colspan": 1}, {"text": "Jack is 12.", "rowspan": 1, "colspan": 1}]},
                            {"cells": [{"text": "", "rowspan": 1, "colspan": 1}, {"text": "Sue is 13.", "rowspan": 1, "colspan": 1}]},
                        ],
                    }],
                    "attachments": [
                        {"kind": "image", "name": "image2.jpeg", "url": "01_source/media/image2.jpeg", "placement": "question"},
                        {"kind": "image", "name": "image3.jpeg", "url": "01_source/media/image3.jpeg", "placement": "question"},
                    ],
                }],
            }
            (batch / "06_batch" / "batch.json").write_text(json.dumps(batch_data, ensure_ascii=False), encoding="utf-8")
            old_wf = server.WORKFLOWS
            try:
                server.WORKFLOWS = wf_root
                preview = server.paper_preview("b1")
            finally:
                server.WORKFLOWS = old_wf
            rows = preview["items"][0]["materialBlocks"][0]["rows"]
            self.assertEqual(rows[0]["cells"][0]["text"], "[IMG image2.jpeg]")
            self.assertEqual(rows[1]["cells"][0]["text"], "[IMG image3.jpeg]")


if __name__ == "__main__":
    unittest.main()
