#!/usr/bin/env python3
"""Opt-in live model probe on synthetic fixtures; report calls and outcomes separately."""
from __future__ import annotations
import argparse
import contextlib
import io
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow import local_agent, self_heal, stress_lab

CASES = (("empty_paragraph", 701), ("listening_stem", 702), ("two_defects", 703),
         ("nonrepairable_prose", 704), ("blank_structure", 705), ("invalid_label", 706),
         ("stale_format_report", 707), ("missing_final", 708), ("corrupt_report", 709),
         ("empty_paragraph", 710), ("listening_stem", 711), ("missing_option", 712))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--case', choices=sorted({case[0] for case in CASES}))
    args=parser.parse_args()
    root=Path(tempfile.mkdtemp(prefix='live-probe-v2-', dir=Path(tempfile.gettempdir()).resolve())).resolve()
    os.environ['TMPDIR']=str(root)
    tempfile.tempdir=str(root)
    os.environ['WF_STRUCTURE_AI']='0'
    config=local_agent.load_config(args.config) if args.config else local_agent.load_config()
    config['self_heal_timeout_seconds']=90
    invoke=local_agent.invoke
    rows=[]
    for defect, seed in CASES:
        if args.case and defect != args.case:
            continue
        case=root/f'{seed}-{defect}'
        row={'defect':defect, 'seed':seed, 'model_calls':0, 'model_ms':[], 'errors':[]}
        def measured(*a, **kw):
            t=time.perf_counter()
            row['model_calls']+=1
            try:
                return invoke(*a, **kw)
            finally:
                row['model_ms'].append(round((time.perf_counter()-t)*1000,1))
        start=time.perf_counter()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                fixture=stress_lab.build_fixture(case,seed)
                post=defect in stress_lab.POST_DEFECTS
                info={'defect':defect} if post else stress_lab.inject(fixture,defect)
                stress_lab._rebaseline(fixture)
                if post:
                    info=stress_lab.inject(fixture,defect)
                if info['defect']!=defect:
                    raise AssertionError('Fault shape substituted')
                with patch.object(local_agent,'invoke',measured):
                    result=self_heal.recover(case,config=config)
            event=result.get('event') or {}
            action=(event.get('plan') or {}).get('action')
            row.update(status=result.get('status'), action=action,
                       reason=(event.get('plan') or {}).get('reason'),
                       installed=bool((result.get('repair') or {}).get('installed')),
                       verified=bool(result.get('verified')), outcome=result.get('outcome'))
            if row['verified'] and not row['installed']:
                row['errors'].append('claimed_verification_without_install')
            if defect=='nonrepairable_prose' and row['verified']:
                row['errors'].append('unsupported_prose_claimed_repaired')
            if result.get('status')=='failed':
                row['errors'].append(str(event.get('error') or event.get('error_code') or 'recovery_failed'))
        except Exception as exc:
            row['errors'].append(f'{type(exc).__name__}: {exc}')
        row['total_ms']=round((time.perf_counter()-start)*1000,1)
        rows.append(row)
        print(json.dumps(row,ensure_ascii=False),flush=True)
    timings=[x for r in rows for x in r['model_ms']]
    summary={'cases':len(rows), 'model_calls':sum(r['model_calls'] for r in rows),
             'cases_without_model':sum(not r['model_calls'] for r in rows),
             'verified_installs':sum(bool(r.get('verified') and r.get('installed')) for r in rows),
             'scheduled':sum(r.get('status')=='requeued' for r in rows),
             'refused_or_pending':sum(r.get('status') in ('recorded','waiting_external','idle') for r in rows),
             'failed_cases':[r for r in rows if r['errors']],
             'incomplete_cases':[r for r in rows if r.get('status') in ('busy','cooldown','excluded')],
             'fixture_root':str(root),
             'model_latency_mean_ms':round(statistics.mean(timings),1) if timings else None,
             'model_latency_max_ms':max(timings) if timings else None,
             'scope':'one real recovery decision per fixture; not full semantic annotation, OCR/ASR or concurrent load'}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps({'summary':summary,'rows':rows},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return bool(summary['failed_cases'] or summary['incomplete_cases'])

if __name__=='__main__':
    raise SystemExit(main())
