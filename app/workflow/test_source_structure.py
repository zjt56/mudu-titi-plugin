"""Source-boundary regressions; all fixtures are synthetic, never real batches."""
import copy
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from workflow import extract, structure_gate
from workflow.docx_extract import extract_docx
from workflow.preview import slice_range, slice_text
from workflow.question_content import audit_batch_source, audit_source_unit, question_parts


def cloze_block(first, last, label):
    passage = f"Passage {label} begins. " + " ".join(f"word __{n}__" for n in range(first, last + 1))
    rows = [label, passage]
    for n in range(first, last + 1):
        rows.extend([f"(　){n}.A.first{n} B.second{n}", "", f"C.third{n}", f"D.fourth{n}"])
    return "\n".join(rows)


def reading_body():
    return ("Ⅰ.阅读理解(共4小题)\nA\n"
            "The first article describes a school garden and its birds.\n"
            "56. What grows?\nA.Flowers B.Trees C.Grass D.Rice\n"
            "57. Who visits?\nA.Birds B.Cats C.Dogs D.Fish\n"
            "B\nThe second article explains the history of a faraway city.\n"
            "58. Where?\nA.North B.South C.East D.West\n"
            "59. When?\nA.Today B.Yesterday C.Tomorrow D.Never\n")


class SourceBoundaryRegressionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def make_batch(self, body, structure=None):
        root = self.root
        (root / "01_source").mkdir(exist_ok=True)
        if structure is None:
            structure, body, _ = extract.infer_structure(body)
        (root / "01_source/正文.txt").write_text(body, encoding="utf-8")
        (root / "01_source/structure.json").write_text(json.dumps(structure, ensure_ascii=False), encoding="utf-8")
        report = structure_gate.run_gate(root)
        confirmed = json.loads((root / "01_source/structure.confirmed.json").read_text())
        return root, report, confirmed

    def candidate(self, units):
        items = []
        source_lines = (self.root / "01_source/正文.txt").read_text(encoding="utf-8").splitlines()
        for index, unit in enumerate(units):
            span = unit.get("source_span") or {}
            if type(span.get("start_line")) is int and type(span.get("end_line")) is int:
                lines = source_lines[span["start_line"] - 1:span["end_line"]]
            else:
                lines = unit["stem"].splitlines()
            material = []
            from workflow.preview import _strong_number
            for line in lines:
                if _strong_number(line) is not None:
                    break
                material.append(line)
            children = []
            for number in unit["numbers"]:
                part = question_parts(lines, number)
                children.append({"number": number, "stem": part["stem"] or "______",
                                 "options": [f"{o['label']}.{o['text']}" for o in part["options"]]})
            row = {"unit_id": f"u{index}", "id": f"u{index}", "unit_kind": unit["unit_kind"],
                   "number": unit["numbers"][0], "numbers": unit["numbers"], "stem": "",
                   "material": "\n".join(material), "options": [], "subquestions": children}
            if unit["unit_kind"] == "standalone":
                row.update(children[0], material="", subquestions=[])
            items.append(row)
        return {"items": items}

    def test_cloze_40_50_keep_all_options_and_stop_before_next_material(self):
        body = "Ⅰ.完形填空(共20小题)\n" + cloze_block(31, 40, "A") + "\n" + cloze_block(41, 50, "B")
        root, report, confirmed = self.make_batch(body)
        self.assertTrue(report["passed"], report["blockers"])
        self.assertEqual([u["numbers"] for u in confirmed["units"]], [list(range(31, 41)), list(range(41, 51))])
        for unit, last in zip(confirmed["units"], [40, 50]):
            span = unit["source_span"]
            source_lines = body.splitlines()[span["start_line"] - 1:span["end_line"]]
            self.assertTrue(any(f"D.fourth{last}" in line for line in source_lines))
            self.assertEqual(len(question_parts(source_lines, last)["options"]), 4)
        self.assertNotIn("Passage B", confirmed["units"][0]["stem"])
        candidate = self.candidate(confirmed["units"])
        self.assertTrue(audit_batch_source(root, candidate)["passed"])
        for index in [0, 1]:
            missing = copy.deepcopy(candidate)
            missing["items"][index]["subquestions"][-1]["options"] = []
            audit = audit_batch_source(root, missing)
            self.assertFalse(audit["passed"])
            self.assertIn("OPTIONS_MISMATCH", {i["code"] for i in audit["issues"]})

    def test_cloze_section_instruction_can_repeat_without_hiding_foreign_prose(self):
        instruction = '阅读下列短文，从每小题所给的A、B、C、D四个选项中选出可以填入空白处的最佳选项。'
        body = ('Ⅰ.完形填空(共20小题)\n**' + instruction + '**\n'
                + cloze_block(31, 40, 'A') + '\n' + cloze_block(41, 50, 'B'))
        root, report, confirmed = self.make_batch(body)
        self.assertTrue(report['passed'], report['blockers'])
        candidate = self.candidate(confirmed['units'])
        # The source slices put the shared instruction only on the first group;
        # the assembled form legitimately repeats it above the second article.
        candidate['items'][1]['stem'] = instruction
        audit = audit_batch_source(root, candidate)
        self.assertTrue(audit['passed'], audit['issues'])
        self.assertEqual(candidate['items'][1]['stem'], instruction)
        missing = copy.deepcopy(candidate)
        missing['items'][0]['material'] = missing['items'][0]['material'].replace('**' + instruction + '**', '')
        self.assertIn('SOURCE_MATERIAL_MISSING', {x['code'] for x in audit_batch_source(root, missing)['issues']})
        leaked = copy.deepcopy(candidate)
        leaked['items'][1]['material'] += '\n' + next(
            line for line in confirmed['units'][0]['stem'].splitlines() if line.startswith('Passage A begins'))
        issues = audit_batch_source(root, leaked)['issues']
        self.assertTrue(any(x['code'] == 'FOREIGN_GROUP_MATERIAL' and 'Passage A begins' in x['message'] for x in issues), issues)

    def test_instruction_prefix_does_not_exempt_attached_article_text(self):
        instruction = '阅读下列短文，从每小题所给的A、B、C、D四个选项中选出可以填入空白处的最佳选项。'
        article = instruction + ' This exclusive article describes unusual birds in a remote mountain forest.'
        body = ('Ⅰ.完形填空(共2小题)\nA\n' + article + '\nText __31__.\n31.A.One B.Two C.Three D.Four\n'
                'B\nAnother article describes a young athlete.\nText __32__.\n32.A.One B.Two C.Three D.Four\n')
        root, report, confirmed = self.make_batch(body)
        self.assertTrue(report['passed'], report['blockers'])
        candidate = self.candidate(confirmed['units'])
        candidate['items'][1]['material'] += '\n' + article
        issues = audit_batch_source(root, candidate)['issues']
        self.assertTrue(any(x['code'] == 'FOREIGN_GROUP_MATERIAL' and 'exclusive' in x['message'] for x in issues), issues)

    def test_numbered_dialogue_leadin_is_still_exclusive_material(self):
        body = ('Ⅱ.长对话理解(共4小题)\n请认真听下面这一段完整对话，并用英语回答第6、7小题。\n'
                '6. First?\nA.One B.Two C.Three\n7. Second?\nA.Red B.Blue C.Green\n'
                '听下面一段对话，回答第8、9小题。\n'
                '8. Third?\nA.Cat B.Dog C.Bird\n9. Fourth?\nA.Bus B.Car C.Train\n')
        root, report, confirmed = self.make_batch(body)
        self.assertTrue(report['passed'], report['blockers'])
        candidate = self.candidate(confirmed['units'])
        # The full numbered lead-in exceeds the existing foreign-prose threshold.
        leadin = confirmed['units'][0]['stem'].splitlines()[0]
        from workflow.question_content import _generic_choice_instruction
        self.assertFalse(_generic_choice_instruction(leadin, '听力长对话理解'))
        candidate['items'][1]['material'] += '\n' + leadin
        self.assertIn('FOREIGN_GROUP_MATERIAL', {x['code'] for x in audit_batch_source(root, candidate)['issues']})

    def test_reading_group_keeps_own_article_and_rejects_foreign_material(self):
        root, report, confirmed = self.make_batch(reading_body())
        self.assertTrue(report["passed"], report["blockers"])
        first, second = confirmed["units"]
        self.assertIn("school garden", first["stem"])
        self.assertNotIn("faraway city", first["stem"])
        self.assertIn("faraway city", second["stem"])
        self.assertNotIn("school garden", second["stem"])
        candidate = self.candidate(confirmed["units"])
        self.assertTrue(audit_batch_source(root, candidate)["passed"])
        candidate["items"][0]["material"] += "\nThe second article explains the history of a faraway city."
        audit = audit_batch_source(root, candidate)
        self.assertIn("FOREIGN_GROUP_MATERIAL", {i["code"] for i in audit["issues"]})

    def test_long_dialogues_6_7_and_8_10_are_independent_and_complete(self):
        body = ("Ⅱ.长对话理解(共5小题)\n听下面一段对话，回答第6、7小题。\n"
                "6. First?\nA.One B.Two C.Three\n7. Second?\nA.Red B.Blue C.Green\n"
                "听下面一段对话，回答第8至10小题。\n"
                "8. Third?\nA.Cat B.Dog C.Bird\n9. Fourth?\nA.Bus B.Car C.Train\n"
                "10. Fifth?\nA.Home B.School C.Park\n")
        root, report, confirmed = self.make_batch(body)
        self.assertTrue(report["passed"], report["blockers"])
        self.assertEqual([u["numbers"] for u in confirmed["units"]], [[6, 7], [8, 9, 10]])
        self.assertNotIn("第8至10", confirmed["units"][0]["stem"])
        self.assertIn("第8至10", confirmed["units"][1]["stem"])
        self.assertIn("C.Park", confirmed["units"][1]["stem"])
        candidate = self.candidate(confirmed["units"])
        self.assertTrue(audit_batch_source(root, candidate)["passed"])
        candidate["items"][1]["material"] = ""
        self.assertIn("SOURCE_MATERIAL_MISSING", {i["code"] for i in audit_batch_source(root, candidate)["issues"]})

    def test_numberless_writing_requires_specific_section_and_adjacent_number(self):
        body = "Ⅰ.单项填空\n80. Choose.\nA.One B.Two\nⅩ.书面表达(共1小题)\nWrite about your town.\n1. its people\n2. its parks\n"
        self.assertEqual(slice_text(body.splitlines(), 81), [])
        self.assertEqual(slice_text(body.splitlines(), 999), [])
        structure = {"sections": [{"type": "单项填空", "questions": [80]},
                                    {"title": "Ⅹ.书面表达(共1小题)", "type": "书面表达", "questions": [81]}]}
        _, report, confirmed = self.make_batch(body, structure)
        self.assertFalse(report["passed"])
        self.assertIn("QUESTION_NUMBER_INFERRED", {b["code"] for b in report["blockers"]})
        self.assertIn("its parks", confirmed["units"][-1]["stem"])
        structure["sections"][-1]["questions"] = [999]
        _, report, confirmed = self.make_batch(body, structure)
        self.assertFalse(report["passed"])
        self.assertEqual(confirmed["units"][-1]["stem"], "")

    def test_2023_writing_stops_before_repeated_title_and_answer_summary(self):
        # Reproduce the real ordering with a synthetic text-only batch, never its files.
        title = '**2023****年安徽省初中学业水平考试**'
        body = ('[TABLE 1]\n<br>' + title + '\n[/TABLE]\n'
                'Ⅰ.单项填空(共1小题)\n80. Choose.\nA.One B.Two C.Three D.Four\n'
                'Ⅹ.书面表达(共1小题；满分20分)\n'
                '用英语写一篇短文，简述制作海报的经历。\n'
                '1. 须包含图示所有信息。\n2. 词数80~100。\n'
                'My Experience of Making a Poster\nLast week, I made a poster.\n')
        tail = ('\n' + title + '\n\n[TABLE 3]\n'
                '**题号** | **1~5** | **6~10** | **71~72**\n'
                '**答案** | **CCAAB** | **CABBC** | **BA**\n[/TABLE]\n'
                'Ⅰ.短对话理解\n1. C\n【解析】Answer discussion.\n')
        structure, main, appendix = extract.infer_structure(body + tail)
        self.assertEqual(main, body)
        self.assertEqual(appendix, tail.strip())
        _, report, confirmed = self.make_batch(main, structure)
        writing = confirmed['units'][-1]
        self.assertEqual(writing['numbers'], [81])
        self.assertIn('Last week, I made a poster.', writing['stem'])
        for leaked in ('[TABLE 3]', 'CCAAB', '【解析】', 'Ⅰ.短对话理解', '2023'):
            self.assertNotIn(leaked, writing['stem'])
        self.assertLessEqual(writing['source_span']['end_line'], len(main.splitlines()))
        self.assertFalse(report['passed'])
        self.assertIn('QUESTION_NUMBER_INFERRED', {x['code'] for x in report['blockers']})
        self.assertTrue(all('resolved' not in x for x in structure['unresolved']))

    def test_source_coverage_cannot_pass_from_structure_self_consistency(self):
        body = "Ⅰ.单项填空\n1. One?\nA.Yes B.No\n2. Two?\nA.Yes B.No\n"
        _, report, _ = self.make_batch(body, {"sections": [{"type": "单项填空", "questions": [1, 3]}]})
        codes = {i["code"] for i in report["blockers"]}
        self.assertIn("SOURCE_NUMBERS_UNDECLARED", codes)
        self.assertIn("SOURCE_NUMBERS_MISSING", codes)

    def test_source_missing_last_cloze_options_blocks_before_assembly(self):
        body = "Ⅰ.完形填空(共10小题)\n" + cloze_block(31, 40, "A")
        body = body.replace("(　)40.A.first40 B.second40\n\nC.third40\nD.fourth40", "(　)40.")
        _, report, _ = self.make_batch(body)
        self.assertFalse(report["passed"])
        self.assertTrue(any(p["code"] == "SOURCE_OPTIONS_INCOMPLETE" and p["number"] == 40 for p in report["blockers"]))

    def test_resolved_text_without_bound_evidence_cannot_clear_gate(self):
        # 一条 resolved 但缺少绑定证据的结构性 unresolved，不能被当成已解决而放行。
        structure = {"sections": [{"type": "单项填空", "questions": [1]}],
                     "unresolved": [{"code": "DUPLICATE_QUESTION_NUMBER", "message": "check source", "resolved": True, "resolution": "looks fine"}]}
        _, report, _ = self.make_batch("Ⅰ.单项填空\n1. Choose?\nA.Yes B.No", structure)
        self.assertFalse(report["passed"])
        self.assertIn("DUPLICATE_QUESTION_NUMBER", {i["code"] for i in report["blockers"]})

    def test_gate_binds_actual_input_and_confirmed_bytes(self):
        root, report, _ = self.make_batch("Ⅰ.单项填空\n1. Choose?\nA.Yes B.No\n")
        for relative, digest in report["input_hashes"].items():
            self.assertEqual(hashlib.sha256((root / relative).read_bytes()).hexdigest(), digest)
        self.assertIsNone(report["original_sha256"])

    def test_reading_answer_instruction_before_f_label_belongs_to_new_group(self):
        body = ("Ⅰ.阅读理解(共3小题)\nE\nFirst article.\n70. Choose?\nA.One B.Two C.Three D.Four\n"
                "阅读下面短文，并用英语回答问题。\nF\nSecond article.\n71. Explain?\n72. Why?\n")
        _, report, confirmed = self.make_batch(body)
        self.assertTrue(report["passed"], report["blockers"])
        first, second = confirmed["units"]
        self.assertNotIn("用英语回答", first["stem"])
        self.assertIn("用英语回答", second["stem"])
        self.assertEqual(second["source_expected_options"], 0)
        self.assertIn("用英语回答", "\n".join(slice_range(body.splitlines(), [71, 72])))

    def test_final_audit_rejects_lost_table_image_and_word_underline(self):
        body = ("Ⅰ.阅读理解(共1小题)\nA\n[TABLE 1]\nName | Value\nSun | Bright\n[/TABLE]\n"
                "This <u>marked</u> word matters. [IMG figure.png]\n56. Choose?\nA.One B.Two C.Three D.Four\n")
        root, report, confirmed = self.make_batch(body)
        self.assertTrue(report["passed"], report["blockers"])
        candidate = self.candidate(confirmed["units"])
        candidate["items"][0]["material"] = candidate["items"][0]["material"].replace("<u>marked</u>", "marked").replace("[IMG figure.png]", "")
        codes = {i["code"] for i in audit_batch_source(root, candidate)["issues"]}
        self.assertIn("TABLE_STRUCTURE_MISSING", codes)
        self.assertIn("SOURCE_UNDERLINE_MISSING", codes)
        self.assertIn("SOURCE_IMAGE_MISSING", codes)
        candidate["items"][0]["material"] = self.candidate(confirmed["units"])["items"][0]["material"]
        candidate["items"][0]["materialBlocks"] = [{"type": "table", "rows": [{"cells": [{"text": "Name"}, {"text": "Value"}]}, {"cells": [{"text": "Sun"}, {"text": "Bright"}]}]}]
        self.assertTrue(audit_batch_source(root, candidate)["passed"])

    def test_shared_dialogue_options_are_audited_at_group_layer(self):
        body = "Ⅰ.补全对话(共2小题)\nAnn: __51__\nBob: __52__\nA.Hello.\nB.Goodbye.\nC.Thanks.\n"
        root, report, confirmed = self.make_batch(body)
        self.assertTrue(report["passed"], report["blockers"])
        candidate = self.candidate(confirmed["units"])
        candidate["items"][0]["material"] = "Ann: __51__\nBob: __52__"
        candidate["items"][0]["options"] = ["A.Hello.", "B.Goodbye.", "C.Thanks."]
        self.assertTrue(audit_batch_source(root, candidate)["passed"])
        candidate["items"][0]["options"].pop()
        self.assertIn("SHARED_OPTIONS_MISMATCH", {i["code"] for i in audit_batch_source(root, candidate)["issues"]})

    def test_long_standalone_keeps_options_beyond_twelve_lines(self):
        body = ["1. Read the following.", *[f"Continuation line {i}" for i in range(15)], "A.One B.Two C.Three D.Four", "2. Next."]
        self.assertIn("D.Four", "\n".join(slice_text(body, 1)))
        self.assertNotIn("Next", "\n".join(slice_text(body, 1)))

    def test_docx_run_formatting_and_breaks_preserve_last_cloze_options(self):
        w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        source = self.root / "source.docx"
        xml = (f'<w:document xmlns:w="{w}"><w:body>'
               '<w:p><w:r><w:t>Ⅰ.完形填空(共2小题)</w:t></w:r></w:p>'
               '<w:p><w:r><w:t>A</w:t></w:r></w:p>'
               '<w:p><w:r><w:t>Text __39__ and __40__.</w:t></w:r></w:p>'
               '<w:p><w:r><w:t>39.A.first B.second C.third D.fourth</w:t></w:r></w:p>'
               '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>(　)40.</w:t></w:r>'
               '<w:r><w:t>A.first B.second</w:t><w:br/><w:br/><w:t>C.third</w:t><w:br/><w:t>D.fourth</w:t></w:r></w:p>'
               '</w:body></w:document>')
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("word/document.xml", xml)
        text = extract_docx(source, self.root / "native")["text"]
        _, report, confirmed = self.make_batch(text)
        self.assertTrue(report["passed"], report["blockers"])
        unit = confirmed["units"][0]
        # 选项属于 source_parts 的独立证据，不应强行拼进正式 stem；
        # 审计会按 source_span 读取原文，正式批次再写入 options。
        span = unit["source_span"]
        source_lines = text.splitlines()[span["start_line"] - 1:span["end_line"]]
        self.assertTrue(any("D.fourth" in line for line in source_lines))
        self.assertEqual(len(question_parts(source_lines, 40)["options"]), 4)


    def test_ampersand_inside_option_text_is_not_a_new_option_label(self):
        # Macon's B&B. 里的 “B.” 不是选项 B，否则会把 88/89 读成 A/B/C/D/B。
        lines = [
            '(　　)88.If your friend is coming for a visit,where will he stay for a night?',
            'A.In J&M Music Store.\tB.In Peth Market.',
            'C.At Times Coffee Shop.\tD.At Macon’s B&B.',
            '(　　)89.Which of the following is TRUE according to the passage?',
            'A.There is no breakfast at Macon’s B&B.',
            'B.You can’t listen to the CD before you buy it.',
            'C.Times Coffee Shop offers Jazz music every day.',
            'D.Kids under 12 can get free ice cream in Peth Market.',
        ]
        q88 = question_parts(lines, 88)
        self.assertEqual([o['label'] for o in q88['options']], ['A', 'B', 'C', 'D'])
        self.assertIn('B&B.', q88['options'][3]['text'])
        q89 = question_parts(lines, 89)
        self.assertEqual([o['label'] for o in q89['options']], ['A', 'B', 'C', 'D'])
        self.assertIn('B&B.', q89['options'][0]['text'])


if __name__ == "__main__":
    unittest.main()
