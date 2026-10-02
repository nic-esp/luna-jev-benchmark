#!/usr/bin/env python3
"""Offline analysis of frozen multi-model observations; never makes model calls."""
from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import random
import statistics

ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT.parent / 'expanded/analyze.py'
_spec = importlib.util.spec_from_file_location('frozen_two_model_metrics', BASE_PATH)
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)
DEFAULT_MODELS = ('Jev', 'Luna', 'Gemini 3.8 Flash', 'GLM 5.3 Flash', 'Qwen3.8 Flash')
READ = base.read_jsonl


def comparison_pairs(models):
    """Each new model against both references; Luna against Jev once."""
    return [(model, ref) for model in models for ref in ('Jev', 'Luna')
            if ref in models and model != ref and not (model == 'Jev' and ref == 'Luna')]


def load_run(path):
    path = Path(path)
    meta = json.loads((path / 'metadata.json').read_text())
    if meta.get('status') in ('running', 'preparing', 'pending'):
        raise ValueError(f'Cannot analyze active run: {path}')
    rows = READ(path / 'responses.jsonl')
    return rows, {'run_dir': str(path.resolve()), 'metadata': meta,
                  'responses_sha256': base.digest(path / 'responses.jsonl'),
                  'attempts': len(rows), 'cost_all_attempts': base.costs(rows)}


def check_source(rows, cases, phase, models=None):
    seen = set()
    for r in rows:
        if r.get('phase') != phase:
            continue
        identity = (r['case_id'], r['model_label'], r.get('repeat', 0))
        if identity in seen:
            raise ValueError(f'Duplicate observation: {identity}')
        seen.add(identity)
        if models and r['model_label'] not in models:
            raise ValueError(f'Unexpected model: {r["model_label"]}')
        if r['case_id'] not in cases:
            raise ValueError(f'Unknown case: {r["case_id"]}')
        c = cases[r['case_id']]
        if any(r.get(k) != c[k] for k in ('experiment', 'target', 'target_kind')):
            raise ValueError(f'Source target/task mismatch: {r["case_id"]}')
        if r.get('valid') is True and not base.valid(r):
            raise ValueError('A response marked valid has an invalid probability')
        if not r.get('valid') and r.get('probability') is not None:
            raise ValueError('Invalid response contains a scored probability')
        if r.get('cost_usd') is not None and (not base.finite(r['cost_usd']) or r['cost_usd'] < 0):
            raise ValueError('Invalid billed cost')
        if phase == 'quality' and r.get('repeat', 0) != 0:
            raise ValueError('Quality results must contain one repeat only')
    return seen


def paired_quality(rows, cases, kind, model, reference, draws, seed):
    """Reuse the frozen scoring implementation with explicit directional labels."""
    mapped = [dict(r, model_label='Luna' if r['model_label'] == model else 'Jev')
              for r in rows if r['model_label'] in (model, reference)]
    out = base.paired_quality(mapped, cases, kind, draws, seed)
    out.update(model=model, reference=reference, direction=f'{model} minus {reference}')
    if 'correctness_pairs' in out:
        cp = out['correctness_pairs']
        out['correctness_pairs'] = {'both_correct': cp['both_correct'], 'model_only_correct': cp['luna_only_correct'],
                                    'reference_only_correct': cp['jev_only_correct'], 'both_incorrect': cp['both_incorrect']}
    return out


def distribution(values):
    return {'n': len(values), 'median_s': base.quantile(values, .5), 'p95_s': base.quantile(values, .95),
            'mean_s': base.average(values)}


def latency_pair(pairs, model, reference, draws, seed):
    def metric(ps):
        if not ps:
            return {k: None for k in ('ratio_of_case_medians', 'median_of_case_ratios', 'mean_case_latency_difference_s')}
        return {'ratio_of_case_medians': statistics.median(a for a,b in ps) / statistics.median(b for a,b in ps),
                'median_of_case_ratios': statistics.median(a / b for a,b in ps),
                'mean_case_latency_difference_s': base.average([a - b for a,b in ps])}
    point = metric(pairs); samples = {k: [] for k in point}; rng = random.Random(seed)
    if len(pairs) >= 2:
        for _ in range(draws):
            for k,v in metric(rng.choices(pairs, k=len(pairs))).items():
                samples[k].append(v)
    return {'model': model, 'reference': reference, 'paired_cases': len(pairs),
            'direction': f'{model} minus {reference}; ratios {model} divided by {reference}',
            'metrics': {k: {'estimate': v, 'ci95': base.interval(samples[k]), 'defined_bootstrap_draws': len(samples[k])} for k,v in point.items()}}


