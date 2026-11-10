"""Display-only ownership of work between transactional stage deliveries.

Never used for dispatch, acceptance, quality approval, or completion.
"""
from contextlib import contextmanager
from pathlib import Path
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone

FILE = "workflow/activity.json"


def read(batch):
    path = Path(batch) / FILE
    try:
        if path.is_symlink() or path.parent.is_symlink():
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        pid = value.get("pid")
        if type(pid) is not int or pid <= 0:
            return {}
        os.kill(pid, 0)
        return value if value.get("stage") and value.get("token") else {}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


@contextmanager
def track(batch, stage, detail):
    path = Path(batch) / FILE
    token = uuid.uuid4().hex
    value = {"stage": stage, "detail": detail, "pid": os.getpid(), "token": token,
             "started_at": datetime.now(timezone.utc).isoformat()}
    installed = False
    if path.parent.is_dir() and not path.parent.is_symlink() and not path.is_symlink():
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=".activity-", delete=False) as handle:
                temp = handle.name
                json.dump(value, handle, ensure_ascii=False)
            os.replace(temp, path)
            installed = True
        except OSError:
            if temp:
                try:
                    os.unlink(temp)
                except OSError:
                    pass
    try:
        yield
    finally:
        if installed:
            try:
                if not path.is_symlink() and json.loads(path.read_text()).get("token") == token:
                    path.unlink()
            except (OSError, ValueError, AttributeError):
                pass


def observed(stage, detail):
    """Observe a real function lifetime without changing its transaction state."""
    from functools import wraps
    def decorate(fn):
        @wraps(fn)
        def wrapped(batch, *args, **kwargs):
            with track(batch, stage, detail):
                return fn(batch, *args, **kwargs)
        return wrapped
    return decorate
