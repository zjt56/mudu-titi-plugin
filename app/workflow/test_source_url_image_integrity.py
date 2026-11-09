"""Regression coverage for source URLs and image-only passage labels."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

from workflow.agent_tasks import _sanitized
from workflow.upload_content import inspect_content


class SourceUrlImageIntegrityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script = Path(__file__).resolve().parents[1] / 'scripts/06-upload/audit_batch_integrity.py'
        spec = importlib.util.spec_from_file_location('url_image_integrity_audit', script)
        cls.audit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.audit)

    def test_url_survives_context_and_strict_payload_source_binding(self):
        explanation = ('推理判断题。根据最后一句“Follow us on '
                       'https://justforfun.com/artofliving for more.”可知,本文可能来自一个网站。')
        with tempfile.TemporaryDirectory() as folder:
            batch = Path(folder)
            (batch / '01_source').mkdir()
            (batch / '01_source/正文.txt').write_text('Ⅷ.阅读理解\n58. Where?\n', encoding='utf-8')
            (batch / '01_source/附录.txt').write_text(
                'Ⅷ.阅读理解\n58.C 【解析】' + explanation + '\n', encoding='utf-8')
            payload = _sanitized({'items': [{'number': 58, 'explanation': explanation}]})
            self.assertEqual(payload['items'][0]['explanation'], explanation)
            self.assertTrue(inspect_content(batch, payload, strict=True)['passed'])
            payload['items'][0]['explanation'] = explanation.replace('artofliving', 'another-page')
            self.assertFalse(inspect_content(batch, payload, strict=True)['passed'])

    def test_urls_with_path_names_survive_but_real_host_paths_are_redacted(self):
        for url in ('http://example.com/home/test', 'https://example.com/Users/test',
                    'https://example.com/tmp/test', 'https://example.com/private/test'):
            self.assertEqual(_sanitized(url), url)
        for path in ('C:\\Users\\person\\paper.docx', 'D:/papers/exam.docx',
                     '/home/person/paper.docx', '/Users/person/paper.docx', '/tmp/private.docx'):
            self.assertEqual(_sanitized('source: ' + path), 'source: [host-path-redacted]')
        self.assertEqual(_sanitized({'token': 'hidden', 'text': 'Bearer abc'}),
                         {'text': '[credential-redacted]'})

    def test_image_section_label_is_not_a_contained_passage(self):
        label = {'unit_id': 'image-a', 'material': 'A\n', 'attachments': [
            {'kind': 'image', 'placement': 'question', 'url': '/source-media/image10.jpeg'}]}
        next_passage = {'unit_id': 'text-b', 'material': 'Another long original passage with enough text.'}
        self.assertEqual(self.audit._check_adjacent_materials([label, next_passage]), [])
        # A text-only short material remains subject to the existing check.
        self.assertTrue(self.audit._check_adjacent_materials([
            dict(label, attachments=[]), next_passage]))
        # An image attachment must not exempt real duplicated passage text.
        passage = 'This is the shared original reading passage.'
        self.assertTrue(self.audit._check_adjacent_materials([
            dict(label, material=passage),
            dict(next_passage, material=passage + ' incorrectly copied additional next passage text')]))


if __name__ == '__main__':
    unittest.main()
