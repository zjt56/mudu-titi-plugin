"""Visual columns use whole-page ownership, never label-name or numbering guesses."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import visual_columns as vc


class VisualColumnsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / '01_source').mkdir()
        self.text = 'Unit 9\n基础巩固\n熟词生义\nRead the word and choose its meaning.\nA. one B. two\n栏目提示\nThis box explains a method.\n'
        self.path = self.root / '01_source/正文.txt'
        self.path.write_text(self.text, encoding='utf-8')
        self.pages = {'status': 'verified', 'source_sha256': 'source-hash',
                      'pages': [{'page': 1, 'path': '01_source/visual-pages/page-1.png', 'image_sha256': 'image-hash'}]}
        self.route = {'provider': 'chosen', 'model': 'chosen-vision', 'profile': 'selected'}
        self.response = {'columns': [self.row('band', '基础巩固', 'container', 3, 7, 2),
                                     self.row('question', '熟词生义', 'question', 4, 5, 3, 'band'),
                                     self.row('hint', '栏目提示', 'guidance', 7, 7, 6, 'band')], 'issues': []}
        self.addCleanup(patch.stopall)
        patch('workflow.source_pages.prepare', return_value=self.pages['pages']).start()
        self.page_reader = patch('workflow.source_pages.load_verified', return_value=self.pages).start()
        patch('workflow.visual_transport.route_identity', return_value=self.route).start()
        self.identify = patch('workflow.visual_transport.identify', side_effect=self.reply).start()

    def row(self, ident, label, role, start, end, heading=None, parent=None):
        lines = self.text.splitlines()
        return {'id': ident, 'label': label, 'role': role, 'parent_id': parent,
                'start_line': start, 'end_line': end, 'heading_line': heading,
                'start_text': lines[start - 1], 'end_text': lines[end - 1],
                'printed_numbers': [], 'boxes': [{'page': 1, 'bbox': [.1, .1, .9, .7]}],
                'reason': '页内边框与标题及作答指令证明所属栏目'}

    def reply(self, *args, **kwargs):
        return {**self.route, 'text': json.dumps(self.response, ensure_ascii=False)}

    def test_visual_roles_and_no_number_are_preserved_and_prompt_has_full_body(self):
        result = vc.build(self.root)
        self.assertEqual(result['status'], 'verified')
        evidence = vc.load_verified(self.root)
        self.assertEqual(evidence['columns'][1]['printed_numbers'], [])
        candidate = vc.annotate_candidate(self.root, self.text, {'sections': []})
        self.assertEqual(len(candidate['sections']), 1)
        self.assertEqual(candidate['sections'][0]['visual_column_id'], 'question')
        self.assertEqual(len(candidate['visual_columns']), 3)
        images, prompt = self.identify.call_args.args
        self.assertEqual(images[0]['id'], 'page-1')
        self.assertIn('7: This box explains a method.', prompt)
        self.assertEqual(vc.build(self.root)['status'], 'cached')
        self.assertEqual(self.identify.call_count, 1)

    def test_names_do_not_determine_question_role(self):
        self.response['columns'][1]['role'] = 'guidance'
        self.assertEqual(vc.build(self.root)['status'], 'verified')
        self.assertEqual(vc.annotate_candidate(self.root, self.text, {})['sections'], [])

    def test_nonheading_question_does_not_invent_heading(self):
        self.response['columns'][1]['heading_line'] = None
        self.assertEqual(vc.build(self.root)['status'], 'verified')
        candidate = vc.annotate_candidate(self.root, self.text, {})
        self.assertEqual(candidate['sections'], [])
        self.assertEqual(candidate['visual_columns'][1]['start_line'], 4)

    def test_forged_coordinates_quotes_parent_and_uncertainty_reject(self):
        base = deepcopy(self.response)
        for mutation in ('quote', 'bbox', 'parent', 'cycle', 'overlap', 'uncertain'):
            with self.subTest(mutation=mutation):
                self.response = deepcopy(base)
                q = self.response['columns'][1]
                if mutation == 'quote': q['start_text'] = 'Invented text'
                elif mutation == 'bbox': q['boxes'][0]['bbox'][2] = 1.5
                elif mutation == 'parent': q['parent_id'] = 'missing'
                elif mutation == 'cycle': self.response['columns'][0]['parent_id'] = 'question'
                elif mutation == 'overlap': self.response['columns'].append(self.row('other', '创新训练', 'question', 4, 5))
                else: self.response['issues'] = [{'page': 1, 'reason': '不确定归属'}]
                self.assertEqual(vc.build(self.root, force=True)['status'], 'unavailable')
                self.assertEqual(vc.load_verified(self.root)['columns'], [])

    def test_text_pages_and_source_change_invalidate_cached_proof(self):
        self.assertEqual(vc.build(self.root)['status'], 'verified')
        self.path.write_text(self.text + 'new question\n')
        self.assertEqual(vc.load_verified(self.root)['status'], 'unavailable')
        self.path.write_text(self.text)
        changed = {**self.pages, 'source_sha256': 'different'}
        self.page_reader.return_value = changed
        self.assertEqual(vc.load_verified(self.root)['status'], 'unavailable')
        self.page_reader.return_value = {**self.pages, 'pages': [{**self.pages['pages'][0], 'image_sha256': 'changed'}]}
        self.assertEqual(vc.load_verified(self.root)['status'], 'unavailable')

    def test_empty_model_result_and_forged_section_metadata_never_authorize_task(self):
        self.response = {'columns': [], 'issues': []}
        self.assertEqual(vc.build(self.root)['status'], 'unavailable')
        candidate = vc.annotate_candidate(self.root, self.text, {'sections': [
            {'source_line': 3, 'visual_column_id': 'forged', 'visual_column': {'role': 'question'}}]})
        self.assertNotIn('visual_column_id', candidate['sections'][0])
        self.assertNotIn('visual_column', candidate['sections'][0])

    def test_unavailable_page_evidence_never_calls_vision(self):
        self.page_reader.return_value = {'status': 'unavailable', 'reason': 'no full pages'}
        self.assertEqual(vc.build(self.root)['status'], 'unavailable')
        self.identify.assert_not_called()

    def test_selected_route_change_requires_new_visual_call(self):
        self.assertEqual(vc.build(self.root)['status'], 'verified')
        self.route['model'] = 'second-selected-model'
        self.assertEqual(vc.build(self.root)['status'], 'verified')
        self.assertEqual(self.identify.call_count, 2)

    def test_reference_answer_region_note_is_benign_not_ownership_failure(self):
        # A compliant model reports that the reference-answer page was
        # deliberately not turned into a question ("参考答案区，非作答单元，未建题").
        # The answer region contains no question; this note must not void the
        # whole-page ownership proof. The text carries a real answer-key heading.
        self.text = self.text + '参考答案\n1. A　2. B\n'
        self.path.write_text(self.text, encoding='utf-8')
        self.response['issues'] = [{'page': 2, 'reason': '参考答案区，非作答单元，未建题'}]
        self.assertEqual(vc.build(self.root)['status'], 'verified')
        self.assertEqual(len(vc.load_verified(self.root)['columns']), 3)

    def test_genuine_uncovered_issue_still_rejects(self):
        # An actual uncovered/uncertain question window is not a benign note.
        self.response['issues'] = [{'page': 1, 'reason': '此处有三道题但无法确定是否独立'}]
        self.assertEqual(vc.build(self.root, force=True)['status'], 'unavailable')

    def test_trailing_nbsp_and_string_numbers_are_normalized(self):
        # Word source ends lines with NBSP; the model may echo without it and may
        # write printed numbers as digit strings. Both are layout-only and must
        # not invalidate the proof, while the interior/coverage checks still hold.
        self.text = 'Unit 9\nⅠ.根据首字母补全单词\n' + '1.Apple\u00a0\n2.Banana\u00a0\n'
        self.path.write_text(self.text, encoding='utf-8')
        self.response = {'columns': [self.row('q', 'Ⅰ.根据首字母补全单词', 'question', 3, 4, 2)],
                         'issues': []}
        self.response['columns'][0]['start_text'] = '1.Apple'   # dropped trailing NBSP
        self.response['columns'][0]['end_text'] = '2.Banana'
        self.response['columns'][0]['printed_numbers'] = ['1', '2']  # strings
        self.assertEqual(vc.build(self.root, force=True)['status'], 'verified')
        cols = vc.load_verified(self.root)['columns']
        self.assertEqual(cols[0]['printed_numbers'], [1, 2])

    def test_interior_quote_change_and_non_numeric_number_still_reject(self):
        base = deepcopy(self.response)
        for mutation in ('interior', 'nonnum'):
            with self.subTest(mutation=mutation):
                self.response = deepcopy(base)
                q = self.response['columns'][1]
                if mutation == 'interior':
                    q['end_text'] = q['end_text'][:-1] + 'X'  # interior char changes
                else:
                    q['printed_numbers'] = ['1a']
                self.assertEqual(vc.build(self.root, force=True)['status'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
