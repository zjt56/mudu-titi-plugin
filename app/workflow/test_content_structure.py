"""Synthetic-only tests for the native whole-paper structure adapter."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import content_structure as adapter, structure_ai, unit_tasks


RAW = "1. First question?\nA. Yes. B. No.\n2. Second question?\nA. Red. B. Blue."


def units():
    return [{"unit_kind": "standalone", "type": "单项填空", "numbers": [n],
             "stem": "\n".join(RAW.splitlines()[start - 1:start + 1]),
             "source_span": {"start_line": start, "end_line": start + 1}}
            for n, start in ((1, 1), (2, 3))]


def envelope(rows=None):
    return {"status": "completed", "artifacts": {"structure": {"units": units() if rows is None else rows}},
            "notes": [], "findings": []}


class AdapterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        self.source = self.batch / "01_source"
        self.source.mkdir()
        (self.source / "正文.txt").write_text(RAW, encoding="utf-8")
        (self.source / "structure.json").write_text('{"sections": []}', encoding="utf-8")

    def prepare(self, raw=RAW, candidate=None, pdf=None):
        return adapter.prepare(batch_dir=self.batch, raw=raw, candidate=candidate or {"sections": []}, pdf_ocr=pdf)

    def test_frame_and_hashes_cover_full_snapshot(self):
        candidate = {"sections": [{"title": "last heading", "source_line": 4}]}
        prepared = self.prepare(candidate=candidate, pdf={"pages": [{"order": [2, 1], "bbox": [1, 2, 3, 4]}]})
        text = prepared["prompt"].split(unit_tasks._BEGIN, 1)[1].rsplit(unit_tasks._END, 1)[0]
        context = json.loads(text)
        self.assertEqual(context["content_structure"]["raw"], RAW)
        self.assertEqual(context["content_structure"]["pdf_ocr"]["pages"][0]["order"], [2, 1])
        self.assertEqual(len(prepared["input_binding"]), 2)
        candidate["sections"].clear()
        self.assertEqual(len(context["content_structure"]["candidate"]["sections"]), 1)

    def test_adjacent_lines_and_pdf_are_read_from_snapshot(self):
        prepared = self.prepare(pdf={"pages": [{"text": "layout evidence"}]})
        (self.source / "正文.txt").write_text("changed", encoding="utf-8")
        got = adapter.read_source(prepared, start_line=3, end_line=3, around=1)
        self.assertEqual([r["line"] for r in got["lines"]], [2, 3, 4])
        self.assertIn("Second", got["lines"][1]["text"])
        self.assertIn("layout evidence", json.dumps(adapter.read_source(prepared, "pdf_ocr")))

    def test_invalid_windows_and_snapshot_mutation_fail(self):
        prepared = self.prepare()
        for args in ({"start_line": 0}, {"start_line": True}, {"start_line": 8}, {"around": -1}):
            with self.assertRaises(ValueError):
                adapter.read_source(prepared, **args)
        prepared["context"]["content_structure"]["raw"] += "extra"
        with self.assertRaisesRegex(ValueError, "snapshot changed"):
            adapter.validate(prepared, envelope())

    def test_directory_only_does_not_restrict_whole_paper(self):
        prepared = self.prepare(candidate={"units": [units()[0]]})
        row = adapter.read_unit(prepared, "candidate-units:1")
        self.assertTrue(row["candidate_only"])
        self.assertTrue(adapter.validate(prepared, envelope())["passed"])
        with self.assertRaises(ValueError):
            adapter.read_unit(prepared, "unknown")

    def test_complete_source_bound_response_passes_without_writes(self):
        before = set(self.batch.rglob("*"))
        checked = adapter.validate(self.prepare(), envelope())
        self.assertTrue(checked["passed"], checked)
        self.assertEqual(len(checked["units"]), 2)
        self.assertEqual(before, set(self.batch.rglob("*")))

    def test_empty_units_cannot_pass(self):
        checked = adapter.validate(self.prepare(), envelope([]))
        self.assertFalse(checked["passed"])
        self.assertEqual(checked["diagnostics"][0]["check"], "UNITS_MISSING")

    def test_partial_candidate_directory_cannot_hide_source_question(self):
        prepared = self.prepare(candidate={"sections": [], "units": [units()[0]]})
        checked = adapter.validate(prepared, envelope([units()[0]]))
        self.assertFalse(checked["passed"])
        self.assertEqual(len(checked["quarantine_units"]), 1)
        self.assertTrue(any(d.get("number") == 2 for d in checked["diagnostics"]))

    def test_cross_unit_source_overlap_and_reordered_stem_rejected(self):
        rows = units()
        rows[1]["source_span"] = {"start_line": 1, "end_line": 4}
        self.assertFalse(adapter.validate(self.prepare(), envelope(rows))["passed"])
        rows = units()
        rows[0]["stem"] = "A. Yes. B. No.\n1. First question?"
        checked = adapter.validate(self.prepare(), envelope(rows))
        self.assertFalse(checked["passed"])
        self.assertTrue(any(d["check"] == "STEM_NOT_UNIQUE_SOURCE_SLICE" for d in checked["diagnostics"]))

    def test_scoped_repeated_numbers_are_position_bound(self):
        raw = "课时A计划 第一课时 Section A\nⅠ.单项填空\n1. First?\nⅡ.单项填空\n1. Second?"
        rows = [{"unit_kind": "standalone", "type": "单项填空", "numbers": [1],
                 "stem": raw.splitlines()[n - 1], "source_span": {"start_line": n, "end_line": n}}
                for n in (3, 5)]
        prepared = self.prepare(raw=raw)
        self.assertTrue(adapter.validate(prepared, envelope(rows))["passed"])
        checked = adapter.validate(prepared, envelope(rows[:1]))
        self.assertFalse(checked["passed"])
        self.assertTrue(any(d["check"] == "SOURCE_SECTION_UNCOVERED" for d in checked["diagnostics"]))

    def test_native_selection_cache_route_and_pdf_binding(self):
        from workflow import content_agent, local_agent
        config = {"provider": "command", "command": ["fake"], "native_agent": {
            "enabled": True, "stages": ["structure_gate"], "mode": "auto"}}
        route = {"provider": "test", "model": "m1", "profile": "p1"}
        with patch.object(local_agent, "_validate_config", side_effect=lambda c, p: c), \
             patch.object(content_agent, "available", return_value=True), \
             patch.object(content_agent, "invoke", return_value={"response": envelope()}) as native, \
             patch.object(local_agent, "invoke") as oneshot, \
             patch("workflow.visual_transport.route_identity", side_effect=lambda **kw: dict(route)):
            first = structure_ai.auto_classify(self.batch, config=config, force=True)
            self.assertEqual(len(first["units"]), 2)
            self.assertTrue(structure_ai.auto_classify(self.batch, config=config)["reused"])
            self.assertEqual(native.call_count, 1)
            self.assertIn("BEGIN SUPPLIED CONTENT", native.call_args.args[0])
            route["profile"] = "p2"
            structure_ai.auto_classify(self.batch, config=config)
            self.assertEqual(native.call_count, 2)
            (self.source / "pdf-ocr.json").write_text('{"pages": []}', encoding="utf-8")
            structure_ai.auto_classify(self.batch, config=config)
            self.assertEqual(native.call_count, 3)
            oneshot.assert_not_called()

    def test_native_rejection_keeps_candidate_and_never_falls_back(self):
        from workflow import content_agent, local_agent
        config = {"provider": "command", "command": ["fake"], "native_agent": {
            "enabled": True, "stages": ["structure_gate"], "mode": "strict"}}
        def reject(prompt, run_dir, settings):
            (run_dir / "candidate.json").write_text(json.dumps(envelope([units()[0]])), encoding="utf-8")
            raise local_agent.AgentError("invalid coverage", "validation")
        with patch.object(local_agent, "_validate_config", side_effect=lambda c, p: c), \
             patch.object(content_agent, "available", return_value=False), \
             patch.object(content_agent, "invoke", side_effect=reject) as native, \
             patch.object(local_agent, "invoke") as oneshot, \
             patch("workflow.visual_transport.route_identity", return_value={"provider": "test", "model": "m"}):
            result = structure_ai.auto_classify(self.batch, config=config)
            self.assertEqual(result["units"], [])
            self.assertEqual(len(result["quarantine_units"]), 1)
            marker = json.loads((self.batch / structure_ai.CLASSIFY_MARKER).read_text(encoding="utf-8"))
            self.assertEqual(len(marker["candidate_response"]["units"]), 1)
            self.assertIn("native_failure", marker)
            second = structure_ai.auto_classify(self.batch, config=config)
            self.assertTrue(second["reused"])
            self.assertEqual(native.call_count, 1)
            oneshot.assert_not_called()

    def test_binding_rejects_optional_evidence_appearance_and_removal(self):
        prepared = self.prepare()
        self.assertTrue(adapter.verify_binding(prepared, self.batch))
        (self.source / "pdf-ocr.json").write_text('{}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "pdf-ocr"):
            adapter.verify_binding(prepared, self.batch)
        prepared = self.prepare()
        (self.source / "正文.txt").unlink()
        with self.assertRaisesRegex(ValueError, "正文"):
            adapter.verify_binding(prepared, self.batch)

    def test_sync_rules_and_late_missing_unnumbered_section_are_preserved(self):
        raw = "课时A计划 第一课时 Section A\nⅠ.单项填空\n1. First?\nⅡ.看图题\n[IMG picture.png]"
        rows = [{"unit_kind": "standalone", "type": "单项填空", "numbers": [1],
                 "stem": "1. First?", "source_span": {"start_line": 3, "end_line": 3}},
                {"unit_kind": "group", "type": "看图题", "numbers": [1],
                 "stem": "[IMG picture.png]", "source_span": {
                     "start_line": 5, "end_line": 5, "number_binding": "unnumbered_section"}}]
        prepared = self.prepare(raw=raw)
        self.assertIn("同步", prepared["prompt"].split(unit_tasks._BEGIN)[0])
        checked = adapter.validate(prepared, envelope(rows))
        self.assertTrue(checked["passed"], checked)
        checked = adapter.validate(prepared, envelope(rows[:1]))
        self.assertFalse(checked["passed"])
        self.assertTrue(any(d["check"] == "SOURCE_SECTION_UNCOVERED" for d in checked["diagnostics"]))

    def test_auto_native_unavailable_retains_existing_one_shot(self):
        from workflow import content_agent, local_agent
        config = {"provider": "command", "command": ["fake"], "native_agent": {
            "enabled": True, "stages": ["structure_gate"], "mode": "auto"}}
        with patch.object(local_agent, "_validate_config", side_effect=lambda c, p: c), \
             patch.object(content_agent, "available", return_value=False), \
             patch.object(content_agent, "invoke") as native, \
             patch.object(local_agent, "invoke", return_value=envelope()) as oneshot:
            self.assertEqual(len(structure_ai.auto_classify(self.batch, config=config)["units"]), 2)
            native.assert_not_called()
            oneshot.assert_called_once()

    def test_native_empty_result_is_quarantined(self):
        from workflow import content_agent, local_agent
        config = {"provider": "command", "command": ["fake"], "native_agent": {
            "enabled": True, "stages": ["structure_gate"], "mode": "auto"}}
        with patch.object(local_agent, "_validate_config", side_effect=lambda c, p: c), \
             patch.object(content_agent, "available", return_value=True), \
             patch.object(content_agent, "invoke", return_value=envelope([])), \
             patch.object(local_agent, "invoke") as oneshot, \
             patch("workflow.visual_transport.route_identity", return_value={"provider": "test", "model": "m"}):
            result = structure_ai.auto_classify(self.batch, config=config)
            self.assertFalse(result["units"])
            self.assertEqual(result["diagnostics"][0]["check"], "UNITS_MISSING")
            oneshot.assert_not_called()

    def test_native_input_change_never_exposes_valid_candidate_as_units(self):
        from workflow import content_agent, local_agent
        config = {"provider": "command", "command": ["fake"], "native_agent": {
            "enabled": True, "stages": ["structure_gate"], "mode": "auto"}}
        def stale(prompt, run_dir, settings):
            (run_dir / "candidate.json").write_text(json.dumps(envelope()), encoding="utf-8")
            raise local_agent.AgentError("input changed", "context_changed")
        with patch.object(local_agent, "_validate_config", side_effect=lambda c, p: c), \
             patch.object(content_agent, "available", return_value=True), \
             patch.object(content_agent, "invoke", side_effect=stale), \
             patch("workflow.visual_transport.route_identity", return_value={"provider": "test", "model": "m"}):
            result = structure_ai.auto_classify(self.batch, config=config)
            self.assertEqual(result["units"], [])
            self.assertEqual(result["quarantine_units"], [])
            marker = json.loads((self.batch / structure_ai.CLASSIFY_MARKER).read_text(encoding="utf-8"))
            self.assertEqual(len(marker["candidate_response"]["units"]), 2)
            result = structure_ai.auto_classify(self.batch, config=config)
            self.assertTrue(result["reused"])
            self.assertFalse(result["units"])

    def test_native_body_change_after_receipt_is_rejected(self):
        from workflow import content_agent, local_agent
        config = {"provider": "command", "command": ["fake"], "native_agent": {
            "enabled": True, "stages": ["structure_gate"], "mode": "auto"}}
        def change(prompt, run_dir, settings):
            (run_dir / "candidate.json").write_text(json.dumps(envelope()), encoding="utf-8")
            (self.source / "pdf-ocr.json").write_text('{}', encoding="utf-8")
            return envelope()
        with patch.object(local_agent, "_validate_config", side_effect=lambda c, p: c), \
             patch.object(content_agent, "available", return_value=True), \
             patch.object(content_agent, "invoke", side_effect=change), \
             patch("workflow.visual_transport.route_identity", return_value={"provider": "test", "model": "m"}):
            result = structure_ai.auto_classify(self.batch, config=config)
            self.assertFalse(result["units"])
            self.assertTrue(any(d.get("code") == "context_changed" for d in result["diagnostics"]))

    def test_ordinary_writing_points_follow_existing_gate_exclusion(self):
        raw = "Ⅰ.书面表达\n80. Write a letter to your friend.\n1. Introduce yourself.\n2. Talk about school."
        candidate = {"sections": [{"title": "Ⅰ.书面表达", "type": "书面表达", "questions": [80]}]}
        row = {"unit_kind": "standalone", "type": "书面表达", "numbers": [80],
               "stem": "\n".join(raw.splitlines()[1:]), "source_span": {"start_line": 2, "end_line": 4}}
        checked = adapter.validate(self.prepare(raw=raw, candidate=candidate), envelope([row]))
        self.assertTrue(checked["passed"], checked)

    def test_cloze_blank_and_printed_option_number_are_one_owner(self):
        raw = "Story <u>　1　</u> and <u>　2　</u>.\n( )1. A. red B. blue\n( )2. A. yes B. no"
        row = {"unit_kind": "group", "type": "完形填空", "numbers": [1, 2], "stem": raw,
               "source_span": {"start_line": 1, "end_line": 3}}
        checked = adapter.validate(self.prepare(raw=raw), envelope([row]))
        self.assertTrue(checked["passed"], checked)

    def media_fixture(self):
        image = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg==")
        media = self.source / "media"
        media.mkdir(exist_ok=True)
        (media / "image1.png").write_bytes(image)
        (media / "not-declared.png").write_bytes(image)
        extraction = {"method": "docx_native", "source_sha256": "f" * 64,
                      "media": [{"name": "image1.png", "path": "C:/private/extract-one/media/image1.png",
                                 "source_part": "word/media/image1.png", "relationship_id": "rId2",
                                 "block_index": 0, "size": len(image), "occurrence": 0}], "pages": []}
        (self.source / "extraction.json").write_text(json.dumps(extraction), encoding="utf-8")
        return image, extraction

    def test_prepare_declares_real_extracted_img_for_broker_snapshot(self):
        from workflow import content_media
        image, extraction = self.media_fixture()
        prepared = self.prepare(raw="1. See picture. [IMG image1.png]")
        media = content_media.build_snapshot(self.batch, prepared["context"], prepared["input_binding"])
        self.assertEqual([row["path"] for row in media["media"]], ["01_source/media/image1.png"])
        self.assertEqual(media["media"][0]["sha256"], hashlib.sha256(image).hexdigest())
        self.assertIn("01_source/extraction.json", prepared["input_binding"])
        self.assertIn("01_source/media/image1.png", prepared["input_binding"])
        self.assertNotIn("01_source/media/not-declared.png", prepared["input_binding"])
        self.assertNotIn("C:/private", prepared["prompt"])
        self.assertTrue(adapter.verify_binding(prepared, self.batch))

    def test_media_bytes_and_inventory_changes_invalidate_binding(self):
        image, extraction = self.media_fixture()
        prepared = self.prepare(raw="1. [IMG image1.png]")
        (self.source / "media/image1.png").write_bytes(image + b"changed")
        with self.assertRaisesRegex(ValueError, "media/image1.png"):
            adapter.verify_binding(prepared, self.batch)
        (self.source / "media/image1.png").write_bytes(image)
        prepared = self.prepare(raw="1. [IMG image1.png]")
        extraction["media"] = []
        (self.source / "extraction.json").write_text(json.dumps(extraction), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "extraction.json"):
            adapter.verify_binding(prepared, self.batch)

    def test_pdf_preview_name_is_declared_but_evidence_path_never_guessed(self):
        from workflow import content_media
        image, extraction = self.media_fixture()
        extraction["media"] = []
        extraction["pages"] = [{"page": 1, "method": "native", "images": [
            {"path": "C:/private/evidence/image.png", "preview_name": "image1.png", "bbox": [1, 2, 3, 4]},
            {"path": "C:/private/evidence/not-declared.png", "bbox": [2, 3, 4, 5]}]}]
        (self.source / "extraction.json").write_text(json.dumps(extraction), encoding="utf-8")
        prepared = self.prepare(raw="1. [IMG image1.png]")
        media = content_media.build_snapshot(self.batch, prepared["context"], prepared["input_binding"])
        self.assertEqual([row["path"] for row in media["media"]], ["01_source/media/image1.png"])

    def test_bare_img_without_extraction_does_not_authorize_file(self):
        from workflow import content_media
        self.media_fixture()
        (self.source / "extraction.json").unlink()
        prepared = self.prepare(raw="1. [IMG image1.png]")
        media = content_media.build_snapshot(self.batch, prepared["context"], prepared["input_binding"])
        self.assertEqual(media["media"], [])
        (self.source / "extraction.json").write_text('{"media": []}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "extraction.json"):
            adapter.verify_binding(prepared, self.batch)

    def test_native_media_bytes_and_declarations_are_cache_bound(self):
        from workflow import content_agent, local_agent
        image, extraction = self.media_fixture()
        config = {"provider": "command", "command": ["fake"], "native_agent": {
            "enabled": True, "stages": ["structure_gate"], "mode": "auto"}}
        with patch.object(local_agent, "_validate_config", side_effect=lambda c, p: c), \
             patch.object(content_agent, "available", return_value=True), \
             patch.object(content_agent, "invoke", return_value=envelope()) as native, \
             patch("workflow.visual_transport.route_identity", return_value={"provider": "test", "model": "m"}):
            self.assertTrue(structure_ai.auto_classify(self.batch, config=config)["units"])
            self.assertTrue(structure_ai.auto_classify(self.batch, config=config)["reused"])
            self.assertEqual(native.call_count, 1)
            self.assertIn("01_source/media/image1.png", native.call_args.args[2]["_content_input_hashes"])
            (self.source / "media/image1.png").write_bytes(image + b"changed")
            changed = structure_ai.auto_classify(self.batch, config=config)
            self.assertFalse(changed["reused"])
            self.assertEqual(native.call_count, 2)
            extraction["media"][0]["occurrence"] = 1
            (self.source / "extraction.json").write_text(json.dumps(extraction), encoding="utf-8")
            self.assertFalse(structure_ai.auto_classify(self.batch, config=config)["reused"])
            self.assertEqual(native.call_count, 3)

    def test_invalid_extraction_name_cannot_authorize_traversal(self):
        self.media_fixture()
        (self.source / "extraction.json").write_text('{"media":[{"name":"../escape.png"}]}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "media name"):
            self.prepare(raw="1. [IMG image1.png]")

    def test_injected_invoker_never_enters_native(self):
        from workflow import content_agent
        with patch.object(content_agent, "invoke") as native:
            result = structure_ai.auto_classify(self.batch, invoke_fn=lambda *a: envelope(),
                                               config={"native_agent": {"enabled": True, "stages": ["structure_gate"]}})
            self.assertEqual(len(result["units"]), 2)
            native.assert_not_called()


if __name__ == "__main__":
    unittest.main()
