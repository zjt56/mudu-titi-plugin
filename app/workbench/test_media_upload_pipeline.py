import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from workbench import server


class MediaUploadPipelineTests(unittest.TestCase):
    def setUp(self):
        check = patch.object(server, '_check_upload_preconditions')
        check.start()
        self.addCleanup(check.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.batch = self.root / 'batch'
        (self.batch / '06_batch').mkdir(parents=True)
        (self.batch / '01_source' / 'media').mkdir(parents=True)
        (self.batch / '07_upload').mkdir()
        self.source = self.batch / '06_batch' / 'batch_meta.json'
        self.source.write_text(json.dumps({'batch_id': 'batch-id', 'items': [{'attachments': [
            {'kind': 'image', 'url': '/source-media/batch/a.png'}]}]}))
        (self.batch / '01_source' / 'media' / 'a.png').write_bytes(b'image')
        (self.batch / '01_source' / 'media' / 'unused.png').write_bytes(b'unused')
        self.mapping = {'a.png': '/source-media/verified.png'}

    def test_stages_only_referenced_and_atomically_persists(self):
        with patch('workflow.media_transfer._settings', return_value=('ubuntu@101.43.1.57', '22', '')), \
             patch('workflow.media_transfer.stage_media', return_value=self.mapping) as stage:
            mapping, digest = server._stage_upload_media(self.batch)
        self.assertEqual(mapping, self.mapping)
        self.assertEqual(digest, hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(stage.call_args.args[1], [{'path': '01_source/media/a.png', 'sha256': hashlib.sha256(b'image').hexdigest()}])
        self.assertEqual(json.loads((self.batch / '07_upload' / 'media-map.json').read_text()), self.mapping)

    def test_failure_or_changed_metadata_preserves_existing_map(self):
        target = self.batch / '07_upload' / 'media-map.json'
        target.write_text('{"old":"value"}')
        def changed(*args, **kwargs):
            self.source.write_text('{}')
            return self.mapping
        with patch('workflow.media_transfer._settings', return_value=('ubuntu@101.43.1.57', '22', '')), \
             patch('workflow.media_transfer.stage_media', side_effect=changed):
            with self.assertRaisesRegex(ValueError, '发生变化'):
                server._stage_upload_media(self.batch)
        self.assertEqual(target.read_text(), '{"old":"value"}')

    def test_unknown_destination_fails_before_transfer(self):
        with patch('workflow.media_transfer._settings', return_value=('other@example.com', '22', '')), \
             patch('workflow.media_transfer.stage_media') as stage:
            with self.assertRaisesRegex(ValueError, '没有经过验证'):
                server._stage_upload_media(self.batch)
            stage.assert_not_called()

    def test_prepare_uses_staged_map_then_mark_then_submit(self):
        events = []
        raw_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        def prepare(folder, **kwargs):
            events.append(('prepare', kwargs['media_map']))
            return {'passed': True, 'source_sha256': raw_hash}
        def runner(args):
            events.append((args[0], None))
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        jobs = {'batch': {'running': True}}
        with patch.object(server, 'WORKFLOWS', self.root), patch.object(server, '_upload_jobs', jobs), \
             patch('workbench.ssh_tunnel.ensure_upload_connection', return_value='ready'), \
             patch.object(server, '_stage_upload_media', side_effect=lambda folder: (events.append(('stage', None)) or (self.mapping, raw_hash))), \
             patch('workflow.upload.prepare', side_effect=prepare), patch.object(server, '_run_runner', side_effect=runner):
            server._run_batch_upload('batch', 'actor')
        self.assertEqual(events, [('stage', None), ('prepare', self.mapping), ('mark', None), ('upload', None)])
        self.assertTrue(jobs['batch']['ok'])

    def test_transfer_failure_stops_prepare_and_preserves_map(self):
        target = self.batch / '07_upload' / 'media-map.json'
        target.write_text('{"old":"value"}')
        jobs = {'batch': {'running': True}}
        with patch.object(server, 'WORKFLOWS', self.root), patch.object(server, '_upload_jobs', jobs), \
             patch('workbench.ssh_tunnel.ensure_upload_connection', return_value='ready'), \
             patch('workflow.media_transfer._settings', return_value=('ubuntu@101.43.1.57', '22', '')), \
             patch('workflow.media_transfer.stage_media', side_effect=ValueError('transfer failed')), \
             patch('workflow.upload.prepare') as prepare, patch.object(server, '_run_runner') as runner:
            server._run_batch_upload('batch', 'actor')
        self.assertEqual(target.read_text(), '{"old":"value"}')
        self.assertFalse(jobs['batch']['ok'])
        prepare.assert_not_called()
        runner.assert_not_called()

    def test_unreviewed_confirmation_is_forwarded(self):
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        jobs = {'batch': {'running': True}}
        with patch.object(server, 'WORKFLOWS', self.root), patch.object(server, '_upload_jobs', jobs), \
             patch('workbench.ssh_tunnel.ensure_upload_connection', return_value='ready'), \
             patch.object(server, '_stage_upload_media', return_value=(self.mapping, digest)), \
             patch('workflow.upload.prepare', return_value={'passed': True, 'source_sha256': digest}), \
             patch.object(server, '_run_runner', return_value=SimpleNamespace(returncode=0, stdout='', stderr='')) as runner:
            server._run_batch_upload('batch', 'actor', 'revision', 'token')
        mark, submit = [call.args[0] for call in runner.call_args_list]
        self.assertEqual(mark[-4:], ['--unreviewed-revision', 'revision', '--unreviewed-attempt', 'token'])
        self.assertEqual(submit[-2:], ['--unreviewed-attempt', 'token'])

    def test_failed_preflight_stops_mark_and_submit(self):
        jobs = {'batch': {'running': True}}
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        with patch.object(server, 'WORKFLOWS', self.root), patch.object(server, '_upload_jobs', jobs), \
             patch('workbench.ssh_tunnel.ensure_upload_connection', return_value='ready'), \
             patch.object(server, '_stage_upload_media', return_value=(self.mapping, digest)), \
             patch('workflow.upload.prepare', return_value={'passed': False, 'source_sha256': digest, 'errors': ['blocked']}), \
             patch.object(server, '_run_runner') as runner:
            server._run_batch_upload('batch', 'actor')
        runner.assert_not_called()
        self.assertFalse(jobs['batch']['ok'])


class UploadPreconditionTests(unittest.TestCase):
    def test_changed_confirmation_blocks_before_media_transfer(self):
        from workflow import manifest, runner
        with patch.object(manifest, 'load', return_value={'stages': {}}), \
             patch.object(runner, 'unreviewed_summary', return_value={'passed': False, 'revision': 'new'}):
            with self.assertRaisesRegex(ValueError, '审核状态已变化'):
                server._check_upload_preconditions(Path('/batch'), 'old', 'token')


if __name__ == '__main__':
    unittest.main()
