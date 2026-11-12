# -*- coding: utf-8 -*-
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(r"C:\Users\Administrator\.dsh\plugin-src\md-upload-tiku\app")))
from workflow import manifest as mf, self_heal, contracts
from workflow.handoff import inspect_stage

B = Path(r"C:\Users\Administrator\.dsh\wf-plugin-data\workflows\05七上人教版课时A计划StarterUnit2KeepTidy!第一课时Sec")
man = mf.load(B)
stage = contracts.STAGE_BY_ID["format_gate"]
rec = man["stages"].get("format_gate", {})
stale, reason = mf.is_stale(B, man, stage)
cand, detail = self_heal._candidate(B, man)
checked = inspect_stage(B, man, "format_gate")
print("status:", rec.get("status"))
print("stale:", stale, reason)
print("missing_produces:", mf.missing_produces(B, stage))
print("candidate:", cand, str(detail)[:120])
print("inspect ok:", checked.get("passed"), str(checked.get("errors"))[:300])
print("hashes match:", rec.get("input_hashes") == mf.input_hashes(B, man, stage))
