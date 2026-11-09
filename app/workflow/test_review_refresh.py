import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import auto, manifest as mf

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


if __name__ == '__main__':
    unittest.main()
