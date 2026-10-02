#!/usr/bin/env python3
"""Larger paired quality run and separate serial timing repeats. No retries."""
from pathlib import Path
import sys,json,random,time,hashlib,threading,concurrent.futures,collections,math
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent))
import runner

def now():return datetime.now(timezone.utc).isoformat()
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,obj):
 tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_text(json.dumps(obj,indent=2));tmp.replace(p)
def reserve_for(case,model):
 payload=runner.payload_for(case,model)[1]
 return (len(json.dumps(payload,ensure_ascii=False).encode())+4096)*0.0000002+128*0.00000075
class Budget:
 def __init__(self,limit):
  if isinstance(limit,bool) or not isinstance(limit,(int,float)) or not math.isfinite(limit) or limit<=0:raise ValueError('Budget must be finite and positive')
  self.limit=limit;self.spent=0;self.pending=0;self.lock=threading.Lock();self.unknown=0;self.stop_reason=None
 def reserve(self,value):
  with self.lock:
   if not math.isfinite(value) or value<=0:raise ValueError('Reservation must be finite and positive')
   if self.stop_reason:raise RuntimeError(self.stop_reason)
   if self.spent+self.pending+value>self.limit:
    self.stop_reason='Budget guard stopped before further calls';raise RuntimeError(self.stop_reason)
   self.pending+=value
 def settle(self,value,row):
  with self.lock:
   self.pending=max(0,self.pending-value)
   charge=row.get('cost_usd')
   if isinstance(charge,(int,float)) and not isinstance(charge,bool) and math.isfinite(charge) and charge>=0:self.spent+=charge
   else:
    self.unknown+=1;self.stop_reason=self.stop_reason or 'Unknown billing stopped further calls'
   if self.spent>self.limit:self.stop_reason=self.stop_reason or 'Observed charge exceeded budget; stopped further calls'
 def stop(self,reason):
  with self.lock:self.stop_reason=self.stop_reason or reason
 def raise_if_stopped(self):
  with self.lock:
   if self.stop_reason:raise RuntimeError(self.stop_reason)
 def snapshot(self):
  with self.lock:return {'charged_usd':None if self.unknown else self.spent,'known_charge_subtotal_usd':self.spent,'unknown_billing':self.unknown,'budget_pending_usd':self.pending,'budget_stop_reason':self.stop_reason}

def run_once(client,key,case,model,phase,sequence):
 started=time.perf_counter()
 try:return client.run(case,model,phase,sequence)
 except Exception as e:
  # Preserve attempted requests even if the adapter unexpectedly raises before
  # returning its normal error record. Billing cannot be assumed to be zero.
  return {'case_id':case['id'],'experiment':case['experiment'],'target':case['target'],'target_kind':case['target_kind'],
          'model_label':model,'requested_model':runner.MODELS[model],'phase':phase,'sequence':sequence,'started_at':now(),
          'probability':None,'valid':False,'error':('Client exception: '+str(e)).replace(key,'[REDACTED]'),
          'cost_usd':None,'usage':{},'response_model':None,'provider':None,'latency_s':time.perf_counter()-started,'request_exception':True}

