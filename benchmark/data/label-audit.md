# BoolQ label audit

Date: 2026-10-02. Scope: all 60 selected BoolQ question/passage/published-label triples in the frozen pilot. This audit used the supplied passages only and did not inspect model predictions, model scores or response files. It was initiated after the scored run began, following identification of a possible source-label error; it is therefore a post-hoc audit, not a preregistered data filter.

Frozen cases SHA-256: `60e6db60c72869936548c3c4620aa3b070cf12e516f3be42a61a306d0f592660`.

## Finding

**One unambiguous passage-label contradiction:** `boolq-dev-1842`. The published answer to whether carbon is a metal is yes, while its passage describes carbon as nonmetallic. The passage-supported answer is no.

The other flags identify uncertainty in the question or sufficiency of the excerpt. They are not asserted label errors. A defensible answer can depend on scope, interpretation, omitted context or source date.

**ambiguous: 15**, **contradiction: 1**, **supported: 38**, **time_or_scope: 6**.

## How to interpret reported quality

The primary BoolQ result measures **agreement with the published labels**, not verified scientific truth. Frozen labels, requests and primary scores remain unchanged. Programmatic checks confirm source fidelity, schema and provenance; they cannot establish that human source labels are correct.

A separately labeled post-hoc sensitivity analysis could exclude only `boolq-dev-1842` (59 remaining cases). This audit does not compute such a score or recommend silently replacing the primary score. Excluding all ambiguous cases would introduce judgment-based selection after the run; any such analysis would need its own explicit case list and label.

`Supported` means the supplied passage supports the published answer under its ordinary intended reading. `Time_or_scope` means it supports the source-era or specified-jurisdiction reading but should not be read as a current universal assertion. `Ambiguous` means interpretation or missing information could alter the answer; it does not mean the opposite label is established.

## Complete review

