"""Batch-local durable OCR receipts. No credentials, remote URLs or responses.

The lock is owned by the OS and released on process exit. A submitting receipt
is deliberately ambiguous after a crash: callers may reconcile its batch_id,
but must never infer that the POST was not received or automatically repeat it.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
import uuid

SCHEMA = "ocr-job/v1"
PHASES = {"prepared", "submitting", "submission_unknown", "submitted", "polling",
          "downloading", "succeeded", "failed"}
ERROR_CODES = {"waiting", "submission_unknown", "submission_conflict", "receipt_corrupt",
               "job_not_found", "job_expired", "job_failed", "http_error", "unsafe_redirect",
               "network_error", "timeout", "invalid_result", "invalid_config", "missing_token",
               "cache_corrupt", "storage_error"}
FIELDS = {"schema", "fingerprint", "source", "config", "phase", "job_id", "batch_id",
          "created_at", "updated_at", "submitted_at", "completed_at", "attempts",
          "last_error", "state", "retry_after_seconds", "total_pages", "extracted_pages", "artifacts"}
ID = re.compile(r"[A-Za-z0-9_-]{1,256}\Z")
DIGEST = re.compile(r"[a-f0-9]{64}\Z")


class StoreError(RuntimeError):
    def __init__(self, code="receipt_corrupt"):
        self.code = code
        super().__init__(code)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(binary):
    return hashlib.sha256(binary).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def binding(binary, config, option_keys, *, pages=None, task="ocr", file_type=""):
    """Only semantic settings participate; auth and timing never affect identity."""
    source = {"sha256": digest(binary), "size": len(binary), "file_type": file_type.lower()}
    # Hash free-form endpoint/model/task values rather than putting them in a
    # receipt. This also prevents accidentally secret-bearing configuration.
    semantics = {"endpoint_sha256": digest(config["endpoint"].encode()),
                 "model_sha256": digest(config["model"].encode()),
                 "options": {key: config[key] for key in option_keys},
                 "pages": None if pages is None else str(pages),
                 "task_sha256": digest(_json(task).encode())}
    fingerprint = digest(_json({"schema": SCHEMA, "source": source, "config": semantics}).encode())
    return {"fingerprint": fingerprint, "source": source, "config": semantics}


def atomic_write(path, binary):
    """Replace within one filesystem, fsync bytes before publishing the name."""
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".ocr-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(binary)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _validate(receipt):
    required = {"schema", "fingerprint", "source", "config", "phase", "job_id", "batch_id",
                "created_at", "updated_at", "attempts", "last_error", "state", "retry_after_seconds"}
    if (not isinstance(receipt, dict) or set(receipt) - FIELDS or not required <= receipt.keys()
            or receipt.get("schema") != SCHEMA or receipt.get("phase") not in PHASES
            or not DIGEST.fullmatch(str(receipt.get("fingerprint", "")))):
        raise StoreError()
    for key in ("job_id", "batch_id"):
        if receipt.get(key) is not None and (not isinstance(receipt[key], str) or not ID.fullmatch(receipt[key])):
            raise StoreError()
    if not ID.fullmatch(str(receipt.get("batch_id", ""))):
        raise StoreError()
    source, config = receipt.get("source"), receipt.get("config")
    if (not isinstance(source, dict) or set(source) != {"sha256", "size", "file_type"}
            or not DIGEST.fullmatch(str(source["sha256"])) or type(source["size"]) is not int
            or source["size"] < 1 or source["file_type"] not in {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
            or not isinstance(config, dict) or set(config) != {"endpoint_sha256", "model_sha256", "options", "pages", "task_sha256"}
            or any(not DIGEST.fullmatch(str(config[key])) for key in ("endpoint_sha256", "model_sha256", "task_sha256"))
            or not isinstance(config["options"], dict)
            or set(config["options"]) != {"useDocOrientationClassify", "useDocUnwarping", "useChartRecognition"}
            or any(type(v) is not bool for v in config["options"].values())
            or (config["pages"] is not None and not re.fullmatch(r"[1-9]\d*(?:-[1-9]\d*)?", str(config["pages"])))):
        raise StoreError()
    expected_fingerprint = digest(_json({"schema": SCHEMA, "source": source, "config": config}).encode())
    if receipt["fingerprint"] != expected_fingerprint:
        raise StoreError()
    if (not isinstance(receipt.get("attempts"), dict)
            or set(receipt["attempts"]) != {"submit", "get", "reconcile", "download"}
            or any(type(v) is not int or v < 0 for v in receipt["attempts"].values())
            or receipt.get("last_error") not in ERROR_CODES | {None}
            or receipt.get("state") not in {None, "pending", "running", "done", "failed"}):
        raise StoreError()
    for key in ("created_at", "updated_at", "submitted_at", "completed_at"):
        if key in receipt:
            try:
                datetime.fromisoformat(receipt[key])
            except (TypeError, ValueError):
                raise StoreError() from None
    for key in ("total_pages", "extracted_pages", "retry_after_seconds"):
        if key in receipt and (type(receipt[key]) is not int or not 0 <= receipt[key] <= 1000000000):
            raise StoreError()
    if receipt["phase"] in {"submitted", "polling", "downloading", "succeeded"} and not receipt.get("job_id"):
        raise StoreError()
    if receipt["phase"] == "prepared" and (receipt.get("job_id") or receipt["attempts"]["submit"] or "submitted_at" in receipt):
        raise StoreError()
    if "artifacts" in receipt:
        artifacts = receipt["artifacts"]
        if not isinstance(artifacts, dict):
            raise StoreError()
        for name, value in artifacts.items():
            if (name not in {"result.json", "result.md", "raw-result.jsonl"}
                    and not re.fullmatch(r"media/page-\d{4,}-[a-z]+-\d{3,}\.(?:png|jpg|jpeg|webp|gif|bmp|tif|tiff)", name)):
                raise StoreError()
            if not DIGEST.fullmatch(str(value)):
                raise StoreError()
    return receipt


class JobStore:
    def __init__(self, scope, identity, progress_callback=None):
        self.identity = identity
        self.progress_callback = progress_callback
        self.directory = Path(scope).expanduser().resolve() / ("paddle-ocr-" + identity["fingerprint"])
        self.receipt_path = self.directory / "receipt.json"
        self.receipt = None
        self.locked = False

    @contextmanager
    def lock(self, timeout=0):
        self.directory.mkdir(parents=True, exist_ok=True)
        stream = open(self.directory / ".lock", "a+b")
        deadline = time.monotonic() + max(0, timeout)
        acquired = False
        try:
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt
                        stream.seek(0)
                        if not stream.read(1):
                            stream.write(b"0")
                            stream.flush()
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = self.locked = True
                    break
                except (BlockingIOError, OSError):
                    if time.monotonic() >= deadline:
                        raise StoreError("waiting") from None
                    time.sleep(min(.05, max(0, deadline - time.monotonic())))
            yield self
        finally:
            if acquired:
                if os.name == "nt":
                    import msvcrt
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            self.locked = False
            stream.close()

    def load(self):
        if not self.locked:
            raise RuntimeError("OCR store lock required")
        if self.receipt_path.exists():
            try:
                receipt = _validate(json.loads(self.receipt_path.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                raise StoreError() from None
            if any(receipt.get(key) != value for key, value in self.identity.items()):
                raise StoreError()
            self.receipt = receipt
        else:
            # Artifacts without a receipt cannot prove a safe first submission.
            if any(self.directory.glob("result.*")) or (self.directory / "raw-result.jsonl").exists():
                raise StoreError()
            now = _now()
            self.receipt = {"schema": SCHEMA, **self.identity, "phase": "prepared", "job_id": None,
                            "batch_id": "ocr-" + uuid.uuid4().hex, "created_at": now, "updated_at": now,
                            "attempts": {"submit": 0, "get": 0, "reconcile": 0, "download": 0},
                            "last_error": None, "state": None, "retry_after_seconds": 2}
            self.save()
        return self.receipt

    def save(self, **changes):
        if not self.locked or self.receipt is None:
            raise RuntimeError("OCR store lock required")
        if set(changes) - FIELDS:
            raise StoreError()
        receipt = {**self.receipt, **changes, "updated_at": _now()}
        _validate(receipt)
        atomic_write(self.receipt_path, (_json(receipt) + "\n").encode())
        self.receipt = receipt
        if self.progress_callback is not None:
            try:
                self.progress_callback(_safe_progress(receipt))
            except Exception:
                raise StoreError("storage_error") from None
        return receipt

    def count(self, kind):
        counts = dict(self.receipt["attempts"])
        counts[kind] += 1
        self.save(attempts=counts)

    def verify_result(self):
        """Hash every completion artifact, then validate the normalized payload."""
        try:
            entries = self.receipt.get("artifacts", {})
            if not isinstance(entries, dict) or not {"result.json", "result.md", "raw-result.jsonl"} <= entries.keys():
                return None
            for relative, expected in entries.items():
                path = self.directory / relative
                if (not isinstance(relative, str) or path.is_symlink()
                        or not path.resolve().is_relative_to(self.directory)
                        or not DIGEST.fullmatch(str(expected)) or digest(path.read_bytes()) != expected):
                    return None
            result = json.loads((self.directory / "result.json").read_text(encoding="utf-8"))
            pages = result["pages"]
            if (not isinstance(pages, list) or not pages or result.get("job_id") != self.receipt["job_id"]
                    or any(not isinstance(page.get("text"), str) for page in pages)
                    or not any(page["text"].strip() for page in pages)
                    or (self.directory / "result.md").read_text(encoding="utf-8") != "\n\n".join(page["text"] for page in pages)):
                return None
            raw = (self.directory / "raw-result.jsonl").read_text(encoding="utf-8")
            if not raw.strip():
                return None
            for line in raw.splitlines():
                if line.strip():
                    json.loads(line)
            return result
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return None

    def complete(self, result):
        entries = {}
        for path in [self.directory / name for name in ("result.json", "result.md", "raw-result.jsonl")] + [Path(p) for p in result["artifacts"].get("images", [])]:
            if path.is_symlink() or not path.resolve().is_relative_to(self.directory):
                raise StoreError("invalid_result")
            # Receipt keys are part of the on-disk contract: always POSIX so a
            # Windows receipt validates on any platform (and matches the
            # media/page-... name rule in _validate).
            entries[path.relative_to(self.directory).as_posix()] = digest(path.read_bytes())
        # Verification precedes the success receipt. A crash at any earlier point
        # resumes the existing remote job, never creates another submission.
        self.save(artifacts=entries)
        if self.verify_result() is None:
            raise StoreError("invalid_result")
        self.save(phase="succeeded", state="done", completed_at=_now(), last_error=None,
                  retry_after_seconds=0)


def _safe_progress(receipt):
    value = {key: receipt[key] for key in ("fingerprint", "phase", "job_id", "batch_id", "created_at", "updated_at",
             "submitted_at", "completed_at", "attempts", "last_error", "state", "source", "config",
             "retry_after_seconds", "total_pages", "extracted_pages") if key in receipt}
    if isinstance(receipt.get("config"), dict):
        value["config_sha256"] = digest(_json(receipt["config"]).encode())
    elif isinstance(receipt.get("config_sha256"), str):
        value["config_sha256"] = receipt["config_sha256"]
    if value["phase"] == "submitting" and not value.get("job_id"):
        value.update(phase="submission_unknown", last_error="submission_unknown")
    value["retryable"] = value["phase"] not in {"failed", "succeeded"}
    # Callbacks must never mutate the store's live binding or attempt counters.
    return json.loads(_json(value))


def progress(output_dir):
    """Read safe atomic snapshots, without network, secrets or lock waiting."""
    root = Path(output_dir).expanduser().resolve()
    states = []
    for path in sorted(root.glob("paddle-ocr-*/receipt.json")):
        try:
            states.append(_safe_progress(_validate(json.loads(path.read_text(encoding="utf-8")))))
        except (StoreError, OSError, ValueError, TypeError):
            states.append({"phase": "failed", "state": "failed", "job_id": None,
                           "last_error": "receipt_corrupt", "retryable": False, "retry_after_seconds": 0})
    return states
