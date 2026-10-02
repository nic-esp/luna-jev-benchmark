#!/usr/bin/env python3
"""Present retained protocol-check measurements as a supplement to the study.

Reads the existing pilot.html archive and writes supplement.html. Embedded case
data and explorer JavaScript are preserved exactly. No model or network calls.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

OUT = Path(__file__).resolve().parent.parent


def one(pattern, replacement, text):
    result, count = re.subn(pattern, lambda _: replacement, text, count=1, flags=re.S)
    if count != 1:
        raise ValueError(f"Expected presentation element not found: {pattern}")
    return result


def build(source=OUT / "pilot.html", destination=OUT / "supplement.html"):
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError("The source archive must remain unchanged")
    original = source.read_text()
    evidence_match = re.search(r'<script id="evidence" type="application/json">(.*?)</script>', original, re.S)
    if not evidence_match:
        raise ValueError("The archived evidence payload is missing")
    evidence = json.loads(evidence_match.group(1))
    if len(evidence["cases"]) != 204 or len(evidence["records"]) != 441:
        raise ValueError("Unexpected supplementary archive: expected 204 cases and 441 calls")
    measured = sum(r["phase"] == "measured" for r in evidence["records"])
    if measured != 432:
        raise ValueError("Unexpected supplementary measured-call count")

    # Only the presentation preceding the scripts is edited. The original JSON
    # payload and all explorer/filter/pagination behavior remain byte-identical.
    script_start = original.index("<script")
    page, scripts = original[:script_start], original[script_start:]
    page = page.replace("PYTHONDONTWRITEBYTECODE=1 python3 build_paper.py",
                        "PYTHONDONTWRITEBYTECODE=1 python3 build_supplement.py")
    saved_pre = []
    def protect_pre(match):
        saved_pre.append(match.group(0))
        return f"<!--SUPPLEMENT-PRE-{len(saved_pre)-1}-->"
    page = re.sub(r"<pre\b[^>]*>.*?</pre>", protect_pre, page, flags=re.S)
    page = one(r"<title>.*?</title>", "<title>Supplementary measurements | Luna and Jev study</title>", page)
    page = one(r'<meta name="description" content="[^"]*">',
        '<meta name="description" content="Supplementary protocol-check measurements for the Luna and Jev study: 204 cases and all 441 recorded calls, kept separate from primary estimates.">', page)
    page = one(r"<header>.*?</header>", """<header>
<p><a class="supplement-back" href="comparison.html">← Back to the Luna and Jev study</a></p>
<div class="journal-line"><span>Luna and Jev study · OpenRouter</span><span>Supplementary measurements</span></div>
<div class="article-type">Protocol checks and retained call-level evidence</div>
<h1>Supplementary measurements</h1>
<p class="subtitle">The study’s earlier protocol checks, with every input, reference target, response, timing and reported charge retained.</p>
<div class="metadata"><strong>Measured:</strong> 2 October 2026 · 08:42–08:50 UTC &nbsp; / &nbsp; <strong>Transport:</strong> OpenRouter<br>
<strong>Configurations:</strong> GPT-6 Luna, reasoning disabled · Jev 1.13<br>
<strong>Archive:</strong> 204 measured case IDs · 432 measured calls · 441 calls including setup, warmups and cache priming</div>
</header>""", page)
    page = one(r'<div class="abstract" id="abstract">.*?</div>\s*</div>', """<div class="abstract" id="abstract">
<h2>Supplementary scope</h2>
<p>These records document the study’s short-input and prefix-cache protocol checks. They are retained for transparency and inspection and are <strong>kept separate from the primary quality, timing and cache estimates</strong> in the <a href="comparison.html">study report</a>.</p>
<p>The 180 short-input cases comprise 60 policy decisions, 60 BoolQ questions and 60 exact-probability problems, each sent to both models. A further 24 equipment-policy cases were sent to three cache-study arms. These account for 432 measured calls; four warmups, one cold cache prime and four setup probes bring the complete record to 441 calls.</p>
<p><strong>Shared data and protocols.</strong> The 60 BoolQ rows also occur in the primary full split, some policy inputs overlap, and the synthetic task families and output contract are shared. Primary estimates use fresh recorded calls, but these datasets and designs are not independent. The supplementary observations are not pooled into the primary estimates.</p>
</div>""", page)
    page = one(r'<section id="introduction">.*?</section>', """<section id="introduction">
