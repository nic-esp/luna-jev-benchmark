#!/usr/bin/env python3
"""Offline expanded-study analysis. No API calls or third-party dependencies.

analyze(quality_dir, timing_dir=None, output_dir=None, cases_path=None)

The adjacent analysis-spec.json fixes endpoints before outcomes are inspected.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import statistics

ROOT=Path(__file__).resolve().parent
MODELS=('Jev','Luna')
DEFAULT_SEED=20261002
DEFAULT_DRAWS=5000
EPS=1e-15


def read_jsonl(path):
    output=[]
    with Path(path).open(encoding='utf-8') as source:
        for line_no,line in enumerate(source,1):
            if not line.strip():continue
            try: row=json.loads(line)
            except json.JSONDecodeError as exc:raise ValueError(f'{path}:{line_no}: invalid JSON') from exc
            if not isinstance(row,dict):raise ValueError(f'{path}:{line_no}: expected object')
            output.append(row)
    return output


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def finite(x):return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)
def average(xs):return math.fsum(xs)/len(xs) if xs else None

def quantile(xs,p):
    if not xs:return None
    ordered=sorted(xs);k=(len(ordered)-1)*p;lo=math.floor(k);hi=math.ceil(k)
    return ordered[lo]+(ordered[hi]-ordered[lo])*(k-lo)


def interval(xs):return [quantile(xs,.025),quantile(xs,.975)] if xs else None

def wilson(correct,n):
    if not n:return None
    z=1.959963984540054;p=correct/n;den=1+z*z/n
    mid=(p+z*z/(2*n))/den;half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [max(0,mid-half),min(1,mid+half)]


def valid(row):
    p,t=row.get('probability'),row.get('target');kind=row.get('target_kind')
    return row.get('valid') is True and finite(p) and 0<=p<=1 and finite(t) and 0<=t<=1 and kind in ('label','probability') and (kind!='label' or t in (0,1))


def correct(row):return int(valid(row) and ((row['probability']>=.5)==bool(row['target'])))

def logloss(row):
    p=max(EPS,min(1-EPS,row['probability']));t=row['target']
    return -(t*math.log(p)+(1-t)*math.log1p(-p))


def costs(rs):
    values=[r['cost_usd'] for r in rs if finite(r.get('cost_usd')) and r['cost_usd']>=0]
    missing=len(rs)-len(values);known=math.fsum(values)
    return {'requests':len(rs),'known_billed_usd':known,'observed_bills':len(values),'missing_bills':missing,
            'total_billed_usd':known if not missing else None,
            'usd_per_1000_attempts':known/len(rs)*1000 if rs and not missing else None}


def class_counts(rs):
    result={}
    for target,name in ((0,'no'),(1,'yes')):
        rows=[r for r in rs if r['target']==target];n=len(rows);k=sum(correct(r) for r in rows)
        result[name]={'attempts':n,'valid':sum(valid(r) for r in rows),'correct':k,'recall_all_attempts':k/n if n else None}
    return result


def balanced(counts):
    values=[counts[label]['recall_all_attempts'] for label in ('no','yes')]
    return sum(values)/2 if all(x is not None for x in values) else None


def describe(rs,kind):
    accepted=[r for r in rs if valid(r)];n=len(rs);nv=len(accepted)
    out={'attempts':n,'valid':nv,'invalid':n-nv,'success_rate':nv/n if n else None,
         'success_rate_wilson95':wilson(nv,n),'unique_cases':len({r['case_id'] for r in rs}),
         'target_kind':kind,'cost':costs(rs)}
    if kind=='label':
        k=sum(correct(r) for r in rs);counts=class_counts(rs)
        out['quality']={'correct':k,'accuracy_all_attempts':k/n if n else None,'accuracy_all_attempts_wilson95':wilson(k,n),
                        'balanced_accuracy_all_attempts':balanced(counts),'class_denominators':counts,
                        'accuracy_valid_only':k/nv if nv else None,'accuracy_valid_only_wilson95':wilson(k,nv),
                        'balanced_accuracy_valid_only':balanced(class_counts(accepted)),
                        'brier_valid_only':average([(r['probability']-r['target'])**2 for r in accepted]),
                        'log_loss_valid_only':average([logloss(r) for r in accepted]),'proper_score_denominator':nv}
        out['cost']['usd_per_correct_answer']=out['cost']['total_billed_usd']/k if k and out['cost']['total_billed_usd'] is not None else None
    else:
        mse=average([(r['probability']-r['target'])**2 for r in accepted])
        out['quality']={'mae_valid_only':average([abs(r['probability']-r['target']) for r in accepted]),
                        'rmse_valid_only':math.sqrt(mse) if mse is not None else None,'excess_brier_valid_only':mse,
                        'expected_brier_valid_only':average([(r['probability']-r['target'])**2+r['target']*(1-r['target']) for r in accepted]),
                        'error_score_denominator':nv}
    return out


def cluster_key(case):
    if case['experiment'].lower()=='boolq' and isinstance(case.get('state',{}).get('passage'),str):
        return 'passage:'+hashlib.sha256(case['state']['passage'].encode()).hexdigest()
    return 'case:'+case['id']


def mcnemar_exact(luna_only,jev_only):
    n=luna_only+jev_only
    if not n:return 1.0
    k=min(luna_only,jev_only)
    # Log-scale binomial probability avoids underflow for large discordance counts.
    logs=[math.lgamma(n+1)-math.lgamma(i+1)-math.lgamma(n-i+1)-n*math.log(2) for i in range(k+1)]
    peak=max(logs)
    return min(1.,2*math.exp(peak)*math.fsum(math.exp(value-peak) for value in logs))


def paired_quality(rs,cases,kind,draws=DEFAULT_DRAWS,seed=DEFAULT_SEED):
    by_case=defaultdict(dict)
    for r in rs:
        model=r['model_label'];cid=r['case_id']
        if model in by_case[cid]:raise ValueError(f'Duplicate quality request for {cid}/{model}')
        by_case[cid][model]=r
    pairs=[(case_id,arms['Jev'],arms['Luna']) for case_id,arms in sorted(by_case.items()) if all(m in arms for m in MODELS)]
    grouped=defaultdict(list)
    for cid,j,l in pairs:
        if j['target']!=l['target'] or j['target_kind']!=l['target_kind']:raise ValueError(f'Paired target mismatch: {cid}')
        grouped[cluster_key(cases[cid])].append((j,l))
    # Each vector holds row counts and sums, so duplicate-passage clusters keep
    # every row and use the correct variable denominator in a cluster resample.
    vectors=[]
    discordant={'both_correct':0,'luna_only_correct':0,'jev_only_correct':0,'both_incorrect':0}
    for group in grouped.values():
        v=[0.]*12
        for j,l in group:
            # n, no n, yes n, accuracy delta, no delta, yes delta,
            # valid-pair n, squared-error delta, logloss delta, absolute-error delta,
            # Luna squared-error sum, Jev squared-error sum.
            v[0]+=1;label=int(j['target']) if kind=='label' else 0
            if kind=='label':
                cj,cl=correct(j),correct(l);delta=cl-cj
                v[1+label]+=1;v[3]+=delta;v[4+label]+=delta
                key='both_correct' if cj and cl else 'luna_only_correct' if cl else 'jev_only_correct' if cj else 'both_incorrect'
                discordant[key]+=1
            if valid(j) and valid(l):
                sj=(j['probability']-j['target'])**2;sl=(l['probability']-l['target'])**2
                v[6]+=1;v[7]+=sl-sj;v[9]+=abs(l['probability']-l['target'])-abs(j['probability']-j['target'])
                v[10]+=sl;v[11]+=sj
                if kind=='label':v[8]+=logloss(l)-logloss(j)
        vectors.append(v)
    def evaluate(v):
        result={}
        if kind=='label':
            result['accuracy_all_attempts_difference']=v[3]/v[0] if v[0] else None
            result['balanced_accuracy_all_attempts_difference']=(v[4]/v[1]+v[5]/v[2])/2 if v[1] and v[2] else None
            result['brier_valid_pairs_difference']=v[7]/v[6] if v[6] else None
            result['log_loss_valid_pairs_difference']=v[8]/v[6] if v[6] else None
        else:
            result['mae_valid_pairs_difference']=v[9]/v[6] if v[6] else None
            result['excess_brier_valid_pairs_difference']=v[7]/v[6] if v[6] else None
            result['rmse_valid_pairs_difference']=math.sqrt(v[10]/v[6])-math.sqrt(v[11]/v[6]) if v[6] else None
        return result
    totals=[math.fsum(v[k] for v in vectors) for k in range(12)]
    point=evaluate(totals);samples={key:[] for key in point};rng=random.Random(seed)
    if len(vectors)>=2:
        for _ in range(draws):
            batch=[vectors[rng.randrange(len(vectors))] for i in vectors]
            total=[math.fsum(v[k] for v in batch) for k in range(12)]
            for key,value in evaluate(total).items():
                if value is not None:samples[key].append(value)
    result={'direction':'Luna minus Jev','attempted_case_union':len(by_case),'paired_cases':len(pairs),
            'unpaired_cases':len(by_case)-len(pairs),'valid_response_pairs':int(totals[6]),'invalid_response_pairs':len(pairs)-int(totals[6]),
            'resampling_clusters':len(vectors),'cluster_rule':'Exact identical BoolQ passage; otherwise case identity',
            'bootstrap_replicates':draws,'bootstrap_seed':seed,'confidence_level':.95,'inference':'exploratory; no multiplicity adjustment',
            'metrics':{key:{'estimate':value,'ci95':interval(samples[key]),'defined_bootstrap_draws':len(samples[key])} for key,value in point.items()}}
    if kind=='label':
        result['correctness_pairs']=discordant
        result['mcnemar_exact_two_sided_p_exploratory']=mcnemar_exact(discordant['luna_only_correct'],discordant['jev_only_correct'])
        result['mcnemar_limit']='Treats discordant rows as independent; duplicate passages/shared templates violate that assumption. Do not substitute this test for the passage-cluster intervals.'
    return result


def latency_stats(rs):
    xs=[r['latency_s'] for r in rs if finite(r.get('latency_s')) and r['latency_s']>0]
    return {'attempts':len(rs),'valid':sum(valid(r) for r in rs),'observed_positive_timings':len(xs),
            'median_s':quantile(xs,.5),'p95_s':quantile(xs,.95),'mean_s':average(xs)}


def timing_analysis(rs,metadata,draws,seed):
    measured=[r for r in rs if r['phase']=='timing'];warmup=[r for r in rs if r['phase']=='timing_warmup']
    repeats=metadata.get('repeats',3);expected=set(range(repeats))
    result={'status':metadata.get('status','unknown'),'metadata':metadata,'measured_attempts':len(measured),'warmup_attempts':len(warmup),
            'expected_repeats':repeats,'cost_all_attempts':costs(rs),'cost_measured':costs(measured),'cost_warmups':costs(warmup),'tasks':{},
            'method':'Exclude warmups. Primary comparisons use cases with valid, positive finite response timings for both arms in all expected repeat blocks; reduce repeats to each case/arm median, then bootstrap paired case identities.',
            'inference':'Exploratory; case resampling preserves repeats but does not estimate provider/day-to-day variation. Cost includes all attempts.'}
    for ti,task in enumerate(sorted({r['experiment'] for r in measured})):
        selected=[r for r in measured if r['experiment']==task];by_case=defaultdict(lambda:defaultdict(dict))
        for r in selected:
            cid,arm,repeat=r['case_id'],r['model_label'],r.get('repeat',0)
            if repeat in by_case[cid][arm]:raise ValueError(f'Duplicate timing request: {cid}/{arm}/{repeat}')
            by_case[cid][arm][repeat]=r
        pairs=[]
        for cid,arms in sorted(by_case.items()):
            if not all(set(arms[m])==expected for m in MODELS):continue
            if not all(valid(r) and finite(r.get('latency_s')) and r['latency_s']>0 for m in MODELS for r in arms[m].values()):continue
            pairs.append((statistics.median(r['latency_s'] for r in arms['Jev'].values()),statistics.median(r['latency_s'] for r in arms['Luna'].values())))
        def metric(ps):
            if not ps:return {'ratio_of_case_medians':None,'median_of_case_ratios':None,'mean_case_latency_difference_s':None}
            return {'ratio_of_case_medians':statistics.median(l for j,l in ps)/statistics.median(j for j,l in ps),
                    'median_of_case_ratios':statistics.median(l/j for j,l in ps),'mean_case_latency_difference_s':average([l-j for j,l in ps])}
        point=metric(pairs);samples={k:[] for k in point};rng=random.Random(seed+ti)
        if len(pairs)>=2:
            for _ in range(draws):
                for key,value in metric([pairs[rng.randrange(len(pairs))] for p in pairs]).items():samples[key].append(value)
        blocks={}
        for repeat in sorted({r.get('repeat',0) for r in selected}):
            blocks[str(repeat)]={m:latency_stats([r for r in selected if r['model_label']==m and r.get('repeat',0)==repeat]) for m in MODELS}
            jm,lm=blocks[str(repeat)]['Jev']['median_s'],blocks[str(repeat)]['Luna']['median_s']
            blocks[str(repeat)]['ratio_of_medians_luna_over_jev']=lm/jm if jm and lm else None
        result['tasks'][task]={'case_union':len(by_case),'complete_valid_paired_cases':len(pairs),'excluded_cases':len(by_case)-len(pairs),
                               'by_model_all_attempts':{m:latency_stats([r for r in selected if r['model_label']==m]) for m in MODELS},
                               'primary_case_median_latency_s':{'Jev':quantile([j for j,l in pairs],.5),'Luna':quantile([l for j,l in pairs],.5)},
                               'paired':{'direction':'Luna minus Jev for difference; Luna divided by Jev for ratio','bootstrap_replicates':draws,'bootstrap_seed':seed+ti,
                                         'metrics':{k:{'estimate':v,'ci95':interval(samples[k])} for k,v in point.items()}},'repeat_blocks':blocks}
    return result


def short_number(x,digits=4):return f'{x:.{digits}f}' if finite(x) else 'unknown'

def make_markdown(summary):
    lines=['# Expanded paired benchmark analysis','','All confidence intervals are exploratory and unadjusted. Concurrent bulk timings are diagnostic only.','',
           f"Quality run status: **{summary['quality_run']['metadata'].get('status','unknown')}**. Recorded attempts: **{summary['quality_run']['attempts']}**.",'',
           '| Task | Model | Valid / attempts | Accuracy, all attempts | Balanced accuracy | Brier, valid | MAE, valid | USD / 1,000 attempts |',
           '|---|---|---:|---:|---:|---:|---:|---:|']
    for task,s in summary['tasks'].items():
        for arm,d in s['models'].items():
            q=d['quality'];lines.append('| '+' | '.join([task,arm,f"{d['valid']}/{d['attempts']}",short_number(q.get('accuracy_all_attempts')),short_number(q.get('balanced_accuracy_all_attempts')),short_number(q.get('brier_valid_only')),short_number(q.get('mae_valid_only')),short_number(d['cost']['usd_per_1000_attempts'],6)])+' |')
    lines+=['','## Interpretation limits','']+[f'- {s}' for s in summary['limitations']]
    lines+=['','## Serial timing','']
    if summary['timing']:
        lines+=['| Task | Complete paired cases | Jev case-median seconds | Luna case-median seconds | Luna / Jev ratio [95% CI] |','|---|---:|---:|---:|---|']
        for task,d in summary['timing']['tasks'].items():
            p=d['paired']['metrics']['ratio_of_case_medians'];ci=p['ci95']
            lines.append(f"| {task} | {d['complete_valid_paired_cases']} | {short_number(d['primary_case_median_latency_s']['Jev'])} | {short_number(d['primary_case_median_latency_s']['Luna'])} | {short_number(p['estimate'])} {str(ci) if ci else '[interval unavailable]'} |")
    else:lines.append('No completed serial timing run was supplied. No comparative speed conclusion is drawn from bulk latency.')
    lines+=['','Exact counts, Wilson intervals, paired cluster intervals, discordant counts, subgroup results, costs, and metadata are in summary.json.','']
    return '\n'.join(lines)


def analyze(quality_dir,timing_dir=None,output_dir=None,cases_path=None,bootstrap_samples=DEFAULT_DRAWS,seed=DEFAULT_SEED):
    if bootstrap_samples<0:raise ValueError('bootstrap_samples must be nonnegative')
    quality_dir=Path(quality_dir);output_dir=Path(output_dir or ROOT);cases_path=Path(cases_path or ROOT/'data/cases.jsonl')
    all_cases=read_jsonl(cases_path);cases={c['id']:c for c in all_cases}
    if len(cases)!=len(all_cases):raise ValueError('Duplicate frozen case IDs')
    all_rows=read_jsonl(quality_dir/'responses.jsonl');metadata=json.loads((quality_dir/'metadata.json').read_text())
    if metadata.get('cases_sha256') and metadata['cases_sha256']!=digest(cases_path):raise ValueError('Frozen cases hash does not match quality metadata')
    rs=[r for r in all_rows if r.get('phase')=='quality']
    if metadata.get('status')=='running':raise ValueError('Quality run is still running; analysis requires a finished or explicitly stopped run')
    for r in rs:
        if r.get('repeat',0)!=0:raise ValueError('Bulk quality row has nonzero repeat')
        if r['model_label'] not in MODELS:raise ValueError('Unknown model label')
        if r['case_id'] not in cases:raise ValueError('Unknown case ID')
        case=cases[r['case_id']]
        if r['target']!=case['target'] or r['target_kind']!=case['target_kind'] or r['experiment']!=case['experiment']:raise ValueError('Recorded case target/task does not match frozen source')
    tasks={}
    for ti,task in enumerate(sorted({c['experiment'] for c in all_cases})):
        selected=[r for r in rs if r['experiment']==task];kinds={c['target_kind'] for c in all_cases if c['experiment']==task}
        if len(kinds)!=1:raise ValueError('A task cannot mix labels and probability targets')
        kind=next(iter(kinds));subset={c['id']:c for c in all_cases if c['experiment']==task}
        tasks[task]={'expected_cases':len(subset),'target_kind':kind,'models':{m:describe([r for r in selected if r['model_label']==m],kind) for m in MODELS},
                     'paired':paired_quality(selected,cases,kind,bootstrap_samples,seed+ti)}
    pilot_path=ROOT.parent/'data/cases.jsonl';pilot_ids=set()
    if pilot_path.exists():pilot_ids={c['id'] for c in read_jsonl(pilot_path) if c['experiment']=='boolq'}
    subgroups={}
    if pilot_ids:
        boolq=[r for r in rs if r['experiment'].lower()=='boolq']
        for name,wanted in [('pilot60',True),('remaining3210',False)]:
            subset=[r for r in boolq if (r['case_id'] in pilot_ids)==wanted]
            subgroups[name]={'identification':'Existing pilot frozen case IDs; no selection on expanded performance','descriptive_only':True,
                             'case_count':len({r['case_id'] for r in subset}),
                             'models':{m:describe([r for r in subset if r['model_label']==m],'label') for m in MODELS}}
    timing=None
    if timing_dir:
        timing_dir=Path(timing_dir);tm=json.loads((timing_dir/'metadata.json').read_text())
        if tm.get('status')=='running':raise ValueError('Timing run is still running')
        timing=timing_analysis(read_jsonl(timing_dir/'responses.jsonl'),tm,bootstrap_samples,seed+100)
    spec_path=ROOT/'analysis-spec.json'
    summary={'analysis_version':1,'generated_at_utc':datetime.now(timezone.utc).isoformat(),'analysis_spec_sha256':digest(spec_path),
             'analysis_spec':json.loads(spec_path.read_text()),'bootstrap_replicates':bootstrap_samples,'seed':seed,
             'cases_sha256':digest(cases_path),'quality_records_sha256':digest(quality_dir/'responses.jsonl'),
             'quality_run':{'metadata':metadata,'attempts':len(rs),'expected_attempts':2*len(cases),'missing_attempts':2*len(cases)-len(rs),
                            'cost_all_phases':costs(all_rows),'cost_quality_phase':costs(rs),
                            'cost_by_phase':{phase:costs([r for r in all_rows if r['phase']==phase]) for phase in sorted({r['phase'] for r in all_rows})},
                            'bulk_latency_diagnostic_only':{m:latency_stats([r for r in rs if r['model_label']==m]) for m in MODELS}},
             'tasks':tasks,'experiments':tasks,'boolq_subgroups':subgroups,'timing':timing,
             'limitations':[
                 'Published benchmark scores are agreement with their frozen reference labels; source fidelity does not establish label correctness. Public development data may overlap model training.',
                 'Accuracy and balanced accuracy count invalid outputs as wrong. Brier/log loss and exact-probability errors require valid probabilities; their denominators and paired exclusions are explicit.',
                 'The full development splits are fixed finite benchmarks. Wilson intervals and bootstrap intervals are nominal exploratory summaries, not guarantees of deployment accuracy.',
                 'Identical BoolQ passages are resampled together. Other shared topics, synthetic templates and time/provider dependence are not fully represented by case resampling.',
                 'No multiple-comparison correction is applied. Degenerate intervals from uniform outcomes do not establish certainty or model equivalence. McNemar p-values additionally assume independent rows.',
                 'Concurrent bulk request latency is excluded from comparative speed claims. Serial timing reduces three repeats to case medians and reports block medians; it cannot establish performance under other traffic or provider conditions.',
                 'Luna reasoning is none. Native API/formatting overhead differs. Both expose the same answer object, but this does not equalize their architecture, training, tokenization or reasoning budget.',
                 'Brier combines calibration and discrimination. Exact-probability arithmetic tasks do not establish real-world forecasting calibration.',
                 'All attempted bills, including failed responses and warmups, remain accounted for. Missing bills remain unknown; per-1,000 costs project the observed workload only.',
                 'Pilot60 and remaining3210 BoolQ subgroups are descriptive. No retrospective relabeling or outcome-based case removal is applied.'
             ]}
    output_dir.mkdir(parents=True,exist_ok=True)
    (output_dir/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    (output_dir/'analysis.md').write_text(make_markdown(summary))
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('quality_dir',type=Path);p.add_argument('--timing-dir',type=Path);p.add_argument('--cases',type=Path);p.add_argument('--output-dir',type=Path,default=ROOT)
    p.add_argument('--bootstrap-samples',type=int,default=DEFAULT_DRAWS);p.add_argument('--seed',type=int,default=DEFAULT_SEED);a=p.parse_args()
    s=analyze(a.quality_dir,a.timing_dir,a.output_dir,a.cases,a.bootstrap_samples,a.seed)
    print(json.dumps({'summary':str((a.output_dir/'summary.json').resolve()),'quality_attempts':s['quality_run']['attempts'],'timing_supplied':s['timing'] is not None}))


if __name__=='__main__':main()
