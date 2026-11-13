"""Read-only repair tools use disposable source trees, never real batches/models."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import operations as op, operations_development as dev


class SourceToolTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workflow = self.root / "app" / "workflow"
        self.workflow.mkdir(parents=True)
        self.path = self.workflow / "fixture.py"
        self.relative = "app/workflow/fixture.py"
        self.job = {"workspace": str(self.root)}

    def source(self, text):
        self.path.write_bytes(text.encode("utf-8"))

    def test_unicode_pages_reconstruct_source_with_exact_byte_cursors(self):
        text = "原文😀\r\n" * 5000
        self.source(text)
        offset, parts = 0, []
        while True:
            page = dev.read(self.job, self.relative, offset=offset, limit=499)
            self.assertLessEqual(len(page["text"]), 499)
            self.assertNotIn("\ufffd", page["text"])
            self.assertEqual(page["offset_unit"], "bytes")
            self.assertEqual(page["offset"], offset)
            parts.append(page["text"])
            self.assertEqual(page["next_offset"], len("".join(parts).encode("utf-8")))
            offset = page["next_offset"]
            if page["eof"]:
                break
        self.assertEqual("".join(parts), text)
        self.assertEqual(page["sha256"], hashlib.sha256(text.encode()).hexdigest())

    def test_legacy_misaligned_byte_offset_advances_without_garbling(self):
        self.source("甲乙abc")
        page = dev.read(self.job, self.relative, offset=1, limit=1)
        self.assertEqual((page["requested_offset"], page["offset"], page["next_offset"]), (1, 3, 6))
        self.assertEqual(page["text"], "乙")
        self.assertEqual(dev.read(self.job, self.relative, offset=100)["text"], "")

    def test_read_cap_and_numbered_line_pagination(self):
        self.source("a\n" * 7000)
        self.assertEqual(len(dev.read(self.job, self.relative, limit=24000)["text"]), 12000)
        first = dev.read_lines(self.job, self.relative, start_line=3, limit=2)
        second = dev.read_lines(self.job, self.relative, start_line=first["next_line"], limit=2)
        self.assertEqual([row["line"] for row in first["lines"] + second["lines"]], [3, 4, 5, 6])
        self.assertFalse(first["eof"])
        self.assertTrue(dev.read_lines(self.job, self.relative, start_line=7001)["eof"])
        self.source("x" * 12001)
        with self.assertRaisesRegex(ValueError, "line_exceeds_read_limit"):
            dev.read_lines(self.job, self.relative)

    def test_search_literal_and_cross_file_cursor_preserve_all_matches(self):
        self.source("before\nneedle.*\nafter\nneedle.*\n")
        (self.workflow / "z_fixture.py").write_text("needle.*\n", encoding="utf-8")
        (self.workflow / "operations.py").write_text("needle.*\n", encoding="utf-8")
        (self.root / "credentials.json").write_text("needle.*", encoding="utf-8")
        results, cursor = [], None
        while True:
            page = dev.search(self.job, "needle.*", max_matches=1, cursor=cursor, context=1)
            results.extend((row["path"], row["line"]) for row in page["matches"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(results, [(self.relative, 2), (self.relative, 4), ("app/workflow/z_fixture.py", 1)])
        self.assertEqual(dev.search(self.job, "n.*e")["matches"], [])

    def test_tools_refuse_traversal_locked_paths_and_links(self):
        self.source("safe")
        for relative in ("../outside.py", "C:/outside.py", "app/workflow/operations.py", "app/references/rules.json"):
            for call in (lambda: dev.read(self.job, relative),
                         lambda: dev.read_lines(self.job, relative),
                         lambda: dev.search(self.job, "safe", relative=relative)):
                with self.assertRaises(ValueError):
                    call()
        link = self.workflow / "linked.py"
        link.symlink_to(self.path)
        for call in (lambda: dev.read(self.job, "app/workflow/linked.py"),
                     lambda: dev.read_lines(self.job, "app/workflow/linked.py"),
                     lambda: dev.search(self.job, "safe", relative="app/workflow/linked.py")):
            with self.assertRaisesRegex(ValueError, "symlink_refused"):
                call()
        self.assertEqual([row["path"] for row in dev.search(self.job, "safe")["matches"]], [self.relative])

    def test_search_validation_and_bounded_long_line_context(self):
        self.source("needle" + "x" * 15000)
        row = dev.search(self.job, "needle")["matches"][0]
        self.assertTrue(row["context"][0]["truncated"])
        self.assertEqual(len(row["context"][0]["text"]), 1000)
        for query in ("", "x" * 501, "a\nb", None):
            with self.assertRaisesRegex(ValueError, "search_query_invalid"):
                dev.search(self.job, query)
        with self.assertRaisesRegex(ValueError, "search_cursor_invalid"):
            dev.search(self.job, "needle", cursor={"path": self.relative})


class SourceToolActionTests(unittest.TestCase):
    """Exercise admission, dispatch and persisted receipts with real temporary files."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.root = base / "batches"
        self.folder = self.root / "fixture"
        workflow = self.folder / "workflow"
        workflow.mkdir(parents=True)
        (workflow / "manifest.json").write_text(json.dumps({
            "stages": {"validate": {"status": "failed"}}, "inputs": {}}), encoding="utf-8")
        (workflow / "self-heal.json").write_text(json.dumps({"events": [{
            "stage": "validate", "detail": "fixture failure", "signature": "fixture",
            "outcome": "repair_failed", "attempt": 1}]}), encoding="utf-8")
        for target, value in (("_snapshot", "fixture-snapshot"),
                              ("_code_fingerprint", "fixture-code")):
            patched = patch.object(op, target, return_value=value)
            patched.start()
            self.addCleanup(patched.stop)
        candidate = patch.object(op.self_heal, "_candidate", return_value=("validate", "fixture failure"))
        candidate.start()
        self.addCleanup(candidate.stop)
        source = base / "isolated" / "app" / "workflow"
        source.mkdir(parents=True)
        self.relative = "app/workflow/fixture.py"
        self.text = "开头\nneedle.* 一\nneedle.* 二\n结尾\n"
        (source / "fixture.py").write_text(self.text, encoding="utf-8")
        self.job = {"job_id": "fixture-job", "workspace": str(source.parents[1])}
        self.record = {"status": "workspace_ready", "path": str(base / "fixture-job.json")}
        mocked_job = patch.object(op, "_code_job", return_value=(self.record, self.job))
        self.code_job = mocked_job.start()
        self.addCleanup(mocked_job.stop)

    def claim(self, mode):
        incident = op.incidents(self.root)["incidents"][0]
        return op.claim(self.root, self.folder, {"incident_id": incident["incident_id"],
                        "session_id": "fixture-session", "mode": mode})

    def request(self, claim, request_id, **data):
        return {**{key: claim[key] for key in ("incident_id", "session_id", "attempt_id")},
                "action": "code_repair", "request_id": request_id, "input": data}

    def test_investigation_refuses_search_and_line_read_before_job_access(self):
        claim = self.claim("investigation")
        for operation in ("search", "read_lines"):
            with self.subTest(operation=operation), self.assertRaisesRegex(ValueError, "repair_claim_required"):
                op.action(self.root, self.folder, self.request(
                    claim, operation, operation=operation, path=self.relative, query="needle.*"))
        self.code_job.assert_not_called()
        self.assertEqual(op._load(self.folder)["receipts"], [])
        self.assertFalse(Path(self.record["path"]).exists())

    def test_repair_search_receipts_accept_object_cursor_and_are_idempotent(self):
        claim = self.claim("repair")
        first_request = self.request(claim, "search-first", operation="search",
                                     query="needle.*", max_matches=1, context=0)
        first = op.action(self.root, self.folder, first_request)
        self.assertEqual(first["status"], "completed")
        cursor = first["evidence"]["next_cursor"]
        self.assertEqual(cursor, {"path": self.relative, "start_line": 3})
        self.assertEqual(first["evidence"]["matches"][0]["line"], 2)
        second = op.action(self.root, self.folder, self.request(
            claim, "search-second", operation="search", query="needle.*",
            max_matches=1, context=0, cursor=cursor))
        self.assertEqual(second["status"], "completed")
        self.assertEqual(second["evidence"]["matches"][0]["line"], 3)
        self.assertIsNone(second["evidence"]["next_cursor"])
        accesses = self.code_job.call_count
        self.assertEqual(op.action(self.root, self.folder, first_request), first)
        self.assertEqual(self.code_job.call_count, accesses)
        persisted = op._load(self.folder)["receipts"]
        self.assertEqual(persisted[0]["evidence"]["next_cursor"], cursor)
        self.assertEqual(len(persisted), 2)
        self.assertEqual((Path(self.job["workspace"]) / self.relative).read_text(encoding="utf-8"), self.text)

    def test_repair_line_read_preserves_line_numbers_and_continuation_through_action(self):
        claim = self.claim("repair")
        first = op.action(self.root, self.folder, self.request(
            claim, "lines-first", operation="read_lines", path=self.relative, start_line=2, limit=1))
        self.assertEqual(first["status"], "completed")
        evidence = first["evidence"]
        self.assertEqual(evidence["lines"], [{"line": 2, "text": "needle.* 一\n"}])
        self.assertEqual(evidence["next_line"], 3)
        self.assertFalse(evidence["eof"])
        last = op.action(self.root, self.folder, self.request(
            claim, "lines-last", operation="read_lines", path=self.relative,
            start_line=evidence["next_line"], limit=80))
        self.assertEqual(last["status"], "completed")
        self.assertEqual([row["line"] for row in last["evidence"]["lines"]], [3, 4])
        self.assertTrue(last["evidence"]["eof"])
        self.assertEqual(last["evidence"]["status"], "workspace_ready")


if __name__ == "__main__":
    unittest.main()
