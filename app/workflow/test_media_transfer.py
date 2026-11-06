"""Media staging uses mocked local reads and SSH; no local or remote writes."""
import base64
import hashlib
import io
import json
import os
import stat
import subprocess
import types
import unittest
from unittest.mock import patch

from workflow import media_transfer as media


class MediaTransferTests(unittest.TestCase):
    def setUp(self):
        self.patches = [patch.object(media.os, 'open', return_value=10),
                        patch.object(media.os, 'close'),
                        patch.object(media, '_read_file', side_effect=lambda root, path: path.encode()),
                        patch.object(media, '_settings', return_value=('user@example.test', '22', '')),
                        patch.object(media.subprocess, 'run', side_effect=self.ssh)]
        self.mocks = [p.start() for p in self.patches]
        self.addCleanup(self.stop_patches)
        self.requests = []

    def stop_patches(self):
        for p in reversed(self.patches):
            p.stop()

    def ssh(self, args, **kwargs):
        request = json.loads(kwargs['input'])
        self.requests.append((args, request, kwargs))
        return subprocess.CompletedProcess(args, 0, json.dumps([f['sha256'] for f in request['files']]).encode())

    def stage(self, refs, **kwargs):
        return media.stage_media('/batch', [{'path': ref, 'sha256': hashlib.sha256(ref.encode()).hexdigest()} for ref in refs], static_root=kwargs.get('root', '/srv/media'),
                                 batch_id=kwargs.get('batch_id', 'batch-one'))

    def test_mapping_hashes_extensions_and_safe_transport(self):
        mapping = self.stage(['01_source/media/image.PNG', '02_audio/clip.mp3'])
        args, request, options = self.requests[0]
        namespace = hashlib.sha256(b'batch-one').hexdigest()
        content_hash = hashlib.sha256(b'01_source/media/image.PNG').hexdigest()
        self.assertEqual(mapping['image.PNG'], '/source-media/' + namespace + '-' + content_hash + '.PNG')
        self.assertEqual(request['root'], '/srv/media')
        self.assertEqual(len(request['files']), 2)
        self.assertIn('BatchMode=yes', args)
        self.assertIn('StrictHostKeyChecking=yes', args)
        self.assertNotIn('/srv/media', args)
        self.assertIs(options['stderr'], subprocess.DEVNULL)

    def test_destination_change_fails_before_ssh(self):
        ref = 'x/a.png'
        with self.assertRaises(media.MediaTransferError):
            media.stage_media('/batch', [{'path': ref, 'sha256': hashlib.sha256(ref.encode()).hexdigest()}],
                              static_root='/srv/media', batch_id='one', expected_destination='other.example')
        self.assertEqual(self.requests, [])

    def test_batch_namespace_distinct_and_repeat_reference_deduplicated(self):
        first = self.stage(['x/a.png'])
        second = self.stage(['x/a.png'], batch_id='different')
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.requests[0][1]['files']), 1)

    def test_collisions_and_traversals_fail_before_ssh(self):
        for refs in (['x/a.png', 'y/a.png'], ['../a.png'], ['/a.png'], ['x//a.png'],
                     ['x/../a.png'], ['x\\a.png'], ['x/a.png\x00'], ['x/a']):
            with self.subTest(refs=refs), self.assertRaises(media.MediaTransferError):
                self.stage(refs)
        self.assertFalse(self.requests)

    def test_rejects_invalid_root_and_identifier(self):
        for root in ('relative', '/', '/srv/../etc', '/srv//media'):
            with self.subTest(root=root), self.assertRaises(media.MediaTransferError):
                self.stage(['x/a.png'], root=root)
        with self.assertRaises(media.MediaTransferError):
            self.stage(['x/a.png'], batch_id='')
        self.assertFalse(self.requests)

    def test_remote_failure_wrong_hash_and_timeout_fail_closed(self):
        run = self.mocks[-1]
        for response in (subprocess.CompletedProcess([], 1, b'secret error'),
                         subprocess.CompletedProcess([], 0, b'["wrong"]'),
                         subprocess.CompletedProcess([], 0, b'not json'),
                         subprocess.TimeoutExpired('ssh', 1)):
            run.side_effect = response if isinstance(response, Exception) else None
            run.return_value = response
            with self.subTest(response=response), self.assertRaises(media.MediaTransferError) as caught:
                self.stage(['x/a.png'])
            self.assertNotIn('secret', str(caught.exception))

    def test_inventory_hash_required_and_checked(self):
        for refs in (['x/a.png'], [{'path': 'x/a.png', 'sha256': '0' * 64}]):
            with self.assertRaises(media.MediaTransferError):
                media.stage_media('/batch', refs, static_root='/srv/media', batch_id='id')
        self.assertFalse(self.requests)

    def test_source_change_during_transfer_rejected(self):
        self.mocks[2].side_effect = [b'x/a.png', b'changed']
        with self.assertRaises(media.MediaTransferError):
            self.stage(['x/a.png'])

    def test_settings_precedence_and_sanitization(self):
        settings = self.patches[3].temp_original
        with patch.dict(media.os.environ, {'MUDU_UPLOAD_SSH_HOST': '-bad'}, clear=True):
            with self.assertRaises(media.MediaTransferError):
                settings()
        with patch.dict(media.os.environ, {'MUDU_UPLOAD_SSH_HOST': 'user@example.test',
                                         'MUDU_UPLOAD_SSH_PORT': '2222'}, clear=True):
            self.assertEqual(settings(), ('user@example.test', '2222', ''))


