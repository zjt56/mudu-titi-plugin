# -*- coding: utf-8 -*-
"""隔离代码修复工作区（Operations 附加能力）。

设计边界（很重要）：

* 本模块只操作**临时副本**。``create`` 复制当前运行源码（含未提交改动）到
  独立目录；模型在其中读写、补测试、跑指定 unittest。
* 默认**不安装**到运行目录。``prepare_install`` 只生成带哈希的安装计划；
  ``install`` 需要显式 ``allow=True``，并且所有改动文件在计划生成后仍与基线
  逐字节相同（CAS），否则拒绝。安装前写 journal、备份原文件，失败自动回滚。
* 这不是操作系统级沙箱。模型写的 Python 测试本身就是可执行代码，只能在
  “信任该补丁来源/另有外部隔离”的前提下运行；本模块不声称提供进程、网络或
  凭据隔离。宿主若要安装，必须先暂停源码监听、排空执行队列并在安装后单次
  重启，否则会出现半更新状态。
* 控制面文件不可改：Operations 自身、阶段契约、清单、调度、runner、上传与
  发布边界。测试文件可新增或修改，但不得删除生产模块。
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
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
    path = _real(path)
    if path.is_symlink() or not path.is_file():
        return None
    return path


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


def read(job, relative, *, offset=0, limit=24000):
    relative = str(relative or "").replace("\\", "/")
    path = _safe(_workspace(job), relative)
    if not allowed(relative):
        raise ValueError("source_not_allowlisted")
    path = _regular(path)
    if path is None:
        raise ValueError("file_missing")
    offset = max(0, min(int(offset), 10_000_000))
    limit = max(1, min(int(limit), 24000))
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        stream.seek(offset)
        return {"path": str(relative).replace("\\", "/"), "offset": offset,
                "text": stream.read(limit), "limit": limit}


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
                or upper.startswith("DSH_"):
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
        target = _regular(root / relative)
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


def _release_marker(root, state, **extra):
    """Tell the Host a release is in flight so it can pause its watcher and
    perform exactly one restart. The Host owns removal after a healthy boot."""
    path = Path(root) / RELEASE_MARKER
    body = {"state": state, "at": _now(), **extra}
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temporary, path)
    return path


def install(job, *, backup_dir, allow=False):
    """Journaled transaction. ``allow`` is deliberate friction: the Host must
    have quiesced the batch, paused its watcher and be ready for one restart."""
    if allow is not True:
        raise ValueError("install_disabled")
    plan = job.get("plan")
    if not plan or plan.get("code_fingerprint") != fingerprint(job):
        raise ValueError("current_install_plan_required")
    root = Path(job["source_root"])
    backups, journal_path = {}, _journal(backup_dir, plan)
    journal = {"plan_id": plan["plan_id"], "status": "prepared", "source_root": str(root),
               "changes": plan["changes"], "at": _now(), "backups": backups}
    journal_path.write_text(json.dumps(journal, ensure_ascii=False, indent=1), encoding="utf-8")
    _release_marker(root, "prepared", plan_id=plan["plan_id"], files=[c["path"] for c in plan["changes"]])
    try:
        for change in plan["changes"]:
            relative = change["path"]
            target = _regular(root / relative)
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
        installed = []
        for change in plan["changes"]:
            relative = change["path"]
            source = _regular(Path(job["workspace"]) / relative)
            if source is None:
                raise ValueError("candidate_missing:" + relative)
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(target.suffix + ".installing")
            temporary.write_bytes(source.read_bytes())
            os.replace(temporary, target)
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
            try:
                target = root / relative
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
            except OSError:
                pass
        journal.update(status="rolled_back", error=str(exc)[:1000], restored=restored, at=_now())
        journal_path.write_text(json.dumps(journal, ensure_ascii=False, indent=1), encoding="utf-8")
        _release_marker(root, "rolled_back", plan_id=plan["plan_id"], error=str(exc)[:1000])
        job["install"] = {"status": "rolled_back", "journal": str(journal_path), "error": str(exc)[:1000]}
        raise
    job["install"] = {"status": "installed", "journal": str(journal_path), "changes": [c["path"] for c in plan["changes"]]}
    return dict(job["install"])


def rollback(job, *, backup_dir):
    install_record = job.get("install") or {}
    journal_path = install_record.get("journal")
    if not journal_path or not Path(journal_path).is_file():
        raise ValueError("journal_missing")
    journal = json.loads(Path(journal_path).read_text(encoding="utf-8"))
    if journal.get("status") != "installed":
        raise ValueError("nothing_to_rollback")
    root = Path(journal["source_root"])
    for change in journal["changes"]:
        target = root / change["path"]
        if target.is_file() and _sha(target.read_bytes()) != change["new_sha256"]:
            raise ValueError("source_changed_after_install:" + change["path"])
    for change in journal["changes"]:
        target = root / change["path"]
        stored = journal["backups"].get(change["path"])
        if change.get("base_sha256") == "missing":
            if target.is_file() and _sha(target.read_bytes()) == change["new_sha256"]:
                target.unlink()
            continue
        if stored and Path(stored).is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(Path(stored).read_bytes())
    journal.update(status="rolled_back", at=_now())
    Path(journal_path).write_text(json.dumps(journal, ensure_ascii=False, indent=1), encoding="utf-8")
    job["install"] = {**(job.get("install") or {}), "status": "rolled_back"}
    return {"status": "rolled_back", "changes": [c["path"] for c in journal["changes"]]}
