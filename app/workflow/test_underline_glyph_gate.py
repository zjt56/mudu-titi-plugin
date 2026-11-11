# -*- coding: utf-8 -*-
"""画线阶段对「私用区字形」提取告警的判定口径。

背景：13 年卷 86 题选项里的音标字形是字体私用区码位（原码位已保留），
旧口径把它当阻断项，画线阶段直接失败并要求人工视觉核对。原码位保留说明
字面没有被改写或丢弃，属于字体呈现限制，不应当阻断机械画线恢复；
Symbol 字体/符号字形仍可能改写字面，继续阻断。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "scripts" / "06-upload"))

from workflow import docx_extract  # noqa: E402
import mark_underline  # noqa: E402
import check_underline  # noqa: E402

RAW_PRESERVED = "Private-use glyph encountered; raw character preserved without font mapping"
SYMBOL_FONT = "Symbol font encountered; glyph/font mapping was not guessed"


class BlockingWarningClassificationTest(unittest.TestCase):
    def test_raw_preserved_glyph_is_not_blocking(self):
        self.assertEqual(docx_extract.blocking_warnings([RAW_PRESERVED]), [])
        self.assertFalse(mark_underline.extraction_gate([RAW_PRESERVED], False)["blocking"])

    def test_symbol_font_mapping_still_blocks(self):
        self.assertEqual(docx_extract.blocking_warnings([SYMBOL_FONT]), [SYMBOL_FONT])
        self.assertTrue(mark_underline.extraction_gate([SYMBOL_FONT], False)["blocking"])

    def test_mixed_warnings_block_while_keeping_the_raw_note(self):
        issue = mark_underline.extraction_gate([RAW_PRESERVED, SYMBOL_FONT], False)
        self.assertTrue(issue["blocking"])
        self.assertEqual(issue["message"], [RAW_PRESERVED, SYMBOL_FONT])

    def test_ocr_evidence_downgrades_every_warning(self):
        self.assertFalse(mark_underline.extraction_gate([SYMBOL_FONT], True)["blocking"])

    def test_check_underline_and_mark_underline_share_one_rule(self):
        for warnings in ([RAW_PRESERVED], [SYMBOL_FONT], [RAW_PRESERVED, SYMBOL_FONT], []):
            self.assertEqual(check_underline.blocking_warnings(warnings),
                             mark_underline.blocking_warnings(warnings))


if __name__ == "__main__":
    unittest.main()
