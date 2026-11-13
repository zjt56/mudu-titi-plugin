# -*- coding: utf-8 -*-
"""隔离代码修复工作区（Operations 附加能力）。

设计边界（很重要）：

* 本模块只操作**临时副本**。``create`` 复制当前运行源码（含未提交改动）到
  独立目录；模型在其中读写、补测试、跑指定 unittest。
* 默认**不安装**到运行目录。``prepare_install`` 只生成带哈希的安装计划；
  ``install`` 需要显式 ``allow=True`` 或本源码根已持久启用本地自治授权；授权
  不改变测试、保护文件与逐字节 CAS 约束。安装前写 journal、备份原文件，失败回滚。
* ``set_autonomy`` 是受控宿主的显式本地授权 API，不能暴露给修复模型；配置仅
  绑定一个源码根，不随隔离副本复制，不授权上传、发布或直接修改用户批次。
* 这不是操作系统级沙箱。模型写的 Python 测试本身就是可执行代码，只能在
  “信任该补丁来源/另有外部隔离”的前提下运行；本模块不声称提供进程、网络或
  凭据隔离。宿主若要安装，必须先暂停源码监听、排空执行队列并在安装后单次
  重启，否则会出现半更新状态。
* 控制面文件不可改：Operations 自身、阶段契约、清单、调度、runner、上传与
  发布边界。测试文件可新增或修改，但不得删除生产模块。
"""
from __future__ import annotations

import codecs
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

MAX_FILE_BYTES = 2_000_000
MAX_PATCH_CHARS = 400_000
COPY_LIMIT = 8_000_000
LOCKED = frozenset((
    "app/workflow/operations.py", "app/workflow/operations_development.py",
    "app/workflow/contracts.py", "app/workflow/manifest.py",
    "app/workflow/supervisor.py", "app/workflow/auto.py",
    "app/workflow/runner.py", "app/workflow/local_agent.py",
    "app/workflow/self_heal.py",
))
PRODUCTION = re.compile(r"app/workflow/[a-z][a-z0-9_]*\.py")
TEST = re.compile(r"app/workflow/test_[a-z0-9_]*\.py")
UNIT_TEST = re.compile(r"workflow\.[a-z][a-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,2}")
SKIP_DIRS = frozenset(("__pycache__", ".git", ".mypy_cache", ".pytest_cache",
                       "node_modules", "workflows", ".venv", "venv", ".learnings"))
SECRET_MARKERS = ("secret", "credential", ".pem", ".key", "id_rsa", "password", "token")
AUTONOMY_CONFIG = ".operations-autonomy.json"
AUTONOMY_SCHEMA = "md-operations-autonomy/v1"


def autonomy_config(source_root):
    """Read local, checkout-bound authorization. Missing means disabled.

    Invalid or foreign configuration fails closed, even when the legacy switch
    is present. This file is local state, never a model-editable repair file.
    """
    root = _real(source_root)
    path = _safe(root, AUTONOMY_CONFIG)
    disabled = {"schema": AUTONOMY_SCHEMA, "source_root": str(root),
                "enabled": False, "config_path": str(path)}
    if not path.exists():
        return disabled
    if not path.is_file() or path.stat().st_size > 16000:
        raise ValueError("autonomy_config_invalid")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("autonomy_config_invalid") from exc
    if (not isinstance(value, dict) or value.get("schema") != AUTONOMY_SCHEMA
            or type(value.get("enabled")) is not bool
            or not isinstance(value.get("source_root"), str)
            or _real(value["source_root"]) != root
            or not isinstance(value.get("authorization"), str)
            or not value["authorization"].strip()):
        raise ValueError("autonomy_config_invalid")
    return {**value, "config_path": str(path)}


def set_autonomy(source_root, *, enabled, authorization):
    """Persist explicit user authorization from a trusted local Host/settings API.

    Do not expose this method as an Operations model tool. Calling it requires
    an actual user decision; repair requests cannot enable their own installer.
    """
    if type(enabled) is not bool:
        raise ValueError("autonomy_enabled_must_be_boolean")
    if not isinstance(authorization, str) or not authorization.strip() or len(authorization) > 2000:
        raise ValueError("autonomy_authorization_required")
    root = _real(source_root)
    if not (root / "app" / "workflow").is_dir():
        raise ValueError("source_root_invalid")
    path = _safe(root, AUTONOMY_CONFIG)
    value = {"schema": AUTONOMY_SCHEMA, "source_root": str(root),
             "enabled": enabled, "authorization": authorization.strip(), "updated_at": _now()}
    _replace_bytes(path, json.dumps(value, ensure_ascii=False, indent=1).encode("utf-8"), suffix=".autonomy")
    return {**value, "config_path": str(path)}


