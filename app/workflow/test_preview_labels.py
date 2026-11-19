"""原卷预览旁的快速核对标签：来源、知识点、难度、词数。"""
import hashlib
import tempfile
import unittest
from pathlib import Path
from workbench.server import answer_point_tags, preview_labels, save_json


class PreviewLabelsTest(unittest.TestCase):
    def test_answer_points_require_current_batch_inputs_and_unit_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            batch = Path(directory)
            files = ("06_batch/batch_meta.json", "01_source_ans/answers.json")
            for relative in files:
                save_json(batch / relative, {})
            save_json(batch / "04_knowledge/answer-point-tags.pending.json", {
                "batch": batch.name, "status": "pending_catalog_binding",
                "source_sha256": {
                    name: hashlib.sha256((batch / name).read_bytes()).hexdigest()
                    for name in files
                },
                "labels": [
                    {"unit_id": "q-1", "question_type": "单词拼写", "name": "people",
                     "proposed_path": "词汇/答案考点/people"},
                    {"unit_id": "q-2", "question_type": "完成句子", "name": "take care of",
                     "proposed_path": "词汇/答案考点/take care of"},
                ],
            })
            self.assertEqual(answer_point_tags(batch, "q-1"), ["people"])
            self.assertEqual(answer_point_tags(batch, "q-2"), [])
            save_json(batch / files[1], {"answers": {"1": "children"}})
            self.assertEqual(answer_point_tags(batch, "q-1"), [])

    def test_combines_source_and_counts_words(self):
        labels = {'source': '2022年安徽省初中学业水平考试', 'source_type': '中考真题',
                  'type': '阅读理解', 'subtypes': ['选择4题'], 'difficulty': '适中',
                  'knowledge': ['主题/人与自然/自然生态/地理环境']}
        subs = [{'stem': 'What is it?', 'options': [{'label': 'A', 'text': 'some words here'}]}]
        out = preview_labels(labels, subs, {'material': 'Antarctica is cold and huge.'}, {})
        self.assertEqual(out['source_type'], '中考真题')
        self.assertNotIn('source_combined', out)
        self.assertEqual(out['difficulty'], '适中')
        self.assertEqual(out['type'], '阅读理解')
        self.assertEqual(out['subtypes'], ['选择4题'])
        self.assertEqual(out['knowledge'], ['人与自然/地理环境'])
        self.assertEqual(out['word_count'], 11)  # 6 material + 3 stem + 2 option letters

    def test_missing_labels_are_blank_not_crash(self):
        out = preview_labels({}, [], {}, {})
        self.assertNotIn('source_combined', out)
        self.assertEqual(out['difficulty'], '')
        self.assertEqual(out['knowledge'], [])
        self.assertEqual(out['word_count'], 0)


if __name__ == '__main__':
    unittest.main()


class ZhReasonTest(unittest.TestCase):
    def test_english_template_prefixes_become_chinese(self):
        from workflow.review import _zh_reason
        out = _zh_reason("What the question tests: 听力细节。Answer-to-point relation: A 对应图。")
        self.assertIn("本题考点", out)
        self.assertIn("答案与考点关系", out)
        self.assertNotIn("What the question tests", out)
        self.assertNotIn("Answer-to-point relation", out)
