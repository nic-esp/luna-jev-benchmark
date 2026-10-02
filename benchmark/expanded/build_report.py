#!/usr/bin/env python3
"""Render the study report and a complete paginated evidence explorer offline."""
from pathlib import Path
import json,html,re,statistics,collections,csv,math
ROOT=Path(__file__).resolve().parent; B=ROOT.parent; OUT=B.parent
RUN_NAMES={'expanded quality':'Benchmark quality','serial timing':'Serial timing','expanded cache':'Prefix caching','pilot main':'Supplementary quality','pilot cache':'Supplementary caching','pilot setup':'Setup checks'}
NAMES={'boolq':'BoolQ','rte':'RTE','wic':'WiC','policy':'Simple policies','probability':'Exact probabilities','expanded_cache_policy':'Long policy / cache'}
def read(p):return json.loads(Path(p).read_text())
def rows(p):return [json.loads(s) for s in Path(p).read_text().splitlines() if s.strip()]
def esc(x):return html.escape(str(x),quote=True)
def num(x,n=3):return f'{x:.{n}f}' if isinstance(x,(int,float)) else 'unknown'
def pct(x,n=1):return num(x*100,n)+'%' if x is not None else 'unknown'
def usd(x,n=6):return '$'+num(x,n) if x is not None else 'not fully reported'
def ci(v,scale=1,n=2):return f"{num(v['estimate']*scale,n)} [{num(v['ci95'][0]*scale,n)}, {num(v['ci95'][1]*scale,n)}]" if v.get('ci95') else num(v.get('estimate'),n)
def table(headers,records,caption=''):
 return '<div class="table-wrap"><table>'+('<caption>'+caption+'</caption>' if caption else '')+'<thead><tr>'+''.join('<th scope="col">'+h+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+str(v)+'</td>' for v in row)+'</tr>' for row in records)+'</tbody></table></div>'
def detail(title,text):return '<details><summary>'+title+'</summary><pre>'+esc(text)+'</pre></details>'
def fig(name,caption,alt):
 path=ROOT/'figures'/f'{name}.svg'
 if not path.exists():return ''
 return f'<figure><img src="benchmark/expanded/figures/{name}.svg" alt="{esc(alt)}" loading="lazy"><figcaption>{caption} <span class="figure-download"><a href="benchmark/expanded/figures/{name}.svg" download>SVG</a> · <a href="benchmark/expanded/figures/{name}.png" download>PNG</a></span></figcaption></figure>'
