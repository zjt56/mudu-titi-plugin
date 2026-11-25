"""Host stop / interrupted invocation must not permanently poison a content identity."""
import time
import unittest

from workflow import content_agent as broker, local_agent
from workflow import test_content_agent as base


class InterruptedRedispatchTests(base.ContentAgentTests):
    def _end(self, task_id, **fields):
        with broker._lock():
            task = broker._read(broker._task_path(task_id))
            task.update(status="cancelled", accepted=False, retryable=False, **fields)
            broker._save(task)

    def _wait_tasks(self, count):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            tasks = list(broker._tasks())
            if len(tasks) >= count:
                return tasks
            time.sleep(.01)
        self.fail("expected %d tasks, got %d" % (count, len(list(broker._tasks()))))

    def _redispatched(self, **fields):
        task, thread, result = self.start()
        self._end(task["task_id"], **fields)
        thread.join(3)
        self.assertIsInstance(result.get("error"), local_agent.AgentError)
        _, thread2, _ = self.start(run="retry")
        tasks = self._wait_tasks(2)
        new = [t for t in tasks if t["task_id"] != task["task_id"]]
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0]["redispatch_of"], task["task_id"])
        self._end(new[0]["task_id"], error="Batch deletion requested", error_code="cancelled")
        thread2.join(3)

    def test_host_manager_stop_redispatches_without_cooldown(self):
        self._redispatched(session_id="host-session", error="Content manager stopped")

    def test_interrupted_invocation_redispatches(self):
        self._redispatched(error="Native content invocation interrupted", error_code="cancelled")

    def _terminal(self, **fields):
        task, thread, _ = self.start()
        self._end(task["task_id"], **fields)
        thread.join(3)
        with self.assertRaises(local_agent.AgentError):
            broker.invoke(self.prompt(), self.base / "retry", self.config)
        self.assertEqual(len(list(broker._tasks())), 1)

    def test_deletion_cancel_stays_terminal(self):
        self._terminal(error="Batch deletion requested", error_code="cancelled")

    def test_timeout_cancel_stays_terminal(self):
        self._terminal(error="Native content task timed out", error_code="timeout")

    def test_context_changed_stays_terminal(self):
        self._terminal(error="Content manager stopped", error_code="context_changed")

    def test_cancel_with_candidate_stays_terminal(self):
        self._terminal(error="Content manager stopped", candidate={"x": 1})

    def test_redispatch_chain_is_bounded(self):
        first, thread, _ = self.start()
        self._end(first["task_id"], error="Content manager stopped")
        thread.join(3)
        for n in range(broker.UPSTREAM_MAX_REDISPATCHES):
            _, t, _ = self.start(run="r%d" % n)
            tasks = self._wait_tasks(n + 2)
            newest = max(tasks, key=lambda x: x["created_at"])
            self._end(newest["task_id"], error="Content manager stopped")
            t.join(3)
        with self.assertRaises(local_agent.AgentError):
            broker.invoke(self.prompt(), self.base / "final", self.config)
        self.assertEqual(len(list(broker._tasks())), broker.UPSTREAM_MAX_REDISPATCHES + 1)


for _name in [n for n in dir(base.ContentAgentTests) if n.startswith("test_")]:
    setattr(InterruptedRedispatchTests, _name, None)

if __name__ == "__main__":
    unittest.main()
