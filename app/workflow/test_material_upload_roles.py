"""Content-based attachment identity before the extraction delivery."""
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from workflow import material_classify as roles, extract, pdf_ocr, manifest as mf

PAPER = 'Unit 2\n第四课时 Section B\nⅠ.单项填空\n1. Who is he?\nA. Dad B. Mum C. Tom D. Jane\n'
ANSWER = '参考答案\nUnit 2\n第四课时 Section B\nⅠ.1.A\nⅡ.1.family\n'

class MaterialUploadRoleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'attachments').mkdir()

    def file(self, name, text):
        path = self.root / 'attachments' / name
        path.write_text(text, encoding='utf-8')
        return {'path': str(path), 'sha256': mf.sha256_file(path), 'required': True}

    def test_answers_first_mixed_formats_rebinds_by_content_and_keeps_original_name(self):
        state = {'inputs': {'source': self.file('source.docx', ANSWER),
                            'attachment_001': self.file('attachment_001.pdf', PAPER)}}
        (self.root / 'attachments/batch.json').write_text(json.dumps({
            'source': 'source.docx', 'original_filenames': {'source': 'a.docx'},
            'extra_documents': [{'path': 'attachment_001.pdf', 'original_filename': 'Unit2课时4.pdf'}]}))
        with patch.object(roles, '_document_text', side_effect=lambda b, p: Path(p).read_text()):
            result = roles.classify_documents(self.root, manifest=state)
            chosen = roles.select_document_roles(self.root, state, result)
            final = roles.classify_documents(self.root, manifest=chosen, classified=result)
        self.assertTrue(chosen['inputs']['source']['path'].endswith('attachment_001.pdf'))
        self.assertEqual(chosen['inputs']['source']['original_filename'], 'Unit2课时4.pdf')
        self.assertTrue(chosen['inputs']['answers']['path'].endswith('source.docx'))
        self.assertNotIn('docx', chosen['inputs'])
        by_key = {row['input']: row for row in final['documents']}
        self.assertEqual((self.root / by_key['source']['text_path']).read_text(), PAPER)
        self.assertEqual((self.root / by_key['answers']['text_path']).read_text(), ANSWER)
        self.assertTrue(by_key['answers']['content_authority'])

    def test_extract_corrects_answer_word_selected_ahead_of_pdf_paper(self):
        state = {'inputs': {'source': self.file('source.docx', ANSWER),
                            'attachment_001': self.file('attachment_001.pdf', PAPER)}}
        def recognize(source, out, **kwargs):
            return {'pages': [{'page': 1, 'text': PAPER}], 'warnings': []}
        def word(source, out):
            return {'text': ANSWER, 'media': [], 'warnings': []}
        with patch('workflow.local_agent._agent_lock', side_effect=lambda *a: nullcontext()), \
             patch.object(mf, 'load', side_effect=lambda *a: state), \
             patch.object(mf, 'save', side_effect=lambda batch, new: (state.clear(), state.update(new)) if new is not state else None), \
             patch('workflow.agent.configured', return_value=False), \
             patch('workflow.docx_extract.extract_docx', side_effect=word), \
             patch.object(pdf_ocr.ocr_backend, 'recognize', side_effect=recognize) as calls, \
             patch.object(pdf_ocr.ocr_backend, 'read_pdf', side_effect=RuntimeError('optional')):
            result = extract.run(self.root)
        self.assertTrue(result['passed'], result.get('error'))
        self.assertTrue(state['inputs']['source']['path'].endswith('attachment_001.pdf'))
        self.assertIn('Who is he?', (self.root / '01_source/原始正文.txt').read_text())
        self.assertNotIn('参考答案', (self.root / '01_source/原始正文.txt').read_text())
        self.assertIn('参考答案', (self.root / '01_source_ans/正文.txt').read_text())
        self.assertEqual(calls.call_count, 1)
        self.assertTrue((self.root / '01_source/pdf-ocr.md').is_file())

    def test_independent_answer_pdf_is_not_word_paper_duplicate(self):
        state = {'inputs': {'source': self.file('paper.docx', PAPER), 'answers': self.file('key.pdf', ANSWER)}}
        with patch.object(roles, '_document_text', side_effect=lambda b, p: Path(p).read_text()):
            result = roles.classify_documents(self.root, manifest=state)
        answer = next(row for row in result['documents'] if row['input'] == 'answers')
        self.assertEqual(answer['role'], 'answers')
        self.assertTrue(answer['content_authority'])

    def test_multiple_different_papers_are_not_merged_by_order(self):
        state = {'inputs': {'source': self.file('one.docx', PAPER), 'answers': self.file('two.docx', PAPER+'Extra')}}
        with patch.object(roles, '_document_text', side_effect=lambda b, p: Path(p).read_text()):
            result = roles.classify_documents(self.root, manifest=state)
        with self.assertRaisesRegex(ValueError, 'MATERIAL_MULTIPLE_PAPERS'):
            roles.select_document_roles(self.root, state, result)

    def test_paper_answers_and_listening_script_bundle_classifies_and_rebinds_in_any_order(self):
        paper = (
            '2025年某省初中学业水平考试\n'
            'Ⅰ.短对话理解\n'
            '1. What is Sandy\'s hobby?\nA. Reading. B. Running. C. Painting.\n'
            'Ⅴ.单项填空\n'
            '21. I took over his ______ as lead singer.\nA. farm B. form C. role D. sale\n'
        )
        answers = (
            '2025年某省初中学业水平考试\n'
            'Ⅰ.短对话理解\n1~5 CBCAC\n'
            'Ⅴ.单项填空\n21.C　【解析】考查名词。role“角色”，符合语境。\n'
            'Ⅵ.完形填空\n【文章大意】本文是一篇记叙文。\n'
            '31.D　【解析】考查副词。\n'
            'Ⅹ.书面表达\n【参考范文】Last Friday...\n【评分标准】20分。\n'
        )
        script = (
            '**2025年某省初中学业水平考试**\n'
            '**Ⅰ****.****短对话理解**\n'
            '**你将听到五段对话。请在每小题所给的A、B、C、三个选项中选出一个最佳选项。每段对话读两遍。**\n'
            '1.W:What is your hobby, Simon?\nM:I like reading.\nW:I enjoy running.\n'
            '**Ⅱ****.****长对话理解**\n'
            '听下面一段对话，回答第6、7题。\n'
            'M:Oh no, we missed the bus.\nW:Take it easy.\n'
        )
        # Deliberately upload in scrambled order: listening script in source slot, answers in attachment_001, paper in attachment_002
        state = {'inputs': {
            'source': self.file('source.docx', script),
            'attachment_001': self.file('attachment_001.docx', answers),
            'attachment_002': self.file('attachment_002.docx', paper),
        }}
        def bad_model(prompt, run_dir, cfg):
            if 'W:What is your hobby' in prompt:
                return json.dumps({'status': 'completed', 'artifacts': {'classification': {'role': 'paper'}}, 'notes': [], 'findings': []})
            if '【解析】考查名词' in prompt:
                return json.dumps({'status': 'completed', 'artifacts': {'classification': {'role': 'mix'}}, 'notes': [], 'findings': []})
            return json.dumps({'status': 'completed', 'artifacts': {'classification': {'role': 'paper'}}, 'notes': [], 'findings': []})

        with patch.object(roles, '_document_text', side_effect=lambda b, p: Path(p).read_text()):
            result = roles.classify_documents(self.root, invoke_fn=bad_model, manifest=state)
            chosen = roles.select_document_roles(self.root, state, result)
            final = roles.classify_documents(self.root, invoke_fn=bad_model, manifest=chosen, classified=result)
        self.assertTrue(chosen['inputs']['source']['path'].endswith('attachment_002.docx'))
        self.assertTrue(chosen['inputs']['answers']['path'].endswith('attachment_001.docx'))
        self.assertTrue(chosen['inputs']['attachment_001']['path'].endswith('source.docx'))
        by_key = {row['input']: row for row in final['documents']}
        self.assertEqual(by_key['source']['role'], 'paper')
        self.assertEqual(by_key['answers']['role'], 'answers')
        self.assertEqual(by_key['attachment_001']['role'], 'listening_script')
        self.assertTrue(by_key['attachment_001']['content_authority'])

    def test_listening_only_paper_with_options_or_blanks_stays_paper(self):
        listening_paper = (
            'Ⅰ.短对话理解\n'
            '你将听到五段对话。每段对话读两遍。\n'
            '1. What does Tim like?\nA. Music. B. Sports. C. Reading.\n'
            'Ⅳ.信息转换\n'
            'Break the <u>　16　</u>.\n'
        )
        self.assertEqual(roles._classify_deterministic(listening_paper), 'paper')
        self.assertFalse(roles._looks_like_listening_script(listening_paper))

    def test_user_roles_override_and_structure_reference_exclusion(self):
        # Two genuinely different papers would normally raise MATERIAL_MULTIPLE_PAPERS;
        # marking the second one as structure_reference via user_roles excludes it cleanly.
        state = {'inputs': {
            'source': self.file('source.docx', PAPER),
            'attachment_001': self.file('attachment_001.docx', PAPER + '\n2. Extra question?\nA. Yes B. No\n'),
        }}
        (self.root / 'attachments/batch.json').write_text(json.dumps({
            'source': 'source.docx',
            'original_filenames': {'source': '2025安徽正文.docx'},
            'extra_documents': [{'path': 'attachment_001.docx', 'original_filename': '版式对照.docx'}],
            'user_roles': {'source.docx': 'paper', 'attachment_001.docx': 'structure_reference'},
        }), encoding='utf-8')
        with patch.object(roles, '_document_text', side_effect=lambda b, p: Path(p).read_text()):
            result = roles.classify_documents(self.root, manifest=state)
            chosen = roles.select_document_roles(self.root, state, result)
        by_key = {row['input']: row for row in result['documents']}
        self.assertEqual(by_key['source']['role'], 'paper')
        self.assertEqual(by_key['source']['source'], 'user')
        self.assertEqual(by_key['source']['original_filename'], '2025安徽正文.docx')
        self.assertEqual(by_key['attachment_001']['role'], 'structure_reference')
        self.assertEqual(by_key['attachment_001']['source'], 'user')
        self.assertFalse(by_key['attachment_001']['content_authority'])
        self.assertNotIn('content_role', by_key['attachment_001'])
        self.assertTrue(chosen['inputs']['source']['path'].endswith('source.docx'))

    def test_filename_role_hint_only_applies_when_content_is_other(self):
        # Ambiguous plain text -> fallback returns 'other' -> filename weak prior kicks in
        role, src = roles._classify_one('一段简短说明文本', None, None, filename='2025安徽听力材料.docx')
        self.assertEqual(role, 'listening_script')
        self.assertEqual(src, 'filename_hint')
        # Clear exam paper content with misleading filename -> content wins, never overridden by filename
        role2, src2 = roles._classify_one(PAPER, None, None, filename='2025安徽听力材料.docx')
        self.assertEqual(role2, 'paper')
        self.assertEqual(src2, 'fallback')

    def test_workbench_upload_and_material_roles_api(self):
        from workbench import server
        with tempfile.TemporaryDirectory() as tmp:
            wf_dir = Path(tmp) / 'workflows'
            inbox_dir = Path(tmp) / 'inbox'
            wf_dir.mkdir(); inbox_dir.mkdir()
            files = [
                {'filename': '听力稿.docx', 'data': b'PK-script'},
                {'filename': '正文卷.docx', 'data': b'PK-paper'},
                {'filename': '参考答案.docx', 'data': b'PK-ans'},
            ]
            fields = {
                'title': '测试多材料卷',
                'file_roles': json.dumps({'0': 'listening_script', '1': 'paper', '2': 'answers'}),
            }
            with patch.object(server, 'WORKFLOWS', wf_dir), \
                 patch.object(server, 'INBOX', inbox_dir), \
                 patch.object(server, 'start_advance', return_value={'started': True}), \
                 patch.object(server, '_wake_supervisor', return_value=None):
                up = server.handle_upload(fields, files)
                bname = up['name']
                desc = json.loads((wf_dir / bname / 'attachments' / 'batch.json').read_text(encoding='utf-8'))
                self.assertEqual(desc['original_filenames']['source'], '正文卷.docx')
                self.assertEqual(desc['original_filenames']['answers'], '参考答案.docx')
                self.assertEqual(desc['user_roles'].get(desc['source']), 'paper')
                self.assertEqual(desc['user_roles'].get(desc['answers']), 'answers')
                extra_path = desc['extra_documents'][0]['path']
                self.assertEqual(desc['user_roles'].get(extra_path), 'listening_script')

                got = server.get_material_roles(bname)
                self.assertTrue(got['ok'])
                self.assertEqual(len(got['documents']), 3)
                by_orig = {d['original_filename']: d for d in got['documents']}
                self.assertEqual(by_orig['听力稿.docx']['user_role'], 'listening_script')

                with patch.object(server, '_launch_advance', return_value={'started': True}) as launch_mock:
                    updated = server.set_material_roles(bname, {extra_path: 'structure_reference'}, rerun=True)
                self.assertTrue(updated['ok'])
                self.assertEqual(updated['user_roles'].get(extra_path), 'structure_reference')
                self.assertTrue(updated['rerun']['started'])
                launch_mock.assert_called_once()
