"""D27 exact source marks: only bounded scoped identities support ownership checks."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import tempfile
import json
import unittest

from . import sync_practice as sp
from .question_identity import assign_scoped


class SourceMarksTest(unittest.TestCase):
    def scoped(self, text, *rows):
        for row in rows:
            if row.get('source_span'):
                row['source_span'].setdefault('basis', 'ai_classified')
        return assign_scoped(list(rows), text)

    def test_no_year_marks_and_non_mark_labels(self):
        marks = ['[传统文化]', '[安徽人文]', '[榜样人物]', '[经典谚语]', '[熟词生义]', '[情境题]']
        self.assertEqual(sp.source_marks(' '.join(marks) + ' [IMG 2025.png] [TABLE 2025] '
                                        '【答案·2025】 【解析2025】 [参考答案2025]'), marks)

    def test_same_year_different_mark_not_kept_in_legacy(self):
        source = '2026期末测试卷\n1.[2025·六安月考]Hi?\n2.[2025·安徽中考]Bye?'
        rows = [{'stem': '[2025·安徽中考]Bye?'}]
        self.assertIn('[2025·六安月考]', sp.audit_batch_source_marks(rows, source)[0]['marks'])
        self.assertEqual(sp.audit_batch_source_marks([{'stem': source}], source), [])

    def test_wrong_question_position_in_same_group(self):
        text = '第一课时 Section A\nⅠ.阅读理解\nA story\n1.[传统文化]Who?\n2.Where?'
        rows = self.scoped(text, {'unit_kind': 'group', 'type': '阅读理解', 'numbers': [1, 2],
                                 'source_span': {'start_line': 2, 'end_line': 5}, 'stem': '阅读理解',
                                 'material': 'A story', 'subquestions': [
                                     {'number': 1, 'stem': 'Who?'}, {'number': 2, 'stem': '[传统文化]Where?'}]})
        before = deepcopy(rows)
        self.assertTrue(sp.audit_batch_source_marks(rows, text))
        self.assertEqual(rows, before)
        rows[0]['subquestions'][0]['stem'] = '[传统文化]Who?'
        rows[0]['subquestions'][1]['stem'] = 'Where?'
        self.assertEqual(sp.audit_batch_source_marks(rows, text), [])

    def test_heading_mark_requires_delivered_instruction(self):
        text = '第一课时 Section A\nⅠ.阅读理解[2025·六安月考]\nA story\n1.Who?'
        rows = self.scoped(text, {'unit_kind': 'group', 'type': '阅读理解', 'numbers': [1],
                                 'source_span': {'start_line': 3, 'end_line': 4}, 'stem': '阅读理解',
                                 'material': 'A story [2025·六安月考]',
                                 'subquestions': [{'number': 1, 'stem': 'Who?'}]})
        self.assertTrue(sp.audit_batch_source_marks(rows, text))
        rows[0]['material'] = 'A story'
        rows[0]['stem'] = '[2025·六安月考]阅读理解'
        self.assertEqual(sp.audit_batch_source_marks(rows, text), [])

    def test_metadata_and_explanation_never_count_as_delivery(self):
        text = '第一课时 Section A\nⅠ.单项填空\n1.[熟词生义]Hi?'
        rows = self.scoped(text, {'unit_kind': 'standalone', 'numbers': [1],
                                 'source_span': {'start_line': 3, 'end_line': 3}, 'stem': 'Hi?',
                                 'explanation': '[熟词生义]'})
        rows[0]['source_section']['extra'] = '[熟词生义]'
        self.assertTrue(sp.audit_batch_source_marks(rows, text))
        # Legacy unbound metadata also cannot satisfy a complete mark check.
        self.assertTrue(sp.audit_batch_source_marks([{'source_section': {'title': '[熟词生义]'}}], text))

    def test_mark_in_cloze_material_is_legal(self):
        text = '第一课时 Section A\nⅠ.完形填空\nA [榜样人物] story with 1.____.\n1.A.x B.y C.z D.w'
        rows = self.scoped(text, {'unit_kind': 'group', 'type': '完形填空', 'numbers': [1],
                                 'source_span': {'start_line': 2, 'end_line': 4}, 'stem': '完形填空',
                                 'material': 'A [榜样人物] story with 1.____.',
                                 'subquestions': [{'number': 1, 'stem': '1.'}]})
        self.assertEqual(sp.audit_batch_source_marks(rows, text), [])

    def test_final_items_without_span_resolve_within_section(self):
        text = '第一课时 Section A\nⅠ.单项填空\n1.[2025·六安月考]Hi?\n2.[2025·安徽中考]Bye?'
        rows = self.scoped(text,
                           {'unit_kind': 'standalone', 'numbers': [1], 'stem': '[2025·安徽中考]Hi?',
                            'source_span': {'start_line': 3, 'end_line': 3}},
                           {'unit_kind': 'standalone', 'numbers': [2], 'stem': '[2025·六安月考]Bye?',
                            'source_span': {'start_line': 4, 'end_line': 4}})
        for row in rows:
            row['number'] = row.pop('numbers')[0]
            row.pop('source_span')
        self.assertEqual(len(sp.audit_batch_source_marks(rows, text)), 2)
        rows[0]['stem'], rows[1]['stem'] = '[2025·六安月考]Hi?', '[2025·安徽中考]Bye?'
        self.assertEqual(sp.audit_batch_source_marks(rows, text), [])

    def test_unscoped_and_exam_remain_conservative(self):
        text = '第一课时 Section A\nⅠ.阅读理解\n1.[传统文化]Who?\n2.Where?'
        rows = [{'stem': 'Where? [传统文化]'}]
        self.assertEqual(sp.audit_batch_source_marks(rows, text), [])
        self.assertEqual(sp.audit_source_marks(rows, '2026期末测试卷\n1.[传统文化]Who?'), [])
        self.assertEqual(sp.audit_batch_source_marks(rows, '2026期末测试卷\n1.[传统文化]Who?'), [])

    def test_formal_child_options_and_structure_children(self):
        for key in ('children', 'subquestions'):
            text = '第一课时 Section A\nⅠ.阅读理解\n1.What?\nA.[熟词生义]word'
            rows = self.scoped(text, {'unit_kind': 'group', 'numbers': [1], 'type': '阅读理解',
                                     'source_span': {'start_line': 2, 'end_line': 4},
                                     key: [{'number': 1, 'stem': 'What?', 'options': ['A.[熟词生义]word']}]})
            self.assertEqual(sp.audit_batch_source_marks(rows, text), [])

    def test_material_mark_cannot_hide_in_child(self):
        text = '第一课时 Section A\nⅠ.阅读理解\nA [传统文化] story\n1.What?'
        rows = self.scoped(text, {'unit_kind': 'group', 'numbers': [1], 'type': '阅读理解',
                                 'source_span': {'start_line': 2, 'end_line': 4}, 'material': 'A story',
                                 'subquestions': [{'number': 1, 'stem': '[传统文化]What?'}]})
        self.assertTrue(sp.audit_batch_source_marks(rows, text))

    def test_table_delivered_text_counts_but_table_label_is_not_a_mark(self):
        source = '2026期末测试卷\n[传统文化] [TABLE 2025]'
        rows = [{'materialBlocks': [{'type': 'table', 'rows': [[{'text': '[传统文化]'}]]}]}]
        self.assertEqual(sp.audit_batch_source_marks(rows, source), [])

    def test_ambiguous_repeated_numbers_do_not_guess_ownership(self):
        text = '第一课时 Section A\nⅠ.阅读理解\nA\n1.[传统文化]Who?\nB\n1.Where?'
        rows = self.scoped(text, {'unit_kind': 'group', 'numbers': [1],
                                 'source_span': {'start_line': 3, 'end_line': 4},
                                 'subquestions': [{'number': 1, 'stem': '[传统文化]Who?'}]})
        rows[0].pop('source_span')
        self.assertEqual(sp.audit_batch_source_marks(rows, text), [])
        rows[0]['subquestions'][0]['stem'] = 'Who?'
        self.assertTrue(sp.audit_batch_source_marks(rows, text))

    def test_inline_two_children_use_their_own_marks(self):
        text = '第一课时 Section A\nⅠ.阅读理解\n1.[熟词生义]What?　2.Where?'
        rows = self.scoped(text, {'unit_kind': 'group', 'numbers': [1, 2],
                                 'source_span': {'start_line': 2, 'end_line': 3},
                                 'subquestions': [{'number': 1, 'stem': 'What?'},
                                                  {'number': 2, 'stem': '[熟词生义]Where?'}]})
        self.assertTrue(sp.audit_batch_source_marks(rows, text))

    def test_stale_section_or_untrusted_span_only_use_conservative_check(self):
        text = '第一课时 Section A\nⅠ.阅读理解\n1.[传统文化]Who?\n2.Where?'
        original = self.scoped(text, {'unit_kind': 'group', 'numbers': [1, 2],
                                     'source_span': {'start_line': 2, 'end_line': 4},
                                     'subquestions': [{'number': 1, 'stem': 'Who?'},
                                                      {'number': 2, 'stem': '[传统文化]Where?'}]})
        self.assertTrue(sp.audit_batch_source_marks(original, text))
        for field, value in [('end_line', 99), ('context', '第二课时'), ('occurrence', 99)]:
            rows = deepcopy(original)
            rows[0]['source_section'][field] = value
            self.assertEqual(sp.audit_batch_source_marks(rows, text), [])
        rows = deepcopy(original)
        rows[0]['source_span']['basis'] = 'guessed'
        self.assertEqual(sp.audit_batch_source_marks(rows, text), [])
        rows[0]['subquestions'][1]['stem'] = 'Where?'
        self.assertTrue(sp.audit_batch_source_marks(rows, text))

    def test_cloze_child_option_marks_belong_to_child(self):
        text = ('第一课时 Section A\nⅠ.完形填空\nA story with 1.____.\n'
                '1.A.[熟词生义]word B.other C.more D.last\n2.A.x B.y C.z D.w')
        rows = self.scoped(text, {'unit_kind': 'group', 'type': '完形填空', 'numbers': [1, 2],
                                 'source_span': {'start_line': 2, 'end_line': 5},
                                 'material': 'A story with 1.____.',
                                 'subquestions': [{'number': 1, 'stem': '1.', 'options': ['A.[熟词生义]word']},
                                                  {'number': 2, 'stem': '2.', 'options': ['A.x']}]})
        self.assertEqual(sp.audit_batch_source_marks(rows, text), [])
        rows[0]['subquestions'][0]['options'] = ['A.word']
        rows[0]['material'] += '[熟词生义]'
        self.assertTrue(sp.audit_batch_source_marks(rows, text))

    def test_script_uses_same_checker_without_source_writes(self):
        path = Path(__file__).resolve().parents[1] / 'scripts/06-upload/audit_source_marks.py'
        spec = importlib.util.spec_from_file_location('test_source_mark_script', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            batch, source = root / 'batch.json', root / 'source.txt'
            batch.write_text(json.dumps({'items': [{'stem': '[2025·安徽]x'}]}), encoding='utf-8')
            source.write_text('1.[2025·六安月考]Hi?', encoding='utf-8')
            before = source.read_bytes(), batch.read_bytes()
            self.assertEqual(module.main(['--batch', str(batch), '--source', str(source)]), 1)
            self.assertEqual((source.read_bytes(), batch.read_bytes()), before)


if __name__ == '__main__':
    unittest.main()
