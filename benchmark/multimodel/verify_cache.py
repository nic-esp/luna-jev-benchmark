#!/usr/bin/env python3
"""Independent offline verification of the generalized cache experiment.

Rebuilds requests with the frozen cases, model specs, seed and saved nonces.
Reads cache and billing evidence directly from saved API responses. No client
initialization, credential access, HTTP calls, or inference calls are performed.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import adapter
import cache_protocol as protocol
import verify as generic


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def same_number(left, right):
    return finite(left) and finite(right) and math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-12)


def response_counts(row):
    """Use the response envelope; do not trust the runner's copied counters."""
    details = ((row.get("response") or {}).get("usage") or {}).get("prompt_tokens_details") or {}
    return {k: details.get(k) if integer(details.get(k)) else None for k in ("cached_tokens", "cache_write_tokens")}


def no_duplicate_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate answer field")
        result[key] = value
    return result


def select_protocol(metadata):
    """Allow only the two local versioned implementations, never arbitrary paths."""
    name = metadata.get("protocol_file", "cache_protocol.py")
    versions = {"cache_protocol.py": 1, "cache_protocol_v2.py": 2}
    if name not in versions or metadata.get("protocol_version", 1) != versions[name]:
        raise ValueError("Unsupported cache protocol file or version")
    path = ROOT / name
    if name == "cache_protocol.py":
        return protocol, path
    spec = importlib.util.spec_from_file_location("verified_cache_protocol_v2", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if metadata.get("protocol_amendment") != module.PROTOCOL_AMENDMENT:
        raise ValueError("Version 2 administrative amendment metadata differs")
    if sha(ROOT / "cache_protocol.py") != module.ORIGINAL_PROTOCOL_SHA256:
        raise ValueError("Original cache protocol source changed")
    return module, path


def raw_cache_verification(rows, specs):
    result = {}
    for spec in specs:
        label = spec["label"]
        measured = [r for r in rows if r.get("phase") == "measured" and r.get("base_model_label") == label]
        if spec["cache_control"] != "explicit":
            counts = [response_counts(r) for r in measured]
            result[label] = {"status": "unsupported", "cache_state": "uncontrolled", "calls": len(measured),
                             "observed_read_counters": sum(c["cached_tokens"] is not None for c in counts),
                             "positive_read_calls": sum(c["cached_tokens"] is not None and c["cached_tokens"] > 0 for c in counts),
                             "observed_write_counters": sum(c["cache_write_tokens"] is not None for c in counts)}
            continue
        blocks = []
        for block in range(4):
            primes = [r for r in rows if r.get("phase") == "cache_prime" and r.get("block") == block and r.get("base_model_label") == label]
            on = [r for r in measured if r.get("block") == block and r.get("cache_mode") == "cached"]
            off = [r for r in measured if r.get("block") == block and r.get("cache_mode") == "uncached"]
            prime = primes[0] if len(primes) == 1 else None
            pc = response_counts(prime) if prime else {"cached_tokens": None, "cache_write_tokens": None}
            on_counts, off_counts = [response_counts(r) for r in on], [response_counts(r) for r in off]
            checks = {
                "one_valid_cold_prime": bool(prime and prime.get("valid") is True and pc["cached_tokens"] == 0 and pc["cache_write_tokens"] is not None and pc["cache_write_tokens"] > 0),
                "all_24_cached_reads_observed_positive": len(on) == 24 and all(c["cached_tokens"] is not None and c["cached_tokens"] > 0 for c in on_counts),
                "all_24_cached_writes_observed_zero": len(on) == 24 and all(c["cache_write_tokens"] == 0 for c in on_counts),
                "all_24_uncached_reads_and_writes_observed_zero": len(off) == 24 and all(c == {"cached_tokens": 0, "cache_write_tokens": 0} for c in off_counts),
                "stable_prefix_read_size_matches_prime_write": len(on) == 24 and pc["cache_write_tokens"] is not None and pc["cache_write_tokens"] > 0 and all(c["cached_tokens"] == pc["cache_write_tokens"] for c in on_counts),
            }
            blocks.append({"block": block, "checks": checks, "passed": all(checks.values()),
                           "prime_write_tokens": pc["cache_write_tokens"],
                           "read_sizes": sorted({c["cached_tokens"] for c in on_counts if c["cached_tokens"] is not None})})
        result[label] = {"status": "verified" if all(b["passed"] for b in blocks) else "failed", "blocks": blocks}
    return result


def _verify(run):
    meta = json.loads((run / "metadata.json").read_text())
    protocol, protocol_path = select_protocol(meta)
    saved = json.loads((run / "plan.json").read_text())
    rows = read_rows(run / "responses.jsonl")
    journal = read_rows(run / "dispatches.jsonl")
    cases, primes, rulebook, source_hashes = protocol.load_source(meta["source_dir"])
    specs = protocol.validate_specs(meta["models"])
    # Only pure build_request/reserve_usd methods are used; no key or connection
    # exists on this object. Any attempt to call its HTTP run method would fail.
    planner = adapter.Adapter.__new__(adapter.Adapter)
    rebuilt, prepared = protocol.create_plan(planner, specs, cases, primes, source_hashes,
                                            meta["seed"], saved["block_identifiers"])
    entries = rebuilt["attempts"]
    arm_modes = {arm: mode for spec in specs for arm, mode in protocol.model_arms(spec)}
    controlled = [s["label"] for s in specs if s["cache_control"] == "explicit"]
    expected_measured = {(c["id"], arm, c["block"]) for c in cases for arm in arm_modes}
    actual_measured = [(r.get("case_id"), r.get("model_label"), r.get("block")) for r in rows if r.get("phase") == "measured"]
    expected_primes = {(f"cache-prime-{block}", label + " cached", block) for block in range(4) for label in controlled}
    actual_primes = [(r.get("case_id"), r.get("model_label"), r.get("block")) for r in rows if r.get("phase") == "cache_prime"]
    old_identifiers = set(json.loads((Path(meta["source_dir"]) / "metadata.json").read_text()).get("block_identifiers", []))
    checks = {
        "completed_run": meta.get("status") == "complete",
        "frozen_96_cases_bytes": sha(run / "cases.jsonl") == protocol.SOURCE_CASES_SHA256,
        "frozen_rulebook_bytes": sha(run / "rulebook.txt") == protocol.SOURCE_RULEBOOK_SHA256,
        "reference_source_hashes": meta.get("source_hashes") == source_hashes,
        "protocol_code_hash": meta.get("protocol_sha256") == sha(protocol_path),
        "saved_plan_hash": meta.get("plan_sha256") == sha(run / "plan.json"),
        "seeded_plan_and_all_requests_reproduce": saved == rebuilt,
        "four_distinct_new_prefixes": len(saved["block_identifiers"]) == 4 and len(set(saved["block_identifiers"])) == 4 and not (set(saved["block_identifiers"]) & old_identifiers),
        "complete_measured_case_arm_cross_product": len(actual_measured) == len(expected_measured) and set(actual_measured) == expected_measured,
        "one_prime_per_controlled_model_per_block": len(actual_primes) == len(expected_primes) and set(actual_primes) == expected_primes,
        "all_attempts_count": len(rows) == len(entries) == meta.get("calls_planned") == meta.get("calls_completed"),
        "ordered_sequences": [r.get("sequence") for r in rows] == list(range(len(entries))),
        "dispatch_count_and_order": len(journal) == len(rows) and [d.get("sequence") for d in journal] == list(range(len(entries))),
        "no_unresolved_dispatches": meta.get("unresolved_dispatch_n", 0) == 0,
        "no_automatic_retries": meta.get("automatic_retries") == 0,
        "all_four_blocks_completed": meta.get("completed_blocks") == [0, 1, 2, 3],
    }
    if (run / "primes.json").exists():
        checks["saved_prime_case_definitions"] = json.loads((run / "primes.json").read_text()) == {str(k): v for k, v in primes.items()}
    issue_rows, usage_observations = [], defaultdict(list)
    bills, missing_bills = [], 0
    for index, row in enumerate(rows):
        if index >= len(entries):
            issue_rows.append({"sequence": index, "issues": ["unplanned_extra_response"]})
            continue
        entry = entries[index]
        case, spec, request = prepared[index]
        # The runner intentionally logs all cache tasks under this neutral alias;
        # the frozen source identity and nonce-bearing supplied state stay exact.
        expected_case = {**case, "experiment": "cache_policy"}
        problems, observations = generic.check_record(row, expected_case, meta,
            payload_factory=lambda c, label, r, m: (request["endpoint"], request["payload"]))
        for field in ("case_id", "base_model_label", "model_label", "phase", "sequence", "block", "pair_id",
                      "target", "cache_mode", "cache_key", "prefix_identifier", "prefix_sha256", "request_sha256", "reserve_usd"):
            if row.get(field) != entry[field]:
                problems.append("plan_field_mismatch:" + field)
        actual_hash = protocol.digest(protocol.canonical({"endpoint": row.get("endpoint"), "payload": row.get("request")}).encode())
        if actual_hash != entry["request_sha256"]:
            problems.append("actual_request_hash")
        if index >= len(journal) or any(journal[index].get(k) != entry[k] for k in ("sequence", "request_sha256", "case_id", "model_label")):
            problems.append("durable_dispatch_identity_and_hash")
        response = row.get("response") or {}
        usage = response.get("usage") or {}
        if row.get("usage", {}) != usage:
            problems.append("all_response_usage_readback")
        if row.get("response_model") != response.get("model") or row.get("provider") != response.get("provider"):
            problems.append("all_response_model_provider_readback")
        raw_cost = usage.get("cost")
        cost = raw_cost if finite(raw_cost) and raw_cost >= 0 else None
        if row.get("cost_usd") != cost:
            problems.append("all_response_cost_readback")
        if cost is None:
            missing_bills += 1
        else:
            bills.append(cost)
        counts = response_counts(row)
        for field in counts:
            if row.get(field) != counts[field]:
                problems.append("copied_cache_counter:" + field)
        if spec["label"] != "Jev":
            if spec.get("reasoning") != {"effort": "none"}:
                problems.append("strict_zero_reasoning_not_requested")
            reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
            if reasoning is not None and (not integer(reasoning) or reasoning != 0):
                problems.append("observed_reasoning_tokens_nonzero_or_invalid")
            output = usage.get("completion_tokens")
            if output is not None and (not integer(output) or output > 128):
                problems.append("reported_completion_tokens_outside_cap")
            messages = [choice.get("message") or {} for choice in response.get("choices", [])]
            visible = any(m.get("reasoning") or m.get("reasoning_details") for m in messages)
            if visible:
                problems.append("visible_reasoning_present")
            if row.get("valid") is True:
                if row.get("reported_reasoning_tokens") != reasoning or row.get("visible_reasoning") is not bool(visible):
                    problems.append("copied_reasoning_evidence_readback")
                if row.get("reasoning_verified_disabled") is not (reasoning == 0 and not visible):
                    problems.append("reasoning_disabled_flag_readback")
                try:
                    json.loads(response["choices"][0]["message"]["content"], object_pairs_hook=no_duplicate_object)
                except (ValueError, TypeError, KeyError, IndexError):
                    problems.append("answer_contains_duplicate_fields_or_unparseable_json")
            observations.update(reasoning_tokens=reasoning, output_tokens=output, generated_reasoning_present=bool(visible))
        if row.get("valid") is not True and row.get("probability") is not None:
            problems.append("invalid_probability_must_remain_unknown")
        usage_observations[entry["model_label"]].append(observations)
        if problems:
            issue_rows.append({"sequence": index, "case_id": row.get("case_id"), "model": row.get("model_label"), "issues": sorted(set(problems))})
    known = sum(bills)
    expected_total = None if missing_bills else known
    accounted = known + missing_bills * protocol.UNKNOWN_BILL_ALLOWANCE_USD
    checks["metadata_actual_bill_complete_or_unknown"] = meta.get("charged_usd") is None if missing_bills else same_number(meta.get("charged_usd"), known)
    checks["metadata_known_bill_subtotal"] = same_number(meta.get("known_charge_subtotal_usd"), known)
    checks["metadata_unknown_bill_count"] = meta.get("cost_missing_n") == missing_bills
    checks["metadata_budget_allowance"] = same_number(meta.get("unknown_bill_allowance_total_usd"), missing_bills * protocol.UNKNOWN_BILL_ALLOWANCE_USD)
    checks["metadata_budget_accounted"] = same_number(meta.get("budget_accounted_usd"), accounted)
    checks["every_response_matches_source_and_contract"] = not issue_rows
    cache_treatment = raw_cache_verification(rows, specs)
    checks["every_controlled_cache_treatment_verified"] = all(cache_treatment[label]["status"] == "verified" for label in controlled)
    observed = {}
    for label, entries_observed in usage_observations.items():
        numbers = [o.get("reasoning_tokens") for o in entries_observed]
        outputs = [o.get("output_tokens") for o in entries_observed]
        observed[label] = {"calls": len(entries_observed),
            "reasoning_counter_observed": sum(integer(n) for n in numbers),
            "reasoning_counter_unknown": sum(n is None for n in numbers),
            "reported_positive_reasoning_calls": sum(finite(n) and n > 0 for n in numbers),
            "visible_reasoning_calls": sum(bool(o.get("generated_reasoning_present")) for o in entries_observed),
            "completion_counter_observed": sum(integer(n) for n in outputs),
            "maximum_reported_completion_tokens": max((n for n in outputs if integer(n)), default=None)}
    return {"passed": all(checks.values()), "checks": checks, "issue_count": len(issue_rows), "issues": issue_rows[:100],
        "attempts": len(rows), "measured_attempts": len(actual_measured), "prime_attempts": len(actual_primes),
        "expected_measured_attempts": len(expected_measured), "expected_prime_attempts": len(expected_primes),
        "valid": sum(r.get("valid") is True for r in rows), "cache_treatment": cache_treatment,
        "billing": {"actual_total_usd": expected_total, "known_subtotal_usd": known, "missing_bills": missing_bills,
                    "budget_only_allowance_usd": missing_bills * protocol.UNKNOWN_BILL_ALLOWANCE_USD,
                    "budget_accounted_usd": accounted}, "observed_usage": observed,
        "hashes": {"responses_sha256": sha(run / "responses.jsonl"), "plan_sha256": sha(run / "plan.json"),
                   "dispatches_sha256": sha(run / "dispatches.jsonl"), "cases_sha256": sha(run / "cases.jsonl"),
                   "rulebook_sha256": sha(run / "rulebook.txt"), "protocol_sha256": sha(protocol_path),
                   "adapter_sha256": sha(ROOT / "adapter.py"), "generic_verifier_sha256": sha(ROOT / "verify.py"),
                   "cache_verifier_sha256": sha(__file__)},
        "limitations": ["Missing reasoning or baseline cache counters remain unknown; they are not verified zero.",
                        "Provider-reported usage establishes recorded billing, not an independently reconciled invoice.",
                        "Four sequential blocks are one measurement session, not independent days or deployments."]}


def verify_cache(run_dir):
    """Return the offline audit and save verification.json inside this run."""
    run = Path(run_dir)
    if not run.is_dir():
        raise ValueError("Cache run directory does not exist")
    try:
        result = _verify(run)
    except Exception as exc:
        result = {"passed": False, "checks": {"verification_completed": False},
                  "issue_count": 1, "issues": [{"error_type": type(exc).__name__, "error": str(exc)}]}
    result.update(run_dir=str(run.resolve()), generated_at_utc=datetime.now(timezone.utc).isoformat())
    (run / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    result = verify_cache(args.run_dir)
    print(json.dumps({"passed": result["passed"], "verification": str(args.run_dir / "verification.json"), "issue_count": result["issue_count"]}))
    raise SystemExit(0 if result["passed"] else 1)
