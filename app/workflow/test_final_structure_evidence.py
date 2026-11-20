import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import final_audit


class FinalStructureEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/private/tmp' if Path('/private/tmp').is_dir() else None)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.hashes = {}
        for rel in final_audit.SOURCE_FILES:
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}' if rel.endswith('.json') else '正文', encoding='utf-8')
            self.hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.original = self.root / 'original.docx'
        self.original.write_bytes(b'original')
        self.manifest = {'inputs': {'source': {'path': str(self.original)}}, 'stages': {}}
        workflow = self.root / 'workflow'
        workflow.mkdir()
        (workflow / 'manifest.json').write_text(json.dumps(self.manifest), encoding='utf-8')
        (self.root / '07_review').mkdir()

    def write_report(self, issues):
        report = {'passed': True, 'input_hashes': dict(self.hashes),
                  'original_sha256': hashlib.sha256(self.original.read_bytes()).hexdigest(),
                  'structure_warnings': issues}
        (self.root / final_audit.STRUCTURE_REPORT).write_text(json.dumps(report), encoding='utf-8')

    def test_deferred_issue_reaches_final_errors_with_coordinates(self):
        self.write_report([{'code': 'GROUP_OUT_OF_SECTION', 'message': '跨大题',
                            'source_line': 3, 'unit_id': 'g-1', 'blocking': False},
                           {'code': 'OCR_VISUAL_REVIEW', 'message': '能力限制'}])
        with patch('workflow.runner.source_audit_for_batch', return_value={'passed': True, 'issues': []}), \
             patch.object(final_audit.upload_content, 'inspect_content', return_value={'passed': True, 'errors': []}):
            result = final_audit.run(self.root, {'items': []})
        self.assertFalse(result['passed'])
        self.assertEqual(result['errors'][0]['code'], 'GROUP_OUT_OF_SECTION')
        self.assertEqual(result['errors'][0]['source_line'], 3)
        self.assertEqual(result['errors'][0]['unit_id'], 'g-1')
        self.assertEqual(result['warnings'][0]['code'], 'OCR_VISUAL_REVIEW')
        self.assertIn(final_audit.STRUCTURE_REPORT, result['source_sha256'])

    def test_missing_stale_and_changed_original_are_not_passed(self):
        errors, _ = final_audit.structure_evidence(self.root, self.manifest, dict(self.hashes))
        self.assertEqual(errors, [])
        self.manifest['stages']['structure_gate'] = {'status': 'completed'}
        errors, _ = final_audit.structure_evidence(self.root, self.manifest, dict(self.hashes))
        self.assertEqual(errors[0]['code'], 'FINAL_STRUCTURE_EVIDENCE_INCOMPLETE')
        self.write_report([])
        changed = dict(self.hashes, **{'01_source/正文.txt': 'different'})
        errors, _ = final_audit.structure_evidence(self.root, self.manifest, changed)
        self.assertEqual(errors[0]['code'], 'FINAL_STRUCTURE_EVIDENCE_INCOMPLETE')
        self.original.write_bytes(b'changed')
        errors, _ = final_audit.structure_evidence(self.root, self.manifest, dict(self.hashes))
        self.assertEqual(errors[0]['code'], 'FINAL_STRUCTURE_EVIDENCE_INCOMPLETE')

    def test_sequential_writing_number_is_warning_not_error(self):
        confirmed = {
            'units': [
                {'unit_id': 'q-95', 'unit_kind': 'standalone', 'numbers': [95], 'type': '单词拼写'},
                {'unit_id': 'q-96', 'unit_kind': 'standalone', 'numbers': [96],
                 'type': '书面表达', 'source_span': {'number_binding': 'single_writing_section'}},
            ]
        }
        (self.root / '01_source/structure.confirmed.json').write_text(
            json.dumps(confirmed), encoding='utf-8')
        self.write_report([{'code': 'QUESTION_NUMBER_INFERRED', 'message': '候选编号 [96]'}])
        errors, warnings = final_audit.structure_evidence(self.root, self.manifest, dict(self.hashes))
        self.assertEqual(errors, [])
        self.assertEqual(warnings[0]['code'], 'QUESTION_NUMBER_INFERRED')
        self.assertFalse(warnings[0]['blocking'])

    def scoped_units(self):
        return {'units': [
            {'unit_id': 'u-7', 'unit_kind': 'group', 'numbers': [1, 2],
             'identity_version': 'scoped-v1', 'section_id': 'sec-a',
             'source_span': {'start_line': 7, 'end_line': 40},
             'source_section': {'start_line': 6, 'end_line': 40, 'occurrence': 1}},
            {'unit_id': 'u-42', 'unit_kind': 'group', 'numbers': [1, 2],
             'identity_version': 'scoped-v1', 'section_id': 'sec-b',
             'source_span': {'start_line': 42, 'end_line': 51},
             'source_section': {'start_line': 41, 'end_line': 51, 'occurrence': 2}},
        ]}

    def test_scoped_section_restart_is_warning_not_error(self):
        (self.root / '01_source/structure.confirmed.json').write_text(
            json.dumps(self.scoped_units()), encoding='utf-8')
        self.write_report([{'code': 'NUMBERING_RESTART', 'message': '题号跨大题重置',
                            'source_line': 46, 'blocking': False}])
        errors, warnings = final_audit.structure_evidence(self.root, self.manifest, dict(self.hashes))
        self.assertEqual(errors, [])
        self.assertEqual(warnings[0]['code'], 'NUMBERING_RESTART')
        self.assertFalse(warnings[0]['blocking'])

    def test_restart_without_section_partition_proof_stays_blocking(self):
        confirmed = self.scoped_units()
        confirmed['units'][1]['source_span'] = {'start_line': 41, 'end_line': 51}
        (self.root / '01_source/structure.confirmed.json').write_text(
            json.dumps(confirmed), encoding='utf-8')
        self.write_report([{'code': 'NUMBERING_RESTART', 'message': '题号跨大题重置',
                            'source_line': 46, 'blocking': False}])
        errors, _ = final_audit.structure_evidence(self.root, self.manifest, dict(self.hashes))
        self.assertEqual(errors[0]['code'], 'NUMBERING_RESTART')
        self.assertTrue(errors[0]['blocking'])

    def test_format_gate_reports_deferred_structure_issue(self):
        from workflow import format_gate
        self.write_report([{'code': 'GROUP_OUT_OF_SECTION', 'message': '跨大题', 'source_line': 3}])
        final = self.root / '06_batch/batch_meta.json'
        final.parent.mkdir()
        final.write_text('{"items": []}', encoding='utf-8')
        with patch('workflow.runner.source_audit_for_batch', return_value={'passed': True, 'issues': []}), \
             patch.object(final_audit.upload_content, 'inspect_content', return_value={'passed': True, 'errors': []}), \
             patch.object(format_gate, '_shape', return_value=[]), \
             patch.object(format_gate, '_options', return_value={}), \
             patch.object(format_gate, '_run_final_format_checks'), \
             patch.object(format_gate, '_local_attachments', return_value=({}, {})), \
             patch.object(format_gate.upload_payload, 'build_payload', return_value={'errors': [], 'warnings': [], 'summary': {}}):
            report = format_gate.run(self.root)
        self.assertFalse(report['passed'])
        self.assertIn('GROUP_OUT_OF_SECTION', [issue['code'] for issue in report['errors']])


if __name__ == '__main__':
    unittest.main()
