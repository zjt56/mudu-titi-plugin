"""缺解析/大意可继续；已有内容必须来自原题，不能用 AI 标记绕过。"""
import json
from pathlib import Path
import tempfile
import unittest
from workflow import checks

MARK = '（AI生成，待校对）'
ORIGINAL = '本题考查动词辨析，结合上下文应选表示参加活动的动词。'


class ExplanationGapTest(unittest.TestCase):
    def run_check(self, explanation, *, qtype='单项填空', summary=None, appendix=None, children=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '01_source').mkdir()
            (root / '06_batch').mkdir()
            (root / '01_source/正文.txt').write_text('1. Choose the right verb.\n2. Choose the right noun.', encoding='utf-8')
            if appendix is not None:
                (root / '01_source/附录.txt').write_text(appendix, encoding='utf-8')
            item = {'unit_id': 'q-1', 'number': 1, 'question_type': qtype, 'answer': 'A', 'explanation': explanation}
            if summary is not None:
                item['articleSummary'] = summary
            if children is not None:
                item['unit_id'] = 'group-1-2'
                item['subquestions'] = children
            path = root / '06_batch/batch_meta.json'
            path.write_text(json.dumps({'items': [item]}, ensure_ascii=False), encoding='utf-8')
            before = path.read_bytes()
            spec = next(check for check in checks.CHECKS if 'check_explanations' in check.script)
            result = checks.run_check(spec, Path(__file__).resolve().parents[1], path, {})
            self.assertEqual(path.read_bytes(), before, '体检不得改写批次')
            return result

    def test_original_explanation_in_extracted_appendix_is_traceable(self):
        result = self.run_check(ORIGINAL, appendix=f'1.A 【解析】{ORIGINAL}')
        self.assertTrue(result['passed'], result)

    def test_missing_optional_explanation_and_summary_pass(self):
        for value in ('', None, '  ', '无'):
            with self.subTest(value=value):
                result = self.run_check(value, summary=value, appendix='1.A')
                self.assertTrue(result['passed'], result)

    def test_non_string_content_is_not_mistaken_for_absence(self):
        for value in ([], {}, False, 0):
            with self.subTest(value=value):
                self.assertFalse(self.run_check(value)['passed'])
                self.assertFalse(self.run_check('无', summary=value)['passed'])

    def test_group_children_may_have_missing_explanations(self):
        result = self.run_check('无', qtype='完形填空', appendix=f'1.A 【解析】{ORIGINAL}\n2.B',
            children=[{'number': 1, 'explanation': ORIGINAL}, {'number': 2, 'explanation': ''}])
        self.assertTrue(result['passed'], result)

    def test_group_explanation_cannot_hide_unverified_child(self):
        result = self.run_check('无', qtype='完形填空',
            children=[{'number': 1, 'explanation': '无'}, {'number': 2, 'explanation': '自行补写的解析。'}])
        self.assertFalse(result['passed'], result)
        self.assertIn('group-1-2/2', result['output'])

    def test_ai_marker_in_any_position_fails(self):
        for value in (MARK, MARK + ORIGINAL, ORIGINAL + MARK, '答案由AI生成' + MARK):
            with self.subTest(value=value):
                self.assertFalse(self.run_check(value)['passed'])

    def test_unmarked_self_written_explanation_fails(self):
        self.assertFalse(self.run_check('本题考查名词辨析，根据上下文可以判断出正确答案。')['passed'])

    def test_listening_missing_explanation_passes_and_generation_fails(self):
        self.assertTrue(self.run_check('无', qtype='听力短对话理解')['passed'])
        for value in ('答案由AI生成' + MARK, '细节题。男士说去图书馆，故选A。' + MARK):
            with self.subTest(value=value):
                self.assertFalse(self.run_check(value, qtype='听力短对话理解')['passed'])

    def test_generated_article_summary_fails_marked_or_unmarked(self):
        for value in ('本文讲述一个人靠坚持实现梦想的小故事。', '本文讲述坚持。' + MARK):
            with self.subTest(value=value):
                self.assertFalse(self.run_check('无', qtype='阅读理解', summary=value)['passed'])

    def test_original_summary_is_preserved_and_cannot_be_omitted(self):
        summary = '这是原附录的文章大意。'
        appendix = f'【文章大意】{summary}\n1.A 【解析】{ORIGINAL}'
        self.assertTrue(self.run_check(ORIGINAL, summary=summary, appendix=appendix)['passed'])
        self.assertFalse(self.run_check(ORIGINAL, summary='无', appendix=appendix)['passed'])

    def test_original_explanation_cannot_be_truncated_rewritten_or_marked_missing(self):
        for value in ('无', '', ORIGINAL[:12], ORIGINAL.replace('动词', '名词'), ORIGINAL.replace('，', ',')):
            with self.subTest(value=value):
                self.assertFalse(self.run_check(value, appendix=f'1.A 【解析】{ORIGINAL}')['passed'])

    def test_copied_other_question_explanation_fails(self):
        appendix = f'1.A 【解析】第一题原解析。\n2.B 【解析】{ORIGINAL}'
        self.assertFalse(self.run_check(ORIGINAL, appendix=appendix)['passed'])


if __name__ == '__main__':
    unittest.main()
