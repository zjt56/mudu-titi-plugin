"""Self-heal gap regressions: isolated sources, real checks/import, stubbed agents."""
from copy import deepcopy
import contextlib
import io
import json
import unittest
from unittest.mock import patch

from workflow import agent_tasks, contracts, format_gate, legacy_format, local_agent
from workflow import manifest as mf, question_content, review_legacy, self_heal, self_heal_actions as actions
from workflow import test_self_heal_actions as transaction_fixture
from workflow import test_repair_feedback as feedback_fixture
from workflow.test_format_gate import question


class SourceOptionRepairTest(unittest.TestCase):
    setUp = transaction_fixture.SelfHealActionsTest.setUp
    put = transaction_fixture.SelfHealActionsTest.put
    fixture = transaction_fixture.SelfHealActionsTest.fixture
    load = transaction_fixture.SelfHealActionsTest.load
    raw_files = transaction_fixture.SelfHealActionsTest.raw_files
    add_manifest = transaction_fixture.SelfHealActionsTest.add_manifest

    def truncated(self, *, objects=False, terminal=False, listening=False):
        batch = self.fixture(listening=listening)
        if listening:
            for row in batch['items']:
                row['stem'] = f"{row['number']}."
            child = batch['items'][0]
        else:
            del batch['items'][0]['materialBlocks'][0]
            child = batch['items'][0]['subquestions'][-1]
        if objects:
            child['options'] = [{'key': o['label'], 'text': o['text']}
                                for o in question_content._candidate_options(child['options'])]
        complete = deepcopy(batch)
        child['options'] = child['options'][:2]
        if terminal:
            if objects:
                child['options'][-1]['text'] = child['options'][-1]['text'][:2]
            else:
                child['options'][-1] = child['options'][-1][:5]
        for rel in legacy_format.BATCH_PATHS:
            self.put(rel, batch)
        return batch, complete

    def test_group_terminal_option_and_missing_tail_restored_exactly_with_backups(self):
        for objects in (False, True):
            with self.subTest(objects=objects):
                _, complete = self.truncated(objects=objects, terminal=True)
                original = self.raw_files()
                self.assertFalse(question_content.audit_batch_source(self.root,
                    self.load(format_gate.FINAL_PATH))['passed'])
                result = actions.apply_source_option_repair(self.root)
                self.assertTrue(result['installed'], result)
                self.assertTrue(result['passed'])
                self.assertEqual(result['checks']['format']['passed'], not objects)
                self.assertEqual(result['format_passed'], not objects)
                receipt = self.load(result['receipt_path'])
                self.assertEqual(receipt['rule'], 'source-option-prefix-v1')
                for rel in legacy_format.BATCH_PATHS:
                    self.assertEqual(self.load(rel), complete)
                    self.assertEqual((self.root / receipt['files'][rel]['backup_path']).read_bytes(), original[rel])
                    self.assertTrue(result['checks']['source_audit'][rel]['passed'])
                    self.assertIsNone(review_legacy.verify_format_equivalence(self.root, rel,
                        result['before_sha256'][rel], mf.sha256_file(self.root / rel)))
                self.assertFalse(result['quality_passed'])
                self.assertTrue(result['prepare_upload_required'])
                self.assertEqual(actions.apply_source_option_repair(self.root)['status'], 'noop')

    def test_listening_source_abc_supported_without_changing_stem_or_audio(self):
        _, complete = self.truncated(listening=True)
        result = actions.apply_source_option_repair(self.root)
        self.assertTrue(result['installed'], result)
        self.assertEqual(self.load(format_gate.FINAL_PATH), complete)

    def test_inherited_group_instructions_do_not_block_an_unrelated_listening_repair(self):
        from workflow import denoise, extract, structure_gate
        from workflow.preview import _strong_number
        from workflow.test_format_gate import group
        body = ('Ⅰ.关键词语选择(共2小题)\n'
                '你将听到两个句子。请在每小题所给的A、B、C三个选项中选出一个单词。\n'
                '1.A.part B.pass C.place\n2.A.dream B.drive C.draw\n'
                'Ⅱ.阅读理解(共4小题)\n'
                '阅读下列短文，从每小题所给的A、B、C、D四个选项中选出最佳选项。\n'
                'A\nThe first passage about school.\n'
                '3.Where is the school?\nA.Here B.There C.Home D.Park\n'
                '4.Who reads?\nA.Amy B.Ben C.Cat D.Dog\n'
                'B\nThe second passage about sport.\n'
                '5.What do they do?\nA.Run B.Sit C.Read D.Walk\n'
                '6.When do they go?\nA.Today B.Tomorrow C.Monday D.Sunday')
        structure, body, _ = extract.infer_structure(body)
        cleaned = denoise.clean_body(body)[0]
        units = structure_gate._build_units(structure['sections'], structure.get('groups', []), cleaned.splitlines())
        self.assertTrue(any(legacy_format._flat(unit['stem']) not in legacy_format._flat(cleaned)
                            for unit in units if unit['unit_kind'] == 'group'))
        self.put('01_source/structure.json', structure)
        self.put('01_source/structure.confirmed.json', {'units': units})
        (self.root / '01_source/正文.txt').write_text(body, encoding='utf-8')
        items = []
        for unit in units:
            numbers = unit['numbers']
            parts = question_content.audit_source_unit(unit)['questions']
            row = question(numbers[0]) if unit['unit_kind'] == 'standalone' else group()
            row.update(id='unit-' + str(numbers[0]), unit_id='unit-' + str(numbers[0]),
                       number=numbers[0], numbers=numbers, unit_kind=unit['unit_kind'],
                       question_type=unit['type'], material='', subquestions=[])
            children = [{'number': n, 'stem': parts[n]['stem'], 'answer': 'A', 'knowledge': [],
                         'explanation': '无', 'options': [f"{o['label']}. {o['text']}" for o in parts[n]['options']]}
                        for n in numbers]
            if unit['unit_kind'] == 'standalone':
                row.update(stem=str(numbers[0]) + '.', options=children[0]['options'])
            else:
                prefix = []
                for line in unit['stem'].splitlines():
                    if _strong_number(line) is not None:
                        break
                    prefix.append(line)
                from workflow.shared_rubric import section_rubric
                shared = section_rubric(cleaned.splitlines(),
                                        unit.get('source_span', {}).get('start_line', 1), unit['type'])
                for line in reversed(shared):
                    if line not in prefix:
                        prefix.insert(0, line)
                row.update(stem='\n'.join(prefix), options=[], subquestions=children)
            items.append(row)
        complete = {'batch_id': 'mixed', 'name': 'isolated mixed paper', 'items': items}
        self.assertTrue(question_content.audit_batch_source(self.root, complete)['passed'])
        broken = deepcopy(complete)
        broken['items'][0]['options'] = broken['items'][0]['options'][:1]
        for rel in legacy_format.BATCH_PATHS:
            self.put(rel, broken)
        result = actions.apply_source_option_repair(self.root)
        self.assertTrue(result['installed'], result)
        self.assertEqual(self.load(format_gate.FINAL_PATH), complete)

    def test_other_source_errors_wrong_options_and_ambiguous_bindings_fail_closed(self):
        mutations = [lambda row: row['subquestions'][-1]['options'].__setitem__(0, 'A. Invented'),
                     lambda row: row['subquestions'][-1].update(stem='Missing source stem'),
                     lambda row: row['subquestions'][-1]['options'].reverse(),
                     lambda row: row['subquestions'][-1].update(number=72)]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                batch, _ = self.truncated()
                mutate(batch['items'][0])
                for rel in legacy_format.BATCH_PATHS:
                    self.put(rel, batch)
                before = self.raw_files()
                result = actions.apply_source_option_repair(self.root)
                self.assertFalse(result['installed'], result)
                self.assertEqual(before, self.raw_files())
        self.truncated()
        confirmed = self.load(legacy_format.SOURCE_PATHS[-1])
        confirmed['units'][0]['stem'] += '\nInvented continuation'
        self.put(legacy_format.SOURCE_PATHS[-1], confirmed)
        before = self.raw_files()
        self.assertFalse(actions.apply_source_option_repair(self.root)['installed'])
        self.assertEqual(before, self.raw_files())

    def test_restored_content_invalidates_review_and_authorization_without_erasing_execution(self):
        self.truncated()
        self.add_manifest()
        state = mf.load(self.root)
        targets = ('review', 'review_gate', 'format_gate', 'upload_gate', 'upload', 'publish_gate')
        for stage in targets:
            state['stages'][stage].update(status=mf.COMPLETED, execution_completed=True,
                                          original_execution_id='retained', quality_passed=False)
        mf.save(self.root, state)
        immutable = {rel: (self.root / rel).read_bytes() for rel in
                     ('workflow/review-plan.json', '07_review/review-rounds.json')}
        result = actions.apply_source_option_repair(self.root)
        self.assertTrue(result['installed'], result)
        state = mf.load(self.root)
        for stage in targets:
            self.assertEqual(state['stages'][stage]['status'], mf.INVALIDATED)
            self.assertTrue(state['stages'][stage]['rebuild_required'])
            self.assertEqual(state['stages'][stage]['original_execution_id'], 'retained')
        for rel, raw in immutable.items():
            self.assertEqual((self.root / rel).read_bytes(), raw)

    def test_gate_retry_selects_source_repair_and_rejects_changed_registered_original(self):
        self.truncated()
        self.add_manifest()
        state = mf.load(self.root)
        snapshot = self_heal._snapshot(self.root, state, 'format_gate')
        result = self_heal._apply(self.root, state, 'format_gate', '选项截断',
            {'action': 'retry_stage', 'reason': '按原卷恢复'}, 1, snapshot)
        self.assertTrue(result['changed'], result)
        self.assertEqual(result['effective_action'], 'repair_source_options')
        self.assertEqual(result['verified_scope'], 'source_option_repair')
        self.truncated()
        state = mf.load(self.root)
        snapshot = self_heal._snapshot(self.root, state, 'format_gate')
        original = self.root / '00_original/source.txt'
        original.write_bytes(original.read_bytes() + b'\nExternal original edit')
        before = self.raw_files()
        result = self_heal._apply(self.root, state, 'format_gate', '选项截断',
            {'action': 'repair_source_options', 'reason': '按原卷恢复'}, 1, snapshot)
        self.assertFalse(result['changed'], result)
        self.assertEqual(before, self.raw_files())

    def test_unproductive_deterministic_gate_retry_is_bounded(self):
        self.fixture()
        self.add_manifest()
        state = mf.load(self.root)
        snapshot = self_heal._snapshot(self.root, state, 'format_gate')
        for action in ('retry_stage', 'retry_stage_with_fresh_context', 'refresh_inputs'):
            with self.subTest(action=action), patch.object(self_heal, '_requeue',
                    side_effect=AssertionError('unbounded gate retry')):
                result = self_heal._apply(self.root, state, 'format_gate', 'unchanged gate failure',
                    {'action': action, 'reason': '普通重试'}, 4, snapshot)
            self.assertFalse(result['changed'])
            self.assertEqual(result['status'], 'retry_budget_exhausted')

    def test_atomic_failure_rolls_back_option_files_and_report(self):
        self.truncated()
        format_gate.run(self.root)
        before = self.raw_files()
        real = actions._atomic_raw
        tripped = False
        def fail(root, relative, raw):
            nonlocal tripped
            if relative == legacy_format.BATCH_PATHS[1] and not tripped:
                tripped = True
                raise OSError('option install failure')
            return real(root, relative, raw)
        with patch.object(actions, '_atomic_raw', side_effect=fail):
            result = actions.apply_source_option_repair(self.root)
        self.assertEqual(result['status'], 'rolled_back', result)
        self.assertEqual(before, self.raw_files())
        self.assertFalse(result['installed'])
        self.assertTrue(result['rollback_complete'])

    def test_postinstall_source_failure_and_concurrent_source_drift_roll_back(self):
        self.truncated()
        before = self.raw_files()
        real = actions._atomic_raw
        changed = False
        def drift(root, relative, raw):
            nonlocal changed
            real(root, relative, raw)
            if relative == legacy_format.BATCH_PATHS[0] and not changed:
                changed = True
                path = root / legacy_format.SOURCE_PATHS[0]
                path.write_bytes(path.read_bytes() + b'\nConcurrent source')
        with patch.object(actions, '_atomic_raw', side_effect=drift):
            result = actions.apply_source_option_repair(self.root)
        self.assertEqual(result['status'], 'rolled_back', result)
        for rel in legacy_format.BATCH_PATHS:
            self.assertEqual((self.root / rel).read_bytes(), before[rel])
        self.assertTrue((self.root / legacy_format.SOURCE_PATHS[0]).read_bytes().endswith(b'Concurrent source'))

    def test_needs_review_record_only_is_a_valid_unrepaired_diagnosis(self):
        self.fixture()
        self.add_manifest()
        response = {'response': {'status': 'needs_review', 'artifacts': {'self_heal': {
            'action': 'record_only', 'stage': 'format_gate',
            'reason': '原始证据中没有行列或单元格结构，猜测性转换会编造内容。'}},
            'notes': ['没有执行上传、发布或质量放行。'], 'findings': []}}
        before = self.raw_files()
        with patch.object(self_heal, '_context', return_value='{}'), \
             patch.object(local_agent, 'invoke', return_value=response) as invoke:
            plan = self_heal._plan(self.root, 'format_gate', '无法安全修复材料', config={})
        state = mf.load(self.root)
        result = self_heal._apply(self.root, state, 'format_gate', '无法安全修复材料',
            plan, 1, self_heal._snapshot(self.root, state, 'format_gate'))
        self.assertEqual(invoke.call_count, 1)
        self.assertEqual(plan['action'], 'record_only')
        # A single failing point must never stop the project. record_only on a
        # stage that has an upstream producer becomes a real re-derivation with
        # the exact error, not an infinite park.
        self.assertTrue(result['changed'])
        self.assertFalse(result['verified'])
        self.assertIn(result['status'], ('requeued', 'deferred'))
        self.assertIn(result['outcome'], ('upstream_escalated', 'deferred_and_continuing'))
        # 原卷与产物文件一个字节都没被这条路径改写（只更新了状态台账）。
        after = self.raw_files()
        for relative, payload in before.items():
            if relative == 'workflow/manifest.json':
                continue
            self.assertEqual(after[relative], payload, relative)

    def test_malformed_diagnosis_gets_one_retry_with_concrete_protocol_feedback(self):
        valid = {'response': {'status': 'completed', 'artifacts': {'self_heal': {
            'action': 'record_only', 'stage': 'format_gate', 'reason': '证据不足，保留未完成'}}}}
        malformed = {'response': {'status': 'needs_review', 'artifacts': {}}}
        with patch.object(self_heal, '_context', return_value='{}'), \
             patch.object(local_agent, 'invoke', side_effect=[malformed, valid]) as invoke:
            plan = self_heal._plan(self.root, 'format_gate', '真实错误', config={})
        self.assertEqual(invoke.call_count, 2)
        self.assertEqual(plan['action'], 'record_only')
        first, second = invoke.call_args_list
        self.assertNotEqual(first.args[0], second.args[0])
        self.assertIn('缺少self_heal对象', second.args[0])
        self.assertIn('不得伪造完成', second.args[0])
        self.assertNotEqual(first.args[1], second.args[1])
        self.assertEqual(second.args[1].name, 'protocol-retry-1')

    def test_protocol_retry_stops_and_needs_review_never_authorizes_repairs(self):
        for invalid in ({'response': {'status': 'needs_review', 'artifacts': {}}},
                        {'response': {'status': 'needs_review', 'artifacts': {'self_heal': {
                            'action': 'repair_source_options', 'stage': 'format_gate', 'reason': '尚未判定'}}}},
                        {'response': {'status': 'completed', 'artifacts': {'self_heal': {
                            'action': ['record_only'], 'stage': 'format_gate', 'reason': '坏结构'}}}},
                        {'response': {'status': 'completed', 'artifacts': {'self_heal': {
                            'action': 'record_only', 'stage': 'format_gate', 'reason': []}}}}):
            with self.subTest(invalid=invalid), \
                 patch.object(self_heal, '_context', return_value='{}'), \
                 patch.object(local_agent, 'invoke', return_value=invalid) as invoke:
                with self.assertRaises(ValueError):
                    self_heal._plan(self.root, 'format_gate', '真实错误', config={})
                self.assertEqual(invoke.call_count, 2)

    def test_transport_error_is_not_retried_as_a_schema_failure(self):
        with patch.object(self_heal, '_context', return_value='{}'), \
             patch.object(local_agent, 'invoke', side_effect=local_agent.AgentError('额度不足', 'quota')) as invoke:
            with self.assertRaises(local_agent.AgentError):
                self_heal._plan(self.root, 'format_gate', '真实错误', config={})
        self.assertEqual(invoke.call_count, 1)

    def test_partial_source_does_not_supply_missing_d_option(self):
        self.truncated()
        path = self.root / legacy_format.SOURCE_PATHS[0]
        path.write_text(path.read_text().replace(' D.Dogs', ''), encoding='utf-8')
        confirmed = self.load(legacy_format.SOURCE_PATHS[-1])
        confirmed['units'][0]['stem'] = confirmed['units'][0]['stem'].replace(' D.Dogs', '')
        self.put(legacy_format.SOURCE_PATHS[-1], confirmed)
        before = self.raw_files()
        self.assertFalse(actions.apply_source_option_repair(self.root)['installed'])
        self.assertEqual(before, self.raw_files())


