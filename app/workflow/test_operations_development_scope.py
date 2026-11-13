"""Script repairs use disposable fake checkouts, never live batches or models."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import operations_development as dev


SCRIPT = "app/scripts/06-upload/audit_type_layout.py"
REGRESSION = "app/workflow/test_audit_type_layout.py"
TEST_NAME = "workflow.test_audit_type_layout"
TEST_SOURCE = '''import importlib.util
from pathlib import Path
import unittest

class Layout(unittest.TestCase):
    def test_script(self):
        path = Path(__file__).parents[1] / "scripts/06-upload/audit_type_layout.py"
        spec = importlib.util.spec_from_file_location("isolated_layout", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.audit(), 2)
'''


class ScriptRepairScopeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "source"
        self.jobs = self.base / "jobs"
        self.backups = self.base / "backups"
        self.put("app/workflow/__init__.py", "")
        self.put(SCRIPT, "def audit():\n    return 1\n")
        self.put("app/scripts/_common.py", "VALUE = 1\n")
        self.put("app/workflow/test_unrelated.py", "import unittest\nclass Other(unittest.TestCase):\n    def test_ok(self):\n        pass\n")
        self.put(REGRESSION, TEST_SOURCE.replace("module.audit(), 2", "module.audit(), 1"))
        for relative in dev.LOCKED:
            self.put(relative, "# locked control plane\n")
        dev.set_autonomy(self.root, enabled=False, authorization="Synthetic disabled fixture")

    def put(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def create(self):
        return dev.create(self.root, self.jobs)

    def repair(self):
        job = self.create()
        for relative, text in ((SCRIPT, "def audit():\n    return 2\n"),
                               (REGRESSION, TEST_SOURCE)):
            dev.write(job, relative, text, expected_sha=dev.read(job, relative)["sha256"])
        return job

    def passing(self, job):
        record = dev.run_tests(job, [TEST_NAME])
        self.assertTrue(record["passed"], record["output"])
        self.assertIn("Ran 1 test", record["output"])
        return record

    def test_capture_files_and_pristine_baseline_include_actual_python_scripts(self):
        expected = (self.root / SCRIPT).read_bytes()
        captured = dev.capture(self.root)
        self.assertIn(SCRIPT, dev.files(self.root))
        self.assertEqual(captured["contents"][SCRIPT], expected)
        self.assertIn("app/scripts/_common.py", captured["files"])
        job = self.create()
        self.assertEqual((Path(job["workspace"]) / ".base" / SCRIPT).read_bytes(), expected)
        self.assertEqual(dev.read(job, SCRIPT)["text"], expected.decode())
        self.assertIn(SCRIPT, [row["path"] for row in dev.search(job, "def audit")["matches"]])
        self.assertEqual(dev.read_lines(job, SCRIPT)["lines"][0]["line"], 1)
        self.assertFalse((Path(job["workspace"]) / dev.AUTONOMY_CONFIG).exists())

    def test_script_and_regression_complete_test_plan_install_and_rollback(self):
        job = self.repair()
        self.passing(job)
        delta = dev.diff(job)
        self.assertEqual({row["path"]: row["kind"] for row in delta["files"]},
                         {SCRIPT: "production", REGRESSION: "test"})
        self.assertIn("a/" + SCRIPT, delta["patch"])
        plan = dev.prepare_install(job)
        self.assertEqual(plan["code_fingerprint"], dev.fingerprint(job))
        self.assertEqual((self.root / SCRIPT).read_text(), "def audit():\n    return 1\n")
        with self.assertRaisesRegex(ValueError, "install_disabled"):
            dev.install(job, backup_dir=self.backups)
        installed = dev.install(job, backup_dir=self.backups, allow=True)
        self.assertEqual(installed["status"], "installed")
        self.assertTrue(installed["restart_required"])
        self.assertEqual((self.root / SCRIPT).read_text(), "def audit():\n    return 2\n")
        journal = json.loads(Path(installed["journal"]).read_text())
        self.assertEqual(journal["status"], "installed")
        self.assertIn(SCRIPT, journal["backups"])
        self.assertEqual(dev.rollback(job, backup_dir=self.backups)["status"], "rolled_back")
        self.assertEqual((self.root / SCRIPT).read_text(), "def audit():\n    return 1\n")

    def test_script_edits_change_capture_and_test_fingerprints(self):
        before = dev.capture(self.root)["code_fingerprint"]
        self.put(SCRIPT, "def audit():\n    return 3\n")
        self.assertNotEqual(before, dev.capture(self.root)["code_fingerprint"])
        job = self.repair()
        self.passing(job)
        passed_fingerprint = dev.fingerprint(job)
        candidate = Path(job["workspace"]) / SCRIPT
        candidate.write_text("def audit():\n    return 2  # changed after test\n")
        self.assertNotEqual(passed_fingerprint, dev.fingerprint(job))
        with self.assertRaisesRegex(ValueError, "passing_tests_required"):
            dev.prepare_install(job)

    def test_script_edits_after_plan_invalidate_install(self):
        job = self.repair()
        self.passing(job)
        dev.prepare_install(job)
        (Path(job["workspace"]) / SCRIPT).write_text("def audit():\n    return 3\n")
        with self.assertRaisesRegex(ValueError, "current_install_plan_required"):
            dev.install(job, backup_dir=self.backups, allow=True)
        self.assertFalse((self.root / dev.RELEASE_MARKER).exists())

    def test_script_source_change_refuses_plan_and_install_with_cas(self):
        job = self.repair()
        self.passing(job)
        dev.prepare_install(job)
        self.put(SCRIPT, "def audit():\n    return 9  # concurrent author\n")
        for operation in (lambda: dev.prepare_install(job),
                          lambda: dev.install(job, backup_dir=self.backups, allow=True)):
            with self.assertRaisesRegex(ValueError, "source_changed"):
                operation()
        self.assertIn("concurrent author", (self.root / SCRIPT).read_text())

    def test_failing_script_regression_still_blocks_plan(self):
        job = self.create()
        dev.write(job, SCRIPT, "def audit():\n    return 2\n", expected_sha=dev.read(job, SCRIPT)["sha256"])
        self.assertFalse(dev.run_tests(job, [TEST_NAME])["passed"])
        with self.assertRaisesRegex(ValueError, "passing_tests_required"):
            dev.prepare_install(job)

    def test_unchanged_test_execution_cannot_prove_changed_regression_was_run(self):
        job = self.repair()
        record = dev.run_tests(job, ["workflow.test_unrelated"])
        self.assertTrue(record["passed"], record["output"])
        with self.assertRaisesRegex(ValueError, "changed_tests_not_run"):
            dev.prepare_install(job)
        self.passing(job)
        self.assertEqual(set(dev.prepare_install(job)["tests"]), {TEST_NAME})

    def test_script_write_hash_and_deletion_protections_remain(self):
        job = self.create()
        with self.assertRaisesRegex(ValueError, "expected_sha_required"):
            dev.write(job, SCRIPT, "# overwrite")
        with self.assertRaisesRegex(ValueError, "expected_sha_mismatch"):
            dev.write(job, SCRIPT, "# overwrite", expected_sha="0" * 64)
        (Path(job["workspace"]) / SCRIPT).unlink()
        with self.assertRaisesRegex(ValueError, "deletion_not_allowed"):
            dev.prepare_install(job, require_tests=False)

    def test_secret_locked_js_config_authorization_and_escape_paths_stay_closed(self):
        refused = ["app/scripts/06-upload/secrets.py", "app/scripts/credentials.py",
                   "app/scripts/passwords/audit.py", "app/scripts/.hidden.py",
                   "app/scripts/06-upload/audit_type_layout.js", "app/scripts/deploy.key",
                   "lib/client.js", "backend/route_pick.py", "config/model-router.json",
                   dev.AUTONOMY_CONFIG, dev.RELEASE_MARKER,
                   "app/scripts/../workflow/runner.py", "../outside.py", "C:/outside.py"]
        for relative in refused[:3]:
            self.put(relative, "SECRET_CANARY = True\n")
        job = self.create()
        for relative in refused + list(dev.LOCKED):
            with self.subTest(relative=relative):
                self.assertFalse(dev.allowed(relative))
                self.assertNotIn(relative, dev.files(self.root))
                self.assertNotIn(relative, job["base"]["files"])
                for operation in (lambda: dev.read(job, relative),
                                  lambda: dev.write(job, relative, "# denied")):
                    with self.assertRaises(ValueError):
                        operation()
        self.assertEqual(dev.search(job, "SECRET_CANARY")["matches"], [])

    def test_symlinked_script_files_and_parent_directories_are_never_enumerated(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "audit.py").write_text("# outside\n")
        link = self.root / "app/scripts/linked"
        link.symlink_to(outside, target_is_directory=True)
        (self.root / "app/scripts/link.py").symlink_to(outside / "audit.py")
        self.assertNotIn("app/scripts/linked/audit.py", dev.files(self.root))
        self.assertNotIn("app/scripts/link.py", dev.capture(self.root)["files"])
        job = self.create()
        workspace = Path(job["workspace"])
        (workspace / "app/scripts/linked").symlink_to(outside, target_is_directory=True)
        for operation in (lambda: dev.read(job, "app/scripts/linked/audit.py"),
                          lambda: dev.write(job, "app/scripts/linked/audit.py", "# denied")):
            with self.assertRaisesRegex(ValueError, "symlink_refused"):
                operation()
        self.assertEqual((outside / "audit.py").read_text(), "# outside\n")

    def test_autonomous_script_install_keeps_strict_changed_test_evidence(self):
        dev.set_autonomy(self.root, enabled=True, authorization="Synthetic fixture authorization")
        job = self.repair()
        self.passing(job)
        dev.prepare_install(job)
        self.assertTrue(dev.run_tests(job, ["workflow.test_unrelated"])["passed"])
        with self.assertRaisesRegex(ValueError, "changed_tests_not_run"):
            dev.install(job, backup_dir=self.backups)
        self.assertFalse((self.root / dev.RELEASE_MARKER).exists())
        self.passing(job)
        dev.prepare_install(job)
        self.assertEqual(dev.install(job, backup_dir=self.backups)["authorization"]["mode"], "local_autonomy")


if __name__ == "__main__":
    unittest.main()