class RemoteScriptTests(unittest.TestCase):
    def execute_remote(self, *, existing=False, corrupt=False, swapped=False):
        content = b'actual media'
        digest = hashlib.sha256(content).hexdigest()
        request = {'root': '/srv/media', 'files': [{'name': 'target.png', 'sha256': digest,
                   'data': base64.b64encode(content).decode()}]}
        fake = types.ModuleType('os')
        for key in ('O_RDONLY', 'O_DIRECTORY', 'O_NOFOLLOW', 'O_CREAT', 'O_EXCL', 'O_RDWR', 'O_NONBLOCK', 'SEEK_SET'):
            setattr(fake, key, getattr(os, key))
        data = bytearray(content if existing else b'')
        position = [0]
        opens = []
        def open_file(path, flags, *args, **kwargs):
            opens.append((path, flags))
            if flags & os.O_CREAT:
                if existing:
                    raise FileExistsError()
                return 50
            return 10
        def write(fd, value):
            data.extend(bytes(value))
            return len(value)
        def read(fd, size):
            value = bytes(data[position[0]:position[0] + size])
            position[0] += len(value)
            return b'wrong' if corrupt and value else value
        def entry(*args, **kwargs):
            return types.SimpleNamespace(st_mode=stat.S_IFREG, st_dev=1, st_ino=2 if swapped else 1,
                                         st_size=len(data), st_mtime_ns=1, st_ctime_ns=1)
        fake.open = open_file
        fake.close = lambda fd: None
        fake.write = write
        fake.read = read
        fake.fsync = lambda fd: None
        fake.lseek = lambda fd, pos, whence: position.__setitem__(0, pos)
        fake.fstat = lambda fd: types.SimpleNamespace(st_mode=stat.S_IFREG, st_dev=1, st_ino=1,
                                                     st_size=len(data), st_mtime_ns=1, st_ctime_ns=1)
        fake.stat = entry
        output = io.StringIO()
        import sys
        with patch.dict(sys.modules, {'os': fake}), patch.object(sys, 'stdin', io.StringIO(json.dumps(request))), \
             patch.object(sys, 'stdout', output):
            try:
                exec(media._REMOTE, {})
                succeeded = True
            except SystemExit:
                succeeded = False
        return succeeded, output.getvalue(), opens, digest

    def test_remote_exclusive_creation_and_readback(self):
        success, output, opens, digest = self.execute_remote()
        self.assertTrue(success)
        self.assertEqual(json.loads(output), [digest])
        flags = opens[-1][1]
        self.assertTrue(flags & os.O_EXCL)
        self.assertTrue(flags & os.O_NOFOLLOW)
        self.assertTrue(all(flags & os.O_NOFOLLOW for _, flags in opens))

    def test_existing_content_is_verified_without_overwrite(self):
        success, output, opens, digest = self.execute_remote(existing=True)
        self.assertTrue(success)
        self.assertEqual(json.loads(output), [digest])
        self.assertFalse(opens[-1][1] & os.O_CREAT)

    def test_remote_races_and_different_bytes_fail(self):
        for kwargs in ({'corrupt': True}, {'swapped': True}, {'existing': True, 'corrupt': True}):
            with self.subTest(kwargs=kwargs):
                success, output, _, _ = self.execute_remote(**kwargs)
                self.assertFalse(success)
                self.assertEqual(output, '')


if __name__ == '__main__':
    unittest.main()
