import json
import tempfile
import time
import unittest
from pathlib import Path

from workflow import manifest as mf, self_heal


class ProducerRoutingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.batch = Path(self.temp.name)
        for relative in ('07_review', 'workflow'):
            (self.batch / relative).mkdir(parents=True, exist_ok=True)

    def test_validation_check_routes_to_actual_producer(self):
        (self.batch / '07_review/validation-report.json').write_text(json.dumps({
            'checks': [{'script': 'scripts/06-upload/check_explanations.py',
                        'passed': False, 'output': 'ORIGINAL_EXPLANATION_OMITTED'}]}))
        self.assertEqual(self_heal._error_producer(self.batch, 'validate', 'validation failed'),
                         'explanations')

    def test_structured_issue_carries_affected_unit_to_producer_feedback(self):
        (self.batch / '01_source').mkdir(parents=True)
        (self.batch / '01_source/structure.confirmed.json').write_text(json.dumps({
            'units': [{'unit_id': 'q-1', 'unit_kind': 'standalone', 'numbers': [1]}]}))
        (self.batch / '07_review/validation-report.json').write_text(json.dumps({
            'errors': [{'code': 'ORIGINAL_EXPLANATION_OMITTED', 'unit_id': 'q-1',
                        'field': 'explanation', 'message': '解析缺失'}]}))
        issues = self_heal._producer_issues(self.batch, 'validate', 'explanations')
        self.assertEqual([issue['unit_id'] for issue in issues], ['q-1'])

    def test_structured_field_routes_and_unknown_error_does_not_guess(self):
        self.assertEqual(self_heal._producer_for_issue({'field': 'answer'}), 'answers')
        self.assertEqual(self_heal._producer_for_issue({'field': 'explanation'}), 'explanations')
        self.assertEqual(self_heal._producer_for_issue({'field': 'type'}), 'types')
        self.assertIsNone(self_heal._error_producer(self.batch, 'validate', 'generic failure'))

    def test_failed_target_waiting_on_dependency_is_not_reported_failed(self):
        manifest = mf.new_manifest('waiting', self.batch, {}, {})
        mf.touch_stage(manifest, 'answers', mf.FAILED, notes='previous failed attempt')
        mf.touch_stage(manifest, 'preassemble', mf.PENDING)
        mf.touch_stage(manifest, 'validate', mf.FAILED)
        mf.save(self.batch, manifest)
        ledger = {'attempts': {}, 'events': [{
            'at': '2000-01-01T00:00:00+00:00', 'stage': 'validate',
            'origin_stage': 'validate', 'target_stage': 'answers',
            'outcome': 'retry_scheduled', 'verification': {},
        }]}
        (self.batch / self_heal.LEDGER).write_text(json.dumps(ledger))
        from unittest.mock import patch
        with patch.object(self_heal, '_path', return_value=self.batch / self_heal.LEDGER), \
             patch.object(self_heal, '_save'):
            outcome = self_heal.observe_outcomes(self.batch, manifest)
        self.assertEqual(outcome['failed'], [])


if __name__ == '__main__':
    unittest.main()
