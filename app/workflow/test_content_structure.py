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

    def visual_fixture(self, *, role="question", raw=None, heading=None, start_line=3):
        from workflow import source_pages, visual_columns
        from workflow.test_source_pages import png
        raw = raw or "第一课时 Section A\n陌生栏目名称\nFill in the blank.\nI ____ a student."
        (self.source / "正文.txt").write_text(raw, encoding="utf-8")
        (self.batch / "attachments").mkdir(exist_ok=True)
        original = self.batch / "attachments/source.docx"
        original.write_bytes(b"Word source visual fixture")
        (self.batch / "workflow").mkdir(exist_ok=True)
        (self.batch / "workflow/manifest.json").write_text(json.dumps({"inputs": {
            "source": {"path": "attachments/source.docx"}}}), encoding="utf-8")
        def render(source, output, config):
            output.mkdir()
            (output / "page-1.png").write_bytes(png())
            (output / "manifest.json").write_text(json.dumps({"source_sha256": source_pages._sha(source.read_bytes()),
                "full_page": True, "page_count": 1, "pages": [{"page": 1, "path": "page-1.png"}]}))
            return "test-command"
        with patch.object(source_pages, "_render", side_effect=render):
            source_pages.prepare(self.batch)
        lines = raw.splitlines()
        result = {"columns": [{"id": "c1", "label": lines[1], "role": role, "parent_id": None,
                  "heading_line": heading, "start_line": start_line, "end_line": len(lines),
                  "start_text": lines[start_line - 1], "end_text": lines[-1], "printed_numbers": [],
                  "boxes": [{"page": 1, "bbox": [0.1, 0.2, 0.9, 0.8]}], "reason": "完整页边框与真实作答窗口"}], "issues": []}
        route = {"provider": "test", "model": "m", "profile": "p"}
        with patch("workflow.visual_transport.route_identity", return_value=route):
            response = visual_columns.build(self.batch, identify=lambda *a, **k: {**route, "text": json.dumps(result)})
        self.assertEqual(response["status"], "verified", response)
        row = {"unit_kind": "standalone", "type": "单词拼写", "numbers": [1], "column_id": "c1",
               "stem": "\n".join(lines[start_line - 1:]), "source_span": {"start_line": start_line, "end_line": len(lines),
               "number_binding": "unnumbered_section", "printed_numbers": []}}
        return raw, row

    def test_visual_direct_unnumbered_task_roundtrips_gate_rebuild_and_null_range(self):
        from workflow import structure_gate, question_content, upload_numbering
        raw, row = self.visual_fixture()
        prepared = self.prepare(raw=raw)
        checked = adapter.validate(prepared, envelope([row]))
        self.assertTrue(checked["passed"], checked)
        unit = checked["units"][0]
        self.assertEqual(unit["source_span"]["printed_numbers"], [])
        self.assertIsNone(unit["original_number"])
        self.assertFalse(unit["printed_number"])
        self.assertEqual(unit["source_section"]["column_path"], [{"column_id": "c1", "label": "陌生栏目名称", "role": "question"}])
        (self.batch / structure_ai.CLASSIFY_MARKER).write_text(json.dumps({"candidate_response": {"units": [row]}}))
        with patch.object(structure_ai, "auto_classify", return_value={"units": [unit]}):
            report = structure_gate.run_gate(self.batch)
        self.assertTrue(report["passed"], report)
        rebuilt = question_content._rebuild_source_units(self.batch, {"sections": []}, raw)
        self.assertEqual([u["unit_id"] for u in rebuilt], [unit["unit_id"]])
        projection = upload_numbering.project_batch({"items": [dict(unit, id=unit["unit_id"])]}, self.batch)
        self.assertEqual(projection["errors"], [], projection["errors"])
        self.assertIsNone(projection["items"][0]["numberStart"])
        self.assertIsNone(projection["items"][0]["numberEnd"])
        self.assertEqual(projection["evidence"]["units"][0]["state"], "proven-unnumbered")
        from workflow import preview
        from workbench import server
        selected = preview.source_slice(self.batch, [1], raw.splitlines(), unit_id=unit['unit_id'])
        self.assertEqual(selected, raw.splitlines()[2:])
        blocks = server.source_blocks(selected)
        self.assertEqual('\n'.join(b['text'] for b in blocks if b['type'] == 'text'), '\n'.join(raw.splitlines()[2:]))
        (self.source / 'visual-columns.json').write_text('{}')
        self.assertEqual(preview.source_slice(self.batch, [1], raw.splitlines(), unit_id=unit['unit_id']), [])

    def test_visual_direct_task_does_not_extend_preceding_numbered_section(self):
        from workflow import structure_gate, visual_columns, question_content
        raw = ("第一课时 Section A\nⅠ.单词拼写\n1. Apple a____.\n"
               "陌生栏目\nFill in the blank.\nI ____ a student.")
        # Prepare source-bound whole-page evidence, then move the exact task
        # coordinates to its real location in the longer Word view.
        # 该栏目前面是带印刷题号的 Ⅰ.单词拼写，必须从真正无号的那一行起绑，
        # 否则整窗会覆盖 1. Apple a____. 而把有号题伪装成无号栏目。
        _, _ = self.visual_fixture(raw=raw, start_line=5)
        path = self.source / "visual-columns.json"
        proof = json.loads(path.read_text())
        row = proof["response"]["columns"][0]
        row.update(start_line=5, start_text=raw.splitlines()[4], heading_line=4)
        proof["columns"] = visual_columns._columns(proof["response"], raw, proof["pages"])
        path.write_text(json.dumps(proof))
        candidate = {"sections": [{"title": "Ⅰ.单词拼写", "type": "单词拼写", "questions": [1],
                                    "source_line": 2, "source_end_line": 6}]}
        (self.source / "structure.json").write_text(json.dumps(candidate))
        rows = [{"unit_kind": "standalone", "type": "单词拼写", "numbers": [1], "stem": raw.splitlines()[2],
                 "source_span": {"start_line": 3, "end_line": 3}},
                {"unit_kind": "standalone", "type": "单词拼写", "numbers": [1], "column_id": "c1",
                 "stem": "\n".join(raw.splitlines()[4:]), "source_span": {"start_line": 5, "end_line": 6,
                 "number_binding": "unnumbered_section", "printed_numbers": []}}]
        checked = adapter.validate(self.prepare(raw=raw, candidate=candidate), envelope(rows))
        self.assertTrue(checked["passed"], checked)
        candidate = visual_columns.annotate_candidate(self.batch, raw, candidate)
        fresh = structure_gate._build_units(candidate["sections"], [], raw.splitlines(), candidate["visual_columns"])
        self.assertEqual(fresh[0]["stem"], "1. Apple a____.")
        merged = structure_gate._merge_visual_units(fresh, checked["units"], candidate["visual_columns"], raw.splitlines(), candidate["sections"])
        self.assertEqual(len(merged), 2)
        self.assertIsNotNone(question_content._validated_ai_units(checked["units"], merged, raw.splitlines()))
        errors = structure_gate._scoped_source_errors(checked["units"][:1], candidate["sections"], raw.splitlines(), candidate["visual_columns"])
        self.assertIn("VISUAL_QUESTION_UNCOVERED", {e["code"] for e in errors})

    def test_failed_visual_ownership_cannot_pass_by_numbered_fallback(self):
        from workflow import structure_gate
        raw, row = self.visual_fixture()
        (self.source / 'visual-columns.json').write_text(json.dumps({'status': 'unavailable', 'reason': 'EMPTY_RESPONSE'}))
        with patch.object(structure_ai, 'auto_classify', return_value={'ran': False}):
            report = structure_gate.run_gate(self.batch)
        self.assertFalse(report['passed'])
        self.assertIn('COLUMN_OWNERSHIP_UNRESOLVED', [r['code'] for r in report['blockers']])

    def test_visual_guidance_never_becomes_question_even_with_familiar_label(self):
        raw, row = self.visual_fixture(role="guidance", raw="第一课时 Section A\n基础巩固\nTip about words.\nRead carefully.")
        checked = adapter.validate(self.prepare(raw=raw), envelope([row]))
        self.assertFalse(checked["passed"])
        self.assertIn("COLUMN_ROLE_INVALID", {r["check"] for r in checked["diagnostics"]})

    def test_visual_no_number_claim_cannot_hide_printed_question(self):
        # 栏目窗口里明明有印刷小题号 1.，视觉却声明 printed_numbers=[]：这种
        # “无号”声明必须在视觉证据落盘前就被拒，不能先写成 verified 再靠下游拦。
        from workflow import source_pages, visual_columns
        from workflow.test_source_pages import png
        raw = "第一课时 Section A\n创新训练\n1. Fill in the blank.\nI ____ a student."
        (self.source / "正文.txt").write_text(raw, encoding="utf-8")
        (self.batch / "attachments").mkdir(exist_ok=True)
        original = self.batch / "attachments/source.docx"
        original.write_bytes(b"Word source visual fixture")
        (self.batch / "workflow").mkdir(exist_ok=True)
        (self.batch / "workflow/manifest.json").write_text(json.dumps({"inputs": {
            "source": {"path": "attachments/source.docx"}}}), encoding="utf-8")
        def render(source, output, config):
            output.mkdir()
            (output / "page-1.png").write_bytes(png())
            (output / "manifest.json").write_text(json.dumps({"source_sha256": source_pages._sha(source.read_bytes()),
                "full_page": True, "page_count": 1, "pages": [{"page": 1, "path": "page-1.png"}]}))
            return "test-command"
        with patch.object(source_pages, "_render", side_effect=render):
            source_pages.prepare(self.batch)
        lines = raw.splitlines()
        result = {"columns": [{"id": "c1", "label": lines[1], "role": "question", "parent_id": None,
                  "heading_line": None, "start_line": 3, "end_line": len(lines),
                  "start_text": lines[2], "end_text": lines[-1], "printed_numbers": [],
                  "boxes": [{"page": 1, "bbox": [0.1, 0.2, 0.9, 0.8]}], "reason": "完整页边框与真实作答窗口"}], "issues": []}
        route = {"provider": "test", "model": "m", "profile": "p"}
        with patch("workflow.visual_transport.route_identity", return_value=route):
            response = visual_columns.build(self.batch, identify=lambda *a, **k: {**route, "text": json.dumps(result)})
        self.assertEqual(response["status"], "unavailable", response)
        self.assertIn("COLUMN_NUMBERLESS_HAS_PRINTED_ANCHORS", response["reason"])

    def test_visual_task_requires_complete_word_window_and_no_rewrite(self):
        raw, row = self.visual_fixture()
        for change in ({"stem": "I am a student."},
                       {"stem": "I ____ a student.", "source_span": {"start_line": 4, "end_line": 4, "number_binding": "unnumbered_section"}}):
            bad = dict(row, **change)
            checked = adapter.validate(self.prepare(raw=raw), envelope([bad]))
            self.assertFalse(checked["passed"], checked)

    def test_visual_pages_and_column_proofs_are_native_snapshot_bound(self):
        raw, row = self.visual_fixture()
        prepared = self.prepare(raw=raw)
        self.assertEqual(len(prepared["context"]["media"]), 1)
        image = prepared["context"]["media"][0]["path"]
        self.assertTrue(image.startswith("01_source/visual-pages/"))
        self.assertIn("01_source/visual-columns.json", prepared["input_binding"])
        self.assertIn("01_source/visual-pages/manifest.json", prepared["input_binding"])
        self.assertTrue(adapter.verify_binding(prepared, self.batch))
        (self.batch / image).write_bytes(b"changed page")
        with self.assertRaises(ValueError):
            adapter.verify_binding(prepared, self.batch)
        # A forged candidate cannot turn an unavailable proof back into trusted columns.
        forged = self.prepare(raw=raw, candidate=prepared["context"]["content_structure"]["candidate"])
        self.assertFalse(forged["context"]["content_structure"]["candidate"]["visual_columns"])
        self.assertFalse(adapter.validate(forged, envelope([row]))["passed"])

    def test_late_visual_task_missing_from_submission_is_not_covered_by_previous_unit(self):
        from workflow import visual_columns
        raw, row = self.visual_fixture()
        candidate = visual_columns.annotate_candidate(self.batch, raw, {"sections": []})
        cols = candidate["visual_columns"]
        from workflow.structure_gate import _scoped_source_errors
        errors = _scoped_source_errors([], [], raw.splitlines(), cols)
        self.assertIn("VISUAL_QUESTION_UNCOVERED", {r["code"] for r in errors})

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
