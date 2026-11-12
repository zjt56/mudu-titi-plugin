import json, os
from pathlib import Path
B = Path(os.environ["B"])
for rel in ("workflow/supervisor.json", "workflow/operations.json"):
    p = B / rel
    d = json.loads(p.read_text(encoding="utf-8-sig")) if p.is_file() else {}
    if rel.endswith("supervisor.json"):
        print("supervisor", {k: d.get(k) for k in ("status","no_progress_attempts","next_retry_at","progress")}, d.get("last_result"))
    else:
        inc = d.get("incident") or {}
        print("operations", d.get("status"), inc.get("status"), inc.get("trigger"), inc.get("stage"), inc.get("eligible"), len(d.get("events") or []))
        for ev in (d.get("events") or [])[-3:]:
            print("  ev", ev.get("at"), ev.get("kind"), (ev.get("summary") or ev.get("message") or "")[:100])
