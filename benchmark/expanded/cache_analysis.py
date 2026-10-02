#!/usr/bin/env python3
"""Offline cache-study analysis with counterfactual-pair clustered intervals.

No request code is imported and no network calls are made. The four observed
time blocks are held fixed; resampling addresses case variation within blocks.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import random
import statistics

ARMS = ("Jev", "Luna uncached", "Luna cached")
PAIRS = (("Luna cached", "Luna uncached"), ("Luna cached", "Jev"), ("Luna uncached", "Jev"))


def percentile(values, p):
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    lower = int(position)
    return ordered[lower] + (ordered[min(lower + 1, len(ordered) - 1)] - ordered[lower]) * (position - lower)


def validate(records):
    rows = [r for r in records if r["phase"] == "measured"]
    primes = [r for r in records if r["phase"] == "cache_prime"]
    if len(rows) != 288 or len(primes) != 4 or {r["block"] for r in primes} != set(range(4)):
        raise ValueError("Analysis requires the complete 288-measured-call, four-prime protocol")
    if any(not r["valid"] or r.get("cost_usd") is None for r in records):
        raise ValueError("Analysis requires valid outputs and complete recorded billing")
    by_case = defaultdict(dict)
    groups = defaultdict(lambda: defaultdict(list))
    for row in rows:
        p, cost, latency = row["probability"], row["cost_usd"], row["latency_s"]
        if not (math.isfinite(p) and 0 <= p <= 1 and math.isfinite(cost) and cost >= 0 and math.isfinite(latency) and latency > 0):
            raise ValueError("Invalid measured value")
        if row["model_label"] in by_case[row["case_id"]]:
            raise ValueError("Duplicate case/model response")
        by_case[row["case_id"]][row["model_label"]] = row
    if len(by_case) != 96:
        raise ValueError("Expected 96 unique case identities")
    for case_id, arms in by_case.items():
        if set(arms) != set(ARMS):
            raise ValueError("Case is missing a model arm")
        first = arms["Jev"]
        if first["target"] not in (0, 1):
            raise ValueError("Nonbinary target")
        for row in arms.values():
            if row["target"] != first["target"] or row["block"] != first["block"] or row["pair_id"] != first["pair_id"]:
                raise ValueError("Model records disagree about case identity or target")
        groups[first["block"]][first["pair_id"]].append(case_id)
    if set(groups) != set(range(4)) or any(len(pairs) != 12 for pairs in groups.values()):
        raise ValueError("Expected 12 counterfactual pairs in each block")
    for block_pairs in groups.values():
        for ids in block_pairs.values():
            if len(ids) != 2 or {by_case[i]["Jev"]["target"] for i in ids} != {0, 1}:
                raise ValueError("Each counterfactual pair must contain a yes and a no case")
    return rows, primes, by_case, groups


def pair_metrics(items):
    # Each item contains the two model responses on the same case.
    def correct(row):
        return int((row["probability"] >= .5) == bool(row["target"]))
    denominator = sum(b["cost_usd"] for a, b in items)
    return {"accuracy_difference": statistics.mean(correct(a) - correct(b) for a, b in items),
            "brier_difference": statistics.mean((a["probability"] - a["target"]) ** 2 - (b["probability"] - b["target"]) ** 2 for a, b in items),
            "median_paired_latency_ratio": statistics.median(a["latency_s"] / b["latency_s"] for a, b in items),
            "mean_latency_difference_s": statistics.mean(a["latency_s"] - b["latency_s"] for a, b in items),
            "cost_ratio": sum(a["cost_usd"] for a, b in items) / denominator if denominator else None,
            "label_agreement": statistics.mean((a["probability"] >= .5) == (b["probability"] >= .5) for a, b in items)}


def analyse(records, metadata, draws=5000, seed=20261019):
    if draws < 1:
        raise ValueError("draws must be positive")
    rows, primes, by_case, groups = validate(records)
    result = {"metadata": metadata, "population": {"measured_calls": len(rows), "cases": 96,
                "counterfactual_pairs": 48, "blocks": 4, "primes": 4},
              "bootstrap": {"draws": draws, "seed": seed, "confidence_level": .95,
                "unit": "Counterfactual pair_id (two correlated cases), keeping all three model responses together.",
                "stratification": "Resample 12 pair identities with replacement within each of the four observed blocks. Hold blocks fixed.",
                "scope": "Exploratory percentile intervals over these synthetic cases, conditional on this run and its four blocks. No multiple-comparison adjustment; no uncertainty over days, providers, or new model draws."},
              "comparisons": {}, "blocks": [], "counterfactual_pair_accuracy": {}}
    block_clusters = [[ids for _, ids in sorted(groups[block].items())] for block in range(4)]
    for index, (left, right) in enumerate(PAIRS):
        rng = random.Random(seed + index)
        items = [(by_case[i][left], by_case[i][right]) for i in sorted(by_case)]
        estimate = pair_metrics(items)
        samples = {key: [] for key in estimate}
        for _ in range(draws):
            selected_ids = [i for clusters in block_clusters for _ in range(12) for i in rng.choice(clusters)]
            metrics = pair_metrics([(by_case[i][left], by_case[i][right]) for i in selected_ids])
            for key, value in metrics.items():
                if value is not None:
                    samples[key].append(value)
        result["comparisons"][left + " / " + right] = {"paired_cases": 96, "clusters": 48,
            "difference_direction": left + " minus " + right, "ratio_direction": left + " divided by " + right,
            "metrics": {key: {"estimate": value, "ci95": [percentile(samples[key], .025), percentile(samples[key], .975)] if samples[key] else None,
                              "defined_draws": len(samples[key])} for key, value in estimate.items()}}
    for arm in ARMS:
        pair_correct = [all((by_case[i][arm]["probability"] >= .5) == bool(by_case[i][arm]["target"]) for i in ids)
                        for clusters in block_clusters for ids in clusters]
        result["counterfactual_pair_accuracy"][arm] = {"both_cases_correct": sum(pair_correct), "pairs": len(pair_correct), "fraction": statistics.mean(pair_correct)}
    for block in range(4):
        prime = next(p for p in primes if p["block"] == block)
        block_result = {"block": block, "prime_cost_usd": prime["cost_usd"], "prime_latency_s": prime["latency_s"],
                        "prime_cache_details": prime.get("usage", {}).get("prompt_tokens_details"), "arms": {}}
        for arm in ARMS:
            arm_rows = [r for r in rows if r["block"] == block and r["model_label"] == arm]
            latency = [r["latency_s"] for r in arm_rows]
            cost = sum(r["cost_usd"] for r in arm_rows)
            reads = [(r.get("usage", {}).get("prompt_tokens_details") or {}).get("cached_tokens") for r in arm_rows]
            writes = [(r.get("usage", {}).get("prompt_tokens_details") or {}).get("cache_write_tokens") for r in arm_rows]
            block_result["arms"][arm] = {"n": len(arm_rows), "accuracy": statistics.mean((r["probability"] >= .5) == bool(r["target"]) for r in arm_rows),
                "median_latency_s": statistics.median(latency), "p95_latency_s": percentile(latency, .95),
                "cost_usd": cost, "cost_per_1000_usd": cost / len(arm_rows) * 1000,
                "cache_read_tokens": sum(reads) if all(isinstance(x, int) for x in reads) else None,
                "cache_write_tokens": sum(writes) if all(isinstance(x, int) for x in writes) else None,
                "cache_hit_calls": sum(isinstance(x, int) and x > 0 for x in reads),
                "cache_counter_observed_n": sum(isinstance(x, int) for x in reads)}
            if arm == "Luna cached":
                block_result["arms"][arm]["cost_per_1000_with_prime_usd"] = (cost + prime["cost_usd"]) / len(arm_rows) * 1000
        result["blocks"].append(block_result)
    return result


def write_markdown(result, path):
    def number(v, digits=4):
        return "unknown" if v is None else f"{v:.{digits}f}"
    lines = ["# Repeated cache study: analysis", "", "Four sequential blocks, 96 distinct policy cases, and 48 counterfactual pairs. Each block has a separate prefix and one priming request. The 5,000-draw bootstrap resamples counterfactual pairs within each block, keeping both cases and all model arms together. Intervals describe case variation within this run; blocks are held fixed.", "",
             "## Paired comparisons", "", "For differences, the arm before the slash minus the arm after it. For ratios, the arm before the slash divided by the arm after it.", "",
             "| Comparison | Metric | Estimate | Exploratory 95% interval |", "|---|---|---:|---:|"]
    for pair, comparison in result["comparisons"].items():
        for metric, value in comparison["metrics"].items():
            interval = " to ".join(number(x) for x in value["ci95"]) if value["ci95"] else "unknown"
            lines.append(f"| {pair} | {metric} | {number(value['estimate'])} | {interval} |")
    lines += ["", "## Block results", "", "Measured calls exclude priming. Cost per 1,000 is a projection at the measured request mix. The final column allocates that block's separate prime across its 24 cached cases.", "",
              "| Block | Arm | Cases | Accuracy | Median s | p95 s | USD / 1,000 | USD / 1,000 with prime | Read tokens | Write tokens |",
              "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for block in result["blocks"]:
        for arm, s in block["arms"].items():
            lines.append(f"| {block['block']+1} | {arm} | {s['n']} | {number(s['accuracy'])} | {number(s['median_latency_s'])} | {number(s['p95_latency_s'])} | {number(s['cost_per_1000_usd'])} | {number(s.get('cost_per_1000_with_prime_usd'))} | {number(s['cache_read_tokens'],0)} | {number(s['cache_write_tokens'],0)} |")
    lines += ["", "## Counterfactual consistency", "", "A pair passes only if both its eligible case and its one-fact ineligible counterpart are classified correctly.", ""]
    for arm, value in result["counterfactual_pair_accuracy"].items():
        lines.append(f"- {arm}: {value['both_cases_correct']}/{value['pairs']} pairs.")
    lines += ["", "These are synthetic written-policy cases. The new identifiers establish separate cache prefixes; they do not create independent days, providers, or repeated model draws on the same case. No general model ranking or cache-induced change in answer quality follows from this experiment alone.", ""]
    path.write_text("\n".join(lines))


def run_analysis(run_dir, draws=5000, seed=20261019):
    directory = Path(run_dir)
    raw = directory / "responses.jsonl"
    metadata = json.loads((directory / "metadata.json").read_text())
    if metadata["status"] != "complete":
        raise ValueError("The cache run is not complete; preserve partial results without full-study intervals")
    records = [json.loads(line) for line in raw.read_text().splitlines() if line.strip()]
    result = analyse(records, metadata, draws, seed)
    result["provenance"] = {"responses_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
                            "analysis_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    output = directory / "clustered-analysis.json"
    output.write_text(json.dumps(result, indent=2))
    write_markdown(result, directory / "clustered-analysis.md")
    return {"analysis_path": str(output), "population": result["population"], "bootstrap": result["bootstrap"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(run_analysis(args.run_dir), indent=2))
