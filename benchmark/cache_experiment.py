#!/usr/bin/env python3
"""Controlled long-prefix cache pilot. Standard library only; no API calls on import."""
import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
import math
import os
from pathlib import Path
import random
import statistics
import sys
import uuid

from runner import Client, MODELS, ROOT, SYSTEM, SCHEMA, shared_input

DOC_URL = "https://openrouter.ai/docs/guides/best-practices/prompt-caching"
ARMS = ("Luna cached", "Luna uncached", "Jev")

# Fictional policy, written specifically for this experiment. Every paragraph
# specifies operative rules or resolves a possible ambiguity in applying them.
RULEBOOK = """NORTH QUAY SHARED EQUIPMENT SERVICE — DISPATCH RULEBOOK, VERSION 1

1. Scope and decision
This fictional service lends six categories of equipment inside a research campus. Decide whether the service must approve the requested dispatch now. Approval means every applicable requirement below passes. A request that fails any requirement is not approved, even if it would pass most others. This is a deterministic policy decision, not an estimate of future behaviour. All facts in a request are authoritative for this exercise. Do not infer missing permission from a persuasive purpose or a low level of apparent risk. Apply only the written rules, using the supplied numerical facts rather than real-world assumptions about equipment, employment, or safety. A fact marked false is false, rather than merely undocumented.

2. Equipment catalogue
There are exactly six categories: optical, thermal, rotating, portable, sensor, and sterile. Their maximum ordinary checkout durations, in hours, are respectively 8, 4, 2, 24, 72, and 6. Their minimum remaining battery percentages are respectively 0, 0, 0, 30, 20, and 10. Zero means there is no battery requirement for that category; it does not mean the asset must have an empty battery. The standard maximum transport distance is 400 metres for optical, 200 for thermal, 100 for rotating, 2000 for portable, 3000 for sensor, and 300 for sterile. Catalogue limits are inclusive. A duration equal to its category limit passes the duration rule. A transport distance equal to its applicable maximum passes the transport rule.

3. Interpretation and precedence
Every numbered section is cumulative except for an exception explicitly named in that section. An exception changes only the particular condition it names. A duty-manager signature has no general overriding force. Likewise an emergency flag is not a universal waiver. If an exception applies, continue checking the other requirements. The words 'and' and 'both' mean every listed condition must hold; 'or' means at least one listed condition must hold. Distances, hours, days, temperatures, counts, and percentages are ordinary numeric values. Do not round a value before comparison. A value just above a maximum fails even if the difference is small. Descriptive notes never override structured facts.

4. Requester and access status
The requester must have an active membership, an active access badge, and no unresolved equipment suspension. These are three independent conditions. A supervisor cannot cure an expired badge or a suspended membership. A duty manager also cannot waive these conditions. The service distinguishes trained members, trainees, and visitors, using the requester_role field. A visitor is never eligible for checkout under this rulebook, regardless of supervision. A trainee can be eligible through the supervision process in section 7. An ordinary member can be eligible through an individual endorsement or through that same supervision process. For the purpose of this test, these are the only requester roles.

5. General safety induction
An otherwise eligible requester must have completed the campus safety induction at most 365 days before dispatch. The days_since_induction field is the elapsed number of full days since that completion. Day zero means completion today. Day 365 still passes; day 366 fails. A negative number is invalid and fails. Category endorsement, experience, and an attending supervisor do not replace the general induction. This condition remains in force during an emergency. The rule is intentionally based on elapsed days rather than calendar years, so leap years and dates need not be considered. Each case contains the elapsed value directly, and that value must be used without recalculation.

6. Individual category endorsement
An ordinary member may satisfy the category qualification requirement by holding a category endorsement completed at most 180 days ago, provided category_endorsed is true. Both the endorsement flag and its age must pass. An age from zero through 180 days is acceptable. An expired endorsement with age 181 does not qualify, even if the requester has often used the device. Trainees cannot qualify through their own endorsement alone: they must meet the supervision process. A member whose endorsement is absent or expired may also meet the supervision process instead. This alternative affects the category qualification condition only; it does not waive induction, access, asset condition, or any operational requirement.

7. Supervised checkout
Supervision qualifies a requester only when supervisor_active, supervisor_present, and supervisor_category_endorsed are all true. These facts mean, respectively, that the supervisor is currently authorised, will be physically present throughout use, and currently holds the category-specific endorsement. Remote availability by telephone does not satisfy physical presence. A supervisor who is present but lacks the appropriate endorsement also fails. Trainees must use this route, while ordinary members may choose this route when their individual endorsement does not qualify. A qualifying supervisor does not change the maximum duration, transport distance, battery limit, or location rules. The supervision requirement is evaluated independently from the separate sterile handling witness in section 15.

8. Asset condition and service status
The asset must have no known damage and must have at least one full day remaining until its next required maintenance. maintenance_days_remaining equal to one passes; zero fails. Negative values fail as overdue. The release tag is green, amber, or red. A green tag passes the tag condition. A red tag always fails, including during an emergency. An amber tag passes only when the request is an emergency and a duty manager has signed it. Both flags are required for that amber exception. The exception does not repair damage or waive maintenance. For example, an amber asset with the necessary signatures still fails if known_damage is true.

9. Calibration and reservations
Optical, thermal, rotating, and sensor devices require calibration_current to be true. Portable and sterile devices have no calibration requirement in this policy. A false calibration flag for those two categories is therefore irrelevant. Every category also requires reservation_conflicts to equal zero. One or more overlapping reservations fails the reservation condition. Manager signature, emergency use, and supervision do not override another reservation. The conflict count is already calculated from the proposed time window; do not compare actual dates. A zero count passes even when other bookings exist outside that window. The policy does not ask the model to decide which of two requesters is more deserving.

10. Pickup timing and advance notice
The requested pickup must be during staffed opening hours, represented by pickup_staffed. When pickup_staffed is false, the timing condition passes only if after_hours_permit and duty_manager_signed are both true. The after-hours permit alone is insufficient. Independently, the request must provide at least two hours of advance notice through notice_hours. The notice minimum is waived only when emergency and duty_manager_signed are both true. A signed non-emergency request still needs two hours. A signed emergency may have zero notice, but it still needs staffed pickup or the named after-hours exception. No part of this section changes the reservation or qualification requirements.

11. Approved place of use
Every request must have destination_approved equal to true. The destination approval is required even for a very short use. Optical, thermal, rotating, and sterile equipment must also remain indoors, so destination_indoor must be true for those categories. Portable and sensor equipment may be used indoors or outdoors. A sensor that will be used outdoors additionally requires weather_clear to be true. Portable equipment has no weather-clear condition. Indoor sensor use does not require clear weather. The emergency status has no effect on these location conditions. The supplied destination facts describe the actual proposed operating location, not merely the pickup point or the beginning of the transport route.

12. Transport route
The proposed transport_distance_m must be nonnegative and no greater than the catalogue maximum for the category. All six categories need protective_case equal to true whenever transport_distance_m is greater than zero. A movement of zero metres does not require a protective case. Sterile and optical devices additionally require a clean_route whenever there is any transport. The other categories do not require a clean route under this rulebook. An approved destination does not imply that the transport route is clean. Supervision, emergency status, and a manager signature cannot increase a distance limit or replace a required protective case. The maximum distance concerns the complete one-way route, not straight-line distance on a map.

13. Duration and workload
The requested duration_hours must be positive and must not exceed the catalogue limit. For trainees, the duration must also be at most four hours, even when the catalogue permits longer borrowing. Apply the smaller applicable limit. A member using supervision keeps the ordinary category duration limit and is not treated as a trainee. Every requester must have active_other_loans less than three. A requester with zero, one, or two other loans passes; a requester with three fails. The asset requested here is not included in that count. An emergency does not waive the loan-count limit. A manager signature does not extend a duration limit.

14. Electrical, thermal, and rotating operation
Thermal and rotating equipment require power_supply_tested equal to true. Optical equipment requires vibration_isolation equal to true. Rotating equipment additionally requires both guard_fitted and area_exclusion to be true. Thermal equipment additionally requires heat_shield and fire_watch to be true. These category-specific requirements do not apply to portable, sensor, or sterile equipment. An unrelated false flag is not a reason for refusal. For example, fire_watch is irrelevant to an optical checkout, while vibration_isolation is irrelevant to a thermal checkout. All electrical and mechanical conditions remain in force during an emergency and when a supervisor attends. A working protective case during transport does not replace these operating protections.

15. Sterile handling
Sterile equipment requires seal_intact, clean_destination, and sterile_witness to be true. All three conditions must pass. The sterile witness is a separate handling role and need not be the category supervisor. A trainee borrowing sterile equipment still needs both a qualifying supervisor and a sterile witness. The presence of one role must not be inferred to imply the other. Sterile equipment also requires at least 10 percent battery under the catalogue, regardless of whether the witness is present. A broken seal cannot be cured by a clean destination or an emergency manager signature. For non-sterile categories these three sterile-specific fields are irrelevant to approval.

16. Environment and battery
The proposed environment_temperature_c must be between 10 and 30 degrees inclusive for optical, between 5 and 35 inclusive for thermal, between 5 and 35 inclusive for rotating, between minus 10 and 45 inclusive for portable, between minus 20 and 50 inclusive for sensor, and between 15 and 25 inclusive for sterile. A value at either endpoint passes. These are operating temperatures; they do not describe weather during transport. Compare battery_percent with the category minimum in section 2. Battery percent must also be between zero and 100 inclusive for every category. A value of 101 is invalid even if the battery minimum is zero. No signature or supervision exception changes these limits.

17. Record completeness and the final answer
The case facts provide every field needed to apply the rules. All listed booleans and numbers are deliberate, including irrelevant fields. Do not award approval merely because the case description calls it routine, urgent, harmless, or educational. Evaluate the structured facts against all applicable sections. The target question is whether dispatch must be approved now under this rulebook, not whether it could become approvable after changes. No means at least one current requirement fails. Yes means every current requirement passes. Return the required probability object; because the policy and facts are deterministic, a completely certain application yields zero for no or one for yes. Do not add explanations or other fields.
"""

