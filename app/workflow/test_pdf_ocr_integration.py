import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import extract, material_classify, pdf_ocr


class PdfOcrIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.batch = Path(self.temp.name)
        self.pdf = self.batch / 'paper.pdf'
        self.pdf.write_bytes(b'paper')
        self.answer = self.batch / 'answer.pdf'
        self.answer.write_bytes(b'answer')
        self.word = self.batch / 'paper.docx'
        self.word.write_bytes(b'word')

    def entry(self, path):
        return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

    def native(self, source, out, config=None):
        return {'pages': [{'page': 1, 'text': '', 'blocks': [], 'images': [], 'needs_ocr': True}], 'warnings': []}

    def recognize(self, source, out, **kwargs):
        text = {'paper': '一、单项选择\n1. Paper question?', 'answer': '参考答案\n1. A'}[source.read_bytes().decode()]
        return {'pages': [{'page': 1, 'text': text}], 'warnings': ['OCR needs review']}

    def test_batch_acquires_once_per_unique_pdf_and_reuses_by_source(self):
        manifest = {'inputs': {'source': self.entry(self.pdf), 'answers': self.entry(self.answer),
                               'attachment_001': self.entry(self.pdf)}}
        with patch.object(extract.mf, 'load', return_value=manifest), \
             patch('workflow.agent.configured', return_value=False), \
             patch.object(pdf_ocr.ocr_backend, 'recognize', side_effect=self.recognize) as calls, \
             patch.object(pdf_ocr.ocr_backend, 'read_pdf', side_effect=self.native):
            result = extract.run(self.batch)
        self.assertTrue(result['passed'])
        self.assertEqual(calls.call_count, 2)
        self.assertIn('Paper question', (self.batch / '01_source/原始正文.txt').read_text())
        self.assertIn('参考答案', (self.batch / '01_source_ans/正文.txt').read_text())
        rows = result['material_roles']['documents']
        for row in rows:
            text = (self.batch / row['text_path']).read_text()
            self.assertEqual('Paper question' in text, row['input'] == 'source')
        extraction = json.loads((self.batch / '01_source/extraction.json').read_text())
        self.assertEqual(extraction['pages'][0]['method'], 'ocr')
        self.assertIn('OCR needs review', extraction['warnings'])
        self.assertTrue((self.batch / result['pdf_ocr']['sources'][0]['result_path']).is_file())

    def test_word_source_and_pdf_error_are_visible_without_retry(self):
        manifest = {'inputs': {'source': self.entry(self.word), 'answers': self.entry(self.pdf)}}
        def word(source, evidence):
            return {'text': '一、单项选择\n1. Word question?', 'media': [], 'warnings': []}
        with patch.object(extract.mf, 'load', return_value=manifest), \
             patch('workflow.agent.configured', return_value=False), \
             patch('workflow.docx_extract.extract_docx', side_effect=word), \
             patch.object(pdf_ocr.ocr_backend, 'recognize', side_effect=RuntimeError('offline')) as calls, \
             patch.object(pdf_ocr.ocr_backend, 'read_pdf', side_effect=self.native):
            result = extract.run(self.batch)
        self.assertTrue(result['passed'])
        self.assertTrue(any('继续使用 Word' in warning for warning in result['warnings']))
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(result['pdf_ocr']['sources'][0]['status'], 'failed')
        self.assertIn('Word question', (self.batch / '01_source/原始正文.txt').read_text())

    def test_empty_scanned_pdf_does_not_become_page_marker_text(self):
        with patch.object(pdf_ocr.ocr_backend, 'read_pdf', side_effect=self.native):
            with self.assertRaisesRegex(ValueError, '提取正文为空'):
                extract.extract_source(self.pdf, self.batch / 'out', ocr_warning='OCR failed')

    def test_native_text_wins_and_wrong_source_is_rejected(self):
        manifest = {'inputs': {'source': self.entry(self.pdf)}}
        with patch.object(pdf_ocr.ocr_backend, 'recognize', side_effect=self.recognize):
            report = pdf_ocr.run(self.batch, manifest)
        found, warning = extract._pdf_ocr_for_source(self.batch, self.answer, report)
        self.assertIsNone(found)
        self.assertTrue(warning)
        native = {'pages': [{'page': 1, 'text': 'native text', 'blocks': [], 'images': [], 'needs_ocr': False}]}
        found, warning = extract._pdf_ocr_for_source(self.batch, self.pdf, report)
        with patch.object(pdf_ocr.ocr_backend, 'read_pdf', return_value=native), \
             patch.object(pdf_ocr.ocr_backend, 'recognize') as calls:
            result = extract._pdf(self.pdf, self.batch, None, found, warning)
        self.assertIn('native text', result['text'])
        self.assertIn('Paper question', result['pdf_ocr']['pages'][0]['text'])
        calls.assert_not_called()
        self.pdf.write_bytes(b'changed')
        found, warning = extract._pdf_ocr_for_source(self.batch, self.pdf, report)
        self.assertIsNone(found)
        self.assertIn('摘要', warning)


if __name__ == '__main__':
    unittest.main()
