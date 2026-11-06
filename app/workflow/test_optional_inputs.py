import json
import tempfile
import unittest
from pathlib import Path

from workflow import contracts, manifest as mf


class OptionalInputSkipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / '01_source').mkdir(parents=True)
        self.manifest = {'batch_dir': str(self.root)}

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
        path = optional_inputs.write_empty_explanations(self.root)
        data = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual([row['explanation'] for row in data['items']], ['无', '无'])
        self.assertEqual([row['subquestions'] for row in data['items']], [[], [{'number': 2, 'explanation': '无'}, {'number': 3, 'explanation': '无'}]])

    def test_explanations_run_when_marker_present(self):
        (self.root / '01_source/附录.txt').write_text('1.A 【解析】because', encoding='utf-8')
        self.assertEqual(mf.skip_reason(self.manifest, contracts.STAGE_BY_ID['explanations']), '')

    def test_optional_flags(self):
        self.assertTrue(contracts.STAGE_BY_ID['answers'].optional)
        self.assertTrue(contracts.STAGE_BY_ID['explanations'].optional)
