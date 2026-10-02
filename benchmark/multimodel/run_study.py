#!/usr/bin/env python3
"""Frozen multimodel quality and serial timing protocols. No automatic retries.

The adapter owns HTTP, output validation and provider identity checks. Every
request is prepared and hashed before the first outcome. A durable dispatch
journal prevents quality resume from repeating an attempted request.
"""
from collections import Counter, defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import threading
import time

ROOT = Path(__file__).resolve().parent
DATA = ROOT.parent / "expanded/data"
CASES_SHA256 = "e271790b2ee38a1c231e312fa15ece2a56c95f6bf705294f1637d43b2edcf256"
TIMING_SHA256 = "aa034a8fd9eedcf2ef2614fd3861d81af9d500eaed94629fb9f72c7d1df504f8"
UNKNOWN_BILL_ALLOWANCE_USD = .01
PHASE_LOCK = ROOT / "runs/.study-phase.lock"
_IN_PROCESS_LOCK = threading.Lock()
_base_spec = importlib.util.spec_from_file_location("multimodel_frozen_runner", ROOT.parent / "runner.py")
base = importlib.util.module_from_spec(_base_spec)
_base_spec.loader.exec_module(base)


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def sha(path):
    return digest(Path(path).read_bytes())


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def append(path, row):
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(canonical(row) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def read_rows(path):
    path = Path(path)
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def nonnegative(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def adapter_class():
    spec = importlib.util.spec_from_file_location("multimodel_runtime_adapter", ROOT / "adapter.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Adapter


def validate_specs(model_specs):
    specs = deepcopy(list(model_specs))
    if not specs or len({s.get("label") for s in specs}) != len(specs):
        raise ValueError("Supply unique model labels")
    for spec in specs:
        for field in ("label", "id", "provider", "prices", "cache_control"):
            if field not in spec or spec[field] is None or spec[field] == "":
                raise ValueError("Model specification is missing " + field)
        if "reasoning" not in spec:
            raise ValueError("Model specification is missing reasoning policy")
    canonical(specs)
    return specs


def load_frozen_sources():
    """Verify both source files from the same bytes used to construct cases."""
    result = []
    for name, expected, count in (("cases.jsonl", CASES_SHA256, 4785),
                                  ("timing-cases.jsonl", TIMING_SHA256, 60)):
        raw = (DATA / name).read_bytes()
        if digest(raw) != expected:
            raise ValueError("Frozen source hash mismatch: " + name)
        cases = [json.loads(line) for line in raw.splitlines() if line.strip()]
        if len(cases) != count or len({c["id"] for c in cases}) != count:
            raise ValueError("Frozen source count/identity mismatch: " + name)
        result.append(cases)
    full = {c["id"]: c for c in result[0]}
    if any(full.get(c["id"]) != c for c in result[1]):
        raise ValueError("Timing cases differ from their frozen quality cases")
    if Counter(c["experiment"] for c in result[1]) != {x: 12 for x in ("policy", "probability", "boolq", "rte", "wic")}:
        raise ValueError("Timing task distribution changed")
    return (*result, {"cases_sha256": CASES_SHA256, "timing_cases_sha256": TIMING_SHA256})


@contextmanager
def isolated_phase(phase):
    """Do not allow another quality/timing run in this process or workspace."""
    if not _IN_PROCESS_LOCK.acquire(blocking=False):
        raise RuntimeError("Another quality/timing phase is active")
    stream = None
    try:
        PHASE_LOCK.parent.mkdir(parents=True, exist_ok=True)
        stream = PHASE_LOCK.open("a+")
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another quality/timing phase is active in this workspace") from exc
        stream.seek(0)
        stream.truncate()
        stream.write(canonical({"pid": os.getpid(), "phase": phase, "started_at": now()}))
        stream.flush()
        yield
    finally:
        if stream is not None:
            stream.close()
        _IN_PROCESS_LOCK.release()


class Budget:
    """Concurrent reservations, actual known bills, and explicit unknown guard."""
    def __init__(self, limit):
        if not nonnegative(limit) or limit <= 0:
            raise ValueError("Budget must be finite and positive")
        self.limit = limit
        self.spent = self.pending = self.unknown_guard = 0.0
        self.unknown = 0
        self.stop_reason = None
        self.lock = threading.Lock()

    def reserve(self, amount):
        if not nonnegative(amount) or amount <= 0:
            raise ValueError("Reservation must be finite and positive")
        with self.lock:
            if self.stop_reason:
                return False
            if self.spent + self.unknown_guard + self.pending + amount > self.limit + 1e-12:
                self.stop_reason = "Budget guard stopped before further calls"
                return False
            self.pending += amount
            return True

    def settle(self, amount, row, reserved=True):
        with self.lock:
            if reserved:
                self.pending = max(0.0, self.pending - amount)
            charge = row.get("cost_usd")
            if nonnegative(charge):
                self.spent += charge
            else:
                self.unknown += 1
                self.unknown_guard += max(UNKNOWN_BILL_ALLOWANCE_USD, amount)
            if self.spent + self.unknown_guard + self.pending > self.limit + 1e-12:
                self.stop_reason = self.stop_reason or "Observed/unknown charges exhausted the local budget"

    def stop(self, reason):
        with self.lock:
            self.stop_reason = self.stop_reason or reason

    def snapshot(self):
        with self.lock:
            return {"charged_usd": None if self.unknown else self.spent,
                    "known_charge_subtotal_usd": self.spent, "unknown_billing": self.unknown,
                    "unknown_billing_guard_usd": self.unknown_guard,
                    "budget_pending_usd": self.pending,
                    "budget_accounted_usd": self.spent + self.unknown_guard,
                    "budget_stop_reason": self.stop_reason}


class Circuit:
    """Completion-order circuit, overall and per model; not a quality cutoff."""
    def __init__(self):
        self.groups = defaultdict(lambda: {"n": 0, "invalid": 0, "consecutive": 0})

    def add(self, row):
        reason = None
        for label in ("all models", row["model_label"]):
            group = self.groups[label]
            group["n"] += 1
            invalid = row.get("valid") is not True
            group["invalid"] += int(invalid)
            group["consecutive"] = group["consecutive"] + 1 if invalid else 0
            if group["consecutive"] >= 5:
                reason = reason or f"Five consecutive invalid responses ({label})"
            if group["n"] >= 100 and group["invalid"] / group["n"] >= .05:
                reason = reason or f"Invalid response fraction reached 5% after 100 attempts ({label})"
        return reason


def validate_request(case, spec, request):
    if set(request) != {"endpoint", "payload"} or not isinstance(request["payload"], dict):
        raise ValueError("Adapter must build endpoint and payload only")
    payload = request["payload"]
    if payload.get("model") != spec["id"]:
        raise ValueError("Request model does not match specification")
    if "messages" in payload:
        users = [m for m in payload["messages"] if m.get("role") == "user"]
        if len(users) != 1:
            raise ValueError("Expected one shared-input user message")
        content = users[0]["content"]
        if isinstance(content, list):
            content = "".join(block["text"] for block in content)
        shared = json.loads(content)
    else:
        shared = {k: payload.get(k) for k in ("state", "questions")}
    if shared != base.shared_input(case):
        raise ValueError("Request changed the frozen state, question or criteria")


def create_plan(adapter, cases, specs, phase, seed):
    rng = random.Random(seed)
    schedule = []
    if phase == "quality":
        shuffled = rng.sample(cases, len(cases))
        blocks = [(0, "quality", shuffled)]
    elif phase == "timing":
        blocks = []
        for repeat in range(3):
            blocks.extend([(repeat, "timing_warmup", base.warmup_cases()),
                           (repeat, "timing", rng.sample(cases, len(cases)))])
    else:
        raise ValueError("Unknown phase")
    group = 0
    for repeat, item_phase, ordered_cases in blocks:
        for case in ordered_cases:
            labels = list(range(len(specs)))
            rng.shuffle(labels)
            for index in labels:
                schedule.append((group, repeat, item_phase, case, specs[index]))
            group += 1
    plan, prepared = [], {}
    for sequence, (group, repeat, item_phase, case, spec) in enumerate(schedule):
        request = adapter.build_request(deepcopy(case), deepcopy(spec))
        validate_request(case, spec, request)
        request = deepcopy(request)
        amount = adapter.reserve_usd(request, spec)
        if not nonnegative(amount) or amount <= 0:
            raise ValueError("Adapter reservation must be finite and positive")
        entry = {"sequence": sequence, "pair_index": group, "case_id": case["id"],
                 "model_label": spec["label"], "phase": item_phase, "repeat": repeat,
                 "request_sha256": digest(canonical(request).encode()),
                 "reserve_usd": max(UNKNOWN_BILL_ALLOWANCE_USD, amount)}
        plan.append(entry)
        prepared[sequence] = (deepcopy(case), deepcopy(spec), request)
    return plan, prepared


def make_error(case, spec, entry, request, message, latency=None):
    return {**entry, "case_id": case["id"], "experiment": case["experiment"],
            "target": case["target"], "target_kind": case["target_kind"],
            "requested_model": spec["id"], "started_at": now(), "request": request["payload"],
            "endpoint": request["endpoint"], "probability": None, "valid": False,
            "error": message, "cost_usd": None, "usage": {}, "response_model": None,
            "provider": None, "latency_s": latency, "request_exception": True}


def run_once(client, key, entry, prepared):
    case, spec, request = prepared
    started = time.perf_counter()
    try:
        row = client.run(case=deepcopy(case), spec=deepcopy(spec), arm_label=spec["label"],
                         phase=entry["phase"], sequence=entry["sequence"],
                         endpoint=request["endpoint"], payload=deepcopy(request["payload"]))
        if not isinstance(row, dict):
            raise TypeError("Adapter returned no record")
        row = deepcopy(row)
        if row.get("request", request["payload"]) != request["payload"] or row.get("endpoint", request["endpoint"]) != request["endpoint"]:
            row.update(valid=False, error="Adapter request record differs from the frozen payload")
        row.update(entry, case_id=case["id"], experiment=case["experiment"], target=case["target"],
                   target_kind=case["target_kind"], requested_model=spec["id"],
                   request=request["payload"], endpoint=request["endpoint"])
        p = row.get("probability")
        if row.get("valid") is not True or not nonnegative(p) or p > 1:
            row.update(valid=False, probability=None)
        if not nonnegative(row.get("cost_usd")):
            row["cost_usd"] = None
        row.setdefault("started_at", now())
        row.setdefault("latency_s", time.perf_counter() - started)
    except Exception as exc:
        # Do not include exception text that could contain credentials or headers.
        row = make_error(case, spec, entry, request,
                         "Adapter raised " + type(exc).__name__ + "; attempted request retained without retry",
                         time.perf_counter() - started)
    try:
        encoded = canonical(row)
    except (TypeError, ValueError):
        # Even a malformed adapter record must not erase a dispatched attempt.
        # Preserve its representation for inspection without treating it as a
        # scored answer or silently assigning a missing bill to zero.
        raw = json.dumps(row, ensure_ascii=False, default=repr)
        charge = row.get("cost_usd")
        row = make_error(case, spec, entry, request,
                         "Adapter record contained values outside the JSON data contract",
                         time.perf_counter() - started)
        row["raw_adapter_record_json"] = raw
        if nonnegative(charge):
            row["cost_usd"] = charge
        encoded = canonical(row)
    return json.loads(encoded.replace(key, "[REDACTED]")) if key else row


def source_files():
    return {"original-runner.py": Path(__file__), "original-adapter.py": ROOT / "adapter.py",
            "original-answer-contract.py": ROOT.parent / "runner.py"}


def prepare_run(key, specs, phase, out, budget, workers, seed, resume, factory):
    full, timing, hashes = load_frozen_sources()
    cases = full if phase == "quality" else timing
    planner = factory(key)
    try:
        plan, prepared = create_plan(planner, cases, specs, phase, seed)
    finally:
        planner.close()
    files = {name: path for name, path in source_files().items() if path.exists()}
    code_hashes = {name: sha(path) for name, path in files.items()}
    meta_path = out / "metadata.json"
    if resume:
        meta = json.loads(meta_path.read_text())
        if meta.get("status") in ("complete", "stopped"):
            for filename, field in (("responses.jsonl", "responses_sha256"), ("attempts.jsonl", "attempts_sha256")):
                if meta.get(field) and sha(out / filename) != meta[field]:
                    raise ValueError("Saved attempt/outcome file changed")
        if meta.get("phase") != phase or meta.get("models") != specs or meta.get("seed") != seed or meta.get("source_hashes") != hashes or meta.get("code_hashes") != code_hashes:
            raise ValueError("Resume changed the frozen models, seed, sources or runner/adapter")
        if meta.get("concurrency_pairs") != workers:
            raise ValueError("Resume must preserve concurrency")
        if sha(out / "plan.json") != meta["plan_sha256"] or json.loads((out / "plan.json").read_text()) != plan:
            raise ValueError("Resume changed the frozen plan or request hashes")
        if sha(out / "requests.jsonl") != meta["requests_sha256"]:
            raise ValueError("Saved exact requests changed")
        history = out / "resume-history"
        history.mkdir(exist_ok=True)
        save(history / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json"), meta)
        meta["resume_count"] = meta.get("resume_count", 0) + 1
    else:
        out.mkdir(parents=True, exist_ok=False)
        save(out / "plan.json", plan)
        with (out / "requests.jsonl").open("w", encoding="utf-8") as stream:
            for entry in plan:
                stream.write(canonical({"sequence": entry["sequence"], "request_sha256": entry["request_sha256"],
                                        **prepared[entry["sequence"]][2]}) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        for name, path in files.items():
            (out / name).write_bytes(path.read_bytes())
        meta = {"schema_version": 1, "created_at": now(), "models": specs, "phase": phase,
                "cases": len(cases), "planned_calls": len(plan), "calls_planned": len(plan),
                "concurrency_pairs": workers, "seed": seed, "source_hashes": hashes,
                "cases_sha256": hashes["cases_sha256" if phase == "quality" else "timing_cases_sha256"],
                "code_hashes": code_hashes, "plan_sha256": sha(out / "plan.json"),
                "requests_sha256": sha(out / "requests.jsonl"), "automatic_retries": 0,
                "repeats": 1 if phase == "quality" else 3, "warmups_per_model_per_block": 0 if phase == "quality" else 2,
                "unknown_bill_allowance_usd": UNKNOWN_BILL_ALLOWANCE_USD,
                "protocol": ("Frozen full quality split; randomized case and within-case model order. Each worker runs a case's models sequentially. Bulk latency is diagnostic only." if phase == "quality" else
                             "Three serial randomized blocks of the same 60 frozen cases, with two warmups per model per block excluded from measured timing. One persistent adapter, nonstreaming full-response latency. No overlapping quality/timing run through this runner."),
                "stop_rule": "Stop new dispatches after 5 consecutive invalid or >=5% invalid after 100 completed calls, overall or per model, in completion order. In-flight attempts are retained.",
                "billing_guard": "Known bills use observed values. Missing bills remain null and retain their reservation, at least $0.01, against the local guard. A local estimate cannot enforce a remote account-level cap.",
                "resume_policy": "Quality only: identical source, adapter, plan and exact request hashes; dispatch journal prevents every attempted sequence from being retried, including lost outcomes."}
    rows = read_rows(out / "responses.jsonl")
    journal = read_rows(out / "attempts.jsonl")
    claims = {}
    for claim in journal:
        seq = claim["sequence"]
        if seq in claims or not isinstance(seq, int) or not 0 <= seq < len(plan) or claim["request_sha256"] != plan[seq]["request_sha256"]:
            raise ValueError("Invalid or duplicate dispatch journal entry")
        claims[seq] = claim
    seen = set()
    for row in rows:
        seq = row["sequence"]
        if seq in seen or seq not in claims or any(row.get(k) != plan[seq][k] for k in ("case_id", "model_label", "phase", "repeat", "request_sha256")):
            raise ValueError("Saved outcome does not match one unique frozen dispatch")
        case, spec, request = prepared[seq]
        if any(row.get(k) != case[k] for k in ("experiment", "target", "target_kind")) or row.get("request") != request["payload"] or row.get("endpoint") != request["endpoint"]:
            raise ValueError("Saved outcome changed its frozen source or request")
        seen.add(seq)
    # A killed process may have sent a request without saving its result. Never
    # replay it: retain a distinct unknown outcome and unknown-bill allowance.
    for seq in sorted(set(claims) - seen):
        case, spec, request = prepared[seq]
        row = make_error(case, spec, plan[seq], request,
                         "Dispatched request has no saved outcome; retained as unknown and not replayed")
        row.update(started_at=claims[seq]["dispatched_at"], lost_outcome=True)
        append(out / "responses.jsonl", row)
        rows.append(row)
    meta.update(status="running", budget_usd=budget, calls_completed=len(rows))
    meta.pop("error", None)
    meta.pop("finished_at", None)
    meta.pop("stop_reason", None)
    save(meta_path, meta)
    return plan, prepared, meta, rows, set(claims)


class Session:
    def __init__(self, out, meta, rows, claims, plan, budget, progress):
        self.out, self.meta, self.rows, self.claims, self.plan = out, meta, rows, claims, plan
        self.guard, self.circuit, self.progress = Budget(budget), Circuit(), progress
        self.lock = threading.Lock()
        for row in rows:
            self.guard.settle(plan[row["sequence"]]["reserve_usd"], row, reserved=False)
            reason = self.circuit.add(row)
            if reason:
                self.guard.stop(reason)

    def claim(self, entry):
        with self.lock:
            if entry["sequence"] in self.claims or not self.guard.reserve(entry["reserve_usd"]):
                return False
            append(self.out / "attempts.jsonl", {"sequence": entry["sequence"],
                   "request_sha256": entry["request_sha256"], "dispatched_at": now()})
            self.claims.add(entry["sequence"])
            return True

    def emit(self, row):
        with self.lock:
            row.update(measurement_role="bulk_quality" if self.meta["phase"] == "quality" else "serial_timing",
                       concurrency_pairs=self.meta["concurrency_pairs"])
            append(self.out / "responses.jsonl", row)
            self.rows.append(row)
            self.guard.settle(self.plan[row["sequence"]]["reserve_usd"], row)
            reason = self.circuit.add(row)
            if reason:
                self.guard.stop(reason)
            self.checkpoint()
            if self.progress and (len(self.rows) % (100 if self.meta["phase"] == "quality" else 24) == 0 or not row["valid"]):
                try:
                    self.progress({"kind": self.meta["phase"], "completed": len(self.rows), "planned": len(self.plan),
                                   "model": row["model_label"], "valid": row["valid"], **self.guard.snapshot()})
                except Exception:
                    self.meta["progress_callback_errors"] = self.meta.get("progress_callback_errors", 0) + 1

    def checkpoint(self):
        self.meta.update(calls_completed=len(self.rows), valid=sum(r.get("valid") is True for r in self.rows),
                         invalid=sum(r.get("valid") is not True for r in self.rows), **self.guard.snapshot())
        save(self.out / "metadata.json", self.meta)

    def finish(self):
        self.meta.update(status="complete" if len(self.rows) == len(self.plan) else "stopped", finished_at=now())
        if self.guard.stop_reason:
            self.meta["stop_reason"] = self.guard.stop_reason
        for name, field in (("responses.jsonl", "responses_sha256"), ("attempts.jsonl", "attempts_sha256")):
            path = self.out / name
            if not path.exists():
                path.touch()
            self.meta[field] = sha(path)
        self.checkpoint()
        return {"run_dir": str(self.out), "metadata": self.meta}


def _run(key, model_specs, out_dir, budget, workers, seed, progress, phase, resume):
    Budget(budget)
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be a positive integer")
    specs = validate_specs(model_specs)
    if resume and (phase != "quality" or out_dir is None):
        raise ValueError("Only quality with an explicit existing output directory can resume")
    out = Path(out_dir or ROOT / "runs" / (phase + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")))
    with isolated_phase(phase):
        # Validate immutable data before importing/constructing the HTTP adapter.
        load_frozen_sources()
        factory = adapter_class()
        plan, prepared, meta, rows, claims = prepare_run(key, specs, phase, out, budget, workers, seed, resume, factory)
        session = Session(out, meta, rows, claims, plan, budget, progress)
        clients, clients_lock, local = [], threading.Lock(), threading.local()

        def execute(entries):
            if not hasattr(local, "client"):
                local.client = factory(key)
                with clients_lock:
                    clients.append(local.client)
            for entry in entries:
                if entry["sequence"] in claims:
                    continue
                if not session.claim(entry):
                    break
                row = run_once(local.client, key, entry, prepared[entry["sequence"]])
                session.emit(row)

        try:
            if phase == "timing":
                execute(plan)
            else:
                groups = defaultdict(list)
                for entry in plan:
                    if entry["sequence"] not in claims:
                        groups[entry["pair_index"]].append(entry)
                tasks = iter(groups.values())
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    pending = set()
                    for _ in range(min(workers, len(groups))):
                        pending.add(pool.submit(execute, next(tasks)))
                    while pending:
                        done, pending = wait(pending, return_when=FIRST_COMPLETED)
                        for future in done:
                            try:
                                future.result()
                            except Exception as exc:
                                session.guard.stop("Worker stopped: " + type(exc).__name__)
                        if not session.guard.stop_reason:
                            for _ in done:
                                item = next(tasks, None)
                                if item is not None:
                                    pending.add(pool.submit(execute, item))
        except Exception as exc:
            session.guard.stop("Protocol stopped: " + type(exc).__name__)
        finally:
            for client in clients:
                try:
                    client.close()
                except Exception:
                    pass
        return session.finish()


def run_quality(key, model_specs, out_dir=None, budget=5, workers=16, seed=20261002, progress=None, *, resume=False):
    return _run(key, model_specs, out_dir, budget, workers, seed, progress, "quality", resume)


def run_timing(key, model_specs, out_dir=None, budget=5, workers=1, seed=20261005, progress=None):
    if workers != 1 or isinstance(workers, bool):
        raise ValueError("Timing requires exactly one worker")
    return _run(key, model_specs, out_dir, budget, 1, seed, progress, "timing", False)