CATALOG = {
    "optical": (8, 0, 400, 10, 30), "thermal": (4, 0, 200, 5, 35),
    "rotating": (2, 0, 100, 5, 35), "portable": (24, 30, 2000, -10, 45),
    "sensor": (72, 20, 3000, -20, 50), "sterile": (6, 10, 300, 15, 25),
}
BASE = dict(category="portable", membership_active=True, badge_active=True,
            unresolved_suspension=False, requester_role="member", days_since_induction=30,
            category_endorsed=True, endorsement_age_days=30, supervisor_active=False,
            supervisor_present=False, supervisor_category_endorsed=False, known_damage=False,
            maintenance_days_remaining=10, release_tag="green", emergency=False,
            duty_manager_signed=False, calibration_current=True, reservation_conflicts=0,
            pickup_staffed=True, after_hours_permit=False, notice_hours=4,
            destination_approved=True, destination_indoor=True, weather_clear=True,
            transport_distance_m=50, protective_case=True, clean_route=True,
            duration_hours=1, active_other_loans=0, power_supply_tested=True,
            vibration_isolation=True, guard_fitted=True, area_exclusion=True,
            heat_shield=True, fire_watch=True, seal_intact=True, clean_destination=True,
            sterile_witness=True, environment_temperature_c=20, battery_percent=100)


