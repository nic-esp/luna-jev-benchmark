#!/usr/bin/env python3
"""Scientific figures from a completed multi-model summary. No model calls.

Uses the existing report style and Matplotlib/NumPy environment. Input outcomes
are opened only after all supplied summary source runs are confirmed complete.
"""
from collections import defaultdict
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import statistics

ROOT=Path(__file__).resolve().parent
sp=importlib.util.spec_from_file_location('multi_paper_style',ROOT.parent/'paper_figures.py')
common=importlib.util.module_from_spec(sp);sp.loader.exec_module(common)
plt,np=common.plt,common.np
from matplotlib.ticker import PercentFormatter
from matplotlib.lines import Line2D

TASKS=('boolq','rte','wic','policy','probability')
NAMES={'boolq':'BoolQ','rte':'RTE','wic':'WiC','policy':'Synthetic policy','probability':'Exact probability'}
PALETTE={'Jev':common.NAVY,'Luna':common.ORANGE,'Gemini 3.8 Flash':'#26846A','GLM 5.3 Flash':'#8051A2','Qwen3.8 Flash':'#1687A0','DeepSeek V4.1 Flash':'#26846A','MiMo V2.6 Flash':'#8051A2','Hy4 Preview':'#B85E75'}
MARKERS=('o','s','D','^','P','X','v')


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read_rows(p):return [json.loads(s) for s in Path(p).read_text().splitlines() if s.strip()]
def color(model):
    return PALETTE.get(model.removesuffix(' uncached').removesuffix(' cached'),'#566573')


def validate(summary):
    if summary['quality']['missing_attempts']:
        raise ValueError('Do not render partial quality populations')
    for source in summary['quality_sources']:
        if source['metadata'].get('status')!='complete':raise ValueError('Quality source is not complete')
    timing=summary.get('timing')
    if timing and (timing['source']['metadata'].get('status')!='complete' or not timing['comparative_speed_eligible']):
        raise ValueError('Timing source does not support completed comparative measurements')
    for session in summary.get('cache_sessions',[]):
        if session['source']['metadata'].get('status')!='complete':raise ValueError('Cache source is not complete')


def quality_plot(s,out,manifest):
    tasks=[t for t in TASKS if t in s['tasks'] and s['tasks'][t]['target_kind']=='label'];models=s['models'];step=.75/max(len(models),1)
    fig,(ax,counts)=plt.subplots(1,2,figsize=(12.4,1.65+len(tasks)*max(1.05,len(models)*.28)),sharey=True,gridspec_kw={'width_ratios':[4.7,1.45]})
    fig.subplots_adjust(left=.145,right=.99,top=.81,bottom=.14,wspace=.08)
    ax.set_title('Binary-task accuracy with nominal Wilson 95% intervals',loc='left',y=1.16,fontweight='bold')
    numbers={}
    for ti,task in enumerate(tasks):
        numbers[task]={}
        for mi,model in enumerate(models):
            d=s['tasks'][task]['models'][model];q=d['quality'];v=q['accuracy_all_attempts'];ci=q['accuracy_all_attempts_wilson95'];y=ti+(mi-(len(models)-1)/2)*step
            if v is None:continue
            ax.errorbar(v,y,xerr=[[max(0,v-ci[0])],[max(0,ci[1]-v)]],fmt=MARKERS[mi%len(MARKERS)],color=color(model),markersize=6,capsize=2,elinewidth=1.3,clip_on=False)
            counts.text(0,y,f"{q['correct']:,}/{d['attempts']:,}",va='center',fontsize=8.7,color=color(model))
            counts.text(.7,y,f'{v:.1%}',va='center',fontsize=8.7,color=color(model))
            numbers[task][model]={'accuracy':v,'wilson95':ci,'correct':q['correct'],'attempts':d['attempts'],'invalid':d['invalid']}
    ax.set_yticks(range(len(tasks)),[NAMES[t] for t in tasks]);ax.set_ylim(len(tasks)-.42,-.58);ax.set_xlim(0,1);ax.xaxis.set_major_formatter(PercentFormatter(1,decimals=0));ax.set_xlabel('Correct / attempted cases')
    common.axis_base(ax,'x');ax.tick_params(axis='y',length=0);counts.set_xlim(0,1.15);counts.axis('off')
    counts.text(0,1.075,'Correct / attempts     Accuracy',transform=counts.transAxes,fontsize=8.5,color=common.GREY)
    legend=[Line2D([],[],marker=MARKERS[i%len(MARKERS)],color=color(m),linestyle='',label=m,markersize=6) for i,m in enumerate(models)]
    fig.legend(handles=legend,loc='upper left',bbox_to_anchor=(.13,.93),ncol=min(3,len(models)),columnspacing=1.6,handletextpad=.4)
    fig.text(.055,.035,'Invalid outputs count as wrong. Intervals are exploratory; use paired contrasts to compare models.',fontsize=9,color=common.GREY)
    manifest['metrics']['quality']=numbers
    common.save(fig,'quality',out,manifest,'Binary-task quality','Observed accuracy across attempted cases. Error bars show nominal Wilson 95% intervals, which do not incorporate passage clustering. Paired cluster contrasts are reported separately. Model quality observations may come from different collection windows.')


