"""子 Agent 路由：角色优先、阶段次之、默认兜底，绝不在无默认模型时静默换模型。"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "backend/route_pick.py"


class RoutePickTest(unittest.TestCase):
    def pick(self, router, stage="", rnd="", agent=""):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model-router.json"
            path.write_text(json.dumps(router, ensure_ascii=False), encoding="utf-8")
            return subprocess.run([sys.executable, str(SCRIPT), str(path), stage, rnd, agent],
                                  capture_output=True, text=True)

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

    def test_missing_default_fails_instead_of_switching_models(self):
        result = self.pick({"roles": {"answers": {"provider": "r", "model": "role-model"}}},
                           stage="answers", agent="answers")
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
