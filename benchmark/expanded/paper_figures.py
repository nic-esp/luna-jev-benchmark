#!/usr/bin/env python3
"""Expanded-study publication figures, from completed runs only.

Run with the workspace's work/figure-venv/bin/python. Requires Matplotlib and
NumPy. No API calls; the generator refuses running or incomplete analyses.
"""
import argparse
from collections import defaultdict
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import statistics
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
STYLE_PATH = ROOT.parent / "paper_figures.py"
spec = importlib.util.spec_from_file_location("publication_figure_style", STYLE_PATH)
common = importlib.util.module_from_spec(spec)
spec.loader.exec_module(common)
plt, np = common.plt, common.np
NAVY, ORANGE, GREY, TEXT = common.NAVY, common.ORANGE, common.GREY, common.TEXT
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import PercentFormatter, FixedLocator, FuncFormatter, NullLocator

TASKS = ("boolq", "rte", "wic", "policy", "probability")
NAMES = {"boolq": "BoolQ", "rte": "RTE", "wic": "WiC", "policy": "Synthetic policy", "probability": "Exact probability"}
COLORS = {"Jev": NAVY, "Luna": ORANGE, "Luna cached": ORANGE, "Luna uncached": ORANGE}


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def valid(row):
    p = row.get("probability")
    return row.get("valid") is True and isinstance(p, (int, float)) and not isinstance(p, bool) and math.isfinite(p) and 0 <= p <= 1


def completed_run(kind, explicit=None):
    if explicit:
        candidates = [Path(explicit)]
    else:
        candidates = []
        for path in (ROOT / "runs").iterdir():
            meta_path = path / "metadata.json"
            if not meta_path.exists():
                continue
            meta = json.loads(meta_path.read_text())
            matches = (kind == "quality" and meta.get("phase") == "quality") or (kind == "timing" and "repeats" in meta) or (kind == "cache" and meta.get("experiment") == "expanded_cache_policy")
            if matches and meta.get("status") == "complete":
                candidates.append(path)
        candidates.sort(key=lambda p: json.loads((p / "metadata.json").read_text()).get("created_at", ""))
    if not candidates:
        raise ValueError(f"No completed {kind} run; figures must wait for final data")
    directory = candidates[-1]
    metadata = json.loads((directory / "metadata.json").read_text())
    if metadata.get("status") != "complete":
        raise ValueError(f"The {kind} run is not complete; no partial scores will be read")
    return directory, metadata, read_rows(directory / "responses.jsonl")


