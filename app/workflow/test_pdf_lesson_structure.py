"""Actual PDF-only lesson regression: captured OCR and actual model response.

The fixture PDF is the unchanged user upload. No OCR/model network calls occur;
OCR text is frozen evidence, not a claim of visual verification or word accuracy.
"""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import structure_ai, sync_practice


FIXTURE = Path(__file__).with_name('fixtures') / 'pdf_lesson_structure'


class PdfLessonStructureTest(unittest.TestCase):
    def setUp(self):
        self.raw = (FIXTURE / 'ocr.md').read_text(encoding='utf-8')
        self.candidate = json.loads((FIXTURE / 'extracted-structure.json').read_text(encoding='utf-8'))
        self.response = json.loads((FIXTURE / 'model-response.json').read_text(encoding='utf-8'))
        self.structured = self.response['artifacts']['structure']

    def sanitize(self, structured=None):
        diagnostics, quarantine = [], []
        units = structure_ai._sanitize_classified(structured or self.structured, self.raw, self.candidate,
                                                 diagnostics=diagnostics, quarantine=quarantine)
        return units, diagnostics, quarantine

    def test_actual_pdf_fixture_and_markdown_lesson_scope(self):
        provenance = json.loads((FIXTURE / 'provenance.json').read_text())
        pdf = (FIXTURE / 'source.pdf').read_bytes()
        self.assertTrue(pdf.startswith(b'%PDF-'))
        self.assertEqual(len(pdf), 714832)
        self.assertEqual(hashlib.sha256(pdf).hexdigest(), provenance['source_sha256'])
        self.assertTrue(sync_practice.looks_like_sync_practice(self.raw))
        headings = sync_practice.heading_candidates(self.raw)
        self.assertEqual([h['start_line'] for h in headings], [6, 18, 34, 74])
        self.assertTrue(all(h['context'].startswith('第五课时') for h in headings))
        self.assertEqual(headings[0]['title'], '### Ⅰ. 根据首字母及汉语提示补全单词')

    def test_old_non_scoped_rejection_was_not_only_reordered_stem(self):
        with patch.object(sync_practice, 'looks_like_sync_practice', return_value=False):
            units, diagnostics, _ = self.sanitize()
        self.assertEqual(units, [])
        duplicates = [d for d in diagnostics if d['check'] == 'GLOBAL_NUMBER_DUPLICATE']
        self.assertEqual([d['unit_index'] for d in duplicates], list(range(6, 13)))
        self.assertEqual(duplicates[0]['source_span'], {'start_line': 20, 'end_line': 32})

    def test_preserves_ten_scoped_candidates_without_formal_partial_success(self):
        units, diagnostics, quarantine = self.sanitize()
        self.assertEqual(units, [])
        self.assertEqual(len(quarantine), 10)
        self.assertTrue(all(u['identity_version'] == 'scoped-v1' for u in quarantine))
        self.assertEqual(len({q for u in quarantine for q in u['question_ids'].values()}), 14)
        # Each repeated 1 belongs to its own original heading, never global fallback.
        ones = [u for u in quarantine if 1 in u['numbers']]
        self.assertEqual(len({u['section_id'] for u in ones}), 3)
        mismatch = next(d for d in diagnostics if d['check'] == 'STEM_NOT_UNIQUE_SOURCE_SLICE')
        self.assertEqual(mismatch['unit_index'], 9)
        self.assertEqual(mismatch['source_span'], {'start_line': 46, 'end_line': 54})
        self.assertFalse(any(u['type'] == '短文填空' for u in quarantine))
        crossing = next(d for d in diagnostics if d['check'] == 'GROUP_CROSSES_SECTION')
        self.assertEqual(crossing['unit_indices'], [12])
        self.assertEqual(crossing['source_spans'][0]['start_line'], 72)
        self.assertTrue(any(d['check'] == 'SOURCE_NUMBERS_UNDECLARED' and d['source_line'] == 54
                            for d in diagnostics))

    def test_omitting_failed_unit_does_not_turn_missing_question_into_success(self):
        value = copy.deepcopy(self.structured)
        del value['units'][8]
        self.assertEqual(self.sanitize(value)[0], [])

    def test_source_parts_do_not_authorize_model_stem_reordering(self):
        value = copy.deepcopy(self.structured)
        value['units'][8]['source_parts'].reverse()
        units, diagnostics, _ = self.sanitize(value)
        self.assertEqual(units, [])
        self.assertTrue(any(d.get('unit_index') == 9 and d['check'] == 'STEM_NOT_UNIQUE_SOURCE_SLICE'
                            for d in diagnostics))

    def test_cached_rejection_has_per_unit_feedback_and_never_reinvokes_same_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            source = batch / '01_source'
            source.mkdir()
            (source / '正文.txt').write_text(self.raw, encoding='utf-8')
            (source / 'structure.json').write_text(json.dumps(self.candidate), encoding='utf-8')
            calls = []

            def invoke(*args):
                calls.append(args)
                return {'response': self.response}

            first = structure_ai.auto_classify(batch, invoke_fn=invoke, force=True)
            second = structure_ai.auto_classify(batch, invoke_fn=invoke, force=True)
            self.assertEqual(len(calls), 1)
            self.assertTrue(second['reused'])
            self.assertFalse(second['retryable'])
            self.assertEqual(first['units'], [])
            self.assertEqual(len(first['quarantine_units']), 10)
            self.assertEqual(len(second['unit_diagnostics']), 12)
            self.assertEqual([d['unit_index'] for d in second['unit_diagnostics'] if d['status'] == 'rejected'], [9, 12])
            marker = json.loads((batch / structure_ai.CLASSIFY_MARKER).read_text())
            self.assertEqual(marker['candidate_response'], self.structured)
            self.assertEqual(marker['status'], 'quarantined')
            self.assertEqual(len(marker['repair_request']['preserved_unit_ids']), 10)
            # Locally revalidate stored evidence after code/policy updates too.
            with patch.object(sync_practice, 'policy_fingerprint', return_value='new-policy'):
                third = structure_ai.auto_classify(batch, invoke_fn=invoke, force=True)
            self.assertTrue(third['reused'])
            self.assertEqual(len(calls), 1)
            # Actual evidence changes permit a fresh request through the runner.
            (source / 'pdf-ocr.json').write_text('{"new_evidence": true}', encoding='utf-8')
            structure_ai.auto_classify(batch, invoke_fn=invoke, force=True)
            self.assertEqual(len(calls), 2)
            self.assertEqual((source / '正文.txt').read_text(encoding='utf-8'), self.raw)

    def test_legacy_empty_marker_revalidates_saved_model_response_without_new_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            source = batch / '01_source'
            source.mkdir()
            (source / '正文.txt').write_text(self.raw, encoding='utf-8')
            (source / 'structure.json').write_text(json.dumps(self.candidate), encoding='utf-8')
            attempt = batch / 'workflow/structure-classify-attempts/existing'
            attempt.mkdir(parents=True)
            (attempt / 'response.json').write_text(json.dumps(self.response), encoding='utf-8')
            marker = {'raw_sha256': structure_ai._sha(source / '正文.txt'),
                      'candidate_sha256': structure_ai._sha(source / 'structure.json'),
                      'pdf_ocr_sha256': '', 'units': [], 'sync_policy_sha256': 'old-code',
                      'route_sha256': hashlib.sha256(json.dumps(
                          {'config': None}, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                      'attempt': str(attempt.relative_to(batch))}
            (batch / structure_ai.CLASSIFY_MARKER).write_text(json.dumps(marker), encoding='utf-8')
            invoke = unittest.mock.Mock(side_effect=AssertionError('must revalidate persisted candidate locally'))
            result = structure_ai.auto_classify(batch, invoke_fn=invoke, force=True)
            invoke.assert_not_called()
            self.assertTrue(result['reused'])
            self.assertEqual(len(result['quarantine_units']), 10)

    def test_markdown_exam_header_still_rejects_global_repeated_numbers(self):
        raw = '# 2025年安徽省中考英语试卷\n## Ⅰ.单项填空\n1. Alpha?\n## Ⅱ.单项填空\n1. Beta?'
        self.assertFalse(sync_practice.looks_like_sync_practice(raw))
        value = {'units': [
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '单项填空', 'stem': '1. Alpha?',
             'source_span': {'start_line': 3, 'end_line': 3}},
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '单项填空', 'stem': '1. Beta?',
             'source_span': {'start_line': 5, 'end_line': 5}},
        ]}
        diagnostics = []
        self.assertEqual(structure_ai._sanitize_classified(value, raw, {}, diagnostics=diagnostics), [])
        self.assertEqual(diagnostics[0]['check'], 'GLOBAL_NUMBER_DUPLICATE')

    def test_crossing_diagnostic_precedes_unrecognized_ocr_blank_anchors(self):
        # Short IPA/LaTeX initial-letter blanks need not be recognized to prove
        # that this candidate already crossed the real heading at source line74.
        with patch("workflow.structure_gate._source_members", return_value=set()):
            units, diagnostics, _ = self.sanitize()
        self.assertEqual(units, [])
        crossing = next(d for d in diagnostics if d["check"] == "GROUP_CROSSES_SECTION")
        self.assertEqual(crossing["unit_indices"], [12])
        self.assertEqual(crossing["source_spans"], [{"start_line": 72, "end_line": 80}])
        self.assertFalse(any(d.get("unit_index") == 12 and d["check"] == "QUESTION_NOT_FOUND"
                             for d in diagnostics))

    def test_heading_normalization_never_discards_non_heading_text(self):
        lines = self.raw.splitlines()
        lines[72] = 'Do not omit this real question text.'
        diagnostics, quarantine = [], []
        units = structure_ai._sanitize_classified(self.structured, '\n'.join(lines), self.candidate,
                                                  diagnostics=diagnostics, quarantine=quarantine)
        self.assertEqual(units, [])
        self.assertFalse(any(u['type'] == '短文填空' for u in quarantine))
        self.assertTrue(any(d['check'] == 'GROUP_CROSSES_SECTION' for d in diagnostics))

    def test_contiguous_synthetic_source_accepts_repeated_numbers_in_distinct_sections(self):
        raw = ('# 第一课时 Section A\n'
               '## Ⅰ.单项填空\n'
               '1. Alpha?\nA. One B. Two C. Three D. Four\n'
               '## Ⅱ.单项填空\n'
               '1. Beta?\nA. Red B. Blue C. Green D. White')
        value = {'units': [
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '单项填空',
             'stem': '1. Alpha?', 'source_span': {'start_line': 3, 'end_line': 4}},
            {'numbers': [1], 'unit_kind': 'standalone', 'type': '单项填空',
             'stem': '1. Beta?', 'source_span': {'start_line': 6, 'end_line': 7}},
        ]}
        diagnostics = []
        units = structure_ai._sanitize_classified(value, raw, {}, diagnostics=diagnostics)
        self.assertEqual(diagnostics, [])
        self.assertEqual(len(units), 2)
        self.assertNotEqual(units[0]['section_id'], units[1]['section_id'])
        self.assertEqual([u['stem'] for u in units], ['1. Alpha?', '1. Beta?'])

    def test_mixed_bad_span_preserves_candidates_and_diagnostics_on_disk(self):
        response = copy.deepcopy(self.response)
        response['artifacts']['structure']['units'][8]['source_span'] = 'bad-span'
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            source = batch / '01_source'
            source.mkdir()
            (source / '正文.txt').write_text(self.raw, encoding='utf-8')
            (source / 'structure.json').write_text(json.dumps(self.candidate), encoding='utf-8')
            invoke = unittest.mock.Mock(return_value={'response': response})
            result = structure_ai.auto_classify(batch, invoke_fn=invoke, force=True)
            self.assertEqual(result['units'], [])
            self.assertEqual(len(result['quarantine_units']), 10)
            marker = json.loads((batch / structure_ai.CLASSIFY_MARKER).read_text())
            failure = marker['unit_diagnostics'][8]
            self.assertEqual(failure['status'], 'rejected')
            self.assertEqual(failure['source_span'], 'bad-span')
            self.assertEqual(failure['checks'][0]['check'], 'UNIT_FIELDS_MISSING')
            self.assertEqual(marker['candidate_response']['units'][8]['source_span'], 'bad-span')
            cached = structure_ai.auto_classify(batch, invoke_fn=invoke, force=True)
            self.assertTrue(cached['reused'])
            self.assertEqual(invoke.call_count, 1)

    def test_heading_with_answer_limit_is_rejected_and_preserved_without_trimming(self):
        raw = ('第五课时\n'
               '## 根据短文内容或所给提示填空（每空限填一词）\n'
               'Alice is 1. ____ today.')
        value = {'units': [{'numbers': [1], 'unit_kind': 'group', 'type': '短文填空',
                            'stem': raw.splitlines()[1] + '\n' + raw.splitlines()[2],
                            'source_span': {'start_line': 2, 'end_line': 3},
                            'source_parts': [{'kind': 'heading', 'start_line': 2, 'end_line': 2},
                                             {'kind': 'material', 'start_line': 3, 'end_line': 3}]}]}
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            source = batch / '01_source'
            source.mkdir()
            (source / '正文.txt').write_text(raw, encoding='utf-8')
            (source / 'structure.json').write_text('{"sections": []}', encoding='utf-8')
            result = structure_ai.auto_classify(batch, force=True, invoke_fn=lambda *args: {
                'response': {'status': 'completed', 'artifacts': {'structure': value}}})
            self.assertEqual(result['units'], [])
            self.assertEqual(result['quarantine_units'], [])
            self.assertTrue(any(d['check'] == 'GROUP_CROSSES_SECTION' and d['unit_indices'] == [1]
                                for d in result['diagnostics']))
            marker = json.loads((batch / structure_ai.CLASSIFY_MARKER).read_text())
            self.assertEqual(marker['candidate_response'], value)
            self.assertIn('每空限填一词', marker['candidate_response']['units'][0]['stem'])
            self.assertEqual(marker['unit_diagnostics'][0]['source_span'], {'start_line': 2, 'end_line': 3})
            self.assertEqual(marker['unit_diagnostics'][0]['status'], 'rejected')
            self.assertEqual((source / '正文.txt').read_text(encoding='utf-8'), raw)

    def test_prompt_explicitly_requires_contiguous_text_and_local_heading_window(self):
        prompt = structure_ai._classify_prompt(self.raw, self.candidate)
        self.assertIn('唯一且连续的原文切片', prompt)
        self.assertIn('每个窗口必须完全落在本大题标题之后', prompt)
        self.assertIn('source_parts 只记录真实源行', prompt)


if __name__ == '__main__':
    unittest.main()
