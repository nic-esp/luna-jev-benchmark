# Luna prompt-cache experiment

Run status: **complete**. Cache status: **observed cache hits and clean uncached control**.

Twenty-four paired synthetic equipment-policy decisions use one frozen rulebook. Ground truth comes from a deterministic oracle, checked against separately frozen expected labels. All three arms receive the full rulebook and case. Luna uses the same message text, segmentation, output schema, and OpenAI provider in both arms; only cache metadata differs. Reasoning is set to none.

| Arm | Calls | Valid | Accuracy | Brier ↓ | Median seconds ↓ | p95 seconds ↓ | Cost USD | Cache hits | Read tokens | Write tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Luna cached | 24 | 24 | 0.8750 | 0.1250 | 0.918 | 1.180 | 0.001711 | 24/24 observed | 60792 | 0 |
| Luna uncached | 24 | 24 | 0.8750 | 0.1250 | 1.060 | 1.291 | 0.007180 | 0/24 observed | 0 | 0 |
| Jev | 24 | 24 | 0.8333 | 0.0879 | 0.254 | 0.337 | 0.003184 | 0/0 observed | unknown | unknown |

## First request and repeated use

The distinct cache-priming request cost **$0.000363** and took **1.204 seconds**. Its reported cache reads were **0 tokens** and writes were **2533 tokens**. It is excluded from the measured accuracy and steady-state latency table.

Cached-arm measured calls plus priming cost **$0.002074**, or **$0.000086 per measured case** after allocating the setup request. A zero reported read count on the first request is the evidence required to call this cold-inclusive; that condition was **observed**.

## Paired comparisons

Ratios below 1 favour the arm before the slash. Ratios use the same cases where both requests were valid and cost was reported.

| Pair | Complete pairs | Median latency ratio | Total cost ratio | Label agreement |
|---|---:|---:|---:|---:|
| Luna cached / Luna uncached | 24 | 0.9126 | 0.2383 | 0.9167 |
| Luna cached / Jev | 24 | 3.6180 | 0.5375 | 0.8750 |
| Luna uncached / Jev | 24 | 3.9186 | 2.2550 | 0.8750 |

## Interpretation

Accuracy uses probability ≥ 0.5 as yes and counts invalid outputs as incorrect. Brier score is mean squared probability error on valid outputs; lower is better. Latency measures the full non-streaming request from this machine, including network time. Arm order is random within each case. The priming request is first, so its latency can include initial connection setup. No exact-response cache or response replay is used.

This is a small synthetic policy-following pilot. It can reveal output, threshold, exception, cost, and latency differences for these prompts; it does not establish broad model quality. Missing bills or cache counters make their full totals unknown. Known subtotals and observation counts remain available in summary.json. A clean uncached control requires observed zero reads and zero writes on every control call. No cache benefit is established unless the cached arm reports actual reads and the control is verified. Repeated-use costs above are measured after a priming request and need not represent a stable cache-hit rate in other workloads.

Cache controls and usage fields follow the [OpenRouter prompt-caching documentation](https://openrouter.ai/docs/guides/best-practices/prompt-caching). The request payloads, raw responses, exact rulebook, frozen cases, and seed are saved with this report. Reported costs are provider usage charges; no currency conversion or extrapolated list-price savings is substituted.
