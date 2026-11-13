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
