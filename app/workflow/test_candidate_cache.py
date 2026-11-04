import json
from pathlib import Path
import tempfile
import unittest

from workflow import candidate_cache as cache


class CandidateCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        self.args = {"stage": "types", "fingerprints": [{"path": "source", "sha256": "a"}],
                     "prompt": "full prompt", "model_config": {"model": "m", "token": "secret"}}
        self.validations = []

    def validate(self, value):
        self.validations.append(value)
        if value != {"status": "completed", "artifacts": {"x": 1}}:
            raise ValueError("invalid candidate")

    def test_round_trip_revalidates_and_keeps_credentials_out_of_files(self):
        value = {"status": "completed", "artifacts": {"x": 1}}
        cache.store(self.batch, **self.args, candidate=value, validate=self.validate)
        self.assertEqual(cache.load(self.batch, **self.args, validate=self.validate), value)
        self.assertEqual(len(self.validations), 2)
        path = next((self.batch / "workflow/candidate-cache").iterdir())
        self.assertNotIn("secret", path.read_text())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_input_prompt_model_and_stage_changes_miss(self):
        cache.store(self.batch, **self.args, candidate={"status": "completed", "artifacts": {"x": 1}}, validate=self.validate)
        for changes in ({"fingerprints": [{"path": "source", "sha256": "b"}]},
                        {"prompt": "different"}, {"model_config": {"model": "m2"}}, {"stage": "answers"}):
            with self.subTest(changes=changes):
                self.assertIsNone(cache.load(self.batch, **dict(self.args, **changes), validate=self.validate))
        with self.assertRaises(ValueError):
            cache.load(self.batch, **dict(self.args, stage="review"), validate=self.validate)

    def test_failed_validation_does_not_store_and_tampering_is_revalidated(self):
        with self.assertRaises(ValueError):
            cache.store(self.batch, **self.args, candidate={}, validate=self.validate)
        self.assertFalse((self.batch / "workflow/candidate-cache").exists())
        cache.store(self.batch, **self.args, candidate={"status": "completed", "artifacts": {"x": 1}}, validate=self.validate)
        path = next((self.batch / "workflow/candidate-cache").iterdir())
        path.write_text(json.dumps({"status": "completed", "artifacts": {"x": 2}}))
        with self.assertRaisesRegex(ValueError, "invalid candidate"):
            cache.load(self.batch, **self.args, validate=self.validate)

    def test_refuses_symlinked_cache_directory_and_file(self):
        (self.batch / "workflow").mkdir()
        (self.batch / "workflow/candidate-cache").symlink_to(self.batch, target_is_directory=True)
        with self.assertRaises(OSError):
            cache.store(self.batch, **self.args, candidate={"status": "completed", "artifacts": {"x": 1}}, validate=self.validate)
        (self.batch / "workflow/candidate-cache").unlink()
        cache.store(self.batch, **self.args, candidate={"status": "completed", "artifacts": {"x": 1}}, validate=self.validate)
        path = next((self.batch / "workflow/candidate-cache").iterdir())
        path.unlink()
        path.symlink_to(self.batch / "outside")
        with self.assertRaises(OSError):
            cache.load(self.batch, **self.args, validate=self.validate)

    def test_strict_json_and_size_limit(self):
        cache.store(self.batch, **self.args, candidate={"status": "completed", "artifacts": {"x": 1}}, validate=self.validate)
        path = next((self.batch / "workflow/candidate-cache").iterdir())
        for raw in (b'{"x":1,"x":2}', b'{"x":1e999}', b' ' * (cache.MAX_BYTES + 1)):
            path.write_bytes(raw)
            with self.assertRaises(ValueError):
                cache.load(self.batch, **self.args, validate=self.validate)


if __name__ == "__main__":
    unittest.main()
