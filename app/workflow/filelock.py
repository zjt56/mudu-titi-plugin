# -*- coding: utf-8 -*-
"""Unified cross-platform OS file locking (POSIX flock / Windows msvcrt byte lock).

Never unlinks lock files (unlinking splits inodes across concurrent processes).
On Windows, uses non-blocking byte-range locks (`LK_NBLCK`) in a poll loop so
blocking waits never hit `msvcrt.LK_LOCK`'s 10-second `EDEADLK` ceiling.
"""
from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import time
from typing import Callable, Iterable

_CONTENDED_ERRNOS = frozenset((errno.EACCES, errno.EAGAIN, errno.EDEADLK))


def _ensure_windows_byte(handle, size: int = 1) -> None:
    handle.seek(0, os.SEEK_END)
    current = handle.tell()
    if current < size:
        handle.write(b"\0" * (size - current))
        handle.flush()


def _lock_nb(handle) -> None:
    if os.name == "nt":
        import msvcrt
        _ensure_windows_byte(handle, 1)
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle) -> None:
    if os.name == "nt":
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _is_contended(exc: OSError) -> bool:
    if isinstance(exc, BlockingIOError):
        return True
    if exc.errno in _CONTENDED_ERRNOS:
        return True
    # Windows msvcrt may surface generic OSError(errno=13/36) or winerror 33 (ERROR_LOCK_VIOLATION).
    return os.name == "nt" and getattr(exc, "winerror", None) in (32, 33)


@contextmanager
def try_file_lock(path: Path | str, *, create_parents: bool = False):
    """Acquire a non-blocking exclusive OS lock; yield True if acquired, False otherwise.

    Refuses symlinks and never deletes the lock inode.
    """
    target = Path(path)
    if target.is_symlink():
        yield False
        return
    handle = None
    acquired = False
    try:
        if create_parents:
            target.parent.mkdir(parents=True, exist_ok=True)
        handle = target.open("a+b")
        _lock_nb(handle)
        acquired = True
    except OSError:
        pass
    try:
        yield acquired
    finally:
        if handle is not None:
            if acquired:
                try:
                    _unlock(handle)
                except OSError:
                    pass
            try:
                handle.close()
            except OSError:
                pass


@contextmanager
def file_lock(
    path: Path | str,
    *,
    timeout: float | None = None,
    poll_interval: float = 0.02,
    create_parents: bool = True,
):
    """Acquire an exclusive cross-process OS lock, blocking or up to `timeout` seconds.

    Raises `TimeoutError` if `timeout` is given and expires before acquisition.
    """
    target = Path(path)
    if target.is_symlink():
        raise OSError(f"Refusing to lock symlink: {target}")
    if create_parents:
        target.parent.mkdir(parents=True, exist_ok=True)
    handle = target.open("a+b")
    locked = False
    try:
        if timeout is None and os.name != "nt":
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            locked = True
        else:
            deadline = None if timeout is None else (time.monotonic() + max(0.0, float(timeout)))
            while True:
                try:
                    _lock_nb(handle)
                    locked = True
                    break
                except OSError as exc:
                    if not _is_contended(exc):
                        raise
                    if deadline is not None and time.monotonic() >= deadline:
                        raise TimeoutError(f"Timed out acquiring file lock: {target}") from None
                    sleep_for = poll_interval
                    if deadline is not None:
                        sleep_for = min(poll_interval, max(0.0, deadline - time.monotonic()))
                    time.sleep(sleep_for)
        yield handle
    finally:
        if locked:
            try:
                _unlock(handle)
            except OSError:
                pass
        try:
            handle.close()
        except OSError:
            pass


def is_file_locked(path: Path | str) -> bool:
    """Non-blocking liveness probe: return True if another process holds the lock."""
    target = Path(path)
    if not target.is_file() or target.is_symlink():
        return False
    try:
        handle = target.open("r+b")
    except OSError:
        return True
    with handle:
        try:
            _lock_nb(handle)
        except OSError:
            return True
        try:
            _unlock(handle)
        except OSError:
            pass
        return False


@contextmanager
def acquire_slot_lock(
    paths: Iterable[Path | str],
    *,
    poll_interval: float = 0.1,
    on_wait: Callable[[], None] | None = None,
):
    """Acquire an exclusive lock on the first available slot file in `paths`."""
    slot_paths = [Path(p) for p in paths]
    if not slot_paths:
        raise ValueError("acquire_slot_lock requires at least one lock path")
    handles = []
    acquired = None
    try:
        for slot_path in slot_paths:
            slot_path.parent.mkdir(parents=True, exist_ok=True)
            handle = slot_path.open("a+b")
            if os.name == "nt":
                _ensure_windows_byte(handle, 1)
            handles.append(handle)
        waiting_reported = False
        while acquired is None:
            for handle in handles:
                try:
                    _lock_nb(handle)
                    acquired = handle
                    break
                except OSError as exc:
                    if not _is_contended(exc):
                        raise
            if acquired is None:
                if not waiting_reported:
                    if on_wait is not None:
                        on_wait()
                    waiting_reported = True
                time.sleep(poll_interval)
        yield acquired
    finally:
        if acquired is not None:
            try:
                _unlock(acquired)
            except OSError:
                pass
        for handle in handles:
            try:
                handle.close()
            except OSError:
                pass
