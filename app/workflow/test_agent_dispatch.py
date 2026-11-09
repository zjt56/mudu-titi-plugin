"""本地 Agent 代理桥调度回归。"""
import argparse
from contextlib import nullcontext
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
        recovery = patch('workflow.self_heal.recover', return_value={})
        recovery.start(); self.addCleanup(recovery.stop)
        # Fixture dispatch never runs a real agent. Avoid waiting behind a live
        # workflow's repository-wide lock, including the protocol's import alias.
        for target in ('workflow.local_agent._agent_lock', 'workflow.agent_protocol._agent_lock'):
            lock = patch(target, side_effect=lambda *args, **kwargs: nullcontext())
            lock.start(); self.addCleanup(lock.stop)
        self.folder = Path(self.temp.name)
        self.state = mf.new_manifest('fixture', self.folder, {}, {})
        # Dispatch starts after source-only preassembly, not at its boundary.
        for rel in (*contracts.STAGE_BY_ID['extract'].produces,
                    *contracts.STAGE_BY_ID['structure_gate'].produces,
                    *contracts.STAGE_BY_ID['preassemble'].produces):
            path = self.folder / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}', encoding='utf-8')
        self.state['stages']['extract']['status'] = mf.COMPLETED
        self.state['stages']['structure_gate']['status'] = mf.COMPLETED
        self.state['stages']['preassemble']['status'] = mf.COMPLETED
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
        # A needs_review producer is never promoted to a usable delivery; the
        # next run must offer the same agent stage rather than move downstream.
        stage = mf.load(self.folder)['stages']['answers']
        self.assertIn(stage['status'], (mf.NEEDS_REVIEW, mf.PENDING))
        self.assertEqual(result['stopped_at'], 'answers')

    def test_cli_stage_uses_same_bridge(self):
        args = argparse.Namespace(batch_dir=str(self.folder), stage='answers', until=None, force=False, dry_run=False)
        def execute(folder, state, stage):
            self.assertEqual(stage.command[0], '{python}')
            return 3
        # With unmet upstream handoff, cmd_run must not execute the dependent
        # stage at all and must report a non-success code.
        with patch.object(auto, 'load_bridge', return_value={'answers': ['{python}', '-m', 'workflow.local_agent']}), \
             patch.object(runner, '_execute_command', side_effect=execute) as call:
            code = runner.cmd_run(args)
        self.assertNotEqual(code, 0)
        self.assertEqual(call.call_count, 0)
