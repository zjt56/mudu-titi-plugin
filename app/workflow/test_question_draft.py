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

    def test_shared_blank_dialogue_children_keep_shell_stem(self):
        """补全对话：材料带齐 1.–5. 空位时，子题题干只能是 ______，不回填后一行对白。"""
        from workflow import question_content
        text = ("A&B:Good morning,Ms Gao!\nC:1.______ What's your name?\nA:2.______\n"
                "C:Nice to meet you.\nA:3.______\nC:4.______\nB:My name is Peter.\n"
                "C:5.______\nB:Hi,Emma.\nA:Hi,Peter.")
        self.source(text, [([1, 2, 3, 4, 5], "group", "补全对话", 1, len(text.splitlines()))])
        payload = {"items": question_draft.build(self.root)["items"]}
        before = [c["stem"] for c in payload["items"][0]["subquestions"]]
        self.assertEqual(before, ["______"] * 5)  # 源草稿已正确
        question_content.restore_source_stems(self.root, payload)
        after = [c["stem"] for c in payload["items"][0]["subquestions"]]
        self.assertEqual(after, ["______"] * 5, "restore 不得把材料里的后一行对白抄进子题题干")

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

    def test_section_rubric_is_shared_by_every_reading_group(self):
        text = "\n".join([
            "2024年九年级英语",
            "Ⅷ.阅读理解(共4小题;每小题2分)",
            "第一节　阅读下列短文，从每小题所给的A、B、C、D四个选项中选出最佳选项。",
            "A",
            "Tom is a boy. He likes apples.",
            "1. What does Tom like?",
            "A. Apples B. Pears C. Cats D. Dogs",
            "2. Who is Tom?",
            "A. A boy B. A girl C. A cat D. A dog",
            "B",
            "Mary is a girl. She likes music.",
            "3. What does Mary like?",
            "A. Music B. Art C. Math D. PE",
            "4. Who is Mary?",
            "A. A boy B. A girl C. A cat D. A dog",
        ])
        rubric = "第一节　阅读下列短文，从每小题所给的A、B、C、D四个选项中选出最佳选项。"
        self.source(text, [([1, 2], "group", "阅读理解", 4, 9),
                           ([3, 4], "group", "阅读理解", 10, 15)])
        rows = question_draft.build(self.root)["items"]
        self.assertEqual([row["stem"] for row in rows], [rubric, rubric])
        # The shared rubric is not part of the later group's slice: its own
        # material/label must survive verbatim.
        self.assertTrue(rows[0]["material"].startswith("A\n"))
        self.assertTrue(rows[1]["material"].startswith("B\n"))
        self.assertNotIn("阅读下列短文", rows[1]["material"])
        for row in rows:
            self.assertEqual([child["number"] for child in row["subquestions"]], [1, 2] if row is rows[0] else [3, 4])
            self.assertFalse(any("阅读下列短文" in (child["stem"] or "") for child in row["subquestions"]))

    def test_cloze_ab_share_the_same_section_rubric_without_losing_material(self):
        text = "\n".join([
            "2024年九年级英语",
            "Ⅶ.完形填空(共4小题)",
            "阅读下列短文,从每小题所给的A、B、C、D四个选项中选出一个最佳选项。",
            "A",
            "Tom has a __1__ and a __2__.",
            "1. A. cat B. dog C. pen D. cup",
            "2. A. hat B. cap C. bag D. box",
            "B",
            "Mary has a __3__ and a __4__.",
            "3. A. cat B. dog C. pen D. cup",
            "4. A. hat B. cap C. bag D. box",
        ])
        rubric = "阅读下列短文,从每小题所给的A、B、C、D四个选项中选出一个最佳选项。"
        self.source(text, [([1, 2], "group", "完形填空", 4, 7),
                           ([3, 4], "group", "完形填空", 8, 11)])
        rows = question_draft.build(self.root)["items"]
        self.assertEqual([row["stem"] for row in rows], [rubric, rubric])
        self.assertTrue(rows[0]["material"].startswith("A\n"))
        self.assertTrue(rows[1]["material"].startswith("B\n"))
        self.assertIn("__3__", rows[1]["material"])

    def test_reading_subsection_uses_its_own_rubric_not_the_choice_one(self):
        text = "\n".join([
            "2024年九年级英语",
            "Ⅷ.阅读理解(共4小题)",
            "第一节　阅读下列短文，从每小题所给的A、B、C、D四个选项中选出最佳选项。",
            "A",
            "Tom is a boy.",
            "1. Who is Tom?",
            "A. A boy B. A girl C. A cat D. A dog",
            "2. Who is Tom?",
            "A. A boy B. A girl C. A cat D. A dog",
            "第二节　阅读下面短文，并用英语回答问题（请注意每小题的词数要求）。",
            "B",
            "Mary is a girl.",
            "3. Who is Mary?",
            "4. Who is Mary?",
        ])
        choice = "第一节　阅读下列短文，从每小题所给的A、B、C、D四个选项中选出最佳选项。"
        answer = "第二节　阅读下面短文，并用英语回答问题（请注意每小题的词数要求）。"
        self.source(text, [([1, 2], "group", "阅读理解", 4, 9),
                           ([3, 4], "group", "阅读理解", 11, 14)])
        rows = question_draft.build(self.root)["items"]
        self.assertEqual(rows[0]["stem"], choice)
        self.assertEqual(rows[1]["stem"], answer)

    def test_missing_section_rubric_stays_empty_instead_of_being_invented(self):
        text = "\n".join([
            "2024年九年级英语",
            "Ⅷ.阅读理解(共4小题)",
            "A",
            "Tom is a boy.",
            "1. Who is Tom?",
            "A. A boy B. A girl C. A cat D. A dog",
            "2. Who is Tom?",
            "A. A boy B. A girl C. A cat D. A dog",
        ])
        self.source(text, [([1, 2], "group", "阅读理解", 3, 8)])
        self.assertEqual(question_draft.build(self.root)["items"][0]["stem"], "")

    def test_shared_rubric_review_regressions(self):
        from workflow.shared_rubric import section_rubric
        from workflow.question_content import _generic_choice_instruction
        rubric = '阅读下列短文，从每小题所给的A、B、C、D四个选项中选出最佳选项。'
        tail = '将答案填写在答题卡上。'
        for prefix in ('第一部分　', '**第一节　'):
            line = prefix + rubric + ('**' if prefix.startswith('**') else '')
            self.assertTrue(_generic_choice_instruction(line, '阅读理解'))
            self.assertEqual(section_rubric(['Ⅷ.阅读理解', line, 'A', 'Article'], 4, '阅读理解'), [line])
        lines = ['Ⅷ.阅读理解', rubric, tail, 'A', 'Article A', '1. Question',
                 '阅读下面短文，回答第2至3题。', 'B', 'Article B', '2. Question', 'C']
        self.assertEqual(section_rubric(lines, 11, '阅读理解'), [rubric, tail])
        self.assertEqual(section_rubric(lines, 11, '听力长对话理解'), [])
        self.assertEqual(section_rubric(['Ⅷ.阅读理解', 'A', 'Article',
                                      '阅读下面短文，回答第2至3题。', 'B'], 5, '阅读理解'), [])

    def test_shared_rubric_survives_preview_payload_and_is_required_by_audit(self):
        from workflow import extract, structure_gate, preview, question_content, upload_payload
        from workflow.test_source_structure import reading_body
        from workbench.server import source_blocks
        rubric = '阅读下列短文，从每小题所给的A、B、C、D四个选项中选出最佳选项。'
        tail = '将答案填写在答题卡指定位置，所有答案必须使用黑色笔填写。'
        body = reading_body().replace('A\n', rubric + '\n' + tail + '\nA\n', 1)
        structure, body, _ = extract.infer_structure(body)
        from workflow import denoise
        cleaned = denoise.clean_body(body)[0]
        units = structure_gate._build_units(structure['sections'], structure['groups'], cleaned.splitlines())
        self.write('01_source/正文.txt', body)
        self.write('01_source/structure.json', structure)
        self.write('01_source/structure.confirmed.json', {'units': units})
        draft = question_draft.build(self.root)
        rows = draft['items']
        self.assertEqual([row['stem'] for row in rows], [rubric + '\n' + tail] * 2)
        report = question_content.audit_batch_source(self.root, {'items': rows})
        self.assertEqual(report['issues'], [])
        chunk = preview.source_slice(self.root, [58, 59])
        self.assertIn(rubric, chunk)
        self.assertIn(rubric, json.dumps(source_blocks(chunk), ensure_ascii=False))
        for row in rows:
            row['question_type'] = '阅读理解'
        payload = upload_payload.build_payload({'items': rows}, catalog=None)['payload']
        self.assertEqual(payload['items'][1]['stem'], rubric + '\n' + tail)
        rows[1]['stem'] = ''
        problems = question_content.audit_batch_source(self.root, {'items': rows})['issues']
        self.assertIn('SOURCE_SHARED_RUBRIC_MISSING', [p['code'] for p in problems])

    def test_raw_source_coordinates_do_not_borrow_future_rubric(self):
        lines = ['扫码练听力'] * 5 + ['Ⅰ.任务型阅读',
                 '阅读下面短文，回答问题（每题不超过五个词）。', 'A', 'Tom is here.', '1. Who is here?',
                 'Ⅱ.任务型阅读', '阅读下面短文，回答问题（每题不超过十个词）。',
                 'B', 'Mary is here.', '2. Who is here?']
        units = self.source('\n'.join(lines), [([1], 'group', '任务型阅读', 8, 10)])
        units[0]['source_span']['basis'] = 'source_lines'
        self.write('01_source/structure.confirmed.json', {'units': units})
        self.assertEqual(question_draft.build(self.root)['items'][0]['stem'], lines[6])

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