def install_authorization(source_root, *, confirm=False):
    """Authorize automatic local install or the compatible manual legacy mode.

    Autonomy removes repeated confirmation and environment prerequisites only.
    The Host still holds the execution lock and handles restart plus reverify.
    """
    config = autonomy_config(source_root)
    autonomous = config["enabled"] is True
    legacy = confirm is True and os.environ.get("MD_OPERATIONS_INSTALL") == "1"
    return {"allowed": autonomous or legacy, "autonomous": autonomous,
            "mode": "local_autonomy" if autonomous else "legacy_confirmed" if legacy else "disabled",
            "config_path": config["config_path"],
            "authorization": config.get("authorization") if autonomous else None}


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _real(path):
    return Path(os.path.realpath(os.fspath(path)))


def _inside(root, path):
    root, path = _real(root), _real(path)
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _regular(path):
    """Reject links/junctions and non-regular files before reading or writing."""
    path = Path(path)
    if path.is_symlink():
        return None
    path = _real(path)
    return path if path.is_file() else None


def allowed(relative):
    relative = str(relative).replace("\\", "/")
    if relative in LOCKED or not (PRODUCTION.fullmatch(relative) or TEST.fullmatch(relative)):
        return False
    return True


def classify(relative):
    relative = str(relative).replace("\\", "/")
    if relative in LOCKED:
        return "locked"
    if PRODUCTION.fullmatch(relative):
        return "production"
    if TEST.fullmatch(relative):
        return "test"
    return "other"


def _safe(workspace, relative):
    relative = str(relative or "").replace("\\", "/")
    if (not relative or relative.startswith("/") or ".." in relative.split("/")
            or re.match(r"^[A-Za-z]:", relative)):
        raise ValueError("unsafe_path")
    lexical = Path(workspace) / relative
    # Refuse a link at any component before resolving it; otherwise realpath
    # would silently follow it out of the workspace.
    current = lexical
    root = Path(workspace)
    while current != root:
        if current.is_symlink():
            raise ValueError("symlink_refused")
        current = current.parent
    path = _real(lexical)
    if not _inside(workspace, path):
        raise ValueError("path_escape")
    return path


def _copy(source, destination):
    """Copy code only; skip user batches, VCS data, caches and obvious secrets."""
    source, destination = _real(source), _real(destination)
    if _inside(destination, source) or _inside(source, destination):
        raise ValueError("copy_overlap")
    destination.mkdir(parents=True, exist_ok=False)
    total = 0
    for current, dirs, files in os.walk(source):
        current = Path(current)
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not (current / d).is_symlink()]
        relative = current.relative_to(source)
        (destination / relative).mkdir(parents=True, exist_ok=True)
        for name in files:
            path = current / name
            lowered = name.lower()
            if (path.is_symlink() or name.startswith(".")
                    or any(marker in lowered for marker in SECRET_MARKERS)):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > COPY_LIMIT or total + size > 400_000_000:
                continue
            try:
                shutil.copy2(path, destination / relative / name)
            except OSError:
                continue
            total += size
    # Git metadata is deliberately absent: the copy must never be committed or
    # used to rewrite the real repository.
    return destination


def files(root):
    """Allowlisted source files currently present in ``root`` (repo root)."""
    result = {}
    base = Path(root) / "app" / "workflow"
    if not base.is_dir():
        return result
    for path in sorted(base.glob("*.py")):
        relative = "app/workflow/" + path.name
        if allowed(relative) and path.is_file() and not path.is_symlink():
            result[relative] = path
    return result


def capture(root, *, head=None):
    """Capture exact bytes/hashes of the code that is running now, including
    dirty and untracked files. Not ``git archive HEAD``: uncommitted fixes are
    part of the running behaviour and must be the repair baseline."""
    root = Path(root)
    entries, contents = {}, {}
    for relative, path in files(root).items():
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if len(raw) > MAX_FILE_BYTES:
            continue
        entries[relative] = {"sha256": _sha(raw), "bytes": len(raw)}
        contents[relative] = raw
    fingerprint = _sha(json.dumps(entries, sort_keys=True).encode())
    return {"files": entries, "contents": contents, "code_fingerprint": fingerprint,
            "head": head, "captured_at": _now()}


