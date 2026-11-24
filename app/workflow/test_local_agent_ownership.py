"""Local fake processes only: POSIX lock ownership follows the executing child."""
import contextlib
import errno
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from workflow import local_agent as agent


RESPONSE = {"status": "completed", "artifacts": {}}
REAL_POPEN = subprocess.Popen
FAKE = r'''
import json, os, pathlib, sys, time
root = pathlib.Path(sys.argv[1])
(root / 'child.pid').write_text(str(os.getpid()))
sys.stdin.read()
(root / 'ready').touch()
if sys.argv[2] != 'success':
    deadline = time.monotonic() + 30
    while not (root / 'release').exists():
        if time.monotonic() > deadline:
            raise SystemExit(91)
        time.sleep(0.02)
print(json.dumps({'status': 'completed', 'artifacts': {}}))
'''
OWNER = r'''
import json, pathlib, signal, sys
from workflow import local_agent as agent
def stop(*args):
    raise KeyboardInterrupt
signal.signal(signal.SIGTERM, stop)
agent.__file__ = sys.argv[1]
root = pathlib.Path(sys.argv[2])
try:
    agent.invoke('fake task', root / 'attempt', json.loads(sys.argv[3]),
                 permit_parallel=sys.argv[4] == 'shared')
except agent.AgentError as exc:
    (root / 'outcome').write_text(exc.code)
else:
    (root / 'outcome').write_text('completed')
'''
PROBE = r'''
import sys
from workflow import local_agent as agent
agent.__file__ = sys.argv[1]
try:
    with agent._agent_lock(0.12, shared=sys.argv[2] == 'shared'):
        print('acquired')
except agent.AgentError as exc:
    print(exc.code)
'''


