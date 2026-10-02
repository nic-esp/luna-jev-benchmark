# Third-party benchmark data

This repository is a public research reproducibility copy. The complete original
study remains preserved locally. Numerical results, IDs, labels, probability
outputs, usage, charges, timing, prompt templates and retrieval code are retained.
Public request records for RTE contain an explicit source-text omission and must
not be described as byte-identical copies of the submitted requests.

## BoolQ

BoolQ by Christopher Clark, Kenton Lee, Ming-Wei Chang, Tom Kwiatkowski,
Michael Collins and Kristina Toutanova (NAACL 2019) is released under
[CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/).
[Author repository](https://github.com/google-research-datasets/boolean-questions).
The development split was normalized into the study schema; question/passage
text and published labels were retained. These adapted dataset portions remain
under CC BY-SA 3.0, separately from original study code and writing.

## WiC

WiC by Mohammad Taher Pilehvar and Jose Camacho-Collados (NAACL 2019) is
licensed under [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/).
[Author dataset page](https://pilehvar.github.io/wic/).
The validation split was normalized and target occurrences marked with
`[TARGET]` / `[/TARGET]`; published labels were retained. The WiC source-text
portions are shared for noncommercial research under that license. Their
presence does not grant permission for commercial reuse or relicense them
under any license assigned to this repository's original code.

## RTE / SuperGLUE

The exact original RTE redistribution terms were not verified. The
[SuperGLUE dataset card](https://huggingface.co/datasets/aps/super_glue)
refers users to original dataset licenses and declares its license as `other`.
[NIST's original data pages](https://tac.nist.gov/2009/RTE/past_data/index.html)
and [TAC data-agreement information](https://tac.nist.gov/2009/)
provide additional provenance; a download link is not treated as a blanket
redistribution grant. This is a conservative publication choice, not a finding
that research redistribution is prohibited.

RTE premises and hypotheses, including copies in API requests and report data,
are omitted from this public copy. Row IDs, labels and every numerical result
are preserved. `benchmark/expanded/fetch_sources.py` records exact retrieval
URLs and expected source hashes, so readers may retrieve the source from its
distributor under the applicable terms. References include Dagan et al. (2006),
Bar Haim et al. (2006), Giampiccolo et al. (2007), Bentivogli et al. (2009), and
Wang et al. (2019, SuperGLUE).

## Original synthetic cases and artifact hashes

Synthetic policy/probability cases were generated for this study. They do not
contain private user records. Benchmark source passages can discuss public
biographical or medical facts and should not be described as all fictional.

Original run metadata, checks and freeze manifests remain historical provenance.
Their original hash values are not rewritten to claim the public copy was the
data sent to the models. `PUBLICATION_MANIFEST.json` identifies transformed
files and supplies SHA-256 hashes of the public bytes. Reproducing original-data
tests requires locally retrieving and rebuilding the original datasets first.
