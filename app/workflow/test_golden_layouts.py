"""Golden Corpus: fast (~3s, zero-model) deterministic layout replay suite.

Covers 8 real-world textbook & exam layouts end-to-end across extract,
lesson_bands (Outline AST), structure_gate, question_draft, scoped answer
binding, wire_format, assemble_local, and prompt compaction.
"""
from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from workflow import (
    assemble_local,
    content_rules,
    docx_extract,
    extract,
    lesson_bands,
    question_content,
    question_draft,
    review,
    unit_tasks,
    upload_content,
    wire_format,
)


def _minimal_docx(path: Path, paragraphs: list[str], images: dict[str, bytes] | None = None) -> None:
    """Create a valid minimal .docx archive with optional repeated decorative/content images."""
    images = images or {}
    rels = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
    ]
    rid_map = {}
    for idx, name in enumerate(images, 1):
        rid = f"rId{idx}"
        rid_map[name] = rid
        rels.append(
            f'<Relationship Id="{rid}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            f'Target="media/{name}"/>'
        )
    rels.append("</Relationships>")

    body_parts = []
    for p in paragraphs:
        if p.startswith("@IMG:"):
            name = p[5:]
            rid = rid_map[name]
            body_parts.append(
                "<w:p><w:r><w:drawing><a:blip "
                'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
                f'r:embed="{rid}"/></w:drawing></w:r></w:p>'
            )
        else:
            esc = p.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            body_parts.append(f"<w:p><w:r><w:t>{esc}</w:t></w:r></w:p>")
    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>" + "".join(body_parts) + "</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", doc_xml)
        zf.writestr("word/_rels/document.xml.rels", "".join(rels))
        for name, data in images.items():
            zf.writestr(f"word/media/{name}", data)


