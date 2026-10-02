# Model comparison analysis

All uncertainty intervals are exploratory; quality is reported by task.

| Task | Model | Valid / attempted | Accuracy | Brier | MAE | USD / 1,000 |
|---|---|---:|---:|---:|---:|---:|
| boolq | Jev | 3270/3270 | 0.9177 | 0.0624 | unknown | 0.019234 |
| boolq | Luna | 3270/3270 | 0.8639 | 0.1254 | unknown | 0.042965 |
| boolq | Qwen3.8 Flash | 3196/3270 | 0.8817 | 0.0875 | unknown | unknown |
| boolq | DeepSeek V4.1 Flash | 3270/3270 | 0.8746 | 0.1084 | unknown | 0.046545 |
| boolq | MiMo V2.6 Flash | 3270/3270 | 0.8890 | 0.0954 | unknown | 0.029242 |
| boolq | Hy4 Preview | 3266/3270 | 0.8896 | 0.1030 | unknown | unknown |
| policy | Jev | 300/300 | 1.0000 | 0.0052 | unknown | 0.020176 |
| policy | Luna | 300/300 | 0.9967 | 0.0033 | unknown | 0.044276 |
| policy | Qwen3.8 Flash | 293/300 | 0.9767 | 0.0000 | unknown | unknown |
| policy | DeepSeek V4.1 Flash | 300/300 | 0.9933 | 0.0067 | unknown | 0.014697 |
| policy | MiMo V2.6 Flash | 300/300 | 0.9867 | 0.0137 | unknown | 0.017260 |
| policy | Hy4 Preview | 300/300 | 0.9533 | 0.0467 | unknown | 0.149457 |
| probability | Jev | 299/300 | unknown | unknown | 0.0764 | unknown |
| probability | Luna | 300/300 | unknown | unknown | 0.0244 | 0.041474 |
| probability | Qwen3.8 Flash | 291/300 | unknown | unknown | 0.0610 | unknown |
| probability | DeepSeek V4.1 Flash | 300/300 | unknown | unknown | 0.0383 | 0.042922 |
| probability | MiMo V2.6 Flash | 300/300 | unknown | unknown | 0.0589 | 0.026370 |
| probability | Hy4 Preview | 300/300 | unknown | unknown | 0.0489 | 0.204514 |
| rte | Jev | 277/277 | 0.9061 | 0.0718 | unknown | 0.016811 |
| rte | Luna | 277/277 | 0.8700 | 0.1103 | unknown | 0.037107 |
| rte | Qwen3.8 Flash | 270/277 | 0.8448 | 0.1113 | unknown | unknown |
| rte | DeepSeek V4.1 Flash | 277/277 | 0.8520 | 0.1283 | unknown | 0.038352 |
| rte | MiMo V2.6 Flash | 277/277 | 0.8375 | 0.1232 | unknown | 0.021414 |
| rte | Hy4 Preview | 277/277 | 0.8700 | 0.0945 | unknown | 0.180937 |
| wic | Jev | 638/638 | 0.7398 | 0.1718 | unknown | 0.016247 |
| wic | Luna | 638/638 | 0.7288 | 0.2600 | unknown | 0.035907 |
| wic | Qwen3.8 Flash | 621/638 | 0.7320 | 0.2233 | unknown | unknown |
| wic | DeepSeek V4.1 Flash | 638/638 | 0.7445 | 0.2195 | unknown | 0.038077 |
| wic | MiMo V2.6 Flash | 638/638 | 0.6959 | 0.2265 | unknown | 0.018960 |
| wic | Hy4 Preview | 638/638 | 0.7210 | 0.2414 | unknown | 0.172176 |

## Limits

- All intervals are exploratory and unadjusted for multiple comparisons. Wilson marginal intervals assume independent rows.
- Earlier Jev/Luna quality outputs are reused. Model time windows and providers differ; quality comparison is conditional on the saved observations.
- Only the new all-model interleaved timing session supports comparative speed claims. No concurrent bulk timing is pooled into it.
- Invalid outputs count as wrong for accuracy. Proper scores require valid probabilities. Missing attempts and missing bills remain explicit.
- Public labels can be wrong and may overlap training data. Shared synthetic templates and one-session sampling limit generalization.
- Schema equality does not equalize tokenization or internal computation. Reasoning configuration is declared per model; observed counters are audited separately.
- Cache sessions are summarized separately. Cost savings depend on verified cache hits, prefix length, reuse and reported billing.
