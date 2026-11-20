"""Scoped answer binding survives undeclared printed numbers in a section span.

Batch 06 …StarterUnit2 第二课时: Ⅱ. 根据图片及所给音标写出下列单词所缺的字母 prints
five two-column items, but the classified structure declared only 1/3/5.  The
structure gate reports the loss as SOURCE_NUMBERS_UNDECLARED (non-blocking), so
the declared questions must still project to their real source coordinates while
the undeclared ones stay reported — never guessed, never silently merged.
"""

import unittest
from pathlib import Path

BATCH = Path(
    '/Users/zjt/Desktop/上传插件版/workflows/'
    '06七上人教版课时A计划StarterUnit2KeepTidy!第二课时Pro'
)

BODY = [
    '七上 · 人教版 · 课时A计划',
    '**Starter Unit 2　Keep Tidy!**',
    '**第二课时**　Pronunciation',
    '',
    '**【A 基础巩固】**',
    '**Ⅰ. 按照字母表顺序分别写出五个元音字母的大小写及音标**',
    '**1.**答案一',
    '---------',
    '**2.**答案二',
    '**Ⅱ. 根据图片及所给音标写出下列单词所缺的字母**',
    '**1.**[IMG a.jpeg]',
    'c______/eɪ/ke\t2.[IMG b.jpeg]',
    'n______/əʊ/se',
    '**3.**[IMG c.jpeg]',
    'b______/aɪ/ke\t4.[IMG d.jpeg]',
    'm______/iː/t',
    '**5.**[IMG e.jpeg]',
    'comp______/juː/ter',
]

# 清洗坐标（去掉第8行分隔线）与时序一致的声明题号：分栏内联的2、4未声明。
UNIT = {
    'identity_version': 'scoped-v1',
    'unit_kind': 'group',
    'numbers': [1, 3, 5],
    'source_span': {'start_line': 10, 'end_line': 17, 'basis': 'cleaned_source'},
    'source_section': {
        'title': '**Ⅱ. 根据图片及所给音标写出下列单词所缺的字母**',
        'ordinal': 'Ⅱ.',
        'context': '第二课时　Pronunciation',
        'occurrence': 2,
        'start_line': 9,
        'end_line': 17,
    },
}


def _mods():
    try:
        from workflow import upload_content
    except ImportError:  # pragma: no cover - repo-root import fallback
        from app.workflow import upload_content
    return upload_content


class ScopedSpanProjectionTest(unittest.TestCase):
    def test_declared_numbers_project_past_undeclared_inline_items(self):
        uc = _mods()
        _, clean_to_raw = uc._body_line_maps(tuple(BODY))
        projected = uc._project_unit_to_body_file(UNIT, clean_to_raw, BODY)
        self.assertEqual(projected['source_section']['start_line'], 10)
        self.assertEqual(projected['source_span']['start_line'], 11,
                         'declared items must project to their real source lines')
        self.assertEqual(projected['source_span']['end_line'], 18)
        self.assertTrue(uc.scoped_source_matches(
            projected, 1, '01_source/正文.txt', 11, {'01_source/正文.txt': BODY},
            _file_coordinates=True))

    def test_undeclared_numbers_are_not_invented(self):
        uc = _mods()
        projected = uc._project_unit_to_body_file(
            UNIT, uc._body_line_maps(tuple(BODY))[1], BODY)
        self.assertEqual(projected['numbers'], [1, 3, 5])


@unittest.skipUnless(BATCH.is_dir(), 'batch workspace not present')
class StarterUnit2AnswerBindingTest(unittest.TestCase):
    """The real batch: Ⅱ.1 must bind to 附录 第7行 ``1.a`` (not a global number)."""

    def test_all_declared_answers_resolve_with_real_coordinates(self):
        import json

        uc = _mods()
        units = json.loads((BATCH / '01_source/structure.confirmed.json').read_text(
            encoding='utf-8-sig'))['units']
        expected = {
            'sec-9b9321a09c58fa2b-u42': {1: ('a', '01_source/附录.txt:7'),
                                         3: ('i', '01_source/附录.txt:7'),
                                         5: ('u', '01_source/附录.txt:7')},
            'sec-9724cd71995a6675-u7': {1: ('Aa;/eɪ/', '01_source/附录.txt:5')},
            'sec-ea75f66fcca1083a-u62': {3: ('fine,nine,kite,ride,bike',
                                             '01_source/附录.txt:11')},
            'sec-a908ce23bc443377-u72': {1: ('C', '01_source/附录.txt:15')},
            'sec-44f9fe5ca780c10f-u91': {2: ('A', '01_source/附录.txt:18')},
        }
        for unit in units:
            for number, (answer, evidence) in expected.get(unit['unit_id'], {}).items():
                with self.subTest(unit=unit['unit_id'], number=number):
                    found = uc.resolve_scoped_answer(str(BATCH), unit, number)
                    self.assertIsNotNone(found)
                    self.assertEqual(found['answer'], answer)
                    self.assertIn(evidence, found['evidence'])
                    uc.validate_scoped_answer(
                        str(BATCH), unit, number, found['answer'], found['evidence'])
