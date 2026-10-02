# Expanded-study figure captions

All figures use completed runs and are reconciled to the audited analysis. The retained pilot figures are unchanged.

## fig4-quality

PNG: 3224 × 1378 pixels. SVG preserves vector geometry and text.

Accuracy on four binary tasks, counting every invalid response as incorrect. Points show observed accuracy and horizontal intervals are nominal 95% Wilson intervals. The table gives correct answers over all attempted cases. BoolQ, RTE, and WiC measure agreement with published reference labels; synthetic policy targets come from a deterministic oracle. The intervals are exploratory, do not account for every shared passage or template, and should not be used to infer model differences from overlap alone.

## fig5-probability

PNG: 2912 × 1482 pixels. SVG preserves vector geometry and text.

Model-generated probabilities versus exact event probabilities on 300 prespecified finite probability problems. Each panel reports its own valid-response denominator and mean absolute error in percentage points. Invalid responses have no plottable probability and are omitted here but retained in the study's failure accounting. The dashed diagonal is exact agreement. No jitter is added to the probability values, and overlapping marks remain present.

## fig6-timing

PNG: 3224 × 1300 pixels. SVG preserves vector geometry and text.

The isolated serial timing study selected 12 cases per task before outcomes were inspected and repeated them three times per model. Each plotted point is one case/model's median over those three repeats. Grey connectors pair the same case across models, and heavy horizontal bars mark medians across case medians. Cases missing a valid positive timing in either arm in any repeat are excluded with counts retained in the analysis. Common logarithmic time axes are used. No concurrent quality-run latency is included.

## fig7-cache

PNG: 3224 × 1508 pixels. SVG preserves vector geometry and text.

Four separately primed long-prefix blocks, each containing 24 distinct deterministic policy cases and all three model arms. Panel A scales each block's actual measured mean cost to 1,000 decisions. The white hatched extension on cached Luna is that block's distinct priming charge divided across its 24 measured cases. Panel B shows individual complete-response latencies as faint dots and block medians as connected marks, excluding all priming requests. The four blocks share one session and are not independent days or deployments. Returned cache counters establish treatment status; no cache-effect interpretation is warranted for a failed control.
