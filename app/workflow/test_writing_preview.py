"""Unnumbered writing must display via its explicit source section, not guesses."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import extract, preview
from workbench import server


class WritingPreviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.batch = self.root / 'paper'
        source = self.batch / '01_source'
        source.mkdir(parents=True)
        text = '\n'.join([
            '2022年安徽省初中学业水平考试',
            'Ⅸ.单词拼写(共1小题)', '80.May the world be full of peace.',
            'Ⅹ.书面表达(共1小题;满分20分)',
            '假定你是李辉。请给Mike推荐《西游记》。要点如下:',
            '1.团队合作;', '2.追逐梦想。', '注意:词数80~100。',
            'Dear Mike,', 'Since you want to read Chinese classic works,',
            '<u>　</u>', 'Yours,', 'Li Hui',
            '2022年安徽省初中学业水平考试',
        ])
        structure, _, _ = extract.infer_structure(text)
        (source / '正文.txt').write_text(text, encoding='utf-8')
        (source / 'structure.json').write_text(json.dumps(structure, ensure_ascii=False), encoding='utf-8')
        self.lines = text.splitlines()

    def test_explicit_writing_section_shows_complete_prompt(self):
        text = '\n'.join(preview.source_slice(self.batch, [81]))
        for phrase in ('假定你是李辉', '1.团队合作', '2.追逐梦想', '80~100', 'Dear Mike,', 'Yours,', 'Li Hui'):
            self.assertIn(phrase, text)
        self.assertNotIn('初中学业水平考试', text)
        self.assertNotIn('80.May', text)
        self.assertEqual(text.splitlines()[-1], 'Li Hui')

    def test_no_arbitrary_future_question_fallback(self):
        self.assertEqual(preview.slice_text(self.lines, 81), [])
        self.assertEqual(preview.source_slice(self.batch, [82]), [])
        self.assertEqual(preview.source_slice(self.batch, [999]), [])

    def test_unbound_or_missing_section_does_not_guess_writing(self):
        path = self.batch / '01_source/structure.json'
        path.write_text('{"sections":[]}', encoding='utf-8')
        self.assertEqual(preview.source_slice(self.batch, [81]), [])

    def test_detail_uses_source_binding_even_when_confirmed_stem_is_stale(self):
        final = self.batch / '06_batch'
        final.mkdir()
        (final / 'batch_meta.json').write_text(json.dumps({'items': [{
            'id': 'q-81', 'unit_kind': 'standalone', 'number': 81, 'numbers': [81],
            'question_type': '书面表达', 'stem': '作文候选题干', 'answer': '参考范文',
            'subquestions': [], 'knowledge': [],
        }]}), encoding='utf-8')
        (final / 'batch.json').write_bytes((final / 'batch_meta.json').read_bytes())
        (self.batch / '01_source/structure.confirmed.json').write_text(
            '{"units":[{"numbers":[81],"stem":"stale wrong content"}]}', encoding='utf-8')
        with patch.object(server, 'WORKFLOWS', self.root):
            result = server.detail('paper', 'q-81')
        self.assertIn('假定你是李辉', result['source_preview'])
        self.assertIn('Li Hui', result['source_preview'])
        self.assertTrue(result['preview_blocks'])
        self.assertNotIn('stale wrong content', result['source_preview'])
        self.assertEqual(result['subquestions'][0]['stem'], '作文候选题干')
        self.assertEqual(result['subquestions'][0]['answer'], '参考范文')


if __name__ == '__main__':
    unittest.main()
