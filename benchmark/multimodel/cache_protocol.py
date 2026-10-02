#!/usr/bin/env python3
"""Serial, paired cache protocol using the frozen 96 equipment-policy cases.

This module has no HTTP client or credential access. Inject an adapter with:
  build_request(case, spec, cache_mode, cache_key) -> {endpoint, payload}
  reserve_usd(request, spec) -> conservative positive dollar reservation
  run(case, spec, arm_label, phase, sequence, endpoint, payload) -> raw row

Specs have label, id, provider, prices and cache_control ('explicit' or
'unsupported'). Explicit means verified OpenRouter-style on/off control; an
unsupported model has one baseline arm whose cache state remains unknown.
Adapters own connection lifecycle. The protocol never retries a request.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import statistics
import time
import uuid

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parent / "expanded/runs/cache-20261002T095722Z"
SOURCE_CASES_SHA256 = "73e400bef4a7025d31ab77523f6dcafe9ae46ee2100c1b8e7d4bd444d3a88e0c"
SOURCE_RULEBOOK_SHA256 = "01006795edc5f9ee13a1e4efc56fc8e19516d74f40cddbce2e72d19b00f0d43b"
UNKNOWN_BILL_ALLOWANCE_USD = .01
SCHEMA_VERSION = 1


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    tmp.replace(path)


def nonnegative_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def load_source(source_dir=SOURCE):
    """Read the immutable cases and recover the four distinct original primes."""
    source_dir = Path(source_dir)
    cases_bytes = (source_dir / "cases.jsonl").read_bytes()
    rulebook_bytes = (source_dir / "rulebook.txt").read_bytes()
    if digest(cases_bytes) != SOURCE_CASES_SHA256 or digest(rulebook_bytes) != SOURCE_RULEBOOK_SHA256:
        raise ValueError("The cache cases or rulebook differ from the frozen reference")
    cases = [json.loads(line) for line in cases_bytes.splitlines() if line.strip()]
    rulebook = rulebook_bytes.decode()
    if len(cases) != 96 or len({c["id"] for c in cases}) != 96:
        raise ValueError("Expected 96 unique frozen cases")
    pairs = defaultdict(list)
    for case in cases:
        if case["state"]["rulebook"] != rulebook or case["target"] not in (0, 1):
            raise ValueError("Unexpected reference rulebook or target")
        pairs[(case["block"], case["pair_id"])].append(case)
    if Counter(c["block"] for c in cases) != {i: 24 for i in range(4)} or len(pairs) != 48:
        raise ValueError("Expected four blocks and 48 counterfactual pairs")
    for pair in pairs.values():
        if len(pair) != 2 or {c["target"] for c in pair} != {0, 1}:
            raise ValueError("Each counterfactual pair must contain yes and no")
        left, right = (c["state"]["facts"] for c in pair)
        if set(left) != set(right) or sum(left[k] != right[k] for k in left) != 1:
            raise ValueError("A frozen counterfactual pair must change exactly one fact")
    raw_bytes = (source_dir / "responses.jsonl").read_bytes()
    primes = {}
    for line in raw_bytes.splitlines():
        row = json.loads(line)
        if row.get("phase") != "cache_prime":
            continue
        block = row["block"]
        if block in primes:
            raise ValueError("Duplicate reference prime")
        content = row["request"]["messages"][-1]["content"]
        shared = json.loads("".join(c["text"] for c in content))
        question = shared["questions"]["answer"]
        primes[block] = {"id": f"cache-prime-{block}", "block": block,
                         "experiment": "cache_policy", "target": row["target"], "target_kind": "label",
                         "state": {"rulebook": rulebook, "facts": shared["state"]["facts"]},
                         "question": question["instructions"], "criteria": question["criteria"]}
    if set(primes) != set(range(4)):
        raise ValueError("Expected four separate reference priming cases")
    case_facts = {canonical(c["state"]["facts"]) for c in cases}
    if any(canonical(c["state"]["facts"]) in case_facts for c in primes.values()):
        raise ValueError("A prime must not repeat a measured decision")
    return cases, primes, rulebook, {"cases_sha256": digest(cases_bytes),
                                    "rulebook_sha256": digest(rulebook_bytes),
                                    "prime_source_responses_sha256": digest(raw_bytes)}


def with_prefix(case, identifier):
    result = deepcopy(case)
    result["state"]["rulebook"] = (f"Administrative batch identifier: {identifier}. "
                                     "This identifier changes no rule or case fact.\n\n" + case["state"]["rulebook"])
    return result


def shared_input(case):
    return {"state": case["state"], "questions": {"answer": {
        "type": "noul", "instructions": case["question"], "criteria": case["criteria"]}}}


def text_blocks(case):
    """The exact frozen two-block serialization, available to injected adapters."""
    prefix = '{"state":{"rulebook":' + json.dumps(case["state"]["rulebook"], ensure_ascii=False) + ','
    suffix = '"facts":' + json.dumps(case["state"]["facts"], sort_keys=True) + '},"questions":' + json.dumps(shared_input(case)["questions"], sort_keys=True) + '}'
    return [{"type": "text", "text": prefix}, {"type": "text", "text": suffix}]


def validate_specs(model_specs):
    specs = deepcopy(list(model_specs))
    if not specs or len({s["label"] for s in specs}) != len(specs):
        raise ValueError("Supply unique, nonempty model labels")
    for spec in specs:
        for field in ("label", "id", "provider", "prices"):
            if field not in spec or spec[field] is None:
                raise ValueError(f"Model spec is missing {field}")
        if spec.get("cache_control") not in ("explicit", "unsupported"):
            raise ValueError("Cache capability must be verified explicit or unsupported; unknown is not runnable")
    return specs


def model_arms(spec):
    label = spec["label"]
    return [(label + " uncached", "uncached"), (label + " cached", "cached")] if spec["cache_control"] == "explicit" else [(label, "baseline")]


def strip_cache_metadata(value):
    if isinstance(value, dict):
        return {k: strip_cache_metadata(v) for k, v in value.items()
                if k not in ("prompt_cache_breakpoint", "prompt_cache_options", "prompt_cache_key", "cache_control")}
    if isinstance(value, list):
        return [strip_cache_metadata(v) for v in value]
    return value


def validate_request(case, spec, mode, request):
    if set(request) != {"endpoint", "payload"} or not isinstance(request["payload"], dict):
        raise ValueError("build_request must return only endpoint and payload")
    payload = request["payload"]
    if payload.get("model") != spec["id"]:
        raise ValueError("Request model does not match the frozen spec")
    if "messages" not in payload:
        if mode != "baseline" or {k: payload.get(k) for k in ("state", "questions")} != shared_input(case):
            raise ValueError("Decision request does not preserve the shared input")
        return
    users = [m for m in payload["messages"] if m["role"] == "user"]
    if len(users) != 1 or strip_cache_metadata(users[0]["content"]) != text_blocks(case):
        raise ValueError("Chat request must preserve the exact same two text blocks")
    if mode == "baseline":
        if strip_cache_metadata(payload) != payload:
            raise ValueError("Unsupported baseline must not claim an on/off cache treatment")
        return
    if payload.get("prompt_cache_options", {}).get("mode") != "explicit":
        raise ValueError("Controlled on/off cache arms require explicit mode")
    first, second = users[0]["content"]
    if second != text_blocks(case)[1]:
        raise ValueError("Cache breakpoint must end the reusable prefix, not the case")
    expected_first = dict(text_blocks(case)[0])
    if mode == "cached":
        expected_first["prompt_cache_breakpoint"] = {"mode": "explicit"}
    if first != expected_first:
        raise ValueError("Cached/off controls must differ only by the prefix breakpoint")
    if any("cache_control" in canonical(m) for m in payload["messages"]):
        raise ValueError("Do not mix provider cache controls with explicit on/off mode")


def create_plan(adapter, specs, cases, primes, source_hashes, seed, identifiers=None):
    """Build and validate every request before any adapter.run call is possible."""
    identifiers = identifiers or [uuid.uuid4().hex for _ in range(4)]
    if len(identifiers) != 4 or len(set(identifiers)) != 4:
        raise ValueError("Each of four blocks needs its own fresh prefix identifier")
    plan = {"schema_version": SCHEMA_VERSION, "models": specs, "seed": seed,
            "source_hashes": source_hashes, "block_identifiers": identifiers, "attempts": []}
    prepared = {}
    spec_lookup = {s["label"]: s for s in specs}
    for block in range(4):
        rng = random.Random(seed + 10000 + block)
        cached_models = [s["label"] for s in specs if s["cache_control"] == "explicit"]
        rng.shuffle(cached_models)
        schedule = [("cache_prime", primes[block], label, label + " cached", "cached") for label in cached_models]
        selected = [c for c in cases if c["block"] == block]
        rng.shuffle(selected)
        for case in selected:
            arms = [(s["label"], arm, mode) for s in specs for arm, mode in model_arms(s)]
            rng.shuffle(arms)
            schedule.extend(("measured", case, label, arm, mode) for label, arm, mode in arms)
        pair_requests = {}
        for phase, original, label, arm, mode in schedule:
            case = with_prefix(original, identifiers[block])
            spec = spec_lookup[label]
            cache_key = "model-comparison-cache-" + identifiers[block] + "-" + digest(label.encode())[:10]
            request = adapter.build_request(case, spec, mode, cache_key)
            validate_request(case, spec, mode, request)
            if phase == "measured" and mode != "baseline":
                pair_requests.setdefault((case["id"], label), {})[mode] = request
            reserve = adapter.reserve_usd(request, spec)
            if not nonnegative_number(reserve) or reserve <= 0:
                raise ValueError("Adapter reservation must be finite and positive")
            sequence = len(plan["attempts"])
            entry = {"sequence": sequence, "block": block, "phase": phase, "case_id": case["id"],
                     "base_model_label": label, "model_label": arm, "cache_mode": mode,
                     "pair_id": case.get("pair_id"), "target": case["target"], "cache_key": cache_key,
                     "prefix_identifier": identifiers[block], "prefix_sha256": digest(case["state"]["rulebook"].encode()),
                     "request_sha256": digest(canonical(request).encode()),
                     "reserve_usd": max(UNKNOWN_BILL_ALLOWANCE_USD, reserve)}
            plan["attempts"].append(entry)
            prepared[sequence] = (case, spec, request)
        for pair in pair_requests.values():
            if set(pair) != {"cached", "uncached"} or strip_cache_metadata(pair["cached"]) != strip_cache_metadata(pair["uncached"]):
                raise ValueError("Matched cached/off payloads differ beyond cache metadata")
    return plan, prepared


def cache_counts(record):
    details = (record.get("usage") or {}).get("prompt_tokens_details") or {}
    return {key: value if isinstance(value := details.get(key), int) and not isinstance(value, bool) and value >= 0 else None
            for key in ("cached_tokens", "cache_write_tokens")}


def billed_cost(record):
    value = record.get("cost_usd")
    return value if nonnegative_number(value) else None


def cost_summary(rows):
    values = [billed_cost(r) for r in rows]
    missing = sum(x is None for x in values)
    subtotal = sum(x for x in values if x is not None)
    return {"total_usd": subtotal if not missing else None, "known_subtotal_usd": subtotal,
            "missing_n": missing, "unknown_bill_allowance_usd": UNKNOWN_BILL_ALLOWANCE_USD * missing,
            "budget_accounted_usd": subtotal + UNKNOWN_BILL_ALLOWANCE_USD * missing}


def valid_probability(row):
    p = row.get("probability")
    return row.get("valid") is True and nonnegative_number(p) and p <= 1


def describe(rows):
    valid = [r for r in rows if valid_probability(r)]
    latency = [r["latency_s"] for r in rows if nonnegative_number(r.get("latency_s")) and r["latency_s"] > 0]
    cost = cost_summary(rows)
    counts = [cache_counts(r) for r in rows]
    reads = [c["cached_tokens"] for c in counts if c["cached_tokens"] is not None]
    writes = [c["cache_write_tokens"] for c in counts if c["cache_write_tokens"] is not None]
    return {"n": len(rows), "valid_n": len(valid), "invalid_n": len(rows) - len(valid), "cost": cost,
            "cost_per_1000_usd": cost["total_usd"] / len(rows) * 1000 if rows and cost["total_usd"] is not None else None,
            "accuracy_including_invalid_as_wrong": sum((r["probability"] >= .5) == bool(r["target"]) for r in valid) / len(rows) if rows else None,
            "brier_valid_only": statistics.mean((r["probability"] - r["target"]) ** 2 for r in valid) if valid else None,
            "median_latency_s": statistics.median(latency) if latency else None,
            "latency_observed_n": len(latency), "cache_observed_n": len(reads),
            "cache_hit_calls": sum(v > 0 for v in reads),
            "cache_read_tokens": sum(reads) if rows and len(reads) == len(rows) else None,
            "cache_write_observed_n": len(writes),
            "cache_write_tokens": sum(writes) if rows and len(writes) == len(rows) else None,
            "unique_cache_read_sizes": sorted(set(reads))}


def summarise(records, metadata):
    measured = [r for r in records if r["phase"] == "measured"]
    primes = [r for r in records if r["phase"] == "cache_prime"]
    specs = metadata["models"]
    labels = [arm for s in specs for arm, _ in model_arms(s)]
    result = {"metadata": metadata, "arms": {a: describe([r for r in measured if r["model_label"] == a]) for a in labels},
              "blocks": [], "cache_verification": {}, "all_attempt_cost": cost_summary(records)}
    unresolved = metadata.get("unresolved_dispatch_n", 0)
    if unresolved:
        result["unresolved_dispatch_n"] = unresolved
        bill = result["all_attempt_cost"]
        bill.update(total_usd=None, missing_n=bill["missing_n"] + unresolved,
                    unknown_bill_allowance_usd=bill["unknown_bill_allowance_usd"] + unresolved * UNKNOWN_BILL_ALLOWANCE_USD,
                    budget_accounted_usd=bill["budget_accounted_usd"] + unresolved * UNKNOWN_BILL_ALLOWANCE_USD)
    for block in range(4):
        result["blocks"].append({"block": block,
                                  "arms": {a: describe([r for r in measured if r["block"] == block and r["model_label"] == a]) for a in labels}})
    for spec in specs:
        label = spec["label"]
        if spec["cache_control"] == "unsupported":
            result["cache_verification"][label] = {"status": "unsupported", "cache_state": "uncontrolled",
                "reason": spec.get("cache_reason", "No verified matched explicit on/off control."),
                "observed_counters": {k: result["arms"][label][k] for k in ("cache_observed_n", "cache_hit_calls", "cache_read_tokens", "cache_write_observed_n", "cache_write_tokens")}}
            continue
        blocks = []
        for block in range(4):
            priming = [r for r in primes if r["block"] == block and r["base_model_label"] == label]
            on = [r for r in measured if r["block"] == block and r["model_label"] == label + " cached"]
            off = [r for r in measured if r["block"] == block and r["model_label"] == label + " uncached"]
            prime = priming[0] if len(priming) == 1 else None
            pc = cache_counts(prime) if prime else {"cached_tokens": None, "cache_write_tokens": None}
            cold = bool(prime and valid_probability(prime) and pc["cached_tokens"] == 0 and pc["cache_write_tokens"] is not None and pc["cache_write_tokens"] > 0)
            on_counts = [cache_counts(r) for r in on]
            hits = len(on) == 24 and all(c["cached_tokens"] is not None and c["cached_tokens"] > 0 and c["cache_write_tokens"] == 0 for c in on_counts)
            clean_off = len(off) == 24 and all(cache_counts(r) == {"cached_tokens": 0, "cache_write_tokens": 0} for r in off)
            stable = bool(on) and len({c["cached_tokens"] for c in on_counts}) == 1 and all(c["cached_tokens"] is not None for c in on_counts)
            matched = cold and hits and all(c["cached_tokens"] == pc["cache_write_tokens"] for c in on_counts)
            blocks.append({"block": block, "cold_prime_verified": cold, "all_24_cached_requests_hit": hits,
                           "uncached_control_verified": clean_off, "stable_cached_prefix_token_count": stable,
                           "read_size_matches_prime_write": matched,
                           "prime_cost_usd": billed_cost(prime) if prime else None,
                           "prime_cache_write_tokens": pc["cache_write_tokens"],
                           "fully_verified": cold and hits and clean_off and stable and matched})
        cached = [r for r in measured if r["model_label"] == label + " cached"]
        these_primes = [r for r in primes if r["base_model_label"] == label]
        setup = cost_summary(cached + these_primes)
        complete = len(cached) == 96 and len(these_primes) == 4
        result["cache_verification"][label] = {"status": "verified" if all(b["fully_verified"] for b in blocks) else "unverified",
            "blocks": blocks, "prime_count": len(these_primes), "priming_cost": cost_summary(these_primes),
            "setup_inclusive_cached_cost_usd": setup["total_usd"] if complete else None,
            "setup_inclusive_cached_cost_per_1000_usd": setup["total_usd"] / 96 * 1000 if complete and setup["total_usd"] is not None else None}
    result["definitions"] = {"accuracy": "Probability >=0.5 means yes. Every invalid attempt counts as incorrect.",
        "cost_per_1000": "Observed mean billed cost projected to 1,000 decisions at the measured request mix.",
        "setup_inclusive": "Four distinct prime charges plus measured cached charges, divided across 96 measured decisions.",
        "baseline": "Unsupported cache control means one baseline arm with uncontrolled cache state. Observed counters are reported; it is not an uncached treatment.",
        "blocks": "Four sequential blocks from one session, each with the original 24 cases and a new common prefix nonce. They are not independent days.",
        "billing": "Missing charges remain unknown. Each unknown attempt consumes a separate $0.01 allowance for the local budget guard; that allowance is not a reported price."}
    return result


def circuit_reason(records):
    if len(records) >= 5 and all(not valid_probability(r) for r in records[-5:]):
        return "Five consecutive invalid responses"
    if len(records) >= 100 and sum(not valid_probability(r) for r in records) / len(records) >= .05:
        return "Invalid response fraction reached 5% after at least 100 attempts"
    return None


def run_cache_protocol(adapter, out_dir, model_specs, budget_usd, *, source_dir=SOURCE,
                       seed=20261002, progress=None, resume=False):
    """Run one serial session; the caller owns the injected adapter connection.

    Resume is allowed only at complete-block boundaries and never replays any
    attempted row. A missing bill remains null but consumes a $0.01 allowance.
    The local guard cannot enforce a remote account-level spend cap.
    """
    if not nonnegative_number(budget_usd) or budget_usd <= 0:
        raise ValueError("Budget must be finite and positive")
    specs = validate_specs(model_specs)
    cases, primes, rulebook, source_hashes = load_source(source_dir)
    out_dir = Path(out_dir)
    source_hash = digest(Path(__file__).read_bytes())
    records = []
    if resume:
        metadata = json.loads((out_dir / "metadata.json").read_text())
        saved_plan = json.loads((out_dir / "plan.json").read_text())
        if metadata["protocol_sha256"] != source_hash or metadata["models"] != specs:
            raise ValueError("Cannot resume with changed protocol source or model specs")
        plan, prepared = create_plan(adapter, specs, cases, primes, source_hashes, seed, saved_plan["block_identifiers"])
        if plan != saved_plan:
            raise ValueError("Cannot resume with changed cases, seed, or request adapter")
        raw_path = out_dir / "responses.jsonl"
        records = [json.loads(line) for line in raw_path.read_text().splitlines() if line.strip()]
        journal = [json.loads(line) for line in (out_dir / "dispatches.jsonl").read_text().splitlines() if line.strip()]
        if len(journal) != len(records) or any(d["sequence"] != i or d["request_sha256"] != plan["attempts"][i]["request_sha256"] for i, d in enumerate(journal)):
            raise ValueError("A durable dispatch lacks a saved response or has changed; its unknown outcome must not be replayed")
        if any(r["sequence"] != i or r["request_sha256"] != plan["attempts"][i]["request_sha256"] or
               any(r.get(k) != plan["attempts"][i][k] for k in ("block", "phase", "case_id", "model_label")) for i, r in enumerate(records)):
            raise ValueError("Saved responses are not the unmodified prefix of the plan")
        if records and len(records) < len(plan["attempts"]) and records[-1]["block"] == plan["attempts"][len(records)]["block"]:
            raise ValueError("Resume is supported only at complete-block boundaries; partial blocks are never replayed")
        metadata["resume_count"] = metadata.get("resume_count", 0) + 1
    else:
        plan, prepared = create_plan(adapter, specs, cases, primes, source_hashes, seed)
        out_dir.mkdir(parents=True, exist_ok=False)
        (out_dir / "cases.jsonl").write_bytes((Path(source_dir) / "cases.jsonl").read_bytes())
        (out_dir / "rulebook.txt").write_text(rulebook)
        (out_dir / "primes.json").write_text(json.dumps(primes, indent=2))
        atomic_json(out_dir / "plan.json", plan)
        metadata = {"schema_version": SCHEMA_VERSION, "experiment": "multimodel_cache_policy", "created_at": now(),
                    "models": specs, "seed": seed, "blocks": 4, "cases": 96, "counterfactual_pairs": 48,
                    "calls_planned": len(plan["attempts"]), "source_hashes": source_hashes,
                    "source_dir": str(Path(source_dir).resolve()), "protocol_sha256": source_hash,
                    "plan_sha256": digest((out_dir / "plan.json").read_bytes()),
                    "automatic_retries": 0, "unknown_bill_allowance_usd": UNKNOWN_BILL_ALLOWANCE_USD,
                    "protocol": "Four sequential blocks. Fresh shared prefix nonce per block; one separate prime per controlled model per block. Frozen case order and all-model arm order randomized within each block. One serial injected adapter session. Unsupported cache models receive one baseline arm only.",
                    "resume_policy": "Complete-block boundaries only; no repeated attempts.",
                    "billing_guard": "Reserve max(adapter conservative estimate, $0.01) before each call. Known charges count at actual recorded values; each missing bill uses a $0.01 guard allowance while its reported price remains null. This local guard is not a remote account spending limit."}
    if cost_summary(records)["budget_accounted_usd"] > budget_usd:
        raise ValueError("Budget is below the already accounted attempts")
    metadata.update(status="running", budget_usd=budget_usd, calls_completed=len(records))
    metadata.pop("error", None)
    atomic_json(out_dir / "metadata.json", metadata)
    try:
        with (out_dir / "responses.jsonl").open("a", buffering=1) as raw, (out_dir / "dispatches.jsonl").open("a", buffering=1) as journal:
            for entry in plan["attempts"][len(records):]:
                reason = circuit_reason(records)
                if reason:
                    raise RuntimeError(reason)
                if cost_summary(records)["budget_accounted_usd"] + entry["reserve_usd"] > budget_usd:
                    raise RuntimeError("Budget guard stopped before the next request")
                case, spec, request = prepared[entry["sequence"]]
                journal.write(json.dumps({"sequence": entry["sequence"], "request_sha256": entry["request_sha256"],
                                          "case_id": entry["case_id"], "model_label": entry["model_label"],
                                          "dispatched_at": now()}) + "\n")
                journal.flush()
                os.fsync(journal.fileno())
                started = time.perf_counter()
                try:
                    row = adapter.run(case=deepcopy(case), spec=deepcopy(spec), arm_label=entry["model_label"],
                        phase=entry["phase"], sequence=entry["sequence"], endpoint=request["endpoint"], payload=deepcopy(request["payload"]))
                    if not isinstance(row, dict):
                        raise TypeError("Adapter did not return a record")
                    row = deepcopy(row)
                except Exception as exc:
                    row = {"valid": False, "probability": None, "cost_usd": None, "usage": {},
                           "latency_s": time.perf_counter() - started,
                           "error": "Injected adapter raised " + type(exc).__name__ + "; request retained without retry"}
                row.update(entry)
                row.update(experiment="cache_policy", target_kind="label", requested_model=spec["id"],
                           endpoint=request["endpoint"], request=request["payload"], **cache_counts(row))
                if billed_cost(row) is None:
                    row["cost_usd"] = None
                if not valid_probability(row):
                    row["valid"] = False
                if row.get("provider") != spec.get("provider_name", spec["provider"]):
                    row.update(valid=False, error="Returned provider differs from the pinned model spec")
                if not row["valid"]:
                    row["probability"] = None
                records.append(row)
                raw.write(json.dumps(row, ensure_ascii=False) + "\n")
                raw.flush()
                os.fsync(raw.fileno())
                cost = cost_summary(records)
                completed_blocks = [b for b in range(4) if sum(r["block"] == b for r in records) == sum(e["block"] == b for e in plan["attempts"])]
                metadata.update(calls_completed=len(records), charged_usd=cost["total_usd"],
                    known_charge_subtotal_usd=cost["known_subtotal_usd"], cost_missing_n=cost["missing_n"],
                    unknown_bill_allowance_total_usd=cost["unknown_bill_allowance_usd"],
                    budget_accounted_usd=cost["budget_accounted_usd"], completed_blocks=completed_blocks)
                atomic_json(out_dir / "metadata.json", metadata)
                atomic_json(out_dir / "checkpoint.json", {"calls_completed": len(records), "completed_blocks": completed_blocks,
                            "last_sequence": entry["sequence"], "phase": entry["phase"], "case_id": entry["case_id"], "model_label": entry["model_label"]})
                if progress:
                    progress({"completed": len(records), "planned": len(plan["attempts"]), "block": entry["block"],
                              "phase": entry["phase"], "model": entry["model_label"], "valid": row["valid"],
                              "known_charge_subtotal_usd": cost["known_subtotal_usd"], "cost_missing_n": cost["missing_n"],
                              "budget_accounted_usd": cost["budget_accounted_usd"]})
                if cost["budget_accounted_usd"] > budget_usd:
                    raise RuntimeError("Observed charges exceeded the local budget; stopped")
        metadata["status"] = "complete"
    except Exception as exc:
        metadata.update(status="stopped", error=str(exc))
    finally:
        cost = cost_summary(records)
        dispatched_n = len((out_dir / "dispatches.jsonl").read_text().splitlines())
        unresolved = dispatched_n - len(records)
        if unresolved:
            metadata.update(status="stopped", error="Dispatched request has no saved outcome; it must not be replayed")
        metadata.update(finished_at=now(), calls_completed=len(records), calls_dispatched=dispatched_n,
            unresolved_dispatch_n=unresolved, charged_usd=None if unresolved else cost["total_usd"],
            known_charge_subtotal_usd=cost["known_subtotal_usd"], cost_missing_n=cost["missing_n"] + unresolved,
            unknown_bill_allowance_total_usd=cost["unknown_bill_allowance_usd"] + unresolved * UNKNOWN_BILL_ALLOWANCE_USD,
            budget_accounted_usd=cost["budget_accounted_usd"] + unresolved * UNKNOWN_BILL_ALLOWANCE_USD)
        atomic_json(out_dir / "metadata.json", metadata)
        result = summarise(records, metadata)
        atomic_json(out_dir / "summary.json", result)
    return {"run_dir": str(out_dir), "metadata": metadata, "summary": result}
