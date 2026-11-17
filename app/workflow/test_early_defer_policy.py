"""Early stages produce candidates; completeness is settled at the final gate."""
import json
import tempfile
import unittest
from pathlib import Path

from workflow import final_audit, manifest as mf, contracts


class EarlyDeferPolicyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def put(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
                        encoding='utf-8')

    def test_content_completeness_false_is_reported_only_at_final_audit(self):
        self.put('03_types/types.json', {'items': [
            {'unit_id': 'q-1', 'type': '单项填空', 'subtypes': ['四个选项'],
             'words': 5, 'contentComplete': False, 'evidence': ['正文 L1']}]})
        errors = final_audit.content_completeness_errors(self.root)
        self.assertEqual([row['code'] for row in errors], ['CONTENT_COMPLETENESS_UNVERIFIED'])
        self.assertEqual(errors[0]['unit_id'], 'q-1')

    def test_unconsumable_confirmed_structure_marks_gate_stale(self):
        self.put('01_source/structure.confirmed.json', {'units': [
            {'unit_kind': 'standalone', 'numbers': [1], 'type': '单项填空'},
            {'unit_kind': 'standalone', 'numbers': [1], 'type': '单项填空'}]})
        self.put('07_review/structure-gate.json', {'passed': True})
        state = {'batch_dir': str(self.root), 'stages': {
            'structure_gate': {'status': mf.COMPLETED, 'input_hashes': {}, 'output_hashes': {}},
            'upload': {'status': mf.PENDING}}}
        stale, reason = mf.is_stale(self.root, state, contracts.STAGE_BY_ID['structure_gate'])
        self.assertTrue(stale)
        self.assertIn('结构确认产物不可消费', reason)


if __name__ == '__main__':
    unittest.main()
