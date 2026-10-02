#!/usr/bin/env python3
"""Reproduce paper figures from recorded benchmark responses; makes no network calls.

Requirements: matplotlib==3.11.2, numpy==2.5.3 (other recent versions may work).
Run from any directory: python paper_figures.py
"""
import argparse
from collections import Counter
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("MPLCONFIGDIR", str(ROOT.parent.parent / "work" / "matplotlib-config"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import FixedLocator, FuncFormatter, PercentFormatter, NullLocator
import numpy as np

MAIN_DEFAULT = ROOT / "runs" / "20261002T084223Z" / "responses.jsonl"
CACHE_DEFAULT = ROOT / "runs" / "cache-20261002T084958Z" / "responses.jsonl"
NAVY = "#173D5E"
ORANGE = "#C96513"
TEXT = "#182431"
GREY = "#697786"
GRID = "#E5E9ED"
COLORS = {"Jev": NAVY, "Luna": ORANGE, "Luna cached": ORANGE, "Luna uncached": ORANGE}
STYLES = {"Jev": "-", "Luna": (0, (4, 2)), "Luna cached": "-", "Luna uncached": (0, (4, 2))}
FAMILIES = ["conditional_table", "without_replacement", "weighted_mixture"]
FAMILY_NAMES = {"conditional_table": "Conditional table", "without_replacement": "Without replacement", "weighted_mixture": "Weighted mixture"}
BOOTSTRAP_SEED = 20261002
BOOTSTRAP_SAMPLES = 10000


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def measured(rows):
    return [r for r in rows if r["phase"] == "measured"]


def validate(main, cache, cases):
    expected = {(task, model): 60 for task in ("boolq", "policy", "probability") for model in ("Jev", "Luna")}
    if Counter((r["experiment"], r["model_label"]) for r in main) != expected:
        raise ValueError("Unexpected main measured population; update the figure protocol explicitly")
    if Counter(r["model_label"] for r in cache) != {"Jev": 24, "Luna cached": 24, "Luna uncached": 24}:
        raise ValueError("Unexpected cache measured population")
    for group in (main, cache):
        if len({(r["case_id"], r["model_label"]) for r in group}) != len(group):
            raise ValueError("Duplicate measured case/model; repeated-measure semantics must be specified")
        for r in group:
            if not r["valid"] or not math.isfinite(r["probability"]):
                raise ValueError("Figures require the complete valid observed population")
            if r.get("cost_usd") is None or not math.isfinite(r["cost_usd"]):
                raise ValueError("Missing or invalid measured cost")
            if r["latency_s"] <= 0 or not math.isfinite(r["latency_s"]):
                raise ValueError("Invalid latency")
    for r in main:
        case = cases[r["case_id"]]
        if case["target"] != r["target"] or case["experiment"] != r["experiment"]:
            raise ValueError("Frozen case disagrees with response record")
    for family in FAMILIES:
        for model in ("Jev", "Luna"):
            matching = [r for r in main if r["experiment"] == "probability" and r["model_label"] == model and cases[r["case_id"]]["source"]["probability_family"] == family]
            if len(matching) != 20:
                raise ValueError("Probability family is not the expected 20-case population")


def style():
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 10,
        "axes.labelsize": 10, "axes.titlesize": 12, "axes.titleweight": "bold",
        "axes.labelcolor": TEXT, "axes.edgecolor": "#A7B1BB", "axes.linewidth": .7,
        "text.color": TEXT, "xtick.color": GREY, "ytick.color": GREY,
        "xtick.labelsize": 9, "ytick.labelsize": 9,
        "legend.fontsize": 9, "legend.frameon": False,
        "axes.spines.top": False, "axes.spines.right": False,
        "figure.facecolor": "white", "axes.facecolor": "white", "savefig.facecolor": "white",
        "grid.color": GRID, "grid.linewidth": .7, "grid.alpha": 1,
        "svg.fonttype": "none", "svg.hashsalt": "luna-jev-paper-20261002",
        "pdf.fonttype": 42, "lines.linewidth": 1.8,
    })


