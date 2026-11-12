"""Isolated final format checks; no real batch, model or network access."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from workbench import taxonomy
from workflow import format_gate


def question(number=1):
    path = "词汇/词汇辨析/动词辨析"
    return {"id": f"Q{number}", "unit_id": f"unit-{number}", "unit_kind": "standalone",
            "number": number, "numbers": [number], "question_type": "单项填空",
            "difficulty": "适中", "filterMetadata": {"subtypes": ["四个选项"]},
            "stem": "Choose the correct word.", "options": ["A. go", "B. see", "C. read", "D. write"],
            "answer": "无答案", "explanation": "未提供", "articleSummary": "无",
            "knowledge": [path], "knowledgeIds": [taxonomy.knowledge_index()[0][path]["id"]],
            "source_type": "中考真题", "year": 2024, "source": "原卷明确标题"}


def batch():
    return {"batch_id": "batch-fixture", "name": "原卷明确标题", "items": [question()]}


def group():
    q = question(2)
    q.update(unit_kind="group", numbers=[2, 3, 4], question_type="阅读理解",
             material="Two children visit a museum.",
             knowledge=["语篇体裁/记叙文/故事", "主题/人与自我/生活与学习/学校生活"],
             filterMetadata={"subtypes": ["选择3题"]},
             subquestions=[{"number": n, "stem": "Where?", "options": ["A. Museum", "B. Park"],
                            "answer": "A", "explanation": "无", "knowledge": []} for n in (2, 3, 4)])
    q.pop("knowledgeIds")
    return q


class FormatGateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="format-gate-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.final = self.root / format_gate.FINAL_PATH
        self.final.parent.mkdir()
        self.env = patch.dict(os.environ, {"TMPDIR": self.temp.name, "WF_STRUCTURE_AI": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)
        # Fail immediately if a future regression adds a network side effect.
        self.network = patch("socket.socket.connect", side_effect=AssertionError("network forbidden"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def put(self, data=None):
        self.final.write_text(json.dumps(batch() if data is None else data, ensure_ascii=False), encoding="utf-8")

    def codes(self, report):
        return {e["code"] for e in report["errors"]}

    def test_valid_read_only_real_builder_deferred_and_noop(self):
        self.put()
        original = self.final.read_bytes()
        real = format_gate.upload_payload.build_payload
        with patch.object(format_gate.upload_payload, "build_payload", wraps=real) as build:
            result = format_gate.run(self.root)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(build.call_count, 1)
        self.assertIsNone(build.call_args.kwargs["catalog"])
        self.assertIn("CATALOG_REQUIRED", {e["code"] for e in result["deferred"]})
        self.assertTrue(result["prepare_upload_required"])
        self.assertNotIn("network_checked", result)
        self.assertNotIn("remote_ready", result)
        self.assertEqual(result["final_sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(self.final.read_bytes(), original)
        report = self.root / format_gate.REPORT_PATH
        before = report.stat()
        with patch.object(format_gate.os, "replace", side_effect=AssertionError("no-op must not replace")):
            again = format_gate.run(self.root)
        self.assertEqual(again, result)
        self.assertEqual(report.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(json.loads(report.read_text()), result)
        self.assertEqual(list(report.parent.glob(".upload-format-*")), [])

    def test_options_must_already_be_strings(self):
        for options in ([{"label": "A", "text": "go"}], [1], "A. go", None):
            with self.subTest(options=options):
                data = batch()
                data["items"][0]["options"] = options
                self.put(data)
                result = format_gate.run(self.root)
                self.assertFalse(result["passed"])
                self.assertIn("OPTIONS_INVALID", self.codes(result))

    def test_global_duplicate_numbers_and_ids(self):
        for changed, code in (("number", "QUESTION_NUMBER_DUPLICATE"), ("id", "QUESTION_ID_DUPLICATE"),
                              ("unit_id", "UNIT_ID_DUPLICATE")):
            with self.subTest(changed=changed):
                data = batch()
                other = question(2)
                other[changed] = data["items"][0][changed]
                data["items"].append(other)
                self.put(data)
                self.assertIn(code, self.codes(format_gate.run(self.root)))

    def test_group_children_and_cross_group_identity(self):
        data = batch()
        data["items"].append(group())
        self.put(data)
        result = format_gate.run(self.root)
        self.assertTrue(result["passed"], result["errors"])
        for mutate, code in (
            (lambda q: q["subquestions"][1].update(number=2), "QUESTION_NUMBER_DUPLICATE"),
            (lambda q: q.update(numbers=[2, 4]), "GROUP_NUMBER_COVERAGE"),
            (lambda q: q["subquestions"][0].update(id="Q1"), "QUESTION_ID_DUPLICATE"),
            (lambda q: q["subquestions"][0].update(unit_kind="group", subquestions=[{}]), "NESTED_GROUP_INVALID"),
        ):
            with self.subTest(code=code):
                modified = deepcopy(data)
                mutate(modified["items"][1])
                self.put(modified)
                self.assertIn(code, self.codes(format_gate.run(self.root)))
        data["items"].append(question(3))
        self.put(data)
        self.assertIn("QUESTION_NUMBER_DUPLICATE", self.codes(format_gate.run(self.root)))

    def test_duplicate_json_keys_nonfinite_and_nonobjects(self):
        for raw in (b'{"items":[],"items":[]}', b'{"x":{"a":1,"a":2}}',
                    b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e9999}', b'[]', b'null'):
            with self.subTest(raw=raw):
                self.final.write_bytes(raw)
                result = format_gate.run(self.root)
                self.assertFalse(result["passed"])
                self.assertEqual(self.final.read_bytes(), raw)
                self.assertEqual(result["final_sha256"], hashlib.sha256(raw).hexdigest())

    def test_schema_required_not_filled_by_builder(self):
        for key in ("id", "unit_kind", "number", "numbers", "stem", "knowledge", "question_type"):
            with self.subTest(key=key):
                data = batch()
                del data["items"][0][key]
                self.put(data)
                self.assertIn("SCHEMA_REQUIRED", self.codes(format_gate.run(self.root)))
        data = batch()
        del data["items"][0]["source"]
        self.put(data)
        self.assertIn("MISSING_SOURCE", self.codes(format_gate.run(self.root)))

    def test_ids_and_types_not_normalized_away(self):
        for key, value in (("knowledgeIds", [True]), ("knowledge_ids", ["x", "x"]),
                           ("answer", []), ("explanation", 0), ("knowledge_version_id", 7)):
            with self.subTest(key=key):
                data = batch()
                data["items"][0][key] = value
                self.put(data)
                self.assertFalse(format_gate.run(self.root)["passed"])

    def test_tables_and_attachments(self):
        data = batch()
        data["items"][0].update(materialBlocks=[{"type": "table", "rows": [{"cells": [{"text": "x"}]}]}],
                                 attachments=[{"kind": "image", "url": "/source-media/fixture/x.png"}])
        self.put(data)
        self.assertTrue(format_gate.run(self.root)["passed"])
        for field, value, code in (
            ("materialBlocks", [{"type": "table", "rows": []}], "MATERIAL_TABLE_INVALID"),
            ("attachments", [{"kind": "image", "url": "file:///tmp/x.png"}], "ATTACHMENT_URL_INVALID"),
            ("attachments", [{"kind": "image", "url": "/source-media/x.png", "placement": "option"}], "ATTACHMENT_OPTION_INVALID"),
        ):
            modified = deepcopy(data)
            modified["items"][0][field] = value
            self.put(modified)
            self.assertIn(code, self.codes(format_gate.run(self.root)))

    def test_missing_remote_catalog_media_and_textbook_deferred_only(self):
        data = batch()
        data["items"][0].update(textbookMappings=[{
            "textbookId": "book-1", "knowledgeVersionId": "book-version", "knowledgeIds": ["book-leaf"]}])
        self.put(data)
        with patch("workflow.upload_client.ApiClient", side_effect=AssertionError("remote client forbidden")):
            result = format_gate.run(self.root)
        self.assertTrue(result["passed"], result["errors"])
        deferred = {e["code"] for e in result["deferred"]}
        self.assertTrue({"CATALOG_REQUIRED", "TEXTBOOK_CATALOG_UNSUPPORTED",
                         "TEXTBOOK_KNOWLEDGE_INVALID"}.issubset(deferred))
        data["items"][0]["textbookMappings"][0]["knowledgeIds"] = [2]
        self.put(data)
        self.assertIn("TEXTBOOK_MAPPINGS_INVALID", self.codes(format_gate.run(self.root)))
        data["items"][0]["textbookMappings"][0].pop("knowledgeVersionId")
        self.put(data)
        self.assertIn("TEXTBOOK_VERSION_INVALID", self.codes(format_gate.run(self.root)))

    def media(self, url, content=b"fixture media"):
        target = self.root / url
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    def test_local_attachments_hashed_read_only_and_deployment_deferred(self):
        urls = ["01_source/media/试题 image.png", "02_audio/listening.mp3"]
        for url in urls:
            self.media(url)
        data = batch()
        data["items"][0]["attachments"] = [
            {"kind": "image", "url": urls[0], "placement": "option", "optionKey": "A"},
            {"kind": "audio", "url": urls[1]},
            {"kind": "image", "url": "/source-media/deployed/actual.png"}]
        self.put(data)
        original = self.final.read_bytes()
        real = format_gate.upload_payload.build_payload
        observed = []

        def build(value, **kwargs):
            self.assertEqual(value, data)
            result = real(value, **kwargs)
            observed.append(deepcopy(result))
            return result

        with patch.object(format_gate.upload_payload, "build_payload", side_effect=build):
            result = format_gate.run(self.root)
        self.assertTrue(result["passed"], result)
        self.assertEqual(self.final.read_bytes(), original)
        for url in urls:
            self.assertEqual(result["input_sha256"][url], hashlib.sha256(b"fixture media").hexdigest())
        deferred = [e for e in result["deferred"] if e["code"] == "MEDIA_DEPLOYMENT_REQUIRED"]
        self.assertEqual(len(deferred), 2)
        self.assertEqual({e["unit_id"] for e in deferred}, {"Q1"})
        self.assertTrue(all(e["source_code"] == "ATTACHMENT_URL_INVALID" for e in deferred))
        self.assertEqual(len([e for e in observed[0]["errors"] if e["code"] == "ATTACHMENT_URL_INVALID"]), 2)
        self.assertNotIn("payload", result)
        self.assertNotIn("/source-media/", json.dumps(result))
        self.assertEqual(format_gate.run(self.root), result)

    def test_local_attachment_missing_directory_and_unsafe_paths_block(self):
        self.media("01_source/media/valid.png")
        (self.root / "02_audio").mkdir()
        for url in ("01_source/media/missing.png", "02_audio", "../escape.png",
                    "01_source/../media/valid.png", "/tmp/outside.png", "C:/outside.png",
                    "https://example.test/a.png", "//example.test/a.png", "file:///tmp/a.png",
                    "/source-media/../outside.png", "/source-media/%2e%2e/outside.png",
                    "/source-media/x.png?redirect=outside",
                    "01_source/media/%2e%2e/x.png", "01_source/media/valid.png?x=1",
                    "01_source/media/valid.png#x", "01_source\\media\\valid.png",
                    "01_source//media/valid.png", "01_source/media/./valid.png", "a\u0000b", "", None):
            with self.subTest(url=url):
                data = batch()
                data["items"][0]["attachments"] = [{"kind": "image", "url": url}]
                self.put(data)
                result = format_gate.run(self.root)
                self.assertFalse(result["passed"], result)
                self.assertIn("ATTACHMENT_URL_INVALID", self.codes(result))
                self.assertFalse(any(e["code"] == "MEDIA_DEPLOYMENT_REQUIRED" for e in result["deferred"]))
        data["items"][0]["attachments"][0]["url"] = "01_source/media/missing.png"
        self.put(data)
        self.assertIn("LOCAL_ATTACHMENT_INVALID", self.codes(format_gate.run(self.root)))

    def test_local_attachment_symlink_and_symlink_ancestor_block(self):
        actual = self.media("actual.png")
        directory = self.root / "01_source/media"
        directory.mkdir(parents=True)
        (directory / "link.png").symlink_to(actual)
        (self.root / "02_audio").symlink_to(directory, target_is_directory=True)
        with tempfile.TemporaryDirectory(prefix="outside-media-") as outside:
            external = Path(outside) / "outside.png"
            external.write_bytes(b"outside")
            (directory / "outside.png").symlink_to(external)
            for url in ("01_source/media/link.png", "01_source/media/outside.png", "02_audio/link.png"):
                with self.subTest(url=url):
                    data = batch()
                    data["items"][0]["attachments"] = [{"kind": "image", "url": url}]
                    self.put(data)
                    result = format_gate.run(self.root)
                    self.assertIn("LOCAL_ATTACHMENT_INVALID", self.codes(result))
                    self.assertIn("ATTACHMENT_URL_INVALID", self.codes(result))
                    self.assertNotIn(url, result["input_sha256"])

    def test_url_deferral_is_owner_specific_and_mixed_urls_block(self):
        url = "01_source/media/valid.png"
        self.media(url)
        data = batch()
        data["items"][0]["attachments"] = [{"kind": "image", "url": url}]
        other = question(2)
        other["attachments"] = [{"kind": "image", "url": "https://example.test/unsafe.png"}]
        data["items"].append(other)
        self.put(data)
        result = format_gate.run(self.root)
        self.assertEqual({e["unit_id"] for e in result["errors"] if e["code"] == "ATTACHMENT_URL_INVALID"}, {"Q2"})
        self.assertEqual({e["unit_id"] for e in result["deferred"] if e["code"] == "MEDIA_DEPLOYMENT_REQUIRED"}, {"Q1"})
        data["items"][0]["attachments"].extend(other["attachments"])
        self.put(data)
        result = format_gate.run(self.root)
        self.assertFalse(result["passed"])
        self.assertFalse(any(e["code"] == "MEDIA_DEPLOYMENT_REQUIRED" for e in result["deferred"]))

    def test_extra_or_unattributed_url_errors_cannot_be_deferred(self):
        url = "01_source/media/valid.png"
        self.media(url)
        data = batch()
        data["items"][0]["attachments"] = [{"kind": "image", "url": url}]
        self.put(data)
        real = format_gate.upload_payload.build_payload
        for owner in ("Q1", "unknown", ""):
            with self.subTest(owner=owner):
                def build(*args, **kwargs):
                    built = real(*args, **kwargs)
                    built["errors"].append({"code": "ATTACHMENT_URL_INVALID", "unit_id": owner,
                                            "message": "Unmatched URL failure"})
                    return built

                with patch.object(format_gate.upload_payload, "build_payload", side_effect=build):
                    result = format_gate.run(self.root)
                self.assertFalse(result["passed"])
                self.assertTrue(any(e.get("unit_id") == owner and e["code"] == "ATTACHMENT_URL_INVALID"
                                    for e in result["errors"]))
                if owner == "Q1":
                    self.assertFalse(any(e["code"] == "MEDIA_DEPLOYMENT_REQUIRED" for e in result["deferred"]))

    def test_child_local_attachment_and_other_attachment_errors(self):
        url = "02_audio/child.mp3"
        self.media(url)
        data = batch()
        data["items"].append(group())
        child = data["items"][1]["subquestions"][0]
        child["attachments"] = [{"kind": "audio", "url": url}]
        self.put(data)
        result = format_gate.run(self.root)
        self.assertTrue(result["passed"], result)
        self.assertEqual({e["unit_id"] for e in result["deferred"] if e["code"] == "MEDIA_DEPLOYMENT_REQUIRED"}, {"Q2/2"})
        child["attachments"][0].update(kind="video", placement="option")
        self.put(data)
        result = format_gate.run(self.root)
        self.assertTrue({"ATTACHMENT_KIND_INVALID", "ATTACHMENT_OPTION_INVALID"}.issubset(self.codes(result)))
        # Colliding builder owner strings cannot justify URL error deferral.
        data["items"][0]["id"] = "Q2/2"
        self.put(data)
        result = format_gate.run(self.root)
        self.assertIn("ATTACHMENT_URL_INVALID", self.codes(result))

    def test_local_attachment_changes_during_build_block(self):
        url = "01_source/media/mutable.png"
        target = self.media(url)
        data = batch()
        data["items"][0]["attachments"] = [{"kind": "image", "url": url}]
        self.put(data)
        real = format_gate.upload_payload.build_payload
        for change in (lambda: target.write_bytes(b"changed"), lambda: target.unlink()):
            target.write_bytes(b"initial")

            def build(*args, **kwargs):
                built = real(*args, **kwargs)
                change()
                return built

            with patch.object(format_gate.upload_payload, "build_payload", side_effect=build):
                self.assertIn("LOCAL_ATTACHMENT_CHANGED", self.codes(format_gate.run(self.root)))

    def test_unvalidated_legacy_media_deployment_error_remains_blocking(self):
        data = batch()
        data["items"][0]["images"] = ["missing.png"]
        self.put(data)
        result = format_gate.run(self.root)
        self.assertFalse(result["passed"])
        self.assertIn("MEDIA_DEPLOYMENT_REQUIRED", self.codes(result))

    def test_only_known_manifest_fallback_and_no_invented_source(self):
        data = batch()
        data.pop("batch_id")
        data.pop("name")
        self.put(data)
        result = format_gate.run(self.root)
        self.assertIn("BATCH_BATCH_ID_REQUIRED", self.codes(result))
        self.assertIn("BATCH_NAME_REQUIRED", self.codes(result))
        manifest = self.root / "workflow/manifest.json"
        manifest.parent.mkdir()
        manifest.write_text(json.dumps({"workflow_id": "known-workflow", "meta": {"paper_title": "明确卷名"}}))
        result = format_gate.run(self.root)
        self.assertTrue(result["passed"], result["errors"])
        self.assertEqual(result["dry_run_options"], {"batch_id": "known-workflow", "name": "明确卷名"})
        self.assertIn("workflow/manifest.json", result["input_sha256"])
        data["items"][0].pop("source")
        self.put(data)
        self.assertIn("MISSING_SOURCE", self.codes(format_gate.run(self.root)))
        data["batch_id"] = 3
        self.put(data)
        result = format_gate.run(self.root)
        self.assertIn("BATCH_BATCH_ID_REQUIRED", self.codes(result))
        self.assertNotIn("batch_id", result["dry_run_options"])

    def test_placeholders_remain_verbatim(self):
        for placeholder in ("无", "未提供", "无答案", "无解析", ""):
            data = batch()
            data["items"][0].update(answer=placeholder, explanation=placeholder, articleSummary=placeholder)
            self.put(data)
            raw = self.final.read_bytes()
            result = format_gate.run(self.root)
            self.assertTrue(result["passed"], result["errors"])
            self.assertEqual(self.final.read_bytes(), raw)

    def test_symlink_inputs_ancestors_and_output_rejected(self):
        self.put()
        target = self.root / "actual.json"
        target.write_bytes(self.final.read_bytes())
        self.final.unlink()
        self.final.symlink_to(target)
        self.assertFalse(format_gate.run(self.root)["passed"])
        self.final.unlink()
        self.put()
        outside = self.root / "outside"
        outside.mkdir()
        review = self.root / "07_review"
        (review / "upload-format.json").unlink()
        review.rmdir()
        review.symlink_to(outside, target_is_directory=True)
        result = format_gate.run(self.root)
        self.assertIn("FORMAT_REPORT_WRITE_FAILED", self.codes(result))
        self.assertEqual(list(outside.iterdir()), [])

    def test_symlinked_input_directory_and_manifest_rejected(self):
        self.put()
        real_folder = self.root / "real-input"
        self.final.parent.rename(real_folder)
        self.final.parent.symlink_to(real_folder, target_is_directory=True)
        result = format_gate.run(self.root)
        self.assertIn("FORMAT_INPUT_INVALID", self.codes(result))
        self.assertIsNone(result["final_sha256"])
        self.final.parent.unlink()
        real_folder.rename(self.final.parent)
        data = batch()
        data.pop("batch_id")
        self.put(data)
        target = self.root / "manifest-real.json"
        target.write_text('{"workflow_id":"known"}')
        manifest = self.root / "workflow/manifest.json"
        manifest.parent.mkdir()
        manifest.symlink_to(target)
        self.assertIn("FORMAT_INPUT_INVALID", self.codes(format_gate.run(self.root)))

    def test_atomic_report_and_concurrent_source_change(self):
        self.put()
        replace = format_gate.os.replace
        with patch.object(format_gate.os, "replace", wraps=replace) as atomic:
            result = format_gate.run(self.root)
        self.assertTrue(result["passed"])
        self.assertEqual(atomic.call_count, 1)
        self.assertEqual(Path(atomic.call_args.args[1]), self.root / format_gate.REPORT_PATH)
        self.assertEqual(set(result["rule_sha256"]), {p.name for p in format_gate.RULE_FILES})
        real_build = format_gate.upload_payload.build_payload

        def changed(*args, **kwargs):
            built = real_build(*args, **kwargs)
            self.final.write_bytes(b'{"items":[]}')
            return built

        with patch.object(format_gate.upload_payload, "build_payload", side_effect=changed):
            result = format_gate.run(self.root)
        self.assertIn("FINAL_BATCH_CHANGED", self.codes(result))
        self.assertFalse(result["passed"])

    def test_final_format_profile_runs_at_upload_boundary_as_warnings(self):
        self.put()
        observed = {}

        def fake_run(*args, **kwargs):
            observed["args"] = args
            observed["kwargs"] = kwargs
            return {"passed": False, "checks": [{
                "check": "排版 check_text", "script": "scripts/06-upload/check_text.py",
                "passed": False, "error": "格式提示", "warnings": []}],
                    "blocking_failures": ["排版 check_text"], "warnings": ["排版 check_text"],
                    "skipped": []}

        with patch.object(format_gate.checks, "run_all", side_effect=fake_run):
            result = format_gate.run(self.root)
        self.assertTrue(result["passed"], result["errors"])
        self.assertEqual(observed["kwargs"].get("profile"), "format")
        self.assertTrue(any(row.get("code") == "FINAL_FORMAT_CHECK_FAILED"
                            for row in result["warnings"]))
        self.assertFalse(any(row.get("code") == "FINAL_FORMAT_CHECK_FAILED"
                             for row in result["errors"]))
        self.assertIn("format_checks", result)

    def test_final_format_profile_execution_failure_is_explicit_warning(self):
        self.put()
        with patch.object(format_gate.checks, "run_all",
                          side_effect=RuntimeError("checker unavailable")):
            result = format_gate.run(self.root)
        self.assertTrue(result["passed"], result["errors"])
        self.assertTrue(any(row.get("code") == "FINAL_FORMAT_CHECK_EXECUTION_FAILED"
                            for row in result["warnings"]))

    def test_cli_exit_codes_and_source_change_sha(self):
        self.put()
        original = format_gate.run(self.root)["final_sha256"]
        root = Path(__file__).resolve().parents[1]
        good = subprocess.run([sys.executable, "-m", "workflow.format_gate", "--batch-dir", str(self.root)],
                              cwd=root, capture_output=True, text=True, env=os.environ.copy())
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertTrue(json.loads(good.stdout)["passed"])
        self.final.write_bytes(b'{"items":[]}')
        bad = subprocess.run([sys.executable, "-m", "workflow.format_gate", "--batch-dir", str(self.root)],
                             cwd=root, capture_output=True, text=True, env=os.environ.copy())
        self.assertEqual(bad.returncode, 1, bad.stderr)
        self.assertNotEqual(json.loads(bad.stdout)["final_sha256"], original)


if __name__ == "__main__":
    unittest.main()
