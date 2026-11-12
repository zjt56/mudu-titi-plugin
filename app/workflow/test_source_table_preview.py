"""Synthetic source-table preview checks; no batch/service/upload is run."""
from copy import deepcopy
import tempfile
import unittest
import zipfile
from pathlib import Path

from workbench.server import attach_media, source_blocks
from workflow.docx_extract import extract_docx
from workflow.test_docx_extract import W, R, A, cell, image, paragraph


class SourceTablePreviewTest(unittest.TestCase):
    def native(self, body, rels="", parts=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        source = root / "source.docx"
        with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", f'<w:document xmlns:w="{W}" xmlns:r="{R}" xmlns:a="{A}"><w:body>{body}</w:body></w:document>')
            if rels:
                archive.writestr("word/_rels/document.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + rels + '</Relationships>')
            for name, content in (parts or {}).items():
                archive.writestr(name, content)
        result = extract_docx(source, root / "01_source")
        return root, result

    def test_native_cells_keep_literal_pipe_images_merges_and_empty_rows(self):
        picture_cell = ('<w:tc><w:p><w:r><w:t>before | after</w:t></w:r>'
                        + image() + '</w:p></w:tc>')
        body = ('<w:tbl><w:tblGrid><w:gridCol/><w:gridCol/><w:gridCol/></w:tblGrid>'
                '<w:tr>' + cell('merged', '<w:gridSpan w:val="2"/><w:vMerge w:val="restart"/>')
                + picture_cell + '</w:tr><w:tr>'
                + cell('', '<w:gridSpan w:val="2"/><w:vMerge/>') + cell('bottom')
                + '</w:tr><w:tr>' + cell('') + cell('') + cell('') + '</w:tr></w:tbl>')
        root, extraction = self.native(body, f'<Relationship Id="image1" Type="{R}/image" Target="media/p.png"/>',
                                       {'word/media/p.png': b'synthetic image'})
        blocks = source_blocks(extraction['text'].splitlines(), extraction)
        self.assertEqual(len(blocks), 1)
        block = blocks[0]
        self.assertEqual(block['rows'], [['merged', 'before | after'], ['bottom'], ['', '', '']])
        self.assertEqual(block['cellSpans'][0], [{'rowspan': 2, 'colspan': 2}, {'rowspan': 1, 'colspan': 1}])
        self.assertEqual(block['cellSpans'][1], [{'rowspan': 1, 'colspan': 1}])
        self.assertEqual(block['cellImages'][0], [[], ['p.png']])
        attached = attach_media(blocks, root, 'synthetic')
        self.assertEqual(attached[0]['cellImages'][0][1], [{'name': 'p.png', 'url': '/api/batches/synthetic/media/p.png'}])
        self.assertNotIn('p.png', attached[0]['rows'][0][1])

    def test_explicit_omitted_edge_columns_remain_empty_cells(self):
        body = ('<w:tbl><w:tblGrid><w:gridCol/><w:gridCol/><w:gridCol/></w:tblGrid>'
                '<w:tr><w:trPr><w:gridBefore w:val="1"/><w:gridAfter w:val="1"/></w:trPr>'
                + cell('middle') + '</w:tr></w:tbl>')
        _, extraction = self.native(body)
        block = source_blocks(extraction['text'].splitlines(), extraction)[0]
        self.assertEqual(block['rows'], [['', 'middle', '']])
        self.assertEqual(block['cellSpans'], [[{'rowspan': 1, 'colspan': 1}] * 3])

    def test_empty_single_column_rows_survive_native_and_legacy_paths(self):
        _, extraction = self.native('<w:tbl><w:tr>' + cell('') + '</w:tr><w:tr>' + cell('text') + '</w:tr><w:tr>' + cell('') + '</w:tr></w:tbl>')
        lines = extraction['text'].splitlines()
        for evidence in (None, extraction):
            with self.subTest(native=evidence is not None):
                self.assertEqual(source_blocks(lines, evidence)[0]['rows'], [[''], ['text'], ['']])

    def test_same_number_with_changed_text_never_borrows_native_cell_boundaries(self):
        _, extraction = self.native('<w:tbl><w:tr>' + cell('alpha | beta') + '</w:tr></w:tbl>')
        block = source_blocks(['[TABLE 1]', 'other | text', '[/TABLE]'], extraction)[0]
        self.assertEqual(block['rows'], [['other', 'text']])
        self.assertNotIn('cellSpans', block)
        self.assertEqual(extraction['tables'][0]['rows'][0]['cells'][0]['text'], 'alpha | beta')

    def test_layout_whitespace_only_does_not_prevent_full_text_binding(self):
        _, extraction = self.native('<w:tbl><w:tr>' + cell('alpha | beta') + '</w:tr></w:tbl>')
        block = source_blocks(['[TABLE 1]', ' alpha  | beta ', '[/TABLE]'], extraction)[0]
        self.assertEqual(block['rows'], [['alpha | beta']])
        self.assertIn('cellSpans', block)

    def test_nested_table_marker_does_not_truncate_outer_or_absorb_following_text(self):
        nested = '<w:tbl><w:tr>' + cell('inner | literal') + '</w:tr></w:tbl>'
        body = ('<w:tbl><w:tr><w:tc>' + paragraph('before') + nested
                + paragraph('after') + '</w:tc></w:tr></w:tbl>' + paragraph('following'))
        _, extraction = self.native(body)
        blocks = source_blocks(extraction['text'].splitlines(), extraction)
        self.assertEqual([b['type'] for b in blocks], ['table', 'text'])
        self.assertEqual(blocks[0]['rows'], [[extraction['tables'][0]['rows'][0]['cells'][0]['text']]])
        self.assertIn('inner | literal', blocks[0]['rows'][0][0])
        self.assertIn('after', blocks[0]['rows'][0][0])
        self.assertEqual(blocks[1]['text'], 'following')
        # No nested table UI is invented. Legacy text also keeps the outer
        # closing boundary and following paragraph separate.
        legacy = source_blocks(['[TABLE 1]', 'before', '[TABLE 2]', 'inner', '[/TABLE]',
                                'after', '[/TABLE]', 'following'])
        self.assertEqual([b['type'] for b in legacy], ['table', 'text'])
        self.assertIn(['after'], legacy[0]['rows'])
        self.assertEqual(legacy[1]['text'], 'following')

    def test_invalid_native_index_and_malformed_evidence_fall_back_safely(self):
        _, extraction = self.native('<w:tbl><w:tr>' + cell('alpha | beta') + '</w:tr></w:tbl>')
        tampered = deepcopy(extraction)
        tampered['source_objects'] = {'version': 'invalid'}
        for evidence in (tampered, [], None):
            with self.subTest(evidence=type(evidence).__name__):
                block = source_blocks(extraction['text'].splitlines(), evidence)[0]
                self.assertEqual(block['rows'], [['alpha', 'beta']])
                self.assertNotIn('cellSpans', block)


if __name__ == '__main__':
    unittest.main()
