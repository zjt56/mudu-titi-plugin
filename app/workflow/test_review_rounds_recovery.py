"""Isolated recovery/path regressions; all model invocations are mocked."""
import hashlib
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from workflow import review, review_rounds
from workflow.local_agent import AgentError


class ReviewRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.batch = self.root / 'batch'
        self.batch.mkdir()
        self.source = '\n'.join(
            f'{n}. Question {n}?\nA. a{n} B. b{n} C. c{n} D. d{n}' for n in range(1, 4))
        self.write('01_source/正文.txt', self.source)
        self.write('01_source/structure.confirmed.json', {'units': [
            {'unit_id': f'u{n}', 'unit_kind': 'standalone', 'numbers': [n],
             'stem': f'{n}. Question {n}?'} for n in range(1, 4)]})
        self.write('03_types/types.json', {'items': [
            {'unit_id': f'u{n}', 'type': '阅读理解', 'subtypes': ['阅读判断'], 'evidence': ['L1']}
            for n in range(1, 4)]})
        self.write('04_knowledge/knowledge.json', {'items': [
            {'unit_id': f'u{n}', 'knowledge': ['语篇体裁/记叙文/故事', '主题/人与自我/生活与学习/日常起居'],
             'subquestions': []} for n in range(1, 4)]})
        self.write('04_knowledge/explanations.json', {'items': [
            {'unit_id': f'u{n}', 'explanation': '无', 'explanation_origin': 'none'}
            for n in range(1, 4)]})
        self.write('01_source_ans/answers.json', {
            'answers': {str(n): 'A' for n in range(1, 4)},
            'difficulty': {str(n): '容易' for n in range(1, 4)}})
        self.write('06_batch/batch.json', {'items': []})
        # Any accidental real model call fails this entire suite.
        self.no_model = patch.object(review_rounds, 'invoke', side_effect=AssertionError('real model forbidden'))
        self.no_model.start()
        self.addCleanup(self.no_model.stop)

    def write(self, relative, value):
        path = self.batch / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding='utf-8')
        return path

    def task(self, exported):
        return json.loads((Path(exported['package_dir']) / 'task.json').read_text())

    def response(self, prompt):
        rid = re.search(r'ROUND: (\w+)', prompt).group(1)
        targets = json.loads(re.search(r'TARGETS: (\[.*?\])\n', prompt).group(1))
        checks = []
        for target in targets:
            uid = target['unit_id'] if isinstance(target, dict) else target
            number = uid[1:]
            assessment = {'type': '阅读理解', 'subtypes': ['阅读判断'],
                          'knowledge': ['语篇体裁/记叙文/故事', '主题/人与自我/生活与学习/日常起居'],
                          'subquestions': [], 'difficulty': [{'number': number, 'value': '容易'}]}
            if rid == 'sample':
                assessment['source_comparison'] = '核对原文题干和答案。'
            checks.append(dict(target if isinstance(target, dict) else {'unit_id': uid},
                               verdict='ok', rationale='原文内容支持题目中的细节判断。',
                               evidence=[{'path': '01_source/正文.txt', 'locator': 'L1',
                                          'quote': f'Question {number}?'}], assessment=assessment))
        return {'status': 'completed', 'artifacts': {'review_round': {'checks': checks}},
                'notes': [], 'findings': []}

    def snapshot(self, prefixes):
        return {str(p.relative_to(self.batch)): p.read_bytes()
                for prefix in prefixes for p in (self.batch / prefix).rglob('*') if p.is_file()}

    def test_attempt_names_are_validated_before_creating_any_directory(self):
        for name in ('../escape', '/tmp/escape', '..', '.', 'x/y', 'x\\y', 'x:y', '', None, 123):
            with self.subTest(name=name), self.assertRaises(ValueError):
                review_rounds._attempt_dir(self.batch, name)
        self.assertFalse((self.batch / 'workflow').exists())
        first = review_rounds._attempt_dir(self.batch, 'round-' + 'a' * 32)
        second = review_rounds._attempt_dir(self.batch, 'round-' + 'a' * 32)
        self.assertTrue(first.is_dir())
        self.assertNotEqual(first, second)
        self.assertTrue(first.resolve().is_relative_to(self.batch.resolve()))

    def test_attempt_directory_rejects_symlink_ancestors_inside_and_outside(self):
        for component in ('workflow', 'workflow/review-attempts'):
            for destination in ('outside', 'inside'):
                with self.subTest(component=component, destination=destination):
                    batch = self.root / (component.replace('/', '-') + '-' + destination)
                    batch.mkdir()
                    target = (self.root / ('target-' + batch.name) if destination == 'outside'
                              else batch / 'target')
                    target.mkdir()
                    link = batch / component
                    link.parent.mkdir(parents=True, exist_ok=True)
                    link.symlink_to(target, target_is_directory=True)
                    with self.assertRaises(ValueError):
                        review_rounds._attempt_dir(batch, 'round-safe')
                    self.assertEqual(list(target.iterdir()), [])

    def test_attempt_collision_cannot_follow_a_symlink(self):
        attempts = self.batch / 'workflow/review-attempts'
        attempts.mkdir(parents=True)
        outside = self.root / 'outside'
        outside.mkdir()
        fixed = type('FixedUUID', (), {'hex': 'c' * 32})()
        (attempts / ('round-safe-' + fixed.hex)).symlink_to(outside, target_is_directory=True)
        with patch.object(review_rounds.uuid, 'uuid4', return_value=fixed), self.assertRaises(ValueError):
            review_rounds._attempt_dir(self.batch, 'round-safe')
        self.assertEqual(list(outside.iterdir()), [])

    def test_quarantine_preserves_all_historical_evidence_and_exact_buffers(self):
        exported = review_rounds.export_task(self.batch)
        tid = exported['task_id']
        response = self.response(self.task(exported)['prompt'])
        with patch.object(review_rounds, '_finish_install', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                review_rounds.import_result(self.batch, tid, response)
        review_rounds._save_pending(self.batch, tid, response)
        partial = self.write('workflow/review-partial/' + tid + '.json', {'failed': ['old']})
        buffers = {'pending': (self.batch / review_rounds._pending_path(tid)).read_bytes(),
                   'partial': partial.read_bytes()}
        evidence = self.snapshot(['workflow/review-bindings', 'workflow/review-transactions',
                                  'workflow/review-partitions', 'workflow/review-results', 'workflow/review-tasks'])
        self.write('01_source/正文.txt', self.source + '\nChanged input.')
        self.assertEqual(review_rounds._purge_stale_pending(self.batch, review.prepare_round_plan(self.batch)), 2)
        for kind, data in buffers.items():
            archives = list((self.batch / 'workflow/review-stale').glob('*/' + kind + '/' + tid + '.json'))
            self.assertEqual(len(archives), 1)
            self.assertEqual(archives[0].read_bytes(), data)
            self.assertFalse((self.batch / ('workflow/review-' + kind) / (tid + '.json')).exists())
        for relative, data in evidence.items():
            self.assertEqual((self.batch / relative).read_bytes(), data, relative)
        with self.assertRaises(review_rounds.ReviewInputsChanged):
            review_rounds.import_result(self.batch, tid, response)
        fresh = review_rounds.export_task(self.batch)
        self.assertNotEqual(fresh['task_id'], tid)
        self.assertFalse((self.batch / ('workflow/review-bindings/' + tid + '.receipt.json')).exists())
        self.assertFalse(review.evaluate_rounds(self.batch)['execution_completed'])

    def test_same_plan_changed_task_hash_preserves_stale_transaction_without_replay(self):
        extra = self.write('extra-bound-input.txt', 'original input')
        original_build = review_rounds.agent_tasks.build_task
        def bound_task(*args, **kwargs):
            task = original_build(*args, **kwargs)
            task['input_hashes']['extra-bound-input.txt'] = hashlib.sha256(extra.read_bytes()).hexdigest()
            return task
        with patch.object(review_rounds.agent_tasks, 'build_task', side_effect=bound_task):
            exported = review_rounds.export_task(self.batch)
            tid = exported['task_id']
            response = self.response(self.task(exported)['prompt'])
            with patch.object(review_rounds, '_finish_install', side_effect=OSError('interrupted')):
                with self.assertRaises(OSError):
                    review_rounds.import_result(self.batch, tid, response)
            review_rounds._save_pending(self.batch, tid, response)
            evidence = self.snapshot(['workflow/review-bindings', 'workflow/review-transactions'])
            plan = review.prepare_round_plan(self.batch)
            extra.write_text('changed input')
            self.assertEqual(review.prepare_round_plan(self.batch), plan)
            self.assertEqual(review_rounds._purge_stale_pending(self.batch, plan), 1)
            with patch.object(review_rounds, '_finish_install', side_effect=AssertionError('stale replay forbidden')):
                fresh = review_rounds.export_task(self.batch)
            self.assertNotEqual(fresh['task_id'], tid)
            for relative, data in evidence.items():
                self.assertEqual((self.batch / relative).read_bytes(), data)
            with self.assertRaises(review_rounds.ReviewInputsChanged):
                review_rounds.import_result(self.batch, tid, response)

    def test_current_buffers_stay_active_and_orphan_partial_is_archived(self):
        exported = review_rounds.export_task(self.batch)
        tid = exported['task_id']
        review_rounds._save_pending(self.batch, tid, {'current': True})
        self.write('workflow/review-partial/' + tid + '.json', {'current': True})
        orphan = 'round-' + 'f' * 32
        path = self.write('workflow/review-partial/' + orphan + '.json', 'orphan exact bytes\n')
        data = path.read_bytes()
        self.assertEqual(review_rounds._purge_stale_pending(self.batch, review.prepare_round_plan(self.batch)), 1)
        self.assertEqual(review_rounds._load_pending(self.batch, tid), {'current': True})
        self.assertTrue((self.batch / ('workflow/review-partial/' + tid + '.json')).exists())
        archive = list((self.batch / 'workflow/review-stale').glob('*/partial/' + orphan + '.json'))
        self.assertEqual(archive[0].read_bytes(), data)

    def test_quarantine_copy_failure_leaves_active_buffer_and_binding_intact(self):
        exported = review_rounds.export_task(self.batch)
        tid = exported['task_id']
        review_rounds._save_pending(self.batch, tid, {'old': True})
        binding = self.batch / ('workflow/review-bindings/' + tid + '.json')
        original = binding.read_bytes()
        self.write('01_source/正文.txt', self.source + '\nChanged.')
        with patch.object(review_rounds, '_atomic', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                review_rounds._purge_stale_pending(self.batch, review.prepare_round_plan(self.batch))
        self.assertEqual(review_rounds._load_pending(self.batch, tid), {'old': True})
        self.assertEqual(binding.read_bytes(), original)

    def test_serial_and_parallel_honor_both_partition_configuration_keys(self):
        for key in ('review_max_units_per_part', 'review_part_units'):
            for parallel in (1, 3):
                with self.subTest(key=key, parallel=parallel):
                    # Independent fixture per configuration prevents state reuse
                    # from masking the actual partition option passed by run.
                    batch = self.batch
                    self.batch = self.root / (key + str(parallel))
                    self.batch.mkdir()
                    for path in batch.rglob('*'):
                        if path.is_file() and 'workflow' not in path.relative_to(batch).parts:
                            target = self.batch / path.relative_to(batch)
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(path.read_bytes())
                    targets_seen = []
                    def fixture(prompt, *args, **kwargs):
                        targets = json.loads(re.search(r'TARGETS: (\[.*?\])\n', prompt).group(1))
                        targets_seen.append(targets)
                        return {'response': self.response(prompt)}
                    with patch.object(review_rounds, 'invoke', side_effect=fixture):
                        result = review_rounds.run(self.batch, config={key: 1, 'review_part_parallel': parallel})
                    self.assertTrue(result['completed'], result)
                    self.assertEqual(len(targets_seen), 4)
                    self.assertTrue(all(len(targets) == 1 for targets in targets_seen))
                    self.batch = batch

    def test_serial_reuses_pending_response_without_a_model_call(self):
        exports = [review_rounds.export_group(self.batch, only_round=rid, max_units_per_part=1)
                   for rid in review.ROUND_IDS]
        for group in exports:
            for part in group['parts']:
                review_rounds._save_pending(self.batch, part['task_id'], self.response(self.task(part)['prompt']))
        result = review_rounds.run(self.batch, config={'review_part_units': 1})
        self.assertTrue(result['completed'], result)
        self.assertEqual(list((self.batch / 'workflow/review-pending').glob('*.json')), [])

    def test_changed_input_before_dispatch_never_calls_model_in_either_mode(self):
        for parallel in (1, 3):
            with self.subTest(parallel=parallel):
                original = review_rounds.export_task if parallel == 1 else review_rounds.export_group
                def changed(*args, **kwargs):
                    exported = original(*args, **kwargs)
                    self.write('01_source/正文.txt', (self.batch / '01_source/正文.txt').read_text() + '\nChanged.')
                    return exported
                target = 'export_task' if parallel == 1 else 'export_group'
                with patch.object(review_rounds, target, side_effect=changed):
                    result = review_rounds.run(self.batch, config={'review_part_parallel': parallel,
                                                                  'review_max_attempts': 5})
                self.assertEqual(result['action'], 'blocked')
                self.assertTrue(result['inputs_changed'])
                self.assertTrue(result['resumable'])
                self.assertEqual(result['resume_action'], 'run')
                self.assertFalse((self.batch / 'workflow/review-attempts').exists())

    def test_changed_input_after_response_does_not_spend_retry_and_next_run_resumes(self):
        calls = []
        def changed(prompt, *args, **kwargs):
            calls.append(prompt)
            self.write('01_source/正文.txt', self.source + '\nChanged after call.')
            return {'response': self.response(prompt)}
        with patch.object(review_rounds, 'invoke', side_effect=changed):
            result = review_rounds.run(self.batch, config={'review_max_attempts': 5})
        self.assertTrue(result['inputs_changed'], result)
        self.assertEqual(len(calls), 1)
        pending = list((self.batch / 'workflow/review-pending').glob('*.json'))
        self.assertEqual(len(pending), 1)
        tid = pending[0].stem
        original_binding = (self.batch / ('workflow/review-bindings/' + tid + '.json')).read_bytes()
        with patch.object(review_rounds, 'invoke', side_effect=lambda prompt, *a, **k: {'response': self.response(prompt)}):
            resumed = review_rounds.run(self.batch, config={'review_part_units': 1})
        self.assertTrue(resumed['completed'], resumed)
        self.assertEqual((self.batch / ('workflow/review-bindings/' + tid + '.json')).read_bytes(), original_binding)
        self.assertTrue(list((self.batch / 'workflow/review-stale').glob('*/pending/' + tid + '.json')))
        self.assertFalse((self.batch / ('workflow/review-bindings/' + tid + '.receipt.json')).exists())

    def test_parallel_change_stops_queued_work_without_retries_and_keeps_response(self):
        group = review_rounds.export_group(self.batch, max_units_per_part=1)
        calls = []
        def changed(prompt, *args, **kwargs):
            calls.append(prompt)
            self.write('01_source/正文.txt', self.source + '\nChanged in first worker.')
            return {'response': self.response(prompt)}
        with patch.object(review_rounds, 'invoke', side_effect=changed):
            with self.assertRaises(review_rounds.ReviewInputsChanged):
                review_rounds._run_parts_parallel(self.batch, group['parts'], {}, workers=1, retries=5)
        self.assertEqual(len(calls), 1)
        self.assertIsNotNone(review_rounds._load_pending(self.batch, group['parts'][0]['task_id']))
        self.assertFalse((self.batch / 'workflow/review-failures').exists())

    def partition_data(self, context, chosen):
        base = {'prompt': 'BEGIN SUPPLIED CONTENT (JSON data)\n'
                          + json.dumps(context, ensure_ascii=False) + 'END SUPPLIED CONTENT'}
        parts = review_rounds._partition_contexts(base, 1000000, round_id='labels', target_unit_ids=chosen)
        return json.loads(parts[0]['context'].split('\n', 1)[1])

    def test_partition_source_excerpts_keep_cross_question_material_and_raw_markup(self):
        source = 'Paper heading\r\n'
        units = []
        for index, letter in enumerate(('A', 'B', 'C')):
            start = len(source.splitlines()) + 1
            first, second = 2 * index + 1, 2 * index + 2
            padding = ' Unselected passage detail.' * 200 if letter == 'B' else ''
            raw = (letter + '\r\nShared passage ' + letter + ' supports BOTH questions.' + padding + '\r\n'
                   '[IMG passage-' + letter + '.png]\r\n[TABLE]\r\ncell-' + letter + '\r\n[/TABLE]\r\n'
                   + f'{first}. Question {first}?\r\nA. a B. b C. c D. d\r\n'
                   + f'{second}. Question {second}?\r\nA. a B. b C. c D. d\r\n')
            source += raw
            units.append({'unit_id': letter, 'unit_kind': 'group', 'numbers': [first, second],
                          'stem': '\n'.join(raw.splitlines()[1:]),
                          'source_span': {'start_line': start, 'end_line': len(source.splitlines()),
                                          'basis': 'cleaned_source'}})
        appendix = 'Answer booklet with full explanations, ownership not proven.'
        catalog = {'points': [{'id': 'untouched', 'name': 'complete-catalog'}]}
        extraction = {'tables': ['all original tables'], 'media': ['all original media'], 'warnings': ['keep warning']}
        context = {'confirmed_units_with_stable_ids': units, '01_source/正文.txt': source,
                   '01_source/附录.txt': appendix, '01_source_ans/正文.txt': appendix,
                   'references/knowledge-catalog.json': catalog, '01_source/extraction.json': extraction}
        original = json.dumps(context, ensure_ascii=False)
        data = self.partition_data(context, {'A', 'C'})
        excerpt = data['01_source/正文.txt']
        self.assertTrue(excerpt['excerpted'])
        self.assertEqual(len(excerpt['spans']), 2)
        for span in excerpt['spans']:
            self.assertIn(span['text'], source)
            self.assertEqual(span['text'], ''.join(source.splitlines(keepends=True)[
                span['start_line'] - 1:span['end_line']]))
        rendered = json.dumps(excerpt, ensure_ascii=False)
        self.assertLess(len(rendered), len(source))
        self.assertIn('Shared passage A supports BOTH questions.', rendered)
        self.assertIn('Shared passage C supports BOTH questions.', rendered)
        self.assertNotIn('Shared passage B', rendered)
        self.assertIn('passage-A.png', rendered)
        self.assertIn('cell-A', rendered)
        self.assertIn('2. Question 2?', rendered)
        self.assertIn('省略', excerpt['omitted'])
        self.assertEqual(data['01_source/附录.txt'], appendix)
        self.assertEqual(data['01_source_ans/正文.txt'], appendix)
        self.assertEqual(data['references/knowledge-catalog.json'], catalog)
        self.assertEqual(data['01_source/extraction.json'], extraction)
        self.assertEqual(json.dumps(context, ensure_ascii=False), original)
        without_spans = [dict(unit, source_span=None) for unit in units]
        # The same full stems also locate uniquely when legacy confirmed units
        # lack line spans; source A/B labels stay in the returned raw slices.
        stem_only = review_rounds._source_excerpts(source, [without_spans[0], without_spans[2]])
        self.assertTrue(stem_only['excerpted'])
        self.assertEqual(stem_only['spans'][0]['text'], excerpt['spans'][0]['text'])
        self.assertEqual(stem_only['spans'][1]['text'], excerpt['spans'][1]['text'])

    def test_unknown_ambiguous_partial_and_stale_source_locations_keep_full_source(self):
        cases = [
            ('1. Known?\n2. Other?', {'unit_id': 'u1', 'unit_kind': 'standalone', 'numbers': [1], 'stem': 'missing text'}),
            ('Repeated paragraph\nRepeated paragraph\n', {'unit_id': 'u1', 'numbers': [1], 'stem': 'Repeated paragraph'}),
            ('1. Known?\nA. a B. b C. c D. d\n2. Other?',
             {'unit_id': 'u1', 'unit_kind': 'standalone', 'numbers': [1], 'stem': '1. Known?'}),
            ('Shared passage must survive\n1. Known?\n2. Other?',
             {'unit_id': 'u1', 'unit_kind': 'group', 'numbers': [1], 'stem': '1. Known?'}),
            ('1. Known?\n2. Other?', {'unit_id': 'u1', 'numbers': [1], 'stem': '1. Known?',
                                    'source_span': {'start_line': 2, 'end_line': 9}}),
            ('1. Known?\n[TABLE]\ncell\n[/TABLE]\n2. Other?',
             {'unit_id': 'u1', 'numbers': [1], 'stem': '1. Known?\n[TABLE]\ncell',
              'source_span': {'start_line': 1, 'end_line': 3}}),
            ('1. Known?\n2. Other?', {'unit_id': 'u1', 'numbers': [1], 'stem': '1. Known?',
                                    'source_span': {'start_line': 1, 'end_line': 1, 'basis': 'original_pdf'}}),
            ('1. Known?\n2. Other?', {'unit_id': 'u1', 'numbers': [1], 'stem': '1. Known?',
                                    'source_span': {'start_line': 1, 'end_line': 1, 'source_path': 'other.txt'}}),
        ]
        for source, unit in cases:
            source += '\n9. unrelated?\n' + 'Unrelated source detail. ' * 300
            with self.subTest(source=source[:100], unit=unit):
                self.assertEqual(review_rounds._source_excerpts(source, [unit]), source)

    def test_clipped_prompt_keeps_original_input_hashes_and_full_source_quote_validation(self):
        lines = self.source.splitlines()
        lines[-1] += ' Non-target original detail.' * 200
        self.write('01_source/正文.txt', '\n'.join(lines))
        self.write('01_source/structure.confirmed.json', {'units': [
            {'unit_id': f'u{n}', 'unit_kind': 'standalone', 'numbers': [n],
             'stem': '\n'.join(lines[2 * (n - 1):2 * n]),
             'source_span': {'start_line': 2 * n - 1, 'end_line': 2 * n, 'basis': 'cleaned_source'}}
            for n in range(1, 4)]})
        before = self.snapshot(['01_source', '01_source_ans'])
        plan = review.prepare_round_plan(self.batch)
        exported = review_rounds.export_task(self.batch, max_units_per_part=1)
        task = self.task(exported)
        context = json.loads(task['prompt'].split('BEGIN SUPPLIED CONTENT', 1)[1]
                             .rsplit('END SUPPLIED CONTENT', 1)[0].split('\n', 1)[1])
        self.assertTrue(context['01_source/正文.txt']['excerpted'])
        self.assertNotIn('Question 2?', json.dumps(context['01_source/正文.txt']))
        binding = json.loads((self.batch / ('workflow/review-bindings/' + exported['task_id'] + '.json')).read_text())
        digest = hashlib.sha256((self.batch / '01_source/正文.txt').read_bytes()).hexdigest()
        self.assertEqual(binding['input_hashes']['01_source/正文.txt'], digest)
        result = review_rounds.import_result(self.batch, exported['task_id'], self.response(task['prompt']))
        self.assertEqual(result['part_completed'], 1)
        self.assertEqual(review.prepare_round_plan(self.batch), plan)
        for relative, data in before.items():
            self.assertEqual((self.batch / relative).read_bytes(), data)

    def test_transcript_clipping_uses_verified_windows_and_preserves_complete_segments(self):
        segments = [{'start': 0, 'end': 20, 'text': 'unrelated first dialogue'},
                    {'start': 90, 'end': 130, 'text': 'question AND complete response crossing the window'},
                    {'start': 200, 'end': 230, 'text': 'unrelated last dialogue'}]
        words = [{'start': 10, 'end': 11, 'word': 'outside'},
                 {'start': 94, 'end': 95, 'word': 'question'},
                 {'start': 125, 'end': 126, 'word': 'response'},
                 {'start': 210, 'end': 211, 'word': 'outside'}]
        transcript = {'schema_version': 2, 'language': 'en', 'segments': segments, 'words': words,
                      'text': ' '.join(row['text'] for row in segments)}
        evidence = {'items': [{'unit_id': 'u1', 'start': 100, 'end': 120, 'verified': True}]}
        original = json.dumps(transcript)
        data = self.partition_data({'confirmed_units_with_stable_ids': [
            {'unit_id': 'u1', 'numbers': [1], 'stem': 'unknown'}],
            '02_audio/full-transcript.json': transcript, '02_audio/segment-evidence.json': evidence}, {'u1'})
        cropped = data['02_audio/full-transcript.json']
        self.assertEqual(cropped['segments'], [segments[1]])
        self.assertEqual(cropped['words'], words[1:3])
        self.assertNotIn('text', cropped)
        self.assertEqual(cropped['review_excerpt']['verified_windows'], [[100, 120]])
        self.assertEqual(json.dumps(transcript), original)

    def test_transcript_without_complete_verified_timing_keeps_everything(self):
        response = 'Complete response evidence. ' * 200
        transcript = {'segments': [{'start': 0, 'end': 5, 'text': 'first'},
                                   {'start': 10, 'end': 20, 'text': response}],
                      'text': 'first ' + response.strip()}
        valid = {'unit_id': 'u1', 'start': 0, 'end': 5, 'verified': True}
        cases = [(transcript, None, {'u1'}),
                 (transcript, {'items': [dict(valid, verified=False)]}, {'u1'}),
                 (transcript, {'items': [valid]}, {'u1', 'u2'}),
                 (transcript, {'items': [dict(valid, end=float('nan'))]}, {'u1'}),
                 (transcript, {'items': [dict(valid, end=10 ** 400)]}, {'u1'}),
                 (transcript, {'items': [dict(valid, start=True)]}, {'u1'}),
                 (transcript, {'items': [dict(valid, start='0')]}, {'u1'}),
                 (dict(transcript, text='additional untimed evidence' * 200), {'items': [valid]}, {'u1'}),
                 (dict(transcript, words=[{'word': 'missing timestamp'}]), {'items': [valid]}, {'u1'}),
                 ({'segments': [{'start': 0, 'end': 5, 'text': 'first'}, {'text': 'unknown timed response'}]},
                  {'items': [valid]}, {'u1'})]
        for document, evidence, chosen in cases:
            with self.subTest(document=document, evidence=evidence, chosen=chosen):
                self.assertIs(review_rounds._transcript_excerpt(document, evidence, chosen), document)

    def test_serial_validation_text_containing_limit_tokens_still_retries_normally(self):
        calls = []
        message = 'Evidence quote 429 quota rate_limit daily_limit usage limit absent from source'
        def fixture(prompt, *args, **kwargs):
            calls.append(prompt)
            if len(calls) == 1:
                raise ValueError(message)
            return {'response': self.response(prompt)}
        with patch.object(review_rounds, 'invoke', side_effect=fixture), patch.object(review_rounds._time, 'sleep'):
            result = review_rounds.run(self.batch, config={'review_max_attempts': 2})
        self.assertTrue(result['completed'], result)
        self.assertNotIn('quota', result)
        self.assertEqual(len(calls), 3)  # failed labels attempt, good labels, good sample
        self.assertIn(message, calls[1])
        failure = json.loads(next((self.batch / 'workflow/review-failures').glob('*.json')).read_text())
        self.assertEqual(failure['error'], message)
        self.assertIsNone(failure['error_code'])

    def test_parallel_non_limit_errors_keep_text_and_codes_separate_and_allow_retry(self):
        part = review_rounds.export_group(self.batch, max_units_per_part=1)['parts'][0]
        message = 'Bound source quote includes 429 quota rate_limit daily_limit usage limit'
        for exception in (ValueError(message), AgentError(message, 'protocol')):
            with self.subTest(exception=exception):
                calls = []
                def fixture(prompt, *args, **kwargs):
                    calls.append(prompt)
                    if len(calls) == 1:
                        raise exception
                    return {'response': self.response(prompt)}
                with patch.object(review_rounds, 'invoke', side_effect=fixture), patch.object(review_rounds._time, 'sleep'):
                    outcomes, quota = review_rounds._run_parts_parallel(self.batch, [part], {}, workers=1, retries=2)
                self.assertFalse(quota)
                self.assertEqual(outcomes, {})
                self.assertEqual(outcomes.error_codes, {})
                self.assertEqual(len(calls), 2)
                review_rounds._clear_pending(self.batch, part['task_id'])
        # Exhaustion must retain the original structured non-limit code rather
        # than replacing the exception with an untyped RuntimeError.
        with patch.object(review_rounds, 'invoke', side_effect=AgentError(message, 'protocol')) as model, \
                patch.object(review_rounds._time, 'sleep'):
            outcomes, quota = review_rounds._run_parts_parallel(self.batch, [part], {}, workers=1, retries=2)
        self.assertFalse(quota)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(outcomes, {part['task_id']: message})
        self.assertEqual(outcomes.error_codes, {part['task_id']: 'protocol'})
        self.assertEqual(json.loads(json.dumps(outcomes)), {part['task_id']: message})
        failure = json.loads((self.batch / ('workflow/review-failures/' + part['task_id'] + '.json')).read_text())
        self.assertEqual(failure['error'], message)
        self.assertEqual(failure['error_code'], 'protocol')

    def test_structured_limits_stop_serial_before_retry_or_next_round(self):
        for code in ('rate_limit', 'quota'):
            with self.subTest(code=code):
                # The message deliberately contains no token used by the old
                # substring classifier; only the exception code is authority.
                with patch.object(review_rounds, 'invoke', side_effect=AgentError('Provider refused request', code)) as model:
                    result = review_rounds.run(self.batch, config={'review_max_attempts': 5})
                self.assertTrue(result['quota'], result)
                self.assertFalse(result['completed'])
                self.assertEqual(model.call_count, 1)
                failure = json.loads(next((self.batch / 'workflow/review-failures').glob('*.json')).read_text())
                self.assertEqual(failure['error'], 'Provider refused request')
                self.assertEqual(failure['error_code'], code)

    def test_structured_parallel_limits_stop_queued_calls_and_keep_legacy_outcomes(self):
        parts = review_rounds.export_group(self.batch, max_units_per_part=1)['parts']
        for code in ('rate_limit', 'quota'):
            with self.subTest(code=code):
                with patch.object(review_rounds, 'invoke', side_effect=AgentError('Provider refused request', code)) as model:
                    outcomes, quota = review_rounds._run_parts_parallel(self.batch, parts, {}, workers=1, retries=5)
                self.assertTrue(quota)
                self.assertEqual(model.call_count, 1)
                self.assertIsInstance(outcomes, dict)
                self.assertEqual(outcomes[parts[0]['task_id']], 'Provider refused request')
                self.assertEqual(outcomes.error_codes[parts[0]['task_id']], code)
                self.assertTrue(all(isinstance(reason, str) for reason in outcomes.values()))
                self.assertTrue(all(outcomes.error_codes[p['task_id']] is None for p in parts[1:]))

    def test_parallel_installation_error_with_limit_words_is_not_a_limit(self):
        original_import = review_rounds.import_result
        message = 'Validation quote 429 quota rate_limit absent from source'
        def checked_import(batch, tid, response):
            binding = json.loads((Path(batch) / ('workflow/review-bindings/' + tid + '.json')).read_text())
            if binding['task']['round_id'] == 'labels':
                raise ValueError(message)
            return original_import(batch, tid, response)
        with patch.object(review_rounds, 'invoke', side_effect=lambda prompt, *a, **k: {'response': self.response(prompt)}) as model, \
                patch.object(review_rounds, 'import_result', side_effect=checked_import):
            result = review_rounds.run(self.batch, config={'review_part_parallel': 2, 'review_max_attempts': 3})
        self.assertFalse(result['completed'])
        self.assertNotIn('quota', result)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(review.evaluate_rounds(self.batch)['checked_rounds'], ['sample'])
        failure = result['summary']['execution_failures']['labels']
        self.assertEqual(failure['reason'], message)
        self.assertIsNone(failure['error_code'])

    def test_structured_context_change_takes_priority_over_limit_words_without_retry(self):
        error = AgentError('429 quota rate_limit text with changed input', 'context_changed')
        with patch.object(review_rounds, 'invoke', side_effect=error) as model:
            result = review_rounds.run(self.batch, config={'review_max_attempts': 5})
        self.assertTrue(result['inputs_changed'])
        self.assertTrue(result['resumable'])
        self.assertNotIn('quota', result)
        self.assertEqual(model.call_count, 1)
        part = review_rounds.export_group(self.batch, max_units_per_part=1)['parts'][0]
        with patch.object(review_rounds, 'invoke', side_effect=error) as model:
            with self.assertRaises(review_rounds.ReviewInputsChanged):
                review_rounds._run_parts_parallel(self.batch, [part], {}, workers=1, retries=5)
        self.assertEqual(model.call_count, 1)
        self.assertFalse((self.batch / 'workflow/review-failures').exists())

    def repair_fixture(self):
        exported = review_rounds.export_task(self.batch)
        task = self.task(exported)
        bad = self.response(task['prompt'])
        bad['artifacts']['review_round']['checks'][1]['evidence'][0]['quote'] = 'not a real source quotation'
        with self.assertRaises(review.RoundCheckError):
            review_rounds.import_result(self.batch, exported['task_id'], bad)
        partial = json.loads((self.batch / ('workflow/review-partial/' + exported['task_id'] + '.json')).read_text())
        return exported, task, partial

    def test_repair_reuses_exact_blind_context_catalog_rules_and_only_failed_targets(self):
        self.write('06_batch/batch_meta.json', {'items': [
            {'unit_id': f'u{n}', 'question_type': 'SECRET_ORIGINAL_TYPE',
             'knowledge': ['SECRET_ORIGINAL_KNOWLEDGE'], 'difficulty': 'SECRET_ORIGINAL_DIFFICULTY',
             'filterMetadata': {'subtypes': ['SECRET_ORIGINAL_SUBTYPE']}} for n in range(1, 4)]})
        self.write('01_source/附录.txt', 'Complete appendix evidence must remain available.')
        self.write('02_audio/full-transcript.json', {'text': 'Complete original dialogue and response.'})
        exported, task, partial = self.repair_fixture()
        original = task['prompt']
        evidence = self.snapshot(['workflow/review-bindings', 'workflow/review-tasks', 'workflow/review-partitions'])
        seen = []
        def fixture(prompt, *args, **kwargs):
            seen.append(prompt)
            self.assertIn('references/knowledge-catalog.json', prompt)
            self.assertNotIn('SECRET_ORIGINAL_', prompt)
            self.assertEqual(prompt.split('\nBEGIN SUPPLIED CONTENT', 1)[1].encode(),
                             original.split('\nBEGIN SUPPLIED CONTENT', 1)[1].encode())
            self.assertEqual(json.loads(re.search(r'TARGETS: (\[.*?\])\n', prompt).group(1)), ['u2'])
            self.assertIn(partial['failed'][0]['reason'], prompt)
            self.assertIn('Complete appendix evidence must remain available.', prompt)
            self.assertIn('Complete original dialogue and response.', prompt)
            return {'response': self.response(prompt)}
        with patch.object(review_rounds, 'invoke', side_effect=fixture), \
                patch.object(review_rounds, '_round_prompt', side_effect=AssertionError('repair must not rebuild prompt')):
            result = review_rounds._repair_round(self.batch, exported['task_id'], {})
        self.assertEqual(result['round_completed'], 'labels')
        self.assertEqual(len(seen), 1)
        for relative, data in evidence.items():
            self.assertEqual((self.batch / relative).read_bytes(), data, relative)
        with self.assertRaisesRegex(ValueError, 'already consumed'):
            review_rounds._repair_round(self.batch, exported['task_id'], {})

    def test_repair_rejects_changed_package_frozen_state_and_unbound_target(self):
        exported, task, partial = self.repair_fixture()
        plan = review.prepare_round_plan(self.batch)
        package = Path(exported['package_dir']) / 'task.json'
        original_package = package.read_bytes()
        changed = dict(task, prompt=task['prompt'] + '\nINJECTED PROMPT')
        package.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, 'exported task'):
            review_rounds._repair_context(self.batch, partial, plan)
        package.write_bytes(original_package)
        outside = dict(partial, failed=[{'unit_id': 'outside', 'number': None, 'reason': 'invalid quote'}])
        with self.assertRaisesRegex(ValueError, 'outside the bound task'):
            review_rounds._repair_context(self.batch, outside, plan)
        binding = json.loads((self.batch / ('workflow/review-bindings/' + exported['task_id'] + '.json')).read_text())
        state_path = self.batch / binding['state_path']
        state = json.loads(state_path.read_text())
        state['parts'][0]['prompt'] += '\nINJECTED STATE'
        state_path.write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError, 'frozen task'):
            review_rounds._repair_context(self.batch, partial, plan)

    def test_repair_uses_frozen_prompt_if_package_lost_and_rejects_extra_checks(self):
        exported, task, partial = self.repair_fixture()
        (Path(exported['package_dir']) / 'task.json').unlink()
        prompt, targets = review_rounds._repair_context(self.batch, partial, review.prepare_round_plan(self.batch))
        self.assertEqual(targets, ['u2'])
        self.assertEqual(prompt.split('\nBEGIN SUPPLIED CONTENT', 1)[1],
                         task['prompt'].split('\nBEGIN SUPPLIED CONTENT', 1)[1])
        with patch.object(review_rounds, 'invoke', return_value={'response': self.response(task['prompt'])}):
            with self.assertRaisesRegex(ValueError, 'only the failed targets'):
                review_rounds._repair_round(self.batch, exported['task_id'], {})
        self.assertFalse((self.batch / ('workflow/review-bindings/' + exported['task_id'] + '.receipt.json')).exists())

    def test_group_sample_repair_preserves_null_target_and_full_catalog(self):
        self.write('01_source/structure.confirmed.json', {'units': [
            {'unit_id': 'g1', 'unit_kind': 'group', 'numbers': [1, 2, 3], 'stem': self.source}]})
        self.write('03_types/types.json', {'items': [{'unit_id': 'g1', 'type': '阅读理解'}]})
        self.write('04_knowledge/knowledge.json', {'items': [{'unit_id': 'g1', 'knowledge': []}]})
        exported = review_rounds.export_task(self.batch, only_round='sample')
        task = self.task(exported)
        plan = review.prepare_round_plan(self.batch)
        partial = {'task_id': exported['task_id'], 'round_id': 'sample', 'input_binding': plan['input_binding'],
                   'part_index': 0, 'valid_checks': [],
                   'failed': [{'unit_id': 'g1', 'number': None, 'reason': 'quote absent'}]}
        prompt, targets = review_rounds._repair_context(self.batch, partial, plan)
        self.assertEqual(targets, [{'unit_id': 'g1', 'number': None}])
        self.assertIn('references/knowledge-catalog.json', prompt)
        self.assertEqual(prompt.split('\nBEGIN SUPPLIED CONTENT', 1)[1],
                         task['prompt'].split('\nBEGIN SUPPLIED CONTENT', 1)[1])

    def complete_fixture_rounds(self):
        with patch.object(review_rounds, 'invoke', side_effect=lambda prompt, *a, **k: {'response': self.response(prompt)}):
            result = review_rounds.run(self.batch, config={'review_part_units': 1})
        self.assertTrue(result['completed'], result)

    def test_default_reuses_completed_rounds_and_fresh_archives_without_rebinding(self):
        self.complete_fixture_rounds()
        active = self.batch / ('07_review/' + review.ROUND_FILE)
        original = active.read_bytes()
        evidence = self.snapshot(['workflow/review-bindings', 'workflow/review-partitions', 'workflow/review-results',
                                  'workflow/review-transactions', 'workflow/review-tasks'])
        result = review_rounds.run(self.batch, config={})
        self.assertTrue(result['completed'])
        self.assertEqual(active.read_bytes(), original)
        self.assertFalse((self.batch / 'workflow/review-generations').exists())
        self.write('07_review/manual-review-queue.json', {'items': [{'finding_id': 'keep', 'status': 'resolved'}]})
        queue = (self.batch / '07_review/manual-review-queue.json').read_bytes()
        plan = review.prepare_round_plan(self.batch)
        old_bindings = [json.loads(data) for relative, data in evidence.items()
                        if relative.startswith('workflow/review-bindings/') and not relative.endswith('.receipt.json')]
        restarted = review_rounds.restart_rounds(self.batch)
        self.assertEqual(Path(restarted['rounds_archive']).read_bytes(), original)
        self.assertTrue(Path(restarted['archive_path']).is_dir())
        self.assertFalse(active.exists())
        self.assertEqual((self.batch / '07_review/manual-review-queue.json').read_bytes(), queue)
        self.assertEqual(review.prepare_round_plan(self.batch), plan)
        for relative, data in evidence.items():
            self.assertEqual((self.batch / relative).read_bytes(), data, relative)
        fresh = review_rounds.export_task(self.batch, max_units_per_part=1)
        self.assertNotIn(fresh['task_id'], [binding['task']['task_id'] for binding in old_bindings])
        bound = json.loads((self.batch / ('workflow/review-bindings/' + fresh['task_id'] + '.json')).read_text())
        self.assertEqual(bound['input_hashes'], old_bindings[0]['input_hashes'])
        self.assertNotIn(bound['state_path'], [binding['state_path'] for binding in old_bindings])

    def test_restart_archive_failure_preserves_active_rounds_and_generation(self):
        self.complete_fixture_rounds()
        before = self.snapshot(['07_review', 'workflow/review-bindings', 'workflow/review-partitions'])
        with patch.object(review_rounds, '_atomic', side_effect=OSError('archive disk full')):
            with self.assertRaises(OSError):
                review_rounds.restart_rounds(self.batch)
        for relative, data in before.items():
            self.assertEqual((self.batch / relative).read_bytes(), data)
        self.assertFalse((self.batch / 'workflow/review-generations').exists())

    def test_restart_refuses_active_model_lock_without_changes(self):
        with review_rounds._agent_lock(0, shared=True):
            with self.assertRaises(AgentError) as caught:
                review_rounds.restart_rounds(self.batch)
        self.assertEqual(caught.exception.code, 'lock_timeout')
        self.assertFalse((self.batch / 'workflow/review-history').exists())
        self.assertFalse((self.batch / 'workflow/review-generations').exists())

    def test_restart_does_not_replay_retired_unreceipted_transaction(self):
        exported = review_rounds.export_task(self.batch)
        response = self.response(self.task(exported)['prompt'])
        with patch.object(review_rounds, '_finish_install', side_effect=OSError('interrupted install')):
            with self.assertRaises(OSError):
                review_rounds.import_result(self.batch, exported['task_id'], response)
        evidence = self.snapshot(['workflow/review-bindings', 'workflow/review-transactions', 'workflow/review-partitions'])
        restarted = review_rounds.restart_rounds(self.batch)
        self.assertIsNone(restarted['rounds_archive'])
        with patch.object(review_rounds, '_finish_install', side_effect=AssertionError('retired install forbidden')):
            fresh = review_rounds.export_task(self.batch)
        self.assertNotEqual(fresh['task_id'], exported['task_id'])
        with self.assertRaises(review_rounds.ReviewInputsChanged):
            review_rounds.import_result(self.batch, exported['task_id'], response)
        for relative, data in evidence.items():
            self.assertEqual((self.batch / relative).read_bytes(), data)
        self.assertFalse((self.batch / ('07_review/' + review.ROUND_FILE)).exists())

    def test_run_fresh_and_cli_require_explicit_flag(self):
        with patch.object(review_rounds, '_run', return_value={'action': 'exported', 'completed': False}):
            ordinary = review_rounds.run(self.batch, config={})
            self.assertNotIn('restart', ordinary)
            self.assertFalse((self.batch / 'workflow/review-generations').exists())
            explicit = review_rounds.run(self.batch, config={}, fresh=True)
            self.assertTrue(Path(explicit['restart']['archive_path']).is_dir())
        with patch.object(review_rounds, 'run', return_value={'action': 'exported', 'completed': False}) as run, \
                patch('builtins.print'):
            self.assertEqual(review_rounds.main(['run', '--batch-dir', str(self.batch), '--fresh']), 0)
        run.assert_called_once_with(str(self.batch), fresh=True)

    def test_invalidated_inputs_do_not_trigger_repair_model(self):
        exported = review_rounds.export_task(self.batch)
        tid = exported['task_id']
        response = self.response(self.task(exported)['prompt'])
        response['artifacts']['review_round']['checks'][0]['evidence'][0]['quote'] = 'not a real quotation'
        with self.assertRaises(review.RoundCheckError):
            review_rounds.import_result(self.batch, tid, response)
        (self.batch / '01_source/正文.txt').unlink()
        with self.assertRaises(review_rounds.ReviewInputsChanged):
            review_rounds._repair_round(self.batch, tid, {})


if __name__ == '__main__':
    unittest.main()
