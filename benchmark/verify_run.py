"""Verify a completed main run against its frozen inputs and API evidence."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import runner

def verify(run_dir):
    run_dir = Path(run_dir)
    cases_path = runner.ROOT / 'data/cases.jsonl'
    cases = {c['id']: c for c in runner.load_cases(cases_path)}
    meta = json.loads((run_dir/'metadata.json').read_text())
    rows = [json.loads(x) for x in (run_dir/'responses.jsonl').read_text().splitlines()]
    measured = [r for r in rows if r['phase']=='measured']
    checks = {}
    checks['run_completed'] = meta['status']=='complete'
    checks['frozen_case_hash_matches'] = meta['cases_sha256']==hashlib.sha256(cases_path.read_bytes()).hexdigest()
    checks['runner_source_hash_matches'] = meta['runner_sha256']==hashlib.sha256((runner.ROOT/'runner.py').read_bytes()).hexdigest()
    checks['expected_number_of_calls'] = len(measured)==meta['cases']*2
    counts = Counter((r['case_id'],r['model_label']) for r in measured)
    checks['one_request_per_model_per_case'] = len(counts)==len(measured) and all(
        counts[(cid,label)]==1 for cid in {r['case_id'] for r in measured} for label in runner.MODELS)
    checks['inputs_and_targets_match_frozen_cases'] = all(
        r['request']==runner.payload_for(cases[r['case_id']],r['model_label'])[1]
        and r['endpoint']==runner.payload_for(cases[r['case_id']],r['model_label'])[0]
        and r['target']==cases[r['case_id']]['target']
        and r['target_kind']==cases[r['case_id']]['target_kind'] for r in measured)
    checks['all_responses_valid'] = all(r['valid'] and runner.validate_answer(r['answer'])==r['probability'] for r in rows)
    checks['all_costs_reported'] = all(r['cost_usd'] is not None and r['cost_usd']==r['response']['usage']['cost'] for r in rows)
    checks['billed_total_reconciles'] = abs(sum(r['cost_usd'] or 0 for r in rows)-meta['charged_usd'])<1e-10
    luna = [r for r in rows if r['model_label']=='Luna']
    checks['luna_is_openai_with_zero_reasoning'] = all(r['provider']=='OpenAI' and r['usage']['completion_tokens_details']['reasoning_tokens']==0 for r in luna)
    checks['main_luna_cache_reads_and_writes_zero'] = all(
        r['usage']['prompt_tokens_details']['cached_tokens']==0 and
        r['usage']['prompt_tokens_details']['cache_write_tokens']==0 for r in luna)
    result = {'passed':all(checks.values()),'checks':checks,'measured_requests':len(measured),
              'warmup_requests':len(rows)-len(measured),'served_models':sorted({r['response_model'] for r in rows}),
              'charged_usd_including_warmups':meta['charged_usd']}
    (run_dir/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('run_dir'); a=p.parse_args()
    result=verify(a.run_dir); print(json.dumps(result,indent=2))
    raise SystemExit(0 if result['passed'] else 1)
