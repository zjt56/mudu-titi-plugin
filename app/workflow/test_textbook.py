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
        self.assertEqual(data["counts"], {"versions": 3, "books": 18, "units": 128})
        self.assertEqual(sorted({row["version"] for row in data["catalog"]}), ["人教版", "外研版", "译林版"])

    def test_generated_json_matches_source(self):
        builder = _loader()
        self.assertEqual(builder.parse(textbook.SOURCE_PATH.read_text(encoding="utf-8-sig")),
                         json.loads(textbook.CATALOG_PATH.read_text(encoding="utf-8-sig")))

    def test_yilin_units_match_remote_catalog(self):
        yilin = textbook.book("牛津译林版七年级上册")
        names = [unit["unit"] for unit in yilin["units"]]
        self.assertIn("Unit 1This is me!", names)
        self.assertIn("Unit 8 Let’s celebrate!", names)
        self.assertEqual(len(names), 8)
        self.assertNotIn("extras", yilin)

    def test_exact_resolution(self):
        placement = textbook.resolve("人教版", "人教版七年级上册", "Unit 1 You and Me", "第一课时 Section A (1a-1d)")
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
        good = {"version": "人教版", "book": "人教版七年级上册", "unit": "Unit 2 We’re Family!",
                "section": "第一课时 Section A (1a-1d)", "evidence": ["正文第1行：Unit 2 We’re Family! 第一课时 Section A (1a-1d)"]}
        validated = textbook.validate_placement(good)
        self.assertEqual(validated["unit"], "Unit 2 We’re Family!")
        for broken in ({k: v for k, v in good.items() if k != "evidence"},
                       dict(good, unit="Unit 2"),
                       dict(good, version="译林版"),
                       dict(good, extra=1)):
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                textbook.validate_placement(broken)

    def test_normalized_placement_can_be_validated_again(self):
        good = {"version": "人教版", "book": "人教版七年级上册", "unit": "Unit 2 We’re Family!",
                "section": "第一课时 Section A (1a-1d)", "evidence": ["正文 L1：Unit 2 We’re Family! 第一课时 Section A (1a-1d)"]}
        normalized = textbook.validate_placement(good)
        self.assertEqual(normalized["grade"], "七年级")
        self.assertEqual(textbook.validate_placement(normalized), normalized)
        for grade in ("八年级", "", None, 7):
            with self.subTest(grade=grade), self.assertRaisesRegex(ValueError, "grade differs"):
                textbook.validate_placement(dict(normalized, grade=grade))

    def test_sync_sources_only(self):
        self.assertTrue(textbook.needs_matching("同步练习"))
        self.assertTrue(textbook.needs_matching("单元测验"))
        self.assertTrue(textbook.needs_matching("课本原创"))
        for source in ("中考真题", "期末试题", "木牍原创", None, ""):
            self.assertFalse(textbook.needs_matching(source))

    def test_unit_level_context_and_section_matching_edge_cases(self):
        for ctx in ("综合阅读提升", "Unit 1 综合阅读提升（一）", "单元话题写作训练", "Reading Plus 拓展阅读", "单元测试"):
            with self.subTest(ctx=ctx):
                self.assertTrue(textbook.is_unit_level_context(ctx))
        for ctx in ("第一课时 Section A (1a-2d)", "第二课时 Pronunciation", ""):
            with self.subTest(ctx=ctx):
                self.assertFalse(textbook.is_unit_level_context(ctx))
        # 八上 Unit 1 首课时在远端目录未带「第一课时」前缀
        u8 = next(u for u in textbook.book("人教版八年级上册")["units"] if u["unit"] == "Unit 1 Happy Holiday")
        self.assertEqual(textbook._match_section(u8, ["第一课时 Section A (1a-1d)"]), "Section A (1a-1d)")
        self.assertEqual(textbook._match_section(u8, ["第一课时"]), "Section A (1a-1d)")
        # 外研版同一单元存在两个「第一课时」，带具体名称可唯一命中，仅写「第一课时」不误猜
        wy = next(u for u in textbook.book("外研版七年级上册")["units"] if u["unit"] == "Unit 1 A new start")
        self.assertEqual(textbook._match_section(wy, ["Unit 1 第一课时 Reading"]), "第一课时 Reading")
        self.assertIsNone(textbook._match_section(wy, ["第一课时"]))


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

    def test_sync_paper_task_enables_textbook_matching(self):
        self.write("01_source/metadata.json", {"source_type": "同步练习", "paper_title": "Unit 2 同步练习"})
        task = tasks.build_task(self.batch, "knowledge")
        self.assertEqual(task["status"], "ready")
        self.assertIn('"textbook_index":', task["prompt"])
        self.assertIn(str(textbook.CATALOG_PATH.resolve()), task["input_hashes"])
        self.assertIn('"enabled":true', task["prompt"])

    def test_submitted_source_type_controls_textbook_matching(self):
        self.write('01_source/metadata.json', {'source_type': '未提供'})
        self.write('workflow/manifest.json', {'inputs': {}, 'meta': {'source_type': '同步练习', 'grade': '七年级'}})
        task = tasks.build_task(self.batch, 'knowledge')
        self.assertIn('"textbook_index":', task['prompt'])
        self.assertIn('七年级', task['prompt'])
        self.write('workflow/manifest.json', {'inputs': {}, 'meta': {'source_type': '中考真题'}})
        self.write('01_source/metadata.json', {'source_type': '同步练习'})
        task = tasks.build_task(self.batch, 'knowledge')
        self.assertNotIn('"textbook_index":', task['prompt'])

    def test_content_agent_child_prompt_retains_textbook_index_and_example(self):
        from workflow import content_agent
        self.write("01_source/metadata.json", {
            "source_type": "同步练习", "textbook_version": "人教版", "grade": "七年级"
        })
        task = tasks.build_task(self.batch, "knowledge")
        self.assertIn('"textbook": {', task["prompt"])
        # 过滤后只保留人教版七年级
        self.assertIn('"book":"人教版七年级上册"', task["prompt"])
        self.assertNotIn('"book":"外研版七年级上册"', task["prompt"])
        # 即使 textbook_index 超过 6000 字符，content_agent._child_prompt 也必须保留
        big_ctx = content_agent._context(task["prompt"])[0]
        big_ctx["textbook_index"] = textbook.compact_index(include_sections=True)
        child_prompt = content_agent._child_prompt(task["prompt"], big_ctx, "knowledge", [])
        self.assertIn('"textbook_index":', child_prompt)

    def test_sync_accepts_lesson_unit_and_blank_textbook_placements(self):
        self.write("01_source/metadata.json", {"source_type": "同步练习"})
        # 1. 具体课时
        lesson_tb = {"version": "人教版", "book": "人教版七年级上册", "unit": "Unit 2 We’re Family!",
                     "section": "第一课时 Section A (1a-1d)", "evidence": ["正文 L1：Unit 2 We’re Family!"]}
        report = self.accept(self.knowledge(textbook=lesson_tb))
        self.assertTrue(report["completed"])
        saved = json.loads((self.batch / tasks.ARTIFACTS["knowledge"]).read_text())
        self.assertEqual(saved["items"][0]["textbook"]["section"], "第一课时 Section A (1a-1d)")
        self.assertEqual(saved["items"][0]["textbook"]["grade"], "七年级")

        # 2. 单元级非课时版块（如单元话题写作、综合阅读提升）：不填 section 直接挂到 unit
        self.write("01_source/structure.confirmed.json", {"units": [
            {"unit_id": "q-1", "unit_kind": "standalone", "numbers": [1], "type": "单项填空",
             "source_section": {"title": "单元话题写作", "context": "单元话题写作"}}]})
        unit_only_tb = {"version": "人教版", "book": "人教版七年级上册", "unit": "Unit 2 We’re Family!",
                        "evidence": ["正文 L1：Unit 2 We’re Family! 单元话题写作"]}
        report = self.accept(self.knowledge(textbook=unit_only_tb))
        self.assertTrue(report["completed"])
        saved = json.loads((self.batch / tasks.ARTIFACTS["knowledge"]).read_text())
        self.assertNotIn("section", saved["items"][0]["textbook"])
        self.assertEqual(saved["items"][0]["textbook"]["unit"], "Unit 2 We’re Family!")
        # 单元级非课时版块误填具体课时会被拒绝
        with self.assertRaisesRegex(ValueError, "单元级非课时版块"):
            self.accept(self.knowledge(textbook=lesson_tb))

        # 3. 无教材线索：留空放行
        report = self.accept(self.knowledge())
        self.assertTrue(report["completed"])
        saved = json.loads((self.batch / tasks.ARTIFACTS["knowledge"]).read_text())
        self.assertNotIn("textbook", saved["items"][0])

        # 4. 原卷卷头含完整教材线索、模型漏填 textbook 时，确定性自动补齐（含单元级非课时版块）
        self.write("01_source/正文.txt", "七上 · 人教版 · 课时A计划\n**Starter Unit 2　Keep Tidy!**\n**第二课时**　Pronunciation\n1. She reads.")
        self.write("01_source/structure.confirmed.json", {"units": [
            {"unit_id": "q-1", "unit_kind": "standalone", "numbers": [1], "type": "单项填空",
             "source_section": {"title": "Ⅰ. 语音题", "context": "第二课时　Pronunciation"}}]})
        report = self.accept(self.knowledge())
        self.assertTrue(report["completed"])
        saved = json.loads((self.batch / tasks.ARTIFACTS["knowledge"]).read_text())
        self.assertEqual(saved["items"][0]["textbook"]["book"], "人教版七年级上册")
        self.assertEqual(saved["items"][0]["textbook"]["unit"], "Starter Unit 2 Keep Tidy!")
        self.assertEqual(saved["items"][0]["textbook"]["section"], "第二课时 Pronunciation")

        # 单元级非课时版块（综合阅读提升）：自动补齐到 unit 级，不填 section
        self.write("01_source/正文.txt", "九上 · 人教版 · 课时A计划\n**Unit 1　The Changing World**\n**综合阅读提升**\n1. She reads.")
        self.write("01_source/structure.confirmed.json", {"units": [
            {"unit_id": "q-1", "unit_kind": "standalone", "numbers": [1], "type": "单项填空",
             "source_section": {"title": "阅读理解", "context": "综合阅读提升"}}]})
        report = self.accept(self.knowledge())
        self.assertTrue(report["completed"])
        saved = json.loads((self.batch / tasks.ARTIFACTS["knowledge"]).read_text())
        self.assertEqual(saved["items"][0]["textbook"]["book"], "人教版九年级上册")
        self.assertEqual(saved["items"][0]["textbook"]["unit"], "Unit 1 The Changing World")
        self.assertNotIn("section", saved["items"][0]["textbook"])

    def test_extra_batch_documents_are_visible_only_in_own_task(self):
        self.write('01_source/materials/attachment_001.txt', '配套解析：第1题专属材料')
        self.write('workflow/manifest.json', {'inputs': {'attachment_001': {'path': 'unused', 'exists': True}},
                                              'meta': {'source_type': '中考真题'}})
        task = tasks.build_task(self.batch, 'knowledge')
        self.assertIn('配套解析：第1题专属材料', task['prompt'])
        self.assertIn('01_source/materials/attachment_001.txt', task['input_hashes'])

    def test_pdf_layout_evidence_is_bound_only_for_layout_consumers(self):
        self.write('01_source/pdf-ocr.json', {'sources': [{'input': 'attachment_001',
                   'pages': [{'page': 1, 'text': 'PDF 题组版面证据'}]}]})
        task = tasks.build_task(self.batch, 'types')
        self.assertNotIn('PDF 题组版面证据', task['prompt'])
        self.assertNotIn('01_source/pdf-ocr.json', task['input_hashes'])
        task = tasks.build_task(self.batch, 'assemble')
        self.assertIn('PDF 题组版面证据', task['prompt'])
        self.assertIn('01_source/pdf-ocr.json', task['input_hashes'])

    def test_exam_paper_task_has_no_textbook_index(self):
        self.write("01_source/metadata.json", {"source_type": "中考真题"})
        task = tasks.build_task(self.batch, "knowledge")
        self.assertNotIn('"textbook_index":', task["prompt"])
        self.assertNotIn(str(textbook.CATALOG_PATH.resolve()), task["input_hashes"])

    def test_non_sync_placement_is_omitted_without_weakening_knowledge_checks(self):
        self.write("01_source/metadata.json", {"source_type": "中考真题"})
        for placement in ({"unit": "错误旧教材", "evidence": "旧格式"}, None, []):
            with self.subTest(placement=placement):
                report = self.accept(self.knowledge(textbook=placement))
                self.assertTrue(report["completed"])
                saved = json.loads((self.batch / tasks.ARTIFACTS["knowledge"]).read_text())
                self.assertNotIn("textbook", saved["items"][0])
                self.assertEqual(saved["items"][0]["knowledge"], ["词汇/词汇辨析/动词辨析"])
        with self.assertRaises(ValueError):
            self.accept(self.knowledge(knowledge=["不存在的知识点"], textbook={}))

    def test_assembly_overlays_preserve_or_clear_local_textbook(self):
        from workflow import assemble_local, batch_fill
        tb = {"version": "人教版", "book": "人教版七年级上册", "unit": "Unit 1 You and Me",
              "section": "第一课时 Section A (1a-1d)", "evidence": ["正文 L1"]}
        for overlay in (assemble_local._overlay_labels,
                        lambda target, source: batch_fill._overlay(target, source, "knowledge")):
            target = {"stem": "原题", "textbookMappings": [{"textbookId": "existing"}]}
            overlay(target, {"knowledge": ["词汇/词汇辨析/动词辨析"], "textbook": tb})
            self.assertEqual(target["textbook"], tb)
            self.assertEqual(target["stem"], "原题")
            self.assertEqual(target["textbookMappings"], [{"textbookId": "existing"}])
            # 无 textbook 时清除旧值
            overlay(target, {"knowledge": ["词汇/词汇辨析/动词辨析"]})
            self.assertNotIn("textbook", target)

    def test_upload_payload_maps_local_textbook_for_lesson_unit_and_blank(self):
        from workflow import upload_payload
        catalog = {
            "version": {"id": "KCV-ENGLISH-KNOWLEDGE-1-1", "subject_id": "english", "textbook_id": None, "status": "active"},
            "points": [
                {"id": "p-root", "name": "词汇", "parent_id": None, "version_id": "KCV-ENGLISH-KNOWLEDGE-1-1", "active": 1},
                {"id": "p-mid", "name": "词汇辨析", "parent_id": "p-root", "version_id": "KCV-ENGLISH-KNOWLEDGE-1-1", "active": 1},
                {"id": "p-leaf", "name": "动词辨析", "parent_id": "p-mid", "version_id": "KCV-ENGLISH-KNOWLEDGE-1-1", "active": 1},
            ],
            "textbooks": [
                {"id": "TB-EN-PEP-NEW", "name": "人教版（新教材）", "subject_id": "english", "active": 1},
            ],
            "curriculum_nodes": {
                "TB-EN-PEP-NEW": [
                    {"id": "U1", "parent_id": None, "grade": "7年级上册", "name": "Unit 1 You and Me", "active": 1},
                    {"id": "L1", "parent_id": "U1", "grade": "7年级上册", "name": "第一课时 Section A (1a-1d)", "active": 1},
                ],
            },
        }
        base_item = {
            "id": "q-1", "unit_kind": "standalone", "number": 1, "numbers": [1],
            "type": "单项填空", "difficulty": "易", "source_type": "同步练习", "source": "人教七上同步",
            "stem": "She ____ to school.", "options": ["A. goes", "B. go", "C. went"], "answer": "A", "explanation": "无",
            "knowledge": ["词汇/词汇辨析/动词辨析"], "filterMetadata": {"subtypes": ["三个选项"]},
        }
        # 1. 具体课时（七年级上册 → 上学期）
        item_lesson = dict(base_item, textbook={"version": "人教版", "book": "人教版七年级上册",
                                                "unit": "Unit 1 You and Me", "section": "第一课时 Section A (1a-1d)",
                                                "evidence": ["正文 L1"]})
        res = upload_payload.build_payload({"batch_id": "b1", "name": "同步练习", "items": [item_lesson]}, catalog=catalog)
        self.assertTrue(res["passed"], res["errors"])
        out = res["payload"]["items"][0]
        self.assertEqual(out["textbook_id"], "TB-EN-PEP-NEW")
        self.assertEqual(out["curriculum"], "7年级上册 · Unit 1 You and Me · 第一课时 Section A (1a-1d)")
        self.assertEqual(out["filterMetadata"]["semesters"], ["上学期"])
        self.assertEqual(out["textbookMappings"], [{
            "textbookId": "TB-EN-PEP-NEW",
            "knowledgeVersionId": "KCV-ENGLISH-KNOWLEDGE-1-1",
            "knowledgeIds": ["p-leaf"],
            "curriculum": "7年级上册 · Unit 1 You and Me · 第一课时 Section A (1a-1d)",
        }])
        self.assertNotIn("textbook", out)

        # 2. 单元级非课时版块（无 section，直接挂到单元，同样自动映射上学期）
        item_unit = dict(base_item, textbook={"version": "人教版", "book": "人教版七年级上册",
                                              "unit": "Unit 1 You and Me", "evidence": ["正文 L1：综合阅读提升"]})
        res_u = upload_payload.build_payload({"batch_id": "b2", "name": "同步练习", "items": [item_unit]}, catalog=catalog)
        self.assertTrue(res_u["passed"], res_u["errors"])
        out_u = res_u["payload"]["items"][0]
        self.assertEqual(out_u["textbook_id"], "TB-EN-PEP-NEW")
        self.assertEqual(out_u["curriculum"], "7年级上册 · Unit 1 You and Me")
        self.assertEqual(out_u["filterMetadata"]["semesters"], ["上学期"])
        self.assertEqual(out_u["textbookMappings"][0]["curriculum"], "7年级上册 · Unit 1 You and Me")

        # 2b. 下册、显式 curriculum 及春/秋季节标记自动映射学期；已有显式 semesters 不覆盖
        self.assertEqual(textbook.book_semester("人教版八年级下册"), "下学期")
        self.assertEqual(textbook.book_semester("9年级上册 · Unit 1"), "上学期")
        self.assertEqual(textbook.book_semester("七下"), "下学期")
        self.assertEqual(textbook.book_semester("26秋人教版课时A计划"), "上学期")
        self.assertEqual(textbook.book_semester("1126秋人教版StarterUnit3"), "上学期")
        self.assertEqual(textbook.book_semester("2026年秋期中试卷"), "上学期")
        self.assertEqual(textbook.book_semester("秋季同步练习"), "上学期")
        self.assertEqual(textbook.book_semester("25春八年级同步"), "下学期")
        self.assertEqual(textbook.book_semester("2025年春季期末"), "下学期")
        self.assertEqual(textbook.book_semester("（春）课时练"), "下学期")
        self.assertEqual(textbook.book_semester("2025年长春市中考"), "")
        self.assertEqual(textbook.book_semester("2025年春秋战国阅读"), "")
        self.assertEqual(textbook.book_semester("2025中秋节阅读"), "")
        self.assertEqual(textbook.book_semester("中考真题"), "")
        item_lower = dict(base_item, curriculum="8年级下册 · Unit 1 What's the matter?")
        res_lower = upload_payload.build_payload({"batch_id": "b2b", "name": "同步练习", "items": [item_lower]}, catalog=catalog)
        self.assertTrue(res_lower["passed"], res_lower["errors"])
        self.assertEqual(res_lower["payload"]["items"][0]["filterMetadata"]["semesters"], ["下学期"])
        item_spring = dict(base_item, source="25春人教同步")
        res_spring = upload_payload.build_payload({"batch_id": "b2s", "name": "同步练习", "items": [item_spring]}, catalog=catalog)
        self.assertTrue(res_spring["passed"], res_spring["errors"])
        self.assertEqual(res_spring["payload"]["items"][0]["filterMetadata"]["semesters"], ["下学期"])
        item_autumn = dict(base_item, source="26秋人教同步")
        res_autumn = upload_payload.build_payload({"batch_id": "b2a", "name": "同步练习", "items": [item_autumn]}, catalog=catalog)
        self.assertTrue(res_autumn["passed"], res_autumn["errors"])
        self.assertEqual(res_autumn["payload"]["items"][0]["filterMetadata"]["semesters"], ["上学期"])
        item_explicit = dict(item_lesson, filterMetadata={"subtypes": ["三个选项"], "semesters": ["下学期"]})
        res_exp = upload_payload.build_payload({"batch_id": "b2c", "name": "同步练习", "items": [item_explicit]}, catalog=catalog)
        self.assertTrue(res_exp["passed"], res_exp["errors"])
        self.assertEqual(res_exp["payload"]["items"][0]["filterMetadata"]["semesters"], ["下学期"])

        # 3. 留空放行（不自动填 semesters）
        res_blank = upload_payload.build_payload({"batch_id": "b3", "name": "同步练习", "items": [base_item]}, catalog=catalog)
        self.assertTrue(res_blank["passed"], res_blank["errors"])
        self.assertNotIn("textbook_id", res_blank["payload"]["items"][0])
        self.assertNotIn("curriculum", res_blank["payload"]["items"][0])
        self.assertNotIn("semesters", res_blank["payload"]["items"][0]["filterMetadata"])

        # 4. 远端目录漂移（缺失课时节点）报错拦截
        item_drift = dict(base_item, textbook={"version": "人教版", "book": "人教版七年级上册",
                                               "unit": "Unit 1 You and Me", "section": "第二课时 Section A (2a-2e)",
                                               "evidence": ["正文 L1"]})
        res_drift = upload_payload.build_payload({"batch_id": "b4", "name": "同步练习", "items": [item_drift]}, catalog=catalog)
        self.assertFalse(res_drift["passed"])
        self.assertTrue(any(e["code"] == "REMOTE_CURRICULUM_MISSING" for e in res_drift["errors"]))


if __name__ == "__main__":
    unittest.main()