def run_quality(key,cases_path=None,out_dir=None,budget=.80,workers=16,seed=20261002,progress=None):
 guard=Budget(budget)
 if isinstance(workers,bool) or not isinstance(workers,int) or workers<1:raise ValueError('workers must be a positive integer')
 cases_path=Path(cases_path or ROOT/'data/cases.jsonl');cases=runner.load_cases(cases_path)
 out=Path(out_dir or ROOT/'runs'/('quality-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')));out.mkdir(parents=True,exist_ok=False)
 rng=random.Random(seed);rng.shuffle(cases)
 plan=[]
 for i,case in enumerate(cases):
  models=list(runner.MODELS);rng.shuffle(models)
  plan.append({'pair_index':i,'case_id':case['id'],'models':models})
 save(out/'plan.json',plan)
 meta={'status':'running','created_at':now(),'models':runner.MODELS,'cases':len(cases),'planned_calls':2*len(cases),'concurrency_pairs':workers,'phase':'quality','seed':seed,'budget_usd':budget,'cases_sha256':sha(cases_path),'runner_sha256':sha(__file__),'adapter_sha256':sha(runner.__file__),'protocol':'Fresh full quality evaluation. Random case and within-pair model order. Thread workers each perform one pair sequentially. No retries or repairs. Concurrent latency is diagnostic only, excluded from speed claims. Shared answer contract and Luna settings unchanged from pilot.'}
 save(out/'metadata.json',meta);lock=threading.Lock();counts=collections.Counter();records=[];threadlocal=threading.local();clients=[]
 def emit(row):
  with lock:
   records.append(row);counts['completed']+=1;counts['valid']+=int(row['valid']);counts['invalid']+=int(not row['valid'])
   with (out/'responses.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
   if counts['invalid']>=5:guard.stop('Five invalid requests: circuit breaker stopped further calls; logged attempts retained')
   meta.update(calls_completed=counts['completed'],valid=counts['valid'],invalid=counts['invalid'],**guard.snapshot())
   save(out/'metadata.json',meta)
   if progress and (counts['completed']%100==0 or not row['valid']):progress({'kind':'quality','completed':counts['completed'],'planned':2*len(cases),'valid':counts['valid'],'invalid':counts['invalid'],'charged_usd':guard.spent,'last_error':row.get('error')})
 def pair(item):
  pi,case=item;ps=plan[pi]
  if not hasattr(threadlocal,'client'):
   threadlocal.client=runner.Client(key);clients.append(threadlocal.client)
  for j,model in enumerate(ps['models']):
   amount=reserve_for(case,model);guard.reserve(amount)
   row=run_once(threadlocal.client,key,case,model,'quality',2*pi+j)
   row.update(pair_index=pi,concurrency_pairs=workers,measurement_role='bulk_quality',repeat=0)
   guard.settle(amount,row);emit(row)
   guard.raise_if_stopped()
  return True
 try:
  with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
   its=iter(enumerate(cases));futures={pool.submit(pair,next(its)) for _ in range(min(workers,len(cases)))}
   while futures:
    done,futures=concurrent.futures.wait(futures,return_when=concurrent.futures.FIRST_COMPLETED)
    for f in done:f.result()
    for _ in done:
     try:item=next(its)
     except StopIteration:break
     futures.add(pool.submit(pair,item))
  guard.raise_if_stopped();meta['status']='complete'
 except Exception as e:
  meta['status']='stopped';meta['error']=str(e).replace(key,'[REDACTED]')
 finally:
  for client in clients:client.close()
  meta.update(finished_at=now(),calls_completed=len(records),**guard.snapshot());save(out/'metadata.json',meta)
 return {'run_dir':str(out),'metadata':meta}

def freeze_timing(cases_path=None,n_per_task=12,seed=20261004):
 cases=runner.load_cases(cases_path or ROOT/'data/cases.jsonl');rng=random.Random(seed);groups=collections.defaultdict(list)
 for c in cases:groups[c['experiment']].append(c)
 selected=[]
 for task,cs in sorted(groups.items()):selected.extend(rng.sample(cs,min(n_per_task,len(cs))))
 p=ROOT/'data/timing-cases.jsonl';p.write_text(''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in selected));return p

def run_timing(key,cases_path=None,out_dir=None,budget=.10,repeats=3,seed=20261005,progress=None):
 guard=Budget(budget)
 if isinstance(repeats,bool) or not isinstance(repeats,int) or repeats<1:raise ValueError('repeats must be a positive integer')
 cases_path=Path(cases_path or ROOT/'data/timing-cases.jsonl');cases=runner.load_cases(cases_path)
 out=Path(out_dir or ROOT/'runs'/('timing-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')));out.mkdir(parents=True,exist_ok=False)
 rng=random.Random(seed);plan=[]
 for repeat in range(repeats):
  # Warm up the persistent client with two cases per model for each serial block.
  for phase,cs in [('timing_warmup',runner.warmup_cases()),('timing',rng.sample(cases,len(cases)))]:
   for case in cs:
    models=list(runner.MODELS);rng.shuffle(models)
    for model in models:plan.append({'case':case,'model':model,'repeat':repeat,'phase':phase})
 save(out/'plan.json',[{'case_id':i['case']['id'],**{k:v for k,v in i.items() if k!='case'}} for i in plan])
 meta={'status':'running','created_at':now(),'models':runner.MODELS,'cases':len(cases),'repeats':repeats,'planned_calls':len(plan),'seed':seed,'budget_usd':budget,'cases_sha256':sha(cases_path),'runner_sha256':sha(__file__),'adapter_sha256':sha(runner.__file__),'protocol':f'Isolated sequential timing run after all bulk calls complete. {repeats} randomized blocks, same {len(cases)} cases. Two warmup cases/model/block excluded. Shared persistent HTTPS connection; nonstreaming complete-response timing; no retries.'};save(out/'metadata.json',meta)
 client=runner.Client(key);records=[]
 try:
  for i,item in enumerate(plan):
   case=item['case'];model=item['model'];amount=reserve_for(case,model);guard.reserve(amount)
   row=run_once(client,key,case,model,item['phase'],i);row.update(repeat=item['repeat'],measurement_role='serial_timing',concurrency_pairs=1);guard.settle(amount,row);records.append(row)
   with (out/'responses.jsonl').open('a')as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
   meta.update(calls_completed=len(records),**guard.snapshot());save(out/'metadata.json',meta)
   if progress and (i%24==0 or not row['valid']):progress({'kind':'timing','completed':i+1,'planned':len(plan),'charged_usd':guard.spent,'last_error':row.get('error')})
   guard.raise_if_stopped()
   if not row['valid']:raise RuntimeError('Invalid timing call: stopped; records retained')
  meta['status']='complete'
 except Exception as e:meta.update(status='stopped',error=str(e).replace(key,'[REDACTED]'))
 finally:client.close();meta.update(finished_at=now(),calls_completed=len(records),**guard.snapshot());save(out/'metadata.json',meta)
 return {'run_dir':str(out),'metadata':meta}