def timing_analysis(rows, cases, models, source, draws, seed):
    selected = [r for r in rows if r.get('phase') == 'timing']
    check_source(selected, cases, 'timing', models)
    if any(r.get('repeat') not in (0,1,2) for r in selected):
        raise ValueError('Timing repeat must be 0, 1 or 2')
    tasks = {}
    for ti, task in enumerate(sorted({c['experiment'] for c in cases.values()})):
        rs = [r for r in selected if r['experiment'] == task]
        bycase = defaultdict(lambda: defaultdict(dict))
        for r in rs:
            bycase[r['case_id']][r['model_label']][r['repeat']] = r
        complete = {m: {} for m in models}
        for cid, arms in bycase.items():
            for m in models:
                rr = arms[m]
                if set(rr) == {0,1,2} and all(base.valid(r) and base.finite(r.get('latency_s')) and r['latency_s'] > 0 for r in rr.values()):
                    complete[m][cid] = statistics.median(r['latency_s'] for r in rr.values())
        common = set.intersection(*(set(complete[m]) for m in models)) if models else set()
        contrasts = {}; sensitivities = {}
        for pi, (model, ref) in enumerate(comparison_pairs(models)):
            key = f'{model} / {ref}'
            contrasts[key] = latency_pair([(complete[model][c], complete[ref][c]) for c in sorted(common)], model, ref, draws, seed + ti*100 + pi)
            pairids = set(complete[model]) & set(complete[ref])
            sensitivities[key] = latency_pair([(complete[model][c], complete[ref][c]) for c in sorted(pairids)], model, ref, draws, seed + ti*100 + pi + 50)
        tasks[task] = {'expected_cases': sum(c['experiment'] == task for c in cases.values()), 'case_union': len(bycase), 'common_complete_cases': len(common), 'excluded_from_common_support': len(bycase) - len(common),
                       'common_case_ids': sorted(common), 'models': {m: {'all_attempts': base.latency_stats([r for r in rs if r['model_label'] == m]),
                           'complete_case_count': len(complete[m]), 'primary_case_median_latency_s': base.quantile([complete[m][c] for c in common], .5),
                           'case_median_distribution': distribution([complete[m][c] for c in common])} for m in models},
                       'comparisons': contrasts, 'pairwise_complete_sensitivity': sensitivities,
                       'repeat_blocks': {str(i): {m: base.latency_stats([r for r in rs if r['model_label'] == m and r['repeat'] == i]) for m in models} for i in range(3)}}
    return {'source': source, 'models': models, 'tasks': tasks, 'measured_attempts': len(selected), 'expected_measured_attempts': len(cases)*len(models)*3, 'missing_measured_attempts': len(cases)*len(models)*3-len(selected),
            'warmup_attempts': sum(r.get('phase') == 'timing_warmup' for r in rows), 'cost_all_attempts': base.costs(rows),
            'cost_measured': base.costs(selected), 'method': 'New interleaved timing only. Common all-model complete case support; median across 3 repeats then median across cases. Pairwise-complete sensitivity separately.',
            'comparative_speed_eligible': source['metadata'].get('status') == 'complete' and len(selected) == len(cases)*len(models)*3 and set(r['model_label'] for r in selected) == set(models)}


def arm_base(arm):
    for suffix in (' uncached', ' cached'):
        if arm.endswith(suffix):
            return arm[:-len(suffix)]
    return arm


def cache_pairs(arms):
    out = []
    refs = [a for a in ('Jev', 'Luna', 'Luna uncached', 'Luna cached') if a in arms]
    for arm in arms:
        if arm == 'Jev':
            continue
        for ref in refs:
            if arm == ref or (ref,arm) in out or (arm == 'Luna uncached' and ref == 'Luna cached'):
                continue
            out.append((arm,ref))
        if arm.endswith(' cached') and arm[:-7] + ' uncached' in arms:
            pair = (arm, arm[:-7] + ' uncached')
            if pair not in out and pair[::-1] not in out:
                out.append(pair)
    return out


