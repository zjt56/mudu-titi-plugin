"""Start a private, non-interactive SSH forward only when upload needs it."""
from __future__ import annotations

import atexit
import json
import os
import socket
import subprocess
import threading
import time
from urllib.parse import urlsplit

from workflow import upload_client

_lock = threading.Lock()
_processes = {}


class TunnelError(Exception):
    """A safe, user-facing tunnel failure (never includes SSH output)."""


def _port(value, variable):
    if not value.isascii() or not value.isdigit() or not 1 <= int(value) <= 65535:
        raise TunnelError(f"{variable} 必须是 1 到 65535 的端口号")
    return int(value)


def _listening(host, port):
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def _endpoint_reachable(host, port, timeout=2.0):
    """端到端可达性：端口 listening 不等于隧道转发正常（僵尸/悬挂 socket 会误判）。

    直接对本地端口发一个极小的 HTTP 请求；只要远端服务返回任意 HTTP 状态即视为可达。
    连接被拒/超时/无响应 → 隧道失效，需要重建。
    """
    import http.client
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        try:
            conn.request("GET", "/api/health")
            resp = conn.getresponse()
            resp.read(64)
            return True
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - 任何失败都视为不可达
        return False



def _stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _cleanup():
    with _lock:
        for process in _processes.values():
            _stop(process)
        _processes.clear()


atexit.register(_cleanup)


def ensure_upload_connection(timeout=10):
    """Ensure the configured upload target can be reached; return a safe status."""
    config = upload_client.load_config()
    target = urlsplit(config["base_url"])
    if target.scheme == "https":
        return "HTTPS 目标，无需 SSH 隧道"
    if target.scheme != "http" or target.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise TunnelError("上传目标不是受支持的本机地址")
    host = target.hostname
    port = target.port or 80
    bind = "::1" if host == "::1" else "127.0.0.1"
    with _lock:
        if _listening(host, port):
            # 端口被占不代表转发可用（可能是上一次 ssh 留下的僵尸 socket/defunct 进程）。
            # 只有端到端可访问才算就绪；否则清理残留并重建。
            if _endpoint_reachable(host, port):
                return "本机上传服务已就绪"
            stale = _processes.pop((bind, port), None)
            if stale is not None:
                _stop(stale)
        previous = _processes.pop((bind, port), None)
        if previous is not None:
            _stop(previous)
        # Re-read private settings on every click: saving in the plugin must not
        # require restarting a running backend or interrupting other work.
        settings_path = os.environ.get('MUDU_UPLOAD_SSH_CONFIG')
        settings = {}
        if settings_path:
            try:
                with open(settings_path, encoding='utf-8') as handle:
                    settings = json.load(handle)
                if not isinstance(settings, dict):
                    raise ValueError('invalid settings')
            except FileNotFoundError:
                settings_path = None
                settings = {}
            except (OSError, ValueError):
                raise TunnelError('无法读取 SSH 隧道设置，请重新保存设置') from None
        def setting(key, env_name, default=''):
            value = settings.get(key, default) if settings_path else os.environ.get(env_name, default)
            return str(value)
        destination = setting('ssh_host', 'MUDU_UPLOAD_SSH_HOST').strip()
        if not destination:
            raise TunnelError("题库连接不可用，请在题库设置的「SSH 隧道」中填写登录地址后重试")
        if destination.startswith("-") or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in destination):
            raise TunnelError("MUDU_UPLOAD_SSH_HOST 格式无效")
        ssh_port = _port(setting('ssh_port', 'MUDU_UPLOAD_SSH_PORT', '22'), 'SSH 端口')
        remote_port = _port(setting('ssh_remote_port', 'MUDU_UPLOAD_SSH_REMOTE_PORT', '89'), '远端端口')
        bind_arg = f"[{bind}]" if ':' in bind else bind
        args = ["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
                "-o", "ConnectTimeout=8", "-p", str(ssh_port),
                "-L", f"{bind_arg}:{port}:127.0.0.1:{remote_port}"]
        identity = setting('ssh_identity', 'MUDU_UPLOAD_SSH_IDENTITY')
        if identity:
            if not os.path.isfile(identity):
                raise TunnelError("SSH 密钥文件不存在；请在题库设置的「SSH 隧道」中确认路径")
            args.extend(["-i", identity, "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=accept-new"])
        args.append(destination)
        try:
            process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
        except (OSError, ValueError):
            raise TunnelError("无法启动 SSH，请检查 SSH 安装和配置") from None
        _processes[(bind, port)] = process
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                _processes.pop((bind, port), None)
                raise TunnelError("SSH 隧道启动失败，请检查主机、密钥、主机指纹和端口配置")
            if _listening(host, port) and process.poll() is None and _endpoint_reachable(host, port):
                return "SSH 隧道已就绪"
            time.sleep(0.1)
        _stop(process)
        _processes.pop((bind, port), None)
        raise TunnelError("SSH 隧道就绪超时，请检查连接和端口配置")