class KnowledgeRejudgmentTest(unittest.TestCase):
    write = feedback_fixture.RepairFeedbackTest.write
    context = feedback_fixture.RepairFeedbackTest.context
    response = feedback_fixture.RepairFeedbackTest.response

    def setUp(self):
        feedback_fixture.RepairFeedbackTest.setUp(self)
        # The source has no unbound trailing prose inside the last option.
        self.source = self.source.replace('End of paper\n', '')
        self.write('01_source/正文.txt', self.source)
        self.write('original.txt', self.source)
        self.addCleanup(patch.stopall)
        patch('socket.socket.connect', side_effect=AssertionError('network forbidden')).start()
        patch('subprocess.Popen', side_effect=AssertionError('process/model forbidden')).start()
        payload = {'batch_id': 'fixture', 'name': 'test-only source', 'items': []}
        for unit in self.units:
            part = question_content.audit_source_unit(unit)['questions'][unit['numbers'][0]]
            row = question(unit['numbers'][0])
            row.update(id=unit['unit_id'], unit_id=unit['unit_id'], unit_kind='standalone',
                       number=unit['numbers'][0], numbers=unit['numbers'], question_type='单项填空',
                       stem=part['stem'], options=[f"{o['label']}. {o['text']}" for o in part['options']],
                       knowledge=['词汇/词汇辨析/动词辨析'])
            payload['items'].append(row)
        self.payload = payload
        for rel in legacy_format.BATCH_PATHS:
            self.write(rel, payload)
        manifest = mf.new_manifest('fixture', self.batch, self.manifest['inputs'], self.manifest['meta'])
        for record in manifest['stages'].values():
            record.update(status=mf.COMPLETED, execution_completed=True, original_execution_id='retain')
        mf.save(self.batch, manifest)
        self.write('07_review/review-rounds.json', {'original_execution_id': 'immutable'})
        self.config = dict(self.config, candidate_cache=False, part_concurrency=1)
        self.config.pop('_part_model_binding')

    def illegal(self):
        payload = deepcopy(self.payload)
        payload['items'][0]['knowledge'] = ['语用/情景交际/询问目的']
        for rel in legacy_format.BATCH_PATHS:
            self.write(rel, payload)

    def raw(self):
        return {str(p.relative_to(self.batch)): p.read_bytes() for p in self.batch.rglob('*')
                if p.is_file() and actions.MIGRATIONS not in p.relative_to(self.batch).as_posix()
                and p.name != '.source-format-repair.lock'}

    def test_gate_retry_routes_to_real_knowledge_feedback_and_invalidates_consumers(self):
        self.illegal()
        state = mf.load(self.batch)
        snapshot = self_heal._snapshot(self.batch, state, 'format_gate')
        old_batch = (self.batch / format_gate.FINAL_PATH).read_bytes()
        old_review = (self.batch / '07_review/review-rounds.json').read_bytes()
        with patch.object(self_heal, '_requeue', side_effect=AssertionError('gate retry forbidden')):
            result = self_heal._apply(self.batch, state, 'format_gate', 'illegal knowledge',
                {'action': 'retry_stage', 'reason': '重新检查'}, 1, snapshot)
        self.assertEqual(result['target_stage'], 'knowledge')
        self.assertEqual(result['outcome'], 'retry_scheduled')
        self.assertFalse(result['verified'])
        self.assertTrue(result['changed'], result)
        feedback = json.loads((self.batch / result['feedback_path']).read_text())
        self.assertEqual(feedback['unit_ids'], ['q-1'])
        self.assertFalse(feedback['all_units'])
        self.assertIn('目录', feedback['feedback_by_unit']['q-1']['error'])
        state = mf.load(self.batch)
        self.assertEqual(state['stages']['knowledge']['status'], mf.PENDING)
        for stage in ('assemble', 'review', 'review_gate', 'format_gate', 'upload_gate'):
            self.assertEqual(state['stages'][stage]['status'], mf.INVALIDATED)
        self.assertEqual(old_batch, (self.batch / format_gate.FINAL_PATH).read_bytes())
        self.assertEqual(old_review, (self.batch / '07_review/review-rounds.json').read_bytes())

    def test_actual_local_agent_entry_reexecutes_only_bad_unit_with_feedback(self):
        calls, prompts = [], []
        def invoke(prompt, run_dir, config, **options):
            context = self.context(prompt)
            uid = context['confirmed_units_with_stable_ids'][0]['unit_id']
            calls.append(uid)
            prompts.append((uid, context))
            row = next(row for row in self.knowledge['items'] if row['unit_id'] == uid)
            return {'response': self.response('knowledge', [row])}
        errors = io.StringIO()
        # 标注阶段默认整卷一次分析；这里显式开启分片，验证“只重跑坏单元”的分片能力仍在。
        cfg = dict(self.config, label_split=True)
        with patch.object(local_agent, 'load_config', return_value=cfg), \
             patch.object(local_agent, 'invoke', side_effect=invoke), \
             patch.object(local_agent, '_agent_lock', side_effect=lambda *_: contextlib.nullcontext()), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
            self.assertEqual(local_agent.main(['--stage', 'knowledge', '--batch-dir', str(self.batch)]), 0, errors.getvalue())
            self.assertEqual(calls, ['q-1', 'q-2'])
            self.illegal()
            source_before = (self.batch / '01_source/正文.txt').read_bytes()
            scheduled = actions.schedule_knowledge_repair(self.batch)
            self.assertTrue(scheduled['installed'], scheduled)
            self.assertEqual(local_agent.main(['--stage', 'knowledge', '--batch-dir', str(self.batch)]), 0, errors.getvalue())
        self.assertEqual(calls, ['q-1', 'q-2', 'q-1'])
        self.assertEqual(prompts[-1][1]['repair_feedback']['unit_ids'], ['q-1'])
        self.assertNotIn('repair_feedback', prompts[1][1])
        self.assertEqual(source_before, (self.batch / '01_source/正文.txt').read_bytes())
        accepted = json.loads((self.batch / agent_tasks.ARTIFACTS['knowledge']).read_text())
        self.assertEqual([r['unit_id'] for r in accepted['items']], ['q-1', 'q-2'])
        self.assertEqual(accepted['items'][1]['knowledge'], self.knowledge['items'][1]['knowledge'])

    def test_same_invalid_labels_budget_never_resets_by_requeued_manifest(self):
        self.illegal()
        for attempt in range(1, 4):
            result = actions.schedule_knowledge_repair(self.batch)
            self.assertTrue(result['installed'], result)
            self.assertEqual(result['attempt'], attempt)
        before = self.raw()
        exhausted = actions.schedule_knowledge_repair(self.batch)
        self.assertEqual(exhausted['status'], 'retry_budget_exhausted', exhausted)
        self.assertFalse(exhausted['installed'])
        self.assertEqual(before, self.raw())

    def test_feedback_install_failure_restores_manifest_and_original_feedback(self):
        self.illegal()
        self.write('workflow/repair-feedback/knowledge.json', {'existing': 'untouched raw evidence'})
        before = self.raw()
        real = actions._atomic_raw
        tripped = False
        def fail(root, relative, raw):
            nonlocal tripped
            if relative == 'workflow/manifest.json' and not tripped:
                tripped = True
                raise OSError('knowledge scheduling failure')
            return real(root, relative, raw)
        with patch.object(actions, '_atomic_raw', side_effect=fail):
            result = actions.schedule_knowledge_repair(self.batch)
        self.assertEqual(result['status'], 'rolled_back', result)
        self.assertEqual(before, self.raw())
        self.assertFalse(result['verified'])

    def test_concurrent_source_and_expected_output_drift_prevent_scheduling(self):
        self.illegal()
        before = self.raw()
        result = actions.schedule_knowledge_repair(self.batch,
            expected_inputs={format_gate.FINAL_PATH: '0' * 64})
        self.assertFalse(result['installed'])
        self.assertEqual(before, self.raw())
        real = actions._atomic_raw
        changed = False
        def drift(root, relative, raw):
            nonlocal changed
            real(root, relative, raw)
            if relative == 'workflow/repair-feedback/knowledge.json' and not changed:
                changed = True
                path = root / '01_source/正文.txt'
                path.write_bytes(path.read_bytes() + b'\nExternal source change')
        with patch.object(actions, '_atomic_raw', side_effect=drift):
            result = actions.schedule_knowledge_repair(self.batch)
        self.assertEqual(result['status'], 'rolled_back', result)
        after = self.raw()
        for rel, raw in before.items():
            if rel != '01_source/正文.txt':
                self.assertEqual(after[rel], raw, rel)
        self.assertNotIn('workflow/repair-feedback/knowledge.json', after)
        self.assertTrue(after['01_source/正文.txt'].endswith(b'External source change'))

    def test_rollback_preserves_a_concurrent_foreign_feedback_edit(self):
        self.illegal()
        old_manifest = (self.batch / 'workflow/manifest.json').read_bytes()
        real = actions._atomic_raw
        changed = False
        def drift(root, relative, raw):
            nonlocal changed
            real(root, relative, raw)
            if relative == 'workflow/repair-feedback/knowledge.json' and not changed:
                changed = True
                (root / relative).write_bytes(b'foreign concurrent feedback')
        with patch.object(actions, '_atomic_raw', side_effect=drift):
            result = actions.schedule_knowledge_repair(self.batch)
        self.assertEqual(result['status'], 'rollback_failed', result)
        self.assertFalse(result['installed'])
        self.assertFalse(result['rollback_complete'])
        self.assertEqual((self.batch / 'workflow/repair-feedback/knowledge.json').read_bytes(), b'foreign concurrent feedback')
        self.assertEqual((self.batch / 'workflow/manifest.json').read_bytes(), old_manifest)
        receipt = json.loads((self.batch / result['receipt_path']).read_text())
        self.assertFalse(receipt['passed'])
        self.assertEqual(receipt['status'], 'rollback_failed')

    def test_incomplete_question_source_never_routes_to_label_guessing(self):
        self.illegal()
        payload = deepcopy(self.payload)
        payload['items'][0].update(stem='Truncated stem', knowledge=['非法目录'])
        self.write(format_gate.FINAL_PATH, payload)
        before = self.raw()
        result = actions.schedule_knowledge_repair(self.batch)
        self.assertFalse(result['installed'], result)
        self.assertEqual(before, self.raw())


