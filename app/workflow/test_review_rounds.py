"""Real-round contracts, portable handoff and separate invocation regressions."""
import contextlib
import concurrent.futures
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import agent_tasks, ingest, review, review_rounds
from workflow import local_agent as agent


class ReviewRoundsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        stem = 'She reads a book.\n' + '\n'.join(
            f'{n}. What happens in question {n}?\nA. book{n} B. pen{n} C. map{n} D. bag{n}' for n in range(1, 7))
        self.write('01_source/正文.txt', stem)
        self.write("01_source/structure.confirmed.json", {"units": [{"unit_id": "group-1-6", "unit_kind": "group", "numbers": [1, 2, 3, 4, 5, 6], "stem": stem}]})
        self.write("03_types/types.json", {"items": [{"unit_id": "group-1-6", "type": "阅读理解", "subtypes": ["阅读判断"], "evidence": ["L1"]}]})
        self.knowledge = {"knowledge": ["语篇体裁/记叙文/故事", "主题/人与自我/生活与学习/日常起居"], "subquestions": [
            {"number": str(n), "knowledge": ["设题角度/阅读/细节理解"]} for n in range(1, 7)]}
        self.write("04_knowledge/knowledge.json", {"items": [dict(self.knowledge, unit_id="group-1-6")]})
        self.write("01_source_ans/answers.json", {"answers": {str(n): "A" for n in range(1, 7)},
                   "difficulty": {str(n): "容易" for n in range(1, 7)}})
        self.write("04_knowledge/explanations.json", {"items": [{
            "unit_id": "group-1-6", "explanation": "", "explanation_origin": "not_applicable",
            "articleSummary": "短文介绍她读书的情景。（AI生成，待校对）", "summary_origin": "ai",
            "evidence": ["正文 L1–6"], "subquestions": [
                {"number": str(n), "explanation": ("主语 She 是第三人称单数，谓语用 reads。" if n == 1
                    else "原文说她读一本书，因此选 a book。") + "（AI生成，待校对）",
                 "explanation_origin": "ai", "evidence": [f"正文 L1、L{n}；答案第{n}题"]}
                for n in range(1, 7)]}]})
        self.write("06_batch/batch.json", {"items": []})
        self.plan = review.prepare_round_plan(self.batch)

    def write(self, relative, value):
        path = self.batch / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def checks(self, round_id):
        targets = self.plan["sample"] if round_id == "sample" else [{"unit_id": "group-1-6"}]
        checks = []
        for target in targets:
            group_level = round_id == 'sample' and target.get('number') is None
            numbers = ([str(n) for n in range(1, 7)] if group_level or round_id != 'sample'
                       else [str(target['number'])])
            assessment = {'type': '阅读理解', 'subtypes': ['阅读判断'],
                          'knowledge': copy.deepcopy(self.knowledge['knowledge']),
                          'subquestions': [copy.deepcopy(child) for child in self.knowledge['subquestions']
                                           if child['number'] in numbers],
                          'difficulty': [{'number': number, 'value': '容易'} for number in numbers]}
            if round_id == 'sample':
                assessment['source_comparison'] = 'The selected group is compared against its source and answer.'
            check = {'unit_id': target['unit_id'],
                     'verdict': 'ok', 'rationale': 'The article and question require detail retrieval, with evidence in the sentence.',
                     'evidence': [{'path': '01_source/正文.txt', 'locator': 'L1',
                                   'quote': ('She reads a book.' if group_level
                                             else (f"What happens in question {target['number']}?" if round_id == 'sample'
                                                   else 'She reads a book.'))}],
                     'assessment': assessment}
            if round_id == 'sample' and not group_level:
                check['number'] = str(target['number'])
            checks.append(check)
        return checks

    def payload(self):
        return {"plan": self.plan, "rounds": [{"round_id": r, "input_binding": self.plan["input_binding"],
                "execution_id": "fixture-separate-" + r, "checks": self.checks(r)} for r in review.ROUND_IDS]}

    def response(self, round_id):
        return {"status": "completed", "artifacts": {"review_round": {"checks": self.checks(round_id)}}, "notes": [], "findings": []}

    def test_blind_partitions_hide_labels_and_keep_source_evidence(self):
        self.write('06_batch/batch.json', {'items': [{'unit_id': 'group-1-6',
                   'question_type': 'SECRET_TYPE', 'knowledge': ['SECRET_KNOWLEDGE'],
                   'filterMetadata': {'subtypes': ['SECRET_SUBTYPE']},
                   'subquestions': [{'number': '1', 'knowledgeIds': ['SECRET_ID'],
                                     'filterMetadata': {'categories': ['SECRET_CATEGORY']}}],
                   'explanation': 'assembled-evidence'}]})
        self.write('03_types/types.json', {'items': [{'unit_id': 'group-1-6', 'type': 'SECRET_TYPE'}]})
        self.write('04_knowledge/knowledge.json', {'items': [{'unit_id': 'group-1-6', 'knowledge': ['SECRET_KNOWLEDGE']}]})
        self.write('01_source_ans/answers.json', {'answers': {'1': 'A'},
                   'evidence': {'1': 'answer-evidence', 'provenance': 'shared-evidence'},
                   'difficulty': {'1': 'SECRET_DIFFICULTY'}, 'difficultyEvidence': {'1': ['SECRET_DIFFICULTY_BASIS']}})
        self.write('02_audio/full-transcript.json', {'text': 'complete-dialogue-evidence'})
        base = agent_tasks.build_task(self.batch, 'review', max_input_chars=1000000)
        for rid in review.ROUND_IDS:
            part = review_rounds._partition_contexts(base, 1000000, round_id=rid)[0]
            text = part['context']
            for secret in ('SECRET_TYPE', 'SECRET_KNOWLEDGE', 'SECRET_ID', 'SECRET_SUBTYPE', 'SECRET_CATEGORY', 'SECRET_DIFFICULTY', 'SECRET_DIFFICULTY_BASIS'):
                self.assertNotIn(secret, text)
            for evidence in ('She reads a book.', 'answer-evidence', 'shared-evidence',
                             'assembled-evidence', 'complete-dialogue-evidence'):
                self.assertIn(evidence, text)
            prompt = review_rounds._round_prompt(rid, [], text)
            self.assertIn('被程序刻意隐藏', prompt)
            self.assertIn('程序负责与原标签比较', prompt)
            self.assertNotIn('必须先引用最终批次中的当前值', prompt)
            self.assertNotIn('字段为空才可报告缺失', prompt)
            data = json.loads(text.split('\n', 1)[1])
            supplied_catalog = data['references/knowledge-catalog.json']
            original_catalog = json.loads((Path(review.__file__).resolve().parents[1]
                                           / 'references/knowledge-catalog.json').read_text())
            # Supplied points carry leaf/leafTerminal markers; compare content only.
            def strip(cat):
                return [{k: v for k, v in p.items() if k not in ('leaf', 'leafTerminal')}
                        for p in cat['points']]
            self.assertEqual(strip(supplied_catalog), strip(original_catalog))
            self.assertTrue(all('leafTerminal' in p for p in supplied_catalog['points']))

    @patch.object(review, '_verify_round_sources', new=lambda *args: None)
    def test_difficulty_blind_assessment_and_comparison(self):
        exported = review_rounds.export_task(self.batch)
        package = Path(exported['package_dir'])
        schema = json.loads((package / 'response.schema.json').read_text())
        assessment = schema['properties']['artifacts']['properties']['review_round']['properties']['checks']['items']['properties']['assessment']
        assessment = next((branch for branch in assessment.get('oneOf', []) if branch.get('type') == 'object'), assessment)
        self.assertNotIn('difficulty', assessment['required'])
        record = self.payload()['rounds'][0]
        self.assertEqual(review._round_checks(record, 'labels', self.plan, self.batch), [])
        payload = self.payload()
        payload['rounds'][0]['checks'][0]['assessment']['difficulty'][2]['value'] = '困难'
        result = review.evaluate_rounds(self.batch, payload)
        # 难度不参与审核比对：难度变化不产生 finding，也不影响通过。
        self.assertTrue(result['execution_completed'])
        self.assertFalse(any(f.get('field') == 'difficulty' for f in result['findings']))
        for bad in ([], [{'number': '1', 'value': '容易'}] * 6,
                    [{'number': str(n), 'value': '未知'} for n in range(1, 7)]):
            record['checks'][0]['assessment']['difficulty'] = bad
            with self.assertRaises(ValueError):
                review._round_checks(record, 'labels', self.plan, self.batch)
        record['checks'][0]['assessment'].pop('difficulty')
        self.assertEqual(review._round_checks(record, 'labels', self.plan, self.batch), [])

    @patch.object(review, '_verify_round_sources', new=lambda *args: None)
    def test_first_round_label_difference_stays_a_manual_finding(self):
        """一审与原标签不同=真实差异，必须进待审核，不能当“过期结论”丢掉。"""
        payload = self.payload()
        labels = next(r for r in payload['rounds'] if r['round_id'] == 'labels')
        labels['checks'][0]['assessment']['knowledge'] = [
            '语篇体裁/记叙文/故事', '主题/人与自我/生活与学习/日常活动']
        result = review.evaluate_rounds(self.batch, payload)
        self.assertIn(('group-1-6', 'knowledge'),
                      [(f.get('unit_id'), f.get('field')) for f in result['findings']])
        self.assertFalse(result['passed'])
        self.assertIn('REVIEW_ROUND_FINDING', {e['code'] for e in result['errors']})

    def test_rule_catalog_evidence_without_prefix_counts_as_bound(self):
        """模型引用 references/*.json 时省略 rules: 前缀，不能因此判整轮失败。"""
        record = self.payload()['rounds'][0]
        record['checks'][0]['evidence'] = [
            {'path': '01_source/正文.txt', 'locator': 'L1', 'quote': 'She reads a book.'},
            {'path': 'references/knowledge-catalog.json', 'locator': 'points', 'quote': 'points'},
        ]
        self.assertEqual(review._round_checks(record, 'labels', self.plan, self.batch), [])
        self.assertEqual(record['checks'][0]['evidence'][1]['path'],
                         'rules:references/knowledge-catalog.json')

    def test_extra_evidence_with_wrong_path_is_remapped_to_bound_source(self):
        self.write('01_source/附录.txt', '1~5 CACBA\n')
        self.plan = review.prepare_round_plan(self.batch)
        record = {'round_id': 'labels', 'input_binding': self.plan['input_binding'],
                  'execution_id': 'fixture-separate-labels', 'checks': self.checks('labels')}
        check = record['checks'][0]
        check['evidence'].append({'path': '01_source/正文.txt', 'locator': '附录答案',
                                  'quote': '1~5 CACBA'})
        self.assertEqual(review._round_checks(record, 'labels', self.plan, self.batch), [])
        self.assertEqual(check['evidence'][-1]['path'], '01_source/附录.txt')

    def test_sample_check_without_source_comparison_is_rejected(self):
        record = {'round_id': 'sample', 'input_binding': self.plan['input_binding'],
                  'execution_id': 'fixture-separate-sample', 'checks': self.checks('sample')}
        for check in record['checks']:
            if isinstance(check.get('assessment'), dict):
                check['assessment'].pop('source_comparison', None)
        with self.assertRaisesRegex(ValueError, 'source comparison'):
            review._round_checks(record, 'sample', self.plan, self.batch)

    def test_sample_prompt_declares_source_comparison(self):
        prompt = review_rounds._round_prompt('sample', [], '{}')
        self.assertIn('source_comparison:string', prompt)
        self.assertIn('二审的 assessment 必须包含', prompt)

    def test_prompt_forbids_option_types_without_letter_options(self):
        """无 A–D 选项的填空题不得判单项填空；示例也不得诱导照抄单项填空/四个选项。

        真实回归：Starter Unit 1 课时4 一审把「根据句意用所给词的适当形式填空」
        6 题判成 单项填空/四个选项（原标签 单词拼写/适当形式），造成 12 项假差异。
        """
        for rid in ('labels', 'sample'):
            with self.subTest(round=rid):
                prompt = review_rounds._round_prompt(rid, [], '{}')
                self.assertIn('【无选项不判选项题型·必须遵守】', prompt)
                self.assertIn('不得因为要填子类', prompt)
                self.assertIn('三个选项、图片选项、共享选项和听力选项', prompt)
                self.assertIn('不得为了目录校验挑一个', prompt)
                # 旧的示例把「单项填空/四个选项」放在必读句里，容易被照抄。
                self.assertNotIn('『单项填空』有 四个选项 等', prompt)
                # 子类必填本身仍然生效（不能为了消除误判而放松）。
                self.assertIn('【子类必填·必须遵守】', prompt)


    def test_prompt_change_gets_new_export_without_changing_frozen_task(self):
        first = review_rounds.export_task(self.batch, only_round='labels')
        package = Path(first['package_dir']) / 'task.json'
        frozen = package.read_bytes()
        original = review_rounds._round_prompt
        with patch.object(review_rounds, '_round_prompt', side_effect=lambda *args:
                          original(*args) + "\nNEW INSTRUCTION REVISION"):
            second = review_rounds.export_task(self.batch, only_round='labels')
        self.assertNotEqual(first['task_id'], second['task_id'])
        self.assertEqual(package.read_bytes(), frozen)
        self.assertIn('NEW INSTRUCTION REVISION',
                      json.loads((Path(second['package_dir']) / 'task.json').read_text())['prompt'])
        review_rounds._check_bound_inputs(self.batch, first['task_id'])

    def test_explicit_self_negation_is_rejected_without_rewriting_labels(self):
        bad = {'assessment': {'type': '单项填空', 'subtypes': ['四个选项']},
               'rationale': '本空考查be动词，按单项填空/四个选项记录不合理，故仅判断知识点。'}
        with self.assertRaisesRegex(ValueError, 'REVIEW_ASSESSMENT_CONTRADICTION'):
            review_rounds._reject_assessment_contradiction(bad)
        self.assertEqual(bad['assessment']['type'], '单项填空')
        for reason in ('原标签是单词拼写，但本题应判单项填空。',
                       '图片选项未见，不能判断答案。',
                       '题型是单项填空，答案不正确。',
                       '上一轮判成单项填空不合理。',
                       '第2题不是单项填空。'):
            review_rounds._reject_assessment_contradiction(dict(bad, rationale=reason))
        review_rounds._reject_assessment_contradiction(dict(bad, assessment=None))

    def test_contradiction_uses_failed_target_path_and_preserves_valid_checks(self):
        good = self.checks('labels')[0]
        bad = copy.deepcopy(good)
        bad['unit_id'] = 'bad-unit'
        bad['assessment']['type'] = '单项填空'
        bad['assessment']['subtypes'] = ['四个选项']
        bad['rationale'] = '按单项填空/四个选项记录不合理'
        valid, failed = review_rounds._partition_check_issues(
            review, self.plan, 'labels', [good, bad], self.batch)
        self.assertEqual(valid, [good])
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]['unit_id'], 'bad-unit')
        self.assertIn('REVIEW_ASSESSMENT_CONTRADICTION', failed[0]['reason'])

    def test_catalog_filter_retains_metadata_and_unknown_type_falls_back(self):
        from workbench import taxonomy
        catalog = json.loads(taxonomy.KNOWLEDGE_PATH.read_text())
        scoped = review.compact_knowledge_catalog(['阅读理解'], catalog)
        self.assertEqual(scoped['version'], catalog['version'])
        self.assertEqual(scoped['versionId'], catalog['versionId'])
        self.assertLess(len(scoped['points']), len(catalog['points']))
        # Every supplied point gains a leaf/leafTerminal marker; content unchanged.
        for point in scoped['points']:
            self.assertIn('leaf', point)
            self.assertIn('leafTerminal', point)
        base = {p['path']: {k: v for k, v in p.items() if k not in ('leaf', 'leafTerminal')}
                for p in catalog['points']}

        def strip(cat):
            return {p['path']: {k: v for k, v in p.items() if k not in ('leaf', 'leafTerminal')}
                    for p in cat['points']}
        for types in ([], ['未知题型'], ['阅读理解', '未知题型'], ['阅读理解', None]):
            scoped_fallback = review.compact_knowledge_catalog(types, catalog)
            self.assertEqual(strip(scoped_fallback), base)
            for point in scoped_fallback['points']:
                self.assertIn('leafTerminal', point)

    def test_knowledge_schema_allows_empty_whole_group_child_labels(self):
        exported = review_rounds.export_task(self.batch, skip_rounds=('labels',))
        schema = json.loads((Path(exported['package_dir']) / 'response.schema.json').read_text())
        check = schema['properties']['artifacts']['properties']['review_round']['properties']['checks']['items']
        assessment = check['properties']['assessment']
        assessment = next((branch for branch in assessment.get('oneOf', []) if branch.get('type') == 'object'), assessment)
        assessment = assessment['properties']
        child = assessment['subquestions']['items']['properties']['knowledge']
        self.assertEqual(child.get('minItems', 0), 0)
        self.assertEqual(assessment['knowledge']['minItems'], 1)
        task = json.loads((Path(exported['package_dir']) / 'task.json').read_text())
        self.assertIn('整篇题组列出目标子题且其 knowledge=[]', task['prompt'])

    def test_sample_selects_whole_target_unit_before_partition(self):
        units = [{'unit_id': 'group-1-6', 'unit_kind': 'group', 'numbers': [1, 2, 3, 4, 5, 6]},
                 {'unit_id': 'other', 'unit_kind': 'standalone', 'numbers': [7]}]
        self.write('01_source/structure.confirmed.json', {'units': units})
        self.write('04_knowledge/explanations.json', {'items': [
            {'unit_id': 'group-1-6', 'material': 'shared-whole-material',
             'subquestions': [{'number': str(n), 'explanation': 'child-' + str(n)} for n in range(1, 7)]},
            {'unit_id': 'other', 'explanation': 'x' * 12000}]})
        base = agent_tasks.build_task(self.batch, 'review', max_input_chars=1000000)
        before = review_rounds._partition_contexts(base, 1000000, round_id='sample')[0]['context']
        after = review_rounds._partition_contexts(base, 1000000, round_id='sample', target_unit_ids={'group-1-6'})[0]['context']
        self.assertGreater(len(before) - len(after), 12000)
        self.assertIn('shared-whole-material', after)
        self.assertIn('child-6', after)
        self.assertIn('She reads a book.', after)
        self.assertNotIn('x' * 100, after)
        print(f'Sample context chars: {len(before)} -> {len(after)}')

    def test_no_records_is_three_unchecked_and_never_fake_evidence(self):
        result = review.evaluate_rounds(self.batch)
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["unchecked"]), 2)
        self.assertEqual(result["findings"], [])
        self.assertFalse((self.batch / "07_review").exists())

    def test_concurrent_exports_share_partition_initialization(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            exports = list(pool.map(lambda _: review_rounds.export_task(self.batch), range(2)))
        bindings = [json.loads((self.batch / 'workflow/review-bindings' / (e['task_id'] + '.json')).read_text()) for e in exports]
        self.assertEqual(bindings[0]['state_path'], bindings[1]['state_path'])
        self.assertEqual(bindings[0]['state_digest'], bindings[1]['state_digest'])
        # Pre-bound parts have a fixed identity: concurrent exports of the same
        # pending part return that same task instead of inventing a new one.
        self.assertEqual(exports[0]['task_id'], exports[1]['task_id'])
        good = self.response('labels')
        first = review_rounds.import_result(self.batch, exports[0]['task_id'], good)
        self.assertEqual(review_rounds.import_result(self.batch, exports[1]['task_id'], good), first)

    def test_install_recovers_missing_queue_after_interruption(self):
        exported = review_rounds.export_task(self.batch)
        response = self.response('labels')
        response['artifacts']['review_round']['checks'][0].update(verdict='uncertain', rationale='原文与答案冲突。')
        write_json = ingest._write_json
        def fail_queue(path, payload):
            if path.name == 'manual-review-queue.json':
                raise OSError('injected queue failure')
            return write_json(path, payload)
        with patch.object(ingest, '_write_json', side_effect=fail_queue):
            with self.assertRaises(OSError):
                review_rounds.import_result(self.batch, exported['task_id'], response)
        self.assertTrue((self.batch / '07_review/review-rounds.json').exists())
        # A subsequent export must complete the pending transaction before advancing.
        self.assertEqual(review_rounds.export_task(self.batch)['round_id'], 'sample')
        items = json.loads((self.batch / '07_review/manual-review-queue.json').read_text())['items']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['status'], 'open')
        self.assertEqual(review_rounds.import_result(self.batch, exported['task_id'], response)['round_completed'], 'labels')
        changed = copy.deepcopy(response)
        changed['notes'] = ['different result']
        with self.assertRaises(ValueError):
            review_rounds.import_result(self.batch, exported['task_id'], changed)

    def test_install_replays_after_receipt_failure(self):
        exported = review_rounds.export_task(self.batch)
        response = self.response('labels')
        save = review_rounds._save
        def fail_receipt(batch, path, data):
            if path.endswith('.receipt.json'):
                raise OSError('injected receipt failure')
            return save(batch, path, data)
        with patch.object(review_rounds, '_save', side_effect=fail_receipt):
            with self.assertRaises(OSError):
                review_rounds.import_result(self.batch, exported['task_id'], response)
        self.assertEqual(review_rounds.import_result(self.batch, exported['task_id'], response)['round_completed'], 'labels')
        self.assertEqual(len(json.loads((self.batch / '07_review/review-rounds.json').read_text())['rounds']), 1)

    def test_partitioned_round_resumes_and_requires_exact_coverage(self):
        units = [{'unit_id': f'u{n}', 'unit_kind': 'standalone', 'numbers': [n],
                  'stem': f'{n}. Question text for unit {n}.'} for n in range(1, 17)]
        self.write('01_source/正文.txt', '\n'.join(unit['stem'] for unit in units))
        self.write('01_source_ans/answers.json', {'answers': {str(n): 'A' for n in range(1, 17)},
                   'difficulty': {str(n): '容易' for n in range(1, 17)}})
        self.write('01_source/structure.confirmed.json', {'units': units})
        self.write('03_types/types.json', {'items': [{'unit_id': u['unit_id'], 'type': '阅读理解',
                   'subtypes': ['阅读判断'], 'evidence': ['L1']} for u in units]})
        self.write('04_knowledge/knowledge.json', {'items': [{'unit_id': u['unit_id'],
                   'knowledge': ['语篇体裁/记叙文/故事', '主题/人与自我/生活与学习/日常起居'], 'subquestions': []} for u in units]})
        self.plan = review.prepare_round_plan(self.batch)
        first = review_rounds.export_task(self.batch, max_units_per_part=15)
        self.assertEqual(first['part_count'], 2)
        duplicate = review_rounds.export_task(self.batch, max_units_per_part=15)
        def response(exported):
            binding = json.loads((self.batch / 'workflow/review-bindings' / (exported['task_id'] + '.json')).read_text())
            state = json.loads((self.batch / binding['state_path']).read_text())
            targets = state['parts'][binding['part_index']]['targets']
            checks = [dict(unit_id=uid, verdict='ok', rationale='核对原文和题型。',
                      evidence=[{'path': '01_source/正文.txt', 'locator': f'第{uid[1:]}题',
                                 'quote': f'Question text for unit {uid[1:]}.'}],
                      assessment={'type': '阅读理解', 'subtypes': ['阅读判断'],
                                  'knowledge': ['语篇体裁/记叙文/故事', '主题/人与自我/生活与学习/日常起居'], 'subquestions': [],
                                  'difficulty': [{'number': uid[1:], 'value': '容易'}]}) for uid in targets]
            return {'status': 'completed', 'artifacts': {'review_round': {'checks': checks}}, 'notes': [], 'findings': []}
        bad = response(first)
        bad['artifacts']['review_round']['checks'].pop()
        with self.assertRaises(ValueError):
            review_rounds.import_result(self.batch, first['task_id'], bad)
        good = response(first)
        report = review_rounds.import_result(self.batch, first['task_id'], good)
        self.assertEqual(report['part_completed'], 1)
        self.assertNotIn('labels', review.evaluate_rounds(self.batch)['checked_rounds'])
        self.assertEqual(review_rounds.import_result(self.batch, first['task_id'], good), report)
        # The duplicate export is the same pending part, so an identical replay
        # is idempotent while a different response for the same task is refused.
        self.assertEqual(duplicate['task_id'], first['task_id'])
        self.assertEqual(review_rounds.import_result(self.batch, duplicate['task_id'], good), report)
        altered = response(duplicate)
        altered['artifacts']['review_round']['checks'][0]['rationale'] = '不同的结论。'
        with self.assertRaises(ValueError):
            review_rounds.import_result(self.batch, duplicate['task_id'], altered)
        second = review_rounds.export_task(self.batch)
        self.assertEqual(second['part_index'], 2)
        review_rounds.import_result(self.batch, second['task_id'], response(second))
        payload = json.loads((self.batch / '07_review/review-rounds.json').read_text())
        record = payload['rounds'][0]
        self.assertEqual([p['execution_id'] for p in record['parts']], [first['task_id'], second['task_id']])
        self.assertEqual(len(record['checks']), 16)
        self.assertEqual(report.get('part_completed'), 1)
        self.assertEqual(report.get('action'), 'continue')
        self.assertEqual(review_rounds.export_task(self.batch)['round_id'], 'sample')

    def test_review_partition_drops_duplicated_extraction_text(self):
        self.write("01_source/extraction.json", {
            "text": "DUPLICATED FULL SOURCE", "blocks": [{"t": "layout noise"}],
            "pages": [{"p": 1}], "tables": [{"rows": [["cell"]]}],
            "media": [{"path": "image1.jpeg"}], "warnings": ["w"]})
        base = agent_tasks.build_task(self.batch, "review", max_input_chars=10 ** 9)
        parts = review_rounds._partition_contexts(base, 10 ** 9, None, "labels", None)
        text = parts[0]["context"]
        self.assertNotIn("DUPLICATED FULL SOURCE", text)
        self.assertNotIn("layout noise", text)
        ctx = json.loads(text.split("\n", 1)[1])
        self.assertFalse({"text", "blocks", "pages"} & set(ctx["01_source/extraction.json"]))
        self.assertIn("image1.jpeg", text)
        self.assertIn("cell", text)

    def _parallel_batch(self):
        questions = {n: f'{n}. Question {n}?\nA. a{n} B. b{n} C. c{n} D. d{n}' for n in range(1, 5)}
        source = '\n'.join(questions[n] for n in range(1, 5))
        units = [{'unit_id': f'u{n}', 'unit_kind': 'standalone', 'numbers': [n],
                  'stem': questions[n]} for n in range(1, 5)]
        self.write('01_source/正文.txt', source)
        self.write('01_source/structure.confirmed.json', {'units': units})
        self.write('01_source_ans/answers.json', {'answers': {str(n): 'A' for n in range(1, 5)},
                   'difficulty': {str(n): '容易' for n in range(1, 5)}})
        self.write('03_types/types.json', {'items': [
            {'unit_id': f'u{n}', 'type': '阅读理解', 'subtypes': ['阅读判断'], 'evidence': ['L1']}
            for n in range(1, 5)]})
        self.write('04_knowledge/knowledge.json', {'items': [
            {'unit_id': f'u{n}', 'knowledge': ['语篇体裁/记叙文/故事',
             '主题/人与自我/生活与学习/日常起居'], 'subquestions': []} for n in range(1, 5)]})
        self.write('06_batch/batch.json', {'items': []})
        self.plan = review.prepare_round_plan(self.batch)

    def _part_response(self, prompt, round_id):
        import re
        targets = json.loads(re.search(r"TARGETS: (\[.*?\])\n", prompt).group(1))
        checks = []
        for target in targets:
            uid = target['unit_id'] if isinstance(target, dict) else target
            numbers = ([str(target['number'])] if round_id == 'sample'
                       else [str(q['number']) for q in self.plan['questions'] if q['unit_id'] == uid])
            assessment = {'type': '阅读理解', 'subtypes': ['阅读判断'],
                          'knowledge': ['语篇体裁/记叙文/故事', '主题/人与自我/生活与学习/日常起居'],
                          'subquestions': [],
                          'difficulty': [{'number': number, 'value': '容易'} for number in numbers]}
            if round_id == 'sample':
                assessment['source_comparison'] = '与原文和答案一致。'
            base = dict(target) if isinstance(target, dict) else {'unit_id': target}
            checks.append(dict(base, verdict='ok', rationale='原文说明题目内容。',
                               evidence=[{'path': '01_source/正文.txt', 'locator': 'L1',
                                          'quote': f"Question {numbers[0]}?"}],
                               assessment=assessment))
        return {'status': 'completed', 'artifacts': {'review_round': {'checks': checks}},
                'notes': [], 'findings': []}

    def test_four_parts_complete_after_earlier_evidence_path_correction(self):
        self._parallel_batch()
        self.write('01_source/附录.txt', '1~4 AAAA\n')
        self.plan = review.prepare_round_plan(self.batch)
        sample = review_rounds.export_task(self.batch, only_round='sample')
        sample_task = review_rounds._check_bound_inputs(self.batch, sample['task_id'])['task']
        review_rounds.import_result(self.batch, sample['task_id'],
                                   self._part_response(sample_task['prompt'], 'sample'))
        group = review_rounds.export_group(self.batch, max_units_per_part=1,
                                           only_round='labels')
        self.assertEqual(len(group['parts']), 4)
        retained_paths = []
        for index, part in enumerate(group['parts']):
            binding = review_rounds._check_bound_inputs(self.batch, part['task_id'])
            response = self._part_response(binding['task']['prompt'], 'labels')
            if index == 1:
                response['artifacts']['review_round']['checks'][0]['evidence'].append({
                    'path': '01_source/正文.txt', 'locator': '附录答案', 'quote': '1~4 AAAA'})
            original = copy.deepcopy(response)
            result = review_rounds.import_result(self.batch, part['task_id'], response)
            self.assertEqual(response, original, 'Caller response must remain unchanged')
            retained_paths.extend((self.batch / 'workflow/review-results' / part['task_id']).glob('*.json'))
        self.assertEqual(result['round_completed'], 'labels')
        self.assertTrue(result['completed'])
        self.assertEqual(result['review']['checked_rounds'], list(review.ROUND_IDS))
        retained = {p: p.read_bytes() for p in retained_paths}
        evaluated = review.evaluate_rounds(self.batch)
        self.assertIn('labels', evaluated['checked_rounds'])
        # Historical response was hashed before the validator corrected attribution.
        state = json.loads((self.batch / binding['state_path']).read_text())
        self.assertEqual(state['completed'][1]['checks'][0]['evidence'][-1]['path'],
                         '01_source/附录.txt')
        self.assertEqual({p: p.read_bytes() for p in retained_paths}, retained)
        texts = review._bound_source_texts(self.batch, self.plan)
        evidence_checks = copy.deepcopy(state['completed'][1]['checks'])
        evidence_checks[0]['evidence'][-1]['path'] = 'unbound.txt'
        with self.assertRaisesRegex(ValueError, 'bound batch input'):
            review._canonical_check_sources(evidence_checks, self.plan, texts)
        evidence_checks[0]['evidence'][-1]['path'] = '01_source/正文.txt'
        evidence_checks[0]['evidence'][-1]['quote'] = 'A fabricated quote absent everywhere'
        with self.assertRaisesRegex(ValueError, 'quote absent'):
            review._canonical_check_sources(evidence_checks, self.plan, texts)
        # Source attribution is the only tolerated difference. A changed judgment
        # remains a provenance failure, even when its evidence resolves correctly.
        state['completed'][1]['checks'][0]['rationale'] = 'Unproven replacement judgment'
        self.write(binding['state_path'], state)
        rejected = review.evaluate_rounds(self.batch)
        self.assertNotIn('labels', rejected['checked_rounds'])
        self.assertTrue(any(e.get('reason') == 'Review execution checks mismatch'
                            for e in rejected['errors']))

    def test_round_prompt_has_complete_envelope_and_scoped_role(self):
        import re
        for rid in review.ROUND_IDS:
            prompt = review_rounds._round_prompt(rid, [], '{}')
            example = re.search(r'返回根结构示例[^\n]*：(.*)\n', prompt)
            self.assertIsNotNone(example)
            envelope = json.loads(example.group(1))
            self.assertEqual(set(envelope), {'status', 'artifacts', 'notes', 'findings'})
            self.assertEqual(envelope['artifacts'], {'review_round': {'checks': []}})
            self.assertNotIn('只写 `findings`、`checked_units`、`unchecked`', prompt)
            self.assertNotIn('07_review/agent-review.json', prompt)
            self.assertIn('不得输出 `checked_units` 或 `unchecked`', prompt)

    def test_three_rounds_run_concurrently(self):
        self._parallel_batch()
        import threading
        barrier = threading.Barrier(2, timeout=5)
        seen = []

        def invoke(prompt, run_dir, config, permit_parallel=False):
            import re
            round_id = re.search(r"ROUND: (\w+)", prompt).group(1)
            seen.append(round_id)
            barrier.wait()  # both rounds must be in flight together
            return {'response': self._part_response(prompt, round_id)}

        with patch.object(review_rounds, 'invoke', side_effect=invoke):
            result = review_rounds.run(self.batch, config={'provider': 'fixture',
                                                           'review_part_parallel': 2})
        self.assertTrue(result['completed'], result)
        self.assertEqual(sorted(seen), sorted(review.ROUND_IDS))
        self.assertEqual(review.evaluate_rounds(self.batch)['checked_rounds'], list(review.ROUND_IDS))

    def test_near_verbatim_quote_is_localized_without_retry(self):
        self._parallel_batch()
        first = review_rounds.export_task(self.batch, max_units_per_part=1)
        task = json.loads((Path(first['package_dir']) / 'task.json').read_text())
        response = self._part_response(task['prompt'], 'labels')
        evidence = response['artifacts']['review_round']['checks'][0]['evidence'][0]
        line = (self.batch / '01_source/正文.txt').read_text(encoding='utf-8-sig').splitlines()[0]
        evidence['quote'] = line + '.'
        report = review_rounds.import_result(self.batch, first['task_id'], response)
        self.assertEqual(report.get('part_completed'), 1)
        stored = json.loads((self.batch / 'workflow/review-results'
                             / first['task_id']).glob('*.json').__next__().read_text())
        quote = stored['artifacts']['review_round']['checks'][0]['evidence'][0]
        self.assertTrue(quote.get('normalized'))
        self.assertIn('model_quote', quote)
        self.assertFalse((self.batch / 'workflow/review-partial' / (first['task_id'] + '.json')).exists())

    def test_bad_target_is_repaired_without_discarding_partition(self):
        self._parallel_batch()
        first = review_rounds.export_task(self.batch, max_units_per_part=1)
        self.assertEqual(first['round_id'], 'labels')
        task = json.loads((Path(first['package_dir']) / 'task.json').read_text())
        good = self._part_response(task['prompt'], 'labels')
        bad = copy.deepcopy(good)
        bad['artifacts']['review_round']['checks'][0]['evidence'][0]['quote'] = '这句话原文里并不存在'
        with self.assertRaises(review.RoundCheckError):
            review_rounds.import_result(self.batch, first['task_id'], bad)
        partial = json.loads((self.batch / 'workflow/review-partial' / (first['task_id'] + '.json')).read_text())
        self.assertEqual(len(partial['failed']), 1)
        self.assertEqual(len(partial['valid_checks']), max(0, len(good['artifacts']['review_round']['checks']) - 1))
        def repair(prompt, run_dir, config, permit_parallel=False):
            import re
            round_id = re.search(r"ROUND: (\w+)", prompt).group(1)
            return {'response': self._part_response(prompt, round_id)}
        with patch.object(review_rounds, 'invoke', side_effect=repair):
            report = review_rounds._repair_round(self.batch, first['task_id'], {'provider': 'fixture'})
        self.assertEqual(report.get('part_completed'), 1)
        self.assertEqual(report.get('action'), 'continue')

    def test_hidden_labels_trigger_real_local_repair_and_keep_other_checks(self):
        self._parallel_batch()
        first = review_rounds.export_task(self.batch, max_units_per_part=2)
        task = json.loads((Path(first['package_dir']) / 'task.json').read_text())
        response = self._part_response(task['prompt'], 'labels')
        checks = response['artifacts']['review_round']['checks']
        checks[0].update(verdict='error', rationale='最终条目未见 type、subtypes、knowledge 字段。')
        failed_uid = checks[0]['unit_id']
        unaffected = copy.deepcopy(checks[1:])
        with self.assertRaisesRegex(review.RoundCheckError, 'BLIND_LABEL_ABSENCE'):
            review_rounds.import_result(self.batch, first['task_id'], response)
        partial = json.loads((self.batch / 'workflow/review-partial' / (first['task_id'] + '.json')).read_text())
        self.assertEqual(partial['valid_checks'], unaffected)
        invoked = []
        def repair(prompt, run_dir, config, permit_parallel=False):
            fixed = self._part_response(prompt, 'labels')
            invoked.extend(c['unit_id'] for c in fixed['artifacts']['review_round']['checks'])
            self.assertIn('BLIND_LABEL_ABSENCE', prompt)
            return {'response': fixed}
        with patch.object(review_rounds, 'invoke', side_effect=repair):
            report = review_rounds._repair_round(self.batch, first['task_id'], {'provider': 'fixture'})
        self.assertEqual(invoked, [failed_uid])
        self.assertEqual(report.get('part_completed'), 1)

    def test_self_negation_repairs_only_failed_target_and_retains_original(self):
        self._parallel_batch()
        first = review_rounds.export_task(self.batch, max_units_per_part=2)
        task = json.loads((Path(first['package_dir']) / 'task.json').read_text())
        response = self._part_response(task['prompt'], 'labels')
        checks = response['artifacts']['review_round']['checks']
        checks[0]['rationale'] = '按阅读理解/阅读判断记录不合理'
        failed_uid = checks[0]['unit_id']
        unaffected = copy.deepcopy(checks[1:])
        with self.assertRaisesRegex(review.RoundCheckError, 'REVIEW_ASSESSMENT_CONTRADICTION'):
            review_rounds.import_result(self.batch, first['task_id'], response)
        partial = json.loads((self.batch / 'workflow/review-partial' / (first['task_id'] + '.json')).read_text())
        self.assertEqual(partial['valid_checks'], unaffected)
        retained = list((self.batch / 'workflow/review-results' / first['task_id']).glob('*.json'))
        self.assertTrue(any('记录不合理' in path.read_text() for path in retained))
        invoked = []
        def repair(prompt, run_dir, config, permit_parallel=False):
            fixed = self._part_response(prompt, 'labels')
            invoked.extend(c['unit_id'] for c in fixed['artifacts']['review_round']['checks'])
            self.assertIn('REVIEW_ASSESSMENT_CONTRADICTION', prompt)
            return {'response': fixed}
        with patch.object(review_rounds, 'invoke', side_effect=repair):
            report = review_rounds._repair_round(self.batch, first['task_id'], {'provider': 'fixture'})
        self.assertEqual(invoked, [failed_uid])
        self.assertEqual(report.get('part_completed'), 1)

    def test_parallel_parts_overlap_and_complete_in_order(self):
        self._parallel_batch()
        import threading, time
        lock = threading.Lock()
        state = {'active': 0, 'peak': 0, 'rounds': []}
        def invoke(prompt, run_dir, config, permit_parallel=False):
            import re
            round_id = re.search(r"ROUND: (\w+)", prompt).group(1)
            with lock:
                state['active'] += 1
                state['peak'] = max(state['peak'], state['active'])
                state['rounds'].append(round_id)
            time.sleep(0.05)
            with lock:
                state['active'] -= 1
            return {'response': self._part_response(prompt, round_id)}
        with patch.object(review_rounds, 'invoke', side_effect=invoke):
            result = review_rounds.run(self.batch, config={'provider': 'fixture', 'review_part_parallel': 4,
                                                           'review_max_units_per_part': 1})
        self.assertTrue(result['completed'], result)
        self.assertGreaterEqual(state['peak'], 2)
        self.assertEqual(review.evaluate_rounds(self.batch)['checked_rounds'], list(review.ROUND_IDS))

    def test_parallel_part_retries_transient_invocation_failure(self):
        # 并行路径中，单次瞬时模型失败应在同一轮内带反馈重试，而不是整轮作废。
        self._parallel_batch()
        import re
        calls = []

        def flaky(prompt, run_dir, config, permit_parallel=False):
            round_id = re.search(r"ROUND: (\w+)", prompt).group(1)
            targets = json.loads(re.search(r"TARGETS: (\[.*?\])\n", prompt).group(1))
            key = (round_id, json.dumps(targets, ensure_ascii=False, sort_keys=True))
            calls.append(key)
            # labels 的 u1 片第一次失败，第二次成功；其余片正常。
            if key == ('labels', '["u1"]') and calls.count(key) == 1:
                raise RuntimeError('临时模型调用失败')
            return {'response': self._part_response(prompt, round_id)}

        with patch.object(review_rounds, 'invoke', side_effect=flaky):
            result = review_rounds.run(self.batch, config={
                'provider': 'fixture', 'review_part_parallel': 4,
                'review_max_units_per_part': 1, 'review_max_attempts': 2})
        self.assertTrue(result['completed'], result)
        self.assertEqual(review.evaluate_rounds(self.batch)['checked_rounds'], list(review.ROUND_IDS))
        self.assertGreaterEqual(calls.count(('labels', '["u1"]')), 2)

    def test_response_json_error_feedback_guides_retry(self):
        # A JSON structural failure (error_code=response) must give the retry an
        # explicit bracket/structure hint, not just echo the parser error.
        self._parallel_batch()
        prompts = []
        def flaky(prompt, run_dir, config, permit_parallel=False):
            import re
            prompts.append(prompt)
            round_id = re.search(r"ROUND: (\w+)", prompt).group(1)
            targets = json.loads(re.search(r"TARGETS: (\[.*?\])\n", prompt).group(1))
            # Fail each partition's first attempt with a JSON protocol error.
            if "前次分片结果未通过协议" not in prompt:
                raise agent.AgentError("Invalid final JSON response: Expecting ',' delimiter", "response")
            return {'response': self._part_response(prompt, round_id)}
        with patch.object(review_rounds, 'invoke', side_effect=flaky):
            result = review_rounds.run(self.batch, config={
                'provider': 'fixture', 'review_part_parallel': 4,
                'review_max_units_per_part': 1, 'review_max_attempts': 2})
        self.assertTrue(result['completed'], result)
        retried = [p for p in prompts if "前次分片结果未通过协议" in p]
        self.assertTrue(retried)
        self.assertTrue(any("合法 JSON" in p and "artifacts" in p for p in retried))

    def test_parallel_failure_keeps_later_pending_for_next_run(self):
        self._parallel_batch()
        def failing(prompt, run_dir, config, permit_parallel=False):
            import re
            round_id = re.search(r"ROUND: (\w+)", prompt).group(1)
            targets = json.loads(re.search(r"TARGETS: (\[.*?\])\n", prompt).group(1))
            if round_id == 'labels' and 'u1' in targets:
                raise RuntimeError('模型调用失败')
            return {'response': self._part_response(prompt, round_id)}
        with patch.object(review_rounds, 'invoke', side_effect=failing):
            first = review_rounds.run(self.batch, config={'provider': 'fixture', 'review_part_parallel': 4,
                                                          'review_max_units_per_part': 1})
        self.assertNotIn('labels', review.evaluate_rounds(self.batch)['checked_rounds'])
        pending = list((self.batch / 'workflow/review-pending').glob('*.json'))
        self.assertGreaterEqual(len(pending), 1)
        # The next invocation must reuse retained responses and only re-run the
        # partition that never produced one.
        called = []
        def second(prompt, run_dir, config, permit_parallel=False):
            import re
            round_id = re.search(r"ROUND: (\w+)", prompt).group(1)
            targets = json.loads(re.search(r"TARGETS: (\[.*?\])\n", prompt).group(1))
            called.append((round_id, targets))
            return {'response': self._part_response(prompt, round_id)}
        with patch.object(review_rounds, 'invoke', side_effect=second):
            review_rounds.run(self.batch, config={'provider': 'fixture', 'review_part_parallel': 4,
                                                          'review_max_units_per_part': 1})
        self.assertEqual([c for c in called if c[0] == 'labels'], [('labels', ['u1'])])

    def test_catalog_scoped_by_paper_sections_per_partition(self):
        questions = {1: '1. Choose one?\nA. a1 B. b1 C. c1 D. d1',
                     2: '2. Choose two?\nA. a2 B. b2 C. c2 D. d2',
                     3: '3. Read three?\nA. a3 B. b3 C. c3 D. d3',
                     4: '4. Read four?\nA. a4 B. b4 C. c4 D. d4'}
        source = ('\u2160.\u5355\u9879\u586b\u7a7a(\u51712\u5c0f\u9898)\n' + questions[1] + '\n' + questions[2]
                  + '\n\u2161.\u9605\u8bfb\u7406\u89e3(\u51712\u5c0f\u9898)\n' + questions[3] + '\n' + questions[4])
        units = [{'unit_id': f'u{n}', 'unit_kind': 'standalone', 'numbers': [n],
                  'stem': questions[n]} for n in (1, 2, 3, 4)]
        self.write('01_source/\u6b63\u6587.txt', source)
        self.write('01_source/structure.confirmed.json', {'units': units})
        self.write('01_source_ans/answers.json', {'answers': {str(n): 'A' for n in range(1, 5)},
                   'difficulty': {str(n): '\u5bb9\u6613' for n in range(1, 5)}})
        self.write('03_types/types.json', {'items': [
            {'unit_id': f'u{n}', 'type': '\u5355\u9879\u586b\u7a7a' if n < 3 else '\u9605\u8bfb\u7406\u89e3',
             'subtypes': ['\u56db\u4e2a\u9009\u9879' if n < 3 else '\u9605\u8bfb\u5224\u65ad'],
             'evidence': ['L1']} for n in range(1, 5)]})
        self.write('04_knowledge/knowledge.json', {'items': [
            {'unit_id': f'u{n}',
             'knowledge': (['\u8bcd\u6c47/\u77ed\u8bed\u4e0e\u56fa\u5b9a\u642d\u914d'] if n < 3 else
                           ['\u8bed\u7bc7\u4f53\u88c1/\u8bb0\u53d9\u6587/\u6545\u4e8b',
                            '\u4e3b\u9898/\u4eba\u4e0e\u81ea\u6211/\u751f\u6d3b\u4e0e\u5b66\u4e60/\u65e5\u5e38\u8d77\u5c45']),
             'subquestions': []} for n in range(1, 5)]})
        self.write('06_batch/batch.json', {'items': []})
        export = review_rounds.export_task(self.batch, max_units_per_part=1)
        self.assertEqual(export['part_count'], 4)
        binding = json.loads((self.batch / 'workflow/review-bindings'
                              / (export['task_id'] + '.json')).read_text())
        state = json.loads((self.batch / binding['state_path']).read_text())
        catalogs = []
        for part in state['parts']:
            body = part['prompt'].split('BEGIN SUPPLIED CONTENT', 1)[1] \
                .rsplit('END SUPPLIED CONTENT', 1)[0].split('\n', 1)[1]
            catalogs.append(json.loads(body)['references/knowledge-catalog.json'])
        single = [point['path'] for point in catalogs[0]['points']]
        reading = [point['path'] for point in catalogs[2]['points']]
        self.assertTrue(single and all(path.startswith(('\u8bcd\u6c47', '\u8bed\u6cd5', '\u8bed\u97f3', '\u8bed\u7528'))
                                       for path in single))
        self.assertFalse(any(path.startswith('\u8bbe\u9898\u89d2\u5ea6/\u9605\u8bfb') for path in single))
        self.assertTrue(reading and all(path.startswith(('\u4e3b\u9898', '\u8bed\u7bc7\u4f53\u88c1',
                                                         '\u8bbe\u9898\u89d2\u5ea6/\u9605\u8bfb'))
                                        for path in reading))
        self.assertFalse(any(path.startswith('\u8bed\u6cd5') for path in reading))

    def test_later_round_completion_keeps_finished_partitions_resumable(self):
        units = [{'unit_id': f'u{n}', 'unit_kind': 'standalone', 'numbers': [n],
                  'stem': f'{n}. Question text for unit {n}.'} for n in range(1, 17)]
        self.write('01_source/正文.txt', '\n'.join(unit['stem'] for unit in units))
        self.write('01_source_ans/answers.json', {'answers': {str(n): 'A' for n in range(1, 17)},
                   'difficulty': {str(n): '容易' for n in range(1, 17)}})
        self.write('01_source/structure.confirmed.json', {'units': units})
        self.write('03_types/types.json', {'items': [{'unit_id': u['unit_id'], 'type': '阅读理解',
                   'subtypes': ['阅读判断'], 'evidence': ['L1']} for u in units]})
        self.write('04_knowledge/knowledge.json', {'items': [{'unit_id': u['unit_id'],
                   'knowledge': ['语篇体裁/记叙文/故事', '主题/人与自我/生活与学习/日常起居'],
                   'subquestions': []} for u in units]})
        self.plan = review.prepare_round_plan(self.batch)

        def answer(exported):
            binding = json.loads((self.batch / 'workflow/review-bindings'
                                  / (exported['task_id'] + '.json')).read_text())
            state = json.loads((self.batch / binding['state_path']).read_text())
            targets = state['parts'][binding['part_index']]['targets']
            checks = []
            for target in targets:
                uid = target['unit_id'] if isinstance(target, dict) else target
                number = str(target['number']) if isinstance(target, dict) else uid[1:]
                assessment = {'type': '阅读理解', 'subtypes': ['阅读判断'],
                              'knowledge': ['语篇体裁/记叙文/故事', '主题/人与自我/生活与学习/日常起居'],
                              'subquestions': [],
                              'difficulty': [{'number': number, 'value': '容易'}]}
                if isinstance(target, dict):
                    assessment['source_comparison'] = '题干与原文、答案一致'
                checks.append(dict(unit_id=uid, verdict='ok', rationale='核对原文和题型。',
                      evidence=[{'path': '01_source/正文.txt', 'locator': f'第{number}题',
                                 'quote': (f'{number}. Question text for unit {number}.'
                                           if isinstance(target, dict) else f'Question text for unit {number}.')}],
                      assessment=assessment))
            return {'status': 'completed', 'artifacts': {'review_round': {'checks': checks}},
                    'notes': [], 'findings': []}

        first = review_rounds.export_task(self.batch, max_units_per_part=15)
        self.assertEqual((first['round_id'], first['part_index'], first['part_count']),
                         ('labels', 1, 2))
        review_rounds.import_result(self.batch, first['task_id'], answer(first))
        knowledge = review_rounds.export_task(self.batch, skip_rounds=('labels',))
        self.assertEqual(knowledge['round_id'], 'sample')
        self.assertEqual(knowledge['part_count'], 1)
        review_rounds.import_result(self.batch, knowledge['task_id'], answer(knowledge))
        # Another round finishing must not discard the finished labels part.
        resumed = review_rounds.export_task(self.batch, max_units_per_part=15)
        self.assertEqual((resumed['round_id'], resumed['part_index'], resumed['part_count']),
                         ('labels', 2, 2))

    def test_prior_round_digests_allow_later_rounds_but_detect_changes(self):
        labels = {'round_id': 'labels', 'checks': []}
        knowledge = {'round_id': 'knowledge', 'checks': []}
        prior = {'labels': review._digest(labels)}
        self.assertTrue(review_rounds._prior_unchanged([labels], prior))
        self.assertTrue(review_rounds._prior_unchanged([labels, knowledge], prior))
        self.assertFalse(review_rounds._prior_unchanged([knowledge], prior))
        self.assertFalse(review_rounds._prior_unchanged(
            [{'round_id': 'labels', 'checks': [1]}], prior))

    @patch.object(review, '_verify_round_sources', new=lambda *args: None)
    def test_sample_partition_provenance_requires_exact_nonoverlapping_targets(self):
        payload = self.payload()
        record = payload['rounds'][1]
        # 组题整组抽取后只有一个目标：单个分片完整覆盖它是有效的。
        record['parts'] = [{'execution_id': 'sample-a', 'targets': [self.plan['sample'][0]]}]
        self.assertTrue(review.evaluate_rounds(self.batch, payload)['passed'])
        # 同一个目标再出现在第二个分片里即重叠，必须被拒绝。
        record['parts'] = [{'execution_id': 'sample-a', 'targets': [self.plan['sample'][0]]},
                           {'execution_id': 'sample-b', 'targets': [self.plan['sample'][0]]}]
        self.assertNotIn('sample', review.evaluate_rounds(self.batch, payload)['checked_rounds'])

    def test_group_type_sampled_as_one_whole_group_target(self):
        # 阅读理解是整组题型：二审整组抽取、只做组级核对，number=None，不拆小题。
        self.assertEqual(len(self.plan["units"]), 1)
        self.assertEqual(len(self.plan["sample"]), 1)
        self.assertEqual(self.plan, review.prepare_round_plan(self.batch))
        self.assertIsNone(self.plan["sample"][0]["number"])
        self.assertEqual(self.plan["sample"][0]["unit_id"], "group-1-6")

    def test_standalone_questions_sample_ceil_not_unit_count_and_reproducible(self):
        # 独立单题仍按题号抽样，抽样数 = ceil(题数/5)，固定在输入上可复现。
        stem = '\n'.join(f'{n}. What happens in question {n}?' for n in range(1, 11))
        units = [{"unit_id": f"q-{n}", "unit_kind": "standalone", "numbers": [n], "stem": f"{n}. What happens in question {n}?"}
                 for n in range(1, 11)]
        self.write("01_source/正文.txt", stem)
        self.write("01_source/structure.confirmed.json", {"units": units})
        self.write("03_types/types.json", {"items": [{"unit_id": f"q-{n}", "type": "单项填空",
                   "subtypes": ["语法"], "evidence": ["L1"]} for n in range(1, 11)]})
        self.write("04_knowledge/knowledge.json", {"items": [
            {"unit_id": f"q-{n}", "knowledge": ["语用/情景交际"], "subquestions": []} for n in range(1, 11)]})
        self.write("01_source_ans/answers.json", {"answers": {str(n): "A" for n in range(1, 11)},
                   "difficulty": {str(n): "容易" for n in range(1, 11)}})
        self.write("04_knowledge/explanations.json", {"items": [
            {"unit_id": f"q-{n}", "explanation": "", "explanation_origin": "not_applicable",
             "articleSummary": "", "summary_origin": "not_applicable", "evidence": ["L1"]} for n in range(1, 11)]})
        plan = review.prepare_round_plan(self.batch)
        self.assertEqual(len(plan["sample"]), (10 + 4) // 5)
        self.assertEqual(plan, review.prepare_round_plan(self.batch))
        self.assertTrue(all(q["number"] is not None for q in plan["sample"]))

    @patch.object(review, '_verify_round_sources', new=lambda *args: None)
    def test_distinct_round_specific_records_pass_without_names_or_signatures(self):
        result = review.evaluate_rounds(self.batch, self.payload())
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["checked_rounds"], list(review.ROUND_IDS))

    @patch.object(review, '_verify_round_sources', new=lambda *args: None)
    def test_same_structural_checks_cannot_be_three_semantic_reviews(self):
        # 仅有结构性声称不能替代每轮所需的独立比较结果。
        payload = self.payload()
        for record in payload["rounds"]:
            for check in record["checks"]:
                check["assessment"] = {"nonempty": True, "valid_taxonomy": True}
        self.assertEqual(len(review.evaluate_rounds(self.batch, payload)["checked_rounds"]), 0)

    @patch.object(review, '_verify_round_sources', new=lambda *args: None)
    def test_catalog_labels_do_not_prove_knowledge_semantics(self):
        payload = self.payload()
        check = payload["rounds"][1]["checks"][0]
        check["assessment"] = {"valid_taxonomy": True}
        self.assertFalse(review.evaluate_rounds(self.batch, payload)["passed"])
        # 盲判允许与原标注不同：差异由程序记为意见，不再判为无效轮次。
        payload = self.payload()
        payload["rounds"][1]["checks"][0]["assessment"]["knowledge"] = ["语篇体裁/说明文/介绍类短文", "主题/人与自我/生活与学习/日常起居"]
        result = review.evaluate_rounds(self.batch, payload)
        self.assertIn("sample", result["checked_rounds"])

    @patch.object(review, '_verify_round_sources', new=lambda *args: None)
    def test_visual_gap_uncertain_is_informational_not_manual(self):
        # 图片占位/看不到图这类“能力受限、无法确认图文对应”不是标签差异：
        # 只作提示，不进人工队列、不阻断闸门。
        payload = self.payload()
        sample = payload["rounds"][1]["checks"][0]
        sample["verdict"] = "uncertain"
        sample["rationale"] = "选项为图片占位，无法确认图文对应。"
        result = review.evaluate_rounds(self.batch, payload)
        codes = [e["code"] for e in result["errors"] if e.get("unit_id") == sample["unit_id"]]
        self.assertNotIn("REVIEW_ROUND_FINDING", codes)
        self.assertTrue(any(str(i.get("unit_id")) == sample["unit_id"] for i in result.get("informational") or []))

    @patch.object(review, '_verify_round_sources', new=lambda *args: None)
    def test_uncertain_label_conflict_is_a_manual_finding(self):
        # 真正点名“与原标签不一致/答案冲突”的不确定仍要送人工。
        payload = self.payload()
        sample = payload["rounds"][1]["checks"][0]
        sample["verdict"] = "uncertain"
        sample["rationale"] = "知识点与原标签不一致：疑似应为说明文主题。"
        result = review.evaluate_rounds(self.batch, payload)
        self.assertIn("REVIEW_ROUND_FINDING",
                      [e["code"] for e in result["errors"] if e.get("unit_id") == sample["unit_id"]])

    @patch.object(review, '_verify_round_sources', new=lambda *args: None)
    def test_stale_inputs_and_added_answer_evidence_invalidate(self):
        payload = self.payload()
        self.write("01_source_ans/正文.txt", "1. A")
        self.assertEqual(len(review.evaluate_rounds(self.batch, payload)["unchecked"]), 2)

    @patch.object(review, '_verify_round_sources', new=lambda *args: None)
    def test_fake_quote_missing_source_and_shared_execution_fail(self):
        # 伪造或缺少原文证据不能证明该轮完成，各轮还必须独立执行。
        payload = self.payload()
        payload["rounds"][0]["checks"][0]["evidence"][0]["quote"] = "fabricated quote"
        self.assertFalse(review.evaluate_rounds(self.batch, payload)["passed"])
        payload = self.payload()
        payload["rounds"][0]["checks"][0]["evidence"] = []
        self.assertFalse(review.evaluate_rounds(self.batch, payload)["passed"])
        payload = self.payload()
        payload["rounds"][1]["execution_id"] = payload["rounds"][0]["execution_id"]
        self.assertFalse(review.evaluate_rounds(self.batch, payload)["passed"])

    @patch.object(review, '_verify_round_sources', new=lambda *args: None)
    def test_new_binding_retires_superseded_open_items_but_keeps_decisions(self):
        payload = self.payload()
        binding = payload['plan']['input_binding']
        def item(fid, status, decided_by=None):
            row = {'finding_id': fid, 'unit_id': 'g', 'field': 'knowledge', 'severity': 'major',
                   'status': status, 'question': '旧问题', 'reason': '旧问题', 'evidence': [],
                   'round_sources': [{'round_id': 'labels', 'execution_id': 'old', 'protocol': review.ROUND_PROTOCOL,
                                      'input_binding': 'old-binding', 'field': 'knowledge', 'value': []}]}
            if decided_by:
                row['decided_by'] = decided_by
                row['decided_at'] = '2026-01-01T00:00:00+08:00'
            return row
        self.write('07_review/manual-review-queue.json',
                   {'queue_version': '1.0', 'items': [item('WF-9001', 'open'), item('WF-9002', 'fixed', 'reviewer')]})
        ingest.build_review_files(self.batch, {'review_rounds': payload})
        items = json.loads((self.batch / '07_review/manual-review-queue.json').read_text())['items']
        ids = {row['finding_id'] for row in items}
        self.assertNotIn('WF-9001', ids)          # 旧绑定的未处理发现已过期
        self.assertIn('WF-9002', ids)             # 已有人工结论保留
        self.assertEqual(len([r for r in items if r.get('round_sources') and
                              r['round_sources'][0].get('input_binding') == binding and r['status'] == 'open']), 0)
        # 过期不等于没发生：旧观察必须先归档再出队，否则跨轮差异会无声蒸发。
        archived = json.loads((self.batch / '07_review/retired-round-findings.json').read_text())['records']
        self.assertIn('WF-9001', {r['finding_id'] for r in archived})
        self.assertTrue(all(r.get('retired_reason') for r in archived))

    @patch.object(review, '_verify_round_sources', new=lambda *args: None)
    def test_rounds_history_is_append_only_and_deduplicates(self):
        self.write('07_review/review-rounds.json', self.payload())
        path = self.batch / '07_review/rounds-history.jsonl'
        first = review_rounds.record_rounds_history(self.batch)
        self.assertIsNotNone(first)
        self.assertEqual(len(path.read_text(encoding='utf-8').splitlines()), 1)
        # 同一轮重复结算（导入重放、闸门刷新）不得重复记账。
        self.assertIsNone(review_rounds.record_rounds_history(self.batch))
        self.assertEqual(len(path.read_text(encoding='utf-8').splitlines()), 1)
        # 没有完成的轮次（记录过期）不写空快照冒充历史。
        self.assertIsNone(review_rounds.record_rounds_history(
            self.batch, {'plan': self.plan, 'checked_rounds': [], 'findings': [], 'passed': False}))
        self.assertEqual(len(path.read_text(encoding='utf-8').splitlines()), 1)
        # 新一轮判据不同：追加一行，旧快照原样保留。
        changed = {'plan': self.plan, 'checked_rounds': list(review.ROUND_IDS), 'passed': False,
                   'findings': [{'unit_id': 'group-1-6', 'field': 'knowledge', 'severity': 'major',
                                 'reason': '知识点应为动词辨析', 'round_sources': [{'round_id': 'labels'}]}]}
        self.assertIsNotNone(review_rounds.record_rounds_history(self.batch, changed))
        lines = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[-1]['findings'][0]['unit_id'], 'group-1-6')

    @patch.object(review, '_verify_round_sources', new=lambda *args: None)
    def test_partial_round_snapshot_only_counts_when_it_carries_differences(self):
        self.write('07_review/review-rounds.json', self.payload())
        path = self.batch / '07_review/rounds-history.jsonl'
        # 只跑完 labels、且没有差异：审核途中态，不得记成「这一轮没差异」。
        partial = {'plan': self.plan, 'checked_rounds': ['labels'], 'passed': False, 'findings': []}
        self.assertIsNone(review_rounds.record_rounds_history(self.batch, partial))
        self.assertFalse(path.exists())
        # 同样的部分轮次但确有差异：必须留证，哪怕是中途快照。
        partial['findings'] = [{'unit_id': 'q-1', 'field': 'knowledge', 'severity': 'major',
                                'reason': '标签与原标注不一致', 'round_sources': [{'round_id': 'labels'}]}]
        self.assertIsNotNone(review_rounds.record_rounds_history(self.batch, partial))
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
        self.assertEqual(rows[-1]['checked_rounds'], ['labels'])
        self.assertEqual(rows[-1]['findings'][0]['unit_id'], 'q-1')

    @patch.object(review, '_verify_round_sources', new=lambda *args: None)
    def test_write_summary_publishes_rounds_history_pointer(self):
        self.write('07_review/review-rounds.json', self.payload())
        review_rounds.write_summary(self.batch)
        summary = json.loads((self.batch / '07_review/overall-review-report.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['rounds_history'], '07_review/rounds-history.jsonl')
        self.assertTrue((self.batch / '07_review/rounds-history.jsonl').is_file())

    @patch.object(review, '_verify_round_sources', new=lambda *args: None)
    def test_completed_round_refresh_retires_obsolete_comparisons_with_archive(self):
        payload = self.payload()
        binding = payload['plan']['input_binding']
        old = {'finding_id': 'WF-9901', 'unit_id': 'group-1-6',
               'field': 'child_knowledge', 'severity': 'major', 'status': 'open',
               'reason': '旧规则要求子题非空', 'question': '旧规则要求子题非空',
               'round_sources': [{'round_id': 'labels', 'execution_id': 'old',
                                  'protocol': review.ROUND_PROTOCOL,
                                  'input_binding': binding, 'field': 'child_knowledge', 'value': []}]}
        manual = {'finding_id': 'WF-9902', 'unit_id': 'group-1-6',
                  'field': 'material', 'severity': 'major', 'status': 'open',
                  'reason': '真实材料问题'}
        self.write('07_review/manual-review-queue.json', {'items': [old, manual]})
        ingest.build_review_files(self.batch, {'review_rounds': payload}, reconcile_rounds=True)
        queue = json.loads((self.batch / '07_review/manual-review-queue.json').read_text())['items']
        self.assertNotIn('WF-9901', {r['finding_id'] for r in queue})
        self.assertIn('WF-9902', {r['finding_id'] for r in queue})
        retired = json.loads((self.batch / '07_review/retired-round-findings.json').read_text())['records']
        self.assertTrue(any(r['finding_id'] == 'WF-9901' for r in retired))

    def test_two_round_review_ids(self):
        self.assertEqual(review.ROUND_IDS, ('labels', 'sample'))
        self.assertTrue(review.ROUND_PROTOCOL.endswith('/v3'))

    def test_removed_round_sources_are_retired_from_queue(self):
        # 旧的多轮记录里 knowledge 轮已被取消：落库时这些观察必须退出队列。
        self.write('07_review/review-rounds.json', {'plan': self.plan, 'rounds': []})
        self.write('07_review/manual-review-queue.json', {'queue_version': '1.0', 'items': [
            {'finding_id': 'WF-7001', 'unit_id': 'group-1-6', 'field': 'knowledge', 'severity': 'major',
             'status': 'open', 'question': '旧', 'reason': '旧', 'evidence': [],
             'round_sources': [{'round_id': 'knowledge', 'execution_id': 'old', 'protocol': review.ROUND_PROTOCOL,
                                'input_binding': self.plan['input_binding'], 'field': 'knowledge', 'value': []}]}]})
        ingest.build_review_files(self.batch, {'findings': []})
        items = json.loads((self.batch / '07_review/manual-review-queue.json').read_text())['items']
        self.assertEqual([i['finding_id'] for i in items], [])

    @patch.object(review, '_verify_round_sources', new=lambda *args: None)
    def test_empty_or_invalid_verdict_does_not_complete_round(self):
        for mode in ('empty', 'verdict', 'rationale'):
            payload = self.payload()
            checks = payload['rounds'][0]['checks']
            if mode == 'empty':
                checks.clear()
            elif mode == 'verdict':
                checks[0]['verdict'] = 'unknown'
            else:
                checks[0]['rationale'] = ''
            self.assertNotIn('labels', review.evaluate_rounds(self.batch, payload)['checked_rounds'])

    @patch.object(review, '_verify_round_sources', new=lambda *args: None)
    def test_sample_missing_duplicate_or_wrong_question_blocks(self):
        # 缺题、重复和题号异常都不能声称抽样完成。
        for mode in ("missing", "duplicate", "wrong"):
            payload = self.payload()
            checks = payload["rounds"][1]["checks"]
            if mode == "missing":
                checks.pop()
            elif mode == "duplicate":
                checks.append(copy.deepcopy(checks[0]))
            else:
                checks[0]["number"] = "999"
            self.assertFalse(review.evaluate_rounds(self.batch, payload)["passed"])

    @patch.object(review, '_verify_round_sources', new=lambda *args: None)
    def test_exact_evidenced_decision_resolves_observation_without_rewriting_round(self):
        from workbench.server import apply_decision
        payload = self.payload()
        payload['rounds'][1]['checks'][0].update(verdict='uncertain', rationale='候选知识点存在歧义，需核原文。')
        ingest.build_review_files(self.batch, {'review_rounds': payload})
        queue_path = self.batch / '07_review/manual-review-queue.json'
        queue = json.loads(queue_path.read_text())
        finding = queue['items'][0]
        finding['status'] = 'fixed'
        self.write('07_review/manual-review-queue.json', queue)
        self.assertFalse(review.evaluate_rounds(self.batch)['passed'])
        apply_decision(self.batch, {'finding_id': finding['finding_id'], 'status': 'fixed',
            'resolution_note': '已据原文She reads a book与设问核对，原标注成立。'}, 'agent-evidence-triage')
        result = review.evaluate_rounds(self.batch)
        self.assertTrue(result['passed'])
        self.assertEqual(len(result['resolved_findings']), 1)
        raw = json.loads((self.batch / '07_review/review-rounds.json').read_text())
        self.assertEqual(raw['rounds'][1]['checks'][0]['verdict'], 'uncertain')
        queue = json.loads(queue_path.read_text())
        queue['items'][0]['reason'] = '另一个问题'
        self.write('07_review/manual-review-queue.json', queue)
        self.assertFalse(review.evaluate_rounds(self.batch)['passed'])

    def test_identical_cross_round_findings_preserve_sources_and_decision(self):
        from workbench.server import apply_decision
        payload = self.payload()
        for record in payload['rounds'][:2]:
            record['checks'][0].update(verdict='uncertain', rationale='原文存在同一处歧义。', issue_key='题1答案与原文矛盾')
        first = {'plan': self.plan, 'rounds': payload['rounds'][:1]}
        self.write('07_review/review-rounds.json', first)
        ingest.build_review_files(self.batch, {'findings': review._round_checks(first['rounds'][0], 'labels', self.plan, self.batch)})
        queue_path = self.batch / '07_review/manual-review-queue.json'
        fid = json.loads(queue_path.read_text())['items'][0]['finding_id']
        apply_decision(self.batch, {'finding_id': fid, 'status': 'fixed',
            'resolution_note': '已核对原文并确认歧义。'}, 'reviewer')
        self.write('07_review/review-rounds.json', payload)
        findings = review._round_checks(payload['rounds'][1], 'sample', self.plan, self.batch)
        ingest.build_review_files(self.batch, {'findings': findings})
        ingest.build_review_files(self.batch, {'findings': findings})
        items = json.loads(queue_path.read_text())['items']
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['status'], 'fixed')
        self.assertEqual(items[0]['decided_by'], 'reviewer')
        self.assertEqual(items[1]['status'], 'open')
        self.assertEqual(items[1]['round_sources'][0]['evidence'], payload['rounds'][1]['checks'][0]['evidence'])
        self.assertFalse(review.evaluate_rounds(self.batch)['passed'])

    def test_different_assessments_merge_only_with_same_explicit_resolution(self):
        payload = self.payload()
        for record in payload['rounds'][:2]:
            record['checks'][0].update(verdict='uncertain', rationale='原文存在同一处歧义。',
                                       issue_key='题1答案冲突', resolution_key='核对题1答案原文')
        payload['rounds'][1]['checks'][0]['assessment']['difficulty'][0]['value'] = '困难'
        findings = [review._round_checks(record, record['round_id'], self.plan, self.batch)[0]
                    for record in payload['rounds'][:2]]
        self.assertNotEqual(findings[0]['round_sources'][0]['assessment'], findings[1]['round_sources'][0]['assessment'])
        ingest.build_review_files(self.batch, {'findings': findings})
        ingest.build_review_files(self.batch, {'findings': findings})
        queue = json.loads((self.batch / '07_review/manual-review-queue.json').read_text())['items']
        # 整组题型的一审与二审都在组级观察同一处问题，且给出同一解决口径：
        # 合并为一条，两个独立轮次都保留为来源，不丢失任一轮的判断。
        self.assertEqual(len(queue), 1)
        self.assertEqual({source['round_id'] for source in queue[0]['round_sources']},
                         {'labels', 'sample'})
        # 解决口径不同则不得合并。
        other = copy.deepcopy(findings[1])
        other['round_sources'][0]['target_number'] = None
        ingest.build_review_files(self.batch, {'findings': findings + [dict(
            other, finding_id='SECOND-RESOLUTION',
            round_sources=[dict(source, resolution_key='另一种解决口径')
                           for source in other['round_sources']])]})
        queue = json.loads((self.batch / '07_review/manual-review-queue.json').read_text())['items']
        self.assertEqual(len(queue), 2)

    def test_matching_later_source_never_inherits_prior_decision(self):
        from workbench.server import apply_decision
        payload = self.payload()
        record = payload['rounds'][0]
        record['checks'][0].update(verdict='uncertain', rationale='题目答案与原文冲突。', issue_key='题1答案冲突', resolution_key='核对题1答案原文')
        first = review._round_checks(record, 'labels', self.plan, self.batch)[0]
        later = copy.deepcopy(first)
        later['field'] = 'knowledge'
        later['round_sources'][0].update(round_id='sample', execution_id='other-execution')
        ingest.build_review_files(self.batch, {'findings': [first]})
        queue_path = self.batch / '07_review/manual-review-queue.json'
        fid = json.loads(queue_path.read_text())['items'][0]['finding_id']
        apply_decision(self.batch, {'finding_id': fid, 'status': 'fixed',
                       'resolution_note': '核对原文后确认。'}, 'reviewer')
        ingest.build_review_files(self.batch, {'findings': [later]})
        items = json.loads(queue_path.read_text())['items']
        self.assertEqual([item['status'] for item in items], ['fixed', 'open'])
        self.assertEqual(items[0]['decided_by'], 'reviewer')
        self.assertEqual(items[0]['round_sources'], first['round_sources'])
        self.assertEqual(items[1]['round_sources'], later['round_sources'])
        self.assertNotIn('decided_by', items[1])

    def test_changed_issue_identity_stays_separate(self):
        record = self.payload()['rounds'][0]
        record['checks'][0].update(verdict='uncertain', rationale='原文存在歧义。', issue_key='答案冲突', resolution_key='将答案改为A')
        first = review._round_checks(record, 'labels', self.plan, self.batch)[0]
        for field, value in [('issue_key', '另一个问题'), ('resolution_key', '将答案改为B'),
                             ('resolution_key', None), ('target_number', '2'),
                             ('input_binding', 'different-binding'), ('verdict', 'incorrect'),
                             ('rationale', '不同理由'), ('evidence', [{'quote': 'different'}])]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                later = copy.deepcopy(first)
                later['round_sources'][0].update(round_id='sample', execution_id='another',
                                                 assessment={'knowledge': ['different']})
                later['round_sources'][0][field] = value
                ingest.build_review_files(Path(directory), {'findings': [first, later]})
                queue = json.loads((Path(directory) / '07_review/manual-review-queue.json').read_text())['items']
                self.assertEqual(len(queue), 2)

    @patch.object(review, '_verify_round_sources', new=lambda *args: None)
    def test_distinct_cross_round_observations_remain_separate(self):
        payload = self.payload()
        for record in payload['rounds']:
            for check in record['checks']:
                check.update(verdict='uncertain', rationale='原文存在歧义。', issue_key='答案与原文冲突')
        # Same words but a different quote are not enough to identify the issue.
        payload['rounds'][1]['checks'][0]['evidence'][0]['quote'] = (
            'A. book1 B. pen1 C. map1 D. bag1')
        ingest.build_review_files(self.batch, {'review_rounds': payload})
        items = json.loads((self.batch / '07_review/manual-review-queue.json').read_text())['items']
        # 一审一条组级发现 + 二审整组一条发现：引证不同，不合并。
        self.assertEqual(len(items), 2)
        self.assertEqual(len({item['finding_id'] for item in items}), 2)

    @patch.object(review, '_verify_round_sources', new=lambda *args: None)
    def test_only_real_findings_enter_manual_queue(self):
        payload = self.payload()
        ingest.build_review_files(self.batch, {"review_rounds": payload})
        self.assertEqual(json.loads((self.batch / "07_review/manual-review-queue.json").read_text())["items"], [])
        payload["rounds"][1]["checks"][0]["verdict"] = "uncertain"
        payload["rounds"][1]["checks"][0]["rationale"] = "The question could test either detail or inference; source answer evidence conflicts."
        ingest.build_review_files(self.batch, {"review_rounds": payload})
        items = json.loads((self.batch / "07_review/manual-review-queue.json").read_text())["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["status"], "open")
        self.assertFalse(review.evaluate_rounds(self.batch)["passed"])

    def test_info_is_retained_without_creating_human_work(self):
        finding = {'unit_id': 'group-1-6', 'field': 'answer', 'severity': 'info',
                   'reason': '与原卷答案一致，无需处理', 'evidence': ['原题第1行']}
        ingest.build_review_files(self.batch, {'findings': [finding]})
        self.assertEqual(json.loads((self.batch / '07_review/manual-review-queue.json').read_text())['items'], [])
        rows = json.loads((self.batch / '07_review/content-review.json').read_text())['findings']
        self.assertEqual(rows[0]['status'], 'informational')
        self.assertFalse(rows[0]['blocking'])

    def test_info_with_explicit_blocking_summary_still_enters_queue(self):
        finding = {'unit_id': 'group-1-6', 'field': 'answer', 'severity': 'info',
                   'reason': '实际存在待核验冲突', 'evidence': ['原题第1行']}
        ingest.build_review_files(self.batch, {'findings': [finding],
            'summary': {'blocking': [{'unit_id': 'group-1-6', 'field': 'answer'}]}})
        # The explicit summary must retain its blocking authority.
        content = json.loads((self.batch / '07_review/content-review.json').read_text())['findings']
        self.assertTrue(content[0]['blocking'])
        self.assertEqual(len(json.loads((self.batch / '07_review/manual-review-queue.json').read_text())['items']), 1)

    def test_same_finding_twice_in_one_submission_is_not_duplicated(self):
        finding = {'unit_id': 'group-1-6', 'field': 'knowledge', 'severity': 'major',
                   'reason': '标签与原题不符', 'evidence': ['原题第1行']}
        result = ingest.build_review_files(self.batch, {'findings': [finding, copy.deepcopy(finding)]})
        self.assertEqual(result['added'], 1)
        self.assertEqual(len(json.loads((self.batch / '07_review/metadata-review.json').read_text())['findings']), 1)
        self.assertEqual(len(json.loads((self.batch / '07_review/manual-review-queue.json').read_text())['items']), 1)

    def test_annotation_ingest_count_is_not_independent_round_count(self):
        result = {"annotations": [{"unit_id": "group-1-6"}]}
        ingest.record_units(self.batch, result)
        ingest.record_units(self.batch, result)
        unit = json.loads((self.batch / "07_review/unit-status.json").read_text())["units"]["group-1-6"]
        self.assertEqual(unit["rounds"], 0)
        self.assertEqual(unit["observations"], 2)

    def test_portable_export_import_three_real_rounds(self):
        for round_id in review.ROUND_IDS:
            exported = review_rounds.export_task(self.batch)
            self.assertEqual(exported["round_id"], round_id)
            package = Path(exported["package_dir"])
            self.assertTrue((package / "response.schema.json").is_file())
            result = review_rounds.import_result(self.batch, exported["task_id"], self.response(round_id))
            self.assertEqual(result["round_completed"], round_id)
        self.assertTrue(result["completed"])
        self.assertEqual(review_rounds.export_task(self.batch)["action"], "complete")
        stored = json.loads((self.batch / "07_review/review-rounds.json").read_text())
        self.assertEqual(len({r["execution_id"] for r in stored["rounds"]}), 2)
        self.assertEqual(json.loads((self.batch / "07_review/manual-review-queue.json").read_text())["items"], [])

    def test_invalid_needs_review_and_stale_import_retain_candidates(self):
        exported = review_rounds.export_task(self.batch)
        result = self.response("labels")
        result["status"] = "needs_review"
        self.assertEqual(review_rounds.import_result(self.batch, exported["task_id"], result)["action"], "blocked")
        self.assertFalse((self.batch / "07_review/review-rounds.json").exists())
        with self.assertRaisesRegex(ValueError, "already consumed"):
            review_rounds.import_result(self.batch, exported["task_id"], self.response("labels"))
        fresh = review_rounds.export_task(self.batch)
        self.write("01_source/正文.txt", "changed input")
        with self.assertRaisesRegex((ValueError, RuntimeError), "changed"):
            review_rounds.import_result(self.batch, fresh["task_id"], self.response("labels"))
        self.assertEqual(len(list((self.batch / "workflow/review-results" / exported["task_id"]).glob("*.json"))), 1)
        self.assertFalse((self.batch / "07_review/review-rounds.json").exists())

    def test_progress_ledger_changes_do_not_invalidate_question_evidence(self):
        original = agent_tasks.build_task(self.batch, 'review', max_input_chars=600000)
        original['input_hashes']['workflow/manifest.json'] = 'old-progress-ledger-hash'
        original['input_hashes']['manifest.json'] = 'legacy-progress-ledger-hash'
        with patch.object(agent_tasks, 'build_task', return_value=original):
            exported = review_rounds.export_task(self.batch)
        binding = json.loads((self.batch / 'workflow/review-bindings' / (exported['task_id'] + '.json')).read_text())
        self.assertNotIn('workflow/manifest.json', binding['input_hashes'])
        self.assertNotIn('manifest.json', binding['input_hashes'])
        self.write('workflow/manifest.json', {'progress': 'changed by another question'})
        result = review_rounds.import_result(self.batch, exported['task_id'], self.response('labels'))
        self.assertEqual(result['round_completed'], 'labels')

    def test_replay_is_idempotent_and_different_result_cannot_overwrite(self):
        exported = review_rounds.export_task(self.batch)
        response = self.response("labels")
        first = review_rounds.import_result(self.batch, exported["task_id"], response)
        self.assertEqual(review_rounds.import_result(self.batch, exported["task_id"], response), first)
        response["artifacts"]["review_round"]["checks"][0]["rationale"] = "changed response"
        with self.assertRaisesRegex(ValueError, "already consumed"):
            review_rounds.import_result(self.batch, exported["task_id"], response)

    def test_prior_round_records_and_budget_are_bound(self):
        exported = review_rounds.export_task(self.batch)
        review_rounds.import_result(self.batch, exported["task_id"], self.response("labels"))
        second = review_rounds.export_task(self.batch)
        payload = json.loads((self.batch / "07_review/review-rounds.json").read_text())
        payload["rounds"][0]["checks"][0]["rationale"] = "Changed earlier review"
        self.write("07_review/review-rounds.json", payload)
        with self.assertRaisesRegex(ValueError, "Earlier round records changed"):
            review_rounds.import_result(self.batch, second["task_id"], self.response("knowledge"))
        with self.assertRaisesRegex(ValueError, "exceed(?:ing|s) max_input_chars"):
            review_rounds.export_task(self.batch, max_input_chars=100)

    def test_conflict_keeps_findings_and_continues_later_round_export(self):
        exported = review_rounds.export_task(self.batch)
        response = self.response("labels")
        response["artifacts"]["review_round"]["checks"][0]["verdict"] = "error"
        response["artifacts"]["review_round"]["checks"][0]["assessment"]["subtypes"] = ["阅读匹配"]
        result = review_rounds.import_result(self.batch, exported["task_id"], response)
        self.assertEqual(result["action"], "continue")
        self.assertEqual(review_rounds.export_task(self.batch)["round_id"], "sample")
        self.assertEqual(len(result["review"]["unchecked"]), 1)
        self.assertFalse(result["quality_passed"])
        self.assertEqual(len(json.loads((self.batch / '07_review/manual-review-queue.json').read_text())['items']), 1)

    def test_real_ai_findings_do_not_stop_independent_rounds(self):
        self.write("07_review/manual-review-queue.json", {"items": [
            {"finding_id": "actual-conflict", "unit_id": "group-1-6", "status": "open", "reason": "Conflicting source answer"}]})
        self.assertEqual(review_rounds.export_task(self.batch)["round_id"], "labels")
        self.assertTrue((self.batch / "workflow/review-tasks").exists())
        self.assertEqual(len(review.evaluate_rounds(self.batch)["unchecked"]), 2)

    def test_run_invokes_separate_process_attempts_in_order(self):
        seen = []
        def invoke(prompt, run_dir, config):
            round_id = review.ROUND_IDS[len(seen)]
            self.assertIn("ROUND: " + round_id, prompt)
            seen.append(run_dir)
            return {"response": self.response(round_id)}
        with patch.object(review_rounds, "invoke", side_effect=invoke):
            result = review_rounds.run(self.batch, config={"provider": "fixture"})
        self.assertTrue(result["completed"])
        self.assertEqual(len(set(seen)), 2)

    def test_findings_allow_all_rounds_but_do_not_pass_quality(self):
        seen = []
        def invoke(prompt, run_dir, config):
            rid = review.ROUND_IDS[len(seen)]
            seen.append(rid)
            response = self.response(rid)
            response['artifacts']['review_round']['checks'][0]['verdict'] = 'uncertain'
            return {'response': response}
        with patch.object(review_rounds, 'invoke', side_effect=invoke):
            result = review_rounds.run(self.batch, config={'provider': 'fixture'})
        self.assertTrue(result['completed'])
        self.assertFalse(result['quality_passed'])
        self.assertEqual(seen, list(review.ROUND_IDS))
        self.assertEqual(result['summary']['open_count'], 2)
        self.assertFalse(result['summary']['release_ready'])

    def test_exhausted_round_failure_does_not_skip_other_rounds(self):
        seen = []
        def invoke(prompt, run_dir, config):
            rid = next(r for r in review.ROUND_IDS if 'ROUND: ' + r in prompt)
            seen.append(rid)
            if rid == 'labels':
                raise ValueError('malformed JSON')
            return {'response': self.response(rid)}
        with patch.object(review_rounds, 'invoke', side_effect=invoke):
            result = review_rounds.run(self.batch, config={'provider': 'fixture', 'review_max_attempts': 2})
        self.assertFalse(result['completed'])
        self.assertEqual(seen, ['labels', 'labels', 'sample'])
        self.assertEqual(result['review']['checked_rounds'], ['sample'])
        self.assertIn('labels', result['summary']['execution_failures'])
        # A new invocation retries only the unfinished round.
        with patch.object(review_rounds, 'invoke', return_value={'response': self.response('labels')}):
            resumed = review_rounds.run(self.batch, config={'provider': 'fixture'})
        self.assertTrue(resumed['completed'])

    def test_agent_entry_reports_partial_round_handoff_and_resume_binding(self):
        from workflow import agent
        def completed_ai():
            print(json.dumps({"report": {"completed": True}}))
            return 0
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(review_rounds, "run", return_value={"action": "exported", "completed": False}):
            code = agent._review_after(completed_ai, self.batch)
        self.assertEqual(code, 3)
        report = json.loads(output.getvalue())
        self.assertFalse(report["completed"])
        self.assertEqual(report["status"], "needs_review")
        self.assertTrue(agent._ai_review_current(self.batch))
        self.write("01_source/metadata.json", {"year": 2022})
        self.assertFalse(agent._ai_review_current(self.batch))

    def test_invalid_prior_round_is_reexecuted_without_blocking_valid_rounds(self):
        for round_id in review.ROUND_IDS:
            exported = review_rounds.export_task(self.batch)
            review_rounds.import_result(self.batch, exported['task_id'], self.response(round_id))
        payload = json.loads((self.batch / '07_review/review-rounds.json').read_text())
        payload['rounds'][0]['checks'][0]['evidence'][0]['quote'] = '不存在的证据'
        self.write('07_review/review-rounds.json', payload)
        exported = review_rounds.export_task(self.batch)
        self.assertEqual(exported['round_id'], 'labels')
        result = review_rounds.import_result(self.batch, exported['task_id'], self.response('labels'))
        self.assertTrue(result['completed'])
        self.assertEqual(result['review']['checked_rounds'], list(review.ROUND_IDS))
        history = list((self.batch / 'workflow/review-history').glob('*.json'))
        self.assertTrue(history)
        self.assertTrue(any(json.loads(path.read_text())['rounds'][0]['checks'][0]['evidence'][0]['quote']
                            == '不存在的证据' for path in history))

    @patch.object(review, '_verify_round_sources', new=lambda *args: None)
    def test_summary_does_not_release_when_other_required_check_is_pending(self):
        ingest.build_review_files(self.batch, {'review_rounds': self.payload()})
        self.write('07_review/unchecked.json', [{'field': 'review_ai', 'required': True, 'reason': '初审尚未完成'}])
        summary = review_rounds.write_summary(self.batch)
        self.assertTrue(summary['execution_completed'])
        self.assertTrue(summary['rounds_quality_passed'])
        self.assertFalse(summary['quality_passed'])
        self.assertFalse(summary['release_ready'])
        self.assertEqual(summary['required_unchecked_count'], 1)

    def test_media_observations_bind_actual_image_bytes(self):
        import hashlib
        image = self.batch / '01_source/image.jpeg'
        image.write_bytes(b'fixture-original-image')
        self.write('workflow/media-observations.json', {'observations': [
            {'path': '01_source/image.jpeg', 'sha256': hashlib.sha256(image.read_bytes()).hexdigest(), 'description': '已观察的题图'}]})
        plan = review.prepare_round_plan(self.batch)
        self.assertIn('01_source/image.jpeg', plan['input_hashes'])
        image.write_bytes(b'changed-image')
        with self.assertRaisesRegex(ValueError, 'Media observation is stale'):
            review.prepare_round_plan(self.batch)

    def test_model_cannot_supply_execution_or_gate_claims(self):
        exported = review_rounds.export_task(self.batch)
        response = self.response("labels")
        response["artifacts"]["review_round"]["execution_id"] = "fake"
        with self.assertRaises(ValueError):
            review_rounds.import_result(self.batch, exported["task_id"], response)
        self.assertFalse((self.batch / "07_review/review-rounds.json").exists())

    def _single_label_record(self, subtypes):
        record = {"round_id": "labels", "input_binding": self.plan["input_binding"],
                  "execution_id": "fixture-separate-labels", "checks": self.checks("labels")}
        record["checks"][0]["assessment"]["subtypes"] = subtypes
        return record

    def test_blank_catalog_subtype_is_recoverable_not_a_fatal_round(self):
        # A type with catalog subtypes but an empty list (e.g. 书面表达 omitted)
        # must not fail the whole target/round; the gap still becomes a visible
        # 子类 difference through evaluate_rounds/consensus.
        findings = review._round_checks(self._single_label_record([]), "labels", self.plan, self.batch)
        self.assertEqual(findings, [])
        # A subtype that is not in the catalog still fails deterministically.
        with self.assertRaisesRegex(ValueError, "Invalid review labels"):
            review._round_checks(self._single_label_record(["不存在的子类"]), "labels", self.plan, self.batch)
        # A malformed subtype list (non-strings) also still fails.
        with self.assertRaises(ValueError):
            review._round_checks(self._single_label_record([1, 2]), "labels", self.plan, self.batch)

    def test_blank_subtype_diff_is_reported_against_original_labels(self):
        # Original label has 子类 ['阅读判断']; a blank blind subtype must surface
        # as a real difference via consensus (never silently accepted).
        from workflow import review_consensus
        record = self._single_label_record([])
        findings = review_consensus.compare(self.batch, self.plan, [record])
        subtype_diffs = [f for f in findings if f.get("field") == "subtypes"]
        self.assertTrue(subtype_diffs, findings)

    def test_identical_round_failure_stops_after_strike_budget(self):
        # A deterministic per-round failure that repeats across re-exported tasks
        # must stop after a small strike budget instead of spinning forever.
        def fake_run(batch_dir, config=None, portable_max_input_chars=None, **kwargs):
            return {"action": "blocked", "completed": False,
                    "summary": {"execution_failures": {"labels": {
                        "attempts": 2,
                        "reason": "1 target(s) need repair: Invalid review labels: 题目：知识点路径不在目录里：['语篇体裁/应用文/电子邮件']"}}}}
        config = {"provider": "fixture", "review_max_attempts": 2}
        results = []
        with patch.object(review_rounds, "_run", side_effect=fake_run):
            for _ in range(5):
                results.append(review_rounds.run(self.batch, config=config))
        # It must eventually turn into a bounded stop with the concrete error.
        stalls = [r for r in results if r.get("stall")]
        self.assertTrue(stalls, [r.get("reason") for r in results])
        stall = stalls[0]
        self.assertIn("应用文/电子邮件", stall["reason"])
        self.assertFalse(stall["completed"])
        # Once stalled, subsequent runs keep being blocked (do not spin again).
        self.assertTrue(all(r.get("stall") for r in results[-2:]))

    def test_persistent_stall_guard_stops_paid_calls_until_route_or_inputs_change(self):
        seen = []
        def invoke(prompt, run_dir, config, permit_parallel=False):
            import re
            rid = re.search(r'ROUND: (\w+)', prompt).group(1)
            seen.append(rid)
            if rid == 'labels':
                raise ValueError('repeatable protocol defect')
            return {'response': self.response(rid)}
        config = {'provider': 'fixture', 'review_max_attempts': 1, 'review_part_parallel': 2}
        with patch.object(review_rounds, 'invoke', side_effect=invoke):
            for _ in range(5):
                result = review_rounds.run(self.batch, config)
            self.assertTrue(result['repair_required'])
            self.assertEqual(seen.count('labels'), 3)
            self.assertEqual(seen.count('sample'), 1)
            self.assertIn('sample', review.evaluate_rounds(self.batch)['checked_rounds'])
            review_rounds.run(self.batch, dict(config, model='explicit-new-route'))
            review_rounds.run(self.batch, config)
        self.assertEqual(seen.count('labels'), 4, 'A new route gets a fresh budget; reverting cannot reset the old one')
        before = review_rounds._stall_context(self.batch, config)
        self.write('01_source/正文.txt', (self.batch / '01_source/正文.txt').read_text() + '\nNew source fact.')
        self.assertNotEqual(before, review_rounds._stall_context(self.batch, config))

    def test_no_progress_import_escalates_without_reinvoking_or_losing_sample(self):
        original = review_rounds.import_result
        calls = []
        def invoke(prompt, run_dir, config, permit_parallel=False):
            import re
            rid = re.search(r'ROUND: (\w+)', prompt).group(1)
            calls.append(rid)
            return {'response': self.response(rid)}
        def stalled_import(batch, tid, response):
            rid = review_rounds._check_bound_inputs(batch, tid)['task']['round_id']
            if rid == 'labels':
                return {'action': 'continue', 'completed': False}
            return original(batch, tid, response)
        config = {'provider': 'fixture', 'review_max_attempts': 2, 'review_part_parallel': 2}
        with patch.object(review_rounds, 'invoke', side_effect=invoke):
            with patch.object(review_rounds, 'import_result', side_effect=stalled_import):
                first = review_rounds.run(self.batch, config)
            self.assertEqual(first['stall']['error_code'], 'review_consumer')
            self.assertIn('sample', review.evaluate_rounds(self.batch)['checked_rounds'])
            self.assertEqual(len(list((self.batch / 'workflow/review-pending').glob('*.json'))), 1)
            with patch.object(review_rounds, 'import_result', side_effect=stalled_import):
                second = review_rounds.run(self.batch, config)
            self.assertTrue(second['repair_required'])
            self.assertEqual(sorted(calls), ['labels', 'sample'])
            # Restored local acceptance can replay retained evidence even with an
            # unchanged budget scope. The guard stops paid calls, not recovery.
            repaired = review_rounds.run(self.batch, config)
            self.assertTrue(repaired['completed'], repaired)
            self.assertEqual(sorted(calls), ['labels', 'sample'])

    def test_stall_accounting_does_not_mask_disappearing_inputs(self):
        calls = []
        def invoke(prompt, run_dir, config):
            calls.append(prompt)
            source = self.batch / '01_source/正文.txt'
            self.assertTrue(source.resolve().is_relative_to(self.batch.resolve()))
            source.unlink()
            return {'response': self.response('labels')}
        with patch.object(review_rounds, 'invoke', side_effect=invoke):
            result = review_rounds.run(self.batch, {'provider': 'fixture', 'review_max_attempts': 2})
        self.assertTrue(result['inputs_changed'], result)
        self.assertTrue(result['resumable'])
        self.assertEqual(len(calls), 1)
        self.assertFalse((self.batch / 'workflow/review-stall.json').exists())

    def test_parallel_missing_root_fields_gets_feedback_before_pending_cache(self):
        seen = []
        def invoke(prompt, run_dir, config, permit_parallel=False):
            import re
            rid = re.search(r'ROUND: (\w+)', prompt).group(1)
            seen.append((rid, prompt))
            response = self.response(rid)
            if rid == 'labels' and sum(r == rid for r, _ in seen) == 1:
                response.pop('notes')
                response.pop('findings')
            return {'response': response}
        with patch.object(review_rounds, 'invoke', side_effect=invoke), patch.object(review_rounds._time, 'sleep'):
            result = review_rounds.run(self.batch, {'provider': 'fixture', 'review_part_parallel': 2,
                                                    'review_max_attempts': 2})
        self.assertTrue(result['completed'], result)
        label_prompts = [p for rid, p in seen if rid == 'labels']
        self.assertEqual(len(label_prompts), 2)
        self.assertIn("absent=['findings', 'notes']", label_prompts[-1])
        self.assertEqual(sum(rid == 'sample' for rid, _ in seen), 1)

    def test_install_provenance_failure_never_publishes_round_or_success_receipt(self):
        exported = review_rounds.export_task(self.batch, only_round='labels')
        with patch.object(review, '_verify_round_sources', side_effect=ValueError('retained checks mismatch')):
            with self.assertRaisesRegex(review_rounds.ReviewConsumerError, 'retained checks mismatch'):
                review_rounds.import_result(self.batch, exported['task_id'], self.response('labels'))
        self.assertFalse((self.batch / '07_review/review-rounds.json').exists())
        self.assertFalse((self.batch / 'workflow/review-bindings' / (exported['task_id'] + '.receipt.json')).exists())
        self.assertTrue((self.batch / 'workflow/review-transactions' / (exported['task_id'] + '.json')).exists())
        # Recovery uses the retained transaction after the verifier is repaired.
        review_rounds.export_task(self.batch)
        self.assertIn('labels', review.evaluate_rounds(self.batch)['checked_rounds'])

    def test_bad_example_contract_is_rejected_before_any_model_call(self):
        example = review_rounds._round_envelope()
        example.pop('notes')
        with patch.object(review_rounds, '_round_envelope', return_value=example):
            with patch.object(review_rounds, 'invoke') as invoke:
                result = review_rounds.run(self.batch, {'provider': 'fixture', 'review_part_parallel': 2})
        self.assertEqual(result['action'], 'blocked')
        self.assertIn("absent=['notes']", result['reason'])
        invoke.assert_not_called()

    def test_invalid_legacy_pending_is_not_replayed_forever_and_retry_has_feedback(self):
        part = review_rounds.export_task(self.batch, only_round='labels')
        response = self.response('labels')
        response.pop('notes')
        response.pop('findings')
        review_rounds._save_pending(self.batch, part['task_id'], response)
        seen = []
        def invoke(prompt, run_dir, config, permit_parallel=False):
            import re
            rid = re.search(r'ROUND: (\w+)', prompt).group(1)
            seen.append((rid, prompt))
            return {'response': self.response(rid)}
        config = {'provider': 'fixture', 'review_part_parallel': 2, 'review_max_attempts': 1}
        with patch.object(review_rounds, 'invoke', side_effect=invoke):
            first = review_rounds.run(self.batch, config)
            self.assertFalse(first['completed'])
            self.assertEqual([rid for rid, _ in seen], ['sample'])
            result = review_rounds.run(self.batch, config)
        self.assertTrue(result['completed'], result)
        self.assertEqual([rid for rid, _ in seen], ['sample', 'labels'])
        self.assertIn("absent=['findings', 'notes']", seen[-1][1])
        failure = json.loads((self.batch / 'workflow/review-failures' / (part['task_id'] + '.json')).read_text())
        self.assertEqual(failure['input_binding'], self.plan['input_binding'])
        self.assertEqual(failure['targets'], ['group-1-6'])

    def test_leaf_markers_present_in_review_catalog(self):
        # The blind review catalog must label every node as a leaf (terminal) or
        # not, so the model stops at real leaves instead of drilling deeper.
        from workflow import agent_tasks
        self._parallel_batch()
        base = agent_tasks.build_task(self.batch, "review", max_input_chars=1000000)
        part = review_rounds._partition_contexts(base, 1000000, round_id="labels")[0]
        context = part["context"]
        self.assertIn("leafTerminal", context)
        data = json.loads(context.split("\n", 1)[1])
        points = data["references/knowledge-catalog.json"]["points"]
        self.assertTrue(points)
        for point in points:
            self.assertIn("leaf", point)
            self.assertIn("leafTerminal", point)


if __name__ == "__main__":
    unittest.main()