def fig_quality(summary, out, manifest):
    tasks = TASKS[:4]
    fig, (ax, counts) = plt.subplots(1, 2, figsize=(12.4, 5.3), gridspec_kw={"width_ratios": [4.5, 1.2]}, sharey=True)
    fig.subplots_adjust(left=.15, right=.98, top=.78, bottom=.19, wspace=.13)
    common.panel_title(ax, "A", "Binary-task agreement with the frozen reference labels")
    numbers = {}
    for index, task in enumerate(tasks):
        numbers[task] = {}
        for model, offset, marker in (("Jev", -.14, "o"), ("Luna", .14, "s")):
            source = summary["tasks"][task]["models"][model]
            score = source["quality"]
            value = score["accuracy_all_attempts"]
            lower, upper = score["accuracy_all_attempts_wilson95"]
            if lower > value + 1e-12 or upper < value - 1e-12:
                raise ValueError("Accuracy estimate lies outside its reported Wilson interval")
            ax.errorbar(value, index + offset, xerr=[[max(0, value-lower)], [max(0, upper-value)]], fmt=marker,
                        color=COLORS[model], markersize=7, elinewidth=1.5, capsize=3, markeredgecolor="white", markeredgewidth=.5,
                        label=model if index == 0 else None, clip_on=False, zorder=3)
            counts.text(.0, index + offset, f"{score['correct']:,} / {source['attempts']:,}", va="center", fontsize=10, color=COLORS[model])
            counts.text(.73, index + offset, f"{value:.1%}", va="center", fontsize=10, color=COLORS[model])
            numbers[task][model] = {"accuracy": value, "wilson95": [lower, upper], "correct": score["correct"], "attempts": source["attempts"], "invalid": source["invalid"]}
    ax.set_xlim(0, 1)
    ax.set_ylim(3.5, -.6)
    ax.set_yticks(range(4), [NAMES[x] for x in tasks])
    ax.set_xticks([0, .2, .4, .6, .8, 1])
    ax.xaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    ax.set_xlabel("Accuracy across all attempted cases", labelpad=11)
    ax.tick_params(axis="y", length=0)
    common.axis_base(ax, "x")
    counts.set_xlim(0, 1.05)
    counts.axis("off")
    counts.text(0, 1.08, "Correct / attempts", transform=counts.transAxes, fontsize=10, color=GREY)
    ax.legend(loc="upper left", bbox_to_anchor=(0, 1.075), ncol=2, handletextpad=.5, columnspacing=2)
    fig.text(.055, .05, "Invalid responses count as incorrect. Bars show nominal 95% Wilson intervals; the four tasks have different reference standards.", fontsize=9, color=GREY)
    manifest["metrics"]["binary_quality"] = numbers
    common.save(fig, "fig4-quality", out, manifest, "Expanded binary-task quality",
        "Accuracy on four binary tasks, counting every invalid response as incorrect. Points show observed accuracy and horizontal intervals are nominal 95% Wilson intervals. The table gives correct answers over all attempted cases. BoolQ, RTE, and WiC measure agreement with published reference labels; synthetic policy targets come from a deterministic oracle. The intervals are exploratory, do not account for every shared passage or template, and should not be used to infer model differences from overlap alone.")


def fig_probability(summary, quality_rows, out, manifest):
    fig, axes = plt.subplots(1, 2, figsize=(11.2, 5.7))
    fig.subplots_adjust(left=.08, right=.96, top=.79, bottom=.20, wspace=.26)
    numbers = {}
    for ax, model, letter in zip(axes, ("Jev", "Luna"), "AB"):
        attempted = [r for r in quality_rows if r["phase"] == "quality" and r["experiment"] == "probability" and r["model_label"] == model]
        rows = [r for r in attempted if valid(r)]
        source = summary["tasks"]["probability"]["models"][model]
        if len(attempted) != source["attempts"] or len(rows) != source["valid"]:
            raise ValueError("Probability plot denominator disagrees with audited summary")
        mae = statistics.mean(abs(r["probability"]-r["target"]) for r in rows)
        if not math.isclose(mae, source["quality"]["mae_valid_only"], rel_tol=1e-10, abs_tol=1e-12):
            raise ValueError("Probability plot score does not reconcile with summary")
        common.panel_title(ax, letter, model + " probability estimates")
        ax.plot([0, 1], [0, 1], color=GREY, linestyle=(0, (3, 3)), lw=1, zorder=1)
        ax.scatter([r["target"] for r in rows], [r["probability"] for r in rows], s=22, marker="o" if model == "Jev" else "s",
                   color=COLORS[model], alpha=.63, edgecolors="white", linewidths=.4, clip_on=False, zorder=3)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.set_xticks([0, .25, .5, .75, 1])
        ax.set_yticks([0, .25, .5, .75, 1])
        ax.xaxis.set_major_formatter(PercentFormatter(1, decimals=0))
        ax.yaxis.set_major_formatter(PercentFormatter(1, decimals=0))
        ax.set_xlabel("Exact event probability", labelpad=10)
        ax.set_ylabel("Predicted probability of yes", labelpad=10)
        ax.set_aspect("equal", adjustable="box")
        common.axis_base(ax, "both")
        ax.text(0, 1.04, f"{len(rows)}/{len(attempted)} valid · MAE {mae*100:.2f} percentage points", transform=ax.transAxes, fontsize=9, color=GREY)
        numbers[model] = {"attempts": len(attempted), "valid": len(rows), "invalid": len(attempted)-len(rows), "mae": mae,
                          "plotted_case_ids": [r["case_id"] for r in rows]}
    fig.text(.06, .06, "Each mark is a valid response; overlapping marks are retained. Dashed diagonal: exact agreement.", fontsize=9, color=GREY)
    fig.text(.06, .022, "The 300 prespecified problems have exact mathematical targets. This task does not test real-world forecasting calibration.", fontsize=9, color=GREY)
    manifest["metrics"]["exact_probability"] = numbers
    common.save(fig, "fig5-probability", out, manifest, "Expanded probability estimates against exact targets",
        "Model-generated probabilities versus exact event probabilities on 300 prespecified finite probability problems. Each panel reports its own valid-response denominator and mean absolute error in percentage points. Invalid responses have no plottable probability and are omitted here but retained in the study's failure accounting. The dashed diagonal is exact agreement. No jitter is added to the probability values, and overlapping marks remain present.")


