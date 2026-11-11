import copy
import io
import json
import tempfile
import unittest
from contextlib import nullcontext, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from workflow import contracts, manifest as mf


class OptionalInputSkipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / '01_source').mkdir(parents=True)
        self.manifest = {'batch_dir': str(self.root)}

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
                        encoding='utf-8')
        return path

    def scoped_fixture(self):
        unit = {'unit_id': 'sec-a-u2', 'section_id': 'sec-a', 'identity_version': 'scoped-v1',
                'source_section': {'title': 'Ⅰ.语音题', 'start_line': 1, 'end_line': 4},
                'source_span': {'start_line': 2, 'end_line': 4, 'basis': 'cleaned_source'},
                'question_ids': {str(n): f'sec-a-l{n+1}-q{n}' for n in (1, 2, 3)},
                'unit_kind': 'group', 'type': '语音题', 'numbers': [1, 2, 3]}
        self.write('01_source/structure.confirmed.json', {'identity_version': 'scoped-v1', 'units': [unit]})
        self.write('01_source/正文.txt', 'Ⅰ.语音题\n1. Aa\n2. Bb\n3. Cc\n')
        self.write('03_types/types.json', {'items': [{'unit_id': unit['unit_id'],
            'type': '语音题', 'subtypes': [], 'words': 3,
            'contentComplete': True, 'evidence': ['正文第1–4行']}]})
        old = {'items': [{'unit_id': unit['unit_id'], 'explanation': '无', 'articleSummary': '无',
                         'subquestions': [{'number': n, 'explanation': '无'} for n in range(1, 9)],
                         'evidence': ['旧结构无解析']}]}
        path = self.write('04_knowledge/explanations.json', old)
        manifest = mf.new_manifest('skip-test', self.root, {}, {})
        mf.touch_stage(manifest, 'explanations', mf.SKIPPED, notes='旧结构跳过')
        mf.save(self.root, manifest)
        return unit, old, path, manifest

    def test_answers_skipped_without_any_answer_source(self):
        self.assertIn('未提供答案来源', mf.skip_reason(self.manifest, contracts.STAGE_BY_ID['answers']))

    def test_answers_run_when_appendix_has_text(self):
        (self.root / '01_source/附录.txt').write_text('1.A 2.B', encoding='utf-8')
        self.assertEqual(mf.skip_reason(self.manifest, contracts.STAGE_BY_ID['answers']), '')

    def test_answers_run_when_answer_volume_has_text(self):
        (self.root / '01_source_ans').mkdir()
        (self.root / '01_source_ans/正文.txt').write_text('1.A', encoding='utf-8')
        self.assertEqual(mf.skip_reason(self.manifest, contracts.STAGE_BY_ID['answers']), '')

    def test_explanations_skipped_without_markers_and_placeholder_written(self):
        from workflow import optional_inputs
        (self.root / '01_source/附录.txt').write_text('1.A 2.B', encoding='utf-8')
        self.assertIn('未提供原解析', mf.skip_reason(self.manifest, contracts.STAGE_BY_ID['explanations']))
        (self.root / '01_source/structure.confirmed.json').write_text(
            '{"units":[{"unit_kind":"standalone","numbers":[1]},{"unit_kind":"group","numbers":[2,3]}]}',
            encoding='utf-8')
        self.write('03_types/types.json', {'items': [
            {'unit_id': uid, 'type': '单项填空', 'subtypes': ['四个选项'], 'evidence': ['正文']}
            for uid in ('q-1', 'group-2-3')]})
        path = optional_inputs.write_empty_explanations(self.root)
        data = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual([row['explanation'] for row in data['items']], ['无', '无'])
        self.assertEqual([row['subquestions'] for row in data['items']], [[], [{'number': 2, 'explanation': '无'}, {'number': 3, 'explanation': '无'}]])

    def test_explanations_run_when_marker_present(self):
        (self.root / '01_source/附录.txt').write_text('1.A 【解析】because', encoding='utf-8')
        self.assertEqual(mf.skip_reason(self.manifest, contracts.STAGE_BY_ID['explanations']), '')

    def test_historical_scoped_skip_rebuilds_current_children_and_assembles(self):
        from workflow import agent_tasks, assemble_local, handoff, runner
        from types import SimpleNamespace
        unit, old, path, manifest = self.scoped_fixture()
        self.assertFalse(handoff.inspect_stage(self.root, manifest, 'explanations')['passed'])
        args = SimpleNamespace(stage='explanations', until=None, dry_run=False, force=False)
        with patch('workflow.local_agent._agent_lock', return_value=nullcontext()), \
             patch('workflow.local_agent.invoke', side_effect=AssertionError('no model')), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(runner._cmd_run_locked(self.root, args), 0)
        current = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual([c['number'] for c in current['items'][0]['subquestions']], [1, 2, 3])
        self.assertEqual(json.loads(path.with_suffix('.json.bak').read_text(encoding='utf-8')), old)
        manifest = mf.load(self.root)
        self.assertEqual(manifest['stages']['explanations']['status'], mf.SKIPPED)
        self.assertTrue(handoff.inspect_stage(self.root, manifest, 'explanations')['passed'])
        for sid in contracts.upstream_of('explanations'):
            mf.touch_stage(manifest, sid, mf.COMPLETED)
        self.assertTrue(mf.dependency_satisfied(manifest, 'explanations'))
        base = {'items': [dict(copy.deepcopy(unit), id=unit['unit_id'], knowledge=[],
                    subquestions=[{'number': n, 'answer': '', 'knowledge': []} for n in (1, 2, 3)])]}
        self.write('06_batch/batch.json', base)
        self.write('04_knowledge/knowledge.json', {'items': [{'unit_id': unit['unit_id'],
            'knowledge': [], 'subquestions': [{'number': n, 'knowledge': []} for n in (1, 2, 3)]}]})
        assembled = assemble_local.build(self.root)
        self.assertEqual([c['explanation'] for c in assembled['items'][0]['subquestions']], ['无'] * 3)
        agent_tasks._children(assembled['items'][0], unit)
        with patch('workflow.local_agent._agent_lock', return_value=nullcontext()), \
             patch.object(mf, 'save', side_effect=AssertionError('qualified skip must not rewrite')):
            self.assertTrue(runner._skip_agent_stage(self.root, manifest, contracts.STAGE_BY_ID['explanations']))

    def test_missing_types_cannot_mark_skip_or_replace_old_artifact(self):
        from workflow import runner
        _, _, path, manifest = self.scoped_fixture()
        (self.root / '03_types/types.json').unlink()
        before = path.read_bytes()
        mf.touch_stage(manifest, 'explanations', mf.PENDING)
        mf.save(self.root, manifest)
        with patch('workflow.local_agent._agent_lock', return_value=nullcontext()):
            self.assertFalse(runner._skip_agent_stage(self.root, manifest, contracts.STAGE_BY_ID['explanations']))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(mf.load(self.root)['stages']['explanations']['status'], mf.PENDING)

    def test_real_old_content_is_never_replaced_by_absence(self):
        from workflow import optional_inputs, runner
        _, old, path, manifest = self.scoped_fixture()
        old['items'][0]['subquestions'][0]['explanation'] = '真实原解析必须保留。'
        self.write('04_knowledge/explanations.json', old)
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'must be preserved'):
            optional_inputs.write_empty_explanations(self.root)
        with patch('workflow.local_agent._agent_lock', return_value=nullcontext()):
            self.assertFalse(runner._skip_agent_stage(self.root, manifest, contracts.STAGE_BY_ID['explanations']))
        self.assertEqual(path.read_bytes(), before)

    def test_original_body_content_rejected_by_normal_omission_validator(self):
        from workflow import optional_inputs
        _, _, path, _ = self.scoped_fixture()
        self.write('01_source/正文.txt', 'Ⅰ.语音题\n1. Aa 【解析】原卷的真实解析。\n2. Bb\n3. Cc\n')
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'CONTENT_SCOPE_UNBOUND|ORIGINAL_EXPLANATION_OMITTED'):
            optional_inputs.write_empty_explanations(self.root)
        self.assertEqual(path.read_bytes(), before)

    def test_source_marker_prevents_overwriting_even_old_placeholders(self):
        from workflow import optional_inputs
        _, _, path, _ = self.scoped_fixture()
        self.write('01_source/附录.txt', '1. A 【解析】原文。')
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'source exists'):
            optional_inputs.write_empty_explanations(self.root)
        self.assertEqual(path.read_bytes(), before)

    def test_optional_flags(self):
        self.assertTrue(contracts.STAGE_BY_ID['answers'].optional)
        self.assertTrue(contracts.STAGE_BY_ID['explanations'].optional)
