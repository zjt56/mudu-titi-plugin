"""Private, resumable speculative results; never an export, receipt or stage result.

This storage primitive is deliberately not wired into the production scheduler.
Callers must build source-bound prompts and must re-evaluate every candidate with
real dependencies in a newly exported task before the normal importer is used.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import json
import os
import stat
import uuid

from . import candidate_cache
from .task_cache import snapshot_key

STAGES = frozenset(("types", "answers", "explanations", "knowledge"))
VERSION = 1


def _identity(stage, prompt, source_fingerprints, model_config):
    if stage not in STAGES or not isinstance(prompt, str) or not prompt:
        raise ValueError("A supported stage and complete prompt are required")
    if not isinstance(source_fingerprints, dict) or not source_fingerprints:
        raise ValueError("Source fingerprints are required")
    if not isinstance(model_config, dict) or not model_config:
        raise ValueError("Model configuration is required")
    # Configuration is hashed, never written to disk.
    return snapshot_key({"version": VERSION, "stage": stage, "prompt": prompt,
                         "source_fingerprints": source_fingerprints, "model_config": model_config})


def _name(key):
    return "speculative-" + key + ".json"


def _read(directory, key, stage):
    try:
        fd = os.open(_name(key), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > candidate_cache.MAX_BYTES:
            raise ValueError("Invalid speculative candidate file")
        raw = stream.read(candidate_cache.MAX_BYTES + 1)
    if len(raw) > candidate_cache.MAX_BYTES:
        raise ValueError("Speculative candidate too large")
    record = candidate_cache._decode(raw)
    if (not isinstance(record, dict) or set(record) != {"version", "stage", "identity", "candidate"}
            or record["version"] != VERSION or record["stage"] != stage or record["identity"] != key
            or not isinstance(record["candidate"], dict)):
        raise ValueError("Invalid speculative candidate record")
    return record["candidate"]


def _store(directory, key, stage, candidate):
    if not isinstance(candidate, dict):
        raise ValueError("Candidate must be a JSON object")
    record = {"version": VERSION, "stage": stage, "identity": key, "candidate": candidate}
    raw = json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
    if len(raw) > candidate_cache.MAX_BYTES:
        raise ValueError("Speculative candidate too large")
    temporary = ".speculative-" + uuid.uuid4().hex + ".tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, _name(key), src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


def generate(batch_dir, tasks, model_config, invoke, current_fingerprints):
    """Generate missing candidates concurrently, retaining completed writes on failure.

    tasks maps stages to {prompt, source_fingerprints}; invoke(stage, prompt)
    returns a JSON object. current_fingerprints(stage) must independently read
    the same sources immediately before and after the call. Results are *only*
    isolated candidates, even when their embedded status says completed. A source
    mismatch rejects that invocation. Each outcome is candidate, stale, or failed.
    """
    if not isinstance(tasks, dict) or not tasks or not callable(invoke) or not callable(current_fingerprints):
        raise ValueError("Tasks and invocation/fingerprint callbacks are required")
    prepared = {}
    for stage, task in tasks.items():
        if not isinstance(task, dict) or set(task) != {"prompt", "source_fingerprints"}:
            raise ValueError("Each task needs prompt and source_fingerprints")
        key = _identity(stage, task["prompt"], task["source_fingerprints"], model_config)
        prepared[stage] = (key, copy.deepcopy(task))
    directory = candidate_cache._directory(batch_dir, True)
    outcomes = {}
    try:
        def run(stage, key, task):
            fingerprints = task["source_fingerprints"]
            if current_fingerprints(stage) != fingerprints:
                return {"status": "stale"}
            candidate = _read(directory, key, stage)
            if candidate is None:
                candidate = invoke(stage, task["prompt"])
                if current_fingerprints(stage) != fingerprints:
                    return {"status": "stale"}
                _store(directory, key, stage, candidate)
            elif current_fingerprints(stage) != fingerprints:
                return {"status": "stale"}
            return {"status": "candidate", "candidate": candidate}

        with ThreadPoolExecutor(max_workers=min(4, len(prepared))) as pool:
            futures = {pool.submit(run, stage, key, task): stage
                       for stage, (key, task) in prepared.items()}
            for future in as_completed(futures):
                stage = futures[future]
                try:
                    outcomes[stage] = future.result()
                except Exception as exc:
                    outcomes[stage] = {"status": "failed", "reason": str(exc)}
        return outcomes
    finally:
        os.close(directory)
