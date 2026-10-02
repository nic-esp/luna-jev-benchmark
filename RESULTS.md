# GPT-6 Luna and Jev: expanded study

Evaluation date: 2 October 2026. Both services were called through OpenRouter.

Open `comparison.html` through a static HTTP server for the full paper, figures, all 4,881 expanded cases and all 10,675 API attempts. The original pilot remains in `pilot.html`.

## Output restriction

Luna returned the same two-field probability object as Jev: `{"type":"noul","noul":p}`. Strict JSON Schema, no explanations, reasoning disabled. Every valid Luna response passed this contract and reported zero reasoning tokens. The maximum was 128 tokens; actual Luna usage was 20–28 tokens. Jev reported 20 per valid answer. Matching the schema did not force identical token counts or decimal precision.

## Quality

| Task | Cases per model | Jev | Luna |
|---|---:|---:|---:|
| boolq | 3270 | 91.77% | 86.39% |
| rte | 277 | 90.61% | 87.00% |
| wic | 638 | 73.98% | 72.88% |
| policy | 300 | 100.00% | 99.67% |

Exact-probability MAE: Jev 7.640 percentage points on 299 valid answers; Luna 2.440 on 300. Paired inference uses the 299 jointly valid cases.

BoolQ, RTE and WiC scores measure agreement with published labels. Jev’s BoolQ advantage has a paired interval above zero; the RTE and WiC accuracy intervals include zero. These are adapted validation tasks, not official hidden-test leaderboard scores.

## Speed and caching

The separate serial timing protocol used 60 frozen cases × 3 repeats × 2 models, plus 12 warmups. Across tasks, Luna/Jev ratios of case-median latency ranged from 4.79 to 5.17. Concurrent quality-run timings are excluded from these comparisons.

Four fresh cache prefixes produced 96 verified hits. Cost per 1,000 long-rulebook decisions:

| Arm | USD per 1,000 |
|---|---:|
| Jev | 0.134768 |
| Luna uncached | 0.302708 |
| Luna cached | 0.071733 |
| Luna cached, including all four primes | 0.086997 |

Jev and cached Luna each answered 85/96 correctly; uncached Luna answered 83/96. Cache quality contrasts were inconclusive. Savings depend on the observed prefix length and 24 reuses per prefix.

## Evidence and accounting

Known charges across all 10,675 attempts total **$0.374134623**. One Jev HTTP 520 failure has no reported bill, so the exact total is unknown. The failure was retained without retry or replacement.

The report includes full protocols, frozen data and analysis decisions, paired confidence intervals, dataset attribution, every request/response, token usage, costs, failures, model IDs, source hashes, tests and plotting/report code. API keys and authorization headers are excluded.

See `benchmark/expanded/README.md` for reproduction. The public GitHub package omits RTE premise/hypothesis source text because redistribution terms were not verified; numeric results, labels, IDs, provenance and retrieval code remain available. The complete local archive preserves the original inputs.
