import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from workflow import independent_review as review


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / 'source.txt'
        self.source.write_text('source', encoding='utf-8')
        self.evidence = {'source': self.source}
        self.batch = {'items': [{'id': 'g', 'unit_kind': 'group', 'subquestions': [{'number': n} for n in range(1, 11)]}, {'id': 's'}]}
        self.seed = b'x' * 32
        self.plan = review.make_plan(self.batch, self.evidence, 'rules-1', self.seed)
        self.trust = {level: {'level': level, 'kind': 'human' if level == 'manual' else 'ai', 'key': bytes([i]) * 32} for i, level in enumerate((*review.LEVELS, 'sample'), 1)}
        self.records = []
        for level in self.trust:
            for question in self.plan['sample'] if level == 'sample' else self.plan['questions']:
                rec = {'actor': level, 'level': level, 'snapshot_id': self.plan['snapshot_id'],
                       'sample_commitment': review._digest(self.plan['sample']), 'question': question,
                       'status': 'reviewed', 'requires_human': level == 'manual', 'note': 'checked',
                       'evidence': {'name': 'source', 'locator': 'page 1'}}
                self.records.append(review.sign_record(rec, self.trust[level]['key']))

    def evaluate(self):
        return review.evaluate(self.batch, self.evidence, 'rules-1', self.seed, self.plan, self.records, self.trust)

    def test_valid_and_sample_rounds_up(self):
        result = self.evaluate()
        self.assertTrue(result['passed'], result)
        self.assertEqual(len(result['sample']), 3)
        self.assertEqual(result['sample'], review.make_plan(self.batch, self.evidence, 'rules-1', self.seed)['sample'])

    def test_missing_forged_and_stale(self):
        self.records.pop()
        self.assertFalse(self.evaluate()['passed'])
        self.records[0]['note'] = 'forged'
        self.assertIn('SIGNATURE_INVALID', [e['code'] for e in self.evaluate()['errors']])
        self.batch['items'][1]['answer'] = 'changed'
        self.assertIn('SNAPSHOT_MISMATCH', [e['code'] for e in self.evaluate()['errors']])

    def test_source_rule_and_plan_changes(self):
        self.source.write_text('changed', encoding='utf-8')
        self.assertFalse(self.evaluate()['passed'])
        self.source.unlink()
        self.assertEqual(self.evaluate()['errors'][0]['code'], 'INPUT_INVALID')
        self.source.write_text('source', encoding='utf-8')
        self.assertFalse(review.evaluate(self.batch, self.evidence, 'rules-2', self.seed, self.plan, self.records, self.trust)['passed'])
        self.plan['sample'] = []
        self.assertFalse(self.evaluate()['passed'])

    def test_identity_and_coverage(self):
        self.trust['manual']['kind'] = 'ai'
        self.assertFalse(self.evaluate()['passed'])
        self.trust['manual']['kind'] = 'human'
        self.trust['sample']['key'] = self.trust['content']['key']
        self.assertIn('REVIEWER_KEY_REUSED', [e['code'] for e in self.evaluate()['errors']])

    def test_invalid_batch_and_directory(self):
        broken = copy.deepcopy(self.batch)
        broken['items'][0]['subquestions'][1]['number'] = 1
        with self.assertRaises(ValueError):
            review.make_plan(broken, self.evidence, 'rules-1', self.seed)
        self.assertFalse(review.evaluate_directory(self.tmp.name, Path(self.tmp.name) / 'missing')['passed'])

    def test_directory_roundtrip(self):
        root = Path(self.tmp.name) / 'batch'
        (root / '07_review').mkdir(parents=True)
        (root / 'batch.json').write_text(json.dumps(self.batch), encoding='utf-8')
        (root / '07_review' / 'independent-plan.json').write_text(json.dumps(self.plan), encoding='utf-8')
        (root / '07_review' / 'independent-records.json').write_text(json.dumps(self.records), encoding='utf-8')
        config = {'version': review.VERSION, 'batch_file': 'batch.json', 'evidence_files': {'source': str(self.source)}, 'rule_version': 'rules-1', 'seed_hex': self.seed.hex(), 'reviewers': {k: {'level': v['level'], 'kind': v['kind'], 'key_hex': v['key'].hex()} for k, v in self.trust.items()}}
        config_path = Path(self.tmp.name) / 'trust.json'
        config_path.write_text(json.dumps(config), encoding='utf-8')
        result = review.evaluate_directory(root, config_path)
        self.assertTrue(result['passed'], result)
        config_path.rename(root / 'trust.json')
        self.assertFalse(review.evaluate_directory(root, root / 'trust.json')['passed'])


if __name__ == '__main__':
    unittest.main()