def inlinejson(x):return json.dumps(x,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c')
def latest(pattern):return sorted((ROOT/'runs').glob(pattern))[-1]

def build():
 qdir=latest('quality-*');tdir=latest('timing-*');cdir=latest('cache-*')
 s=read(ROOT/'summary.json');cache=read(cdir/'summary.json');cm=cache['metadata'];man=read(ROOT/'data/manifest.json')
 assert s['quality_run']['metadata']['status']=='complete' and s['quality_run']['missing_attempts']==0
 assert s['timing']['status']=='complete' and cm['status']=='complete'
 quality=rows(qdir/'responses.jsonl');timing=rows(tdir/'responses.jsonl');crows=rows(cdir/'responses.jsonl')
 all_records=[]
 for run,rr in [('expanded quality',quality),('serial timing',timing),('expanded cache',crows),('pilot main',rows(B/'runs/20261002T084223Z/responses.jsonl')),('pilot cache',rows(B/'runs/cache-20261002T084958Z/responses.jsonl')),('pilot setup',rows(B/'runs/setup-probes.jsonl'))]:
  for row in rr:all_records.append({**row,'run':run,'record_index':len(all_records)})
 cases=rows(ROOT/'data/cases.jsonl')+rows(cdir/'cases.jsonl')
 bycase=collections.defaultdict(list)
 for r in all_records:
  if r['run'].startswith('expanded') or r['run']=='serial timing':bycase[r['case_id']].append(r['record_index'])
 audit=[]
 for line in (B/'data/label-audit.md').read_text().splitlines():
  if line.startswith('| boolq-dev-'):
   a=[x.strip()for x in line.strip('|').split('|')];audit.append(dict(zip(['id','label','category','evidence'],a)))
 auditby={a['id']:a for a in audit}
 for c in cases:c['records']=bycase[c['id']];c['audit']=auditby.get(c['id'])
 dest=ROOT/'report-data';dest.mkdir(exist_ok=True)
 compact=[]
 for i in range(0,len(all_records),100):
  chunk=all_records[i:i+100];file=f'records-{i//100:03d}.json'
  (dest/file).write_text(json.dumps(chunk,ensure_ascii=False))
  for r in chunk:compact.append({k:v for k,v in r.items() if k not in ['request','response','usage']}|{'record_file':'benchmark/expanded/report-data/'+file})
 evidence={'cases':cases,'records':compact,'audit':audit,'case_count':len(cases),'call_count':len(all_records)}
 (dest/'evidence.json').write_text(json.dumps({'cases':cases,'records':all_records,'main_summary':s,'cache_summary':cache},ensure_ascii=False))
 fields=['run','phase','repeat','block','sequence','case_id','experiment','model_label','requested_model','response_model','provider','started_at','target_kind','target','probability','valid','latency_s','cost_usd','generation_id']
 with (dest/'requests.csv').open('w',newline='')as f:
  writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(all_records)
 tasks=s['tasks'];qtable=[];costtable=[];intervaltable=[];toktable=[]
 for task in ['boolq','rte','wic','policy','probability']:
  ex=tasks[task]
  for model in ['Jev','Luna']:
   v=ex['models'][model];q=v['quality'];cost=v['cost']
   if task!='probability':
    w=q['accuracy_all_attempts_wilson95'];qtable.append([NAMES[task],model,f"{v['valid']}/{v['attempts']}",f"{q['correct']}/{v['attempts']} · {pct(q['accuracy_all_attempts'])}",f'[{pct(w[0])}, {pct(w[1])}]',pct(q['balanced_accuracy_all_attempts']),num(q['brier_valid_only'],4),num(q['log_loss_valid_only'],4)])
   costtable.append([NAMES[task],model,v['attempts'],usd(cost['known_billed_usd'],9),cost['missing_bills'],usd(cost['usd_per_1000_attempts'])])
   rr=[r for r in quality if r['experiment']==task and r['model_label']==model and r['valid']]
   inp=[r['usage'].get('prompt_tokens',r['usage'].get('input_tokens'))for r in rr];out=[r['usage'].get('completion_tokens',r['usage'].get('output_tokens'))for r in rr]
   toktable.append([NAMES[task],model,len(rr),num(statistics.mean(inp),1),f'{min(inp)}–{max(inp)}',num(statistics.mean(out),1),sum(inp),sum(out)])
  for metric,v in ex['paired']['metrics'].items():
   name=metric.replace('_all_attempts_difference','').replace('_valid_pairs_difference','').replace('_',' ').title();scale=100 if metric.startswith(('accuracy','balanced','mae','rmse')) else 1
   intervaltable.append([NAMES[task],name+(' (pp)' if scale==100 else ''),ci(v,scale,3),ex['paired']['paired_cases'],ex['paired']['valid_response_pairs'],ex['paired']['resampling_clusters']])
 prob=tasks['probability']['models'];probtable=[]
 for model in ['Jev','Luna']:
  v=prob[model];p=v['quality'];probtable.append([model,f"{v['valid']}/{v['attempts']}",num(p['mae_valid_only']*100),num(p['rmse_valid_only']*100),num(p['excess_brier_valid_only'],6),num(p['expected_brier_valid_only'],6)])
 timingtable=[];blocktable=[]
 for task,d in s['timing']['tasks'].items():
  timingtable.append([NAMES[task],d['complete_valid_paired_cases'],num(d['primary_case_median_latency_s']['Jev']),num(d['primary_case_median_latency_s']['Luna']),ci(d['paired']['metrics']['ratio_of_case_medians'],1,3),num(d['by_model_all_attempts']['Jev']['p95_s']),num(d['by_model_all_attempts']['Luna']['p95_s'])])
  for block,v in d['repeat_blocks'].items():blocktable.append([NAMES[task],int(block)+1,num(v['Jev']['median_s']),num(v['Luna']['median_s']),num(v['ratio_of_medians_luna_over_jev'])])
 # Descriptive repeated answer variation, never pooled into main quality scores.
 consistent=[]
 for task in ['boolq','rte','wic','policy','probability']:
  for model in ['Jev','Luna']:
   grouped=collections.defaultdict(list)
   for r in timing:
    if r['phase']=='timing' and r['experiment']==task and r['model_label']==model:grouped[r['case_id']].append(r)
   valid=[rs for rs in grouped.values()if len(rs)==3 and all(r['valid']for r in rs)]
   yesno=sum(len({r['probability']>=.5 for r in rs})==1 for rs in valid)
   prange=statistics.mean(max(r['probability']for r in rs)-min(r['probability']for r in rs)for rs in valid)if valid else None
   consistent.append([NAMES[task],model,len(valid),f'{yesno}/{len(valid)}' if task!='probability' else 'Not a label task',num(prange,5)])
 ca=cache['arms'];cachetable=[];blockcache=[]
 for arm in ['Jev','Luna uncached','Luna cached']:
  v=ca[arm];cachetable.append([arm,f"{v['valid_n']}/{v['n']}",pct(v['accuracy_including_invalid_as_wrong']),num(v['brier_valid_only'],4),num(v['median_latency_s']),num(v['p95_latency_s']),usd(v['cost_per_1000_usd'])])
 for b in cache['blocks']:
  blockcache.append([b['block']+1,usd(b['prime']['cost_usd'],9),b['prime']['cache_write_tokens'],f"{b['arms']['Luna cached']['cache_hit_calls']}/24",b['stable_cached_prefix_token_count'],'0 / 0' if b['uncached_control_verified'] else 'Check failed',num(b['arms']['Jev']['median_latency_s']),num(b['arms']['Luna cached']['median_latency_s'])])
 cv=cache['cache_verification'];known=sum(r['cost_usd'] for r in all_records if r['cost_usd']is not None);missing=sum(r['cost_usd']is None for r in all_records);validsum=sum(r['valid']for r in all_records)
 ledger=[]
 for (run,phase),rr in sorted(_group(all_records,lambda r:(r['run'],r['phase'])).items()):ledger.append([RUN_NAMES[run],phase,len(rr),sum(r['valid']for r in rr),usd(sum(r['cost_usd']for r in rr if r['cost_usd']is not None),9),sum(r['cost_usd']is None for r in rr)])
 ledger.append(['<strong>All recorded calls</strong>','',len(all_records),validsum,usd(known,9),missing])
 overlap=[]
 for group,d in s['boolq_subgroups'].items():
  for model,v in d['models'].items():overlap.append([group.replace('pilot60','60 overlapping rows (new primary calls)').replace('remaining3210','3,210 other rows'),model,f"{v['quality']['correct']}/{v['attempts']}",pct(v['quality']['accuracy_all_attempts']),num(v['quality']['brier_valid_only'],4)])
 carbon=[r for r in quality if r['case_id']=='boolq-dev-1842'];carbontext='Primary benchmark responses: '+', '.join(r['model_label']+' '+('invalid'if not r['valid']else('yes'if r['probability']>=.5 else'no'))+' (p='+num(r['probability'],3)+')' for r in carbon)+'.'
 sensitivity=[]
 for model in ['Jev','Luna']:
  rr=[r for r in quality if r['experiment']=='boolq' and r['model_label']==model and r['case_id']!='boolq-dev-1842'];k=sum(r['valid']and(r['probability']>=.5)==bool(r['target'])for r in rr);sensitivity.append([model,f'{k}/{len(rr)}',pct(k/len(rr))])
 system=next(r['request']['messages'][0]['content']for r in quality if r['model_label']=='Luna');schema=next(r['request']['response_format']['json_schema']['schema']for r in quality if r['model_label']=='Luna')
 payloads=''
 for task in ['boolq','rte','wic','policy','probability']:
  case=next(c for c in cases if c['experiment']==task)
  for r in quality:
   if r['case_id']==case['id']:payloads+=detail(NAMES[task]+' · '+r['model_label']+' exact request',json.dumps(r['request'],ensure_ascii=False,indent=2))
 modelid='Returned model/provider combinations were '+', '.join('<code>'+esc(model)+' / '+esc(provider)+'</code>'for model,provider in sorted({(r['response_model'],r['provider'])for r in quality if r['valid']}))+'. Luna’s returned identifier has no dated suffix.'
 qtext='<p>'+ ' '.join(f"On {NAMES[t]}, Jev agreed with {pct(tasks[t]['models']['Jev']['quality']['accuracy_all_attempts'])} of published labels and Luna with {pct(tasks[t]['models']['Luna']['quality']['accuracy_all_attempts'])}."for t in ['boolq','rte','wic'])+'</p>'
 qtext+='<p>Simple-policy accuracy was '+pct(tasks['policy']['models']['Jev']['quality']['accuracy_all_attempts'])+' for Jev and '+pct(tasks['policy']['models']['Luna']['quality']['accuracy_all_attempts'])+' for Luna. Scores describe the frozen reference tasks, with validity and proper-score denominators retained.</p>'
 ptext=f"<p>The valid-response MAE was {num(prob['Jev']['quality']['mae_valid_only']*100)} percentage points for Jev and {num(prob['Luna']['quality']['mae_valid_only']*100)} for Luna. The paired MAE difference was {ci(tasks['probability']['paired']['metrics']['mae_valid_pairs_difference'],100,3)} points. This contrast uses {tasks['probability']['paired']['valid_response_pairs']} cases valid for both models.</p>"
 ratios=[d['paired']['metrics']['ratio_of_case_medians']['estimate']for d in s['timing']['tasks'].values()]
 timetext=f'<p>Across tasks, the ratio of Luna to Jev case-median latency ranged from {min(ratios):.2f} to {max(ratios):.2f}. The intervals resample the 12 paired cases per task; they do not estimate variation across days or providers. p95 columns summarize all measured serial calls for that task and model, not the within-case medians.</p>'
 saveoff=1-ca['Luna cached']['cost']['total_usd']/ca['Luna uncached']['cost']['total_usd'];savej=1-cv['setup_inclusive_cached_cost_usd']/ca['Jev']['cost']['total_usd']
 cachetext=f"<p>After priming, cached Luna cost {pct(saveoff,2)} less than uncached Luna. With all four extra primes allocated across the 96 cached decisions, it cost {pct(savej,2)} less than Jev. These savings are calculated from observed charges in the same long-input experiment.</p><p>Accuracy was {pct(ca['Jev']['accuracy_including_invalid_as_wrong'])} for Jev, {pct(ca['Luna uncached']['accuracy_including_invalid_as_wrong'])} for uncached Luna and {pct(ca['Luna cached']['accuracy_including_invalid_as_wrong'])} for cached Luna. Cached and uncached Luna agreed on {round(cache['paired']['Luna cached / Luna uncached']['label_agreement']*96)}/96 binary answers.</p>"
 abstract='; '.join(NAMES[t]+': '+pct(tasks[t]['models']['Jev']['quality']['accuracy_all_attempts'])+' Jev, '+pct(tasks[t]['models']['Luna']['quality']['accuracy_all_attempts'])+' Luna'for t in ['boolq','rte','wic'])+'. Probability MAE was '+num(prob['Jev']['quality']['mae_valid_only']*100,2)+' versus '+num(prob['Luna']['quality']['mae_valid_only']*100,2)+f' percentage points. Serial Luna/Jev latency ratios were {min(ratios):.2f}–{max(ratios):.2f}. Cached Luna was {pct(saveoff)} cheaper than uncached Luna after priming and {pct(savej)} cheaper than Jev including the four primes.'
 interpretation='The results quantify task-specific quality and cost trade-offs. '
 if min(ratios)>1:interpretation+='Jev was faster in the isolated timing protocol. '
 interpretation+='Jev had higher published-label agreement on BoolQ; the RTE and WiC accuracy intervals included no difference. Luna had lower error on exact synthetic probabilities. Verified prefix reuse reduced Luna’s cost on the long-rulebook workload.'
 failures=[r for r in quality if not r['valid']]
 failtext=f"<p>The quality protocol retained {len(failures)} failed request(s). "
 failtext+=' '.join(esc(r['model_label'])+' '+esc(r['case_id'])+': HTTP '+str(r.get('http_status','unknown'))+'.'for r in failures)
 failtext+=' The first unreported bill paused the run. A documented continuation processed only unattempted case/model pairs, keeping all failed attempts in the dataset. A $0.01 allowance for each unreported bill was used only by the budget guard; it is not an observed or estimated API charge.</p>'
 assets=[]
 for p in sorted(B.rglob('*')):
  if p.is_file() and '__pycache__'not in p.parts and '/report-data/records-'not in str(p):assets.append([f'<a href="{esc(p.relative_to(OUT))}" download>{esc(p.relative_to(B))}</a>',f'{p.stat().st_size:,} B'])
 verify_path=ROOT/'verification.json';verify=detail('Completed-run checks',verify_path.read_text())if verify_path.exists()else'<p>Verification file pending.</p>'
 for p in [ROOT/'TESTS.txt',B/'TESTS.txt']:
  if p.exists():verify+=detail(str(p.relative_to(B)),p.read_text())
 cacheanalysis=cdir/'clustered-analysis.json';cstats='<p>See the per-block results and raw paired records.</p>'
 if cacheanalysis.exists():
  cx=read(cacheanalysis); cr=[]
  for comparison,d in cx['comparisons'].items():
   for metric,v in d['metrics'].items():
    scale=100 if metric in ['accuracy_difference','label_agreement'] else 1
    cr.append([comparison,metric.replace('_',' ').title()+(' (pp)' if metric=='accuracy_difference' else ' (%)' if metric=='label_agreement' else ''),ci(v,scale,4)])
  cstats='<p>Differences subtract the second arm from the first; ratios divide the first by the second. Intervals use 5,000 resamples of counterfactual pairs within each fixed block. Costs below exclude the four primes; their allocation is shown above.</p>'+table(['Comparison','Metric','Estimate [95% interval]'],cr)
  cstats+='<h4>Both counterfactual cases correct</h4>'+table(['Arm','Complete correct pairs','Fraction'],[[arm,str(v['both_cases_correct'])+'/'+str(v['pairs']),pct(v['fraction'])]for arm,v in cx['counterfactual_pair_accuracy'].items()])
  cstats+=detail('Full clustered analysis',cacheanalysis.read_text())
 datasettable=table(['Task','Source / construction','Cases','No / yes','Reference'],[['BoolQ','Full development split',3270,'1,237 / 2,033','Published labels'],['RTE','Full SuperGLUE validation split',277,'131 / 146','Published entailment labels'],['WiC','Full SuperGLUE validation split',638,'319 / 319','Published same-sense labels'],['Policies','Three synthetic rule families',300,'150 / 150','Exact Boolean oracle'],['Probabilities','Three exact sampling mechanisms',300,'Not binary targets','Exact rational q']], 'Table 2. Quality datasets. No performance-based filtering is applied.')
 context={'CSS':(ROOT/'style.css').read_text(),'WINDOW':min(r['started_at']for r in all_records)[11:16]+'–'+cm['finished_at'][11:16],'ABSTRACTRESULTS':abstract,'ABSTRACTINTERPRETATION':interpretation,'MODELIDENTITY':modelid,'DATASETTABLE':datasettable,'QUALITYTABLE':table(['Task','Model','Valid / attempts','Correct / attempts','Wilson 95%','Balanced acc.','Brier ↓','Log loss ↓'],qtable,'Table 3. Accuracy and published-label agreement. Proper scores use valid responses.'),'QUALITYTEXT':qtext,'PROBTABLE':table(['Model','Valid / attempts','MAE (pp) ↓','RMSE (pp) ↓','Excess Brier ↓','Expected Brier ↓'],probtable,'Table 4. Exact-probability scores. Every q is known analytically.'),'PROBTEXT':ptext,'INTERVALTABLE':table(['Task','Luna − Jev metric','Estimate [95% interval]','Pairs','Valid pairs','Clusters'],intervaltable,'Table 5. Paired contrasts, 5,000 resamples. Exact duplicate BoolQ passages are clustered.'),'COSTTABLE':table(['Task','Model','Attempts','Known billed subtotal','Missing bills','USD / 1,000'],costtable,'Table 6. Quality-run costs. Missing billing remains unknown; full projections are withheld when incomplete.'),'TIMINGTABLE':table(['Task','Complete paired cases','Jev (s)','Luna (s)','Luna / Jev [95%]','Jev p95 (s)','Luna p95 (s)'],timingtable,'Table 7. Median across within-case medians, plus p95 of all serial calls. 12 cases ×3 repeats per task/model.'),'TIMINGBLOCKS':table(['Task','Repeat block','Jev median (s)','Luna median (s)','Ratio'],blocktable),'TIMINGTEXT':timetext,'CONSISTENCY':table(['Task','Model','Complete cases','Same label across 3','Mean probability range'],consistent),'CACHEVERIFICATION':f"<p><strong>{cv['cold_primed_blocks']}/4 cold primes, {cv['blocks_with_all_24_cache_hits']}/4 blocks with every cached request hitting, and {cv['blocks_with_verified_uncached_control']}/4 clean uncached controls</strong> were verified from returned usage. Jev did not expose equivalent cache counters; its cache state is unknown.</p>",'CACHEBLOCKTABLE':table(['Block','Prime bill','Tokens written','Hits','Tokens read / hit','Off reads / writes','Jev median (s)','Cached median (s)'],blockcache,'Table 8. Cache treatment verification by block.'),'CACHETABLE':table(['Arm','Valid / attempts','Accuracy','Brier ↓','Median (s)','p95 (s)','USD / 1,000'],cachetable,'Table 9. Measured cache-study calls, excluding the four extra primes.'),'CACHETEXT':cachetext,'CACHEAMORTIZATION':f"<strong>All four primes included</strong><div class='formula'>{usd(ca['Luna cached']['cost']['total_usd'],9)} measured + {usd(cv['priming_cost']['total_usd'],9)} priming<br>= <strong>{usd(cv['setup_inclusive_cached_cost_usd'],9)}</strong> for 96 cached decisions<br>= <strong>{usd(cv['setup_inclusive_cached_cost_per_1000_usd'])} per 1,000 decisions</strong> at 24 reuses per prefix.</div>",'CACHESTATS':cstats,'CARBON':carbontext,'SENSITIVITY':table(['Model','Correct / remaining','Label agreement'],sensitivity,'Post-hoc sensitivity: exclude only known contradictory BoolQ row 1842. Primary scores remain unchanged.'),'OVERLAPTABLE':table(['BoolQ subgroup','Model','Correct / cases','Label agreement','Brier'],overlap),'CLUSTERS':str(tasks['boolq']['paired']['resampling_clusters']),'AUDIT':table(['Case','Published','Assessment','Evidence'],[[a['id'],a['label'],a['category'].replace('_',' '),esc(a['evidence'])]for a in audit]),'DISCUSSION':'<p>'+interpretation+'</p><p>The study reports quality for each task. Pricing depends on actual input length, returned output and prefix reuse. Those workload choices should remain explicit when using the results to select a service.</p>','CONCLUSION':'<p>'+interpretation+' A practical choice should jointly consider the relevant task’s errors, required latency and the expected frequency of prefix reuse.</p>','ACCOUNTING':f'<p>The study, including supplementary measurements and setup checks, recorded <strong>{len(all_records):,} API attempts</strong>, of which <strong>{validsum:,}</strong> returned valid probabilities. Known reported charges sum to <strong>{usd(known,9)}</strong>; <strong>{missing}</strong> attempt(s) lack a billing value. The exact all-call bill is therefore unknown.</p>'+failtext,'LEDGER':table(['Run','Phase','Calls','Valid','Known subtotal','Missing bills'],ledger,'Table 10. Every recorded call, including supplementary measurements, warmups, primes and failures.'),'VERIFICATION':verify,'SYSTEM':esc(system),'SCHEMA':esc(json.dumps(schema,indent=2)),'PAYLOADS':payloads,'RULEBOOK':esc((cdir/'rulebook.txt').read_text()),'MANIFEST':esc(json.dumps(man,indent=2)),'SUMMARY':esc(json.dumps(s,indent=2)),'PRICING':esc((B/'data/pricing.json').read_text()),'ASSETS':table(['File','Size'],assets),'TOKENSTABLE':table(['Task','Model','Valid n','Mean input','Input range','Mean output','Input total','Output total'],toktable,'Reported token counts for valid quality responses; no missing count is imputed.'),'EVIDENCE':inlinejson(evidence),'EXPLORER':(ROOT/'explorer.html').read_text(),'JAVASCRIPT':(ROOT/'explorer.js').read_text()}
 lunar=[r for r in all_records if r['valid'] and r['model_label']!='Jev']
 jevr=[r for r in all_records if r['valid'] and r['model_label']=='Jev']
 lt=[r['usage']['completion_tokens']for r in lunar];jt=[r['usage']['output_tokens']for r in jevr]
 context['OUTPUTAUDIT']=f'<div class="note"><strong>Output restriction audit.</strong> All {len(lunar):,} valid Luna responses returned only the exact two-field Noul object, with zero reported reasoning tokens. The output limit was 128 tokens; actual usage was {min(lt)}–{max(lt)} tokens. Jev reported {min(jt)}–{max(jt)} output tokens. The contract matches the answer schema; it does not force identical decimal precision or identical token counts across services.</div>'
 context.update(QUALITYFIG=fig('fig4-quality','<strong>Figure 1.</strong> Binary-task accuracy with nominal Wilson 95% intervals. BoolQ, RTE and WiC use published labels; simple policies use exact rules. Invalid responses count as wrong.','Jev and Luna accuracy on the four binary tasks.'),PROBFIG=fig('fig5-probability','<strong>Figure 2.</strong> Probability estimates versus exact targets. Invalid predictions are omitted and their count is reported in Table 4.','Probability predictions and exact event probabilities.'),TIMINGFIG=fig('fig6-timing','<strong>Figure 3.</strong> Serial latency from three repeats, reduced to per-case medians.','Isolated repeated complete-response latency.'),CACHEFIG=fig('fig7-cache','<strong>Figure 4.</strong> Four-block cache costs and latency, with the extra prime allocation shown separately.','Cache cost and latency with cold primes.'))
 page=(ROOT/'template.html').read_text()
 for k,v in context.items():page=page.replace('@@'+k+'@@',str(v))
 leftover=re.findall(r'@@[A-Z]+@@',page)
 if leftover:raise RuntimeError(leftover)
 (OUT/'comparison.html').write_text(page)
 reportmeta={'all_attempts':len(all_records),'expanded_cases':len(cases),'known_cost_usd':known,'missing_bills':missing,'quality_run':str(qdir.relative_to(OUT)),'timing_run':str(tdir.relative_to(OUT)),'cache_run':str(cdir.relative_to(OUT)),'public_note':'Local complete evidence; publication package may separate restricted third-party source text.'}
 (dest/'report-metadata.json').write_text(json.dumps(reportmeta,indent=2))
 print(json.dumps(reportmeta))

def _group(rows,key):
 out=collections.defaultdict(list)
 for r in rows:out[key(r)].append(r)
 return out
if __name__=='__main__':build()
