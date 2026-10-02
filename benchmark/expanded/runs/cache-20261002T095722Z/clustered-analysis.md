# Repeated cache study: analysis

Four sequential blocks, 96 distinct policy cases, and 48 counterfactual pairs. Each block has a separate prefix and one priming request. The 5,000-draw bootstrap resamples counterfactual pairs within each block, keeping both cases and all model arms together. Intervals describe case variation within this run; blocks are held fixed.

## Paired comparisons

For differences, the arm before the slash minus the arm after it. For ratios, the arm before the slash divided by the arm after it.

| Comparison | Metric | Estimate | Exploratory 95% interval |
|---|---|---:|---:|
| Luna cached / Luna uncached | accuracy_difference | 0.0208 | -0.0625 to 0.1042 |
| Luna cached / Luna uncached | brier_difference | -0.0208 | -0.1042 to 0.0625 |
| Luna cached / Luna uncached | median_paired_latency_ratio | 0.8747 | 0.8484 to 0.9033 |
| Luna cached / Luna uncached | mean_latency_difference_s | -0.1424 | -0.2129 to -0.0798 |
| Luna cached / Luna uncached | cost_ratio | 0.2370 | 0.2369 to 0.2371 |
| Luna cached / Luna uncached | label_agreement | 0.8542 | 0.7812 to 0.9169 |
| Luna cached / Jev | accuracy_difference | 0.0000 | -0.0625 to 0.0625 |
| Luna cached / Jev | brier_difference | 0.0310 | -0.0256 to 0.0923 |
| Luna cached / Jev | median_paired_latency_ratio | 3.8451 | 3.6826 to 3.9391 |
| Luna cached / Jev | mean_latency_difference_s | 0.7545 | 0.7111 to 0.7999 |
| Luna cached / Jev | cost_ratio | 0.5323 | 0.5321 to 0.5325 |
| Luna cached / Jev | label_agreement | 0.8750 | 0.8125 to 0.9271 |
| Luna uncached / Jev | accuracy_difference | -0.0208 | -0.1042 to 0.0625 |
| Luna uncached / Jev | brier_difference | 0.0518 | -0.0019 to 0.1083 |
| Luna uncached / Jev | median_paired_latency_ratio | 4.2249 | 4.0755 to 4.5011 |
| Luna uncached / Jev | mean_latency_difference_s | 0.8968 | 0.8470 to 0.9526 |
| Luna uncached / Jev | cost_ratio | 2.2462 | 2.2459 to 2.2464 |
| Luna uncached / Jev | label_agreement | 0.8542 | 0.7812 to 0.9167 |

## Block results

Measured calls exclude priming. Cost per 1,000 is a projection at the measured request mix. The final column allocates that block's separate prime across its 24 cached cases.

| Block | Arm | Cases | Accuracy | Median s | p95 s | USD / 1,000 | USD / 1,000 with prime | Read tokens | Write tokens |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | Jev | 24 | 0.8333 | 0.2451 | 0.4441 | 0.1347 | unknown | unknown | unknown |
| 1 | Luna uncached | 24 | 0.8333 | 1.1055 | 1.8930 | 0.3027 | unknown | 0 | 0 |
| 1 | Luna cached | 24 | 0.8750 | 0.9378 | 1.3077 | 0.0717 | 0.0870 | 61608 | 0 |
| 2 | Jev | 24 | 0.9583 | 0.2558 | 0.4115 | 0.1348 | unknown | unknown | unknown |
| 2 | Luna uncached | 24 | 0.8750 | 0.9796 | 2.3324 | 0.3024 | unknown | 0 | 0 |
| 2 | Luna cached | 24 | 0.8333 | 0.8967 | 1.2707 | 0.0717 | 0.0869 | 61560 | 0 |
| 3 | Jev | 24 | 0.8333 | 0.2505 | 0.3015 | 0.1348 | unknown | unknown | unknown |
| 3 | Luna uncached | 24 | 0.8750 | 1.1333 | 1.3412 | 0.3026 | unknown | 0 | 0 |
| 3 | Luna cached | 24 | 0.9167 | 0.9929 | 1.4793 | 0.0717 | 0.0870 | 61608 | 0 |
| 4 | Jev | 24 | 0.9167 | 0.2538 | 0.3924 | 0.1348 | unknown | unknown | unknown |
| 4 | Luna uncached | 24 | 0.8750 | 1.1307 | 1.4321 | 0.3031 | unknown | 0 | 0 |
| 4 | Luna cached | 24 | 0.9167 | 1.0119 | 1.6308 | 0.0718 | 0.0871 | 61704 | 0 |

## Counterfactual consistency

A pair passes only if both its eligible case and its one-fact ineligible counterpart are classified correctly.

- Jev: 38/48 pairs.
- Luna uncached: 35/48 pairs.
- Luna cached: 38/48 pairs.

These are synthetic written-policy cases. The new identifiers establish separate cache prefixes; they do not create independent days, providers, or repeated model draws on the same case. No general model ranking or cache-induced change in answer quality follows from this experiment alone.
