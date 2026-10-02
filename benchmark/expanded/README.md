# Expanded GPT-6 Luna / Jev study

This directory contains a larger, reproducible comparison through OpenRouter using the same `{"type":"noul","noul":p}` answer object. Luna uses strict JSON Schema, `reasoning.effort=none`, an OpenAI-only provider route, and a maximum of 128 output tokens. The ceiling does not force token consumption: inspect the actual returned usage and output audit. The answer schema matches Jev; decimal precision/token counts are not forced to be identical.

## Protocols

- **Quality:** all 3,270 BoolQ development rows, 277 RTE validation rows, 638 WiC validation rows, 300 policy cases, 300 exact-probability cases. 9,570 fresh attempts. Up to 16 concurrent case pairs, with sequential random model order inside each pair. Bulk latency is diagnostic only.
- **Timing:** 60 frozen cases (12/task), repeated 3 times/model serially. 360 measured calls plus 12 warmups.
- **Caching:** 96 cases in 48 counterfactual pairs, 4 separately primed blocks, 3 arms/case. 288 measured calls plus 4 primes. Identical text/segmentation across Luna arms, explicit breakpoint only in the cache-on arm.

Analysis decisions were frozen in `analysis-spec.json` before expanded outcomes were inspected. `data/manifest.json` and `sources/freeze.json` record source identity, mappings, row counts, hashes, overlap and licensing. Published labels are retained without outcome-based editing.

## The service-error amendment

The initial quality run stopped after 308 attempts because a Jev HTTP 520 response lacked a billing field. The 308 original JSONL lines are preserved byte for byte. `continue_quality.py` completed only unattempted case/model combinations; the failed answer was never retried or replaced. Every original planned pair therefore has exactly one attempted response per model.

Missing bills remain `null`. A separate $0.01 allowance per unknown charge was held by the local budget guard; it is neither an observed bill nor an estimated inference price. The amended protocol and initial metadata/raw-prefix hashes are recorded in run metadata. With incomplete billing, exact total cost and affected per-1,000 projections are withheld. Known subtotals remain inspectable.

`run_study.py` is the original frozen runner. The copy `runs/quality-20261002T093341Z/original-runner.py` has the same hash. The continuation is a separate, tested module. No automatic retries or answer repairs are used.

## Inspect and rebuild without API calls

From the parent `benchmark` directory:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s expanded -p 'test_*.py' -v
python3 expanded/analyze.py --help
python3 expanded/verify_expanded.py --help
python3 expanded/cache_analysis.py --help
python3 expanded/build_report.py
```

`analyze.py QUALITY_RUN --timing-dir TIMING_RUN` writes expanded `summary.json` and `analysis.md`. `cache_analysis.py` reports paired uncertainty for the expanded cache run. `paper_figures.py` generates SVG and PNG figures using Matplotlib. Python standard-library modules handle model runs and statistics; plotting was performed with Matplotlib 3.11.2 and NumPy 2.5.3. The final figures and their data accompany the archive.

The report uses small saved-record JSON chunks for its expandable API records. Serve the `outputs` directory through a local static HTTP server, or use the published GitHub Pages site. No application server or API key is required to read the study.

## Fresh model calls

For a public clone, first restore the original inputs with `python3 expanded/fetch_sources.py` and `python3 expanded/datasets.py` from the parent `benchmark` directory. Do not submit the public RTE omission placeholders to a model. The hash check below refuses those transformed inputs.

Supply `OPENROUTER_API_KEY` securely in the environment; never put a key in code, URLs, browser storage or command arguments. A fresh run incurs charges. The following uses the saved frozen inputs and imports the original quality runner:

```python
import os, hashlib
from pathlib import Path
from expanded.run_study import run_quality
cases = Path('expanded/data/cases.jsonl')
expected = 'e271790b2ee38a1c231e312fa15ece2a56c95f6bf705294f1637d43b2edcf256'
if hashlib.sha256(cases.read_bytes()).hexdigest() != expected:
    raise RuntimeError('Restore and verify the original frozen inputs before model calls.')
result = run_quality(os.environ['OPENROUTER_API_KEY'], cases_path=cases, budget=0.80)
```

Use `run_timing` only after bulk calls stop, with the frozen `data/timing-cases.jsonl`. The cache runner accepts a key through stdin; see its `--help`. Preserve any failed attempts and unknown bills when interpreting a new run. The budget guard is conservative local accounting, not an account-level provider cap.

## Source provenance and publication

- BoolQ: Google Research, Clark et al. 2019. Source passages/labels CC BY-SA 3.0. Inputs are normalized and yes/no questions mapped to the Noul contract.
- RTE: full SuperGLUE validation split from `aps/super_glue`. Class 0 = entailment, 1 = not entailment, mapped to yes=`1-label`. Original redistribution terms were not conclusively verified; the public package omits premise/hypothesis source text while retaining numerical results, IDs, templates, hashes and retrieval code. Complete inputs remain in the local research artifact.
- WiC: Pilehvar and Camacho-Collados 2019; CC BY-NC 4.0. Source word spans are wrapped in `[TARGET]` markers. Noncommercial terms and attribution remain attached to the data.
- Synthetic cases: generated locally with Boolean or exact rational oracles, all target computations tested independently.

`fetch_sources.py` retrieves the normalized public sources and verifies recorded source hashes. Public-package byte hashes differ from original input hashes where RTE text is omitted; the publication manifest explains the transformations. Original hashes remain provenance for the actual experiment, not claims about redacted bytes.

The pilot is kept separately. Sixty BoolQ rows overlap but all expanded predictions are fresh. Thirteen policy operand sets overlap the pilot; no identical probability input overlaps. Shared templates and public training-data exposure remain limitations.
