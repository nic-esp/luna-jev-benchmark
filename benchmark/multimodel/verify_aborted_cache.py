#!/usr/bin/env python3
"""Recompute supplementary aborted-cache closure evidence without inference."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import verify as generic
import adapter
import cache_protocol as original_protocol


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def rows(path):return [json.loads(s) for s in Path(path).read_text().splitlines() if s.strip()]
def finite(x):return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)
def close(a,b):return finite(a) and finite(b) and math.isclose(a,b,rel_tol=1e-10,abs_tol=1e-12)


def verify_aborted_cache(run_dir,*,expected_attempts=55,expected_saved_responses=54,output_path=None):
    run=Path(run_dir);checks={};issues=[];result={'passed':False,'primary_analysis_eligible':False,'role':'supplementary_aborted_cache',
        'run_dir':str(run.resolve()),'generated_at_utc':datetime.now(timezone.utc).isoformat(),'verifier_sha256':sha(__file__)}
    def check(name,value):
        checks[name]=bool(value)
        if not value:issues.append(name)
    try:
        meta=json.loads((run/'metadata.json').read_text());closure=json.loads((run/'aborted-session-audit.json').read_text())
        recorded=rows(run/'responses.jsonl');dispatch=rows(run/'dispatches.jsonl');saved=json.loads((run/'plan.json').read_text())
        check('session_aborted_and_ineligible_for_primary',meta.get('status')=='aborted' and meta.get('primary_analysis_eligible') is False)
        check('reported_closure_passed',closure.get('status')=='passed' and bool(closure.get('checks')) and all(closure['checks'].values()))
        check('closure_script_hash',sha(run/'close_aborted_cache.py')==closure['closure_script_sha256'])
        check('original_protocol_hash',sha(ROOT/'cache_protocol.py')==meta['protocol_sha256'])
        original=[]
        check('exact_original_evidence_file_set',set(closure['original_evidence'])=={'responses.jsonl','dispatches.jsonl','plan.json'})
        for name in ('responses.jsonl','dispatches.jsonl','plan.json'):
            old=closure['original_evidence'][name];data=(run/name).read_bytes();prefix=data[:old['bytes']]
            check(name+'_original_prefix_preserved',len(prefix)==old['bytes'] and hashlib.sha256(prefix).hexdigest()==old['sha256'])
            if name=='responses.jsonl':original=[json.loads(s) for s in prefix.decode().splitlines() if s.strip()]
            else:check(name+'_entire_original_file_preserved',len(data)==old['bytes'])
        check('original_saved_response_count',len(original)==closure['original_saved_responses']==expected_saved_responses)
        check('final_response_dispatch_counts',len(recorded)==len(dispatch)==meta['calls_completed']==meta['calls_dispatched']==closure['final_attempts']==expected_attempts)
        check('one_ordered_outcome_per_dispatch',[r['sequence'] for r in recorded]==[d['sequence'] for d in dispatch]==list(range(expected_attempts)))
        cases,primes,_,hashes=original_protocol.load_source(meta['source_dir'])
        planner=adapter.Adapter.__new__(adapter.Adapter)
        rebuilt,prepared=original_protocol.create_plan(planner,meta['models'],cases,primes,hashes,meta['seed'],saved['block_identifiers'])
        check('original_plan_reproduces',saved==rebuilt and sha(run/'plan.json')==meta['plan_sha256'])
        check('frozen_source_hashes',meta['source_hashes']==hashes and sha(run/'cases.jsonl')==hashes['cases_sha256'] and sha(run/'rulebook.txt')==hashes['rulebook_sha256'])
        for row,d in zip(recorded,dispatch):
            seq=row['sequence'];entry=rebuilt['attempts'][seq];case,spec,request=prepared[seq]
            problems,_=generic.check_record(row,{**case,'experiment':'cache_policy'},meta,lambda c,l,r,m:(request['endpoint'],request['payload']))
            if any(row.get(k)!=entry[k] for k in ('case_id','base_model_label','model_label','phase','sequence','block','pair_id','target','cache_mode','cache_key','prefix_identifier','prefix_sha256','request_sha256','reserve_usd')):problems.append('saved_plan_identity')
            if any(d.get(k)!=entry[k] for k in ('sequence','request_sha256','case_id','model_label')):problems.append('dispatch_identity')
            if problems:issues.append({'sequence':seq,'issues':problems})
        check('all_retained_rows_match_source_requests_and_response_contract',not any(isinstance(x,dict) for x in issues))
        lost=[r for r in recorded if r.get('lost_outcome') is True]
        expected_lost=list(range(expected_saved_responses,expected_attempts))
        check('only_appended_lost_dispatches_marked_unknown',[r['sequence'] for r in lost]==closure['lost_sequences_retained_as_unknown']==expected_lost)
        check('lost_dispatches_have_no_fabricated_outcome',all(r.get('valid') is False and r.get('probability') is None and r.get('cost_usd') is None and r.get('latency_s') is None and not r.get('response') and not r.get('usage') and r.get('http_status') is None and bool(r.get('error')) for r in lost))
        check('lost_dispatch_metadata',meta.get('lost_outcome_n')==len(lost) and meta.get('unresolved_dispatch_n')==0)
        cost=generic.base.costs(recorded);known=cost['known_billed_usd'];missing=cost['missing_bills'];guard=missing*.01
        check('closure_valid_count',sum(r.get('valid') is True for r in recorded)==closure['valid'])
        check('closure_billing_complete_or_unknown',closure['cost']['total_usd']==cost['total_billed_usd'] and close(closure['cost']['known_subtotal_usd'],known) and closure['cost']['missing_n']==missing and close(closure['cost']['unknown_bill_allowance_usd'],guard) and close(closure['cost']['budget_accounted_usd'],known+guard))
        check('metadata_billing_complete_or_unknown',meta['charged_usd']==cost['total_billed_usd'] and close(meta['known_charge_subtotal_usd'],known) and meta['cost_missing_n']==missing and close(meta['unknown_bill_allowance_total_usd'],guard) and close(meta['budget_accounted_usd'],known+guard))
        result.update(attempts=len(recorded),original_saved_responses=len(original),valid=sum(r.get('valid') is True for r in recorded),lost_outcomes=len(lost),cost=cost,
            hashes={name:sha(run/name) for name in ('metadata.json','responses.jsonl','dispatches.jsonl','plan.json','aborted-session-audit.json','close_aborted_cache.py')})
    except (OSError,KeyError,ValueError,TypeError,IndexError) as exc:
        check('closure_verification_completed',False);issues.append({'error_type':type(exc).__name__,'error':str(exc)})
    result.update(checks=checks,issues=issues,passed=bool(checks) and all(checks.values()))
    result['status']='passed' if result['passed'] else 'failed'
    result['limitations']=['This is evidence and billing closure for an aborted administrative-key session. It does not establish a successful cache treatment and is excluded from all primary quality, timing and cache estimates. A lost dispatched outcome and its bill remain unknown.']
    target=Path(output_path) if output_path else run/'closure-verification.json'
    target.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('run_dir',type=Path);p.add_argument('--output',type=Path);a=p.parse_args()
    r=verify_aborted_cache(a.run_dir,output_path=a.output);print(json.dumps({k:r.get(k) for k in ('passed','status','attempts','valid','lost_outcomes','cost')}));raise SystemExit(0 if r['passed'] else 1)
