import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from workflow import assemble_precheck  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "check_difficulty", ROOT / "scripts/06-upload/check_difficulty.py")
check_difficulty = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_difficulty)


def unit(**changes):
    item = {
        "unit_id": "q-1", "id": "q-1", "unit_kind": "standalone",
        "number": 1, "numbers": [1], "question_type": "单项填空",
        "filterMetadata": {"subtypes": ["四个选项"], "source": "某卷",
                           "source_type": "中考真题", "year": 2019, "region": "安徽省",
                           "difficulty": "容易", "difficultyValue": 0.83},
        "stem": "He ______ to school every day.", "material": "", "options": [
            {"label": "A", "text": "go"}, {"label": "B", "text": "goes"},
            {"label": "C", "text": "went"}, {"label": "D", "text": "gone"}],
        "knowledge": ["词汇/词汇辨析/动词辨析"], "answer": "B",
        "explanation": "第三人称单数用 goes。", "subquestions": [],
    }
    item.update(changes)
    return item


class AssemblePrecheckTest(unittest.TestCase):
    def test_clean_units_pass(self):
        result = assemble_precheck.precheck({"items": [unit()]})
        self.assertTrue(result["passed"], result["problems"])

    def test_cloze_child_blank_only_is_accepted_when_material_has_number(self):
        group = unit(
            unit_id="group-1-2", id="group-1-2", unit_kind="group", number=1, numbers=[1, 2],
            question_type="完形填空", filterMetadata={"subtypes": ["10空"]},
            knowledge=["语篇体裁/记叙文/故事", "主题/人与自我/生活与学习/日常活动"],
            stem="阅读下列短文，从每小题所给的A、B、C、D四个选项中选出一个最佳选项。",
            material="Tom is a boy. He __1__ to school and __2__ back.",
            options=[],
            subquestions=[
                {"number": 1, "stem": "______", "options": [
                    {"label": "A", "text": "go"}, {"label": "B", "text": "goes"},
                    {"label": "C", "text": "went"}, {"label": "D", "text": "gone"}],
                 "knowledge": ["词汇/词汇辨析/动词辨析"], "answer": "B", "explanation": "x"},
                {"number": 2, "stem": "2.", "options": [
                    {"label": "A", "text": "come"}, {"label": "B", "text": "comes"},
                    {"label": "C", "text": "came"}, {"label": "D", "text": "coming"}],
                 "knowledge": ["词汇/词汇辨析/动词辨析"], "answer": "B", "explanation": "x"},
            ])
        result = assemble_precheck.precheck({"items": [group]})
        self.assertTrue(result["passed"], result["problems"])

    def test_cloze_numbered_child_still_rejects_mismatched_number(self):
        group = unit(
            unit_id="group-1-2", id="group-1-2", unit_kind="group", number=1, numbers=[1, 2],
            question_type="完形填空", filterMetadata={"subtypes": ["10空"]},
            stem="指令", material="材料", options=[],
            subquestions=[{"number": 2, "stem": "1.", "options": [
                {"label": "A", "text": "a"}, {"label": "B", "text": "b"},
                {"label": "C", "text": "c"}, {"label": "D", "text": "d"}],
                "knowledge": ["词汇/词汇辨析/动词辨析"], "answer": "A", "explanation": "x"}])
        result = assemble_precheck.precheck({"items": [group]})
        self.assertFalse(result["passed"])
        self.assertTrue(any("[D8]" in problem for problem in result["problems"]), result["problems"])


class DifficultyCheckTest(unittest.TestCase):
    def test_conflicting_label_and_value(self):
        problems = check_difficulty.problems({"items": [unit()]})
        self.assertEqual(problems, [])

        bad = unit(filterMetadata={**unit()["filterMetadata"], "difficulty": "适中"})
        self.assertTrue(check_difficulty.problems({"items": [bad]}))

        invalid = unit(filterMetadata={**unit()["filterMetadata"], "difficulty": "简单"})
        self.assertTrue(check_difficulty.problems({"items": [invalid]}))

        unscored = unit(filterMetadata={**unit()["filterMetadata"], "difficultyValue": None})
        self.assertEqual(check_difficulty.problems({"items": [unscored]}), [])


if __name__ == "__main__":
    unittest.main()
