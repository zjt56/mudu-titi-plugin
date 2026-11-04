"""Answer appendix classification and structure-review human closure."""
import json
from pathlib import Path
import tempfile
import unittest

from workflow import extract, structure_gate
from workbench import server


class AppendixClassificationTest(unittest.TestCase):
    def test_answer_key_tail_is_classified_not_ambiguous(self):
        text = '\n'.join([
            '2022年安徽省初中学业水平考试', 'Ⅸ.单词拼写(共1小题)', '80.May the world be full of peace.',
            'Ⅹ.书面表达(共1小题;满分20分)', '假定你是李辉,请写邮件。', 'Yours,', 'Li Hui',
            'Ⅸ.单词拼写', '80.peace', 'Ⅹ.书面表达', '参考范文：Dear Mike, ...',
            '21.A 【解析】考查动词。', '22.B 【解析】考查不定代词。',
        ])
        structure, body, appendix = extract.infer_structure(text)
        codes = [u['code'] for u in structure['unresolved']]
        self.assertIn('ANSWER_APPENDIX_DETECTED', codes)
        self.assertNotIn('REPEATED_SECTION_APPENDIX', codes)
        self.assertIn('【解析】', appendix)
        self.assertNotIn('【解析】', body)

    def test_unrelated_duplicate_section_stays_ambiguous(self):
        text = '\n'.join(['2022年安徽省中考', 'Ⅰ.单项填空', '1.A. a', '2.B. b',
                          'Ⅰ.单项填空', '1.C. c', '2.D. d'])
        structure, _body, _appendix = extract.infer_structure(text)
        self.assertIn('REPEATED_SECTION_APPENDIX', [u['code'] for u in structure['unresolved']])
        self.assertNotIn('ANSWER_APPENDIX_DETECTED', [u['code'] for u in structure['unresolved']])

    def test_answer_appendix_does_not_block_gate(self):
        text = '\n'.join([
            '2022年安徽省中考', 'Ⅰ.单项填空(共2小题)', '1.A. a  B. b', '2.C. c  D. d',
            'Ⅰ.单项填空', '1.A', '2.C', '解析：示例。',
        ])
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp); src = folder / '01_source'; src.mkdir()
            structure, body, appendix = extract.infer_structure(text)
            (src / '正文.txt').write_text(body, encoding='utf-8')
            (src / 'structure.json').write_text(json.dumps(structure, ensure_ascii=False), encoding='utf-8')
            report = structure_gate.run_gate(folder)
            codes = [b['code'] for b in report['blockers']]
            self.assertNotIn('ANSWER_APPENDIX_DETECTED', codes)


class StructureHumanClosureTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.batch = self.root / 'paper'
        src = self.batch / '01_source'; src.mkdir(parents=True)
        (src / '正文.txt').write_text('Ⅹ.书面表达(共1小题)\n假定你是李辉。\nYours,\nLi Hui\n', encoding='utf-8')
        (src / 'structure.json').write_text(json.dumps({'unresolved': [
            {'code': 'QUESTION_NUMBER_INFERRED', 'line': 1, 'message': '作文无印刷题号'}],
            'sections': [], 'groups': []}, ensure_ascii=False), encoding='utf-8')
        manifest = {'inputs': {'source': {'path': str(self.batch / 'orig.docx')}}, 'stages': {}}
        (self.batch / 'workflow').mkdir(); (self.batch / 'workflow/manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        (self.batch / 'orig.docx').write_bytes(b'original')
        (self.batch / '07_review').mkdir()
        (self.batch / '07_review/manual-review-queue.json').write_text(json.dumps({'queue_version': '1.0', 'items': [
            {'finding_id': 'STRUCT-QUESTION_NUMBER_INFERRED', 'review_type': 'structure',
             'structure_code': 'QUESTION_NUMBER_INFERRED', 'field': 'number', 'status': 'open', 'reason': '作文无题号'}]}, ensure_ascii=False), encoding='utf-8')

    def test_fresh_pass_closes_old_structure_tasks_but_not_label_conflict(self):
        from workflow import runner, manifest as mf
        confirmed = self.batch / '01_source/structure.confirmed.json'
        confirmed.write_text('{}')
        queue_path = self.batch / '07_review/manual-review-queue.json'
        queue = json.loads(queue_path.read_text())
        queue['items'].append({'finding_id': 'LABEL-1', 'review_type': 'knowledge', 'status': 'open'})
        queue_path.write_text(json.dumps(queue))
        report = {'passed': True, 'input_hashes': {p: mf.sha256_file(self.batch / p) for p in
                  ('01_source/正文.txt', '01_source/structure.json', '01_source/structure.confirmed.json')},
                  'original_sha256': mf.sha256_file(self.batch / 'orig.docx')}
        bad = dict(report, passed=False)
        self.assertEqual(runner._reconcile_structure_findings(self.batch, bad), 0)
        stale = dict(report, original_sha256='stale')
        self.assertEqual(runner._reconcile_structure_findings(self.batch, stale), 0)
        self.assertEqual(runner._reconcile_structure_findings(self.batch, report), 1)
        rows = json.loads(queue_path.read_text())['items']
        self.assertEqual(rows[0]['status'], 'fixed')
        self.assertEqual(rows[0]['decided_by'], 'automatic-structure-gate')
        self.assertEqual(rows[1]['status'], 'open')
        self.assertEqual(runner._reconcile_structure_findings(self.batch, report), 0)
        self.assertTrue((self.batch / 'workflow/decisions.jsonl').is_file())

    def test_structure_finding_is_visible_to_human(self):
        finding = {'review_type': 'structure', 'field': 'number', 'status': 'open'}
        self.assertEqual(server.finding_bucket(finding), 'label')

    def test_human_confirmation_binds_source_hash_and_resolves(self):
        server.apply_decision(self.batch, {'finding_id': 'STRUCT-QUESTION_NUMBER_INFERRED', 'status': 'fixed',
                                           'resolution_note': '已核对原卷为第81题'}, actor='reviewer')
        structure = json.loads((self.batch / '01_source/structure.json').read_text())
        issue = next(u for u in structure['unresolved'] if u['code'] == 'QUESTION_NUMBER_INFERRED')
        self.assertTrue(issue['resolved'])
        self.assertEqual(issue['resolution_evidence']['source_sha256'],
                         server.wf_manifest.sha256_file(self.batch / '01_source/正文.txt'))
        self.assertEqual(issue['resolution_evidence']['original_sha256'],
                         server.wf_manifest.sha256_file(self.batch / 'orig.docx'))
        self.assertTrue(issue['resolution_evidence']['evidence'])

    def test_same_code_preserves_distinct_issue_and_resolves_exact_match(self):
        from workflow import runner
        path = self.batch / '01_source/structure.json'
        structure = json.loads(path.read_text())
        structure['unresolved'] = [
            {'code': 'QUESTION_BOUNDARY_MISSING', 'line': 10, 'message': '长对话缺题号'},
            {'code': 'QUESTION_BOUNDARY_MISSING', 'line': 20, 'message': '短文缺题号'}]
        path.write_text(json.dumps(structure, ensure_ascii=False))
        report = {'blockers': [{'code': x['code'], 'source_line': x['line'], 'message': x['message']}
                               for x in structure['unresolved']]}
        runner._record_structure_findings(self.batch, report)
        rows = json.loads((self.batch / '07_review/manual-review-queue.json').read_text())['items']
        matches = [x for x in rows if x.get('structure_code') == 'QUESTION_BOUNDARY_MISSING']
        self.assertEqual(len(matches), 2)
        self.assertNotEqual(matches[0]['finding_id'], matches[1]['finding_id'])
        server.apply_decision(self.batch, {'finding_id': matches[1]['finding_id'], 'status': 'fixed',
            'resolution_note': '对应第20行已核对'}, actor='tester')
        updated = json.loads(path.read_text())['unresolved']
        self.assertFalse(updated[0].get('resolved', False))
        self.assertTrue(updated[1]['resolved'])
        runner._record_structure_findings(self.batch, report)
        self.assertEqual(len([x for x in json.loads((self.batch / '07_review/manual-review-queue.json').read_text())['items']
                              if x.get('structure_code') == 'QUESTION_BOUNDARY_MISSING']), 2)

    def test_identical_messages_resolve_by_line_and_keep_id_when_count_shrinks(self):
        from workflow import runner
        path = self.batch / '01_source/structure.json'
        structure = json.loads(path.read_text())
        structure['unresolved'] = [{'code': 'SAME', 'line': n, 'message': '编号待核对'} for n in (10, 20)]
        path.write_text(json.dumps(structure))
        report = {'blockers': [{'code': 'SAME', 'source_line': n, 'message': '编号待核对'} for n in (10, 20)]}
        runner._record_structure_findings(self.batch, report)
        queue_path = self.batch / '07_review/manual-review-queue.json'
        records = [x for x in json.loads(queue_path.read_text())['items'] if x.get('structure_code') == 'SAME']
        selected = next(x for x in records if x['source_line'] == 20)
        server.apply_decision(self.batch, {'finding_id': selected['finding_id'], 'status': 'fixed',
            'resolution_note': '已核对第20行'}, 'tester')
        issues = json.loads(path.read_text())['unresolved']
        self.assertFalse(issues[0].get('resolved', False))
        self.assertTrue(issues[1]['resolved'])
        runner._record_structure_findings(self.batch, {'blockers': report['blockers'][1:]})
        records2 = [x for x in json.loads(queue_path.read_text())['items'] if x.get('structure_code') == 'SAME']
        self.assertEqual(len(records2), 2)
        self.assertEqual({x['finding_id'] for x in records}, {x['finding_id'] for x in records2})

    def test_rejecting_records_rejection_without_faking_pass(self):
        server.apply_decision(self.batch, {'finding_id': 'STRUCT-QUESTION_NUMBER_INFERRED', 'status': 'rejected'})
        structure = json.loads((self.batch / '01_source/structure.json').read_text())
        issue = next(u for u in structure['unresolved'] if u['code'] == 'QUESTION_NUMBER_INFERRED')
        self.assertFalse(issue['resolved'])


if __name__ == '__main__':
    unittest.main()
