# GPT-6 Luna and Jev: decision quality, latency and cost

[Read the scientific report](https://nic-esp.github.io/luna-jev-benchmark/) · [Download the public study](https://nic-esp.github.io/luna-jev-benchmark/benchmark.zip)

A reproducible OpenRouter comparison evaluated on 2 October 2026. Luna was constrained to Jev’s exact probability answer schema, with strict JSON Schema and reasoning disabled. The same schema does not imply identical reported token counts: actual Luna outputs used 20–28 tokens; Jev reported 20.

## Study

- **Quality:** 4,785 cases per model, covering full BoolQ, RTE and WiC validation splits plus exact policy and probability tasks.
- **Timing:** 60 frozen cases, three serial repeats per model, with warmups accounted separately.
- **Caching:** 96 cases across four freshly primed prefixes, including uncached controls.
- **Complete record:** 10,675 attempts including the original pilot; 10,674 valid responses. Known charges total $0.374134623; one failed request has no reported bill.

Jev had higher BoolQ label agreement and was about five times faster in serial timing. Luna had lower error on exact synthetic probabilities. Cached Luna cost 35.4% less than Jev on the long-rulebook task after allocating all four priming calls. Results depend on the task, model settings and this measurement session; this is not an official leaderboard result.

## Evidence and reproduction

The site includes figures, full methods, paired uncertainty estimates, every numerical result, prompts, usage, prices and an interactive case/call explorer. It is static: no API key or application server is needed to read it.

See [expanded protocol and reproduction](benchmark/expanded/README.md), [frozen analysis specification](benchmark/expanded/analysis-spec.json), [verification](benchmark/expanded/verification.json), and [original pilot](pilot.html).

This public copy **omits RTE premises and hypotheses**, including copies in submitted requests, because exact upstream redistribution terms were not verified. It retains labels, predictions, IDs, numbers, original-source hashes and retrieval code. The complete original research artifact remains local. [Dataset attribution and terms](THIRD_PARTY_DATA.md) apply separately to BoolQ, WiC and other source material. Original-source integrity tests require retrieval of the original inputs first; a public-copy hash is not an experimental-input hash.

[PUBLICATION_MANIFEST.json](PUBLICATION_MANIFEST.json) records public file hashes and transformations. API keys and authorization headers are excluded.

## Serve locally

```sh
python3 -m http.server 8767
```

Open `http://127.0.0.1:8767/`. Report generation uses saved evidence and does not call models. Fresh evaluation calls are a separate paid action, documented in the expanded README.
