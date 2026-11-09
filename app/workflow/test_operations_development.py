# -*- coding: utf-8 -*-
"""Isolated code-repair workspace tests: disposable roots, no models, no user batches."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from workflow import operations_development as dev


class DevelopmentWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "source"
        self.workflow = self.root / "app" / "workflow"
        self.workflow.mkdir(parents=True)
        (self.workflow / "__init__.py").write_text("", encoding="utf-8")
        self.module = self.workflow / "fixture_mod.py"
        self.module.write_text("def answer():\n    return 1\n", encoding="utf-8")
        self.test_module = self.workflow / "test_fixture.py"
        self.test_module.write_text(
            "import os\nimport unittest\nfrom workflow import fixture_mod\n\n"
            "class Fixture(unittest.TestCase):\n"
            "    def test_answer(self):\n"
            "        self.assertEqual(fixture_mod.answer(), 1)\n"
            "    def test_no_credentials(self):\n"
            "        self.assertNotIn('DEPLOY_TOKEN', os.environ)\n"
            "        self.assertNotIn('MDU_API_KEY', os.environ)\n",
            encoding="utf-8")
        (self.workflow / "operations.py").write_text("# control plane\n", encoding="utf-8")
        (self.workflow / "contracts.py").write_text("# control plane\n", encoding="utf-8")
        (self.root / "workflows" / "paper").mkdir(parents=True)
        (self.root / "workflows" / "paper" / "batch.json").write_text("{\"user\": 1}", encoding="utf-8")
        (self.root / ".env").write_text("SECRET=1\n", encoding="utf-8")
        (self.root / "deploy.pem").write_text("PRIVATE", encoding="utf-8")
        self.jobs = base / "jobs"
        self.backups = base / "backups"

    def create(self):
        return dev.create(self.root, self.jobs)

    def test_create_copies_code_only_and_keeps_dirty_baseline(self):
        self.module.write_text("def answer():\n    return 2\n", encoding="utf-8")  # dirty, uncommitted
        job = self.create()
        workspace = Path(job["workspace"])
        self.assertTrue((workspace / "app" / "workflow" / "fixture_mod.py").is_file())
        self.assertFalse((workspace / "workflows").exists())
        self.assertFalse((workspace / ".env").exists())
        self.assertFalse((workspace / "deploy.pem").exists())
        self.assertEqual((workspace / "app" / "workflow" / "fixture_mod.py").read_text(encoding="utf-8"),
                         "def answer():\n    return 2\n")
        self.assertEqual(job["base"]["files"]["app/workflow/fixture_mod.py"]["bytes"],
                         len((workspace / "app" / "workflow" / "fixture_mod.py").read_bytes()))
        self.assertNotIn("app/workflow/operations.py", job["base"]["files"])
        self.assertTrue((self.jobs / (job["job_id"] + ".json")).is_file())

    def test_write_rejects_escapes_locked_control_plane_and_unexpected_state(self):
        job = self.create()
        with self.assertRaisesRegex(ValueError, "source_not_allowlisted"):
            dev.write(job, "app/workflow/operations.py", "x", expected_sha=None)
        with self.assertRaisesRegex(ValueError, "source_not_allowlisted"):
            dev.write(job, "../outside.py", "x", expected_sha=None)
        with self.assertRaisesRegex(ValueError, "source_not_allowlisted"):
            dev.write(job, "app/other.py", "x", expected_sha=None)
        with self.assertRaisesRegex(ValueError, "unsafe_path"):
            dev.read(job, "C:/Windows/win.ini")
        with self.assertRaisesRegex(ValueError, "unsafe_path"):
            dev.read(job, "app/workflow/../../x.py")
        with self.assertRaisesRegex(ValueError, "expected_sha_required"):
            dev.write(job, "app/workflow/test_fixture.py", "changed", expected_sha=None)
        with self.assertRaisesRegex(ValueError, "expected_sha_mismatch"):
            dev.write(job, "app/workflow/test_fixture.py", "changed", expected_sha="0" * 64)

    def test_symlink_inside_workspace_is_refused(self):
        job = self.create()
        target = Path(self.tmp.name) / "outside.txt"
        target.write_text("outside", encoding="utf-8")
        link = Path(job["workspace"]) / "app" / "workflow" / "test_link.py"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlink unavailable")
        with self.assertRaisesRegex(ValueError, "symlink_refused|path_escape"):
            dev.write(job, "app/workflow/test_link.py", "x", expected_sha=None)
        with self.assertRaisesRegex(ValueError, "symlink_refused|path_escape"):
            dev.read(job, "app/workflow/test_link.py")

    def test_tests_run_with_fixed_argv_and_stripped_credentials(self):
        os.environ["DEPLOY_TOKEN"] = "do-not-inherit"
        os.environ["MDU_API_KEY"] = "do-not-inherit"
        self.addCleanup(os.environ.pop, "DEPLOY_TOKEN", None)
        self.addCleanup(os.environ.pop, "MDU_API_KEY", None)
        job = self.create()
        record = dev.run_tests(job, ["workflow.test_fixture"])
        self.assertTrue(record["passed"], record["output"])
        self.assertIn("Ran 2 tests", record["output"])
        self.assertEqual(record["fingerprint"], dev.fingerprint(job))

    def test_failing_test_blocks_install_until_fixed(self):
        job = self.create()
        dev.write(job, "app/workflow/fixture_mod.py", "def answer():\n    return 3\n",
                  expected_sha=job["base"]["files"]["app/workflow/fixture_mod.py"]["sha256"])
        failed = dev.run_tests(job, ["workflow.test_fixture"])
        self.assertFalse(failed["passed"])
        with self.assertRaisesRegex(ValueError, "passing_tests_required"):
            dev.prepare_install(job)
        dev.write(job, "app/workflow/fixture_mod.py", "def answer():\n    return 1\n",
                  expected_sha=dev.diff(job)["files"][0]["new_sha256"])
        passed = dev.run_tests(job, ["workflow.test_fixture"])
        self.assertTrue(passed["passed"], passed["output"])
        plan = dev.prepare_install(job)
        self.assertEqual([c["path"] for c in plan["changes"]], ["app/workflow/fixture_mod.py"])

    def test_new_behavior_requires_its_own_tests_to_run(self):
        job = self.create()
        dev.write(job, "app/workflow/test_new.py", "import unittest\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n",
                  expected_sha=None)
        dev.run_tests(job, ["workflow.test_fixture"])
        self.assertTrue(dev.run_tests(job, ["workflow.test_fixture"])["passed"])
        with self.assertRaisesRegex(ValueError, "changed_tests_not_run"):
            dev.prepare_install(job)
        self.assertTrue(dev.run_tests(job, ["workflow.test_new", "workflow.test_fixture"])["passed"])
        self.assertEqual(dev.prepare_install(job)["changes"][0]["path"], "app/workflow/test_new.py")

    def test_edit_after_test_invalidates_the_evidence(self):
        job = self.create()
        self.assertTrue(dev.run_tests(job, ["workflow.test_fixture"])["passed"])
        dev.write(job, "app/workflow/fixture_mod.py", "def answer():\n    return 1  # edited\n",
                  expected_sha=job["base"]["files"]["app/workflow/fixture_mod.py"]["sha256"])
        with self.assertRaisesRegex(ValueError, "passing_tests_required"):
            dev.prepare_install(job)

    def test_install_is_disabled_unless_explicitly_authorized(self):
        job = self.create()
        dev.write(job, "app/workflow/fixture_mod.py", "def answer():\n    return 1  # patched\n",
                  expected_sha=job["base"]["files"]["app/workflow/fixture_mod.py"]["sha256"])
        self.assertTrue(dev.run_tests(job, ["workflow.test_fixture"])["passed"])
        self.assertEqual(dev.prepare_install(job)["changes"][0]["kind"], "production")
        with self.assertRaisesRegex(ValueError, "install_disabled"):
            dev.install(job, backup_dir=self.backups)

    def test_install_and_rollback_are_journaled_and_cas_checked(self):
        job = self.create()
        original = self.module.read_text(encoding="utf-8")
        dev.write(job, "app/workflow/fixture_mod.py",
                  "def answer():\n    return 1\n\ndef extra():\n    return 2\n",
                  expected_sha=job["base"]["files"]["app/workflow/fixture_mod.py"]["sha256"])
        self.assertTrue(dev.run_tests(job, ["workflow.test_fixture"])["passed"])
        plan = dev.prepare_install(job)
        installed = dev.install(job, backup_dir=self.backups, allow=True)
        self.assertEqual(installed["status"], "installed")
        self.assertIn("def extra()", self.module.read_text(encoding="utf-8"))
        journal = json.loads(Path(installed["journal"]).read_text(encoding="utf-8"))
        self.assertEqual(journal["status"], "installed")
        rolled = dev.rollback(job, backup_dir=self.backups)
        self.assertEqual(rolled["status"], "rolled_back")
        self.assertEqual(self.module.read_text(encoding="utf-8"), original)
        self.assertEqual(journal["plan_id"], plan["plan_id"])

    def test_install_refuses_source_that_changed_after_the_plan(self):
        job = self.create()
        dev.write(job, "app/workflow/fixture_mod.py", "def answer():\n    return 1\n\ndef extra():\n    return 2\n",
                  expected_sha=job["base"]["files"]["app/workflow/fixture_mod.py"]["sha256"])
        self.assertTrue(dev.run_tests(job, ["workflow.test_fixture"])["passed"])
        dev.prepare_install(job)
        self.module.write_text("def answer():\n    return 99\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "source_changed"):
            dev.install(job, backup_dir=self.backups, allow=True)
        self.assertEqual(self.module.read_text(encoding="utf-8"), "def answer():\n    return 99\n")

    def test_rollback_refuses_when_the_installed_file_changed_again(self):
        job = self.create()
        dev.write(job, "app/workflow/fixture_mod.py", "def answer():\n    return 1\n\ndef extra():\n    return 2\n",
                  expected_sha=job["base"]["files"]["app/workflow/fixture_mod.py"]["sha256"])
        self.assertTrue(dev.run_tests(job, ["workflow.test_fixture"])["passed"])
        dev.prepare_install(job)
        dev.install(job, backup_dir=self.backups, allow=True)
        self.module.write_text("def answer():\n    return 7\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "source_changed_after_install"):
            dev.rollback(job, backup_dir=self.backups)

    def test_deleting_a_production_file_is_refused(self):
        job = self.create()
        (Path(job["workspace"]) / "app" / "workflow" / "fixture_mod.py").unlink()
        with self.assertRaisesRegex(ValueError, "deletion_not_allowed"):
            dev.prepare_install(job, require_tests=False)

    def test_capture_includes_untracked_new_module(self):
        (self.workflow / "extra_mod.py").write_text("VALUE = 1\n", encoding="utf-8")
        captured = dev.capture(self.root)
        self.assertIn("app/workflow/extra_mod.py", captured["files"])
        self.assertTrue(dev.allowed("app/workflow/extra_mod.py"))
        self.assertFalse(dev.allowed("app/workflow/self_heal.py"))

    def test_copy_never_overlaps_the_source_tree(self):
        with self.assertRaisesRegex(ValueError, "copy_overlap"):
            dev.create(self.root, self.root / "app" / "jobs")


if __name__ == "__main__":
    unittest.main()
