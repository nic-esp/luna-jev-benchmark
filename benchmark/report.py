#!/usr/bin/env python3
"""Dependency-free, auditable reporting for the Jev/Luna paired benchmark.

    python report.py runs.jsonl --output-dir results --metadata run.json

All quality summaries use measured requests only. Missing usage/cost is never
treated as zero. Paired bootstrap samples are drawn at the case level, retaining
the dependence between models and clustering any repeated case measurements.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import random
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MODELS = ("Jev", "Luna")
EPSILON = 1e-15
TITLES = {
    "policy": "Policy decisions",
    "boolq": "Reading comprehension · BoolQ",
    "probability": "Exact probabilities",
}


def finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def percentile(values: list[float], q: float) -> float | None:
    """Linear interpolation between sorted sample values (inclusive endpoints)."""
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * q
    low, high = math.floor(index), math.ceil(index)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def valid_prediction(record: dict[str, Any]) -> bool:
    prediction, target = record.get("probability"), record.get("target")
    return (
        record.get("valid") is True
        and finite_number(prediction)
        and 0 <= prediction <= 1
        and finite_number(target)
        and 0 <= target <= 1
        and record.get("target_kind") in ("label", "probability")
        and (record.get("target_kind") != "label" or target in (0, 1))
    )


def loss_values(record: dict[str, Any]) -> dict[str, float]:
    prediction, target = record["probability"], record["target"]
    squared = (prediction - target) ** 2
    if record["target_kind"] == "probability":
        return {
            "mae": abs(prediction - target),
            "excess_brier": squared,
            "expected_brier": squared + target * (1 - target),
        }
    clipped = min(1 - EPSILON, max(EPSILON, prediction))
    return {
        "accuracy": float((prediction >= 0.5) == bool(target)),
        "brier": squared,
        "log_loss": -(target * math.log(clipped) + (1 - target) * math.log1p(-clipped)),
    }


def latency_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    values = [r["latency_s"] for r in records if finite_number(r.get("latency_s")) and r["latency_s"] >= 0]
    return {"count": len(values), "p50": percentile(values, 0.5), "p95": percentile(values, 0.95), "mean": mean(values)}


def cost_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    costs = [r["cost_usd"] for r in records if finite_number(r.get("cost_usd")) and r["cost_usd"] >= 0]
    missing = len(records) - len(costs)
    total = math.fsum(costs)
    return {
        "known_total_usd": total,
        "known_count": len(costs),
        "missing_count": missing,
        "complete": missing == 0,
        "total_usd": total if missing == 0 else None,
        "per_1000_requests_usd": total / len(records) * 1000 if records and missing == 0 else None,
    }


def summarize_group(records: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [r for r in records if valid_prediction(r)]
    kinds = sorted({r.get("target_kind", "unknown") for r in records})
    if len(kinds) != 1:
        raise ValueError(f"An experiment/model group must have exactly one target kind, got {kinds}")
    kind = kinds[0]
    losses = [loss_values(r) for r in valid]
    keys = ("accuracy", "brier", "log_loss") if kind == "label" else ("mae", "excess_brier", "expected_brier")
    quality = {key: mean([loss[key] for loss in losses]) for key in keys}
    if kind == "label":
        correct = sum(loss["accuracy"] for loss in losses)
        quality.update(correct_count=int(correct), accuracy_all_requests=correct / len(records) if records else None)
    else:
        quality["rmse"] = math.sqrt(quality["excess_brier"]) if quality["excess_brier"] is not None else None
    costs = cost_summary(records)
    costs["per_correct_answer_usd"] = (
        costs["total_usd"] / quality["correct_count"]
        if kind == "label" and costs["complete"] and quality["correct_count"] > 0 else None
    )
    return {
        "requests": len(records),
        "unique_cases": len({r["case_id"] for r in records}),
        "valid_count": len(valid),
        "invalid_count": len(records) - len(valid),
        "success_rate": len(valid) / len(records) if records else None,
        "target_kind": kind,
        "quality": quality,
        "valid_response_latency_s": latency_summary(valid),
        "all_request_latency_s": latency_summary(records),
        "cost": costs,
        "response_models": sorted({str(r["response_model"]) for r in records if r.get("response_model")}),
        "providers": sorted({str(r["provider"]) for r in records if r.get("provider")}),
    }


def paired_comparison(records: list[dict[str, Any]], samples: int, seed: int) -> dict[str, Any]:
    by_case: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for record in records:
        by_case[record["case_id"]][record["model_label"]].append(record)
    pairs = []
    mismatched_cases = []
    for case_id in sorted(by_case):
        models = by_case[case_id]
        accepted = {model: [r for r in models[model] if valid_prediction(r)] for model in MODELS}
        if not all(accepted.values()):
            continue
        targets = {(r["target_kind"], r["target"]) for rs in accepted.values() for r in rs}
        if len(targets) != 1:
            mismatched_cases.append(case_id)
            continue
        pair: dict[str, Any] = {"case_id": case_id, "kind": next(iter(targets))[0]}
        for model in MODELS:
            losses = [loss_values(r) for r in accepted[model]]
            pair[model] = {key: statistics.fmean(loss[key] for loss in losses) for key in losses[0]}
            pair[model]["latency"] = latency_summary(accepted[model])["p50"]
        pairs.append(pair)
    result: dict[str, Any] = {
        "difference_direction": "Luna minus Jev",
        "latency_ratio_direction": "Luna divided by Jev",
        "unit": "case; valid repeats averaged within case, latency repeats reduced to median",
        "total_cases": len(by_case),
        "paired_cases": len(pairs),
        "excluded_cases": len(by_case) - len(pairs),
        "mismatched_target_case_ids": mismatched_cases,
        "bootstrap_samples": samples,
        "bootstrap_seed": seed,
        "confidence_level": 0.95,
        "quality_differences": {},
        "latency_median_ratio": None,
    }
    if not pairs:
        return result
    kinds = {p["kind"] for p in pairs}
    if len(kinds) != 1:
        raise ValueError("An experiment cannot mix label and probability targets")
    metrics = ("accuracy", "brier", "log_loss") if pairs[0]["kind"] == "label" else ("mae", "excess_brier", "expected_brier")
    rng = random.Random(seed)
    draws: dict[str, list[float]] = {key: [] for key in metrics}
    latency_pairs = [p for p in pairs if p["Jev"]["latency"] is not None and p["Luna"]["latency"] is not None]
    latency_draws = []

    def ratio(batch: list[dict[str, Any]]) -> float | None:
        if not batch:
            return None
        denominator = statistics.median(p["Jev"]["latency"] for p in batch)
        return statistics.median(p["Luna"]["latency"] for p in batch) / denominator if denominator > 0 else None

    if len(pairs) >= 2:
        for _ in range(samples):
            batch = [pairs[rng.randrange(len(pairs))] for _ in pairs]
            for key in metrics:
                draws[key].append(statistics.fmean(p["Luna"][key] - p["Jev"][key] for p in batch))
    for key in metrics:
        result["quality_differences"][key] = {
            "estimate": statistics.fmean(p["Luna"][key] - p["Jev"][key] for p in pairs),
            "ci95": [percentile(draws[key], 0.025), percentile(draws[key], 0.975)] if draws[key] else None,
            "favours_luna_when": "positive" if key == "accuracy" else "negative",
        }
    if len(latency_pairs) >= 2:
        for _ in range(samples):
            estimate = ratio([latency_pairs[rng.randrange(len(latency_pairs))] for _ in latency_pairs])
            if estimate is not None:
                latency_draws.append(estimate)
    result["latency_median_ratio"] = {
        "estimate": ratio(latency_pairs),
        "ci95": [percentile(latency_draws, 0.025), percentile(latency_draws, 0.975)] if latency_draws else None,
        "paired_cases": len(latency_pairs),
        "defined_bootstrap_samples": len(latency_draws),
        "favours_luna_when": "less than 1",
    }
    return result


def summarize_records(records: list[dict[str, Any]], metadata: dict[str, Any] | None = None,
                      bootstrap_samples: int = 2000, seed: int = 1729) -> dict[str, Any]:
    if bootstrap_samples < 0:
        raise ValueError("bootstrap_samples must be nonnegative")
    for index, record in enumerate(records, 1):
        for key in ("case_id", "experiment", "model_label", "phase"):
            if key not in record:
                raise ValueError(f"Record {index} is missing {key}")
        if record["phase"] not in ("measured", "warmup"):
            raise ValueError(f"Record {index} has unknown phase: {record['phase']!r}")
        if record["model_label"] not in MODELS:
            raise ValueError(f"Record {index} has unsupported model label: {record['model_label']!r}")
    measured = [r for r in records if r["phase"] == "measured"]
    warmup = [r for r in records if r["phase"] == "warmup"]
    experiments: dict[str, Any] = {}
    for name in sorted({r["experiment"] for r in measured}):
        selected = [r for r in measured if r["experiment"] == name]
        target_kinds = {r.get("target_kind") for r in selected}
        if len(target_kinds) != 1 or not target_kinds <= {"label", "probability"}:
            raise ValueError(f"Experiment {name!r} has invalid or mixed target kinds")
        experiment_seed = seed + int.from_bytes(hashlib.sha256(name.encode()).digest()[:4], "big")
        experiments[name] = {
            "target_kind": next(iter(target_kinds)),
            "models": {model: summarize_group([r for r in selected if r["model_label"] == model])
                       for model in MODELS if any(r["model_label"] == model for r in selected)},
            "paired": paired_comparison(selected, bootstrap_samples, experiment_seed),
        }
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "metadata": metadata or {},
        "totals": {
            "measured_requests": len(measured),
            "measured_valid": sum(valid_prediction(r) for r in measured),
            "warmup_requests": len(warmup),
            "all_cost": cost_summary(records),
            "measured_cost": cost_summary(measured),
            "warmup_cost": cost_summary(warmup),
        },
        "experiments": experiments,
        "definitions": {
            "success": "valid=true with a finite probability and valid target in [0,1]; label targets must be 0 or 1",
            "accuracy": "Fraction correct among valid responses, predicting yes when p_yes >= 0.5",
            "accuracy_all_requests": "Correct valid responses divided by every measured request; invalid requests count as failures",
            "brier": "Mean (p_yes - observed_label)^2; lower is better",
            "log_loss": "Mean binary negative log likelihood in natural-log units; p clipped to [1e-15, 1-1e-15]",
            "mae": "Mean absolute difference between reported p_yes and exact reference probability",
            "rmse": "Square root of mean squared error against exact reference probabilities",
            "excess_brier": "Mean (p_yes - true_probability)^2: expected Brier above the optimal probability forecast",
            "expected_brier": "Mean [(p_yes - true_probability)^2 + true_probability*(1-true_probability)]",
            "latency": "End-to-end request time through receipt of a complete response; main table includes valid responses only",
            "cost": "Recorded billed USD across all requests in the stated phase, including failed/invalid requests when billed",
            "cost_per_1000": "Measured total billed cost / measured request count * 1000; withheld if any bill is missing",
            "cost_per_correct": "All measured billed cost / number of correct valid label responses; withheld for missing bills or zero correct",
            "bootstrap": "Percentile 95% paired bootstrap by case identity; quality repeats averaged within case; latency repeats use median; no CI for fewer than 2 pairs",
        },
        "caveats": [
            "This is a small benchmark. Intervals describe variation across the sampled cases; they do not prove a general model ranking.",
            "Luna supplies generated numerical probabilities; Jev supplies its native Noul probabilities. Neither adapter extracts next-token probabilities. Brier combines calibration and discrimination; this small sample cannot establish general calibration.",
            "Each task has a separate quality scale. Quality is not pooled across tasks; no overall winner is inferred.",
            "BoolQ is public and may have appeared in training data. Reading-comprehension results can be affected by contamination.",
            "Paired comparisons include only cases with valid responses from both models. Check success rates and excluded pairs before comparing conditional quality.",
            "Latency includes the OpenRouter/provider/network path. Provider routing, caching, load, reasoning settings, and output length can change speed and price.",
            "The intended primary protocol is Luna reasoning=none, sequential randomized interleaving, and no retries. The recorded run metadata is the source of truth for whether that protocol was used.",
            "95% intervals are exploratory percentile bootstrap intervals without multiple-comparison correction. Zero-width intervals can occur in a small or uniform sample.",
            "Costs are USD as reported by the runner. Missing billing remains unknown; cost per 1,000 is a sample-based projection, not a price guarantee.",
        ],
    }


def number(value: Any, digits: int = 3) -> str:
    return f"{value:,.{digits}f}" if finite_number(value) else "—"


def percentage(value: Any) -> str:
    return f"{value * 100:.1f}%" if finite_number(value) else "—"


def money(value: Any) -> str:
    return f"${value:.6f}" if finite_number(value) else "—"


def interval(metric: dict[str, Any] | None, percent: bool = False, ratio: bool = False) -> str:
    if not metric or metric.get("estimate") is None:
        return "unavailable"
    multiplier = 100 if percent else 1
    suffix = " pp" if percent else "×" if ratio else ""
    estimate = metric["estimate"] * multiplier
    text = f"{estimate:+.2f}{suffix}" if not ratio else f"{estimate:.2f}{suffix}"
    bounds = metric.get("ci95")
    if bounds and all(finite_number(value) for value in bounds):
        text += f" [{bounds[0] * multiplier:.2f}, {bounds[1] * multiplier:.2f}]"
    else:
        text += " [CI unavailable]"
    return text


def markdown_report(summary: dict[str, Any]) -> str:
    totals = summary["totals"]
    lines = ["# Jev / Luna benchmark", "", "Speed, cost, and validated quality, with identical response schemas.", "",
             f"Measured requests: **{totals['measured_requests']}** · Valid responses: **{totals['measured_valid']}** · Warmups: **{totals['warmup_requests']}**.", "",
             f"Known billed cost including warmups: **{money(totals['all_cost']['known_total_usd'])}**. Missing bills: **{totals['all_cost']['missing_count']}**.", ""]
    if not summary["experiments"]:
        lines += ["No measured results are available.", ""]
    for name, experiment in summary["experiments"].items():
        kind = experiment["target_kind"]
        label = kind == "label"
        lines += [f"## {TITLES.get(name, name)}", "",
                  "| Model | Valid / requests | Accuracy (valid) | Accuracy (all) | Brier | Log loss | p50 / p95 seconds | USD / 1,000 requests | USD / correct |"
                  if label else "| Model | Valid / requests | MAE | RMSE | Excess Brier | Expected Brier | p50 / p95 seconds | USD / 1,000 requests |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|" if label else "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for model, metrics in experiment["models"].items():
            q, costs, timing = metrics["quality"], metrics["cost"], metrics["valid_response_latency_s"]
            row = [model, f"{metrics['valid_count']} / {metrics['requests']}"]
            row += [percentage(q["accuracy"]), percentage(q["accuracy_all_requests"]), number(q["brier"]), number(q["log_loss"])] if label else [number(q["mae"]), number(q["rmse"]), number(q["excess_brier"]), number(q["expected_brier"])]
            row += [f"{number(timing['p50'])} / {number(timing['p95'])}", money(costs["per_1000_requests_usd"])]
            if label:
                row += [money(costs["per_correct_answer_usd"])]
            lines.append("| " + " | ".join(row) + " |")
        paired = experiment["paired"]
        primary = "accuracy" if label else "mae"
        lines += ["", f"Paired cases: **{paired['paired_cases']} / {paired['total_cases']}**. Luna − Jev {primary}: **{interval(paired['quality_differences'].get(primary), percent=label)}** (95% CI).",
                  f"Luna / Jev ratio of median latency: **{interval(paired['latency_median_ratio'], ratio=True)}** (95% CI). Below 1 means Luna is faster.", ""]
        for model, metrics in experiment["models"].items():
            lines += [f"- {model}: known measured billing {money(metrics['cost']['known_total_usd'])}; {metrics['cost']['missing_count']} missing bills. Resolved models: {', '.join(metrics['response_models']) or 'unreported'}. Providers: {', '.join(metrics['providers']) or 'unreported'}."]
        lines.append("")
    lines += ["## Interpretation and limits", ""] + [f"- {text}" for text in summary["caveats"]]
    lines += ["", "## Metric definitions", ""] + [f"- **{key}**: {text}" for key, text in summary["definitions"].items()]
    lines += ["", "## Run metadata", "", "```json", json.dumps(summary["metadata"], indent=2, ensure_ascii=False), "```", ""]
    return "\n".join(lines)


def html_report(summary: dict[str, Any], records: list[dict[str, Any]]) -> str:
    esc = lambda value: html.escape(str(value), quote=True)
    totals = summary["totals"]
    sections = []
    for name, experiment in summary["experiments"].items():
        label = experiment["target_kind"] == "label"
        headers = ["Model", "Valid / requests", "Accuracy · valid", "Accuracy · all", "Brier", "Log loss", "p50 / p95", "USD / 1,000", "USD / correct"] if label else ["Model", "Valid / requests", "MAE", "RMSE", "Excess Brier", "Expected Brier", "p50 / p95", "USD / 1,000"]
        rows = []
        bars = []
        cost_notes = []
        available_latencies = [metrics["valid_response_latency_s"]["p50"] for metrics in experiment["models"].values() if metrics["valid_response_latency_s"]["p50"] is not None]
        max_latency = max(available_latencies, default=1) or 1
        for model, metrics in experiment["models"].items():
            q, cost, timing = metrics["quality"], metrics["cost"], metrics["valid_response_latency_s"]
            cells = [f'<strong class="model {model.lower()}">{esc(model)}</strong>', f"{metrics['valid_count']} / {metrics['requests']}"]
            cells += [percentage(q["accuracy"]), percentage(q["accuracy_all_requests"]), number(q["brier"]), number(q["log_loss"])] if label else [number(q["mae"]), number(q["rmse"]), number(q["excess_brier"]), number(q["expected_brier"])]
            cells += [f"{number(timing['p50'])} / {number(timing['p95'])} s", money(cost["per_1000_requests_usd"])]
            if label:
                cells.append(money(cost["per_correct_answer_usd"]))
            rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
            if timing["p50"] is not None:
                bars.append(f'<div class="bar-row"><span>{esc(model)}</span><div class="bar-track"><div class="bar {model.lower()}" style="width:{timing["p50"] / max_latency * 100:.2f}%"></div></div><b>{number(timing["p50"])} s</b></div>')
            cost_notes.append(f'{esc(model)}: {money(cost["known_total_usd"])} known measured billing · {cost["missing_count"]} missing bills · resolved model {esc(", ".join(metrics["response_models"]) or "unreported")} · provider {esc(", ".join(metrics["providers"]) or "unreported")}')
        paired = experiment["paired"]
        primary = "accuracy" if label else "mae"
        quality = interval(paired["quality_differences"].get(primary), percent=label)
        speed = interval(paired["latency_median_ratio"], ratio=True)
        sections.append(f'''<section class="experiment">
<div class="section-head"><div><span class="eyebrow">{'Observed labels' if label else 'Known mathematical probabilities'}</span><h2>{esc(TITLES.get(name, name))}</h2></div><span class="pill">{paired['paired_cases']} paired cases</span></div>
<p class="muted">{'Accuracy: higher is better. Brier and log loss: lower is better.' if label else 'MAE, RMSE, and excess Brier: lower is better. Expected Brier includes irreducible outcome uncertainty.'}</p>
<div class="table-wrap"><table><thead><tr>{''.join(f'<th>{esc(header)}</th>' for header in headers)}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<div class="comparison-grid"><div><h3>Median complete-response time</h3>{''.join(bars) or '<p>No valid latency data.</p>'}<p class="small">Valid responses only. Smaller is faster.</p></div><div class="paired"><h3>Paired comparison · 95% intervals</h3><p><span>Luna − Jev {esc(primary)}</span><strong>{esc(quality)}</strong></p><p><span>Luna / Jev median time</span><strong>{esc(speed)}</strong></p><small>{paired['paired_cases']} of {paired['total_cases']} cases paired; {paired['excluded_cases']} excluded. {'Positive accuracy difference favours Luna.' if label else 'Negative MAE difference favours Luna.'} A time ratio below 1 favours Luna.</small></div></div>
<details><summary>Billing and routing detail</summary><ul>{''.join(f'<li>{note}</li>' for note in cost_notes)}</ul><p class="small">All-request timing, additional paired metrics, interval seeds, and exact unrounded numbers are in summary.json. Billing includes invalid measured requests; warmups are shown separately above.</p></details></section>''')
    raw_rows = []
    for record in records:
        ok = valid_prediction(record)
        cells = [record["case_id"], record["experiment"], record["model_label"], record["phase"],
                 number(record.get("target"), 4), number(record.get("probability"), 4),
                 "Valid" if ok else "Invalid", number(record.get("latency_s")), money(record.get("cost_usd")),
                 record.get("error") or ""]
        raw_rows.append(f'<tr data-model="{esc(record["model_label"])}" data-task="{esc(record["experiment"])}" data-phase="{esc(record["phase"])}" data-valid="{str(ok).lower()}">' + "".join(f"<td>{esc(cell)}</td>" for cell in cells) + "</tr>")
    task_options = "".join(f'<option value="{esc(name)}">{esc(TITLES.get(name, name))}</option>' for name in sorted({r["experiment"] for r in records}))
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="light"><title>Jev / Luna · benchmark report</title>
<style>
:root{{--ink:#192438;--muted:#647084;--line:#e0e5ec;--paper:#f4f6f9;--jev:#7d65cf;--luna:#d7752b}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:14px;line-height:1.6}}main{{max-width:1320px;margin:auto;padding:48px 36px 64px}}header{{margin-bottom:28px}}h1{{font-size:clamp(36px,5vw,62px);letter-spacing:-.06em;line-height:1.08;margin:13px 0 18px;font-weight:650}}h2{{font-size:25px;letter-spacing:-.035em;margin:4px 0 0}}h3{{font-size:13px;margin:0 0 20px;font-weight:650}}p{{margin:8px 0 16px}}.eyebrow{{font-size:11px;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);font-weight:650}}.lede{{max-width:670px;font-size:16px;color:var(--muted)}}.topline,.section-head{{display:flex;justify-content:space-between;align-items:center;gap:24px}}.tag,.pill{{border:1px solid var(--line);padding:5px 11px;border-radius:999px;font-size:12px;white-space:nowrap}}.tag{{background:white}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:26px 0 14px}}.card{{padding:22px 24px;background:white;border:1px solid var(--line);border-radius:14px}}.card strong{{display:block;font-size:29px;letter-spacing:-.05em;line-height:1.3;margin:6px 0}}.card span,.small,small{{color:var(--muted);font-size:12px}}.muted{{color:var(--muted)}}.note{{font-size:12px;color:var(--muted);margin-bottom:28px}}.experiment,.panel{{background:white;padding:28px 30px;border:1px solid var(--line);border-radius:16px;margin-top:20px}}.table-wrap{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:12px}}th{{color:var(--muted);font-size:11px;font-weight:550;text-align:right;white-space:nowrap;padding:14px 10px;border-bottom:1px solid var(--line)}}td{{padding:17px 10px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}}th:first-child,td:first-child{{text-align:left;padding-left:0}}.model{{display:inline-flex;align-items:center;gap:9px;font-size:14px}}.model::before{{content:"";width:8px;height:8px;border-radius:50%;background:currentColor}}.jev{{color:var(--jev)}}.luna{{color:var(--luna)}}.comparison-grid{{display:grid;grid-template-columns:1fr 1fr;gap:50px;margin:26px 0 20px}}.bar-row{{display:grid;grid-template-columns:44px 1fr 66px;gap:12px;align-items:center;margin:13px 0;font-size:12px}}.bar-row b{{font-weight:500;text-align:right}}.bar-track{{height:14px;background:#f0f2f6;border-radius:3px;overflow:hidden}}.bar{{height:100%;min-width:1px}}.bar.jev{{background:var(--jev)}}.bar.luna{{background:var(--luna)}}.paired p{{display:flex;justify-content:space-between;gap:15px;font-size:12px;margin:8px 0}}.paired strong{{font-variant-numeric:tabular-nums;white-space:nowrap}}details{{border-top:1px solid var(--line);padding-top:15px;font-size:12px;color:var(--muted)}}summary{{cursor:pointer;font-weight:600}}li{{margin:9px 0}}.panel h2{{margin-bottom:18px}}.limits{{columns:2;column-gap:45px;padding-left:20px}}.limits li{{break-inside:avoid;margin:0 0 13px;padding-left:3px}}.filters{{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}}label{{font-size:12px;color:var(--muted)}}select{{display:block;margin-top:4px;padding:8px 12px;border:1px solid var(--line);border-radius:7px;color:var(--ink);background:white;min-width:120px}}.raw{{max-height:500px;overflow:auto}}.raw thead{{position:sticky;top:0;background:white}}.raw td{{padding:10px;font-size:11px;text-align:left}}.raw th{{text-align:left}}.raw td:last-child{{white-space:normal;min-width:100px;max-width:280px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--paper);padding:18px;border-radius:10px;font-size:11px;color:var(--muted)}}.definition{{font-size:12px}}.definition dt{{font-weight:650;margin-top:12px}}.definition dd{{margin:2px 0 0;color:var(--muted)}}footer{{font-size:11px;color:var(--muted);margin-top:26px}}a{{color:var(--ink)}}[hidden]{{display:none!important}}@media(max-width:800px){{main{{padding:26px 16px}}.cards{{grid-template-columns:repeat(2,1fr)}}.card{{padding:16px}}.experiment,.panel{{padding:22px 18px}}.comparison-grid{{grid-template-columns:1fr;gap:20px}}.limits{{columns:1}}.section-head{{align-items:flex-start}}.topline{{gap:10px}}}}@media print{{body{{background:white}}main{{padding:0;max-width:none}}.experiment,.panel,.card{{break-inside:avoid}}.filters,.raw-panel{{display:none}}details{{display:block}}h1{{font-size:40px}}}}
</style></head><body><main>
<header><div class="topline"><span class="eyebrow">OpenRouter · controlled comparison</span><span class="tag">Exploratory benchmark</span></div><h1>Jev <span style="color:#a7afbc;font-weight:350">/</span> Luna</h1><p class="lede">How much quality, how quickly, at what cost. Identical output schemas; separately scored tasks with checkable answers.</p></header>
<div class="cards"><div class="card"><span>Measured requests</span><strong>{totals['measured_requests']}</strong><span>{len(summary['experiments'])} distinct experiments</span></div><div class="card"><span>Valid responses</span><strong>{totals['measured_valid']}</strong><span>{percentage(totals['measured_valid'] / totals['measured_requests'] if totals['measured_requests'] else None)} schema success</span></div><div class="card"><span>Known total billing</span><strong style="font-size:25px">{money(totals['all_cost']['known_total_usd'])}</strong><span>{totals['all_cost']['missing_count']} missing bills · includes warmups</span></div><div class="card"><span>Warmup requests</span><strong>{totals['warmup_requests']}</strong><span>{money(totals['warmup_cost']['known_total_usd'])} known billing · excluded from scores</span></div></div>
<p class="note">Quality and response-time tables use valid measured responses. “Accuracy · all” also counts invalid requests as failures. Cost includes all measured requests. A dash means unavailable; unknown cost is never assumed to be zero.</p>
{''.join(sections) or '<section class="panel"><h2>No measured results yet</h2><p>The runner has not supplied any measured requests.</p></section>'}
<section class="panel"><h2>Read the result in context</h2><ul class="limits">{''.join(f'<li>{esc(text)}</li>' for text in summary['caveats'])}</ul></section>
<section class="panel raw-panel"><h2>Every request</h2><p class="muted">Inspect the record behind each aggregate. Includes warmups and failed requests.</p><div class="filters"><label>Model<select id="model-filter"><option value="">All models</option><option>Jev</option><option>Luna</option></select></label><label>Experiment<select id="task-filter"><option value="">All experiments</option>{task_options}</select></label><label>Phase<select id="phase-filter"><option value="">All phases</option><option value="measured">Measured</option><option value="warmup">Warmup</option></select></label><label>Result<select id="valid-filter"><option value="">All results</option><option value="true">Valid</option><option value="false">Invalid</option></select></label></div><p class="small" id="row-count">{len(records)} requests shown</p><div class="table-wrap raw"><table><thead><tr>{''.join(f'<th>{heading}</th>' for heading in ['Case', 'Experiment', 'Model', 'Phase', 'Target', 'p(yes)', 'Result', 'Seconds', 'USD', 'Error'])}</tr></thead><tbody id="requests">{''.join(raw_rows)}</tbody></table></div></section>
<section class="panel"><details><summary>Metric definitions and statistical method</summary><dl class="definition">{''.join(f'<dt>{esc(key)}</dt><dd>{esc(value)}</dd>' for key, value in summary['definitions'].items())}</dl></details><details style="margin-top:16px"><summary>Recorded run metadata</summary><pre>{esc(json.dumps(summary['metadata'], indent=2, ensure_ascii=False))}</pre></details></section>
<footer>Generated {esc(summary['generated_at_utc'])} · <a href="summary.json">Machine-readable summary</a> · <a href="report.md">Markdown report</a> · Self-contained HTML; no external assets or tracking.</footer>
</main><script>
const filters = ['model','task','phase','valid'];
function applyFilters() {{ let shown = 0; document.querySelectorAll('#requests tr').forEach(row => {{ row.hidden = !filters.every(key => {{ const value = document.getElementById(key + '-filter').value; return !value || row.dataset[key] === value; }}); if (!row.hidden) shown++; }}); document.getElementById('row-count').textContent = shown + ' requests shown'; }}
filters.forEach(key => document.getElementById(key + '-filter').addEventListener('change', applyFilters));
</script></body></html>'''


def read_records(path: str | Path) -> list[dict[str, Any]]:
    records = []
    with Path(path).open(encoding="utf-8") as source:
        for number_, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {number_}: {exc.msg}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"Line {number_} is not an object")
            records.append(record)
    return records


def generate_report(records_path: str | Path, output_dir: str | Path,
                    metadata: dict[str, Any] | None = None, bootstrap_samples: int = 2000,
                    seed: int = 1729) -> dict[str, Any]:
    records = read_records(records_path)
    summary = summarize_records(records, metadata, bootstrap_samples, seed)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (destination / "report.md").write_text(markdown_report(summary), encoding="utf-8")
    (destination / "report.html").write_text(html_report(summary, records), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", type=Path, help="Runner JSONL file")
    parser.add_argument("--output-dir", type=Path, default=Path("report"))
    parser.add_argument("--metadata", type=Path, help="Run metadata JSON")
    parser.add_argument("--bootstrap-samples", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=1729)
    args = parser.parse_args()
    metadata = json.loads(args.metadata.read_text(encoding="utf-8")) if args.metadata else None
    summary = generate_report(args.records, args.output_dir, metadata, args.bootstrap_samples, args.seed)
    print(json.dumps({"report": str((args.output_dir / "report.html").resolve()), "measured_requests": summary["totals"]["measured_requests"], "known_total_cost_usd": summary["totals"]["all_cost"]["known_total_usd"]}))


if __name__ == "__main__":
    main()
