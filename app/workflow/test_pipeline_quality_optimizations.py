"""Regression tests for universal pipeline quality & efficiency optimizations."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from workflow import (
    assemble_local,
    lesson_bands,
    question_content,
    question_draft,
    unit_tasks,
    wire_format,
)


class PipelineQualityOptimizationsTest(unittest.TestCase):
    def test_raw_fragment_strips_orphan_closing_bold_after_bold_question_number(self):
        raw = "**1.**Hefei ______(改变了很多) since I moved here."
        frag, _ = question_draft._raw_fragment(raw, "Hefei ______(改变了很多) since I moved here.")
        self.assertEqual(frag, "Hefei ______(改变了很多) since I moved here.")
        self.assertFalse(frag.startswith("**"))

        # Legitimate bold wrapping the stem itself is preserved.
        raw_bold = "**1.****Important** word in sentence."
        frag_bold, _ = question_draft._raw_fragment(raw_bold, "Important word in sentence.")
        self.assertEqual(frag_bold, "**Important** word in sentence.")

        # Opening bold belonging to the next token on the right is not pulled in.
        raw_right = "First part.**Next**"
        frag_right, _ = question_draft._raw_fragment(raw_right, "First part.")
        self.assertEqual(frag_right, "First part.")

    def test_step_banners_recognized_as_column_boundaries(self):
        for line in (
            "**【第1步 词汇积累】**",
            "**【第2步 句型训练】**",
            "**【第3步 典例分析】**",
            "**【第4步 实战演练】**",
            "【Step 1 Warmup】",
        ):
            self.assertTrue(lesson_bands.is_column_banner(line), f"expected banner: {line}")
        self.assertEqual(lesson_bands.extract_outline_heading("**【第2步 句型训练】**"), "句型训练")
        self.assertEqual(lesson_bands.extract_outline_heading("**【第3步 典例分析】**"), "典例分析")
        self.assertEqual(lesson_bands.extract_outline_heading("**【第4步 实战演练】**"), "实战演练")
        # 词汇积累 is in _NON_OUTLINE_LABELS so it is a banner boundary but not an answer outline band.
        self.assertEqual(lesson_bands.extract_outline_heading("**【第1步 词汇积累】**"), "")
        # Ordinary numbered questions must never be mistaken for banners.
        self.assertFalse(lesson_bands.is_column_banner("1. Hefei ______(改变了很多) since I moved here."))

    def test_normalize_blank_text_preserves_chinese_paragraph_indent(self):
        writing = "　　注意:1.词数80~100,开头已给出,不计入总词数;\n　　假定你是李辉。"
        self.assertEqual(wire_format.normalize_blank_text(writing), writing)
        # Actual fill-in-the-blank fullwidth spaces still convert to ______.
        self.assertEqual(
            wire_format.normalize_blank_text("Hefei 　　(改变了很多) since I moved here."),
            "Hefei ______(改变了很多) since I moved here.",
        )
        self.assertEqual(
            wire_format.normalize_blank_text("　　(多亏了村民们的努力),our hometown..."),
            "______(多亏了村民们的努力),our hometown...",
        )

    def test_strip_writing_answer_space_removes_lone_trailing_underscore_before_rule(self):
        src = (
            "My Experience of Making a Poster\n"
            "Last week,I made a poster of the great changes in my hometown._\n"
            "___________________________________________________________________________________"
        )
        cleaned = question_content.strip_writing_answer_space(src)
        self.assertEqual(
            cleaned,
            "My Experience of Making a Poster\n"
            "Last week,I made a poster of the great changes in my hometown.",
        )
        # Word-internal underscore and spelling initial r______ stay intact.
        self.assertEqual(question_content.strip_writing_answer_space("r______"), "r______")

    def test_project_early_fields_preserves_explanation_and_answer_attachments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "01_source").mkdir(parents=True)
            (root / "06_batch").mkdir(parents=True)
            unit = {
                "unit_id": "u1",
                "unit_kind": "standalone",
                "numbers": [1],
                "type": "书面表达",
                "stem": "Prompt",
            }
            (root / "01_source/structure.confirmed.json").write_text(
                json.dumps({"units": [unit]}, ensure_ascii=False), encoding="utf-8"
            )
            early_item = {
                "id": "u1",
                "unit_id": "u1",
                "unit_kind": "standalone",
                "number": 1,
                "numbers": [1],
                "stem": "Prompt",
                "attachments": [
                    {"kind": "image", "url": "01_source/media/image22.jpg", "name": "image22.jpg", "placement": "question"}
                ],
                "filterMetadata": {"difficulty": "适中"},
                "subquestions": [],
            }
            (root / "06_batch/batch_meta.base.json").write_text(
                json.dumps({"items": [early_item]}, ensure_ascii=False), encoding="utf-8"
            )
            target_item = dict(
                early_item,
                attachments=[
                    {"kind": "image", "url": "01_source/media/image22.jpg", "name": "image22.jpg", "placement": "question"},
                    {"kind": "image", "url": "01_source/media/image23.jpg", "name": "image23.jpg", "placement": "explanation"},
                ],
            )
            merged = assemble_local.project_final_view(root, {"items": [target_item]})
            atts = merged["items"][0]["attachments"]
            names_by_placement = [(a["name"], a["placement"]) for a in atts]
            self.assertIn(("image22.jpg", "question"), names_by_placement)
            self.assertIn(("image23.jpg", "explanation"), names_by_placement)

    def test_compact_extraction_drops_source_objects_bloat(self):
        raw = {
            "version": "1.0",
            "text": "big text",
            "blocks": [{"type": "p"}],
            "pages": [{"page": 1}],
            "source_objects": {"objects": [{"id": "o1", "xml": "huge"}]},
            "tables": [{"id": "t1"}],
            "media": [{"name": "a.jpg"}],
            "warnings": [],
        }
        compact = unit_tasks.compact_extraction(raw)
        self.assertNotIn("source_objects", compact)
        self.assertNotIn("text", compact)
        self.assertNotIn("blocks", compact)
        self.assertNotIn("pages", compact)
        self.assertEqual(compact["tables"], [{"id": "t1"}])
        self.assertEqual(compact["media"], [{"name": "a.jpg"}])

    def test_audit_sentence_blanks_accepts_chinese_hint_phrase_blanks(self):
        script = Path(__file__).resolve().parents[1] / "scripts/06-upload/audit_sentence_blanks.py"
        batch = {
            "items": [
                {
                    "unit_id": "u1",
                    "question_type": "完成句子",
                    "stem": "Hefei ______(改变了很多) since I moved here.",
                    "answer": "has changed a lot",
                },
                {
                    "unit_id": "u2",
                    "question_type": "完成句子",
                    "stem": "______(随着……的发展) Chinese railways, travel has become ______(越来越方便).",
                    "answer": "With the development of;more and more convenient",
                },
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "batch.json"
            p.write_text(json.dumps(batch, ensure_ascii=False), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(script), "--batch", str(p)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("[OK]", proc.stdout)

    def test_persisted_section_end_with_trailing_banner_only_still_binds_scoped_answers(self):
        from workflow import upload_content

        body = [
            "七上 · 人教版 · 课时A计划",
            "**Starter Unit 2　Keep Tidy!**",
            "**第四课时**　Project",
            "**【A 重要考点集训】**",
            "**Ⅰ. 根据汉语意思及所给提示翻译句子**",
            "**1.**你的玩偶是什么颜色的?(doll)",
            "**2.**我的排球在哪里?(volleyball)",
            "**【B 创新阅读专练】**",
            "**任务型阅读**",
            "Frank is a boy.",
            "**1.**What colour is Frank's desk?",
        ]
        appendix = [
            "****参考答案**",
            "**重要考点集训**",
            "Ⅰ.1.What colour is your doll?",
            "2.Where is my volleyball?",
            "**创新阅读专练**",
            "1.It's blue.",
        ]
        source_lines = {"01_source/正文.txt": body, "01_source/附录.txt": appendix}
        # Legacy persisted structure included line 8 (**【B 创新阅读专练】**) at the end of section Ⅰ,
        # whereas tightened heading_candidates ends section Ⅰ at line 7.
        unit = {
            "unit_id": "sec-demo-u7",
            "unit_kind": "standalone",
            "type": "翻译",
            "numbers": [2],
            "identity_version": "scoped-v1",
            "source_span": {"start_line": 7, "end_line": 8, "basis": "cleaned_source"},
            "source_section": {
                "title": "**Ⅰ. 根据汉语意思及所给提示翻译句子**",
                "ordinal": "Ⅰ.",
                "context": "第四课时　Project",
                "occurrence": 1,
                "start_line": 5,
                "end_line": 8,
            },
        }
        self.assertTrue(
            upload_content.scoped_source_matches(unit, 2, "01_source/正文.txt", 7, source_lines, _file_coordinates=True)
        )
        hits = upload_content._compact_scoped_candidates(unit, 2, source_lines)
        self.assertEqual(hits, [("01_source/附录.txt", 4, "Where is my volleyball?")])

        # Crossing into the next section heading (line 9) must STILL be rejected.
        crossed = dict(unit, source_section=dict(unit["source_section"], end_line=9))
        self.assertFalse(
            upload_content.scoped_source_matches(crossed, 2, "01_source/正文.txt", 7, source_lines, _file_coordinates=True)
        )
        self.assertEqual(upload_content._compact_scoped_candidates(crossed, 2, source_lines), [])


if __name__ == "__main__":
    unittest.main()
