"""Route profiles must inherit providers the base profile does not declare."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "backend" / "merge-route-providers.py"

BASE = """- id: llm-pi-ai
  name: "@deepseek-ai/dsh-llm-pi-ai"
  config:
    providers:
      magpie:
        apiKeyEnv: MAGPIE_GATEWAY_KEY
        models:
          - id: auto/deepseek-v4.1-flash
- id: agent-default-model
  config:
    provider: magpie
    model: auto/deepseek-v4.1-flash
"""

OVERLAY = """- id: llm-pi-ai
  config:
    providers:
      opencode-go:
        apiKeyEnv: OPENCODE_GO_API_KEY
        models:
          - id: deepseek-v4.1-flash
            contextWindow: 1000000
      magpie:
        apiKeyEnv: WRONG_OVERLAY_KEY
        models:
          - id: overlay-only
- id: agent-default-model
  config:
    provider: opencode-go
    model: deepseek-v4.1-flash
"""


class RouteProviderMergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.target = self.root / "cordis.patch.yml"
        self.source = self.root / "desktop.patch.yml"
        self.target.write_text(BASE, encoding="utf-8")
        self.source.write_text(OVERLAY, encoding="utf-8")

    def merge(self):
        return subprocess.run([sys.executable, str(SCRIPT), str(self.target), str(self.source)],
                              capture_output=True, text=True)

    def test_missing_provider_is_copied_and_base_models_win(self):
        self.assertEqual(self.merge().returncode, 0)
        text = self.target.read_text(encoding="utf-8")
        self.assertIn("opencode-go:", text)
        self.assertIn("OPENCODE_GO_API_KEY", text)
        self.assertIn("auto/deepseek-v4.1-flash", text)
        self.assertNotIn("WRONG_OVERLAY_KEY", text)
        self.assertNotIn("overlay-only", text)
        self.assertNotIn("provider: opencode-go", text)  # default row stays owned by the router

    def test_merge_is_idempotent_and_leaves_other_rows_alone(self):
        self.merge()
        once = self.target.read_text(encoding="utf-8")
        self.assertEqual(self.merge().returncode, 0)
        self.assertEqual(once, self.target.read_text(encoding="utf-8"))
        self.assertEqual(once.count("opencode-go:"), 1)
        self.assertIn("provider: magpie", once)

    def test_missing_source_file_is_reported_not_guessed(self):
        self.source.unlink()
        result = self.merge()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.target.read_text(encoding="utf-8"), BASE)

    def test_source_without_provider_block_changes_nothing(self):
        self.source.write_text("- id: agent-default-model\n  config:\n    provider: x\n", encoding="utf-8")
        self.assertEqual(self.merge().returncode, 0)
        self.assertEqual(self.target.read_text(encoding="utf-8"), BASE)


if __name__ == "__main__":
    unittest.main()