def policy_truth(f):
    """Executable oracle for the fictional written policy; returns exact 0 or 1."""
    category = f["category"]
    duration, battery, distance, low, high = CATALOG[category]
    supervisor = all(f[x] for x in ("supervisor_active", "supervisor_present", "supervisor_category_endorsed"))
    endorsed = f["category_endorsed"] and 0 <= f["endorsement_age_days"] <= 180
    qualified = (f["requester_role"] == "member" and (endorsed or supervisor)) or (f["requester_role"] == "trainee" and supervisor)
    emergency_signed = f["emergency"] and f["duty_manager_signed"]
    checks = [
        f["membership_active"], f["badge_active"], not f["unresolved_suspension"],
        0 <= f["days_since_induction"] <= 365, qualified, not f["known_damage"],
        f["maintenance_days_remaining"] >= 1,
        f["release_tag"] == "green" or (f["release_tag"] == "amber" and emergency_signed),
        category not in ("optical", "thermal", "rotating", "sensor") or f["calibration_current"],
        f["reservation_conflicts"] == 0,
        f["pickup_staffed"] or (f["after_hours_permit"] and f["duty_manager_signed"]),
        f["notice_hours"] >= 2 or emergency_signed,
        f["destination_approved"],
        category not in ("optical", "thermal", "rotating", "sterile") or f["destination_indoor"],
        category != "sensor" or f["destination_indoor"] or f["weather_clear"],
        0 <= f["transport_distance_m"] <= distance,
        f["transport_distance_m"] == 0 or f["protective_case"],
        category not in ("optical", "sterile") or f["transport_distance_m"] == 0 or f["clean_route"],
        0 < f["duration_hours"] <= duration,
        f["requester_role"] != "trainee" or f["duration_hours"] <= 4,
        0 <= f["active_other_loans"] < 3,
        category not in ("thermal", "rotating") or f["power_supply_tested"],
        category != "optical" or f["vibration_isolation"],
        category != "rotating" or (f["guard_fitted"] and f["area_exclusion"]),
        category != "thermal" or (f["heat_shield"] and f["fire_watch"]),
        category != "sterile" or (f["seal_intact"] and f["clean_destination"] and f["sterile_witness"]),
        low <= f["environment_temperature_c"] <= high,
        battery <= f["battery_percent"] <= 100,
    ]
    return int(all(checks))


