import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from workflow import speculative_candidates as candidates


class SpeculativeCandidatesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        (self.batch / "workflow").mkdir()
        self.stages = ("types", "answers", "explanations", "knowledge")
        self.fingerprints = {"source": "sha256-source"}
        self.tasks = {stage: {"prompt": stage + " prompt", "source_fingerprints": dict(self.fingerprints)}
                      for stage in self.stages}
        self.config = {"model": "fake", "secret": "do-not-persist"}

    def test_four_calls_overlap_and_resume_after_partial_failure(self):
        barrier = threading.Barrier(4, timeout=5)
        seen = []
        guard = threading.Lock()

        def invoke(stage, prompt):
            with guard:
                seen.append(stage)
            barrier.wait()
            if stage == "knowledge":
                raise RuntimeError("simulated interruption")
            return {"status": "completed", "value": stage}

        first = candidates.generate(self.batch, self.tasks, self.config, invoke,
                                    lambda stage: dict(self.fingerprints))
        self.assertCountEqual(seen, self.stages)
        self.assertEqual(first["knowledge"]["status"], "failed")
        self.assertEqual([first[s]["status"] for s in self.stages[:3]], ["candidate"] * 3)
        called = []
        def resume(stage, prompt):
            called.append(stage)
            return {"status": "needs_review"}
        second = candidates.generate(self.batch, self.tasks, self.config, resume,
                                     lambda stage: dict(self.fingerprints))
        self.assertEqual(called, ["knowledge"])
        self.assertTrue(all(x["status"] == "candidate" for x in second.values()))
        self.assertNotIn("do-not-persist", "".join(p.read_text() for p in (self.batch / "workflow" / "candidate-cache").glob("*.json")))
        self.assertFalse((self.batch / "workflow" / "manifest.json").exists())

    def test_process_crash_after_atomic_write_recovers_candidate(self):
        script = '''
import os, sys
from workflow import speculative_candidates as c
original = c._store
def crash(*args):
    original(*args)
    os._exit(17)
c._store = crash
c.generate(sys.argv[1], {"types": {"prompt": "types prompt", "source_fingerprints": {"source": "sha256-source"}}},
           {"model": "fake", "secret": "do-not-persist"}, lambda stage, prompt: {"status": "completed"},
           lambda stage: {"source": "sha256-source"})
'''
        process = subprocess.run([sys.executable, "-c", script, os.fspath(self.batch)], check=False)
        self.assertEqual(process.returncode, 17)
        def no_call(stage, prompt):
            raise AssertionError("stored candidate must be recovered")
        result = candidates.generate(self.batch, {"types": self.tasks["types"]}, self.config,
                                     no_call, lambda stage: dict(self.fingerprints))
        self.assertEqual(result["types"]["status"], "candidate")

    def test_source_change_during_call_is_never_stored(self):
        state = {"source": "sha256-source"}
        def invoke(stage, prompt):
            state["source"] = "changed"
            return {"status": "completed"}
        result = candidates.generate(self.batch, {"types": self.tasks["types"]}, self.config,
                                     invoke, lambda stage: dict(state))
        self.assertEqual(result["types"]["status"], "stale")
        self.assertEqual(list((self.batch / "workflow" / "candidate-cache").glob("*.json")), [])

    def test_changed_prompt_or_configuration_never_reuses_candidate(self):
        calls = []
        def invoke(stage, prompt):
            calls.append(prompt)
            return {"status": "completed"}
        task = {"types": self.tasks["types"]}
        for config in (self.config, self.config, {"model": "other"}):
            candidates.generate(self.batch, task, config, invoke, lambda stage: dict(self.fingerprints))
        task = {"types": {**self.tasks["types"], "prompt": "different"}}
        candidates.generate(self.batch, task, self.config, invoke, lambda stage: dict(self.fingerprints))
        self.assertEqual(calls, ["types prompt", "types prompt", "different"])


if __name__ == "__main__":
    unittest.main()
