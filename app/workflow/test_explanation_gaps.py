"""解析/文章大意按 F1/F2/F3/F5/F6/F7 校验：可溯源或已标注 AI，缺解析拦停（非听力）。"""
import json
from pathlib import Path
import tempfile
import unittest
from workflow import checks

MARK = '（AI生成，待校对）'


class ExplanationGapTest(unittest.TestCase):
    def run_check(self, explanation, *, qtype='单项填空', summary=''):
        with tempfile.TemporaryDirectory() as tmp:
            item = {'unit_id': 'q-1', 'question_type': qtype,
                    'answer': 'A', 'explanation': explanation}
            if summary:
                item['articleSummary'] = summary
            path = Path(tmp) / 'batch.json'
            path.write_text(json.dumps({'items': [item]}, ensure_ascii=False), encoding='utf-8')
            spec = next(c for c in checks.CHECKS if 'check_explanations' in c.script)
            return checks.run_check(spec, Path(__file__).resolve().parents[1], path, {})

    def test_original_explanation_in_extracted_appendix_is_traceable(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            (batch / '06_batch').mkdir()
            (batch / '01_source').mkdir()
            explanation = '本题考查动词辨析，结合上下文应选表示参加活动的动词。'
            (batch / '01_source/正文.txt').write_text('1. Choose the right verb.')
            (batch / '01_source/附录.txt').write_text(explanation)
            path = batch / '06_batch/batch_meta.json'
            path.write_text(json.dumps({'items': [{'unit_id': 'q-1', 'question_type': '单项填空',
                'answer': 'A', 'explanation': explanation}]}, ensure_ascii=False))
            spec = next(c for c in checks.CHECKS if 'check_explanations' in c.script)
            result = checks.run_check(spec, Path(__file__).resolve().parents[1], path,
                {'source': {'path': str(batch / '01_source/正文.txt'), 'exists': True}})
            self.assertTrue(result['passed'], result)

    def test_missing_explanation_on_non_listening_fails_f7(self):
        # 非听力单元缺解析 → 拦停（F7 要求自补并标注）。
        self.assertFalse(self.run_check('')['passed'])

    def test_group_explanation_does_not_hide_empty_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'batch.json'
            path.write_text(json.dumps({'items': [{'unit_id': 'group', 'question_type': '完形填空',
                'explanation': '整组概述。' + MARK, 'subquestions': [
                    {'number': 1, 'explanation': '根据语境应选A。' + MARK},
                    {'number': 2, 'explanation': ''}]}]}, ensure_ascii=False))
            spec = next(c for c in checks.CHECKS if 'check_explanations' in c.script)
            result = checks.run_check(spec, Path(__file__).resolve().parents[1], path, {})
            self.assertFalse(result['passed'])
            self.assertIn('子题2', result['output'])

    def test_ai_mark_only_or_nonterminal_mark_fails(self):
        self.assertFalse(self.run_check(MARK)['passed'])
        self.assertFalse(self.run_check(MARK + '本题考查词汇辨析。')['passed'])

    def test_marked_ai_explanation_passes(self):
        result = self.run_check('本题考查名词辨析，根据上下文可判断。' + MARK)
        self.assertTrue(result['passed'])

    def test_unmarked_self_written_explanation_fails(self):
        # 无来源、无标记的自撰解析拦停（F2/F5）。
        result = self.run_check('本题考查名词辨析，根据上下文可以判断出正确答案。')
        self.assertFalse(result['passed'])

    def test_listening_unit_with_single_answer_marker_passes(self):
        self.assertTrue(self.run_check('答案由AI生成' + MARK, qtype='听力短对话理解')['passed'])

    def test_listening_content_explanation_fails_f6(self):
        self.assertFalse(self.run_check('细节题。男士说去图书馆，故选A。' + MARK, qtype='听力短对话理解')['passed'])

    def test_marked_article_summary_passes(self):
        self.assertTrue(self.run_check('解析内容按要求补齐并标注。' + MARK,
                                       qtype='阅读理解',
                                       summary='本文讲述一个人靠坚持实现梦想的小故事。' + MARK)['passed'])

    def test_unmarked_article_summary_fails(self):
        result = self.run_check('解析内容按要求补齐并标注。' + MARK,
                                qtype='阅读理解',
                                summary='本文讲述一个人靠坚持实现梦想的小故事。')
        self.assertFalse(result['passed'])
