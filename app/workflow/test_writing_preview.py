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

    def _writing_detail(self, body):
        title = 'Ⅹ.书面表达(共1小题;满分20分)'
        source = self.batch / '01_source'
        text = '\n'.join(['2022年安徽省初中学业水平考试',
                          'Ⅸ.单词拼写(共1小题)', '80.May the world be full of peace.',
                          title, *body])
        structure, _, _ = extract.infer_structure(text)
        (source / '正文.txt').write_text(text, encoding='utf-8')
        (source / 'structure.json').write_text(json.dumps(structure, ensure_ascii=False), encoding='utf-8')
        final = self.batch / '06_batch'
        final.mkdir(exist_ok=True)
        data = {'items': [{'id': 'q-81', 'unit_id': 'q-81', 'unit_kind': 'standalone',
                          'number': 81, 'numbers': [81], 'question_type': '书面表达',
                          'stem': '\n'.join(body), 'material': '', 'answer': '参考范文',
                          'subquestions': [], 'knowledge': []}]}
        for name in ('batch.json', 'batch_meta.json'):
            (final / name).write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        tracked = [source / '正文.txt', source / 'structure.json',
                   final / 'batch.json', final / 'batch_meta.json']
        before = {p: p.read_bytes() for p in tracked}
        lines = text.splitlines()
        chunk = preview.source_slice(self.batch, [81], lines)
        self.assertTrue(chunk)
        self.assertEqual(preview.section_leadin(lines, [81], chunk[0]), [title])
        with patch.object(server, 'WORKFLOWS', self.root):
            result = server.detail('paper', 'q-81')
        self.assertEqual(result['source_preview'], '\n'.join([title, *chunk]))
        self.assertEqual(result['item']['stem'], '\n'.join(body))
        self.assertEqual({p: p.read_bytes() for p in tracked}, before)
        return result

    def test_bullet_writing_prompt_and_requirements_appear_once_through_detail(self):
        body = ['假定你是李华，请根据提示给朋友回信。',
                '●点赞：赞同、喜爱', '●任性：随心所欲',
                '●秒杀：快速达到目的', '注意：词数80~100。', 'Dear Peter,', 'Yours,', 'Li Hua']
        result = self._writing_detail(body)
        for line in body:
            self.assertEqual(result['source_preview'].splitlines().count(line), 1)
            self.assertEqual(sum(b.get('text') == line for b in result['preview_blocks']), 1)

    def test_writing_image_and_notes_appear_once_through_detail(self):
        media = self.batch / '01_source/media'
        media.mkdir()
        # A local image is enough for URL ownership; no OCR or model is invoked.
        (media / 'writing.png').write_bytes(b'writing-preview-fixture')
        for body in (
                ['请根据下图写一篇短文。', '[IMG writing.png]', '要点：',
                 '1.活动目的；', '2.你的体会。', '注意：词数80~100。'],
                ['**请根据图示简述制作海报的经历。**', '**[IMG writing.png]**',
                 '**注意：短文须包含上图所有信息。**', '**词数80~100。**']):
            with self.subTest(body=body):
                result = self._writing_detail(body)
                self.assertEqual(result['source_preview'].count('[IMG writing.png]'), 1)
                images = [b for b in result['preview_blocks'] if b.get('type') == 'image']
                self.assertEqual([b['name'] for b in images], ['writing.png'])
                for line in (body[0], *body[2:]):
                    self.assertEqual(sum(b.get('text') == line for b in result['preview_blocks']), 1)

    def test_genuine_repetition_in_writing_source_is_preserved(self):
        body = ['请写一封邮件。', '注意：不要出现真实姓名。',
                'Dear Mike,', '注意：不要出现真实姓名。', 'Yours,', 'Li Hui']
        result = self._writing_detail(body)
        self.assertEqual(result['source_preview'].count('注意：不要出现真实姓名。'), 2)

    def test_numbered_question_keeps_section_title_and_listening_instructions(self):
        lines = ['Ⅱ.短对话理解(共2小题)', '你将听到两段对话，每段对话读两遍。',
                 '1.What does he want?', 'A. ruler B. pen',
                 '2.Where is he?', 'A. school B. home']
        for number, first in ((1, lines[2]), (2, lines[4])):
            self.assertEqual(preview.section_leadin(lines, [number], first), lines[:2])

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
