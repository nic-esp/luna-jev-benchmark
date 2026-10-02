#!/usr/bin/env python3
"""Build the single comparative paper from saved evidence; no inference calls."""
from pathlib import Path
from collections import defaultdict
import html
import importlib.util
import json
import re
import statistics

ROOT=Path(__file__).resolve().parent
B=ROOT.parent
OUT=B.parent
OLD=B/'expanded'
TASKS={'boolq':'BoolQ','rte':'RTE','wic':'WiC','policy':'Simple policies','probability':'Exact probabilities'}
def read(p):return json.loads(Path(p).read_text())
def rows(p):return [json.loads(x) for x in Path(p).read_text().splitlines() if x.strip()]
def esc(x):return html.escape(str(x),quote=True)
def n(x,d=3):return f'{x:.{d}f}' if isinstance(x,(int,float)) else 'unknown'
def pct(x,d=2):return n(x*100,d)+'%' if x is not None else 'unknown'
def usd(x,d=6):return '$'+n(x,d) if x is not None else 'unknown'
def ci(x,scale=1,d=3):
    if x.get('estimate') is None:return 'undefined'
    out=n(x['estimate']*scale,d)
    return out+' ['+', '.join(n(y*scale,d) for y in x['ci95'])+']' if x.get('ci95') else out
def table(head,rr,caption=''):
    return '<div class="table-wrap"><table><caption>'+caption+'</caption><thead><tr>'+''.join('<th>'+str(h)+'</th>' for h in head)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+str(v)+'</td>' for v in r)+'</tr>' for r in rr)+'</tbody></table></div>'
def detail(title,value):return '<details><summary>'+esc(title)+'</summary><pre>'+esc(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,indent=2))+'</pre></details>'
def fig(name,caption):
    path=ROOT/'figures'/f'{name}.svg'
    if not path.exists():return ''
    href=f'benchmark/multimodel/figures/{name}'
    import xml.etree.ElementTree as ET
    box=ET.parse(path).getroot().get('viewBox').split()
    width,height=box[2:4]
    return f'<figure><img src="{href}.svg" width="{width}" height="{height}" alt="{esc(caption)}" loading="lazy"><figcaption>{caption} <a href="{href}.svg" download>SVG</a> · <a href="{href}.png" download>PNG</a></figcaption></figure>'
def section(ident,title,body):return f'<section id="{ident}"><h2>{title}</h2>{body}</section>'
def source_path(path):
    p=Path(path)
    if p.is_absolute():return p
    if (OUT/p).exists():return OUT/p
    if (ROOT/p).exists():return ROOT/p
    return p

