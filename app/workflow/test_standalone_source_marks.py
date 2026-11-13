"""Marked unnumbered standalone tasks retain only their proven source heading."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import denoise, extract, question_draft, structure_gate, sync_practice


class StandaloneSourceMarksTest(unittest.TestCase):
    heading = 'Ⅰ.[**跨学科**]写出下列古诗画线句中表示数字的英文单词'
    poem = '《咏雪》<br>郑燮<br><u>一片两片三四片</u>,五六七八九十片。<br>千片万片无数片,飞入梅花都不见。'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def source(self, heading=None, body=None):
        raw = '\n'.join(['七上 · 人教版 · 课时A计划', 'Starter Unit 3 Welcome!',
                         '第四课时 Project', heading or self.heading,
                         body or '[TABLE 1]\n' + self.poem + '\n[/TABLE]\n<u>　</u>'])
        structure, raw, _ = extract.infer_structure(raw)
        cleaned = denoise.clean_body(raw)[0]
        units = structure_gate._build_units(structure['sections'], structure.get('groups', []), cleaned.splitlines())
        folder = self.root / '01_source'
        folder.mkdir(exist_ok=True)
        (folder / '正文.txt').write_text(raw, encoding='utf-8')
        for name, value in [('structure.json', structure), ('structure.confirmed.json', {'units': units})]:
            (folder / name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        if body is None:
            table = '[TABLE 1]\n' + self.poem + '\n[/TABLE]'
            (folder / 'extraction.json').write_text(json.dumps({'tables': [
                {'number': 1, 'text': table, 'rows': [{'cells': [
                    {'text': self.poem, 'rowspan': 1, 'colspan': 1}]}]}]}, ensure_ascii=False), encoding='utf-8')
        self.assertTrue(units)
        return raw, units

    def test_complete_actual_heading_and_poem_survive_consumers(self):
        from workflow import question_content, upload_payload
        from workbench.server import source_blocks
        raw, units = self.source()
        draft = question_draft.ensure(self.root)
        row = draft['items'][0]
        with patch.object(question_draft, '_standalone_marked_heading', return_value=''):
            baseline = question_draft.build(self.root)['items'][0]
        self.assertEqual(row['stem'], self.heading + '\n' + baseline['stem'])
        self.assertEqual(row['materialBlocks'], baseline['materialBlocks'])
        self.assertTrue(row['stem'].startswith(self.heading + '\n'))
        self.assertEqual(row['stem'].count(self.heading), 1)
        self.assertIn('千片万片无数片,飞入梅花都不见。', row['stem'])
        self.assertIn('《咏雪》', row['materialBlocks'][0]['rows'][0]['cells'][0]['text'])
        self.assertEqual(sync_practice.audit_batch_source_marks([row], raw), [])
        self.assertTrue(question_content.audit_batch_source(self.root, {'items': [row]})['passed'])
        self.assertIn('千片万片无数片', json.dumps(source_blocks(row['stem'].splitlines()), ensure_ascii=False))
        row['question_type'] = '单词拼写'
        payload = upload_payload.build_payload({'items': [row]}, catalog=None)['payload']
        self.assertIn('跨学科', json.dumps(payload, ensure_ascii=False))
        self.assertIn('千片万片无数片', json.dumps(payload, ensure_ascii=False))
        self.assertEqual(question_draft.ensure(self.root)['content_sha256'], draft['content_sha256'])

    def test_wrong_heading_position_or_section_identity_cannot_borrow_mark(self):
        raw, units = self.source()
        for mutate in [lambda u: u['source_section'].update(start_line=1),
                       lambda u: u.update(section_id='other-section'),
                       lambda u: u['source_section'].update(title='Ⅰ.[情境题]写出英文单词'),
                       lambda u: u['source_section'].update(occurrence=2)]:
            bad = copy.deepcopy(units)
            mutate(bad[0])
            row = question_draft.build(self.root, units=bad)['items'][0]
            self.assertNotIn('跨学科', row['stem'])
            self.assertTrue(sync_practice.audit_batch_source_marks([row], raw))

    def test_partial_fragment_does_not_gain_section_heading(self):
        _, units = self.source()
        units[0]['stem'] = '<u>　</u>'
        units[0]['source_span']['start_line'] = units[0]['source_span']['end_line']
        row = question_draft.build(self.root, units=units)['items'][0]
        self.assertNotIn('跨学科', row['stem'])

    def test_ordinary_unmarked_title_is_not_added(self):
        _, _ = self.source(heading='Ⅰ.写出下列古诗画线句中表示数字的英文单词')
        row = question_draft.build(self.root)['items'][0]
        self.assertNotIn('写出下列', row['stem'])
        self.assertIn('千片万片无数片', row['stem'])

    def test_numbered_task_does_not_receive_heading(self):
        _, _ = self.source(body='1.[跨学科]Write one.\n2.Write two.')
        rows = question_draft.build(self.root)['items']
        self.assertEqual(rows[0]['stem'].count('跨学科'), 1)
        self.assertNotIn('写出下列', rows[0]['stem'])
        self.assertNotIn('跨学科', rows[1]['stem'])

    def test_parser_repair_refreshes_persisted_draft_without_source_edit(self):
        self.source()
        source = (self.root / '01_source/正文.txt').read_bytes()
        with patch.object(question_draft, '_standalone_marked_heading', return_value=''):
            old = question_draft.ensure(self.root)
        new = question_draft.ensure(self.root)
        self.assertNotEqual(old['content_sha256'], new['content_sha256'])
        uid = new['items'][0]['unit_id']
        self.assertEqual(old['source_bindings'][uid]['source_text_sha256'],
                         new['source_bindings'][uid]['source_text_sha256'])
        self.assertEqual((self.root / '01_source/正文.txt').read_bytes(), source)


if __name__ == '__main__':
    unittest.main()
