"""Isolated, deterministic pre-label objects; no model/batch execution."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from workflow import agent_tasks, question_draft


class QuestionDraftTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def source(self, text, specs, extraction=None):
        self.write("01_source/正文.txt", text)
        lines = text.splitlines()
        units = []
        for numbers, kind, hint, start, end in specs:
            units.append({"numbers": numbers, "unit_kind": kind, "type": hint,
                          "stem": "\n".join(lines[start - 1:end]),
                          "source_span": {"basis": "ai_classified", "start_line": start, "end_line": end}})
        self.write("01_source/structure.confirmed.json", {"units": units, "meta": {"source": "原卷", "year": "2022"}})
        self.write("01_source/structure.json", {"sections": [], "groups": []})
        if extraction is not None:
            self.write("01_source/extraction.json", extraction)
        return units

    def standalone(self):
        return self.source("1. She <u>reads</u> a book. A. reads B. read C. reading D. to read",
                           [([1], "standalone", "单项填空", 1, 1)])

    def test_span_binding_accepts_complete_content_reflowed_across_lines(self):
        locate = question_draft._locate_in_window
        self.assertEqual(locate('1. Choose the answer. A.Yes B.No',
                                ['标题', '1. Choose the answer.', 'A.Yes B.No'], 10),
                         (11, 12, True))
        self.assertEqual(locate('1. Choose the answer.\nA.Yes B.No',
                                ['1. Choose the answer. A.Yes B.No'], 10),
                         (10, 10, True))
        self.assertIsNone(locate('1. Choose the answer. A.Yes',
                                 ['1. Choose the answer. A.Yes B.No'], 10))

    def test_span_binding_rejects_dropped_words_options_and_table_cells(self):
        cases = [
            ("1.Choose the answer. A.Yes B.No", "1.Do not Choose the answer. A.Yes B.No"),
            ("1.Question? A.Yes", "1.Question? A.Yes B.No"),
            ("[TABLE 1]\na|c\n[/TABLE]", "[TABLE 1]\na|b|c\n[/TABLE]"),
            ("[TABLE 1]\nB\n[/TABLE]", "[TABLE 1]\nA\nB\n[/TABLE]"),
        ]
        for candidate, source in cases:
            with self.subTest(candidate=candidate):
                self.assertFalse(question_draft._ordered_within(candidate, source))
                unit = {"stem": candidate, "numbers": [1], "unit_kind": "group",
                        "source_span": {"start_line": 1, "end_line": len(source.splitlines()),
                                        "basis": "cleaned_source"}}
                bound, proof = question_draft._bound_source(unit, source, source, [])
                self.assertFalse(bound, proof)

    def test_span_binding_allows_only_whitespace_and_standalone_article_labels(self):
        source = "阅读下列短文。\nA\nThe full passage.\n1.Question? A.Yes B.No"
        candidate = "阅读下列短文。\nThe full passage.\n1.Question? A.Yes B.No"
        self.assertTrue(question_draft._ordered_within(candidate, source))
        self.assertFalse(question_draft._ordered_within(candidate.replace("full ", ""), source))
        self.assertFalse(question_draft._ordered_within("[TABLE 1]a|b[/TABLE]", "a|b"))

    def test_word_choice_retains_printed_number_without_inventing_stem(self):
        self.source('1.A. tie B. scarf C. card',
                    [([1], 'standalone', '听力关键词语选择', 1, 1)])
        row = question_draft.build(self.root)['items'][0]
        self.assertEqual(row['stem'], '1.')
        self.assertEqual(row['options'], ['A. tie', 'B. scarf', 'C. card'])

    def test_wire_shape_word_markup_and_empty_labels(self):
        self.standalone()
        result = question_draft.ensure(self.root)
        row = result["items"][0]
        self.assertEqual(row["id"], "q-1")
        self.assertEqual(row["unit_id"], "q-1")
        self.assertEqual(row["stem"], "She <u>reads</u> a book.")
        self.assertEqual(row["options"], ["A. reads", "B. read", "C. reading", "D. to read"])
        self.assertEqual(row["knowledge"], [])
        self.assertEqual(row["question_type"], "")
        self.assertEqual(row["knowledge_ids"], [])
        self.assertEqual(row["answer"], "")
        self.assertEqual(row["explanation"], "无")
        self.assertEqual(row["subquestions"], [])
        self.assertEqual(row["source"], "原卷")
        self.assertEqual(row["year"], 2022)
        self.assertFalse(result["release_ready"])
        self.assertEqual(result["status"], "candidate")
        self.assertEqual(result["label_status"], "not_started")
        self.assertEqual(result["source_bindings"]["q-1"]["start_line"], 1)
        self.assertFalse((self.root / "06_batch/batch.json").exists())
        self.assertFalse((self.root / "workflow/manifest.json").exists())

    def test_assembly_source_restore_cannot_erase_preassembled_leading_blank(self):
        from workflow import question_content
        self.source('(　　)43.<u>　　　　</u>,Chinese <u>traditions</u>. A. Yes B. No',
                    [([43], 'standalone', '单项填空', 1, 1)])
        payload = {'items': question_draft.build(self.root)['items']}
        question_content.restore_source_stems(self.root, payload)
        self.assertEqual(payload['items'][0]['stem'], '______,Chinese <u>traditions</u>.')
        self.assertEqual(payload['items'][0]['options'],
                         [{'key': 'A', 'text': 'Yes'}, {'key': 'B', 'text': 'No'}])

    def test_leading_and_inline_word_blanks_normalized_without_losing_words(self):
        text = ("(　　)43.<u>　　　　</u>,Chinese <u>traditions</u>. A. Yes B. No\n"
                "44. If <u>　　</u> happens, use <u>　 </u>. A. Yes B. No\n"
                "45. Say <u>hello</u> <u>　 </u>. A. <u>　 </u> B. <u>word</u>")
        self.source(text, [([43], "standalone", "单项填空", 1, 1),
                           ([44], "standalone", "单项填空", 2, 2),
                           ([45], "standalone", "单项填空", 3, 3)])
        rows = question_draft.build(self.root)["items"]
        self.assertEqual(rows[0]["stem"], "______,Chinese <u>traditions</u>.")
        self.assertEqual(rows[1]["stem"], "If ______ happens, use ______.")
        self.assertEqual(rows[2]["stem"], "Say <u>hello</u> ______.")
        self.assertEqual(rows[2]["options"], ["A. ______", "B. <u>word</u>"])
        self.assertEqual((self.root / "01_source/正文.txt").read_text(), text)

    def test_table_blanks_multiple_cells_and_per_cell_preserve_markup_and_variant(self):
        table = ("[TABLE 1]\nPlace | <u>　26　</u><br>and <u>　27　</u>\n"
                 "Work | <u>\n　28　\n</u><br/>with <u>farmers</u> and <u>　29　</u>\n[/TABLE]")
        text = "你将听到一篇短文。\n" + table
        self.source(text, [([26, 27, 28, 29], "group", "听力信息转换", 1, len(text.splitlines()))])
        row = question_draft.build(self.root)["items"][0]
        block = row["materialBlocks"][0]
        self.assertEqual(block["variant"], "information-conversion")
        self.assertEqual(block["rows"][0]["cells"][1]["text"], "__26__\nand __27__")
        self.assertEqual(block["rows"][1]["cells"][1]["text"], "__28__\nwith <u>farmers</u> and __29__")
        self.assertNotIn("<br", row["material"])
        for number in (26, 27, 28, 29):
            self.assertIn(f"__{number}__", row["material"])
        self.assertIn("<u>farmers</u>", row["material"])
        self.assertEqual((self.root / "01_source/正文.txt").read_text(), text)

    def test_proven_table_cells_normalize_blanks_without_changing_merges(self):
        table = "[TABLE 1]\n<u>　26　</u> | <u>　27　</u>\n[/TABLE]"
        metadata = {"tables": [{"number": 1, "text": table, "rows": [{"cells": [
            {"text": "<u>　26　</u><br><u>　27　</u>", "colspan": 2, "rowspan": 1},
            {"text": "covered", "merge_origin": [0, 0]}]}]}]}
        self.source(table, [([26, 27], "group", "听力信息转换", 1, 3)], metadata)
        cell = question_draft.build(self.root)["items"][0]["materialBlocks"][0]["rows"][0]["cells"][0]
        self.assertEqual(cell, {"text": "__26__\n__27__", "colspan": 2, "rowspan": 1})

    def test_unchanged_ensure_preserves_file_and_ignores_label_changes(self):
        self.standalone()
        first = question_draft.ensure(self.root)
        path = self.root / question_draft.PATH
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        self.write("04_knowledge/knowledge.json", {"items": [{"unit_id": "q-1", "knowledge": ["new"]}]})
        self.write("04_knowledge/explanations.json", {"items": [{"unit_id": "q-1", "explanation": "原解析"}]})
        self.assertEqual(question_draft.ensure(self.root), first)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)

    def test_ocr_is_separate_evidence_never_word_source(self):
        self.standalone()
        first = question_draft.ensure(self.root)
        self.write("01_source/pdf-ocr.json", {"pages": [{"text": "1. OCR rewrites words. A. wrong"}]})
        second = question_draft.ensure(self.root)
        self.assertEqual(first["items"], second["items"])
        self.assertEqual(first["source_bindings"], second["source_bindings"])
        self.assertNotEqual(first["content_sha256"], second["content_sha256"])
        self.assertNotIn("OCR rewrites", json.dumps(second["items"]))

    def test_group_material_children_and_option_image_ownership(self):
        text = ("Read this story. [IMG shared.png]\nA short <u>story</u>.\n"
                "2. Who went? A. [IMG child.png] B. Tom\n3. Why? A. Fun B. Work")
        self.source(text, [([2, 3], "group", "阅读理解", 1, 4)])
        self.write("01_source/media/shared.png", "shared-bytes")
        self.write("01_source/media/child.png", "child-bytes")
        result = question_draft.ensure(self.root)
        row = result["items"][0]
        self.assertEqual(row["id"], "group-2-3")
        self.assertIn("A short <u>story</u>.", row["material"])
        self.assertEqual([c["number"] for c in row["subquestions"]], [2, 3])
        first, second = row["subquestions"]
        self.assertEqual(first["stem"], "Who went?")
        self.assertEqual(second["stem"], "Why?")
        self.assertEqual(row["attachments"][0]["name"], "shared.png")
        self.assertEqual(first["attachments"][0]["name"], "child.png")
        self.assertEqual(first["attachments"][0]["placement"], "option")
        self.assertEqual(first["attachments"][0]["optionKey"], "A")
        self.assertNotIn("attachments", second)
        self.assertEqual(first["attachments"][0]["url"], "01_source/media/child.png")
        self.assertEqual(result["media_status"], "local_evidence_only_unverified")
        self.assertEqual(result["input_hashes"]["01_source/media/child.png"], hashlib.sha256(b"child-bytes").hexdigest())
        self.assertEqual(result["source_bindings"]["group-2-3"]["media_hashes"]["01_source/media/child.png"],
                         hashlib.sha256(b"child-bytes").hexdigest())
        self.write("01_source/media/child.png", "changed-image")
        changed = question_draft.ensure(self.root)
        self.assertNotEqual(result["source_bindings"]["group-2-3"]["media_hashes"],
                            changed["source_bindings"]["group-2-3"]["media_hashes"])

    def test_cloze_children_keep_options_and_only_source_blank_placeholder(self):
        text = "Read. He __26__ home and __27__ lunch.\n26. A. goes B. go\n27. A. has B. have"
        self.source(text, [([26, 27], "group", "完形填空", 1, 3)])
        row = question_draft.build(self.root)["items"][0]
        self.assertIn("__26__", row["material"])
        self.assertEqual([c["stem"] for c in row["subquestions"]], ["______", "______"])
        self.assertEqual(row["subquestions"][1]["options"], ["A. has", "B. have"])

    def test_shared_dialogue_options_stay_group_level(self):
        text = "Li: Hello. __31__\nTom: Fine. __32__\nA. How are you?\nB. Thank you."
        self.source(text, [([31, 32], "group", "补全对话", 1, 4)])
        row = question_draft.build(self.root)["items"][0]
        self.assertNotIn("A. How", row["material"])
        self.assertEqual(row["options"], ["A. How are you?", "B. Thank you."])
        self.assertTrue(all(c["options"] == [] and c["stem"] == "______" for c in row["subquestions"]))

    def test_table_uses_proven_word_spans_and_retains_source_grid(self):
        table = "[TABLE 1]\nTitle | \n__16__ | Detail\n[/TABLE]"
        text = "Listen and complete.\n" + table
        metadata = {"tables": [{"number": 1, "text": table, "rows": [
            {"cells": [{"text": "Title", "colspan": 2, "rowspan": 1}]},
            {"cells": [{"text": "__16__", "rowspan": 1, "colspan": 1}, {"text": "Detail", "rowspan": 1, "colspan": 1}]}]}]}
        self.source(text, [([16], "group", "听力信息转换", 1, 5)], metadata)
        result = question_draft.build(self.root)
        row = result["items"][0]
        self.assertIn("[TABLE 1]", row["material"])
        self.assertEqual(row["materialBlocks"][0]["rows"][0]["cells"][0]["colspan"], 2)
        self.assertNotIn("TABLE_LAYOUT_UNVERIFIED", [p["code"] for p in result["issues"]])

    def test_word_omitted_edge_columns_survive_draft_and_payload_validation(self):
        from workflow import upload_payload
        table = '[TABLE 1]\n | Poem A | Poem B | Poem C\nNotes |  |  | \n[/TABLE]'
        extraction = {'tables': [{'number': 1, 'text': table, 'grid_columns': 4, 'rows': [
            {'grid_before': 1, 'grid_after': 0, 'cells': [
                {'text': text, 'rowspan': 1, 'colspan': 1} for text in ('Poem A', 'Poem B', 'Poem C')]},
            {'grid_before': 0, 'grid_after': 0, 'cells': [{'text': 'Notes', 'rowspan': 1, 'colspan': 4}]}]}]}
        blocks = question_draft._table_blocks(table, extraction, [], 'poems')
        rows = blocks[0]['rows']
        self.assertEqual([c['text'] for c in rows[0]['cells']], ['', 'Poem A', 'Poem B', 'Poem C'])
        self.assertEqual(rows[1]['cells'][0]['colspan'], 4)
        result = upload_payload.build_payload({'items': [{'id': 'poems', 'unit_kind': 'standalone',
            'number': 1, 'numbers': [1], 'question_type': '阅读理解', 'stem': 'Read.',
            'knowledge': [], 'materialBlocks': blocks}]}, catalog=None)
        self.assertNotIn('MATERIAL_TABLE_GRID_INVALID', [e['code'] for e in result['errors']])
        after = {'rows': [{'grid_before': 0, 'grid_after': 2, 'cells': [{'text': 'A'}]}]}
        self.assertEqual([c['text'] for c in question_draft._source_table_rows(after)[0]['cells']], ['A', '', ''])
        self.assertEqual(extraction['tables'][0]['rows'][0]['grid_before'], 1)

    def test_unproven_table_and_media_report_issues_without_fabrication(self):
        text = "Read.\n[TABLE 1]\n__1__ | [IMG missing.png]\n[/TABLE]"
        self.source(text, [([1], "group", "任务型阅读", 1, 4)])
        result = question_draft.build(self.root)
        self.assertIn("TABLE_LAYOUT_UNVERIFIED", [p["code"] for p in result["issues"]])
        self.assertIn("MEDIA_MISSING", [p["code"] for p in result["issues"]])
        self.assertNotIn("attachments", result["items"][0])
        self.assertIn("[IMG missing.png]", result["items"][0]["material"])

    def test_changed_word_rejects_stale_confirmed_stem_and_keeps_identity(self):
        self.standalone()
        first = question_draft.ensure(self.root)
        self.write("01_source/正文.txt", "1. Different wording. A. new B. old")
        second = question_draft.ensure(self.root)
        self.assertEqual(second["items"][0]["unit_id"], "q-1")
        self.assertEqual(second["items"][0]["stem"], "")
        self.assertIn("SOURCE_SCOPE_UNPROVEN", [p["code"] for p in second["issues"]])
        self.assertNotEqual(first["input_hashes"], second["input_hashes"])

    def test_explicit_uid_preserved_and_group_scope_change_changes_derived_uid(self):
        self.source("2. One? A. Yes B. No\n3. Two? A. Yes B. No", [([2, 3], "group", "阅读理解", 1, 2)])
        confirmed = json.loads((self.root / "01_source/structure.confirmed.json").read_text())
        confirmed["units"][0]["unit_id"] = "reading-source-a"
        self.write("01_source/structure.confirmed.json", confirmed)
        self.assertEqual(question_draft.build(self.root)["items"][0]["unit_id"], "reading-source-a")
        self.source("2. One? A. Yes B. No\n3. Two? A. Yes B. No", [([2], "standalone", "单项填空", 1, 1), ([3], "standalone", "单项填空", 2, 2)])
        self.assertEqual([r["unit_id"] for r in question_draft.build(self.root)["items"]], ["q-2", "q-3"])

    def test_cached_draft_tamper_rebuilt_and_symlinks_refused(self):
        self.standalone()
        expected = question_draft.ensure(self.root)
        tampered = copy.deepcopy(expected)
        tampered["items"][0]["stem"] = "invented"
        self.write(question_draft.PATH, tampered)
        self.assertEqual(question_draft.ensure(self.root), expected)
        path = self.root / question_draft.PATH
        path.unlink()
        path.symlink_to(self.root / "01_source/正文.txt")
        with self.assertRaises(ValueError):
            question_draft.ensure(self.root)

    def test_label_and_assemble_tasks_receive_candidate_with_fingerprint(self):
        self.standalone()
        self.write("03_types/types.json", {"items": []})
        self.write("04_knowledge/knowledge.json", {"items": []})
        self.write("04_knowledge/explanations.json", {"items": []})
        for stage in ("types", "explanations", "knowledge", "assemble"):
            task = agent_tasks.build_task(self.root, stage, max_input_chars=500000)
            self.assertIn(question_draft.PATH, task["input_hashes"])
            self.assertIn("question_draft_usage", task["prompt"])
            self.assertIn("not_started", task["prompt"])
            self.assertIn("不能完成assemble或审核", task["prompt"])
            self.assertEqual(task["input_hashes"][question_draft.PATH], hashlib.sha256((self.root / question_draft.PATH).read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()

    def test_information_transfer_table_uses_upload_blank_format(self):
        text = ("你将听到一篇短文。\n[TABLE 1]\nOur town | \n"
                "Introduction | changed in the last <u>\u300026\u3000</u> years.\n"
                "Jobs | work in <u>\u300028\u3000</u> factories.\n[/TABLE]\n"
                "26.Question?\nA.one B.two\n27.Question?\nA.one B.two\n")
        self.source(text, [([26, 27], "group", "听力信息转换", 1, len(text.splitlines()))])
        result = question_draft.ensure(self.root)
        block = result["items"][0]["materialBlocks"][0]
        self.assertEqual(block.get("variant"), "information-conversion")
        cells = json.dumps(block, ensure_ascii=False)
        for number in (26, 28):
            self.assertIn(f"__{number}__", cells)
        self.assertNotIn("<u>", cells)

    def test_regular_reading_table_keeps_source_blank_markers(self):
        text = ("阅读短文。\n[TABLE 1]\nA | <u>\u300026\u3000</u>\n[/TABLE]\n"
                "26.Question?\nA.one B.two\n27.Question?\nA.one B.two\n")
        self.source(text, [([26, 27], "group", "阅读理解", 1, len(text.splitlines()))])
        result = question_draft.ensure(self.root)
        block = result["items"][0]["materialBlocks"][0]
        self.assertNotIn("variant", block)
        self.assertIn("<u>", json.dumps(block, ensure_ascii=False))
