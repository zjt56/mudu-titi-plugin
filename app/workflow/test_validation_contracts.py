"""Source-backed underline requirements and child-owned group answer contracts."""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from workflow import checks
from workflow.test_source_completion_scripts import docx, module

underline = module('check_underline')
explanations = module('check_explanations')


class ValidationContractsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.batch = self.root / 'batch.json'
        self.source = self.root / 'source.docx'

    def run_script(self, script, items, source=False):
        self.batch.write_text(json.dumps({'items': items}, ensure_ascii=False))
        before = self.batch.read_bytes()
        argv = ['--batch', str(self.batch)]
        if source:
            argv += ['--source', str(self.source)]
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            code = script.main(argv)
        self.assertEqual(self.batch.read_bytes(), before)
        return code, out.getvalue()

    def test_ordinary_quotation_is_not_an_underline_requirement(self):
        passage = 'The programme “causes” sends books to Africa.'
        docx(self.source, [passage])
        code, output = self.run_script(underline, [{'id': 'reading', 'material': passage,
            'stem': 'Read the text.', 'question_type': '阅读理解'}], source=True)
        self.assertEqual(code, 0, output)
        self.assertNotIn('未画线', output)

    def test_explicit_docx_marks_required_even_without_underlined_question(self):
        docx(self.source, ['The marked <u>soundly</u> word matters.'])
        row = {'id': 'reading', 'question_type': '阅读理解',
               'material': 'The marked soundly word matters.', 'stem': 'Read the text.'}
        code, output = self.run_script(underline, [row], source=True)
        self.assertEqual(code, 1)
        self.assertIn('原文画线未保留: soundly', output)
        row['material'] = 'The marked <u>soundly</u> word matters.'
        code, output = self.run_script(underline, [row], source=True)
        self.assertEqual(code, 0, output)

    def test_explicit_question_references_require_material_mark_not_question_mark(self):
        for stem, word in [('What does the underlined word “soundly” probably mean?', 'soundly'),
                           ('What does “it” underlined in Paragraph 1 refer to?', 'it')]:
            row = {'id': 'reading', 'question_type': '阅读理解', 'material': f'The word {word} occurs here.',
                   'subquestions': [{'number': 63, 'stem': stem}]}
            code, output = self.run_script(underline, [row])
            self.assertEqual(code, 1, output)
            row['material'] = f'The word <u>{word}</u> occurs here.'
            code, output = self.run_script(underline, [row])
            self.assertEqual(code, 0, output)

    def test_source_mark_missing_from_entire_candidate_is_not_excused(self):
        docx(self.source, ['The original <u>important</u> word matters.'])
        code, output = self.run_script(underline, [{'id': 'reading', 'material': 'Changed article.'}], source=True)
        self.assertEqual(code, 1)
        self.assertIn('UNDERLINE_MAPPING_MISSING', output)

    def test_writing_placeholders_and_unmarked_blanks_still_fail(self):
        for row in ({'id': 'essay', 'question_type': '书面表达', 'stem': 'Write here ______'},
                    {'id': 'cloze', 'question_type': '完形填空', 'material': 'Fill　31　here.'}):
            code, output = self.run_script(underline, [row])
            self.assertEqual(code, 1, output)

    def test_underline_check_receives_source_without_becoming_skippable(self):
        check = next(c for c in checks.CHECKS if c.script.endswith('/check_underline.py'))
        self.assertEqual(check.needs, ())
        argv = checks._argv(check, Path('.'), self.batch, {'source': {'path': str(self.source)}})
        self.assertEqual(argv[-2:], ['--source', str(self.source)])

    def test_group_with_marked_explanations_passes_but_empty_group_is_f7_violation(self):
        # 新口径（F7）：非听力单元至少要有一条解析（组级或子题），缺失即拦停；
        # 组级可留空但子题各自带已标注解析则放行。答案是否为空由 check_answers 负责，这里不管。
        row = {'id': 'group', 'unit_kind': 'group', 'answer': '', 'explanation': '',
               'subquestions': [{'number': 1, 'answer': 'A', 'explanation': '本题考查时态（AI生成，待校对）'},
                                {'number': 2, 'answer': 'B', 'explanation': '本题考查搭配（AI生成，待校对）'}]}
        code, output = self.run_script(explanations, [row])
        self.assertEqual(code, 0, output)
        # 组级与全部子题都没有解析 → F7 拦停。
        empty = {'id': 'group', 'unit_kind': 'group', 'answer': 'A', 'explanation': '',
                 'subquestions': [{'number': 1, 'answer': 'A', 'explanation': ''}]}
        code, output = self.run_script(explanations, [empty])
        self.assertEqual(code, 1, output)
        self.assertIn('F7', output)

    def test_standalone_and_group_units_needing_explanation_fail(self):
        # 非听力单元没有解析一律拦停（F7）；带完整 AI 标注则放行。
        for row in ({'id': 'single', 'unit_kind': 'standalone', 'answer': 'A', 'explanation': ''},
                    {'id': 'group', 'unit_kind': 'group', 'answer': 'A', 'subquestions': []}):
            code, output = self.run_script(explanations, [row])
            self.assertEqual(code, 1, output)
        # 只写「AI生成」而缺完整标注，仍未通过（F5 要求整串「（AI生成，待校对）」）。
        code, output = self.run_script(explanations, [{'id': 'single', 'unit_kind': 'standalone',
            'answer': 'A', 'explanation': '由AI生成，本题根据上下文判断。'}])
        self.assertEqual(code, 1, output)
        # 完整标注放行。
        code, output = self.run_script(explanations, [{'id': 'single', 'unit_kind': 'standalone',
            'answer': 'A', 'explanation': '本题根据上下文判断（AI生成，待校对）'}])
        self.assertEqual(code, 0, output)


if __name__ == '__main__':
    unittest.main()
