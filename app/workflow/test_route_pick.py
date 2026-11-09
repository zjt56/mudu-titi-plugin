"""子 Agent 路由：角色优先、阶段次之、默认兜底，绝不在无默认模型时静默换模型。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "backend/route_pick.py"


class RoutePickTest(unittest.TestCase):
    def pick(self, router, stage="", rnd="", agent="", env=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model-router.json"
            path.write_text(json.dumps(router, ensure_ascii=False), encoding="utf-8")
            run_env = {**os.environ, **(env or {})}
            return subprocess.run([sys.executable, str(SCRIPT), str(path), stage, rnd, agent],
                                  capture_output=True, text=True, env=run_env)

    @staticmethod
    def base(**overrides):
        router = {"default": {"provider": "p", "model": "m", "profile": "wf-deepseek-fast"}}
        router.update(overrides)
        return router

    def test_role_wins_over_stage(self):
        result = self.pick(self.base(stages={"answers": {"provider": "s", "model": "stage-model"}},
                                     roles={"answers": {"provider": "r", "model": "role-model"}}),
                           stage="answers", agent="answers")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().split("\t")[:2], ["r", "role-model"])

    def test_stage_used_when_role_absent(self):
        result = self.pick(self.base(stages={"answers": {"provider": "s", "model": "stage-model"}}),
                           stage="answers", agent="answers")
        self.assertEqual(result.stdout.strip().split("\t")[:2], ["s", "stage-model"])

    def test_default_when_nothing_configured(self):
        result = self.pick(self.base(), stage="types", agent="types")
        self.assertEqual(result.stdout.strip().split("\t")[:2], ["p", "m"])

    def test_review_round_wins_over_role(self):
        result = self.pick(self.base(roles={"review": {"provider": "r", "model": "role-model"}},
                                     review={"rounds": {"sample": {"provider": "x", "model": "round-model"}}}),
                           stage="review", rnd="sample", agent="review")
        self.assertEqual(result.stdout.strip().split("\t")[:2], ["x", "round-model"])

    def test_review_role_used_when_round_absent(self):
        result = self.pick(self.base(roles={"review": {"provider": "r", "model": "role-model"}}),
                           stage="review", rnd="labels", agent="review")
        self.assertEqual(result.stdout.strip().split("\t")[:2], ["r", "role-model"])

    def test_empty_default_without_harness_model_fails(self):
        # 仓库不固定模型：default 为空且本机也没有 Harness 默认时才报错。
        with tempfile.TemporaryDirectory() as empty:
            result = self.pick({"default": {}, "roles": {"answers": {"provider": "r", "model": "role-model"}}},
                               stage="answers", agent="answers",
                               env={"DSH_HOME": empty, "DSH_PROFILE": ""})
        self.assertEqual(result.returncode, 1)
        self.assertIn("no valid default", result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    def test_empty_default_follows_device_harness_model(self):
        # 每台设备跟随自己 Harness 菜单当前模型。
        with tempfile.TemporaryDirectory() as home:
            desktop = Path(home) / "profiles" / "desktop"
            desktop.mkdir(parents=True)
            (desktop / "cordis.patch.yml").write_text(
                '- id: llm-pi-ai\n'
                '  config:\n'
                '    providers:\n'
                '      opencode-go:\n'
                '        models:\n'
                '          - id: deepseek-v4.1-flash\n'
                '- id: agent-default-model # device menu\n'
                '  config:\n'
                '    provider: opencode-go\n'
                '    model: deepseek-v4.1-flash\n',
                encoding="utf-8")
            result = self.pick({"default": {}, "roles": {}}, stage="types", agent="types",
                               env={"DSH_HOME": home, "DSH_PROFILE": ""})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().split("\t")[:2], ["opencode-go", "deepseek-v4.1-flash"])

    def test_harness_default_provider_not_servable_is_skipped(self):
        # WorkBuddy 的 magpie 只存在于 Harness 菜单、不在路由 overlay：不能选它。
        with tempfile.TemporaryDirectory() as home:
            desktop = Path(home) / "profiles" / "desktop"
            desktop.mkdir(parents=True)
            (desktop / "cordis.patch.yml").write_text(
                '- id: llm-pi-ai\n'
                '  config:\n'
                '    providers:\n'
                '      opencode-go:\n'
                '        models:\n'
                '          - id: deepseek-v4.1-flash\n'
                '- id: agent-default-model\n'
                '  config:\n'
                '    provider: magpie\n'
                '    model: auto/deepseek-v4.1-flash\n',
                encoding="utf-8")
            result = self.pick({"default": {}, "roles": {}}, stage="types", agent="types",
                               env={"DSH_HOME": home, "DSH_PROFILE": ""})
        self.assertEqual(result.returncode, 1)
        self.assertIn("no valid default", result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    def test_corrupt_explicit_default_never_falls_back_to_harness(self):
        # 显式配置存在但不完整是错误，不能悄悄改用 Harness 模型。
        with tempfile.TemporaryDirectory() as home:
            desktop = Path(home) / "profiles" / "desktop"
            desktop.mkdir(parents=True)
            (desktop / "cordis.patch.yml").write_text(
                '- id: agent-default-model\n  config:\n    provider: magpie\n    model: from-harness\n',
                encoding="utf-8")
            result = self.pick({"default": {"provider": "only-provider"}}, stage="types", agent="types",
                               env={"DSH_HOME": home, "DSH_PROFILE": ""})
        self.assertEqual(result.returncode, 1)
        self.assertIn("no valid default", result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    def test_corrupt_config_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model-router.json"
            path.write_text("{not json", encoding="utf-8")
            result = subprocess.run([sys.executable, str(SCRIPT), str(path), "answers", "", "answers"],
                                    capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot read model-router.json", result.stderr)


if __name__ == "__main__":
    unittest.main()
