"""Real merged grids and PDF answer artifacts must not create false blockers."""
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch
from workflow import checks

ROOT = Path(__file__).resolve().parents[1]


class TableSpanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.check_table = staticmethod(runpy.run_path(str(ROOT / 'scripts/06-upload/check_tables.py'))['_check_rectangular'])

    def test_vertical_heading_spans_three_rows(self):
        block = {'rows': [
            {'cells': [{'text': 'Title', 'colspan': 3}]},
            {'cells': [{'text': 'Reasons', 'rowspan': 3}, {'text': 'One'}, {'text': 'A'}]},
            {'cells': [{'text': 'Two'}, {'text': 'B'}]},
            {'cells': [{'text': 'Three'}, {'text': 'C'}]},
        ]}
        problems = []
        self.check_table({'unit_id': 'group-16-20'}, block, 0, problems)
        self.assertEqual(problems, [])

    def test_missing_body_cell_still_fails(self):
        problems = []
        self.check_table({'unit_id': 'x'}, {'rows': [['Title'], ['A', 'B', 'C'], ['D', 'E']]}, 0, problems)
        self.assertTrue(problems)

    def test_rowspan_beyond_table_still_fails(self):
        problems = []
        self.check_table({'unit_id': 'x'}, {'rows': [[{'text': 'A', 'rowspan': 2}]]}, 0, problems)
        self.assertTrue(problems)


class NativeSpellingBlankTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.check_unit = staticmethod(runpy.run_path(str(ROOT / 'scripts/06-upload/audit_underlines.py'))['_check_unit'])

    def test_source_empty_underlined_run_is_valid_spelling_blank(self):
        unit = {'unit_id': 'q-76', 'question_type': '单词拼写',
                'stem': 'The lake is <u>r</u><u>\u3000\u3000</u>（丰富） in wildlife.', 'answer': 'rich'}
        self.assertEqual(self.check_unit(unit), [])
        unit['stem'] = 'The lake is <u>r</u>（丰富） in wildlife.'
        self.assertTrue(self.check_unit(unit))
        unit['stem'] = 'The lake is <u>rich</u> in wildlife.'
        self.assertTrue(self.check_unit(unit))


class PdfAnswerArtifactTests(unittest.TestCase):
    def test_pdf_compared_using_prepared_answers_with_explicit_basis(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            (batch / '06_batch').mkdir()
            (batch / '01_source_ans').mkdir()
            target = batch / '06_batch/batch_meta.json'
            target.write_text(json.dumps({'items': []}))
            prepared = batch / '01_source_ans/answers.json'
            prepared.write_text(json.dumps({'answers': {'1': 'A'}}))
            def fake_run(check, root, path, inputs):
                self.assertEqual(inputs['answers']['path'], str(prepared))
                return {'check': check.name, 'script': check.script, 'passed': True, 'blocking': True}
            with patch('workflow.checks.run_check', side_effect=fake_run):
                report = checks.run_all(ROOT, target, {'answers': {'exists': True, 'path': '/source/answers.pdf'}},
                                        only=('scripts/06-upload/check_answers.py',))
            self.assertTrue(report['passed'])
            self.assertIn('不声称直接解析PDF', report['checks'][0]['input_basis'])