def axis_base(ax, grid="y"):
    ax.set_axisbelow(True)
    ax.grid(axis=grid)
    ax.tick_params(length=3, width=.6)


def panel_title(ax, letter, title):
    ax.set_title(letter + "  " + title, loc="left", y=1.14, pad=0)


def ecdf(ax, values, label, color, linestyle="-", lower=.18, upper=3.1):
    values = np.sort(np.asarray(values))
    x = np.r_[lower, values, upper]
    y = np.r_[0., np.arange(1, len(values) + 1) / len(values), 1.]
    ax.step(x, y, where="post", color=color, linestyle=linestyle, label=label)


def time_axis(ax, lower=.18, upper=3.1):
    ax.set_xscale("log")
    ax.set_xlim(lower, upper)
    ax.xaxis.set_major_locator(FixedLocator([.2, .3, .5, 1, 2, 3]))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:g}"))
    ax.xaxis.set_minor_locator(NullLocator())
    ax.set_ylim(0, 1.02)
    ax.set_yticks([0, .25, .5, .75, 1])
    ax.yaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    ax.set_xlabel("End-to-end latency (seconds; log scale)", labelpad=10)
    axis_base(ax)


def save(fig, name, out_dir, manifest, title, description):
    fig.canvas.draw()
    svg = out_dir / (name + ".svg")
    png = out_dir / (name + ".png")
    fig.savefig(svg, metadata={"Title": title, "Description": description, "Date": "2026-10-02"})
    fig.savefig(png, dpi=260, metadata={"Title": title, "Description": description})
    inches = fig.get_size_inches()
    manifest["figures"][name] = {"svg": svg.name, "png": png.name, "size_inches": list(inches),
                                 "png_pixels": [round(x * 260) for x in inches], "title": title, "caption": description,
                                 "svg_sha256": sha(svg), "png_sha256": sha(png)}
    plt.close(fig)


def fig_latency(rows, out_dir, manifest):
    fig, axes = plt.subplots(1, 3, figsize=(12.4, 4.6), sharex=True, sharey=True)
    fig.subplots_adjust(left=.06, right=.985, bottom=.20, top=.78, wspace=.16)
    names = [("boolq", "Reading comprehension"), ("policy", "Short policy decisions"), ("probability", "Exact probabilities")]
    metric = {}
    for ax, letter, (task, title) in zip(axes, "ABC", names):
        panel_title(ax, letter, title)
        metric[task] = {}
        for model in ("Jev", "Luna"):
            latencies = [r["latency_s"] for r in rows if r["experiment"] == task and r["model_label"] == model]
            ecdf(ax, latencies, model, COLORS[model], STYLES[model])
            median = float(np.median(latencies))
            metric[task][model] = {"n": len(latencies), "median_s": median, "p95_s": float(np.quantile(latencies, .95))}
        time_axis(ax)
        ax.text(.96, .09, "Medians\n" + "\n".join(f"{m}  {metric[task][m]['median_s']:.3f} s" for m in ("Jev", "Luna")), transform=ax.transAxes, va="bottom", ha="right", fontsize=9, linespacing=1.5, bbox={"facecolor":"white", "edgecolor":"none", "pad":3})
        ax.text(.0, 1.035, "60 measured requests per model", transform=ax.transAxes, color=GREY, fontsize=9)
    axes[0].set_ylabel("Requests completed by this time", labelpad=10)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=2, bbox_to_anchor=(.50, .98), handlelength=3.2, columnspacing=2.2)
    fig.text(.06, .045, "Empirical cumulative distributions • 2 October 2026 • Warmup requests excluded", fontsize=9, color=GREY)
    manifest["metrics"]["main_latency"] = metric
    save(fig, "fig1-latency", out_dir, manifest, "Latency distributions across three benchmark tasks",
         "Empirical cumulative distribution of end-to-end non-streaming latency for each task, using 60 measured requests per model per panel. Each step represents observed calls, with common logarithmic time axes. Two warmup requests per model are excluded. Timing includes network, OpenRouter, and provider processing; it is not isolated model inference time. Solid navy denotes Jev and dashed orange denotes Luna.")


