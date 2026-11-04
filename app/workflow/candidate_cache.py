"""Opt-in private cache of validated non-review candidates.

The caller must supply complete current fingerprints, the exact prompt and full
model configuration, and a validator that applies the existing result checks.
A cache hit is a candidate, never a receipt or permission to skip import checks.
No files are created unless the caller explicitly stores a candidate.
"""
from __future__ import annotations

import copy
import json
import math
import os
import stat
import uuid
from pathlib import Path

from .task_cache import snapshot_key

MAX_BYTES = 8 * 1024 * 1024
_STAGES = frozenset(("answers", "audio", "types", "explanations", "knowledge", "assemble"))
_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


def _key(stage, fingerprints, prompt, model_config):
    if stage not in _STAGES:
        raise ValueError("Candidate caching is prohibited for this stage")
    if not isinstance(fingerprints, (dict, list)) or not fingerprints:
        raise ValueError("Complete input fingerprints are required")
    if not isinstance(prompt, str) or not prompt:
        raise ValueError("The complete prompt is required")
    if not isinstance(model_config, dict) or not model_config:
        raise ValueError("The complete model configuration is required")
    return snapshot_key({"version": 1, "stage": stage, "fingerprints": fingerprints,
                         "prompt": prompt, "model_config": model_config})


def _directory(batch_dir, create):
    """Open each component without following symlinks; return an owned directory fd."""
    fd = os.open(Path(batch_dir), _FLAGS)
    try:
        for component in ("workflow", "candidate-cache"):
            if create:
                try:
                    os.mkdir(component, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(component, _FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate candidate cache key")
            result[key] = value
        return result
    def reject(value):
        raise ValueError("Nonfinite candidate cache number")
    def number(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("Nonfinite candidate cache number")
        return result
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=reject, parse_float=number)


def _validate_candidate(value, validate):
    if not isinstance(value, dict) or value.get("status") != "completed":
        raise ValueError("Only completed candidates may be cached")
    report = validate(value)
    if isinstance(report, dict) and report.get("completed") is False:
        raise ValueError("Candidate validation did not complete")


def store(batch_dir, *, stage, fingerprints, prompt, model_config, candidate, validate):
    """Validate and atomically store a candidate; do not store model credentials."""
    key = _key(stage, fingerprints, prompt, model_config)
    if not callable(validate):
        raise ValueError("A result validator is required")
    value = copy.deepcopy(candidate)
    _validate_candidate(value, validate)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
    if len(raw) > MAX_BYTES:
        raise ValueError("Candidate exceeds cache size limit")
    directory = _directory(batch_dir, True)
    temporary = "." + uuid.uuid4().hex + ".tmp"
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, key + ".json", src_dir_fd=directory, dst_dir_fd=directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
    finally:
        os.close(directory)


def load(batch_dir, *, stage, fingerprints, prompt, model_config, validate):
    """Load and revalidate a candidate, or return None when no entry exists."""
    key = _key(stage, fingerprints, prompt, model_config)
    if not callable(validate):
        raise ValueError("A result validator is required")
    try:
        directory = _directory(batch_dir, False)
    except FileNotFoundError:
        return None
    try:
        try:
            fd = os.open(key + ".json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                raise ValueError("Invalid candidate cache file")
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("Candidate exceeds cache size limit")
        value = _decode(raw)
        _validate_candidate(value, validate)
        return value
    finally:
        os.close(directory)
