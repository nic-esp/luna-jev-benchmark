# Expanded paired benchmark analysis

All confidence intervals are exploratory and unadjusted. Concurrent bulk timings are diagnostic only.

Quality run status: **complete**. Recorded attempts: **9570**.

| Task | Model | Valid / attempts | Accuracy, all attempts | Balanced accuracy | Brier, valid | MAE, valid | USD / 1,000 attempts |
|---|---|---:|---:|---:|---:|---:|---:|
| boolq | Jev | 3270/3270 | 0.9177 | 0.9163 | 0.0624 | unknown | 0.019234 |
| boolq | Luna | 3270/3270 | 0.8639 | 0.8578 | 0.1254 | unknown | 0.042965 |
| policy | Jev | 300/300 | 1.0000 | 1.0000 | 0.0052 | unknown | 0.020176 |
| policy | Luna | 300/300 | 0.9967 | 0.9967 | 0.0033 | unknown | 0.044276 |
| probability | Jev | 299/300 | unknown | unknown | unknown | 0.0764 | unknown |
| probability | Luna | 300/300 | unknown | unknown | unknown | 0.0244 | 0.041474 |
| rte | Jev | 277/277 | 0.9061 | 0.9074 | 0.0718 | unknown | 0.016811 |
| rte | Luna | 277/277 | 0.8700 | 0.8685 | 0.1103 | unknown | 0.037107 |
| wic | Jev | 638/638 | 0.7398 | 0.7398 | 0.1718 | unknown | 0.016247 |
| wic | Luna | 638/638 | 0.7288 | 0.7288 | 0.2600 | unknown | 0.035907 |

## Interpretation limits

- Published benchmark scores are agreement with their frozen reference labels; source fidelity does not establish label correctness. Public development data may overlap model training.
- Accuracy and balanced accuracy count invalid outputs as wrong. Brier/log loss and exact-probability errors require valid probabilities; their denominators and paired exclusions are explicit.
- The full development splits are fixed finite benchmarks. Wilson intervals and bootstrap intervals are nominal exploratory summaries, not guarantees of deployment accuracy.
- Identical BoolQ passages are resampled together. Other shared topics, synthetic templates and time/provider dependence are not fully represented by case resampling.
- No multiple-comparison correction is applied. Degenerate intervals from uniform outcomes do not establish certainty or model equivalence. McNemar p-values additionally assume independent rows.
- Concurrent bulk request latency is excluded from comparative speed claims. Serial timing reduces three repeats to case medians and reports block medians; it cannot establish performance under other traffic or provider conditions.
- Luna reasoning is none. Native API/formatting overhead differs. Both expose the same answer object, but this does not equalize their architecture, training, tokenization or reasoning budget.
- Brier combines calibration and discrimination. Exact-probability arithmetic tasks do not establish real-world forecasting calibration.
- All attempted bills, including failed responses and warmups, remain accounted for. Missing bills remain unknown; per-1,000 costs project the observed workload only.
- Pilot60 and remaining3210 BoolQ subgroups are descriptive. No retrospective relabeling or outcome-based case removal is applied.

## Serial timing

| Task | Complete paired cases | Jev case-median seconds | Luna case-median seconds | Luna / Jev ratio [95% CI] |
|---|---:|---:|---:|---|
| boolq | 12 | 0.2403 | 1.1607 | 4.8296 [4.580866503596673, 5.501579804969975] |
| policy | 12 | 0.2455 | 1.2276 | 5.0002 [4.317739170424662, 5.402041667447928] |
| probability | 12 | 0.2394 | 1.1965 | 4.9977 [4.517802485268757, 5.813563405090273] |
| rte | 12 | 0.2443 | 1.2628 | 5.1699 [4.70460388839347, 5.4954292322251055] |
| wic | 12 | 0.2383 | 1.1404 | 4.7852 [4.605385357641358, 5.3953990524788535] |

Exact counts, Wilson intervals, paired cluster intervals, discordant counts, subgroup results, costs, and metadata are in summary.json.
