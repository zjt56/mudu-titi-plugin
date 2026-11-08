"""Real isolated source/candidate/format transactions; no model or network calls."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from workflow import batch_fill, format_gate, legacy_format, manifest as mf
from workflow import question_draft, review_legacy, self_heal_actions as actions
from workflow import test_legacy_format as sourcefixture


class SelfHealActionsTest(unittest.TestCase):
    setUp = sourcefixture.LegacyFormatTest.setUp
    put = sourcefixture.LegacyFormatTest.put
    fixture = sourcefixture.LegacyFormatTest.fixture
    modify = sourcefixture.LegacyFormatTest.modify
    snapshot = sourcefixture.LegacyFormatTest.snapshot

    def load(self, relative):
        return format_gate._load((self.root / relative).read_bytes())

    def raw_files(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*')
                if p.is_file() and 'format-migrations' not in p.parts
                and p.name != '.source-format-repair.lock'}

    def add_manifest(self):
        original = self.root / '00_original/source.txt'
        original.parent.mkdir()
        original.write_bytes((self.root / legacy_format.SOURCE_PATHS[0]).read_bytes())
        state = mf.new_manifest('fixture', self.root,
            {'source': {'path': str(original), 'sha256': mf.sha256_file(original)}}, {})
        for stage_id in ('assemble', 'meta', 'underlines'):
            record = state['stages'][stage_id]
            record.update(status='completed', quality_status='failed', quality_passed=False,
                          marker='preserve execution history', history=[{'from': 'pending', 'to': 'completed'}])
            stage = actions.contracts.STAGE_BY_ID[stage_id]
            record['input_hashes'] = mf.input_hashes(self.root, state, stage)
            record['output_hashes'] = mf.output_hashes(self.root, stage)
            mf.record_semantics(self.root, state, stage)
        for stage_id in ('validate', 'format_gate', 'review', 'review_gate'):
            state['stages'][stage_id].update(status='failed', quality_status='failed', quality_passed=False)
        mf.save(self.root, state)
        self.put('workflow/delivery.json', {'version': '1.0', 'stages': {
            s: {'status': 'delivered', 'quality_status': 'failed', 'release_ready': False,
                'artifacts': [], 'input_hashes': {}, 'consumed_by': ['review']}
            for s in ('assemble', 'meta', 'underlines', 'validate', 'format_gate', 'review')}})
        # Frozen review data is never rewritten by the migration.
        self.put('workflow/review-plan.json', {'sample': [{'unit_id': 'q-1', 'number': 1}], 'seed': 'original'})
        self.put('07_review/review-rounds.json', {'original_execution_id': 'immutable-execution'})
        return state

    def receipt(self, result):
        return self.load(result['receipt_path'])

    def proof(self, result, relative=legacy_format.BATCH_PATHS[-1]):
        return review_legacy.verify_format_equivalence(self.root, relative,
            result['before_sha256'][relative], mf.sha256_file(self.root / relative))

    def test_empty_blocks_real_apply_preserves_every_other_field_and_raw_backup(self):
        original = self.fixture()
        # Keep noncanonical raw whitespace to prove backup hashes use actual bytes.
        for rel in legacy_format.BATCH_PATHS:
            (self.root / rel).write_bytes(b'  \n' + (self.root / rel).read_bytes() + b' \n')
        old = self.raw_files()
        self.assertFalse(format_gate.run(self.root)['passed'])
        result = actions.apply_source_format_repair(self.root)
        self.assertTrue(result['passed'], result)
        self.assertTrue(result['installed'])
        self.assertEqual(result['status'], 'applied')
        self.assertTrue(format_gate.run(self.root)['passed'])
        expected = deepcopy(original)
        del expected['items'][0]['materialBlocks'][0]
        receipt = self.receipt(result)
        for rel in legacy_format.BATCH_PATHS:
            self.assertEqual(self.load(rel), expected)
            entry = receipt['files'][rel]
            raw = (self.root / entry['backup_path']).read_bytes()
            self.assertEqual(raw, old[rel])
            self.assertEqual(format_gate._sha(raw), result['before_sha256'][rel])
            self.assertEqual(entry['after_sha256'], mf.sha256_file(self.root / rel))
            self.assertTrue(result['checks']['source_audit'][rel]['passed'])
        self.assertFalse(result['quality_passed'])
        self.assertTrue(result['prepare_upload_required'])
        self.assertFalse((self.root / batch_fill.STATE).exists())
        self.assertTrue(self.proof(result))

    def test_listening_real_apply_retains_number_options_answers_tags_and_other_items(self):
        original = self.fixture(listening=True)
        original['items'][-1]['stem'] = '5.'  # Already valid item stays byte-for-field identical.
        for rel in legacy_format.BATCH_PATHS:
            self.put(rel, original)
        result = actions.apply_source_format_repair(self.root)
        self.assertTrue(result['passed'], result)
        expected = deepcopy(original)
        for index, row in enumerate(expected['items'], 1):
            row['stem'] = f'{index}.'
        self.assertEqual(self.load(format_gate.FINAL_PATH), expected)
        self.assertTrue(format_gate.run(self.root)['passed'])
        self.assertTrue(self.proof(result))

    def test_second_call_is_true_noop_with_no_new_report_or_journal(self):
        self.fixture()
        first = actions.apply_source_format_repair(self.root)
        self.assertTrue(first['passed'], first)
        before = self.snapshot()
        result = actions.apply_source_format_repair(self.root)
        self.assertEqual(result['status'], 'noop')
        self.assertTrue(result['passed'])
        self.assertFalse(result['installed'])
        self.assertEqual(result['changes'], [])
        self.assertIsNone(result['receipt_path'])
        self.assertEqual(self.snapshot(), before)

    def test_preparation_refusal_writes_no_canonical_or_reports(self):
        batch = self.fixture()
        self.modify(batch, lambda row: row.update(difficulty='invalid'))
        before = self.snapshot()
        result = actions.apply_source_format_repair(self.root)
        self.assertFalse(result['passed'])
        self.assertEqual(result['status'], 'refused')
        self.assertEqual(self.snapshot(), before)

    def test_real_postinstall_format_failure_rolls_back_all_files_and_previous_report(self):
        self.fixture()
        self.add_manifest()
        format_gate.run(self.root)
        original = self.raw_files()
        real = format_gate.run
        def fail(root):
            report = real(root)
            self.assertTrue(report['passed'])
            # Fault injection runs the real checks, then models a final check failure.
            report.update(passed=False, status='failed')
            report['errors'].append({'code': 'INJECTED_CHECK_FAILURE'})
            format_gate._write_report(root / format_gate.REPORT_PATH, report)
            return report
        with patch.object(format_gate, 'run', side_effect=fail):
            result = actions.apply_source_format_repair(self.root)
        self.assertEqual(result['status'], 'rolled_back', result)
        self.assertFalse(result['passed'])
        self.assertFalse(result['installed'])
        self.assertTrue(result['rollback_complete'])
        self.assertEqual(self.raw_files(), original)
        self.assertEqual(self.receipt(result)['status'], 'rolled_back')
        self.assertIsNone(self.proof(result))

    def test_real_postinstall_source_failure_rolls_back_without_format_forgery(self):
        self.fixture()
        original = self.raw_files()
        real = actions.question_content.audit_batch_source
        def fail_after(root, payload):
            report = real(root, payload)
            # prepare checks in-memory candidates before disk has changed.
            installed = self.load(format_gate.FINAL_PATH)['items'][0]['materialBlocks']
            if all(b['type'] == 'table' for b in installed):
                report['passed'] = False
                report['issues'].append({'code': 'INJECTED_SOURCE_FAILURE'})
            return report
        with patch.object(actions.question_content, 'audit_batch_source', side_effect=fail_after):
            result = actions.apply_source_format_repair(self.root)
        self.assertEqual(result['status'], 'rolled_back', result)
        self.assertEqual(self.raw_files(), original)
        self.assertNotIn('format', result['checks'])

    def test_partial_atomic_install_error_restores_every_installed_raw_file(self):
        self.fixture()
        original = self.raw_files()
        real = actions._atomic_raw
        tripped = False
        def interrupted(root, relative, raw):
            nonlocal tripped
            if relative == legacy_format.BATCH_PATHS[1] and not tripped:
                tripped = True
                raise OSError('injected replacement failure')
            return real(root, relative, raw)
        with patch.object(actions, '_atomic_raw', side_effect=interrupted):
            result = actions.apply_source_format_repair(self.root)
        self.assertEqual(result['status'], 'rolled_back', result)
        self.assertEqual(self.raw_files(), original)

    def test_expected_input_and_prepare_time_drift_are_rejected(self):
        self.fixture()
        before = self.raw_files()
        result = actions.apply_source_format_repair(self.root,
            expected_inputs={legacy_format.BATCH_PATHS[0]: '0' * 64})
        self.assertFalse(result['passed'])
        self.assertEqual(self.raw_files(), before)
        real = legacy_format.prepare
        def drift(root):
            result = real(root)
            path = root / legacy_format.SOURCE_PATHS[0]
            path.write_bytes(path.read_bytes() + b'\n')
            return result
        with patch.object(legacy_format, 'prepare', side_effect=drift):
            result = actions.apply_source_format_repair(self.root)
        self.assertFalse(result['passed'], result)
        for rel in legacy_format.BATCH_PATHS:
            self.assertEqual((self.root / rel).read_bytes(), before[rel])
        self.assertFalse((self.root / actions.MIGRATIONS).exists())

    def test_missing_expected_inputs_supported_and_late_creation_rejected(self):
        self.fixture()
        result = actions.apply_source_format_repair(self.root,
            expected_inputs={'optional/input.json': 'missing'})
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['status'], 'applied')
        # Reintroduce the fixture's legacy encoding in this same temp directory.
        self.fixture()
        original = {rel: (self.root / rel).read_bytes() for rel in legacy_format.BATCH_PATHS}
        real = actions._atomic_raw
        created = False
        def create(root, relative, raw):
            nonlocal created
            real(root, relative, raw)
            if relative == legacy_format.BATCH_PATHS[0] and not created:
                created = True
                self.put('optional/input.json', {'concurrent': True})
        with patch.object(actions, '_atomic_raw', side_effect=create):
            result = actions.apply_source_format_repair(self.root,
                expected_inputs={'optional/input.json': 'missing'})
        self.assertEqual(result['status'], 'rolled_back', result)
        self.assertEqual(self.load('optional/input.json'), {'concurrent': True})
        for rel, raw in original.items():
            self.assertEqual((self.root / rel).read_bytes(), raw)

    def test_upstream_stage_artifact_drift_blocks_manifest_rebaselining(self):
        self.fixture()
        self.add_manifest()
        self.put('04_knowledge/knowledge.json', {'items': []})
        old_manifest = (self.root / 'workflow/manifest.json').read_bytes()
        original = {rel: (self.root / rel).read_bytes() for rel in legacy_format.BATCH_PATHS}
        real = actions._atomic_raw
        changed = False
        def drift(root, relative, raw):
            nonlocal changed
            real(root, relative, raw)
            if relative == legacy_format.BATCH_PATHS[0] and not changed:
                changed = True
                self.put('04_knowledge/knowledge.json', {'items': [{'changed': True}]})
        with patch.object(actions, '_atomic_raw', side_effect=drift):
            result = actions.apply_source_format_repair(self.root)
        self.assertEqual(result['status'], 'rolled_back', result)
        self.assertEqual((self.root / 'workflow/manifest.json').read_bytes(), old_manifest)
        for rel, raw in original.items():
            self.assertEqual((self.root / rel).read_bytes(), raw)

    def test_input_change_mid_install_rolls_back_ours_preserves_external_source(self):
        self.fixture()
        original = self.raw_files()
        real = actions._atomic_raw
        changed = False
        def drift(root, relative, raw):
            nonlocal changed
            real(root, relative, raw)
            if relative == legacy_format.BATCH_PATHS[0] and not changed:
                changed = True
                path = root / legacy_format.SOURCE_PATHS[0]
                path.write_bytes(path.read_bytes() + b'\n')
        with patch.object(actions, '_atomic_raw', side_effect=drift):
            result = actions.apply_source_format_repair(self.root)
        self.assertEqual(result['status'], 'rolled_back', result)
        for rel in legacy_format.BATCH_PATHS:
            self.assertEqual((self.root / rel).read_bytes(), original[rel])
        self.assertEqual((self.root / legacy_format.SOURCE_PATHS[0]).read_bytes(),
                         original[legacy_format.SOURCE_PATHS[0]] + b'\n')

    def test_refreshes_persisted_producer_hashes_and_only_local_consumers(self):
        self.fixture()
        original = self.add_manifest()
        immutable = {p: (self.root / p).read_bytes() for p in
                     ('workflow/review-plan.json', '07_review/review-rounds.json')}
        expected_inputs = {p: mf.sha256_file(self.root / p) for p in legacy_format.BATCH_PATHS}
        result = actions.apply_source_format_repair(self.root, expected_inputs=expected_inputs)
        self.assertTrue(result['passed'], result)
        state = self.load('workflow/manifest.json')
        self.assertEqual(result['refreshed_stages'], ['assemble', 'meta', 'underlines'])
        self.assertEqual(result['invalidated_stages'], ['validate', 'format_gate'])
        for stage_id in result['refreshed_stages']:
            stage = actions.contracts.STAGE_BY_ID[stage_id]
            record = state['stages'][stage_id]
            self.assertEqual(record['output_hashes'], mf.output_hashes(self.root, stage))
            self.assertEqual(record['input_hashes'], mf.input_hashes(self.root, state, stage))
            self.assertEqual(mf.is_stale(self.root, state, stage), (False, ''))
            self.assertEqual(record['quality_status'], 'failed')
            self.assertFalse(record['quality_passed'])
            self.assertEqual(record['history'], original['stages'][stage_id]['history'])
        for stage_id in ('validate', 'format_gate'):
            self.assertEqual(state['stages'][stage_id]['status'], 'invalidated')
            self.assertEqual(state['stages'][stage_id]['quality_status'], 'failed')
        for stage_id in ('review', 'review_gate'):
            self.assertEqual(state['stages'][stage_id], original['stages'][stage_id])
        for path, raw in immutable.items():
            self.assertEqual((self.root / path).read_bytes(), raw)
        delivery = self.load('workflow/delivery.json')['stages']
        for stage_id in result['refreshed_stages']:
            self.assertEqual(delivery[stage_id]['quality_status'], 'failed')
            self.assertEqual(delivery[stage_id]['consumed_by'], ['review'])
            self.assertTrue(delivery[stage_id]['artifacts'][0]['sha256'])
        self.assertTrue(self.proof(result))

    def managed_fixture(self):
        original = self.fixture(listening=True)
        # The initializer correctly refuses taking over legacy files. Start an
        # owned temp batch first, then recreate the legacy accepted encoding.
        (self.root / batch_fill.BATCH).unlink()
        initialized = batch_fill.initialize(self.root)
        self.assertEqual(initialized['status'], 'initialized')
        # Recreate an accepted legacy encoding in the owned current bodies.
        # The source base/bindings remain those generated by the real initializer.
        for rel in legacy_format.BATCH_PATHS:
            self.put(rel, original)
        state = self.load(batch_fill.STATE)
        state['managed_bodies'] = {row['unit_id']: batch_fill._body(row) for row in original['items']}
        state['payload_sha256'] = question_draft._digest(original)
        self.put(batch_fill.STATE, batch_fill._seal(state))
        batch_fill._managed(self.root)
        return original, state

    def test_managed_batch_uses_real_finalize_without_source_or_quality_takeover(self):
        _, old = self.managed_fixture()
        base = (self.root / batch_fill.BATCH_BASE).read_bytes()
        result = actions.apply_source_format_repair(self.root)
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['batch_fill_status'], 'finalized')
        state, payload = batch_fill._managed(self.root)
        self.assertEqual(state['mode'], 'finalized')
        self.assertEqual(state['units'], old['units'])
        self.assertEqual((self.root / batch_fill.BATCH_BASE).read_bytes(), base)
        self.assertEqual(state['payload_sha256'], question_draft._digest(payload))
        self.assertFalse(result['quality_passed'])

    def test_managed_state_conflict_refused_and_finalize_failure_rolls_back(self):
        self.managed_fixture()
        original = self.raw_files()
        real = batch_fill.mark_finalized
        def interrupted(root):
            real(root)
            raise ValueError('injected post-finalize failure')
        with patch.object(batch_fill, 'mark_finalized', side_effect=interrupted):
            result = actions.apply_source_format_repair(self.root)
        self.assertEqual(result['status'], 'rolled_back', result)
        self.assertEqual(self.raw_files(), original)
        batch_fill._managed(self.root)
        state = self.load(batch_fill.STATE)
        state['state_sha256'] = 'f' * 64
        self.put(batch_fill.STATE, state)
        before = self.snapshot()
        result = actions.apply_source_format_repair(self.root)
        self.assertFalse(result['passed'])
        self.assertEqual(self.snapshot(), before)

    def table_fixture(self):
        original = self.fixture()
        path = self.root / legacy_format.SOURCE_PATHS[0]
        body = path.read_text().replace('First game<br>', 'First game | ').replace('Second game<br>', 'Second game | ')
        structure, body, _ = actions.extract.infer_structure(body)
        units = actions.structure_gate._build_units(structure.get('sections') or [],
            structure.get('groups') or [], actions.denoise.clean_body(body)[0].splitlines())
        path.write_text(body, encoding='utf-8')
        self.put(legacy_format.SOURCE_PATHS[1], structure)
        self.put(legacy_format.SOURCE_PATHS[2], {'units': units})
        original['items'][0]['materialBlocks'][1]['rows'] = []
        for rel in legacy_format.BATCH_PATHS:
            self.put(rel, original)
        return original

    def test_source_table_action_restores_real_rows_preserves_existing_format_failure(self):
        original = self.table_fixture()
        old = self.raw_files()
        result = actions.apply_source_table_repair(self.root)
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['action'], 'repair_source_tables')
        self.assertFalse(result['format_passed'])
        self.assertFalse(self.load(format_gate.REPORT_PATH)['passed'])
        self.assertTrue(result['installed'])
        self.assertEqual(len(result['changed_files']), 3)
        current = self.load(format_gate.FINAL_PATH)
        rows = current['items'][0]['materialBlocks'][1]['rows']
        self.assertEqual(rows[0]['cells'][0]['text'], 'First game')
        self.assertEqual(rows[0]['cells'][1]['text'], 'Children play outside.')
        unchanged = deepcopy(current)
        unchanged['items'][0]['materialBlocks'][1]['rows'] = []
        self.assertEqual(unchanged, original)
        for rel, audit in result['checks']['source_audit'].items():
            self.assertTrue(audit['passed'])
            backup = self.receipt(result)['files'][rel]['backup_path']
            self.assertEqual((self.root / backup).read_bytes(), old[rel])
        self.assertIsNone(self.proof(result))
        again = actions.apply_source_table_repair(self.root)
        self.assertEqual(again['status'], 'noop', again)

    def test_table_action_rejects_extra_label_changes_and_failed_source_recheck(self):
        self.table_fixture()
        original = self.raw_files()
        real_restore = actions.question_content.restore_table_blocks_from_structure
        def wrong(root, payload):
            count = real_restore(root, payload)
            payload['items'][0]['answer'] = 'changed answer'
            return count
        with patch.object(actions.question_content, 'restore_table_blocks_from_structure', side_effect=wrong):
            result = actions.apply_source_table_repair(self.root)
        self.assertFalse(result['passed'], result)
        self.assertEqual(self.raw_files(), original)
        real_audit = actions.question_content.audit_batch_source
        def failed_recheck(root, payload):
            audit = real_audit(root, payload)
            installed = self.load(format_gate.FINAL_PATH)['items'][0]['materialBlocks'][1]['rows']
            if installed:
                audit.update(passed=False, issues=[{'code': 'INJECTED_POST_TABLE_SOURCE_FAILURE'}])
            return audit
        with patch.object(actions.question_content, 'audit_batch_source', side_effect=failed_recheck):
            result = actions.apply_source_table_repair(self.root)
        self.assertEqual(result['status'], 'rolled_back', result)
        self.assertEqual(self.raw_files(), original)

    def test_table_action_partial_write_rolls_back_real_raw_backups(self):
        self.table_fixture()
        original = self.raw_files()
        real = actions._atomic_raw
        failed = False
        def interrupted(root, relative, raw):
            nonlocal failed
            if relative == legacy_format.BATCH_PATHS[-1] and not failed:
                failed = True
                raise OSError('injected table install failure')
            return real(root, relative, raw)
        with patch.object(actions, '_atomic_raw', side_effect=interrupted):
            result = actions.apply_source_table_repair(self.root)
        self.assertEqual(result['status'], 'rolled_back', result)
        self.assertEqual(self.raw_files(), original)

    def test_original_plan_sample_and_execution_restored_sources_remain_compatible(self):
        from workflow import review
        from workflow import test_review_legacy as reviewfixture
        original = self.fixture()
        confirmed = self.load(legacy_format.SOURCE_PATHS[2])
        for unit, row in zip(confirmed['units'], original['items']):
            unit['unit_id'] = row['unit_id']
        self.put(legacy_format.SOURCE_PATHS[2], confirmed)
        helper = reviewfixture.ReviewLegacyTest()
        helper.batch = self.root
        helper.units = confirmed['units']
        helper.knowledge = original['items'][0]['knowledge']
        for rel in ('03_types/types.json', '04_knowledge/knowledge.json', '04_knowledge/explanations.json'):
            self.put(rel, {'items': [dict(unit_id=u['unit_id'], type='阅读理解',
                subtypes=['阅读判断'], knowledge=helper.knowledge, explanation='无',
                articleSummary='无', subquestions=[]) for u in helper.units]})
        self.put('01_source_ans/answers.json', {'answers': {'71': 'A', '72': 'A', '73': 'A'}})
        helper.legacy()
        frozen = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*')
                  if p.is_file() and ('review-' in p.name or 'review-bindings' in p.parts
                                     or 'review-transactions' in p.parts or 'review-tasks' in p.parts
                                     or 'review-results' in p.parts)}
        result = actions.apply_source_format_repair(self.root)
        self.assertTrue(result['passed'], result)
        current = review.prepare_round_plan(self.root)
        effective, info = review.resolve_round_plan(self.root, current, helper.payload)
        self.assertTrue(info['accepted'], info)
        self.assertEqual(effective, helper.old)
        self.assertEqual(effective['sample'], helper.old['sample'])
        self.assertTrue(info['format_equivalence'])
        evaluation = review.evaluate_rounds(self.root, helper.payload)
        self.assertTrue(evaluation['execution_completed'], evaluation)
        for rel, raw in frozen.items():
            self.assertEqual((self.root / rel).read_bytes(), raw)

    def test_archive_and_symlink_targets_are_refused(self):
        self.fixture()
        archived = self.root / '.workbench-trash' / 'old'
        archived.mkdir(parents=True)
        before = self.snapshot()
        result = actions.apply_source_format_repair(archived)
        self.assertFalse(result['passed'])
        self.assertEqual(self.snapshot(), before)
        link = self.root / 'linked-batch'
        link.symlink_to(self.root, target_is_directory=True)
        self.assertFalse(actions.apply_source_format_repair(link)['passed'])


class FormatEquivalenceTest(unittest.TestCase):
    setUp = SelfHealActionsTest.setUp
    put = SelfHealActionsTest.put
    fixture = SelfHealActionsTest.fixture
    load = SelfHealActionsTest.load
    add_manifest = SelfHealActionsTest.add_manifest
    receipt = SelfHealActionsTest.receipt
    proof = SelfHealActionsTest.proof

    def test_bad_backup_fake_receipt_and_semantic_changes_are_fail_closed(self):
        original = self.fixture(listening=True)
        self.add_manifest()
        result = actions.apply_source_format_repair(self.root)
        self.assertTrue(self.proof(result))
        receipt = self.receipt(result)
        rel = legacy_format.BATCH_PATHS[-1]
        backup = self.root / receipt['files'][rel]['backup_path']
        raw = backup.read_bytes()
        backup.write_bytes(raw + b' ')
        self.assertIsNone(self.proof(result))
        backup.write_bytes(raw)
        self.assertTrue(self.proof(result))
        receipt_path = self.root / result['receipt_path']
        receipt_raw = receipt_path.read_bytes()
        receipt['changes'][0]['action'] = 'forged'
        self.put(result['receipt_path'], actions._seal_receipt(receipt))
        self.assertIsNone(self.proof(result))
        receipt_path.write_bytes(receipt_raw)
        current_raw = (self.root / rel).read_bytes()
        for mutation in (lambda row: row.update(stem='Real changed question?'),
                         lambda row: row.update(answer='B'),
                         lambda row: row.update(knowledge=['changed label']),
                         lambda row: row['options'].__setitem__(0, 'A. changed')):
            with self.subTest(mutation=mutation):
                payload = format_gate._load(current_raw)
                mutation(payload['items'][0])
                changed = actions._canonical(payload)
                (self.root / rel).write_bytes(changed)
                receipt = format_gate._load(receipt_raw)
                receipt['files'][rel]['after_sha256'] = format_gate._sha(changed)
                self.put(result['receipt_path'], actions._seal_receipt(receipt))
                (self.root / receipt['files'][rel]['candidate_path']).write_bytes(changed)
                self.assertIsNone(self.proof(result))
        (self.root / rel).write_bytes(current_raw)
        (self.root / receipt['files'][rel]['candidate_path']).write_bytes(current_raw)
        receipt_path.write_bytes(receipt_raw)
        self.assertTrue(self.proof(result))

    def test_original_body_structure_and_rules_are_rehashed_not_trusted(self):
        self.fixture()
        self.add_manifest()
        result = actions.apply_source_format_repair(self.root)
        self.assertTrue(self.proof(result))
        for relative in ('00_original/source.txt', *legacy_format.SOURCE_PATHS):
            with self.subTest(relative=relative):
                path = self.root / relative
                original = path.read_bytes()
                path.write_bytes(original + b'\n')
                self.assertIsNone(self.proof(result))
                path.write_bytes(original)
                self.assertTrue(self.proof(result))
        receipt = self.receipt(result)
        receipt['rule_sha256']['workflow/legacy_format.py'] = 'f' * 64
        self.put(result['receipt_path'], actions._seal_receipt(receipt))
        self.assertIsNone(self.proof(result))

    def test_supplied_current_sha_cannot_override_actual_raw_hash(self):
        self.fixture()
        result = actions.apply_source_format_repair(self.root)
        rel = legacy_format.BATCH_PATHS[-1]
        self.assertIsNone(review_legacy.verify_format_equivalence(self.root, rel,
            result['before_sha256'][rel], '0' * 64))


if __name__ == '__main__':
    unittest.main()