def probability_family_stats(rows, cases):
    stats = {}
    for index, family in enumerate(FAMILIES):
        both = {}
        for model in ("Jev", "Luna"):
            selected = sorted([r for r in rows if r["experiment"] == "probability" and r["model_label"] == model and cases[r["case_id"]]["source"]["probability_family"] == family], key=lambda r: r["case_id"])
            both[model] = selected
        if [r["case_id"] for r in both["Jev"]] != [r["case_id"] for r in both["Luna"]]:
            raise ValueError("Probability family is not paired by case")
        n = len(both["Jev"])
        rng = np.random.default_rng(BOOTSTRAP_SEED + index)
        samples = rng.integers(0, n, size=(BOOTSTRAP_SAMPLES, n))
        stats[family] = {}
        for model, selected in both.items():
            errors = np.asarray([abs(r["probability"] - r["target"]) for r in selected])
            distribution = errors[samples].mean(axis=1)
            stats[family][model] = {"n": n, "mae": float(errors.mean()), "ci95": [float(x) for x in np.quantile(distribution, [.025, .975])]}
    return stats


def fig_probability(rows, cases, out_dir, manifest):
    fig, axes = plt.subplots(1, 3, figsize=(12.4, 4.9), gridspec_kw={"width_ratios": [1, 1, 1.25]})
    fig.subplots_adjust(left=.06, right=.975, bottom=.23, top=.80, wspace=.40)
    for ax, model, letter in zip(axes[:2], ("Jev", "Luna"), "AB"):
        selected = [r for r in rows if r["experiment"] == "probability" and r["model_label"] == model]
        panel_title(ax, letter, model + " predictions")
        ax.plot([0, 1], [0, 1], color=GREY, lw=1, linestyle=(0, (3, 3)), zorder=1)
        ax.scatter([r["target"] for r in selected], [r["probability"] for r in selected], s=28, marker="o" if model == "Jev" else "s", color=COLORS[model], alpha=.80, edgecolors="white", linewidths=.5, zorder=3, clip_on=False)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.set_xticks([0, .25, .5, .75, 1])
        ax.set_yticks([0, .25, .5, .75, 1])
        ax.xaxis.set_major_formatter(PercentFormatter(1, decimals=0))
        ax.yaxis.set_major_formatter(PercentFormatter(1, decimals=0))
        ax.set_xlabel("Exact event probability", labelpad=9)
        ax.set_ylabel("Predicted probability of yes", labelpad=8)
        axis_base(ax, "both")
        ax.set_aspect("equal", adjustable="box")
        mae = float(np.mean([abs(r["probability"] - r["target"]) for r in selected]))
        ax.text(.0, 1.04, f"60 cases · MAE {mae * 100:.2f} pp", transform=ax.transAxes, color=GREY, fontsize=9)
    stats = probability_family_stats(rows, cases)
    ax = axes[2]
    panel_title(ax, "C", "Error by probability family")
    for index, family in enumerate(FAMILIES):
        for model, offset, marker in (("Jev", -.13, "o"), ("Luna", .13, "s")):
            s = stats[family][model]
            mean = 100 * s["mae"]
            low, high = [100 * x for x in s["ci95"]]
            ax.errorbar(mean, index + offset, xerr=[[mean - low], [high - mean]], fmt=marker, markersize=6, capsize=3, color=COLORS[model], elinewidth=1.3, markeredgecolor="white", markeredgewidth=.5, label=model if index == 0 else None, zorder=3, clip_on=False)
    max_ci = max(stats[f][m]["ci95"][1] for f in FAMILIES for m in ("Jev", "Luna")) * 100
    ax.set_xlim(0, math.ceil(max_ci / 5) * 5)
    ax.set_ylim(2.45, -.65)
    ax.set_yticks(range(len(FAMILIES)), [FAMILY_NAMES[x].replace(" ", "\n", 1) if x != "conditional_table" else "Conditional\ntable" for x in FAMILIES])
    ax.tick_params(axis="y", length=0)
    ax.set_xlabel("Mean absolute error (percentage points)", labelpad=10)
    ax.text(.0, 1.04, "20 paired cases per family", transform=ax.transAxes, color=GREY, fontsize=9)
    axis_base(ax, "x")
    ax.legend(loc="lower right", bbox_to_anchor=(1, -.26), ncol=2, handlelength=1.3, columnspacing=1.4)
    fig.text(.06, .075, "A–B: each mark is one case; dashed line is exact agreement. Overlapping marks are retained.", color=GREY, fontsize=9)
    fig.text(.06, .027, "C: points are means; intervals are exploratory 95% percentile bootstrap intervals (10,000 paired case resamples).", color=GREY, fontsize=9)
    manifest["metrics"]["probability_families"] = stats
    save(fig, "fig2-probability", out_dir, manifest, "Probability estimation against exact references",
         "Predicted probabilities versus exact event probabilities for Jev (A) and Luna (B), using the same 60 finite probability problems. The dashed diagonal is exact agreement, not a fitted line. Panel C shows mean absolute error in percentage points for three prespecified 20-case families. Intervals are exploratory 95% percentile bootstrap intervals from 10,000 resamples of case identities within each family, retaining model pairing. These intervals do not establish broad calibration or correct for multiple comparisons.")


