"""复合提取路由与证据保留，不依赖 GPU 的回归。"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import auto, contracts, extract, manifest as mf, ocr_backend, runner


class MaterialRoleAgentTest(unittest.TestCase):
    def test_role_classification_uses_local_agent_envelope(self):
        from workflow import local_agent, material_classify
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / 'agent.py'
            result = {'status': 'completed', 'artifacts': {'classification': {'role': 'answers'}},
                      'notes': [], 'findings': []}
            script.write_text('import sys\nsys.stdin.read()\nprint(' + repr(json.dumps(result)) + ')\n', encoding='utf-8')
            config = local_agent._validate_config({'provider': 'command', 'command': ['{python}', str(script)],
                                                   'timeout_seconds': 10}, root)
            def invoke(prompt, run_dir, cfg):
                return local_agent.invoke(prompt, run_dir, cfg)['response']
            role, source = material_classify._classify_one('1. A\n2. B', invoke, config)
            self.assertEqual((role, source), ('answers', 'model'))


class PdfOcrDiagnosticsTest(unittest.TestCase):
    def test_failed_pdf_aliases_are_summarized_once_with_safe_details(self):
        error = {"code": "http_error", "message": "private-config",
                 "details": {"http_status": 429, "error_code": "QuotaExceeded",
                             "error_message": "quota exhausted", "token": "private-token"}}
        sources = [{"input": "source", "path": "paper.pdf", "sha256": "same",
                    "status": "failed", "error": error},
                   {"input": "alias", "path": "copy.pdf", "sha256": "same",
                    "duplicate_of": "source", "status": "failed", "error": error},
                   {"input": "answers", "path": "answers.pdf", "sha256": "other",
                    "status": "failed", "error": {"code": "timeout"}}]
        message = extract._pdf_ocr_failure({"sources": sources})
        self.assertEqual(message.count("[http_error]"), 1)
        self.assertEqual(message.count("[timeout]"), 1)
        self.assertIn("http_status=429", message)
        self.assertIn("error_code=QuotaExceeded", message)
        self.assertIn("quota exhausted", message)
        self.assertNotIn("private", message)

    def test_fixed_local_failures_remain_specific_in_summary(self):
        for message, code in ocr_backend.SAFE_VALUE_ERRORS.items():
            with self.subTest(code=code):
                report = {"sources": [{"status": "failed", "path": "paper.pdf",
                                       "error": {"type": "ValueError", "message": message}}]}
                summary = extract._pdf_ocr_failure(report)
                self.assertIn(message, summary)
                self.assertIn(f"[{code}]", summary)
                self.assertEqual(ocr_backend.safe_error(ValueError(message))["code"], code)

    def test_source_failure_and_invalid_result_do_not_leak_exception_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            source = batch / "paper.pdf"
            source.write_bytes(b"synthetic PDF")
            item = {"path": str(source), "status": "failed", "error": {
                "message": "token=private https://private.invalid/signed", "code": "unknown-private"}}
            result, message = extract._pdf_ocr_for_source(batch, source, {"sources": [item]})
            self.assertIsNone(result)
            self.assertIn("[ocr_failed]", message)
            self.assertNotIn("private", message)
            item.update(status="completed", sha256=mf.sha256_file(source),
                        result_path="secret-token-result.json")
            _, message = extract._pdf_ocr_for_source(batch, source, {"sources": [item]})
            self.assertEqual(message, "PDF OCR 结果不可用：来源或页面证据无效")
            self.assertNotIn("secret-token", message)

    def test_run_reports_source_ocr_failure_once_without_extract_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            source = batch / "paper.pdf"
            source.write_bytes(b"synthetic PDF")
            report = {"status": "failed", "sources": [{"input": "source", "path": str(source),
                "sha256": mf.sha256_file(source), "status": "failed",
                "error": {"code": "missing_token", "message": "private config"}}]}
            with patch.object(mf, "load", return_value={"inputs": {"source": {"path": str(source)}}}), \
                 patch("workflow.pdf_ocr.run", return_value=report), \
                 patch.object(extract, "extract_source", side_effect=RuntimeError("private token")):
                with self.assertRaises(ValueError) as raised:
                    extract.run(batch)
            message = str(raised.exception)
            self.assertEqual(message.count("PDF OCR 获取失败"), 1)
            self.assertEqual(message.count("[missing_token]"), 1)
            self.assertNotIn("private", message)

    def test_unexpected_acquisition_failure_is_generic(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            source = batch / "paper.pdf"
            source.write_bytes(b"synthetic PDF")
            with patch.object(mf, "load", return_value={"inputs": {"source": {"path": str(source)}}}), \
                 patch("workflow.pdf_ocr.run", side_effect=RuntimeError("secret token https://private.invalid")):
                with self.assertRaises(ValueError) as raised:
                    extract.run(batch)
            self.assertNotIn("secret", str(raised.exception))
            self.assertNotIn("private", str(raised.exception))


class StructureCandidateTest(unittest.TestCase):
    def test_writing_bullets_not_question_numbers_and_appendix_is_separate(self):
        body = ('Ⅰ.单项填空(共2小题)\n1. First?\n2. Second?\n'
                'Ⅱ.书面表达(共1小题)\n1. 内容要点\n2. 内容要点\n'
                'Ⅰ.单项填空\n1. A 2. B\n')
        structure, main, appendix = extract.infer_structure(body)
        self.assertEqual(structure['sections'][0]['questions'], [1, 2])
        self.assertEqual(structure['sections'][1]['questions'], [3])
        self.assertTrue(structure['sections'][1]['numbers_inferred'])
        self.assertIn('1. A 2. B', appendix)
        self.assertNotIn('1. A 2. B', main)
        self.assertFalse(structure['coverage_verified'])

    def test_inline_answer_block_before_later_section_keeps_full_paper(self):
        # 解析版：听力答案插在听力题之后、笔试之前；不能把整段笔试当答案册切掉。
        text = ('2023年某省初中学业水平考试\n'
                '第一部分 听力\n'
                'Ⅰ.关键词语选择(共2小题)\n1. A\n2. B\n'
                'Ⅱ.短对话理解(共2小题)\n3. C\n4. D\n'
                'Ⅰ.关键词语选择\n1~2 AB\n'
                'Ⅱ.短对话理解\n3~4 CD\n'
                '第二部分 英语知识运用\n'
                'Ⅵ.单项填空(共2小题)\n'
                '31. It is ___ for me.\nA.bad B.easy C.hard D.right\n'
                '32. He is ___ .\nA.tall B.short C.thin D.fat\n')
        structure, main, appendix = extract.infer_structure(text)
        self.assertIn('Ⅵ.单项填空', main)
        self.assertIn('31. It is', main)
        self.assertNotIn('1~2 AB', main)
        self.assertIn('1~2 AB', appendix)
        self.assertIn('3~4 CD', appendix)
        self.assertEqual([s['type'] for s in structure['sections']],
                         ['听力关键词语选择', '听力短对话理解', '单项填空'])
        self.assertEqual({n for s in structure['sections'] for n in s['questions']},
                         {1, 2, 3, 4, 31, 32})
        self.assertIn('INLINE_ANSWER_BLOCK', [u['code'] for u in structure['unresolved']])

    def test_listening_script_uses_leadin_ranges_and_chinese_count(self):
        text = ('Ⅰ.短对话理解\n1. A\n2. B\n3. C\n4. A\n5. B\n'
                'Ⅱ.长对话理解\n听下面一段对话，回答第6~7题。\n'
                '对话内容\n听下面一段对话，回答第8~10题。\n对话内容\n'
                'Ⅲ.短文理解\n短文后有五个小题。\n短文内容\n'
                'Ⅳ.信息转换\n请填写表格。\n短文内容\n')
        structure, _, _ = extract.infer_structure(text)
        self.assertEqual(structure['sections'][1]['questions'], [6, 7, 8, 9, 10])
        self.assertEqual(structure['sections'][2]['questions'], [11, 12, 13, 14, 15])
        self.assertEqual(structure['sections'][3]['questions'], [16, 17, 18, 19, 20])
        self.assertFalse(any(x['code'] == 'QUESTION_BOUNDARY_MISSING' for x in structure['unresolved']))

    def test_answer_line_numbers_are_not_questions(self):
        # 解析版：答案紧跟在题目下一行（91.题目 / 91.nature），或长答案带【解析】。
        text = ('Ⅹ.单词拼写(共2小题)\n'
                '91.Mary has developed a new way of life by getting close to n(自然).\n'
                '91.nature\n'
                '92.Frank,take off your w(湿的)jacket.\n'
                '92.wet\n'
                'Ⅸ.阅读理解(共1小题)\n'
                '88.What advantages does rock climbing have?\n'
                '88.It is fun and it also helps people forget worries.　【解析】根据第一段。\n')
        structure, main, appendix = extract.infer_structure(text)
        self.assertIn('91.Mary has developed', main)
        self.assertNotIn('91.nature', main)
        self.assertNotIn('88.It is fun', main)
        self.assertIn('91.nature', appendix)
        self.assertIn('88.It is fun', appendix)
        self.assertEqual(structure['sections'][0]['questions'], [91, 92])
        self.assertEqual(structure['sections'][1]['questions'], [88])
        self.assertNotIn('DUPLICATE_QUESTION_NUMBER',
                         [u['code'] for u in structure['unresolved']])

    def test_numbered_letter_answers_after_options_are_moved(self):
        text = ('Ⅰ.单项填空(共2小题)\n'
                '1. What do you want?\nA.Yes.\tB.No.\n'
                '2. Where are you?\nA.Home.\tB.School.\n'
                '1.A\n2.B\n')
        structure, main, appendix = extract.infer_structure(text)
        self.assertNotIn('1.A', main)
        self.assertNotIn('2.B', main)
        self.assertIn('1.A', appendix)
        self.assertIn('2.B', appendix)
        self.assertEqual(structure['sections'][0]['questions'], [1, 2])
        self.assertNotIn('DUPLICATE_QUESTION_NUMBER',
                         [u['code'] for u in structure['unresolved']])

    def test_option_line_with_sentence_numbers_is_kept(self):
        # “A.Between 8 and 12. B.Between 11 and 15.” 里的 12. 15. 不能被当成题号删除。
        text = ('Ⅳ.短文理解(共3小题)\n'
                '(　　)21.Where is Paul\'s school?\n'
                'A.In America.\tB.In Canada.\tC.In China.\n'
                '(　　)22.How old are the students in Paul\'s school?\n'
                'A.Between 8 and 12.\tB.Between 11 and 15.\tC.Between 12 and 15.\n'
                '(　　)23.Where does Paul begin his day?\n'
                'A.In the radio station.\tB.In the dining hall.\tC.In his “home class”.\n')
        structure, main, appendix = extract.infer_structure(text)
        self.assertIn('Between 8 and 12', main)
        self.assertIn('Between 12 and 15', main)
        self.assertEqual(structure['sections'][0]['questions'], [21, 22, 23])
        self.assertNotIn('A.Between 8 and 12', appendix)

    def test_skip_lines_removes_confirmed_answer_lines(self):
        text = ('Ⅹ.单词拼写(共2小题)\n'
                '91.Question one.\n91.answerone\n'
                '92.Question two.\n92.answertwo\n')
        structure, main, appendix = extract.infer_structure(text, skip_lines={4})
        self.assertIn('91.answerone', appendix)
        self.assertIn('92.answertwo', main)
        self.assertNotIn('91.answerone', main)

    def test_trailing_answer_heading_block_is_separated(self):
        text = ('2026年某省中考英语\n'
                'Ⅰ.单项填空(共2小题;每小题1分,满分2分)\n'
                '1. What do you want?\nA.Yes.\tB.No.\n'
                '2. Where are you?\nA.Home.\tB.School.\n'
                'Ⅱ.单词拼写(共2小题;每小题1分,满分2分)\n'
                '3.Mary has a n　　　　(自然) life.\n'
                '4.Frank has a w　　　　(湿的) jacket.\n'
                '参考答案及解析\n'
                'Ⅰ.单项填空\n1.A　【解析】根据答语。\n2.B　【解析】根据问句。\n'
                'Ⅱ.单词拼写\n3.nature\n4.wet\n')
        structure, main, appendix = extract.infer_structure(text)
        self.assertTrue(main.rstrip().endswith('jacket.'))
        self.assertTrue(appendix.lstrip().startswith('参考答案及解析'))
        self.assertIn('3.nature', appendix)
        self.assertNotIn('3.nature', main)
        self.assertEqual([s['questions'] for s in structure['sections']], [[1, 2], [3, 4]])
        self.assertNotIn('DUPLICATE_QUESTION_NUMBER',
                         [u['code'] for u in structure['unresolved']])

    def test_repeated_paper_title_requires_adjacent_valid_answer_summary(self):
        title = '2023年某省初中学业水平考试'
        prefix = title + '\nⅠ.单项填空(共1小题)\n80. Choose?\nA.Yes B.No\nⅩ.书面表达(共1小题)\nWrite about your town.\n'
        summary = '[TABLE 3]\n**题号** | **1~5** | **6~10**\n**答案** | **CCAAB** | **CABBC**\n[/TABLE]\n'
        for suffix in ('', 'Ⅰ.单项填空\n1. C\n【解析】Example.\n'):
            with self.subTest(suffix=suffix):
                tail = '**2023****年某省初中学业水平考试**\n\n' + summary + suffix
                structure, main, appendix = extract.infer_structure(prefix + tail)
                self.assertEqual(main, prefix)
                self.assertEqual(appendix, tail.strip())
                self.assertEqual(structure['sections'][-1]['source_end_line'], len(prefix.splitlines()))
                self.assertEqual(structure['sections'][-1]['questions'], [81])
                issue = next(x for x in structure['unresolved'] if x['code'] == 'ANSWER_APPENDIX_DETECTED')
                self.assertEqual(issue['line'], len(prefix.splitlines()) + 1)
                self.assertNotIn('resolved', issue)
                self.assertFalse(structure['coverage_verified'])

    def test_title_or_answer_like_table_alone_does_not_cut_real_body(self):
        title = '2023年某省初中学业水平考试'
        header = title + '\nⅠ.阅读理解(共1小题)\nA\nRead the table.\n'
        summary = '[TABLE 3]\n题号 | 1~5 | 6~10\n答案 | CCAAB | CABBC\n[/TABLE]\n'
        cases = {
            'ordinary_body_table': summary,
            'repeated_page_header': title + '\n[TABLE 2]\nName | Grade | Age\nAnn | A | 15\n[/TABLE]\n',
            'writing_table': title + '\nⅩ.书面表达(共1小题)\n' + summary,
            'title_mentioned_in_prose': '阅读“' + title + '”并回答。\n' + summary,
            'different_paper_title': '2024年某省初中学业水平考试\n' + summary,
            'nonadjacent_answers': title + '\nContinue reading the article.\n' + summary,
            'wrong_answer_counts': title + '\n' + summary.replace('CCAAB', 'CCA'),
            'not_answer_letters': title + '\n' + summary.replace('CCAAB', 'Words'),
        }
        for name, content in cases.items():
            with self.subTest(name=name):
                text = header + content + '56. Question?\nA.One B.Two C.Three D.Four\n'
                structure, main, appendix = extract.infer_structure(text)
                self.assertEqual(main, text)
                self.assertEqual(appendix, '')
                self.assertNotIn('ANSWER_APPENDIX_DETECTED', {x['code'] for x in structure['unresolved']})

    def test_trailing_repeated_paper_title_with_list_prefix_is_removed(self):
        # 2017 安徽卷文末重复卷名带 **1** 前缀，不能留在最后一题的正文里。
        body = ('**1**.**2023**年安徽省初中学业水平考试\n'
                'Ⅰ.关键词语选择(共1小题)\n'
                '(  )1.A.one B.two\n'
                'Ⅱ.书面表达(共1小题)\n'
                'Write about your town.\n'
                '**1**.**2023**年安徽省初中学业水平考试\n'
                'Ⅰ.关键词语选择\n'
                '1. A\n')
        structure, main, appendix = extract.infer_structure(body)
        self.assertEqual(main.splitlines()[-1], 'Write about your town.')
        self.assertTrue(appendix.startswith('Ⅰ.关键词语选择'))
        self.assertEqual(structure['sections'][-1]['questions'], [2])

    def test_reading_material_headings_split_groups(self):
        structure, _, _ = extract.infer_structure(
            'Ⅷ.阅读理解(共4小题)\nA\nMaterial A\n56. Q\n57. Q\nB\nMaterial B\n58. Q\n59. Q\n')
        self.assertEqual([g['questions'] for g in structure['groups']], [[56, 57], [58, 59]])
        self.assertEqual(structure['unresolved'], [])

    def test_cloze_blanks_and_option_rows_are_same_questions(self):
        structure, _, _ = extract.infer_structure(
            'Ⅰ.完形填空(共2小题)\nA\nText <u>　31　</u> and <u>　32　 </u>\n31.A. first\n32.A. next\n')
        self.assertEqual(structure['sections'][0]['questions'], [31, 32])
        self.assertNotIn('DUPLICATE_QUESTION_NUMBER', {x['code'] for x in structure['unresolved']})
        dialog, _, _ = extract.infer_structure('Ⅰ.补全对话(共1小题)\nAnn:<u>　51　 </u>')
        self.assertEqual(dialog['sections'][0]['questions'], [51])

    def test_dialogue_blank_padded_with_nbsp_is_still_a_number(self):
        # 2017 安徽卷的 docx 把空位写成 <u>　66\xa0</u>（只有前导全角空格）。
        structure, _, _ = extract.infer_structure(
            'Ⅰ.补全对话(共3小题)\n'
            'Bruce:<u>\u300066\xa0</u>\n'
            'Bruce:<u>\u300067\u3000</u>\n'
            'Bruce:<u>\u300068\xa0</u>\n')
        self.assertEqual(structure['sections'][0]['questions'], [66, 67, 68])
        self.assertNotIn('QUESTION_COUNT_MISMATCH', {x['code'] for x in structure['unresolved']})

    def test_same_line_pairs_are_all_numbered(self):
        """一行两个小题（字母书写、判断大小写）不能被当成只有一个题号。"""
        structure, _, _ = extract.infer_structure(
            '2026年某同步练习\n'
            'Ⅰ.语音题(共4小题)\n'
            '1.U　v (　　　)　　　　2.W　x (　　　)\n'
            '3.F　f (　　　)　　　　4.C　g (　　　)\n')
        self.assertEqual(structure['sections'][0]['questions'], [1, 2, 3, 4])
        self.assertNotIn('QUESTION_COUNT_MISMATCH', {x['code'] for x in structure['unresolved']})
        self.assertEqual(extract.same_line_numbers('1.U　v (　　　)　　　　2.W　x (　　　)', 1), [2])

    def test_same_line_number_needs_a_real_parenthesised_slot(self):
        """正文里随手出现的「2.」不是第二个小题；必须有成组空档才算。"""
        self.assertEqual(extract.same_line_numbers('1.U v and 2.W x', 1), [])
        # 缺空档的「( )」也不算，避免把括号里的注释当答题位
        self.assertEqual(extract.same_line_numbers('1.U v  2.W x', 1), [])

    def test_dialogue_slot_prefixed_by_speaker_is_numbered(self):
        """同步课时补全对话的小题号常写成 `C:1.<u>`，必须认得出题号。"""
        structure, _, _ = extract.infer_structure(
            '2026年某同步练习\n'
            'Ⅰ.补全对话(共2小题)\n'
            'A:Hi,Ann.\n'
            'B:1.<u>　　　</u>\n'
            'A:2.<u>　　　</u>\n')
        self.assertEqual(structure['sections'][0]['questions'], [1, 2])
        self.assertNotIn('QUESTION_BOUNDARY_MISSING', {x['code'] for x in structure['unresolved']})

    def test_speaker_question_extension_does_not_apply_to_non_sync_material(self):
        structure, _, _ = extract.infer_structure(
            '2026年某期末试卷\n'
            'Ⅰ.单项填空(共1小题)\n'
            '1. Read the label A:2. and choose a reply.\n'
            'B:3.<u>　　　</u>\n')
        self.assertEqual(structure['sections'][0]['questions'], [1])

    def test_image_only_section_is_informational_not_a_boundary_error(self):
        """小题只在图片里（字母书写）时按图片能力限制记录，不报题号缺失。"""
        structure, _, _ = extract.infer_structure(
            '2026年某同步练习\n'
            'Ⅰ.语音题(共1小题)\n'
            '[IMG image2.jpeg]\n'
            'Ⅱ.单项填空(共1小题)\n'
            '1.Hello!\nA.Hi. B.Bye.\n')
        first = structure['sections'][0]
        self.assertTrue(first.get('image_only'))
        self.assertEqual(structure['sections'][1]['questions'], [1])
        codes = {x['code'] for x in structure['unresolved']}
        self.assertIn('IMAGE_SECTION_UNNUMBERED', codes)
        self.assertNotIn('QUESTION_BOUNDARY_MISSING', codes)

    def test_ambiguous_sections_never_claim_coverage(self):
        structure, _, _ = extract.infer_structure('Ⅰ.完形填空(共5小题)\n1. one\n2. two\n')
        codes = {x['code'] for x in structure['unresolved']}
        self.assertIn('QUESTION_COUNT_MISMATCH', codes)
        self.assertIn('GROUP_BOUNDARY_REVIEW', codes)


class CompositeExtractionTest(unittest.TestCase):
    def test_pdf_all_pages_use_existing_ocr_and_preserve_native_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'source.pdf'; source.write_bytes(b'PDF fixture')
            native = {'pages': [
                {'page': 1, 'method': 'text', 'text': 'native page', 'needs_ocr': False},
                {'page': 2, 'method': 'text', 'text': 'bad', 'needs_ocr': True}], 'warnings': []}
            recognized = {'pages': [
                {'page': 1, 'method': 'ocr', 'text': 'OCR clean page'},
                {'page': 2, 'method': 'ocr', 'text': 'Ⅰ.单项填空(共1小题)\n1. Read?'}], 'warnings': [], 'artifacts': {}}
            with patch.object(ocr_backend, 'read_pdf', return_value=native), \
                 patch.object(ocr_backend, 'recognize') as recognize:
                report = extract.extract_source(source, root / 'out', ocr_result=recognized)
            self.assertTrue(report['passed'])
            recognize.assert_not_called()
            evidence = json.loads((root / 'out' / 'extraction.json').read_text())
            self.assertEqual(evidence['pages'][1]['native_text'], 'bad')
            self.assertEqual(evidence['pages'][0]['text'], 'OCR clean page')
            self.assertEqual(evidence['pages'][0]['native_text'], 'native page')
            self.assertTrue(all(page['method'] == 'ocr' for page in evidence['pages']))
            structure = json.loads((root / 'out' / 'structure.json').read_text())
            self.assertIn('OCR_VISUAL_REVIEW', {i['code'] for i in structure['unresolved']})
            self.assertEqual(source.read_bytes(), b'PDF fixture')

    def test_pdf_layout_survives_extraction_without_borrowing_native_coordinates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source.pdf'
            source.write_bytes(b'PDF fixture')
            native_box = {'type': 'text', 'bbox': [1, 2, 3, 4], 'text': 'bad'}
            ocr_box = {'type': 'paragraph_title', 'bbox': [100, 200, 300, 400],
                       'text': 'Ⅰ.单项填空', 'order': 7}
            native = {'pages': [{'page': 1, 'method': 'text', 'text': 'bad',
                                'blocks': [native_box], 'needs_ocr': True}], 'warnings': []}
            recognized = {'pages': [{'page': 1, 'method': 'ocr',
                'text': 'Ⅰ.单项填空(共1小题)\n1. Read?', 'blocks': [ocr_box],
                'coordinate_system': 'image pixels',
                'outputImages': {'layout_det_res': 'local-layout.jpg'},
                'warnings': ['downloaded_images_require_visual_review']}], 'warnings': []}
            with patch.object(ocr_backend, 'read_pdf', return_value=native):
                extract.extract_source(source, root / 'out', ocr_result=recognized)
            evidence = json.loads((root / 'out' / 'extraction.json').read_text())
            page = evidence['pages'][0]
            self.assertEqual(page['blocks'], [ocr_box])
            self.assertEqual(page['native_blocks'], [native_box])
            self.assertEqual(page['ocr_coordinate_system'], 'image pixels')
            self.assertEqual(page['ocr_outputImages'], {'layout_det_res': 'local-layout.jpg'})
            self.assertEqual(evidence['pdf_ocr']['pages'][0]['blocks'], page['blocks'])
            self.assertEqual(source.read_bytes(), b'PDF fixture')

    def test_pdf_uses_existing_ocr_with_assist_off_and_native_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'source.pdf'; source.write_bytes(b'PDF fixture')
            recognized = {'pages': [
                {'page': 2, 'text': 'Ⅰ.单项填空(共1小题)\n1. Read?'},
                {'page': 1, 'text': ''}], 'warnings': []}
            state = root / 'assist.json'; state.write_text('{"enabled": false}', encoding='utf-8')
            with patch.dict(os.environ, {'MUDU_OCR_ASSIST_STATE': str(state)}), \
                 patch.object(ocr_backend, 'read_pdf', side_effect=RuntimeError('private path')), \
                 patch.object(ocr_backend, 'recognize', side_effect=AssertionError('OCR must not run')):
                report = extract.extract_source(source, root / 'out',
                    pdf_ocr_wait=lambda: (recognized, None))
            self.assertTrue(report['passed'])
            evidence = json.loads((root / 'out' / 'extraction.json').read_text())
            self.assertEqual([page['page'] for page in evidence['pages']], [2, 1])
            self.assertEqual(evidence['pages'][0]['text'], recognized['pages'][0]['text'])
            self.assertFalse(evidence['ocr_assist_enabled'])
            self.assertEqual(evidence['native_error'], {'type': 'RuntimeError'})
            self.assertNotIn('private path', json.dumps(evidence))

    def test_pdf_missing_ocr_never_falls_back_to_native_or_resubmits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for result in (None, {'pages': []}, {'pages': [{'page': 1, 'text': ''}]},
                           {'pages': [{'page': 1, 'text': None}]}):
                with self.subTest(result=result), \
                     patch.object(ocr_backend, 'read_pdf') as native, \
                     patch.object(ocr_backend, 'recognize') as recognize:
                    with self.assertRaisesRegex(ValueError, 'PDF OCR'):
                        extract._pdf(root / 'source.pdf', root, None, ocr_result=result)
                    native.assert_not_called()
                    recognize.assert_not_called()

    def test_image_source_requires_ocr_assist_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'scan.png'; source.write_bytes(b'image fixture')
            state = root / 'assist.json'; state.write_text('{"enabled": false}', encoding='utf-8')
            with patch.dict(os.environ, {'MUDU_OCR_ASSIST_STATE': str(state)}), \
                 patch.object(ocr_backend, 'recognize', side_effect=AssertionError('OCR must not run')):
                with self.assertRaisesRegex(ValueError, 'OCR 辅助已关闭'):
                    extract.extract_source(source, root / 'out')

    def test_native_pdf_images_remain_evidence_without_changing_ocr_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'source.pdf'; source.write_bytes(b'pdf')
            image = root / 'original.png'; image.write_bytes(b'original image')
            native = {'pages': [{'page': 1, 'method': 'text', 'needs_ocr': False,
                'text': 'Ⅰ.单项填空(共1小题)\n1. What?',
                'images': [{'path': str(image), 'bbox': [0, 0, 50, 50]}]}], 'warnings': []}
            recognized = {'pages': [{'page': 1, 'text': 'Ⅰ.单项填空(共1小题)\n1. OCR question?'}]}
            with patch.object(ocr_backend, 'read_pdf', return_value=native):
                extract.extract_source(source, root / 'out', ocr_result=recognized)
            evidence = json.loads((root / 'out' / 'extraction.json').read_text())
            self.assertEqual(evidence['pages'][0]['native_images'], native['pages'][0]['images'])
            self.assertEqual(evidence['pages'][0]['text'], recognized['pages'][0]['text'])
            self.assertNotIn('[IMG pdf-page-', (root / 'out' / '正文.txt').read_text())
            self.assertEqual(image.read_bytes(), b'original image')

    def test_native_word_does_not_require_ocr_config_and_keeps_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            from workflow import docx_extract
            root = Path(tmp); source = root / 'source.docx'; source.write_bytes(b'word fixture')
            def native(src, out):
                (out / 'media').mkdir()
                (out / 'media' / 'image.png').write_bytes(b'image')
                return {'method': 'docx-native', 'text': 'Ⅰ.单项填空(共1小题)\n1. ' + 'Native text. ' * 10 + '\n[IMG image.png]',
                        'blocks': [], 'tables': [], 'warnings': [], 'media': [{'name': 'image.png'}]}
            with patch.object(docx_extract, 'extract_docx', side_effect=native), \
                 patch.object(ocr_backend, 'recognize', side_effect=AssertionError('unexpected OCR')):
                extract.extract_source(source, root / 'out')
                extract.extract_source(source, root / 'out')
            self.assertEqual((root / 'out' / 'media' / 'image.png').read_bytes(), b'image')
            self.assertTrue((root / 'out' / '正文.txt.bak').exists())
            self.assertEqual(len(list((root / 'out').glob('extract-*'))), 2)

    def test_cli_failure_records_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(extract, 'run', side_effect=ValueError('OCR configuration missing')):
                self.assertEqual(extract.main(['--batch-dir', tmp]), 1)
            report = json.loads((Path(tmp) / '07_review' / 'extraction-report.json').read_text())
            self.assertFalse(report['passed'])
            self.assertIn('configuration missing', report['error'])

    def test_ocr_visual_review_is_non_blocking_but_requires_layout(self):
        # OCR 复核是版面视觉证据，不阻断结构切题；但必须置 layout_required，上传前走版面复核。
        from workflow import structure_gate
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            src = folder / '01_source'; src.mkdir()
            (src / '正文.txt').write_text('Ⅰ.单项填空\n1. Example?\n')
            (src / 'structure.json').write_text(json.dumps({
                'sections': [{'type': '单项填空', 'questions': [1]}],
                'unresolved': [{'code': 'OCR_VISUAL_REVIEW', 'message': '需要核对原页'}]}))
            report = structure_gate.run_gate(folder)
            self.assertNotIn('OCR_VISUAL_REVIEW', [x['code'] for x in report['blockers']])
            self.assertIn('OCR_VISUAL_REVIEW', [x.get('code') for x in report.get('warnings', [])])
            self.assertTrue(report['layout_required'])

    def test_automatic_runner_uses_builtin_extract_without_bridge(self):
        self.assertEqual(contracts.STAGE_BY_ID['extract'].kind, contracts.COMMAND)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / 'source.docx'; source.write_bytes(b'fixture')
            manifest = mf.new_manifest('fixture', folder, {'source': {'path': str(source), 'exists': True}}, {})
            mf.save(folder, manifest)
            def completed(batch, state, stage):
                self.assertEqual(stage.command[0], 'workflow/extract.py')
                # Empty extract output is not a valid delivery now, so the
                # stage is recorded failed instead of completed.
                mf.touch_stage(state, 'extract', mf.FAILED, notes='empty source')
                mf.save(batch, state)
                return 0
            with patch.object(runner, '_execute_command', side_effect=completed):
                result = auto.advance(folder, {})
            self.assertNotEqual(mf.load(folder)['stages']['extract']['status'], mf.COMPLETED)
            # No valid extraction means structure_gate cannot be claimed.
            self.assertFalse(mf.dependency_satisfied(mf.load(folder), 'structure_gate'))
