import json
import tempfile
import unittest
from pathlib import Path

from workflow import content_snapshot


class ContentSnapshotTest(unittest.TestCase):
    def test_snapshot_copies_preassemble_base_without_rewriting_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "06_batch" / "batch.base.json"
            path.parent.mkdir()
            payload = {"items": [{"unit_id": "q-1", "stem": "原文"}]}
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            out = content_snapshot.build(root)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")), payload)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), payload)


if __name__ == "__main__":
    unittest.main()
