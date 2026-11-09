"""Trust-boundary and domain validation tests for JSON-only local agent tasks."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import agent_tasks as tasks
from workflow.structure_gate import _build_units

SUMMARY = "本文讲述校园生活中的一段经历，并说明了同学之间的互助。"


def explanations_of(knowledge):
    """从知识点夹具派生子智能体1的产物，供 knowledge 阶段逐字搬运。"""
    items = []
    for row in knowledge["items"]:
        items.append({"unit_id": row["unit_id"], "explanation": row.get("explanation", ""),
                      "articleSummary": row.get("articleSummary", ""),
                      "subquestions": [{"number": child["number"], "explanation": child.get("explanation", "")}
                                       for child in row.get("subquestions", [])],
                      "evidence": row.get("evidence") or ["正文 L1"]})
    return {"items": items}


class AgentTasksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        self.write("01_source/正文.txt", "1. She ______ a book. A. reads B. read C. reading D. to read")
        self.write("01_source/structure.json", {"sections": [{"type": "单项填空", "questions": [1]}], "groups": []})
        # Actual structure_gate output has no ID; do not invent a fixture shape.
        self.units = _build_units([{"type": "单项填空", "questions": [1]}], [], ["1. She ______ a book."])
        self.write("01_source/structure.confirmed.json", {"version": "1.0", "units": self.units})
        self.write("01_source/附录.txt", "答案: 1. A")
        self.types = {"items": [{"unit_id": "q-1", "type": "单项填空", "subtypes": ["四个选项"],
                                "words": 5, "contentComplete": True, "evidence": ["正文 L1: A–D"]}]}
        self.answers = {"answers": {"1": "A"}, "evidence": {"1": ["附录 L1"]},
                        "missing": {}, "difficulty": {"1": "容易"}, "difficultyEvidence": {"1": ["单句、四个等长选项"]}}
        self.write(tasks.ARTIFACTS["types"], self.types)
        self.write(tasks.ARTIFACTS["answers"], self.answers)
        self.knowledge = {"items": [{"unit_id": "q-1", "knowledge": ["词汇/词汇辨析/动词辨析"],
            "answer": "A", "explanation": "无", "subquestions": [], "evidence": ["正文 L1"]}]}
        self.write(tasks.ARTIFACTS["explanations"], explanations_of(self.knowledge))
        self.write(tasks.ARTIFACTS["knowledge"], self.knowledge)

    def write(self, rel, value):
        path = self.batch / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def result(self, stage, payload, status="completed"):
        return {"status": status, "artifacts": {tasks.ARTIFACTS[stage]: payload}, "notes": [], "findings": []}

    def accept(self, stage, payload):
        return tasks.accept_result(self.batch, stage, self.result(stage, payload))

    def test_validate_only_does_not_install_candidate(self):
        artifact = self.batch / tasks.ARTIFACTS["types"]
        original = artifact.read_bytes()
        candidate = copy.deepcopy(self.types)
        candidate["items"][0]["words"] = 8
        report = tasks.accept_result(self.batch, "types", self.result("types", candidate), validate_only=True)
        self.assertTrue(report["completed"])
        self.assertEqual(artifact.read_bytes(), original)

    def test_actual_gate_units_get_stable_ids_without_mutation(self):
        self.assertNotIn("unit_id", self.units[0])
        self.assertEqual(tasks.confirmed_units(self.batch)[0]["unit_id"], "q-1")
        task = tasks.build_task(self.batch, "types")
        self.assertEqual(task["status"], "ready")
        self.assertIn('"unit_id": "q-1"', task["prompt"])
        self.assertNotIn(str(self.batch), task["prompt"])
        for key, digest in task["input_hashes"].items():
            path = Path(key) if Path(key).is_absolute() else self.batch / key
            if digest == tasks.MISSING_INPUT:
                self.assertIn(key, tasks.CONTENT_OPTIONAL_INPUTS)
                self.assertFalse(path.exists(), key)
            else:
                self.assertTrue(path.is_file(), key)

    def test_assemble_prompt_binds_word_and_pdf_ocr_as_structure_evidence(self):
        word = "1. She reads a book. A. reads B. read"
        ocr = {"documents": [{"file": "paper.pdf", "pages": [
            {"page": 1, "text": "1. She reacs a book. A. reacs B. read", "hierarchy": "Section I / question 1"}]}]}
        self.write("01_source/正文.txt", word)
        self.write("01_source/pdf-ocr.json", ocr)

        task = tasks.build_task(self.batch, "assemble")
        self.assertIn(word, task["prompt"])
        self.assertIn("She reacs a book", task["prompt"])
        self.assertIn("Section I / question 1", task["prompt"])
        self.assertIn("copy its wording, spelling, punctuation and options exactly", task["prompt"])
        self.assertIn("use PDF OCR only as a guide to hierarchy and layout", task["prompt"])
        self.assertIn("never to replace, correct or rewrite Word text", task["prompt"])
        for relative in ("01_source/正文.txt", "01_source/pdf-ocr.json"):
            self.assertEqual(task["input_hashes"][relative],
                             hashlib.sha256((self.batch / relative).read_bytes()).hexdigest())
        ocr["documents"][0]["pages"][0]["hierarchy"] = "Section II / question 1"
        self.write("01_source/pdf-ocr.json", ocr)
        updated = tasks.build_task(self.batch, "assemble")
        self.assertNotEqual(task["input_hashes"]["01_source/pdf-ocr.json"],
                            updated["input_hashes"]["01_source/pdf-ocr.json"])
        self.assertIn("Section II / question 1", updated["prompt"])

    def test_knowledge_task_does_not_bind_explanations(self):
        # 解析产物变化不能让知识点任务过期：提示词与指纹都不含 explanations。
        task = tasks.build_task(self.batch, "knowledge")
        self.assertNotIn(tasks.ARTIFACTS["explanations"], task["input_hashes"])
        # 规则文本里会提到该产物路径，但解析正文不应被塞进提示词。
        self.assertNotIn("本题考查动词辨析", task["prompt"])
        self.assertTrue(self.accept("knowledge", self.knowledge)["completed"])
        before = dict(task["input_hashes"])
        changed = explanations_of(self.knowledge)
        changed["items"][0]["explanation"] = "改写后的解析，与知识点任务无关。" + tasks.AI_MARK
        self.write(tasks.ARTIFACTS["explanations"], changed)
        # 解析改写只影响下一次导入时的逐字注入，不再让知识点任务整体过期重跑。
        self.assertEqual(tasks.build_task(self.batch, "knowledge")["input_hashes"], before)

    def test_knowledge_drops_repeated_type_fields_from_agent_output(self):
        candidate = copy.deepcopy(self.knowledge)
        candidate["items"][0].update({"type": "单项填空", "subtypes": ["四个选项"]})
        report = self.accept("knowledge", candidate)
        self.assertTrue(report["completed"])
        saved = json.loads((self.batch / tasks.ARTIFACTS["knowledge"]).read_text())
        self.assertNotIn("type", saved["items"][0])
        self.assertNotIn("subtypes", saved["items"][0])
        self.assertTrue(any("自动修复" in note for note in report["notes"]))

        # 解析由单独阶段拥有；知识点交付绝不读取或校验它。
        report = self.accept("knowledge", self.knowledge)
        self.assertTrue(report["completed"])
        saved = json.loads((self.batch / tasks.ARTIFACTS["knowledge"]).read_text())
        self.assertNotIn("answer", saved["items"][0])
        self.assertNotIn("explanation", saved["items"][0])
        forged = explanations_of(self.knowledge)
        forged["items"][0]["explanation"] = "根据上下文自行生成。" + tasks.AI_MARK
        self.write(tasks.ARTIFACTS["explanations"], forged)
        self.assertTrue(self.accept("knowledge", self.knowledge)["completed"])

    def test_assemble_cannot_rewrite_source_stem_or_options(self):
        row = {"unit_id": "q-1", "id": "q-1", "unit_kind": "standalone", "number": 1, "numbers": [1],
               "question_type": "单项填空", "filterMetadata": {"subtypes": ["四个选项"]},
               "stem": "</u>,mangled blank", "material": "", "options": ["A. reads", "B. read"],
               "knowledge": ["词汇/词汇辨析/动词辨析"], "answer": "A", "explanation": "无",
               "subquestions": []}
        source = {"unit_id": "q-1", "stem": "______,real sentence",
                  "material": "", "options": ["A. reads", "B. read"], "subquestions": []}
        notes = []
        tasks._restore_source_fields(row, [], source, notes)
        self.assertEqual(row["stem"], "______,real sentence")
        self.assertTrue(any("确定性切片" in note for note in notes))

    def test_blind_task_hides_current_labels_but_keeps_question_content(self):
        # 盲判：只给原卷、题干、材料、选项、答案、解析，不给现成标签。
        self.write("06_batch/batch_meta.json", {"items": [{
            "id": "q-1", "question_type": "单项填空", "filterMetadata": {"subtypes": ["四个选项"]},
            "knowledge": ["词汇/词汇辨析/动词辨析"], "stem": "1. She ______ a book.",
            "options": [{"key": "A", "text": "reads"}], "answer": "A", "explanation": "解析"}]})
        normal, _, _, _ = tasks._collect(self.batch, "review")
        blind = tasks._blind_context(normal)
        self.assertIn(tasks.ARTIFACTS["types"], normal)
        self.assertIn(tasks.ARTIFACTS["knowledge"], normal)
        self.assertNotIn(tasks.ARTIFACTS["types"], blind)
        self.assertNotIn(tasks.ARTIFACTS["knowledge"], blind)
        self.assertIn("01_source_ans/answers.json", blind)
        self.assertIn("04_knowledge/explanations.json", blind)
        item = blind["06_batch/batch_meta.json"]["items"][0]
        self.assertEqual(item["stem"], "1. She ______ a book.")
        self.assertEqual(item["options"], [{"key": "A", "text": "reads"}])
        self.assertNotIn("question_type", item)
        self.assertNotIn("knowledge", item)
        self.assertNotIn("subtypes", item.get("filterMetadata", {}))
        self.assertNotIn("type", blind["confirmed_units_with_stable_ids"][0])

    def test_source_structure_is_required_only_for_content_validation_stages(self):
        structure = self.batch / '01_source/structure.json'
        structure.unlink()
        for stage in ('knowledge', 'assemble'):
            with self.subTest(stage=stage):
                task = tasks.build_task(self.batch, stage)
                self.assertEqual(task['status'], 'needs_review')
                self.assertTrue(any('01_source/structure.json' in p for p in task['preconditions']))
        self.assertEqual(tasks.build_task(self.batch, 'types')['status'], 'ready')

    def test_explicit_confirmed_id_is_preserved(self):
        self.units[0]["unit_id"] = "TEST-001"
        self.write("01_source/structure.confirmed.json", {"units": self.units})
        self.assertEqual(tasks.confirmed_units(self.batch)[0]["unit_id"], "TEST-001")

    def test_unknown_stage_cannot_import_manifest(self):
        with self.assertRaises(ValueError):
            tasks.accept_result(self.batch, "extract", {"artifacts": {"workflow/manifest.json": {}}})
        self.assertFalse((self.batch / "workflow/manifest.json").exists())
        with self.assertRaises(ValueError):
            tasks.build_task(self.batch, "upload")

    def test_full_context_over_budget_is_actionable_never_truncated(self):
        tail = "TAIL_MUST_BE_PRESENT"
        self.write("01_source/正文.txt", "a" * 2000 + tail)
        full = tasks.build_task(self.batch, "types")
        self.assertIn(tail, full["prompt"])
        with self.assertRaisesRegex(ValueError, "Split the batch.*no text was truncated"):
            tasks.build_task(self.batch, "types", max_input_chars=100)

    def test_extra_artifact_validation_is_transactional(self):
        target = self.batch / tasks.ARTIFACTS["types"]
        original = target.read_bytes()
        result = self.result("types", self.types)
        result["artifacts"]["workflow/manifest.json"] = {"stages": {"types": "completed"}}
        with self.assertRaises(ValueError):
            tasks.accept_result(self.batch, "types", result)
        self.assertEqual(target.read_bytes(), original)
        self.assertFalse(Path(str(target) + ".bak").exists())

    def test_illegal_paths_rejected_even_for_needs_review(self):
        for path in ("../outside.json", "/tmp/out.json", "03_types/../types.json", "03_types\\types.json", "./03_types/types.json"):
            result = self.result("types", self.types, "needs_review")
            result["artifacts"] = {path: {}}
            with self.subTest(path=path), self.assertRaises(ValueError):
                tasks.accept_result(self.batch, "types", result)

    def test_symlink_target_and_backup_rejected(self):
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / "outside.json"
            outside.write_text("untouched")
            target = self.batch / tasks.ARTIFACTS["types"]
            target.unlink()
            target.symlink_to(outside)
            with self.assertRaises(ValueError):
                self.accept("types", self.types)
            self.assertEqual(outside.read_text(), "untouched")
            target.unlink()
            target.write_text("{}")
            Path(str(target) + ".bak").symlink_to(outside)
            with self.assertRaises(ValueError):
                self.accept("types", self.types)
            self.assertEqual(target.read_text(), "{}")

    def test_type_catalog_subtype_uniqueness_and_coverage(self):
        variants = []
        for changes in ({"type": "invented"}, {"subtypes": ["invented"]}, {"subtypes": []}, {"subtypes": ["四个选项", "四个选项"]}, {"unit_id": "invented"}, {"evidence": []}):
            candidate = copy.deepcopy(self.types)
            candidate["items"][0].update(changes)
            variants.append(candidate)
        variants += [{"items": []}, {"items": self.types["items"] * 2}]
        for candidate in variants:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                self.accept("types", candidate)

    def test_missing_subtype_error_names_unit_type_and_allowed_values(self):
        candidate = copy.deepcopy(self.types)
        candidate["items"][0]["subtypes"] = []
        with patch.dict("os.environ", {"WF_MODEL_FIRST": "0"}):
            with self.assertRaisesRegex(ValueError, "q-1.*单项填空.*三个选项.*四个选项"):
                self.accept("types", candidate)

    def test_success_backups_and_never_marks_manifest(self):
        self.write("workflow/manifest.json", {"inputs": {}, "stages": {"types": {"status": "pending"}}})
        manifest = (self.batch / "workflow/manifest.json").read_bytes()
        target = self.batch / tasks.ARTIFACTS["types"]
        old = target.read_bytes()
        report = self.accept("types", self.types)
        self.assertTrue(report["completed"])
        self.assertEqual(Path(str(target) + ".bak").read_bytes(), old)
        self.assertEqual((self.batch / "workflow/manifest.json").read_bytes(), manifest)

    def test_needs_review_candidate_remains_unqualified(self):
        target = self.batch / tasks.ARTIFACTS["types"]
        old = json.loads(target.read_text())
        report = tasks.accept_result(self.batch, "types", self.result("types", {"items": []}, "needs_review"))
        self.assertFalse(report["completed"])
        self.assertEqual(report["status"], "needs_review")
        self.assertEqual(report["artifacts"], [tasks.ARTIFACTS["types"]])
        self.assertEqual(json.loads(target.read_text()), old)

    def test_malformed_json_and_duplicate_keys(self):
        for raw in ('```json\n{}\n```', '{"status":"completed","status":"needs_review"}', '{"a":NaN}', '[]'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                tasks.accept_result(self.batch, "types", raw)

    def test_answer_shape_requires_complete_decisions_and_verbatim_source_binding(self):
        self.assertTrue(self.accept("answers", self.answers)["completed"])
        decided_missing = {"answers": {}, "evidence": {},
                           "missing": {"1": ["原文中未提供第1题答案，按无答案占位"]},
                           "difficulty": {"1": "容易"}}
        self.assertTrue(self.accept("answers", decided_missing)["completed"])
        with self.assertRaisesRegex(ValueError, "Every confirmed question"):
            self.accept("answers", {"answers": {}, "evidence": {}, "missing": {}})
        with self.assertRaisesRegex(ValueError, "verbatim-bound"):
            self.accept("answers", {"answers": {"1": "fabricated"}, "evidence": {"1": ["附录 L1"]}, "missing": {}})
        with self.assertRaises(ValueError):
            self.accept("answers", {"answers": {"1": "A"}, "evidence": {"2": ["附录 L1"]}, "missing": {}})

    def test_answers_ignore_extracted_word_markup_but_preserve_answer_text(self):
        source = ('**73****.****(Watching) a video about the findings of ancient ruins****.****'
                  '　【解析】原解析\n'
                  '**74****.****It wasn****’****t easy****.****/(It was) hard /difficult****.**\n'
                  '**75****.****Never give up****.****/Face difficulties bravely****.**')
        answers = {'73': '(Watching) a video about the findings of ancient ruins.',
                   '74': 'It wasn’t easy./(It was) hard /difficult.',
                   '75': 'Never give up./Face difficulties bravely.'}
        payload = {'answers': answers, 'evidence': {n: ['附录对应答案行'] for n in answers}, 'missing': {}}
        units = [{'unit_id': 'group-73-74-75', 'numbers': [73, 74, 75], 'unit_kind': 'group'}]
        tasks._validate(self.batch, 'answers', payload, {'01_source/附录.txt': source}, units)
        self.assertEqual(payload['answers'], answers)
        for wrong in ('Watching a video about the findings of ancient ruins.',
                      '(Watching) a video about the findings of modern ruins.',
                      '(Watching) a video about the findings of ancient ruins!',
                      '(watching) a video about the findings of ancient ruins.'):
            with self.subTest(wrong=wrong), self.assertRaisesRegex(ValueError, 'Answer 73.*verbatim-bound'):
                invalid = dict(payload, answers=dict(answers, **{'73': wrong}))
                tasks._validate(self.batch, 'answers', invalid, {'01_source/附录.txt': source}, units)

    def test_answer_contract_errors_name_the_offending_fields_and_numbers(self):
        # 重试反馈要能让模型自己改对：只说 Invalid fields 时模型只能靠猜，白烧一次模型调用。
        with self.assertRaisesRegex(ValueError, r"absent=\['missing'\]"):
            self.accept("answers", {"answers": {"1": "A"}, "evidence": {"1": ["附录 L1"]}})
        with self.assertRaisesRegex(ValueError, r"unexpected=\['answer'\]"):
            self.accept("answers", {"answer": "A", "answers": {"1": "A"},
                                    "evidence": {"1": ["附录 L1"]}, "missing": {}})
        with self.assertRaisesRegex(ValueError, r"evidence_without_answer=2"):
            self.accept("answers", {"answers": {"1": "A"}, "evidence": {"1": ["附录 L1"], "2": ["附录 L2"]},
                                    "missing": {}})
        with self.assertRaisesRegex(ValueError, r"not_in_batch=2"):
            self.accept("answers", {"answers": {"1": "A"}, "evidence": {"1": ["附录 L1"]},
                                    "missing": {"2": ["原文未提供"]}})

    def test_answers_task_example_carries_every_required_field(self):
        # 示例漏掉必填字段时，模型每一轮都会照抄，流程就会在同一个阶段无限重试（2026-11-09 卡点）。
        unit = {"unit_id": "q-1", "numbers": [1], "unit_kind": "standalone"}
        task = tasks._build_task_template("answers", 200000, {}, {}, [], [unit])
        tail = task["prompt"].split("Complete valid JSON syntax example", 1)[1]
        payload = json.loads(tail.split("\n", 1)[1].split("\nPreconditions:", 1)[0])
        example = payload["artifacts"][tasks.ARTIFACTS["answers"]]
        for field in ("answers", "evidence", "missing"):
            self.assertIn(field, example)
        self.assertFalse(set(example["answers"]) & set(example["missing"]))

    def test_every_stage_example_satisfies_its_declared_contract(self):
        # 覆盖所有内容阶段：任何一个阶段示例回退成缺键都会被这里挡住，不必再等真实批次空转。
        unit = {"unit_id": "q-1", "numbers": [1], "unit_kind": "standalone"}
        self.assertEqual(set(tasks.EXAMPLE_REQUIRED), set(tasks.ARTIFACTS))
        for stage, required in tasks.EXAMPLE_REQUIRED.items():
            with self.subTest(stage=stage):
                task = tasks._build_task_template(stage, 200000, {}, {}, [], [unit])
                tail = task["prompt"].split("Complete valid JSON syntax example", 1)[1]
                payload = json.loads(tail.split("\n", 1)[1].split("\nPreconditions:", 1)[0])
                example = payload["artifacts"][tasks.ARTIFACTS[stage]]
                self.assertEqual([key for key in required if key not in example], [],
                                 f"{stage} example lost a required key")

    def test_answer_comparison_tolerates_layout_but_rejects_content_changes(self):
        expected = 'He said, “It’s  fine.”'
        tasks._provenance({"answer": 'He said, "It\'s\n fine."', "explanation": ""}, "", expected)
        for answer in ('He said, "Its fine."', 'He said, "It\'s not fine."',
                       'Hesaid, "It\'s fine."', 'he said, "It\'s fine."'):
            with self.subTest(answer=answer), self.assertRaisesRegex(ValueError, "differs"):
                tasks._provenance({"answer": answer, "explanation": ""}, "", expected)
        # The source gate and downstream comparison share format tolerance.
        tasks._provenance({"answer": '"yes"', "explanation": ""}, '**“yes”**')
        for answer in ('**He said, “It’s fine.”**', 'He said, “It’s fine”',
                       'Ｈｅ said， “It’s fine。”', 'He said, “It’s fine.”'):
            with self.subTest(answer=answer):
                tasks._provenance({'answer': answer, 'explanation': ''}, '', expected)
        for expected_answer, changed in (('3.14', '314'), ('-2', '2'), ('A', 'B'),
                                         ('10²', '102'), ('do not go', 'do go')):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, 'differs'):
                tasks._provenance({'answer': changed, 'explanation': ''}, '', expected_answer)

    def test_answer_source_and_downstream_share_harmless_format_tolerance(self):
        source = '**1****.****It wasn****’****t easy****.****/(It was) hard /difficult****.**'
        variants = ('It wasn’t easy./(It was) hard /difficult.',
                    "It wasn't easy/(It was) hard/difficult",
                    'Ｉｔ wasn’t easy。／(It was) hard ／difficult。')
        unit = [{'unit_id': 'q-1', 'numbers': [1], 'unit_kind': 'standalone'}]
        for answer in variants:
            with self.subTest(answer=answer):
                payload = {'answers': {'1': answer}, 'evidence': {'1': ['附录']}, 'missing': {}}
                tasks._validate(self.batch, 'answers', payload, {'01_source/附录.txt': source}, unit)
                tasks._provenance({'answer': answer, 'explanation': ''}, '', variants[0])
                self.assertEqual(payload['answers']['1'], answer)

    def test_answers_task_uses_appendix_without_explicit_answers_input(self):
        from workflow import contracts
        self.assertEqual(contracts.STAGE_BY_ID["answers"].requires, ())
        task = tasks.build_task(self.batch, "answers")
        self.assertEqual(task["status"], "ready")
        self.assertIn("答案: 1. A", task["prompt"])

    def test_unprepared_answers_docx_keeps_warning_and_continues(self):
        docx = self.batch / "answers.docx"
        docx.write_bytes(b"binary")
        self.write("workflow/manifest.json", {"inputs": {"answers": {"path": str(docx)}}})
        task = tasks.build_task(self.batch, "answers")
        self.assertEqual(task["status"], "ready")
        self.assertIn("答案文件存在但尚未提取", task["prompt"])
        self.write("01_source_ans/正文.txt", "1. A")
        self.assertEqual(tasks.build_task(self.batch, "answers")["status"], "ready")

    def test_knowledge_leaf_scope_ids_and_provenance(self):
        self.assertTrue(self.accept("knowledge", self.knowledge)["completed"])
        for changes in ({"knowledge": ["词汇"]}, {"knowledge": ["主题/不存在"]},
                        {"knowledge": ["语篇体裁/记叙文/故事"]}, {"evidence": []}):
            candidate = copy.deepcopy(self.knowledge)
            candidate["items"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.accept("knowledge", candidate)
        # 用户规则：答案/解析由上游产物确定性覆盖，模型回显不一致不再阻断。
        for changes in ({"answer": "B"}, {"explanation": "unmarked generated explanation"}):
            candidate = copy.deepcopy(self.knowledge)
            candidate["items"][0].update(changes)
            with self.subTest(changes=changes):
                self.assertTrue(self.accept("knowledge", candidate)["completed"])
        # 路径合法的前提下，模型自填的错误 knowledgeIds 会被目录确定性回填纠正，而不是整轮重跑。
        corrected = copy.deepcopy(self.knowledge)
        corrected["items"][0]["knowledgeIds"] = ["wrong"]
        self.assertTrue(self.accept("knowledge", corrected)["completed"])

    def group_fixture(self):
        units = _build_units([{"type": "阅读理解", "questions": [[1, 3]]}], [], ["1. Text", "2. Text", "3. Text"])
        self.write("01_source/structure.confirmed.json", {"units": units})
        uid = "group-1-2-3"
        self.write("01_source/正文.txt", "1. Text\n2. Text\n3. Text")
        self.write("01_source/附录.txt", "【文章大意】" + SUMMARY + "\n" + "\n".join(
            f"{n}.{answer} 【解析】原卷第{n}题解析。" for n, answer in ((1, "A"), (2, "B"), (3, "C"))))
        self.write(tasks.ARTIFACTS["types"], {"items": [{"unit_id": uid, "type": "阅读理解", "subtypes": ["选择3题"], "evidence": ["L1"]}]})
        self.write(tasks.ARTIFACTS["answers"], {"answers": {"1": "A", "2": "B", "3": "C"}})
        knowledge = {"items": [{"unit_id": uid, "knowledge": ["语篇体裁/记叙文/故事", "主题/人与自我/生活与学习/学校生活"],
            "answer": "", "explanation": "", "articleSummary": SUMMARY, "evidence": ["L1–3"], "subquestions": [
                {"number": n, "knowledge": [], "answer": a, "explanation": f"原卷第{n}题解析。"}
                for n, a in ((1, "A"), (2, "B"), (3, "C"))]}]}
        self.write(tasks.ARTIFACTS["explanations"], explanations_of(knowledge))
        return knowledge

    def test_knowledge_keeps_only_labels_and_assembly_owns_source_content(self):
        valid = self.group_fixture()
        valid['items'][0]['articleSummary'] = '本文讲述校园生活中的一段经历，并说明了同学之间的互助。（AI生成，待校对）'
        self.assertTrue(self.accept('knowledge', valid)['completed'])
        candidate = copy.deepcopy(valid)
        candidate['items'][0]['articleSummary'] = ''
        self.assertTrue(self.accept('knowledge', candidate)['completed'])
        saved = json.loads((self.batch / tasks.ARTIFACTS["knowledge"]).read_text())["items"][0]
        for content_field in ("answer", "explanation", "articleSummary"):
            self.assertNotIn(content_field, saved)
        self.assertEqual([child["knowledge"] for child in saved["subquestions"]], [[], [], []])
        self.assertTrue(all("explanation" not in child and "answer" not in child
                            for child in saved["subquestions"]))

    def test_group_child_exact_coverage_and_reading_scope(self):
        valid = self.group_fixture()
        self.assertTrue(self.accept("knowledge", valid)["completed"])
        for children in (valid["items"][0]["subquestions"][:2], valid["items"][0]["subquestions"] * 2):
            candidate = copy.deepcopy(valid)
            candidate["items"][0]["subquestions"] = children
            with self.assertRaisesRegex(ValueError, "child number coverage"):
                self.accept("knowledge", candidate)
        candidate = copy.deepcopy(valid)
        candidate["items"][0]["subquestions"][0]["knowledge"] = ["设题角度/听力/细节理解"]
        with self.assertRaises(ValueError):
            self.accept("knowledge", candidate)

    def test_review_rejects_fake_signatures_and_incomplete_checks(self):
        self.write(tasks.ARTIFACTS["assemble"], {"items": []})
        valid = {"findings": [], "checked_units": ["q-1"], "unchecked": []}
        for changes in ({"signature": "trusted"}, {"reviewer": "independent"}, {"checked_units": []},
                        {"unchecked": [{"unit_id": "q-1", "required": True}]},
                        {"findings": [{"unit_id": "q-1", "severity": "major", "field": "answer", "reason": "wrong", "evidence": [], "signature": "fake"}]}):
            candidate = dict(valid, **changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.accept("review", candidate)
        self.assertFalse((self.batch / "07_review/unchecked.json").exists())

    def test_review_merge_preserves_human_decisions_and_creates_runner_queue(self):
        self.write(tasks.ARTIFACTS["assemble"], {"items": []})
        finding = {"unit_id": "q-1", "severity": "major", "field": "answer", "reason": "check", "evidence": ["L1"]}
        old = dict(finding, finding_id="WF-0001", status="accepted", actor="human")
        self.write("07_review/content-review.json", {"findings": [old]})
        self.write("07_review/metadata-review.json", {"findings": []})
        self.write("07_review/manual-review-queue.json", {"items": [dict(old, status="closed")]})
        payload = {"findings": [finding], "checked_units": ["q-1"], "unchecked": []}
        report = self.accept("review", payload)
        self.assertTrue(report["completed"])
        self.assertIn("07_review/manual-review-queue.json", report["artifacts"])
        stored = json.loads((self.batch / "07_review/content-review.json").read_text())["findings"]
        self.assertEqual(stored, [old])
        unchecked = json.loads((self.batch / "07_review/unchecked.json").read_text())
        self.assertEqual({r["round_id"] for r in unchecked}, {"labels", "sample"})
        self.assertTrue(all(r["required"] and r["status"] == "unchecked" for r in unchecked))

    def test_incomplete_review_never_creates_empty_unchecked(self):
        self.write(tasks.ARTIFACTS["assemble"], {"items": []})
        payload = {"findings": [], "checked_units": [], "unchecked": [{"unit_id": "q-1", "required": True}]}
        report = tasks.accept_result(self.batch, "review", self.result("review", payload, "needs_review"))
        self.assertFalse(report["completed"])
        self.assertFalse((self.batch / "07_review").exists())

    def test_review_multi_file_target_failure_writes_nothing(self):
        self.write(tasks.ARTIFACTS["assemble"], {"items": []})
        with tempfile.TemporaryDirectory() as other:
            folder = self.batch / "07_review"
            folder.mkdir()
            (folder / "manual-review-queue.json").symlink_to(Path(other) / "queue.json")
            with self.assertRaises(ValueError):
                self.accept("review", {"findings": [], "checked_units": ["q-1"], "unchecked": []})
            self.assertFalse((folder / "agent-review.json").exists())
            self.assertFalse((folder / "content-review.json").exists())

    def test_extra_original_audio_is_never_offered_as_a_cut(self):
        original = self.batch / 'attachments/audio_001.mp3'
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_bytes(b'original recording')
        self.write('workflow/manifest.json', {'inputs': {'audio_001': {'path': str(original), 'exists': True}}})
        context, _, _, _ = tasks._collect(self.batch, 'audio')
        self.assertNotIn('attachments/audio_001.mp3', context.get('existing_audio_files', []))

    def bind_audio_evidence(self, row, *, verified, review_reasons=None):
        self.units[0]["type"] = "听力短对话理解"
        self.write("01_source/structure.confirmed.json", {"units": self.units})
        self.types["items"][0]["type"] = "听力短对话理解"
        self.types["items"][0]["subtypes"] = ["选文字"]
        self.write(tasks.ARTIFACTS["types"], self.types)
        from audio.binding import listening_context
        binding, _listening = listening_context(self.batch)
        path = self.batch / row["file"]
        row["file_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        row["verified"] = verified
        if review_reasons:
            row["review_reasons"] = review_reasons
        return {"unit_binding": binding, "items": [row]}

    def test_audio_timestamps_without_existing_verified_cuts_rejected(self):
        self.write("02_audio/segment.mp3", "existing audio fixture")
        row = {"unit_id": "q-1", "file": "02_audio/segment.mp3", "start": 0, "end": 5, "evidence": ["first-line verification"]}
        self.bind_audio_evidence(dict(row), verified=True)
        self.write("02_audio/segment-evidence.json", {"items": []})
        with self.assertRaisesRegex(ValueError, "Timestamps are not cut outputs"):
            self.accept("audio", {"items": [row]})
        self.write("02_audio/segment-evidence.json", self.bind_audio_evidence(dict(row), verified=True))
        self.assertTrue(self.accept("audio", {"items": [row]})["completed"])
        row["start"] = -1
        with self.assertRaises(ValueError):
            self.accept("audio", {"items": [row]})

    def test_audio_candidate_slices_accepted_with_warning(self):
        self.write("02_audio/segment.mp3", "existing audio fixture")
        row = {"unit_id": "q-1", "file": "02_audio/segment.mp3", "start": 0, "end": 5,
               "evidence": ["candidate cut"]}
        self.write("02_audio/segment-evidence.json", self.bind_audio_evidence(
            dict(row), verified=False, review_reasons=["unsupported_end"]))
        report = self.accept("audio", {"items": [row]})
        self.assertTrue(report["completed"])
        self.assertTrue(any("候选切片" in note for note in report.get("notes", [])))

    def test_audio_candidate_slices_rejected_in_strict_mode(self):
        self.write("02_audio/segment.mp3", "existing audio fixture")
        row = {"unit_id": "q-1", "file": "02_audio/segment.mp3", "start": 0, "end": 5,
               "evidence": ["candidate cut"]}
        self.write("02_audio/segment-evidence.json", self.bind_audio_evidence(
            dict(row), verified=False, review_reasons=["unsupported_end"]))
        with patch.dict("os.environ", {"WF_ACCEPT_AUDIO_CANDIDATES": "0"}):
            with self.assertRaisesRegex(ValueError, "候选切片边界未验证"):
                self.accept("audio", {"items": [row]})

    def test_audio_prompt_requires_copying_verified_timestamps(self):
        self.write("02_audio/segment.mp3", "existing audio fixture")
        row = {"unit_id": "q-1", "file": "02_audio/segment.mp3", "start": 12, "end": 20,
               "evidence": ["first-line verification"]}
        self.write("02_audio/segment-evidence.json", self.bind_audio_evidence(dict(row), verified=True))
        prompt = tasks.build_task(self.batch, "audio")["prompt"]
        self.assertIn("copy file, start and end EXACTLY", prompt)
        self.assertIn("never re-based to the segment's own 0", prompt)

    def test_failed_structure_gate_remains_visible_to_continuing_agent(self):
        gate = {"passed": False, "blockers": [{"code": "UNCERTAIN"}]}
        self.write("07_review/structure-gate.json", gate)
        task = tasks.build_task(self.batch, "types")
        self.assertEqual(task["status"], "ready")
        self.assertIn("UNCERTAIN", task["prompt"])
        self.assertTrue(self.accept("types", self.types)["completed"])
        self.assertEqual(json.loads((self.batch / "07_review/structure-gate.json").read_text()), gate)

    def test_completed_nonreview_with_blocker_imports_nothing(self):
        target = self.batch / tasks.ARTIFACTS["types"]
        old = target.read_bytes()
        result = self.result("types", self.types)
        result["findings"] = [{"unit_id": "q-1", "severity": "major", "field": "type", "reason": "ambiguous", "evidence": ["L1"]}]
        report = tasks.accept_result(self.batch, "types", result)
        self.assertFalse(report["completed"])
        self.assertEqual(report["status"], "needs_review")
        self.assertEqual(target.read_bytes(), old)

    def test_assemble_preserves_word_options_when_pdf_ocr_misreads_them(self):
        self.write('01_source/pdf-ocr.json', {'sources': [{'input': 'attachment_001',
                   'status': 'completed', 'pages': [{'page': 1, 'text': '1. She reads a book. A. breads B. red'}]}]})
        row = dict(self.knowledge['items'][0], id='q-1', number=1, numbers=[1], unit_kind='standalone',
                   question_type='单项填空', filterMetadata={'subtypes': ['四个选项']},
                   stem='She ______ a book.', material='',
                   options=[{'label': label, 'text': text} for label, text in
                            (('A', 'reads'), ('B', 'read'), ('C', 'reading'), ('D', 'to read'))])
        self.assertTrue(self.accept('assemble', {'items': [row]})['completed'])
        installed = json.loads((self.batch / tasks.ARTIFACTS['assemble']).read_text())['items'][0]
        self.assertEqual(installed['options'], ['A. reads', 'B. read', 'C. reading', 'D. to read'])
        self.assertNotIn('breads', json.dumps(installed, ensure_ascii=False))

    def test_assemble_preserves_source_bound_group_material_through_normalization(self):
        from workflow import question_draft
        from workflow.wire_format import normalize_item

        uid = "group-1-2"
        children_text = ["1. Where is Tom? A. At home. B. At school. C. At work.",
                         "2. What will he do? A. Read. B. Swim. C. Run."]
        for shared in ("", "M: Tom is at home. He will read a book."):
            with self.subTest(shared=shared):
                lines = ["听下面一段对话。", *([shared] if shared else []), *children_text]
                sections = [{"type": "听力长对话理解", "questions": [[1, 2]]}]
                self.write("01_source/正文.txt", "\n".join(lines))
                self.write("01_source/structure.json", {"sections": sections, "groups": []})
                units = _build_units(sections, [], lines)
                self.write("01_source/structure.confirmed.json", {"units": units})
                self.write(tasks.ARTIFACTS["types"], {"items": [{"unit_id": uid,
                    "type": "听力长对话理解", "subtypes": ["选择2题"], "evidence": ["L1–3"]}]})
                self.write(tasks.ARTIFACTS["answers"], {"answers": {"1": "A", "2": "A"}})
                knowledge = {"items": [{"unit_id": uid, "knowledge": ["主题/人与自我/生活与学习/学校生活"],
                    "answer": "", "explanation": "无", "articleSummary": "无", "evidence": ["L1–3"],
                    "subquestions": [{"number": n, "knowledge": [], "answer": "A", "explanation": "无"}
                                     for n in (1, 2)]}]}
                self.write(tasks.ARTIFACTS["knowledge"], knowledge)
                self.write(tasks.ARTIFACTS["explanations"], explanations_of(knowledge))
                source = question_draft.build(self.batch)["items"][0]
                self.assertEqual(source["material"], shared)
                row = dict(source, question_type="听力长对话理解",
                           filterMetadata={"subtypes": ["选择2题"]},
                           knowledge=knowledge["items"][0]["knowledge"])
                row.pop("knowledgeIds", None)
                row.pop("knowledge_ids", None)
                expected_children = copy.deepcopy(row["subquestions"])
                self.assertTrue(self.accept("assemble", {"items": [row]})["completed"])
                installed = json.loads((self.batch / tasks.ARTIFACTS["assemble"]).read_text(encoding="utf-8"))["items"][0]
                self.assertEqual(installed["material"], shared)
                self.assertEqual(installed["stem"], source["stem"])
                for actual, expected in zip(installed["subquestions"], expected_children):
                    self.assertEqual(actual["stem"], expected["stem"])
                    self.assertEqual(actual["options"], expected["options"])
                self.assertEqual(normalize_item(installed)["material"], shared)

    def test_assemble_preserves_upstream_and_strict_labels(self):
        row = dict(self.knowledge["items"][0], id="q-1", number=1, numbers=[1], unit_kind="standalone",
                   question_type="单项填空", filterMetadata={"subtypes": ["四个选项"]}, stem="She ______ a book.", material="",
                   options=[{"label": label, "text": text} for label, text in (("A", "reads"), ("B", "read"), ("C", "reading"), ("D", "to read"))])
        payload = {"items": [row]}
        self.assertTrue(self.accept("assemble", payload)["completed"])
        installed = json.loads((self.batch / tasks.ARTIFACTS["assemble"]).read_text())
        self.assertTrue(installed["items"][0]["knowledgeIds"])
        # A22：装配器应确定性补齐 knowledge_ids，并与 knowledgeIds 一致。
        self.assertEqual(installed["items"][0]["knowledge_ids"], installed["items"][0]["knowledgeIds"])
        for changes in ({"numbers": [2]}, {"number": 2}, {"status": "published"}, {"knowledge": ["词汇"]},
                        {"filterMetadata": {"subtypes": ["三个选项"]}},
                        {"knowledgeIds": ["invented"]}, {"knowledge_ids": ["invented"]}, {"options": []}):
            candidate = copy.deepcopy(payload)
            candidate["items"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.accept("assemble", candidate)
        # answer/explanation/articleSummary 是确定性副本：模型抄写漂移由导入器
        # 按上游已验证产物覆盖，不再整批失败（可自动修复的错误不重试）。
        upstream_answer = self.knowledge["items"][0].get("answer")
        drifted = copy.deepcopy(payload)
        drifted["items"][0].update({"answer": "Z", "explanation": "编造的解析"})
        report = self.accept("assemble", drifted)
        self.assertTrue(report["completed"])
        installed = json.loads((self.batch / tasks.ARTIFACTS["assemble"]).read_text())
        self.assertEqual(installed["items"][0]["answer"], upstream_answer)
        self.assertNotEqual(installed["items"][0]["explanation"], "编造的解析")
        # 题干/选项按原卷确定性回填，模型抄错不再整批失败。
        for changes in ({"stem": "Foreign question text"},):
            drifted = copy.deepcopy(payload)
            drifted["items"][0].update(changes)
            with self.subTest(changes=changes):
                self.assertTrue(self.accept("assemble", drifted)["completed"])
            installed = json.loads((self.batch / tasks.ARTIFACTS["assemble"]).read_text())
            self.assertEqual(installed["items"][0]["stem"], "She ______ a book.")
            self.assertEqual(installed["items"][0]["stem"], "She ______ a book.")

    def test_assemble_fills_missing_source_placeholders_without_model_rework(self):
        self.write("01_source/structure.confirmed.json", {"units": self.units, "meta": {
            "paper_title": "未提供", "source": "未提供", "source_type": "未提供"}})
        row = dict(self.knowledge["items"][0], id="q-1", number=1, numbers=[1], unit_kind="standalone",
                   question_type="单项填空", filterMetadata={"subtypes": ["四个选项"]},
                   stem="She ______ a book.", material="",
                   options=[{"label": label, "text": text} for label, text in
                            (("A", "reads"), ("B", "read"), ("C", "reading"), ("D", "to read"))])
        self.assertTrue(self.accept("assemble", {"items": [row]})["completed"])
        installed = json.loads((self.batch / tasks.ARTIFACTS["assemble"]).read_text())
        filters = installed["items"][0]["filterMetadata"]
        self.assertEqual(filters["source"], "未提供")
        self.assertEqual(filters["source_type"], "未提供")
        self.assertNotIn("year", filters)

    def test_assemble_preserves_identified_original_metadata(self):
        self.write("01_source/structure.confirmed.json", {"units": self.units, "meta": {
            "source": "2022年安徽省初中学业水平考试", "source_type": "中考真题", "year": 2022, "region": "安徽省"}})
        row = dict(self.knowledge["items"][0], id="q-1", number=1, numbers=[1], unit_kind="standalone",
                   question_type="单项填空", filterMetadata={"subtypes": ["四个选项"], "year": 2026},
                   stem="She ______ a book.", material="", options=[])
        with self.assertRaisesRegex(ValueError, "source metadata"):
            self.accept("assemble", {"items": [row]})
        prompt = tasks.build_task(self.batch, "assemble")["prompt"]
        self.assertIn("Never let manifest filenames", prompt)

    def test_assemble_region_is_not_source_locator(self):
        self.write("01_source/structure.confirmed.json", {"units": self.units, "meta": {"region": "安徽省"}})
        row = dict(self.knowledge["items"][0], id="q-1", number=1, numbers=[1], unit_kind="standalone",
                   question_type="单项填空", filterMetadata={"subtypes": ["四个选项"],
                   "region": "安徽省", "location": "第1题"}, stem="She ______ a book.", material="",
                   options=[{"label": label, "text": text} for label, text in
                            (("A", "reads"), ("B", "read"), ("C", "reading"), ("D", "to read"))])
        self.assertTrue(self.accept("assemble", {"items": [row]})["completed"])
        row["filterMetadata"]["region"] = "江苏省"
        with self.assertRaisesRegex(ValueError, "source metadata region"):
            self.accept("assemble", {"items": [row]})

    def test_all_stage_prompts_fit_default_fixture_budget(self):
        for stage in ("types", "knowledge", "assemble", "review"):
            with self.subTest(stage=stage):
                task = tasks.build_task(self.batch, stage)
                self.assertLessEqual(len(task["prompt"]), 180000)
                self.assertNotIn(str(tasks.ROOT), task["prompt"])

    def test_atomic_install_rolls_back_first_file_on_replace_failure(self):
        self.write("03_types/a.json", {"old": 1})
        self.write("03_types/b.json", {"old": 2})
        original_replace = tasks.os.replace
        def fail_second(source, target):
            if str(target).endswith("b.json"):
                raise OSError("simulated disk failure")
            return original_replace(source, target)
        with patch.object(tasks.os, "replace", side_effect=fail_second):
            with self.assertRaises(OSError):
                tasks._install(self.batch, {"03_types/a.json": {"new": 1}, "03_types/b.json": {"new": 2}})
        self.assertEqual(json.loads((self.batch / "03_types/a.json").read_text()), {"old": 1})
        self.assertEqual(json.loads((self.batch / "03_types/b.json").read_text()), {"old": 2})


if __name__ == "__main__":
    unittest.main()
