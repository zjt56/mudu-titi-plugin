"""Media staging uses mocked SSH and isolated local filesystem tests."""
import base64
import hashlib
import json
import os
import stat
import subprocess
import tempfile
import sys
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
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)
        self.target = os.path.join(self.root, 'target.png')
        self.content = b'actual media'
        self.digest = hashlib.sha256(self.content).hexdigest()

    def run_script(self, setup='', root=None):
        request = {'root': root or self.root, 'files': [{'name': 'target.png', 'sha256': self.digest,
                   'data': base64.b64encode(self.content).decode()}]}
        return subprocess.run([sys.executable, '-c', 'import os\n' + setup + '\n' + media._REMOTE],
                              input=json.dumps(request).encode(), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, check=False)

    def assert_clean(self):
        self.assertFalse(any(n.startswith('.media-transfer-') for n in os.listdir(self.root)))

    def test_atomic_publication_with_partial_writes(self):
        setup = """
real_write = os.write
def checked(fd, data):
    assert not os.path.exists(%r)
    assert os.fstat(fd).st_mode & 0o777 == 0o600
    return real_write(fd, data[:3])
os.write = checked
""" % self.target
        result = self.run_script(setup)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [self.digest])
        with open(self.target, 'rb') as f:
            self.assertEqual(f.read(), self.content)
        self.assertEqual(stat.S_IMODE(os.stat(self.target).st_mode), 0o644)
        self.assert_clean()

    def test_existing_content_verified_without_overwrite(self):
        with open(self.target, 'wb') as f:
            f.write(self.content)
        inode = os.stat(self.target).st_ino
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual(os.stat(self.target).st_ino, inode)
        with open(self.target, 'wb') as f:
            f.write(b'different')
        self.assertNotEqual(self.run_script().returncode, 0)
        with open(self.target, 'rb') as f:
            self.assertEqual(f.read(), b'different')
        self.assert_clean()

    def test_failed_write_and_corrupt_read_cleanup(self):
        for setup in ("""
real_write = os.write
def failed(fd, data):
    real_write(fd, data[:3])
    raise OSError('failure')
os.write = failed
""", """
real_read = os.read
def corrupt(fd, size):
    data = real_read(fd, size)
    return b'bad' if data else data
os.read = corrupt
"""):
            with self.subTest(setup=setup):
                self.assertNotEqual(self.run_script(setup).returncode, 0)
                self.assertFalse(os.path.lexists(self.target))
                self.assert_clean()

    def test_symlink_target_rejected(self):
        os.symlink('missing', self.target)
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertTrue(os.path.islink(self.target))
        self.assert_clean()

    def test_disappearing_competing_target_retried(self):
        setup = """
real_link = os.link
calls = [0]
def race(src, dst, **kwargs):
    calls[0] += 1
    if calls[0] == 1:
        raise FileExistsError()
    return real_link(src, dst, **kwargs)
os.link = race
"""
        result = self.run_script(setup)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_clean()

    def test_directory_symlink_and_traversal_rejected(self):
        link = os.path.join(self.root, 'alias')
        os.symlink(self.root, link)
        for root in (link, self.root + '/../' + os.path.basename(self.root)):
            self.assertNotEqual(self.run_script(root=root).returncode, 0)
        self.assert_clean()


if __name__ == '__main__':
    unittest.main()
