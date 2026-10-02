# Scientific audit and uncertainty supplement

## Status and reproducibility

The original response files, frozen cases, prompts, labels, scoring rules, and existing comparison page were not edited. `scientific_audit.py` independently recomputes the supplied summaries using only recorded data. `scientific-stats.json` contains exact estimates, nominal 95% Wilson intervals for binary accuracy, 20,000-draw paired percentile bootstrap intervals, seeds, raw-file hashes, checks, and explicitly labeled post-hoc sensitivity results.

All integrity checks passed. There are 360 measured main requests (180 cases × 2 models), 4 main warmups, 72 measured cache requests (24 cases × 3 arms), 1 cache-prime request, and 4 separate setup probes: 441 recorded calls. Every scored answer satisfies the two-field Noul schema; every bill is present and matches raw usage; all existing summary metrics reconcile. Case and runner-source hashes match their recorded metadata. Each measured case has exactly one request per arm, with matching frozen inputs and targets. Actual Luna cache payloads differ only by the explicit cache breakpoint. Main Luna and cache-off requests have zero reported cache reads and writes. All 24 measured cache-on requests report cache reads. Luna always resolved to OpenAI/GPT-6 Luna with zero reasoning tokens; Jev resolved to TypeSafe/Jev 1.13 dated 2026-09-17.

The all-call billed total is **$0.024075561**: main measured $0.011462728, main warmups $0.000087342, setup probes $0.000087342, cache measured $0.012075624, and cache prime $0.000362525. These are API-reported charges, not independently reconciled invoice/account-balance amounts.

## Main results: exact defensible conclusions

The comparison concerns two deployed configurations: Luna with reasoning set to `none`, and Jev's native Decisions API. Both produce the same answer object, but their API envelopes, tokenizers, backend processing, and formatting overhead differ. This estimates cost and latency to obtain one usable decision from those services.

| Task | Jev | Luna | Paired Luna − Jev (95% bootstrap interval) |
|---|---:|---:|---:|
| Policy accuracy | 60/60, 100%; Wilson 94.0–100% | 60/60, 100%; Wilson 94.0–100% | 0 percentage points; bootstrap [0, 0] is degenerate and does not establish equivalence |
| Policy Brier | 0.008312 | 0 | −0.008312 [−0.014859, −0.003795] |
| BoolQ published-label agreement | 57/60, 95.0%; Wilson 86.3–98.3% | 49/60, 81.7%; Wilson 70.1–89.4% | −13.3 percentage points [−23.3, −5.0] |
| BoolQ Brier against published labels | 0.068360 | 0.167615 | +0.099255 [+0.033590, +0.175719] |
| Exact-probability MAE | 0.064429 | 0.032770 | −0.031658 [−0.052212, −0.011477] |
| Exact-probability excess Brier | 0.009977 | 0.005510 | −0.004467 [−0.011735, +0.002057] |

**Interpretation:** The policy decisions were all correct for both models; Luna's endpoint probabilities produced a lower Brier score. Jev matched the published BoolQ labels more often. Luna had lower absolute error on the exact-probability tasks; the interval for the squared-error difference includes zero. These findings support task-specific tradeoffs, not an overall quality ranking. Brier combines calibration and discrimination; a lower Brier score alone does not demonstrate better general calibration.

Jev's observed median response time was 0.236–0.243 seconds across the three main tasks, versus 1.143–1.149 seconds for Luna. Ratios of medians (Luna/Jev) were 4.72 [4.27, 5.05] for policy, 4.86 [4.44, 5.12] for BoolQ, and 4.81 [4.48, 5.37] for exact probabilities. Luna's billed cost was approximately 2.19–2.24 times Jev's on these uncached workloads. These are end-to-end times including network, gateway/provider work, and parsing/validation; they are not time-to-first-token measurements.

## Cache experiment

| Arm | Accuracy (Wilson 95%) | Brier | Median / p95 seconds | Billed USD per 1,000 measured requests |
|---|---:|---:|---:|---:|
| Luna cached | 21/24, 87.5% [69.0%, 95.7%] | 0.125000 | 0.918 / 1.180 | $0.071305 |
| Luna uncached | 21/24, 87.5% [69.0%, 95.7%] | 0.125000 | 1.060 / 1.291 | $0.299175 |
| Jev | 20/24, 83.3% [64.1%, 93.3%] | 0.087867 | 0.254 / 0.337 | $0.132671 |

All 24 cached requests read 2,533 tokens apiece, and all uncached controls read and wrote zero cache tokens. Across the cached arm, 87.56% of billed prompt tokens were reported as cache reads. The priming request had zero reads and 2,533 written tokens.

