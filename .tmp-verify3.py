# -*- coding: utf-8 -*-
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(r"C:\Users\Administrator\.dsh\plugin-src\md-upload-tiku\app")))
from workflow import operations

B = Path(r"C:\Users\Administrator\.dsh\wf-plugin-data\workflows\05七上人教版课时A计划StarterUnit2KeepTidy!第一课时Sec")
state = operations._load(B) or {}
rows = [r for r in state.get("receipts") or [] if r.get("status") == "unknown"]
print("unknown count:", len(rows))
for row in rows:
    print(json.dumps(row, ensure_ascii=False, indent=1)[:2000])
inc = state.get("incident") or {}
print("incident keys:", sorted(inc.keys()))
print("incident:", json.dumps({k: inc.get(k) for k in ("status", "stage", "detail", "trigger", "signature", "attempts", "events")}, ensure_ascii=False)[:800])
print("resume_outbox:", bool(state.get("resume_outbox")))
