"""Source copying must retain the ordinary explanation ownership contract."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from workflow.content_fragments import expand_references, source_index
from workflow.content_rules import validate
from workflow.explanations_direct import DIRECT_COPY_EVIDENCE


class ContentFragmentsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        self.source = self.batch / '01_source/附录.txt'
        self.source.parent.mkdir()
        self.text = '【文章大意】原文大意。\n1.A 【解析】第一题解析。\n2.B 【解析】第二题解析。\n'
        self.source.write_text(self.text, encoding='utf-8')
        (self.batch / '01_source/正文.txt').write_text('1.题目\n2.题目\n', encoding='utf-8')
        self.units = [{'unit_id': 'q-1', 'unit_kind': 'standalone', 'numbers': [1]}]
        self.typed = {'q-1': {'type': '阅读理解'}}

    def ref(self, line=2, **changes):
        ref = {'path': '01_source/附录.txt', 'sha256': hashlib.sha256(self.source.read_bytes()).hexdigest(),
               'start_line': line, 'end_line': line, 'start_column': 8 if line > 1 else 6}
        ref.update(changes)
        return {'source_ref': ref}

    def payload(self, explanation=None, summary=None):
        return {'items': [{'unit_id': 'q-1', 'explanation': explanation if explanation is not None else self.ref(),
                           'articleSummary': summary if summary is not None else self.ref(1),
                           'subquestions': [], 'evidence': ['附录原文']}]}

    def check(self, payload):
        return validate(payload, self.units, self.typed, self.batch)['q-1']

    def test_references_expand_and_retain_hash_bound_evidence(self):
        row = self.check(self.payload())
        self.assertEqual(row['explanation'], '第一题解析。')
        self.assertEqual(row['articleSummary'], '原文大意。')
        self.assertTrue(any('source_ref 01_source/附录.txt:2:8-2:14 sha256=' in e for e in row['evidence']))
        self.assertNotIn('source_ref', json.dumps(row['explanation']))

    def test_legacy_strings_are_unchanged(self):
        row = self.check(self.payload('第一题解析。', '原文大意。'))
        self.assertEqual(row['evidence'], ['附录原文'])
        self.assertEqual(row['explanation'], '第一题解析。')

    def test_placeholder_legacy_normalization_is_unchanged(self):
        self.source.write_text('1.A\n', encoding='utf-8')
        row = self.check(self.payload('', '无解析'))
        self.assertEqual((row['explanation'], row['articleSummary']), ('无', '无'))

    def test_child_explanation_and_group_summary(self):
        units = [{'unit_id': 'g-1-2', 'unit_kind': 'group', 'numbers': [1, 2]}]
        payload = {'items': [{'unit_id': 'g-1-2', 'explanation': '无', 'articleSummary': self.ref(1),
                             'evidence': ['附录'], 'subquestions': [
                                 {'number': 1, 'explanation': self.ref()},
                                 {'number': 2, 'explanation': self.ref(3)}]}]}
        row = validate(payload, units, {'g-1-2': {'type': '阅读理解'}}, self.batch)['g-1-2']
        self.assertEqual([c['explanation'] for c in row['subquestions']], ['第一题解析。', '第二题解析。'])

    def test_stale_or_forged_hash_is_rejected(self):
        for digest in ('0' * 64, 'bad', 'A' * 64, None):
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                self.check(self.payload(self.ref(sha256=digest)))
        payload = self.payload()
        self.source.write_text(self.text + '\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'stale or forged'):
            self.check(payload)

    def test_invalid_ranges_are_rejected_without_partial_expansion(self):
        for changes in ({'start_line': 0}, {'end_line': 99}, {'start_line': 3, 'end_line': 2},
                        {'start_line': True}, {'end_line': 2.0}, {'start_line': '2'},
                        {'start_column': -1}, {'end_column': 100}, {'start_column': False},
                        {'start_column': 12, 'end_column': 9}, {'end_column': 6}):
            with self.subTest(changes=changes):
                payload = self.payload(summary=self.ref(1, **changes))
                before = copy.deepcopy(payload)
                with self.assertRaises(ValueError):
                    self.check(payload)
                self.assertEqual(payload, before)

    def test_reference_shape_is_strict(self):
        refs = [{'source_ref': {}}, {'source_ref': []}, {'text': '第一题解析。'},
                dict(self.ref(), text='forged'), self.ref(unknown=True)]
        for ref in refs:
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                self.check(self.payload(ref))

    def test_traversal_absolute_and_non_source_paths_are_rejected(self):
        for path in ('../outside.txt', '/tmp/source.txt', '01_source/../01_source/附录.txt',
                     '04_knowledge/explanations.json', '01_source/materials/attachment_x.txt'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.check(self.payload(self.ref(path=path)))

    def test_symlink_file_and_parent_are_rejected(self):
        materials = self.source.parent / 'materials'
        materials.mkdir()
        linked = materials / 'attachment_001.txt'
        linked.symlink_to(self.source)
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            self.check(self.payload(self.ref(path='01_source/materials/attachment_001.txt')))
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            source_index(self.batch)
        other = self.batch / '01_source_ans'
        other.symlink_to(self.source.parent, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            self.check(self.payload(self.ref(path='01_source_ans/正文.txt')))

    def test_wrong_question_still_fails_ownership(self):
        with self.assertRaisesRegex(ValueError, 'EXPLANATION_SOURCE_UNVERIFIED'):
            self.check(self.payload(self.ref(3)))

    def test_authoritative_conflict_still_fails_uniqueness(self):
        self.source.write_text(self.text + '1.B 【解析】不同解析。\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'EXPLANATION_SOURCE_UNVERIFIED'):
            self.check(self.payload())

    def test_direct_copy_evidence_cannot_bypass_reference_ownership(self):
        payload = self.payload(self.ref(3))
        payload['items'][0]['evidence'].append(DIRECT_COPY_EVIDENCE)
        before = copy.deepcopy(payload)
        with self.assertRaisesRegex(ValueError, 'direct-copy'):
            self.check(payload)
        self.assertEqual(payload, before)

    def test_generated_source_text_still_fails(self):
        self.source.write_text(self.text.replace('第一题解析。', '第一题解析。（AI生成，待校对）'), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'AI-generated'):
            self.check(self.payload())

    def test_attachment_sources_and_reference_only_exclusion(self):
        materials = self.source.parent / 'materials'
        materials.mkdir()
        attachment = materials / 'attachment_001.txt'
        attachment.write_text(self.text, encoding='utf-8')
        relative = '01_source/materials/attachment_001.txt'
        row = self.check(self.payload(self.ref(path=relative)))
        self.assertEqual(row['explanation'], '第一题解析。')
        (self.source.parent / 'material-roles.json').write_text(json.dumps({'documents': [
            {'text_path': relative, 'content_authority': False}]}), encoding='utf-8')
        self.assertNotIn(relative, [s['path'] for s in source_index(self.batch)['sources']])
        with self.assertRaisesRegex(ValueError, 'reference-only'):
            self.check(self.payload(self.ref(path=relative)))

    def test_source_index_has_hashes_and_numbered_raw_text(self):
        index = source_index(self.batch)
        self.assertEqual(index['format'], 'source-fragment/v1')
        source = next(s for s in index['sources'] if s['path'] == '01_source/附录.txt')
        self.assertEqual(source['sha256'], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(source['lines'][1], {'number': 2, 'text': '1.A 【解析】第一题解析。'})

    def test_multiline_columns_preserve_whitespace_crlf_and_bom(self):
        self.source.write_bytes(b'\xef\xbb\xbf' + 'head first\r\n  middle  \r\nlast tail\r\n'.encode())
        ref = self.ref(1, end_line=3, start_column=5, end_column=4)
        rows = [{'explanation': ref, 'evidence': ['source']}]
        self.assertTrue(expand_references(rows, self.batch))
        self.assertEqual(rows[0]['explanation'], 'first\r\n  middle  \r\nlast')

    def test_missing_evidence_and_bad_children_still_fail(self):
        for evidence in (None, [], 1, [''], [42]):
            payload = self.payload()
            if evidence is None:
                payload['items'][0].pop('evidence')
            else:
                payload['items'][0]['evidence'] = evidence
            with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                self.check(payload)
        payload = self.payload()
        payload['items'][0]['subquestions'] = [{'number': 2, 'explanation': '无'}]
        with self.assertRaisesRegex(ValueError, 'exact confirmed child'):
            self.check(payload)


if __name__ == '__main__':
    unittest.main()
