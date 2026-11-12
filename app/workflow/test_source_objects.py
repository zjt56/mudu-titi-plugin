"""Synthetic native object/projection checks; never run or upload a user batch."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import docx_extract, question_draft, source_objects, upload_payload
from workflow import test_docx_extract as docx_fixtures
from workflow import test_upload_payload as payload_fixtures
from workbench.server import source_blocks

cell = docx_fixtures.cell
paragraph = docx_fixtures.paragraph
image = docx_fixtures.image
R = docx_fixtures.R


class SourceObjectTests(unittest.TestCase):
    setUp = docx_fixtures.DocxExtractTests.setUp
    make_docx = docx_fixtures.DocxExtractTests.make_docx
    run_extract = docx_fixtures.DocxExtractTests.run_extract

    def test_refs_bind_source_path_and_each_shared_image_occurrence(self):
        table = '<w:tbl><w:tr><w:tc><w:p>' + image() + '</w:p></w:tc></w:tr></w:tbl>'
        self.make_docx('<w:p>' + image() + '</w:p>' + table,
                       f'<Relationship Id="image1" Type="{R}/image" Target="media/x.png"/>',
                       parts={"word/media/x.png": b"image"})
        result = self.run_extract()
        index = result["source_objects"]
        self.assertEqual(index["source_sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        ids = [obj["id"] for obj in index["objects"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertNotEqual(result["media"][0]["source_ref"], result["media"][1]["source_ref"])
        cell_obj = result["tables"][0]["rows"][0]["cells"][0]
        by_id = {obj["id"]: obj for obj in index["objects"]}
        para_id = by_id[result["media"][1]["source_ref"]]["parent"]
        self.assertEqual(by_id[para_id]["parent"], cell_obj["source_ref"])
        # Rebuilding the index is deterministic and does not alter source content.
        source_objects.attach(result, index["source_sha256"])
        self.assertEqual(ids, [obj["id"] for obj in result["source_objects"]["objects"]])

    def test_table_resolution_reflow_keeps_literal_pipe_and_empty_rows(self):
        table = ('<w:tbl><w:tblGrid><w:gridCol/><w:gridCol/><w:gridCol/></w:tblGrid>'
                 '<w:tr><w:trPr><w:gridBefore w:val="1"/><w:gridAfter w:val="1"/></w:trPr>'
                 + cell('left | right') + '</w:tr><w:tr>' + cell('') * 3 + '</w:tr></w:tbl>')
        self.make_docx(table)
        result = self.run_extract()
        marker = result['text'].replace(' | ', '|').replace('\n', '\n  ')
        found, reason = source_objects.resolve_table(result, marker)
        self.assertFalse(reason)
        self.assertEqual(found['rows'][0]['cells'][0]['text'], 'left | right')
        refs, issues = [], []
        blocks = question_draft._table_blocks(marker, result, issues, 'scope-A', references=refs)
        self.assertFalse(issues)
        self.assertEqual([cell['text'] for cell in blocks[0]['rows'][0]['cells']], ['', 'left | right', ''])
        self.assertEqual([cell['text'] for cell in blocks[0]['rows'][1]['cells']], ['', '', ''])
        self.assertEqual(refs[0]['source_ref'], found['source_ref'])

    def test_source_ref_corruption_never_falls_back_to_number_only(self):
        self.make_docx('<w:tbl><w:tr>' + cell('not the same') + '</w:tr></w:tbl>')
        result = self.run_extract()
        marker = result['text']
        changed = copy.deepcopy(result)
        changed['tables'][0]['rows'][0]['cells'][0]['colspan'] = 2
        self.assertIsNone(source_objects.resolve_table(changed, marker)[0])
        self.assertIsNone(source_objects.resolve_table(result, marker.replace('not ', ''))[0])
        changed = copy.deepcopy(result)
        changed['source_objects']['objects'].append(copy.deepcopy(changed['source_objects']['objects'][0]))
        self.assertIsNone(source_objects.resolve_table(changed, marker)[0])
        changed = copy.deepcopy(result)
        changed['tables'][0]['source_ref'] = 'src-forged'
        self.assertIsNone(source_objects.resolve_table(changed, marker)[0])

    def test_table_resolution_rejects_removed_english_word_boundary(self):
        self.make_docx('<w:tbl><w:tr>' + cell('an apple') + '</w:tr></w:tbl>')
        result = self.run_extract()
        marker = result['text']
        found, reason = source_objects.resolve_table(result, marker)
        self.assertIsNotNone(found)
        self.assertEqual(reason, '')
        found, reason = source_objects.resolve_table(result, marker.replace('an apple', 'anapple'))
        self.assertIsNone(found)
        self.assertEqual(reason, '表格编号与完整原文不能唯一绑定')

    def test_table_resolution_rejects_index_and_extraction_source_digest_mismatch(self):
        self.make_docx('<w:tbl><w:tr>' + cell('same content') + '</w:tr></w:tbl>')
        result = self.run_extract()
        source_sha = result['source_objects']['source_sha256']
        result['source_sha256'] = source_sha
        self.assertIsNotNone(source_objects.resolve_table(result, result['text'])[0])
        changed = copy.deepcopy(result)
        changed['source_sha256'] = '0' * 64 if source_sha != '0' * 64 else '1' * 64
        found, reason = source_objects.resolve_table(changed, result['text'])
        self.assertIsNone(found)
        self.assertEqual(reason, '原生来源对象摘要与提取原件不一致')

    def supported_table_batch(self):
        table = ('<w:tbl><w:tblGrid><w:gridCol/><w:gridCol/><w:gridCol/></w:tblGrid>'
                 '<w:tr>' + cell('left | right', '<w:gridSpan w:val="2"/><w:vMerge w:val="restart"/>')
                 + cell('an  apple') + '</w:tr><w:tr>'
                 + cell('', '<w:gridSpan w:val="2"/><w:vMerge/>') + cell('bottom')
                 + '</w:tr><w:tr>' + cell('') * 3 + '</w:tr></w:tbl>')
        self.make_docx(paragraph('阅读下面表格。') + table + ''.join(
            paragraph(f'{n}. Choose. A. Yes B. No') for n in (1, 2, 3)))
        extraction = self.run_extract()
        source = self.root / '01_source'
        source.mkdir()
        unit = {'unit_id': 'group-1-1', 'numbers': [1, 2, 3], 'unit_kind': 'group', 'type': '阅读理解',
                'stem': extraction['text'], 'source_span': {'basis': 'ai_classified', 'start_line': 1,
                'end_line': len(extraction['text'].splitlines())}}
        files = {'extraction.json': extraction, 'structure.confirmed.json': {'units': [unit], 'meta': {}},
                 'structure.json': {'sections': [], 'groups': []}}
        for name, value in files.items():
            (source / name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        (source / '正文.txt').write_text(extraction['text'], encoding='utf-8')
        draft = question_draft.build(self.root, units=[unit])
        item = copy.deepcopy(draft['items'][0])
        sample = payload_fixtures.reading_group()
        for key in ('question_type', 'difficulty', 'filterMetadata', 'knowledge', 'articleSummary',
                    'source_type', 'year', 'source', 'location', 'version', 'authorization'):
            item[key] = copy.deepcopy(sample[key])
        for child, template in zip(item['subquestions'], sample['subquestions']):
            for key in ('answer', 'explanation', 'knowledge'):
                child[key] = copy.deepcopy(template[key])
        return extraction, draft, payload_fixtures.batch(item)

    def test_supported_native_table_survives_draft_preview_and_actual_payload(self):
        extraction, draft, batch = self.supported_table_batch()
        self.assertFalse(draft['issues'], draft['issues'])
        self.assertFalse(draft['layout_diagnostics'])
        blocks = draft['items'][0]['materialBlocks']
        self.assertEqual(blocks[0]['rows'][0]['cells'][0],
                         {'text': 'left | right', 'rowspan': 2, 'colspan': 2})
        self.assertEqual(blocks[0]['rows'][0]['cells'][1]['text'], 'an  apple')
        self.assertEqual([cell['text'] for cell in blocks[0]['rows'][2]['cells']], ['', '', ''])
        preview = next(block for block in source_blocks(extraction['text'].splitlines(), extraction)
                       if block['type'] == 'table')
        self.assertEqual(preview['rows'], [['left | right', 'an  apple'], ['bottom'], ['', '', '']])
        self.assertEqual(preview['cellSpans'][0][0], {'rowspan': 2, 'colspan': 2})
        before = {path: path.read_bytes() for path in (self.root / '01_source').iterdir()}
        with patch('workflow.upload_numbering.project_batch', return_value={
                'items': batch['items'], 'errors': [], 'evidence': {}}), \
             patch('workflow.upload_payload.label_audit.inspect_batch', return_value={'errors': []}):
            report = upload_payload.build_payload(batch, payload_fixtures.catalog_snapshot(), batch_dir=self.root)
        self.assertTrue(report['passed'], report['errors'])
        self.assertEqual(report['payload']['items'][0]['materialBlocks'], blocks)
        self.assertNotIn('source_ref', json.dumps(report['payload']))
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_legal_table_grid_with_changed_cell_ownership_or_span_is_rejected(self):
        _, _, batch = self.supported_table_batch()
        self.assertEqual(source_objects.upload_errors(self.root, batch['items']), [])
        for change in ('swap', 'span', 'missing'):
            with self.subTest(change=change):
                item = copy.deepcopy(batch['items'][0])
                if change == 'swap':
                    cells = item['materialBlocks'][0]['rows'][0]['cells']
                    cells[0]['text'], cells[1]['text'] = cells[1]['text'], cells[0]['text']
                elif change == 'span':
                    # Horizontal split/merge changes while retaining a complete 3-column grid.
                    rows = item['materialBlocks'][0]['rows']
                    rows[0]['cells'][0]['colspan'] = 1
                    rows[0]['cells'][1]['colspan'] = 2
                    rows[1]['cells'][0]['colspan'] = 2
                else:
                    item.pop('materialBlocks')
                if change != 'missing':
                    validator = upload_payload._Builder(None, {})
                    validator.blocks(item['materialBlocks'], item['unit_id'])
                    self.assertEqual(validator.errors, [])
                errors = source_objects.upload_errors(self.root, [item])
                self.assertEqual([error['code'] for error in errors], ['LAYOUT_TABLE_PROJECTION_MISMATCH'])

    def test_nested_table_stays_one_complete_marker_and_reports_capability(self):
        inner = '<w:tbl><w:tr>' + cell('inside') + '</w:tr></w:tbl>'
        outer = '<w:tbl><w:tr><w:tc>' + paragraph('before') + inner + paragraph('after') + '</w:tc></w:tr></w:tbl>'
        self.make_docx(outer)
        result = self.run_extract()
        markers = list(source_objects.table_markers(result['text']))
        self.assertEqual(markers, [result['text']])
        diagnostics, issues = [], []
        blocks = question_draft._table_blocks(result['text'], result, issues, 'group-A', layout_diagnostics=diagnostics)
        self.assertFalse(issues)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(diagnostics[0]['code'], 'LAYOUT_NESTED_TABLE_UNSUPPORTED')
        self.assertEqual(diagnostics[0]['stage'], 'format_gate')


class TableProjectionCanonicalTests(unittest.TestCase):
    @staticmethod
    def project(*texts):
        return source_objects._table_projection([{'type': 'table', 'rows': [
            {'cells': [{'text': text} for text in texts]}]}])

    def test_blank_and_numbered_blank_forms_are_equivalent(self):
        for native, adapted in (('<u>　　</u>', '______'),
                                ('<u>　26　</u>', '__26__'),
                                ('　26　', '__26__')):
            with self.subTest(native=native):
                self.assertEqual(self.project(native), self.project(adapted))
        self.assertEqual(self.project('an  apple<br>today'), self.project('an apple today'))

    def test_letter_underline_cannot_be_lost_or_moved_between_letters_or_cells(self):
        expected = self.project('c<u>a</u>t', 'cat')
        for texts in (('cat', 'cat'), ('ca<u>t</u>', 'cat'), ('cat', 'c<u>a</u>t')):
            with self.subTest(texts=texts):
                self.assertNotEqual(expected, self.project(*texts))


class ProjectionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / '01_source').mkdir()

    def write(self, name, value):
        path = self.root / '01_source' / name
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def fixture(self):
        marker = '[TABLE 1]\ntext [IMG cell.png] | value\n[/TABLE]'
        table = {'type': 'table', 'number': 1, 'text': marker, 'rows': [{'cells': [
            {'text': 'text [IMG cell.png]', 'column': 0, 'rowspan': 1, 'colspan': 1, 'blocks': []},
            {'text': 'value', 'column': 1, 'rowspan': 1, 'colspan': 1, 'blocks': []}]}]}
        extraction = source_objects.attach({'blocks': [table], 'tables': [table], 'media': []}, 'a' * 64)
        self.table = table
        text = '阅读下面表格。\n' + marker + '\n1. Choose. A. Yes B. No'
        unit = {'unit_id': 'group-1-1', 'numbers': [1], 'unit_kind': 'group', 'type': '阅读理解',
                'stem': text, 'source_span': {'basis': 'ai_classified', 'start_line': 1, 'end_line': 5}}
        self.write('正文.txt', text)
        self.write('structure.confirmed.json', {'units': [unit], 'meta': {}})
        self.write('structure.json', {'sections': [], 'groups': []})
        self.write('extraction.json', extraction)
        (self.root / '01_source/media').mkdir()
        (self.root / '01_source/media/cell.png').write_bytes(b'png')
        return unit

    def test_cell_media_limitation_is_visible_warning_not_a_gate_failure(self):
        unit = self.fixture()
        draft = question_draft.build(self.root, units=[unit])
        self.assertFalse([row for row in draft['issues'] if row['code'].startswith('LAYOUT_')])
        self.assertEqual(draft['layout_diagnostics'][0]['code'], 'LAYOUT_CELL_MEDIA_UNSUPPORTED')
        self.assertEqual(draft['source_bindings']['group-1-1']['source_objects'][0]['binding'], 'native_object')
        before = (self.root / '01_source/正文.txt').read_bytes()
        # Use actual builder, stubbing unrelated source-numbering/labels only.
        batch = {'batch_id': 'test', 'name': 'test', 'items': draft['items']}
        self.assertEqual(source_objects.upload_errors(self.root, batch['items']), [])
        issues = source_objects.layout_issues(self.root, batch['items'])
        self.assertEqual(issues['errors'], [])
        self.assertEqual([row['code'] for row in issues['warnings']], ['LAYOUT_CELL_MEDIA_UNSUPPORTED'])
        self.assertTrue(issues['warnings'][0]['non_blocking'])
        self.assertEqual(issues['warnings'][0]['unit_id'], 'group-1-1')
        self.assertIn('row', issues['warnings'][0])
        with patch('workflow.upload_numbering.project_batch', return_value={
                'items': batch['items'], 'errors': [], 'evidence': {}}), \
             patch('workflow.upload_payload.label_audit.inspect_batch', return_value={'errors': []}):
            built = upload_payload.build_payload(batch, batch_dir=self.root)
        self.assertNotIn('LAYOUT_CELL_MEDIA_UNSUPPORTED', {row['code'] for row in built['errors']})
        self.assertIn('LAYOUT_CELL_MEDIA_UNSUPPORTED', {row['code'] for row in built['warnings']})
        # This fixture is a minimal draft, so unrelated field checks still fail;
        # what matters is that the capability limitation no longer blocks.
        self.assertNotIn('LAYOUT_', {row['code'] for row in built['errors']})
        self.assertEqual(before, (self.root / '01_source/正文.txt').read_bytes())
        self.assertFalse((self.root / 'workflow/manifest.json').exists())

    def test_nested_table_capability_still_blocks_the_final_gate(self):
        unit = self.fixture()
        table = self.table
        table['rows'][0]['cells'][1]['blocks'] = [{'type': 'table', 'rows': [{'cells': [{'text': 'inner'}]}]}]
        self.write('extraction.json', source_objects.attach(
            {'blocks': [table], 'tables': [table], 'media': []}, 'a' * 64))
        items = question_draft.build(self.root, units=[unit])['items']
        issues = source_objects.layout_issues(self.root, items)
        self.assertEqual([row['code'] for row in issues['errors']], ['LAYOUT_NESTED_TABLE_UNSUPPORTED'])
        self.assertEqual([row['code'] for row in issues['warnings']], ['LAYOUT_CELL_MEDIA_UNSUPPORTED'])
        self.assertEqual([row['code'] for row in source_objects.upload_errors(self.root, items)],
                         ['LAYOUT_NESTED_TABLE_UNSUPPORTED'])

    def test_upload_errors_returns_structured_error_for_invalid_json_evidence(self):
        self.fixture()
        for filename in ('extraction.json', 'structure.confirmed.json'):
            with self.subTest(filename=filename):
                path = self.root / '01_source' / filename
                original = path.read_bytes()
                self.write(filename, '{invalid JSON')
                try:
                    errors = source_objects.upload_errors(self.root, [{'unit_id': 'group-1-1'}])
                    self.assertEqual(len(errors), 1)
                    self.assertEqual(errors[0]['code'], 'LAYOUT_SOURCE_EVIDENCE_INVALID')
                    self.assertEqual(errors[0]['path'], '01_source/extraction.json')
                    self.assertTrue(errors[0]['message'].startswith('不能复建当前来源表格证据：'))
                    self.assertEqual(path.read_text(encoding='utf-8'), '{invalid JSON')
                finally:
                    path.write_bytes(original)

    def test_capability_error_is_scoped_not_applied_to_other_unit(self):
        self.fixture()
        draft = question_draft.build(self.root)
        items = copy.deepcopy(draft['items'])
        draft['layout_diagnostics'] = [{'unit_id': 'A', 'code': 'LAYOUT_CELL_MEDIA_UNSUPPORTED',
                                        'reason': 'cell media', 'row': 0, 'column': 0}]
        with patch('workflow.question_draft.build', return_value=draft):
            self.assertEqual(source_objects.upload_errors(self.root, items), [])

    def test_indexed_tables_require_current_source_and_confirmed_evidence(self):
        self.fixture()
        for filename in ('正文.txt', 'structure.confirmed.json'):
            with self.subTest(filename=filename):
                path = self.root / '01_source' / filename
                original = path.read_bytes()
                path.unlink()
                try:
                    errors = source_objects.upload_errors(self.root, [{'unit_id': 'group-1-1'}])
                    self.assertEqual([row['code'] for row in errors], ['LAYOUT_SOURCE_EVIDENCE_INVALID'])
                    self.assertIn(filename, errors[0]['message'])
                finally:
                    path.write_bytes(original)

    def test_null_index_and_indexed_table_with_empty_tables_are_rejected(self):
        self.fixture()
        path = self.root / '01_source/extraction.json'
        extraction = json.loads(path.read_text(encoding='utf-8'))
        for change in ({'source_objects': None}, {'tables': []}):
            with self.subTest(change=change):
                changed = copy.deepcopy(extraction)
                changed.update(change)
                self.write('extraction.json', changed)
                errors = source_objects.upload_errors(self.root, [{'unit_id': 'group-1-1'}])
                self.assertEqual([row['code'] for row in errors], ['LAYOUT_SOURCE_EVIDENCE_INVALID'])

    def test_unknown_owner_rejected_and_only_current_owner_units_are_rebuilt(self):
        unit = self.fixture()
        errors = source_objects.upload_errors(self.root, [{'unit_id': 'unknown'}])
        self.assertEqual([row['code'] for row in errors], ['LAYOUT_SOURCE_EVIDENCE_INVALID'])
        other = copy.deepcopy(unit)
        other['unit_id'] = 'other'
        other['numbers'] = [2]
        self.write('structure.confirmed.json', {'units': [unit, other], 'meta': {}})
        with patch('workflow.question_draft.build', return_value={
                'items': [{'unit_id': unit['unit_id']}], 'layout_diagnostics': [], 'issues': []}) as build:
            self.assertEqual(source_objects.upload_errors(self.root, [{'unit_id': unit['unit_id']}]), [])
        self.assertEqual([row['unit_id'] for row in build.call_args.kwargs['units']], [unit['unit_id']])

    def test_only_current_owner_unproven_scope_becomes_layout_binding_error(self):
        self.fixture()
        draft = {'items': [{'unit_id': 'group-1-1'}], 'layout_diagnostics': [], 'issues': [
            {'unit_id': 'group-1-1', 'code': 'SOURCE_SCOPE_UNPROVEN', 'reason': 'current scope lost'},
            {'unit_id': 'other', 'code': 'SOURCE_SCOPE_UNPROVEN', 'reason': 'unrelated'},
            {'unit_id': 'group-1-1', 'code': 'CHILD_CONTENT_UNRESOLVED', 'reason': 'not a scope error'}]}
        with patch('workflow.question_draft.build', return_value=draft):
            errors = source_objects.upload_errors(self.root, [{'unit_id': 'group-1-1'}])
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]['unit_id'], 'group-1-1')
        self.assertEqual(errors[0]['code'], 'LAYOUT_SOURCE_BINDING_UNPROVEN')
        self.assertIn('current scope lost', errors[0]['message'])


if __name__ == '__main__':
    unittest.main()
