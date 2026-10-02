#!/usr/bin/env python3
"""Offline source, plan, output-contract and billing verification for any model.

Optional payload_factory(case, model_label, row, metadata) -> (endpoint,payload)
and plan_factory(cases, metadata) -> plan enable exact adapter/seed checks without
assuming a particular runner implementation. Their imports must make no calls.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('multi_analysis_for_audit',ROOT/'analyze.py')
analysis=importlib.util.module_from_spec(spec);spec.loader.exec_module(analysis)
base=analysis.base
SCHEMA={'type':'object','properties':{'type':{'type':'string','enum':['noul']},'noul':{'type':'number','minimum':0,'maximum':1}},'required':['type','noul'],'additionalProperties':False}


def shared(case):
    return {'state':case['state'],'questions':{'answer':{'type':'noul','instructions':case['question'],'criteria':case['criteria']}}}


def model_specs(metadata):
    models=metadata.get('models',{})
    if isinstance(models,list):
        return {m['label']:m for m in models if isinstance(m,dict)}
    return {k:({'id':v} if isinstance(v,str) else v) for k,v in models.items()}


def user_text(payload):
    contents=[m['content'] for m in payload.get('messages',[]) if m.get('role')=='user']
    if len(contents)!=1:
        raise ValueError('Expected exactly one user message')
    c=contents[0]
    if isinstance(c,str):return c
    return ''.join(part['text'] for part in c if part.get('type')=='text')


def no_duplicate_object(pairs):
    out={}
    for key,value in pairs:
        if key in out:raise ValueError('Duplicate JSON answer field')
        out[key]=value
    return out


def check_record(row,case,metadata,payload_factory=None):
    issues=[];observed={}
    def check(name,ok):
        if not ok:issues.append(name)
    label=row.get('model_label');arm=row.get('base_model_label',analysis.arm_base(label));payload=row.get('request',{})
    cfg=model_specs(metadata).get(arm,{})
    check('target_and_task',all(row.get(k)==case.get(k) for k in ('target','target_kind','experiment')))
    check('finite_latency_or_explicit_lost_outcome',(base.finite(row.get('latency_s')) and row['latency_s']>0) or (row.get('lost_outcome') is True and row.get('valid') is False and row.get('latency_s') is None))
    check('requested_model_readback',row.get('requested_model')==payload.get('model'))
    if cfg.get('id'):check('frozen_model_id',row.get('requested_model')==cfg['id'])
    if payload_factory:
        ep,expected=payload_factory(case,label,row,metadata)
        check('exact_frozen_payload',payload==expected and row.get('endpoint')==ep)
    elif row.get('phase') in ('quality','timing','timing_warmup'):
        try:
            supplied={k:payload[k] for k in ('state','questions')} if arm=='Jev' else json.loads(user_text(payload))
            check('source_input_exact',supplied==shared(case))
        except (KeyError,ValueError,TypeError):issues.append('source_input_unparseable')
    else:
        observed['full_cache_payload']='not independently regenerated; supply payload_factory'
    if arm!='Jev':
        check('strict_noul_schema',payload.get('response_format')=={'type':'json_schema','json_schema':{'name':'noul_answer','strict':True,'schema':SCHEMA}})
        check('max_tokens_128',payload.get('max_tokens')==128)
        declared_reasoning = cfg.get('reasoning', {'effort':'none'})
        check('declared_reasoning_requested',payload.get('reasoning')==declared_reasoning)
        observed['requested_reasoning']=payload.get('reasoning')
        check('nonstreaming_no_tools',payload.get('stream') is False and not payload.get('tools'))
        provider=payload.get('provider',{})
        check('provider_pinned_no_fallback',len(provider.get('only',[]))==1 and provider.get('allow_fallbacks') is False and provider.get('require_parameters') is True)
        if cfg.get('provider'):check('provider_setting',provider.get('only')==[cfg['provider']])
    response=row.get('response') or {};usage=response.get('usage') or {};cost=usage.get('cost')
    if row.get('raw_adapter_record_json') and row.get('valid') is False:
        try:
            original=json.loads(row['raw_adapter_record_json'])
            original_usage=(original.get('response') or {}).get('usage') or original.get('usage') or {}
            cost=original_usage.get('cost',original.get('cost_usd'))
        except (ValueError,TypeError,AttributeError):issues.append('raw_adapter_record_unparseable')
    expected_cost=cost if base.finite(cost) and cost>=0 else None
    check('billed_cost_readback',row.get('cost_usd')==expected_cost)
    if row.get('valid') is True:
        check('success_status',row.get('http_status')==200 and row.get('error') is None)
        try:
            choice=None if arm=='Jev' else response['choices'][0]
            answer=response['answers']['answer'] if arm=='Jev' else json.loads(choice['message']['content'],object_pairs_hook=no_duplicate_object)
            check('strict_answer',isinstance(answer,dict) and set(answer)=={'type','noul'} and answer['type']=='noul' and base.finite(answer['noul']) and 0<=answer['noul']<=1)
            check('probability_readback',row.get('answer')==answer and row.get('probability')==answer['noul'])
            check('usage_readback',row.get('usage')==usage)
            check('model_provider_readback',row.get('response_model')==response.get('model') and row.get('provider')==response.get('provider'))
            request_id=row.get('requested_model','');served=row.get('response_model','')
            allowed=cfg.get('response_model_ids',[])
            check('served_model',served in allowed or served==request_id or served.startswith(request_id+'-20'))
            if cfg.get('response_provider_names'):check('served_provider',row.get('provider') in cfg['response_provider_names'])
            elif cfg.get('provider_name'):check('served_provider',row.get('provider')==cfg['provider_name'])
            if choice:
                check('finished_normally',choice.get('finish_reason')=='stop')
                msg=choice['message']
                observed['generated_reasoning_present']=bool(msg.get('reasoning') or msg.get('reasoning_details'))
                if payload.get('reasoning',{}).get('effort')=='none':
                    check('no_generated_reasoning',not observed['generated_reasoning_present'])
                rt=(usage.get('completion_tokens_details') or {}).get('reasoning_tokens')
                observed['reasoning_tokens']=rt
                if payload.get('reasoning',{}).get('effort')=='none':
                    check('no_observed_reasoning_tokens',rt is None or (base.finite(rt) and rt==0))
                details=usage.get('prompt_tokens_details') or {}
                observed['cached_tokens']=details.get('cached_tokens');observed['cache_write_tokens']=details.get('cache_write_tokens')
                if payload.get('prompt_cache_options',{}).get('mode')=='explicit' and row.get('cache_mode')!='cached':
                    markers=any('cache_control' in part or 'prompt_cache_breakpoint' in part for m in payload.get('messages',[]) if isinstance(m.get('content'),list) for part in m['content'])
                    if not markers:
                        check('no_observed_off_cache_reads_or_writes',all(details.get(k) in (None,0) for k in ('cached_tokens','cache_write_tokens')))
            observed['output_tokens']=usage.get('completion_tokens',usage.get('output_tokens'))
            if arm!='Jev':
                output=observed['output_tokens']
                check('reported_output_within_128_cap',output is None or (isinstance(output,int) and not isinstance(output,bool) and 0<=output<=128))
        except (KeyError,TypeError,ValueError,IndexError):issues.append('valid_response_unparseable')
    else:
        check('failure_explicit',row.get('valid') is False and bool(row.get('error')) and row.get('probability') is None)
    return issues,observed


def flatten_plan(plan):
    """Support grouped quality plans and per-request serial plans."""
    out=[]
    if isinstance(plan,dict):
        plan=plan['attempts']
    for item in plan:
        if 'models' in item:
            for label in item['models']:
                out.append(dict(item,model=label,phase=item.get('phase','quality'),repeat=item.get('repeat',0)))
        else:out.append(item)
    return out


def verify_run(run_dir,cases_path,*,extra_cases=None,expected_models=None,payload_factory=None,plan_factory=None):
    run=Path(run_dir);cp=Path(cases_path);cases_list=analysis.READ(cp);cases={c['id']:c for c in cases_list}
    cases.update({c['id']:c for c in (extra_cases or [])})
    meta=json.loads((run/'metadata.json').read_text());rows=analysis.READ(run/'responses.jsonl')
    checks={};issues=[]
    checks['completed']=meta.get('status')=='complete'
    checks['unique_frozen_case_ids']=len({c['id'] for c in cases_list})==len(cases_list)
    checks['frozen_cases_hash']=(meta.get('cases_sha256') or meta.get('source_hashes',{}).get('cases_sha256'))==base.digest(cp)
    expected_specs=model_specs(meta)
    expected_models=list(expected_models or expected_specs or {r['model_label'] for r in rows})
    checks['expected_models_present']=set(expected_models)<=set(r.get('base_model_label',analysis.arm_base(r['model_label'])) for r in rows)
    checks['frozen_source_code_hashes']=True
    for key in ('code_hashes',):
        for path,digest in meta.get(key,{}).items():
            f=Path(path)
            if not f.is_absolute():f=run/f
            if not f.exists() or base.digest(f)!=digest:
                checks['frozen_source_code_hashes']=False;issues.append({'code_hash':path})
    if meta.get('protocol_sha256'):
        checks['cache_protocol_source_hash']=base.digest(ROOT/'cache_protocol.py')==meta['protocol_sha256']
    plan_path=run/'plan.json';plan=None
    if plan_path.exists():
        rawplan=json.loads(plan_path.read_text());plan=flatten_plan(rawplan)
        checks['plan_count']=len(plan)==len(rows)
        if meta.get('plan_sha256'):checks['plan_sha256']=base.digest(plan_path)==meta['plan_sha256']
        if plan_factory:checks['seeded_plan_reproduces']=rawplan==plan_factory(cases_list,meta)
    else:checks['saved_plan_available']=False
    identities=[];observations=defaultdict(list)
    for row in rows:
        label=row['model_label'];identity=(row.get('phase'),row.get('repeat',0),row.get('block'),row['case_id'],label);identities.append(identity)
        c=cases.get(row['case_id'])
        if not c:
            issues.append({'identity':identity,'issues':['unknown_case']});continue
        errs,observed=check_record(row,c,meta,payload_factory)
        if plan is not None:
            seq=row.get('sequence')
            if not isinstance(seq,int) or not 0<=seq<len(plan):errs.append('sequence_outside_plan')
            else:
                p=plan[seq];plabel=p.get('model_label',p.get('model',p.get('arm')))
                if p.get('request_sha256'):
                    rawrequest={'endpoint':row.get('endpoint'),'payload':row.get('request')}
                    request_hash=hashlib.sha256(json.dumps(rawrequest,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
                    if request_hash!=p['request_sha256']:errs.append('planned_request_hash_mismatch')
                if row['case_id']!=p.get('case_id') or label!=plabel or any(k in p and row.get(k,0 if k=='repeat' else None)!=p[k] for k in ('phase','repeat','block')):errs.append('planned_identity_mismatch')
        if errs:issues.append({'identity':identity,'issues':errs})
        if row.get('valid'):observations[label].append(observed)
    checks['unique_attempt_identities']=len(identities)==len(set(identities))
    checks['every_record_matches_contract_and_source']=not issues
    quality=[r for r in rows if r.get('phase')=='quality']
    if quality:
        checks['complete_quality_cross_product']={(r['case_id'],r['model_label']) for r in quality}=={(c['id'],m) for c in cases_list for m in expected_models} and len(quality)==len(cases_list)*len(expected_models)
    timing=[r for r in rows if r.get('phase')=='timing']
    if timing:
        checks['complete_timing_cross_product']={(r['case_id'],r['model_label'],r.get('repeat')) for r in timing}=={(c['id'],m,i) for c in cases_list for m in expected_models for i in range(3)}
        checks['serial_timing_concurrency']=all(r.get('concurrency_pairs',r.get('concurrency',1))==1 for r in rows)
    for journal_name in ('attempts.jsonl','dispatches.jsonl'):
        journal_path=run/journal_name
        if journal_path.exists():
            journal=analysis.READ(journal_path)
            checks[journal_name+'_unique_dispatches']=len({r['sequence'] for r in journal})==len(journal)==len(rows) and {r['sequence'] for r in journal}=={r['sequence'] for r in rows}
            if plan is not None:
                checks[journal_name+'_frozen_request_hashes']=all(0<=r['sequence']<len(plan) and r.get('request_sha256')==plan[r['sequence']].get('request_sha256') for r in journal)
    if meta.get('requests_sha256'):
        checks['saved_exact_requests_hash']=base.digest(run/'requests.jsonl')==meta['requests_sha256']
    for filename,key in [('responses.jsonl','responses_sha256'),('attempts.jsonl','attempts_sha256')]:
        if meta.get(key):checks['saved_'+key]=base.digest(run/filename)==meta[key]
    cost=base.costs(rows)
    if 'charged_usd' in meta:
        checks['metadata_billing_complete_or_unknown']=meta['charged_usd'] is None if cost['missing_bills'] else base.finite(meta['charged_usd']) and math.isclose(meta['charged_usd'],cost['known_billed_usd'],rel_tol=1e-10,abs_tol=1e-12)
    usage={}
    for model,obs in observations.items():
        out=[o['output_tokens'] for o in obs if base.finite(o.get('output_tokens'))]
        usage[model]={'valid_calls':len(obs),'output_tokens':{'observed':len(out),'min':min(out) if out else None,'max':max(out) if out else None,'total':sum(out) if out else None},
            'reasoning_counter_observed':sum(o.get('reasoning_tokens') is not None for o in obs),
            'reasoning_counter_unknown':sum(o.get('reasoning_tokens') is None for o in obs),
            'reasoning_tokens_reported_total':sum(o.get('reasoning_tokens') or 0 for o in obs) if all(o.get('reasoning_tokens') is not None for o in obs) else None,
            'visible_reasoning_calls':sum(o.get('generated_reasoning_present',False) for o in obs),
            'cache_read_counter_observed':sum(o.get('cached_tokens') is not None for o in obs),
            'cache_write_counter_observed':sum(o.get('cache_write_tokens') is not None for o in obs)}
    return {'passed':all(checks.values()),'checks':checks,'issues':issues[:50], 'issue_count':len(issues),'run_dir':str(run.resolve()),
            'raw_sha256':base.digest(run/'responses.jsonl'),'case_sha256':base.digest(cp),'attempts':len(rows),'valid':sum(r.get('valid') is True for r in rows),'cost':cost,
            'observed_usage':usage,'exact_payload_factory_supplied':payload_factory is not None,'seeded_plan_factory_supplied':plan_factory is not None,
            'limitations':['Missing reasoning/cache counters are unobserved, not verified zero. Without factories, quality/timing source content and contracts are checked, but complete payload and seeded plan regeneration are not established.']}


def _module(name):
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    sp=importlib.util.spec_from_file_location('audit_'+name,ROOT/(name+'.py'))
    module=importlib.util.module_from_spec(sp);sp.loader.exec_module(module);return module


def verify_protocol(run_dir):
    """Exact offline regeneration for the root-owned quality/timing/cache runners."""
    run=Path(run_dir);meta=json.loads((run/'metadata.json').read_text())
    if meta.get('status')!='complete':
        raise ValueError('Exact final protocol verification requires a completed run')
    if meta.get('experiment')=='multimodel_cache_policy':
        # Cache rows use a documented logging alias and nonce-bearing input.
        # The independent cache verifier rebuilds those exact prepared cases.
        return _module('verify_cache').verify_cache(run)
    adapter_module=_module('adapter')
    # Request construction and reservation use no instance state or credentials.
    planner=adapter_module.Adapter.__new__(adapter_module.Adapter)
    protocol=_module('run_study');quality,timing,hashes=protocol.load_frozen_sources()
    cases=quality if meta['phase']=='quality' else timing
    plan,prepared=protocol.create_plan(planner,cases,meta['models'],meta['phase'],meta['seed'])
    cp=protocol.DATA/('cases.jsonl' if meta['phase']=='quality' else 'timing-cases.jsonl')
    extra=protocol.base.warmup_cases() if meta['phase']=='timing' else []
    def payload_factory(case,label,row,metadata):
        request=prepared[row['sequence']][2]
        return request['endpoint'],request['payload']
    result=verify_run(run,cp,extra_cases=extra,payload_factory=payload_factory,plan_factory=lambda c,m:plan)
    result['checks']['named_source_hashes_match']=all(meta.get('source_hashes',{}).get(k)==v for k,v in hashes.items())
    if meta.get('code_hashes'):
        files=protocol.source_files()
        result['checks']['current_code_matches_saved_snapshot']=all(name in files and base.digest(files[name])==value for name,value in meta['code_hashes'].items())
    if meta.get('operational_amendments'):
        continuation_audit=_module('continuation_audit')
        result['continuation_audit']=continuation_audit.verify_continuations(run,lambda case,spec:planner.build_request(case,spec))
        result['checks']['prospective_continuation_evidence_preserved']=result['continuation_audit']['passed']
    result['passed']=all(result['checks'].values())
    return result


def verify_study(quality_dirs,timing_dir=None,cache_dirs=None,*,models=None,output_path=None):
    """Audit reused legacy quality plus every supplied new completed protocol."""
    result={'generated_at_utc':datetime.now(timezone.utc).isoformat(),'quality':[], 'timing':None, 'cache':[]}
    all_quality=[]
    for directory in quality_dirs:
        directory=Path(directory);meta=json.loads((directory/'metadata.json').read_text())
        if 'code_hashes' in meta:
            audit=verify_protocol(directory)
        else:
            path=ROOT.parent/'expanded/verify_expanded.py'
            sp=importlib.util.spec_from_file_location('legacy_quality_audit',path)
            legacy=importlib.util.module_from_spec(sp);sp.loader.exec_module(legacy)
            audit=legacy.verify_quality(directory)
            audit['run_dir']=str(directory.resolve())
        result['quality'].append(audit)
        all_quality.extend(r for r in analysis.READ(directory/'responses.jsonl') if r.get('phase')=='quality')
    cases={c['id']:c for c in analysis.READ(ROOT.parent/'expanded/data/cases.jsonl')}
    labels=list(models or sorted({r['model_label'] for r in all_quality}))
    analysis.check_source(all_quality,cases,'quality',labels)
    actual={(r['case_id'],r['model_label']) for r in all_quality}
    result['quality_cross_product_complete']=actual=={(cid,m) for cid in cases for m in labels}
    result['models']=labels
    if timing_dir:result['timing']=verify_protocol(timing_dir)
    for directory in cache_dirs or []:result['cache'].append(verify_protocol(directory))
    result['passed']=result['quality_cross_product_complete'] and all(x['passed'] for x in result['quality']+result['cache']+([result['timing']] if result['timing'] else []))
    if output_path:Path(output_path).write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('run_dir',type=Path);p.add_argument('--cases',type=Path);p.add_argument('--output',type=Path,default=ROOT/'verification.json')
    p.add_argument('--models',nargs='+');a=p.parse_args();result=verify_run(a.run_dir,a.cases,expected_models=a.models) if a.cases else verify_protocol(a.run_dir)
    result['generated_at_utc']=datetime.now(timezone.utc).isoformat();a.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'passed':result['passed'],'output':str(a.output)}))
