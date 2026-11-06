"""Trust-boundary and domain validation tests for JSON-only local agent tasks."""
import copy
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
                        "difficulty": {"1": "容易"}, "difficultyEvidence": {"1": ["单句、四个等长选项"]}}
        self.write(tasks.ARTIFACTS["types"], self.types)
        self.write(tasks.ARTIFACTS["answers"], self.answers)
        self.knowledge = {"items": [{"unit_id": "q-1", "knowledge": ["词汇/词汇辨析/动词辨析"],
            "answer": "A", "explanation": "本题考查动词辨析，结合主语与时态判断应选 reads。" + tasks.AI_MARK, "subquestions": [], "evidence": ["正文 L1"]}]}
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
        for key in task["input_hashes"]:
            path = Path(key) if Path(key).is_absolute() else self.batch / key
            self.assertTrue(path.is_file(), key)

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

    def test_knowledge_without_appendix_can_accept_ai_explanation(self):
        (self.batch / "01_source/附录.txt").unlink()
        task = tasks.build_task(self.batch, "knowledge")
        self.assertEqual(task["status"], "ready")
        self.assertEqual(task["input_hashes"]["01_source/附录.txt"], tasks.MISSING_INPUT)
        self.assertTrue(self.accept("knowledge", self.knowledge)["completed"])

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

    def test_needs_review_imports_nothing(self):
        target = self.batch / tasks.ARTIFACTS["types"]
        old = target.read_bytes()
        report = tasks.accept_result(self.batch, "types", self.result("types", {"items": []}, "needs_review"))
        self.assertFalse(report["completed"])
        self.assertEqual(report["artifacts"], [])
        self.assertEqual(target.read_bytes(), old)

    def test_malformed_json_and_duplicate_keys(self):
        for raw in ('```json\n{}\n```', '{"status":"completed","status":"needs_review"}', '{"a":NaN}', '[]'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                tasks.accept_result(self.batch, "types", raw)

    def test_answer_shape_source_and_exact_number_coverage(self):
        self.assertTrue(self.accept("answers", self.answers)["completed"])
        for candidate in ({"1": "A"}, {"answers": {"2": "A"}, "evidence": {"2": ["L1"]}},
                          {"answers": {"1": "fabricated"}, "evidence": {"1": ["L1"]}}):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                self.accept("answers", candidate)

    def test_answer_comparison_only_normalizes_quotes_and_whitespace(self):
        expected = 'He said, “It’s  fine.”'
        tasks._provenance({"answer": 'He said, "It\'s\n fine."', "explanation": ""}, "", expected)
        for answer in ('He said, "Its fine."', 'He said, "It\'s not fine."',
                       'Hesaid, "It\'s fine."', 'he said, "It\'s fine."'):
            with self.subTest(answer=answer), self.assertRaisesRegex(ValueError, "differs"):
                tasks._provenance({"answer": answer, "explanation": ""}, "", expected)
        # Source extraction still requires verbatim text, even for equivalent punctuation.
        with self.assertRaisesRegex(ValueError, "Unsourced answer"):
            tasks._provenance({"answer": '"yes"', "explanation": ""}, '“yes”')

    def test_answers_task_uses_appendix_without_explicit_answers_input(self):
        from workflow import contracts
        self.assertEqual(contracts.STAGE_BY_ID["answers"].requires, ())
        task = tasks.build_task(self.batch, "answers")
        self.assertEqual(task["status"], "ready")
        self.assertIn("答案: 1. A", task["prompt"])

    def test_explicit_docx_requires_prepared_text(self):
        docx = self.batch / "answers.docx"
        docx.write_bytes(b"binary")
        self.write("workflow/manifest.json", {"inputs": {"answers": {"path": str(docx)}}})
        task = tasks.build_task(self.batch, "answers")
        self.assertEqual(task["status"], "needs_review")
        self.assertIn("parent extraction helper", task["preconditions"][0])
        with self.assertRaisesRegex(ValueError, "preconditions"):
            self.accept("answers", self.answers)
        self.write("01_source_ans/正文.txt", "1. A")
        self.assertEqual(tasks.build_task(self.batch, "answers")["status"], "ready")

    def test_knowledge_leaf_scope_ids_and_provenance(self):
        self.assertTrue(self.accept("knowledge", self.knowledge)["completed"])
        for changes in ({"knowledge": ["词汇"]}, {"knowledge": ["主题/不存在"]},
                        {"knowledge": ["语篇体裁/记叙文/故事"]},
                        {"answer": "B"}, {"explanation": "unmarked generated explanation"}, {"evidence": []}):
            candidate = copy.deepcopy(self.knowledge)
            candidate["items"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.accept("knowledge", candidate)
        # 路径合法的前提下，模型自填的错误 knowledgeIds 会被目录确定性回填纠正，而不是整轮重跑。
        corrected = copy.deepcopy(self.knowledge)
        corrected["items"][0]["knowledgeIds"] = ["wrong"]
        self.assertTrue(self.accept("knowledge", corrected)["completed"])

    def group_fixture(self):
        units = _build_units([{"type": "阅读理解", "questions": [[1, 3]]}], [], ["1. Text", "2. Text", "3. Text"])
        self.write("01_source/structure.confirmed.json", {"units": units})
        uid = "group-1-2-3"
        self.write(tasks.ARTIFACTS["types"], {"items": [{"unit_id": uid, "type": "阅读理解", "subtypes": ["选择3题"], "evidence": ["L1"]}]})
        self.write(tasks.ARTIFACTS["answers"], {"answers": {"1": "A", "2": "B", "3": "C"}})
        knowledge = {"items": [{"unit_id": uid, "knowledge": ["语篇体裁/记叙文/故事", "主题/人与自我/生活与学习/学校生活"],
            "answer": "", "explanation": "", "articleSummary": SUMMARY + tasks.AI_MARK, "evidence": ["L1–3"], "subquestions": [
                {"number": n, "knowledge": ["设题角度/阅读/细节理解"], "answer": a, "explanation": "根据首段细节可判断该题答案为该项，需回到原文对应句核对。" + tasks.AI_MARK}
                for n, a in ((1, "A"), (2, "B"), (3, "C"))]}]}
        self.write(tasks.ARTIFACTS["explanations"], explanations_of(knowledge))
        return knowledge

    def test_knowledge_accepts_article_summary_required_by_new_prompt(self):
        valid = self.group_fixture()
        valid['items'][0]['articleSummary'] = '本文讲述校园生活中的一段经历，并说明了同学之间的互助。（AI生成，待校对）'
        self.assertTrue(self.accept('knowledge', valid)['completed'])
        for malformed in ('', [], {'text': 'summary'}):
            candidate = copy.deepcopy(valid)
            candidate['items'][0]['articleSummary'] = malformed
            with self.assertRaisesRegex(ValueError, 'articleSummary'):
                self.accept('knowledge', candidate)

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

    def test_audio_timestamps_without_existing_verified_cuts_rejected(self):
        self.units[0]["type"] = "听力短对话理解"
        self.write("01_source/structure.confirmed.json", {"units": self.units})
        self.write("02_audio/segment.mp3", "existing audio fixture")
        row = {"unit_id": "q-1", "file": "02_audio/segment.mp3", "start": 0, "end": 5, "evidence": ["first-line verification"]}
        self.write("02_audio/segment-evidence.json", {"items": []})
        with self.assertRaisesRegex(ValueError, "Timestamps are not cut outputs"):
            self.accept("audio", {"items": [row]})
        self.write("02_audio/segment-evidence.json", {"items": [dict(row, verified=True)]})
        self.assertTrue(self.accept("audio", {"items": [row]})["completed"])
        row["start"] = -1
        with self.assertRaises(ValueError):
            self.accept("audio", {"items": [row]})

    def test_audio_prompt_requires_copying_verified_timestamps(self):
        self.units[0]["type"] = "听力短对话理解"
        self.write("01_source/structure.confirmed.json", {"units": self.units})
        self.write("02_audio/segment.mp3", "existing audio fixture")
        row = {"unit_id": "q-1", "file": "02_audio/segment.mp3", "start": 12, "end": 20,
               "evidence": ["first-line verification"]}
        self.write("02_audio/segment-evidence.json", {"items": [dict(row, verified=True)]})
        prompt = tasks.build_task(self.batch, "audio")["prompt"]
        self.assertIn("copy file, start and end EXACTLY", prompt)
        self.assertIn("never re-based to the segment's own 0", prompt)

    def test_failed_structure_gate_blocks_completed_import(self):
        self.write("07_review/structure-gate.json", {"passed": False, "blockers": [{"code": "UNCERTAIN"}]})
        self.assertEqual(tasks.build_task(self.batch, "types")["status"], "needs_review")
        with self.assertRaisesRegex(ValueError, "structure_gate has not passed"):
            self.accept("types", self.types)

    def test_completed_nonreview_with_blocker_imports_nothing(self):
        target = self.batch / tasks.ARTIFACTS["types"]
        old = target.read_bytes()
        result = self.result("types", self.types)
        result["findings"] = [{"unit_id": "q-1", "severity": "major", "field": "type", "reason": "ambiguous", "evidence": ["L1"]}]
        report = tasks.accept_result(self.batch, "types", result)
        self.assertFalse(report["completed"])
        self.assertEqual(report["status"], "needs_review")
        self.assertEqual(target.read_bytes(), old)

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
                        {"knowledgeIds": ["invented"]}, {"knowledge_ids": ["invented"]},
                        {"stem": "Foreign question text"}, {"options": []}):
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
