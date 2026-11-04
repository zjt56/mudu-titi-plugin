"""Regression checks for source fidelity and the actual review boundary."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import manifest as mf, runner


class PipelineRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for rel in ('01_source/正文.txt', '01_source/structure.json',
                    '01_source/structure.confirmed.json', '06_batch/batch_meta.json'):
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('{}', encoding='utf-8')
        self.original = self.root / 'original.docx'
        self.original.write_bytes(b'original')
        self.manifest = mf.new_manifest('test', self.root,
            {'source': {'path': str(self.original), 'sha256': mf.sha256_file(self.original)}}, {})
        self.gate = {'passed': True, 'original_sha256': mf.sha256_file(self.original),
                     'input_hashes': {rel: mf.sha256_file(self.root / rel) for rel in (
                         '01_source/正文.txt', '01_source/structure.json', '01_source/structure.confirmed.json')}}
        self.write_gate()

    def write_gate(self):
        path = self.root / '07_review/structure-gate.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(self.gate), encoding='utf-8')

    def test_embedded_answer_extraction_is_checked_without_separate_upload(self):
        from workflow import checks
        prepared = self.root / '01_source_ans/answers.json'
        prepared.parent.mkdir()
        prepared.write_text('{"answers":{"40":"D"}}', encoding='utf-8')
        def run_check(check, skill_root, batch_json, inputs):
            self.assertEqual(inputs['answers']['path'], str(prepared))
            return {'check': check.name, 'blocking': True, 'passed': True}
        with patch.object(checks, 'run_check', side_effect=run_check) as run:
            result = checks.run_all(self.root, self.root / '06_batch/batch_meta.json', {},
                                   only=('scripts/06-upload/check_answers.py',))
        self.assertTrue(result['passed'])
        self.assertEqual(result['skipped'], [])
        run.assert_called_once()

    def test_evidence_not_reviewer_names_is_required(self):
        with patch.dict(os.environ, {}, clear=True), patch('workflow.question_content.audit_batch_source',
                return_value={'passed': True, 'issues': []}):
            result = runner.flowchart_checks(self.root, self.manifest)
        self.assertTrue(result['passed'], result)
        self.assertNotIn('INDEPENDENT_CONFIG_MISSING', [x['code'] for x in result['errors']])
        self.assertFalse((self.root / '07_review/structure-review.json').exists())

    def test_changed_structure_requires_new_source_check(self):
        (self.root / '01_source/structure.json').write_text('{"changed":true}', encoding='utf-8')
        with patch.dict(os.environ, {}, clear=True), patch('workflow.question_content.audit_batch_source',
                return_value={'passed': True, 'issues': []}):
            result = runner.flowchart_checks(self.root, self.manifest)
        self.assertFalse(result['passed'])
        self.assertIn('STRUCTURE_EVIDENCE_STALE', [x['code'] for x in result['errors']])

    def test_final_candidate_loss_blocks_even_when_script_checks_pass(self):
        script_report = {'passed': True, 'checks': [], 'blocking_failures': [], 'warnings': [], 'skipped': []}
        with patch.object(runner.checks, 'run_all', return_value=script_report), \
             patch('workflow.question_content.audit_batch_source', return_value={
                 'passed': False, 'issues': [{'code': 'OPTIONS_MISMATCH', 'message': '第40题缺D选项'}]}):
            code = runner.do_validate(self.root, self.manifest)
        self.assertEqual(code, 1)
        saved = json.loads((self.root / '07_review/validation-report.json').read_text())
        self.assertFalse(saved['passed'])
        self.assertIn('SOURCE_SLICE_MISMATCH', saved['blocking_failures'])

    def test_validate_merges_concurrent_stage_update(self):
        mf.save(self.root, self.manifest)
        script_report = {'passed': True, 'checks': [], 'blocking_failures': [], 'warnings': [], 'skipped': []}
        def run_all(*args, **kwargs):
            # Another cooperative writer completes `meta` while checks are running.
            current = mf.load(self.root)
            mf.touch_stage(current, 'meta', mf.COMPLETED, exit_code=0)
            mf.save(self.root, current)
            return dict(script_report)

        with patch.object(runner.checks, 'run_all', side_effect=run_all), \
             patch('workflow.question_content.audit_batch_source',
                   return_value={'passed': True, 'issues': []}):
            code = runner.do_validate(self.root, self.manifest)
        self.assertEqual(code, 0)
        manifest = mf.load(self.root)
        self.assertEqual(manifest['stages']['meta']['status'], mf.COMPLETED)
        self.assertEqual(manifest['stages']['validate']['status'], mf.COMPLETED)

    def test_validate_refuses_stale_source_evidence(self):
        mf.save(self.root, self.manifest)
        script_report = {'passed': True, 'checks': [], 'blocking_failures': [], 'warnings': [], 'skipped': []}
        def run_all(*args, **kwargs):
            (self.root / '01_source/正文.txt').write_text('{"changed":true}', encoding='utf-8')
            return dict(script_report)

        with patch.object(runner.checks, 'run_all', side_effect=run_all), \
             patch('workflow.question_content.audit_batch_source',
                   return_value={'passed': True, 'issues': []}):
            code = runner.do_validate(self.root, self.manifest)
        self.assertEqual(code, 1)
        manifest = mf.load(self.root)
        self.assertNotEqual(manifest['stages']['validate']['status'], mf.COMPLETED)

    def test_review_gate_refuses_changed_review_evidence(self):
        mf.save(self.root, self.manifest)
        (self.root / '07_review').mkdir(parents=True, exist_ok=True)
        queue = self.root / '07_review/manual-review-queue.json'
        queue.write_text('{"items": []}', encoding='utf-8')
        base = {'passed': True, 'counts': {'blocking_findings': 0, 'open_manual_items': 0,
                                           'required_unchecked': 0}, 'errors': []}

        def evaluate(*args, **kwargs):
            queue.write_text('{"items": [{"finding_id": "WF-0001", "status": "open"}]}', encoding='utf-8')
            return dict(base)

        with patch.object(runner.review, 'evaluate', side_effect=evaluate), \
             patch.object(runner, 'flowchart_checks', return_value={'passed': True, 'errors': []}), \
             patch('workflow.label_audit.inspect_file', return_value={'passed': True, 'errors': []}):
            code = runner.do_review_gate(self.root, self.manifest)
        self.assertEqual(code, 1)
        manifest = mf.load(self.root)
        self.assertNotEqual(manifest['stages']['review_gate']['status'], mf.COMPLETED)

    def test_review_gate_merges_concurrent_update_and_passes(self):
        mf.save(self.root, self.manifest)
        (self.root / '07_review').mkdir(parents=True, exist_ok=True)
        base = {'passed': True, 'counts': {'blocking_findings': 0, 'open_manual_items': 0,
                                           'required_unchecked': 0}, 'errors': []}

        def evaluate(*args, **kwargs):
            current = mf.load(self.root)
            mf.touch_stage(current, 'review', mf.COMPLETED, exit_code=0)
            mf.save(self.root, current)
            return dict(base)

        with patch.object(runner.review, 'evaluate', side_effect=evaluate), \
             patch.object(runner, 'flowchart_checks', return_value={'passed': True, 'errors': []}), \
             patch('workflow.label_audit.inspect_file', return_value={'passed': True, 'errors': []}):
            code = runner.do_review_gate(self.root, self.manifest)
        self.assertEqual(code, 0)
        manifest = mf.load(self.root)
        self.assertEqual(manifest['stages']['review']['status'], mf.COMPLETED)
        self.assertEqual(manifest['stages']['review_gate']['status'], mf.COMPLETED)

    def test_blocked_layout_is_dispatched_without_approving_gate(self):
        mf.save(self.root, self.manifest)
        report = {'passed': False, 'unit_counts': {}, 'removed_count': 0,
                  'blockers': [{'code': 'LAYOUT_UNCERTAIN', 'message': '栏目边界不明确'}],
                  'layout_required': True,
                  'source_sha256': mf.sha256_file(self.root / '01_source/正文.txt'),
                  'structure_sha256': mf.sha256_file(self.root / '01_source/structure.json'),
                  'original_sha256': mf.sha256_file(self.original),
                  'input_hashes': {rel: mf.sha256_file(self.root / rel) for rel in (
                      '01_source/正文.txt', '01_source/structure.json')}}
        with patch.object(runner.structure_gate, 'run_gate', return_value=report), \
             patch('workflow.layout_dispatch.prepare_layout_fallback', return_value={
                 'status': 'needs_review', 'dispatch_state': 'requires_render'}) as dispatch:
            code = runner.do_structure_gate(self.root, self.manifest)
        self.assertEqual(code, 1)
        dispatch.assert_called_once()
        saved = json.loads((self.root / '07_review/structure-gate.json').read_text())
        self.assertFalse(saved['passed'])
        self.assertEqual(saved['layout_fallback']['dispatch_state'], 'requires_render')
        self.assertEqual(mf.load(self.root)['stages']['structure_gate']['status'], mf.BLOCKED)

    def test_original_change_invalidates_bound_structure_evidence(self):
        self.original.write_bytes(b'changed original')
        with patch.dict(os.environ, {}, clear=True), patch('workflow.question_content.audit_batch_source',
                return_value={'passed': True, 'issues': []}):
            result = runner.flowchart_checks(self.root, self.manifest)
        self.assertIn('ORIGINAL_EVIDENCE_STALE', [x['code'] for x in result['errors']])


if __name__ == '__main__':
    unittest.main()
