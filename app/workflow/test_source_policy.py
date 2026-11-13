# -*- coding: utf-8 -*-
"""提取来源优先级（Word > PDF 整卷 OCR > 扫描件）的回归测试。"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from . import source_policy as sp


class TestKindAndRank(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(sp.kind("卷.docx"), sp.WORD_KIND)
        self.assertEqual(sp.kind("卷.DOC"), sp.LEGACY_WORD_KIND)
        self.assertEqual(sp.kind("卷.pdf"), sp.PDF_KIND)
        self.assertEqual(sp.kind("答案.txt"), sp.OTHER_KIND)

    def test_dict_items_use_filename(self):
        self.assertEqual(sp.kind({"filename": "a.docx"}), sp.WORD_KIND)
        self.assertEqual(sp.kind({"name": "b.pdf"}), sp.PDF_KIND)

    def test_rank_order(self):
        self.assertLess(sp.rank("a.docx"), sp.rank("a.doc"))
        self.assertLess(sp.rank("a.doc"), sp.rank("a.pdf"))
        self.assertLess(sp.rank("a.pdf"), sp.rank("a.png"))


class TestSelectPrimary(unittest.TestCase):
    def test_word_beats_pdf_regardless_of_upload_order(self):
        self.assertEqual(sp.select_primary(["卷.pdf", "卷.docx"]), "卷.docx")
        self.assertEqual(sp.select_primary(["卷.docx", "卷.pdf"]), "卷.docx")

    def test_stable_within_same_kind(self):
        self.assertEqual(sp.select_primary(["甲.pdf", "乙.pdf"]), "甲.pdf")

    def test_ignores_none(self):
        self.assertEqual(sp.select_primary([None, "卷.pdf"]), "卷.pdf")
        self.assertIsNone(sp.select_primary([]))

    def test_dict_candidates(self):
        chosen = sp.select_primary([{"filename": "a.pdf"}, {"filename": "b.docx"}])
        self.assertEqual(chosen["filename"], "b.docx")


class TestPdfUsage(unittest.TestCase):
    def test_all_native_text_layer(self):
        usage = sp.pdf_usage([{"page": 1, "method": "native"}, {"page": 2}])
        self.assertEqual(usage["mode"], sp._PDF_MODE_TEXT)
        self.assertEqual((usage["pages"], usage["ocr_pages"]), (2, 0))

    def test_mixed(self):
        usage = sp.pdf_usage([{"page": 1}, {"page": 2, "method": "ocr"}])
        self.assertEqual(usage["mode"], sp._PDF_MODE_MIXED)
        self.assertEqual(usage["ocr_pages"], 1)

    def test_all_ocr_and_skipped(self):
        usage = sp.pdf_usage([{"page": 1, "method": "ocr"},
                              {"page": 2, "method": "ocr", "ocr_skipped": True}])
        self.assertEqual(usage["mode"], sp._PDF_MODE_OCR)
        self.assertEqual(usage["ocr_skipped_pages"], 1)

    def test_no_pages(self):
        self.assertEqual(sp.pdf_usage([])["mode"], sp._PDF_MODE_UNKNOWN)


class TestBuildReport(unittest.TestCase):
    def test_word_primary_pdf_is_structure_reference(self):
        report = sp.build_report("批次/source.docx", ["批次/answers.pdf"])
        self.assertEqual(report["rule_id"], sp.RULE_ID)
        self.assertEqual(report["authority"], "word")
        self.assertEqual(report["extraction"], "docx_native")
        row = report["supporting"][0]
        self.assertEqual(row["role"], "structure_reference")
        self.assertFalse(row["content_authority"])
        self.assertIn("Word 为唯一提取主源", report["recommendation"])

    def test_pdf_only_text_layer(self):
        report = sp.build_report("/tmp/卷.pdf", pages=[{"page": 1}])
        self.assertEqual(report["authority"], "pdf")
        self.assertEqual(report["extraction"], "pdf_text_layer")
        self.assertEqual(report["pdf"]["mode"], sp._PDF_MODE_TEXT)
        self.assertIn("文字层", report["note"])

    def test_pdf_only_ocr_is_flagged(self):
        report = sp.build_report("/tmp/卷.pdf", pages=[{"page": 1, "method": "ocr"}])
        self.assertEqual(report["extraction"], "pdf_ocr")
        self.assertIn("整卷 OCR", report["note"])
        self.assertIn("JSON 与 Markdown", report["note"])
        self.assertIn("服务阅读顺序", report["note"])
        self.assertNotIn("质量不足", report["note"])
        self.assertIn("不要求原生解析成功", report["recommendation"])
        self.assertIn("Word", report["recommendation"])

    def test_legacy_doc_warns_conversion(self):
        report = sp.build_report("旧卷.doc")
        self.assertEqual(report["authority"], "word")
        self.assertEqual(report["extraction"], "doc_legacy_converted")
        self.assertIn("图片", report["note"])


class TestUploadNotice(unittest.TestCase):
    def test_word_with_pdf_attachment(self):
        notice = sp.upload_notice("卷.docx", ["卷.pdf"])
        self.assertIn("Word 原卷作为提取主源", notice)
        self.assertIn("不覆盖 Word 原文", notice)

    def test_pdf_only_mentions_ocr_and_word(self):
        notice = sp.upload_notice("卷.pdf", [])
        self.assertIn("整卷 OCR", notice)
        self.assertIn("JSON 与 Markdown", notice)
        self.assertIn("正文全部使用 OCR", notice)
        self.assertNotIn("质量不足", notice)
        self.assertIn("Word", notice)

    def test_pdf_with_word_attachment_still_says_pdf_primary(self):
        # 主源判定由 select_primary 负责；这里只确认提示不会谎称 Word 优先。
        notice = sp.upload_notice("卷.pdf", ["卷.docx"])
        self.assertIn("仅上传 PDF", notice)


class TestExtractionRecordsPolicy(unittest.TestCase):
    """extract.run() 必须把本次来源策略落成 01_source/source-policy.json。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.batch = Path(self.temp.name)
        self.word = self.batch / "paper.docx"
        self.word.write_bytes(b"word")
        self.pdf = self.batch / "paper-scan.pdf"
        self.pdf.write_bytes(b"pdf")

    def entry(self, path):
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def test_word_source_records_pdf_as_structure_reference(self):
        from workflow import extract, pdf_ocr

        manifest = {"inputs": {"source": self.entry(self.word),
                               "attachment_001": self.entry(self.pdf)}}

        def word(source, evidence):
            return {"text": "一、单项选择\n1. Word question?", "media": [], "warnings": []}

        with patch.object(extract.mf, "load", return_value=manifest), \
             patch("workflow.agent.configured", return_value=False), \
             patch("workflow.docx_extract.extract_docx", side_effect=word), \
             patch.object(pdf_ocr.ocr_backend, "read_pdf",
                          return_value={"pages": [{"page": 1, "text": "", "blocks": [],
                                                   "images": [], "needs_ocr": True}],
                                        "warnings": []}):
            result = extract.run(self.batch)

        policy_path = self.batch / "01_source" / "source-policy.json"
        self.assertTrue(policy_path.is_file())
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        self.assertEqual(policy["rule_id"], sp.RULE_ID)
        self.assertEqual(policy["authority"], "word")
        self.assertEqual(policy["source"], "paper.docx")
        self.assertEqual([row["name"] for row in policy["supporting"]], ["paper-scan.pdf"])
        self.assertEqual(policy["supporting"][0]["role"], "structure_reference")
        self.assertFalse(policy["supporting"][0]["content_authority"])
        self.assertEqual(result["source_policy"], policy)
        report = json.loads((self.batch / "07_review" / "extraction-report.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(report["source_policy"]["authority"], "word")


    def test_word_without_pdf_is_text_only_and_records_visual(self):
        # 用户 2026-11-11：无 PDF 的纯 Word 直接文字提取、不 OCR；配合视觉理解标记。
        report = sp.build_report("only.docx", [], visual_assist=True)
        self.assertEqual(report["extraction"], "docx_native")
        self.assertEqual(report["authority"], "word")
        self.assertNotIn("pdf", report)
        self.assertTrue(report["visual_structure_assist"])
        self.assertIn("无需 OCR", report["recommendation"])

    def test_pdf_text_layer_does_not_ocr_and_marks_visual(self):
        report = sp.build_report("only.pdf", pages=[{"method": "text"}], visual_assist=True)
        self.assertEqual(report["extraction"], "pdf_text_layer")
        self.assertEqual(report["pdf"]["ocr_pages"], 0)
        self.assertTrue(report["visual_structure_assist"])

    def test_visual_flag_omitted_when_unknown(self):
        report = sp.build_report("only.docx", [])
        self.assertNotIn("visual_structure_assist", report)


if __name__ == "__main__":
    unittest.main()
