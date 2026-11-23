"""Current-source proof for phonetic A8/A9 forms and every content consumer."""
import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import (question_draft, phonetic_structure, question_content, preview,
                      upload_payload, assemble_local)


class PhoneticStructureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.text = ('Ⅰ.按照字母表顺序,写出空白处所缺字母\n[IMG image2.jpeg]\n'
                     'Ⅱ.按照名字的字母顺序为下列人物排序,并按照性别进行分类\n[IMG image3.jpeg]\n'
                     '1.Boys’ names:\n[TABLE 1]\n\n\n\n[/TABLE]\n'
                     '2.Girls’ names:\n[TABLE 2]\n\n\n\n[/TABLE]\n'
                     'Ⅰ.判断下列各组字母的大小写是否一致(一致的在括号内写T,不一致的写F)\n'
                     '1.U　v (　　　)　　　　2.W　x (　　　)')
        from workflow import extract, structure_gate, denoise
        self.text = 'Starter Unit 1 Hello!\n第一课时 Section A (1a-2d)\n' + self.text
        structure, self.text, _ = extract.infer_structure(self.text)
        cleaned = denoise.clean_body(self.text)[0]
        self.units = structure_gate._build_units(structure['sections'], structure['groups'], cleaned.splitlines())
        self.write('01_source/正文.txt', self.text)
        self.write('01_source/structure.confirmed.json', {'units': self.units, 'identity_version': 'scoped-v1'})
        self.write('01_source/structure.json', structure)
        for name in ('image2.jpeg', 'image3.jpeg'):
            path = self.root / '01_source/media' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'nonempty-source-media-fixture')
        self.rows = question_draft.build(self.root)['items']
        for row in self.rows:
            row['question_type'] = '语音题'
            row['difficulty'] = '容易'
            row.setdefault('filterMetadata', {})['difficulty'] = '容易'
            row['attachments'] = [dict(a, url='/source-media/' + self.root.name + '/' + a['name'])
                                  for a in row.get('attachments') or []]
            for child in row['subquestions']:
                child['answer'] = 'F'
        self.write('06_batch/batch.json', {'items': self.rows})

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def verified(self):
        return phonetic_structure.verified_groups(self.root / '06_batch/batch.json')

    def check_script(self, name):
        path = Path(__file__).parents[1] / 'scripts/06-upload' / (name + '.py')
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_three_native_forms_have_verbatim_heading_and_original_images(self):
        self.assertEqual(len(self.verified()), 3)
        self.assertEqual([phonetic_structure.shape(r, self.verified()) for r in self.rows],
                         [(True, True), (True, False), (True, False)])
        for row, unit in zip(self.rows, self.units):
            self.assertEqual(row['stem'], unit['source_section']['title'])
            self.assertEqual(row['material'], '')
        self.assertEqual(self.rows[0]['subquestions'][0]['stem'], '')
        self.assertIn('Boys’ names:', self.rows[1]['subquestions'][0]['stem'])
        self.assertEqual([a['name'] for r in self.rows for a in r['attachments']],
                         ['image2.jpeg', 'image3.jpeg'])
        self.assertEqual(question_content.audit_batch_source(self.root, {'items': self.rows})['issues'], [])

    def test_source_preview_draft_assembly_payload_chain(self):
        from workbench.server import source_blocks
        # The normal assembly path overlays fields without fabricating body content.
        for path in ('03_types/types.json', '04_knowledge/knowledge.json', '04_knowledge/explanations.json'):
            self.write(path, {'items': []})
        assembled = assemble_local.build(self.root)
        # Assembly's final restoration must use the same scoped inline parser.
        question_content.restore_source_stems(self.root, assembled)
        self.assertEqual(assembled['items'][2]['subquestions'][1]['stem'], self.rows[2]['subquestions'][1]['stem'])
        result = upload_payload.build_payload(assembled, batch_dir=self.root)
        codes = [e['code'] for e in result['errors']]
        self.assertNotIn('GROUP_INCOMPLETE', codes)
        self.assertNotIn('MISSING_STEM', codes)
        self.assertFalse(any(e['code'] == 'UNKNOWN_FIELD' and ('source_span' in e['message'] or 'printed_numbers' in e['message'])
                             for e in result['errors']))
        self.assertNotIn('source_span', result['payload']['items'][0])
        for index, unit in enumerate(self.units):
            chunk = preview.source_slice(self.root, unit['numbers'], unit_id=unit['unit_id'])
            self.assertEqual(chunk[0], unit['source_section']['title'])
            blocks = json.dumps(source_blocks(chunk), ensure_ascii=False)
            self.assertIn(unit['source_section']['title'], blocks)
            wire = result['payload']['items'][index]
            self.assertEqual(wire['stem'], self.rows[index]['stem'])
            if index < 2:
                name = 'image' + str(index + 2) + '.jpeg'
                self.assertIn(name, blocks)
                self.assertEqual(wire['attachments'][0]['name'], name)

    def test_both_local_checkers_share_proof_and_keep_answers_required(self):
        structure = self.check_script('check_structure')
        verified = self.verified()
        self.assertEqual([structure._check_unit(r, verified) for r in self.rows], [[], [], []])
        publish = self.check_script('publish_check')
        self.assertEqual(publish.main(['--batch', str(self.root / '06_batch/batch.json')]), 0)
        broken = copy.deepcopy(self.rows[0])
        broken['subquestions'][0]['answer'] = ''
        self.assertTrue(any('[A9]' in p for p in structure._check_unit(broken, verified)))

    def test_picture_sentence_group_keeps_rubric_and_one_shared_image(self):
        from workbench.server import source_blocks
        from workflow import denoise, extract, handoff, structure_gate

        text = ('Starter Unit 2 Keep Tidy!\n第四课时 Project\n'
                '**Ⅰ. 根据图片提示完成下列句子**\n[IMG shared.jpeg]\n'
                '**1.**The ball is ______ the chair.\n**2.**The books are ______ the desk.')
        structure, text, _ = extract.infer_structure(text)
        units = structure_gate._build_units(structure['sections'], structure['groups'],
                                            denoise.clean_body(text)[0].splitlines())
        self.assertEqual(len(units), 1)
        unit = units[0]
        for path, content in (('正文.txt', text), ('structure.json', structure),
                              ('structure.confirmed.json', {'units': units})):
            self.write('01_source/' + path, content)
        media = self.root / '01_source/media/shared.jpeg'
        media.write_bytes(b'shared-source-image')
        row = question_draft.build(self.root)['items'][0]
        row['question_type'] = '完成句子'
        for child in row['subquestions']:
            child['answer'] = 'under'
        row['attachments'][0]['url'] = '/source-media/' + self.root.name + '/shared.jpeg'
        self.assertEqual(row['stem'], unit['source_section']['title'])
        self.assertEqual(row['material'], '')
        self.assertEqual(phonetic_structure.shape(row, self.verified()), (True, False))
        check = self.check_script('check_structure')._check_unit
        self.assertEqual(check(row, self.verified()), [])
        self.assertEqual(question_content.audit_batch_source(self.root, {'items': [row]})['issues'], [])
        filled = {'items': [row]}
        early = copy.deepcopy(filled)
        early['items'][0]['stem'] = ''
        for filename in ('content.snapshot.json', 'batch_meta.base.json', 'batch_meta.json'):
            self.write('06_batch/' + filename, early)
        final = assemble_local.project_final_view(self.root, filled)
        self.assertEqual(final['items'][0]['stem'], row['stem'])
        self.write('06_batch/batch_meta.json', final)
        self.assertEqual(assemble_local.project_final_view(self.root, filled), final)
        headings = phonetic_structure.verified_picture_headings(self.root, units)
        handoff._assert_underline_projection(early, final, source_headings=headings)
        chunk = preview.source_slice(self.root, row['numbers'], unit_id=row['unit_id'])
        self.assertEqual(chunk[:2], [row['stem'], '[IMG shared.jpeg]'])
        self.assertIn('shared.jpeg', json.dumps(source_blocks(chunk)))
        result = upload_payload.build_payload(final, batch_dir=self.root)
        self.assertNotIn('GROUP_INCOMPLETE', {e['code'] for e in result['errors']})
        wire = result['payload']['items'][0]
        self.assertEqual(wire['stem'], row['stem'])
        self.assertEqual(wire['attachments'], row['attachments'])
        for invalid in ([], [dict(row['attachments'][0], placement='answer')],
                        [dict(row['attachments'][0], name='other.jpeg')]):
            self.assertTrue(any('[A8]' in problem for problem in
                                check({**row, 'attachments': invalid}, self.verified())))
        media.unlink()
        self.assertFalse(self.verified())
        self.assertTrue(any('[A8]' in problem for problem in check(row, self.verified())))

    def test_heading_word_bank_group_keeps_heading_rubric_and_passes_all_layers(self):
        from workflow import denoise, extract, handoff, structure_gate

        text = ('Unit 2 Home Sweet Home\n第三课时 Grammar Focus\n'
                '**Ⅱ. 用can、can’t、could或couldn’t的适当形式填空**\n\n'
                '**1.**—________ I ask you a question?\n'
                '—Yes, of course.\n'
                '**2.**My brother ________ play the piano when he was five.')
        structure, text, _ = extract.infer_structure(text)
        units = structure_gate._build_units(structure['sections'], structure['groups'],
                                            denoise.clean_body(text)[0].splitlines())
        self.assertEqual(len(units), 1)
        unit = units[0]
        self.assertEqual(unit['unit_kind'], 'group')
        self.assertEqual(unit['type'], '选词填空')
        for path, content in (('正文.txt', text), ('structure.json', structure),
                              ('structure.confirmed.json', {'units': units})):
            self.write('01_source/' + path, content)
        row = question_draft.build(self.root)['items'][0]
        row['question_type'] = '选词填空'
        row['filterMetadata'] = {'subtypes': ['多句']}
        row['knowledge'] = ['语篇体裁/应用文/其他', '主题/人与自我/生活与学习/日常起居']
        for child, ans in zip(row['subquestions'], ('Could', 'could')):
            child['answer'] = ans
        self.assertEqual(row['stem'], unit['source_section']['title'])
        self.assertEqual(row['material'], '')
        self.assertEqual(phonetic_structure.shape(row, self.verified()), (True, False))
        check = self.check_script('check_structure')._check_unit
        self.assertEqual(check(row, self.verified()), [])
        self.assertTrue(any('[A8]' in p for p in check({**row, 'stem': ''}, self.verified())))
        self.assertTrue(any('[A8]' in p for p in check({**row, 'stem': '编造指令'}, self.verified())))
        self.assertEqual(question_content.audit_batch_source(self.root, {'items': [row]})['issues'], [])
        filled = {'items': [row]}
        early = copy.deepcopy(filled)
        early['items'][0]['stem'] = ''
        for filename in ('content.snapshot.json', 'batch_meta.base.json', 'batch_meta.json'):
            self.write('06_batch/' + filename, early)
        final = assemble_local.project_final_view(self.root, filled)
        self.assertEqual(final['items'][0]['stem'], row['stem'])
        headings = phonetic_structure.verified_picture_headings(self.root, units)
        handoff._assert_underline_projection(early, final, source_headings=headings)
        chunk = preview.source_slice(self.root, row['numbers'], unit_id=row['unit_id'])
        self.assertEqual(chunk[0], row['stem'])
        result = upload_payload.build_payload(final, batch_dir=self.root)
        error_codes = {e['code'] for e in result['errors']}
        self.assertNotIn('GROUP_INCOMPLETE', error_codes)
        self.assertNotIn('MISSING_STEM', error_codes)
        self.assertEqual(result['payload']['items'][0]['stem'], row['stem'].replace('**', ''))

        # 反向保障：真正需要方框词库的 word-bank 组若 material 为空，绝不豁免 A8
        wb_text = ('Unit 2 Home Sweet Home\n第三课时 Grammar Focus\n'
                   '**Ⅱ. 用方框中所给单词的适当形式填空**\n'
                   '**1.**He is ______.\n**2.**She is ______.')
        wb_struct, wb_text, _ = extract.infer_structure(wb_text)
        wb_units = structure_gate._build_units(wb_struct['sections'], wb_struct['groups'],
                                               denoise.clean_body(wb_text)[0].splitlines())
        for path, content in (('正文.txt', wb_text), ('structure.json', wb_struct),
                              ('structure.confirmed.json', {'units': wb_units})):
            self.write('01_source/' + path, content)
        wb_row = question_draft.build(self.root)['items'][0]
        wb_row['question_type'] = '选词填空'
        for child in wb_row['subquestions']:
            child['answer'] = 'kind'
        self.assertEqual(wb_row['stem'], '')
        self.assertEqual(wb_row['material'], '')
        self.assertTrue(any('[A8]' in p for p in check(wb_row, self.verified())))

    def test_no_source_context_is_strict_even_for_plausible_native_attachment(self):
        self.assertEqual(phonetic_structure.shape(self.rows[0]), (False, False))
        codes = [p['code'] for p in upload_payload.build_payload({'items': self.rows})['errors']]
        self.assertIn('GROUP_INCOMPLETE', codes)
        self.assertIn('MISSING_STEM', codes)

    def test_tampered_identity_rubric_type_image_or_child_is_rejected(self):
        verified = self.verified()
        variants = []
        for key, value in [('question_type', '阅读理解'), ('stem', '编造指令'),
                           ('section_id', 'foreign'), ('printed_numbers', [1])]:
            row = copy.deepcopy(self.rows[0]); row[key] = value; variants.append(row)
        row = copy.deepcopy(self.rows[0]); row['attachments'] = []; variants.append(row)
        row = copy.deepcopy(self.rows[0]); row['attachments'][0]['url'] = '/source-media/x/wrong.jpeg'; variants.append(row)
        row = copy.deepcopy(self.rows[1]); row['subquestions'][0]['stem'] = ''; variants.append(row)
        for row in variants:
            with self.subTest(row=row):
                self.assertFalse(all(phonetic_structure.shape(row, verified)))
        for row in variants[:3] + variants[4:]:
            self.assertEqual(phonetic_structure.shape(row, verified), (False, False))

    def test_missing_empty_or_shared_media_cannot_prove_image_exception(self):
        image = self.root / '01_source/media/image2.jpeg'
        image.unlink()
        self.assertNotIn(self.units[0]['unit_id'], self.verified())
        image.write_bytes(b'')
        self.assertNotIn(self.units[0]['unit_id'], self.verified())
        image.write_bytes(b'image')
        self.units[1]['stem'] = self.units[1]['stem'].replace('image3.jpeg', 'image2.jpeg')
        self.write('01_source/正文.txt', self.text.replace('image3.jpeg', 'image2.jpeg'))
        self.write('01_source/structure.confirmed.json', {'units': self.units})
        self.assertNotIn(self.units[0]['unit_id'], self.verified())

    def test_stale_or_cross_section_source_heading_cannot_supply_rubric(self):
        for mutate in ('title', 'start_line'):
            units = copy.deepcopy(self.units)
            units[0]['source_section'][mutate] = 'forged' if mutate == 'title' else 4
            self.write('01_source/structure.confirmed.json', {'units': units, 'identity_version': 'scoped-v1'})
            self.assertNotIn(self.units[0]['unit_id'], self.verified())

    def test_precheck_temporary_candidate_needs_explicit_source_context(self):
        from workflow.assemble_precheck import precheck
        with patch('workflow.assemble_precheck.SCRIPTS', ('check_structure.py',)):
            self.assertFalse(precheck({'items': self.rows})['passed'])
            self.assertTrue(precheck({'items': self.rows}, batch_dir=self.root)['passed'])

    def test_string_source_numbers_are_supported_without_changing_identity(self):
        units = copy.deepcopy(self.units)
        for unit in units:
            unit['numbers'] = [str(n) for n in unit['numbers']]
        self.write('01_source/structure.confirmed.json', {'units': units, 'identity_version': 'scoped-v1'})
        self.assertEqual(len(self.verified()), 3)

    def test_source_owner_must_include_whole_section_and_exact_span(self):
        verified = self.verified()
        row = copy.deepcopy(self.rows[2])
        row['source_span']['end_line'] -= 1
        self.assertEqual(phonetic_structure.shape(row, verified), (False, False))
        for mutation in ('end_line', 'cross_section', 'omit_child'):
            units = copy.deepcopy(self.units)
            if mutation == 'end_line':
                units[0]['source_section']['end_line'] += 1
            elif mutation == 'cross_section':
                units[0]['source_span']['end_line'] = units[1]['source_span']['end_line']
                units[0]['source_section']['end_line'] = units[1]['source_section']['end_line']
            else:
                units[2]['numbers'] = units[2]['numbers'][:1]
                units[2]['question_ids'] = {'1': units[2]['question_ids']['1']}
            self.write('01_source/structure.confirmed.json', {'units': units, 'identity_version': 'scoped-v1'})
            uid = units[2 if mutation == 'omit_child' else 0]['unit_id']
            self.assertNotIn(uid, self.verified(), mutation)

    def test_unchanged_fill_source_refreshes_span_without_changing_guard_hash(self):
        from workflow import batch_fill
        old = copy.deepcopy(self.rows[1]); old.pop('source_span')
        fresh = copy.deepcopy(self.rows[1])
        before = batch_fill._source_token(old, {})
        batch_fill._sync_media_attachments(old, fresh, self.root.name)
        self.assertEqual(old['source_span'], fresh['source_span'])
        self.assertEqual(before, batch_fill._source_token(old, {}))
        self.assertEqual(batch_fill._refresh_row({}, fresh, preserve_fill=True)['source_span'], fresh['source_span'])

    def test_attachment_url_must_belong_to_current_source_batch(self):
        for url in ('https://evil.test/image2.jpeg', '/source-media/other/image2.jpeg',
                    '01_source/media/image2.jpeg?x=1'):
            row = copy.deepcopy(self.rows[0]); row['attachments'][0]['url'] = url
            self.assertEqual(phonetic_structure.shape(row, self.verified()), (False, False))

    def test_source_choices_only_phonetic_group_survives_draft_and_payload(self):
        from workflow import extract, structure_gate, denoise
        text = ('Unit 1 Where did you go on vacation?\n第一课时 Section A\n'
                'Ⅰ.找出每组单词中画线部分的读音不同的一项\n'
                '(　)1.A.c<u>a</u>t　B.<u>a</u>pple　C.n<u>a</u>me　D.b<u>a</u>g\n'
                '(　)2.A.p<u>e</u>n　B.r<u>e</u>d　C.b<u>e</u>d　D.h<u>e</u>\n'
                '(　)3.A.b<u>i</u>g　B.s<u>i</u>t　C.t<u>i</u>me　D.f<u>i</u>sh\n'
                '(　)4.A.n<u>o</u>t　B.d<u>o</u>g　C.b<u>o</u>x　D.h<u>o</u>me\n'
                '(　)5.A.c<u>u</u>p　B.b<u>u</u>s　C.m<u>u</u>sic　D.s<u>u</u>n')
        structure, body, _ = extract.infer_structure(text)
        lines = denoise.clean_body(body)[0].splitlines()
        units = structure_gate._build_units(structure['sections'], structure['groups'], lines)
        self.write('01_source/正文.txt', body)
        self.write('01_source/structure.json', structure)
        self.write('01_source/structure.confirmed.json', {'units': units, 'identity_version': 'scoped-v1'})
        draft = question_draft.build(self.root)
        self.assertEqual(draft['issues'], [])
        self.assertEqual(len(draft['items']), 1)
        row = draft['items'][0]
        row['question_type'] = '语音题'
        self.assertEqual(row['material'], '')
        self.assertTrue(row['stem'])
        self.assertEqual(len(row['subquestions']), 5)
        self.assertTrue(all(not child['stem'] and len(child['options']) == 4 for child in row['subquestions']))
        verified = self.verified()
        self.assertEqual(phonetic_structure.shape(row, verified), (True, True))
        # Keep an untouched source-shaped baseline: a consumer may normalize its
        # input objects in place, so each invocation owns a fresh deep copy.
        baseline = copy.deepcopy(row)
        result = upload_payload.build_payload({'items': [copy.deepcopy(baseline)]}, batch_dir=self.root)
        self.assertEqual(row, baseline)
        codes = {error['code'] for error in result['errors']}
        self.assertNotIn('GROUP_INCOMPLETE', codes)
        self.assertNotIn('MISSING_STEM', codes)
        self.assertEqual(result['payload']['items'][0]['subquestions'][0]['stem'], '')
        for mutation in ('missing', 'changed', 'child_id', 'invented_stem'):
            bad = copy.deepcopy(baseline)
            child = bad['subquestions'][0]
            if mutation == 'missing':
                child['options'].pop()
            elif mutation == 'changed':
                child['options'][0] = 'A. fabricated'
            elif mutation == 'child_id':
                child['question_id'] = 'foreign'
            else:
                child['stem'] = 'fabricated'
            self.assertEqual(phonetic_structure.shape(bad, verified), (False, False), mutation)
            before_call = copy.deepcopy(bad)
            codes = {e['code'] for e in upload_payload.build_payload({'items': [copy.deepcopy(bad)]}, batch_dir=self.root)['errors']}
            self.assertEqual(bad, before_call)
            self.assertEqual(row, baseline)
            self.assertIn('GROUP_INCOMPLETE', codes, mutation)
        # A deficient original is not enough proof, even if the batch matches it.
        deficient = copy.deepcopy(verified)
        deficient[baseline['unit_id']]['subquestions'][0]['options'].pop()
        same = copy.deepcopy(baseline)
        same['subquestions'][0]['options'].pop()
        self.assertEqual(phonetic_structure.shape(same, deficient), (False, False))
        self.assertEqual(phonetic_structure.shape(row), (False, False))

    def test_source_judgement_group_with_three_choices_needs_no_material(self):
        """Ⅲ 判断题给 3 个待比较项、按 S/D 作答：只有印刷选项，同样合法。"""
        from workflow import extract, structure_gate, denoise
        text = ('Unit 3 Welcome!\n第二课时 Pronunciation\n'
                'Ⅲ.判断每组画线部分的读音是否相同(相同的在括号内写S,不同的写D)\n'
                '(　)1.A.m<u>a</u>p　B.<u>a</u>pple　C.w<u>a</u>nt\n'
                '(　)2.A.tw<u>e</u>lve　B.g<u>e</u>t　C.y<u>e</u>s\n'
                '(　)3.A.d<u>o</u>　B.h<u>o</u>t　C.d<u>o</u>g')
        structure, body, _ = extract.infer_structure(text)
        lines = denoise.clean_body(body)[0].splitlines()
        units = structure_gate._build_units(structure['sections'], structure['groups'], lines)
        self.write('01_source/正文.txt', body)
        self.write('01_source/structure.json', structure)
        self.write('01_source/structure.confirmed.json', {'units': units, 'identity_version': 'scoped-v1'})
        draft = question_draft.build(self.root)
        self.assertEqual(draft['issues'], [])
        row = draft['items'][0]
        row['question_type'] = '语音题'
        self.assertEqual(row['material'], '')
        self.assertEqual(len(row['subquestions']), 3)
        self.assertTrue(all(not child['stem'] and len(child['options']) == 3
                            for child in row['subquestions']))
        verified = self.verified()
        self.assertEqual(phonetic_structure.shape(row, verified), (True, True))
        # 只有 3 个选项、题干又没写“判断/是否相同 + S/D”时不能当成合法形式。
        expected = copy.deepcopy(verified)
        expected[row['unit_id']]['stem'] = 'Ⅲ.找出每组单词中画线部分的读音不同的一项'
        self.assertEqual(phonetic_structure.shape(row, expected), (False, False))

    def test_source_audit_requires_instruction_and_shared_image(self):
        for field in ('stem', 'attachments'):
            rows = copy.deepcopy(self.rows)
            rows[0][field] = '' if field == 'stem' else []
            codes = [p['code'] for p in question_content.audit_batch_source(self.root, {'items': rows})['issues']]
            self.assertIn('SOURCE_SHARED_RUBRIC_MISSING' if field == 'stem' else 'SOURCE_IMAGE_MISSING', codes)


if __name__ == '__main__':
    unittest.main()
