#!/usr/bin/env python3
"""Build the paper-style report from saved evidence; makes no network/model calls."""
from pathlib import Path
import json, html, statistics, hashlib, csv, sys
ROOT = Path(__file__).resolve().parent
OUT = ROOT.parent
sys.path.insert(0, str(ROOT))
from runner import SYSTEM, SCHEMA
M=ROOT/'runs/20261002T084223Z'; C=ROOT/'runs/cache-20261002T084958Z'
def read(p): return json.loads(p.read_text())
def lines(p): return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
def esc(v): return html.escape(str(v), quote=True)
def js(v): return json.dumps(v,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
def fmt(x,n=4): return f'{x:.{n}f}'
def table(headers,rows,caption='',cls=''):
 return '<div class="table-wrap"><table class="'+cls+'">'+('<caption>'+caption+'</caption>' if caption else '')+'<thead><tr>'+''.join('<th scope="col">'+h+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+str(v)+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table></div>'
def details(title,text,pre=True): return '<details><summary>'+title+'</summary>'+('<pre>'+esc(text)+'</pre>' if pre else text)+'</details>'
def fig(name,caption,alt):
 return f'<figure><img src="benchmark/figures/{name}.svg" alt="{esc(alt)}" loading="lazy"><figcaption>{caption} <span class="figure-download"><a href="benchmark/figures/{name}.svg" download>SVG</a> · <a href="benchmark/figures/{name}.png" download>PNG</a></span></figcaption></figure>'
s=read(M/'summary.json'); c=read(C/'summary.json'); manifest=read(ROOT/'data/manifest.json')
main=lines(M/'responses.jsonl'); cache=lines(C/'responses.jsonl'); setup=lines(ROOT/'runs/setup-probes.jsonl')
cases=lines(ROOT/'data/cases.jsonl')+lines(C/'cases.jsonl')
records=[]
for run,rs in [('main',main),('cache',cache),('setup',setup)]:
 for r in rs:
  r={**r,'run':run,'record_index':len(records)}
  records.append(r)
measured=[r for r in records if r['phase']=='measured']
# Parse the full source audit, preserving all findings.
audit=[]
for line in (ROOT/'data/label-audit.md').read_text().splitlines():
 if line.startswith('| boolq-dev-'):
  cols=[v.strip() for v in line.strip('|').split('|')]
  audit.append(dict(zip(['id','label','category','evidence'],cols)))
audit_by={a['id']:a for a in audit}
for case in cases:
 case['records']=[r['record_index'] for r in measured if r['case_id']==case['id']]
 case['audit']=audit_by.get(case['id'])
 case['family']=case.get('source',{}).get('rule_family') or case.get('source',{}).get('family') or case.get('source',{}).get('probability_family') or case['experiment']
# Single data payload includes every field of every recorded call. Authorization headers were never logged.
data={'cases':cases,'records':records,'audit':audit,'main':s,'cache':c,'manifest':manifest}
(ROOT/'paper/evidence.json').write_text(json.dumps(data,ensure_ascii=False,indent=2))
# Flat machine-readable export, with the complete JSON also available.
fields=['run','phase','sequence','case_id','experiment','model_label','requested_model','response_model','provider','started_at','target_kind','target','probability','valid','latency_s','cost_usd','generation_id']
with (ROOT/'paper/requests.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore'); w.writeheader();w.writerows(records)
quality=[];timing=[];intervals=[];tokens=[]
labels={'policy':'Simple policies','boolq':'BoolQ reading','probability':'Exact probabilities'}
for task in ['policy','boolq','probability']:
 ex=s['experiments'][task]
 for model in ['Jev','Luna']:
  v=ex['models'][model]; q=v['quality'];lt=v['valid_response_latency_s'];bill=v['cost']
  quality.append([labels[task],model,'60',f"{q['correct_count']}/60 ({q['accuracy']:.1%})" if task!='probability' else '—',fmt(q['brier']) if 'brier'in q else '—',fmt(q['log_loss']) if 'log_loss'in q else '—',f"{100*q['mae']:.3f}" if 'mae'in q else '—',f"{100*q['rmse']:.3f}" if 'rmse'in q else '—'])
  timing.append([labels[task],model,fmt(lt['p50'],3),fmt(lt['p95'],3),fmt(lt['mean'],3),f"${bill['total_usd']:.9f}",f"${bill['per_1000_requests_usd']:.6f}"])
  rs=[r for r in main if r['phase']=='measured' and r['experiment']==task and r['model_label']==model]
  inp=[r['usage'].get('prompt_tokens',r['usage'].get('input_tokens')) for r in rs];out=[r['usage'].get('completion_tokens',r['usage'].get('output_tokens')) for r in rs]
  tokens.append([labels[task],model,fmt(statistics.mean(inp),1),f'{min(inp)}–{max(inp)}',fmt(statistics.mean(out),1),str(sum(inp)),str(sum(out))])
 for metric, v in ex['paired']['quality_differences'].items():
  pct=metric in ('accuracy','mae'); fac=100 if pct else 1
  intervals.append([labels[task],{'accuracy':'Accuracy (pp)','brier':'Brier','log_loss':'Log loss','mae':'MAE (pp)','excess_brier':'Excess Brier','expected_brier':'Expected Brier'}[metric],fmt(v['estimate']*fac),f"[{v['ci95'][0]*fac:.4f}, {v['ci95'][1]*fac:.4f}]",'Positive' if metric=='accuracy' else 'Negative'])
 v=ex['paired']['latency_median_ratio']
 intervals.append([labels[task],'Ratio of median times',fmt(v['estimate'],3),f"[{v['ci95'][0]:.3f}, {v['ci95'][1]:.3f}]",'Below 1'])
qa_table=table(['Task','Model','n','Accuracy / label agreement','Brier ↓','Log loss ↓','MAE (pp) ↓','RMSE (pp) ↓'],quality,'Table 2. Quality by task. A dash denotes a metric that does not apply; pp = percentage points.')
lat_table=table(['Task','Model','Median (s)','p95 (s)','Mean (s)','Billed USD','USD / 1,000'],timing,'Table 3. Complete-response latency and observed billing. Each row contains 60 measured calls.')
ci_table=table(['Task','Metric','Estimate','95% paired interval','Favours Luna'],intervals,'Table 4. Paired contrasts: Luna − Jev for quality; Luna / Jev for time. Original 2,000-draw analysis.')
cache_rows=[]
for label in ['Jev','Luna uncached','Luna cached']:
 v=c['arms'][label]
 cache_rows.append([label,f"{round(v['accuracy_including_invalid_as_wrong']*24)}/24 ({v['accuracy_including_invalid_as_wrong']:.1%})",fmt(v['brier_valid_only']),fmt(v['median_latency_s'],3),fmt(v['p95_latency_s'],3),f"${v['total_cost_usd']:.9f}",f"${1000*v['mean_cost_usd']:.6f}"])
cache_table=table(['Arm','Accuracy','Brier ↓','Median (s)','p95 (s)','Billed USD','USD / 1,000'],cache_rows,'Table 5. Long-prefix experiment. Measured calls only; the extra cold prime is accounted for separately below.')
total=sum(r['cost_usd'] for r in records)
cost_table=table(['Phase','Calls','Billed USD','Included in quality / latency?'],[
 ['Main measured','360',f"${sum(r['cost_usd'] for r in main if r['phase']=='measured'):.9f}",'Yes, main results'],
 ['Main warmups','4',f"${sum(r['cost_usd'] for r in main if r['phase']=='warmup'):.9f}",'No'],
 ['Cache measured','72',f"${sum(r['cost_usd'] for r in cache if r['phase']=='measured'):.9f}",'Yes, cache results'],
 ['Cache prime','1',f"${c['cache_verification']['prime_cost_usd']:.9f}",'No; included in amortized cache cost'],
 ['Setup probes','4',f"${sum(r['cost_usd'] for r in setup):.9f}",'No'],
 ['<strong>Total</strong>','<strong>441</strong>',f'<strong>${total:.9f}</strong>','All 441 bills observed']],
 'Table 8. Complete cost ledger. Cost is reported usage.cost, not an invoice reconciliation.')
checks=[]
for label,p in [('Main',M/'verification.json'),('Cache',C/'verification.json')]:
 for name,passed in read(p)['checks'].items():checks.append([label,name.replace('_',' '),'Pass' if passed else 'Fail'])
# Exact recorded payloads, representative paired case; no editing of code or data.
example=next(case for case in cases if case['id']=='policy-001')
example_records=[r for r in records if r['phase']=='measured' and r['case_id']==example['id']]
payloads=''.join(details(f"{r['model_label']} — exact request · {r['endpoint']}",json.dumps(r['request'],ensure_ascii=False,indent=2)) for r in example_records)
cache_control=next(r for r in cache if r['phase']=='measured' and r['model_label']=='Luna cached')
cache_control_json={k:v for k,v in cache_control['request'].items() if k!='messages'}
cache_control_json['prefix_breakpoint']=cache_control['request']['messages'][1]['content'][0].get('prompt_cache_breakpoint')
asset_links=[]
for p in sorted(ROOT.rglob('*')):
 if not p.is_file() or '__pycache__' in p.parts or p.suffix in ('.pyc',):continue
 rel=p.relative_to(OUT)
 asset_links.append([f'<a href="{esc(rel)}" download>{esc(p.relative_to(ROOT))}</a>',f'{p.stat().st_size:,} B'])
assets=table(['Artifact','Size'],asset_links,'Complete experiment files. The bundle also contains this report, figures and its build source.')
aux=''
stats_path=ROOT/'paper/scientific-statistics.json'
if stats_path.exists():aux=details('Additional uncertainty analysis (JSON)',stats_path.read_text())
context={'QUALITY':qa_table,'LATENCY':lat_table,'INTERVALS':ci_table,'CACHE':cache_table,'COSTLEDGER':cost_table,'TOKENS':table(['Task','Model','Mean input','Input range','Mean output','Total input','Total output'],tokens,'Table 7. Reported token counts. Provider tokenization and native overhead differ.'),'AUDIT':table(['Case','Published','Assessment','Evidence / limitation'],[[a['id'],a['label'],a['category'].replace('_',' '),esc(a['evidence'])] for a in audit]),'CHECKS':table(['Run','Completed-run check','Result'],checks),'PAYLOADS':payloads,'SYSTEM':esc(SYSTEM),'SCHEMA':esc(json.dumps(SCHEMA,indent=2)),'RULEBOOK':esc((C/'rulebook.txt').read_text()),'CACHECONTROL':esc(json.dumps(cache_control_json,indent=2)),'MANIFEST':esc(json.dumps(manifest,indent=2)),'ASSETS':assets,'DATA':js(data),'AUXSTATS':aux,'TOTAL':f'{total:.9f}','FIG1':fig('fig1-latency','<strong>Figure 1.</strong> Empirical cumulative distributions of complete-response time. Each curve contains 60 requests. A curve further left indicates faster responses; the y-axis is the fraction completed by that time.','Main-run latency distributions for Jev and Luna on three tasks.'),'FIG2':fig('fig2-probability','<strong>Figure 2.</strong> Reported probabilities against exact event probabilities, plus error by problem family. The diagonal denotes perfect agreement with the oracle. Family error bars use a separate 10,000-draw paired case bootstrap (seed 20261002). These are probability calculations, not prospective forecasts.','Probability estimates compared with exact reference values and absolute error by family.'),'FIG3':fig('fig3-cache','<strong>Figure 3.</strong> Observed long-prefix costs and latency. The hatched allocation adds the entire extra priming request across 24 measured decisions. There is no primed latency observation for a hypothetical 1,000-call batch.','Cost and latency of Jev, Luna uncached and Luna cached, separating cache priming cost.')}
page=(ROOT/'paper/template.html').read_text()
for key,val in context.items(): page=page.replace('@@'+key+'@@',val)
import re
left=re.findall(r'@@[A-Z0-9]+@@',page)
if left:raise ValueError(left)
(OUT/'comparison.html').write_text(page)
print(json.dumps({'cases':len(cases),'measured_requests':len(measured),'all_calls':len(records),'audit_rows':len(audit),'total_cost_usd':total,'html_bytes':len(page.encode())}))
