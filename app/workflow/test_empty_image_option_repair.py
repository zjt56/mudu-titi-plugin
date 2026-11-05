"""Pure, fail-closed repair candidates; every fixture lives in a temporary batch."""
import base64
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from workflow import assemble_local, extract, structure_gate
from workflow.question_content import IMAGE, audit_batch_source, question_parts


BODY = ('Ⅰ.短对话理解(共2小题)\n'
        '9.What time is it?\nA.[IMG image12.png] B.[IMG image13.png] C.[IMG image14.png]\n'
        '10.What does Li Ming do?\nA.[IMG image15.png] B.[IMG image16.png] C.[IMG image17.png]\n')
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')


class EmptyImageOptionRepairTest(unittest.TestCase):
    def make_batch(self, body=BODY):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = root / '01_source'
        media = source / 'media'
        media.mkdir(parents=True)
        (root / '06_batch').mkdir()
        structure, body, _ = extract.infer_structure(body)
        units = structure_gate._build_units(structure['sections'], structure.get('groups', []), body.splitlines())
        (source / '正文.txt').write_text(body)
        (source / 'structure.json').write_text(json.dumps(structure))
        (source / 'structure.confirmed.json').write_text(json.dumps({'units': units}))
        for name in IMAGE.findall(body):
            if Path(name).name == name:
                (media / name).write_bytes(PNG)
        items = []
        for unit in units:
            uid = ('q-' if unit['unit_kind'] == 'standalone' else 'group-') + '-'.join(map(str, unit['numbers']))
            children = []
            for n in unit['numbers']:
                part = question_parts(unit['stem'].splitlines(), n)
                children.append({'number': n, 'stem': part['stem'],
                                 'options': [{'key': o['label'], 'text': '', 'retain': True} for o in part['options']]})
            row = dict(id=uid, unit_id=uid, unit_kind=unit['unit_kind'], numbers=unit['numbers'],
                       number=unit['numbers'][0], stem='', options=[], subquestions=children, evidence={'retained': True})
            if unit['unit_kind'] == 'standalone':
                row.update(children[0], subquestions=[])
            items.append(row)
        payload = {'items': items, 'preserved': {'unknown': [1, 2]}}
        (root / assemble_local.BATCH).write_text(json.dumps(payload))
        return root, payload

    def hashes(self, root):
        return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in root.rglob('*') if p.is_file()}

    def test_recovers_six_exact_images_without_mutating_payload_or_files(self):
        root, payload = self.make_batch()
        before, hashes = copy.deepcopy(payload), self.hashes(root)
        report = assemble_local.build_image_option_repair(root, payload)
        self.assertEqual(len(report['repairs']), 6)
        self.assertEqual(report['unresolved'], [])
        self.assertEqual(payload, before)
        self.assertEqual(self.hashes(root), hashes)
        for row, first in zip(report['batch']['items'], [12, 15]):
            for offset, option in enumerate(row['options']):
                self.assertEqual(option, {'key': 'ABC'[offset], 'text': f'[IMG image{first + offset}.png]', 'retain': True})
        self.assertTrue(audit_batch_source(root, report['batch'])['passed'])
        self.assertEqual(report['batch']['preserved'], payload['preserved'])
        self.assertTrue(all(r['media_sha256'] == hashlib.sha256(PNG).hexdigest() for r in report['repairs']))

    def test_default_reads_existing_batch_but_never_installs_candidate(self):
        root, payload = self.make_batch()
        hashes = self.hashes(root)
        report = assemble_local.build_image_option_repair(root)
        self.assertEqual(len(report['repairs']), 6)
        self.assertEqual(self.hashes(root), hashes)
        self.assertEqual(json.loads((root / assemble_local.BATCH).read_text()), payload)

    def test_exact_nonempty_option_is_preserved_while_its_empty_siblings_recover(self):
        root, payload = self.make_batch()
        payload['items'][0]['options'][0]['text'] = '[IMG image12.png]'
        report = assemble_local.build_image_option_repair(root, payload)
        self.assertEqual(len(report['repairs']), 5)
        self.assertEqual(report['batch']['items'][0]['options'][0], payload['items'][0]['options'][0])

    def test_conflicting_nonempty_text_prevents_repair_of_that_whole_question(self):
        root, payload = self.make_batch()
        payload['items'][0]['options'][0]['text'] = '[IMG image99.png]'
        report = assemble_local.build_image_option_repair(root, payload)
        self.assertEqual(len(report['repairs']), 3)
        self.assertEqual(report['batch']['items'][0], payload['items'][0])
        self.assertIn('已有非空选项', report['unresolved'][0]['reason'])

    def test_mismatched_or_duplicate_option_letters_stay_unchanged(self):
        root, payload = self.make_batch()
        for keys in ['ACB', 'AAC', 'ABZ']:
            changed = copy.deepcopy(payload)
            for option, key in zip(changed['items'][0]['options'], keys):
                option['key'] = key
            report = assemble_local.build_image_option_repair(root, changed)
            self.assertEqual(report['batch']['items'][0], changed['items'][0])
            self.assertTrue(report['unresolved'])

    def test_file_missing_empty_or_symlink_prevents_question_repair(self):
        for mode in ['missing', 'empty', 'symlink']:
            with self.subTest(mode=mode):
                root, payload = self.make_batch()
                target = root / '01_source/media/image12.png'
                self.assertEqual(target.parent.resolve(), (root / '01_source/media').resolve())
                target.unlink()
                if mode == 'empty':
                    target.write_bytes(b'')
                if mode == 'symlink':
                    target.symlink_to(root / '01_source/media/image15.png')
                report = assemble_local.build_image_option_repair(root, payload)
                self.assertEqual(len(report['repairs']), 3)
                self.assertEqual(report['batch']['items'][0], payload['items'][0])
                self.assertIn('路径不安全', report['unresolved'][0]['reason'])

    def test_stale_confirmed_source_stays_unchanged(self):
        root, payload = self.make_batch()
        source = root / '01_source/正文.txt'
        source.write_text(source.read_text().replace('image12.png', 'new.png'))
        report = assemble_local.build_image_option_repair(root, payload)
        self.assertEqual(len(report['repairs']), 3)
        self.assertEqual(report['batch']['items'][0], payload['items'][0])
        self.assertIn('当前原文切片不一致', report['unresolved'][0]['reason'])

    def test_reused_filename_cannot_be_borrowed_from_another_question(self):
        root, payload = self.make_batch(BODY.replace('image15.png', 'image12.png'))
        report = assemble_local.build_image_option_repair(root, payload)
        self.assertEqual(report['repairs'], [])
        self.assertEqual(report['batch'], payload)
        self.assertTrue(all('归属不能一一证明' in p['reason'] for p in report['unresolved']))

    def test_stale_foreign_source_cannot_create_duplicate_image_ownership(self):
        root, payload = self.make_batch()
        source = root / '01_source/正文.txt'
        source.write_text(source.read_text().replace('image15.png', 'image12.png'))
        report = assemble_local.build_image_option_repair(root, payload)
        self.assertEqual(report['repairs'], [])
        self.assertEqual(report['batch'], payload)
        self.assertEqual(len(report['unresolved']), 2)

    def test_repeated_picture_in_two_source_options_is_ambiguous(self):
        root, payload = self.make_batch(BODY.replace('image13.png', 'image12.png'))
        report = assemble_local.build_image_option_repair(root, payload)
        self.assertEqual(len(report['repairs']), 3)
        self.assertEqual(report['batch']['items'][0], payload['items'][0])

    def test_text_with_image_or_multiple_images_is_not_invented(self):
        for replacement in ['A.clock [IMG image12.png]', 'A.[IMG image12.png] [IMG other.png]']:
            with self.subTest(replacement=replacement):
                root, payload = self.make_batch(BODY.replace('A.[IMG image12.png]', replacement))
                report = assemble_local.build_image_option_repair(root, payload)
                self.assertEqual(len(report['repairs']), 3)
                self.assertEqual(report['batch']['items'][0], payload['items'][0])
                self.assertIn('纯图片选项', report['unresolved'][0]['reason'])

    def test_existing_nested_attachments_are_not_overwritten_or_duplicated(self):
        root, payload = self.make_batch()
        for option in payload['items'][0]['options']:
            option['blocks'] = [{'attachments': [{'kind': 'image', 'url': '/media/existing.png'}]}]
        report = assemble_local.build_image_option_repair(root, payload)
        self.assertEqual(len(report['repairs']), 3)
        self.assertEqual(report['batch']['items'][0], payload['items'][0])

    def test_path_traversal_source_filename_is_not_repaired(self):
        root, payload = self.make_batch(BODY.replace('image12.png', '../image12.png'))
        report = assemble_local.build_image_option_repair(root, payload)
        self.assertEqual(len(report['repairs']), 3)
        self.assertEqual(report['batch']['items'][0], payload['items'][0])

    def test_duplicate_unit_binding_rejects_without_mutating_input(self):
        root, payload = self.make_batch()
        payload['items'].append(copy.deepcopy(payload['items'][0]))
        before = copy.deepcopy(payload)
        with self.assertRaisesRegex(ValueError, '单元唯一'):
            assemble_local.build_image_option_repair(root, payload)
        self.assertEqual(payload, before)

    def test_wrong_number_binding_cannot_borrow_source_options(self):
        root, payload = self.make_batch()
        payload['items'][0]['number'] = 10
        report = assemble_local.build_image_option_repair(root, payload)
        self.assertEqual(len(report['repairs']), 3)
        self.assertEqual(report['batch']['items'][0], payload['items'][0])

    def test_group_children_recover_only_their_own_source_images(self):
        body = ('Ⅱ.长对话理解(共2小题)\n听下面一段对话，回答第6、7小题。\n'
                '6.First?\nA.[IMG first.png] B.[IMG next.png] C.[IMG third.png]\n'
                '7.Second?\nA.[IMG other.png] B.[IMG fifth.png] C.[IMG sixth.png]\n')
        root, payload = self.make_batch(body)
        self.assertEqual(len(payload['items']), 1)
        report = assemble_local.build_image_option_repair(root, payload)
        self.assertEqual(len(report['repairs']), 6)
        children = report['batch']['items'][0]['subquestions']
        self.assertEqual(children[0]['options'][0]['text'], '[IMG first.png]')
        self.assertEqual(children[1]['options'][0]['text'], '[IMG other.png]')
        payload['items'][0]['subquestions'][0]['number'] = 7
        report = assemble_local.build_image_option_repair(root, payload)
        self.assertEqual(report['repairs'], [])
        self.assertEqual(report['batch'], payload)


if __name__ == '__main__':
    unittest.main()
