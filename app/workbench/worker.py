"""Independent background execution & supervisor daemon for the workbench.

Runs in its own OS process (separate POSIX process group and Python GIL from
``workbench.server``) so hot-reloading or restarting the HTTP API plane never
interrupts in-flight workflow stages, audio cuts, or uploads.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import signal
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

# The worker process itself ALWAYS runs execution locally in-process.
os.environ["WF_SPLIT_WORKER"] = "0"

from workbench import execution_client as ec
from workbench import server as srv


_STATE_SEQ = 0
_STATE_LOCK = threading.Lock()
_DRAINING = threading.Event()
_STOP_EVENT = threading.Event()


def _serialize_advance_state() -> dict:
    with srv._lock:
        snapshot = {}
        for k, v in srv._advance_state.items():
            if isinstance(v, dict):
                snapshot[k] = {sk: sv for sk, sv in v.items() if isinstance(sv, (str, int, float, bool, type(None), list, dict))}
        return snapshot


def _serialize_queue() -> list:
    with srv._advance_queue_lock:
        out = []
        for entry in srv._advance_queue:
            if isinstance(entry, dict):
                out.append(str(entry.get("name") or ""))
            else:
                out.append(str(entry))
        return [x for x in out if x]


def _serialize_upload_jobs() -> dict:
    with srv._lock:
        out = {}
        for k, v in getattr(srv, "_upload_jobs", {}).items():
            if isinstance(v, dict):
                out[k] = {sk: sv for sk, sv in v.items() if isinstance(sv, (str, int, float, bool, type(None), list, dict))}
        return out


def _serialize_audio_job() -> dict:
    job = getattr(srv, "_audio_job", {}) or {}
    return {
        "running": bool(job.get("running")),
        "kind": str(job.get("kind") or ""),
        "batch": str(job.get("batch") or ""),
    }


def _is_busy() -> bool:
    adv = _serialize_advance_state()
    if any(bool(v.get("running") or v.get("queued")) for v in adv.values()):
        return True
    if _serialize_queue():
        return True
    if any(bool(v.get("running")) for v in _serialize_upload_jobs().values()):
        return True
    if _serialize_audio_job().get("running"):
        return True
    return False


def publish_state() -> dict:
    """Atomically write worker-state.json and advance-queue.json for zero-IPC API reads."""
    global _STATE_SEQ
    with _STATE_LOCK:
        adv = _serialize_advance_state()
        queue = _serialize_queue()
        uploads = _serialize_upload_jobs()
        audio = _serialize_audio_job()
        busy = bool(
            any(v.get("running") or v.get("queued") for v in adv.values())
            or queue
            or any(v.get("running") for v in uploads.values())
            or audio.get("running")
        )
        _STATE_SEQ += 1
        seq = _STATE_SEQ
        payload = {
            "seq": seq,
            "pid": os.getpid(),
            "updated_at": srv.now(),
            "busy": busy,
            "draining": _DRAINING.is_set(),
            "advance_state": adv,
            "queue": queue,
            "upload_jobs": uploads,
            "audio_job": audio,
        }
        try:
            ec.atomic_write_json(ec.state_path(srv.WORKFLOWS), payload)
            # While draining, keep the preserved pending queue in advance-queue.json
            # so the respawned worker restores it on startup.
            if not _DRAINING.is_set():
                ec.atomic_write_json(ec.queue_path(srv.WORKFLOWS), {
                    "updated_at": payload["updated_at"],
                    "items": queue,
                })
        except OSError:
            pass
        return payload


def _state_sync_loop():
    last_digest = ""
    while not _STOP_EVENT.is_set():
        try:
            adv = _serialize_advance_state()
            queue = _serialize_queue()
            uploads = _serialize_upload_jobs()
            audio = _serialize_audio_job()
            digest = json.dumps([adv, queue, uploads, audio, _DRAINING.is_set()],
                                ensure_ascii=False, sort_keys=True, default=str)
            if digest != last_digest:
                publish_state()
                last_digest = digest
        except Exception:
            pass
        _STOP_EVENT.wait(0.4)


def _restore_persisted_queue():
    path = ec.queue_path(srv.WORKFLOWS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return
    for name in items:
        if not isinstance(name, str) or not name.strip():
            continue
        try:
            srv.start_advance(name, reset_budget=False)
        except Exception:
            pass


def _make_handler(token: str, instance_id: str, started_at: float, server_ref: list):
    class WorkerHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def _send(self, code: int, data: dict):
            raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _authorized(self) -> bool:
            if not token:
                return True
            got = self.headers.get("X-Worker-Token") or ""
            return hmac.compare_digest(got, token)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(min(length, 2 * 1024 * 1024))
            try:
                val = json.loads(raw.decode("utf-8"))
                return val if isinstance(val, dict) else {}
            except (UnicodeError, ValueError):
                return {}

        def do_GET(self):
            if not self._authorized():
                return self._send(403, {"error": "forbidden"})
            route = urlparse(self.path).path
            if route == "/internal/health":
                return self._send(200, {
                    "ok": True,
                    "pid": os.getpid(),
                    "instance_id": instance_id,
                    "started_at": started_at,
                    "busy": _is_busy(),
                    "draining": _DRAINING.is_set(),
                })
            if route == "/internal/state":
                return self._send(200, publish_state())
            return self._send(404, {"error": "not_found"})

        def do_POST(self):
            if not self._authorized():
                return self._send(403, {"error": "forbidden"})
            route = urlparse(self.path).path
            body = self._read_json()
            try:
                if route == "/internal/enqueue":
                    if _DRAINING.is_set():
                        return self._send(503, {"error": "worker_draining"})
                    name = str(body.get("name") or "")
                    reset_budget = bool(body.get("reset_budget", True))
                    ops_exp = body.get("operations_expectation")
                    details = body.get("details") if isinstance(body.get("details"), dict) else {}
                    folder = srv.batch_dir(name)
                    if reset_budget and (folder / srv.QUEUE_REMOVED).is_file():
                        srv.restore_queue(name)
                    res = srv._launch_advance(
                        name, folder,
                        operations_expectation=ops_exp,
                        reset_budget=reset_budget,
                        **details,
                    )
                    publish_state()
                    return self._send(200, res)
                if route == "/internal/remove-queue":
                    name = str(body.get("name") or "")
                    reason = str(body.get("reason") or "")
                    res = srv.remove_from_queue(name, reason=reason)
                    publish_state()
                    return self._send(200, res)
                if route == "/internal/cancel":
                    name = str(body.get("name") or "")
                    wait_seconds = float(body.get("wait_seconds") or 90.0)
                    folder = srv.batch_dir(name)
                    res = srv._cancel_running_batch(name, folder, wait_seconds=wait_seconds)
                    publish_state()
                    return self._send(200, res)
                if route == "/internal/wake":
                    srv._wake_supervisor()
                    return self._send(200, {"ok": True})
                if route == "/internal/drain":
                    _DRAINING.set()
                    # Snapshot and freeze pending queued batches to disk so no new
                    # batch starts during drain; the respawned worker restores them.
                    with srv._advance_queue_lock:
                        pending_names = [
                            str(entry.get("name") if isinstance(entry, dict) else entry)
                            for entry in srv._advance_queue
                        ]
                        pending_names = [n for n in pending_names if n]
                        srv._advance_queue.clear()
                    try:
                        ec.atomic_write_json(ec.queue_path(srv.WORKFLOWS), {
                            "updated_at": srv.now(),
                            "items": pending_names,
                        })
                    except OSError:
                        pass
                    publish_state()
                    if srv._workflow_supervisor is not None:
                        try:
                            srv._workflow_supervisor.stop()
                        except Exception:
                            pass
                    def _shutdown_when_idle():
                        # Match max stage timeout (900s) so an in-flight stage finishes cleanly.
                        deadline = time.monotonic() + 900.0
                        while _is_busy() and time.monotonic() < deadline and not _STOP_EVENT.is_set():
                            time.sleep(0.5)
                        _STOP_EVENT.set()
                        if server_ref:
                            try:
                                server_ref[0].shutdown()
                            except Exception:
                                pass
                    threading.Thread(target=_shutdown_when_idle, daemon=True, name="worker-drain").start()
                    return self._send(200, {"ok": True, "draining": True, "busy": _is_busy(),
                                            "preserved_queue": pending_names})
                return self._send(404, {"error": "not_found"})
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            except Exception as exc:
                return self._send(500, {"error": str(exc)})

    return WorkerHandler


def run_worker(host: str = "127.0.0.1", port: int = 0) -> None:
    token = os.environ.get("WF_WORKER_TOKEN") or uuid.uuid4().hex
    instance_id = os.environ.get("WF_WORKER_INSTANCE_ID") or uuid.uuid4().hex
    started_at = time.time()
    server_ref: list = []

    class WorkerHTTPServer(ThreadingHTTPServer):
        request_queue_size = 32
        allow_reuse_address = True
        daemon_threads = True

    handler_cls = _make_handler(token, instance_id, started_at, server_ref)
    httpd = WorkerHTTPServer((host, port), handler_cls)
    server_ref.append(httpd)
    actual_port = httpd.server_address[1]

    ep_file = ec.endpoint_path(srv.WORKFLOWS)
    ec.atomic_write_json(ep_file, {
        "host": host,
        "port": actual_port,
        "token": token,
        "pid": os.getpid(),
        "instance_id": instance_id,
        "started_at": started_at,
    })

    # Start WorkflowSupervisor + CHANGELOG pruning inside worker daemon.
    from dataclasses import replace as _dc_replace
    from workflow import supervisor as wf_supervisor
    from workflow.supervisor import SupervisorConfig, WorkflowSupervisor

    max_concurrent = srv._max_concurrent_batches()
    sup_cfg = SupervisorConfig.from_env()
    if max_concurrent > sup_cfg.max_dispatch_per_tick:
        sup_cfg = _dc_replace(sup_cfg, max_dispatch_per_tick=max_concurrent)

    def _guarded_dispatch(name):
        if _DRAINING.is_set():
            return {"started": False, "draining": True}
        res = srv._dispatch_supervised(name)
        publish_state()
        return res

    supervisor = WorkflowSupervisor(
        srv.WORKFLOWS,
        _guarded_dispatch,
        is_active=lambda name: bool(
            (srv._advance_state.get(name) or {}).get("running")
            or (srv._upload_jobs.get(name) or {}).get("running")
        ),
        config=sup_cfg,
        busy=lambda f: wf_supervisor.execution_busy(f, include_global_agent=(max_concurrent <= 1)),
    )
    srv._workflow_supervisor = supervisor

    srv._changelog_prune_halt.clear()
    prune_thread = threading.Thread(target=srv._changelog_prune_loop, daemon=True, name="worker-changelog-prune")
    prune_thread.start()

    def _handle_signal(_signum, _frame):
        _STOP_EVENT.set()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    print(f"Workbench Worker: http://{host}:{actual_port} (pid={os.getpid()})", flush=True)
    # Restore persisted queue BEFORE starting state sync / initial publish_state(),
    # otherwise publishing an empty startup queue would overwrite advance-queue.json.
    _restore_persisted_queue()
    publish_state()
    sync_thread = threading.Thread(target=_state_sync_loop, daemon=True, name="worker-state-sync")
    sync_thread.start()
    try:
        supervisor.start()
        httpd.serve_forever()
    finally:
        _STOP_EVENT.set()
        srv._changelog_prune_halt.set()
        try:
            supervisor.stop()
        except Exception:
            pass
        srv._workflow_supervisor = None
        httpd.server_close()
        try:
            cur = json.loads(ep_file.read_text(encoding="utf-8"))
            if isinstance(cur, dict) and cur.get("instance_id") == instance_id:
                ep_file.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass


def main():
    parser = argparse.ArgumentParser(description="木牍录题后台任务调度与执行守护进程")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    run_worker(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
