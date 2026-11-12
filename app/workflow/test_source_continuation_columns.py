"""A right-column item can start after an unnumbered continuation line."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import extract, structure_gate, question_content, question_draft, preview, upload_payload
from workflow.source_anchors import project_numbered
from workbench.server import source_blocks


class ContinuationColumnsTest(unittest.TestCase):
    TEXT = ('Starter Unit 2 Keep Tidy!\n第二课时 Pronunciation\n'
            'Ⅰ.根据图片及所给音标写出下列单词所缺的字母\n'
            '1.[IMG cake.jpeg]\nc<u>　　</u>/eɪ/ke\t2.[IMG nose.jpeg]\n'
            'n<u>　　</u>/əʊ/se\n3.[IMG bike.jpeg]\n'
            'b<u>　　</u>/aɪ/ke\t4.[IMG meat.jpeg]\nm<u>　　</u>/iː/t\n'
            '5.[IMG computer.jpeg]\ncomp<u>　　</u>/juː/ter\n'
            'Ⅱ.找出下列每组单词中画线部分读音不同的一项\n'
            '(　)1.A.make B.take C.father D.game\n')

    def test_single_right_column_token_splits_but_keeps_left_continuation(self):
        left = 'c<u>　　</u>/eɪ/ke'
        self.assertEqual(project_numbered([left + '\t2.[IMG nose.jpeg]']),
                         [left, '2.[IMG nose.jpeg]'])
        self.assertEqual(project_numbered(['Class 2. is upstairs.']), ['Class 2. is upstairs.'])
        self.assertEqual(project_numbered(['1.Alpha\t2.Beta']), ['1.Alpha', '2.Beta'])

    def test_each_child_retains_its_own_image_and_following_transcription(self):
        structure, body, _ = extract.infer_structure(self.TEXT)
        units = structure_gate._build_units(structure['sections'], structure['groups'], body.splitlines())
        unit = units[0]
        self.assertEqual(unit['numbers'], [1, 2, 3, 4, 5])
        self.assertEqual(structure_gate._scoped_source_errors(units, structure['sections'], body.splitlines()), [])
        audited = question_content.audit_source_unit(unit)
        self.assertTrue(audited['passed'], audited['issues'])
        for number, own, other, suffix in [(1, 'cake.jpeg', 'nose.jpeg', '/eɪ/ke'),
                                           (2, 'nose.jpeg', 'cake.jpeg', '/əʊ/se'),
                                           (3, 'bike.jpeg', 'meat.jpeg', '/aɪ/ke'),
                                           (4, 'meat.jpeg', 'bike.jpeg', '/iː/t')]:
            part = audited['questions'][number]
            self.assertTrue(part['found'])
            self.assertIn(own, part['stem'])
            self.assertNotIn(other, part['stem'])
            self.assertIn(suffix, part['stem'])

    def test_gate_draft_source_blocks_and_payload_share_complete_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / '01_source'
            (folder / 'media').mkdir(parents=True)
            for name in ('cake', 'nose', 'bike', 'meat', 'computer'):
                (folder / 'media' / (name + '.jpeg')).write_bytes(b'nonempty synthetic image')
            structure, body, appendix = extract.infer_structure(self.TEXT)
            (folder / '正文.txt').write_text(body)
            (folder / 'structure.json').write_text(json.dumps(structure))
            with patch('workflow.structure_ai.auto_classify', return_value={'ran': False}):
                report = structure_gate.run_gate(root)
            self.assertTrue(report['passed'], report['blockers'])
            self.assertFalse(any(b['code'] in {'NUMBERING_RESTART', 'QUESTION_NOT_FOUND'} for b in report['blockers']))
            draft = question_draft.build(root)
            self.assertEqual(draft['issues'], [])
            first = draft['items'][0]
            self.assertEqual(len(first['subquestions']), 5)
            self.assertIn('/əʊ/se', first['subquestions'][1]['stem'])
            self.assertIn('/iː/t', first['subquestions'][3]['stem'])
            units = json.loads((folder / 'structure.confirmed.json').read_text())['units']
            chunk = preview.source_slice(root, units[0]['numbers'], unit_id=units[0]['unit_id'])
            blocks = json.dumps(source_blocks(chunk), ensure_ascii=False)
            for image in ('cake.jpeg', 'nose.jpeg', 'meat.jpeg'):
                self.assertIn(image, blocks)
            for row, unit in zip(draft['items'], units):
                row['question_type'] = unit['type']
            wire = upload_payload.build_payload({'items': deepcopy(draft['items'])}, batch_dir=root)['payload']['items']
            self.assertEqual(len(wire[0]['subquestions']), 5)
            self.assertIn('/əʊ/se', wire[0]['subquestions'][1]['stem'])
            self.assertIn('/iː/t', wire[0]['subquestions'][3]['stem'])


if __name__ == '__main__':
    unittest.main()