if __name__ == '__main__':
    unittest.main()


class UpstreamEscalationTest(unittest.TestCase):
    """A parked downstream stage must escalate to its producer instead of waiting."""

    def test_repeated_record_only_preassemble_requeues_structure_gate_once(self):
        import tempfile
        from pathlib import Path as _Path
        from workflow import contracts, manifest as _mf, self_heal as _heal

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        batch = _Path(tmp.name)
        (batch / "01_source").mkdir(parents=True)
        (batch / "01_source/structure.confirmed.json").write_text('{"units": []}', encoding="utf-8")
        state = _mf.new_manifest("escalate", batch, {}, {})
        _mf.touch_stage(state, "structure_gate", _mf.COMPLETED)
        _mf.touch_stage(state, "preassemble", _mf.FAILED)
        _mf.save(batch, state)

        result = _heal._apply(batch, state, "preassemble", "Cannot fill batch without proven source binding",
                              {"action": "record_only", "reason": "没有已验证修复"}, 2,
                              {"files": {}})
        self.assertTrue(result["changed"], result)
        self.assertEqual(result["status"], "requeued")
        self.assertEqual(result["target_stage"], "structure_gate")
        self.assertIn(result["outcome"], ("upstream_escalated", "deferred_and_continuing"))
        self.assertFalse(result["verified"])
        reloaded = _mf.load(batch)
        self.assertEqual(reloaded["stages"]["structure_gate"]["status"], _mf.PENDING)

    def test_escalation_is_bounded_per_confirmed_structure(self):
        import tempfile
        from pathlib import Path as _Path
        from workflow import manifest as _mf, self_heal as _heal

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        batch = _Path(tmp.name)
        (batch / "01_source").mkdir(parents=True)
        (batch / "01_source/structure.confirmed.json").write_text('{"units": []}', encoding="utf-8")
        state = _mf.new_manifest("escalate2", batch, {}, {})
        _mf.touch_stage(state, "structure_gate", _mf.COMPLETED)
        _mf.touch_stage(state, "preassemble", _mf.FAILED)
        _mf.save(batch, state)
        args = ("preassemble", "Cannot fill batch without proven source binding",
                {"action": "record_only", "reason": "没有已验证修复"}, 2, {"files": {}})
        self.assertTrue(_heal._apply(batch, state, *args)["changed"])
        # Same stage + same error + same evidence must never bounce upstream twice.
        # Preassembly is now a model stage, so after the bounded source retry it
        # can retry its own type work with concrete feedback.
        again = _heal._apply(batch, _mf.load(batch), "preassemble",
                             "Cannot fill batch without proven source binding",
                             {"action": "record_only", "reason": "没有已验证修复"}, 2, {"files": {}})
        self.assertTrue(again["changed"])
        self.assertEqual(again["status"], "requeued")
        self.assertEqual(again["outcome"], "content_retry_with_feedback")
        self.assertEqual(again["target_stage"], "preassemble")
        self.assertFalse(again["verified"])
        self.assertTrue((batch / again["feedback_path"]).is_file())
        self.assertEqual(_mf.load(batch)["stages"]["structure_gate"]["status"], _mf.PENDING)

    def test_first_attempt_still_escalates_instead_of_parking(self):
        import tempfile
        from pathlib import Path as _Path
        from workflow import manifest as _mf, self_heal as _heal

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        batch = _Path(tmp.name)
        (batch / "01_source").mkdir(parents=True)
        state = _mf.new_manifest("escalate3", batch, {}, {})
        _mf.save(batch, state)
        result = _heal._apply(batch, state, "preassemble", "err",
                              {"action": "record_only", "reason": "r"}, 1, {"files": {}})
        self.assertTrue(result["changed"])
        # Unknown free-text errors stay with the preassembly model stage;
        # they do not guess a different producer or claim a verified repair.
        self.assertEqual(result["status"], "requeued")
        self.assertEqual(result["outcome"], "content_retry_with_feedback")
        self.assertEqual(result["target_stage"], "preassemble")
        self.assertFalse(result["verified"])
        self.assertTrue((batch / result["feedback_path"]).is_file())
        self.assertEqual(_mf.load(batch)["stages"]["preassemble"]["status"], _mf.PENDING)


