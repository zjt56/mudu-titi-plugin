"""教材目录（课时同步练习）解析、精确匹配与试卷不匹配的边界。"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from workbench import textbook
from workflow import agent_tasks as tasks

ROOT = Path(__file__).resolve().parents[1]


def _loader():
    spec = importlib.util.spec_from_file_location(
        "build_textbook_catalog", ROOT / "scripts/02-structure/build_textbook_catalog.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TextbookCatalogTest(unittest.TestCase):
    def test_source_parses_to_expected_counts(self):
        builder = _loader()
        data = builder.parse(textbook.SOURCE_PATH.read_text(encoding="utf-8-sig"))
        self.assertEqual(data["counts"], {"versions": 3, "books": 18, "units": 138})
        self.assertEqual(sorted({row["version"] for row in data["catalog"]}), ["人教版", "外研版", "译林版"])

    def test_generated_json_matches_source(self):
        builder = _loader()
        self.assertEqual(builder.parse(textbook.SOURCE_PATH.read_text(encoding="utf-8-sig")),
                         json.loads(textbook.CATALOG_PATH.read_text(encoding="utf-8-sig")))

    def test_projects_are_first_class_units(self):
        yilin = textbook.book("牛津译林版七年级上册")
        names = [unit["unit"] for unit in yilin["units"]]
        self.assertIn("Project 1 A video about my new school life", names)
        self.assertIn("Project 2 A party plan to celebrate New Year", names)
        self.assertEqual(len(names), 10)
        self.assertNotIn("extras", yilin)

    def test_exact_resolution(self):
        placement = textbook.resolve("人教版", "人教版七年级上册", "Unit 1 You and Me", "Section A")
        self.assertEqual(placement["grade"], "七年级")
        self.assertEqual(placement["topic"], "结识新朋友与自我介绍")
        self.assertNotIn("section", textbook.resolve("人教版", "人教版七年级上册", "Unit 1 You and Me"))

    def test_fuzzy_names_are_rejected(self):
        for args in (("人教版", "人教版七年级上册", "Unit 1"),
                     ("人教版", "人教版七年级上册", "Unit 1 You and Me", "Section C"),
                     ("外研版", "人教版七年级上册", "Unit 1 You and Me")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                textbook.resolve(*args)

    def test_validate_placement_requires_evidence_and_known_names(self):
        good = {"version": "人教版", "book": "人教版七年级上册", "unit": "Unit 2 We're Family!",
                "section": "Section A", "evidence": ["正文第1行：Unit 2 We're Family! Section A"]}
        validated = textbook.validate_placement(good)
        self.assertEqual(validated["unit"], "Unit 2 We're Family!")
        for broken in ({k: v for k, v in good.items() if k != "evidence"},
                       dict(good, unit="Unit 2"),
                       dict(good, version="译林版"),
                       dict(good, extra=1)):
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                textbook.validate_placement(broken)

    def test_sync_sources_only(self):
        self.assertTrue(textbook.needs_matching("同步练习"))
        self.assertTrue(textbook.needs_matching("单元测验"))
        for source in ("中考真题", "期末试题", "木牍原创", None, ""):
            self.assertFalse(textbook.needs_matching(source))


class KnowledgeTextbookWiringTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        self.write("01_source/正文.txt", "Unit 2 We're Family! Section A\n1. She reads. A. reads B. read")
        self.write("01_source/附录.txt", "1.A")
        self.write("01_source/structure.json", {"sections": [], "groups": []})
        self.write("01_source/structure.confirmed.json", {"units": [
            {"unit_id": "q-1", "unit_kind": "standalone", "numbers": [1], "type": "单项填空"}]})
        self.write("07_review/structure-gate.json", {"passed": True})
        self.write(tasks.ARTIFACTS["types"], {"items": [{
            "unit_id": "q-1", "type": "单项填空", "subtypes": ["四个选项"],
            "words": 3, "contentComplete": True, "evidence": ["正文 L2"]}]})
        self.write(tasks.ARTIFACTS["answers"], {"answers": {"1": "A"}, "evidence": {"1": ["附录 L1"]},
                                                 "difficulty": {"1": "容易"},
                                                 "difficultyEvidence": {"1": ["单句"]}})
        self.explanation = "无"
        self.write(tasks.ARTIFACTS["explanations"], {"items": [{
            "unit_id": "q-1", "explanation": self.explanation, "articleSummary": "",
            "subquestions": [], "evidence": ["正文 L2"]}]})

    def write(self, rel, value):
        path = self.batch / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def knowledge(self, **extra):
        row = {"unit_id": "q-1", "knowledge": ["词汇/词汇辨析/动词辨析"], "answer": "A",
               "explanation": self.explanation, "articleSummary": "", "subquestions": [],
               "evidence": ["正文 L2"]}
        row.update(extra)
        return {"items": [row]}

    def accept(self, payload):
        return tasks.accept_result(self.batch, "knowledge", {
            "status": "completed", "artifacts": {tasks.ARTIFACTS["knowledge"]: payload},
            "notes": [], "findings": []})

    def test_sync_paper_task_carries_textbook_index(self):
        self.write("01_source/metadata.json", {"source_type": "同步练习", "paper_title": "Unit 2 同步练习"})
        task = tasks.build_task(self.batch, "knowledge")
        self.assertEqual(task["status"], "ready")
        self.assertIn("textbook_index", task["prompt"])
        self.assertIn("Unit 2 We're Family!", task["prompt"])

    def test_submitted_source_type_controls_textbook_index(self):
        self.write('01_source/metadata.json', {'source_type': '未提供'})
        self.write('workflow/manifest.json', {'inputs': {}, 'meta': {'source_type': '同步练习', 'grade': '七年级'}})
        task = tasks.build_task(self.batch, 'knowledge')
        self.assertIn('"textbook_index":', task['prompt'])
        self.assertIn('七年级', task['prompt'])
        self.write('workflow/manifest.json', {'inputs': {}, 'meta': {'source_type': '中考真题'}})
        self.write('01_source/metadata.json', {'source_type': '同步练习'})
        task = tasks.build_task(self.batch, 'knowledge')
        self.assertNotIn('"textbook_index":', task['prompt'])

    def test_extra_batch_documents_are_visible_only_in_own_task(self):
        self.write('01_source/materials/attachment_001.txt', '配套解析：第1题专属材料')
        self.write('workflow/manifest.json', {'inputs': {'attachment_001': {'path': 'unused', 'exists': True}},
                                              'meta': {'source_type': '中考真题'}})
        task = tasks.build_task(self.batch, 'knowledge')
        self.assertIn('配套解析：第1题专属材料', task['prompt'])
        self.assertIn('01_source/materials/attachment_001.txt', task['input_hashes'])

    def test_types_task_receives_batch_pdf_ocr_evidence(self):
        self.write('01_source/pdf-ocr.json', {'sources': [{'input': 'attachment_001',
                   'pages': [{'page': 1, 'text': 'PDF 题组版面证据'}]}]})
        task = tasks.build_task(self.batch, 'types')
        self.assertIn('PDF 题组版面证据', task['prompt'])
        self.assertIn('01_source/pdf-ocr.json', task['input_hashes'])

    def test_exam_paper_task_has_no_textbook_index(self):
        self.write("01_source/metadata.json", {"source_type": "中考真题"})
        task = tasks.build_task(self.batch, "knowledge")
        self.assertNotIn('"textbook_index":', task["prompt"])
        self.assertNotIn(str(textbook.CATALOG_PATH.resolve()), task["input_hashes"])

    def test_valid_placement_is_accepted_and_invalid_rejected(self):
        placement = {"version": "人教版", "book": "人教版七年级上册", "unit": "Unit 2 We're Family!",
                     "section": "Section A", "evidence": ["正文第1行：Unit 2 We're Family! Section A"]}
        self.assertTrue(self.accept(self.knowledge(textbook=placement))["completed"])
        saved = json.loads((self.batch / tasks.ARTIFACTS["knowledge"]).read_text())
        self.assertEqual(saved["items"][0]["textbook"]["unit"], "Unit 2 We're Family!")
        for broken in (dict(placement, unit="Unit 2"),
                       dict(placement, evidence=[])):
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                self.accept(self.knowledge(textbook=broken))


if __name__ == "__main__":
    unittest.main()
