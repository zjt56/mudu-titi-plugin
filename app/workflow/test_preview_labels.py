"""原卷预览旁的快速核对标签：来源、知识点、难度、词数。"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from workbench import server
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
                    {"unit_id": "q-3", "question_type": "选词填空", "name": "happy",
                     "proposed_path": "词汇/答案考点/happy"},
                    {"unit_id": "q-4", "question_type": "教材语篇填空", "name": "remember",
                     "proposed_path": "词汇/答案考点/remember"},
                ],
            })
            self.assertEqual(answer_point_tags(batch, "q-1"), ["people"])
            self.assertEqual(answer_point_tags(batch, "q-2"), [])
            self.assertEqual(answer_point_tags(batch, "q-3"), [])
            self.assertEqual(answer_point_tags(batch, "q-4"), [])
            from workbench import taxonomy
            for excluded in ("选词填空", "教材语篇填空"):
                self.assertTrue(taxonomy.check_labels({
                    "type": excluded, "knowledge": ["词汇/答案考点/happy"],
                }), excluded)
            save_json(batch / files[1], {"answers": {"1": "children"}})
            self.assertEqual(answer_point_tags(batch, "q-1"), [])

    def test_answer_point_terms_preserve_numbered_multiword_alternatives(self):
        self.assertEqual(server._answer_point_terms(
            "1.VR　2.PRC　3.WHO　4.UN　5.PLA　6.CHINA", []),
            ["VR", "PRC", "WHO", "UN", "PLA", "CHINA"])
        self.assertEqual(server._answer_point_terms("A", ["A. lab / laboratory"]),
                         ["lab", "laboratory"])
        self.assertEqual(server._answer_point_terms("OK", []), ["OK"])
        self.assertEqual(server._answer_point_terms("You are", []), [])
        self.assertEqual(server._answer_point_terms("They're ready", []), [])
        self.assertEqual(server._answer_point_terms("3.14", []), [])

    def test_original_answer_points_are_projected_from_current_accepted_content(self):
        from workflow import contracts, manifest

        with tempfile.TemporaryDirectory() as directory:
            batch = Path(directory)
            question_id = "source-l4-q1"
            save_json(batch / "01_source_ans/answers.json", {
                "answers": {question_id: "A"}, "evidence": {question_id: {"source": "fixture"}}})
            save_json(batch / "06_batch/batch_meta.json", {"items": [{
                "unit_id": "source-u4", "question_type": "单项填空", "number": 1,
                "question_id": question_id,
                "options": ["A. children", "B. child"], "answer": "A",
                "subquestions": [],
            }]})
            save_json(batch / "03_types/types.json", {"items": [{
                "unit_id": "source-u4", "type": "单项填空",
            }]})
            save_json(batch / "01_source/structure.confirmed.json", {"identity_version": "scoped-v1",
                "units": [{"unit_id": "source-u4", "unit_kind": "standalone", "numbers": [1],
                           "question_ids": {"1": question_id}, "identity_version": "scoped-v1",
                           "section_id": "source", "source_section": {}}]})
            save_json(batch / "workflow/manifest.json", {"stages": {}})
            saved = manifest.load(batch)
            answer_stage = contracts.STAGE_BY_ID["answers"]
            saved["stages"]["answers"].update(
                status=manifest.COMPLETED,
                output_hashes={"01_source_ans/answers.json": manifest.sha256_file(
                    batch / "01_source_ans/answers.json")})
            manifest.save(batch, saved)
            self.assertEqual(answer_point_tags(batch, "source-u4"), ["children"])
            self.assertEqual(answer_point_tags(batch, "source-u4", number="2"), [])
            bound = {relative: manifest.sha256_file(batch / relative) for relative in (
                "01_source_ans/answers.json", "03_types/types.json", "06_batch/batch_meta.json")}
            self.assertEqual(answer_point_tags(batch, "source-u4", review_hashes=bound), ["children"])

            for excluded_type in ("完成句子", "选词填空", "教材语篇填空"):
                save_json(batch / "03_types/types.json", {"items": [{
                    "unit_id": "source-u4", "type": excluded_type,
                }]})
                self.assertEqual(answer_point_tags(batch, "source-u4"), [], excluded_type)
                self.assertEqual(answer_point_tags(batch, "source-u4", review_hashes=bound), [], excluded_type)

            save_json(batch / "03_types/types.json", {"items": [{
                "unit_id": "source-u4", "type": "单项填空",
            }]})
            item = json.loads((batch / "06_batch/batch_meta.json").read_text())
            item["items"][0]["answer"] = "child"
            save_json(batch / "06_batch/batch_meta.json", item)
            self.assertEqual(answer_point_tags(batch, "source-u4"), [])

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


class BandHeadingNotPreviewContentTest(unittest.TestCase):
    """板块栏目标题（如 【三、B 创新阅读专练】）只是容器分隔，不能混进题目预览。

    用户 2026-11-19：预览把栏目标题显示在了完成句子题干后面，要求只做区分、不当题目内容。
    """

    def test_is_band_heading_recognizes_wrapped_forms(self):
        from workflow.preview import _is_band_heading
        for line in ('**【三、B 创新阅读专练】**', '【三、B 创新阅读专练】',
                     '【A 基础巩固】', '**【一、重要考点集训】**', '三、创新阅读专练'):
            self.assertTrue(_is_band_heading(line), line)
        # 普通题干不是板块标题。
        self.assertFalse(_is_band_heading('**5.**公园里有很多种花。'))
        self.assertFalse(_is_band_heading('There are ____ flowers in the park.'))

    def test_scoped_slice_excludes_band_heading(self):
        from workflow.preview import _scoped_source_slice
        lines = [
            '**Ⅲ. 根据汉语意思完成句子,每空一词**',
            '**5.**公园里有很多种花。',
            'There are ________________________ flowers in the park. ',
            '**【三、B 创新阅读专练】**',
            '**1.**________ Some unrelated next-section line.',
        ]
        unit = {"unit_id": "u5", "unit_kind": "standalone", "numbers": [5],
                "stem": "公园里有很多种花。There are ________________________ flowers in the park.",
                "source_span": {"start_line": 2, "end_line": 4, "basis": "manual"}}
        out = _scoped_source_slice(unit, lines, batch_dir=None)
        self.assertTrue(out)
        self.assertFalse(any('创新阅读专练' in line for line in out), out)
        self.assertIn('There are ________________________ flowers in the park.', out)


class ZhReasonTest(unittest.TestCase):
    def test_english_template_prefixes_become_chinese(self):
        from workflow.review import _zh_reason
        out = _zh_reason("What the question tests: 听力细节。Answer-to-point relation: A 对应图。")
        self.assertIn("本题考点", out)
        self.assertIn("答案与考点关系", out)
        self.assertNotIn("What the question tests", out)
        self.assertNotIn("Answer-to-point relation", out)
