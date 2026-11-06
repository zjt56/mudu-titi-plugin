"""本地 Agent 代理桥调度回归。"""
import argparse
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import auto, contracts, manifest as mf, runner


class AgentDispatchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.state = mf.new_manifest('fixture', self.folder, {}, {})
        for rel in (*contracts.STAGE_BY_ID['extract'].produces,
                    *contracts.STAGE_BY_ID['structure_gate'].produces):
            path = self.folder / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}', encoding='utf-8')
        self.state['stages']['extract']['status'] = mf.COMPLETED
        self.state['stages']['structure_gate']['status'] = mf.COMPLETED
        mf.save(self.folder, self.state)

    def test_interpreter_placeholder_uses_current_python(self):
        self.assertEqual(runner._render(('{python}', '-m', 'workflow.local_agent'), self.folder)[0], sys.executable)

    def test_zero_exit_without_required_artifact_fails(self):
        stage = auto._bridge_stage(contracts.STAGE_BY_ID['answers'], ['echo', 'done'])
        with patch.object(runner.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'done', '')):
            self.assertEqual(runner._execute_command(self.folder, self.state, stage), 1)
        self.assertEqual(mf.load(self.folder)['stages']['answers']['status'], mf.FAILED)

    def test_agent_needs_review_persists_and_stops_auto(self):
        with patch.object(runner.subprocess, 'run', return_value=subprocess.CompletedProcess([], 3, 'missing evidence', '')):
            result = auto.advance(self.folder, {'answers': ['echo', 'review']})
        self.assertEqual(mf.load(self.folder)['stages']['answers']['status'], mf.NEEDS_REVIEW)
        self.assertEqual(result['action'], 'review')
        self.assertEqual(result['stopped_at'], 'answers')

    def test_cli_stage_uses_same_bridge(self):
        args = argparse.Namespace(batch_dir=str(self.folder), stage='answers', until=None, force=False, dry_run=False)
        def execute(folder, state, stage):
            self.assertEqual(stage.command[0], '{python}')
            return 3
        with patch.object(auto, 'load_bridge', return_value={'answers': ['{python}', '-m', 'workflow.local_agent']}), \
             patch.object(runner, '_execute_command', side_effect=execute) as call:
            self.assertEqual(runner.cmd_run(args), 3)
        self.assertEqual(call.call_count, 1)
