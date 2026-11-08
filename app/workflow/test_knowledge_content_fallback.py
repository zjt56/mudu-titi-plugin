"""Group optional-content fallback: real importer/CLI, isolated data, no models."""
import contextlib
from copy import deepcopy
import io
import json
import unittest
from unittest.mock import patch

from workflow import agent_tasks as tasks, local_agent, unit_tasks
from workflow import test_agent_tasks as fixture


class KnowledgeContentFallbackTest(unittest.TestCase):
    write = fixture.AgentTasksTest.write
    result = fixture.AgentTasksTest.result
    accept = fixture.AgentTasksTest.accept

    def setUp(self):
        fixture.AgentTasksTest.setUp(self)
        self.payload = fixture.AgentTasksTest.group_fixture(self)
        # No original analysis/summary exists: optional absence cannot erase
        # genuine original content or excuse a broken explicit artifact.
        self.write('01_source/附录.txt', '1. A\n2. B\n3. C')
        self.explanations = tasks._safe_path(self.batch, tasks.ARTIFACTS['explanations'])
        self.assertTrue(self.explanations.is_relative_to(self.batch.resolve()))
        self.explanations.unlink()
        self.addCleanup(patch.stopall)
        patch('socket.socket.connect', side_effect=AssertionError('network forbidden')).start()
        patch('subprocess.Popen', side_effect=AssertionError('model/process forbidden')).start()

    def test_absent_explanations_group_preserves_exact_children_answers_and_tags(self):
        original = deepcopy(self.payload)
        report = self.accept('knowledge', self.payload)
        self.assertTrue(report['completed'], report)
        saved = json.loads((self.batch / tasks.ARTIFACTS['knowledge']).read_text())['items'][0]
        self.assertEqual([c['number'] for c in saved['subquestions']], [1, 2, 3])
        # knowledge now carries labels only; answers/explanations merge at assemble.
        self.assertNotIn('answer', saved)
        self.assertNotIn('explanation', saved)
        self.assertEqual([c['knowledge'] for c in saved['subquestions']], [[], [], []])
        self.assertEqual(saved['knowledge'], original['items'][0]['knowledge'])
        self.assertFalse(self.explanations.exists())
        # accept_result copies its input, including all model-only prose echoes.
        self.assertEqual(self.payload, original)

    def test_explicit_invalid_explanations_is_rejected_without_replacement(self):
        # Assembly is now the single merge point that validates explanations
        # child coverage; knowledge no longer reads them.
        from workflow import content_rules
        units = [{'unit_id': 'group-1-2-3', 'unit_kind': 'group', 'numbers': [1, 2, 3]}]
        typed = {'group-1-2-3': {'type': '补全对话', 'subtypes': ['7选5']}}
        for children in ([], [{'number': 1, 'explanation': '无'}],
                         [{'number': 1, 'explanation': '无'}] * 3):
            with self.subTest(children=children):
                payload = {'items': [{
                    'unit_id': 'group-1-2-3', 'explanation': '无', 'articleSummary': '无',
                    'subquestions': children, 'evidence': ['正文未提供解析']}]}
                with self.assertRaisesRegex(ValueError, 'child number coverage'):
                    content_rules.validate(payload, units, typed, self.batch)

    def test_missing_optional_content_does_not_excuse_missing_knowledge_children(self):
        candidate = deepcopy(self.payload)
        candidate['items'][0]['subquestions'] = []
        before = (self.batch / tasks.ARTIFACTS['knowledge']).read_bytes()
        with self.assertRaisesRegex(ValueError, 'child number coverage'):
            self.accept('knowledge', candidate)
        self.assertEqual(before, (self.batch / tasks.ARTIFACTS['knowledge']).read_bytes())
        self.assertFalse(self.explanations.exists())

    def test_group_missing_required_genre_still_fails_after_content_fallback(self):
        candidate = deepcopy(self.payload)
        candidate['items'][0]['knowledge'] = ['主题/人与自我/生活与学习/学校生活']
        before = (self.batch / tasks.ARTIFACTS['knowledge']).read_bytes()
        with self.assertRaisesRegex(ValueError, '组级缺必带维度：语篇体裁'):
            self.accept('knowledge', candidate)
        self.assertEqual(before, (self.batch / tasks.ARTIFACTS['knowledge']).read_bytes())

    def test_real_cli_and_importer_accept_group_with_missing_explanations(self):
        config = {'provider': 'codex', 'model': 'offline-stub', 'executable': '/unused/model',
                  'max_input_chars': 1000000, 'label_part_units': 1, 'part_concurrency': 1,
                  'retries': 0, 'candidate_cache': False}
        errors = io.StringIO()
        def invoke(prompt, run_dir, settings, **options):
            context = json.loads(prompt.split(unit_tasks._BEGIN, 1)[1].rsplit(unit_tasks._END, 1)[0])
            self.assertEqual([u['unit_id'] for u in context['confirmed_units_with_stable_ids']], ['group-1-2-3'])
            return {'response': self.result('knowledge', self.payload)}
        with patch.object(local_agent, 'load_config', return_value=config), \
             patch.object(local_agent, 'invoke', side_effect=invoke) as model, \
             patch.object(local_agent, '_agent_lock', side_effect=lambda *_: contextlib.nullcontext()), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
            code = local_agent.main(['--stage', 'knowledge', '--batch-dir', str(self.batch)])
        self.assertEqual(code, 0, errors.getvalue())
        self.assertEqual(model.call_count, 1)
        saved = json.loads((self.batch / tasks.ARTIFACTS['knowledge']).read_text())['items'][0]
        self.assertEqual([c['number'] for c in saved['subquestions']], [1, 2, 3])
        self.assertNotIn('explanation', saved)
        self.assertFalse(self.explanations.exists())


if __name__ == '__main__':
    unittest.main()