def create(root, parent, *, head=None):
    """Create a disposable copy of the running checkout and its code baseline."""
    root = Path(root)
    if not (root / "app" / "workflow").is_dir():
        raise ValueError("source_root_invalid")
    parent = Path(parent)
    parent.mkdir(parents=True, exist_ok=True)
    job_id = "cr-" + uuid.uuid4().hex[:16]
    workspace = parent / job_id
    _copy(root, workspace)
    base = capture(root, head=head)
    for relative, raw in base["contents"].items():
        target = _safe(workspace, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        pristine = _safe(workspace, ".base/" + relative)
        pristine.parent.mkdir(parents=True, exist_ok=True)
        pristine.write_bytes(raw)
    job = {"job_id": job_id, "workspace": str(workspace), "source_root": str(root),
           "created_at": _now(), "base": {k: base[k] for k in ("files", "code_fingerprint", "head", "captured_at")},
           "writes": {}, "tests": [], "plan": None, "install": None}
    _save(job, parent / (job_id + ".json"))
    return job


def _save(job, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temporary, path)


def load(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not value.get("job_id") or not value.get("workspace"):
        raise ValueError("job_invalid")
    return value


def save(job, path):
    _save(job, path)
    return path


def _workspace(job):
    workspace = Path(job["workspace"])
    if not workspace.is_dir():
        raise ValueError("workspace_missing")
    return workspace


def fingerprint(job):
    workspace = _workspace(job)
    entries = {}
    for path in sorted((workspace / "app" / "workflow").glob("*.py")):
        relative = "app/workflow/" + path.name
        if allowed(relative):
            try:
                entries[relative] = _sha(path.read_bytes())
            except OSError:
                entries[relative] = "missing"
    return _sha(json.dumps(entries, sort_keys=True).encode())


READ_CHARS = 12000


def _read_source(job, relative):
    relative = str(relative or "").replace("\\", "/")
    path = _safe(_workspace(job), relative)
    if not allowed(relative):
        raise ValueError("source_not_allowlisted")
    path = _regular(path)
    if path is None:
        raise ValueError("file_missing")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("source_too_large")
    raw = path.read_bytes()
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("source_too_large")
    return relative, raw


def read(job, relative, *, offset=0, limit=READ_CHARS):
    """Legacy offsets are bytes; continuation offsets always end on UTF-8 boundaries."""
    relative, raw = _read_source(job, relative)
    requested = max(0, min(int(offset), 10_000_000))
    start = min(requested, len(raw))
    # Old callers may seek into a multibyte character. Skip its remaining bytes
    # rather than introduce a replacement character into valid source code.
    while start < len(raw) and raw[start] & 0xC0 == 0x80:
        start += 1
    limit = max(1, min(int(limit), READ_CHARS))
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    chunks, count, end = [], 0, start
    while end < len(raw) and count < limit:
        piece = decoder.decode(raw[end:end + 1])
        end += 1
        if piece:
            chunks.append(piece)
            count += len(piece)
    if end == len(raw):
        chunks.append(decoder.decode(b"", final=True))
    text = "".join(chunks)
    return {"path": relative, "requested_offset": requested, "offset": start,
            "offset_unit": "bytes", "next_offset": end, "text": text,
            "limit": limit, "eof": end == len(raw), "truncated": end < len(raw),
            "sha256": _sha(raw)}


def read_lines(job, relative, *, start_line=1, limit=80):
    """Read numbered lines with an explicit continuation and a bounded response."""
    relative, raw = _read_source(job, relative)
    lines = raw.decode("utf-8", "replace").splitlines(keepends=True)
    start = max(1, int(start_line))
    limit = max(1, min(int(limit), 400))
    selected, size = [], 0
    for index in range(start - 1, min(len(lines), start - 1 + limit)):
        line = lines[index]
        if size + len(line) > READ_CHARS:
            if not selected:
                raise ValueError("line_exceeds_read_limit: use read with byte offsets")
            break
        selected.append({"line": index + 1, "text": line})
        size += len(line)
    next_line = start + len(selected)
    eof = next_line > len(lines)
    return {"path": relative, "start_line": start, "lines": selected,
            "text": "".join(row["text"] for row in selected), "limit": limit,
            "next_line": next_line, "eof": eof, "truncated": not eof,
            "total_lines": len(lines), "sha256": _sha(raw)}


def search(job, query, *, relative=None, cursor=None, start_line=1,
           max_matches=20, context=2):
    """Bounded literal source search; never evaluate patterns or execute commands."""
    if not isinstance(query, str) or not query or len(query) > 500 or "\n" in query:
        raise ValueError("search_query_invalid")
    if cursor is not None and (not isinstance(cursor, dict)
                               or set(cursor) != {"path", "start_line"}):
        raise ValueError("search_cursor_invalid")
    if relative is not None:
        name, _ = _read_source(job, relative)
        names = [name]
    else:
        names = sorted(files(_workspace(job)))
    begin = max(1, int(start_line))
    if cursor:
        name, _ = _read_source(job, cursor["path"])
        if name not in names:
            raise ValueError("search_cursor_invalid")
        names = names[names.index(name):]
        begin = max(1, int(cursor["start_line"]))
    maximum = max(1, min(int(max_matches), 80))
    context = max(0, min(int(context), 3))
    matches, used, scanned = [], 0, 0
    for file_index, name in enumerate(names):
        name, raw = _read_source(job, name)
        if scanned + len(raw) > 16_000_000:
            return {"query": query, "matches": matches, "truncated": True,
                    "next_cursor": {"path": name, "start_line": begin if file_index == 0 else 1}}
        scanned += len(raw)
        lines = raw.decode("utf-8", "replace").splitlines()
        for index in range(begin - 1 if file_index == 0 else 0, len(lines)):
            if query not in lines[index]:
                continue
            # Clip unusually long individual lines explicitly, preserving line location.
            lo, hi = max(0, index - context), min(len(lines), index + context + 1)
            rows = [{"line": at + 1, "text": lines[at][:1000],
                     "truncated": len(lines[at]) > 1000} for at in range(lo, hi)]
            cost = sum(len(row["text"]) for row in rows)
            if len(matches) >= maximum or used + cost > READ_CHARS:
                return {"query": query, "matches": matches, "truncated": True,
                        "next_cursor": {"path": name, "start_line": index + 1}}
            matches.append({"path": name, "line": index + 1,
                            "context": rows, "sha256": _sha(raw)})
            used += cost
    return {"query": query, "matches": matches, "truncated": False, "next_cursor": None}


def write(job, relative, content, *, expected_sha=None):
    relative = str(relative).replace("\\", "/")
    if not allowed(relative):
        raise ValueError("source_not_allowlisted")
    if not isinstance(content, str) or len(content.encode()) > MAX_FILE_BYTES:
        raise ValueError("content_invalid")
    path = _safe(_workspace(job), relative)
    current = path.read_bytes() if path.is_file() else None
    if expected_sha is not None:
        actual = _sha(current) if current is not None else "missing"
        if actual != expected_sha:
            raise ValueError("expected_sha_mismatch")
    elif current is not None:
        raise ValueError("expected_sha_required")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="")
    os.replace(temporary, path)
    job.setdefault("writes", {})[relative] = _sha(path.read_bytes())
    job["plan"] = None
    return {"path": relative, "sha256": job["writes"][relative], "written": True}


def diff(job):
    workspace, changes, chunks = _workspace(job), [], []
    base = job["base"]["files"]
    temporary = None
    for relative in sorted(set(base) | set(job.get("writes", {}))):
        path = _safe(workspace, relative)
        raw = path.read_bytes() if path.is_file() else None
        old_sha = (base.get(relative) or {}).get("sha256", "missing")
        new_sha = _sha(raw) if raw is not None else "missing"
        if old_sha == new_sha:
            continue
        changes.append({"path": relative, "base_sha256": old_sha, "new_sha256": new_sha,
                        "kind": classify(relative)})
        if len(chunks) < 40:
            before = _base_text(job, relative).splitlines(keepends=True)
            after = (raw or b"").decode("utf-8", "replace").splitlines(keepends=True)
            chunks.extend(difflib.unified_diff(before, after, fromfile="a/" + relative,
                                               tofile="b/" + relative, n=3))
    patch = "".join(chunks)
    return {"files": changes, "patch": patch[:MAX_PATCH_CHARS],
            "truncated": len(patch) > MAX_PATCH_CHARS,
            "fingerprint": fingerprint(job)}


def _base_text(job, relative):
    """Baseline text comes from the pristine copy, never from a moved source."""
    pristine = _regular(Path(job["workspace"]) / ".base" / relative)
    if pristine is not None:
        return pristine.read_text(encoding="utf-8", errors="replace")
    return ""


def _clean_env(home, app_dir):
    environment = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if any(marker in upper for marker in ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "API_KEY", "AUTH")) \
                or upper.startswith("DSH_") or upper == "MD_OPERATIONS_INSTALL":
            continue
        environment[key] = value
    environment.update({"PYTHONPATH": str(app_dir), "PYTHONDONTWRITEBYTECODE": "1",
                        "PYTHONNOUSERSITE": "1", "PYTHONIOENCODING": "utf-8",
                        "HOME": str(home), "USERPROFILE": str(home), "TMPDIR": str(home), "TEMP": str(home), "TMP": str(home)})
    return environment


