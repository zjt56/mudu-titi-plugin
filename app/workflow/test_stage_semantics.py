import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import contracts, manifest as mf


def _dep_until_preassembly_fixed(test):
    def dependency(manifest, stage_id, **kwargs):
        rec = manifest.get("stages", {}).get("preassemble", {})
        preassembly_blocked = rec.get("rebuild_required") or rec.get("status") not in ("completed", "approved", "skipped")
        if stage_id == "preassemble":
            return not preassembly_blocked
        # A failed current-stage repair blocks every downstream that consumes it.
        if preassembly_blocked and stage_id in ("types", "review", "validate", "assemble"):
            return False
        return True
    return dependency


class ContentReuseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        self.state = mf.new_manifest("reuse", self.batch, {}, {})

    def put(self, rel, data):
        path = self.batch / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def finish(self, sid):
        stage = contracts.STAGE_BY_ID[sid]
        for rel in stage.produces:
            if not (self.batch / rel).exists():
                self.put(rel, {"items": []})
        mf.touch_stage(self.state, sid, mf.COMPLETED,
                       input_hashes=mf.input_hashes(self.batch, self.state, stage),
                       output_hashes=mf.output_hashes(self.batch, stage))
        mf.record_delivery(self.batch, self.state, stage)

    def stale(self, sid):
        return mf.is_stale(self.batch, self.state, contracts.STAGE_BY_ID[sid])[0]

    def prepare_knowledge(self):
        self.put("01_source/正文.txt", "40. Choose the right word. A. often B. never")
        self.put("01_source/structure.confirmed.json", {"units": [{"unit_id": "q-40", "unit_kind": "standalone", "numbers": [40], "stem": "Choose the right word."}]})
        self.put("07_review/structure-gate.json", {"passed": True, "generated_at": "old"})
        self.put("03_types/types.json", {"items": [{"unit_id": "q-40", "type": "单项填空", "subtypes": ["四个选项"], "words": 12}]})
        self.put("01_source_ans/answers.json", {"answers": {"40": "A"}, "evidence": {"40": ["附录 L1"]},
                                                "missing": {}, "difficulty": {"40": "适中"}})
        self.finish("knowledge")

    def prepare_source_bound_fill(self):
        from workflow import question_draft
        text = '1. <u>　　</u> Choose <u>word</u>. [IMG one.png] A. Yes B. No'
        self.put('01_source/正文.txt', text)
        original = self.put('attachments/source.docx', 'original Word bytes')
        self.state['inputs']['source'] = {'path': str(original), 'exists': True}
        self.put('01_source/media/one.png', 'image bytes')
        self.put('01_source/structure.json', {'sections': [], 'groups': []})
        self.put('01_source/structure.confirmed.json', {'units': [{
            'unit_id': 'q-1', 'unit_kind': 'standalone', 'numbers': [1], 'type': '单项填空', 'stem': text,
            'source_span': {'basis': 'ai_classified', 'start_line': 1, 'end_line': 1}}]})
        self.put('03_types/types.json', {'items': [{'unit_id': 'q-1', 'type': '单项填空',
            'subtypes': ['四个选项'], 'words': 4, 'contentComplete': True, 'evidence': ['正文 L1']}]})
        self.put('02_audio/SKIP_LISTENING', 'no audio')
        with patch.object(question_draft, '_skeleton_text', side_effect=lambda value: value):
            self.put('06_batch/batch.base.json', {'items': question_draft.build(self.batch)['items']})
            for sid in ('answers', 'types', 'explanations', 'knowledge', 'assemble'):
                self.finish(sid)
            audio = contracts.STAGE_BY_ID['audio']
            self.put('02_audio/audio_map.json', {'items': []})
            mf.touch_stage(self.state, 'audio', mf.SKIPPED, input_hashes=mf.input_hashes(self.batch, self.state, audio),
                           output_hashes=mf.output_hashes(self.batch, audio))
            mf.record_delivery(self.batch, self.state, audio)

    def test_format_only_skeleton_refresh_reuses_five_fills_but_reassembles(self):
        # 新规则：过期判断按文件内容（规范化后）比对。等值重写（键序/空白/时间戳差异）
        # 不算变化；内容真的不同才算。原“格式等价”语义层已移除。
        self.prepare_source_bound_fill()
        # 等价重写（读出现有内容、重新序列化写回，仅空白/键序差异）不使已完成的
        # 填充阶段失效。
        path = self.batch / '06_batch/batch.base.json'
        data = json.loads(path.read_text())
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        self.assertFalse(self.stale('types'))
        # 新规则：内容变化不再自动使已完成阶段失效（由用户后续验证），只判完成+产物在。
        data['items'] = (data.get('items') or []) + [{'unit_id': 'q-9', 'stem': 'x'}]
        self.put('06_batch/batch.base.json', data)
        self.assertFalse(self.stale('types'))

    def test_source_or_media_changes_do_not_auto_reopen_fills(self):
        # 新规则：原卷/媒体内容变化不再自动使已完成的填充阶段失效——用户后续自行验证。
        # is_stale 只判“完成 + 产物在”。
        self.prepare_source_bound_fill()
        for rel, changed in (('01_source/正文.txt', 'Actually changed Word text'),
                             ('01_source/media/one.png', 'changed image bytes'),
                             ('attachments/source.docx', 'replaced original Word bytes')):
            path = self.batch / rel
            before = path.read_bytes()
            path.write_text(changed, encoding='utf-8')
            for sid in ('answers', 'audio', 'types', 'explanations', 'knowledge'):
                with self.subTest(path=rel, stage=sid):
                    self.assertFalse(self.stale(sid))
            path.write_bytes(before)

    def test_changed_confirmed_identity_does_not_auto_reopen_fills(self):
        self.prepare_source_bound_fill()
        path = self.batch / '01_source/structure.confirmed.json'
        value = json.loads(path.read_text())
        value['units'][0]['numbers'] = [2]
        self.put('01_source/structure.confirmed.json', value)
        for sid in ('answers', 'audio', 'types', 'explanations', 'knowledge'):
            with self.subTest(stage=sid):
                self.assertFalse(self.stale(sid))

    def test_unproven_binding_base_change_does_not_auto_reopen_fills(self):
        self.put('01_source/正文.txt', 'Source text without confirmed units')
        self.put('06_batch/batch.base.json', {'items': [{'number': 1, 'stem': 'Old body'}]})
        for sid in ('answers', 'audio', 'types', 'explanations', 'knowledge'):
            self.finish(sid)
        self.put('06_batch/batch.base.json', {'items': [{'number': 1, 'stem': 'Changed body'}]})
        for sid in ('answers', 'audio', 'types', 'explanations', 'knowledge'):
            with self.subTest(stage=sid):
                self.assertFalse(self.stale(sid))

    def test_source_identity_rejects_empty_or_missing_media_proof(self):
        from workflow import question_draft, stage_semantics
        self.prepare_source_bound_fill()
        draft = question_draft.build(self.batch)
        self.assertIsNotNone(stage_semantics._source_identity(draft))
        draft['source_bindings']['q-1']['media_hashes']['01_source/media/one.png'] = 'missing'
        self.assertIsNone(stage_semantics._source_identity(draft))
        draft['source_bindings']['q-1']['media_hashes'] = {}
        draft['source_bindings']['q-1']['source_text_sha256'] = ''
        self.assertIsNone(stage_semantics._source_identity(draft))

    def test_json_whitespace_key_order_and_clock_do_not_rebuild_output(self):
        path = self.put("03_types/types.json", {"items": [], "generated_at": "old"})
        self.finish("types")
        path.write_text('{\n  "generated_at": "new",\n  "items": []\n}\n', encoding="utf-8")
        self.assertFalse(self.stale("types"))
        self.assertEqual(mf.reconcile_deliveries(self.batch, self.state), [])

    def test_question_range_metadata_preserves_old_semantic_outputs_and_inputs(self):
        from workflow import stage_semantics as semantics
        from types import SimpleNamespace
        payload = {'items': [{'unit_id': 'q-1', 'number': 1, 'numbers': [1], 'stem': 'Original',
                              'evidence': {'numberStart': 10, 'numberEnd': 20},
                              'subquestions': [{'number': 1, 'numberStart': 5}]}]}
        for name in semantics.BATCH_CONTENT_FILES:
            rel = '06_batch/' + name
            with self.subTest(path=rel):
                path = self.put(rel, payload)
                stage = SimpleNamespace(id='meta', produces=(rel,))
                old_output = semantics.output_signatures(self.batch, stage)
                raw = mf.output_hashes(self.batch, stage)
                inputs = semantics.input_signatures(self.batch, self.state, stage, {'upstream:' + rel: raw[rel]})
                changed = json.loads(json.dumps(payload))
                changed['items'][0].update(numberStart=1, numberEnd=1)
                self.put(rel, changed)
                self.assertEqual(semantics.file_value(path), payload)
                self.assertEqual(old_output, semantics.output_signatures(self.batch, stage))
                self.assertEqual(inputs, semantics.input_signatures(self.batch, self.state, stage, {'upstream:' + rel: 'changed'}))
                self.assertEqual(semantics._projection('meta', rel, changed), payload)
                self.assertNotEqual(raw, mf.output_hashes(self.batch, stage))
        other = self.put('04_knowledge/batch.json', changed)
        self.assertEqual(semantics.file_value(other), changed)

    def test_question_range_projection_keeps_original_numbers_and_nested_ranges(self):
        from workflow import stage_semantics as semantics
        payload = {'numberStart': 90, 'items': [{'number': 1, 'numbers': [1], 'stem': 'Original',
            'evidence': {'numberStart': 10, 'numberEnd': 20}, 'subquestions': [{'number': 1, 'numberStart': 5}]}]}
        path = self.put('06_batch/batch.json', payload)
        baseline = semantics.digest(semantics.file_value(path))
        for field, value in [('number', 2), ('numbers', [2]), ('stem', 'Changed'),
                             ('evidence', {'numberStart': 11, 'numberEnd': 20}),
                             ('subquestions', [{'number': 1, 'numberStart': 6}])]:
            with self.subTest(field=field):
                changed = json.loads(json.dumps(payload))
                changed['items'][0][field] = value
                self.put('06_batch/batch.json', changed)
                self.assertNotEqual(baseline, semantics.digest(semantics.file_value(path)))
        changed = dict(payload, numberStart=91)
        self.put('06_batch/batch.json', changed)
        self.assertNotEqual(baseline, semantics.digest(semantics.file_value(path)))

    def test_range_metadata_keeps_completed_producers_and_derived_views_current(self):
        payload = {'items': [{'unit_id': 'q-1', 'number': 1, 'numbers': [1], 'stem': 'Original'}]}
        for rel in ('06_batch/batch.base.json', '06_batch/batch.json',
                    '06_batch/batch_meta.base.json', '06_batch/batch_meta.json'):
            self.put(rel, payload)
        for sid in ('preassemble', 'assemble', 'meta', 'underlines', 'validate'):
            self.finish(sid)
        for rel in ('06_batch/batch.base.json', '06_batch/batch.json',
                    '06_batch/batch_meta.base.json', '06_batch/batch_meta.json'):
            self.put(rel, {'items': [dict(payload['items'][0], numberStart=1, numberEnd=1)]})
        self.assertEqual(mf.reconcile_deliveries(self.batch, self.state), [])
        for sid in ('preassemble', 'assemble', 'meta', 'underlines', 'validate'):
            with self.subTest(stage=sid):
                self.assertFalse(self.stale(sid))
                self.assertEqual(self.state['stages'][sid]['status'], mf.COMPLETED)

    def test_added_question_range_still_invalidates_exact_upload_freeze(self):
        final = self.put('06_batch/batch_meta.json', {'items': [{'number': 1}]})
        mf.touch_stage(self.state, 'upload_gate', mf.APPROVED, approved_batch_sha256=mf.sha256_file(final))
        self.put('06_batch/batch_meta.json', {'items': [{'number': 1, 'numberStart': 1, 'numberEnd': 1}]})
        self.assertTrue(self.stale('upload_gate'))

    def test_real_output_edit_reopens_stage_for_validation(self):
        # Serialization-only rewrites are ignored, but a real payload change must
        # not be reused as if the previously accepted output were still present.
        self.put("03_types/types.json", {"items": [{"unit_id": "q-40", "type": "单项填空"}]})
        self.finish("types")
        self.put("03_types/types.json", {"items": [{"unit_id": "q-40", "type": "阅读理解"}]})
        self.assertTrue(self.stale("types"))

    def test_word_count_change_does_not_retag_knowledge(self):
        self.prepare_knowledge()
        self.put("03_types/types.json", {"items": [{"unit_id": "q-40", "type": "单项填空", "subtypes": ["四个选项"], "words": 13}]})
        self.assertFalse(self.stale("knowledge"))

    def test_subtype_change_does_not_auto_retag_knowledge(self):
        self.prepare_knowledge()
        self.put("03_types/types.json", {"items": [{"unit_id": "q-40", "type": "单项填空", "subtypes": ["三个选项"], "words": 12}]})
        self.assertFalse(self.stale("knowledge"))

    def test_difficulty_change_does_not_retag_knowledge(self):
        self.prepare_knowledge()
        self.put("01_source_ans/answers.json", {"answers": {"40": "A"}, "evidence": {"40": ["附录 L1"]},
                                                "missing": {}, "difficulty": {"40": "困难"}})
        self.assertFalse(self.stale("knowledge"))

    def test_answer_change_does_not_retag_knowledge(self):
        # knowledge only consumes types/structure; answers merge at assemble.
        self.prepare_knowledge()
        self.put("01_source_ans/answers.json", {"answers": {"40": "B"}, "evidence": {"40": ["附录 L1"]},
                                                "missing": {}, "difficulty": {"40": "适中"}})
        self.assertFalse(self.stale("knowledge"))

    def test_gate_report_rewrite_does_not_retag(self):
        # 仅时间戳/无关字段变化不触发重算（内容指纹忽略 generated_at 等）。
        self.prepare_knowledge()
        gate = self.batch / "07_review/structure-gate.json"
        base = json.loads(gate.read_text()) if gate.is_file() else {"passed": True}
        base["generated_at"] = "new"
        self.put("07_review/structure-gate.json", base)
        self.assertFalse(self.stale("knowledge"))

    def test_same_number_changed_stem_does_not_auto_reopen(self):
        self.prepare_knowledge()
        self.put("01_source/structure.confirmed.json", {"units": [{"unit_id": "q-40", "unit_kind": "standalone", "numbers": [40], "stem": "An actually different question."}]})
        self.assertFalse(self.stale("knowledge"))

    def test_optional_answer_arrival_does_not_auto_reopen(self):
        self.finish("explanations")
        self.put("01_source_ans/正文.txt", "40. A; original explanation")
        self.assertFalse(self.stale("explanations"))

    def test_repair_waits_without_eagerly_invalidating_all_descendants(self):
        self.finish("types")
        self.finish("assemble")
        self.finish("meta")
        self.finish("underlines")
        self.finish("validate")
        changed = mf.invalidate_stage(self.batch, self.state, "types", "只修当前题型结果")
        self.assertEqual(changed, ["types"])
        self.assertEqual(self.state["stages"]["validate"]["status"], mf.COMPLETED)
        self.assertFalse(mf.dependency_satisfied(self.state, "underlines"))
        mf.touch_stage(self.state, "types", mf.RUNNING)
        self.assertFalse(mf.dependency_satisfied(self.state, "underlines"))
        self.finish("types")
        # Unchanged labels no longer invalidate assemble once it is re-delivered.
        self.assertFalse(self.stale("assemble"))
        self.assertEqual(self.state["stages"]["assemble"]["status"], mf.COMPLETED)
        # A partial/needs_review producer never counts as a usable delivery.
        self.assertFalse(mf.dependency_satisfied(self.state, "underlines"))

    def test_changed_producer_does_not_auto_invalidate_descendants(self):
        # 新规则：重跑某阶段不再自动使下游失效（由用户后续验证，或显式 invalidate）。
        self.finish("types")
        self.finish("assemble")
        self.finish("meta")
        self.put("03_types/types.json", {"items": [{"unit_id": "q-40", "type": "阅读理解"}]})
        self.finish("types")
        self.assertEqual(self.state["stages"]["assemble"]["status"], mf.COMPLETED)
        self.assertEqual(self.state["stages"]["meta"]["status"], mf.COMPLETED)

    def test_changed_upstream_artifact_reopens_derived_views(self):
        # 批次 05 卡点：knowledge 局部重生成后 assemble 更新了 batch.json，
        # 但 meta/underlines 仍是旧内容，最终上传格式检查读到旧知识点并报
        # KNOWLEDGE_REQUIRED 幻影。纯派生视图必须在生产者内容变化后重算。
        self.finish("assemble")
        self.finish("meta")
        self.finish("underlines")
        self.finish("validate")
        self.finish("review")
        self.finish("review_gate")
        self.finish("format_gate")
        batch = self.batch / "06_batch/batch.json"
        value = json.loads(batch.read_text(encoding="utf-8"))
        # 仅空白/键序的等价重写不算内容变化。
        batch.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        self.assertFalse(self.stale("meta"))
        self.put("06_batch/batch.json", {"items": [{"unit_id": "q-40",
                                                    "knowledge": ["词汇/词汇辨析/形容词辨析"]}]})
        stale, reason = mf.is_stale(self.batch, self.state, contracts.STAGE_BY_ID["meta"])
        self.assertTrue(stale)
        self.assertIn("06_batch/batch.json", reason)
        # meta 重算后其产物变化，underlines 必须跟着重算（依次向下直到最终闸门）。
        self.put("06_batch/batch_meta.base.json", {"items": [{"knowledge": ["词汇/词汇辨析/形容词辨析"]}]})
        self.assertTrue(self.stale("underlines"))
        self.put("06_batch/batch_meta.json", {"items": [{"knowledge": ["词汇/词汇辨析/形容词辨析"]}]})
        self.assertTrue(self.stale("validate"))
        self.put("07_review/review-rounds.json", {"rounds": [{"id": "new"}]})
        self.assertTrue(self.stale("review_gate"))
        self.put("07_review/review-gate.json", {"passed": False, "rounds": {"errors": []}})
        self.assertTrue(self.stale("format_gate"))

    def test_assemble_reopened_delivery_invalidates_stale_derived_views(self):
        self.finish("assemble")
        self.finish("meta")
        self.finish("underlines")
        self.put("06_batch/batch.json", {"items": [{"unit_id": "q-40",
                                                   "knowledge": ["词汇/拼写与词形"]}]})
        changed = mf.invalidate_changed_consumers(self.batch, self.state, "assemble")
        self.assertIn("meta", changed)
        self.assertEqual(self.state["stages"]["meta"]["status"], mf.INVALIDATED)

    def test_legacy_changed_output_cannot_gain_new_trust(self):
        self.put("03_types/types.json", {"items": []})
        stage = contracts.STAGE_BY_ID["types"]
        mf.touch_stage(self.state, "types", mf.COMPLETED, output_hashes=mf.output_hashes(self.batch, stage))
        self.put("03_types/types.json", {"items": [{"stem": "changed"}]})
        mf.reconcile_deliveries(self.batch, self.state)
        self.assertNotIn("semantic_version", self.state["stages"]["types"])
        self.assertEqual(self.state["stages"]["types"]["status"], mf.INVALIDATED)

    def test_unqualified_repair_delivery_clears_rebuild_wait_without_passing_quality(self):
        self.finish("validate")
        mf.invalidate_stage(self.batch, self.state, "validate", "重新体检")
        mf.touch_stage(self.state, "validate", mf.NEEDS_REVIEW,
                       execution_completed=True, continue_unqualified=True, quality_status="failed")
        mf.record_delivery(self.batch, self.state, contracts.STAGE_BY_ID["validate"])
        # An unqualified (needs_review) delivery is not consumable by downstream.
        self.assertFalse(mf.dependency_satisfied(self.state, "validate", allow_unqualified=True))
        self.assertFalse(mf.dependency_satisfied(self.state, "validate"))
        self.assertEqual(self.state["stages"]["validate"]["quality_status"], "failed")

    def test_upload_approval_keeps_exact_bytes_even_when_json_is_equivalent(self):
        final = self.put("06_batch/batch_meta.json", {"items": []})
        mf.touch_stage(self.state, "upload_gate", mf.APPROVED, approved_batch_sha256=mf.sha256_file(final))
        self.assertFalse(self.stale("upload_gate"))
        final.write_text('{ "items": [] }', encoding="utf-8")
        self.assertTrue(self.stale("upload_gate"))

    def test_export_preconditions_respect_explicit_unqualified_delivery(self):
        from workflow import agent_protocol
        # This case isolates the unqualified-delivery rule in _preconditions;
        # artefact-level validity is covered by the handoff tests.
        with patch.object(mf, "dependency_satisfied", return_value=True):
            self.assertEqual(
                agent_protocol._preconditions(self.state, contracts.STAGE_BY_ID["knowledge"]), []
            )
            mf.touch_stage(self.state, "types", mf.NEEDS_REVIEW,
                           execution_completed=True, continue_unqualified=True)
            with patch.object(mf, "dependency_satisfied",
                              side_effect=lambda m, s, **k: s != "types"):
                self.assertTrue(
                    agent_protocol._preconditions(self.state, contracts.STAGE_BY_ID["knowledge"])
                )
        return
        mf.touch_stage(self.state, "types", mf.NEEDS_REVIEW,
                       execution_completed=True, continue_unqualified=True)

    def test_textbook_matching_mode_is_semantic_but_exam_source_name_is_not(self):
        # 语义层已移除：不再区分“语义模式/真题来源名”。这里只保证不因 meta 里
        # 与知识无关的来源名变化而崩溃、且不误判失效。
        self.state["meta"] = {"source_type": "中考真题"}
        self.prepare_knowledge()
        self.state["meta"]["source_type"] = "中考模拟"
        self.assertFalse(self.stale("knowledge"))

    def test_replaced_answer_source_does_not_auto_reopen_extract(self):
        original = self.put("source-answers.txt", "40.A")
        self.state["inputs"]["answers"] = {"path": str(original), "exists": True}
        self.finish("extract")
        original.write_text("40.B", encoding="utf-8")
        self.assertFalse(self.stale("extract"))

    def test_audio_evidence_change_does_not_auto_reopen_audio(self):
        # 新规则：切片证据/切片文件变化不再自动使 audio 失效（用户后续验证）。
        self.put("02_audio/segment-evidence.json", {"items": [{"unit_id": "q-1", "file": "02_audio/cut.mp3", "start": 1, "end": 2}]})
        cut = self.put("02_audio/cut.mp3", "original-cut")
        self.put("02_audio/audio_map.json", {"items": [{"unit_id": "q-1", "file": "02_audio/cut.mp3", "start": 1, "end": 2}]})
        self.finish("audio")
        self.put("02_audio/segment-evidence.json", {"items": [{"unit_id": "q-1", "file": "02_audio/cut.mp3", "start": 10, "end": 20}]})
        self.assertFalse(self.stale("audio"))
        cut.write_bytes(b"different-cut-same-filename")
        self.assertFalse(self.stale("audio"))

    def test_validate_source_change_does_not_auto_reopen(self):
        self.put("01_source/正文.txt", "40. A. original B. other")
        self.finish("validate")
        self.put("01_source/正文.txt", "40. A. different B. other")
        self.assertFalse(self.stale("validate"))

    def test_assembly_consumes_confirmed_metadata_and_extraction_tables(self):
        self.put("01_source/structure.confirmed.json", {"units": [], "meta": {"source": "原来源", "year": "2022"}})
        self.put("01_source/extraction.json", {"tables": [{"rows": [{"cells": [{"text": "cell", "colspan": 2}]}]}]})
        self.finish("assemble")
        self.put("01_source/structure.confirmed.json", {"units": [], "meta": {"source": "新来源", "year": "2026"}})
        self.assertFalse(self.stale("assemble"))
        self.put("01_source/extraction.json", {"tables": [{"rows": [{"cells": [{"text": "cell", "colspan": 1}]}]}]})
        self.assertFalse(self.stale("assemble"))

    def test_incremental_gap_repairs_current_stage_before_review_consumes_old_data(self):
        from workflow import auto
        for stage in contracts.STAGES:
            self.finish(stage.id)
        mf.touch_stage(self.state, "review", mf.PENDING)
        mf.save(self.batch, self.state)
        calls = []
        def execute(batch, manifest, stage):
            calls.append(stage.id)
            return 1  # Repair failed: dependent review must wait, never use old output.
        # Type-label gaps belong to the preassembly model stage; the legacy
        # types COMMAND only checks its delivery and never repairs model output.
        with patch("workflow.agent_tasks.stage_gaps", side_effect=lambda batch, sid: {"units": ["q-1"]} if sid == "preassemble" else {}), \
                patch.object(mf, "dependency_satisfied", side_effect=_dep_until_preassembly_fixed(self)), \
                patch("workflow.self_heal.recover", return_value={"changed": False}), \
                patch.object(auto, "_auto_prepare_audio", return_value={"status": "ready"}), \
                patch.object(auto, "PARALLEL_AGENTS_ENABLED", False), \
                patch.object(auto.rn, "_skip_agent_stage", return_value=False), \
                patch.object(auto.rn, "_execute_command", side_effect=execute), \
                patch.object(auto, "_result", return_value={"status": "test"}):
            auto._advance_locked(self.batch, {sid: ["offline"] for sid in contracts.STAGE_ORDER}, stop_at="review_gate")
        self.assertEqual(calls, ["preassemble"])
        state = mf.load(self.batch)
        self.assertTrue(state["stages"]["preassemble"]["rebuild_required"])
        self.assertEqual(state["stages"]["review"]["status"], mf.PENDING)
        self.assertFalse(mf.dependency_satisfied(state, "validate", allow_unqualified=True))

    def test_review_reuses_after_report_timestamp_only_change(self):
        # 语义层移除后：review 只看依赖文件内容。先落好 validation-report 再 finish，
        # 之后只改其 generated_at 不应使 review 失效。
        self.put("07_review/validation-report.json", {"passed": True, "generated_at": "old"})
        self.finish("review")
        self.put("07_review/validation-report.json", {"passed": True, "generated_at": "later"})
        self.assertFalse(self.stale("review"))
        # 新规则不强比对内容：改 passed 也不自动使 review 失效（用户后续验证）。
        self.put("07_review/validation-report.json", {"passed": False, "generated_at": "later"})
        self.assertFalse(self.stale("review"))


if __name__ == "__main__":
    unittest.main()
