"""Best-effort timings for existing attempts. Never gate delivery on telemetry."""
from contextlib import contextmanager
import json
import time


@contextmanager
def measure(path, phase, **dimensions):
    started = time.monotonic()
    outcome = 'returned'
    try:
        yield
    except BaseException:
        outcome = 'raised'
        raise
    finally:
        row = {**dimensions, 'phase': phase, 'outcome': outcome,
               'seconds': round(time.monotonic() - started, 4)}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('x', encoding='utf-8') as handle:
                json.dump(row, handle, ensure_ascii=False)
                handle.write('\n')
        except OSError:
            pass