def run_tests(job, tests, *, timeout=300):
    """Run only named unittest modules inside the copy. Fixed argv, no shell."""
    workspace = _workspace(job)
    if not isinstance(tests, list) or not tests or len(tests) > 12:
        raise ValueError("tests_invalid")
    names = []
    for entry in tests:
        if not isinstance(entry, str) or not UNIT_TEST.fullmatch(entry):
            raise ValueError("tests_invalid")
        names.append(entry)
    home = workspace / ".test-home"
    home.mkdir(exist_ok=True)
    environment = _clean_env(home, workspace / "app")
    started = time.time()
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "unittest", *names], cwd=workspace / "app",
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=max(30, min(int(timeout), 900)), env=environment,
            stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        code, output = completed.returncode, (completed.stdout or "") + (completed.stderr or "")
    except subprocess.TimeoutExpired as exc:
        code, output = -1, ((exc.stdout or "") + (exc.stderr or "")) if isinstance(exc.stdout, str) else "timeout"
    record = {"tests": names, "returncode": code, "passed": code == 0,
              "output": output[-8000:], "seconds": round(time.time() - started, 1),
              "fingerprint": fingerprint(job), "at": _now()}
    job.setdefault("tests", []).append(record)
    job["tests"] = job["tests"][-20:]
    return record


