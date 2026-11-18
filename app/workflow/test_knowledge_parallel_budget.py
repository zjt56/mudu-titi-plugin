"""Exercise real parallel dispatch, bounded execution and the final importer."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import agent_tasks, auto, content_agent, contracts, local_agent, manifest as mf, unit_tasks


class KnowledgeParallelBudgetTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.batch = Path(self.temp.name) / 'batch'
        lines = [f'{n}. Choose the correct verb. A. go B. goes' for n in range(1, 49)]
        units = [{'unit_id': f'q-{n}', 'unit_kind': 'standalone', 'numbers': [n],
                  'type': '单项填空', 'stem': line} for n, line in enumerate(lines, 1)]
        self.put('01_source/正文.txt', '\n'.join(lines))
        self.put('01_source/structure.json', {'sections': [], 'groups': []})
        self.put('01_source/structure.confirmed.json', {'units': units})
        self.put(agent_tasks.ARTIFACTS['types'], {'items': [
            {'unit_id': u['unit_id'], 'type': '单项填空', 'subtypes': ['四个选项'],
             'words': 6, 'contentComplete': True, 'evidence': ['正文']} for u in units]})
        state = mf.new_manifest('knowledge-budget-fixture', self.batch, {}, {})
        for stage in ('extract', 'structure_gate', 'preassemble', 'types'):
            mf.touch_stage(state, stage, mf.COMPLETED)
        mf.save(self.batch, state)
        self.config = {'max_input_chars': 180000, 'retries': 0, 'part_concurrency': 1,
                       'model_cache_identity': 'knowledge-budget-test', 'label_split': False,
                       'native_agent': {'enabled': True, 'stages': ['knowledge'],
                                        'max_tool_calls': 40}}

    def put(self, relative, value):
        path = self.batch / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
                        encoding='utf-8')

    @staticmethod
    def selected(prompt):
        context = json.loads(prompt.split(unit_tasks._BEGIN, 1)[1].split(unit_tasks._END, 1)[0])
        return [u['unit_id'] for u in context['confirmed_units_with_stable_ids']]

    def dispatch(self, invoke):
        # Isolate readiness from unneeded upstream artifacts, retaining real
        # task export, unit validation, checkpoints and protocol import.
        with patch('workflow.stage_preflight.run', return_value=None), \
             patch.object(mf, 'dependency_satisfied', return_value=True), \
             patch.object(local_agent, 'invoke', side_effect=invoke):
            return auto._parallel_ready_agents(
                self.batch, [contracts.STAGE_BY_ID['knowledge']], self.config)

    def candidate(self, ids):
        return {'response': {'status': 'completed', 'artifacts': {
            agent_tasks.ARTIFACTS['knowledge']: {'items': [
                {'unit_id': uid, 'knowledge': ['词汇/词汇辨析/动词辨析'],
                 'subquestions': [], 'evidence': ['正文']} for uid in ids]}},
            'notes': [], 'findings': []}}

    def native_checked_candidate(self, ids):
        candidate = self.candidate(ids)['response']
        task = {'batch_dir': str(self.batch), 'stage': 'knowledge',
                'unit_ids': ids, 'partial': True}
        # Session admission/route fencing is tested by content-agent tests;
        # exercise its real partial source/catalog validator here as well.
        with patch.object(content_agent, '_guard'), patch.object(content_agent, '_check_route'):
            checked = content_agent._validate(task, candidate)
        return {'response': checked}

    def test_48_units_use_six_bounded_calls_and_real_final_import(self):
        calls = []

        def invoke(prompt, directory, config, **options):
            ids = self.selected(prompt)
            self.assertLessEqual(len(ids), 8)
            self.assertEqual(config['native_agent']['max_tool_calls'], 40)
            self.assertTrue(options['coordinated_imports'])
            calls.append(ids)
            return self.native_checked_candidate(ids)

        self.assertEqual(self.dispatch(invoke), {'knowledge': 0})
        self.assertEqual([len(ids) for ids in calls], [8] * 6)
        self.assertEqual([uid for ids in calls for uid in ids], [f'q-{n}' for n in range(1, 49)])
        saved = json.loads((self.batch / agent_tasks.ARTIFACTS['knowledge']).read_text())
        self.assertEqual(len(saved['items']), 48)
        self.assertEqual(mf.load(self.batch)['stages']['knowledge']['status'], mf.COMPLETED)

    def test_failure_resumes_only_missing_units_without_partial_delivery(self):
        calls = []

        def fail_second(prompt, directory, config, **options):
            ids = self.selected(prompt)
            calls.append(ids)
            if len(calls) == 2:
                raise local_agent.AgentError('Content tool-call budget exceeded', 'failed')
            return self.native_checked_candidate(ids)

        self.assertEqual(self.dispatch(fail_second), {'knowledge': 1})
        self.assertFalse((self.batch / agent_tasks.ARTIFACTS['knowledge']).exists())
        self.assertNotEqual(mf.load(self.batch)['stages']['knowledge']['status'], mf.COMPLETED)
        resumed = []

        def recover(prompt, directory, config, **options):
            ids = self.selected(prompt)
            resumed.extend(ids)
            return self.native_checked_candidate(ids)

        self.assertEqual(self.dispatch(recover), {'knowledge': 0})
        self.assertEqual(resumed, calls[1])
        self.assertEqual(mf.load(self.batch)['stages']['knowledge']['status'], mf.COMPLETED)


if __name__ == '__main__':
    unittest.main()
