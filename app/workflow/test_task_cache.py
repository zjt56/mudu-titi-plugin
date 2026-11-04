import unittest

from workflow.task_cache import TaskCache, snapshot_key


class TaskCacheTests(unittest.TestCase):
    def test_reuses_same_snapshot_and_returns_defensive_copies(self):
        cache = TaskCache()
        calls = []

        def build():
            calls.append(1)
            return {"prompt": ["original"]}

        first = cache.get_or_build({"stage": "types", "inputs": {"source": "a"}}, build)
        first["prompt"].append("changed")
        second = cache.get_or_build({"inputs": {"source": "a"}, "stage": "types"}, build)
        self.assertEqual(second, {"prompt": ["original"]})
        second["prompt"].append("other")
        self.assertEqual(cache.get_or_build({"stage": "types", "inputs": {"source": "a"}}, build),
                         {"prompt": ["original"]})
        self.assertEqual(len(calls), 1)

    def test_changed_inputs_absence_budget_and_rules_miss(self):
        cache = TaskCache()
        calls = []
        base = {"stage": "types", "files": {"optional": None}, "budget": 10, "rules": "a"}
        variants = [base, dict(base, files={"optional": "digest"}),
                    dict(base, budget=11), dict(base, rules="b"), dict(base, stage="review")]
        for snapshot in variants:
            cache.get_or_build(snapshot, lambda: calls.append(1))
        self.assertEqual(len(calls), len(variants))

    def test_eviction_and_failures_are_not_cached(self):
        cache = TaskCache(max_entries=1)
        calls = []
        def fail():
            calls.append("failure")
            raise ValueError("failed")
        for _ in range(2):
            with self.assertRaises(ValueError):
                cache.get_or_build({"x": 1}, fail)
        cache.get_or_build({"x": 1}, lambda: calls.append("first"))
        cache.get_or_build({"x": 2}, lambda: calls.append("second"))
        cache.get_or_build({"x": 1}, lambda: calls.append("again"))
        self.assertEqual(calls, ["failure", "failure", "first", "second", "again"])

    def test_snapshot_rejects_ambiguous_keys_and_nonfinite_numbers(self):
        for snapshot in ({1: "value"}, {"x": float("nan")}, {"x": float("inf")}):
            with self.subTest(snapshot=snapshot), self.assertRaises(ValueError):
                snapshot_key(snapshot)


if __name__ == "__main__":
    unittest.main()
