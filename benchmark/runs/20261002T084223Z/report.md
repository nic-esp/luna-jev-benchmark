# Jev / Luna benchmark

Speed, cost, and validated quality, with identical response schemas.

Measured requests: **360** · Valid responses: **360** · Warmups: **4**.

Known billed cost including warmups: **$0.011550**. Missing bills: **0**.

## Reading comprehension · BoolQ

| Model | Valid / requests | Accuracy (valid) | Accuracy (all) | Brier | Log loss | p50 / p95 seconds | USD / 1,000 requests | USD / correct |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Jev | 60 / 60 | 95.0% | 95.0% | 0.068 | 0.248 | 0.236 / 0.372 | $0.019847 | $0.000021 |
| Luna | 60 / 60 | 81.7% | 81.7% | 0.168 | 3.676 | 1.148 / 1.470 | $0.044483 | $0.000054 |

Paired cases: **60 / 60**. Luna − Jev accuracy: **-13.33 pp [-21.67, -5.00]** (95% CI).
Luna / Jev ratio of median latency: **4.86× [4.42, 5.10]** (95% CI). Below 1 means Luna is faster.

- Jev: known measured billing $0.001191; 0 missing bills. Resolved models: typesafe/jev-1.13-20260917. Providers: TypeSafe.
- Luna: known measured billing $0.002669; 0 missing bills. Resolved models: openai/gpt-6-luna. Providers: OpenAI.

## Policy decisions

| Model | Valid / requests | Accuracy (valid) | Accuracy (all) | Brier | Log loss | p50 / p95 seconds | USD / 1,000 requests | USD / correct |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Jev | 60 / 60 | 100.0% | 100.0% | 0.008 | 0.067 | 0.243 / 0.331 | $0.020927 | $0.000021 |
| Luna | 60 / 60 | 100.0% | 100.0% | 0.000 | 0.000 | 1.149 / 1.647 | $0.045735 | $0.000046 |

Paired cases: **60 / 60**. Luna − Jev accuracy: **+0.00 pp [0.00, 0.00]** (95% CI).
Luna / Jev ratio of median latency: **4.72× [4.26, 5.05]** (95% CI). Below 1 means Luna is faster.

- Jev: known measured billing $0.001256; 0 missing bills. Resolved models: typesafe/jev-1.13-20260917. Providers: TypeSafe.
- Luna: known measured billing $0.002744; 0 missing bills. Resolved models: openai/gpt-6-luna. Providers: OpenAI.

## Exact probabilities

| Model | Valid / requests | MAE | RMSE | Excess Brier | Expected Brier | p50 / p95 seconds | USD / 1,000 requests |
|---|---:|---:|---:|---:|---:|---:|---:|
| Jev | 60 / 60 | 0.064 | 0.100 | 0.010 | 0.156 | 0.237 / 0.311 | $0.018645 |
| Luna | 60 / 60 | 0.033 | 0.074 | 0.006 | 0.152 | 1.143 / 1.596 | $0.041408 |

Paired cases: **60 / 60**. Luna − Jev mae: **-0.03 [-0.05, -0.01]** (95% CI).
Luna / Jev ratio of median latency: **4.81× [4.47, 5.37]** (95% CI). Below 1 means Luna is faster.

- Jev: known measured billing $0.001119; 0 missing bills. Resolved models: typesafe/jev-1.13-20260917. Providers: TypeSafe.
- Luna: known measured billing $0.002485; 0 missing bills. Resolved models: openai/gpt-6-luna. Providers: OpenAI.

## Interpretation and limits

- This is a small benchmark. Intervals describe variation across the sampled cases; they do not prove a general model ranking.
- Luna supplies generated numerical probabilities; Jev supplies its native Noul probabilities. Neither adapter extracts next-token probabilities. Brier combines calibration and discrimination; this small sample cannot establish general calibration.
- Each task has a separate quality scale. Quality is not pooled across tasks; no overall winner is inferred.
- BoolQ is public and may have appeared in training data. Reading-comprehension results can be affected by contamination.
- Paired comparisons include only cases with valid responses from both models. Check success rates and excluded pairs before comparing conditional quality.
- Latency includes the OpenRouter/provider/network path. Provider routing, caching, load, reasoning settings, and output length can change speed and price.
- The intended primary protocol is Luna reasoning=none, sequential randomized interleaving, and no retries. The recorded run metadata is the source of truth for whether that protocol was used.
- 95% intervals are exploratory percentile bootstrap intervals without multiple-comparison correction. Zero-width intervals can occur in a small or uniform sample.
- Costs are USD as reported by the runner. Missing billing remains unknown; cost per 1,000 is a sample-based projection, not a price guarantee.

## Metric definitions

- **success**: valid=true with a finite probability and valid target in [0,1]; label targets must be 0 or 1
- **accuracy**: Fraction correct among valid responses, predicting yes when p_yes >= 0.5
- **accuracy_all_requests**: Correct valid responses divided by every measured request; invalid requests count as failures
- **brier**: Mean (p_yes - observed_label)^2; lower is better
- **log_loss**: Mean binary negative log likelihood in natural-log units; p clipped to [1e-15, 1-1e-15]
- **mae**: Mean absolute difference between reported p_yes and exact reference probability
- **rmse**: Square root of mean squared error against exact reference probabilities
- **excess_brier**: Mean (p_yes - true_probability)^2: expected Brier above the optimal probability forecast
- **expected_brier**: Mean [(p_yes - true_probability)^2 + true_probability*(1-true_probability)]
- **latency**: End-to-end request time through receipt of a complete response; main table includes valid responses only
- **cost**: Recorded billed USD across all requests in the stated phase, including failed/invalid requests when billed
- **cost_per_1000**: Measured total billed cost / measured request count * 1000; withheld if any bill is missing
- **cost_per_correct**: All measured billed cost / number of correct valid label responses; withheld for missing bills or zero correct
- **bootstrap**: Percentile 95% paired bootstrap by case identity; quality repeats averaged within case; latency repeats use median; no CI for fewer than 2 pairs

## Run metadata

```json
{
  "models": {
    "Jev": "typesafe/jev-1.13",
    "Luna": "openai/gpt-6-luna"
  },
  "created_at": "2026-10-02T08:42:23.244948+00:00",
  "seed": 20261002,
  "cases": 180,
  "calls_planned": 360,
  "budget_usd": 0.999912658,
  "luna_reasoning": "none",
  "luna_provider": "OpenAI",
  "protocol": "One non-streaming request per model per case; sequential paired random order; one shared persistent HTTPS connection; no automatic retries; two warmups per model excluded from measured results.",
  "cases_sha256": "60e6db60c72869936548c3c4620aa3b070cf12e516f3be42a61a306d0f592660",
  "runner_sha256": "3a05cb20816fbc5b7c30a065429a7365afa9583f273d683bf69784f4af3e9ad7",
  "status": "complete",
  "charged_usd": 0.011550069999999994,
  "calls_completed": 364,
  "finished_at": "2026-10-02T08:46:47.575786+00:00"
}
```