# Each tuple has an independently specified expected label. The builder checks
# it against the executable oracle, and tests also cover individual rule limits.
SPECS = [
    ("induction-boundary", 1, dict(category="optical", days_since_induction=365, endorsement_age_days=180, duration_hours=8)),
    ("induction-expired", 0, dict(category="optical", days_since_induction=366, emergency=True, duty_manager_signed=True)),
    ("trainee-supervised", 1, dict(requester_role="trainee", category_endorsed=False, supervisor_active=True, supervisor_present=True, supervisor_category_endorsed=True, duration_hours=4)),
    ("trainee-remote", 0, dict(requester_role="trainee", supervisor_active=True, supervisor_present=False, supervisor_category_endorsed=True)),
    ("amber-exception", 1, dict(category="thermal", release_tag="amber", emergency=True, duty_manager_signed=True, notice_hours=0)),
    ("red-no-exception", 0, dict(category="thermal", release_tag="red", emergency=True, duty_manager_signed=True)),
    ("after-hours-permitted", 1, dict(category="rotating", pickup_staffed=False, after_hours_permit=True, duty_manager_signed=True, notice_hours=2, duration_hours=2)),
    ("after-hours-unsigned", 0, dict(category="rotating", pickup_staffed=False, after_hours_permit=True, duty_manager_signed=False)),
    ("sensor-outdoor-boundary", 1, dict(category="sensor", destination_indoor=False, environment_temperature_c=-20, duration_hours=72, transport_distance_m=3000, battery_percent=20)),
    ("sensor-outdoor-weather", 0, dict(category="sensor", destination_indoor=False, weather_clear=False)),
    ("portable-weather-irrelevant", 1, dict(destination_indoor=False, weather_clear=False, calibration_current=False, fire_watch=False, battery_percent=30, duration_hours=24)),
    ("portable-battery-low", 0, dict(battery_percent=29.9, emergency=True, duty_manager_signed=True)),
    ("sterile-limits", 1, dict(category="sterile", environment_temperature_c=25, battery_percent=10, duration_hours=6, transport_distance_m=300)),
    ("sterile-broken-seal", 0, dict(category="sterile", seal_intact=False, emergency=True, duty_manager_signed=True)),
    ("member-supervised-expired", 1, dict(category="sensor", endorsement_age_days=181, supervisor_active=True, supervisor_present=True, supervisor_category_endorsed=True, duration_hours=5)),
    ("trainee-duration-limit", 0, dict(category="sensor", requester_role="trainee", supervisor_active=True, supervisor_present=True, supervisor_category_endorsed=True, duration_hours=4.1)),
    ("optical-no-transport", 1, dict(category="optical", transport_distance_m=0, protective_case=False, clean_route=False, fire_watch=False, environment_temperature_c=10)),
    ("optical-dirty-route", 0, dict(category="optical", transport_distance_m=1, clean_route=False)),
    ("maintenance-last-day", 1, dict(category="thermal", maintenance_days_remaining=1, active_other_loans=2, environment_temperature_c=35)),
    ("maintenance-due", 0, dict(category="thermal", maintenance_days_remaining=0, emergency=True, duty_manager_signed=True)),
    ("sensor-indoor-weather", 1, dict(category="sensor", destination_indoor=True, weather_clear=False, battery_percent=20, active_other_loans=2)),
    ("reservation-no-waiver", 0, dict(category="sensor", reservation_conflicts=1, emergency=True, duty_manager_signed=True)),
    ("rotating-limits", 1, dict(category="rotating", transport_distance_m=100, duration_hours=2, environment_temperature_c=5, battery_percent=0)),
    ("rotating-no-guard", 0, dict(category="rotating", guard_fitted=False, emergency=True, duty_manager_signed=True)),
]


def make_case(case_id, facts, expected):
    result = policy_truth(facts)
    if result != expected:
        raise ValueError("Oracle disagrees with frozen expected label for " + case_id)
    return {"id": "cache-" + case_id, "experiment": "cache_policy",
            "state": {"rulebook": RULEBOOK, "facts": facts},
            "question": "Must this equipment dispatch be approved now under the supplied fictional rulebook?",
            "criteria": {"true": "Every applicable requirement passes, including any precisely stated exceptions.",
                         "false": "At least one applicable requirement fails."},
            "target": expected, "target_kind": "label"}


def cases():
    return [make_case(name, dict(BASE, **changes), expected) for name, expected, changes in SPECS]