- **Repeated-use cost:** cached/uncached mean billed cost ratio 0.238339 [0.238265, 0.238413], or 76.17% lower in this fixed workload. Every pair saved exactly $0.00022787, so the paired cost-difference interval is degenerate. This narrow uncertainty reflects the repeated prefix and deterministic billing, not certainty about future provider pricing or cache-hit rates.
- **Including this observed priming call:** cached/uncached total cost ratio 0.288828 [0.288759, 0.288898], or 71.12% lower after allocating the prime across 24 measured requests. This interval holds the single observed priming bill fixed; it does not estimate variability across future cold starts. The prime is an extra test request, not a matched production decision in the control arms.
- **Latency:** ratio of arm medians 0.865838 [0.800258, 0.962759]; cached Luna was 13.4% lower on this statistic. The mean paired difference was −0.194 seconds [−0.372, −0.073]. The existing cache report uses a different statistic—median of per-case latency ratios, 0.912556 [0.779504, 0.959343]. Name the statistic rather than mixing these two ratios.
- **Quality:** the two Luna arms had the same number correct, but disagreed on two cases (22/24 label agreement). Their accuracy difference was 0 percentage points [−12.5, +12.5], which does not establish equivalent predictions or equivalent quality. Luna cached − Jev accuracy was +4.17 points [−8.33, +16.67]; Brier difference +0.03713 [−0.06411, +0.15367]. This sample does not establish a quality advantage between those arms.
- **Against Jev:** cached Luna was cheaper per measured request (cost ratio 0.537457), but its median response time remained 3.61 times Jev's [3.40, 3.74]. With the observed priming request included, the cached-Luna/Jev cost ratio was 0.651312. Do not pool this long-prefix task with the short-input main tasks.

## Data quality and sensitivity

The existing passage-only BoolQ audit identifies 38 supported labels, 15 ambiguous cases, 6 cases with time/scope concerns, and one clear passage-label contradiction. These annotations were made after the scored run began; they are not preregistered exclusion criteria. The primary endpoint should be called **agreement with published BoolQ labels**, not verified factual accuracy.

The contradicted case is `boolq-dev-1842`: the published label says carbon is a metal while the passage describes it as nonmetallic. Both models answered no (Jev 0.03, Luna 0), and both were penalized by the published label. A clearly labeled post-hoc analysis excluding only this case gives Jev 57/59 (96.6%) and Luna 49/59 (83.1%); paired Luna − Jev is −13.56 points [−23.73, −5.08]. The directional published-label conclusion is unchanged. Do not silently replace the original score or remove the other ambiguous cases.

Two BoolQ rows (`0659` and `0383`) share a passage and paraphrase the same host-qualification question: 60 rows represent 59 unique passages. Synthetic tasks also share a small number of rule families or probability templates. A claim of 60 independent topics is unsupported.

## Required limitations in a paper-style report

1. This was a small, single-run exploratory pilot; one observation per model/case gives no direct estimate of within-case output variability. No overall winner, quality equivalence, broad forecasting calibration, or best-achievable model performance is established.
2. Wilson intervals assume independent Bernoulli trials; the bootstrap resamples case identities and assumes cases sufficiently represent the target population. Hand-selected boundary cases, shared templates, duplicated passage content, and a single cache rulebook weaken those assumptions. Treat the intervals as nominal/descriptive; they do not establish generalization to new families or domains.
3. Intervals are unadjusted across multiple endpoints. The 20,000-draw supplement is post hoc; it improves Monte Carlo precision over the original 2,000-draw summaries without changing scores or endpoints. Small differences between interval endpoints across the two analyses are expected. Choose and label one interval specification consistently in the revised report.
4. Public BoolQ may overlap model training data; published labels and passage sufficiency are imperfect. Source fidelity tests do not validate the truth of a label.
5. API/provider overhead is part of measured latency and price. Randomized paired arm order limits time-order confounding but cannot eliminate serial dependence, shared infrastructure, network conditions, or cache/provider drift. All calls used a single client and narrow time window.
6. Caching changed only request cache metadata between Luna arms, and observed counters confirm the treatment worked here. Savings depend on prefix length, reuse, actual cache reads, pricing, and priming policy. Narrow cost intervals do not forecast those conditions.
7. Luna returned generated numerical probabilities; Jev returned native Noul probabilities. Neither adapter extracted next-token probabilities. Exact-probability tasks assess arithmetic/interpretation against a known mathematical target; they are not real-world forecasting trials.
8. Logged costs are complete API usage charges in USD for these runs. Advertising per-token rates are contextual only. Report main measured, warmups/probes, cache repeated use, priming, and the combined observed bill separately.

## Structural/reporting issues

No raw-data or numerical-summary blocker was found. The main reporting issues are interpretive: the degenerate policy accuracy interval must not imply certainty/equivalence; BoolQ must be framed as published-label agreement; cache quality ties are not identical outputs; and the two different latency-ratio estimands must not be interchanged. The existing budget-abort subtotal edge is irrelevant to these complete, fully billed runs.
