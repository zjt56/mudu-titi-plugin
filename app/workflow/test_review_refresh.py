import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import auto, contracts, manifest as mf

ROUNDS = {"plan": {"contract": "test"},
          "rounds": [{"round_id": "labels", "execution_id": "run-1",
                      "checks": [{"unit_id": "q-1", "verdict": "ok"}]},
                     {"round_id": "sample", "execution_id": "run-2",
                      "checks": [{"unit_id": "q-1", "number": "1", "verdict": "ok"}]}]}


class ReviewRefreshTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        self.manifest = mf.new_manifest('test', self.batch, {}, {})
        mf.touch_stage(self.manifest, 'review', mf.COMPLETED)
        mf.save(self.batch, self.manifest)

    def _write_rounds(self, payload=ROUNDS):
        (self.batch / '07_review').mkdir(parents=True, exist_ok=True)
        (self.batch / '07_review/review-rounds.json').write_text(
            json.dumps(payload), encoding='utf-8')

    def test_invalid_contract_requeues_review(self):
        self._write_rounds()
        with patch('workflow.review.evaluate_rounds', return_value={
                'execution_completed': False, 'passed': False}):
            self.assertTrue(auto._refresh_completed_review(self.batch, self.manifest))
        saved = mf.load(self.batch)
        self.assertNotEqual(saved['stages']['review']['status'], mf.COMPLETED)
        self.assertIn('当前审核契约', saved['stages']['review']['notes'])
        # 已排回待办后不再重复失效。
        with patch('workflow.review.evaluate_rounds') as evaluate:
            self.assertFalse(auto._refresh_completed_review(self.batch, self.manifest))
        evaluate.assert_not_called()

    def test_real_differences_do_not_requeue_completed_execution(self):
        self._write_rounds()
        with patch('workflow.review.evaluate_rounds', return_value={
                'execution_completed': True, 'passed': False,
                'findings': [{'id': 'real'}]}), \
             patch.object(mf, 'invalidate_stage') as invalidate:
            self.assertFalse(auto._refresh_completed_review(self.batch, self.manifest))
        invalidate.assert_not_called()
        self.assertEqual(mf.load(self.batch)['stages']['review']['status'], mf.COMPLETED)

    def test_no_round_evidence_keeps_completed_status(self):
        # 旧批次或被清空的记录不构成“已执行但失效”，不得误触发重审。
        with patch('workflow.review.evaluate_rounds') as evaluate:
            self.assertFalse(auto._refresh_completed_review(self.batch, self.manifest))
        evaluate.assert_not_called()
        self.assertEqual(mf.load(self.batch)['stages']['review']['status'], mf.COMPLETED)

    def test_changed_bound_input_invalidates_completed_review(self):
        self._write_rounds()
        stage = contracts.STAGE_BY_ID['review']
        mf.touch_stage(self.manifest, 'review', mf.COMPLETED,
                       input_hashes=mf.input_hashes(self.batch, self.manifest, stage))
        mf.save(self.batch, self.manifest)
        # A completed review is bound to the bytes it actually judged. A changed
        # validation report must invalidate it on the next scheduler pass.
        (self.batch / '07_review/validation-report.json').write_text(
            '{"passed": true, "generated_at": "now"}', encoding='utf-8')
        self.assertTrue(auto._refresh_completed_review(self.batch, self.manifest))
        saved = mf.load(self.batch)
        self.assertEqual(saved['stages']['review']['status'], mf.INVALIDATED)
        self.assertIn('审核绑定输入已变化', saved['stages']['review']['notes'])
        # Already invalidated evidence is not requeued again.
        self.assertFalse(auto._refresh_completed_review(self.batch, saved))

    def test_legacy_raw_review_binding_does_not_false_positive(self):
        self._write_rounds()
        stage = contracts.STAGE_BY_ID['review']
        legacy = mf.input_hashes(self.batch, self.manifest, stage, raw=True)
        mf.touch_stage(self.manifest, 'review', mf.COMPLETED, input_hashes=legacy)
        self.assertEqual(mf.review_bound_inputs_changed(self.batch, self.manifest), [])
        (self.batch / '07_review/validation-report.json').write_text('{"passed": true}', encoding='utf-8')
        self.assertIn('review:07_review/validation-report.json',
                      mf.review_bound_inputs_changed(self.batch, self.manifest))


if __name__ == '__main__':
    unittest.main()
