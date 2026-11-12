# -*- coding: utf-8 -*-
import json, sys
from pathlib import Path
B = Path(r"C:\Users\Administrator\.dsh\wf-plugin-data\workflows\05七上人教版课时A计划StarterUnit2KeepTidy!第一课时Sec")
def load(rel):
    p = B / rel
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception as e:
        return {"_error": str(e)}
sup = load("workflow/supervisor.json") or {}
print("supervisor:", {k: sup.get(k) for k in ("status", "no_progress_attempts", "next_retry_at", "last_dispatched_at", "error_signature")})
print("last_result:", sup.get("last_result"))
man = load("workflow/manifest.json") or {}
print("manifest updated:", man.get("updated_at"), "status:", man.get("status"))
for name in ("review", "review_gate", "format_gate", "upload_gate"):
    st = (man.get("stages") or {}).get(name) or {}
    print(" stage", name, st.get("status"), st.get("finished_at"), (st.get("notes") or "")[:60])
fmt = load("07_review/upload-format.json") or {}
print("upload-format:", fmt.get("status"), "passed=", fmt.get("passed"), "generated=", fmt.get("generated_at"), "code_fp=", str(fmt.get("code_fingerprint"))[:12])
print(" errors:", [(e.get("code"), e.get("unit_id")) for e in fmt.get("errors") or []][:8])
print(" warnings:", [(w.get("code"), w.get("unit_id"), w.get("non_blocking")) for w in fmt.get("warnings") or []][:8])
print(" summary:", fmt.get("summary"))
