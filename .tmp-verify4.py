# -*- coding: utf-8 -*-
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(r"C:\Users\Administrator\.dsh\plugin-src\md-upload-tiku\app")))
from workflow import operations

B = Path(r"C:\Users\Administrator\.dsh\wf-plugin-data\workflows\05七上人教版课时A计划StarterUnit2KeepTidy!第一课时Sec")
state = operations._load(B) or {}
rows = state.get("receipts") or []
fp = "7b1755ceaee8939a207e71bb041e5b05a406465f085e219583d944caf55e2710"
hits = [r for r in rows if r.get("fingerprint") == fp]
print("same-fingerprint receipts:", len(hits))
for r in hits:
    print(json.dumps(r, ensure_ascii=False)[:400])
print("total receipts:", len(rows), "statuses:", {})
from collections import Counter
print(Counter(r.get("status") for r in rows))
print("last 3 receipts:", json.dumps(rows[-3:], ensure_ascii=False)[:600])