def timing_case_points(rows, repeats):
    expected = set(range(repeats))
    grouped = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        if r["phase"] != "timing":
            continue
        if r["repeat"] in grouped[r["case_id"]][r["model_label"]]:
            raise ValueError("Duplicate timing repeat")
        grouped[r["case_id"]][r["model_label"]][r["repeat"]] = r
    points = defaultdict(list)
    for case_id, arms in sorted(grouped.items()):
        if not all(set(arms[m]) == expected for m in ("Jev", "Luna")):
            continue
        if not all(valid(r) and isinstance(r.get("latency_s"), (int, float)) and math.isfinite(r["latency_s"]) and r["latency_s"] > 0 for m in ("Jev", "Luna") for r in arms[m].values()):
            continue
        task = arms["Jev"][0]["experiment"]
        points[task].append({"case_id": case_id, **{m: statistics.median(r["latency_s"] for r in arms[m].values()) for m in ("Jev", "Luna")}})
    return points


def fig_timing(summary, timing_rows, metadata, out, manifest):
    if summary.get("timing") is None or summary["timing"]["status"] != "complete":
        raise ValueError("Completed isolated timing analysis is required")
    repeats = metadata["repeats"]
    points = timing_case_points(timing_rows, repeats)
    if set(points) != set(TASKS):
        raise ValueError("The five timing tasks are not all represented")
    fig, axes = plt.subplots(1, 5, figsize=(12.4, 5.0), sharey=True)
    fig.subplots_adjust(left=.07, right=.985, top=.79, bottom=.27, wspace=.18)
    values = [p[m] for task in TASKS for p in points[task] for m in ("Jev", "Luna")]
    lower = min(values) / 1.2
    upper = max(values) * 1.2
    ticks = [x for x in (.05, .1, .2, .3, .5, 1, 2, 3, 5, 10, 20) if lower <= x <= upper]
    for ax, task, letter in zip(axes, TASKS, "ABCDE"):
        common.panel_title(ax, letter, NAMES[task].replace("Synthetic policy", "Policy").replace("Exact probability", "Probability"))
        task_points = points[task]
        source = summary["timing"]["tasks"][task]
        if len(task_points) != source["complete_valid_paired_cases"]:
            raise ValueError("Timing point count disagrees with audited summary")
        for i, point in enumerate(task_points):
            jitter = ((i * 7) % 13 - 6) / 80
            ax.plot([jitter, 1+jitter], [point["Jev"], point["Luna"]], color="#CCD2D8", lw=.65, alpha=.7, zorder=1)
            for j, model, marker in ((0, "Jev", "o"), (1, "Luna", "s")):
                ax.scatter(j+jitter, point[model], s=23, color=COLORS[model], marker=marker, edgecolors="white", linewidth=.45, zorder=3)
        medians = {}
        for j, model in enumerate(("Jev", "Luna")):
            median = statistics.median(p[model] for p in task_points)
            medians[model] = median
            if not math.isclose(median, source["primary_case_median_latency_s"][model], rel_tol=1e-10):
                raise ValueError("Timing median does not reconcile with summary")
            ax.hlines(median, j-.22, j+.22, color=COLORS[model], linewidth=3.0, zorder=4)
        ax.set_xlim(-.38, 1.38)
        ax.set_xticks([0, 1], ["Jev", "Luna"])
        ax.set_yscale("log")
        ax.set_ylim(lower, upper)
        ax.yaxis.set_major_locator(FixedLocator(ticks))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:g}"))
        ax.yaxis.set_minor_locator(NullLocator())
        common.axis_base(ax)
        ax.text(0, 1.04, f"{len(task_points)} paired cases × {repeats} repeats", transform=ax.transAxes, fontsize=8.6, color=GREY)
        ax.text(.5, -.18, f"Medians\n{medians['Jev']:.3f} / {medians['Luna']:.3f} s", transform=ax.transAxes, fontsize=9, ha="center", va="top", linespacing=1.5)
    axes[0].set_ylabel("Within-case median latency (seconds; log scale)", labelpad=10)
    fig.text(.06, .055, "Each dot is one case's median over three serial repeats; grey lines pair the same case. Heavy bars mark medians across cases.", fontsize=9, color=GREY)
    fig.text(.06, .017, "Only cases with valid timing responses in all repeats for both models are included. Warmups and concurrent bulk timings are excluded.", fontsize=9, color=GREY)
    manifest["metrics"]["timing_case_points"] = dict(points)
    common.save(fig, "fig6-timing", out, manifest, "Isolated repeated latency across five tasks",
        "The isolated serial timing study selected 12 cases per task before outcomes were inspected and repeated them three times per model. Each plotted point is one case/model's median over those three repeats. Grey connectors pair the same case across models, and heavy horizontal bars mark medians across case medians. Cases missing a valid positive timing in either arm in any repeat are excluded with counts retained in the analysis. Common logarithmic time axes are used. No concurrent quality-run latency is included.")


