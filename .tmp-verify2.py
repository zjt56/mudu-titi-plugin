# -*- coding: utf-8 -*-
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(r"C:\Users\Administrator\.dsh\plugin-src\md-upload-tiku\app")))
from workflow import operations

B = Path(r"C:\Users\Administrator\.dsh\wf-plugin-data\workflows\05七上人教版课时A计划StarterUnit2KeepTidy!第一课时Sec")
state = operations._load(B) or {}
print("incident status:", (state.get("incident") or {}).get("status"))
for row in state.get("receipts") or []:
    print(" receipt:", {k: row.get(k) for k in ("at", "kind", "status", "action", "name", "outcome", "error")})
print("receipts count:", len(state.get("receipts") or []))