def prepare_install(job, *, require_tests=True, require_regression=True):
    """Freeze a CAS install plan. Never writes the running checkout."""
    workspace, root = _workspace(job), Path(job["source_root"])
    changes = diff(job)["files"]
    if not changes:
        raise ValueError("no_changes")
    if any(change["kind"] == "locked" for change in changes):
        raise ValueError("locked_file_changed")
    if any(change["new_sha256"] == "missing" for change in changes):
        raise ValueError("deletion_not_allowed")
    last = (job.get("tests") or [{}])[-1]
    if require_tests and not (last.get("passed") and last.get("fingerprint") == fingerprint(job)):
        raise ValueError("passing_tests_required")
    if last.get("passed"):
        names = last.get("tests") or []
        modules = {"app/" + name.split(".")[0] + "/" + name.split(".")[1] + ".py"
                   for name in names if name.count(".") >= 1}
        changed_tests = {c["path"] for c in changes if TEST.fullmatch(c["path"])}
        if require_regression and not modules:
            raise ValueError("regression_test_required")
        if changed_tests and not changed_tests <= modules:
            raise ValueError("changed_tests_not_run")
    for change in changes:
        relative = change["path"]
        _safe(workspace, relative)
        target = _regular(_safe(root, relative))
        if target is None and change["base_sha256"] != "missing":
            raise ValueError("source_missing:" + relative)
        if target is not None and _sha(target.read_bytes()) != change["base_sha256"]:
            raise ValueError("source_changed:" + relative)
    plan = {"plan_id": "plan-" + uuid.uuid4().hex[:12], "changes": changes,
            "code_fingerprint": fingerprint(job), "base_code_fingerprint": job["base"]["code_fingerprint"],
            "tests": last.get("tests"), "prepared_at": _now()}
    job["plan"] = plan
    return plan


