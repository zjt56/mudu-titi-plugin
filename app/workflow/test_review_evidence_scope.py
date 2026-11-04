"""Review evidence must belong to its confirmed target, including shared material."""
import json
import tempfile
import unittest
from pathlib import Path

from workflow import review


class ReviewEvidenceScopeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        self.source = '2017 examination\nShared passage about a library.\n1. Read the story.\n2. Name the book.\n3. Different question here.\n'
        self.save('01_source/正文.txt', self.source)
        self.save('01_source/structure.confirmed.json', {'units': [
            {'unit_id': 'group', 'unit_kind': 'group', 'numbers': [1, 2],
             'stem': 'Shared passage about a library.\n1. Read the story.\n2. Name the book.'},
            {'unit_id': 'other', 'unit_kind': 'standalone', 'numbers': [3],
             'stem': '3. Different question here.'}]})
        self.save('03_types/types.json', {'items': [
            {'unit_id': uid, 'type': '阅读理解', 'subtypes': ['选择5题']} for uid in ('group', 'other')]})
        self.plan = {'input_binding': 'binding', 'input_hashes': {'01_source/正文.txt': 'bound'},
                     'units': ['group', 'other'],
                     'questions': [{'unit_id': 'group', 'number': n} for n in ('1', '2')] + [{'unit_id': 'other', 'number': '3'}],
                     'sample': [{'unit_id': 'group', 'number': '2'}]}

    def save(self, path, value):
        target = self.batch / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value if isinstance(value, str) else json.dumps(value), encoding='utf-8')

    def check(self, uid, quote, number=None):
        numbers = [number] if number is not None else (['1', '2'] if uid == 'group' else ['3'])
        assessment = {'type': '阅读理解', 'subtypes': ['选择5题'],
                      'knowledge': ['语篇体裁/记叙文/故事', '主题/人与自我/生活与学习/日常起居'],
                      'subquestions': [{'number': n, 'knowledge': []} for n in numbers] if uid == 'group' else [],
                      'difficulty': [{'number': n, 'value': '容易'} for n in numbers]}
        item = {'unit_id': uid, 'verdict': 'ok', 'rationale': '根据题干核对选项',
                'evidence': [{'path': '01_source/正文.txt', 'locator': '原文', 'quote': quote}],
                'assessment': assessment}
        if number is not None:
            item['number'] = number
            assessment['source_comparison'] = '已对照共享文章和题干'
        return item

    def record(self, rid, checks):
        return {'round_id': rid, 'input_binding': 'binding', 'execution_id': 'exec', 'checks': checks}

    def test_year_or_another_unit_cannot_stand_in_for_target(self):
        for quote in ('2017', 'Different question here.'):
            with self.subTest(quote=quote), self.assertRaises(ValueError):
                review._round_checks(self.record('labels', [self.check('group', quote),
                    self.check('other', 'Different question here.')]), 'labels', self.plan, self.batch)

    def test_shared_passage_allowed_for_sample_child(self):
        check = self.check('group', 'Shared passage about a library.', '2')
        self.assertEqual(review._round_checks(self.record('sample', [check]), 'sample', self.plan, self.batch), [])

    def test_short_real_quote_is_accepted(self):
        self.save('01_source/正文.txt', '1. No.\n2. Yes.\n')
        self.save('01_source/structure.confirmed.json', {'units': [
            {'unit_id': 'group', 'unit_kind': 'group', 'numbers': [1, 2], 'stem': '1. No.\n2. Yes.'}]})
        self.assertEqual(review._round_checks(self.record('sample', [self.check('group', 'No.', '2')]),
                                              'sample', self.plan, self.batch), [])

    def test_missing_confirmed_target_text_stays_unchecked(self):
        self.save('01_source/structure.confirmed.json', {'units': [
            {'unit_id': 'group', 'unit_kind': 'group', 'numbers': [1, 2]},
            {'unit_id': 'other', 'unit_kind': 'standalone', 'numbers': [3], 'stem': '3. Different question here.'}]})
        with self.assertRaisesRegex(ValueError, 'no confirmed source text'):
            review._round_checks(self.record('sample', [self.check('group', 'Shared passage about a library.', '2')]),
                                 'sample', self.plan, self.batch)


if __name__ == '__main__':
    unittest.main()
