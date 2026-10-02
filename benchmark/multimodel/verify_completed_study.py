#!/usr/bin/env python3
"""Audit explicitly supplied completed study directories, entirely offline.

Example:
  python3 multimodel/verify_completed_study.py --quality OLD QUALITY_QWEN QUALITY_NEW \
      --timing TIMING_ALL --cache CACHE_ALL
No credential, controller, socket, HTTP client or model call is used.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent
sys.dont_write_bytecode=True
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import verify as audit
import adapter
import run_study as protocol
import verify_aborted_cache as aborted_cache


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read_rows(path):return [json.loads(s) for s in Path(path).read_text().splitlines() if s.strip()]
def json_read(path):return json.loads(Path(path).read_text())
def snapshot(run):
    return {p.name:{'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(Path(run).iterdir()) if p.is_file() and p.name in ('metadata.json','responses.jsonl','attempts.jsonl','dispatches.jsonl','plan.json','requests.jsonl','cases.jsonl','primes.json','rulebook.txt','aborted-session-audit.json','close_aborted_cache.py')}


def verify_probes(directory):
    """Every setup response is retained; unavailable probes may validly fail."""
    run=Path(directory);meta=json_read(run/'metadata.json');rr=read_rows(run/'responses.jsonl')
    cases={c['id']:c for c in (read_rows(run/'cases.jsonl') if (run/'cases.jsonl').exists() else protocol.base.warmup_cases())}
    planner=adapter.Adapter.__new__(adapter.Adapter);specs=audit.model_specs(meta);checks={};issues=[];observed=[]
    identities=[(r['model_label'],r['case_id'],r.get('sequence')) for r in rr]
    checks['unique_probe_identities']=len(identities)==len(set(identities))
    checks['probe_phases_excluded_from_benchmarks']=all(r.get('phase') in ('setup_probe','recovery_probe') for r in rr)
    if meta.get('phase')=='recovery_probe':checks['recovery_stopped_or_complete']=meta.get('status') in ('complete','stopped')
    else:checks['two_synthetic_fixtures_per_declared_model']={(r['model_label'],r['case_id']) for r in rr}=={(m,c) for m in specs for c in cases} and len(rr)==2*len(specs)
    for r in rr:
        if r['case_id'] not in cases or r['model_label'] not in specs:
            issues.append({'sequence':r.get('sequence'),'issues':['unknown_case_or_model']});continue
        case=cases[r['case_id']];spec=specs[r['model_label']];request=planner.build_request(case,spec)
        problems,usage=audit.check_record(r,case,meta,lambda c,l,row,m:(request['endpoint'],request['payload']))
        if problems:issues.append({'sequence':r.get('sequence'),'model':r['model_label'],'issues':problems})
        if r.get('valid') is True:observed.append(usage)
    checks['all_record_contracts_and_payloads_match']=not issues
    return {'passed':all(checks.values()),'checks':checks,'issues':issues,'attempts':len(rr),'valid':sum(r.get('valid') is True for r in rr),
        'cost':audit.base.costs(rr),'run_dir':str(run.resolve()),'hashes':snapshot(run),
        'reasoning':{'valid_calls':len(observed),'counter_observed':sum(x.get('reasoning_tokens') is not None for x in observed),'counter_unknown':sum(x.get('reasoning_tokens') is None for x in observed),'positive_calls':sum((x.get('reasoning_tokens') or 0)>0 for x in observed),'visible_calls':sum(bool(x.get('generated_reasoning_present')) for x in observed)},
        'limitations':['Setup probes are excluded from benchmark scores. Failed probes stay in the ledger and missing bills stay unknown.']}


def verify_completed(quality_dirs,timing_dir,cache_dir,*,probe_dirs=None,supplementary_cache_dirs=None,selection_path=ROOT/'model-selection.json',output_path=ROOT/'verification.json'):
    quality_dirs=[Path(p).resolve() for p in quality_dirs];timing_dir=Path(timing_dir).resolve();cache_dir=Path(cache_dir).resolve()
    primary=quality_dirs+[timing_dir,cache_dir]
    probes=[Path(p).resolve() for p in (probe_dirs if probe_dirs is not None else sorted((ROOT/'runs').glob('probes-*'))) if (Path(p)/'responses.jsonl').exists()]
    supplementary=[Path(p).resolve() for p in (supplementary_cache_dirs if supplementary_cache_dirs is not None else [p.parent for p in sorted((ROOT/'runs').glob('cache-*/aborted-session-audit.json'))])]
    all_inputs=primary+probes+supplementary
    selection_path=Path(selection_path);selection=json_read(selection_path);models=[s['label'] for s in selection['models']]
    result={'generated_at_utc':datetime.now(timezone.utc).isoformat(),'passed':False,'status':'failed','models':models,
        'scope':'Reused Jev/Luna quality, added-model quality, common new timing, common new cache, all supplied setup/recovery probes, and separately audited supplementary aborted-cache closures. Supplementary rows contribute only to the ledger, never primary estimates. Earlier historical timing/cache protocols are documented in their original audits.',
        'input_paths':{'quality':[str(p) for p in quality_dirs],'timing':str(timing_dir),'cache':str(cache_dir),'probes':[str(p) for p in probes],'supplementary_aborted_cache':[str(p) for p in supplementary]},
        'checks':{},'errors':[],'verification_code_sha256':sha(__file__),'selection_sha256':sha(selection_path),
        'analysis_spec_sha256':sha(ROOT/'analysis-spec.json'),'generic_verifier_sha256':sha(ROOT/'verify.py')}
    checks=result['checks'];checks['all_input_sources_distinct']=len(set(all_inputs))==len(all_inputs)
    missing=[str(p) for p in primary if not (p/'metadata.json').exists() or not (p/'responses.jsonl').exists()]
    checks['all_primary_sources_exist']=not missing
    if missing:result['errors'].append({'missing_inputs':missing})
    if not missing:
        metadata={p:json_read(p/'metadata.json') for p in primary}
        checks['all_primary_runs_complete']=all(m.get('status')=='complete' for m in metadata.values())
        if not checks['all_primary_runs_complete']:
            result['errors'].append({'incomplete_inputs':{str(p):m.get('status') for p,m in metadata.items() if m.get('status')!='complete'}})
    if all(checks.values()):
        before={str(p):snapshot(p) for p in all_inputs};result['evidence_before']=before
        try:
            study=audit.verify_study(quality_dirs,timing_dir,[cache_dir],models=models)
            result['study']=study;checks['quality_timing_cache_audits_pass']=study['passed']
            setup=[]
            for p in probes:
                try:setup.append(verify_probes(p))
                except Exception as exc:setup.append({'passed':False,'run_dir':str(p),'error_type':type(exc).__name__,'error':str(exc)})
            result['probes']=setup;checks['all_probe_audits_pass']=all(s['passed'] for s in setup)
            supplemental=[aborted_cache.verify_aborted_cache(p) for p in supplementary]
            result['supplementary_aborted_cache']=supplemental;checks['all_aborted_cache_closures_pass']=all(s['passed'] for s in supplemental)
            expected={s['label']:s for s in selection['models']}
            config_fields=('id','provider','reasoning','cache_control','max_tokens')
            config_checks={}
            for p,m in metadata.items():
                specs=audit.model_specs(m)
                if p==timing_dir or p==cache_dir:config_checks[str(p)+'_all_selected_models']=set(specs)==set(expected)
                # Legacy reference request/provider settings are checked by its
                # original independent verifier; its metadata has an older shape.
                if m.get('code_hashes') or p==cache_dir:
                    config_checks[str(p)+'_selected_model_settings']=all(label in expected and all(spec.get(k)==expected[label].get(k) for k in config_fields) for label,spec in specs.items())
            result['selected_configuration_checks']=config_checks;checks['selected_configurations_match']=all(config_checks.values())
            all_rows=[];ledger=[]
            for role,paths in [('quality',quality_dirs),('timing',[timing_dir]),('cache',[cache_dir]),('setup',probes),('supplementary_aborted_cache',supplementary)]:
                for p in paths:
                    rr=read_rows(p/'responses.jsonl');all_rows.extend(rr)
                    ledger.append({'role':role,'primary_estimate_eligible':role in ('quality','timing','cache'),'run_dir':str(p),'attempts':len(rr),'valid':sum(r.get('valid') is True for r in rr),'phase_counts':dict(Counter(r.get('phase') for r in rr)),'cost':audit.base.costs(rr),'raw_sha256':sha(p/'responses.jsonl')})
            result['ledger']=ledger;result['totals']={'attempts':len(all_rows),'valid':sum(r.get('valid') is True for r in all_rows),'invalid':sum(r.get('valid') is not True for r in all_rows),'cost':audit.base.costs(all_rows)}
            # Every linked recovery must occur exactly once in the setup ledger.
            linked={str(Path(a['recovery_run_dir']).resolve()) for m in metadata.values() for a in m.get('operational_amendments',[]) if a.get('recovery_run_dir') and (Path(a['recovery_run_dir'])/'responses.jsonl').exists()}
            checks['all_linked_recovery_probes_in_ledger']=linked<=set(str(p) for p in probes)
            after={str(p):snapshot(p) for p in all_inputs};checks['all_inputs_unchanged_during_audit']=before==after;result['evidence_after']=after
        except Exception as exc:
            checks['verification_completed']=False;result['errors'].append({'error_type':type(exc).__name__,'error':str(exc)})
    result['passed']=bool(checks) and all(checks.values());result['status']='passed' if result['passed'] else 'failed'
    out=Path(output_path);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--quality',nargs='+',type=Path,required=True);p.add_argument('--timing',type=Path,required=True);p.add_argument('--cache',type=Path,required=True)
    p.add_argument('--probes',nargs='*',type=Path);p.add_argument('--supplementary-cache',nargs='*',type=Path,help='Aborted cache session directories; default discovers closure audits');p.add_argument('--selection',type=Path,default=ROOT/'model-selection.json');p.add_argument('--output',type=Path,default=ROOT/'verification.json');a=p.parse_args()
    result=verify_completed(a.quality,a.timing,a.cache,probe_dirs=a.probes,supplementary_cache_dirs=a.supplementary_cache,selection_path=a.selection,output_path=a.output)
    print(json.dumps({'passed':result['passed'],'status':result['status'],'output':str(a.output.resolve()),'failed_checks':[k for k,v in result['checks'].items() if not v],'totals':result.get('totals')}))
    return 0 if result['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
