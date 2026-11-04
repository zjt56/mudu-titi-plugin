"""Review must follow source dialogue partitions even with an obsolete batch."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import extract, preview
from workbench import server


class DialogueWorkbenchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.batch = self.root / 'paper'
        source = self.batch / '01_source'
        source.mkdir(parents=True)
        lines = ['2022年安徽省初中学业水平考试', 'Ⅱ.长对话理解(共5小题)',
                 '听下面一段对话,回答第6、7题。']
        for n in (6, 7):
            lines += [f'{n}.First dialogue question {n}?', 'A.First. B.Second. C.Third.']
        lines += ['听下面一段对话,回答第8至10题。']
        for n in (8, 9, 10):
            lines += [f'{n}.Second dialogue question {n}?', 'A.New first. B.New second. C.New third.']
        text = '\n'.join(lines)
        structure, _, _ = extract.infer_structure(text)
        (source / '正文.txt').write_text(text, encoding='utf-8')
        (source / 'structure.json').write_text(json.dumps(structure, ensure_ascii=False), encoding='utf-8')
        final = self.batch / '06_batch'
        final.mkdir()
        self.old = {'items': [{'id': 'group-6-7-8-9-10', 'unit_kind': 'group',
            'number': 6, 'numbers': [6, 7, 8, 9, 10], 'question_type': '听力长对话理解',
            'material': 'Both conversations merged together', 'audio': 'old-combined.mp3',
            'filterMetadata': {'subtypes': ['选择5题']}, 'knowledge': [],
            'subquestions': [{'number': n, 'stem': 'outdated', 'answer': 'B', 'options': [],
                              'audio': 'old-combined.mp3'} for n in range(6, 11)]}]}
        for name in ('batch.json', 'batch_meta.json'):
            (final / name).write_text(json.dumps(self.old), encoding='utf-8')

    def test_list_splits_two_dialogues_and_drops_old_review_claims(self):
        original = (self.batch / '06_batch/batch.json').read_bytes()
        rows = preview.batch_rows(self.batch, [{'number': str(n), 'state': 'ok', 'rounds': 3,
                                               'verdict': 'passed'} for n in range(6, 11)])
        self.assertEqual([r['numbers'] for r in rows], [[6, 7], [8, 9, 10]])
        self.assertEqual([r['unit_id'] for r in rows], ['group-6-7', 'group-8-9-10'])
        self.assertTrue(all(r['state'] == 'unverified' and r['rounds'] == 0 for r in rows))
        self.assertEqual((self.batch / '06_batch/batch.json').read_bytes(), original)

    def test_each_detail_has_only_its_own_questions_and_source_preview(self):
        with patch.object(server, 'WORKFLOWS', self.root):
            first = server.detail('paper', 'group-6-7')
            second = server.detail('paper', 'group-8-9-10')
            old_link = server.detail('paper', 'group-6-7-8-9-10')
        self.assertEqual(first['numbers'], [6, 7])
        self.assertEqual(second['numbers'], [8, 9, 10])
        self.assertEqual([s['number'] for s in first['subquestions']], [6, 7])
        self.assertEqual([s['number'] for s in second['subquestions']], [8, 9, 10])
        self.assertNotIn('8.Second', first['source_preview'])
        self.assertNotIn('6.First', second['source_preview'])
        self.assertTrue(all(len(s['options']) == 3 for d in (first, second) for s in d['subquestions']))
        self.assertTrue(all('outdated' not in s['stem'] for d in (first, second) for s in d['subquestions']))
        self.assertEqual(first['labels']['subtypes'], ['选择2题'])
        self.assertEqual(second['labels']['subtypes'], ['选择3题'])
        self.assertNotIn('audio', first['item'])
        self.assertTrue(all('audio' not in s for s in second['item']['subquestions']))
        self.assertTrue(first['label_warnings'])
        self.assertIn(old_link['numbers'], ([6, 7], [8, 9, 10]))

    def test_no_explicit_source_partition_keeps_original_unit(self):
        (self.batch / '01_source/structure.json').write_text('{"sections":[]}', encoding='utf-8')
        self.assertEqual(preview.source_group_candidates(self.batch, self.old['items']), self.old['items'])


if __name__ == '__main__':
    unittest.main()
