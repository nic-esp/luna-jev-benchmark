# Compact decisions: quality, latency and cost

Evaluation date: 2 October 2026. All services were called through OpenRouter. Only the approved Alpha credential was used for inference.

## Shared output contract

Every chat model receives strict JSON Schema for `{"type":"noul","noul":p}`, with reasoning disabled, no explanations or tools, non-streaming output and a 128-token ceiling. Jev uses its native typed decision. Matching the schema permits different tokenization and decimal precision. Returned reasoning counters and actual output lengths are audited in the report.

## Quality

Each model has one recorded attempt on the same 4,785 frozen cases. Label accuracy counts service and formatting failures as wrong. Exact-probability error uses valid outputs. The report includes valid-only sensitivities and paired uncertainty.

| Model | BoolQ | RTE | WiC | Simple policy | Probability MAE (pp) |
|---|---|---|---|---|---|
| Jev | 91.77% | 90.61% | 73.98% | 100.00% | 7.640 |
| Luna | 86.39% | 87.00% | 72.88% | 99.67% | 2.440 |
| Qwen3.8 Flash | 88.17% | 84.48% | 73.20% | 97.67% | 6.103 |
| DeepSeek V4.1 Flash | 87.46% | 85.20% | 74.45% | 99.33% | 3.835 |
| MiMo V2.6 Flash | 88.90% | 83.75% | 69.59% | 98.67% | 5.892 |
| Hy4 Preview | 88.96% | 87.00% | 72.10% | 95.33% | 4.888 |

BoolQ, RTE and WiC measure agreement with published labels. These are adapted validation tasks, not official hidden-test leaderboard scores.

## Complete-response latency

One serial interleaved session evaluates 60 cases three times per model. The table reports each task’s median across within-case medians, on the same complete cases for all models. Warmups and concurrent quality traffic are excluded from these speed estimates.

| Model | BoolQ (s) | RTE (s) | WiC (s) | Simple policy (s) | Exact probability (s) |
|---|---|---|---|---|---|
| Jev | 0.267 | 0.260 | 0.254 | 0.259 | 0.259 |
| Luna | 1.377 | 1.348 | 1.205 | 1.251 | 1.277 |
| Qwen3.8 Flash | 1.115 | 1.168 | 1.233 | 1.368 | 1.274 |
| DeepSeek V4.1 Flash | 0.912 | 0.944 | 0.842 | 0.792 | 0.800 |
| MiMo V2.6 Flash | 1.675 | 1.962 | 1.451 | 1.477 | 1.970 |
| Hy4 Preview | 2.823 | 2.983 | 2.784 | 3.007 | 3.103 |

## Quality-workload price

USD per 1,000 attempted decisions, using actual response bills. A missing bill makes the complete projection unknown; the paper also retains known subtotals and billed-call means.

| Model | BoolQ | RTE | WiC | Simple policy | Exact probability |
|---|---|---|---|---|---|
| Jev | 0.019234 | 0.016811 | 0.016247 | 0.020176 | unknown |
| Luna | 0.042965 | 0.037107 | 0.035907 | 0.044276 | 0.041474 |
| Qwen3.8 Flash | unknown | unknown | unknown | unknown | unknown |
| DeepSeek V4.1 Flash | 0.046545 | 0.038352 | 0.038077 | 0.014697 | 0.042922 |
| MiMo V2.6 Flash | 0.029242 | 0.021414 | 0.018960 | 0.017260 | 0.026370 |
| Hy4 Preview | unknown | 0.180937 | 0.172176 | 0.149457 | 0.204514 |

## Long-prefix decisions and caching

The common fictional rulebook is tested on 96 decisions, arranged as 48 counterfactual pairs in four blocks. Luna has matched cached/uncached arms and four separately billed cold primes. Other models have one baseline arm with observed cache counters.

| Arm | Valid / attempts | Accuracy | Median (s) | USD / 1,000 measured | USD / 1,000 incl. primes |
|---|---|---|---|---|---|
| DeepSeek V4.1 Flash | 96/96 | 75.00% | 1.094 | 0.079520 | 0.079520 |
| Hy4 Preview | 96/96 | 81.25% | 3.318 | 0.828287 | 0.828287 |
| Jev | 96/96 | 96.88% | 0.297 | 0.134715 | 0.134715 |
| Luna cached | 96/96 | 86.46% | 1.138 | 0.071758 | 0.087035 |
| Luna uncached | 96/96 | 86.46% | 1.093 | 0.302958 | 0.302958 |
| MiMo V2.6 Flash | 96/96 | 80.21% | 2.033 | 0.081026 | 0.081026 |
| Qwen3.8 Flash | 96/96 | 76.04% | 1.490 | 0.120424 | 0.120424 |

Cache conclusions require verified read/write counters and depend on prefix length, actual hits and reuse. One account, day and client environment do not establish general provider latency or availability.

## Evidence and accounting

The ledger contains 31,680 primary and supplementary API attempts, $2.152037107 in known charges and 139 unreported bills. The exact total is unknown when any bill is missing. Failed attempts, probes, warmups and cache primes are retained.

Qwen’s shared-capacity errors remain in its results. Only unattempted cases continued after recovery checks; the prospectively recorded lower request rate changes bulk scheduling but leaves provider, prompts and scoring unchanged.

A cache setup session stopped because its 66-character administrative keys exceeded the provider’s limit. All 55 attempts remain supplementary evidence and their costs remain in the complete ledger. The primary cache session uses validated 46-character keys with unchanged rules, case facts and settings, and fresh administrative prefix identifiers. Its setup-inclusive prices allocate its four cold primes only.

Open `comparison.html` through a static HTTP server for the scientific report, figures, every benchmark/cache case and all saved API records. See `benchmark/multimodel/README.md` for offline reproduction. The public package omits RTE source passages and echoed account identifiers; numerical results remain intact. Credentials and authorization headers are excluded.