def cache_pair_metrics(items):
    accepted = [(a,b) for a,b in items if base.valid(a) and base.valid(b)]
    timed = [(a,b) for a,b in accepted if all(base.finite(r.get('latency_s')) and r['latency_s'] > 0 for r in (a,b))]
    ca, cb = base.costs([a for a,b in items]), base.costs([b for a,b in items])
    return {'accuracy_difference': base.average([base.correct(a)-base.correct(b) for a,b in items]),
            'brier_valid_pairs_difference': base.average([(a['probability']-a['target'])**2-(b['probability']-b['target'])**2 for a,b in accepted]),
            'median_paired_latency_ratio': base.quantile([a['latency_s']/b['latency_s'] for a,b in timed], .5),
            'mean_latency_difference_s': base.average([a['latency_s']-b['latency_s'] for a,b in timed]),
            'cost_ratio': ca['total_billed_usd']/cb['total_billed_usd'] if ca['total_billed_usd'] is not None and cb['total_billed_usd'] else None,
            'label_agreement_valid_pairs': base.average([int((a['probability']>=.5)==(b['probability']>=.5)) for a,b in accepted])}


def cache_analysis(rows, source, draws, seed):
    measured = [r for r in rows if r.get('phase') in ('measured', 'cache_measured')]
    primes = [r for r in rows if r.get('phase') == 'cache_prime']
    arms = sorted({r['model_label'] for r in measured}); bycase = defaultdict(dict)
    for r in measured:
        if r['model_label'] in bycase[r['case_id']]:
            raise ValueError('Duplicate cache arm/case')
        if r.get('target_kind') != 'label' or r.get('target') not in (0,1):
            raise ValueError('Cache case must have a binary target')
        if r.get('pair_id') is None or r.get('block') is None:
            raise ValueError('Cache clustering requires pair_id and block')
        if r.get('valid') is True and not base.valid(r):
            raise ValueError('Invalid probability marked valid')
        bycase[r['case_id']][r['model_label']] = r
    comparisons = {}
    for pi, (arm,ref) in enumerate(cache_pairs(arms)):
        groups = defaultdict(lambda: defaultdict(list)); items = []
        for cid, aa in sorted(bycase.items()):
            if arm not in aa or ref not in aa:
                continue
            a,b = aa[arm],aa[ref]
            if any(a[k] != b[k] for k in ('target','pair_id','block')):
                raise ValueError('Cache pair source mismatch')
            groups[a['block']][a['pair_id']].append((a,b)); items.append((a,b))
        clusters = {block: list(g.values()) for block,g in groups.items()}
        for block in clusters.values():
            if any(len(g) != 2 or {a['target'] for a,b in g} != {0,1} for g in block):
                raise ValueError('A cache cluster must have both counterfactual cases')
        point = cache_pair_metrics(items); samples = {k: [] for k in point}; rng = random.Random(seed+pi)
        if sum(len(v) for v in clusters.values()) >= 2:
            for _ in range(draws):
                batch = [pair for block in clusters.values() for group in rng.choices(block, k=len(block)) for pair in group]
                for k,v in cache_pair_metrics(batch).items():
                    # No known-cost subset interval when the full contrast is unknown.
                    if v is not None and point[k] is not None:
                        samples[k].append(v)
        comparisons[f'{arm} / {ref}'] = {'model': arm, 'reference': ref, 'paired_cases': len(items),
            'valid_response_pairs': sum(base.valid(a) and base.valid(b) for a,b in items),
            'clusters': sum(len(v) for v in clusters.values()), 'fixed_blocks': len(clusters),
            'metrics': {k: {'estimate': v, 'ci95': base.interval(samples[k]), 'defined_bootstrap_draws': len(samples[k])} for k,v in point.items()}}
    described = {}
    for arm in arms:
        rr = [r for r in measured if r['model_label'] == arm]
        pp = [r for r in primes if r.get('prime_for_arm', r['model_label']) == arm]
        costs = base.costs(rr+pp)
        counts = [(r.get('usage',{}).get('prompt_tokens_details') or {}) for r in rr]
        reads = [d.get('cached_tokens') for d in counts]
        writes = [d.get('cache_write_tokens') for d in counts]
        observed_cache = {}
        for name,values in [('read_tokens',reads),('write_tokens',writes)]:
            known = [v for v in values if base.finite(v) and v >= 0]
            observed_cache[name] = {'observed_calls':len(known), 'unknown_calls':len(values)-len(known), 'known_subtotal':sum(known), 'total':sum(known) if len(known)==len(values) else None}
        observed_cache['positive_read_calls'] = sum(base.finite(v) and v > 0 for v in reads)
        observed_cache['all_counters_observed_zero'] = bool(rr) and all(base.finite(v) and v == 0 for v in reads+writes)
        described[arm] = {**base.describe(rr, 'label'), 'latency': base.latency_stats(rr), 'prime_attempts': len(pp),
            'priming_cost': base.costs(pp), 'setup_inclusive_cost': costs, 'reported_cache_usage': observed_cache,
            'setup_inclusive_usd_per_1000_decisions': costs['total_billed_usd']/len(rr)*1000 if rr and costs['total_billed_usd'] is not None else None}
    return {'source': source, 'measured_attempts': len(measured), 'prime_attempts': len(primes), 'arms': described,
            'cost_all_attempts': base.costs(rows), 'comparisons': comparisons,
            'blocks': {str(block): {arm: {'quality': base.describe([r for r in measured if r['block'] == block and r['model_label'] == arm], 'label'),
                        'latency': base.latency_stats([r for r in measured if r['block'] == block and r['model_label'] == arm])} for arm in arms} for block in sorted({r['block'] for r in measured})},
            'inference': 'Exploratory paired percentile intervals over pair_id within fixed blocks, conditional on this session. Different sessions are not pooled.'}