def build(destination=None):
    destination=Path(destination or OUT)
    destination.mkdir(parents=True,exist_ok=True)
    s=read(ROOT/'summary.json');models=s['models'];tasks=s['tasks'];timing=s['timing']
    if s['quality']['missing_attempts'] or not timing or not timing['comparative_speed_eligible']:
        raise ValueError('Complete quality and interleaved timing are required for publication')
    if len(s['cache_sessions'])!=1:raise ValueError('Select one primary cache session')
    cache=s['cache_sessions'][0]
    quality_dirs=[source_path(x['run_dir']) for x in s['quality_sources']]
    timing_dir=source_path(timing['source']['run_dir']);cache_dir=source_path(cache['source']['run_dir'])
    helper_spec=importlib.util.spec_from_file_location('report_evidence',ROOT/'report_evidence.py')
    helper=importlib.util.module_from_spec(helper_spec);helper_spec.loader.exec_module(helper)
    evidence_result=helper.build_evidence(quality_dirs,timing_dir,cache_dir)
    evidence=evidence_result['evidence'];allrows=evidence_result['records']
    quality=[r for d in quality_dirs for r in rows(d/'responses.jsonl') if r['phase']=='quality']
    tr=rows(timing_dir/'responses.jsonl');cr=rows(cache_dir/'responses.jsonl')
    specs={}
    for src in s['quality_sources']+[timing['source']]:
        cfg=src['metadata']['models']
        specs.update({x['label']:x for x in cfg} if isinstance(cfg,list) else {k:({'id':v} if isinstance(v,str) else v) for k,v in cfg.items()})
    short=', '.join(models)
    mode_rows=[]
    for m in models:
        spec=specs[m]
        mode_rows.append([esc(m),'<code>'+esc(spec['id'])+'</code>',esc(spec.get('provider_name',spec.get('provider','Native Decisions'))+' · '+str(spec.get('provider','Native Decisions'))),esc(spec.get('reasoning') or 'Native service'),'Native Noul' if m=='Jev' else 'Strict JSON · max 128',esc(spec.get('protocol_difference') or 'Shared compact-answer protocol')])
    compatible=set(models)
    for m in ('Gemini 3.8 Flash','GLM 5.3 Flash','GPT-OSS-120B'):
        if m not in compatible:mode_rows.append([m,'See capability evidence','—','Mandatory','Not evaluated','Cannot meet reasoning-disabled configuration'])
    modeltable=table(['Model','Requested ID','Pinned provider','Reasoning','Output','Configuration'],mode_rows,'Table 1. Exact evaluated services and configuration compatibility.')
    qrows=[];prows=[];costrows=[];contrast=[];conditional=[]
    for task in TASKS:
        for m in models:
            v=tasks[task]['models'][m];q=v['quality'];cost=v['cost']
            if task=='probability':prows.append([m,f"{v['valid']}/{v['attempts']}",n(q['mae_valid_only']*100),n(q['rmse_valid_only']*100),n(q['excess_brier_valid_only'],6),n(q['expected_brier_valid_only'],6)])
            else:
                conditional.append([TASKS[task],m,f"{q['correct']}/{v['valid']}",pct(q['accuracy_valid_only']),pct(q['balanced_accuracy_valid_only'])])
                w=q['accuracy_all_attempts_wilson95']
                qrows.append([TASKS[task],m,f"{v['valid']}/{v['attempts']}",f"{q['correct']}/{v['attempts']} · {pct(q['accuracy_all_attempts'])}",'['+', '.join(pct(x) for x in w)+']',pct(q['balanced_accuracy_all_attempts']),n(q['brier_valid_only'],4),n(q['log_loss_valid_only'],4)])
            costrows.append([TASKS[task],m,v['attempts'],usd(cost['known_billed_usd'],9),cost['missing_bills'],cost['observed_bills'],usd(cost['known_billed_usd']/cost['observed_bills']*1000 if cost['observed_bills'] else None),usd(cost['usd_per_1000_attempts'])])
        for name,p in tasks[task]['comparisons'].items():
            for metric,v in p['metrics'].items():
                scale=100 if metric.startswith(('accuracy','balanced','mae','rmse')) else 1
                contrast.append([TASKS[task],name,metric.replace('_',' ')+(' (pp)' if scale==100 else ''),ci(v,scale),p['paired_cases'],p['valid_response_pairs'],p['resampling_clusters']])
    result_text=' '.join(f"{TASKS[t]} accuracy: "+', '.join(m+' '+pct(tasks[t]['models'][m]['quality']['accuracy_all_attempts']) for m in models)+'.' for t in ('boolq','rte','wic'))
    prob_text='Exact-probability MAE: '+', '.join(m+' '+n(tasks['probability']['models'][m]['quality']['mae_valid_only']*100,2)+' percentage points' for m in models)+'.'
    time_rows=[];ratio_rows=[];repeat_rows=[];consistency=[]
    for task,d in timing['tasks'].items():
        for m in models:
            v=d['models'][m]
            time_rows.append([TASKS[task],m,d['common_complete_cases'],n(v['primary_case_median_latency_s']),n(v['all_attempts']['p95_s']),v['complete_case_count']])
            grouped=defaultdict(list)
            for r in tr:
                if r['phase']=='timing' and r['model_label']==m and r['experiment']==task and r['valid']:grouped[r['case_id']].append(r['probability'])
            complete=[ps for ps in grouped.values() if len(ps)==3]
            consistency.append([TASKS[task],m,len(complete),sum(len({p>=.5 for p in ps})==1 for ps in complete) if task!='probability' else 'Not a binary target task',n(statistics.mean(max(ps)-min(ps) for ps in complete),5) if complete else 'unknown'])
        for name,p in d['comparisons'].items():ratio_rows.append([TASKS[task],name,ci(p['metrics']['ratio_of_case_medians']),ci(p['metrics']['median_of_case_ratios']),p.get('paired_cases',d['common_complete_cases'])])
        for block,b in d['repeat_blocks'].items():
            for m,v in b.items():repeat_rows.append([TASKS[task],int(block)+1,m,n(v['median_s']),n(v['p95_s'])])
    cache_rows=[];counter_rows=[];cache_ci=[];block_rows=[];pair_rows=[]
    for arm in cache['arms']:
        pairs=defaultdict(list)
        for r in cr:
            if r['phase']=='measured' and r['model_label']==arm:pairs[(r['block'],r['pair_id'])].append(r)
        complete=[rr for rr in pairs.values() if len(rr)==2]
        both=sum(all(r['valid'] and (r['probability']>=.5)==bool(r['target']) for r in rr) for rr in complete)
        pair_rows.append([arm,f'{both}/{len(complete)}',pct(both/len(complete)) if complete else 'undefined'])
    for arm,v in cache['arms'].items():
        q=v['quality'];c=v['reported_cache_usage']
        cache_rows.append([arm,f"{v['valid']}/{v['attempts']}",pct(q['accuracy_all_attempts']),n(q['brier_valid_only'],4),n(v['latency']['median_s']),usd(v['cost']['usd_per_1000_attempts']),usd(v['setup_inclusive_usd_per_1000_decisions'])])
        counter_rows.append([arm,c['positive_read_calls'],c['read_tokens']['known_subtotal'],c['read_tokens']['unknown_calls'],c['write_tokens']['known_subtotal'],c['write_tokens']['unknown_calls'],v['prime_attempts'],usd(v['priming_cost']['total_billed_usd'],9)])
    for name,p in cache['comparisons'].items():
        for metric,v in p['metrics'].items():cache_ci.append([name,('Accuracy difference (pp)' if metric=='accuracy_difference' else metric.replace('_',' ')),ci(v,100 if metric=='accuracy_difference' else 1,4),p['paired_cases'],p['clusters']])
    for block,arms in cache['blocks'].items():
        for arm,v in arms.items():block_rows.append([int(block)+1,arm,pct(v['quality']['quality']['accuracy_all_attempts']),n(v['latency']['median_s']),usd(v['quality']['cost']['usd_per_1000_attempts'])])
    treatment=read(cache_dir/'summary.json').get('cache_verification',{})
    cache_verified=treatment.get('Luna',{}).get('status')=='verified'
    cache_note=''
    if {'Luna cached','Luna uncached','Jev'}<=set(cache['arms']):
        on,off,j=[cache['arms'][x] for x in ('Luna cached','Luna uncached','Jev')]
        if cache_verified and all(x['cost']['total_billed_usd'] is not None for x in (on,off,j)):
            cache_note=f"Measured cached Luna requests cost {pct(1-on['cost']['total_billed_usd']/off['cost']['total_billed_usd'])} less than its controlled uncached arm. Including all four primary-session primes, its cost was {usd(on['setup_inclusive_usd_per_1000_decisions'])} per 1,000 decisions, versus {usd(j['cost']['usd_per_1000_attempts'])} for Jev. Accuracy was {pct(on['quality']['accuracy_all_attempts'])} and {pct(j['quality']['accuracy_all_attempts'])}, respectively."
    audit=[];tokens=[]
    for m in models:
        rr=[r for r in quality if r['model_label']==m];vr=[r for r in rr if r['valid']]
        out=[r['usage'].get('completion_tokens',r['usage'].get('output_tokens')) for r in vr]
        out=[x for x in out if isinstance(x,(int,float))]
        reasoning=[(r.get('usage',{}).get('completion_tokens_details') or {}).get('reasoning_tokens') for r in rr]
        audit.append([m,len(rr),len(vr),f'{min(out)}–{max(out)}' if out else 'unknown',sum(x==0 for x in reasoning),sum(isinstance(x,(int,float)) and x>0 for x in reasoning),sum(x is None for x in reasoning),sum(bool(r.get('visible_reasoning')) for r in rr)])
        for t in TASKS:
            rrs=[r for r in vr if r['experiment']==t];ii=[r['usage'].get('prompt_tokens',r['usage'].get('input_tokens')) for r in rrs];oo=[r['usage'].get('completion_tokens',r['usage'].get('output_tokens')) for r in rrs]
            tokens.append([TASKS[t],m,len(rrs),n(statistics.mean(ii),1) if ii and all(isinstance(x,(int,float)) for x in ii) else 'unknown',n(statistics.mean(oo),1) if oo and all(isinstance(x,(int,float)) for x in oo) else 'unknown'])
    ledger=[];groups=defaultdict(list)
    for r in allrows:groups[(r['run'],r['phase'])].append(r)
    for (run,phase),rr in groups.items():ledger.append([run,phase,len(rr),sum(r['valid'] for r in rr),usd(sum(r['cost_usd'] for r in rr if r['cost_usd'] is not None),9),sum(r['cost_usd'] is None for r in rr)])
    known=sum(r['cost_usd'] for r in allrows if r['cost_usd'] is not None);missing=sum(r['cost_usd'] is None for r in allrows)
    provenance=[]
    for m,pr in s['model_provenance'].items():provenance.append([m,esc(json.dumps(pr,ensure_ascii=False))])
    start=min(r['started_at'] for r in allrows);end=max(r['started_at'] for r in allrows)
    original=(OLD/'template.html').read_text()
    datasets=original.split('<h3>2.2 Established benchmark datasets</h3>',1)[1].split('<h3>2.4 Request schedule',1)[0]
    datasets=datasets.replace('The primary dataset, timing sample and analysis specification were fixed before primary outcomes were inspected.', 'The primary dataset and timing sample were fixed before the Jev/Luna primary outcomes. The added-model analysis specification was fixed before added-model outcomes, with existing Jev/Luna outcomes already known.')
    datasets=datasets.replace('@@DATASETTABLE@@',table(['Task','Cases','Reference'],[[TASKS[t],tasks[t]['expected_cases'],'Published labels' if t in ('boolq','rte','wic') else 'Exact oracle'] for t in TASKS]))
    methods=f'''<h3>Models and output restriction</h3>{modeltable}<p>Candidate selection uses OpenRouter token-usage popularity and documented zero-reasoning support. DeepSeek V4.1 Flash, MiMo V2.6 Flash and Hy4 Preview ranked 2, 4 and 8 in the latest complete day (1 October 2026). Popularity does not measure accuracy or speed. Exact provider choices, the setup-only Hy4 provider amendment, capability sources and exclusions are recorded in the <a href="benchmark/multimodel/model-selection.json">prospective selection record</a>.</p><div class="definition"><strong>Shared answer contract</strong><pre>{{"type":"noul","noul":0.73}}</pre><p>p is P(yes); P(no) = 1 − p. The binary answer is yes when p ≥ 0.5. All models receive the same state, question and yes/no criteria. Jev uses native typed decisions; chat models receive the same format instruction and strict JSON Schema, a 128-token completion ceiling, no tools and non-streaming output. Temperature is unspecified. No label, target, case ID or provenance is sent. Chat models generate the numeric probability; Jev returns its native Noul value. Neither adapter extracts token log probabilities. Decimal precision and tokenization may differ.</p></div><p>Provider routing is pinned and fallback disabled. Latency results apply to these selected endpoints; a different provider can change both speed and cost. All evaluated chat models use reasoning.effort=none. Models with mandatory reasoning are excluded. Returned reasoning counters are audited; an absent counter remains unknown.</p>{table(['Model','Attempts','Valid','Output-token range','Zero reasoning reported','Positive reasoning','Reasoning unreported','Visible reasoning'],audit,'Output restriction audit: reasoning counters cover all primary quality attempts; output length describes valid outputs only.')}
    <h3>Datasets and exact references</h3>{datasets}
    <h3>Schedule, failures and measurement</h3><p>Data were collected through OpenRouter on 2 October 2026, from {start[11:16]} to {end[11:16]} UTC.</p><p>Quality uses one attempt per case and model. Jev/Luna, Qwen, and DeepSeek/MiMo/Hy4 were collected in separate sessions, with randomized case and within-case model order inside each session. Up to 16 case groups run concurrently on persistent HTTPS connections; comparisons pair the same frozen case identities across sessions. Quality latency is diagnostic only. The serial timing protocol interleaves every eligible model on the same 60 cases in three randomized blocks, after bulk traffic finishes. Two warmups per model and block are excluded from speed estimates and retained in billing. No automatic retry or answer repair occurs. After repeated shared-capacity errors, Qwen’s remaining unattempted quality cases used a prospectively recorded two-worker ceiling and at least 0.25 seconds between dispatch claims. Earlier failures remain in the scoring and billing records; the request payloads and provider are unchanged.</p><p>Latency starts before connection setup/request and ends after the entire response is received, parsed and validated. Payload serialization occurs before timing. It includes the network, OpenRouter, provider processing and client parsing; it is complete-response latency. Invalid requests remain in all-attempt accuracy and cost denominators. The added-model runner stops after five consecutive invalid responses or an invalid fraction of at least 5% after 100 calls, globally or within a model. An allowance of at least $0.01 per unreported bill is used only for the budget guard; it is never reported as a measured cost.</p>
    <h3>Metrics and uncertainty</h3><div class="two-col"><div><h4>Binary labels</h4><p>Accuracy counts invalid responses as wrong. Balanced accuracy averages recall for yes and no. Brier = mean[(p−y)²]. Log loss = −mean[y ln(p)+(1−y)ln(1−p)], clipped at 10⁻¹⁵. Proper scores use valid responses, with denominators retained.</p></div><div><h4>Exact probabilities</h4><p>MAE = mean|p−q|. RMSE = √mean[(p−q)²]. Excess Brier is mean[(p−q)²]; expected Brier adds q(1−q). Probability error in percentage points is error ×100. Exact probabilities are not replaced with sampled outcomes.</p></div></div><p>Paired percentile intervals use 5,000 resamples: exact duplicate BoolQ passages are clustered; other quality tasks use case identities. Marginal accuracy uses Wilson intervals, which assume independent rows. Paired BoolQ intervals account for exact duplicate passages; all intervals are conditional on these cases and sessions. Added models compare with both Jev and Luna; differences subtract the named reference. Timing first takes each case/model’s median over three repeats, then compares medians across cases. Primary timing uses the common set complete for all participating models; pairwise support is retained as a sensitivity analysis. Cache resampling keeps counterfactual pairs together within four fixed blocks. All intervals are exploratory and unadjusted for multiple comparisons.</p><p>Reported cost uses usage.cost, including input, output and any billed reasoning/cache operations. Cost per 1,000 = total reported charge ÷ attempts ×1,000. Missing bills remain unknown and suppress complete cost projections. The same model can have different costs on different input lengths and cache conditions.</p>'''
    results=table(['Task','Model','Valid / attempts','Correct / attempts','Wilson 95%','Balanced accuracy','Brier ↓','Log loss ↓'],qrows,'Table 2. Binary decision quality; published labels for public benchmarks.')+fig('quality','Figure 1. Accuracy by task, with nominal 95% intervals.')+'<p>'+result_text+'</p><details><summary>Binary accuracy conditional on valid responses</summary>'+table(['Task','Model','Correct / valid','Accuracy among valid','Balanced accuracy among valid'],conditional,'Sensitivity: excludes service and schema failures; primary accuracy retains them as wrong.')+'</details><h3>Exact-probability estimates</h3>'+table(['Model','Valid / attempts','MAE (pp) ↓','RMSE (pp) ↓','Excess Brier ↓','Expected Brier ↓'],prows,'Table 3. Exact synthetic probability targets.')+fig('probability','Figure 2. Exact-probability error.')+'<p>'+prob_text+'</p>'
    results+='<details><summary>Paired comparisons</summary>'+table(['Task','Contrast','Metric','Estimate [95%]','Paired cases','Valid pairs','Clusters'],contrast,'Table 4. Direction: first model minus reference.')+'</details><h3>Billed cost by workload</h3>'+table(['Task','Model','Attempts','Known subtotal','Missing bills','Reported bills','USD / 1,000 billed calls','USD / 1,000 all attempts'],costrows,'Table 5. Quality charges.')+'<p>Luna uses explicit cache mode without a breakpoint. Other providers may cache implicitly; their returned counters remain in the records. The billed-call mean excludes attempts whose charges were not reported; it is not a full-attempt cost estimate. Complete projections remain unknown when bills are missing. Costs describe the measured service configuration, with no assumption that an unmarked request is cache-off.</p>'
    timing_start=min(r['started_at'] for r in tr);timing_end=max(r['started_at'] for r in tr)
    timinghtml=f'<p>Timing requests began between {timing_start[:10]} {timing_start[11:19]} and {timing_end[:10]} {timing_end[11:19]} UTC. {timing["measured_attempts"]:,} measured calls: 60 cases ×3 repeats ×{len(models)} models, with {timing["warmup_attempts"]} separately accounted warmups. Every model is interleaved within one measurement window. Earlier timing calls are retained as supplementary evidence.</p>'+table(['Task','Model','Common complete cases','Case-median latency (s)','All-call p95 (s)','Complete model cases'],time_rows,'Table 6. Median across within-case medians; p95 uses all measured calls.')+fig('timing','Figure 3. Complete-response latency.')+table(['Task','Model / reference','Ratio of case medians [95%]','Median paired ratio [95%]','Pairs'],ratio_rows,'Table 7. A ratio of 2 means twice the latency.')+'<details><summary>Repeats and answer variation</summary>'+table(['Task','Block','Model','Median (s)','p95 (s)'],repeat_rows)+table(['Task','Model','Complete cases','Same binary answer over 3','Mean probability range'],consistency)+'</details>'
    cachehtml='<div class="note"><strong>Controlled Luna cache treatment: '+('verified in all four blocks' if cache_verified else 'not verified; savings claims withheld')+'.</strong></div><p>Four blocks share the same 1,903-word fictional rulebook, each with a fresh administrative prefix identifier. The 96 cases are 48 counterfactual pairs with one controlling fact changed. Every model sees the same facts and rules. Randomized arm order compares all eligible models; Luna additionally has cached and uncached arms and one cold prime per block.</p><p>Luna’s controlled arms use identical two-block text, settings, a 30-minute TTL and block key. Only the cached arm marks the prefix breakpoint. The other providers have no verified OpenRouter on/off control in this protocol: their single long-input arm is described by observed counters. A positive read counter establishes a hit for that request, without creating a controlled cache comparison.</p>'+table(['Arm','Valid / attempts','Accuracy','Brier ↓','Median (s)','USD / 1,000 measured','USD / 1,000 incl. primes'],cache_rows,'Table 8. Primary-session cold primes allocated to the decisions served.')+fig('cache-1','Figure 4. Long-prefix decision cost, latency and explicit prime allocation.')+'<p>'+cache_note+'</p>'+table(['Arm','Read-hit calls','Read tokens','Unknown read counters','Written tokens','Unknown write counters','Extra primes','Prime cost'],counter_rows,'Table 9. Returned cache evidence; missing values are not imputed as zero.')+'<details><summary>Cache blocks and comparisons</summary>'+table(['Arm','Both counterfactual cases correct','Fraction'],pair_rows)+table(['Block','Arm','Accuracy','Median (s)','USD / 1,000'],block_rows)+table(['Contrast','Metric','Estimate [95%]','Cases','Pair clusters'],cache_ci)+'</details><p>A setup session was stopped because its 66-character Luna cache keys exceeded the provider’s 64-character limit. Its 55 attempts remain supplementary evidence, including one interrupted call with an unknown outcome. Those setup costs remain in the complete study ledger and are excluded from Table 8. The primary session uses a validated 46-character administrative key; rules, case facts, schema and model settings are unchanged, with fresh administrative prefix identifiers. Cache findings are conditional on prefix length, actual hits, four cold writes and 24 measured reuses per prefix. They do not measure expiry, eviction, low reuse, concurrent cache traffic or multi-day availability.</p>'
    # Keep the original label audit and provenance intact in the paper.
    dq=original.split('<section id="data-quality">',1)[1].split('</section>',1)[0]
    carbon=[r for r in quality if r['case_id']=='boolq-dev-1842']
    dq=dq.replace('@@CARBON@@','Primary answers: '+', '.join(r['model_label']+' '+('yes' if r['probability'] is not None and r['probability']>=.5 else 'no' if r['valid'] else 'invalid') for r in carbon)+'.')
    sens=[]
    for m in models:
        rr=[r for r in quality if r['experiment']=='boolq' and r['case_id']!='boolq-dev-1842' and r['model_label']==m]
        k=sum(r['valid'] and (r['probability']>=.5)==bool(r['target']) for r in rr)
        sens.append([m,f'{k}/{len(rr)}',pct(k/len(rr))])
    dq=dq.replace('@@SENSITIVITY@@',table(['Model','Correct / remaining','Accuracy'],sens,'Post-hoc exclusion of the one known contradictory label; primary scores unchanged.'))
    dq=dq.replace('All 3,270 primary benchmark predictions were obtained from new calls. The subgroup table shows the 60 overlapping rows and the remaining 3,210 rows to make this overlap transparent; no rows were excluded based on performance.', 'Every primary case/model has its own recorded attempt. The 60 overlapping rows and the remaining 3,210 rows are identified in the saved evidence; no rows were excluded based on performance.')
    dq=dq.replace('@@CLUSTERS@@','2,938').replace('@@OVERLAPTABLE@@','<p>Per-model subgroup evidence is retained in the original analysis and raw case records.</p>').replace('@@AUDIT@@','<pre>'+esc((B/'data/label-audit.md').read_text())+'</pre>')
    conclusion='<p>Accuracy, probability error, latency and cost are reported separately. Cache savings depend on prefix length, verified hit rate and the allocation of priming costs.</p>'
    discussion=conclusion+'<h3>Limits</h3><ul><li>The benchmark covers three public validation tasks and two synthetic task families. Public labels can be wrong and training contamination is unknown; these are not official SuperGLUE leaderboard scores.</li><li>One day, one account and one client environment cannot establish latency across regions, days or traffic levels. Jev/Luna quality records and later model records come from different hours; timestamps and configurations are retained. The comparative timing session interleaves all eligible models.</li><li>Matching a compact answer schema does not equalize internal processing. Model-specific reasoning limits and observed counters are reported explicitly.</li><li>Confidence intervals are exploratory, conditional on the resampling assumptions and unadjusted for multiple comparisons. Repeated synthetic templates do not add new domains.</li><li>Reported charges are reconciled to API usage, not an independent invoice. Missing bills remain unknown. Prices and provider configurations can change.</li></ul>'
    checks=''.join(detail(p.name,p.read_text()) for p in [ROOT/'verification.json',ROOT/'summary-verification.json',ROOT/'TESTS.txt'] if p.exists())
    accounting=f'<p>Across all primary and supplementary evidence, the ledger retains <strong>{len(allrows):,} API attempts</strong>, <strong>{sum(r["valid"] for r in allrows):,} valid outputs</strong>, <strong>{usd(known,9)} in known charges</strong> and <strong>{missing} unreported bill(s)</strong>. An unreported bill is not zero. Warmups, probes, primes and failed attempts remain visible.</p>'
    repro=accounting+table(['Protocol / session','Phase','Attempts','Valid','Known subtotal','Missing bills'],ledger,'Table 10. Call accounting.')+'<p>The quality dataset and targets are frozen. Added-model analysis was specified before those outcomes were observed. Existing Jev/Luna quality observations are reused on the identical case identities; timestamps are explicit. Model-specific deviations require a prospective recorded amendment. Earlier timing/cache measurements are supplementary and are not pooled into the primary tables.</p>'+detail('Model and measurement provenance',s['model_provenance'])+checks+'<h3>Rebuild without model calls</h3><pre>python3 multimodel/analyze.py --help\npython3 multimodel/build_report.py\npython3 -m unittest discover -s multimodel -p \'test_*.py\' -v</pre><p><a href="benchmark/multimodel/analysis-spec.json">Frozen analysis plan</a> · <a href="benchmark/multimodel/analysis-scope.md">Six-service scope clarification</a> · <a href="benchmark/multimodel/model-source-notes.md">Provider and model capability evidence</a> · <a href="benchmark/multimodel/model-selection-notes.md">Zero-reasoning model selection</a> · <a href="benchmark/expanded/data/manifest.json">Dataset manifest</a>. Public RTE source passages are omitted from this publication copy; original source retrieval is documented and all numerical results are retained.</p>'
    refs=original.split('<section id="references">',1)[1].split('</section>',1)[0]
    refs=refs.replace('This report is served locally.','').replace('This report is published as a static GitHub Pages site.','')+'<p>Additional primary sources: <a href="https://openrouter.ai/deepseek/deepseek-v4.1-flash">DeepSeek V4.1 Flash</a>, <a href="https://openrouter.ai/xiaomi/mimo-v2.6-flash">MiMo V2.6 Flash</a>, <a href="https://openrouter.ai/tencent/hy4-preview">Hy4 Preview</a>, <a href="https://openrouter.ai/rankings">OpenRouter usage rankings</a> (CC BY 4.0), <a href="https://openrouter.ai/qwen/qwen3.8-flash">Qwen3.8 Flash</a>, <a href="https://openrouter.ai/google/gemini-3.8-flash">Gemini 3.8 Flash</a>, <a href="https://openrouter.ai/z-ai/glm-5.3-flash">GLM 5.3 Flash</a>, and the <a href="benchmark/multimodel/model-source-notes.md">captured capability and upstream documentation</a>.</p>'
    system=next(r['request']['messages'][0]['content'] for r in quality if r['model_label']=='Luna')
    schema=next(r['request']['response_format'] for r in quality if r['model_label']=='Luna')
    prompts=detail('Shared chat system instruction',system)+detail('Strict JSON Schema',schema)
    for t in TASKS:
        caseid=next(r['case_id'] for r in quality if r['experiment']==t)
        for m in models:
            rr=next(r for r in quality if r['case_id']==caseid and r['model_label']==m)
            prompts+=detail(TASKS[t]+' · '+m+' · saved request',rr['request'])
    prompts+=detail('Complete fictional rulebook',(cache_dir/'rulebook.txt').read_text())
    assets=[]
    for p in sorted(B.rglob('*')):
        if p.is_file() and not any(part.startswith('.') or part=='__pycache__' for part in p.relative_to(B).parts) and '/report-data/records-' not in str(p):assets.append([f'<a href="{esc(p.relative_to(OUT))}" download>{esc(p.relative_to(B))}</a>',f'{p.stat().st_size:,} B'])
    explorer=evidence_result['explorer_html'];js=(ROOT/'explorer.js').read_text()
    appendix=table(['Task','Model','Valid','Mean input tokens','Mean output tokens'],tokens)+detail('Analysis JSON',s)+'<details><summary>Files</summary>'+table(['File','Size'],assets)+'</details><p><a href="benchmark.zip" download>Complete study archive</a> · <a href="benchmark/multimodel/report-data/requests.csv" download>Every call (CSV)</a> · <a href="benchmark/multimodel/report-data/evidence.json">Evidence manifest and record chunks</a> · <a href="supplement.html">Supplementary measurements</a></p>'
    nav=[('abstract','Abstract'),('introduction','1 · Study design'),('methods','2 · Methods'),('main-results','3 · Quality and cost'),('timing-results','4 · Repeated timing'),('cache-results','5 · Prefix caching'),('data-quality','6 · Data quality'),('discussion','7 · Discussion'),('reproducibility','8 · Reproducibility'),('references','References'),('prompts','A · Prompts and schema'),('explorer','B · Every test case'),('requests','C · Every API call'),('appendices','D · Supporting evidence')]
    title='Compact decisions: quality, latency and cost'
    def speed_description(candidates):
        winners=[min(candidates,key=lambda m:timing['tasks'][t]['models'][m]['primary_case_median_latency_s']) for t in TASKS]
        if len(set(winners))!=1:return 'The fastest endpoint varied by task.'
        winner=winners[0]
        values=[timing['tasks'][t]['models'][winner]['primary_case_median_latency_s'] for t in TASKS]
        return f"{winner} had the lowest task-median latency across all five tasks ({n(min(values))}–{n(max(values))} s)."
    abstract_results=speed_description(models)+' Among chat models, '+speed_description([m for m in models if m!='Jev'])
    for t in ('boolq','rte','wic','policy'):
        best=max(models,key=lambda m:tasks[t]['models'][m]['quality']['accuracy_all_attempts'])
        abstract_results+=f" {best} had the highest observed {TASKS[t]} accuracy ({pct(tasks[t]['models'][best]['quality']['accuracy_all_attempts'])})."
    best_probability=min(models,key=lambda m:tasks['probability']['models'][m]['quality']['mae_valid_only'])
    abstract_results+=f" {best_probability} had the lowest exact-probability MAE ({n(100*tasks['probability']['models'][best_probability]['quality']['mae_valid_only'],2)} percentage points)."
    if all(tasks[t]['models']['Luna']['cost']['usd_per_1000_attempts']>tasks[t]['models']['Jev']['cost']['usd_per_1000_attempts'] for t in ('boolq','rte','wic','policy')):
        abstract_results+=' Luna cost more than Jev on each short binary workload.'
    abstract_results+=' '+cache_note
    abstract=f'<div class="abstract" id="abstract"><h2>Abstract</h2><p><strong>Objective.</strong> Compare general language models constrained to Jev’s compact probability-of-yes output with the native decision service.</p><p><strong>Methods.</strong> {len(models)} eligible models ({esc(short)}) use the same probability-of-yes schema, with chat reasoning disabled, on 4,785 frozen cases: full BoolQ, RTE and WiC validation splits, 300 exact-policy cases and 300 exact-probability cases. A serial protocol repeats 60 cases three times per model. A four-block long-prefix protocol uses 96 counterfactual policy cases and records cache usage and every prime. Quality, speed and billed cost retain separate denominators.</p><p><strong>Results.</strong> {abstract_results}</p><p><strong>Interpretation.</strong> Performance depends on the task and input-reuse pattern.</p></div>'
    design=table(['Protocol','Cases','Measured calls','Excluded from score, retained in billing'],[['Quality',4785,4785*len(models),'Setup probes'],['Serial timing','60 ×3 repeats',timing['measured_attempts'],str(timing['warmup_attempts'])+' warmups'],['Long-prefix decisions',96,cache['measured_attempts'],str(cache['prime_attempts'])+' primes']])
    page='<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+title+'</title><style>'+(OLD/'style.css').read_text()+'</style></head><body><div class="page"><aside class="toc"><nav>'+''.join(f'<a href="#{i}">{t}</a>' for i,t in nav)+'</nav><div class="nav-small"><a href="benchmark.zip" download>Download ZIP ↗</a></div></aside><main class="paper"><header><h1>'+title+'</h1></header>'+abstract+section('introduction','1 · Study design',design)+section('methods','2 · Methods',methods)+section('main-results','3 · Benchmark quality and cost',results)+section('timing-results','4 · Repeated, isolated timing',timinghtml)+section('cache-results','5 · Four-block prompt-caching experiment',cachehtml)+'<section id="data-quality">'+dq+'</section>'+section('discussion','7 · Discussion and limits',discussion)+section('reproducibility','8 · Reproducibility and complete accounting',repro)+'<section id="references">'+refs+'</section>'+section('prompts','A · Exact prompts and schema',prompts)+explorer+section('appendices','D · Supporting evidence',appendix)+'</main></div><script id="evidence" type="application/json">'+json.dumps(evidence,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c')+'</script><script>'+js+'</script></body></html>'
    if re.search(r'@@[A-Z]+@@',page):raise ValueError('Unfilled template placeholder')
    (destination/'comparison.html').write_text(page)
    (ROOT/'report-metadata.json' if destination==OUT else destination/'report-metadata.json').write_text(json.dumps({'models':models,'quality_cases':4785,'quality_attempts':len(quality),'all_attempts':len(allrows),'known_charge_subtotal_usd':known,'missing_bills':missing},indent=2))
    print(json.dumps({'models':models,'all_attempts':len(allrows),'output':str(destination/'comparison.html')}))

if __name__=='__main__':build()
