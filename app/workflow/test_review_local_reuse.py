"""Real retained executions survive unrelated changes; affected units stay blind."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import review, review_rounds


class ReviewLocalReuseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        self.units = [dict(unit_id=f'q-{n}', unit_kind='standalone', numbers=[n],
                           type='阅读理解', stem=f'{n}. Question number {n}?\nA. book B. pen C. map D. bag')
                      for n in range(1, 6)]
        self.units.append(dict(unit_id='group-6-7', unit_kind='group', numbers=[6, 7], type='阅读理解',
                               stem='Shared passage about reading.\n6. First group question?\nA. yes B. no\n7. Last group question?\nA. yes B. no'))
        self.knowledge = ['语篇体裁/记叙文/故事', '主题/人与自我/生活与学习/日常起居']
        self.write_source()
        self.write('03_types/types.json', {'items': [dict(unit_id=u['unit_id'], type='阅读理解', subtypes=['阅读判断']) for u in self.units]})
        self.write('04_knowledge/knowledge.json', {'items': [dict(unit_id=u['unit_id'], knowledge=self.knowledge, subquestions=[]) for u in self.units]})
        self.write('04_knowledge/explanations.json', {'items': [dict(unit_id=u['unit_id'], explanation='无', articleSummary='无', subquestions=[]) for u in self.units]})
        self.write('01_source_ans/answers.json', {'answers': {str(n): 'A' for n in range(1, 8)}})
        self.write('06_batch/batch.json', {'items': [dict(unit_id=u['unit_id'], stem=u['stem'], answer='A',
            explanation='原文依据。', knowledge=self.knowledge,
            filterMetadata={'subtypes': ['阅读判断'], 'difficulty': '容易', 'source': '原卷'}) for u in self.units]})

    def write(self, name, value):
        path = self.batch / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def load(self, name):
        return json.loads((self.batch / name).read_text())

    def write_source(self):
        self.write('01_source/正文.txt', '\n'.join(u['stem'] for u in self.units))
        self.write('01_source/structure.confirmed.json', {'units': self.units})

    def response(self, exported):
        binding = self.load('workflow/review-bindings/' + exported['task_id'] + '.json')
        state = self.load(binding['state_path'])
        targets = state['parts'][binding['part_index']]['targets']
        checks = []
        for target in targets:
            uid = target['unit_id'] if isinstance(target, dict) else target
            unit = next(u for u in self.units if u['unit_id'] == uid)
            assessment = dict(type='阅读理解', subtypes=['阅读判断'], knowledge=self.knowledge,
                subquestions=[dict(number=str(n), knowledge=[]) for n in unit['numbers']] if unit['unit_kind'] == 'group' else [])
            check = dict(unit_id=uid, verdict='ok', rationale='逐题对照原文、答案和组级考点。',
                evidence=[dict(path='01_source/正文.txt', locator='当前单元', quote=unit['stem'].splitlines()[0])], assessment=assessment)
            if exported['round_id'] == 'sample':
                check['number'] = target['number']
                assessment['source_comparison'] = '已对照本单元原文和答案。'
            checks.append(check)
        return dict(status='completed', artifacts={'review_round': {'checks': checks}}, notes=[], findings=[])

    def finish(self):
        for _ in range(20):
            exported = review_rounds.export_task(self.batch, max_units_per_part=2)
            if exported['action'] == 'complete':
                return exported
            self.assertEqual(exported['action'], 'exported', exported)
            review_rounds.import_result(self.batch, exported['task_id'], self.response(exported))
        self.fail('Review did not complete')

    def assert_only_target_pending(self, uid):
        exported = review_rounds.export_task(self.batch, max_units_per_part=2)
        self.assertEqual(exported['round_id'], 'labels')
        self.assertEqual(exported['part_count'], 1)
        binding = self.load('workflow/review-bindings/' + exported['task_id'] + '.json')
        state = self.load(binding['state_path'])
        self.assertEqual(state['parts'][0]['targets'], [uid])
        self.assertEqual(len(state['reused_checks']), len(self.units) - 1)
        # A repair remains independently blind, including final assembled labels.
        prompt = binding['task']['prompt']
        supplied = json.loads(prompt.split('BEGIN SUPPLIED CONTENT', 1)[1].split('\n', 1)[1].rsplit('END SUPPLIED CONTENT', 1)[0])
        final = supplied['06_batch/batch.json']['items']
        self.assertEqual([row['unit_id'] for row in final], [uid])
        self.assertNotIn('knowledge', final[0])
        self.assertNotIn('filterMetadata', final[0])
        return exported

    def test_metadata_difficulty_and_report_refresh_reuse_both_rounds(self):
        self.finish()
        old_plan = review.prepare_round_plan(self.batch)
        semantic = review.stage_semantic_inputs(self.batch)
        final = self.load('06_batch/batch.json')
        for item in final['items']:
            item['filterMetadata'].update(difficulty='困难', source='更新来源', categories=['分类'])
            item['words'] = 999
        self.write('06_batch/batch.json', final)
        self.write('01_source/metadata.json', {'source_type': '中考真题', 'year': 2026})
        self.write('07_review/validation-report.json', {'generated_at': 'later', 'passed': True})
        self.assertEqual(semantic, review.stage_semantic_inputs(self.batch))
        new_plan = review.prepare_round_plan(self.batch)
        self.assertNotEqual(old_plan['input_binding'], new_plan['input_binding'])
        self.assertEqual(old_plan['sample'], new_plan['sample'])
        self.assertTrue(review.evaluate_rounds(self.batch)['execution_completed'])
        self.assertEqual(review_rounds.export_task(self.batch)['action'], 'complete')
        records = self.load('07_review/review-rounds.json')['rounds']
        self.assertTrue(all(record['reuse'] for record in records))

    def test_question_and_child_source_metadata_preserves_bindings_and_findings(self):
        final = self.load('06_batch/batch.json')
        for row in final['items']:
            row.update(source='未提供', source_type='未提供', year='')
            row['subquestions'] = [dict(number=1, source='未提供', source_type='未提供', year='', answer='A')]
        self.write('06_batch/batch_meta.json', final)
        self.finish()
        old = review.prepare_round_plan(self.batch)
        for row in final['items']:
            row.update(source='当前来源', source_type='同步练习', year=2026, difficulty='适中')
            child = row['subquestions'][0]
            for key in ('source', 'source_type', 'year'):
                child.pop(key)
            child['difficulty'] = '适中'
        self.write('06_batch/batch_meta.json', final)
        current = review.prepare_round_plan(self.batch)
        self.assertNotEqual(old['input_hashes']['06_batch/batch_meta.json'], current['input_hashes']['06_batch/batch_meta.json'])
        self.assertEqual(old['unit_bindings'], current['unit_bindings'])
        self.assertTrue(review.evaluate_rounds(self.batch)['execution_completed'])
        self.assertEqual(review_rounds.export_task(self.batch)['action'], 'complete')
        for field, value in [('stem', '真实题干变化'), ('answer', 'B'), ('knowledge', ['真实标签变化'])]:
            with self.subTest(field=field):
                changed = copy.deepcopy(final)
                changed['items'][0]['subquestions'][0][field] = value
                self.write('06_batch/batch_meta.json', changed)
                rebound = review.prepare_round_plan(self.batch)
                self.assertEqual([uid for uid in current['unit_bindings'] if current['unit_bindings'][uid] != rebound['unit_bindings'][uid]], ['q-1'])
        self.assertNotEqual(review.semantic_review_value({'source': {'text': '来源对象A'}}, question=True),
                            review.semantic_review_value({'source': {'text': '来源对象B'}}, question=True))
        self.assertNotEqual(review.semantic_review_value({'source': '真实来源证据A'}),
                            review.semantic_review_value({'source': '真实来源证据B'}))
        self.assertNotEqual(review.semantic_review_value({'unit_id': 'q-1', 'content': {'source': 'A'}}, question=True),
                            review.semantic_review_value({'unit_id': 'q-1', 'content': {'source': 'B'}}, question=True))

    def test_legacy_completed_rounds_migrate_source_projection_without_model(self):
        final = self.load('06_batch/batch.json')
        for row in final['items']:
            row['subquestions'] = [dict(number=1, source='未提供', answer='A')]
        self.write('06_batch/batch_meta.json', final)
        current_bindings = review._unit_bindings
        def legacy(batch, units, hashes, **kwargs):
            return current_bindings(batch, units, hashes, legacy_source=True)
        with patch.object(review, '_unit_bindings', side_effect=legacy):
            self.finish()
        review._PLAN_CACHE.clear()
        old = self.load('07_review/review-rounds.json')
        for row in final['items']:
            row['subquestions'][0].pop('source')
        self.write('06_batch/batch_meta.json', final)
        plan = review.prepare_round_plan(self.batch)
        accepted = review.acceptable_unit_bindings(self.batch, plan)
        self.assertTrue(all(digest in accepted[uid] for uid, digest in old['plan']['unit_bindings'].items()))
        for field, value in [('stem', 'Changed stem'), ('answer', 'B'), ('knowledge', ['Changed label']),
                             ('content', {'source': 'Changed substantive evidence'})]:
            with self.subTest(field=field):
                changed = copy.deepcopy(final)
                changed['items'][0]['subquestions'][0][field] = value
                self.write('06_batch/batch_meta.json', changed)
                self.assertNotIn(old['plan']['unit_bindings']['q-1'], review.acceptable_unit_bindings(self.batch)['q-1'])
        self.write('06_batch/batch_meta.json', final)
        with patch.object(review_rounds, 'invoke', side_effect=AssertionError('No model allowed')) as invoke:
            self.assertEqual(review_rounds.export_task(self.batch)['action'], 'complete')
            self.assertTrue(review.evaluate_rounds(self.batch)['execution_completed'])
            invoke.assert_not_called()

    def test_metadata_only_change_still_rejects_inflight_exact_hash(self):
        final = self.load('06_batch/batch.json')
        final['items'][0]['source'] = '旧来源'
        self.write('06_batch/batch_meta.json', final)
        exported = review_rounds.export_task(self.batch, max_units_per_part=2)
        response = self.response(exported)
        final['items'][0]['source'] = '新来源'
        self.write('06_batch/batch_meta.json', final)
        with self.assertRaisesRegex((ValueError, RuntimeError), 'changed|stale|binding'):
            review_rounds.import_result(self.batch, exported['task_id'], response)

    def test_final_answer_change_rechecks_only_affected_unit_and_sample(self):
        self.finish()
        old_plan = review.prepare_round_plan(self.batch)
        uid = old_plan['sample'][0]['unit_id']
        final = self.load('06_batch/batch.json')
        next(row for row in final['items'] if row['unit_id'] == uid)['answer'] = 'B'
        self.write('06_batch/batch.json', final)
        self.assertFalse(review.evaluate_rounds(self.batch)['execution_completed'])
        exported = self.assert_only_target_pending(uid)
        review_rounds.import_result(self.batch, exported['task_id'], self.response(exported))
        sample = review_rounds.export_task(self.batch)
        self.assertEqual(sample['round_id'], 'sample')
        binding = self.load('workflow/review-bindings/' + sample['task_id'] + '.json')
        state = self.load(binding['state_path'])
        self.assertEqual({t['unit_id'] for t in state['parts'][0]['targets']}, {uid})
        review_rounds.import_result(self.batch, sample['task_id'], self.response(sample))
        self.assertTrue(review.evaluate_rounds(self.batch)['execution_completed'])
        # Subsequent metadata-only changes flatten references to the true origin.
        self.write('01_source/metadata.json', {'year': 2027})
        self.assertTrue(review.evaluate_rounds(self.batch)['execution_completed'])
        self.assertEqual(review_rounds.export_task(self.batch)['action'], 'complete')

    def test_source_stem_change_and_shared_material_have_local_group_scope(self):
        self.write('01_source/extraction.json', {'source': 'original.docx', 'source_sha256': 'old-original',
            'text': 'duplicated extracted text', 'characters': 500, 'warnings': [], 'tables': [], 'images': []})
        self.finish()
        old = review.prepare_round_plan(self.batch)['unit_bindings']
        self.units[-1]['stem'] = self.units[-1]['stem'].replace('about reading', 'about writing')
        self.write_source()
        self.write('01_source/extraction.json', {'source': 'original.docx', 'source_sha256': 'new-original',
            'text': 'changed duplicated text', 'characters': 501, 'warnings': [], 'tables': [], 'images': []})
        new = review.prepare_round_plan(self.batch)['unit_bindings']
        self.assertEqual([uid for uid in old if old[uid] != new[uid]], ['group-6-7'])
        self.assert_only_target_pending('group-6-7')

    def test_explanation_and_knowledge_changes_are_not_metadata(self):
        old = review.prepare_round_plan(self.batch)['unit_bindings']
        final = self.load('06_batch/batch.json')
        final['items'][0]['explanation'] = '解析实际更改。'
        final['items'][1]['knowledge'] = ['主题/人与自我/生活与学习/学校生活']
        final['items'][2]['filterMetadata']['subtypes'] = ['阅读选择']
        final['items'][3]['title'] = '实际材料标题'
        self.write('06_batch/batch.json', final)
        new = review.prepare_round_plan(self.batch)['unit_bindings']
        self.assertEqual({uid for uid in old if old[uid] != new[uid]}, {'q-1', 'q-2', 'q-3', 'q-4'})

    def test_image_change_cannot_reuse_nonlistening_reviews(self):
        self.finish()
        image = self.batch / '01_source/image.png'
        image.write_bytes(b'new image')
        self.write('workflow/media-observations.json', {'observations': [dict(path='01_source/image.png',
            sha256=hashlib.sha256(image.read_bytes()).hexdigest(), description='图片证据')]})
        self.assertEqual(review.evaluate_rounds(self.batch)['checked_rounds'], [])

    def test_missing_response_or_modified_reused_check_cannot_pass(self):
        self.finish()
        self.write('01_source/metadata.json', {'year': 2026})
        review_rounds.export_task(self.batch)
        payload = self.load('07_review/review-rounds.json')
        bad = copy.deepcopy(payload)
        bad['rounds'][0]['checks'][0]['rationale'] = '伪造检查'
        self.assertNotIn('labels', review.evaluate_rounds(self.batch, bad)['checked_rounds'])
        ref = payload['rounds'][0]['reuse'][0]
        transaction = self.load('workflow/review-transactions/' + ref['task_id'] + '.json')
        origin = transaction['record']
        execution = origin.get('parts', [{'execution_id': origin['execution_id']}])[0]['execution_id']
        for path in (self.batch / 'workflow/review-results' / execution).glob('*.json'):
            path.unlink()
        self.assertNotIn('labels', review.evaluate_rounds(self.batch)['checked_rounds'])

    def test_forged_original_transaction_binding_or_checks_cannot_be_reused(self):
        self.finish()
        payload = self.load('07_review/review-rounds.json')
        record = next(row for row in payload['rounds'] if row['round_id'] == 'labels')
        task_id = record['parts'][-1]['execution_id']
        name = 'workflow/review-transactions/' + task_id + '.json'
        original = self.load(name)
        self.write('01_source/metadata.json', {'year': 2026})
        plan = review.prepare_round_plan(self.batch)
        ref = dict(unit_id=record['checks'][0]['unit_id'], number=None, task_id=task_id)
        for field, forged in [('input_binding', 'FORGED-ORIGIN-BINDING'), ('round_id', 'sample')]:
            with self.subTest(field=field):
                changed = copy.deepcopy(original)
                changed['record'][field] = forged
                self.write(name, changed)
                with self.assertRaises(ValueError):
                    review._reuse_source(self.batch, ref, 'labels', plan)
                result = review.evaluate_rounds(self.batch)
                self.assertNotIn('labels', result['checked_rounds'])
                self.assertNotIn('FORGED-ORIGIN-BINDING', json.dumps(result))
        changed = copy.deepcopy(original)
        changed['record']['checks'][0]['rationale'] = '篡改原始事务中的审核判断。'
        self.write(name, changed)
        with self.assertRaises(ValueError):
            review._reuse_source(self.batch, ref, 'labels', plan)
        self.assertNotIn('labels', review.evaluate_rounds(self.batch)['checked_rounds'])
        changed = copy.deepcopy(original)
        changed['task_id'] = 'round-' + 'f' * 32
        self.write(name, changed)
        with self.assertRaises(ValueError):
            review._reuse_source(self.batch, ref, 'labels', plan)

    def test_content_labeling_rule_change_invalidates_real_review_units(self):
        root = Path(review.__file__).resolve().parents[1]
        base = review_rounds.agent_tasks.build_task(self.batch, 'review', max_input_chars=1000000)
        supplied_rules = {Path(path).relative_to(root).as_posix() for path in base['input_hashes']
            if Path(path).is_absolute() and Path(path).is_relative_to(root)
            and Path(path).relative_to(root).parts[0] in {'docs', 'references', 'skills-reference'}}
        self.assertLessEqual(supplied_rules, set(review.ROUND_RULES))
        self.finish()
        original = review.stage_semantic_inputs(self.batch)
        rule = Path(review.__file__).resolve().parents[1] / 'docs/content-labeling.md'
        read_bytes = Path.read_bytes
        def changed(path):
            raw = read_bytes(path)
            return raw + b'\nChanged explanation/source comparison rule.\n' if path == rule else raw
        with patch.object(Path, 'read_bytes', new=changed):
            current = review.stage_semantic_inputs(self.batch)
            self.assertTrue(all(original[key] != current[key] for key in original if key.startswith('review-unit:')))
            self.assertEqual(review.evaluate_rounds(self.batch)['checked_rounds'], [])
            exported = review_rounds.export_task(self.batch)
            binding = self.load('workflow/review-bindings/' + exported['task_id'] + '.json')
            state = self.load(binding['state_path'])
            self.assertEqual(state['reuse'], [])
            self.assertEqual(sum(len(part['targets']) for part in state['parts']), len(self.units))

    def test_reused_real_finding_keeps_original_provenance_and_human_decision(self):
        original_response = self.response
        def with_finding(exported):
            response = original_response(exported)
            for check in response['artifacts']['review_round']['checks']:
                if exported['round_id'] == 'labels' and check['unit_id'] == 'q-3':
                    check.update(verdict='error', rationale='第3题答案与正文语义冲突。')
            return response
        self.response = with_finding
        self.finish()
        before = next(f for f in review.evaluate_rounds(self.batch)['findings'] if f['unit_id'] == 'q-3')
        item = next(row for row in self.load('07_review/manual-review-queue.json')['items'] if row['unit_id'] == 'q-3')
        review.resolve(self.batch, [item['finding_id']], action='accept', actor='test-reviewer', note='已按本题原文核对，接受现有结论。', force=True)
        self.write('01_source/metadata.json', {'year': 2026})
        self.assertEqual(review_rounds.export_task(self.batch)['action'], 'complete')
        result = review.evaluate_rounds(self.batch)
        after = next(f for f in result['findings'] if f['unit_id'] == 'q-3')
        self.assertEqual(before['round_sources'], after['round_sources'])
        self.assertTrue(any(f['unit_id'] == 'q-3' for f in result['resolved_findings']))

    def test_accepted_part_survives_another_unit_change_before_round_completes(self):
        first = review_rounds.export_task(self.batch, max_units_per_part=1)
        review_rounds.import_result(self.batch, first['task_id'], self.response(first))
        self.assertFalse((self.batch / '07_review/review-rounds.json').exists())
        final = self.load('06_batch/batch.json')
        final['items'][4]['answer'] = 'B'
        self.write('06_batch/batch.json', final)
        exported = review_rounds.export_task(self.batch, max_units_per_part=1)
        binding = self.load('workflow/review-bindings/' + exported['task_id'] + '.json')
        state = self.load(binding['state_path'])
        self.assertEqual(state['reuse'], [dict(unit_id='q-1', number=None, task_id=first['task_id'], part=True)])
        self.assertEqual([p['targets'][0] for p in state['parts']], [u['unit_id'] for u in self.units[1:]])
        self.finish()
        self.assertTrue(review.evaluate_rounds(self.batch)['execution_completed'])

    def test_explicit_fresh_review_does_not_recover_archived_results(self):
        self.finish()
        self.write('01_source/metadata.json', {'year': 2026})
        self.assertEqual(review_rounds.export_task(self.batch)['action'], 'complete')
        review_rounds.restart_rounds(self.batch)
        exported = review_rounds.export_task(self.batch, max_units_per_part=2)
        state = self.load(self.load('workflow/review-bindings/' + exported['task_id'] + '.json')['state_path'])
        self.assertEqual(state['reuse'], [])
        self.assertEqual(sum(len(p['targets']) for p in state['parts']), len(self.units))

    def test_legacy_without_unit_bindings_requires_real_review(self):
        self.finish()
        payload = self.load('07_review/review-rounds.json')
        payload['plan'].pop('unit_bindings')
        self.write('07_review/review-rounds.json', payload)
        self.write('01_source/metadata.json', {'year': 2026})
        self.assertEqual(review.evaluate_rounds(self.batch)['checked_rounds'], [])


if __name__ == '__main__':
    unittest.main()