def analyze(quality_dirs, timing_dir=None, cache_dirs=None, *, cases_path=None, timing_cases_path=None, models=None, output_dir=None, bootstrap_samples=5000, seed=20261002):
    if bootstrap_samples < 0:
        raise ValueError('bootstrap_samples must be nonnegative')
    cases_path = Path(cases_path or ROOT.parent/'expanded/data/cases.jsonl')
    case_rows = READ(cases_path); cases = {c['id']:c for c in case_rows}
    if len(cases) != len(case_rows):
        raise ValueError('Duplicate frozen case IDs')
    if isinstance(quality_dirs, (str,Path)):
        quality_dirs = [quality_dirs]
    all_rows = []; sources = []
    for path in quality_dirs:
        rr, source = load_run(path)
        if source['metadata'].get('cases_sha256') and source['metadata']['cases_sha256'] != base.digest(cases_path):
            raise ValueError('Source cases hash mismatch')
        sources.append(source)
        all_rows.extend(dict(r, analysis_source_run=str(Path(path).resolve())) for r in rr)
    rows = [r for r in all_rows if r.get('phase') == 'quality']
    models = list(models or sorted({r['model_label'] for r in rows}, key=lambda m: (m not in DEFAULT_MODELS, DEFAULT_MODELS.index(m) if m in DEFAULT_MODELS else m)))
    check_source(rows, cases, 'quality', models)
    tasks = {}
    for ti, task in enumerate(sorted({c['experiment'] for c in case_rows})):
        subset = {c['id']:c for c in case_rows if c['experiment'] == task}
        kinds = {c['target_kind'] for c in subset.values()}
        if len(kinds) != 1:
            raise ValueError('Mixed task target kinds')
        kind = kinds.pop(); rs = [r for r in rows if r['experiment'] == task]
        mm = {}
        for m in models:
            rr = [r for r in rs if r['model_label'] == m]
            mm[m] = {**base.describe(rr,kind), 'expected_cases':len(subset), 'missing_attempts':len(subset)-len(rr)}
        tasks[task] = {'expected_cases':len(subset), 'target_kind':kind, 'models':mm,
            'comparisons': {f'{m} / {ref}':paired_quality(rs,cases,kind,m,ref,bootstrap_samples,seed+ti*100+pi) for pi,(m,ref) in enumerate(comparison_pairs(models))}}
    timing = None
    if timing_dir:
        rr, source = load_run(timing_dir)
        tcp = Path(timing_cases_path or ROOT.parent/'expanded/data/timing-cases.jsonl')
        tcases = {c['id']:c for c in READ(tcp)}
        if source['metadata'].get('cases_sha256') and source['metadata']['cases_sha256'] != base.digest(tcp):
            raise ValueError('Timing cases hash mismatch')
        timing = timing_analysis(rr,tcases,models,source,bootstrap_samples,seed+10000)
    caches = []
    for i,path in enumerate(cache_dirs or []):
        rr,source = load_run(path)
        caches.append(cache_analysis(rr,source,bootstrap_samples,seed+20000+i*100))
    provenance = {}
    for m in models:
        rr = [r for r in rows if r['model_label'] == m]; dates = [r['started_at'] for r in rr if r.get('started_at')]
        provenance[m] = {'source_runs':sorted({r['analysis_source_run'] for r in rr}), 'first_started_at':min(dates) if dates else None,
                         'last_started_at':max(dates) if dates else None, 'requested_model_ids':sorted({r['requested_model'] for r in rr if r.get('requested_model')}),
                         'returned_model_provider_pairs':sorted({(r.get('response_model'),r.get('provider')) for r in rr if r.get('valid')})}
    result = {'analysis_version':1, 'generated_at_utc':datetime.now(timezone.utc).isoformat(), 'analysis_spec_sha256':base.digest(ROOT/'analysis-spec.json'),
        'analysis_code_sha256':base.digest(Path(__file__)), 'scoring_dependency_sha256':base.digest(BASE_PATH), 'cases_sha256':base.digest(cases_path), 'models':models,
        'bootstrap_replicates':bootstrap_samples, 'bootstrap_seed':seed, 'quality_sources':sources, 'model_provenance':provenance,
        'quality':{'attempts':len(rows), 'expected_attempts':len(cases)*len(models), 'missing_attempts':len(cases)*len(models)-len(rows),
                   'cost_all_quality_sources':base.costs(all_rows), 'cost_quality_phase':base.costs(rows),
                   'bulk_latency_diagnostic_only':{m:base.latency_stats([r for r in rows if r['model_label']==m]) for m in models}},
        'tasks':tasks, 'experiments':tasks, 'timing':timing, 'cache_sessions':caches,
        'limitations':['All intervals are exploratory and unadjusted for multiple comparisons. Wilson marginal intervals assume independent rows.',
            'Earlier Jev/Luna quality outputs are reused. Model time windows and providers differ; quality comparison is conditional on the saved observations.',
            'Only the new all-model interleaved timing session supports comparative speed claims. No concurrent bulk timing is pooled into it.',
            'Invalid outputs count as wrong for accuracy. Proper scores require valid probabilities. Missing attempts and missing bills remain explicit.',
            'Public labels can be wrong and may overlap training data. Shared synthetic templates and one-session sampling limit generalization.',
            'Schema equality does not equalize tokenization or internal computation. Reasoning configuration is declared per model; observed counters are audited separately.',
            'Cache sessions are summarized separately. Cost savings depend on verified cache hits, prefix length, reuse and reported billing.']}
    dest = Path(output_dir or ROOT); dest.mkdir(parents=True,exist_ok=True)
    (dest/'summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    lines=['# Model comparison analysis','','All uncertainty intervals are exploratory; quality is reported by task.','',
           '| Task | Model | Valid / attempted | Accuracy | Brier | MAE | USD / 1,000 |','|---|---|---:|---:|---:|---:|---:|']
    for task, item in tasks.items():
        for model, d in item['models'].items():
            q=d['quality']; f=base.short_number
            lines.append(f"| {task} | {model} | {d['valid']}/{d['attempts']} | {f(q.get('accuracy_all_attempts'))} | {f(q.get('brier_valid_only'))} | {f(q.get('mae_valid_only'))} | {f(d['cost']['usd_per_1000_attempts'],6)} |")
    lines += ['', '## Limits', ''] + ['- '+s for s in result['limitations']]
    (dest/'analysis.md').write_text('\n'.join(lines)+'\n')
    return result


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('quality_dirs',nargs='+',type=Path);p.add_argument('--timing-dir',type=Path)
    p.add_argument('--cache-dir',action='append',default=[],type=Path);p.add_argument('--cases',type=Path);p.add_argument('--models',nargs='+')
    p.add_argument('--output-dir',type=Path,default=ROOT);p.add_argument('--bootstrap-samples',type=int,default=5000)
    a=p.parse_args();s=analyze(a.quality_dirs,a.timing_dir,a.cache_dir,cases_path=a.cases,models=a.models,output_dir=a.output_dir,bootstrap_samples=a.bootstrap_samples)
    print(json.dumps({'summary':str(a.output_dir/'summary.json'),'models':s['models'],'attempts':s['quality']['attempts']}))
