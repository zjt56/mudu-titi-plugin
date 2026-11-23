"""D27 exact source marks: only bounded scoped identities support ownership checks."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import tempfile
import json
import unittest

from . import denoise, extract, preview, question_draft, review_child_source, structure_gate, upload_content
from . import sync_practice as sp
from .question_identity import assign_scoped


class SourceMarksTest(unittest.TestCase):
    def scoped(self, text, *rows):
        for row in rows:
            if row.get('source_span'):
                row['source_span'].setdefault('basis', 'ai_classified')
        return assign_scoped(list(rows), text)

    def test_no_year_marks_and_non_mark_labels(self):
        marks = ['[传统文化]', '[安徽人文]', '[榜样人物]', '[经典谚语]', '[熟词生义]', '[情境题]']
        self.assertEqual(sp.source_marks(' '.join(marks) + ' [IMG 2025.png] [TABLE 2025] '
                                        '【答案·2025】 【解析2025】 [参考答案2025]'), marks)

    def test_same_year_different_mark_not_kept_in_legacy(self):
        source = '2026期末测试卷\n1.[2025·六安月考]Hi?\n2.[2025·安徽中考]Bye?'
        rows = [{'stem': '[2025·安徽中考]Bye?'}]
        self.assertIn('[2025·六安月考]', sp.audit_batch_source_marks(rows, source)[0]['marks'])
        self.assertEqual(sp.audit_batch_source_marks([{'stem': source}], source), [])

    def test_wrong_question_position_in_same_group(self):
        text = '第一课时 Section A\nⅠ.阅读理解\nA story\n1.[传统文化]Who?\n2.Where?'
        rows = self.scoped(text, {'unit_kind': 'group', 'type': '阅读理解', 'numbers': [1, 2],
                                 'source_span': {'start_line': 2, 'end_line': 5}, 'stem': '阅读理解',
                                 'material': 'A story', 'subquestions': [
                                     {'number': 1, 'stem': 'Who?'}, {'number': 2, 'stem': '[传统文化]Where?'}]})
        before = deepcopy(rows)
        self.assertTrue(sp.audit_batch_source_marks(rows, text))
        self.assertEqual(rows, before)
        rows[0]['subquestions'][0]['stem'] = '[传统文化]Who?'
        rows[0]['subquestions'][1]['stem'] = 'Where?'
        self.assertEqual(sp.audit_batch_source_marks(rows, text), [])

    def test_heading_mark_requires_delivered_instruction(self):
        text = '第一课时 Section A\nⅠ.阅读理解[2025·六安月考]\nA story\n1.Who?'
        rows = self.scoped(text, {'unit_kind': 'group', 'type': '阅读理解', 'numbers': [1],
                                 'source_span': {'start_line': 3, 'end_line': 4}, 'stem': '阅读理解',
                                 'material': 'A story [2025·六安月考]',
                                 'subquestions': [{'number': 1, 'stem': 'Who?'}]})
        self.assertTrue(sp.audit_batch_source_marks(rows, text))
        rows[0]['material'] = 'A story'
        rows[0]['stem'] = '[2025·六安月考]阅读理解'
        self.assertEqual(sp.audit_batch_source_marks(rows, text), [])

    def test_metadata_and_explanation_never_count_as_delivery(self):
        text = '第一课时 Section A\nⅠ.单项填空\n1.[熟词生义]Hi?'
        rows = self.scoped(text, {'unit_kind': 'standalone', 'numbers': [1],
                                 'source_span': {'start_line': 3, 'end_line': 3}, 'stem': 'Hi?',
                                 'explanation': '[熟词生义]'})
        rows[0]['source_section']['extra'] = '[熟词生义]'
        self.assertTrue(sp.audit_batch_source_marks(rows, text))
        # Legacy unbound metadata also cannot satisfy a complete mark check.
        self.assertTrue(sp.audit_batch_source_marks([{'source_section': {'title': '[熟词生义]'}}], text))

    def test_mark_in_cloze_material_is_legal(self):
        text = '第一课时 Section A\nⅠ.完形填空\nA [榜样人物] story with 1.____.\n1.A.x B.y C.z D.w'
        rows = self.scoped(text, {'unit_kind': 'group', 'type': '完形填空', 'numbers': [1],
                                 'source_span': {'start_line': 2, 'end_line': 4}, 'stem': '完形填空',
                                 'material': 'A [榜样人物] story with 1.____.',
                                 'subquestions': [{'number': 1, 'stem': '1.'}]})
        self.assertEqual(sp.audit_batch_source_marks(rows, text), [])

    def test_final_items_without_span_resolve_within_section(self):
        text = '第一课时 Section A\nⅠ.单项填空\n1.[2025·六安月考]Hi?\n2.[2025·安徽中考]Bye?'
        rows = self.scoped(text,
                           {'unit_kind': 'standalone', 'numbers': [1], 'stem': '[2025·安徽中考]Hi?',
                            'source_span': {'start_line': 3, 'end_line': 3}},
                           {'unit_kind': 'standalone', 'numbers': [2], 'stem': '[2025·六安月考]Bye?',
                            'source_span': {'start_line': 4, 'end_line': 4}})
        for row in rows:
            row['number'] = row.pop('numbers')[0]
            row.pop('source_span')
        self.assertEqual(len(sp.audit_batch_source_marks(rows, text)), 2)
        rows[0]['stem'], rows[1]['stem'] = '[2025·六安月考]Hi?', '[2025·安徽中考]Bye?'
        self.assertEqual(sp.audit_batch_source_marks(rows, text), [])

    def test_unscoped_and_exam_remain_conservative(self):
        text = '第一课时 Section A\nⅠ.阅读理解\n1.[传统文化]Who?\n2.Where?'
        rows = [{'stem': 'Where? [传统文化]'}]
        self.assertEqual(sp.audit_batch_source_marks(rows, text), [])
        self.assertEqual(sp.audit_source_marks(rows, '2026期末测试卷\n1.[传统文化]Who?'), [])
        self.assertEqual(sp.audit_batch_source_marks(rows, '2026期末测试卷\n1.[传统文化]Who?'), [])

    def test_formal_child_options_and_structure_children(self):
        for key in ('children', 'subquestions'):
            text = '第一课时 Section A\nⅠ.阅读理解\n1.What?\nA.[熟词生义]word'
            rows = self.scoped(text, {'unit_kind': 'group', 'numbers': [1], 'type': '阅读理解',
                                     'source_span': {'start_line': 2, 'end_line': 4},
                                     key: [{'number': 1, 'stem': 'What?', 'options': ['A.[熟词生义]word']}]})
            self.assertEqual(sp.audit_batch_source_marks(rows, text), [])

    def test_material_mark_cannot_hide_in_child(self):
        text = '第一课时 Section A\nⅠ.阅读理解\nA [传统文化] story\n1.What?'
        rows = self.scoped(text, {'unit_kind': 'group', 'numbers': [1], 'type': '阅读理解',
                                 'source_span': {'start_line': 2, 'end_line': 4}, 'material': 'A story',
                                 'subquestions': [{'number': 1, 'stem': '[传统文化]What?'}]})
        self.assertTrue(sp.audit_batch_source_marks(rows, text))

    def test_table_delivered_text_counts_but_table_label_is_not_a_mark(self):
        source = '2026期末测试卷\n[传统文化] [TABLE 2025]'
        rows = [{'materialBlocks': [{'type': 'table', 'rows': [[{'text': '[传统文化]'}]]}]}]
        self.assertEqual(sp.audit_batch_source_marks(rows, source), [])

    def test_ambiguous_repeated_numbers_do_not_guess_ownership(self):
        text = '第一课时 Section A\nⅠ.阅读理解\nA\n1.[传统文化]Who?\nB\n1.Where?'
        rows = self.scoped(text, {'unit_kind': 'group', 'numbers': [1],
                                 'source_span': {'start_line': 3, 'end_line': 4},
                                 'subquestions': [{'number': 1, 'stem': '[传统文化]Who?'}]})
        rows[0].pop('source_span')
        self.assertEqual(sp.audit_batch_source_marks(rows, text), [])
        rows[0]['subquestions'][0]['stem'] = 'Who?'
        self.assertTrue(sp.audit_batch_source_marks(rows, text))

    def test_inline_two_children_use_their_own_marks(self):
        text = '第一课时 Section A\nⅠ.阅读理解\n1.[熟词生义]What?　2.Where?'
        rows = self.scoped(text, {'unit_kind': 'group', 'numbers': [1, 2],
                                 'source_span': {'start_line': 2, 'end_line': 3},
                                 'subquestions': [{'number': 1, 'stem': 'What?'},
                                                  {'number': 2, 'stem': '[熟词生义]Where?'}]})
        self.assertTrue(sp.audit_batch_source_marks(rows, text))

    def test_stale_section_or_untrusted_span_only_use_conservative_check(self):
        text = '第一课时 Section A\nⅠ.阅读理解\n1.[传统文化]Who?\n2.Where?'
        original = self.scoped(text, {'unit_kind': 'group', 'numbers': [1, 2],
                                     'source_span': {'start_line': 2, 'end_line': 4},
                                     'subquestions': [{'number': 1, 'stem': 'Who?'},
                                                      {'number': 2, 'stem': '[传统文化]Where?'}]})
        self.assertTrue(sp.audit_batch_source_marks(original, text))
        for field, value in [('end_line', 99), ('context', '第二课时'), ('occurrence', 99)]:
            rows = deepcopy(original)
            rows[0]['source_section'][field] = value
            self.assertEqual(sp.audit_batch_source_marks(rows, text), [])
        rows = deepcopy(original)
        rows[0]['source_span']['basis'] = 'guessed'
        self.assertEqual(sp.audit_batch_source_marks(rows, text), [])
        rows[0]['subquestions'][1]['stem'] = 'Where?'
        self.assertTrue(sp.audit_batch_source_marks(rows, text))

    def test_cloze_child_option_marks_belong_to_child(self):
        text = ('第一课时 Section A\nⅠ.完形填空\nA story with 1.____.\n'
                '1.A.[熟词生义]word B.other C.more D.last\n2.A.x B.y C.z D.w')
        rows = self.scoped(text, {'unit_kind': 'group', 'type': '完形填空', 'numbers': [1, 2],
                                 'source_span': {'start_line': 2, 'end_line': 5},
                                 'material': 'A story with 1.____.',
                                 'subquestions': [{'number': 1, 'stem': '1.', 'options': ['A.[熟词生义]word']},
                                                  {'number': 2, 'stem': '2.', 'options': ['A.x']}]})
        self.assertEqual(sp.audit_batch_source_marks(rows, text), [])
        rows[0]['subquestions'][0]['options'] = ['A.word']
        rows[0]['material'] += '[熟词生义]'
        self.assertTrue(sp.audit_batch_source_marks(rows, text))

    def test_script_uses_same_checker_without_source_writes(self):
        path = Path(__file__).resolve().parents[1] / 'scripts/06-upload/audit_source_marks.py'
        spec = importlib.util.spec_from_file_location('test_source_mark_script', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            batch, source = root / 'batch.json', root / 'source.txt'
            batch.write_text(json.dumps({'items': [{'stem': '[2025·安徽]x'}]}), encoding='utf-8')
            source.write_text('1.[2025·六安月考]Hi?', encoding='utf-8')
            before = source.read_bytes(), batch.read_bytes()
            self.assertEqual(module.main(['--batch', str(batch), '--source', str(source)]), 1)
            self.assertEqual((source.read_bytes(), batch.read_bytes()), before)

    def test_script_counts_only_question_body_when_structure_binding_is_current(self):
        import hashlib
        path = Path(__file__).resolve().parents[1] / 'scripts/06-upload/audit_source_marks.py'
        spec = importlib.util.spec_from_file_location('test_bound_source_mark_script', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)

            def put(relative, value):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(value, encoding='utf-8')
                return target

            def digest(target):
                return hashlib.sha256(target.read_bytes()).hexdigest()

            question = '1.[传统文化]Who?'
            original = put('original.txt', question + '\n答案与解析\n【熟词生义】\ncanned adj. 罐装的')
            batch = put('06_batch/batch_meta.json', json.dumps({'items': [{'stem': question}]}))
            body = put('01_source/正文.txt', question)
            structure = put('01_source/structure.json', '{}')
            confirmed = put('01_source/structure.confirmed.json', '{}')
            put('07_review/structure-gate.json', json.dumps({
                'passed': True, 'original_sha256': digest(original),
                'input_hashes': {p.relative_to(root).as_posix(): digest(p) for p in (body, structure, confirmed)},
            }))
            argv = ['--batch', str(batch), '--source', str(original)]
            self.assertEqual(module.main(argv), 0)
            put('06_batch/batch_meta.json', json.dumps({'items': [{'stem': '1.Who?'}]}))
            self.assertEqual(module.main(argv), 1)  # A real paper mark is still required.
            put('06_batch/batch_meta.json', json.dumps({'items': [{'stem': question}]}))
            for target in (body, original):
                with self.subTest(target=target.name):
                    before = target.read_bytes()
                    target.write_bytes(before + b'\nchanged')
                    self.assertEqual(module.main(argv), 1)  # No narrowed check on stale evidence.
                    target.write_bytes(before)


    def test_inline_phrase_answers_and_unnumbered_writing_keep_the_mark(self):
        text = ('九上 · 人教版 · 课时A计划\n'
                'Unit 1　The Changing World\n'
                '单元话题写作\n'
                '根据汉语提示完成下列句子\n'
                '1.Hefei ______(改变了很多) since I moved here.\n'
                '2.______(多亏了村民们的努力),our hometown is becoming more beautiful.\n'
                '3.Many rivers were polluted in my hometown ______(在过去).\n'
                '4.______(随着……的发展) Chinese railways are ______(越来越方便).\n'
                '5.The local government ______(已把这家旧工厂变成了) a beautiful park.\n'
                '[2023·**安徽**]假定你是李辉,你制作的关于家乡变化的海报在某英文网站展示并获得好评。'
                '请结合下面图示信息,用英语写一篇短文,向该网站浏览者简述你的制作经历。\n'
                '[IMG image22.jpg]\n'
                'My Experience of Making a Poster\n'
                'Last week,I made a poster of the great changes in my hometown.<u> </u>\n'
                '[IMG image9.jpg]审题思路[IMG image10.jpg]\n'
                '1.文体:记叙文。\n'
                '2.主题:介绍自己制作关于家乡变化的海报的经历。\n'
                '3.人称:以第一人称为主。\n'
                '4.时态:以一般过去时为主。\n'
                '[IMG image9.jpg]写作提纲[IMG image10.jpg]\n'
                '[IMG image23.jpg]\n'
                '**参考答案**\n'
                '[IMG image85.jpg]句型训练[IMG image86.jpg]\n'
                '1.has changed a lot　2.Thanks to the villagers’ efforts　3.in the past　'
                '4.With the development of;more and more convenient　5.has turned/changed the/this old factory into\n')
        self.assertEqual(sp.answer_key_start(text), 21)
        structure, body, appendix = extract.infer_structure(text)
        self.assertNotIn('参考答案', body)
        self.assertTrue(appendix.startswith('**参考答案**'))
        cleaned = denoise.clean_body(body)[0]
        units = structure_gate._build_units(structure['sections'], structure.get('groups', []),
                                            cleaned.splitlines())
        self.assertFalse(any('has turned/changed' in str(unit.get('stem')) for unit in units))
        writing = [unit for unit in units
                   if (unit.get('source_span') or {}).get('number_binding') == 'single_writing_section']
        self.assertEqual(len(writing), 1)
        span = writing[0]['source_span']
        self.assertEqual(span.get('printed_numbers'), [])
        section = writing[0]['source_section']
        self.assertIn('[2023·安徽]', sp.source_marks(section['title']))
        heading_line = section['start_line']
        self.assertEqual(sp.plain_text(cleaned.splitlines()[heading_line - 1]).strip(),
                         sp.plain_text(section['title']).strip())
        self.assertTrue(heading_line <= span['start_line'] <= span['end_line'] <= section['end_line'])

    def test_two_writing_prompts_and_labelled_sentence_key_keep_separate_scopes(self):
        text = (
            '九上 · 人教版 · 课时A计划\nUnit 1 The Changing World\n单元话题写作\n'
            '常从描述城市、乡镇的变化进行考查。\n'
            '根据汉语提示完成下列句子\n1.Hefei ______ (改变了很多).\n2.We ______ (取得了进步).\n'
            '[2023·安徽]请结合下图,用英语写一篇短文,简述你的制作经历。\n'
            'My Poster\nI made a poster.<u> </u>\n'
            '[IMG left.jpg]审题思路[IMG right.jpg]\n1.文体:记叙文。\n2.主题:制作海报。\n'
            '[新考法·思维导图提示]请根据下图,用英语向他们介绍科技如何改变生活。\n'
            '注意:1.词数80~100;\n2.适当发挥。\nTechnology has changed our lives.<u> </u>\n'
            '**参考答案**\n[IMG left.jpg]句型训练[IMG right.jpg]\n1.has changed a lot　2.made progress\n'
            '实战演练\nTechnology has changed our lives.\n'
        )
        structure, body, appendix = extract.infer_structure(text)
        self.assertNotIn('region', denoise.extract_meta(text.splitlines()))
        self.assertNotIn('made progress', body)
        units = structure_gate._build_units(structure['sections'], [], body.splitlines())
        self.assertEqual([unit['type'] for unit in units],
                         ['完成句子', '完成句子', '书面表达', '书面表达'])
        self.assertEqual(structure_gate._scoped_source_errors(units, structure['sections'], body.splitlines(), []), [])
        first, second = units[-2:]
        for unit in (first, second):
            self.assertEqual(unit['source_span']['printed_numbers'], [])
            self.assertTrue(preview._scoped_source_slice(unit, body.splitlines())[0].startswith('['))
        self.assertNotIn('文体:', first['stem'])
        self.assertIn('2.适当发挥', second['stem'])
        self.assertNotEqual(first['question_ids']['1'], second['question_ids']['1'])
        proof = review_child_source.prove_child(body, second, '1')
        self.assertEqual(proof['status'], 'proven', proof)
        self.assertIn('2.适当发挥', proof['text'])
        self.assertNotIn('My Poster', proof['text'])
        shortened = {**second, 'stem': '2.适当发挥。'}
        self.assertEqual(review_child_source.prove_child(body, shortened, '1')['status'], 'uncertain')

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '01_source').mkdir()
            (root / '01_source/正文.txt').write_text(body, encoding='utf-8')
            (root / '01_source/附录.txt').write_text(appendix, encoding='utf-8')
            (root / '01_source/structure.json').write_text(json.dumps(structure), encoding='utf-8')
            draft = question_draft.build(root, units=units)
            self.assertEqual(draft['issues'], [])
            self.assertIn('[2023·安徽]', draft['items'][-2]['stem'])
            self.assertIn('[新考法·思维导图提示]', draft['items'][-1]['stem'])
            self.assertEqual(upload_content.resolve_scoped_answer(root, units[1], 2),
                             {'answer': 'made progress', 'evidence': ['01_source/附录.txt:3']})
            (root / '01_source/附录.txt').write_text(appendix.replace('句型训练', '其他练习'), encoding='utf-8')
            self.assertIsNone(upload_content.resolve_scoped_answer(root, units[1], 2))

    def test_word_box_sentence_list_keeps_source_mark_on_child_stem(self):
        raw = (
            "第三课时　Grammar Focus\n"
            "Ⅱ. 用方框中所给单词的适当形式填空\n"
            "guide　towards　scarf　bore　I\n"
            "1.Charles never feels ________ when reading his favourite books.\n"
            "2.[2025·甘肃兰州改编]I am 15 years old and I’m able to look after ________ well.\n"
        )
        rows = self.scoped(raw, {
            "unit_kind": "group",
            "question_type": "选词填空",
            "numbers": [1, 2],
            "stem": "",
            "material": "guide　towards　scarf　bore　I",
            "source_span": {"start_line": 3, "end_line": 5, "basis": "cleaned_source"},
            "subquestions": [
                {"number": 1, "stem": "Charles never feels ______ when reading his favourite books."},
                {"number": 2, "stem": "[2025·甘肃兰州改编]I am 15 years old and I’m able to look after ______ well."},
            ],
        })
        self.assertEqual(sp.audit_source_marks(rows, raw), [])

        dropped = json.loads(json.dumps(rows))
        dropped[0]["subquestions"][1]["stem"] = "I am 15 years old and I’m able to look after ______ well."
        findings = sp.audit_source_marks(dropped, raw)
        self.assertEqual(len(findings), 1)
        self.assertIn("[2025·甘肃兰州改编]", findings[0]["marks"])
        self.assertTrue(any("所属子题2题干" in p for p in findings[0]["problems"]))


if __name__ == '__main__':
    unittest.main()
