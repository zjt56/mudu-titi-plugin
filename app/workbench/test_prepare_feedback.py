import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from workbench import server


class PrepareFeedbackTests(unittest.TestCase):
    def setUp(self):
        check = patch.object(server, '_check_upload_preconditions')
        check.start()
        self.addCleanup(check.stop)

    def test_failed_prepare_and_restart_summarize_findings(self):
        report = {'passed': False, 'errors': [
            {'code': 'MISSING', 'message': '缺少答案', 'unit_id': f'Q{i}'} for i in range(8)
        ] + [{'code': 'BAD_MEDIA', 'message': '媒体无效', 'unit_id': 'Q9'}],
            'warnings': [{'code': 'WARN', 'message': 'warning noise' * 200}] * 20,
            'content_source': {'warnings': [{'code': 'OTHER'}] * 4}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 'batch'
            (folder / '07_upload').mkdir(parents=True)
            (folder / '07_upload' / 'preflight.json').write_text(json.dumps(report), encoding='utf-8')
            jobs = {'batch': {'running': True, 'ok': None, 'step': '', 'output': '', 'error': ''}}
            proc = SimpleNamespace(returncode=1, stdout=json.dumps(report), stderr='stderr diagnostic')
            with patch.object(server, 'WORKFLOWS', root), patch.object(server, '_upload_jobs', jobs), \
                    patch.object(server, '_run_runner', return_value=proc) as runner, \
                    patch('workbench.ssh_tunnel.ensure_upload_connection', return_value='ready'):
                server._run_batch_upload('batch', 'actor')
                runner.assert_called_once()
                status = server.upload_state('batch')
                self.assertEqual(status['state'], 'failed')
                text = status['error']
                self.assertIn('9 项阻断错误', text)
                self.assertIn('缺少答案 [MISSING]（8 项，示例单元：Q0、Q1、Q2）', text)
                self.assertIn('媒体无效 [BAD_MEDIA]', text)
                self.assertIn('24 项非阻断警告', text)
                self.assertNotIn('warning noise', text)
                self.assertEqual(status['output'], '')
                jobs.clear()
                self.assertEqual(server.upload_state('batch')['error'], text)

    def test_plain_exception_and_string_errors_remain_visible(self):
        summary = server._prepare_failure_summary({'errors': ['missing media', 'other']})
        self.assertIn('missing media', summary)
        self.assertIn('other', summary)
        with tempfile.TemporaryDirectory() as tmp, patch.object(server, 'WORKFLOWS', Path(tmp)), \
                patch.object(server, '_upload_jobs', {'batch': {}}), \
                patch.object(server, '_run_runner', return_value=SimpleNamespace(returncode=2, stdout='connection failed', stderr='')), \
                patch('workbench.ssh_tunnel.ensure_upload_connection', return_value='ready'):
            (Path(tmp) / 'batch').mkdir()
            server._run_batch_upload('batch', 'actor')
            self.assertIn('connection failed', server.upload_state('batch')['error'])


if __name__ == '__main__':
    unittest.main()
