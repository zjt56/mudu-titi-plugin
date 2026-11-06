import os
import json
import tempfile
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from workbench import server, ssh_tunnel


class TunnelTests(unittest.TestCase):
    def setUp(self):
        check = patch.object(server, '_check_upload_preconditions')
        check.start()
        self.addCleanup(check.stop)
        self.processes = patch.object(ssh_tunnel, '_processes', {})
        self.processes.start()
        self.addCleanup(self.processes.stop)
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.config = patch.object(ssh_tunnel.upload_client, 'load_config', return_value={'base_url': 'http://127.0.0.1:8089/api'})
        self.config.start()
        self.addCleanup(self.config.stop)

    @patch.object(ssh_tunnel.subprocess, 'Popen')
    def test_https_and_existing_listener_skip_ssh(self, popen):
        ssh_tunnel.upload_client.load_config.return_value = {'base_url': 'https://example.test/api'}
        ssh_tunnel.ensure_upload_connection()
        ssh_tunnel.upload_client.load_config.return_value = {'base_url': 'http://127.0.0.1:8089/api'}
        with patch.object(ssh_tunnel, '_listening', return_value=True):
            ssh_tunnel.ensure_upload_connection()
        popen.assert_not_called()

    @patch.object(ssh_tunnel, '_listening', return_value=False)
    @patch.object(ssh_tunnel.subprocess, 'Popen')
    def test_invalid_port_fails_without_start(self, popen, listening):
        # 未配置 host 时使用内置服务器，不再要求用户填写 SSH 登录地址。
        os.environ.update(MUDU_UPLOAD_SSH_HOST='user@example.test', MUDU_UPLOAD_SSH_PORT='-1')
        with self.assertRaisesRegex(ssh_tunnel.TunnelError, 'SSH 端口'):
            ssh_tunnel.ensure_upload_connection()
        popen.assert_not_called()

    @patch.object(ssh_tunnel, '_listening', side_effect=[False, True])
    @patch.object(ssh_tunnel.subprocess, 'Popen')
    def test_start_and_wait_with_noninteractive_arguments(self, popen, listening):
        with tempfile.NamedTemporaryFile() as identity:
            os.environ.update(MUDU_UPLOAD_SSH_HOST='user@example.test', MUDU_UPLOAD_SSH_PORT='2222',
                              MUDU_UPLOAD_SSH_REMOTE_PORT='90', MUDU_UPLOAD_SSH_IDENTITY=identity.name)
            process = popen.return_value
            process.poll.return_value = None
            self.assertIn('就绪', ssh_tunnel.ensure_upload_connection())
            args = popen.call_args.args[0]
        self.assertEqual(args[0], 'ssh')
        for value in ('-N', 'BatchMode=yes', 'ExitOnForwardFailure=yes', '2222',
                      '127.0.0.1:8089:127.0.0.1:90', identity.name, 'user@example.test'):
            self.assertIn(value, args)
            self.assertEqual(popen.call_args.kwargs['stdin'], subprocess.DEVNULL)
            self.assertEqual(popen.call_args.kwargs['stderr'], subprocess.DEVNULL)
            self.assertNotIn('shell', popen.call_args.kwargs)
            self.assertIs(ssh_tunnel._processes[('127.0.0.1', 8089)], process)

    @patch.object(ssh_tunnel, '_listening', side_effect=[False, True])
    @patch.object(ssh_tunnel.subprocess, 'Popen')
    def test_saved_settings_are_read_at_click_time(self, popen, listening):
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8') as saved:
            json.dump({'ssh_host': 'user@new.example', 'ssh_port': 2222, 'ssh_remote_port': 90}, saved)
            saved.flush()
            os.environ['MUDU_UPLOAD_SSH_CONFIG'] = saved.name
            popen.return_value.poll.return_value = None
            ssh_tunnel.ensure_upload_connection()
        self.assertIn('user@new.example', popen.call_args.args[0])
        self.assertIn('2222', popen.call_args.args[0])

    @patch.object(ssh_tunnel, '_listening', return_value=False)
    @patch.object(ssh_tunnel.subprocess, 'Popen')
    def test_failed_or_timed_out_process_is_cleared(self, popen, listening):
        os.environ['MUDU_UPLOAD_SSH_HOST'] = 'example.test'
        process = popen.return_value
        process.poll.return_value = 255
        with self.assertRaisesRegex(ssh_tunnel.TunnelError, '启动失败'):
            ssh_tunnel.ensure_upload_connection()
        self.assertFalse(ssh_tunnel._processes)
        process.poll.return_value = None
        with self.assertRaisesRegex(ssh_tunnel.TunnelError, '超时'):
            ssh_tunnel.ensure_upload_connection(timeout=0)
        process.terminate.assert_called_once()
        self.assertFalse(ssh_tunnel._processes)

    def test_upload_stops_before_runner_on_tunnel_failure(self):
        jobs = {'batch': {'running': True, 'output': '', 'error': ''}}
        with patch.object(server, '_upload_jobs', jobs), patch.object(server, 'batch_dir', return_value='batch'), \
             patch.object(ssh_tunnel, 'ensure_upload_connection', side_effect=ssh_tunnel.TunnelError('请配置 MUDU_UPLOAD_SSH_HOST')), \
             patch.object(server, '_run_runner') as run:
            server._run_batch_upload('batch', 'actor')
        run.assert_not_called()
        self.assertFalse(jobs['batch']['running'])
        self.assertFalse(jobs['batch']['ok'])
        self.assertIn('MUDU_UPLOAD_SSH_HOST', jobs['batch']['error'])
        self.assertEqual(jobs['batch']['step'], '检查连接，必要时启动 SSH 隧道')

    def test_upload_prepares_only_after_connection(self):
        jobs = {'batch': {'running': True, 'output': '', 'error': ''}}
        failure = SimpleNamespace(returncode=1, stdout='', stderr='failed')
        with patch.object(server, '_upload_jobs', jobs), patch.object(server, 'batch_dir', return_value='batch'), \
             patch.object(ssh_tunnel, 'ensure_upload_connection', return_value='ready') as ensure, \
             patch.object(server, '_stage_upload_media', return_value=None), \
             patch.object(server, '_run_runner', return_value=failure) as run:
            server._run_batch_upload('batch', 'actor')
        ensure.assert_called_once()
        self.assertEqual(run.call_args.args[0][0], 'prepare-upload')
        self.assertEqual(jobs['batch']['step'], '准备上传（冻结请求）')
