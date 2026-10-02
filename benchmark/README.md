# Luna versus Jev

A small, reproducible OpenRouter comparison of **speed, billed price, and answer quality**. Python 3.9 or later; no third-party packages required.

## Models and matched output

| Model | Requested ID | API |
|---|---|---|
| GPT-6 Luna | `openai/gpt-6-luna` | OpenRouter Chat Completions, OpenAI provider, no fallback |
| Jev 1.13 | `typesafe/jev-1.13` | OpenRouter Decisions API |

Both answer with the same two-field object:

```json
{"type":"noul","noul":0.73}
```

`noul` is P(yes). The binary distribution is therefore `{yes: 0.73, no: 0.27}`. A yes/no decision is derived using the frozen threshold **P(yes) >= 0.5**. Luna uses strict JSON Schema, 128 maximum output tokens, and `reasoning.effort = none`. Its predictions are generated numerical probabilities, not next-token probabilities. Each provider's outer API envelope differs; the answer object matches exactly.

The same state, question and yes/no criteria reach both models. No ground-truth labels or source metadata are sent. Luna additionally receives the format instruction needed to produce Jev's native answer object. Tokenization and native API overhead differ; the measured price is the actual cost of obtaining a decision with each service.

## Main experiments

| Experiment | Cases | Validation | Primary quality measure |
|---|---:|---|---|
| Policy decisions | 60 | Three fictional rule families evaluated in Python; 30 yes and 30 no | Accuracy; Brier score |
| Reading comprehension | 60 | Seeded uniform sample of published BoolQ development labels | Accuracy; Brier score |
| Known probabilities | 60 | Exact rational probabilities from counts, without-replacement draws, and mixtures | Probability MAE and RMSE |

Cases and prompts were frozen before the scored run. Seed: `20261002`. `data/manifest.json` records hashes, source attribution, selected BoolQ row indices, and limitations. The original Google-hosted file returned HTTP 403, so the Google-owned Hugging Face mirror supplied the same named validation split. Its normalized local-file hash is recorded explicitly.

For labeled tasks, Brier = mean `(p-y)^2`; lower is better. Log loss uses natural logs and clips probabilities only for scoring. For known-probability tasks, expected Brier = `q*(1-p)^2 + (1-q)*p^2`; excess Brier = `(p-q)^2`. A sampled random outcome is never substituted for the known probability `q`.

## Speed and cost protocol

- One question per request; no tools or generated explanations.
- Sequential calls, randomized case order and randomized model order within each pair.
- Two warmup cases per model excluded from measured results. Setup probes are also excluded.
- A shared persistent HTTPS connection, with normal reconnection on errors.
- Latency runs from HTTP request preparation through receipt, parsing and validation of the complete response. It includes network and service time; it is not time to first token.
- No automatic retries and no answer repair. Invalid answers remain visible as failures.
- Billed USD comes from OpenRouter `usage.cost`, including output/reasoning charges where applicable. Missing billing is unknown, never zero.
- Cost per 1,000 decisions is a projection from this workload, not a universal price.
- A conservative request reservation and cumulative reported-cost guard stop the run at a configurable budget. A changing upstream rate or delayed charge can prevent this client-side guard from being an absolute billing guarantee.
- The main Luna run explicitly disables prompt caching. The cache experiment measures it separately.

## Cached-input experiment

`cache_experiment.py` compares Luna with and without caching, plus Jev, on the same long fictional policy reference and varied records. The Luna text and segmentation match across cache arms; cache controls differ. A reusable prefix is explicitly marked for caching. The uncached arm uses explicit mode without any breakpoint.

The report checks actual `cached_tokens` and `cache_write_tokens`, shows the initial priming call separately, and includes its price in the cold-start total. Caching is only claimed when the API reports cache reads. This is prompt-prefix caching: every request still produces a new answer.

## Run

Run these commands from this folder. Supply the key through the environment or a secure stdin mechanism; do not put it in source files or command arguments.

```sh
python3 -m unittest discover -p 'test_*.py' -v
python3 runner.py --dry-run
python3 runner.py --budget 1
```

`runner.py` reads `OPENROUTER_API_KEY` by default. `--key-stdin` reads one line from stdin. `--limit N` supports a smaller check. Each run writes a new directory under `runs/` containing requests, responses, timestamps, exact served model IDs, charges, report files and metadata. No API key is saved.

The frozen `data/cases.jsonl` is sufficient to rerun the benchmark. Regenerating the same sample additionally requires the original normalized BoolQ mirror download referenced in the manifest:

```sh
python3 datasets.py --help
python3 cache_experiment.py --help
```

## How to interpret the result

This pilot compares two deployed configurations on a small, specified workload. It does not establish overall model quality. Sixty cases per task leave substantial uncertainty; paired bootstrap intervals resample case identities. BoolQ is a long-public dataset and may overlap with model training. Its labels are human annotations rather than an exact mathematical oracle. Synthetic cases test the supplied rules and sampling mechanisms; they do not demonstrate real-world forecasting calibration. The cache test's benefit depends on prefix length, actual reuse, and cache hit rate.

The policy sample is balanced within each rule family but sampled uniformly within each label. Override branches can therefore be common; exact boundary sensitivity is not exhaustively covered.

The initial run fixes Luna's reasoning to `none`; another reasoning level would be a separate configuration. Results should not be presented as Luna's best achievable quality at any latency or price.

## Existing tools checked

[Promptfoo](https://www.promptfoo.dev/docs/providers/openrouter/) already supports OpenRouter, cost reporting and custom providers. [Inspect](https://inspect.aisi.org.uk/) supports custom scoring and evaluation logs. Jev's separate Decisions endpoint still requires an adapter. This requested small test uses a standard-library runner to keep installation and rerunning simple; the frozen cases and adapters can later be moved into either framework.

## Sources

- [OpenRouter Jev tutorial and native answer schema](https://openrouter.ai/docs/guides/community/jev-tutorial)
- [OpenRouter Jev pricing](https://openrouter.ai/typesafe/jev-1.13)
- [OpenRouter Luna pricing](https://openrouter.ai/openai/gpt-6-luna)
- [OpenRouter prompt-cache controls and usage fields](https://openrouter.ai/docs/guides/best-practices/prompt-caching)
- [OpenAI GPT-6 Luna settings](https://developers.openai.com/api/docs/models/gpt-6-luna)
- [BoolQ primary repository](https://github.com/google-research-datasets/boolean-questions)
- [BoolQ dataset mirror](https://huggingface.co/datasets/google/boolq)
- [BoolQ paper](https://arxiv.org/abs/1905.10044)

BoolQ selected passages and annotations retain their [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/) license. Attribution and changes are recorded per case in `source`.
