# -*- coding: utf-8 -*-
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(r"C:\Users\Administrator\.dsh\plugin-src\md-upload-tiku\app")))
from workflow import operations

ROOT = Path(r"C:\Users\Administrator\.dsh\wf-plugin-data\workflows")
B = ROOT / "05七上人教版课时A计划StarterUnit2KeepTidy!第一课时Sec"
view = operations.view(ROOT, B)
inc = view.get("incident") or {}
print("status:", inc.get("status"))
print("quarantined:", inc.get("quarantined_receipts"))
print("verification:", json.dumps(inc.get("verification"), ensure_ascii=False)[:300])
print("last events:", json.dumps((inc.get("events") or [])[-2:], ensure_ascii=False)[:600])
print("pending_incident:", operations.pending_incident(B))