def fig_cache(cache_summary, rows, out, manifest):
    if cache_summary["metadata"]["status"] != "complete":
        raise ValueError("Cache figures require a completed four-block run")
    measured = [r for r in rows if r["phase"] == "measured"]
    if len(measured) != 288:
        raise ValueError("Expected 96 cases with three arms in the cache figure")
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 5.8))
    fig.subplots_adjust(left=.08, right=.98, top=.79, bottom=.28, wspace=.27)
    ax = axes[0]
    common.panel_title(ax, "A", "Cost by separately primed block")
    offsets = {"Jev": -.24, "Luna uncached": 0, "Luna cached": .24}
    cost_rows = []
    max_cost = 0
    for block in cache_summary["blocks"]:
        index = block["block"]
        for arm in ("Jev", "Luna uncached", "Luna cached"):
            source = block["arms"][arm]
            cost = source["cost_per_1000_usd"]
            if cost is None or source["n"] != 24:
                raise ValueError("Cache block lacks complete measured billing")
            face = "#FAE9DC" if arm == "Luna uncached" else COLORS[arm]
            ax.bar(index+offsets[arm], cost, width=.2, color=face, edgecolor=COLORS[arm], linewidth=1, zorder=2)
            prime_allocation = 0
            if arm == "Luna cached":
                prime_allocation = block["prime"]["cost_usd"] / source["n"] * 1000
                ax.bar(index+offsets[arm], prime_allocation, bottom=cost, width=.2, facecolor="white", edgecolor=ORANGE, linewidth=1, hatch="////", zorder=3)
            cost_rows.append({"block": index, "arm": arm, "measured_cost_per_1000_usd": cost, "allocated_prime_cost_per_1000_usd": prime_allocation})
            max_cost = max(max_cost, cost+prime_allocation)
    ax.set_ylim(0, max_cost*1.16)
    ax.set_xticks(range(4), [f"Block {i+1}" for i in range(4)])
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"${x:.2f}"))
    ax.set_ylabel("Cost per 1,000 decisions (USD)", labelpad=10)
    common.axis_base(ax)
    ax.text(0, 1.04, "24 cases per arm per block; one prime allocated over 24", transform=ax.transAxes, fontsize=8.6, color=GREY)
    handles = [Patch(facecolor=NAVY, edgecolor=NAVY, label="Jev"), Patch(facecolor="#FAE9DC", edgecolor=ORANGE, label="Luna uncached"),
               Patch(facecolor=ORANGE, edgecolor=ORANGE, label="Luna cached"), Patch(facecolor="white", edgecolor=ORANGE, hatch="////", label="Separate prime allocation")]
    ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(-.04, -.14), ncol=2, columnspacing=1.0, handlelength=1.4)
    ax = axes[1]
    common.panel_title(ax, "B", "Latency within each block")
    latency_rows = []
    all_values = []
    for arm in ("Jev", "Luna uncached", "Luna cached"):
        medians = []
        for block in range(4):
            selected = sorted([r for r in measured if r["block"] == block and r["model_label"] == arm], key=lambda r: r["case_id"])
            if len(selected) != 24 or any(not valid(r) or not math.isfinite(r["latency_s"]) or r["latency_s"] <= 0 for r in selected):
                raise ValueError("Incomplete valid cache latency block")
            values = [r["latency_s"] for r in selected]
            all_values.extend(values)
            x = [block+offsets[arm]+((i*7)%25-12)/180 for i in range(24)]
            ax.scatter(x, values, color=COLORS[arm], alpha=.24, s=13, marker="o", linewidths=0, zorder=2)
            median = statistics.median(values)
            medians.append(median)
            expected = cache_summary["blocks"][block]["arms"][arm]["median_latency_s"]
            if not math.isclose(median, expected, rel_tol=1e-10):
                raise ValueError("Cache latency does not reconcile with summary")
            latency_rows.append({"block": block, "arm": arm, "median_latency_s": median, "observations": values})
        marker = "o" if arm == "Jev" else "s"
        face = "white" if arm == "Luna uncached" else COLORS[arm]
        ax.plot([i+offsets[arm] for i in range(4)], medians, marker=marker, ms=6, linestyle="--" if arm == "Luna uncached" else "-",
                color=COLORS[arm], mfc=face, mec=COLORS[arm], lw=1.5, label=arm, zorder=4)
    ax.set_yscale("log")
    low, high = min(all_values)/1.15, max(all_values)*1.15
    ticks = [x for x in (.1, .2, .3, .5, 1, 2, 3, 5, 10, 20) if low <= x <= high]
    ax.set_ylim(low, high)
    ax.yaxis.set_major_locator(FixedLocator(ticks))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:g}"))
    ax.yaxis.set_minor_locator(NullLocator())
    ax.set_xticks(range(4), [f"Block {i+1}" for i in range(4)])
    ax.set_ylabel("End-to-end latency (seconds; log scale)", labelpad=10)
    common.axis_base(ax)
    ax.text(0, 1.04, "Faint dots: individual calls; connected marks: medians", transform=ax.transAxes, fontsize=8.6, color=GREY)
    ax.legend(loc="upper left", bbox_to_anchor=(-.04, -.14), ncol=2, columnspacing=1.1, handlelength=2)
    verification = cache_summary["cache_verification"]
    fig.text(.055, .067, f"Observed cache controls: {verification['cold_primed_blocks']}/4 cold primes; {verification['blocks_with_all_24_cache_hits']}/4 blocks with 24/24 hits; {verification['blocks_with_verified_uncached_control']}/4 clean uncached controls.", fontsize=9, color=GREY)
    fig.text(.055, .027, "Each block uses a new common prefix and 24 distinct cases. Priming latency is excluded; the four time blocks are from one session.", fontsize=9, color=GREY)
    manifest["metrics"]["cache_cost_by_block"] = cost_rows
    manifest["metrics"]["cache_latency_by_block"] = latency_rows
    manifest["metrics"]["cache_verification"] = verification
    common.save(fig, "fig7-cache", out, manifest, "Four-block cache cost and latency",
        "Four separately primed long-prefix blocks, each containing 24 distinct deterministic policy cases and all three model arms. Panel A scales each block's actual measured mean cost to 1,000 decisions. The white hatched extension on cached Luna is that block's distinct priming charge divided across its 24 measured cases. Panel B shows individual complete-response latencies as faint dots and block medians as connected marks, excluding all priming requests. The four blocks share one session and are not independent days or deployments. Returned cache counters establish treatment status; no cache-effect interpretation is warranted for a failed control.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, default=ROOT / "summary.json")
    parser.add_argument("--quality-dir", type=Path)
    parser.add_argument("--timing-dir", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "figures")
    args = parser.parse_args()
    summary = json.loads(args.summary.read_text())
    if summary["quality_run"]["metadata"]["status"] != "complete" or summary["quality_run"]["missing_attempts"] != 0:
        raise ValueError("Wait for the completed quality analysis; partial scores are not plotted")
    qdir, qmeta, quality = completed_run("quality", args.quality_dir)
    tdir, tmeta, timing = completed_run("timing", args.timing_dir)
    cdir, cmeta, cache = completed_run("cache", args.cache_dir)
    if sha(qdir / "responses.jsonl") != summary["quality_records_sha256"]:
        raise ValueError("Quality raw data does not match the audited analysis")
    cache_summary = json.loads((cdir / "summary.json").read_text())
    manifest = {"inputs": {"summary": {"path": str(args.summary), "sha256": sha(args.summary)},
                           "quality": {"path": str(qdir / "responses.jsonl"), "sha256": sha(qdir / "responses.jsonl")},
                           "timing": {"path": str(tdir / "responses.jsonl"), "sha256": sha(tdir / "responses.jsonl")},
                           "cache": {"path": str(cdir / "responses.jsonl"), "sha256": sha(cdir / "responses.jsonl")}},
                "generator": {"path": str(Path(__file__).resolve()), "sha256": sha(__file__), "shared_style_sha256": sha(STYLE_PATH),
                              "matplotlib": common.matplotlib.__version__, "numpy": np.__version__}, "figures": {}, "metrics": {}}
    args.out_dir.mkdir(parents=True, exist_ok=True)
    common.style()
    fig_quality(summary, args.out_dir, manifest)
    fig_probability(summary, quality, args.out_dir, manifest)
    fig_timing(summary, timing, tmeta, args.out_dir, manifest)
    fig_cache(cache_summary, cache, args.out_dir, manifest)
    (args.out_dir / "figure-manifest.json").write_text(json.dumps(manifest, indent=2))
    notes = ["# Expanded-study figure captions", "", "All figures use completed runs and are reconciled to the audited analysis. The retained pilot figures are unchanged.", ""]
    for name, item in manifest["figures"].items():
        notes += [f"## {name}", "", f"PNG: {item['png_pixels'][0]} × {item['png_pixels'][1]} pixels. SVG preserves vector geometry and text.", "", item["caption"], ""]
    (args.out_dir / "captions.md").write_text("\n".join(notes))
    print(json.dumps({"output_directory": str(args.out_dir), "figures": list(manifest["figures"]), "quality_run": str(qdir), "timing_run": str(tdir), "cache_run": str(cdir)}, indent=2))


if __name__ == "__main__":
    main()
