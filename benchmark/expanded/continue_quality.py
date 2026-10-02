#!/usr/bin/env python3
"""Continue only unattempted calls under a recorded unknown-billing amendment.

Unknown charges remain unknown. A separate $0.01 budget allowance is held for
each unknown attempt; this allowance is never written as a reported charge.
"""
from collections import Counter
import concurrent.futures
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
import runner

DEFAULT_RESUME = ROOT / "runs" / "quality-20261002T093341Z"
UNKNOWN_ALLOWANCE_USD = .01


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def save(path, obj):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(obj, indent=2))
    temp.replace(path)


def charge(row):
    value = row.get("cost_usd")
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


class AllowanceBudget:
    def __init__(self, limit, records):
        if isinstance(limit, bool) or not isinstance(limit, (int, float)) or not math.isfinite(limit) or limit <= 0:
            raise ValueError("Budget must be finite and positive")
        self.limit = limit
        self.known = sum(charge(r) for r in records if charge(r) is not None)
        self.unknown = sum(charge(r) is None for r in records)
        self.pending = 0.
        self.stop_reason = None
        self.lock = threading.Lock()
        if self.known + self.unknown * UNKNOWN_ALLOWANCE_USD > limit:
            raise ValueError("Existing known charges and unknown-charge allowances already exceed budget")

    def reserve(self, estimate):
        amount = max(estimate, UNKNOWN_ALLOWANCE_USD)
        with self.lock:
            if self.stop_reason:
                raise RuntimeError(self.stop_reason)
            if not math.isfinite(amount) or amount <= 0:
                raise ValueError("Invalid request reservation")
            if self.known + self.unknown * UNKNOWN_ALLOWANCE_USD + self.pending + amount > self.limit:
                self.stop_reason = "Budget guard stopped before further requests"
                raise RuntimeError(self.stop_reason)
            self.pending += amount
        return amount

    def settle(self, reserved, row):
        with self.lock:
            self.pending = max(0., self.pending - reserved)
            value = charge(row)
            if value is None:
                self.unknown += 1
            else:
                self.known += value
            if self.known + self.unknown * UNKNOWN_ALLOWANCE_USD > self.limit:
                self.stop_reason = self.stop_reason or "Recorded charges plus unknown-charge allowances exceeded budget"

    def stop(self, reason):
        with self.lock:
            self.stop_reason = self.stop_reason or reason

    def raise_if_stopped(self):
        with self.lock:
            if self.stop_reason:
                raise RuntimeError(self.stop_reason)

    def snapshot(self):
        with self.lock:
            allowance = self.unknown * UNKNOWN_ALLOWANCE_USD
            return {"charged_usd": None if self.unknown else self.known,
                    "known_charge_subtotal_usd": self.known, "unknown_billing": self.unknown,
                    "unknown_charge_allowance_per_attempt_usd": UNKNOWN_ALLOWANCE_USD,
                    "unknown_charge_allowance_total_usd": allowance,
                    "budget_accounted_usd": self.known + allowance,
                    "budget_pending_usd": self.pending, "budget_stop_reason": self.stop_reason}


def estimate_request(case, model):
    payload = runner.payload_for(case, model)[1]
    return (len(json.dumps(payload, ensure_ascii=False).encode()) + 4096) * .0000002 + 128 * .00000075


def request_once(client, key, case, model, sequence):
    started = time.perf_counter()
    try:
        return client.run(case, model, "quality", sequence)
    except Exception as exc:
        return {"case_id": case["id"], "experiment": case["experiment"], "target": case["target"],
                "target_kind": case["target_kind"], "model_label": model, "requested_model": runner.MODELS[model],
                "phase": "quality", "sequence": sequence, "started_at": now(), "valid": False,
                "probability": None, "cost_usd": None, "usage": {}, "provider": None, "response_model": None,
                "latency_s": time.perf_counter() - started, "request_exception": True,
                "error": ("Client exception: " + str(exc)).replace(key, "[REDACTED]")}


