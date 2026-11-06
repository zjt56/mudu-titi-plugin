"""Unreviewed upload confirmation is specific to the displayed state and attempt."""
import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import runner
from workbench import server


class ConfirmationTests(unittest.TestCase):
    def test_summary_revision_changes_with_review_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            review_dir = batch / '07_review'
            review_dir.mkdir()
            queue = review_dir / 'manual-review-queue.json'
            queue.write_text('{"items": []}')
            with patch.object(runner.review, 'evaluate', return_value={'passed': False, 'counts': {}, 'errors': []}):
                before = runner.unreviewed_summary(batch, {})
                queue.write_text('{"items": [{"status": "open"}]}')
                after = runner.unreviewed_summary(batch, {})
            self.assertNotEqual(before['revision'], after['revision'])
            self.assertFalse(before['passed'])

    def test_server_requires_boolean_and_current_revision(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(server, 'batch_dir', return_value=Path(tmp)), \
             patch.object(server.wf_manifest, 'load', return_value={}), \
             patch.object(runner, 'unreviewed_summary', return_value={'revision': 'current'}), \
             patch.object(server, 'upload_state', return_value={'state': 'idle'}), \
             patch.object(server.threading, 'Thread') as thread:
            with self.assertRaises(ValueError):
                server.start_batch_upload('batch', allow_unreviewed='true', unreviewed_revision='current')
            with self.assertRaises(ValueError):
                server.start_batch_upload('batch', allow_unreviewed=True, unreviewed_revision='old')
            self.assertFalse(thread.called)
            server.start_batch_upload('batch', allow_unreviewed=True, unreviewed_revision='current')
            args = thread.call_args.kwargs['args']
            self.assertEqual(args[2], 'current')
            self.assertTrue(args[3])
            server._upload_jobs.pop('batch', None)

    def test_attempt_token_mismatch_rejected_before_submit(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / '06_batch').mkdir()
            (folder / '06_batch' / 'batch_meta.json').write_text('{}')
            manifest = {'stages': {'upload_gate': {'status': runner.mf.APPROVED,
                        'approved_batch_sha256': runner.mf.sha256_file(folder / '06_batch' / 'batch_meta.json'),
                        'unreviewed_confirmation': {'revision': 'r', 'attempt_sha256': 'other'}}}}
            with patch.object(runner.mf, 'load', return_value=manifest), \
                 patch('workflow.upload.submit_approved') as submit:
                result = runner.cmd_upload(argparse.Namespace(batch_dir=str(folder), yes=True,
                                                               unreviewed_attempt='wrong'))
            self.assertEqual(result, 2)
            submit.assert_not_called()

    def test_confirmed_attempt_preserves_review_and_consumes_token(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / '06_batch').mkdir()
            (folder / '07_review').mkdir()
            batch_file = folder / '06_batch' / 'batch_meta.json'
            batch_file.write_text('{"knowledge": ["original"]}')
            review_file = folder / '07_review' / 'review-gate.json'
            review_file.write_text('{"passed": false}')
            manifest = {'stages': {'upload_gate': {'status': runner.mf.APPROVED,
                        'approved_batch_sha256': runner.mf.sha256_file(batch_file),
                        'unreviewed_confirmation': {'revision': 'r',
                            'attempt_sha256': hashlib.sha256(b'one-attempt').hexdigest()}},
                        'validate': {'status': runner.mf.COMPLETED}}, 'side_effects': []}
            args = argparse.Namespace(batch_dir=str(folder), yes=True, unreviewed_attempt='one-attempt')
            with patch.object(runner.mf, 'load', return_value=manifest), \
                 patch.object(runner.mf, 'is_stale', return_value=(False, '')), \
                 patch.object(runner.mf, 'save'), patch.object(runner.mf, 'append_log'), \
                 patch.object(runner.mf, 'derive_status', return_value='active'), \
                 patch.object(runner, '_record_success'), \
                 patch.object(runner, 'unreviewed_summary', return_value={'revision': 'r'}), \
                 patch.object(runner.review, 'evaluate', return_value={'passed': False}), \
                 patch.object(runner, 'flowchart_checks', return_value={'passed': True, 'errors': []}), \
                 patch('workflow.label_audit.inspect_file', return_value={'passed': True}), \
                 patch('workflow.upload.submit_approved', return_value={'id': 'local-test', 'payload_sha256': 'hash'}) as submit:
                self.assertEqual(runner.cmd_upload(args), 0)
                self.assertNotIn('unreviewed_confirmation', manifest['stages']['upload_gate'])
                submit.assert_called_once()
            self.assertEqual(batch_file.read_text(), '{"knowledge": ["original"]}')
            self.assertEqual(review_file.read_text(), '{"passed": false}')


if __name__ == '__main__':
    unittest.main()
