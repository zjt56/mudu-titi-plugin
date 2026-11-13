"""Loopback HTTP through the batch acquisition/backend/store boundary."""
import hashlib
import json
from unittest.mock import patch
from workflow import pdf_ocr, ocr_backend
from workflow.test_paddle_jobs_recovery import PaddleRecoveryTest


class OcrPipelineRecoveryTest(PaddleRecoveryTest):
    # Reuse only fixture setup and HTTP server; inherit existing recovery checks too.
    def test_batch_wait_resume_and_cache_through_real_backend(self):
        batch = self.root / 'batch'
        batch.mkdir()
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        entry = {'path': str(self.source), 'sha256': digest}
        manifest = {'inputs': {'source': entry, 'alias': dict(entry)}}
        self.states = ['running']
        config = {'backend': 'paddle_jobs', **self.config}
        first = pdf_ocr.run(batch, manifest, config)
        self.assertEqual(first['status'], 'waiting')
        self.assertEqual(first['sources'][0]['progress']['job_id'], 'durable-job')
        self.assertEqual(first['sources'][0]['progress']['total_pages'], 12)
        self.assertNotIn('result_path', first['sources'][0])
        self.assertEqual(sum(method == 'POST' for method, path in self.calls), 1)
        self.states = ['done']
        second = pdf_ocr.run(batch, manifest, config)
        self.assertEqual(second['status'], 'completed')
        self.assertEqual(second['sources'][1]['duplicate_of'], 'source')
        self.assertEqual(sum(method == 'POST' for method, path in self.calls), 1)
        count = len(self.calls)
        third = pdf_ocr.run(batch, manifest, config)
        self.assertEqual(third['status'], 'completed')
        self.assertEqual(len(self.calls), count)
        stored = json.loads((batch / third['sources'][0]['result_path']).read_text())
        self.assertEqual(stored['result']['pages'][0]['text'], 'Synthetic OCR')
        self.assertNotIn('secret-private', json.dumps(third))

    def test_batch_reconciles_unknown_submission_without_second_post(self):
        batch = self.root / 'batch'
        batch.mkdir()
        entry = {'path': str(self.source), 'sha256': hashlib.sha256(self.source.read_bytes()).hexdigest()}
        manifest = {'inputs': {'source': entry}}
        config = {'backend': 'paddle_jobs', **self.config}
        self.post_drop = True
        first = pdf_ocr.run(batch, manifest, config)
        self.assertEqual(first['status'], 'failed')
        self.assertEqual(first['sources'][0]['error']['code'], 'submission_unknown')
        second = pdf_ocr.run(batch, manifest, config)
        self.assertEqual(second['status'], 'completed')
        self.assertEqual(sum(method == 'POST' for method, path in self.calls), 1)
        self.assertTrue(any('/batch/' in path for method, path in self.calls))
