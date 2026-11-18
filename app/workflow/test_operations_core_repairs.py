"""Guarded core repair capability uses disposable source roots only."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import operations_development as dev


class CoreRepairCapabilityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'source'
        self.workflow = self.root / 'app/workflow'
        self.workflow.mkdir(parents=True)
        (self.workflow / '__init__.py').write_text('')
        (self.workflow / 'auto.py').write_text('def answer():\n    return 0\n')
        (self.workflow / 'test_extra.py').write_text(
            'import unittest\nclass Extra(unittest.TestCase):\n'
            '    def test_extra(self):\n        self.assertEqual(1, 1)\n')
        for module in dev.CORE_REGRESSIONS['app/workflow/auto.py']:
            (self.root / ('app/' + module.replace('.', '/') + '.py')).write_text(
                'import unittest\nfrom workflow import auto\n'
                'class Owner(unittest.TestCase):\n'
                '    def test_original_owner_contract(self):\n'
                '        self.assertEqual(auto.answer(), 1)\n')
        self.job = dev.create(self.root, Path(self.temp.name) / 'jobs')
        self.relative = 'app/workflow/auto.py'

    def fix(self, value=1):
        current = dev.read(self.job, self.relative)['sha256']
        dev.write(self.job, self.relative, f'def answer():\n    return {value}\n',
                  expected_sha=current)

    def test_core_owners_can_be_repaired_but_integrity_and_host_remain_locked(self):
        for relative in dev.CORE_REGRESSIONS:
            with self.subTest(relative=relative):
                self.assertTrue(dev.allowed(relative))
                self.assertEqual(dev.classify(relative), 'production')
        for relative in (*dev.LOCKED, '.operations-autonomy.json',
                         'lib/operations-agent.js', 'lib/index.js',
                         'workflows/paper/workflow/manifest.json'):
            with self.subTest(relative=relative), self.assertRaisesRegex(ValueError, 'source_not_allowlisted'):
                dev.write(self.job, relative, '# changed\n')

    def test_test_call_adds_complete_existing_owner_suites(self):
        self.fix()
        required = set(dev.CORE_REGRESSIONS[self.relative])
        result = dev.run_tests(self.job, ['workflow.test_extra'])
        self.assertTrue(result['passed'], result['output'])
        self.assertEqual(set(result['tests']), {'workflow.test_extra', *required})
        self.assertEqual(set(result['required_core_regressions']), required)
        self.assertEqual(result['core_regression_results'], {module: 1 for module in required})
        self.assertIn('Ran 3 tests', result['output'])
        plan = dev.prepare_install(self.job)
        self.assertEqual(set(plan['required_core_regressions']), required)

    def test_failing_real_owner_contract_prevents_install(self):
        self.fix(2)
        result = dev.run_tests(self.job, ['workflow.test_extra'])
        self.assertFalse(result['passed'])
        self.assertIn('test_original_owner_contract', result['output'])
        with self.assertRaisesRegex(ValueError, 'passing_tests_required'):
            dev.prepare_install(self.job, require_tests=False, require_regression=False)

    def test_baseline_suites_cannot_be_changed_to_make_the_fix_pass(self):
        self.fix(2)
        module = dev.CORE_REGRESSIONS[self.relative][0]
        relative = 'app/' + module.replace('.', '/') + '.py'
        before = dev.read(self.job, relative)
        dev.write(self.job, relative, before['text'].replace('auto.answer(), 1', 'auto.answer(), 2'),
                  expected_sha=before['sha256'])
        with self.assertRaisesRegex(ValueError, 'core_regression_test_changed'):
            dev.run_tests(self.job, [module + '.Owner.test_original_owner_contract'])
        with self.assertRaisesRegex(ValueError, 'core_regression_test_changed'):
            dev.prepare_install(self.job, require_tests=False, require_regression=False)

    def test_missing_baseline_suite_is_not_replaced_by_a_new_model_authored_test(self):
        module = dev.CORE_REGRESSIONS[self.relative][0]
        relative = 'app/' + module.replace('.', '/') + '.py'
        self.job['base']['files'].pop(relative)
        self.fix()
        with self.assertRaisesRegex(ValueError, 'core_regression_baseline_missing'):
            dev.run_tests(self.job, ['workflow.test_extra'])

    def test_optional_checks_and_class_selectors_cannot_bypass_required_suites(self):
        self.fix()
        self.job['tests'] = [{'passed': True, 'fingerprint': dev.fingerprint(self.job),
                             'tests': [name + '.Owner.test_original_owner_contract'
                                       for name in dev.CORE_REGRESSIONS[self.relative]]}]
        with self.assertRaisesRegex(ValueError, 'core_regression_tests_required'):
            dev.prepare_install(self.job, require_tests=False, require_regression=False)

    def test_empty_or_all_skipped_mandatory_suite_does_not_count_as_execution(self):
        module = dev.CORE_REGRESSIONS[self.relative][0]
        source = self.root / ('app/' + module.replace('.', '/') + '.py')
        for contents in ('# no tests\n',
                         'import unittest\n@unittest.skip("unavailable")\n'
                         'class T(unittest.TestCase):\n    def test_skip(self):\n        pass\n'):
            with self.subTest(contents=contents):
                source.write_text(contents)
                self.job = dev.create(self.root, Path(self.temp.name) / 'jobs')
                self.fix()
                result = dev.run_tests(self.job, ['workflow.test_extra'])
                self.assertFalse(result['passed'], result['output'])
                self.assertEqual(result['core_regression_results'][module], 0)
                with self.assertRaisesRegex(ValueError, 'passing_tests_required'):
                    dev.prepare_install(self.job)

    def test_success_exit_without_execution_receipt_is_rejected(self):
        self.fix()
        completed = type('Completed', (), {'returncode': 0, 'stdout': 'OK\n', 'stderr': ''})()
        with patch.object(dev.subprocess, 'run', return_value=completed):
            result = dev.run_tests(self.job, ['workflow.test_extra'])
        self.assertFalse(result['passed'])
        self.assertIn('core_regression_execution_incomplete', result['output'])

    def test_preparation_requires_actual_owner_execution_receipt(self):
        self.fix()
        self.job['tests'] = [{'passed': True, 'fingerprint': dev.fingerprint(self.job),
                             'tests': list(dev.CORE_REGRESSIONS[self.relative])}]
        with self.assertRaisesRegex(ValueError, 'core_regression_execution_required'):
            dev.prepare_install(self.job, require_tests=False, require_regression=False)

    def test_install_rechecks_required_suites_even_for_explicit_host_authorization(self):
        self.fix()
        self.job['plan'] = {'plan_id': 'forged-old-plan', 'changes': dev.diff(self.job)['files'],
                            'code_fingerprint': dev.fingerprint(self.job)}
        with self.assertRaisesRegex(ValueError, 'passing_tests_required'):
            dev.install(self.job, backup_dir=Path(self.temp.name) / 'backups', allow=True)
        self.assertEqual((self.workflow / 'auto.py').read_text(), 'def answer():\n    return 0\n')

    def test_core_fix_can_install_with_baseline_suites_and_rollback(self):
        self.fix()
        result = dev.run_tests(self.job, ['workflow.test_extra'])
        self.assertTrue(result['passed'], result['output'])
        dev.prepare_install(self.job)
        backups = Path(self.temp.name) / 'backups'
        installed = dev.install(self.job, backup_dir=backups, allow=True)
        self.assertEqual(installed['status'], 'installed')
        self.assertEqual((self.workflow / 'auto.py').read_text(), 'def answer():\n    return 1\n')
        journal = json.loads(Path(installed['journal']).read_text())
        self.assertEqual(journal['status'], 'installed')
        self.assertEqual(dev.rollback(self.job, backup_dir=backups)['status'], 'rolled_back')
        self.assertEqual((self.workflow / 'auto.py').read_text(), 'def answer():\n    return 0\n')


if __name__ == '__main__':
    unittest.main()
