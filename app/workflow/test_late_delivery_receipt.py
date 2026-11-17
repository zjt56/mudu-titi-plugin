import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import contracts, manifest as mf


class LateDeliveryReceiptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = mf.new_manifest('receipt', self.root, {}, {})
        self.spec = contracts.STAGE_BY_ID['underlines']
        self.put('06_batch/batch_meta.base.json', {'items': []})
        self.put('06_batch/batch_meta.json', {'items': []})
        self.record = self.state['stages']['underlines']
        self.record.update(status=mf.COMPLETED, semantic_outputs={'old': 'output'},
                           semantic_inputs={'old': 'input'})
        self.proof = {'version': 1, 'workflow_id': self.state['workflow_id'],
                      'before_underlines_outputs': {'old': 'output'},
                      'before_underlines_inputs': {'old': 'input'},
                      'meta_base_sha256': mf.sha256_file(self.root / '06_batch/batch_meta.base.json'),
                      'final_sha256': mf.sha256_file(self.root / '06_batch/batch_meta.json')}

    def put(self, rel, data):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding='utf-8')

    def apply(self):
        self.put('workflow/late-assembly-delivery.json', self.proof)
        with patch.object(mf, 'input_hashes', return_value={'old': 'input'}), \
                patch('workflow.handoff.inspect_stage', return_value={'passed': True}), \
                patch.object(mf, 'record_semantics') as refreshed:
            result = mf._refresh_assembled_underline_delivery(self.root, self.state)
        return result, refreshed.call_count

    def test_current_receipt_refreshes_only_existing_underline_delivery(self):
        before = copy.deepcopy(self.state['stages']['answers'])
        self.assertEqual(self.apply(), (True, 1))
        self.assertEqual(self.state['stages']['answers'], before)

    def test_receipt_never_blesses_stale_or_modified_views(self):
        for key, value in (('workflow_id', 'other'), ('version', 2),
                           ('final_sha256', 'wrong'), ('meta_base_sha256', 'wrong'),
                           ('before_underlines_inputs', {'new': 'input'}),
                           ('before_underlines_outputs', {'new': 'output'})):
            with self.subTest(key=key):
                previous = copy.deepcopy(self.proof)
                self.proof[key] = value
                self.assertEqual(self.apply(), (False, 0))
                self.proof = previous
        self.record['rebuild_required'] = True
        self.assertEqual(self.apply(), (False, 0))

    def test_only_final_assembly_requires_explanation_projection_for_staleness(self):
        for record in self.state['stages'].values():
            record.update(status=mf.COMPLETED)
            for key in ('output_hashes', 'semantic_outputs', 'semantic_inputs'):
                record.pop(key, None)
        from workflow.handoff import ExplanationProjectionMismatch
        with patch.object(mf, 'missing_produces', return_value=[]), \
                patch('workflow.handoff.require_explanation_projection',
                      side_effect=ExplanationProjectionMismatch('missing late explanation')) as required:
            for stage_id in ('meta', 'underlines'):
                self.assertFalse(mf.is_stale(self.root, self.state, contracts.STAGE_BY_ID[stage_id])[0])
            required.assert_not_called()
            self.assertTrue(mf.is_stale(self.root, self.state, contracts.STAGE_BY_ID['assemble'])[0])
            required.assert_called_once()

    def test_failed_consumer_revalidation_refuses_refresh(self):
        self.put('workflow/late-assembly-delivery.json', self.proof)
        with patch.object(mf, 'input_hashes', return_value={'old': 'input'}), \
                patch('workflow.handoff.inspect_stage', return_value={'passed': False}), \
                patch.object(mf, 'record_semantics') as refreshed:
            self.assertFalse(mf._refresh_assembled_underline_delivery(self.root, self.state))
            refreshed.assert_not_called()


if __name__ == '__main__':
    unittest.main()