@unittest.skipIf(os.name == "nt", "inherited flock ownership is POSIX only")
class LocalAgentOwnershipTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mudu-lock-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.identity = str(self.root / "local_agent.py")
        self.addCleanup(patch.stopall)
        patch.object(agent, "__file__", self.identity).start()
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        self.env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)

    def config(self, root, mode="wait", timeout=15):
        return {"provider": "command",
                "command": [sys.executable, "-B", "-c", FAKE, str(root), mode],
                "timeout_seconds": timeout}

    def wait_for(self, predicate, message, timeout=8):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail(message)
            time.sleep(0.02)

    def running(self, pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        # An orphan can briefly remain a zombie until the OS reaps it; it has
        # exited and owns no FDs. Do not mistake that for a surviving process.
        with REAL_POPEN(["ps", "-o", "stat=", "-p", str(pid)],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as state:
            stdout, stderr = state.communicate(timeout=3)
            if state.returncode not in (0, 1):
                self.fail("Cannot verify child state: " + stderr)
        return bool(stdout.strip()) and not stdout.lstrip().startswith("Z")

    def probe(self, shared=False):
        result = subprocess.run(
            [sys.executable, "-B", "-c", PROBE, self.identity,
             "shared" if shared else "exclusive"],
            env=self.env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def start_owner(self, name, *, shared=False, mode="wait", timeout=15):
        root = self.root / name
        root.mkdir()
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", OWNER, self.identity, str(root),
             json.dumps(self.config(root, mode, timeout)),
             "shared" if shared else "exclusive"],
            env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        return process, root

    def ready(self, root):
        self.wait_for((root / "ready").exists, "fake child never became ready")
        return int((root / "child.pid").read_text())

    def cleanup_owner(self, process, root):
        # The owner handles TERM through invoke's cancellation path. For killed
        # owners, the fake's PID was recorded before readiness/SIGKILL.
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        finally:
            pid_path = root / "child.pid"
            if pid_path.exists():
                pid = int(pid_path.read_text())
                if self.running(pid):
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                self.wait_for(lambda: not self.running(pid),
                              "fake child survived final cleanup")
            self.assertIsNotNone(process.poll(), "owner survived final cleanup")

    def test_sigkill_owner_keeps_exclusive_lock_until_child_exits(self):
        owner, root = self.start_owner("killed-owner")
        try:
            child = self.ready(root)
            self.assertEqual(self.probe(), "lock_timeout")
            owner.kill()
            self.assertEqual(owner.wait(timeout=5), -signal.SIGKILL)
            self.assertTrue(self.running(child))
            self.assertEqual(self.probe(), "lock_timeout")
            self.assertEqual(self.probe(shared=True), "lock_timeout")
            (root / "release").touch()
            self.wait_for(lambda: not self.running(child), "orphan child did not exit")
            self.assertEqual(self.probe(), "acquired")
        finally:
            self.cleanup_owner(owner, root)

    def test_success_and_timeout_reap_child_and_release_lock(self):
        for mode in ("success", "wait"):
            with self.subTest(mode=mode):
                owner, root = self.start_owner(mode, mode=mode,
                                               timeout=0.4 if mode == "wait" else 5)
                try:
                    child = self.ready(root)
                    self.assertEqual(owner.wait(timeout=8), 0)
                    expected = "timeout" if mode == "wait" else "completed"
                    self.assertEqual((root / "outcome").read_text(), expected)
                    self.assertFalse(self.running(child))
                    self.assertEqual(self.probe(), "acquired")
                finally:
                    self.cleanup_owner(owner, root)

    def test_shared_children_overlap_and_last_survivor_holds_lock(self):
        owners = []
        try:
            for name in ("shared-one", "shared-two"):
                owner, root = self.start_owner(name, shared=True)
                owners.append((owner, root))
                self.ready(root)
            self.assertEqual(self.probe(shared=True), "acquired")
            self.assertEqual(self.probe(), "lock_timeout")
            first, second = owners
            first[0].kill()
            first[0].wait(timeout=5)
            second[0].kill()
            second[0].wait(timeout=5)
            for _, root in owners:
                self.assertTrue(self.running(int((root / "child.pid").read_text())))
            (first[1] / "release").touch()
            self.wait_for(lambda: not self.running(int((first[1] / "child.pid").read_text())),
                          "first shared child did not exit")
            self.assertEqual(self.probe(), "lock_timeout")
            self.assertEqual(self.probe(shared=True), "acquired")
            (second[1] / "release").touch()
            self.wait_for(lambda: not self.running(int((second[1] / "child.pid").read_text())),
                          "last shared child did not exit")
            self.assertEqual(self.probe(), "acquired")
        finally:
            for owner, root in owners:
                self.cleanup_owner(owner, root)

    def test_parent_context_close_does_not_unlock_inherited_reference(self):
        # Covers the explicit LOCK_UN pitfall independently of SIGKILL.
        child = None
        try:
            with agent._agent_lock(1) as handle:
                fd = handle.fileno()
                self.assertFalse(os.get_inheritable(fd))
                child = subprocess.Popen(
                    [sys.executable, "-B", "-c", "import time; time.sleep(30)"],
                    pass_fds=(fd,), start_new_session=True)
            self.assertTrue(handle.closed)
            self.assertEqual(self.probe(), "lock_timeout")
        finally:
            if child is not None:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=5)
                self.assertIsNotNone(child.poll())
        self.assertEqual(self.probe(), "acquired")

    def test_real_invokes_close_parent_fds_on_success_timeout_and_spawn_error(self):
        real_popen = subprocess.Popen
        inherited = []
        def spawn(*args, **kwargs):
            self.assertEqual(len(kwargs["pass_fds"]), 2)
            for fd in kwargs["pass_fds"]:
                self.assertFalse(os.get_inheritable(fd))
            inherited[:] = kwargs["pass_fds"]
            return real_popen(*args, **kwargs)
        for mode in ("success", "wait", "spawn-error"):
            with self.subTest(mode=mode):
                root = self.root / ("fd-" + mode)
                root.mkdir()
                with patch.object(agent.subprocess, "Popen", side_effect=spawn):
                    try:
                        if mode == "success":
                            self.assertEqual(agent.invoke("fake", root / "attempt",
                                             self.config(root, mode))["response"], RESPONSE)
                        else:
                            if mode == "spawn-error":
                                # An existing non-executable-format file passes config
                                # checks, then exec fails without starting a fake child.
                                bad = root / "bad-executable"
                                bad.write_text("not an executable format\n")
                                bad.chmod(0o700)
                                config = {"provider": "command", "command": [str(bad)]}
                            else:
                                config = self.config(root, timeout=0.3)
                            with self.assertRaises(agent.AgentError) as caught:
                                agent.invoke("fake", root / "attempt", config)
                            self.assertEqual(caught.exception.code,
                                             "spawn" if mode == "spawn-error" else "timeout")
                    finally:
                        pid_path = root / "child.pid"
                        if pid_path.exists():
                            pid = int(pid_path.read_text())
                            if self.running(pid):
                                try:
                                    os.killpg(pid, signal.SIGKILL)
                                except ProcessLookupError:
                                    pass
                            self.wait_for(lambda: not self.running(pid), "fake child leaked")
                for fd in inherited:
                    with self.assertRaises(OSError) as caught:
                        os.fstat(fd)
                    self.assertEqual(caught.exception.errno, errno.EBADF)
                self.assertEqual(self.probe(), "acquired")

    def test_mock_context_yielding_none_omits_pass_fds(self):
        real_popen = subprocess.Popen
        def spawn(*args, **kwargs):
            self.assertNotIn("pass_fds", kwargs)
            return real_popen(*args, **kwargs)
        root = self.root / "no-lock-context"
        root.mkdir()
        with patch.object(agent, "_agent_lock", return_value=contextlib.nullcontext()), \
             patch.object(agent, "_runtime_lock", return_value=contextlib.nullcontext()), \
             patch.object(agent.subprocess, "Popen", side_effect=spawn):
            try:
                result = agent.invoke("fake", root / "attempt", self.config(root, "success"))
                self.assertEqual(result["response"], RESPONSE)
            finally:
                pid_path = root / "child.pid"
                if pid_path.exists():
                    pid = int(pid_path.read_text())
                    if self.running(pid):
                        os.killpg(pid, signal.SIGKILL)
                    self.wait_for(lambda: not self.running(pid), "fake child leaked")

    def test_batch_lock_isolates_two_batches_for_invoke_self_heal_and_supervisor(self):
        from workflow import self_heal, supervisor
        batch_a = self.root / "batch-a"
        batch_b = self.root / "batch-b"
        (batch_a / "workflow").mkdir(parents=True)
        (batch_b / "workflow").mkdir(parents=True)
        code = (
            "from pathlib import Path; import sys; from workflow.local_agent import _batch_lock\n"
            "with _batch_lock(Path(sys.argv[1]), 0, shared=True):\n"
            "    print('ready', flush=True)\n"
            "    sys.stdin.read()\n"
        )
        holder = subprocess.Popen(
            [sys.executable, "-c", code, str(batch_a)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
        try:
            self.assertEqual(holder.stdout.readline().strip(), "ready")
            # Batch A is busy under its own per-batch lock.
            self.assertTrue(self_heal._busy(batch_a))
            self.assertTrue(supervisor.execution_busy(batch_a, include_global_agent=False))
            with self.assertRaises(agent.AgentError) as caught:
                with agent._batch_lock(batch_a, 0):
                    pass
            self.assertEqual(caught.exception.code, "lock_timeout")
            # Batch B is completely unblocked for lock acquisition, self_heal, supervisor, and invoke.
            self.assertFalse(self_heal._busy(batch_b))
            self.assertFalse(supervisor.execution_busy(batch_b, include_global_agent=False))
            with agent._batch_lock(batch_b, 0):
                pass
            cfg = dict(self.config(batch_b / "workflow", "success"), _content_batch_dir=str(batch_b))
            result = agent.invoke("fake", batch_b / "workflow/agent-runs/a1", cfg)
            self.assertEqual(result["response"], RESPONSE)
        finally:
            holder.communicate(timeout=5)
        self.assertEqual(holder.returncode, 0)

    def test_review_invoke_sigkill_owner_keeps_batch_lock_until_child_exits(self):
        """审核父进程被 SIGKILL 后，仍在运行的模型子进程必须继续持有同批次 _batch_lock 共享锁。"""
        batch_a = self.root / "review-batch-a"
        batch_b = self.root / "review-batch-b"
        (batch_a / "workflow").mkdir(parents=True)
        (batch_b / "workflow").mkdir(parents=True)
        owner_code = (
            "import json, pathlib, sys\n"
            "from workflow import local_agent, review_rounds\n"
            "batch = pathlib.Path(sys.argv[1])\n"
            "root = pathlib.Path(sys.argv[2])\n"
            "cfg = json.loads(sys.argv[3])\n"
            "local_agent.__file__ = sys.argv[4]\n"
            "review_rounds._invoke_review(batch, 'fake review', root / 'attempt', cfg, permit_parallel=True)\n"
        )
        probe_code = (
            "import pathlib, sys\n"
            "from workflow import local_agent as agent\n"
            "try:\n"
            "    with agent._batch_lock(pathlib.Path(sys.argv[1]), 0.12, shared=sys.argv[2] == 'shared'):\n"
            "        print('acquired')\n"
            "except agent.AgentError as exc:\n"
            "    print(exc.code)\n"
        )
        def probe_batch(batch_dir, shared=False):
            res = subprocess.run(
                [sys.executable, "-B", "-c", probe_code, str(batch_dir), "shared" if shared else "exclusive"],
                env=self.env, capture_output=True, text=True, timeout=5,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            return res.stdout.strip()

        root = self.root / "review-killed-owner"
        root.mkdir()
        owner = subprocess.Popen(
            [sys.executable, "-B", "-c", owner_code, str(batch_a), str(root),
             json.dumps(self.config(root, "wait", timeout=15)), self.identity],
            env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            child = self.ready(root)
            self.assertEqual(probe_batch(batch_a), "lock_timeout")
            self.assertEqual(probe_batch(batch_b), "acquired")
            owner.kill()
            self.assertEqual(owner.wait(timeout=5), -signal.SIGKILL)
            self.assertTrue(self.running(child))
            with self.assertRaisesRegex(agent.AgentError, 'source installation lock'):
                with agent._runtime_lock(0, shared=False):
                    self.fail('Installer entered while the review child held the runtime lock')
            # 父进程异常退出后，同批次独占锁仍被活着的模型子进程持有；他批次不受影响
            self.assertEqual(probe_batch(batch_a), "lock_timeout")
            self.assertEqual(probe_batch(batch_a, shared=True), "acquired")
            self.assertEqual(probe_batch(batch_b), "acquired")
            (root / "release").touch()
            self.wait_for(lambda: not self.running(child), "orphan review child did not exit")
            self.assertEqual(probe_batch(batch_a), "acquired")
            with agent._runtime_lock(0, shared=False):
                pass
        finally:
            self.cleanup_owner(owner, root)


if __name__ == "__main__":
    unittest.main()
