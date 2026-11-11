"""Real temporary-source integration: repeated 1 survives gate through preview.

Only model execution is disabled; source validators, identity assignment, draft,
answer checks, source_ref expansion, label injection and audits all run normally.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from . import agent_tasks, assemble_local, content_rules, extract, preview
from .question_content import audit_batch_source
from .question_draft import build as build_draft
from .question_identity import question_key
from .structure_gate import run_gate
from .upload_content import inspect_content


class ScopedQuestionPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        self.text = '\n'.join([
            'Unit 1', '第一课时 Section A', '基础巩固',
            'Ⅰ.根据首字母及汉语提示补全单词',
            '1. The animal is a z______ (斑马).',
            'Ⅱ.根据汉语意思完成句子,每空一词',
            '1. The light is ______ (黄色的).',
        ])
        self.appendix = '\n'.join([
            'Unit 1', '第一课时 Section A', '基础巩固',
            'Ⅰ.根据首字母及汉语提示补全单词',
            '1.zebra 【解析】斑马的英文是zebra。',
            'Ⅱ.根据汉语意思完成句子,每空一词',
            '1.yellow 【解析】黄色的英文是yellow。',
        ])
        structure, body, _ = extract.infer_structure(self.text)
        self.write('01_source/正文.txt', body)
        self.write('01_source/附录.txt', self.appendix)
        self.write_json('01_source/structure.json', structure)
        # This flag disables external model calls, not any gate or identity helper.
        with patch.dict(os.environ, {'WF_STRUCTURE_AI': '0'}):
            gate = run_gate(self.batch)
        self.assertTrue(gate['passed'], gate.get('blockers'))
        self.units = agent_tasks.confirmed_units(self.batch)
        self.assertEqual([u['numbers'] for u in self.units], [[1], [1]])
        self.assertTrue(all(u['identity_version'] == 'scoped-v1' for u in self.units))
        self.assertEqual([u['type'] for u in self.units], ['单词拼写', '完成句子'])
        self.qids = [question_key(u, 1) for u in self.units]
        self.assertEqual(len(set(self.qids)), 2)
        self.types = {'items': [
            {'unit_id': u['unit_id'], 'type': u['type'], 'subtypes': [subtype],
             'words': 8, 'contentComplete': True, 'evidence': ['正文大题标题']}
            for u, subtype in zip(self.units, ('汉语及首字母', '汉语提示'))]}
        self.context = {'01_source/附录.txt': self.appendix,
                        agent_tasks.ARTIFACTS['types']: self.types}
        self.answers = {'answers': dict(zip(self.qids, ('zebra', 'yellow'))),
                        'evidence': {qid: [f'01_source/附录.txt:{line}']
                                     for qid, line in zip(self.qids, (5, 7))}, 'missing': {}}

    def write(self, relative, text):
        path = self.batch / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def write_json(self, relative, value):
        self.write(relative, json.dumps(value, ensure_ascii=False))

    def explanation_refs(self):
        path = self.batch / '01_source/附录.txt'
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return {'items': [
            {'unit_id': u['unit_id'], 'explanation': {'source_ref': {
                'path': '01_source/附录.txt', 'sha256': digest,
                'start_line': line, 'end_line': line,
                'start_column': self.appendix.splitlines()[line - 1].index('【解析】') + len('【解析】')}},
             'articleSummary': '无', 'subquestions': [], 'evidence': [f'附录 L{line}']}
            for u, line in zip(self.units, (5, 7))]}

    def assemble(self):
        draft = build_draft(self.batch)
        self.assertEqual(draft['issues'], [])
        self.assertTrue(audit_batch_source(self.batch, draft)['passed'])
        bindings = [draft['source_bindings'][u['unit_id']] for u in self.units]
        self.assertLess(bindings[0]['end_line'], bindings[1]['start_line'])
        agent_tasks._validate(self.batch, 'answers', deepcopy(self.answers), self.context, self.units)
        refs = self.explanation_refs()
        # Exercise the actual agent-task entry and its same source_ref consumer.
        accepted = agent_tasks._validate(self.batch, 'explanations', deepcopy(refs), self.context, self.units)
        self.assertEqual(set(accepted), {u['unit_id'] for u in self.units})
        rows = content_rules.validate(refs, self.units,
                                      {r['unit_id']: r for r in self.types['items']}, self.batch)
        self.write_json('03_types/types.json', self.types)
        self.write_json('01_source_ans/answers.json', self.answers)
        self.write_json('04_knowledge/explanations.json', {'items': list(rows.values())})
        self.write_json('04_knowledge/knowledge.json', {'items': []})
        return assemble_local.build(self.batch)

    def test_gate_answers_refs_assembly_source_audit_and_preview_remain_scoped(self):
        payload = self.assemble()
        items = payload['items']
        self.assertEqual([r['numbers'] for r in items], [[1], [1]])
        self.assertEqual([r['question_id'] for r in items], self.qids)
        self.assertEqual([r['answer'] for r in items], ['zebra', 'yellow'])
        self.assertEqual([r['explanation'] for r in items],
                         ['斑马的英文是zebra。', '黄色的英文是yellow。'])
        self.assertIn('斑马', items[0]['stem'])
        self.assertNotIn('黄色', items[0]['stem'])
        self.assertIn('黄色', items[1]['stem'])
        audit = audit_batch_source(self.batch, payload)
        self.assertTrue(audit['passed'], audit['issues'])
        content_audit = inspect_content(self.batch, payload, strict=True)
        self.assertEqual(content_audit['errors'], [])
        self.write_json('06_batch/batch.json', payload)
        slices = ['\n'.join(preview.source_slice(self.batch, [1], unit_id=u['unit_id']))
                  for u in self.units]
        self.assertIn('斑马', slices[0]); self.assertNotIn('黄色', slices[0])
        self.assertIn('黄色', slices[1]); self.assertNotIn('斑马', slices[1])
        self.assertEqual(preview.source_slice(self.batch, [1]), [])

    def test_same_one_wrong_answer_reference_and_stem_each_fail_in_real_pipeline(self):
        wrong = deepcopy(self.answers)
        wrong['answers'][self.qids[0]] = 'yellow'
        wrong['evidence'][self.qids[0]] = ['01_source/附录.txt:7']
        # yellow exists verbatim globally, so failure must come from scope proof.
        with self.assertRaisesRegex(ValueError, 'scoped answer source'):
            agent_tasks._validate(self.batch, 'answers', wrong, self.context, self.units)
        numeric = {'answers': {'1': 'zebra'}, 'evidence': {'1': ['附录 L5']}, 'missing': {}}
        with self.assertRaisesRegex(ValueError, 'not_in_batch|uncovered'):
            agent_tasks._validate(self.batch, 'answers', numeric, self.context, self.units)
        refs = self.explanation_refs()
        refs['items'][0]['explanation'] = deepcopy(refs['items'][1]['explanation'])
        with self.assertRaisesRegex(ValueError, 'source_ref.*source scope'):
            agent_tasks._validate(self.batch, 'explanations', refs, self.context, self.units)
        payload = self.assemble()
        payload['items'][0]['stem'] = payload['items'][1]['stem']
        audit = audit_batch_source(self.batch, payload)
        self.assertFalse(audit['passed'])
        self.assertTrue(any(row.get('unit_id') == self.units[0]['unit_id'] for row in audit['issues']))


if __name__ == '__main__':
    unittest.main()
