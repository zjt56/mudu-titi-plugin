"""Only isolated temporary batches; no model, manifest or remote execution."""
import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from workflow import batch_fill, question_draft


class BatchFillTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="batch-fill-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.source()

    def put(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def load(self, relative=batch_fill.BATCH):
        return json.loads((self.root / relative).read_text())

    def source(self, first="One?", group=False):
        text = f"1. {first} A. Yes B. No\n2. Two? A. Good B. Bad"
        lines = text.splitlines()
        if group:
            text = "Read this shared story.\n" + text
            specs = [([1, 2], "group", 1, 3)]
        else:
            specs = [([1], "standalone", 1, 1), ([2], "standalone", 2, 2)]
        lines = text.splitlines()
        units = [{"numbers": ns, "unit_kind": kind, "type": "阅读理解" if group else "单项填空",
                  "stem": "\n".join(lines[start - 1:end]),
                  "source_span": {"basis": "ai_classified", "start_line": start, "end_line": end}}
                 for ns, kind, start, end in specs]
        self.put("01_source/正文.txt", text)
        self.put("01_source/structure.confirmed.json", {"units": units, "meta": {"source": "真实原卷", "year": "2022"}})
        self.put("01_source/structure.json", {"sections": [], "groups": []})

    def snapshot(self):
        return {rel: ((self.root / rel).read_bytes(), (self.root / rel).stat().st_mtime_ns)
                for rel in (batch_fill.BATCH, batch_fill.BATCH_BASE, batch_fill.STATE)}

    def test_table_empty_items_equivalence_keeps_real_body_conflicts(self):
        batch_fill.initialize(self.root)
        payload = batch_fill._read(self.root, batch_fill.BATCH)
        state = batch_fill._read(self.root, batch_fill.STATE)
        row = payload["items"][0]
        uid = row["unit_id"]
        row["materialBlocks"] = [{"type": "table", "rows": [{"cells": [
            {"text": "", "items": [], "colspan": 2},
            {"text": "Original", "items": [{"text": "Nested", "items": []}]}]}]}]
        state["managed_bodies"] = {uid: batch_fill._body(row)}
        self.put(batch_fill.STATE, batch_fill._seal(state))
        row["materialBlocks"][0]["rows"][0]["cells"][0].pop("items")
        row["materialBlocks"][0]["rows"][0]["cells"][1]["items"][0].pop("items")
        self.put(batch_fill.BATCH, payload)
        self.assertTrue(batch_fill.is_filling(self.root))
        unchanged = copy.deepcopy(payload)
        for key, value in (("text", "Changed"), ("colspan", 1),
                           ("items", [{"text": "New content"}])):
            with self.subTest(key=key):
                candidate = copy.deepcopy(unchanged)
                candidate["items"][0]["materialBlocks"][0]["rows"][0]["cells"][0][key] = value
                self.put(batch_fill.BATCH, candidate)
                with self.assertRaisesRegex(ValueError, "正文 conflict"):
                    batch_fill.is_filling(self.root)
        self.put(batch_fill.BATCH, unchanged)
        self.assertTrue(batch_fill.is_filling(self.root))

    def test_early_real_payload_without_label_artifacts_and_empty_unknowns(self):
        result = batch_fill.initialize(self.root)
        self.assertEqual(result["status"], "initialized")
        self.assertTrue(batch_fill.is_filling(self.root))
        row = self.load()["items"][0]
        self.assertEqual(row["stem"], "One?")
        self.assertEqual(row["options"], ["A. Yes", "B. No"])
        for key in ("answer", "question_type", "explanation", "articleSummary", "source_type"):
            self.assertEqual(row[key], "", key)
        # Difficulty stays absent until a stage judges it: a placeholder "" would
        # collide with the filter value written by the metadata step.
        self.assertNotIn("difficulty", row)
        self.assertEqual(row["source"], "真实原卷")
        self.assertEqual(row["year"], 2022)
        self.assertEqual(row["knowledge"], [])
        self.assertEqual(self.load(), self.load(batch_fill.BATCH_BASE))
        self.assertNotIn("mode", self.load())
        self.assertNotIn("release_ready", self.load())
        self.assertFalse((self.root / "workflow/manifest.json").exists())

    def test_stages_merge_into_same_objects_preserving_unrelated_content(self):
        batch_fill.initialize(self.root)
        before_base = (self.root / batch_fill.BATCH_BASE).read_bytes()
        original = self.load()["items"][0]["stem"]
        batch_fill.sync(self.root, "answers", {"answers": {"1": "A"}, "difficulty": {"1": "容易"}})
        batch_fill.sync(self.root, "types", {"items": [{"unit_id": "q-1", "type": "单项填空", "subtypes": ["语法"], "words": 3}]})
        batch_fill.sync(self.root, "explanations", {"items": [{"unit_id": "q-1", "explanation": "原解析", "articleSummary": "无"}]})
        batch_fill.sync(self.root, "knowledge", {"items": [{"unit_id": "q-1", "knowledge": [], "knowledgeIds": [], "stem": "evil overwrite", "answer": "B", "explanation": "ignored"}]})
        row = self.load()["items"][0]
        self.assertEqual(row["stem"], original)
        self.assertEqual(row["answer"], "A")
        self.assertEqual(row["difficulty"], "容易")
        self.assertEqual(row["question_type"], "单项填空")
        self.assertEqual(row["wordCount"], 3)
        self.assertEqual(row["explanation"], "原解析")
        self.assertEqual(row["articleSummary"], "无")
        self.assertEqual((self.root / batch_fill.BATCH_BASE).read_bytes(), before_base)
        state = self.load(batch_fill.STATE)
        self.assertEqual(set(state["units"]["q-1"]["stages"]), {"answers", "types", "explanations", "knowledge"})
        self.assertEqual(state["payload_sha256"], question_draft._digest(self.load()))

    def test_file_payload_metadata_and_local_audio(self):
        batch_fill.initialize(self.root)
        self.put(batch_fill.ARTIFACTS["answers"], {"answers": {"2": "B"}})
        batch_fill.sync(self.root, "answers")
        batch_fill.sync(self.root, "metadata", {"meta": {"region": "安徽省", "year": "2021", "paper_title": "真题卷"}})
        self.put("02_audio/clips/one.mp3", "bytes")
        batch_fill.sync(self.root, "audio", {"items": [{"unit_id": "q-1", "file": "02_audio/clips/one.mp3", "start": 1, "end": 3}]})
        rows = self.load()["items"]
        self.assertEqual(rows[1]["answer"], "B")
        self.assertEqual(rows[0]["filterMetadata"]["region"], "安徽省")
        self.assertEqual(self.load()["name"], "真题卷")
        self.assertEqual(rows[1]["filterMetadata"]["year"], 2021)
        self.assertEqual(rows[0]["attachments"][0]["url"], "02_audio/clips/one.mp3")
        self.assertTrue(batch_fill.is_filling(self.root))

    def test_unchanged_initialize_and_sync_do_not_rewrite_or_erase_labels(self):
        batch_fill.initialize(self.root)
        artifact = {"answers": {"1": "A"}}
        batch_fill.sync(self.root, "answers", artifact)
        saved = self.snapshot()
        self.assertEqual(batch_fill.initialize(self.root)["status"], "unchanged")
        self.assertEqual(batch_fill.sync(self.root, "answers", artifact)["status"], "unchanged")
        self.assertEqual(self.snapshot(), saved)

    def test_format_only_refresh_preserves_fill_for_parent_and_children(self):
        for group in (False, True):
            with self.subTest(group=group), tempfile.TemporaryDirectory() as tmp:
                self.root = Path(tmp).resolve()
                self.source("<u>　　</u> One?", group=group)
                uid = "group-1-2" if group else "q-1"
                with patch.object(question_draft, '_skeleton_text', side_effect=lambda text: text):
                    batch_fill.initialize(self.root)
                    batch_fill.sync(self.root, "answers", {"answers": {"1": "A", "2": "B"}})
                    batch_fill.sync(self.root, "types", {"items": [{"unit_id": uid, "type": "阅读理解" if group else "单项填空",
                                                                  "subtypes": ["语法"]}]})
                    batch_fill.sync(self.root, "knowledge", {"items": [{"unit_id": uid, "knowledge": ["已填知识"]}]})
                    explanation = {"unit_id": uid, "explanation": "已填解析"}
                    if group:
                        explanation["subquestions"] = [{"number": 1, "explanation": "子题解析"}]
                    batch_fill.sync(self.root, "explanations", {"items": [explanation]})
                before = self.load()["items"][0]
                old_stages = self.load(batch_fill.STATE)["units"][uid]["stages"]
                result = batch_fill.initialize(self.root)
                row = self.load()["items"][0]
                self.assertIn(uid, result["changed_units"])
                self.assertEqual(row["question_type"], before["question_type"])
                self.assertEqual(row["knowledge"], before["knowledge"])
                self.assertEqual(row["explanation"], before["explanation"])
                self.assertEqual(row["filterMetadata"], before["filterMetadata"])
                target = row["subquestions"][0] if group else row
                self.assertEqual(target["answer"], "A")
                self.assertIn("______ One?", target["stem"])
                if group:
                    self.assertEqual(target["explanation"], "子题解析")
                    self.assertEqual(row["subquestions"][1]["answer"], "B")
                self.assertEqual(self.load(batch_fill.STATE)["units"][uid]["stages"], old_stages)
                self.source("Changed source?", group=group)
                batch_fill.initialize(self.root)
                changed = self.load()["items"][0]
                self.assertEqual(changed["question_type"], "")
                self.assertEqual(changed["knowledge"], [])
                target = changed["subquestions"][0] if group else changed
                self.assertEqual(target["answer"], "")
                self.assertEqual(self.load(batch_fill.STATE)["units"][uid]["stages"], {})

    def test_changed_source_media_does_not_preserve_fill(self):
        batch_fill.initialize(self.root)
        batch_fill.sync(self.root, "answers", {"answers": {"1": "A"}})
        draft = question_draft.build(self.root)
        draft["items"][0]["stem"] = "Formatted One?"
        draft["source_bindings"]["q-1"]["media_hashes"] = {"01_source/media/new.png": "changed"}
        with patch.object(question_draft, "ensure", return_value=draft):
            batch_fill.initialize(self.root)
        self.assertEqual(self.load()["items"][0]["answer"], "")

    def test_one_source_change_resets_just_that_unit(self):
        batch_fill.initialize(self.root)
        batch_fill.sync(self.root, "answers", {"answers": {"1": "A", "2": "B"}})
        batch_fill.sync(self.root, "explanations", {"items": [{"unit_id": "q-2", "explanation": "保留"}]})
        self.source("Changed?")
        result = batch_fill.initialize(self.root)
        self.assertEqual(result["changed_units"], ["q-1"])
        first, second = self.load()["items"]
        self.assertEqual(first["stem"], "Changed?")
        self.assertEqual(first["answer"], "")
        self.assertEqual(second["answer"], "B")
        self.assertEqual(second["explanation"], "保留")
        self.assertEqual(self.load(batch_fill.STATE)["units"]["q-1"]["stages"], {})

    def test_group_children_shared_material_and_group_change(self):
        self.source(group=True)
        batch_fill.initialize(self.root)
        uid = "group-1-2"
        batch_fill.sync(self.root, "answers", {"answers": {"1": "A", "2": "B"}})
        batch_fill.sync(self.root, "explanations", {"items": [{"unit_id": uid, "articleSummary": "整组大意",
            "subquestions": [{"number": 2, "explanation": "子题原解析"}]}]})
        row = self.load()["items"][0]
        self.assertEqual(row["answer"], "")
        self.assertEqual([c["answer"] for c in row["subquestions"]], ["A", "B"])
        self.assertEqual(row["subquestions"][1]["explanation"], "子题原解析")
        self.source("Changed?", group=True)
        batch_fill.initialize(self.root)
        row = self.load()["items"][0]
        self.assertEqual(row["articleSummary"], "")
        self.assertEqual([c["answer"] for c in row["subquestions"]], ["", ""])
        self.assertIn("shared story", row["material"])

    def test_regrouping_resets_corresponding_objects(self):
        batch_fill.initialize(self.root)
        batch_fill.sync(self.root, "answers", {"answers": {"1": "A", "2": "B"}})
        self.source(group=True)
        result = batch_fill.initialize(self.root)
        self.assertEqual(result["removed_units"], ["q-1", "q-2"])
        self.assertEqual(result["changed_units"], ["group-1-2"])
        self.assertTrue(all(c["answer"] == "" for c in self.load()["items"][0]["subquestions"]))

    def test_unknown_fields_and_external_labels_survive_refresh(self):
        batch_fill.initialize(self.root)
        data = self.load()
        data["customBatch"] = {"keep": True}
        data["items"][0]["customQuestion"] = "manual"
        data["items"][1]["knowledge"] = ["existing external tag"]
        self.put(batch_fill.BATCH, data)
        batch_fill.initialize(self.root)
        self.source("Changed?")
        batch_fill.initialize(self.root)
        self.assertEqual(self.load()["customBatch"], {"keep": True})
        self.assertEqual(self.load()["items"][0]["customQuestion"], "manual")
        self.assertEqual(self.load()["items"][1]["knowledge"], ["existing external tag"])

    def test_legacy_batch_is_untouched_with_source_only_base_delivery(self):
        self.put(batch_fill.BATCH, {"items": [{"old": True}]})
        path = self.root / batch_fill.BATCH
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        self.assertEqual(batch_fill.initialize(self.root)["status"], "untouched")
        self.assertEqual(batch_fill.sync(self.root, "types", {"items": []})["status"], "untouched")
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
        self.assertFalse(batch_fill.is_filling(self.root))
        self.assertFalse((self.root / batch_fill.STATE).exists())
        self.assertEqual(self.load(batch_fill.BATCH_BASE)["items"][0]["stem"], "One?")
        self.assertEqual(self.load(batch_fill.BATCH_BASE)["items"][0]["answer"], "")

    def test_manually_changed_body_refuses_initialize_and_sync(self):
        batch_fill.initialize(self.root)
        data = self.load()
        data["items"][0]["stem"] = "human authored words"
        self.put(batch_fill.BATCH, data)
        saved = (self.root / batch_fill.BATCH).read_bytes()
        for action in (lambda: batch_fill.initialize(self.root), lambda: batch_fill.sync(self.root, "answers", {"answers": {"1": "A"}})):
            with self.assertRaisesRegex(ValueError, "conflict"):
                action()
        self.assertEqual((self.root / batch_fill.BATCH).read_bytes(), saved)

    def test_tampered_state_base_or_identity_is_not_trusted(self):
        batch_fill.initialize(self.root)
        saved = self.snapshot()
        state = self.load(batch_fill.STATE)
        state["units"]["q-1"]["base"]["stem"] = "forged"
        self.put(batch_fill.STATE, state)
        with self.assertRaisesRegex(ValueError, "integrity"):
            batch_fill.initialize(self.root)
        (self.root / batch_fill.STATE).write_bytes(saved[batch_fill.STATE][0])
        base = self.load(batch_fill.BATCH_BASE)
        base["items"][0]["stem"] = "forged"
        self.put(batch_fill.BATCH_BASE, base)
        with self.assertRaisesRegex(ValueError, "integrity"):
            batch_fill.is_filling(self.root)
        self.assertEqual((self.root / batch_fill.BATCH).read_bytes(), saved[batch_fill.BATCH][0])

    def test_unsafe_paths_symlink_batch_and_root_are_rejected(self):
        batch_fill.initialize(self.root)
        saved = (self.root / batch_fill.BATCH).read_bytes()
        for file in ("../elsewhere.mp3", "/etc/passwd", "02_audio/../clip.mp3", "02_audio\\clip.mp3"):
            with self.assertRaises(ValueError):
                batch_fill.sync(self.root, "audio", {"items": [{"unit_id": "q-1", "file": file}]})
        self.assertEqual((self.root / batch_fill.BATCH).read_bytes(), saved)
        (self.root / batch_fill.BATCH).unlink()
        (self.root / batch_fill.BATCH).symlink_to(self.root / "01_source/正文.txt")
        with self.assertRaises(ValueError):
            batch_fill.initialize(self.root)
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            batch_fill.initialize(alias)

    def test_unproven_changed_source_preserves_existing_body(self):
        batch_fill.initialize(self.root)
        saved = (self.root / batch_fill.BATCH).read_bytes()
        self.put("01_source/正文.txt", "1. Unconfirmed new source. A. Yes B. No")
        with self.assertRaisesRegex(ValueError, "proven"):
            batch_fill.initialize(self.root)
        self.assertEqual((self.root / batch_fill.BATCH).read_bytes(), saved)

    def test_unknown_duplicate_uid_child_and_number_mapping_are_rejected(self):
        batch_fill.initialize(self.root)
        saved = (self.root / batch_fill.BATCH).read_bytes()
        artifacts = [
            {"items": [{"unit_id": "missing", "answer": "A"}]},
            {"items": [{"unit_id": "q-1", "answer": "A"}, {"unit_id": "q-1", "answer": "B"}]},
            {"items": [{"unit_id": "q-1", "number": 2, "answer": "A"}]},
            {"items": [{"unit_id": "q-1", "subquestions": [{"number": 2, "answer": "A"}]}]},
            {"answers": {"1": "A", "01": "B"}},
        ]
        for artifact in artifacts:
            with self.assertRaises(ValueError):
                batch_fill.sync(self.root, "answers", artifact)
        self.assertEqual((self.root / batch_fill.BATCH).read_bytes(), saved)

    def test_duplicate_question_numbers_require_explicit_uid(self):
        draft = question_draft.ensure(self.root)
        second = draft["items"][1]
        second["number"] = 1
        second["numbers"] = [1]
        with patch.object(question_draft, "ensure", return_value=copy.deepcopy(draft)):
            batch_fill.initialize(self.root)
            with self.assertRaisesRegex(ValueError, "unique uid"):
                batch_fill.sync(self.root, "answers", {"answers": {"1": "wrong shared A"}})
            batch_fill.sync(self.root, "answers", {"items": [{"unit_id": "q-2", "number": 1, "answer": "B"}]})
        self.assertEqual([r["answer"] for r in self.load()["items"]], ["", "B"])

    def test_finalization_after_validated_normalization_records_final_hash(self):
        batch_fill.initialize(self.root)
        data = self.load()
        data["items"][0]["options"] = ["A. validated normalization"]
        self.put(batch_fill.BATCH, data)
        self.assertEqual(batch_fill.mark_finalized(self.root)["status"], "finalized")
        self.assertFalse(batch_fill.is_filling(self.root))
        saved = self.snapshot()
        self.assertEqual(batch_fill.initialize(self.root)["status"], "finalized")
        self.assertEqual(self.snapshot(), saved)
        self.assertEqual(self.load(batch_fill.STATE)["payload_sha256"], question_draft._digest(data))
        self.assertEqual(batch_fill.sync(self.root, "answers", {"answers": {"1": "A"}})["status"], "filled")
        self.assertTrue(batch_fill.is_filling(self.root))
        self.assertEqual(self.load()["items"][0]["options"], ["A. validated normalization"])
        self.assertEqual(self.load()["items"][0]["answer"], "A")
        self.assertEqual(batch_fill.initialize(self.root)["status"], "unchanged")

    def test_repeated_final_import_can_update_accepted_normalization(self):
        batch_fill.initialize(self.root)
        batch_fill.mark_finalized(self.root)
        data = self.load()
        data["items"][0]["options"] = ["A. second accepted normalization"]
        self.put(batch_fill.BATCH, data)
        batch_fill.mark_finalized(self.root)
        self.assertFalse(batch_fill.is_filling(self.root))
        self.assertEqual(self.load(batch_fill.STATE)["payload_sha256"], question_draft._digest(data))
        saved = self.snapshot()
        batch_fill.mark_finalized(self.root)
        self.assertEqual(self.snapshot(), saved)

    def test_finalize_rejects_changed_source_and_does_not_mark_completion(self):
        batch_fill.initialize(self.root)
        self.source("Changed?")
        with self.assertRaisesRegex(ValueError, "Source changed"):
            batch_fill.mark_finalized(self.root)
        self.assertEqual(self.load(batch_fill.STATE)["mode"], "filling")
        self.assertFalse((self.root / "workflow/manifest.json").exists())

    def test_finalize_reports_changed_unit_and_body_field_without_writing_state(self):
        batch_fill.initialize(self.root)
        draft = question_draft.ensure(self.root)
        draft["items"][0]["stem"] = "New proven shared section rubric"
        saved = self.snapshot()
        with patch.object(question_draft, "ensure", return_value=draft):
            with self.assertRaisesRegex(ValueError, "q-1; changed fields: stem"):
                batch_fill.mark_finalized(self.root)
        self.assertEqual(self.snapshot(), saved)
        self.assertEqual(self.load(batch_fill.STATE)["mode"], "filling")

    def test_finalize_reports_media_and_source_binding_changes(self):
        batch_fill.initialize(self.root)
        for field, value in (("source_text_sha256", "a" * 64),
                             ("media_hashes", {"01_source/media/new.png": "b" * 64})):
            with self.subTest(field=field):
                draft = question_draft.ensure(self.root)
                draft["source_bindings"]["q-1"][field] = value
                saved = self.snapshot()
                with patch.object(question_draft, "ensure", return_value=draft):
                    with self.assertRaisesRegex(ValueError, "q-1; changed fields: " + field):
                        batch_fill.mark_finalized(self.root)
                self.assertEqual(self.snapshot(), saved)

    def test_source_media_change_only_resets_its_owner(self):
        self.source("One? [IMG first.png]")
        self.put("01_source/media/first.png", "original image")
        batch_fill.initialize(self.root)
        batch_fill.sync(self.root, "answers", {"answers": {"1": "A", "2": "B"}})
        self.put("01_source/media/first.png", "changed image")
        result = batch_fill.initialize(self.root)
        self.assertEqual(result["changed_units"], ["q-1"])
        self.assertEqual([r["answer"] for r in self.load()["items"]], ["", "B"])

    def test_media_url_normalization_does_not_poison_fill_guard(self):
        # initialize 会把附件 URL 规范成部署形式；这一步不能把刚写入的正文判成
        # 人工篡改，否则答案/题型一启动就失败、监督器无限重试同一冲突。
        self.source("One? [IMG first.png]")
        self.put("01_source/media/first.png", "original image")
        batch_fill.initialize(self.root)
        self.assertEqual(batch_fill.initialize(self.root)["status"], "refreshed")
        self.assertEqual(batch_fill.initialize(self.root)["status"], "unchanged")
        self.assertEqual(batch_fill.sync(self.root, "answers", {"answers": {"1": "A"}})["status"], "filled")
        row = self.load()["items"][0]
        self.assertEqual(row["answer"], "A")
        self.assertEqual(row["attachments"][0]["url"],
                         "/source-media/" + self.root.name + "/first.png")

    def test_deployed_attachment_url_is_equivalent_but_real_body_edit_still_conflicts(self):
        self.source("One? [IMG first.png]")
        self.put("01_source/media/first.png", "original image")
        batch_fill.initialize(self.root)
        data = self.load()
        data["items"][0]["attachments"][0]["url"] = "/source-media/batch/first.png"
        self.put(batch_fill.BATCH, data)
        self.assertTrue(batch_fill.is_filling(self.root))
        data["items"][0]["stem"] = "human authored words"
        self.put(batch_fill.BATCH, data)
        with self.assertRaisesRegex(ValueError, "conflict"):
            batch_fill.initialize(self.root)

    def test_concurrent_body_edit_during_draft_build_is_not_overwritten(self):
        batch_fill.initialize(self.root)
        ensure = question_draft.ensure
        edited = []
        def racing_ensure(root):
            result = ensure(root)
            payload = self.load()
            payload["items"][0]["stem"] = "edited during source build"
            self.put(batch_fill.BATCH, payload)
            edited.append((self.root / batch_fill.BATCH).read_bytes())
            return result
        with patch.object(question_draft, "ensure", side_effect=racing_ensure):
            with self.assertRaisesRegex(ValueError, "changed during"):
                batch_fill.initialize(self.root)
        self.assertEqual((self.root / batch_fill.BATCH).read_bytes(), edited[0])

    def test_legacy_null_batch_and_invalid_null_state(self):
        self.put(batch_fill.BATCH, "null\n")
        self.assertEqual(batch_fill.initialize(self.root)["status"], "untouched")
        self.assertEqual((self.root / batch_fill.BATCH).read_text(), "null\n")
        self.put(batch_fill.STATE, "null\n")
        with self.assertRaisesRegex(ValueError, "null batch fill state"):
            batch_fill.initialize(self.root)

    def test_same_semantic_json_is_not_reformatted(self):
        batch_fill.initialize(self.root)
        self.put(batch_fill.BATCH, self.load())
        before = self.snapshot()
        self.assertEqual(batch_fill.initialize(self.root)["status"], "unchanged")
        self.assertEqual(self.snapshot(), before)

    def test_knowledge_ids_and_sidecar_symlinks(self):
        batch_fill.initialize(self.root)
        batch_fill.sync(self.root, "knowledge", {"items": [{"unit_id": "q-1", "knowledge": ["accepted catalog leaf"], "knowledgeIds": ["accepted-id"]}]})
        row = self.load()["items"][0]
        self.assertEqual(row["knowledgeIds"], ["accepted-id"])
        self.assertEqual(row["knowledge_ids"], ["accepted-id"])
        state = self.root / batch_fill.STATE
        state.unlink()
        state.symlink_to(self.root / "01_source/正文.txt")
        with self.assertRaises(ValueError):
            batch_fill.is_filling(self.root)

    def test_answers_outside_batch_are_explicitly_ignored(self):
        batch_fill.initialize(self.root)
        result = batch_fill.sync(self.root, "answers", {"answers": {"1": "A", "40": "unowned"}, "difficulty": {"99": "困难"}})
        self.assertEqual([row["answer"] for row in self.load()["items"]], ["A", ""])
        self.assertEqual({(x["field"], x["number"]) for x in result["ignored"]}, {("answers", "40"), ("difficulty", "99")})
        self.assertTrue(result["notes"])

    def test_metadata_defaults_to_identified_fields_and_confirmed_fallback(self):
        batch_fill.initialize(self.root)
        self.put(batch_fill.ARTIFACTS["metadata"], {"region": "安徽省", "source": "", "year": 2023})
        batch_fill.sync(self.root, "metadata")
        row = self.load()["items"][0]
        self.assertEqual(row["source"], "真实原卷")
        self.assertEqual(row["year"], 2023)
        self.assertEqual(row["filterMetadata"]["region"], "安徽省")

    def test_per_item_metadata_reopens_finalized_without_erasing_labels(self):
        batch_fill.initialize(self.root)
        batch_fill.sync(self.root, "answers", {"answers": {"1": "A"}})
        batch_fill.sync(self.root, "explanations", {"items": [{"unit_id": "q-1", "explanation": "原解析", "articleSummary": "无"}]})
        batch_fill.sync(self.root, "knowledge", {"items": [{"unit_id": "q-1", "knowledge": ["accepted leaf"], "knowledgeIds": ["accepted-id"]}]})
        data = self.load()
        data["items"][0]["filterMetadata"]["custom"] = {"old": 1}
        self.put(batch_fill.BATCH, data)
        batch_fill.mark_finalized(self.root)
        before = self.load()
        base_bytes = (self.root / batch_fill.BATCH_BASE).read_bytes()
        row = copy.deepcopy(before["items"][0])
        row.update(wordCount=25, region="安徽省", categories=["accepted category"],
                   answer="ignored answer", explanation="ignored explanation", knowledge=["ignored tag"], knowledgeIds=["ignored-id"])
        row["filterMetadata"] = {"difficulty": "容易", "categories": ["accepted category"], "custom": {"new": 2}}
        artifact = {"items": [row]}
        result = batch_fill.sync(self.root, "metadata", artifact)
        self.assertEqual(result["units"], ["q-1"])
        self.assertTrue(batch_fill.is_filling(self.root))
        actual = self.load()["items"][0]
        for key in ("answer", "explanation", "articleSummary", "knowledge", "knowledgeIds", "knowledge_ids", "stem", "material", "options", "question_type"):
            self.assertEqual(actual[key], before["items"][0][key], key)
        self.assertEqual(actual["wordCount"], 25)
        self.assertEqual(actual["filterMetadata"]["region"], "安徽省")
        self.assertEqual(actual["difficulty"], "容易")
        self.assertEqual(actual["filterMetadata"]["difficulty"], "容易")
        self.assertEqual(actual["filterMetadata"]["custom"], {"old": 1})
        self.assertEqual(actual["filterMetadata"]["categories"], ["accepted category"])
        self.assertNotIn("categories", actual)
        self.assertEqual(actual["filterMetadata"]["subtypes"], before["items"][0]["filterMetadata"]["subtypes"])
        self.assertEqual(self.load()["items"][1], before["items"][1])
        self.assertEqual((self.root / batch_fill.BATCH_BASE).read_bytes(), base_bytes)
        saved = self.snapshot()
        self.assertEqual(batch_fill.sync(self.root, "metadata", artifact)["status"], "unchanged")
        self.assertEqual(self.snapshot(), saved)

    def test_per_item_metadata_unknown_duplicate_uid_and_body_change_rejected(self):
        batch_fill.initialize(self.root)
        batch_fill.mark_finalized(self.root)
        saved = self.snapshot()
        for rows in ([{"unit_id": "unknown", "wordCount": 20}],
                     [{"unit_id": "q-1", "wordCount": 20}, {"unit_id": "q-1", "wordCount": 30}],
                     [{"unit_id": "q-1", "stem": "changed wording", "wordCount": 20}],
                     [{"unit_id": "q-1", "options": [], "wordCount": 20}]):
            with self.assertRaises(ValueError):
                batch_fill.sync(self.root, "metadata", {"items": rows})
            self.assertEqual(self.snapshot(), saved)
        self.assertFalse(batch_fill.is_filling(self.root))

    def test_per_item_metadata_child_ownership_and_content_preservation(self):
        self.source(group=True)
        batch_fill.initialize(self.root)
        batch_fill.sync(self.root, "answers", {"answers": {"1": "A", "2": "B"}})
        before = self.load()
        artifact = {"items": [{"unit_id": "group-1-2", "wordCount": 30, "subquestions": [
            {"number": 2, "difficulty": "困难", "filterMetadata": {"categories": ["accepted child category"]},
             "answer": "ignored", "explanation": "ignored"}]}]}
        batch_fill.sync(self.root, "metadata", artifact)
        row = self.load()["items"][0]
        self.assertEqual(row["material"], before["items"][0]["material"])
        self.assertEqual(row["subquestions"][0], before["items"][0]["subquestions"][0])
        self.assertEqual(row["subquestions"][1]["answer"], "B")
        self.assertEqual(row["subquestions"][1]["explanation"], "")
        self.assertEqual(row["subquestions"][1]["difficulty"], "困难")
        self.assertEqual(row["subquestions"][1]["filterMetadata"]["categories"], ["accepted child category"])
        saved = self.snapshot()
        with self.assertRaises(ValueError):
            batch_fill.sync(self.root, "metadata", {"items": [{"unit_id": "group-1-2", "subquestions": [{"number": 99, "wordCount": 4}]}]})
        self.assertEqual(self.snapshot(), saved)

    def test_finalized_source_change_reopens_only_affected_units(self):
        batch_fill.initialize(self.root)
        batch_fill.sync(self.root, "answers", {"answers": {"1": "A", "2": "B"}})
        data = self.load()
        data["items"][1]["options"] = ["A. normalized other unit"]
        self.put(batch_fill.BATCH, data)
        batch_fill.mark_finalized(self.root)
        self.source("Changed?")
        result = batch_fill.initialize(self.root)
        self.assertEqual(result["changed_units"], ["q-1"])
        self.assertTrue(batch_fill.is_filling(self.root))
        one, two = self.load()["items"]
        self.assertEqual(one["answer"], "")
        self.assertEqual(two["answer"], "B")
        self.assertEqual(two["options"], ["A. normalized other unit"])
        self.assertEqual(batch_fill.initialize(self.root)["status"], "unchanged")

    def test_legacy_cli_delivers_base_without_adopting_batch(self):
        self.put(batch_fill.BATCH, {"items": [{"unknownLegacy": True}]})
        saved = (self.root / batch_fill.BATCH).read_bytes()
        with redirect_stdout(io.StringIO()) as stdout:
            code = batch_fill.main(["initialize", "--batch-dir", str(self.root)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "untouched")
        self.assertTrue((self.root / batch_fill.BATCH_BASE).is_file())
        self.assertFalse((self.root / batch_fill.STATE).exists())
        self.assertEqual((self.root / batch_fill.BATCH).read_bytes(), saved)

    def test_generated_metadata_matches_real_upload_allowlist(self):
        from workflow import upload_payload
        confirmed = self.load("01_source/structure.confirmed.json")
        confirmed["meta"].update(region="安徽省", paper_title="明确原卷标题")
        self.put("01_source/structure.confirmed.json", confirmed)
        batch_fill.initialize(self.root)
        self.assertEqual(self.load()["name"], "明确原卷标题")
        self.assertEqual(self.load(batch_fill.BATCH_BASE)["name"], "明确原卷标题")
        batch_fill.sync(self.root, "types", {"items": [{"unit_id": "q-1", "type": "单项填空", "words": 5, "contentComplete": True}]})
        batch_fill.sync(self.root, "metadata", {"items": [{"unit_id": "q-1", "region": "浙江省", "paper_title": "更新标题",
            "contentComplete": False, "city": "杭州市", "difficultyValue": 0.5, "categories": [], "wordCount": 9,
            "filterMetadata": {"paper_title": "illegal nested title", "contentComplete": True, "inventedUploadField": "ignored"}}]})
        self.assertEqual(self.load()["name"], "更新标题")
        self.assertEqual(self.load(batch_fill.BATCH_BASE)["name"], "明确原卷标题")
        for row in self.load()["items"]:
            for key in ("region", "paper_title", "contentComplete", "city", "district", "semesters", "difficultyValue"):
                self.assertNotIn(key, row)
            for key in ("paper_title", "contentComplete", "inventedUploadField"):
                self.assertNotIn(key, row["filterMetadata"])
        row = self.load()["items"][0]
        self.assertEqual(row["filterMetadata"]["region"], "浙江省")
        self.assertEqual(row["filterMetadata"]["city"], "杭州市")
        self.assertEqual(row["wordCount"], 9)
        self.assertTrue(self.load(batch_fill.STATE)["units"]["q-1"]["stages"]["types"]["contentComplete"])
        report = upload_payload.build_payload(self.load())
        self.assertEqual([error for error in report["errors"] if error["code"] == "UNKNOWN_FIELD"], [])

    def test_group_children_do_not_leak_local_metadata_fields(self):
        from workflow import upload_payload
        self.source(group=True)
        batch_fill.initialize(self.root)
        batch_fill.sync(self.root, "metadata", {"meta": {"region": "安徽省", "paper_title": "共享题组卷"}})
        batch_fill.sync(self.root, "types", {"items": [{"unit_id": "group-1-2", "type": "阅读理解", "contentComplete": True,
            "subquestions": [{"number": 1, "contentComplete": False}]}]})
        row = self.load()["items"][0]
        for node in (row, *row["subquestions"]):
            self.assertEqual(node["filterMetadata"]["region"], "安徽省")
            for key in ("region", "paper_title", "contentComplete"):
                self.assertNotIn(key, node)
                self.assertNotIn(key, node["filterMetadata"]) if key != "region" else None
        report = upload_payload.build_payload(self.load())
        self.assertEqual([error for error in report["errors"] if error["code"] == "UNKNOWN_FIELD"], [])

    def test_cli_initialization(self):
        with redirect_stdout(io.StringIO()) as stdout:
            code = batch_fill.main(["initialize", "--batch-dir", str(self.root)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "initialized")


if __name__ == "__main__":
    unittest.main()
