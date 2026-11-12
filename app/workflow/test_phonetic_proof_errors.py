"""Narrow source-proof failure and rebuild-cost checks, without real batches."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow.phonetic_structure import verified_groups


class PhoneticProofErrorsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / '06_batch' / 'batch_meta.json'

    def test_no_eligible_units_never_builds_whole_paper_draft(self):
        for units in ([], [{'unit_id': 'reading', 'type': '阅读理解', 'unit_kind': 'group',
                            'numbers': [1, 2]}]):
            with self.subTest(units=units), \
                 patch('workflow.agent_tasks.confirmed_units', return_value=units), \
                 patch('workflow.question_draft.build') as build:
                self.assertEqual(verified_groups(self.path), {})
                build.assert_not_called()

    def test_bad_confirmed_evidence_never_builds_or_grants_exemption(self):
        errors = [OSError('missing'), ValueError('bad unit'), KeyError('unit_id'), TypeError('shape'),
                  UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'invalid'), AttributeError('shape'), IndexError('row')]
        for error in errors:
            with self.subTest(error=type(error).__name__), \
                 patch('workflow.agent_tasks.confirmed_units', side_effect=error), \
                 patch('workflow.question_draft.build') as build:
                self.assertEqual(verified_groups(self.path), {})
                build.assert_not_called()
        for units in ([None], [{'type': '语音题'}]):
            with self.subTest(units=units), patch('workflow.agent_tasks.confirmed_units', return_value=units):
                self.assertEqual(verified_groups(self.path), {})

    def test_eligible_unit_build_failure_returns_no_proof(self):
        unit = {'unit_id': 'phonetic', 'type': '语音题'}
        for error in (AttributeError('extraction shape'), IndexError('bad row'),
                      UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'invalid')):
            with self.subTest(error=type(error).__name__), \
                 patch('workflow.agent_tasks.confirmed_units', return_value=[unit]), \
                 patch('workflow.question_draft.build', side_effect=error) as build:
                self.assertEqual(verified_groups(self.path), {})
                build.assert_called_once()

    def test_malformed_draft_result_never_grants_exemption(self):
        for draft in (None, {}, {'items': [None]}, {'items': [], 'issues': [None]}):
            with self.subTest(draft=draft), \
                 patch('workflow.agent_tasks.confirmed_units', return_value=[{'unit_id': 'phonetic', 'type': '语音题'}]), \
                 patch('workflow.question_draft.build', return_value=draft):
                self.assertEqual(verified_groups(self.path), {})


if __name__ == '__main__':
    unittest.main()
