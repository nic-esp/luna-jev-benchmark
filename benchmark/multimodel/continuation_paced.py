#!/usr/bin/env python3
"""Prospective two-active-request service-recovery continuation; no request occurs on import.

resume_qwen(key, run_dir, budget=5, progress=None) performs two separately
journaled synthetic recovery probes, then resumes only unattempted identities.
The original runner, adapter, cases, request plan and saved outcomes stay intact.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import threading
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parent
LABEL='Qwen3.8 Flash'
EFFECTIVE_CONCURRENCY=2
MIN_DISPATCH_SPACING_S=0.25


def stamp():return datetime.now(timezone.utc).isoformat()
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def rows(path):return [json.loads(s) for s in Path(path).read_text().splitlines() if s.strip()]

def _load_protocol():
    spec=importlib.util.spec_from_file_location('frozen_recovery_runner',ROOT/'run_study.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    original=module.run_once
    executor=module.ThreadPoolExecutor
    class LimitedExecutor(executor):
        def __init__(self,max_workers=None,**kwargs):
            if max_workers!=16:raise ValueError('Paced recovery expects the preserved 16-worker schedule')
            super().__init__(max_workers=EFFECTIVE_CONCURRENCY,**kwargs)
    def paced_once(client,key,entry,prepared):
        row=original(client,key,entry,prepared)
        if entry['phase']=='quality':
            row['effective_max_active_requests']=EFFECTIVE_CONCURRENCY
            row['effective_worker_ceiling']=EFFECTIVE_CONCURRENCY
            row['original_worker_setting']=16
            row['quality_pacing_amendment']='capacity-recovery-two-active'
            row['min_dispatch_spacing_s']=MIN_DISPATCH_SPACING_S
        return row
    # Cap actual workers before Session.claim writes a dispatch. Tasks still
    # queued after a circuit stop reach the existing guard before any call.
    module.ThreadPoolExecutor=LimitedExecutor
    module.run_once=paced_once
    return module


def _cost_guard(protocol,records,plan):
    guard=protocol.Budget(1e100)
    for r in records:guard.settle(plan[r['sequence']]['reserve_usd'],r,reserved=False)
    return guard.snapshot()


def _validate_prior(protocol,run):
    meta=json.loads((run/'metadata.json').read_text());plan=json.loads((run/'plan.json').read_text())
    rr=rows(run/'responses.jsonl');journal=rows(run/'attempts.jsonl')
    if meta.get('status')!='stopped' or meta.get('phase')!='quality':raise ValueError('Only a stopped quality run can use this amendment')
    if meta.get('concurrency_pairs')!=16:raise ValueError('The frozen continuation requires worker count 16')
    if len(meta.get('models',[]))!=1 or meta['models'][0]['label']!=LABEL or meta['models'][0].get('reasoning')!={'effort':'none'}:
        raise ValueError('This continuation is restricted to the frozen zero-reasoning Qwen configuration')
    reason=meta.get('stop_reason') or meta.get('budget_stop_reason') or ''
    if not reason.startswith('Five consecutive invalid responses'):
        raise ValueError('This amendment only re-arms a historical consecutive-failure circuit')
    for name,path in protocol.source_files().items():
        expected=meta.get('code_hashes',{}).get(name)
        if not expected or sha(path)!=expected or sha(run/name)!=expected:raise ValueError('Frozen source/adapter snapshot changed: '+name)
    for name,key in [('plan.json','plan_sha256'),('requests.jsonl','requests_sha256'),('responses.jsonl','responses_sha256'),('attempts.jsonl','attempts_sha256')]:
        if not meta.get(key) or sha(run/name)!=meta[key]:raise ValueError('Frozen evidence hash changed: '+name)
    _,_,source_hashes=protocol.load_frozen_sources()
    if meta['source_hashes']!=source_hashes:raise ValueError('Frozen dataset identity changed')
    seen={r['sequence'] for r in rr};dispatched={r['sequence'] for r in journal}
    if len(seen)!=len(rr) or len(dispatched)!=len(journal) or seen!=dispatched:raise ValueError('Every original dispatch must have one retained outcome before recovery')
    if len(rr)>=len(plan):raise ValueError('No unattempted quality requests remain')
    for r in rr:
        seq=r['sequence']
        if not 0<=seq<len(plan) or any(r.get(k)!=plan[seq][k] for k in ('case_id','model_label','phase','repeat','request_sha256')):
            raise ValueError('Original outcome does not match its frozen identity')
    for amendment in meta.get('operational_amendments',[]):
        probe=Path(amendment.get('recovery_run_dir',''))
        if not amendment.get('recovery_run_dir') or not probe.exists():continue
        dispatched=rows(probe/'attempts.jsonl') if (probe/'attempts.jsonl').exists() else []
        outcomes=rows(probe/'responses.jsonl') if (probe/'responses.jsonl').exists() else []
        if {r['sequence'] for r in dispatched}!={r['sequence'] for r in outcomes}:
            raise ValueError('An earlier recovery dispatch has no retained outcome; account for it explicitly before further calls')
        # Reconstruct prior probe accounting from its durable evidence even if
        # a process stopped before copying the final subtotal into this run.
        amendment['recovery_budget_accounted_usd']=sum(r['cost_usd'] if protocol.nonnegative(r.get('cost_usd')) else max(protocol.UNKNOWN_BILL_ALLOWANCE_USD,r.get('reserve_usd',0)) for r in outcomes)
    invalid=sum(r.get('valid') is not True for r in rr)
    if not rr or invalid/len(rr)>=.05:raise ValueError('Cumulative invalid rate must be below 5% before continuation')
    return meta,plan,rr,journal


def _recovery_cases(protocol,identifier):
    cases=deepcopy(protocol.base.warmup_cases())
    if len(cases)!=2:raise ValueError('Expected exactly two independent synthetic recovery cases')
    for i,c in enumerate(cases):
        c['id']=f'recovery-{identifier}-{i+1}'
        c['experiment']='recovery_protocol_check'
    return cases


def _recovery_probes(key,protocol,spec,budget_available,directory,identifier,progress):
    directory.mkdir(parents=True,exist_ok=False);cases=_recovery_cases(protocol,identifier)
    (directory/'cases.jsonl').write_text(''.join(json.dumps(c)+'\n' for c in cases))
    meta={'created_at':stamp(),'status':'running','phase':'recovery_probe','models':[spec],
          'planned_calls':2,'protocol':'Two synthetic service-health probes; validity and explicitly observed zero reasoning are required. Correctness is not a recovery selection criterion.',
          'excluded_from_quality_timing_and_cache_scores':True,'continuation_sha256':sha(Path(__file__)),'continuation_file':Path(__file__).name}
    protocol.save(directory/'metadata.json',meta);records=[];guard=protocol.Budget(budget_available)
    client=protocol.adapter_class()(key)
    try:
        for sequence,c in enumerate(cases):
            request=client.build_request(c,spec);protocol.validate_request(c,spec,request)
            reservation=max(protocol.UNKNOWN_BILL_ALLOWANCE_USD,client.reserve_usd(request,spec))
            if not guard.reserve(reservation):raise ValueError('Recovery probe budget guard stopped before dispatch')
            entry={'sequence':sequence,'pair_index':sequence,'case_id':c['id'],'model_label':LABEL,'phase':'recovery_probe','repeat':0,
                   'request_sha256':protocol.digest(protocol.canonical(request).encode()),'reserve_usd':reservation}
            protocol.append(directory/'attempts.jsonl',{'sequence':sequence,'request_sha256':entry['request_sha256'],'dispatched_at':stamp()})
            r=protocol.run_once(client,key,entry,(c,spec,request));records.append(r)
            protocol.append(directory/'responses.jsonl',r);guard.settle(reservation,r)
            usage=r.get('usage') or {};reasoning=(usage.get('completion_tokens_details') or {}).get('reasoning_tokens')
            data=r.get('response') or {};choices=data.get('choices') or [];message=choices[0].get('message',{}) if choices else {}
            observed_zero=isinstance(reasoning,(int,float)) and not isinstance(reasoning,bool) and reasoning==0
            if r.get('valid') is not True or not observed_zero or message.get('reasoning') or message.get('reasoning_details'):
                raise ValueError('Recovery probe did not establish a valid response with observed zero reasoning')
            if progress:
                try:progress({'kind':'recovery_probe','completed':len(records),'planned':2,'valid':True})
                except Exception:meta['progress_callback_errors']=meta.get('progress_callback_errors',0)+1
        meta['status']='complete'
    except Exception as exc:
        # No private exception body or adapter/key representation is recorded.
        meta.update(status='stopped',error=type(exc).__name__+': recovery conditions were not satisfied')
    finally:
        try:client.close()
        except Exception:meta['close_error']=True
        meta.update(finished_at=stamp(),calls_completed=len(records),**guard.snapshot())
        if (directory/'responses.jsonl').exists():meta['responses_sha256']=sha(directory/'responses.jsonl')
        protocol.save(directory/'metadata.json',meta)
    return meta


def _armed_session_class(protocol,amendment):
    original=protocol.Session
    class RecoveredSession(original):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            self.pacing_lock=threading.Lock()
            self.last_claim_at=None
            reason=self.guard.stop_reason or ''
            if not reason.startswith('Five consecutive invalid responses'):
                raise ValueError('Refusing to clear a stop other than the replayed historical consecutive-failure circuit')
            for group in self.circuit.groups.values():
                if group['n'] and group['invalid']/group['n']>=.05:
                    raise ValueError('Cumulative invalid rate is at least 5%; do not re-arm')
            if self.guard.spent+self.guard.unknown_guard+self.guard.pending>=self.guard.limit:
                raise ValueError('Recovery cannot clear an exhausted budget')
            # Counters and every prior unknown-bill reservation are retained.
            # Only the stale consecutive-run state is re-armed after two probes.
            old_streaks={k:v['consecutive'] for k,v in self.circuit.groups.items()}
            self.guard.stop_reason=None
            for group in self.circuit.groups.values():group['consecutive']=0
            self.meta.setdefault('continuation_runtime',[]).append({'amendment_id':amendment['id'],'armed_at':stamp(),
                'cleared_reason':reason,'prior_consecutive_counts':old_streaks,'cumulative_counts_retained':True,
                'unknown_bill_guard_retained':self.guard.unknown_guard,'effective_max_active_requests':EFFECTIVE_CONCURRENCY})
        def claim(self,entry):
            # Waiting precedes original claim, so journal/timer exclude pacing.
            # The original guard is rechecked after waiting.
            with self.pacing_lock:
                if self.last_claim_at is not None:
                    delay=MIN_DISPATCH_SPACING_S-(time.monotonic()-self.last_claim_at)
                    if delay>0:time.sleep(delay)
                claimed=super().claim(entry)
                if claimed:self.last_claim_at=time.monotonic()
                return claimed
    return RecoveredSession


def resume_qwen(key,run_dir,budget=5,progress=None):
    """Root calls this with its memory-held Alpha credential; no other credentials."""
    protocol=_load_protocol();run=Path(run_dir);protocol.Budget(budget)
    original_session=protocol.Session
    with protocol.isolated_phase('prospective_qwen_recovery'):
        meta,plan,prior,journal=_validate_prior(protocol,run)
        identifier=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        history=run/'continuation-history'/identifier;history.mkdir(parents=True,exist_ok=False)
        (history/'metadata-before.json').write_bytes((run/'metadata.json').read_bytes())
        baseline={name:{'sha256':sha(run/name),'bytes':(run/name).stat().st_size} for name in ('responses.jsonl','attempts.jsonl','plan.json','requests.jsonl')}
        probe_dir=ROOT/'runs'/('probes-recovery-'+identifier)
        amendment={'id':identifier,'decided_at_utc':stamp(),'status':'prospective','continuation_sha256':sha(Path(__file__)),'continuation_file':Path(__file__).name,
            'pre_continuation':baseline,'metadata_before_sha256':sha(history/'metadata-before.json'),
            'metadata_before_path':str((history/'metadata-before.json').resolve()),'recovery_run_dir':str(probe_dir.resolve()),
            'original_attempts':len(prior),'original_invalid':sum(r.get('valid') is not True for r in prior),'unattempted_requests':len(plan)-len(prior),
            'models':meta['models'],'workers':16,'original_worker_setting':16,'effective_worker_ceiling':EFFECTIVE_CONCURRENCY,'effective_max_active_requests':EFFECTIVE_CONCURRENCY,'min_dispatch_spacing_s':MIN_DISPATCH_SPACING_S,'first_unattempted_sequence':min(set(range(len(plan)))-{r['sequence'] for r in prior}),'seed':meta['seed'],'total_quality_and_recovery_budget_usd':budget,
            'change':'Prospectively limit active outbound calls to two after two worker-16 episodes hit the shared provider quota. The saved original worker setting remains 16; actual executor workers are prospectively capped at two before the dispatch journal and adapter timer. Every new row records the effective ceiling. A minimum 0.25-second spacing is enforced before dispatch claims, with the original stop and budget guards rechecked after waiting. This changes the remaining bulk dispatch schedule; bulk timing remains diagnostic only. No provider, prompt, schema, reasoning setting, case, score or attempted request changes. After two valid synthetic service-health probes with observed zero reasoning, clear only the replayed historical consecutive-failure stop and streaks. Preserve cumulative invalid counts, every original attempt and bill, all unknown-bill reservations, request hashes, worker16 and the original live circuit. Dispatch only previously unattempted sequences. No benchmark output was selected for removal or retry.'}
        protocol.save(history/'amendment.json',amendment)
        meta.setdefault('operational_amendments',[]).append(amendment)
        meta.update(original_worker_setting=16,effective_worker_ceiling=EFFECTIVE_CONCURRENCY,effective_max_active_requests=EFFECTIVE_CONCURRENCY,min_dispatch_spacing_s=MIN_DISPATCH_SPACING_S)
        protocol.save(run/'metadata.json',meta)
        prior_guard=_cost_guard(protocol,prior,plan)
        historical_recovery_guard=sum(a.get('recovery_budget_accounted_usd',0) for a in meta['operational_amendments'][:-1])
        remaining=budget-prior_guard['budget_accounted_usd']-historical_recovery_guard
        if remaining<=0:raise ValueError('No budget remains for service recovery')
        recovery=_recovery_probes(key,protocol,meta['models'][0],remaining,probe_dir,identifier,progress)
        amendment.update(status='recovery_passed' if recovery['status']=='complete' else 'recovery_failed',recovery_finished_at=recovery['finished_at'],
            recovery_budget_accounted_usd=recovery['budget_accounted_usd'],historical_recovery_budget_accounted_usd=historical_recovery_guard,recovery_calls=recovery['calls_completed'])
        protocol.save(history/'amendment.json',amendment)
        meta['operational_amendments'][-1]=amendment;protocol.save(run/'metadata.json',meta)
        if recovery['status']!='complete':return {'status':'recovery_failed','run_dir':str(run),'recovery':recovery,'amendment':amendment}
        for name,previous in baseline.items():
            if sha(run/name)!=previous['sha256']:raise ValueError('Evidence changed during recovery: '+name)
    # The original runner acquires its own exclusive phase lock. Its resume
    # checks revalidate all source/request hashes and never replay claimed IDs.
    protocol.Session=_armed_session_class(protocol,amendment)
    try:
        result=protocol.run_quality(key,meta['models'],out_dir=run,budget=budget-historical_recovery_guard-recovery['budget_accounted_usd'],workers=16,seed=meta['seed'],progress=progress,resume=True)
    finally:
        protocol.Session=original_session
    final=json.loads((run/'metadata.json').read_text())
    amendment.update(status='completed' if final.get('status')=='complete' else 'continuation_stopped',finished_at_utc=stamp(),
                     final_quality_calls=final.get('calls_completed'),final_quality_unknown_bills=final.get('unknown_billing'))
    final['operational_amendments'][-1]=amendment;protocol.save(run/'metadata.json',final);protocol.save(history/'amendment.json',amendment)
    for name in ('responses.jsonl','attempts.jsonl'):
        previous=baseline[name];prefix=(run/name).read_bytes()[:previous['bytes']]
        if hashlib.sha256(prefix).hexdigest()!=previous['sha256']:raise ValueError('Original evidence prefix changed: '+name)
    return {**result,'metadata':final,'recovery':recovery,'amendment':amendment}
