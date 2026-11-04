"""Small process-local cache for task templates, keyed by complete input snapshots.

Callers must discover and read all current inputs, including absent optional files
and directory listings, before computing a key. Never cache responses or bindings.
"""
from __future__ import annotations

from collections import OrderedDict
import copy
import hashlib
import json
from threading import Lock


def snapshot_key(snapshot):
    """Hash a JSON-compatible snapshot; reject non-finite values and ambiguous keys."""
    def validate(value):
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise ValueError("Cache snapshot object keys must be strings")
            for item in value.values():
                validate(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                validate(item)
    validate(snapshot)
    raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                     allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class TaskCache:
    """Bounded, thread-safe cache; callers own discovery and freshness checks."""

    def __init__(self, max_entries=8):
        if isinstance(max_entries, bool) or not isinstance(max_entries, int) or max_entries < 1:
            raise ValueError("max_entries must be a positive integer")
        self._max_entries = max_entries
        self._entries = OrderedDict()
        self._lock = Lock()

    def get_or_build(self, snapshot, builder):
        key = snapshot_key(snapshot)
        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
                return copy.deepcopy(self._entries[key])
        # Do not hold a global lock while building or cache failed builds.
        built = builder()
        stored = copy.deepcopy(built)
        with self._lock:
            self._entries[key] = stored
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
        return built

    def clear(self):
        with self._lock:
            self._entries.clear()
