import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import operations as op
from workflow import operations_development as dev


class RepairSettlementIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / 'batch'
        self.folder.mkdir()
        self.job_path = Path(self.temp.name) / 'job.json'
        self.job_path.write_text(json.dumps({'job_id': 'job', 'workspace': str(self.folder), 'status': 'workspace_ready'}))
        self.incident = {'incident_id': 'incident-a', 'stage': 'validate',
                         'code_repair': {'job_id': 'job', 'status': 'workspace_ready'}}

    def record(self, status, incident_id='incident-a'):
        record = {'job_id': 'job', 'path': str(self.job_path), 'status': status,
                  'incident_id': incident_id}
        op._save_code_record(self.folder, record)
        return record

    def test_uninstalled_states_are_never_verified(self):
        for status in ('workspace_ready', 'tests_passed', 'prepared', 'tests_failed', 'installed_pending_restart'):
            with self.subTest(status=status):
                original = self.record(status)
                op._settle_code_repair(self.folder, self.incident, 'resolved')
                self.assertEqual(op._code_record(self.folder), original)
                self.assertEqual(dev.load(self.job_path)['status'], 'workspace_ready')

    def test_restarted_repair_settles_after_verification(self):
        self.record('restarted_pending_verify')
        op._settle_code_repair(self.folder, self.incident, 'resolved')
        self.assertEqual(op._code_record(self.folder)['status'], 'verified')
        self.assertEqual(dev.load(self.job_path)['status'], 'verified')
        self.assertEqual(self.incident['code_repair']['status'], 'verified')

    def test_other_incident_cannot_settle_or_acknowledge(self):
        original = self.record('restarted_pending_verify', 'old-incident')
        op._settle_code_repair(self.folder, self.incident, 'resolved')
        op._acknowledge_restart(self.folder, self.incident)
        self.assertEqual(op._code_record(self.folder), original)
        self.assertEqual(dev.load(self.job_path)['status'], 'workspace_ready')
        with self.assertRaisesRegex(ValueError, 'code_repair_not_requested'):
            op._code_job(self.folder, self.incident)

    def test_transient_aggregate_and_mixed_discovery(self):
        transient = '；'.join(f'unit-{i}：Upstream HTTP/2 stream failed' for i in range(19))
        mixed = transient + '；unit-20：validator source binding mismatch'
        same_item = 'unit-1：validator source binding mismatch; timeout'
        same_http = 'unit-1：Upstream HTTP/2 stream failed; validator source binding mismatch'
        unavailable = 'unit-1：Upstream service temporarily unavailable'
        for detail, expected in ((transient, False), (unavailable, False),
                                 (mixed, True), (same_item, True), (same_http, True)):
            with self.subTest(expected=expected):
                state = {'identity': {}, 'incident': None, 'receipts': [],
                         'failures': {'old': {'stage': 'validate', 'detail': detail}}}
                manifest = {'stages': {'validate': {'status': op.mf.FAILED}}}
                with patch.object(op, '_load', return_value=state), \
                     patch.object(op, '_identity', return_value={}), \
                     patch.object(op.mf, 'load', return_value=manifest), \
                     patch.object(op.self_heal, '_candidate', return_value=('validate', detail)), \
                     patch.object(op.self_heal, '_load', return_value={'events': []}), \
                     patch.object(op.self_heal, '_retry_evidence', return_value={}), \
                     patch.object(op, 'service_available', return_value=True):
                    result = op._discover(self.folder)
                self.assertEqual(bool(result['incident']), expected)

    def test_request_creates_new_workspace_for_other_incident(self):
        old = self.record('workspace_ready', 'old-incident')
        new = {'job_id': 'new', 'workspace': str(self.folder / 'workspace'),
               'base': {'code_fingerprint': 'fingerprint'}, 'source_root': str(self.folder)}
        with patch.object(op, '_check_budget'), patch.object(op, '_source_root', return_value=self.folder), \
             patch.object(dev, 'create', return_value=new) as create, patch.object(dev, 'files', return_value=[]):
            result = op._request_code_repair(self.folder, self.incident, {})
        create.assert_called_once()
        self.assertEqual(result['job_id'], 'new')
        record = op._code_record(self.folder)
        self.assertEqual(record['incident_id'], 'incident-a')
        self.assertEqual(record['history'], [old])


if __name__ == '__main__':
    unittest.main()