| Case ID | Published label | Audit status | Passage evidence or limitation |
|---|---|---|---|
| boolq-dev-0659 | yes | supported | Passage explicitly gives the host an automatic berth. |
| boolq-dev-2774 | no | ambiguous | Source describes a proposed acquisition followed by acquisition of stores; its last sentence can be read as acquisition of Rite Aid itself. Company ownership versus store ownership and the 2017 time frame need care. |
| boolq-dev-0579 | yes | supported | Passage says males and females are almost identical, while acknowledging male size differs. |
| boolq-dev-2217 | no | supported | SWIFT is used in addition to BSB for international transfers; they are distinct identifiers. |
| boolq-dev-1517 | no | ambiguous | Passage says the 2018 tournament took place in Russia. No is defensible for all World Cups across history, while yes is natural for all matches in the 2018 tournament. The question does not identify its time scope. |
| boolq-dev-1662 | yes | supported | Passage explicitly says the injury can occur on lesser toes. |
| boolq-dev-2868 | no | time_or_scope | Passage explicitly says no permit is required for open handgun carry in North Carolina. This is passage-era legal text; no current-law verification was performed. |
| boolq-dev-1447 | yes | supported | A virtual image is described as formed yet unable to be projected onto a screen. |
| boolq-dev-0383 | yes | supported | Passage explicitly gives the host an automatic berth. It repeats the passage in case 0659 with a paraphrased question. |
| boolq-dev-2665 | no | supported | The town name is explicitly described as fictional; the real place mentioned is a different bay/inlet. |
| boolq-dev-2041 | no | supported | The passage defines an unlisted public company as a public company not listed on any exchange. |
| boolq-dev-1296 | no | ambiguous | Passage describes the history of rainforest being called jungle and a broad definition of jungle; it does not directly settle equivalence. The no label is plausible under a strict distinction, but the passage also supports colloquial overlap. |
| boolq-dev-1114 | yes | supported | Passage explicitly calls gray wolf also known as timber wolf. |
| boolq-dev-1953 | no | supported | Passage lists three main Australian time zones and different external-territory zones. |
| boolq-dev-2493 | yes | time_or_scope | Passage supports any-person arrest in England and Wales. That supports an existential reading of in the UK, but does not establish every UK jurisdiction or current eligibility conditions. |
| boolq-dev-3009 | yes | time_or_scope | Passage explicitly says service is offered 24 hours daily, while some routes are part-time. It is a source-era system-wide statement, not a guarantee for every route or current service. |
| boolq-dev-2504 | no | supported | Passage says Ian McKellen was offered the role and turned it down, while identifying him as Gandalf. The intended role is supplied by the question. |
| boolq-dev-3125 | yes | supported | Passage identifies an optical phenomenon called an upside-down rainbow, while distinguishing it physically from rainbows. |
| boolq-dev-1092 | no | supported | Passage lists the six New England states and describes New York as a bordering state. |
| boolq-dev-0055 | no | ambiguous | Passage describes kissing and mutual feelings in season 3 but a final choice of Stefan. Get together could mean a romantic encounter or becoming a couple; the no label uses the latter sense. |
| boolq-dev-2791 | no | supported | Manhattan is listed as one of five constituent boroughs of New York City. |
| boolq-dev-1842 | yes | contradiction | Published label is yes, but the passage explicitly describes carbon as nonmetallic. Under the ordinary meaning of the question, passage-supported answer is no. |
| boolq-dev-2951 | yes | supported | Passage gives explicit style guidance allowing actor for both women and men. |
| boolq-dev-0852 | no | ambiguous | Only passage: the Gold Award is often compared to Eagle Scout. This does not state eligibility rules, whether a person can join both organizations, or the relevant date. It cannot establish the no label by itself. |
| boolq-dev-1171 | no | time_or_scope | Passage treats Turkey and the EU as separate partners with a common border and customs union; the no label is consistent with that source-era reading. Present membership was not checked. |
| boolq-dev-1550 | no | supported | Passage explicitly says the relevant part of the Ring of Fire excludes Australia. |
| boolq-dev-2116 | yes | supported | Passage explicitly states that Father's Day is celebrated in the Netherlands. |
| boolq-dev-0341 | no | supported | Passage explicitly says sound speed varies by substance and provides different values. |
| boolq-dev-2098 | yes | supported | Passage includes siblings-in-law among relations connected by marriage. |
| boolq-dev-2597 | yes | supported | Passage identifies Hellmann's and Best Foods as names for the same product line. The yes label follows the intended common-brand reading, rather than a separately verified legal-entity claim. |
| boolq-dev-0468 | yes | supported | Passage describes floating plastic and debris in the North Pacific; mass is read as a collection, not a solid island. |
| boolq-dev-0065 | yes | supported | The angle-sum statement and acute/obtuse definitions support at least two acute angles for ordinary nondegenerate Euclidean triangles. |
| boolq-dev-3114 | yes | supported | The opening sentence explicitly calls the play a comedy. |
| boolq-dev-0877 | yes | ambiguous | Passage equates federal and national government within a federation. The question leaves country and constitutional structure unspecified, so universal equivalence is not established. |
| boolq-dev-2630 | yes | supported | Passage explicitly lists garbanzo bean as another name for chickpea. |
| boolq-dev-0448 | yes | ambiguous | Passage describes a song about obsession, jealousy and surveillance, consistent with stalking. It never names the song, so the question's exact title/reference cannot be established from this passage alone. |
| boolq-dev-3134 | yes | supported | Passage defines stateless people as not considered nationals by any state. |
| boolq-dev-0525 | no | supported | Passage identifies CAD as Canada's currency and distinguishes it from other dollar-denominated currencies. |
| boolq-dev-1381 | no | time_or_scope | Passage prohibits plate blocking without the ball in MLB from the 2014 season. The question does not specify league or date; other rulesets are not established. |
| boolq-dev-0308 | no | supported | Passage describes coriander being used together with cumin, treating them as separate ingredients. |
| boolq-dev-2849 | no | ambiguous | Passage supports that the stinger is not always left lodged in skin. If stinger in a bee sting means the apparatus that delivers the sting, the question asks something different. The intended retained-stinger meaning is unstated. |
| boolq-dev-0210 | yes | supported | Passage describes pregnancy, choosing to have the child, and the baby shower; supports the intended storyline answer. |
| boolq-dev-3115 | yes | ambiguous | Passage permits multiple citizenship under Czech law from 2014 but does not state US rules or the questioner's eligibility. Yes is plausible for possibility, but a personal can-I conclusion is not established by the passage. |
| boolq-dev-0587 | yes | ambiguous | Passage says Boost and Virgin were two brands reorganized into one Sprint group in 2010. Same parent versus same brand/service gives different readings; the ownership frame is also dated. |
| boolq-dev-2552 | yes | supported | Passage explicitly calls lily-of-the-valley highly poisonous. |
| boolq-dev-1859 | no | supported | Passage describes Pinot gris as a distinct variety thought to be a mutant clone of Pinot noir. |
| boolq-dev-1739 | yes | ambiguous | Passage places spongy bone in the walls of the medullary cavity and marrow in the space itself. In the cavity can include its walls or mean only its contents; yes adopts the former. |
| boolq-dev-1066 | yes | supported | Passage describes the preamble as the Constitution's introductory statement, adopted by the Constituent Assembly with it. |
| boolq-dev-3247 | yes | supported | Passage explicitly says the fifth season premiered in 2018; this establishes existence without relying on its future-release details. |
| boolq-dev-1335 | yes | supported | Passage explicitly includes optic and olfactory nerves as parts of the CNS under a stated common classification. |
| boolq-dev-1979 | yes | supported | Passage defines insulin resistance as a pathological condition. |
| boolq-dev-2426 | yes | time_or_scope | Passage explicitly states no duty to answer police questions outside detention or arrest, in a US/Fifth-Amendment context. The question does not name a jurisdiction; no current legal verification was performed. |
| boolq-dev-3165 | yes | ambiguous | Passage only describes a 2010 US bottling-size reduction. It names no product and does not distinguish Red Stripe Light from another beverage or current availability. The yes label is unsupported by this excerpt alone. |
| boolq-dev-0980 | no | supported | Passage identifies pyruvate as the conjugate base of pyruvic acid, distinguishing the chemical forms. |
| boolq-dev-2011 | yes | supported | Passage explicitly says Howard joined Houston and spent three seasons with the Rockets. |
| boolq-dev-2818 | yes | supported | Passage reports yield increases under rotation; uncertain causal explanation does not negate the stated increase. |
| boolq-dev-1603 | no | supported | Passage explicitly describes Father's Day on different dates across countries. |
| boolq-dev-1625 | yes | ambiguous | Passage says black garlic softness increases with water content. It does not establish a normal or desired mushy texture, or a specifically Japanese preparation. |
| boolq-dev-0767 | yes | ambiguous | Passage compares playing-field widths, not whole-stadium size or seating capacity. Bigger stadium is broader than the evidence; yes assumes a field-size interpretation. |
| boolq-dev-1570 | no | ambiguous | Passage establishes a touchscreen phone in 1994 but gives no iPhone release date. The no answer requires background knowledge not contained in the supplied excerpt. |

## Additional sampling limitation

Cases `boolq-dev-0659` and `boolq-dev-0383` share the same passage and paraphrase the host-qualification question. Both are retained because the sample was frozen before evaluation. A row-level sample is therefore not 60 fully independent topics.

## Provenance

Source: [Google Research BoolQ](https://github.com/google-research-datasets/boolean-questions), via the Google-owned Hugging Face validation mirror documented in `manifest.json`. [Original paper](https://arxiv.org/abs/1905.10044). The selected source passages retain their [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/) attribution and license. This audit is a separate annotation; it does not modify the frozen dataset.
