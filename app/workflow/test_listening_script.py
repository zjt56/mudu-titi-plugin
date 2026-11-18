import json
import tempfile
import unittest
from pathlib import Path

from workflow import explanations_direct, listening_script, upload_content
from unittest.mock import patch


class ListeningScriptTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.batch = Path(self.temp.name)
        self.units = [
            {'unit_id': 'q-1', 'unit_kind': 'standalone', 'numbers': [1], 'type': '听力关键词语选择'},
            {'unit_id': 'group-16-17', 'unit_kind': 'group', 'numbers': [16, 17], 'type': '听力长对话理解'},
            {'unit_id': 'group-21-25', 'unit_kind': 'group', 'numbers': [21, 22, 23, 24, 25], 'type': '听力短文理解'},
            {'unit_id': 'group-26-30', 'unit_kind': 'group', 'numbers': [26, 27, 28, 29, 30], 'type': '听力信息转换'}]
        self.put('01_source/structure.confirmed.json', json.dumps({'units': self.units}, ensure_ascii=False))
        self.put('01_source/附录.txt', '附:听力录音材料\nⅠ.关键词语选择\n1.Keep your attention.\nⅢ.长对话理解\n听下面一段对话,回答第16至17小题。\nM:Hello.\nW:Hi.\nⅣ.短文理解\nTony studies music.\nⅤ.信息转换\nGood morning, boys.\n')

    def put(self, name, value):
        path = self.batch / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding='utf-8')

    def test_matches_question_range_and_unique_section_without_changing_words(self):
        result = listening_script.extract(self.batch, self.units)
        self.assertEqual(result['q-1'][0], 'Keep your attention.')
        self.assertEqual(result['group-16-17'][0], 'M:Hello.\nW:Hi.')
        self.assertEqual(result['group-21-25'][0], 'Tony studies music.')
        self.assertEqual(result['group-26-30'][0], 'Good morning, boys.')
        self.assertEqual(listening_script.combine('原有解析', result['q-1'][0]),
                         '原有解析\n\n听力录音材料：\nKeep your attention.')

    def test_unknown_or_conflicting_ownership_is_not_guessed(self):
        self.units.append({'unit_id': 'group-other', 'unit_kind': 'group', 'numbers': [31, 32], 'type': '听力短文理解'})
        result = listening_script.extract(self.batch, self.units)
        self.assertNotIn('group-21-25', result)
        self.put('01_source/正文.txt', '附:听力录音材料\nⅠ.关键词语选择\n1.Other words.\n')
        self.assertNotIn('q-1', listening_script.extract(self.batch, self.units))

    def test_varied_titles_numbering_and_unheaded_explicit_ranges(self):
        for header in ('听力原文', '附录：听力稿', '录音稿', 'Listening scripts', 'Tapescript'):
            with self.subTest(header=header):
                self.put('01_source/附录.txt', header + '\n（１）Keep your attention.\n'
                         'Questions 16–17\nM:Hello.\nW:Hi.\n'
                         '第四部分：短文理解\nTony studies music.\n'
                         '五、信息转换\nGood morning, boys.\n参考答案\n1. A\n')
                result = listening_script.extract(self.batch, self.units)
                self.assertEqual(result['q-1'][0], 'Keep your attention.')
                self.assertEqual(result['group-16-17'][0], 'M:Hello.\nW:Hi.')
                self.assertEqual(result['group-21-25'][0], 'Tony studies music.')
                self.assertEqual(result['group-26-30'][0], 'Good morning, boys.')

    def test_explicit_range_never_borrows_a_partial_or_wrong_type_unit(self):
        self.put('01_source/附录.txt', '听力文本\nⅢ.长对话理解\n回答第16至18小题\nM:Wrong range.\n')
        self.assertNotIn('group-16-17', listening_script.extract(self.batch, self.units))

    def test_unscoped_duplicate_numbers_are_never_guessed(self):
        self.units.append({'unit_id': 'duplicate', 'unit_kind': 'standalone', 'numbers': [1], 'type': '听力短对话理解'})
        self.assertEqual(listening_script.extract(self.batch, self.units), {})

    def test_validator_rejects_missing_or_changed_script(self):
        item = dict(self.units[0], question_type='听力关键词语选择', explanation='无')
        report = upload_content.inspect_content(self.batch, {'items': [item]}, strict=True)
        self.assertIn('ORIGINAL_LISTENING_SCRIPT_OMITTED', {row['code'] for row in report['errors']})
        item['explanation'] = listening_script.combine('', 'Keep your attention.')
        report = upload_content.inspect_content(self.batch, {'items': [item]}, strict=True)
        self.assertNotIn('ORIGINAL_LISTENING_SCRIPT_OMITTED', {row['code'] for row in report['errors']})
        self.assertNotIn('EXPLANATION_SOURCE_UNVERIFIED', {row['code'] for row in report['errors']})

    def test_numbered_answer_section_is_not_part_of_script(self):
        for heading in ('Ⅱ.参考答案', '二、答案与解析', '第2部分：参考答案'):
            with self.subTest(heading=heading):
                self.put('01_source/附录.txt', '听力稿\nⅠ.关键词语选择\n1.Keep your attention.\n' + heading + '\nC\n')
                self.assertEqual(listening_script.extract(self.batch, self.units)['q-1'][0], 'Keep your attention.')

    def test_original_explanation_cannot_be_replaced_by_script_only(self):
        self.put('01_source/附录.txt', '1. A 【解析】原有解析。\n听力稿\nⅠ.关键词语选择\n1.Keep your attention.\n')
        item = dict(self.units[0], question_type='听力关键词语选择', explanation=listening_script.combine('', 'Keep your attention.'))
        report = upload_content.inspect_content(self.batch, {'items': [item]}, strict=True)
        self.assertIn('ORIGINAL_EXPLANATION_OMITTED', {row['code'] for row in report['errors']})

    def test_direct_explanation_retains_original_and_supplies_script(self):
        word = {'number': 1, 'source_text': '原有解析', 'path': '01_source/附录.txt', 'line': 1}
        report = {'all_questions': [word], 'all_summaries': []}
        with patch.object(explanations_direct, '_units', return_value=self.units), \
             patch.object(upload_content, 'inspect_content', return_value=report), \
             patch.object(explanations_direct, '_parse', return_value=([], [])):
            payload = explanations_direct.build_payload(self.batch)
        self.assertEqual(payload['items'][0]['explanation'],
                         '原有解析\n\n听力录音材料：\nKeep your attention.')
        self.assertEqual(payload['items'][1]['explanation'], '听力录音材料：\nM:Hello.\nW:Hi.')
        self.assertEqual(payload['items'][2]['explanation'], '听力录音材料：\nTony studies music.')

    def test_script_survives_assembly_and_upload_projection(self):
        from workflow import batch_fill, upload_payload
        from workflow.test_upload_payload import batch, question, catalog_snapshot
        source = listening_script.combine('原有解析', 'Keep your attention.')
        item = question()
        batch_fill._overlay(item, {'explanation': source}, 'explanations')
        report = upload_payload.build_payload(batch(item), catalog_snapshot())
        self.assertEqual(report['payload']['items'][0]['explanation'], source)
