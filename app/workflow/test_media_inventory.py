import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

from workflow.media_inventory import MediaInventoryError, referenced_media


class MediaInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(os.path.realpath(self.temp.name)) / '批次 A'
        self.root.mkdir()
        self.prefix = '/source-media/' + quote(self.root.name, safe='') + '/'

    def put(self, relative, data=b'content'):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def attachment(self, filename, kind='image', **extra):
        return {'url': self.prefix + quote(filename, safe=''), 'kind': kind, **extra}

    def test_nested_references_are_unique_and_only_referenced_files_selected(self):
        self.put('01_source/media/a.png')
        self.put('01_source/media/unused.png')
        self.put('02_audio/b.mp3')
        attachment = self.attachment('a.png', name='a.png')
        metadata = {'items': [{'attachments': [attachment], 'subquestions': [
            {'attachments': [attachment, self.attachment('b.mp3', 'audio')]}]}]}
        self.assertEqual(referenced_media(self.root, metadata),
                         [{'path': '01_source/media/a.png', 'sha256': hashlib.sha256(b'content').hexdigest()},
                          {'path': '02_audio/b.mp3', 'sha256': hashlib.sha256(b'content').hexdigest()}])

    def test_duplicate_names_require_identical_content(self):
        self.put('01_source/media/a.png', b'one')
        self.put('01_source_ans/media/a.png', b'one')
        meta = {'attachments': [self.attachment('a.png')]}
        with self.assertRaises(MediaInventoryError):
            referenced_media(self.root, meta)
        self.put('01_source_ans/media/a.png', b'two')
        with self.assertRaises(MediaInventoryError):
            referenced_media(self.root, meta)

    def test_foreign_or_noncanonical_urls_are_rejected(self):
        self.put('01_source/media/a.png')
        for url in ('https://example.com' + self.prefix + 'a.png',
                    '/source-media/other/a.png', '/source-media/a.png',
                    self.prefix + '../a.png', self.prefix + '%2e%2e%2fa.png',
                    self.prefix + 'a.png?x=1', self.prefix + 'a.png#x',
                    self.prefix + '%61.png', self.prefix + 'a%2F.png',
                    self.prefix + '%252e%252e', self.prefix + 'a%5C.png'):
            with self.subTest(url=url), self.assertRaises(MediaInventoryError):
                referenced_media(self.root, {'attachments': [{'url': url, 'kind': 'image'}]})

    def test_missing_mismatched_and_malformed_attachments_rejected(self):
        self.put('01_source/media/a.png')
        for attachment in (self.attachment('missing.png'), self.attachment('a.png', 'audio'),
                           self.attachment('a.png', []), {'kind': 'image'}, 'a.png'):
            with self.subTest(attachment=attachment), self.assertRaises(MediaInventoryError):
                referenced_media(self.root, {'attachments': [attachment]})
        with self.assertRaises(MediaInventoryError):
            referenced_media(self.root, {'attachments': {}})

    def test_symlink_file_and_directory_rejected(self):
        external = Path(self.temp.name) / 'outside'
        external.mkdir()
        (external / 'a.png').write_bytes(b'outside')
        directory = self.root / '01_source/media'
        directory.mkdir(parents=True)
        (directory / 'a.png').symlink_to(external / 'a.png')
        meta = {'attachments': [self.attachment('a.png')]}
        with self.assertRaises(MediaInventoryError):
            referenced_media(self.root, meta)
        (directory / 'a.png').unlink()
        directory.rmdir()
        directory.symlink_to(external, target_is_directory=True)
        with self.assertRaises(MediaInventoryError):
            referenced_media(self.root, meta)

    def test_display_name_and_image_marker(self):
        self.put('01_source/media/a.png')
        result = referenced_media(self.root, {'attachments': [self.attachment('a.png', name='Caption')],
                                               'stem': '[IMG a.png]'})
        self.assertEqual(result[0]['path'], '01_source/media/a.png')
        with self.assertRaises(MediaInventoryError):
            referenced_media(self.root, {'stem': '[IMG ../a.png]'})

    def test_root_symlink_ancestor_and_large_file_rejected(self):
        self.put('01_source/media/a.png')
        link = self.root.parent / 'alias'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(MediaInventoryError):
            referenced_media(link, {'attachments': []})
        with open(self.root / '01_source/media/a.png', 'wb') as f:
            f.truncate(8 * 1024 * 1024 + 1)
        with self.assertRaises(MediaInventoryError):
            referenced_media(self.root, {'attachments': [self.attachment('a.png')]})

    def test_empty_attachments(self):
        self.assertEqual(referenced_media(self.root, {'items': [{'attachments': []}]}), [])


if __name__ == '__main__':
    unittest.main()
