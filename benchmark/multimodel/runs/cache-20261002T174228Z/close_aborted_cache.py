from pathlib import Path
import json,hashlib,importlib.util,sys
from datetime import datetime,timezone
root=Path('outputs/benchmark/multimodel').resolve();sys.path.insert(0,str(root));run=root/'runs/cache-20261002T174228Z'
def load(name):
 sp=importlib.util.spec_from_file_location(name,root/(name+'.py'));m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m);return m
cp=load('cache_protocol');am=load('adapter');planner=am.Adapter.__new__(am.Adapter)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def rows(p):return [json.loads(s) for s in p.read_text().splitlines() if s.strip()]
history=run/'aborted-history';history.mkdir(exist_ok=True)
meta=json.loads((run/'metadata.json').read_text())
if (history/'metadata-before.json').exists():assert (history/'metadata-before.json').read_bytes()==(run/'metadata.json').read_bytes()
else:(history/'metadata-before.json').write_bytes((run/'metadata.json').read_bytes())
original={name:{'sha256':sha(run/name),'bytes':(run/name).stat().st_size} for name in ['responses.jsonl','dispatches.jsonl','plan.json']}
plan=json.loads((run/'plan.json').read_text());cases,primes,rulebook,source_hashes=cp.load_source(meta['source_dir'])
regenerated,prepared=cp.create_plan(planner,meta['models'],cases,primes,source_hashes,meta['seed'],plan['block_identifiers'])
assert regenerated==plan
rr=rows(run/'responses.jsonl');jj=rows(run/'dispatches.jsonl');seen={r['sequence'] for r in rr};lost=[]
for d in jj:
 if d['sequence'] in seen:continue
 e=plan['attempts'][d['sequence']];c,spec,request=prepared[e['sequence']]
 assert e['request_sha256']==d['request_sha256']
 r={**e,'experiment':'cache_policy','target_kind':'label','requested_model':spec['id'],'endpoint':request['endpoint'],'request':request['payload'],
 'started_at':d['dispatched_at'],'valid':False,'probability':None,'cost_usd':None,'usage':{},'response':{},'provider':None,'response_model':None,'generation_id':None,'http_status':None,'latency_s':None,
 'cached_tokens':None,'cache_write_tokens':None,'lost_outcome':True,'error':'Controller stopped after detecting a cache-key construction error; this already dispatched request has no saved outcome. Retained as unknown and not replayed.'}
 with (run/'responses.jsonl').open('a') as f:f.write(json.dumps(r,ensure_ascii=False)+'\n')
 rr.append(r);lost.append(e['sequence'])
cost=cp.cost_summary(rr);now=datetime.now(timezone.utc).isoformat()
meta.update(status='aborted',finished_at=now,calls_completed=len(rr),calls_dispatched=len(jj),unresolved_dispatch_n=0,lost_outcome_n=len(lost),
 charged_usd=cost['total_usd'],known_charge_subtotal_usd=cost['known_subtotal_usd'],cost_missing_n=cost['missing_n'],unknown_bill_allowance_total_usd=cost['unknown_bill_allowance_usd'],budget_accounted_usd=cost['budget_accounted_usd'],
 error='Cache construction generated 66-character Luna keys; upstream maximum is 64. Entire session excluded from primary cache inference and retained as supplementary setup evidence.',primary_analysis_eligible=False)
cp.atomic_json(run/'metadata.json',meta);cp.atomic_json(run/'summary.json',cp.summarise(rr,meta))
checks={}
for name,v in original.items():
 data=(run/name).read_bytes();checks[name+'_original_prefix_unchanged']=hashlib.sha256(data[:v['bytes']]).hexdigest()==v['sha256']
 if name!='responses.jsonl':checks[name+'_entire_file_unchanged']=sha(run/name)==v['sha256']
checks['one_outcome_per_dispatch']=len(rr)==len(jj) and len({r['sequence'] for r in rr})==len(rr)
checks['exact_plan_reconstruction']=regenerated==plan
assert all(checks.values())
audit={'status':'passed','closed_at':now,'checks':checks,'original_evidence':original,'original_saved_responses':len(rr)-len(lost),'lost_sequences_retained_as_unknown':lost,'final_attempts':len(rr),'valid':sum(r['valid'] for r in rr),'cost':cost,'closure_script_sha256':sha(Path(__file__))}
cp.atomic_json(run/'aborted-session-audit.json',audit)
(run/'close_aborted_cache.py').write_bytes(Path(__file__).read_bytes())
print(json.dumps({k:audit[k] for k in ['status','final_attempts','valid','lost_sequences_retained_as_unknown','cost']}))