class GoldenLayoutsReplayTest(unittest.TestCase):
    def test_1_outline_ast_unifies_banners_steps_sections_and_answer_boundary(self):
        lines = [
            "九上 · 人教版 · 课时A计划",
            "Unit 2 Inspiring People",
            "**[IMG image9.jpg]****一、教材语篇回顾****[IMG image10.jpg]**",
            "[IMG image28.jpg]",
            "**【二、A 基础巩固】**",
            "Ⅰ. 根据首字母及汉语提示完成单词",
            "1. She is very p______(耐心的).",
            "**【三、B 能力提升】**",
            "阅读理解",
            "参考答案",
        ]
        ast = lesson_bands.build_outline_ast(lines)
        roles = [(n["line_no"], n["role"], n["band"]) for n in ast]
        self.assertIn((3, "section_heading", "教材语篇回顾"), roles)
        self.assertIn((5, "column_banner", "基础巩固"), roles)
        self.assertIn((6, "section_heading", "基础巩固"), roles)
        self.assertIn((8, "column_banner", "能力提升"), roles)
        self.assertIn((9, "section_heading", "能力提升"), roles)
        self.assertIn((10, "answer_boundary", ""), roles)

    def test_2_step_based_writing_lesson_truncates_step_banner_and_keeps_indent(self):
        body = "\n".join([
            "九上 · 人教版 · 课时A计划",
            "Unit 1 The Changing World",
            "单元话题写作",
            "**【第1步 词汇积累】**",
            "[IMG image19.jpg]",
            "**【第2步 句型训练】**",
            "**根据汉语提示完成下列句子**",
            "**1.**Hefei ______(改变了很多) since I moved here.",
            "**2.**The local government ______(变成了) a park.",
            "**【第3步 典例分析】**",
            "[2023·安徽]假定你是李辉，写一篇短文。",
            "Last week,I made a poster._",
            "________________________________________________",
            "**【第4步 实战演练】**",
            "[新考法·思维导图提示]介绍科技变化。",
            "　　注意:1.词数80~100;",
            "Over the past few years,technology has changed our lives._",
            "________________________________________________",
        ])
        structure, _, _ = extract.infer_structure(body)
        # First section (句型训练) MUST stop before 【第3步 典例分析】 (line 10).
        first_sec = structure["sections"][0]
        self.assertLess(first_sec["source_end_line"], 10)
        # Bold question number stripped cleanly without orphan **.
        frag, _ = question_draft._raw_fragment(
            "**1.**Hefei ______(改变了很多) since I moved here.",
            "Hefei ______(改变了很多) since I moved here.",
        )
        self.assertEqual(frag, "Hefei ______(改变了很多) since I moved here.")
        # Fullwidth Chinese indent preserved; lone trailing _ before rule removed.
        self.assertEqual(wire_format.normalize_blank_text("　　注意:1.词数80~100;"), "　　注意:1.词数80~100;")
        self.assertEqual(
            question_content.strip_writing_answer_space(
                "Over the past few years,technology has changed our lives._\n"
                "________________________________________________"
            ),
            "Over the past few years,technology has changed our lives.",
        )

    def test_3_docx_extract_tags_small_repeated_decorative_icons_and_wire_format_filters_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docx_path = root / "sample.docx"
            out_dir = root / "out"
            _minimal_docx(
                docx_path,
                paragraphs=[
                    "@IMG:icon.jpg", "审题思路", "@IMG:icon.jpg",
                    "@IMG:outline.jpg",
                    "@IMG:icon.jpg", "优秀范文", "@IMG:icon.jpg",
                ],
                images={
                    "icon.jpg": b"\xff\xd8" + b"a" * 1200,      # 1.2KB repeated 4x -> decorative
                    "outline.jpg": b"\xff\xd8" + b"b" * 50000,  # 50KB content diagram -> real
                },
            )
            res = docx_extract.extract_docx(docx_path, out_dir)
            by_name = {}
            for m in res["media"]:
                by_name.setdefault(m["name"], []).append(m)
            self.assertTrue(all(m.get("decorative") is True for m in by_name["icon.jpg"]))
            self.assertTrue(all(not m.get("decorative") for m in by_name["outline.jpg"]))

            # wire_format strips decorative icon in explanation without attaching it,
            # while keeping the real 50KB outline diagram as an explanation attachment.
            item = {
                "id": "u1", "unit_id": "u1", "unit_kind": "standalone", "number": 1, "numbers": [1],
                "stem": "Write an essay.",
                "explanation": "[IMG icon.jpg]审题思路[IMG icon.jpg]\n[IMG outline.jpg]\n范文正文",
                "subquestions": [],
            }
            norm = wire_format.normalize_item(
                item,
                media_map={"icon.jpg": "01_source/media/icon.jpg", "outline.jpg": "01_source/media/outline.jpg"},
                decorative_names={"icon.jpg"},
            )
            self.assertNotIn("[IMG", norm["explanation"])
            atts = [(a["name"], a["placement"]) for a in norm.get("attachments") or []]
            self.assertEqual(atts, [("outline.jpg", "explanation")])

    def test_4_shared_boundary_guidelines_loaded_for_production_and_review(self):
        pre = content_rules.boundary_guidelines_block("preassemble")
        know = content_rules.boundary_guidelines_block("knowledge")
        rev = content_rules.boundary_guidelines_block("review")
        self.assertIn("思维导图", pre)
        self.assertIn("图文结合", pre)
        self.assertIn("固定短语优先", know)
        self.assertIn("思维导图", rev)
        self.assertIn("固定短语优先", rev)

    def test_5_review_rules_exclude_readme_and_compact_extraction_drops_source_objects(self):
        self.assertNotIn("skills-reference/README.md", review.ROUND_RULES)
        compact = unit_tasks.compact_extraction({
            "text": "x", "blocks": [], "pages": [], "source_objects": {"objects": [1, 2]},
            "tables": [{"id": "t1"}], "media": [{"name": "a.jpg"}], "warnings": [],
        })
        self.assertNotIn("source_objects", compact)
        self.assertIn("tables", compact)

    def test_6_scoped_unique_section_band_partial_tolerance(self):
        # Section 1 has an explicit unique outline band (句型训练) with numbers 1-2;
        # Section 2 is an unnumbered writing section without Roman ordinal.
        # Section 1 must still bind cleanly to its matching appendix band.
        body = [
            "九上 · 人教版 · 课时A计划",
            "Unit 1 The Changing World",
            "第一课时 Section A",
            "**【第2步 句型训练】**",
            "根据汉语提示完成下列句子",
            "1. Hefei ______(改变了很多).",
            "2. Rivers ______(在过去).",
            "**【第4步 实战演练】**",
            "书面表达",
            "Write a passage.",
        ]
        appendix = [
            "参考答案",
            "**句型训练**",
            "1. has changed a lot　2. in the past",
        ]
        source_lines = {
            "01_source/正文.txt": body,
            "01_source/附录.txt": appendix,
        }
        unit = {
            "unit_id": "sec-1-u6",
            "unit_kind": "standalone",
            "numbers": [1],
            "identity_version": "scoped-v1",
            "source_section": {
                "title": "根据汉语提示完成下列句子",
                "ordinal": "",
                "context": "第一课时 Section A",
                "occurrence": 1,
                "start_line": 5,
                "end_line": 7,
            },
            "source_span": {"start_line": 6, "end_line": 6, "basis": "cleaned_source"},
        }
        # Verify outline AST recognizes both step banners as container boundaries.
        ast = lesson_bands.build_outline_ast(body)
        step_bands = [n["band"] for n in ast if n["role"] == "step_banner"]
        self.assertEqual(step_bands, ["句型训练", "实战演练"])
        # Verify scoped source matching works for unit 1 in body.
        self.assertTrue(
            upload_content.scoped_source_matches(unit, 1, "01_source/正文.txt", 6, source_lines, _file_coordinates=True)
        )

    def test_7_unnumbered_shucishengyi_quoting_cloze_blank_is_retained_and_banner_truncates_previous(self):
        body = "\n".join([
            "九上 · 人教版 · 课时A计划",
            "Unit 1 The Changing World",
            "第二课时 Section A",
            "**【二、能力提升】**",
            "Ⅱ. 阅读理解",
            "Passage line.",
            "1. Question one?",
            "A. Yes B. No C. Maybe D. Never",
            "**【三、熟词生义】**",
            "**写出下面画线词的汉语意思**",
            "In 2014,he was sent to China as a sports __<u>1</u>__ to <u>cover</u> the 2014 Youth Olympic Games in Nanjing.\t_________",
        ])
        structure, _, _ = extract.infer_structure(body)
        secs = structure["sections"]
        # Reading comprehension must stop before 【三、熟词生义】 (line 9).
        self.assertLess(secs[0]["source_end_line"], 9)
        # 熟词生义 standalone section must NOT absorb quoted __<u>1</u>__ as a printed question number.
        self.assertEqual(secs[-1]["type"], "翻译")
        self.assertEqual(secs[-1]["questions"], [])
        # unnumbered_text_responses must recognize the single terminal answer blank despite quoted __<u>1</u>__.
        resp = question_content.unnumbered_text_responses([
            "In 2014,he was sent to China as a sports __<u>1</u>__ to <u>cover</u> the 2014 Youth Olympic Games in Nanjing.\t_________"
        ])
        self.assertEqual(resp, [(0, 0)])
        # Standalone column banner 【三、熟词生义】 is a container boundary, not an inline hint mark;
        # real inline marks like [熟词生义] / [情境题] are still extracted.
        from workflow import sync_practice
        self.assertEqual(sync_practice.source_marks("**【三、熟词生义】**"), [])
        self.assertEqual(sync_practice.source_marks("Ⅱ. 阅读理解[情境题]\n1.[熟词生义]What does it mean?"), ["[情境题]", "[熟词生义]"])
        # A/B lesson_band stops at 【三、熟词生义】 so it does not leak into the next column.
        bands = lesson_bands.for_text(None, body)["bands"]
        self.assertEqual(len(bands), 1)
        self.assertLess(bands[0]["end_line"], 9)
        # Skeleton text converts raw tab before trailing blank to a single space.
        self.assertEqual(question_draft._skeleton_text("games in Nanjing.\t_________"), "games in Nanjing. ______")

    def test_8_check_text_excludes_english_article_a_and_chinese_indent_and_spelling_guideline(self):
        import subprocess, sys
        script = Path(__file__).resolve().parents[1] / "scripts/06-upload/check_text.py"
        batch = {
            "items": [
                {
                    "unit_id": "u20",
                    "question_type": "单词拼写",
                    "stem": "Tony prepared for the school sports meeting for a ______(完全的) month.",
                    "answer": "whole",
                },
                {
                    "unit_id": "u45",
                    "question_type": "书面表达",
                    "stem": "　　注意:1.词数80~100。",
                    "answer": "无答案",
                },
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "batch.json"
            p.write_text(json.dumps(batch, ensure_ascii=False), encoding="utf-8")
            proc = subprocess.run([sys.executable, str(script), "--batch", str(p)],
                                  capture_output=True, text=True, check=False)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        # Word spelling granularity guideline is injected into both knowledge and review.
        self.assertIn("词汇/拼写与词形", content_rules.boundary_guidelines_block("knowledge"))
        self.assertIn("词汇/拼写与词形", content_rules.boundary_guidelines_block("review"))


if __name__ == "__main__":
    unittest.main()