def _journal(backup_dir, plan):
    path = Path(backup_dir) / (plan["plan_id"] + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


RELEASE_MARKER = ".operations-release.json"


def _replace_bytes(target, raw, *, suffix):
    """Replace from an exclusively created sibling, never a predictable link."""
    target = Path(target)
    descriptor, name = tempfile.mkstemp(prefix=target.name + ".", suffix=suffix, dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
        os.replace(temporary, target)
    finally:
        # This is the exact exclusive temporary created above, not a caller path.
        if temporary.exists():
            temporary.unlink()


def _release_marker(root, state, **extra):
    """Tell the Host a release is in flight so it can pause its watcher and
    perform exactly one restart. The Host owns removal after a healthy boot."""
    path = _safe(root, RELEASE_MARKER)
    body = {"state": state, "at": _now(), **extra}
    _replace_bytes(path, json.dumps(body, ensure_ascii=False, indent=1).encode("utf-8"), suffix=".release")
    return path


def install(job, *, backup_dir, allow=False):
    """Journaled install under explicit authorization or local autonomy.

    ``allow=True`` remains the trusted Host's legacy API. Autonomous calls leave
    it false so the local switch is read again at the point of installation.
    The Host must hold the execution lock and handle one restart and reverify.
    """
    root = Path(job["source_root"])
    authorization = {"allowed": True, "autonomous": False, "mode": "explicit_host"}
    if allow is not True:
        authorization = install_authorization(root)
        if not authorization["allowed"]:
            raise ValueError("install_disabled")
    plan = job.get("plan")
    if not plan or plan.get("code_fingerprint") != fingerprint(job):
        raise ValueError("current_install_plan_required")
    if authorization["autonomous"]:
        # Authorization never substitutes for test evidence. Re-check strict
        # preparation even if a caller prepared with optional checks disabled.
        frozen = dict(plan)
        try:
            checked = prepare_install(job, require_tests=True, require_regression=True)
        finally:
            job["plan"] = plan
        if checked["changes"] != frozen.get("changes"):
            raise ValueError("current_install_plan_required")
    candidates = {}
    for change in plan["changes"]:
        relative = change["path"]
        if not allowed(relative):
            raise ValueError("source_not_allowlisted")
        _safe(root, relative)
        source = _regular(_safe(_workspace(job), relative))
        if source is None:
            raise ValueError("candidate_missing:" + relative)
        raw = source.read_bytes()
        if _sha(raw) != change["new_sha256"]:
            raise ValueError("candidate_hash_mismatch:" + relative)
        candidates[relative] = raw
    backups, journal_path = {}, _journal(backup_dir, plan)
    installed = []
    journal = {"plan_id": plan["plan_id"], "status": "prepared", "source_root": str(root),
               "changes": plan["changes"], "at": _now(), "backups": backups,
               "authorization": authorization}
    journal_path.write_text(json.dumps(journal, ensure_ascii=False, indent=1), encoding="utf-8")
    _release_marker(root, "prepared", plan_id=plan["plan_id"], files=[c["path"] for c in plan["changes"]])
    try:
        for change in plan["changes"]:
            relative = change["path"]
            target = _regular(_safe(root, relative))
            if target is None and change["base_sha256"] != "missing":
                raise ValueError("source_missing:" + relative)
            if target is not None and _sha(target.read_bytes()) != change["base_sha256"]:
                raise ValueError("source_changed:" + relative)
            if target is not None:
                stored = Path(backup_dir) / plan["plan_id"] / relative
                stored.parent.mkdir(parents=True, exist_ok=True)
                stored.write_bytes(target.read_bytes())
                backups[relative] = str(stored)
        journal["status"] = "prepared"
        journal_path.write_text(json.dumps(journal, ensure_ascii=False, indent=1), encoding="utf-8")
        for change in plan["changes"]:
            relative = change["path"]
            target = _safe(root, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            current = _regular(target)
            current_sha = _sha(current.read_bytes()) if current is not None else "missing"
            if current_sha != change["base_sha256"]:
                raise ValueError("source_changed:" + relative)
            _replace_bytes(target, candidates[relative], suffix=".installing")
            installed.append(relative)
            if _sha(target.read_bytes()) != change["new_sha256"]:
                raise ValueError("installed_hash_mismatch:" + relative)
        journal["status"] = "installed"
        journal_path.write_text(json.dumps(journal, ensure_ascii=False, indent=1), encoding="utf-8")
        _release_marker(root, "installed", plan_id=plan["plan_id"],
                        files=[c["path"] for c in plan["changes"]], journal=str(journal_path))
    except Exception as exc:
        restored = []
        for change in plan["changes"]:
            relative = change["path"]
            if relative not in installed:
                continue
            try:
                target = _safe(root, relative)
                if not target.is_file() or _sha(target.read_bytes()) != change["new_sha256"]:
                    continue
                if change["base_sha256"] == "missing":
                    if target.is_file() and _sha(target.read_bytes()) == change["new_sha256"]:
                        target.unlink()
                        restored.append(relative)
                    continue
                stored = backups.get(relative)
                if stored and Path(stored).is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(Path(stored).read_bytes())
                    restored.append(relative)
            except (OSError, ValueError):
                pass
        rollback_status = "rolled_back" if set(restored) == set(installed) else "rollback_failed"
        journal.update(status=rollback_status, error=str(exc)[:1000], restored=restored, at=_now())
        journal_path.write_text(json.dumps(journal, ensure_ascii=False, indent=1), encoding="utf-8")
        _release_marker(root, "rolled_back" if rollback_status == "rolled_back" else "failed",
                        plan_id=plan["plan_id"], error=str(exc)[:1000])
        job["install"] = {"status": rollback_status, "journal": str(journal_path), "error": str(exc)[:1000]}
        raise
    job["install"] = {"status": "installed", "journal": str(journal_path),
                      "changes": [c["path"] for c in plan["changes"]],
                      "authorization": authorization, "restart_required": True}
    return dict(job["install"])


def rollback(job, *, backup_dir):
    install_record = job.get("install") or {}
    journal_path = install_record.get("journal")
    if not journal_path or not Path(journal_path).is_file():
        raise ValueError("journal_missing")
    journal = json.loads(Path(journal_path).read_text(encoding="utf-8"))
    if journal.get("status") != "installed":
        raise ValueError("nothing_to_rollback")
    plan = job.get("plan") or {}
    if (_real(journal_path) != _real(Path(backup_dir) / (str(plan.get("plan_id")) + ".json"))
            or journal.get("plan_id") != plan.get("plan_id")
            or _real(journal.get("source_root", "")) != _real(job["source_root"])
            or journal.get("changes") != plan.get("changes")):
        raise ValueError("journal_mismatch")
    root = Path(job["source_root"])
    prepared = []
    # Validate the entire transaction, including every backup, before the first write.
    for change in journal["changes"]:
        relative = change["path"]
        if not allowed(relative):
            raise ValueError("source_not_allowlisted")
        target = _safe(root, relative)
        if not target.is_file() or _sha(target.read_bytes()) != change["new_sha256"]:
            raise ValueError("source_changed_after_install:" + relative)
        original = None
        if change.get("base_sha256") != "missing":
            stored = journal.get("backups", {}).get(relative)
            expected = _safe(Path(backup_dir) / plan["plan_id"], relative)
            if not stored or _real(stored) != expected or not expected.is_file():
                raise ValueError("backup_missing:" + relative)
            original = expected.read_bytes()
            if _sha(original) != change["base_sha256"]:
                raise ValueError("backup_hash_mismatch:" + relative)
        prepared.append((target, original, target.read_bytes()))
    restored = []
    try:
        for target, original, installed in prepared:
            if _sha(target.read_bytes()) != _sha(installed):
                raise ValueError("source_changed_after_install:" + target.name)
            if original is None:
                target.unlink()
            else:
                _replace_bytes(target, original, suffix=".rolling-back")
            restored.append((target, installed))
        settled_journal = {**journal, "status": "rolled_back", "at": _now()}
        _replace_bytes(Path(journal_path), json.dumps(settled_journal, ensure_ascii=False, indent=1).encode("utf-8"),
                       suffix=".journal")
    except Exception:
        # A write failure must not strand a half-restored source tree.
        for target, installed in reversed(restored):
            _replace_bytes(target, installed, suffix=".rolling-forward")
        raise
    _release_marker(root, "rolled_back", plan_id=plan["plan_id"])
    job["install"] = {**(job.get("install") or {}), "status": "rolled_back"}
    return {"status": "rolled_back", "changes": [c["path"] for c in journal["changes"]]}
