# Provider speed follow-up — advice only

Checked 2 October 2026 using public pages and unauthenticated endpoint metadata. No inference, credentials, routing changes or modifications to the active study were made.

## MiMo and Hy4

The current [MiMo-V2.6-Flash page](https://openrouter.ai/xiaomi/mimo-v2.6-flash) gives these standard-service P50 figures:

| Provider | Page's latency | Output throughput | Input / output per million tokens |
|---|---:|---:|---:|
| DeepInfra | 1.76 s | 35 tokens/s | $0.14 / $0.28 |
| Xiaomi | 4.87 s | 33 tokens/s | $0.14 / $0.28 |

The current [Hy4 preview page](https://openrouter.ai/tencent/hy4-preview) shows:

| Provider | Page's latency | Output throughput | Input / output per million tokens |
|---|---:|---:|---:|
| DeepInfra | 2.97 s | 17 tokens/s | $0.834 / $2.501 |
| SiliconFlow | 1.62 s | 50 tokens/s | $0.834 / $2.501 |
| Tencent | 4.28 s | 43 tokens/s | $0.834 / $2.501 |

The pages use a one-week window, all locations, and aggregate traffic; Hy4 explicitly has all reasoning efforts selected. These figures are candidate-selection evidence, not matched measurements of this study's compact answer protocol. OpenRouter's [provider-evaluation guide](https://openrouter.ai/blog/insights/evaluate-llm-provider-performance/) distinguishes first-token latency from output throughput. The study separately measures the time through the completed, parsed and validated response. Public aggregates cannot be substituted for it.

**Advice:** DeepInfra is a plausible faster MiMo route to investigate in a separate future provider comparison. SiliconFlow is the analogous Hy4 candidate. Public data alone does not establish the effect on accuracy, strict-schema behavior, reasoning-off observability or this account's complete-response latency. Keep the active pinned study unchanged and do not combine observations from a later route with its current primary arm.

## Groq and Cerebras alternative check

The closest documented candidate is **Qwen3.8 27B**, a different model from Qwen3.8 Flash already in the study.

- [Groq's exact model documentation](https://console.groq.com/docs/model/qwen/qwen3.8-27b) advertises approximately 450+ tokens/s and an instruct mode using `reasoning_effort=none`. Its [strict-output list](https://console.groq.com/docs/structured-outputs) includes this model. However, the current [OpenRouter endpoint response](https://openrouter.ai/api/v1/models/qwen/qwen3.8-27b/endpoints) contains **no Groq endpoint**. Upstream availability does not authorize or establish an OpenRouter route.
- [Cerebras' model catalog](https://inference-docs.cerebras.ai/models/overview) advertises about 1,850 tokens/s for Qwen3.8 27B. OpenRouter exposes `cerebras/fp16` at $0.99 input / $1.49 output per million tokens. That endpoint advertises reasoning controls but lists **neither `response_format` nor `structured_outputs`**. It does not meet the unchanged strict-schema requirement through OpenRouter. The [OpenRouter model page](https://openrouter.ai/qwen/qwen3.8-27b) shows 0.23 s and 115 tokens/s for Cerebras, further demonstrating that the provider's native speed advertisement and OpenRouter's observed aggregate are different quantities.
- Groq's strict-output documentation lists GPT-OSS 20B, GPT-OSS 120B and Qwen3.8 27B. GPT-OSS remains excluded because reasoning is mandatory. Llama and older Qwen alternatives are not listed for Groq strict schema; its JSON-object mode would change the study contract.

There is therefore **no verified Groq/Cerebras addition that satisfies the current OpenRouter-only, reasoning-disabled, strict-schema protocol** based on these public records. Qwen3.8 27B merits reconsideration if Groq appears on OpenRouter or Cerebras advertises and passes the required schema controls. A top-usage rank for this exact model was not established; it should not be described as another current top-ten choice.

The raw Qwen3.8 27B endpoint response and its retrieval URL, time and hash are saved under `sources/provider-speed-advice/`. The active frozen selection and all ongoing requests remain untouched.
