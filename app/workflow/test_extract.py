"""复合提取路由与证据保留，不依赖 GPU 的回归。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import auto, contracts, extract, manifest as mf, ocr_backend, runner


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

    def test_ambiguous_sections_never_claim_coverage(self):
        structure, _, _ = extract.infer_structure('Ⅰ.完形填空(共5小题)\n1. one\n2. two\n')
        codes = {x['code'] for x in structure['unresolved']}
        self.assertIn('QUESTION_COUNT_MISMATCH', codes)
        self.assertIn('GROUP_BOUNDARY_REVIEW', codes)


class CompositeExtractionTest(unittest.TestCase):
    def test_pdf_only_ocr_for_bad_page_and_preserves_native_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'source.pdf'; source.write_bytes(b'PDF fixture')
            native = {'pages': [
                {'page': 1, 'method': 'text', 'text': 'native page', 'needs_ocr': False},
                {'page': 2, 'method': 'text', 'text': 'bad', 'needs_ocr': True}], 'warnings': []}
            recognized = {'pages': [{'page': 2, 'method': 'ocr', 'text': 'Ⅰ.单项填空(共1小题)\n1. Read?'}], 'warnings': [], 'artifacts': {}}
            with patch.object(ocr_backend, 'read_pdf', return_value=native), \
                 patch.object(ocr_backend, 'recognize', return_value=recognized) as recognize:
                report = extract.extract_source(source, root / 'out')
            self.assertTrue(report['passed'])
            self.assertEqual(recognize.call_count, 1)
            self.assertEqual(recognize.call_args.kwargs['pages'], '2')
            self.assertTrue(recognize.call_args.kwargs['force_ocr'])
            evidence = json.loads((root / 'out' / 'extraction.json').read_text())
            self.assertEqual(evidence['pages'][1]['native_text'], 'bad')
            self.assertEqual(evidence['pages'][0]['text'], 'native page')
            structure = json.loads((root / 'out' / 'structure.json').read_text())
            self.assertIn('OCR_VISUAL_REVIEW', {i['code'] for i in structure['unresolved']})
            self.assertEqual(source.read_bytes(), b'PDF fixture')

    def test_native_pdf_images_are_preserved_as_reviewable_page_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'source.pdf'; source.write_bytes(b'pdf')
            image = root / 'original.png'; image.write_bytes(b'original image')
            native = {'pages': [{'page': 1, 'method': 'text', 'needs_ocr': False,
                'text': 'Ⅰ.单项填空(共1小题)\n1. What?',
                'images': [{'path': str(image), 'bbox': [0, 0, 50, 50]}]}], 'warnings': []}
            with patch.object(ocr_backend, 'read_pdf', return_value=native):
                extract.extract_source(source, root / 'out')
            self.assertEqual((root / 'out' / 'media' / 'pdf-page-1-image-1.png').read_bytes(), b'original image')
            self.assertIn('[IMG pdf-page-1-image-1.png]', (root / 'out' / '正文.txt').read_text())
            structure = json.loads((root / 'out' / 'structure.json').read_text())
            self.assertIn('EXTRACTION_WARNING', {x['code'] for x in structure['unresolved']})

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

    def test_unresolved_ocr_cannot_pass_structure_gate(self):
        from workflow import structure_gate
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            src = folder / '01_source'; src.mkdir()
            (src / '正文.txt').write_text('Ⅰ.单项填空\n1. Example?\n')
            (src / 'structure.json').write_text(json.dumps({
                'sections': [{'type': '单项填空', 'questions': [1]}],
                'unresolved': [{'code': 'OCR_VISUAL_REVIEW', 'message': '需要核对原页'}]}))
            report = structure_gate.run_gate(folder)
            self.assertFalse(report['passed'])
            self.assertIn('OCR_VISUAL_REVIEW', [x['code'] for x in report['blockers']])

    def test_automatic_runner_uses_builtin_extract_without_bridge(self):
        self.assertEqual(contracts.STAGE_BY_ID['extract'].kind, contracts.COMMAND)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / 'source.docx'; source.write_bytes(b'fixture')
            manifest = mf.new_manifest('fixture', folder, {'source': {'path': str(source), 'exists': True}}, {})
            mf.save(folder, manifest)
            def completed(batch, state, stage):
                self.assertEqual(stage.command[0], 'workflow/extract.py')
                mf.touch_stage(state, 'extract', mf.COMPLETED)
                mf.save(batch, state)
                return 0
            with patch.object(runner, '_execute_command', side_effect=completed):
                result = auto.advance(folder, {})
            self.assertEqual(mf.load(folder)['stages']['extract']['status'], mf.COMPLETED)
            # 新 DAG 在答案前先验证源头；此 mock 没有产出正文，应停在结构闸门。
            self.assertEqual(result['stopped_at'], 'structure_gate')
            gate = json.loads((folder / '07_review/structure-gate.json').read_text())
            self.assertFalse(gate['passed'])
            self.assertIn('SOURCE_EMPTY', {issue['code'] for issue in gate['blockers']})
