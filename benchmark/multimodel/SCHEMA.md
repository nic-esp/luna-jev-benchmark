# Multi-model offline analysis interface

The frozen statistical plan is `analysis-spec.json`. The current six-service scope is recorded in [analysis-scope.md](analysis-scope.md) and `model-selection.json`; the original frozen files retain their historical five-service names. No module here makes model calls. Earlier Jev/Luna quality records are read in place; source files are never changed.

## Python API

```python
summary = analyze(
    quality_dirs=[original_quality_directory, new_quality_directory],
    timing_dir=new_interleaved_timing_directory,  # optional
    cache_dirs=[new_cache_directory],             # optional; sessions kept separate
    cases_path=frozen_quality_cases_path,         # defaults to expanded/data/cases.jsonl
    timing_cases_path=frozen_timing_cases_path,   # defaults to expanded/data/timing-cases.jsonl
    models=["Jev", "Luna", "Qwen3.8 Flash", "DeepSeek V4.1 Flash",
            "MiMo V2.6 Flash", "Hy4 Preview"],  # explicit current six-service set
    output_dir=output_directory,
    bootstrap_samples=5000,
    seed=20261002,
)
```

Every run directory supplies `metadata.json` and `responses.jsonl`. Active runs are rejected. Finished partial/stopped runs are descriptive, with missing attempts reported; verification requires completion. Each quality model/case identity may appear once across all supplied runs. Responses have the original schema, with `phase="quality"` and `repeat=0`; timing uses `phase="timing"`, repeats 0/1/2, and separate `timing_warmup` rows. Cache uses `phase="measured"` (or `cache_measured`), `cache_prime`, `pair_id`, `block`, arm `model_label`, optional `base_model_label`, and optional `prime_for_arm`.

`models` is flexible. The current set is Jev, Luna, Qwen3.8 Flash, DeepSeek V4.1 Flash, MiMo V2.6 Flash and Hy4 Preview. Gemini 3.8 Flash and GLM 5.3 Flash were excluded because they cannot satisfy the requested zero-reasoning mode. Pass the six labels explicitly through `models=` or `--models` for the current study; do not rely on the historical `DEFAULT_MODELS` ordering. Configuration changes and service-recovery continuations retain their prospective amendments. Do not rewrite the original frozen specification or analysis defaults.

Outputs: `summary.json` and `analysis.md` in the selected output directory. JSON uses null for undefined values. Probability errors use a 0–1 scale; multiply by 100 for percentage points. Prices are USD.

## Report-friendly summary

- `models`: ordered labels.
- `quality_sources[]`: absolute source run path, raw SHA256, full metadata, all-phase cost.
- `model_provenance[label]`: source paths, first/last start times, requested model IDs, returned model/provider pairs. Preserve these when comparing reused and later quality observations.
- `quality`: attempts, expected_attempts, missing_attempts, all-source/quality-phase costs. `bulk_latency_diagnostic_only` must not drive comparative speed claims.
- `tasks[task]` (alias `experiments`): `expected_cases`, `target_kind`, `models`, `comparisons`.
- `tasks[task].models[label]`: original metric structure plus expected_cases and missing_attempts. `attempts`, `valid`, `invalid`, `success_rate`, `success_rate_wilson95`, `quality`, `cost`.
- Label `quality`: `correct`, `accuracy_all_attempts`, `accuracy_all_attempts_wilson95`, `balanced_accuracy_all_attempts`, class denominators, valid-only accuracy/BA, `brier_valid_only`, `log_loss_valid_only`, `proper_score_denominator`.
- Probability `quality`: `mae_valid_only`, `rmse_valid_only`, `excess_brier_valid_only`, `expected_brier_valid_only`, `error_score_denominator`.
- Every `cost`: requests, known_billed_usd, observed_bills, missing_bills, total_billed_usd, usd_per_1000_attempts. Full total and projections are null if any bill is missing. Quality label costs additionally contain usd_per_correct_answer.
- `tasks[task].comparisons["MODEL / REFERENCE"]`: model, reference, direction, paired_cases, unpaired_cases, valid_response_pairs, invalid_response_pairs, resampling_clusters, and metrics. Every difference is MODEL minus REFERENCE. Each metric has estimate, ci95, defined_bootstrap_draws. Added models compare against both Jev and Luna; Luna/Jev is included once. `correctness_pairs` uses model_only_correct/reference_only_correct.

## Timing

`timing` is null unless supplied. It contains only the new interleaved session.

