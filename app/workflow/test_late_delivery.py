"""Late assembly owns actual final delivery; all fixtures are temporary."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import agent_tasks as tasks, batch_fill, handoff, assemble_local, manifest as mf, contracts
from workflow.test_agent_tasks import explanations_of


class LateDeliveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='late-delivery-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        source = '1. She ______ a book. A. reads B. read C. reading D. to read'
        self.put('01_source/正文.txt', source)
        self.put('01_source/附录.txt', '答案: 1. A')
        self.put('01_source/structure.json', {'sections': [{'type': '单项填空', 'questions': [1]}], 'groups': []})
        self.put('01_source/structure.confirmed.json', {'units': [{
            'unit_kind': 'standalone', 'numbers': [1], 'type': '单项填空',
            'stem': source, 'source_span': {'basis': 'ai_classified', 'start_line': 1, 'end_line': 1}}]})
        batch_fill.initialize(self.root)
        self.base = self.get(batch_fill.BATCH_BASE)
        self.put('06_batch/content.snapshot.json', self.base)
        meta = copy.deepcopy(self.base)
        meta['items'][0]['filterMetadata'].update(difficulty='适中', categories=['未提供'])
        self.put('06_batch/batch_meta.base.json', meta)
        marked = copy.deepcopy(meta)
        marked['items'][0]['stem'] = '<u>She</u> ______ a book.'
        self.put('06_batch/batch_meta.json', marked)
        self.types = {'items': [{'unit_id': 'q-1', 'type': '单项填空', 'subtypes': ['四个选项'],
                                'words': 5, 'contentComplete': True, 'evidence': ['正文 L1']}]}
        self.answers = {'answers': {'1': 'A'}, 'missing': {}, 'evidence': {'1': ['附录 L1']},
                        'difficulty': {'1': '容易'}, 'difficultyEvidence': {'1': ['正文 L1']}}
        self.knowledge = {'items': [{'unit_id': 'q-1', 'knowledge': ['词汇/词汇辨析/动词辨析'],
                                    'answer': 'A', 'explanation': '无', 'subquestions': [], 'evidence': ['正文 L1']}]}

    def put(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def get(self, relative):
        return json.loads((self.root / relative).read_text(encoding='utf-8'))

    def fills(self):
        for stage, payload in [('types', self.types), ('answers', self.answers),
                               ('explanations', explanations_of(self.knowledge)), ('knowledge', self.knowledge)]:
            self.put(tasks.ARTIFACTS[stage], payload)
            batch_fill.sync(self.root, stage, payload)

    def result(self):
        return {'status': 'completed', 'notes': [], 'findings': [],
                'artifacts': {tasks.ARTIFACTS['assemble']: assemble_local.build(self.root)}}

    def snapshot(self):
        return {rel: (self.root / rel).read_bytes() for rel in (
            batch_fill.BATCH, batch_fill.BATCH_BASE, batch_fill.STATE,
            '06_batch/content.snapshot.json', '06_batch/batch_meta.base.json', '06_batch/batch_meta.json')}

    def test_early_views_accept_without_future_labels_or_explanations(self):
        for stage in ('meta', 'underlines'):
            report = handoff.inspect_stage(self.root, {}, stage)
            self.assertTrue(report['passed'], report)
        self.assertFalse((self.root / tasks.ARTIFACTS['answers']).exists())
        self.assertFalse((self.root / tasks.ARTIFACTS['explanations']).exists())

    def test_early_views_reject_source_identity_and_nested_evidence_changes(self):
        for field, value in [('stem', 'Foreign source'), ('numbers', [2]), ('unit_id', 'other')]:
            with self.subTest(field=field):
                view = self.get('06_batch/batch_meta.json')
                before = copy.deepcopy(view)
                view['items'][0][field] = value
                self.put('06_batch/batch_meta.json', view)
                self.assertFalse(handoff.inspect_stage(self.root, {}, 'underlines')['passed'])
                self.put('06_batch/batch_meta.json', before)
        before = {'items': [{'unit_id': 'q', 'evidence': {'answer': 'source coordinate'}}]}
        after = copy.deepcopy(before)
        after['items'][0]['evidence']['answer'] = 'foreign coordinate'
        with self.assertRaisesRegex(ValueError, 'content snapshot'):
            handoff._assert_meta_projection(before, after)

    def test_accepted_fill_reaches_final_consumer_preserving_marks_and_metadata(self):
        self.fills()
        stable = self.snapshot()
        report = tasks.accept_result(self.root, 'assemble', self.result())
        self.assertTrue(report['completed'], report)
        self.assertIn('06_batch/batch_meta.json', report['artifacts'])
        final = self.get('06_batch/batch_meta.json')
        self.assertEqual(final, self.get(batch_fill.BATCH))
        row = final['items'][0]
        self.assertEqual(row['stem'], '<u>She</u> ______ a book.')
        self.assertEqual(row['answer'], 'A')
        self.assertEqual(row['explanation'], '无')
        self.assertTrue(row['knowledgeIds'])
        self.assertEqual(row['filterMetadata']['categories'], ['未提供'])
        self.assertEqual(row['difficulty'], '容易')
        self.assertEqual(row['filterMetadata']['difficulty'], '容易')
        self.assertEqual(self.get(batch_fill.STATE)['mode'], 'finalized')
        batch_fill._managed(self.root)
        self.assertTrue(handoff.inspect_stage(self.root, {}, 'underlines')['passed'])
        self.assertTrue(handoff.inspect_stage(self.root, {}, 'assemble')['passed'])
        for relative in (batch_fill.BATCH_BASE, '06_batch/content.snapshot.json', '06_batch/batch_meta.base.json'):
            self.assertEqual((self.root / relative).read_bytes(), stable[relative])

    def test_validate_only_keeps_all_files_exact(self):
        self.fills()
        response = self.result()
        before = self.snapshot()
        self.assertTrue(tasks.accept_result(self.root, 'assemble', response, validate_only=True)['completed'])
        self.assertEqual(self.snapshot(), before)

    def test_assemble_still_rejects_missing_final_fills(self):
        early = self.get('06_batch/batch_meta.json')
        with self.assertRaises((ValueError, TypeError, KeyError)):
            handoff._batch(self.root, 'assemble', early)

    def test_needs_review_assembly_never_installs_final_or_seals(self):
        self.fills()
        response = self.result()
        response['status'] = 'needs_review'
        before = self.snapshot()
        report = tasks.accept_result(self.root, 'assemble', response)
        self.assertFalse(report['completed'])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.get(batch_fill.STATE)['mode'], 'filling')

    def test_changed_source_during_acceptance_does_not_install(self):
        self.fills()
        response = self.result()
        before = self.snapshot()
        original = tasks._assembly_delivery_files
        def race(root, payload):
            files = original(root, payload)
            self.put('01_source/正文.txt', 'Changed original')
            return files
        with patch.object(tasks, '_assembly_delivery_files', side_effect=race):
            with self.assertRaisesRegex(ValueError, 'inputs changed'):
                tasks.accept_result(self.root, 'assemble', response)
        self.assertEqual(self.snapshot(), before)

    def test_real_current_receipt_refreshes_underline_delivery_after_assembly(self):
        spec = contracts.STAGE_BY_ID['underlines']
        manifest = {'workflow_id': 'receipt-fixture', 'batch_dir': str(self.root), 'inputs': {},
                    'stages': {stage.id: {'status': mf.PENDING} for stage in contracts.STAGES}}
        manifest['stages']['underlines']['status'] = mf.COMPLETED
        manifest['stages']['underlines']['output_hashes'] = mf.output_hashes(self.root, spec)
        mf.record_semantics(self.root, manifest, spec)
        self.put('workflow/manifest.json', manifest)
        self.fills()
        report = tasks.accept_result(self.root, 'assemble', self.result())
        self.assertTrue(report['completed'], report)
        proof = self.get('workflow/late-assembly-delivery.json')
        self.assertEqual(proof['final_sha256'], mf.sha256_file(self.root / '06_batch/batch_meta.json'))
        self.assertEqual(proof['before_underlines_outputs'], manifest['stages']['underlines']['semantic_outputs'])
        self.assertTrue(mf._refresh_assembled_underline_delivery(self.root, manifest))
        self.assertFalse(mf.is_stale(self.root, manifest, spec)[0])

    def test_stale_underline_input_never_emits_refresh_receipt(self):
        spec = contracts.STAGE_BY_ID['underlines']
        manifest = {'workflow_id': 'receipt-stale-fixture', 'batch_dir': str(self.root), 'inputs': {},
                    'stages': {stage.id: {'status': mf.PENDING} for stage in contracts.STAGES}}
        manifest['stages']['underlines']['status'] = mf.COMPLETED
        manifest['stages']['underlines']['output_hashes'] = mf.output_hashes(self.root, spec)
        mf.record_semantics(self.root, manifest, spec)
        self.put('workflow/manifest.json', manifest)
        meta = self.get('06_batch/batch_meta.base.json')
        meta['items'][0]['filterMetadata']['categories'] = ['同步练习']
        self.put('06_batch/batch_meta.base.json', meta)
        self.fills()
        self.assertIsNone(tasks._assembly_refresh_proof(self.root))
        self.assertTrue(tasks.accept_result(self.root, 'assemble', self.result())['completed'])
        self.assertFalse((self.root / 'workflow/late-assembly-delivery.json').exists())

    def test_install_failure_rolls_back_batch_final_and_state(self):
        self.fills()
        response = self.result()
        before = self.snapshot()
        replace = os.replace
        def fail(source, target):
            if Path(target) == self.root / '06_batch/batch_meta.json' and Path(source).name.startswith('.agent-'):
                raise OSError('simulated final installation failure')
            return replace(source, target)
        with patch.object(tasks.os, 'replace', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'simulated final'):
                tasks.accept_result(self.root, 'assemble', response)
        self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
