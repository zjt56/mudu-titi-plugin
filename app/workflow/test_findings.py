import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import findings, self_heal, final_audit


class FindingsTest(unittest.TestCase):
    def test_complete_scope_survives_more_than_twenty_four_defects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / '07_review/validation-report.json'
            report.parent.mkdir()
            errors = [{'code': 'EXPLANATION_MISSING', 'unit_id': f'u-{n}',
                       'field': 'explanation', 'message': '原解析遗漏'} for n in range(60)]
            report.write_text(json.dumps({'errors': errors}), encoding='utf-8')
            rows = self_heal._producer_issues(root, 'validate', 'explanations')
            self.assertEqual(len(rows), 60)
            self.assertEqual(rows[-1]['unit_id'], 'u-59')

    def test_attach_is_idempotent_and_keeps_structured_evidence(self):
        row = {'code': 'EXPLANATION_MISSING', 'unit_id': 'u-1', 'field': 'explanation',
               'message': 'missing', 'evidence': ['正文.txt:12']}
        report = {'errors': [row, dict(row)]}
        findings.attach(report, path='validation')
        first = json.dumps(report, sort_keys=True)
        findings.attach(report, path='validation')
        self.assertEqual(json.dumps(report, sort_keys=True), first)
        self.assertEqual(report['finding_summary']['total'], 1)
        self.assertEqual(report['findings'][0]['evidence'], ['正文.txt:12'])

    def test_review_and_advisory_do_not_trigger_content_repair(self):
        for row in ({'code': 'REVIEW_ROUND_FINDING', 'producer_stage': 'knowledge'},
                    {'blocking': False, 'field': 'explanation', 'message': 'warning'}):
            value = findings.normalize(row)
            self.assertIsNone(self_heal._producer_for_issue(value))
        self.assertFalse(findings.normalize({'code': 'MANUAL_REVIEW_OPEN'})['blocking'])

    def test_warnings_keep_evidence_without_becoming_repair_jobs(self):
        report = {'warnings': [{'code': 'FORMAT_SPACING', 'unit_id': 'u-2',
                               'field': 'stem', 'evidence': ['line 5'], 'message': 'spacing'},
                              '延期核对']}
        findings.attach(report)
        self.assertEqual(report['finding_summary']['total'], 2)
        self.assertEqual(report['finding_summary']['blocking'], 0)
        self.assertEqual(report['findings'][0]['evidence'], ['line 5'])
        self.assertIsNone(self_heal._producer_for_issue(report['findings'][0]))
        before = json.dumps(report, sort_keys=True)
        findings.attach(report)
        self.assertEqual(json.dumps(report, sort_keys=True), before)

    def test_real_option_comparisons_remain_hard(self):
        rows = [{'code': code} for code in ('OPTION_CONTENT_MISMATCH', 'SOURCE_OPTIONS_INCOMPLETE',
                                            'SHARED_OPTIONS_MISMATCH', 'SOURCE_IMAGE_MISSING')]
        hard, soft = findings.source_partition(rows)
        self.assertEqual(len(hard), 3)
        self.assertEqual(soft, [rows[-1]])
        hard, _ = findings.source_partition([{'code': 'OPTION_CONTENT_MISMATCH',
            'failure_class': 'capability_limited', 'comparison_performed': True}])
        self.assertEqual(len(hard), 1)


class FinalAuditTest(unittest.TestCase):
    def test_standalone_format_is_explicit_about_unchecked_source(self):
        with tempfile.TemporaryDirectory() as directory:
            result = final_audit.run(directory, {'items': []})
        self.assertIsNone(result['passed'])
        self.assertIn('source_fidelity', result['unchecked'])

    def test_managed_missing_source_cannot_become_standalone(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'workflow').mkdir()
            (root / 'workflow/manifest.json').write_text('{"stages": {}}', encoding='utf-8')
            result = final_audit.run(root, {'items': []})
            self.assertFalse(result['passed'])
            self.assertEqual(len(result['errors']), 3)
            (root / 'workflow/manifest.json').write_text('broken', encoding='utf-8')
            result = final_audit.run(root, {'items': []})
            self.assertIn('FINAL_SOURCE_CONTEXT_INVALID', [r['code'] for r in result['errors']])

    def test_partial_source_context_requires_complete_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '01_source').mkdir()
            (root / '01_source/正文.txt').write_text('原卷正文', encoding='utf-8')
            result = final_audit.run(root, {'items': []})
            self.assertFalse(result['passed'])
            self.assertEqual(len(result['errors']), 2)

    def test_managed_delivery_merges_scoped_errors_without_mutating_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'workflow').mkdir()
            (root / 'workflow/manifest.json').write_text('{"stages": {}}', encoding='utf-8')
            for rel in final_audit.SOURCE_FILES:
                target = root / rel
                target.parent.mkdir(exist_ok=True)
                target.write_text('{}' if rel.endswith('.json') else '原卷正文', encoding='utf-8')
            payload = {'items': []}
            with patch('workflow.runner.source_audit_for_batch', return_value={'passed': False,
                      'issues': [{'code': 'OPTION_CONTENT_MISMATCH', 'unit_id': 'u-7'}]}), \
                 patch.object(final_audit.upload_content, 'inspect_content', return_value={'passed': False,
                      'errors': [{'code': 'ORIGINAL_EXPLANATION_OMITTED', 'unit_id': 'u-9',
                                  'field': 'explanation'}], 'source_sha256': {'01_source/正文.txt': 'abc'}}) as content:
                result = final_audit.run(root, payload)
            self.assertFalse(result['passed'])
            self.assertEqual([row['unit_id'] for row in result['errors']], ['u-7', 'u-9'])
            self.assertTrue(content.call_args.kwargs['strict'])
            self.assertEqual(payload, {'items': []})


if __name__ == '__main__':
    unittest.main()
