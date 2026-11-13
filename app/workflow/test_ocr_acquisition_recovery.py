"""Batch acquisition must preserve durable scope and never deliver partial waits."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import pdf_ocr, ocr_backend, extract


class AcquisitionRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.batch = self.root / 'batch'
        self.batch.mkdir()
        self.pdf = self.root / 'paper.pdf'
        self.pdf.write_bytes(b'%PDF-recovery')
        self.manifest = {'inputs': {'source': self.entry(self.pdf)}}

    def entry(self, p):
        return {'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}

    def waiting(self):
        return ocr_backend.OCRBackendError('private', code='waiting', details={
            'job_id': 'job-123', 'phase': 'polling', 'retryable': True})

    def test_wait_then_resume_uses_same_source_scope_and_no_false_delivery(self):
        scopes = []
        def recognize(source, output, **kwargs):
            scopes.append(output)
            kwargs['progress_callback']({'job_id': 'job-123', 'phase': 'running',
                'total_pages': 2, 'extracted_pages': 1, 'token': 'secret',
                'state': 'running', 'url': 'https://private.invalid/signed'})
            live = json.loads((self.batch / pdf_ocr.REPORT).read_text())
            self.assertEqual(live['status'], 'running')
            self.assertEqual(live['sources'][0]['progress']['extracted_pages'], 1)
            self.assertNotIn('secret', json.dumps(live))
            self.assertNotIn('private.invalid', json.dumps(live))
            if len(scopes) == 1:
                raise self.waiting()
            return {'pages': [{'page': 1, 'text': 'service original'}]}
        with patch.object(ocr_backend, 'recognize', side_effect=recognize):
            waiting = pdf_ocr.run(self.batch, self.manifest)
            self.assertEqual(waiting['status'], 'waiting')
            self.assertNotIn('result_path', waiting['sources'][0])
            self.assertEqual((self.batch / pdf_ocr.MARKDOWN).read_text(), '')
            done = pdf_ocr.run(self.batch, self.manifest)
        self.assertEqual(scopes[0], scopes[1])
        self.assertEqual(done['status'], 'completed')
        self.assertEqual(done['sources'][0]['pages'][0]['text'], 'service original')
        self.assertEqual(waiting['sources'][0]['progress']['job_id'], 'job-123')

    def test_wait_alias_is_deduplicated_but_real_failure_wins(self):
        other = self.root / 'answers.pdf'
        other.write_bytes(b'%PDF-other')
        self.manifest['inputs'].update(alias=self.entry(self.pdf), answers=self.entry(other))
        with patch.object(ocr_backend, 'recognize', side_effect=[self.waiting(), RuntimeError('private')]) as call:
            report = pdf_ocr.run(self.batch, self.manifest)
        self.assertEqual(call.call_count, 2)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual([s['status'] for s in report['sources']], ['waiting', 'waiting', 'failed'])
        self.assertEqual(report['sources'][1]['duplicate_of'], 'source')

    def test_unknown_submission_retains_bound_get_only_reconciliation(self):
        def recognize(source, output, **kwargs):
            kwargs['progress_callback']({'phase': 'submission_unknown', 'batch_id': 'batch-123',
                'fingerprint': 'a' * 64, 'config_sha256': 'b' * 64})
            raise ocr_backend.OCRBackendError('private', code='submission_unknown')
        with patch.object(ocr_backend, 'recognize', side_effect=recognize):
            report = pdf_ocr.run(self.batch, self.manifest)
        self.assertEqual(report['status'], 'failed')
        task = report['sources'][0]['reconciliation']
        self.assertEqual(task['method'], 'GET')
        self.assertEqual(task['batch_id'], 'batch-123')
        self.assertEqual(task['source_sha256'], self.entry(self.pdf)['sha256'])
        self.assertEqual(task['fingerprint'], 'a' * 64)

    def test_unknown_submission_does_not_become_resumable_wait(self):
        error = ocr_backend.OCRBackendError('private', code='submission_unknown',
                                           details={'phase': 'submitting', 'retryable': False})
        with patch.object(ocr_backend, 'recognize', side_effect=error):
            report = pdf_ocr.run(self.batch, self.manifest)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['sources'][0]['error']['code'], 'submission_unknown')

    def test_main_wait_exit_is_six_and_report_is_not_passed(self):
        with patch.object(extract, 'run', side_effect=extract.OCRAcquisitionPending('waiting')):
            code = extract.main(['--batch-dir', str(self.batch)])
        self.assertEqual(code, 6)
        report = json.loads((self.batch / '07_review/extraction-report.json').read_text())
        self.assertFalse(report['passed'])
        self.assertEqual(report['status'], 'waiting')

    def test_multiple_documents_wait_before_role_selection(self):
        word = self.root / 'answers.docx'
        word.write_bytes(b'word')
        manifest = {'inputs': {'source': self.entry(self.pdf), 'answers': self.entry(word)}}
        from workflow import material_classify
        with patch.object(extract.mf, 'load', return_value=manifest), \
             patch.object(pdf_ocr, 'run', return_value={'status': 'waiting'}), \
             patch.object(material_classify, 'classify_documents') as classify:
            with self.assertRaises(extract.OCRAcquisitionPending):
                extract.run(self.batch)
        classify.assert_not_called()

    def test_completed_and_waiting_sources_hold_entire_acquisition(self):
        other = self.root / 'other.pdf'
        other.write_bytes(b'%PDF-two')
        self.manifest['inputs']['answers'] = self.entry(other)
        with patch.object(ocr_backend, 'recognize', side_effect=[
                {'pages': [{'page': 1, 'text': 'ready'}]}, self.waiting()]):
            report = pdf_ocr.run(self.batch, self.manifest)
        self.assertEqual(report['status'], 'waiting')
        with self.assertRaises(extract.OCRAcquisitionPending):
            extract._require_pdf_acquisition(report)


if __name__ == '__main__':
    unittest.main()
