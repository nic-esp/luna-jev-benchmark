#!/usr/bin/env python3
"""Independently verify frozen plans, every recorded request, answers and billing.

No API calls. Run only after the supplied runs have finished. Failed attempts
remain evidence; a complete run need not have 100% successful responses.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import random
import sys

ROOT=Path(__file__).resolve().parent
PILOT=ROOT.parent
if str(PILOT) not in sys.path:sys.path.insert(0,str(PILOT))
import runner as adapter


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def sha_bytes(value):return hashlib.sha256(value).hexdigest()
def read_rows(path):return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
def load(path):return json.loads(Path(path).read_text())
def finite(x):return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)
def same(a,b):return finite(a) and finite(b) and math.isclose(a,b,rel_tol=1e-10,abs_tol=1e-12)


class Audit:
    def __init__(self):self.checks={};self.details={}
    def check(self,name,condition,detail=None):
        self.checks[name]=bool(condition)
        if not condition and detail is not None:self.details[name]=detail
    def finish(self,**extra):return {'passed':all(self.checks.values()),'checks':self.checks,'issues':self.details,**extra}


def billing(a,rs,meta):
    known=[r['cost_usd'] for r in rs if finite(r.get('cost_usd')) and r['cost_usd']>=0]
    missing=len(rs)-len(known);subtotal=math.fsum(known)
    a.check('missing_bills_not_imputed',all(r.get('cost_usd') is None or (finite(r['cost_usd']) and r['cost_usd']>=0) for r in rs))
    a.check('metadata_total_billing_semantics',meta.get('charged_usd') is None if missing else same(meta.get('charged_usd'),subtotal))
    if 'known_charge_subtotal_usd' in meta:a.check('known_billed_subtotal_reconciles',same(meta['known_charge_subtotal_usd'],subtotal))
    if 'unknown_billing' in meta:a.check('unknown_bill_count_reconciles',meta['unknown_billing']==missing)
    if 'cost_missing_n' in meta:a.check('missing_bill_count_reconciles',meta['cost_missing_n']==missing)
    if 'unknown_charge_allowance_total_usd' in meta:
        allowance=meta.get('unknown_charge_allowance_per_attempt_usd',0)*missing
        a.check('allowance_is_separate_from_reported_bill',same(meta['unknown_charge_allowance_total_usd'],allowance) and same(meta.get('budget_accounted_usd'),subtotal+allowance))
    return {'known_billed_usd':subtotal,'missing_bills':missing,'total_billed_usd':None if missing else subtotal}


def check_record(row,case,endpoint,payload,label,cache_mode=None):
    errors=[]
    def check(name,truth):
        if not truth:errors.append(name)
    check('target/source',row.get('target')==case['target'] and row.get('target_kind')==case['target_kind'] and row.get('experiment')==case['experiment'])
    check('payload_exact',row.get('request')==payload and row.get('endpoint')==endpoint)
    base='Jev' if label=='Jev' else 'Luna'
    check('requested_model',row.get('requested_model')==adapter.MODELS[base])
    check('latency',finite(row.get('latency_s')) and row['latency_s']>0)
    recorded_cost=row.get('cost_usd');raw_cost=(row.get('response') or {}).get('usage',{}).get('cost')
    check('raw_billing',recorded_cost==raw_cost)
    if row.get('valid') is True:
        check('success_status',row.get('http_status')==200 and row.get('error') is None)
        try:
            data=row['response']
            answer=data['answers']['answer'] if base=='Jev' else json.loads(data['choices'][0]['message']['content'])
            p=answer['noul']
            check('answer_schema',set(answer)=={'type','noul'} and answer['type']=='noul' and finite(p) and 0<=p<=1)
            check('answer_readback',row.get('answer')==answer and row.get('probability')==p)
            check('model_readback',row.get('response_model')==data.get('model') and row.get('provider')==data.get('provider'))
            check('served_model',row.get('response_model')==adapter.MODELS[base] or str(row.get('response_model','')).startswith(adapter.MODELS[base]+'-20'))
            check('usage_readback',row.get('usage')==data.get('usage'))
            if base=='Luna':
                check('luna_provider',row.get('provider')=='OpenAI')
                check('luna_finished',data['choices'][0].get('finish_reason')=='stop')
                check('no_extra_generated_reasoning_text',not data['choices'][0]['message'].get('reasoning') and not data['choices'][0]['message'].get('reasoning_details'))
                check('luna_no_reasoning',row['usage'].get('completion_tokens_details',{}).get('reasoning_tokens')==0 and payload.get('reasoning')=={'effort':'none'})
                counts=row['usage'].get('prompt_tokens_details',{})
                if cache_mode=='off':check('cache_off_observed',counts.get('cached_tokens')==0 and counts.get('cache_write_tokens')==0)
            else:check('jev_provider',row.get('provider')=='TypeSafe')
        except (KeyError,IndexError,TypeError,ValueError):errors.append('successful_response_unparseable')
    else:
        check('failure_explicit',row.get('valid') is False and bool(row.get('error')))
        check('failure_has_no_prediction',row.get('probability') is None)
    return errors


def usage_statistics(rs):
    result={}
    for model in ('Jev','Luna'):
        selected=[r for r in rs if r.get('model_label')==model and r.get('valid') is True]
        outputs=[];inputs=[];reasoning=[];fields=Counter()
        for r in selected:
            u=r.get('usage',{})
            for field in ('completion_tokens','output_tokens'):
                if finite(u.get(field)):
                    outputs.append(u[field]);fields[field]+=1;break
            for field in ('prompt_tokens','input_tokens'):
                if finite(u.get(field)):inputs.append(u[field]);break
            reason=u.get('completion_tokens_details',{}).get('reasoning_tokens')
            if finite(reason):reasoning.append(reason)
        def stats(values):
            ordered=sorted(values);n=len(ordered)
            return {'observed':n,'min':min(ordered) if n else None,'max':max(ordered) if n else None,'mean':math.fsum(ordered)/n if n else None,'total':math.fsum(ordered) if n else None}
        result[model]={'valid_calls':len(selected),'reported_output_tokens':stats(outputs),'reported_output_token_source_fields':dict(fields),'reported_input_tokens':stats(inputs),'reported_reasoning_tokens':stats(reasoning),'luna_requested_output_ceiling':128 if model=='Luna' else None}
    return result


def verify_quality(run_dir,cases_path=None):
    run=Path(run_dir);cp=Path(cases_path or ROOT/'data/cases.jsonl');cases_list=read_rows(cp);cases={c['id']:c for c in cases_list}
    meta=load(run/'metadata.json');rs=read_rows(run/'responses.jsonl');plan=load(run/'plan.json');audit=Audit()
    audit.check('run_completed',meta.get('status')=='complete')
    audit.check('frozen_cases_hash',sha(cp)==meta.get('cases_sha256'))
    snapshot=run/'original-runner.py';source=snapshot if snapshot.exists() else ROOT/'run_study.py'
    audit.check('initial_runner_hash',sha(source)==meta.get('runner_sha256'))
    audit.check('current_shared_adapter_hash',sha(PILOT/'runner.py')==meta.get('adapter_sha256'))
    regen_cases=list(cases_list);rng=random.Random(meta['seed']);rng.shuffle(regen_cases);regen=[]
    for i,c in enumerate(regen_cases):
        arms=list(adapter.MODELS);rng.shuffle(arms);regen.append({'pair_index':i,'case_id':c['id'],'models':arms})
    audit.check('frozen_random_plan_reproduces',plan==regen)
    expected={(item['case_id'],model):(item['pair_index'],2*item['pair_index']+j) for item in plan for j,model in enumerate(item['models'])}
    identities=[(r.get('case_id'),r.get('model_label')) for r in rs]
    audit.check('all_expected_identities_exactly_once',len(identities)==len(expected) and len(set(identities))==len(identities) and set(identities)==set(expected))
    audit.check('metadata_counts_match',meta.get('calls_completed')==len(rs) and meta.get('planned_calls')==2*len(cases))
    failures=[];bad=[]
    for row in rs:
        identity=(row.get('case_id'),row.get('model_label'))
        if identity not in expected:bad.append({'identity':identity,'issues':['unexpected_identity']});continue
        pair_index,sequence=expected[identity];c=cases[identity[0]];endpoint,payload=adapter.payload_for(c,identity[1])
        issues=check_record(row,c,endpoint,payload,identity[1],'off')
        if row.get('sequence')!=sequence or row.get('pair_index')!=pair_index or row.get('phase')!='quality' or row.get('repeat',0)!=0:issues.append('frozen_sequence_phase_repeat')
        if row.get('measurement_role')!='bulk_quality':issues.append('bulk_measurement_role')
        if issues:bad.append({'identity':identity,'issues':issues})
        if not row.get('valid'):failures.append({'case_id':row['case_id'],'model':row['model_label'],'http_status':row.get('http_status'),'cost_usd':row.get('cost_usd'),'error':row.get('error')})
    audit.check('all_requests_responses_and_targets_match',not bad,bad[:20])
    raw=(run/'responses.jsonl').read_bytes()
    for i,amend in enumerate(meta.get('protocol_amendments',[]),1):
        prefix=raw[:amend['initial_raw_prefix_bytes']];sp=run/amend['initial_metadata_snapshot']
        audit.check(f'amendment_{i}_original_prefix_unchanged',sha_bytes(prefix)==amend['initial_raw_prefix_sha256'])
        audit.check(f'amendment_{i}_snapshot_hash',sp.exists() and sha(sp)==amend['initial_metadata_sha256'])
        audit.check(f'amendment_{i}_continuation_code_hash',sha(ROOT/'continue_quality.py')==amend['code_sha256'])
        audit.check(f'amendment_{i}_initial_code_matches',amend['initial_code_sha256']==meta['runner_sha256'])
        audit.check(f'amendment_{i}_frozen_plan_hash',sha(run/'plan.json')==amend['frozen_plan_sha256'])
        old=[json.loads(x) for x in prefix.decode().splitlines() if x.strip()]
        oldids={(r['case_id'],r['model_label']) for r in old}
        audit.check(f'amendment_{i}_attempt_count_and_no_replays',len(old)==amend['initial_attempts'] and len(oldids)==len(old) and amend['unattempted_calls']==len(expected)-len(old) and all((r['case_id'],r['model_label']) not in oldids for r in rs[len(old):]))
    bill=billing(audit,rs,meta)
    return audit.finish(attempts=len(rs),valid=sum(r.get('valid') is True for r in rs),invalid=len(failures),failed_attempts=failures,billing=bill,
                        raw_sha256=sha(run/'responses.jsonl'),plan_sha256=sha(run/'plan.json'),case_sha256=sha(cp),
                        valid_model_provider_counts=dict(Counter(str((r.get('response_model'),r.get('provider'))) for r in rs if r.get('valid'))),observed_usage_statistics=usage_statistics(rs),
                        scientific_note='Failed attempts remain in primary success/accuracy denominators; unknown bills remain unknown. Concurrent quality latency is diagnostic only.')


def verify_timing(run_dir,cases_path=None,quality_dir=None):
    run=Path(run_dir);cp=Path(cases_path or ROOT/'data/timing-cases.jsonl');cl=read_rows(cp);cases={c['id']:c for c in cl};warm={c['id']:c for c in adapter.warmup_cases()}
    meta=load(run/'metadata.json');rs=read_rows(run/'responses.jsonl');plan=load(run/'plan.json');audit=Audit();rng=random.Random(meta['seed']);regen=[]
    for repeat in range(meta['repeats']):
        for phase,cs in [('timing_warmup',adapter.warmup_cases()),('timing',rng.sample(cl,len(cl)))]:
            for c in cs:
                arms=list(adapter.MODELS);rng.shuffle(arms)
                for model in arms:regen.append({'case_id':c['id'],'model':model,'repeat':repeat,'phase':phase})
    audit.check('run_completed',meta.get('status')=='complete');audit.check('frozen_cases_hash',sha(cp)==meta.get('cases_sha256'))
    audit.check('runner_and_adapter_hashes',sha(ROOT/'run_study.py')==meta.get('runner_sha256') and sha(PILOT/'runner.py')==meta.get('adapter_sha256'))
    audit.check('serial_random_plan_reproduces',plan==regen)
    audit.check('expected_calls',len(rs)==len(plan)==meta.get('planned_calls'))
    bad=[]
    for i,row in enumerate(rs):
        if i>=len(plan):bad.append({'row':i,'issues':['extra_row']});continue
        p=plan[i];c=(warm if p['phase']=='timing_warmup' else cases)[p['case_id']];ep,payload=adapter.payload_for(c,p['model'])
        issues=check_record(row,c,ep,payload,p['model'],'off')
        if any(row.get(k)!=p[k] for k in ('case_id','phase','repeat')) or row.get('model_label')!=p['model'] or row.get('sequence')!=i:issues.append('plan_sequence_mismatch')
        if row.get('concurrency_pairs')!=1 or row.get('measurement_role')!='serial_timing':issues.append('not_serial')
        if issues:bad.append({'row':i,'issues':issues})
    audit.check('every_request_matches_plan_and_source',not bad,bad[:20])
    if quality_dir:
        qm=load(Path(quality_dir)/'metadata.json')
        audit.check('timing_started_after_bulk_finished',datetime.fromisoformat(meta['created_at'])>=datetime.fromisoformat(qm['finished_at']) and qm.get('status')=='complete')
    return audit.finish(attempts=len(rs),phase_counts=dict(Counter(r['phase'] for r in rs)),billing=billing(audit,rs,meta),raw_sha256=sha(run/'responses.jsonl'))


def verify_cache(run_dir):
    run=Path(run_dir);meta=load(run/'metadata.json');rs=read_rows(run/'responses.jsonl');frozen=read_rows(run/'cases.jsonl');audit=Audit()
    spec=importlib.util.spec_from_file_location('expanded_cache_for_verification',ROOT/'cache_study.py');cache=importlib.util.module_from_spec(spec);spec.loader.exec_module(cache)
    audit.check('run_completed',meta.get('status')=='complete')
    audit.check('cases_and_source_hashes',sha(run/'cases.jsonl')==meta.get('cases_sha256') and sha(ROOT/'cache_study.py')==meta.get('script_sha256'))
    audit.check('imported_adapters_unchanged',sha(PILOT/'runner.py')==meta.get('pilot_runner_sha256') and sha(PILOT/'cache_experiment.py')==meta.get('pilot_cache_adapter_sha256'))
    audit.check('case_generator_reproduces_frozen_cases',frozen==cache.dataset(meta['seed']))
    expected=[]
    for block in range(meta['blocks']):
        identifier=meta['block_identifiers'][block];rng=random.Random(meta['seed']+10000+block);cs=[c for c in frozen if c['block']==block];rng.shuffle(cs)
        expected.append((block,'cache_prime',cache.prime_case(block,identifier),'Luna cached',identifier))
        for c in cs:
            arms=list(cache.ARMS);rng.shuffle(arms)
            expected.extend((block,'measured',cache.with_prefix(c,identifier),arm,identifier) for arm in arms)
    audit.check('expected_calls',len(rs)==len(expected)==meta['calls_planned'])
    bad=[]
    for i,row in enumerate(rs):
        if i>=len(expected):bad.append({'row':i,'issues':['extra_row']});continue
        block,phase,c,arm,identifier=expected[i];ep,payload=cache.cache_payload(c,arm,'expanded-cache-'+identifier)
        issues=check_record(row,c,ep,payload,arm,'off' if arm=='Luna uncached' else None)
        if row.get('sequence')!=i or row.get('block')!=block or row.get('phase')!=phase or row.get('case_id')!=c['id'] or row.get('model_label')!=arm:issues.append('plan_sequence_mismatch')
        if row.get('prefix_identifier')!=identifier or row.get('prefix_sha256')!=sha_bytes(c['state']['rulebook'].encode()):issues.append('prefix_identity')
        if issues:bad.append({'row':i,'issues':issues})
    audit.check('every_request_matches_frozen_plan_and_source',not bad,bad[:20])
    for block in range(meta['blocks']):
        primes=[r for r in rs if r['block']==block and r['phase']=='cache_prime'];cached=[r for r in rs if r['block']==block and r['phase']=='measured' and r['model_label']=='Luna cached']
        counts=primes[0].get('usage',{}).get('prompt_tokens_details',{}) if len(primes)==1 else {}
        writes=counts.get('cache_write_tokens')
        audit.check(f'block_{block}_cold_prime_and_24_matching_reads',len(primes)==1 and primes[0]['valid'] and counts.get('cached_tokens')==0 and finite(writes) and writes>0 and len(cached)==24 and all(r['valid'] and r.get('usage',{}).get('prompt_tokens_details',{}).get('cached_tokens')==writes for r in cached))
    return audit.finish(attempts=len(rs),billing=billing(audit,rs,meta),raw_sha256=sha(run/'responses.jsonl'))


def verify(quality_dir,timing_dir=None,cache_dir=None,output_path=None):
    result={'generated_at_utc':datetime.now(timezone.utc).isoformat(),'quality':verify_quality(quality_dir)}
    if timing_dir:result['timing']=verify_timing(timing_dir,quality_dir=quality_dir)
    if cache_dir:result['cache']=verify_cache(cache_dir)
    result['passed']=all(value['passed'] for value in result.values() if isinstance(value,dict) and 'passed' in value)
    out=Path(output_path or ROOT/'verification.json');out.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('quality_dir',type=Path);p.add_argument('--timing-dir',type=Path);p.add_argument('--cache-dir',type=Path);p.add_argument('--output',type=Path,default=ROOT/'verification.json');args=p.parse_args()
    result=verify(args.quality_dir,args.timing_dir,args.cache_dir,args.output)
    print(json.dumps({'passed':result['passed'],'verification':str(args.output.resolve())}))
    raise SystemExit(0 if result['passed'] else 1)


if __name__=='__main__':main()