class NeverParkTest(unittest.TestCase):
    """任何单点失败都不能让整条流程停住：只能回上游重算或延后并继续。"""

    def make(self, name):
        import tempfile
        from pathlib import Path as _Path
        from workflow import manifest as _mf
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        batch = _Path(tmp.name)
        (batch / "01_source").mkdir(parents=True)
        (batch / "01_source/structure.confirmed.json").write_text('{"units": []}', encoding="utf-8")
        state = _mf.new_manifest(name, batch, {}, {})
        _mf.save(batch, state)
        return batch, state

    def test_upstream_map_covers_every_resumable_stage(self):
        from workflow import self_heal as _heal
        for stage in ("structure_gate", "preassemble", "answers", "audio", "types",
                      "explanations", "knowledge", "assemble", "meta", "underlines",
                      "validate", "review", "review_gate", "format_gate"):
            self.assertIn(stage, _heal.UPSTREAM, stage)
            self.assertNotIn(_heal.UPSTREAM[stage], _heal.BOUNDARIES, stage)

    def test_record_only_stops_same_signature_after_retry_budget(self):
        from workflow import self_heal as _heal, manifest as _mf
        batch, state = self.make("record-budget")
        for stage in ("review", "types", "preassemble"):
            with self.subTest(stage=stage), patch.object(_heal, "_escalate_upstream") as escalate:
                result = _heal._apply(batch, state, stage,
                                      "Cannot fill batch without proven source binding",
                                      {"action": "record_only", "reason": "证据不足，无法安全修复"},
                                      _heal.MAX_ATTEMPTS_PER_STAGE, {"files": {}})
                self.assertFalse(result["changed"])
                self.assertEqual(result["status"], "retry_budget_exhausted")
                self.assertEqual(result["outcome"], "retry_budget_exhausted")
                self.assertEqual(result["escalation"], "code_repair")
                # Preserve the original failure and its signature. Re-diagnosis
                # remains scheduled; exhaustion cannot manufacture a human wait.
                self.assertEqual(_mf.load(batch)["stages"][stage], state["stages"][stage])
                self.assertNotIn("待人工", result["reason"])
                escalate.assert_not_called()

    def test_record_only_on_any_agent_stage_never_returns_waiting_external(self):
        from workflow import self_heal as _heal
        for stage in ("answers", "types", "explanations", "knowledge", "assemble"):
            batch, state = self.make("np-" + stage)
            result = _heal._apply(batch, state, stage, "某处校验失败",
                                  {"action": "record_only", "reason": "证据里没有可改写的内容"}, 1,
                                  {"files": {}})
            self.assertTrue(result["changed"], stage)
            self.assertNotEqual(result["status"], "waiting_external", stage)
            self.assertIn(result["outcome"],
                          {"upstream_escalated", "content_retry_with_feedback", "deferred_and_continuing"},
                          stage)

    def test_defer_keeps_other_stages_moving(self):
        from workflow import self_heal as _heal, manifest as _mf
        batch, state = self.make("defer1")
        _mf.touch_stage(state, "preassemble", _mf.FAILED)
        _mf.touch_stage(state, "assemble", _mf.PENDING)
        _mf.save(batch, state)
        result = _heal._apply(batch, state, "preassemble", "外部额度暂不可用",
                              {"action": "defer_and_continue", "reason": "外部模型额度暂不可用"}, 1,
                              {"files": {}})
        self.assertTrue(result["changed"])
        self.assertEqual(result["outcome"], "deferred_and_continuing")
        reloaded = _mf.load(batch)
        # 该阶段被明确延后（不是卡死），其它未完成阶段仍可被调度执行。
        self.assertEqual(reloaded["stages"]["preassemble"]["status"], _mf.PENDING)
        self.assertIn("延后", reloaded["stages"]["preassemble"].get("notes", ""))
        self.assertEqual(reloaded["stages"]["assemble"]["status"], _mf.PENDING)

    def test_model_prose_never_selects_the_producer_stage(self):
        from workflow import self_heal as _heal
        batch, _ = self.make("prose")
        detail = ("Stage preconditions unmet: Prerequisite preassemble must have a current, "
                  "consumer-validated delivery\ntypes 与题文、答案等本地数据无关")
        # 自由文本里出现「答案」不能把已完成的 answers 拉回重跑；只认机器证据。
        self.assertEqual(_heal._error_producer(batch, "types", detail), "preassemble")
        self.assertEqual(
            _heal._error_producer(batch, "answers", "Batch fill正文 conflict for q-6; existing content preserved"),
            "preassemble")

    def test_same_error_never_bounces_upstream_twice(self):
        from workflow import self_heal as _heal, manifest as _mf
        batch, state = self.make("cycle1")
        _mf.touch_stage(state, "structure_gate", _mf.COMPLETED)
        _mf.touch_stage(state, "preassemble", _mf.FAILED)
        _mf.save(batch, state)
        # Without structured producer evidence, recovery defers instead of
        # blindly bouncing to a guessed upstream stage.
        plan = {"action": "upstream_rederive", "reason": "无法在本地修复"}
        first = _heal._apply(batch, state, "preassemble", "同样的错误", plan, 1, {"files": {}})
        self.assertIn(first["outcome"], ("deferred_and_continuing", "upstream_escalated"))
        second = _heal._apply(batch, _mf.load(batch), "preassemble", "同样的错误", plan, 2, {"files": {}})
        self.assertEqual(second["outcome"], "deferred_and_continuing")
        self.assertTrue(second["changed"])

    def test_changed_evidence_allows_a_fresh_upstream_derivation(self):
        from workflow import self_heal as _heal, manifest as _mf
        batch, state = self.make("cycle2")
        _mf.touch_stage(state, "structure_gate", _mf.COMPLETED)
        _mf.touch_stage(state, "preassemble", _mf.FAILED)
        _mf.save(batch, state)
        plan = {"action": "upstream_rederive", "reason": "无法在本地修复"}
        _heal._apply(batch, state, "preassemble", "同样的错误", plan, 1, {"files": {}})
        (batch / "01_source/structure.confirmed.json").write_text('{"units": [{"numbers": [1]}]}', encoding="utf-8")
        again = _heal._apply(batch, _mf.load(batch), "preassemble", "同样的错误", plan, 2, {"files": {}})
        self.assertIn(again["outcome"], ("upstream_escalated", "deferred_and_continuing"))
