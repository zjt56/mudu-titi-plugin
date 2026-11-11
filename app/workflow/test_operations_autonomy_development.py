# -*- coding: utf-8 -*-
"""Local autonomy authorization; all installations target disposable checkouts."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import operations_development as dev


class AutonomousDevelopmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "source"
        self.workflow = self.root / "app" / "workflow"
        self.workflow.mkdir(parents=True)
        (self.workflow / "__init__.py").write_text("", encoding="utf-8")
        self.module = self.workflow / "fixture_mod.py"
        self.module.write_text("VALUE = 1\n", encoding="utf-8")
        (self.workflow / "test_fixture.py").write_text(
            "import unittest\nfrom workflow import fixture_mod\n"
            "class Fixture(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(fixture_mod.VALUE, 1)\n", encoding="utf-8")
        self.jobs = self.base / "jobs"
        self.backups = self.base / "backups"
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def enable(self):
        return dev.set_autonomy(self.root, enabled=True,
                                authorization="User explicitly authorized local autonomous repairs")

    def planned(self, *, strict=True):
        job = dev.create(self.root, self.jobs)
        relative = "app/workflow/fixture_mod.py"
        dev.write(job, relative, "VALUE = 1  # fixed\n",
                  expected_sha=job["base"]["files"][relative]["sha256"])
        if strict:
            result = dev.run_tests(job, ["workflow.test_fixture"])
            self.assertTrue(result["passed"], result["output"])
        dev.prepare_install(job, require_tests=strict, require_regression=strict)
        return job

    def test_missing_config_defaults_to_disabled_without_creating_state(self):
        self.assertFalse(dev.autonomy_config(self.root)["enabled"])
        self.assertFalse(dev.install_authorization(self.root)["allowed"])
        self.assertFalse((self.root / dev.AUTONOMY_CONFIG).exists())

    def test_enable_is_persistent_and_bound_to_one_checkout(self):
        config = self.enable()
        self.assertEqual(Path(config["config_path"]), self.root / dev.AUTONOMY_CONFIG)
        self.assertTrue(dev.autonomy_config(self.root)["enabled"])
        result = dev.install_authorization(self.root)
        self.assertTrue(result["allowed"])
        self.assertTrue(result["autonomous"])
        self.assertEqual(result["mode"], "local_autonomy")
        other = self.base / "other"
        other.mkdir()
        self.assertFalse(dev.install_authorization(other)["allowed"])
        (other / dev.AUTONOMY_CONFIG).write_bytes(Path(config["config_path"]).read_bytes())
        with self.assertRaisesRegex(ValueError, "autonomy_config_invalid"):
            dev.install_authorization(other)

    def test_disable_revokes_automatic_install_and_legacy_remains_opt_in(self):
        self.enable()
        dev.set_autonomy(self.root, enabled=False, authorization="User disabled autonomy")
        self.assertFalse(dev.install_authorization(self.root)["allowed"])
        os.environ["MD_OPERATIONS_INSTALL"] = "1"
        self.assertFalse(dev.install_authorization(self.root)["allowed"])
        result = dev.install_authorization(self.root, confirm=True)
        self.assertTrue(result["allowed"])
        self.assertFalse(result["autonomous"])
        self.assertEqual(result["mode"], "legacy_confirmed")
        os.environ.pop("MD_OPERATIONS_INSTALL")
        self.assertFalse(dev.install_authorization(self.root, confirm=True)["allowed"])

    def test_truthy_values_do_not_authorize_legacy_install(self):
        os.environ["MD_OPERATIONS_INSTALL"] = "true"
        self.assertFalse(dev.install_authorization(self.root, confirm=True)["allowed"])
        os.environ["MD_OPERATIONS_INSTALL"] = "1"
        self.assertFalse(dev.install_authorization(self.root, confirm="true")["allowed"])

    def test_invalid_config_does_not_fall_back_to_legacy_switch(self):
        os.environ["MD_OPERATIONS_INSTALL"] = "1"
        path = self.root / dev.AUTONOMY_CONFIG
        for value in ("{broken", "[]", '{"enabled": true}',
                      json.dumps({"schema": dev.AUTONOMY_SCHEMA, "enabled": "true",
                                  "source_root": str(self.root), "authorization": "yes"})):
            with self.subTest(value=value):
                path.write_text(value, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "autonomy_config_invalid"):
                    dev.install_authorization(self.root, confirm=True)

    def test_configuration_requires_explicit_boolean_and_authorization(self):
        for enabled in (1, "true", None):
            with self.assertRaisesRegex(ValueError, "autonomy_enabled_must_be_boolean"):
                dev.set_autonomy(self.root, enabled=enabled, authorization="yes")
        for reason in ("", "   ", None):
            with self.assertRaisesRegex(ValueError, "autonomy_authorization_required"):
                dev.set_autonomy(self.root, enabled=True, authorization=reason)
        with self.assertRaisesRegex(ValueError, "source_root_invalid"):
            dev.set_autonomy(self.base / "missing", enabled=True, authorization="yes")

    def test_config_is_not_copied_or_model_editable(self):
        self.enable()
        job = dev.create(self.root, self.jobs)
        self.assertFalse((Path(job["workspace"]) / dev.AUTONOMY_CONFIG).exists())
        self.assertFalse(dev.allowed(dev.AUTONOMY_CONFIG))
        with self.assertRaisesRegex(ValueError, "source_not_allowlisted"):
            dev.write(job, dev.AUTONOMY_CONFIG, "{}")
        self.assertFalse(dev.install_authorization(job["workspace"])["allowed"])

    def test_config_symlink_is_refused_for_read_and_write(self):
        outside = self.base / "outside.json"
        outside.write_text("do not overwrite", encoding="utf-8")
        path = self.root / dev.AUTONOMY_CONFIG
        try:
            path.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlink unavailable")
        with self.assertRaisesRegex(ValueError, "symlink_refused"):
            dev.autonomy_config(self.root)
        with self.assertRaisesRegex(ValueError, "symlink_refused"):
            self.enable()
        self.assertEqual(outside.read_text(), "do not overwrite")

    def test_test_environment_has_no_install_switch_or_host_secrets(self):
        os.environ.update(MD_OPERATIONS_INSTALL="1", DSH_TEST_VALUE="secret",
                          PROVIDER_API_KEY="secret", PATH="safe-path")
        env = dev._clean_env(self.base, self.root / "app")
        self.assertNotIn("MD_OPERATIONS_INSTALL", env)
        self.assertNotIn("DSH_TEST_VALUE", env)
        self.assertNotIn("PROVIDER_API_KEY", env)
        self.assertEqual(env["PATH"], "safe-path")

    def test_autonomous_install_needs_no_repeated_confirmation_or_environment(self):
        self.enable()
        user_data = self.root / "workflows" / "paper" / "data.json"
        user_data.parent.mkdir(parents=True)
        user_data.write_bytes(b'{"original": true}')
        job = self.planned()
        result = dev.install(job, backup_dir=self.backups)
        self.assertEqual(result["status"], "installed")
        self.assertTrue(result["restart_required"])
        self.assertEqual(result["authorization"]["mode"], "local_autonomy")
        self.assertIn("# fixed", self.module.read_text())
        self.assertEqual(user_data.read_bytes(), b'{"original": true}')
        journal = json.loads(Path(result["journal"]).read_text())
        self.assertEqual(journal["authorization"]["config_path"], str(self.root / dev.AUTONOMY_CONFIG))
        marker = json.loads((self.root / dev.RELEASE_MARKER).read_text())
        self.assertEqual(marker["state"], "installed")
        self.assertEqual(marker["journal"], result["journal"])
        self.assertEqual(dev.rollback(job, backup_dir=self.backups)["status"], "rolled_back")
        self.assertEqual(self.module.read_text(), "VALUE = 1\n")

    def test_revocation_is_checked_again_at_install_time(self):
        self.enable()
        job = self.planned(strict=False)
        dev.set_autonomy(self.root, enabled=False, authorization="User revoked authorization")
        with self.assertRaisesRegex(ValueError, "install_disabled"):
            dev.install(job, backup_dir=self.backups)
        self.assertEqual(self.module.read_text(), "VALUE = 1\n")
        self.assertFalse((self.root / dev.RELEASE_MARKER).exists())

    def test_autonomy_never_waives_passing_test_evidence(self):
        self.enable()
        job = self.planned(strict=False)
        original_plan = job["plan"]
        with self.assertRaisesRegex(ValueError, "passing_tests_required"):
            dev.install(job, backup_dir=self.backups)
        self.assertIs(job["plan"], original_plan)
        self.assertEqual(self.module.read_text(), "VALUE = 1\n")
        self.assertFalse((self.root / dev.RELEASE_MARKER).exists())

    def test_autonomy_preserves_fingerprint_and_cas_checks(self):
        self.enable()
        job = self.planned()
        self.module.write_text("VALUE = 3  # concurrent author\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "source_changed"):
            dev.install(job, backup_dir=self.backups)
        self.assertIn("concurrent author", self.module.read_text())
        self.assertFalse((self.root / dev.RELEASE_MARKER).exists())

    def test_autonomy_refuses_workspace_edits_after_passing_tests(self):
        self.enable()
        job = self.planned()
        candidate = Path(job["workspace"]) / "app" / "workflow" / "fixture_mod.py"
        candidate.write_text("VALUE = 999\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "current_install_plan_required"):
            dev.install(job, backup_dir=self.backups)
        self.assertEqual(self.module.read_text(), "VALUE = 1\n")

    def test_autonomy_does_not_waive_changed_test_execution(self):
        self.enable()
        job = self.planned()
        relative = "app/workflow/test_new.py"
        dev.write(job, relative, "import unittest\nclass New(unittest.TestCase):\n"
                  "    def test_ok(self):\n        pass\n")
        self.assertTrue(dev.run_tests(job, ["workflow.test_fixture"])["passed"])
        with self.assertRaisesRegex(ValueError, "changed_tests_not_run"):
            dev.prepare_install(job)
        for relative in dev.LOCKED:
            self.assertFalse(dev.allowed(relative), relative)
            with self.assertRaisesRegex(ValueError, "source_not_allowlisted"):
                dev.write(job, relative, "# locked\n")

    def test_autonomous_install_failure_rolls_back_changed_and_new_files(self):
        self.enable()
        job = self.planned()
        new_relative = "app/workflow/new_fixture.py"
        dev.write(job, new_relative, "VALUE = 2\n")
        self.assertTrue(dev.run_tests(job, ["workflow.test_fixture"])["passed"])
        dev.prepare_install(job)
        marker = dev._release_marker
        def fail_installed(root, state, **extra):
            if state == "installed":
                raise OSError("injected release marker failure")
            return marker(root, state, **extra)
        with patch.object(dev, "_release_marker", side_effect=fail_installed):
            with self.assertRaisesRegex(OSError, "release marker failure"):
                dev.install(job, backup_dir=self.backups)
        self.assertEqual(self.module.read_text(), "VALUE = 1\n")
        self.assertFalse((self.root / new_relative).exists())
        self.assertEqual(job["install"]["status"], "rolled_back")
        release = json.loads((self.root / dev.RELEASE_MARKER).read_text())
        self.assertEqual(release["state"], "rolled_back")
        journal = json.loads(Path(job["install"]["journal"]).read_text())
        self.assertEqual(journal["status"], "rolled_back")

    def test_legacy_explicit_host_api_still_works(self):
        job = self.planned()
        result = dev.install(job, backup_dir=self.backups, allow=True)
        self.assertEqual(result["status"], "installed")
        self.assertEqual(result["authorization"]["mode"], "explicit_host")

    def test_atomic_config_failure_leaves_previous_authorization(self):
        self.enable()
        original = (self.root / dev.AUTONOMY_CONFIG).read_bytes()
        with patch.object(dev.os, "replace", side_effect=OSError("injected config write failure")):
            with self.assertRaisesRegex(OSError, "config write failure"):
                dev.set_autonomy(self.root, enabled=False, authorization="disable")
        self.assertEqual((self.root / dev.AUTONOMY_CONFIG).read_bytes(), original)
        self.assertTrue(dev.autonomy_config(self.root)["enabled"])


if __name__ == "__main__":
    unittest.main()