def probability_plot(s,rows,out,manifest):
    models=s['models'];columns=min(3,len(models));height=math.ceil(len(models)/columns)
    fig,axes=plt.subplots(height,columns,figsize=(4.15*columns,3.7*height+1),squeeze=False)
    fig.subplots_adjust(left=.075,right=.97,top=.87,bottom=.115,wspace=.32,hspace=.52)
    numbers={}
    for mi,(ax,model) in enumerate(zip(axes.flat,models)):
        selected=[r for r in rows if r.get('phase')=='quality' and r['experiment']=='probability' and r['model_label']==model]
        valid=[r for r in selected if r.get('valid') is True];d=s['tasks']['probability']['models'][model]
        if len(selected)!=d['attempts'] or len(valid)!=d['valid']:raise ValueError('Probability figure denominator mismatch')
        mae=statistics.mean(abs(r['probability']-r['target']) for r in valid) if valid else None
        expected=d['quality']['mae_valid_only']
        if mae is not None and not math.isclose(mae,expected,abs_tol=1e-12):raise ValueError('Probability figure score mismatch')
        ax.plot([0,1],[0,1],color=common.GREY,ls=(0,(3,3)),lw=1)
        ax.scatter([r['target'] for r in valid],[r['probability'] for r in valid],s=19,color=color(model),marker=MARKERS[mi%len(MARKERS)],alpha=.65,edgecolors='white',linewidths=.3,clip_on=False)
        ax.set_xlim(0,1);ax.set_ylim(0,1);ax.set_aspect('equal');ax.set_xticks([0,.5,1]);ax.set_yticks([0,.5,1]);ax.xaxis.set_major_formatter(PercentFormatter(1));ax.yaxis.set_major_formatter(PercentFormatter(1))
        ax.set_xlabel('Exact event probability');ax.set_ylabel('Returned probability');ax.set_title(model,loc='left',y=1.13,color=color(model))
        ax.text(0,1.035,f"{len(valid)}/{len(selected)} valid · MAE {mae*100:.2f} pp" if mae is not None else 'No valid outputs',transform=ax.transAxes,fontsize=9,color=common.GREY)
        common.axis_base(ax,'both');numbers[model]={'valid':len(valid),'attempts':len(selected),'mae':mae,'case_ids':[r['case_id'] for r in valid]}
    for ax in list(axes.flat)[len(models):]:ax.axis('off')
    fig.suptitle('Returned probabilities against exact mathematical targets',x=.055,y=.99,ha='left',fontweight='bold',fontsize=13)
    fig.text(.055,.035,'One point per valid answer; no jitter. Diagonal = exact agreement. This is arithmetic, not future-event calibration.',fontsize=9,color=common.GREY)
    manifest['metrics']['probability']=numbers
    common.save(fig,'probability',out,manifest,'Exact-probability estimates','Returned probabilities versus frozen mathematical targets. Each model uses its own valid-response denominator; invalid predictions remain in failure accounting. Points are not jittered. The paired MAE comparisons use common valid cases.')


def timing_plot(s,out,manifest):
    t=s['timing'];models=s['models'];tasks=[x for x in TASKS if x in t['tasks']];step=.75/max(1,len(models))
    fig,ax=plt.subplots(figsize=(12.4,1.8+len(tasks)*max(1,len(models)*.27)));fig.subplots_adjust(left=.16,right=.97,top=.80,bottom=.14)
    numbers={};maxvalue=0
    for ti,task in enumerate(tasks):
        numbers[task]={}
        for mi,model in enumerate(models):
            d=t['tasks'][task];v=d['models'][model]['primary_case_median_latency_s'];y=ti+(mi-(len(models)-1)/2)*step
            if v is None:continue
            maxvalue=max(maxvalue,v);ax.plot(v,y,MARKERS[mi%len(MARKERS)],color=color(model),markersize=6)
            ax.annotate(f'{v:.3f}',(v,y),xytext=(7,0),textcoords='offset points',va='center',fontsize=8,color=color(model))
            numbers[task][model]={'median_of_case_medians_s':v,'common_complete_cases':d['common_complete_cases']}
    ax.set_yticks(range(len(tasks)),[NAMES[t] for t in tasks]);ax.set_ylim(len(tasks)-.45,-.55);ax.set_xlim(0,maxvalue*1.18 if maxvalue else 1)
    ax.set_xlabel('Complete-response latency (seconds); median across within-case medians');common.axis_base(ax,'x');ax.tick_params(axis='y',length=0)
    ax.set_title('One interleaved serial run, common case support across every model',loc='left',y=1.18,fontweight='bold')
    legend=[Line2D([],[],marker=MARKERS[i%len(MARKERS)],color=color(m),linestyle='',label=m,markersize=6) for i,m in enumerate(models)]
    fig.legend(handles=legend,loc='upper left',bbox_to_anchor=(.145,.93),ncol=min(3,len(models)),columnspacing=1.6,handletextpad=.4)
    fig.text(.055,.035,'Three repeats per case; warmups excluded. These are point summaries. Paired bootstrap intervals are in the results tables.',fontsize=9,color=common.GREY)
    manifest['metrics']['timing']=numbers
    common.save(fig,'timing',out,manifest,'Interleaved complete-response latency','Median of three observations for each case and model, then median across the same complete cases for every model. Only the new serial interleaved session is shown; no old or concurrent bulk latencies are pooled. Point summaries do not imply cross-day precision.')


