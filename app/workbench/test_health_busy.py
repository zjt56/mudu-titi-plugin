import threading
import unittest
from unittest.mock import patch

from workbench import server


class HealthBusyTests(unittest.TestCase):
    """后端 /api/health 的 busy 判据：只有真在跑的工作才算忙。

    插件据此决定能否安全回收后端进程树；假忙会让代码更新永远不生效，漏判会
    把在途模型调用连同 agent 子进程一起杀掉（表现为 self-heal 事件停在
    outcome=diagnosing，没有 finished_at）。
    """

    def setUp(self):
        self.state = patch.object(server, "_advance_state", {}).start()
        self.queue = patch.object(server, "_advance_queue", []).start()
        self.audio = patch.object(server, "_audio_job", {"running": False, "lock": threading.Lock()}).start()
        self.upload = patch.object(server, "_upload_jobs", {}).start()
        self.leased = patch.object(server, "_operations_leased", return_value=False).start()
        self.addCleanup(patch.stopall)

    def test_idle_backend_is_not_busy(self):
        self.assertFalse(server.work_in_flight())

    def test_operations_lease_is_busy(self):
        # Restarting the backend replaces the execution process, which voids an
        # in-flight operations attempt; the host must wait for the lease instead.
        self.leased.return_value = True
        self.assertTrue(server.work_in_flight())

    def test_expired_operations_lease_is_not_busy(self):
        self.leased.return_value = False
        self.assertFalse(server.work_in_flight())

    def test_running_advance_is_busy(self):
        self.state["paper"] = {"running": True, "started_at": 1}
        self.assertTrue(server.work_in_flight())

    def test_queued_batch_is_busy(self):
        self.state["paper"] = {"running": False, "queued": True}
        self.queue.append("paper")
        self.assertTrue(server.work_in_flight())

    def test_audio_job_is_busy(self):
        self.audio["running"] = True
        self.assertTrue(server.work_in_flight())

    def test_upload_job_is_busy(self):
        # Killing an upload mid-flight leaves an unknown remote receipt behind,
        # which costs more than rerunning a local stage.
        self.upload["paper"] = {"running": True, "ok": None, "step": "开始"}
        self.assertTrue(server.work_in_flight())

    def test_finished_upload_is_not_busy(self):
        self.upload["paper"] = {"running": False, "ok": True, "step": "完成"}
        self.assertFalse(server.work_in_flight())

    def test_finished_advance_is_not_busy(self):
        self.state["paper"] = {"running": False, "result": {"action": "waiting"}}
        self.assertFalse(server.work_in_flight())

    def test_unreadable_state_is_treated_as_busy(self):
        class Broken(dict):
            def values(self):
                raise RuntimeError("state unavailable")

        with patch.object(server, "_advance_state", Broken()):
            self.assertTrue(server.work_in_flight())


if __name__ == "__main__":
    unittest.main()