def run_quality(key, cases_path=None, out_dir=None, budget=.80, workers=16, seed=20261002, progress=None, resume_dir=None):
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be a positive integer")
    out = Path(resume_dir or out_dir or DEFAULT_RESUME)
    cases_path = Path(cases_path or ROOT / "data" / "cases.jsonl")
    cases = {c["id"]: c for c in runner.load_cases(cases_path)}
    meta_path, raw_path, plan_path = out / "metadata.json", out / "responses.jsonl", out / "plan.json"
    initial_metadata_bytes = meta_path.read_bytes()
    initial_raw = raw_path.read_bytes()
    meta = json.loads(initial_metadata_bytes)
    plan = json.loads(plan_path.read_text())
    records = [json.loads(line) for line in initial_raw.decode().splitlines() if line.strip()]
    if meta["cases_sha256"] != sha(cases_path.read_bytes()):
        raise ValueError("Frozen case file changed; continuation refused")
    if meta["adapter_sha256"] != sha(Path(runner.__file__).read_bytes()):
        raise ValueError("Request adapter changed; continuation refused")
    if seed != meta["seed"]:
        raise ValueError("Continuation retains the original frozen seed")
    if len(plan) != len(cases) or {item["case_id"] for item in plan} != set(cases):
        raise ValueError("Saved plan does not match frozen cases")
    expected = {}
    for i, item in enumerate(plan):
        if item["pair_index"] != i or len(item["models"]) != 2 or set(item["models"]) != set(runner.MODELS):
            raise ValueError("Malformed frozen pair order")
        for j, model in enumerate(item["models"]):
            expected[(item["case_id"], model)] = (i, 2 * i + j)
    attempted = set()
    for row in records:
        identity = (row["case_id"], row["model_label"])
        if identity not in expected or identity in attempted:
            raise ValueError("Unknown or duplicate attempted case/model")
        pair_index, sequence = expected[identity]
        if row["phase"] != "quality" or row["sequence"] != sequence:
            raise ValueError("Attempt does not match frozen phase/sequence")
        if row["target"] != cases[row["case_id"]]["target"]:
            raise ValueError("Attempted target disagrees with frozen case")
        attempted.add(identity)
    guard = AllowanceBudget(budget, records)
    counts = Counter(completed=len(records), valid=sum(bool(r["valid"]) for r in records),
                     invalid=sum(not r["valid"] for r in records))
    consecutive = 0
    for row in reversed(records):
        if row["valid"]:
            break
        consecutive += 1
    remaining = [(item["pair_index"], cases[item["case_id"]],
                  [model for model in item["models"] if (item["case_id"], model) not in attempted]) for item in plan]
    remaining = [item for item in remaining if item[2]]
    original_code = out / "original-runner.py"
    if original_code.exists() and sha(original_code.read_bytes()) != meta["runner_sha256"]:
        raise ValueError("Preserved original runner does not match the initial recorded hash")
    amendment_number = len(meta.get("protocol_amendments", [])) + 1
    snapshot_path = out / f"metadata-before-amendment-{amendment_number}.json"
    if snapshot_path.exists():
        raise ValueError("Metadata amendment snapshot already exists")
    snapshot_path.write_bytes(initial_metadata_bytes)
    amendment = {"timestamp": now(), "reason": "Continue only unattempted calls after a transient HTTP 520 and unknown bill. Retain the failed attempt without retry. Separate budget allowances permit continuation without fabricating billing.",
                 "code_sha256": sha(Path(__file__).read_bytes()), "initial_code_sha256": meta["runner_sha256"],
                 "initial_metadata_sha256": sha(initial_metadata_bytes), "initial_metadata_snapshot": snapshot_path.name,
                 "initial_raw_prefix_sha256": sha(initial_raw), "initial_raw_prefix_bytes": len(initial_raw),
                 "initial_attempts": len(records), "unattempted_calls": len(expected) - len(attempted),
                 "frozen_plan_sha256": sha(plan_path.read_bytes()), "workers": workers,
                 "unknown_billing_policy": "$0.01 accounting allowance per unknown attempt; reported cost remains null. In-flight reservations are the larger of the original request estimate and $0.01.",
                 "failure_circuit": "Five consecutive invalid completions, or at least 5% invalid among global completed attempts once 100 have completed. Completion order is append order in responses.jsonl.",
                 "retry_policy": "No retries, repairs, or replays; every original (case_id,model) is attempted at most once."}
    meta.setdefault("protocol_amendments", []).append(amendment)
    meta.pop("error", None)
    meta.update(status="running", continuation_started_at=now(), budget_usd=budget,
                calls_completed=len(records), valid=counts["valid"], invalid=counts["invalid"],
                consecutive_invalid=consecutive, **guard.snapshot())
    save(meta_path, meta)
    lock = threading.Lock()
    local = threading.local()
    clients = []

    def check_circuit():
        if consecutive >= 5:
            guard.stop("Five consecutive invalid completions; stopped further calls")
        if counts["completed"] >= 100 and counts["invalid"] * 20 >= counts["completed"]:
            guard.stop("At least 5% invalid after 100 completed attempts; stopped further calls")

    check_circuit()

    def emit(row, reserved):
        nonlocal consecutive
        # Settlement, completion counters, and append order share one lock so
        # the circuit's definition is reproducible from the final JSONL log.
        with lock:
            guard.settle(reserved, row)
            records.append(row)
            counts["completed"] += 1
            counts["valid"] += int(bool(row["valid"]))
            counts["invalid"] += int(not row["valid"])
            consecutive = 0 if row["valid"] else consecutive + 1
            with raw_path.open("a") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            check_circuit()
            meta.update(calls_completed=counts["completed"], valid=counts["valid"], invalid=counts["invalid"],
                        consecutive_invalid=consecutive, **guard.snapshot())
            save(meta_path, meta)
            if progress and (counts["completed"] % 100 == 0 or not row["valid"]):
                progress({"kind": "quality_continuation", "completed": counts["completed"], "planned": len(expected),
                          "valid": counts["valid"], "invalid": counts["invalid"], **guard.snapshot(), "last_error": row.get("error")})

    def pair(item):
        pair_index, case, models = item
        if not hasattr(local, "client"):
            local.client = runner.Client(key)
            with lock:
                clients.append(local.client)
        for model in models:
            reserved = guard.reserve(estimate_request(case, model))
            _, sequence = expected[(case["id"], model)]
            row = request_once(local.client, key, case, model, sequence)
            row.update(pair_index=pair_index, concurrency_pairs=workers, measurement_role="bulk_quality", repeat=0,
                       protocol_amendment=amendment_number)
            emit(row, reserved)
            guard.raise_if_stopped()
        return True

    try:
        guard.raise_if_stopped()
        if initial_raw and not initial_raw.endswith(b"\n"):
            with raw_path.open("ab") as handle:
                handle.write(b"\n")
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            iterator = iter(remaining)
            futures = {pool.submit(pair, next(iterator)) for _ in range(min(workers, len(remaining)))}
            while futures:
                done, futures = concurrent.futures.wait(futures, return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    future.result()
                for _ in done:
                    try:
                        item = next(iterator)
                    except StopIteration:
                        break
                    futures.add(pool.submit(pair, item))
        guard.raise_if_stopped()
        if len(records) != len(expected):
            raise RuntimeError("Continuation ended without every planned attempt")
        meta["status"] = "complete"
    except Exception as exc:
        meta.update(status="stopped", error=str(exc).replace(key, "[REDACTED]"))
    finally:
        for client in clients:
            client.close()
        final_prefix = raw_path.read_bytes()[:len(initial_raw)]
        if final_prefix != initial_raw:
            meta.update(status="stopped", error="Original response prefix changed")
        meta.update(finished_at=now(), calls_completed=len(records), valid=counts["valid"], invalid=counts["invalid"],
                    consecutive_invalid=consecutive, **guard.snapshot())
        save(meta_path, meta)
    return {"run_dir": str(out), "metadata": meta}