- source metadata, models, measured/warmup attempts, expected_measured_attempts, missing_measured_attempts, costs, comparative_speed_eligible.
- `tasks[task]`: expected_cases, case_union, common_complete_cases, excluded_from_common_support, common_case_ids.
- `tasks[task].models[label]`: `all_attempts` latency distribution, complete_case_count, primary_case_median_latency_s, case_median_distribution.
- `comparisons["MODEL / REFERENCE"]`: primary contrasts restricted to the same common complete case set across every participating model; metrics ratio_of_case_medians, median_of_case_ratios, mean_case_latency_difference_s.
- `pairwise_complete_sensitivity`: separate contrasts using complete paired support, preserving usable observations if a third model fails.
- `repeat_blocks["0"|"1"|"2"][label]`: per-block all-attempt latency statistics.

Ratios are MODEL divided by REFERENCE. A ratio of 2 means the model took twice as long. Warmups contribute costs but no comparative latency point estimate. A complete case has valid finite timing in all three repetitions.

## Cache

`cache_sessions[]` keeps each source session separate. Arms may be base labels, `MODEL uncached`, or `MODEL cached`. All model/case responses and both counterfactual cases remain paired while resampling pair_id inside each fixed block.

- `arms[arm]`: original describe metrics, latency, prime_attempts, priming_cost, setup_inclusive_cost, setup_inclusive_usd_per_1000_decisions.
- `comparisons["ARM / REFERENCE"]`: paired_cases, valid_response_pairs, clusters, fixed_blocks, metrics. Available arms compare against Jev/Luna controls and each model's own cached/uncached pair.
- Metrics: accuracy_difference, brier_valid_pairs_difference, median_paired_latency_ratio, mean_latency_difference_s, cost_ratio, label_agreement_valid_pairs.
- `blocks[block][arm]`: quality/cost and latency summaries.
- Missing measured billing suppresses a cost ratio and its interval. Missing prime billing suppresses setup-inclusive costs, leaving measured costs intact.

## Verification

```python
result = verify_run(
    run_dir, cases_path,
    extra_cases=warmup_or_prime_case_objects,
    expected_models=eligible_base_model_labels,
    payload_factory=payload_factory,  # optional exact adapter regeneration
    plan_factory=plan_factory,        # optional seeded plan regeneration
)
```

`payload_factory(case, model_label, row, metadata)` returns `(endpoint, payload)`.
`plan_factory(case_list, metadata)` returns the saved plan object.

Factories enable exact full-payload and seeded-plan checks. Without them, benchmark/timing source content, strict schema, requested settings, response readback, plan identities and billing are checked, but full cache payload regeneration and seeded schedule reproduction are explicitly unestablished. The base verifier expects `plan.json`: either one request per entry with case_id/model/phase/repeat or grouped quality entries with case_id/models.

Metadata models may be a mapping `label -> spec` or a list of specs containing label. Spec fields: id, provider, optional reasoning (default effort:none), optional response_model_ids, optional response_provider_names. Optional code_hashes/source_hashes map paths to SHA256. Missing returned reasoning/cache counters are counted as unknown rather than verified zero. The result distinguishes supplied exact factories from basic contract checks.

### Exact runner wrappers

`verify_protocol(run_dir)` automatically regenerates a completed new quality, timing or cache run using its frozen specs, seed and prefix identifiers, without constructing a credential or opening a connection. It checks source hashes, saved code snapshots, request hashes, response readback and durable dispatch identities. Timing warmups and cache primes are recovered automatically. CLI: `python3 multimodel/verify.py RUN_DIR --output VERIFICATION_JSON`.

`verify_study(quality_dirs, timing_dir=None, cache_dirs=None, models=None, output_path=None)` also verifies reused original quality using the original independent verifier, checks the complete quality case/model cross product and combines every supplied protocol audit.

The figure generator `figures.py` consumes only a completed summary. It produces `figures/quality.svg`, `probability.svg`, `timing.svg` and `cache-1.svg`, with matching PNG exports and `manifest.json`. It is flexible in model count and label. Figure manifests retain plotted values, denominators and source hashes. Run through the existing `work/figure-venv/bin/python` environment. No installation or inference call is needed.

## Completed-study commands

`verify_completed_study.py` accepts explicit quality directories, one timing directory and one cache directory. It adds setup/recovery probes, checks preserved continuation evidence and writes a combined verification that passes only when every component passes.

After that verification passes, `analyze_completed_study.py` accepts the same explicit directories and all six `--models`. It checks that the verified inputs are unchanged, acquires the shared phase lock, computes 5,000 bootstrap replicates, renders figures and builds the local report. Pass `--figure-python` if Matplotlib/NumPy are available in a different Python runtime. It performs no controller, credential, network or model calls. Neither helper waits for live jobs; an active phase or incomplete source causes it to stop.

`verify_aborted_cache.py RUN_DIR` independently checks the aborted 55-call cache ledger: the original 54-response prefix, immutable dispatches and plan, regenerated requests, one lost outcome retained as unknown, and all known/unknown billing. It writes `closure-verification.json`. The final helper discovers these closure sessions automatically and records them under `supplementary_aborted_cache`; they contribute to accounting only.
