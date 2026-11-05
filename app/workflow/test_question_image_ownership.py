"""Image evidence must stay bound to its source unit, child, and option.

Synthetic temporary batches only; never rewrites a real workflow artifact.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from workflow import extract, structure_gate
from workflow.question_content import audit_batch_source, question_parts


class QuestionImageOwnershipTest(unittest.TestCase):
    def make_batch(self, body):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        source = root / '01_source'
        source.mkdir()
        structure, body, _ = extract.infer_structure(body)
        (source / '正文.txt').write_text(body, encoding='utf-8')
        (source / 'structure.json').write_text(json.dumps(structure), encoding='utf-8')
        units = structure_gate._build_units(structure['sections'], structure.get('groups', []), body.splitlines())
        (source / 'structure.confirmed.json').write_text(json.dumps({'units': units}), encoding='utf-8')
        items = []
        for i, unit in enumerate(units):
            parts = [dict(number=n, stem=question_parts(unit['stem'].splitlines(), n)['stem'],
                          options=[dict(key=o['label'], text=o['text'])
                                   for o in question_parts(unit['stem'].splitlines(), n)['options']])
                     for n in unit['numbers']]
            row = dict(unit_id=f'u{i}', number=unit['numbers'][0], numbers=unit['numbers'],
                       stem='', options=[], material='', subquestions=parts)
            if unit['unit_kind'] == 'standalone':
                row.update(parts[0], subquestions=[])
            else:
                row['material'] = unit['stem'].split(str(unit['numbers'][0]) + '.', 1)[0].strip()
            items.append(row)
        return root, {'items': items}

    def choices(self):
        return self.make_batch('Ⅰ.短对话理解(共2小题)\n'
                               '9.What time is it?\nA.[IMG image12.jpeg] B.[IMG image13.jpeg] C.[IMG image14.jpeg]\n'
                               '10.What does Li Ming do?\nA.[IMG image15.jpeg] B.[IMG image16.jpeg] C.[IMG image17.jpeg]\n')

    def image_issues(self, root, batch):
        return [i for i in audit_batch_source(root, batch)['issues'] if i['code'] == 'SOURCE_IMAGE_MISSING']

    def attach_options(self, batch):
        for row in batch['items']:
            for option in row['options']:
                name = option['text'][5:-1]
                option['text'] = ''
                option['blocks'] = [{'content': {'attachments': [{'kind': 'image', 'url': '/media/' + name}]}}]

    def test_source_markers_in_each_option_pass(self):
        root, batch = self.choices()
        self.assertTrue(audit_batch_source(root, batch)['passed'])

    def test_real_2014_shape_empty_q9_q10_options_stays_blocked(self):
        root, batch = self.choices()
        for row in batch['items']:
            for option in row['options']:
                option['text'] = ''
        issues = self.image_issues(root, batch)
        self.assertEqual(len(issues), 6)
        self.assertEqual({i['number'] for i in issues}, {9, 10})
        for n in range(12, 18):
            self.assertTrue(any(i['message'].endswith(f'image{n}.jpeg') for i in issues))
        self.assertFalse(audit_batch_source(root, batch)['passed'])

    def test_recursively_nested_option_attachments_are_complete_content(self):
        root, batch = self.choices()
        self.attach_options(batch)
        self.assertTrue(audit_batch_source(root, batch)['passed'], audit_batch_source(root, batch)['issues'])

    def test_parent_option_key_bindings_are_accepted(self):
        root, batch = self.choices()
        for row in batch['items']:
            row['attachments'] = []
            for option in row['options']:
                row['attachments'].append({'kind': 'image', 'url': '/media/' + option['text'][5:-1],
                                           'placement': 'option', 'optionKey': option['key']})
                option['text'] = ''
        self.assertTrue(audit_batch_source(root, batch)['passed'])

    def test_swapping_options_does_not_borrow_sibling_image(self):
        root, batch = self.choices()
        self.attach_options(batch)
        options = batch['items'][0]['options']
        options[0]['blocks'], options[1]['blocks'] = options[1]['blocks'], options[0]['blocks']
        self.assertEqual(len(self.image_issues(root, batch)), 2)

    def test_foreign_unit_image_does_not_satisfy_current_unit(self):
        root, batch = self.choices()
        self.attach_options(batch)
        foreign = batch['items'][0]['options'][0].pop('blocks')
        batch['items'][1]['options'][0]['blocks'] += foreign
        issues = self.image_issues(root, batch)
        self.assertEqual(len(issues), 1)
        self.assertEqual((issues[0]['unit_id'], issues[0]['number']), ('u0', 9))

    def test_unbound_parent_attachment_cannot_replace_specific_option(self):
        root, batch = self.choices()
        batch['items'][0]['options'][0]['text'] = ''
        batch['items'][0]['attachments'] = [{'kind': 'image', 'url': '/media/image12.jpeg'}]
        self.assertEqual(len(self.image_issues(root, batch)), 1)

    def test_exact_filename_not_substring_query_caption_or_audio(self):
        root, batch = self.choices()
        batch['items'][0]['options'][0]['text'] = ''
        for attachment in [
            {'kind': 'image', 'url': '/media/image12.jpeg.backup'},
            {'kind': 'image', 'url': '/media/other.jpeg?missing=image12.jpeg'},
            {'kind': 'image', 'url': '/media/other.jpeg', 'notes': 'image12.jpeg'},
            {'kind': 'audio', 'url': '/media/image12.jpeg'},
        ]:
            with self.subTest(attachment=attachment):
                batch['items'][0]['options'][0]['attachments'] = [attachment]
                self.assertEqual(len(self.image_issues(root, batch)), 1)

    def test_encoded_url_and_html_markdown_images_are_recognized(self):
        root, batch = self.choices()
        for text in ['<img src="/media/image%31%32.jpeg?cache=1">',
                     '![clock](/media/image12.jpeg)',
                     '<IMG alt="clock" SRC="/media/image12.jpeg">']:
            with self.subTest(text=text):
                batch['items'][0]['options'][0]['text'] = text
                self.assertTrue(audit_batch_source(root, batch)['passed'])
        for text in ['<img src="/media/other.jpeg" alt="image12.jpeg">',
                     '<img data-src="/media/image12.jpeg" src="/media/other.jpeg">',
                     "<img alt=' src=\"/media/image12.jpeg\"' src='/media/other.jpeg'>"]:
            with self.subTest(text=text):
                batch['items'][0]['options'][0]['text'] = text
                self.assertEqual(len(self.image_issues(root, batch)), 1)

    def test_answers_metadata_and_unrelated_fields_are_not_content(self):
        root, batch = self.choices()
        row = batch['items'][0]
        row['options'][0]['text'] = ''
        row['options'][0]['explanation'] = '[IMG image12.jpeg]'
        row['answer'] = '[IMG image12.jpeg]'
        row['review'] = {'attachments': [{'url': '/media/image12.jpeg'}]}
        batch['attachments'] = [{'url': '/media/image12.jpeg'}]
        self.assertEqual(len(self.image_issues(root, batch)), 1)

    def test_non_image_words_still_require_exact_option_content(self):
        root, batch = self.make_batch('Ⅰ.短对话理解(共1小题)\n9.What time is it?\n'
                                      'A.clock [IMG image12.jpeg] B.[IMG image13.jpeg] C.[IMG image14.jpeg]\n')
        for option, name in zip(batch['items'][0]['options'], ['image12.jpeg', 'image13.jpeg', 'image14.jpeg']):
            option['text'] = ''
            option['attachments'] = [{'url': '/media/' + name}]
        issues = audit_batch_source(root, batch)['issues']
        self.assertFalse(self.image_issues(root, batch))
        self.assertTrue(any(i['code'] == 'OPTION_CONTENT_MISMATCH' and i['number'] == 9 for i in issues))

    def group(self):
        root, batch = self.make_batch('Ⅱ.长对话理解(共2小题)\n听下面一段对话，回答第6、7小题。\n'
                                      '6.First? [IMG first.png]\nA.One B.Two C.Three\n'
                                      '7.Second? [IMG second.png]\nA.One B.Two C.Three\n')
        self.assertEqual(len(batch['items']), 1)
        self.assertTrue(audit_batch_source(root, batch)['passed'], audit_batch_source(root, batch)['issues'])
        return root, batch

    def test_child_attachments_are_recursive_and_stem_words_still_match(self):
        root, batch = self.group()
        for child in batch['items'][0]['subquestions']:
            name = 'first.png' if child['number'] == 6 else 'second.png'
            child['stem'] = child['stem'].replace('[IMG ' + name + ']', '')
            child['materialBlocks'] = [{'rows': [{'cells': [{'attachments': [{'url': '/media/' + name}]}]}]}]
        self.assertTrue(audit_batch_source(root, batch)['passed'], audit_batch_source(root, batch)['issues'])
        batch['items'][0]['subquestions'][0]['stem'] = 'Changed?'
        self.assertIn('SOURCE_STEM_MISSING', {i['code'] for i in audit_batch_source(root, batch)['issues']})

    def test_sibling_or_parent_cannot_satisfy_child_stem_image(self):
        root, batch = self.group()
        first, second = batch['items'][0]['subquestions']
        first['stem'] = first['stem'].replace('[IMG first.png]', '')
        for owner in [second, batch['items'][0]]:
            with self.subTest(owner=owner['number']):
                changed = copy.deepcopy(batch)
                dest = changed['items'][0]['subquestions'][1] if owner is second else changed['items'][0]
                dest['attachments'] = [{'kind': 'image', 'url': '/media/first.png'}]
                issues = self.image_issues(root, changed)
                self.assertEqual(len(issues), 1)
                self.assertEqual(issues[0]['number'], 6)

    def test_shared_material_image_cannot_borrow_child_image(self):
        root, batch = self.make_batch('Ⅰ.阅读理解(共1小题)\nA\nArticle. [IMG shared.png]\n'
                                      '56.Choose?\nA.One B.Two C.Three D.Four\n')
        row = batch['items'][0]
        row['material'] = row['material'].replace('[IMG shared.png]', '')
        row['subquestions'][0]['attachments'] = [{'url': '/media/shared.png'}]
        self.assertEqual(len(self.image_issues(root, batch)), 1)
        row['attachments'] = [{'kind': 'image', 'url': '/media/shared.png'}]
        self.assertTrue(audit_batch_source(root, batch)['passed'], audit_batch_source(root, batch)['issues'])

    def test_shared_options_accept_own_nested_attachments(self):
        root, batch = self.make_batch('Ⅰ.补全对话(共2小题)\nAnn: __51__\nBob: __52__\n'
                                      'A.[IMG a.png] B.Hello. C.Goodbye.\n')
        row = batch['items'][0]
        row['material'] = 'Ann: __51__\nBob: __52__'
        row['options'] = [{'key': 'A', 'text': '', 'attachments': [{'url': '/media/a.png'}]},
                          {'key': 'B', 'text': 'Hello.'}, {'key': 'C', 'text': 'Goodbye.'}]
        self.assertTrue(audit_batch_source(root, batch)['passed'], audit_batch_source(root, batch)['issues'])
        row['options'][0].pop('attachments')
        row['subquestions'][0]['attachments'] = [{'url': '/media/a.png'}]
        self.assertEqual(len(self.image_issues(root, batch)), 1)

    def test_nested_option_attachment_with_foreign_option_key_is_not_borrowed(self):
        root, batch = self.choices()
        self.attach_options(batch)
        attachment = batch['items'][0]['options'][0]['blocks'][0]['content']['attachments'][0]
        attachment['placement'] = 'option'
        attachment['optionKey'] = 'B'
        self.assertEqual(len(self.image_issues(root, batch)), 1)

    def test_reused_source_image_still_requires_both_shared_and_child_placements(self):
        root, batch = self.make_batch('Ⅰ.阅读理解(共1小题)\nA\nArticle. [IMG shared.png]\n'
                                      '56.Choose? [IMG shared.png]\nA.One B.Two C.Three D.Four\n')
        row = batch['items'][0]
        row['material'] = row['material'].replace('[IMG shared.png]', '')
        self.assertEqual(len(self.image_issues(root, batch)), 1)
        row['attachments'] = [{'url': '/media/shared.png'}]
        self.assertTrue(audit_batch_source(root, batch)['passed'])


if __name__ == '__main__':
    unittest.main()
