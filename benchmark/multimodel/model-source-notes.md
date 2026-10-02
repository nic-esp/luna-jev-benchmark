# Additional model capability verification

Checked 2 October 2026 using public OpenRouter metadata and primary documentation. No credentials, inference requests, or benchmark data changes were used for this check. This records advertised capabilities, not successful live request validation.

## Exact identities and reasoning compatibility

| Study name | OpenRouter request ID | Catalog canonical slug | Reasoning metadata | Compatibility with reasoning disabled |
|---|---|---|---|---|
| Gemini 3.8 Flash | `google/gemini-3.8-flash` | `google/gemini-3.8-flash-20260902` | Mandatory; efforts `high`, `medium`, `low`; default `medium` | **Not supported by the verified documentation** |
| GLM 5.3 Flash | `z-ai/glm-5.3-flash` | `z-ai/glm-5.3-flash-20260826` | Mandatory; efforts `max`, `high`, `low`; default `max` | **Not supported by the verified documentation** |
| Qwen3.8 Flash | `qwen/qwen3.8-flash` | `qwen/qwen3.8-flash-20260826` | Optional, on by default; token-budget control advertised | Reasoning can be explicitly disabled; validate the returned usage |

The user-facing Qwen choice is **Qwen3.8 Flash**. A bare “Qwen 3.8” name is ambiguous: the catalog also lists Omni Flash, 27B, Max and other variants. These are different models and must retain their exact names.

The [OpenRouter models API](https://openrouter.ai/api/v1/models) supplies the catalog fields above. Its [reasoning documentation](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens) says mandatory models reject `effort: "none"`. `reasoning.exclude: true` hides reasoning text while retaining reasoning computation and billing; it does not create a reasoning-disabled condition. A small completion limit can be consumed by reasoning before a visible answer is emitted.

Independent upstream checks agree:

- [Google’s Gemini 3.8 guide](https://ai.google.dev/gemini-api/docs/latest-model?hl=en) lists low, medium and high thinking and says minimal is unsupported. It also advises replacing token budgets with thinking levels for this model.
- [Z.AI’s GLM-5.3-Flash guide](https://docs.z.ai/guides/vlm/glm-5.3-flash) explicitly says thinking cannot be disabled.
- [Alibaba’s Chat Completions reference](https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-openai-chat-completions) maps Qwen’s `none` effort to disabled thinking. OpenRouter exposes the unified `reasoning` control; `reasoning: {"enabled": false}` is the explicit off request to validate for Qwen.

Consequently, the existing reasoning-disabled protocol cannot be claimed identical for all three additions. A low-effort Gemini/GLM condition would be a documented protocol difference, not reasoning disabled. Do not silently substitute older models, hide reasoning, or treat a missing reasoning-token field as zero.

## Provider routing and the shared JSON schema

| Candidate provider | Exact endpoint tag | Advertised `response_format` | Advertised `structured_outputs` | Observation |
|---|---|---|---|---|
| Google AI Studio, standard | `google-ai-studio` | Yes | Yes | Distinct from `/flex` and `/priority` service tiers |
| Google Vertex, standard global | `google-vertex/global` | Yes | Yes | Alternative provider; do not mix silently with AI Studio |
| DeepInfra GLM | `deepinfra/fp4` | Yes | Yes | Endpoint advertises FP4 quantization |
| Z.AI GLM | `z-ai/fp8` | Yes | **Not listed** | JSON formatting alone does not establish strict schema enforcement |
| Alibaba Qwen | `alibaba` | Yes | Yes | Sole Qwen3.8 Flash endpoint in the retrieved snapshot |

These are endpoint tags from the public endpoint API, not inferred display-name slugs. The public endpoint routes are [Gemini](https://openrouter.ai/api/v1/models/google/gemini-3.8-flash/endpoints), [GLM](https://openrouter.ai/api/v1/models/z-ai/glm-5.3-flash/endpoints), and [Qwen](https://openrouter.ai/api/v1/models/qwen/qwen3.8-flash/endpoints).

Keep the same `{"type":"noul","noul":p}` schema and current system instruction. The [structured-output guide](https://openrouter.ai/docs/guides/features/structured-outputs) uses `response_format.type="json_schema"`, `strict=true`, and recommends `provider.require_parameters=true`. Pin one exact endpoint and disable fallback. Continue local validation of exact keys, finite numeric probability, and the interval [0,1]; metadata support is not evidence that a particular request succeeded. Do not enable response healing, repair, or automatic retries as an unreported change.

## Caching: what is and is not established

The [OpenRouter prompt-caching guide](https://openrouter.ai/docs/guides/best-practices/prompt-caching) documents `prompt_cache_options` as **OpenAI-only**. Its explicit mode without a breakpoint is therefore not a verified cache-off control for Google, Alibaba or GLM. The guide describes Google implicit caching, Google explicit `cache_control` markers, and automatic Z.AI caching. Read/write counters use `usage.prompt_tokens_details.cached_tokens` and `cache_write_tokens` where reported. Missing counters remain unknown.

Provider metadata adds these observations:

- Google AI Studio and Vertex advertise `supports_implicit_caching=true`, cache-read prices and cache-write prices.
- Alibaba Qwen3.8 Flash advertises `supports_implicit_caching=true` and both cache prices. [Alibaba’s model page](https://www.alibabacloud.com/help/en/model-studio/qwen3-8-flash) independently lists implicit and explicit caching. However, the OpenRouter guide’s explicit-cache model list does not include Qwen3.8 Flash. Upstream support does not prove that OpenRouter forwards every control.
- The retrieved GLM endpoint records report `supports_implicit_caching=false` despite listing cache-read prices. That is inconsistent with the generic Z.AI automatic-cache documentation and is insufficient to label any GLM endpoint as cache-off. No controlled enable/disable pair was verified.

Recommended reporting consequence: until a provider-specific treatment and control are demonstrated, retain one long-input baseline arm with observed cache counters and an explicit control limitation. Do not call an unmarked request “uncached” merely because no explicit marker was sent. A positive cache read establishes a hit for that request; it does not establish a controlled on/off experiment. Session-affinity keys do not disable caching.

## Preserved public evidence

`sources/retrieval.json` records retrieval URLs, UTC times and SHA-256 hashes for the unmodified public API responses:

- `catalog.json`
- `gemini-3.8-flash-endpoints.json`
- `glm-5.3-flash-endpoints.json`
- `qwen3.8-flash-endpoints.json`

These snapshots contain public model/provider metadata, not benchmark API credentials or model responses. List prices, endpoint status, quantization and capabilities can change; the study must retain actual served model/provider identities and returned usage for each later inference request.
