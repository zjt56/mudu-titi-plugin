"""Exercise provenance cleanup through the actual meta/underline/upload consumers."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from workflow import format_gate, metadata_policy, upload_payload
from workflow.test_upload_payload import batch, catalog_snapshot, question, reading_group

APP = Path(__file__).resolve().parents[1]


def script(relative):
    spec = importlib.util.spec_from_file_location(Path(relative).stem, APP / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MetaSourceProjectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.meta = script('scripts/05-meta/assign_meta.py')
        cls.underline = script('scripts/06-upload/mark_underline.py')
        cls.catalog = catalog_snapshot()

    def pipeline(self, data):
        data = deepcopy(data)
        for item in data['items']:
            item['options'] = [f"{option['label']}. {option['text']}" if isinstance(option, dict) else option
                               for option in item.get('options', [])]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / '06_batch').mkdir()
            source = root / '06_batch/batch.json'
            base = root / '06_batch/batch_meta.base.json'
            final = root / '06_batch/batch_meta.json'
            raw = json.dumps(data, ensure_ascii=False)
            source.write_text(raw, encoding='utf-8')
            with patch.object(sys, 'argv', ['assign_meta', '--batch', str(source), '--out', str(base)]):
                self.assertEqual(self.meta.main(), 0)
            self.assertEqual(self.underline.main(['--batch', str(base), '--out', str(final), '--apply']), 0)
            projected = json.loads(final.read_text(encoding='utf-8'))
            report = format_gate.run(root)
            built = upload_payload.build_payload(projected, self.catalog)
            self.assertEqual(source.read_text(encoding='utf-8'), raw)
            return projected, report, built

    def test_parent_and_children_placeholders_are_projected_without_inventing_year(self):
        item = reading_group()
        item.update(source='七上人教版课时A计划 Starter Unit 1 课时1', source_type='同步练习', year='')
        item['filterMetadata'].update(source='未提供', source_type='未提供', year=None)
        for child in item['subquestions']:
            child.update(source='未提供', source_type='未提供', year='')
            child['filterMetadata'] = {'source': '未提供', 'source_type': '未提供', 'year': '未提供'}
        data = batch(item)
        data['upload_target'] = '同步练习'
        projected, report, built = self.pipeline(data)
        self.assertTrue(report['passed'], report['errors'])
        self.assertTrue(built['passed'], built['errors'])
        parent = projected['items'][0]
        self.assertEqual(parent['filterMetadata']['source'], item['source'])
        self.assertEqual(parent['filterMetadata']['source_type'], '同步练习')
        for node in [parent, *parent['subquestions']]:
            self.assertNotIn('year', node)
            self.assertNotIn('year', node['filterMetadata'])
        for child in parent['subquestions']:
            self.assertNotIn('source_type', child)
            self.assertNotIn('source_type', child['filterMetadata'])
        self.assertEqual(parent['material'], item['material'])
        self.assertEqual(parent['knowledge'], item['knowledge'])

    def test_real_source_alias_conflicts_remain_blocking(self):
        item = question()
        item['filterMetadata'].update(source='另一份真实试卷', source_type='同步练习', year=2023)
        projected, report, built = self.pipeline(batch(item))
        self.assertFalse(report['passed'])
        self.assertFalse(built['passed'])
        self.assertEqual(projected['items'][0]['filterMetadata']['source'], '另一份真实试卷')
        self.assertEqual(sum(e['code'] == 'ALIAS_CONFLICT' for e in built['errors']), 3)

    def test_substantive_invalid_year_and_source_type_still_rejected(self):
        item = reading_group()
        item['subquestions'][0].update(year=0, source_type='随意题源')
        _, report, built = self.pipeline(batch(item))
        for result in (report, built):
            codes = {e['code'] for e in result['errors']}
            self.assertIn('YEAR_INVALID', codes)
            self.assertIn('SOURCE_TYPE_INVALID', codes)

    def test_valid_values_survive_and_digit_year_aliases_share_numeric_type(self):
        data = batch()
        item = data['items'][0]
        item['year'] = '2024'
        item['filterMetadata'].update(source='未提供', source_type='未提供', year=2024)
        projected, report, built = self.pipeline(data)
        self.assertTrue(report['passed'], report['errors'])
        self.assertTrue(built['passed'], built['errors'])
        self.assertEqual(projected['items'][0]['year'], 2024)
        self.assertEqual(projected['items'][0]['filterMetadata']['source'], item['source'])

    def test_pure_projection_is_read_only_idempotent_and_missing_parent_is_not_fabricated(self):
        data = batch()
        data['items'][0].update(source='未提供', source_type='未提供', year=None)
        original = deepcopy(data)
        projected = metadata_policy.project_source_metadata(data)
        self.assertEqual(data, original)
        self.assertEqual(projected, metadata_policy.project_source_metadata(projected))
        report = upload_payload.build_payload(projected, self.catalog)
        codes = {e['code'] for e in report['errors']}
        self.assertIn('MISSING_SOURCE', codes)
        self.assertIn('MISSING_SOURCE_TYPE', codes)


if __name__ == '__main__':
    unittest.main()
