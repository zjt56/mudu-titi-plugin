"""Local IPC client and durable state reader for split workbench API/Worker planes.

When ``WF_SPLIT_WORKER=1`` is set (the plugin default), ``workbench.server``
acts as a lightweight, hot-reloadable HTTP API plane and delegates background
batch execution, queue scheduling, supervisor wakeups, and upload/audio jobs to
the independent ``workbench.worker`` daemon.

When ``WF_SPLIT_WORKER=0`` (default for direct CLI/unit tests), everything runs
in-process with zero behavior change.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path


def is_split_mode() -> bool:
    return str(os.environ.get("WF_SPLIT_WORKER", "0")).strip().lower() in (
        "1", "true", "yes", "on",
    )


def runtime_dir(workflows_dir: Path | None = None) -> Path:
    explicit = os.environ.get("WF_RUNTIME_DIR")
    if explicit:
        return Path(explicit).expanduser().resolve()
    if workflows_dir is not None:
        return Path(workflows_dir).resolve().parent / "runtime"
    env_wf = os.environ.get("WF_WORKFLOWS_DIR")
    if env_wf:
        return Path(env_wf).expanduser().resolve().parent / "runtime"
    return Path(__file__).resolve().parents[2] / "runtime"


def endpoint_path(workflows_dir: Path | None = None) -> Path:
    return runtime_dir(workflows_dir) / "worker-endpoint.json"


def state_path(workflows_dir: Path | None = None) -> Path:
    return runtime_dir(workflows_dir) / "worker-state.json"


def queue_path(workflows_dir: Path | None = None) -> Path:
    return runtime_dir(workflows_dir) / "advance-queue.json"


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def _pid_alive(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


class RemoteWorkerClient:
    """Thin HTTP/JSON client + zero-socket disk snapshot reader for workbench.worker."""

    def __init__(self, workflows_dir: Path | None = None):
        self._workflows_dir = workflows_dir
        self._lock = threading.Lock()
        self._ep_mtime = -1
        self._ep_cache: dict | None = None
        self._state_mtime = -1
        self._state_cache: dict = {}

    def _load_endpoint(self) -> dict | None:
        path = endpoint_path(self._workflows_dir)
        try:
            st = path.stat()
        except OSError:
            return None
        with self._lock:
            if self._ep_cache is not None and self._ep_mtime == st.st_mtime_ns:
                return self._ep_cache
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or not data.get("port"):
            return None
        with self._lock:
            self._ep_mtime = st.st_mtime_ns
            self._ep_cache = data
        return data

    def read_state(self) -> dict:
        """Read latest worker state snapshot from disk (cached by mtime_ns)."""
        path = state_path(self._workflows_dir)
        try:
            st = path.stat()
        except OSError:
            return {}
        with self._lock:
            if self._state_mtime == st.st_mtime_ns and self._state_cache:
                cached = self._state_cache
                pid = cached.get("pid")
                if cached.get("alive") and isinstance(pid, int) and pid > 0 and not _pid_alive(pid):
                    dead = dict(cached, alive=False, busy=False, advance_state={}, queue=[],
                                upload_jobs={}, audio_job={"running": False})
                    self._state_cache = dead
                    return dead
                return cached
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        pid = data.get("pid")
        if isinstance(pid, int) and pid > 0 and not _pid_alive(pid):
            # Dead worker snapshot must not report phantom running batches.
            data = dict(data, alive=False, busy=False, advance_state={}, queue=[], upload_jobs={}, audio_job={"running": False})
        else:
            data = dict(data, alive=True)
        with self._lock:
            self._state_mtime = st.st_mtime_ns
            self._state_cache = data
        return data

    def state_seq(self) -> int:
        snap = self.read_state()
        return int(snap.get("seq") or 0)

    def _request(self, method: str, route: str, body: dict | None = None, *, timeout: float = 10.0) -> dict:
        ep = self._load_endpoint()
        if not ep:
            raise RuntimeError("后台执行进程尚未就绪（缺少 worker-endpoint.json）")
        host = ep.get("host") or "127.0.0.1"
        port = int(ep["port"])
        token = str(ep.get("token") or "")
        url = f"http://{host}:{port}{route}"
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        headers = {"X-Worker-Token": token}
        if raw is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        req = urllib.request.Request(url, data=raw, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read().decode("utf-8")
                return json.loads(text) if text.strip() else {}
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")
                parsed = json.loads(detail)
                if isinstance(parsed, dict) and parsed.get("error"):
                    raise ValueError(str(parsed["error"])) from exc
            except ValueError:
                raise
            except Exception:
                pass
            raise RuntimeError(f"Worker IPC HTTP {exc.code}: {detail[:200]}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"无法连接后台执行进程（127.0.0.1:{port}）：{exc}") from exc

    def health(self, timeout: float = 2.0) -> dict:
        return self._request("GET", "/internal/health", timeout=timeout)

    def enqueue(self, name: str, *, reset_budget: bool = True,
                operations_expectation: dict | None = None, details: dict | None = None) -> dict:
        return self._request("POST", "/internal/enqueue", {
            "name": name,
            "reset_budget": bool(reset_budget),
            "operations_expectation": operations_expectation,
            "details": details or {},
        }, timeout=15.0)

    def remove_from_queue(self, name: str, reason: str = "") -> dict:
        return self._request("POST", "/internal/remove-queue", {
            "name": name, "reason": reason,
        }, timeout=10.0)

    def cancel_batch(self, name: str, wait_seconds: float = 90.0) -> dict:
        return self._request("POST", "/internal/cancel", {
            "name": name, "wait_seconds": float(wait_seconds),
        }, timeout=max(15.0, float(wait_seconds) + 5.0))

    def wake_supervisor(self) -> dict:
        try:
            return self._request("POST", "/internal/wake", {}, timeout=3.0)
        except Exception:
            return {"ok": False}

    def drain(self, timeout: float = 5.0) -> dict:
        return self._request("POST", "/internal/drain", {}, timeout=timeout)

    def advance_state(self, name: str) -> dict:
        snap = self.read_state()
        states = snap.get("advance_state") or {}
        row = states.get(name)
        return dict(row) if isinstance(row, dict) else {"running": False}

    def upload_state(self, name: str) -> dict | None:
        snap = self.read_state()
        jobs = snap.get("upload_jobs") or {}
        row = jobs.get(name)
        return dict(row) if isinstance(row, dict) else None

    def audio_job_state(self) -> dict:
        snap = self.read_state()
        job = snap.get("audio_job")
        return dict(job) if isinstance(job, dict) else {"running": False, "kind": "", "batch": ""}

    def is_busy(self) -> bool:
        snap = self.read_state()
        return bool(snap.get("busy"))
