import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from workflow.upload_content import inspect_content


class UploadContentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name); (self.batch / '01_source').mkdir()
        self.text = ('Ⅵ.完形填空\nA\n【文章大意】游泳文章。\n31.A 【解析】游泳原因。\n'
                     'Ⅷ.阅读理解\nA\n【文章大意】南极文章。\n56.D 【解析】南极气温。\n'
                     'B\n【文章大意】舞狮文章。\n59.B 【解析】祈愿好运。\n')
        self.path = self.batch / '01_source/附录.txt'; self.path.write_text(self.text)
        (self.batch / '01_source/正文.txt').write_text(
            ''.join(f'{n}.题目\n' for n in range(1, 31)) + 'Ⅵ.完形填空\n' +
            ''.join(f'{n}.题目\n' for n in range(31, 51)) + 'Ⅷ.阅读理解\n' +
            ''.join(f'{n}.题目\n' for n in range(51, 101)))

    def test_primary_text_wins_over_duplicate_pdf_ocr_explanations(self):
        materials = self.batch / '01_source/materials'
        materials.mkdir()
        (materials / 'attachment_001.txt').write_text(
            self.text.replace('游泳原因。', 'OCR 改写的游泳原因。'))
        payload = {'items': [{'number': 31, 'explanation': '游泳原因。', 'articleSummary': '游泳文章。'}]}
        result = inspect_content(self.batch, payload, strict=True)
        self.assertTrue(result['passed'], result)
        payload['items'][0]['explanation'] = 'OCR 改写的游泳原因。'
        self.assertFalse(inspect_content(self.batch, payload, strict=True)['passed'])

    def test_reference_copy_role_excludes_it_from_ambiguity(self):
        materials = self.batch / '01_source/materials'; materials.mkdir()
        (materials / 'attachment_001.txt').write_text(
            'Ⅵ.完形填空\n31.A 【解析】OCR 改写的游泳原因。\n')
        (self.batch / '01_source/material-roles.json').write_text(json.dumps({
            'documents': [
                {'input': 'source', 'role': 'paper', 'content_authority': True,
                 'text_path': '01_source/正文.txt'},
                {'input': 'attachment_001', 'role': 'structure_reference',
                 'content_authority': False,
                 'text_path': '01_source/materials/attachment_001.txt'}]}, ensure_ascii=False))
        payload = {'items': [{'number': 31, 'explanation': '游泳原因。', 'articleSummary': '游泳文章。'}]}
        result = inspect_content(self.batch, payload, strict=True)
        self.assertTrue(result['passed'], result)
        # 权威来源内部再出现同题另一份解析，仍然算冲突，不能借参考件规则放行。
        self.path.write_text(self.text + '31.B 【解析】另一种说法。\n')
        self.assertFalse(inspect_content(self.batch, payload, strict=True)['passed'])

    def test_summary_owner_survives_digit_inside_explanation(self):
        # "at age 5." is sentence text, not a second question number. The row must
        # still be recognised so the passage summary keeps its owner.
        self.path.write_text(
            'Ⅷ.阅读理解\nB\n　　【文章大意】天才少年Mike文章。\n'
            '59.A　【解析】根据第二段第二句“He began to study computer programming at age 5.”可知。\n'
            '60.C　【解析】推理判断题。\n')
        item = {'unit_id': 'b', 'numbers': [59, 60], 'articleSummary': '天才少年Mike文章。',
                'subquestions': [{'number': 59, 'explanation': '根据第二段第二句“He began to study computer programming at age 5.”可知。'},
                                 {'number': 60, 'explanation': '推理判断题。'}]}
        report = inspect_content(self.batch, {'items': [item]}, strict=True)
        self.assertTrue(report['passed'], report)
        owners = [row['owner'] for row in report['summaries'] if row['text'] == '天才少年Mike文章。']
        self.assertEqual(owners, [59], report['summaries'])

    def test_summary_owner_survives_pure_answer_lines(self):
        # Answers printed alone with the explanation on the next paragraph must
        # still let the group summary bind to its first question.
        self.path.write_text(
            'Ⅷ.阅读理解\nF\n　　【文章大意】智者和儿子的故事。\n'
            '73.Because he had no goal in life.\n【解析】根据第一段第二句可知。\n'
            '74.He thought everything was nice.\n【解析】根据第四段最后一句可知。\n')
        item = {'unit_id': 'f', 'numbers': [73, 74], 'articleSummary': '智者和儿子的故事。',
                'subquestions': [{'number': 73, 'explanation': '根据第一段第二句可知。'},
                                 {'number': 74, 'explanation': '根据第四段最后一句可知。'}]}
        report = inspect_content(self.batch, {'items': [item]}, strict=True)
        self.assertTrue(report['passed'], report)
        owners = [row['owner'] for row in report['summaries'] if row['text'] == '智者和儿子的故事。']
        self.assertEqual(owners, [73], report['summaries'])

    def test_markdown_prefixed_annotation_heading_ends_explanation(self):
        self.path.write_text('Ⅵ.完形填空\n31.A 【解析】游泳原因。\n##【词汇积累】swim v.游泳\n')
        payload = {'items': [{'number': 31, 'explanation': '游泳原因。'}]}
        self.assertTrue(inspect_content(self.batch, payload, strict=True)['passed'])

    def test_stray_explanation_binds_to_preceding_numbered_answer(self):
        # Answer on its own line, explanation in the next paragraph without a number.
        self.path.write_text('Ⅷ.阅读理解\n74. It wasn\u2019t easy.\n\n'
                             '【解析】根据第三段可知,作者感觉不容易。\n')
        payload = {'items': [{'number': 74, 'explanation': '根据第三段可知,作者感觉不容易。'}]}
        self.assertTrue(inspect_content(self.batch, payload, strict=True)['passed'])
        # A rewritten explanation must still be rejected.
        payload['items'][0]['explanation'] = '根据第三段可知,作者感觉很困难。'
        self.assertFalse(inspect_content(self.batch, payload, strict=True)['passed'])

    def test_quote_style_difference_restores_authoritative_text(self):
        materials = self.batch / '01_source/materials'; materials.mkdir()
        (materials / 'attachment_001.txt').write_text(
            'Ⅷ.阅读理解\n62. D 【解析】根据最后一段中的 "farm." 可知,Gill 写书的灵感来自农场。\n')
        payload = {'items': [{'number': 62,
            'explanation': '根据最后一段中的\u201cfarm.\u201d可知,Gill写书的灵感来自农场。'}]}
        result = inspect_content(self.batch, payload, strict=True)
        self.assertTrue(result['passed'], result)

    def test_attachment_only_explanation_remains_available(self):
        materials = self.batch / '01_source/materials'
        materials.mkdir()
        (materials / 'attachment_001.txt').write_text('Ⅷ.阅读理解\n60.A 【解析】仅附件提供的解析。\n')
        payload = {'items': [{'number': 60, 'explanation': '仅附件提供的解析。'}]}
        self.assertTrue(inspect_content(self.batch, payload, strict=True)['passed'])

    def test_explanation_starting_with_option_letter_keeps_first_character(self):
        self.path.write_text('Ⅵ.完形填空\n31.B 【解析】A项意为“观看”;B项意为“开始”。\n')
        result = inspect_content(self.batch, {'items': [{'number': 31,
            'explanation': 'A项意为“观看”;B项意为“开始”。'}]}, strict=True)
        self.assertTrue(result['passed'], result)

    def test_writing_hint_at_end_of_primary_word_body(self):
        body = 'Ⅹ.书面表达（共1小题）\n请写一封信。\n【思路点拨】先总写，再提出建议。\n'
        (self.batch / '01_source/正文.txt').write_text(body)
        self.path.write_text('【参考范文】\nDear Jack,\n范文。\n')
        confirmed = {'units': [{'unit_id': 'q-96', 'type': '书面表达',
                               'unit_kind': 'standalone', 'numbers': [96]}]}
        (self.batch / '01_source/structure.confirmed.json').write_text(json.dumps(confirmed))
        payload = {'items': [{'unit_id': 'q-96', 'number': 96, 'explanation': '先总写，再提出建议。'}]}
        self.assertTrue(inspect_content(self.batch, payload, strict=True)['passed'])
        payload['items'][0]['explanation'] = '先总写，再生成建议。'
        self.assertFalse(inspect_content(self.batch, payload, strict=True)['passed'])

    def writing_fixture(self):
        body = ('Ⅸ.单词拼写\n80.题目\nⅩ.书面表达（共1小题；满分20分）\n'
                '请写一篇英语短文。\n要点：\n1.活动目的；\n2.使用方法。\n')
        appendix = ('Ⅸ.单词拼写\n80.handed\nⅩ.书面表达\n'
                    '[IMG image12.jpeg]思路点拨[IMG image13.jpeg]\n'
                    '1.文体:说明文。\n2.主题:介绍灭火器的使用方法。\n'
                    '3.人称:以第一人称为主。\n4.时态:以一般过去时和一般现在时为主。\n'
                    '[IMG image12.jpeg]参考范文[IMG image13.jpeg]\n范文。\n')
        (self.batch / '01_source/正文.txt').write_text(body)
        self.path.write_text(appendix)
        structure = {'sections': [{'type': '书面表达', 'title': 'Ⅹ.书面表达（共1小题；满分20分）',
                                   'source_line': 3, 'questions': [81], 'declared_count': 1}]}
        confirmed = {'units': [{'type': '书面表达', 'unit_kind': 'standalone', 'numbers': [81],
                                'source_span': {'number_binding': 'single_writing_section'}}]}
        for name, data in [('structure.json', structure), ('structure.confirmed.json', confirmed)]:
            (self.batch / '01_source' / name).write_text(json.dumps(data, ensure_ascii=False))
        (self.batch / '07_review').mkdir(exist_ok=True)
        self.bind_writing_gate()
        return '1.文体:说明文。\n2.主题:介绍灭火器的使用方法。\n3.人称:以第一人称为主。\n4.时态:以一般过去时和一般现在时为主。'

    def bind_writing_gate(self):
        paths = ('01_source/正文.txt', '01_source/structure.json', '01_source/structure.confirmed.json')
        hashes = {p: hashlib.sha256((self.batch / p).read_bytes()).hexdigest() for p in paths}
        (self.batch / '07_review/structure-gate.json').write_text(json.dumps({'passed': True, 'input_hashes': hashes}))

    def test_prompts_strengthen_label_handling_and_json_discipline(self):
        from workflow import content_rules, agent_tasks, local_agent
        rules = content_rules.RULES
        for keyword in ('思路点拨', '字段标题处理', 'JSON 输出', '只返回一个根 JSON 对象'):
            self.assertIn(keyword, rules)
        self.assertIn('mandatory dimension', agent_tasks.RULES['knowledge'])
        self.assertIn('JSON discipline', agent_tasks.RULES['knowledge'])
        self.assertIn('EXACTLY ONE root object', local_agent.INSTRUCTIONS)

    def test_appendixed_writing_hint_explanation_passes_strict(self):
        # The original 解析 for 书面表达 is the appendix 【思路点拨】 paragraph; it is not
        # a question line, so strict validation must still accept the verbatim hint.
        (self.batch / '01_source/正文.txt').write_text('Ⅹ.书面表达(共1小题;满分20分)\n请写一封自荐信。\n')
        self.path.write_text('Ⅹ.书面表达\n\u3000\u3000【思路点拨】本篇要求写一封自荐信，注意人称与时态。\n\u3000\u3000【参考范文】\nDear Sir,\nI am Li Hua.\n')
        (self.batch / '01_source/structure.confirmed.json').write_text(json.dumps({'units': [
            {'unit_id': 'q-81', 'type': '书面表达', 'unit_kind': 'standalone', 'numbers': [81]}]}))
        payload = {'items': [{'unit_id': 'q-81', 'number': 81,
                              'explanation': '本篇要求写一封自荐信，注意人称与时态。'}]}
        result = inspect_content(self.batch, payload, strict=True)
        self.assertTrue(result['passed'], result)
        payload['items'][0]['explanation'] = '本篇要求写一封自荐信，注意人称。'
        self.assertFalse(inspect_content(self.batch, payload, strict=True)['passed'])

    def test_unprinted_writing_requires_complete_owned_source(self):
        explanation = self.writing_fixture()
        def report(value=explanation, number=81):
            return inspect_content(self.batch, {'items': [{'number': number, 'explanation': value}]})
        self.assertTrue(report()['passed'], report())
        for value in (explanation.split('\n')[0], explanation + '\n范文。', explanation.replace('说明文', '记叙文')):
            self.assertIn('EXPLANATION_SOURCE_UNVERIFIED', {e['code'] for e in report(value)['warnings']})
        self.assertIn('EXPLANATION_SOURCE_UNVERIFIED', {e['code'] for e in report(number=80)['warnings']})

    def test_writing_hints_reject_embedded_question_and_allow_decimal(self):
        explanation = self.writing_fixture()
        for suffix in ('82.【解析】另一题', '82.A 另一题', '【解析】另一题'):
            with self.subTest(suffix=suffix):
                changed = explanation.replace('2.主题:介绍灭火器的使用方法。',
                                              '2.主题:介绍灭火器的使用方法。' + suffix)
                original = self.path.read_text()
                self.path.write_text(original.replace(explanation, changed))
                result = inspect_content(self.batch, {'items': [{'number': 81, 'explanation': changed}]})
                self.assertIn('EXPLANATION_SOURCE_UNVERIFIED', {e['code'] for e in result['warnings']})
                self.path.write_text(original)
        decimal = explanation.replace('2.主题:介绍灭火器的使用方法。',
                                      '2.主题:介绍3.14的用途。')
        self.path.write_text(self.path.read_text().replace(explanation, decimal))
        result = inspect_content(self.batch, {'items': [{'number': 81, 'explanation': decimal}]})
        self.assertTrue(result['passed'], result)

    def test_unprinted_writing_rejects_ambiguous_evidence(self):
        explanation = self.writing_fixture()
        body = self.batch / '01_source/正文.txt'
        original_body = body.read_text(); original_appendix = self.path.read_text()
        structure_path = self.batch / '01_source/structure.json'
        confirmed_path = self.batch / '01_source/structure.confirmed.json'
        original_structure = structure_path.read_text(); original_confirmed = confirmed_path.read_text()
        cases = [
            ('second body writing', lambda: body.write_text(original_body + 'Ⅺ.书面表达（共1小题）\n请写一篇作文。\n')),
            ('second task in same section', lambda: body.write_text(original_body + '请写一封英语信。\n')),
            ('two tasks on one line', lambda: body.write_text(original_body.replace(
                '请写一篇英语短文。', '请写一篇英语短文。另请写一封英语信。'))),
            ('two Chinese tasks same sentence', lambda: body.write_text(original_body.replace(
                '请写一篇英语短文。', '请写一篇英语短文，并写一封英语信。'))),
            ('two English tasks same sentence', lambda: body.write_text(original_body.replace(
                '请写一篇英语短文。', 'Write a letter and write an essay.'))),
            ('second appendix writing', lambda: self.path.write_text(original_appendix + 'Ⅺ.书面表达\n其他作文\n')),
            ('wrong section', lambda: self.path.write_text(original_appendix.replace('Ⅹ.书面表达', 'Ⅺ.书面表达'))),
            ('no end boundary', lambda: self.path.write_text(original_appendix.replace('参考范文', '其他内容'))),
            ('other hint', lambda: self.path.write_text(original_appendix + '[IMG a]思路点拨[IMG b]\n')),
            ('second unit', lambda: confirmed_path.write_text(json.dumps({'units': [
                {'type': '书面表达', 'unit_kind': 'standalone', 'numbers': [81], 'source_span': {'number_binding': 'single_writing_section'}},
                {'type': '书面表达', 'unit_kind': 'standalone', 'numbers': [82]}]}))),
            ('wrong number binding', lambda: confirmed_path.write_text(original_confirmed.replace('single_writing_section', 'guessed'))),
            ('number used elsewhere', lambda: confirmed_path.write_text(json.dumps({'units': [
                {'type': '书面表达', 'unit_kind': 'standalone', 'numbers': [81], 'source_span': {'number_binding': 'single_writing_section'}},
                {'type': '阅读理解', 'numbers': [81]}]}))),
        ]
        for label, mutate in cases:
            with self.subTest(label=label):
                body.write_text(original_body); self.path.write_text(original_appendix)
                structure_path.write_text(original_structure); confirmed_path.write_text(original_confirmed)
                mutate(); self.bind_writing_gate()
                result = inspect_content(self.batch, {'items': [{'number': 81, 'explanation': explanation}]})
                self.assertIn('EXPLANATION_SOURCE_UNVERIFIED', {e['code'] for e in result['warnings']})
        body.write_text(original_body); self.path.write_text(original_appendix)
        structure_path.write_text(original_structure); confirmed_path.write_text(original_confirmed)
        self.bind_writing_gate(); body.write_text(original_body + '修改')
        result = inspect_content(self.batch, {'items': [{'number': 81, 'explanation': explanation}]})
        self.assertIn('EXPLANATION_SOURCE_UNVERIFIED', {e['code'] for e in result['warnings']})

    def test_unprinted_writing_accepts_single_paragraph_hint(self):
        """单段【思路点拨】紧接【参考范文】是合法原卷写法，解析逐字取该段。"""
        body = ('Ⅸ.单词拼写\n80.题目\nⅩ.书面表达（共1小题；满分20分）\n'
                '请你用英文给他写封电子邮件。\n')
        paragraph = '本篇为电子邮件，人称用第一人称，时态以一般现在时为主，注意不要遗漏要点。'
        appendix = ('Ⅸ.单词拼写\n80.handed\nⅩ.书面表达\n'
                    f'　　【思路点拨】{paragraph}\n　　【参考范文】\n范文。\n')
        (self.batch / '01_source/正文.txt').write_text(body)
        self.path.write_text(appendix)
        structure = {'sections': [{'type': '书面表达', 'title': 'Ⅹ.书面表达（共1小题；满分20分）',
                                   'source_line': 3, 'questions': [81], 'declared_count': 1}]}
        confirmed = {'units': [{'type': '书面表达', 'unit_kind': 'standalone', 'numbers': [81],
                                'source_span': {'number_binding': 'single_writing_section'}}]}
        for name, data in [('structure.json', structure), ('structure.confirmed.json', confirmed)]:
            (self.batch / '01_source' / name).write_text(json.dumps(data, ensure_ascii=False))
        (self.batch / '07_review').mkdir(exist_ok=True)
        self.bind_writing_gate()
        report = inspect_content(self.batch, {'items': [{'number': 81, 'explanation': paragraph}]})
        self.assertTrue(report['passed'], report)
        for value in (paragraph + '范文。', '改写后的解析。'):
            with self.subTest(value=value):
                report = inspect_content(self.batch, {'items': [{'number': 81, 'explanation': value}]})
                self.assertIn('EXPLANATION_SOURCE_UNVERIFIED', {e['code'] for e in report['warnings']})
        # 第二处思路点拨提示仍视为归属不明。
        self.path.write_text(appendix + '【思路点拨】另一段。\n')
        self.bind_writing_gate()
        report = inspect_content(self.batch, {'items': [{'number': 81, 'explanation': paragraph}]})
        self.assertIn('EXPLANATION_SOURCE_UNVERIFIED', {e['code'] for e in report['warnings']})

    def test_writing_primary_hint_ignores_companion_attachment_duplicate(self):
        explanation = self.writing_fixture()
        materials = self.batch / '01_source/materials'
        materials.mkdir()
        (materials / 'attachment_001.txt').write_text(self.path.read_text())
        report = inspect_content(self.batch, {'items': [{'number': 81, 'explanation': explanation}]}, strict=True)
        self.assertTrue(report['passed'], report)
        # Repetition in the authority itself remains ambiguous, even with a PDF.
        self.path.write_text(self.path.read_text() + '【思路点拨】另一段。\n')
        report = inspect_content(self.batch, {'items': [{'number': 81, 'explanation': explanation}]}, strict=True)
        self.assertFalse(report['passed'])

    def test_writing_two_primary_sources_remain_ambiguous(self):
        explanation = self.writing_fixture()
        answer = self.batch / '01_source_ans/正文.txt'
        answer.parent.mkdir()
        answer.write_text('【思路点拨】另一个权威来源。\n')
        report = inspect_content(self.batch, {'items': [{'number': 81, 'explanation': explanation}]}, strict=True)
        self.assertFalse(report['passed'])

    def test_source_restoration_copies_word_characters_and_keeps_inspection_read_only(self):
        from workflow.upload_content import restore_source_content
        self.path.write_text('Ⅵ.完形填空\n31.B 【解析】A项:开始;B项:结束。\n')
        materials = self.batch / '01_source/materials'
        materials.mkdir()
        (materials / 'attachment_001.txt').write_text('Ⅵ.完形填空\n31.B 【解析】A项：开始；B项：结束。\n')
        payload = {'items': [{'number': 31, 'explanation': 'A项：开始；B项：结束。'}]}
        original = self.path.read_bytes()
        strict = inspect_content(self.batch, payload, strict=True)
        self.assertFalse(strict['passed'])
        self.assertEqual(payload['items'][0]['explanation'], 'A项：开始；B项：结束。')
        self.assertFalse(strict['warnings'])
        self.assertNotIn('不阻断', strict['errors'][0]['message'])
        report = restore_source_content(self.batch, payload)
        self.assertTrue(report['passed'], report)
        self.assertEqual(payload['items'][0]['explanation'], 'A项:开始;B项:结束。')
        self.assertEqual(report['restorations'][0]['path'], '01_source/附录.txt')
        self.assertEqual(self.path.read_bytes(), original)

    def test_source_restoration_refuses_substantive_edits_and_repeated_authority(self):
        from workflow.upload_content import restore_source_content
        for appendix, candidate in [
            ('Ⅵ.完形填空\n31.B 【解析】A项:开始。\n', 'A项：结束。'),
            ('Ⅵ.完形填空\n31.B 【解析】A项:开始。\n31.B 【解析】A项:开始。\n', 'A项：开始。'),
            ('Ⅵ.完形填空\n32.B 【解析】A项:开始。\n', 'A项：开始。'),
        ]:
            with self.subTest(appendix=appendix):
                self.path.write_text(appendix)
                payload = {'items': [{'number': 31, 'explanation': candidate}]}
                report = restore_source_content(self.batch, payload)
                self.assertFalse(report['passed'], report)
                self.assertEqual(report['restorations'], [])
                self.assertEqual(payload['items'][0]['explanation'], candidate)

    def group(self, **changes):
        item = {'unit_id': 'group-56', 'numbers': [56], 'articleSummary': '南极文章。',
                'explanation': '【文章大意】南极文章。',
                'subquestions': [{'number': 56, 'explanation': '南极气温。'}]}
        item.update(changes)
        return {'items': [item]}

    def test_broken_docx_style_markers_keep_summary_and_number_scope(self):
        text = ('**2023****年某省考试**\n[TABLE 3]\n'
                '**题号** | **31~35** | **56~60**\n**答案** | **ABCDE** | **DACDB**\n[/TABLE]\n'
                '**Ⅵ****.****完形填空**\n**A**\n'
                '**　　****【文章大意】****游泳文章。**\n**31****.****A　【解析】游泳原因。**\n'
                '**Ⅷ****.****阅读理解**\n**A**\n'
                '**　　****【文章大意】****南极****文章。**\n**56****.****D　【解析】南极气温。**\n'
                '**B**\n**【文章大意】舞狮文章。**\n**59****.****B　【解析】祈愿好运。**\n')
        self.path.write_text(text)
        before = self.path.read_bytes()
        report = inspect_content(self.batch, self.group(articleSummary='南极****文章。'))
        self.assertTrue(report['passed'], report)
        self.assertEqual(self.path.read_bytes(), before)
        report = inspect_content(self.batch, self.group(articleSummary='游泳文章。', explanation='【文章大意】游泳文章。'))
        self.assertIn('SUMMARY_SOURCE_UNVERIFIED', {w['code'] for w in report['warnings']})
        self.assertIn('EXPLANATION_CROSSES_MATERIAL', {e['code'] for e in report['errors']})
        report = inspect_content(self.batch, self.group(subquestions=[{'number': 56,
            'explanation': '**56****.****D　【解析】南极气温。**\n**B**\n**【文章大意】舞狮文章。**'}]))
        self.assertIn('EXPLANATION_CROSSES_MATERIAL', {e['code'] for e in report['errors']})

    def test_unowned_summary_cannot_borrow_next_material_or_section_number(self):
        for boundary in ('**B**', '**Ⅷ****.****阅读理解**', '[TABLE 4]'):
            self.path.write_text('**【文章大意】南极文章。**\n' + boundary + '\n**56****.****D　【解析】南极气温。**\n')
            with self.subTest(boundary=boundary):
                report = inspect_content(self.batch, self.group(explanation=''))
                self.assertIn('SUMMARY_SOURCE_UNVERIFIED', {e['code'] for e in report['warnings']})

    def test_multiline_summary_stops_at_first_formatted_real_question(self):
        (self.batch / '01_source/正文.txt').write_text('56.题目\n')
        self.path.write_text('**A**\n**【文章大意】南极**\n**文章。**\n**56****.****D　【解析】南极气温。**\n')
        self.assertTrue(inspect_content(self.batch, self.group())['passed'])
        report = inspect_content(self.batch, self.group(articleSummary='南极文章。56.D【解析】南极气温。'))
        self.assertIn('SUMMARY_SOURCE_UNVERIFIED', {e['code'] for e in report['warnings']})

    def test_real_icon_summary_and_question_markers_keep_ownership(self):
        self.path.write_text(
            'Ⅵ.完形填空\nA\n[IMG image12.jpeg]文章大意[IMG image13.jpeg]本文介绍了养宠物的好处。\n'
            '31.A [IMG image14.jpeg]解析[IMG image15.jpeg]宠物可以帮助活动身体。\n'
            'Ⅷ.阅读理解\nC\n[IMG image12.jpeg]文章大意[IMG image13.jpeg]本文介绍食物浪费。\n'
            '63.B [IMG image14.jpeg]解析[IMG image14.jpeg]指代食物清单。\n'
            'D\n[IMG image12.jpeg]文章大意[IMG image13.jpeg]本文介绍管理愤怒的方法。\n'
            '66.B [IMG image14.jpeg]解析[IMG image15.jpeg]寻找共同之处。\n')
        item = {'unit_id': 'c', 'numbers': [63], 'articleSummary': '本文介绍食物浪费。',
                'subquestions': [{'number': 63, 'explanation': '指代食物清单。'}]}
        self.assertTrue(inspect_content(self.batch, {'items': [item]})['passed'])
        item['articleSummary'] = '本文介绍了养宠物的好处。'
        item['subquestions'][0]['explanation'] += '\n本文介绍管理愤怒的方法。'
        report = inspect_content(self.batch, {'items': [item]})
        codes = {e['code'] for e in report['errors']} | {w['code'] for w in report['warnings']}
        self.assertIn('SUMMARY_SOURCE_UNVERIFIED', codes)
        self.assertIn('EXPLANATION_CROSSES_MATERIAL', codes)

    def test_icon_summary_cannot_cross_unrecognized_question_or_heading(self):
        for boundary in ('56.D 没有明确解析标记', '[IMG image12.jpeg]评分标准[IMG image13.jpeg]',
                         'B', 'Ⅷ.阅读理解', '[TABLE 4]',
                         '[IMG image12.jpeg]文章大意[IMG image13.jpeg]另一篇。'):
            with self.subTest(boundary=boundary):
                self.path.write_text('[IMG image12.jpeg]文章大意[IMG image13.jpeg]南极文章。\n'
                                     + boundary + '\n56.D [IMG image14.jpeg]解析[IMG image15.jpeg]南极气温。\n')
                report = inspect_content(self.batch, self.group())
                self.assertIn('SUMMARY_SOURCE_UNVERIFIED', {e['code'] for e in report['warnings']})

    def test_lookalike_icon_or_plain_text_is_not_a_source_summary(self):
        for marker in ('文章大意', '[IMG image12.jpeg]文章大意',
                       '[IMG image12.jpeg]文章大意说明[IMG image99.jpeg]',
                       '[IMG image99.jpeg]疑似文章大意[IMG image13.jpeg]',
                       '[IMG image12.jpeg]评分标准[IMG image13.jpeg]'):
            with self.subTest(marker=marker):
                self.path.write_text(marker + '南极文章。\n56.D [IMG image14.jpeg]解析[IMG image15.jpeg]南极气温。\n')
                report = inspect_content(self.batch, self.group())
                self.assertIn('SUMMARY_SOURCE_UNVERIFIED', {e['code'] for e in report['warnings']})

    def test_arbitrary_image_filenames_are_not_semantic_evidence(self):
        self.path.write_text('Ⅷ.阅读理解\nA\n[IMG figure-a.png]文章大意[IMG diagram.svg]南极文章。\n'
                             '56.D [IMG other.webp]解析[IMG image99.jpeg]南极气温。\n')
        self.assertTrue(inspect_content(self.batch, self.group())['passed'])

    def test_eight_2024_style_groups_with_body_anchors(self):
        rows = [
            (31, '本文是一篇说明文。文章主要介绍了养宠物的好处。', '养宠物可以帮助你活动身体。'),
            (41, '本文是一篇记叙文。文章主要讲述了作者的头痛在中医的治疗下恢复。', '这是介绍作者的经历。'),
            (56, '本文是一篇应用文,介绍了家庭水上安全教育、水上安全计划两个项目。', '庆典在星期天举行。'),
            (59, '本文是一篇记叙文,主要讲述了一位校车司机在业余时间为学生们编织帽子的故事。', '决定开始编织更多帽子。'),
            (63, '本文是一篇说明文,主要介绍了一项关于计量食物浪费的研究以及研究所带来的影响。', 'it指代上文中提到的“the list”。'),
            (66, '本文是一篇说明文,主要介绍了管理愤怒情绪的方法。', '寻找共同之处。'),
            (70, '本文是一篇记叙文,通过介绍Dennis Hong的团队设计人形机器人的研发过程,告诉我们要勇于尝试,坚持学习。', '最新发明的机器人。'),
            (73, '本文是一篇记叙文,讲述了作者一家的家庭聚餐传统,这代表了家人之间的关爱。', '每周至少一次或每个星期天进行家庭聚餐。'),
        ]
        self.path.write_text(''.join(f'A\n[IMG image12.jpeg]文章大意[IMG image13.jpeg]{summary}\n'
                                     f'{number}.A [IMG image14.jpeg]解析[IMG image15.jpeg]{explanation}\n'
                                     for number, summary, explanation in rows))
        (self.batch / '01_source/正文.txt').write_text(''.join(f'（　）{number}.题目\n' for number, _, _ in rows))
        items = [{'unit_id': str(number), 'numbers': [number], 'articleSummary': summary,
                  'subquestions': [{'number': number, 'explanation': explanation}]}
                 for number, summary, explanation in rows]
        report = inspect_content(self.batch, {'items': items})
        self.assertTrue(report['passed'], report)

    def test_explanation_cannot_borrow_another_question(self):
        self.path.write_text('1.A 【解析】第一题解析。\n2.B 【解析】第二题解析。\n')
        payload = {'items': [{'number': 1, 'explanation': '第二题解析。'},
                             {'number': 2, 'explanation': '第二题解析。'}]}
        warnings = inspect_content(self.batch, payload)['warnings']
        self.assertEqual([(e['unit_id'], e['code']) for e in warnings],
                         [('0', 'EXPLANATION_SOURCE_UNVERIFIED')])

    def test_repeated_numbers_fail_closed_without_affecting_unique_numbers(self):
        (self.batch / '01_source/正文.txt').write_text('Ⅰ.第一节\n1.题目\nⅡ.第二节\n2.题目\n')
        self.path.write_text('Ⅰ.第一节\nA\n【文章大意】第一篇。\n1.A 【解析】第一题。\n'
                             'Ⅱ.第二节\nA\n【文章大意】第二篇。\n1.B 【解析】第二题。\n'
                             '2.C 【解析】独立题。\n')
        payload = {'items': [{'unit_id': 'duplicate', 'number': 1, 'articleSummary': '第一篇。',
                              'explanation': '第一题。'},
                             {'unit_id': 'unique', 'number': 2, 'explanation': '独立题。'}]}
        warnings = inspect_content(self.batch, payload)['warnings']
        self.assertEqual({e['code'] for e in warnings}, {'SUMMARY_SOURCE_UNVERIFIED', 'EXPLANATION_SOURCE_UNVERIFIED'})
        self.assertEqual({e['unit_id'] for e in warnings}, {'duplicate'})

    def test_body_number_must_be_unique_when_body_exists(self):
        (self.batch / '01_source/正文.txt').write_text('Ⅰ.第一节\n56.题目一\nⅡ.第二节\n56.题目二\n')
        warnings = inspect_content(self.batch, self.group())['warnings']
        self.assertIn('SUMMARY_SOURCE_UNVERIFIED', {e['code'] for e in warnings})
        self.assertIn('EXPLANATION_SOURCE_UNVERIFIED', {e['code'] for e in warnings})

    def test_missing_body_and_conflicting_section_require_review(self):
        body = self.batch / '01_source/正文.txt'
        body.unlink()
        self.assertIn('SUMMARY_SOURCE_UNVERIFIED',
                      {e['code'] for e in inspect_content(self.batch, self.group())['warnings']})
        body.write_text('Ⅵ.完形填空\n56.题目\n')
        self.assertIn('EXPLANATION_SOURCE_UNVERIFIED',
                      {e['code'] for e in inspect_content(self.batch, self.group())['warnings']})

    def test_parenthesized_question_boundaries_and_duplicates(self):
        self.path.write_text('1.A 【解析】第一题。\n（ ）2.B 【解析】第二题。\n')
        result = inspect_content(self.batch, {'items': [{'number': 1, 'explanation': '第二题。'},
                                                        {'number': 2, 'explanation': '第二题。'}]})
        self.assertEqual([(e['unit_id'], e['code']) for e in result['warnings']],
                         [('0', 'EXPLANATION_SOURCE_UNVERIFIED')])
        self.path.write_text('1.A 【解析】第一题。\n（ ）1.B 【解析】重复题。\n')
        result = inspect_content(self.batch, {'items': [{'number': 1, 'explanation': '第一题。'}]})
        self.assertIn('EXPLANATION_SOURCE_UNVERIFIED', {e['code'] for e in result['warnings']})

    def test_missing_or_conflicting_section_evidence(self):
        body = self.batch / '01_source/正文.txt'
        for body_text, appendix in [
            ('Ⅵ.完形填空\n1.题一\n', '阅读理解\n1.A 【解析】错误章节。\n'),
            ('Ⅵ.完形填空\n1.题一\n', '1.A 【解析】错误章节。\n'),
            ('1.题一\n', '阅读理解\n1.A 【解析】错误章节。\n'),
            ('Ⅰ.阅读理解\n1.题一\n', 'Ⅰ.完形填空\n1.A 【解析】错误章节。\n'),
        ]:
            with self.subTest(body=body_text, appendix=appendix):
                body.write_text(body_text); self.path.write_text(appendix)
                result = inspect_content(self.batch, {'items': [{'number': 1, 'explanation': '错误章节。'}]})
                self.assertIn('EXPLANATION_SOURCE_UNVERIFIED', {e['code'] for e in result['warnings']})

    def test_passage_heading_and_unowned_group_summary(self):
        self.path.write_text('A\n【文章大意】第一篇。\n1.A 【解析】一。\nB 阅读第二篇\n2.B 【解析】二。\n')
        items = [{'numbers': [1, 2], 'articleSummary': '第一篇。',
                  'subquestions': [{'number': 1, 'explanation': '一。'}, {'number': 2, 'explanation': '二。'}]}]
        self.assertIn('SUMMARY_SOURCE_UNVERIFIED', {e['code'] for e in inspect_content(self.batch, {'items': items})['warnings']})
        self.path.write_text('【文章大意】无归属的摘要。\nB\n1.A 【解析】一。\n')
        items = [{'explanation': '【文章大意】无归属的摘要。', 'subquestions': [{'number': 1, 'explanation': '一。'}]}]
        self.assertIn('EXPLANATION_SOURCE_UNVERIFIED', {e['code'] for e in inspect_content(self.batch, {'items': items})['warnings']})

    def test_empty_normalized_explanation_is_not_source_text(self):
        self.path.write_text('1.A 【解析】第一题。\n')
        for value in ('**', '<b></b>'):
            with self.subTest(value=value):
                result = inspect_content(self.batch, {'items': [{'number': 1, 'explanation': value}]})
                self.assertIn('EXPLANATION_SOURCE_UNVERIFIED', {e['code'] for e in result['warnings']})

    def test_explicit_passage_headings_and_english_continuation(self):
        for heading in ('B：第二篇', 'B篇', 'Passage B', '（B）', '(B)', '第二篇'):
            with self.subTest(heading=heading):
                self.path.write_text('A\n【文章大意】第一篇。\n1.A 【解析】一。\n' + heading + '\n2.B 【解析】二。\n')
                items = [{'articleSummary': '第一篇。', 'subquestions': [
                    {'number': 1, 'explanation': '一。'}, {'number': 2, 'explanation': '二。'}]}]
                self.assertIn('SUMMARY_SOURCE_UNVERIFIED',
                              {e['code'] for e in inspect_content(self.batch, {'items': items})['warnings']})
        self.path.write_text('A\n【文章大意】第一篇。\n1.A 【解析】首句。\nA good reason is clear.\n')
        items = [{'number': 1, 'explanation': '首句。\nA good reason is clear.'}]
        self.assertTrue(inspect_content(self.batch, {'items': items})['passed'])

    def test_multiple_numbers_on_one_line_do_not_assign_explanation(self):
        for line in ('1.A 2.B 【解析】第二题。', '1.A2.B 【解析】第二题。',
                     '1.A 【解析】第一题。 2.B 【解析】第二题。',
                     '1.A 【解析】第一题。 1.B 【解析】第二题。'):
            with self.subTest(line=line):
                self.path.write_text(line + '\n')
                for explanation in ('第一题。', '第二题。'):
                    result = inspect_content(self.batch, {'items': [{'number': 1, 'explanation': explanation}]})
                    self.assertIn('EXPLANATION_SOURCE_UNVERIFIED', {e['code'] for e in result['warnings']})
        self.path.write_text('1.A 【解析】一。 2.B 【解析】二。\n2.B 【解析】另一个二。\n')
        result = inspect_content(self.batch, {'items': [{'number': 2, 'explanation': '另一个二。'}]})
        self.assertIn('EXPLANATION_SOURCE_UNVERIFIED', {e['code'] for e in result['warnings']})

    def test_decimal_numbers_do_not_create_question_anchors(self):
        (self.batch / '01_source/正文.txt').write_text('1.价格是2.30元\n2.第二题\n')
        self.path.write_text('1.A 【解析】圆周率约3.14。\n2.B 【解析】第二题解析。\n')
        items = [{'number': 1, 'explanation': '圆周率约3.14。'},
                 {'number': 2, 'explanation': '第二题解析。'}]
        report = inspect_content(self.batch, {'items': items})
        self.assertTrue(report['passed'], report)

    def test_ai_marker_requires_actual_normalized_content(self):
        for value in ('**（AI生成，待校对）', '<b></b>（AI生成，待校对）'):
            with self.subTest(value=value):
                result = inspect_content(self.batch, {'items': [{'number': 1, 'explanation': value,
                                                                  'articleSummary': value}]})
                self.assertEqual([e['field'] for e in result['warnings'] if e['code'] == 'AI_PROVENANCE_IGNORED'],
                                 ['articleSummary', 'explanation'])

    def test_icon_source_does_not_validate_fake_ai_markers(self):
        self.path.write_text('[IMG image12.jpeg]文章大意[IMG image13.jpeg]南极文章。\n'
                             '56.D [IMG image14.jpeg]解析[IMG image15.jpeg]南极气温。\n')
        for fake in ('南极文章。（AI生成，待校对）额外内容', '（AI生成，待校对）'):
            with self.subTest(fake=fake):
                report = inspect_content(self.batch, self.group(articleSummary=fake))
                self.assertIn('AI_PROVENANCE_IGNORED', {e['code'] for e in report['warnings']})

    def test_correct_source_summary_and_explanation_pass(self):
        report = inspect_content(self.batch, self.group())
        self.assertTrue(report['passed'], report)
        self.assertEqual(report['source_sha256']['01_source/附录.txt'], hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_repeated_A_heading_does_not_allow_cloze_summary_on_reading(self):
        report = inspect_content(self.batch, self.group(articleSummary='游泳文章。', explanation='【文章大意】游泳文章。'))
        self.assertIn('SUMMARY_SOURCE_UNVERIFIED', {w['code'] for w in report['warnings']})
        self.assertIn('EXPLANATION_CROSSES_MATERIAL', {e['code'] for e in report['errors']})

    def test_last_question_cannot_swallow_next_material_summary(self):
        report = inspect_content(self.batch, self.group(subquestions=[{
            'number': 56, 'explanation': '南极气温。\nB\n【文章大意】舞狮文章。'}]))
        self.assertFalse(report['passed'])
        finding = next(e for e in report['errors'] if e['code'] == 'EXPLANATION_CROSSES_MATERIAL')
        self.assertEqual(finding['unit_id'], 'group-56/56')
        self.assertIn('题 59', finding['message'])

    def test_summary_is_source_provided_even_when_unmarked_ai_text(self):
        report = inspect_content(self.batch, self.group(articleSummary='自编的一篇新文章大意。'))
        self.assertIn('SUMMARY_SOURCE_UNVERIFIED', {e['code'] for e in report['warnings']})

    def test_absent_explanation_is_allowed_and_unsourced_generation_rejected(self):
        # 用户规则：非听力单元缺解析填占位（待补充）或留空都放行，不再要求补写。
        for value in ('', '待补充', '暂无', '无'):
            report = inspect_content(self.batch, {'items': [{'unit_id': 'q-1', 'number': 1, 'explanation': value}]})
            self.assertTrue(report['passed'], report)
        # 未标注、无法溯源的生成内容仍拦停。
        report = inspect_content(self.batch, {'items': [{'unit_id': 'q-1', 'number': 1, 'explanation': '原资料未提供逐题解析'}]})
        self.assertIn('EXPLANATION_SOURCE_UNVERIFIED', {e['code'] for e in report['warnings']})
        # 明确标注 AI 生成的解析放行（F2/F5）。
        report = inspect_content(self.batch, {'items': [{'unit_id': 'q-1', 'number': 1, 'explanation': '本题考查名词辨析（AI生成，待校对）'}]})
        self.assertTrue(report['passed'], report)

    def test_listening_unit_may_omit_explanation(self):
        report = inspect_content(self.batch, {'items': [{'unit_id': 'l-1', 'question_type': '听力短对话理解',
                                                         'number': 1, 'explanation': ''}]})
        self.assertTrue(report['passed'], report)

    def test_province_is_not_a_within_source_locator(self):
        import json
        body = self.batch / '01_source/正文.txt'; body.write_text('2022年安徽省试卷')
        (self.batch / '01_source/metadata.json').write_text(json.dumps({
            'region': '安徽省', 'source_sha256': hashlib.sha256(body.read_bytes()).hexdigest()}))
        data = self.group(); data['items'][0]['filterMetadata'] = {'location': '安徽省'}
        result = inspect_content(self.batch, data)
        self.assertIn('SOURCE_LOCATION_IS_REGION', {e['code'] for e in result['errors']})
        self.assertIn('01_source/正文.txt', result['source_sha256'])

    def test_formatting_and_whitespace_do_not_make_source_untraceable(self):
        report = inspect_content(self.batch, self.group(articleSummary='**南极文章。**', explanation='【文章大意】\n 南极文章。'))
        self.assertTrue(report['passed'], report)
