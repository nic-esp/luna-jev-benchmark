#!/usr/bin/env python3
"""Four independently primed blocks for the expanded cache experiment.

No network calls occur on import. The frozen pilot implementation is read only.
Each block contains 12 counterfactual pairs: one approved case and a case that
changes exactly one fact to violate one named requirement. Labels are checked
against two independently structured rule implementations before any API call.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import sys
import uuid

HERE = Path(__file__).resolve().parent
PILOT = HERE.parent
if str(PILOT) not in sys.path:
    sys.path.insert(0, str(PILOT))
from runner import Client, MODELS
from cache_experiment import BASE, RULEBOOK, CATALOG, ARMS, cache_payload, cache_counts, policy_truth, percentile

SCHEMA_VERSION = 1
SEED = 20261002
SUPERVISED = {"requester_role": "trainee", "supervisor_active": True,
              "supervisor_present": True, "supervisor_category_endorsed": True}

# name, category (None rotates through the catalogue), expected failed rule,
# modifications to make an eligible baseline, one changed fact causing refusal.
SPECIFICATIONS = [
    ("membership", None, "membership", {}, {"membership_active": False}),
    ("badge", None, "badge", {}, {"badge_active": False}),
    ("suspension", None, "suspension", {}, {"unresolved_suspension": True}),
    ("visitor", None, "qualification", {}, {"requester_role": "visitor"}),
    ("induction-expired", None, "induction", {"days_since_induction": 365}, {"days_since_induction": 366}),
    ("induction-negative", None, "induction", {"days_since_induction": 0}, {"days_since_induction": -1}),
    ("no-endorsement", None, "qualification", {}, {"category_endorsed": False}),
    ("endorsement-expired", None, "qualification", {"endorsement_age_days": 180}, {"endorsement_age_days": 181}),
    ("supervisor-absent", None, "qualification", SUPERVISED, {"supervisor_present": False}),
    ("supervisor-inactive", None, "qualification", SUPERVISED, {"supervisor_active": False}),
    ("supervisor-unqualified", None, "qualification", SUPERVISED, {"supervisor_category_endorsed": False}),
    ("asset-damage", None, "damage", {}, {"known_damage": True}),
    ("maintenance-due", None, "maintenance", {"maintenance_days_remaining": 1}, {"maintenance_days_remaining": 0}),
    ("red-tag", None, "release_tag", {"emergency": True, "duty_manager_signed": True}, {"release_tag": "red"}),
    ("amber-unsigned", None, "release_tag", {"release_tag": "amber", "emergency": True, "duty_manager_signed": True}, {"duty_manager_signed": False}),
    ("amber-nonemergency", None, "release_tag", {"release_tag": "amber", "emergency": True, "duty_manager_signed": True}, {"emergency": False}),
    ("calibration", "sensor", "calibration", {}, {"calibration_current": False}),
    ("reservation", None, "reservation", {"emergency": True, "duty_manager_signed": True}, {"reservation_conflicts": 1}),
    ("pickup-unsigned", None, "pickup", {"pickup_staffed": False, "after_hours_permit": True, "duty_manager_signed": True}, {"duty_manager_signed": False}),
    ("pickup-unpermitted", None, "pickup", {"pickup_staffed": False, "after_hours_permit": True, "duty_manager_signed": True}, {"after_hours_permit": False}),
    ("notice-unsigned", None, "notice", {"notice_hours": 0, "emergency": True, "duty_manager_signed": True}, {"duty_manager_signed": False}),
    ("notice-nonemergency", None, "notice", {"notice_hours": 0, "emergency": True, "duty_manager_signed": True}, {"emergency": False}),
    ("destination-unapproved", None, "destination", {}, {"destination_approved": False}),
    ("indoor-only", "optical", "indoor", {}, {"destination_indoor": False}),
    ("sensor-weather", "sensor", "weather", {"destination_indoor": False}, {"weather_clear": False}),
    ("negative-distance", None, "distance", {"transport_distance_m": 0}, {"transport_distance_m": -.01}),
    ("excess-distance", "portable", "distance", {"transport_distance_m": 2000}, {"transport_distance_m": 2000.01}),
    ("protective-case", None, "protective_case", {}, {"protective_case": False}),
    ("optical-route", "optical", "clean_route", {}, {"clean_route": False}),
    ("sterile-route", "sterile", "clean_route", {}, {"clean_route": False}),
    ("zero-duration", None, "duration", {}, {"duration_hours": 0}),
    ("excess-duration", "rotating", "duration", {"duration_hours": 2}, {"duration_hours": 2.01}),
    ("trainee-duration", "portable", "trainee_duration", dict(SUPERVISED, duration_hours=4), {"duration_hours": 4.01}),
    ("loan-count", None, "loans", {"active_other_loans": 2}, {"active_other_loans": 3}),
    ("temperature-low", "rotating", "temperature", {"environment_temperature_c": 5}, {"environment_temperature_c": 4.99}),
    ("temperature-high", "sensor", "temperature", {"environment_temperature_c": 50}, {"environment_temperature_c": 50.01}),
    ("thermal-power", "thermal", "power", {}, {"power_supply_tested": False}),
    ("rotating-power", "rotating", "power", {}, {"power_supply_tested": False}),
    ("optical-isolation", "optical", "isolation", {}, {"vibration_isolation": False}),
    ("rotating-guard", "rotating", "guard", {}, {"guard_fitted": False}),
    ("rotating-area", "rotating", "area", {}, {"area_exclusion": False}),
    ("thermal-shield", "thermal", "heat_shield", {}, {"heat_shield": False}),
    ("thermal-watch", "thermal", "fire_watch", {}, {"fire_watch": False}),
    ("sterile-seal", "sterile", "seal", {}, {"seal_intact": False}),
    ("sterile-destination", "sterile", "clean_destination", {}, {"clean_destination": False}),
    ("sterile-witness", "sterile", "witness", {}, {"sterile_witness": False}),
    ("battery-below-minimum", "portable", "battery", {"battery_percent": 30}, {"battery_percent": 29.99}),
    ("battery-invalid", "optical", "battery", {"battery_percent": 100}, {"battery_percent": 100.01}),
]


def failed_rules(f):
    """Independent procedural audit; names each failed written requirement."""
    failures = []
    def require(rule, condition):
        if not condition:
            failures.append(rule)
    category = f["category"]
    require("membership", f["membership_active"])
    require("badge", f["badge_active"])
    require("suspension", not f["unresolved_suspension"])
    require("induction", f["days_since_induction"] >= 0 and f["days_since_induction"] <= 365)
    qualification = False
    if f["requester_role"] in ("member", "trainee"):
        if f["supervisor_active"] and f["supervisor_present"] and f["supervisor_category_endorsed"]:
            qualification = True
        if f["requester_role"] == "member" and f["category_endorsed"] and f["endorsement_age_days"] >= 0 and f["endorsement_age_days"] <= 180:
            qualification = True
    require("qualification", qualification)
    require("damage", not f["known_damage"])
    require("maintenance", f["maintenance_days_remaining"] >= 1)
    if f["release_tag"] == "amber":
        require("release_tag", f["emergency"] and f["duty_manager_signed"])
    else:
        require("release_tag", f["release_tag"] == "green")
    if category in ("optical", "thermal", "rotating", "sensor"):
        require("calibration", f["calibration_current"])
    require("reservation", f["reservation_conflicts"] == 0)
    if not f["pickup_staffed"]:
        require("pickup", f["after_hours_permit"] and f["duty_manager_signed"])
    if f["notice_hours"] < 2:
        require("notice", f["emergency"] and f["duty_manager_signed"])
    require("destination", f["destination_approved"])
    if category in ("optical", "thermal", "rotating", "sterile"):
        require("indoor", f["destination_indoor"])
    if category == "sensor" and not f["destination_indoor"]:
        require("weather", f["weather_clear"])
    limits = {"optical": (8, 0, 400, 10, 30), "thermal": (4, 0, 200, 5, 35),
              "rotating": (2, 0, 100, 5, 35), "portable": (24, 30, 2000, -10, 45),
              "sensor": (72, 20, 3000, -20, 50), "sterile": (6, 10, 300, 15, 25)}
    duration, battery, distance, low, high = limits[category]
    require("distance", f["transport_distance_m"] >= 0 and f["transport_distance_m"] <= distance)
    if f["transport_distance_m"] > 0:
        require("protective_case", f["protective_case"])
        if category in ("optical", "sterile"):
            require("clean_route", f["clean_route"])
    require("duration", f["duration_hours"] > 0 and f["duration_hours"] <= duration)
    if f["requester_role"] == "trainee":
        require("trainee_duration", f["duration_hours"] <= 4)
    require("loans", f["active_other_loans"] >= 0 and f["active_other_loans"] < 3)
    if category in ("thermal", "rotating"):
        require("power", f["power_supply_tested"])
    if category == "optical":
        require("isolation", f["vibration_isolation"])
    if category == "rotating":
        require("guard", f["guard_fitted"])
        require("area", f["area_exclusion"])
    if category == "thermal":
        require("heat_shield", f["heat_shield"])
        require("fire_watch", f["fire_watch"])
    if category == "sterile":
        require("seal", f["seal_intact"])
        require("clean_destination", f["clean_destination"])
        require("witness", f["sterile_witness"])
    require("temperature", f["environment_temperature_c"] >= low and f["environment_temperature_c"] <= high)
    require("battery", f["battery_percent"] >= battery and f["battery_percent"] <= 100)
    return failures


def dataset(seed=SEED):
    records = []
    # Distribute the rule families through all four time blocks. Each block
    # receives three scenarios from each quarter of the specification list.
    for index, (name, category, failure, good_changes, bad_change) in enumerate(SPECIFICATIONS):
        rng = random.Random(seed + index)
        category = category or list(CATALOG)[index % 6]
        max_duration, min_battery, max_distance, low, high = CATALOG[category]
        facts = dict(BASE, category=category, days_since_induction=rng.choice([0, 30, 180, 365]),
                     endorsement_age_days=rng.choice([0, 30, 90, 180]),
                     maintenance_days_remaining=rng.randint(1, 100), notice_hours=rng.randint(2, 36),
                     transport_distance_m=rng.randint(1, max_distance), duration_hours=rng.choice([.5, 1, min(2, max_duration)]),
                     active_other_loans=rng.randint(0, 2), battery_percent=rng.randint(min_battery, 100),
                     environment_temperature_c=rng.randint(low, high))
        facts.update(good_changes)
        for target in (1, 0):
            use = dict(facts) if target else dict(facts, **bad_change)
            audit = failed_rules(use)
            expected_failures = [] if target else [failure]
            if audit != expected_failures or policy_truth(use) != target:
                raise ValueError(f"Ground-truth audit failed for {name}/{target}: {audit}")
            records.append({"id": f"expanded-cache-{index:02d}-{name}-{'yes' if target else 'no'}",
                            "experiment": "expanded_cache_policy", "target": target, "target_kind": "label",
                            "state": {"rulebook": RULEBOOK, "facts": use},
                            "question": "Must this equipment dispatch be approved now under the supplied fictional rulebook?",
                            "criteria": {"true": "Every applicable requirement passes, including any precisely stated exceptions.",
                                         "false": "At least one applicable requirement fails."},
                            "block": index % 4, "pair_id": f"policy-pair-{index:02d}",
                            "source": {"kind": "synthetic_counterfactual_policy", "generator_version": SCHEMA_VERSION,
                                       "seed": seed, "scenario": name, "changed_fact": next(iter(bad_change)),
                                       "manual_expected_label": target, "expected_failed_rules": expected_failures,
                                       "validation": "Manual expected label, frozen pilot oracle, and independent named-rule audit agree."}})
    facts_hashes = [json.dumps(r["state"]["facts"], sort_keys=True) for r in records]
    if len(set(facts_hashes)) != 96:
        raise ValueError("Duplicate case facts")
    return records


def with_prefix(case, identifier):
    # The nonce is in the message prefix itself, not just the routing cache key.
    # It appears identically in all three arms and conveys no target information.
    result = dict(case)
    result["state"] = dict(case["state"])
    result["state"]["rulebook"] = ("Administrative batch identifier: " + identifier + ". "
                                  "This identifier changes no rule or case fact.\n\n" + RULEBOOK)
    return result


def prime_case(block, identifier):
    facts = dict(BASE, duration_hours=1.125 + block / 10, notice_hours=47 + block, battery_percent=83 + block)
    assert policy_truth(facts) == 1 and failed_rules(facts) == []
    result = {"id": f"expanded-cache-prime-{block}", "experiment": "expanded_cache_policy",
              "target": 1, "target_kind": "label", "block": block,
              "state": {"rulebook": RULEBOOK, "facts": facts},
              "question": "Must this equipment dispatch be approved now under the supplied fictional rulebook?",
              "criteria": {"true": "Every applicable requirement passes.", "false": "At least one requirement fails."}}
    return with_prefix(result, identifier)


def atomic_json(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2))
    tmp.replace(path)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def complete_cost(rows):
    known = [r["cost_usd"] for r in rows if r.get("cost_usd") is not None]
    return {"total_usd": sum(known) if len(known) == len(rows) else None,
            "known_subtotal_usd": sum(known), "missing_n": len(rows) - len(known)}


def describe(rows):
    valid = [r for r in rows if r["valid"]]
    latency = [r["latency_s"] for r in rows]
    counts = [cache_counts(r) for r in rows]
    reads = [c["cached_tokens"] for c in counts if c["cached_tokens"] is not None]
    writes = [c["cache_write_tokens"] for c in counts if c["cache_write_tokens"] is not None]
    cost = complete_cost(rows)
    return {"n": len(rows), "valid_n": len(valid), "cost": cost,
            "cost_per_1000_usd": cost["total_usd"] / len(rows) * 1000 if rows and cost["total_usd"] is not None else None,
            "accuracy_including_invalid_as_wrong": sum((r["probability"] >= .5) == bool(r["target"]) for r in valid) / len(rows) if rows else None,
            "brier_valid_only": statistics.mean((r["probability"] - r["target"]) ** 2 for r in valid) if valid else None,
            "median_latency_s": statistics.median(latency) if latency else None,
            "mean_latency_s": statistics.mean(latency) if latency else None,
            "p95_latency_s": percentile(latency, .95),
            "cache_observed_n": len(reads), "cache_hit_calls": sum(x > 0 for x in reads),
            "cache_read_tokens": sum(reads) if rows and len(reads) == len(rows) else None,
            "cache_write_observed_n": len(writes),
            "cache_write_tokens": sum(writes) if rows and len(writes) == len(rows) else None,
            "unique_cache_read_sizes": sorted(set(reads)),
            "response_models": sorted({r.get("response_model") for r in rows if r.get("response_model")}),
            "providers": sorted({r.get("provider") for r in rows if r.get("provider")})}


def paired_stats(rows):
    by_case = {}
    for r in rows:
        by_case.setdefault(r["case_id"], {})[r["model_label"]] = r
    result = {}
    for left, right in (("Luna cached", "Luna uncached"), ("Luna cached", "Jev"), ("Luna uncached", "Jev")):
        pairs = [(arms[left], arms[right]) for arms in by_case.values() if left in arms and right in arms and arms[left]["valid"] and arms[right]["valid"]]
        costs_known = pairs and all(a.get("cost_usd") is not None and b.get("cost_usd") is not None for a, b in pairs)
        denominator = sum(b["cost_usd"] for a, b in pairs) if costs_known else 0
        result[left + " / " + right] = {
            "n": len(pairs), "median_latency_ratio": statistics.median(a["latency_s"] / b["latency_s"] for a, b in pairs) if pairs else None,
            "mean_latency_difference_s": statistics.mean(a["latency_s"] - b["latency_s"] for a, b in pairs) if pairs else None,
            "cost_ratio": sum(a["cost_usd"] for a, b in pairs) / denominator if denominator else None,
            "label_agreement": statistics.mean((a["probability"] >= .5) == (b["probability"] >= .5) for a, b in pairs) if pairs else None,
            "accuracy_difference": statistics.mean(int((a["probability"] >= .5) == bool(a["target"])) - int((b["probability"] >= .5) == bool(b["target"])) for a, b in pairs) if pairs else None,
        }
    return result


def summary(records, metadata):
    measured = [r for r in records if r["phase"] == "measured"]
    primes = [r for r in records if r["phase"] == "cache_prime"]
    result = {"schema_version": SCHEMA_VERSION, "metadata": metadata,
              "arms": {arm: describe([r for r in measured if r["model_label"] == arm]) for arm in ARMS},
              "paired": paired_stats(measured), "blocks": [], "total_cost": complete_cost(records)}
    for block in range(4):
        rows = [r for r in measured if r["block"] == block]
        prime = next((r for r in primes if r["block"] == block), None)
        prime_counts = cache_counts(prime) if prime else {}
        arms = {arm: describe([r for r in rows if r["model_label"] == arm]) for arm in ARMS}
        cached, control = arms["Luna cached"], arms["Luna uncached"]
        stable_reads = cached["n"] == 24 and cached["cache_observed_n"] == 24 and len(cached["unique_cache_read_sizes"]) == 1
        read_size_matches_write = stable_reads and cached["unique_cache_read_sizes"][0] == prime_counts.get("cache_write_tokens") and cached["unique_cache_read_sizes"][0] > 0
        result["blocks"].append({"block": block, "arms": arms, "paired": paired_stats(rows),
                                 "prime": {"cost_usd": prime.get("cost_usd"), "latency_s": prime["latency_s"], **prime_counts} if prime else None,
                                 "cold_prime_verified": bool(prime and prime["valid"] and prime_counts.get("cached_tokens") == 0 and (prime_counts.get("cache_write_tokens") or 0) > 0),
                                 "all_cached_requests_hit": cached["n"] == 24 and cached["cache_observed_n"] == 24 and cached["cache_hit_calls"] == 24,
                                 "stable_cached_prefix_token_count": cached["unique_cache_read_sizes"][0] if stable_reads else None,
                                 "read_size_matches_prime_write": read_size_matches_write,
                                 "uncached_control_verified": control["n"] == 24 and control["cache_observed_n"] == 24 and control["cache_read_tokens"] == 0 and control["cache_write_observed_n"] == 24 and control["cache_write_tokens"] == 0,
                                 "all_arms_complete": all(s["n"] == 24 and s["valid_n"] == 24 for s in arms.values())})
    prime_cost = complete_cost(primes)
    cached = result["arms"]["Luna cached"]
    setup_cost = cached["cost"]["total_usd"] + prime_cost["total_usd"] if cached["cost"]["total_usd"] is not None and prime_cost["total_usd"] is not None and len(primes) == 4 else None
    result["cache_verification"] = {
        "cold_primed_blocks": sum(b["cold_prime_verified"] for b in result["blocks"]),
        "blocks_with_all_24_cache_hits": sum(b["all_cached_requests_hit"] for b in result["blocks"]),
        "blocks_with_verified_uncached_control": sum(b["uncached_control_verified"] for b in result["blocks"]),
        "fully_verified": all(b["cold_prime_verified"] and b["all_cached_requests_hit"] and b["uncached_control_verified"] and b["all_arms_complete"] and b["read_size_matches_prime_write"] for b in result["blocks"]),
        "prime_count": len(primes), "priming_cost": prime_cost, "setup_inclusive_cached_cost_usd": setup_cost,
        "setup_inclusive_cached_cost_per_1000_usd": setup_cost / cached["n"] * 1000 if setup_cost is not None and cached["n"] else None}
    result["definitions"] = {"accuracy": "Probability >=0.5 means yes; invalid responses count as incorrect.",
                             "cost_per_1000": "Observed mean billed cost scaled to 1,000 requests; not another run.",
                             "setup_inclusive": "All four distinct priming charges plus cached measured charges, allocated across measured cached cases.",
                             "latency": "Full non-streaming request time, including network and service processing; prime excluded from measured distributions.",
                             "blocks": "Four separately primed prefixes, with 12 distinct counterfactual case pairs per block. Blocks are sequential in one run, not independent days or deployments.",
                             "ground_truth": "Synthetic written-policy cases. Each pair differs by one fact; manual expected labels checked by two local rule implementations. This is not an established external benchmark.",
                             "randomisation": "Counterfactual pairs distributed across blocks, case order random within block, model arm order random within case; same pair may appear far apart in time."}
    return result


def run_cache_study(key, out_dir, budget, progress=None, seed=SEED, resume=False):
    """Run exactly 292 planned requests; never retry a failed call automatically.

    Resume is supported only after complete blocks, preserving time-block meaning.
    Mid-block logs remain reviewable; a partial block is not silently rerun.
    """
    if not isinstance(key, str) or not key.startswith("sk-or-"):
        raise ValueError("Expected an OpenRouter API key")
    if not math.isfinite(budget) or budget <= 0:
        raise ValueError("Budget must be finite and positive")
    out_dir = Path(out_dir)
    cases = dataset(seed)
    frozen = "".join(json.dumps(c, sort_keys=True) + "\n" for c in cases)
    meta_path = out_dir / "metadata.json"
    raw_path = out_dir / "responses.jsonl"
    records = []
    completed_blocks = set()
    if resume:
        metadata = json.loads(meta_path.read_text())
        if metadata["cases_sha256"] != digest(frozen.encode()) or metadata["script_sha256"] != digest(Path(__file__).read_bytes()):
            raise ValueError("Cannot resume with changed cases or cache-study source")
        if metadata["pilot_runner_sha256"] != digest((PILOT / "runner.py").read_bytes()) or metadata["pilot_cache_adapter_sha256"] != digest((PILOT / "cache_experiment.py").read_bytes()):
            raise ValueError("Cannot resume with changed imported request adapters")
        records = [json.loads(line) for line in raw_path.read_text().splitlines() if line.strip()]
        if any(not r["valid"] or r.get("cost_usd") is None for r in records):
            raise ValueError("Cannot resume a run with an invalid request or unknown charge")
        for block in {r["block"] for r in records}:
            block_records = [r for r in records if r["block"] == block]
            if len(block_records) != 73:
                raise ValueError("Resume is allowed only at complete 73-request block boundaries")
            expected = {(c["id"], arm, "measured") for c in cases if c["block"] == block for arm in ARMS}
            expected.add((f"expanded-cache-prime-{block}", "Luna cached", "cache_prime"))
            if {(r["case_id"], r["model_label"], r["phase"]) for r in block_records} != expected:
                raise ValueError("Saved block identities do not match the frozen protocol")
            completed_blocks.add(block)
        metadata["budget_usd"] = budget
        metadata["resume_count"] = metadata.get("resume_count", 0) + 1
    else:
        out_dir.mkdir(parents=True, exist_ok=False)
        (out_dir / "cases.jsonl").write_text(frozen)
        (out_dir / "rulebook.txt").write_text(RULEBOOK)
        metadata = {"schema_version": SCHEMA_VERSION, "experiment": "expanded_cache_policy", "created_at": datetime.now(timezone.utc).isoformat(),
                    "seed": seed, "models": {arm: MODELS["Jev" if arm == "Jev" else "Luna"] for arm in ARMS},
                    "blocks": 4, "cases": 96, "counterfactual_pairs": 48, "calls_planned": 292, "budget_usd": budget,
                    "block_identifiers": [uuid.uuid4().hex for _ in range(4)],
                    "cases_sha256": digest(frozen.encode()), "rulebook_sha256": digest(RULEBOOK.encode()),
                    "script_sha256": digest(Path(__file__).read_bytes()), "pilot_runner_sha256": digest((PILOT / "runner.py").read_bytes()),
                    "pilot_cache_adapter_sha256": digest((PILOT / "cache_experiment.py").read_bytes()),
                    "luna_reasoning": "none", "luna_provider": "OpenAI", "automatic_retries": 0,
                    "resume_policy": "Only complete-block boundaries; partial blocks are preserved without replay.",
                    "protocol": "Four sequential blocks. Each has a unique administrative identifier inside the shared prefix, one separate Luna cached cold-prime attempt, and 24 cases with randomly ordered three-arm requests. One persistent HTTPS connection. Cache identity, cold writes, reads, and disabled controls verified from usage.",
                    "billing_guard": "Before each call reserve every request UTF-8 byte plus 4096 as input tokens at $1/M, plus 128 output tokens at $2/M. Stop on unknown costs or observed charges above budget. This is a local accounting guard, not an account-level provider spend cap."}
    charged = sum(r["cost_usd"] for r in records)
    if charged > budget:
        raise ValueError("Budget is already below recorded spend")
    metadata.pop("error", None)
    metadata.update(status="running", charged_usd=charged, calls_completed=len(records))
    atomic_json(meta_path, metadata)
    client = Client(key)
    try:
        with raw_path.open("a", buffering=1) as raw:
            for block in range(4):
                if block in completed_blocks:
                    continue
                identifier = metadata["block_identifiers"][block]
                rng = random.Random(seed + 10000 + block)
                block_cases = [c for c in cases if c["block"] == block]
                rng.shuffle(block_cases)
                schedule = [("cache_prime", prime_case(block, identifier), "Luna cached")]
                for case in block_cases:
                    arms = list(ARMS)
                    rng.shuffle(arms)
                    schedule.extend(("measured", with_prefix(case, identifier), arm) for arm in arms)
                for phase, case, arm in schedule:
                    endpoint, payload = cache_payload(case, arm, "expanded-cache-" + identifier)
                    reserve = (len(json.dumps(payload).encode()) + 4096) * .000001 + 128 * .000002
                    if charged + reserve > budget:
                        raise RuntimeError("Budget guard stopped before next request")
                    record = client.run(case, arm, phase, len(records), request_override=payload,
                                        endpoint_override=endpoint, base_label="Jev" if arm == "Jev" else "Luna")
                    if arm != "Jev" and record.get("provider") != "OpenAI":
                        record.update(valid=False, error="Unexpected Luna provider; expected OpenAI")
                    record.update(block=block, pair_id=case.get("pair_id"), prefix_identifier=identifier,
                                  prefix_sha256=digest(case["state"]["rulebook"].encode()), **cache_counts(record))
                    records.append(record)
                    raw.write(json.dumps(record, ensure_ascii=False) + "\n")
                    if record.get("cost_usd") is not None:
                        charged += record["cost_usd"]
                    cost = complete_cost(records)
                    metadata.update(charged_usd=cost["total_usd"], known_charge_subtotal_usd=charged,
                                    cost_missing_n=cost["missing_n"], calls_completed=len(records), current_block=block)
                    atomic_json(meta_path, metadata)
                    atomic_json(out_dir / "checkpoint.json", {"completed_calls": len(records), "completed_blocks": sorted(completed_blocks),
                                                               "last_case": case["id"], "last_arm": arm, "phase": phase})
                    event = {"phase": phase, "block": block, "completed": len(records), "planned": 292,
                             "case": case["id"], "model": arm, "valid": record["valid"], "latency_s": record["latency_s"],
                             "cached_tokens": record["cached_tokens"], "cache_write_tokens": record["cache_write_tokens"],
                             "known_charge_subtotal_usd": charged}
                    if progress:
                        progress(event)
                    else:
                        print(json.dumps(event), flush=True)
                    if record.get("cost_usd") is None:
                        raise RuntimeError("Missing recorded charge; stopped to preserve budget accounting")
                    if not record["valid"]:
                        raise RuntimeError("Request failed; no retry: " + str(record.get("error")))
                    if charged > budget:
                        raise RuntimeError("Observed charges exceeded budget; stopped")
                completed_blocks.add(block)
                metadata["completed_blocks"] = sorted(completed_blocks)
                atomic_json(meta_path, metadata)
                atomic_json(out_dir / "checkpoint.json", {"completed_calls": len(records), "completed_blocks": sorted(completed_blocks),
                                                           "last_case": case["id"], "last_arm": arm, "phase": "block_complete"})
                atomic_json(out_dir / "summary.json", summary(records, metadata))
        metadata["status"] = "complete"
    except Exception as exc:
        metadata.update(status="stopped", error=str(exc).replace(key, "[REDACTED]"))
    finally:
        client.close()
        cost = complete_cost(records)
        metadata.update(finished_at=datetime.now(timezone.utc).isoformat(), charged_usd=cost["total_usd"],
                        known_charge_subtotal_usd=cost["known_subtotal_usd"], cost_missing_n=cost["missing_n"],
                        completed_blocks=sorted(completed_blocks))
        atomic_json(meta_path, metadata)
        result = summary(records, metadata)
        atomic_json(out_dir / "summary.json", result)
    return {"run_dir": str(out_dir), "metadata": metadata, "summary": result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--budget", type=float, default=1.0)
    parser.add_argument("--key-stdin", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        cases = dataset()
        print(json.dumps({"cases": len(cases), "yes": sum(c["target"] for c in cases),
                          "blocks": dict(Counter(c["block"] for c in cases)), "distinct_scenarios": len(SPECIFICATIONS),
                          "named_failed_rules": sorted({x for c in cases for x in c["source"]["expected_failed_rules"]}),
                          "calls": 292}, indent=2))
        return
    if not args.key_stdin or args.out_dir is None:
        parser.error("Use --key-stdin and --out-dir; credentials are never accepted in command arguments.")
    result = run_cache_study(sys.stdin.readline().strip(), args.out_dir, args.budget, resume=args.resume)
    print(json.dumps(result), flush=True)
    if result["metadata"]["status"] != "complete":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
