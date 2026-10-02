# Compact-decision model comparison

This directory contains the additional model adapters, frozen selection records,
quality and timing protocols, cache controls, analysis, figures and report builder
for the same study. `../expanded/data/` contains the unchanged 4,785 quality
cases and 60 timing cases. Earlier Jev/Luna quality responses are reused by case
identity; the primary timing and long-prefix protocols interleave the selected
services in new measurement sessions. Every session is identified in the report.

## Output and reasoning contract

Every chat model receives the saved system instruction and strict JSON Schema
for `{"type":"noul","noul":p}`, with finite `p` between zero and one, a
128-token completion ceiling, `reasoning.effort=none`, no tools and non-streaming
output. The decision threshold is 0.5. Jev supplies the same native typed answer.
The provider is pinned and automatic fallback is disabled. Raw API replies and
reported token counts are preserved; missing counters are unknown. Matching the
answer schema does not force identical tokenization or decimal precision.

`model-selection.json` records the selected exact IDs, endpoint-specific price
snapshots, reasoning support, popularity evidence and any setup-only provider
amendment. Earlier versions are retained. GPT-OSS-120B, Gemini 3.8 Flash and
GLM 5.3 Flash are excluded because their advertised reasoning is mandatory.
Popularity measures OpenRouter token usage, not accuracy or speed.

## Measurement and failures

- Quality: one attempt per frozen case/model; 16 workers process randomized case
  groups. After two episodes of shared-capacity failures, Qwen’s unattempted
  cases use a prospectively recorded two-worker ceiling and at least 0.25 seconds
  between dispatch claims. Prior attempts stay intact; payloads/provider do not
  change. Concurrent latency is diagnostic only.
- Timing: the same 60 cases, three sequential randomized blocks and two warmups
  per model/block. Primary contrasts use common complete support across models.
- Long-prefix protocol: the same 96 policy cases in four blocks, with matched
  counterfactual pairs and fresh prefix identifiers. Luna has verified explicit
  cache-on/off controls and one extra prime per block. Other services have one
  uncontrolled baseline, with their returned cache counters reported.
- No automatic retries or answer repairs. Failed attempts remain in accuracy,
  validity and billing accounting. A durable dispatch journal prevents replay
  when an interrupted quality run resumes.
- Missing charges remain null. A separate $0.01 allowance per unreported charge
  protects the local spending guard; it is not a measured bill. A mean over
  reported bills is explicitly distinguished from a complete cost projection.

The statistical plan was saved before added-model outcomes. Analysis reports
task-specific accuracy, balanced accuracy, proper scores, exact-probability
errors, paired uncertainty and billed costs. It does not produce an overall
model ranking. See `analysis-spec.json` and `SCHEMA.md` for definitions and the
analysis interface.

## Reproduce without model calls

From the `benchmark/` directory:

```sh
python3 multimodel/analyze.py --help
python3 -m unittest discover -s multimodel -p 'test_*.py' -v
python3 multimodel/build_report.py
```

The complete offline workflow uses the archived helpers. Supply the exact three
quality run directories (reused Jev/Luna, Qwen, and the three additions), plus the
completed common timing and cache runs:

```sh
python3 multimodel/verify_completed_study.py --quality QUALITY_REFERENCE QUALITY_QWEN QUALITY_ADDITIONS --timing TIMING_ALL --cache CACHE_ALL
python3 multimodel/analyze_completed_study.py --quality QUALITY_REFERENCE QUALITY_QWEN QUALITY_ADDITIONS --timing TIMING_ALL --cache CACHE_ALL --models "Jev" "Luna" "Qwen3.8 Flash" "DeepSeek V4.1 Flash" "MiMo V2.6 Flash" "Hy4 Preview"
```

The verification result retains input hashes, setup/recovery ledgers, source and
contract checks, counts, and complete-versus-unknown billing. It also discovers
aborted cache sessions through their saved closure audits, checks the original
response/dispatch prefixes and lost outcomes, and includes their charges in a
separate supplementary ledger. These rows never enter primary estimates. Use
`--supplementary-cache RUN_DIR` to supply those directories explicitly. The second command
requires that result to pass and every verified input to remain unchanged. It
uses 5,000 bootstrap draws, then renders figures and builds the local report.
It stops if a protocol holds the shared phase lock. Both helpers work offline
and read no credentials. Use `--figure-python /path/to/python` when the plotting
libraries are installed in a separate runtime. The canonical helper source and
tests are included here; workspace wrappers are optional conveniences.

The report builder requires a completed `multimodel/summary.json`. The analysis
source directories and hashes are embedded in that file. Figure generation uses
Matplotlib and NumPy:

```sh
python3 multimodel/figures.py --summary multimodel/summary.json
```

The public copy omits RTE premise/hypothesis passages, including their copies in
requests. Original study hashes describe the complete local data. The publication
manifest describes public bytes. Source-hash tests and fresh paid runs therefore
require restoring the original datasets using the documented source retrieval
and dataset builders first. The runner refuses altered source hashes before
making API calls. Public response scores, timings, charges and reference labels
remain intact.

## Evidence and credentials

`report-data/evidence.json` is a manifest for case data, compact call summaries,
CSV and 100-record raw chunks. It avoids one oversized combined response file.
All original run records remain in their own directories. Setup probes, warmups,
primes, failures, recovery probes and supplementary measurements contribute to
the complete ledger, with separate score denominators.

No API key or authorization header belongs in this repository. Live execution
uses a credential held only in process memory and sent only to OpenRouter.
Public exports remove account identifiers echoed in API errors as well as source
passages whose redistribution terms were not verified.

## Cache setup correction

The preserved first multi-service cache session
(`runs/cache-20261002T174228Z`) used a 66-character Luna cache key, exceeding
the provider's 64-character limit. Its 55 dispatched calls are retained as
supplementary setup evidence, including one interrupted call with an unknown
outcome. None are pooled into the primary cache estimates.

`cache_protocol_v2.py` shortens the administrative key to 46 characters and
validates all planned keys before dispatch. It preserves the input text, cases,
model settings, schema, seeded order and treatment definitions. A failed prime
is saved and then stops the session. The original protocol source and aborted
evidence remain unchanged apart from explicit closure metadata and the retained
unknown-outcome record, with original byte-prefix hashes checked.
