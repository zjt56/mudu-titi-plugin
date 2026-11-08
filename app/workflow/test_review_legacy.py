"""Isolated original-execution regression tests; no model/network/real batches."""
import copy
import json
import unittest
from unittest.mock import patch

from workflow import review, review_legacy, review_rounds
from workflow import test_review_local_reuse as fixtures


class ReviewLegacyTest(unittest.TestCase):
    setUp = fixtures.ReviewLocalReuseTest.setUp
    write = fixtures.ReviewLocalReuseTest.write
    write_source = fixtures.ReviewLocalReuseTest.write_source
    load = fixtures.ReviewLocalReuseTest.load
    response = fixtures.ReviewLocalReuseTest.response
    finish = fixtures.ReviewLocalReuseTest.finish

    def legacy(self, *, unit_bindings=False, sample=None):
        current = review.prepare_round_plan(self.batch)
        old = copy.deepcopy(current)
        old['input_hashes'].pop('rules:docs/content-labeling.md')
        old['input_binding'] = review._digest({'input_hashes': old['input_hashes'], 'questions': old['questions']})
        if unit_bindings:
            old['unit_bindings'] = {uid: '0' * 64 for uid in old['units']}
        else:
            old.pop('unit_bindings')
        pool = review.round_sample_pool(self.batch)
        old['sample'] = (sample if sample is not None else sorted(pool, key=lambda q: review._digest([old['input_binding'], q]))[:(len(pool) + 4) // 5])
        old.pop('sample_method', None)
        with patch.object(review, 'prepare_round_plan', return_value=old), \
             patch.object(review_rounds, 'invoke', side_effect=AssertionError('No model calls')):
            self.finish()
        self.old = old
        self.payload = self.load('07_review/review-rounds.json')
        return current

    def test_rule_registration_and_projection_upgrade_preserve_original_records_read_only(self):
        for projections in (False, True):
            with self.subTest(unit_bindings=projections):
                # The second iteration uses a fresh isolated batch.
                if projections:
                    self.setUp()
                current = self.legacy(unit_bindings=projections)
                before = {p: p.read_bytes() for p in self.batch.rglob('*') if p.is_file()}
                payload_before = copy.deepcopy(self.payload)
                effective, info = review.resolve_round_plan(self.batch, current, self.payload)
                self.assertTrue(info['accepted'], info)
                self.assertEqual(effective, self.old)
                self.assertFalse(info['unit_projection_hashes_used'])
                self.assertFalse(info['private_raw_inputs_claimed_unchanged'])
                self.assertEqual(info['registered_existing_rules'], ['rules:docs/content-labeling.md'])
                self.assertEqual(self.payload, payload_before)
                result = review.evaluate_rounds(self.batch, self.payload)
                self.assertTrue(result['execution_completed'], result)
                self.assertEqual(result['plan'], self.old)
                self.assertEqual(before, {p: p.read_bytes() for p in self.batch.rglob('*') if p.is_file()})

    def test_resume_does_not_issue_new_task_or_rewrite_original_bindings(self):
        self.legacy()
        ledger = self.batch / '07_review/review-rounds.json'
        before = ledger.read_bytes()
        with patch.object(review_rounds, 'invoke', side_effect=AssertionError('No new model call')):
            result = review_rounds.export_task(self.batch)
        self.assertEqual(result['action'], 'complete', result)
        self.assertTrue(result['completed'], result)
        self.assertEqual(ledger.read_bytes(), before)
        snapshot = self.load(review.COMPARISON_SNAPSHOT_FILE)
        self.assertEqual(snapshot['plan'], self.old)
        self.assertEqual(snapshot['input_binding'], self.old['input_binding'])

    def test_explicit_fresh_review_still_uses_current_plan(self):
        current = self.legacy()
        review_rounds.restart_rounds(self.batch)
        result = review_rounds.export_task(self.batch)
        self.assertEqual(result['action'], 'exported', result)
        binding = self.load('workflow/review-bindings/' + result['task_id'] + '.json')
        self.assertEqual(binding['plan'], current)

    def test_sample_seed_change_keeps_legal_original_sample(self):
        current = self.legacy()
        self.assertNotEqual(current['sample'], self.old['sample'])
        result = review.evaluate_rounds(self.batch, self.payload)
        self.assertTrue(result['compatibility']['accepted'], result)
        self.assertEqual(result['plan']['sample'], self.old['sample'])
        self.assertEqual(result['compatibility']['verified_task_ids'], [p['execution_id'] for r in self.payload['rounds'] for p in r.get('parts', [{'execution_id': r['execution_id']}])])

    def test_real_stem_knowledge_and_label_changes_deny_whole_plan(self):
        self.legacy()
        for path, change in (('06_batch/batch.json', lambda v: v['items'][0].update(stem='Actual different question')),
                             ('04_knowledge/knowledge.json', lambda v: v['items'][0].update(knowledge=['词汇/词汇辨析/动词辨析'])),
                             ('03_types/types.json', lambda v: v['items'][0].update(subtypes=['事实细节']))):
            with self.subTest(path=path):
                original = (self.batch / path).read_bytes()
                value = self.load(path)
                change(value)
                self.write(path, value)
                effective, info = review.resolve_round_plan(self.batch)
                self.assertFalse(info['accepted'], info)
                self.assertNotEqual(effective, self.old)
                (self.batch / path).write_bytes(original)

    def test_future_prompt_semantic_contract_is_not_covered_by_legacy_migration(self):
        current = self.legacy()
        with patch.object(review_rounds, 'PROMPT_VERSION', 'review-prompt-v10'):
            info = review_legacy.compatible_plan(self.batch, current, self.payload)
        self.assertFalse(info['accepted'], info)
        self.assertIn('contract', info['reason'])

    def test_real_old_rule_change_denied(self):
        current = self.legacy()
        original = review_legacy._sha
        name = next(k[6:] for k in self.old['input_hashes'] if k.startswith('rules:'))
        with patch.object(review_legacy, '_sha', side_effect=lambda p: 'f' * 64 if p.as_posix().endswith(name) else original(p)):
            self.assertFalse(review_legacy.compatible_plan(self.batch, current, self.payload)['accepted'])

    def test_task_response_receipt_and_transaction_tampering_denied(self):
        current = self.legacy()
        record = self.payload['rounds'][0]
        task_id = record['parts'][0]['execution_id'] if record.get('parts') else record['execution_id']
        last = record['parts'][-1]['execution_id'] if record.get('parts') else record['execution_id']
        response = next((self.batch / 'workflow/review-results' / task_id).glob('*.json'))
        cases = [(self.batch / f'workflow/review-tasks/{task_id}/task.json', lambda v: v.update(prompt=v['prompt'] + 'tampered')),
                 (response, lambda v: v.update(notes=['tampered response'])),
                 (self.batch / f'workflow/review-bindings/{task_id}.receipt.json', lambda v: v.update(result_digest='f' * 64)),
                 (self.batch / f'workflow/review-transactions/{last}.json', lambda v: v['record'].update(execution_id='tampered'))]
        for path, mutate in cases:
            with self.subTest(file=path.name):
                original = path.read_bytes()
                value = json.loads(original)
                mutate(value)
                self.write(path.relative_to(self.batch).as_posix(), value)
                self.assertFalse(review_legacy.compatible_plan(self.batch, current, self.payload)['accepted'])
                path.write_bytes(original)
        receipt = self.batch / f'workflow/review-bindings/{task_id}.receipt.json'
        receipt.unlink()
        self.assertFalse(review_legacy.compatible_plan(self.batch, current, self.payload)['accepted'])

    def test_added_rule_needs_private_hash_and_every_full_frozen_prompt(self):
        current = self.legacy()
        record = self.payload['rounds'][0]
        task_id = record['parts'][0]['execution_id'] if record.get('parts') else record['execution_id']
        path = self.batch / f'workflow/review-bindings/{task_id}.json'
        original = path.read_bytes()
        binding = self.load(path.relative_to(self.batch).as_posix())
        key = next(k for k in binding['input_hashes'] if k.endswith('/docs/content-labeling.md'))
        binding['input_hashes'][key] = 'f' * 64
        self.write(path.relative_to(self.batch).as_posix(), binding)
        info = review_legacy.compatible_plan(self.batch, current, self.payload)
        self.assertFalse(info['accepted'], info)
        path.write_bytes(original)
        # All original execution files still pass, but a partial/missing decoded
        # rule must fail the additional whole-plan proof.
        for content in ({}, {'docs/content-labeling.md': 'partial rule text'}):
            with patch.object(review_legacy, '_supplied', return_value=content):
                info = review_legacy.compatible_plan(self.batch, current, self.payload)
                self.assertFalse(info['accepted'], info)
                self.assertIn('fully frozen', info['reason'])

    def test_compatibility_does_not_approve_real_review_findings(self):
        def response(exported):
            result = fixtures.ReviewLocalReuseTest.response(self, exported)
            result['artifacts']['review_round']['checks'][0].update(
                verdict='error', rationale='原标注存在真实差异，需要保留人工处理。')
            return result
        with patch.object(self, 'response', side_effect=response):
            self.legacy()
        result = review.evaluate_rounds(self.batch, self.payload)
        self.assertTrue(result['compatibility']['accepted'], result)
        self.assertTrue(result['execution_completed'], result)
        self.assertFalse(result['passed'], result)
        self.assertTrue(result['findings'])

    def test_sample_insufficient_duplicate_or_outside_pool_denied(self):
        current = self.legacy()
        for sample in (self.old['sample'][:1], [self.old['sample'][0]] * 2, [dict(unit_id='unknown', number='999')] * 2):
            with self.subTest(sample=sample):
                payload = copy.deepcopy(self.payload)
                payload['plan']['sample'] = sample
                info = review_legacy.compatible_plan(self.batch, current, payload)
                self.assertFalse(info['accepted'], info)
                self.assertIn('sample', info['reason'])

    def test_format_equivalence_requires_independent_verifier(self):
        self.assertIsNone(review_legacy.verify_format_equivalence(self.batch, '06_batch/batch.json', 'a' * 64, 'b' * 64))


if __name__ == '__main__':
    unittest.main()