def prime_case():
    return make_case("prime-unique", dict(BASE, duration_hours=3, battery_percent=77, notice_hours=6), 1)


def cache_payload(case, arm, cache_key):
    """Luna arms differ only in caching metadata, never message text/segmentation."""
    shared = shared_input(case)
    if arm == "Jev":
        return "/api/alpha/decisions", {"model": MODELS["Jev"], **shared}
    if arm not in ARMS:
        raise ValueError("Unknown cache arm")
    # Concatenating these two text blocks produces the exact shared JSON object.
    prefix = '{"state":{"rulebook":' + json.dumps(case["state"]["rulebook"], ensure_ascii=False) + ','
    suffix = '"facts":' + json.dumps(case["state"]["facts"], sort_keys=True) + '},"questions":' + json.dumps(shared["questions"], sort_keys=True) + '}'
    first = {"type": "text", "text": prefix}
    if arm == "Luna cached":
        first["prompt_cache_breakpoint"] = {"mode": "explicit"}
    payload = {"model": MODELS["Luna"],
               "messages": [{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": [first, {"type": "text", "text": suffix}]}],
               "response_format": {"type": "json_schema", "json_schema": {"name": "noul_answer", "strict": True, "schema": SCHEMA}},
               "reasoning": {"effort": "none"}, "max_tokens": 128, "stream": False,
               "provider": {"only": ["OpenAI"], "allow_fallbacks": False, "require_parameters": True},
               "prompt_cache_options": {"mode": "explicit", "ttl": "30m"},
               "prompt_cache_key": cache_key}
    return "/api/v1/chat/completions", payload


def cache_counts(record):
    details = (record.get("usage") or {}).get("prompt_tokens_details") or {}
    def count(field):
        value = details.get(field)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    return {"cached_tokens": count("cached_tokens"), "cache_write_tokens": count("cache_write_tokens")}


def percentile(values, p):
    values = sorted(values)
    if not values:
        return None
    position = (len(values) - 1) * p
    lower = int(position)
    return values[lower] + (values[min(lower + 1, len(values) - 1)] - values[lower]) * (position - lower)


def summarise(records, metadata):
    result = {"metadata": metadata, "arms": {}, "paired": {}, "cache_verification": {}}
    measured = [r for r in records if r["phase"] == "measured"]
    for arm in ARMS:
        rows = [r for r in measured if r["model_label"] == arm]
        valid = [r for r in rows if r["valid"]]
        costs = [r["cost_usd"] for r in rows if r["cost_usd"] is not None]
        latency = [r["latency_s"] for r in rows]
        cache = [cache_counts(r) for r in rows]
        hits = [x["cached_tokens"] for x in cache if x["cached_tokens"] is not None]
        writes = [x["cache_write_tokens"] for x in cache if x["cache_write_tokens"] is not None]
        prompt = [(r.get("usage") or {}).get("prompt_tokens") for r in rows]
        prompt = [x for x in prompt if isinstance(x, int)]
        result["arms"][arm] = {
            "n": len(rows), "valid": len(valid), "cost_observed_n": len(costs),
            "accuracy_including_invalid_as_wrong": sum((r["probability"] >= .5) == bool(r["target"]) for r in valid) / len(rows) if rows else None,
            "brier_valid_only": statistics.mean((r["probability"] - r["target"]) ** 2 for r in valid) if valid else None,
            "median_latency_s": statistics.median(latency) if latency else None, "p95_latency_s": percentile(latency, .95),
            "known_cost_subtotal_usd": sum(costs), "cost_missing_n": len(rows) - len(costs),
            "total_cost_usd": sum(costs) if len(costs) == len(rows) else None,
            "mean_cost_usd": statistics.mean(costs) if costs and len(costs) == len(rows) else None,
            "cache_observed_n": len(hits), "cache_hit_calls": sum(x > 0 for x in hits),
            "cache_read_tokens": sum(hits) if rows and len(hits) == len(rows) else None,
            "known_cache_read_subtotal_tokens": sum(hits),
            "cache_write_observed_n": len(writes),
            "cache_write_tokens": sum(writes) if rows and len(writes) == len(rows) else None,
            "known_cache_write_subtotal_tokens": sum(writes),
            "prompt_tokens": sum(prompt) if len(prompt) == len(rows) else None,
            "cache_read_fraction": sum(hits) / sum(prompt) if sum(prompt) and len(hits) == len(rows) and len(prompt) == len(rows) else None,
        }
    by_case = {}
    for row in measured:
        by_case.setdefault(row["case_id"], {})[row["model_label"]] = row
    for left, right in (("Luna cached", "Luna uncached"), ("Luna cached", "Jev"), ("Luna uncached", "Jev")):
        pairs = [(r[left], r[right]) for r in by_case.values() if left in r and right in r]
        usable = [(a, b) for a, b in pairs if a["valid"] and b["valid"] and a["cost_usd"] is not None and b["cost_usd"] is not None]
        result["paired"][left + " / " + right] = {
            "n": len(usable),
            "median_latency_ratio": statistics.median(a["latency_s"] / b["latency_s"] for a, b in usable) if usable else None,
            "total_cost_ratio": sum(a["cost_usd"] for a, b in usable) / sum(b["cost_usd"] for a, b in usable) if usable and sum(b["cost_usd"] for a, b in usable) else None,
            "label_agreement": statistics.mean((a["probability"] >= .5) == (b["probability"] >= .5) for a, b in usable) if usable else None,
        }
    prime = next((r for r in records if r["phase"] == "cache_prime"), None)
    cached = result["arms"]["Luna cached"]
    uncached = result["arms"]["Luna uncached"]
    prime_counts = cache_counts(prime) if prime else {}
    prime_cost = prime.get("cost_usd") if prime else None
    setup_cost = cached["total_cost_usd"] + prime_cost if cached["total_cost_usd"] is not None and prime_cost is not None else None
    evidence = cached["cache_hit_calls"] > 0
    clean_control = uncached["n"] > 0 and uncached["cache_observed_n"] == uncached["n"] and uncached["cache_read_tokens"] == 0 and uncached["cache_write_observed_n"] == uncached["n"] and uncached["cache_write_tokens"] == 0
    result["cache_verification"] = {
        "cached_arm_has_observed_hits": evidence, "uncached_control_verified": clean_control,
        "controlled_cache_comparison_verified": evidence and clean_control,
        "status": "observed cache hits and clean uncached control" if evidence and clean_control else "unverified; do not attribute differences to caching",
        "prime_cached_tokens": prime_counts.get("cached_tokens"), "prime_cache_write_tokens": prime_counts.get("cache_write_tokens"),
        "prime_cost_usd": prime_cost,
        "prime_latency_s": prime.get("latency_s") if prime else None,
        "prime_was_observed_cold": prime_counts.get("cached_tokens") == 0,
        "setup_inclusive_cached_cost_usd": setup_cost,
        "setup_inclusive_cost_per_measured_case_usd": setup_cost / cached["n"] if setup_cost is not None and cached["n"] else None,
    }
    return result


def write_report(records, out_dir, metadata):
    out_dir = Path(out_dir)
    summary = summarise(records, metadata)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    verification = summary["cache_verification"]
    def fmt(value, digits=4):
        return "unknown" if value is None else f"{value:.{digits}f}"
    lines = ["# Luna prompt-cache experiment", "", f"Run status: **{metadata['status']}**. Cache status: **{verification['status']}**.", "",
             "Twenty-four paired synthetic equipment-policy decisions use one frozen rulebook. Ground truth comes from a deterministic oracle, checked against separately frozen expected labels. All three arms receive the full rulebook and case. Luna uses the same message text, segmentation, output schema, and OpenAI provider in both arms; only cache metadata differs. Reasoning is set to none.", "",
             "| Arm | Calls | Valid | Accuracy | Brier ↓ | Median seconds ↓ | p95 seconds ↓ | Cost USD | Cache hits | Read tokens | Write tokens |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for arm, s in summary["arms"].items():
        lines.append(f"| {arm} | {s['n']} | {s['valid']} | {fmt(s['accuracy_including_invalid_as_wrong'])} | {fmt(s['brier_valid_only'])} | {fmt(s['median_latency_s'], 3)} | {fmt(s['p95_latency_s'], 3)} | {fmt(s['total_cost_usd'], 6)} | {s['cache_hit_calls']}/{s['cache_observed_n']} observed | {fmt(s['cache_read_tokens'], 0)} | {fmt(s['cache_write_tokens'], 0)} |")
    lines += ["", "## First request and repeated use", "",
              f"The distinct cache-priming request cost **${fmt(verification['prime_cost_usd'], 6)}** and took **{fmt(verification['prime_latency_s'], 3)} seconds**. Its reported cache reads were **{verification['prime_cached_tokens']} tokens** and writes were **{verification['prime_cache_write_tokens']} tokens**. It is excluded from the measured accuracy and steady-state latency table.", "",
              f"Cached-arm measured calls plus priming cost **${fmt(verification['setup_inclusive_cached_cost_usd'], 6)}**, or **${fmt(verification['setup_inclusive_cost_per_measured_case_usd'], 6)} per measured case** after allocating the setup request. A zero reported read count on the first request is the evidence required to call this cold-inclusive; that condition was **{'observed' if verification['prime_was_observed_cold'] else 'not established'}**.", "",
              "## Paired comparisons", "", "Ratios below 1 favour the arm before the slash. Ratios use the same cases where both requests were valid and cost was reported.", "",
              "| Pair | Complete pairs | Median latency ratio | Total cost ratio | Label agreement |", "|---|---:|---:|---:|---:|"]
    for name, p in summary["paired"].items():
        lines.append(f"| {name} | {p['n']} | {fmt(p['median_latency_ratio'])} | {fmt(p['total_cost_ratio'])} | {fmt(p['label_agreement'])} |")
    lines += ["", "## Interpretation", "",
              "Accuracy uses probability ≥ 0.5 as yes and counts invalid outputs as incorrect. Brier score is mean squared probability error on valid outputs; lower is better. Latency measures the full non-streaming request from this machine, including network time. Arm order is random within each case. The priming request is first, so its latency can include initial connection setup. No exact-response cache or response replay is used.", "",
              "This is a small synthetic policy-following pilot. It can reveal output, threshold, exception, cost, and latency differences for these prompts; it does not establish broad model quality. Missing bills or cache counters make their full totals unknown. Known subtotals and observation counts remain available in summary.json. A clean uncached control requires observed zero reads and zero writes on every control call. No cache benefit is established unless the cached arm reports actual reads and the control is verified. Repeated-use costs above are measured after a priming request and need not represent a stable cache-hit rate in other workloads.", "",
              f"Cache controls and usage fields follow the [OpenRouter prompt-caching documentation]({DOC_URL}). The request payloads, raw responses, exact rulebook, frozen cases, and seed are saved with this report. Reported costs are provider usage charges; no currency conversion or extrapolated list-price savings is substituted.", ""]
    if metadata.get("error"):
        lines += ["## Run stopped", "", metadata["error"], ""]
    markdown = "\n".join(lines)
    (out_dir / "report.md").write_text(markdown)
    # Readable standalone HTML with no external resources; markdown source is
    # also kept as an audit-friendly companion.
    table_rows = "".join("<tr>" + "".join("<td>" + html.escape(str(x)) + "</td>" for x in [arm, s["n"], fmt(s["accuracy_including_invalid_as_wrong"]), fmt(s["median_latency_s"], 3), fmt(s["p95_latency_s"], 3), fmt(s["total_cost_usd"], 6), fmt(s["cache_read_tokens"], 0), fmt(s["cache_write_tokens"], 0)]) + "</tr>" for arm, s in summary["arms"].items())
    document = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Luna cache experiment</title><style>body{font:16px/1.55 system-ui,sans-serif;max-width:1100px;margin:45px auto;padding:0 24px;color:#16202c;background:#f7f8fa}h1{font-size:36px;line-height:1.1}table{width:100%;border-collapse:collapse;background:white}th,td{text-align:left;padding:12px;border-bottom:1px solid #dae0e8}th{font-size:13px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.7 system-ui,sans-serif;background:white;padding:24px;border-radius:8px}.status{padding:14px;background:#e7eef8}.scroll{overflow-x:auto}a{color:#2451a0}</style><h1>Luna prompt-cache experiment</h1><p class="status">' + html.escape(verification["status"]) + '</p><div class="scroll"><table><thead><tr><th>Arm</th><th>Calls</th><th>Accuracy</th><th>Median s</th><th>p95 s</th><th>Cost USD</th><th>Read tokens</th><th>Write tokens</th></tr></thead><tbody>' + table_rows + '</tbody></table></div><h2>Protocol and full results</h2><pre>' + html.escape(markdown) + '</pre><p><a href="summary.json">Structured results</a> · <a href="responses.jsonl">Raw responses</a> · <a href="report.md">Markdown report</a></p></html>'
    (out_dir / "report.html").write_text(document)
    return summary


def run_cache(key, out_dir=None, budget=1.0, progress=None, seed=20261002):
    if not key.startswith("sk-or-"):
        raise ValueError("Expected an OpenRouter API key")
    if not math.isfinite(budget) or budget <= 0:
        raise ValueError("Budget must be positive and finite")
    out_dir = Path(out_dir or ROOT / "runs" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-cache"))
    out_dir.mkdir(parents=True, exist_ok=False)
    dataset = cases()
    frozen = "".join(json.dumps(c, sort_keys=True) + "\n" for c in dataset)
    (out_dir / "cases.jsonl").write_text(frozen)
    (out_dir / "rulebook.txt").write_text(RULEBOOK)
    cache_key = "luna-jev-policy-" + uuid.uuid4().hex
    metadata = {"experiment": "cache_policy", "created_at": datetime.now(timezone.utc).isoformat(),
                "models": {arm: MODELS["Jev" if arm == "Jev" else "Luna"] for arm in ARMS},
                "seed": seed, "cases": len(dataset), "calls_planned": 1 + len(dataset) * 3,
                "budget_usd": budget, "charged_usd": 0.0, "calls_completed": 0, "status": "running",
                "rulebook_sha256": hashlib.sha256(RULEBOOK.encode()).hexdigest(),
                "cases_sha256": hashlib.sha256(frozen.encode()).hexdigest(),
                "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "rulebook_words": len(RULEBOOK.split()), "prompt_cache_key": cache_key,
                "source": DOC_URL, "luna_reasoning": "none", "luna_provider": "OpenAI",
                "protocol": "One distinct Luna cached priming call, then 24 sequential cases with all three arms randomly ordered within case; no automatic retries; actual usage cost and cache counters; exact same Luna message text and segmentation."}
    meta_path = out_dir / "metadata.json"
    meta_path.write_text(json.dumps(metadata, indent=2))
    rng = random.Random(seed)
    rng.shuffle(dataset)
    schedule = [("cache_prime", prime_case(), "Luna cached")]
    for case in dataset:
        arms = list(ARMS)
        rng.shuffle(arms)
        schedule.extend(("measured", case, arm) for arm in arms)
    client = Client(key)
    records = []
    charged = 0.0
    try:
        with (out_dir / "responses.jsonl").open("a", buffering=1) as raw:
            for sequence, (phase, case, arm) in enumerate(schedule):
                endpoint, payload = cache_payload(case, arm, cache_key)
                # Conservative byte-as-token reservation at a rate above the
                # pilot's inspected input prices; observed charges remain final.
                reserve = (len(json.dumps(payload).encode()) + 4096) * 0.00000025 + 128 * 0.00000075
                if charged + reserve > budget:
                    raise RuntimeError("Budget guard stopped before next request")
                record = client.run(case, arm, phase, sequence, request_override=payload,
                                    endpoint_override=endpoint, base_label="Jev" if arm == "Jev" else "Luna")
                record.update(cache_counts(record))
                records.append(record)
                raw.write(json.dumps(record, ensure_ascii=False) + "\n")
                if record["cost_usd"] is not None:
                    charged += record["cost_usd"]
                metadata.update(charged_usd=charged, calls_completed=len(records))
                meta_path.write_text(json.dumps(metadata, indent=2))
                update = {"phase": phase, "completed": len(records), "planned": len(schedule), "model": arm,
                          "case": case["id"], "valid": record["valid"], "latency_s": round(record["latency_s"], 3),
                          "cached_tokens": record["cached_tokens"], "cache_write_tokens": record["cache_write_tokens"], "charged_usd": charged}
                if progress:
                    progress(update)
                else:
                    print(json.dumps(update), flush=True)
                if record["cost_usd"] is None:
                    raise RuntimeError("Missing reported cost; stopped to retain budget accounting")
                if not record["valid"]:
                    raise RuntimeError("Request failed: " + str(record["error"]))
                if charged > budget:
                    raise RuntimeError("Observed charge exceeded budget; stopped")
        metadata["status"] = "complete"
    except Exception as exc:
        metadata.update(status="stopped", error=str(exc).replace(key, "[REDACTED]"))
    finally:
        client.close()
        metadata.update(charged_usd=charged, finished_at=datetime.now(timezone.utc).isoformat())
        meta_path.write_text(json.dumps(metadata, indent=2))
        summary = write_report(records, out_dir, metadata)
    return {"run_dir": str(out_dir), "metadata": metadata, "summary": summary}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--budget", type=float, default=1.0)
    parser.add_argument("--key-stdin", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        data = cases()
        print(json.dumps({"cases": len(data), "yes": sum(c["target"] for c in data), "rulebook_words": len(RULEBOOK.split()),
                          "calls": 1 + 3 * len(data), "rulebook_sha256": hashlib.sha256(RULEBOOK.encode()).hexdigest()}, indent=2))
        return
    key = sys.stdin.readline().strip() if args.key_stdin else os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise SystemExit("Set OPENROUTER_API_KEY or use --key-stdin; never pass a key as a command argument.")
    result = run_cache(key, args.out_dir, args.budget)
    print(json.dumps(result), flush=True)
    if result["metadata"]["status"] != "complete":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