class ImagePrefixedOptionsTest(unittest.TestCase):
    """图片前置选项 `[IMG x]A. …` 按顺序把图配到 A/B/C/D，不错位、不丢失。"""

    def test_images_bound_in_order(self):
        raw = ("( )4.[新考法·图片理解]Jimmy can't use ______ now.\n"
               "[IMG image14.jpeg]A.\t[IMG image15.jpeg]B.\n"
               "[IMG image16.jpeg]C.\t[IMG image17.jpeg]D.\n")
        r = question_draft._image_prefixed_options(raw)
        self.assertIsNotNone(r)
        got = {o["key"]: o["text"] for o in r["options"]}
        self.assertEqual(got, {"A": "[IMG image14.jpeg]", "B": "[IMG image15.jpeg]",
                               "C": "[IMG image16.jpeg]", "D": "[IMG image17.jpeg]"})
        self.assertIn("Jimmy", r["stem"])

    def test_non_prefixed_returns_none(self):
        # 正常 `A.[IMG]` 后置形态与纯文本都不走此分支，返回 None。
        self.assertIsNone(question_draft._image_prefixed_options(
            "( )1. Pick one.\nA.[IMG a.jpeg] B.[IMG b.jpeg]\n"))
        self.assertIsNone(question_draft._image_prefixed_options(
            "( )1. Q?\nA.One B.Two C.Three D.Four\n"))


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
