"""Offline verification of prospective recovery amendments and preserved evidence."""
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path

ROOT=Path(__file__).resolve().parent

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def rows(path):return [json.loads(s) for s in Path(path).read_text().splitlines() if s.strip()]
def finite(x):return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)
def canonical(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def accounting(records):
    return sum(r['cost_usd'] if finite(r.get('cost_usd')) and r['cost_usd']>=0 else max(.01,r.get('reserve_usd',0)) for r in records)


def wrapper_path(filename):
    """Only a single Python basename in this archived module directory is safe."""
    if not isinstance(filename,str) or not filename or Path(filename).name!=filename or '/' in filename or '\\' in filename or Path(filename).suffix!='.py':
        raise ValueError('Continuation file must be a same-directory Python basename')
    path=ROOT/filename
    if path.resolve().parent!=ROOT.resolve():raise ValueError('Continuation source resolves outside the archived module directory')
    return path


def paced_evidence(amendment,meta,new_records,new_dispatch,runtime):
    declared={'original_worker_setting':16,'effective_worker_ceiling':2,'effective_max_active_requests':2,'min_dispatch_spacing_s':.25}
    checks={
        'paced_amendment_fields':all(amendment.get(k)==v for k,v in declared.items()),
        'paced_run_metadata':all(meta.get(k)==v for k,v in declared.items()),
        'paced_new_quality_row_fields':all(all(r.get(k)==v for k,v in declared.items()) and r.get('quality_pacing_amendment')=='capacity-recovery-two-active' and r.get('concurrency_pairs')==16 for r in new_records),
        'paced_runtime_ceiling':len(runtime)==1 and runtime[0].get('effective_max_active_requests')==2,
        'paced_rows_and_dispatches_match':len(new_records)==len(new_dispatch) and {r['sequence'] for r in new_records}=={r['sequence'] for r in new_dispatch},
    }
    try:
        times=[datetime.fromisoformat(d['dispatched_at']).timestamp() for d in new_dispatch]
        gaps=[b-a for a,b in zip(times,times[1:])]
        # Claims use a monotonic clock; saved wall timestamps have finite precision.
        checks['observed_dispatch_spacing_at_least_0_25s']=all(g>=.249 for g in gaps)
    except (KeyError,ValueError,TypeError):
        gaps=[];checks['observed_dispatch_spacing_at_least_0_25s']=False
    return checks,{'new_quality_rows':len(new_records),'observed_minimum_dispatch_gap_s':min(gaps) if gaps else None,'saved_timestamp_tolerance_s':.001}


def verify_continuations(run_dir,payload_factory=None):
    """Check prefix preservation, separate probes, observed reasoning and budget.

    payload_factory(case, spec) optionally rebuilds a request without credentials.
    Quality and score calculations are deliberately outside this audit.
    """
    run=Path(run_dir);meta=json.loads((run/'metadata.json').read_text());checks={};issues=[];details=[]
    amendments=meta.get('operational_amendments',[])
    records=rows(run/'responses.jsonl');main_ids={r['case_id'] for r in records}
    historical_recovery=0
    def check(name,condition):
        checks[name]=bool(condition)
        if not condition:issues.append(name)
    for index,a in enumerate(amendments):
        prefix=f'amendment_{index+1}_';probe_records=[]
        try:
            snapshot_path=Path(a['metadata_before_path']);snapshot=json.loads(snapshot_path.read_text())
            check(prefix+'metadata_snapshot_hash',sha(snapshot_path)==a['metadata_before_sha256'])
            filename=a.get('continuation_file','continuation.py')
            try:
                wrapper=wrapper_path(filename);check(prefix+'safe_continuation_filename',True)
                check(prefix+'wrapper_hash',sha(wrapper)==a['continuation_sha256'])
            except (ValueError,OSError):
                check(prefix+'safe_continuation_filename',False);check(prefix+'wrapper_hash',False)
            check(prefix+'saved_amendment_matches_metadata',json.loads((snapshot_path.parent/'amendment.json').read_text())==a)
            check(prefix+'frozen_configuration',snapshot['models']==meta['models']==a['models'] and snapshot['seed']==meta['seed']==a['seed'] and snapshot['concurrency_pairs']==meta['concurrency_pairs']==a['workers']==16)
            original=[]
            for name,old in a['pre_continuation'].items():
                data=(run/name).read_bytes();before=data[:old['bytes']]
                check(prefix+name+'_preserved_prefix',len(before)==old['bytes'] and hashlib.sha256(before).hexdigest()==old['sha256'])
                if name in ('plan.json','requests.jsonl'):check(prefix+name+'_unchanged',len(data)==old['bytes'])
                if name=='responses.jsonl':original=[json.loads(s) for s in before.decode().splitlines() if s.strip()]
            check(prefix+'original_counts',len(original)==a['original_attempts'] and sum(r.get('valid') is not True for r in original)==a['original_invalid'])
            check(prefix+'retained_cumulative_invalid_rate',bool(original) and sum(r.get('valid') is not True for r in original)/len(original)<.05)
            original_sequences={r['sequence'] for r in original};later=records[len(original):]
            check(prefix+'no_original_identity_retried',not original_sequences.intersection(r['sequence'] for r in later))
            probe=Path(a['recovery_run_dir']);pm=json.loads((probe/'metadata.json').read_text());probe_records=rows(probe/'responses.jsonl') if (probe/'responses.jsonl').exists() else []
            dispatch=rows(probe/'attempts.jsonl') if (probe/'attempts.jsonl').exists() else []
            cases={c['id']:c for c in rows(probe/'cases.jsonl')}
            check(prefix+'probe_is_separate',pm['phase']=='recovery_probe' and pm.get('excluded_from_quality_timing_and_cache_scores') is True and not main_ids.intersection(cases))
            check(prefix+'probe_metadata_hash',pm['continuation_sha256']==a['continuation_sha256'])
            check(prefix+'probe_continuation_file',pm.get('continuation_file','continuation.py')==filename)
            check(prefix+'probe_outcome_hash',not probe_records or sha(probe/'responses.jsonl')==pm['responses_sha256'])
            sequences=[r['sequence'] for r in probe_records]
            check(prefix+'probe_dispatches_unique_retained',len(set(sequences))==len(sequences)==len(dispatch) and set(sequences)=={r['sequence'] for r in dispatch})
            check(prefix+'probe_count_matches',len(probe_records)==pm['calls_completed']==a['recovery_calls'] and len(probe_records)<=2)
            success=a.get('status') in ('recovery_passed','completed','continuation_stopped')
            check(prefix+'recovery_result_consistent',(pm['status']=='complete' and len(probe_records)==2) if success else pm['status']=='stopped')
            for r in probe_records:
                key=prefix+f'probe_{r["sequence"]}_';c=cases[r['case_id']]
                request={'endpoint':r['endpoint'],'payload':r['request']}
                matching=[d for d in dispatch if d['sequence']==r['sequence']]
                digest=hashlib.sha256(canonical(request).encode()).hexdigest()
                check(key+'request_journal',len(matching)==1 and digest==r['request_sha256']==matching[0]['request_sha256'])
                check(key+'frozen_identity',r['phase']=='recovery_probe' and r['model_label']=='Qwen3.8 Flash' and r['requested_model']==a['models'][0]['id'] and all(r.get(k)==c.get(k) for k in ('experiment','target','target_kind')))
                if payload_factory:check(key+'exact_payload',request==payload_factory(c,a['models'][0]))
                bill=(r.get('response',{}).get('usage') or {}).get('cost')
                expected_bill=bill if finite(bill) and bill>=0 else None
                check(key+'billing_readback',r.get('cost_usd')==expected_bill)
                if success:
                    rt=(r.get('usage',{}).get('completion_tokens_details') or {}).get('reasoning_tokens')
                    msg=r['response']['choices'][0]['message'];answer=json.loads(msg['content'])
                    check(key+'valid_observed_zero_reasoning',r['valid'] is True and finite(rt) and rt==0 and not msg.get('reasoning') and not msg.get('reasoning_details'))
                    check(key+'strict_noul_answer',set(answer)=={'type','noul'} and answer['type']=='noul' and finite(answer['noul']) and 0<=answer['noul']<=1 and r['probability']==answer['noul'])
            spent=accounting(probe_records)
            known=sum(r['cost_usd'] for r in probe_records if finite(r.get('cost_usd')) and r['cost_usd']>=0)
            unknown=sum(not finite(r.get('cost_usd')) for r in probe_records)
            check(prefix+'probe_billing_complete_or_unknown',pm.get('charged_usd') is None if unknown else finite(pm.get('charged_usd')) and math.isclose(pm['charged_usd'],known,abs_tol=1e-12))
            check(prefix+'probe_budget_retained',math.isclose(spent,a['recovery_budget_accounted_usd'],abs_tol=1e-12) and math.isclose(spent,pm['budget_accounted_usd'],abs_tol=1e-12))
            check(prefix+'earlier_probe_budget_retained',math.isclose(historical_recovery,a.get('historical_recovery_budget_accounted_usd',0),abs_tol=1e-12))
            if success:
                runtime=[r for r in meta.get('continuation_runtime',[]) if r.get('amendment_id')==a['id']]
                check(prefix+'only_historical_circuit_rearmed',len(runtime)==1 and runtime[0]['cleared_reason'].startswith('Five consecutive invalid responses') and runtime[0]['cumulative_counts_retained'] is True)
                unknown_guard=sum(max(.01,r.get('reserve_usd',0)) for r in original if not finite(r.get('cost_usd')))
                check(prefix+'original_unknown_guard_retained',len(runtime)==1 and math.isclose(runtime[0]['unknown_bill_guard_retained'],unknown_guard,abs_tol=1e-12))
            paced=None
            if filename=='continuation_paced.py' or any(k in a for k in ('effective_worker_ceiling','effective_max_active_requests','min_dispatch_spacing_s')):
                plan=json.loads((run/'plan.json').read_text())
                unattempted=set(range(len(plan)))-original_sequences
                check(prefix+'paced_first_unattempted_sequence',bool(unattempted) and a.get('first_unattempted_sequence')==min(unattempted))
                if success:
                    end=a.get('final_quality_calls',len(records))
                    check(prefix+'paced_window_bounds',isinstance(end,int) and len(original)<=end<=len(records))
                    window=records[len(original):end];sequence_set={r['sequence'] for r in window}
                    dispatch_window=[d for d in rows(run/'attempts.jsonl') if d['sequence'] in sequence_set]
                    pacing_checks,paced=paced_evidence(a,meta,window,dispatch_window,runtime)
                    for name,value in pacing_checks.items():check(prefix+name,value)
            historical_recovery+=spent
            details.append({'id':a['id'],'status':a['status'],'continuation_file':filename,'original_attempts':len(original),'original_invalid':sum(r.get('valid') is not True for r in original),'recovery_calls':len(probe_records),'recovery_budget_accounted_usd':spent,'paced_evidence':paced})
        except (OSError,KeyError,ValueError,TypeError,IndexError) as exc:
            check(prefix+'evidence_readable',False);issues.append(prefix+'error_type_'+type(exc).__name__)
    if amendments:
        last=amendments[-1]
        if last.get('status') in ('completed','continuation_stopped'):
            check('quality_budget_excludes_all_linked_probe_charges',math.isclose(meta['budget_usd']+historical_recovery,last['total_quality_and_recovery_budget_usd'],abs_tol=1e-12))
    return {'passed':all(checks.values()),'checks':checks,'issues':issues,'amendments':details,'linked_recovery_budget_accounted_usd':historical_recovery,
            'limitations':['Successful serial probes establish momentary service response; they do not establish sustained provider capacity. Pacing checks compare declared executor limits and recorded dispatch spacing; they do not measure upstream internal concurrency. Original invalid quality outcomes remain in their original denominators.']}
