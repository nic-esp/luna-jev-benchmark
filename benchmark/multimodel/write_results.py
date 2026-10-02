#!/usr/bin/env python3
"""Write the concise local study summary from verified saved results."""
from pathlib import Path
import json

ROOT=Path(__file__).resolve().parent
NAMES={'boolq':'BoolQ','rte':'RTE','wic':'WiC','policy':'Simple policy','probability':'Exact probability'}
def num(v,d=3):return f'{v:.{d}f}' if v is not None else 'unknown'
def pct(v):return num(100*v,2)+'%' if v is not None else 'unknown'
def table(headers,records):
    return '\n'.join(['| '+' | '.join(headers)+' |','|'+'|'.join(['---']*len(headers))+'|']+['| '+' | '.join(map(str,r))+' |' for r in records])

def build():
    for name in ('verification.json','summary-verification.json'):
        v=json.loads((ROOT/name).read_text())
        if v.get('status')!='passed' and v.get('passed') is not True:raise ValueError('Passed verification required: '+name)
    s=json.loads((ROOT/'summary.json').read_text());m=json.loads((ROOT/'report-metadata.json').read_text());models=s['models']
    lines=['# Compact decisions: quality, latency and cost','','Evaluation date: 2 October 2026. All services were called through OpenRouter. Only the approved Alpha credential was used for inference.','',
    '## Shared output contract','',
    'Every chat model receives strict JSON Schema for `{"type":"noul","noul":p}`, with reasoning disabled, no explanations or tools, non-streaming output and a 128-token ceiling. Jev uses its native typed decision. Matching the schema permits different tokenization and decimal precision. Returned reasoning counters and actual output lengths are audited in the report.','',
    '## Quality','',
    'Each model has one recorded attempt on the same 4,785 frozen cases. Label accuracy counts service and formatting failures as wrong. Exact-probability error uses valid outputs. The report includes valid-only sensitivities and paired uncertainty.','']
    records=[]
    for model in models:
        records.append([model]+[pct(s['tasks'][t]['models'][model]['quality']['accuracy_all_attempts']) for t in ('boolq','rte','wic','policy')]+[num(100*s['tasks']['probability']['models'][model]['quality']['mae_valid_only'])])
    lines+=[table(['Model','BoolQ','RTE','WiC','Simple policy','Probability MAE (pp)'],records),'',
    'BoolQ, RTE and WiC measure agreement with published labels. These are adapted validation tasks, not official hidden-test leaderboard scores.','',
    '## Complete-response latency','',
    'One serial interleaved session evaluates 60 cases three times per model. The table reports each task’s median across within-case medians, on the same complete cases for all models. Warmups and concurrent quality traffic are excluded from these speed estimates.','']
    lines += [table(['Model']+[NAMES[t]+' (s)' for t in NAMES],[[model]+[num(s['timing']['tasks'][t]['models'][model]['primary_case_median_latency_s']) for t in NAMES] for model in models]),'',
    '## Quality-workload price','',
    'USD per 1,000 attempted decisions, using actual response bills. A missing bill makes the complete projection unknown; the paper also retains known subtotals and billed-call means.','']
    lines += [table(['Model']+[NAMES[t] for t in NAMES],[[model]+[num(s['tasks'][t]['models'][model]['cost']['usd_per_1000_attempts'],6) for t in NAMES] for model in models]),'',
    '## Long-prefix decisions and caching','',
    'The common fictional rulebook is tested on 96 decisions, arranged as 48 counterfactual pairs in four blocks. Luna has matched cached/uncached arms and four separately billed cold primes. Other models have one baseline arm with observed cache counters.','']
    cache=s['cache_sessions'][0]
    lines += [table(['Arm','Valid / attempts','Accuracy','Median (s)','USD / 1,000 measured','USD / 1,000 incl. primes'],[[arm,f"{v['valid']}/{v['attempts']}",pct(v['quality']['accuracy_all_attempts']),num(v['latency']['median_s']),num(v['cost']['usd_per_1000_attempts'],6),num(v['setup_inclusive_usd_per_1000_decisions'],6)] for arm,v in cache['arms'].items()]),'',
    'Cache conclusions require verified read/write counters and depend on prefix length, actual hits and reuse. One account, day and client environment do not establish general provider latency or availability.','',
    '## Evidence and accounting','',
    f"The ledger contains {m['all_attempts']:,} primary and supplementary API attempts, ${m['known_charge_subtotal_usd']:.9f} in known charges and {m['missing_bills']} unreported bills. The exact total is unknown when any bill is missing. Failed attempts, probes, warmups and cache primes are retained.",'',
    'Qwen’s shared-capacity errors remain in its results. Only unattempted cases continued after recovery checks; the prospectively recorded lower request rate changes bulk scheduling but leaves provider, prompts and scoring unchanged.','',
    'A cache setup session stopped because its 66-character administrative keys exceeded the provider’s limit. All 55 attempts remain supplementary evidence and their costs remain in the complete ledger. The primary cache session uses validated 46-character keys with unchanged rules, case facts and settings, and fresh administrative prefix identifiers. Its setup-inclusive prices allocate its four cold primes only.','',
    'Open `comparison.html` through a static HTTP server for the scientific report, figures, every benchmark/cache case and all saved API records. See `benchmark/multimodel/README.md` for offline reproduction. The public package omits RTE source passages and echoed account identifiers; numerical results remain intact. Credentials and authorization headers are excluded.','']
    path=ROOT.parent.parent/'RESULTS.md';path.write_text('\n'.join(lines));return path

if __name__=='__main__':print(build())
