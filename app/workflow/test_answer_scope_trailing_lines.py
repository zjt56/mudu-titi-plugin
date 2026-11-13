"""A heading window may absorb trailing bare lines past the confirmed span.

The heading re-derivation ends a section only at the next heading, so a section
whose persisted end stops earlier (e.g. before a lone [IMG ...] after a cloze
passage) still shares the same start line and full signature. Ownership must be
proved by that unique signature while the confirmed span stays inside the one
derived window. The compact-candidate lookup (which reads the body section the
unit already belongs to) must apply the same tolerance so the answer is not
silently dropped, and a second matching heading or an out-of-window end must
still fail.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import upload_content


BODY = (
    'Unit 5\n第二课时 Section A\n'
    'Ⅰ.完形填空\n'
    'A short passage with a blank <u>　1　</u>.\n'
    '[IMG trailing.jpeg]\n'
    'Ⅱ.阅读理解\n1.Read and answer.\n'
)
APPENDIX = '参考答案\nⅠ.1.word\nⅡ.1.answer\n'


class TrailingBareLineScopeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        (self.batch / '01_source').mkdir()
        (self.batch / '01_source/正文.txt').write_text(BODY, encoding='utf-8')
        (self.batch / '01_source/附录.txt').write_text(APPENDIX, encoding='utf-8')
        rows = BODY.splitlines()
        self.cloze = rows.index('Ⅰ.完形填空') + 1
        reading = rows.index('Ⅱ.阅读理解') + 1
        self.units = [
            {'unit_kind': 'group', 'type': '完形填空', 'numbers': [1],
             'source_span': {'start_line': self.cloze + 1, 'end_line': self.cloze + 2,
                             'basis': 'cleaned_source'},
             # Persisted section end stops at the passage line, before the trailing image.
             'source_section': {'title': 'Ⅰ.完形填空', 'ordinal': 'Ⅰ.',
                                'context': '第二课时 Section A', 'occurrence': 1,
                                'start_line': self.cloze, 'end_line': self.cloze + 1},
             'identity_version': 'scoped-v1', 'section_id': 'sec-cloze',
             'unit_id': 'sec-cloze-u4', 'question_ids': {'1': 'sec-cloze-u4-q1'}},
            {'unit_kind': 'group', 'type': '阅读理解', 'numbers': [1],
             'source_span': {'start_line': reading + 1, 'end_line': reading + 1,
                             'basis': 'cleaned_source'},
             'source_section': {'title': 'Ⅱ.阅读理解', 'ordinal': 'Ⅱ.',
                                'context': '第二课时 Section A', 'occurrence': 2,
                                'start_line': reading, 'end_line': reading + 1},
             'identity_version': 'scoped-v1', 'section_id': 'sec-reading',
             'unit_id': 'sec-reading-u7', 'question_ids': {'1': 'sec-reading-u7-q1'}},
        ]

    def lines(self):
        return {path: (self.batch / path).read_text(encoding='utf-8').splitlines()
                for path in ('01_source/正文.txt', '01_source/附录.txt')}

    def test_trailing_image_beyond_persisted_end_still_binds_section(self):
        unit = self.units[0]
        self.assertTrue(upload_content.scoped_source_matches(unit, 1, '01_source/正文.txt', 4, self.lines()))
        # The compact lookup that resolves the answer must find the same section.
        sections = [{'start_line': unit['source_section']['start_line'],
                     'end_line': unit['source_section']['end_line'] + 1,
                     'title': 'Ⅰ.完形填空', 'context': '第二课时 Section A', 'occurrence': 1}]
        self.assertEqual(upload_content._compact_scoped_candidates(unit, 1, self.lines(), sections=sections), [])

    def test_body_span_still_resolves_answer_within_window(self):
        unit = self.units[0]
        self.assertTrue(upload_content.scoped_source_matches(unit, 1, '01_source/正文.txt', 4, self.lines()))

    def test_end_outside_derived_window_rejects(self):
        drift = copy.deepcopy(self.units[0])
        drift['source_section']['end_line'] = 999
        self.assertFalse(upload_content.scoped_source_matches(
            drift, 1, '01_source/正文.txt', 4, self.lines()))

    def test_heading_window_not_granting_end_tolerance_rejects(self):
        # Without the persisted-end tolerance, a span whose end exceeds the
        # derived window must not bind. This keeps the tolerance narrow.
        body = BODY.splitlines()
        unit = copy.deepcopy(self.units[0])
        unit['source_span']['end_line'] = 6  # reaches into the next section heading window
        self.assertFalse(upload_content.scoped_source_matches(
            unit, 1, '01_source/正文.txt', 4, self.lines()))


if __name__ == '__main__':
    unittest.main()
