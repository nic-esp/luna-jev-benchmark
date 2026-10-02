# Selection for the reasoning-disabled comparison

Selection frozen on 2 October 2026 at 16:01:17 UTC, before probes or benchmark outcomes from these three additions. The immutable initial artifact is `model-selection-v1.json` (SHA-256 `1f3e0ba21c50863cfbcaf5137b62d4935c1f2c1b43fa9d12f2e0bc00b7a6c425`). It retains Jev, GPT-6 Luna and Qwen3.8 Flash and adds the following models.

| Study label / OpenRouter ID | Pinned endpoint and returned provider | Input / output USD per million tokens | OpenRouter daily token rank |
|---|---|---:|---:|
| DeepSeek V4.1 Flash — `deepseek/deepseek-v4.1-flash` | `deepinfra/fp8` / DeepInfra | $0.14 / $0.42 | 2 |
| MiMo V2.6 Flash — `xiaomi/mimo-v2.6-flash` | `xiaomi/fp8` / Xiaomi | $0.14 / $0.28 | 4 |
| Hy4 Preview — `tencent/hy4-preview` | `deepinfra/fp8` / DeepInfra | $0.834 / $2.501 | 8 |

Prices are from the exact selected endpoints, not the model catalog's cheapest advertised route. They can change; each response's recorded bill remains the price evidence. All three selected endpoints advertise `reasoning`, `response_format` and `structured_outputs` and had status 0 in the preserved endpoint response.

## Why these models are eligible for setup checks

- **DeepSeek:** its [Chat Completions reference](https://api-docs.deepseek.com/api/create-chat-completion/) supports disabled thinking and says `reasoning_effort=none` turns thinking off. The [release log](https://api-docs.deepseek.com/updates/) identifies `deepseek-flash` as V4.1 Flash. The native DeepSeek endpoint only advertises JSON-object formatting; the selected DeepInfra endpoint explicitly advertises strict structured outputs.
- **MiMo:** the [exact MiMo-V2.6-Flash model page](https://mimo.mi.com/models/en-US/mimo-v2.6-flash) includes a request with disabled thinking and lists structured output support. OpenRouter's selected Xiaomi endpoint advertises both relevant formatting parameters.
- **Hy4:** [Tencent's thinking-mode documentation](https://intl.cloud.tencent.com/zh/document/product/1300/80637) lists Hy4 preview under models supporting disabled thinking and specifies the `none` effort option. The selected DeepInfra endpoint also advertises the required schema support.

OpenRouter catalog metadata marks all three models' reasoning as optional. Use the unchanged system instruction, `reasoning.effort=none`, strict probability schema, 128-token ceiling, exact provider pin, and disabled fallback. [OpenRouter's reasoning documentation](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens) distinguishes disabling reasoning from merely hiding its text. Setup checks and the full run must audit returned counters: positive reasoning or visible reasoning fails this condition; a missing counter remains unverified.

## Popularity and speed limits

[OpenRouter rankings](https://openrouter.ai/rankings) showed the daily ranks above for public token usage through **1 October 2026**. These rank positions describe usage, not accuracy, unique users or spend. The saved facts include the source, date, metric and attribution. Source: OpenRouter (openrouter.ai/rankings), as of 2026-10-01; rankings are licensed CC BY 4.0.

The public endpoint API returned null recent latency and throughput for the selected routes. The selection therefore does not establish the fastest provider. Comparable short-answer latency comes from the study's interleaved serial test. Optional reasoning documents an off switch; external API observations cannot reveal every internal computation.

## Exclusions and cache treatment

`openai/gpt-oss-120b`, Gemini 3.8 Flash and GLM 5.3 Flash declare mandatory reasoning and are excluded. Low effort and hidden reasoning do not satisfy the user's off requirement. Nemotron 3 Ultra was considered as a fallback, but its observed top-seven rank belongs to the free variant, which lacks advertised strict schema support. The paid route would be a different popularity comparison.

No matched cache enable/disable control has been verified for the additions. Each receives one long-input baseline with returned cache counters; omitted cache markers do not establish an uncached condition.

## Evidence files

`sources/zero-reasoning-selection/` contains the unmodified public catalog and endpoint JSON, a factual extraction from the rankings page, and `retrieval.json` with source URLs, save times and hashes. The direct HTML download returned HTTP 403; the rankings facts were read from the public page through the web tool and are explicitly identified as an extraction. Research used no credentials or model calls.

## Provider amendment before primary calls

At 16:04:18 UTC, version 2 in `model-selection.json` retained the three selected
models and changed Hy4's pin to **Tencent (`tencent/fp8`)**, after the DeepInfra
setup endpoint returned two HTTP 429 overload errors. The earlier version and
both failed probes remain in the archive. No primary case for these additions
had been attempted when this routing amendment was saved. The native Tencent
endpoint advertises the same required formatting/reasoning parameters. Its base
rates are $0.834 input and $2.501 output per million tokens, with a captured
time-of-day override; actual response bills determine measured cost.

The table above records the initial selection. The report and primary requests
use version 2's exact endpoints. Both snapshots retain their original hashes.