<h2><span class="num">1</span>Role of these measurements</h2>
<p>The checks exercised the common probability-of-yes answer schema, the two OpenRouter request adapters, sequential timing and billing capture, and explicit prefix caching. The tables below describe their observed outcomes and preserve the original analysis choices.</p>
<p>The <a href="comparison.html">study report</a> provides the primary estimates and overall interpretation. This supplement supplies the earlier call-level evidence and its limitations. Original run labels, case IDs, filenames and source hashes remain unchanged so records can be reconciled with their archived artifacts.</p>
</section>""", page)
    discussion = re.search(r'<section id="discussion">.*?</section>', page, re.S).group(0)
    limits = re.search(r'<ol class="compact">.*?</ol>', discussion, re.S).group(0)
    limits = limits.replace("per main task", "per short-input task").replace("this pilot", "these supplementary measurements")
    limits = limits.replace("Primary results should be read as agreement with published annotations.",
                            "These BoolQ scores should be read as agreement with published annotations.")
    page = one(r'<section id="discussion">.*?</section>',
        '<section id="discussion"><h2><span class="num">6</span>Scope and interpretation limits</h2>'
        '<p>These small protocol checks support inspection of the recorded configurations. Their descriptive contrasts and intervals are supplementary evidence; the study’s primary estimates are reported separately. Shared source rows and task templates limit independence between the two sets of measurements.</p>'
        + limits + '</section>', page)
    replacements = {
        "Experimental report<br>02 October 2026": "Study supplement<br>02 October 2026",
        '<nav>': '<nav><a class="supplement-back" href="comparison.html">← Back to study</a>',
        '>Abstract</a>': '>Supplementary scope</a>',
        '1 · Research questions': '1 · Role of these checks',
        '2 · Methods': '2 · Measurement protocol',
        '3 · Main results': '3 · Short-input checks',
        '4 · Cache experiment': '4 · Prefix-cache check',
        '6 · Discussion': '6 · Interpretation limits',
        '<span class="num">2</span>Methods': '<span class="num">2</span>Measurement protocol',
        '<span class="num">3</span>Main results': '<span class="num">3</span>Recorded short-input checks',
        '3.1 Quality depended on the task': '3.1 Quality by task',
        '3.2 Jev completed requests sooner at lower short-input cost': '3.2 Complete-response time and reported charges',
        '<span class="num">4</span>Cached-input experiment': '<span class="num">4</span>Prefix-cache protocol check',
        'The main run shuffled': 'The short-input check shuffled',
        'Main Luna calls used': 'Short-input Luna calls used',
        'Every main Luna response': 'Every short-input Luna response',
        'The main window was': 'The short-input measurement window was',
        'Main contrasts use': 'Short-input contrasts use',
        'Main-run latency distributions': 'Supplementary short-input latency distributions',
        'A second experiment used': 'The prefix-cache protocol check used',
        'the study does not isolate whether caching': 'this supplementary check does not isolate whether caching',
        'Thirty-four offline tests passed.': 'The archived validation recorded 34 offline tests passing.',
        'rebuild the report without calling a model': 'rebuild this supplementary page without calling a model',
        'the paper’s tables remain the full frozen study.': 'the tables above retain all supplementary cases in their stated denominators.',
        '<td>Main measured</td>': '<td>Short-input measured</td>',
        '<td>Main warmups</td>': '<td>Short-input warmups</td>',
        '<td>Yes, main results</td>': '<td>Yes, supplementary short-input estimates</td>',
        '<td>Yes, cache results</td>': '<td>Yes, supplementary cache estimates</td>',
        '<td>Main</td>': '<td>Short-input check</td>',
        '>Main experiment</option>': '>Short-input check</option>',
        '>Cache experiment</option>': '>Prefix-cache check</option>',
        'Luna (main / setup)': 'Luna (short-input / setup)',
        '<a href="RESULTS.md">Short summary ↗</a>': '<a href="comparison.html">Primary study report ↗</a>',
    }
    for old, new in replacements.items():
        page = page.replace(old, new)
    page = one(r"<footer>.*?</footer>",
        '<footer>Supplementary measurements for the Luna and Jev study, recorded on 2 October 2026. '
        'This presentation preserves the original evidence and introduces no new model calls. '
        'Selected BoolQ passages and annotations are attributed to Clark et al. / Google Research under CC BY-SA 3.0. '
        'Explorer filters do not change any reported estimate. <a href="comparison.html">Return to the study report</a>.</footer>', page)
    page = page.replace("</style>", ".supplement-back{display:inline-block;font:600 14px/1.4 var(--sans);padding:10px 14px;border:1px solid var(--line);border-radius:4px;background:var(--wash);text-decoration:none}.toc a.supplement-back{margin-bottom:12px}.abstract{border-left:4px solid var(--blue)}\n</style>", 1)
    for i, pre in enumerate(saved_pre):
        page = page.replace(f"<!--SUPPLEMENT-PRE-{i}-->", pre)
    result = page + scripts
    if result[ result.index("<script"): ] != scripts:
        raise AssertionError("Embedded evidence or explorer code changed")
    if re.search(r'<h[1-6][^>]*>[^<]*(?:Abstract|Conclusion)', page):
        raise AssertionError("Standalone paper framing remains")
    destination.write_text(result)
    return {"cases": 204, "measured_calls": 432, "all_calls": 441,
            "embedded_evidence_and_scripts_unchanged": True,
            "source_sha256": hashlib.sha256(original.encode()).hexdigest(),
            "output_sha256": hashlib.sha256(result.encode()).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=OUT / "pilot.html")
    parser.add_argument("--output", type=Path, default=OUT / "supplement.html")
    args = parser.parse_args()
    print(json.dumps(build(args.input, args.output)))
