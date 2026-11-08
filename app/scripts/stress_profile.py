#!/usr/bin/env python3
"""Profile synthetic batches only; no user/archive files and no model or network."""
from __future__ import annotations
import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow import batch_fill, format_gate, manifest as mf, question_draft, runner, stress_lab


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    root=Path(tempfile.mkdtemp(prefix='synthetic-profile-',dir=Path(tempfile.gettempdir()).resolve())).resolve()
    os.environ['TMPDIR']=str(root)
    os.environ['WF_STRUCTURE_AI']='0'
    rows=[]
    for count in (12,48,96):
        batch=root/str(count)
        stages=[]
        def measure(name, fn):
            start=time.perf_counter()
            with contextlib.redirect_stdout(io.StringIO()):
                result=fn()
            info={'stage':name,'ms':round((time.perf_counter()-start)*1000,1)}
            if isinstance(result,dict) and 'passed' in result:
                info['passed']=bool(result['passed'])
            if name=='validate':
                info['exit_code']=result
            stages.append(info)
            return result
        fixture=measure('build_synthetic_fixture',lambda:stress_lab.build_fixture(batch,900+count,standalone_count=count))
        measure('draft_cached',lambda:question_draft.ensure(batch))
        measure('fill_initialize_cached',lambda:batch_fill.initialize(batch))
        report=batch/'07_review/upload-format.json'
        report.unlink(missing_ok=True)
        result=measure('format_gate_cold',lambda:format_gate.run(batch))
        measure('validate',lambda:runner.do_validate(batch,mf.load(batch)))
        rows.append({'standalone_count':count,'units':len(fixture.units),'questions':sum(len(u['numbers']) for u in fixture.units),
                     'stages':stages,'format_passed':bool(result.get('passed')),
                     'scope':'synthetic text pipeline, simulated annotations/audio references; excludes OCR, ASR and semantic AI latency'})
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(rows,ensure_ascii=False,indent=2))
    return 0 if all(r['format_passed'] for r in rows) else 1

if __name__=='__main__':
    raise SystemExit(main())