def cache_plot(session,index,out,manifest):
    arms=list(session['arms']);fig,(costax,timeax)=plt.subplots(1,2,figsize=(12.4,2.6+len(arms)*.53),gridspec_kw={'width_ratios':[1.35,1]})
    fig.subplots_adjust(left=.185,right=.98,top=.80,bottom=.20,wspace=.35);values={}
    for i,arm in enumerate(arms):
        d=session['arms'][arm];cost=d['cost']['usd_per_1000_attempts'];setup=d['setup_inclusive_usd_per_1000_decisions'];lat=d['latency']['median_s'];c=color(arm)
        if cost is not None:
            costax.barh(i,cost,height=.46,color=c,alpha=.88)
            if setup is not None and setup>cost:costax.barh(i,setup-cost,left=cost,height=.46,facecolor='white',edgecolor=c,hatch='////',linewidth=.8)
            costax.annotate(f'${setup if setup is not None else cost:.4f}',(setup if setup is not None else cost,i),xytext=(5,0),textcoords='offset points',va='center',fontsize=8)
        else:costax.text(0,i,'Bill incomplete',va='center',fontsize=9,color=common.GREY)
        if lat is not None:timeax.plot(lat,i,'o',color=c,markersize=6);timeax.annotate(f'{lat:.3f} s',(lat,i),xytext=(7,0),textcoords='offset points',va='center',fontsize=8)
        values[arm]={'measured_usd_per_1000':cost,'prime_inclusive_usd_per_1000':setup,'median_latency_s':lat,'attempts':d['attempts'],'valid':d['valid']}
    costax.set_yticks(range(len(arms)),arms);timeax.set_yticks(range(len(arms)),['']*len(arms))
    for ax in (costax,timeax):ax.set_ylim(len(arms)-.5,-.65);common.axis_base(ax,'x');ax.tick_params(axis='y',length=0);ax.set_xlim(left=0,right=ax.get_xlim()[1]*1.23)
    costax.set_xlabel('USD per 1,000 decisions');timeax.set_xlabel('Median complete-response time (seconds)')
    costax.set_title('A  Billed service cost',loc='left',y=1.07);timeax.set_title('B  Observed response time',loc='left',y=1.07)
    fig.suptitle('Shared long rulebook: controlled caching and other service baselines',x=.055,y=.985,ha='left',fontsize=13,fontweight='bold')
    fig.text(.055,.075,'Hatching allocates each model’s separate prime charges over its measured decisions. Unknown bills are not plotted as zero.',fontsize=9,color=common.GREY)
    fig.text(.055,.032,'Only explicitly controlled arms establish cache on/off comparisons; other models retain an uncontrolled cache state.',fontsize=9,color=common.GREY)
    manifest['metrics'][f'cache_session_{index}']=values
    common.save(fig,f'cache-{index+1}',out,manifest,'Long-prefix service costs and latency','Measured cost and latency within one cache session. Hatched increments allocate the separate primes to the measured cached decisions. Models without verified cache controls are uncontrolled service baselines. Session results and priming costs are not pooled across measurement dates.')


def generate(summary_path=ROOT/'summary.json',output_dir=ROOT/'figures'):
    path=Path(summary_path);s=json.loads(path.read_text());validate(s);out=Path(output_dir);out.mkdir(parents=True,exist_ok=True);common.style()
    unused=[c for c in ['#26846A','#8051A2','#1687A0','#A83D5A','#9B7D00','#547854','#486AA5'] if c not in {PALETTE[m] for m in s['models'] if m in PALETTE}]
    for model in s['models']:
        if model not in PALETTE:
            PALETTE[model]=unused.pop(0) if unused else '#566573'
    rows=[];sources={}
    for source in s['quality_sources']:
        p=Path(source['run_dir'])/'responses.jsonl'
        if sha(p)!=source['responses_sha256']:raise ValueError('Quality source changed after analysis')
        rows.extend(read_rows(p));sources[str(p)]=sha(p)
    manifest={'summary_sha256':sha(path),'models':s['models'],'quality_sources':sources,'figures':{},'metrics':{}}
    quality_plot(s,out,manifest);probability_plot(s,rows,out,manifest)
    if s.get('timing'):timing_plot(s,out,manifest)
    for i,c in enumerate(s.get('cache_sessions',[])):cache_plot(c,i,out,manifest)
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2,allow_nan=False)+'\n')
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--summary',type=Path,default=ROOT/'summary.json');p.add_argument('--output-dir',type=Path,default=ROOT/'figures');a=p.parse_args()
    m=generate(a.summary,a.output_dir);print(json.dumps({'figures':list(m['figures']),'directory':str(a.output_dir)}))