def fig_cache(rows, all_rows, out_dir, manifest):
    primes = [r for r in all_rows if r["phase"] == "cache_prime"]
    if len(primes) != 1 or not primes[0]["valid"] or primes[0].get("cost_usd") is None:
        raise ValueError("A single valid billed priming request is required")
    prime = primes[0]
    aggregate = {}
    for arm in ("Jev", "Luna uncached", "Luna cached"):
        chosen = [r for r in rows if r["model_label"] == arm]
        aggregate[arm] = {"n": len(chosen), "cost_usd": sum(r["cost_usd"] for r in chosen),
                          "cost_per_1000_usd": sum(r["cost_usd"] for r in chosen) / len(chosen) * 1000,
                          "median_s": float(np.median([r["latency_s"] for r in chosen]))}
    read = lambda r: (r.get("usage", {}).get("prompt_tokens_details") or {}).get("cached_tokens")
    write = lambda r: (r.get("usage", {}).get("prompt_tokens_details") or {}).get("cache_write_tokens")
    cached_rows = [r for r in rows if r["model_label"] == "Luna cached"]
    control = [r for r in rows if r["model_label"] == "Luna uncached"]
    if not all(isinstance(read(r), int) and read(r) > 0 for r in cached_rows):
        raise ValueError("Every measured cached request must have observed cache hits for this figure")
    if not all(read(r) == 0 and write(r) == 0 for r in control):
        raise ValueError("Uncached control was not verified")
    prime_allocated = prime["cost_usd"] / len(cached_rows) * 1000
    base = aggregate["Luna cached"]["cost_per_1000_usd"]
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 5.3), gridspec_kw={"width_ratios": [1.05, 1]})
    fig.subplots_adjust(left=.18, right=.98, top=.81, bottom=.25, wspace=.31)
    ax = axes[0]
    panel_title(ax, "A", "Cost at the observed request mix")
    order = ["Jev", "Luna uncached", "Luna cached", "Luna cached + prime"]
    labels = ["Jev", "Luna uncached", "Luna cached\nmeasured calls", "Luna cached\nprime allocated over 24 cases"]
    values = [aggregate["Jev"]["cost_per_1000_usd"], aggregate["Luna uncached"]["cost_per_1000_usd"], base, base]
    for index, arm in enumerate(order):
        color = NAVY if arm == "Jev" else ORANGE
        ax.barh(index, values[index], height=.56, color=color if arm != "Luna uncached" else "#FAE9DC", edgecolor=color, linewidth=1, zorder=2)
    ax.barh(3, prime_allocated, left=base, height=.56, facecolor="white", edgecolor=ORANGE, hatch="////", linewidth=1, zorder=3)
    total = values[:3] + [base + prime_allocated]
    for index, amount in enumerate(total):
        ax.text(amount + .007, index, f"${amount:.4f}", va="center", fontsize=9)
    ax.set_yticks(range(4), labels)
    ax.set_ylim(3.6, -.7)
    ax.set_xlim(0, .37)
    ax.xaxis.set_major_locator(FixedLocator([0, .1, .2, .3]))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"${value:.2f}"))
    ax.set_xlabel("Cost per 1,000 requests (USD)", labelpad=10)
    ax.tick_params(axis="y", length=0)
    axis_base(ax, "x")
    ax.legend(handles=[Patch(facecolor="white", edgecolor=ORANGE, hatch="////", label="Allocated priming cost")], loc="lower left", bbox_to_anchor=(-.04, -.31))
    ax.text(.0, 1.04, "24 measured requests per arm", transform=ax.transAxes, color=GREY, fontsize=9)
    ax = axes[1]
    panel_title(ax, "B", "Latency after the priming request")
    for arm in ("Jev", "Luna uncached", "Luna cached"):
        values = [r["latency_s"] for r in rows if r["model_label"] == arm]
        ecdf(ax, values, arm, COLORS[arm], STYLES[arm])
    time_axis(ax)
    ax.set_ylabel("Requests completed by this time", labelpad=9)
    ax.legend(loc="lower right", bbox_to_anchor=(.99, .05), ncol=1, handlelength=2.4, columnspacing=1.2)
    ax.text(.0, 1.04, "24 observations per arm; prime excluded", transform=ax.transAxes, color=GREY, fontsize=9)
    cache_n = sum(read(r) > 0 for r in cached_rows)
    token_n = sum(read(r) for r in cached_rows)
    fig.text(.055, .055, f"Cache evidence: {cache_n}/24 cached calls reported reads ({token_n:,} tokens total); all 24 uncached controls reported zero reads and writes.", color=GREY, fontsize=9)
    fig.text(.055, .016, f"Separate prime: ${prime['cost_usd']:.7f}, {prime['latency_s']:.3f} s, {read(prime):,} tokens read and {write(prime):,} written. No first-request latency is amortized.", color=GREY, fontsize=9)
    manifest["metrics"]["cache"] = {"arms": aggregate, "prime_cost_usd": prime["cost_usd"], "prime_latency_s": prime["latency_s"],
                                      "prime_cost_allocated_per_1000_usd": prime_allocated,
                                      "cached_plus_prime_cost_per_1000_usd": base + prime_allocated,
                                      "measured_cache_reads_tokens": token_n, "measured_cache_hit_calls": cache_n,
                                      "uncached_control_calls_with_zero_reads_and_writes": len(control)}
    save(fig, "fig3-cache", out_dir, manifest, "Measured cost and latency in the long-prefix caching experiment",
         "Three arms answered the same 24 equipment-policy cases. Panel A scales actual measured mean billed costs to 1,000 requests; this is a workload-based projection, not a list price or a further 1,000-request run. The fourth row adds one distinct priming request divided across the 24 measured cached cases; the hatched segment isolates that allocation. Panel B shows empirical latency distributions after the priming request, excluding the prime itself. All 24 Luna cached calls reported cache reads and every uncached Luna control reported zero reads and writes. Same prompt text and segmentation were used for both Luna arms, with only cache metadata changed. Lower observed latency in this run does not identify provider-only inference latency.")


