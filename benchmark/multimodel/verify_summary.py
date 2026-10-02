#!/usr/bin/env python3
"""Independently recompute descriptive summary results from completed raw runs.

Standard library only; no analysis-module imports and no network operations.
Bootstrap intervals and request protocol compliance have separate verifiers.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parent
CASES = ROOT.parent / 'expanded/data/cases.jsonl'
TIMING_CASES = ROOT.parent / 'expanded/data/timing-cases.jsonl'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rows_from(path):
    rows = []
    for number, line in enumerate(Path(path).read_text().splitlines(), 1):
        if line.strip():
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError(f'{path}:{number}: expected an object')
            rows.append(item)
    return rows


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def accepted(row):
    p, y, kind = row.get('probability'), row.get('target'), row.get('target_kind')
    return (row.get('valid') is True and number(p) and 0 <= p <= 1 and
            number(y) and 0 <= y <= 1 and kind in ('label', 'probability') and
            (kind != 'label' or y in (0, 1)))


def correct(row):
    return int(accepted(row) and (row['probability'] >= .5) == bool(row['target']))


def average(values):
    return math.fsum(values) / len(values) if values else None


def quantile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * fraction
    left, right = math.floor(index), math.ceil(index)
    return values[left] * (1 - (index - left)) + values[right] * (index - left)


def wilson(successes, count):
    if not count:
        return None
    z = 1.959963984540054
    denominator = count + z * z
    center = (successes + z * z / 2) / denominator
    radius = z * math.sqrt(successes * (1 - successes / count) + z * z / 4) / denominator
    return [max(0, center - radius), min(1, center + radius)]


def billing(rows):
    known = [r['cost_usd'] for r in rows if number(r.get('cost_usd')) and r['cost_usd'] >= 0]
    subtotal = math.fsum(known)
    missing = len(rows) - len(known)
    return {'requests': len(rows), 'known_billed_usd': subtotal,
            'observed_bills': len(known), 'missing_bills': missing,
            'total_billed_usd': None if missing else subtotal,
            'usd_per_1000_attempts': subtotal * 1000 / len(rows) if rows and not missing else None}


def class_counts(rows):
    result = {}
    for label, name in ((0, 'no'), (1, 'yes')):
        selected = [r for r in rows if r['target'] == label]
        successes = sum(correct(r) for r in selected)
        result[name] = {'attempts': len(selected), 'valid': sum(accepted(r) for r in selected),
                        'correct': successes, 'recall_all_attempts': successes / len(selected) if selected else None}
    return result


def balanced(counts):
    values = [counts[k]['recall_all_attempts'] for k in ('no', 'yes')]
    return average(values) if all(v is not None for v in values) else None


def describe(rows, kind):
    valid_rows = [r for r in rows if accepted(r)]
    n, nv = len(rows), len(valid_rows)
    result = {'attempts': n, 'valid': nv, 'invalid': n - nv,
              'success_rate': nv / n if n else None, 'success_rate_wilson95': wilson(nv, n),
              'unique_cases': len({r['case_id'] for r in rows}), 'target_kind': kind, 'cost': billing(rows)}
    squared = [(r['probability'] - r['target']) ** 2 for r in valid_rows]
    if kind == 'label':
        k = sum(correct(r) for r in rows)
        counts = class_counts(rows)
        losses = []
        for row in valid_rows:
            p = min(1 - 1e-15, max(1e-15, row['probability']))
            losses.append(-math.log(p) if row['target'] else -math.log1p(-p))
        result['quality'] = {'correct': k, 'accuracy_all_attempts': k / n if n else None,
            'accuracy_all_attempts_wilson95': wilson(k, n),
            'balanced_accuracy_all_attempts': balanced(counts), 'class_denominators': counts,
            'accuracy_valid_only': k / nv if nv else None, 'accuracy_valid_only_wilson95': wilson(k, nv),
            'balanced_accuracy_valid_only': balanced(class_counts(valid_rows)),
            'brier_valid_only': average(squared), 'log_loss_valid_only': average(losses),
            'proper_score_denominator': nv}
        total = result['cost']['total_billed_usd']
        result['cost']['usd_per_correct_answer'] = total / k if total is not None and k else None
    else:
        mse = average(squared)
        result['quality'] = {
            'mae_valid_only': average([abs(r['probability'] - r['target']) for r in valid_rows]),
            'rmse_valid_only': math.sqrt(mse) if mse is not None else None,
            'excess_brier_valid_only': mse,
            'expected_brier_valid_only': average([e + r['target'] * (1 - r['target']) for e, r in zip(squared, valid_rows)]),
            'error_score_denominator': nv}
    return result


def distribution(values):
    return {'n': len(values), 'median_s': quantile(values, .5), 'p95_s': quantile(values, .95), 'mean_s': average(values)}


def latency(rows):
    values = [r['latency_s'] for r in rows if number(r.get('latency_s')) and r['latency_s'] > 0]
    return {'attempts': len(rows), 'valid': sum(accepted(r) for r in rows),
            'observed_positive_timings': len(values), 'median_s': quantile(values, .5),
            'p95_s': quantile(values, .95), 'mean_s': average(values)}


class Audit:
    def __init__(self):
        self.mismatches = []
        self.checked = 0
        self.inputs = {}

    def require(self, condition, location, detail):
        self.checked += 1
        if not condition:
            self.mismatches.append({'path': location, 'issue': detail})

    def compare(self, actual, expected, location):
        """Compare expected fields only; inferential fields have separate audits."""
        if isinstance(expected, dict):
            if not isinstance(actual, dict):
                self.require(False, location, 'Expected an object')
                return
            for key, value in expected.items():
                if key not in actual:
                    self.require(False, location + '.' + key, 'Missing summary field')
                else:
                    self.compare(actual[key], value, location + '.' + key)
        elif isinstance(expected, list):
            if not isinstance(actual, list) or len(actual) != len(expected):
                self.require(False, location, 'Array length or type differs')
                return
            for index, value in enumerate(expected):
                self.compare(actual[index], value, f'{location}[{index}]')
        else:
            if number(expected):
                equal = number(actual) and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-12)
            else:
                equal = type(actual) is type(expected) and actual == expected
            self.checked += 1
            if not equal:
                self.mismatches.append({'path': location, 'reported': actual, 'recomputed': expected})

    def track(self, path):
        path = Path(path).resolve()
        self.inputs[str(path)] = digest(path)


def case_map(path):
    rows = rows_from(path)
    cases = {r['id']: r for r in rows}
    if len(cases) != len(rows):
        raise ValueError(f'Duplicate frozen case IDs in {path}')
    return cases


def resolve_source(directory, summary_path):
    p = Path(directory)
    candidates = [p] if p.is_absolute() else [summary_path.parent / p, ROOT.parent.parent / p, ROOT / p, p]
    for candidate in candidates:
        if (candidate / 'metadata.json').exists():
            return candidate.resolve()
    raise ValueError(f'Missing source directory: {directory}')


def load_source(source, summary_path, audit, location):
    directory = resolve_source(source['run_dir'], summary_path)
    metadata_path, raw_path = directory / 'metadata.json', directory / 'responses.jsonl'
    metadata = json.loads(metadata_path.read_text())
    # Refuse active/stopped runs before reading observations.
    if metadata.get('status') != 'complete':
        raise ValueError(f'Refusing unfinished source {directory}: {metadata.get("status")}')
    audit.track(metadata_path)
    audit.track(raw_path)
    rows = rows_from(raw_path)
    audit.compare(source, {'metadata': metadata, 'responses_sha256': digest(raw_path),
                          'attempts': len(rows), 'cost_all_attempts': billing(rows)}, location)
    for i, row in enumerate(rows):
        cost = row.get('cost_usd')
        audit.require(cost is None or (number(cost) and cost >= 0), f'{location}.raw[{i}].cost', 'Bill must be nonnegative finite or unknown')
        audit.require(accepted(row) if row.get('valid') is True else row.get('probability') is None,
                      f'{location}.raw[{i}].valid', 'Validity and scored probability disagree')
    return directory, rows


def check_rows(rows, cases, models, audit, location, repeats):
    seen = set()
    for row in rows:
        identity = (row['case_id'], row['model_label'], row.get('repeat', 0))
        audit.require(identity not in seen, location, f'Duplicate observation {identity}')
        seen.add(identity)
        audit.require(row['model_label'] in models, location, f'Unexpected model {row["model_label"]}')
        audit.require(row.get('repeat', 0) in repeats, location, f'Unexpected repeat {identity}')
        case = cases.get(row['case_id'])
        audit.require(case is not None, location, f'Unknown case {row["case_id"]}')
        if case:
            audit.require(all(row.get(k) == case[k] for k in ('experiment', 'target', 'target_kind')),
                          location, f'Target/task mismatch {identity}')
    expected = {(cid, model, repeat) for cid in cases for model in models for repeat in repeats}
    audit.require(seen == expected, location + '.cross_product', f'Expected {len(expected)} case/model/repeat identities; observed {len(seen)}')


def verify_quality(summary, path, cases, audit):
    models, all_rows = summary['models'], []
    directories = []
    for i, source in enumerate(summary['quality_sources']):
        directory, rows = load_source(source, path, audit, f'quality_sources[{i}]')
        directories.append(directory)
        if source['metadata'].get('cases_sha256'):
            audit.compare(source['metadata']['cases_sha256'], summary['cases_sha256'], f'quality_sources[{i}].metadata.cases_sha256')
        all_rows.extend(rows)
    audit.require(len(directories) == len(set(directories)), 'quality_sources', 'Duplicate quality source directories')
    rows = [r for r in all_rows if r.get('phase') == 'quality']
    check_rows(rows, cases, models, audit, 'quality.raw', {0})
    expected_count = len(cases) * len(models)
    audit.compare(summary['quality'], {'attempts': len(rows), 'expected_attempts': expected_count,
        'missing_attempts': expected_count - len(rows), 'cost_all_quality_sources': billing(all_rows),
        'cost_quality_phase': billing(rows), 'bulk_latency_diagnostic_only': {
            m: latency([r for r in rows if r['model_label'] == m]) for m in models}}, 'quality')
    tasks = sorted({c['experiment'] for c in cases.values()})
    audit.require(set(summary['tasks']) == set(tasks), 'tasks', 'Task set differs from frozen cases')
    for task in tasks:
        cc = [c for c in cases.values() if c['experiment'] == task]
        kinds = {c['target_kind'] for c in cc}
        if len(kinds) != 1:
            raise ValueError(f'Mixed target kinds in task {task}')
        kind = next(iter(kinds))
        expected = {'expected_cases': len(cc), 'target_kind': kind, 'models': {}}
        for model in models:
            selected = [r for r in rows if r['experiment'] == task and r['model_label'] == model]
            expected['models'][model] = {**describe(selected, kind), 'expected_cases': len(cc), 'missing_attempts': len(cc) - len(selected)}
        audit.require(set(summary['tasks'].get(task, {}).get('models', {})) == set(models), 'tasks.' + task + '.models', 'Task model set differs')
        audit.compare(summary['tasks'].get(task), expected, 'tasks.' + task)
    if 'experiments' in summary:
        audit.require(summary['experiments'] == summary['tasks'], 'experiments', 'Task alias differs')


def timing_contrast(left, right, identities):
    a, b = [left[c] for c in identities], [right[c] for c in identities]
    values = {'ratio_of_case_medians': statistics.median(a) / statistics.median(b) if a else None,
              'median_of_case_ratios': statistics.median(x / y for x, y in zip(a, b)) if a else None,
              'mean_case_latency_difference_s': average([x - y for x, y in zip(a, b)])}
    return {'paired_cases': len(identities), 'metrics': {key: {'estimate': value} for key, value in values.items()}}


def verify_timing(summary, path, cases, audit):
    item, models = summary['timing'], summary['models']
    _, all_rows = load_source(item['source'], path, audit, 'timing.source')
    rows = [r for r in all_rows if r.get('phase') == 'timing']
    check_rows(rows, cases, models, audit, 'timing.raw', {0, 1, 2})
    expected_count = len(cases) * len(models) * 3
    audit.compare(item, {'models': models, 'measured_attempts': len(rows),
        'expected_measured_attempts': expected_count, 'missing_measured_attempts': expected_count - len(rows),
        'warmup_attempts': sum(r.get('phase') == 'timing_warmup' for r in all_rows),
        'cost_all_attempts': billing(all_rows), 'cost_measured': billing(rows),
        'comparative_speed_eligible': len(rows) == expected_count and set(r['model_label'] for r in rows) == set(models)}, 'timing')
    task_names = {c['experiment'] for c in cases.values()}
    audit.require(set(item['tasks']) == task_names, 'timing.tasks', 'Timing task set differs')
    for task in sorted(task_names):
        selected = [r for r in rows if r['experiment'] == task]
        index = defaultdict(lambda: defaultdict(dict))
        for row in selected:
            index[row['model_label']][row['case_id']][row['repeat']] = row
        complete = {m: {} for m in models}
        for model in models:
            for cid, repeats in index[model].items():
                if set(repeats) == {0, 1, 2} and all(accepted(r) and number(r.get('latency_s')) and r['latency_s'] > 0 for r in repeats.values()):
                    complete[model][cid] = statistics.median(r['latency_s'] for r in repeats.values())
        common = set.intersection(*(set(complete[m]) for m in models)) if models else set()
        case_union = {r['case_id'] for r in selected}
        expected = {'expected_cases': sum(c['experiment'] == task for c in cases.values()),
            'case_union': len(case_union), 'common_complete_cases': len(common),
            'excluded_from_common_support': len(case_union - common), 'common_case_ids': sorted(common),
            'models': {}, 'repeat_blocks': {}}
        for model in models:
            values = [complete[model][cid] for cid in sorted(common)]
            expected['models'][model] = {'all_attempts': latency([r for r in selected if r['model_label'] == model]),
                'complete_case_count': len(complete[model]), 'primary_case_median_latency_s': quantile(values, .5),
                'case_median_distribution': distribution(values)}
        for repeat in range(3):
            expected['repeat_blocks'][str(repeat)] = {m: latency([r for r in selected if r['model_label'] == m and r['repeat'] == repeat]) for m in models}
        reported = item['tasks'][task]
        audit.require(set(reported['models']) == set(models), 'timing.tasks.' + task + '.models', 'Timing model set differs')
        audit.compare(reported, expected, 'timing.tasks.' + task)
        for section in ('comparisons', 'pairwise_complete_sensitivity'):
            for key, contrast in reported[section].items():
                model, reference = contrast['model'], contrast['reference']
                support = common if section == 'comparisons' else set(complete[model]) & set(complete[reference])
                audit.compare(contrast, timing_contrast(complete[model], complete[reference], sorted(support)),
                              'timing.tasks.' + task + '.' + section + '.' + key)


def cache_usage(rows, audit, location):
    reads, writes = [], []
    for index, row in enumerate(rows):
        response = row.get('response') or {}
        usage = response.get('usage') or {}
        audit.require((row.get('usage') or {}) == usage, f'{location}.raw_usage[{index}]', 'Saved usage differs from raw response usage')
        counters = usage.get('prompt_tokens_details') or {}
        reads.append(counters.get('cached_tokens'))
        writes.append(counters.get('cache_write_tokens'))
    output = {}
    for name, values in (('read_tokens', reads), ('write_tokens', writes)):
        known = [v for v in values if number(v) and v >= 0]
        output[name] = {'observed_calls': len(known), 'unknown_calls': len(values) - len(known),
                        'known_subtotal': math.fsum(known), 'total': math.fsum(known) if len(known) == len(values) else None}
    output['positive_read_calls'] = sum(number(v) and v > 0 for v in reads)
    output['all_counters_observed_zero'] = bool(rows) and all(number(v) and v == 0 for v in reads + writes)
    return output


def verify_cache_session(session, path, audit, location):
    directory, rows = load_source(session['source'], path, audit, location + '.source')
    case_path = directory / 'cases.jsonl'
    audit.track(case_path)
    cases = case_map(case_path)
    measured = [r for r in rows if r.get('phase') in ('measured', 'cache_measured')]
    primes = [r for r in rows if r.get('phase') == 'cache_prime']
    arms = sorted({r['model_label'] for r in measured})
    identities = [(r['case_id'], r['model_label']) for r in measured]
    audit.require(len(identities) == len(set(identities)), location, 'Duplicate cache case/arm')
    audit.require(set(identities) == {(cid, arm) for cid in cases for arm in arms}, location, 'Incomplete cache case/arm cross-product')
    audit.require(set(session['arms']) == set(arms), location + '.arms', 'Cache arm set differs')
    for row in measured:
        case = cases.get(row['case_id'])
        audit.require(case is not None and all(row.get(k) == case.get(k) for k in ('target', 'target_kind', 'block', 'pair_id')),
                      location, f'Cache source target/cluster mismatch {row["case_id"]}')
    for row in primes:
        audit.require(row.get('prime_for_arm', row['model_label']) in arms, location, 'Unallocated prime')
    expected = {'measured_attempts': len(measured), 'prime_attempts': len(primes),
                'cost_all_attempts': billing(rows), 'arms': {}, 'blocks': {}}
    for arm in arms:
        rr = [r for r in measured if r['model_label'] == arm]
        pp = [r for r in primes if r.get('prime_for_arm', r['model_label']) == arm]
        total = billing(rr + pp)
        expected['arms'][arm] = {**describe(rr, 'label'), 'latency': latency(rr), 'prime_attempts': len(pp),
            'priming_cost': billing(pp), 'setup_inclusive_cost': total,
            'setup_inclusive_usd_per_1000_decisions': total['total_billed_usd'] * 1000 / len(rr) if rr and total['total_billed_usd'] is not None else None,
            'reported_cache_usage': cache_usage(rr, audit, location + '.arms.' + arm)}
        # Prime counters are also read back, even though the summary exposes only measured totals.
        if pp:
            cache_usage(pp, audit, location + '.primes.' + arm)
    for block in sorted({r['block'] for r in measured}):
        expected['blocks'][str(block)] = {}
        for arm in arms:
            rr = [r for r in measured if r['block'] == block and r['model_label'] == arm]
            expected['blocks'][str(block)][arm] = {'quality': describe(rr, 'label'), 'latency': latency(rr)}
    audit.compare(session, expected, location)


def verify_summary(summary_path=ROOT / 'summary.json', *, output_path=None, cases_path=CASES, timing_cases_path=TIMING_CASES):
    path = Path(summary_path).resolve()
    output = Path(output_path) if output_path else path.with_name('summary-verification.json')
    audit = Audit()
    result = {'generated_at_utc': datetime.now(timezone.utc).isoformat(), 'passed': False,
              'summary_path': str(path), 'verifier_sha256': digest(__file__),
              'scope': 'Independent raw-data recomputation of descriptive quality scores and Wilson intervals, billing and null projections, all-model timing support and point estimates, cache arm/block scores, prime allocations and raw cache counters.',
              'not_recomputed': ['Bootstrap confidence intervals', 'Paired quality/cache contrast metrics', 'Request protocol compliance'],
              'errors': []}
    try:
        audit.track(path)
        summary = json.loads(path.read_text())
        models = summary['models']
        audit.require(bool(models) and len(models) == len(set(models)), 'models', 'Models must be a nonempty unique list')
        audit.track(cases_path)
        cases = case_map(cases_path)
        audit.compare(summary['cases_sha256'], digest(cases_path), 'cases_sha256')
        verify_quality(summary, path, cases, audit)
        if summary.get('timing') is not None:
            audit.track(timing_cases_path)
            tcases = case_map(timing_cases_path)
            source_hash = summary['timing']['source']['metadata'].get('cases_sha256')
            if source_hash:
                audit.compare(source_hash, digest(timing_cases_path), 'timing.source.metadata.cases_sha256')
            verify_timing(summary, path, tcases, audit)
        for i, session in enumerate(summary.get('cache_sessions', [])):
            verify_cache_session(session, path, audit, f'cache_sessions[{i}]')
        for input_path, original_hash in audit.inputs.items():
            audit.require(digest(input_path) == original_hash, input_path, 'Input changed during verification')
    except Exception as exc:
        result['errors'].append({'type': type(exc).__name__, 'message': str(exc)})
    result.update(checked_values=audit.checked, mismatches=audit.mismatches, input_sha256=audit.inputs)
    result['passed'] = not result['errors'] and not audit.mismatches
    result['status'] = 'passed' if result['passed'] else 'failed'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('summary', nargs='?', type=Path, default=ROOT / 'summary.json')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--cases', type=Path, default=CASES)
    parser.add_argument('--timing-cases', type=Path, default=TIMING_CASES)
    args = parser.parse_args()
    result = verify_summary(args.summary, output_path=args.output, cases_path=args.cases, timing_cases_path=args.timing_cases)
    print(json.dumps({k: result[k] for k in ('passed', 'status', 'checked_values', 'errors')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
