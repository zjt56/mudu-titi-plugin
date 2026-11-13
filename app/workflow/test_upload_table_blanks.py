"""Final transport projection for source-proven table response blanks."""
import copy
import json
from unittest.mock import patch
from workflow import test_table_blank_structure as fixtures, upload_payload


class UploadTableBlanksTest(fixtures.TableBlankStructureTest):
    def payload(self, row=None, source=True):
        return upload_payload.build_payload({'items': [row or self.row]}, batch_id='fixture', name='fixture',
                                            batch_dir=self.root if source else None)

    def test_transport_only_projects_exact_source_tokens(self):
        before = copy.deepcopy(self.row)
        result = self.payload()
        children = result['payload']['items'][0]['subquestions']
        self.assertEqual([c['stem'] for c in children[5:]], [f'{n}.______' for n in range(6, 11)])
        self.assertFalse(any(e['code'] == 'MISSING_STEM' for e in result['errors']), result['errors'])
        changes = [w for w in result['warnings'] if w['code'] == 'TABLE_BLANK_STEM_PROJECTED']
        self.assertEqual(len(changes), 5)
        self.assertTrue(all(w['table_number'] == 2 and w['source_ref'].startswith('src-') for w in changes))
        self.assertEqual(self.row, before)
        self.assertEqual(result['payload']['items'][0]['materialBlocks'], self.row['materialBlocks'])
        self.assertEqual([c['answer'] for c in children], [c['answer'] for c in self.row['subquestions']])

    def test_no_proof_changed_table_or_partial_group_remains_blocked(self):
        variants = []
        for mutate in (lambda r: r.update(materialBlocks=[]),
                       lambda r: r['subquestions'].pop(),
                       lambda r: r['subquestions'][6].update(number=6),
                       lambda r: r['subquestions'][0].update(stem=''),
                       lambda r: r.update(section_id='other')):
            row = copy.deepcopy(self.row); mutate(row); variants.append(row)
        for row in variants:
            result = self.payload(row)
            self.assertTrue(any(e['code'] == 'MISSING_STEM' for e in result['errors']))
            self.assertFalse(any(w['code'] == 'TABLE_BLANK_STEM_PROJECTED' for w in result['warnings']))
        bare = self.payload(source=False)
        self.assertTrue(any(e['code'] == 'MISSING_STEM' for e in bare['errors']))

    def test_nonempty_stem_is_never_overwritten_and_unmatched_marker_declines(self):
        row = copy.deepcopy(self.row); row['subquestions'][5]['stem'] = 'Existing source stem'
        result = self.payload(row)
        self.assertEqual(result['payload']['items'][0]['subquestions'][5]['stem'], 'Existing source stem')
        proof = self.verified()
        uid = self.row['unit_id']
        proof[uid]['blanks'][6]['marker'] += '\n6.<u>　</u>'
        with patch('workflow.table_structure.verified_groups', return_value=proof):
            result = self.payload()
        self.assertTrue(any(e['code'] == 'MISSING_STEM' and e['unit_id'].endswith('/6') for e in result['errors']))