def write_notes(manifest, notes_path):
    paragraphs = ["# Figure notes", "", "All figures are calculated from saved measured response records. No additional model calls were made. White background; Jev navy (#173D5E), Luna orange (#C96513), with line or marker differences so comparisons do not rely on colour alone. SVG preserves text and PNG exports use 260 dpi.", ""]
    for i, (name, item) in enumerate(manifest["figures"].items(), 1):
        paragraphs += [f"## Figure {i}: {item['title']}", "", f"Files: `outputs/benchmark/figures/{name}.svg` and `.png`. PNG: {item['png_pixels'][0]} × {item['png_pixels'][1]} pixels. SVG: {item['size_inches'][0]} × {item['size_inches'][1]} inches.", "", item["caption"], ""]
    paragraphs += ["## Reproduction and evidence", "", "Run `work/figure-venv/bin/python outputs/benchmark/paper_figures.py` from the workspace. Dependencies: Matplotlib " + matplotlib.__version__ + ", NumPy " + np.__version__ + ". Inputs and SHA-256 hashes, exact figure metrics, bootstrap seed, sample count, and export hashes are in `outputs/benchmark/figures/figure-manifest.json`. The compact CSV files retain plotted case identities, targets, predictions, latency, cost, and family. They exclude prompts and credentials.", "", "The observed protocol has one request per model per case. Consequently, task latency distributions reflect both request conditions and varying cases; there are no within-case repeated timing runs. The optional exploratory family bootstrap resamples case identities, not new model responses. Its 95% intervals are not adjusted for multiple comparisons. No generalized model-ranking claim is encoded in the figures.", ""]
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    notes_path.write_text("\n".join(paragraphs))


