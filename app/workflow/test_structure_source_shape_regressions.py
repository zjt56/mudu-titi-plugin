"""Real structure gates on isolated source fixtures; never use user batches."""
from copy import deepcopy
import json
from pathlib import Path
import os
import subprocess
import sys
import zipfile
from xml.sax.saxutils import escape
import tempfile
import unittest
from unittest.mock import patch

from .extract import infer_structure, same_line_numbers
from .question_content import legitimate_source_shape
from .structure_gate import run_gate, _build_units, _scoped_source_errors, _proven_scoped_restart


class StructureSourceShapeRegressions(unittest.TestCase):
    POEM = ('Unit 3\n第四课时 Project\nⅠ.写出下列古诗画线句中表示数字的英文单词\n'
            '[TABLE 1]\n《咏雪》<br>郑燮<br><u>一片两片三四片</u><br>五六七八九十片。\n[/TABLE]\n<u>　</u>')

    def gate(self, text):
        structure, body, _ = infer_structure(text)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / '01_source').mkdir()
            (root / '01_source/正文.txt').write_text(body, encoding='utf-8')
            (root / '01_source/structure.json').write_text(json.dumps(structure), encoding='utf-8')
            with patch('workflow.structure_ai.auto_classify', return_value={'ran': False}):
                report = run_gate(root)
            units = json.loads((root / '01_source/structure.confirmed.json').read_text())['units']
        return structure, body, report, units

    def test_poem_table_answer_lines_keep_exact_unnumbered_unit(self):
        _, body, report, units = self.gate(self.POEM)
        self.assertTrue(report['passed'], report['blockers'])
        self.assertEqual(len(units), 1)
        unit = units[0]
        self.assertEqual(unit['stem'], '\n'.join(body.splitlines()[3:]))
        self.assertEqual(unit['numbers'], [1])
        self.assertEqual(unit['source_span']['printed_numbers'], [])
        self.assertEqual(unit['source_span']['number_binding'], 'unnumbered_section')
        self.assertIsNone(unit['original_number'])
        self.assertFalse(unit['printed_number'])
        self.assertEqual(legitimate_source_shape(unit), 'unnumbered_table_response')

    def test_familiar_word_single_unnumbered_response_is_source_bound(self):
        text = ('Unit 3\n第一课时 Section A\n写出下面画线词的汉语意思\n'
                'Lang Lang is famous in the <u>field</u> of music.\t<u>　</u>\n'
                'Pronunciation\nⅠ.找出下列每组单词中画线部分读音不同的一项\n'
                '( )1.A.a B.b C.c D.d')
        _, _, report, units = self.gate(text)
        self.assertTrue(report['passed'], report['blockers'])
        unit = units[0]
        self.assertEqual(unit['type'], '翻译')
        self.assertEqual(unit['unit_kind'], 'standalone')
        self.assertEqual(unit['source_span']['printed_numbers'], [])
        self.assertEqual(unit['source_span']['number_binding'], 'unnumbered_section')
        self.assertIsNone(unit['original_number'])
        self.assertFalse(unit['printed_number'])
        self.assertNotIn('Pronunciation', unit['stem'])
        self.assertEqual(legitimate_source_shape(unit), 'unnumbered_no_image')

    def test_single_unnumbered_response_survives_draft_and_preview(self):
        from . import preview, question_draft, upload_numbering
        text = ('Unit 3\n第一课时 Section A\n写出下面画线词的汉语意思\n'
                'Lang Lang is famous in the <u>field</u> of music.\t<u>　</u>')
        structure, body, _ = infer_structure(text)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / '01_source').mkdir()
            (root / '01_source/正文.txt').write_text(body, encoding='utf-8')
            (root / '01_source/structure.json').write_text(json.dumps(structure), encoding='utf-8')
            with patch('workflow.structure_ai.auto_classify', return_value={'ran': False}):
                report = run_gate(root)
            self.assertTrue(report['passed'], report['blockers'])
            units = json.loads((root / '01_source/structure.confirmed.json').read_text(encoding='utf-8'))['units']
            draft = question_draft.build(root)
            self.assertEqual(draft['issues'], [])
            self.assertEqual(len(draft['items']), 1)
            self.assertIn('Lang Lang', draft['items'][0]['stem'])
            self.assertIn('<u>field</u>', draft['items'][0]['stem'])
            shown = preview.source_slice(root, [1], unit_id=units[0]['unit_id'])
            self.assertEqual(shown, [body.splitlines()[-1]])
            projected = upload_numbering.project_batch({'items': draft['items']}, root)
            self.assertEqual(projected['errors'], [])
            self.assertIsNone(projected['items'][0]['numberStart'])
            self.assertIsNone(projected['items'][0]['numberEnd'])

    def test_unnumbered_response_does_not_invent_count_or_missing_content(self):
        for content in ('<u>　</u>', 'Lang Lang is famous.',
                        'One ____ and two ____', 'One sentence.\nTwo <u>　</u>', '[IMG word.jpeg]\nOne <u>　</u>'):
            with self.subTest(content=content):
                _, _, report, units = self.gate('Unit 3\n第一课时 Section A\n写出下面画线词的汉语意思\n' + content)
                self.assertFalse(report['passed'], report)
                self.assertEqual(units, [])

    def test_consecutive_unnumbered_items_keep_separate_source_identities(self):
        from . import preview, question_draft, upload_numbering
        text = ('Unit 3\n第一课时 Section A\n【文化拾趣】\n写出下面画线词的汉语意思\n'
                'She is in the <u>field</u>.\t<u>　</u>\n'
                'It is a <u>piece</u> of cake.\t____')
        structure, body, _ = infer_structure(text)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / '01_source').mkdir()
            (root / '01_source/正文.txt').write_text(body, encoding='utf-8')
            (root / '01_source/structure.json').write_text(json.dumps(structure), encoding='utf-8')
            with patch('workflow.structure_ai.auto_classify', return_value={'ran': False}):
                report = run_gate(root)
            self.assertTrue(report['passed'], report['blockers'])
            units = json.loads((root / '01_source/structure.confirmed.json').read_text(encoding='utf-8'))['units']
            self.assertEqual(len(units), 2)
            self.assertEqual(len({u['unit_id'] for u in units}), 2)
            self.assertEqual(len({u['question_ids']['1'] for u in units}), 2)
            self.assertTrue(all(u['numbers'] == [1] and u['source_span']['printed_numbers'] == [] for u in units))
            draft = question_draft.build(root)
            self.assertEqual(draft['issues'], [])
            self.assertEqual(len(draft['items']), 2)
            for unit, word, other in zip(units, ('field', 'piece'), ('piece', 'field')):
                shown = '\n'.join(preview.source_slice(root, [1], unit_id=unit['unit_id']))
                self.assertIn(word, shown)
                self.assertNotIn(other, shown)
            projected = upload_numbering.project_batch({'items': draft['items']}, root)
            self.assertEqual(projected['errors'], [])
            self.assertTrue(all(i['numberStart'] is None and i['numberEnd'] is None for i in projected['items']))

    def test_wrapped_unnumbered_response_is_one_complete_unit(self):
        _, _, report, units = self.gate('Unit 3\n第一课时 Section A\n写出下面画线词的汉语意思\n'
                                       'Lang Lang is famous in the\n<u>field</u> of music.\t____')
        self.assertTrue(report['passed'], report['blockers'])
        self.assertEqual(len(units), 1)
        self.assertIn('the\n<u>field</u>', units[0]['stem'])

    def test_prose_age_is_not_printed_number(self):
        text = ('Unit 3\n第四课时 Project\nⅠ.任务型阅读\n'
                '1.<u>　　　　</u> Sally.She is 15.She is not tidy.\n'
                '2.<u>　　　　</u> Ms Lin is 28 years old.')
        structure, _, report, units = self.gate(text)
        self.assertEqual(structure['sections'][0]['questions'], [1, 2])
        self.assertTrue(report['passed'], report['blockers'])
        self.assertEqual(units[0]['numbers'], [1, 2])

    def test_true_columns_and_numbered_blanks_survive(self):
        for separator in ('\t', '　　', '    '):
            self.assertEqual(same_line_numbers('1.Alpha ____' + separator + '2.Beta ____', 1), [2])
        _, _, report, units = self.gate('Unit 3\n第四课时 Project\nⅠ.补全对话\nA:1.<u>　</u>\nB:2.<u>　</u>')
        self.assertTrue(report['passed'], report['blockers'])
        self.assertEqual(units[0]['numbers'], [1, 2])

    def test_restart_resolves_despite_unrelated_empty_section(self):
        text = ('Unit 3\n第四课时 Project\nⅠ.写出下列古诗画线句中表示数字的英文单词\n\n'
                'Ⅱ.根据汉语意思完成句子\n1.Alpha ____\nⅢ.根据汉语意思完成句子\n1.Beta ____')
        _, _, report, _ = self.gate(text)
        codes = {p['code'] for p in report['blockers']}
        self.assertFalse(report['passed'])
        self.assertIn('SOURCE_SECTION_UNCOVERED', codes)
        self.assertNotIn('NUMBERING_RESTART', codes)

    def test_stale_occurrence_does_not_resolve_restart(self):
        text = 'Unit 3\n第四课时 Project\nⅠ.根据汉语意思完成句子\n1.A ____\nⅡ.根据汉语意思完成句子\n1.B ____'
        structure, body, _ = infer_structure(text)
        lines = body.splitlines()
        units = _build_units(structure['sections'], structure['groups'], lines)
        self.assertTrue(_proven_scoped_restart(5, units, structure['sections'], lines, []))
        units[-1]['source_section']['occurrence'] = 999
        self.assertFalse(_proven_scoped_restart(5, units, structure['sections'], lines, []))

    def test_duplicate_same_scope_still_blocks(self):
        text = 'Unit 3\n第四课时 Project\nⅠ.根据汉语意思完成句子\n1.A ____\n1.B ____'
        _, _, report, _ = self.gate(text)
        self.assertFalse(report['passed'])
        self.assertIn('SOURCE_DUPLICATE_NUMBERS', {p['code'] for p in report['blockers']})

    def test_table_missing_body_or_closure_is_not_legal_shape(self):
        for body in ('[TABLE 1]\n\n[/TABLE]\n<u>　</u>', '[TABLE 1]\nReal poem\n<u>　</u>',
                     '[TABLE 1]\nReal poem\n[/TABLE]', '请填写下列内容', '<u>　</u>'):
            text = 'Unit 3\n第四课时 Project\nⅠ.写出下列古诗画线句中表示数字的英文单词\n' + body
            with self.subTest(body=body):
                _, _, report, units = self.gate(text)
                self.assertFalse(report['passed'], report)
                self.assertEqual(units, [])

    def test_extract_direct_script_entry_keeps_columns_without_prose_age(self):
        # runner executes this file as a script, so __package__ is empty. Use
        # that actual __main__ entry in a child process, with only model dispatch
        # disabled; native Word extraction and the real parser still execute.
        text = ('Unit 3\n第四课时 Project\nⅠ.任务型阅读\n'
                '1.________ Sally.She is 15.She is not tidy.\n'
                'Ⅱ.根据汉语意思完成句子\n1.Alpha ____\t2.Beta ____')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source.docx'
            xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                   + ''.join('<w:p><w:r><w:t xml:space="preserve">' + escape(line)
                             + '</w:t></w:r></w:p>' for line in text.splitlines()) + '</w:body></w:document>')
            with zipfile.ZipFile(source, 'w') as archive:
                archive.writestr('word/document.xml', xml)
            (root / 'workflow').mkdir()
            (root / 'workflow/manifest.json').write_text(json.dumps({'inputs': {'source': {'path': str(source)}}}))
            script = Path(__file__).with_name('extract.py').resolve()
            code = ('import runpy,sys\nfrom unittest.mock import patch\n'
                    'sys.argv=[sys.argv[1],"--batch-dir",sys.argv[2]]\n'
                    'with patch("workflow.agent.configured",return_value=False):\n'
                    ' runpy.run_path(sys.argv[0],run_name="__main__")\n')
            app = str(script.parents[1])
            env = {**os.environ, 'PYTHONPATH': app, 'PYTHONDONTWRITEBYTECODE': '1', 'WF_STRUCTURE_AI': '0'}
            result = subprocess.run([sys.executable, '-c', code, str(script), str(root)], env=env,
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            structure = json.loads((root / '01_source/structure.json').read_text())
            self.assertEqual([s['questions'] for s in structure['sections']], [[1], [1, 2]])

    def test_missing_number_not_borrowed_from_other_section(self):
        text = 'Unit 3\n第四课时 Project\nⅠ.根据汉语意思完成句子\n1.A ____\nⅡ.根据汉语意思完成句子\n2.B ____'
        structure, body, _ = infer_structure(text)
        lines = body.splitlines()
        units = _build_units(structure['sections'], structure['groups'], lines)
        altered = deepcopy(units)
        altered[-1]['numbers'] = [1]
        self.assertTrue(_scoped_source_errors(altered, structure['sections'], lines))


if __name__ == '__main__':
    unittest.main()
