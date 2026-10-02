# Scope of the frozen analysis plan

This clarification was written on 2 October 2026 while quality collection was
under way. It documents the relationship between preserved prospective records;
it does not retrospectively replace or relabel a frozen plan.

`analysis-spec.json` was saved at 15:32:22 UTC before any added-model outcomes.
Its model-name and chronology fields refer to the then-requested five-service
set: Jev, Luna, Gemini 3.8 Flash, GLM 5.3 Flash and Qwen3.8 Flash. Its statistical
methods already define comparisons for every explicitly supplied non-reference
model against Jev and Luna. Existing Jev/Luna quality outcomes were known.

The user's later requirement for disabled reasoning changed model eligibility.
The preserved prospective `model-selection-v1.json` (16:01:17 UTC) selected
DeepSeek V4.1 Flash, MiMo V2.6 Flash and Hy4 Preview, alongside the existing Jev,
Luna and Qwen3.8 Flash services. GPT-OSS-120B, Gemini 3.8 Flash and GLM 5.3 Flash
were excluded for mandatory reasoning. Version 2 (`model-selection.json`,
16:04:18 UTC) changed only the Hy4 provider after setup errors and before these
three models' primary quality calls. Both model selections precede the
16:05:44 UTC primary run containing these three services.

The final study therefore has six services. Metrics, thresholds, source cases,
reference models, bootstrap counts and clustering, failure accounting, and
separation of quality/timing/cache protocols are unchanged. The serial timing
and cache protocols include all six services. The original references to
"all five" describe the earlier requested scope; they are not the executed
model count. Exact executed specs and request plans are preserved per run.

Qwen's provider subsequently triggered the saved consecutive-failure stop twice.
A second operational continuation prospectively reduces the worker ceiling to
two and spaces dispatch claims by at least 0.25 seconds for remaining unattempted
cases. Its exact amendment is saved in the Qwen run before its recovery probes.
This changes the bulk schedule; primary quality metrics remain unchanged and
bulk latency is diagnostic only. Prior failed cases are retained, never retried.