def write_plot_rows(rows, cases, path):
    fields = ["case_id", "experiment", "model_label", "target", "probability", "absolute_error", "latency_s", "cost_usd", "probability_family"]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            row = {key: r.get(key, "") for key in fields}
            row["absolute_error"] = abs(r["probability"] - r["target"])
            row["probability_family"] = cases.get(r["case_id"], {}).get("source", {}).get("probability_family", "")
            writer.writerow(row)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--main", type=Path, default=MAIN_DEFAULT)
    parser.add_argument("--cache", type=Path, default=CACHE_DEFAULT)
    parser.add_argument("--cases", type=Path, default=ROOT / "data" / "cases.jsonl")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "figures")
    parser.add_argument("--notes", type=Path, default=ROOT.parent.parent / "work" / "figure-notes.md")
    args = parser.parse_args()
    main_rows = measured(read_rows(args.main))
    all_cache = read_rows(args.cache)
    cache_rows = measured(all_cache)
    cases = {r["id"]: r for r in read_rows(args.cases)}
    validate(main_rows, cache_rows, cases)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    style()
    manifest = {"inputs": {"main": {"path": str(args.main), "sha256": sha(args.main)},
                           "cache": {"path": str(args.cache), "sha256": sha(args.cache)},
                           "cases": {"path": str(args.cases), "sha256": sha(args.cases)}},
                "generator": {"path": str(Path(__file__).resolve()), "sha256": sha(__file__), "python": sys.version,
                              "matplotlib": matplotlib.__version__, "numpy": np.__version__, "bootstrap_samples": BOOTSTRAP_SAMPLES,
                              "bootstrap_seed": BOOTSTRAP_SEED, "bootstrap_unit": "case identity within probability family, retaining model pairing"},
                "population": {"main_measured_requests": len(main_rows), "cache_measured_requests": len(cache_rows), "cache_prime_requests": 1},
                "figures": {}, "metrics": {}}
    fig_latency(main_rows, args.out_dir, manifest)
    fig_probability(main_rows, cases, args.out_dir, manifest)
    fig_cache(cache_rows, all_cache, args.out_dir, manifest)
    write_plot_rows(main_rows, cases, args.out_dir / "main-plot-data.csv")
    write_plot_rows(cache_rows, cases, args.out_dir / "cache-plot-data.csv")
    (args.out_dir / "figure-manifest.json").write_text(json.dumps(manifest, indent=2))
    write_notes(manifest, args.notes)
    print(json.dumps({"figures": list(manifest["figures"]), "output_directory": str(args.out_dir), "notes": str(args.notes), "population": manifest["population"]}, indent=2))


if __name__ == "__main__":
    main()
