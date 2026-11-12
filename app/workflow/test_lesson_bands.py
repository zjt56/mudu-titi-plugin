"""Graphical containers bind source coordinates, not filenames or numbering."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import lesson_bands as bands, extract, question_identity, structure_gate


class LessonBandsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / '01_source/media').mkdir(parents=True)
        (self.root / 'attachments').mkdir()
        (self.root / 'attachments/source.docx').write_bytes(b'original source proof')
        self.text = ('Unit 1 You and Me\n第一课时 Section A\n[IMG a.png]\n'
                     'Ⅰ.单项填空\n1. First? A. One B. Two\n[IMG b.png]\n'
                     'Ⅰ.单项填空\n1. Second? A. One B. Two\nPronunciation\n'
                     'Ⅰ.找出选项中画线部分的发音与所给音标相同的单词\n1. /e/ A. eye B. all\n')
        self.write('01_source/正文.txt', self.text)
        for name in ('a.png', 'b.png'):
            (self.root / '01_source/media' / name).write_bytes(name.encode())
        self.route = {'provider': 'test', 'model': 'vision', 'profile': 'device'}

    def write(self, path, data):
        (self.root / path).write_text(data if isinstance(data, str) else json.dumps(data), encoding='utf-8')

    def identify(self, images, prompt, **kwargs):
        rows = []
        for image in images:
            label = '基础巩固' if image['id'] == 'a.png' else '能力提升'
            marker = 'A' if label == '基础巩固' else 'B'
            rows.append({'id': image['id'], 'role': 'band', 'label': label,
                         'marker': marker, 'visible_text': marker + ' ' + label})
        return {'provider': 'test', 'model': 'vision', 'text': json.dumps({'items': rows}, ensure_ascii=False)}

    def build(self, **kwargs):
        with patch('workflow.visual_transport.route_identity', return_value=self.route):
            return bands.build(self.root, identify=self.identify, **kwargs)

    def test_assembly_audit_and_phonetic_rubric_use_verified_banner_stop(self):
        self.text = ('Unit 1 You and Me\n第一课时 Pronunciation\n[IMG a.png]\n'
                     'Ⅰ.找出下列每组单词中画线部分读音不同的一项\n'
                     '(　)1.A.make B.take C.father D.game\n[IMG b.png]\n'
                     '阅读理解\nA short story.\n(　)1.What?\nA.One B.Two C.Three D.Four\n')
        self.write('01_source/正文.txt', self.text)
        self.build()
        structure, _, _ = extract.infer_structure(self.text)
        self.write('01_source/structure.json', structure)
        from workflow import question_draft, question_content, phonetic_structure, preview
        with patch('workflow.structure_ai.auto_classify', return_value={'ran': False}):
            report = structure_gate.run_gate(self.root)
        self.assertTrue(report['passed'], report['blockers'])
        draft = question_draft.build(self.root)
        self.assertEqual(draft['issues'], [])
        first = draft['items'][0]
        self.assertIn('找出下列每组', first['stem'])
        self.assertEqual(first['material'], '')
        self.assertNotIn('b.png', json.dumps(first))
        confirmed = json.loads((self.root / '01_source/structure.confirmed.json').read_text())
        for row, unit in zip(draft['items'], confirmed['units']):
            row['question_type'] = unit['type']
        checked = question_content.audit_batch_source(self.root, {'items': draft['items']})
        self.assertTrue(checked['passed'], checked['issues'])
        verified = phonetic_structure.verified_groups(self.root / '06_batch/batch.json')
        self.assertEqual(phonetic_structure.shape(first, verified), (True, True))
        # The importer restores options as key/text dictionaries before the
        # CLI format precheck; the draft/wire form uses labeled strings.
        restored = copy.deepcopy(first)
        for child in restored['subquestions']:
            child['options'] = [{'key': option[0], 'text': option[3:]} for option in child['options']]
        self.assertEqual(phonetic_structure.shape(restored, verified), (True, True))
        for child in restored['subquestions']:
            child['answer'] = 'A'
        from workflow import assemble_precheck
        pre = assemble_precheck.precheck({'items': [restored]}, batch_dir=self.root)
        self.assertFalse(any('[A8]' in p or '[A9]' in p for p in pre['problems']), pre)
        restored['subquestions'][0]['options'][0]['text'] = 'fabricated'
        self.assertEqual(phonetic_structure.shape(restored, verified), (False, False))
        view = preview.source_slice(self.root, [1], unit_id=first['unit_id'])
        self.assertIn(first['stem'], '\n'.join(view))
        self.assertNotIn('b.png', '\n'.join(view))
        bad = copy.deepcopy(draft['items'])
        bad[0]['source_section']['lesson_band']['label'] = '能力提升'
        self.assertIn('BATCH_IDENTITY_MISMATCH', {e['code'] for e in
            question_content.audit_batch_source(self.root, {'items': bad})['issues']})
        # Loss of current media proof must not authorize the tightened window.
        (self.root / '01_source/media/a.png').write_bytes(b'tampered')
        self.assertFalse(question_content.audit_batch_source(self.root, {'items': draft['items']})['passed'])

    def test_canonical_names_are_exact_and_marker_consistent(self):
        for value in ('A 基础巩固', '**基础巩固**', '[IMG x.png]基础巩固[IMG y.png]'):
            self.assertEqual(bands.canonical_label(value), '基础巩固')
        for value in ('A能力提升', 'B基础巩固', 'Pronunciation', '提升能力', '题干基础巩固方法'):
            self.assertEqual(bands.canonical_label(value), '')

    def test_one_batch_binds_two_bands_and_ends_before_topic(self):
        result = self.build()
        self.assertEqual(result['model_calls'], 1)
        self.assertEqual([(r['label'], r['start_line'], r['end_line']) for r in result['bands']],
                         [('基础巩固', 3, 5), ('能力提升', 6, 8)])
        self.assertEqual(bands.load_verified(self.root)['bands'], result['bands'])
        self.assertEqual((self.root / '01_source/正文.txt').read_text(), self.text)

    def test_cache_is_invalidated_by_route_and_bytes_not_filename(self):
        self.build()
        self.assertEqual(self.build()['model_calls'], 0)
        self.route['model'] = 'another'
        self.assertEqual(self.build()['model_calls'], 1)
        (self.root / '01_source/media/a.png').write_bytes(b'changed image')
        self.assertEqual(bands.load_verified(self.root)['status'], 'unavailable')
        self.assertEqual(self.build()['model_calls'], 1)

    def test_source_body_and_boundary_tampering_are_rejected(self):
        result = self.build()
        for change in ('source', 'text', 'boundary'):
            with self.subTest(change=change):
                self.write('01_source/正文.txt', self.text)
                (self.root / 'attachments/source.docx').write_bytes(b'original source proof')
                self.write(bands.ARTIFACT, copy.deepcopy(result))
                if change == 'source':
                    (self.root / 'attachments/source.docx').write_bytes(b'changed')
                elif change == 'text':
                    self.write('01_source/正文.txt', self.text + 'extra')
                else:
                    altered = copy.deepcopy(result)
                    altered['bands'][1]['start_line'] -= 1
                    self.write(bands.ARTIFACT, altered)
                self.assertEqual(bands.load_verified(self.root)['status'], 'unavailable')

    def test_partial_or_wrong_image_reply_is_not_accepted(self):
        def bad(images, prompt, **kwargs):
            return {'text': '{"items": []}'}
        with patch('workflow.visual_transport.route_identity', return_value=self.route):
            result = bands.build(self.root, identify=bad)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['bands'], [])
        self.assertEqual(result['issues'][0]['code'], 'BAND_VISUAL_UNAVAILABLE')

    def test_appendix_cannot_supply_missing_body_banner_or_guess_image_content(self):
        self.text += '\n参考答案\nA 基础巩固\nⅠ.1.A\nB 能力提升\nⅠ.1.B\n'
        self.write('01_source/正文.txt', self.text)
        def other(images, prompt, **kwargs):
            return {'text': json.dumps({'items': [{'id': i['id'], 'role': 'other'} for i in images]})}
        with patch('workflow.visual_transport.route_identity', return_value=self.route):
            result = bands.build(self.root, identify=other)
        self.assertEqual(result['bands'], [])

    def test_boundaries_reach_structure_identity_draft_and_preview_without_renumbering(self):
        evidence = self.build()
        structure, _, _ = extract.infer_structure(self.text)
        sections = bands.annotate_sections(structure['sections'], evidence)
        original_sections = copy.deepcopy(structure['sections'])
        units = structure_gate._build_units(sections, [], self.text.splitlines())
        prior_units = structure_gate._build_units(structure['sections'], [], self.text.splitlines())
        self.assertEqual([u['question_ids'] for u in units], [u['question_ids'] for u in prior_units])
        self.assertEqual([u['source_section'].get('lesson_band', {}).get('label') for u in units],
                         ['基础巩固', '能力提升', None])
        self.assertNotIn('[IMG b.png]', units[0]['stem'])
        self.assertNotIn('Pronunciation', units[1]['stem'])
        self.assertEqual(structure['sections'], original_sections)
        self.write('01_source/structure.confirmed.json', {'identity_version': 'scoped-v1', 'units': units})
        from workflow import question_draft
        draft = question_draft.build(self.root)
        self.assertEqual(len(draft['items']), 3)
        self.assertNotIn('[IMG b.png]', json.dumps(draft['items'][0]))
        from workflow import preview, upload_payload
        from workbench.server import source_blocks
        for index, row in enumerate(draft['items']):
            row['question_type'] = units[index]['type']
            row['difficulty'] = '容易'
            row.setdefault('filterMetadata', {})['difficulty'] = '容易'
            for child in row.get('subquestions') or []:
                child['answer'] = 'A'
        wire = upload_payload.build_payload({'items': draft['items']}, batch_dir=self.root)['payload']['items']
        for index in (0, 1):
            unit = units[index]
            chunk = preview.source_slice(self.root, unit['numbers'], unit_id=unit['unit_id'])
            rendered = json.dumps(source_blocks(chunk), ensure_ascii=False)
            self.assertNotIn('b.png', rendered)
            self.assertNotIn('Pronunciation', rendered)
            self.assertNotIn('b.png', json.dumps(wire[index], ensure_ascii=False))
            self.assertNotIn('Pronunciation', json.dumps(wire[index], ensure_ascii=False))

    def test_other_image_bytes_are_also_inputs_and_failure_is_temporarily_deferred(self):
        def other(images, prompt, **kwargs):
            reply = self.identify(images, prompt, **kwargs)
            data = json.loads(reply['text'])
            data['items'][-1].update(role='other', label='', marker='', visible_text='')
            reply['text'] = json.dumps(data)
            return reply
        with patch('workflow.visual_transport.route_identity', return_value=self.route):
            bands.build(self.root, identify=other)
            (self.root / '01_source/media/b.png').write_bytes(b'new banner')
            self.assertEqual(bands.load_verified(self.root)['status'], 'unavailable')
            self.assertEqual(bands.build(self.root, identify=self.identify)['model_calls'], 1)
        (self.root / '01_source/media/b.png').write_bytes(b'next')
        calls = []
        def failed(*args, **kwargs):
            calls.append(True)
            raise RuntimeError('rate limited')
        with patch('workflow.visual_transport.route_identity', return_value=self.route):
            self.assertEqual(bands.build(self.root, identify=failed)['status'], 'partial')
            self.assertEqual(bands.build(self.root, identify=failed)['model_calls'], 0)
            self.assertEqual(len(calls), 1)

    def test_first_visible_band_does_not_pollute_preceding_unscoped_material(self):
        self.text = ('Unit 1 You and Me\n第一课时 Section A\nⅠ.阅读理解\nRead this article.\n'
                     '1. First? A. One B. Two C. Three D. Four\n[IMG b.png]\n'
                     'Ⅱ.单项填空\n1. Second? A. One B. Two C. Three D. Four\n')
        self.write('01_source/正文.txt', self.text)
        evidence = self.build()
        structure, _, _ = extract.infer_structure(self.text)
        sections = bands.annotate_sections(structure['sections'], evidence)
        units = structure_gate._build_units(sections, [], self.text.splitlines())
        self.assertNotIn('b.png', units[0]['stem'])

    def test_same_line_columns_bind_draft_preview_and_payload_separately(self):
        self.text = ('Unit 1 You and Me\n第一课时 Section A\nPronunciation\n'
                     'Ⅰ.写出下列缩写词的完全形式\n1.I’m __\t2.it’s __\n')
        self.write('01_source/正文.txt', self.text)
        structure, _, _ = extract.infer_structure(self.text)
        units = structure_gate._build_units(structure['sections'], [], self.text.splitlines())
        self.assertEqual([u['numbers'] for u in units], [[1], [2]])
        self.write('01_source/structure.json', structure)
        self.write('01_source/structure.confirmed.json', {'identity_version': 'scoped-v1', 'units': units})
        from workflow import question_draft, preview, upload_payload
        from workbench.server import source_blocks
        draft = question_draft.build(self.root)
        self.assertEqual(draft['issues'], [])
        for index, row in enumerate(draft['items']):
            row['question_type'] = units[index]['type']
            row['answer'] = 'I am' if index == 0 else 'it is'
        payload = upload_payload.build_payload({'items': draft['items']}, batch_dir=self.root)['payload']['items']
        for index, word, other in [(0, 'I’m', 'it’s'), (1, 'it’s', 'I’m')]:
            chunk = preview.source_slice(self.root, units[index]['numbers'], unit_id=units[index]['unit_id'])
            self.assertIn(word, '\n'.join(chunk))
            self.assertNotIn(other, json.dumps(source_blocks(chunk), ensure_ascii=False))
            self.assertIn(word, payload[index]['stem'])
            self.assertNotIn(other, payload[index]['stem'])
        broken = copy.deepcopy(units)
        broken[0]['source_span']['end_offset'] += 1
        self.assertEqual(question_draft.build(self.root, units=broken)['issues'][0]['code'], 'SOURCE_SCOPE_UNPROVEN')

    def test_text_projection_after_denoise_keeps_current_image_proof(self):
        self.build()
        projected = bands.for_text(self.root, '\n'.join(self.text.splitlines()[1:]))
        self.assertEqual([b['start_line'] for b in projected['bands']], [2, 5])
        self.assertEqual(bands.load_verified(self.root)['bands'][0]['start_line'], 3)


if __name__ == '__main__':
    unittest.main()
